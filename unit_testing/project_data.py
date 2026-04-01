from __future__ import annotations

import json
import os
from pathlib import Path

import sys

# Make `python-lib/` and `python-runnables/` importable for local testing.
# In DSS plugins, this is handled by the plugin runtime.
_BASE_DIR = Path(__file__).resolve().parents[1]

_PYTHON_LIB_DIR = _BASE_DIR / "python-lib"
for _p in [_PYTHON_LIB_DIR, _PYTHON_LIB_DIR / "data_collection"]:
    if _p.exists() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_PROJECT_DATA_DIR = _BASE_DIR / "python-runnables" / "data-gather-project"
if str(_PROJECT_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DATA_DIR))

from runnable import MyRunnable


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    base_dir = Path(__file__).resolve().parents[1]

    # Simulate a macro run using the same runnable entrypoint.
    # Inputs live outside this repo for easy swapping.
    inputs_dir = Path("/home/dataiku/workspace/project-lib-versioned/python/runnable_inputs")
    config_path = inputs_dir / "config.json"
    plugin_config_path = inputs_dir / "plugin_config.json"

    config = _load_json(config_path)
    plugin_config = _load_json(plugin_config_path)


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
