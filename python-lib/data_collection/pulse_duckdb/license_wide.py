from __future__ import annotations

from pathlib import Path

import yaml


def load_license_profiles(base_dir: Path) -> list[str]:
    path = base_dir / "license_profiles.yaml"
    if not path.exists():
        return []

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"Invalid license_profiles.yaml (expected YAML list): {path}")

    profiles: list[str] = []
    for value in raw:
        token = str(value or "").strip().lower()
        if token and token not in profiles:
            profiles.append(token)
    return profiles


def build_license_wide_sql_params(base_dir: Path) -> dict[str, str]:
    profiles = load_license_profiles(base_dir)
    if not profiles:
        raise ValueError("license_profiles.yaml must define at least one known license profile")

    column_lines: list[str] = []
    for profile in profiles:
        upper_profile = profile.upper()
        column_lines.append(
            "      MAX(CASE WHEN max_licenses.license_profile = '{profile}' THEN try_cast(max_licenses.max_licenses AS BIGINT) END) AS max_licenses_{column},".format(
                profile=upper_profile,
                column=profile,
            )
        )
        column_lines.append(
            "      MAX(CASE WHEN max_licenses.license_profile = 'SUBLICENSE_{profile}' THEN try_cast(max_licenses.max_licenses AS BIGINT) END) AS sublicense_{column},".format(
                profile=upper_profile,
                column=profile,
            )
        )

    if column_lines:
        column_lines[-1] = column_lines[-1].rstrip(",")

    return {"wide_columns": ",\n" + "\n".join(column_lines)}
