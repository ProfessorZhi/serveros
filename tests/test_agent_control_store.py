#!/usr/bin/env python3
"""The control store's writer model: one writer at a time, and what a reader sees.

Every other test file in this tree drives `runtime/agent_control_store.py`
through a consumer — approvals, contexts, bindings, selections — and each of
them is about what the consumer decides. This one is about the store itself,
and about a property none of those consumers could see: what happens when two
of them write.

The property has a failure mode that was measured rather than imagined, and it
is worth stating in the shape it was measured, because the shape is why it went
unnoticed. Two stores were opened on one path, the first loaded before the
second wrote:

    a = AgentControlStore(p); b = AgentControlStore(p)
    a.put_approval({... "state": "CONSUMED"})       # the audit record
    b.put_approval({... "state": "PENDING"})
    json.loads(p.read_text())["approvals"]          # -> the CONSUMED one is gone

Nothing raised. Nothing was logged. The record of what a person approved was
deleted by a writer that simply never had it in memory — because `_flush` wrote
`self._data`, the document this process loaded, and the document it loaded did
not contain the other writer's record. `_atomic_write` made that write
all-or-nothing, which is why the write itself looked safe; all-or-nothing is a
property of ONE write, and the defect is about two.

So there are four things asserted here, in the order a reader needs them:

  1. TWO WRITERS. Neither loses the other's record, whichever order they go in,
     including the case where the loser loaded first and wrote second.
  2. THE EXISTING INVARIANT, ACROSS WRITERS. `put_binding` is write-once on
     `session_ref`. A second writer's binding must make the first refuse, which
     it can only do if the mutation read the document that has the binding in
     it — so this test is about the re-read, stated as the rule it protects.
  3. THE READER. A record another writer committed is visible here, and
     `serverosd` is the reader that matters: it holds one store for its whole
     lifetime, so its memory is exactly the copy that goes stale.
  4. THE LOCK'S TARGET. The exclusion is on a sidecar and not on the store file,
     and that is not a preference: `_atomic_write` replaces the store with a new
     inode, and a lock is held on an inode, so two writers locking the store
     file could each hold a lock on a different file and neither would be
     excluded. `test_the_lock_target_is_a_sidecar_and_not_the_file_it_replaces`
     is that argument in two `st_ino` comparisons.

Hermetic: a temporary directory per test, no daemon, no socket, no network. The
one test that uses processes uses them to be processes — a thread cannot show
that two OS processes exclude each other — and writes only inside `tmp_path`.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load("serveros_runtime_agent_control_store", "agent_control_store.py")

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError


def a_context(ref: str) -> dict:
    return {"context_ref": ref, "actor_ref": "actor:1", "expires_at": "2030-01-01T00:00:00Z"}


def an_approval(ref: str, state: str = "GRANTED") -> dict:
    return {"approval_ref": ref, "actor_ref": "actor:1", "action": "session.stop",
            "state": state, "expires_at": "2030-01-01T00:00:00Z"}


def stored(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestTwoWriters:
    def test_neither_writers_record_is_erased(self, tmp_path):
        path = tmp_path / "store.json"
        a = AgentControlStore(path)
        b = AgentControlStore(path)

        a.put_context(a_context("ctx:a"))
        b.put_context(a_context("ctx:b"))

        assert set(stored(path)["contexts"]) == {"ctx:a", "ctx:b"}

    def test_the_writer_that_loaded_first_and_wrote_second_still_keeps_both(self, tmp_path):
        """The measured failure, stated as the assertion that would have caught it."""
        path = tmp_path / "store.json"
        stale = AgentControlStore(path)
        fresh = AgentControlStore(path)

        fresh.put_approval(an_approval("ap:fresh"))
        stale.put_approval(an_approval("ap:stale"))

        assert set(stored(path)["approvals"]) == {"ap:fresh", "ap:stale"}

    def test_a_consumed_approval_survives_a_writer_that_never_saw_it(self, tmp_path):
        """The record whose loss is worst, on its own.

        A consumed approval is the audit record of a decision somebody made:
        `sweep` keeps it past its window for exactly that reason. A writer
        that erased it would not thin a cache, it would make "who approved
        this" unanswerable — and the erasing writer is the ordinary case, not
        an exotic one, because it is simply a store that was opened earlier.
        """
        path = tmp_path / "store.json"
        other = AgentControlStore(path)
        audit = AgentControlStore(path)
        audit.put_approval(an_approval("ap:spent", state="CONSUMED"))

        other.put_approval(an_approval("ap:new"))

        assert stored(path)["approvals"]["ap:spent"]["state"] == "CONSUMED"

    def test_a_binding_written_elsewhere_is_refused_rather_than_clobbered(self, tmp_path):
        """§17's write-once rule, holding between two writers.

        `put_binding` refuses to replace a binding, and it does that by reading
        the document first. A mutation that read its own stale copy would find
        no binding there, write one, and overwrite the Item the thread was
        created for — which is the failure §17 exists to prevent, arrived at
        through the store rather than through the API.
        """
        path = tmp_path / "store.json"
        early = AgentControlStore(path)
        late = AgentControlStore(path)
        late.put_binding({"session_ref": "kernel:1:1", "session_role": "execution",
                          "bound_item_ref": "SRV-0011"})

        with pytest.raises(StoreError, match="cannot be replaced"):
            early.put_binding({"session_ref": "kernel:1:1", "session_role": "execution",
                               "bound_item_ref": "SRV-9999"})

        assert stored(path)["bindings"]["kernel:1:1"]["bound_item_ref"] == "SRV-0011"

    def test_the_refusal_writes_nothing_at_all(self, tmp_path):
        path = tmp_path / "store.json"
        early = AgentControlStore(path)
        late = AgentControlStore(path)
        late.put_binding({"session_ref": "kernel:1:1", "session_role": "coordinator"})

        with pytest.raises(StoreError):
            early.put_binding({"session_ref": "kernel:1:1", "session_role": "execution",
                               "bound_item_ref": "SRV-0011"})

        assert "SRV-0011" not in path.read_text(encoding="utf-8")

    def test_a_sweep_does_not_take_out_what_another_writer_added(self, tmp_path):
        """The deleter, which is the one place a stale copy is worst.

        `sweep` removes records. Applied to a stale copy it would remove the
        ones it means to and write back a document missing everything else —
        a deletion nobody asked for, on records nobody was looking at.
        """
        path = tmp_path / "store.json"
        sweeper = AgentControlStore(path)
        other = AgentControlStore(path)
        sweeper.put_context({**a_context("ctx:old"), "expires_at": "2001-01-01T00:00:00Z"})
        other.put_context({**a_context("ctx:fresh"), "expires_at": "2030-01-01T00:00:00Z"})

        gone = sweeper.sweep(before="2026-01-01T00:00:00Z")

        assert gone["contexts"] == ["ctx:old"]
        assert set(stored(path)["contexts"]) == {"ctx:fresh"}


class TestTheReader:
    def test_a_reader_sees_a_write_it_did_not_make(self, tmp_path):
        """`serverosd` is this test's subject: one store, held for its lifetime."""
        path = tmp_path / "store.json"
        daemon_store = AgentControlStore(path)
        assert daemon_store.get_context("ctx:elsewhere") is None

        AgentControlStore(path).put_context(a_context("ctx:elsewhere"))

        assert daemon_store.get_context("ctx:elsewhere") is not None
        assert daemon_store.summary()["contexts"] == 1

    def test_a_removed_approval_is_visible_as_removed(self, tmp_path):
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_approval(an_approval("ap:1"))
        other = AgentControlStore(path)
        other.replace({**an_approval("ap:1"), "state": "CONSUMED"}, kind="approval")

        assert store.get_approval("ap:1")["state"] == "CONSUMED"

    def test_a_read_does_not_wait_for_a_writer(self, tmp_path):
        """Readers take no lock, so a mutation in progress cannot block an answer.

        Asserted through a thread with a deadline rather than directly, because
        the failure this guards against is a hang: if a read ever took the write
        lock, this call would block until the mutation finished and the test
        would report it rather than the suite stopping.
        """
        path = tmp_path / "store.json"
        writer = AgentControlStore(path)
        writer.put_context(a_context("ctx:1"))
        reader = AgentControlStore(path)
        done = threading.Event()
        answer: list = []

        def read():
            answer.append(reader.contexts())
            done.set()

        with writer._mutation():
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            assert done.wait(5), "a read waited for the write lock"

        assert set(answer[0]) == {"ctx:1"}

    def test_a_store_that_never_existed_is_an_empty_register_and_not_a_failure(self, tmp_path):
        store = AgentControlStore(tmp_path / "never-written.json")

        assert store.approvals() == {}
        assert store.contexts() == {}
        assert store.get_binding("kernel:1:1") is None
        assert store.summary()["selections"] == 0

    def test_a_store_this_writer_has_written_is_not_read_as_empty_when_it_is_removed(self, tmp_path):
        """Absent-at-startup and absent-later are the same bytes and opposite answers."""
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_approval(an_approval("ap:1"))
        path.unlink()

        with pytest.raises(StoreError, match="has been removed"):
            store.approvals()


class TestTheWritePath:
    def test_a_write_outside_a_mutation_is_refused(self, tmp_path):
        path = tmp_path / "store.json"
        store = AgentControlStore(path)

        with pytest.raises(StoreError, match="outside a mutation"):
            store._flush()

        assert not path.exists()

    def test_a_validated_record_is_refused_before_the_lock_is_taken(self, tmp_path):
        """A refusal that needs no lock does not take one, and records nothing."""
        path = tmp_path / "store.json"
        store = AgentControlStore(path)

        with pytest.raises(StoreError, match="cannot be stored"):
            store.put_context({"actor_ref": "actor:1"})

        assert not path.exists()

    def test_the_lock_is_released_when_a_mutation_refuses(self, tmp_path):
        """A refusal inside the block must not leave the store locked for good.

        Called twice from the same instance: the second call can only reach its
        own refusal if the first released the lock. A lock held past an
        exception would deadlock the process that raised.
        """
        path = tmp_path / "store.json"
        AgentControlStore(path).put_binding({"session_ref": "kernel:1:1",
                                             "session_role": "coordinator"})
        store = AgentControlStore(path)

        for _ in range(2):
            with pytest.raises(StoreError, match="cannot be replaced"):
                store.put_binding({"session_ref": "kernel:1:1",
                                   "session_role": "execution",
                                   "bound_item_ref": "SRV-0011"})


class TestTheLockTarget:
    def test_the_sidecar_is_named_after_the_store(self, tmp_path):
        path = tmp_path / "store.json"

        assert store_mod.lock_path(path) == Path(str(path) + ".lock")
        assert store_mod.lock_path(path) != path

    def test_the_lock_target_is_a_sidecar_and_not_the_file_it_replaces(self, tmp_path):
        """Why locking the store file would work everywhere except under a race.

        `_atomic_write` ends in `os.replace`, so the store is a new inode after
        every write. An `flock` is held on an inode. Two writers that each
        locked the store file would each be holding a lock on a different file
        and neither would be excluded — the exclusion would appear to work and
        would be absent exactly when two writers overlapped.
        """
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_context(a_context("ctx:1"))
        lock_inode = store.lock_path.stat().st_ino
        store_inode = path.stat().st_ino

        store.put_context(a_context("ctx:2"))

        assert path.stat().st_ino != store_inode, "the store file is replaced by a write"
        assert store.lock_path.stat().st_ino == lock_inode, "the lock target is not"

    def test_the_sidecar_carries_no_bytes_and_that_is_what_makes_it_not_a_record(self, tmp_path):
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_context(a_context("ctx:1"))

        assert store.lock_path.exists()
        assert store.lock_path.read_text() == ""
        assert json.loads(path.read_text())["contexts"]  # the store is the record

    def test_a_sidecar_left_by_a_killed_writer_does_not_block_the_next_writer(self, tmp_path):
        """A lock file's existence is not the lock — the kernel releases it on exit.

        The file is left behind WITH a plausible claim written into it, because
        the thing being refused is not "a file is there" but "someone read the
        file and concluded a lease is held", which is the reading
        `runtime/state_root.py` warns about for `locks/`.
        """
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_context(a_context("ctx:1"))
        store.lock_path.write_text("held by pid 999999 since forever\n")

        next_writer = AgentControlStore(path)
        next_writer.put_context(a_context("ctx:2"))

        assert set(stored(path)["contexts"]) == {"ctx:1", "ctx:2"}

    def test_a_mutation_holds_the_lock_until_it_is_done(self, tmp_path):
        """Exclusion, shown by ordering rather than by inspection.

        The first holder is inside a mutation and not leaving. The second
        writer is started while it holds. If the lock excludes, the second
        cannot have written while the first was inside; the assertion is in
        that direction deliberately, so it can fail only by the lock not
        excluding and never by a slow machine.
        """
        path = tmp_path / "store.json"
        holder = AgentControlStore(path)
        second = AgentControlStore(path)
        held = threading.Event()
        release = threading.Event()
        written = threading.Event()

        def hold():
            with holder._mutation():
                held.set()
                release.wait(10)

        def write():
            second.put_context(a_context("ctx:second"))
            written.set()

        holder_thread = threading.Thread(target=hold, daemon=True)
        holder_thread.start()
        assert held.wait(10), "the holder never took the lock"
        writer_thread = threading.Thread(target=write, daemon=True)
        writer_thread.start()

        assert not written.wait(0.3), "a second writer wrote while the lock was held"

        release.set()
        assert written.wait(10), "the second writer never got the lock"
        holder_thread.join(10)
        writer_thread.join(10)
        assert holder.get_context("ctx:second") is not None


class TestTwoProcesses:
    """The case a thread cannot show: `flock` between separate processes."""

    WRITER = """
import sys
sys.path.insert(0, {runtime!r})
import agent_control_store as store_mod
path, tag, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
store = store_mod.AgentControlStore(path)
for i in range(count):
    store.put_context({{"context_ref": "ctx:%s:%d" % (tag, i), "actor_ref": tag}})
"""

    def test_four_processes_writing_at_once_lose_nothing(self, tmp_path):
        path = tmp_path / "store.json"
        script = self.WRITER.format(runtime=str(RUNTIME))
        writers = [subprocess.Popen(
            [sys.executable, "-c", script, str(path), f"w{n}", "6"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for n in range(4)]

        for writer in writers:
            _, errors = writer.communicate(timeout=60)
            assert writer.returncode == 0, errors.decode()

        expected = {f"ctx:w{n}:{i}" for n in range(4) for i in range(6)}
        assert set(stored(path)["contexts"]) == expected
