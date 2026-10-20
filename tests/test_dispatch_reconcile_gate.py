"""#245 / S2 — the native `agent.dispatch.reconcile` gate.

WHY THIS FILE EXISTS
--------------------
`integrations/v1-effect-reconciliation/effect_reconciliation.py` (#299) is the
driver that asks an owner what happened to an EFFECT_UNKNOWN attempt. It had no
caller on the daemon's socket: `agent.dispatch.resolve` and
`agent.dispatch.record` existed, and nothing connected them to an observation
source after a timeout or a reconnect.

This file pins the native entry, `runtime/daemon.py#_r_agent_dispatch_reconcile`:

  * the request carries only `context_ref` and `key`; the principal is resolved
    from the trusted, FRESH context, the writer from the attempt's own session
    binding, and the observation source and grant verifier from the DAEMON's
    construction — never from the request;
  * the production answer is `BLOCKED_PROVIDER_SOURCE` because neither current
    adapter can be asked by the client turn id after a lost response (R1 #308
    and the #299 capability table: BotMux needs the lost `trigger_id`, Codex
    needs the lost `turn.id`);
  * an attempt with a recorded `grant_ref` is `BLOCKED_GRANT_UNKNOWN` unless a
    trusted grant verifier is bound; the future #272 `agent.lease.authority.
    verify` adapter is that seam;
  * no path dispatches, retries, or marks success. A refused reconciliation
    leaves the attempt exactly where it was, which is what keeps the retry door
    closed.

The `CLIENT_TURN_ID` cases use a recording TEST DOUBLE. They prove the gate and
the kernel path; they are not a production source, and no provider or paid send
is involved.

LEVEL: LOCAL / HERMETIC. Temp daemon state, a temp socket path never bound, no
network, no provider, no model call.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import tempfile
import unittest
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


daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime" / "daemon.py")
driver_mod = _load(
    "serveros_integrations_effect_reconciliation",
    REPO / "integrations" / "v1-effect-reconciliation" / "effect_reconciliation.py")
attempts = daemon_mod.dispatch_attempts_mod
coordination = daemon_mod.coordination_mod
actor = daemon_mod.actor_mod

ITEM = "SRV-0001"
OTHER_ITEM = "SRV-9999"
KEY = "effect-key-1"
PRINCIPAL = "human:A"
OTHER_PRINCIPAL = "human:B"
SESSION = "session:A"
WRITER = {"owner": "agent:A", "epoch": 3, "token": "3"}


def a_context(now: str, *, actor_ref: str = PRINCIPAL) -> dict:
    return actor.build({
        "human_control_provider": "provider:test",
        "transport": "feishu",
        "transport_identity": "test",
        "conversation_ref": "oc_reconcile",
        "message_ref": "om_reconcile",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_ref,
        "provider_pid": os.getpid(),
    }, now=now)


def a_digest(**overrides) -> str:
    values = dict(
        action="agent.session.send", device_ref="device:test",
        verb="session.send", params={"instruction": "do it"},
        work_item=ITEM, session_ref=SESSION, principal=PRINCIPAL)
    values.update(overrides)
    return attempts.intent_digest(**values)


def an_observation(state: str = "APPLIED", *, digest: str | None = None,
                   result=None) -> dict:
    out = {"state": state, "source": "test:owner-journal",
           "intent_digest": a_digest() if digest is None else digest}
    if result is not None:
        out["result"] = result
    return out


class RecordingSource:
    """A test source. It records reads; it is not a provider adapter."""

    def __init__(self, capability: str, *, observation=None, exc=None) -> None:
        self.capability = capability
        self._observation = observation
        self._exc = exc
        self.calls: list[str] = []

    def observe(self, recorded, *, key):
        self.calls.append(key)
        if self._exc is not None:
            raise self._exc
        return self._observation


class ReconcileGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "run").mkdir()
        self.state = self.root / "state"

    # -- fixture -----------------------------------------------------------

    def _daemon(self, *, source=None, grant_verifier=None):
        registry = daemon_mod.registry_mod.Registry(document={
            "schema": "serveros/managed-runtime-registry.v1",
            "device_ref": "device:test",
            "resources": [],
        })
        return daemon_mod.Daemon(
            registry,
            state_dir=self.state,
            socket_path=self.root / "run" / "serveros.sock",
            log=lambda message: None,
            dispatch_observation_source=source,
            dispatch_grant_verifier=grant_verifier,
        )

    def _seed(self, daemon, *, phase: str = attempts.EFFECT_UNKNOWN,
              principal: str = PRINCIPAL, work_item=ITEM,
              session_ref: str = SESSION, grant_ref=None,
              digest_value: str | None = None, writer=WRITER,
              binding_item=ITEM, role: str = "execution",
              extra_binding=None):
        now = daemon_mod._iso(daemon._now())
        context = a_context(now)
        store = daemon.control_store()
        store.put_context(context)
        if role == "execution":
            binding = coordination.binding(
                session_role="execution",
                bound_item_ref=binding_item or ITEM,
                device_ref="device:test",
                recorded_at=now,
                writer=writer,
            )
        else:
            binding = coordination.binding(
                session_role="coordinator",
                device_ref="device:test",
                recorded_at=now,
            )
        binding["session_ref"] = session_ref
        store.put_binding(binding)
        if extra_binding is not None:
            extra_ref, extra_writer = extra_binding
            extra = coordination.binding(
                session_role="execution", bound_item_ref=binding_item or ITEM,
                device_ref="device:test", recorded_at=now, writer=extra_writer)
            extra["session_ref"] = extra_ref
            store.put_binding(extra)
        record = attempts.attempt_record(
            key=KEY,
            action="agent.session.send",
            device_ref="device:test",
            verb="session.send",
            intent_digest=digest_value or a_digest(),
            phase=phase,
            work_item=work_item,
            principal=principal,
            session_ref=session_ref,
            grant_ref=grant_ref,
            result={"ok": True} if phase == attempts.SETTLED else None,
        )
        store.put_dispatch_attempt(record, actor=principal)
        return context, store

    def _reconcile(self, daemon, context, *, key: str = KEY, **extra) -> dict:
        params = {"context_ref": context["context_ref"], "key": key}
        params.update(extra)
        answer = daemon.handle(
            {"method": "agent.dispatch.reconcile", "params": params},
            peer={"pid": os.getpid(), "uid": os.getuid(),
                  "gid": os.getgid()})
        self.assertTrue(answer.get("ok"), answer)
        return answer["result"]

    def _assert_unknown(self, store) -> None:
        self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                         attempts.EFFECT_UNKNOWN)

    # -- provider capability is the production gate ------------------------

    def test_no_source_is_blocked_and_writes_nothing(self) -> None:
        daemon = self._daemon(source=None)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "BLOCKED_PROVIDER_SOURCE")
        self.assertIs(result["dispatching"], False)
        self.assertFalse(result["source_called"])
        self._assert_unknown(store)

    def test_a_response_id_only_source_is_blocked_and_not_called(self) -> None:
        source = RecordingSource(driver_mod.RESPONSE_ID_ONLY,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "BLOCKED_PROVIDER_SOURCE")
        self.assertEqual(result["source_capability"], driver_mod.RESPONSE_ID_ONLY)
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_an_unknown_capability_is_not_read_as_client_turn_id(self) -> None:
        source = RecordingSource("SOMETHING_ELSE", observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "BLOCKED_PROVIDER_SOURCE")
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    # -- identity is resolved, never asserted -----------------------------

    def test_a_missing_or_unresolved_context_is_untrusted_actor(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        _context, store = self._seed(daemon)
        for context_ref in (None, "", "ctx:invented:deadbeef"):
            with self.subTest(context_ref=context_ref):
                params = {"key": KEY}
                if context_ref is not None:
                    params["context_ref"] = context_ref
                answer = daemon.handle(
                    {"method": "agent.dispatch.reconcile", "params": params},
                    peer={"pid": os.getpid(), "uid": os.getuid(),
                          "gid": os.getgid()})
                self.assertTrue(answer.get("ok"), answer)
                self.assertEqual(answer["result"]["status"], "UNTRUSTED_ACTOR")
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_a_stale_context_is_untrusted_actor(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        _context, store = self._seed(daemon)
        stale = a_context(daemon_mod._iso(daemon._now() - 7200))
        store.put_context(stale)
        result = self._reconcile(daemon, stale)
        self.assertEqual(result["status"], "UNTRUSTED_ACTOR")
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_a_cross_principal_observation_settles_nothing(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        _context, store = self._seed(daemon, principal=PRINCIPAL)
        other = a_context(daemon_mod._iso(daemon._now()),
                          actor_ref=OTHER_PRINCIPAL)
        store.put_context(other)
        result = self._reconcile(daemon, other)
        self.assertEqual(result["status"], "REFUSE_PRINCIPAL")
        self.assertEqual(source.calls, [],
                         "a different principal must not cause a provider read")
        self._assert_unknown(store)

    def test_a_cross_item_attempt_has_no_current_writer(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon, work_item=ITEM,
                                    binding_item=OTHER_ITEM)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "REFUSE_UNKNOWN")
        self._assert_unknown(store)

    def test_a_stale_writer_settles_nothing(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(
            daemon,
            writer={"owner": "agent:old", "epoch": 1, "token": "1"},
            extra_binding=("session:B",
                           {"owner": "agent:new", "epoch": 3, "token": "3"}))
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "REFUSE_STALE_WRITER")
        self._assert_unknown(store)

    # -- the grant authority is a daemon-held seam ------------------------

    def test_a_grant_ref_without_a_verifier_is_blocked(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon, grant_ref="grant:1")
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "BLOCKED_GRANT_UNKNOWN")
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_a_stale_grant_is_refused_before_the_source(self) -> None:
        def stale_grant(attempt, *, context, now):
            return {"verified": False, "reason": "GRANT_REVOKED",
                    "because": "the grant was revoked"}

        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source, grant_verifier=stale_grant)
        context, store = self._seed(daemon, grant_ref="grant:1")
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "GRANT_REVOKED")
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_a_live_grant_verifier_is_an_admission_seam_not_a_success_path(self) -> None:
        def live_grant(attempt, *, context, now):
            return {"verified": True, "grant_ref": attempt["grant_ref"],
                    "epoch": 9}

        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation(result={"ok": True}))
        daemon = self._daemon(source=source, grant_verifier=live_grant)
        context, store = self._seed(daemon, grant_ref="grant:1")
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], attempts.SETTLE_APPLIED)
        self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                         attempts.SETTLED)
        self.assertIs(result["dispatching"], False)

    # -- owner answers: failure and unknown decide nothing ----------------

    def test_a_source_failure_leaves_the_effect_unknown(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 exc=TimeoutError("owner did not answer"))
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        self._assert_unknown(store)

    def test_an_unknown_observation_decides_nothing(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation("UNKNOWN"))
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
        self._assert_unknown(store)

    def test_a_terminal_attempt_is_not_offered_to_the_source(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        # The attempt carries a grant_ref and the daemon has no verifier. The
        # phase gate must win: an attempt that already has its answer does not
        # need a grant read, and a late reply must not be offered to the source.
        context, store = self._seed(daemon, phase=attempts.SETTLED,
                                    grant_ref="grant:1")
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], "NOT_UNKNOWN")
        self.assertEqual(source.calls, [])
        self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                         attempts.SETTLED)

    # -- the same existing ledger -----------------------------------------

    def test_an_authoritative_applied_settles_in_the_same_ledger(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation(result={"ok": True}))
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], attempts.SETTLE_APPLIED)
        self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                         attempts.SETTLED)
        self.assertIs(result["dispatching"], False)
        files = sorted(path.name for path in store.path.parent.iterdir())
        self.assertTrue(set(files) <= {"store.json", store.lock_path.name},
                        f"a second state artifact appeared: {files}")

    def test_a_coordinator_scope_needs_no_writer(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation(result={"ok": True}))
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon, work_item=None,
                                    role="coordinator")
        result = self._reconcile(daemon, context)
        self.assertEqual(result["status"], attempts.SETTLE_APPLIED)
        self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                         attempts.SETTLED)

    def test_lost_reply_restart_retry_stays_unknown(self) -> None:
        source = RecordingSource(driver_mod.RESPONSE_ID_ONLY,
                                 observation=an_observation())
        first = self._daemon(source=source)
        context, _store = self._seed(first)
        self.assertEqual(self._reconcile(first, context)["status"],
                         "BLOCKED_PROVIDER_SOURCE")

        restarted = self._daemon(source=source)
        read = restarted.handle(
            {"method": "agent.dispatch.resolve", "params": {"key": KEY}},
            peer={"pid": os.getpid(), "uid": os.getuid(),
                  "gid": os.getgid()})["result"]
        self.assertTrue(read["found"])
        self.assertEqual(read["attempt"]["phase"], attempts.EFFECT_UNKNOWN)
        self.assertEqual(self._reconcile(restarted, context)["status"],
                         "BLOCKED_PROVIDER_SOURCE")
        verdict = driver_mod.EffectReconciler(
            attempts=restarted.control_store(),
            observation_source=source).retry_verdict(KEY, digest=a_digest())
        self.assertEqual(verdict["verdict"], attempts.REFUSE_UNKNOWN)

    # -- the request cannot carry authority or a verdict ------------------

    def test_forbidden_authority_arguments_are_refused(self) -> None:
        source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                 observation=an_observation())
        daemon = self._daemon(source=source)
        context, store = self._seed(daemon)
        forbidden = {
            "principal": OTHER_PRINCIPAL,
            "writer": WRITER,
            "current_writer": WRITER,
            "source": source,
            "capability": driver_mod.CLIENT_TURN_ID,
            "observation": an_observation(),
            "reconciler": {"principal": PRINCIPAL},
            "grant_ref": "grant:forged",
            "attempt": {"phase": attempts.SETTLED},
            "phase": attempts.SETTLED,
            "result": {"ok": True},
            "success": True,
        }
        for field, value in forbidden.items():
            with self.subTest(field=field):
                result = self._reconcile(daemon, context, **{field: value})
                self.assertEqual(result["status"], "INVALID_CONTEXT", field)
        self.assertEqual(source.calls, [])
        self._assert_unknown(store)

    def test_same_key_different_payload_is_refused_by_the_record_route(self) -> None:
        daemon = self._daemon(source=None)
        context, store = self._seed(daemon)
        changed = {
            "key": KEY,
            "action": "agent.session.send",
            "device_ref": "device:test",
            "verb": "session.send",
            "intent_digest": a_digest(params={"instruction": "something else"}),
            "phase": attempts.EFFECT_UNKNOWN,
            "work_item": ITEM,
            "session_ref": SESSION,
        }
        answer = daemon.handle(
            {"method": "agent.dispatch.record",
             "params": {"context_ref": context["context_ref"],
                        "attempt": changed}},
            peer={"pid": os.getpid(), "uid": os.getuid(),
                  "gid": os.getgid()})
        self.assertTrue(answer.get("ok"), answer)
        result = answer["result"]
        self.assertFalse(result["recorded"])
        self.assertEqual(result["reason"], "INVALID_CONTEXT")
        self.assertEqual(store.get_dispatch_attempt(KEY)["intent_digest"],
                         a_digest())
        self._assert_unknown(store)


if __name__ == "__main__":
    unittest.main()
