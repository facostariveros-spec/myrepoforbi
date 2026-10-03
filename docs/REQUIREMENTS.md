# Product requirements — V1 authority

## Objective and scope

Build a production-quality autonomous adaptive grid system with explainable decisions and operator control. Architecture must support eventual $5,000–$7,000 capital without redesign; initial Live rollout is $50, then $100. Capital amounts are rollout limits, not return expectations or automatic scaling approvals.

### Non-negotiable boundaries

- Binance **Spot only**, with **USDT quote only**. No Futures, Margin, leverage or withdrawals, including withdrawal automation and withdrawal permissions.
- Human override has highest authority. Automated actions cannot override an operator stop. Overrides cannot grant prohibited capabilities or bypass Risk Manager authorization.
- Risk Manager authorizes every capital-changing or trading action, including opening, placing/canceling/replacing orders, expansion, regridding, closing and compounding.
- Maximize safe capital utilization, ideally 100%, without weakening safety or quality thresholds to deploy idle funds. Fees, exchange constraints and risk buffers can require idle USDT.
- Paper, Shadow and Live share the same decision logic. Mode differences belong to data/execution adapters and explicit configuration, not strategy forks.
- Every automated trading decision, including inaction, is explainable and auditable.
- No dependency may require a mandatory monthly payment. Cloud budget targets and secret handling are owned by [CLOUD](CLOUD.md).

## Functional requirements

| Area | Required outcome | Detail authority |
| --- | --- | --- |
| Intelligence | Analyze 15m, 1h, 4h and 1d; rank candidates with eligibility and rejection reasons; directional strength alone is insufficient | [TRADING_ENGINE](TRADING_ENGINE.md) |
| Adaptation | Support KEEP, WAIT, EXPAND, REGRID and CLOSE; distinguish temporary deviation, range expansion, new range and confirmed breakout; prevent OPEN/CLOSE oscillation | [TRADING_ENGINE](TRADING_ENGINE.md) |
| Compounding | V1 reserve funded only by realized net profit; evaluate COMPOUND_REGRID or COMPOUND_INJECT at configurable COMPOUND_TRIGGER_PCT; obey Risk Manager and HARD_CAP | [TRADING_ENGINE](TRADING_ENGINE.md), [RISK](RISK.md) |
| Operator experience | Scanner rankings/reasons, live charts, PNL, grid health, compounding, individual stop, global stop, system health and decision log | [DASHBOARD](DASHBOARD.md) |
| Operations | Telegram and email notifications; Cost Guard for cloud anomalies; recovery and reconciliation | [CLOUD](CLOUD.md) |
| Security | Binance secrets never reach frontend, repository, database or logs | [CLOUD](CLOUD.md) |

USDC/BNB fee optimization and multi-quote support are V2 only; see [V2_BACKLOG](V2_BACKLOG.md).

## Deployment progression

Advance in this exact order. Completion means evidence reviewed against [TESTING](TESTING.md), not simply code merged. Live activation and each capital increase require explicit human approval.

| Stage | Exit evidence |
| --- | --- |
| foundation | Domain documents and empty implementation directories; unresolved decisions recorded |
| Binance connectivity | Public/read-only access, symbol filter and clock handling; credentials secured; no trading effects |
| scanner/intelligence | Multi-timeframe rankings and reasons; anti-FOMO fixture coverage |
| paper | Shared pipeline with simulated fills, fees and balances; no real orders |
| adaptive engine | Adaptive classifications/actions and hysteresis verified in Paper |
| capital/risk | Central authorization, reservation/accounting and override tests pass |
| dashboard | Required views and authenticated controls verified |
| operations | Recovery, notifications, secret hygiene and Cost Guard verified |
| shadow | Real market/read-only account inputs; hypothetical execution; parity and reconciliation evidence |
| live-disabled | Live adapter integrated and tested with effects forcibly disabled; explicit activation gate verified |
| live $50 | Human-approved capital limit; supervised trial with stop/recovery evidence |
| live $100 | $50 trial reviewed; explicit approval and updated limits |
| gradual scaling | Incremental evidence-based approvals toward eventual $5k–$7k envelope |

The Risk Manager contract exists from the first executable Paper pipeline; the later capital/risk stage completes and validates its full controls. No earlier stage may bypass it.

## Product acceptance and open decisions

V1 must demonstrate the scope boundaries, both named market scenarios, mode parity, auditable decisions, safe compounding, operator precedence and operational recovery. Test ownership is in [TESTING](TESTING.md).

Before Live, decide trial durations, measurable acceptance thresholds, allowed symbols/account eligibility and scaling criteria. Strategy profitability is unproven. No numerical strategy settings or automatic release permissions are implied by this foundation.

