# Configuration reference

All services read environment variables prefixed `CONDUCTOR_`, nested with
`__`. Secret values normally come from files mounted read-only. There are no
deployment-specific defaults in the code.

## Core settings

| Variable | Required | Meaning |
|---|---|---|
| `CONDUCTOR_ENVIRONMENT` | yes | `development`, `demo`, `test`, or `production` |
| `CONDUCTOR_CLOUD_ID` | yes | Logical cloud identifier used to scope mappings and prices |
| `CONDUCTOR_PUBLIC_BASE_URL` | yes | Public HTTPS origin of the portal/API |
| `CONDUCTOR_API_BASE_URL` | yes | Internal API URL, e.g. `http://api:8000` |
| `CONDUCTOR_EXECUTION_BASE_URL` | yes | Internal execution URL, e.g. `http://execution:8001` |
| `CONDUCTOR_DATABASE__URL` | yes | `postgresql+asyncpg://...` |
| `CONDUCTOR_INTERNAL_TOKEN_FILE` | yes | Secret file for service-to-service auth |
| `CONDUCTOR_VAULT_KEY_FILE` | execution | Secret file holding the AES master key |
| `CONDUCTOR_LOG_LEVEL` | no | `INFO` by default |
| `CONDUCTOR_ALLOWED_ORIGINS` | no | JSON list for local development CORS |
| `CONDUCTOR_TRUSTED_PROXY_CIDRS` | no | JSON list of proxy peers allowed to supply forwarding headers |

## Authentication

| Variable | Meaning |
|---|---|
| `CONDUCTOR_AUTH__MODE` | `oidc` for production, `demo` only for the synthetic demo |
| `CONDUCTOR_AUTH__ALLOW_DEMO_AUTH` | Explicit demo-login switch; production refuses it |
| `CONDUCTOR_AUTH__OIDC_ISSUER` | Exact issuer URL |
| `CONDUCTOR_AUTH__OIDC_CLIENT_ID` | OIDC client identifier |
| `CONDUCTOR_AUTH__OIDC_CLIENT_SECRET_FILE` | Secret file |
| `CONDUCTOR_AUTH__SESSION_SECRET_FILE` | Session/CSRF signing secret file |
| `CONDUCTOR_AUTH__OIDC_REDIRECT_PATH` | Callback path, default `/api/v1/auth/callback` |
| `CONDUCTOR_AUTH__OPERATOR_GROUPS` | JSON list of IdP groups granting operator |
| `CONDUCTOR_AUTH__DOMAIN_ADMIN_GROUPS` | JSON list granting domain-admin |
| `CONDUCTOR_AUTH__MFA_CLAIM` | Claim used to detect MFA (`acr` by default) |
| `CONDUCTOR_AUTH__SECURE_COOKIES` | Must be `true` in production |

The OIDC redirect URI is `CONDUCTOR_PUBLIC_BASE_URL` + redirect path. Register
that exact URI in the identity provider.

## Model gateway

| Variable | Meaning |
|---|---|
| `CONDUCTOR_LLM__BASE_URL` | OpenAI-compatible local gateway base URL, ending in `/v1` |
| `CONDUCTOR_LLM__MODEL_ALIAS` | The only approved model alias |
| `CONDUCTOR_LLM__API_KEY_FILE` | Optional secret file |
| `CONDUCTOR_LLM__MAX_CONCURRENCY` | Concurrent requests, default 4 |
| `CONDUCTOR_LLM__MAX_QUEUE` | Bounded queue, default 32 |
| `CONDUCTOR_LLM__MOCK` | `true` only for synthetic environments |

Never put a public-model API key here. The deployment exposes only the approved
local alias and does not fall back to an external provider.

## MCP

| Variable | Meaning |
|---|---|
| `CONDUCTOR_MCP_PUBLIC_URL` | Public MCP origin, e.g. `https://mcp.example.com` |
| `CONDUCTOR_MCP_SIGNING_KEY_FILE` | Explicit JWT signing key secret file |
| `CONDUCTOR_MCP_STORAGE_DIR` | Encrypted client/token store directory |
| `CONDUCTOR_MCP_ALLOWED_REDIRECT_URIS` | JSON list for dynamic client registration |
| `CONDUCTOR_FEATURES__MCP_ENABLED` | Enable the MCP service |

Production requires an OIDC issuer, a public MCP URL, and an explicit signing
key.

## Console

| Variable | Meaning |
|---|---|
| `CONDUCTOR_FEATURES__CONSOLE_ENABLED` | Enable the broker |
| `CONDUCTOR_CONSOLE__ALLOWED_HOSTS` | JSON list of permitted console-proxy hosts |
| `CONDUCTOR_CONSOLE__ALLOWED_PORTS` | JSON list, default `[443, 8443]` |
| `CONDUCTOR_CONSOLE__REQUIRE_TLS` | Default `true`; do not disable in production |

An empty host allowlist refuses every console launch. There is no implicit
wildcard.

## Pricing and knowledge

| Variable | Meaning |
|---|---|
| `CONDUCTOR_KNOWLEDGE__EMBEDDING_BASE_URL` | Local embedding service URL |
| `CONDUCTOR_KNOWLEDGE__EMBEDDING_MODEL` | `BAAI/bge-m3` by default (local path or HF repo) |
| `CONDUCTOR_KNOWLEDGE__CHUNK_TOKENS` | 300–2000, default 750 |
| `CONDUCTOR_KNOWLEDGE__OVERLAP_TOKENS` | Overlap, default 100 |
| `CONDUCTOR_KNOWLEDGE__TOP_K` | Final evidence chunks, default 8 |
| `CONDUCTOR_KNOWLEDGE__MOCK_EMBEDDINGS` | Tests/demo only; production refuses |
| `CONDUCTOR_USAGE__*` | Restricted usage-reader credential files for the collector |

Pricing data is loaded from the private operator database via the
`conductor-admin` importer; it is not an environment variable.

## Branding

| Variable | Meaning |
|---|---|
| `CONDUCTOR_BRANDING_CONFIG_FILE` | Read-only JSON document with allowlisted keys |
| `CONDUCTOR_BRANDING_ASSETS_DIR` | Read-only directory for logo/favicon files |
| `CONDUCTOR_PORTAL_STATIC_DIR` | Built portal assets (image default `/app/portal`) |

Branding documents must use application-relative asset paths. See
[branding.md](branding.md).

## Secret files

The Compose definition mounts secrets from a private directory outside the
repository. Typical files:

```text
session_secret
internal_token
vault_key
oidc_client_secret
mcp_signing_key
authentik_service_token
cloudstack_provisioner_key
cloudstack_provisioner_secret
usage_reader_key
usage_reader_secret
```

Generate random values with `openssl rand -base64 48`, store them mode `0600`,
and back them up encrypted. Do not place them in Git, image layers, build
arguments, or CI secrets for the public repository.

## Production startup checks

`environment=production` refuses:

- demo authentication;
- mock model or mock embeddings;
- insecure cookies;
- a missing OIDC issuer/client/secret;
- a non-PostgreSQL database URL;
- MCP enabled without a public URL and explicit signing key;
- console enabled without an allowlist.

A clear failure is intentional. Do not weaken these checks to get a deployment
running.
