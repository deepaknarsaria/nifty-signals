# NIFTY signal system: step 1 (backtest)

## Setup (once)
1. Install Python 3.10+, then: `pip install -r requirements.txt`
2. On smartapi.angelone.in: create an app (gives the API key) and use "Enable TOTP"
   (gives a TOTP secret, the long text code shown next to the QR).
3. Copy `.env.example` to `.env` and fill in the four values. Never share or commit `.env`.

## Run
```
python angel_data.py login                        # checks your credentials work
python angel_data.py download --symbol NIFTY --years 3
python backtest.py --symbol NIFTY
python chain_recorder.py --symbol NIFTY           # leave running 9:15 to 15:30 daily
```
No Angel One setup yet? `python make_sample_data.py && python backtest.py --data-dir sample_data`
runs the engine on fake data, only to confirm the code works.

## Live Telegram alerts (paper trading)
1. In Telegram open @BotFather, send `/newbot`, copy the bot token into `.env` as `TELEGRAM_BOT_TOKEN=...`
2. Open your new bot in Telegram and send it `hi`, then run `python telegram_setup.py` (or double-click `5_telegram_setup.bat`).
3. Each market day before 9:15: `python live_signals.py` (or double-click `6_live_signals.bat`). Leave the window open until 15:15.

It records the option chain too, so `chain_recorder.py` does not need to run separately.
Check the data paths any time with `python live_signals.py --selftest`.
Dry run on a past day: `python live_signals.py --replay 2026-10-01`.

## Files
| File | Purpose |
|---|---|
| `config.py` | Every setting: thresholds, stop/target, costs, lot sizes |
| `signals.py` | Scoring rules. Shared by backtest and the later live alerts |
| `backtest.py` | Simulates trades, writes `results/` report and trade log |
| `angel_data.py` | Angel One SmartAPI login and historical download |
| `chain_recorder.py` | Saves option chain OI snapshots for later OI backtests |
| `live_signals.py` | Live alerts on Telegram with real option prices, logs paper trades |
| `telegram_setup.py` | One-time Telegram connection |
| `research.py` | Compares rule families and option decay assumptions |

## Rules being tested
Score from 5 components, each +1 / -1 / 0: trend (EMA20 vs EMA50), session VWAP,
opening 15-min range break, RSI momentum, previous day high/low break.
- BUY CALL: score >= +3 on two consecutive 5-min closes. BUY PUT: score <= -3.
- Entry at next bar open, 09:30 to 14:30, ATM strike, nearest expiry, max 3 trades a day.
- Exit: stop 1.5 ATR on the index, target 2x the stop distance, score back to neutral
  on two closes, or 15:15 square-off.

## How to read the report
- Judge by OUT-OF-SAMPLE, not ALL. If in-sample is good and out-of-sample is bad, the rules are fitted to noise.
- Minimum bar before going further: profit factor above 1.2 out-of-sample, 200+ trades, both CALL and PUT sides positive.
- Do not tune `config.py` repeatedly against the same data. Each retune makes the result less trustworthy.

## Known limits
- Option premiums are modelled (Black-Scholes, India VIX as IV), not real traded prices.
  Real ATM IV differs from VIX and moves intraday, so rupee P&L is an estimate.
- No OI, PCR or volume in this backtest. The index has no volume and Angel One has no
  intraday history for expired options. `chain_recorder.py` builds that dataset going forward.
- Holidays and special sessions are ignored in the expiry calendar.
- Lot sizes, STT and charges in `config.py` must be checked against a current contract note.
