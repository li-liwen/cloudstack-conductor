#!/usr/bin/env bash
# Install the repository's hooks into its Git configuration.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
chmod +x .githooks/pre-commit .githooks/pre-push
git config core.hooksPath .githooks
echo "Installed hooks from .githooks (core.hooksPath=.githooks)."
