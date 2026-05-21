from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List

from dataikuapi.dss.project import DSSProject

from .introspection import get_noarg_list_methods

from data_collection.exclusion_config import load_exclusions
from data_collection.helper import DSSFolderTarget, OutputLayout
from data_collection.method_rules import MethodCallContext, MethodCollectResult, collect_method_output


@dataclass(frozen=True)
class CollectResult:
    project_key: str
    collected: List[str]
    errors: Dict[str, str]
    method_results: List[MethodCollectResult]


def collect_project_list_methods(
    *,
    project: DSSProject,
    project_key: str,
    output_base_dir: Path,
    instance_name: str,
    run_ts: str,
    run_date: date,
    since: datetime | None = None,
    output_folder_target: DSSFolderTarget = DSSFolderTarget(project_key="DATA_COLLECTION"),
    debug_dir: Path | None = None,
) -> CollectResult:
    """Collect all configured project-level list_* outputs for a project handle."""

    layout = OutputLayout(base_dir=output_base_dir, module="project_metadata")
    methods = get_noarg_list_methods(project)
    excluded = set(load_exclusions("projects_data").excluded_methods)

    collected: List[str] = []
    errors: Dict[str, str] = {}
    method_results: List[MethodCollectResult] = []

    for method_name, fn in sorted(methods.items()):
        if method_name in excluded:
            method_results.append(MethodCollectResult(method_name, "project", "excluded", 0, 0, 0))
            continue

        result = collect_method_output(
            fn=fn,
            method_name=method_name,
            file_key=project_key,
            layout=layout,
            target=output_folder_target,
            context=MethodCallContext(
                scope="project",
                instance_name=instance_name,
                run_ts=run_ts,
                param_set={},
                project_key=project_key,
                since=since,
            ),
            run_date=run_date,
            todo_section="project",
            debug_dir=debug_dir,
        )
        method_results.append(result)
        if result.status == "silver_written":
            collected.append(method_name)
        elif result.status == "call_failed":
            errors[method_name] = result.message or "call_failed"

    return CollectResult(
        project_key=project_key,
        collected=collected,
        errors=errors,
        method_results=method_results,
    )
