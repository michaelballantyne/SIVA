#!/usr/bin/env bash
# Build the SIVA container image.
#
# Runs from anywhere; the build context is the repo root (the Dockerfile COPYs
# pyproject.toml + siva/ from there). Override the tag with SIVA_IMAGE.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
IMAGE="${SIVA_IMAGE:-siva:latest}"

echo "Building $IMAGE (context: $REPO_ROOT)…"
DOCKER_BUILDKIT=1 docker build -f "$HERE/Dockerfile" -t "$IMAGE" "$REPO_ROOT"
echo "Done. Next: ./run.sh <workspace-dir> [data-file-or-dir]"
