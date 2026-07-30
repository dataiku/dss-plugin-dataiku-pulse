#!/usr/bin/env bash
set -euo pipefail

# Copy a pre-built React frontend into the plugin `resource/` folder.
#
# Usage:
#   scripts/webapp/sync_pulse_dashboard_build.sh /abs/path/to/react/app/build
#
# Example:
#   scripts/webapp/sync_pulse_dashboard_build.sh \
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

DEFAULT_PLUGIN_DIR="/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse"
PLUGIN_DIR=${PLUGIN_DIR:-"${DEFAULT_PLUGIN_DIR}"}
DEST_DIR="${PLUGIN_DIR}/resource/pulse-dashboard/build"

if [[ ! -d "${PLUGIN_DIR}" ]]; then
  echo "ERROR: plugin directory not found: ${PLUGIN_DIR}" >&2
  exit 2
fi

mkdir -p "${DEST_DIR}"

# Remove previous build output (keeps the plugin lightweight and avoids stale assets)
rm -rf "${DEST_DIR}"/*

# Copy build output
cp -a "${SRC_BUILD_DIR}/." "${DEST_DIR}/"

echo "Synced build to: ${DEST_DIR}"
