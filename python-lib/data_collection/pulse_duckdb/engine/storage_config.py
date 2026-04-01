from __future__ import annotations

import base64
import logging

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from ..context import StorageContext
from ..helpers import yaml_loader


logger = logging.getLogger(__name__)


def _load_queries(ctx: StorageContext) -> dict:
    from ..constants import BASE_DIR

    return yaml_loader.load_yaml(BASE_DIR / "config/blob/blob_credentials.yaml")


def aws_credentials(ctx: StorageContext) -> tuple[str, str]:
    info = ctx.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("credentialsMode")
    aws_region = params.get("regionOrEndpoint")

    queries = _load_queries(ctx)
    blob_module = queries["blob_setup"]["aws_modules"]

    if credentials_mode == "KEYPAIR":
        key_id = params["accessKey"]
        secret = params["secretKey"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_static"],
            key_id=key_id,
            secret=secret,
            aws_region=aws_region,
        )
        return blob_module, blob_credentials

    if credentials_mode in {"STS_ASSUME_ROLE", "ENVIRONMENT"}:
        resolved = info["resolvedAWSCredential"]
        key_id = resolved["accessKey"]
        secret = resolved["secretKey"]
        token = resolved["sessionToken"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_assume"],
            key_id=key_id,
            secret=secret,
            token=token,
            aws_region=aws_region,
        )
        return blob_module, blob_credentials

    raise RuntimeError(f"Unsupported AWS credentials mode: {credentials_mode}")


def azure_credentials(ctx: StorageContext) -> tuple[str, str]:
    info = ctx.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("authType")
    storage_account = params.get("storageAccount")

    queries = _load_queries(ctx)
    blob_module = queries["blob_setup"]["azure_modules"]

    if credentials_mode == "SHARED_KEY":
        account_name = params["storageAccount"]
        access_key = params["accessKey"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["azure_access_key_headers"],
            account_name=account_name,
            access_key=access_key,
        )
        return blob_module, blob_credentials

    if credentials_mode == "OAUTH2_APP":
        tenant_id = params["tenantId"]
        client_id = params["appId"]
        client_secret = params["appSecret"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["azure_oauth2_headers"],
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            account_name=storage_account,
        )
        return blob_module, blob_credentials

    raise RuntimeError(f"Unsupported Azure authentication type: {credentials_mode}")


def configure_gcs_filesystem(conn) -> None:
    from fsspec import filesystem

    conn.register_filesystem(filesystem("gcs"))


def derive_key_from_password(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=390000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def decrypt_string(ciphertext: bytes, password: str, salt: bytes) -> str:
    key = derive_key_from_password(password, salt)
    fernet = Fernet(key)
    return fernet.decrypt(ciphertext).decode()


def gcp_credentials(ctx: StorageContext):
    variables = ctx.project_handle.get_variables()
    gcs_hmac = (variables.get("local") or {}).get("gcs_hmac")

    if not gcs_hmac:
        return None, None

    try:
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        access_key = gcs_hmac["access_key"]
    except Exception:
        return None, None

    try:
        hmac_secret = decrypt_string(ciphertext, password="DF2!&sEkm)f4}i99,e&9bS:Wj", salt=salt)
    except Exception:
        return None, None

    queries = _load_queries(ctx)
    blob_module = queries["blob_setup"]["gcp_modules"]
    blob_credentials = yaml_loader.render_query(
        queries["blob_setup"]["gcp_headers"],
        hmac_id=access_key,
        hmac_secret=hmac_secret,
    )

    return blob_module, blob_credentials


def configure_storage(conn, *, ctx: StorageContext) -> dict:
    """Configure DuckDB to read directly from blob storage.

    Returns a small dict with resolved provider + credential mode.
    """

    ctype = ctx.connection_type

    blob_module = None
    blob_credentials = None
    credential_mode = None

    if ctype == "EC2":
        credential_mode = (ctx.connection_handle.get_info().get("params") or {}).get("credentialsMode")
        blob_module, blob_credentials = aws_credentials(ctx)
    elif ctype == "Azure":
        credential_mode = (ctx.connection_handle.get_info().get("params") or {}).get("authType")
        blob_module, blob_credentials = azure_credentials(ctx)
    elif ctype == "GCS":
        blob_module, blob_credentials = gcp_credentials(ctx)
        if not blob_module or not blob_credentials:
            configure_gcs_filesystem(conn)
            return {"provider": "GCS", "credential_mode": "filesystem"}
        return {"provider": "GCS", "credential_mode": "hmac"}
    else:
        raise ValueError(f"Unknown storage provider: {ctype}")

    conn.execute(blob_module)
    conn.execute(blob_credentials)

    return {"provider": ctype, "credential_mode": credential_mode}
