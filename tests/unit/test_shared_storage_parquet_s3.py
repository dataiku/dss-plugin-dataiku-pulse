from __future__ import annotations

from itertools import permutations
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

    class FakeParquetFile:
        def __init__(self, path, *, filesystem):
            self.schema_arrow = f"schema-{path}"

    class FakeDataset:
        def to_table(self):
            return FakeTable()

    def fake_dataset(paths, *, filesystem, format, schema):
        seen["paths"] = list(paths)
        seen["filesystem"] = filesystem
        seen["format"] = format
        seen["schema"] = schema
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
    monkeypatch.setitem(
        sys.modules,
        "pyarrow",
        SimpleNamespace(
            fs=SimpleNamespace(S3FileSystem=FakeS3FileSystem),
            dataset=SimpleNamespace(dataset=fake_dataset),
            parquet=SimpleNamespace(ParquetFile=FakeParquetFile),
            unify_schemas=lambda schemas: tuple(schemas),
        ),
    )

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
    assert seen["schema"] == (
        "schema-bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file1.parquet",
        "schema-bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/file2.parquet",
    )
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


def test_iter_s3_parquet_file_batches_splits_large_inputs_and_refreshes_context(monkeypatch):
    dataset_calls = []
    filesystem_kwargs = []
    contexts = [_Ctx(connection_type="EC2", marker="initial")]

    class FakeS3FileSystem:
        def __init__(self, **kwargs):
            filesystem_kwargs.append(kwargs)

    class FakeTable:
        def __init__(self, rows):
            self.rows = rows

        def to_pandas(self):
            return pd.DataFrame([{"row": self.rows}])

    class FakeParquetFile:
        def __init__(self, path, *, filesystem):
            self.schema_arrow = f"schema-{path}"

    class FakeDataset:
        def __init__(self, rows):
            self.rows = rows

        def to_table(self):
            return FakeTable(self.rows)

    def fake_dataset(paths, *, filesystem, format, schema):
        path_list = list(paths)
        dataset_calls.append(path_list)
        assert schema == tuple(f"schema-{path}" for path in path_list)
        return FakeDataset(len(path_list))

    def fake_resolve(ctx):
        return {
            "access_key": f"access-{ctx.marker}",
            "secret_key": f"secret-{ctx.marker}",
            "session_token": f"token-{ctx.marker}",
            "region_name": "us-east-1",
        }

    def context_factory():
        ctx = _Ctx(connection_type="EC2", marker=f"batch-{len(contexts)}")
        contexts.append(ctx)
        return ctx

    monkeypatch.setattr(parquet_s3, "resolve_aws_access", fake_resolve)
    monkeypatch.setitem(
        sys.modules,
        "pyarrow",
        SimpleNamespace(
            fs=SimpleNamespace(S3FileSystem=FakeS3FileSystem),
            dataset=SimpleNamespace(dataset=fake_dataset),
            parquet=SimpleNamespace(ParquetFile=FakeParquetFile),
            unify_schemas=lambda schemas: tuple(schemas),
        ),
    )

    paths = [f"bucket/root/file-{index}.parquet" for index in range(10_001)]
    batches = list(
        parquet_s3.iter_s3_parquet_file_batches(
            contexts[0],
            full_paths=paths,
            batch_size=5_000,
            storage_ctx_factory=context_factory,
        )
    )

    assert [batch.files_read for batch in batches] == [5_000, 5_000, 1]
    assert [len(call) for call in dataset_calls] == [5_000, 5_000, 1]
    assert paths not in dataset_calls
    assert [ctx.marker for ctx in contexts[1:]] == ["batch-1", "batch-2", "batch-3"]
    assert [kwargs["access_key"] for kwargs in filesystem_kwargs] == ["access-batch-1", "access-batch-2", "access-batch-3"]
    assert all("secret-batch" in kwargs["secret_key"] for kwargs in filesystem_kwargs)


def _read_local_parquet_files_with_s3_reader(monkeypatch, full_paths):
    import pyarrow.fs as pa_fs

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
    monkeypatch.setattr(pa_fs, "S3FileSystem", lambda **kwargs: pa_fs.LocalFileSystem())
    return parquet_s3.read_s3_parquet_file_batch(_Ctx(connection_type="EC2"), full_paths=full_paths)


def _write_parquet(path, schema, rows):
    import pyarrow as pa
    import pyarrow.parquet as pq

    columns = {
        field.name: pa.array([row.get(field.name) for row in rows], type=field.type)
        for field in schema
    }
    pq.write_table(pa.table(columns, schema=schema), path)


def _timestamp_epoch_ns_values(df: pd.DataFrame, column: str) -> list[int | None]:
    values = []
    for value in df[column].tolist():
        if pd.isna(value):
            values.append(None)
        else:
            values.append(pd.Timestamp(value).value)
    return values


def _write_timestamp_unit_fixture(path, *, unit, row_id, timestamp, date, run_ts):
    import pyarrow as pa

    schema = pa.schema([
        pa.field("row_id", pa.int64()),
        pa.field("timestamp", pa.timestamp(unit, tz="UTC")),
        pa.field("date", pa.timestamp(unit, tz="UTC")),
        pa.field("run_ts", pa.timestamp(unit, tz="UTC")),
        pa.field("label", pa.string()),
    ])
    row = {
        "row_id": row_id,
        "timestamp": timestamp,
        "date": date,
        "run_ts": run_ts,
        "label": f"row-{row_id}",
    }
    _write_parquet(path, schema, [row])


@pytest.mark.parametrize("conflicting_types", [("null", "string", "null"), ("null", "null", "string")])
def test_read_s3_parquet_file_batch_reconciles_null_string_schema_order_independently(
    monkeypatch,
    tmp_path,
    conflicting_types,
):
    import pyarrow as pa
    from pyarrow import dataset as pa_dataset

    type_by_name = {"null": pa.null(), "string": pa.string()}
    paths = []
    expected_rows = []
    for index, type_name in enumerate(conflicting_types):
        path = tmp_path / f"compact-part-{index}.parquet"
        conflict_value = None if type_name == "null" else f"project-{index}"
        timestamp_value = pd.Timestamp(f"2026-06-0{index + 1}T12:34:56Z")
        rows = [
            {
                "row_id": index,
                "conflicting_field": conflict_value,
                "unaffected_int": 100 + index,
                "unaffected_string": f"module-{index}",
                "event_ts": timestamp_value,
            }
        ]
        schema = pa.schema(
            [
                pa.field("row_id", pa.int64()),
                pa.field("conflicting_field", type_by_name[type_name]),
                pa.field("unaffected_int", pa.int64()),
                pa.field("unaffected_string", pa.string()),
                pa.field("event_ts", pa.timestamp("ms", tz="UTC")),
            ]
        )
        _write_parquet(path, schema, rows)
        paths.append(str(path))
        expected_rows.append(rows[0])

    for ordered_paths in permutations(paths):
        if pa_dataset.dataset(list(ordered_paths), format="parquet").schema.field("conflicting_field").type == pa.null():
            with pytest.raises(pa.ArrowNotImplementedError, match="Unsupported cast from string to null"):
                pa_dataset.dataset(list(ordered_paths), format="parquet").to_table()

        df = _read_local_parquet_files_with_s3_reader(monkeypatch, list(ordered_paths))

        assert list(df.columns) == ["row_id", "conflicting_field", "unaffected_int", "unaffected_string", "event_ts"]
        assert str(df["event_ts"].dtype) == "datetime64[ms, UTC]"
        expected_by_path = {
            path: expected_rows[paths.index(path)]
            for path in paths
        }
        expected_ordered = [expected_by_path[path] for path in ordered_paths]
        actual_rows = df.to_dict("records")
        assert actual_rows == expected_ordered


def test_read_s3_parquet_file_batch_reconciles_same_timezone_timestamp_units_order_independently(monkeypatch, tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import dataset as pa_dataset

    fixture_rows = [
        {
            "name": "ordinary-ns",
            "unit": "ns",
            "row_id": 1,
            "timestamp": pd.Timestamp("1969-12-31T23:59:59.123456789Z"),
            "date": pd.Timestamp("2026-10-20T00:00:00.000000123Z"),
            "run_ts": pd.Timestamp("2026-10-20T01:02:03.000000456Z"),
        },
        {
            "name": "compact-us",
            "unit": "us",
            "row_id": 2,
            "timestamp": pd.Timestamp("2026-10-20T01:02:03.123456Z"),
            "date": pd.Timestamp("2026-10-20T00:00:00Z"),
            "run_ts": pd.Timestamp("2026-10-20T01:02:04.123456Z"),
        },
        {
            "name": "compact-us-null",
            "unit": "us",
            "row_id": 3,
            "timestamp": None,
            "date": pd.Timestamp("2026-10-20T00:00:00.654321Z"),
            "run_ts": pd.Timestamp("2026-10-20T01:02:05.654321Z"),
        },
    ]
    paths = []
    expected_epoch_ns_by_path = {}
    for fixture in fixture_rows:
        path = tmp_path / f"{fixture['name']}.parquet"
        _write_timestamp_unit_fixture(path, unit=fixture["unit"], row_id=fixture["row_id"], timestamp=fixture["timestamp"], date=fixture["date"], run_ts=fixture["run_ts"])
        paths.append(str(path))
        expected_epoch_ns_by_path[str(path)] = {
            column: None if fixture[column] is None else pd.Timestamp(fixture[column]).value
            for column in ["timestamp", "date", "run_ts"]
        }

    with pytest.raises(pa.ArrowTypeError, match="timestamp"):
        pa.unify_schemas([pq.ParquetFile(path).schema_arrow for path in paths[:2]])
    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError), match="timestamp|would lose data"):
        pa_dataset.dataset([paths[1], paths[0]], format="parquet").to_table()

    for ordered_paths in permutations(paths):
        df = _read_local_parquet_files_with_s3_reader(monkeypatch, list(ordered_paths))

        assert list(df.columns) == ["row_id", "timestamp", "date", "run_ts", "label"]
        assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]"
        assert str(df["date"].dtype) == "datetime64[ns, UTC]"
        assert str(df["run_ts"].dtype) == "datetime64[ns, UTC]"
        assert df["label"].tolist() == [f"row-{fixture_rows[paths.index(path)]['row_id']}" for path in ordered_paths]
        for column in ["timestamp", "date", "run_ts"]:
            assert _timestamp_epoch_ns_values(df, column) == [expected_epoch_ns_by_path[path][column] for path in ordered_paths]


def test_read_s3_parquet_file_batch_preserves_timestamp_null_and_later_only_union(monkeypatch, tmp_path):
    import pyarrow as pa

    first = tmp_path / "all-null-timestamp.parquet"
    second = tmp_path / "later-ns-timestamp.parquet"
    _write_parquet(
        first,
        pa.schema([pa.field("row_id", pa.int64()), pa.field("timestamp", pa.null())]),
        [{"row_id": 1, "timestamp": None}],
    )
    _write_parquet(
        second,
        pa.schema([
            pa.field("row_id", pa.int64()),
            pa.field("timestamp", pa.timestamp("ns", tz="UTC")),
            pa.field("later_only", pa.timestamp("us", tz="UTC")),
        ]),
        [{"row_id": 2, "timestamp": pd.Timestamp("2026-10-20T01:02:03.000000789Z"), "later_only": pd.Timestamp("2026-10-20T01:02:03.123456Z")}],
    )

    df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second)])

    assert list(df.columns) == ["row_id", "timestamp", "later_only"]
    assert str(df["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert str(df["later_only"].dtype) == "datetime64[us, UTC]"
    assert _timestamp_epoch_ns_values(df, "timestamp") == [None, pd.Timestamp("2026-10-20T01:02:03.000000789Z").value]
    assert _timestamp_epoch_ns_values(df, "later_only") == [None, pd.Timestamp("2026-10-20T01:02:03.123456Z").value]


def test_read_s3_parquet_file_batch_keeps_identical_and_finest_necessary_timestamp_units(monkeypatch, tmp_path):
    first = tmp_path / "seconds.parquet"
    second = tmp_path / "millis.parquet"
    third = tmp_path / "micros.parquet"
    _write_timestamp_unit_fixture(first, unit="s", row_id=1, timestamp=pd.Timestamp("2026-10-20T01:02:03Z"), date=pd.Timestamp("2026-10-20T00:00:00Z"), run_ts=pd.Timestamp("2026-10-20T01:02:03Z"))
    _write_timestamp_unit_fixture(second, unit="ms", row_id=2, timestamp=pd.Timestamp("2026-10-20T01:02:03.123Z"), date=pd.Timestamp("2026-10-20T00:00:00.456Z"), run_ts=pd.Timestamp("2026-10-20T01:02:03.789Z"))
    _write_timestamp_unit_fixture(third, unit="us", row_id=3, timestamp=pd.Timestamp("2026-10-20T01:02:03.123456Z"), date=pd.Timestamp("2026-10-20T00:00:00.456789Z"), run_ts=pd.Timestamp("2026-10-20T01:02:03.789123Z"))

    single_df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(third)])
    combined_df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second), str(third)])

    assert str(single_df["timestamp"].dtype) == "datetime64[us, UTC]"
    assert str(combined_df["timestamp"].dtype) == "datetime64[us, UTC]"
    assert str(combined_df["date"].dtype) == "datetime64[us, UTC]"
    assert str(combined_df["run_ts"].dtype) == "datetime64[us, UTC]"
    assert _timestamp_epoch_ns_values(combined_df, "timestamp") == [
        pd.Timestamp("2026-10-20T01:02:03Z").value,
        pd.Timestamp("2026-10-20T01:02:03.123Z").value,
        pd.Timestamp("2026-10-20T01:02:03.123456Z").value,
    ]


@pytest.mark.parametrize("first_type,second_type", [("utc", "america"), ("utc", "naive")])
def test_read_s3_parquet_file_batch_rejects_timestamp_timezone_mismatches(monkeypatch, tmp_path, first_type, second_type):
    import pyarrow as pa

    type_by_name = {
        "utc": pa.timestamp("ns", tz="UTC"),
        "america": pa.timestamp("us", tz="America/New_York"),
        "naive": pa.timestamp("us"),
    }
    first = tmp_path / "timestamp-first.parquet"
    second = tmp_path / "timestamp-second.parquet"
    _write_parquet(first, pa.schema([pa.field("timestamp", type_by_name[first_type])]), [{"timestamp": pd.Timestamp("2026-10-20T01:02:03Z")}])
    _write_parquet(second, pa.schema([pa.field("timestamp", type_by_name[second_type])]), [{"timestamp": pd.Timestamp("2026-10-20T01:02:04Z")}])

    with pytest.raises(pa.ArrowTypeError, match="timestamp"):
        _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second)])


def test_read_s3_parquet_file_batch_surfaces_timestamp_unit_overflow(monkeypatch, tmp_path):
    import pyarrow as pa

    seconds = tmp_path / "seconds.parquet"
    nanos = tmp_path / "nanos.parquet"
    _write_parquet(seconds, pa.schema([pa.field("timestamp", pa.timestamp("s", tz="UTC"))]), [{"timestamp": pd.Timestamp("2500-01-01T00:00:00Z")}])
    _write_parquet(nanos, pa.schema([pa.field("timestamp", pa.timestamp("ns", tz="UTC"))]), [{"timestamp": pd.Timestamp("2026-10-20T01:02:03Z")}])

    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError, OverflowError), match="out of bounds|overflow|Casting|timestamp"):
        _read_local_parquet_files_with_s3_reader(monkeypatch, [str(seconds), str(nanos)])


def test_read_s3_parquet_file_batch_preserves_all_null_and_missing_fields(monkeypatch, tmp_path):
    import pyarrow as pa

    first = tmp_path / "compact-part-0.parquet"
    second = tmp_path / "compact-part-1.parquet"
    _write_parquet(
        first,
        pa.schema([pa.field("row_id", pa.int64()), pa.field("all_null", pa.null())]),
        [{"row_id": 1, "all_null": None}],
    )
    _write_parquet(
        second,
        pa.schema([
            pa.field("row_id", pa.int64()),
            pa.field("all_null", pa.null()),
            pa.field("later_only", pa.string()),
        ]),
        [{"row_id": 2, "all_null": None, "later_only": "present"}],
    )

    df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second)])

    assert list(df.columns) == ["row_id", "all_null", "later_only"]
    assert df.to_dict("records") == [
        {"row_id": 1, "all_null": None, "later_only": None},
        {"row_id": 2, "all_null": None, "later_only": "present"},
    ]


def test_read_s3_parquet_file_batch_preserves_single_and_identical_schema_reads(monkeypatch, tmp_path):
    import pyarrow as pa

    first = tmp_path / "part-0.parquet"
    second = tmp_path / "part-1.parquet"
    schema = pa.schema([
        pa.field("row_id", pa.int64()),
        pa.field("name", pa.string()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
    ])
    first_rows = [{"row_id": 1, "name": "alpha", "created_at": pd.Timestamp("2026-06-01T00:00:00Z")}]
    second_rows = [{"row_id": 2, "name": "beta", "created_at": pd.Timestamp("2026-06-02T00:00:00Z")}]
    _write_parquet(first, schema, first_rows)
    _write_parquet(second, schema, second_rows)

    single_df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first)])
    combined_df = _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second)])

    assert list(single_df.columns) == ["row_id", "name", "created_at"]
    assert str(single_df["created_at"].dtype) == "datetime64[us, UTC]"
    assert single_df.to_dict("records") == first_rows
    assert str(combined_df["created_at"].dtype) == "datetime64[us, UTC]"
    assert combined_df.to_dict("records") == first_rows + second_rows


def test_read_s3_parquet_file_batch_rejects_incompatible_concrete_types(monkeypatch, tmp_path):
    import pyarrow as pa

    first = tmp_path / "compact-part-0.parquet"
    second = tmp_path / "compact-part-1.parquet"
    _write_parquet(first, pa.schema([pa.field("value", pa.int64())]), [{"value": 1}])
    _write_parquet(second, pa.schema([pa.field("value", pa.string())]), [{"value": "one"}])

    with pytest.raises(pa.ArrowTypeError, match="value"):
        _read_local_parquet_files_with_s3_reader(monkeypatch, [str(first), str(second)])


def test_read_s3_parquet_file_batch_surfaces_schema_read_failure(monkeypatch, tmp_path):
    good = tmp_path / "compact-part-0.parquet"
    bad = tmp_path / "compact-part-1.parquet"
    good.write_bytes(b"not needed")
    bad.write_text("not a parquet file")

    with pytest.raises(Exception, match="Parquet"):
        _read_local_parquet_files_with_s3_reader(monkeypatch, [str(bad)])
