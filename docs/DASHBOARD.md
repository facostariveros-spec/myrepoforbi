# Dashboard — operator contract

Provide a private authenticated operator interface backed by sanitized backend data. Display the active mode prominently and distinguish simulated/hypothetical values from real balances. Include timestamps and freshness so stale data cannot appear live. Security controls are in [CLOUD](CLOUD.md).

## Required views

| View | Content |
| --- | --- |
| Scanner | Ranked symbols, eligibility/rejections, timeframe evidence, scores, uncertainty and reasons |
| Live charts | Streaming price/candles, selected timeframe, grid bounds/levels, orders and fills; clear stale/disconnected state |
| PNL and capital | Realized net and unrealized PNL separately, fees, deployed/available capital, reservations, reserve and HARD_CAP headroom |
| Grid health | Lifecycle, range classification, inventory/exposure, active action/reasons, cooldown, fill and reconciliation state |
| Compounding | Reserve, trigger value and defined baseline, eligibility, proposed method, approvals/deferrals, deployments and history |
| Controls | Individual bot stop, global stop and explicit human resume; requested versus confirmed execution state |
| System health | Market/account freshness, connectivity, execution/reconciliation, storage, notification delivery and cloud cost status |
| Decision log | Searchable timeline linking evidence, proposals, risk outcomes, operator events and resulting orders/fills |

Controls must persist and acknowledge operator intent through the backend, enforce authorization and show failures. Keep stops readily accessible. Consequential configuration changes and any future Live activation need an explicit review of the resulting mode/capital scope. Dashboard disconnect must not erase an already accepted stop. [RISK](RISK.md) defines stop semantics.

## Notifications

Support both Telegram and email for risk events, stop outcomes, connectivity/reconciliation failures, action failures, compounding results and Cost Guard alerts. Include severity, mode, bot/symbol where applicable, timestamp, reason, action taken and a dashboard reference. Exclude secrets and unnecessary account details.

Deduplicate repeated events, distinguish incident/recovery, retain delivery status and retry boundedly. Notification failure must be visible in system health and must not bypass risk controls. Transport/credentials and cloud-cost behavior belong to [CLOUD](CLOUD.md).

## Unresolved

Authentication/access model, chart library, refresh intervals, PNL display currency conventions, alert routing/severity policy and recipient configuration. Telegram/email are notification channels in V1; remote trading commands are not specified.

