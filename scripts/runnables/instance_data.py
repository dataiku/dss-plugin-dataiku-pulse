from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import warnings

from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter("ignore", InsecureRequestWarning)


def _default_env_path_file() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    repo_local = repo_root / ".local" / "plugin_env_path.txt"
    if repo_local.is_file():
        return str(repo_local)
    return "/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/plugin_env_path.txt"

def _maybe_reexec_with_pulse_env() -> None:
    """Re-exec this script with the Pulse plugin env if configured."""

    env_path_file = os.getenv(
        "PULSE_ENV_PATH_FILE",
        _default_env_path_file(),
    )

    try:
        venv_dir = Path(env_path_file).read_text(encoding="utf-8").strip()
    except OSError:
        return

    if not venv_dir:
        return

    venv_python = str(Path(venv_dir) / "bin" / "python")

    if os.path.realpath(sys.executable) == os.path.realpath(venv_python):
        return

    os.execv(venv_python, [venv_python, *sys.argv])


_maybe_reexec_with_pulse_env()


# Make this repo importable for local testing.
# In DSS plugins, this is handled by the plugin runtime.
_SCRIPT_ROOT = Path(__file__).resolve().parents[2]
_BASE_DIR_CANDIDATES = [
    _SCRIPT_ROOT.parent / "dataiku-pulse",
    _SCRIPT_ROOT / "dss-plugin-dataiku-pulse",
    _SCRIPT_ROOT,
]
_BASE_DIR = next((p for p in _BASE_DIR_CANDIDATES if (p / "python-lib").exists() and (p / "python-runnables").exists()), _SCRIPT_ROOT)

_PYTHON_LIB_DIR = _BASE_DIR / "python-lib"
if _PYTHON_LIB_DIR.exists() and str(_PYTHON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_LIB_DIR))

_RUNNABLE_DIR = _BASE_DIR / "python-runnables" / "data-gather-instance"
if str(_RUNNABLE_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNABLE_DIR))

from runnable import MyRunnable  # noqa: E402


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    # Simulate a macro run using the same runnable entrypoint.
    # Config inputs are stored in a workspace-managed location for easy swapping.
    default_inputs_dirs = [
        Path("/home/dataiku/workspace/project-lib-versioned/python/dataiku-pulse.extras/runnable-configs"),
        # Backward-compat (older workspace layout)
        Path("/home/dataiku/workspace/project-lib-versioned/python/runnable_inputs"),
    ]

    inputs_dir_env = os.getenv("PULSE_RUNNABLE_CONFIGS_DIR")
    if inputs_dir_env:
        default_inputs_dirs.insert(0, Path(inputs_dir_env))

    inputs_dir: Path | None = None
    for candidate_dir in default_inputs_dirs:
        if (candidate_dir / "config.json").exists() and (candidate_dir / "plugin_config.json").exists():
            inputs_dir = candidate_dir
            break

    if inputs_dir is None:
        raise FileNotFoundError(
            "Could not find runnable configs (config.json, plugin_config.json). Tried: "
            + ", ".join(str(p) for p in default_inputs_dirs)
        )

    config_path = inputs_dir / "config.json"
    plugin_config_path = inputs_dir / "plugin_config.json"

    config = _load_json(config_path)
    plugin_config = _load_json(plugin_config_path)

    # Local test: keep using the remote target from runnable_inputs.

    runnable = MyRunnable(project_key="DATA_COLLECTION", config=config, plugin_config=plugin_config)

    def progress(p: int) -> None:
        print(f"progress={p}")

    result = runnable.run(progress)

    # ResultTable pretty-print for local testing
    if hasattr(result, "columns") and hasattr(result, "records"):
        headers = [c.get("displayName") for c in result.columns]
        print("\t".join(headers))
        for r in result.records:
            print("\t".join(map(str, r)))
    else:
        print(result)


if __name__ == "__main__":
    main()
