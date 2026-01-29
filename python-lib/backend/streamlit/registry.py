import logging
from pathlib import Path
import importlib.util

logger = logging.getLogger(__name__)


SUPPORTED_TYPES = {
    "metric": "metrics",
    "graph": "graphs",
    "dataframe": "dataframes",
}

def load_analytics(root: str):
    root = Path(root)

    analytics = {
        "metrics": {},
        "graphs": {},
        "dataframes": {},
    }

    for path in root.rglob("*.py"):
        # --- create a unique module name ---
        module_name = "analytics_" + "_".join(path.with_suffix("").parts)

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue  # skip unreadable modules safely

        module = importlib.util.module_from_spec(spec)

        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load {path}: {e}")
            continue

        # --- validate META ---
        if not hasattr(module, "META"):
            continue

        meta = module.META

        if not isinstance(meta, dict):
            continue

        if "id" not in meta or "type" not in meta:
            continue

        analytic_type = meta["type"]
        bucket = SUPPORTED_TYPES.get(analytic_type)

        if bucket is None:
            logger.warning(f"⚠️ Unsupported analytic type '{analytic_type}' in {path}")
            continue

        analytics[bucket][meta["id"]] = {
            "meta": meta,
            "module": module,
            "path": path,
        }

    return analytics
