#!/usr/bin/env python3
"""A forged remote ServerOS node, speaking the Agent Control wire.

WHY THIS IS A PROCESS
---------------------
§7 asks for forged-remote integration tests, and the half of the contract that
integrates is the wire: one JSON document out, one back. An in-process fake would
let the router's serialization be skipped entirely, and serialization is where a
remote call actually fails — an enum that does not survive `json.dumps`, a key
that arrives as a string instead of an int, a field that is silently dropped and
therefore silently defaulted on the far side. None of those are visible to a
function-call fake.

So this speaks newline-delimited JSON on stdin/stdout, exactly as the deployed
node connector will, and `SERVEROS_FAKE_NODE_MODE` decides what it does with a
request:

    ok        answer successfully (default)
    refuse    refuse with SERVEROS_FAKE_NODE_REASON
    hang      never answer, so a timeout has something to time out
    die       exit without answering, so a broken pipe has something to break
    badjson   answer with something that is not JSON

It deliberately does NOT validate the request against a schema. A fake that
validated would be enforcing the contract from the wrong side; the caller's
obligation to send the right shape is the router's test, not this file's.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


PROBE_OPS = ("node.probe", "resolve.inputs")


def answer(request: dict) -> dict:
    mode = os.environ.get("SERVEROS_FAKE_NODE_MODE") or "ok"
    if mode == "hang":
        while True:
            time.sleep(3600)
    if mode == "die":
        sys.exit(0)
    if mode == "badjson":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
        sys.exit(0)
    if mode == "refuse" and request.get("op") not in PROBE_OPS:
        # The node is up and answers probes; the control call is what fails.
        # That is the realistic shape — a node whose provider broke — and it is
        # the only shape in which the refusal reaches the caller instead of
        # being swallowed by the resolver as an unreachable device.
        return {
            "ok": False,
            "reason": os.environ.get("SERVEROS_FAKE_NODE_REASON") or "PROVIDER_ERROR",
            "detail": os.environ.get("SERVEROS_FAKE_NODE_DETAIL") or "refused by fixture",
            "retryable": os.environ.get("SERVEROS_FAKE_NODE_RETRYABLE") == "1",
        }
    if request.get("op") == "node.probe":
        return {
            "ok": True,
            "result": {
                "presence": os.environ.get("SERVEROS_FAKE_NODE_PRESENCE") or "KNOWN_ONLINE",
                "node_runtime": os.environ.get("SERVEROS_FAKE_NODE_RUNTIME") or "RUNNING",
                "observed_at": observed_at(),
            },
        }
    if request.get("op") == "resolve.inputs":
        # The forged node reports a provider that is ready and supports
        # everything the runtime declares. `observed_at` is now, because a
        # fixture that pinned a date would age out of the readiness window and
        # the test would pass or fail depending on when it ran.
        return {
            "ok": True,
            "result": {
                "providers": [{"id": os.environ.get("SERVEROS_FAKE_NODE_PROVIDER")
                               or "provider:fake-node-session"}],
                "instances": [{
                    "provider": os.environ.get("SERVEROS_FAKE_NODE_PROVIDER")
                                or "provider:fake-node-session",
                    "device_ref": request.get("device_ref"),
                    "observed_at": observed_at(),
                    "status": "AVAILABLE",
                    "status_provenance": "INFERRED",
                    "reason": None,
                    "action_support": {
                        action: {"state": "SUPPORTED"}
                        for action in ("session.create", "session.inspect",
                                       "session.send", "session.result",
                                       "session.resume", "session.interrupt",
                                       "session.stop", "session.handoff")},
                }],
                "runtime_record": {
                    "id": request.get("runtime_id"),
                    "consumable_provider_kinds": ["skill", "mcp-server", "cli",
                                                  "local-service", "remote-service",
                                                  "builtin"],
                },
            },
        }
    # An answered control request. The correlation id is echoed back so the
    # router's own echo can be asserted rather than assumed: a node that did not
    # echo it would make two concurrent calls indistinguishable in a log.
    return {
        "ok": True,
        "result": {
            "session_ref": request.get("session_ref") or "session:fake-node:1",
            "action": request.get("action"),
            "correlation_id_seen": request.get("correlation_id"),
            "params_seen": request.get("params"),
            "device_ref_seen": request.get("device_ref"),
            "idempotency_key_seen": request.get("idempotency_key"),
        },
    }


def main() -> int:
    sys.stderr.write("fake node control ready\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        sys.stdout.write(json.dumps(answer(request)) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
