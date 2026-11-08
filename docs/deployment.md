# Deployment guide

This guide is generic. Real endpoints, credentials, identity mappings, and
prices belong in a private operator workspace outside this repository.

## Prerequisites

- Linux application server with Docker Engine and Compose v2.
- PostgreSQL 16 with pgvector (the supplied Compose service or an external
  managed database).
- A private TLS reverse proxy.
- An identity provider (Authentik or equivalent OIDC/SAML provider).
- A local OpenAI-compatible model gateway for the approved alias.
- CloudStack 4.22.1.1 management API reachable from the execution service.

## Build from a clean export

Build images only from an approved Git commit exported by the publication gate,
never from an unrestricted working directory:

```bash
git archive --format=tar -o candidate.tar <approved-commit>
mkdir -p /tmp/conductor-build && tar -xf candidate.tar -C /tmp/conductor-build
docker build -f deploy/docker/Dockerfile -t cloudstack-conductor:<commit> /tmp/conductor-build
```

`.dockerignore` excludes private inputs, history, caches, tests, and evidence.
The Dockerfile uses explicit `COPY` paths, a non-root user, and no secrets in
build arguments. Frontend assets are built from generic source only.

Record the image digest:

```bash
docker image inspect --format '{{index .RepoDigests 0}}' cloudstack-conductor:<commit>
```

## Release bundle

A release contains:

- image tar(s) tagged by commit, with digests recorded;
- the parameterised Compose definition;
- database migrations;
- checksums;
- public documentation for the deployed version.

Ship private configuration and branding separately. Verify checksums on the
server, load images, run migrations, and start with `--no-build`.

## Configuration

Copy `deploy/compose/env.example` to a private file, fill placeholders, and
mount secret files from outside the repository. Never commit the resolved file.

Example layout on the server:

```text
/srv/conductor/
├── compose.yml              # from the release bundle
├── env                      # private, mode 0600
├── secrets/                 # private, mode 0700
├── branding/                # private read-only assets
├── releases/<commit>/       # retained previous release
└── backups/                 # encrypted, off-server copy
```

## Start and migrate

```bash
docker compose --env-file env -f compose.yml pull --policy never
docker compose --env-file env -f compose.yml run --rm api alembic upgrade head
docker compose --env-file env -f compose.yml up -d --no-build
docker compose --env-file env -f compose.yml ps
```

Run one API process with one worker per deployment unless you have added
external session/rate-limit stores. The execution service runs its own
operation worker; the separate worker process handles agent runs.

## Reverse proxy

The proxy must:

- terminate TLS on port 443 and forward the whole hostname, including OIDC
  discovery/callback and MCP OAuth paths;
- preserve `Host` and forwarding headers;
- enable WebSockets;
- disable response buffering for SSE routes;
- use a long read timeout (for example 3600 seconds) for streaming routes;
- restrict published upstream ports to the proxy and health checks at the
  host firewall/Docker forwarding layer.

Placeholders only:

```text
https://portal.example.com      → http://<application-host>:8080
https://mcp.example.com         → http://<application-host>:8083
https://auth.example.com        → http://<sso-host>:9000
```

Use the deployment's private DNS or host mappings for canonical HTTPS names.

## Resource budget

Approximate RAM for the full stack (the demo needs far less):

| Group | Budget |
|---|---:|
| PostgreSQL | 2 GiB |
| Authentik | 2 GiB |
| API/workers/execution/identity/MCP | 3 GiB |
| Open WebUI | 1.5 GiB |
| Embeddings (BAAI/bge-m3) | 3 GiB |
| Edge/console/monitoring | 1.5 GiB |
| OS/headroom | ~3 GiB |

Start with four concurrent model requests and a bounded queue. Apply
user/client rate limits, CloudStack request caps, and polling backoff. Run bulk
knowledge ingestion outside load tests.

## Backups and recovery

- Separate PostgreSQL databases/roles for the application, Authentik, and Open
  WebUI.
- Encrypt backups before they leave the server; store them at a restricted
  off-server destination.
- Retain seven daily and four weekly restore points.
- Include application databases, encrypted credential/token stores, recovery
  keys, private configuration, blueprints, deployment manifests, and branding.
- Restore into an isolated Compose project with cloud mutations and external
  connections disabled. Pending jobs must not execute during restoration.
- Targets: application RPO 24 hours, RTO one hour.
- Defaults: chats 90 days, operational logs 30 days, audit events one year,
  pricing/usage history two years.

Test restore procedures privately; do not publish live evidence.

## Upgrade preparation

Keep the CloudStack version pinned for the first delivery. Maintain private
baseline manifests, pre/post-upgrade tests, management/database backups,
versioned UI-patch reapplication, system-VM checks, and disposable workload
restore tests. An upgrade needs an explicit target version and coordinated
control-plane/storage recovery points.
