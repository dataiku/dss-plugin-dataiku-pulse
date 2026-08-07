from __future__ import annotations

from pathlib import Path

import yaml

from data_collection.data_normalizer.flatten_config import _slug


def load_dev_toolbox_modules(base_dir: Path) -> list[str]:
    """Load development-activity modules from YAML.

    Expected file: gold_specs/dataiku_dev_tools/toolbox.yaml
    """

    path = base_dir / "dataiku_dev_tools" / "toolbox.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid dataiku_dev_tools toolbox.yaml (expected YAML list): {path}")

    out: list[str] = []
    seen: set[str] = set()
    for value in raw:
        if value is None:
            continue
        module_name = _slug(str(value))
        if not module_name or module_name in seen:
            continue
        seen.add(module_name)
        out.append(module_name)
    return out
