#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Run the Pulse dashboard backend locally (prod-like).

Usage:
  bash scripts/webapp/run_backend.sh

Environment variables:
  PLUGIN_DIR           Path to the plugin repo root (defaults to this workspace)
  PULSE_ENV_PATH_FILE  Pointer file containing venv path (1 line, absolute path)

Common overrides:
  HOST, PORT, TIMEOUT, LOG_DIR, ACCESS_LOGFILE, ERROR_LOGFILE, LOG_LEVEL, RELOAD,
  WORKERS, THREADS

Notes:
  - Backend entrypoint: webapps/pulse-dashboard/backend.py (gunicorn app: backend:app)
  - No --dev/--prod: this always runs the plugin webapp backend.
EOF
}

if [[ ${1:-} == "-h" || ${1:-} == "--help" ]]; then
  usage
  exit 0
elif [[ ${1:-} != "" ]]; then
  echo "ERROR: unknown argument: ${1}" >&2
  usage >&2
  exit 2
fi

PLUGIN_DIR=${PLUGIN_DIR:-"/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse"}
APP_DIR="${PLUGIN_DIR}/webapps/pulse-dashboard"
GUNICORN_APP="backend:app"

REPO_LOCAL_ENV_PATH_FILE="${PLUGIN_DIR}/.local/plugin_env_path.txt"
LEGACY_ENV_PATH_FILE="/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt"
ENV_PATH_FILE=${PULSE_ENV_PATH_FILE:-}
if [[ -z "${ENV_PATH_FILE}" ]]; then
  if [[ -f "${REPO_LOCAL_ENV_PATH_FILE}" ]]; then
    ENV_PATH_FILE="${REPO_LOCAL_ENV_PATH_FILE}"
  else
    ENV_PATH_FILE="${LEGACY_ENV_PATH_FILE}"
  fi
fi

if [[ ! -d "${PLUGIN_DIR}" ]]; then
  echo "ERROR: PLUGIN_DIR not found: ${PLUGIN_DIR}" >&2
  exit 2
fi

if [[ ! -d "${APP_DIR}" ]]; then
  echo "ERROR: App directory not found: ${APP_DIR}" >&2
  exit 2
fi

if [[ ! -f "${ENV_PATH_FILE}" ]]; then
  echo "ERROR: Missing env path file: ${ENV_PATH_FILE}" >&2
  echo "Looked for pointer file at: ${ENV_PATH_FILE}" >&2
  echo "Fallback order: PULSE_ENV_PATH_FILE -> ${PLUGIN_DIR}/.local/plugin_env_path.txt -> ${LEGACY_ENV_PATH_FILE}" >&2
  exit 2
fi

VENV_DIR=$(tr -d ' \t\r\n' <"${ENV_PATH_FILE}")
if [[ -z "${VENV_DIR}" ]]; then
  echo "ERROR: Empty env path file: ${ENV_PATH_FILE}" >&2
  exit 2
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "ERROR: Invalid venv dir (missing bin/python): ${VENV_DIR}" >&2
  exit 2
fi

# Make plugin python-lib importable for local runs.
PYTHONLIB_DIR="${PLUGIN_DIR}/python-lib"
if [[ -d "${PYTHONLIB_DIR}" ]]; then
  export PYTHONPATH="${PYTHONLIB_DIR}:${PYTHONPATH:-}"
fi

cd "${APP_DIR}"

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8995}
# DuckDB reloads can take a while locally; use a generous default timeout.
TIMEOUT=${TIMEOUT:-300}
LOG_LEVEL=${LOG_LEVEL:-debug}
RELOAD=${RELOAD:-0}
WORKERS=${WORKERS:-1}
THREADS=${THREADS:-4}

# Avoid blocking on stdout/stderr pipes by logging to files.
LOG_DIR=${LOG_DIR:-/tmp/pulse}
ACCESS_LOGFILE=${ACCESS_LOGFILE:-"${LOG_DIR}/gunicorn-access.log"}
ERROR_LOGFILE=${ERROR_LOGFILE:-"${LOG_DIR}/gunicorn-error.log"}
mkdir -p "${LOG_DIR}"

GUNICORN_ARGS=(
  --bind "${HOST}:${PORT}"
  --timeout "${TIMEOUT}"
  --workers "${WORKERS}"
  --threads "${THREADS}"
  --log-level "${LOG_LEVEL}"
  --access-logfile "${ACCESS_LOGFILE}"
  --error-logfile "${ERROR_LOGFILE}"
  --capture-output
)

if [[ "${RELOAD}" == "1" ]]; then
  GUNICORN_ARGS+=(--reload)
fi

exec "${VENV_DIR}/bin/python" -m gunicorn "${GUNICORN_ARGS[@]}" "${GUNICORN_APP}"