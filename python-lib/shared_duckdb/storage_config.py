from __future__ import annotations

import base64
import logging
from pathlib import Path

# cryptography is imported lazily inside the decrypt helpers: it is only
# needed for the GCS HMAC path, and keeping it out of module import keeps
# `shared_duckdb` importable in light environments (tests, validators).

from .context import StorageContext
from .extensions import duckdb_version, ensure_provider_extensions
from . import yaml_loader


logger = logging.getLogger(__name__)


def _load_queries() -> dict:
    return yaml_loader.load_yaml(Path(__file__).with_name("blob_credentials.yaml"))


def aws_credentials(ctx: StorageContext) -> str:
    info = ctx.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("credentialsMode")
    aws_region = params.get("regionOrEndpoint")

    queries = _load_queries()

    if credentials_mode == "KEYPAIR":
        return yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_static"],
            key_id=params["accessKey"],
            secret=params["secretKey"],
            aws_region=aws_region,
        )

    if credentials_mode in {"STS_ASSUME_ROLE", "ENVIRONMENT"}:
        resolved = info["resolvedAWSCredential"]
        return yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_assume"],
            key_id=resolved["accessKey"],
            secret=resolved["secretKey"],
            token=resolved["sessionToken"],
            aws_region=aws_region,
        )

    raise RuntimeError(f"Unsupported AWS credentials mode: {credentials_mode}")


def azure_credentials(ctx: StorageContext) -> str:
    info = ctx.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("authType")
    storage_account = params.get("storageAccount")

    queries = _load_queries()

    if credentials_mode == "SHARED_KEY":
        return yaml_loader.render_query(
            queries["blob_setup"]["azure_access_key_headers"],
            account_name=params["storageAccount"],
            access_key=params["accessKey"],
        )

    if credentials_mode == "OAUTH2_APP":
        return yaml_loader.render_query(
            queries["blob_setup"]["azure_oauth2_headers"],
            tenant_id=params["tenantId"],
            client_id=params["appId"],
            client_secret=params["appSecret"],
            account_name=storage_account,
        )

    raise RuntimeError(f"Unsupported Azure authentication type: {credentials_mode}")


def configure_gcs_filesystem(conn) -> None:
    from fsspec import filesystem

    conn.register_filesystem(filesystem("gcs"))


def derive_key_from_password(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def decrypt_string(ciphertext: bytes, password: str, salt: bytes) -> str:
    from cryptography.fernet import Fernet

    return Fernet(derive_key_from_password(password, salt)).decrypt(ciphertext).decode()


def gcp_credentials(ctx: StorageContext):
    variables = ctx.project_handle.get_variables()
    gcs_hmac = (variables.get("local") or {}).get("gcs_hmac")
    if not gcs_hmac:
        return None, None

    try:
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        access_key = gcs_hmac["access_key"]
        hmac_secret = decrypt_string(ciphertext, password="DF2!&sEkm)f4}i99,e&9bS:Wj", salt=salt)
    except Exception:
        return None, None

    queries = _load_queries()
    return yaml_loader.render_query(
        queries["blob_setup"]["gcp_headers"],
        hmac_id=access_key,
        hmac_secret=hmac_secret,
    )


def _render_azure_shared_key_queries(ctx: StorageContext) -> list[tuple[str, str]]:
    info = ctx.connection_handle.get_info()
    params = info.get("params", {})
    queries = _load_queries()
    account_name = params["storageAccount"]
    access_key = params["accessKey"]
    return [
        (
            "provider_config_access_key",
            yaml_loader.render_query(
                queries["blob_setup"]["azure_access_key_headers"],
                account_name=account_name,
                access_key=access_key,
            ),
        ),
        (
            "connection_string",
            yaml_loader.render_query(
                queries["blob_setup"]["azure_connection_string_headers"],
                account_name=account_name,
                access_key=access_key,
            ),
        ),
    ]


def _configure_azure_shared_key(conn, ctx: StorageContext) -> dict:
    errors: list[str] = []
    for variant_name, query in _render_azure_shared_key_queries(ctx):
        try:
            conn.execute(query)
            logger.info("Configured Azure DuckDB secret using variant=%s", variant_name)
            return {
                "provider": "Azure",
                "credential_mode": "SHARED_KEY",
                "secret_variant": variant_name,
            }
        except Exception as exc:
            logger.info(
                "Azure shared-key secret creation failed for variant=%s duckdb_version=%s",
                variant_name,
                duckdb_version(),
                exc_info=True,
            )
            errors.append(f"{variant_name}: {exc}")

    raise RuntimeError(
        "Azure shared-key secret creation failed "
        f"for duckdb_version={duckdb_version()} after trying variants: {' | '.join(errors)}"
    )


def refresh_storage_credentials(conn, *, ctx: StorageContext) -> bool:
    """Re-create the DuckDB storage secret with freshly resolved credentials.

    DSS hands out temporary STS tokens for ENVIRONMENT / STS_ASSUME_ROLE
    connections; re-fetching connection info returns a rotated token once the
    previous one nears expiry, and the secret templates are CREATE OR REPLACE.

    Returns True when a secret was refreshed, False when the provider has no
    refreshable secret (e.g. GCS in filesystem mode).
    """

    ctype = ctx.connection_type
    if ctype == "EC2":
        conn.execute(aws_credentials(ctx))
        return True
    if ctype == "Azure":
        credential_mode = (ctx.connection_handle.get_info().get("params") or {}).get("authType")
        if credential_mode == "SHARED_KEY":
            _configure_azure_shared_key(conn, ctx)
        else:
            conn.execute(azure_credentials(ctx))
        return True
    return False


def configure_storage(conn, *, ctx: StorageContext) -> dict:
    ctype = ctx.connection_type
    extension_source = ensure_provider_extensions(conn, ctype)
    if extension_source:
        logger.info(
            "DuckDB extensions ready for provider=%s sources=%s",
            ctype,
            extension_source,
        )

    if ctype == "EC2":
        credential_mode = (ctx.connection_handle.get_info().get("params") or {}).get("credentialsMode")
        blob_credentials = aws_credentials(ctx)
        conn.execute(blob_credentials)
        return {"provider": ctype, "credential_mode": credential_mode, "extension_source": extension_source}
    elif ctype == "Azure":
        credential_mode = (ctx.connection_handle.get_info().get("params") or {}).get("authType")
        if credential_mode == "SHARED_KEY":
            storage_info = _configure_azure_shared_key(conn, ctx)
            storage_info["extension_source"] = extension_source
            return storage_info
        blob_credentials = azure_credentials(ctx)
        conn.execute(blob_credentials)
        return {"provider": ctype, "credential_mode": credential_mode, "extension_source": extension_source}
    elif ctype == "GCS":
        blob_credentials = gcp_credentials(ctx)
        if not blob_credentials:
            configure_gcs_filesystem(conn)
            return {
                "provider": "GCS",
                "credential_mode": "filesystem",
                "extension_source": extension_source,
            }
        conn.execute(blob_credentials)
        return {"provider": "GCS", "credential_mode": "hmac", "extension_source": extension_source}
    else:
        raise ValueError(f"Unknown storage provider: {ctype}")
