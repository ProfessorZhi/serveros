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
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Mapping

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "integrations" / "fleet-next-monitor-source-adapter"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from fleet_next_monitor_source_adapter import adapter as adapter_mod  # noqa: E402
from fleet_next_monitor_source_adapter import cli as cli_mod          # noqa: E402
from fleet_next_monitor_source_adapter import serializer as ser       # noqa: E402
from fleet_next_monitor_source_adapter.contract import (              # noqa: E402
    STATUS_DENIED,
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
                                lastAssistantMessage={"content": "hello"})
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

    def test_completed_with_an_assistant_turn_is_terminal_verified(self):
        """A carrier is normalised into the monitor's message, not accepted as
        a second, looser form of evidence (the #508 P1)."""
        observation = adapter(RecordingClient(run=a_run(
            status="completed",
            transcript=[{"role": "user", "content": "go"},
                        {"role": "assistant", "content": "native output"}],
        ))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)

    def test_a_verified_artifact_must_be_bound_to_the_expected_source_sha(self):
        unbound = a_run(status="completed",
                        artifacts=[{"verified": True, "sourceSHA": OTHER_SHA}])
        bound = a_run(status="completed",
                      artifacts=[{"verified": True, "sourceSHA": SHA}])
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
            "read_request": {"view": "run", "device_ref": DEVICE, "session_ref": SESSION,
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
            "read_request": {"view": "run", "device_ref": DEVICE,
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


# ---------------------------------------------------------------------------
# The wire name of the request block.
#
# `request` is also the name of a record field that `abilities/target-contract.yaml`
# declares and `tests/test_declared_implementations.py` tracks. That gate is a
# name-based reader detector: a lookup spelled `d.get("request")` here is counted
# as a reader of THAT record field, and the field is on the gate's KNOWN_UNREAD
# list, so the adapter made the gate demand the deletion of an exemption that is
# still correct. The two are different declaration surfaces, but the gate cannot
# tell them apart and it is not this package's test to change.
#
# So the adapter's own namespace moved: the canonical v2 key is `read_request`,
# and a v1 document is still accepted through the one constant that names the
# retired spelling. `test_no_module_reads_the_unrelated_record_field_name` is the
# regression: the retired name may appear in a comment, a docstring or a named
# constant, never in a read position.
# ---------------------------------------------------------------------------
RECORD_FIELD_NAME = "request"

_ENVELOPE = {
    "schema": "serveros/fleet-next-monitor-request.v1",
    "grant": {"principal": "monitor", "devices": [DEVICE],
              "sessions": [SESSION], "views": ["run"]},
    "fixture": {"run": a_run(status="running")},
    "instance": {"instanceId": INSTANCE_A, "routeId": ROUTE, "bootId": BOOT},
}
_RUN_BLOCK = {"view": "run", "device_ref": DEVICE, "session_ref": SESSION,
              "run_id": RUN_ID}


def _envelope(key: str, block: dict | None = None) -> dict:
    document = json.loads(json.dumps(_ENVELOPE))
    document[key] = block if block is not None else dict(_RUN_BLOCK)
    return document


def _compat():
    """The v2 compatibility view, imported lazily.

    Imported here rather than at module scope so that its absence reads as a
    handful of failing contract tests instead of a collection error that hides
    the 35 tests above.
    """
    from fleet_next_monitor_source_adapter import compat as compat_mod
    return compat_mod


class TheWireNameIsNamespacedTest(unittest.TestCase):
    def test_the_canonical_key_produces_an_observation(self):
        code, output = cli_mod.run_text(json.dumps(_envelope("read_request")))
        self.assertEqual(code, cli_mod.EXIT_OK)
        self.assertEqual(json.loads(output)["status"], STATUS_RUNNING)

    def test_a_v1_document_is_still_accepted(self):
        """v1 spelled the block `request`. A document written to the old
        contract must keep parsing; the migration is a rename, not a break."""
        code, output = cli_mod.run_text(json.dumps(_envelope(RECORD_FIELD_NAME)))
        self.assertEqual(code, cli_mod.EXIT_OK)
        self.assertEqual(json.loads(output)["status"], STATUS_RUNNING)

    def test_a_document_carrying_both_keys_is_refused(self):
        """Two spellings of one block is an ambiguity, and an ambiguity here
        would mean one of them is silently ignored."""
        document = _envelope("read_request")
        document[RECORD_FIELD_NAME] = {"view": "session", "device_ref": DEVICE,
                                       "session_ref": SESSION}
        with self.assertRaises(SourceRefused) as caught:
            cli_mod.run_text(json.dumps(document))
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertIn("both", caught.exception.why)

    def test_the_retired_spelling_is_named_once(self):
        """The retired spelling lives in exactly one place: the constant that
        documents it. A second literal anywhere is how it comes back."""
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        holders = []
        for path in sorted(package.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and node.value == RECORD_FIELD_NAME:
                    holders.append((path.name, getattr(node, "lineno", 0)))
        self.assertEqual(len(holders), 1, f"the retired spelling is spelled {holders}")

    def test_no_module_reads_the_unrelated_record_field_name(self):
        """The governance detector's rule, applied to this package only.

        It is the rule of `tests/test_declared_implementations.py` (a constant
        subscript, a `.get`/`.pop`/`.setdefault` key, a `in` operand, or an
        element of a tuple/list/set), minus the tool-bag exclusion, which does
        not apply: this package has no tool handler. A hit means the gate will
        read this package as a reader of `abilities/target-contract.yaml`'s
        record field, which is a different field that nothing reads.
        """
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        hits: list[str] = []
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Subscript)
                        and isinstance(node.slice, ast.Constant)
                        and node.slice.value == RECORD_FIELD_NAME):
                    hits.append(f"{path.name}:{node.lineno} (subscript)")
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in {"get", "pop", "setdefault", "getattr"}
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == RECORD_FIELD_NAME):
                    hits.append(f"{path.name}:{node.lineno} (.{node.func.attr})")
                elif (isinstance(node, ast.Compare)
                        and isinstance(node.left, ast.Constant)
                        and node.left.value == RECORD_FIELD_NAME
                        and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)):
                    hits.append(f"{path.name}:{node.lineno} (in)")
                elif isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    for element in node.elts:
                        if isinstance(element, ast.Constant) and element.value == RECORD_FIELD_NAME:
                            hits.append(f"{path.name}:{node.lineno} (container)")
        self.assertEqual(hits, [], "\n".join(
            ["this package must not read the record field of the same name:",
             *[f"  {hit}" for hit in hits]]))


# ---------------------------------------------------------------------------
# The published call shape of #493's monitor, used as the compatibility edge.
#
# #493's `sources.observe_watch` (sources.py:494-504) calls
# `observe_run(..., expected_boot_id=, expected_source_sha=)` and reads the
# result as a mapping; `observe_ci(source_sha=, project=)`. Both are exactly
# reproduced below, so a signature drift is a failure here and not in a
# composed PR.
# ---------------------------------------------------------------------------
PUBLISHED_RUN_KWARGS = ("device_ref", "session_ref", "run_id", "delivery_id",
                        "project", "expected_boot_id", "expected_source_sha")
PUBLISHED_CI_KWARGS = ("source_sha", "project")


def monitor_run_watch(**overrides) -> dict:
    watch = {"kind": "run", "device_ref": DEVICE, "session_ref": SESSION,
             "run_id": RUN_ID, "delivery_id": DELIVERY_ID,
             "expected_boot_id": BOOT, "expected_source_sha": SHA,
             "project": "project-1"}
    watch.update(overrides)
    return watch


def monitor_observe_run(source, watch: dict):
    """The body of `observe_watch` for a run watch, verbatim in call shape."""
    return source.observe_run(
        device_ref=watch["device_ref"], session_ref=watch["session_ref"],
        run_id=watch.get("run_id"), delivery_id=watch.get("delivery_id"),
        project=watch.get("project"),
        expected_boot_id=watch.get("expected_boot_id"),
        expected_source_sha=watch.get("expected_source_sha"))


def monitor_observe_ci(source, watch: dict):
    return source.observe_ci(source_sha=watch["expected_source_sha"],
                             project=watch.get("project"))


def monitor_ci_watch(**overrides) -> dict:
    watch = {"kind": "ci", "expected_source_sha": SHA, "project": "project-1"}
    watch.update(overrides)
    return watch


def cloudcli_run(**overrides) -> dict:
    """The shape a real CloudCLI answer carries: native activity is the last
    assistant message, not a transcript list."""
    payload = a_run(status="completed", sourceSHA=SHA,
                    lastAssistantMessage={"content": "native output"})
    payload.update(overrides)
    return payload


class TheMonitorCanUseTheAdapterTest(unittest.TestCase):
    """The two failures #495 pinned at the seam, each with its fix."""

    def test_the_published_call_shape_does_not_raise_type_error(self):
        client = RecordingClient(run=cloudcli_run())
        view = monitor_observe_run(adapter(client), monitor_run_watch())
        self.assertIsInstance(view, Mapping)
        self.assertEqual(view["status"], STATUS_TERMINAL_VERIFIED)
        self.assertEqual(client.calls[0][0], "read_run")

    def test_the_adapter_declares_every_published_parameter(self):
        import inspect
        declared = set(inspect.signature(
            LegacyReadOnlySourceAdapter.observe_run).parameters)
        missing = set(PUBLISHED_RUN_KWARGS) - declared
        self.assertEqual(missing, set(), f"observe_run lacks {sorted(missing)}")
        declared_ci = set(inspect.signature(
            LegacyReadOnlySourceAdapter.observe_ci).parameters)
        self.assertEqual(set(PUBLISHED_CI_KWARGS) - declared_ci, set())

    def test_an_explicit_boot_expectation_is_honoured_not_dropped(self):
        """The client's boot and the answer's boot agree; the WATCH expects a
        different one. Dropping the expectation (what #495's bridge did) would
        report this run as verified, which is the swallowed mismatch."""
        client = RecordingClient(run=cloudcli_run(bootId=BOOT), boot=BOOT)
        view = monitor_observe_run(adapter(client),
                                   monitor_run_watch(expected_boot_id="boot-2"))
        self.assertEqual((view["status"], view["reason"]),
                         (STATUS_UNKNOWN, "BOOT_MISMATCH"))
        self.assertNotEqual(view["status"], STATUS_TERMINAL_VERIFIED)

    def test_with_no_expectation_the_clients_boot_is_the_expectation(self):
        client = RecordingClient(run=cloudcli_run(bootId="boot-2"), boot=BOOT)
        view = monitor_observe_run(adapter(client),
                                   monitor_run_watch(expected_boot_id=None))
        self.assertEqual((view["status"], view["reason"]),
                         (STATUS_UNKNOWN, "BOOT_MISMATCH"))

    def test_an_explicit_source_sha_binds_the_artifact(self):
        bound = a_run(status="completed",
                      artifacts=[{"verified": True, "sourceSHA": SHA}])
        other = a_run(status="completed",
                      artifacts=[{"verified": True, "sourceSHA": OTHER_SHA}])
        self.assertEqual(
            monitor_observe_run(adapter(RecordingClient(run=bound)),
                                monitor_run_watch())["status"],
            STATUS_TERMINAL_VERIFIED)
        self.assertEqual(
            monitor_observe_run(adapter(RecordingClient(run=other)),
                                monitor_run_watch())["status"],
            STATUS_UNKNOWN)

    def test_two_disagreeing_sha_arguments_are_refused_before_any_read(self):
        client = RecordingClient(run=cloudcli_run())
        with self.assertRaises(SourceRefused) as caught:
            adapter(client).observe_run(device_ref=DEVICE, session_ref=SESSION,
                                        run_id=RUN_ID, source_sha=OTHER_SHA,
                                        expected_source_sha=SHA)
        self.assertEqual(caught.exception.code, "BAD_REQUEST")
        self.assertEqual(client.calls, [])

    def test_the_observation_indexes_like_the_mapping_the_monitor_reads(self):
        observation = adapter(RecordingClient(run=cloudcli_run())).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation["status"], STATUS_TERMINAL_VERIFIED)
        self.assertEqual(observation["identity"]["runId"], RUN_ID)
        self.assertEqual(observation.get("reason"), None)
        self.assertIn("status", dict(observation))
        self.assertEqual(dict(observation)["view"], "run")
        self.assertEqual(json.loads(ser.dump_observation(observation))["status"],
                         STATUS_TERMINAL_VERIFIED)

    def test_an_unknown_key_is_a_key_error_not_an_attribute(self):
        observation = adapter(RecordingClient(run=cloudcli_run())).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        with self.assertRaises(KeyError):
            observation["no-such-field"]


class NativeActivityVocabularyTest(unittest.TestCase):
    """Two real Legacy answers, one standard of evidence."""

    def test_a_last_assistant_message_is_native_activity(self):
        observation = adapter(RecordingClient(run=cloudcli_run())).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)

    def test_an_empty_assistant_message_is_not(self):
        for content in ("", "   ", None):
            with self.subTest(content=content):
                observation = adapter(RecordingClient(
                    run=cloudcli_run(lastAssistantMessage={"content": content}))).observe_run(
                    device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
                self.assertEqual((observation.status, observation.reason),
                                 (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_a_device_note_saying_nothing_was_readable_vetoes_the_message(self):
        """A device that tells us it had nothing is not evidence of output."""
        observation = adapter(RecordingClient(run=cloudcli_run(
            note="no readable assistant message was produced"))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual((observation.status, observation.reason),
                         (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_an_artifacts_list_bound_to_the_expected_sha_is_evidence(self):
        run = a_run(status="completed",
                    artifacts=[{"verified": True, "sha": SHA}])
        self.assertEqual(
            adapter(RecordingClient(run=run)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                expected_source_sha=SHA).status,
            STATUS_TERMINAL_VERIFIED)

    def test_an_artifacts_list_for_another_sha_is_not_evidence(self):
        run = a_run(status="completed",
                    artifacts=[{"verified": True, "sha": OTHER_SHA}])
        self.assertEqual(
            adapter(RecordingClient(run=run)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                expected_source_sha=SHA).status,
            STATUS_UNKNOWN)

    def test_an_unverified_artifact_in_the_list_is_not_evidence(self):
        run = a_run(status="completed",
                    artifacts=[{"verified": False, "sha": SHA}])
        self.assertEqual(
            adapter(RecordingClient(run=run)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                expected_source_sha=SHA).status,
            STATUS_UNKNOWN)

    def test_a_transcript_of_bare_strings_is_not_native_activity(self):
        """#508 P1, in one line: this exact payload used to be
        `TERMINAL_VERIFIED` here and `REMOTE_ValueError` / `UNKNOWN` on the
        monitor's own path, because a non-empty list was read as "output".
        A string names no role, so it cannot be shown to be assistant text."""
        for carrier in (["x"], "x", [{"role": "assistant"}]):
            with self.subTest(carrier=carrier):
                observation = adapter(RecordingClient(run=a_run(
                    status="completed", transcript=carrier))).observe_run(
                    device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
                self.assertEqual(
                    (observation.status, observation.reason),
                    (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))


def _evidence():
    """The shared evidence module, imported where it is used.

    Imported inside the test rather than at module scope so that the RED run on
    the unfixed tree still collects this file: there the module does not exist,
    and only the tests that name it fail instead of the whole file erroring.
    """
    from fleet_next_monitor_source_adapter import evidence
    return evidence


class TheEvidenceIsTheMonitorsEvidenceTest(unittest.TestCase):
    """One standard of evidence on both paths (#508 P1, #515).

    The monitor reads a run fact through a *closed* vocabulary and credits
    native output from exactly two places. This adapter must answer the same
    yes/no as the monitor for the same facts, and must never turn a carrier it
    can only partly understand into a verified run.
    """

    def normalised(self, payload) -> dict:
        return _evidence().normalise_run_fact(payload)

    def run_status(self, payload, **call) -> tuple[str, str | None]:
        call.setdefault("device_ref", DEVICE)
        call.setdefault("session_ref", SESSION)
        call.setdefault("run_id", RUN_ID)
        observation = adapter(RecordingClient(run=payload)).observe_run(**call)
        return observation.status, observation.reason

    def test_the_evidence_seam_is_importable_from_the_package(self):
        """So the monitor's own reader can adopt it with a one-line import
        instead of owning a second implementation."""
        import fleet_next_monitor_source_adapter as package
        evidence = _evidence()
        self.assertIs(package.normalise_run_fact, evidence.normalise_run_fact)
        self.assertIs(package.native_evidence, evidence.native_evidence)
        self.assertEqual(package.MONITOR_RUN_KEYS, evidence.MONITOR_RUN_KEYS)

    def test_the_monitors_vocabulary_is_pinned(self):
        """Copied from `offline.py:27` at #493's head `6286bfbc`. If the
        monitor's closed set moves, this is where the divergence shows up."""
        self.assertEqual(
            _evidence().MONITOR_RUN_KEYS,
            frozenset({"instanceId", "deviceRef", "sessionRef", "sessionId",
                       "runId", "deliveryId", "bootId", "status",
                       "lastAssistantMessage", "artifacts", "note"}))

    def test_every_normalised_fact_is_one_the_monitor_would_accept(self):
        """No key outside the closed set survives — a key the monitor refuses
        is exactly the `REMOTE_ValueError` half of the divergence."""
        evidence = _evidence()
        payloads = [a_run(),
                    a_run(transcript=["x"]),
                    a_run(messages=[{"role": "assistant", "content": "hi"}]),
                    a_run(secret=SECRET, token=SECRET, stack=SECRET),
                    cloudcli_run(transcript=["junk"]),
                    cloudcli_run(lastAssistantMessage={"content": "ok",
                                                       "token": SECRET})]
        for payload in payloads:
            with self.subTest(payload=sorted(payload)):
                fact = evidence.normalise_run_fact(payload)
                self.assertLessEqual(set(fact), evidence.MONITOR_RUN_KEYS)
                self.assertNotIn("transcript", fact)
                self.assertNotIn("messages", fact)
                self.assertNotIn(SECRET, json.dumps(fact, default=str))

    def test_the_normaliser_emits_the_runs_last_assistant_turn(self):
        fact = self.normalised(a_run(status="completed", transcript=[
            {"role": "assistant", "content": "first"},
            {"role": "user", "content": "again"},
            {"role": "assistant", "content": "second"},
        ]))
        self.assertEqual(fact["lastAssistantMessage"], {"content": "second"})

    def test_a_prompt_is_not_output(self):
        for carrier in ([{"role": "user", "content": "do it"}],
                        [{"role": "system", "content": "do it"}],
                        [{"role": "tool", "content": "do it"}]):
            with self.subTest(carrier=carrier):
                self.assertEqual(
                    self.run_status(a_run(status="completed", transcript=carrier)),
                    (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_an_assistant_turn_without_text_is_not_output(self):
        for content in ("", "   ", None):
            with self.subTest(content=content):
                turn = {"role": "assistant"}
                if content is not None:
                    turn["content"] = content
                self.assertEqual(
                    self.run_status(a_run(status="completed", transcript=[turn])),
                    (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_another_runs_turn_is_not_this_runs_output(self):
        """The turn names an identity that contradicts the fact: it is not this
        run's output however plausible its text is, and the run does not fall
        back to an earlier turn either."""
        for forged in ({"runId": "run-other"}, {"sessionRef": "sess-other"},
                       {"deviceRef": "device:other"}, {"bootId": "boot-2"},
                       {"instanceId": INSTANCE_B}):
            with self.subTest(forged=sorted(forged)):
                turn = {"role": "assistant", "content": "stolen"}
                turn.update(forged)
                payload = a_run(status="completed", transcript=[
                    {"role": "assistant", "content": "mine"}, turn])
                self.assertEqual(
                    self.run_status(payload), (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_a_turn_that_agrees_with_the_fact_is_own_output(self):
        turn = {"role": "assistant", "content": "mine",
                "runId": RUN_ID, "sessionRef": SESSION, "instanceId": INSTANCE_A}
        self.assertEqual(
            self.run_status(a_run(status="completed", transcript=[turn]))[0],
            STATUS_TERMINAL_VERIFIED)

    def test_a_declared_assistant_message_keeps_its_text_and_nothing_else(self):
        """The monitor reads `.content`; everything else in the object is remote
        content nobody reads, so it is not forwarded."""
        message = {"content": "ok", "role": "assistant", "token": SECRET}
        fact = self.normalised(a_run(status="completed",
                                     lastAssistantMessage=message,
                                     transcript=["junk"]))
        self.assertEqual(fact["lastAssistantMessage"], {"content": "ok"})
        self.assertEqual(
            self.run_status(a_run(status="completed",
                                  lastAssistantMessage=message,
                                  transcript=["junk"]))[0],
            STATUS_TERMINAL_VERIFIED)

    def test_an_empty_declared_message_is_not_upgraded_by_a_carrier(self):
        """The monitor reads `lastAssistantMessage` first. An answer that calls
        its own message blank is not rescued by a carrier it also shipped."""
        payload = a_run(status="completed", lastAssistantMessage={"content": ""},
                        transcript=[{"role": "assistant", "content": "maybe"}])
        self.assertEqual(self.normalised(payload)["lastAssistantMessage"],
                         {"content": ""})
        self.assertEqual(self.run_status(payload),
                         (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_the_note_veto_outranks_a_normalised_turn(self):
        payload = a_run(status="completed",
                        note="no readable assistant message was produced",
                        transcript=[{"role": "assistant", "content": "maybe"}])
        self.assertEqual(self.run_status(payload),
                         (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_an_unbound_verified_artifact_is_not_evidence(self):
        """#508 D2: the previous version returned True whenever the caller
        named no sha, so a verified artifact naming no commit verified the run.
        The monitor requires the commit; so does this adapter."""
        for artifact in ({"verified": True},
                         {"verified": True, "sha": ""},
                         {"verified": True, "sha": "   "},
                         {"verified": True, "sha": SHA}):
            with self.subTest(artifact=artifact):
                self.assertEqual(
                    self.run_status(a_run(status="completed", artifacts=[artifact])),
                    (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))
        self.assertEqual(
            self.run_status(a_run(status="completed",
                                  artifacts=[{"verified": True, "sha": SHA}]),
                            expected_source_sha=SHA)[0],
            STATUS_TERMINAL_VERIFIED)

    def test_the_singular_artifact_alias_is_not_the_monitors_vocabulary(self):
        """`verifiedArtifact` was this adapter's own spelling. The monitor has
        `artifacts[]`, so the singular is not evidence — accepting it would be a
        second standard of evidence on one path only."""
        for name in ("verifiedArtifact", "verified_artifact",
                     "verified_native_artifact"):
            with self.subTest(name=name):
                payload = a_run(status="completed",
                                **{name: {"verified": True, "sourceSHA": SHA}})
                self.assertEqual(
                    self.run_status(payload, expected_source_sha=SHA),
                    (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY"))

    def test_a_delivery_only_watch_adopts_the_native_run_id(self):
        """#493's rule: the answer saying which run this delivery is, is
        adoption, not invention."""
        observation = adapter(RecordingClient(run=cloudcli_run())).observe_run(
            device_ref=DEVICE, session_ref=SESSION, delivery_id=DELIVERY_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)
        self.assertEqual(observation.identity.run_id, RUN_ID)
        self.assertEqual(observation.identity.delivery_id, DELIVERY_ID)

    def test_a_delivery_only_watch_invents_no_run_id(self):
        payload = cloudcli_run()
        payload.pop("runId")
        observation = adapter(RecordingClient(run=payload)).observe_run(
            device_ref=DEVICE, session_ref=SESSION, delivery_id=DELIVERY_ID)
        self.assertIsNone(observation.identity.run_id)
        self.assertIsNone(observation.native.get("runId"))

    def test_adoption_is_refused_when_the_answer_names_another_instance(self):
        observation = adapter(RecordingClient(
            run=cloudcli_run(instanceId=INSTANCE_B))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, delivery_id=DELIVERY_ID)
        self.assertEqual((observation.status, observation.reason),
                         (STATUS_UNKNOWN, "INSTANCE_MISMATCH"))
        self.assertIsNone(observation.identity.run_id)

    def test_the_carrier_is_never_forwarded_as_native(self):
        observation = adapter(RecordingClient(run=a_run(
            status="completed", token=SECRET,
            transcript=[{"role": "assistant", "content": SECRET}]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)
        self.assertNotIn("transcript", observation.native)
        self.assertNotIn("messages", observation.native)
        rendered = json.dumps(observation.as_dict(), default=str)
        self.assertNotIn(SECRET, rendered)


class TheCiVocabularyIsSharedTest(unittest.TestCase):
    """#493's CI answer uses `head_sha`/`draft`; #490's own used
    `sourceSHA`/`isDraft`. One adapter reads both, and never credits a green
    pipeline on another commit."""

    def monitor_ci(self, **overrides) -> dict:
        payload = {"head_sha": SHA, "status": "completed",
                   "conclusion": "success", "draft": False,
                   "verified": True, "trusted": True}
        payload.update(overrides)
        return payload

    def test_the_monitors_own_ci_shape_is_understood(self):
        observation = adapter(RecordingClient(ci=self.monitor_ci())).observe_ci(
            source_sha=SHA, project="project-1")
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)

    def test_a_draft_in_the_monitors_shape_is_unknown(self):
        observation = adapter(RecordingClient(ci=self.monitor_ci(draft=True))).observe_ci(
            source_sha=SHA, project="project-1")
        self.assertEqual((observation.status, observation.reason),
                         (STATUS_UNKNOWN, "DRAFT_CI"))

    def test_an_untrusted_green_pipeline_is_unknown(self):
        observation = adapter(RecordingClient(
            ci=self.monitor_ci(trusted=False))).observe_ci(
            source_sha=SHA, project="project-1")
        self.assertEqual((observation.status, observation.reason),
                         (STATUS_UNKNOWN, "UNVERIFIED_CI"))

    def test_green_on_another_commit_is_not_green_here(self):
        observation = adapter(RecordingClient(
            ci=self.monitor_ci(head_sha=OTHER_SHA))).observe_ci(
            source_sha=SHA, project="project-1")
        self.assertEqual(observation.status, STATUS_UNKNOWN)

    def test_a_failed_pipeline_is_failed(self):
        observation = adapter(RecordingClient(
            ci=self.monitor_ci(conclusion="failure"))).observe_ci(
            source_sha=SHA, project="project-1")
        self.assertEqual(observation.status, STATUS_FAILED)


class TheCompatibilityViewTest(unittest.TestCase):
    """The versioned view #493 can be handed: mapping out, refusals as
    observations, and the monitor's own words for the two reasons whose
    vocabulary differs."""

    def test_the_view_names_its_contract_version(self):
        compat = _compat()
        self.assertEqual(compat.COMPAT_SCHEMA,
                         "serveros/fleet-next-monitor-source-adapter.compat.v2")

    def test_it_returns_a_mapping_for_a_run(self):
        compat = _compat()
        view = compat.CompatMonitorSource(
            adapter(RecordingClient(run=cloudcli_run()))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertIsInstance(view, dict)
        self.assertEqual(view["schema"], compat.COMPAT_SCHEMA)
        self.assertEqual(view["status"], STATUS_TERMINAL_VERIFIED)

    def test_it_reads_its_usage_from_the_adapter(self):
        compat = _compat()
        native = adapter(RecordingClient(run=cloudcli_run()))
        view = compat.CompatMonitorSource(native).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(view["readingsUsed"], 1)
        self.assertEqual(native.readings_used, 1)

    def test_a_denial_is_an_observation_and_costs_zero_reads(self):
        compat = _compat()
        client = RecordingClient(run=cloudcli_run())
        view = compat.CompatMonitorSource(
            adapter(client, devices=frozenset())).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                expected_boot_id=BOOT, expected_source_sha=SHA)
        self.assertEqual((view["status"], view["reason"]),
                         ("DENIED", "NOT_GRANTED"))
        self.assertEqual(view["readingsUsed"], 0)
        self.assertEqual(client.calls, [], "a denial must cost zero reads")

    def test_the_native_adapter_still_raises_on_a_denial(self):
        """The view converts; the native contract does not soften. A caller
        that wants an exception keeps getting one."""
        client = RecordingClient(run=cloudcli_run())
        with self.assertRaises(SourceRefused) as caught:
            adapter(client, devices=frozenset()).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(caught.exception.code, "DENIED")

    def test_a_ci_sha_mismatch_speaks_the_monitors_word(self):
        compat = _compat()
        answer = {"sourceSHA": OTHER_SHA, "conclusion": "success", "verified": True}
        native = adapter(RecordingClient(ci=answer)).observe_ci(
            source_sha=SHA, project="project-1")
        view = compat.CompatMonitorSource(
            adapter(RecordingClient(ci=answer))).observe_ci(
                source_sha=SHA, project="project-1")
        self.assertEqual(native.reason, "IDENTITY_MISMATCH")
        self.assertEqual((view["status"], view["reason"]),
                         (STATUS_UNKNOWN, "SHA_MISMATCH"))

    def test_every_other_reason_passes_through_unchanged(self):
        compat = _compat()
        for payload, status, reason in (
                (cloudcli_run(lastAssistantMessage={}), STATUS_UNKNOWN,
                 "NO_NATIVE_ACTIVITY"),
                (cloudcli_run(bootId="boot-2"), STATUS_UNKNOWN, "BOOT_MISMATCH"),
                (cloudcli_run(instanceId=INSTANCE_B, transcript=["x"]),
                 STATUS_UNKNOWN, "INSTANCE_MISMATCH"),
                (a_run(status="failed"), STATUS_FAILED, None)):
            with self.subTest(reason=reason):
                view = compat.CompatMonitorSource(
                    adapter(RecordingClient(run=payload))).observe_run(
                        device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                        expected_source_sha=SHA)
                self.assertEqual((view["status"], view["reason"]),
                                 (status, reason))

    def test_a_run_naming_no_identifier_is_bad_payload_for_the_monitor(self):
        compat = _compat()
        view = compat.CompatMonitorSource(
            adapter(RecordingClient(run=cloudcli_run()))).observe_run(
                device_ref=DEVICE, session_ref=SESSION)
        self.assertEqual((view["status"], view["reason"]),
                         (STATUS_UNKNOWN, "BAD_PAYLOAD"))

    def test_an_offline_route_is_offline_through_the_view(self):
        compat = _compat()
        view = compat.CompatMonitorSource(
            adapter(RecordingClient(raises=RouteOffline()))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(view["status"], STATUS_OFFLINE)
        self.assertIs(view["retryable"], True)

    def test_the_boot_expectation_survives_the_view(self):
        compat = _compat()
        view = compat.CompatMonitorSource(adapter(RecordingClient(
            run=cloudcli_run(bootId=BOOT)))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID,
                expected_boot_id="boot-2")
        self.assertEqual((view["status"], view["reason"]),
                         (STATUS_UNKNOWN, "BOOT_MISMATCH"))

    def test_a_configuration_refusal_is_not_an_observation(self):
        """A bad grant or an unnameable identity is a programming error, not a
        fact about the far end. The view must not turn it into UNKNOWN."""
        compat = _compat()
        client = RecordingClient(run=cloudcli_run(), instance="")
        with self.assertRaises(SourceRefused):
            compat.CompatMonitorSource(adapter(client)).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)

    def test_a_secret_in_the_answer_is_not_forwarded_by_the_view(self):
        compat = _compat()
        view = compat.CompatMonitorSource(adapter(RecordingClient(
            run=cloudcli_run(message=SECRET, token=SECRET)))).observe_run(
                device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertNotIn(SECRET, json.dumps(view, default=str))

    def test_the_view_reads_nothing_but_the_two_published_ci_names(self):
        compat = _compat()
        imports = _compat().CompatMonitorSource
        client = RecordingClient(ci={"head_sha": SHA, "status": "completed",
                                     "conclusion": "success", "verified": True,
                                     "trusted": True})
        view = monitor_observe_ci(compat.CompatMonitorSource(adapter(client)),
                                  monitor_ci_watch())
        self.assertEqual(view["status"], STATUS_TERMINAL_VERIFIED)
        self.assertEqual(client.calls, [("read_ci", {"source_sha": SHA})])


class TheComposedSeamFailsClosedTest(unittest.TestCase):
    """Cross-module negatives, run through the published call shape."""

    def test_one_run_id_under_two_instances_is_not_one_run(self):
        fact = cloudcli_run(instanceId=INSTANCE_B)
        a = adapter(RecordingClient(run=fact, instance=INSTANCE_A))
        b = adapter(RecordingClient(run=fact, instance=INSTANCE_B))
        view_a = monitor_observe_run(a, monitor_run_watch())
        view_b = monitor_observe_run(b, monitor_run_watch())
        self.assertEqual((view_a["status"], view_a["reason"]),
                         (STATUS_UNKNOWN, "INSTANCE_MISMATCH"))
        self.assertEqual(view_b["status"], STATUS_TERMINAL_VERIFIED)
        self.assertNotIn("runId", view_a["native"] or {})

    def test_a_forged_transcript_does_not_verify_a_foreign_answer(self):
        forged = a_run(status="completed", instanceId=INSTANCE_B,
                       transcript=[{"role": "assistant", "content": "x"}])
        view = monitor_observe_run(adapter(RecordingClient(run=forged)),
                                   monitor_run_watch())
        self.assertEqual(view["status"], STATUS_UNKNOWN)
        self.assertIsNone(view["native"])

    def test_a_claimed_done_without_evidence_never_dispatches(self):
        dispatchable = {"TERMINAL_VERIFIED", "FAILED"}
        for payload in (a_run(status="completed", done=True, terminal=True),
                        a_run(status="DONE"), cloudcli_run(lastAssistantMessage={})):
            with self.subTest(payload=payload.get("status")):
                view = monitor_observe_run(adapter(RecordingClient(run=payload)),
                                           monitor_run_watch())
                self.assertNotIn(view["status"], dispatchable)

    def test_a_boot_mismatch_never_dispatches(self):
        view = monitor_observe_run(adapter(RecordingClient(
            run=cloudcli_run(bootId="boot-2"))), monitor_run_watch())
        self.assertNotIn(view["status"], {"TERMINAL_VERIFIED", "FAILED"})

    def test_a_draft_pipeline_never_dispatches(self):
        view = monitor_observe_ci(adapter(RecordingClient(
            ci={"head_sha": SHA, "status": "completed", "conclusion": "success",
                "draft": True, "verified": True, "trusted": True})),
            monitor_ci_watch())
        self.assertNotIn(view["status"], {"TERMINAL_VERIFIED", "FAILED"})

    def test_an_unreachable_route_is_not_a_failure(self):
        view = monitor_observe_run(adapter(RecordingClient(raises=RouteOffline())),
                                   monitor_run_watch())
        self.assertEqual(view["status"], STATUS_OFFLINE)
        self.assertNotEqual(view["status"], STATUS_FAILED)

    def test_a_refused_edge_accepts_no_code_and_no_secret(self):
        document = _envelope("read_request",
                             {"view": "run", "device_ref": DEVICE,
                              "session_ref": SESSION, "run_id": RUN_ID,
                              "api_token": SECRET})
        with self.assertRaises(SourceRefused) as caught:
            cli_mod.run_text(json.dumps(document))
        self.assertEqual(caught.exception.code, "UNKNOWN_FIELD")
        self.assertNotIn(SECRET, str(caught.exception))


# ---------------------------------------------------------------------------
# W19E — one legacy answer, two real processes, one journal
# ---------------------------------------------------------------------------
#
# #508's QA measured the divergence by reading one interpreter's objects. This
# measures it the way it would show up in production: `python -m
# fleet_next_agent_monitor` is a real process with #493's own offline client,
# core and #423's real notifier; the compat path is a second real process with
# #499's adapter and `CompatMonitorSource`, then the *same* core, the *same*
# state file and the *same* sqlite ledger. Neither process can see the other's
# objects, so "sent exactly once" and "sent nothing" are facts about the
# journal file instead of facts about a shared interpreter.
#
# The claim under test is that one standard of evidence means the two paths
# cannot disagree about one fact. Where the monitor as it ships still cannot
# read a `transcript` / `messages` carrier, the difference is *pinned in the
# fail-closed direction* — the monitor never verifies what it could not read,
# and never sends — and the strict assertion is gated on the core actually
# folding the carrier. `W19_REQUIRE_NATIVE_PARITY=1` forces the strict
# assertion, which is how the RED half of this test was measured against the
# unfixed core.

MONITOR_ROOT = REPO / "integrations" / "fleet-next-agent-monitor"
PARITY_PROBE = PACKAGE_ROOT / "qa" / "parity_probe.py"

PARITY_DEVICE = "device:parity-1"
PARITY_VICTIM = "device:parity-victim"
PARITY_SESSION = "device:parity-1|claude|sess-parity"
PARITY_RUN = "run-parity-1"
PARITY_DELIVERY = "delivery-parity-1"
PARITY_BOOT = "boot-parity-1"
PARITY_INSTANCE = "inst-parity"
PARITY_ROUTE = "route-parity"
PARITY_SHA = "a" * 40
PARITY_OTHER_SHA = "f" * 40
PARITY_VERIFIED = (STATUS_TERMINAL_VERIFIED, None)
PARITY_SILENT = (STATUS_UNKNOWN, "NO_NATIVE_ACTIVITY")


def parity_fact(**overrides):
    """One Legacy answer in the monitor's own vocabulary."""
    payload = {"instanceId": PARITY_INSTANCE, "deviceRef": PARITY_DEVICE,
               "sessionRef": PARITY_SESSION, "runId": PARITY_RUN,
               "deliveryId": PARITY_DELIVERY, "bootId": PARITY_BOOT,
               "status": "completed"}
    payload.update(overrides)
    return payload


PARITY_ASSISTANT_TURN = [{"role": "user", "content": "go"},
                         {"role": "assistant", "content": "native output"}]

#: name, the one Legacy answer, the verdict both paths owe, grants, and whether
#: the answer only becomes readable once its carrier is folded.
PARITY_CASES = (
    {"name": "message",
     "fact": parity_fact(lastAssistantMessage={"content": "native output"}),
     "expected": PARITY_VERIFIED},
    {"name": "assistant_turn",
     "fact": parity_fact(transcript=PARITY_ASSISTANT_TURN),
     "expected": PARITY_VERIFIED, "carrier": True},
    {"name": "bare_string",
     "fact": parity_fact(transcript=["x"]),
     "expected": PARITY_SILENT, "carrier": True},
    {"name": "prompt_only",
     "fact": parity_fact(transcript=[{"role": "user", "content": "do it"}]),
     "expected": PARITY_SILENT, "carrier": True},
    {"name": "empty_turn",
     "fact": parity_fact(transcript=[{"role": "assistant", "content": "   "}]),
     "expected": PARITY_SILENT, "carrier": True},
    {"name": "foreign_turn",
     "fact": parity_fact(transcript=[{"role": "assistant", "content": "stolen",
                                      "runId": "run-other"}]),
     "expected": PARITY_SILENT, "carrier": True},
    {"name": "no_output",
     "fact": parity_fact(),
     "expected": PARITY_SILENT},
    {"name": "unbound_artifact",
     "fact": parity_fact(artifacts=[{"verified": True}]),
     "expected": PARITY_SILENT},
    # The watch names no commit, so the artifact names nothing to be bound to:
    # #508 D2, which the pre-fix adapter read as "verified".
    {"name": "unbound_artifact_no_sha",
     "fact": parity_fact(artifacts=[{"verified": True}]),
     "expected": PARITY_SILENT, "watch": {"expected_source_sha": None}},
    {"name": "sha_other",
     "fact": parity_fact(artifacts=[{"verified": True, "sha": PARITY_OTHER_SHA}]),
     "expected": PARITY_SILENT},
    {"name": "sha_bound",
     "fact": parity_fact(artifacts=[{"verified": True, "sha": PARITY_SHA}]),
     "expected": PARITY_VERIFIED},
    {"name": "boot_other",
     "fact": parity_fact(bootId="boot-parity-2",
                         lastAssistantMessage={"content": "native output"}),
     "expected": (STATUS_UNKNOWN, "BOOT_MISMATCH")},
    {"name": "run_other",
     "fact": parity_fact(runId="run-parity-2",
                         lastAssistantMessage={"content": "native output"}),
     "expected": (STATUS_UNKNOWN, "IDENTITY_MISMATCH")},
    {"name": "denied",
     "fact": parity_fact(lastAssistantMessage={"content": "native output"}),
     "expected": (STATUS_DENIED, "NOT_GRANTED"),
     "devices": (PARITY_VICTIM,)},
)


def _child_python(program: str, workdir: Path) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with this tree's packages on its path."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("FLEET_NEXT_AGENT_MONITOR_")}
    env["PYTHONPATH"] = os.pathsep.join(
        [str(MONITOR_ROOT), str(PACKAGE_ROOT), str(REPO)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-c", program], cwd=str(workdir),
                          env=env, capture_output=True, text=True, timeout=120)


def the_monitor_does_not_run_here(workdir: Path) -> str | None:
    """Why the two-process comparison cannot run on this platform, or None.

    The monitor's own core locks its cursor with `fcntl`, which is POSIX-only,
    so on Windows `python -m fleet_next_agent_monitor` does not start at all —
    the same reason #493's own test file is red there. Skipping is the honest
    statement: a parity assertion that cannot start two processes asserts
    nothing, and a green one would be a claim about a run that never happened.
    """
    try:
        done = _child_python("import fleet_next_agent_monitor.core\n", workdir)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"a child interpreter could not be started ({type(exc).__name__})"
    if done.returncode == 0:
        return None
    lines = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
    detail = lines[-1] if lines else f"exit {done.returncode}"
    return (f"the monitor's own core is not importable here ({detail}), so "
            f"`python -m fleet_next_agent_monitor` cannot start. Its cursor lock is "
            f"`fcntl`-based, which is the usual cause on Windows; whatever the "
            f"cause, the two-process parity runs for real on Linux (hosted CI) and "
            f"is not turned into a green assertion on a platform where the monitor "
            f"itself does not run")


def the_shipped_core_folds_a_carrier(workdir: Path) -> bool:
    """Whether #493's own reader *and* classifier can see a carrier.

    Asked of the shipped module in its own process, not of a copy of the file,
    so a `.pyc` or an earlier import in this interpreter cannot answer for it.
    Both halves are needed and both belong to the monitor owner: the closed
    reader must admit the key (`offline.RUN_KEYS`) and the classifier must fold
    it (`sources` has to reach `normalise_run_fact`, or carry its own fold,
    `_normalise_native`, as #527 landed it). One alone changes nothing, so one
    alone is not "folded".
    """
    program = (
        "import inspect, sys\n"
        "from fleet_next_agent_monitor import offline, sources\n"
        "admits = 'transcript' in offline.RUN_KEYS and 'messages' in offline.RUN_KEYS\n"
        "folds = ('normalise_run_fact' in inspect.getsource(sources)\n"
        "         or callable(getattr(sources, '_normalise_native', None)))\n"
        "sys.stdout.write('yes' if admits and folds else 'no')\n")
    try:
        done = _child_python(program, workdir)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == "yes"


class TheTwoPathsAgreeOnOneFactTest(unittest.TestCase):
    """One Legacy answer, both entry points, one journal on disk."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="w19e-parity-"))
        unavailable = the_monitor_does_not_run_here(cls.root)
        if unavailable:
            shutil.rmtree(cls.root, ignore_errors=True)
            raise unittest.SkipTest(unavailable)
        cls.folds = the_shipped_core_folds_a_carrier(cls.root)
        cls.strict = cls.folds or os.environ.get("W19_REQUIRE_NATIVE_PARITY") == "1"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    # -- scenario: one answer, written twice from the same dict ------------
    def _scenario(self, case, tag):
        directory = self.root / f"{case['name']}-{tag}"
        directory.mkdir(parents=True, exist_ok=True)
        fact = case["fact"]
        # Both documents are written from the SAME fact object, and both declare
        # the same instance, route and boot: the premise of the comparison is
        # that the two paths are asked about one answer, from one client.
        declared = {"fleet_instance_id": PARITY_INSTANCE,
                    "route_id": PARITY_ROUTE, "boot_id": PARITY_BOOT}
        (directory / "facts.json").write_text(json.dumps(dict(
            declared, ci={}, runs={PARITY_RUN: fact})), encoding="utf-8")
        (directory / "fixture.json").write_text(json.dumps(dict(
            declared, run=fact)), encoding="utf-8")
        watch = {"kind": "run", "device_ref": PARITY_DEVICE,
                 "session_ref": PARITY_SESSION, "run_id": PARITY_RUN,
                 "delivery_id": PARITY_DELIVERY,
                 "expected_boot_id": PARITY_BOOT,
                 "expected_source_sha": PARITY_SHA, "major": True}
        watch.update(case.get("watch") or {})
        (directory / "config.json").write_text(json.dumps({
            "schema": "serveros/fleet-next-agent-monitor-config.v1",
            "grant": {"principal": "w19e",
                      "devices": list(case.get("devices") or (PARITY_DEVICE,)),
                      "sessions": [PARITY_SESSION], "projects": ["serveros"],
                      "views": ["run"]},
            "watches": [watch],
            "bounds": {"debounce_ticks": 1, "stale_after_seconds": 3600,
                       "max_warnings": 8},
        }), encoding="utf-8")
        return directory

    def _env(self, directory):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("FLEET_NEXT_AGENT_MONITOR_")}
        env["PYTHONPATH"] = os.pathsep.join([
            str(PACKAGE_ROOT), str(PACKAGE_ROOT / "qa"), str(MONITOR_ROOT),
            str(REPO / "integrations" / "fleet-v1-milestone-notify"), str(REPO)])
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["W19_JOURNAL"] = str(directory / "journal.jsonl")
        env["W19_LEDGER"] = str(directory / "ledger.sqlite")
        env["FLEET_NEXT_AGENT_MONITOR_CONFIG"] = str(directory / "config.json")
        env["FLEET_NEXT_AGENT_MONITOR_STATE"] = str(directory / "state.json")
        return env

    def _run(self, argv, directory, label):
        done = subprocess.run(argv, cwd=str(directory), env=self._env(directory),
                              capture_output=True, text=True, timeout=300)
        self.assertIn(done.returncode, (0, 3, 4),
                      f"{label} exited {done.returncode}: {done.stderr[-800:]}")
        self.assertTrue(done.stdout.strip(),
                        f"{label} printed no tick: {done.stderr[-800:]}")
        return json.loads(done.stdout)

    def _native(self, directory):
        """#493's own one-shot CLI, its own offline reader, its own core."""
        return self._run([sys.executable, "-m", "fleet_next_agent_monitor",
                          "--config", str(directory / "config.json"),
                          "--state", str(directory / "state.json"),
                          "--offline-facts", str(directory / "facts.json"),
                          "--notifier-entry", "parity_probe:notifier"],
                         directory, "native")

    def _compat(self, directory):
        """This adapter, the same core and the same notifier, own process."""
        return self._run([sys.executable, str(PARITY_PROBE), "compat",
                          str(directory)], directory, "compat")

    @staticmethod
    def _head(tick):
        watches = tick.get("watches") or [{}]
        return {"status": watches[0].get("status"),
                "reason": watches[0].get("reason"),
                "delivered": tick.get("delivered")}

    @staticmethod
    def _journal(directory):
        path = directory / "journal.jsonl"
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip())

    def _case(self, name):
        return next(case for case in PARITY_CASES if case["name"] == name)

    # -- the two claims ----------------------------------------------------
    def test_one_fact_gets_one_verdict_and_one_send_budget(self):
        for case in PARITY_CASES:
            with self.subTest(case=case["name"]):
                wanted = case["expected"]
                verifying = wanted[0] == STATUS_TERMINAL_VERIFIED

                compat_dir = self._scenario(case, "compat")
                compat = self._head(self._compat(compat_dir))
                self.assertEqual((compat["status"], compat["reason"]),
                                 (wanted[0], wanted[1]),
                                 "this adapter's path owes the documented verdict")
                self.assertEqual(self._journal(compat_dir), 1 if verifying else 0,
                                 "the compat path's send budget")

                native_dir = self._scenario(case, "native")
                native = self._head(self._native(native_dir))
                sent = self._journal(native_dir)

                if self.strict or not case.get("carrier"):
                    self.assertEqual((native["status"], native["reason"]),
                                     (compat["status"], compat["reason"]),
                                     "one standard of evidence means two paths "
                                     "cannot disagree about one fact")
                    self.assertEqual(sent, 1 if verifying else 0,
                                     "the native path's send budget")
                else:
                    # The monitor as it ships refuses the key before any
                    # classifier can fold it (#493's closed `RUN_KEYS`). The
                    # difference is pinned in the only safe direction: what it
                    # could not read, it did not verify and did not send.
                    self.assertEqual(native["status"], STATUS_UNKNOWN,
                                     f"an unread carrier must read UNKNOWN, not "
                                     f"{native['status']!r}")
                    self.assertTrue(str(native["reason"] or "").startswith("REMOTE_"),
                                    f"expected a named remote refusal, got "
                                    f"{native['reason']!r}")
                    self.assertEqual(sent, 0,
                                     "a monitor that could not read the fact must "
                                     "not have sent it")
                    if verifying:
                        # The paired half of the same claim: this direction is
                        # the one that is not allowed until the core folds.
                        self.assertNotEqual(compat["status"], STATUS_UNKNOWN,
                                            "the adapter must still verify a fact it "
                                            "can read, or the gate hides a regression")

    def test_a_verified_fact_is_not_sent_twice_across_the_two_paths(self):
        for name in ("message", "assistant_turn"):
            case = self._case(name)
            for order, first, second in (("compat_then_native", self._compat, self._native),
                                         ("native_then_compat", self._native, self._compat)):
                with self.subTest(case=name, order=order):
                    directory = self._scenario(case, f"once-{order}")
                    first(directory)
                    later = second(directory)
                    self.assertEqual(
                        self._journal(directory), 1,
                        f"{order}: one fact, two processes, one ledger — the second "
                        f"tick must find the receipt, not send again")
                    if self.strict or not case.get("carrier"):
                        # Both paths reached the same dispatchable fact, so the
                        # second one must have found the receipt rather than run
                        # out of things to say. Without this, "one send" would
                        # also be true of a process that never dispatched.
                        self.assertEqual(later.get("delivered"), 0,
                                         f"{order}: the second tick delivered again")
                        self.assertGreaterEqual(
                            int(later.get("duplicate") or 0), 1,
                            f"{order}: the second tick saw no receipt for the fact "
                            f"the first one delivered")


class TheAdapterDoesNotImportTheMonitorTest(unittest.TestCase):
    """The dependency runs one way, so a native→adapter cycle cannot form.

    The monitor may import this package (it publishes `normalise_run_fact` for
    exactly that). The reverse would close a cycle the moment the monitor also
    imported the adapter — and a cycle between the two owners' packages is a
    merge-order problem nobody can test.
    """

    def test_no_package_module_imports_the_monitor(self):
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        offenders = []
        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if name.split(".")[0] == "fleet_next_agent_monitor":
                        offenders.append(f"{path.relative_to(PACKAGE_ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [],
                         "the adapter must not import the monitor: publish the rule "
                         "and let the owner import it, or vendor it — never both ways")

    def test_the_probe_is_not_inside_the_package(self):
        package = PACKAGE_ROOT / "fleet_next_monitor_source_adapter"
        self.assertTrue(PARITY_PROBE.is_file(),
                        "the two-process probe must exist for the parity test")
        self.assertNotEqual(PARITY_PROBE.parent, package,
                            "the probe lives beside the package, so importing the "
                            "package cannot drag the monitor in through it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
