#!/usr/bin/env python3
"""The two factories the Batch CLI tests name as `module:callable`.

Nothing here is product code. It is what a *test* passes to `--provider-factory`
and `--source-factory`, which is the same authority a real deployment passes:
one spec string, validated by `#446`'s own `load_factory`, and called once with
the options object the operator wrote into a file.

Why a module rather than a closure
----------------------------------
The CLI's idempotency claim is about **two independent processes**, and a
closure only exists in the process that made it. `FileLark` therefore keeps its
memory in a journal file: process 2 and process 3 read the same file, so "the
ledger stopped the second send" can be asserted as a fact about the provider
rather than as a fact about one interpreter's object graph.

Nothing here reads a credential, opens a socket, or spawns a model. The
transport is the test's own directory.
"""
from __future__ import annotations

import json
from pathlib import Path

#: The capability name #423's probe asks for (`runtime/notifications.py`).
CAPABILITY = "human.notification.transport"


class SourceRefused(Exception):
    """The refusal shape `#428` maps to a REFUSED row (`exc.code`)."""

    def __init__(self, code: str):
        self.code = code
        self.detail = code
        super().__init__(code)


class FileLark:
    """One provider whose memory is a journal file, so N processes share it."""

    provider_id = "provider:fake-lark-file"
    transport = "fake"

    def __init__(self, journal, receipts=(), capability: str = "SUPPORTED",
                 closed=None):
        self.journal = Path(journal)
        self.receipts = list(receipts)
        self.capability = capability
        self.closed = Path(closed) if closed else None

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

    def _count(self) -> int:
        if not self.journal.exists():
            return 0
        return sum(1 for line in self.journal.read_text(encoding="utf-8").splitlines()
                   if line.strip())

    def notify(self, *, conversation_ref: str, text: str, idempotency_key: str):
        nth = self._count() + 1
        receipt = self.receipts.pop(0) if self.receipts else None
        if receipt is None:
            receipt = {"delivered": True, "remote_id": f"om_{nth}"}
        record = {"conversation_ref": conversation_ref, "text": text,
                  "idempotency_key": idempotency_key, "receipt": receipt}
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False,
                                    sort_keys=True) + "\n")
        return receipt

    def close(self):
        if self.closed is not None:
            with self.closed.open("a", encoding="utf-8") as handle:
                handle.write("closed\n")


class ScriptedSource:
    """`read(source, ref)` answered from the options' `script`, one line per read.

    The read journal is how a test proves "one tick read this ref exactly once"
    across a process boundary: `#428` retries with backoff *within* a lifecycle,
    and the point of this entrypoint is that one invocation is one pass.
    """

    def __init__(self, script, reads=None):
        self.script = {key: list(value) for key, value in (script or {}).items()}
        self.reads = Path(reads) if reads else None
        self.calls = []

    def read(self, source: str, ref: str) -> dict:
        key = f"{source}:{ref}"
        self.calls.append(key)
        if self.reads is not None:
            with self.reads.open("a", encoding="utf-8") as handle:
                handle.write(key + "\n")
        answers = self.script.get(key)
        if not answers:
            raise SourceRefused("RUN_NOT_FOUND")
        value = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(value, dict) and "$refuse" in value:
            raise SourceRefused(str(value["$refuse"]))
        return value


# -- the factories themselves ---------------------------------------------- #
def file_lark(options):
    return FileLark(**options)


def scripted_source(options):
    return ScriptedSource(**options)


def bad_provider(options=None):
    """An object that is not a provider: no `notify`, no `capabilities`."""
    return object()


def raising_factory(options=None):
    raise RuntimeError("the provider could not be built")
