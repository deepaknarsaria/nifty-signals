# What has been tested, and what worked

A running record, so nothing has to be re-tested from scratch. Update it after every review.
"Edge" means the rule picked direction better than chance on data it was not tuned on.
Rupee figures use modelled option premiums calibrated to real September 2026 prices, 1 lot (65).

## Tested on 3 years of NIFTY 5-minute data (Oct 2023 to Oct 2026)

| Rule | Result | Status |
|---|---|---|
| Late-day momentum: at 14:00, if NIFTY is 0.3%+ from yesterday's close, trade that direction, exit 15:10 | +7 to +9 index points per trade in both periods; about break-even to slightly positive in rupees | Live (rule A). Only rule with a tested edge, and it is small |
| Chart score: EMA trend + session VWAP + opening range + RSI + previous day levels, 2 closes | +2 to +4 points per trade, loses after costs | Live only with OI and breadth filters (rule B, experimental) |
| Fading the chart score, fading stretched moves, late-day fade | Negative | Dropped |
| Opening range breakout, hold to close | Positive first 2 years, negative last 11 months | Dropped |
| First-hour and midday momentum | No reliable edge | Dropped |
| Gap follow (0.3%+ gap, hold to close) | Positive but not significant | Watch list |
| Hedged selling: iron fly, iron condor, 14:00 credit spread | -1 to -4 premium points per trade after costs | Dropped for now; re-test on recorded real prices |

## Exits

| Exit | Result |
|---|---|
| Quick small targets (0.5R, 1R, +25% premium) | Higher win rate, worse overall |
| Book everything at +40% premium (rule B) | About equal to holding for 2R. Reached on roughly 1 trade in 4 |
| At +40% premium, lock +25% and trail 15% below the peak (rule B) | Better than booking at +40% in both periods (Rs -105 vs -165, and -72 vs -164 per trade). Live since 7 Oct 2026 |
| Move stop to entry once +1R in profit | Neutral for rule B, equal or slightly better for rule A. Live since 7 Oct 2026 |
| Trailing stops on rule B | Inconsistent between periods |
| 45-minute time stop | No improvement |

## BANKNIFTY
Same 16 rules tested. Nothing significant. Gap follow was the best (+39 and +50 points in the two periods, borderline).

## Strategy lab (real prices, from 5 Oct 2026)
`strategy_lab.py` paper-trades 34 ready-made option structures every market day on the recorded option chain
(open 9:20, close 15:10, real bid and ask, charges deducted). Results accumulate in `lab_results.csv`.
Do not read anything into it before about 15 days.

## Cannot be backtested yet
Option chain (PCR, OI bias, OI walls) and heavyweight stock breadth have no history. Evidence for them
comes only from `paper_trades.csv` and the `chain/` snapshots this system records each market day.

## Live paper trades
See `paper_trades.csv`. First two days (5 and 6 Oct 2026): 5 trades, net Rs -2,025 per lot.
Three of the five were 40%+ in profit at some point, which is what led to the +40% booking rule.

## How to re-run
`python research.py` (rule families), `python sell_research.py` (selling), `python backtest.py` (base rules).

## Historical run of the 34 strategy-builder structures (6 Oct 2026)

`hist_lab.py`: 737 days (Oct 2023 to Oct 2026), nearest weekly expiry, open 9:20, close 15:10, one lot,
0.5 point slippage per leg per side, all charges. Premiums are MODELLED (no real history exists for expired contracts).

- No structure made money regularly. Not one hedged structure was profitable over the 3 years.
- Hedged neutral selling: Short Iron Condor avg Rs -405 a day (44% winning days), Iron Butterfly Rs -353 (28%).
- Expiry day only: Iron Condor wins 73% of days but still averages Rs -536; Iron Butterfly is closest to break-even (59%, Rs -44).
- Unhedged selling wins often but loses overall: Short Strangle 70% winning days, Rs -243 a day, worst day Rs -39,321;
  Short Straddle 67%, Rs -165, worst day Rs -40,573.
- The only positive rows (Short Synthetic Future, Sell Call, Buy Put) are direction bets, up in only 2 of 4 calendar years.
- Reason: a hedged structure collects 10 to 20 points of decay in a day and pays about 7 points in spread and charges
  across 4 legs; one trending day removes many small wins.

`hist_check.py`: model against real option candles, 13 days of September 2026, same structures.
Correlation 0.98. The model was about Rs 200 a day too harsh on Iron Condor and Iron Butterfly; they were still negative on real prices.
The 5 and 6 October lab wins (Iron Condor +619, +1,090) were quiet days close to expiry, the best case for this structure.
