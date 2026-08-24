from __future__ import annotations

import base64
import logging
from typing import Any, Protocol


logger = logging.getLogger(__name__)


class StorageContextLike(Protocol):
    connection_name: str
    connection_handle: Any
    cached_connection_info: dict[str, Any]
    project_handle: Any


def connection_info(ctx: StorageContextLike, *, allow_cached: bool = False) -> dict:
    try:
        info = ctx.connection_handle.get_info()
    except Exception:
        cached_info = ctx.cached_connection_info if isinstance(ctx.cached_connection_info, dict) else {}
        if allow_cached and cached_info:
            params = cached_info.get("params") or {}
            credential_mode = params.get("credentialsMode") or params.get("authType") or "unknown"
            logger.warning(
                "Falling back to cached DSS connection info for connection=%s credential_mode=%s after get_info() failure",
                ctx.connection_name,
                credential_mode,
                exc_info=True,
            )
            return cached_info
        raise

    if isinstance(info, dict):
        ctx.cached_connection_info.clear()
        ctx.cached_connection_info.update(info)
        return info

    return {}


def derive_key_from_password(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=390000)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def decrypt_string(ciphertext: bytes, password: str, salt: bytes) -> str:
    from cryptography.fernet import Fernet

    return Fernet(derive_key_from_password(password, salt)).decrypt(ciphertext).decode()


def resolve_gcs_hmac_credentials(ctx: StorageContextLike) -> tuple[str, str] | None:
    variables = ctx.project_handle.get_variables()
    gcs_hmac = (variables.get("local") or {}).get("gcs_hmac")
    if not gcs_hmac:
        return None

    try:
        salt = base64.b64decode(gcs_hmac["salt"])
        ciphertext = base64.b64decode(gcs_hmac["ciphertext"])
        access_key = gcs_hmac["access_key"]
        hmac_secret = decrypt_string(ciphertext, password="DF2!&sEkm)f4}i99,e&9bS:Wj", salt=salt)
    except Exception:
        return None

    return access_key, hmac_secret
