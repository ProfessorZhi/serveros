#!/usr/bin/env python3
"""What a woken Parent may do, and what each refusal is for.

WHY THIS FILE EXISTS
--------------------
`runtime/wake.py` starts a Parent and files a capability envelope beside the
invocation receipt. The envelope is a BINDING — it says which wake is calling,
which Item and epoch it is for, and which operations it was checked for — and
`runtime/continuation_control.py` is the half that reads it back and answers
ALLOW / DENY / UNASKABLE before anything is dispatched.

The whole point of that second half is that the document is not the authority:
a grant revoked one second after a wake would still authorize everything the
wake went on to do if the envelope were believed, and the only way to withdraw
authority would be to kill the process. So every check here is made from the
store AS IT STANDS, and the tests that matter are the ones where the store has
MOVED since the envelope was filed.

Six properties, in the order a reader needs them:

  1. THE TWO HALVES AGREE ON ONE VOCABULARY, AND NEITHER CAN DRIFT ALONE. The
     operation list belongs to the issuer; this module imports it rather than
     restating it, and a table that grew a permission its issuer does not have
     would be widening a capability from the reading side. `TestTheTwoHalvesAgree`
     is the comparison, because a module cannot see its own drift.

  2. A DOCUMENT THAT IS NOT AN ENVELOPE IS REFUSED, NOT DEFAULTED. And the
     surface built on this module refuses to START without one, which is
     `tests/test_continuation_mcp.py`'s first test. A reader that answered
     without an envelope would be answering for whichever wake asked.

  3. THE FIVE THINGS THAT ARE TRUE OF EVERY OPERATION ARE CHECKED BEFORE THE
     OPERATION'S OWN TARGET. Identity before authority, and both before the
     verb — so a call naming the wrong Item is reported as a call naming the
     wrong Item and not as a grant that has expired. `TestTheFiveGenericChecks`
     walks them in order and asserts the ORDER, not only the answers.

  4. THE OPERATION'S TARGET IS WHAT EACH ONE DIFFERS BY, and the two child
     operations share ONE predicate. That sharing is the design and not an
     economy: if a child ref were readable but not receivable, a Parent would
     read another scope's result and only then be told the ref was never its
     own — and the reading is the leak.
     `TestOnePredicateGovernsReadingAndReceiving` is the test that fails if
     somebody splits them.

  5. UNASKABLE IS NOT A SOFT DENY. Three separations are asserted and each of
     them is a real confusion this fabric has paid for: `verify` without a
     completion reader, an envelope whose expiry will not parse, and a grant
     whose window will not parse. In every case the answer is «this module could
     not look», and printing it as DENY would tell a person their authority was
     withdrawn when nobody checked. `runtime/ownership_gate.py` keeps the same
     distinction between `EXIT_DENY` and `EXIT_UNASKABLE`.

  6. `work.complete` IS NAMED AND UNIMPLEMENTABLE, AND THE REASON IS MEASURED.
     Completion in this fabric is not a record: it is a verdict composed from
     the Item's document chain by `intake/workchain.completion_of` and READ by
     `serverosctl work item`. `TestCompletionIsAVerdictAndNotARecord` asserts
     both halves — that the verb family has no `complete` and that the store has
     no completions collection — so that the sentence in `UNSUPPORTED_BECAUSE`
     is a measurement and not a belief.

Hermetic: a temporary directory per test, no daemon, no socket, no clock. Every
instant is an argument, for `derivation`'s reason — a verdict computed from a
clock the caller cannot see is a verdict nobody can reproduce.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The same module names `tests/test_wake.py` uses, and deliberately: pytest runs
# both files in one process, and two module objects named differently would give
# `derivation` and `CONTINUATION_OPERATIONS` two homes — which is the one thing
# the modules being compared here exist to prevent.
store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation",
                     RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")
cc = _load("serveros_runtime_continuation_control",
           RUNTIME / "continuation_control.py")

AgentControlStore = store_mod.AgentControlStore

NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
TOMORROW = "2026-09-27T10:00:00+00:00"
# A window that has ALREADY closed at `NOW`, and a write clock before it: the
# pair is `tests/test_wake.py`'s, because `build_checkpoint` refuses to save a
# window that has already passed — «a window that has already passed is not a
# wait» — and this file needs records a Parent could really have written.
CLOSED = "2026-09-26T09:00:00+00:00"
WRITTEN = "2026-09-26T08:00:00+00:00"
# A revocation INSIDE the wake's own window. It has to be inside: an envelope
# filed at `NOW` with a 900-second wall budget stops being in force at 10:15, and
# a reading after that is refused by `ENVELOPE_IN_FORCE` before anything looks at
# the grant — which is correct and is not what a grant-revocation test is about.
REVOKED_AT = "2026-09-26T10:05:00+00:00"
ITEM = "SRV-0001"
OTHER_ITEM = "SRV-0002"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
BUDGET = {"wall_seconds": 900, "max_attempts": 2, "max_output_bytes": 200000}
KEY = "continuation-wake-SRV-0001-e1-a1"
SCOPE = "workspace/records"


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def a_context(**over) -> dict:
    """A trusted context that authorizes at `NOW`.

    `context/actor.authorizes` wants FRESH + FACT + VERIFIED, and FRESH is
    computed from `observed_at` against the reading clock — so `observed_at` is
    `NOW` and every test here reads at `NOW`. A test that wants a refusal moves
    one of the two and says which.
    """
    record = {"context_ref": CONTEXT, "actor_ref": "human:hwz",
              "provenance": "FACT", "provider_status": "VERIFIED",
              "observed_at": NOW, "expires_at": "2026-12-31T00:00:00+00:00"}
    record.update(over)
    return record


def a_grant(**over) -> dict:
    """A standing delegation naming BOTH Items, and the second one is not noise.

    The child-ownership tests need a cycle admitted for `OTHER_ITEM` to exist at
    all, and `AgentControlStore.admit` checks each selected Item against this
    list — a gap that is the store's question and not the engine's, which never
    reads `items`. Narrowing the list here would make those tests fail in the
    store's admission gate while looking like the engine refusing them, which is
    the failure mode a fixture must not have.
    """
    record = {"grant_ref": GRANT, "delegated_by": CONTEXT,
              "items": [ITEM, OTHER_ITEM],
              "actions": [], "revoked": False,
              "valid_from": "2026-09-01T00:00:00+00:00",
              "expires_at": "2026-12-31T00:00:00+00:00",
              "budget": dict(BUDGET)}
    record.update(over)
    return record


def a_store(tmp_path: Path, *, context=None, grant=None) -> AgentControlStore:
    store = AgentControlStore(tmp_path / "store.json")
    store.put_context(a_context() if context is None else context)
    store.put_grant(a_grant() if grant is None else grant)
    return store


def rewrite_store(store: AgentControlStore, mutate) -> None:
    """Edit the store document under the API, for a state the API will not make.

    Two checks in the engine guard states `AgentControlStore` refuses to
    produce: a live record whose `work_item` moved, and two live records of one
    Item at different epochs. `put_checkpoint` will not replace a live scoped
    record, and a higher epoch SUPERSEDES the lower one in the same transaction
    — so neither state is reachable by calling the store, and both are written
    here by editing the document, which is exactly what a store file edited by
    another revision or another process looks like.

    This is honest as a test and only as a test: it says the guard is a guard
    rather than a path, and it is the shape the guard exists for. `_read()`
    keeps no cache, so what is written here is what the next reader sees.
    """
    doc = json.loads(store.path.read_text(encoding="utf-8"))
    mutate(doc)
    store.path.write_text(json.dumps(doc), encoding="utf-8")


def a_checkpoint(store: AgentControlStore, *, work_item: str = ITEM, epoch: int = 1,
                 child_refs=("cyc-1",), **over) -> dict:
    """One live checkpoint, waiting on a window so the record is valid alone.

    The child refs go in through `child_attempts`, which is the field the
    envelope reads to build `allowed_child_refs`. `waiting_on` is a window
    because a `kind: child` wait must name refs this store holds an admission
    for, and the tests that are not about children should not have to seed one.
    """
    record = continuation.build_checkpoint(
        work_item=work_item, epoch=epoch, parent_owner="parent-a",
        next_safe_action="carry the Item on from the accepted evidence",
        waiting_on={"kind": "window", "until": CLOSED}, now=WRITTEN,
        requirement_revision="REQ-2026-09-20",
        child_attempts=list(child_refs), write_scope=[SCOPE])
    record.update(over)
    return store.put_checkpoint(record)


def an_admission(store: AgentControlStore, ref: str = "cyc-1",
                 work_item: str = ITEM) -> None:
    """One admission under `ref`, for the Item the child checks compare against."""
    store.admit({
        "cycle_ref": ref, "grant_ref": GRANT,
        "selection": {"selection_digest": "a" * 64, "observed_at": NOW,
                      "selected": [work_item]},
        "reservation": dict(BUDGET),
        "lease": {"resource": "browser:chromium", "scope": "session",
                  "holder": ref},
        "action_key": "act-" + ref, "admitted_at": NOW})


def a_wake(store: AgentControlStore, *, attempt: int = 1, key: str = KEY,
           now: str = NOW, work_item: str = ITEM, epoch: int = 1, **over) -> dict:
    """The envelope `runtime/wake.py` would have filed for this checkpoint.

    The record is looked up by Item and epoch rather than taken as «the first
    one», because several tests below put a SECOND checkpoint in the same store
    — a higher epoch, a sibling Item — and an envelope built from whichever
    record happened to be inserted first would make those tests pass for a
    reason nobody wrote down.
    """
    matches = [r for r in store.checkpoints().values()
               if r.get("work_item") == work_item and r.get("epoch") == epoch]
    assert len(matches) == 1, f"expected one record for {work_item}#{epoch}"
    envelope = wake.capability_envelope(
        matches[0], store.grants()[GRANT], GRANT, attempt=attempt, key=key,
        spend={"wall_seconds": BUDGET["wall_seconds"], "attempts_allowed": 2},
        now=now)
    envelope.update(over)
    return envelope


def a_completion(state: str = "OPEN", why: str = "two requirements are open"):
    """A completion reader, standing in for `serverosctl work item`'s chain.

    A callable and not a value, because the engine calls it per verdict: the
    boundary is composed from the Item's documents and is not a field anybody
    caches.
    """
    return lambda item_id: {"item": item_id, "state": state, "why": why}


def ask(envelope, store, operation, arguments=None, *, now: str = NOW,
        completion=None, contexts=None) -> dict:
    return cc.verify(
        envelope=envelope, store=store,
        contexts=contexts if contexts is not None else store.contexts(),
        operation=operation, arguments=arguments or {}, now=now,
        completion_of_item=completion or a_completion())


def denied(verdict: dict, check: str) -> bool:
    """`verdict` is a DENY whose FIRST refusal is `check`.

    The first and not any, because the whole ordering claim in this module is
    that the most binding refusal is the one reported: a verdict that also
    contains the right check further down was decided by something else.
    """
    first = [c for c in verdict["checks"] if c["decision"] != "ALLOW"]
    return bool(first) and first[0]["check"] == check


# ---------------------------------------------------------------------------
# 1. The two halves agree on one vocabulary.
# ---------------------------------------------------------------------------


class TestTheTwoHalvesAgree:
    def test_the_issuers_vocabulary_and_this_tables_are_the_same_set(self):
        """The comparison the module's own docstring says is made here.

        A reader that grew an operation of its own would be widening a
        capability; one that lost an operation would be narrowing authority the
        issuer granted. Neither is visible from `OPERATIONS` alone.
        """
        issued = set(cc.issuer_operations()) | set(cc.issuer_unsupported())
        assert issued == set(cc.OPERATIONS), (
            "the envelope's vocabulary and this module's table have drifted")

    def test_the_unsupported_name_is_declared_by_the_issuer_and_not_invented(self):
        assert cc.issuer_unsupported() == wake.CONTINUATION_OPERATIONS_UNSUPPORTED
        assert "work.complete" in cc.issuer_unsupported()
        assert "work.complete" not in cc.issuer_operations()

    def test_the_envelope_variable_is_the_one_the_receiver_sets(self):
        """Two spellings of one name is how a surface starts reading a variable
        nobody sets. The literal exists so `load_envelope` can quote it in a
        message without loading the receiver to explain itself."""
        assert cc.ENVELOPE_ENV_NAME == wake.ENVELOPE_ENV

    def test_every_operation_that_mutates_names_the_verb_that_owns_it(self):
        """`owner` is a claim about where the mutation is implemented, and a
        claim nothing checks is prose. The file it names must exist, and the
        verb must be one the CLI actually registers."""
        source = (RUNTIME / "cli.py").read_text()
        for name, spec in cc.OPERATIONS.items():
            if not spec["mutates"]:
                assert spec["owner"], name
                continue
            if spec["owner"] is None:
                continue  # `work.complete`: the absence IS the claim
            module = spec["owner"].split(" ")[0]
            assert (REPO / module).exists(), spec["owner"]
            verb = name.split(".", 1)[1]
            assert re.search(r'add_parser\(\s*\n?\s*"' + verb + r'"', source), (
                f"{name} names a verb the CLI does not register")

    def test_the_two_read_operations_are_the_only_ones_that_do_not_mutate(self):
        """The classification decides whether the target rules run at all, so
        it is asserted rather than read off the table it sits in."""
        reads = {n for n, s in cc.OPERATIONS.items() if not s["mutates"]}
        assert reads == {"continuation.inspect", "child-result.inspect"}

    def test_the_decisions_are_the_three_the_gate_distinguishes(self):
        assert cc.DECISIONS == ("ALLOW", "DENY", "UNASKABLE")


# ---------------------------------------------------------------------------
# 2. A document that is not an envelope is refused, not defaulted.
# ---------------------------------------------------------------------------


class TestAnEnvelopeThatIsNotOne:
    def test_a_path_that_is_not_absolute_is_refused(self):
        with pytest.raises(cc.BadEnvelope, match="not absolute"):
            cc.load_envelope("relative/envelope.json")

    def test_a_path_with_no_file_is_refused_and_names_the_variable(self, tmp_path):
        with pytest.raises(cc.BadEnvelope) as exc:
            cc.load_envelope(tmp_path / "absent.json")
        assert cc.ENVELOPE_ENV_NAME in str(exc.value)

    def test_a_document_with_another_schema_is_refused(self, tmp_path):
        path = tmp_path / "envelope.json"
        path.write_text('{"schema": "serveros/checkpoint.v1"}')
        with pytest.raises(cc.BadEnvelope, match="continuation-envelope"):
            cc.load_envelope(path)

    def test_a_document_that_is_not_a_mapping_is_refused(self, tmp_path):
        path = tmp_path / "envelope.json"
        path.write_text("[1, 2, 3]")
        with pytest.raises(cc.BadEnvelope):
            cc.load_envelope(path)

    def test_the_real_envelope_this_repo_files_is_accepted(self, tmp_path):
        """The positive control for the four above: the loader is not refusing
        everything."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        path = wake.envelope_path(tmp_path, KEY)
        path.write_text(json.dumps(envelope, indent=2))
        assert cc.load_envelope(path)["action_key"] == KEY


# ---------------------------------------------------------------------------
# 3. The five things true of every operation, and the order they are asked in.
# ---------------------------------------------------------------------------


class TestTheFiveGenericChecks:
    def test_an_operation_the_wake_does_not_carry_is_refused_by_its_name(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store, allowed_operations=["continuation.inspect"])
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert verdict["decision"] == "DENY"
        assert denied(verdict, "OPERATION_NOT_IN_ENVELOPE")
        assert "work.park" in verdict["checks"][-1]["because"]
        assert verdict["checks"][-1]["allowed_operations"] == ["continuation.inspect"]

    def test_an_operation_named_unsupported_is_refused_with_the_reason(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.complete")
        assert verdict["decision"] == "DENY"
        assert denied(verdict, "OPERATION_NAMED_UNSUPPORTED")
        assert cc.UNSUPPORTED_BECAUSE in verdict["checks"][-1]["because"]

    def test_the_operation_check_comes_before_everything_that_reads_the_store(
            self, tmp_path):
        """Order, not only answer. A wake that asked for an operation it does
        not carry must not be told anything about a grant — the refusal is about
        the request and is decidable without the store."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store, allowed_operations=[])
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert [c["check"] for c in verdict["checks"]] == ["OPERATION_NOT_IN_ENVELOPE"]

    def test_an_expired_wake_is_refused_before_anything_but_its_own_window(
            self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store, expires_at=NOW, expires_because="wall_budget")
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "ENVELOPE_EXPIRED")
        assert "wall_budget" in verdict["checks"][-1]["because"]

    def test_an_envelope_whose_expiry_will_not_parse_is_unaskable_not_denied(
            self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store, expires_at="soon")
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert verdict["decision"] == "UNASKABLE"
        assert denied(verdict, "ENVELOPE_EXPIRES_UNREADABLE")

    def test_a_checkpoint_the_store_no_longer_holds_ends_the_wake(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        # A store with the envelope's records removed, which is what «the record
        # is gone» looks like from this side.
        emptied = AgentControlStore(tmp_path / "other.json")
        emptied.put_context(a_context())
        emptied.put_grant(a_grant())
        verdict = ask(envelope, emptied, "work.park", {"reason": "stop"})
        assert denied(verdict, "CHECKPOINT_NOT_IN_STORE")

    def test_a_checkpoint_that_moved_to_another_item_is_not_this_wake(self, tmp_path):
        """The SAME ref, now recording a different Item.

        `build_checkpoint` derives the ref from the Item and the epoch, and
        `put_checkpoint` will not replace a live scoped record, so the store
        cannot produce this state — `rewrite_store` writes it, which is what a
        document edited by another revision or another process would be. The
        point is that the ref a wake holds still points at a record, and the
        record is no longer the wake's.
        """
        store = a_store(tmp_path)
        record = a_checkpoint(store)
        envelope = a_wake(store)
        rewrite_store(store, lambda doc: doc["checkpoints"].update(
            {record["checkpoint_ref"]: {**record, "work_item": OTHER_ITEM}}))
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "CHECKPOINT_IDENTITY_CHANGED")
        assert OTHER_ITEM in verdict["checks"][-1]["because"]

    def test_a_superseded_checkpoint_is_no_longer_live(self, tmp_path):
        store = a_store(tmp_path)
        record = a_checkpoint(store, epoch=1)
        envelope = a_wake(store)
        a_checkpoint(store, epoch=2, parent_owner="parent-b")  # same scope, higher
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "CHECKPOINT_NOT_LIVE")
        assert record["checkpoint_ref"] in verdict["checks"][-1]["because"]
        assert "superseded by" in verdict["checks"][-1]["because"]

    def test_a_parked_checkpoint_is_no_longer_live(self, tmp_path):
        store = a_store(tmp_path)
        record = a_checkpoint(store)
        envelope = a_wake(store)
        store.park_checkpoint(record["checkpoint_ref"], reason="set down", by="ops",
                              at=NOW)
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "CHECKPOINT_NOT_LIVE")
        assert "parked at" in verdict["checks"][-1]["because"]

    def test_a_grant_the_store_no_longer_holds_ends_the_wake(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store, grant_ref="grant-that-was-never-recorded")
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "GRANT_NOT_IN_STORE")

    def test_a_revoked_grant_refuses_whatever_the_envelope_still_says(self, tmp_path):
        """THE TEST THIS MODULE EXISTS FOR. The envelope was filed while the
        grant was live and says so; the store says otherwise, and the store is
        what decides."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        assert envelope["grant_expires_at"] == "2026-12-31T00:00:00+00:00"
        store.revoke_grant(GRANT, at=REVOKED_AT, reason="the person went home")
        verdict = ask(envelope, store, "work.park", {"reason": "stop"},
                      now=REVOKED_AT)
        assert verdict["decision"] == "DENY"
        assert denied(verdict, "GRANT_REVOKED")
        because = verdict["checks"][-1]["because"]
        assert "the person went home" in because and REVOKED_AT in because

    def test_a_grant_whose_window_will_not_parse_is_unaskable(self, tmp_path):
        """Stored that way, not edited into that way: `put_grant` does not read
        the window, so the unreadable grant is the grant the store was opened
        with and `a_wake` then files an envelope from it — which is the point,
        because the envelope is filed before anything reads the dates."""
        store = a_store(tmp_path, grant=a_grant(expires_at="whenever"))
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert verdict["decision"] == "UNASKABLE"
        assert denied(verdict, "GRANT_WINDOW_UNREADABLE")

    def test_a_grant_that_has_not_started_yet_or_has_ended_refuses(self, tmp_path):
        """The same shape one case over: a grant whose window is perfectly
        readable and does not contain `NOW`."""
        store = a_store(tmp_path, grant=a_grant(
            valid_from="2026-10-01T00:00:00+00:00",
            expires_at="2026-11-01T00:00:00+00:00"))
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.park", {"reason": "stop"})
        assert denied(verdict, "GRANT_NOT_CURRENT")

    def test_a_delegation_that_no_longer_authorizes_ends_the_wake(self, tmp_path):
        """The grant is live and the context behind it is not. A context ages
        out of the window that authorizes mutations while the grant's own dates
        are still open, and this is the check that reads the strong question
        rather than the display one."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        stale = store.contexts()
        stale[CONTEXT] = a_context(observed_at="2026-09-20T00:00:00+00:00")
        verdict = ask(envelope, store, "work.park", {"reason": "stop"},
                      contexts=stale)
        assert denied(verdict, "DELEGATION_DOES_NOT_AUTHORIZE")
        assert verdict["checks"][-1]["derivation_state"] == \
            "DELEGATION_DOES_NOT_AUTHORIZE"

    def test_a_completion_boundary_that_is_reached_ends_the_wake(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.park", {"reason": "stop"},
                      completion=a_completion("COMPLETE", "every requirement met"))
        assert denied(verdict, "ITEM_COMPLETION_BOUNDARY_REACHED")
        assert "a person's decision" in verdict["checks"][-1]["because"]

    def test_a_completion_boundary_that_cannot_be_composed_is_unaskable(self, tmp_path):
        """A store problem and an unfinished Item are different, and a wake that
        read one as the other would either stop work that should continue or
        continue work on a finished Item."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        blind = lambda item_id: {"unreadable": "the boards are not on this node"}
        verdict = ask(envelope, store, "work.park", {"reason": "stop"},
                      completion=blind)
        assert verdict["decision"] == "UNASKABLE"
        assert denied(verdict, "COMPLETION_UNREADABLE")

    def test_the_five_checks_all_hold_on_a_wake_that_should_be_allowed(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "continuation.inspect")
        assert verdict["decision"] == "ALLOW"
        passed = [c["check"] for c in verdict["checks"]]
        assert passed[:6] == [
            "OPERATION_IN_ENVELOPE", "ENVELOPE_IN_FORCE",
            "CHECKPOINT_IS_THIS_WAKE", "CHECKPOINT_LIVE",
            "GRANT_IN_FORCE", "DELEGATION_AUTHORIZES"]
        assert passed[6] == "COMPLETION_BOUNDARY_OPEN"


# ---------------------------------------------------------------------------
# 4. Each operation's own target, and the one predicate the two child
#    operations share.
# ---------------------------------------------------------------------------


class TestTheTargetIsWhatTheOperationsDifferBy:
    def test_a_read_returns_before_any_target_rule_runs_and_says_so(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "continuation.inspect")
        assert verdict["checks"][-1]["check"] == "READ_OPERATION"
        assert verdict["checks"][-1]["decision"] == "ALLOW"

    def test_a_checkpoint_inside_the_recorded_scope_is_allowed(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.checkpoint",
                      {"item": ITEM, "next_action": "carry on",
                       "write_scope": [SCOPE + "/litellm"]})
        assert verdict["decision"] == "ALLOW"
        assert denied(verdict, "READ_OPERATION") is False

    def test_a_write_scope_outside_the_envelope_is_refused_by_name(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.checkpoint",
                      {"item": ITEM, "next_action": "carry on",
                       "write_scope": ["/etc"]})
        assert denied(verdict, "WRITE_SCOPE_OUTSIDE_ENVELOPE")
        assert "widening it is not a continuation" in verdict["checks"][-1]["because"]

    def test_the_scope_match_is_a_prefix_of_a_segment_and_not_of_a_string(
            self, tmp_path):
        """`workspace/records-elsewhere` must NOT be read as inside
        `workspace/records`. A boundary that a sibling directory can satisfy by
        sharing a first N characters is not a boundary."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.checkpoint",
                      {"item": ITEM, "next_action": "carry on",
                       "write_scope": ["workspace/records-elsewhere"]})
        assert denied(verdict, "WRITE_SCOPE_OUTSIDE_ENVELOPE")

    def test_an_empty_scope_ask_is_not_an_outside_ask(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.checkpoint",
                      {"item": ITEM, "next_action": "carry on"})
        assert verdict["decision"] == "ALLOW"

    def test_a_checkpoint_aimed_at_another_item_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.checkpoint",
                      {"item": OTHER_ITEM, "next_action": "carry on"})
        assert denied(verdict, "TARGET_ITEM_IS_NOT_THIS_WAKE")

    def test_a_park_aimed_at_a_ref_the_store_does_not_hold_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "work.park",
                      {"checkpoint_ref": "SRV-9999#1", "reason": "stop"})
        assert denied(verdict, "PARK_TARGET_NOT_IN_STORE")

    def test_a_park_aimed_at_another_items_checkpoint_is_refused(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        theirs = a_checkpoint(store, work_item=OTHER_ITEM, epoch=1)
        verdict = ask(envelope, store, "work.park",
                      {"checkpoint_ref": theirs["checkpoint_ref"], "reason": "stop"})
        assert denied(verdict, "PARK_TARGET_IS_NOT_THIS_ITEM")

    def test_a_park_aimed_at_another_epoch_of_this_item_is_refused(self, tmp_path):
        """The Item is the same and the epoch is not, and taking another
        epoch's record down takes the work away from a wake that is not this
        one.

        BOTH RECORDS HAVE TO BE LIVE FOR THIS RULE TO BE THE ONE THAT ANSWERS,
        and the store will not leave two of one Item live: writing epoch 2
        supersedes epoch 1 inside the same transaction, so `CHECKPOINT_NOT_LIVE`
        fires first and this check is never reached. The state is written by
        putting epoch 2 and then restoring epoch 1's `superseded_by` in the
        document — `rewrite_store`'s case exactly. The rule is therefore a guard
        against a document this store's own writer will not produce, and saying
        so is the difference between a check and a decoration.
        """
        store = a_store(tmp_path)
        lower = a_checkpoint(store, epoch=1)
        envelope = a_wake(store)
        higher = a_checkpoint(store, epoch=2, parent_owner="parent-b")
        rewrite_store(store, lambda doc: doc["checkpoints"][
            lower["checkpoint_ref"]].pop("superseded_by", None))
        verdict = ask(envelope, store, "work.park",
                      {"checkpoint_ref": higher["checkpoint_ref"], "reason": "stop"})
        assert denied(verdict, "PARK_TARGET_IS_ANOTHER_EPOCH")

    def test_an_operation_with_no_target_rule_is_a_loud_failure_and_not_a_pass(
            self, tmp_path):
        """The `else` branch is reachable only from a hand-made envelope that
        lists an operation the table has no rule for. It raises, because a
        verdict computed by falling through to ALLOW is the failure mode this
        whole module is built to avoid."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        invented = {**envelope, "allowed_operations": ["work.complete"],
                    "unsupported_operations": []}
        with pytest.raises(cc.BadEnvelope, match="no target rule"):
            ask(invented, store, "work.complete")


class TestOnePredicateGovernsReadingAndReceiving:
    """The two child operations must answer alike, and this is the test that
    fails if somebody gives one of them its own rule.

    WHY IT IS ONE PREDICATE. If a child ref were readable but not receivable, a
    Parent would dump another scope's result and only then be told the ref was
    never its own — and the reading is the leak. The refusal has to come first,
    on the same terms, for both.
    """

    CASES = [
        ("a ref this checkpoint does not name", "cyc-elsewhere", None),
        ("a ref with no admission in the store", "cyc-named-but-absent", ITEM),
        ("a ref whose admission belongs to another Item", "cyc-2", OTHER_ITEM),
    ]

    @pytest.mark.parametrize("label,ref,owner", CASES)
    def test_the_same_ref_gets_the_same_answer_from_both(
            self, tmp_path, label, ref, owner):
        store = a_store(tmp_path)
        named = ["cyc-1"] if ref != "cyc-elsewhere" else []
        a_checkpoint(store, child_refs=named)
        envelope = a_wake(store)
        if ref != "cyc-elsewhere" and owner is None:
            pass  # named but not admitted: nothing to seed
        elif owner is not None:
            an_admission(store, ref, work_item=owner)

        read = ask(envelope, store, "child-result.inspect", {"cycle": ref})
        receive = ask(envelope, store, "work.accept",
                      {"cycle": ref, "locator": "rev-1"})
        assert [c["check"] for c in read["checks"]] == \
               [c["check"] for c in receive["checks"]], label
        assert read["decision"] == receive["decision"], label

    def test_a_ref_the_checkpoint_names_with_no_admission_is_neither_readable_nor_receivable(
            self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store, child_refs=["cyc-1"])
        envelope = a_wake(store)
        for operation in ("child-result.inspect", "work.accept"):
            verdict = ask(envelope, store, operation,
                          {"cycle": "cyc-1", "locator": "rev-1"})
            assert denied(verdict, "CHILD_ADMISSION_NOT_IN_STORE"), operation

    def test_a_checkpoint_that_named_no_child_can_receive_none(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store, child_refs=())
        envelope = a_wake(store)
        assert envelope["allowed_child_refs"] == []
        verdict = ask(envelope, store, "work.accept",
                      {"cycle": "cyc-1", "locator": "rev-1"})
        assert denied(verdict, "CHILD_NOT_THIS_WAKE'S")
        assert "another scope's" in verdict["checks"][-1]["because"]

    def test_another_items_admitted_cycle_is_refused_even_when_the_ref_is_named(
            self, tmp_path):
        """The named check and the ownership check are separate, and this is the
        case that tells them apart: the checkpoint names the ref, the store holds
        an admission under it, and the admission belongs to somebody else."""
        store = a_store(tmp_path)
        a_checkpoint(store, child_refs=["cyc-2"])
        an_admission(store, "cyc-2", work_item=OTHER_ITEM)
        envelope = a_wake(store)
        assert envelope["allowed_child_refs"] == ["cyc-2"]
        for operation in ("child-result.inspect", "work.accept"):
            verdict = ask(envelope, store, operation,
                          {"cycle": "cyc-2", "locator": "rev-1"})
            assert denied(verdict, "CHILD_ADMISSION_IS_ANOTHER_ITEM"), operation

    def test_a_child_this_wake_owns_is_readable_and_receivable(self, tmp_path):
        """The positive control for the five refusals above."""
        store = a_store(tmp_path)
        a_checkpoint(store, child_refs=["cyc-1"])
        an_admission(store, "cyc-1")
        envelope = a_wake(store)
        read = ask(envelope, store, "child-result.inspect", {"cycle": "cyc-1"})
        assert read["decision"] == "ALLOW"
        # The ownership check is the LAST thing before a read stops, and the
        # read's own ALLOW is appended after it — so `[-1]` is `READ_OPERATION`
        # here and the ownership check is `[-2]`. Read by name rather than by
        # position, because the position is the thing under test.
        passed = {c["check"]: c["decision"] for c in read["checks"]}
        assert passed["CHILD_OWNED_BY_THIS_WAKE"] == "ALLOW"
        receive = ask(envelope, store, "work.accept",
                      {"cycle": "cyc-1", "locator": "rev-1"})
        assert receive["decision"] == "ALLOW"

    def test_a_child_wait_contributes_its_refs_to_the_same_set(self, tmp_path):
        """A Parent waiting on a child named only in `waiting_on` must be able
        to receive it: the envelope unions the two fields, and this asserts the
        union rather than the one field the fixtures happen to use."""
        store = a_store(tmp_path)
        an_admission(store, "cyc-1")
        record = continuation.build_checkpoint(
            work_item=ITEM, epoch=1, parent_owner="parent-a",
            next_safe_action="wait for the child", now=WRITTEN,
            waiting_on={"kind": "child", "refs": ["cyc-1"]},
            admissions=store.admissions())
        store.put_checkpoint(record)
        envelope = a_wake(store, child_refs=())
        assert envelope["allowed_child_refs"] == ["cyc-1"]
        assert ask(envelope, store, "child-result.inspect",
                   {"cycle": "cyc-1"})["decision"] == "ALLOW"


# ---------------------------------------------------------------------------
# 5. UNASKABLE is not a soft deny.
# ---------------------------------------------------------------------------


class TestUnaskableIsNotASoftDeny:
    def test_a_clock_that_is_not_an_instant_cannot_decide_anything(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "continuation.inspect", now="yesterday")
        assert verdict["decision"] == "UNASKABLE"
        assert "two instants" in verdict["because"]

    def test_a_naive_instant_is_refused_rather_than_assumed_local(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "continuation.inspect",
                      now="2026-09-26T10:00:00")
        assert verdict["decision"] == "UNASKABLE"

    def test_no_completion_reader_is_a_wiring_fault_and_not_a_fact(self, tmp_path):
        """A default here would have this module report a check as passed
        without having made it, which is the one thing worse than refusing."""
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = cc.verify(envelope=envelope, store=store,
                            contexts=store.contexts(),
                            operation="continuation.inspect", now=NOW)
        assert verdict["decision"] == "UNASKABLE"
        assert "wiring fault" in verdict["because"]

    def test_the_three_words_are_the_ones_the_gate_uses(self):
        assert set(cc.DECISIONS) == {"ALLOW", "DENY", "UNASKABLE"}


# ---------------------------------------------------------------------------
# 6. `work.complete` is named, unperformable, and the reason is measured.
# ---------------------------------------------------------------------------


class TestCompletionIsAVerdictAndNotARecord:
    def test_the_verb_family_has_no_complete(self):
        """Measured from the parser rather than believed: `work.complete` is in
        the envelope precisely because a Parent will look for it."""
        source = (RUNTIME / "cli.py").read_text()
        assert 'add_parser(\n        "complete"' not in source
        assert re.search(r'work_sub\.add_parser\(\s*\n?\s*"complete"', source) is None

    def test_the_control_store_has_no_completions_collection(self):
        store = AgentControlStore(Path("/tmp") / "never-written.json")
        methods = [m for m in dir(store) if "complet" in m.lower()]
        assert methods == [], methods

    def test_the_reason_the_engine_gives_names_where_the_answer_actually_is(self):
        assert "intake/workchain" in cc.UNSUPPORTED_BECAUSE
        assert "continuation.inspect" in cc.UNSUPPORTED_BECAUSE
        assert "COMPLETE" in cc.UNSUPPORTED_BECAUSE

    def test_a_verdict_carries_the_wake_it_decided_for(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        verdict = ask(envelope, store, "continuation.inspect")
        assert verdict["schema"] == cc.SCHEMA
        assert verdict["envelope_ref"] == KEY
        assert verdict["work_item"] == ITEM
        assert verdict["epoch"] == 1
        assert verdict["read_at"] == NOW


# ---------------------------------------------------------------------------
# The claim that this module holds no clock of its own.
# ---------------------------------------------------------------------------


class TestItHoldsNoClock:
    def test_every_instant_in_the_module_is_the_callers(self):
        """A verdict computed from a clock the caller cannot see is a verdict
        nobody can reproduce. `verify` takes `now` and there is no other source:
        the module imports no clock-reading name at all."""
        source = (RUNTIME / "continuation_control.py").read_text()
        assert "datetime.now" not in source
        assert "time.time" not in source
        assert "utcnow" not in source

    def test_reading_the_same_wake_twice_at_one_instant_gives_one_answer(self, tmp_path):
        store = a_store(tmp_path)
        a_checkpoint(store)
        envelope = a_wake(store)
        first = ask(envelope, store, "continuation.inspect")
        second = ask(envelope, store, "continuation.inspect")
        assert first == second
