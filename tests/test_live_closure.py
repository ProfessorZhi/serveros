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
    for not doing.
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


class FakeClient:
    """An Agent Control client that answers from a script, and records calls."""

    def __init__(self, *, agents=None, turn_outputs=None, turn_state="TURN_COMPLETED"):
        self._agents = agents if agents is not None else [
            {"agent_id": "agent-1", "provider": "provider:botmux-agent-session"}]
        self._outputs = list(turn_outputs or [])
        self._turn_state = turn_state
        self.calls: list[tuple[str, dict]] = []

    def call(self, name: str, arguments: dict | None = None) -> dict:
        self.calls.append((name, dict(arguments or {})))
        if name == "agent.list":
            return {"agents": self._agents, "providers": []}
        if name == "session.create":
            return {"session_ref": "agent-session:ref-1", "trigger_id": "trg-1",
                    "admission": {"decision": "ALLOW"},
                    "provider_readiness": {"status": "UNKNOWN"}}
        if name == "session.send":
            return {"session_ref": "agent-session:ref-1", "trigger_id": "trg-2",
                    "admission": {"decision": "ALLOW"}}
        if name == "session.result":
            output = self._outputs.pop(0) if self._outputs else None
            return {"turn_state": self._turn_state, "output": output}
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
    c = closure.Closure(client, deadline_seconds=deadline, poll_seconds=5.0,
                        sleep=clock.sleep, now=clock.now)
    return c.run(agent_id="agent-1", provider_id="provider:botmux-agent-session")


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
        sent = [a for n, a in client.calls if n == "session.send"]
        self.assertEqual(1, len(sent))
        self.assertEqual(result["session_ref"], sent[0]["ref"])


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

        The message carries the provider's own list rather than only the thing
        that is missing, because the two disagreeing is the diagnostic: an
        operator who expects an agent and sees `[]` has a different repair from
        one who sees the agent listed and misspelled.
        """
        client = FakeClient(agents=[])
        with self.assertRaises(SystemExit) as caught:
            run_client(client)
        message = str(caught.exception)
        self.assertIn("agent-1", message)
        self.assertIn("not enrolled", message)
        self.assertIn("[]", message)

    def test_an_agent_that_is_not_enrolled_is_refused(self):
        client = FakeClient(agents=[{"agent_id": "someone-else",
                                     "provider": "provider:botmux-agent-session"}])
        with self.assertRaises(SystemExit) as caught:
            run_client(client)
        self.assertIn("someone-else", str(caught.exception))

    def test_no_agent_id_is_hard_coded_in_the_source(self):
        """The second device is the point.

        A `larkAppId` written into this file would work here and nowhere else,
        and Phase 5 of this round is Windows nodes. The id is discovered from
        the provider's own inventory or the harness does not run.
        """
        source = (REPO / "scripts" / "live_closure.py").read_text(encoding="utf-8")
        self.assertNotIn("cli_", source)
        self.assertIn("agent.list", source)

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


if __name__ == "__main__":
    unittest.main()
