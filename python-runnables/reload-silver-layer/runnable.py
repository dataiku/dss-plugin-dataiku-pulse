from __future__ import annotations

import gzip
import html
import importlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

import dataiku
import pandas as pd
from dataiku.runnables import Runnable

from data_collection.data_normalizer import check_silver_dq, normalize_silver
from data_collection.helper import build_context, ensure_output_folder, upload_json, upload_json_gzip, upload_parquet
from data_collection.helper.raw_transform import raw_to_dataframe
from data_collection.method_rules import cleanup_dataframe, load_method_rules, resolve_method_rule


logger = logging.getLogger(__name__)

SUPPORTED_RAW_EXTENSIONS = (".json", ".json.gz", ".parquet")
AUDIT_BACKUP_MODULE = "backup"
LEGACY_AUDIT_MODULES = {"legacy", "v2", "legacy_v2"}
SAMPLE_LIMIT = 5


@dataclass(frozen=True)
class RawPathInfo:
    path: str
    layer: str
    category: str | None
    module: str | None
    instance_name: str | None
    year: str | None
    month: str | None
    day: str | None
    file_key: str | None
    extension: str | None


@dataclass(frozen=True)
class ReplayResult:
    path: str
    status: str
    message: str = ""


def _clean_path(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""
    return f"/{normalized.lstrip('/')}"


def _extract_file_key(name: str) -> tuple[str | None, str | None]:
    lowered = name.lower()
    for extension in SUPPORTED_RAW_EXTENSIONS:
        if lowered.endswith(extension):
            return name[: -len(extension)], extension
    if "." in name:
        stem, suffix = name.rsplit(".", 1)
        return stem or None, f".{suffix}"
    return name or None, None


def _parse_raw_path(path: str) -> RawPathInfo | None:
    cleaned = _clean_path(path)
    parts = PurePosixPath(cleaned).parts
    if not parts:
        return None

    layer = parts[1] if parts and parts[0] == "/" and len(parts) > 1 else parts[0].lstrip("/")
    if layer != "raw":
        return None

    values: dict[str, str] = {}
    filename: str | None = None
    for part in parts[2:] if parts and parts[0] == "/" else parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            if key and value:
                values[key] = value
            continue
        filename = part

    file_key, extension = _extract_file_key(filename or "")
    return RawPathInfo(
        path=cleaned,
        layer=layer,
        category=values.get("category"),
        module=values.get("module"),
        instance_name=values.get("instance_name"),
        year=values.get("year"),
        month=values.get("month"),
        day=values.get("day"),
        file_key=file_key,
        extension=extension,
    )


def _is_legacy_audit_candidate(info: RawPathInfo | None) -> bool:
    if info is None or info.category != "audit_logs":
        return False
    module_name = str(info.module or "").strip().lower()
    return module_name in LEGACY_AUDIT_MODULES


def _classify_raw_path(info: RawPathInfo | None) -> tuple[str, str]:
    if info is None:
        return "unsupported", "Unrecognized path layout"

    required_values = [
        info.category,
        info.module,
        info.instance_name,
        info.year,
        info.month,
        info.day,
        info.file_key,
    ]
    if any(not value for value in required_values):
        return "unsupported", "Missing required raw partition fields"

    if info.category == "audit_logs" and info.module == AUDIT_BACKUP_MODULE:
        return "audit_candidate", "Audit raw backup detected"

    if _is_legacy_audit_candidate(info):
        return "legacy_audit_candidate", "Legacy audit raw candidate detected"

    if info.category == "audit_logs":
        return "unsupported", "Audit raw path not in current backup format"

    return "metadata_candidate", "Metadata-style raw file candidate"


def _sample_list(paths: list[str], *, limit: int = SAMPLE_LIMIT) -> str:
    if not paths:
        return "<em>None</em>"
    items = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in paths[:limit])
    suffix = "" if len(paths) <= limit else f"<li><em>... {len(paths) - limit} more</em></li>"
    return f"<ul>{items}{suffix}</ul>"


def _replace_layer(path: str, *, new_layer: str) -> str:
    parts = list(PurePosixPath(path).parts)
    if parts and parts[0] == "/":
        if len(parts) < 2:
            raise ValueError(f"Cannot replace layer in path {path!r}")
        parts[1] = new_layer
    else:
        if not parts:
            raise ValueError(f"Cannot replace layer in path {path!r}")
        parts[0] = new_layer
    return str(PurePosixPath(*parts))


def _swap_extension(path: str, *, new_suffix: str) -> str:
    pure_path = PurePosixPath(path)
    name = pure_path.name
    file_key, _ = _extract_file_key(name)
    if not file_key:
        raise ValueError(f"Could not derive file key from {path!r}")
    return str(pure_path.with_name(f"{file_key}.{new_suffix}"))


def _raw_path_to_silver_path(raw_path: str) -> str:
    return _swap_extension(_replace_layer(raw_path, new_layer="silver"), new_suffix="parquet")


def _raw_path_to_silver_fail_path(raw_path: str) -> str:
    return _swap_extension(_replace_layer(raw_path, new_layer="silver_fail"), new_suffix="parquet")


def _raw_path_to_silver_fail_reason_path(raw_path: str) -> str:
    return _swap_extension(_replace_layer(raw_path, new_layer="silver_fail"), new_suffix="dq.json")


def _audit_group_path(
    raw_path: str,
    *,
    layer: str,
    processor_name: str,
    module_name: str,
    event_date: date,
    suffix: str,
) -> str:
    info = _parse_raw_path(raw_path)
    if info is None or not info.instance_name:
        raise ValueError(f"Cannot build audit path from {raw_path!r}")
    return str(
        PurePosixPath("/")
        / layer
        / f"category={processor_name}"
        / f"module={module_name}"
        / f"instance_name={info.instance_name}"
        / f"year={event_date.year:04d}"
        / f"month={event_date.month:02d}"
        / f"day={event_date.day:02d}"
        / f"{info.file_key}.{suffix}"
    )


def _read_managed_folder_json(folder: dataiku.Folder, path: str, extension: str | None) -> Any:
    with folder.get_download_stream(path) as stream:
        payload = stream.read()

    if extension == ".json.gz":
        payload = gzip.decompress(payload)

    if extension in {".json", ".json.gz"}:
        return json.loads(payload.decode("utf-8"))

    raise ValueError(f"Unsupported raw extension for replay: {extension!r}")


def _parse_run_date(info: RawPathInfo) -> date:
    return date(int(info.year or 0), int(info.month or 0), int(info.day or 0))


def _derive_run_ts(payload: Any, fallback_date: date) -> str:
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            for key in ["run_ts", "pulse_run_ts"]:
                value = first.get(key)
                if value:
                    return str(value)
    if isinstance(payload, dict):
        for key in ["run_ts", "pulse_run_ts"]:
            value = payload.get(key)
            if value:
                return str(value)
    return f"{fallback_date.isoformat()}T00:00:00Z"


def _load_yaml_list(path: Path) -> list[str]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    raise ValueError(f"Expected YAML list in {path}, got {type(raw)!r}")


def _load_processor_names() -> list[str]:
    from importlib.resources import as_file, files

    modules_res = files("data_collection.audit_logs_modules").joinpath("modules.yaml")
    with as_file(modules_res) as path:
        return _load_yaml_list(Path(path))


def _load_processors(names: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
    processors: dict[str, Any] = {}
    messages: dict[str, str] = {}
    for name in names:
        try:
            processors[name] = importlib.import_module(f"data_collection.audit_logs_modules.{name}")
        except Exception as exc:
            logger.exception("Failed loading audit processor %s", name)
            messages[name] = repr(exc)
    return processors, messages


def _build_replay_context(info: RawPathInfo, run_ts: str):
    return type(
        "ReplayContext",
        (),
        {
            "scope": info.module or "project",
            "instance_name": info.instance_name,
            "run_ts": run_ts,
            "param_set": {},
            "project_key": info.file_key,
            "worker_project_key": None,
            "since": None,
        },
    )()


def _record_result(
    *,
    result: ReplayResult,
    counts: dict[str, int],
    unsupported_reasons: dict[str, int],
) -> None:
    if result.status in counts:
        counts[result.status] += 1
    elif result.status == "unsupported":
        counts["unsupported"] += 1
        unsupported_reasons[result.message] = unsupported_reasons.get(result.message, 0) + 1
    elif result.status == "replay_failed":
        counts["replay_failed"] += 1


def _replay_metadata_file(*, folder: dataiku.Folder, target: Any, info: RawPathInfo, raw_path: str) -> ReplayResult:
    if info.extension not in {".json", ".json.gz"}:
        return ReplayResult(raw_path, "unsupported", f"Unsupported metadata raw extension {info.extension!r}")

    payload = _read_managed_folder_json(folder, raw_path, info.extension)
    run_date = _parse_run_date(info)
    run_ts = _derive_run_ts(payload, run_date)

    method_name = f"list_{info.category}"
    rule = resolve_method_rule(method_name, load_method_rules(info.module or "project"))
    artifact_name = rule.artifact_key or method_name
    prefix = f"{artifact_name.replace('list_', '', 1)}_"

    raw_df = raw_to_dataframe(payload, prefix=prefix)
    raw_df = cleanup_dataframe(rule, raw_df, _build_replay_context(info, run_ts))

    if raw_df.shape[0] == 0:
        return ReplayResult(raw_path, "empty_dataframe", "Replay produced an empty dataframe")

    silver_df = normalize_silver(
        df=raw_df,
        instance_name=str(info.instance_name),
        run_ts=run_ts,
        category=str(info.category),
        module=str(info.module),
        todo_section=str(info.module),
    )
    dq = check_silver_dq(silver_df)

    if dq.ok:
        target_path = _raw_path_to_silver_path(raw_path)
        upload_parquet(
            target=target,
            output_path=PurePosixPath(target_path),
            output_base_dir=PurePosixPath("/"),
            df=silver_df,
            compression="snappy",
        )
        return ReplayResult(raw_path, "silver_written", target_path)

    upload_parquet(
        target=target,
        output_path=PurePosixPath(_raw_path_to_silver_fail_path(raw_path)),
        output_base_dir=PurePosixPath("/"),
        df=silver_df,
        compression="snappy",
    )
    upload_json(
        target=target,
        output_path=PurePosixPath(_raw_path_to_silver_fail_reason_path(raw_path)),
        output_base_dir=PurePosixPath("/"),
        payload={
            "instance_name": info.instance_name,
            "project_key": info.file_key,
            "run_ts": run_ts,
            "category": info.category,
            "module": info.module,
            "rows": int(silver_df.shape[0]),
            "cols": int(silver_df.shape[1]),
            "dq_errors": dq.errors,
            "raw_path": raw_path,
        },
    )
    return ReplayResult(raw_path, "silver_failed_dq", ", ".join(dq.errors))


def _resolve_audit_normalize_args(*, processor_name: str, module_name: str) -> dict[str, Any]:
    if processor_name == "event_mapping":
        return {
            "category": "audit_dataiku_usage",
            "module": "audit_metadata",
            "todo_section": "audit",
            "flatten_base": ("audit_dataiku_usage", "audit_metadata"),
            "flatten_variant": module_name,
        }
    return {
        "category": processor_name,
        "module": module_name,
        "todo_section": "audit",
    }


def _resolve_event_date(*, silver_df: pd.DataFrame, fallback_date: date) -> date:
    if "timestamp" in silver_df.columns:
        event_ts = pd.to_datetime(silver_df["timestamp"].max(), utc=True, errors="coerce")
        if not pd.isna(event_ts):
            return event_ts.date()
    return fallback_date


def _normalize_legacy_audit_payload(payload: Any, *, info: RawPathInfo) -> tuple[pd.DataFrame | None, str | None]:
    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        if not rows:
            return None, "Legacy audit payload list has no dict rows"
        legacy_df = pd.DataFrame(rows)
    elif isinstance(payload, dict):
        candidate_rows = None
        for key in ["records", "events"]:
            if isinstance(payload.get(key), list):
                candidate_rows = [item for item in payload.get(key, []) if isinstance(item, dict)]
                if candidate_rows:
                    break
        if not candidate_rows:
            return None, "Legacy audit payload shape is unsupported"
        legacy_df = pd.DataFrame(candidate_rows)
    else:
        return None, "Legacy audit payload is not a list or dict"

    out = legacy_df.copy()
    out["instance_name"] = out.get("instance_name", pd.Series(info.instance_name, index=out.index)).fillna(info.instance_name)

    if "message" in out.columns and out["message"].apply(lambda value: isinstance(value, dict)).any():
        message_df = pd.json_normalize(out["message"]).add_prefix("message_")
        message_df.index = out.index
        out = out.drop(columns=["message"], errors="ignore")
        out = pd.concat([out, message_df], axis=1)
    else:
        alias_map = {
            "message_msgType": ["msgType", "msgtype", "messageType"],
            "message_msgTypeBase": ["msgTypeBase", "msgtypebase", "messageTypeBase"],
            "message_login": ["login", "user", "username", "authUser"],
            "message_authSource": ["authSource"],
            "message_project_key": ["project_key", "projectKey"],
            "message_projectKey": ["project_key", "projectKey"],
            "message_scenarioId": ["scenarioId"],
            "message_jobId": ["jobId"],
        }
        for target_col, candidates in alias_map.items():
            if target_col in out.columns:
                continue
            for candidate in candidates:
                if candidate in out.columns:
                    out[target_col] = out[candidate]
                    break

    if "timestamp" not in out.columns:
        for candidate in ["eventTime", "time", "date", "ts"]:
            if candidate in out.columns:
                out["timestamp"] = out[candidate]
                break

    if "timestamp" not in out.columns:
        return None, "Legacy audit payload has no timestamp column"

    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True, errors="coerce")
    out = out[out["timestamp"].notna()].copy()
    if out.shape[0] == 0:
        return None, "Legacy audit payload has no valid timestamps"

    if "date" not in out.columns:
        out["date"] = out["timestamp"].dt.date

    return out.reset_index(drop=True), None


def _replay_audit_dataframe(*, raw_df: pd.DataFrame, target: Any, info: RawPathInfo, raw_path: str, run_date: date, run_ts: str) -> list[ReplayResult]:
    processor_names = _load_processor_names()
    processors, processor_messages = _load_processors(processor_names)
    results: list[ReplayResult] = []

    for processor_name in processor_names:
        processor = processors.get(processor_name)
        if processor is None:
            results.append(
                ReplayResult(raw_path, "audit_processor_failed", f"{processor_name}: {processor_messages.get(processor_name, 'not loaded')}")
            )
            continue

        try:
            out_df = processor.main(raw_df.copy())
        except Exception as exc:
            logger.exception("Audit processor %s failed for %s", processor_name, raw_path)
            results.append(ReplayResult(raw_path, "audit_processor_failed", f"{processor_name}: {repr(exc)}"))
            continue

        if out_df is None or not isinstance(out_df, pd.DataFrame) or out_df.shape[0] == 0:
            results.append(ReplayResult(raw_path, "audit_processor_empty", processor_name))
            continue

        if "dataiku_category" not in out_df.columns:
            results.append(ReplayResult(raw_path, "audit_processor_failed", f"{processor_name}: missing dataiku_category"))
            continue

        for module_name, group_df in out_df.groupby("dataiku_category"):
            module_name_str = str(module_name)
            normalize_kwargs = _resolve_audit_normalize_args(processor_name=processor_name, module_name=module_name_str)
            silver_df = normalize_silver(
                df=group_df,
                instance_name=str(info.instance_name),
                run_ts=run_ts,
                **normalize_kwargs,
            )
            event_date = _resolve_event_date(silver_df=silver_df, fallback_date=run_date)
            dq = check_silver_dq(silver_df)

            if dq.ok:
                silver_path = _audit_group_path(
                    raw_path,
                    layer="silver",
                    processor_name=processor_name,
                    module_name=module_name_str,
                    event_date=event_date,
                    suffix="parquet",
                )
                upload_parquet(
                    target=target,
                    output_path=PurePosixPath(silver_path),
                    output_base_dir=PurePosixPath("/"),
                    df=silver_df,
                    compression="snappy",
                )
                results.append(ReplayResult(raw_path, "audit_silver_written", silver_path))
                continue

            fail_path = _audit_group_path(
                raw_path,
                layer="silver_fail",
                processor_name=processor_name,
                module_name=module_name_str,
                event_date=event_date,
                suffix="parquet",
            )
            fail_reason_path = _audit_group_path(
                raw_path,
                layer="silver_fail",
                processor_name=processor_name,
                module_name=module_name_str,
                event_date=event_date,
                suffix="dq.json.gz",
            )
            upload_parquet(
                target=target,
                output_path=PurePosixPath(fail_path),
                output_base_dir=PurePosixPath("/"),
                df=silver_df,
                compression="snappy",
            )
            upload_json_gzip(
                target=target,
                output_path=PurePosixPath(fail_reason_path),
                output_base_dir=PurePosixPath("/"),
                payload={
                    "instance_name": info.instance_name,
                    "run_ts": run_ts,
                    "processor": processor_name,
                    "module": module_name_str,
                    "rows": int(silver_df.shape[0]),
                    "cols": int(silver_df.shape[1]),
                    "dq_errors": dq.errors,
                    "raw_path": raw_path,
                },
            )
            results.append(
                ReplayResult(
                    raw_path,
                    "audit_silver_failed_dq",
                    f"{processor_name}/{module_name_str}: {', '.join(dq.errors)}",
                )
            )

    return results


def _replay_audit_file(*, folder: dataiku.Folder, target: Any, info: RawPathInfo, raw_path: str) -> list[ReplayResult]:
    if info.extension != ".json.gz":
        return [ReplayResult(raw_path, "unsupported", f"Unsupported audit raw extension {info.extension!r}")]

    payload = _read_managed_folder_json(folder, raw_path, info.extension)
    if not isinstance(payload, list):
        raise ValueError(f"Expected audit raw payload list in {raw_path!r}")

    raw_df = pd.DataFrame(payload)
    if raw_df.shape[0] == 0:
        return [ReplayResult(raw_path, "empty_dataframe", "Audit raw payload is empty")]

    run_date = _parse_run_date(info)
    run_ts = _derive_run_ts(payload, run_date)
    return _replay_audit_dataframe(
        raw_df=raw_df,
        target=target,
        info=info,
        raw_path=raw_path,
        run_date=run_date,
        run_ts=run_ts,
    )


def _replay_legacy_audit_file(*, folder: dataiku.Folder, target: Any, info: RawPathInfo, raw_path: str) -> list[ReplayResult]:
    if info.extension not in {".json", ".json.gz"}:
        return [ReplayResult(raw_path, "legacy_audit_skipped", f"Unsupported legacy audit extension {info.extension!r}")]

    payload = _read_managed_folder_json(folder, raw_path, info.extension)
    adapted_df, skip_reason = _normalize_legacy_audit_payload(payload, info=info)
    if adapted_df is None:
        return [ReplayResult(raw_path, "legacy_audit_skipped", skip_reason or "Legacy audit payload could not be adapted")]

    run_date = _parse_run_date(info)
    run_ts = _derive_run_ts(payload, run_date)
    results = _replay_audit_dataframe(
        raw_df=adapted_df,
        target=target,
        info=info,
        raw_path=raw_path,
        run_date=run_date,
        run_ts=run_ts,
    )
    return [ReplayResult(raw_path, "legacy_audit_converted", f"Adapted {adapted_df.shape[0]} rows")] + results


def _build_summary_html(
    *,
    folder_id: str,
    project_key: str,
    folder_lookup: str,
    counts: dict[str, int],
    samples: dict[str, list[str]],
    replay_results: list[ReplayResult],
    unsupported_reasons: dict[str, int],
) -> str:
    reason_items = "".join(
        f"<li>{html.escape(reason)}: <strong>{count}</strong></li>"
        for reason, count in sorted(unsupported_reasons.items())
    ) or "<li><em>None</em></li>"

    replay_rows = "".join(
        f"<tr><td><code>{html.escape(item.path)}</code></td><td>{html.escape(item.status)}</td><td>{html.escape(item.message)}</td></tr>"
        for item in replay_results[:100]
    ) or "<tr><td colspan='3'><em>No replayed files</em></td></tr>"

    return "".join(
        [
            "<div>",
            "<h2>Reload Silver Layer Replay</h2>",
            f"<p>Managed folder <code>{html.escape(folder_lookup)}</code> "
            f"in project <code>{html.escape(project_key)}</code> "
            f"(resolved id <code>{html.escape(folder_id)}</code>).</p>",
            "<table border='1' cellpadding='6' cellspacing='0'>",
            "<tr><th>Metric</th><th>Count</th></tr>",
            f"<tr><td>Metadata candidates discovered</td><td>{counts['metadata_candidate']}</td></tr>",
            f"<tr><td>Audit candidates discovered</td><td>{counts['audit_candidate']}</td></tr>",
            f"<tr><td>Legacy audit candidates discovered</td><td>{counts['legacy_audit_candidate']}</td></tr>",
            f"<tr><td>Legacy audit converted</td><td>{counts['legacy_audit_converted']}</td></tr>",
            f"<tr><td>Legacy audit skipped</td><td>{counts['legacy_audit_skipped']}</td></tr>",
            f"<tr><td>Metadata silver written</td><td>{counts['silver_written']}</td></tr>",
            f"<tr><td>Metadata silver failed DQ</td><td>{counts['silver_failed_dq']}</td></tr>",
            f"<tr><td>Audit silver written</td><td>{counts['audit_silver_written']}</td></tr>",
            f"<tr><td>Audit silver failed DQ</td><td>{counts['audit_silver_failed_dq']}</td></tr>",
            f"<tr><td>Audit processor empty</td><td>{counts['audit_processor_empty']}</td></tr>",
            f"<tr><td>Audit processor failed</td><td>{counts['audit_processor_failed']}</td></tr>",
            f"<tr><td>Replay failed</td><td>{counts['replay_failed']}</td></tr>",
            f"<tr><td>Metadata empty dataframes</td><td>{counts['empty_dataframe']}</td></tr>",
            f"<tr><td>Unsupported / unrecognized</td><td>{counts['unsupported']}</td></tr>",
            f"<tr><td>Total raw files discovered</td><td>{counts['total_raw']}</td></tr>",
            "</table>",
            (
                "<p><strong>Audit fallback:</strong> no current-format audit raw backups were found; "
                "use <code>load-event-server-history</code> if audit history must be rebuilt from source logs.</p>"
                if counts["audit_candidate"] == 0 and counts["legacy_audit_candidate"] == 0
                else ""
            ),
            "<h3>Replay Results</h3>",
            "<table border='1' cellpadding='6' cellspacing='0'>",
            "<tr><th>Raw Path</th><th>Status</th><th>Details</th></tr>",
            replay_rows,
            "</table>",
            "<h3>Metadata Samples</h3>",
            _sample_list(samples["metadata_candidate"]),
            "<h3>Audit Samples</h3>",
            _sample_list(samples["audit_candidate"]),
            "<h3>Legacy Audit Samples</h3>",
            _sample_list(samples["legacy_audit_candidate"]),
            "<h3>Unsupported Samples</h3>",
            _sample_list(samples["unsupported"]),
            "<h3>Unsupported Reasons</h3>",
            f"<ul>{reason_items}</ul>",
            "</div>",
        ]
    )


class MyRunnable(Runnable):
    """Replay metadata, current audit, and best-effort legacy audit raw files into matching silver outputs."""

    def __init__(self, project_key: str, config: dict[str, Any] | None, plugin_config: dict[str, Any] | None):
        self.project_key = project_key
        self.config = config or {}
        self.plugin_config = plugin_config or {}
        self.param_set = self.plugin_config.get("pulse_primary", {}) or {}

    def get_progress_target(self):
        return None

    def run(self, progress_callback):
        ctx = build_context(plugin_config=self.plugin_config)
        target = ensure_output_folder(param_set=self.param_set, remote_client=ctx.remote_client)

        folder = dataiku.Folder(
            lookup=target.folder_lookup,
            project_key=target.project_key,
            ignore_flow=True,
        )
        folder_id = folder.get_id()

        try:
            all_paths = [str(path) for path in folder.list_paths_in_partition("NP") or []]
        except Exception as exc:
            raise RuntimeError(
                f"Could not list managed folder paths for {target.folder_lookup!r} in project {target.project_key!r}"
            ) from exc

        raw_paths = sorted({_clean_path(path) for path in all_paths if _clean_path(path).startswith("/raw/")})
        counts = {
            "metadata_candidate": 0,
            "audit_candidate": 0,
            "legacy_audit_candidate": 0,
            "legacy_audit_converted": 0,
            "legacy_audit_skipped": 0,
            "silver_written": 0,
            "silver_failed_dq": 0,
            "audit_silver_written": 0,
            "audit_silver_failed_dq": 0,
            "audit_processor_empty": 0,
            "audit_processor_failed": 0,
            "replay_failed": 0,
            "empty_dataframe": 0,
            "unsupported": 0,
            "total_raw": len(raw_paths),
        }
        samples = {
            "metadata_candidate": [],
            "audit_candidate": [],
            "legacy_audit_candidate": [],
            "unsupported": [],
        }
        unsupported_reasons: dict[str, int] = {}
        replay_results: list[ReplayResult] = []

        for index, path in enumerate(raw_paths, start=1):
            progress_callback(index)
            parsed = _parse_raw_path(path)
            bucket, reason = _classify_raw_path(parsed)

            if bucket == "metadata_candidate":
                counts["metadata_candidate"] += 1
                if len(samples[bucket]) < SAMPLE_LIMIT:
                    samples[bucket].append(path)
                try:
                    replay = _replay_metadata_file(folder=folder, target=target, info=parsed, raw_path=path)
                except Exception as exc:
                    logger.exception("Metadata replay failed for %s", path)
                    replay = ReplayResult(path, "replay_failed", repr(exc))
                replay_results.append(replay)
                _record_result(result=replay, counts=counts, unsupported_reasons=unsupported_reasons)
                continue

            if bucket == "audit_candidate":
                counts["audit_candidate"] += 1
                if len(samples[bucket]) < SAMPLE_LIMIT:
                    samples[bucket].append(path)
                try:
                    audit_results = _replay_audit_file(folder=folder, target=target, info=parsed, raw_path=path)
                except Exception as exc:
                    logger.exception("Audit replay failed for %s", path)
                    audit_results = [ReplayResult(path, "audit_processor_failed", repr(exc))]
                replay_results.extend(audit_results)
                for result in audit_results:
                    _record_result(result=result, counts=counts, unsupported_reasons=unsupported_reasons)
                continue

            if bucket == "legacy_audit_candidate":
                counts["legacy_audit_candidate"] += 1
                if len(samples[bucket]) < SAMPLE_LIMIT:
                    samples[bucket].append(path)
                try:
                    audit_results = _replay_legacy_audit_file(folder=folder, target=target, info=parsed, raw_path=path)
                except Exception as exc:
                    logger.exception("Legacy audit replay failed for %s", path)
                    audit_results = [ReplayResult(path, "legacy_audit_skipped", repr(exc))]
                replay_results.extend(audit_results)
                for result in audit_results:
                    _record_result(result=result, counts=counts, unsupported_reasons=unsupported_reasons)
                continue

            counts[bucket] += 1
            if len(samples[bucket]) < SAMPLE_LIMIT:
                samples[bucket].append(path)
            if bucket == "unsupported":
                unsupported_reasons[reason] = unsupported_reasons.get(reason, 0) + 1

        logger.info(
            "reload-silver-layer replay finished for folder %s: metadata_written=%s audit_written=%s metadata_dq_failures=%s audit_dq_failures=%s legacy_converted=%s legacy_skipped=%s replay_failed=%s unsupported=%s",
            target.folder_lookup,
            counts["silver_written"],
            counts["audit_silver_written"],
            counts["silver_failed_dq"],
            counts["audit_silver_failed_dq"],
            counts["legacy_audit_converted"],
            counts["legacy_audit_skipped"],
            counts["replay_failed"],
            counts["unsupported"],
        )

        return _build_summary_html(
            folder_id=folder_id,
            project_key=target.project_key,
            folder_lookup=target.folder_lookup,
            counts=counts,
            samples=samples,
            replay_results=replay_results,
            unsupported_reasons=unsupported_reasons,
        )
