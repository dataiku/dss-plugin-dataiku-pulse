from __future__ import annotations

__all__ = ["register_routes", "register_local_routes"]


# Flask (and the 5k-line full_backend module) are imported lazily so that
# Flask-free members of this package — e.g. table_registry, used by the
# contract validator and tests — can be imported without a webapp stack.
def __getattr__(name: str):
    if name == "register_routes":
        from .full_backend import register_routes

        return register_routes
    if name == "register_local_routes":

        def register_local_routes(app) -> None:
            from .full_backend import register_routes

            register_routes(app, is_local_dev=True)

        return register_local_routes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
