from __future__ import annotations

import importlib
import sys
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest

import shared_storage_discovery as discovery
import shared_storage_discovery_azure as discovery_azure
import shared_storage_discovery_gcs as discovery_gcs
import shared_storage_discovery_s3 as discovery_s3


class _Ctx(SimpleNamespace):
    pass


def test_importing_discovery_does_not_import_shared_duckdb_runtime():
    sys.modules.pop("shared_storage_discovery", None)
    sys.modules.pop("shared_duckdb", None)
    module = importlib.import_module("shared_storage_discovery")
    assert module is not None
    assert "shared_duckdb" not in sys.modules


def test_s3_paginated_prefix_listing_translates_to_relative_paths(monkeypatch):
    seen = {}

    class Paginator:
        def paginate(self, **kwargs):
            seen.update(kwargs)
            yield {
                "Contents": [
                    {"Key": "root/silver/category=event_mapping/module=a/file1.parquet"},
                    {"Key": "root/silver/category=event_mapping/module=a/file2.txt"},
                ]
            }
            yield {
                "Contents": [
                    {"Key": "root/silver/category=event_mapping/module=a/instance_name=x/year=2026/month=08/day=24/file3.parquet"},
                    {"Key": "otherroot/silver/category=event_mapping/module=a/file4.parquet"},
                    {"Key": "root/silver/category=event_mapping/module=a/subdir/"},
                ]
            }

    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

    monkeypatch.setattr(discovery_s3.boto3, "client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(
        discovery_s3,
        "resolve_aws_access",
        lambda ctx: {
            "access_key": "a",
            "secret_key": "b",
            "session_token": None,
            "region_name": "us-east-1",
        },
    )
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object())

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="/silver/category=event_mapping//", suffix=".parquet"))

    assert seen == {"Bucket": "bucket", "Prefix": "root/silver/category=event_mapping/"}
    assert paths == [
        "/silver/category=event_mapping/module=a/file1.parquet",
        "/silver/category=event_mapping/module=a/instance_name=x/year=2026/month=08/day=24/file3.parquet",
    ]


def test_azure_credential_selection_and_prefix_listing(monkeypatch):
    seen = {}

    class Blob:
        def __init__(self, name):
            self.name = name

    class ContainerClient:
        def list_blobs(self, name_starts_with=None):
            seen["prefix"] = name_starts_with
            return [Blob("root/silver/category=event_mapping/module=a/file1.parquet"), Blob("root/silver/category=event_mapping/module=a/subdir/")]

    class ServiceClient:
        def get_container_client(self, container):
            seen["container"] = container
            return ContainerClient()

    monkeypatch.setattr(discovery_azure, "_build_azure_blob_service_client", lambda ctx: ServiceClient())
    ctx = _Ctx(connection_type="Azure", folder_root="root", bucket_or_container="container", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object())

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {"container": "container", "prefix": "root/silver/category=event_mapping/"}
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_gcs_environment_requests_default_credentials_and_never_anonymous(monkeypatch):
    seen = {}

    class Blob:
        def __init__(self, name):
            self.name = name

    class Client:
        def __init__(self, project=None, credentials=None):
            seen["project"] = project
            seen["credentials"] = credentials

        def list_blobs(self, bucket, prefix=None):
            seen["bucket"] = bucket
            seen["prefix"] = prefix
            return [Blob("root/silver/category=event_mapping/module=a/file1.parquet")]

    fake_credentials = object()
    monkeypatch.setattr(discovery_gcs, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "ENVIRONMENT"}})
    monkeypatch.setitem(sys.modules, "google.auth", SimpleNamespace(default=lambda: (fake_credentials, "proj")))
    monkeypatch.setitem(sys.modules, "google.cloud", SimpleNamespace(storage=SimpleNamespace(Client=Client)))

    ctx = _Ctx(connection_type="GCS", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=SimpleNamespace(get_variables=lambda: {}))
    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {
        "project": "proj",
        "credentials": fake_credentials,
        "bucket": "bucket",
        "prefix": "root/silver/category=event_mapping/",
    }
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_gcs_environment_missing_default_credentials_fails_clearly(monkeypatch):
    monkeypatch.setattr(discovery_gcs, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "ENVIRONMENT"}})

    class MissingCreds(Exception):
        pass

    monkeypatch.setitem(sys.modules, "google.auth", SimpleNamespace(default=lambda: (_ for _ in ()).throw(MissingCreds("no creds"))))
    monkeypatch.setitem(sys.modules, "google.cloud", SimpleNamespace(storage=SimpleNamespace(Client=object)))
    ctx = _Ctx(connection_type="GCS", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=SimpleNamespace(get_variables=lambda: {}))
    with pytest.raises(MissingCreds):
        list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))


def test_gcs_hmac_uses_decrypted_established_hmac_and_is_recursive(monkeypatch):
    seen = {}

    class GCSFSClient:
        def __init__(self, access=None, secret=None):
            seen["access"] = access
            seen["secret"] = secret

        def find(self, path, detail=True):
            seen["path"] = path
            seen["detail"] = detail
            return {
                "a": {"name": "bucket/root/silver/category=event_mapping/module=a/file1.parquet"},
                "b": {"name": "bucket/root/silver/category=event_mapping/module=a/instance_name=x/year=2026/month=08/day=24/file2.parquet"},
                "c": {"name": "bucket/root/silver/category=event_mapping/module=a/instance_name=x/year=2026/month=08/day=24/file3.json"},
                "d": {"name": "bucket/root/silver/category=event_mapping/module=a/subdir/"},
            }

    monkeypatch.setattr(discovery_gcs, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "HMAC"}})
    monkeypatch.setattr(discovery_gcs, "resolve_gcs_hmac_credentials", lambda ctx: ("AKIAHMAC", "SECRET-HMAC"))
    monkeypatch.setitem(sys.modules, "gcsfs", SimpleNamespace(GCSFileSystem=GCSFSClient))
    ctx = _Ctx(connection_type="GCS", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=SimpleNamespace(get_variables=lambda: {}))

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {
        "access": "AKIAHMAC",
        "secret": "SECRET-HMAC",
        "path": "bucket/root/silver/category=event_mapping/",
        "detail": True,
    }
    assert paths == [
        "/silver/category=event_mapping/module=a/file1.parquet",
        "/silver/category=event_mapping/module=a/instance_name=x/year=2026/month=08/day=24/file2.parquet",
    ]


def test_unsupported_provider_and_modes_fail_clearly(monkeypatch):
    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        list(discovery.iter_managed_folder_paths(_Ctx(connection_type="LocalFS"), relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object())
    monkeypatch.setattr(discovery_s3, "resolve_aws_access", lambda ctx: (_ for _ in ()).throw(RuntimeError("Unsupported AWS credentials mode for managed-folder discovery: UNKNOWN")))
    with pytest.raises(RuntimeError, match="Unsupported AWS credentials mode"):
        list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))


def test_discovery_does_not_call_broad_dss_listing(monkeypatch):
    class Folder:
        def list_paths_in_partition(self, *args, **kwargs):
            raise AssertionError("broad listing should not be used")

        def list_contents(self, *args, **kwargs):
            raise AssertionError("broad listing should not be used")

    class Paginator:
        def paginate(self, **kwargs):
            yield {"Contents": [{"Key": "root/silver/category=event_mapping/module=a/file1.parquet"}]}

    class Client:
        def get_paginator(self, name):
            return Paginator()

    monkeypatch.setattr(discovery_s3.boto3, "client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(
        discovery_s3,
        "resolve_aws_access",
        lambda ctx: {
            "access_key": "a",
            "secret_key": "b",
            "session_token": None,
            "region_name": "us-east-1",
        },
    )
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object(), folder_handle=Folder())

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_count_api_counts_without_materializing_list(monkeypatch):
    seen = {"iterated": 0}

    def fake_iter(storage_ctx, *, relative_prefix, suffix=None):
        assert relative_prefix == "silver/category=event_mapping/"
        assert suffix == ".parquet"
        for index in range(7):
            seen["iterated"] += 1
            yield f"/silver/category=event_mapping/module=a/file-{index}.parquet"

    monkeypatch.setattr(discovery, "iter_managed_folder_paths", fake_iter)

    count = discovery.count_managed_folder_paths(
        _Ctx(connection_type="EC2"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
    )

    assert count == 7
    assert seen["iterated"] == 7


def test_two_day_selector_returns_exact_top_two_eligible_days_newest_first(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=22/source-b.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/source-recent.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=21/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/compact_silver-1786510805000-0001.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=20/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=22/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
        ]),
    )

    selected = discovery.select_latest_partition_paths_batch(
        _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        partition_filters={
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        partition_count=2,
        minimum_age_days=3,
        utc_today=date(2026, 8, 27),
    )

    assert selected.total_matched_paths == 8
    assert selected.filtered_matching_paths == 7
    assert selected.skipped_compact_outputs == 1
    assert selected.excluded_recent_paths == 1
    assert selected.eligible_paths == 6
    assert [(item.year, item.month, item.day) for item in selected.selected_partitions] == [
        ("2026", "08", "23"),
        ("2026", "08", "22"),
    ]
    assert selected.selected_partitions[0].relative_paths == [
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-b.parquet",
    ]
    assert selected.selected_partitions[1].full_paths == [
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=22/source-a.parquet",
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=22/source-b.parquet",
    ]


def test_selector_returns_fewer_available_eligible_days_without_failing(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/source-a.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/source-recent.parquet",
        ]),
    )

    selected = discovery.select_latest_partition_paths_batch(
        _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        partition_filters={
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        partition_count=7,
        minimum_age_days=3,
        utc_today=date(2026, 8, 27),
    )

    assert [(item.year, item.month, item.day) for item in selected.selected_partitions] == [("2026", "08", "23")]
    assert selected.eligible_paths == 1


def test_selector_still_fails_when_zero_eligible_days_exist(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/source-recent.parquet",
        ]),
    )

    with pytest.raises(ValueError, match="All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24"):
        discovery.select_latest_partition_paths_batch(
            _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "mazzei_pulse",
            },
            partition_count=7,
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
        )


def test_snapshot_api_sorts_and_reports_bounded_progress(monkeypatch):
    seen = []
    paths = [
        "/silver/category=event_mapping/module=b/file-2.parquet",
        "/silver/category=event_mapping/module=a/file-1.parquet",
        "/silver/category=event_mapping/module=c/file-3.parquet",
    ]

    monkeypatch.setattr(discovery, "iter_managed_folder_paths", lambda *args, **kwargs: iter(paths))

    snapshot = discovery.collect_managed_folder_snapshot(
        _Ctx(connection_type="EC2"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        progress_interval=2,
        progress_callback=seen.append,
    )

    assert snapshot == sorted(paths)
    assert seen == [2]


def test_build_managed_folder_path_index_returns_exact_clean_schema(monkeypatch):
    ctx = _Ctx(connection_type="EC2", folder_root="mazzzei-designer/dataiku/DATAIKU_PULSE_DASHBOARD/TggdpIiE", bucket_or_container="mazzei-dss-bucket")
    path = "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/audit_logs-history-1780699259547-2.parquet"
    monkeypatch.setattr(discovery, "iter_managed_folder_paths", lambda *args, **kwargs: iter([path]))

    df = discovery.build_managed_folder_path_index(ctx, relative_prefix="silver/category=event_mapping/", suffix=".parquet")

    assert list(df.columns) == discovery.PATH_INDEX_COLUMNS
    assert df.to_dict(orient="records") == [{
        "full_path": "mazzei-dss-bucket/mazzzei-designer/dataiku/DATAIKU_PULSE_DASHBOARD/TggdpIiE/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=04/day=24/audit_logs-history-1780699259547-2.parquet",
        "header_path": "mazzei-dss-bucket/mazzzei-designer/dataiku/DATAIKU_PULSE_DASHBOARD/TggdpIiE",
        "layer": "silver",
        "category": "event_mapping",
        "module": "administration",
        "instance_name": "mazzei_pulse",
        "year": "2026",
        "month": "04",
        "day": "24",
        "base_name": "audit_logs-history-1780699259547-2.parquet",
    }]


def test_build_managed_folder_path_index_rejects_malformed_paths(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter(["/silver/category=event_mapping/module=administration/bad-segment/year=2026/month=04/day=24/file.parquet"]),
    )

    with pytest.raises(ValueError, match="Unexpected managed-folder path format"):
        discovery.build_managed_folder_path_index(
            _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
        )


def test_filter_and_latest_day_selection_are_exact_and_numeric():
    df = pd.DataFrame(
        [
            {"full_path": "p1", "header_path": "h", "layer": "silver", "category": "event_mapping", "module": "administration", "instance_name": "mazzei_pulse", "year": "2026", "month": "04", "day": "09", "base_name": "a.parquet"},
            {"full_path": "p2", "header_path": "h", "layer": "silver", "category": "event_mapping", "module": "administration", "instance_name": "mazzei_pulse", "year": "2026", "month": "04", "day": "24", "base_name": "b.parquet"},
            {"full_path": "p3", "header_path": "h", "layer": "silver", "category": "event_mapping", "module": "administration", "instance_name": "other_instance", "year": "2027", "month": "12", "day": "31", "base_name": "c.parquet"},
        ],
        columns=discovery.PATH_INDEX_COLUMNS,
    )

    filtered = discovery.filter_path_index(
        df,
        category="event_mapping",
        module="administration",
        instance_name="mazzei_pulse",
    )
    selected = discovery.select_latest_partition_day(filtered)

    assert filtered["full_path"].tolist() == ["p1", "p2"]
    assert selected == ("2026", "04", "24")


def test_streaming_selector_applies_three_day_utc_guard_and_keeps_latest_eligible(monkeypatch):
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")
    paths = [
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/recent-24.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/eligible-23-b.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=27/recent-27.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=other/year=2026/month=08/day=22/ignored.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=26/recent-26.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=22/eligible-22.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=25/recent-25.parquet",
        "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/eligible-23-a.parquet",
    ]
    monkeypatch.setattr(discovery, "iter_managed_folder_paths", lambda *args, **kwargs: iter(paths))

    selected = discovery.select_latest_partition_paths(
        ctx,
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        partition_filters={
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        minimum_age_days=3,
        utc_today=date(2026, 8, 27),
    )

    assert selected.cutoff_date == date(2026, 8, 24)
    assert selected.total_matched_paths == 8
    assert selected.filtered_matching_paths == 7
    assert selected.excluded_recent_paths == 4
    assert selected.eligible_paths == 3
    assert (selected.year, selected.month, selected.day) == ("2026", "08", "23")
    assert selected.full_paths == [
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/eligible-23-a.parquet",
        "bucket/root/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/eligible-23-b.parquet",
    ]


def test_streaming_selector_default_clock_uses_utc_today(monkeypatch):
    class _FakeNow:
        def date(self):
            return date(2026, 8, 27)

    seen = {}

    def fake_now(tz):
        seen["tz"] = tz
        return _FakeNow()

    monkeypatch.setattr(discovery, "datetime", SimpleNamespace(now=fake_now))
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=23/eligible-23.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/recent-24.parquet",
        ]),
    )

    selected = discovery.select_latest_partition_paths(
        _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
        relative_prefix="silver/category=event_mapping/",
        suffix=".parquet",
        partition_filters={
            "category": "event_mapping",
            "module": "administration",
            "instance_name": "mazzei_pulse",
        },
        minimum_age_days=3,
    )

    assert seen["tz"] is discovery.timezone.utc
    assert selected.cutoff_date == date(2026, 8, 24)
    assert (selected.year, selected.month, selected.day) == ("2026", "08", "23")


def test_streaming_selector_all_recent_matches_fail_before_read(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=24/recent-24.parquet",
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=08/day=27/recent-27.parquet",
        ]),
    )

    with pytest.raises(ValueError, match="All exact-filter matches are excluded by minimum_age_days=3; cutoff_date=2026-08-24"):
        discovery.select_latest_partition_paths(
            _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "mazzei_pulse",
            },
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
        )


def test_streaming_selector_invalid_date_components_fail_clearly(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "iter_managed_folder_paths",
        lambda *args, **kwargs: iter([
            "/silver/category=event_mapping/module=administration/instance_name=mazzei_pulse/year=2026/month=13/day=40/bad-date.parquet",
        ]),
    )

    with pytest.raises(ValueError, match="invalid UTC calendar date"):
        discovery.select_latest_partition_paths(
            _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket"),
            relative_prefix="silver/category=event_mapping/",
            suffix=".parquet",
            partition_filters={
                "category": "event_mapping",
                "module": "administration",
                "instance_name": "mazzei_pulse",
            },
            minimum_age_days=3,
            utc_today=date(2026, 8, 27),
        )
