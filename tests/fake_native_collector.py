"""A fake native collector, because the boundary under test is a process boundary.

Why a process and not a mock
----------------------------
What the adapter does with a collector that exits 2, or hangs, or prints half a
document is the whole of what it is for, and none of those is a property of a
Python object. `runtime/bounded_invocation.invoke_once` decides them by
reaping a real child, so a test that replaced it would be testing the mock. This
is the same call the two other process-level fakes in this directory make
(`tests/fake_codex_app_server.py`, `tests/fake_node_control.py`).

Modes come from `SERVEROS_FAKE_NATIVE_MODE` and are read per run, which is what
lets one wrapper executable serve every case in
`tests/test_native_node_core_adapter.py`:

  ok              the committed fixture, unchanged
  wrong-schema    the same document under a different `schema`
  wrong-collector the same document attributed to another collector
  badjson         bytes that are not JSON
  notjson         a plain string
  empty           nothing at all, exit 0
  exit2           a failure on stderr, exit 2
  hang            sleeps far past any test's timeout
  huge            far more bytes than any caller's bound
  live-now        the fixture, with `observed_at` stamped by THIS child's clock

It ignores the adapter's own flags (`--interval-ms`, `--filesystem`,
`--interface`, `--compact`) on purpose: they are the adapter's argv contract and
`test_native_node_core_adapter.py` asserts them directly, where the assertion can
name the argument instead of a line of output.

`live-now` exists for one property and no other: a caller that reads its clock
BEFORE running the collector ages a reading the child stamped afterwards, which
is a reading from the future. The committed fixture lives in 2026-01-01 and
cannot show that, because its age is positive whichever clock is used.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MODE = os.environ.get("SERVEROS_FAKE_NATIVE_MODE", "ok")

FIXTURE = (Path(__file__).resolve().parents[1] / "native" / "node-core" / "adapter"
           / "fixtures" / "snapshot.v1.example.json")


def main() -> int:
    if MODE == "hang":
        # Long enough that only a bound can end it, short enough that a bound
        # which failed to fire does not hold the suite.
        time.sleep(30)
        return 0
    if MODE == "exit2":
        print("collector: cannot read /proc/stat", file=sys.stderr)
        return 2
    if MODE == "badjson":
        sys.stdout.write('{"schema": "serveros/node-resource-snapshot.v1",')
        return 0
    if MODE == "notjson":
        sys.stdout.write("this is not a document\n")
        return 0
    if MODE == "empty":
        return 0
    if MODE == "huge":
        sys.stdout.write("x" * 500_000)
        return 0

    if MODE == "live-now":
        document = json.loads(FIXTURE.read_text())
        stamped = datetime.now(timezone.utc).isoformat()
        document["observed_at"] = stamped
        document["sample_started_at"] = stamped
        json.dump(document, sys.stdout, sort_keys=True)
        return 0

    document = json.loads(FIXTURE.read_text())
    if MODE == "wrong-schema":
        document["schema"] = "serveros/node-resource-snapshot.v2"
    elif MODE == "wrong-collector":
        document["collector"]["name"] = "some-other-collector"
    elif MODE != "ok":
        print(f"fake collector: unknown mode {MODE!r}", file=sys.stderr)
        return 3
    json.dump(document, sys.stdout, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
