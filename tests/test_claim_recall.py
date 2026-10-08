"""A2 — what may still be recalled, tested against receipts the writer really made.

Nothing here hand-writes a receipt unless the test is ABOUT a damaged record.
Every claim the ordinary cases ask about was committed through
`understanding_semantic.apply`, into a records tree this file built, against a
capture whose messages file it wrote — because the question A2 answers is a
question about a real commit and a real source, and a fixture that fabricated
the record would be testing the fixture's idea of what the writer does. The two
halves that can lie to each other are exactly the two halves this file joins:
the receipt the writer left and the source the reader reads.

The cases are the ones the contract names, each as the failure it prevents: a
source edited under an unchanged span; a claim replaced by a later reading; a
line removed from its card by hand; a source that went away; one id written from
two spans; a claim the record names and no card owns; and the provider, which is
never consulted and whose absence is reported rather than allowed to change an
answer.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"


def _load(name: str, relative: str):
    registered = "serveros_intake_" + name
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(
        registered, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module


us = _load("understanding_semantic", "understanding_semantic.py")
ud = _load("understanding_delta", "understanding_delta.py")
cr = _load("claim_recall", "claim_recall.py")


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


# --------------------------------------------------------------------------
# the tree
# --------------------------------------------------------------------------

CARD = "information/understanding/people/alice.md"
VIEW = "information/understanding/projects/widget.md"
OTHER_CARD = "information/understanding/people/bruce.md"
#: Raw-root-relative, which is the capture index's own coordinate system and the
#: one `understanding_delta.messages_file` joins onto `information/raw`. A card
#: path is records-root-relative instead: two roots in one reading, which is why
#: this file names both and joins neither by hand.
MANIFEST = "feishu/chats/people/alice/oc_alice/manifest.json"
GRANT = {"covers": ["alice", "bruce"], "until": None, "granted_by": "user",
         "source": "session-1"}
STAMP = "2026-09-23T10:00:00+08:00"
SECOND = "2026-09-23T11:00:00+08:00"
THIRD = "2026-09-23T12:00:00+08:00"


class Tree:
    """A records checkout with one capture, three cards, and the writer to fill it."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root = tmp_path / "records"
        (self.root / "information/understanding/people").mkdir(parents=True)
        (self.root / "information/understanding/projects").mkdir(parents=True)
        self.capture = (self.root / "information/raw/feishu/chats/people/alice/oc_alice")
        self.capture.mkdir(parents=True)
        (self.root / CARD).write_text("# Alice\n\n手写散文。\n", encoding="utf-8")
        (self.root / OTHER_CARD).write_text("# Bruce\n\n另一个人的手写散文。\n",
                                            encoding="utf-8")
        (self.root / VIEW).write_text("# widget\n\n项目视角。\n", encoding="utf-8")
        (self.capture / "manifest.json").write_text(
            json.dumps({"source": "feishu", "coverage": {"components": {}}}),
            encoding="utf-8")
        self.write_messages(range(1, 61))

    def write_messages(self, positions, *, edit=None) -> None:
        """The capture's messages file. `edit` is `{position: new content}`."""
        edit = edit or {}
        (self.capture / "messages.jsonl").write_text(
            "".join(json.dumps({"message_position": p,
                                "content": edit.get(p, f"m{p}")},
                               ensure_ascii=False) + "\n" for p in positions),
            encoding="utf-8")

    def identity(self, low: int, high: int) -> str | None:
        return ud.range_content_digest(
            ud.messages_file(MANIFEST, self.root), low, high)[0]

    def reading(self, claims, *, low: int = 10, high: int = 20,
                card: str = CARD) -> dict:
        return {"schema": us.READING_SCHEMA,
                "source": {"card": card, "manifest": MANIFEST,
                           "from_position": low, "to_position": high},
                "read": {"card": card, "content_identity": self.identity(low, high),
                         "card_digest": us.card_digest(
                             (self.root / card).read_text(encoding="utf-8"))},
                "claims": claims}

    def claim(self, claim_id: str, now: str, positions, **over) -> dict:
        return {"id": claim_id, "kind": over.pop("kind", us.OBSERVED_FACT),
                "subject": "alice",
                "assertion": {"was": None, "now": now},
                "support": {"positions": list(positions), "excerpt": "摘录"},
                "applies_to": None, "touches": [],
                "supersedes": over.pop("supersedes", []), "conflicts": [],
                "card": over.pop("card", CARD),
                "views": over.pop("views", [VIEW])}

    def commit(self, name: str, document: dict,
               committed_at: str = STAMP) -> dict:
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        low = document["source"]["from_position"]
        high = document["source"]["to_position"]
        card = document["read"]["card"]

        def measure():
            return {"card_digest": us.card_digest(
                        (self.root / card).read_text(encoding="utf-8")),
                    "content_identity": self.identity(low, high),
                    "from_position": low, "to_position": high}
        return us.apply(path, self.root, measure=measure, delegation=GRANT,
                        committed_at=committed_at)

    def receipts(self) -> list[Path]:
        return sorted((self.root / "information/understanding/.serveros")
                      .glob("receipt-*.json"))

    def edit_receipt(self, number: int = 0, change=None) -> dict:
        """A receipt damaged by hand — for the states the writer cannot produce."""
        path = self.receipts()[number]
        document = json.loads(path.read_text(encoding="utf-8"))
        change(document)
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2,
                                   sort_keys=True), encoding="utf-8")
        return document

    def recall(self, **kwargs) -> dict:
        return cr.recall(self.root, **kwargs)

    def one(self, claim_id: str = "c1") -> dict:
        return {row["id"]: row for row in self.recall()["claims"]}[claim_id]

    def verdicts(self) -> dict[str, str]:
        return {row["id"]: row["verdict"] for row in self.recall()["claims"]}


@pytest.fixture
def tree(tmp_path) -> Tree:
    return Tree(tmp_path)


# --------------------------------------------------------------------------
# the ordinary case, through the real writer
# --------------------------------------------------------------------------

class TestAClaimNobodyHasTouchedIsRecallable:

    def test_a_committed_claim_comes_back_current_and_recallable(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        row = tree.one("c1")
        assert row["verdict"] == cr.CURRENT
        assert row["recallable"] is True
        assert row["card"] == CARD
        assert row["span"] == {"manifest": MANIFEST, "from_position": 10,
                               "to_position": 20}

    def test_the_revision_it_reports_is_the_receipts_and_the_sources(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        revision = tree.one("c1")["source_revision"]
        assert revision["recorded"] == tree.identity(10, 20)
        assert revision["current"] == revision["recorded"]
        assert revision["why"] == ""

    def test_the_rule_that_decides_recallability_is_the_one_in_daily_cycle(self, tree):
        # Not a restatement of the verdict: `invalidate_claims` has been in this
        # tree since before this module, tested, and with no caller. Its answer
        # is what a consumer reads, and the module refuses to emit at all if its
        # own label disagrees — so the two agreeing here is the join.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        document = tree.recall()
        assert document["rule"]["recallable_decided_by"] == \
            "daily_cycle.invalidate_claims"
        assert document["claims"][0]["recallable"] is True

    def test_a_claim_that_never_existed_is_refused_not_reported(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        with pytest.raises(cr.Refused) as caught:
            tree.recall(only=["c9"])
        assert "no receipt names" in str(caught.value)

    def test_asking_about_one_claim_does_not_change_its_answer(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说过周五", [11]),
                                         tree.claim("c2", "她也说过周一", [12])]))
        every = {row["id"]: row for row in tree.recall()["claims"]}
        one = tree.recall(only=["c2"])["claims"][0]
        assert one == every["c2"]

    def test_the_view_is_a_route_to_the_claim_and_not_the_claim(self, tree):
        # The record's `role` is what makes this answerable. Without it the same
        # claim reads as living in the person's card and the project view at
        # once, which is a CONFLICT about a claim that has none.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        assert tree.one("c1")["verdict"] == cr.CURRENT
        assert tree.one("c1")["card"] == CARD

    def test_a_later_write_of_another_span_does_not_move_this_claims_revision(self, tree):
        # `written[i].claims` is what the card carries, and the second commit's
        # card carries the first commit's claim too. Read as "what this write
        # wrote", the second commit's span would become the span this claim is
        # compared against — and the neighbouring range would decide whether an
        # unrelated claim is still current.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c2", "她后来补充了一句", [35])],
                                        low=30, high=40), committed_at=SECOND)
        row = tree.one("c1")
        assert row["span"]["from_position"] == 10
        assert row["source_revision"]["recorded"] == tree.identity(10, 20)
        tree.write_messages(range(1, 61), edit={35: "m35 改过了"})
        assert tree.verdicts() == {"c1": cr.CURRENT, "c2": cr.STALE}

    def test_a_claim_a_card_carries_forward_is_still_owned_by_that_card(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c2", "她后来补充了一句", [35])],
                                        low=30, high=40), committed_at=SECOND)
        assert tree.one("c1")["card"] == CARD
        assert tree.one("c1")["still_in_card"] is True


# --------------------------------------------------------------------------
# the source moved
# --------------------------------------------------------------------------

class TestASourceThatMovedUnderAnUnchangedSpan:

    def test_an_edit_inside_the_span_makes_the_claim_stale(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        assert tree.one("c1")["verdict"] == cr.CURRENT
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        row = tree.one("c1")
        assert row["verdict"] == cr.STALE
        assert row["recallable"] is False
        assert row["source_revision"]["current"] != row["source_revision"]["recorded"]

    def test_an_edit_outside_the_span_leaves_it_current(self, tree):
        # The unit is the span, not the file. Keyed by the file, this edit would
        # mark a claim stale that was read from text nobody touched — and the
        # span key is what keeps the two apart.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={45: "m45 改过了"})
        assert tree.one("c1")["verdict"] == cr.CURRENT

    def test_a_deletion_inside_the_span_makes_the_claim_stale(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages([p for p in range(1, 61) if p != 11])
        assert tree.one("c1")["verdict"] == cr.STALE

    def test_the_span_it_compares_is_the_one_the_receipt_is_filed_under(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [35])],
                                        low=30, high=40))
        row = tree.one("c1")
        assert row["span"]["from_position"] == 30
        assert row["span"]["to_position"] == 40
        assert row["source_revision"]["recorded"] == tree.identity(30, 40)

    def test_two_spans_from_one_source_are_not_one_revision(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])],
                                        low=10, high=20))
        tree.commit("two", tree.reading([tree.claim("c2", "她后来改了口", [35])],
                                        low=30, high=40,
                                        card=OTHER_CARD), committed_at=SECOND)
        tree.write_messages(range(1, 61), edit={35: "m35 改过了"})
        # The second span moved and the first did not. Keyed by the capture
        # object instead of the span, one digest would have decided both.
        assert tree.verdicts() == {"c1": cr.CURRENT, "c2": cr.STALE}

    def test_a_reading_that_never_identified_its_content_is_unresolved(self, tree):
        # A capture with no messages file digests to nothing, and a reading made
        # against it commits with a null `content_identity`. There is then no
        # recorded revision to compare the source against, and `STALE` would be
        # a statement about a comparison that never happened.
        (tree.capture / "messages.jsonl").unlink()
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        row = tree.one("c1")
        assert row["source_revision"]["recorded"] is None
        assert row["verdict"] == cr.UNRESOLVED
        assert row["recallable"] is False
        assert "never identified" in row["why"]

    def test_a_null_identity_stays_unresolved_when_the_source_comes_back(self, tree):
        # The capture returns, and a digest can be taken over the span again.
        # There is still nothing recorded to compare it WITH, so the answer may
        # not become `STALE`: "the text no longer digests to what this claim was
        # read from" is false when nothing was recorded.
        (tree.capture / "messages.jsonl").unlink()
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        row = tree.one("c1")
        assert row["source_revision"]["current"] == tree.identity(10, 20)
        assert row["verdict"] == cr.UNRESOLVED
        assert "never identified" in row["why"]


# --------------------------------------------------------------------------
# the source went away
# --------------------------------------------------------------------------

class TestASourceThatCannotBeIdentified:

    def test_a_missing_messages_file_is_unresolved_and_not_stale(self, tree):
        # Not stale: nothing was compared, and calling it stale would be a
        # statement about content nobody read.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        (tree.capture / "messages.jsonl").unlink()
        row = tree.one("c1")
        assert row["verdict"] == cr.UNRESOLVED
        assert row["recallable"] is False
        assert "cannot be identified" in row["why"]
        assert row["source_revision"]["current"] is None
        assert row["source_revision"]["why"]

    def test_a_span_holding_nothing_is_unresolved(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 6))
        assert tree.one("c1")["verdict"] == cr.UNRESOLVED

    def test_a_source_that_went_away_does_not_make_the_others_unresolved(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c2", "另一段里的话", [35])],
                                        low=30, high=40,
                                        card=OTHER_CARD), committed_at=SECOND)
        tree.write_messages(range(1, 26))
        assert tree.verdicts() == {"c1": cr.CURRENT, "c2": cr.UNRESOLVED}


# --------------------------------------------------------------------------
# the record says the claim was replaced
# --------------------------------------------------------------------------

class TestAClaimALaterReadingReplaced:

    def test_a_superseded_claim_is_not_recallable_and_leaves_the_card(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], supersedes=["c1"])],
            low=30, high=40), committed_at=SECOND)
        rows = {row["id"]: row for row in tree.recall()["claims"]}
        assert rows["c1"]["verdict"] == cr.SUPERSEDED
        assert rows["c1"]["recallable"] is False
        assert rows["c1"]["withdrawal"][0]["removal_landed"] is True
        assert "STILL" not in rows["c1"]["why"]
        assert rows["c2"]["verdict"] == cr.CURRENT

    def test_a_supersede_that_reached_no_line_says_so_and_still_withdraws(self, tree):
        # A claim whose owning card this commit never opened: the supersede
        # cannot take the line out, so the replaced claim stays in its card
        # reading as current. Nothing may recall it, and the card wants looking
        # at — which is a different sentence from the one above, and the reason
        # `removal_landed` is carried at all.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], card=OTHER_CARD,
                        views=[], supersedes=["c1"])], low=30, high=40),
            committed_at=SECOND)
        row = tree.one("c1")
        assert row["verdict"] == cr.SUPERSEDED
        assert row["recallable"] is False
        assert row["withdrawal"][0]["removal_landed"] is False
        assert "STILL" in row["why"]
        assert row["still_in_card"] is True

    def test_one_receipt_is_not_both_the_remover_and_the_one_that_failed(self, tree):
        # `superseded` is the intent and `superseded_unmet` is what did not
        # land, so a reader taking both fields at face value would list one
        # receipt twice and contradict itself about whether the line left.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], card=OTHER_CARD,
                        views=[], supersedes=["c1"])], low=30, high=40),
            committed_at=SECOND)
        assert len(tree.one("c1")["withdrawal"]) == 1

    def test_a_withdrawal_names_the_record_that_replaced_the_claim(self, tree):
        # The line is gone from the card and the record of the claim is not:
        # `withdrawal` names the receipt, so a reader can go and look at what
        # replaced it. A filter that reported only what it dropped would leave
        # nothing to check the drop against.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], supersedes=["c1"])],
            low=30, high=40), committed_at=SECOND)
        withdrawal = tree.one("c1")["withdrawal"]
        assert len(withdrawal) == 1
        assert withdrawal[0]["committed_at"] == SECOND
        assert withdrawal[0]["receipt"].startswith("receipt-")


# --------------------------------------------------------------------------
# a reading withdrew a claim
# --------------------------------------------------------------------------

def retracted(tree: Tree, name: str = "two", *, ids=("c1",), low: int = 30,
              high: int = 40, at: str = SECOND) -> dict:
    """One withdrawal, through the real writer. The shape every case here uses."""
    return tree.commit(name, tree.reading(
        [tree.claim("r1", "撤回", [low + 5], kind=us.RETRACTION, supersedes=list(ids))],
        low=low, high=high), committed_at=at)


class TestAClaimAReadingWithdrew:
    """The producer A2 used to say it did not have.

    `understanding_semantic.eligible` has stated since it was written that a
    withdrawal takes effect locally and at once, and this filter named the
    missing half — no durable producer — in `unobserved` rather than answering
    `CURRENT`. These are the cases that close it, and every one of them goes
    through `apply`, so the receipt under test is one the writer really wrote.
    """

    def test_a_withdrawal_leaves_a_receipt_with_nothing_written_into_it(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        receipt = retracted(tree)
        assert receipt["state"] == "RETRACTION_ONLY"
        assert receipt["written"] == []
        assert receipt["retracted"] == ["c1"]
        # The receipt is on disk. A withdrawal whose only record is the return
        # value of the run that computed it is a fact that lives as long as the
        # process.
        assert len(tree.receipts()) == 2

    def test_the_claim_is_not_recallable_and_the_provider_is_not_in_the_path(
            self, tree):
        # The contract's §6: the remote can be down and the withdrawn fact still
        # must not come back. Nothing here asks anything anywhere.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        retracted(tree)
        row = tree.one("c1")
        assert row["verdict"] == cr.RETRACTED
        assert row["mark"] == "不可召回"
        assert row["recallable"] is False
        assert row["retraction"][0]["committed_at"] == SECOND
        assert tree.recall()["provider"]["in_the_path"] is False

    def test_the_line_stays_in_the_card_because_that_is_the_audit(self, tree):
        # `RETRACTED` is not `SUPERSEDED`: nothing took the line out, and a
        # filter that enforced the withdrawal by hiding it would be destroying
        # the record that the claim was ever believed. The contract's §9 wants
        # "this was believed then" and "this may not be used now" as two facts.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        retracted(tree)
        row = tree.one("c1")
        assert row["still_in_card"] is True
        assert "c1" in (tree.root / CARD).read_text(encoding="utf-8")
        assert "audit history" in row["why"] or "believed" in row["why"]

    def test_the_same_withdrawal_read_twice_is_one_receipt_and_one_entry(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        first = retracted(tree)
        again = retracted(tree, at=THIRD)
        assert again["reused"] is True
        assert len(tree.receipts()) == 2
        assert len(tree.one("c1")["retraction"]) == 1

    def test_a_later_reading_that_does_not_mention_it_cannot_clear_it(self, tree):
        # The source reappearing, or being read again, is a new claim with its
        # own revision — never an un-withdrawal. A withdrawal that a later
        # reading of the same source could quietly erase would be one that a
        # late remote update could erase too.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        retracted(tree)
        tree.commit("three", tree.reading(
            [tree.claim("c2", "另一段里她说周一", [45])], low=40, high=50),
            committed_at=THIRD)
        assert tree.one("c1")["verdict"] == cr.RETRACTED
        assert tree.one("c1")["recallable"] is False
        assert tree.one("c2")["verdict"] == cr.CURRENT

    def test_the_withdrawal_holds_when_the_text_under_it_moved_too(self, tree):
        # Two facts about one claim and neither cancels the other. The source
        # moved — so the claim is stale about text that is no longer there — and
        # it is withdrawn, which is the more decisive of the two: re-reading
        # cannot make it usable.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        retracted(tree)
        tree.write_messages(range(1, 61), edit={11: "她后来说改期了"})
        row = tree.one("c1")
        assert row["verdict"] == cr.RETRACTED
        assert row["source_revision"]["recorded"] != row["source_revision"]["current"]
        assert row["recallable"] is False

    def test_a_withdrawal_and_a_replacement_are_both_reported(self, tree):
        # The contract requires both to be able to hold of one claim without
        # either overriding the other, so they are two fields and not one. The
        # card is where the difference shows: the supersede took the line out,
        # the withdrawal did not need to.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], supersedes=["c1"])],
            low=30, high=40), committed_at=SECOND)
        retracted(tree, name="three", at=THIRD, low=50, high=60)
        row = tree.one("c1")
        assert len(row["withdrawal"]) == 1
        assert len(row["retraction"]) == 1
        assert row["verdict"] == cr.RETRACTED
        assert row["recallable"] is False

    def test_a_withdrawal_and_a_conflict_on_one_claim_are_both_reported(
            self, tree):
        # The same requirement on the axis that is NOT a replacement. The id
        # names more than one claim, so the label has to be the one that sends a
        # person to look — and the withdrawal still happened, so a reader that
        # dropped it because the label is about something else would let an id
        # the record explicitly took out of use read as merely disputed.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c1", "另一段里也用了这个 id", [35], card=OTHER_CARD,
                        views=[])], low=30, high=40), committed_at=SECOND)
        retracted(tree, name="three", at=THIRD, low=50, high=60)
        row = tree.one("c1")
        assert row["verdict"] == cr.CONFLICT
        assert row["recallable"] is False
        assert len(row["retraction"]) == 1
        assert "more than one span" in row["why"]

    def test_a_withdrawal_naming_an_id_nothing_wrote_is_reported_not_dropped(
            self, tree):
        # A retraction of a claim no receipt ever wrote. Dropping it would make
        # an id the record explicitly took out of use indistinguishable from one
        # nobody ever mentioned — and the withdrawal is what a consumer acts on,
        # so it holds whether or not the write is still here.
        retracted(tree, ids=("c9",))
        row = tree.one("c9")
        assert row["verdict"] == cr.RETRACTED
        assert row["recallable"] is False
        assert row["card"] is None
        assert row["retraction"][0]["committed_at"] == SECOND

    def test_the_receipt_binds_the_reading_the_source_and_the_baseline(self, tree):
        # What a person needs to re-examine the withdrawal later, all of it in
        # the one durable record: which place, which revision of the source,
        # which reading, which text the reader was looking at, which authority
        # was in play, and which ids came out of use. Nothing here is derivable
        # after the fact — the commit's own file does not survive the run.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        card_digest = us.card_digest((tree.root / CARD).read_text(encoding="utf-8"))
        retracted(tree)
        # Found by what it says and not by position: `receipts()` is sorted by
        # FILE NAME, which is a hash of each receipt's own key and orders them
        # by nothing a reader can see.
        document = next(json.loads(path.read_text(encoding="utf-8"))
                        for path in tree.receipts()
                        if json.loads(path.read_text(encoding="utf-8"))["state"]
                        == "RETRACTION_ONLY")
        assert len(document["key"]) == 5
        assert document["key"][0] == CARD and document["key"][4] == tree.identity(30, 40)
        assert document["reading_digest"]
        assert document["baseline"] == {"card": CARD, "card_digest": card_digest}
        assert document["accepted"] is None
        assert document["retracted"] == ["c1"]

    def test_the_rule_was_given_the_withdrawal_and_not_a_constant(self, tree):
        # `invalidate_claims` has two inputs and one of them used to be a
        # constant: every row that set `withdrawn` also cleared `verification`,
        # so the rule's `withdrawn` branch could not be reached and a reader was
        # told the rule decided when one of its inputs never varied. The first
        # half is the rule on its own; the second is the row this module hands
        # it for a retracted claim.
        revisions = {"span": "digest"}
        live = {"source_revisions": {"span": "digest"}, "verification": cr.EVIDENCED,
                "withdrawn": False}
        gone = {**live, "withdrawn": True}
        assert cr._cycle.invalidate_claims([live], revisions)[0]["recallable"] is True
        assert cr._cycle.invalidate_claims([gone], revisions)[0]["recallable"] is False
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        retracted(tree)
        assert tree.one("c1")["recallable"] is False
        assert tree.one("c1")["verdict"] not in cr.RECALLABLE_VERDICTS


# --------------------------------------------------------------------------
# the card and the record disagree
# --------------------------------------------------------------------------

class TestACardThatDoesNotHoldWhatTheRecordSays:

    def test_a_line_removed_by_hand_is_missing(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        card = tree.root / CARD
        head, body, tail = us.fence_parts(card.read_text(encoding="utf-8"))
        before, _, after = us.claims_region(body)
        card.write_text(head + us.FENCE_BEGIN + before
                        + us._claims_marked([]) + after + us.FENCE_END + tail,
                        encoding="utf-8")
        row = tree.one("c1")
        assert row["verdict"] == cr.MISSING
        assert row["recallable"] is False
        assert row["still_in_card"] is False

    def test_a_card_whose_fence_was_removed_is_missing(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        (tree.root / CARD).write_text("# Alice\n\n有人把整段删了。\n",
                                      encoding="utf-8")
        assert tree.one("c1")["verdict"] == cr.MISSING

    def test_a_person_editing_the_card_is_not_a_missing_claim(self, tree):
        # The fence exists so somebody disputing a claim can write next to it.
        # An edit around the block is the ordinary case and must not make the
        # claim unrecallable; it is reported as a fact about the CARD.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        card = tree.root / CARD
        card.write_text(card.read_text(encoding="utf-8") + "\n我不同意这条。\n",
                        encoding="utf-8")
        row = tree.one("c1")
        assert row["verdict"] == cr.CURRENT
        assert row["recallable"] is True
        assert row["card_changed_since"] is True

    def test_the_card_change_axis_does_not_claim_to_know_which_line_changed(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        document = tree.recall()
        assert "line_text" in document["unobserved"]
        assert "line's text" in document["unobserved"]["line_text"]

    def test_a_receipt_naming_a_claim_no_card_owns_is_missing(self, tree):
        # A dangling reference: a view routes to a claim nothing holds. Written
        # by hand here because the writer cannot produce it — the two entries a
        # commit makes are an owner and its views, and this removes the owner.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))

        def drop_the_owner(document):
            document["written"] = [entry for entry in document["written"]
                                   if entry["role"] != us.OWNER]

        tree.edit_receipt(change=drop_the_owner)
        row = tree.one("c1")
        assert row["verdict"] == cr.MISSING
        assert "dangling reference" in row["why"]
        assert row["recallable"] is False
        assert row["card"] is None


# --------------------------------------------------------------------------
# one id, two claims
# --------------------------------------------------------------------------

class TestAnIdThatNamesMoreThanOneClaim:

    def test_one_id_written_from_two_spans_is_a_conflict(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c1", "另一段里也用了这个 id",
                                                    [35], card=OTHER_CARD,
                                                    views=[])], low=30, high=40),
                    committed_at=SECOND)
        row = tree.one("c1")
        assert row["verdict"] == cr.CONFLICT
        assert row["recallable"] is False
        assert "more than one span" in row["why"]
        assert "30–40" in row["why"]

    def test_two_cards_each_holding_a_line_for_one_id_is_a_conflict(self, tree):
        # A person's edit, and the state no rule can read: the claim's line
        # copied into the card that only routed to it. Both cards now carry a
        # line for c1 and neither says the other is the owner, so which card the
        # claim lives in is not in the record at all.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        view = tree.root / VIEW
        view.write_text(
            "# widget\n\n项目视角。\n\n<!-- serveros:semantic:begin -->\n"
            "<!-- serveros:semantic:claims -->\n"
            "- `c1` **[OBSERVED_FACT]** alice：她说周五交付（agent_reconciled）\n"
            "<!-- serveros:semantic:claims:end -->\n"
            "<!-- serveros:semantic:end -->\n", encoding="utf-8")
        row = tree.one("c1")
        assert row["verdict"] == cr.CONFLICT
        assert row["recallable"] is False
        assert "two claims in two cards" in row["why"]

    def test_a_receipt_claiming_ownership_the_card_does_not_show_is_not_believed(
            self, tree):
        # The other side of the same question, and the answer goes the other way
        # on purpose. Here the damaged thing is the receipt: both entries of one
        # commit say OWNER, while the card one of them names carries a REFERENCE
        # line for that id. The writer never puts a reference under an owner
        # entry, so one of the two is wrong — and the card is the artifact a
        # person reads and can see for themselves which of the two kinds of line
        # it holds. Believing the receipt would report a second owner of a claim
        # nothing in the tree says it owns.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))

        def both_own_it(document):
            for entry in document["written"]:
                entry["role"] = us.OWNER

        tree.edit_receipt(change=both_own_it)
        roles = cr.build_index(tree.root)["claims"]["c1"]
        assert sorted(roles["owners"]) == [CARD]
        assert sorted(roles["referenced_by"]) == [VIEW]
        row = tree.one("c1")
        assert row["verdict"] == cr.CURRENT
        assert row["recallable"] is True
        assert row["card"] == CARD

    def test_a_view_that_later_owns_a_claim_of_its_own_stays_a_view(self, tree):
        # The ordinary sequence, and the one this module used to read as the
        # conflict above: the first commit puts a REFERENCE to c1 into VIEW, the
        # second OWNS a different claim in VIEW. Both entries are legal, and the
        # second one's `claims` is what the card carries afterwards — so taking
        # every carried id under that write's role reports c1 as owned by two
        # cards, which is "no rule can decide" for a record every rule can read.
        # A claim nobody may recall because a card points at it is the whole of
        # what A2 exists to prevent.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "VIEW 自己的主张", [35], card=VIEW, views=[])],
            low=30, high=40, card=VIEW), committed_at=SECOND)
        roles = cr.build_index(tree.root)["claims"]["c1"]
        assert sorted(roles["owners"]) == [CARD]
        assert sorted(roles["referenced_by"]) == [VIEW]
        first = tree.one("c1")
        assert first["verdict"] == cr.CURRENT
        assert first["recallable"] is True
        assert first["card"] == CARD
        second = tree.one("c2")
        assert second["verdict"] == cr.CURRENT
        assert second["card"] == VIEW

    def test_a_reference_no_receipt_explains_is_not_read_as_ownership(self, tree):
        # The same rule one write further back, where the record really is
        # silent: receipt "one" is what put c1 into VIEW as a reference, and with
        # it gone nothing in the record says how that id got into that card. The
        # card's own text does say — it points at the owner rather than holding
        # the line — and a person reading that card can see which of the two it
        # is. Reading the id as owned by whoever wrote the card last would make a
        # view the owner of somebody else's claim and report it CURRENT, which is
        # the one answer the line in front of them rules out.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "VIEW 自己的主张", [35], card=VIEW, views=[])],
            low=30, high=40, card=VIEW), committed_at=SECOND)
        gone = [path for path in tree.receipts()
                if json.loads(path.read_text(encoding="utf-8"))["committed_at"]
                == STAMP]
        assert len(gone) == 1, "the receipt for the first commit is the one deleted"
        gone[0].unlink()
        roles = cr.build_index(tree.root)["claims"]["c1"]
        assert sorted(roles["owners"]) == []
        assert sorted(roles["referenced_by"]) == [VIEW]
        row = tree.one("c1")
        assert row["verdict"] == cr.MISSING
        assert row["card"] is None
        assert row["recallable"] is False

    def test_a_conflict_is_not_quietly_resolved_by_precedence(self, tree):
        # The record also names the claim as replaced and the source has also
        # moved. The label has to be the one that says a person must look,
        # because that is the only repair — every other verdict is what a rule
        # decided, and this one is that no rule can decide.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c1", "另一段里也用了这个 id",
                                                    [35], card=OTHER_CARD,
                                                    views=[])], low=30, high=40),
                    committed_at=SECOND)
        tree.commit("three", tree.reading(
            [tree.claim("c9", "她说要改到下周", [50], supersedes=["c1"])],
            low=45, high=55), committed_at=THIRD)
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        assert tree.one("c1")["verdict"] == cr.CONFLICT

    def test_a_withdrawal_is_recorded_even_for_a_conflicted_id(self, tree):
        # Withdrawal is recorded independently of the label a reader is given:
        # the removal happened, and the conflict is about the record, so both
        # facts survive on the row instead of one overwriting the other.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading([tree.claim("c1", "另一段里也用了这个 id",
                                                    [35], card=OTHER_CARD,
                                                    views=[])], low=30, high=40),
                    committed_at=SECOND)
        tree.commit("three", tree.reading(
            [tree.claim("c9", "她说要改到下周", [50], supersedes=["c1"])],
            low=45, high=55), committed_at=THIRD)
        row = tree.one("c1")
        assert row["verdict"] == cr.CONFLICT
        assert row["withdrawal"]


# --------------------------------------------------------------------------
# the record is the only thing read, and it is read whole
# --------------------------------------------------------------------------

class TestTheRecordIsReadWholeOrNotAtAll:

    def test_a_receipt_of_another_schema_refuses_the_whole_answer(self, tree):
        # Not skipped. An index missing a receipt answers `recallable` for a
        # claim it never saw, and "may I use this" has no partial answer that is
        # safer than saying nothing.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.edit_receipt(change=lambda d: d.update(
            schema="serveros/understanding-receipt.v3"))
        with pytest.raises(cr.Refused) as caught:
            tree.recall()
        assert "v3" in str(caught.value)

    def test_a_receipt_that_is_not_json_refuses_the_whole_answer(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.receipts()[0].write_text("{ not json", encoding="utf-8")
        with pytest.raises(cr.Refused):
            tree.recall()

    def test_a_receipt_file_holding_something_else_refuses_the_whole_answer(
            self, tree):
        # Valid JSON and not a receipt. Treating it as an absent receipt is how
        # a committed claim drops out of the index while the file is still
        # sitting there.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.receipts()[0].write_text("[]", encoding="utf-8")
        with pytest.raises(cr.Refused) as caught:
            tree.recall()
        assert "does not hold a receipt" in str(caught.value)

    def test_a_receipt_with_no_role_refuses_the_whole_answer(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))

        def no_roles(document):
            for entry in document["written"]:
                entry.pop("role")

        tree.edit_receipt(change=no_roles)
        with pytest.raises(cr.Refused) as caught:
            tree.recall()
        assert "role" in str(caught.value)

    def test_a_key_that_does_not_say_what_it_was_read_from_is_refused(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.edit_receipt(change=lambda d: d.update(key=d["key"][:4]))
        with pytest.raises(cr.Refused) as caught:
            tree.recall()
        assert "identity" in str(caught.value)

    def test_a_relative_root_is_refused(self):
        with pytest.raises(cr.Refused) as caught:
            cr.recall(Path("records"))
        assert "not absolute" in str(caught.value)

    def test_a_tree_with_no_receipts_answers_about_nothing_and_says_so(self, tree):
        document = tree.recall()
        assert document["index"] == {"receipts": 0, "claims": 0, "cards": 0}
        assert document["claims"] == []
        assert document["counts"][cr.CURRENT] == 0

    def test_every_verdict_is_counted_even_when_it_is_zero(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        counts = tree.recall()["counts"]
        assert set(counts) == set(cr.VERDICTS)
        assert counts[cr.CONFLICT] == 0


# --------------------------------------------------------------------------
# the provider
# --------------------------------------------------------------------------

class TestTheProviderIsNeverInThePath:

    def test_no_provider_is_consulted_and_the_answer_says_so(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        provider = tree.recall()["provider"]
        assert provider["in_the_path"] is False
        assert provider["consulted"] is False
        assert provider["because"]

    def test_a_withdrawal_is_effective_with_no_provider_in_the_path(self, tree):
        # `understanding_semantic.eligible` states the rule: a retraction takes
        # effect locally and immediately, "and not when a remote memory provider
        # acknowledges it". So the withdrawal above has to be visible with the
        # provider absent — which is the only state this node can be in.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("c2", "她改成了周一", [35], supersedes=["c1"])],
            low=30, high=40), committed_at=SECOND)
        assert tree.one("c1")["recallable"] is False
        assert tree.one("c1")["withdrawal"]

    def test_the_axes_this_filter_cannot_see_are_named(self, tree):
        # `retraction` used to be the fourth name here, saying that the rule for
        # a withdrawal was tested while nothing durable carried a withdrawal
        # into this filter. It is not in the table any more because it is not
        # unobserved any more: a retraction is written into the receipt of the
        # reading that performed it and is read back on every run. A table of
        # what is NOT checked that kept naming a checked axis would be the same
        # error the other way round.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        assert set(tree.recall()["unobserved"]) == {"permission", "provider",
                                                    "line_text"}

    def test_an_axis_that_left_the_table_is_not_read_as_still_unchecked(self, tree):
        # Two halves, and the second is the one that matters: the name is gone
        # from `unobserved`, AND a withdrawal actually decides the verdict. A
        # table edit alone would have removed the warning without supplying the
        # answer.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.commit("two", tree.reading(
            [tree.claim("r1", "撤回 c1", [35], kind=us.RETRACTION,
                        supersedes=["c1"])], low=30, high=40),
            committed_at=SECOND)
        document = tree.recall()
        assert "retraction" not in document["unobserved"]
        assert document["claims"][0]["verdict"] == cr.RETRACTED
        assert document["claims"][0]["recallable"] is False

    def test_a_revoked_permission_is_reported_as_unobserved_not_as_a_verdict(
            self, tree):
        # There is no source-permission store in this fabric, deliberately, so
        # this filter cannot drop a claim for that reason. It says so rather than
        # letting `CURRENT` be read as "the source may still be recalled from" —
        # and the test asserts the limitation rather than a filter firing, since
        # a test that asserted a filter would be testing something that is not
        # there.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        document = tree.recall()
        assert "revoked permission" in document["unobserved"]["permission"]
        assert document["claims"][0]["verdict"] == cr.CURRENT

    def test_no_message_body_can_reach_the_output(self, tree):
        # The digest is over the records and the output carries the digest. If a
        # body reached a row, this would find it.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        text = json.dumps(tree.recall(), ensure_ascii=False)
        assert "m11" not in text
        assert "摘录" not in text


# --------------------------------------------------------------------------
# the producer tables and the layers
# --------------------------------------------------------------------------

class TestTheTablesAndTheLayersAgree:

    def test_every_key_the_recall_emits_has_a_named_producer(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        document = tree.recall()
        assert set(document) == set(cr.RECALL_PRODUCED)
        assert set(document["claims"][0]) == set(cr.RECALL_PRODUCED_CLAIM)

    def test_a_field_with_no_producer_is_refused(self, tree, monkeypatch):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        monkeypatch.delitem(cr.RECALL_PRODUCED_CLAIM, "why")
        with pytest.raises(Exception) as caught:
            tree.recall()
        assert "producer table does not name a source" in str(caught.value)

    def test_the_three_labels_of_the_contract_are_all_reachable(self):
        # `docs/INFORMATION-DOCUMENT-LIFECYCLE.md` §7 names stale, conflict and
        # 不可召回. Every verdict is one of those three or CURRENT, and no verdict
        # is unlabelled — a verdict a reader cannot map back to the contract is a
        # word this module invented.
        assert set(cr.MARK) == set(cr.VERDICTS)
        assert set(cr.MARK.values()) == {"可召回", "stale", "不可召回", "conflict"}
        assert cr.MARK[cr.SUPERSEDED] == "不可召回"
        assert cr.MARK[cr.MISSING] == "不可召回"
        assert cr.MARK[cr.UNRESOLVED] == "不可召回"

    def test_the_precedence_names_every_verdict_exactly_once(self):
        assert sorted(cr.PRECEDENCE) == sorted(cr.VERDICTS)
        assert cr.PRECEDENCE[0] == cr.CONFLICT
        assert cr.PRECEDENCE[-1] == cr.CURRENT

    def test_recallability_is_decided_by_exactly_one_rule(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        document = tree.recall()
        for row in document["claims"]:
            assert row["recallable"] == (row["verdict"] == cr.CURRENT)
        assert document["rule"]["given"]["revisions_compared"] == 1

    def test_the_label_and_the_rule_disagreeing_refuses_the_answer(
            self, tree, monkeypatch):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        real = cr._cycle.invalidate_claims

        def lying(claims, source_revisions):
            return [dict(row, recallable=True)
                    for row in real(claims, source_revisions)]

        monkeypatch.setattr(cr._cycle, "invalidate_claims", lying)
        with pytest.raises(cr.Refused) as caught:
            tree.recall()
        assert "would be a second answer" in str(caught.value)

    def test_it_writes_nothing(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        before = {p: p.read_bytes() for p in sorted(tree.root.rglob("*")) if p.is_file()}
        tree.recall()
        after = {p: p.read_bytes() for p in sorted(tree.root.rglob("*")) if p.is_file()}
        assert before == after

    def test_a_read_only_module_has_no_write_path_to_reach(self):
        source = (REPO_ROOT / "intake" / "claim_recall.py").read_text(
            encoding="utf-8")
        assert "_atomic_write" not in source
        assert "write_text" not in source
        assert ".write(" not in source
        assert "mkdir" not in source


# --------------------------------------------------------------------------
# the verb
# --------------------------------------------------------------------------

class TestTheVerb:
    """End to end, through the real CLI, against a tree the real writer filled."""

    def test_it_answers_over_a_real_commit(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        done = cli("work", "claim-recall", "--records-root", str(tree.root),
                   "--json")
        assert done.returncode == 0, done.stderr
        payload = json.loads(done.stdout)
        assert payload["schema"] == cr.SCHEMA
        assert payload["claims"][0]["verdict"] == cr.CURRENT
        assert payload["claims"][0]["recallable"] is True

    def test_the_lines_a_person_reads_carry_the_verdict_and_the_why(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        done = cli("work", "claim-recall", "--records-root", str(tree.root))
        assert done.returncode == 0, done.stderr
        assert "stale" in done.stdout
        assert "c1" in done.stdout
        assert "未观察" in done.stdout

    def test_a_stale_claim_and_a_refusal_have_different_exit_codes(self, tree):
        # The answer is unpleasant; the verb answered. A shell has to be able to
        # tell that from a refusal, and it reads `--json` when it wants a gate.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        tree.write_messages(range(1, 61), edit={11: "m11 改过了"})
        answered = cli("work", "claim-recall", "--records-root", str(tree.root))
        refused = cli("work", "claim-recall", "--records-root", str(tree.root),
                      "--claim", "c9")
        assert answered.returncode == 0, answered.stderr
        assert refused.returncode != 0
        assert refused.returncode != answered.returncode

    def test_a_relative_root_is_refused_with_a_reason(self):
        done = cli("work", "claim-recall", "--records-root", "records")
        assert done.returncode != 0
        assert "absolute" in done.stderr

    def test_an_id_the_record_never_held_is_refused_with_a_reason(self, tree):
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        done = cli("work", "claim-recall", "--records-root", str(tree.root),
                   "--claim", "c9")
        assert done.returncode != 0
        assert "no receipt names" in done.stderr

    def test_the_verb_loads_the_module_rather_than_shelling_out_to_it(self):
        source = (REPO_ROOT / "runtime" / "cli.py").read_text(encoding="utf-8")
        body = source.split("def cmd_work_claim_recall(")[1].split("\ndef ")[0]
        assert "intake" in body and "claim_recall.py" in body
        assert "subprocess" not in body
        assert not hasattr(cr, "main")
