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
    def test_the_modules_own_lines_are_replaced_and_not_duplicated(
            self, tmp_path, where):
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        corrected(root, where=where)
        document = reading(root, [claim("c2", now="她说下周一交付", positions=(28,),
                                        excerpt="下周一给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        card = (root / CARD).read_text(encoding="utf-8")
        assert "pos 12" not in card
        assert card.count("pos 28") == 1
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

    def test_the_same_lines_written_twice_do_not_grow_the_card(self, tmp_path):
        # Asked of the splice rather than through a second commit: a second
        # reading over one span is refused for an unrelated reason (two readings
        # of one span cannot both be the committed one), so it cannot answer
        # this. What is being pinned here is that the splice is idempotent — a
        # `\n` added on each write would pass every assertion above.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        text = (root / CARD).read_text(encoding="utf-8")
        lines = [f"- **[{us.OBSERVED_FACT}]** alice：她说下周一交付"]
        once = us._replace_fence(text, lines)
        assert once != text and "她说下周一交付" in once
        assert us._replace_fence(once, lines) == once

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

    def test_a_fence_written_before_the_region_existed_migrates_cleanly(
            self, tmp_path):
        # Cards written by the earlier version hold the module's lines with no
        # region marker around them. Replacing that whole body is right, and it
        # is exact rather than a guess: back then the writer replaced the whole
        # body on every commit, so every line in it is one the module put there.
        root = records_tree(tmp_path)
        card = root / CARD
        card.write_text(ALICE_PROSE + "\n" + us.FENCE_BEGIN + "\n- 老的自动摘要行\n"
                        + us.FENCE_END, encoding="utf-8")
        document = reading(root, [claim("c2", now="她说周三交付", positions=(28,),
                                        excerpt="周三给你")], to_position=60)
        commit_and_write(tmp_path, root, delegation=GRANT, document=document,
                         to_position=60)
        migrated = card.read_text(encoding="utf-8")
        line = ("- **[OBSERVED_FACT]** alice：她说周三交付"
                "（agent_reconciled；来源 pos 28；excerpt: 周三给你）")
        assert migrated == ALICE_PROSE + "\n" + fenced(line)
        assert "老的自动摘要行" not in migrated

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
        # as no region, and the migration that follows replaces the whole body
        # — taking a person's lines with it. Neither half is a shape this module
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

    def test_two_claims_in_one_card_are_both_named_in_id_order(self, tmp_path):
        root = records_tree(tmp_path)
        document = reading(root, [claim("z9"), claim("a1", now="她说周一交付")])
        receipt = applied(tmp_path, root, document, delegation=GRANT)
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["claims"] == ["a1", "z9"]

    def test_an_id_that_appears_nowhere_in_the_line_is_still_recorded(
            self, tmp_path):
        # The mutation this keeps: recovering the ids by parsing the rendered
        # line. The line carries the kind, the subject, the excerpt and the
        # confirmation and NOT the id, so a parser finds nothing — and an id
        # that happens to appear as prose would be found and be wrong.
        root = records_tree(tmp_path)
        document = reading(root, [claim("zz-9", now="她说周五交付")])
        receipt = applied(tmp_path, root, document, delegation=GRANT)
        body = (root / CARD).read_text(encoding="utf-8")
        assert "zz-9" not in body
        entries = {entry["card"]: entry for entry in receipt["written"]}
        assert entries[CARD]["claims"] == ["zz-9"]

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
                                        "writes-nothing-at-all"],
                             ids=["drops-everything-inside-the-fence",
                                  "writes-nothing-at-all"])
    def test_a_splice_that_loses_a_persons_text_is_refused(
            self, tmp_path, monkeypatch, broken):
        # `_write_fence` checks its own splice before writing, and that check
        # never fires while the splice is correct — so nothing in the suite
        # exercises it, and a check nothing exercises is a comment. This test
        # supplies the splice it exists to catch: one that replaces the whole
        # fence body, which is the defect this slice was opened for, and one
        # that writes nothing, which leaves the card with no region at all.
        root = records_tree(tmp_path)
        commit_and_write(tmp_path, root, delegation=GRANT)
        card = root / CARD
        corrected(root)
        before = card.read_text(encoding="utf-8")

        def drops(text: str, lines: list[str]) -> str:
            head, _, tail = us.fence_parts(text)
            return head + us.FENCE_BEGIN + us._fence_body(lines) + us.FENCE_END + tail

        monkeypatch.setattr(us, "_replace_fence",
                            drops if broken.startswith("drops") else lambda t, l: t)
        with pytest.raises(us.Refused) as caught:
            us._write_fence(root, us._understanding_root(root), CARD,
                            ["- **[OBSERVED_FACT]** alice：她说周四交付"])
        assert "does not own" in str(caught.value)
        assert card.read_text(encoding="utf-8") == before
