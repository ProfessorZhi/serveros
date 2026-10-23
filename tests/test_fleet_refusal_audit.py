#!/usr/bin/env python3
"""A refusal is a control-plane fact, so it goes in the log.

What this file is the admission question for
--------------------------------------------
`record_grant` fires when a grant *admits* a call. Nothing fired when one
refused it. Measured on the running Next surface (3411) on 2026-10-22: three
refusals — two `APPROVAL_REQUIRED`, one `WRITE_NOT_GRANTED` — left the audit
file with three `mcp.request` lines carrying a byte count and nothing else. No
tool name, no device, no code. A surface answering on a public Funnel that
records who signed in and not what they were turned away from is a log that
answers half of what it exists for.

Two properties are asserted here, and the second is the one that would be lost
first in a hurry:

1.  A refusal produces one `fleet.refused` record naming the code, the tool and
    whatever addresses the caller had already named.
2.  The refusal *detail* does not. That prose is assembled around caller input
    and, on a device error, around the provider's own words. The code is this
    surface's; the detail is not, and only the code goes in.

Nothing here touches a device, a session or a real credential.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.audit import AuditLog  # noqa: E402
from fleet_mcp.config import AuditBinding  # noqa: E402
from fleet_mcp.fleet import Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.tools import ToolContext, call_tool  # noqa: E402


class _Service:
    """The two things `call_tool` reads off the service: its audit log, and the
    one method the scope case below reaches.

    `send_to_session` asks the principal for the scope before it asks a device
    for anything — the same order the real service keeps — so a read-only
    credential is refused here without a route, a provider or a network call
    being involved.
    """

    def __init__(self, audit: AuditLog | None) -> None:
        self.audit = audit

    def resolve_session(self, principal: Principal, *, session_ref: str | None = None,
                        device: str | None = None, session: str | None = None,
                        provider_name: str | None = None) -> SessionKey:
        # Address parsing only. Whether this principal may address it is the
        # next thing the service answers, and that is the answer under test.
        return SessionKey.parse(session_ref or f"{device}|{provider_name or 'claude'}|{session}")

    def send_to_session(self, principal: Principal, key, *, message: str,
                        idempotency_key: str | None = None):
        principal.require("fleet:send")
        raise AssertionError("the scope check should have refused this call")


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class TheRefusalIsRecorded(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "audit.jsonl"
        self.audit = AuditLog(AuditBinding(path=self.path, rate_limit_per_minute=10_000))
        self.ctx = ToolContext(
            service=_Service(self.audit),
            principal=Principal(name="oauth:test-client",
                                scopes=frozenset({"fleet:read"}),
                                credential_kind="oauth"),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def refusals(self) -> list[dict]:
        return [row for row in _rows(self.path) if row.get("event") == "fleet.refused"]

    def test_an_unknown_argument_is_recorded_with_its_code_and_tool(self) -> None:
        result = call_tool(self.ctx, "fleet_send_to_session", {"bogus": True})

        self.assertTrue(result["isError"])
        rows = self.refusals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error_code"], "BAD_REQUEST")
        self.assertEqual(rows[0]["tool"], "fleet_send_to_session")
        self.assertEqual(rows[0]["principal"], "oauth:test-client")
        # A field this surface could not vouch for is counted, not silently
        # dropped: `withheld` appearing here would mean the record is thinner
        # than the code that wrote it intended.
        self.assertNotIn("withheld", rows[0])

    def test_an_unknown_tool_is_recorded_without_a_tool_name(self) -> None:
        result = call_tool(self.ctx, "fleet_no_such_tool", {})

        self.assertTrue(result["isError"])
        rows = self.refusals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error_code"], "BAD_REQUEST")
        self.assertNotIn("tool", rows[0])

    def test_the_refusal_detail_never_reaches_the_log(self) -> None:
        marker = "caller_supplied_marker_9f31"
        result = call_tool(self.ctx, "fleet_send_to_session", {marker: True})

        detail = result["structuredContent"]["detail"]
        self.assertIn(marker, detail, "the caller must still be told what to fix")
        self.assertNotIn(marker, self.path.read_text(encoding="utf-8"),
                         "caller text belongs in the answer, not in the log beside it")

    def test_a_missing_scope_is_recorded_under_its_own_code(self) -> None:
        # This is the exact refusal measured on 3411 that left no trace: a
        # read-only credential asking to send.
        result = call_tool(self.ctx, "fleet_send_to_session", {
            "message": "x", "device": "device:work-fj", "session": "s",
        })

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "SCOPE_DENIED")
        rows = self.refusals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["error_code"], "SCOPE_DENIED")
        self.assertEqual(rows[0]["tool"], "fleet_send_to_session")


class WithoutAnAuditLog(unittest.TestCase):
    def test_a_refusal_still_answers_when_nothing_is_being_recorded(self) -> None:
        # An unbound audit is a deployment choice, not a reason for a tool call
        # to fail: the answer is the caller's, the record is the operator's.
        ctx = ToolContext(
            service=_Service(None),
            principal=Principal(name="oauth:test-client",
                                scopes=frozenset({"fleet:read"}),
                                credential_kind="oauth"),
        )
        result = call_tool(ctx, "fleet_no_such_tool", {})

        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "BAD_REQUEST")


if __name__ == "__main__":
    unittest.main()
