"""Offline contract tests for the Fleet Next read-only Legacy source adapter.

Every client here is a local fake. No socket, no credential, no Legacy service,
no CloudCLI call, no Feishu send. The JSON is synthetic; the identities are
deliberately similar so that a fold would show up as a passing test.

The four failures this file exists to prevent:

*   two Legacy instances with the same `runId` being reported as one run;
*   a session the caller was not granted being exposed;
*   a remote error message or credential being reflected into an observation;
*   a native `completed`, a `RUN_NOT_FOUND`, an offline route or a boot mismatch
    being reported as success.
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "integrations" / "fleet-next-monitor-source-adapter"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fleet_next_monitor_source_adapter import adapter as adapter_mod  # noqa: E402
from fleet_next_monitor_source_adapter import cli as cli_mod          # noqa: E402
from fleet_next_monitor_source_adapter import serializer as ser       # noqa: E402
from fleet_next_monitor_source_adapter.contract import (              # noqa: E402
    STATUS_FAILED,
    STATUS_OFFLINE,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_STALE,
    STATUS_TERMINAL_VERIFIED,
    STATUS_UNKNOWN,
    CallerGrant,
    SourceRefused,
)
from fleet_next_monitor_source_adapter.adapter import (               # noqa: E402
    FixtureLegacyClient,
    LegacyReadOnlySourceAdapter,
    RemoteRefused,
    RouteOffline,
    RunNotFound,
)

DEVICE = "device:synthetic"
SESSION = "device:synthetic|claude|sess-1"
RUN_ID = "run-same"
DELIVERY_ID = "delivery-1"
BOOT = "boot-1"
INSTANCE_A = "instance-a"
INSTANCE_B = "instance-b"
ROUTE = "route-a"
SHA = "a" * 40
OTHER_SHA = "b" * 40
SECRET = "sk-live-SYNTHETIC-0123456789"


def grant(**overrides) -> CallerGrant:
    values = dict(principal="monitor", devices=frozenset({DEVICE}),
                  projects=frozenset({"project-1"}),
                  sessions=frozenset({SESSION}),
                  views=frozenset({"session", "run", "ci"}))
    values.update(overrides)
    return CallerGrant(**values)


class RecordingClient:
    """A fake Legacy client that records calls and returns synthetic JSON."""

    def __init__(self, *, run=None, session=None, ci=None, raises=None,
                 instance=INSTANCE_A, boot=BOOT) -> None:
        self.fleet_instance_id = instance
        self.route_id = ROUTE
        self.boot_id = boot
        self.provider = "claude"
        self._run = run or {}
        self._session = session or {}
        self._ci = ci or {}
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def _maybe_raise(self):
        if self._raises is not None:
            raise self._raises

    def read_session(self, *, device_ref, session_ref):
        self.calls.append(("read_session", {"device_ref": device_ref, "session_ref": session_ref}))
        self._maybe_raise()
        return dict(self._session)

    def read_run(self, *, device_ref, session_ref, run_id=None, delivery_id=None):
        self.calls.append(("read_run", {"device_ref": device_ref, "session_ref": session_ref,
                                        "run_id": run_id, "delivery_id": delivery_id}))
        self._maybe_raise()
        return dict(self._run)

    def read_ci(self, *, source_sha):
        self.calls.append(("read_ci", {"source_sha": source_sha}))
        self._maybe_raise()
        return dict(self._ci)


def adapter(client, **overrides) -> LegacyReadOnlySourceAdapter:
    return LegacyReadOnlySourceAdapter(client, grant(**overrides), max_readings=500)


def a_run(**overrides) -> dict:
    payload = {
        "instanceId": INSTANCE_A, "deviceRef": DEVICE, "sessionRef": SESSION,
        "runId": RUN_ID, "deliveryId": DELIVERY_ID, "bootId": BOOT,
        "provider": "claude", "status": "running",
    }
    payload.update(overrides)
    return payload


class NoWritePathExistsTest(unittest.TestCase):
    def test_the_adapter_offers_no_write_verb(self):
        public = {name for name in dir(LegacyReadOnlySourceAdapter) if not name.startswith("_")}
        for verb in adapter_mod.LegacyReadOnlySourceAdapter.WRITE_VERBS:
            self.assertNotIn(verb, public)
        for name in public:
            self.assertTrue(name.startswith("observe") or name in {"WRITE_VERBS", "readings_used"},
                            f"unexpected public surface: {name}")

    def test_the_protocol_declares_only_reads(self):
        declared = {name for name in dir(adapter_mod.LegacyReadOnlyClient) if not name.startswith("_")}
        self.assertEqual(declared, {"read_session", "read_run", "read_ci"})
        for verb in adapter_mod.LegacyReadOnlySourceAdapter.WRITE_VERBS:
            self.assertNotIn(verb, declared)

    def test_a_denied_grant_costs_zero_client_calls(self):
        client = RecordingClient(run=a_run())
        with self.assertRaises(SourceRefused) as caught:
            adapter(client, devices=frozenset()).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(caught.exception.code, "DENIED")
        self.assertEqual(client.calls, [])

    def test_another_callers_session_is_not_exposed(self):
        client = RecordingClient(run=a_run())
        with self.assertRaises(SourceRefused) as caught:
            adapter(client, sessions=frozenset({"someone-elses-session"})).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(caught.exception.code, "DENIED")
        self.assertEqual(client.calls, [])

    def test_a_view_the_caller_lacks_is_refused_before_the_call(self):
        client = RecordingClient(ci={"sourceSHA": SHA, "conclusion": "success"})
        with self.assertRaises(SourceRefused):
            adapter(client, views=frozenset({"session"})).observe_ci(
                source_sha=SHA, project="project-1")
        self.assertEqual(client.calls, [])


class IdentityIsNeverFoldedTest(unittest.TestCase):
    def test_two_instances_with_one_run_id_do_not_cross(self):
        same_run_from_b = a_run(instanceId=INSTANCE_B, status="completed",
                                transcript=["hello"])
        a = RecordingClient(run=same_run_from_b, instance=INSTANCE_A)
        b = RecordingClient(run=same_run_from_b, instance=INSTANCE_B)
        observed_a = adapter(a).observe_run(device_ref=DEVICE, session_ref=SESSION,
                                            run_id=RUN_ID)
        observed_b = adapter(b).observe_run(device_ref=DEVICE, session_ref=SESSION,
                                            run_id=RUN_ID)
        self.assertEqual(observed_a.status, STATUS_UNKNOWN)
        self.assertEqual(observed_a.reason, "INSTANCE_MISMATCH")
        self.assertEqual(observed_b.status, STATUS_TERMINAL_VERIFIED)
        self.assertEqual(observed_a.identity.fleet_instance_id, INSTANCE_A)
        self.assertEqual(observed_b.identity.fleet_instance_id, INSTANCE_B)
        self.assertEqual(observed_a.identity.run_id, observed_b.identity.run_id)

    def test_every_identity_keeps_its_own_field(self):
        observation = adapter(RecordingClient(run=a_run(transcript=["x"]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
            delivery_id=DELIVERY_ID, source_sha=SHA)
        identity = observation.as_dict()["identity"]
        for key in ("instanceId", "routeId", "deviceRef", "sessionRef", "runId",
                    "deliveryId", "bootId", "provider", "sourceSHA", "observedAt"):
            self.assertIn(key, identity)
        self.assertEqual(identity["runId"], RUN_ID)
        self.assertEqual(identity["deliveryId"], DELIVERY_ID)
        self.assertEqual(identity["bootId"], BOOT)
        self.assertEqual(identity["sourceSHA"], SHA)

    def test_the_native_block_is_kept_apart_from_the_requested_identity(self):
        """The request named a run; the answer also carried a delivery id. Both
        survive, in different blocks, so a monitor can compare instead of assume."""
        observation = adapter(RecordingClient(run=a_run(transcript=["x"]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID, source_sha=SHA)
        self.assertIsNone(observation.identity.delivery_id)
        self.assertEqual(observation.native["deliveryId"], DELIVERY_ID)
        self.assertEqual(observation.native["instanceId"], INSTANCE_A)
        self.assertEqual(observation.identity.run_id, observation.native["runId"])

    def test_a_boot_mismatch_is_unknown_never_terminal(self):
        observation = adapter(RecordingClient(run=a_run(
            status="completed", bootId="boot-2", transcript=["x"]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_UNKNOWN)
        self.assertEqual(observation.reason, "BOOT_MISMATCH")


class CompletionNeedsNativeEvidenceTest(unittest.TestCase):
    def test_completed_without_transcript_or_artifact_is_unknown(self):
        observation = adapter(RecordingClient(run=a_run(status="completed"))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_UNKNOWN)
        self.assertEqual(observation.reason, "NO_NATIVE_ACTIVITY")

    def test_completed_with_a_transcript_is_terminal_verified(self):
        observation = adapter(RecordingClient(run=a_run(
            status="completed", transcript=[{"role": "assistant"}]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)

    def test_a_verified_artifact_must_be_bound_to_the_expected_source_sha(self):
        unbound = a_run(status="completed",
                        verifiedArtifact={"verified": True, "sourceSHA": OTHER_SHA})
        bound = a_run(status="completed",
                      verifiedArtifact={"verified": True, "sourceSHA": SHA})
        self.assertEqual(
            adapter(RecordingClient(run=unbound)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                source_sha=SHA).status,
            STATUS_UNKNOWN)
        self.assertEqual(
            adapter(RecordingClient(run=bound)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                source_sha=SHA).status,
            STATUS_TERMINAL_VERIFIED)

    def test_a_claimed_terminal_without_evidence_is_not_terminal(self):
        for payload in (a_run(status="completed", terminal=True, done=True),
                        a_run(status="DONE"),
                        a_run(status="completed", result="success")):
            with self.subTest(payload=payload.get("status")):
                observation = adapter(RecordingClient(run=payload)).observe_run(
                    device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
                self.assertNotEqual(observation.status, STATUS_TERMINAL_VERIFIED)

    def test_run_not_found_keeps_native_retryable_and_is_unknown(self):
        observation = adapter(RecordingClient(
            raises=RunNotFound(reason="expired", retryable=False))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, delivery_id=DELIVERY_ID)
        self.assertEqual(observation.status, STATUS_UNKNOWN)
        self.assertEqual(observation.reason, "expired")
        self.assertIs(observation.retryable, False)

    def test_an_offline_route_is_offline_not_failed(self):
        observation = adapter(RecordingClient(raises=RouteOffline())).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_OFFLINE)
        self.assertNotEqual(observation.status, STATUS_FAILED)

    def test_explicit_failure_is_failed(self):
        observation = adapter(RecordingClient(run=a_run(status="failed"))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_FAILED)

    def test_queued_and_running_are_distinct(self):
        self.assertEqual(
            adapter(RecordingClient(run=a_run(status="queued"))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID).status,
            STATUS_QUEUED)
        self.assertEqual(
            adapter(RecordingClient(run=a_run(status="running"))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID).status,
            STATUS_RUNNING)


class RemoteTextNeverCrossesTest(unittest.TestCase):
    def test_a_remote_exception_message_is_not_reflected(self):
        observation = adapter(RecordingClient(
            raises=RuntimeError(f"please use {SECRET}"))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        rendered = json.dumps(observation.as_dict(), default=str)
        self.assertNotIn(SECRET, rendered)
        self.assertEqual(observation.reason, "REMOTE_RuntimeError")

    def test_a_remote_refusal_carries_no_message_at_all(self):
        exc = RemoteRefused()
        self.assertNotIn("sk-", str(exc))
        observation = adapter(RecordingClient(raises=exc)).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.reason, "REMOTE_REFUSED")

    def test_a_payload_full_of_secrets_is_not_echoed(self):
        payload = a_run(status="running", message=SECRET, token=SECRET,
                        stack=f"at x({SECRET})")
        observation = adapter(RecordingClient(run=payload)).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        rendered = json.dumps(observation.as_dict(), default=str)
        self.assertNotIn(SECRET, rendered)


class NoInventedCapabilitiesTest(unittest.TestCase):
    def test_no_model_list_can_be_produced(self):
        observation = adapter(RecordingClient(run=a_run(models=["m-1", "m-2"]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        rendered = json.dumps(observation.as_dict())
        self.assertNotIn("models", rendered)
        self.assertFalse(hasattr(LegacyReadOnlySourceAdapter, "list_models"))

    def test_a_ci_draft_or_unverified_run_is_unknown(self):
        draft = adapter(RecordingClient(ci={
            "sourceSHA": SHA, "conclusion": "success", "verified": True, "isDraft": True,
        })).observe_ci(source_sha=SHA, project="project-1")
        unverified = adapter(RecordingClient(ci={
            "sourceSHA": SHA, "conclusion": "success", "verified": False,
        })).observe_ci(source_sha=SHA, project="project-1")
        verified = adapter(RecordingClient(ci={
            "sourceSHA": SHA, "conclusion": "success", "verified": True, "isDraft": False,
        })).observe_ci(source_sha=SHA, project="project-1")
        self.assertEqual(draft.reason, "DRAFT_CI")
        self.assertEqual(unverified.reason, "UNVERIFIED_CI")
        self.assertEqual(verified.status, STATUS_TERMINAL_VERIFIED)

    def test_ci_must_answer_the_exact_source_sha(self):
        observation = adapter(RecordingClient(ci={
            "sourceSHA": OTHER_SHA, "conclusion": "success", "verified": True,
        })).observe_ci(source_sha=SHA, project="project-1")
        self.assertEqual(observation.status, STATUS_UNKNOWN)
        self.assertEqual(observation.reason, "IDENTITY_MISMATCH")


class TheReadBudgetIsBoundedTest(unittest.TestCase):
    def test_five_hundred_readings_is_the_cap(self):
        client = RecordingClient(run=a_run(status="running"))
        subject = LegacyReadOnlySourceAdapter(client, grant(), max_readings=500)
        for _ in range(500):
            self.assertEqual(
                subject.observe_run(device_ref=DEVICE, session_ref=SESSION,
                                    run_id=RUN_ID).status,
                STATUS_RUNNING)
        capped = subject.observe_run(device_ref=DEVICE, session_ref=SESSION,
                                     run_id=RUN_ID)
        self.assertEqual(capped.status, STATUS_STALE)
        self.assertEqual(capped.reason, "READINGS_CAPPED")
        self.assertEqual(len(client.calls), 500, "the cap was exceeded at the client")

    def test_a_non_positive_budget_is_refused(self):
        for bad in (0, -1, True):
            with self.subTest(bad=bad):
                with self.assertRaises(SourceRefused):
                    LegacyReadOnlySourceAdapter(RecordingClient(), grant(), max_readings=bad)


class TheEdgeAcceptsNoCodeTest(unittest.TestCase):
    def test_import_shaped_fields_are_refused(self):
        for field in ("factory", "module", "callable", "python_module", "__import__"):
            with self.subTest(field=field):
                with self.assertRaises(SourceRefused) as caught:
                    ser.parse_request(json.dumps({"view": "run", field: "os.system"}))
                self.assertEqual(caught.exception.code, "IMPORT_SPEC_REFUSED")

    def test_unknown_fields_are_refused(self):
        with self.assertRaises(SourceRefused) as caught:
            ser.parse_request(json.dumps({"view": "run", "command": "rm -rf /"}))
        self.assertEqual(caught.exception.code, "UNKNOWN_FIELD")

    def test_the_envelope_cannot_smuggle_a_client(self):
        document = {"request": {"view": "run", "device_ref": DEVICE,
                               "session_ref": SESSION, "run_id": RUN_ID},
                    "grant": {"principal": "m", "devices": [DEVICE],
                              "sessions": [SESSION], "views": ["run"]},
                    "fixture": {"run": a_run()},
                    "client": {"module": "os"}}
        with self.assertRaises(SourceRefused):
            ser.parse_envelope(json.dumps(document))

    def test_the_package_has_no_network_or_process_import(self):
        forbidden = {"socket", "urllib", "requests", "http", "subprocess", "importlib"}
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertEqual(imported & forbidden, set(), f"{path.name} imports {imported & forbidden}")

    def test_no_eval_exec_or_dunder_import(self):
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, {"eval", "exec", "__import__"}, path.name)

    def test_input_size_is_bounded(self):
        huge = json.dumps({"view": "run", "pad": "x" * (ser.MAX_INPUT_BYTES + 1)})
        with self.assertRaises(SourceRefused) as caught:
            ser.parse_request(huge)
        self.assertEqual(caught.exception.code, "INPUT_TOO_LARGE")


class TheCliIsOneOfflineTickTest(unittest.TestCase):
    def test_one_tick_reads_once_and_exits_zero(self):
        document = {
            "request": {"view": "run", "device_ref": DEVICE, "session_ref": SESSION,
                        "run_id": RUN_ID},
            "grant": {"principal": "monitor", "devices": [DEVICE],
                      "sessions": [SESSION], "views": ["run"]},
            "fixture": {"run": a_run(status="running")},
            "instance": {"instanceId": INSTANCE_A, "routeId": ROUTE, "bootId": BOOT},
        }
        code, output = cli_mod.run_text(json.dumps(document))
        self.assertEqual(code, cli_mod.EXIT_OK)
        payload = json.loads(output)
        self.assertEqual(payload["status"], STATUS_RUNNING)
        self.assertEqual(payload["identity"]["instanceId"], INSTANCE_A)
        self.assertEqual(payload["readingsUsed"], 1)

    def test_an_ungranted_view_exits_refused(self):
        document = {
            "request": {"view": "run", "device_ref": DEVICE,
                        "session_ref": SESSION, "run_id": RUN_ID},
            "grant": {"principal": "monitor", "devices": [DEVICE],
                      "sessions": [SESSION], "views": []},
            "fixture": {"run": a_run()},
        }
        with self.assertRaises(SourceRefused) as caught:
            cli_mod.run_text(json.dumps(document))
        self.assertEqual(caught.exception.code, "DENIED")

    def test_a_missing_input_file_is_blocked_upstream(self):
        code = cli_mod.main(["--input", "Z:/definitely/not/here.json"])
        self.assertEqual(code, cli_mod.EXIT_BLOCKED_UPSTREAM)

    def test_the_fixture_client_is_never_implicit(self):
        """A caller must inject it; there is no default Legacy client."""
        import inspect
        signature = inspect.signature(LegacyReadOnlySourceAdapter.__init__)
        self.assertIs(signature.parameters["client"].default, inspect.Parameter.empty)


if __name__ == "__main__":
    unittest.main(verbosity=2)
