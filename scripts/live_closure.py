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
mints a trusted actor context, creates one agent session and runs two turns in
it. On this deployment that posts to the bot's chat. It therefore refuses to run
without `--yes`, and it never prints the dashboard token it authenticates with.

WHAT IT SPEAKS, because this file was wrong about it for a round and the wrong
version was invisible. Every call below goes through the Agent Session MCP
surface by the name that surface declares — `agent.session.create`, not
`session.create` — addresses a device and a runtime on an envelope the router
closes per action, and presents a `context_ref` minted by the one doorway that
can mint one (`mcp/human_control.py#attach_context`). A mutation without a
context_ref is refused by the router with `INVALID_CONTEXT`, so a closure that
had no context had never reached a provider at all. The test that keeps this
true reads the calls this file makes and checks each one against
`mcp/agent_mcp.py`'s declared tool schemas, rather than against a fake written
here: the earlier fake agreed with the script and disagreed with the graph, and
two stubs that agree with each other prove nothing about either.
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

# `session_role` has no default anywhere in this stack, by design: a thread is
# created with a stated purpose, and a default would be a guess about somebody's
# intent recorded as a fact. This closure crosses Items — it is an acceptance of
# the control plane, not of any one Item's work — so it is `coordinator`, which
# is the role that takes no `bound_item_ref`.
SESSION_ROLE = "coordinator"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Closure:
    """One live closure, with every step's verdict kept."""

    def __init__(self, client, *, device: str, context_ref: str,
                 runtime: str = "", deadline_seconds: float = 180.0,
                 poll_seconds: float = 5.0, sleep=time.sleep, now=time.monotonic):
        self.client = client
        self.device = device
        self.context_ref = context_ref
        # Set from the agent the caller asked for, before any session exists.
        self.runtime = runtime
        self.deadline_seconds = deadline_seconds
        self.poll_seconds = poll_seconds
        self.sleep = sleep
        self.now = now
        self.steps: list[dict] = []

    def _record(self, step: str, **fields) -> None:
        self.steps.append({"step": step, **fields})

    def _answer(self, name: str, arguments: dict) -> dict:
        """One tool call, with a refusal reported as a refusal.

        The surface answers a routed control action with `ok: False` and a
        reason word rather than raising, so a closure that read straight into
        `answer["result"]` would report a KeyError where the control plane had
        said something specific. The refusal is carried out with the step and
        the reason intact.
        """
        answer = self.client.call(name, arguments)
        if answer.get("ok") is False:
            raise SystemExit(
                f"{name} was refused: {answer.get('reason')}: "
                f"{answer.get('detail')}. This is the control plane answering, "
                f"not the harness failing — the reason names what to repair.")
        return answer

    def _result(self, name: str, arguments: dict) -> dict:
        return self._answer(name, arguments).get("result") or {}

    def _await_answer(self, session_ref: str, trigger_id: str | None) -> dict:
        """Poll until the provider reports a finished turn, or the deadline.

        A timeout is reported as TURN_DID_NOT_FINISH rather than as a failure
        of the provider: the two have different repairs, and a session that is
        still running is not a session that failed.
        """
        deadline = self.now() + self.deadline_seconds
        last: dict = {}
        while True:
            args = {"session_ref": session_ref, "runtime": self.runtime,
                    "device": self.device}
            if trigger_id:
                args["trigger_id"] = trigger_id
            last = self._result("agent.session.result", args)
            if last.get("turn_state") in ("TURN_COMPLETED", "TURN_FAILED"):
                return last
            if self.now() >= deadline:
                return dict(last, turn_state="TURN_DID_NOT_FINISH")
            self.sleep(self.poll_seconds)

    def run(self, *, agent: dict) -> dict:
        agent_id = agent["agent_id"]
        self.runtime = self.runtime or str(agent.get("agent_runtime") or "")
        if not self.runtime:
            raise SystemExit(
                f"the inventory lists {agent_id!r} with no `agent_runtime`, and "
                f"the envelope requires one rather than deriving it: which "
                f"runtime a device DECLARES is an authoring statement, and "
                f"routing on a declaration would make a registry entry into a "
                f"capability. Row: {json.dumps(agent, sort_keys=True, default=str)}")

        created = self._result("agent.session.create", {
            "device": self.device,
            "runtime": self.runtime,
            "agent_id": agent_id,
            "instruction": INSTRUCTION.format(marker=SMOKE_MARKER),
            "idempotency_key": "serveros-live-closure-smoke",
            "context_ref": self.context_ref,
            "session_role": SESSION_ROLE,
        })
        ref = created.get("session_ref")
        if not ref:
            raise SystemExit(
                "the provider created a session and returned no session_ref, so "
                "there is nothing to send a follow-up to. It answered: "
                + json.dumps(created, sort_keys=True, default=str))
        self._record("agent.session.create", session_ref=ref,
                     trigger_id=created.get("trigger_id"),
                     turn_state=created.get("turn_state"),
                     provider=created.get("provider"))
        smoke = self._await_answer(ref, created.get("trigger_id"))
        self._record("agent.session.result(first turn)",
                     turn_state=smoke.get("turn_state"),
                     output=smoke.get("output"), error_code=smoke.get("error_code"))

        sent = self._result("agent.session.send", {
            "device": self.device,
            "session_ref": ref,
            "runtime": self.runtime,
            "instruction": INSTRUCTION.format(marker=FOLLOWUP_MARKER),
            "turn_idempotency_key": "serveros-live-closure-followup",
            "context_ref": self.context_ref,
        })
        self._record("agent.session.send", session_ref=ref,
                     trigger_id=sent.get("trigger_id"))
        follow = self._await_answer(ref, sent.get("trigger_id") or created.get("trigger_id"))
        self._record("agent.session.result(second turn)",
                     turn_state=follow.get("turn_state"),
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


def _agent_for(client, device: str, requested: str | None) -> dict:
    """The agent to drive, from the provider's own inventory.

    Discovered rather than defaulted: a hard-coded agent id would make this
    program fail on every device but the one it was written on, and the whole
    point of the control plane is that the second device is not a rewrite. The
    runtime the envelope requires comes from this same row, because the two
    facts are one observation — asking twice would let them disagree.
    """
    listing = client.call("agent.list", {"device": device})
    agents = listing.get("agents") or []
    if not agents:
        raise SystemExit(
            f"this device exposes no agents; nothing to close over. The provider "
            f"answered {json.dumps(listing, sort_keys=True, default=str)}")
    for agent in agents:
        if requested in (None, agent.get("agent_id"), agent.get("agent_ref")):
            return agent
    listed = sorted(str(a.get("agent_id")) for a in agents)
    raise SystemExit(
        f"no enrolled agent named {requested!r}; the provider reports {listed}. "
        f"A closure against an agent that is not enrolled is not a closure")


def _mint_context():
    """The one doorway that mints a trusted actor context.

    Not optional and not a parameter. Every mutating Agent Session action is
    refused by the router with `INVALID_CONTEXT` when it carries no ref, and a
    ref can only come from a HumanControlProvider's own observation presented to
    serverosd by the process that made it. A closure that took a ref as an
    argument would be testing whatever the operator pasted in.
    """
    human_control = _load("serveros_mcp_human_control_for_closure",
                          MCP / "human_control.py")
    result = human_control.attach_context(human_control.BotMuxHumanControlProvider())
    if not result.get("attached"):
        raise SystemExit(
            f"no trusted context was minted, so no mutation could be admitted. "
            f"state={result.get('state')!r} detail={result.get('detail')!r}. "
            f"BLOCKED means the identity surface answered and declined to verify "
            f"this process — which is the correct answer outside a BotMux turn. "
            f"Run this from inside one.")
    return result["context_ref"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="live-closure",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--agent", help="agent id to drive; default: the first enrolled one")
    parser.add_argument("--device", help="device ref; default: this node's authored ref")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--deadline", type=float, default=180.0,
                        help="seconds to wait for one turn before reporting that it did not finish")
    parser.add_argument("--yes", action="store_true",
                        help="required: this creates a real provider session and runs two real turns")
    args = parser.parse_args(argv)

    if not args.yes:
        sys.stderr.write(
            "refusing without --yes: this mints a trusted context, creates a real "
            "agent session and runs two real turns in it. That is a live mutation "
            "on a shared device, and a script that performs one must be asked to.\n")
        return 2
    if not os.environ.get("SERVEROS_BOTMUX_DASHBOARD_TOKEN"):
        sys.stderr.write(
            "SERVEROS_BOTMUX_DASHBOARD_TOKEN is not set. Get it with "
            "`botmux dashboard current` and inject it; do not paste it into a "
            "command line, where it lands in the transcript.\n")
        return 2

    agent_mcp = _load("serveros_agent_mcp_for_closure", MCP / "agent_mcp.py")
    client = agent_mcp.AgentControlClient()
    # The authored device ref, not a hostname: which device this is is an
    # authoring statement, and §4 forbids deriving it from the machine.
    device = args.device or str(client.router.local_device_ref)

    context_ref = _mint_context()
    agent = _agent_for(client, device, args.agent)

    result = Closure(client, device=device, context_ref=context_ref,
                     deadline_seconds=args.deadline).run(agent=agent)

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
