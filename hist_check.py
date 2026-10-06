"""Check the pricing model against REAL option prices (5-minute candles of the 6 Oct 2026 weekly contract)."""
from datetime import date, time
import numpy as np, pandas as pd
import research as R
from backtest import costs, load
from strategy_lab import STRATS
from hist_lab import run, LOT, STEP, SLIP

o = pd.read_csv("data/opt_2026-10-06.csv")
o["date"] = pd.to_datetime(o["date"]).dt.tz_localize(None)
o["day"] = o.date.dt.date
nf = load("data/NIFTY_5m.csv"); v = load("data/VIX_5m.csv").copy(); v["close"] *= 0.9
days = [D for D in R.prepare(nf, v) if D["day"] in set(o.day)]
model = run(days, expiry_of=lambda d: date(2026, 10, 6))
rows = []
for D in days:
    g = o[o.day == D["day"]]
    spot0 = D["o"][D["t"].index(time(9, 20))]
    atm = round(spot0 / STEP) * STEP
    def px(strike, typ, when, last):
        s = g[(g.strike == strike) & (g.type == typ)]
        s = s[s.date.dt.time <= when] if last else s[(s.date.dt.time >= when) & (s.date.dt.time <= time(9, 35))]
        if s.empty: return None
        return float(s.close.iloc[-1]) if last else float(s.open.iloc[0])
    for name, (view, legs) in STRATS.items():
        e = [px(atm + off, t, time(9, 20), False) for _, off, t in legs]
        x = [px(atm + off, t, time(15, 5), True) for _, off, t in legs]
        if None in e or None in x: continue
        e = [p + (SLIP if q > 0 else -SLIP) for (q, _, _), p in zip(legs, e)]
        x = [max(p + (-SLIP if q > 0 else SLIP), 0.05) for (q, _, _), p in zip(legs, x)]
        fee = sum(costs(a, b, LOT * abs(q)) if q > 0 else costs(b, a, LOT * abs(q)) for (q, _, _), a, b in zip(legs, e, x))
        rows.append((D["day"], name, sum(q * (b - a) for (q, _, _), a, b in zip(legs, e, x)) * LOT - fee))
real = pd.DataFrame(rows, columns=["date", "strategy", "real"])
m = real.merge(model[["date", "strategy", "net", "hedged", "view"]], on=["date", "strategy"])
print(m.date.nunique(), "days", m.date.min(), m.date.max(), "| correlation real vs model:", round(m.real.corr(m.net), 3))
t = m.groupby("strategy").agg(days=("real", "size"), real_avg=("real", "mean"), model_avg=("net", "mean"),
                              real_win=("real", lambda s: (s > 0).mean()), real_worst=("real", "min")).round(2)
t["gap"] = (t.real_avg - t.model_avg).round(0)
pd.set_option("display.width", 200)
print(t.sort_values("real_avg", ascending=False).round(0).to_string())
m.to_csv("hist_check_results.csv", index=False)
