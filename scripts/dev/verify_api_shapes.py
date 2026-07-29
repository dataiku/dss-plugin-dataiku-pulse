#!/usr/bin/env python3
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
    except Exception as exc:
        print(f"# instance info unavailable: {exc!r}")

    print("\n## messaging channels")
    try:
        channels = client.list_messaging_channels()
    except Exception as exc:
        print(f"!! list_messaging_channels failed: {exc!r}")
        channels = []

    for ch in channels:
        print(
            json.dumps(
                {
                    "id": getattr(ch, "id", None),
                    "name": getattr(ch, "name", None),
                    "type": getattr(ch, "type", None),
                    "family": getattr(ch, "family", None),
                },
                indent=2,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
