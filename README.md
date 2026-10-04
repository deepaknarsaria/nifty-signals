# NIFTY signal system: step 1 (backtest)

## Setup (once)
1. Install Python 3.10+, then: `pip install kiteconnect pandas numpy`
2. Create a Kite Connect app at developers.kite.trade (paid Connect plan, needed for market data).
   Set the redirect URL to `http://127.0.0.1`.
3. Create a file named `.env` in this folder:
   ```
   KITE_API_KEY=your_key
   KITE_API_SECRET=your_secret
   ```

## Run
```
python kite_data.py login                         # every morning, token expires daily
python kite_data.py download --symbol NIFTY --years 3
python backtest.py --symbol NIFTY
python chain_recorder.py --symbol NIFTY           # leave running 9:15 to 15:30 daily
```
No Kite account yet? `python make_sample_data.py && python backtest.py --data-dir sample_data`
runs the engine on fake data, only to confirm the code works.

## Files
| File | Purpose |
|---|---|
| `config.py` | Every setting: thresholds, stop/target, costs, lot sizes |
| `signals.py` | Scoring rules. Shared by backtest and the later live alerts |
| `backtest.py` | Simulates trades, writes `results/` report and trade log |
| `kite_data.py` | Kite login and historical download |
| `chain_recorder.py` | Saves option chain OI snapshots for later OI backtests |

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
- No OI, PCR or volume in this backtest. The index has no volume and Kite has no
  intraday history for expired options. `chain_recorder.py` builds that dataset going forward.
- Holidays and special sessions are ignored in the expiry calendar.
- Lot sizes, STT and charges in `config.py` must be checked against a current contract note.
