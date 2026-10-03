# Historical calibration and validation

## Purpose and separation

Deployment 2C measures whether the current production scanner and candidate-intelligence outputs are associated with later grid-friendly market behavior. It is an offline research system using public Binance candles. It neither trades nor simulates a Binance grid.

Keep three concerns separate:

1. **Observation** produces historical features at T and records later outcomes.
2. **Calibration** summarizes associations, failures and uncertainty.
3. **Optimization** would propose parameter changes and is not implemented or authorized.

Reports may identify evidence, but they never update environment variables, weights, thresholds, regimes or range parameters. Any future change must be proposed separately, tested on chronological training data, selected with validation data, and accepted only after untouched test data confirms it.

## Replay and anti-lookahead design

For each symbol and evaluation timestamp T, the replay filters every timeframe to candles whose `close_time <= T`, then takes the same bounded history window used by production. It calls the production timeframe-feature builder and `GridCandidateIntelligence`; there is no research copy of the indicators or Grid Score. The 15m, 1h, 4h and 1d clocks align independently at their last fully closed candle.

Feature calculation and forward labeling are separate calls. Forward outcomes use 15-minute candles with `open_time >= T` and `close_time <= T + horizon`; a candle opening at the boundary contains no information available at T. Future regime is an outcome label calculated only after the assessment at T is complete. Tests mutate post-T prices without changing the assessment at T.

Historical Binance candles contain volume and trade count but not historical spread or order-book depth. Replay derives trailing 24-hour quote volume and trades from closed 15-minute candles, then uses explicit research assumptions for spread and top-of-book liquidity. These assumptions are recorded in the manifest and affect liquidity quality and Grid Score. They are a material limitation.

## Forward outcomes

Initial horizons are 6h, 12h, 24h, 48h, 72h and 7d.

- **Range durability:** percentage/count of closes inside the proposed range, first raw upper/lower close violation, time to first violation, return inside, maximum continuous outside duration, and raw wick violation counts.
- **Confirmed breakout:** upper, lower or none. Confirmation defaults to two consecutive closes outside or 30 minutes continuously outside. Raw candle observations and the rule are retained. An isolated wick does not confirm a breakout.
- **Mean reversion:** midpoint crossings, outer-zone episodes returning to the middle third, distance recovered toward midpoint and average latency.
- **Opportunity proxy:** absolute close-to-close travel, unannualized realized volatility, configurable movement counts, direction alternation, midpoint oscillations and movements exceeding a configurable round-trip fee-plus-margin assumption. This is market behavior, not a fill model or profitability estimate.
- **Excursion:** signed maximum adverse and favorable excursion from the price at T, plus positive magnitudes beyond the proposed upper/lower bounds.

## Validation statistics

Breakout and FOMO risks use 0–10 through 90–100 buckets. Reports include sample count, confirmed/upper/lower breakout rates, median time to breakout and survival rate. Observed buckets are not forced to be monotonic.

Grid Score uses `<45`, `45-60`, `60-75` and `75+` buckets. Reports include durability, breakout, crossings, oscillation, forward volatility, opportunity proxy and excursions. Each score component also receives decile outcome summaries, sample/missing counts and Spearman rank correlations with durability, breakout and opportunity outcomes. Spearman correlation measures rank association; it does not establish causality, executable profit or stability in another period.

Regime reports group outcomes by initial combined regime and record initial-to-future regime counts. Zone reports group by `LOWER_ZONE`, `MID_ZONE`, `UPPER_ZONE` and `OUTSIDE_RANGE`. These are validation inputs for a possible future Entry Gate; no Entry Gate exists here.

## Chronological and walk-forward evaluation

TRAIN, VALIDATION and TEST splits use unique evaluation timestamps in chronological order without shuffling, so all symbols and horizons at a timestamp stay in the same temporal partition. Defaults are 60/20/20. Optional walk-forward mode creates forward-moving windows, reports each fold’s partitions, and aggregates fold results. The TEST partition should remain untouched while research choices are made.

Repeated observations overlap when evaluation steps are shorter than forward horizons. Correlation confidence intervals, dependence-aware uncertainty and multiple-testing controls are not implemented; interpret sample sizes accordingly.

## Historical cache and reproducibility

Public klines are stored as deterministic JSON pages under `.calibration_cache/<symbol>/<interval>/<start>-<end>.json`. Each page covers at most 1,000 interval slots and is written atomically after validation. A failed run reuses completed pages. `calibration_results/` and `.calibration_cache/` are ignored by Git.

The output directory uses a deterministic hash of symbols, dates, code version and all scanner, intelligence and research configuration. Each manifest records run time, version, symbols, range, timeframes, horizons, unchanged production weights/thresholds, range configuration, fee and liquidity assumptions, split, counts, download diagnostics and errors. With identical cached data, code and configuration, calculations and serialized tables are deterministic; the recorded execution timestamp is expected to differ between distinct executions.

Artifacts:

```text
calibration_results/<run_id>/
├── manifest.json
├── observations.csv
├── score_buckets.csv
├── breakout_risk_buckets.csv
├── fomo_risk_buckets.csv
├── component_validation.csv
├── regime_validation.csv
├── zone_validation.csv
├── walk_forward.csv
├── summary.json
└── summary.txt
```

## CLI

```bash
python -m adaptive_grid_manager.calibration \
  --symbols BTCUSDT ETHUSDT BNBUSDT SUIUSDT \
  --start 2025-01-01 \
  --end 2026-01-01 \
  --output calibration_results \
  --cache-dir .calibration_cache
```

Custom horizons, daily evaluation and chronological ratios:

```bash
python -m adaptive_grid_manager.calibration \
  --symbols BTCUSDT ETHUSDT BNBUSDT SOLUSDT SUIUSDT \
  --start 2024-01-01 --end 2026-01-01 \
  --horizons 6h 12h 24h 48h 72h 7d \
  --evaluation-step 24h \
  --train-ratio 0.60 --validation-ratio 0.20 --test-ratio 0.20 \
  --movement-thresholds-pct 0.25 0.50 1.00 \
  --fee-rate-pct 0.10 --minimum-fee-adjusted-move-pct 0.10 \
  --walk-forward --walk-forward-folds 4
```

The CLI requires no Binance credentials and is not connected to the Cloud Run HTTP service or order adapter.

## Limitations

- Explicit symbols do not reconstruct the point-in-time Binance universe. Delisted assets and assets unavailable today can be missing, creating survivorship and selection bias. Use diverse assets and preserve failed/missing histories; do not choose symbols from future performance.
- Binance public kline availability begins at listing and can contain gaps or later corrections. The current cache validates row structure and ordering but does not reconstruct exchange outages.
- Historical spread, depth, latency, slippage, queue position, partial fills and filter changes are unavailable from klines. Fee-adjusted opportunity count is an assumption-driven proxy.
- Overlapping horizons make observations statistically dependent. Spearman values have no confidence intervals or significance correction in this deployment.
- A structurally durable range does not prove a realizable or profitable grid. Results exclude execution mechanics, inventory risk, capital constraints and taxes.
- Current parameters remain provisional until diverse, point-in-time data produces stable validation and untouched out-of-sample evidence.
