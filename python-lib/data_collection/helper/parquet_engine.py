from __future__ import annotations

import sys
from pathlib import Path


def _add_dataiku_pyenv_site_packages() -> None:
    """Make `pyarrow` available if present in the base Dataiku pyenv.

    In some DSS Code Envs, `pyarrow` is not installed directly, but exists in the
    base Dataiku python environment. We avoid modifying the code env and instead
    extend `sys.path` at runtime.

    This tries common locations under `/opt/dataiku/pyenv` and uses the current
    interpreter's major/minor version when possible.
    """

    major = sys.version_info.major
    minor = sys.version_info.minor

    candidates = [
        Path(f"/opt/dataiku/pyenv/lib/python{major}.{minor}/site-packages"),
        Path(f"/opt/dataiku/pyenv/lib64/python{major}.{minor}/site-packages"),
        # Fallback for older containers where the base pyenv is 3.9.
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
