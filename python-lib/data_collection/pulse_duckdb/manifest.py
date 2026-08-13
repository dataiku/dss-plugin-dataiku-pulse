from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
import logging

import dataiku

logger = logging.getLogger(__name__)

MANIFEST_PATH = "gold/_state/manifest.json"


def read_manifest(folder_lookup: str) -> dict[str, object]:
    folder = dataiku.Folder(folder_lookup)
    try:
        with folder.get_download_stream(MANIFEST_PATH) as stream:
            payload = json.loads(stream.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        logger.info("No existing GOLD manifest found at %s", MANIFEST_PATH)
        return {}


def write_manifest(folder_lookup: str, manifest: dict[str, object]) -> None:
    folder = dataiku.Folder(folder_lookup)
    content = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
    folder.upload_stream(MANIFEST_PATH, io.BytesIO(content))


def manifest_watermark(manifest: dict[str, object], key: str) -> str | None:
    watermarks = manifest.get("watermarks")
    if not isinstance(watermarks, dict):
        return None
    value = watermarks.get(key)
    return str(value) if value else None


def set_manifest_watermark(manifest: dict[str, object], key: str, value: str | None) -> None:
    if not value:
        return
    watermarks = manifest.setdefault("watermarks", {})
    if isinstance(watermarks, dict):
        watermarks[key] = value


def lookback_adjusted_watermark(watermark: str | None, lookback_days: int) -> str | None:
    if not watermark or lookback_days <= 0:
        return watermark
    try:
        normalized = watermark.replace("Z", "+00:00")
        adjusted = datetime.fromisoformat(normalized) - timedelta(days=lookback_days)
        return adjusted.isoformat()
    except Exception:
        logger.warning("Failed to parse watermark %s for lookback adjustment", watermark)
        return watermark


def stamp_manifest_updated_at(manifest: dict[str, object]) -> None:
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
