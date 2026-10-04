"""Pull real 5-minute candles for LIVE NIFTY option contracts (expired ones are not available).
Usage: python fetch_option_history.py 2026-10-06 22200 23500 50 2026-09-14
Resumable: strikes already saved are skipped. Output: data/opt_<expiry>.csv
"""
import os, sys, time
from datetime import date
import pandas as pd
import angel_data as A

exp, lo, hi, step, start = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
budget = float(sys.argv[6]) if len(sys.argv) > 6 else 36
m = A.scrip_master()
m = m[(m.exch_seg == "NFO") & (m.name == "NIFTY")].copy()
m["exp"] = pd.to_datetime(m.expiry, format="%d%b%Y").dt.date.astype(str)
m["k"] = m.strike.astype(float) / 100
m = m[(m.exp == exp) & (m.k >= lo) & (m.k <= hi) & (m.k % step == 0)]
path = f"data/opt_{exp}.csv"
done = set()
if os.path.exists(path):
    d = pd.read_csv(path); done = set(zip(d.strike, d.type))
api, t0, n = A.Angel(), time.time(), 0
for r in m.sort_values("k").itertuples():
    typ = r.symbol[-2:]
    if (r.k, typ) in done:
        continue
    if time.time() - t0 > budget:
        print("time budget reached, run again to continue"); break
    df = api.candles("NFO", r.token, start, date.today())
    time.sleep(0.36)
    if len(df):
        df["strike"], df["type"] = r.k, typ
        df.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
    n += 1
left = len(m) - len(done) - n
print(f"{exp}: fetched {n} contracts this run, {max(left,0)} left")
