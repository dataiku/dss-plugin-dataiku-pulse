from __future__ import annotations

import logging
import platform
from pathlib import Path
from typing import Callable

import duckdb


logger = logging.getLogger(__name__)

_SUPPORTED_EXTENSIONS = frozenset({"azure", "httpfs"})

_NATIVE_RESOURCE_RESOLVERS = (
    ("dataiku.customwebapp.get_webapp_resource", "dataiku.customwebapp.get_webapp_resource"),
    ("dataiku.customrecipe.get_recipe_resource", "dataiku.customrecipe.get_recipe_resource"),
)

_PROVIDER_TO_EXTENSIONS = {
    "EC2": ["httpfs"],
    "Azure": ["azure"],
    "GCS": ["httpfs"],
}


def _local_repo_resource_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "resource"


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


def _validated_extension_name(extension_name: str) -> str:
    normalized = str(extension_name or "").strip()
    if normalized not in _SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(_SUPPORTED_EXTENSIONS))
        raise ValueError(
            f"Unsupported DuckDB extension {extension_name!r}. Supported extensions: {supported}."
        )
    return normalized


def _resource_dir_from_native_api(
    *,
    import_path: str,
    source_name: str,
) -> tuple[Path | None, str | None]:
    module_name, _, function_name = import_path.rpartition(".")
    try:
        module = __import__(module_name, fromlist=[function_name])
        resolver: Callable[[], str | None] = getattr(module, function_name)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None, None

    try:
        resource_dir = resolver()
    except Exception as exc:
        logger.warning(
            "DuckDB bundled resource resolution failed via %s: %s: %s",
            source_name,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None, f"{source_name}: {type(exc).__name__}: {exc}"

    if not resource_dir:
        return None, None

    return Path(resource_dir), None


def _resolved_resource_dir() -> tuple[Path | None, str, list[str]]:
    failures: list[str] = []
    for import_path, source_name in _NATIVE_RESOURCE_RESOLVERS:
        resource_dir, failure = _resource_dir_from_native_api(
            import_path=import_path,
            source_name=source_name,
        )
        if failure:
            failures.append(failure)
        if resource_dir is not None:
            return resource_dir, source_name, failures

    return _local_repo_resource_dir(), "filesystem_fallback", failures


def _available_bundle_matrix(bundle_root: Path) -> list[str]:
    return sorted(
        str(path.relative_to(bundle_root))
        for path in bundle_root.glob("*/*")
        if path.is_dir()
    )


def bundled_extension_path(extension_name: str) -> Path:
    extension_name = _validated_extension_name(extension_name)
    version = duckdb_version()
    platform_name = platform_slug()
    resource_dir, resource_source, resolver_failures = _resolved_resource_dir()
    resolver_diagnostics = ""
    if resolver_failures:
        resolver_diagnostics = f" Native resource resolution failures: {' | '.join(resolver_failures)}"

    if resource_dir is None or not resource_dir.is_dir():
        raise FileNotFoundError(
            f"DuckDB bundled resource directory unavailable for extension {extension_name!r}. "
            f"Resolved via {resource_source} to: {resource_dir}. Expected plugin resource directory "
            f"containing duckdb_extensions/{version}/{platform_name}/{extension_name}.duckdb_extension."
            f"{resolver_diagnostics}"
        )

    bundle_root = resource_dir / "duckdb_extensions"
    if not bundle_root.is_dir():
        raise FileNotFoundError(
            f"DuckDB bundled extensions directory unavailable for extension {extension_name!r}. "
            f"Resolved resource directory via {resource_source}: {resource_dir}. Expected directory: {bundle_root}."
            f"{resolver_diagnostics}"
        )

    version_platform_dir = bundle_root / version / platform_name
    if not version_platform_dir.is_dir():
        available = _available_bundle_matrix(bundle_root)
        raise FileNotFoundError(
            f"No bundled DuckDB extension directory for duckdb {version} on platform {platform_name} "
            f"under {bundle_root}. Bundled version/platform matrix: "
            f"{', '.join(available) if available else 'none'}.{resolver_diagnostics}"
        )

    extension_path = version_platform_dir / f"{extension_name}.duckdb_extension"
    if not extension_path.is_file():
        available_extensions = sorted(
            path.name for path in version_platform_dir.glob("*.duckdb_extension") if path.is_file()
        )
        raise FileNotFoundError(
            f"No bundled DuckDB extension {extension_name!r} for duckdb {version} on platform {platform_name} "
            f"- expected file: {extension_path}. Available bundled extensions for this version/platform: "
            f"{', '.join(available_extensions) if available_extensions else 'none'}.{resolver_diagnostics}"
        )

    return extension_path


def required_extensions(provider: str) -> list[str]:
    return list(_PROVIDER_TO_EXTENSIONS.get(provider, []))


def _load_installed_extension(conn, extension_name: str) -> None:
    conn.execute(f"LOAD {extension_name};")


def _install_and_load_extension(conn, extension_name: str) -> None:
    conn.execute(f"INSTALL {extension_name};")
    _load_installed_extension(conn, extension_name)


def _load_bundled_extension(conn, extension_name: str) -> None:
    extension_path = bundled_extension_path(extension_name)
    escaped_path = str(extension_path).replace("'", "''")
    conn.execute(f"LOAD '{escaped_path}';")


def ensure_extension_loaded(conn, extension_name: str) -> str:
    extension_name = _validated_extension_name(extension_name)
    try:
        _install_and_load_extension(conn, extension_name)
        return "installed"
    except Exception as exc:
        logger.info(
            "DuckDB extension install failed for %s; trying cached load (%s: %s)",
            extension_name,
            type(exc).__name__,
            exc,
        )

    try:
        _load_installed_extension(conn, extension_name)
        return "cached"
    except Exception as exc:
        logger.info(
            "DuckDB cached extension load failed for %s; trying bundled resource (%s: %s)",
            extension_name,
            type(exc).__name__,
            exc,
        )

    _load_bundled_extension(conn, extension_name)
    return "bundled"


def ensure_provider_extensions(conn, provider: str) -> dict[str, str]:
    results: dict[str, str] = {}
    for extension_name in required_extensions(provider):
        results[extension_name] = ensure_extension_loaded(conn, extension_name)
    return results
