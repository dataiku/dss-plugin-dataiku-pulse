from __future__ import annotations

from types import SimpleNamespace

import pytest

import shared_storage_discovery as discovery


class _Ctx(SimpleNamespace):
    pass


def test_s3_paginated_prefix_listing_translates_to_relative_paths(monkeypatch):
    seen = {}

    class Paginator:
        def paginate(self, **kwargs):
            seen.update(kwargs)
            yield {"Contents": [{"Key": "root/silver/category=event_mapping/module=a/file1.parquet"}]}
            yield {"Contents": [{"Key": "root/silver/category=event_mapping/module=a/dir/"}, {"Key": "root/silver/category=event_mapping/module=a/file2.txt"}]}

    class Client:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return Paginator()

    monkeypatch.setattr(discovery.boto3, "client", lambda *args, **kwargs: Client())
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")
    monkeypatch.setattr(discovery, "_connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "KEYPAIR", "accessKey": "a", "secretKey": "b", "regionOrEndpoint": "us-east-1"}})

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {"Bucket": "bucket", "Prefix": "root/silver/category=event_mapping/"}
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_azure_prefix_listing_translates_relative_paths(monkeypatch):
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

    monkeypatch.setattr(discovery, "_build_azure_blob_service_client", lambda ctx: ServiceClient())
    ctx = _Ctx(connection_type="Azure", folder_root="root", bucket_or_container="container")

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {"container": "container", "prefix": "root/silver/category=event_mapping/"}
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_gcs_prefix_listing_translates_relative_paths(monkeypatch):
    seen = {}

    class Blob:
        def __init__(self, name):
            self.name = name

    class Client:
        def list_blobs(self, bucket, prefix=None):
            seen["bucket"] = bucket
            seen["prefix"] = prefix
            return [Blob("root/silver/category=event_mapping/module=a/file1.parquet"), Blob("root/silver/category=event_mapping/module=a/file2.json")]

    monkeypatch.setattr(discovery, "_build_gcs_client", lambda ctx: ("google-cloud-storage", Client()))
    ctx = _Ctx(connection_type="GCS", folder_root="root", bucket_or_container="bucket")

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    assert seen == {"bucket": "bucket", "prefix": "root/silver/category=event_mapping/"}
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]


def test_nested_roots_and_suffix_filtering(monkeypatch):
    monkeypatch.setattr(
        discovery,
        "_iter_s3_paths",
        lambda ctx, relative_prefix, suffix: iter([
            "/silver/category=event_mapping/module=a/file1.parquet",
            "/silver/category=event_mapping/module=a/file2.txt",
        ]),
    )
    ctx = _Ctx(connection_type="EC2")
    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="/silver/category=event_mapping//", suffix=".parquet"))
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet", "/silver/category=event_mapping/module=a/file2.txt"]


def test_unsupported_provider_and_modes_fail_clearly(monkeypatch):
    with pytest.raises(RuntimeError, match="Unsupported managed-folder provider"):
        list(discovery.iter_managed_folder_paths(_Ctx(connection_type="LocalFS"), relative_prefix="silver/category=event_mapping", suffix=".parquet"))

    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket")
    monkeypatch.setattr(discovery, "_connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "UNKNOWN"}})
    with pytest.raises(RuntimeError, match="Unsupported AWS credentials mode"):
        list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))


def test_discovery_does_not_call_broad_dss_listing():
    class Folder:
        def list_paths_in_partition(self, *args, **kwargs):
            raise AssertionError("broad listing should not be used")

        def list_contents(self, *args, **kwargs):
            raise AssertionError("broad listing should not be used")

    folder = Folder()
    assert hasattr(folder, "list_paths_in_partition")
    assert hasattr(folder, "list_contents")
