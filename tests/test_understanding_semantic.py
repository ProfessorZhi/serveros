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


def reading(root: Path, claims=None, *, card: str = CARD,
            content_identity: str | None = None,
            card_digest: str | None = None,
            from_position: int = 10, to_position: int = 20) -> dict:
    """A reading whose two digests match the tree on disk, unless told otherwise."""
    return {
        "schema": us.READING_SCHEMA,
        "source": {"card": card, "manifest": MANIFEST,
                   "from_position": from_position, "to_position": to_position},
        "read": {
            "card": card,
            "content_identity": content_identity or "a" * 64,
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
             delegation=None, card: str = CARD, **overrides) -> dict:
    return us.resolve(at(tmp_path, document),
                      current(root, card, **overrides), delegation)


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
            committed_at: str = STAMP, **overrides) -> dict:
    """The whole path, through the one entry point that orders it correctly.

    A replay has to be looked up before the reading is re-validated against the
    card, and the entry point is where that ordering lives. These tests go
    through it for anything that applies twice; the tests that reach past it do
    so to exercise the steps themselves.
    """
    path = tmp_path / "reading.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return us.apply(path, root, measure=lambda: current(root, **overrides),
                    delegation=delegation, committed_at=committed_at)


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
        assert entries[CARD]["claims"] == 1


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
        assert receipt["pending_views"][0]["lines"], \
            "a resume must not have to re-derive the lines it still owes"
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
