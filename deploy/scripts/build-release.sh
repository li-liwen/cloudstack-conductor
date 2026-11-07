#!/usr/bin/env bash
# Build a private release from an exact public commit and package it for the
# deployment server. Run from the public repository root after the owner's
# publication gate has passed. Never build from an unrestricted parent folder.
set -euo pipefail

COMMIT="${1:?usage: build-release.sh <commit> [output-directory]}"
OUTPUT="${2:-release-${COMMIT}}"
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

if ! git cat-file -e "${COMMIT}^{commit}" 2>/dev/null; then
  echo "commit not found: $COMMIT" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty; build from a clean export" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git archive --format=tar -o "$WORK/source.tar" "$COMMIT"
mkdir -p "$WORK/source"
tar -xf "$WORK/source.tar" -C "$WORK/source"

mkdir -p "$OUTPUT/images"
IMAGE="cloudstack-conductor:${COMMIT:0:12}"
EMBEDDING_IMAGE="cloudstack-conductor-embeddings:${COMMIT:0:12}"

docker build -f "$WORK/source/deploy/docker/Dockerfile" -t "$IMAGE" "$WORK/source"
docker build -f "$WORK/source/deploy/docker/Dockerfile.embeddings" -t "$EMBEDDING_IMAGE" "$WORK/source"

docker save "$IMAGE" -o "$OUTPUT/images/cloudstack-conductor.tar"
docker save "$EMBEDDING_IMAGE" -o "$OUTPUT/images/cloudstack-conductor-embeddings.tar"
docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE" > "$OUTPUT/image-digest.txt"
docker image inspect --format '{{index .RepoDigests 0}}' "$EMBEDDING_IMAGE" > "$OUTPUT/embeddings-image-digest.txt"

cp "$WORK/source/deploy/compose/compose.prod.yml" "$OUTPUT/compose.yml"
cp "$WORK/source/deploy/compose/env.example" "$OUTPUT/env.example"
cp -r "$WORK/source/migrations" "$OUTPUT/migrations"
cp "$WORK/source/README.md" "$OUTPUT/README.md"
printf '%s\n' "$COMMIT" > "$OUTPUT/COMMIT"
printf 'image=%s\nembeddings_image=%s\n' "$IMAGE" "$EMBEDDING_IMAGE" > "$OUTPUT/IMAGES"

(
  cd "$OUTPUT"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

echo "Release written to $OUTPUT"
echo "Transfer the release, private configuration, and branding separately."
