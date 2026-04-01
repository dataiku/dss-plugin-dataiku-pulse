from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

import dataiku
from dataikuapi import DSSClient


@dataclass(frozen=True)
class ClientConfig:
    """Configuration for connecting to a DSS instance."""

    # If None, dataiku.api_client() decides (in-instance).
    host: Optional[str] = None
    api_key: Optional[str] = None


def load_client_config_from_env() -> ClientConfig:
    """Load DSS connection details from environment variables.

    Supported env vars (optional):
    - DSS_HOST
    - DSS_API_KEY

    If not provided, defaults to using in-instance `dataiku.api_client()`.
    """

    host = os.environ.get("DSS_HOST")
    api_key = os.environ.get("DSS_API_KEY")
    return ClientConfig(host=host, api_key=api_key)


def get_client(config: Optional[ClientConfig] = None) -> DSSClient:
    """Create a DSS client.

    - If `config.host` is provided, uses explicit host/api_key.
    - Otherwise uses `dataiku.api_client()` (best for Code Studio/in-DSS).
    """

    if config is None:
        config = load_client_config_from_env()

    if config.host:
        return DSSClient(config.host, api_key=config.api_key)

    return dataiku.api_client()
