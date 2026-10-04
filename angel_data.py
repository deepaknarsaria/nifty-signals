"""Angel One SmartAPI: login + historical 5-minute download.

Setup once: copy .env.example to .env and fill in the four values.
Check login:  python angel_data.py login
Download:     python angel_data.py download --symbol NIFTY --years 3

Login is automatic (client code + PIN + TOTP), so there is no daily browser step.
"""
import argparse
import os
import sys
import time as _time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

import config as C

BASE = "https://apiconnect.angelone.in"


def now_ist():
    """Current India time as a plain datetime, whatever the computer's clock zone is."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
SCRIP_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIP_CACHE = ".scrip_master.json"


def _env():
    if os.path.exists(".env"):
        for line in open(".env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    keys = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"]
    missing = [k for k in keys if not os.getenv(k)]
    if missing:
        sys.exit("Missing in .env: " + ", ".join(missing))
    return [os.getenv(k) for k in keys]


class Angel:
    def __init__(self):
        import pyotp
        key, client, pin, secret = _env()
        self.h = {"Content-Type": "application/json", "Accept": "application/json",
                  "X-UserType": "USER", "X-SourceID": "WEB", "X-ClientLocalIP": "127.0.0.1",
                  "X-ClientPublicIP": "127.0.0.1", "X-MACAddress": "00:00:00:00:00:00",
                  "X-PrivateKey": key}
        d = self._post("/rest/auth/angelbroking/user/v1/loginByPassword",
                       {"clientcode": client, "password": pin, "totp": pyotp.TOTP(secret).now()})
        self.h["Authorization"] = "Bearer " + d["jwtToken"]

    def _post(self, path, body):
        r = requests.post(BASE + path, json=body, headers=self.h, timeout=30)
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        if not (j.get("status") or j.get("success")):
            raise RuntimeError(f"Angel One error {j.get('errorcode') or j.get('errorCode')}: {j.get('message')}")
        return j.get("data")

    def candles(self, exchange, token, start, end, interval="FIVE_MINUTE"):
        rows = self._post("/rest/secure/angelbroking/historical/v1/getCandleData",
                          {"exchange": exchange, "symboltoken": str(token), "interval": interval,
                           "fromdate": f"{start} 09:15", "todate": f"{end} 15:40"}) or []
        return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])

    def quote(self, exchange_tokens, mode="FULL"):
        d = self._post("/rest/secure/angelbroking/market/v1/quote/",
                       {"mode": mode, "exchangeTokens": exchange_tokens}) or {}
        return d.get("fetched", [])


def scrip_master():
    """Angel One's instrument list, cached once per day (large file)."""
    fresh = os.path.exists(SCRIP_CACHE) and date.fromtimestamp(os.path.getmtime(SCRIP_CACHE)) == date.today()
    if not fresh:
        print("Downloading instrument list ...")
        r = requests.get(SCRIP_URL, timeout=120)
        r.raise_for_status()
        open(SCRIP_CACHE, "wb").write(r.content)
    return pd.read_json(SCRIP_CACHE, dtype=str)


def fetch(api, token, start, end, chunk_days=90):
    frames, cur = [], start
    while cur <= end:
        to = min(cur + timedelta(days=chunk_days), end)
        df = api.candles("NSE", token, cur, to)
        if len(df):
            frames.append(df)
        print(f"  {cur} to {to}: {len(df)} bars")
        cur = to + timedelta(days=1)
        _time.sleep(0.5)  # limit is 3 requests/second
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).drop_duplicates("date")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.sort_values("date")


def download(symbol, years):
    api = Angel()
    end, start = date.today(), date.today() - timedelta(days=int(365 * years))
    os.makedirs("data", exist_ok=True)
    for name, token in ((symbol, C.INSTRUMENTS[symbol]["angel_token"]), ("VIX", C.VIX_ANGEL_TOKEN)):
        print(f"Downloading {name} ...")
        df = fetch(api, token, start, end)
        if df.empty:
            print(f"  No data returned for {name}. Check the token in config.py.")
            continue
        df.to_csv(f"data/{name}_5m.csv", index=False)
        print(f"Saved data/{name}_5m.csv  ({len(df)} bars, {df.date.min()} to {df.date.max()})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["login", "download"])
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--years", type=float, default=3)
    a = ap.parse_args()
    if a.cmd == "login":
        Angel()
        print("Login OK.")
    else:
        download(a.symbol, a.years)
