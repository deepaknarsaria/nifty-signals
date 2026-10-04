"""Kite Connect login + historical 5-minute download.

Setup once:  set KITE_API_KEY and KITE_API_SECRET as environment variables
             (or put them in a file named .env next to this script).
Each morning: python kite_data.py login        (Kite tokens expire daily)
Download:     python kite_data.py download --symbol NIFTY --years 3
"""
import argparse
import json
import os
import sys
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd

import config as C

TOKEN_FILE = ".kite_token.json"


def _env():
    if os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    key, secret = os.getenv("KITE_API_KEY"), os.getenv("KITE_API_SECRET")
    if not key or not secret:
        sys.exit("Set KITE_API_KEY and KITE_API_SECRET (environment or .env file).")
    return key, secret


def get_kite():
    from kiteconnect import KiteConnect
    key, _ = _env()
    kite = KiteConnect(api_key=key)
    if os.path.exists(TOKEN_FILE):
        tok = json.load(open(TOKEN_FILE))
        if tok.get("date") == str(date.today()):
            kite.set_access_token(tok["access_token"])
            return kite
    sys.exit("No valid token for today. Run: python kite_data.py login")


def login():
    from kiteconnect import KiteConnect
    key, secret = _env()
    kite = KiteConnect(api_key=key)
    print("1. Open this URL and log in:\n  ", kite.login_url())
    raw = input("2. Paste the full redirect URL (or just the request_token): ").strip()
    if "request_token=" in raw:
        raw = raw.split("request_token=")[1].split("&")[0]
    sess = kite.generate_session(raw, api_secret=secret)
    json.dump({"date": str(date.today()), "access_token": sess["access_token"]}, open(TOKEN_FILE, "w"))
    print("Logged in. Token saved for today.")


def fetch(kite, token, start, end, interval="5minute", chunk_days=95):
    frames, cur = [], start
    while cur <= end:
        to = min(cur + timedelta(days=chunk_days), end)
        rows = kite.historical_data(token, datetime.combine(cur, datetime.min.time()),
                                    datetime.combine(to, datetime.max.time()), interval)
        if rows:
            frames.append(pd.DataFrame(rows))
        print(f"  {cur} to {to}: {len(rows)} bars")
        cur = to + timedelta(days=1)
        _time.sleep(0.4)  # stay under the 3 requests/second limit
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates("date")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.sort_values("date")


def download(symbol, years):
    kite = get_kite()
    end, start = date.today(), date.today() - timedelta(days=int(365 * years))
    os.makedirs("data", exist_ok=True)
    for name, token in ((symbol, C.INSTRUMENTS[symbol]["token"]), ("VIX", C.VIX_TOKEN)):
        print(f"Downloading {name} ...")
        df = fetch(kite, token, start, end)
        df.to_csv(f"data/{name}_5m.csv", index=False)
        print(f"Saved data/{name}_5m.csv  ({len(df)} bars)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "download"])
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--years", type=float, default=3)
    a = ap.parse_args()
    login() if a.cmd == "login" else download(a.symbol, a.years)
