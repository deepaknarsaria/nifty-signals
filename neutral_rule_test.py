"""Range-day hedged selling (Iron Butterfly = hedged straddle, Iron Condor = hedged strangle) on recorded bars + real bid/ask."""
import glob
import pandas as pd
from backtest import costs
LOT = 65
STRUCT = {"Iron Butterfly": [(-1, 0, "CE"), (-1, 0, "PE"), (1, 200, "CE"), (1, -200, "PE")],
          "Iron Condor": [(-1, 150, "CE"), (-1, -150, "PE"), (1, 350, "CE"), (1, -350, "PE")]}

def run(day, sname, quiet=6, start="10:30", end="13:30", tp=0.3, sl=0.5, band=1, walls=False):
    b = pd.read_csv(f"barlog/NIFTY_{day}.csv", parse_dates=["time"]).drop_duplicates("time").reset_index(drop=True)
    c = pd.read_csv(f"chain/NIFTY_{day}.csv", parse_dates=["time"])
    snaps = sorted(c.time.unique()); by = {t: g.set_index(["strike", "type"]) for t, g in c.groupby("time")}
    def px(t, k, ty, side):
        ts = [s for s in snaps if s <= t]
        if not ts: return None
        s = by[ts[-1]]
        if (k, ty) not in s.index: return None
        r = s.loc[(k, ty)]; v = r[side] if r[side] > 0 else r.ltp
        return float(v) if v > 0 else None
    legs0 = STRUCT[sname]; pos = None
    for i in range(quiet, len(b)):
        r = b.iloc[i]; t = r.time + pd.Timedelta(minutes=5)
        if pos is None:
            win = b.iloc[i - quiet + 1:i + 1]
            ok = pd.Timestamp(start).time() <= t.time() <= pd.Timestamp(end).time() and (win.score.abs() <= band).all()
            if walls: ok = ok and r.put_wall < r.spot < r.call_wall
            if not ok: continue
            atm = round(r.spot / 50) * 50
            legs = [(q, atm + o, ty) for q, o, ty in legs0]
            e = [px(t, k, ty, "bid" if q < 0 else "ask") for q, k, ty in legs]
            if None in e: continue
            credit = -sum(q * p for (q, _, _), p in zip(legs, e))
            if credit <= 0: continue
            pos = dict(legs=legs, e=e, credit=credit, t=t)
            continue
        x = [px(t, k, ty, "ask" if q < 0 else "bid") for q, k, ty in pos["legs"]]
        if None in x: continue
        cost = -sum(q * p for (q, _, _), p in zip(pos["legs"], x))
        gain = pos["credit"] - cost
        reason = "time" if t.time() >= pd.Timestamp("15:10").time() else "target" if gain >= tp * pos["credit"] else "stop" if gain <= -sl * pos["credit"] else None
        if reason:
            fee = sum(costs(xp, ep, LOT) if q < 0 else costs(ep, xp, LOT) for (q, _, _), ep, xp in zip(pos["legs"], pos["e"], x))
            return dict(day=day, entry=pos["t"].strftime("%H:%M"), exit=t.strftime("%H:%M"), reason=reason, pnl=round(gain * LOT - fee))
    return None

days = sorted(f[-14:-4] for f in glob.glob("barlog/NIFTY_*.csv"))[:-1]   # skip today (partial)
rows = []
for s in STRUCT:
    for kw in [dict(), dict(quiet=4), dict(quiet=8), dict(tp=0.5), dict(sl=0.3), dict(band=0), dict(walls=True), dict(start="11:30")]:
        res = pd.DataFrame([x for d in days if (x := run(d, s, **kw))])
        if res.empty: rows.append(dict(structure=s, variant=str(kw), trades=0)); continue
        rows.append(dict(structure=s, variant=str(kw) or "base", trades=len(res), total=res.pnl.sum(), win=f"{(res.pnl > 0).mean():.0%}", worst=res.pnl.min(), best=res.pnl.max()))
print(len(days), "days"); print(pd.DataFrame(rows).sort_values("total", ascending=False).to_string(index=False))
