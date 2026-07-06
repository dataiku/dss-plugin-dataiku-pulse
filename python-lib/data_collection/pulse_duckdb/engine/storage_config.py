"""Compat shim: real implementation lives in `shared_duckdb.storage_config`.

Kept for import stability of existing callers; add new code to shared_duckdb.
"""

from shared_duckdb.storage_config import aws_credentials, azure_credentials, configure_gcs_filesystem, configure_storage, decrypt_string, derive_key_from_password, gcp_credentials

__all__ = [
    "aws_credentials",
    "azure_credentials",
    "configure_gcs_filesystem",
    "configure_storage",
    "decrypt_string",
    "derive_key_from_password",
    "gcp_credentials",
]
