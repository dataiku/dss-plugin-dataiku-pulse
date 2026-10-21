import boto3
import dataiku


def _folder_s3_source(folder, *, api, project):
    """Resolve location and connection credentials without printing secrets."""
    access = folder.get_info().get("accessInfo") or {}
    bucket = access.get("bucket")

    if not bucket or "root" not in access:
        raise ValueError(
            "Selected folder must expose S3 accessInfo.bucket and root"
        )

    root = str(access["root"] or "").strip("/")

    settings = (
        project.get_managed_folder(folder.get_id())
        .get_settings()
        .get_raw()
    )

    connection = (settings.get("params") or {}).get("connection")
    if not connection:
        connection = (
            (settings.get("settings") or {}).get("params") or {}
        ).get("connection")

    if not connection:
        raise ValueError(
            "Selected folder's DSS connection could not be resolved"
        )

    info = api.get_connection(connection).get_info()

    if info.get("type") != "EC2":
        raise ValueError(
            "Selected folder must use a DSS S3 (EC2) connection"
        )

    params = info.get("params") or {}
    mode = params.get("credentialsMode")

    if mode == "KEYPAIR":
        credentials = {
            "aws_access_key_id": params["accessKey"],
            "aws_secret_access_key": params["secretKey"],
        }
    elif mode in {"STS_ASSUME_ROLE", "ENVIRONMENT"}:
        resolved = info.get("resolvedAWSCredential") or {}
        credentials = {
            "aws_access_key_id": resolved["accessKey"],
            "aws_secret_access_key": resolved["secretKey"],
            "aws_session_token": resolved["sessionToken"],
        }
    else:
        raise ValueError(
            f"Unsupported DSS S3 credentials mode: {mode}"
        )

    client = boto3.client(
        "s3",
        region_name=params.get("regionOrEndpoint") or None,
        **credentials,
    )

    return client, bucket, root


def do(payload, config, plugin_config, inputs):
    primary = plugin_config.get("pulse_primary") or {}
    project_key = str(primary.get("pulse_project_key") or "").strip()

    if not project_key:
        raise ValueError(
            "Missing pulse_primary.pulse_project_key in plugin_config"
        )

    api = dataiku.api_client()
    project = api.get_project(project_key)

    folder = dataiku.Folder(
        lookup="partitioned_data",
        project_key=project_key,
        ignore_flow=True,
    )

    client, bucket, root = _folder_s3_source(
        folder,
        api=api,
        project=project,
    )

    silver_prefix = "/".join(
        part for part in (root, "silver") if part
    ) + "/"

    categories = set()

    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket,
        Prefix=silver_prefix,
        Delimiter="/",
    ):
        for entry in page.get("CommonPrefixes", []):
            category_folder = entry["Prefix"][
                len(silver_prefix):
            ].rstrip("/")

            if category_folder.startswith("category="):
                category = category_folder.split("=", 1)[1]
                if category:
                    categories.add(category)

    return {
        "choices": [
            {"value": category, "label": category}
            for category in sorted(categories)
        ]
    }