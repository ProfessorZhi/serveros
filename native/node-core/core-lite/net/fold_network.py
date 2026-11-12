#!/usr/bin/env python3
"""Fold a fresh network.json into a Core-Lite status document, in place.

    fold_network.py STATUS NETWORK [--max-age 900]

Adds one `network.readonly` observation (PRESENT, COMPLETE) built from
NETWORK when it exists, parses, and was sampled within MAX-AGE seconds.
Otherwise the document is left exactly as the builder wrote it: a missing
or old probe is no reading, never a fabricated one. Exit 0 either way;
the status document is what matters and must still be published.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def fold(status: dict, network: dict | None, now: datetime, max_age: float) -> dict:
    if not isinstance(network, dict) or not isinstance(network.get("paths"), list):
        return status
    try:
        sampled = datetime.fromisoformat(str(network.get("sample_time")).replace("Z", "+00:00"))
    except ValueError:
        return status
    if sampled.tzinfo is None or not 0 <= (now - sampled).total_seconds() <= max_age:
        return status
    rows = status.get("observations")
    if not isinstance(rows, list) or any(isinstance(r, dict) and r.get("name") == "network.readonly"
                                         for r in rows):
        return status
    rows.append({"name": "network.readonly", "state": "PRESENT", "completeness": "COMPLETE",
                 "value": {"paths": network["paths"], "sample_time": network["sample_time"]}})
    return status


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    status_path, network_path = argv[0], argv[1]
    max_age = float(argv[argv.index("--max-age") + 1]) if "--max-age" in argv else 900.0
    try:
        with open(network_path, encoding="utf-8") as handle:
            network = json.load(handle)
    except (OSError, ValueError):
        return 0
    with open(status_path, encoding="utf-8") as handle:
        status = json.load(handle)
    with open(status_path, "w", encoding="utf-8") as handle:
        json.dump(fold(status, network, datetime.now(timezone.utc), max_age), handle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
