from __future__ import annotations

import logging
import platform
from pathlib import Path

import duckdb


logger = logging.getLogger(__name__)


_PROVIDER_TO_EXTENSIONS = {
    "EC2": ["httpfs"],
    "Azure": ["azure"],
    "GCS": ["httpfs"],
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def duckdb_version() -> str:
    return getattr(duckdb, "__version__", "unknown")


def platform_slug() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    aliases = {
        ("linux", "x86_64"): "linux_amd64",
        ("linux", "amd64"): "linux_amd64",
        ("linux", "aarch64"): "linux_arm64",
        ("darwin", "arm64"): "darwin_arm64",
        ("darwin", "x86_64"): "darwin_amd64",
        ("windows", "amd64"): "windows_amd64",
        ("windows", "x86_64"): "windows_amd64",
    }
    return aliases.get((system, machine), f"{system}_{machine}")


def bundled_extension_path(extension_name: str) -> Path:
    return (
        _repo_root()
        / "resource"
        / "duckdb_extensions"
        / duckdb_version()
        / platform_slug()
        / f"{extension_name}.duckdb_extension"
    )


def required_extensions(provider: str) -> list[str]:
    return list(_PROVIDER_TO_EXTENSIONS.get(provider, []))


def _load_installed_extension(conn, extension_name: str) -> None:
    conn.execute(f"LOAD {extension_name};")


def _install_and_load_extension(conn, extension_name: str) -> None:
    conn.execute(f"INSTALL {extension_name};")
    _load_installed_extension(conn, extension_name)


def _load_bundled_extension(conn, extension_name: str) -> None:
    extension_path = bundled_extension_path(extension_name)
    if not extension_path.exists():
        bundle_root = _repo_root() / "resource" / "duckdb_extensions"
        try:
            available = sorted(
                str(p.relative_to(bundle_root))
                for p in bundle_root.glob("*/*")
                if p.is_dir()
            )
        except Exception:
            available = []
        raise FileNotFoundError(
            f"No bundled DuckDB extension {extension_name!r} for duckdb "
            f"{duckdb_version()} on platform {platform_slug()} — expected file: "
            f"{extension_path}. Bundled version/platform matrix: "
            f"{', '.join(available) if available else 'none'}. Fix by allowing "
            "network access (INSTALL), pre-populating the DuckDB extension cache, "
            "or adding the bundled binary for this duckdb version and platform "
            "(see docs/install.md, 'DuckDB extensions')."
        )
    escaped_path = str(extension_path).replace("'", "''")
    conn.execute(f"LOAD '{escaped_path}';")


def ensure_extension_loaded(conn, extension_name: str) -> str:
    try:
        _install_and_load_extension(conn, extension_name)
        return "installed"
    except Exception:
        logger.info("DuckDB extension install failed for %s; trying cached load", extension_name, exc_info=True)

    try:
        _load_installed_extension(conn, extension_name)
        return "cached"
    except Exception:
        logger.info("DuckDB cached extension load failed for %s; trying bundled resource", extension_name, exc_info=True)

    _load_bundled_extension(conn, extension_name)
    return "bundled"


def ensure_provider_extensions(conn, provider: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for extension_name in required_extensions(provider):
        results[extension_name] = ensure_extension_loaded(conn, extension_name)
    return results
