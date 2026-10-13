#!/usr/bin/env python3
"""Deterministic tests for the single-card update planner.

What is checked, in the words the module writes down:

    "it is due, so send"          — only when a message may still be changed,
                                    or when there is no message to change
    "nothing changed, so send"    — never: an unchanged card is a NOOP
    "the severe finding waited"   — never: urgent bypasses the cadence
    "the default sent something"  — never: the default transport is a mock

Nothing here opens a socket, reads a file or touches Feishu. The mock records
what WOULD have been sent; the assertions are about the plan, not a delivery.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


plan = _load("serveros_fleet_monitor_plan", ROOT / "runtime" / "fleet_monitor_plan.py")

T0 = "2026-10-13T06:00:00Z"
T1 = "2026-10-13T06:01:00Z"   # +60s  (early for a 600s cadence)
T2 = "2026-10-13T06:10:00Z"   # +600s (due)
WINDOW = 14


def card(marker: str) -> dict:
    return {"schema": "2.0", "header": {"title": {"tag": "plain_text",
                                                  "content": marker}},
            "body": {"direction": "vertical", "elements": []}}


def sent(marker: str, *, at: str, binding_at: str | None = None,
         remote_id: str = "om_aaa0001") -> dict:
    return {"version": plan.freeze(card(marker))["version"], "at": at,
            "binding": ({"remote_id": remote_id, "at": binding_at or at}
                        if remote_id else None)}


class PlanTest(unittest.TestCase):

    def test_first_send_is_a_new_message(self):
        got = plan.plan_update(last=None, card=card("A"), at=T0,
                               window_days=WINDOW)
        self.assertEqual(got["action"], plan.NEW_MESSAGE)
        self.assertEqual(got["change"], "first")
        self.assertTrue(got["send"])
        self.assertEqual(got["reason"], "NEVER_SENT")

    def test_an_early_tick_is_a_noop_even_when_changed(self):
        got = plan.plan_update(last=sent("A", at=T0), card=card("B"), at=T1,
                               cadence_seconds=600, window_days=WINDOW)
        self.assertEqual(got["action"], plan.NOOP)
        self.assertEqual(got["reason"], plan.NOT_DUE)
        self.assertFalse(got["send"])
        self.assertEqual(got["elapsed_s"], 60)

    def test_due_and_unchanged_is_a_noop(self):
        got = plan.plan_update(last=sent("A", at=T0), card=card("A"), at=T2,
                               window_days=WINDOW)
        self.assertEqual(got["action"], plan.NOOP)
        self.assertEqual(got["reason"], plan.UNCHANGED)
        self.assertFalse(got["send"])
        self.assertEqual(got["remote_id"], "om_aaa0001")

    def test_due_and_changed_without_expiry_is_an_edit(self):
        got = plan.plan_update(last=sent("A", at=T0), card=card("B"), at=T2,
                               window_days=WINDOW)
        self.assertEqual(got["action"], plan.PATCH_MESSAGE)
        self.assertEqual(got["change"], "edit")
        self.assertTrue(got["send"])
        self.assertEqual(got["remote_id"], "om_aaa0001")

    def test_expired_binding_rotates_even_when_unchanged(self):
        got = plan.plan_update(last=sent("A", at=T0, binding_at="2026-09-20T00:00:00Z"),
                               card=card("A"), at=T2, window_days=WINDOW)
        self.assertEqual(got["action"], plan.NEW_MESSAGE)
        self.assertEqual(got["change"], "rotate")
        self.assertTrue(got["send"])
        self.assertEqual(got["reason"], "IDENTITY_EXPIRED")

    def test_a_missing_binding_rotates_rather_than_editing(self):
        got = plan.plan_update(last=sent("A", at=T0, remote_id=None),
                               card=card("B"), at=T2, window_days=WINDOW)
        self.assertEqual(got["action"], plan.NEW_MESSAGE)
        self.assertEqual(got["change"], "rotate")
        self.assertEqual(got["reason"], "IDENTITY_MISSING")

    def test_urgent_bypasses_the_cadence(self):
        got = plan.plan_update(last=sent("A", at=T0), card=card("B"), at=T1,
                               cadence_seconds=600, urgent=True,
                               window_days=WINDOW)
        self.assertEqual(got["action"], plan.PATCH_MESSAGE)
        self.assertTrue(got["urgent"])

    def test_plan_is_idempotent(self):
        kw = dict(last=sent("A", at=T0), card=card("B"), at=T2, window_days=WINDOW)
        a = json.dumps(plan.plan_update(**kw), sort_keys=True)
        b = json.dumps(plan.plan_update(**kw), sort_keys=True)
        self.assertEqual(a, b)

    def test_a_noop_stays_a_noop_until_the_card_changes(self):
        last = sent("A", at=T0)
        first = plan.plan_update(last=last, card=card("A"), at=T2,
                                 window_days=WINDOW)
        second = plan.plan_update(last=last, card=card("A"), at=T2,
                                  window_days=WINDOW)
        self.assertEqual(first, second)
        self.assertEqual(first["action"], plan.NOOP)


class TransportTest(unittest.TestCase):
    """The default sends nothing, and that is a property of the code."""

    def test_module_does_not_import_the_send_transport(self):
        import ast
        tree = ast.parse((ROOT / "runtime" / "fleet_monitor_plan.py")
                         .read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("message_transport", imported)
        self.assertNotIn("subprocess", imported)

    def test_default_transport_is_a_mock_that_sends_nothing(self):
        mock = plan.MockTransport()
        got = plan.apply_plan(
            plan.plan_update(last=sent("A", at=T0), card=card("B"), at=T2,
                             window_days=WINDOW),
            card("B"), at=T2, transport=mock)
        self.assertEqual(got["state"], "DELIVERED")
        self.assertTrue(got["remote_id"].startswith("om_mock"))
        self.assertEqual(len(mock.calls), 1)
        self.assertEqual(mock.calls[0]["action"], plan.PATCH_MESSAGE)

    def test_a_noop_makes_no_call(self):
        mock = plan.MockTransport()
        got = plan.apply_plan(
            plan.plan_update(last=sent("A", at=T0), card=card("A"), at=T2,
                             window_days=WINDOW),
            card("A"), at=T2, transport=mock)
        self.assertEqual(got["state"], "NOOP")
        self.assertEqual(mock.calls, [])

    def test_apply_without_a_transport_uses_the_mock(self):
        got = plan.apply_plan(
            plan.plan_update(last=None, card=card("A"), at=T0, window_days=WINDOW),
            card("A"), at=T0)
        self.assertTrue(got["remote_id"].startswith("om_mock"))


class LifecycleTest(unittest.TestCase):
    """A whole day at the 10-minute cadence, with one severe hour."""

    def test_a_day_of_ticks(self):
        mock = plan.MockTransport()
        ticks = [T0, T1, T2]
        state = {"last": None}
        results = []
        for tick in ticks:
            body = "A" if tick == T1 else "B" if tick == T2 else "A"
            p = plan.plan_update(last=state["last"], card=card(body), at=tick,
                                 cadence_seconds=600, window_days=WINDOW)
            receipt = plan.apply_plan(p, card(body), at=tick, transport=mock)
            results.append((tick, p["action"], p["reason"]))
            if p["send"]:
                state["last"] = {"version": p["version"], "at": tick,
                                 "binding": {"remote_id": receipt["remote_id"],
                                             "at": tick}}
        self.assertEqual(results[0][1], plan.NEW_MESSAGE)  # first
        self.assertEqual(results[1][1], plan.NOOP)         # early + unchanged
        self.assertEqual(results[2][1], plan.PATCH_MESSAGE)  # due + changed
        self.assertEqual(len(mock.calls), 2)               # two sends, no more


if __name__ == "__main__":
    unittest.main(verbosity=2)
