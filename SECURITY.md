# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem, a leaked credential, or a
private deployment detail. Use GitHub's private vulnerability reporting
("Security" → "Advisories" → "Report a vulnerability") for this repository, or
contact the maintainers through the private channel listed in the deployment
handoff.

Include:

- affected component and version/commit;
- a minimal reproduction with synthetic data;
- the impact you believe is possible;
- whether any real credential or tenant data may be exposed.

Do not include live credentials, customer data, private hostnames, or captured
production traffic. Redact evidence before sending it.

## Supported versions

The project is alpha; security fixes target the latest release and the `main`
branch. There is no long-term-support commitment yet.

## Security model in brief

- Browser sessions are signed, HTTP-only cookies; mutating requests require a
  CSRF token. OIDC uses the authorization-code flow with PKCE and a nonce.
- Identity mapping is pre-provisioned. The stable OIDC subject is the key;
  usernames and email addresses are never merged.
- Every CloudStack call uses the mapped user's own API key, decrypted only in
  the execution service. Vault ciphertext is bound to the cloud/user IDs.
- Effective permission is the intersection of CloudStack permission,
  application policy, selected account/project context, MCP scope, and the
  required approval.
- Approvals are authenticated application actions bound to an immutable plan
  digest and expire after ten minutes if execution has not started.
- The execution service rechecks the digest, approval, and live cloud state
  before mutating. Lost responses become `needs_reconciliation`; creates are
  never blindly retried.
- Console endpoints are decrypted server-side, validated against an explicit
  host/port allowlist, and exchanged for single-use user-bound launch URLs.
- MCP uses OIDCProxy with PKCE, audience/resource validation, per-client
  consent, short-lived access tokens, rotating refresh tokens, revocation
  checks, and encrypted client/token storage.
- Logs redact credential-shaped values and known secrets. Reports from the
  privacy scanner identify a rule, relative path, location, and fingerprint,
  never the matched value.

## Out of scope

The project does not provide a payment ledger, invoice, or authoritative
balance. Estimates are informational. It does not protect a tenant from a
CloudStack administrator, a compromised hypervisor, or shared-L2 network
sniffing; document those CloudStack-level risks in your deployment.
