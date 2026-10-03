# Adaptive Grid Manager

An autonomous adaptive grid system for Binance Spot USDT markets, beginning in Paper and Shadow and progressing through tightly gated Live trials.

**Status: documentation foundation only.** There is no application code, connectivity, executable trading system, deployment, or Live capability yet. Empty source directories are tracked with `.gitkeep` placeholders. No runtime or dependencies have been selected.

## Documentation routing

Read the domain you are changing; every task does not need every document.

| Document | Authoritative for |
| --- | --- |
| [REQUIREMENTS](docs/REQUIREMENTS.md) | Product scope, invariants, release progression and product acceptance |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Component boundaries, mode parity, data flow and persistence contracts |
| [TRADING_ENGINE](docs/TRADING_ENGINE.md) | Scanner, grid lifecycle, adaptive actions and compounding workflow |
| [RISK](docs/RISK.md) | Authorization, capital accounting, limits, overrides and fail-safe behavior |
| [DASHBOARD](docs/DASHBOARD.md) | Operator views, controls and notification content |
| [CLOUD](docs/CLOUD.md) | Hosting, credentials, operational controls and Cost Guard |
| [TESTING](docs/TESTING.md) | Verification strategy and evidence for release gates |
| [V2_BACKLOG](docs/V2_BACKLOG.md) | Deferred features outside V1 |
| [AGENTS.md](AGENTS.md) | Concise contributor instructions |

`src/` will hold the backend and adapters, `tests/` verification assets, and `dashboard/` the operator interface. No packages or services are prescribed yet.

## Intended shape

Start with a modular single backend, a durable transactional store, exchange and notification adapters, and a private dashboard. Use deterministic decision logic across all modes, with execution selected explicitly. Separate decisions, risk authorization and exchange effects so every action can be explained and reconstructed. See [ARCHITECTURE](docs/ARCHITECTURE.md).

## Next step and open choices

The next stage is Binance connectivity, subject to [release gates](docs/REQUIREMENTS.md#deployment-progression). Language/runtime, persistence technology, deployment topology and all numerical strategy/risk settings remain undecided. Each domain document lists its unresolved decisions; documentation targets are not evidence that a strategy is safe or that a hosting budget has been met.

