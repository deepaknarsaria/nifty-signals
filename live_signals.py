"""Live NIFTY option alerts on Telegram, in PAPER-TRADING mode (no orders are placed).

    python live_signals.py                      # run 9:15 to 15:15 on a market day
    python live_signals.py --replay 2026-10-01  # dry run on a past day, prints to screen

Every 5 minutes it reads the NIFTY candles, the option chain (OI) and the heavyweight
stocks, then applies two rules:

  A  LATE MOMENTUM  (backtested): at 14:00, if NIFTY is 0.3%+ away from yesterday's close,
     buy an ATM option in that direction. Stop 3 ATR. Exit 15:10.
  B  TREND + CONFIRMATION  (experimental, not backtested with OI/breadth): chart score of
     +/-3 on two straight 5-min closes, AND heavyweight stocks agree, AND option OI agrees.
     Stop 1.5 ATR, target 2x the stop. Entries 9:30 to 13:45.

Each alert carries the real option price. Every trade is logged to paper_trades.csv with
real entry and exit premiums, which is the evidence for whether the rules make money.
"""
import argparse
import csv
import json
import os
import time as _time
from datetime import datetime, time, timedelta

import pandas as pd
import requests

import config as C
from angel_data import Angel, _env, now_ist, scrip_master
from backtest import bs_price, t_to_expiry
from signals import add_indicators, confirmed, faded

LOG = "paper_trades.csv"
FIELDS = ["date", "rule", "side", "strike", "expiry", "entry_time", "entry_spot", "entry_prem",
          "exit_time", "exit_spot", "exit_prem", "reason", "pnl_per_lot", "breadth", "oi_bias", "pcr"]


def telegram(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print("\n" + text + "\n")
    if not tok or not chat:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": text}, timeout=15)
    except Exception as e:
        print("telegram send failed:", type(e).__name__)  # never print the URL, it contains the bot token


class Engine:
    def __init__(self, notify, lot, step, log=LOG):
        self.notify, self.lot, self.step, self.log = notify, lot, step, log
        self.pos, self.done, self.cool = None, [], 0
        self.count = {"A": 0, "B": 0}

    # -- confirmations for rule B ------------------------------------------
    def agree(self, d, ctx):
        if ctx.get("replay"):
            return True
        b, o = ctx.get("breadth"), ctx.get("oi_bias")
        if b is None or o is None:
            return False
        return (b >= C.BREADTH_BULL and o >= C.OI_BIAS_MIN) if d > 0 else (b <= C.BREADTH_BEAR and o <= -C.OI_BIAS_MIN)

    # -- called once per completed 5-minute bar -----------------------------
    def on_bar(self, ind, ctx, now):
        last = ind.iloc[-1]
        sc = ind[ind.index.date == last.name.date()].score.values
        i, nxt = len(sc) - 1, (last.name + timedelta(minutes=5)).time()
        if self.pos:
            if self.pos["rule"] == "B" and faded(sc, i, self.pos["d"]):
                self.exit(ctx, last.close, now, "signal faded")
            return
        if self.cool > 0:
            self.cool -= 1
            return
        if pd.isna(last.atr) or pd.isna(last.pd_close) or nxt >= C.SQUARE_OFF:
            return
        if nxt == C.LATE_TIME and self.count["A"] < 1:
            mv = (last.close - last.pd_close) / last.pd_close
            if abs(mv) >= C.LATE_MIN_MOVE:
                self.enter("A", 1 if mv > 0 else -1, C.LATE_STOP_ATR * last.atr, None,
                           f"day move {mv:+.2%} at 14:00", last.close, ctx, now)
        elif C.ENTRY_START <= nxt <= C.TREND_ENTRY_END and self.count["B"] < 2:
            for d in (1, -1):
                if confirmed(sc, i, d) and self.agree(d, ctx):
                    sd = C.STOP_ATR * last.atr
                    self.enter("B", d, sd, sd * C.TARGET_R, f"chart score {int(last.score):+d}/5, stocks and OI agree",
                               last.close, ctx, now)
                    break

    def enter(self, rule, d, sd, td, why, spot, ctx, now):
        typ = "CE" if d > 0 else "PE"
        strike = round(spot / self.step) * self.step
        q = ctx["quote"](strike, typ)
        prem = q.get("ask") or q.get("ltp")
        if not prem:
            self.notify(f"Signal skipped: no price for {strike} {typ}")
            return
        stop, tgt = spot - d * sd, (spot + d * td) if td else None
        est = ctx["est"]
        self.pos = dict(rule=rule, d=d, typ=typ, strike=strike, prem=prem, spot=spot, stop=stop, tgt=tgt,
                        time=now, breadth=ctx.get("breadth"), oi_bias=ctx.get("oi_bias"), pcr=ctx.get("pcr"))
        self.count[rule] += 1
        name = "LATE MOMENTUM (tested rule)" if rule == "A" else "TREND + CONFIRMATION (experimental)"
        lines = [f"BUY NIFTY {strike} {typ}", f"Expiry {ctx['expiry']:%d %b}", "",
                 f"Rule: {name}", f"Why: {why}", "",
                 f"Entry premium: Rs {prem:.1f}", f"NIFTY at entry: {spot:,.1f}", "",
                 f"Stop loss: NIFTY {stop:,.1f} (premium about Rs {est(stop, strike, typ):.0f})",
                 (f"Target: NIFTY {tgt:,.1f} (premium about Rs {est(tgt, strike, typ):.0f})" if tgt
                  else "Target: none, hold to time exit"),
                 f"Time exit: {C.SQUARE_OFF:%H:%M}"]
        cl = self.context_line(ctx)
        lines += (["", cl] if cl else []) + ["", "PAPER TRADE. Not advice."]
        self.notify("\n".join(lines))

    @staticmethod
    def context_line(ctx):
        bits = []
        if ctx.get("breadth") is not None:
            bits.append(f"stocks above VWAP {ctx['breadth']:.0%}")
        if ctx.get("pcr") is not None:
            bits.append(f"PCR {ctx['pcr']:.2f}")
        if ctx.get("oi_bias") is not None:
            bits.append(f"OI bias {ctx['oi_bias']:+.1%}")
        return "Context: " + " | ".join(bits) if bits else ""

    # -- called between bars with the latest index price ---------------------
    def on_tick(self, spot, ctx, now):
        p = self.pos
        if not p:
            return
        d = p["d"]
        if now.time() >= C.SQUARE_OFF:
            self.exit(ctx, spot, now, "time exit")
        elif (d > 0 and spot <= p["stop"]) or (d < 0 and spot >= p["stop"]):
            self.exit(ctx, spot, now, "stop loss hit")
        elif p["tgt"] is not None and ((d > 0 and spot >= p["tgt"]) or (d < 0 and spot <= p["tgt"])):
            self.exit(ctx, spot, now, "target hit")

    def exit(self, ctx, spot, now, reason):
        p = self.pos
        note = ""
        try:
            q = ctx["quote"](p["strike"], p["typ"])
        except Exception as e:
            print("exit quote failed:", e)
            q = {}
        px = q.get("bid") or q.get("ltp")
        if not px:                                   # no live price: fall back to the model, and say so
            px, note = round(ctx["est"](spot, p["strike"], p["typ"]), 1), " (estimated, live price unavailable)"
        pnl = (px - p["prem"]) * self.lot
        row = dict(date=now.date(), rule=p["rule"], side=p["typ"], strike=p["strike"], expiry=ctx["expiry"],
                   entry_time=p["time"].strftime("%H:%M:%S"), entry_spot=round(p["spot"], 1), entry_prem=p["prem"],
                   exit_time=now.strftime("%H:%M:%S"), exit_spot=round(spot, 1), exit_prem=px, reason=reason,
                   pnl_per_lot=round(pnl), breadth=p["breadth"], oi_bias=p["oi_bias"], pcr=p["pcr"])
        self.done.append(row)
        if self.log:
            new = not os.path.exists(self.log)
            with open(self.log, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        self.notify(f"EXIT NIFTY {p['strike']} {p['typ']}\nReason: {reason}\n\n"
                    f"Premium: Rs {p['prem']:.1f} -> Rs {px:.1f}{note}\nNIFTY: {p['spot']:,.1f} -> {spot:,.1f}\n\n"
                    f"Result: Rs {pnl:+,.0f} per lot ({self.lot} qty), before charges")
        self.pos, self.cool = None, C.COOLDOWN_BARS

    def summary(self):
        if not self.done:
            return "DAY SUMMARY\n\nNo trades today."
        tot = sum(r["pnl_per_lot"] for r in self.done)
        return (f"DAY SUMMARY\n\n{len(self.done)} paper trade(s)\nTotal: Rs {tot:+,.0f} per lot, before charges\n\n"
                + "\n".join(f"{r['strike']} {r['side']} (rule {r['rule']}): {r['reason']}, Rs {r['pnl_per_lot']:+,}" for r in self.done))


# ------------------------------------------------------------------ live feed
class Live:
    def __init__(self, symbol):
        from chain_recorder import nearest_chain
        self.symbol, self.spec = symbol, C.INSTRUMENTS[symbol]
        self.api = Angel()
        self.chain, self.expiry = nearest_chain(symbol)
        self.lot = int(float(self.chain.lotsize.iloc[0]))
        m = scrip_master()
        eq = m[(m.exch_seg == "NSE") & m.symbol.isin([s + "-EQ" for s in C.HEAVYWEIGHTS])]
        self.stocks = {r.token: C.HEAVYWEIGHTS[r.symbol[:-3]] for r in eq.itertuples()}
        self.base_oi, self.vix, self.last_snap = None, C.FALLBACK_VIX, None

    def relogin(self):
        self.api = Angel()

    def candles(self):
        today = now_ist().date()
        df = self.api.candles("NSE", self.spec["angel_token"], today - timedelta(days=10), today)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df.set_index("date").sort_index()

    def spot(self):
        q = self.api.quote({"NSE": [self.spec["angel_token"]]}, mode="LTP")
        if not q:
            raise RuntimeError("no index quote returned")
        return q[0]["ltp"]

    def quote(self, strike, typ):
        row = self.chain[(self.chain.strike == strike) & (self.chain.type == typ)]
        if row.empty:
            return {}
        q = self.api.quote({"NFO": [row.token.iloc[0]]})
        if not q:
            return {}
        depth = q[0].get("depth") or {}
        return dict(ltp=q[0].get("ltp"), bid=(depth.get("buy") or [{}])[0].get("price"),
                    ask=(depth.get("sell") or [{}])[0].get("price"))

    def est(self, level, strike, typ):
        t = t_to_expiry(pd.Timestamp(now_ist()), self.expiry)
        return bs_price(level, strike, t, self.vix / 100, typ == "CE")

    def context(self):
        """Option chain + heavyweight stocks. Any part that fails is left as None."""
        from chain_recorder import snapshot
        ctx = dict(expiry=self.expiry, quote=self.quote, est=self.est, breadth=None, oi_bias=None, pcr=None)
        try:
            snap, _ = snapshot(self.api, self.chain, self.expiry, self.spec)
            os.makedirs("chain", exist_ok=True)
            path = f"chain/{self.symbol}_{now_ist().date()}.csv"
            snap.to_csv(path, mode="a", header=not os.path.exists(path), index=False)
            oi = snap.groupby("type").oi.sum()
            ce, pe = float(oi.get("CE", 0)), float(oi.get("PE", 0))
            if ce > 0:
                ctx["pcr"] = pe / ce
                if self.base_oi is None:
                    self.base_oi = (ce, pe)
                ctx["oi_bias"] = ((pe - self.base_oi[1]) - (ce - self.base_oi[0])) / (ce + pe)
            by = snap.pivot_table(index="strike", columns="type", values="oi", aggfunc="last")
            ctx["call_wall"], ctx["put_wall"] = by["CE"].idxmax(), by["PE"].idxmax()
            self.last_snap = snap
        except Exception as e:
            print("option chain unavailable:", e)
        try:
            _time.sleep(1.1)
            qs = self.api.quote({"NSE": list(self.stocks)})
            up = sum(self.stocks[str(q["symbolToken"])] for q in qs if q.get("ltp", 0) > q.get("avgPrice", 0))
            tot = sum(self.stocks[str(q["symbolToken"])] for q in qs)
            ctx["breadth"] = up / tot if tot else None
            v = self.api.quote({"NSE": [C.VIX_ANGEL_TOKEN]}, mode="LTP")
            if v:
                self.vix = v[0]["ltp"]
        except Exception as e:
            print("stock breadth unavailable:", e)
        return ctx


def run_live(symbol):
    _env()
    feed = Live(symbol)
    eng = Engine(telegram, feed.lot, feed.spec["step"])
    resume = os.getenv("RESUME_POS", "").strip()
    if resume:      # restarted mid-day: keep tracking the paper trade that was open
        p = json.loads(resume)
        p["d"] = 1 if p["typ"] == "CE" else -1
        p["time"] = datetime.combine(now_ist().date(), time.fromisoformat(p["time"]))
        for k in ("breadth", "oi_bias", "pcr", "tgt"):
            p.setdefault(k, None)
        eng.pos = p
        eng.count[p["rule"]] += 1
        telegram(f"SYSTEM RESTARTED after a fix\n\nStill tracking: NIFTY {p['strike']} {p['typ']} bought at Rs {p['prem']:.1f} ({p['time']:%H:%M})")
    else:
        telegram(f"SYSTEM STARTED (paper trading)\n\nNIFTY expiry {feed.expiry:%d %b}, lot size {feed.lot}")
    last_bar, ctx, brief = None, None, False
    errors, bar_fails, last_warn, last_spot = 0, 0, None, None

    def problem(e, now):
        nonlocal errors, last_warn
        errors += 1
        msg = str(e)[:140]
        print(f"{now:%H:%M:%S} error:", msg)
        if "AG" in msg or "token" in msg.lower():
            try:
                feed.relogin()
            except Exception as e2:
                print("re-login failed:", e2)
        if errors >= 5 and (last_warn is None or now - last_warn > timedelta(minutes=30)):
            telegram(f"Warning: data errors from Angel One\n\n{msg}\n\nThe system keeps retrying. Signals may be delayed.")
            last_warn = now

    while True:
        now = now_ist()
        if now.time() >= time(15, 10, 45) and not eng.pos:
            telegram(eng.summary())
            break
        if now.time() < time(9, 20):
            _time.sleep(20)
            continue
        ok = True
        # 1) once per completed 5-minute bar: candles, option chain, stocks, entry/exit rules
        try:
            bar = (now - timedelta(minutes=5, seconds=8)).replace(second=0, microsecond=0)
            bar -= timedelta(minutes=bar.minute % 5)
            if bar != last_bar and bar.time() < C.SQUARE_OFF:
                df = feed.candles()
                df = df[df.index <= bar]
                if len(df) and df.index[-1] == bar:
                    ctx = feed.context()
                    if not brief:
                        l = df.iloc[-1]
                        prev = df[df.index.date < now.date()].close.iloc[-1]
                        telegram(f"MORNING BRIEF  {now:%d %b, %H:%M}\n\n"
                                 f"NIFTY {l.close:,.1f} ({(l.close - prev) / prev:+.2%} vs yesterday)\nIndia VIX {feed.vix:.1f}\n\n"
                                 + eng.context_line(ctx) +
                                 (f"\n\nResistance: biggest call OI at {ctx['call_wall']:.0f}\nSupport: biggest put OI at {ctx['put_wall']:.0f}"
                                  if ctx.get("call_wall") else ""))
                        brief = True
                    eng.on_bar(add_indicators(df), ctx, now)
                    last_bar, bar_fails = bar, 0
                elif now.time() >= time(9, 40) and not eng.pos and (df.empty or df.index[-1].date() < now.date()):
                    telegram(f"{now:%d %b}: no NIFTY data today, market looks closed. Stopping.")
                    break
                elif now - bar > timedelta(minutes=8):
                    last_bar = bar  # candle never arrived; skip it
        except Exception as e:
            ok = False
            bar_fails += 1
            if bar_fails >= 6:
                last_bar, bar_fails = bar, 0   # give up on this bar so the loop cannot get stuck on it
            problem(e, now)
        # 2) every loop while a trade is open: stop loss, target and time exit. Runs even if step 1 failed.
        try:
            if eng.pos and ctx:
                tnow = now_ist()
                try:
                    last_spot = feed.spot()
                except Exception:
                    if tnow.time() < time(15, 12) or last_spot is None:
                        raise
                    print("spot unavailable after 15:12, closing on the last known price")
                eng.on_tick(last_spot, ctx, tnow)
        except Exception as e:
            ok = False
            problem(e, now)
        if ok:
            errors = 0
        _time.sleep(15)


# ---------------------------------------------------------------- replay mode
def run_replay(day, symbol, send=False):
    from backtest import load, next_expiry
    spec = C.INSTRUMENTS[symbol]
    px = load(f"data/{symbol}_5m.csv")
    vix = load("data/VIX_5m.csv")["close"]
    d = pd.Timestamp(day).date()
    hist = px[px.index.date <= d]
    today = hist[hist.index.date == d]
    if today.empty:
        raise SystemExit(f"No data for {day}")
    exp = next_expiry(d, spec["expiry"])
    state = {}

    def est(level, strike, typ):
        return bs_price(level, strike, t_to_expiry(state["now"], exp), state["vix"] / 100, typ == "CE")

    def quote(strike, typ):
        p = round(est(state["spot"], strike, typ), 1)
        return dict(ltp=p, bid=p - C.SLIPPAGE_PTS, ask=p + C.SLIPPAGE_PTS)

    ctx = dict(expiry=exp, quote=quote, est=est, replay=True)
    if send:
        _env()
        out = lambda s: (telegram(f"[SAMPLE {state['now']:%H:%M}]\n" + s), _time.sleep(1.2))
        telegram(f"[SAMPLE] Replay of {pd.Timestamp(day):%d %b %Y}, to show what a market day looks like.\n\n"
                 "Option prices are modelled, not live. Nothing below is a real signal.")
    else:
        out = lambda s: print(f"[{state['now']:%H:%M}] " + s.replace("\n", "\n        ") + "\n")
    eng = Engine(out, spec["lot"], spec["step"], log=None)
    print(f"REPLAY {day} (modelled option prices, rule B confirmations switched off)\n")
    for ts in today.index:
        bar = today.loc[ts]
        state["vix"] = float(vix[vix.index < ts].iloc[-1]) if (vix.index < ts).any() else C.FALLBACK_VIX
        ticks = (bar.open, bar.low, bar.high, bar.close)
        if eng.pos and eng.pos["d"] < 0:
            ticks = (bar.open, bar.high, bar.low, bar.close)
        for k, s in enumerate(ticks):
            state["now"], state["spot"] = ts + timedelta(minutes=k), s
            eng.on_tick(s, ctx, state["now"])
        state["now"], state["spot"] = ts + timedelta(minutes=5), bar.close
        if state["now"].time() < C.SQUARE_OFF:
            eng.on_bar(add_indicators(hist[hist.index <= ts].tail(600)), ctx, state["now"])
    state["now"] = pd.Timestamp.combine(d, time(15, 11))
    out(eng.summary())
    return eng


def selftest(symbol):
    """Check every live data path once, without sending signals. Works on a holiday too."""
    _env()
    feed = Live(symbol)
    print(f"login ok | expiry {feed.expiry} | lot {feed.lot} | heavyweights found {len(feed.stocks)}/{len(C.HEAVYWEIGHTS)}")
    df = feed.candles()
    print(f"candles: {len(df)} bars, last {df.index[-1]} close {df.close.iloc[-1]}")
    ind = add_indicators(df)
    print(f"indicators ok: score {int(ind.score.iloc[-1])}, atr {ind.atr.iloc[-1]:.1f}")
    ctx = feed.context()
    print("context:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in ctx.items() if k not in ("quote", "est")})
    spot = feed.spot()
    atm = round(spot / feed.spec["step"]) * feed.spec["step"]
    print(f"spot {spot} | ATM {atm} CE quote {feed.quote(atm, 'CE')} | model {feed.est(spot, atm, 'CE'):.1f} | vix {feed.vix}")
    print("telegram:", "configured" if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID") else "NOT configured yet")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY", choices=list(C.INSTRUMENTS))
    ap.add_argument("--replay", default="", help="YYYY-MM-DD: dry run on a past day")
    ap.add_argument("--send", action="store_true", help="with --replay: send the replay to Telegram as [SAMPLE] messages")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest(a.symbol)
    elif a.replay:
        run_replay(a.replay, a.symbol, a.send)
    else:
        run_live(a.symbol)
