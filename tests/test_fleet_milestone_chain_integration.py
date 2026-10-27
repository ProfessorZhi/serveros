#!/usr/bin/env python3
"""The three milestone modules composed the way production would compose them.

WHY THIS FILE EXISTS
--------------------
Each piece has its own tests: #423 the delivery ledger, #426 the source adapters,
#428 the observer tick. Each of those tests fakes the pieces around it. Nothing
so far proves that **#426's adapter output is what #423's normalizer accepts** and
that **#428's observer drives the pair with its real collaborators rather than
scripted stand-ins** — which is exactly the seam where three PRs that are each
green can still not fit together.

So every test here wires the real modules together:

    CloudCliRunSource.read  ->  MilestoneObserver.run_once  ->  MilestoneNotifier
            (#426)                      (#428)                     (#423)
                                                                      |
                                                              FakeLark (#423's
                                                              injected provider)

Only the provider stands in, and it is injected — the modules never construct
one. No network, no CLI, no credential, no Feishu: the point is the seam, and a
socket would make the test prove something else.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. HOSTED_CI is the RC PR's workflow. REAL_DEVICE,
CLIENT_E2E and REAL_FEISHU_DELIVERY are NOT_RUN and are not claimed here: a
green run proves the modules compose, not that a person received a message.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

NOTIFY = REPO / "integrations" / "fleet-v1-milestone-notify" / "milestone_notify.py"
SOURCES = REPO / "integrations" / "fleet-v1-milestone-sources" / "milestone_sources.py"
OBSERVER = REPO / "integrations" / "fleet-v1-milestone-observer" / "observer.py"

CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"
DEVICE = "device:dell-fjd"
SESSION = "device:dell-fjd|claude-code|sess_01JCEPTEST0000000000000000"
RUN_ID = "run_01JCEPTEST0000000000000000"
BOOT = "boot-A"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class FakeLark:
    """The one outbound provider, standing in. No CLI, no socket, no token."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None, capability: str = "SUPPORTED"):
        self.receipts = list(receipts or [])
        self.capability = capability
        self.calls = []

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

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


class Clock:
    def __init__(self, at: float = 1_761_566_400.0):
        self.at = float(at)

    def __call__(self) -> float:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += float(seconds)


def raw_run(*, status: str = "completed", with_output: bool = True,
            boot: str = BOOT, run_id: str = RUN_ID) -> dict:
    """A `run_get` answer as the device states it."""
    return {
        "idKind": "run",
        "runId": run_id,
        "sessionId": "sess_01JCEPTEST0000000000000000",
        "status": status,
        "bootId": boot,
        "lastAssistantMessage": ({"content": "the task finished"} if with_output
                                 else None),
    }


def cloudcli_adapter(sources, *, clock, answers=None):
    """#426's real adapter over a scripted reader. One answer per read."""
    answers = list(answers or [raw_run()])
    binding = sources.RunBinding(device_ref=DEVICE, session_ref=SESSION,
                                 run_id=RUN_ID, bound_boot_id=BOOT)
    calls = []

    def read_run(*, run_id, timeout_seconds):
        calls.append(run_id)
        value = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(value, BaseException):
            raise value
        return json.loads(json.dumps(value))

    adapter = sources.CloudCliRunSource(
        read_run, {RUN_ID: binding},
        policy=sources.MilestonePolicy(
            source_trusted=True, milestone=True,
            gates_required=("native-output", "boot-identity"),
            summary="the requested task finished"),
        clock=clock, monotonic=lambda: float(clock()))
    adapter.read_calls = calls
    return adapter


def observer_for(observer_mod, *, source, provider, ledger, state, clock):
    """#428's real observer with #423's real notifier and an injected provider."""
    notifier = observer_mod.NOTIFY.MilestoneNotifier(
        provider, ledger, conversation_ref=RECIPIENT, clock=clock)
    watch = observer_mod.Watch(
        source="cloudcli", ref=RUN_ID, device_ref=DEVICE, boot_id=BOOT,
        expires_at="2026-10-29T10:00:00Z")
    return observer_mod.MilestoneObserver(
        source=source, notifier=notifier, state_path=state, watches=[watch],
        clock=clock, options=observer_mod.Options())


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-chain-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clock = Clock()
        self.provider = FakeLark()
        self.sources = _load("serveros_chain_sources", SOURCES)
        self.observer = _load("serveros_chain_observer", OBSERVER)
        self.notify = self.observer.NOTIFY

    def cloudcli_adapter(self, *, answers=None):
        return cloudcli_adapter(self.sources, clock=self.clock, answers=answers)

    def observer_for(self, source, *, ledger=None, state=None):
        return observer_for(
            self.observer, source=source, provider=self.provider,
            ledger=ledger or self.tmp / "ledger.sqlite",
            state=state or self.tmp / "observer.json", clock=self.clock)


class TheEnvelopesFit(unittest.TestCase):
    """The seam no single PR could prove: what one module emits, the next reads."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-chain-env-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.clock = Clock()
        self.sources = _load("serveros_chain_sources_env", SOURCES)
        self.notify = _load("serveros_chain_notify_env", NOTIFY)
        self.observer = _load("serveros_chain_observer_env", OBSERVER)

    def test_the_cloudcli_adapter_emits_exactly_the_normalizer_envelope(self) -> None:
        adapter = cloudcli_adapter(self.sources, clock=self.clock)
        payload = adapter.read("cloudcli", RUN_ID)
        self.assertEqual(set(payload), set(self.notify._CLOUDCLI_ENVELOPE),
                         "#426 emits a different key set than #423 closes over; "
                         "the strict envelope would refuse every real read")

    def test_the_cloudcli_adapter_output_is_accepted_by_the_normalizer(self) -> None:
        payload = cloudcli_adapter(self.sources, clock=self.clock).read(
            "cloudcli", RUN_ID)
        evidence = self.notify.normalize("cloudcli", payload)
        self.assertEqual(evidence.source, "cloudcli")
        self.assertTrue(evidence.device_ref)

    def test_the_github_adapter_emits_exactly_the_normalizer_envelope(self) -> None:
        run = {"id": 40486346485, "name": "Tests",
               "head_sha": "3e9428ab5f057b5ac6a1da1cebda7bf05c47901c",
               "status": "completed", "conclusion": "success",
               "event": "pull_request"}
        adapter = self.sources.GitHubActionsSource(
            lambda *, owner, repo, run_id, timeout_seconds: run,
            device_ref="device:ci-ProfessorZhi-serveros",
            policy=self.sources.MilestonePolicy(
                source_trusted=True, milestone=True),
            clock=self.clock, monotonic=lambda: float(self.clock()))
        payload = adapter.read("github_actions", "ProfessorZhi/serveros#40486346485")
        self.assertEqual(set(payload), set(self.notify._GITHUB_ENVELOPE),
                         "#426 emits a different key set than #423 closes over")


class TheChainDeliversAfterTheChatIsGone(Base):
    """GPT dispatch returns; the run turns terminal later; only the observer acts."""

    def test_one_terminal_run_produces_exactly_one_message(self) -> None:
        # The dispatch is acknowledged while the run is still going. Nothing has
        # been sent at this point — that is the whole premise of an out-of-band
        # observer.
        adapter = self.cloudcli_adapter(
            answers=[raw_run(status="running", with_output=False),
                     raw_run(status="completed", with_output=True)])
        observer = self.observer_for(adapter)

        first = observer.run_once()
        self.assertEqual(self.provider.calls, [],
                         "a running run must not produce a message")
        self.assertTrue(first["rows"])

        self.clock.advance(120)
        observer.run_once()

        self.assertEqual(len(self.provider.calls), 1,
                         "one terminal run is one message")
        key = self.provider.calls[0]["idempotency_key"]
        self.assertTrue(key)

    def test_a_restart_after_the_send_does_not_send_again(self) -> None:
        ledger = self.tmp / "ledger.sqlite"
        adapter = self.cloudcli_adapter()
        first = self.observer_for(adapter, ledger=ledger)
        first.run_once()
        self.assertEqual(len(self.provider.calls), 1)

        # A new process: a fresh observer state file, the same ledger. The
        # ledger is what makes replay safe; if the state file were the only
        # memory, this would resend.
        self.clock.advance(600)
        second = self.observer_for(self.cloudcli_adapter(), ledger=ledger,
                                   state=self.tmp / "observer-after-restart.json")
        second.run_once()
        self.assertEqual(len(self.provider.calls), 1,
                         "a restart re-observed the same milestone and resent it")

    def test_losing_the_observer_state_does_not_resend(self) -> None:
        ledger = self.tmp / "ledger.sqlite"
        self.observer_for(self.cloudcli_adapter(), ledger=ledger).run_once()
        self.assertEqual(len(self.provider.calls), 1)

        self.clock.advance(60)
        self.observer_for(self.cloudcli_adapter(), ledger=ledger,
                          state=self.tmp / "gone.json").run_once()
        self.assertEqual(len(self.provider.calls), 1)


class TheChainStaysOffline(Base):
    """The composition must not acquire an exit no test injected."""

    def test_no_module_opens_a_network_client(self) -> None:
        import ast
        forbidden = {"socket", "ssl", "http", "urllib", "requests", "httpx",
                     "aiohttp", "websocket", "lark_oapi", "subprocess"}
        offenders = {}
        for path in (NOTIFY, SOURCES, OBSERVER):
            found = set()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    found.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    found.add(node.module.split(".")[0])
            hit = sorted(found & forbidden)
            if hit:
                offenders[path.name] = hit
        self.assertEqual(offenders, {},
                         "the chain must reach the outside world only through "
                         "the injected provider")

    def test_the_message_carries_no_transcript_or_credential(self) -> None:
        adapter = self.cloudcli_adapter()
        self.observer_for(adapter).run_once()
        self.assertEqual(len(self.provider.calls), 1)
        text = self.provider.calls[0]["text"]
        # The run's assistant content is `PRESENT` at the adapter boundary; the
        # words themselves never leave the device.
        self.assertNotIn("the task finished", text)


if __name__ == "__main__":
    unittest.main()
