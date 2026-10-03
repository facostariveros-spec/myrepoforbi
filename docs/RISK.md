# Risk — authorization and capital authority

## Authority and authorization

Human override has highest authority over autonomous strategy. A persisted individual or global stop blocks new/increased exposure immediately; it cannot be cleared by the engine. All trading and capital changes, including operator-requested ones, pass Risk Manager. The manager must offer a protective cancellation path without requiring healthy market signals; inventory sales still require explicit constraints and authorization. It cannot authorize capabilities prohibited by [REQUIREMENTS](REQUIREMENTS.md).

Every proposal receives allow, deny or defer with reason codes, evidence, limits checked and the exact authorized effects. Check symbol/quote eligibility, mode, operator state, data freshness, reconciled balances/orders, price/quantity/notional filters, fees, liquidity, exposure, capital reservations, concentration, loss limits and HARD_CAP. Revalidate before dispatch if the authorization is expired or state changed. Reject unsupported or ambiguous actions by default.

## Capital and profit accounting

Use exact decimal units and explicit rounding per exchange filters. Distinguish available USDT, committed order funds, held inventory, principal, realized net PNL, unrealized PNL, compounding reserve and pending reservations. These are ownership/accounting views; do not add overlapping balances together as independent capital.

`HARD_CAP` is the configured absolute deployment ceiling. Enforce it across active bots, held inventory, outstanding orders and concurrent reservations using an agreed valuation method. Reinvestment does not increase the cap. Unrealized losses must not create artificial spending headroom. Serialize reservation updates so competing bots cannot spend the same funds.

Only settled, reconciled realized **net** profit is eligible for compounding, after attributable fees, realized losses and already compounded/reserved amounts. Preserve cost basis and fill-level attribution. Unrealized appreciation is never profit reserve. Positive reserve accounting must also be backed by spendable assets; do not sell or convert assets implicitly to manufacture compoundable USDT. Negative realized results reduce eligibility according to the approved ledger policy.

The objective is maximum safe utilization, ideally 100%. Required fee buffers, minimum order sizes, residual inventory, poor candidate quality and limits take precedence. Waiting with unused capital is an acceptable outcome.

## Stops and degraded conditions

Individual stop applies to one bot; global stop applies to all bots and new entries. Persist scope, actor, timestamp and reason. Stop request, exchange cancellation confirmation and inventory liquidation are distinct outcomes. Never display a requested stop as completed before reconciliation confirms its effects. Resume requires an explicit human action followed by normal eligibility/risk checks.

Default stop intent is to block increased exposure and request authorized cancellation of resting orders. Holding versus liquidating remaining inventory requires a separately approved policy; do not equate stop with automatic market sell.

On stale/missing market data, account discrepancies, unknown order outcomes, connectivity loss, authorization failure or store failure, block new/increased exposure. Preserve visibility and attempt only safely authorized protective actions when possible. Alert on unresolved exposure. Existing exchange orders may remain live during outages; recovery must reconcile them before resuming.

## Unresolved before Live

- Numerical HARD_CAP, per-bot/symbol limits, concentration, drawdown/loss limits and fee/liquidity buffers for each approved stage.
- HARD_CAP exposure valuation and treatment of price appreciation, residual inventory and external account activity.
- Profit cost basis, netting period, loss carryforward and accounting for any non-USDT fee charges; no V1 BNB fee optimization.
- Stop/liquidation policy, emergency action constraints, reconciliation tolerances and stale-data deadlines.

Fail closed for unset required Live limits; do not infer safe values from the $50/$100 rollout labels.

