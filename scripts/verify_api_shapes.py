#!/usr/bin/env python3
"""Dev tool: capture the live DSS API shapes the notifications module depends on.

Never assume reporter/channel payload shapes from docs — run this against a dev
DSS (ideally the OLDEST version in the fleet) and compare the output with the
templates in `python-lib/pulse_init/notifications.py` before releasing.

Usage:
    DSS_URL=https://... DSS_API_KEY=... python scripts/verify_api_shapes.py [PROJECT_KEY]

What it captures:
1. `client.list_messaging_channels()` item shapes (id / type / family), and
   whether the `channel_family=` filter kwarg is supported.
2. The raw reporter dict of every scenario in PROJECT_KEY (create one in the
   DSS UI first: "at scenario end", outcome FAILED/ABORTED, mail channel) —
   this JSON is the template `build_failure_reporter` must produce.
"""

from __future__ import annotations

import json
import os
import sys

import dataikuapi


def main() -> int:
    url = os.environ.get("DSS_URL")
    api_key = os.environ.get("DSS_API_KEY")
    if not url or not api_key:
        print("Set DSS_URL and DSS_API_KEY", file=sys.stderr)
        return 2

    client = dataikuapi.DSSClient(url, api_key)
    print(f"# DSS at {url}")
    try:
        print(f"# DSS version: {client.get_instance_info().raw.get('dssVersion')}")
    except Exception as exc:  # noqa: BLE001
        print(f"# instance info unavailable: {exc!r}")

    print("\n## 1) list_messaging_channels()")
    try:
        channels = client.list_messaging_channels()
    except AttributeError:
        print("!! client.list_messaging_channels is MISSING on this DSS/dataikuapi version")
        channels = []
    except Exception as exc:  # noqa: BLE001
        print(f"!! list_messaging_channels failed: {exc!r}")
        channels = []

    for ch in channels:
        raw = getattr(ch, "settings", None)
        print(
            json.dumps(
                {
                    "id": getattr(ch, "id", None),
                    "type": getattr(ch, "type", None),
                    "family": getattr(ch, "family", None),
                    "class": type(ch).__name__,
                    "raw_keys": sorted(raw.keys()) if isinstance(raw, dict) else None,
                },
                indent=2,
            )
        )

    print("\n## 1b) channel_family= kwarg")
    try:
        mail = client.list_messaging_channels(channel_family="mail")
        print(f"channel_family='mail' supported, {len(mail)} channel(s)")
    except TypeError as exc:
        print(f"!! channel_family kwarg NOT supported: {exc!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"!! channel_family kwarg failed: {exc!r}")

    project_key = sys.argv[1] if len(sys.argv) > 1 else None
    if not project_key:
        print("\n(pass a PROJECT_KEY argument to also dump scenario reporter shapes)")
        return 0

    print(f"\n## 2) raw reporters of scenarios in {project_key}")
    project = client.get_project(project_key)
    for item in project.list_scenarios():
        scenario_id = item.get("id") if isinstance(item, dict) else item
        scenario = project.get_scenario(scenario_id)
        settings = scenario.get_settings()
        raw = settings.get_raw()
        reporters = raw.get("reporters")
        print(f"\n### scenario {scenario_id} ({raw.get('name')!r})")
        print(f"has raw_reporters attr: {hasattr(settings, 'raw_reporters')}")
        print(json.dumps(reporters, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
