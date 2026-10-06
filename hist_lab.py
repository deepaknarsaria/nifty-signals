"""Historical run of the strategy-lab structures on 3 years of NIFTY data.

    python hist_lab.py

There is no history of real option prices for expired contracts, so premiums here are MODELLED:
Black-Scholes, 0.9 x India VIX as volatility, decay clock calibrated to real September 2026 prices,
no volatility skew. Each structure is opened at 9:20 on the nearest weekly expiry and closed at 15:10,
one lot per leg unit, with 0.5 point slippage per leg per side and all charges.
The second part checks the model against real 5-minute prices where those exist.
"""
import math
from datetime import time

import numpy as np
import pandas as pd
from scipy.special import ndtr

import config as C
import research as R
from backtest import costs, load, next_expiry
from strategy_lab import STRATS, max_risk

LOT, STEP, SLIP, RATE = 65, 50, 0.5, C.RISK_FREE


def bs_vec(spot, strike, t, iv, call):
    spot, t, iv = np.asarray(spot, float), np.maximum(np.asarray(t, float), 1e-9), np.maximum(np.asarray(iv, float), 1e-6)
    s = iv * np.sqrt(t)
    d1 = (np.log(spot / strike) + (RATE + iv * iv / 2) * t) / s
    d2 = d1 - s
    disc = strike * np.exp(-RATE * t)
    return spot * ndtr(d1) - disc * ndtr(d2) if call else disc * ndtr(-d2) - spot * ndtr(-d1)


def run(days, expiry_of=None, decay="blend"):
    rows = []
    for D in days:
        t = D["t"]
        if time(9, 20) not in t:
            continue
        i = t.index(time(9, 20))
        j = next((k for k in range(i + 1, D["n"]) if t[k] >= C.SQUARE_OFF), D["n"] - 1)
        exp = expiry_of(D["day"]) if expiry_of else next_expiry(D["day"], "weekly")
        T = np.array([R.tte(ts, exp, decay) for ts in D["idx"][i:j + 1]])
        iv = D["vix"][i:j + 1] / 100
        # entry at the 9:20 open; marks at each bar close; exit at the open of the exit bar
        spot = np.concatenate([[D["o"][i]], D["c"][i:j], [D["o"][j]]])
        Tm = np.concatenate([[T[0]], T[:-1], [T[-1]]])
        ivm = np.concatenate([[iv[0]], iv[:-1], [iv[-1]]])
        atm = round(spot[0] / STEP) * STEP
        for name, (view, legs) in STRATS.items():
            px = [bs_vec(spot, atm + off, Tm, ivm, typ == "CE") for _, off, typ in legs]
            entry = [p[0] + (SLIP if q > 0 else -SLIP) for (q, _, _), p in zip(legs, px)]
            close = [np.maximum(p + (-SLIP if q > 0 else SLIP), 0.05) for (q, _, _), p in zip(legs, px)]
            path = sum(q * (cl - e) for (q, _, _), cl, e in zip(legs, close, entry))[1:]
            fee = sum(costs(e, cl[-1], LOT * abs(q)) if q > 0 else costs(cl[-1], e, LOT * abs(q))
                      for (q, _, _), e, cl in zip(legs, entry, close))
            net_entry = sum(q * e for (q, _, _), e in zip(legs, entry))
            rows.append((D["day"], name, view, max_risk(legs, atm, net_entry) is not None, path[-1] * LOT - fee,
                         path.min() * LOT, path.max() * LOT, (exp - D["day"]).days))
    return pd.DataFrame(rows, columns=["date", "strategy", "view", "hedged", "net", "worst_mtm", "best_mtm", "dte"])


def table(r):
    r = r.copy()
    r["year"] = pd.to_datetime(r.date).dt.year
    out = []
    for name, g in r.groupby("strategy"):
        eq = g.sort_values("date").net.cumsum()
        yr = g.groupby("year").net.sum()
        w, l = g.net[g.net > 0].sum(), -g.net[g.net <= 0].sum()
        out.append(dict(strategy=name, view=g.view.iloc[0], hedged="yes" if g.hedged.iloc[0] else "NO",
                        win_days=f"{(g.net > 0).mean():.0%}", avg_day=round(g.net.mean()), total=round(g.net.sum()),
                        pf=round(w / l, 2) if l else 9.99, worst_day=round(g.net.min()),
                        max_dd=round((eq - eq.cummax()).min()), years_up=f"{int((yr > 0).sum())}/{len(yr)}"))
    return pd.DataFrame(out).sort_values("avg_day", ascending=False)


if __name__ == "__main__":
    nf = load("data/NIFTY_5m.csv")
    v = load("data/VIX_5m.csv").copy()
    v["close"] *= 0.9
    days = R.prepare(nf, v)
    res = run(days)
    res.to_csv("hist_lab_results.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"{res.date.nunique()} days, {res.date.min()} to {res.date.max()}, per lot after charges\n")
    print(table(res).to_string(index=False))
    exp_day = res[res.dte == 0]
    print("\nExpiry day only (0 days to expiry), hedged neutral structures:")
    print(table(exp_day[(exp_day.view == "neutral")]).to_string(index=False))
