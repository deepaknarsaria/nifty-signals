"""Signal engine shared by the backtest and (later) the live Telegram alerts.

Input: 5-minute candles, DatetimeIndex = bar START time (Kite convention),
columns open/high/low/close/volume. Every value on a row uses only data
available at that bar's close, so nothing here looks into the future.

Score = sum of 5 components, each +1 (bullish), -1 (bearish) or 0:
  trend    close > EMA20 > EMA50            (reverse for bearish)
  session  close above/below session VWAP   (plain session average if no volume)
  orb      close beyond the opening 15-min range
  momentum RSI above 55 / below 45
  prevday  close beyond previous day's high / low
CALL when score >= +3 on two consecutive closes, PUT when <= -3.
"""
import numpy as np
import pandas as pd
import config as C


def _rsi(close, n):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _atr(df, n):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df):
    df = df.sort_index().copy()
    day = pd.Series(df.index.date, index=df.index)
    df["ema_fast"] = df["close"].ewm(span=C.EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=C.EMA_SLOW, adjust=False).mean()
    df["rsi"] = _rsi(df["close"], C.RSI_LEN)
    df["atr"] = _atr(df, C.ATR_LEN)

    # session VWAP (falls back to a running average of typical price for an index with no volume)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"] if "volume" in df and df["volume"].sum() > 0 else pd.Series(1.0, index=df.index)
    vol = vol.where(vol > 0, 0.0)
    cum_v = vol.groupby(day).cumsum()
    df["session_avg"] = ((tp * vol).groupby(day).cumsum() / cum_v.replace(0, np.nan)).ffill()

    # opening range: known only once the first N bars have closed
    n = C.OPENING_RANGE_BARS
    pos = df.groupby(day).cumcount()
    first = pos < n
    orh = df["high"].where(first).groupby(day).transform("max")
    orl = df["low"].where(first).groupby(day).transform("min")
    df["or_high"] = orh.where(pos >= n)
    df["or_low"] = orl.where(pos >= n)

    # previous day's high / low
    daily = df.groupby(day).agg(h=("high", "max"), l=("low", "min"), c=("close", "last")).shift(1)
    df["pd_high"] = day.map(daily["h"]).values
    df["pd_low"] = day.map(daily["l"]).values
    df["pd_close"] = day.map(daily["c"]).values

    c = df["close"]
    comp = pd.DataFrame(index=df.index)
    comp["trend"] = np.where((c > df.ema_fast) & (df.ema_fast > df.ema_slow), 1,
                             np.where((c < df.ema_fast) & (df.ema_fast < df.ema_slow), -1, 0))
    comp["session"] = np.sign(c - df.session_avg).fillna(0)
    comp["orb"] = np.where(c > df.or_high, 1, np.where(c < df.or_low, -1, 0))
    comp["momentum"] = np.where(df.rsi > C.RSI_BULL, 1, np.where(df.rsi < C.RSI_BEAR, -1, 0))
    comp["prevday"] = np.where(c > df.pd_high, 1, np.where(c < df.pd_low, -1, 0))
    for k in comp:
        df["c_" + k] = comp[k].astype(int)
    df["score"] = comp.sum(axis=1).astype(int)
    return df


def confirmed(scores, i, direction):
    """True if the last CONFIRM_BARS closes (ending at bar i) all meet the threshold."""
    k = C.CONFIRM_BARS
    if i < k - 1:
        return False
    w = scores[i - k + 1:i + 1]
    return bool((w >= C.SCORE_THRESHOLD).all()) if direction > 0 else bool((w <= -C.SCORE_THRESHOLD).all())


def faded(scores, i, direction):
    """Exit condition: score back to neutral or opposite on CONFIRM_BARS consecutive closes."""
    k = C.CONFIRM_BARS
    if i < k - 1:
        return False
    w = scores[i - k + 1:i + 1]
    return bool((w <= 0).all()) if direction > 0 else bool((w >= 0).all())
