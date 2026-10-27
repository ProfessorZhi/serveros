#!/usr/bin/env python3
"""#423 x #428 — the short transport key at the observer boundary.

The #428 observer's ledger row carries #423's FULL delivery identity; the
Provider receives the <=50-character transport digest #423 derives from it.
This file pins that boundary without re-testing the observer's scheduling logic
(that belongs to `tests/test_fleet_v1_milestone_observer.py`):

1. the ledger keeps the full identity, and the provider gets `ms1:` + 46 hex;
2. two different Runs on one device do not share a transport key (the old
   `full[:50]` truncation did);
3. a restart of the same event keeps the same key;
4. a legacy DELIVERED row is never replayed;
5. an UNKNOWN delivery re-asks the same short key in reconcile;
6. the observer module and these checks use the same #423 module object.

No network, CLI, credential, service or real Feishu send.
"""
from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OBSERVER_MODULE = (REPO / "integrations" / "fleet-v1-milestone-observer"
                   / "observer.py")
FIXTURE = (REPO / "integrations" / "fleet-v1-milestone-observer"
           / "fixtures" / "cloudcli" / "completed.json")
CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"
START = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc).timestamp()


def _load(name: str, path: Path):
    """Cache-first, and REGISTERED BEFORE execution.

    The observer module's dataclass annotations resolve their defining module
    through `sys.modules` while the module body runs. Registering after
    `exec_module` leaves that lookup `None` and aborts collection when this
    file runs alone (as it does in its own CI shard).
    """
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


sys.dont_write_bytecode = True
observer = _load("serveros_fleet_v1_milestone_observer", OBSERVER_MODULE)
m = observer.NOTIFY


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class Clock:
    def __init__(self, at: float = START):
        self.at = float(at)

    def __call__(self) -> float:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += float(seconds)


class FakeLark:
    """The injected provider. It records; it never touches a service."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None):
        self.receipts = list(receipts or [])
        self.calls = []

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": "SUPPORTED"}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self.receipts:
            receipt = self.receipts.pop(0)
            if isinstance(receipt, BaseException):
                raise receipt
            return receipt
        return {"delivered": True, "remote_id": "om_default",
                "provider": self.provider_id, "transport": self.transport}


class TransportInteropTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-interop-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "milestones.sqlite"
        self.clock = Clock()

    def notifier(self, provider):
        return m.MilestoneNotifier(provider, self.ledger,
                                   conversation_ref=RECIPIENT, clock=self.clock)

    def evidence(self, run_id="run_demo_1"):
        payload = copy.deepcopy(fixture())
        payload["run"] = {**payload["run"], "runId": run_id}
        return m.normalize("cloudcli", payload)

    def row(self, key):
        db = sqlite3.connect(self.ledger)
        try:
            db.row_factory = sqlite3.Row
            found = db.execute(
                "SELECT * FROM milestone_deliveries WHERE delivery_key=?",
                (key,)).fetchone()
            return dict(found)
        finally:
            db.close()

    def test_full_ledger_identity_and_short_provider_key(self):
        evidence = self.evidence()
        decision = m.decide(evidence)
        full = m.delivery_key_for(decision)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.DELIVERED)
        self.assertEqual(observation.delivery_key, full)
        self.assertTrue(full.startswith("milestone:cloudcli:"))
        self.assertGreater(len(full), 50)

        short = m.transport_key_for(full)
        self.assertEqual(observation.transport_key, short)
        self.assertRegex(short, r"^ms1:[0-9a-f]{46}$")
        self.assertEqual(len(short), 50)
        self.assertNotEqual(short, full)
        self.assertEqual(provider.calls[0]["idempotency_key"], short)

        row = self.row(full)
        self.assertEqual(row["delivery_key"], full)
        self.assertEqual(row["transport_key"], short)
        self.assertEqual(row["state"], m.DELIVERED)

    def test_two_same_device_runs_do_not_share_a_transport_key(self):
        first = self.evidence("run_demo_1")
        second = self.evidence("run_demo_2")
        full_first = m.delivery_key_for(m.decide(first))
        full_second = m.delivery_key_for(m.decide(second))
        self.assertNotEqual(full_first, full_second)
        # The old transport key was `full[:50]`; these two collided there.
        self.assertEqual(full_first[:50], full_second[:50])
        short_first = m.transport_key_for(full_first)
        short_second = m.transport_key_for(full_second)
        self.assertNotEqual(short_first, short_second)
        self.assertEqual(len(short_first), 50)
        self.assertEqual(len(short_second), 50)

        provider = FakeLark()
        notifier = self.notifier(provider)
        one = notifier.observe(first)
        two = notifier.observe(second)
        self.assertEqual((one.state, two.state), (m.DELIVERED, m.DELIVERED))
        self.assertEqual([call["idempotency_key"] for call in provider.calls],
                         [short_first, short_second])
        self.assertEqual(self.row(full_first)["transport_key"], short_first)
        self.assertEqual(self.row(full_second)["transport_key"], short_second)

    def test_a_restart_keeps_the_same_transport_key(self):
        evidence = self.evidence()
        full = m.delivery_key_for(m.decide(evidence))
        short = m.transport_key_for(full)
        first = self.notifier(FakeLark()).observe(evidence)
        self.assertEqual(first.transport_key, short)

        restarted_provider = FakeLark()
        restarted = self.notifier(restarted_provider).observe(evidence)
        self.assertEqual(restarted.state, m.DUPLICATE)
        self.assertEqual(restarted.delivery_key, full)
        self.assertEqual(restarted.transport_key, short)
        self.assertEqual(restarted_provider.calls, [])

    def test_a_legacy_delivered_row_is_not_replayed(self):
        evidence = self.evidence()
        decision = m.decide(evidence)
        full = m.delivery_key_for(decision)
        legacy = full[:50]
        self.notifier(FakeLark())  # create/migrate the table
        db = sqlite3.connect(self.ledger)
        try:
            db.execute(
                "INSERT INTO milestone_deliveries "
                "(delivery_key, milestone_key, outcome, state, remote_id, "
                " attempted_at, why, intent, transport_key) "
                "VALUES (?,?,?,?,?,?,?,?,NULL)",
                (full, decision.key, decision.outcome, m.DELIVERED, "om_old",
                 datetime.now(timezone.utc).isoformat(), "legacy delivered",
                 json.dumps({"text": m.message(decision)})))
            db.commit()
        finally:
            db.close()

        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.DUPLICATE)
        self.assertEqual(observation.delivery_key, full)
        self.assertEqual(observation.transport_key, legacy)
        self.assertEqual(self.row(full)["state"], m.DELIVERED)
        self.assertEqual(provider.calls, [])

    def test_an_unknown_delivery_reconciles_with_the_same_short_key(self):
        evidence = self.evidence()
        full = m.delivery_key_for(m.decide(evidence))
        short = m.transport_key_for(full)
        first_provider = FakeLark(receipts=[{"delivered": True,
                                             "remote_id": None}])
        first = self.notifier(first_provider).observe(evidence)
        self.assertEqual(first.state, m.UNKNOWN_DELIVERY)
        self.assertEqual(first.transport_key, short)
        self.assertEqual(first_provider.calls[0]["idempotency_key"], short)

        recovery_provider = FakeLark()
        recovered = self.notifier(recovery_provider).reconcile(full)
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertEqual(recovered.transport_key, short)
        self.assertEqual(recovery_provider.calls[0]["idempotency_key"], short)
        self.assertEqual(self.row(full)["delivery_key"], full)
        self.assertEqual(self.row(full)["transport_key"], short)

    def test_the_observer_uses_one_registered_product_module(self):
        module = observer.NOTIFY
        self.assertIs(sys.modules[module.__name__], module,
                      "the observer's notifier is the single registered module")
        source = inspect.getsourcefile(module)
        self.assertTrue(source.endswith(
            "integrations/fleet-v1-milestone-notify/milestone_notify.py"))


if __name__ == "__main__":
    unittest.main()
