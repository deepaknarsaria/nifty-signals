"""Backtest the signal engine on historical 5-minute index candles.

    python backtest.py                      # NIFTY, data/NIFTY_5m.csv
    python backtest.py --symbol BANKNIFTY

Option premiums are MODELLED (Black-Scholes, ATM strike, India VIX as implied
volatility, trading-time decay) because Kite does not supply intraday history
for expired option contracts. Treat rupee figures as estimates.
"""
import argparse
import math
import os
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

import config as C
from signals import add_indicators, confirmed, faded

MIN_PER_DAY = 375
MIN_PER_YEAR = 252 * MIN_PER_DAY


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot, strike, t_years, iv, is_call, r=C.RISK_FREE):
    if t_years <= 0 or iv <= 0:
        return max(spot - strike, 0.0) if is_call else max(strike - spot, 0.0)
    s = iv * math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t_years) / s
    d2 = d1 - s
    if is_call:
        return spot * _ncdf(d1) - strike * math.exp(-r * t_years) * _ncdf(d2)
    return strike * math.exp(-r * t_years) * _ncdf(-d2) - spot * _ncdf(-d1)


def next_expiry(d, mode):
    wd = C.EXPIRY_WD_NEW if d >= C.EXPIRY_SWITCH_DATE else C.EXPIRY_WD_OLD
    if mode == "weekly":
        return d + timedelta(days=(wd - d.weekday()) % 7)
    y, m = d.year, d.month
    while True:
        last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        last -= timedelta(days=(last.weekday() - wd) % 7)
        if last >= d:
            return last
        y, m = y + (m == 12), m % 12 + 1


def t_to_expiry(ts, expiry):
    """Years to expiry counted in trading minutes (holidays ignored)."""
    close = datetime.combine(ts.date(), time(15, 30))
    today = max((close - ts.to_pydatetime().replace(tzinfo=None)).total_seconds() / 60, 0)
    later = np.busday_count(ts.date() + timedelta(days=1), expiry + timedelta(days=1)) * MIN_PER_DAY
    return max(today + later, 1.0) / MIN_PER_YEAR


def costs(buy_prem, sell_prem, qty):
    buy, sell = buy_prem * qty, sell_prem * qty
    txn = (buy + sell) * (C.EXCH_TXN + C.SEBI_FEE)
    brokerage = 2 * C.BROKERAGE
    return brokerage + sell * C.STT_SELL + txn + buy * C.STAMP_BUY + (brokerage + txn) * C.GST


def run(df, vix, spec):
    df = add_indicators(df)
    if vix is not None and len(vix):
        df["vix"] = vix["close"].reindex(df.index, method="ffill").shift(1)  # prior bar's VIX only
        df["vix"] = df["vix"].fillna(C.FALLBACK_VIX)
    else:
        print(f"WARNING: no VIX data, using a constant {C.FALLBACK_VIX}")
        df["vix"] = C.FALLBACK_VIX
    qty = spec["lot"] * C.LOTS
    trades = []

    for day, g in df.groupby(df.index.date):
        idx, n = g.index, len(g)
        o, h, l, c = g.open.values, g.high.values, g.low.values, g.close.values
        sc, atr, vx = g.score.values, g.atr.values, g.vix.values
        pos, pending_entry, pending_exit = None, 0, None
        ntr, cool = 0, 0

        def close_pos(i, spot, reason):
            nonlocal pos, cool
            is_call = pos["dir"] > 0
            t = t_to_expiry(idx[i], pos["expiry"])
            px = max(bs_price(spot, pos["strike"], t, vx[i] / 100, is_call) - C.SLIPPAGE_PTS, 0.05)
            cost = costs(pos["entry_prem"], px, qty)
            gross = (px - pos["entry_prem"]) * qty
            trades.append(dict(
                date=day, entry_time=pos["time"], exit_time=idx[i], side="CALL" if is_call else "PUT",
                strike=pos["strike"], expiry=pos["expiry"], entry_spot=round(pos["spot"], 2),
                exit_spot=round(spot, 2), entry_prem=round(pos["entry_prem"], 2), exit_prem=round(px, 2),
                gross=round(gross, 2), costs=round(cost, 2), net=round(gross - cost, 2),
                reason=reason, score=pos["score"]))
            pos, cool = None, C.COOLDOWN_BARS

        for i in range(n):
            t = idx[i].time()
            # 1) act at this bar's open on decisions taken at the previous close
            if pos and (pending_exit or t >= C.SQUARE_OFF):
                close_pos(i, o[i], pending_exit or "square_off")
            pending_exit = None
            if pending_entry and pos is None and C.ENTRY_START <= t <= C.ENTRY_END and not math.isnan(atr[i - 1]):
                d = pending_entry
                expiry = next_expiry(day, spec["expiry"])
                strike = round(o[i] / spec["step"]) * spec["step"]
                prem = bs_price(o[i], strike, t_to_expiry(idx[i], expiry), vx[i] / 100, d > 0) + C.SLIPPAGE_PTS
                stop_dist = C.STOP_ATR * atr[i - 1]
                pos = dict(dir=d, time=idx[i], spot=o[i], strike=strike, expiry=expiry, entry_prem=prem,
                           stop=o[i] - d * stop_dist, target=o[i] + d * stop_dist * C.TARGET_R,
                           score=int(sc[i - 1]))
                ntr += 1
            pending_entry = 0

            # 2) intrabar stop / target on the index (stop wins if both are touched)
            if pos:
                d = pos["dir"]
                if (d > 0 and l[i] <= pos["stop"]) or (d < 0 and h[i] >= pos["stop"]):
                    close_pos(i, pos["stop"], "stop")
                elif (d > 0 and h[i] >= pos["target"]) or (d < 0 and l[i] <= pos["target"]):
                    close_pos(i, pos["target"], "target")

            # 3) decisions at this bar's close, executed at the next open
            if pos:
                if faded(sc, i, pos["dir"]):
                    pending_exit = "signal_fade"
                elif i == n - 1:
                    close_pos(i, c[i], "day_end")
            else:
                if cool > 0:
                    cool -= 1
                elif ntr < C.MAX_TRADES_PER_DAY:
                    if confirmed(sc, i, 1):
                        pending_entry = 1
                    elif confirmed(sc, i, -1):
                        pending_entry = -1
    return pd.DataFrame(trades), df


def stats(t):
    if t.empty:
        return dict(trades=0)
    w, lo = t[t.net > 0].net, t[t.net <= 0].net
    eq = t.net.cumsum()
    return dict(
        trades=len(t), win_rate=round(100 * len(w) / len(t), 1),
        avg_win=round(w.mean(), 0) if len(w) else 0, avg_loss=round(lo.mean(), 0) if len(lo) else 0,
        profit_factor=round(w.sum() / -lo.sum(), 2) if lo.sum() < 0 else float("inf"),
        expectancy=round(t.net.mean(), 0), gross=round(t.gross.sum(), 0), costs=round(t.costs.sum(), 0),
        net=round(t.net.sum(), 0), max_drawdown=round((eq - eq.cummax()).min(), 0))


def report(trades, df):
    if trades.empty:
        return "No trades generated."
    days = sorted(set(df.index.date))
    split = days[int(len(days) * C.IN_SAMPLE_FRACTION)]
    rows = {"ALL": stats(trades),
            f"IN-SAMPLE (before {split})": stats(trades[trades.date < split]),
            f"OUT-OF-SAMPLE (from {split})": stats(trades[trades.date >= split]),
            "CALL only": stats(trades[trades.side == "CALL"]),
            "PUT only": stats(trades[trades.side == "PUT"])}
    for y, g in trades.groupby(pd.to_datetime(trades.date).dt.year):
        rows[f"Year {y}"] = stats(g)
    out = [f"Period: {days[0]} to {days[-1]}  ({len(days)} trading days)",
           pd.DataFrame(rows).T.to_string(), "",
           "Exit reasons:", trades.groupby("reason").net.agg(["count", "sum", "mean"]).round(0).to_string()]
    return "\n".join(out)


def load(path):
    d = pd.read_csv(path, parse_dates=["date"]).set_index("date")
    if d.index.tz is not None:
        d.index = d.index.tz_localize(None)
    return d[~d.index.duplicated()].sort_index()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()
    px = load(os.path.join(a.data_dir, f"{a.symbol}_5m.csv"))
    vpath = os.path.join(a.data_dir, "VIX_5m.csv")
    vix = load(vpath) if os.path.exists(vpath) else None
    trades, df = run(px, vix, C.INSTRUMENTS[a.symbol])
    text = report(trades, df)
    print(text)
    os.makedirs("results", exist_ok=True)
    trades.to_csv(f"results/{a.symbol}_trades.csv", index=False)
    with open(f"results/{a.symbol}_report.txt", "w") as f:
        f.write(text + "\n")
    print(f"\nSaved results/{a.symbol}_trades.csv and results/{a.symbol}_report.txt")
