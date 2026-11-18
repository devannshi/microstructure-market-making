# Data dictionary and provenance

## Offline simulation

`microstructure_mm.synthetic.generate(seed, config)` creates original event tapes. Seed, generator configuration, calibration split, and execution configuration are recorded in the run manifest. No network access is needed for synthetic experiments or tests.

## Public historical case study

The historical experiment uses ETHUSDT and BTCUSDT USD-margined futures, with 00:00–02:00 UTC on January 1 and 2, 2024, plus 11:00–13:00 UTC on January 3, 2024, predeclared for evaluation. The January 3 window extends the study into a crash period. All six asset-date samples are retained, regardless of performance.

Each source has the form:

```text
https://data.binance.vision/data/futures/um/daily/bookTicker/{SYMBOL}/{SYMBOL}-bookTicker-{DATE}.zip
https://data.binance.vision/data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{DATE}.zip
```

The pipeline obtains each provider `.CHECKSUM`, streams the complete ZIP without storing it, validates decompression CRC and the complete archive SHA-256, and caches only the requested normalized time window. It scans all timestamps, rather than stopping at the first observation after the cutoff, and records timestamp regressions. This prevents an out-of-order message from being silently excluded. Small compact caches are local and Git-ignored. Their own SHA-256 is verified on reuse.

[The saved provenance](../results/data_provenance.json) lists exact URLs, provider digests, archive sizes, parsed row counts, selected event counts, removed invalid quotes, timestamp policy, and cache digests. The complete raw archives are not redistributed with this repository. A network rerun may take substantially longer than the offline Monte Carlo study, especially on the stress date.

### Source normalization

| Source field | Meaning and use |
|---|---|
| `transaction_time` | Book update exchange timestamp, milliseconds since epoch |
| `transact_time` | Aggregate-trade exchange timestamp, milliseconds since epoch |
| `best_bid_price`, `best_ask_price` | Divided by configured tick size; observed sub-tick prices preserved |
| `best_bid_qty`, `best_ask_qty` | Divided by configured lot size |
| `price`, `quantity` | Trade price and size, normalized to ticks/lots |
| `is_buyer_maker=true` | Aggressive sell, `side=-1` |
| `is_buyer_maker=false` | Aggressive buy, `side=+1` |

`event_time` is not treated as a complete local receipt-time feed. The two archives do not supply a universally ordered event sequence. Trades precede quotes on equal transaction timestamps; order eligibility uses strict timestamp inequality. Invalid crossed/nonpositive quotes are counted and removed. Aggregate trade rows are one recorded volume event, not fabricated constituent prints.

## Neutral CSV input

`read_tape(path, tick_size, lot_size)` supports user-supplied normalized event data, including licensed equity feeds converted by their owner. The CSV has these columns:

| Column | Type | Units / definition |
|---|---|---|
| `time` | float | Nonnegative seconds relative to a session origin |
| `kind` | string | `quote` or `trade` |
| `bid`, `ask` | float | Tick prices; zero for a trade |
| `bid_size`, `ask_size` | float | Displayed lots; zero for a trade |
| `price`, `size` | float | Trade ticks and lots; zero for a quote |
| `side` | integer | Aggressive buy `+1`, aggressive sell `-1`; zero for a quote |

The tape must begin with a valid uncrossed quote and be time sorted. Tied rows retain supplied ordering. `Tape.window(start,end)` uses a half-open interval and inserts only the most recent *past* quote as the initial book. It never initializes from a quote after the requested window starts. The requested end is retained as a scheduled session boundary; a quiet final gap does not shorten the risk horizon.

## Result files

| Artifact | Row unit |
|---|---|
| `synthetic_trials.csv` | One held-out path × policy |
| `calibration_trials.csv` | One training path and fit diagnostics |
| `paired_comparisons.csv` | One policy-minus-baseline metric with path-bootstrap interval |
| `stress_trials.csv` | One path × policy × execution scenario |
| `rank_stability.csv` | Mean rank in one scenario; not a significance test |
| `historical_blocks.csv` | One recorded time block × policy × execution scenario |
| `historical_break_even.csv` | One asset-date × queue × latency × policy; fee threshold conditional on fixed fills |
| `*_manifest.json` | Environment, source hash, settings, run counts and measured runtime |
| `example_trace.csv`, `example_fills.csv` | Original synthetic demonstration path for audit |

## Licensing

Original code, documentation, and synthetic outputs are MIT licensed. The original synthetic generator creates no derivative exchange data.

Binance Vision's terms retrieved for this study license its dataset under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), with [additional provider terms](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md). Historical-data-derived tables, traces, calibration statistics, figures, and the historical sections of the research report are distributed under that same CC BY-NC-SA 4.0 license and attributed to Binance Vision. The top-level MIT license does not relicense these data-derived artifacts. No endorsement by the data provider is implied.

## Observed sub-tick prices

A small set of BTC crash-window observations has prices inside the nominal 0.10 USDT order-price grid (for example, 44582.20 bid and 44582.24 ask). These may reflect legacy resting prices or provider/exchange details that the tape alone cannot identify. The loader preserves them as fractional tick values rather than rounding them into a false locked book or discarding them. New strategy quotes are still rounded outward to the configured order-price grid. Provenance reports `off_grid_quotes_preserved`; a regression test verifies the distinction. Normalized cache schema 2 invalidates the initial rounding-based prototype.
