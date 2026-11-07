# Contributing

Thanks for helping CloudStack Conductor. This is a public repository; keep all
contributions generic and reproducible.

## Development setup

```bash
git clone <your-fork>
cd cloudstack-conductor
uv sync --extra dev
uv run pytest -q
cd apps/portal && npm ci && npm test && npm run build
```

Requirements: Python 3.12+, Node.js 22+, Docker for the synthetic demo, and
PostgreSQL 16 with pgvector for integration tests.

## Workflow

1. Fork and branch from `main`.
2. Keep changes focused and add tests for behavior, especially authorization,
   approval, pricing, and privacy boundaries.
3. Run `uv run ruff check src tests tools demo`, `uv run ruff format --check`,
   `uv run mypy`, and `uv run pytest -q`.
4. Run the privacy scanner: `uv run python tools/privacy/scan.py --scope tree`.
5. Open a pull request using synthetic data only. Do not attach live logs,
   screenshots of a real deployment, database dumps, or pricebooks.
6. Public CI must pass. No workflow may receive deployment credentials.

`git add .` is not accepted in the owner's publication workflow; list explicit
public paths. Never use `--no-verify`.

## Code style

- Python: Ruff formatting/lint, type hints on public functions, Pydantic models
  for boundaries, `decimal.Decimal` for money.
- TypeScript/React: strict TypeScript, accessible components, runtime branding,
  no hardcoded deployment identity.
- Tests: deterministic, no sleeps where an injected clock or fake solves the
  problem, and no real private values as fixtures.
- Documentation: write it fresh. Do not copy the private execution plan.

## Commit messages

Use an imperative summary, for example:

```text
Add decimal storage pricing for detached volumes
```

Do not put addresses, tenant names, or credentials in commit messages, tags, or
branch names. The gate scans metadata as well as file content.

## Security reports

See [SECURITY.md](SECURITY.md). Do not disclose a vulnerability publicly before
the maintainers have had a reasonable opportunity to respond.

## Code of conduct

Be respectful, assume good faith, and keep discussions technical. Harassment,
doxxing, and sharing another person's private deployment details are not
acceptable.
