from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import dataiku
from dataikuapi.dssclient import DSSClient


@dataclass(frozen=True)
class PulseMacroContext:
    """Shared context for Pulse macros.

    Standardizes how macros:
    - read the single DSS macro parameter set (`pulse_primary`)
    - create the local client (always `dataiku.api_client()`)
    - create the remote client (DSSClient from pulse_primary settings)

    The remote client is intentionally required for the "data gather" macros,
    because uploads should go through a single client consistently.
    """

    param_set: dict[str, Any]
    local_client: Any
    remote_client: DSSClient


def get_param_set(plugin_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the canonical param set for all macros."""

    if not plugin_config:
        return {}
    param_set = plugin_config.get("pulse_primary")  # type: ignore[call-arg]
    if isinstance(param_set, dict):
        return dict(param_set)
    return {}


def build_context(*, plugin_config: Mapping[str, Any] | None) -> PulseMacroContext:
    """Build a macro context with local + remote clients."""

    param_set = get_param_set(plugin_config)

    local_client = dataiku.api_client()

    remote_host = param_set.get("pulse_project_url")
    remote_api_key = param_set.get("pulse_project_api")
    if not remote_host or not remote_api_key:
        raise ValueError(
            "Missing remote target configuration in pulse_primary: expected pulse_project_url and pulse_project_api"
        )

    remote_client = DSSClient(
        remote_host,
        api_key=remote_api_key,
        no_check_certificate=bool(param_set.get("ignore_certs", False)),
    )

    return PulseMacroContext(param_set=param_set, local_client=local_client, remote_client=remote_client)
