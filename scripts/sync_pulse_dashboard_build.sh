#!/usr/bin/env bash
set -euo pipefail

# Copy a pre-built React frontend into the plugin `resource/` folder.
#
# Usage:
#   scripts/sync_pulse_dashboard_build.sh /abs/path/to/react/app/build
#
# Example:
#   scripts/sync_pulse_dashboard_build.sh \
#     /home/dataiku/workspace/project-lib-resources/OLD/webapps/DEMO/frontend/build

SRC_BUILD_DIR=${1:-}
if [[ -z "${SRC_BUILD_DIR}" ]]; then
  echo "ERROR: missing source build directory argument" >&2
  exit 2
fi

if [[ ! -d "${SRC_BUILD_DIR}" ]]; then
  echo "ERROR: build directory not found: ${SRC_BUILD_DIR}" >&2
  exit 2
fi

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST_DIR="${REPO_ROOT}/resource/pulse-dashboard/build"

mkdir -p "${DEST_DIR}"

# Remove previous build output (keeps the plugin lightweight and avoids stale assets)
rm -rf "${DEST_DIR}"/*

# Copy build output
cp -a "${SRC_BUILD_DIR}/." "${DEST_DIR}/"

echo "Synced build to: ${DEST_DIR}"
