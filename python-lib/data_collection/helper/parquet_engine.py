from __future__ import annotations

import sys
from pathlib import Path


def _add_dataiku_pyenv_site_packages() -> None:
    """Make `pyarrow` available if installed in /opt/dataiku/pyenv.

    In some DSS Code Envs, `pyarrow` is not installed directly, but exists in the
    base Dataiku python environment. We avoid modifying the code env and instead
    extend `sys.path` at runtime.
    """

    candidates = [
        Path("/opt/dataiku/pyenv/lib/python3.9/site-packages"),
        Path("/opt/dataiku/pyenv/lib64/python3.9/site-packages"),
    ]
    for p in candidates:
        if p.exists() and str(p) not in sys.path:
            sys.path.append(str(p))


def ensure_pyarrow() -> None:
    try:
        import pyarrow  # noqa: F401

        return
    except Exception:
        _add_dataiku_pyenv_site_packages()

    import pyarrow  # noqa: F401
