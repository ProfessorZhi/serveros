#!/usr/bin/env python3
"""#417 — an offline end-to-end for out-of-band milestone delivery.

WHAT THIS FILE PINS
-------------------
One Task/Run and one CI milestone independently reach the outbound provider
after the GPT turn is gone, from source-shaped evidence, with no network:

    CloudCLI run_get (verbatim 1.41 result) ─┐
    GitHub Actions workflow_run             ─┼─> normalize -> decide -> compose
    signed-off PHASE_ACCEPTED input         ─┘        -> notifications.deliver
                                                      -> FakeLark (tests only)

The acceptance matrix is:

* VERIFIED_GREEN is delivered once, as INFO (not a red alert).
* ORDINARY_GREEN and YELLOW are card-only; they do not interrupt.
* RED/action_required sends a deterministic template even when the summarizer
  raises.
* `completed` without trusted native output or with a boot-id mismatch is
  UNKNOWN, never success.
* CI success is CI_PASSED, never RELEASE_ACCEPTED/PHASE_ACCEPTED.
* Duplicate observation, duplicate webhook and restart replay do not resend.
* A delivery with no remote id, or a provider failure, is UNKNOWN — never
  DELIVERED. An explicit reconcile inside the transport window can recover it.
* Prompts, transcripts and token-shaped secrets do not enter the message or the
  receipt ledger.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. HOSTED_CI is the PR's workflow. REAL_FEISHU_DELIVERY
is NOT claimed: `FakeLark` stands in for the provider, no credential is read and
no socket is opened. Only a real provider receipt plus owner approval could
claim that a person received a message.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (REPO / "integrations" / "fleet-v1-milestone-notify"
               / "milestone_notify.py")
FIXTURES = MODULE_PATH.parent / "fixtures"
MODULE_NAME = "serveros_fleet_v1_milestone_notify"
CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_module():
    return _load(MODULE_NAME, MODULE_PATH)


m = _load_module()


class FakeLark:
    """The outbound provider, with no CLI, no credential and no network."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None, capability="SUPPORTED"):
        self.receipts = list(receipts or [])
        self.capability = capability
        self.calls = []

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self.receipts:
            receipt = self.receipts.pop(0)
            if isinstance(receipt, BaseException):
                raise receipt
            return receipt
        return {"delivered": True, "remote_id": "om_default",
                "provider": self.provider_id, "transport": self.transport}


class MilestoneNotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="serveros-milestone-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = Path(self.tmp) / "milestones.sqlite3"
        self.fixtures = m.FixtureSource(FIXTURES)

    def payload(self, source, ref):
        return self.fixtures.read(source, ref)

    def evidence(self, source, ref):
        return m.normalize(source, self.payload(source, ref))

    def run_payload(self, run_id):
        payload = self.payload("cloudcli", "run-completed")
        payload["run"] = {**payload["run"], "runId": run_id}
        return payload

    def notifier(self, provider, **kwargs):
        kwargs.setdefault("conversation_ref", "conversation:ops")
        return m.MilestoneNotifier(provider, self.ledger, **kwargs)

    def ledger_row(self, delivery_key):
        db = sqlite3.connect(self.ledger)
        try:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM milestone_deliveries WHERE delivery_key=?",
                (delivery_key,)).fetchone()
            return dict(row)
        finally:
            db.close()

    # -- VERIFIED_GREEN ---------------------------------------------------- #
    def test_cloudcli_green_milestone_is_delivered_once_as_info(self):
        evidence = self.evidence("cloudcli", "run-completed")
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.VERIFIED_GREEN)
        self.assertEqual(decision.severity, "INFO")

        provider = FakeLark()
        first = self.notifier(provider).observe(evidence)
        self.assertEqual(first.state, m.DELIVERED)
        self.assertTrue(first.delivered)
        self.assertEqual(first.remote_id, "om_default")
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("VERIFIED_GREEN", first.message)
        self.assertIn("RUN_COMPLETED", first.message)
        # The native assistant text is read only as "is there a result", never
        # copied into the sentence.
        self.assertNotIn("COMPAT-OK", first.message)
        self.assertEqual(provider.calls[0]["conversation_ref"], "conversation:ops")

    def test_replay_and_restart_do_not_send_twice(self):
        evidence = self.evidence("cloudcli", "run-completed")
        first_provider = FakeLark()
        first = self.notifier(first_provider).observe(evidence)
        second = self.notifier(first_provider).observe(evidence)
        self.assertEqual(second.state, m.DUPLICATE)
        self.assertEqual(len(first_provider.calls), 1)

        # A new process, a new provider, the same durable ledger.
        restarted_provider = FakeLark()
        restarted = self.notifier(restarted_provider).observe(evidence)
        self.assertEqual(restarted.state, m.DUPLICATE)
        self.assertEqual(restarted.remote_id, "om_default")
        self.assertEqual(len(restarted_provider.calls), 0)
        self.assertEqual(restarted.delivery_key, first.delivery_key)

    def test_a_replay_does_not_invoke_the_caretaker_model(self):
        evidence = self.evidence("cloudcli", "run-completed")
        seen = []

        def summarizer(facts):
            seen.append(facts)
            return "one short brief"

        provider = FakeLark()
        notifier = self.notifier(provider, summarizer=summarizer)
        first = notifier.observe(evidence)
        self.assertEqual(first.state, m.DELIVERED)
        self.assertEqual(len(seen), 1)

        second = notifier.observe(evidence)
        self.assertEqual(second.state, m.DUPLICATE)
        self.assertEqual(len(seen), 1, "a replay must not spend a model call")

        restarted = self.notifier(FakeLark(), summarizer=summarizer).observe(evidence)
        self.assertEqual(restarted.state, m.DUPLICATE)
        self.assertEqual(len(seen), 1)

    def test_it_drives_the_existing_direct_lark_provider_without_sending(self):
        """The adapter is thin: it hands the sentence to the REAL provider class.

        `run` is monkeypatched, so there is no subprocess, no CLI and no Feishu;
        what is exercised is the provider's own capability probe, sentence path
        and receipt shape.
        """
        runtime = REPO / "runtime"
        transport = _load("serveros_runtime_message_transport",
                          runtime / "message_transport.py")
        direct = _load("serveros_direct_lark_notify",
                       runtime / "direct_lark_notify.py")
        seen = []
        real_run = transport.run

        def fake_run(argv, *, timeout=120, cwd=None):
            seen.append(list(argv))
            return {"ok": True, "data": {"message_id": "om_real_1"}}

        transport.run = fake_run
        self.addCleanup(lambda: setattr(transport, "run", real_run))

        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        observation = self.notifier(provider).observe(
            self.evidence("cloudcli", "run-completed"))
        self.assertEqual(observation.state, m.DELIVERED)
        self.assertEqual(observation.remote_id, "om_real_1")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][1:3], ["im", "+messages-send"])
        # The real transport path receives the derived short key, unchanged by
        # its own `[:50]`, not the 111-character ledger identity.
        sent_key = seen[0][seen[0].index("--idempotency-key") + 1]
        self.assertEqual(sent_key, observation.transport_key)
        self.assertLessEqual(len(sent_key), 50)

    def test_the_two_vocabularies_are_disjoint(self):
        """`milestone_kind` is the fact; `delivery_state` is the lifecycle.

        This pins #423's side of the #417/#422 vocabulary freeze proposal. The
        five positive kinds are evidence kinds; `DISPATCH_ACK` and
        `FEISHU_DELIVERED` are positions on the delivery chain and must never
        appear as a milestone kind.
        """
        milestone_kinds = {
            m.RUN_COMPLETED, m.CI_PASSED, m.PHASE_ACCEPTED,
            m.PR_ACCEPTED, m.TASK_COMPLETED,
        }
        delivery_states = {
            m.DELIVERED, m.UNKNOWN_DELIVERY, m.IN_FLIGHT, m.CARD_ONLY,
            m.SILENT_STATE, m.DUPLICATE, m.UNKNOWN_REPLAY,
        }
        self.assertEqual(milestone_kinds & delivery_states, set())
        for lifecycle in ("DISPATCH_ACK", "FEISHU_DELIVERED"):
            self.assertNotIn(lifecycle, milestone_kinds)
        self.assertEqual(
            milestone_kinds,
            {"RUN_COMPLETED", "CI_PASSED", "PHASE_ACCEPTED",
             "PR_ACCEPTED", "TASK_COMPLETED"})

    # -- transport key derivation (the 50-character ceiling) ---------------- #
    def test_two_runs_sharing_the_old_50_char_prefix_get_distinct_transport_keys(self):
        """The old transport key was the first 50 chars; that collided here.

        Both full identities share the same source/device/kind prefix, so
        `full[:50]` is identical for two different Runs. The derived transport
        key must differ, fit the ceiling, and be what the provider receives.
        """
        evidence_first = m.normalize("cloudcli", self.run_payload(
            "a1b1c184-ea82-4079-8da9-a96bd3568cc3"))
        evidence_second = m.normalize("cloudcli", self.run_payload(
            "ffffffff-ea82-4079-8da9-a96bd3568cc3"))
        first = m.decide(evidence_first)
        second = m.decide(evidence_second)
        full_first = m.delivery_key_for(first)
        full_second = m.delivery_key_for(second)
        self.assertNotEqual(full_first, full_second)
        # The premise of the bug: the old truncation made them the same key.
        self.assertEqual(full_first[:50], full_second[:50])

        short_first = m.transport_key_for(full_first)
        short_second = m.transport_key_for(full_second)
        self.assertNotEqual(short_first, short_second)
        self.assertLessEqual(len(short_first), 50)
        self.assertLessEqual(len(short_second), 50)
        self.assertTrue(short_first.startswith("ms1:"))

        provider = FakeLark()
        notifier = self.notifier(provider)
        one = notifier.observe(evidence_first)
        two = notifier.observe(evidence_second)
        self.assertEqual((one.state, two.state), (m.DELIVERED, m.DELIVERED))
        self.assertEqual([call["idempotency_key"] for call in provider.calls],
                         [short_first, short_second])
        self.assertEqual(self.ledger_row(full_first)["transport_key"], short_first)
        self.assertEqual(self.ledger_row(full_second)["transport_key"], short_second)

    def test_transport_key_is_stable_across_restart_and_ignores_the_sentence(self):
        base = self.payload("cloudcli", "run-completed")
        changed = {**base, "observed_at": "2026-10-27T12:00:00Z",
                   "summary": "a different sentence entirely"}
        evidence_first = m.normalize("cloudcli", base)
        evidence_second = m.normalize("cloudcli", changed)
        first = m.decide(evidence_first)
        second = m.decide(evidence_second)
        self.assertEqual(m.delivery_key_for(first), m.delivery_key_for(second))
        self.assertEqual(m.transport_key_for(m.delivery_key_for(first)),
                         m.transport_key_for(m.delivery_key_for(second)))

        first_provider = FakeLark()
        first_run = self.notifier(first_provider).observe(evidence_first)
        restarted_provider = FakeLark()
        restarted = self.notifier(restarted_provider).observe(evidence_second)
        self.assertEqual(restarted.state, m.DUPLICATE)
        self.assertEqual(restarted.transport_key, first_run.transport_key)
        self.assertEqual(restarted_provider.calls, [])

    def test_transport_key_is_case_sensitive_and_unicode_normalized(self):
        composed = self.payload("phase", "accepted")
        composed["key"] = "café"
        decomposed = self.payload("phase", "accepted")
        decomposed["key"] = "café"
        upper = self.payload("phase", "accepted")
        upper["key"] = "CAFÉ"

        composed_decision = m.decide(m.normalize("phase", composed))
        decomposed_decision = m.decide(m.normalize("phase", decomposed))
        upper_decision = m.decide(m.normalize("phase", upper))

        # NFC canonicalisation: the same identity in two encodings is one key.
        self.assertEqual(m.delivery_key_for(composed_decision),
                         m.delivery_key_for(decomposed_decision))
        self.assertEqual(
            m.transport_key_for(m.delivery_key_for(composed_decision)),
            m.transport_key_for(m.delivery_key_for(decomposed_decision)))
        # Case is part of the identity, not folded.
        self.assertNotEqual(m.delivery_key_for(composed_decision),
                            m.delivery_key_for(upper_decision))
        self.assertNotEqual(
            m.transport_key_for(m.delivery_key_for(composed_decision)),
            m.transport_key_for(m.delivery_key_for(upper_decision)))
        self.assertEqual(m.transport_key_for("stable"), m.transport_key_for("stable"))

    def test_an_old_ledger_schema_is_migrated_without_losing_rows(self):
        db = sqlite3.connect(self.ledger)
        try:
            db.executescript("""
                CREATE TABLE milestone_deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    milestone_key TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    state TEXT NOT NULL,
                    remote_id TEXT,
                    attempted_at TEXT NOT NULL,
                    why TEXT NOT NULL,
                    intent TEXT NOT NULL);
            """)
            db.execute(
                "INSERT INTO milestone_deliveries VALUES (?,?,?,?,?,?,?,?)",
                ("old-full-key", "m", m.VERIFIED_GREEN, m.DELIVERED, "om_old",
                 "2026-10-27T10:00:00+00:00", "old receipt", "{}"))
            db.commit()
        finally:
            db.close()

        ledger = m.MilestoneLedger(self.ledger)
        row = ledger.get("old-full-key")
        self.assertEqual(row["state"], m.DELIVERED)
        self.assertEqual(row["remote_id"], "om_old")
        self.assertIn("transport_key", row)
        self.assertIsNone(row["transport_key"])

    def test_a_legacy_unknown_row_is_not_replayed_and_keeps_the_old_short_key(self):
        evidence = self.evidence("cloudcli", "run-completed")
        decision = m.decide(evidence)
        full = m.delivery_key_for(decision)
        legacy = full[:50]
        text = m.message(decision)

        provider = FakeLark()
        notifier = self.notifier(provider)  # creates the (migrated) table
        db = sqlite3.connect(self.ledger)
        try:
            db.execute(
                "INSERT INTO milestone_deliveries "
                "(delivery_key, milestone_key, outcome, state, remote_id, "
                " attempted_at, why, intent, transport_key) "
                "VALUES (?,?,?,?,?,?,?,?,NULL)",
                (full, evidence.key, decision.outcome, m.UNKNOWN_DELIVERY,
                 None, datetime.now(timezone.utc).isoformat(),
                 "legacy UNKNOWN", json.dumps({"text": text})))
            db.commit()
        finally:
            db.close()

        replay = notifier.observe(evidence)
        self.assertEqual(replay.state, m.UNKNOWN_REPLAY)
        self.assertEqual(provider.calls, [])

        recovered = notifier.reconcile(full)
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertEqual(provider.calls[0]["idempotency_key"], legacy)
        self.assertEqual(len(provider.calls[0]["idempotency_key"]), 50)
        self.assertEqual(self.ledger_row(full)["transport_key"], legacy)

    def test_duplicate_github_webhook_is_idempotent(self):
        evidence = self.evidence("github_actions", "ci-green")
        provider = FakeLark()
        notifier = self.notifier(provider)
        first = notifier.observe(evidence)
        second = notifier.observe(evidence)
        self.assertEqual(first.state, m.DELIVERED)
        self.assertEqual(second.state, m.DUPLICATE)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len({call["idempotency_key"] for call in provider.calls}), 1)

    def test_phase_accepted_delivers_the_signed_evidence(self):
        evidence = self.evidence("phase", "accepted")
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.VERIFIED_GREEN)
        self.assertEqual(decision.why, m.PHASE_ACCEPTED)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.DELIVERED)
        self.assertIn("PHASE_ACCEPTED", observation.message)
        self.assertIn("pr:415", observation.message)
        self.assertIn("sha:dd5919a9", observation.message)

    # -- green is not an alert --------------------------------------------- #
    def test_ci_green_is_ci_passed_not_release_accepted(self):
        evidence = self.evidence("github_actions", "ci-green")
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.VERIFIED_GREEN)
        self.assertEqual(decision.why, m.CI_PASSED)
        self.assertEqual(decision.severity, "INFO")
        text = m.message(decision)
        self.assertIn("CI_PASSED", text)
        self.assertNotIn("RELEASE_ACCEPTED", text)
        self.assertNotIn("PHASE_ACCEPTED", text)

    def test_ordinary_green_is_card_only(self):
        payload = self.payload("cloudcli", "run-completed")
        payload["milestone"] = False
        evidence = m.normalize("cloudcli", payload)
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.ORDINARY_GREEN)
        self.assertFalse(decision.deliver)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.CARD_ONLY)
        self.assertIn("ORDINARY_GREEN", observation.card_summary)
        self.assertEqual(provider.calls, [])

    def test_yellow_recoverable_warning_is_card_only(self):
        payload = self.payload("github_actions", "ci-green")
        payload["workflow_run"]["conclusion"] = "neutral"
        evidence = m.normalize("github_actions", payload)
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.YELLOW)
        self.assertFalse(decision.deliver)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.CARD_ONLY)
        self.assertEqual(provider.calls, [])

    def test_phase_without_signoff_stays_yellow(self):
        payload = self.payload("phase", "accepted")
        payload["accepted"] = False
        payload.pop("accepted_by")
        evidence = m.normalize("phase", payload)
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.YELLOW)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.CARD_ONLY)
        self.assertEqual(provider.calls, [])

    def test_a_signed_phase_milestone_needs_evidence(self):
        payload = self.payload("phase", "accepted")
        payload["evidence"] = []
        with self.assertRaises(m.MilestoneRefused):
            m.normalize("phase", payload)

    def test_non_terminal_sources_are_silent(self):
        payload = self.payload("github_actions", "ci-green")
        payload["workflow_run"]["status"] = "in_progress"
        payload["workflow_run"]["conclusion"] = None
        evidence = m.normalize("github_actions", payload)
        self.assertEqual(m.decide(evidence).outcome, m.SILENT)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.SILENT_STATE)
        self.assertEqual(provider.calls, [])

    def test_an_untrusted_source_cannot_raise_an_alert(self):
        payload = self.payload("github_actions", "ci-action-required")
        payload["source_trusted"] = False
        evidence = m.normalize("github_actions", payload)
        self.assertEqual(m.decide(evidence).outcome, m.SILENT)
        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.SILENT_STATE)
        self.assertEqual(provider.calls, [])

    # -- RED and UNKNOWN --------------------------------------------------- #
    def test_red_action_required_uses_deterministic_template_when_summarizer_fails(self):
        evidence = self.evidence("github_actions", "ci-action-required")
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.RED)
        self.assertEqual(decision.severity, "ACTION")
        self.assertTrue(decision.action_required)

        def down(_facts):
            raise RuntimeError("model down and no fallback in the caller")

        text = m.message(decision, summarizer=down)
        self.assertIn("Action required", text)
        self.assertIn("Next:", text)
        self.assertNotIn("model down", text)

        provider = FakeLark()
        observation = self.notifier(provider, summarizer=down).observe(evidence)
        self.assertEqual(observation.state, m.DELIVERED)
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("Action required", observation.message)
        self.assertIn("CI_ACTION_REQUIRED", observation.message)
        self.assertNotIn("CI_PASSED", observation.message)

    def test_run_completed_without_native_output_is_unknown_not_success(self):
        evidence = self.evidence("cloudcli", "run-completed-no-output")
        decision = m.decide(evidence)
        self.assertEqual(decision.outcome, m.UNKNOWN)
        self.assertEqual(decision.severity, "ACTION")
        self.assertEqual(decision.why, "RUN_COMPLETED_WITHOUT_NATIVE_OUTPUT")

        provider = FakeLark()
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.DELIVERED)
        self.assertIn("UNKNOWN", observation.message)
        self.assertIn("Do not treat this as success", observation.message)
        self.assertNotIn("Turn completed", observation.message)
        # A caller-supplied gates_passed must not make the receipt say a gate
        # the classifier found missing had passed.
        self.assertIn("native-output=MISSING", observation.message)
        self.assertNotIn("native-output=PASS", observation.message)

    def test_boot_identity_mismatch_is_unknown(self):
        payload = self.payload("cloudcli", "run-completed")
        payload["bound_boot_id"] = "00000000-0000-0000-0000-000000000000"
        decision = m.decide(m.normalize("cloudcli", payload))
        self.assertEqual(decision.outcome, m.UNKNOWN)
        self.assertEqual(decision.why, "RUN_COMPLETED_WITHOUT_TRUSTED_BOOT")

    def test_a_missing_required_gate_is_unknown_not_partial_green(self):
        payload = self.payload("github_actions", "ci-green")
        payload["gates_passed"] = ["plugin", "shard-1"]
        decision = m.decide(m.normalize("github_actions", payload))
        self.assertEqual(decision.outcome, m.UNKNOWN)
        self.assertEqual(decision.why, "REQUIRED_GATES_NOT_EVIDENCED")

    # -- delivery UNKNOWN and recovery ------------------------------------- #
    def test_an_outcome_correction_is_a_new_delivery(self):
        """UNKNOWN then VERIFIED_GREEN is a correction, not a duplicate.

        The notification contract's identity includes the event word, so the
        correction gets its own key and one message. Replaying either outcome
        is still a DUPLICATE.
        """
        green = self.payload("cloudcli", "run-completed")
        unknown = {
            **green,
            "run": {
                **green["run"],
                "lastAssistantMessage": None,
                "note": "This run has ended, but the session has no readable "
                        "assistant message.",
            },
        }
        provider = FakeLark()
        notifier = self.notifier(provider)
        first = notifier.observe(m.normalize("cloudcli", unknown))
        second = notifier.observe(m.normalize("cloudcli", green))
        self.assertEqual(first.outcome, m.UNKNOWN)
        self.assertEqual(second.outcome, m.VERIFIED_GREEN)
        self.assertEqual(first.state, m.DELIVERED)
        self.assertEqual(second.state, m.DELIVERED)
        self.assertEqual(len(provider.calls), 2)
        self.assertNotEqual(first.delivery_key, second.delivery_key)
        self.assertIn("VERIFIED_GREEN", second.message)

    def test_provider_failure_is_unknown_not_delivered(self):
        evidence = self.evidence("cloudcli", "run-completed")
        provider = FakeLark(receipts=[RuntimeError("gateway 502")])
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        self.assertIsNone(observation.remote_id)
        self.assertEqual(self.ledger_row(observation.delivery_key)["state"],
                         m.UNKNOWN_DELIVERY)

    def test_unsupported_transport_is_unknown_not_delivered(self):
        evidence = self.evidence("cloudcli", "run-completed")
        provider = FakeLark(capability="UNSUPPORTED")
        observation = self.notifier(provider).observe(evidence)
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        self.assertEqual(provider.calls, [])

    def test_a_receipt_without_an_explicit_delivered_flag_is_unknown(self):
        provider = FakeLark(receipts=[{"remote_id": "om_x"}])
        observation = self.notifier(provider).observe(
            self.evidence("cloudcli", "run-completed"))
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        self.assertIsNone(observation.remote_id)

    def test_a_second_claim_loses_to_the_first(self):
        ledger = m.MilestoneLedger(self.ledger)
        first = ledger.claim(
            delivery_key="one-key", milestone_key="m", outcome=m.VERIFIED_GREEN,
            attempted_at="2026-10-27T10:00:00+00:00", intent="{}",
            transport_key=m.transport_key_for("one-key"))
        self.assertIsNone(first)
        second = ledger.claim(
            delivery_key="one-key", milestone_key="m", outcome=m.VERIFIED_GREEN,
            attempted_at="2026-10-27T10:00:01+00:00", intent="{}",
            transport_key=m.transport_key_for("one-key"))
        self.assertIsNotNone(second)
        self.assertEqual(second["state"], m.IN_FLIGHT)

    def test_a_delivered_row_is_never_downgraded(self):
        ledger = m.MilestoneLedger(self.ledger)
        key = "test-delivered-row"
        ledger.claim(delivery_key=key, milestone_key="milestone",
                     outcome=m.VERIFIED_GREEN,
                     attempted_at="2026-10-27T10:00:00+00:00", intent="{}",
                     transport_key=m.transport_key_for(key))
        self.assertTrue(ledger.finish(
            delivery_key=key, state=m.DELIVERED, remote_id="om_1",
            attempted_at="2026-10-27T10:00:01+00:00",
            why="provider returned a message id"))
        # A late UNKNOWN must not erase the delivered receipt.
        self.assertFalse(ledger.resolve(
            delivery_key=key, state=m.UNKNOWN_DELIVERY, remote_id=None,
            attempted_at="2026-10-27T10:00:02+00:00", why="late unknown"))
        row = ledger.get(key)
        self.assertEqual(row["state"], m.DELIVERED)
        self.assertEqual(row["remote_id"], "om_1")

    def test_a_crashed_claim_is_unknown_replay_and_reconcile_recovers(self):
        evidence = self.evidence("cloudcli", "run-completed")
        decision = m.decide(evidence)
        key = m.delivery_key_for(decision)
        ledger = m.MilestoneLedger(self.ledger)
        ledger.claim(
            delivery_key=key, milestone_key=evidence.key,
            outcome=decision.outcome,
            attempted_at=datetime.now(timezone.utc).isoformat(),
            intent=json.dumps({"text": m.message(decision)},
                              ensure_ascii=False, sort_keys=True),
            transport_key=m.transport_key_for(key))

        provider = FakeLark()
        replay = self.notifier(provider).observe(evidence)
        self.assertEqual(replay.state, m.UNKNOWN_REPLAY)
        self.assertEqual(provider.calls, [])

        recovered = self.notifier(provider).reconcile(key)
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertEqual(len(provider.calls), 1)

    def test_unknown_delivery_is_not_delivered_and_reconcile_recovers(self):
        evidence = self.evidence("cloudcli", "run-completed")
        no_id = FakeLark(receipts=[{"delivered": True, "remote_id": None}])
        first = self.notifier(no_id).observe(evidence)
        self.assertEqual(first.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(first.delivered)
        self.assertIsNone(first.remote_id)
        self.assertEqual(self.ledger_row(first.delivery_key)["state"],
                         m.UNKNOWN_DELIVERY)

        recovering = FakeLark()
        restarted = self.notifier(recovering)
        replay = restarted.observe(evidence)
        self.assertEqual(replay.state, m.UNKNOWN_REPLAY)
        self.assertEqual(recovering.calls, [])

        recovered = restarted.reconcile(first.delivery_key)
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertTrue(recovered.delivered)
        self.assertEqual(recovered.remote_id, "om_default")
        self.assertEqual(len(recovering.calls), 1)
        self.assertEqual(recovering.calls[0]["idempotency_key"],
                         first.transport_key)

        final = restarted.observe(evidence)
        self.assertEqual(final.state, m.DUPLICATE)
        self.assertEqual(len(recovering.calls), 1)

    def test_reconcile_outside_the_transport_window_does_not_resend(self):
        evidence = self.evidence("cloudcli", "run-completed")
        start = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        first = self.notifier(
            FakeLark(receipts=[{"delivered": True, "remote_id": None}]),
            clock=lambda: start).observe(evidence)

        late_provider = FakeLark()
        late = self.notifier(late_provider,
                             clock=lambda: start + timedelta(hours=2))
        observation = late.reconcile(first.delivery_key)
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertEqual(late_provider.calls, [])
        self.assertIn("window", observation.why)

    # -- secret / prompt boundary ------------------------------------------ #
    def test_prompts_and_secrets_do_not_enter_message_or_ledger(self):
        payload = self.payload("cloudcli", "run-completed")
        payload["summary"] = (
            "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 "
            "AKIAIOSFODNN7EXAMPLE "
            "Bearer abc.def.ghi password=hunter2 "
            "-----BEGIN PRIVATE KEY-----\nMIIEsecret\n-----END PRIVATE KEY-----")
        # The nested run is the source's verbatim result: only an allowlist of
        # fields is read. `prompt` there is ignored, not refused; what matters
        # is that it cannot reach the sentence or the ledger.
        payload["run"]["lastAssistantMessage"]["content"] = "PROMPT-LEAK-UNIQUE"
        payload["run"]["prompt"] = "PROMPT-FIELD-UNIQUE"

        provider = FakeLark()
        observation = self.notifier(provider).observe(
            m.normalize("cloudcli", payload))
        self.assertEqual(observation.state, m.DELIVERED)
        tokens = ("ghp_", "AKIAIOSFODNN7EXAMPLE", "Bearer abc", "hunter2",
                  "MIIEsecret", "PROMPT-LEAK-UNIQUE", "PROMPT-FIELD-UNIQUE")
        for token in tokens:
            self.assertNotIn(token, observation.message)

        intent = self.ledger_row(observation.delivery_key)["intent"]
        for token in tokens:
            self.assertNotIn(token, intent)

    def test_raw_fields_are_refused_at_the_closed_envelope(self):
        payload = self.payload("cloudcli", "run-completed")
        with self.assertRaises(m.MilestoneRefused):
            m.normalize("cloudcli", {**payload, "prompt": "do not carry me"})
        github = self.payload("github_actions", "ci-green")
        with self.assertRaises(m.MilestoneRefused):
            m.normalize("github_actions", {**github, "transcript": "do not carry me"})
        with self.assertRaises(m.MilestoneRefused):
            m.normalize("unknown_source", payload)

    def test_an_unknown_run_status_is_refused(self):
        payload = self.payload("cloudcli", "run-completed")
        payload["run"] = {**payload["run"], "status": "almost-done"}
        with self.assertRaises(m.MilestoneRefused):
            m.normalize("cloudcli", payload)

    # -- boundaries -------------------------------------------------------- #
    def test_the_module_has_no_network_or_credential_import(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & {"subprocess", "socket", "ssl", "urllib", "http",
                        "requests", "os"},
            set())
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("SERVEROS_ALERT_RECIPIENT", source)
        self.assertNotIn("getenv", source)
        self.assertNotIn("__import__", source)
        self.assertNotIn("importlib.import_module", source)

    def test_fixture_source_is_the_offline_source_seam(self):
        source = m.FixtureSource(FIXTURES)
        self.assertIsInstance(source, m.SourceRead)
        self.assertIsInstance(source.read("github_actions", "ci-green"), dict)
        with self.assertRaises(m.MilestoneRefused):
            source.read("cloudcli", "../../etc/passwd")
        with self.assertRaises(m.MilestoneRefused):
            source.read("unknown_source", "ci-green")


if __name__ == "__main__":
    unittest.main()
