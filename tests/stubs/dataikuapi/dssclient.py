from __future__ import annotations

from typing import Any


class DSSClient:
    def __init__(self, host: str | None = None, api_key: str | None = None, insecure_tls: bool = False):
        self.host = host
        self.api_key = api_key
        self.insecure_tls = insecure_tls

    def get_project(self, project_key: str) -> Any:  # pragma: no cover
        raise NotImplementedError("stub DSSClient has no projects")
