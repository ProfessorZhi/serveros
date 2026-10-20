#!/usr/bin/env python3
"""V13 monitor contract · 无副作用合成负测（M1-monitoring-v13）。

WHAT THIS FILE IS
-----------------
A closed set of synthetic documents is pushed through the V13 monitoring seams:

    Core readback  ->  device entry  (fleet_core_bridge)
    report intent  ->  receipt gate  (card_surface)
    sample store   ->  ownership     (snapshot_journal vs TrendArchive)

Nothing here opens a socket, runs `lark-cli`, reads production state, starts a
service, or calls `card_source.gather()`. Every `SurfaceStore` write goes to a
`tempfile.TemporaryDirectory()`.

TWO KINDS OF TEST, AND WHY BOTH ARE LABELLED
--------------------------------------------
  * the plain tests assert invariants the CURRENT code already keeps;
  * the `expectedFailure` tests name a gap confirmed by the audit
    (`integrations/fleet-v13-monitor/MONITOR-SCHEMA-GAP-MATRIX.md`, `V13-G*`).
    They are NOT evidence that the gap is closed. When the gap is fixed the
    test reports "unexpected success" and must be turned into a real assertion.

WHY THE FILENAME IS `check_*.py`
--------------------------------
`tests/test_collection_scope.py` forbids a `test_*.py` / `*_test.py` module
outside `tests/`. This lane owns `integrations/fleet-v13-monitor/**` and must
not make that guard red, so the file is run by explicit path.

    python3 integrations/fleet-v13-monitor/tests/check_monitor_contract.py
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME = REPO / "runtime"

NOW = "2026-10-16T06:00:00Z"
TOKYO = "device:tokyo-shared-dev-01"
OTHER = "device:llm-lab-dell"
T0 = "2026-10-16T05:30:00Z"
T1 = "2026-10-16T06:00:00Z"
T2 = "2026-10-16T06:05:00Z"
PAST_WINDOW = "2026-10-16T08:30:00Z"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load("serveros_fleet_core_bridge", RUNTIME / "fleet_core_bridge.py")
monitor = _load("serveros_fleet_monitor_card", RUNTIME / "fleet_monitor_card.py")
source = _load("serveros_fleet_monitor_source", RUNTIME / "fleet_monitor_source.py")
journal = _load("serveros_runtime_snapshot_journal", RUNTIME / "snapshot_journal.py")
surface = _load("serveros_runtime_card_surface", RUNTIME / "card_surface.py")


def stamp(minutes: int = 0) -> str:
    base = datetime(2026, 10, 16, 6, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def readback(**over) -> dict:
    """A well-formed admitted readback for TOKYO. Synthetic, never fetched."""
    doc = {
        "schema": bridge.CORE_READBACK_SCHEMA,
        "device_ref": TOKYO,
        "source": {"schema": "serveros/core-lite-status.v1",
                   "verified_route": True, "received_at": NOW},
        "device": {"declared": TOKYO, "state": "MATCH"},
        "principal": {"declared": "vince", "expected": "vince",
                      "state": "MATCH", "basis": "binding"},
        "connected": "CONNECTED",
        "live": {"claimed": True, "accepted": True, "why": None},
        "freshness": {"state": "FRESH", "age_seconds": 1.0, "bound_seconds": 60.0},
        "observations": {
            "cpu": {"state": "PRESENT", "value": {"used_percent": 32.5},
                    "completeness": "COMPLETE"},
            "memory": {"state": "PRESENT",
                       "value": {"total_bytes": 134217728000,
                                 "available_bytes": 67108864000,
                                 "used_percent": 50.0},
                       "completeness": "COMPLETE"},
            "swap": {"state": "PRESENT",
                     "value": {"total_bytes": 34359738368,
                               "used_bytes": 17179869184,
                               "pages_in_per_second": 12.5,
                               "pages_out_per_second": 3.0},
                     "completeness": "COMPLETE"},
            "psi.cpu": {"state": "PRESENT",
                        "value": {"some": {"avg10": 0.9}, "full": {"avg10": 0.0}},
                        "completeness": "COMPLETE"},
            "psi.io": {"state": "PRESENT",
                       "value": {"some": {"avg10": 0.1}, "full": {"avg10": 0.1}},
                       "completeness": "COMPLETE"},
            "storage.readonly": {"state": "PRESENT", "value": {
                "filesystems": {"roles": [
                    {"role": "DATA_FS", "total_bytes": 4000000000000,
                     "available_bytes": 2950000000000}]},
                "disk_io": {"roles": []}}, "completeness": "COMPLETE"},
            "services": {"state": "PRESENT", "value": {"services": [
                {"id": "serverosd", "unit": "serverosd.service",
                 "state": "ACTIVE"},
                {"id": "gateway0", "unit": "gateway0.service",
                 "state": "FAILED"}]}, "completeness": "COMPLETE"},
            "gpu.readonly": {"state": "PRESENT", "value": {
                "gpus": [{"index": 0, "label": "A6000",
                          "gpu_util_percent": 71.0,
                          "memory_used_bytes": 40802189312,
                          "memory_total_bytes": 51539607552,
                          "temperature_c": 68.0}],
                "sample_time": NOW}, "completeness": "COMPLETE"},
        },
        "observations_unavailable": [],
        "integrity": "OK",
        "refusals": [],
        "route": {"state": "VERIFIED", "device_ref": TOKYO,
                  "authenticated": True, "reason": None, "failure": None},
    }
    doc.update(over)
    return doc


def by_label(entry: dict) -> dict:
    return {reading["label"]: reading for reading in entry["readings"]}


class CoreReadbackContractTest(unittest.TestCase):
    """The Core summary may only become numbers when attribution is established."""

    def entry(self, doc, **kw):
        return bridge.device_from_core_readback(
            doc, device_ref=kw.pop("ref", TOKYO), now=kw.pop("now", NOW), **kw)

    def assert_unknown_without_numbers(self, entry):
        self.assertEqual(entry["reachability"]["state"], monitor.STATE_UNKNOWN)
        for reading in entry["readings"]:
            self.assertEqual(reading["state"], monitor.STATE_UNKNOWN)
            self.assertIsNone(reading["value"])

    def test_unverified_route_is_unknown_and_carries_no_numbers(self):
        entry = self.entry(readback(route={"state": "UNAUTHENTICATED",
                                           "reason": "no authenticated route"}))
        self.assert_unknown_without_numbers(entry)
        self.assertIn("UNAUTHENTICATED", entry["reachability"]["reason"])

    def test_live_not_accepted_is_unknown(self):
        entry = self.entry(readback(live={"claimed": True, "accepted": False,
                                          "why": "readback did not earn it"}))
        self.assert_unknown_without_numbers(entry)

    def test_principal_unknown_is_unknown(self):
        entry = self.entry(readback(principal={"state": "UNKNOWN"}))
        self.assert_unknown_without_numbers(entry)

    def test_future_receipt_is_refused(self):
        entry = self.entry(readback(source={"received_at": stamp(5)}))
        self.assert_unknown_without_numbers(entry)
        self.assertIn("未来", entry["reachability"]["reason"])

    def test_aged_reading_is_stale_and_never_known(self):
        entry = self.entry(readback(source={"received_at": stamp(-60)}))
        self.assertEqual(entry["reachability"]["state"], monitor.STATE_STALE)
        states = {reading["state"] for reading in entry["readings"]}
        self.assertNotIn(monitor.STATE_KNOWN, states)
        self.assertIn(monitor.STATE_STALE, states)
        for reading in entry["readings"]:
            if reading["state"] == monitor.STATE_STALE:
                self.assertIn("新鲜度", reading["reason"])

    def test_wrong_device_is_refused(self):
        with self.assertRaises(bridge.CoreRefused):
            bridge.core_verdict(readback(), device_ref=OTHER, now=NOW,
                                stale_after_s=1800)

    def test_wrong_schema_is_refused(self):
        with self.assertRaises(bridge.CoreRefused):
            bridge.core_verdict(readback(schema="serveros/other.v1"),
                                device_ref=TOKYO, now=NOW, stale_after_s=1800)

    def test_missing_observations_is_unobserved_not_zero(self):
        doc = readback()
        doc.pop("observations")
        entry = self.entry(doc)
        self.assertEqual(len(entry["readings"]), 1)
        reading = entry["readings"][0]
        self.assertEqual(reading["state"], monitor.STATE_UNKNOWN)
        self.assertIsNone(reading["value"])
        self.assertIn("observations", reading["reason"])

    def test_present_block_without_object_is_unobserved(self):
        doc = readback()
        doc["observations"]["cpu"] = {"state": "PRESENT", "value": "32.5"}
        cpu = by_label(self.entry(doc))["CPU"]
        self.assertEqual(cpu["state"], monitor.STATE_UNKNOWN)
        self.assertIsNone(cpu["value"])
        self.assertIn("未读到", cpu["reason"])

    def test_service_state_and_gpu_utilization_are_not_health(self):
        readings = by_label(self.entry(readback()))
        self.assertEqual(readings["服务 gateway0"]["severity"], "CRITICAL")
        self.assertEqual(readings["服务 serverosd"]["severity"], "OK")
        # 71% utilization is a counter, not a health verdict.
        self.assertEqual(readings["GPU A6000 利用率"]["severity"], "OK")
        self.assertEqual(readings["GPU A6000 利用率"]["kind"], "gpu")


class ReportReceiptContractTest(unittest.TestCase):
    """Report cursors move only on a DELIVERED receipt ABOUT that report."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = surface.SurfaceStore(Path(self._tmp.name) / "surfaces.json")

    def bind_live(self):
        self.store.stage_live(
            decision={"action": surface.CREATE, "slot": 0, "events": [],
                      "remote_id": None, "reasons": [surface.HEARTBEAT]},
            at=T0, idempotency_key="k-live", recipient="user:ou_x")
        self.store.confirm_live(
            at=T0, receipt={"state": surface.delivery.DELIVERED,
                            "remote_id": "om_live"})

    def bind_report(self, ref="2026-10-16/morning", key="rk"):
        self.store.stage_report(period_ref=ref, at=T0, idempotency_key=key,
                                payload_digest="digest-A")
        self.store.bind_report(period_ref=ref, at=T0, remote_id="om_report")
        return ref

    def test_create_receipt_without_message_id_is_refused(self):
        self.store.stage_live(
            decision={"action": surface.CREATE, "slot": 0, "events": [],
                      "remote_id": None, "reasons": [surface.HEARTBEAT]},
            at=T0, idempotency_key="k-live", recipient="user:ou_x")
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_live(
                at=T0, receipt={"state": surface.delivery.DELIVERED})
        live = self.store.read()["live"]
        self.assertEqual(live["phase"], surface.CREATE_PENDING)
        self.assertIsNotNone(live["pending"])

    def test_failed_live_patch_does_not_move_cursor(self):
        self.bind_live()
        decision = self.store.decide_live(at=T1)
        self.assertEqual(decision["action"], surface.PATCH)
        self.store.stage_live(decision=decision, at=T1,
                              idempotency_key="k-patch",
                              recipient="user:ou_x")
        before = self.store.summary()["live"]["last_delivered_regular_slot"]
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_live(
                at=T1, receipt={"state": "FAILED", "why": "timeout"})
        after = self.store.summary()["live"]
        self.assertEqual(after["last_delivered_regular_slot"], before)
        self.assertIsNotNone(after["pending"])

    def test_stage_report_persists_intent_before_send(self):
        ref = "2026-10-16/morning"
        self.store.stage_report(period_ref=ref, at=T0, idempotency_key="rk",
                                payload_digest="digest-A")
        row = self.store.read()["reports"][0]
        self.assertEqual(row["phase"], surface.CREATE_PENDING)
        self.assertIsNone(row["remote_id"])
        self.assertEqual(row["pending"]["idempotency_key"], "rk")

    def test_bind_report_refuses_empty_message_id(self):
        ref = "2026-10-16/morning"
        self.store.stage_report(period_ref=ref, at=T0, idempotency_key="rk")
        with self.assertRaises(surface.CardSurfaceError):
            self.store.bind_report(period_ref=ref, at=T0, remote_id="")

    def test_failed_report_patch_keeps_cursor_and_pending(self):
        ref = self.bind_report()
        self.store.apply_report(
            period={"period_ref": ref},
            decision={"action": surface.PATCH, "period_ref": ref},
            at=T1, archive={"summary": "first"})
        row = self.store.read()["reports"][0]
        self.assertEqual(row["last_patch_at"], T0)
        self.assertIsNotNone(row["pending_patch"])
        with self.assertRaises(surface.CardSurfaceError):
            self.store.ack_report_patch(
                period_ref=ref, at=T1,
                receipt={"state": "FAILED", "remote_id": "om_report"})
        row = self.store.read()["reports"][0]
        self.assertEqual(row["last_patch_at"], T0)
        self.assertIsNotNone(row["pending_patch"])

    def test_report_final_requires_version_and_same_message(self):
        ref = self.bind_report()
        self.store.apply_report(
            period={"period_ref": ref},
            decision={"action": surface.FINAL, "period_ref": ref},
            at=T2, archive={"summary": "final"}, prepared_version="v1")
        bad = [
            {"state": "DELIVERED", "version": "v2", "remote_id": "om_report"},
            {"state": "DELIVERED", "version": "v1", "remote_id": "om_other"},
            {"state": "DELIVERED", "version": "v1"},
            {"state": "FAILED", "version": "v1", "remote_id": "om_report"},
        ]
        for receipt in bad:
            with self.subTest(receipt=receipt):
                with self.assertRaises(surface.CardSurfaceError):
                    self.store.confirm_report(period_ref=ref, at=T2,
                                              receipt=receipt)
                self.assertEqual(
                    self.store.read()["reports"][0]["phase"],
                    surface.FINAL_PENDING)
        self.store.confirm_report(
            period_ref=ref, at=T2,
            receipt={"state": "DELIVERED", "version": "v1",
                     "remote_id": "om_report"})
        self.assertEqual(self.store.read()["reports"][0]["phase"], surface.FROZEN)

    def test_report_operations_do_not_touch_live(self):
        self.bind_live()
        before = json.dumps(self.store.summary()["live"], sort_keys=True)
        ref = self.bind_report()
        self.store.apply_report(
            period={"period_ref": ref},
            decision={"action": surface.PATCH, "period_ref": ref},
            at=T1, archive={"summary": "s"})
        self.store.apply_report(
            period={"period_ref": ref},
            decision={"action": surface.FINAL, "period_ref": ref},
            at=T2, archive={"summary": "f"}, prepared_version="v1")
        self.store.confirm_report(
            period_ref=ref, at=T2,
            receipt={"state": "DELIVERED", "version": "v1",
                     "remote_id": "om_report"})
        after = json.dumps(self.store.summary()["live"], sort_keys=True)
        self.assertEqual(after, before)

    def test_report_create_resumes_same_key_then_reconciles(self):
        ref = "2026-10-16/morning"
        self.store.stage_report(period_ref=ref, at=T0, idempotency_key="rk")
        within = self.store.decide_report(
            period={"period_ref": ref, "in_period": True},
            at="2026-10-16T05:40:00Z")
        self.assertEqual(within["action"], surface.CREATE)
        self.assertTrue(within["resume"])
        self.assertEqual(within["idempotency_key"], "rk")
        past = self.store.decide_report(
            period={"period_ref": ref, "in_period": True}, at=PAST_WINDOW)
        self.assertEqual(past["action"], surface.SKIP)
        self.assertEqual(past["reason"], surface.NEEDS_RECONCILE)


class SampleLedgerOwnershipTest(unittest.TestCase):
    """One active sample ledger; the second one is explicitly superseded."""

    def test_paths_and_schemas_are_distinct(self):
        state = Path("/home/example/state/runtime/alerts/state.json")
        self.assertNotEqual(journal.journal_path(state),
                            source.trend_archive_path(state))
        self.assertNotEqual(journal.SCHEMA, source.TREND_SCHEMA)

    def test_peer_model_source_defaults_to_no_second_ledger(self):
        default = inspect.signature(bridge.peer_model_source).parameters["archive"].default
        self.assertIsNone(default)

    def test_trend_archive_is_marked_superseded(self):
        text = Path(source.__file__).read_text(encoding="utf-8")
        start = text.index("class TrendArchive")
        self.assertIn("SUPERSEDED", text[start:start + 2500])


class KnownGapTest(unittest.TestCase):
    """Confirmed gaps. expectedFailure is not a fix; see V13-G* in the matrix."""

    @unittest.expectedFailure
    def test_g1_missing_principal_must_be_unknown(self):
        doc = readback()
        doc.pop("principal")
        state, why, _ = bridge.core_verdict(
            doc, device_ref=TOKYO, now=NOW, stale_after_s=1800)
        self.assertEqual(state, monitor.STATE_UNKNOWN, why)

    @unittest.expectedFailure
    def test_g2_non_object_source_must_be_unknown_not_a_crash(self):
        state, _, _ = bridge.core_verdict(
            readback(source="not-an-object"), device_ref=TOKYO, now=NOW,
            stale_after_s=1800)
        self.assertEqual(state, monitor.STATE_UNKNOWN)

    @unittest.expectedFailure
    def test_g3_live_patch_receipt_without_message_id_must_be_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = surface.SurfaceStore(Path(tmp) / "surfaces.json")
            store.stage_live(
                decision={"action": surface.CREATE, "slot": 0, "events": [],
                          "remote_id": None, "reasons": [surface.HEARTBEAT]},
                at=T0, idempotency_key="k-live", recipient="user:ou_x")
            store.confirm_live(
                at=T0, receipt={"state": surface.delivery.DELIVERED,
                                "remote_id": "om_live"})
            decision = store.decide_live(at=T1)
            store.stage_live(decision=decision, at=T1,
                             idempotency_key="k-patch",
                             recipient="user:ou_x")
            with self.assertRaises(surface.CardSurfaceError):
                store.confirm_live(
                    at=T1, receipt={"state": surface.delivery.DELIVERED})

    @unittest.expectedFailure
    def test_g5_core_readings_must_not_silently_lose_cpu_and_swap_axes(self):
        entry = bridge.device_from_core_readback(
            readback(), device_ref=TOKYO, now=NOW)
        sample = journal.sample_from_readings(
            entry["readings"], at=NOW, provenance="core")
        missing = [axis for axis, value in sample["readings"].items()
                   if value is None]
        self.assertEqual(missing, [], f"axes silently lost: {missing}")

    @unittest.expectedFailure
    def test_g6_same_report_key_with_different_payload_is_not_a_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = surface.SurfaceStore(Path(tmp) / "surfaces.json")
            ref = "2026-10-16/morning"
            store.stage_report(period_ref=ref, at=T0, idempotency_key="rk",
                               payload_digest="digest-A")
            result = store.stage_report(period_ref=ref, at=T0,
                                        idempotency_key="rk",
                                        payload_digest="digest-B")
            self.assertFalse(result.get("idempotent"), result)

    @unittest.expectedFailure
    def test_g7_unknown_freshness_within_ttl_must_not_claim_ttl_exceeded(self):
        _, why, _ = bridge.core_verdict(
            readback(freshness={"state": "STALE"}), device_ref=TOKYO,
            now=NOW, stale_after_s=1800)
        self.assertNotIn("超过上限", why or "")

    @unittest.expectedFailure
    def test_g9_report_create_pending_without_durable_key_is_refused_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "surfaces.json"
            path.write_text(json.dumps({
                "schema": surface.SCHEMA,
                "live": None,
                "reports": [{"period_ref": "p", "phase": surface.CREATE_PENDING,
                             "pending": {"action": "CREATE", "at": T0}}],
                "retired": [],
            }), encoding="utf-8")
            with self.assertRaises(surface.CardSurfaceError):
                surface.SurfaceStore(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
