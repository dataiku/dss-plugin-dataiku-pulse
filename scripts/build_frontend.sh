#!/usr/bin/env bash
# Build the dashboard React frontend and sync it into the plugin resources.
#
# Usage: bash scripts/build_frontend.sh
#
# Requires frontend/ to be populated with the React source (see
# frontend/README.md for the one-time vendoring step) and npm on PATH.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
BUILD_DIR="$FRONTEND_DIR/build"
TARGET_DIR="$REPO_ROOT/resource/pulse-dashboard/build"

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
    echo "ERROR: $FRONTEND_DIR is not populated with the React source." >&2
    echo "See frontend/README.md for the one-time vendoring step." >&2
    exit 1
fi

echo "==> npm ci"
(cd "$FRONTEND_DIR" && npm ci)

echo "==> npm run build"
(cd "$FRONTEND_DIR" && npm run build)

if [[ ! -d "$BUILD_DIR" ]]; then
    echo "ERROR: build output $BUILD_DIR not found (does the build script output elsewhere?)" >&2
    exit 1
fi

echo "==> sync build -> $TARGET_DIR"
mkdir -p "$TARGET_DIR"
rsync -a --delete "$BUILD_DIR/" "$TARGET_DIR/"

echo "Done. Commit the refreshed resource/pulse-dashboard/build/."
