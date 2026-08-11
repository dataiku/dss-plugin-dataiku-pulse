from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, send_from_directory

_REPO_ROOT = Path(__file__).resolve().parents[4]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"


def _err(message: str, *, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def register_routes(bp: Blueprint) -> None:
    @bp.route("/resource/pulse-dashboard/build/<path:filename>")
    def serve_packaged_build(filename: str):  # pragma: no cover
        if not _BUILD_DIR.is_dir():
            return (
                "Pulse dashboard build not found. Expected: "
                f"{_BUILD_DIR}. Run scripts/webapp/sync_pulse_dashboard_build.sh to populate it.",
                404,
            )

        filename = (filename or "").lstrip("/")
        candidate = (_BUILD_DIR / filename).resolve()

        try:
            candidate.relative_to(_BUILD_DIR)
        except ValueError:
            return _err("Invalid path", status=400)

        if not candidate.exists() or not candidate.is_file():
            return _err("Not found", status=404)

        return send_from_directory(_BUILD_DIR, filename)
