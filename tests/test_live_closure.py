#!/usr/bin/env python3
"""Tests for the live closure harness (§11/§63/§64).

The harness itself makes no live call here. It is written to be driven by an
injected client and an injected clock for exactly one reason: a smoke test whose
only test is running it against the live provider is a smoke test that is
verified once, on the machine where it was written, and never again. The live
run is worth doing and it is not a test — it is an acceptance, and it belongs in
the report.

What is tested here is the part that decides:
  * that it refuses to act without being asked, and refuses to guess a token;
  * that a marker is printed only when the PROVIDER returned that string, and
    not when a trigger was merely accepted — which is the exact state §63 was
    stuck in for a whole round;
  * that a turn which never finishes is reported as unfinished rather than as a
    failed turn, because the two have different repairs;
  * that it does not carry a hard-coded agent id, because a closure that only
    works on the device it was written on is the thing the control plane is
    for not doing;
  * that every call it makes is a call the real surface declares, checked
    against that surface rather than against a fake written here.

The last one is not decoration. An earlier version of this harness called
`session.create`, `session.send`, `session.result` and `agent.list` — bare
names, no envelope, no `context_ref` — and the `FakeClient` below implemented
exactly those names, so every test passed while the script could not have
reached a provider at all: the router refuses a mutation with no context, and
`AgentControlClient.call` raises `unimplemented Agent MCP operation` for a name
it does not serve. Two stubs that agree with each other prove nothing about the
graph, which is why the gate below asks the graph.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
import contextlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


closure = _load("serveros_live_closure", REPO / "scripts" / "live_closure.py")
agent_mcp = _load("serveros_agent_mcp_for_closure_tests",
                  REPO / "mcp" / "agent_mcp.py")

DEVICE = "device:server-beijing"
RUNTIME = "agent-runtime:fixture"
CONTEXT_REF = "human-context:provider%3Afixture/deadbeef"

AGENT_ROW = {
    "agent_id": "agent-1",
    "agent_ref": "agent:device%3Aserver-beijing/provider%3Afixture/agent-1",
    "provider": "provider:fixture",
    "agent_runtime": RUNTIME,
}


class FakeClient:
    """An Agent Control client that answers from a script, and records calls.

    It answers in the shapes `mcp/agent_mcp.py` serves: a control action comes
    back as `{"ok": ..., "reason": ..., "detail": ..., "result": ...}`, which is
    what `AgentControlResponse.show()` produces. A fake that returned the
    provider's dict at the top level would agree with a harness that read it
    there, and both would be wrong together.
    """

    def __init__(self, *, agents=None, turn_outputs=None, turn_state="TURN_COMPLETED",
                 refuse=None):
        self._agents = agents if agents is not None else [dict(AGENT_ROW)]
        self._outputs = list(turn_outputs or [])
        self._turn_state = turn_state
        self._refuse = refuse
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, arguments: dict | None = None) -> dict:
        self.calls.append((name, dict(arguments or {})))
        if self._refuse and name == self._refuse:
            return {"ok": False, "reason": "INVALID_CONTEXT",
                    "detail": "this mutation presented no resolvable context_ref"}
        if name == "agent.list":
            return {"device_ref": DEVICE, "agents": self._agents, "providers": [],
                    "provider_errors": [], "complete": True}
        if name == "agent.session.create":
            return {"ok": True, "result": {
                "session_ref": "agent-session:ref-1", "trigger_id": "trg-1",
                "turn_state": "TURN_UNKNOWN", "provider": "provider:fixture"}}
        if name == "agent.session.send":
            return {"ok": True, "result": {
                "session_ref": "agent-session:ref-1", "trigger_id": "trg-2",
                "turn_state": "TURN_UNKNOWN"}}
        if name == "agent.session.result":
            output = self._outputs.pop(0) if self._outputs else None
            return {"ok": True, "result": {"turn_state": self._turn_state,
                                           "output": output}}
        raise AssertionError(f"unexpected call {name!r}")


class Clock:
    """A clock that only moves when something sleeps on it."""

    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def run_client(client, *, deadline=180.0, clock=None):
    clock = clock or Clock()
    c = closure.Closure(client, device=DEVICE, context_ref=CONTEXT_REF,
                        deadline_seconds=deadline, poll_seconds=5.0,
                        sleep=clock.sleep, now=clock.now)
    return c.run(agent=dict(AGENT_ROW))


class TheHappyPathTests(unittest.TestCase):
    def test_both_markers_are_reported_when_the_provider_returns_them(self):
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.FOLLOWUP_MARKER])
        result = run_client(client)
        self.assertEqual({closure.SMOKE_MARKER: True, closure.FOLLOWUP_MARKER: True},
                         result["markers"])

    def test_an_accepted_trigger_is_not_a_passed_marker(self):
        """The §63 state, kept out of the pass condition.

        A trigger BotMux accepts and never runs answers TURN_COMPLETED with no
        output on some paths and TURN_UNKNOWN on others. Either way the marker
        was never READ from the provider, and the closure must not print it.
        """
        client = FakeClient(turn_outputs=[None, None])
        result = run_client(client)
        self.assertEqual({closure.SMOKE_MARKER: False, closure.FOLLOWUP_MARKER: False},
                         result["markers"])
        self.assertEqual("TURN_COMPLETED", result["smoke"]["turn_state"])
        self.assertIsNone(result["smoke"]["output"])

    def test_the_first_turn_marker_is_not_accepted_as_the_second_one(self):
        """One string cannot stand in for two turns: the follow-up must run."""
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.SMOKE_MARKER])
        result = run_client(client)
        self.assertTrue(result["markers"][closure.SMOKE_MARKER])
        self.assertFalse(result["markers"][closure.FOLLOWUP_MARKER])

    def test_the_followup_is_sent_to_the_session_the_create_returned(self):
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.FOLLOWUP_MARKER])
        result = run_client(client)
        sent = [a for n, a in client.calls if n == "agent.session.send"]
        self.assertEqual(1, len(sent))
        self.assertEqual(result["session_ref"], sent[0]["session_ref"])

    def test_the_runtime_in_the_envelope_comes_from_the_agent_row(self):
        """Not from a flag, and not derived from the device.

        The envelope requires a runtime and the router refuses to derive one,
        so a harness that guessed would be inventing the very fact the field
        exists to keep authored. It is one observation, taken once, and used
        for the create, the send and every poll.
        """
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.FOLLOWUP_MARKER])
        run_client(client)
        for name, arguments in client.calls:
            if name.startswith("agent.session."):
                self.assertEqual(RUNTIME, arguments["runtime"], name)


class UnfinishedTurnTests(unittest.TestCase):
    def test_a_turn_that_never_finishes_is_reported_as_unfinished(self):
        """Not as a failure, and not as a success.

        The provider says TURN_UNKNOWN while a turn is running, so the harness
        polls. A deadline that expires is its own verdict: a session still
        running is not a session that failed, and reporting it as one would
        send its reader to look for an error that does not exist.
        """
        client = FakeClient(turn_outputs=[], turn_state="TURN_UNKNOWN")
        result = run_client(client, deadline=30.0)
        self.assertEqual("TURN_DID_NOT_FINISH", result["smoke"]["turn_state"])
        self.assertFalse(result["markers"][closure.SMOKE_MARKER])

    def test_polling_stops_as_soon_as_the_turn_finishes(self):
        """A completed turn must not be polled to the deadline."""
        clock = Clock()
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.FOLLOWUP_MARKER])
        run_client(client, clock=clock)
        self.assertEqual(0.0, clock.t,
                         "a finished turn waited; the loop condition is wrong")


class ItRefusesToActTests(unittest.TestCase):
    def test_no_agent_on_the_device_is_refused_rather_than_guessed(self):
        """An empty inventory is refused by name, with what was found beside it.

        The message carries the provider's own answer rather than only the thing
        that is missing, because the two disagreeing is the diagnostic: an
        operator who expects an agent and sees `[]` has a different repair from
        one who sees the agent listed and misspelled.
        """
        client = FakeClient(agents=[])
        with self.assertRaises(SystemExit) as caught:
            closure._agent_for(client, DEVICE, None)
        message = str(caught.exception)
        self.assertIn("no agents", message)
        self.assertIn("[]", message)

    def test_an_agent_that_is_not_enrolled_is_refused_with_what_was_found(self):
        """Naming an agent that is not there is refused, and the refusal lists.

        The list matters more than the refusal: `--agent typo-1` against a
        provider that reports `agent-1` has one repair, and against one that
        reports nothing has another.
        """
        client = FakeClient(agents=[{"agent_id": "someone-else",
                                     "agent_runtime": RUNTIME}])
        with self.assertRaises(SystemExit) as caught:
            closure._agent_for(client, DEVICE, "typo-1")
        message = str(caught.exception)
        self.assertIn("typo-1", message)
        self.assertIn("someone-else", message)

    def test_asking_for_no_agent_takes_the_first_enrolled_one(self):
        """The default is a stated default, not a discovery.

        `--agent` omitted must not mean "guess"; it means "the first one this
        device enrolls", which is one observation the provider already made.
        """
        client = FakeClient()
        agent = closure._agent_for(client, DEVICE, None)
        self.assertEqual("agent-1", agent["agent_id"])

    def test_a_refused_control_action_is_reported_with_its_reason(self):
        """A refusal is carried out with the reason word intact.

        The surface answers a routed action with `ok: False` and a reason
        instead of raising, and the reasons name different repairs:
        `INVALID_CONTEXT` is a context problem, `DEVICE_UNREACHABLE` is a
        network one. A harness that read `answer["result"]` unconditionally
        would report a KeyError where the control plane had said something
        specific.
        """
        client = FakeClient(refuse="agent.session.create")
        with self.assertRaises(SystemExit) as caught:
            run_client(client)
        message = str(caught.exception)
        self.assertIn("agent.session.create was refused", message)
        self.assertIn("INVALID_CONTEXT", message)

    def test_an_agent_row_with_no_runtime_is_refused(self):
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER])
        with self.assertRaises(SystemExit) as caught:
            closure.Closure(client, device=DEVICE, context_ref=CONTEXT_REF,
                            poll_seconds=0.0, sleep=lambda s: None
                            ).run(agent={"agent_id": "agent-1"})
        self.assertIn("agent_runtime", str(caught.exception))

    def test_no_agent_id_is_hard_coded_in_the_source(self):
        """The second device is the point.

        An id written into this file would work here and nowhere else, and
        Phase 5 of this round is Windows nodes. The id is discovered from the
        provider's own inventory or the harness does not run.
        """
        source = (REPO / "scripts" / "live_closure.py").read_text(encoding="utf-8")
        self.assertNotIn("cli_", source)
        self.assertIn("agent.list", source)

    def test_the_context_is_minted_here_rather_than_accepted_as_a_parameter(self):
        """A `--context-ref` flag would make the closure test the operator.

        The ref has to come from a HumanControlProvider's own observation,
        presented by the process that made it. A harness that took one as an
        argument would be exercising whatever string it was handed, and the
        router would then be the only thing standing between a pasted ref and
        an unattributed mutation.
        """
        source = (REPO / "scripts" / "live_closure.py").read_text(encoding="utf-8")
        self.assertIn("attach_context", source)
        self.assertNotIn("--context", source)

    def test_the_live_run_is_opt_in(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = closure.main([])
        self.assertEqual(2, code)
        self.assertIn("--yes", err.getvalue())

    def test_a_missing_token_is_refused_before_any_session_is_created(self):
        out, err = io.StringIO(), io.StringIO()
        environ = dict(os.environ)
        os.environ.pop("SERVEROS_BOTMUX_DASHBOARD_TOKEN", None)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = closure.main(["--yes"])
        finally:
            os.environ.clear()
            os.environ.update(environ)
        self.assertEqual(2, code)
        self.assertIn("SERVEROS_BOTMUX_DASHBOARD_TOKEN", err.getvalue())

    def test_the_token_is_never_printed(self):
        """Read structurally, because the failure is silent by construction.

        A token echoed into stdout lands in a transcript and stays there. The
        harness reads it from the environment and must never write it out; the
        assertion is that no formatting of it appears.
        """
        source = (REPO / "scripts" / "live_closure.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "DASHBOARD_TOKEN" in line and "os.environ" not in line:
                self.assertNotIn("print", line)
                self.assertNotIn("write", line)


class EveryCallTheHarnessMakesIsOneTheSurfaceDeclares(unittest.TestCase):
    """The gate that the old fake made impossible.

    Driven, not read: the harness is run against a recording client with every
    call site exercised, and each recorded call is then checked against
    `mcp/agent_mcp.py`'s own tool table. A name the surface does not serve, a
    key its schema does not declare, or a required key left out, is a harness
    that cannot reach a provider — and, before this, was also a harness whose
    tests all passed.
    """

    def _calls(self):
        client = FakeClient(turn_outputs=[closure.SMOKE_MARKER, closure.FOLLOWUP_MARKER])
        # Both entry points, because they are two call sites: `main` discovers
        # the agent and the closure drives it.
        closure._agent_for(client, DEVICE, None)
        run_client(client)
        self.assertTrue(client.calls, "the harness made no calls at all")
        return client.calls

    def test_every_name_is_a_tool_the_surface_serves(self):
        served = set(agent_mcp.tool_names())
        for name, _ in self._calls():
            self.assertIn(name, served, (
                f"{name!r} is not served by the Agent Session MCP surface. "
                f"Available: {sorted(served)}"))

    def test_every_argument_is_declared_and_every_required_one_is_present(self):
        for name, arguments in self._calls():
            schema = agent_mcp.tool(name)["inputSchema"]
            declared = set(schema["properties"])
            required = set(schema.get("required") or ())
            self.assertEqual(set(), set(arguments) - declared, (
                f"{name} was sent {sorted(set(arguments) - declared)}, which its "
                f"schema does not declare; the router closes the envelope per "
                f"action, and a field the surface drops silently is a caller "
                f"believing it took effect"))
            self.assertEqual(set(), required - set(arguments), (
                f"{name} is missing required {sorted(required - set(arguments))}"))

    def test_every_mutating_call_presents_a_trusted_context(self):
        """The specific defect: a mutation with no actor is refused by the graph.

        Asserted separately from the schema sweep because it is the one that
        was wrong, and because "required by the schema" and "actually sent"
        are two facts that a future edit could separate.
        """
        mutations = [c for c in self._calls() if c[0] in agent_mcp.CONTROL_ACTIONS]
        self.assertTrue(mutations, "the harness made no mutating call; that is the whole point of it")
        for name, arguments in mutations:
            self.assertTrue(arguments.get("context_ref"), (
                f"{name} presented no context_ref; the router refuses that with "
                f"INVALID_CONTEXT before any provider is asked"))


if __name__ == "__main__":
    unittest.main()
