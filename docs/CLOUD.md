# Cloud and operations — deployment authority

## Budget and topology targets

Use GCP with a **$0/month initial-testing target** and **≤$5/month target at future $5k–$7k capital**. These are design constraints to validate, not verified provider price guarantees. No dependency may impose a mandatory monthly fee. Do not base ongoing affordability on temporary credits.

Begin with local Paper testing when it avoids hosting cost. For hosted testing, evaluate the smallest suitable GCP compute footprint with a durable store and static/private dashboard delivery. Keep the modular backend together. Avoid Kubernetes, managed database baselines, paid market-data subscriptions and always-on auxiliary services unless they can meet the budget with measured evidence.

Before selecting topology, verify current regional pricing, free-tier eligibility, continuous worker support, persistent storage, exchange access from the region, egress, logging, secret access and backup costs. A scale-to-zero HTTP service must not be assumed suitable for continuous trading, streams or durable local state. Reliable execution and recovery take priority over a nominal free-tier fit; report infeasibility instead of silently exceeding budget or degrading safety.

## Secret handling

Binance secrets must never reach frontend, repository, database or logs. Retrieve them only in the backend at runtime from a dedicated secret facility with tightly scoped workload identity; for local development use an approved OS credential facility or session-only injection. Dedicated secret storage is distinct from the application database. Do not persist credential values in application configuration, debug dumps, request traces, fixtures or backups.

Use public/no-key access where possible, read-only credentials for Shadow, and separately scoped Spot trading credentials for approved Live. Never grant withdrawal, Margin or Futures permissions. Apply IP restrictions where feasible, rotate/revoke keys, sanitize exception paths, and keep frontend build/runtime configuration free of credentials. Telegram/email credentials receive equivalent protection.

## Operations and Cost Guard

Monitor process health, data staleness, clock drift, reconciliation, storage, execution failures and notifications. Bound log verbosity, retention, network usage and retries. Preserve audit/financial records under an explicit retention policy. Back up durable state and test restoration; reconcile exchange state after every recovery. Cost targets must include these controls.

Cost Guard monitors billing/budget signals alongside faster resource/usage indicators, establishes expected usage and detects anomalies. Billing data may lag; budget alerts alone are not a hard spending cap. Enforce available quotas/resource bounds and alert through [DASHBOARD](DASHBOARD.md) channels.

On anomaly, throttle optional work such as chart refresh, redundant scans and verbose telemetry first. Do not kill the executor or delete storage while orders/exposure remain unmanaged. If safe operation cannot continue within policy, request a risk-authorized controlled stop, reconcile orders and notify the operator. Record signal, estimated cost, policy and action. Configuration and numerical thresholds require approval before deployment.

## Unresolved before hosted operation

- GCP project, billing account, region, compute/storage choice and current cost estimate.
- Always-on versus scheduled operation, durable storage/backup layout and recovery objectives.
- Secret facility, key restrictions, operator authentication and private dashboard access.
- Cost Guard data sources, polling cadence, anomaly thresholds, usage ceilings and escalation policy.
- Telegram/email transports and their no-mandatory-fee suitability.

The foundation created no cloud resources, keys, notification sends or deployment.

## Deployment 1: connectivity service

The stateless Python 3.12 ASGI service exposes `/health` and `/connectivity/binance` and listens on `PORT`. It requires no credentials for public checks. When both `BINANCE_API_KEY` and `BINANCE_API_SECRET` are present, it also performs read-only signed diagnostics; provide these to Cloud Run through Secret Manager references, never literal deploy-command values.

Local setup and run:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
PORT=8080 .venv/bin/uvicorn adaptive_grid_manager.app:app --host 127.0.0.1 --port 8080
```

Example build and deploy commands (do not execute until project, region, access policy and current costs are approved):

```bash
gcloud builds submit --project "$GCP_PROJECT_ID" --tag "$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$ARTIFACT_REPOSITORY/adaptive-grid-manager:connectivity-1" .
gcloud run deploy adaptive-grid-manager --project "$GCP_PROJECT_ID" --region "$GCP_REGION" --image "$GCP_REGION-docker.pkg.dev/$GCP_PROJECT_ID/$ARTIFACT_REPOSITORY/adaptive-grid-manager:connectivity-1" --platform managed --port 8080 --no-allow-unauthenticated --min-instances 0 --max-instances 1 --set-secrets 'BINANCE_API_KEY=binance-api-key:latest,BINANCE_API_SECRET=binance-api-secret:latest'
```

Omit `--set-secrets` for the public-only gate. The commands assume the Artifact Registry repository and Secret Manager entries already exist; they intentionally do not create GCP resources. Keep Cloud Run authenticated. Before executing, verify Binance reachability from the selected region and recheck cost/free-tier assumptions.
