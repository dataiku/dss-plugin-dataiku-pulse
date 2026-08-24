from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

import shared_storage_discovery as discovery


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

    monkeypatch.setattr(discovery.boto3, "client", lambda *args, **kwargs: Client())
    ctx = _Ctx(connection_type="EC2", folder_root="root/nested", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object())
    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "KEYPAIR", "accessKey": "a", "secretKey": "b", "regionOrEndpoint": "us-east-1"}})
    ctx.folder_root = "root"

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

    monkeypatch.setattr(discovery, "_build_azure_blob_service_client", lambda ctx: ServiceClient())
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
    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "ENVIRONMENT"}})
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
    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "ENVIRONMENT"}})
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

    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "HMAC"}})
    monkeypatch.setattr(discovery, "resolve_gcs_hmac_credentials", lambda ctx: ("AKIAHMAC", "SECRET-HMAC"))
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
    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "UNKNOWN"}})
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

    monkeypatch.setattr(discovery.boto3, "client", lambda *args, **kwargs: Client())
    monkeypatch.setattr(discovery, "connection_info", lambda ctx, allow_cached=True: {"params": {"credentialsMode": "KEYPAIR", "accessKey": "a", "secretKey": "b", "regionOrEndpoint": "us-east-1"}})
    ctx = _Ctx(connection_type="EC2", folder_root="root", bucket_or_container="bucket", cached_connection_info={}, connection_name="c", connection_handle=object(), project_handle=object(), folder_handle=Folder())

    paths = list(discovery.iter_managed_folder_paths(ctx, relative_prefix="silver/category=event_mapping", suffix=".parquet"))
    assert paths == ["/silver/category=event_mapping/module=a/file1.parquet"]
