# ADR 0003: Publication safety as a prerequisite

## Context

The project began inside a private operator workspace containing real
infrastructure notes, credentials, and a pricebook. Git ignore rules do not
remove already tracked or historical content, and GitHub scanning does not
cover internal addresses or private business data.

## Decision

- Keep the public repository in a separate child directory and never initialize
  Git in the private workspace.
- Use three layers: private inputs outside the repository, local scans before
  every commit/push, and GitHub protections/public CI as defense in depth.
- Complement Gitleaks with a privacy scanner for internal addresses, hostnames,
  workstation paths, private identifiers, binaries, unsafe symlinks, and commit
  metadata.
- Maintain a private supplemental denylist outside the repository.
- Make one publication gate the owner's procedure: verify root/origin, stage
  explicit paths, scan, commit, scan history, clean-export build/test, scan
  outputs, write a private manifest, push only the explicit ref.
- Fail closed when a scanner, private policy, or build is unavailable.

## Consequences

Publication is slower and deliberate. Private data in any unpublished commit
must be rewritten before publication. Public CI cannot replace the local gate or
receive deployment credentials. Scanner reports identify rules and locations,
not matched values, so they can be discussed safely.
