#!/usr/bin/env python3
"""`work delegate` / `work revoke`: the producer §5 needs, and its one premise.

WHY THIS FILE EXISTS
--------------------
`CC-EXECUTION-LEDGER.md` recorded the gap in its own words: the first real grant
«still PARTIAL: `put_grant` is called only by tests — there is no CLI verb, no
daemon method, no MCP tool», and it named that gap as one the plan forbade
closing with a fixture. `put_grant` was reachable and nothing on the node could
reach it. A delegation that no person can record is a delegation that only
tests ever made.

`cmd_work_grants` states the reason the missing verb had to be written carefully
rather than as an `--approve` flag:

    it writes nothing. There is no `--approve` and no way to mint a grant from
    here — a delegation is a person's decision, and a verb that could record one
    is a verb that could authorize work on its own.

So the property this file pins is not «the verb exists». It is the one that
makes the verb safe to exist: A DELEGATION HAS A ROOT THE STORE ALREADY HELD.
`--context-ref` must name a trusted context, and that ref has exactly one
producer on this node — `agent.context.attach`, which refuses unless the pid the
report was observed by is the pid on the other end of its own socket
(`SO_PEERCRED`, `runtime/daemon.py`). Every test below is that property seen
from a different side:

  1. NO ROOT, NO RECORD. Without `--context-ref`, and with one this store has
     never held, the verb refuses and writes nothing. The store's own check is
     exercised separately, because it is the guard and the CLI's check is only
     the courtesy that tells a reader where the missing thing comes from.

  2. THE RECORD NAMES THE CONTEXT, NOT AN ACTOR. There is no flag that supplies
     an actor, and the record carries none. `context/delegation.derivation`
     reads the actor back out of the context on every judgement; a second copy
     here would be a copy that could outlive or contradict its source.

  3. THE BOUNDS A WAKE SPENDS ARE ALL REQUIRED. `--item`, the window and every
     `BUDGET_KEYS` entry. `runtime/wake.authority` carries a wake only under a
     grant that NAMES the Item and `runtime/wake.spending` reads the budget off
     the grant — so a grant that declares none is one the routine could only
     honour by inventing.

  4. THE WRITE IS WRITE-ONCE AND ENDING IS NOT EXTENDING. A second identical
     report returns the first record; a second report with a wider scope is
     refused; a revoke keeps the FIRST instant; a revoked grant stops
     authorizing without stopping existing.

  5. THE PRODUCER REALLY REACHES THE CONSUMER. The last two tests take the
     grant `serverosctl` wrote, hand the store's own dicts to
     `runtime/wake.authority` and then to `wake.plan`, and require a carried
     wake — and, after a revoke through the same CLI, require the refusal back.
     A verb whose record nothing reads would pass every test above this line.

Hermetic: one temporary state root per test, driven the way
`tests/test_admission.py` drives the CLI, so the verbs under test read the
fixture store and not this node's live one.
"""
from __future__ import annotations

import importlib.util
import json
import os
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


# The same module names `tests/test_wake.py` loads, and for the same reason: two
# copies of `AgentControlStore` in one pytest process would make an `isinstance`
# check in one file mean nothing about an object raised in the other.
store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation",
                     RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")
# Loaded under the name `runtime/cli.py` uses, so a value this verb records is
# judged by the same function the reading is resolved with rather than by a
# restatement of it here.
semantic = _load("serveros_intake_understanding_semantic",
                 REPO / "intake" / "understanding_semantic.py")

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError


# The instants `tests/test_wake.py` already uses, kept equal on purpose: the
# context must be FRESH at the instant the wake is PLANNED and the wait must be
# OVER at that same instant, and reusing its three constants is how this file
# and that one cannot drift into testing two different clocks.
NOW = "2026-09-26T10:00:00+00:00"
CLOSED = "2026-09-26T09:00:00+00:00"
WRITTEN = "2026-09-26T08:00:00+00:00"

ITEM = "SRV-0001"
CONTEXT = "human-context:botmux-human-control:" + "c" * 32
GRANT = "grant:tokyo-srv-0001"


def env_for(tmp_path: Path) -> dict:
    """A state root of this test's own, and every root the verbs consult.

    Copied from `tests/test_admission.py` rather than narrowed to the one store,
    because `work delegate` reaches for the resource leases and the receipts
    through `runtime/paths.py` as well, and a fixture that isolated only the
    control store would have a verb reading this node's live ones.
    """
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


def store_path(env: dict) -> Path:
    return Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"


def cli(*args: str, env: dict) -> subprocess.CompletedProcess:
    full = dict(os.environ)
    full.update(env)
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO), env=full)


def seed_context(env: dict, **over) -> dict:
    """One trusted context in the fixture store, authorizing at `NOW`.

    Written straight to the store because its only producer,
    `agent.context.attach`, requires a real turn — which is the whole point of
    the verb under test and cannot be manufactured here. What CAN be asserted
    without a turn is every use the verb makes of a context that already
    exists, and that is what these tests assert.
    """
    record = {"context_ref": CONTEXT, "actor_ref": "human:hwz",
              "provenance": "FACT", "provider_status": "VERIFIED",
              "observed_at": NOW, "expires_at": "2026-12-31T00:00:00+00:00"}
    record.update(over)
    AgentControlStore(store_path(env)).put_context(record)
    return record


def a_checkpoint(env: dict) -> dict:
    """One live checkpoint for `ITEM` whose wait has already closed at `NOW`.

    Shaped after `tests/test_wake.py`'s fixture, instants and all: the wait is
    over at the instant the plan reads, so the only thing left that can decide
    the row is the authority. `WAIT_OPEN` would otherwise outrank it and the
    test would pass for the wrong reason.
    """
    store = AgentControlStore(store_path(env))
    record = continuation.build_checkpoint(
        work_item=ITEM, epoch=1, parent_owner="parent-a",
        next_safe_action="carry the Item on from the accepted evidence",
        waiting_on={"kind": "window", "until": CLOSED},
        now=WRITTEN, requirement_revision="REQ-2026-09-20",
        remaining_results=["the reconciliation note"],
        evidence_locators=["artifacts/work-items/serveros/SRV-0001/RUN-0001"],
        child_attempts=["cyc-1"], unresolved_effects=["the mirror write"],
        write_scope=["workspace/records"])
    return store.put_checkpoint(record)


def delegate(env: dict, *extra: str, grant: str = GRANT,
             context: str = CONTEXT) -> subprocess.CompletedProcess:
    return cli("work", "delegate", "--json", "--grant-ref", grant,
               "--context-ref", context,
               "--item", ITEM,
               "--valid-from", "2026-09-01T00:00:00+00:00",
               "--expires-at", "2026-12-31T00:00:00+00:00",
               "--wall-seconds", "30", "--max-attempts", "2",
               "--max-output-bytes", "200000", *extra, env=env)


def recorded_grant(env: dict, ref: str = GRANT) -> dict | None:
    return AgentControlStore(store_path(env)).get_grant(ref)


# ---------------------------------------------------------------------------
# 1. No root, no record.
# ---------------------------------------------------------------------------


class TestADelegationNeedsARootTheStoreAlreadyHeld:
    def test_without_a_context_ref_it_refuses_and_writes_nothing(self, tmp_path):
        env = env_for(tmp_path)
        done = cli("work", "delegate", "--grant-ref", GRANT, "--item", ITEM,
                   env=env)
        assert done.returncode == 1, done.stdout
        assert "--context-ref is required" in done.stderr
        # The refusal is the point, but the SENTENCE is what a reader needs:
        # a bare «required» would send them looking for a flag rather than at
        # the producer, and there deliberately is no flag that would do.
        assert "could sign its own authority" in done.stderr
        assert recorded_grant(env) is None

    def test_a_context_ref_this_node_never_held_is_refused(self, tmp_path):
        env = env_for(tmp_path)
        done = delegate(env, context="human-context:nobody:" + "d" * 32)
        assert done.returncode == 1, done.stdout
        assert "nothing to derive from" in done.stderr
        # Named so that the reader goes to the producer and not to the store.
        assert "agent.context.attach" in done.stderr or "work grants" in done.stderr
        assert recorded_grant(env) is None

    def test_the_store_refuses_a_root_it_would_have_to_invent(self, tmp_path):
        """The guard, tested where it lives rather than where it is announced.

        The CLI's check is a better sentence; `put_grant`'s is the one that
        stands. Anything reaching the store by another route must meet it, so
        this asserts the store directly and not through the verb.
        """
        env = env_for(tmp_path)
        store = AgentControlStore(store_path(env))
        try:
            store.put_grant({"grant_ref": GRANT, "delegated_by": CONTEXT,
                             "items": [ITEM],
                             "valid_from": "2026-09-01T00:00:00+00:00",
                             "expires_at": "2026-12-31T00:00:00+00:00",
                             "budget": {"wall_seconds": 30, "max_attempts": 2,
                                        "max_output_bytes": 200000}})
        except StoreError:
            pass
        else:
            raise AssertionError(
                "a grant derived from a context this store never held was "
                "accepted: authority with no root, which no later clock can "
                "supply")
        assert store.get_grant(GRANT) is None


# ---------------------------------------------------------------------------
# 2. The record names the context, not an actor.
# ---------------------------------------------------------------------------


class TestTheRecordDerivesItsActorAndNeverCarriesOne:
    def test_a_held_context_is_recorded_as_the_root(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        done = delegate(env)
        assert done.returncode == 0, done.stderr
        grant = recorded_grant(env)
        assert grant is not None
        assert grant["delegated_by"] == CONTEXT
        assert grant["items"] == [ITEM]
        assert grant["budget"] == {"wall_seconds": 30, "max_attempts": 2,
                                   "max_output_bytes": 200000}

    def test_the_verb_has_no_flag_that_supplies_an_actor(self, tmp_path):
        """A structural assertion, and the one worth having.

        «The actor is read from the context» is easy to say and easy to break
        by adding a convenience flag later. A flag that does not exist cannot be
        passed, so this asks the parser rather than the behaviour.
        """
        env = env_for(tmp_path)
        done = cli("work", "delegate", "--help", env=env)
        assert done.returncode == 0
        for flag in ("--actor", "--actor-ref", "--as", "--user", "--approved-by"):
            assert flag not in done.stdout, (
                f"{flag} exists on `work delegate`: a verb that can be told who "
                f"is delegating can sign its own authority")

    def test_the_stored_record_carries_no_second_copy_of_the_actor(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        grant = recorded_grant(env)
        # `revoked` and its two companions are the STORE's fields and have to be
        # here: `put_grant` writes `revoked: False` on the record it stages, and
        # `context/delegation.ROW_PRODUCED` says of them «written by the store,
        # never here». A verb that could write one could also write it back.
        allowed = {"grant_ref", "delegated_by", "valid_from", "expires_at",
                   "items", "projects", "actions", "budget", "covers",
                   "revoked", "revoked_at", "revoked_reason"}
        assert set(grant) <= allowed, (
            f"the grant carries {sorted(set(grant) - allowed)}: a field this "
            f"verb wrote alongside `delegated_by` is a second answer to «who "
            f"delegated this», and that question must not have two")
        for field in ("actor_ref", "actor", "delegated_to", "owner"):
            assert field not in grant

    def test_the_context_is_reported_but_not_copied_into_the_grant(self, tmp_path):
        """The actor is printed for the person running the verb, and only there.

        Read through the human lines rather than `--json` on purpose: `emit`
        prints the structured form INSTEAD of the lines, so the actor appears in
        exactly one of the two and this asks the one a person reads at a
        terminal.
        """
        env = env_for(tmp_path)
        seed_context(env)
        done = cli("work", "delegate", "--grant-ref", GRANT,
                   "--context-ref", CONTEXT, "--item", ITEM,
                   "--valid-from", "2026-09-01T00:00:00+00:00",
                   "--expires-at", "2026-12-31T00:00:00+00:00",
                   "--wall-seconds", "30", "--max-attempts", "2",
                   "--max-output-bytes", "200000", env=env)
        assert done.returncode == 0, done.stderr
        assert "human:hwz" in done.stdout
        assert "human:hwz" not in json.dumps(recorded_grant(env))

    def test_a_dry_run_records_nothing(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        done = delegate(env, "--dry-run")
        assert done.returncode == 0, done.stderr
        assert json.loads(done.stdout)["would_record"]["delegated_by"] == CONTEXT
        assert recorded_grant(env) is None


# ---------------------------------------------------------------------------
# 2b. The scope is recorded as written, and never as a shape no reader can read.
# ---------------------------------------------------------------------------


class TestTheScopeIsRecordedSoEveryReaderCanReadIt:
    def test_the_scope_is_copied_as_written(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        done = delegate(env, "--covers", "bruce", "--covers", "alice")
        assert done.returncode == 0, done.stderr
        assert recorded_grant(env)["covers"] == ["alice", "bruce"]

    def test_a_grant_without_a_scope_records_no_scope_key(self, tmp_path):
        """Absent, not empty: `put_grant` is handed a record without the key and
        the reader answers «names no subject» for it either way — two spellings
        of one fact that must not become two facts."""
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        assert "covers" not in recorded_grant(env)

    @pytest.mark.parametrize("blank", ["", " ", "\t"])
    def test_a_blank_prefix_is_refused_before_it_is_recorded(
            self, tmp_path, blank):
        """A blank prefix matches every subject, so recording one is recording an
        unbounded scope — and `understanding_semantic.delegation_scope` refuses
        such a value on every read. Refused here rather than filtered, because a
        verb that dropped the entry would leave the caller believing the scope
        they typed had been recorded."""
        env = env_for(tmp_path)
        seed_context(env)
        done = delegate(env, "--covers", blank)
        assert done.returncode == 1, done.stdout
        assert "--covers cannot carry an empty entry" in done.stderr
        assert recorded_grant(env) is None

    def test_every_scope_this_verb_records_can_be_read_by_the_reader(
            self, tmp_path):
        """The anti-drift property, and the reason the verb asks the module's
        question instead of its own: a grant the store hands out whose scope the
        reader refuses is a grant that authorizes nothing while looking recorded,
        and the two halves — what this verb writes and what that module reads —
        have no shared test today except this one.

        Stated as a disjunction over adversarial values rather than as a list of
        expected recordings, because that is the property: for each value, either
        the verb refused it and recorded nothing, or it recorded it and the
        reader reads it back as what was typed. There is no third outcome. Read
        through `understanding_semantic.delegation_scope` — not through a copy of
        the rule written here, which would be the drift it guards against.
        """
        env = env_for(tmp_path)
        seed_context(env)
        # A ref per value: `put_grant` refuses to overwrite an existing ref, and
        # a reuse would show up as a refusal that says nothing about the value.
        for index, value in enumerate(("", " ", "\t", "x", "a:b", "*",
                                       "alice smith", "0")):
            ref = f"grant:shape-{index}"
            done = delegate(env, "--covers", value, grant=ref)
            if done.returncode != 0:
                assert recorded_grant(env, ref) is None, (
                    f"{value!r} was refused and recorded anyway")
            else:
                assert semantic.delegation_scope(
                    recorded_grant(env, ref)) == [value], (
                    f"{value!r} was recorded and the reader does not read it "
                    f"back as what was typed")


# ---------------------------------------------------------------------------
# 3. Every bound the wake spends is required.
# ---------------------------------------------------------------------------


class TestTheBoundsAWakeSpendsAreAllRequired:
    """Each refusal names the consumer that would have had to invent the value."""

    def test_a_grant_that_names_no_item_is_refused(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        done = cli("work", "delegate", "--grant-ref", GRANT,
                   "--context-ref", CONTEXT,
                   "--valid-from", "2026-09-01T00:00:00+00:00",
                   "--expires-at", "2026-12-31T00:00:00+00:00",
                   "--wall-seconds", "30", "--max-attempts", "2",
                   "--max-output-bytes", "200000", env=env)
        assert done.returncode == 1
        assert "--item is required" in done.stderr
        assert "wake" in done.stderr
        assert recorded_grant(env) is None

    def test_a_grant_with_no_window_is_refused(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        done = cli("work", "delegate", "--grant-ref", GRANT,
                   "--context-ref", CONTEXT, "--item", ITEM,
                   "--expires-at", "2026-12-31T00:00:00+00:00",
                   "--wall-seconds", "30", "--max-attempts", "2",
                   "--max-output-bytes", "200000", env=env)
        assert done.returncode == 1
        assert "--valid-from is required" in done.stderr
        assert recorded_grant(env) is None

    def test_every_budget_key_is_required(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        complete = {"--wall-seconds": "30", "--max-attempts": "2",
                    "--max-output-bytes": "200000"}
        for dropped in complete:
            args: list[str] = []
            for flag, value in complete.items():
                if flag != dropped:
                    args += [flag, value]
            with_args = cli("work", "delegate", "--grant-ref", GRANT,
                            "--context-ref", CONTEXT, "--item", ITEM,
                            "--valid-from", "2026-09-01T00:00:00+00:00",
                            "--expires-at", "2026-12-31T00:00:00+00:00",
                            *args, env=env)
            assert with_args.returncode == 1, (dropped, with_args.stdout)
            assert dropped in with_args.stderr, (dropped, with_args.stderr)
            assert "wake" in with_args.stderr


# ---------------------------------------------------------------------------
# 4. Write-once, and ending is not extending.
# ---------------------------------------------------------------------------


class TestTheWriteIsOnceAndRevocationIsFinal:
    def test_a_second_identical_report_returns_the_first_record(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        first = delegate(env)
        assert first.returncode == 0, first.stderr
        second = delegate(env)
        assert second.returncode == 0, second.stderr
        assert (json.loads(second.stdout)["grant"]
                == json.loads(first.stdout)["grant"])

    def test_a_second_report_with_a_wider_scope_is_refused(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        wider = delegate(env, "--item", "SRV-0002")
        assert wider.returncode == 1, wider.stdout
        # The stored record is the first one, whole: a refusal that had already
        # widened the stored scope would be the failure this settles.
        assert recorded_grant(env)["items"] == [ITEM]

    def test_a_revoke_keeps_the_first_instant(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        first = cli("work", "revoke", "--json", "--grant-ref", GRANT,
                    "--reason", "the objective was withdrawn", "--at",
                    "2026-09-26T12:00:00+00:00", env=env)
        assert first.returncode == 0, first.stderr
        second = cli("work", "revoke", "--json", "--grant-ref", GRANT,
                     "--reason", "asking again later", "--at",
                     "2026-09-27T12:00:00+00:00", env=env)
        assert second.returncode == 0, second.stderr
        grant = recorded_grant(env)
        assert grant["revoked"] is True
        # «When did the authority end» is answered by the first instant, not by
        # the last time somebody mentioned it.
        assert grant["revoked_at"] == "2026-09-26T12:00:00+00:00"
        assert grant["revoked_reason"] == "the objective was withdrawn"

    def test_a_grant_this_node_never_recorded_is_not_revocable(self, tmp_path):
        env = env_for(tmp_path)
        done = cli("work", "revoke", "--grant-ref", "grant:nobody",
                   "--reason", "cleanup", env=env)
        assert done.returncode == 1
        assert "is not a grant this node recorded" in done.stderr

    def test_a_revoke_with_no_reason_is_refused(self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        done = cli("work", "revoke", "--grant-ref", GRANT, env=env)
        assert done.returncode == 1
        assert "--reason is required" in done.stderr
        assert recorded_grant(env).get("revoked") is not True


# ---------------------------------------------------------------------------
# 5. The producer reaches the consumer.
# ---------------------------------------------------------------------------


class TestTheGrantServerosctlWroteIsTheGrantTheWakeReads:
    def test_wake_authority_carries_a_wake_under_it(self, tmp_path):
        """The seam §5 names, closed end to end.

        Every test above this line would pass if `work delegate` wrote a record
        into a store nothing reads. This one hands the store's OWN dicts to the
        function the continuation routine asks, and requires the grant back.
        """
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        store = AgentControlStore(store_path(env))
        report = wake.authority(store.grants(), store.contexts(),
                                item=ITEM, now=NOW)
        assert report["authorizing"] == [GRANT], report
        assert report["envelopes"], "an authorizing grant with no envelope"

    def test_a_grant_naming_another_item_is_authority_that_does_not_cover(
            self, tmp_path):
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        store = AgentControlStore(store_path(env))
        report = wake.authority(store.grants(), store.contexts(),
                                item="SRV-9999", now=NOW)
        assert report["authorizing"] == []
        assert report["not_covering"] == [GRANT]

    def test_after_a_revoke_the_wake_is_refused_again(self, tmp_path):
        """§3's «revoke must stop authorizing», asserted at the consumer."""
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        store = AgentControlStore(store_path(env))
        assert wake.authority(store.grants(), store.contexts(),
                              item=ITEM, now=NOW)["authorizing"] == [GRANT]

        assert cli("work", "revoke", "--grant-ref", GRANT,
                   "--reason", "withdrawn", env=env).returncode == 0
        store = AgentControlStore(store_path(env))
        report = wake.authority(store.grants(), store.contexts(),
                                item=ITEM, now=NOW)
        assert report["authorizing"] == []
        # It is not «nothing was ever delegated»: the record is still there, and
        # the refusal names WHICH of the two it is. The row's `state` stays
        # `AUTHORIZED` because that is `context/delegation.derivation`'s verdict
        # and its docstring assigns `revoked` to `standing_reason` rather than to
        # itself — so the standing judgement is reported beside it and never
        # merged into it.
        assert report["not_standing"] == [
            {"grant_ref": GRANT, "because": "MISSING_OR_REVOKED_GRANT"}]
        assert report["rows"], "the revoked grant vanished instead of ending"
        assert report["rows"][0]["revoked"] is True

    def test_a_context_that_grew_stale_stops_authorizing_its_grant(self, tmp_path):
        """The delegation is re-read at the instant of the wake, not at write.

        The grant is recorded while the context is fresh and judged after it has
        gone stale. Recording it must therefore SUCCEED — the store holds no
        clock — and the refusal must arrive from the reader, which is where the
        delegation's own contract puts it.
        """
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        # `context/actor-context-contract.yaml#freshness`: FRESH for 300s from
        # `observed_at`, EXPIRED at 900s. Two hours is unambiguously the latter.
        store = AgentControlStore(store_path(env))
        report = wake.authority(store.grants(), store.contexts(),
                                item=ITEM, now="2026-09-26T12:00:00+00:00")
        assert report["authorizing"] == []
        assert report["rows"]

    def test_plan_wakes_under_it_and_refuses_after_the_revoke(self, tmp_path):
        """The whole chain in one test: a person records, the routine wakes.

        This is the row §5 is about. `authority` is the judgement; `plan` is what
        a timer and a person at a terminal both read, and `WAKE` here is the only
        value that ever starts a Parent. The second half is the reason §3's
        «revoke must stop authorizing» had to be asserted at THIS level: a
        revoked grant is still a recorded grant, and a routine that read only the
        record would wake on authority a person had already ended.
        """
        env = env_for(tmp_path)
        seed_context(env)
        assert delegate(env).returncode == 0
        a_checkpoint(env)

        before = wake.plan(AgentControlStore(store_path(env)), now=NOW)
        assert [row["action"] for row in before["rows"]] == ["WAKE"], before["rows"]
        assert before["would_wake"] == [before["rows"][0]["checkpoint_ref"]]

        assert cli("work", "revoke", "--grant-ref", GRANT,
                   "--reason", "the objective was withdrawn", env=env).returncode == 0

        after = wake.plan(AgentControlStore(store_path(env)), now=NOW)
        row = after["rows"][0]
        assert row["action"] == "REFUSE", row
        assert row["reason"] == "NO_AUTHORIZING_GRANT"
        assert after["would_wake"] == []
        # Named, and named as the standing refusal rather than as an absent
        # grant: «nothing was ever delegated» and «the delegation was ended»
        # send a reader to two different producers.
        assert GRANT in row["says"]
        assert "MISSING_OR_REVOKED_GRANT" in row["says"]
