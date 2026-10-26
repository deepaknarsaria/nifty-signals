"""Stand-alone hedged (credit spread) setups, tested on recorded bars + real option chain bid/ask."""
import glob
import pandas as pd
from backtest import costs
LOT, W = 65, 200

def run(day, filt, stop_atr=1.5, tp=0.5, start="09:45", end="13:30", maxn=2, short_off=0):
    b = pd.read_csv(f"barlog/NIFTY_{day}.csv", parse_dates=["time"]).drop_duplicates("time").reset_index(drop=True)
    c = pd.read_csv(f"chain/NIFTY_{day}.csv", parse_dates=["time"])
    snaps = sorted(c.time.unique()); by = {t: g.set_index(["strike", "type"]) for t, g in c.groupby("time")}
    def px(t, k, ty, side, first=True):
        ts = [s for s in snaps if (s >= t if first else s <= t)]
        if not ts: return None
        s = by[ts[0] if first else ts[-1]]
        if (k, ty) not in s.index: return None
        r = s.loc[(k, ty)]; v = r[side] if r[side] > 0 else r.ltp
        return float(v) if v > 0 else None
    out, pos, n, cool = [], None, 0, 0
    for i in range(1, len(b)):
        r, p = b.iloc[i], b.iloc[i - 1]
        t = r.time
        if pos:
            d = pos["d"]
            # mark: cost to close = buy back short at ask, sell long at bid
            sh = px(t, pos["ks"], pos["ty"], "ask", False); lg = px(t, pos["kl"], pos["ty"], "bid", False)
            reason = None
            if t.time() >= pd.Timestamp("15:10").time(): reason = "time"
            elif d * (r.spot - pos["stop"]) <= 0: reason = "stop"
            elif sh is not None and lg is not None and (sh - lg) <= pos["credit"] * (1 - tp): reason = "target"
            if reason and sh is not None and lg is not None:
                pnl = (pos["credit"] - (sh - lg)) * LOT - costs(sh, pos["s0"], LOT) - costs(pos["l0"], lg, LOT)
                out.append(dict(day=day, d=d, entry=pos["t"].strftime("%H:%M"), exit=t.strftime("%H:%M"), reason=reason, pnl=round(pnl)))
                pos, cool = None, 3
            continue
        if cool: cool -= 1; continue
        if n >= maxn or not (pd.Timestamp(start).time() <= t.time() <= pd.Timestamp(end).time()): continue
        for d in (1, -1):
            if filt(r, p, d):
                atm = round(r.spot / 50) * 50 - d * short_off
                ty = "PE" if d > 0 else "CE"
                ks, kl = atm, atm - d * W
                s0 = px(t, ks, ty, "bid"); l0 = px(t, kl, ty, "ask")
                if s0 and l0 and s0 > l0:
                    pos = dict(d=d, ks=ks, kl=kl, ty=ty, s0=s0, l0=l0, credit=s0 - l0, t=t, stop=r.spot - d * stop_atr * r.atr); n += 1
                break
    return out

F = {
 "score>=2 x2": lambda r, p, d: d * r.score >= 2 and d * p.score >= 2,
 "score>=2 x2 + session": lambda r, p, d: d * r.score >= 2 and d * p.score >= 2 and d * r.session > 0,
 "score>=2 x2 + PCR side": lambda r, p, d: d * r.score >= 2 and d * p.score >= 2 and (r.pcr >= 1 if d > 0 else r.pcr <= 1),
 "score>=3 x2": lambda r, p, d: d * r.score >= 3 and d * p.score >= 3,
 "score>=2 x2 + OI bias side": lambda r, p, d: d * r.score >= 2 and d * p.score >= 2 and d * r.oi_bias >= 0,
}
days = sorted(f[-14:-4] for f in glob.glob("barlog/NIFTY_*.csv"))
rows = []
for name, f in F.items():
    for kw in [dict(), dict(short_off=100), dict(tp=0.3), dict(stop_atr=2.5)]:
        res = pd.DataFrame([x for dy in days for x in run(dy, f, **kw)])
        if res.empty: continue
        dd = res.groupby("day").pnl.sum()
        rows.append(dict(rule=name, variant=str(kw) or "base", trades=len(res), per_day=round(len(res) / len(days), 1), total=res.pnl.sum(),
                         win=f"{(res.pnl > 0).mean():.0%}", worst=res.pnl.min(), up_days=f"{(dd > 0).sum()}/{len(days)}"))
pd.set_option("display.width", 200)
print(len(days), "days:", days[0], "to", days[-1]); print(pd.DataFrame(rows).sort_values("total", ascending=False).to_string(index=False))
