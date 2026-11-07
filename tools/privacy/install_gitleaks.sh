#!/usr/bin/env bash
# Install a pinned Gitleaks binary for local publication checks.
# The binary and checksums are never committed to the public repository.
set -euo pipefail

VERSION="${GITLEAKS_VERSION:-8.30.1}"
DESTINATION="${1:-$(cd "$(dirname "$0")/../../../private/tools" 2>/dev/null && pwd || echo /tmp)}"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) PLATFORM="linux_x64" ;;
  aarch64|arm64) PLATFORM="linux_arm64" ;;
  *) echo "unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

mkdir -p "$DESTINATION"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

BASE="https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}"
echo "Downloading Gitleaks ${VERSION} (${PLATFORM})..." >&2
curl -fsSL --retry 3 --retry-delay 2 -o "$TMPDIR/gitleaks_${VERSION}_${PLATFORM}.tar.gz" "${BASE}/gitleaks_${VERSION}_${PLATFORM}.tar.gz"
curl -fsSL --retry 3 --retry-delay 2 -o "$TMPDIR/checksums.txt" "${BASE}/gitleaks_${VERSION}_checksums.txt"
(
  cd "$TMPDIR"
  grep "gitleaks_${VERSION}_${PLATFORM}.tar.gz" checksums.txt > selected.txt
  sha256sum -c selected.txt
)
tar -xzf "$TMPDIR/gitleaks_${VERSION}_${PLATFORM}.tar.gz" -C "$TMPDIR" gitleaks
install -m 0755 "$TMPDIR/gitleaks" "$DESTINATION/gitleaks"
"$DESTINATION/gitleaks" version
