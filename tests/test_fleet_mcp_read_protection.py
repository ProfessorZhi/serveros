#!/usr/bin/env python3
"""Read-path protection: what is collapsed, what is never collapsed, and what
a caller is told when it is.

Two halves. The first exercises `ReadGuard` directly, because the two promises
it makes -- a failure is never stored, and nothing completed is served without
a configured TTL -- are properties of that object and are worth pinning where
they are implemented. The second drives them through `FleetService`, because
the property that matters operationally is the composition: authorisation runs
before the guard, one device's failure stays one device's failure, and a caller
that was answered from someone else's concurrent measurement cannot mistake it
for a cached one.

Counts are asserted, not durations. A timing assertion on a shared host measures
the host; a provider-invocation count measures the change.
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.audit import AuditLog  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.errors import (  # noqa: E402
    PROVIDER_TIMEOUT,
    PROVIDER_UNREACHABLE,
    RATE_LIMITED,
    SCOPE_DENIED,
    FleetRefusal,
    refuse,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    MANAGED_IDLE,
    RUNNING_AGENT,
    ProviderHealth,
    SessionSummary,
    classify,
)
from fleet_mcp.readguard import ReadGuard, canonical_value  # noqa: E402

WINDOWS = "device:work-fj"
TOKYO = "device:tokyo-shared-dev-01"

# Long enough that a caller arriving after the leader can be relied upon to
# arrive while the leader is still inside the provider; short enough that the
# suite does not notice. The gated providers below make this a bound rather
# than the mechanism.
ARRIVAL_WINDOW_SECONDS = 0.3


def _summary(device: str, native: str, *, title: str = "a session", **raw) -> SessionSummary:
    row = {"running": False, "hostState": None, **raw}
    verdict, running, resident, evidence = classify(row)
    return SessionSummary(
        key=SessionKey.compose(device, "claude", native),
        title=title,
        classification=verdict,
        running=running,
        resident_host=resident,
        host_state=row.get("hostState"),
        lifecycle_mode=row.get("lifecycleMode"),
        project_id=row.get("projectId"),
        last_activity=row.get("lastActivity"),
        current_run=None,
        evidence=evidence,
    )


class ControlledProvider:
    """A device whose answers -- and whose *timing* -- the test owns.

    `park()` makes the next call block inside the provider until `release()`.
    That is what turns "these two calls overlapped" from a hope about the
    scheduler into a fact the test can rely on: the leader cannot finish while
    the gate is shut, so every other caller has no choice but to arrive during
    it.
    """

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str, *, sessions=None, sessions_error=None,
                 reachable: bool = True, authorized: bool = True,
                 health_detail: str = "controlled") -> None:
        self.device_ref = device_ref
        self.reachable = reachable
        self.authorized = authorized
        self.health_detail = health_detail
        self._sessions = sessions or []
        self._sessions_error = sessions_error
        self.health_calls = 0
        self.session_calls = 0
        self.session_filters: list[dict] = []
        self.sends: list[tuple] = []
        self.reads: list[tuple] = []
        self.run_calls = 0
        self._parked = False
        self._gate = threading.Event()
        self.entered = threading.Event()

    # -- test control ------------------------------------------------------

    def park(self) -> None:
        self._parked = True

    def release(self) -> None:
        self._gate.set()

    def _pass_through_gate(self) -> None:
        if not self._parked:
            return
        self.entered.set()
        self._gate.wait(timeout=10.0)

    # -- DeviceProvider ----------------------------------------------------

    def health(self) -> ProviderHealth:
        self.health_calls += 1
        self._pass_through_gate()
        return ProviderHealth(
            device_ref=self.device_ref,
            kind=self.kind,
            reachable=self.reachable,
            authorized=self.authorized,
            detail=self.health_detail,
            server_name="claudecodeui-mcp-gateway",
            server_version="0.1.0",
            tool_count=22,
            latency_ms=2.0,
        )

    def list_sessions(self, *, limit: int, project=None, state=None):
        self.session_calls += 1
        self.session_filters.append({"limit": limit, "project": project, "state": state})
        self._pass_through_gate()
        if self._sessions_error is not None:
            raise self._sessions_error
        return self._sessions

    def get_session(self, key):
        for summary in self._sessions:
            if summary.key == key:
                return {"session": summary.as_dict()}
        raise FleetRefusal("SESSION_NOT_FOUND", "no such session on this device")

    def read_session(self, key, *, mode="latest", limit=20, around_id=None, before=None,
                     after=None, cursor=None):
        self.reads.append((key.ref, limit))
        self._pass_through_gate()
        return {"messages": [{"id": "m1", "text": "a transcript"}]}

    def send(self, key, message, *, idempotency_key=None):
        self.sends.append((key.ref, message, idempotency_key))
        self._pass_through_gate()
        return {"runId": f"run-{len(self.sends)}", "queuedMessageUuid": None, "accepted": True}

    def get_run(self, key, run_id, *, wait=False, wait_seconds=0.0):
        self.run_calls += 1
        return {"state": "completed", "output": "done"}


def _binding(device_ref: str, *, anchor: Path, allow_send: bool = False) -> ProviderBinding:
    return ProviderBinding(
        device_ref=device_ref, kind="cloudcli-mcp-http",
        endpoint="http://127.0.0.1:3011/mcp",
        credential_anchor=anchor, allow_send=allow_send,
    )


class Harness:
    """A service over controlled devices, with a real registry and audit file."""

    def __init__(self, tmp: Path, *, sessions_cache_seconds: float = 0.0,
                 rate_limit: int = 10_000, send_allow: tuple[str, ...] = (),
                 allow_windows_send: bool = False) -> None:
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        self.audit_path = tmp / "audit.jsonl"
        config = FleetConfig(
            source=None,
            local_device_ref=WINDOWS,
            providers={
                WINDOWS: _binding(WINDOWS, anchor=anchor, allow_send=allow_windows_send),
                TOKYO: _binding(TOKYO, anchor=anchor),
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                send_allow=send_allow,
                sessions_cache_seconds=sessions_cache_seconds,
            ),
            audit=AuditBinding(path=self.audit_path, rate_limit_per_minute=rate_limit),
        )
        self.config = config
        self.windows = ControlledProvider(WINDOWS, sessions=[_summary(WINDOWS, "s-win")])
        self.tokyo = ControlledProvider(TOKYO, sessions=[_summary(TOKYO, "s-tok")])
        self.service = FleetService(
            config, providers={WINDOWS: self.windows, TOKYO: self.tokyo}
        )

    def principal(self, *scopes: str, name: str = "tester") -> Principal:
        return Principal(
            name=name,
            scopes=frozenset(scopes or ("fleet:read", "fleet:send")),
            credential_kind="pat",
        )

    @staticmethod
    def unprivileged(name: str = "tester") -> Principal:
        """A credential that carries nothing. Not the same as `principal()`."""
        return Principal(name=name, scopes=frozenset(), credential_kind="pat")

    def audit_records(self) -> list[dict]:
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]


def _run_concurrently(workers: list) -> list:
    """Run every worker behind a barrier, so they all enter at once.

    The barrier is what makes "concurrently" a property of the test rather than
    of how the host happened to schedule eight threads.
    """
    barrier = threading.Barrier(len(workers))
    results: list = [None] * len(workers)
    errors: list = [None] * len(workers)

    def wrapped(index: int, fn) -> None:
        try:
            barrier.wait(timeout=10.0)
            results[index] = fn()
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            errors[index] = exc

    threads = [
        threading.Thread(target=wrapped, args=(i, fn), daemon=True)
        for i, fn in enumerate(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15.0)
    if any(error is not None for error in errors):
        raise errors[[i for i, e in enumerate(errors) if e is not None][0]]
    return results


def _race(providers: list, start_first, start_rest) -> None:
    """Drive concurrent callers through a parked fan-out, one device at a time.

    `providers` is the devices in the order the code under test visits them.
    Each gate is opened only once the *next* device has a caller parked inside
    it: a device whose gate is already open holds nobody, so opening two at once
    would let the second answer one caller and then be asked again -- which
    looks exactly like a guard that does not coalesce, and is not one.

    Parking is what makes "these calls overlapped" a fact rather than a hope
    about the scheduler: a leader cannot finish while its gate is shut, so the
    other callers have no choice but to arrive during it. A guard that did not
    coalesce still finishes here -- with a larger call count, which is what the
    caller asserts.
    """
    import time

    start_first()
    assert providers[0].entered.wait(timeout=5.0), "the first call never reached a provider"
    start_rest()
    time.sleep(ARRIVAL_WINDOW_SECONDS)
    for current, following in zip(providers, providers[1:]):
        current.release()
        assert following.entered.wait(timeout=5.0), f"{following.device_ref} was never reached"
        time.sleep(ARRIVAL_WINDOW_SECONDS)
    providers[-1].release()


# ==========================================================================
# The guard itself
# ==========================================================================


class ReadGuardUnit(unittest.TestCase):
    """The two promises, and the key discipline that keeps them honest."""

    def test_concurrent_callers_share_one_measurement(self):
        guard = ReadGuard()
        calls = []
        release = threading.Event()
        entered = threading.Event()

        def measure():
            calls.append(1)
            entered.set()
            release.wait(timeout=5.0)
            return "measured-once"

        def caller():
            return guard.run(("k",), measure, label="k")[0]

        results = []
        workers = [lambda: results.append(caller()) for _ in range(6)]
        # The first caller parks inside `measure`; the rest must join it.
        thread = threading.Thread(target=workers[0], daemon=True)
        thread.start()
        self.assertTrue(entered.wait(timeout=5.0))
        threads = [threading.Thread(target=w, daemon=True) for w in workers[1:]]
        for t in threads:
            t.start()
        import time

        time.sleep(ARRIVAL_WINDOW_SECONDS)
        release.set()
        thread.join(timeout=10.0)
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(calls), 1)
        self.assertEqual(results, ["measured-once"] * 6)
        self.assertEqual(guard.stats()["measurements"], 1)
        self.assertEqual(guard.stats()["joined"], 5)

    def test_a_completed_measurement_is_not_reused_without_a_ttl(self):
        guard = ReadGuard()
        calls = []
        for _ in range(3):
            value, reuse = guard.run(("k",), lambda: calls.append(1) or "v", label="k")
            self.assertIsNone(reuse)
        self.assertEqual(len(calls), 3)
        self.assertEqual(guard.tracked(), 0)

    def test_a_configured_ttl_reuses_and_reports_the_reuse(self):
        guard = ReadGuard()
        calls = []
        first, reuse = guard.run(("k",), lambda: calls.append(1) or "v", ttl_seconds=30.0, label="sessions:d")
        second, reuse2 = guard.run(("k",), lambda: calls.append(1) or "v", ttl_seconds=30.0, label="sessions:d")
        self.assertEqual((first, second), ("v", "v"))
        self.assertIsNone(reuse)
        self.assertIsNotNone(reuse2)
        self.assertTrue(reuse2["reused"])
        self.assertEqual(reuse2["scope"], "sessions:d")
        self.assertIn("age_ms", reuse2)
        self.assertEqual(reuse2["ttl_ms"], 30_000.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(guard.stats()["reused"], 1)

    def test_an_expired_ttl_measures_again(self):
        import time

        guard = ReadGuard()
        calls = []
        guard.run(("k",), lambda: calls.append(1) or "v", ttl_seconds=0.05, label="k")
        time.sleep(0.08)
        _value, reuse = guard.run(("k",), lambda: calls.append(1) or "v", ttl_seconds=0.05, label="k")
        self.assertIsNone(reuse)
        self.assertEqual(len(calls), 2)

    def test_a_failure_is_delivered_to_every_waiter_and_then_forgotten(self):
        guard = ReadGuard()
        attempts = []
        release = threading.Event()
        entered = threading.Event()

        def failing():
            attempts.append(1)
            if len(attempts) == 1:
                entered.set()
                release.wait(timeout=5.0)
            raise refuse(PROVIDER_UNREACHABLE, "the tunnel is down")

        def caller():
            return guard.run(("k",), failing, label="k")

        caught: list = []

        def catching_caller():
            try:
                caller()
            except FleetRefusal as exc:
                caught.append(exc.code)

        first = threading.Thread(target=catching_caller, daemon=True)
        first.start()
        self.assertTrue(entered.wait(timeout=5.0))
        others = [threading.Thread(target=catching_caller, daemon=True) for _ in range(3)]
        for t in others:
            t.start()
        import time

        time.sleep(ARRIVAL_WINDOW_SECONDS)
        release.set()
        first.join(timeout=10.0)
        for t in others:
            t.join(timeout=10.0)

        # Every caller is told the same thing, and none of them is told "fine".
        self.assertEqual(caught, [PROVIDER_UNREACHABLE] * 4)
        # Nothing was remembered: the failed entry is gone.
        self.assertEqual(guard.tracked(), 0)
        self.assertEqual(guard.stats()["failures"], 1)
        # And the next call asks again rather than replaying the refusal.
        with self.assertRaises(FleetRefusal):
            guard.run(("k",), failing, label="k")
        self.assertEqual(len(attempts), 2)

    def test_a_waiter_gets_its_own_refusal_object_not_the_leader_s(self):
        """A shared exception object would carry one traceback for two threads."""
        guard = ReadGuard()
        original = refuse(PROVIDER_UNREACHABLE, "the tunnel is down", device_ref=WINDOWS)
        release = threading.Event()
        entered = threading.Event()
        seen: dict = {}

        def failing():
            if "leader" not in seen:
                seen["leader"] = True
                entered.set()
                release.wait(timeout=5.0)
            raise original

        def catching(label: str) -> None:
            try:
                guard.run(("k",), failing, label="k")
            except FleetRefusal as exc:
                seen[label] = exc

        leader = threading.Thread(target=catching, args=("leader_refusal",), daemon=True)
        leader.start()
        self.assertTrue(entered.wait(timeout=5.0))
        import time

        waiter = threading.Thread(target=catching, args=("waiter_refusal",), daemon=True)
        waiter.start()
        time.sleep(ARRIVAL_WINDOW_SECONDS)
        release.set()
        leader.join(timeout=10.0)
        waiter.join(timeout=10.0)

        # The leader re-raises what it caught; the waiter is handed a copy that
        # carries the same code, detail and extras.
        self.assertIs(seen["leader_refusal"], original)
        self.assertIsNot(seen["waiter_refusal"], original)
        for exc in (seen["leader_refusal"], seen["waiter_refusal"]):
            self.assertEqual(exc.code, PROVIDER_UNREACHABLE)
            self.assertEqual(exc.detail, "the tunnel is down")
            self.assertEqual(exc.extra.get("device_ref"), WINDOWS)

    def test_a_waiter_that_outlives_the_bound_measures_for_itself(self):
        import time

        guard = ReadGuard(max_wait_seconds=0.05)
        calls = []
        release = threading.Event()
        entered = threading.Event()

        def slow():
            calls.append(1)
            if len(calls) == 1:
                entered.set()
                release.wait(timeout=5.0)
            return "ok"

        def caller():
            return guard.run(("k",), slow, label="k")[0]

        leader = threading.Thread(target=caller, daemon=True)
        leader.start()
        self.assertTrue(entered.wait(timeout=5.0))
        # This one will not wait for the leader; it takes its own measurement.
        self.assertEqual(_run_concurrently([caller])[0], "ok")
        time.sleep(0.0)
        release.set()
        leader.join(timeout=10.0)
        self.assertEqual(len(calls), 2)

    def test_distinct_keys_do_not_share(self):
        guard = ReadGuard()
        calls = []

        def measure(tag):
            return lambda: calls.append(tag) or tag

        guard.run(("a",), measure("a"), label="a")
        guard.run(("b",), measure("b"), label="b")
        self.assertEqual(calls, ["a", "b"])

    def test_canonical_value_separates_what_means_different_things(self):
        """Erring toward "not the same" is the safe direction.

        A key that merged two filters would answer a question nobody asked; a
        key that splits two spellings of one filter costs one extra measurement
        and tells nobody anything false. So the coercion is shallow: `None` is
        not `""`, `running` is not an absent filter, and `20` is not `"20"`.
        The call site casts `limit` to `int` before building the key, which is
        why the last pair never actually arises.
        """
        self.assertEqual(canonical_value(True), ("bool", "1"))
        self.assertNotEqual(canonical_value(None), canonical_value(""))
        self.assertNotEqual(canonical_value("running"), canonical_value(None))
        self.assertNotEqual(canonical_value(20), canonical_value(40))
        self.assertNotEqual(canonical_value(20), canonical_value("20"))


# ==========================================================================
# The composition
# ==========================================================================


class ServiceReadProtection(unittest.TestCase):
    """What `FleetService` collapses, and what it must never collapse."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def harness(self, **kwargs) -> Harness:
        return Harness(self.tmp, **kwargs)

    # -- the saving --------------------------------------------------------

    def test_concurrent_list_devices_probes_each_device_once(self):
        h = self.harness()
        for provider in (h.windows, h.tokyo):
            provider.park()
        principal = h.principal()

        results: list = []
        errors: list = []

        def worker():
            try:
                results.append(h.service.list_devices(principal))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def start(count: int) -> list:
            threads = [threading.Thread(target=worker, daemon=True) for _ in range(count)]
            for thread in threads:
                thread.start()
            return threads

        # `list_devices` walks the registry, so it reaches `work-fj` before it
        # reaches Tokyo: that is the order the gates are handed over in.
        threads = start(1)
        _race([h.windows, h.tokyo], lambda: None, lambda: threads.extend(start(5)))
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 6)
        # Six callers, one probe per device between them.
        self.assertEqual(h.windows.health_calls, 1)
        self.assertEqual(h.tokyo.health_calls, 1)
        self.assertEqual(h.service.reads.stats()["measurements"], 2)
        self.assertEqual(h.service.reads.stats()["joined"], 10)
        # Every caller still got the whole answer.
        for payload in results:
            self.assertEqual(payload["total"], len(h.service.registry.all()))

    def test_status_and_list_devices_share_one_probe_for_one_caller(self):
        """Both verbs key a probe the same way, so following one with the other
        does not probe a device twice.

        Only `work-fj` is parked. `status` walks providers in sorted order and
        therefore reaches Tokyo first, while `list_devices` walks the registry
        and reaches `work-fj` first; parking both would have the two verbs
        probe two different devices rather than sharing one.
        """
        h = self.harness()
        h.windows.park()
        principal = h.principal()
        windows_result: list = []
        status_result: list = []
        threads: list = []

        def spawn(target) -> None:
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            threads.append(thread)

        _race(
            [h.windows],
            lambda: spawn(lambda: windows_result.append(h.service.list_devices(principal))),
            lambda: spawn(lambda: status_result.append(h.service.status(principal))),
        )
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(len(windows_result), 1)
        self.assertEqual(len(status_result), 1)
        # One probe of `work-fj` between the two verbs and the two callers.
        self.assertEqual(h.windows.health_calls, 1)

    def test_two_principals_do_not_share_a_probe(self):
        h = self.harness()
        for provider in (h.windows, h.tokyo):
            provider.park()
        one = h.principal(name="alice")
        two = h.principal(name="bob")
        results: list = []
        threads: list = []

        def spawn(principal) -> None:
            thread = threading.Thread(
                target=lambda: results.append(h.service.list_devices(principal)),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        _race([h.windows, h.tokyo], lambda: spawn(one), lambda: spawn(two))
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(len(results), 2)
        # One device, two callers, two measurements: identity is part of the key.
        self.assertEqual(h.windows.health_calls, 2)
        self.assertEqual(h.tokyo.health_calls, 2)

    def test_concurrent_identical_session_listings_call_each_device_once(self):
        h = self.harness()
        for provider in (h.windows, h.tokyo):
            provider.park()
        principal = h.principal()

        def worker():
            h.service.list_sessions(principal, limit=20)

        threads: list = []

        def start(count: int) -> None:
            for _ in range(count):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                threads.append(thread)

        _race([h.windows, h.tokyo], lambda: start(1), lambda: start(3))
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(h.windows.session_calls, 1)
        self.assertEqual(h.tokyo.session_calls, 1)

    def test_different_filters_are_different_reads(self):
        h = self.harness()
        principal = h.principal()
        h.service.list_sessions(principal, device=WINDOWS, limit=20)
        h.service.list_sessions(principal, device=WINDOWS, limit=40)
        h.service.list_sessions(principal, device=WINDOWS, limit=20, project="p1")
        h.service.list_sessions(principal, device=WINDOWS, limit=20, state="running")
        self.assertEqual(h.windows.session_calls, 4)
        self.assertEqual(
            [f["limit"] for f in h.windows.session_filters], [20, 40, 20, 20]
        )

    # -- never stored, never hidden ----------------------------------------

    def test_a_failed_device_is_reported_and_the_next_call_re_measures(self):
        h = self.harness()
        h.windows._sessions_error = refuse(PROVIDER_UNREACHABLE, "connector is down")
        principal = h.principal()

        first = h.service.list_sessions(principal, device=WINDOWS)
        self.assertEqual(first["total"], 0)
        self.assertEqual(
            [u["code"] for u in first["devices_unreachable"]], [PROVIDER_UNREACHABLE]
        )
        self.assertEqual(h.windows.session_calls, 1)

        # The failure was not remembered: the device is asked again, and this
        # time it answers.
        h.windows._sessions_error = None
        second = h.service.list_sessions(principal, device=WINDOWS)
        self.assertEqual(h.windows.session_calls, 2)
        self.assertEqual(second["devices_unreachable"], [])
        self.assertEqual(second["total"], 1)

    def test_a_failed_device_does_not_change_the_answer_about_another(self):
        h = self.harness()
        h.windows._sessions_error = refuse(PROVIDER_TIMEOUT, "no answer in 45s")
        payload = h.service.list_sessions(h.principal())
        # `work-fj` failed at the provider. The other unbound devices are
        # reported under UNKNOWN_DEVICE by the registry, which is a different
        # answer to a different question; only the provider failure is ours.
        provider_failures = [
            u for u in payload["devices_unreachable"] if u["code"] == PROVIDER_TIMEOUT
        ]
        self.assertEqual([u["device_ref"] for u in provider_failures], [WINDOWS])
        refs = [row["session_ref"] for row in payload["sessions"]]
        self.assertIn(f"{TOKYO}|claude|s-tok", refs)
        self.assertNotIn(f"{WINDOWS}|claude|s-win", refs)

    def test_concurrent_callers_all_see_the_failure_and_are_never_told_it_worked(self):
        h = self.harness()
        h.windows.park()
        h.windows._sessions_error = refuse(PROVIDER_UNREACHABLE, "tunnel dropped")
        principal = h.principal()
        results: list = []

        def worker():
            results.append(h.service.list_sessions(principal, device=WINDOWS))

        threads: list = []

        def start(count: int) -> None:
            for _ in range(count):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                threads.append(thread)

        _race([h.windows], lambda: start(1), lambda: start(3))
        for thread in threads:
            thread.join(timeout=15.0)

        self.assertEqual(len(results), 4)
        # One call between them, and every one of the four is told the same
        # refusal. A coalesced *failure* must not become a coalesced success.
        self.assertEqual(h.windows.session_calls, 1)
        for payload in results:
            self.assertEqual(
                [u["code"] for u in payload["devices_unreachable"]],
                [PROVIDER_UNREACHABLE],
            )
            self.assertEqual(payload["total"], 0)

    def test_an_unreachable_health_answer_is_reported_per_device(self):
        h = self.harness()
        h.windows.reachable = False
        h.windows.health_detail = "connector did not answer"
        payload = h.service.list_devices(h.principal())
        by_ref = {row["device_ref"]: row for row in payload["devices"]}
        self.assertFalse(by_ref[WINDOWS]["provider"]["reachable"])
        self.assertTrue(by_ref[TOKYO]["provider"]["reachable"])
        self.assertFalse(by_ref[WINDOWS]["probe"]["reachable"])

    # -- authorisation is not cacheable and not bypassable -----------------

    def test_a_denied_scope_never_reaches_a_provider_and_leaves_no_entry(self):
        h = self.harness()
        principal = h.unprivileged(name="nobody")
        for _ in range(3):
            with self.assertRaises(FleetRefusal) as caught:
                h.service.list_devices(principal)
            self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.windows.health_calls, 0)
        self.assertEqual(h.tokyo.health_calls, 0)
        # A refusal must not be storable, so it must not even be represented.
        # The guard is reached only after admission, so a denied caller never
        # creates an entry and cannot be answered from one either.
        self.assertEqual(h.service.reads.tracked(), 0)
        self.assertEqual(h.service.reads.stats()["measurements"], 0)

    def test_a_rate_limited_caller_never_reaches_a_provider(self):
        h = self.harness(rate_limit=1)
        principal = h.principal()
        h.service.list_devices(principal)
        before = h.windows.health_calls
        with self.assertRaises(FleetRefusal) as caught:
            h.service.list_devices(principal)
        self.assertEqual(caught.exception.code, RATE_LIMITED)
        self.assertEqual(h.windows.health_calls, before)

    def test_authorisation_is_re_evaluated_on_every_call(self):
        h = self.harness()
        denied = h.unprivileged(name="later")
        for _ in range(2):
            with self.assertRaises(FleetRefusal) as caught:
                h.service.list_sessions(denied, device=WINDOWS)
            self.assertEqual(caught.exception.code, SCOPE_DENIED)
        self.assertEqual(h.windows.session_calls, 0)
        # The same name presenting a credential that carries the scope is not
        # the same caller: the guard key is the principal, and the scope check
        # runs on every call rather than being remembered from the refusal.
        allowed = Principal(
            name="later", scopes=frozenset({"fleet:read"}), credential_kind="pat"
        )
        payload = h.service.list_sessions(allowed, device=WINDOWS)
        self.assertEqual(h.windows.session_calls, 1)
        self.assertEqual(payload["total"], 1)

    # -- writes and transcripts are untouched ------------------------------

    def test_send_and_get_run_are_never_coalesced(self):
        h = self.harness(send_allow=(f"{WINDOWS}|claude|s-win",), allow_windows_send=True)
        principal = h.principal()
        key = SessionKey.compose(WINDOWS, "claude", "s-win")

        results = _run_concurrently([
            lambda: h.service.send_to_session(principal, key, message="one"),
            lambda: h.service.send_to_session(principal, key, message="two"),
        ])
        self.assertEqual(len(results), 2)
        # Both messages reached the device: the order is the scheduler's, the
        # count is the assertion.
        self.assertEqual(
            sorted(h.windows.sends), [(key.ref, "one", None), (key.ref, "two", None)]
        )
        self.assertEqual(len({r["run_id"] for r in results}), 2)

    def test_idempotency_still_replays_rather_than_resending(self):
        h = self.harness(send_allow=(f"{WINDOWS}|claude|s-win",), allow_windows_send=True)
        principal = h.principal()
        key = SessionKey.compose(WINDOWS, "claude", "s-win")
        first = h.service.send_to_session(principal, key, message="once", idempotency_key="k1")
        second = h.service.send_to_session(principal, key, message="once", idempotency_key="k1")
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(h.windows.sends), 1)

    def test_transcripts_are_never_reused_even_with_a_ttl_configured(self):
        h = self.harness(sessions_cache_seconds=60.0)
        principal = h.principal()
        key = SessionKey.compose(WINDOWS, "claude", "s-win")
        h.service.read_session(principal, key)
        h.service.read_session(principal, key)
        self.assertEqual(len(h.windows.reads), 2)

    def test_get_run_is_never_coalesced(self):
        h = self.harness()
        principal = h.principal()
        key = SessionKey.compose(WINDOWS, "claude", "s-win")
        _run_concurrently([
            lambda: h.service.get_run(principal, key, "run-1"),
            lambda: h.service.get_run(principal, key, "run-1"),
        ])
        self.assertEqual(h.windows.run_calls, 2)

    # -- the opt-in TTL discloses itself -----------------------------------

    def test_the_default_configuration_never_reuses_and_never_discloses(self):
        h = self.harness()
        principal = h.principal()
        first = h.service.list_sessions(principal, device=WINDOWS)
        second = h.service.list_sessions(principal, device=WINDOWS)
        self.assertEqual(h.windows.session_calls, 2)
        self.assertNotIn("devices_reused_within_ttl", first)
        self.assertNotIn("devices_reused_within_ttl", second)

    def test_a_configured_ttl_reuses_and_says_so_in_the_payload_and_the_audit(self):
        h = self.harness(sessions_cache_seconds=60.0)
        principal = h.principal()
        first = h.service.list_sessions(principal, device=WINDOWS)
        second = h.service.list_sessions(principal, device=WINDOWS)

        self.assertEqual(h.windows.session_calls, 1)
        self.assertNotIn("devices_reused_within_ttl", first)
        self.assertIn("devices_reused_within_ttl", second)
        reused = second["devices_reused_within_ttl"]
        self.assertEqual([r["device_ref"] for r in reused], [WINDOWS])
        self.assertTrue(reused[0]["reused"])
        self.assertIn("age_ms", reused[0])
        self.assertIn("ttl_ms", reused[0])
        self.assertIn("measured earlier", second["freshness_note"])

        records = [r for r in h.audit_records() if r["event"] == "fleet.list_sessions"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["reused"], [])
        self.assertEqual(records[1]["reused"], [WINDOWS])

    # -- the audit records every call, coalesced or not --------------------

    def test_every_coalesced_call_is_still_audited(self):
        h = self.harness()
        for provider in (h.windows, h.tokyo):
            provider.park()
        principal = h.principal()

        def worker():
            h.service.list_devices(principal)

        threads: list = []

        def start(count: int) -> None:
            for _ in range(count):
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                threads.append(thread)

        _race([h.windows, h.tokyo], lambda: start(1), lambda: start(4))
        for thread in threads:
            thread.join(timeout=15.0)

        # The probe was shared; the record of who asked was not.
        self.assertEqual(h.windows.health_calls, 1)
        records = [r for r in h.audit_records() if r["event"] == "fleet.list_devices"]
        self.assertEqual(len(records), 5)
        for record in records:
            self.assertEqual(record["principal"], "tester")


# ==========================================================================
# The audit tail
# ==========================================================================


class AuditTailBounded(unittest.TestCase):
    """`fleet_status` reads the end of the audit file, and the same records."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.path = self.tmp / "audit.jsonl"
        self.log = AuditLog(AuditBinding(path=self.path))

    def _write(self, count: int, *, filler: int = 0) -> None:
        with self.path.open("w", encoding="utf-8") as handle:
            for index in range(count):
                record = {"ts": "2026-10-12T00:00:00Z", "event": f"e{index}"}
                if filler:
                    record["filler"] = "x" * filler
                handle.write(json.dumps(record) + "\n")

    def test_the_same_records_as_a_full_read(self):
        self._write(500)
        expected = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()[-20:]
        ]
        self.assertEqual(self.log.tail(20), expected)

    def test_the_window_widens_rather_than_returning_a_short_answer(self):
        # Fewer records than the request, but far more bytes than one window.
        self._write(30, filler=4096)
        self.assertGreater(self.path.stat().st_size, 64 * 1024)
        got = self.log.tail(20)
        self.assertEqual(len(got), 20)
        self.assertEqual(got[-1]["event"], "e29")
        self.assertEqual(got[0]["event"], "e10")

    def test_a_single_line_longer_than_the_window_still_answers(self):
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "a", "filler": "y" * (128 * 1024)}) + "\n")
            handle.write(json.dumps({"event": "b"}) + "\n")
        got = self.log.tail(2)
        self.assertEqual([r["event"] for r in got], ["a", "b"])

    def test_the_head_of_the_file_is_never_decoded(self):
        """This is what pins the read as bounded.

        The equivalence tests above return the right records whether the file
        was read from the end or in full, so none of them can tell the two
        apart. Here the head of the file is bytes that are not valid UTF-8: a
        `tail` that decoded the whole file would raise on them, and one that
        reads only the end never sees them. The records are the same either
        way -- it is the cost that differs, which is the point of the change.
        """
        with self.path.open("wb") as handle:
            handle.write(b"\xff" * (200 * 1024))
            handle.write(b"\n")
            for index in range(10):
                handle.write(json.dumps({"event": f"e{index}"}).encode() + b"\n")
        got = self.log.tail(5)
        self.assertEqual([r["event"] for r in got], ["e5", "e6", "e7", "e8", "e9"])

    def test_a_missing_file_is_an_empty_tail_not_an_error(self):
        self.assertEqual(AuditLog(AuditBinding(path=self.tmp / "absent.jsonl")).tail(5), [])

    def test_no_binding_is_an_empty_tail(self):
        self.assertEqual(AuditLog(None).tail(5), [])

    def test_a_partial_line_is_skipped_not_returned_broken(self):
        self._write(10)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"event": "truncated"')
        got = self.log.tail(5)
        self.assertEqual([r["event"] for r in got], ["e6", "e7", "e8", "e9"])


if __name__ == "__main__":
    unittest.main()
