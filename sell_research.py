"""Modelled test of same-day HEDGED option-selling structures on NIFTY.

    python sell_research.py

Premiums are modelled (Black-Scholes, India VIX as IV, no skew), so treat the output as a
first filter only. Each structure is run under two decay assumptions:
  fast = all time decay happens inside market hours (kind to sellers)
  slow = decay is spread evenly over the clock, nights and weekends included (harsh on sellers)
The truth lies between them. Entry at the open of the entry bar, exit by config.SQUARE_OFF.
"""
import math

import numpy as np
import pandas as pd

import config as C
from backtest import bs_price, costs, load, next_expiry
from research import prepare, tte

SPEC = C.INSTRUMENTS["NIFTY"]
QTY, STEP = SPEC["lot"], SPEC["step"]
SLIP = 0.5  # premium points lost per leg, per side


def value(legs, spot, ts, exp, vix, decay):
    """Signed premium of the structure: positive = what it would cost to buy it."""
    t = tte(ts, exp, decay)
    return [s * bs_price(spot, k, t, vix / 100, call) for s, k, call in legs]


def simulate(days, build, entry, decay, stop_frac=None):
    out = []
    for D in days:
        t = D["t"]
        if entry not in t:
            continue
        i = t.index(entry)
        if i == 0 or math.isnan(D["atr"][i - 1]):
            continue
        spot0 = D["o"][i]
        spec = build(spot0, D, i)
        if not spec:
            continue
        legs, exp = spec["legs"], next_expiry(D["day"], SPEC["expiry"])
        v0 = value(legs, spot0, D["idx"][i], exp, D["vix"][i], decay)
        credit = -sum(v0) - SLIP * len(legs)          # net points received (negative = paid)
        risk = spec["max_loss"](credit)
        exit_spot, exit_j, why = None, None, None
        for j in range(i, D["n"]):
            if j > i and t[j] >= C.SQUARE_OFF:
                exit_spot, exit_j, why = D["o"][j], j, "time"
                break
            for s in (D["l"][j], D["h"][j]):
                if spec.get("idx_stop") and (s - spec["idx_stop"]) * spec["dir"] <= 0:
                    exit_spot, exit_j, why = spec["idx_stop"], j, "index stop"
                elif stop_frac:
                    pnl = credit + sum(value(legs, s, D["idx"][j], exp, D["vix"][j], decay)) - SLIP * len(legs)
                    if pnl <= -stop_frac * risk:
                        exit_spot, exit_j, why = s, j, "loss stop"
                if exit_spot is not None:
                    break
            if exit_spot is not None:
                break
        if exit_spot is None:
            exit_spot, exit_j, why = D["c"][-1], D["n"] - 1, "day end"
        v1 = value(legs, exit_spot, D["idx"][exit_j], exp, D["vix"][exit_j], decay)
        pts = credit + sum(v1) - SLIP * len(legs)
        fee = sum(costs(abs(b), abs(a), QTY) if s < 0 else costs(abs(a), abs(b), QTY)
                  for (s, _, _), a, b in zip(legs, v0, v1))
        dte = (exp - D["day"]).days
        out.append(dict(date=D["day"], pts=pts, net=pts * QTY - fee, risk=risk * QTY, why=why, dte=dte))
    return pd.DataFrame(out)


def atm(spot):
    return round(spot / STEP) * STEP


def iron_fly(wing):
    def f(spot, D, i):
        k = atm(spot)
        return dict(legs=[(-1, k, True), (-1, k, False), (1, k + wing, True), (1, k - wing, False)],
                    max_loss=lambda c: wing - c)
    return f


def iron_condor(short, wing):
    def f(spot, D, i):
        k = atm(spot)
        return dict(legs=[(-1, k + short, True), (-1, k - short, False),
                          (1, k + short + wing, True), (1, k - short - wing, False)],
                    max_loss=lambda c: wing - c)
    return f


def trend_spread(width):
    """At 14:00, sell a put spread on an up day or a call spread on a down day (late-momentum rule)."""
    def f(spot, D, i):
        mv = (D["c"][i - 1] - D["pdc"]) / D["pdc"]
        if abs(mv) < C.LATE_MIN_MOVE:
            return None
        d, k = (1 if mv > 0 else -1), atm(spot)
        legs = [(-1, k, False), (1, k - width, False)] if d > 0 else [(-1, k, True), (1, k + width, True)]
        return dict(legs=legs, max_loss=lambda c: width - c, dir=d,
                    idx_stop=spot - d * C.LATE_STOP_ATR * D["atr"][i - 1])
    return f


from datetime import time
T920, T1400 = time(9, 20), time(14, 0)
STRUCTS = {
    "iron fly, 200 wings, no stop":          (iron_fly(200), T920, None),
    "iron fly, 200 wings, stop at 40% risk": (iron_fly(200), T920, 0.4),
    "iron fly, 300 wings, stop at 40% risk": (iron_fly(300), T920, 0.4),
    "condor, sell 150 away, 200 wings":      (iron_condor(150, 200), T920, None),
    "condor, 150 away, stop at 40% risk":    (iron_condor(150, 200), T920, 0.4),
    "trend credit spread 14:00, 100 wide":   (trend_spread(100), T1400, None),
    "trend credit spread 14:00, 200 wide":   (trend_spread(200), T1400, None),
}


def row(t):
    if t.empty:
        return {}
    w, lo = t[t.net > 0].net.sum(), -t[t.net <= 0].net.sum()
    eq = t.net.cumsum()
    return dict(trades=len(t), win=round(100 * (t.net > 0).mean(), 1), pts=round(t.pts.mean(), 1),
                rs_trade=round(t.net.mean()), pf=round(w / lo, 2) if lo else 9.99,
                worst=round(t.net.min()), max_dd=round((eq - eq.cummax()).min()), risk_lot=round(t.risk.mean()))


if __name__ == "__main__":
    days = prepare(load("data/NIFTY_5m.csv"), load("data/VIX_5m.csv"))
    cut = int(len(days) * C.IN_SAMPLE_FRACTION)
    pd.set_option("display.width", 250)
    for label, dd in (("FIRST 70% OF DAYS", days[:cut]), ("LAST 30% OF DAYS", days[cut:])):
        res = {}
        for name, (b, e, sf) in STRUCTS.items():
            for decay, tag in (("trading", "fast"), ("calendar", "slow")):
                res[(name, tag)] = row(simulate(dd, b, e, decay, sf))
        print(f"\n{label}: {dd[0]['day']} to {dd[-1]['day']} ({len(dd)} days)   [pts = premium points per trade, rs = per lot after costs]")
        print(pd.DataFrame(res).T.to_string())
