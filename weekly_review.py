"""Weekly review: which indicators were right this week, and how the paper trades did.

    python weekly_review.py            # print the review and save reviews/REVIEW_<date>.md
    python weekly_review.py --send     # also send a short summary to Telegram

Reads only what the live system recorded: paper_trades.csv, chain/ (option chain snapshots)
and barlog/ (one row per 5-minute bar with every indicator's reading).
"An indicator was right" = NIFTY moved in the indicated direction over the next 30 minutes.
Readings 5 minutes apart overlap heavily, so small samples here prove very little.
"""
import argparse
import glob
import os
from datetime import timedelta

import numpy as np
import pandas as pd

import config as C

FWD_BARS = 6          # 30 minutes ahead
MIN_N = 150           # below this many readings, or
MIN_DAYS = 8          # fewer than this many separate days, call it "too few to judge"


def oi_from_chain():
    """Per snapshot: PCR and OI bias (put OI added minus call OI added since the day's first snapshot)."""
    rows = []
    for f in sorted(glob.glob("chain/NIFTY_*.csv")):
        c = pd.read_csv(f, parse_dates=["time"])
        if c.empty:
            continue
        g = c.groupby(["time", "type"]).oi.sum().unstack().dropna()
        spot = c.groupby("time").spot.first()
        if "CE" not in g or "PE" not in g or len(g) < 3:
            continue
        ce0, pe0 = g.CE.iloc[0], g.PE.iloc[0]
        d = pd.DataFrame({"spot": spot.reindex(g.index), "pcr": g.PE / g.CE,
                          "oi_bias": ((g.PE - pe0) - (g.CE - ce0)) / (g.PE + g.CE)})
        d["day"] = d.index.date
        rows.append(d)
    return pd.concat(rows) if rows else pd.DataFrame()


def barlog():
    fs = sorted(glob.glob("barlog/NIFTY_*.csv"))
    if not fs:
        return pd.DataFrame()
    d = pd.concat(pd.read_csv(f, parse_dates=["time"]) for f in fs).set_index("time").sort_index()
    d["day"] = d.index.date
    return d


def add_forward(d):
    """Forward move in index points over about 30 minutes, same day only."""
    d = d.sort_index().copy()
    d["fwd"] = np.nan
    for _, g in d.groupby("day"):
        t = g.index
        later = g.spot.reindex(t + timedelta(minutes=5 * FWD_BARS), method="nearest", tolerance=timedelta(minutes=4))
        d.loc[t, "fwd"] = later.values - g.spot.values
    return d.dropna(subset=["fwd"])


def judge(d, mask, direction, label):
    x = d.loc[mask, "fwd"] * direction
    if len(x) == 0:
        return dict(indicator=label, readings=0, days=0, right="", avg_pts="", verdict="no readings")
    nd = d.loc[mask, "day"].nunique()
    v = "too few to judge" if (len(x) < MIN_N or nd < MIN_DAYS) else (
        "useful" if x.mean() > 3 and (x > 0).mean() > 0.53 else "wrong side" if x.mean() < -3 else "no edge")
    return dict(indicator=label, readings=len(x), days=nd, right=f"{(x > 0).mean():.0%}", avg_pts=f"{x.mean():+.1f}", verdict=v)


def indicator_table(days=None):
    out = []
    oi = oi_from_chain()
    if len(oi):
        if days:
            oi = oi[oi.day.isin(days)]
        oi = add_forward(oi)
        out += [judge(oi, oi.oi_bias >= C.OI_BIAS_MIN, 1, "OI bias bullish (puts being added)"),
                judge(oi, oi.oi_bias <= -C.OI_BIAS_MIN, -1, "OI bias bearish (calls being added)"),
                judge(oi, oi.pcr >= 1.2, 1, "PCR above 1.2 (bullish reading)"),
                judge(oi, oi.pcr <= 0.8, -1, "PCR below 0.8 (bearish reading)")]
    b = barlog()
    if len(b):
        if days:
            b = b[b.day.isin(days)]
        b = add_forward(b)
        up = (b.score >= C.SCORE_THRESHOLD)
        dn = (b.score <= -C.SCORE_THRESHOLD)
        out += [judge(b, up, 1, "Chart score +3 or more"), judge(b, dn, -1, "Chart score -3 or less"),
                judge(b, b.breadth >= C.BREADTH_BULL, 1, "Heavyweights above VWAP 60%+"),
                judge(b, b.breadth <= C.BREADTH_BEAR, -1, "Heavyweights above VWAP 40% or less"),
                judge(b, up & (b.breadth >= C.BREADTH_BULL) & (b.oi_bias >= C.OI_BIAS_MIN), 1, "All three bullish together"),
                judge(b, dn & (b.breadth <= C.BREADTH_BEAR) & (b.oi_bias <= -C.OI_BIAS_MIN), -1, "All three bearish together")]
    return pd.DataFrame(out)


def trades_summary(t):
    if t.empty:
        return "No paper trades.", {}
    w = t[t.pnl_per_lot > 0]
    s = dict(trades=len(t), wins=len(w), net=int(t.pnl_per_lot.sum()), best=int(t.pnl_per_lot.max()), worst=int(t.pnl_per_lot.min()))
    by = t.groupby("rule").pnl_per_lot.agg(["count", "sum"]).astype(int)
    lines = [f"{s['trades']} trades, {s['wins']} winners, net Rs {s['net']:+,} per lot",
             f"Best Rs {s['best']:+,} | Worst Rs {s['worst']:+,}"]
    lines += [f"Rule {r}: {int(v['count'])} trades, Rs {int(v['sum']):+,}" for r, v in by.iterrows()]
    lines += ["Exits: " + ", ".join(f"{k} x{v}" for k, v in t.reason.str.replace(r" \(.*\)", "", regex=True).value_counts().items())]
    return "\n".join(lines), s


def main(send, preview=False):
    t = pd.read_csv("paper_trades.csv", parse_dates=["date"]) if os.path.exists("paper_trades.csv") else pd.DataFrame()
    today = pd.Timestamp.now(tz="Asia/Kolkata").date()
    week_start = today - timedelta(days=today.weekday())
    done = [f for f in glob.glob("reviews/REVIEW_*.md") if pd.to_datetime(os.path.basename(f)[7:17]).date() >= week_start]
    if done and send and not preview:
        print("This week's review was already sent:", done[0])
        return
    wk = t[t.date.dt.date >= week_start] if len(t) else t
    wk_days = sorted({d for d in (pd.to_datetime(os.path.basename(f)[6:16]).date() for f in glob.glob("chain/NIFTY_*.csv")) if d >= week_start})
    wtxt, _ = trades_summary(wk)
    atxt, _ = trades_summary(t)
    week_tbl, all_tbl = indicator_table(wk_days), indicator_table()
    lab_md, lab_top = "No strategy lab results yet.", []
    if os.path.exists("lab_results.csv"):
        from strategy_lab import leaderboard
        lab = pd.read_csv("lab_results.csv", parse_dates=["date"])
        lb = leaderboard(lab)
        show = lb.assign(total=lb.total.astype(int), avg=lb.avg.round().astype(int), win=(lb.win * 100).round().astype(int).astype(str) + "%", worst=lb.worst.astype(int))
        lab_md = f"{lab.date.nunique()} days recorded. Paper results on real prices, 9:20 to 15:10, no stop or target.\n\n" + show.to_markdown()
        lab_top = [f"{k}: Rs {int(v.total):+,} over {int(v.days)} days ({v.win:.0%} winning days)" for k, v in lb.head(3).iterrows()]
    md = [f"# Weekly review, week of {week_start:%d %b %Y}", "",
          f"Market days recorded this week: {len(wk_days)}", "",
          "## Paper trades this week", "", wtxt.replace("\n", "  \n"), "",
          "## Paper trades since the start", "", atxt.replace("\n", "  \n"), "",
          "## Strategy lab: hedged structures, running total per lot", "",
          lab_md, "",
          "## Which indicators were right (next 30 minutes), all recorded days", "",
          all_tbl.to_markdown(index=False) if len(all_tbl) else "No indicator data recorded yet.", "",
          "## This week only", "",
          week_tbl.to_markdown(index=False) if len(week_tbl) else "No indicator data this week.", "",
          f"A verdict needs at least {MIN_N} readings across {MIN_DAYS} separate days. Readings 5 minutes apart overlap, so treat even 'useful' as a lead, not proof."]
    path = f"reviews/REVIEW_{today}.md"
    if not preview:
        os.makedirs("reviews", exist_ok=True)
        open(path, "w").write("\n".join(md) + "\n")
    print("\n".join(md))
    if send:
        from live_signals import telegram
        good = all_tbl[all_tbl.verdict == "useful"].indicator.tolist() if len(all_tbl) else []
        bad = all_tbl[all_tbl.verdict == "wrong side"].indicator.tolist() if len(all_tbl) else []
        few = int((all_tbl.verdict == "too few to judge").sum()) if len(all_tbl) else 0
        msg = [("[PREVIEW] " if preview else "") + f"WEEKLY REVIEW  week of {week_start:%d %b}", "", "This week", wtxt, "", "Since the start", atxt.split("\n")[0], "",
               "Strategy lab, best hedged structures so far"] + (lab_top or ["No results yet"]) + ["",
               "Indicators (right over the next 30 min)",
               "Useful: " + (", ".join(good) if good else "none proven yet"),
               "Wrong side: " + (", ".join(bad) if bad else "none"),
               f"Too few readings to judge: {few}", "",
               f"A verdict needs {MIN_N}+ readings over {MIN_DAYS}+ days.",
               "" if preview else f"Full tables saved in the repo: {path}"]
        telegram("\n".join(msg))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--preview", action="store_true", help="send a [PREVIEW] now without saving it as this week's review")
    a = ap.parse_args()
    main(a.send or a.preview, a.preview)
