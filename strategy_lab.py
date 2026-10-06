"""Strategy lab: paper-test ready-made option structures on REAL recorded prices, every market day.

    python strategy_lab.py            # process any recorded day not yet in lab_results.csv, print the scorecard
    python strategy_lab.py --send     # same, and send the day's scorecard to Telegram

How each structure is traded on paper, one lot per leg unit:
  * Opened at the first option-chain snapshot from 9:20 (sell legs at the bid, buy legs at the ask).
  * Marked every 5 minutes on the price it could be closed at (buy back at the ask, sell at the bid).
  * Closed at the last snapshot up to 15:10. No stop or target: the best and worst points of the day
    are recorded, so stop and target rules can be judged later without guessing.
  * Brokerage, STT and charges are deducted per leg.
Calendar spreads are left out (they need the next expiry, which is not recorded).
"UNLIMITED" in the risk column means the structure has a naked short side: not a hedged strategy.
"""
import argparse
import glob
import os

import numpy as np
import pandas as pd

import config as C
from backtest import costs

OUT = "lab_results.csv"
STEP, LOT = C.INSTRUMENTS["NIFTY"]["step"], C.INSTRUMENTS["NIFTY"]["lot"]
C_, P_ = "CE", "PE"

# name: (view, [(quantity, strike offset from ATM, type)])   quantity > 0 = buy, < 0 = sell
STRATS = {
    # ---- neutral, selling
    "Short Straddle":        ("neutral", [(-1, 0, C_), (-1, 0, P_)]),
    "Iron Butterfly":        ("neutral", [(-1, 0, C_), (-1, 0, P_), (1, 200, C_), (1, -200, P_)]),
    "Short Strangle":        ("neutral", [(-1, 150, C_), (-1, -150, P_)]),
    "Short Iron Condor":     ("neutral", [(-1, 150, C_), (-1, -150, P_), (1, 350, C_), (1, -350, P_)]),
    "Batman":                ("neutral", [(1, 50, C_), (-2, 150, C_), (1, 250, C_), (1, -50, P_), (-2, -150, P_), (1, -250, P_)]),
    "Double Plateau":        ("neutral", [(1, 50, C_), (-1, 100, C_), (-1, 200, C_), (1, 250, C_),
                                          (1, -50, P_), (-1, -100, P_), (-1, -200, P_), (1, -250, P_)]),
    "Jade Lizard":           ("neutral", [(-1, -100, P_), (-1, 100, C_), (1, 200, C_)]),
    "Reverse Jade Lizard":   ("neutral", [(-1, 100, C_), (-1, -100, P_), (1, -200, P_)]),
    # ---- bullish
    "Buy Call":              ("bullish", [(1, 0, C_)]),
    "Sell Put":              ("bullish", [(-1, -50, P_)]),
    "Bull Call Spread":      ("bullish", [(1, 0, C_), (-1, 150, C_)]),
    "Bull Put Spread":       ("bullish", [(-1, -50, P_), (1, -200, P_)]),
    "Call Ratio Back Spread": ("bullish", [(-1, 0, C_), (2, 150, C_)]),
    "Bull Condor":           ("bullish", [(1, 0, C_), (-1, 100, C_), (-1, 250, C_), (1, 350, C_)]),
    "Bull Butterfly":        ("bullish", [(1, 0, C_), (-2, 150, C_), (1, 300, C_)]),
    "Range Forward":         ("bullish", [(1, 100, C_), (-1, -100, P_)]),
    "Long Synthetic Future": ("bullish", [(1, 0, C_), (-1, 0, P_)]),
    # ---- bearish
    "Buy Put":               ("bearish", [(1, 0, P_)]),
    "Sell Call":             ("bearish", [(-1, 50, C_)]),
    "Bear Call Spread":      ("bearish", [(-1, 50, C_), (1, 200, C_)]),
    "Bear Put Spread":       ("bearish", [(1, 0, P_), (-1, -150, P_)]),
    "Put Ratio Back Spread": ("bearish", [(-1, 0, P_), (2, -150, P_)]),
    "Bear Condor":           ("bearish", [(1, 0, P_), (-1, -100, P_), (-1, -250, P_), (1, -350, P_)]),
    "Bear Butterfly":        ("bearish", [(1, 0, P_), (-2, -150, P_), (1, -300, P_)]),
    "Risk Reversal":         ("bearish", [(1, -100, P_), (-1, 100, C_)]),
    "Short Synthetic Future": ("bearish", [(-1, 0, C_), (1, 0, P_)]),
    # ---- others
    "Call Ratio Spread":     ("other", [(1, 0, C_), (-2, 150, C_)]),
    "Put Ratio Spread":      ("other", [(1, 0, P_), (-2, -150, P_)]),
    "Long Straddle":         ("other", [(1, 0, C_), (1, 0, P_)]),
    "Long Strangle":         ("other", [(1, 150, C_), (1, -150, P_)]),
    "Long Iron Butterfly":   ("other", [(1, 0, C_), (1, 0, P_), (-1, 200, C_), (-1, -200, P_)]),
    "Long Iron Condor":      ("other", [(1, 150, C_), (1, -150, P_), (-1, 350, C_), (-1, -350, P_)]),
    "Strip":                 ("other", [(1, 0, C_), (2, 0, P_)]),
    "Strap":                 ("other", [(2, 0, C_), (1, 0, P_)]),
}


def max_risk(legs, atm, entry_net):
    """Worst loss at expiry in premium points, or None if a short side is naked (unlimited)."""
    net_calls = sum(q for q, _, t in legs if t == C_)
    net_puts = sum(q for q, _, t in legs if t == P_)
    if net_calls < 0 or net_puts < 0:
        return None
    grid = np.arange(atm - 2000, atm + 2001, 50.0)
    pay = sum(q * np.maximum(grid - (atm + off), 0) if t == C_ else q * np.maximum((atm + off) - grid, 0) for q, off, t in legs)
    return float(-(pay - entry_net).min())


def run_day(path):
    c = pd.read_csv(path, parse_dates=["time"])
    day = c.time.dt.date.iloc[0]
    times = sorted(t for t in c.time.unique() if pd.Timestamp(t).time() <= C.SQUARE_OFF.replace(second=59))
    start = [t for t in times if pd.Timestamp(t).time() >= pd.Timestamp("09:20").time()]
    if len(start) < 6:
        return pd.DataFrame()
    snaps = {t: g.set_index(["strike", "type"]) for t, g in c.groupby("time")}
    t0 = start[0]
    s0 = snaps[t0]
    atm = round(float(s0.spot.iloc[0]) / STEP) * STEP
    rows = []
    for name, (view, legs) in STRATS.items():
        keys = [(float(atm + off), t) for _, off, t in legs]
        if any(k not in s0.index for k in keys):
            continue
        # entry: pay the ask on buys, receive the bid on sells
        entry = [float(s0.loc[k, "ask"] if q > 0 else s0.loc[k, "bid"]) for (q, _, _), k in zip(legs, keys)]
        entry_net = sum(q * p for (q, _, _), p in zip(legs, entry))          # > 0 debit paid, < 0 credit received
        last = list(entry)
        path_pts, when = [], []
        for t in start[1:]:
            s = snaps[t]
            for n, ((q, _, _), k) in enumerate(zip(legs, keys)):
                if k in s.index:                                           # close: sell longs at bid, buy shorts back at ask
                    px = float(s.loc[k, "bid"] if q > 0 else s.loc[k, "ask"])
                    if px > 0:
                        last[n] = px
            path_pts.append(sum(q * (now - e) for (q, _, _), now, e in zip(legs, last, entry)))
            when.append(t)
        fee = sum(costs(e, x, LOT * abs(q)) if q > 0 else costs(x, e, LOT * abs(q)) for (q, _, _), e, x in zip(legs, entry, last))
        risk = max_risk(legs, atm, entry_net)
        pp = np.array(path_pts)
        rows.append(dict(date=day, strategy=name, view=view, hedged=risk is not None, entry_time=pd.Timestamp(t0).strftime("%H:%M"),
                         exit_time=pd.Timestamp(when[-1]).strftime("%H:%M"), atm=atm, legs=len(legs),
                         entry_net_pts=round(entry_net, 2), result_pts=round(pp[-1], 2), fees=round(fee),
                         net_rs=round(pp[-1] * LOT - fee), best_rs=round(pp.max() * LOT), best_time=pd.Timestamp(when[int(pp.argmax())]).strftime("%H:%M"),
                         worst_rs=round(pp.min() * LOT), max_risk_rs=(round(risk * LOT) if risk is not None else ""),
                         nifty_move=round(float(snaps[when[-1]].spot.iloc[0]) - float(s0.spot.iloc[0]), 1)))
    return pd.DataFrame(rows)


def leaderboard(res, hedged_only=True, n=None):
    r = res[res.hedged] if hedged_only else res
    g = r.groupby("strategy").agg(days=("net_rs", "size"), total=("net_rs", "sum"), avg=("net_rs", "mean"),
                                  win=("net_rs", lambda x: (x > 0).mean()), worst=("net_rs", "min")).sort_values("total", ascending=False)
    return g.head(n) if n else g


def message(day_res, res):
    d = day_res.sort_values("net_rs", ascending=False)
    h = d[d.hedged]
    mv = d.nifty_move.iloc[0]
    line = lambda r: f"{r.strategy}: Rs {r.net_rs:+,}"
    out = [f"STRATEGY LAB  {pd.Timestamp(d.date.iloc[0]):%d %b}", "",
           f"{len(d)} structures paper-traded on real prices, {d.entry_time.iloc[0]} to {d.exit_time.iloc[0]}",
           f"NIFTY moved {mv:+.0f} points in that time", "",
           "Best hedged today"] + [line(r) for r in h.head(5).itertuples()] + ["", "Worst hedged today"] + \
          [line(r) for r in h.tail(3).itertuples()]
    naked = d[~d.hedged]
    if len(naked):
        out += ["", "Unhedged, for comparison (unlimited risk)"] + [line(r) for r in naked.head(2).itertuples()] + [line(naked.iloc[-1])]
    days = res.date.nunique()
    lb = leaderboard(res, n=5)
    out += ["", f"Running total, hedged only ({days} day{'s' if days != 1 else ''})"] + \
           [f"{k}: Rs {int(v.total):+,} ({v.win:.0%} winning days)" for k, v in lb.iterrows()]
    out += ["", "Per lot, after charges. One or two days prove nothing; judge after 15 or more."]
    return "\n".join(out)


def main(send):
    res = pd.read_csv(OUT, parse_dates=["date"]) if os.path.exists(OUT) else pd.DataFrame()
    have = set(res.date.dt.date) if len(res) else set()
    new = []
    for f in sorted(glob.glob("chain/NIFTY_*.csv")):
        day = pd.to_datetime(os.path.basename(f)[6:16]).date()
        if day in have:
            continue
        r = run_day(f)
        if len(r):
            new.append(r)
    if not new:
        print("No new recorded day to process.")
        return
    add = pd.concat(new)
    add["date"] = pd.to_datetime(add["date"])
    res = pd.concat([res, add], ignore_index=True) if len(res) else add
    res.to_csv(OUT, index=False)
    latest = add[add.date == add.date.max()]
    text = message(latest, res)
    print(text)
    if send:
        from live_signals import telegram
        telegram(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    main(ap.parse_args().send)
