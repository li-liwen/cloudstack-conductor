# Implementation status

This page is honest about what the public repository contains and what still
requires a private deployment. It is not a promise of a production SLA.

## Implemented and tested

| Area | State |
|---|---|
| Publication safety | Privacy scanner, Gitleaks config/hooks, publication gate, negative canaries |
| Public/private separation | Repository layout, `.dockerignore`, runtime configuration, no private defaults |
| Synthetic demo | Mock signed CloudStack API, mock local model, synthetic identities/prices/docs, Docker Compose |
| CloudStack adapter | Signature v1, typed resources, pagination, job polling, error mapping, usage reads, console endpoint, typed mutations |
| Pricing | Component decimal arithmetic, offering UUID bindings, effective-dated account/domain overrides, compute-only discounts, storage quoting, workbook importer, Asia/Shanghai periods, estimates/forecasts |
| Execution | Immutable plans, digest approval, revalidation, vault, per-user credentials, durable operation queue, steps/events, cancellation, reconciliation |
| Agent | Deterministic Mandarin/English intent rules, optional local-model refinement, LangGraph checkpoints, clarification, plan creation, SSE |
| Portal API | OIDC/demo auth, sessions/CSRF, conversations, SSE reconnection, plans/approvals, resources, quotes, docs, connections, operator views, console broker routes |
| MCP | OIDCProxy, encrypted client store, scopes, consent, curated tools, approval links, connection audit |
| Knowledge | Version-pinned ingestion, chunking, Chinese-aware lexical index, BGE-M3 client, pgvector + lexical RRF, citations |
| Usage | Restricted reader collection, idempotent windows, gap reporting, running/allocated separation, provisional labels |
| Portal | React/TypeScript/Vite, runtime branding, Mandarin-first i18n, pages for chat/plans/resources/operations/estimates/docs/connections/operator |
| CI | Public-hosted workflows for lint, unit, PostgreSQL integration, portal build, privacy, clean export |

## Requires private deployment work

- Real Authentik instance, OIDC clients, per-domain SAML providers, MFA policies.
- Real CloudStack credentials and identity mappings for test identities.
- Password/legacy-2FA migration against the real identity provider; the public
  code includes the state machine and adapters, but the live cutover must be
  rehearsed privately.
- Real private workbook import, overrides, and golden tests.
- Live console proxy allowlist and noVNC relay verification.
- Open WebUI, Authentik, Prometheus/Grafana, and backup integration.
- Private publication denylist, manifests, and the owner's release records.
- Load/evaluation evidence against the lab and real local model.

## Known limitations

- The public demo uses mock embeddings and a mock model; production rejects both.
- The MCP service requires OIDC; it is disabled in the demo Compose profile.
- Managed guest tasks are disabled unless an isolated guest executor is wired.
- Volume shrinking is intentionally unsupported.
- Live snapshots of running instances are refused.
- The portal's console page is a relay-status surface, not a full noVNC decoder;
  a production viewer is supplied by the deployment's approved console bundle.
- Usage forecasts are current-state/provisional in the demo; historical usage
  depends on the CloudStack usage server and the private reader credential.
- Public load numbers are not provided.
