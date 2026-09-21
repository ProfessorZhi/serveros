"""Real checks for the daily profile; synthetic fixtures are not live admission."""
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from intake.daily_cycle import select_focus, review_gate, invalidate_claims, fingerprint, instant
from runtime.bounded_invocation import invoke_once

NOW = datetime(2026, 9, 22, 1, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def item(i="T-1", **extra):
    return {"id": i, "project": "test", "state": "READY", "scope_digest": "scope",
            "priority": "P1", "worker_stopped": True, "resources_released": True,
            "grant_ref": "g", "next_action": "research", **extra}


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
