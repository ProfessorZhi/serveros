#!/usr/bin/env python3
"""Q1 round-8 probe: test-fixture age at the approval gate (#338).

RED mode (base e3dc9c7): install a 301-second-old context as the test module's
CONTEXT, call `build()` (the base does not re-mint) and a human-gated
`agent.session.stop` with no approval. The freshness check runs at read time
with stale_after_seconds=300, so the expected APPROVAL_REQUIRED is preceded by
UNTRUSTED_ACTOR. The probe records expected vs actual and the provider calls.

GREEN mode (#338 96e0fcf): the same aged context is installed, but `build()`
re-mints, so the positive call returns APPROVAL_REQUIRED and the provider is
not called. Independently, a 301s STALE and a 901s EXPIRED context presented
explicitly still return UNTRUSTED_ACTOR with provider calls == 0. The contract
values and the product diff are checked (only the test file changed).

Run: QA_TREE=<candidate> QA_MODE=red|green python3 qa_probe_r8_context_freshness.py
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
MODE = os.environ.get("QA_MODE", "green").strip().lower()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mod = load("q1r8_agent_mcp_tests", REPO / "tests" / "test_agent_mcp.py")


def aged_context(seconds: int) -> dict:
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=seconds)).isoformat()
    report = {
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_q1_age_test",
        "message_ref": "om_q1_age_test",
        "observed_at": past,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:hwz",
        "provider_pid": 4242,
    }
    return mod.actor.build(report, now=past)


def stop(client, context_ref):
    return mod.payload(mod.call(
        client, "agent.session.stop",
        session_ref=mod.semantics.session_ref(mod.LOCAL, mod.PROVIDER, "s1"),
        runtime=mod.RUNTIME, context_ref=context_ref))


aged = aged_context(301)
previous_context, previous_ref = mod.CONTEXT, mod.CONTEXT_REF
mod.CONTEXT, mod.CONTEXT_REF = aged, aged["context_ref"]
try:
    client, service, _daemon = mod.build()
    is_error, answer = stop(client, mod.CONTEXT_REF)
    calls = service.providers[mod.PROVIDER].calls
    actual = answer.get("reason") if isinstance(answer, dict) else answer
    print(f"OBSERVE module context aged 301s: expected=APPROVAL_REQUIRED "
          f"actual={actual!r} provider_calls={len(calls)}")
    if MODE == "red":
        check("RED aged positive fixture is refused before approval",
              not is_error and isinstance(answer, dict)
              and answer.get("ok") is False
              and actual == "UNTRUSTED_ACTOR",
              f"expected=APPROVAL_REQUIRED actual={actual!r}")
        check("RED no provider call", calls == [], repr(calls))
    else:
        check("GREEN build() re-mints; the positive call reaches approval",
              not is_error and isinstance(answer, dict)
              and answer.get("ok") is False
              and actual == "APPROVAL_REQUIRED",
              f"expected=APPROVAL_REQUIRED actual={actual!r}")
        check("GREEN no provider call", calls == [], repr(calls))

        for seconds, because in ((301, "not recently enough"),
                                 (901, "past the 900s window")):
            stale = aged_context(seconds)
            daemon = mod.FakeDaemon(
                contexts={stale["context_ref"]: stale})
            client2, service2, _ = mod.build(daemon=daemon)
            err, ans = stop(client2, stale["context_ref"])
            calls2 = service2.providers[mod.PROVIDER].calls
            check(f"{seconds}s context still returns UNTRUSTED_ACTOR",
                  not err and isinstance(ans, dict)
                  and ans.get("ok") is False
                  and ans.get("reason") == "UNTRUSTED_ACTOR",
                  repr(ans)[:220])
            check(f"{seconds}s context detail names the age boundary",
                  isinstance(ans, dict) and because in ans.get("detail", ""),
                  ans.get("detail", "")[:180] if isinstance(ans, dict) else "")
            check(f"{seconds}s context sends nothing", calls2 == [],
                  repr(calls2))
finally:
    mod.CONTEXT, mod.CONTEXT_REF = previous_context, previous_ref

if MODE == "green":
    import yaml
    contract = yaml.safe_load(
        (REPO / "context" / "actor-context-contract.yaml").read_text(
            encoding="utf-8"))
    freshness = contract["freshness"]
    check("stale_after_seconds is 300",
          int(freshness["stale_after_seconds"]) == 300,
          repr(freshness.get("stale_after_seconds")))
    check("window_seconds is 900",
          int(freshness["window_seconds"]) == 900,
          repr(freshness.get("window_seconds")))

    diff = subprocess.run(
        ["git", "-C", str(REPO), "diff", "--name-only",
         "e3dc9c7", "96e0fcf"],
        capture_output=True, text=True)
    files = [line for line in diff.stdout.splitlines() if line.strip()]
    check("the candidate changes only tests/test_agent_mcp.py",
          files == ["tests/test_agent_mcp.py"], repr(files))
    added = subprocess.run(
        ["git", "-C", str(REPO), "diff", "e3dc9c7", "96e0fcf",
         "--", "tests/test_agent_mcp.py"],
        capture_output=True, text=True).stdout
    check("no xfail/skip added", "xfail" not in added and "skip" not in added,
          "")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"mode": MODE, "checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
