# Experiment protocol

This file states the evaluation design before the full benchmark is run. Small hand-built tapes and short generated paths are used to debug accounting, not to select a profitable parameter set. All four strategies remain in the report, including unhelpful extensions.

## Primary question

Does a strategy ranking survive changes in execution assumptions? A positive simulated P&L under one fill model is insufficient evidence of a usable strategy.

## Fixed design

- Four policies: passive BBO, fixed-volatility Avellaneda–Stoikov, causal EWMA volatility, and EWMA plus a fixed book-imbalance skew.
- 256 independent synthetic paths; each is 600 seconds, with its first 150 seconds reserved for calibration. Seed sequence starts at 12000. No training observations enter evaluated P&L.
- Every policy receives the same test tape, fee schedule, risk cap, and queue assumptions. No policy tuning on test P&L.
- Risk aversion follows an explicit training-only budget: the reservation-price displacement at the inventory limit equals three training half-spreads over the capped risk horizon.
- Primary outcomes: terminal cash after forced liquidation, inventory RMS, maximum inventory, maker turnover, fill counts, and drawdown.
- Paired path bootstrap with 3,000 resamples and percentile 95% intervals for policy-minus-passive P&L and inventory RMS. These describe uncertainty under this generator; they are not market confidence intervals.
- Factorial robustness grid: queue front/back; entry/cancel latency 0, 50, 250 ms; maker fee 0, 1, 2 bps; adverse-selection strength 0 and 0.5 ticks per signed trade. 32 independent seeds per cell. Common seeds and tapes pair the strategies and execution assumptions. Multiple exploratory comparisons are not interpreted as confirmatory p-values.
- Fee inputs are scenario assumptions, not a claim about an exchange's current account-specific schedule.
- Real-data case study: publicly downloadable ETHUSDT futures quotes and aggregate trades from 2024-01-01, first 7,200 seconds UTC. First 1,800 seconds calibrate; the following six nonoverlapping 900-second blocks evaluate. An optional second day repeats the design without adjusting policy definitions. Within-day blocks are dependent: empirical tables are descriptive and do not use fill-level or block-level IID confidence intervals.
- Real prices and quantities are converted to tick and lot units. Every figure identifies synthetic or historical data explicitly. No annualized Sharpe is computed from a short tape.

## Interpreting failures

A wide interval, a strategy-ranking reversal, an unprofitable strategy, and poor calibration fit are reportable outcomes. The study does not redefine its winner or omit a difficult queue setting after seeing results. The imbalance extension is deliberately simple and may fail.

## Known non-identification

Top-of-book quantities and prints do not reveal the order-level queue. Front/back scenarios are alternative models, not guaranteed upper/lower bounds on economic P&L. Trade-flow counts at nested distances are dependent; the calibration likelihood is composite, so its optimizer Hessian is not used for ordinary Poisson inference.

## Scope amendment recorded before the expanded empirical run

The original ETHUSDT January 1 pilot was expanded to ETHUSDT and BTCUSDT on January 1 and 2 (00:00–02:00 UTC), and January 3 (11:00–13:00 UTC), 2024. The first 30 minutes of each selected window remain training only. August 5 was initially proposed as a stress day, but its public bookTicker archive returned HTTP 404. January 3 was chosen for its independently documented crash, not observed strategy profitability; see [the market-price-discovery study](https://econ.washington.edu/sites/econ/files/documents/research/Misguided_Price_Discovery.pdf) and [Bitfinex Alpha 86](https://blog.bitfinex.com/wp-content/uploads/2024/01/Bitfinex-Alpha-86.pdf). All six asset-date samples are kept.

An exploratory post-core-run analysis maps economic preferences by subtracting a squared-inventory penalty from mean P&L. This does not tune a new policy or replace the primary P&L ranking. Fee break-even levels are conditional algebraic calculations with fixed fills.

## Interpretation clarified during independent review

The synthetic generator displays each interval's depth imbalance after that interval's trade directions have been drawn, and redraws its latent imbalance independently next interval. Thus the observable imbalance feature is lagged and nonpredictive of subsequent order flow. The existing DGP and all outcomes were retained; the imbalance policy is interpreted as a negative-control-style feature check, not evidence against genuinely predictive microprice signals. No new predictive signal was inserted after observing poor performance.
