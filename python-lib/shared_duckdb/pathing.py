from __future__ import annotations

import getpass
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import uuid


DB_PATH_DEFAULT = Path(tempfile.gettempdir()) / "duckdb" / "pulse.duckdb"
DB_PATH_ENV = "PULSE_DUCKDB_PATH"
DB_DIR_ENV = "PULSE_DUCKDB_DIR"

_SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_filename(value: str) -> str:
    cleaned = _SAFE_CHARS_RE.sub("_", value).strip("_")
    return cleaned or "default"


def resolve_db_path(*, project_key: str | None = None, purpose: str = "default") -> Path:
    explicit = os.environ.get(DB_PATH_ENV)
    if explicit:
        return Path(explicit)

    base_dir = Path(os.environ.get(DB_DIR_ENV, str(DB_PATH_DEFAULT.parent)))
    stem = DB_PATH_DEFAULT.stem
    suffix = DB_PATH_DEFAULT.suffix

    name_parts = [stem, safe_filename(purpose)]
    if project_key:
        name_parts.append(safe_filename(project_key))

    return base_dir / ("_".join(name_parts) + suffix)


def resolve_unique_db_path(*, project_key: str | None = None, purpose: str = "default") -> Path:
    explicit = os.environ.get(DB_PATH_ENV)
    if explicit:
        return Path(explicit)

    base_dir = Path(os.environ.get(DB_DIR_ENV, str(DB_PATH_DEFAULT.parent)))
    stem = DB_PATH_DEFAULT.stem
    suffix = DB_PATH_DEFAULT.suffix

    user_name = os.environ.get("DKU_CURRENT_USER") or getpass.getuser()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = uuid.uuid4().hex[:8]

    name_parts = [stem, safe_filename(purpose)]
    if project_key:
        name_parts.append(safe_filename(project_key))
    if user_name:
        name_parts.append(safe_filename(user_name))
    name_parts.extend([ts, token])

    return base_dir / ("_".join(name_parts) + suffix)
