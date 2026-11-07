# CloudStack Conductor

A tenant-aware agent and MCP control plane for Apache CloudStack.

CloudStack Conductor gives tenant users a conversational interface and
external AI agents a curated, scoped MCP interface to a CloudStack cloud. It
preserves each user's CloudStack identity and permissions: the agent can read,
quote, and prepare a plan, but a human must approve any change before the
execution service submits it. The model never receives administrator
credentials, never approves its own work, and never invents a price.

> Status: alpha. The synthetic demo and the public test suite work end to end.
> Live deployment requires the private configuration and identity-provider
> setup described in the documentation. See [docs/status.md](docs/status.md)
> for the current implementation matrix.

## Why it exists

- **Tenant-aware.** Identities map to exact CloudStack user, account, and
  domain identifiers. Usernames and email addresses are never merged.
- **Human-approved changes.** Every mutation is an immutable, priced plan bound
  to a digest. Approval is an authenticated action with an expiry, not a
  boolean supplied to a model.
- **One execution path.** The portal and MCP call the same execution service,
  typed actions, pricing engine, vault, and CloudStack adapter.
- **Decimal money.** Prices are decimal strings. Compute and storage are
  calculated from component rates; storage is never hidden in a compute rate.
- **Public source, private deployment.** The repository contains no deployment
  addresses, credentials, customer data, or real prices. Runtime configuration
  and branding are supplied privately.
- **Publication safety as code.** A privacy scanner, Gitleaks, Git hooks, and a
  complete local publication gate block private material before it can be
  committed or pushed.

## Architecture

```text
 Browser (React portal)
        │  HTTPS / SSE / WSS
        ▼
 Portal API ────────────── conversations, plans, approvals, operations
        │ internal token          │
        ▼                         ▼
 Agent worker (LangGraph)   Execution service ──► encrypted vault
        │ internal token          │              per-user CloudStack keys
        └────────────────────────┴──────────────► CloudStack API
 MCP service (OIDCProxy) ───────────────────────► same execution service
```

- **Portal API** owns browser sessions, conversations, SSE streams, and the
  approval UI. It never decrypts CloudStack credentials.
- **Agent worker** resolves intent with deterministic rules and an approved
  local model, loads authorized context, and prepares plans. It cannot approve
  or execute.
- **Execution service** is the only process that decrypts per-user CloudStack
  keys. It revalidates authorizations and live preconditions, calculates price
  snapshots, and submits changes through a durable PostgreSQL queue.
- **MCP service** exposes a curated Streamable HTTP tool surface with OIDC,
  per-client consent, scopes, connection revocation, and approval links.
- **PostgreSQL** stores application state, audit events, queued work, MCP
  connections, pricing catalogues, and the knowledge index (`pgvector`).

More detail: [docs/architecture.md](docs/architecture.md).

## Quick start (synthetic demo)

Requirements: Docker Engine and Docker Compose v2. Roughly 3 GB of RAM is
enough for the synthetic demo.

```bash
docker compose -f deploy/compose/compose.demo.yml up -d --build
# open http://localhost:8080
```

Sign in with the demo identity `demo-user` (ordinary user) or
`demo-operator` (operator views). The demo contains a fictional cloud, a
fictional tenant, fictional offerings, and synthetic prices. It includes:

- a conversation with clarify → quote → plan → approval → execution;
- a mock CloudStack API that verifies request signatures;
- a mock local model gateway;
- synthetic public documentation in the knowledge index.

The demo is not a marketing screenshot of a real cloud, and its data is not
derived from a customer deployment. Stop it with:

```bash
docker compose -f deploy/compose/compose.demo.yml down -v
```

## Local development

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check src tests tools demo
uv run mypy
```

The portal is a separate Node project:

```bash
cd apps/portal
npm ci
npm run dev      # Vite dev server; proxy to the API
npm test
npm run build
```

Optional PostgreSQL-backed integration tests:

```bash
export CONDUCTOR_TEST_DATABASE_URL='postgresql+asyncpg://conductor@localhost:5432/conductor_test'
uv run pytest -m requires_db
```

## Configuration

Services read `CONDUCTOR_*` environment variables and secret files. Production
refuses to start with demo authentication, mock services, insecure cookies, or
a missing identity provider. There are no private defaults.

See [docs/configuration.md](docs/configuration.md) and the parameterised
[production Compose definition](deploy/compose/compose.prod.yml).

## Security and publication

- Do not open a public issue with deployment addresses, credentials, or live
  logs. Follow [SECURITY.md](SECURITY.md).
- Contributions must be generic. Read [AGENTS.md](AGENTS.md) before staging.
- The local gate is mandatory for the repository owner:

```bash
tools/privacy/install_gitleaks.sh
tools/privacy/install_hooks.sh
uv run python tools/publication_gate.py --check-only
```

See [docs/release.md](docs/release.md) for the complete publication procedure.

## Repository layout

| Path | Contents |
|---|---|
| `src/conductor/` | API, execution, worker, MCP, pricing, CloudStack adapter, knowledge, usage |
| `apps/portal/` | React + TypeScript + Vite portal |
| `deploy/` | Dockerfile, Compose definitions, Nginx guidance, Authentik blueprints, CloudStack UI patches |
| `demo/` | Synthetic CloudStack/LLM services, fixtures, seed data, demo knowledge |
| `docs/` | Public architecture, setup, security, and contributor documentation |
| `tests/` | Unit, privacy-canary, and PostgreSQL integration tests |
| `tools/` | Privacy scanner and the publication gate |

## License

Apache License 2.0. See [LICENSE](LICENSE).
