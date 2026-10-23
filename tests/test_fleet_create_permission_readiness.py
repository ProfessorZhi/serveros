#!/usr/bin/env python3
"""`fleet_create_session` reports what the device applied, not what it was asked.

The failure this file exists for: a caller minted three sessions on a remote
device, every create answered successfully, and every session then sat waiting
on an approval nobody was there to give. "Created" was read as "permission-
ready", and nothing in the answer contradicted it.

So the create now reads the mode back from the device, in the same call, and
this file pins the four states that readback can produce plus the risk that
follows from the reported mode.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp.config import CreateGrant  # noqa: E402

from test_fleet_create_session_runtime import (  # noqa: E402
    PROJECT,
    TwoAccounts,
    _CreatingProvider,
)


class _ReadbackProvider(_CreatingProvider):
    """Mints like the shared harness, and can be told what it reports back."""

    def __init__(self, ref, runtime, *, native_id, **kw):
        super().__init__(ref, runtime, native_id=native_id, **kw)
        self.reported = "dontAsk"
        self.nested = True
        self.raises = None
        self.runs = False

    def create(self, *, project, message=None, provider=None, model=None,
               lifecycle_mode=None, permission_mode=None):
        payload = super().create(project=project, message=message, provider=provider,
                                 model=model, lifecycle_mode=lifecycle_mode,
                                 permission_mode=permission_mode)
        if message and self.runs:
            payload["runId"] = "run-1"
        return payload

    def get_session(self, key):
        self.calls.append(("get_session", key.ref))
        if self.raises is not None:
            raise self.raises
        if self.reported is None:
            return {"session_ref": key.ref, "served_by": self.runtime}
        if self.nested:
            return {"session_ref": key.ref, "served_by": self.runtime,
                    "session": {"permissionMode": self.reported}}
        return {"session_ref": key.ref, "served_by": self.runtime,
                "permissionMode": self.reported}


def _harness(tmp, *, modes=(), **kw):
    grants = {PROJECT: CreateGrant(permission_modes=tuple(modes))}
    return TwoAccounts(tmp, grants=grants, provider_cls=_ReadbackProvider, **kw)


class ReadbackBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class TheFourStates(ReadbackBase):

    def test_the_reported_mode_matching_the_request_is_confirmed(self):
        h = _harness(self.tmp, modes=("dontAsk",))
        h.owner_provider.reported = "dontAsk"
        out = h.create(permission_mode="dontAsk")
        readiness = out["permission_readiness"]
        self.assertEqual(readiness["state"], "CONFIRMED")
        self.assertEqual(readiness["requested"], "dontAsk")
        self.assertEqual(readiness["effective"], "dontAsk")
        self.assertEqual(out["defaults"]["effective"]["permission_mode"], "dontAsk")

    def test_a_device_that_applied_something_else_is_a_mismatch(self):
        """The case the whole readback exists for: the ask does not survive."""
        h = _harness(self.tmp, modes=("dontAsk",))
        h.owner_provider.reported = "acceptEdits"
        out = h.create(permission_mode="dontAsk")
        readiness = out["permission_readiness"]
        self.assertEqual(readiness["state"], "MISMATCH")
        self.assertEqual(readiness["requested"], "dontAsk")
        self.assertEqual(readiness["effective"], "acceptEdits")
        self.assertEqual(readiness["prompt_risk"], "HIGH",
                         "the reported mode is what decides the risk, not the ask")

    def test_a_device_default_with_nothing_requested_is_reported(self):
        h = _harness(self.tmp, modes=())
        h.owner_provider.reported = "dontAsk"
        out = h.create()
        readiness = out["permission_readiness"]
        self.assertEqual(readiness["state"], "REPORTED")
        self.assertIsNone(readiness["requested"])
        self.assertEqual(readiness["effective"], "dontAsk")
        self.assertEqual(readiness["prompt_risk"], "NONE")

    def test_no_reported_mode_is_unverified_and_never_the_resolved_value(self):
        h = _harness(self.tmp, modes=("dontAsk",))
        h.owner_provider.reported = None
        out = h.create(permission_mode="dontAsk")
        readiness = out["permission_readiness"]
        self.assertEqual(readiness["state"], "UNVERIFIED")
        self.assertEqual(readiness["prompt_risk"], "UNKNOWN")
        self.assertIsNone(readiness["effective"])
        self.assertEqual(readiness["requested"], "dontAsk",
                         "the request is kept beside the absence of an answer")
        self.assertIn("not proof", readiness["reason"])
        self.assertIsNone(out["defaults"]["effective"]["permission_mode"])

    def test_a_failed_readback_does_not_turn_a_minted_session_into_an_error(self):
        h = _harness(self.tmp, modes=("dontAsk",))
        h.owner_provider.raises = RuntimeError("device went away")
        out = h.create(permission_mode="dontAsk")
        self.assertEqual(out["session_ref"],
                         f"{h.owner_provider.device_ref}|{h.owner_provider.runtime}"
                         f"|native-owner-0001")
        readiness = out["permission_readiness"]
        self.assertEqual(readiness["state"], "UNVERIFIED")
        self.assertIn("RuntimeError", readiness["reason"])

    def test_a_flat_top_level_mode_is_read_too(self):
        """The device may report the mode beside `session` rather than inside it."""
        h = _harness(self.tmp, modes=("dontAsk",))
        h.owner_provider.reported = "dontAsk"
        h.owner_provider.nested = False
        out = h.create(permission_mode="dontAsk")
        self.assertEqual(out["permission_readiness"]["state"], "CONFIRMED")


class PromptRiskFollowsTheReportedMode(ReadbackBase):

    def test_interactive_modes_are_high_risk(self):
        for mode in ("default", "manual", "auto", "acceptEdits"):
            with self.subTest(mode=mode):
                h = _harness(self.tmp)
                h.owner_provider.reported = mode
                self.assertEqual(h.create()["permission_readiness"]["prompt_risk"],
                                 "HIGH")

    def test_unattended_modes_are_no_risk(self):
        for mode in ("dontAsk", "plan", "bypassPermissions"):
            with self.subTest(mode=mode):
                h = _harness(self.tmp)
                h.owner_provider.reported = mode
                self.assertEqual(h.create()["permission_readiness"]["prompt_risk"],
                                 "NONE")

    def test_a_mode_this_build_does_not_know_is_unknown_not_safe(self):
        h = _harness(self.tmp)
        h.owner_provider.reported = "someFutureMode"
        readiness = h.create()["permission_readiness"]
        self.assertEqual(readiness["state"], "REPORTED")
        self.assertEqual(readiness["prompt_risk"], "UNKNOWN")


class RunStartedIsSeparateFromPermissionReadiness(ReadbackBase):

    def test_a_first_message_says_a_run_started(self):
        h = _harness(self.tmp)
        h.owner_provider.runs = True
        out = h.create(message="go")
        self.assertTrue(out["permission_readiness"]["run_started"])

    def test_a_session_minted_without_a_message_has_no_run(self):
        h = _harness(self.tmp)
        out = h.create()
        self.assertFalse(out["permission_readiness"]["run_started"])


class AReplayIsHonestAboutWhatItDoesNotKnow(ReadbackBase):

    def test_a_replay_reports_unverified_and_calls_no_device(self):
        h = _harness(self.tmp)
        h.owner_provider.reported = "dontAsk"
        h.create(message=None, idempotency_key="k-1")
        before = list(h.owner_provider.calls)
        again = h.create(message=None, idempotency_key="k-1")
        self.assertTrue(again["idempotent_replay"])
        readiness = again["permission_readiness"]
        self.assertEqual(readiness["state"], "UNVERIFIED")
        self.assertEqual(readiness["prompt_risk"], "UNKNOWN")
        self.assertIsNone(readiness["effective"])
        self.assertIn("no device call was made", readiness["reason"])
        self.assertEqual(h.owner_provider.calls, before,
                         "a replay must not reach the device, including to read "
                         "back a mode it did not apply")


if __name__ == "__main__":
    unittest.main()
