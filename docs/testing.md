# Testing

## Layers

| Layer | Command | Needs |
|---|---|---|
| Unit | `uv run pytest tests/unit tests/privacy` | Python only |
| PostgreSQL integration | `CONDUCTOR_TEST_DATABASE_URL=... uv run pytest -m requires_db` | PostgreSQL + pgvector |
| Portal | `cd apps/portal && npm test` | Node |
| Synthetic demo | `docker compose -f deploy/compose/compose.demo.yml up --build` | Docker |
| Publication gate | `uv run python tools/publication_gate.py --check-only` | Gitleaks + private denylist |

## PostgreSQL integration setup

```bash
docker run --rm -d --name conductor-test-db \
  -e POSTGRES_PASSWORD=test-only \
  -e POSTGRES_DB=conductor_test \
  -p 127.0.0.1:5432:5432 pgvector/pgvector:pg16
export CONDUCTOR_TEST_DATABASE_URL='postgresql+asyncpg://postgres:test-only@127.0.0.1:5432/conductor_test'
uv run pytest -m requires_db
```

The integration tests create/drop their own tables. Never point them at a real
deployment database.

## What is tested

- CloudStack signature v1, pagination, error mapping, job polling, lost-response
  reconciliation, console endpoint validation, usage parsing;
- decimal component arithmetic, compute-only discounts, effective-dated
  account/domain overrides, unpriced offerings, offered shape binding;
- workbook import separation of compute and storage, combined-total validation,
  shape mismatch rejection, checksum recording;
- Asia/Shanghai month and half-year boundaries and calendar durations;
- typed action policy and approval requirements, SSH key validation, firewall
  CIDR validation, network CIDR restrictions, guest task parameter safety;
- plan digest materiality and approval expiry/ownership;
- vault AES-GCM AAD binding and tamper rejection, sessions, CSRF, MFA claims,
  branding validation, rate limiting;
- privacy scanner canaries in temporary repositories: private addresses,
  deleted-file history, commit messages, non-noreply identities, absolute
  symlinks, missing denylist, blocked file types, and redaction;
- end-to-end plan → approval → queued operation → execution against a mocked
  CloudStack with PostgreSQL;
- API authentication, session, CSRF, security headers, and tenant isolation;
- portal component tests and production build.

## Privacy canaries

Negative tests generate synthetic canaries in temporary repositories at runtime.
Do not commit real private values as fixtures. Scanner output must identify the
rule, relative path, location, and fingerprint without echoing the match.

## Clean export

The publication gate exports the exact candidate commit, installs dependencies
from public sources, runs the unit/integration-appropriate tests, builds the
portal, and scans generated outputs. A build that needs a private file fails the
gate by design.

## Evaluation and load

For a real delivery, maintain at least 100 reviewed Mandarin and 30 English
agent cases. Require all authorization/approval invariants to pass, at least 95%
correct completion or expected operational failure for unambiguous supported
tasks, no false success for failed cloud jobs, correct documentation
versions/citations, and authoritative arithmetic in code.

Load targets (private evidence):

- 50 authenticated sessions and 100 streaming connections;
- 30 minutes with 10 active local-LLM conversations and bounded dispatch;
- a four-hour mixed soak;
- authentication/registration abuse tests;
- controlled concurrent lifecycle operations within lab limits;
- P95 local session/cached reads below 500 ms, acknowledgement/progress below
  one second, no OOM or unbounded queue, no lost approval, duplicate mutation,
  or tenant leakage, and controlled backpressure.

Model latency must be reported separately from application and CloudStack job
latency. Live reports stay private; public performance examples must come from
synthetic runs or a reviewed aggregate.
