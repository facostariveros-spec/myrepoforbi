# Agent guide

This repository is Adaptive Grid Manager. Implemented gates cover public/read-only Binance connectivity, disabled-by-default Spot test orders, scanner data, descriptive grid-candidate intelligence, and offline historical calibration. No bot creation, real trading, automated optimization, or capital decisions are authorized.

- Start with [README.md](README.md), then read only the authoritative documents relevant to the task. The README contains the routing map.
- Preserve the V1 boundaries and release gates in [REQUIREMENTS](docs/REQUIREMENTS.md). Human override has highest authority; every capital-changing or trading action requires Risk Manager authorization.
- Never introduce secrets into source, frontend, database, fixtures, or logs. Never enable Live implicitly.
- Keep Paper, Shadow and Live decision logic identical; isolate execution differences in adapters.
- Document unresolved choices explicitly. Do not invent trading thresholds, risk limits, credentials, or deployment approvals.
- Keep requirements in their owning document; cross-reference rather than duplicate. Update affected contracts when behavior changes.
- Implement only the requested stage. Avoid speculative services, frameworks, and dependencies. Run checks appropriate to the change and report results and remaining gaps.
- Historical research must preserve the observation/calibration/optimization separation in [CALIBRATION](docs/CALIBRATION.md). Never use post-evaluation data to construct a historical feature.
