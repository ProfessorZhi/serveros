"""Offline fakes and the one notifier factory the composed run injects.

Only the *ends* are fake: a provider that writes a JSONL journal and a source
that answers from a JSON document. The monitor, the activation wrapper, the
Batch CLI and #423's notifier are the real modules from the pinned commits.

The journal is a file, not a list, because the question "did three separate
processes send once or three times?" cannot be answered from one interpreter's
object graph. Every line is appended with ``O_APPEND`` and fsynced.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

CAPABILITY = "human.notification.transport"


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(handle, "a", encoding="utf-8") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


class JournalProvider:
    """A provider whose only memory is the file the test can read back.

    ``mode`` is the delivery answer: ``deliver`` (a remote id), ``unknown`` (no
    receipt), ``raise`` (the provider itself fails) or ``unsupported`` (the
    capability table says the transport cannot carry a message).
    """

    provider_id = "provider:qa-journal"
    transport = "fake"

    def __init__(self, journal: str | Path, *, mode: str = "deliver",
                 capability: str = "SUPPORTED") -> None:
        self.journal = Path(journal)
        self.mode = mode
        self.capability = capability
        self.calls: list[dict] = []

    def capabilities(self) -> dict:
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict:
        record = {"conversation_ref": conversation_ref, "text": text,
                  "idempotency_key": idempotency_key, "mode": self.mode,
                  "remote_id": None}
        if self.mode == "deliver":
            record["remote_id"] = f"om_qa_{_line_count(self.journal) + 1}"
        _append(self.journal, record)
        self.calls.append(record)
        if self.mode == "raise":
            raise RuntimeError("qa fake provider: simulated send failure")
        if self.mode == "unknown":
            return {"delivered": False, "why": "qa fake provider: no receipt"}
        return {"delivered": True, "remote_id": record["remote_id"]}


def notifier_factory(config=None):
    """``module:callable`` for #493's ``--notifier-entry``.

    The object returned is #423's real ``MilestoneNotifier``; only its provider
    and ledger path come from the environment, because a zero-argument tick has
    nowhere else to read them.
    """
    import milestone_notify as notify  # #423's pinned module

    journal = os.environ["QA_JOURNAL"]
    ledger = os.environ["QA_LEDGER"]
    mode = os.environ.get("QA_PROVIDER_MODE", "deliver")
    capability = os.environ.get("QA_PROVIDER_CAPABILITY", "SUPPORTED")
    conversation = os.environ.get("QA_CONVERSATION", "chat:qa-crosspr")
    return notify.MilestoneNotifier(
        JournalProvider(journal, mode=mode, capability=capability),
        ledger, conversation_ref=conversation)


def batch_provider(options=None):
    """``--provider-factory`` for #481's Batch CLI."""
    journal = os.environ["QA_JOURNAL"]
    mode = os.environ.get("QA_PROVIDER_MODE", "deliver")
    return JournalProvider(journal, mode=mode)


def batch_source(options=None):
    """``--source-factory`` for #481's Batch CLI: one cloudcli envelope.

    The envelope is #423's closed cloudcli shape. It carries a transcript-shaped
    ``lastAssistantMessage`` exactly as the monitor's own ``cloudcli_payload``
    does, so the two entrypoints can be compared on the same fact.
    """
    run_id = os.environ.get("QA_RUN_ID", "run-qa-1")
    device = os.environ.get("QA_DEVICE", "device:tokyo-qa-1")
    session = os.environ.get(
        "QA_SESSION", "device:tokyo-qa-1|claude|sess-qa")
    boot = os.environ.get("QA_BOOT", "boot-qa-1")

    class _Source:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def read(self, source: str, ref: str) -> dict:
            self.calls.append(f"{source}:{ref}")
            return copy.deepcopy({
                "device_ref": device,
                "session_ref": session,
                "observed_at": "2026-10-28T09:00:00Z",
                "bound_boot_id": boot,
                "source_trusted": True,
                "milestone": True,
                "gates_required": ["native-output", "boot-identity"],
                "gates_passed": ["native-output", "boot-identity"],
                "summary": None,
                "refs": [f"run:{run_id}"],
                "run": {
                    "runId": run_id,
                    "status": "completed",
                    "sessionId": session,
                    "bootId": boot,
                    "lastAssistantMessage": {"content": "native output"},
                },
            })

    return _Source()


def probe_true() -> bool:
    return True


def probe_false() -> bool:
    return False
