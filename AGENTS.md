# Contributor and agent rules

This is a **public** repository. Read these rules before changing or staging
anything. They apply to humans and to automated agents.

## Never add private material

Do not add or reference:

- real endpoints, IP addresses, hostnames, SSH destinations, or topology;
- CloudStack or LLM credentials, tokens, cookies, private keys, or session data;
- real CloudStack resource IDs, tenant/customer names, email addresses, or
  account names;
- real pricebook rows, rates, discounts, filenames, or golden values;
- workstation paths, private runbooks, evidence, screenshots, logs, or captured
  request recordings;
- the private execution plan or internal implementation notes.

Use `${VARIABLE}` placeholders, `example.com`, loopback for local development,
and RFC 5737 documentation addresses (`192.0.2.0/24`, `198.51.100.0/24`,
`203.0.113.0/24`) for address examples. Use the synthetic fixtures in `demo/`.

## Required checks

```bash
uv run ruff check src tests tools demo
uv run ruff format --check src tests tools demo
uv run mypy
uv run pytest -q
uv run python tools/privacy/scan.py --scope tree
```

For PostgreSQL-backed tests, provide `CONDUCTOR_TEST_DATABASE_URL` and run
`uv run pytest -m requires_db`.

Install the local hooks once per clone:

```bash
tools/privacy/install_hooks.sh
tools/privacy/install_gitleaks.sh
```

## Staging and publication

- Stage **explicit** public paths. Never `git add .`.
- Never use `git push --mirror`, `--no-verify`, or force-push public history.
- Commit with the configured GitHub noreply identity.
- Use the publication gate for owner publications; a failed or unavailable
  scanner blocks publication. See [docs/release.md](docs/release.md).
- Private operator state, manifests, denylists, and evidence live outside this
  repository (in a sibling private workspace).

## Design rules

- Authorization is deterministic. A language model may propose; it never
  approves, grants a scope, supplies a price, or injects a CloudStack ID.
- Every mutation is a typed action with `extra="forbid"` parameters and a
  human approval. Do not add arbitrary shell, SQL, URL, or credential tools.
- Compute and storage are priced separately. Money is `decimal.Decimal` on the
  server and a decimal string on the wire.
- Runtime branding is loaded from an allowlisted JSON document. Do not compile
  deployment identity into the portal bundle.
- New services must fail clearly when private configuration is missing; never
  fall back to a vendor environment or a mock mode silently.
- Keep audit output free of secrets. Log redaction is a safety net, not a
  license to log sensitive data.
- Add tests for security-relevant behavior, including negative cases in
  temporary repositories for scanner changes.

## Public CI

Public CI uses synthetic data and mock services only. Do not add deployment
credentials as GitHub Actions secrets, do not run untrusted pull-request code in
a privileged workflow, pin third-party actions to reviewed commit SHAs, and use
minimal workflow permissions.
