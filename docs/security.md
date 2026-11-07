# Security design

This page describes the threat model and the controls implemented by the
public source. Deployment-specific controls (firewall, TLS certificates,
identity-provider policy, backup encryption keys) belong in the private
runbook.

## Assets

- CloudStack API keys for tenant users and restricted reader/provisioner roles.
- Identity-provider client secrets, session signing keys, vault master key.
- MCP client registrations, refresh tokens, and signing key.
- Console endpoints/tokens.
- Private pricebooks, overrides, customer identities, and conversations.
- Plan approvals and execution results.

## Trust boundaries

1. **Browser ↔ portal API.** Untrusted. Requires TLS, OIDC, signed session
   cookie, CSRF for mutations, tenant-scoped queries.
2. **Portal API/worker/MCP ↔ execution service.** Private network + shared
   internal token. The caller supplies an authenticated subject; execution
   loads the mapped principal itself and never trusts a role.
3. **Execution service ↔ CloudStack.** Untrusted network response; credentials
   are per-user and explicit. Upstream errors are sanitized.
4. **MCP client ↔ MCP service.** External OAuth client; OIDCProxy validates
   issuer/audience/resource, scopes, consent, refresh rotation, revocation.
5. **Build ↔ runtime.** Build contexts exclude private data; images receive
   no secrets as build args/layers.

## Controls

### Authentication and sessions

- OIDC authorization code + PKCE S256 + nonce; ID-token signature, issuer,
  audience, expiry, and nonce verified.
- Stable `sub` is the identity key. No username/email merge.
- MFA required for operators, domain admins, and users who had legacy 2FA.
- Sessions are signed HTTP-only cookies; CSRF double-submit token is bound to
  the signed session.
- Demo authentication is an explicit mode that production refuses.
- Security headers include CSP, HSTS (HTTPS), frame denial, no-sniff, and
  no-referrer.

### Authorization

- Effective permission is the intersection of CloudStack permission,
  application policy, account/project context, MCP scope, and approval.
- CloudStack calls use the user's key, so CloudStack remains an independent
  enforcement point.
- Cross-tenant reads are rejected in application code and rely on CloudStack
  scoping; conversations are private to their subject even within a shared
  account.
- Operator endpoints require the mapped operator role and CSRF.
- Role and account changes are read from the mapping on every request, so a
  revoked role takes effect immediately.

### Plans and approvals

- Plans are immutable and typed; parameters forbid extra fields.
- Approval is bound to the plan digest, the approving subject, and a ten-minute
  window. Execution rechecks digest, owner, expiry, and current plan state.
- A material change creates a new digest and requires new approval.
- Model output cannot supply approval, scope, price, or a raw cloud ID.

### Credentials and vault

- AES-256-GCM with a random nonce and AAD binding cloud + CloudStack user IDs.
- Master key outside PostgreSQL in a mounted secret file; ciphertext cannot be
  moved between users.
- Keys are never returned to browsers, MCP clients, prompts, or logs.
- Missing or disabled keys fail closed; no administrator fallback.
- Rotation is an operator action with re-encryption; no automatic rotation.

### Operations

- Durable queue with row locks; duplicate execution prevented by state checks.
- Preconditions revalidated immediately before mutation.
- Lost response → `needs_reconciliation`; no blind retry of creates.
- Cancellation never claims to undo accepted cloud work.
- Every operation, approval, plan, MCP call, and operator action is audited
  without secrets.

### Console

- Native endpoint remains server-side, encrypted with AAD bound to the lease.
- Explicit host/port allowlist; no wildcard; TLS required in production.
- Single-use launch token expires after 60 seconds, bound to the user.
- Relay authenticates the signed console cookie and the browser session, then
  an internal subject header; keystrokes are not recorded.

### MCP

- OIDCProxy with PKCE, issuer/audience/resource validation, consent, short
  access tokens, rotating refresh tokens, seven-day maximum authorization.
- Encrypted persistent client/token store on a private volume.
- Scope checked per tool; connection revocation checked per call.
- Curated tools only; no arbitrary command/shell/SQL/URL/credential surface.
- Deterministic output filtering and tenant-scoped execution calls.
- Metadata-fetch SSRF controls are provided by FastMCP's proxy; keep the
  deployment's OIDC endpoints restricted.

### Logging and errors

- Request bodies are not logged.
- A redaction filter removes registered secret values and credential-shaped
  strings; this is a defense-in-depth net.
- Public errors use stable codes and generic messages. Upstream detail is logged
  privately or discarded.
- The privacy scanner reports fingerprints, not values.

## Residual risks

- Shared VLAN connectivity does not imply packet isolation between tenants.
- A CloudStack administrator or compromised hypervisor is outside the
  application's trust boundary.
- The local model can be prompt-injected by user content; deterministic
  authorization prevents it from causing a mutation, but it can produce
  misleading explanations. Plans display authoritative effects and prices from
  code.
- A configuration mistake that puts private values into a public file is the
  main publication risk; the gate and CI are designed to catch it, but the
  owner's private denylist must be maintained.
- No payments/invoices/authoritative balance are provided; estimates are
  informational.
