#!/usr/bin/env python3
"""The egress allowlist: what leaves, what is withheld, and how you can tell.

The sentences these tests keep false
------------------------------------
Each of them is something a reader of this surface could otherwise reasonably
believe:

*   "Anything the audit is handed ends up in the audit." It does not; the field
    names are closed, and the values have to be facts rather than documents.
*   "A record already on disk is what some earlier build wrote, so it is fine."
    It is re-projected when it is read, which is the case that covers a file
    edited by hand.
*   "Remote text is truncated to a safe length, so it is safe." The length was
    never the problem. A hundred and fifty characters is ample for a key.

Nothing here uses a real credential. Fixtures are generated per run, and the
shapes that exist only to be recognised are assembled from pieces at runtime so
that a scanner reading this file finds no credential in it — the same idiom
`tests/test_fleet_mcp_identity.py` uses, and for the same reason.
"""
from __future__ import annotations

import json
import re
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import disclosure  # noqa: E402
from fleet_mcp.audit import AuditLog  # noqa: E402
from fleet_mcp.config import AuditBinding  # noqa: E402


def a_secret() -> str:
    """A value worth not leaking. Random, so no list of examples can cover it."""
    return secrets.token_hex(24)


class ClassifyBodyTellsYouWhereItCameFrom(unittest.TestCase):
    """The kind is a fact about which process answered; the type string is not."""

    def test_each_shape_gets_its_own_kind(self):
        cases = [
            ('{"error": "nope"}', "application/json", "json"),
            ("event: message\ndata: {}\n", "text/event-stream", "event-stream"),
            ("<html><body>502</body></html>", "text/html", "html"),
            ("the far end said nothing useful", "text/plain", "plain-text"),
            ("", "application/json", "empty"),
            ("\x00\x01\x02", None, "binary"),
        ]
        for raw, ctype, expected in cases:
            with self.subTest(raw=raw[:12], ctype=ctype):
                self.assertEqual(disclosure.classify_body(raw, ctype), expected)

    def test_the_media_type_string_is_never_returned(self):
        """A content type is remote input too, so it is classified, not echoed."""
        marker = f"x-{a_secret()}/json"
        self.assertEqual(disclosure.classify_body("{}", marker), "json")
        self.assertNotIn(marker, disclosure.classify_body("{}", marker))

    def test_the_declared_type_wins_and_the_body_is_the_fallback(self):
        """What the far end *said* it sent is reported, because the answer to
        "which process wrote this page" is the question being asked. The body is
        sniffed only when nothing was declared."""
        self.assertEqual(disclosure.classify_body('  {"a": 1}', "text/html"), "html")
        self.assertEqual(disclosure.classify_body('  {"a": 1}', None), "json")

    def test_every_kind_returned_is_from_the_small_closed_set(self):
        for raw in ("{}", "<html>", "plain", "", "\x00", "event: x"):
            self.assertIn(
                disclosure.classify_body(raw),
                {"json", "event-stream", "html", "plain-text", "empty", "binary"},
            )


class RemoteSummaryRelaysNothingItWasGiven(unittest.TestCase):
    """The rule is not "truncate the body". It is "do not send the body"."""

    def test_a_body_is_measured_and_left_behind(self):
        secret = a_secret()
        body = f'{{"error": "invalid api key: {secret}"}}'
        summary = disclosure.remote_summary(
            "cloudcli-gateway", method="tools/call", status=500, body=body
        )
        self.assertNotIn(secret, summary)
        self.assertNotIn("invalid api key", summary)
        self.assertIn("cloudcli-gateway", summary)
        self.assertIn("HTTP 500", summary)
        self.assertIn("tools/call", summary)
        self.assertIn(f"{len(body)} bytes", summary)

    def test_an_upstream_error_message_is_measured_and_left_behind(self):
        secret = a_secret()
        summary = disclosure.remote_summary(
            "cloudcli-gateway",
            method="tools/call",
            rpc_code=-32603,
            error_message=f"upstream provider rejected {secret}",
        )
        self.assertNotIn(secret, summary)
        self.assertNotIn("upstream provider rejected", summary)
        self.assertIn("-32603", summary)

    def test_every_credential_shape_the_redactor_knows_is_also_kept_in(self):
        """Assembled at runtime, as `tests/test_fleet_mcp_identity.py` does."""
        shapes = [
            "Bearer " + secrets.token_urlsafe(30),
            "ccp_" + secrets.token_hex(16),
            "sk-ant-api03-" + "A" * 24,
            "ghp_" + "A" * 36,
            "AKIA" + "B" * 16,
            "eyJhbGciOiJIUzI1NiJ9." + "C" * 20 + "." + "D" * 20,
            "-----BEGIN RSA PRIVATE KEY-----",
        ]
        for shape in shapes:
            with self.subTest(shape=shape[:8]):
                body = json.dumps({"detail": shape, "note": shape})
                summary = disclosure.remote_summary("provider", status=502, body=body)
                self.assertNotIn(shape, summary)
                # And not any fragment long enough to be the credential itself.
                for fragment in re.findall(r"[A-Za-z0-9_\-]{16,}", shape):
                    self.assertNotIn(fragment, summary)

    def test_a_non_integer_rpc_code_is_reported_as_such_not_echoed(self):
        summary = disclosure.remote_summary("provider", rpc_code=a_secret()[:20])
        self.assertIn("not-an-integer", summary)

    def test_a_failure_with_nothing_measured_still_says_something(self):
        summary = disclosure.remote_summary("provider")
        self.assertIn("provider", summary)
        self.assertTrue(summary.endswith("."))
        self.assertIn("withheld", summary)

    def test_a_body_of_any_size_is_bounded_output(self):
        """The summary is a sentence about a body, so its length cannot follow
        the body's. A 4 MiB error page produces the same size of message."""

        class Pretend(str):
            def __len__(self) -> int:  # a body that claims to be enormous
                return 4 * 1024 * 1024

            def __iter__(self):  # and that must never actually be iterated
                raise AssertionError("the body was walked")

        summary = disclosure.remote_summary("provider", status=500, body=Pretend("x"))
        self.assertLess(len(summary), 600)
        self.assertIn("4194304 bytes", summary)


class ProjectValueKeepsFactsAndLosesDocuments(unittest.TestCase):
    def test_short_scalars_pass_through_unchanged(self):
        for value in (None, True, False, 0, -1, 42, 1.5, "", "fleet.read_session", "device:tokyo"):
            with self.subTest(value=value):
                self.assertEqual(disclosure.project_value(value), value)

    def test_a_false_is_not_read_as_a_zero(self):
        self.assertIs(disclosure.project_value(False), False)

    def test_a_long_string_is_a_payload_not_a_fact(self):
        long_value = a_secret() * 20
        self.assertGreater(len(long_value), disclosure.MAX_STRING_CHARS)
        self.assertEqual(disclosure.project_value(long_value), disclosure.OMITTED_MARK)

    def test_a_string_exactly_at_the_bound_is_a_fact(self):
        value = "x" * disclosure.MAX_STRING_CHARS
        self.assertEqual(disclosure.project_value(value), value)
        over = "x" * (disclosure.MAX_STRING_CHARS + 1)
        self.assertEqual(disclosure.project_value(over), disclosure.OMITTED_MARK)

    def test_a_nested_object_is_refused_rather_than_walked(self):
        """The case this module exists for: a parsed config section.

        Walking it would mean deciding per key which of its members were safe,
        which is the guess this allowlist replaces with a rule.
        """
        document = {"providers": {"claude": {"credential_anchor": "/home/x/.cloudcli/pat"}}}
        self.assertEqual(disclosure.project_value(document), disclosure.OMITTED_MARK)

    def test_a_list_of_facts_is_projected_item_by_item(self):
        projected = disclosure.project_value(["a", a_secret() * 20, 3])
        self.assertEqual(projected, ["a", disclosure.OMITTED_MARK, 3])

    def test_a_long_list_is_capped(self):
        projected = disclosure.project_value([f"device:{i}" for i in range(1000)])
        self.assertEqual(len(projected), disclosure.MAX_LIST_ITEMS)


class ProjectRecordNamesWhatItWithheld(unittest.TestCase):
    def test_an_unlisted_field_name_is_withheld_and_counted(self):
        projected, withheld = disclosure.project_record(
            {"event": "fleet.send", "principal": "owner", "authorization_header": a_secret()}
        )
        self.assertEqual(withheld, 1)
        self.assertNotIn("authorization_header", projected)
        self.assertEqual(projected["withheld"], 1)

    def test_a_withheld_value_is_marked_in_place(self):
        """A field that simply vanished would make a truncated record look whole."""
        projected, withheld = disclosure.project_record({"detail": a_secret() * 20})
        self.assertEqual(projected["detail"], disclosure.OMITTED_MARK)
        self.assertEqual(withheld, 1)

    def test_projection_is_idempotent(self):
        """Reading the file back must not count the same omission again."""
        once, first = disclosure.project_record({"event": "e", "nope": "x"})
        twice, second = disclosure.project_record(once)
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(twice, once)

    def test_reprojecting_a_clean_record_does_not_invent_a_count(self):
        projected, withheld = disclosure.project_record({"event": "e", "principal": "owner"})
        self.assertEqual(withheld, 0)
        self.assertNotIn("withheld", projected)
        again, count = disclosure.project_record(projected)
        self.assertEqual(again, projected)
        self.assertEqual(count, 0)

    def test_the_allowlist_is_a_superset_of_what_the_surface_records(self):
        """Every field name any caller passes, checked against the allowlist.

        This is the test that makes the allowlist a control rather than a
        snapshot: a future `record()` call with a field nobody allowlisted
        fails here, in the same commit, instead of quietly dropping a fact.
        """
        used: dict[str, list[str]] = {}
        for path in sorted((ROOT / "fleet_mcp").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for args in _record_arguments(source):
                for name in _keyword_names(args):
                    used.setdefault(name, []).append(path.name)
        self.assertTrue(used, "no `record(` call sites were found — the scan is broken")
        unknown = {name: where for name, where in used.items() if name not in disclosure.SAFE_AUDIT_FIELDS}
        self.assertEqual(
            unknown,
            {},
            "these audit fields are not in disclosure.SAFE_AUDIT_FIELDS, so they "
            "would be withheld: add them there if they are facts, or stop "
            "recording them",
        )


class TheAuditAppliesTheRuleInBothDirections(unittest.TestCase):
    """Not "the callers are careful" — the module, on write and on read."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fleet-audit-"))
        self.path = self.tmp / "audit.jsonl"
        self.audit = AuditLog(AuditBinding(path=self.path, rate_limit_per_minute=10_000))

    def test_a_withheld_field_never_reaches_the_disk(self):
        secret = a_secret()
        self.audit.record("fleet.send", principal="owner", authorization=f"Bearer {secret}")
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn("Bearer", raw)
        self.assertIn("fleet.send", raw)

    def test_an_oversized_value_never_reaches_the_disk(self):
        secret = a_secret() * 20
        self.audit.record("fleet.read_session", session_ref="device:x|claude|1", note=secret)
        self.assertNotIn(secret, self.path.read_text(encoding="utf-8"))

    def test_a_nested_document_never_reaches_the_disk(self):
        self.audit.record(
            "oauth.register",
            client_id="fleet-abc",
            provider_config={"apiKey": a_secret()},
        )
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn("apiKey", raw)
        self.assertIn("fleet-abc", raw)

    def test_a_record_this_build_did_not_write_is_still_projected_on_read(self):
        """The case a write-side-only rule misses: a file edited by hand."""
        secret = a_secret()
        planted = {
            "ts": "2026-01-01T00:00:00Z",
            "event": "fleet.send",
            "principal": "owner",
            "SESSION_TOKEN": secret,
            "nested": {"apiKey": secret},
        }
        self.path.write_text(json.dumps(planted) + "\n", encoding="utf-8")
        tail = self.audit.tail(10)
        self.assertEqual(len(tail), 1)
        self.assertNotIn("SESSION_TOKEN", tail[0])
        self.assertNotIn("nested", tail[0])
        self.assertNotIn(secret, json.dumps(tail[0]))
        self.assertEqual(tail[0]["withheld"], 2)

    def test_a_line_that_is_not_a_record_is_skipped(self):
        self.path.write_text('[{"event": "not a record"}]\n{}\nnot json\n', encoding="utf-8")
        self.assertEqual(self.audit.tail(10), [])

    def test_ordinary_events_come_back_unchanged(self):
        """The rule must not cost the log its reason for existing."""
        self.audit.record(
            "fleet.list_sessions",
            principal="owner",
            devices=2,
            sessions=7,
            unreachable=["device:dell"],
        )
        tail = self.audit.tail(10)
        self.assertEqual(tail[0]["devices"], 2)
        self.assertEqual(tail[0]["sessions"], 7)
        self.assertEqual(tail[0]["unreachable"], ["device:dell"])
        self.assertNotIn("withheld", tail[0])


# -- reading the call sites -------------------------------------------------


def _record_arguments(source: str) -> list[str]:
    """Every `.record(...)` argument list in one module, balanced.

    A regex cannot do this — the arguments contain parentheses and nested calls
    and run across lines — so the parentheses are counted, skipping string
    literals, which is enough for the shape of code that appears here.
    """
    out: list[str] = []
    for match in re.finditer(r"\.record\(", source):
        depth = 1
        index = match.end()
        quote: str | None = None
        while index < len(source) and depth:
            char = source[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1
        out.append(source[match.end(): index - 1])
    return out


def _keyword_names(arguments: str) -> list[str]:
    """The `name=` keywords in one argument list. `==` is not one."""
    return re.findall(r"(?:^|[,(]\s*)([a-z_][a-z0-9_]*)\s*=(?!=)", arguments)


if __name__ == "__main__":
    unittest.main()
