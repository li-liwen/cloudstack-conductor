# MCP integration

The MCP service exposes curated business tools over authenticated Streamable
HTTP. It uses FastMCP's `OIDCProxy` in front of the deployment's identity
provider.

## OAuth and consent

- Authorization-code flow with PKCE S256.
- Protected-resource and authorization-server discovery.
- Issuer, audience, and resource validation by `OIDCProxy`.
- CIMD and controlled dynamic client registration.
- Client registrations and upstream token sets are persisted in an encrypted
  file store under a private volume.
- Explicit JWT signing key from a secret file.
- Per-client consent is required; do not set
  `require_authorization_consent=false`.
- Access tokens expire after 10 minutes; refresh tokens rotate with a maximum
  7-day authorization lifetime.
- Connection revocation is checked on every tool call.
- "Connected agents" in the portal lists identity, account/project context,
  scopes, last use, expiry, revocation, and audit history.

SSO removes repeated password entry, but it does not replace explicit
external-client authorization.

## Scopes

| Scope | Access |
|---|---|
| `docs:read` | Public documentation |
| `cloud:read` | Authorized inventory/status |
| `pricing:read` | Caller's effective quotes |
| `usage:read` | Permitted usage forecasts |
| `console:open` | Authorized console launch |
| `actions:propose` | Prepare cloud changes |
| `guest:propose` | Prepare supported guest changes |

Default requested scopes are `docs:read` and `cloud:read`. A client cannot
retrieve another tenant's resources, prices, quotes, operations, or
connections. No tool or scope can approve its own mutation.

## Tools

| Tool | Scope | Behavior |
|---|---|---|
| `cloud_list_instances` | `cloud:read` | Caller's instances |
| `cloud_get_instance` | `cloud:read` | One authorized instance |
| `cloud_list_volumes` | `cloud:read` | Caller's volumes |
| `cloud_list_networks` | `cloud:read` | Caller's networks |
| `cloud_list_offerings` | `cloud:read` | Available offerings |
| `cloud_quote` | `pricing:read` | Effective quote for an offering |
| `cloud_get_usage_estimate` | `usage:read` | Provisional current-state forecast |
| `cloud_prepare_action` | `actions:propose` | Persist a plan; return approval URL |
| `cloud_get_operation` | `cloud:read` | Poll by operation ID or plan ID |
| `cloud_open_console` | `console:open` | Single-use user-bound launch link |
| `guest_prepare_task` | `guest:propose` | Prepare a reviewed guest task |
| `docs_search` | `docs:read` | Versioned public docs search |
| `docs_get` | `docs:read` | One public document |
| `support_answer` | `docs:read` | Answer from cited excerpts |

There is no arbitrary CloudStack command execution, SQL, filesystem, shell,
URL fetch, or credential argument.

## Approval flow for external agents

1. The agent calls `cloud_prepare_action` with an action name and typed
   parameters.
2. The execution service validates and prices the plan. The tool returns
   `plan_id`, `digest`, effects, and an `approval_url`.
3. The matching user opens the portal and approves the exact digest.
4. The agent polls `cloud_get_operation` with `plan_id` or the resulting
   `operation_id`.
5. Execution revalidates the approval and live state before submitting.

An external agent can propose, but only the authenticated user approves.

## Client example

```json
{
  "mcpServers": {
    "cloudstack-conductor": {
      "type": "http",
      "url": "https://mcp.example.com/mcp"
    }
  }
}
```

Use MCP Inspector or another OAuth-capable client for testing. The client must
support discovery, PKCE, dynamic registration (or a pre-registered client), and
a browser consent step.

## TLS requirement

FastMCP's authorization-server implementation rejects an HTTP issuer at route
construction (`Issuer URL must be HTTPS`). Terminate TLS in front of both the
MCP public URL and the OIDC issuer, and configure the issuer to that HTTPS URL.
A test deployment may use a private CA, but every MCP client must trust it.
Do not disable certificate verification or expose OAuth over plain HTTP.

## Configuration

Set `CONDUCTOR_MCP_PUBLIC_URL`, `CONDUCTOR_MCP_SIGNING_KEY_FILE`,
`CONDUCTOR_MCP_STORAGE_DIR`, the OIDC issuer/client/secret, and optionally
`CONDUCTOR_MCP_ALLOWED_REDIRECT_URIS`. The reverse proxy must forward the whole
MCP hostname, including OAuth discovery, authorization, callback, and token
paths, not only `/mcp`.

## Disclosure boundaries

External agents may receive public documentation, allowlisted facts about
resources the caller can access, the caller's effective quote/estimate, and
user-bound console launch links.

They must never receive source pricebooks, other tenants' prices or overrides,
internal margin/procurement data, credentials or raw console tokens, private
administrative runbooks, or infrastructure details embedded in backend errors.
Deterministic output filtering applies to tools, caches, errors, retrieval, job
status, and connection management.
