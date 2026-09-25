"""Real checks for the daily profile; synthetic fixtures are not live admission."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from intake.daily_cycle import (
    accepted_items,
    classify,
    continue_focus,
    fingerprint,
    instant,
    invalidate_claims,
    review_gate,
    select_focus,
)
from runtime.bounded_invocation import invoke_once

NOW = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def item(i="T-1", **extra):
    return {"id": i, "project": "test", "state": "READY", "scope_digest": "scope",
            "priority": "P1", "worker_stopped": True, "resources_released": True,
            "grant_ref": "g", "next_action": "research", **extra}


def a_completion(state="COMPLETE", **extra):
    """A board row's completion verdict, as `workchain.completion_of` writes it."""
    return {"state": state, "assessed": True, "missing_fields": [],
            "missing_documents": [], "unreported_documents": [],
            "why": "test fixture", **extra}


def grant():
    return {"ref": "g", "revoked": False, "valid_from": "2026-09-21T00:00:00Z",
            "expires_at": "2026-09-23T00:00:00Z", "projects": ["test"],
            "items": ["T-1", "T-2", "T-3"], "actions": ["research", "implement"],
            "scope_digests": {"T-1": "scope", "T-2": "scope", "T-3": "scope"},
            "budget": {"wall_seconds": 60, "max_attempts": 1,
                       "max_output_bytes": 40000, "exhausted": False}}


def packet():
    return {**item(), "action": "implement", "author_session": "author",
            "source_digest": "sources", "design_digest": "design", "plan_digest": "plan",
            "baseline_digest": "base", "allowed_review_models": ["verified-model"],
            "review_ttl_seconds": 900, "binding_ready": True, "prior_effect": "NONE"}


def review(p=None):
    p = p or packet()
    return {**{k: p[k] for k in ("id", "scope_digest", "source_digest", "design_digest", "plan_digest", "baseline_digest")},
            "adapter_verified": True, "process_exit_code": 0, "verdict": "APPROVE",
            "reviewer_session": "independent", "model": "verified-model",
            "reasoning_effort": "high", "observed_at": STAMP}


class SelectionTests(unittest.TestCase):
    def choose(self, rows, g=None, **kw):
        return select_focus(rows, {"g": grant() if g is None else g}, now=NOW,
                            observed_at=kw.pop("observed_at", STAMP), **kw)
    def test_selects_at_most_two_by_accepted_priority(self):
        ans=self.choose([item(),item("T-2",priority="P0"),item("T-3")])
        self.assertEqual(ans["selected"],["T-2","T-1"])
        self.assertTrue(ans["admission_required"])
    def test_existing_wip_uses_slot(self):
        ans=self.choose([item(state="ACTIVE"),item("T-2"),item("T-3")])
        self.assertEqual(ans["selected"],["T-2"])
    def test_uncertain_worker_uses_slot_even_if_closed(self):
        ans=self.choose([item(state="CLOSED",worker_stopped=False),item("T-2")])
        self.assertEqual(ans["carryover"],["T-1"])
    def test_waiting_does_not_open_new_capacity(self):
        ans=self.choose([item(state="WAITING"),item("T-2",state="CANCELLING"),item("T-3")])
        self.assertEqual(ans["selected"],[])
    def test_parked_released_is_not_carryover(self):
        ans=self.choose([item(state="WAITING",focus_disposition="PARKED",parking_acknowledged=True),item("T-2")]);self.assertEqual(ans["carryover"],[])
    def test_closed_objectives_do_not_revive(self):
        self.assertEqual(self.choose([item(state="CANCELLED")])["selected"],[])
    def test_missing_grant_does_not_admit(self):
        self.assertEqual(self.choose([item()],g={})["selected"],[])
    def test_revocation_does_not_admit(self):
        g=grant();g['revoked']=True;self.assertEqual(self.choose([item()],g)["selected"],[])
    def test_scope_must_be_nonempty(self):
        g=grant();g['scope_digests']={};self.assertEqual(self.choose([item(scope_digest=None)],g)["selected"],[])
    def test_expired_authorization(self):
        g=grant();g['expires_at']=STAMP;self.assertEqual(self.choose([item()],g)["selected"],[])
    def test_budget_exhaustion(self):
        g=grant();g['budget']['exhausted']=True;self.assertEqual(self.choose([item()],g)["selected"],[])
    def test_stale_projection_blocks_selection(self):
        self.assertEqual(self.choose([item()],observed_at='2026-09-21T00:00:00Z')['reason'],'STALE_BOARD')
    def test_future_projection_not_fresh(self):
        self.assertEqual(self.choose([item()],observed_at='2026-09-23T00:00:00Z')['reason'],'STALE_BOARD')
    def test_duplicate_id_fails(self):
        with self.assertRaises(ValueError):self.choose([item(),item()])
    def test_missing_priority_not_invented(self):
        self.assertEqual(self.choose([item(priority=None)])["selected"],[])
    def test_does_not_fill_empty_slots_with_work(self):
        self.assertEqual(self.choose([])['unused_slots'],2)
    def test_timezone_required(self):
        with self.assertRaises(ValueError):instant('2026-09-22T00:00:00')


def selection(items, state=None, why="", **extra):
    """A person's authored selection, in the shape `intake/workchain.py` writes.

    `state` is derived the way `load_today` derives it rather than defaulted
    here: a document that names Items is `SELECTED` and one that names none is
    `EMPTY`, so a fixture cannot carry the pair (`SELECTED`, `[]`) the producer
    cannot produce. A fixture that could would make this file test its own
    invention.
    """
    items = list(items)
    if state is None:
        state = "SELECTED" if items else "EMPTY"
    return {"state": state, "items": items, "why": why, "date": "2026-09-22",
            "path": "/records/projects/test/board/today.yaml",
            "by_project": {"test": {"state": state, "items": items,
                                    "date": "2026-09-22", "why": why}}, **extra}


class ContinuationIsNotRecommendation(unittest.TestCase):
    """`select_focus` proposes; `continue_focus` carries what a person accepted.

    The two questions look alike and are not, and the whole of P6's fourth change
    is that one function was answering both. Every test here is written so that
    it would still pass if the other function were the one under test — except
    the ones that assert they DISAGREE, which are the point.
    """

    def carry(self, rows, accepted, g=None, **kw):
        return continue_focus(rows, {"g": grant() if g is None else g}, accepted,
                              now=NOW, observed_at=kw.pop("observed_at", STAMP), **kw)

    def propose(self, rows, g=None, **kw):
        return select_focus(rows, {"g": grant() if g is None else g}, now=NOW,
                            observed_at=kw.pop("observed_at", STAMP), **kw)

    def test_the_same_row_continues_and_is_not_proposed(self):
        """One row, one acceptance, two functions, two answers — and the
        divergence is exactly the rule. A person accepted T-1; it has no
        established priority because no card carries one. The recommender must
        refuse it (ordering two proposals needs a ranking, and defaulting to P0
        would invent one). The continuer must carry it (a person already
        decided; there is nothing to rank)."""
        row = item(priority=None)
        accepted = selection(["T-1"])
        self.assertEqual(self.carry([row], accepted)["selected"], ["T-1"])
        self.assertEqual(self.propose([row])["selected"], [])
        self.assertEqual(self.propose([row])["excluded"],
                         [{"id": "T-1", "reason": "PRIORITY_NOT_ESTABLISHED"}])

    def test_the_recommender_does_not_propose_an_accepted_objective(self):
        """Both functions are pointed at the same board and the same selection.
        The accepted Item appears in the continuation and NOT in the proposal:
        one Item in two answers would be two pieces of work, and `work admit`
        reads the second one as a selection."""
        rows = [item(), item("T-2")]
        accepted = selection(["T-1"])
        proposal = select_focus(rows, {"g": grant()}, now=NOW, observed_at=STAMP,
                                accepted=accepted)
        self.assertEqual(proposal["selected"], ["T-2"])
        self.assertIn({"id": "T-1", "reason": "ALREADY_ACCEPTED_FOCUS"},
                      proposal["excluded"])

    def test_nothing_selected_is_zero_and_not_a_refill(self):
        """Acceptance A, as arithmetic. A person recorded a selection and it is
        finished; nothing new was chosen. Focus is 0 — and the recommender,
        given the same board, would have filled both slots. The two numbers are
        kept apart because only one of them is the answer to "what is today's
        focus"."""
        rows = [item("T-2"), item("T-3")]
        chose_nothing = selection([])
        kept = self.carry(rows, chose_nothing)
        self.assertEqual(kept["selected"], [])
        self.assertEqual(kept["reason"], "SELECTION_IS_EMPTY")
        self.assertEqual(kept["unused_slots"], 2)
        # The same board, asked the other question, proposes two.
        self.assertEqual(self.propose(rows)["selected"], ["T-2", "T-3"])

    def test_nobody_having_chosen_is_not_someone_choosing_nothing(self):
        chosen = self.carry([item()], selection([]))
        never = self.carry([item()], {"state": "UNPRODUCED", "items": [], "why": "no file"})
        self.assertEqual(chosen["reason"], "SELECTION_IS_EMPTY")
        self.assertEqual(never["reason"], "NO_SELECTION_AUTHORED")
        self.assertEqual(chosen["accepted_state"], "EMPTY")
        self.assertEqual(never["accepted_state"], "UNPRODUCED")
        # And neither carries a digest, for `STALE_BOARD`'s reason: a decision
        # that admits nothing has no selection to name. The digest appears only
        # where there is something to admit, which is what `work admit` reads.
        self.assertNotIn("selection_digest", chosen)
        self.assertNotIn("selection_digest", never)
        self.assertIn("selection_digest", self.carry([item()], selection(["T-1"])))

    def test_a_selection_that_could_not_be_read_is_not_an_empty_one(self):
        """An unreadable file is not a person choosing nothing. It is BLOCKED
        and not PLANNED, because the answer to "what did they choose" is
        unknown and a cycle that planned on it would be planning on a guess."""
        decision = self.carry([item()], {"state": "UNREADABLE", "items": [],
                                         "why": "the file did not parse"})
        self.assertEqual(decision["status"], "BLOCKED")
        self.assertEqual(decision["reason"], "SELECTION_UNREADABLE")
        self.assertEqual(decision["blocked_because"], "the file did not parse")
        # And the prose is not where a per-Item lookup is: a reader of a
        # decision that reached rows finds `{id: reason}` there.
        self.assertEqual(decision["because"], {})

    def test_a_projection_without_the_selection_does_not_answer_the_question(self):
        """Both the empty document and the missing one produce NO_SELECTION_
        AUTHORED here — they are both `nothing was accepted` — but the reason
        says which, because one is a person's file and the other is an adapter
        that never carried the field."""
        absent = self.carry([item()], None)
        self.assertEqual(absent["accepted_state"], "NOT_ON_THIS_PROJECTION")
        self.assertEqual(absent["reason"], "NO_SELECTION_AUTHORED")
        self.assertIn("not the same as nobody having chosen", absent["because"])

    def test_an_accepted_objective_still_needs_an_authorization(self):
        """Acceptance is of an OBJECTIVE, not of an action. The person choosing
        T-1 does not make the fabric able to find a grant for it."""
        accepted = selection(["T-1"])
        no_grant = self.carry([item()], accepted, g={})
        self.assertEqual(no_grant["selected"], [])
        self.assertEqual(no_grant["excluded"],
                         [{"id": "T-1", "reason": "MISSING_OR_REVOKED_GRANT"}])
        revoked = grant(); revoked["revoked"] = True
        self.assertEqual(self.carry([item()], accepted, g=revoked)["selected"], [])

    def test_an_accepted_objective_with_no_named_action_is_not_given_one(self):
        """The default this replaced was `research`, and it is gone. With it,
        this row was refused MISSING_OR_REVOKED_GRANT — a true sentence about
        the grant, arrived at by asking it about an action nobody named. The
        row's own gap is the one to report."""
        row = item()
        row.pop("next_action")
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "NEXT_ACTION_NOT_ESTABLISHED"}])
        self.assertEqual(self.propose([row])["excluded"],
                         [{"id": "T-1", "reason": "NEXT_ACTION_NOT_ESTABLISHED"}])

    def test_a_protected_effect_is_not_continued_by_a_selection(self):
        """A person choosing an objective does not authorize a production
        cutover; that is HUMAN_EXECUTED and stays so."""
        decision = self.carry([item(next_action="production.cutover")],
                              selection(["T-1"]))
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "HUMAN_EXECUTED"}])

    def test_an_accepted_objective_that_finished_does_not_continue(self):
        """Finished, and the contract says nothing is owed: the slot frees."""
        row = item(state="CLOSED", completion=a_completion())
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "TERMINAL_STATE"}])

    def test_a_terminal_row_with_no_verdict_is_not_a_finished_objective(self):
        """An unasked question is not a favourable answer.

        The row carries no `completion` at all, so "this objective is done" is
        not something the cycle can say. It continues and the unanswered
        question is named — the same rule `liveness` follows about workers, and
        the reason this is not read as "finished" by default.
        """
        decision = self.carry([item(state="CLOSED")], selection(["T-1"]))
        self.assertEqual(decision["selected"], ["T-1"])
        self.assertEqual(decision["because"]["T-1"], "COMPLETION_NOT_ASSESSED")
        self.assertEqual([u["fields"] for u in decision["unevaluated"]],
                         [["completion"]])

    def test_a_delivered_objective_missing_its_report_continues(self):
        """`DELIVERED` sounds done and is not, when the contract's own
        boundary says the report a delivery needs is not there."""
        row = item(state="DELIVERED", completion=a_completion(
            "OPEN", missing_documents=["report"]))
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["selected"], ["T-1"])
        self.assertEqual(decision["because"]["T-1"],
                         "AWAITING_ACCEPTANCE_EVIDENCE:report")
        self.assertEqual(decision["excluded"], [])

    def test_a_closed_objective_closed_without_its_evidence_is_named_as_such(self):
        """A closure the contract's own rule cannot check, and the word for it
        is different because what a reader would do about it is different."""
        row = item(state="CLOSED", outcome=None,
                   completion=a_completion("OPEN", missing_fields=["outcome"],
                                           missing_documents=["report"]))
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["because"]["T-1"],
                         "CLOSED_UNEVIDENCED:outcome,report")

    def test_a_delivered_objective_that_owes_nothing_still_finishes(self):
        """Discharged is not complete: what a delivered, evidenced Item waits
        for is a person's acceptance, which is not work this cycle can hold a
        slot for."""
        row = item(state="DELIVERED", completion=a_completion("DISCHARGED"))
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "TERMINAL_STATE"}])

    def test_the_two_answers_about_one_row_differ_only_where_they_should(self):
        """The asymmetry, pinned so it cannot be tidied away by accident.

        A `CLOSED` Item that still owes its report cannot take new work, so the
        RECOMMENDER counts it where it always did — excluded, holding no slot.
        Reading the obligation into the disposition instead would put finished
        Items back into `carryover` and report a node whose work is done as
        OVER_CAPACITY, which is the defect this module already carries a
        measurement for. And the CONTINUER must not call a person's accepted
        objective finished. Same reading, two callers, two answers.
        """
        row = item(state="CLOSED", completion=a_completion(
            "OPEN", missing_documents=["report"]))
        proposal = self.propose([row])
        self.assertEqual(proposal["excluded"],
                         [{"id": "T-1", "reason": "TERMINAL_STATE"}])
        self.assertEqual(proposal["carryover"], [])
        self.assertEqual(self.carry([row], selection(["T-1"]))["selected"],
                         ["T-1"])

    def test_a_worker_still_held_continues_even_though_the_state_is_terminal(self):
        """Acceptance D, as the order of two lines. The Run is CLOSED and the
        worker is still writing: nothing has been released, so the objective
        continues and holds its slot."""
        row = item(state="CLOSED", worker_stopped=False)
        decision = self.carry([row], selection(["T-1"]))
        self.assertEqual(decision["selected"], ["T-1"])
        self.assertEqual(decision["because"]["T-1"],
                         "WORKER_OR_RESERVATION_HELD:worker_stopped")
        self.assertEqual(decision["excluded"], [])

    def test_an_accepted_item_that_is_not_on_the_board_is_named(self):
        """A selection names an Item the board does not carry. Silence here
        would be a selection that shrank without saying so."""
        decision = self.carry([item("T-2")], selection(["T-1"]))
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "ACCEPTED_ITEM_NOT_ON_THE_BOARD"}])

    def test_more_accepted_objectives_than_the_profile_carries(self):
        rows = [item("T-1"), item("T-2"), item("T-3")]
        decision = self.carry(rows, selection(["T-1", "T-2", "T-3"]))
        self.assertEqual(decision["status"], "OVER_CAPACITY")
        self.assertEqual(decision["unused_slots"], 0)
        self.assertEqual(decision["recommendation_slots"], 0)

    def test_a_stale_board_blocks_both_questions(self):
        for decision in (self.carry([item()], selection(["T-1"]),
                                    observed_at="2026-09-21T00:00:00Z"),
                         self.propose([item()], observed_at="2026-09-21T00:00:00Z")):
            self.assertEqual(decision["status"], "BLOCKED")
            self.assertEqual(decision["reason"], "STALE_BOARD")

    def test_the_continuation_says_it_is_one_and_the_proposal_says_it_is_one(self):
        self.assertTrue(self.carry([item()], selection(["T-1"]))["continuation"])
        self.assertTrue(self.propose([item()])["recommendation_only"])
        self.assertNotIn("continuation", self.propose([item()]))

    def test_the_two_functions_classify_the_same_row_the_same_way(self):
        """The one copy of the rule, checked rather than asserted. `classify` is
        what both callers read; a second copy of these judgements would be a
        second answer to 'is this row still held', which the tree's ledger
        records as the failure a single mutation cannot catch."""
        for row in (item(), item(state="ACTIVE"), item(state="CLOSED"),
                    item(state="WAITING", focus_disposition="PARKED",
                         parking_acknowledged=True), item(state="INBOX"),
                    item(worker_stopped=False), item(next_action="production.cutover")):
            verdict = classify(row, {"g": grant()}, now=NOW)
            self.assertIn(verdict["disposition"],
                          ("HELD", "FINISHED", "PARKED", "ELIGIBLE", "BLOCKED"))


class TheOrderCannotBeAskedWithoutAPriority(unittest.TestCase):
    def test_ordering_a_row_with_no_priority_raises_rather_than_picking_one(self):
        from intake.daily_cycle import _order
        with self.assertRaises(ValueError):
            _order([item(priority=None)])

    def test_and_the_filter_that_keeps_it_safe_is_the_one_that_does(self):
        """The invariant, end to end: nothing reaches `_order` without a
        priority because `select_focus` excluded it first, and this is the
        assertion that the two agree."""
        decision = select_focus([item(priority=None)], {"g": grant()}, now=NOW,
                                observed_at=STAMP)
        self.assertEqual(decision["selected"], [])
        self.assertEqual(decision["excluded"],
                         [{"id": "T-1", "reason": "PRIORITY_NOT_ESTABLISHED"}])


class AcceptedItemsAreReadNotInvented(unittest.TestCase):
    def test_an_absent_document_is_not_an_empty_one(self):
        absent = accepted_items(None)
        empty = accepted_items({"state": "EMPTY", "items": []})
        self.assertEqual(absent["state"], "NOT_ON_THIS_PROJECTION")
        self.assertEqual(empty["state"], "EMPTY")
        self.assertNotEqual(absent["why"], empty["why"])


class ReviewTests(unittest.TestCase):
    def run_gate(self,p=None,v=None,g=None):
        p=p or packet();return review_gate(p,v or review(p),g or grant(),now=NOW)
    def test_pass_is_not_execution_authorization(self):
        a=self.run_gate();self.assertEqual(a['decision'],'ELIGIBLE_FOR_ADMISSION');self.assertFalse(a['execution_authorized'])
    def test_missing_author_is_not_independent(self):
        p=packet();p.pop("author_session")
        self.assertEqual(self.run_gate(p=p)["reason"],"AUTHOR_SESSION_UNRESOLVED")
    def test_same_session_rejected(self):
        v=review();v['reviewer_session']='author';self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_NOT_INDEPENDENT')
    def test_rejected_review_preserved(self):
        v=review();v['verdict']='CHANGES_REQUIRED';self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_NOT_APPROVED')
    def test_each_binding_is_checked(self):
        for k in ['id','scope_digest','source_digest','design_digest','plan_digest','baseline_digest']:
            with self.subTest(k=k):
                v=review();v[k]='drift';self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_INPUT_CHANGED')
    def test_expired_review(self):
        v=review();v['observed_at']='2026-09-21T00:00:00Z';self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_EXPIRED')
    def test_low_reasoning_not_silently_used(self):
        v=review();v['reasoning_effort']='low';self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_DEPTH_INSUFFICIENT')
    def test_bool_exit_is_not_real_exit_code(self):
        v=review();v['process_exit_code']=False;self.assertEqual(self.run_gate(v=v)['reason'],'REVIEW_RECEIPT_UNVERIFIED')
    def test_unverified_model_json_is_not_receipt(self):
        v=review();v['adapter_verified']=False;self.assertEqual(self.run_gate(v=v)['decision'],'BLOCKED')
    def test_production_stays_human(self):
        for effect in ['production.cutover','production.rollback','production.mutate']:
            p=packet();p['action']=effect;self.assertEqual(self.run_gate(p=p)['decision'],'HUMAN_EXECUTED')
    def test_effect_unknown_cannot_repeat(self):
        p=packet();p['prior_effect']='UNKNOWN';self.assertEqual(self.run_gate(p=p)['reason'],'RECONCILE_PRIOR_EFFECT_FIRST')
    def test_missing_binding_blocks(self):
        p=packet();p['binding_ready']=False;self.assertEqual(self.run_gate(p=p)['reason'],'ENVIRONMENT_NOT_READY')
    def test_source_change_invalidates_recall(self):
        c=[{'source_revisions':{'source':'v1'},'verification':'EVIDENCED','withdrawn':False}]
        self.assertTrue(invalidate_claims(c,{'source':'v1'})[0]['recallable'])
        self.assertFalse(invalidate_claims(c,{'source':'v2'})[0]['recallable'])
        self.assertNotIn('recallable',c[0])
    def test_null_or_missing_source_revision_never_recalled(self):
        for refs in [{'s':None},{'s':''},{'s':' '},'not-a-map',{}]:
            with self.subTest(refs=refs):
                a=invalidate_claims([{'source_revisions':refs,'verification':'EVIDENCED','withdrawn':False}],{})
                self.assertFalse(a[0]['recallable'])
    def test_withdrawn_claim_not_recalled(self):
        c=[{'source_revisions':{'s':'v'},'verification':'EVIDENCED','withdrawn':True}]
        self.assertFalse(invalidate_claims(c,{'s':'v'})[0]['recallable'])


class InvocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.receipts=self.root/'receipts'
    def tearDown(self):self.tmp.cleanup()
    def invoke(self, code, key='one', **extra):
        return invoke_once(key=key,argv=[sys.executable,'-c',code],cwd=self.root,
                           receipts=self.receipts,**extra)
    def test_real_exit_receipt_not_business_success(self):
        a=self.invoke('print("verified-process")');self.assertEqual(a['process_exit_code'],0)
        self.assertFalse(a['worker_stopped']);self.assertEqual(a['effect'],'UNVERIFIED')
    def test_idempotency_does_not_execute_twice(self):
        a=self.invoke('print("once")');b=self.invoke('print("once")')
        self.assertFalse(b['executed_now']);self.assertEqual(a['output_sha256'],b['output_sha256'])
    def test_changed_input_same_key_refused(self):
        self.invoke('print(1)')
        with self.assertRaises(ValueError):self.invoke('print(2)')
    def test_failed_exit_not_retried(self):
        a=self.invoke('raise SystemExit(7)');self.assertEqual(a['process_exit_code'],7)
        self.assertFalse(self.invoke('raise SystemExit(7)')['executed_now'])
    def test_timeout_retains_unknown(self):
        a=self.invoke('import time; time.sleep(5)',timeout=1)
        self.assertEqual(a['status'],'TIMED_OUT');self.assertEqual(a['effect'],'UNKNOWN')
        self.assertFalse(a['worker_stopped'])
    def test_output_is_bounded(self):
        a=self.invoke('print("x"*30000)',max_output_bytes=512)
        self.assertLessEqual((self.receipts/'one.stdout').stat().st_size,512)
        self.assertEqual(a['status'],'OUTPUT_LIMIT')
    def test_output_bound_does_not_limit_child_regular_files(self):
        a=self.invoke('from pathlib import Path; Path("native-cache").write_bytes(b"x"*30000); print("ok")',max_output_bytes=512)
        self.assertEqual(a["process_exit_code"],0)
        self.assertEqual((self.root/"native-cache").stat().st_size,30000)
    def test_private_log_mode(self):
        self.invoke('print("ok")');self.assertEqual((self.receipts/'one.stdout').stat().st_mode & 0o777,0o600)
    def test_key_path_escape_refused(self):
        with self.assertRaises(ValueError):self.invoke('print(1)',key='../bad')



class NativeReceiptTests(unittest.TestCase):
    def test_exit_zero_without_completion_is_not_review(self):
        from runtime.review_receipt import normalize_review
        ans=normalize_review(events=[{'type':'thread.started','thread_id':'s'}, {'type':'turn.started'}],
             final_text=None, process_receipt={'status':'EXITED','process_exit_code':0}, packet=packet(),
             configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
        self.assertEqual(ans['reason'],'NO_COMPLETED_TURN')
    def test_complete_result_retains_rejection(self):
        from runtime.review_receipt import normalize_review
        ans=normalize_review(events=[{'type':'thread.started','thread_id':'s'},{'type':'turn.started'}, {'type':'item.completed','item':{'type':'agent_message','text':json.dumps({'verdict':'CHANGES_REQUIRED','findings':['missing data'],'limitations':[]})}}, {'type':'turn.completed'}],
             final_text=json.dumps({'verdict':'CHANGES_REQUIRED','findings':['missing data'],'limitations':[]}),
             process_receipt={'status':'EXITED','process_exit_code':0},packet=packet(),
             configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
        self.assertTrue(ans['adapter_verified']);self.assertEqual(ans['verdict'],'CHANGES_REQUIRED')
        self.assertFalse(ans['execution_authorized'])
    def test_model_cannot_supply_its_own_authentication(self):
        from runtime.review_receipt import normalize_review
        body={'verdict':'APPROVE','findings':[],'limitations':[],'adapter_verified':True}
        ans=normalize_review(events=[{'type':'thread.started','thread_id':'s'},{'type':'turn.started'},{'type':'turn.completed'}],
             final_text=json.dumps(body),process_receipt={'status':'EXITED','process_exit_code':0},packet=packet(),
             configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
        self.assertEqual(ans['reason'],'REVIEW_SCHEMA_MISMATCH')

    def test_trailing_unfinished_turn_is_rejected(self):
        from runtime.review_receipt import normalize_review
        a=normalize_review(events=[{'type':'thread.started','thread_id':'s'}, {'type':'turn.started'}, {'type':'turn.completed'}, {'type':'turn.started'}], final_text='{}', process_receipt={'status':'EXITED','process_exit_code':0},packet=packet(),configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
        self.assertEqual(a['reason'],'REVIEW_TURN_AMBIGUOUS')
    def test_stale_final_file_is_rejected(self):
        from runtime.review_receipt import normalize_review
        body={'verdict':'APPROVE','findings':[],'limitations':[]}
        other={'verdict':'CHANGES_REQUIRED','findings':['bad'],'limitations':[]}
        a=normalize_review(events=[{'type':'thread.started','thread_id':'s'}, {'type':'turn.started'}, {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(other)}},{'type':'turn.completed'}],final_text=json.dumps(body),process_receipt={'status':'EXITED','process_exit_code':0},packet=packet(),configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
        self.assertEqual(a['reason'],'FINAL_NOT_BOUND_TO_COMPLETED_TURN')

    def test_nonstring_model_verdict_is_rejected(self):
        from runtime.review_receipt import normalize_review
        for verdict in [[],{},None,42]:
            with self.subTest(verdict=verdict):
                body={'verdict':verdict,'findings':[],'limitations':[]}
                a=normalize_review(events=[{'type':'thread.started','thread_id':'s'}, {'type':'turn.started'}, {'type':'turn.completed'}],final_text=json.dumps(body),process_receipt={'status':'EXITED','process_exit_code':0},packet=packet(),configured_model='verified-model',reasoning_effort='high',observed_at=STAMP)
                self.assertEqual(a['reason'],'INVALID_REVIEW_VERDICT')

if __name__=='__main__':unittest.main()
