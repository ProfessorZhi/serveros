"""B2's semantic half — what may become committed understanding, and what may not.

Every test here is a counterexample to something the contract forbids, because
the failure mode of this layer is not a crash: it is a plausible sentence
appearing in a card with nobody's name on it. So the tests are written against
the four ways that happens — a reading committed against text that has moved, a
model supplying its own confirmation, a citation to a position nobody read, and
a second write that no reader could tell from a second real change.

The last group covers the other half of the same risk: a write that succeeds in
its owning card and fails in a view. "Claim 提交成功但 view 更新失败" has to be
readable as PARTIAL, recoverable, and never as all consumers aligned.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


us = _load("understanding_semantic", "understanding_semantic.py")

#: Records-root-relative, the same coordinate system the reader and the patch
#: name their card paths in. A card path that meant something different here
#: would make the two layers unjoinable at exactly the point where a claim is
#: written into the card a reader was told about.
CARD = "information/understanding/people/alice.md"
VIEW = "information/understanding/projects/widget.md"
MANIFEST = "information/raw/feishu/oc_group/manifest.json"
ALICE_PROSE = "# Alice\n\n手写的散文，不属于这个模块。\n\n## Sources\n\n- raw/feishu/oc_group\n"
VIEW_PROSE = "# widget\n\n项目视角。\n"


def records_tree(tmp_path: Path) -> Path:
    """A records checkout with an understanding layer in it, and returns the root."""
    root = tmp_path / "records"
    people = root / "information" / "understanding" / "people"
    projects = root / "information" / "understanding" / "projects"
    people.mkdir(parents=True)
    projects.mkdir(parents=True)
    (root / CARD).write_text(ALICE_PROSE, encoding="utf-8")
    (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")
    return root


def digest_of(root: Path, relative: str) -> str:
    return us.card_digest((root / relative).read_text(encoding="utf-8"))


def claim(claim_id: str = "c1", *, kind: str = us.OBSERVED_FACT,
          subject: str = "alice", was=None, now: str = "她说周五交付",
          positions=(12,), excerpt: str = "周五给你", touches=(),
          supersedes=(), conflicts=(), card: str = CARD,
          views=(VIEW,)) -> dict:
    return {
        "id": claim_id, "kind": kind, "subject": subject,
        "assertion": {"was": was, "now": now},
        "support": {"positions": list(positions), "excerpt": excerpt},
        "applies_to": None, "touches": list(touches),
        "supersedes": list(supersedes), "conflicts": list(conflicts),
        "card": card, "views": list(views),
    }


#: Distinct from None on purpose. The reader does not always identify the
#: content, and a reading that says so carries a null; a test helper that turned
#: "not given" and "given as null" into the same value could not build the second
#: and would quietly test the first.
_UNSET = object()


def reading(root: Path, claims=None, *, card: str = CARD,
            content_identity=_UNSET,
            card_digest: str | None = None,
            from_position: int = 10, to_position: int = 20) -> dict:
    """A reading whose two digests match the tree on disk, unless told otherwise."""
    return {
        "schema": us.READING_SCHEMA,
        "source": {"card": card, "manifest": MANIFEST,
                   "from_position": from_position, "to_position": to_position},
        "read": {
            "card": card,
            "content_identity": ("a" * 64 if content_identity is _UNSET
                                 else content_identity),
            "card_digest": card_digest or digest_of(root, card),
        },
        "claims": [claim()] if claims is None else list(claims),
    }


def current(root: Path, card: str = CARD, *, from_position: int = 10,
            to_position: int = 20, content_identity: str = "a" * 64) -> dict:
    """What the caller measured, now. The digests come off the disk."""
    return {"card_digest": digest_of(root, card),
            "content_identity": content_identity,
            "from_position": from_position, "to_position": to_position}


def at(tmp_path: Path, document: dict) -> dict:
    """A reading as a file, read back through the reader — never hand-made."""
    path = tmp_path / "reading.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return us.read_reading(path)


def resolved(tmp_path: Path, root: Path, document: dict, *,
             delegation=None, accepted=None, card: str = CARD,
             **overrides) -> dict:
    return us.resolve(at(tmp_path, document),
                      current(root, card, **overrides), delegation, accepted)


def accepted_document(tmp_path: Path, *rows: dict, name: str = "accepted.jsonl") -> dict:
    """An authored acceptance file, read back through the reader — never hand-made."""
    return us.read_acceptances(accepted_file(tmp_path, *rows, name=name))


def accepted_file(tmp_path: Path, *rows: dict, name: str = "accepted.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n"
                            for row in rows), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# a reading is checked where it is read
# --------------------------------------------------------------------------

class TestAReadingIsCheckedWhereItIsRead:

    def test_a_reading_that_does_not_say_which_revision_it_read_is_malformed(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        del document["read"]["card_digest"]
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "read.card_digest" in str(caught.value)
        assert "names only a place" in str(caught.value)

    def test_a_reading_carrying_its_own_confirmation_is_malformed(self, tmp_path):
        # Rule 2. The interesting case is not a malformed file: it is a
        # perfectly well-formed one whose author decided the answer.
        root = records_tree(tmp_path)
        document = reading(root)
        document["claims"][0]["confirmation"] = us.AGENT_RECONCILED
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "does not carry `confirmation`" in str(caught.value)
        assert "ratify" in str(caught.value)

    def test_a_claim_with_nothing_asserted_is_malformed(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(now="   ")])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "is a heading" in str(caught.value)

    def test_no_item_field_may_be_omitted_even_when_it_is_empty(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        del document["claims"][0]["supersedes"]
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "`supersedes`" in str(caught.value)
        assert "which shape" in str(caught.value)

    def test_one_id_twice_in_one_reading_is_malformed(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("c1"), claim("c1", now="另一句")])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "appears twice" in str(caught.value)

    def test_a_retraction_that_retracts_nothing_is_malformed(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("r1", kind=us.RETRACTION)])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "retraction of nothing" in str(caught.value)

    def test_an_unknown_kind_is_malformed(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(kind="GUESS")])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "GUESS" in str(caught.value)

    def test_an_unknown_touch_target_is_malformed(self, tmp_path):
        # `touches` is what decides whether a person is asked, so an unrecognised
        # value must not fall through to "does not touch anything important".
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=("importance",))])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "importance" in str(caught.value)

    def test_an_unknown_schema_is_malformed(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        document["schema"] = "serveros/understanding-reading.v2"
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "near-miss" in str(caught.value)


# --------------------------------------------------------------------------
# §2/F04: a confirmation is about a revision, not a place
# --------------------------------------------------------------------------

class TestABindingThatMovedRefuses:

    def test_the_reading_applies_against_the_revision_it_read(self, tmp_path):
        # The positive control for the two refusals below: same helper, same
        # tree, nothing moved, and it resolves.
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root))
        assert [c["id"] for c in result["awaiting"]] == ["c1"]
        assert result["committed"] == []

    def test_a_source_whose_content_changed_refuses(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, content_identity="b" * 64)
        with pytest.raises(us.Refused) as caught:
            resolved(tmp_path, root, document)
        assert "the content this reading is about changed" in str(caught.value)
        assert "no longer the one on disk" in str(caught.value)

    def test_a_card_rewritten_under_its_own_path_refuses(self, tmp_path):
        # Same path, same span, different prose in the card. Without the second
        # digest this reading would be committed against an interpretation of
        # text that has been replaced.
        root = records_tree(tmp_path)
        document = reading(root)
        (root / CARD).write_text(ALICE_PROSE + "\n她后来改了主意。\n", encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            resolved(tmp_path, root, document)
        assert "the understanding card was rewritten" in str(caught.value)
        assert "the content this reading is about changed" not in str(caught.value)

    def test_a_rewrite_of_both_is_reported_as_both(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, content_identity="c" * 64)
        (root / CARD).write_text(ALICE_PROSE + "\n又一句。\n", encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            resolved(tmp_path, root, document)
        assert "changed and the understanding card was rewritten" in str(caught.value)

    def test_a_citation_outside_what_was_read_is_refused(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(positions=(21,))])
        with pytest.raises(us.Refused) as caught:
            resolved(tmp_path, root, document)
        assert "outside the range this reading declares it read (10–20)" in str(
            caught.value)

    def test_the_ends_of_the_range_are_inside_it(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(positions=(10, 20))])
        result = resolved(tmp_path, root, document)
        assert [c["id"] for c in result["awaiting"]] == ["c1"]

    def test_an_unmeasured_range_refuses_rather_than_passing_everything(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        with pytest.raises(us.Refused) as caught:
            us.resolve(at(tmp_path, document),
                       {"card_digest": digest_of(root, CARD),
                        "content_identity": "a" * 64,
                        "from_position": None, "to_position": None}, None)
        assert "no citation can be checked" in str(caught.value)


# --------------------------------------------------------------------------
# what a machine may commit, and what only a person may
# --------------------------------------------------------------------------

class TestWhatCommitsWithoutAPerson:

    def test_with_no_delegation_nothing_commits(self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root))
        assert result["committed"] == []
        assert "no standing delegation covers this work" in \
            result["awaiting"][0]["confirmation_why"]
        assert result["delegation"] is None

    def test_an_observation_inside_a_delegation_commits(self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root),
                          delegation={"covers": ["alice"], "until": "2026-10-01",
                                      "granted_by": "user", "source": "session-1"})
        assert [c["id"] for c in result["committed"]] == ["c1"]
        assert result["committed"][0]["confirmation"] == us.AGENT_RECONCILED
        assert "touching none of" in result["committed"][0]["confirmation_why"]

    def test_a_delegation_that_does_not_cover_the_subject_leaves_it_awaiting(
            self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root),
                          delegation={"covers": ["bruce"], "until": None,
                                      "granted_by": "user", "source": "session-1"})
        assert result["committed"] == []
        assert "'bruce'" in result["awaiting"][0]["confirmation_why"]
        assert "'alice'" in result["awaiting"][0]["confirmation_why"]

    def test_the_delegation_is_copied_and_not_paraphrased(self, tmp_path):
        root = records_tree(tmp_path)
        grant = {"covers": ["alice"], "until": "2026-10-01",
                 "granted_by": "user", "source": "session-1"}
        result = resolved(tmp_path, root, reading(root), delegation=grant)
        assert result["delegation"] == grant

    @pytest.mark.parametrize("kind", [us.PROPOSAL, us.ACCEPTED_DECISION,
                                      us.CONFLICT, us.CORRECTION])
    def test_what_to_do_is_not_a_machines_to_accept(self, tmp_path, kind):
        # An accepted decision is the sharpest of these: the reading says a
        # person accepted it, and this module still does not commit it on that
        # say-so. Acceptance arrives as its own authored input.
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root, [claim(kind=kind)]),
                          delegation={"covers": ["alice"], "until": None,
                                      "granted_by": "user", "source": "session-1"})
        assert result["committed"] == []
        assert "is not a machine's to accept" in \
            result["awaiting"][0]["confirmation_why"]

    def test_an_inference_the_source_supports_commits(self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(
            tmp_path, root, reading(root, [claim(kind=us.SUPPORTED_INFERENCE)]),
            delegation={"covers": ["alice"], "until": None, "granted_by": "user",
                        "source": "session-1"})
        assert [c["id"] for c in result["committed"]] == ["c1"]

    @pytest.mark.parametrize("touch", us.MAJOR_CHANGES)
    def test_a_major_change_awaits_even_inside_a_covering_delegation(
            self, tmp_path, touch):
        # The point of the pair: an OBSERVED_FACT about a deadline is still a
        # person's decision, and an OBSERVED_FACT about anything else is not.
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root, [claim(touches=(touch,))]),
                          delegation={"covers": ["alice"], "until": None,
                                      "granted_by": "user", "source": "session-1"})
        assert result["committed"] == []
        assert touch in result["awaiting"][0]["confirmation_why"]

    def test_a_claim_with_no_positions_has_nothing_to_check_and_still_resolves(
            self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root, [claim(positions=())]),
                          delegation={"covers": ["alice"], "until": None,
                                      "granted_by": "user", "source": "session-1"})
        assert [c["id"] for c in result["committed"]] == ["c1"]


# --------------------------------------------------------------------------
# a retraction takes effect locally, now
# --------------------------------------------------------------------------

class TestARetractionActsLocally:

    def test_a_retracted_claim_stops_being_recallable_at_once(self):
        # No provider is involved. The contract's §6: the remote can be down and
        # the withdrawn fact still must not come back.
        result = us.eligible({"c1": "line one", "c2": "line two"},
                             {"c1": "r1"})
        assert result["recallable"] == {"c2": "line two"}
        assert result["withdrawn"] == {"c1": {"line": "line one",
                                              "retracted_by": "r1"}}

    def test_a_retraction_of_a_claim_this_module_never_held_is_reported(self):
        result = us.eligible({"c1": "line one"}, {"c9": "r1"})
        assert result["retract_nothing"] == ["c9"]

    def test_a_retraction_is_recorded_but_not_committed_and_says_why(
            self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(
            root, [claim("r1", kind=us.RETRACTION, supersedes=("c9",))]),
            delegation={"covers": ["alice"], "until": None, "granted_by": "user",
                        "source": "session-1"})
        assert result["committed"] == []
        assert result["retracted"] == ["c9"]
        assert "takes effect locally at once" in \
            result["awaiting"][0]["confirmation_why"]

    def test_a_withdrawal_needs_no_grant_and_that_is_the_safe_direction(
            self, tmp_path):
        # The contract's §6 read from the other side: a withdrawal is the act
        # that makes a fact UNUSABLE, and a grant is what an act that puts a
        # fact into a card needs. Gating this on a grant would leave the claim
        # recallable whenever no grant happened to be in play — which is the
        # state the retraction gap exists to remove. It is not free: the
        # withdrawal takes effect, so the receipt has to be re-examinable, and
        # the field that would name an authority says there was none.
        root = records_tree(tmp_path)
        document = reading(root, [claim("r1", kind=us.RETRACTION,
                                        supersedes=("c1",))])
        receipt = applied(tmp_path, root, document, delegation=None)
        assert receipt["state"] == "RETRACTION_ONLY"
        assert receipt["retracted"] == ["c1"]
        assert receipt["accepted"] is None
        assert receipt["written"] == []


# --------------------------------------------------------------------------
# the write happens once, and only inside the fence
# --------------------------------------------------------------------------

def commit_and_write(tmp_path: Path, root: Path, *, delegation=None,
                     committed_at: str = "2026-09-23T10:00:00+08:00",
                     document: dict | None = None, **overrides) -> dict:
    document = document if document is not None else reading(root)
    result = resolved(tmp_path, root, document, delegation=delegation, **overrides)
    plan = us.commit(result, at(tmp_path, document))
    return us.write(plan, root, committed_at=committed_at)


GRANT = {"covers": ["alice"], "until": None, "granted_by": "user",
         "source": "session-1"}

STAMP = "2026-09-23T10:00:00+08:00"


def applied(tmp_path: Path, root: Path, document: dict, *, delegation=None,
            accepted=None, committed_at: str = STAMP, **overrides) -> dict:
    """The whole path, through the one entry point that orders it correctly.

    A replay has to be looked up before the reading is re-validated against the
    card, and the entry point is where that ordering lives. These tests go
    through it for anything that applies twice; the tests that reach past it do
    so to exercise the steps themselves.
    """
    path = tmp_path / "reading.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return us.apply(path, root, measure=lambda: current(root, **overrides),
                    delegation=delegation, accepted=accepted,
                    committed_at=committed_at)


class TestAWithdrawalLeavesARecordThoughItWritesNoFile:
    """A retraction commits nowhere, so the receipt is the only record of it.

    The local effect the contract gives a withdrawal — that it takes hold at
    once, with the provider down — is worth exactly as much as its durable
    record: an effect that exists only in the process that computed it is gone
    with that process, and A2's recall filter reads receipts and reads nothing
    else. These tests are the producer side of that; `test_claim_recall` is the
    consumer side.
    """

    def _withdrawn(self, tmp_path: Path, root: Path):
        """c1 committed at one span, then withdrawn from a later one.

        Returns the withdrawing reading, the receipt the run left, and the
        card's bytes from just before that run. The third is what "this run
        wrote no file" can be measured against at all.
        """
        commit_and_write(tmp_path, root, document=reading(root, [claim("c1")]),
                         delegation=GRANT)
        frozen = (root / CARD).read_bytes()
        document = reading(root, [claim("r1", kind=us.RETRACTION,
                                        supersedes=("c1",), positions=(35,))],
                           from_position=30, to_position=40)
        receipt = applied(tmp_path, root, document, delegation=GRANT,
                          from_position=30, to_position=40)
        return document, receipt, frozen

    def test_a_run_whose_only_effect_is_a_withdrawal_leaves_a_receipt(
            self, tmp_path):
        root = records_tree(tmp_path)
        document, receipt, frozen = self._withdrawn(tmp_path, root)
        assert receipt["state"] == "RETRACTION_ONLY"
        assert receipt["written"] == []
        assert receipt["pending_views"] == []
        assert receipt["retracted"] == ["c1"]
        assert receipt["reused"] is False
        path = us._receipt_path(us._understanding_root(root), at(tmp_path, document))
        assert path.is_file()
        assert us._read_receipt(path) == receipt
        # And it is the record of a withdrawal and not of a write, which is the
        # difference the card's own bytes can be asked about.
        assert (root / CARD).read_bytes() == frozen

    def test_the_receipt_binds_the_withdrawal_to_its_source_and_its_reading(
            self, tmp_path):
        # The withdrawal names a claim; the receipt has to say which source
        # revision, which range of it, and which reading of it that claim was
        # taken out of — otherwise the evidence cannot be re-examined, which is
        # the only thing durable evidence is for.
        root = records_tree(tmp_path)
        document, receipt, _ = self._withdrawn(tmp_path, root)
        read = at(tmp_path, document)
        assert receipt["schema"] == us.RECEIPT_SCHEMA
        assert receipt["key"] == list(us.commit_key(read))
        assert receipt["reading_digest"] == us.reading_digest(read)
        assert receipt["baseline"] == {"card": CARD,
                                       "card_digest": digest_of(root, CARD)}
        assert receipt["retracted"] == ["c1"]

    def test_the_same_withdrawal_run_twice_is_one_receipt_on_disk(self, tmp_path):
        root = records_tree(tmp_path)
        document, first, _ = self._withdrawn(tmp_path, root)
        path = us._receipt_path(us._understanding_root(root), at(tmp_path, document))
        before = path.read_bytes()
        again = applied(tmp_path, root, document, delegation=GRANT,
                        from_position=30, to_position=40,
                        committed_at="2026-09-23T23:00:00+08:00")
        assert again["reused"] is True
        assert again["state"] == "RETRACTION_ONLY"
        assert again["retracted"] == ["c1"]
        # Not rewritten means not restamped either: a receipt's `committed_at`
        # is when the withdrawal happened, and the second run did not make one.
        assert path.read_bytes() == before
        assert again["committed_at"] == first["committed_at"]

    def test_the_persons_answer_arriving_later_commits_and_keeps_the_withdrawal(
            self, tmp_path):
        # The ordinary order of events, with a withdrawal in the same run: the
        # card is waiting for a person AND a claim is withdrawn, the person
        # answers later. The yes must not be refused by the receipt the
        # withdrawal left, and the withdrawal must not be dropped by the write
        # that follows — the two are separate fields of one receipt.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, document=reading(root, [claim("c1")]),
                         delegation=GRANT)
        document = reading(root, [claim("c2", touches=DEADLINE, positions=(35,)),
                                  claim("r1", kind=us.RETRACTION,
                                        supersedes=("c1",), positions=(36,))],
                           from_position=30, to_position=40)
        span = {"from_position": 30, "to_position": 40}
        waiting = applied(tmp_path, root, document, delegation=GRANT, **span)
        assert waiting["state"] == "RETRACTION_ONLY"
        assert waiting["retracted"] == ["c1"]
        assert "她说周五交付" in (root / CARD).read_text(encoding="utf-8")

        proposal = resolved(tmp_path, root, document, delegation=GRANT, **span)
        row = acceptance(proposal, claims=["c2"])
        receipt = applied(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row), **span)
        assert receipt["state"] == "COMPLETE"
        assert receipt["reused"] is False
        assert receipt["retracted"] == ["c1"]
        assert receipt["superseded"] == []
        # `written[i].claims` is the ids the FILE carries, so the card that took
        # c2 carries the withdrawn c1's line as well — the withdrawal takes the
        # claim out of what may be recalled and leaves it where it is.
        assert [claim_id for entry in receipt["written"]
                if entry["card"] == CARD for claim_id in entry["claims"]] == \
            ["c1", "c2"]

    def test_a_withdrawal_with_nothing_waiting_is_still_not_a_write(self, tmp_path):
        # The `RETRACTION_ONLY` receipt is written where `NOTHING_TO_WRITE` is
        # not, and the two states differ by exactly one thing: whether anything
        # was withdrawn. This is the same run as the case above with the waiting
        # claim taken out, so that nothing but the withdrawal is left in it.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, document=reading(root, [claim("c1")]),
                         delegation=GRANT)
        document = reading(root, [claim("r1", kind=us.RETRACTION,
                                        supersedes=("c1",), positions=(35,))],
                           from_position=30, to_position=40)
        nothing = applied(tmp_path, root, document, delegation=GRANT,
                          from_position=30, to_position=40)
        assert nothing["state"] == "RETRACTION_ONLY"
        # And with nothing withdrawn the state is the other one, on the same
        # span and the same card, so that the assertion above is about the
        # withdrawal and not about the span.
        plain = reading(root, [claim("c9", touches=DEADLINE, positions=(55,))],
                        from_position=50, to_position=60)
        empty = applied(tmp_path, root, plain, delegation=GRANT,
                        from_position=50, to_position=60)
        assert empty["state"] == "NOTHING_TO_WRITE"
        assert empty["retracted"] == []
        assert not us._receipt_path(
            us._understanding_root(root), at(tmp_path, plain)).exists()

    def test_a_resumed_write_does_not_drop_the_withdrawal_it_started_with(
            self, tmp_path):
        # A run that both writes and withdraws, with a view it could not reach:
        # the receipt is PARTIAL and holds the withdrawal, and the run that
        # finishes it owes the view and nothing else. A resume that re-derived
        # the withdrawal, or dropped it because this run did not make one, would
        # leave the claim recallable again while the receipt on disk said it was
        # not — the one direction a withdrawal must never move.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, document=reading(root, [claim("c1")]),
                         delegation=GRANT)
        (root / VIEW).unlink()
        (root / VIEW).mkdir()
        document = reading(root, [claim("c2", positions=(35,)),
                                  claim("r1", kind=us.RETRACTION,
                                        supersedes=("c1",), positions=(36,))],
                           from_position=30, to_position=40)
        span = {"from_position": 30, "to_position": 40}
        first = applied(tmp_path, root, document, delegation=GRANT, **span)
        assert first["state"] == "PARTIAL"
        assert first["retracted"] == ["c1"]
        (root / VIEW).rmdir()
        (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")
        second = applied(tmp_path, root, document, delegation=GRANT, **span)
        assert second["state"] == "COMPLETE"
        assert second["reused"] is True
        assert second["retracted"] == ["c1"]
        assert list(second["pending_views"]) == []

    def test_a_v4_receipt_is_read_as_what_it_is_and_not_migrated(self, tmp_path):
        # v5 ADDED two fields and changed none, so a v4 receipt is read directly:
        # its absent `retracted` is not a guess about what it meant, it is what a
        # v4 receipt says — v4 had no way to record a withdrawal that wrote no
        # file, so it recorded none. Nothing here rewrites the file on disk.
        root = records_tree(tmp_path)
        document = reading(root)
        receipt = commit_and_write(tmp_path, root, document=document,
                                   delegation=GRANT)
        path = us._receipt_path(us._understanding_root(root), at(tmp_path, document))
        older = {name: value for name, value in receipt.items()
                 if name not in ("retracted", "baseline")}
        older["schema"] = "serveros/understanding-receipt.v4"
        path.write_text(json.dumps(older, ensure_ascii=False), encoding="utf-8")

        assert us._read_receipt(path) == older
        again = applied(tmp_path, root, document, delegation=GRANT)
        assert again["reused"] is True
        assert "retracted" not in again
        assert json.loads(path.read_text(encoding="utf-8")) == older

    def test_a_v4_receipt_that_was_partial_can_still_be_finished(self, tmp_path):
        # The other half of reading an older receipt as what it is: a v4 receipt
        # can be PARTIAL — a view it could not write — and finishing it must not
        # be a `KeyError` over the field v5 added. What such a receipt says about
        # the card it was reasoned against is that it does not record one, which
        # is what a null baseline on the finished receipt means.
        root = records_tree(tmp_path)
        (root / VIEW).unlink()
        (root / VIEW).mkdir()
        document = reading(root)
        first = applied(tmp_path, root, document, delegation=GRANT)
        assert first["state"] == "PARTIAL"
        path = us._receipt_path(us._understanding_root(root), at(tmp_path, document))
        older = {name: value for name, value in first.items()
                 if name not in ("retracted", "baseline")}
        older["schema"] = "serveros/understanding-receipt.v4"
        path.write_text(json.dumps(older, ensure_ascii=False), encoding="utf-8")
        (root / VIEW).rmdir()
        (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")

        second = applied(tmp_path, root, document, delegation=GRANT)
        assert second["state"] == "COMPLETE"
        assert second["reused"] is True
        assert second["retracted"] == []
        assert second["baseline"] is None
        assert [entry["card"] for entry in second["written"]] == [CARD, VIEW]

    def test_a_v3_receipt_is_refused_by_name(self, tmp_path):
        # A v3 receipt's `written[i]` has no `role`, so a reader that knows which
        # field says whether a card OWNS a claim or only references it cannot
        # read that entry without inventing one.
        path = tmp_path / "receipt-v3.json"
        path.write_text(json.dumps({
            "schema": "serveros/understanding-receipt.v3",
            "key": [CARD, MANIFEST, 10, 20, "a" * 64],
            "written": [{"card": CARD, "before": "b" * 64, "after": "c" * 64,
                         "claims": ["c1"]}],
        }), encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            us._read_receipt(path)
        assert "serveros/understanding-receipt.v3" in str(caught.value)
        assert "serveros/understanding-receipt.v5" in str(caught.value)


class TestTheWriteHappensOnce:

    def test_a_committed_claim_lands_in_its_owning_card_inside_the_fence(
            self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        assert receipt["state"] == "COMPLETE"
        assert receipt["reused"] is False
        body = (root / CARD).read_text(encoding="utf-8")
        assert us.FENCE_BEGIN in body and us.FENCE_END in body
        assert "她说周五交付" in body
        assert "excerpt: 周五给你" in body

    def test_the_prose_outside_the_fence_is_untouched(self, tmp_path):
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        head, _, tail = us.fence_parts((root / CARD).read_text(encoding="utf-8"))
        assert head.startswith(ALICE_PROSE)
        assert head[len(ALICE_PROSE):].strip("\n") == ""
        assert tail == ""
        head, _, tail = us.fence_parts((root / VIEW).read_text(encoding="utf-8"))
        assert head.startswith(VIEW_PROSE)
        assert head[len(VIEW_PROSE):].strip("\n") == ""
        assert tail == ""

    def test_the_view_gets_a_reference_and_not_a_copy(self, tmp_path):
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        view = (root / VIEW).read_text(encoding="utf-8")
        assert f"`{CARD}`" in view
        assert "她说周五交付" not in view

    def test_a_second_apply_returns_the_first_receipt_and_writes_nothing(
            self, tmp_path):
        # The same reading file, applied twice. Not a re-read: a re-read of the
        # same span is the case below, and it is refused.
        root = records_tree(tmp_path)
        document = reading(root)
        first = applied(tmp_path, root, document, delegation=GRANT)
        after_first = (root / CARD).read_bytes()
        second = applied(tmp_path, root, document, delegation=GRANT)
        assert second["reused"] is True
        assert second["written"] == first["written"]
        assert (root / CARD).read_bytes() == after_first

    def test_the_same_proposal_read_again_differently_is_refused(self, tmp_path):
        # Same source revision — same card, same span, same content identity —
        # and a different set of claims. Both readings of one revision cannot be
        # the committed one, and picking between them is a person's decision.
        root = records_tree(tmp_path)
        document = reading(root, [claim(now="她说周五交付")])
        applied(tmp_path, root, document, delegation=GRANT)
        other = reading(root, [claim(now="她说周一交付", excerpt="周一给")])
        with pytest.raises(us.Refused) as caught:
            applied(tmp_path, root, other, delegation=GRANT)
        assert "a different reading" in str(caught.value)
        assert "她说周五交付" in (root / CARD).read_text(encoding="utf-8")

    def test_a_reading_of_the_same_place_at_a_new_revision_is_a_new_proposal(
            self, tmp_path):
        # The control for the test above: the same card and span with a NEW
        # content identity is a new revision and gets its own receipt.
        root = records_tree(tmp_path)
        document = reading(root, [claim(now="她说周五交付")])
        applied(tmp_path, root, document, delegation=GRANT)
        later = reading(root, [claim("c2", now="她改到周一交付")],
                        content_identity="b" * 64)
        receipt = applied(tmp_path, root, later, delegation=GRANT,
                          content_identity="b" * 64)
        assert receipt["reused"] is False
        assert "她改到周一交付" in (root / CARD).read_text(encoding="utf-8")

    def test_a_card_edited_between_the_reading_and_the_write_is_refused(
            self, tmp_path):
        # `resolve` checked the digest; this checks it again at the write. The
        # gap between the two is a person's editing session.
        root = records_tree(tmp_path)
        document = reading(root)
        result = resolved(tmp_path, root, document, delegation=GRANT)
        plan = us.commit(result, at(tmp_path, document))
        (root / CARD).write_text(ALICE_PROSE + "\n人刚刚加了一句。\n",
                                 encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            us.write(plan, root, committed_at="2026-09-23T10:00:00+08:00")
        assert "has changed since this reading was made" in str(caught.value)

    def test_an_unreadable_receipt_is_not_a_missing_receipt(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        applied(tmp_path, root, document, delegation=GRANT)
        receipts = list(
            (root / "information" / "understanding" / ".serveros").glob(
                "receipt-*.json"))
        assert len(receipts) == 1
        receipts[0].write_text("{ this is not json", encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            applied(tmp_path, root, document, delegation=GRANT)
        assert "duplicate it" in str(caught.value)

    def test_committed_at_must_come_from_the_caller(self, tmp_path):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root), delegation=GRANT)
        plan = us.commit(result, at(tmp_path, reading(root)))
        with pytest.raises(us.Refused) as caught:
            us.write(plan, root, committed_at="")
        assert "committed_at must be supplied" in str(caught.value)

    def test_a_relative_root_is_refused(self, tmp_path, monkeypatch):
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root), delegation=GRANT)
        plan = us.commit(result, at(tmp_path, reading(root)))
        with pytest.raises(us.Refused) as caught:
            us.write(plan, Path("understanding"),
                     committed_at="2026-09-23T10:00:00+08:00")
        assert "is not absolute" in str(caught.value)

    def test_a_claim_whose_owning_card_does_not_exist_is_refused_not_partial(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(
            root, [claim(card="information/understanding/people/nobody.md")])
        result = resolved(tmp_path, root, document, delegation=GRANT)
        plan = us.commit(result, at(tmp_path, document))
        with pytest.raises(us.Refused) as caught:
            us.write(plan, root, committed_at=STAMP)
        assert "does not exist" in str(caught.value)
        assert "author of the baseline" in str(caught.value)

    def test_a_claim_pointing_outside_the_understanding_layer_is_refused(
            self, tmp_path):
        # The guard on the join. A card path is records-root-relative, and the
        # only thing standing between that and a reading that edits a source is
        # this check.
        root = records_tree(tmp_path)
        (root / "information" / "raw" / "feishu").mkdir(parents=True)
        source = root / "information" / "raw" / "feishu" / "notes.md"
        source.write_text("原始材料\n", encoding="utf-8")
        document = reading(root, [claim(card="information/raw/feishu/notes.md")])
        result = resolved(tmp_path, root, document, delegation=GRANT)
        plan = us.commit(result, at(tmp_path, document))
        with pytest.raises(us.Refused) as caught:
            us.write(plan, root, committed_at=STAMP)
        assert "writes understanding cards and nothing else" in str(caught.value)
        assert source.read_text(encoding="utf-8") == "原始材料\n"

    def test_two_claims_in_one_card_share_one_fence(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("c1"), claim("c2", now="她说下周一交付")])
        receipt = commit_and_write(tmp_path, root, delegation=GRANT,
                                   document=document)
        assert receipt["state"] == "COMPLETE"
        body = (root / CARD).read_text(encoding="utf-8")
        assert body.count(us.FENCE_BEGIN) == 1
        assert "她说周五交付" in body and "她说下周一交付" in body

    def test_a_card_with_no_fence_gains_one_and_keeps_its_prose(self, tmp_path):
        root = records_tree(tmp_path)
        assert us.FENCE_BEGIN not in ALICE_PROSE
        commit_and_write(tmp_path, root, delegation=GRANT)
        body = (root / CARD).read_text(encoding="utf-8")
        assert body.startswith(ALICE_PROSE)
        assert body.count(us.FENCE_BEGIN) == 1

    def test_the_receipt_names_what_it_changed_by_digest(self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["before"] == us.card_digest(ALICE_PROSE)
        assert entries[CARD]["after"] == digest_of(root, CARD)
        assert entries[CARD]["claims"] == ["c1"]


# --------------------------------------------------------------------------
# a person's yes: what it reaches, and what it is bound to
# --------------------------------------------------------------------------

def acceptance(resolution: dict, *, claims, by: str = "alice", at: str = STAMP,
               **overrides) -> dict:
    """An acceptance as a person writes one: copied off the resolution.

    Every identity field is copied from the RESOLUTION rather than rebuilt from
    the reading, because copying is what a person does — and it is why
    `reading_digest` is on the resolution at all. A helper that constructed the
    key itself would keep agreeing with the module after the module changed what
    the key is, which is the one thing this helper must not do.
    """
    source = resolution["reading"]
    read = resolution["read"]
    row = {"card": source["card"], "manifest": source["manifest"],
           "from_position": source["from_position"],
           "to_position": source["to_position"],
           "card_digest": read["card_digest"],
           "content_identity": read["content_identity"],
           "reading_digest": resolution["reading_digest"],
           "claims": list(claims), "by": by, "at": at}
    row.update(overrides)
    return row


#: A proposal the machine will not commit on its own, however certain it is.
DEADLINE = ("deadline",)


class TestAPersonsYesIsWhatMakesAMajorChangeCommittable:

    def test_without_a_person_a_major_change_is_prepared_and_never_written(
            self, tmp_path):
        # The gap this slice was opened for. `user_confirmed` was a constant
        # nothing ever produced, so a claim about a deadline could be prepared
        # and never written — the far end of the chain ("必需结果真正交到用户/文档/
        # 下游") was simply missing.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        result = resolved(tmp_path, root, document, delegation=GRANT)
        assert result["committed"] == []
        assert result["awaiting"][0]["confirmation"] == us.AWAITING_DECISION
        assert "deadline" in result["awaiting"][0]["confirmation_why"]
        assert us.USER_CONFIRMED in us.CONFIRMATIONS

    def test_a_person_accepting_it_is_the_thing_that_commits_it(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(
                              tmp_path, acceptance(proposal, claims=["c1"])))
        assert [entry["id"] for entry in result["committed"]] == ["c1"]
        assert result["committed"][0]["confirmation"] == us.USER_CONFIRMED
        assert result["acceptance"]["applied"] == ["c1"]
        # The reason the machine could not commit it is not lost when a person
        # does: a reader has to be able to see why this one was asked about.
        assert "deadline" in result["committed"][0]["confirmation_why"]
        assert "accepted by alice" in result["committed"][0]["confirmation_why"]

    def test_the_card_says_a_person_confirmed_it_rather_than_the_machine(
            self, tmp_path):
        # The line used to be rendered with `agent_reconciled` hardcoded, which
        # was invisible while nothing else could commit and would have gone on
        # asserting that a machine reconciled a claim somebody had signed.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        plan = us.commit(resolved(tmp_path, root, document, delegation=GRANT,
                                  accepted=accepted_document(
                                      tmp_path, acceptance(proposal, claims=["c1"]))),
                         at(tmp_path, document))
        us.write(plan, root, committed_at=STAMP)
        body = (root / CARD).read_text(encoding="utf-8")
        assert us.USER_CONFIRMED in body
        assert us.AGENT_RECONCILED not in body

    def test_a_delegated_claim_still_reads_as_the_machines_own(self, tmp_path):
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        body = (root / CARD).read_text(encoding="utf-8")
        assert us.AGENT_RECONCILED in body
        assert us.USER_CONFIRMED not in body

    def test_a_claim_the_delegation_already_committed_is_not_needing_the_person(
            self, tmp_path):
        # The other direction of the same rule. A marker that appeared wherever
        # somebody happened to agree with the machine would stop meaning "this
        # one needed a person", which is the only thing it is there to say.
        root = records_tree(tmp_path)
        document = reading(root)
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(
                              tmp_path, acceptance(proposal, claims=["c1"])))
        assert [entry["id"] for entry in result["committed"]] == ["c1"]
        assert result["committed"][0]["confirmation"] == us.AGENT_RECONCILED
        assert result["acceptance"]["applied"] == []
        assert result["acceptance"]["covered_by_the_delegation_already"] == ["c1"]

    def test_an_id_that_is_not_in_this_reading_is_reported_and_the_rest_applies(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(
                              tmp_path,
                              acceptance(proposal, claims=["c9", "c1"])))
        assert result["acceptance"]["applied"] == ["c1"]
        assert result["acceptance"]["not_in_this_reading"] == ["c9"]

    def test_accepting_a_retraction_is_not_a_way_to_commit_one(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("r1", kind=us.RETRACTION,
                                        supersedes=("c9",))])
        proposal = resolved(tmp_path, root, document)
        result = resolved(tmp_path, root, document,
                          accepted=accepted_document(
                              tmp_path, acceptance(proposal, claims=["r1"])))
        assert result["committed"] == []
        assert result["retracted"] == ["c9"]
        assert [entry["id"] for entry in result["acceptance"]["not_committable"]] \
            == ["r1"]
        assert "takes effect locally at once" in \
            result["acceptance"]["not_committable"][0]["why"]

    def test_a_resolution_with_no_acceptance_supplied_says_so(self, tmp_path):
        root = records_tree(tmp_path)
        report = resolved(tmp_path, root, reading(root))["acceptance"]
        assert report["supplied"] is None
        assert report["as_written"] is None
        assert report["applied"] == []

    def test_the_identity_the_report_shows_is_the_one_the_resolution_carries(
            self, tmp_path):
        # What a person copies is what the run will match on, so the report's
        # identity is checked against the resolution's own fields rather than
        # against the function that built it: an extra normalisation inside the
        # key would make every acceptance a person writes from this screen fail
        # to match, and it would fail silently — as an acceptance about nothing.
        root = records_tree(tmp_path)
        result = resolved(tmp_path, root, reading(root))
        source, read = result["reading"], result["read"]
        assert result["acceptance"]["identity"] == [
            source["card"], source["manifest"], source["from_position"],
            source["to_position"], read["card_digest"],
            read["content_identity"], result["reading_digest"]]


class TestAConfirmationBelongsToTheTextItWasMadeAbout:

    def test_an_acceptance_for_the_old_card_is_reported_and_commits_nothing(
            self, tmp_path):
        # §2's own requirement, from the person's side: the path and the range
        # are unchanged, and the card they read is not the card there is now.
        # The old confirmation must not carry over — and the run has to SAY so,
        # because a person who supplied a file and watched nothing happen would
        # otherwise conclude the tool ignored them.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        row = acceptance(resolved(tmp_path, root, document, delegation=GRANT),
                         claims=["c1"])
        (root / CARD).write_text(ALICE_PROSE + "\n她后来改了主意。\n",
                                 encoding="utf-8")
        again = reading(root, [claim(touches=DEADLINE)])
        result = resolved(tmp_path, root, again, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row))
        assert result["committed"] == []
        assert result["acceptance"]["applied"] == []
        stale = result["acceptance"]["for_other_revisions"]
        assert len(stale) == 1
        assert stale[0]["by"] == "alice"
        assert "the understanding card has been rewritten since it was accepted" \
            in stale[0]["differs"]
        assert "the claims this reading produced are not the ones that were " \
               "accepted" in stale[0]["differs"]

    def test_an_acceptance_for_the_old_content_is_reported(self, tmp_path):
        # The message was rewritten inside the same span. Nothing about the
        # PLACE looks different, which is exactly why the version has to be part
        # of the identity.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        row = acceptance(resolved(tmp_path, root, document, delegation=GRANT),
                         claims=["c1"])
        again = reading(root, [claim(touches=DEADLINE)], content_identity="b" * 64)
        result = resolved(tmp_path, root, again, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row),
                          content_identity="b" * 64)
        assert result["committed"] == []
        differs = result["acceptance"]["for_other_revisions"][0]["differs"]
        assert "the content this reading is about has changed since it was " \
               "accepted" in differs
        assert "the understanding card has been rewritten since it was accepted" \
            not in differs

    def test_an_acceptance_naming_another_reading_is_reported(self, tmp_path):
        # Same place, same source revision, same card: only the reading differs,
        # which is the case a place-and-range identity cannot see at all.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        row = acceptance(resolved(tmp_path, root, document, delegation=GRANT),
                         claims=["c1"], reading_digest="b" * 64)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row))
        assert result["committed"] == []
        assert result["acceptance"]["for_other_revisions"][0]["differs"] == [
            "the claims this reading produced are not the ones that were accepted"]

    def test_an_acceptance_about_another_span_altogether_is_not_reported_here(
            self, tmp_path):
        # The other side of the judgement: a file holding several acceptances
        # must not have the ones about somebody else's span listed as failures.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        row = acceptance(resolved(tmp_path, root, document, delegation=GRANT),
                         claims=["c1"], to_position=30)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row))
        assert result["acceptance"]["for_other_revisions"] == []


class TestTheAcceptanceFileIsAuthoredInputAndIsChecked:

    def test_an_unknown_field_is_refused(self, tmp_path):
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, {"card": CARD, "mood": "confident"})
        assert "unexpected field(s)" in str(caught.value)
        assert "mood" in str(caught.value)

    def test_an_acceptance_that_names_no_claim_is_refused(self, tmp_path):
        # An acceptance that names nothing would read as a signature on whatever
        # the reading happened to contain.
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, acceptance_probe(claims=[]))
        assert "accepted nothing" in str(caught.value)

    def test_an_acceptance_with_no_author_is_refused(self, tmp_path):
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, acceptance_probe(by=""))
        assert "by" in str(caught.value)

    def test_a_claims_field_that_is_not_a_list_of_ids_is_refused(self, tmp_path):
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, acceptance_probe(claims=["c 1"]))
        assert "is not a claim id" in str(caught.value)

    def test_an_acceptance_that_omits_the_revision_is_refused(self, tmp_path):
        # Absent and null are different answers, and the reader has to tell them
        # apart: `get` returns None for both, and one of them is a person's yes
        # to a path that a later edit would inherit. Both spellings are checked
        # here, because a test that only asserted the refusal would pass just as
        # well against a reader that refused the null one too — and would then be
        # asserting the opposite of what it says.
        row = acceptance_probe(claims=["c1"])
        row.pop("card_digest")
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, row)
        assert "names a place and not a revision" in str(caught.value)
        assert "null is accepted only when the reading itself carries null" \
            in str(caught.value)
        spelled = acceptance_probe(claims=["c1"], card_digest=None)
        assert len(us.read_acceptances(
            accepted_file(tmp_path, spelled, name="null.jsonl"))) == 1

    def test_a_revision_the_reading_could_not_identify_may_be_written_as_null(
            self, tmp_path):
        # The reader does not always identify content, and the field is `None`
        # then. An acceptance for such a reading must be writable, and it is
        # still bound to the card's baseline, so it is not unbound either.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)], content_identity=None)
        proposal = resolved(tmp_path, root, document, delegation=GRANT,
                            content_identity=None)
        row = acceptance(proposal, claims=["c1"], content_identity=None)
        result = resolved(tmp_path, root, document, delegation=GRANT,
                          accepted=accepted_document(tmp_path, row),
                          content_identity=None)
        assert result["acceptance"]["applied"] == ["c1"]
        assert result["acceptance"]["for_other_revisions"] == []

    def test_two_acceptances_about_one_proposal_are_refused(self, tmp_path):
        with pytest.raises(us.Refused) as caught:
            accepted_document(tmp_path, acceptance_probe(claims=["c1"]),
                              acceptance_probe(claims=["c1"], by="bruce"))
        assert "two acceptances about one proposal" in str(caught.value)

    def test_blank_lines_are_not_acceptances(self, tmp_path):
        path = accepted_file(tmp_path, acceptance_probe(claims=["c1"]))
        path.write_text("\n" + path.read_text(encoding="utf-8") + "\n\n",
                        encoding="utf-8")
        assert len(us.read_acceptances(path)) == 1


def acceptance_probe(*, claims=None, by: str = "alice", at: str = STAMP,
                     **overrides) -> dict:
    """A well-formed acceptance for a run that has not happened yet.

    For the tests about READING the file, where there is no resolution to copy
    from and the identity only has to be shaped correctly.
    """
    row = {"card": CARD, "manifest": MANIFEST, "from_position": 10,
           "to_position": 20, "card_digest": "a" * 64,
           "content_identity": "a" * 64, "reading_digest": "c" * 64,
           "claims": ["c1"] if claims is None else list(claims),
           "by": by, "at": at}
    row.update(overrides)
    return row


class TestTheWriteRecordsWhichAcceptanceItWasMadeUnder:

    def test_later_decisions_append_without_rewriting_facts_or_receipts(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(), claim("c2", touches=DEADLINE),
                                  claim("c3", touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        first = applied(tmp_path, root, document, delegation=GRANT)
        original = us._receipt_path(us.understanding_root(root), document)
        original_bytes = original.read_bytes()
        assert first["accepted"] is None
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c2"]))
        second = applied(tmp_path, root, document, accepted=rows)
        assert second["accepted"]["claims"] == ["c2"]
        assert original.read_bytes() == original_bytes
        after = (root / CARD).read_bytes()
        replay = applied(tmp_path, root, document, accepted=rows)
        assert replay["reused"] is True
        assert (root / CARD).read_bytes() == after
        third = applied(tmp_path, root, document,
                        accepted=accepted_document(tmp_path, acceptance(proposal, claims=["c3"])))
        assert len(third["previous_receipts"]) == 2
        assert original.read_bytes() == original_bytes
        body = (root / CARD).read_text()
        assert body.count(us.AGENT_RECONCILED) == 1
        assert body.count(us.USER_CONFIRMED) == 2
        recall = _load("claim_recall_later", "claim_recall.py")
        assert set(recall.build_index(root)["claims"]) == {"c1", "c2", "c3"}

    @pytest.mark.parametrize("change", ["card", "source", "reading"])
    def test_later_acceptance_revalidates_instead_of_bypassing_changed_input(self, tmp_path, change):
        root = records_tree(tmp_path)
        document = reading(root, [claim(), claim("c2", touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        applied(tmp_path, root, document, delegation=GRANT)
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c2"]))
        if change == "card":
            (root / CARD).write_text((root / CARD).read_text() + "\n人的新批注\n")
        if change == "reading":
            document["claims"][1]["assertion"]["now"] = "改变了的提案"
        before = (root / CARD).read_bytes()
        with pytest.raises(us.Refused):
            applied(tmp_path, root, document, accepted=rows,
                    **({"content_identity": "b" * 64} if change == "source" else {}))
        assert (root / CARD).read_bytes() == before

    def test_later_acceptance_can_resume_its_view_without_overwriting_first_receipt(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(), claim("c2", touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        applied(tmp_path, root, document, delegation=GRANT)
        original = us._receipt_path(us.understanding_root(root), document)
        original_bytes = original.read_bytes()
        (root / VIEW).unlink()
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c2"]))
        second = applied(tmp_path, root, document, accepted=rows)
        assert second["state"] == "PARTIAL"
        (root / VIEW).write_text(VIEW_PROSE)
        resumed = applied(tmp_path, root, document, accepted=rows)
        assert resumed["state"] == "COMPLETE"
        assert original.read_bytes() == original_bytes

    def test_the_receipt_names_the_person_and_the_acceptance_by_digest(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c1"]))
        receipt = commit_and_write(tmp_path, root, document=document,
                                   accepted=rows)
        assert receipt["accepted"]["by"] == "alice"
        assert receipt["accepted"]["claims"] == ["c1"]
        assert receipt["accepted"]["digest"] == \
            us.acceptance_digest(next(iter(rows.values())))
        assert us.USER_CONFIRMED in (root / CARD).read_text(encoding="utf-8")

    def test_a_replay_under_the_same_acceptance_writes_once(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c1"]))
        first = applied(tmp_path, root, document, delegation=GRANT, accepted=rows)
        after = (root / CARD).read_bytes()
        second = applied(tmp_path, root, document, delegation=GRANT, accepted=rows)
        assert first["state"] == "COMPLETE"
        assert second["reused"] is True
        assert (root / CARD).read_bytes() == after

    def test_a_replay_holding_a_different_acceptance_is_refused_by_name(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        first = accepted_document(tmp_path, acceptance(proposal, claims=["c1"]),
                                  name="one.jsonl")
        applied(tmp_path, root, document, delegation=GRANT, accepted=first)
        other = accepted_document(
            tmp_path, acceptance(proposal, claims=["c1"], by="bruce"),
            name="two.jsonl")
        with pytest.raises(us.Refused) as caught:
            applied(tmp_path, root, document, delegation=GRANT, accepted=other)
        assert "while this run holds an acceptance" in str(caught.value)
        # The remedy has to be one that WORKS. Reading the source again does not
        # reach this: the proposal is the place and the source revision, so an
        # unchanged source re-read is the same proposal and finds this receipt.
        assert "Reading the source again does not help" in str(caught.value)
        assert "removes this receipt" in str(caught.value)

    def test_a_receipt_written_without_an_acceptance_refuses_one_arriving_after(
            self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        # The acceptance is authored from the proposal as it stood BEFORE the
        # write, because that is when a person would have been asked.
        row = acceptance(resolved(tmp_path, root, document, delegation=GRANT),
                         claims=["c1"])
        applied(tmp_path, root, document, delegation=GRANT)
        with pytest.raises(us.Refused) as caught:
            applied(tmp_path, root, document, delegation=GRANT,
                    accepted=accepted_document(tmp_path, row))
        assert "it was written for no acceptance" in str(caught.value)

    def test_a_first_run_that_commits_nothing_leaves_no_receipt_to_hide_behind(
            self, tmp_path):
        # The ordinary order of events: the Agent prepares, the person is asked,
        # the person answers. If a run that committed nothing left a receipt,
        # the acceptance arriving afterwards would find it and be refused — the
        # proposal would be closed by a write that changed no file.
        root = records_tree(tmp_path)
        document = reading(root, [claim(touches=DEADLINE)])
        before = (root / CARD).read_bytes()
        nothing = applied(tmp_path, root, document, delegation=GRANT)
        assert nothing["state"] == "NOTHING_TO_WRITE"
        assert nothing["written"] == []
        assert (root / CARD).read_bytes() == before
        assert not (root / "information" / "understanding" / ".serveros").exists()

        proposal = resolved(tmp_path, root, document, delegation=GRANT)
        row = acceptance(proposal, claims=["c1"])
        after = applied(tmp_path, root, document, delegation=GRANT,
                        accepted=accepted_document(tmp_path, row))
        assert after["state"] == "COMPLETE"
        assert us.USER_CONFIRMED in (root / CARD).read_text(encoding="utf-8")

    def test_the_write_is_a_second_opinion_on_the_acceptance(self, tmp_path):
        # `apply` finds the receipt before it measures the source, so its check
        # is the one every ordinary run takes; this one is reached only by a
        # caller that goes to `write` directly, which is why it has to be
        # exercised that way. A check nothing exercises is a comment, and this is
        # the instant of writing — the instant the earlier question cannot speak
        # for, because between the two a person can edit a card.
        root = records_tree(tmp_path)
        document = reading(root)
        parsed = at(tmp_path, document)
        measured = current(root)
        proposal = us.resolve(parsed, measured, GRANT)
        first = us.write(us.commit(proposal, parsed), root, committed_at=STAMP)
        assert first["accepted"] is None
        rows = accepted_document(tmp_path, acceptance(proposal, claims=["c1"]))
        second = us.commit(us.resolve(parsed, measured, GRANT, rows), parsed)
        with pytest.raises(us.Refused) as caught:
            us.write(second, root, committed_at=STAMP)
        assert "it was written for no acceptance" in str(caught.value)
        body = (root / CARD).read_text(encoding="utf-8")
        assert body.count(us.FENCE_BEGIN) == 1
        assert body.count("她说周五交付") == 1


# --------------------------------------------------------------------------
# a line a person wrote inside the fence is not this module's to remove
# --------------------------------------------------------------------------

CORRECTION = "- 我核对过：这条不是他说的，是转述。"


def fenced(*lines: str) -> str:
    """What this module's own block looks like and nothing else in it.

    Spelled out here rather than read off the module, so that a card's shape is
    pinned by the test instead of by the writer agreeing with itself. The
    newlines are part of it: a block whose markers sit flush against the lines
    is a different card, and one that grows a `\n` per commit is a different
    bug — both pass every "the text is in there" assertion.
    """
    return (us.FENCE_BEGIN + "\n" + us.CLAIMS_BEGIN + "\n" + "\n".join(lines)
            + "\n" + us.CLAIMS_END + "\n" + us.FENCE_END)


def corrected(root: Path, *, line: str = CORRECTION, where: str = "after") -> None:
    """A person's line added INSIDE the fence, above or below the module's block.

    Inside the fence on purpose: that is where the claim is, so that is where
    somebody disputing it writes. The two placements are asked separately
    because the bytes above the module's block and the bytes below it are
    different slots in the splice, and one surviving says nothing about the
    other.
    """
    card = root / CARD
    head, body, tail = us.fence_parts(card.read_text(encoding="utf-8"))
    before, claims, after = us.claims_region(body)
    if where == "after":
        body = before + us.CLAIMS_BEGIN + claims + us.CLAIMS_END + line + after
    else:
        body = before + line + us.CLAIMS_BEGIN + claims + us.CLAIMS_END + after
    card.write_text(head + us.FENCE_BEGIN + body + us.FENCE_END + tail,
                    encoding="utf-8")


class TestALineAPersonWroteInsideTheFence:

    def test_a_correction_inside_the_fence_survives_the_next_commit(
            self, tmp_path):
        # The reproduction this class exists for. The module used to replace
        # everything between the fence markers as one unit, so a correction
        # written next to a claim — inside the fence, because that is where the
        # claim is — was destroyed by the next commit, and the receipt reported
        # success. §2: 下一轮旧的自动摘要不能覆盖用户的更正.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        corrected(root)
        # The reading is made AFTER the card was edited: its digest binds it to
        # the text it was reasoned against, so a reading made before the edit
        # would be refused instead of written over the correction. That refusal
        # is the last test in this class.
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        card = (root / CARD).read_text(encoding="utf-8")
        assert card.count(CORRECTION) == 1
        assert card.startswith(ALICE_PROSE)

    @pytest.mark.parametrize("where", ["after", "before"])
    def test_a_second_commit_adds_a_line_and_does_not_duplicate_the_block(
            self, tmp_path, where):
        # This test used to assert `"pos 12" not in card` — the first claim's
        # line GONE after the second commit. That was stronger than the stated
        # intent ("the module's own lines are replaced and not duplicated"), and
        # it is what let the line-loss defect pass the suite: a card is a
        # person's view of everything understood about a subject, so a commit
        # about one span must not delete what an earlier commit about another
        # span put there, while its receipt went on naming it. Both claims are
        # asserted present, each exactly once, which is what "not duplicated"
        # has to mean once the block accumulates.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        corrected(root, where=where)
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        card = (root / CARD).read_text(encoding="utf-8")
        assert card.count("pos 12") == 1
        assert card.count("pos 28") == 1
        assert card.count(CORRECTION) == 1
        assert card.count(us.FENCE_BEGIN) == 1
        assert card.count(us.CLAIMS_BEGIN) == 1
        assert card.count(us.CLAIMS_END) == 1

    def test_a_persons_line_that_looks_like_one_of_ours_is_still_theirs(
            self, tmp_path):
        # The tempting implementation is "remove every line shaped like one of
        # ours", and it would pass every test above while eating this one.
        lookalike = f"- **[{us.OBSERVED_FACT}]** alice：这句是人写的"
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        corrected(root, line=lookalike)
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        card = (root / CARD).read_text(encoding="utf-8")
        assert card.count(lookalike) == 1

    def test_the_same_claim_written_twice_does_not_grow_the_card(self, tmp_path):
        # Asked of the splice rather than through a second commit: a second
        # reading over one span is refused for an unrelated reason (two readings
        # of one span cannot both be the committed one), so it cannot answer
        # this. What is being pinned here is that the merge is idempotent — a
        # `\n` added on each write would pass every assertion above, and so would
        # a merge that matched on the line's text instead of its id.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        text = (root / CARD).read_text(encoding="utf-8")
        claimed = [("c9", "- `c9` **[OBSERVED_FACT]** alice：她说下周一交付")]
        once = us._replace_fence(text, claimed)
        assert once != text and "她说下周一交付" in once
        assert once.count("她说下周一交付") == 1
        assert us._replace_fence(once, claimed) == once

    def test_a_correction_written_above_the_block_survives_too(self, tmp_path):
        # The other slot. A person who writes the correction first and the claim
        # under it is placing it in `before`, which is a different slice.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        corrected(root, where="before")
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        card = (root / CARD).read_text(encoding="utf-8")
        assert card.count(CORRECTION) == 1
        assert card.index(CORRECTION) < card.index("pos 28")

    def test_a_correction_makes_a_reading_made_before_it_stale(self, tmp_path):
        # The other half of the same promise, and the reason the two belong in
        # one class: keeping the correction is no use if a reading reasoned
        # against the older text still commits. Editing the card moves its
        # digest, so that reading is refused rather than written over what a
        # person said.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        corrected(root)
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                             to_position=60)
        assert "the understanding card was rewritten" in str(caught.value)
        assert (root / CARD).read_text(encoding="utf-8").count(CORRECTION) == 1



class TestAFenceThisModuleCannotReadIsRefused:

    def test_lines_written_before_the_ids_existed_are_refused_not_replaced(
            self, tmp_path):
        # This test used to pin the opposite: a fence with no claims region was
        # migrated by replacing its whole body, and the old line was asserted
        # GONE. That judgement was about OWNERSHIP — back then the writer
        # replaced the whole body on every commit, so every line in such a body
        # is one the module put there and none of it is a person's — and
        # ownership was sufficient while replacing was what the writer did.
        # It stopped being sufficient when the body became a merge: the lines in
        # an old body carry no id, so the merge can neither match them nor say
        # which claim each one is, and both available answers lose something —
        # dropping them takes claims out of a card, keeping them leaves lines
        # nothing can match on again. So the commit is refused and a person
        # looks at the card.
        root = records_tree(tmp_path)
        card = root / CARD
        old = (ALICE_PROSE + "\n" + us.FENCE_BEGIN + "\n- 老的自动摘要行\n"
               + us.FENCE_END)
        card.write_text(old, encoding="utf-8")
        document = reading(root, [claim("c2", now="她说周三交付", positions=(28,),
                                        excerpt="周三给你")], to_position=60)
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                             to_position=60)
        assert "carries no claim id this module can read" in str(caught.value)
        assert str(card) in str(caught.value)
        assert card.read_text(encoding="utf-8") == old

    def test_an_empty_fence_body_takes_the_commit_without_a_refusal(
            self, tmp_path):
        # The boundary of the judgement above, pinned so that it is the
        # unreadable LINES that are refused and not the absence of a region: a
        # fence with nothing in it has nothing to merge and nothing to lose.
        root = records_tree(tmp_path)
        card = root / CARD
        card.write_text(ALICE_PROSE + "\n" + us.FENCE_BEGIN + us.FENCE_END,
                        encoding="utf-8")
        document = reading(root, [claim("c2", now="她说周三交付", positions=(28,),
                                        excerpt="周三给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        line = ("- `c2` **[OBSERVED_FACT]** alice：她说周三交付"
                "（agent_reconciled；来源 pos 28；excerpt: 周三给你）")
        assert card.read_text(encoding="utf-8") == ALICE_PROSE + "\n" + fenced(line)

    @pytest.mark.parametrize("half,diagnosis", [
        pytest.param(ALICE_PROSE + "\n" + us.FENCE_BEGIN + "\n- 半截的\n",
                     "the fence's begin marker and not its counterpart",
                     id="fence-begin-alone"),
        pytest.param(ALICE_PROSE + "\n" + us.FENCE_END + "\n- 半截的\n",
                     "the fence's end marker and not its counterpart",
                     id="fence-end-alone"),
        pytest.param(ALICE_PROSE + "\n" + us.FENCE_BEGIN + "\n"
                     + us.CLAIMS_BEGIN + "\n- 半截的\n" + us.FENCE_END,
                     "the claims region's begin marker and not its counterpart",
                     id="claims-begin-alone-in-a-fence"),
        pytest.param(ALICE_PROSE + "\n" + us.FENCE_BEGIN + "\n- 半截的\n"
                     + us.CLAIMS_END + "\n" + us.FENCE_END,
                     "the claims region's end marker and not its counterpart",
                     id="claims-end-alone-in-a-fence"),
    ])
    def test_one_marker_without_its_counterpart_is_refused_and_changes_nothing(
            self, tmp_path, half, diagnosis):
        # Four shapes, two consequences, one answer. A half FENCE puts a second
        # copy of the lines in the card and puts them outside any guard
        # (`fence_parts` calls such a card all head). A half CLAIMS region reads
        # as no region, so the body it is half-marking goes into the merge as
        # though the module owned all of it — which is what the whole-body
        # migration used to do on purpose, and what the merge now refuses to do
        # at all when the lines carry no id. Neither half is a shape this module
        # ever wrote, so it cannot say which lines are its own.
        #
        # A CLAIMS marker in the prose OUTSIDE any fence is not one of these
        # shapes and is not refused: `claims_region` only ever looks inside a
        # fence, so out there the marker is somebody's text and gets appended
        # after, not read.
        root = records_tree(tmp_path)
        card = root / CARD
        card.write_text(half, encoding="utf-8")
        document = reading(root, [claim("c2", now="她说周四交付", positions=(28,),
                                        excerpt="周四给你")], to_position=60)
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                             to_position=60)
        assert diagnosis in str(caught.value)
        assert card.read_text(encoding="utf-8") == half

    def test_a_claims_marker_in_the_prose_is_a_persons_text_and_not_a_region(
            self, tmp_path):
        # The other side of the same judgement, pinned rather than asserted in
        # prose: `claims_region` only ever looks inside a fence, so a marker out
        # in the prose is somebody's text. Refusing it would mean a person
        # cannot write this string in their own card, and there is nothing to
        # refuse — the module appends its own fence after it and reads only
        # what is in there.
        root = records_tree(tmp_path)
        card = root / CARD
        prose = ALICE_PROSE + "\n" + us.CLAIMS_BEGIN + "\n"
        card.write_text(prose, encoding="utf-8")
        document = reading(root, [claim("c2", now="她说周五交付", positions=(12,))],
                           to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        body = card.read_text(encoding="utf-8")
        assert body.startswith(prose)
        assert body.count(us.CLAIMS_BEGIN) == 2
        assert body.count(us.CLAIMS_END) == 1


# --------------------------------------------------------------------------
# a commit that fails in a view is PARTIAL, and resumable
# --------------------------------------------------------------------------

class TestAViewThatFailsIsPartial:

    def break_view(self, root: Path) -> None:
        (root / VIEW).unlink()
        (root / VIEW).mkdir()

    def test_a_view_that_cannot_be_written_makes_the_commit_partial(
            self, tmp_path):
        root = records_tree(tmp_path)
        self.break_view(root)
        document = reading(root)
        receipt = applied(tmp_path, root, document, delegation=GRANT)
        assert receipt["state"] == "PARTIAL"
        assert [entry["view"] for entry in receipt["pending_views"]] == [VIEW]
        owed = receipt["pending_views"][0]["references"]
        assert list(owed) == ["c1"], \
            "a resume must not have to re-derive the references it still owes"
        assert f"`{CARD}`" in owed["c1"] and "c1" in owed["c1"]
        # The claim IS in its owning card. Reporting COMPLETE would say the
        # consumers agree; reporting nothing would lose the claim.
        assert "她说周五交付" in (root / CARD).read_text(encoding="utf-8")

    def test_applying_again_resumes_the_pending_view_and_only_that(
            self, tmp_path):
        root = records_tree(tmp_path)
        self.break_view(root)
        document = reading(root)
        first = applied(tmp_path, root, document, delegation=GRANT)
        owner_after_first = (root / CARD).read_bytes()
        (root / VIEW).rmdir()
        (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")
        second = applied(tmp_path, root, document, delegation=GRANT)
        assert second["state"] == "COMPLETE"
        assert second["pending_views"] == []
        assert second["reused"] is True
        assert [entry["card"] for entry in second["written"]] == [CARD, VIEW]
        assert len(second["written"]) == len(first["written"]) + 1
        # The owning card was not written a second time: it may have been edited
        # since, and this module does not get a second opinion about text it has
        # already committed.
        assert (root / CARD).read_bytes() == owner_after_first

    def test_a_resume_that_still_fails_stays_partial_and_does_not_lie(
            self, tmp_path):
        root = records_tree(tmp_path)
        self.break_view(root)
        document = reading(root)
        applied(tmp_path, root, document, delegation=GRANT)
        second = applied(tmp_path, root, document, delegation=GRANT)
        assert second["state"] == "PARTIAL"
        assert [entry["view"] for entry in second["pending_views"]] == [VIEW]


# --------------------------------------------------------------------------
# the receipt names its claims
# --------------------------------------------------------------------------

class TestTheReceiptNamesItsClaims:
    """A receipt that named no claim could not answer the second question.

    The first question a receipt answers is whether anything was written. The
    second is whether the fact a reader is looking for is in what was written —
    and a count of lines cannot answer it. Everything A2 does with a committed
    claim starts here: without the ids, "is this claim still recallable" has no
    subject.
    """

    def test_the_owning_card_records_the_claim_id_and_not_a_count(self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["claims"] == ["c1"]

    def test_the_view_records_the_claims_it_is_now_a_route_to(self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[VIEW]["claims"] == ["c1"]

    def test_the_record_says_which_card_owns_the_claim_and_which_only_points_at_it(
            self, tmp_path):
        # The two writes above are the same shape and carry the same `claims`
        # list, so a reader with only those two fields has to guess which card
        # HOLDS the claim. An A2 recall filter, reading a v3 receipt as written,
        # reported one claim as living in the person's card and in the project
        # view at once, and `CONFLICT` for it — with the whole of the durable
        # record in front of it. A claim has one owner and N views; the record
        # has to say which is which.
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        roles = {entry["card"]: entry["role"] for entry in receipt["written"]}
        assert roles == {CARD: us.OWNER, VIEW: us.VIEW}
        owners = [entry["card"] for entry in receipt["written"]
                  if entry["role"] == us.OWNER]
        assert owners == [CARD]

    def test_a_write_whose_role_is_neither_is_refused(self, tmp_path):
        root = records_tree(tmp_path)
        with pytest.raises(us.Refused) as caught:
            us._write_fence(root, us._understanding_root(root), CARD,
                            [("c1", "- `c1` **[OBSERVED_FACT]** alice：她说周五交付")],
                            role="MENTIONED")
        assert "neither" in str(caught.value)

    def test_the_rendered_receipt_tells_the_two_apart(self, tmp_path):
        # The rendering is what a person reads, and the distinction is what
        # stops them going to look for a claim in a card that only points at it.
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        rendered = "\n".join(us.render_receipt(receipt))
        assert f"  · [持有] {CARD}  " in rendered
        assert f"  · [引用] {VIEW}  " in rendered

    def test_two_claims_in_one_card_are_both_named_in_id_order(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("z9"), claim("a1", now="她说周一交付")])
        receipt = applied(tmp_path, root, document, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["claims"] == ["a1", "z9"]

    def test_the_line_carries_its_id_because_the_next_commit_merges_on_it(
            self, tmp_path):
        # This test used to assert the opposite — `"zz-9" not in body` — because
        # the ids were deliberately kept off the line and copied from the
        # commit's keys. That was right while the writer replaced its whole
        # block on every commit: a card held only the last commit's lines, so
        # the commit knew what was in it. A card accumulates now, so the file is
        # the only record of what it holds and the id has to be in the file.
        #
        # The identity of the claim still does not come from the rendering: the
        # id comes from the reading and is validated there, the merge refuses a
        # line whose first token is not a claim id, and the receipt's ids are the
        # merge's key set — read back out of the file that was written.
        root = records_tree(tmp_path)
        document = reading(root, [claim("zz-9", now="她说周五交付")])
        receipt = applied(tmp_path, root, document, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["claims"] == ["zz-9"]
        assert us._line_id("- `zz-9` **x**") == "zz-9"
        for card in (CARD, VIEW):
            body = (root / card).read_text(encoding="utf-8")
            assert "`zz-9`" in body
            lines = [line for line in us.claims_region(
                us.fence_parts(body)[1])[1].splitlines() if line.strip()]
            assert [us._line_id(line) for line in lines] == ["zz-9"]

    def test_a_resume_names_the_claims_it_was_owed(self, tmp_path):
        # ONE reading, applied twice. Calling `reading()` again after the first
        # write would compute a fresh `card_digest` off a card that has since
        # gained a fence, and the second application would be a different
        # reading of the same span — which is refused, and not the case here.
        root = records_tree(tmp_path)
        document = reading(root)
        (root / VIEW).unlink()
        (root / VIEW).mkdir()
        first = applied(tmp_path, root, document, delegation=GRANT)
        assert first["state"] == "PARTIAL"
        (root / VIEW).rmdir()
        (root / VIEW).write_text(VIEW_PROSE, encoding="utf-8")
        second = applied(tmp_path, root, document, delegation=GRANT)
        entries = {entry["card"]: entry for entry in second["written"]}
        assert entries[VIEW]["claims"] == ["c1"]
        assert "c1" in (root / VIEW).read_text(encoding="utf-8")

    def test_the_rendered_receipt_lists_the_ids_and_not_only_a_count(self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        rendered = "\n".join(us.render_receipt(receipt))
        assert "1 条：c1" in rendered
        assert "1 条\n" not in rendered and not rendered.endswith("1 条")

    def test_a_receipt_from_before_the_shape_changed_is_refused_by_name(
            self, tmp_path):
        # A v1 receipt is not an absent receipt and not a readable one. Its
        # `written[i].claims` was a COUNT, so a reader expecting ids would
        # report a number where the claims should be — and the write that
        # followed would duplicate a commit this module cannot read.
        path = tmp_path / "receipt-v1.json"
        path.write_text(json.dumps({
            "schema": "serveros/understanding-receipt.v1",
            "key": [CARD, MANIFEST, 10, 20, "a" * 64],
            "written": [{"card": CARD, "before": "b" * 64, "after": "c" * 64,
                         "claims": 1}],
        }), encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            us._read_receipt(path)
        assert "serveros/understanding-receipt.v1" in str(caught.value)
        assert "duplicate" in str(caught.value)

    def test_a_receipt_the_module_writes_is_one_it_can_read_back(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        receipt = commit_and_write(tmp_path, root, document=document, delegation=GRANT)
        path = us._receipt_path(us._understanding_root(root), at(tmp_path, document))
        assert us._read_receipt(path) == receipt

    def test_receipt_for_refuses_a_receipt_it_cannot_read(self, tmp_path):
        # The read-only accessor is a reader too, and the one most likely to be
        # pointed at a file somebody copied in.
        root = records_tree(tmp_path)
        document = reading(root)
        base = us._understanding_root(root)
        path = us._receipt_path(base, at(tmp_path, document))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": "something-else/1",
                                    "key": [], "written": []}), encoding="utf-8")
        with pytest.raises(us.Refused):
            us.receipt_for(at(tmp_path, document), root)


# --------------------------------------------------------------------------
# a card accumulates what is committed into it
# --------------------------------------------------------------------------

def region_edited(root: Path, *lines: str, card: str = CARD) -> None:
    """This module's own block rewritten by hand, as a merge would leave it.

    Only the bytes between the claims markers are replaced, and the bytes around
    them are read off the card rather than spelled out here: the failure this
    helper exists to set up is a line inside the region, so the region's shape
    must be whatever the writer actually produced.
    """
    target = root / card
    head, body, tail = us.fence_parts(target.read_text(encoding="utf-8"))
    before, _, after = us.claims_region(body)
    target.write_text(head + us.FENCE_BEGIN + before + us._claims_marked(list(lines))
                      + after + us.FENCE_END + tail, encoding="utf-8")


class TestACardAccumulatesWhatIsCommittedIntoIt:
    """One card holds every commit that has landed in it, and can still be merged.

    The defect this class exists for: the writer replaced its whole block on
    every commit, so a second commit into one card deleted the first commit's
    line — in the card AND in the view's reference to it — while the receipt of
    the first commit went on naming a claim that was no longer anywhere a person
    could read. That is the worst shape a defect can take here: the record says
    the understanding changed and every surface a person reads says it did not.

    The fix is not "append". A block that only ever grew would hold the replaced
    claim beside the claim replacing it, both reading as current, and a claim
    committed twice — after a resume, or after a refusal and a retry — would be
    two lines for one id with no way to order them. So a card's block is a map
    from claim id to line: this commit's ids replace, the ids a committed claim
    supersedes are removed, and every other line is kept.
    """

    def two_commits(self, tmp_path, root, *, claim_id="c2", now="她说周一交付",
                    position=32, low=30, high=40, supersedes=()):
        """Two proposals about different spans of one source, both committed.

        Two spans rather than one, because two readings of one span are refused
        for an unrelated reason and could not reach the merge at all.
        """
        first = commit_and_write(tmp_path, root, delegation=GRANT)
        document = reading(root, [claim(claim_id, now=now, positions=(position,),
                                        excerpt="第二次",
                                        supersedes=supersedes)],
                           from_position=low, to_position=high)
        second = commit_and_write(tmp_path, root, delegation=GRANT,
                                  document=document, from_position=low,
                                  to_position=high)
        return first, second, document

    def test_a_second_commit_keeps_the_first_claims_line(self, tmp_path):
        root = records_tree(tmp_path)
        first, second, _ = self.two_commits(tmp_path, root)
        assert first["written"][0]["claims"] == ["c1"]
        card = (root / CARD).read_text(encoding="utf-8")
        assert "她说周五交付" in card
        assert "她说周一交付" in card
        # In id order, so that the block is a function of the claims in it and
        # not of the order the commits arrived in.
        assert card.index("`c1`") < card.index("`c2`")
        assert card.count(us.FENCE_BEGIN) == 1
        assert card.count(us.CLAIMS_BEGIN) == 1
        assert card.startswith(ALICE_PROSE)

    def test_the_view_keeps_its_reference_to_the_first_claim(self, tmp_path):
        root = records_tree(tmp_path)
        self.two_commits(tmp_path, root)
        view = (root / VIEW).read_text(encoding="utf-8")
        assert f"- `c1` → `{CARD}`" in view
        assert f"- `c2` → `{CARD}`" in view
        assert view.startswith(VIEW_PROSE)

    def test_a_later_claim_that_sorts_earlier_goes_above_the_first(self, tmp_path):
        # The order of a block is a function of the claims in it, and this is
        # the case that tells that apart from "the order they arrived in": the
        # second commit's id sorts before the first's, so a merge that kept the
        # arrival order would put it below. Ids come from readings and are
        # arbitrary strings, so this is the ordinary case rather than a corner.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT,
                         document=reading(root, [claim("z9")]))
        document = reading(root, [claim("a1", now="她说周一交付", positions=(32,),
                                        excerpt="第二次")],
                           from_position=30, to_position=40)
        second = commit_and_write(tmp_path, root, delegation=GRANT,
                                  document=document, from_position=30,
                                  to_position=40)
        card = (root / CARD).read_text(encoding="utf-8")
        assert card.index("`a1`") < card.index("`z9`")
        view = (root / VIEW).read_text(encoding="utf-8")
        assert view.index("`a1`") < view.index("`z9`")
        entries = {entry["card"]: entry for entry in second["written"]}
        assert entries[CARD]["claims"] == ["a1", "z9"]
        assert entries[VIEW]["claims"] == ["a1", "z9"]

    def test_the_receipt_says_what_the_card_carries_and_not_only_this_runs_lines(
            self, tmp_path):
        # The reason the receipt's schema had to move again. `written[i].claims`
        # read as "what this run wrote" is a different field from "what the file
        # holds", and a reader that took the second for the first would report a
        # claim that IS in the card as absent from it.
        root = records_tree(tmp_path)
        _, second, _ = self.two_commits(tmp_path, root)
        entries = {entry["card"]: entry for entry in second["written"]}
        assert entries[CARD]["claims"] == ["c1", "c2"]
        assert entries[VIEW]["claims"] == ["c1", "c2"]
        assert entries[CARD]["removed"] == []

    def test_a_replaced_claim_is_taken_out_of_the_card_and_the_view(self, tmp_path):
        root = records_tree(tmp_path)
        _, second, _ = self.two_commits(tmp_path, root, supersedes=("c1",))
        card = (root / CARD).read_text(encoding="utf-8")
        assert "她说周五交付" not in card
        assert "她说周一交付" in card
        view = (root / VIEW).read_text(encoding="utf-8")
        assert "`c1`" not in view and "`c2`" in view
        entries = {entry["card"]: entry for entry in second["written"]}
        assert entries[CARD]["removed"] == ["c1"]
        assert second["superseded"] == ["c1"]
        assert second["superseded_unmet"] == []

    def test_a_supersede_that_found_no_line_says_so_instead_of_looking_done(
            self, tmp_path):
        # The one outcome of a supersede that reads as success and is not: the
        # claim it names is in a card this write never opened, so nothing was
        # taken out and the replaced claim still reads as current. Which card a
        # claim is in is not recorded here — that is what the receipts are read
        # for — so the honest report is "no line this write carried it".
        root = records_tree(tmp_path)
        _, second, _ = self.two_commits(tmp_path, root, supersedes=("c9",))
        assert second["superseded"] == ["c9"]
        assert second["superseded_unmet"] == ["c9"]
        assert "她说周五交付" in (root / CARD).read_text(encoding="utf-8")
        rendered = "\n".join(us.render_receipt(second))
        assert "c9" in rendered and "没有对应行" in rendered

    def test_a_claim_that_supersedes_its_own_commit_is_refused(self, tmp_path):
        # Refused rather than resolved: "this claim replaces that one" and "that
        # one is being committed here" cannot both be meant, and choosing which
        # is not a decision this module can make on the author's behalf.
        root = records_tree(tmp_path)
        before = (root / CARD).read_bytes()
        document = reading(root, [claim("c2", supersedes=("c2",))])
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document)
        assert "both committed here and named as replaced" in str(caught.value)
        assert (root / CARD).read_bytes() == before

    def test_a_supersede_that_is_not_a_claim_id_is_refused_where_it_is_read(
            self, tmp_path):
        # At the read, not at the write, and the difference is visible: left to
        # the write this would remove nothing and read — from the receipt alone
        # — exactly like a supersede whose target was never committed.
        root = records_tree(tmp_path)
        document = reading(root, [claim("c2", supersedes=("not an id",))])
        with pytest.raises(us.Malformed) as caught:
            at(tmp_path, document)
        assert "not a claim id" in str(caught.value)
        assert "remove nothing" in str(caught.value)

    @pytest.mark.parametrize("line", [
        # Two shapes of unreadable, and the second is the one that matters:
        # backticked, but not a claim id. `_line_id` asks for the shape as well
        # as the position, because "whatever sits in backticks first" is a
        # reading that finds an id in a line somebody wrote by hand.
        pytest.param("- 一句没有 id 的行", id="no-backticks"),
        pytest.param("- `_c1` **x**", id="backticked-but-not-a-claim-id"),
    ])
    def test_a_region_line_the_module_cannot_read_blocks_the_write(
            self, tmp_path, line):
        # A line inside the module's own block whose id cannot be read: written
        # by no version of this module, so somebody merged or edited a card by
        # hand. Both available answers lose something — keeping it leaves a line
        # nothing can match on again, dropping it takes a claim out of the card
        # — so the write does not happen and the refusal names the card to open.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        region_edited(root, line)
        before = (root / CARD).read_bytes()
        document = reading(root, [claim("c2", now="她说周一交付", positions=(32,),
                                        excerpt="第二次")],
                           from_position=30, to_position=40)
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                             from_position=30, to_position=40)
        assert "carries no claim id this module can read" in str(caught.value)
        assert str(root / CARD) in str(caught.value)
        assert (root / CARD).read_bytes() == before

    def test_two_lines_for_one_id_in_the_block_are_refused(self, tmp_path):
        # The same judgement from the other side, and the reason the merge keys
        # on the id rather than on the line: two lines for one claim leave a
        # reader no way to say which of them is the claim.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        line = "- `c1` **[OBSERVED_FACT]** alice：她说周五交付（旧的；来源 pos 12）"
        region_edited(root, line, "- `c1` **[OBSERVED_FACT]** alice：她说周六交付"
                                "（新的；来源 pos 12）")
        before = (root / CARD).read_bytes()
        document = reading(root, [claim("c2", now="她说周一交付", positions=(32,),
                                        excerpt="第二次")],
                           from_position=30, to_position=40)
        with pytest.raises(us.Refused) as caught:
            commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                             from_position=30, to_position=40)
        assert "two lines for claim c1" in str(caught.value)
        assert (root / CARD).read_bytes() == before

    def test_a_resume_removes_what_the_first_write_could_not_reach(self, tmp_path):
        # A supersede is the commit's INTENT and is copied into the receipt
        # rather than recomputed, and this is the case that tells the two apart:
        # the view was missing when the commit ran and came back afterwards
        # already routing to the replaced claim. A resume that recomputed its
        # removals would have nothing to recompute them FROM — it cannot re-run
        # the reading, because the first write moved the card the reading was
        # reasoned against — and would leave the view routing to a claim the
        # same commit took out of the card.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        (root / VIEW).unlink()
        document = reading(root, [claim("c2", now="她说周一交付", positions=(32,),
                                        supersedes=("c1",), excerpt="第二次")],
                           from_position=30, to_position=40)
        first = applied(tmp_path, root, document, delegation=GRANT,
                        from_position=30, to_position=40)
        assert first["state"] == "PARTIAL"
        # The card took the removal; the view was never opened, so whether the
        # id is in it cannot be said in either direction — which is why the
        # stale reference is not reported as `superseded_unmet` (that field
        # answers "did a file this write touched carry the id", and the answer
        # is yes) but as a pending view, named with the reason.
        assert first["superseded"] == ["c1"]
        assert first["superseded_unmet"] == []
        assert [entry["view"] for entry in first["pending_views"]] == [VIEW]
        (root / VIEW).write_text(
            VIEW_PROSE + "\n" + fenced(f"- `c1` → `{CARD}`（alice）"),
            encoding="utf-8")
        second = applied(tmp_path, root, document, delegation=GRANT,
                         from_position=30, to_position=40)
        assert second["state"] == "COMPLETE"
        assert second["superseded_unmet"] == []
        view = (root / VIEW).read_text(encoding="utf-8")
        assert "`c1`" not in view and "`c2`" in view
        assert view.startswith(VIEW_PROSE)

    def test_the_resolution_says_a_line_will_be_taken_out(self, tmp_path):
        # A person shown only "a new claim is being written" would find a line
        # gone from a card later and have nothing to connect it to.
        root = records_tree(tmp_path)
        document = reading(root, [claim("c2", supersedes=("c1",))])
        rendered = "\n".join(us.render_resolution(
            resolved(tmp_path, root, document, delegation=GRANT)))
        assert "取代 c1" in rendered
        assert "会从它所在的卡片移出" in rendered

    def test_the_rendered_receipt_says_what_left_the_card(self, tmp_path):
        root = records_tree(tmp_path)
        _, second, _ = self.two_commits(tmp_path, root, supersedes=("c1",))
        rendered = "\n".join(us.render_receipt(second))
        # Both cards end up routing only to the claim that replaced c1: the
        # replaced claim's line leaves its owning card, and the reference to it
        # leaves the view. A receipt that showed a count without the removal
        # would leave a reader unable to say whether the claim was ever taken
        # out or had simply not been written yet.
        assert rendered.count("1 条：c2  移出 c1") == 2

    def test_a_v2_receipt_is_refused_by_name(self, tmp_path):
        # v2 named the ids a commit WROTE; this version names what the file
        # CARRIES. A card with two commits is where the two answers differ, so a
        # v2 receipt read as a v3 one would report a claim that is in the card
        # as absent from it.
        path = tmp_path / "receipt-v2.json"
        path.write_text(json.dumps({
            "schema": "serveros/understanding-receipt.v2",
            "key": [CARD, MANIFEST, 10, 20, "a" * 64],
            "written": [{"card": CARD, "before": "b" * 64, "after": "c" * 64,
                         "claims": ["c1"]}],
        }), encoding="utf-8")
        with pytest.raises(us.Refused) as caught:
            us._read_receipt(path)
        assert "serveros/understanding-receipt.v2" in str(caught.value)
        assert "duplicate" in str(caught.value)


# --------------------------------------------------------------------------
# the producer tables
# --------------------------------------------------------------------------

class TestTheProducerTables:

    def test_a_field_with_no_named_source_is_refused(self):
        with pytest.raises(us.Refused) as caught:
            us.no_unsourced({"schema": "x", "mood": "confident"},
                            {"schema": "the schema"}, "commit")
        assert "commit.mood" in str(caught.value)
        assert "add the producer or drop the field" in str(caught.value)

    def test_a_resolution_emits_exactly_the_keys_its_table_names(self, tmp_path):
        root = records_tree(tmp_path)
        assert set(resolved(tmp_path, root, reading(root))) \
            == set(us.RESOLUTION_PRODUCED)

    def test_a_commit_emits_exactly_the_keys_its_table_names(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root)
        plan = us.commit(resolved(tmp_path, root, document, delegation=GRANT),
                         at(tmp_path, document))
        assert set(plan) == set(us.COMMIT_PRODUCED)

    def test_a_receipt_emits_exactly_the_keys_its_table_names(self, tmp_path):
        root = records_tree(tmp_path)
        receipt = commit_and_write(tmp_path, root, delegation=GRANT)
        assert set(receipt) == set(us.RECEIPT_PRODUCED)

    def test_every_confirmation_the_module_can_emit_is_one_of_the_named_ones(
            self, tmp_path):
        root = records_tree(tmp_path)
        without = resolved(tmp_path, root, reading(root))
        with_grant = resolved(tmp_path, root, reading(root), delegation=GRANT)
        emitted = {claim["confirmation"] for claim in
                   without["awaiting"] + with_grant["committed"]}
        assert emitted == {us.AWAITING_DECISION, us.AGENT_RECONCILED}
        assert us.USER_CONFIRMED in us.CONFIRMATIONS
        assert us.USER_CONFIRMED not in emitted


# --------------------------------------------------------------------------
# the two layers have to agree about where a card lives
# --------------------------------------------------------------------------

class TestTheTwoLayersAgree:

    def test_they_agree_on_where_the_cards_live(self):
        # The repetition of the two segments is what makes this test worth
        # having. A drifted constant here is a writer that writes claims into a
        # directory no reader opens — and every test in this module would still
        # pass, because they all build their own tree.
        ud = _load("understanding_delta", "understanding_delta.py")
        assert us.UNDERSTANDING_SEGMENTS == ud.UNDERSTANDING_SEGMENTS

    def test_a_card_path_in_a_reading_is_the_path_the_reader_reports(
            self, tmp_path):
        root = records_tree(tmp_path)
        ud = _load("understanding_delta", "understanding_delta.py")
        report = ud.read_and_reconcile(root, [])
        assert CARD in [card["card"] for card in report["cards"]]

    def test_a_replay_is_reported_without_measuring_the_source_again(
            self, tmp_path):
        # The range the reader proposed can be gone by the time a replay is
        # asked about — the card has moved on. A write that already happened
        # must still be reportable, which is why `measure` is called only when
        # there is no receipt.
        root = records_tree(tmp_path)
        document = reading(root)
        first = applied(tmp_path, root, document, delegation=GRANT)

        def refuse():
            raise AssertionError("a replay measured the source again")

        second = us.apply(tmp_path / "reading.json", root, measure=refuse,
                          delegation=GRANT, committed_at=STAMP)
        assert second["reused"] is True
        assert second["written"] == first["written"]


# --------------------------------------------------------------------------
# the whole chain, through the verb
# --------------------------------------------------------------------------

CHAIN_CARD = "information/understanding/people/x.md"
CHAIN_VIEW = "information/understanding/projects/widget.md"
CHAIN_MANIFEST = "feishu/chats/people/x/oc_x/manifest.json"
CHAIN_SOURCE = "information/raw/feishu/chats/people/x/oc_x/messages.jsonl"
CHAIN_CARD_PROSE = ("# X\n\n手写。\n\n## Sources\n\n"
                    f"- `{CHAIN_SOURCE}` message_position 0-4\n")
CHAIN_VIEW_PROSE = "# widget\n\n项目视角。\n"


def chain(tmp_path: Path) -> Path:
    """A records checkout with a real capture, a card behind it, and a view.

    Built here rather than shared with the patch tests on purpose: this file has
    to fail if the reader's shape moves, and a fixture shared between two layers
    would let both drift together and stay green.
    """
    records = tmp_path / "records"
    for relative in ("information/understanding/people",
                     "information/understanding/projects",
                     "information/raw/feishu/chats"):
        (records / relative).mkdir(parents=True)
    capture = records / "information" / "raw" / "feishu" / "chats" / "people" / "x" / "oc_x"
    capture.mkdir(parents=True)
    (capture / "manifest.json").write_text(json.dumps({
        "contact": {"name": "X"},
        "chat": {"chat_id": "oc_x", "chat_type": "p2p", "name": "X"},
        "sync": {"mode": "incremental", "method": "im/chat-messages-list",
                 "msg_count": 10, "remote_unique_message_count": 10,
                 "fetched_page_count": 1, "pagination_complete": True,
                 "earliest_create_time": "2026-09-11 09:45",
                 "latest_create_time": "2026-09-11 10:07",
                 "last_message_id": "om_9"},
    }, ensure_ascii=False), encoding="utf-8")
    (capture / "messages.jsonl").write_text(
        "\n".join(json.dumps({"message_position": str(position),
                              "message_id": f"om_{position}",
                              "content": f"第{position}条", "updated": False,
                              "deleted": False}) for position in range(10)) + "\n",
        encoding="utf-8")
    (records / CHAIN_CARD).write_text(CHAIN_CARD_PROSE, encoding="utf-8")
    (records / CHAIN_VIEW).write_text(CHAIN_VIEW_PROSE, encoding="utf-8")
    return records


def chain_reading(records: Path, *, claims=None) -> dict:
    """A reading made against the reader's own report, not against guessed numbers.

    Every field the reader owns is taken from it: the card path, the raw-root
    relative manifest, the range, and both digests. A reading that reconstructed
    any of them would be the case `_reading_current` refuses.
    """
    ud = _load("understanding_delta", "understanding_delta.py")
    cc = _load("capture_coverage", "capture_coverage.py")
    report = ud.read_and_reconcile(records, cc.read_root(records)["objects"])
    card = next(row for row in report["cards"] if row["card"] == CHAIN_CARD)
    row = next(iter(card["judged"]["review_ranges"]))
    return {
        "schema": us.READING_SCHEMA,
        "source": {"card": row["card"], "manifest": row["manifest"],
                   "from_position": row["from_position"],
                   "to_position": row["to_position"]},
        "read": {"card": row["card"], "content_identity": row["content_identity"],
                 "card_digest": card["card_digest"]},
        "claims": claims if claims is not None else [{
            "id": "c1", "kind": us.OBSERVED_FACT, "subject": "x",
            "assertion": {"was": None, "now": "她说周五交付"},
            "support": {"positions": [row["to_position"]], "excerpt": "周五给你"},
            "applies_to": None, "touches": [], "supersedes": [], "conflicts": [],
            "card": CHAIN_CARD, "views": [CHAIN_VIEW],
        }],
    }


def run(*args, cwd=None):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "runtime" / "cli.py"), *map(str, args)],
        capture_output=True, text=True, cwd=str(cwd or REPO_ROOT))


@pytest.fixture
def chain_tree(tmp_path):
    """A records checkout, a reading against it, and a delegation covering it."""
    records = chain(tmp_path)
    reading_path = tmp_path / "reading.json"
    reading_path.write_text(json.dumps(chain_reading(records), ensure_ascii=False),
                            encoding="utf-8")
    grant = tmp_path / "delegation.json"
    grant.write_text(json.dumps({"covers": ["x"], "until": "2026-10-01",
                                 "granted_by": "user", "source": "session-1"}),
                     encoding="utf-8")
    return records, reading_path, grant


class TestTheVerbEndToEnd:

    def test_without_commit_nothing_is_written_and_the_resolution_is_shown(
            self, chain_tree):
        records, reading_path, grant = chain_tree
        before = (records / CHAIN_CARD).read_bytes()
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant, "--json")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert [claim["id"] for claim in payload["committed"]] == ["c1"]
        assert (records / CHAIN_CARD).read_bytes() == before
        assert not (records / "information" / "understanding" / ".serveros").exists()

    def test_the_verb_shows_why_each_awaiting_claim_is_awaiting(self, chain_tree):
        records, reading_path, _ = chain_tree
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path)
        assert result.returncode == 0, result.stderr
        assert "无 —— 没有常设委托时机器不自行提交任何一条" in result.stdout
        assert "no standing delegation covers this work" in result.stdout

    def test_with_commit_the_claim_is_in_its_card_and_the_view_is_readable(
            self, chain_tree):
        records, reading_path, grant = chain_tree
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant,
                     "--commit", "--committed-at", STAMP, "--json")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "COMPLETE"
        body = (records / CHAIN_CARD).read_text(encoding="utf-8")
        head, _, tail = us.fence_parts(body)
        assert head.startswith(CHAIN_CARD_PROSE)
        assert "她说周五交付" in body
        view = (records / CHAIN_VIEW).read_text(encoding="utf-8")
        assert f"`{CHAIN_CARD}`" in view
        assert "她说周五交付" not in view

    def test_a_replay_through_the_verb_writes_once_and_says_so(self, chain_tree):
        records, reading_path, grant = chain_tree
        args = ("work", "understanding-semantic", "--records-root", records,
                "--reading", reading_path, "--delegation", grant,
                "--commit", "--committed-at", STAMP, "--json")
        first = run(*args)
        after_first = (records / CHAIN_CARD).read_bytes()
        second = run(*args)
        assert second.returncode == 0, second.stderr
        assert json.loads(second.stdout)["reused"] is True
        assert json.loads(second.stdout)["written"] == json.loads(first.stdout)["written"]
        assert (records / CHAIN_CARD).read_bytes() == after_first

    def test_a_reading_whose_range_is_no_longer_proposed_is_refused_by_name(
            self, chain_tree):
        # The capture waterline moved past the range the reading was made about,
        # so the reader proposes a different one. The old reading is not applied
        # against the new proposal: it is refused, and the refusal names both.
        records, reading_path, grant = chain_tree
        capture = records / "information" / "raw" / "feishu" / "chats" / "people" / "x" / "oc_x"
        rows = [{"message_position": str(position), "message_id": f"om_{position}",
                 "content": f"第{position}条", "updated": False, "deleted": False}
                for position in range(14)]
        (capture / "messages.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        manifest = json.loads((capture / "manifest.json").read_text(encoding="utf-8"))
        manifest["sync"]["msg_count"] = 14
        manifest["sync"]["remote_unique_message_count"] = 14
        (capture / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant,
                     "--commit", "--committed-at", STAMP)
        assert result.returncode != 0
        assert "no longer proposes" in result.stderr
        assert "Read the current proposal" in result.stderr
        assert us.FENCE_BEGIN not in (records / CHAIN_CARD).read_text(encoding="utf-8")

    def test_a_card_rewritten_in_place_after_the_reading_is_refused(
            self, chain_tree):
        # Same path, same citation, same range: the only thing that moved is the
        # card's own text. If the verb derived the card's digest from the reading
        # instead of from the reader's report, this write would go through and
        # record an interpretation of prose nobody wrote.
        records, reading_path, grant = chain_tree
        (records / CHAIN_CARD).write_text(
            CHAIN_CARD_PROSE + "\n她后来改了主意。\n", encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant,
                     "--commit", "--committed-at", STAMP)
        assert result.returncode != 0
        assert "the understanding card was rewritten" in result.stderr
        assert us.FENCE_BEGIN not in (records / CHAIN_CARD).read_text(encoding="utf-8")

    def test_the_same_range_holding_different_content_is_refused(
            self, chain_tree):
        # §2's own counterexample, through the whole chain: the range did not
        # move and the card did not move, so nothing about the proposal's PLACE
        # looks different. The reading is refused because the content it
        # interpreted is not the content that is there.
        records, reading_path, grant = chain_tree
        capture = (records / "information" / "raw" / "feishu" / "chats"
                   / "people" / "x" / "oc_x" / "messages.jsonl")
        rows = [json.loads(line) for line in
                capture.read_text(encoding="utf-8").splitlines() if line]
        for row in rows:
            if row["message_position"] == "7":
                row["content"] = "她改到周一了"
        capture.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                     for row in rows) + "\n", encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant,
                     "--commit", "--committed-at", STAMP)
        assert result.returncode != 0
        assert "the content this reading is about changed" in result.stderr
        assert "the understanding card was rewritten" not in result.stderr
        assert us.FENCE_BEGIN not in (records / CHAIN_CARD).read_text(encoding="utf-8")

    def test_the_verb_takes_a_persons_acceptance_and_commits_what_it_names(
            self, chain_tree, tmp_path):
        records, reading_path, _ = chain_tree
        # The proposal first and with no write: this is the run a person is
        # shown, and its `reading_digest` is the value they copy onto their
        # answer. No delegation, so every claim is one a person decides.
        proposal = json.loads(run(
            "work", "understanding-semantic", "--records-root", records,
            "--reading", reading_path, "--json").stdout)
        assert [entry["id"] for entry in proposal["committed"]] == []
        accepted = accepted_file(tmp_path, acceptance(proposal, claims=["c1"]))
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--accepted", accepted,
                     "--commit", "--committed-at", STAMP, "--json")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "COMPLETE"
        assert receipt["accepted"]["by"] == "alice"
        card = (records / CHAIN_CARD).read_text(encoding="utf-8")
        assert us.USER_CONFIRMED in card
        assert us.AGENT_RECONCILED not in card

    def test_the_verb_says_when_the_acceptance_is_for_text_that_has_moved(
            self, chain_tree, tmp_path):
        # §2's 必须修 seen from the outside: the path, the range and the content
        # are the same, the card is not, and the run has to SAY that the person's
        # yes was for the text that was there before — not commit it, and not
        # fail silently either.
        records, reading_path, _ = chain_tree
        proposal = json.loads(run(
            "work", "understanding-semantic", "--records-root", records,
            "--reading", reading_path, "--json").stdout)
        accepted = accepted_file(tmp_path, acceptance(proposal, claims=["c1"]))
        (records / CHAIN_CARD).write_text(
            CHAIN_CARD_PROSE + "\n她后来改了主意。\n", encoding="utf-8")
        again = tmp_path / "reading2.json"
        again.write_text(json.dumps(chain_reading(records), ensure_ascii=False),
                         encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", again, "--accepted", accepted)
        assert result.returncode == 0, result.stderr
        assert "没有一条匹配本次读数" in result.stdout
        assert "the understanding card has been rewritten since it was accepted" \
            in result.stdout
        assert us.FENCE_BEGIN not in (records / CHAIN_CARD).read_text(encoding="utf-8")

    def test_with_commit_and_no_acceptance_nothing_is_written_and_no_receipt_left(
            self, chain_tree):
        records, reading_path, _ = chain_tree
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--commit", "--committed-at", STAMP,
                     "--json")
        assert result.returncode == 0, result.stderr
        receipt = json.loads(result.stdout)
        assert receipt["state"] == "NOTHING_TO_WRITE"
        assert receipt["written"] == []
        assert not (records / "information" / "understanding" / ".serveros").exists()
        assert us.FENCE_BEGIN not in (records / CHAIN_CARD).read_text(encoding="utf-8")

    def test_an_accepted_file_that_cannot_be_read_is_not_an_absent_one(
            self, chain_tree, tmp_path):
        records, reading_path, _ = chain_tree
        broken = tmp_path / "broken-accepted.jsonl"
        broken.write_text("{ not json", encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--accepted", broken)
        assert result.returncode != 0
        assert "could not be read is not an absent one" in result.stderr

    def test_a_delegation_file_that_cannot_be_read_is_not_an_absent_one(
            self, chain_tree, tmp_path):
        records, reading_path, _ = chain_tree
        broken = tmp_path / "broken.json"
        broken.write_text("{ not json", encoding="utf-8")
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", broken)
        assert result.returncode != 0
        assert "could not be read is not an absent one" in result.stderr

    def test_commit_without_a_caller_supplied_time_is_refused(self, chain_tree):
        records, reading_path, grant = chain_tree
        result = run("work", "understanding-semantic", "--records-root", records,
                     "--reading", reading_path, "--delegation", grant, "--commit")
        assert result.returncode != 0
        assert "--committed-at" in result.stderr

class TestTheGuardOnTheSpliceIsASecondOpinion:

    @pytest.mark.parametrize("broken", ["drops-everything-inside-the-fence",
                                        "drops-the-prose-before-the-fence",
                                        "normalises-the-bytes-around-the-block",
                                        "writes-nothing-at-all"])
    def test_a_splice_that_loses_a_persons_text_is_refused(
            self, tmp_path, monkeypatch, broken):
        # `_write_fence` checks its own splice before writing, and that check
        # never fires while the splice is correct — so nothing in the suite
        # exercises it, and a check nothing exercises is a comment. This test
        # supplies the splices it exists to catch, one per clause: one that
        # replaces the whole fence body (the defect this slice was opened for),
        # one that gets the fence exactly right and rebuilds the card without
        # the prose that was before it (a writer that assembles a file out of
        # its own parts drops what it does not know it has), one that reflows
        # the space inside the fence around the block (which is where a person
        # disputing a claim writes), and one that writes nothing at all.
        #
        # Each splice is written so that the clauses OTHER than the one it is
        # about would let it through. A splice that loses the head and also
        # writes a wrong region is refused by the region clause instead, and
        # would never tell the head clause apart from absent — and it is the
        # clause whose whole subject is somebody else's prose.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        card = root / CARD
        corrected(root)
        before = card.read_text(encoding="utf-8")
        correct_splice = us._replace_fence

        def drops_inside(text: str, claimed, *, superseded=()) -> str:
            head, _, tail = us.fence_parts(text)
            return (head + us.FENCE_BEGIN
                    + us._fence_body([line for _, line in claimed])
                    + us.FENCE_END + tail)

        def drops_head(text: str, claimed, *, superseded=()) -> str:
            fixed = correct_splice(text, claimed, superseded=superseded)
            _, rest = fixed.split(us.FENCE_BEGIN, 1)
            return us.FENCE_BEGIN + rest

        def normalises_around(text: str, claimed, *, superseded=()) -> str:
            fixed = correct_splice(text, claimed, superseded=superseded)
            head, body, tail = us.fence_parts(fixed)
            _, claims, _ = us.claims_region(body)
            return (head + us.FENCE_BEGIN + "\n" + us.CLAIMS_BEGIN + claims
                    + us.CLAIMS_END + "\n" + us.FENCE_END + tail)

        splices = {
            "drops-everything-inside-the-fence": drops_inside,
            "drops-the-prose-before-the-fence": drops_head,
            "normalises-the-bytes-around-the-block": normalises_around,
            "writes-nothing-at-all": lambda t, c, *, superseded=(): t,
        }
        monkeypatch.setattr(us, "_replace_fence", splices[broken])
        with pytest.raises(us.Refused) as caught:
            us._write_fence(root, us._understanding_root(root), CARD,
                            [("c9", "- `c9` **[OBSERVED_FACT]** alice：她说周四交付")],
                            role=us.OWNER)
        assert "does not own" in str(caught.value)
        assert card.read_text(encoding="utf-8") == before
