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
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
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
        return {"id": claim_id, "kind": us.OBSERVED_FACT, "subject": "alice",
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

    def test_two_entries_owning_one_line_is_a_conflict(self, tree):
        # A record the writer cannot make and a damaged one can: both entries of
        # one commit claiming ownership, which is one reading and two cards.
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))

        def both_own_it(document):
            for entry in document["written"]:
                entry["role"] = us.OWNER

        tree.edit_receipt(change=both_own_it)
        row = tree.one("c1")
        assert row["verdict"] == cr.CONFLICT
        assert row["recallable"] is False
        assert "two claims in two cards" in row["why"]

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
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        assert set(tree.recall()["unobserved"]) == {"permission", "provider",
                                                    "line_text", "retraction"}

    def test_a_retraction_is_named_as_a_gap_rather_than_silently_passed(self, tree):
        # The one axis here that has a RULE and no producer: `eligible` states
        # that a withdrawal takes effect locally and at once, and a retraction
        # commits nowhere and writes no receipt, so nothing durable carries it
        # into this filter. A completed claim reads as CURRENT, and the answer
        # says so rather than letting CURRENT mean "no withdrawal applies".
        tree.commit("one", tree.reading([tree.claim("c1", "她说周五交付", [11])]))
        document = tree.recall()
        assert "eligible" in document["unobserved"]["retraction"]
        assert "gap in this answer" in document["unobserved"]["retraction"]
        assert document["claims"][0]["verdict"] == cr.CURRENT

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
