# Quick start: synthetic demo

The demo is completely synthetic. It contains a fictional tenant, fictional
CloudStack API, fictional model gateway, invented offerings, and invented
prices. It is safe to run on a laptop and safe to publish.

## Requirements

- Docker Engine 24+ with Compose v2
- About 3 GB of free RAM and 5 GB of disk
- Free port `8080`

## Run

```bash
docker compose -f deploy/compose/compose.demo.yml up -d --build
```

Wait for health checks, then open <http://localhost:8080>.

The first start builds the portal and Python image, applies migrations, creates
the demo database, seeds synthetic identities, and indexes the demo knowledge.
The init container runs once and exits.

## Demo identities

| Subject | Role | What it shows |
|---|---|---|
| `demo-user` | Tenant user | Conversation, quotes, plans, approvals, resources, docs |
| `demo-operator` | Operator | Operator mapping/pricing/health/audit views |

Sign in on `/login` with the demo subject. OIDC and consent are intentionally
replaced by an explicit demo login in this mode; production requires OIDC.

## Things to try

1. Ask: `列出云主机` (list instances). The agent returns only `demo-user`'s
   fictional resources.
2. Ask: `创建一台 2C4G 的云主机` (create a 2 vCPU / 4 GiB instance). The agent
   resolves the synthetic offering and template, shows a quote and plan, and
   asks for approval.
3. Open the plan, inspect the effects and digest, and approve. The execution
   service rechecks the plan against the mock CloudStack, starts a job, and
   streams progress.
4. Stop an instance and inspect the compute/storage distinction in the quote.
5. Ask a pricing question and confirm the Mandarin disclaimer is shown.
6. Search the public demo documentation.
7. Sign in as `demo-operator` for the operator pages.

## What the demo proves

- The real CloudStack adapter, including signature v1, is used against the mock.
- Authentication, CSRF, conversation ownership, typed actions, pricing,
  digest-bound approval, the operation queue, SSE events, and audit are real.
- No real deployment configuration is needed.

## What the demo does not prove

- Real CloudStack capability differences, live console tokens, live snapshots,
  GPU passthrough, multi-host failover, or real identity-provider integration.
- Real usage aggregation. Demo estimates are current-state forecasts.
- Production capacity or load results.

## Stop

```bash
docker compose -f deploy/compose/compose.demo.yml down -v
```

## Non-Docker development

```bash
uv sync --extra dev
uv run pytest -q
uv run uvicorn conductor.api.app:create_app --factory --port 8000
```

The portal (`apps/portal`) proxies `/api` to the API in Vite development mode.
See [testing.md](testing.md) for PostgreSQL integration tests.
