"""All tunable settings in one place. Change values here, not in the engine."""
from datetime import time, date

# ---- Instruments -----------------------------------------------------------
# lot sizes change by NSE circular: VERIFY before trusting rupee P&L.
# The live recorder saves the real lot size from Angel One's instrument list.
INSTRUMENTS = {
    "NIFTY":     dict(angel_token="99926000", step=50,  lot=65, expiry="weekly"),
    "BANKNIFTY": dict(angel_token="99926009", step=100, lot=30, expiry="monthly"),
}
VIX_ANGEL_TOKEN = "99926017"   # INDIA VIX (verify: download prints a warning if it returns nothing)

# Expiry weekday: Thursday (3) until Aug 2025, Tuesday (1) from 1 Sep 2025.
EXPIRY_SWITCH_DATE = date(2025, 9, 1)
EXPIRY_WD_OLD, EXPIRY_WD_NEW = 3, 1

# ---- Signal engine ---------------------------------------------------------
EMA_FAST = 20
EMA_SLOW = 50
RSI_LEN = 14
RSI_BULL = 55
RSI_BEAR = 45
ATR_LEN = 14
OPENING_RANGE_BARS = 3      # first 15 minutes on a 5-min chart
SCORE_THRESHOLD = 3         # out of 5 components
CONFIRM_BARS = 2            # two consecutive closes rule

# ---- Trade management ------------------------------------------------------
ENTRY_START = time(9, 30)
ENTRY_END = time(14, 30)
SQUARE_OFF = time(15, 10)   # index stops updating at 15:15 since the Closing Auction Session (3 Aug 2026)
STOP_ATR = 1.5              # stop distance on the index, in ATRs
TARGET_R = 2.0              # target = TARGET_R x stop distance
MAX_TRADES_PER_DAY = 3
COOLDOWN_BARS = 3           # bars to wait after an exit
LOTS = 1

# ---- Option pricing model (backtest only) ----------------------------------
RISK_FREE = 0.065
FALLBACK_VIX = 13.0         # used only if VIX data is missing
SLIPPAGE_PTS = 0.75         # premium points lost per side (spread + impact)

# ---- Costs (per order unless stated). VERIFY against a real contract note. --
BROKERAGE = 20.0            # Rs per executed order
STT_SELL = 0.0015           # on sell-side premium
EXCH_TXN = 0.0003503        # on premium, both sides
SEBI_FEE = 0.000001         # on premium, both sides
STAMP_BUY = 0.00003         # on buy-side premium
GST = 0.18                  # on brokerage + exchange + SEBI

# ---- Backtest --------------------------------------------------------------
IN_SAMPLE_FRACTION = 0.70   # first 70% of days = in-sample, last 30% = out-of-sample

# ---- Live alerts -----------------------------------------------------------
# Session notes (NSE rule changes):
#  * Closing Auction Session from 3 Aug 2026: F&O stocks stop continuous trading at 15:15,
#    auction 15:15-15:30, so NIFTY spot is frozen in that window and jumps to the auction
#    close. Derivatives trade until 15:40. All positions are closed by 15:10.
#  * Revised pre-open from 7 Sep 2026: 9:00-9:05 market+limit orders, 9:05-9:10 limit only,
#    random close 9:08-9:10. Options are not part of pre-open. No entries before 9:30.
LATE_TIME = time(14, 0)        # rule A: late-day momentum decision time
LATE_MIN_MOVE = 0.003          # rule A: minimum move from previous close (0.3%)
LATE_STOP_ATR = 3.0            # rule A: stop distance in 5-min ATRs
TREND_ENTRY_END = time(13, 45) # rule B: last entry time
BREADTH_BULL = 0.60            # rule B: weighted share of heavyweights above their VWAP
BREADTH_BEAR = 0.40
OI_BIAS_MIN = 0.01             # rule B: (put OI change - call OI change) / total OI
# Approximate NIFTY weights (%), top names. Used only for breadth; refresh occasionally.
HEAVYWEIGHTS = {"HDFCBANK": 13.0, "ICICIBANK": 9.0, "RELIANCE": 8.5, "INFY": 5.0, "BHARTIARTL": 4.7,
                "LT": 4.0, "ITC": 3.5, "TCS": 3.0, "SBIN": 3.0, "AXISBANK": 3.0, "KOTAKBANK": 2.7, "M&M": 2.6}
