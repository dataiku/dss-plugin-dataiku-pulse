from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

from flask import Flask

app = cast(Flask | None, globals().get("app"))
_HAS_INJECTED_DSS_APP = app is not None

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_DIR = _REPO_ROOT / "resource" / "pulse-dashboard" / "build"

try:
    from pulse_dashboard.webapp_backend import register_local_routes, register_routes  # type: ignore
except Exception:
    python_lib = _REPO_ROOT / "python-lib"
    if python_lib.is_dir():
        sys.path.insert(0, str(python_lib))
    from pulse_dashboard.webapp_backend import register_local_routes, register_routes  # type: ignore

if app is None:  # pragma: no cover
    static_dir = _BUILD_DIR / "static"
    if static_dir.is_dir():
        app = Flask(__name__, static_folder=str(static_dir), static_url_path="/static")
    else:
        app = Flask(__name__)

app = cast(Flask, app)
if not _HAS_INJECTED_DSS_APP:
    register_local_routes(app)
else:
    register_routes(app)
