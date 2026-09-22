"""Standing delegation: from a recorded grant to the envelope a cycle may carry.

WHY THIS FILE EXISTS
--------------------
`intake/focus_source.py` said, in its own words, that nothing on this node
mints a grant — "there is no grant store under `state/serveros/`" — and that
sentence was true until this round. `serverosctl work focus` took its envelope
from `--grants <file>`: a JSON object of scopes, budgets and windows that
`intake/daily_cycle.grant_reason` validated for scope, time and budget, in
exactly the words its own docstring uses — "not the authenticity of the
supplied grant". So the one gate that decides whether a cycle may carry an
objective could be opened by writing a file.

The property this file pins is the one that closes that: a grant authorizes
only while the trusted context it derives from still does. Four claims, and
each is asserted against the thing that would break it:

  1. A GRANT NEEDS A ROOT. `put_grant` refuses a `delegated_by` this store has
     never held a context for. Authority with no root is authority nobody
     granted, and no later clock can give it one — which is why this is the one
     refusal that belongs at the write rather than at the read.

  2. A GRANT CANNOT OUTLIVE ITS DELEGATION. The grant below carries
     `expires_at` a month out and its delegation goes STALE in five minutes; at
     the far clock it is excluded anyway. `context/actor.py` decides the second
     half and this file does not re-word its reason.

  3. THE RECORD IS WRITE-ONCE, AND REVOCATION IS NOT A FIELD A PUT CAN WRITE.
     A second put with a wider scope is refused; a put carrying `revoked: True`
     is refused by name; a re-report of a revoked grant returns the revocation
     rather than clearing it.

  4. THE STATES DO NOT COLLAPSE. "Nothing was delegated", "the delegation does
     not authorize any more", "the root is gone" and "a row names a ref nothing
     recorded" are four facts about three different objects, and a reader given
     one sentence for all four would be told to check the wrong producer.

WHAT IS NOT TESTED HERE
-----------------------
That a board row can name a grant. It cannot: the captured boards in
`tests/data/` carry `id`, `project`, `state`, `kind`, `title`, `priority` and
`grant_ref` is not among them, nor is it on any card. So the row below is built
by hand and says so, and the assertion that no real row carries one lives in
`tests/test_focus_source.py`. The gap between "the store can hold a grant" and
"a row can name one" is a missing card field, and this file does not paper over
it by inventing a producer.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
STORE_MODULE = REPO_ROOT / "runtime" / "agent_control_store.py"
MODULE = REPO_ROOT / "context" / "delegation.py"
DAILY_CYCLE = REPO_ROOT / "intake" / "daily_cycle.py"

CONTEXT_REF = "human-context:botmux-human-control:" + "a" * 32
# 2026-09-22T11:58:00Z observed, FRESH until +300s, EXPIRED at +900s
# (`context/actor-context-contract.yaml#freshness`).
OBSERVED_AT = "2026-09-22T11:58:00+00:00"
STALE_AT = "2026-09-22T12:05:00+00:00"
FRESH_AT = "2026-09-22T12:00:00+00:00"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load("serveros_runtime_agent_control_store", STORE_MODULE)
delegation = _load("serveros_context_delegation", MODULE)
cycle = _load("serveros_intake_daily_cycle", DAILY_CYCLE)

AgentControlStore = store_mod.AgentControlStore
StoreError = store_mod.StoreError


def a_context(**over) -> dict:
    record = {
        "context_ref": CONTEXT_REF,
        "actor_ref": "human:hwz",
        "provenance": "FACT",
        "provider_status": "VERIFIED",
        "observed_at": OBSERVED_AT,
        "expires_at": "2026-09-22T12:13:00+00:00",
    }
    record.update(over)
    return record


def a_grant(**over) -> dict:
    record = {
        "grant_ref": "grant:tokyo-2026-09-22",
        "delegated_by": CONTEXT_REF,
        "valid_from": "2026-09-21T00:00:00+00:00",
        "expires_at": "2026-10-21T00:00:00+00:00",
        "projects": ["litellm"],
        "items": ["LIT-0103"],
        "actions": ["research"],
        "scope_digests": {"LIT-0103": "sha256:" + "b" * 64},
        "budget": {"wall_seconds": 600, "max_attempts": 2,
                   "max_output_bytes": 65536, "exhausted": False},
    }
    record.update(over)
    return record


def an_item(**over) -> dict:
    item = {"id": "LIT-0103", "project": "litellm", "state": "READY",
            "scope_digest": "sha256:" + "b" * 64, "next_action": "research"}
    item.update(over)
    return item


def seeded(tmp_path: Path) -> tuple:
    """A store holding one context and one grant, from the reported context."""
    store = AgentControlStore(tmp_path / "store.json")
    store.put_context(a_context())
    store.put_grant(a_grant())
    return store


def report_for(store, now: str = FRESH_AT, named_by=()) -> dict:
    return delegation.envelopes(store.grants(), store.contexts(), now=now,
                                named_by=named_by)


def cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT),
                          env=env)


def data_root(tmp_path: Path, env_extra: dict | None = None) -> dict:
    """A fixture DATA_ROOT, so `paths` resolves the store inside the test."""
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    env = {"DATA_ROOT": str(data), "HOME": str(data),
           "WORKSPACE_ROOT": str(data / "workspace"),
           "ARTIFACTS_ROOT": str(data / "artifacts"),
           "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}
    env.update(env_extra or {})
    return env


# --------------------------------------------------------------------------
# 1. a grant needs a root
# --------------------------------------------------------------------------

class TestAGrantNeedsARoot:
    def test_a_grant_whose_delegation_was_never_reported_is_refused(self, tmp_path):
        store = AgentControlStore(tmp_path / "store.json")

        with pytest.raises(StoreError, match="no trusted context"):
            store.put_grant(a_grant())

    def test_the_refusal_records_nothing(self, tmp_path):
        store = AgentControlStore(tmp_path / "store.json")

        with pytest.raises(StoreError):
            store.put_grant(a_grant())

        assert store.grants() == {}

    def test_a_delegation_reported_after_the_open_store_is_seen(self, tmp_path):
        """The check reads the document, not the copy this instance loaded.

        A store opened before the context was reported holds an empty
        `contexts`. If `put_grant` checked its own copy, whether a grant landed
        would depend on which order two writers happened to run in — and the
        root of an authorization is not something to decide by race.
        """
        path = tmp_path / "store.json"
        early = AgentControlStore(path)
        AgentControlStore(path).put_context(a_context())

        assert early.put_grant(a_grant())["delegated_by"] == CONTEXT_REF

    def test_a_grant_with_no_name_is_refused(self, tmp_path):
        store = seeded(tmp_path)

        with pytest.raises(StoreError, match="without a grant_ref"):
            store.put_grant(a_grant(grant_ref=""))

    def test_a_grant_that_names_no_delegation_is_refused(self, tmp_path):
        store = seeded(tmp_path)

        with pytest.raises(StoreError, match="without a delegated_by"):
            store.put_grant(a_grant(delegated_by=""))


# --------------------------------------------------------------------------
# 2. a grant cannot outlive its delegation
# --------------------------------------------------------------------------

class TestAGrantCannotOutliveItsDelegation:
    """The property `--grants <file>` could not have: authority rooted in evidence."""

    def test_the_same_grant_authorizes_then_does_not_as_the_clock_moves(self, tmp_path):
        """`expires_at` a month out, and the delegation goes STALE in five minutes."""
        store = seeded(tmp_path)
        assert store.get_grant("grant:tokyo-2026-09-22")["expires_at"] > STALE_AT

        assert report_for(store, now=FRESH_AT)["summary"]["authorizing"] == 1
        after = report_for(store, now=STALE_AT)

        assert after["summary"]["authorizing"] == 0
        assert after["rows"][0]["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"
        # The reason is `context/actor.py`'s sentence, not a second one written
        # here: one module decides this, and a paraphrase would be a second
        # opinion about the same record.
        assert "not recently enough" in after["rows"][0]["because"]

    def test_the_verdict_is_computed_from_the_supplied_clock(self, tmp_path):
        """Two clocks, two answers, one record — nothing was written in between."""
        store = seeded(tmp_path)
        before = report_for(store, now=FRESH_AT)
        after = report_for(store, now=STALE_AT)

        assert before["rows"][0]["state"] == "AUTHORIZED"
        assert after["rows"][0]["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"
        assert store.grants() == store.grants()

    def test_an_unverified_provider_does_not_authorize(self, tmp_path):
        """FRESH is not enough; FACT and VERIFIED are the other two halves.

        A context is evidence of WHO, and a provider that answered without
        verifying has produced a context that authorizes nothing — so a grant
        deriving from one is excluded, and the exclusion names the provider
        rather than the clock.
        """
        store = AgentControlStore(tmp_path / "store.json")
        store.put_context(a_context(provider_status="UNVERIFIED"))
        store.put_grant(a_grant())

        row = report_for(store, now=FRESH_AT)["rows"][0]

        assert row["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"
        assert "did not verify this actor" in row["because"]

    def test_a_context_swept_away_leaves_the_grant_unresolved(self, tmp_path):
        """The root is gone, and that is not a revocation.

        `sweep` drops contexts whose window closed. A grant whose context is
        gone reports `DELEGATION_UNRESOLVED` — "what this was delegated by
        cannot be established any more" — which is a different next action from
        `DELEGATION_DOES_NOT_AUTHORIZE`, and neither of them is a withdrawal.
        """
        store = seeded(tmp_path)
        gone = store.sweep(before="2030-01-01T00:00:00+00:00")

        assert gone["contexts"] == [CONTEXT_REF]
        assert store.get_grant("grant:tokyo-2026-09-22") is not None
        assert report_for(store, now=FRESH_AT)["rows"][0]["state"] == "DELEGATION_UNRESOLVED"


# --------------------------------------------------------------------------
# 3. the record is write-once, and revocation is not a field a put can write
# --------------------------------------------------------------------------

class TestTheRecordIsWriteOnce:
    def test_a_second_put_with_a_wider_scope_is_refused(self, tmp_path):
        store = seeded(tmp_path)

        with pytest.raises(StoreError, match="cannot be replaced"):
            store.put_grant(a_grant(items=["LIT-0103", "LIT-0104"]))

        assert store.get_grant("grant:tokyo-2026-09-22")["items"] == ["LIT-0103"]

    def test_an_equal_re_report_returns_the_recorded_one(self, tmp_path):
        store = seeded(tmp_path)

        assert store.put_grant(a_grant()) == store.get_grant("grant:tokyo-2026-09-22")
        assert len(store.grants()) == 1

    def test_a_caller_cannot_write_a_revocation(self, tmp_path):
        store = seeded(tmp_path)

        with pytest.raises(StoreError, match="may not be stored carrying revoked"):
            store.put_grant(a_grant(revoked=True))
        with pytest.raises(StoreError, match="revoked_at"):
            store.put_grant(a_grant(revoked_at="2026-09-22T12:00:00+00:00"))

    def test_a_re_report_cannot_un_revoke(self, tmp_path):
        """The put that would restore authority has to be refused, not ignored."""
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22", at="2026-09-22T12:01:00+00:00")

        assert store.put_grant(a_grant())["revoked"] is True
        assert store.get_grant("grant:tokyo-2026-09-22")["revoked"] is True


class TestRevocation:
    def test_revoking_keeps_the_record_and_says_when(self, tmp_path):
        store = seeded(tmp_path)

        revoked = store.revoke_grant("grant:tokyo-2026-09-22",
                                     at="2026-09-22T12:01:00+00:00",
                                     reason="holder changed")

        assert revoked["revoked"] is True
        assert revoked["revoked_at"] == "2026-09-22T12:01:00+00:00"
        assert revoked["revoked_reason"] == "holder changed"
        # The scope is still there. A revoked grant answers "what was
        # authorized", and a record that dropped its scope could not.
        assert revoked["items"] == ["LIT-0103"]

    def test_revoking_twice_keeps_the_first_time(self, tmp_path):
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22", at="2026-09-22T12:01:00+00:00")

        again = store.revoke_grant("grant:tokyo-2026-09-22",
                                   at="2026-09-22T12:30:00+00:00")

        assert again["revoked_at"] == "2026-09-22T12:01:00+00:00"

    def test_revoking_something_never_recorded_is_refused(self, tmp_path):
        store = seeded(tmp_path)

        with pytest.raises(StoreError, match="nothing to revoke|no grant to revoke"):
            store.revoke_grant("grant:never", at="2026-09-22T12:01:00+00:00")

    def test_a_sweep_does_not_take_the_grant(self, tmp_path):
        """A grant has no window this store sweeps on, and the reason is not tidiness.

        A swept grant would leave an expired delegation and one that never
        existed looking the same, and the reader would report the same reason
        for both.
        """
        path = tmp_path / "store.json"
        store = AgentControlStore(path)
        store.put_context(a_context(expires_at="2001-01-01T00:00:00+00:00"))
        store.put_grant(a_grant())
        store.sweep(before="2026-01-01T00:00:00+00:00")

        assert "grant:tokyo-2026-09-22" in json.loads(
            path.read_text(encoding="utf-8"))["grants"]

    def test_the_summary_counts_them(self, tmp_path):
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22", at="2026-09-22T12:01:00+00:00")

        summary = store.summary()

        assert summary["grants"] == 1
        assert summary["grants_revoked"] == 1


# --------------------------------------------------------------------------
# 4. the states do not collapse
# --------------------------------------------------------------------------

class TestEveryRefusalHasItsOwnState:
    def test_a_named_grant_nothing_recorded_is_not_an_empty_store(self, tmp_path):
        """The board's gap and the store's gap are different producers.

        On this node both are true at once and neither sentence may stand in
        for the other: an empty store is a delegation nobody made, and a row
        without a `grant_ref` is a card field nobody built.
        """
        store = AgentControlStore(tmp_path / "store.json")

        report = report_for(store, now=FRESH_AT, named_by={"grant:nowhere"})

        assert report["summary"]["recorded"] == 0
        assert [r["state"] for r in report["rows"]] == ["GRANT_NOT_RECORDED"]
        assert "gap in the board's producer" in report["rows"][0]["because"]

    def test_a_ref_the_store_does_hold_is_not_also_reported_as_missing(self, tmp_path):
        """The join's other direction. A recorded grant named by a row is one row.

        Without this, a reader looking at the report could not tell a board
        naming a grant the store holds from one naming a grant it does not, and
        the fix for the second — build the card field — would look like the fix
        for the first.
        """
        store = seeded(tmp_path)

        report = report_for(store, now=FRESH_AT,
                            named_by={"grant:tokyo-2026-09-22", "grant:nowhere"})

        # Recorded grants first, then the refs nothing recorded: the store's
        # answer is what the envelope is about, and the board's gap is a
        # footnote to it.
        assert [r["grant_ref"] for r in report["rows"]] == [
            "grant:tokyo-2026-09-22", "grant:nowhere"]
        assert report["summary"]["states"] == {"AUTHORIZED": 1, "GRANT_NOT_RECORDED": 1}

    def test_a_record_whose_two_names_disagree_is_malformed(self, tmp_path):
        """`grant_ref` is the key; `ref` is the field the cycle reads.

        A record carrying both and disagreeing with itself means two things,
        and a reader that picked one would be choosing which of the record's
        own statements to believe.
        """
        store = seeded(tmp_path)
        store.put_grant(a_grant())  # the legitimate record

        report = delegation.envelopes(
            {"grant:two": {**a_grant(), "ref": "grant:one"}},
            store.contexts(), now=FRESH_AT)

        assert report["rows"][0]["state"] == "GRANT_MALFORMED"
        assert report["rows"][0]["in_envelope"] is False

    def test_a_revoked_grant_is_in_the_envelope_and_grant_reason_excludes_it(self, tmp_path):
        """Whose judgement is whose, pinned by an assertion on both.

        The envelope is about DERIVATION — whether the delegation authorizes.
        A revocation is a fact about the grant itself, and it stays
        `intake/daily_cycle.grant_reason`'s to judge. So a revoked grant whose
        delegation is still live IS in the envelope, and the reason the cycle
        refuses it comes from the other module. Collapsing the two would put a
        second judge on one question, and the day they disagreed the envelope
        would be silently narrower than the policy.
        """
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22", at="2026-09-22T12:01:00+00:00")

        report = report_for(store, now=FRESH_AT)
        envelope = report["envelopes"]["grant:tokyo-2026-09-22"]

        assert report["rows"][0]["state"] == "AUTHORIZED"
        assert report["rows"][0]["revoked"] is True
        assert cycle.grant_reason(envelope, item=an_item(), action="research",
                                  now=cycle.instant(FRESH_AT)) == "MISSING_OR_REVOKED_GRANT"

    def test_the_envelope_ref_is_the_key(self, tmp_path):
        """A grant stored without a `ref` is not one with a missing name."""
        store = seeded(tmp_path)
        envelope = report_for(store, now=FRESH_AT)["envelopes"]["grant:tokyo-2026-09-22"]

        assert envelope["ref"] == "grant:tokyo-2026-09-22"
        assert "ref" not in store.get_grant("grant:tokyo-2026-09-22")

    def test_the_envelope_is_a_grant_the_cycle_accepts(self, tmp_path):
        """The end of the chain: a recorded delegation admits the row that names it."""
        store = seeded(tmp_path)
        envelope = report_for(store, now=FRESH_AT)["envelopes"]

        assert cycle.grant_reason(envelope["grant:tokyo-2026-09-22"],
                                  item=an_item(), action="research",
                                  now=cycle.instant(FRESH_AT)) is None

    def test_an_empty_envelope_excludes_the_row_that_names_it(self, tmp_path):
        """And the coarse reason the cycle gives is not the one a reader needs.

        With the delegation stale the envelope is empty, so `select_focus`
        reports `MISSING_OR_REVOKED_GRANT` — which names neither the clock nor
        the context. That is exactly why `envelopes()` returns rows beside the
        map, and why every caller prints them.
        """
        store = seeded(tmp_path)
        report = report_for(store, now=STALE_AT)
        board_row = {**an_item(), "grant_ref": "grant:tokyo-2026-09-22",
                     "priority": "P1", "created_at": "2026-09-01T00:00:00+00:00"}

        decision = cycle.select_focus([board_row], report["envelopes"],
                                      now=cycle.instant(STALE_AT),
                                      observed_at="2026-09-22T11:59:00+00:00")

        assert decision["selected"] == []
        assert decision["excluded"][0]["reason"] == "MISSING_OR_REVOKED_GRANT"
        assert report["rows"][0]["state"] == "DELEGATION_DOES_NOT_AUTHORIZE"

    def test_the_same_row_is_selected_when_the_delegation_is_live(self, tmp_path):
        """The positive half. Without it the exclusion above proves only that
        something refused, not that the envelope is what admits."""
        store = seeded(tmp_path)
        report = report_for(store, now=FRESH_AT)
        board_row = {**an_item(), "grant_ref": "grant:tokyo-2026-09-22",
                     "priority": "P1", "created_at": "2026-09-01T00:00:00+00:00"}

        decision = cycle.select_focus([board_row], report["envelopes"],
                                      now=cycle.instant(FRESH_AT),
                                      observed_at="2026-09-22T11:59:00+00:00")

        assert decision["selected"] == ["LIT-0103"]


# --------------------------------------------------------------------------
# 5. the module reads no clock
# --------------------------------------------------------------------------

class TestTheModuleReadsNoClock:
    """One line would turn a reproducible verdict into an unreproducible one."""

    def test_no_time_module_is_imported(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert not imported & {"time", "datetime", "calendar", "zoneinfo"}

    def test_nothing_calls_a_clock(self):
        source = MODULE.read_text(encoding="utf-8")
        for call in ("now()", "utcnow", "today()", "monotonic", "time()"):
            assert call not in source

    def test_now_is_a_keyword_only_parameter_of_both_entry_points(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        wanted = {"derivation": ast.FunctionDef, "envelopes": ast.FunctionDef}
        found = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name in wanted}

        assert set(found) == set(wanted)
        for name, node in found.items():
            kwonly = [a.arg for a in node.args.kwonlyargs]
            assert "now" in kwonly, f"{name} takes `now` positionally or not at all"
            assert "now" not in [a.arg for a in node.args.args], name

    def test_the_cli_verb_supplies_the_clock_as_a_flag(self):
        help_text = cli("work", "grants", "--help").stdout
        assert "--now" in help_text


# --------------------------------------------------------------------------
# 6. every emitted field names its producer
# --------------------------------------------------------------------------

class TestEveryEmittedFieldHasAProducer:
    def test_a_row_carries_only_keys_the_table_names(self, tmp_path):
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22", at="2026-09-22T12:01:00+00:00")

        for row in report_for(store, now=FRESH_AT, named_by={"grant:nowhere"})["rows"]:
            assert set(row) <= set(delegation.ROW_PRODUCED)

    def test_every_declared_producer_is_actually_emitted(self, tmp_path):
        """Both directions: a producer with no field is as wrong as a field with
        no producer, and only this direction catches a table going stale as the
        record grows.

        The fixture revokes WITH a reason, because `revoked_reason` is only
        emitted when the record carries one: a row that printed
        `revoked_reason: None` for a grant that has no such field would be this
        module inventing a fact about a revocation that never said why.
        """
        store = seeded(tmp_path)
        store.revoke_grant("grant:tokyo-2026-09-22",
                           at="2026-09-22T12:01:00+00:00", reason="holder changed")
        emitted = set()
        for row in report_for(store, now=FRESH_AT, named_by={"grant:nowhere"})["rows"]:
            emitted |= set(row)

        assert set(delegation.ROW_PRODUCED) == emitted

    def test_a_field_with_no_producer_is_refused(self, tmp_path):
        """The guard itself, driven directly so it cannot rot unnoticed."""
        with pytest.raises(delegation.Refused, match="no producer"):
            delegation.no_unsourced({"grant_ref": "g", "invented": 1},
                                    delegation.ROW_PRODUCED, "a test row")

    def test_the_reported_states_are_the_declared_ones(self, tmp_path):
        store = AgentControlStore(tmp_path / "store.json")
        store.put_context(a_context())
        store.put_grant(a_grant())

        for state in ("DELEGATION_UNRESOLVED", "DELEGATION_DOES_NOT_AUTHORIZE",
                      "GRANT_MALFORMED", "GRANT_NOT_RECORDED", "AUTHORIZED"):
            assert state in delegation.DERIVATION_STATES


# --------------------------------------------------------------------------
# 7. the two verbs
# --------------------------------------------------------------------------

class TestTheCli:
    def test_work_focus_no_longer_takes_a_grant_file(self):
        """The removed authority path, pinned where a later commit would look.

        `--grants` was an absolute path to a JSON object of scopes and budgets
        that nothing rooted in a delegation. Restoring it would restore the
        hole this round closed, and it would look like a convenience.
        """
        help_text = cli("work", "focus", "--help").stdout

        assert "--grants" not in help_text
        assert "--ttl" in help_text

    def test_work_grants_reports_an_empty_store_as_empty(self, tmp_path):
        env = data_root(tmp_path)

        done = cli("work", "grants", env_extra=env)

        assert done.returncode == 0, done.stderr
        assert "recorded    0" in done.stdout
        assert "nothing has delegated work on this node" in done.stdout
        assert "store.json" in done.stdout

    def test_work_grants_reports_the_state_of_a_recorded_grant(self, tmp_path):
        env = data_root(tmp_path)
        path = Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"
        store = AgentControlStore(path)
        store.put_context(a_context())
        store.put_grant(a_grant())

        live = cli("work", "grants", "--now", FRESH_AT, env_extra=env)
        stale = cli("work", "grants", "--now", STALE_AT, env_extra=env)

        assert live.returncode == 0, live.stderr
        assert "authorizing 1 of 1" in live.stdout
        assert "AUTHORIZED  (in the envelope)" in live.stdout
        assert "authorizing 0 of 1" in stale.stdout
        assert "DELEGATION_DOES_NOT_AUTHORIZE" in stale.stdout

    def test_work_grants_refuses_a_clock_it_cannot_read(self, tmp_path):
        env = data_root(tmp_path)

        done = cli("work", "grants", "--now", "yesterday", env_extra=env)

        assert done.returncode != 0
        assert "is not a timestamp" in done.stderr

    def test_work_grants_refuses_a_clock_with_no_timezone(self, tmp_path):
        env = data_root(tmp_path)

        done = cli("work", "grants", "--now", "2026-09-22T12:00:00", env_extra=env)

        assert done.returncode != 0
        assert "carries no timezone" in done.stderr

    def test_the_json_payload_is_the_same_report_the_text_prints(self, tmp_path):
        env = data_root(tmp_path)
        path = Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"
        store = AgentControlStore(path)
        store.put_context(a_context())
        store.put_grant(a_grant())

        done = cli("work", "grants", "--now", FRESH_AT, "--json", env_extra=env)
        payload = json.loads(done.stdout)

        assert payload["delegations"]["schema"] == delegation.SCHEMA
        assert payload["delegations"]["summary"]["authorizing"] == 1
        assert payload["now"] == FRESH_AT
