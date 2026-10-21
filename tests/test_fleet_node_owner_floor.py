"""The connector's owner floor: a grant that names no session.

`allow_send_to` is a list of sessions, and the one thing it structurally cannot
say is "any session on this machine, including one that does not exist yet" —
because a session that does not exist yet has no name to write down. The floor
added here says exactly that, and only that, and these tests are about its
*edges*: that it does not become a wildcard inside the named list, that it does
not become reachable by misspelling, that it does not widen when a verb is added
to the vocabulary, that it does not outrank the named list, and that it cannot
be turned on from outside this machine.

The tests are split the way the gate is. `TheGrantItself` and `TheBindingFile`
need no socket: the decision is a pure function of the config, so it is tested
as one. `OverTheWire` runs the real `ThreadingHTTPServer` with a fake CloudCLI
behind it, so that "the floor admitted this message" is measured at the same
boundary a caller would hit rather than at the function the boundary calls.

Only the fake provider stands in for CloudCLI; no real Agent, session or
gateway is involved anywhere in this file.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from fleet_mcp import node_protocol as np
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_mcp.node.connector import (
    OWNER_FLOOR_VERBS,
    Connector,
    NodeConfig,
    load_node_config,
    serve,
)
from fleet_mcp.providers.base import ProviderHealth

DEVICE = "device:work-fj"
OTHER_DEVICE = "device:personal-dell"
SECRET = "node-secret-do-not-commit"
NAMED = "11111111-2222-3333-4444-555555555555"
NEVER_NAMED = "99999999-8888-7777-6666-555555555555"


class FakeProvider:
    """A CloudCLI that records what reached it. Nothing here is a real session."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []

    def send(self, key, message, *, idempotency_key=None):
        self.calls.append(("send", key.ref))
        self.sent.append((key.native_session_id, message))
        return {"run_id": "run-7", "session_ref": key.ref, "queued": False}

    def list_sessions(self, limit=20, project=None, state=None):
        self.calls.append(("list_sessions", str(limit)))
        return []

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        return {"session": {"id": key.native_session_id, "state": "idle"}}

    def read_session(self, key, **kwargs):
        self.calls.append(("read_session", key.ref))
        return {"messages": [], "total": 0}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        return {"run_id": run_id, "status": "succeeded"}

    def health(self):
        return ProviderHealth(
            device_ref=DEVICE, kind="cloudcli-mcp-http", reachable=True,
            authorized=True, detail="fake", server_name="fake", server_version="0",
            tool_count=0, latency_ms=0.5,
        )


def _config(root: Path, **over) -> NodeConfig:
    (root / "node.secret").write_text(SECRET, encoding="utf-8")
    (root / "cloudcli.token").write_text("ccp_not-a-real-token", encoding="utf-8")
    fields = dict(
        device_ref=DEVICE,
        secret_anchor=root / "node.secret",
        cloudcli_endpoint="http://127.0.0.1:3001/mcp",
        cloudcli_credential_anchor=root / "cloudcli.token",
        port=0,
    )
    fields.update(over)
    return NodeConfig(**fields)


# ---------------------------------------------------------------------------
# The grant itself: a pure function of the config, so tested without a socket
# ---------------------------------------------------------------------------


class TheGrantItself(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def named_key(self) -> SessionKey:
        return SessionKey.compose(DEVICE, "claude", NAMED)

    def other_key(self) -> SessionKey:
        return SessionKey.compose(DEVICE, "claude", NEVER_NAMED)

    def test_the_default_is_closed_and_says_so(self):
        """The field's default is the behaviour that existed before it."""
        config = _config(self.root)
        self.assertEqual(config.allow_all_sessions_for_owner, ())
        self.assertIsNone(config.send_grant(self.other_key()))
        self.assertFalse(config.permits_send(self.other_key()))
        self.assertEqual(config.send_default(), "refused")

    def test_the_floor_admits_a_session_this_file_never_named(self):
        config = _config(self.root, allow_all_sessions_for_owner=("send",))
        self.assertEqual(config.send_grant(self.other_key()), "owner-floor")
        self.assertTrue(config.permits_send(self.other_key()))

    def test_the_named_list_still_wins_so_attribution_survives_withdrawal(self):
        """A named session stays named while a floor is open.

        Otherwise withdrawing the floor would have to be read as "these sends
        were the floor's", and an audit line that grows entries it did not
        admit is worse than no audit line.
        """
        config = _config(
            self.root, allow_send_to=(NAMED,), allow_all_sessions_for_owner=("send",)
        )
        self.assertEqual(config.send_grant(self.named_key()), "named")
        self.assertEqual(config.send_grant(self.other_key()), "owner-floor")

    def test_the_floor_is_what_was_granted_and_not_merely_that_something_was(self):
        """A floor for another verb does not open send.

        `control` is now a word the loader accepts, so this could be built
        through `load_node_config` — but it is built directly anyway, because
        what is being asserted is the *runtime*'s verb-specificity, not the
        loader's. A runtime that read the floor as "something was granted here"
        would open send for a device that only ever agreed to stop runs, and
        this is the test that would fail.

        The mirror image is the next test.
        """
        config = _config(self.root, allow_all_sessions_for_owner=("control",))
        self.assertIsNone(config.send_grant(self.other_key()))
        self.assertFalse(config.permits_send(self.other_key()))
        self.assertEqual(config.send_default(), "refused")

    def test_a_send_floor_opens_no_interrupt(self):
        """The other direction of the same rule, and the more dangerous one.

        `control` ends work; `send` adds to it. A device whose owner floor says
        "this operator may message anything here" has said nothing whatever
        about stopping runs, so the flag that turns control on for that device
        must still find no session the floor admits. Read the other way round,
        this would be a silent widening of a destructive verb, which is exactly
        the shape of mistake the closed vocabulary exists to prevent.
        """
        config = _config(
            self.root,
            allow_control=True,
            allow_all_sessions_for_owner=("send",),
        )
        self.assertIsNone(config.control_grant(self.other_key()))
        self.assertFalse(config.permits_control(self.other_key()))
        self.assertEqual(config.control_default(), "refused")

    def test_a_wildcard_in_the_named_list_is_still_just_a_name_that_matches_nothing(self):
        """`"*"` is the one string an operator is most likely to reach for, and
        in the named list it is a session id — one nothing will ever match. The
        floor must not be where it silently becomes "everything" either, which
        is why it is a separate key with its own closed vocabulary."""
        config = _config(self.root, allow_send_to=("*",))
        self.assertIsNone(config.send_grant(self.named_key()))
        self.assertIsNone(config.send_grant(self.other_key()))
        self.assertEqual(config.send_default(), "only the sessions named in allow_send_to")

    def test_the_default_sentence_distinguishes_the_three_states(self):
        """`send_default` is what an operator reads to answer "why was I
        refused", so "nothing is named" and "nothing may be sent" must not come
        back as the same sentence."""
        named_only = _config(self.root, allow_send_to=(NAMED,))
        floor_only = _config(self.root, allow_all_sessions_for_owner=("send",))
        both = _config(
            self.root, allow_send_to=(NAMED,), allow_all_sessions_for_owner=("send",)
        )
        closed = _config(self.root)
        sentences = {
            named_only.send_default(),
            floor_only.send_default(),
            both.send_default(),
            closed.send_default(),
        }
        self.assertEqual(len(sentences), 4, sentences)
        self.assertIn("allow_send_to", named_only.send_default())
        self.assertNotIn("allow_send_to", floor_only.send_default())
        self.assertIn("owner floor", floor_only.send_default())
        self.assertIn("owner floor", both.send_default())
        self.assertIn("allow_send_to", both.send_default())

    def test_the_key_is_always_this_machines_identity(self):
        """Neither gate can be satisfied on another device's behalf: the key is
        composed from this config's own `device_ref`, never from the request."""
        connector = Connector(_config(self.root))
        key = connector._key(NEVER_NAMED)
        self.assertEqual(key.device_ref, DEVICE)
        self.assertEqual(key.provider, "claude")
        self.assertEqual(key.native_session_id, NEVER_NAMED)

    def test_the_vocabulary_is_closed_and_names_exactly_two_verbs(self):
        """The closed vocabulary, and the words that must stay out of it.

        `control` is in this tuple because the interrupt route exists to honour
        it — the route, the provider method and the scope all ship together. So
        this test fixes the vocabulary at two words; it does not assert that one
        word implies the other. `test_the_floor_is_what_was_granted_and_not_
        merely_that_something_was` above is where the two are held apart: a
        floor of `["control"]` opens no send, and `["send"]` opens no interrupt.

        The negative list is deliberately wider than the names of things:
        `interrupt` is the operation, but `control` is the word the grant uses,
        so `interrupt` must not be spellable as a floor. `read` and `create`
        are verbs this vocabulary does not carry at all — a floor is a floor for
        a verb that has a route and a scope, and neither of those does today.
        """
        self.assertEqual(OWNER_FLOOR_VERBS, ("send", "control"))
        for word in ("*", "all", "root", "bypassPermissions", "read", "create",
                     "start", "close", "interrupt"):
            self.assertNotIn(word, OWNER_FLOOR_VERBS)


# ---------------------------------------------------------------------------
# The binding file: a floor that means nothing must not load
# ---------------------------------------------------------------------------


class TheBindingFile(unittest.TestCase):
    def _write(self, text: str) -> Path:
        handle, path = tempfile.mkstemp(suffix=".yaml")
        os.close(handle)
        Path(path).write_text(text, encoding="utf-8")
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        return Path(path)

    def _valid(self, floor: str = "") -> str:
        base = (
            "schema: serveros/fleet-node.local/v1\n"
            f"device_ref: {DEVICE}\n"
            "secret_anchor: ~/.serveros/fleet-node.secret\n"
            "cloudcli_endpoint: http://127.0.0.1:3001/mcp\n"
            "cloudcli_credential_anchor: ~/.serveros/cloudcli.pat\n"
        )
        return base + floor

    def test_a_binding_without_the_key_is_closed(self):
        config = load_node_config(self._write(self._valid()))
        self.assertEqual(config.allow_all_sessions_for_owner, ())

    def test_a_granted_send_floor_survives_the_round_trip(self):
        config = load_node_config(
            self._write(self._valid("allow_all_sessions_for_owner:\n  - send\n"))
        )
        self.assertEqual(config.allow_all_sessions_for_owner, ("send",))
        self.assertTrue(
            config.permits_send(SessionKey.compose(DEVICE, "claude", NEVER_NAMED))
        )

    def test_an_empty_floor_is_the_closed_default_not_an_error(self):
        config = load_node_config(
            self._write(self._valid("allow_all_sessions_for_owner: []\n"))
        )
        self.assertEqual(config.allow_all_sessions_for_owner, ())

    def test_a_repeated_grant_is_one_grant(self):
        config = load_node_config(
            self._write(
                self._valid("allow_all_sessions_for_owner:\n  - send\n  - send\n")
            )
        )
        self.assertEqual(config.allow_all_sessions_for_owner, ("send",))

    def test_an_unknown_word_is_refused_when_the_binding_is_read(self):
        """The failure this protects against is not an attacker widening the
        floor, it is nobody noticing the floor was never on. A word this
        connector cannot honour has to fail where an operator is watching."""
        for word in ("sends", "*", "all", "root", "bypassPermissions"):
            with self.subTest(word=word):
                with self.assertRaises(FleetRefusal) as caught:
                    load_node_config(
                        self._write(
                            # Quoted, which is how `*` has to be written anyway —
                            # bare it is a YAML alias and never reaches the
                            # validator at all.
                            self._valid(
                                f'allow_all_sessions_for_owner:\n  - "{word}"\n'
                            )
                        )
                    )
                self.assertEqual(caught.exception.code, "BAD_REQUEST")
                self.assertIn(word, caught.exception.detail)
                # The refusal has to be actionable without reading this file.
                self.assertIn("send", caught.exception.detail)

    def test_a_bare_string_is_refused_rather_than_read_as_one_verb(self):
        """`allow_all_sessions_for_owner: send` is the shape an operator writes
        by mistake, and YAML hands it over as a string. Iterating it would grant
        from the letters; refusing is the only honest reading."""
        with self.assertRaises(FleetRefusal) as caught:
            load_node_config(self._write(self._valid("allow_all_sessions_for_owner: send\n")))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("list of verbs", caught.exception.detail)

    def test_a_mapping_is_refused(self):
        with self.assertRaises(FleetRefusal):
            load_node_config(
                self._write(self._valid("allow_all_sessions_for_owner:\n  send: true\n"))
            )


# ---------------------------------------------------------------------------
# Over the wire: the real server, a fake CloudCLI, and the real signature
# ---------------------------------------------------------------------------


class _Rig(unittest.TestCase):
    allow_send_to: tuple[str, ...] = ()
    owner_floor: tuple[str, ...] = ()

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = _config(
            Path(cls.tmp.name),
            allow_send_to=tuple(cls.allow_send_to),
            allow_all_sessions_for_owner=tuple(cls.owner_floor),
        )
        cls.connector = Connector(cls.config)
        cls.provider = FakeProvider()
        cls.connector.provider = cls.provider
        cls.server = serve(cls.config, connector=cls.connector)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.provider.calls.clear()
        self.provider.sent.clear()
        self.connector.replay = np.ReplayCache()

    def request(self, method: str, path: str, body=None, *, device=DEVICE):
        """One signed request, over a real socket. Signing the path and never
        the query, the same rule `FleetNodeProvider._request` follows."""
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = dict(np.sign(
            SECRET, method=method, path=np.split_path_and_query(path)[0],
            body=raw, device_ref=device,
        ))
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=raw or None,
            headers=headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")


class FloorClosed(_Rig):
    """The default. Identical to the behaviour before the field existed."""

    def test_a_session_that_is_not_named_is_refused_and_nothing_reaches_cloudcli(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": "hello"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(payload["error"]["code"], "WRITE_NOT_GRANTED")
        self.assertEqual(self.provider.sent, [])

    def test_the_refusal_names_both_gates_and_this_machine(self):
        _, payload = self.request(
            "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": "hi"}
        )
        detail = payload["error"]["detail"]
        self.assertIn("allow_send_to", detail)
        self.assertIn("owner floor", detail)
        self.assertIn("closed", detail)
        self.assertIn("this machine", detail)

    def test_self_reports_the_floor_as_present_and_closed(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control"]["send_owner_floor"], [])
        self.assertEqual(payload["control"]["send_default"], "refused")


class FloorOpen(_Rig):
    """`send` granted for the whole machine, with nothing named in it."""

    owner_floor = ("send",)

    def test_a_session_that_does_not_exist_yet_can_be_sent_to(self):
        """This is the whole point: `NEVER_NAMED` is in no list anywhere, and
        stands in for a session created after this binding was last read."""
        status, payload = self.request(
            "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": "status?"}
        )
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["run_id"], "run-7")
        self.assertEqual(self.provider.sent, [(NEVER_NAMED, "status?")])

    def test_self_reports_the_floor_and_names_the_verb(self):
        status, payload = self.request("GET", "/v1/self")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control"]["send_owner_floor"], ["send"])
        self.assertEqual(payload["control"]["send_allow_list"], [])
        self.assertIn("owner floor", payload["control"]["send_default"])

    def test_reads_are_unaffected_by_the_floor(self):
        status, payload = self.request("GET", "/v1/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sessions"], [])
        self.assertIn(("list_sessions", "20"), self.provider.calls)

    def test_an_empty_message_is_refused_before_the_floor_is_consulted(self):
        status, payload = self.request(
            "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": "   "}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")
        self.assertEqual(self.provider.sent, [])

    def test_the_floor_leaves_a_line_in_the_local_journal(self):
        """A floor admission is explained nowhere in the binding, so the local
        journal is where "what went out under the floor" is answered."""
        message = "please-rotate-the-upstream-token"
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            self.request(
                "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": message}
            )
        line = captured.getvalue()
        self.assertIn("owner floor", line)
        self.assertIn(NEVER_NAMED, line)
        # It names the session and the gate, and carries nothing of the message.
        self.assertNotIn(message, line)

    def test_a_named_send_writes_no_floor_line(self):
        """The line has to mean "the floor admitted this". A named send is
        already explained by the binding, and duplicating it here would make the
        line useless as an exception report."""
        connector = Connector(
            NodeConfig(
                device_ref=DEVICE, secret_anchor=self.config.secret_anchor,
                cloudcli_endpoint=self.config.cloudcli_endpoint,
                cloudcli_credential_anchor=self.config.cloudcli_credential_anchor,
                allow_send_to=(NEVER_NAMED,),
            )
        )
        connector.provider = FakeProvider()
        target = f"/v1/sessions/{NEVER_NAMED}/send"
        body = json.dumps({"message": "hi"}).encode("utf-8")
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            status, _ = connector.route(
                "POST", target, body,
                dict(np.sign(SECRET, method="POST", path=target, body=body,
                             device_ref=DEVICE)),
            )
        self.assertEqual(status, 200)
        self.assertEqual(connector.provider.sent, [(NEVER_NAMED, "hi")])
        self.assertNotIn("owner floor", captured.getvalue())


class FloorCannotBeReachedFromElsewhere(_Rig):
    """An open floor is not an open door to a different device."""

    owner_floor = ("send",)

    def test_a_foreign_device_ref_in_the_path_is_refused(self):
        """The path segment is a *native session id*, and the device half of the
        key is composed from this binding; there is no request field that
        supplies it. So a caller cannot address another device by writing its
        ref where a session id goes, and this is what that looks like."""
        status, payload = self.request(
            "POST", f"/v1/sessions/{OTHER_DEVICE}%7Cclaude%7C{NEVER_NAMED}/send",
            {"message": "hello"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")
        self.assertEqual(self.provider.sent, [])

    def test_a_request_signed_for_another_device_is_refused(self):
        """`DEVICE_MISMATCH` is a 400 and not a 401: the peer is recognized, the
        message is simply not for this device. Either way the floor is
        irrelevant, because the device is checked before any route runs."""
        status, payload = self.request(
            "POST", f"/v1/sessions/{NEVER_NAMED}/send", {"message": "hello"},
            device=OTHER_DEVICE,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "DEVICE_MISMATCH")
        self.assertEqual(self.provider.sent, [])

    def test_the_floor_does_not_extend_to_reading_another_devices_session(self):
        status, payload = self.request("GET", f"/v1/sessions/{OTHER_DEVICE}%7Cclaude%7Cx")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "BAD_REQUEST")


if __name__ == "__main__":
    unittest.main()
