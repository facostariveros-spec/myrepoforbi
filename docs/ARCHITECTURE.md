# Architecture — boundaries and contracts

## Minimal structure

Use a modular monolith: one backend process with clear internal interfaces, one durable transactional store, and a private dashboard communicating through an authenticated backend API. Avoid microservices, distributed queues and a separate analytics platform until measured needs justify them. Logical modules may share a process; safety gates must remain explicit.

| Module | Responsibility |
| --- | --- |
| Market data / Binance adapter | Normalize candles, prices, filters, timestamps and account/order events |
| Scanner and intelligence | Produce multi-timeframe features, eligibility and ranked explanations |
| Decision engine | Propose lifecycle and compounding actions using [TRADING_ENGINE](TRADING_ENGINE.md) |
| Capital ledger | Track ownership, fills, fees, reservations and profit reserves under [RISK](RISK.md) |
| Risk Manager | Authorize, reject or defer every proposed trading/capital effect |
| Execution adapter | Apply authorized intents to simulation or Binance; enforce mode and idempotency |
| Reconciler | Compare durable intents, orders, fills and balances; resolve unknown outcomes |
| Operator API | Authenticate controls, persist stop/configuration changes and expose sanitized views |
| Operations | Deliver notifications, monitor health and enforce Cost Guard policy |

## Decision and execution flow

Validated data + reconciled state + versioned configuration → scanner → decision proposal → Risk Manager → durable authorized intent and capital reservation → execution adapter → observed results → reconciliation and ledger → dashboard/notifications.

Operator stop state is checked before authorization and again before dispatch. Any state/configuration change that invalidates authorization requires reevaluation. An authorization binds the exact action, amount, symbol, state version and expiry; it is not a reusable blanket permission.

Persist decisions even when the action is WAIT/KEEP or risk denies a proposal. Atomically persist intent/reservation before external effects. Use stable client order identifiers and reconciliation after timeouts; never assume an unanswered request failed and blindly submit a replacement. Only one active writer/executor may control an account allocation. A restart must reconcile before allowing new risk.

## Modes

| Mode | Inputs | Effects |
| --- | --- | --- |
| Paper | Recorded or streaming market data; simulated portfolio | Simulated orders, fills, fees and ledger |
| Shadow | Live market and read-only account observations | Separate hypothetical portfolio/execution; no real trading effects |
| Live | Live market and reconciled account state | Explicitly enabled Binance Spot effects after authorization |

All modes call the same scanner, decision and risk interfaces. Feed clock and configuration explicitly for replayability. Simulation assumptions are recorded. Different fills and portfolio states can legitimately lead to different decisions; identical input/state/configuration must produce identical proposals. Shadow must never treat hypothetical profit as real capital. Keep mode-specific ledgers and identifiers separate.

The Live adapter remains disabled by default, including after restart unless a deliberately designed activation policy permits continuation; that policy must be settled before Live. No automatic Paper/Shadow-to-Live transition.

## Durable records

Persist versioned configuration, operator events, bot/range lifecycle, input/feature snapshot references, proposals, reason codes, authorization outcomes, intents, orders, fills, fees, reservations, ledger entries, profit reserves, health events and notification delivery state. Audit entries need timestamps, mode, correlation IDs, software/configuration versions and sufficient evidence to reproduce a decision. Preserve corrections as additional records rather than rewriting financial history.

Bound market-data retention and downsample for charts; retain financial and decision evidence according to an explicit retention policy. Secrets are excluded from every record. Security and deployment belong to [CLOUD](CLOUD.md).

## Unresolved

- Language/runtime and libraries; use maintained dependencies without mandatory monthly fees.
- Store technology and backup strategy: must support transactions, exact accounting and restart recovery; choose after hosting durability constraints are known.
- Candle aggregation/source, event transport, data retention and dashboard chart library.
- Dedicated account versus segregated allocation, and policy for externally placed orders.
- Live activation persistence, authorization expiry and executor ownership mechanism.

## Deployment 1B safety boundary

The connectivity adapter exposes one narrowly defined signed POST operation: Binance Spot `POST /api/v3/order/test`. It is disabled by default, validates current exchange metadata and exact decimal filters locally, and cannot select a real-order path. The POST transport sends once and never retries; existing safe GET retry behavior remains unchanged. This diagnostic gate is not a general execution adapter and grants no Live-order authority.
