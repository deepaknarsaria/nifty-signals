"""Signal trades re-done as spreads on real recorded bid/ask: debit (buy the signal side) vs credit (sell the other side)."""
import pandas as pd
from backtest import costs
LOT = 65
p = pd.read_csv("paper_trades.csv")
rows = []
for t in p.itertuples():
    try:
        c = pd.read_csv(f"chain/NIFTY_{t.date}.csv", parse_dates=["time"])
    except FileNotFoundError:
        continue
    bull = t.side == "CE"
    atm = round(t.entry_spot / 50) * 50
    def q(ts, strike, typ, first):
        s = c[(c.strike == strike) & (c.type == typ)]
        tt = pd.Timestamp(f"{t.date} {ts}")
        s = s[s.time >= tt] if first else s[s.time <= tt]
        return None if s.empty else (s.iloc[0] if first else s.iloc[-1])
    for w in (150, 200):
        # debit: Bull Call / Bear Put. credit: Bull Put / Bear Call (sell ATM on the side the market should move away from)
        plans = {"debit": [(1, atm, "CE" if bull else "PE"), (-1, atm + (w if bull else -w), "CE" if bull else "PE")],
                 "credit": [(-1, atm, "PE" if bull else "CE"), (1, atm - (w if bull else -w), "PE" if bull else "CE")]}
        for kind, legs in plans.items():
            e = [q(t.entry_time, k, ty, True) for _, k, ty in legs]
            x = [q(t.exit_time, k, ty, False) for _, k, ty in legs]
            if any(v is None for v in e + x):
                continue
            ep = [r.ask if n > 0 else r.bid for (n, _, _), r in zip(legs, e)]
            xp = [r.bid if n > 0 else r.ask for (n, _, _), r in zip(legs, x)]
            if min(ep + xp) <= 0:
                continue
            pts = sum(n * (b - a) for (n, _, _), a, b in zip(legs, ep, xp))
            fee = sum(costs(a, b, LOT) if n > 0 else costs(b, a, LOT) for (n, _, _), a, b in zip(legs, ep, xp))
            rows.append(dict(date=t.date, rule=t.rule, view="bull" if bull else "bear", width=w, kind=kind,
                             net=round(pts * LOT - fee), naked=t.pnl_per_lot, vix_hint=t.pcr))
r = pd.DataFrame(rows)
r.to_csv("spread_check_results.csv", index=False)
name = {("bull", "debit"): "Bull Call Spread", ("bull", "credit"): "Bull Put Spread",
        ("bear", "debit"): "Bear Put Spread", ("bear", "credit"): "Bear Call Spread"}
r["structure"] = [name[v, k] for v, k in zip(r.view, r.kind)]
pd.set_option("display.width", 200)
for (rule, w), g in r.groupby(["rule", "width"]):
    print(f"\nRule {rule}, {w} points wide")
    print(g.groupby("structure").agg(trades=("net", "size"), total=("net", "sum"), win=("net", lambda s: f"{(s > 0).mean():.0%}"),
                                    worst=("net", "min"), naked_same_trades=("naked", "sum")).to_string())
    print(g.groupby("kind").agg(trades=("net", "size"), total=("net", "sum"), win=("net", lambda s: f"{(s > 0).mean():.0%}"), worst=("net", "min")).to_string())
