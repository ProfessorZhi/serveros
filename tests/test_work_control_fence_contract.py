#!/usr/bin/env python3
"""The D4 fence contract, held still while its directory is not ours to move.

D4 of `docs/plans/fleet-v1-gap-and-work-packages.md` moves ten work-control
modules into `workengine/agent_control/`. That directory, and two of the ten
modules (`agent_control_store`, `owner_policy`), belong to #407 right now, so
the move is not available to this work order — only the audit is. This file is
the audit's checkable half:

  what the lease / epoch / fence boundary actually promises, written down as
  assertions that pass TODAY and must still pass after the move.

That framing is deliberate. A characterization test cannot be written before
the code it characterizes, so it earns its keep differently: by failing when a
refactor changes the promise. Every test here therefore states a refusal or an
axis value that a careless move would quietly change, and the ones that could
pass without looking carry a control that fails if the check goes dead.

THE FINDING THIS FILE EXISTS FOR
--------------------------------
One word, `epoch`, names three different counters on this boundary, and each
one has its own refusal:

  1. `owner_policy.OwnerPolicy.epoch` — the OWNER POLICY GENERATION. A grant
     stores `owner_policy_epoch`; when the live policy's epoch moves, the grant
     refuses as BLOCKED_OWNER_POLICY and says the binding is stale.
  2. `agent_control_store`'s `grant_epoch` — a durable, monotonic counter in the
     store document, minted only inside a mutation. Deliberately NOT derived
     from a grant count, because revoking a grant must not let a later grant
     reuse its number.
  3. `coordination`'s writer `epoch` — half of the WRITER FENCE. It moves when
     authority changes hands, while the token moves when the same holder
     re-takes the scope.

They are three counters because they answer three questions, and none of them
can be substituted for another. The risk the move creates is exactly the one
that a single directory named `agent_control` invites: someone reads `epoch`,
picks the nearest one, and the failure is a refusal with a message that reads
like a policy decision. `TestThreeEpochsAreThreeCounters` pins the separation,
including the structural reason it holds — `coordination.py` is a pure kernel
that loads nothing, so it cannot consult an owner policy even by accident.

Nothing here imports the daemon, opens a socket, or touches a store: the fence
kernel is pure by design (`writer_axis` takes the current writer as an
argument rather than fetching it), and only a test that respects that can run
on a Windows box as well as on the node.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load(name: str, path: Path):
    """The repository's loader convention: consult the cache before overwriting."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coordination = _load("serveros_runtime_coordination_contract", RUNTIME / "coordination.py")
owner_policy = _load("serveros_runtime_owner_policy_contract", RUNTIME / "owner_policy.py")

NOW = "2026-10-28T00:00:00Z"


def _binding(writer=None, **extra) -> dict:
    record = {"session_role": "implementation", "bound_item_ref": "item:1"}
    if writer is not None:
        record["writer"] = writer
    record.update(extra)
    return record


def _policy(**overrides):
    fields = dict(
        policy_ref="op:test", os_principal="uid:1002", actor_ref="actor:alice",
        projects=("p",), items=("i",), actions=("read",),
        valid_from="2026-01-01T00:00:00Z", expires_at="2027-01-01T00:00:00Z",
        revoked=False, epoch=3, issued_by="owner:alice",
        issued_at="2026-01-01T00:00:00Z")
    fields.update(overrides)
    return owner_policy.OwnerPolicy(**fields)


def _grant(epoch=3, **overrides) -> dict:
    grant = {"owner_policy_ref": "op:test", "owner_policy_epoch": epoch,
             "os_principal": "uid:1002", "actor_ref": "actor:alice",
             "projects": ["p"], "items": ["i"], "actions": ["read"]}
    grant.update(overrides)
    return grant


PEER = {"uid": 1002}
CONTEXT = {"actor_ref": "actor:alice"}


class TestTheWriterFenceIsOneTuple:
    def test_the_axis_is_four_closed_words(self) -> None:
        """A fifth word, or a rename, changes what every reader can branch on."""
        declared = coordination.tree_shape()["writer_axis"]
        assert declared == list(coordination.WRITER_AXIS)
        assert declared == ["CURRENT", "STALE", "EXPIRED", "UNKNOWN"]

    def test_the_kernel_can_reach_all_four_and_nothing_else(self) -> None:
        """Every declared word has a producer, and no case leaves the vocabulary.

        A word with no producer is the "declared, tested, unreachable" defect
        this repository keeps finding one plane over; a word produced but not
        declared is the mirror image and evades every reader that switches on it.
        """
        current = {"owner": "dell-5820", "epoch": 1, "token": 1}
        produced = {
            "UNKNOWN": coordination.writer_axis(_binding()),
            "CURRENT": coordination.writer_axis(
                _binding(dict(current)), current_writer=current),
            "STALE": coordination.writer_axis(
                _binding({**current, "token": 2}), current_writer=current),
            "EXPIRED": coordination.writer_axis(
                _binding({**current, "expires_at": "2026-10-27T00:00:00Z"}),
                current_writer=current, now=NOW),
        }
        assert produced == {"UNKNOWN": "UNKNOWN", "CURRENT": "CURRENT",
                            "STALE": "STALE", "EXPIRED": "EXPIRED"}
        assert set(produced.values()) == set(coordination.WRITER_AXIS)

    def test_the_stamp_is_fixed_width_so_strings_close(self) -> None:
        """Why `WRITER_STAMP` is a contract and not a formatting preference.

        The expiry is compared as a STRING against a fixed-width UTC stamp, and
        the same format is shared with the Work lease. An `isoformat()` that
        keeps microseconds sorts `...:00Z` and `...:00.123456Z` at the same
        second by string — which is the bug the fixed width exists to prevent.
        """
        assert coordination.WRITER_STAMP == "%Y-%m-%dT%H:%M:%SZ"
        stamp = datetime.datetime(2026, 10, 28, 0, 0, 0, 500000).strftime(
            coordination.WRITER_STAMP)
        assert stamp == "2026-10-28T00:00:00Z"
        assert "." not in stamp
        # Control: the naive spelling this format exists to replace does carry
        # the subsecond field, so the assertion above is not vacuous.
        assert "." in datetime.datetime(2026, 10, 28, 0, 0, 0, 500000).isoformat()

    def test_a_boolean_is_not_an_epoch(self) -> None:
        """`isinstance(True, int)` is why this check is spelled out in the source."""
        with pytest.raises(coordination.ThreadRefused) as raised:
            coordination.normalize_writer({"owner": "a", "epoch": True, "token": "t"})
        assert raised.value.reason == "INVALID_CONTEXT"

        with pytest.raises(coordination.ThreadRefused) as raised:
            coordination.normalize_writer({"owner": "a", "epoch": 0, "token": "t"})
        assert raised.value.reason == "INVALID_CONTEXT"

        # Control: epoch 1 and a real token are accepted, so the refusals above
        # are about the value and not about the writer being unparseable.
        assert coordination.normalize_writer(
            {"owner": "a", "epoch": 1, "token": "t"})["epoch"] == 1

    def test_a_writer_without_a_token_is_refused(self) -> None:
        """Without a token, a re-take is indistinguishable from a handover."""
        for token in (None, "", "   "):
            with pytest.raises(coordination.ThreadRefused) as raised:
                coordination.normalize_writer(
                    {"owner": "a", "epoch": 1, "token": token})
            assert raised.value.reason == "INVALID_CONTEXT", token


class TestTheWriterAxisFailsClosed:
    def test_a_binding_with_no_writer_is_unknown_not_current(self) -> None:
        assert coordination.writer_axis(_binding()) == "UNKNOWN"

    def test_a_claim_with_nothing_to_compare_against_is_unknown(self) -> None:
        """A claim nobody checked is not an authorization — the source says so."""
        writer = {"owner": "a", "epoch": 1, "token": 1}
        assert coordination.writer_axis(
            _binding(writer), current_writer=None) == "UNKNOWN"

    def test_an_unreadable_expiry_is_expired_not_current(self) -> None:
        """The fail-closed bucket: a claim that cannot be aged is not fresh."""
        writer = {"owner": "a", "epoch": 1, "token": 1,
                  "expires_at": "the day after tomorrow"}
        assert coordination.writer_axis(
            _binding(writer), current_writer=writer, now=NOW) == "EXPIRED"

    def test_a_tuple_that_moved_on_either_half_is_stale(self) -> None:
        current = {"owner": "a", "epoch": 2, "token": 1}
        for moved in ({"owner": "a", "epoch": 1, "token": 1},     # epoch: handover
                      {"owner": "a", "epoch": 2, "token": 2},     # token: re-take
                      {"owner": "b", "epoch": 2, "token": 1}):    # owner: transfer
            assert coordination.writer_axis(
                _binding(moved), current_writer=current, now=NOW) == "STALE", moved

    def test_the_kernel_is_pure(self) -> None:
        """It takes its inputs rather than fetching them; a move must keep that.

        A kernel that read the store would need the store to be tested — which
        is exactly what `writer_axis`'s docstring declines, and what makes the
        same function usable from the daemon, the CLI and this file.
        """
        record = _binding({"owner": "a", "epoch": 1, "token": 1})
        before = json.dumps(record, sort_keys=True)
        first = coordination.writer_axis(record, current_writer={"owner": "a",
                                                                "epoch": 1,
                                                                "token": 1},
                                         now=NOW)
        second = coordination.writer_axis(record, current_writer={"owner": "a",
                                                                 "epoch": 1,
                                                                 "token": 1},
                                          now=NOW)
        assert first == second == "CURRENT"
        assert json.dumps(record, sort_keys=True) == before


class TestASuccessorOutranksItsPredecessor:
    def test_the_first_claim_at_a_scope_is_epoch_one_token_one(self) -> None:
        claim = coordination.mint_writer({}, "item:1", owner="dell-5820")
        assert claim["owner"] == "dell-5820"
        assert claim["epoch"] == 1
        assert claim["token"] == 1

    def test_the_token_advances_past_every_token_even_across_owners(self) -> None:
        """A transfer outranks its predecessor, so the token spans owners."""
        bindings = {
            "b1": {"bound_item_ref": "item:1", "writer": {"owner": "a", "epoch": 1, "token": 3}},
            "b2": {"bound_item_ref": "item:1", "writer": {"owner": "b", "epoch": 2, "token": 7}},
            "b3": {"bound_item_ref": "item:2", "writer": {"owner": "c", "epoch": 9, "token": 99}},
        }
        claim = coordination.mint_writer(bindings, "item:1", owner="b")
        assert claim["token"] == 8, "another item's token must not set this scope's"
        assert claim["epoch"] == 2, "the epoch is held, not advanced, by a re-take"

        successor = coordination.mint_writer(
            {**bindings, "b4": {"bound_item_ref": "item:1", "writer": claim}},
            "item:1", owner="b")
        assert successor["token"] == 9

    def test_a_new_claim_is_current_against_itself(self) -> None:
        """Control joining the two halves: mint, then ask the axis about it."""
        claim = coordination.mint_writer({}, "item:1", owner="dell-5820")
        assert coordination.writer_axis(_binding(claim), current_writer=claim,
                                        now=NOW) == "CURRENT"


class TestTheLeaseStampIsOneFormatAcrossThreeDirectories:
    """One format string, three directories, no test tying them together.

    `runtime/coordination.py` compares a writer's expiry as a string against a
    fixed-width stamp; `workengine/store.py` writes the Work lease's expiry with
    the same one; `context/resolver.py` filters that column with SQL. Three
    files, three copies of the literal, and the reason it must be one value is
    the reason the source gives: an offset or subsecond spelling sorts
    differently at the same second under a string comparison.

    Measured, and asserted as a floor so the scan cannot go quiet: seven
    definitions in the tree carry a strftime stamp constant today, and all
    seven are `%Y-%m-%dT%H:%M:%SZ`. `scripts/verify.py`'s
    `%Y-%m-%dT%H:%M:%S%z` is deliberately NOT one of them — it is a
    human-facing log line, not a lease column — so the test says so rather
    than leaving the next reader to "fix" it.
    """

    STAMP_ASSIGNMENT = re.compile(
        r"^\s*([A-Za-z_]*STAMP[A-Za-z_]*)\s*=\s*[\"'](%[^\"']+)[\"']", re.M)

    def _sites(self) -> list[tuple[str, str, str]]:
        import subprocess
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "*.py"],
                             capture_output=True, text=True, check=True).stdout
        sites = []
        for rel in out.splitlines():
            try:
                text = (REPO / rel).read_text(encoding="utf-8")
            except OSError:
                continue
            for match in self.STAMP_ASSIGNMENT.finditer(text):
                sites.append((rel, match.group(1), match.group(2)))
        return sorted(sites)

    def test_every_stamp_constant_is_the_same_format(self) -> None:
        sites = self._sites()
        assert len(sites) >= 7, (
            "floor: the lease stamp is defined in context/, workengine/ and "
            "runtime/ and copied into four lease tests; seeing fewer means the "
            f"scan stopped looking, not that they left: {sites}")
        values = {value for _, _, value in sites}
        assert values == {"%Y-%m-%dT%H:%M:%SZ"}, (
            "these are compared as strings across directories and a repository "
            f"boundary; one spelling only: {sites}")

    def test_the_three_homes_are_all_present(self) -> None:
        """The scan is over the whole tree; naming the homes keeps it honest.

        `context/resolver.py` and `workengine/store.py` do not move in D4, so
        they are asserted by path. The kernel is asserted through its own
        `__file__`, because relocating it is exactly what D4 proposes and this
        test is meant to survive that.
        """
        paths = {rel for rel, _, _ in self._sites()}
        assert "context/resolver.py" in paths
        assert "workengine/store.py" in paths
        kernel = Path(coordination.__file__).resolve().relative_to(REPO).as_posix()
        assert kernel in paths
        assert coordination.WRITER_STAMP == "%Y-%m-%dT%H:%M:%SZ"

    def test_an_offset_or_subsecond_spelling_would_fail_the_scan(self) -> None:
        """Control: the matcher does not accept just any `%`-format.

        The two spells that would break the string comparison are `%z` and
        `%f`; both are real formats Python accepts, which is what makes them
        the wrong thing to leave unpinned.
        """
        for bad in ('STAMP = "%Y-%m-%dT%H:%M:%S%z"',
                    'STAMP = "%Y-%m-%dT%H:%M:%S.%fZ"',
                    'STAMP = "%Y-%m-%dT%H:%M:%S+00:00"'):
            match = self.STAMP_ASSIGNMENT.search(bad)
            assert match is not None, bad
            assert match.group(2) != "%Y-%m-%dT%H:%M:%SZ", bad
        assert self.STAMP_ASSIGNMENT.search('_LEASE_STAMP = "%Y-%m-%dT%H:%M:%SZ"')


class TestThreeEpochsAreThreeCounters:
    """The audit's finding, in its checkable form.

    Three counters named `epoch` share this boundary. The danger of collecting
    them into one directory is not that they collide — it is that they start to
    look interchangeable. Each test below states one way they are not.
    """

    def test_the_owner_policy_epoch_refuses_a_stale_grant(self) -> None:
        stale = owner_policy.check_policy_binding(
            grant=_grant(epoch=2), context=CONTEXT, peer=PEER, project="p",
            item_ref="i", action="read", now=NOW, policy=_policy())
        assert stale == {
            "verified": False, "reason": owner_policy.BLOCKED_OWNER_POLICY,
            "because": "the owner policy moved from epoch 2 to 3; "
                       "the grant's binding is stale"}

        # Control: the same grant at the live epoch is accepted, so the refusal
        # above is attributable to the epoch and to nothing else in the fixture.
        assert owner_policy.check_policy_binding(
            grant=_grant(epoch=3), context=CONTEXT, peer=PEER, project="p",
            item_ref="i", action="read", now=NOW, policy=_policy()) is None

    def test_a_grant_that_records_no_usable_epoch_authorizes_nothing(self) -> None:
        missing = _grant()
        missing.pop("owner_policy_epoch")
        for grant in (missing, _grant(epoch=True)):
            refusal = owner_policy.check_policy_binding(
                grant=grant, context=CONTEXT, peer=PEER, project="p",
                item_ref="i", action="read", now=NOW, policy=_policy())
            assert refusal["reason"] == owner_policy.BLOCKED_OWNER_POLICY
            assert refusal["because"] == "the grant records no owner-policy epoch"

    def test_a_boolean_policy_epoch_is_not_generation_three(self) -> None:
        refusal = owner_policy._common_refusal(
            policy=_policy(epoch=True), grant=_grant(epoch=True),
            context=CONTEXT, peer=PEER, now=NOW, require_binding=False)
        assert refusal["reason"] == owner_policy.BLOCKED_OWNER_POLICY
        assert refusal["because"] == (
            "the owner policy epoch True is not a positive integer")

    def test_the_writer_epoch_cannot_be_the_owner_policy_epoch(self) -> None:
        """The structural reason the two counters cannot merge by accident.

        `coordination.py` is a pure kernel: it loads nothing, so it has no route
        to an owner policy even if someone wrote one. That is what makes its
        `epoch` a per-item fence counter by construction rather than by
        convention — and it is the property a move into `agent_control/` would
        put at risk, since that directory is where the policy lives.
        """
        from loader_graph import module_refs

        # Addressed through the module's own `__file__`, not through a literal
        # path: this file is meant to keep passing after the move, and a pinned
        # path would turn the move into a test edit — which is the signal that
        # this assertion is about the boundary rather than about a directory.
        kernel = Path(coordination.__file__).resolve()
        assert set(module_refs(kernel)) == set(), \
            "coordination.py must load nothing; it is a pure kernel"

        source = kernel.read_text(encoding="utf-8")
        for name in ("owner_policy", "OwnerPolicy", "owner_policy_epoch",
                     "BLOCKED_OWNER_POLICY"):
            assert name not in source, name

        # Controls: the same scan finds an edge where one exists, and finds the
        # policy epoch where it does belong — so the two assertions above are
        # about coordination.py and not about a scan that stopped looking.
        assert set(module_refs(RUNTIME / "wake.py")) != set()
        assert "owner_policy_epoch" in Path(
            owner_policy.__file__).read_text(encoding="utf-8")
