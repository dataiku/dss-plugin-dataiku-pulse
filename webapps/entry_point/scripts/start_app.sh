#!/bin/bash
# 1. Resolve Pulse code environment dynamically
# The active env path is written by the workspace-managed plugin env bootstrap.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
REPO_LOCAL_ENV_PATH_FILE="${REPO_ROOT}/.local/plugin_env_path.txt"
LEGACY_ENV_PATH_FILE="/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt"
ENV_PATH_FILE=${PULSE_ENV_PATH_FILE:-}
if [[ -z "$ENV_PATH_FILE" ]]; then
  if [[ -f "$REPO_LOCAL_ENV_PATH_FILE" ]]; then
    ENV_PATH_FILE="$REPO_LOCAL_ENV_PATH_FILE"
  else
    ENV_PATH_FILE="$LEGACY_ENV_PATH_FILE"
  fi
fi
if [[ ! -f "$ENV_PATH_FILE" ]]; then
  echo "ERROR: Missing env path file: $ENV_PATH_FILE" >&2
  echo "Fallback order: PULSE_ENV_PATH_FILE -> ${REPO_ROOT}/.local/plugin_env_path.txt -> ${LEGACY_ENV_PATH_FILE}" >&2
  exit 2
fi

VENV_DIR="$(cat "$ENV_PATH_FILE" | tr -d ' \t\r\n')"
if [[ -z "$VENV_DIR" ]]; then
  echo "ERROR: Empty env path file: $ENV_PATH_FILE" >&2
  exit 2
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# 2. Navigate to your app (relative to this script)
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP_DIR" || exit 1

# 3. Start the server
HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8995}

# Avoid blocking on stdout/stderr pipes by logging to files.
# (In some Code Studio launch contexts, `--access-logfile -` can cause requests
# to hang once the pipe buffer fills.)
LOG_DIR=${LOG_DIR:-/tmp/pulse}
ACCESS_LOGFILE=${ACCESS_LOGFILE:-"$LOG_DIR/gunicorn-access.log"}
ERROR_LOGFILE=${ERROR_LOGFILE:-"$LOG_DIR/gunicorn-error.log"}
mkdir -p "$LOG_DIR"

exec python -m gunicorn \
  --bind "$HOST:$PORT" \
  --log-level debug \
  --access-logfile "$ACCESS_LOGFILE" \
  --error-logfile "$ERROR_LOGFILE" \
  --capture-output \
  --reload \
  app:app
