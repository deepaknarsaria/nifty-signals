"""Record option chain snapshots (OI, volume, LTP, bid/ask) every 5 minutes.

    python chain_recorder.py --symbol NIFTY

Why: there is no intraday history for expired options, so OI-based rules
(PCR, OI walls, OI build-up) can only be tested on data you record yourself.
Start this every trading morning; each day is saved to chain/SYMBOL_YYYY-MM-DD.csv.
"""
import argparse
import os
import time as _time
from datetime import date, datetime, time

import pandas as pd

import config as C
from angel_data import Angel, scrip_master

STRIKES_EACH_SIDE = 12  # 25 strikes x CE/PE = 50 symbols = one quote request


def nearest_chain(symbol):
    m = scrip_master()
    m = m[(m.exch_seg == "NFO") & (m.instrumenttype == "OPTIDX") & (m.name == symbol)].copy()
    m["expiry"] = pd.to_datetime(m.expiry, format="%d%b%Y").dt.date
    m["strike"] = m.strike.astype(float) / 100
    m["type"] = m.symbol.str[-2:]
    expiry = m[m.expiry >= date.today()].expiry.min()
    return m[m.expiry == expiry], expiry


def snapshot(api, chain, expiry, spec):
    spot = api.quote({"NSE": [spec["angel_token"]]}, mode="LTP")[0]["ltp"]
    atm = round(spot / spec["step"]) * spec["step"]
    band = STRIKES_EACH_SIDE * spec["step"]
    sel = chain[(chain.strike >= atm - band) & (chain.strike <= atm + band)].set_index("token")
    tokens, got = list(sel.index), []
    for i in range(0, len(tokens), 50):
        _time.sleep(1.1)  # 50 symbols per request, 1 request per second
        got += api.quote({"NFO": tokens[i:i + 50]})
    now, rows = datetime.now().replace(microsecond=0), []
    for q in got:
        r = sel.loc[str(q["symbolToken"])]
        depth = q.get("depth") or {}
        rows.append(dict(time=now, spot=spot, expiry=expiry, strike=r.strike, type=r.type,
                         ltp=q.get("ltp"), oi=q.get("opnInterest", 0), volume=q.get("tradeVolume", 0),
                         bid=(depth.get("buy") or [{}])[0].get("price"),
                         ask=(depth.get("sell") or [{}])[0].get("price"), lot=r.lotsize))
    return pd.DataFrame(rows), spot


def main(symbol, every):
    api, spec = Angel(), C.INSTRUMENTS[symbol]
    chain, expiry = nearest_chain(symbol)
    os.makedirs("chain", exist_ok=True)
    path = f"chain/{symbol}_{date.today()}.csv"
    print(f"Recording {symbol} expiry {expiry} to {path} every {every}s. Ctrl+C to stop.")
    while True:
        now = datetime.now().time()
        if now > time(15, 31):
            print("Market closed. Done.")
            break
        if now >= time(9, 15):
            try:
                snap, spot = snapshot(api, chain, expiry, spec)
                snap.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
                ce, pe = snap[snap.type == "CE"].oi.sum(), snap[snap.type == "PE"].oi.sum()
                print(f"{datetime.now():%H:%M:%S} spot {spot:.1f}  PCR {pe / ce:.2f}" if ce else "no OI yet")
            except Exception as e:  # keep recording through a bad tick or network blip
                print("snapshot failed:", e)
                try:
                    api = Angel()  # session may have expired; log in again
                except Exception as e2:
                    print("re-login failed:", e2)
        _time.sleep(every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--every", type=int, default=300)
    a = ap.parse_args()
    main(a.symbol, a.every)
