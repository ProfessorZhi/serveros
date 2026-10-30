"""The Fleet event inbox (§5.1): seq is the cursor, a fact is recorded once,
and a cursor only moves forward. Offline: a temporary SQLite file, nothing else.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.inbox import (  # noqa: E402
    EventInbox, InboxError, MAX_LIMIT, MAX_SUMMARY, event_id_for,
)

TOKYO = "device:tokyo-shared-dev-01"
DELL = "device:llm-lab-dell"


def run_event(run_id="run-1", kind="run.completed", device=TOKYO, **extra):
    event = {"kind": kind, "level": "notify", "device_ref": device,
             "runtime": "claude", "session_ref": f"{device}|claude|s-1",
             "run_id": run_id, "project": "serveros",
             "summary": f"{run_id} {kind}"}
    event.update(extra)
    return event


class InboxTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.path = self.dir / "inbox.sqlite"
        self.inbox = EventInbox(self.path)

    def tearDown(self):
        self._td.cleanup()


class TheFileIsPrivate(InboxTest):
    def test_created_owner_only(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_a_world_readable_file_is_refused(self):
        os.chmod(self.path, 0o644)
        with self.assertRaises(InboxError):
            EventInbox(self.path)

    def test_a_symlinked_file_is_refused(self):
        link = self.dir / "link.sqlite"
        link.symlink_to(self.path)
        with self.assertRaises(InboxError):
            EventInbox(link)


class AFactIsRecordedOnce(InboxTest):
    def test_seq_is_monotonic(self):
        seqs = [self.inbox.record(run_event(f"run-{i}")).seq for i in range(5)]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 5)

    def test_the_same_run_fact_observed_twice_is_one_row(self):
        first = self.inbox.record(run_event(occurred_at="2026-10-30T01:00:00Z"))
        again = self.inbox.record(run_event(occurred_at="2026-10-30T01:05:00Z",
                                            summary="seen again"))
        self.assertTrue(first.created)
        self.assertFalse(again.created)
        self.assertEqual(first.seq, again.seq)
        self.assertEqual(len(self.inbox.read("x")["events"]), 1)

    def test_a_different_terminal_state_is_a_different_fact(self):
        a = self.inbox.record(run_event(kind="run.unknown"))
        b = self.inbox.record(run_event(kind="run.completed"))
        self.assertNotEqual(a.seq, b.seq)

    def test_a_node_fact_recurs_per_transition(self):
        down = {"kind": "node.unreachable", "level": "notify", "device_ref": DELL,
                "summary": "dell unreachable"}
        a = self.inbox.record(dict(down, occurred_at="2026-10-30T01:00:00Z"))
        b = self.inbox.record(dict(down, occurred_at="2026-10-30T03:00:00Z"))
        self.assertTrue(a.created and b.created)
        self.assertNotEqual(event_id_for(dict(down, occurred_at="1")),
                            event_id_for(dict(down, occurred_at="2")))


class TheInputIsBounded(InboxTest):
    def test_unknown_kind_and_level_are_refused(self):
        with self.assertRaises(InboxError):
            self.inbox.record(run_event(kind="run.done"))
        with self.assertRaises(InboxError):
            self.inbox.record(run_event(level="urgent"))

    def test_a_summary_is_required_and_capped(self):
        with self.assertRaises(InboxError):
            self.inbox.record(run_event(summary="   "))
        seq = self.inbox.record(run_event(summary="x" * 5000)).seq
        row = self.inbox.read("x")["events"][0]
        self.assertEqual(row["seq"], seq)
        self.assertEqual(len(row["summary"]), MAX_SUMMARY)

    def test_control_characters_do_not_survive(self):
        self.inbox.record(run_event(summary="ok\x1b[31mred\x00"))
        self.assertNotIn("\x1b", self.inbox.read("x")["events"][0]["summary"])

    def test_a_device_ref_must_be_a_device_reference(self):
        with self.assertRaises(InboxError):
            self.inbox.record(run_event(device="tokyo"))


class ReadingAndCursors(InboxTest):
    def fill(self, n, device=TOKYO):
        return [self.inbox.record(run_event(f"run-{device}-{i}", device=device)).seq
                for i in range(n)]

    def test_paging_with_since(self):
        seqs = self.fill(5)
        page = self.inbox.read("gpt", limit=2)
        self.assertEqual([e["seq"] for e in page["events"]], seqs[:2])
        self.assertTrue(page["more"])
        rest = self.inbox.read("gpt", since=page["next_since"], limit=10)
        self.assertEqual([e["seq"] for e in rest["events"]], seqs[2:])
        self.assertFalse(rest["more"])

    def test_without_ack_the_cursor_does_not_move(self):
        self.fill(3)
        self.inbox.read("gpt")
        self.assertEqual(self.inbox.cursor("gpt"), 0)
        self.assertEqual(len(self.inbox.read("gpt")["events"]), 3)

    def test_ack_advances_only_the_callers_cursor(self):
        seqs = self.fill(3)
        page = self.inbox.read("gpt", ack=True)
        self.assertEqual(page["cursor"], seqs[-1])
        self.assertEqual(self.inbox.read("gpt")["events"], [])
        self.assertEqual(len(self.inbox.read("claude-commander")["events"]), 3)

    def test_a_cursor_never_moves_backwards(self):
        seqs = self.fill(4)
        self.inbox.read("gpt", ack=True)
        replay = self.inbox.read("gpt", since=0, ack=True)
        self.assertEqual(len(replay["events"]), 4)
        self.assertEqual(self.inbox.cursor("gpt"), seqs[-1])
        older = self.inbox.read("gpt", since=0, limit=1, ack=True)
        self.assertEqual(older["cursor"], seqs[-1])

    def test_unreadable_devices_are_dropped_but_passed(self):
        tokyo = self.fill(2)
        self.fill(2, device=DELL)
        page = self.inbox.read("gpt", readable=lambda ref: ref == TOKYO, ack=True)
        self.assertEqual([e["seq"] for e in page["events"]], tokyo)
        self.assertEqual(self.inbox.read("gpt")["events"], [])

    def test_kind_and_level_filters(self):
        self.inbox.record(run_event("a", kind="run.completed"))
        self.inbox.record(run_event("b", kind="run.failed", level="decide"))
        self.assertEqual([e["run_id"] for e in self.inbox.read("x", kinds=["run.failed"])["events"]], ["b"])
        self.assertEqual([e["run_id"] for e in self.inbox.read("x", level="decide")["events"]], ["b"])

    def test_a_page_larger_than_a_scan_batch(self):
        seqs = self.fill(600)
        page = self.inbox.read("gpt", limit=MAX_LIMIT)
        self.assertEqual(len(page["events"]), MAX_LIMIT)
        self.assertTrue(page["more"])
        everything, since = [], 0
        while True:
            page = self.inbox.read("gpt", since=since, limit=MAX_LIMIT)
            everything += [e["seq"] for e in page["events"]]
            since = page["next_since"]
            if not page["more"]:
                break
        self.assertEqual(everything, seqs)

    def test_bad_requests_are_refused(self):
        for kwargs in ({"limit": 0}, {"limit": MAX_LIMIT + 1}, {"since": -1},
                       {"kinds": ["nope"]}, {"level": "x"}, {"since": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(InboxError):
                self.inbox.read("gpt", **kwargs)

    def test_state_survives_a_new_handle(self):
        seqs = self.fill(3)
        self.inbox.read("gpt", ack=True)
        reopened = EventInbox(self.path)
        self.assertEqual(reopened.cursor("gpt"), seqs[-1])
        self.assertFalse(reopened.record(run_event(f"run-{TOKYO}-0")).created)
        self.assertTrue(reopened.record(run_event("run-new")).created)


if __name__ == "__main__":
    unittest.main()


# -- through the Fleet surface ------------------------------------------------

from fleet_mcp import tools as fleet_tools  # noqa: E402
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from test_fleet_mcp_surface import Harness  # noqa: E402


class TheToolReadsTheHubInbox(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = Path(self._td.name)
        self.harness = Harness(tmp)
        self.service = self.harness.service
        self.inbox = EventInbox(tmp / "inbox.sqlite")

    def tearDown(self):
        self._td.cleanup()

    def call(self, principal, **args):
        ctx = fleet_tools.ToolContext(service=self.service, principal=principal)
        return fleet_tools.call_tool(ctx, "fleet_inbox", args)

    def test_it_is_served_as_a_read(self):
        tool = fleet_tools.SERVED_BY_NAME["fleet_inbox"]
        self.assertTrue(tool.annotations["readOnlyHint"])
        self.assertIn("NOT_CONFIGURED", tool.description)

    def test_no_inbox_bound_is_said_not_shown_as_empty(self):
        answer = self.service.read_inbox(self.harness.principal("fleet:read"))
        self.assertEqual(answer["inbox"], "NOT_CONFIGURED")
        self.assertEqual(answer["events"], [])

    def test_reading_needs_fleet_read(self):
        self.service.inbox = self.inbox
        with self.assertRaises(FleetRefusal) as caught:
            self.service.read_inbox(self.harness.principal("fleet:send"))
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")

    def test_only_enrolled_devices_are_returned(self):
        self.service.inbox = self.inbox
        self.inbox.record(run_event("enrolled", device=TOKYO))
        self.inbox.record(run_event("stranger", device="device:not-in-registry"))
        answer = self.service.read_inbox(self.harness.principal("fleet:read"))
        self.assertEqual(answer["inbox"], "OK")
        self.assertEqual([e["run_id"] for e in answer["events"]], ["enrolled"])

    def test_each_principal_has_its_own_cursor(self):
        self.service.inbox = self.inbox
        self.inbox.record(run_event("one"))
        gpt = self.harness.principal("fleet:read", name="gpt")
        cc = self.harness.principal("fleet:read", name="claude-commander")
        self.assertEqual(len(self.service.read_inbox(gpt, ack=True)["events"]), 1)
        self.assertEqual(self.service.read_inbox(gpt)["events"], [])
        self.assertEqual(len(self.service.read_inbox(cc)["events"]), 1)

    def test_a_bad_argument_is_a_typed_refusal(self):
        self.service.inbox = self.inbox
        answer = self.call(self.harness.principal("fleet:read"), limit=500)
        self.assertTrue(answer.get("isError"))
        self.assertIn("BAD_REQUEST", str(answer))
