from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb

from .pathing import resolve_db_path


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


def _connect_config() -> dict[str, str]:
    cpu_count = os.cpu_count() or 2
    duckdb_threads = max(1, cpu_count - 1)

    memory_limit_bytes = _cgroup_memory_limit_bytes()

    config: dict[str, str] = {
        "threads": str(duckdb_threads),
        "temp_directory": _resolve_temp_directory(),
        # Large sorts/COPYs buffer far less when insertion order is free.
        "preserve_insertion_order": "false",
    }
    if memory_limit_bytes > 0:
        # 50%, not 80%: DuckDB overshoots its memory_limit on partitioned
        # parquet COPY (untracked writer/compression buffers), and the cgroup
        # limit is shared with every other local DSS process. An 80% cap let
        # the gold build reach ~7GB anon RSS inside a 7.5GB cgroup → OOM kill.
        memory_limit_gib = max(1, int((memory_limit_bytes * 0.5) / (1024**3)))
        config["memory_limit"] = f"{memory_limit_gib}GB"
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
