from __future__ import annotations

from flask import Flask

from .full_backend import register_routes

__all__ = ["register_routes", "register_local_routes"]


def register_local_routes(app: Flask) -> None:
    register_routes(app, is_local_dev=True)
