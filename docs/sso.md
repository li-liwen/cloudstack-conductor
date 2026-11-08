# SSO, identity mapping, and password migration

CloudStack Conductor uses one Authentik installation as the identity provider.
OIDC protects the portal, console, Open WebUI, and MCP upstream authentication.
Each exact CloudStack domain gets its own SAML provider so that CloudStack
4.22's username-plus-IdP-entity resolution cannot cross domains.

This page describes the generic design and the private handoff. Real issuer
URLs, client IDs, secrets, domain IDs, usernames, and mappings belong in the
private operator workspace.

## Identity mapping

A pre-provisioned mapping binds the stable OIDC `sub` to:

```text
cloud_id
cloudstack_user_id
cloudstack_account_id
cloudstack_domain_id
cloudstack_username
mapping_version
```

It never merges identities by username or email. Duplicate usernames in
different domains are therefore safe.

Operators provision mappings before first login:

```bash
conductor-admin import-mapping /private/path/mappings.json
```

The portal rejects an unmapped or incomplete identity. Role changes are read
from the mapping on every request.

## Domain-safe SAML

For each CloudStack domain:

1. Create a unique SAML provider/entity ID.
2. Restrict the provider to identities mapped to that domain (group/property
   policy).
3. Emit the original CloudStack username in the signed `uid` attribute.
4. Bind the CloudStack user with `authorizeSamlSso` using an isolated
   provisioner credential.
5. Publish aggregated metadata containing only configured providers.
6. Enforce signature validation, request correlation, replay rejection, and
   clock checks.

A domain administrator operates as their own identity and uses CloudStack's
existing descendant permissions. Do not impersonate a similarly named user in
a child domain.

### Management-server plugin

`saml2.*` configuration alone is not enough. The CloudStack SAML2 user
authenticator plugin must be installed on every management server and the
service restarted. Some packages ship the plugin separately or omit it. Verify
the plugin before changing global SAML settings:

```bash
# The command should be discoverable after the plugin is loaded.
curl -sS 'http://CLOUDSTACK/client/api/?command=listIdps&response=json'
```

An HTTP 401 with "command ... does not exist, is not available for user" in
the management log means the authenticator plugin is not registered, not that
the IdP is misconfigured. Install the matching version of
`cloud-plugin-user-authenticator-saml2`, restart management, then configure
`saml2.enabled` and the IdP metadata. Do not enable global SAML on a production
management server until the plugin and metadata fetch are verified on a
canary.

### Blueprints

`deploy/authentik/blueprints/` contains generic, version-controlled templates:

- `oidc-portal.yaml`
- `oidc-openwebui.yaml`
- `oidc-mcp.yaml`
- `saml-cloudstack-domain.template.yaml` (copy one per domain)
- `policies-mfa.yaml`

They use placeholders and Authentik `!Env` references; no secrets are embedded.
Import order, private value requirements, and metadata publication are
documented in `deploy/authentik/README.md`.

## Password migration

State machine:

```text
unmigrated
→ legacy_credentials_verified
→ idp_credentials_ready
→ required_mfa_ready
→ cloudstack_saml_authorized
→ complete
```

First login:

1. Resolve the pre-provisioned mapping.
2. Validate the password through native CloudStack login in a temporary
   session.
3. Confirm the returned user/account/domain IDs match the mapping.
4. Validate existing 2FA through the same temporary session when enabled.
5. Write the password through Authentik's supported password mechanism.
6. Clear password/OTP material before subsequent interactive stages.
7. Complete required Authentik MFA.
8. Authorize the correct domain-specific SAML provider.
9. Mark completion and close the temporary CloudStack session.

Implementation: `src/conductor/identity/`. The service accepts passwords only
in request bodies over TLS, never logs or persists them, uses row locks for
concurrent/interrupted migrations, and records only non-secret evidence hashes.
It does not export password hashes or maintain permanent password sync.

For identities with existing 2FA:

- verify the old factor;
- establish and verify the Authentik factor;
- then complete SSO-only CloudStack authentication.

API keys are preserved and stay in the encrypted vault. Disabled API access is
respected; there is no administrator fallback for a customer request.

## Enabling the bridge

Set `CONDUCTOR_IDENTITY__ENABLED=true` and provide:

| Variable | Meaning |
|---|---|
| `CONDUCTOR_IDENTITY__AUTHENTIK_BASE_URL` | Private Authentik base URL (HTTPS) |
| `CONDUCTOR_IDENTITY__AUTHENTIK_SERVICE_TOKEN_FILE` | Service token file |
| `CONDUCTOR_IDENTITY__CLOUDSTACK_API_URL` | CloudStack management API URL |
| `CONDUCTOR_IDENTITY__PROVISIONER_API_KEY_FILE` | Isolated provisioner key |
| `CONDUCTOR_IDENTITY__PROVISIONER_API_SECRET_FILE` | Isolated provisioner secret |
| `CONDUCTOR_IDENTITY__DOMAIN_PROVIDER_MAP_FILE` | Private JSON domain→provider map |

The portal mounts `/api/v1/identity` only when enabled. Without complete
configuration the service fails clearly rather than silently skipping a stage.

## Recovery

- A failed stage does not reset a password. Operators inspect the private stage
  record and retry only the incomplete step.
- A lost temporary CloudStack session is discarded; start the first-login flow
  again.
- If an Authentik password write succeeded but the next stage failed, do not
  write the password again; continue from `idp_credentials_ready`.
- If SAML authorization succeeded but completion failed, resume at
  `cloudstack_saml_authorized`.
- All recovery evidence stays private.

## Acceptance tests

Test duplicate usernames in different domains, duplicate emails without
merging, wrong SAML provider/domain, invalid signature/replay/issuer/request
mismatch, wrong legacy password/OTP, interrupted/concurrent migration, existing
API-key preservation, disabled API access, role/account changes, cross-tenant
conversations/resources/prices/jobs/consoles/MCP connections, shared-account
behavior, and correct domain-admin descendant scope.
