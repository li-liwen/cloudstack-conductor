# ADR 0001: Tenancy and authorization

## Context

CloudStack identities are domain-scoped and usernames can repeat across
domains. External AI agents may be authorized by a different identity provider.
A model must not become a source of authorization truth.

## Decision

- Bind the OIDC subject to exact CloudStack user/account/domain UUIDs through a
  pre-provisioned mapping. Never merge by username or email.
- Use the mapped user's CloudStack API key for every cloud call, so CloudStack
  enforces its own permissions.
- Compute effective permission as an intersection of CloudStack permission,
  application policy, selected account/project context, MCP scope, and plan
  approval.
- Load the mapping on every request so role changes revoke promptly.
- Keep conversations private to their subject, independent of shared account
  resource access.

## Consequences

Duplicate usernames are safe; identity migration is an explicit operator
process; a stale or missing mapping fails closed. Cross-domain administration
requires CloudStack's own descendant permissions and a correct mapping, not
username impersonation.
