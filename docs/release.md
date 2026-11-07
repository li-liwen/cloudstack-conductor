# Release and publication

The owner's publication procedure is local and mandatory. Public CI is an extra
check, not a substitute for the local gate.

## One-time setup

```bash
tools/privacy/install_gitleaks.sh
tools/privacy/install_hooks.sh
```

Create a private supplemental denylist **outside** the repository, for example
a sibling `private/privacy-denylist.txt`, containing deployment-specific
identifiers and known private values. Keep it mode `0600`. Do not put those
values in the public scanner configuration.

Set `CONDUCTOR_PRIVATE_DENYLIST` if the file is not at the default sibling path.

## Check before committing

```bash
uv run python tools/publication_gate.py --check-only
```

This verifies the repository root, origin, the public/private separation, the
scanner, Gitleaks, the staged index, the working tree, and the complete reachable
history. A missing denylist or unavailable scanner blocks the check.

## Publish an explicit set of paths

```bash
uv run python tools/publication_gate.py \
  --paths README.md src deploy docs tests \
  --message "Describe the generic change" \
  --branch main \
  --remote origin \
  --push
```

The gate:

1. verifies the repository root and approved GitHub origin;
2. stages only the explicit public paths and rejects anything staged outside
   them;
3. scans the staged index with the privacy scanner and Gitleaks;
4. verifies the configured GitHub noreply commit identity and scans the message;
5. commits without `--no-verify`;
6. scans all history, objects, metadata, and tags;
7. exports the exact commit to a clean temporary directory;
8. installs dependencies, runs the public tests, and builds the portal there;
9. scans generated distributable files;
10. writes a private manifest containing the commit, scanner versions, test
    results, and artifact hashes;
11. pushes only the explicit commit to the explicit branch.

`--push` requires `CONDUCTOR_APPROVED_REMOTE` to exactly match the origin URL.
The gate never uses `--mirror`, never pushes all branches/tags, never uploads
`.git`, and never uses `--no-verify`.

## Forbidden practices

- Broad staging (`git add .`) or uploading whole-workspace archives.
- Blanket scanner allowlists.
- Fixing a leak after pushing.
- Force-pushing public history as a substitute for incident handling.
- Publishing private deployment bundles, live screenshots, logs, or backups.

## If sensitive data entered an unpublished commit

Remove it from every affected unpublished commit, then rescan. Deleting it in a
later commit or reverting does not remove the earlier object.

## If exposure occurred

Stop further publication, notify the owner privately, and remediate the exposed
credentials/data. Do not silently force-push as a substitute for incident
handling.

## Public CI

Public CI runs on GitHub-hosted runners with synthetic data and mock services.
It uses minimal permissions, pins third-party actions to reviewed commit SHAs,
does not connect to the private cloud or local model, and never receives
deployment credentials. It runs privacy checks, tests, type checks, and a clean
build. Artifacts come only from synthetic/public inputs.

The initial publication scope is source code and public documentation.
Application images are shipped privately to the deployment server and are not
automatically published as GitHub releases.
