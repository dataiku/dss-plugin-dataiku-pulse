import logging
from backend import settings
from backend.utils import yaml_loader

logger = logging.getLogger(__name__)
queries = yaml_loader.load_yaml(settings.BASE_DIR / "backend/config/blob/blob_credentials.yaml")

# -------------------------------------------------------------------
# AWS Credentials
# -------------------------------------------------------------------
def aws_credentials(blob_module=None, blob_credentials=None):
    blob_module = queries["blob_setup"]["aws_modules"]
    aws_region = settings.connection_handle.get_info()["params"]["regionOrEndpoint"]
    credentials_mode = settings.connection_handle.get_info()["params"]["credentialsMode"]
    if credentials_mode == "KEYPAIR":
        accessKey = settings.connection_handle.get_info()["params"]["accessKey"]
        secretKey = settings.connection_handle.get_info()["params"]["secretKey"]
        logger.exception("KEYPAIR NEEDS TO BE INIT")
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    elif credentials_mode == "STS_ASSUME_ROLE" or credentials_mode == "ENVIRONMENT":
        key_id = settings.connection_handle.get_info()["resolvedAWSCredential"]["accessKey"]
        secret = settings.connection_handle.get_info()["resolvedAWSCredential"]["secretKey"]
        token  = settings.connection_handle.get_info()["resolvedAWSCredential"]["sessionToken"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["aws_headers_assume"],
            key_id=key_id,
            secret=secret,
            token=token,
            aws_region=aws_region
        )
    else:
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    return blob_module, blob_credentials

# -------------------------------------------------------------------
# Azure Credentials
# -------------------------------------------------------------------
def azure_credentials(blob_module=None, blob_credentials=None):
    blob_module = queries["blob_setup"]["azure_modules"]
    storageAccount = settings.connection_handle.get_info()["params"]["storageAccount"]
    credentials_mode = settings.connection_handle.get_info()["params"]["authType"]
    if credentials_mode == "SHARED_KEY":
        accessKey = settings.connection_handle.get_info()["params"]["accessKey"]
        logger.error("SHARED KEY NEEDS TO BE INIT")
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    elif credentials_mode == "OAUTH2_APP":
        tenant_id = settings.connection_handle.get_info()["params"]["tenantId"]
        client_id = settings.connection_handle.get_info()["params"]["appId"]
        client_secret = settings.connection_handle.get_info()["params"]["appSecret"]
        blob_credentials = yaml_loader.render_query(
            queries["blob_setup"]["azure_oauth2_headers"],
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            account_name=storageAccount
        )
    else:
        raise Exception("Failed to find proper BLOB information. Check logs for detail.")
    return blob_module, blob_credentials

# -------------------------------------------------------------------
# GCP Credentials
#    - GCS HMAC Handler -- Designed to grab encrypted HMAC from Project Vars
# -------------------------------------------------------------------
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
    f = Fernet(key)
    return f.decrypt(ciphertext).decode()

def gcp_credentials(blob_module=None, blob_credentials=None):
    try:
        variables = project_handle.get_variables()
        gcs_hmac = variables["local"].get("gcs_hmac", False)
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        hmac_secret = decrypt_string(ciphertext, "DF2!&sEkm)f4}i99,e&9bS:Wj", salt)
        blob_module = queries["blob_setup"]["gcp_modules"]
        blob_credentials = render_query(
            queries["blob_setup"]["gcp_headers"],
            hmac_id=gcs_hmac["access_key"],
            hmac_secret=hmac_secret
        )
    except:
        blob_module = False
        blob_credentials = False
    return blob_module, blob_credentials

# -------------------------------------------------------------------
# Blob Storage Main
# -------------------------------------------------------------------
def configure_storage(conn):
    blob_module = None
    blob_credentials = None
    connection_type = settings.connection_type
    logger.warning(f"Loading {connection_type} Storage...")
    if connection_type == "EC2":
        blob_module, blob_credentials = aws_credentials(blob_module, blob_credentials)
    elif connection_type == "Azure":
        blob_module, blob_credentials = azure_credentials(blob_module, blob_credentials)
    elif connection_type == "GCS":
        blob_module, blob_credentials = gcp_credentials(blob_module, blob_credentials)
    else:
        raise ValueError(f"Unknown storage provider: {connection_type}")

    if blob_module == None or blob_credentials == None:
        raise ValueError(f"Failed to configure blob storage: {connection_type}")

    logger.warning("Configuring BLOB Storage...")
    if blob_module and blob_credentials:
        conn.execute(f"{blob_module}")
        conn.execute(f"{blob_credentials}")
    elif not blob_module and not blob_credentials and connection_type == "GCS":
        try:
            from fsspec import filesystem
            conn.register_filesystem(filesystem('gcs'))
        except Exception as e:
            logger.error(f"Failed to get HMAC Key and Secret: {e}")
    return