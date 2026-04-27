#!/usr/bin/env bash
set -euo pipefail

# Promote the dev-built Pulse dashboard frontend into the plugin.
#
# This script supports the workflow:
# - Develop & test the React app under `dataiku-pulse.extras/webapps/entry_point/frontend/`
# - Build it to produce `.../frontend/build/`
# - Sync the resulting build into the plugin `resource/` folder so DSS Visual Webapps
#   use the updated compiled assets.
#
# Usage:
#   scripts/update_pulse_webapp_from_extras.sh
#   scripts/update_pulse_webapp_from_extras.sh --build
#
# Environment overrides:
#   EXTRAS_ENTRYPOINT_DIR  Path to `.../dataiku-pulse.extras/webapps/entry_point`
#   EXTRAS_BUILD_DIR       Path to React `build/` directory

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

EXTRAS_ENTRYPOINT_DIR=${EXTRAS_ENTRYPOINT_DIR:-"/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/webapps/entry_point"}
EXTRAS_BUILD_DIR=${EXTRAS_BUILD_DIR:-"$EXTRAS_ENTRYPOINT_DIR/frontend/build"}

DO_BUILD=0
if [[ ${1:-} == "--build" ]]; then
  DO_BUILD=1
elif [[ ${1:-} != "" ]]; then
  echo "ERROR: unknown argument: ${1}" >&2
  echo "Usage: scripts/update_pulse_webapp_from_extras.sh [--build]" >&2
  exit 2
fi

if [[ ! -d "$EXTRAS_ENTRYPOINT_DIR" ]]; then
  echo "ERROR: extras entrypoint directory not found: $EXTRAS_ENTRYPOINT_DIR" >&2
  exit 2
fi

if [[ $DO_BUILD -eq 1 ]]; then
  BUILD_SCRIPT="$EXTRAS_ENTRYPOINT_DIR/scripts/build_frontend.sh"
  if [[ ! -f "$BUILD_SCRIPT" ]]; then
    echo "ERROR: build script not found: $BUILD_SCRIPT" >&2
    exit 2
  fi

  echo "Building frontend in: $EXTRAS_ENTRYPOINT_DIR" >&2
  bash "$BUILD_SCRIPT"
fi

if [[ ! -d "$EXTRAS_BUILD_DIR" ]]; then
  echo "ERROR: React build directory not found: $EXTRAS_BUILD_DIR" >&2
  echo "Hint: run this with --build or build the React app first." >&2
  exit 2
fi

echo "Syncing build into plugin resources..." >&2
bash "$REPO_ROOT/scripts/sync_pulse_dashboard_build.sh" "$EXTRAS_BUILD_DIR"

echo "" >&2
echo "Next steps:" >&2
echo "  git status" >&2
echo "  git add resource/pulse-dashboard/build" >&2
echo "  git commit -m \"Update: refresh Pulse dashboard frontend build\"" >&2
