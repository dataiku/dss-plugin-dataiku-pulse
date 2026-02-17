import base64
import logging

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from pulse_duckdb import settings
from pulse_duckdb.helpers import yaml_loader

logger = logging.getLogger(__name__)

queries = yaml_loader.load_yaml(settings.BASE_DIR / "pulse_duckdb/config/blob/blob_credentials.yaml")

# -------------------------------------------------------------------
# AWS Credentials
# -------------------------------------------------------------------
def aws_credentials() -> tuple[str, str]:
    info = settings.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("credentialsMode")
    aws_region = params.get("regionOrEndpoint")

    blob_module = queries["blob_setup"]["aws_modules"]

    if credentials_mode == "KEYPAIR":
        try:
            key_id = params["accessKey"]
            secret = params["secretKey"]
        except KeyError as exc:
            logger.exception("Missing resolved AWS credentials")
            raise RuntimeError(
                "Failed to resolve AWS credentials from Dataiku connection"
            ) from exc
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_static"],
            key_id=key_id,
            secret=secret,
            aws_region=aws_region,
        )
        return blob_module, blob_credentials

    if credentials_mode in {"STS_ASSUME_ROLE", "ENVIRONMENT"}:
        try:
            resolved = info["resolvedAWSCredential"]
            key_id = resolved["accessKey"]
            secret = resolved["secretKey"]
            token = resolved["sessionToken"]
        except KeyError as exc:
            logger.exception("Missing resolved AWS credentials")
            raise RuntimeError(
                "Failed to resolve AWS credentials from Dataiku connection"
            ) from exc
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_assume"],
            key_id=key_id,
            secret=secret,
            token=token,
            aws_region=aws_region,
        )
        return blob_module, blob_credentials

    logger.error("Unsupported AWS credentials mode: %s", credentials_mode)
    raise RuntimeError(f"Unsupported AWS credentials mode: {credentials_mode}")
    return


# -------------------------------------------------------------------
# Azure Credentials
# -------------------------------------------------------------------
def azure_credentials() -> tuple[str, str]:
    info = settings.connection_handle.get_info()
    params = info.get("params", {})
    credentials_mode = params.get("authType")
    storage_account = params.get("storageAccount")

    blob_module = queries["blob_setup"]["azure_modules"]

    if credentials_mode == "SHARED_KEY":
        logger.error("Azure SHARED_KEY authentication is not supported yet")
        raise RuntimeError("Azure SHARED_KEY authentication is not supported")

    if credentials_mode == "OAUTH2_APP":
        try:
            tenant_id = params["tenantId"]
            client_id = params["appId"]
            client_secret = params["appSecret"]
        except KeyError as exc:
            logger.exception("Missing Azure OAuth2 parameters")
            raise RuntimeError(
                "Failed to resolve Azure OAuth2 credentials from Dataiku connection"
            ) from exc

        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["azure_oauth2_headers"],
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            account_name=storage_account,
        )

        return blob_module, blob_credentials

    logger.error("Unsupported Azure authentication type: %s", credentials_mode)
    raise RuntimeError(f"Unsupported Azure authentication type: {credentials_mode}")

# -------------------------------------------------------------------
# GCP Credentials
#    - GCS HMAC Handler -- Designed to grab encrypted HMAC from Project Vars
# -------------------------------------------------------------------
def configure_gcs_filesystem(conn) -> None:
    try:
        from fsspec import filesystem
        conn.register_filesystem(filesystem("gcs"))
    except Exception as exc:
        logger.exception("Failed to register GCS filesystem")
        raise RuntimeError("Failed to configure GCS filesystem") from exc
    return


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


def gcp_credentials():
    variables = settings.project_handle.get_variables()
    gcs_hmac = variables.get("local", {}).get("gcs_hmac")

    if not gcs_hmac:
        logger.warning("No gsc_hmac found, falling back to filesystem.")
        return None, None
    
    try:
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        access_key = gcs_hmac["access_key"]
    except KeyError:
        logger.info("Incomplete gcs_hmac configuration, falling back to filesystem")
        return None, None
    except Exception:
        logger.info(f"Invalid base64 encoding in gcs_hmac, falling back to filesystem")
        return None, None

    try:
        hmac_secret = decrypt_string(ciphertext, password="DF2!&sEkm)f4}i99,e&9bS:Wj", salt=salt)
    except Exception as e:
        logger.info(f"Failed to decrypt GCS HMAC secret, falling back to filesystem: {e}")
        return None, None

    try:
        blob_module = queries["blob_setup"]["gcp_modules"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["gcp_headers"],
            hmac_id=access_key,
            hmac_secret=hmac_secret,
        )
    except Exception:
        logger.info("Failed to render GCS HMAC configuration, falling back to filesystem")
        return None, None

    logger.info("GCS HMAC credentials successfully loaded")
    return blob_module, blob_credentials

# -------------------------------------------------------------------
# Blob Storage Main
# -------------------------------------------------------------------
def apply_blob_encryption(conn):
    params = settings.connection_handle.get_info().get("params", {})
    encryption_mode = params.get("encryptionMode", "NONE").upper()

    logger.debug(f"Blob encryption mode: {encryption_mode}")

    if encryption_mode == "NONE":
        return conn

    elif encryption_mode == "SSE_S3":
        logger.info("Enabling S3 server-side encryption (AES256).")
        conn.execute("SET s3_server_side_encryption='AES256';")

    elif encryption_mode == "SSE_KMS":
        kms_key = params.get("encryptionKeyId", None)
        if not kms_key:
            raise ValueError("SSE_KMS selected but encryptionKeyId not provided.")

        logger.info("Enabling S3 server-side encryption (aws:kms).")
        conn.execute("SET s3_server_side_encryption='aws:kms';")
        conn.execute(f"SET s3_sse_kms_key_id='{kms_key}';")

    elif encryption_mode == "AZURE":
        logger.info("Azure encryption handled at storage account level.")
        # Placeholder for future Azure logic

    elif encryption_mode == "GCS":
        logger.info("GCS encryption handled via bucket configuration.")
        # Placeholder for future GCS logic

    else:
        logger.warning(f"Unknown encryption mode: {encryption_mode}")

    return conn



# -------------------------------------------------------------------
# Blob Storage Main
# -------------------------------------------------------------------
def configure_storage(conn) -> None:
    connection_type = settings.connection_type
    logger.info("Loading %s storage configuration", connection_type)

    blob_module = None
    blob_credentials = None

    if connection_type == "EC2":
        blob_module, blob_credentials = aws_credentials()
    elif connection_type == "Azure":
        blob_module, blob_credentials = azure_credentials()
    elif connection_type == "GCS":
        blob_module, blob_credentials = gcp_credentials()
        if not blob_module or not blob_credentials:
            logger.info("GCS HMAC not available, falling back to filesystem")
            try:
                configure_gcs_filesystem(conn)
                logger.info("GCS filesystem registered successfully")
                return
            except Exception:
                logger.exception("GCS filesystem fallback failed")
                raise RuntimeError(
                    "Failed to configure GCS storage via HMAC and filesystem"
                )
    else:
        raise ValueError(f"Unknown storage provider: {connection_type}")

    if not blob_module or not blob_credentials:
        raise RuntimeError(
            f"Failed to configure blob storage for {connection_type}"
        )

    logger.info("Configuring %s BLOB storage", connection_type)
    try:
        conn.execute(blob_module)
        conn.execute(blob_credentials)
    except Exception as exc:
        logger.exception("DuckDB storage configuration failed")
        raise RuntimeError(
            f"Failed to configure {connection_type} blob storage"
        ) from exc



        