#!/usr/bin/env bash
# Verify a deployment's runtime integration assets before enabling them.
# Usage: verify.sh <base-url-of-injected-assets> <expected-marker>
set -euo pipefail
BASE="${1:?usage: verify.sh <base-url> [marker]}"
MARKER="${2:-CONDUCTOR_UI_CONFIG}"

for asset in config.js sso-entry.js console-launch.js; do
  code="$(curl -fsS -o "/tmp/conductor-ui-${asset}" -w '%{http_code}' "${BASE%/}/${asset}")"
  if [[ "$code" != "200" ]]; then
    echo "missing asset: $asset (HTTP $code)" >&2
    exit 1
  fi
done
if ! grep -q "$MARKER" /tmp/conductor-ui-config.js; then
  echo "configuration marker not found" >&2
  exit 1
fi
if ! grep -q "data-conductor-sso-entry" /tmp/conductor-ui-sso-entry.js; then
  echo "SSO entry marker not found" >&2
  exit 1
fi
if ! grep -q "data-conductor-console" /tmp/conductor-ui-console-launch.js; then
  echo "console marker not found" >&2
  exit 1
fi
rm -f /tmp/conductor-ui-*.js
echo "CloudStack UI integration assets verified."
