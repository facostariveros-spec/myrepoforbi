# Trading engine — behavior specification

This document defines behavior, not executable strategy or validated numerical thresholds. Capital authority belongs to [RISK](RISK.md); component contracts belong to [ARCHITECTURE](ARCHITECTURE.md).

## Scanner and candidate quality

Evaluate 15m, 1h, 4h and 1d together using validated data. Assess range persistence, mean reversion, volatility, trend context, liquidity/spread, estimated fees/slippage and executable grid spacing. Publish feature evidence, score contributions, eligibility, disqualifiers and uncertainty. Quality gates precede ranking and allocation; insufficient evidence means WAIT.

**ADA-type anti-FOMO:** a strong directional rise or fall, high momentum or a high scanner score alone must not qualify an asset for opening a grid. Require independent evidence of a tradable range and acceptable net economics. Idle funds do not relax these gates.

Numerical lookbacks, weights, cutoffs and conflict resolution across timeframes remain unresolved. Use closed candles for structural decisions unless a separately specified intrabar rule is validated; record candle boundaries and freshness.

### Deployment 2A scanner base

The scanner is a public-data pipeline with no Grid Score or trading decision:

`exchangeInfo → USDT Spot universe → batch 24h ticker/book prefilter → bounded per-symbol kline collection → normalized candles → base indicators → MarketSnapshot`

Universe filtering records explicit reasons for non-USDT, inactive, non-Spot, denylisted, identifiable leveraged/tokenized and malformed entries. Leveraged identification uses Binance metadata first and a conservative product-suffix heuristic; it is not a complete product taxonomy. The cheap prefilter records 24-hour quote volume/trades, bid/ask spread and top-of-book quote-value proxy. `limit` caps deep analysis in deterministic exchange metadata order; it is a resource control, not ranking or selection.

Each snapshot contains symbol assets, prefilter evidence and, for 15m/1h/4h/1d, the latest closed-candle price, ATR and percentage, RSI, ADX, unannualized realized log-return volatility, rolling high/low and midpoint-relative width, volume statistics, midpoint crossing count, candle count and quality state. Financial inputs and arithmetic use `Decimal`; API serialization uses finite decimal strings. Symbols missing required history on any timeframe are returned as data-quality rejections rather than partial candidates.

The following environment values are provisional calibration parameters, not approved strategy thresholds:

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `SCANNER_MIN_QUOTE_VOLUME` | 1,000,000 | Minimum 24h USDT quote volume |
| `SCANNER_MIN_TRADES` | 1,000 | Minimum reported 24h trades |
| `SCANNER_MAX_SPREAD_PCT` | 0.50 | Maximum midpoint-relative bid/ask spread percent |
| `SCANNER_MIN_BOOK_LIQUIDITY_QUOTE` | 1,000 | Minimum top-of-book quote-value proxy |
| `SCANNER_DENYLIST` | empty | Comma-separated symbols excluded locally |
| `SCANNER_KLINE_LIMIT` / `SCANNER_MIN_CANDLES` | 200 / 100 | Requested and required closed candles per timeframe |
| `SCANNER_INDICATOR_PERIOD` | 14 | ATR, RSI and ADX period |
| `SCANNER_VOLATILITY_PERIOD` | 30 | Realized volatility return count |
| `SCANNER_ROLLING_PERIOD` | 20 | Rolling range window |
| `SCANNER_VOLUME_PERIOD` | 20 | Volume-statistics window |
| `SCANNER_OSCILLATION_PERIOD` | 20 | Range-midpoint crossing window |
| `SCANNER_MAX_CONCURRENCY` | 4 | Maximum concurrent deep-analysis workers |
| `SCANNER_MAX_LIMIT` | 100 | Maximum API `limit` value |

Process-local TTL caches cover exchange metadata (300 seconds), batch ticker/book data (30 seconds) and klines (60 seconds); their TTLs have matching `SCANNER_*_TTL_SECONDS` settings. Cache loss on restart is acceptable. A cold scan uses three batch requests plus four kline requests per analyzed symbol, subject to safe GET retries. The fixed worker pool bounds kline concurrency. Existing 429/`Retry-After` and retry limits apply; diagnostics report actual attempts for completed calls, terminal rate limits, cache activity, error categories and rejection counts. Final calibration must use measured Binance weights, market coverage and data-quality evidence.

### Deployment 2B candidate intelligence

Candidate intelligence is a pure interpretation layer over completed MarketSnapshots. It does not fetch duplicate market data or authorize an action. `GET /scanner/candidates?limit=N` performs a scan, assesses every resulting snapshot, ranks non-rejected assessments, and applies `limit` to the ranked response. Scanner exclusions remain explicit rejected candidates.

Universe classification now uses Binance tokenized-asset flags, product/security/underlying metadata and tags when available. A configurable `SCANNER_TOKENIZED_EQUITY_ROOTS` fallback recognizes the observed Binance `B`-suffixed equity/ETF representation when metadata is incomplete. The fallback is deliberately limited to known equity roots so ordinary crypto assets ending in `B` or `UP`, such as ARB or JUP, are not excluded merely by their suffix. Metadata and fallback exclusions have distinct reasons.

The cheap liquidity prefilter remains an inexpensive universe gate. Candidate intelligence computes a separate `liquidity_quality` component from quote volume, trades, spread and the top-of-book proxy. It identifies its source as `top_of_book_proxy`, reports depth as `not_measured`, and caps the provisional score at 85 until shortlisted-symbol depth analysis exists. No per-universe depth calls are made.

Each timeframe is classified from multiple pieces of evidence into `RANGING`, `TRENDING_UP`, `TRENDING_DOWN`, `HIGH_VOLATILITY`, `LOW_VOLATILITY` or `TRANSITIONAL`. Directional regimes require ADX, RSI and price position together. Volatility regimes require at least two of ATR percentage, realized volatility and range width. Ranging requires at least three of low ADX, repeated crossings, non-extreme RSI and an interior price. The combined regime uses provisional weights of 15m 0.15, 1h 0.25, 4h 0.35 and 1d 0.25; conflicting short/long direction or range/trend evidence forces `TRANSITIONAL`. Results retain confidence, raw evidence and reasons.

Seven 0–100 components use “higher is safer/better for a grid”: range quality, oscillation quality, liquidity quality, volatility suitability, trend safety, breakout safety and data quality. The provisional Grid Score is:

`0.22 range + 0.18 oscillation + 0.15 liquidity + 0.12 volatility suitability + 0.15 trend safety + 0.13 breakout safety + 0.05 data quality`

Weights are normalized at runtime, configured with `GRID_WEIGHT_*`, and each contribution is returned. Initial state thresholds are 75 `STRONG_CANDIDATE`, 60 `CANDIDATE`, 45 `WATCH`, otherwise `REJECT`. Data quality below 70, liquidity below 25 or breakout risk at least 85 independently rejects. These values and all `REGIME_*`, `RISK_*`, `GRID_*`, `LIQUIDITY_*` and `RANGE_ATR_BUFFER` settings are calibration parameters, not optimized strategy claims.

Breakout risk combines range-edge proximity (25%), ADX (15%), ATR expansion (15%), realized-volatility expansion (10%), abnormal volume (10%), directional persistence (15%) and timeframe conflict (10%). FOMO risk combines extreme RSI (20%), ADX (20%), edge proximity (15%), maximum ATR/volatility expansion (15%), abnormal volume (15%) and maximum directional/conflict evidence (15%). Both are 0–100 where higher is riskier. High RSI contributes evidence but cannot reject by itself. This general mechanism covers rapid directional moves without asset-specific rules.

For `CANDIDATE` and `STRONG_CANDIDATE`, the analytical range uses rolling support/resistance proxies from 1h (20%), 4h (45%) and 1d (35%), expanded on each side by a configurable fraction of the similarly weighted ATR. It returns boundaries, width, current-price position, `LOWER_ZONE`/`MID_ZONE`/`UPPER_ZONE`/`OUTSIDE_RANGE`, confidence and reasons. It does not create grid levels or choose a grid count.

Ranking sorts by Grid Score, then range quality, oscillation, liquidity, lower breakout/FOMO risk, data quality and symbol for deterministic ties. Ranking is descriptive; it does not allocate capital or select a trade.

### Deployment 2C historical validation

Offline replay observes the unchanged Deployment 2A/2B features, regimes, risks, score and structural range at historical timestamps, then labels later market behavior in a separate outcome phase. It does not alter production parameters or expose an HTTP endpoint. The production timeframe-feature builder and candidate-intelligence implementation are reused directly so research cannot silently acquire a second score formula. Methodology, artifacts and limitations are authoritative in [CALIBRATION](CALIBRATION.md).

## Lifecycle and actions

Logical lifecycle: candidate → eligible/waiting → proposed OPEN → authorized execution → active → adapting or closing → closed/cooldown. OPEN is an entry operation; the required ongoing decisions are below. Rejections and incomplete operations remain explicit states, not successful transitions.

| Action | Meaning |
| --- | --- |
| KEEP | Current grid remains valid; maintain its plan, with each resulting order effect separately authorized |
| WAIT | Defer change or entry due to uncertainty, cooldown, stale inputs or risk constraints; identify what existing orders remain active |
| EXPAND | Widen the existing range when its structure remains valid; assess inventory, spacing and required capital |
| REGRID | Replace the grid around a supported new range; plan cancellations, inventory handling and replacement orders |
| CLOSE | End the strategy under confirmed invalidation, risk policy or human instruction; specify cancellation and inventory disposition |

**SUI-type range departure:** leaving the grid is evidence to assess, never an automatic CLOSE trigger. Distinguish:

- Temporary deviation: original range remains supported; KEEP or WAIT as justified.
- Range expansion: wider boundaries remain coherent; evaluate EXPAND.
- New range: sustained structure forms elsewhere; evaluate REGRID.
- Confirmed breakout: range thesis is invalidated; evaluate CLOSE or a risk-approved protective plan.

Use multi-timeframe evidence and confidence/persistence criteria to support classification. Risk emergencies and operator stops take precedence over normal classification. Record rejected alternatives and why the selected action is safer or better supported.

## Stability and adaptation

Use separate entry/exit criteria, persistence requirements and cooldowns to prevent OPEN/CLOSE oscillation and excessive EXPAND/REGRID activity. Track prior decisions and time since transitions. Emergency risk reduction and human stops bypass strategy cooldown delays, but still pass the risk gate. Exact durations and confirmation thresholds require testing.

Reconfiguration is a recoverable sequence, not an assumed atomic exchange action: reconcile fills, authorize the plan, cancel affected orders, reconcile late/partial fills, recalculate exposure and authorize replacements. Failed steps must leave visible recovery state; do not create overlapping grids or unreserved commitments.

## Auto-compounding — V1

Realized net profit accrues in a separate ledger reserve. [RISK](RISK.md) owns its eligibility and capital accounting. At configurable `COMPOUND_TRIGGER_PCT`, evaluate:

- `COMPOUND_REGRID`: incorporate eligible reserve into a newly assessed grid plan.
- `COMPOUND_INJECT`: add eligible reserve to the current plan only if its structure, sizing and economics remain valid.

The trigger requests evaluation; it does not compel reinvestment. Recheck market quality, all limits, HARD_CAP, exchange filters and marginal fees/slippage. Risk Manager must authorize the reserve transfer and each execution effect. Reserve funds stay reserved/unspent until a plan is accepted; unused amounts remain available after reconciliation. Failed or retried plans cannot compound the same profit twice.

Record trigger inputs, proposed method, eligible amount, costs, authorization, actual deployment and remaining reserve. Hold below-minimum amounts until executable without weakening quality gates. No unrealized gains, deposits, borrowed funds or hypothetical Shadow profits are compoundable.

## Unresolved before strategy implementation

- Range detection, breakout confirmation, timeframe arbitration and quality thresholds.
- Grid spacing/count/sizing, inventory acquisition/disposal and order types.
- Hysteresis and cooldown settings; exact behavior of resting orders during WAIT.
- `COMPOUND_TRIGGER_PCT` value **and denominator/baseline**, evaluation cadence, minimum amount and method selection. No formula is approved until its denominator is defined.
- Close/unwind policy, including whether confirmed breakout calls for immediate or staged disposal.
