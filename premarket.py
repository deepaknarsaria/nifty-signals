"""Pre-market Telegram brief for NIFTY.

    python premarket.py            # scheduled use: brief at 9:01, opening indication at 9:12
    python premarket.py --test     # print both messages now, send nothing

What it uses
  * Angel One: NIFTY futures (in the pre-open session since 7 Sep 2026), MCX crude oil,
    USDINR futures, India VIX, NIFTY last close.
  * Finnhub (optional, free key in FINNHUB_API_KEY): last US session via SPY / QQQ / DIA.
GIFT Nifty itself is not available from Angel One and has no free official feed, so the
opening gap is read from NSE's own pre-open price at about 9:12 instead.
The sentiment label is a simple transparent score, not a backtested signal.
"""
import argparse
import os
import time as _time
from datetime import datetime, time, timedelta

import pandas as pd
import requests

import config as C
from angel_data import Angel, _env, now_ist, scrip_master
from live_signals import telegram

GAP_FLAT = 0.25  # % either side of zero that counts as a flat open


def near_future(m, seg, name):
    f = m[(m.exch_seg == seg) & (m.name == name) & m.instrumenttype.str.startswith("FUT")].copy()
    f["exp"] = pd.to_datetime(f.expiry, format="%d%b%Y").dt.date
    f = f[f.exp >= now_ist().date()].sort_values("exp")
    monthly = f[f.symbol.str.match(rf"^{name}\d{{2}}[A-Z]{{3}}FUT$")]   # skip thin weekly currency contracts
    f = monthly if len(monthly) else f
    return None if f.empty else str(f.token.iloc[0])


def fresh(q):
    """True if the quote was updated today (so it reflects this morning, not yesterday)."""
    try:
        return datetime.strptime(q["exchFeedTime"], "%d-%b-%Y %H:%M:%S").date() == now_ist().date()
    except Exception:
        return False


def angel_snapshot(api, m):
    tok = dict(nifty=C.INSTRUMENTS["NIFTY"]["angel_token"], vix=C.VIX_ANGEL_TOKEN,
               fut=near_future(m, "NFO", "NIFTY"), crude=near_future(m, "MCX", "CRUDEOIL"),
               usdinr=near_future(m, "CDS", "USDINR"))
    req = {"NSE": [tok["nifty"], tok["vix"]]}
    for seg, key in (("NFO", "fut"), ("MCX", "crude"), ("CDS", "usdinr")):
        if tok[key]:
            req[seg] = [tok[key]]
    got = {str(q["symbolToken"]): q for q in api.quote(req)}
    snap = {k: got.get(v) for k, v in tok.items()}
    for k in ("crude", "usdinr"):
        if snap[k] and not snap[k].get("ltp"):
            snap[k] = None                               # no price: leave the line out
    snap["fut_prev"] = None
    if tok["fut"]:                                       # yesterday's last futures price, from candles
        try:
            today = now_ist().date()
            c = api.candles("NFO", tok["fut"], today - timedelta(days=7), today)
            c["date"] = pd.to_datetime(c["date"]).dt.tz_localize(None)
            c = c[c.date.dt.date < today]
            snap["fut_prev"] = float(c.close.iloc[-1]) if len(c) else None
        except Exception:
            pass
    return snap


def us_markets():
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        return None
    out = {}
    for sym, name in (("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow")):
        try:
            r = requests.get("https://finnhub.io/api/v1/quote", params={"symbol": sym, "token": key}, timeout=15).json()
            if r.get("pc"):
                out[name] = (r["c"] - r["pc"]) / r["pc"] * 100
        except Exception:
            pass
    return out or None


def step(x, small, big):
    return 0 if abs(x) < small else (0.5 if abs(x) < big else 1.0) * (1 if x > 0 else -1)


def score(us, crude, rupee, gap=None):
    """Positive = bullish for NIFTY. Each input adds at most +/-1 (gap +/-1.5)."""
    parts = {}
    if us:
        parts["US markets"] = step(sum(us.values()) / len(us), 0.2, 0.5)
    if crude is not None:
        parts["crude oil"] = -step(crude, 0.75, 1.5)          # dearer crude hurts India
    if rupee is not None:
        parts["rupee"] = -0.5 * step(rupee, 0.1, 0.2)          # weaker rupee is a mild negative
    if gap is not None:
        parts["opening gap"] = 1.5 * step(gap, GAP_FLAT, 0.5)
    total = sum(parts.values())
    label = ("BULLISH" if total >= 1.5 else "MILDLY BULLISH" if total >= 0.5 else
             "BEARISH" if total <= -1.5 else "MILDLY BEARISH" if total <= -0.5 else "FLAT / NEUTRAL")
    pos, neg = [k for k, v in parts.items() if v > 0], [k for k, v in parts.items() if v < 0]
    why = "\n".join(x for x in ("Supportive: " + ", ".join(pos) if pos else "", "Negative: " + ", ".join(neg) if neg else "") if x) or "No strong cue either way"
    return label, why


def pct(q):
    return float(q["percentChange"]) if q and q.get("percentChange") is not None else None


def brief(s, us, tag=""):
    crude = pct(s["crude"]) if s["crude"] and fresh(s["crude"]) else None
    rupee = pct(s["usdinr"]) if s["usdinr"] and fresh(s["usdinr"]) else None
    lines = [f"{tag}PRE-MARKET BRIEF  {now_ist():%d %b, %H:%M}", "", "US last night"]
    lines.append(" | ".join(f"{k} {v:+.1f}%" for k, v in us.items()) if us else "Not connected")
    if s["crude"]:
        lines += ["", "Crude oil (MCX)", f"Rs {s['crude']['ltp']:,.0f}" +
                  (f", {crude:+.1f}% since last night" if crude is not None else " (last close, market not open yet)")]
    if s["usdinr"]:
        lines += ["", "Rupee", f"USD/INR {s['usdinr']['ltp']:.2f}" +
                  (f", {'rupee weaker' if rupee > 0 else 'rupee stronger'} {abs(rupee):.2f}%" if rupee is not None else " (last close)")]
    india = []
    if s["vix"]:
        india.append(f"VIX {s['vix']['ltp']:.1f} (last close)")
    if s["nifty"]:
        india.append(f"NIFTY last close {s['nifty']['ltp']:,.1f}")
    if india:
        lines += ["", "India"] + india
    label, why = score(us, crude, rupee)
    lines += ["", f"EARLY READ: {label}", why, "", "Opening gap follows at about 9:12."]
    return "\n".join(lines), (crude, rupee)


def gap_alert(s, us, crude, rupee, tag=""):
    f, n, prev = s["fut"], s["nifty"], s.get("fut_prev")
    if not f or not prev or not fresh(f) or abs(f["ltp"] - prev) < 0.05:
        return f"{tag}OPENING INDICATION\n\nPre-open price not available from the data feed.\nThe gap will be in the 9:20 brief."
    gap = (f["ltp"] - prev) / prev * 100
    kind = "GAP UP" if gap > GAP_FLAT else "GAP DOWN" if gap < -GAP_FLAT else "FLAT OPEN"
    label, why = score(us, crude, rupee, gap)
    est = f"\nImplied NIFTY open about {n['ltp'] * (1 + gap / 100):,.0f}" if n else ""
    return (f"{tag}OPENING INDICATION  {now_ist():%H:%M}\n\n{kind} {gap:+.2f}%\n\n"
            f"NIFTY futures pre-open {f['ltp']:,.1f}\nLast close {prev:,.1f}{est}\n\n"
            f"SENTIMENT TODAY: {label}\n{why}\n\n"
            "A read of the cues, not a trade signal.")


def wait_until(t):
    while now_ist().time() < t:
        _time.sleep(15)


def main(test, to_telegram=False):
    _env()
    api, m = Angel(), scrip_master()
    send = telegram if (to_telegram or not test) else print
    tag = "[TEST] " if test else ""
    if not test:
        if now_ist().time() > time(9, 25):
            return                                     # too late to be useful; the 9:20 brief covers it
        wait_until(time(9, 1))                         # MCX crude and USDINR open at 9:00
    us = us_markets()
    text, (crude, rupee) = brief(angel_snapshot(api, m), us, tag)
    send(text)
    if not test:
        wait_until(time(9, 12, 20))                    # pre-open matching is done by 9:12
    send(gap_alert(angel_snapshot(api, m), us, crude, rupee, tag))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="run now and print, send nothing")
    ap.add_argument("--send-test", action="store_true", help="run now and send the test messages to Telegram")
    a = ap.parse_args()
    main(a.test or a.send_test, a.send_test)
