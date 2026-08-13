from __future__ import annotations

from collections import Counter
import logging
from typing import Any

from flask import Blueprint, jsonify

from pulse_dashboard.webapp_backend.support import _has_administration_access

logger = logging.getLogger(__name__)


def _resolve_dashboard_topology_preset(plugin_handle: Any) -> tuple[str, str | None]:
    try:
        plugin_settings = plugin_handle.get_settings()
        parameter_set = plugin_settings.get_parameter_set(parameter_set_name="params-dashboard-instance")
        preset_names = parameter_set.list_preset_names() or []
        if preset_names:
            return str(preset_names[0]), None
        return "primary", "No preset found in params-dashboard-instance; using 'primary'"
    except Exception as exc:
        return "primary", f"Could not resolve preset name; using 'primary': {exc!r}"


def _sanitize_topology_config(plugin_config: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(plugin_config or {})
    hub_url = str(config.get("pulse_project_url") or "").strip()
    raw_workers = config.get("worker_hosts")
    workers: list[dict[str, str]] = []
    invalid_worker_entries = 0

    if isinstance(raw_workers, list):
        for entry in raw_workers:
            if not isinstance(entry, dict):
                invalid_worker_entries += 1
                continue
            worker_url = str(entry.get("worker_url") or "").strip()
            if not worker_url:
                invalid_worker_entries += 1
                continue
            workers.append(
                {
                    "url": worker_url,
                    "classification": str(entry.get("worker_classification") or "").strip(),
                    "presetName": str(entry.get("preset_name") or "").strip(),
                }
            )

    return {
        "hub": {"url": hub_url},
        "workers": workers,
        "diagnostics": {
            "hub_url_found": bool(hub_url),
            "worker_hosts_found": raw_workers is not None,
            "worker_hosts_type": type(raw_workers).__name__ if raw_workers is not None else "missing",
            "worker_entries_seen": len(raw_workers) if isinstance(raw_workers, list) else 0,
            "valid_worker_urls": len(workers),
            "invalid_worker_entries": invalid_worker_entries,
        },
    }


def register_routes(bp: Blueprint) -> None:
    @bp.route("/api/admin/pulse-topology")
    def admin_pulse_topology():
        try:
            if not _has_administration_access():
                return jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "ADMIN_ACCESS_REQUIRED",
                            "message": "Administration access is required.",
                        },
                        "counts": {"hubs": 0, "workers": 0},
                        "warnings": [],
                        "diagnostics": {"route_reached": True},
                    }
                ), 403

            import dataiku

            client = dataiku.api_client()
            plugin_handle = client.get_plugin(plugin_id="dataiku-pulse")
            parameter_set = plugin_handle.get_settings().get_parameter_set(parameter_set_name="params-dashboard-instance")
            preset_name, preset_warning = _resolve_dashboard_topology_preset(plugin_handle)
            preset = parameter_set.get_preset(preset_name)

            if not preset:
                return jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "TOPOLOGY_CONFIG_NOT_FOUND",
                            "message": "Pulse topology configuration is unavailable.",
                        },
                        "diagnostics": {
                            "parameter_set_found": True,
                            "preset_found": False,
                        },
                    }
                ), 404

            raw_config = getattr(preset, "plugin_config", None)
            if not isinstance(raw_config, dict):
                return jsonify(
                    {
                        "ok": False,
                        "error": {
                            "code": "TOPOLOGY_CONFIG_INVALID",
                            "message": "Pulse topology configuration is unavailable.",
                        },
                        "diagnostics": {
                            "parameter_set_found": True,
                            "preset_found": True,
                            "plugin_config_type": type(raw_config).__name__ if raw_config is not None else "missing",
                        },
                    }
                ), 500

            topology = _sanitize_topology_config(raw_config)
            workers = topology.get("workers") or []
            warnings: list[str] = []
            if preset_warning:
                warnings.append(preset_warning)

            return jsonify(
                {
                    "ok": True,
                    "hub": topology.get("hub") or {"url": ""},
                    "workers": workers,
                    "counts": {
                        "hubs": 1 if (topology.get("hub") or {}).get("url") else 0,
                        "workers": len(workers),
                    },
                    "warnings": warnings,
                    "diagnostics": {
                        "parameter_set_found": True,
                        "preset_found": True,
                        **(topology.get("diagnostics") or {}),
                    },
                    "summary": {
                        "hubCount": 1 if (topology.get("hub") or {}).get("url") else 0,
                        "workerCount": len(workers),
                        "classifications": dict(
                            Counter(
                                worker.get("classification")
                                for worker in workers
                                if str(worker.get("classification") or "").strip()
                            )
                        ),
                    },
                    "topology": {
                        "hub": {
                            "url": str((topology.get("hub") or {}).get("url") or "").strip(),
                            "label": "Pulse Hub",
                        },
                        "spokes": [
                            {
                                "url": str(worker.get("url") or "").strip(),
                                "classification": str(worker.get("classification") or "").strip(),
                                "presetName": str(worker.get("presetName") or "").strip(),
                            }
                            for worker in workers
                        ],
                    },
                }
            )
        except Exception:
            logger.exception("Unable to read Pulse topology configuration")
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "TOPOLOGY_ENDPOINT_FAILED",
                        "message": "Unable to load the Pulse topology configuration.",
                    },
                    "counts": {"hubs": 0, "workers": 0},
                    "warnings": [],
                    "diagnostics": {"route_reached": True},
                }
            ), 500
