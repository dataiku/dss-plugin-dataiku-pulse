from __future__ import annotations

from pathlib import Path

from shared_duckdb import create_conn as cc


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_cgroup_v2_finite_memory_limit(tmp_path):
    cgroup_root = tmp_path / "sys_fs_cgroup"
    proc_cgroup = tmp_path / "proc_self_cgroup"
    _write(proc_cgroup, "0::/dss/workloads/job\n")
    _write(cgroup_root / "dss/workloads/job/memory.max", str(8 * 1024**3))

    assert cc._cgroup_memory_limit_bytes(cgroup_root=cgroup_root, proc_cgroup=proc_cgroup) == 8 * 1024**3  # nosec B101


def test_cgroup_v1_finite_memory_limit(tmp_path):
    cgroup_root = tmp_path / "sys_fs_cgroup"
    proc_cgroup = tmp_path / "proc_self_cgroup"
    _write(proc_cgroup, "5:memory:/docker/abc\n")
    _write(cgroup_root / "memory/docker/abc/memory.limit_in_bytes", str(6 * 1024**3))

    assert cc._cgroup_memory_limit_bytes(cgroup_root=cgroup_root, proc_cgroup=proc_cgroup) == 6 * 1024**3  # nosec B101


def test_cgroup_v2_max_returns_zero(tmp_path):
    cgroup_root = tmp_path / "sys_fs_cgroup"
    proc_cgroup = tmp_path / "proc_self_cgroup"
    _write(proc_cgroup, "0::/dss/workloads/job\n")
    _write(cgroup_root / "dss/workloads/job/memory.max", "max")

    assert cc._cgroup_memory_limit_bytes(cgroup_root=cgroup_root, proc_cgroup=proc_cgroup) == 0  # nosec B101


def test_invalid_cgroup_contents_return_zero(tmp_path):
    cgroup_root = tmp_path / "sys_fs_cgroup"
    proc_cgroup = tmp_path / "proc_self_cgroup"
    _write(proc_cgroup, "0::/dss/workloads/job\n")
    _write(cgroup_root / "dss/workloads/job/memory.max", "not-a-number")

    assert cc._cgroup_memory_limit_bytes(cgroup_root=cgroup_root, proc_cgroup=proc_cgroup) == 0  # nosec B101


def test_unlimited_sentinel_returns_zero(tmp_path):
    cgroup_root = tmp_path / "sys_fs_cgroup"
    proc_cgroup = tmp_path / "proc_self_cgroup"
    _write(proc_cgroup, "5:memory:/docker/abc\n")
    _write(cgroup_root / "memory/docker/abc/memory.limit_in_bytes", str((1 << 60) + 4096))

    assert cc._cgroup_memory_limit_bytes(cgroup_root=cgroup_root, proc_cgroup=proc_cgroup) == 0  # nosec B101


def test_effective_memory_falls_back_to_system(monkeypatch):
    monkeypatch.setattr(cc, "_cgroup_memory_limit_bytes", lambda: 0)
    monkeypatch.setattr(cc, "_system_memory_limit_bytes", lambda: 10 * 1024**3)

    assert cc._effective_memory_limit_bytes() == (10 * 1024**3, "system_fallback")  # nosec B101


def test_duckdb_memory_limit_uses_ninety_percent():
    effective = 15 * 1024**3
    assert cc._duckdb_memory_limit_bytes(effective) == int(effective * 0.80)  # nosec B101


def test_duckdb_memory_limit_setting_preserves_precision():
    effective = 15 * 1024**3
    limit = cc._duckdb_memory_limit_bytes(effective)
    assert cc._duckdb_memory_limit_setting(limit) == "12288MiB"  # nosec B101


def test_effective_memory_reports_cgroup_source(monkeypatch):
    monkeypatch.setattr(cc, "_cgroup_memory_limit_bytes", lambda: 12 * 1024**3)
    assert cc._effective_memory_limit_bytes() == (12 * 1024**3, "cgroup_v2")  # nosec B101
