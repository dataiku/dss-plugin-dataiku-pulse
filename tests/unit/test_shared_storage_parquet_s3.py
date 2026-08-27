from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

import shared_storage_parquet_s3 as parquet_s3


class _Ctx(SimpleNamespace):
    pass


def test_read_s3_parquet_files_uses_resolved_credentials_and_reports_metrics(monkeypatch):
    seen = {}

    class FakeS3FileSystem:
        def __init__(self, **kwargs):
            seen["filesystem_kwargs"] = kwargs

    class FakeTable:
        def to_pandas(self):
            return pd.DataFrame([
                {"a": 1, "b": "x"},
                {"a": 1, "b": "x"},
                {"a": 2, "b": "y"},
            ])

    class FakeDataset:
        def to_table(self):
            return FakeTable()

    def fake_dataset(paths, *, filesystem, format):
        seen["paths"] = list(paths)
        seen["filesystem"] = filesystem
        seen["format"] = format
        return FakeDataset()

    monkeypatch.setattr(
        parquet_s3,
        "resolve_aws_access",
        lambda ctx: {
            "access_key": "AKIA...",
            "secret_key": "SECRET...",
            "session_token": "TOKEN...",
            "region_name": "us-east-1",
        },
    )
    monkeypatch.setitem(sys.modules, "pyarrow.fs", SimpleNamespace(S3FileSystem=FakeS3FileSystem))
    monkeypatch.setitem(sys.modules, "pyarrow.dataset", SimpleNamespace(dataset=fake_dataset))

    df = parquet_s3.read_s3_parquet_files(
        _Ctx(connection_type="EC2"),
        full_paths=[
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file1.parquet",
            "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file2.parquet",
        ],
    )

    assert seen["paths"] == [
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file1.parquet",
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file2.parquet",
    ]
    assert seen["format"] == "parquet"
    assert seen["filesystem_kwargs"] == {
        "access_key": "AKIA...",
        "secret_key": "SECRET...",
        "session_token": "TOKEN...",
        "region": "us-east-1",
    }
    assert len(df) == 2
    assert list(df.columns) == ["a", "b"]
    assert df.attrs == {
        "files_read": 2,
        "raw_rows": 3,
        "rows_after_drop_duplicates": 2,
        "output_column_count": 2,
    }


def test_read_s3_parquet_files_rejects_non_s3_provider():
    with pytest.raises(RuntimeError, match="only supports EC2"):
        parquet_s3.read_s3_parquet_files(_Ctx(connection_type="Azure"), full_paths=["bucket/root/file.parquet"])


def test_read_s3_parquet_files_requires_at_least_one_path():
    with pytest.raises(ValueError, match="at least one selected source path"):
        parquet_s3.read_s3_parquet_files(_Ctx(connection_type="EC2"), full_paths=[])
