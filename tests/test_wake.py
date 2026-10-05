#!/usr/bin/env python3
"""The wake half: who resumes a checkpoint, and what stops it.

WHY THIS FILE EXISTS
--------------------
`tests/test_continuation.py` pins the record, the wait and the attempts, and its
own docstring says of the receiver question that «`RECEIVERS` is empty BY
CONSTRUCTION and not by oversight: it is the registry the wake half will fill».
`runtime/wake.py` is that half. This file is the assertion that it fills it from a
MEASUREMENT and that it refuses far more often than it wakes.

SIX PROPERTIES, IN THE ORDER A READER NEEDS THEM

  1. A RECEIVER IS MEASURED AND NOT DECLARED. `wake.equipment` reads this node's
     unit directory: the timer's presence in `timers.target.wants` (the symlink
     `systemctl --user enable` writes) and the `--parent` in the rendered
     `ExecStart=`. BOTH HALVES ARE REQUIRED, and each one alone reports nothing —
     a timer with no Parent fires and wakes nothing, a Parent with no timer is
     never reached. The error leans toward under-reporting, because the opposite
     error is `continuation_state` saying `RECEIVER_INSTALLED` about a node that
     will never wake anything, which is the sentence §17.4 forbids.

  2. IT REFUSES MORE THAN IT WAKES, AND EVERY REFUSAL HAS ITS OWN REASON. Nine
     reasons, each a fact about a different object — a wait still open, a wait
     nothing can evaluate, no grant, a grant that does not name this Item, a
     grant with no attempt bound, a grant with no wall bound, an exhausted
     budget, an attempt already in flight, attempts used up. Collapsing any two
     of them into «nothing to do» is what makes a parked Item look like a
     finished one.

  3. A WAKE SPENDS THE GRANT'S OWN BUDGET AND NOT ONE OF ITS OWN. `max_attempts`
     bounds how many times an epoch may be woken and `wall_seconds` bounds how
     long one Parent may run; the install supplies an OUTER budget and the
     receiver uses the smaller. Nothing here invents a retry policy.

  4. THE RECEIPT IS WHAT MAKES IT ONCE. `runtime/bounded_invocation.invoke_once`
     binds one action key to one receipt, replays a second call under the same
     key with the same input instead of executing, and refuses a key another
     wake still holds. `TestOneAttemptIsOneStart` runs a real process to check
     the mechanical half: the Parent really starts, its briefing really arrives
     on stdin, and a second pass really does not run it again.

  5. THE BRIEFING CARRIES THE CHECKPOINT AND THE DELEGATION, AND DECIDES
     NOTHING. Every line is a field of the record, of the wait or of the grant,
     plus the four reconciliations the resuming Parent must do for itself. The
     context ref is in it because that is what makes a wake «carrying a
     delegation a person already made» rather than a claim of authority.

  6. THE ROUTINE STILL DOES NOT RUN THE DAEMON. `runtime/daemon.py`'s tick
     reads neither admissions nor checkpoints, and a tick that started session
     shaped work would make every tick a possible writer of authority. There is
     a test below that reads the tick's own source, so the day somebody wires the
     two together is the day this file says so.

Hermetic: a temporary directory per test, no daemon, no socket, and the instant
an argument — EXCEPT in the tests that really start a Parent, which read the real
clock because the verb refuses `--now` on a wake that acts; `an_instant_now`
carries the reason. The only subprocesses are the Parent fixtures, which are
shell scripts written into the test's own temporary directory — and the CLI,
which `tests/test_continuation.py` already drives the same way.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
CLI = REPO / "bin" / "serverosctl"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The same module names `tests/test_continuation.py` uses, and deliberately:
# pytest runs both files in one process, and two copies of `AgentControlStore`
# would make an `isinstance` check in one file mean nothing about an object
# raised in the other. `continuation` is loaded under the name `wake` itself
# loads it under, so `fill_registry` writes to the module this test reads.
store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation",
                     RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError

NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
TOMORROW = "2026-09-27T10:00:00+00:00"
# A window that has ALREADY closed at `NOW`. The pair is the point: the context
# that authorizes must be FRESH at the instant the wake is PLANNED, and the wait
# must be OVER at that same instant. Both are `now` for the plan.
CLOSED = "2026-09-26T09:00:00+00:00"
# ...and the checkpoint is WRITTEN before it closes, because
# `continuation.build_checkpoint` refuses to save a window that has already
# passed — «a window that has already passed is not a wait». The two constants
# are three hours apart so the record is one a Parent could really have written
# and the plan is really reading a closed wait rather than an impossible one.
WRITTEN = "2026-09-26T08:00:00+00:00"
ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
BUDGET = {"wall_seconds": 30, "max_attempts": 2, "max_output_bytes": 200000,
          "exhausted": False}


def an_instant_now() -> str:
    """The real clock, ISO UTC.

    The two tests that really start a Parent plan at the real clock, because
    `work wake` refuses `--now` on a real wake — that rule is asserted below and
    is the reason this helper exists rather than a literal. Their context has to
    be observed at the real clock for the same reason: `actor.freshness` is
    computed against the clock DOING THE READING, so a fixture pinned to a
    literal date would be FRESH only on the day somebody wrote it down.
    """
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def a_context(**over) -> dict:
    """A trusted context that authorizes at `NOW`, which is the whole test.

    `context/actor.authorizes` wants FRESH + FACT + VERIFIED, and FRESH is
    computed from `observed_at` against the reading clock. So `observed_at` is
    `NOW` and every test that expects a wake plans at `NOW`; a test that wishes
    to see a refusal moves one of the two and says which.
    """
    record = {"context_ref": CONTEXT, "actor_ref": "human:hwz",
              "provenance": "FACT", "provider_status": "VERIFIED",
              "observed_at": NOW, "expires_at": "2026-12-31T00:00:00+00:00"}
    record.update(over)
    return record


def a_grant(**over) -> dict:
    record = {"grant_ref": GRANT, "delegated_by": CONTEXT, "items": [ITEM],
              "actions": ["research"],
              "valid_from": "2026-09-01T00:00:00+00:00",
              "expires_at": "2026-12-31T00:00:00+00:00",
              "budget": dict(BUDGET)}
    record.update(over)
    return record


def a_store(tmp_path: Path, *, context=None, grant=None) -> AgentControlStore:
    """A store holding one authorizing delegation over `SRV-0001`."""
    store = AgentControlStore(tmp_path / "store.json")
    store.put_context(a_context() if context is None else context)
    store.put_grant(a_grant() if grant is None else grant)
    return store


def a_checkpoint(store, *, work_item: str = ITEM, epoch: int = 1,
                 **over) -> dict:
    """One live checkpoint waiting on a window, which the caller chooses.

    A window and not a child, because most of this file is about the decision the
    routine makes and a window lets the callers that are not about children say
    what they mean in one argument. `wait_state`'s reading of an admission is
    `tests/test_continuation.py`'s subject and is not re-pinned here.

    `work_item` and `epoch` go through `build_checkpoint` so that
    `checkpoint_ref` follows them — the ref is derived from both, and a record
    whose ref said `#1` while its epoch said 2 would make the supersession test
    pass for the wrong reason. `over` is applied to the RECORD afterwards, which
    is how a test can put one on disk that `build_checkpoint` would have refused:
    an unanswerable `waiting_on` is exactly that case, and the point of it is a
    record the fabric cannot read rather than one the builder would not write.
    """
    record = continuation.build_checkpoint(
        work_item=work_item, epoch=epoch, parent_owner="parent-a",
        next_safe_action="carry the Item on from the accepted evidence",
        waiting_on={"kind": "window", "until": CLOSED},
        now=WRITTEN, requirement_revision="REQ-2026-09-20",
        remaining_results=["the reconciliation note"],
        evidence_locators=["artifacts/work-items/serveros/SRV-0001/RUN-0001"],
        child_attempts=["cyc-1"], unresolved_effects=["the mirror write"],
        write_scope=["workspace/records"])
    record.update(over)
    return store.put_checkpoint(record)


def ready_store(tmp_path: Path, **over) -> AgentControlStore:
    store = a_store(tmp_path)
    a_checkpoint(store, **over)
    return store


def a_script(tmp_path: Path, name: str, body: str) -> str:
    """One executable a wake can start. One path, no arguments — see `launcher_argv`."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(path)


@pytest.fixture
def registry_put_back():
    """Restore `continuation.RECEIVERS` after a test fills it.

    `fill_registry` assigns a module global, which is the point — one place fills
    it and every reader asks that place. A test that fills it and walks away would
    leave the registry non-empty for whichever test runs next in the same
    process, and the failure would land on `tests/test_continuation.py`.
    """
    before = continuation.RECEIVERS
    yield
    continuation.RECEIVERS = before


def units(tmp_path: Path, *, parent: str | None = "/bin/true",
          armed: bool = True) -> Path:
    """A unit directory with the two halves of a receiver, or whichever of them.

    Written by hand rather than by `scripts/release.py`, because what this file
    asserts is that the reader measures a DIRECTORY. `TestTheInstallRendersThese`
    is where the renderer is checked; rendering here would make this file fail
    for the renderer's reasons as well as its own.
    """
    root = tmp_path / "units"
    (root / "timers.target.wants").mkdir(parents=True, exist_ok=True)
    exec_line = (f"ExecStart=/usr/bin/serverosctl work wake --budget 60 "
                 f"--parent {parent}\n") if parent else "ExecStart=/usr/bin/true\n"
    (root / wake.SERVICE_NAME).write_text(
        "[Service]\n" + exec_line + "TimeoutStartSec=120\n")
    # Both unit files EXIST before either is enabled: `enable` writes a symlink
    # into the unit, and a symlink whose target is missing is a dangling one.
    # `armed` reads the symlink rather than asking a manager, and it must not
    # count a dangling one — a timer whose unit file was removed but never
    # disabled fires nothing, which is the same quiet failure as no timer at all.
    (root / wake.TIMER_NAME).write_text(
        "[Timer]\nOnCalendar=*-*-* *:1/5:00\nUnit=" + wake.SERVICE_NAME + "\n")
    if armed:
        (root / "timers.target.wants" / wake.TIMER_NAME).symlink_to(
            root / wake.TIMER_NAME)
    return root


def a_data_root(tmp_path: Path) -> dict:
    """A fixture DATA_ROOT, so `paths` resolves the store inside the test."""
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items",
                 "state/serveros/runtime/leases",
                 "state/serveros/runtime/receipts"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


def cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO), env=env)


# ---------------------------------------------------------------------------
# 1. A receiver is measured, not declared.
# ---------------------------------------------------------------------------


class TestAReceiverIsMeasured:
    """The four readings, and the two halves each of which is useless alone."""

    def test_nothing_installed_reads_as_nothing_installed(self, tmp_path):
        """A directory with neither half. This is where every node starts."""
        host = tmp_path / "units"
        host.mkdir()
        equipment = wake.equipment(host)
        assert equipment["armed"] is False
        assert equipment["wired"] is False
        assert equipment["installed"] is False
        assert wake.installed(host) == ()

    def test_an_armed_timer_with_no_parent_is_not_a_receiver(self, tmp_path):
        """The quiet failure this whole reading exists for.

        A unit whose ExecStart starts a command that starts no Parent fires on
        cadence, wakes nothing, and exits 0 — a success report for a node whose
        checkpoints are not being resumed. It must not read as installed.
        """
        host = units(tmp_path, parent=None)
        equipment = wake.equipment(host)
        assert equipment["armed"] is True
        assert equipment["wired"] is False
        assert wake.installed(host) == ()

    def test_a_parent_with_no_timer_is_not_a_receiver(self, tmp_path):
        """The other half, and the other repair: nothing ever fires it."""
        host = units(tmp_path, armed=False)
        equipment = wake.equipment(host)
        assert equipment["armed"] is False
        assert equipment["wired"] is True
        assert wake.installed(host) == ()

    def test_both_halves_report_the_receiver(self, tmp_path):
        host = units(tmp_path)
        equipment = wake.equipment(host)
        assert equipment["installed"] is True
        assert equipment["launcher"] == ["/bin/true"]
        assert wake.installed(host) == (wake.RECEIVER_ID,)

    def test_an_unrendered_placeholder_reads_as_no_parent(self, tmp_path):
        """A template installed without being rendered must not read as wired.

        `@SERVEROS_WAKE_PARENT@` is a file path as far as systemd is concerned,
        and the program it names does not exist. Counting it as a launcher would
        report a receiver and produce `START_FAILED` receipts — a broken launcher
        reported as a broken launcher, when the truth is an install that never
        finished.
        """
        host = units(tmp_path, parent=f"{wake.SERVICE_NAME}")
        (host / wake.SERVICE_NAME).write_text(
            "[Service]\nExecStart=/usr/bin/serverosctl work wake --parent "
            "@SERVEROS_WAKE_PARENT@\n")
        assert wake.launcher_argv(host) == []
        assert wake.installed(host) == ()

    def test_fill_registry_writes_what_is_measured(self, tmp_path, registry_put_back):
        """The single writer, and the reading it puts in the registry.

        `continuation.receivers()` is what `work continuations` prints and what
        `continuation_state` answers `AWAITING_CONTINUATION` or
        `RECEIVER_INSTALLED` from. A registry that no measurement reaches would
        keep saying «nothing will wake it» on a node where the timer is armed.
        """
        host = units(tmp_path)
        assert continuation.receivers() == ()
        assert wake.fill_registry(continuation, unit_dir=host) == (wake.RECEIVER_ID,)
        assert continuation.receivers() == (wake.RECEIVER_ID,)
        # And it is assigned rather than appended: a second call with nothing
        # installed reflects the node NOW rather than what was ever installed.
        empty = tmp_path / "gone"
        empty.mkdir()
        assert wake.fill_registry(continuation, unit_dir=empty) == ()
        assert continuation.receivers() == ()


# ---------------------------------------------------------------------------
# 2. It refuses more than it wakes.
# ---------------------------------------------------------------------------


class TestItRefusesMoreThanItWakes:
    """One row per live checkpoint, and the reason each one got.

    Nine reasons and no two of them share a sentence, because a reader who is
    told «nothing to do» for a checkpoint that is merely HELD would conclude the
    Item is finished — and a reader told it for a checkpoint no grant covers
    would conclude nothing is wrong with the delegation.
    """

    def test_a_wait_that_is_still_open_is_held_and_not_refused(self, tmp_path):
        """HOLD is not a failure. The wait is open; the next tick is after it."""
        store = a_store(tmp_path)
        a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW})
        plan = wake.plan(store, now=NOW)
        assert [row["action"] for row in plan["rows"]] == ["HOLD"]
        assert plan["rows"][0]["reason"] == "WAIT_OPEN"
        assert plan["would_wake"] == []

    def test_a_wait_nothing_can_evaluate_is_refused(self, tmp_path):
        """`WAIT_UNANSWERABLE` outranks the authority, and it must.

        A record whose wait cannot be read is one whose readiness nobody knows;
        waking it would be acting on a checkpoint the fabric cannot read.
        """
        store = a_store(tmp_path)
        a_checkpoint(store, waiting_on={"kind": "window", "until": "not-an-instant"})
        plan = wake.plan(store, now=NOW)
        assert plan["rows"][0]["action"] == "REFUSE"
        assert plan["rows"][0]["reason"] == "WAIT_UNANSWERABLE"

    def test_a_node_with_no_grant_at_all_says_so(self, tmp_path):
        """The ordinary state of a node nobody has delegated anything on."""
        store = AgentControlStore(tmp_path / "store.json")
        a_checkpoint(store)
        plan = wake.plan(store, now=NOW)
        row = plan["rows"][0]
        assert (row["action"], row["reason"]) == ("REFUSE", "NO_AUTHORIZING_GRANT")
        assert "no grant that authorizes now" in row["says"]
        assert row["grants_considered"] == []

    def test_a_grant_that_authorizes_but_names_no_item_is_a_different_refusal(
            self, tmp_path):
        """The narrowing this module makes, and the reason it is stated.

        A checkpoint records no project, so a project-scoped grant is one this
        routine could only match by guessing — and a receiver that guessed would
        be waking a Parent on authority that does not name the work. The row
        names the grant it saw, so the reader is not told «no grant».
        """
        store = a_store(tmp_path, grant=a_grant(items=["SRV-9999"]))
        a_checkpoint(store)
        plan = wake.plan(store, now=NOW)
        row = plan["rows"][0]
        assert row["reason"] == "NO_AUTHORIZING_GRANT"
        assert row["grants_not_covering"] == [GRANT]
        assert "name no Item" in row["says"]

    def test_a_context_that_no_longer_authorizes_is_not_a_grant(self, tmp_path):
        """The delegation is re-checked at the instant of the wake, not at write.

        This is the load-bearing half of `context/delegation.py`'s sentence: a
        grant cannot outlive the delegation it derives from. A stale context is
        `DELEGATION_DOES_NOT_AUTHORIZE`, which is not «no grant was ever made».
        """
        store = a_store(tmp_path, context=a_context(
            observed_at="2026-09-01T00:00:00+00:00"))
        a_checkpoint(store)
        plan = wake.plan(store, now=NOW)
        row = plan["rows"][0]
        assert row["reason"] == "NO_AUTHORIZING_GRANT"
        assert "DELEGATION_DOES_NOT_AUTHORIZE" in row["says"]

    def test_a_grant_with_no_attempt_bound_authorizes_nothing_to_spend(
            self, tmp_path):
        """A receiver that cannot bound itself is one whose Parent count nobody chose."""
        store = a_store(tmp_path, grant=a_grant(
            budget={"wall_seconds": 30, "max_output_bytes": 1024}))
        a_checkpoint(store)
        row = wake.plan(store, now=NOW)["rows"][0]
        assert row["reason"] == "NO_ATTEMPT_BUDGET"
        assert "unbounded receiver" in row["says"]

    def test_a_grant_with_no_wall_bound_authorizes_no_deadline(self, tmp_path):
        """`invoke_once` kills what outlives its bound, so no bound is no start."""
        store = a_store(tmp_path, grant=a_grant(
            budget={"max_attempts": 2, "max_output_bytes": 1024}))
        a_checkpoint(store)
        row = wake.plan(store, now=NOW)["rows"][0]
        assert row["reason"] == "NO_WALL_BUDGET"
        assert "starting one to kill it" in row["says"]

    def test_an_exhausted_budget_is_authority_the_grant_no_longer_has(
            self, tmp_path):
        store = a_store(tmp_path, grant=a_grant(
            budget={**BUDGET, "exhausted": True}))
        a_checkpoint(store)
        assert wake.plan(store, now=NOW)["rows"][0]["reason"] == "BUDGET_EXHAUSTED"

    def test_a_boolean_is_not_a_budget(self, tmp_path):
        """`True` is an `int`, and a budget of `True` is a budget of 1."""
        store = a_store(tmp_path, grant=a_grant(
            budget={"max_attempts": True, "wall_seconds": 30}))
        a_checkpoint(store)
        assert wake.plan(store, now=NOW)["rows"][0]["reason"] == "NO_ATTEMPT_BUDGET"

    def test_a_superseded_record_is_never_woken(self, tmp_path):
        """«who carries this» is answered before «what would wake it».

        A wake of a replaced epoch would be a second Parent on an Item that has
        changed hands — the thing the store's one-owner-per-scope rule exists to
        prevent, reached from the wake half.
        """
        store = a_store(tmp_path)
        a_checkpoint(store)
        a_checkpoint(store, epoch=2, parent_owner="parent-b")
        plan = wake.plan(store, now=NOW)
        by_ref = {row["checkpoint_ref"]: row for row in plan["rows"]}
        assert by_ref["SRV-0001#1"]["reason"] == "SUPERSEDED"
        assert by_ref["SRV-0001#1"]["action"] == "REFUSE"
        assert by_ref["SRV-0001#2"]["action"] == "WAKE"
        assert plan["would_wake"] == ["SRV-0001#2"]

    def test_a_parked_record_is_refused_in_the_park_s_own_words(self, tmp_path):
        """The record is otherwise READY — grant in force, attempts unspent, and
        nothing else in this store — and it is still not woken.

        That is what separates this refusal from every other one in this class: a
        park is not a missing authority, and the row says so in the words the
        person used, pointing at the verb that undoes it. `work wake` used to
        evaluate exactly this record once a minute and print «no authorizing
        grant», which is the sentence a genuinely blocked Item produces.
        """
        store = ready_store(tmp_path)
        store.park_checkpoint("SRV-0001#1", at=NOW, by="vince",
                              reason="the probe is answered; nobody waits on it")
        plan = wake.plan(store, now=NOW)
        row = plan["rows"][0]
        assert row["action"] == "REFUSE"
        assert row["reason"] == "PARKED"
        assert "the probe is answered" in row["says"]
        assert "vince" in row["says"]
        assert "work unpark" in row["says"]
        assert plan["would_wake"] == []
        assert plan["counts"] == {"PARKED": 1}

    def test_the_park_is_answered_before_the_wait_it_was_filed_with(
            self, tmp_path):
        """A park is a statement about the work and not a wait that ran out.

        The record below is filed with a window that is still OPEN, so a routine
        that asked the wait first would report `HOLD WAIT_OPEN` — «held, not yet
        due» about work a person has already set down, which is the reading that
        sends somebody back to wait for a timer that is never coming.
        """
        store = a_store(tmp_path)
        a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW})
        store.park_checkpoint("SRV-0001#1", at=NOW, by="vince", reason="set down")
        row = wake.plan(store, now=NOW)["rows"][0]
        assert row["reason"] == "PARKED"

    def test_a_ready_checkpoint_is_one_row_away_from_a_wake(self, tmp_path):
        """The positive case, and everything the row has to carry to act on it."""
        plan = wake.plan(ready_store(tmp_path), now=NOW)
        row = plan["rows"][0]
        assert (row["action"], row["reason"]) == ("WAKE", "READY")
        assert row["authorized_by"] == GRANT
        assert row["attempt"] == 1
        assert row["attempts_allowed"] == BUDGET["max_attempts"]
        assert row["wall_seconds"] == BUDGET["wall_seconds"]
        assert row["attempts_terminal"] == 0
        assert row["action_key"] == "continuation-wake-SRV-0001-e1-a1"
        assert plan["would_wake"] == ["SRV-0001#1"]
        assert plan["counts"] == {"READY": 1}

    def test_the_item_filter_narrows_the_rows_and_nothing_else(self, tmp_path):
        """`--item` is a filter over the live set, not a second decision path."""
        store = ready_store(tmp_path)
        a_checkpoint(store, work_item="SRV-0002", epoch=1,
                     parent_owner="parent-c")
        plan = wake.plan(store, now=NOW, item="SRV-0002")
        assert [row["work_item"] for row in plan["rows"]] == ["SRV-0002"]

    def test_every_reason_is_in_the_closed_vocabulary(self, tmp_path):
        """A row whose reason is not in `PLAN_REASONS` is a row nobody can read."""
        store = ready_store(tmp_path)
        a_checkpoint(store, work_item="SRV-0002", epoch=1, parent_owner="parent-c",
                     waiting_on={"kind": "window", "until": TOMORROW})
        plan = wake.plan(store, now=NOW)
        assert plan["rows"]
        for row in plan["rows"]:
            assert row["reason"] in wake.PLAN_REASONS
            assert row["action"] in wake.PLAN_ACTIONS


# ---------------------------------------------------------------------------
# 3. A wake spends the grant's budget and not one of its own.
# ---------------------------------------------------------------------------


class TestTheBoundIsTheGrants:
    """Attempts, walls, and the receipts that are the only memory here."""

    def test_a_terminal_receipt_moves_the_attempt_number(self, tmp_path):
        """The recovery path: a Parent that exited without taking the Item over.

        `invoke_once` waits synchronously and kills the group on its way out, so
        a terminal receipt means the process is gone. The checkpoint is still
        live and still ready, so the next tick spends the next attempt — which is
        what makes a Parent that died recoverable without a person.
        """
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        (receipts / "continuation-wake-SRV-0001-e1-a1.json").write_text(json.dumps(
            {"action_key": "continuation-wake-SRV-0001-e1-a1", "status": "EXITED",
             "started_at": NOW, "finished_at": LATER, "process_exit_code": 0}))
        plan = wake.plan(store, now=NOW, receipts=receipts)
        row = plan["rows"][0]
        assert row["attempt"] == 2
        assert row["attempts_terminal"] == 1
        assert row["action_key"] == "continuation-wake-SRV-0001-e1-a2"

    def test_an_attempt_already_in_flight_stops_the_wake(self, tmp_path):
        """The one case that is NOT retried, and `invoke_once` says why.

        A receipt left reading `STARTED` is a wake running right now or one whose
        writer died mid-flight — and that module's own comment is that the second
        «requires reconciliation, never blind replay». Starting a second Parent
        under it would be exactly the blind replay it refuses.
        """
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        (receipts / "continuation-wake-SRV-0001-e1-a1.json").write_text(json.dumps(
            {"action_key": "continuation-wake-SRV-0001-e1-a1", "status": "STARTED",
             "started_at": NOW}))
        row = wake.plan(store, now=NOW, receipts=receipts)["rows"][0]
        assert (row["action"], row["reason"]) == ("REFUSE", "WAKE_IN_FLIGHT")
        assert "never blind replay" in row["says"] or "blind" in row["says"]

    def test_an_unreadable_receipt_is_in_flight_rather_than_skipped(self, tmp_path):
        """Skipping it would let the next attempt COLLIDE with a key that has a file.

        `invoke_once` would then refuse the spawn with a message about the output
        files already existing, which is a true sentence about a different
        problem.
        """
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        (receipts / "continuation-wake-SRV-0001-e1-a1.json").write_text("{not json")
        row = wake.plan(store, now=NOW, receipts=receipts)["rows"][0]
        assert row["reason"] == "WAKE_IN_FLIGHT"

    def test_the_attempts_the_grant_allows_are_exactly_what_is_spent(
            self, tmp_path):
        """Two allowed, two recorded, and the third ask is refused."""
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        for attempt in (1, 2):
            (receipts / f"continuation-wake-SRV-0001-e1-a{attempt}.json").write_text(
                json.dumps({"status": "EXITED", "process_exit_code": 0}))
        row = wake.plan(store, now=NOW, receipts=receipts)["rows"][0]
        assert row["reason"] == "ATTEMPTS_EXHAUSTED"
        assert "2 of the 2 attempt(s)" in row["says"]
        # And the refusal hands the decision back rather than describing itself
        # as «nothing to do», which is the sentence a reader would take to mean
        # the Item is finished.
        assert "is a person's decision now" in row["says"]

    def test_receipts_for_another_item_or_epoch_are_not_counted(self, tmp_path):
        """The count is a match on the key, not «every file in the directory».

        The receipts root belongs to `invoke_once` and holds keys this module
        never wrote; a count that read all of them would refuse a wake because
        some other Item had been woken twice.
        """
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        for name in ("continuation-wake-SRV-0002-e1-a1.json",
                     "continuation-wake-SRV-0001-e2-a1.json",
                     "some-other-key.json"):
            (receipts / name).write_text(json.dumps({"status": "EXITED"}))
        assert wake.plan(store, now=NOW, receipts=receipts)["rows"][0]["attempt"] == 1

    def test_a_key_this_module_cannot_derive_is_refused_where_it_is_derived(
            self, tmp_path):
        """An Item name with a space in it cannot be an action key, and says so.

        `invoke_once` would refuse it a call later with a message about its own
        rule; refusing it here is the difference between «this Item's name is the
        problem» and «the key is malformed».
        """
        with pytest.raises(wake.WakeRefused) as raised:
            wake.action_key("SRV 0001", 1, 1)
        assert "not one `invoke_once` accepts" in str(raised.value)

    @pytest.mark.parametrize("item", ["SRV-0001", "LIT-0103", "MUC-0007",
                                      "a_b_c", "SRV-0001-x"])
    def test_the_key_reads_back_into_the_attempt_it_names(self, item):
        """The formatter and the parser are inverses, checked as a round trip.

        THE DEFECT THIS PINS, and it was mine: `ATTEMPT_KEY` began with the Item
        pattern `[A-Za-z0-9_]+`, which is a legal subset of `KEY_RULE` and cannot
        read back `continuation-wake-SRV-0001-e1-a1` at all — the parser stops at
        the hyphen before `0001` and finds no match. `action_key` would have
        written every key correctly and `attempts_recorded` would have found
        none of them, so the attempt number would have stuck at 1 for every Item
        in this product, every retry would have replayed the first receipt, and
        `WAKE_IN_FLIGHT` and `ATTEMPTS_EXHAUSTED` would have been unreachable.
        Nothing about a single call would have looked wrong.

        So the property is stated as a round trip, and the round trip is the
        thing `action_key` now enforces — a key that cannot be read back is
        refused at the moment it is derived rather than discovered as a missing
        receipt hours later.
        """
        for epoch in (1, 12):
            for attempt in (1, 7):
                key = wake.action_key(item, epoch, attempt)
                match = wake.ATTEMPT_KEY.match(key)
                assert match, key
                assert match.group("item") == item
                assert int(match.group("epoch")) == epoch
                assert int(match.group("attempt")) == attempt

    def test_an_item_whose_name_contains_the_suffix_still_reads_back(self):
        """The case that looks like it should break the round trip, and why not.

        An Item called `SRV-0001-e1-a1` produces a key with two «-e…-a…» tails.
        It still reads back, because the parser is anchored at the END and its
        Item group is greedy: the appended suffix is always the last occurrence,
        so the longest matching Item is always the one that was written. Stated
        as a test rather than left to the reader, because the safety of the
        round trip rests on that anchoring and an unanchored pattern would break
        this name first.
        """
        key = wake.action_key("SRV-0001-e1-a1", 2, 3)
        match = wake.ATTEMPT_KEY.match(key)
        assert match.group("item") == "SRV-0001-e1-a1"
        assert (int(match.group("epoch")), int(match.group("attempt"))) == (2, 3)


# ---------------------------------------------------------------------------
# 4. One attempt is one start.
# ---------------------------------------------------------------------------


class TestOneAttemptIsOneStart:
    """The mechanical half, with a real process.

    These are the slowest tests in the file and the only ones that spawn
    anything. They are here rather than left to the journey because the property
    they hold — that a second pass does not start a second Parent — is the one
    whose failure mode is two Parents on one Item, and a journey would show that
    as «the work happened twice» long after.
    """

    def test_the_parent_really_starts_and_really_gets_the_briefing(self, tmp_path):
        """A real process, a real receipt, and the checkpoint on its stdin."""
        store = ready_store(tmp_path)
        out = tmp_path / "parent.out"
        parent = a_script(tmp_path, "parent.sh", f"cat > {out}\nexit 0\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        rows = wake.plan(store, now=NOW, receipts=receipts)["rows"]
        results = wake.wake(rows, launcher=[parent], cwd=tmp_path, receipts=receipts)
        assert [entry["result"] for entry in results] == ["WOKEN"]
        assert results[0]["receipt_status"] == "EXITED"
        assert results[0]["process_exit_code"] == 0
        briefing = out.read_text()
        assert ITEM in briefing
        assert "REQ-2026-09-20" in briefing
        assert "carry the Item on from the accepted evidence" in briefing
        assert CONTEXT in briefing, "the delegation the wake was verified under"
        assert "receive each result" in briefing
        # The receipt is on disk, under the key the row named.
        assert (receipts / "continuation-wake-SRV-0001-e1-a1.json").is_file()

    def test_the_same_row_asked_twice_starts_one_parent(self, tmp_path):
        """The property the whole design rests on, at the layer that holds it.

        `invoke_once` binds one action key to one receipt: the same key with the
        same input returns the stored record and spawns nothing. The row is
        handed to `wake` TWICE because that is the collision this guards — two
        invocations of the unit within one tick window, a systemd restart of the
        oneshot, a person running the verb while the timer is mid-flight. The
        Parent counts its own starts, because a log line that one process
        appended once and two processes appended twice look the same either way.
        """
        store = ready_store(tmp_path)
        log = tmp_path / "parent.log"
        parent = a_script(tmp_path, "parent.sh", f"echo run >> {log}\nexit 0\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        rows = wake.plan(store, now=NOW, receipts=receipts)["rows"]
        first = wake.wake(rows, launcher=[parent], cwd=tmp_path, receipts=receipts)
        second = wake.wake(rows, launcher=[parent], cwd=tmp_path, receipts=receipts)
        assert [entry["result"] for entry in first] == ["WOKEN"]
        assert [entry["result"] for entry in second] == ["REPLAYED"]
        assert "nothing was spawned now" in second[0]["because"]
        assert log.read_text().splitlines() == ["run"]

    def test_a_second_plan_spends_the_next_attempt_and_then_stops(self, tmp_path):
        """A Parent that exited without taking the Item over is really retried.

        This is the recovery path end to end on real receipts rather than on
        fabricated ones: the wake writes attempt 1, the checkpoint is untouched
        (nothing in this test pretends to be the Parent's own write), so the next
        plan is attempt 2 — and the plan after that refuses, because the grant
        allowed 2. Counted from the Parent's own log, so «it was retried» is a
        fact about two processes rather than about two rows.
        """
        store = ready_store(tmp_path)
        log = tmp_path / "parent.log"
        parent = a_script(tmp_path, "parent.sh", f"echo run >> {log}\nexit 0\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        argv = dict(launcher=[parent], cwd=tmp_path, receipts=receipts)
        keys = []
        for round_number in (1, 2):
            rows = wake.plan(store, now=NOW, receipts=receipts)["rows"]
            keys.append(rows[0]["action_key"])
            assert [entry["result"] for entry in wake.wake(rows, **argv)] == ["WOKEN"]
        assert keys == ["continuation-wake-SRV-0001-e1-a1",
                        "continuation-wake-SRV-0001-e1-a2"]
        assert log.read_text().splitlines() == ["run", "run"]
        final = wake.plan(store, now=NOW, receipts=receipts)["rows"][0]
        assert (final["action"], final["reason"]) == ("REFUSE", "ATTEMPTS_EXHAUSTED")

    def test_a_parent_that_outlives_its_bound_is_killed_and_the_receipt_says_so(
            self, tmp_path):
        """The bound is real, and the receipt does not claim the work was done.

        `TIMED_OUT` carries `effect: UNKNOWN` and a sentence saying so; a reader
        who saw «a Parent started» without that would conclude the Item had been
        carried.
        """
        store = a_store(tmp_path, grant=a_grant(
            budget={**BUDGET, "wall_seconds": 1}))
        a_checkpoint(store)
        parent = a_script(tmp_path, "slow.sh", "sleep 30\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        results = wake.wake(wake.plan(store, now=NOW, receipts=receipts)["rows"],
                            launcher=[parent], cwd=tmp_path, receipts=receipts)
        assert results[0]["receipt_status"] == "TIMED_OUT"
        assert results[0]["result"] == "WOKEN"
        assert results[0]["timeout"] == 1
        assert results[0]["process_exit_code"] is not None

    def test_the_install_budget_wins_when_it_is_the_smaller_one(self, tmp_path):
        """Two bounds, and `min` is applied rather than whichever layer noticed.

        The grant says 30s and the install says 1; systemd would kill the unit at
        the second and `invoke_once` at the first, so the receiver applies the
        smaller and NAMES it in the result.
        """
        store = ready_store(tmp_path)
        parent = a_script(tmp_path, "slow.sh", "sleep 30\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        results = wake.wake(wake.plan(store, now=NOW, receipts=receipts)["rows"],
                            launcher=[parent], cwd=tmp_path, receipts=receipts,
                            budget=1)
        assert results[0]["timeout"] == 1

    def test_a_row_that_is_not_a_wake_starts_nothing(self, tmp_path):
        """The hold and the refusals never reach a launcher.

        Asserted by giving the launcher a marker: if a HOLD row reached `wake`,
        the marker would exist.
        """
        store = a_store(tmp_path)
        a_checkpoint(store, waiting_on={"kind": "window", "until": TOMORROW})
        log = tmp_path / "parent.log"
        parent = a_script(tmp_path, "parent.sh", f"echo run >> {log}\n")
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        rows = wake.plan(store, now=NOW, receipts=receipts)["rows"]
        assert wake.wake(rows, launcher=[parent], cwd=tmp_path,
                         receipts=receipts) == []
        assert not log.exists()

    def test_a_launcher_that_does_not_exist_fails_the_row_and_not_the_run(
            self, tmp_path):
        """One Item's broken launcher is not another Item's answer.

        A timer that exited non-zero because one of four Items could not be
        started would be reporting a delivery failure about three deliveries that
        happened.
        """
        store = ready_store(tmp_path)
        receipts = tmp_path / "receipts"
        receipts.mkdir(mode=0o700)
        results = wake.wake(wake.plan(store, now=NOW, receipts=receipts)["rows"],
                            launcher=[str(tmp_path / "absent")], cwd=tmp_path,
                            receipts=receipts)
        assert results[0]["result"] == "WOKEN"
        assert results[0]["receipt_status"] == "START_FAILED"
        assert results[0]["effect"] == "NO_PROCESS_STARTED"


# ---------------------------------------------------------------------------
# 5. The briefing carries the checkpoint and the delegation, and decides nothing.
# ---------------------------------------------------------------------------


class TestTheBriefingCarriesAndDoesNotDecide:
    """The four reconciliations, and the two things it must never say."""

    def briefing(self, tmp_path) -> str:
        plan = wake.plan(ready_store(tmp_path), now=NOW)
        return plan["rows"][0]["briefing"]

    def test_it_names_all_four_reconciliations(self, tmp_path):
        """The one instruction block, asserted by its four steps.

        §17.4 names the reading («恢复前对账 baseline、writer、effect 和旧 attempt»)
        and `docs/context-capsule.md` names the fields. A briefing that carried
        the fields and not the instruction would leave a Parent that has to know
        the protocol already — which is the session replay this object exists to
        avoid.
        """
        text = self.briefing(tmp_path)
        assert "BEFORE YOU ACT, RECONCILE" in text
        assert "re-check the current authority" in text
        assert "Requirement revision" in text
        assert "EXACTLY ONCE" in text
        assert "unresolved effects" in text
        assert "write scope" in text

    def test_it_says_where_its_own_authority_came_from(self, tmp_path):
        """A wake is legitimate only as far as the delegation still authorizes.

        The briefing names the ref so the Parent can check it AGAIN rather than
        take this document's word for it — which is what makes the routine a
        carrier of a person's delegation rather than a claim of its own.
        """
        text = self.briefing(tmp_path)
        assert f"under delegation     {CONTEXT}" in text
        assert f"authorizing grant    {GRANT}" in text

    def test_it_never_claims_the_work_is_done_or_the_scope_is_wider(self, tmp_path):
        text = self.briefing(tmp_path)
        assert "authority to widen" in text
        assert "do not treat this document as authority" in text.lower()

    def test_it_says_what_this_wake_spent(self, tmp_path):
        """So the Parent can see it is attempt 1 of 2 and how long it has."""
        text = self.briefing(tmp_path)
        assert f"attempt 1 of {BUDGET['max_attempts']}" in text
        assert f"{BUDGET['wall_seconds']}s" in text

    def test_a_briefing_over_the_bound_is_refused_and_not_truncated(self, tmp_path):
        """The instruction block is at the END, so truncation loses the instruction.

        A Parent started on a briefing nobody read to the end is worse than one
        that is not started, so the row is a REFUSE with its own reason rather
        than a shorter document.
        """
        store = ready_store(tmp_path, write_scope=[f"x{i}" for i in range(2000)])
        row = wake.plan(store, now=NOW)["rows"][0]
        assert (row["action"], row["reason"]) == ("REFUSE", "BRIEFING_OVER_BOUND")
        assert "the instruction block is at the end" in row["says"]


# ---------------------------------------------------------------------------
# 6. The routine still does not run the daemon.
# ---------------------------------------------------------------------------


class TestTheRoutineIsNotATick:
    """A property of this tree rather than of this feature, asserted as source.

    `runtime/daemon.py`'s tick evaluates detectors. A loop that also started
    session-shaped work would make every tick a possible writer of authority, and
    the wake would stop being a bounded act somebody can read afterwards. The
    check is on the tick's own body, because a comment saying «the tick does not
    wake anything» is exactly the kind of claim this repository keeps paying for.
    """

    def test_the_daemon_tick_still_reads_no_checkpoints(self):
        source = (RUNTIME / "daemon.py").read_text()
        tick = source.split("def tick(", 1)[1].split("\n    def ", 1)[0]
        assert "checkpoint" not in tick.lower()
        assert "admissions" not in tick.lower()

    def test_the_wake_half_is_reachable_only_through_its_own_entry_points(self):
        """`RECEIVERS` has one writer, and it is a measurement.

        Asserted as source because the property is «no other module assigns to
        it»: a second writer would be a second answer to «is anything going to
        wake this», and the two would disagree the first time one was edited.
        """
        assignment = re.compile(r"^\s*(?:\w+\.)?RECEIVERS\s*(?::[^=]*)?=", re.M)
        writers = []
        for path in sorted(RUNTIME.glob("*.py")) + sorted(REPO.glob("context/*.py")):
            # An ANNOTATED assignment counts: `RECEIVERS: tuple = ()` is where
            # the empty default lives, and a detector that only saw the bare form
            # would report the registry as having one writer when it has two.
            if assignment.search(path.read_text()):
                writers.append(path.name)
        assert sorted(writers) == ["continuation.py", "wake.py"], writers


# ---------------------------------------------------------------------------
# 7. The verb, end to end, against a scratch state root.
# ---------------------------------------------------------------------------


class TestTheVerbDecidesAndReports:
    """`serverosctl work wake`, the surface the timer runs and a person reads."""

    def a_scratch_node(self, tmp_path: Path, *, observed_at: str = NOW) -> dict:
        """A DATA_ROOT with a live, ready checkpoint in it.

        `observed_at` is the reading clock the context is minted for, and it
        splits these tests in two. The deciding ones pass `--now` and get the
        literal default, so their verdict does not depend on what time of day
        the suite ran. The two that really start a Parent cannot pass `--now` —
        the verb refuses it — so they read the real clock and their context has
        to be observed at it too; `an_instant_now()` is that instant.
        """
        env = a_data_root(tmp_path)
        store = AgentControlStore(
            Path(env["SERVEROS_STATE_DIR"]) / "runtime" / "agent-control" / "store.json")
        store.put_context(a_context(observed_at=observed_at))
        store.put_grant(a_grant())
        a_checkpoint(store)
        return env

    def test_dry_run_decides_and_starts_nothing(self, tmp_path):
        """The reading an operator needs after a routine has misbehaved.

        It must be the SAME decision the timer acts on — which it is, because the
        verb calls the same `plan` — and it must start nothing, which is asserted
        by there being no receipt afterwards.
        """
        env = self.a_scratch_node(tmp_path)
        done = cli("work", "wake", "--dry-run", "--json", "--now", NOW, env_extra=env)
        assert done.returncode == 0, done.stderr
        payload = json.loads(done.stdout)
        assert payload["would_wake"] == ["SRV-0001#1"]
        assert payload["rows"][0]["reason"] == "READY"
        assert not list(Path(env["SERVEROS_STATE_DIR"]).rglob("continuation-wake-*"))

    def test_the_receiver_it_reports_is_this_node_and_not_a_default(self, tmp_path):
        """`--unit-dir` is a parameter, so the reading is reproducible in a test."""
        env = self.a_scratch_node(tmp_path)
        host = units(tmp_path)
        done = cli("work", "wake", "--dry-run", "--json", "--unit-dir", str(host),
                   "--now", NOW, env_extra=env)
        payload = json.loads(done.stdout)
        assert payload["equipment"]["installed"] is True
        assert payload["equipment"]["launcher"] == ["/bin/true"]

    def test_without_a_parent_it_decides_and_says_nothing_was_started(self, tmp_path):
        """The fabrication §17.4 forbids, refused from the verb's side.

        A `work wake` that decided «this Item is ready» and reported success
        having started nothing would tell the reader the node handled it.
        """
        env = self.a_scratch_node(tmp_path)
        done = cli("work", "wake", "--now", NOW, env_extra=env)
        assert done.returncode == 0, done.stderr
        assert "no --parent was given, so nothing could be started" in done.stdout
        assert not list(Path(env["SERVEROS_STATE_DIR"]).rglob("continuation-wake-*"))

    def test_a_relative_parent_is_refused_before_anything_is_planned(self, tmp_path):
        """A Parent found through PATH is one whose identity depends on the caller.

        No `--now`, so the ONLY reason this can fail is the one it names: with
        both refusals present a `--now` would have fired first, and a
        counter-proof that removed the path check would still see red — for the
        other rule. A test that cannot fail for its own reason is not evidence
        about it.
        """
        env = self.a_scratch_node(tmp_path)
        done = cli("work", "wake", "--parent", "true", env_extra=env)
        assert done.returncode != 0
        assert "is not an absolute path" in done.stderr
        assert not list(Path(env["SERVEROS_STATE_DIR"]).rglob("continuation-wake-*"))

    def test_the_verb_starts_a_parent_and_records_the_receipt(self, tmp_path):
        """The whole path, once, against a scratch state root and a real process.

        No `--now`: a real wake refuses one, so this test is also the standing
        check that the production shape — the service unit's `ExecStart=`, which
        names no instant either — still wakes. The context is observed at the
        real clock to match.
        """
        env = self.a_scratch_node(tmp_path, observed_at=an_instant_now())
        log = tmp_path / "parent.log"
        parent = a_script(tmp_path, "parent.sh", f"cat > {log}\nexit 0\n")
        done = cli("work", "wake", "--parent", parent, "--cwd", str(tmp_path),
                   env_extra=env)
        assert done.returncode == 0, done.stderr
        assert "WOKEN" in done.stdout
        assert ITEM in log.read_text()
        receipts = list(Path(env["SERVEROS_STATE_DIR"]).rglob(
            "continuation-wake-SRV-0001-e1-a1.json"))
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())["status"] == "EXITED"

    def test_a_real_wake_refuses_a_named_instant(self, tmp_path):
        """The forgery this refusal exists for, refused before anything runs.

        `--now` moves the instant the PLAN is judged at while the Parent is
        spawned at the real clock. A wait that ended hours ago would then read
        as READY and a real Parent would really start on the strength of it.
        Asserted in both directions, because no instant a caller can name is one
        this node can check — and asserted to have started NOTHING, since a
        refusal that had already spawned the Parent would be the same defect
        wearing an error message.
        """
        env = self.a_scratch_node(tmp_path)
        parent = a_script(tmp_path, "parent.sh", "exit 0\n")
        for instant in (CLOSED, an_instant_now()):
            done = cli("work", "wake", "--parent", parent, "--cwd", str(tmp_path),
                       "--now", instant, env_extra=env)
            assert done.returncode != 0, done.stdout
            assert "--now" in done.stderr and "Nothing was started" in done.stderr
            assert "really start a Parent" in done.stderr
        assert not list(Path(env["SERVEROS_STATE_DIR"]).rglob("continuation-wake-*"))

    def test_a_dry_run_may_still_name_the_instant(self, tmp_path):
        """The gate is about STARTING, and this is the half it must not break.

        Reproducing a past decision against a named instant is what an operator
        reaches for after a timer has misbehaved, and `--parent` is routinely
        present in the environment it is read in. Refusing the pair here would
        take away the only reading that can explain a timer.
        """
        env = self.a_scratch_node(tmp_path)
        parent = a_script(tmp_path, "parent.sh", "exit 0\n")
        done = cli("work", "wake", "--dry-run", "--json", "--parent", parent,
                   "--cwd", str(tmp_path), "--now", NOW, env_extra=env)
        assert done.returncode == 0, done.stderr
        payload = json.loads(done.stdout)
        assert payload["would_wake"] == ["SRV-0001#1"]
        assert not list(Path(env["SERVEROS_STATE_DIR"]).rglob("continuation-wake-*"))

    def test_a_timer_that_fires_three_times_spends_two_attempts_and_then_stops(
            self, tmp_path):
        """The cadence, run the way the timer really runs it.

        Three firings of the verb against one unchanged checkpoint, with the
        grant allowing two attempts: attempts 1 and 2 start a Parent, and the
        third refuses with the reason a person would need. This is the shape a
        node shows when a Parent keeps exiting without taking the Item over —
        the routine retries within its bound and then stops rather than looping
        forever, which is the failure a wake half is most likely to have.
        """
        env = self.a_scratch_node(tmp_path, observed_at=an_instant_now())
        log = tmp_path / "parent.log"
        parent = a_script(tmp_path, "parent.sh", f"echo run >> {log}\nexit 0\n")
        args = ("work", "wake", "--parent", parent, "--cwd", str(tmp_path))
        first, second, third = (cli(*args, env_extra=env) for _ in range(3))
        assert [done.returncode for done in (first, second, third)] == [0, 0, 0]
        assert "WOKEN" in first.stdout and "WOKEN" in second.stdout
        assert "ATTEMPTS_EXHAUSTED" in third.stdout
        assert log.read_text().splitlines() == ["run", "run"]


# ---------------------------------------------------------------------------
# The cadence, which is derived from the authority and not chosen.
# ---------------------------------------------------------------------------

WAKE_TIMER = RUNTIME / "serveros-continuation-wake.timer"
_ACTOR_MODULE = "serveros_context_actor"


def actor_module():
    """`context/actor.py`, under the name `context/delegation.py` loads it as.

    The thresholds below are not written down a second time here. They are read
    from the module the receiver itself asks, off the contract file it reads, so
    that a contract edit moves both sides of the comparison at once and only a
    CADENCE that has stopped fitting it can fail these tests.
    """
    if _ACTOR_MODULE in sys.modules:
        return sys.modules[_ACTOR_MODULE]
    spec = importlib.util.spec_from_file_location(_ACTOR_MODULE,
                                                  REPO / "context" / "actor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ACTOR_MODULE] = module
    spec.loader.exec_module(module)
    return module


def timer_directive(name: str) -> str:
    """One `Key=value` line of the installed timer template, or a failure."""
    lines = [line for line in WAKE_TIMER.read_text().splitlines()
             if line.startswith(name + "=")]
    assert len(lines) == 1, f"{WAKE_TIMER.name} carries `{name}=` {len(lines)} times"
    return lines[0].split("=", 1)[1].strip()


def seconds(text: str) -> int:
    """`30s`, `1min`, `90` — the three shapes systemd's own units use here."""
    text = text.strip()
    for suffix, scale in (("min", 60), ("s", 1)):
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * scale
    return int(text)


def period_seconds(oncalendar: str) -> int:
    """The gap between two consecutive firings, from the expression alone.

    Narrow on purpose. It understands the two shapes this timer can honestly
    have — «every minute, at :SS» and «every N minutes, at :MM:SS» — and REFUSES
    anything else instead of returning a default, because an expression this
    cannot read is a cadence nobody is checking, and a check that silently
    passes on the unreadable is the one that stops being run.
    """
    match = re.fullmatch(r"\*-\*-\* \*:(?P<minute>\*|\d+/\d+):(?P<second>\d+)",
                         oncalendar)
    assert match, (f"`OnCalendar={oncalendar}` is not a shape this file can read, "
                   f"so the cadence it states is not measured by it")
    minute = match.group("minute")
    return 60 if minute == "*" else int(minute.split("/")[1]) * 60


class TestTheCadenceFitsInsideTheAuthorityItSpends:
    """A receiver faster than the delegation it spends, or it cannot wake at all.

    WHY THIS IS AN ASSERTION AND NOT A COMMENT. A wake spends a delegation, and
    `context/delegation.derivation` judges the delegation's trusted context at
    the instant the tick runs: the context must be FRESH, where the contract puts
    `stale_after_seconds: 300` between FRESH and STALE. So the tick period is not
    a delivery preference — it is the width of the only window in which this
    routine is allowed to act, and a period at or above the threshold makes the
    receiver one that fires on schedule, refuses BY NAME on every tick that
    matters, and never wakes anything.

    That is not hypothetical: this timer was installed on 2026-09-26 with
    `*-*-* *:1/5:00` — exactly 300 seconds, exactly the threshold — and the two
    ticks that ran against a real finished child both refused with
    `DELEGATION_DOES_NOT_AUTHORIZE`. The mechanism was correct and the cadence
    made it unreachable. The last test in this class runs that value back through
    the same arithmetic, so the rule can be seen rejecting something a reader
    knows was really shipped.
    """

    def test_the_period_and_the_accuracy_fit_inside_the_fresh_window(self):
        """The inequality the cadence has to satisfy, with both sides read."""
        stale = int(actor_module().contract()["freshness"]["stale_after_seconds"])
        period = period_seconds(timer_directive("OnCalendar"))
        accuracy = seconds(timer_directive("AccuracySec"))
        assert period + accuracy < stale, (
            f"the timer fires every {period}s with {accuracy}s of slack, which is "
            f"{period + accuracy}s between two consecutive ticks, and a "
            f"delegation stops authorizing after {stale}s. A wait that ends more "
            f"than {stale - period - accuracy}s after its delegation was observed "
            f"would then be refused by every tick that could have woken it.")

    def test_the_window_still_covers_a_useful_stretch_after_the_last_tick(self):
        """The same inequality read for what it buys, rather than for its margin.

        The gap between ticks is what a wait has to be unlucky about: a wait that
        ends just after one tick waits one gap for the next. This asserts that the
        deadline a Parent leaves is inside that stretch — i.e. that the thing the
        receiver exists to catch is not excluded by the arithmetic — without
        pinning the period to a number of seconds, which is the timer's business.
        """
        stale = int(actor_module().contract()["freshness"]["stale_after_seconds"])
        period = period_seconds(timer_directive("OnCalendar"))
        accuracy = seconds(timer_directive("AccuracySec"))
        assert stale - (period + accuracy) >= period, (
            f"only {stale - period - accuracy}s of the {stale}s freshness window "
            f"survive a tick landing late, which is less than one {period}s "
            f"period: a wait would have to end in a window narrower than the "
            f"interval between the ticks that are supposed to find it.")

    def test_the_expression_is_one_this_file_can_read(self):
        """The template's real value, through the parser, by name."""
        assert period_seconds(timer_directive("OnCalendar")) == 60

    def test_the_cadence_shipped_on_this_node_is_refused_by_that_rule(self):
        """The control: five minutes, which was really installed, does not fit."""
        stale = int(actor_module().contract()["freshness"]["stale_after_seconds"])
        assert period_seconds("*-*-* *:1/5:00") + seconds("30s") >= stale


# ---------------------------------------------------------------------------
# 8. One fact, three surfaces, one reading.
# ---------------------------------------------------------------------------


class TestEverySurfaceAnswersTheReceiverQuestionFromOneReading:
    """`work checkpoint` used to answer «nothing will wake it» on an armed node.

    FOUND BY READING THE OUTPUT OF THE PARENT THE ROUTINE HAD JUST STARTED. The
    wake was real — a receipt, an exit 0, a briefing on stdin — and the Parent's
    own checkpoint, three seconds later, printed:

        checkpoint 已保存，待续 — SRV-0001 epoch 2 is held by
        wake-parent-successor and no continuation receiver is installed on this
        node, so nothing will wake it.

    on the node whose routine was armed, had just fired, and had started that
    process. `RECEIVERS` is filled by `wake.fill_registry` and by nothing else;
    `work wake` and `work continuations` both call it, and `work checkpoint` —
    the third surface that reports the same state — did not. Its answer came from
    the module's import-time empty tuple, which is a measurement of nothing.

    THE READER THAT FOUND IT IS THE ONE THAT MATTERS. A Parent is started by the
    routine, so it is the one reader guaranteed to be told «nothing will wake
    you» precisely when something did — and the product's literal for the
    unwired state («待续») is the sentence a Parent reports to whoever it hands
    the Item back to. The other two surfaces existed and agreed with each other,
    which is what made this one look like it agreed too.

    WHAT THE TESTS ASSERT IS AGREEMENT, not a value. «Both say installed when I
    installed one» is the property, with the uninstalled case as the control that
    keeps it from being satisfied by printing a constant — and the last test pins
    that the reading is still an ARGUMENT rather than a field, so the fix did not
    reintroduce the record that can claim its own background run.
    """

    def write_it(self, env: dict, host: Path, *extra: str):
        """One `work checkpoint`, against a node whose units are `host`."""
        return cli("work", "checkpoint", ITEM, "--epoch", "1", "--parent", "p-1",
                   "--next-action", "carry the Item on", "--waiting-on-window",
                   TOMORROW, "--at", NOW, "--unit-dir", str(host),
                   *extra, env_extra=env)

    @pytest.mark.parametrize("equipped", [True, False])
    def test_the_two_verbs_agree_about_whether_a_receiver_is_installed(
            self, tmp_path, equipped):
        """Written by one verb and read by the other, on the same unit directory."""
        env = a_data_root(tmp_path)
        host = units(tmp_path, armed=equipped)
        written = self.write_it(env, host, "--json")
        assert written.returncode == 0, written.stderr
        listed = cli("work", "continuations", "--json", "--unit-dir", str(host),
                     "--now", NOW, env_extra=env)
        assert listed.returncode == 0, listed.stderr
        expected = "RECEIVER_INSTALLED" if equipped else "AWAITING_CONTINUATION"
        assert json.loads(written.stdout)["continuation"]["state"] == expected
        assert json.loads(listed.stdout)["states"][f"{ITEM}#1"]["state"] == expected

    def test_an_unwired_node_still_prints_the_products_own_literal(self, tmp_path):
        """The control, in the words the acceptance criterion names.

        A timer whose unit file exists but was never enabled is not a receiver,
        which is the dangling half this file's `units()` fixture already covers:
        taking it for one would trade this defect for its mirror image.
        """
        env = a_data_root(tmp_path)
        done = self.write_it(env, units(tmp_path, armed=False))
        assert done.returncode == 0, done.stderr
        assert "checkpoint 已保存，待续" in done.stdout
        assert "no continuation receiver is installed" in done.stdout

    def test_the_writer_still_cannot_name_its_own_receiver(self, tmp_path):
        """What the fix must not have done: put the receiver into the record.

        `continuation_state` takes the list as an argument precisely so that a
        checkpoint cannot carry a claim about what would wake it. Filling the
        argument from a measurement keeps that; the record is asserted to carry
        no receiver field at all, which is the difference between a reading and
        a declaration.
        """
        env = a_data_root(tmp_path)
        written = self.write_it(env, units(tmp_path), "--json")
        assert written.returncode == 0, written.stderr
        record = json.loads(written.stdout)["checkpoint"]
        assert not [key for key in record if "receiver" in str(key)]
