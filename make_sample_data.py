"""Generate FAKE random-walk candles so you can check the code runs before
you have Kite data. Results on this data mean nothing about real markets.

    python make_sample_data.py && python backtest.py --data-dir sample_data
"""
import os
import numpy as np
import pandas as pd

rng = np.random.default_rng(7)
VOL, DAYS, BARS, SUB = 0.13, 500, 75, 5
sd = VOL / np.sqrt(252 * 375)  # per-minute volatility, all variance inside market hours
days = pd.bdate_range("2024-01-01", periods=DAYS)
rows, px = [], 22000.0
for d in days:
    for b in range(BARS):
        path = px * np.exp(np.cumsum(rng.normal(0, sd, SUB)))
        rows.append((d + pd.Timedelta(hours=9, minutes=15 + 5 * b), px, max(px, path.max()), min(px, path.min()), path[-1], 0))
        px = path[-1]
os.makedirs("sample_data", exist_ok=True)
df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
df.to_csv("sample_data/NIFTY_5m.csv", index=False)
v = df[["date"]].copy()
for k in ("open", "high", "low", "close"):
    v[k] = VOL * 100
v["volume"] = 0
v.to_csv("sample_data/VIX_5m.csv", index=False)
print("Wrote sample_data/ (fake data, for testing the code only)")
