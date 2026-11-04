"""Hosted-Windows acceptance for the Windows collector and status builder.

    python ci_check.py snapshot SNAPSHOT.json
    python ci_check.py status   STATUS.json

Each document is validated against its contract schema, then checked for the
facts a real Windows host must produce: CPU, memory and the system drive are
PRESENT; swap is UNAVAILABLE with a reason (never zero); on the status side the
principal and device are MATCHED, psi is UNSUPPORTED_PLATFORM and nothing
granted is missing. Green here is HOSTED_WINDOWS evidence, not REAL_DEVICE.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema

CONTRACT = Path(__file__).resolve().parents[2] / "contract"


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def check_snapshot(doc: dict) -> None:
    schema = json.loads((CONTRACT / "node-resource-snapshot.v1.schema.json").read_text("utf-8"))
    jsonschema.validate(doc, schema)
    if doc["collector"]["live"] is not True:
        fail("a snapshot of this host must say live=true")
    if doc["cpu"].get("state") != "PRESENT":
        fail(f"cpu is not PRESENT: {doc['cpu']}")
    if doc["memory"].get("state") != "PRESENT" or doc["memory"].get("source") != "GlobalMemoryStatusEx":
        fail(f"memory is not PRESENT from GlobalMemoryStatusEx: {doc['memory']}")
    if not 0 < doc["memory"]["total_bytes"]:
        fail("memory total is not positive")
    roles = {row["role"]: row for row in doc["filesystems"]["roles"]}
    root = roles.get("ROOT_FS")
    if not root or root.get("state") != "PRESENT" or not root.get("total_bytes"):
        fail(f"the system drive is not PRESENT: {root}")
    if doc["swap"].get("state") != "UNAVAILABLE" or "windows" not in doc["swap"].get("reason", ""):
        fail(f"swap must be UNAVAILABLE with a windows reason, never a number: {doc['swap']}")
    print(f"snapshot ok: cpu {doc['cpu'].get('used_percent')}%, memory "
          f"{doc['memory'].get('used_percent')}%, C: {root.get('used_percent')}%")


def check_status(doc: dict) -> None:
    schema = json.loads((CONTRACT / "core-lite-status.v1.schema.json").read_text("utf-8"))
    jsonschema.validate(doc, schema)
    if doc["platform"] != "windows":
        fail("the status is not for a windows profile")
    for key in ("principal_identity", "device_identity"):
        if doc[key].get("state") != "MATCHED":
            fail(f"{key} is not MATCHED: {doc[key]}")
    states = {row["name"]: row for row in doc["observations"]}
    for name in ("cpu", "memory", "storage.readonly"):
        if states.get(name, {}).get("state") != "PRESENT":
            fail(f"{name} is not PRESENT: {states.get(name)}")
    if states.get("psi.memory", {}).get("reason") != "UNSUPPORTED_PLATFORM":
        fail(f"psi on windows must be UNSUPPORTED_PLATFORM: {states.get('psi.memory')}")
    if states.get("swap", {}).get("reason") != "NO_SOURCE":
        fail(f"swap the collector could not read must be NO_SOURCE: {states.get('swap')}")
    print("status ok: " + ", ".join(f"{n}={r['state']}" for n, r in states.items()))


def main() -> None:
    kind, path = sys.argv[1], Path(sys.argv[2])
    doc = json.loads(path.read_text("utf-8-sig"))
    {"snapshot": check_snapshot, "status": check_status}[kind](doc)


if __name__ == "__main__":
    main()
