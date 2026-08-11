from __future__ import annotations

from flask import Blueprint, Flask

from .routes.frontend import register_routes as register_frontend_routes
from .routes.startup import register_routes as register_startup_routes
from .startup import initialize_startup_ownership, run_initial_local_startup

bp = Blueprint("pulse_dashboard", __name__)
_IS_LOCAL_DEV = False


def register_routes(app: Flask, *, is_local_dev: bool = False) -> None:
    global _IS_LOCAL_DEV
    _IS_LOCAL_DEV = is_local_dev
    initialize_startup_ownership()
    register_startup_routes(bp)
    register_frontend_routes(bp)
    app.register_blueprint(bp)
    if _IS_LOCAL_DEV:
        run_initial_local_startup()
