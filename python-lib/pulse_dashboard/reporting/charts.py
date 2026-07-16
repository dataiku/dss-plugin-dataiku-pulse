from __future__ import annotations

import base64
from io import BytesIO
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _fig_to_base64() -> str:
    buffer = BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight", dpi=160)
    plt.close()
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def build_users_report_charts(payload: dict[str, Any]) -> dict[str, str]:
    charts: dict[str, str] = {}
    sections = payload.get("sections") or {}

    monthly = payload.get("activeMonthly", {}).get("series", []) or []
    if monthly and sections.get("includeMonthly", True):
        labels = [str(row.get("month") or "")[:7] for row in monthly]
        active_users = [float(row.get("activeUsers") or 0) for row in monthly]
        active_rate = [100.0 * float(row.get("activeRate") or 0.0) for row in monthly]

        fig, ax = plt.subplots(figsize=(8, 3.2))
        ax.plot(labels, active_users, marker="o", color="#2563eb", linewidth=2)
        ax.set_title("Monthly Active Users")
        ax.set_ylabel("Users")
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()
        charts["active_monthly_png"] = _fig_to_base64()

        fig, ax = plt.subplots(figsize=(8, 3.2))
        ax.plot(labels, active_rate, marker="o", color="#16a34a", linewidth=2)
        ax.set_title("Monthly Active Rate")
        ax.set_ylabel("Percent")
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=30)
        fig.tight_layout()
        charts["active_rate_png"] = _fig_to_base64()

    segments = payload.get("segments", {}).get("segments", []) or []
    if segments and sections.get("includeSegments", True):
        labels = [str(row.get("label") or "Unknown") for row in segments]
        values = [float(row.get("value") or 0) for row in segments]
        fig, ax = plt.subplots(figsize=(8, 3.4))
        ax.bar(labels, values, color="#7c3aed")
        ax.set_title("Observed User Segments")
        ax.set_ylabel("Users")
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=20)
        fig.tight_layout()
        charts["segments_png"] = _fig_to_base64()

    license_rows = payload.get("licenseSummary", {}).get("byLicenseGroup", []) or []
    if license_rows and sections.get("includeLicenseSummary", True):
        labels = [str(row.get("license_group") or row.get("label") or "Unknown") for row in license_rows]
        values = [float(row.get("enabled_users") or row.get("value") or 0) for row in license_rows]
        fig, ax = plt.subplots(figsize=(8, 3.4))
        ax.bar(labels, values, color="#ea580c")
        ax.set_title("Enabled Users by License Group")
        ax.set_ylabel("Users")
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=20)
        fig.tight_layout()
        charts["license_groups_png"] = _fig_to_base64()

    return charts
