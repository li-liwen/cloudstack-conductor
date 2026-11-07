# Architecture

CloudStack Conductor separates browser experience, agent reasoning, and cloud
execution so that no single process holds both a language model and the keys to
the cloud.

## Components

| Component | Responsibility | Holds |
|---|---|---|
| Portal (`apps/portal`) | Conversation UI, plan/approval cards, resource pages, operator views | No credentials |
| Portal API (`conductor.api`) | Browser sessions, OIDC, conversations, SSE, approval endpoints, resource/quote proxies, docs, MCP connection views | Session secret; no CloudStack keys |
| Agent worker (`conductor.worker`) | Intent extraction, LangGraph checkpoints, context loading, plan preparation calls | No CloudStack keys |
| Execution service (`conductor.execution`) | Authorization revalidation, typing, pricing snapshots, vault, CloudStack adapter, durable operation queue | Vault master key, per-user CloudStack keys |
| MCP service (`conductor.mcp`) | OIDCProxy, consent, scopes, curated tools, connection audit | MCP signing key, encrypted OIDC client store; no CloudStack keys |
| Embedding worker (`conductor.embeddings`) | Local BGE-M3 inference | Model weights |
| PostgreSQL | Application state, plans, approvals, operations, pricing catalogue, knowledge index, checkpoints, MCP connections | Encrypted credentials only |
| Reverse proxy (private) | TLS, routing, WebSocket/SSE buffering, source restrictions | Certificates (private) |

The execution service is the only process that decrypts customer CloudStack
credentials. The API, worker, and MCP service call it over a private Compose
network with a shared internal token and an authenticated subject. The
execution service reconstructs the mapped principal from the database; it never
trusts a caller-supplied role.

## End-to-end change flow

```text
user message
  → agent graph extracts intent and loads authorized resources
  → execution service validates typed parameters, capabilities, ownership
  → pricing engine produces an immutable PriceQuote
  → planner builds ActionPlan {preconditions, effects, downtime, data effects}
  → digest = SHA-256(canonical material fields)
  → portal shows the plan and approval link
  → user approves the exact digest (authenticated + CSRF, 10-minute window)
  → operation queued in PostgreSQL (Procrastinate)
  → execution rechecks approval digest and live cloud state
  → CloudStack call(s) with the user's own API key
  → job polling, per-step events, verification
  → succeeded / failed / needs_reconciliation
```

A model may explain a plan, but it cannot change the typed parameters, price,
approval, or execution path.

## Authorization intersection

```text
CloudStack permission
  ∩ application policy
  ∩ selected account/project context
  ∩ external-client scope (MCP only)
  ∩ valid approval for the exact plan digest
```

- CloudStack itself enforces the user's permissions because every call uses that
  user's API key.
- Application policy validates the action, account context, feature flag, and
  typed parameters.
- MCP scopes are checked per tool before any execution call.
- Execution requires a matching, unexpired approval for every action whose
  policy requires it.

## Durable state

- Plans are immutable. A material change creates a new digest and requires a new
  approval; rewording an explanation does not.
- Operations record steps, CloudStack job/resource IDs, errors, and results.
- A lost mutation response marks the operation `needs_reconciliation`; creates
  are never retried blindly.
- Cancellation stops unsubmitted work. It does not claim to undo a cloud job
  that was already accepted.
- Approvals expire ten minutes after approval if execution has not started.

Operation states:

```text
draft → awaiting_approval → queued → running → succeeded
                                     ├→ failed
                                     ├→ partially_succeeded
                                     ├→ needs_reconciliation
                                     └→ cancel_requested → cancelled
```

## Tenant isolation

- Identity mappings bind the stable OIDC subject to exact CloudStack user,
  account, and domain UUIDs. There is no username or email merge.
- Conversations are private to their subject, even when users share a
  CloudStack account.
- Pricing overrides are scoped by cloud and account/domain effective dates.
- MCP tools filter to the caller's authorized resources and return only
  allowlisted fields.
- Knowledge retrieval filters to public visibility; private pricebooks and
  runbooks are never ingested into the public index.

## Deployment shape

Docker Compose hosts the application stack on the private application server.
CloudStack management, database, storage, and hypervisors remain in place.
A private reverse proxy terminates TLS and forwards whole hostnames, including
OIDC discovery and callback paths.

See [deployment.md](deployment.md), [security.md](security.md), and the
[ADRs](adr/).
