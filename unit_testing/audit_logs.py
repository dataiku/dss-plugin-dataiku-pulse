from __future__ import annotations

import json
from pathlib import Path
import sys

# Make this repo importable for local testing.
# In DSS plugins, this is handled by the plugin runtime.
_BASE_DIR = Path(__file__).resolve().parents[1]

_PYTHON_LIB_DIR = _BASE_DIR / "python-lib"
if _PYTHON_LIB_DIR.exists() and str(_PYTHON_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_LIB_DIR))

_RUNNABLE_DIR = _BASE_DIR / "python-runnables" / "data-gather-audit-logs"
if str(_RUNNABLE_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNNABLE_DIR))

from runnable import MyRunnable  # noqa: E402


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    # Simulate a macro run using the same runnable entrypoint.
    inputs_dir = Path("/home/dataiku/workspace/project-lib-versioned/python/runnable_inputs")
    config_path = inputs_dir / "config.json"
    plugin_config_path = inputs_dir / "plugin_config.json"

    config = _load_json(config_path)
    plugin_config = _load_json(plugin_config_path)

    # Force local DSS mode (disable hub/spoke remote uploads).
    plugin_config["pulse_project_url"] = None
    plugin_config["pulse_project_api"] = None

    # Force audit debug mode so we read from `python/audit_data/`.
    plugin_config["pulse_audit_logs_debug"] = True

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
