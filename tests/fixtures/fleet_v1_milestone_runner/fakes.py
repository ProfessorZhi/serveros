"""Offline factories for tests/test_fleet_v1_milestone_runner.py.

This module is a TEST FIXTURE, not a production adapter. It is imported by the
runner subprocess through ``PYTHONPATH`` so the process entry exercises its real
config/lock/observer code with a scripted source and a provider that writes a
local JSONL log. No network, CLI, credential, service or real Feishu send.
"""
from __future__ import annotations

import json
from pathlib import Path

CAPABILITY = "human.notification.transport"


class FakeRefused(Exception):
    """The #428 observer reads ``code``/``detail`` from a source refusal."""

    def __init__(self, code: str):
        self.code = code
        self.detail = code
        super().__init__(code)


class FakeProvider:
    provider_id = "provider:fake-runner"
    transport = "fake"

    def __init__(self, log_path):
        self.log_path = Path(log_path)
        self.calls = 0

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": "SUPPORTED"}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        self.calls += 1
        record = {"conversation_ref": conversation_ref, "text": text,
                  "idempotency_key": idempotency_key}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        return {"delivered": True, "remote_id": f"om_{self.calls}",
                "provider": self.provider_id, "transport": self.transport}


def provider(options):
    return FakeProvider(options["log"])


class FakeSource:
    """A scripted SourceRead: payload names are fixtures; ``raise`` is a code.

    The payload's Run id is replaced with the watch ref, so two watches in one
    tick are two real milestones rather than the same fixture twice.
    """

    def __init__(self, sequence, fixtures):
        self.sequence = list(sequence)
        self.fixtures = Path(fixtures)
        self.index = 0

    def read(self, source, ref):
        item = self.sequence[min(self.index, len(self.sequence) - 1)]
        self.index += 1
        if "raise" in item:
            raise FakeRefused(item["raise"])
        name = item["payload"]
        payload = json.loads(
            (self.fixtures / "cloudcli" / f"{name}.json").read_text(encoding="utf-8"))
        if source == "cloudcli":
            payload["run"] = {**payload["run"], "runId": ref}
        return payload


def source(options):
    return FakeSource(options["sequence"], options["fixtures"])


def summarizer(options):
    if options.get("raise"):
        def unavailable(_facts):
            raise RuntimeError("fake summarizer unavailable")
        return unavailable

    def brief(_facts):
        return str(options.get("text") or "brief")
    return brief
