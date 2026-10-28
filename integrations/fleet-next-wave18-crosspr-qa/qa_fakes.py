"""Offline provider/source fakes for the Wave 16 composition.

Only the ends are fake. The monitor, the activation wrapper, #423's notifier,
the Batch CLI and #499's adapter are the real files from the composed tree.

The provider journals with ``O_APPEND`` + ``fsync`` so "three processes sent
once" and "the reconcile reused the same transport key" are facts about the
filesystem, not about one interpreter's objects.
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
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


class JournalProvider:
    """One provider whose memory is the journal the QA reads back."""

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
    """``module:callable`` for #493's ``--notifier-entry``."""
    import milestone_notify as notify

    return notify.MilestoneNotifier(
        JournalProvider(
            os.environ["QA_JOURNAL"],
            mode=os.environ.get("QA_PROVIDER_MODE", "deliver"),
            capability=os.environ.get("QA_PROVIDER_CAPABILITY", "SUPPORTED")),
        os.environ["QA_LEDGER"],
        conversation_ref=os.environ.get("QA_CONVERSATION", "chat:qa-wave16"))


def batch_provider(options=None):
    return JournalProvider(
        os.environ["QA_JOURNAL"],
        mode=os.environ.get("QA_PROVIDER_MODE", "deliver"))


def batch_source(options=None):
    """One cloudcli envelope for #481's Batch CLI."""
    run_id = os.environ.get("QA_RUN_ID", "run-qa-1")
    device = os.environ.get("QA_DEVICE", "device:tokyo-qa-1")
    session = os.environ.get("QA_SESSION", "device:tokyo-qa-1|claude|sess-qa")
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
