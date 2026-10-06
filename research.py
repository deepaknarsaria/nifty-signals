"""Research harness: test several rule families with one shared simulator.

    python research.py            # in-sample only (first 70% of days)
    python research.py --oos      # also show the untouched last 30%

Every rule decides at a bar's close and trades at the next bar's open.
Option P&L uses the same model and costs as backtest.py.
"""
import argparse
import math
from datetime import time

import numpy as np
import pandas as pd

import config as C
from backtest import bs_price, costs, load, next_expiry, t_to_expiry
from signals import add_indicators, confirmed

SPEC = C.INSTRUMENTS["NIFTY"]
QTY = SPEC["lot"] * C.LOTS


def prepare(px, vix):
    df = add_indicators(px)
    df["vix"] = vix["close"].reindex(df.index, method="ffill").shift(1).fillna(C.FALLBACK_VIX)
    day = pd.Series(df.index.date, index=df.index)
    dclose = df.groupby(day)["close"].last().shift(1)
    df["pd_close"] = day.map(dclose).values
    days = []
    for d, g in df.groupby(df.index.date):
        if len(g) < 60 or np.isnan(g.pd_close.iloc[0]):
            continue
        days.append(dict(day=d, idx=g.index, t=[x.time() for x in g.index], n=len(g),
                         o=g.open.values, h=g.high.values, l=g.low.values, c=g.close.values,
                         atr=g.atr.values, sc=g.score.values, avg=g.session_avg.values, rsi=g.rsi.values,
                         vix=g.vix.values, pdc=g.pd_close.iloc[0], pdh=g.pd_high.iloc[0], pdl=g.pd_low.iloc[0]))
    return days


def tte(ts, exp, decay):
    """Years to expiry. 'trading' = all decay inside market hours (pessimistic for an intraday buyer);
    'calendar' = decay spread evenly over the clock, nights and weekends included (optimistic)."""
    if decay == "trading":
        return t_to_expiry(ts, exp)
    secs = (pd.Timestamp(exp) + pd.Timedelta(hours=15, minutes=30) - ts).total_seconds()
    cal = max(secs, 60) / (365 * 86400)
    if decay == "blend":   # calibrated on real Sep 2026 option candles: roughly half-way between the two clocks
        return 0.5 * t_to_expiry(ts, exp) + 0.5 * cal
    return cal


def simulate(days, decide, max_trades=1, exit_time=C.SQUARE_OFF, cooldown=3, itm=0, decay="trading", slip=C.SLIPPAGE_PTS,
             lock_at=None, trail=None, max_bars=None, min_prog=0.0, prem_pct=None, prem_pts=None):
    """Exit options (all in multiples of the initial stop distance R, judged on the index):
    lock_at  : once the trade is this far in profit, move the stop to breakeven
    trail    : after locking, keep the stop this far behind the best price reached
    max_bars : time stop, close at the next open if after this many bars profit is below min_prog
    """
    out = []
    for D in days:
        o, h, l, c, t, n = D["o"], D["h"], D["l"], D["c"], D["t"], D["n"]
        pos, pend, ntr, cool = None, None, 0, 0

        def close(i, spot, why, px=None):
            nonlocal pos, cool
            call = pos["d"] > 0
            if px is None:
                px = max(bs_price(spot, pos["k"], tte(D["idx"][i], pos["exp"], decay), D["vix"][i] / 100, call) - slip, 0.05)
            cost = costs(pos["prem"], px, QTY)
            out.append(dict(date=D["day"], side="CALL" if call else "PUT", pts=pos["d"] * (spot - pos["spot"]),
                            net=(px - pos["prem"]) * QTY - cost, why=why))
            pos, cool = None, cooldown

        for i in range(n):
            if pos and t[i] >= exit_time:
                close(i, o[i], "time")
            if pend and pos is None and t[i] < exit_time:
                d, sd, td = pend
                exp = next_expiry(D["day"], SPEC["expiry"])
                k = round(o[i] / SPEC["step"]) * SPEC["step"] - d * itm * SPEC["step"]
                prem = bs_price(o[i], k, tte(D["idx"][i], exp, decay), D["vix"][i] / 100, d > 0) + slip
                pos = dict(d=d, spot=o[i], k=k, exp=exp, prem=prem, stop=o[i] - d * sd,
                           tgt=(o[i] + d * td) if td else None, r=sd, best=o[i], bars=0, locked=False)
                ntr += 1
            pend = None
            if pos:
                d = pos["d"]
                if (d > 0 and l[i] <= pos["stop"]) or (d < 0 and h[i] >= pos["stop"]):
                    gapped = (d > 0 and o[i] <= pos["stop"]) or (d < 0 and o[i] >= pos["stop"])
                    close(i, o[i] if gapped else pos["stop"],
                          pos.get("why") or ("trail" if pos["locked"] else "stop"))
                elif pos["tgt"] is not None and ((d > 0 and h[i] >= pos["tgt"]) or (d < 0 and l[i] <= pos["tgt"])):
                    close(i, pos["tgt"], "target")
                elif prem_pct or prem_pts:       # quick-profit target on the option premium itself
                    want = pos["prem"] * (1 + prem_pct) if prem_pct else pos["prem"] + prem_pts
                    fav = h[i] if d > 0 else l[i]
                    best = bs_price(fav, pos["k"], tte(D["idx"][i], pos["exp"], decay), D["vix"][i] / 100, d > 0)
                    if best - slip >= want:
                        close(i, fav, "quick_profit", px=want)
            if pos:      # update the protective stop only after the bar, so it takes effect from the next bar
                d = pos["d"]
                pos["bars"] += 1
                pos["best"] = max(pos["best"], h[i]) if d > 0 else min(pos["best"], l[i])
                gain = d * (pos["best"] - pos["spot"])
                if lock_at is not None and gain >= lock_at * pos["r"]:
                    pos["locked"] = True
                    new_stop = pos["spot"] if trail is None else pos["best"] - d * trail * pos["r"]
                    if trail is not None:
                        new_stop = max(new_stop, pos["spot"]) if d > 0 else min(new_stop, pos["spot"])
                    pos["stop"] = max(pos["stop"], new_stop) if d > 0 else min(pos["stop"], new_stop)
                if i == n - 1:
                    close(i, c[i], "day_end")
                elif max_bars and pos["bars"] >= max_bars and d * (c[i] - pos["spot"]) < min_prog * pos["r"] and not pos["locked"]:
                    pos["tgt"] = None
                    pos["stop"] = c[i] + d * 1e9      # forces an exit at the next bar open
                    pos["why"] = "time_stop"
            elif cool > 0:
                cool -= 1
            elif ntr < max_trades and i < n - 1 and not math.isnan(D["atr"][i]):
                r = decide(i, D)
                if r:
                    pend = r
    return pd.DataFrame(out)


# ---------------------------------------------------------------- rule families
def in_window(D, i, a=C.ENTRY_START, b=C.ENTRY_END):
    return a <= D["t"][i + 1] <= b


def base(stop=1.5, r=2.0, flip=1):
    def f(i, D):
        if not in_window(D, i):
            return None
        for d in (1, -1):
            if confirmed(D["sc"], i, d):
                return (d * flip, stop * D["atr"][i], stop * D["atr"][i] * r if r else None)
    return f


def orb(bars, flip=1):
    def f(i, D):
        if i < bars or not in_window(D, i):
            return None
        hi, lo, c = D["h"][:bars].max(), D["l"][:bars].min(), D["c"][i]
        if c > hi:
            return (flip, max(c - lo, D["atr"][i]) if flip > 0 else 2 * D["atr"][i], None)
        if c < lo:
            return (-flip, max(hi - c, D["atr"][i]) if flip > 0 else 2 * D["atr"][i], None)
    return f


def timed(at, ref, flip=1, min_move=0.0, stop_atr=4.0):
    """At a fixed clock time, trade in (or against) the direction of the day's move so far."""
    def f(i, D):
        if D["t"][i + 1] != at:
            return None
        base_px = D["pdc"] if ref == "prev_close" else D["o"][0]
        mv = (D["c"][i] - base_px) / base_px
        if abs(mv) < min_move:
            return None
        return (flip * (1 if mv > 0 else -1), stop_atr * D["atr"][i], None)
    return f


def gap(flip, min_gap=0.003):
    def f(i, D):
        if i != 0:
            return None
        g = (D["o"][0] - D["pdc"]) / D["pdc"]
        if abs(g) < min_gap:
            return None
        d = flip * (1 if g > 0 else -1)
        return (d, 4 * D["atr"][i], abs(D["c"][i] - D["pdc"]) if flip < 0 else None)
    return f


def stretch(k=2.5, rsi_hi=70, rsi_lo=30):
    """Fade an over-extended move back toward the session average."""
    def f(i, D):
        if not in_window(D, i, time(9, 45), time(14, 45)):
            return None
        dev, a = D["c"][i] - D["avg"][i], D["atr"][i]
        if dev > k * a and D["rsi"][i] > rsi_hi:
            return (-1, 2 * a, abs(dev))
        if dev < -k * a and D["rsi"][i] < rsi_lo:
            return (1, 2 * a, abs(dev))
    return f


RULES = {
    "base trend (original)":        (base(), dict(max_trades=3)),
    "base faded":                   (base(flip=-1), dict(max_trades=3)),
    "base faded, 1R target":        (base(1.5, 1.0, -1), dict(max_trades=3)),
    "ORB 15m, hold to close":       (orb(3), {}),
    "ORB 30m, hold to close":       (orb(6), {}),
    "ORB 60m, hold to close":       (orb(12), {}),
    "ORB 30m faded":                (orb(6, -1), {}),
    "first hour momentum (10:15)":  (timed(time(10, 15), "open"), {}),
    "first hour faded (10:15)":     (timed(time(10, 15), "open", -1), {}),
    "midday momentum (12:00)":      (timed(time(12, 0), "prev_close", min_move=0.003), {}),
    "late momentum (14:00)":        (timed(time(14, 0), "prev_close", min_move=0.003, stop_atr=3), {}),
    "late fade (14:00)":            (timed(time(14, 0), "prev_close", -1, 0.003, 3), {}),
    "gap follow (>0.3%)":           (gap(1), {}),
    "gap fade (>0.3%)":             (gap(-1), {}),
    "stretch fade 2.5 ATR":         (stretch(), dict(max_trades=2)),
    "stretch fade 3.5 ATR":         (stretch(3.5, 75, 25), dict(max_trades=2)),
}


def row(t):
    if t.empty:
        return dict(trades=0)
    w, lo = t[t.net > 0].net.sum(), -t[t.net <= 0].net.sum()
    eq = t.net.cumsum()
    return dict(trades=len(t), win=round(100 * (t.net > 0).mean(), 1), pf=round(w / lo, 2) if lo else 9.99,
                net=round(t.net.sum()), per_trade=round(t.net.mean()), pts=round(t.pts.mean(), 1),
                t_pts=round(t.pts.mean() / (t.pts.std() / math.sqrt(len(t))), 2),
                max_dd=round((eq - eq.cummax()).min()))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--oos", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    days = prepare(load("data/NIFTY_5m.csv"), load("data/VIX_5m.csv"))
    cut = int(len(days) * C.IN_SAMPLE_FRACTION)
    ins, oos = days[:cut], days[cut:]
    half = len(ins) // 2
    print(f"in-sample {ins[0]['day']} to {ins[-1]['day']} ({len(ins)} days); out-of-sample {oos[0]['day']} to {oos[-1]['day']} ({len(oos)} days)")
    res = {}
    for name, (fn, kw) in RULES.items():
        if a.only and a.only not in name:
            continue
        r = row(simulate(ins, fn, **kw))
        r["h1_net"] = row(simulate(ins[:half], fn, **kw)).get("net")
        r["h2_net"] = row(simulate(ins[half:], fn, **kw)).get("net")
        if a.oos:
            x = row(simulate(oos, fn, **kw))
            r.update({"OOS_trades": x.get("trades"), "OOS_pf": x.get("pf"), "OOS_net": x.get("net"), "OOS_pts": x.get("pts")})
        res[name] = r
    pd.set_option("display.width", 250)
    print(pd.DataFrame(res).T.to_string())
