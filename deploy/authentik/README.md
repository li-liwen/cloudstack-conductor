# Authentik blueprints

Generic, version-controlled templates for the identity provider. They contain
placeholders and `!Env` references only; every real value belongs in a private
operator workspace outside this repository.

## Files

| File | Purpose |
|---|---|
| `blueprints/oidc-portal.yaml` | Portal OIDC client |
| `blueprints/oidc-openwebui.yaml` | Optional Open WebUI OIDC client |
| `blueprints/oidc-mcp.yaml` | MCP OIDC client |
| `blueprints/saml-cloudstack-domain.template.yaml` | One SAML provider per CloudStack domain |
| `blueprints/policies-mfa.yaml` | MFA/role/domain-scope policy expressions |

## Before importing

1. Review every file against the installed Authentik version (blueprint model
   and field names differ between releases).
2. Replace placeholders and supply private values through Authentik's env or a
   private secret mechanism. Do not put real values into these files.
3. Create private groups matching the policy templates and the portal's
   `CONDUCTOR_AUTH__OPERATOR_GROUPS` / `DOMAIN_ADMIN_GROUPS`.
4. Use an explicit consent flow for the portal, Open WebUI, and MCP. Never
   disable authorization consent for MCP.
5. Create one SAML provider per exact CloudStack domain with a unique entity
   ID. Never share a provider between domains.
6. Bind each SAML application to a policy that compares the user's mapped
   `conductor_cloudstack_domain_id` with the application's domain. The supplied
   expression fails closed without the private binding.
7. Publish aggregated SAML metadata containing only configured providers.
8. Register the exact portal callback URI:
   `${CONDUCTOR_PUBLIC_BASE_URL}/api/v1/auth/callback`.

## Import order

1. Groups and policies.
2. Portal/Open WebUI/MCP OIDC providers and applications.
3. SAML providers, one per domain.
4. Domain bindings and metadata publication.
5. Test each flow with synthetic identities before enabling real users.

## Secret handling

- Blueprints are configuration, not a secret store. The Authentik database and
  its backups contain client secrets and tokens; protect and encrypt them.
- Keep the service token used by the identity bridge in a file, not in a
  blueprint.
- Do not print tokens, password-write requests, SAML assertions, or session
  cookies in logs.

## Rollback

Keep the previous blueprints and database backup. Disable a provider or policy
binding rather than deleting history when investigating an incident. Never
reuse an entity ID across domains after a rollback.
