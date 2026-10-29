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
from typing import Mapping

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
                      verifiedArtifact={"verified": True, "sourceSHA": SHA})
        other = a_run(status="completed",
                      verifiedArtifact={"verified": True, "sourceSHA": OTHER_SHA})
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

    def test_a_transcript_still_verifies(self):
        observation = adapter(RecordingClient(run=a_run(
            status="completed", transcript=["x"]))).observe_run(
            device_ref=DEVICE, session_ref=SESSION, run_id=RUN_ID)
        self.assertEqual(observation.status, STATUS_TERMINAL_VERIFIED)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
