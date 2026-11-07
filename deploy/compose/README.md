# Compose definitions

| File | Purpose |
|---|---|
| `compose.demo.yml` | Completely synthetic demo: mock CloudStack, mock model, demo data |
| `compose.prod.yml` | Parameterised production template; all values are private placeholders |
| `env.example` | Placeholder variables for the production template |

## Demo

```bash
docker compose -f deploy/compose/compose.demo.yml up -d --build
docker compose -f deploy/compose/compose.demo.yml down -v
```

The demo uses only synthetic data and obvious non-production values. Do not
copy its configuration into a real deployment.

## Production

1. Build the image from a clean export of an approved commit (see
   [deployment.md](../../docs/deployment.md)).
2. Copy `env.example` outside the repository, replace every placeholder, and
   create the secret files under a private directory.
3. Run migrations, then start with `--no-build`.
4. Keep the previous release image and Compose file for rollback.

The production template does not include Authentik itself; point
`OIDC_ISSUER` and the MCP OIDC client at the private Authentik installation and
provide the per-domain SAML blueprints from `deploy/authentik/`.

The optional `chat` profile adds Open WebUI. Configure it with OIDC only; local
registration and email-based merging must remain disabled.

## Network notes

- Compose publishes API/MCP/embeddings only on the configured bind address
  (loopback by default). The private reverse proxy is the only public path.
- Do not mount the Docker socket into any application container.
- The internal service network is private; the internal token is still required
  because "private network" is not an authorization boundary by itself.
- Pin image digests at release time and verify them on the server.
