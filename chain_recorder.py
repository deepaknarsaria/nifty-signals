"""Record option chain snapshots (OI, volume, LTP, bid/ask) every 5 minutes.

    python chain_recorder.py --symbol NIFTY

Why: Kite has no intraday history for expired options, so OI-based rules
(PCR, OI walls, OI build-up) can only be tested on data you record yourself.
Start this every trading morning; each day is saved to chain/SYMBOL_YYYY-MM-DD.csv.
"""
import argparse
import os
import time as _time
from datetime import date, datetime, time

import pandas as pd

import config as C
from kite_data import get_kite

STRIKES_EACH_SIDE = 12


def nearest_chain(kite, symbol):
    ins = pd.DataFrame(kite.instruments("NFO"))
    ins = ins[(ins.name == symbol) & (ins.instrument_type.isin(["CE", "PE"]))]
    ins["expiry"] = pd.to_datetime(ins.expiry).dt.date
    expiry = ins[ins.expiry >= date.today()].expiry.min()
    return ins[ins.expiry == expiry], expiry


def snapshot(kite, chain, expiry, spec):
    spot = kite.ltp([spec["kite_symbol"]])[spec["kite_symbol"]]["last_price"]
    atm = round(spot / spec["step"]) * spec["step"]
    band = STRIKES_EACH_SIDE * spec["step"]
    sel = chain[(chain.strike >= atm - band) & (chain.strike <= atm + band)]
    q = kite.quote(["NFO:" + s for s in sel.tradingsymbol])
    now, rows = datetime.now().replace(microsecond=0), []
    for _, r in sel.iterrows():
        d = q.get("NFO:" + r.tradingsymbol)
        if not d:
            continue
        depth = d.get("depth", {})
        rows.append(dict(time=now, spot=spot, expiry=expiry, strike=r.strike, type=r.instrument_type,
                         ltp=d["last_price"], oi=d.get("oi", 0), volume=d.get("volume", 0),
                         bid=(depth.get("buy") or [{}])[0].get("price"),
                         ask=(depth.get("sell") or [{}])[0].get("price"), lot=r.lot_size))
    return pd.DataFrame(rows), spot


def main(symbol, every):
    kite, spec = get_kite(), C.INSTRUMENTS[symbol]
    chain, expiry = nearest_chain(kite, symbol)
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
                snap, spot = snapshot(kite, chain, expiry, spec)
                snap.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
                ce, pe = snap[snap.type == "CE"].oi.sum(), snap[snap.type == "PE"].oi.sum()
                print(f"{datetime.now():%H:%M:%S} spot {spot:.1f}  PCR {pe / ce:.2f}" if ce else "no OI yet")
            except Exception as e:  # keep recording through a bad tick or network blip
                print("snapshot failed:", e)
        _time.sleep(every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--every", type=int, default=300)
    a = ap.parse_args()
    main(a.symbol, a.every)
