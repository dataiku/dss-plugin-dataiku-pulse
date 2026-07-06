"""Contract validation across Pulse's hand-synchronized string contracts.

Pulse is wired together by exact string matching: collector method names →
flatten/casting YAML filenames and columns → gold spec SQL → dashboard table
registries. Nothing at runtime validates these, so drift degrades data
silently (e.g. casting entries that no longer match any real column simply
never apply).

This module cross-checks all of them and returns a list of ContractIssue.
It is pure Python: yaml + dataikuapi *class* introspection only — no
`dataiku` runtime, no network, no DSS connection.

Wiring: the gather runnables, the gold recipe and initialize-dashboard call
`validate_all()` at startup and log/report a summary (warn-only by default;
set PULSE_CONTRACTS_STRICT=1 or the equivalent param to fail hard).
"""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContractIssue:
    severity: str  # "error" | "warning"
    domain: str  # "casting" | "collectors" | "gold_specs" | "dashboard" | "flatten"
    message: str


# Columns produced dynamically (audit processors, license/trial payloads,
# footprint summaries, commit history) by categories that have no flatten
# config: casting still applies to them via the no-flatten branch of
# normalize_silver, so they legitimately appear in casting YAMLs without
# appearing in any flatten-column YAML.
KNOWN_DYNAMIC_COLUMNS: frozenset[str] = frozenset(
    {
        # audit-derived
        "run_timestamp",
        "calltime",
        "severity",
        "sourcetype",
        # license / trial payloads (license_status rows are built in code)
        "enabled",
        "trial_exists",
        "trial_valid",
        "trial_expired",
        "trial_illegal",
        "trial_granted_on",
        "trial_expires_on",
        "is_mau_eligible",
        # users / git history
        "creationdate",
        "last_session_activity",
        "first_commit_date",
        "last_commit_date",
        "creationtag_lastmodifiedon",
        # footprint summaries
        "level_1_size",
        "level_2_size",
        "level_3_size",
        "size",
        "used",
        "available",
        "used_pct",
    }
)

# Instance-scope categories produced by dedicated code paths rather than
# `client.list_*` reflection (see data-gather-instance/runnable.py).
KNOWN_INSTANCE_CATEGORIES: frozenset[str] = frozenset({"license", "instance_info"})

# Instance-scope flatten configs with no known producer in the current code.
# Surfaced as warnings so they are consciously kept or removed.
_MODULE_SUFFIXES = (
    "project_metadata",
    "instance_metadata",
    "audit_metadata",
    "license_status",
    "max_licenses",
    "addon_licenses",
    "user_activity",
)


def _python_lib_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _schema_consistency_dir() -> Path:
    return Path(__file__).resolve().parent / "data_normalizer" / "schema_consistency"


def _split_stem(stem: str) -> tuple[str, str] | None:
    """Split a flatten filename stem into (category, module).

    Convention: `{category}_{module}.yaml` where module is one of the known
    module suffixes. Returns None when no known module suffix matches.
    """

    for module in _MODULE_SUFFIXES:
        suffix = f"_{module}"
        if stem.endswith(suffix) and len(stem) > len(suffix):
            return stem[: -len(suffix)], module
    return None


def _load_yaml_list(path: Path) -> list[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")
    return [str(v).strip().lower() for v in raw if v is not None and str(v).strip()]


def load_flatten_registry() -> dict[str, dict[tuple[str, str], list[str]]]:
    """Load scope -> {(category, module): required_columns} from flatten YAMLs.

    TODO_* stubs are skipped. Files whose name does not follow the
    `{category}_{module}.yaml` convention are keyed as (stem, "").
    """

    registry: dict[str, dict[tuple[str, str], list[str]]] = {}
    base = _schema_consistency_dir() / "flatten_columns"
    for scope in ("project", "instance", "audit"):
        scope_map: dict[tuple[str, str], list[str]] = {}
        scope_dir = base / scope
        if not scope_dir.is_dir():
            registry[scope] = scope_map
            continue
        for path in sorted(scope_dir.glob("*.yaml")):
            if path.name.startswith("TODO_"):
                continue
            split = _split_stem(path.stem)
            key = split if split is not None else (path.stem, "")
            scope_map[key] = _load_yaml_list(path)
        registry[scope] = scope_map
    return registry


def flatten_column_union(registry: dict[str, dict[tuple[str, str], list[str]]] | None = None) -> set[str]:
    registry = registry if registry is not None else load_flatten_registry()
    union: set[str] = set()
    for scope_map in registry.values():
        for cols in scope_map.values():
            union.update(cols)
    return union


def validate_casting_columns(
    registry: dict[str, dict[tuple[str, str], list[str]]] | None = None,
) -> list[ContractIssue]:
    """Every casting YAML entry must match a real silver column name.

    A casting entry not present in the flatten-column union (nor in the
    dynamic-column allowlist) never applies to anything — this is the bug
    class where casts silently no-op (singular `dataset_*` vs actual plural
    `datasets_*`).
    """

    issues: list[ContractIssue] = []
    union = flatten_column_union(registry)
    casting_dir = _schema_consistency_dir() / "casting_columns"
    for path in sorted(casting_dir.glob("*.yaml")):
        for col in _load_yaml_list(path):
            if col in union or col in KNOWN_DYNAMIC_COLUMNS:
                continue
            issues.append(
                ContractIssue(
                    severity="error",
                    domain="casting",
                    message=(
                        f"casting_columns/{path.name}: {col!r} matches no flatten-column "
                        "entry and is not in KNOWN_DYNAMIC_COLUMNS — this cast never applies"
                    ),
                )
            )
    return issues


def _dataikuapi_list_categories(cls: type) -> set[str]:
    """Categories derivable from `list_*` members of a dataikuapi class."""

    return {
        name[len("list_"):]
        for name in dir(cls)
        if name.startswith("list_") and callable(getattr(cls, name, None))
    }


def _dataikuapi_method_names(cls: type) -> set[str]:
    return {name for name in dir(cls) if callable(getattr(cls, name, None)) and not name.startswith("_")}


def _audit_variant_slugs() -> set[str]:
    """Valid event_mapping variants: slugged dataiku_category values from mapping.csv."""

    path = Path(__file__).resolve().parent / "audit_logs_modules" / "mapping.csv"
    slugs: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            value = (row.get("dataiku_category") or "").strip()
            if not value or value == "DROP_DELETE":
                continue
            slug = re.sub(r"[^A-Za-z0-9]+", "_", value.lower()).strip("_")
            if slug:
                slugs.add(slug)
    return slugs


def _audit_processor_names() -> list[str]:
    path = Path(__file__).resolve().parent / "audit_logs_modules" / "modules.yaml"
    return _load_yaml_list(path)


def validate_collector_categories(
    registry: dict[str, dict[tuple[str, str], list[str]]] | None = None,
) -> list[ContractIssue]:
    """Flatten categories must correspond to real collectors, and vice versa.

    - project scope: category ↔ `list_{category}` on dataikuapi DSSProject
    - instance scope: category ↔ `list_{category}` on dataikuapi DSSClient,
      the project-inclusion list methods, or a known code-driven category
    - audit scope: category ↔ audit processors (modules.yaml) and
      event_mapping variants ↔ mapping.csv dataiku_category values
    - collection_exclusions entries must name real methods
    """

    from dataikuapi.dss.project import DSSProject
    from dataikuapi.dssclient import DSSClient

    issues: list[ContractIssue] = []
    registry = registry if registry is not None else load_flatten_registry()

    project_categories = _dataikuapi_list_categories(DSSProject)
    instance_categories = _dataikuapi_list_categories(DSSClient)
    processors = set(_audit_processor_names())
    variant_slugs = _audit_variant_slugs()

    exclusions_dir = Path(__file__).resolve().parent / "collection_exclusions"

    def _inclusion_categories() -> set[str]:
        path = exclusions_dir / "instance_project_inclusion.yaml"
        if not path.exists():
            return set()
        return {m[len("list_"):] for m in _load_yaml_list(path) if m.startswith("list_")}

    inclusion_categories = _inclusion_categories()

    for (category, module), _cols in registry.get("project", {}).items():
        if category not in project_categories:
            issues.append(
                ContractIssue(
                    severity="warning",
                    domain="collectors",
                    message=(
                        f"flatten_columns/project/{category}_{module}.yaml: no "
                        f"`list_{category}` method on dataikuapi DSSProject — config is "
                        "dead (never loaded) or the SDK dropped the method"
                    ),
                )
            )

    for (category, module), _cols in registry.get("instance", {}).items():
        known = (
            category in instance_categories
            or category in inclusion_categories
            or category in KNOWN_INSTANCE_CATEGORIES
        )
        if not known:
            issues.append(
                ContractIssue(
                    severity="warning",
                    domain="collectors",
                    message=(
                        f"flatten_columns/instance/{category}_{module}.yaml: no "
                        f"`list_{category}` on dataikuapi DSSClient, not in the "
                        "project-inclusion list, and not a known code-driven category — "
                        "config appears to have no producer"
                    ),
                )
            )

    for (category, module), _cols in registry.get("audit", {}).items():
        if category in processors:
            continue
        # event_mapping writes audit_dataiku_usage[_variant] configs.
        if category == "audit_dataiku_usage":
            continue
        if category.startswith("audit_dataiku_usage_"):
            variant = category[len("audit_dataiku_usage_"):]
            if variant not in variant_slugs:
                issues.append(
                    ContractIssue(
                        severity="warning",
                        domain="collectors",
                        message=(
                            f"flatten_columns/audit/{category}_{module}.yaml: variant "
                            f"{variant!r} is not a dataiku_category in mapping.csv"
                        ),
                    )
                )
            continue
        issues.append(
            ContractIssue(
                severity="warning",
                domain="collectors",
                message=(
                    f"flatten_columns/audit/{category}_{module}.yaml: {category!r} is "
                    "not an audit processor (modules.yaml) nor an event_mapping variant"
                ),
            )
        )

    # Exclusion / rule files must reference real methods on the right class.
    checks = [
        ("projects_data.yaml", _dataikuapi_method_names(DSSProject)),
        ("instance_data.yaml", _dataikuapi_method_names(DSSClient)),
        ("instance_project_inclusion.yaml", _dataikuapi_method_names(DSSProject)),
    ]
    for filename, valid_methods in checks:
        path = exclusions_dir / filename
        if not path.exists():
            continue
        for method in _load_yaml_list(path):
            if method not in valid_methods:
                issues.append(
                    ContractIssue(
                        severity="warning",
                        domain="collectors",
                        message=(
                            f"collection_exclusions/{filename}: {method!r} is not a "
                            "method on the corresponding dataikuapi class"
                        ),
                    )
                )

    # instance rules also cover project-inclusion methods (DSSProject methods
    # collected under instance scope), so both classes are valid there.
    for filename, valid_methods in [
        ("project_method_rules.yaml", _dataikuapi_method_names(DSSProject)),
        (
            "instance_method_rules.yaml",
            _dataikuapi_method_names(DSSClient) | _dataikuapi_method_names(DSSProject),
        ),
    ]:
        path = exclusions_dir / filename
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for method in (raw.get("methods") or {}):
            if str(method) not in valid_methods:
                issues.append(
                    ContractIssue(
                        severity="warning",
                        domain="collectors",
                        message=(
                            f"collection_exclusions/{filename}: rule for {method!r} "
                            "names a method that does not exist on the dataikuapi class"
                        ),
                    )
                )

    return issues


def _gold_specs_dir() -> Path:
    return Path(__file__).resolve().parent / "pulse_duckdb" / "gold_specs"


# Tables built by dedicated code in create-gold-tables/recipe.py (not specs).
RECIPE_BUILTIN_TABLES: frozenset[str] = frozenset(
    {
        "dim_category_to_capability",
        "dim_addon_feature_flags",
        "fact_object_activity_events",
        "base_object_activity_events",  # compat view over fact_object_activity_events
        "fact_dev_activity_events",
        "fact_user_activity_daily",
        "fact_user_activity_project_daily",
        "base_dataiku_products_registry",
    }
)


def gold_spec_names() -> set[str]:
    names: set[str] = set()
    for scope in ("project", "instance"):
        for path in sorted((_gold_specs_dir() / scope).glob("base_*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data:
                names.add(str(next(iter(data.keys()))))
    return names


def validate_gold_specs(
    registry: dict[str, dict[tuple[str, str], list[str]]] | None = None,
) -> list[ContractIssue]:
    """Gold specs must reference real silver sources, resolvable deps, and render."""

    issues: list[ContractIssue] = []
    registry = registry if registry is not None else load_flatten_registry()

    # (category, module) pairs available in silver, across all scopes.
    silver_pairs = {key for scope_map in registry.values() for key in scope_map}
    # user_activity is produced by the audit `users` processor.
    silver_pairs.add(("users", "user_activity"))

    spec_names = gold_spec_names()
    known_tables = spec_names | RECIPE_BUILTIN_TABLES

    # Lazy import: gold_builder needs duckdb, which not every caller has.
    from data_collection.pulse_duckdb.gold_builder import load_gold_spec

    for scope in ("project", "instance"):
        for path in sorted((_gold_specs_dir() / scope).glob("base_*.yaml")):
            sql_params = None
            if path.name == "base_license_limits_wide_latest.yaml":
                sql_params = {"wide_columns": ""}
            try:
                spec = load_gold_spec(path, sql_params=sql_params)
            except Exception as exc:  # noqa: BLE001 - report, don't crash validation
                issues.append(
                    ContractIssue(
                        severity="error",
                        domain="gold_specs",
                        message=f"gold_specs/{scope}/{path.name}: failed to load/render: {exc!r}",
                    )
                )
                continue

            if not spec.sql.strip():
                issues.append(
                    ContractIssue(
                        severity="error",
                        domain="gold_specs",
                        message=f"gold_specs/{scope}/{path.name}: rendered SQL is empty",
                    )
                )

            if spec.category and spec.module:
                if (spec.category, spec.module) not in silver_pairs:
                    issues.append(
                        ContractIssue(
                            severity="error",
                            domain="gold_specs",
                            message=(
                                f"gold_specs/{scope}/{path.name}: silver source "
                                f"(category={spec.category!r}, module={spec.module!r}) has "
                                "no flatten config — the silver view will never have data"
                            ),
                        )
                    )

            for dep in spec.depends_on:
                if str(dep) not in known_tables:
                    issues.append(
                        ContractIssue(
                            severity="error",
                            domain="gold_specs",
                            message=(
                                f"gold_specs/{scope}/{path.name}: depends_on {dep!r} is "
                                "neither a gold spec nor a recipe-built table"
                            ),
                        )
                    )

    return issues


def _dashboard_dir() -> Path:
    return _python_lib_dir() / "pulse_dashboard"


def _dashboard_dataset_names() -> tuple[set[str], set[str]]:
    """(base dataset names, view names) from pulse_dashboard/pulse_duckdb/datasets."""

    datasets_dir = _dashboard_dir() / "pulse_duckdb" / "datasets"
    base_names = {p.stem for p in (datasets_dir / "base").glob("*.yaml")}
    view_names = {p.stem for p in (datasets_dir / "views").glob("*.yaml")}
    return base_names, view_names


def validate_dashboard_tables() -> list[ContractIssue]:
    """Dashboard table expectations must resolve to buildable tables/views."""

    from pulse_dashboard.webapp_backend.table_registry import (
        EXPECTED_STARTUP_OBJECTS,
        OBJECT_EXTRAS_SOURCES,
    )

    issues: list[ContractIssue] = []
    base_names, view_names = _dashboard_dataset_names()
    dashboard_names = base_names | view_names
    gold_names = gold_spec_names() | RECIPE_BUILTIN_TABLES

    for name in EXPECTED_STARTUP_OBJECTS:
        if name not in dashboard_names:
            issues.append(
                ContractIssue(
                    severity="error",
                    domain="dashboard",
                    message=(
                        f"table_registry.EXPECTED_STARTUP_OBJECTS: {name!r} has no "
                        "dataset/view spec under pulse_dashboard/pulse_duckdb/datasets"
                    ),
                )
            )

    for object_type, spec in OBJECT_EXTRAS_SOURCES.items():
        table = str(spec.get("table") or "")
        if table not in gold_names:
            issues.append(
                ContractIssue(
                    severity="error",
                    domain="dashboard",
                    message=(
                        f"table_registry.OBJECT_EXTRAS_SOURCES[{object_type!r}]: table "
                        f"{table!r} is neither a gold spec nor a recipe-built table"
                    ),
                )
            )

    all_known = dashboard_names | gold_names
    for filename in ("asset_structure.yaml", "product_structure.yaml"):
        path = _dashboard_dir() / "configs" / filename
        if not path.exists():
            issues.append(
                ContractIssue(
                    severity="error",
                    domain="dashboard",
                    message=f"configs/{filename} is missing",
                )
            )
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for object_type, spec in raw.items():
            if not isinstance(spec, dict):
                continue
            table = str(spec.get("table") or "")
            if table and table not in all_known:
                issues.append(
                    ContractIssue(
                        severity="error",
                        domain="dashboard",
                        message=(
                            f"configs/{filename}: {object_type!r} references table "
                            f"{table!r} which no dataset spec, gold spec or recipe builds"
                        ),
                    )
                )

    return issues


def validate_all() -> list[ContractIssue]:
    registry = load_flatten_registry()
    issues: list[ContractIssue] = []
    issues.extend(validate_casting_columns(registry))
    issues.extend(validate_collector_categories(registry))
    issues.extend(validate_gold_specs(registry))
    issues.extend(validate_dashboard_tables())
    return issues


def format_report(issues: list[ContractIssue]) -> str:
    if not issues:
        return "contracts OK: no issues"
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    lines = [f"contracts: {len(errors)} error(s), {len(warnings)} warning(s)"]
    for issue in errors + warnings:
        lines.append(f"[{issue.severity}] {issue.domain}: {issue.message}")
    return "\n".join(lines)


def strict_mode_enabled(param_set: dict | None = None) -> bool:
    if os.environ.get("PULSE_CONTRACTS_STRICT", "") in {"1", "true", "True"}:
        return True
    if param_set and str(param_set.get("pulse_contracts_strict", "")).lower() in {"1", "true"}:
        return True
    return False


def run_startup_validation(*, param_set: dict | None = None, domains: list[str] | None = None) -> str:
    """Validate contracts at runnable/recipe startup; warn-only unless strict.

    Returns the one-line summary (for ResultTable rows). Raises RuntimeError
    in strict mode when errors are present. Never raises otherwise — a broken
    validation must not break collection.
    """

    try:
        issues = validate_all()
    except Exception as exc:  # noqa: BLE001 - validation itself must never break runs
        logger.exception("Contract validation crashed")
        return f"contracts validation crashed: {exc!r}"

    if domains:
        issues = [i for i in issues if i.domain in set(domains)]

    report = format_report(issues)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        logger.error("%s", report)
    elif issues:
        logger.warning("%s", report)
    else:
        logger.info("%s", report)

    if errors and strict_mode_enabled(param_set):
        raise RuntimeError(f"Contract validation failed in strict mode:\n{report}")

    return report.splitlines()[0]
