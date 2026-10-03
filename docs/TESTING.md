# Testing — evidence and release gates

There are no executable tests at foundation stage. This document specifies future verification; it does not claim any trading behavior has passed. Release order and human approvals are defined in [REQUIREMENTS](REQUIREMENTS.md#deployment-progression).

## Strategy

Start with deterministic fixtures and a controllable clock for pure decisions, risk and accounting. Add adapter contract tests, replay/scenario tests, recovery/fault injection, and limited integration checks as their stages arrive. Use exact accounting assertions and representative market paths; do not validate safety only with mocked happy paths.

Use public/read-only connectivity before any trading-capable credentials. Validate execution contracts in simulation or suitable exchange test facilities after verifying their current capabilities. No test may place real Live orders by default.

## Required evidence

| Area | Scenarios and assertions |
| --- | --- |
| Scope/security | Reject non-USDT quotes and non-Spot capabilities; no withdrawal paths; frontend/log/database/repository secret checks |
| Scanner | All four timeframes, missing/stale candles, spread/liquidity/fee rejection, reproducible rankings and reasons |
| ADA-type anti-FOMO | Strong directional trend without range evidence fails grid eligibility despite idle capital |
| SUI-type departure | Separate temporary deviation, expansion, new range and confirmed breakout fixtures; crossing a bound alone cannot trigger CLOSE |
| Adaptation | KEEP/WAIT/EXPAND/REGRID/CLOSE explanations; partial cancellation, late fills and restart during regrid |
| Stability | Repeated boundary noise cannot cause rapid OPEN/CLOSE churn; human/emergency stop is not delayed by cooldown |
| Risk | Every trading/capital path requires authorization, including stop effects and compounding; stale grants rejected; concurrent allocations cannot overspend |
| Capital | Fees, partial fills, rounding, minimum notionals, losses and external changes; no duplicate funds or HARD_CAP bypass |
| Compounding | Unrealized gains excluded, net losses/fees accounted for, trigger boundary and defined denominator, both methods, no duplicate reserve consumption after retry |
| Mode parity | Identical inputs/state/configuration yield identical decisions; Paper/Shadow never dispatch real orders; hypothetical ledger isolated |
| Recovery | Timeout with unknown outcome, duplicate/out-of-order events, store failure, rate limits, disconnect, restart and backup restore reconcile before new risk |
| Operator UI | Individual/global stop precedence, persistence and acknowledgement; requested versus completed state; stale charts and PNL separation |
| Operations | Telegram/email delivery/retry/failure; secret redaction; Cost Guard lag/anomaly and safe degradation with open orders |

Replay reports should include capital use, net realized/unrealized PNL, fees, drawdown, churn, exposure, rejected actions and assumptions about fill/slippage behavior. Profitable simulation alone is insufficient for release.

## Gate records

For each release stage, retain software/configuration version, dataset/fixture identifiers, assumptions, checks run, outcomes, known limitations and reviewer approval where required. Define pass thresholds before assessing results. No production reliability claim follows from documentation checks alone.

Shadow exit requires observed parity explanations, reconciliation stability and zero real effects. Live-disabled exit requires a proven dispatch lock and verified activation/stop controls. $50 and $100 trials require explicit operator approval, validated capital limits and reviewed operational evidence before any increase.

## Unresolved

Test framework, market datasets, fill/slippage model, exchange test environment, performance targets, observation windows, pass thresholds and recovery objectives. Set them at the relevant implementation stage; numerical gates required for Live cannot remain unset at activation.

## Deployment 1B gate evidence

The Spot test-order gate must prove default disablement, credentials gating, BUY/SELL LIMIT validation, USDT and Spot restrictions, PRICE_FILTER, LOT_SIZE and notional checks, sanitized Binance rejection handling, timeout/429/5xx behavior, exactly-once POST dispatch, and the absence of any `/api/v3/order` call. All connectivity tests remain part of this gate. Passing mocked tests does not establish that credentials, permissions, regional access or Binance behavior work from Cloud Run.

## Deployment 2A gate evidence

The scanner base uses mocked public Binance responses to verify universe reasons, provisional prefilter gates, normalized exact-decimal candles, insufficient/malformed history, pure indicator behavior, cache hit/expiry, bounded concurrency, GET retry/rate-limit behavior, partial failure isolation, credential independence, API limits and finite serialization. Connectivity and test-order suites remain mandatory. This evidence validates data plumbing only; it does not validate candidate selection, Grid Score, profitability or final calibration.

## Deployment 2B gate evidence

Deterministic synthetic snapshots cover ranging, upward/downward trends, generic rapid breakouts, false-breakout-like moves, high/low volatility, timeframe conflicts, overbought ranges, liquidity and data hard failures, score/risk bounds, stable ranking, range/zone calculations and finite serialization. Universe fixtures cover metadata and fallback tokenized-equity exclusions plus ordinary crypto suffix controls. Passing these tests proves formula behavior for the fixtures; it does not validate weights, thresholds, live execution quality, candidate profitability or range durability.

## Deployment 2C gate evidence

Offline calibration tests prove closed-candle cutoff, multi-timeframe alignment, forward-only horizon slicing, confirmed upper/lower breakouts, rejection of isolated wick/close violations, range durability, midpoint reversion, excursions, bucket boundaries, rank correlation, chronological splits, rolling folds, Decimal preservation, resumable cache files and deterministic artifact serialization. The mandatory anti-lookahead fixture changes every candle after T to extreme values and requires the assessment at T to remain identical. Existing connectivity, test-order, scanner and candidate-intelligence suites remain mandatory.
