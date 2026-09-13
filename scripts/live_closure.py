#!/usr/bin/env python3
"""Run one real Agent Session turn, and one real follow-up, end to end.

    SERVEROS_BOTMUX_DASHBOARD_TOKEN=... python3 scripts/live_closure.py --yes

§63 shipped the Agent Session vertical slice and §64 gave a provider readiness a
second axis, and neither of them had ever completed a live turn: BotMux's
read-isolation guard refused to start the CLI, so every declared action read
SUPPORTED over a backend that could not run. §64 recorded that as NEEDS_HUMAN
and left it there, which was correct and is also why this file exists — a
closure that is performed once by hand is a closure nobody can repeat, and a
claim that cannot be repeated is the thing this product keeps finding written
down instead of implemented.

So the acceptance is a program. It dispatches a real instruction to a real
agent on a real device and prints a marker only after reading the agent's real
answer back:

    SERVEROS_AGENT_SESSION_SMOKE_OK
    SERVEROS_AGENT_SESSION_FOLLOWUP_OK

Both markers mean "the provider returned this string for that turn". Neither
means "the call was accepted" — an accepted trigger is exactly the state §63
was stuck in, and `turn_state: TURN_UNKNOWN` is printed beside a success for
that reason rather than being smoothed into a boolean.

WHAT IT MUTATES, because a script that starts a CLI must say so out loud: it
creates one BotMux session and runs two turns in it. On this deployment that
posts to the bot's chat. It therefore refuses to run without `--yes`, and it
never prints the dashboard token it authenticates with.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP = REPO_ROOT / "mcp"

SMOKE_MARKER = "SERVEROS_AGENT_SESSION_SMOKE_OK"
FOLLOWUP_MARKER = "SERVEROS_AGENT_SESSION_FOLLOWUP_OK"

# The instruction is written to make the answer CHECKABLE rather than
# interesting. Asking the agent to do any real work would make a failure
# ambiguous between "the session did not run" and "the work went wrong", and
# the question here is only whether a turn runs at all.
INSTRUCTION = "Reply with exactly this token and nothing else: {marker}"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Closure:
    """One live closure, with every step's verdict kept."""

    def __init__(self, client, *, deadline_seconds: float = 180.0,
                 poll_seconds: float = 5.0, sleep=time.sleep, now=time.monotonic):
        self.client = client
        self.deadline_seconds = deadline_seconds
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.now = now
        self.steps: list[dict] = []

    def _record(self, step: str, **fields) -> None:
        self.steps.append({"step": step, **fields})

    def _await_answer(self, ref: str, trigger_id: str | None) -> dict:
        """Poll until the provider reports a finished turn, or the deadline.

        A timeout is reported as TURN_DID_NOT_FINISH rather than as a failure
        of the provider: the two have different repairs, and a session that is
        still running is not a session that failed.
        """
        deadline = self.now() + self.deadline_seconds
        last = {}
        while True:
            last = self.client.call("session.result",
                                    {"ref": ref, "trigger_id": trigger_id})
            if last.get("turn_state") in ("TURN_COMPLETED", "TURN_FAILED"):
                return last
            if self.now() >= deadline:
                return dict(last, turn_state="TURN_DID_NOT_FINISH")
            self.sleep(self.poll_seconds)

    def run(self, *, agent_id: str, provider_id: str) -> dict:
        agents = self.client.call("agent.list")
        known = [a.get("agent_id") for a in agents.get("agents") or []]
        if agent_id not in known:
            raise SystemExit(
                f"no agent {agent_id!r} on this device; the provider reports "
                f"{known}. A closure against an agent that is not enrolled is "
                "not a closure")
        self._record("agent.list", agents=known)

        created = self.client.call("session.create", {
            "provider": provider_id, "agent_id": agent_id,
            "instruction": INSTRUCTION.format(marker=SMOKE_MARKER),
            "idempotency_key": "serveros-live-closure-smoke",
        })
        ref = created["session_ref"]
        self._record("session.create", session_ref=ref,
                     trigger_id=created.get("trigger_id"),
                     admission=(created.get("admission") or {}).get("decision"),
                     provider_readiness=(created.get("provider_readiness") or {}).get("status"))
        smoke = self._await_answer(ref, created.get("trigger_id"))
        self._record("session.result(first turn)", turn_state=smoke.get("turn_state"),
                     output=smoke.get("output"), error_code=smoke.get("error_code"))

        sent = self.client.call("session.send", {
            "ref": ref,
            "instruction": INSTRUCTION.format(marker=FOLLOWUP_MARKER),
            "turn_idempotency_key": "serveros-live-closure-followup",
        })
        self._record("session.send", session_ref=ref,
                     admission=(sent.get("admission") or {}).get("decision"))
        follow = self._await_answer(ref, sent.get("trigger_id") or created.get("trigger_id"))
        self._record("session.result(second turn)", turn_state=follow.get("turn_state"),
                     output=follow.get("output"), error_code=follow.get("error_code"))

        return {
            "session_ref": ref,
            "smoke": {"turn_state": smoke.get("turn_state"), "output": smoke.get("output")},
            "followup": {"turn_state": follow.get("turn_state"), "output": follow.get("output")},
            "steps": self.steps,
            "markers": {
                SMOKE_MARKER: smoke.get("output") == SMOKE_MARKER,
                FOLLOWUP_MARKER: follow.get("output") == FOLLOWUP_MARKER,
            },
        }


def _agent_and_provider(client, requested_agent: str | None) -> tuple[str, str]:
    """The agent to drive, and the provider that owns it.

    Discovered rather than defaulted: a hard-coded `larkAppId` would make this
    program fail on every device but the one it was written on, and the whole
    point of the control plane is that the second device is not a rewrite.
    """
    agents = client.call("agent.list").get("agents") or []
    if not agents:
        raise SystemExit("this device exposes no agents; nothing to close over")
    chosen = None
    for agent in agents:
        if requested_agent in (None, agent.get("agent_id")):
            chosen = agent
            break
    if chosen is None:
        raise SystemExit(f"no enrolled agent named {requested_agent!r}")
    return chosen["agent_id"], chosen["provider"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live-closure",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--agent", help="agent id to drive; default: the first enrolled one")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--deadline", type=float, default=180.0,
                        help="seconds to wait for one turn before reporting that it did not finish")
    parser.add_argument("--yes", action="store_true",
                        help="required: this creates a real provider session and runs two real turns")
    args = parser.parse_args(argv)

    if not args.yes:
        sys.stderr.write(
            "refusing without --yes: this creates a real BotMux session and runs two "
            "real turns in it. That is a live mutation on a shared device, and a "
            "script that performs one must be asked to.\n")
        return 2
    if not os.environ.get("SERVEROS_BOTMUX_DASHBOARD_TOKEN"):
        sys.stderr.write(
            "SERVEROS_BOTMUX_DASHBOARD_TOKEN is not set. Get it with "
            "`botmux dashboard current` and inject it; do not paste it into a "
            "command line, where it lands in the transcript.\n")
        return 2

    agent_mcp = _load("serveros_agent_mcp_for_closure", MCP / "agent_mcp.py")
    client = agent_mcp.AgentControlClient()
    agent_id, provider_id = _agent_and_provider(client, args.agent)

    result = Closure(client, deadline_seconds=args.deadline).run(
        agent_id=agent_id, provider_id=provider_id)

    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
    else:
        for step in result["steps"]:
            sys.stdout.write(json.dumps(step, sort_keys=True, default=str) + "\n")

    failed = [marker for marker, ok in result["markers"].items() if not ok]
    if failed:
        sys.stderr.write(
            "\nclosure NOT passed. The turn did not return the marker it was "
            "asked for:\n")
        for marker in failed:
            sys.stderr.write(f"  missing: {marker}\n")
        sys.stderr.write(
            f"  first turn : state={result['smoke']['turn_state']!r} "
            f"output={result['smoke']['output']!r}\n"
            f"  second turn: state={result['followup']['turn_state']!r} "
            f"output={result['followup']['output']!r}\n")
        return 1

    sys.stdout.write(f"\n{SMOKE_MARKER}\n{FOLLOWUP_MARKER}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
