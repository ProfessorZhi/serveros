#!/usr/bin/env python3
"""#417 — the real SourceRead inputs: read-only CloudCLI run_get and GitHub Actions.

WHAT THIS FILE PINS
-------------------
#423 shipped the delivery half (``normalize`` -> ``decide`` -> provider) behind one
unwritten seam::

    class SourceRead(Protocol):
        def read(self, source, ref) -> Mapping[str, Any]: ...

This file pins the *other* half: two injectable adapters that turn an authorised
read into exactly that payload, and nothing else.

* The CloudCLI adapter reads ONE native ``run_get`` result that the owner's ACK
  already bound to a ``runId`` **and** a ``bootId``. A ``deliveryId`` is never
  assumed re-readable (#416: a queued send's deliveryId can answer
  ``RUN_NOT_FOUND`` while its ``runId`` reads fine), so a binding that carries
  only a deliveryId is refused instead of being guessed at.
* The GitHub adapter reads one already-finished workflow run plus its jobs, and
  derives gate evidence only from job conclusions that belong to the same
  ``head_sha``.
* Every refusal is a typed ``SourceRefused`` — never a payload, never a green.
  A timeout, a permission denial, an expired Run and a malformed sha all take
  that shape, so the observer settles them as UNKNOWN by construction.

DATA TRIMMING is a test, not a promise: the prompt, the transcript and
token-shaped strings in the fake upstream are walked for after every read.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. HOSTED_CI is the PR's workflow. NOT_LIVE: no device
is called, no OAuth grant is exercised, no GitHub token exists, no socket is
opened, and no Feishu message is sent. ``FakeRunRead``/``FakeActionsRead`` stand
in for the authorised transports the production caller must inject.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOTIFY_PATH = (REPO / "integrations" / "fleet-v1-milestone-notify"
               / "milestone_notify.py")
SOURCES_PATH = (REPO / "integrations" / "fleet-v1-milestone-sources"
                / "milestone_sources.py")

DEVICE = "device:tokyo-shared-dev-01"
SESSION_REF = "session:codex:676632e0-630a-4ad3-8fab-6e977aa19e9a"
NATIVE_SESSION = "676632e0-630a-4ad3-8fab-6e977aa19e9a"
RUN_ID = "a1b1c184-ea82-4079-8da9-a96bd3568cc3"
BOOT_ID = "a6d0f308-81df-4edb-8811-66400b51870e"
SHA = "dd5919a9256246570e611a7fa86aea28b46ef14d"

#: Free text a real device would carry. Every one of these must be absent from
#: what the adapter returns.
PROMPT = "SYSTEM: you are a helpful agent. PROMPT-SENTINEL-9f3"
TRANSCRIPT = "user said something private TRANSCRIPT-SENTINEL-1a7"
TOKEN = "ghp_AAAAbbbbCCCCddddEEEEffffGGGG1234"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


notify = _load("serveros_fleet_v1_milestone_notify", NOTIFY_PATH)
m = _load("serveros_fleet_v1_milestone_sources", SOURCES_PATH)


class ProviderError(Exception):
    """A provider refusal shaped like ``fleet_mcp.errors.FleetRefusal``."""

    def __init__(self, code, detail="provider detail that must not be copied"):
        super().__init__(detail)
        self.code = code


class FakeRunRead:
    """The authorised CloudCLI read, with no device, no socket and no token."""

    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def __call__(self, *, run_id, timeout_seconds):
        self.calls.append({"run_id": run_id, "timeout_seconds": timeout_seconds})
        if self.error is not None:
            raise self.error
        return copy.deepcopy(self.payload)


class FakeActionsRead:
    """The authorised GitHub Actions read. Fake statuses only."""

    def __init__(self, run=None, jobs=None, run_error=None, jobs_error=None):
        self.run = run
        self.jobs = jobs if jobs is not None else []
        self.run_error = run_error
        self.jobs_error = jobs_error
        self.run_calls = []
        self.jobs_calls = []

    def __call__(self, *, owner, repo, run_id, timeout_seconds):
        self.run_calls.append({"owner": owner, "repo": repo, "run_id": run_id,
                               "timeout_seconds": timeout_seconds})
        if self.run_error is not None:
            raise self.run_error
        return copy.deepcopy(self.run)


class FakeJobsRead:
    def __init__(self, owner_read):
        self.owner_read = owner_read

    def __call__(self, *, owner, repo, run_id, timeout_seconds):
        own = self.owner_read
        own.jobs_calls.append({"owner": owner, "repo": repo, "run_id": run_id,
                               "timeout_seconds": timeout_seconds})
        if own.jobs_error is not None:
            raise own.jobs_error
        return copy.deepcopy(own.jobs)


class Clock:
    """A wall clock and a monotonic reader that can be advanced per call."""

    def __init__(self, now=1793000000.0, step=0.0):
        self.now = now
        self.step = step
        self.reads = 0

    def wall(self):
        return self.now

    def monotonic(self):
        value = self.reads * self.step
        self.reads += 1
        return value


def cloudcli_run(**overrides):
    run = {
        "idKind": "run",
        "runId": RUN_ID,
        "deliveryId": None,
        "sessionId": NATIVE_SESSION,
        "source": "mcp",
        "status": "completed",
        "bootId": BOOT_ID,
        "note": None,
        "prompt": PROMPT,
        "transcript": [{"role": "user", "content": TRANSCRIPT}],
        "token": TOKEN,
        "lastAssistantMessage": {
            "id": "45dee66c-1e85-45ac-96b9-ec35b1a74af7_0",
            "sessionId": NATIVE_SESSION,
            "provider": "claude",
            "kind": "text",
            "role": "assistant",
            "content": "COMPAT-OK",
        },
    }
    run.update(overrides)
    return run


def workflow_run(**overrides):
    run = {
        "id": 40479463067,
        "name": "Tests",
        "head_branch": "fix/ci-lark-card-window-20261027",
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "event": "pull_request",
    }
    run.update(overrides)
    return run


def jobs(*pairs):
    return {"total_count": len(pairs),
            "jobs": [{"id": 900 + index, "name": name, "status": "completed",
                      "conclusion": conclusion, "head_sha": SHA,
                      "steps": [{"name": "run", "conclusion": conclusion}]}
                     for index, (name, conclusion) in enumerate(pairs)]}


def walk(value, path="payload"):
    """Every key and string leaf, so a leak cannot hide in a nested object."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append((f"{path}.{key}", key))
            found.extend(walk(item, f"{path}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(walk(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        found.append((path, value))
    return found


class CloudCliSourceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def source(self, payload=None, *, policy=None, error=None, binding=None,
               timeout_seconds=5.0, clock=None):
        read = FakeRunRead(payload, error)
        bound = binding if binding is not None else m.RunBinding(
            device_ref=DEVICE, session_ref=SESSION_REF, run_id=RUN_ID,
            bound_boot_id=BOOT_ID)
        ticks = clock or self.clock
        return m.CloudCliRunSource(
            read, {RUN_ID: bound},
            policy=policy or m.MilestonePolicy(
                source_trusted=True, milestone=True,
                gates_required=("native-output", "boot-identity")),
            clock=ticks.wall, monotonic=ticks.monotonic,
            timeout_seconds=timeout_seconds), read

    def test_cloudcli_source_satisfies_the_published_sourceread_protocol(self):
        source, _ = self.source(cloudcli_run())
        self.assertIsInstance(source, notify.SourceRead)

    def test_cloudcli_green_milestone_is_verified_green_run_completed(self):
        source, _ = self.source(cloudcli_run())
        payload = source.read("cloudcli", RUN_ID)
        decision = notify.decide(notify.normalize("cloudcli", payload))
        self.assertEqual(decision.outcome, notify.VERIFIED_GREEN)
        self.assertEqual(decision.milestone_kind, notify.RUN_COMPLETED)

    def test_cloudcli_payload_uses_only_the_closed_envelope(self):
        source, _ = self.source(cloudcli_run())
        payload = source.read("cloudcli", RUN_ID)
        self.assertEqual(payload["device_ref"], DEVICE)
        self.assertEqual(payload["bound_boot_id"], BOOT_ID)
        self.assertEqual(payload["run"]["runId"], RUN_ID)
        self.assertEqual(payload["run"]["bootId"], BOOT_ID)

    def test_cloudcli_completed_without_native_output_is_unknown(self):
        run = cloudcli_run(lastAssistantMessage=None,
                           note="This run has ended, but the session has no "
                                "readable assistant message.")
        source, _ = self.source(run)
        payload = source.read("cloudcli", RUN_ID)
        decision = notify.decide(notify.normalize("cloudcli", payload))
        self.assertEqual(decision.outcome, notify.UNKNOWN)
        self.assertNotEqual(decision.outcome, notify.VERIFIED_GREEN)

    def test_cloudcli_boot_mismatch_is_unknown(self):
        source, _ = self.source(cloudcli_run(bootId="00000000-dead-beef-0000-000000000000"))
        payload = source.read("cloudcli", RUN_ID)
        decision = notify.decide(notify.normalize("cloudcli", payload))
        self.assertEqual(decision.outcome, notify.UNKNOWN)
        self.assertEqual(decision.why, "RUN_COMPLETED_WITHOUT_TRUSTED_BOOT")

    def test_cloudcli_run_not_found_after_expiry_is_a_refusal(self):
        source, _ = self.source(error=ProviderError("RUN_NOT_FOUND"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, "RUN_NOT_FOUND")
        self.assertEqual(ctx.exception.source, "cloudcli")

    def test_cloudcli_permission_refusal_is_a_refusal(self):
        source, _ = self.source(error=ProviderError("PERMISSION_DENIED",
                                                    detail=TOKEN))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, m.PERMISSION_REFUSED)
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_cloudcli_unmapped_provider_code_falls_back_to_provider_refused(self):
        source, _ = self.source(error=ProviderError("RUN_COMPLETED"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, m.PROVIDER_REFUSED)

    def test_cloudcli_timeout_is_a_refusal(self):
        clock = Clock(step=10.0)
        source, read = self.source(cloudcli_run(), clock=clock, timeout_seconds=5.0)
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, m.SOURCE_TIMEOUT)
        self.assertEqual(len(read.calls), 1)

    def test_cloudcli_reader_is_told_the_run_id_and_the_timeout(self):
        source, read = self.source(cloudcli_run(), timeout_seconds=2.5)
        source.read("cloudcli", RUN_ID)
        self.assertEqual(read.calls, [{"run_id": RUN_ID, "timeout_seconds": 2.5}])

    def test_cloudcli_read_does_not_copy_prompt_transcript_or_token(self):
        source, _ = self.source(cloudcli_run())
        payload = source.read("cloudcli", RUN_ID)
        blob = json.dumps(payload, ensure_ascii=False)
        for secret in (PROMPT, TRANSCRIPT, TOKEN, "PROMPT-SENTINEL-9f3",
                       "TRANSCRIPT-SENTINEL-1a7"):
            self.assertNotIn(secret, blob)
        keys = {key.lower() for _, key in walk(payload)}
        for banned in ("prompt", "transcript", "token", "authorization",
                       "credential", "app_secret"):
            self.assertNotIn(banned, keys)
        # `lastAssistantMessage.content` must survive as a PRESENCE MARKER only:
        # #423's normalizer reads exactly that field to decide whether the device
        # produced a readable result, so it is not a field this adapter may drop.
        self.assertEqual(payload["run"]["lastAssistantMessage"],
                         {"content": m.PRESENT})

    def test_cloudcli_read_is_idempotent_and_leaves_the_source_untouched(self):
        run = cloudcli_run()
        before = copy.deepcopy(run)
        source, read = self.source(run)
        first = source.read("cloudcli", RUN_ID)
        second = source.read("cloudcli", RUN_ID)
        self.assertEqual(first, second)
        self.assertEqual(run, before)
        self.assertEqual(len(read.calls), 2)

    def test_cloudcli_binding_with_only_a_delivery_id_is_refused(self):
        with self.assertRaises(m.SourceRefused) as ctx:
            m.RunBinding(device_ref=DEVICE, session_ref=SESSION_REF, run_id=None)
        self.assertEqual(ctx.exception.code, m.RUN_NOT_ACKNOWLEDGED)

    def test_cloudcli_unacknowledged_ref_is_refused_without_a_read(self):
        source, read = self.source(cloudcli_run())
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", "not-an-acknowledged-run")
        self.assertEqual(ctx.exception.code, m.UNKNOWN_RUN)
        self.assertEqual(read.calls, [])

    def test_cloudcli_answer_for_another_session_is_refused(self):
        source, _ = self.source(cloudcli_run(sessionId="11111111-2222-3333-4444-555555555555"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, m.SESSION_MISMATCH)

    def test_cloudcli_answer_for_another_run_is_refused(self):
        source, _ = self.source(cloudcli_run(runId="99999999-8888-7777-6666-555555555555"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("cloudcli", RUN_ID)
        self.assertEqual(ctx.exception.code, m.RUN_IDENTITY_MISMATCH)

    def test_cloudcli_wrong_source_name_is_refused(self):
        source, _ = self.source(cloudcli_run())
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("github_actions", RUN_ID)
        self.assertEqual(ctx.exception.code, m.BAD_SOURCE)


class GitHubActionsSourceTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()

    def source(self, run=None, *, jobs_read=None, policy=None, run_error=None):
        read = FakeActionsRead(run, run_error=run_error)
        reader = FakeJobsRead(read) if jobs_read is not None else None
        if jobs_read is not None:
            read.jobs = jobs_read
        return m.GitHubActionsSource(
            read, device_ref=DEVICE,
            policy=policy or m.MilestonePolicy(
                source_trusted=True, milestone=True,
                gates_required=("plugin", "shard-1")),
            jobs_reader=reader, clock=self.clock.wall,
            monotonic=self.clock.monotonic), read

    def ref(self):
        return f"ProfessorZhi/serveros#{40479463067}"

    def test_github_green_without_job_evidence_is_unknown(self):
        source, _ = self.source(workflow_run())
        decision = notify.decide(notify.normalize(
            "github_actions", source.read("github_actions", self.ref())))
        self.assertEqual(decision.outcome, notify.UNKNOWN)
        self.assertEqual(decision.why, "REQUIRED_GATES_NOT_EVIDENCED")

    def test_github_green_with_green_jobs_is_verified_ci_passed(self):
        source, _ = self.source(workflow_run(),
                                jobs_read=jobs(("plugin", "success"),
                                               ("shard-1", "success")))
        payload = source.read("github_actions", self.ref())
        self.assertEqual(payload["gates_passed"], ["plugin", "shard-1"])
        decision = notify.decide(notify.normalize("github_actions", payload))
        self.assertEqual(decision.outcome, notify.VERIFIED_GREEN)
        self.assertEqual(decision.milestone_kind, notify.CI_PASSED)

    def test_github_failed_is_red(self):
        source, _ = self.source(workflow_run(conclusion="failure"))
        decision = notify.decide(notify.normalize(
            "github_actions", source.read("github_actions", self.ref())))
        self.assertEqual(decision.outcome, notify.RED)

    def test_github_action_required_is_red_and_flagged(self):
        source, _ = self.source(workflow_run(conclusion="action_required"))
        decision = notify.decide(notify.normalize(
            "github_actions", source.read("github_actions", self.ref())))
        self.assertEqual(decision.outcome, notify.RED)
        self.assertEqual(decision.why, "ACTION_REQUIRED")

    def test_github_running_workflow_is_silent_not_green(self):
        source, _ = self.source(workflow_run(status="in_progress", conclusion=None))
        decision = notify.decide(notify.normalize(
            "github_actions", source.read("github_actions", self.ref())))
        self.assertEqual(decision.outcome, notify.SILENT)

    def test_github_malformed_sha_is_refused(self):
        source, _ = self.source(workflow_run(head_sha="dd5919a9"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("github_actions", self.ref())
        self.assertEqual(ctx.exception.code, m.BAD_SHA)

    def test_github_job_from_another_sha_is_refused(self):
        drifted = jobs(("plugin", "success"))
        drifted["jobs"][0]["head_sha"] = "0" * 40
        source, _ = self.source(workflow_run(), jobs_read=drifted)
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("github_actions", self.ref())
        self.assertEqual(ctx.exception.code, m.SHA_MISMATCH)

    def test_github_ref_must_name_one_repository_and_one_run(self):
        source, _ = self.source(workflow_run())
        for bad in ("ProfessorZhi/serveros", "ProfessorZhi/serveros#",
                    "../serveros#1", "ProfessorZhi serveros#1",
                    "ProfessorZhi/serveros#abc", "ProfessorZhi/serveros#-1"):
            with self.subTest(ref=bad):
                with self.assertRaises(m.SourceRefused) as ctx:
                    source.read("github_actions", bad)
                self.assertEqual(ctx.exception.code, m.BAD_REF)

    def test_github_refusal_propagates_and_never_produces_a_payload(self):
        source, _ = self.source(run_error=ProviderError("PERMISSION_DENIED"))
        with self.assertRaises(m.SourceRefused) as ctx:
            source.read("github_actions", self.ref())
        self.assertEqual(ctx.exception.code, m.PERMISSION_REFUSED)

    def test_github_payload_drops_fields_the_boundary_does_not_read(self):
        source, _ = self.source(workflow_run())
        payload = source.read("github_actions", self.ref())
        self.assertEqual(set(payload["workflow_run"]),
                         {"id", "name", "status", "conclusion", "head_sha"})
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(TOKEN, blob)


class DuplicateObservationTest(unittest.TestCase):
    """Two observations of one finished Run reach the provider once."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serveros-milestone-sources-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = Path(self.tmp) / "milestones.sqlite3"

    def test_duplicate_observation_delivers_once_and_reports_duplicate(self):
        provider = FakeLark()
        ticks = Clock()
        source = m.CloudCliRunSource(
            FakeRunRead(cloudcli_run()),
            {RUN_ID: m.RunBinding(device_ref=DEVICE, session_ref=SESSION_REF,
                                  run_id=RUN_ID, bound_boot_id=BOOT_ID)},
            policy=m.MilestonePolicy(source_trusted=True, milestone=True,
                                     gates_required=("native-output",
                                                     "boot-identity")),
            clock=ticks.wall, monotonic=ticks.monotonic)

        def notifier(provider_used):
            return notify.MilestoneNotifier(
                provider_used, self.ledger, conversation_ref="user:ou_1",
                clock=lambda: datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc))

        first = notifier(provider).observe_payload(
            "cloudcli", source.read("cloudcli", RUN_ID))
        second = notifier(provider).observe_payload(
            "cloudcli", source.read("cloudcli", RUN_ID))

        self.assertEqual(first.outcome, notify.VERIFIED_GREEN)
        self.assertTrue(first.delivered)
        self.assertEqual(second.state, notify.DUPLICATE)
        self.assertIsNone(second.message, "a replay must not compose a new sentence")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(second.delivery_key, first.delivery_key)

        # A restart with a fresh provider and the same durable ledger: still no
        # second send, because the fact is the same fact.
        restarted = FakeLark()
        replay = notifier(restarted).observe_payload(
            "cloudcli", source.read("cloudcli", RUN_ID))
        self.assertEqual(replay.state, notify.DUPLICATE)
        self.assertEqual(len(restarted.calls), 0)


class FakeLark:
    """The outbound provider, with no CLI, no credential and no network."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self):
        self.calls = []

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {"human.notification.transport":
                                 {"state": "SUPPORTED"}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        return {"delivered": True, "remote_id": "om_1",
                "provider": self.provider_id, "transport": self.transport}


class ReadOnlySurfaceTest(unittest.TestCase):
    """The adapter has no socket, no filesystem and no credential surface."""

    BANNED_IMPORTS = frozenset({
        "os", "socket", "ssl", "subprocess", "urllib", "urllib.request",
        "http", "httplib", "requests", "aiohttp", "ftplib", "smtplib",
        "telnetlib", "pathlib", "shutil", "tempfile",
    })

    def test_module_imports_nothing_that_can_open_a_socket_or_a_file(self):
        tree = ast.parse(SOURCES_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module)
                    imported.update(f"{node.module}.{alias.name}"
                                    for alias in node.names)
        self.assertEqual(imported & self.BANNED_IMPORTS, set())

    def test_module_never_reads_the_environment_or_a_file(self):
        tree = ast.parse(SOURCES_PATH.read_text(encoding="utf-8"))
        names = {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)}
        self.assertEqual(names & {"environ", "getenv", "getenvb"}, set())
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertEqual(called & {"open", "eval", "exec"}, set())


if __name__ == "__main__":
    unittest.main()
