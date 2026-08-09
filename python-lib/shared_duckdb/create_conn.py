from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import duckdb

from .pathing import resolve_db_path


logger = logging.getLogger(__name__)

DUCKDB_MEMORY_PERCENTAGE = 0.90
_UNLIMITED_MEMORY_SENTINEL = 1 << 60


def _resolve_temp_directory() -> str:
    temp_dir = Path(tempfile.gettempdir()) / "pulse"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return str(temp_dir)


def _cgroup_memory_limit_bytes(
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    proc_cgroup: Path = Path("/proc/self/cgroup"),
) -> int:
    """Effective cgroup memory limit for *this* process, 0 if unlimited/unknown.

    The limit that matters is the one on the process's own cgroup (or any
    ancestor), not the root hierarchy: DSS runs jobs in nested cgroups like
    /DSS/workloads with their own memory.max while the root says "max".
    """

    limits: list[int] = []
    try:
        # cgroup v2: "0::/DSS/workloads/..." — check the process's own cgroup
        # and every ancestor up to the root.
        for line in proc_cgroup.read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            hierarchy, controllers, cg_path = parts
            rel = cg_path.lstrip("/")
            if hierarchy == "0" and controllers == "":  # v2 unified
                node = cgroup_root / rel
                limit_file = "memory.max"
            elif "memory" in controllers.split(","):  # v1 memory controller
                node = cgroup_root / "memory" / rel
                limit_file = "memory.limit_in_bytes"
            else:
                continue
            while True:
                try:
                    raw = (node / limit_file).read_text(encoding="utf-8").strip()
                    if raw != "max":
                        value = int(raw)
                        # v1 reports "unlimited" as a huge page-rounded number.
                        if 0 < value < 1 << 60:
                            limits.append(value)
                except OSError:
                    pass
                if node == cgroup_root or node.parent == node:
                    break
                node = node.parent
    except Exception:
        return 0
    return min(limits) if limits else 0


def _system_memory_limit_bytes() -> int:
    meminfo = Path("/proc/meminfo")
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if not line.startswith("MemTotal:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return 0
            value = int(parts[1]) * 1024
            return value if value > 0 else 0
    except (OSError, ValueError):
        return 0
    return 0


def _effective_memory_limit_bytes() -> tuple[int, str]:
    cgroup_limit = _cgroup_memory_limit_bytes()
    if 0 < cgroup_limit < _UNLIMITED_MEMORY_SENTINEL:
        cgroup_version = "cgroup_v2"
        try:
            for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
                parts = line.split(":", 2)
                if len(parts) != 3:
                    continue
                hierarchy, controllers, _cg_path = parts
                if hierarchy == "0" and controllers == "":
                    cgroup_version = "cgroup_v2"
                    break
                if "memory" in controllers.split(","):
                    cgroup_version = "cgroup_v1"
                    break
        except OSError:
            pass
        return cgroup_limit, cgroup_version

    system_limit = _system_memory_limit_bytes()
    if system_limit > 0:
        return system_limit, "system_fallback"

    return 0, "unknown"


def _duckdb_memory_limit_bytes(effective_memory_bytes: int) -> int:
    if effective_memory_bytes <= 0:
        return 0
    return max(1, int(effective_memory_bytes * DUCKDB_MEMORY_PERCENTAGE))


def _format_gib(memory_bytes: int) -> str | None:
    if memory_bytes <= 0:
        return None
    return f"{memory_bytes / (1024**3):.2f} GiB"


def _duckdb_memory_limit_setting(memory_bytes: int) -> str | None:
    if memory_bytes <= 0:
        return None
    return f"{memory_bytes / (1024**3):.2f}GB"


def _connect_config() -> dict[str, str]:
    cpu_count = os.cpu_count() or 2
    duckdb_threads = max(1, cpu_count - 1)

    effective_memory_bytes, memory_source = _effective_memory_limit_bytes()
    memory_limit_bytes = _duckdb_memory_limit_bytes(effective_memory_bytes)

    config: dict[str, str] = {
        "threads": str(duckdb_threads),
        "temp_directory": _resolve_temp_directory(),
        # Large sorts/COPYs buffer far less when insertion order is free.
        "preserve_insertion_order": "false",
    }
    if memory_limit_bytes > 0:
        config["memory_limit"] = _duckdb_memory_limit_setting(memory_limit_bytes)

    logger.info(
        "DuckDB memory configuration: effective_memory=%s memory_source=%s duckdb_percentage=%s duckdb_memory_limit=%s threads=%s temp_directory=%s",
        _format_gib(effective_memory_bytes),
        memory_source,
        int(DUCKDB_MEMORY_PERCENTAGE * 100),
        _format_gib(memory_limit_bytes),
        duckdb_threads,
        config["temp_directory"],
    )
    return config


def reset_duckdb(*, path: Path | None = None, project_key: str | None = None, purpose: str = "default") -> None:
    resolved = path or resolve_db_path(project_key=project_key, purpose=purpose)
    if resolved.exists():
        resolved.unlink()
    resolved.parent.mkdir(parents=True, exist_ok=True)


def create_connection(
    *,
    read_only: bool,
    path: Path | None = None,
    project_key: str | None = None,
    purpose: str = "default",
) -> duckdb.DuckDBPyConnection:
    resolved = path or resolve_db_path(project_key=project_key, purpose=purpose)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(resolved), read_only=read_only, config=_connect_config())
