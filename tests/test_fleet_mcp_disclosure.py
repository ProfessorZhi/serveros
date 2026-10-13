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
from fleet_mcp import scopes as scope_module  # noqa: E402
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
    """One value, one kind. The kind is an argument, not a guess."""

    def test_a_value_without_a_kind_is_kept_by_nobody(self):
        """The default has to be the safe one: a caller that forgot to say what
        the value is gets a missing field, not a credential in a log."""
        for value in ("fleet.read_session", "device:tokyo", a_secret()):
            with self.subTest(value=value[:12]):
                self.assertEqual(disclosure.project_value(value), disclosure.OMITTED_MARK)

    def test_an_absent_value_is_absent_under_every_kind(self):
        for kind in (disclosure.COUNT, disclosure.FLAG, disclosure.STAMP, disclosure.CODE,
                     disclosure.REF, disclosure.SCOPE, disclosure.URI, disclosure.DIGEST,
                     disclosure.WITHHELD):
            with self.subTest(kind=kind):
                self.assertIsNone(disclosure.project_value(None, kind=kind))

    def test_a_count_is_a_non_negative_integer(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.COUNT)  # noqa: E731
        self.assertEqual(keep(0), 0)
        self.assertEqual(keep(7), 7)
        self.assertEqual(keep(-1), disclosure.OMITTED_MARK)
        self.assertEqual(keep(True), disclosure.OMITTED_MARK)  # a bool is not a count
        self.assertEqual(keep("7"), disclosure.OMITTED_MARK)
        self.assertEqual(keep(a_secret()), disclosure.OMITTED_MARK)

    def test_a_flag_is_a_boolean(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.FLAG)  # noqa: E731
        self.assertIs(keep(True), True)
        self.assertIs(keep(False), False)  # and a false is not read as a zero
        self.assertEqual(keep(0), disclosure.OMITTED_MARK)

    def test_a_stamp_is_the_format_this_module_writes(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.STAMP)  # noqa: E731
        self.assertEqual(keep("2026-10-13T04:05:06Z"), "2026-10-13T04:05:06Z")
        self.assertEqual(keep("last Tuesday"), disclosure.OMITTED_MARK)
        self.assertEqual(keep("Bearer " + a_secret()), disclosure.OMITTED_MARK)

    def test_a_code_is_lowercase_and_unpunctuated(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.CODE)  # noqa: E731
        for value in ("fleet.send.replay", "oauth.token", "latest", "e"):
            with self.subTest(value=value):
                self.assertEqual(keep(value), value)
        for value in ("Fleet.Send", "fleet send", "fleet.send ", "", "fleet$send"):
            with self.subTest(value=value):
                self.assertEqual(keep(value), disclosure.OMITTED_MARK)

    def test_a_reference_has_no_whitespace_and_no_punctuation_a_token_needs(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.REF)  # noqa: E731
        for value in ("device:tokyo-shared-dev-01", "oauth:fleet-abc", "device:x|claude|1", "k-1"):
            with self.subTest(value=value):
                self.assertEqual(keep(value), value)
        for value in ("Authorization: Bearer " + a_secret(), "api_key=" + a_secret(), ""):
            with self.subTest(value=value[:12]):
                self.assertEqual(keep(value), disclosure.OMITTED_MARK)

    def test_a_scope_is_a_space_separated_scope_list(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.SCOPE)  # noqa: E731
        self.assertEqual(keep("fleet:read"), "fleet:read")
        self.assertEqual(keep("fleet:read fleet:send"), "fleet:read fleet:send")
        self.assertEqual(keep("fleet:read; rm -rf /"), disclosure.OMITTED_MARK)

    def test_a_scope_token_that_is_really_a_bearer_header_is_not_a_scope(self):
        """Fixed, not random: an early version of this pattern accepted any
        RFC-6749 scope-token, which made `Bearer <hex>` two tokens whenever the
        hex happened to begin with a letter — a leak that showed up in one run
        in three. The fixture starts with a letter on purpose."""
        keep = lambda v: disclosure.project_value(v, kind=disclosure.SCOPE)  # noqa: E731
        for payload in (f"Bearer abcdef{a_secret()}", f"Bearer abcdef0123456789",
                        f"abcdef0123456789", f"fleet:read abcdef0123456789"):
            with self.subTest(payload=payload[:12]):
                self.assertEqual(keep(payload), disclosure.OMITTED_MARK)

    def test_a_uri_keeps_its_origin_and_nothing_after_it(self):
        keep = lambda v: disclosure.project_value(v, kind=disclosure.URI)  # noqa: E731
        self.assertEqual(keep("https://cb.example/cb?a=1#f"), "https://cb.example")
        self.assertEqual(keep("http://127.0.0.1:8765/cb"), "http://127.0.0.1:8765")
        self.assertEqual(keep("https://u:p@h.example/cb"), "https://h.example")
        for value in ("not a uri", "/relative/cb", "", a_secret()):
            with self.subTest(value=value[:12]):
                self.assertEqual(keep(value), disclosure.OMITTED_MARK)

    def test_a_digest_is_one_way_and_stable(self):
        value = a_secret()
        first = disclosure.project_value(value, kind=disclosure.DIGEST)
        self.assertEqual(first, disclosure.project_value(value, kind=disclosure.DIGEST))
        self.assertNotIn(value, first)
        self.assertNotEqual(first, disclosure.project_value(a_secret(), kind=disclosure.DIGEST))

    def test_a_digest_we_already_wrote_is_not_hashed_again(self):
        """`tail()` re-projects what `record()` wrote; the marker has to survive."""
        once = disclosure.project_value(a_secret(), kind=disclosure.DIGEST)
        self.assertEqual(disclosure.project_value(once, kind=disclosure.DIGEST), once)

    def test_a_long_string_is_a_payload_not_a_fact(self):
        long_value = a_secret() * 20
        self.assertGreater(len(long_value), disclosure.MAX_STRING_CHARS)
        for kind in (disclosure.REF, disclosure.CODE, disclosure.SCOPE, disclosure.URI,
                     disclosure.STAMP):
            with self.subTest(kind=kind):
                self.assertEqual(disclosure.project_value(long_value, kind=kind),
                                 disclosure.OMITTED_MARK)

    def test_a_nested_object_is_refused_rather_than_walked(self):
        """The case this module exists for: a parsed config section.

        Walking it would mean deciding per key which of its members were safe,
        which is the guess this allowlist replaces with a rule.
        """
        document = {"providers": {"claude": {"credential_anchor": "/home/x/.cloudcli/pat"}}}
        for kind in (disclosure.REF, disclosure.URI, disclosure.DIGEST, disclosure.WITHHELD):
            with self.subTest(kind=kind):
                self.assertEqual(disclosure.project_value(document, kind=kind),
                                 disclosure.OMITTED_MARK)

    def test_a_list_is_projected_item_by_item_under_the_same_kind(self):
        projected = disclosure.project_value(
            ["device:dell", "Authorization: Bearer " + a_secret()], kind=disclosure.REF
        )
        self.assertEqual(projected, ["device:dell", disclosure.OMITTED_MARK])

    def test_a_long_list_is_capped(self):
        projected = disclosure.project_value(
            [f"device:{i}" for i in range(1000)], kind=disclosure.REF
        )
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


class FreeTextIsNotAFactBecauseItIsShort(unittest.TestCase):
    """A name on the allowlist is not a licence for any value to wear it.

    The first version of the allowlist closed the field *names* and let the
    values through on a length bound, on the reasoning that a fact is short.
    That is true of a fact and false of a credential: `ccp_` plus sixteen bytes
    of hex is thirty-seven characters, `Bearer <token>` is a header, and both
    fit comfortably under two hundred. The names that a caller can fill with
    free text — `detail`, `reason`, `client_name` — and the ones that carry a
    value back from a caller — `redirect_uri`, `idempotency_key` — were
    therefore exactly the names an injected credential could be parked in.

    These tests fix the rule to the *value*: the field name selects a kind, the
    kind decides what the value may be, and free text is not a kind.
    """

    IDEMPOTENCY = "fleet.send"
    SESSION = "device:tokyo-shared-dev-01|claude|abc"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fleet-audit-"))
        self.path = self.tmp / "audit.jsonl"
        self.audit = AuditLog(AuditBinding(path=self.path, rate_limit_per_minute=10_000))

    def _raw(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_a_detail_that_relays_a_bearer_never_reaches_the_disk(self):
        secret = a_secret()
        self.audit.record("fleet.error", detail=f"Authorization: Bearer {secret}")
        self.assertNotIn(secret, self._raw())
        self.assertNotIn("Bearer", self._raw())

    def test_a_reason_that_relays_a_key_never_reaches_the_disk(self):
        secret = a_secret()
        self.audit.record("fleet.error", reason=f"api_key={secret}")
        self.assertNotIn(secret, self._raw())
        self.assertNotIn("api_key", self._raw())

    def test_a_redirect_uri_keeps_its_origin_and_loses_everything_else(self):
        """The query is where an implicit-flow token rides, and the userinfo is
        where a credential is *supposed* to be — so neither is a fact here."""
        secret = a_secret()
        self.audit.record(
            "oauth.approve",
            client_id="fleet-abc",
            redirect_uri=f"https://app:hunter2@cb.example/cb?access_token={secret}#frag",
        )
        raw = self._raw()
        for absent in (secret, "hunter2", "access_token", "cb.example/cb", "#frag"):
            self.assertNotIn(absent, raw)
        self.assertIn("https://cb.example", raw)

    def test_a_redirect_uri_keeps_a_non_default_port(self):
        """The port distinguishes the loopback client from whatever else is
        listening, so it is part of which app this is and it stays."""
        self.audit.record("oauth.approve", redirect_uri="http://127.0.0.1:8765/cb?x=1")
        self.assertIn("http://127.0.0.1:8765", self._raw())

    def test_an_idempotency_key_carrying_a_secret_is_never_stored_verbatim(self):
        secret = a_secret()
        self.audit.record(self.IDEMPOTENCY, session_ref=self.SESSION, idempotency_key=secret)
        self.assertNotIn(secret, self._raw())

    def test_a_client_name_is_free_text_and_is_not_stored(self):
        secret = a_secret()
        self.audit.record("oauth.register", client_id="fleet-abc", client_name=secret)
        self.assertNotIn(secret, self._raw())
        self.assertIn("fleet-abc", self._raw())

    def test_a_short_free_text_value_is_still_withheld(self):
        """The failure mode is "short, therefore fine". Nothing here is short
        in a way that matters, and nothing here is passed."""
        self.audit.record("fleet.error", detail="nothing to see here")
        self.assertNotIn("nothing to see here", self._raw())

    def test_a_credential_hidden_under_the_character_bound_is_still_withheld(self):
        credential = "ccp_" + secrets.token_hex(16)
        self.assertLess(len(credential), disclosure.MAX_STRING_CHARS)
        self.audit.record("fleet.error", detail=credential, reason=credential)
        self.assertNotIn(credential, self._raw())

    def test_a_record_this_build_did_not_write_is_projected_on_read_too(self):
        """The same four shapes, arriving through the read path: a file written
        by an older build, or edited by hand, still hands out nothing."""
        detail = "Authorization: Bearer " + a_secret()
        reason = "api_key=" + a_secret()
        query = a_secret()
        digest = a_secret()
        planted = [
            {"ts": "2026-01-01T00:00:00Z", "event": "fleet.error", "detail": detail},
            {"ts": "2026-01-01T00:00:00Z", "event": "fleet.error", "reason": reason},
            {"ts": "2026-01-01T00:00:00Z", "event": "oauth.approve",
             "redirect_uri": f"https://cb.example/cb?access_token={query}"},
            {"ts": "2026-01-01T00:00:00Z", "event": "fleet.send",
             "session_ref": self.SESSION, "idempotency_key": digest},
        ]
        self.path.write_text(
            "".join(json.dumps(row) + "\n" for row in planted), encoding="utf-8"
        )
        tail = self.audit.tail(10)
        wire = json.dumps(tail)
        self.assertEqual(len(tail), 4)
        for value in (detail, reason, query, digest):
            self.assertNotIn(value, wire)
        self.assertNotIn("Bearer", wire)
        self.assertNotIn("access_token", wire)
        # And the read path says something was dropped rather than looking whole.
        for row in tail[:2]:
            self.assertEqual(row["withheld"], 1)
        # The other two rows drop no *field* — they keep a fact of the right
        # shape instead: an origin, and a fingerprint. Neither is the value that
        # was planted, which is what the four assertions above just said.
        self.assertEqual(tail[2]["redirect_uri"], "https://cb.example")
        self.assertNotIn("withheld", tail[2])
        self.assertNotEqual(tail[3]["idempotency_key"], digest)
        self.assertNotIn("withheld", tail[3])
        self.assertEqual(tail[3]["session_ref"], self.SESSION)

    def test_the_same_idempotency_key_still_correlates_two_records(self):
        """Withholding the value outright would cost the log the one thing an
        idempotency key is in it for: proving the replay matched."""
        self.audit.record(self.IDEMPOTENCY, session_ref=self.SESSION, idempotency_key="k-1")
        self.audit.record("fleet.send.replay", session_ref=self.SESSION, idempotency_key="k-1")
        self.audit.record(self.IDEMPOTENCY, session_ref=self.SESSION, idempotency_key="k-2")
        tail = self.audit.tail(10)
        self.assertEqual(tail[0]["idempotency_key"], tail[1]["idempotency_key"])
        self.assertNotEqual(tail[0]["idempotency_key"], tail[2]["idempotency_key"])

    def test_the_ordinary_facts_of_a_read_come_back_whole(self):
        """The rule has to leave the log legible, or it will be turned off."""
        self.audit.record(
            "fleet.read_session",
            principal="oauth:fleet-abc",
            session_ref=self.SESSION,
            mode="latest",
        )
        row = self.audit.tail(10)[0]
        self.assertEqual(row["event"], "fleet.read_session")
        self.assertEqual(row["principal"], "oauth:fleet-abc")
        self.assertEqual(row["session_ref"], self.SESSION)
        self.assertEqual(row["mode"], "latest")
        self.assertNotIn("withheld", row)


class TheKindTableIsTheAllowlist(unittest.TestCase):
    """The durable half: a future field cannot arrive without a kind, and no
    kind is a hole through which an arbitrary string travels."""

    def test_every_allowlisted_field_has_a_kind(self):
        missing = set(disclosure.SAFE_AUDIT_FIELDS) - set(disclosure.KIND_BY_FIELD)
        self.assertEqual(
            missing,
            set(),
            "these fields are allowlisted by name but no kind says what their "
            "value may be, so the value rule would be skipped for them",
        )

    def test_the_name_set_is_derived_from_the_kind_table(self):
        """Two parallel lists drift; one list cannot."""
        self.assertEqual(set(disclosure.SAFE_AUDIT_FIELDS), set(disclosure.KIND_BY_FIELD))

    def test_no_kind_lets_a_credential_embedded_in_text_through(self):
        """Every field, fed the shapes a credential actually travels in.

        A bare token in an identifier-shaped field is a narrower question, and
        `test_free_text_fields_reject_a_bare_token` below is where it is asked.
        """
        secret = a_secret()
        payloads = [
            f"Bearer {secret}",
            f"Authorization: Bearer {secret}",
            f"api_key={secret}",
            f'{{"key": "{secret}"}}',
            f"https://cb.example/cb?access_token={secret}",
        ]
        for name in sorted(disclosure.SAFE_AUDIT_FIELDS):
            for payload in payloads:
                with self.subTest(field=name, shape=payload[:12]):
                    projected, _ = disclosure.project_record({"event": "e", name: payload})
                    self.assertNotIn(secret, json.dumps(projected))

    def test_free_text_fields_reject_a_bare_token(self):
        """The shape that no pattern can recognise: a token pasted alone.

        These three names hold text a caller chose, so the *only* answer that
        does not depend on recognising the token is to not keep the text.
        """
        token = "ccp_" + secrets.token_hex(16)
        for name in ("detail", "reason", "client_name"):
            with self.subTest(field=name):
                projected, withheld = disclosure.project_record({"event": "e", name: token})
                self.assertEqual(projected[name], disclosure.OMITTED_MARK)
                self.assertEqual(withheld, 1)

    def test_an_unknown_kind_sends_nothing(self):
        """A half-finished edit to the table — a name given a kind this module
        has no branch for — loses the field rather than passing it."""
        original = disclosure.KIND_BY_FIELD["detail"]
        disclosure.KIND_BY_FIELD["detail"] = "a-kind-from-a-future-version"
        try:
            projected, withheld = disclosure.project_record({"event": "e", "detail": "x"})
        finally:
            disclosure.KIND_BY_FIELD["detail"] = original
        self.assertEqual(projected["detail"], disclosure.OMITTED_MARK)
        self.assertEqual(withheld, 1)
        self.assertEqual(projected["event"], "e")

    def test_every_scope_this_deployment_mints_satisfies_the_scope_pattern(self):
        """The scope kind requires a `namespace:action` shape, which is a rule
        about this deployment written in a module that does not own the scope
        vocabulary. This is what keeps the two from drifting: add a scope
        without a colon and it fails here, in the commit that adds it, rather
        than being withheld in the audit months later.

        The scan reads `scopes.py`, which is where the vocabulary lives: it was
        written against `fleet.py` before the constants were gathered into one
        module, and a scan of the old file finds nothing — which would have made
        this test pass by measuring an empty list. The second assertion is what
        catches that: the names found in the source have to be the names the
        module exports, so the scan cannot go quiet again if the vocabulary
        moves a third time.
        """
        source = (ROOT / "fleet_mcp" / "scopes.py").read_text(encoding="utf-8")
        found = re.findall(r'^(SCOPE_\w+) = "([^"]+)"', source, re.MULTILINE)
        self.assertTrue(found, "no scope constants were found — the scan is broken")
        names, scopes = zip(*found)
        self.assertEqual(
            set(names),
            {name for name in dir(scope_module) if name.startswith("SCOPE_")},
            "scopes.py declares a constant this scan did not read, or the scan "
            "is reading a file that no longer holds the vocabulary",
        )
        for scope in scopes:
            with self.subTest(scope=scope):
                self.assertEqual(
                    disclosure.project_value(scope, kind=disclosure.SCOPE),
                    scope,
                    f"{scope} is issued by this deployment but the audit would "
                    f"withhold it: teach disclosure._SCOPE the shape, or stop "
                    f"minting scopes the audit cannot name",
                )

    def test_an_omitted_mark_read_back_is_not_counted_again(self):
        """`tail()` projects what `record()` already projected. Counting the
        marker twice would report two omissions where there was one."""
        once, first = disclosure.project_record({"event": "e", "detail": a_secret()})
        twice, second = disclosure.project_record(once)
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        self.assertEqual(twice, once)


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
