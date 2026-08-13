from __future__ import annotations

from flask import Blueprint, Flask

from .routes.build_users import register_routes as register_build_users_routes
from .routes.build_assets import register_routes as register_build_assets_routes
from .routes.build_products import register_routes as register_build_products_routes
from .routes.consumption_products import register_routes as register_consumption_products_routes
from .routes.development_activity import register_routes as register_development_activity_routes
from .routes.debug import register_routes as register_debug_routes
from .routes.frontend import register_routes as register_frontend_routes
from .routes.startup import register_routes as register_startup_routes
from .startup import initialize_startup_ownership, run_initial_local_startup

bp = Blueprint("pulse_dashboard", __name__)
_IS_LOCAL_DEV = False


def is_local_dev() -> bool:
    return bool(_IS_LOCAL_DEV)


def register_routes(app: Flask, *, is_local_dev: bool = False) -> None:
    global _IS_LOCAL_DEV
    _IS_LOCAL_DEV = is_local_dev
    initialize_startup_ownership()
    register_startup_routes(bp)
    register_frontend_routes(bp)
    register_build_users_routes(bp)
    register_build_assets_routes(bp)
    register_build_products_routes(bp)
    register_consumption_products_routes(bp)
    register_development_activity_routes(bp)
    register_debug_routes(bp)
    app.register_blueprint(bp)
    if _IS_LOCAL_DEV:
        run_initial_local_startup()
