from __future__ import annotations

import os

import pandas as pd

from data_collection.audit_logs_modules.file_selection import select_candidate_audit_files


def _make_file(tmp_path, name: str, mtime_epoch: float):
    path = tmp_path / name
    path.write_text("x")
    os.utime(path, (mtime_epoch, mtime_epoch))
    return path


def _cursor(offset_hours: float = 0.0) -> pd.Timestamp:
    base = pd.Timestamp("2024-06-01T00:00:00Z")
    return base + pd.Timedelta(hours=offset_hours)


def test_fewer_eligible_than_max_files_includes_boundary_file(tmp_path):
    cursor = _cursor()
    cursor_epoch = cursor.timestamp()

    eligible = [
        _make_file(tmp_path, "e1.txt", cursor_epoch + 3600),
        _make_file(tmp_path, "e2.txt", cursor_epoch + 7200),
        _make_file(tmp_path, "e3.txt", cursor_epoch + 10800),
    ]
    boundary = _make_file(tmp_path, "boundary.txt", cursor_epoch - 3600)

    result = select_candidate_audit_files(
        eligible + [boundary], cursor_ts=cursor, max_files=5
    )

    assert set(result.paths) == set(eligible) | {boundary}
    assert len(result.paths) == 4
    assert result.excluded_eligible_count == 0
    assert result.oldest_excluded_eligible_mtime is None


def test_truncation_selects_newest_and_reports_excluded(tmp_path):
    cursor = _cursor()
    cursor_epoch = cursor.timestamp()

    files = [
        _make_file(tmp_path, f"f{i}.txt", cursor_epoch + i * 3600)
        for i in range(1, 9)  # 8 files newer than cursor, offsets +1h..+8h
    ]

    result = select_candidate_audit_files(files, cursor_ts=cursor, max_files=5)

    assert len(result.paths) == 5
    # The 5 newest are offsets +4h..+8h.
    expected_selected = {files[i] for i in range(3, 8)}  # index 3..7 -> +4h..+8h
    assert set(result.paths) == expected_selected

    assert result.excluded_eligible_count == 3
    excluded = files[:3]  # +1h, +2h, +3h
    oldest_excluded_epoch = min(p.stat().st_mtime for p in excluded)
    assert result.oldest_excluded_eligible_mtime.timestamp() == oldest_excluded_epoch


def test_all_files_older_than_cursor_selects_single_boundary_file(tmp_path):
    cursor = _cursor()
    cursor_epoch = cursor.timestamp()

    older_files = [
        _make_file(tmp_path, "o1.txt", cursor_epoch - 3 * 3600),
        _make_file(tmp_path, "o2.txt", cursor_epoch - 2 * 3600),
        _make_file(tmp_path, "o3.txt", cursor_epoch - 1 * 3600),  # newest of the old ones
    ]

    result = select_candidate_audit_files(older_files, cursor_ts=cursor, max_files=5)

    assert len(result.paths) == 1
    assert result.paths[0] == older_files[2]
    assert result.excluded_eligible_count == 0


def test_empty_file_list_returns_empty_selection():
    cursor = _cursor()

    result = select_candidate_audit_files([], cursor_ts=cursor, max_files=5)

    assert result.paths == []
    assert result.available_count == 0
    assert result.excluded_eligible_count == 0
    assert result.oldest_selected_mtime is None
    assert result.newest_selected_mtime is None
    assert result.oldest_excluded_eligible_mtime is None
