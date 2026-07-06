"""Cgroup-aware memory limit detection (regression for the akaos OOM kill).

The process runs in a nested cgroup (e.g. /DSS/workloads) whose memory.max
is finite while the root hierarchy says "max". Reading only the root file
left DuckDB unlimited and the kernel OOM-killed the gold build.
"""

from shared_duckdb.create_conn import _cgroup_memory_limit_bytes

GIB = 1024**3


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_v2_nested_limit_found_when_root_is_max(tmp_path):
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_cgroup"
    _write(proc, "0::/DSS/workloads/akaos\n")
    _write(root / "DSS" / "workloads" / "akaos" / "memory.max", "max\n")
    _write(root / "DSS" / "workloads" / "memory.max", str(7 * GIB) + "\n")
    _write(root / "DSS" / "memory.max", "max\n")

    assert _cgroup_memory_limit_bytes(root, proc) == 7 * GIB


def test_v2_takes_min_of_ancestors(tmp_path):
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_cgroup"
    _write(proc, "0::/a/b\n")
    _write(root / "a" / "b" / "memory.max", str(4 * GIB) + "\n")
    _write(root / "a" / "memory.max", str(2 * GIB) + "\n")

    assert _cgroup_memory_limit_bytes(root, proc) == 2 * GIB


def test_v2_all_max_means_unlimited(tmp_path):
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_cgroup"
    _write(proc, "0::/a\n")
    _write(root / "a" / "memory.max", "max\n")

    assert _cgroup_memory_limit_bytes(root, proc) == 0


def test_v1_memory_controller(tmp_path):
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_cgroup"
    _write(proc, "4:memory:/dss/jobs\n")
    _write(root / "memory" / "dss" / "jobs" / "memory.limit_in_bytes", str(6 * GIB) + "\n")

    assert _cgroup_memory_limit_bytes(root, proc) == 6 * GIB


def test_v1_huge_sentinel_is_unlimited(tmp_path):
    root = tmp_path / "cgroup"
    proc = tmp_path / "proc_cgroup"
    _write(proc, "4:memory:/\n")
    _write(root / "memory" / "memory.limit_in_bytes", str(1 << 62) + "\n")

    assert _cgroup_memory_limit_bytes(root, proc) == 0


def test_missing_proc_file_is_safe(tmp_path):
    assert _cgroup_memory_limit_bytes(tmp_path / "cgroup", tmp_path / "nope") == 0
