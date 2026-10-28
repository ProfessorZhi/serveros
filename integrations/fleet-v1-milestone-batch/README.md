# fleet-v1-milestone-batch — one dispatch, N devices, one summary

`#417`'s owner scenario, in their words: a ChatGPT turn dispatches several Runs
across devices and returns without waiting; the Tokyo loop keeps reading them;
when every Run of that one dispatch is terminal, **one** batch summary reaches
the owner over the existing Product IM / Feishu path, and a member that failed,
went unknown or went offline raises a **finite, actionable** alert instead of
silence. Not a resident LLM agent per device, not a new scheduler, and not
`serverosd` sending Feishu.

#423 (delivery), #426 (sources), #428 (the tick) and #446 (the bounded runner)
already do the reading. What none of them owns is the *correlation*: which refs
belong to one dispatch, and when that set is done. That is this directory, and
nothing else.

## What it is not

| it is not | why |
|---|---|
| a second event bus | the input is #428's `run_once()` return value; the output is a frozen `BatchTick`. No queue, no subscriber, no thread, no callback registry. |
| a second outbound provider | every message goes through #423's notifier: its claim, its receipt ledger, its transport key, its capability probe. This module has no credential and no transport. |
| a scheduler | it never reads a source and never decides when to look. A caller drives it after a tick. |
| a second **delivery** state | a verdict is recomputed from the report; what was already sent is the ledger's answer. `registration.py` writes one durable *declaration* — an input — and never a claim about delivery. |
| a reporting system | two message templates, both deterministic, no model call. |

## The contract

```
#456 commander plan + ACKs
  └─ registration.from_commander(...) -> one durable file, one Batch ID
       └─ .batch       -> Batch(batch_id, members=[BatchMember(source, ref|task_id, device_ref, boot_id|source_sha, role)], expires_at)
       └─ .batch.watches()  -> the #428 Watch list, derived — never a second list

report = MilestoneObserver.run_once()          # #428, unchanged
tick   = BatchAggregator(batch, notifier=…).absorb(report)
  └─ .state / .verdicts / .counts               open, or settled: SENT | REPLAY
  └─ .alerts                                    the members nothing else spoke about
  └─ .summary                                   the one summary, with #423's receipt
  └─ .receipts                                  delivery_key + provider remote_id, per message
```

Keys, all derived, never stored twice:

```
batch:<batch_id>:summary                       one key for the whole dispatch
batch:<batch_id>:alert:<member key>:<VERDICT>  one key per alerted member
```

`BatchMember.__post_init__` validates itself by constructing a `#428` `Watch`,
so a member can never be something the tick would refuse to start with. A
`Batch` refuses two members that name the same `source:ref`, for the same reason
#428 refuses two watches for it: one tick cannot hold two facts about one ref.

`absorb()` never raises for a row. A row it cannot interpret settles nothing and
leaves the batch open; the batch deadline, not a guess, is what ends that wait.

## The durable declaration

`#456`'s `batch-commander` decides *what to dispatch*; `#428` can only watch a
ref that already exists. Between them is a fact neither owns: **which refs one
ChatGPT turn created, and for which device**. Without it, the moment the chat
that issued the dispatch exits, nothing can trace the dispatch — the Runs are
still running and nobody knows they were one batch.

So `registration.py` writes exactly one durable document per dispatch:

```json
{"schema": "serveros/fleet-milestone-batch-registration.v1",
 "batch_id": "dispatch-2026-10-28-alpha",
 "registered_at": "...", "registered_by": "chatgpt",
 "conversation_ref": "chatgpt:conv-…", "dispatch_ref": "…",
 "expires_at": "…", "plan_digest": "sha256:…", "amendments": [],
 "members": [{"task_id": "t-tokyo", "source": "cloudcli",
              "provider": "claude", "device_ref": "device:…",
              "session_ref": "session:…", "run_id": "run_…",
              "delivery_id": "om_…", "boot_id": "boot-…",
              "source_sha": null, "role": "tokyo"}]}
```

The ten member columns are deliberately separate, and a test asserts they are
pairwise distinct for one real dispatch (`ID_CONFLATION` refuses `run_id ==
delivery_id`, or a `session_ref` reused as either):

| column | what it answers | who owns it |
|---|---|---|
| `task_id` | #456's stable name for the task, valid **before** any Run exists | #456 |
| `source` | which #426/#428 adapter reads it | #428 |
| `provider` | which agent runtime ran it — `claude`, `codex` | #456 |
| `device_ref` | which machine | #428 |
| `session_ref` | the agent session the task ran in | #456/#423 |
| `run_id` | the Run — **the watch ref**, `None` while queued | source |
| `delivery_id` | #423's dispatch observation — **not** a watch ref | #423 |
| `boot_id` | the boot that may qualify a cloudcli Run | #428 |
| `source_sha` | the commit that may qualify a CI run | #428 |
| `role` | the human label used in messages | this file |

**An ACK is not an execution.** `from_commander` accepts an ACK with no
`run_id` (`ack_only`, or the commander queued it) and files a member that is
`watchable == False`: `BatchMember.ref is None`, its key is
`<source>:task:<task_id>`, and `Batch.watches()` leaves it out entirely. It can
never be read, so it can never be counted as done. Such a batch also refuses
`NO_DEADLINE`: a member no source can speak about must have a deadline, or it is
silent for ever. When the Run does appear, `registration.amend(path, task_id=…,
run_id=…, boot_id=…)` fills it in and `BatchAggregator.refresh(batch)` adopts
it — unless the member already settled, which is `TOO_LATE`.

`save` is content-addressed, not overwrite: a second registration of the same
batch is `UNCHANGED` (no write at all) or `CONFLICTING_REGISTRATION` (refused,
the first bytes kept). `plan_digest` pins which #456 plan a file came from.

`from_commander` refuses a lot, on purpose: `COMMANDER_CONTRACT_MISSING` (no
`validate_batch`), `BAD_PLAN`, `COMMANDER_REFUSED` (verdict not `PASS`),
`ACK_MISSING`, `ACK_UNKNOWN_TASK`, `ACK_INCOMPLETE` (no `delivery_id`, a
duplicate, or a plan/ACK `session_ref` disagreement). It records; it never
executes, and it has no column a verdict could hide in.

## The verdict table

One #428 row settles one member. The order is the contract.

| row (`state`, `outcome`) | verdict | message raised by the batch |
|---|---|---|
| `NO_EVENT`, `SKIPPED_BACKOFF`, `RATE_LIMITED` | `PENDING` | none — the tick established nothing |
| `REF_MISMATCH` / `DEVICE_SCOPE_MISMATCH` / `BOOT_SCOPE_MISMATCH` / `SOURCE_SHA_MISMATCH` | `UNKNOWN` | **alert** |
| `NOT_QUALIFYING` | `SKIPPED` | none — the caller declared it non-qualifying |
| `EXPIRED`, or the batch deadline passed on a watchable member | `OFFLINE` | **alert** |
| the batch deadline passed on a member no Run was ever issued for | `UNOBSERVED` | **alert** |
| `REFUSED` with a retryable code (`RUN_NOT_FOUND`, timeouts) | `PENDING` | none — it is a read that can clear |
| `REFUSED` with any other code, `MILESTONE_REFUSED` | `UNKNOWN` | **alert** |
| outcome `VERIFIED_GREEN` | `VERIFIED` | summary line only |
| outcome `ORDINARY_GREEN` / `YELLOW` | `GREEN` / `ATTENTION` | summary line only |
| outcome `RED` | `FAILED` | summary line only |
| outcome `UNKNOWN` | `UNKNOWN` | summary line only |
| anything else | `PENDING` | none |

Two rules are worth their own sentence:

1. **`state` before `outcome`.** #428 stamps `SKIPPED_BACKOFF` and
   `RATE_LIMITED` with `outcome=UNKNOWN` — the same word a genuinely
   unestablished outcome carries. Reading the outcome first would alert on a Run
   that is merely being retried, and would let a read budget settle a member.
2. **Settlement is by outcome, not by delivery state.** A Run that failed is a
   failed Run whether or not the phone was offline. Waiting for delivery would
   let a provider outage hold the whole batch open — the one thing the batch
   exists to avoid.
3. **A first settlement is frozen for the life of the process.** `UNOBSERVED` at
   the deadline does not become `VERIFIED` when the Run finally appears: the
   owner was told, and `refresh` refuses that adoption as `TOO_LATE` rather than
   retroactively making the message a lie.

`ACK_ONLY`, `RUN_NOT_FOUND`, a `false` completion and `UNOBSERVED` are never
success. `ACK_ONLY` cannot be watched at all; `RUN_NOT_FOUND` is a read that can
clear (`PENDING`); a completed Run without trusted native output is `UNKNOWN`,
never `VERIFIED`; and a member nothing could observe is `UNOBSERVED`, not a
quiet pass.

## The message budget

For one dispatch:

* **exactly one** summary, under one key, once (`batch:…:summary`);
* **one** alert per member that settled `FAILED`/`UNKNOWN`/`OFFLINE`/
  `UNOBSERVED` **and that #423 never spoke about** (its row names no delivery
  key and it was not delivered);
* **at most `MAX_ALERTS` (10)** such alerts. Everything past the cap is *named
  in the summary*, which gains one line: `另有 N 项未单独告警，见上行明细`. The cap
  therefore bounds noise without ever becoming silence.

The second clause is the anti-noise rule. #423 already delivers a `RED` and an
`UNKNOWN` as their own milestone messages, as they happen; a batch alert for
those would be the same fact twice. The members that would otherwise stay silent
for ever are the ones the tick could not observe at all — a scope mismatch, a
permanent refusal, an expired window, a batch deadline. Those get the alert. The
total is `1 + (members nothing else could speak about)`, and a test pins both
halves. A twelve-device dispatch that #423 can say nothing about produces eleven
messages, not thirteen.

An unconfirmed send is **reconciled under the same transport key**, never resent
under a new one: that is #423's own window and #423's own rule.

## Delivery receipts

`tick.receipts` hands the caller one `Receipt` per message **this tick caused** —
the `delivery_key`, #423's `outcome` word, the row's `state`, the provider's own
`remote_id`, the 50-char `transport_key`, and `confirmed` (delivered, now or by
an earlier tick). A summary that is a replay carries the *original* `remote_id`
and `state=DUPLICATE`; it is a receipt, not a second send.

## The one exact patch this needs from #423 — **BLOCKED**

State as of 2026-10-28:

| fact | evidence |
|---|---|
| #423's HEAD is `d9b2948a62609e16007f2fe0ae288f9652d64cd1` (`feat/fleet-v1-milestone-notify-20261027`, base `main`, draft) | `git ls-remote` |
| the `milestone_notify.py` in this tree is byte-identical to that HEAD | blob `72fcec8d553f1568a114449d352e1803250d26cc` on both |
| `send_text` does not exist there | `hasattr(MilestoneNotifier, "send_text") is False`, asserted by `test_the_real_notifier_without_the_seam_is_refused_up_front` |
| the seam is owned by the Dell lane, in a stacked Draft, not by this PR | owner handoff on #423, comment `6460182021` |

So the wire is **BLOCKED** on its owner: `BatchAggregator` refuses a notifier
without `send_text` at construction (`NOTIFIER_SEAM_MISSING`), and this lane does
**not** work around it. The tests drive the behaviour through
`PublicSeamStub` / `SeamedNotifier`, which are **sealed** stand-ins — they add
`send_text` using only #423's public surface (`ledger.claim/finish/get`,
`transport_key_for`, `Observation`, `provider`, `conversation_ref`, `clock`, and
`runtime/notifications.capability_of`) and raise `AttributeError` for **any**
attribute starting with `_`. A test asserts that no production module here names
`._send(`, `._prior_observation`, `._now(` or `._scrub(`.

When #423 lands the method, the only change on this side is passing the real
notifier; the two assertions above flip and the stub is deleted.

The proposal below is kept as the specification the Dell lane is implementing —
the semantics it must reproduce are this directory's tests, not this
directory's code.

### The exact patch

`MilestoneNotifier` has no public way to send a caller-authored sentence:
`observe()` derives the whole message from a payload, and `_send()` is private.
A batch summary is a statement about *several* milestones, not a milestone, so
this contract needs one public method. The proposal, as a patch to
`integrations/fleet-v1-milestone-notify/milestone_notify.py`:

```python
    def send_text(self, *, delivery_key: str, milestone_key: str, outcome: str,
                  text: str, why: str) -> "Observation":
        """Send one caller-authored sentence under an existing delivery key.

        This is `observe`'s outbound half with the sentence supplied by the
        caller instead of derived from a payload: same claim, same transport
        key, same finish, same reconcile. It exists because a batch summary is
        a statement about several milestones, not a milestone.
        """
        delivery_key = _text(delivery_key, "delivery_key", 512)
        milestone_key = _text(milestone_key, "milestone_key", 512)
        outcome = _text(outcome, "outcome", 32)
        text = _text(text, "text", 2000)
        transport = transport_key_for(delivery_key)
        intent = json.dumps({"text": text, "transport_key": transport},
                            ensure_ascii=False, sort_keys=True)
        prior = self.ledger.claim(delivery_key=delivery_key,
                                  milestone_key=milestone_key, outcome=outcome,
                                  attempted_at=self._now().isoformat(),
                                  intent=intent, transport_key=transport)
        if prior is not None:
            return _prior_observation(prior, delivery_key)
        try:
            state, remote_id, sent_why = self._send(text, transport)
        except Exception as exc:      # a claim must never be left IN_FLIGHT
            state, remote_id = UNKNOWN_DELIVERY, None
            sent_why = _scrub(f"{type(exc).__name__}: {exc}")[:240]
        self.ledger.finish(delivery_key=delivery_key, state=state,
                           remote_id=remote_id,
                           attempted_at=self._now().isoformat(),
                           why=sent_why, intent=intent)
        row = self.ledger.get(delivery_key) or {"state": state,
                                                "remote_id": remote_id}
        if row["state"] == DELIVERED and state != DELIVERED:
            sent_why = "another observation recorded the delivered receipt"
        return Observation(outcome, row["state"], delivery_key, row["remote_id"],
                           text, None, sent_why, row["state"] == DELIVERED,
                           transport)
```

What it changes about #423's data: `milestone_deliveries` gains rows whose
`delivery_key` starts with `batch:` and whose `outcome` is `BATCH_SUMMARY` or
`BATCH_ALERT`. Neither word is in #423's milestone vocabulary, and a test here
pins that they can never collide with it. If the owner prefers a declared closed
set over free text, that is two lines (`NON_MILESTONE_OUTCOMES`) in their module
— but this side already sends exactly those two words.

Until this lands, `BatchAggregator` raises `NOTIFIER_SEAM_MISSING` at
construction — up front, not at the moment a summary is due.

### Why not one of the other three ways

| way | why not |
|---|---|
| call `_send` directly | a second write path to the one provider, with no claim: two replicas could both send. |
| a second ledger / table | a second source of truth for "was this delivered", which is the thing #423 exists to have exactly one of. |
| extend `observe()` with synthetic `Evidence` | a batch is not a milestone: it would put a `batch` source into `_SOURCES` and a batch kind into `MILESTONE_KINDS`, and a reader could no longer tell a milestone from a summary. |

## Wiring, without a new loop

```python
report = observer.run_once()          # #428, or #446's tick
tick = aggregator.absorb(report)      # this module: pure, no I/O but the ledger
```

#446's `MilestoneRunner` already owns the loop, the lock and the rate limit; the
aggregator is one more pure call inside a tick that already exists. Nothing here
starts a service, and nothing here wakes a model.

## Evidence

`tests/test_fleet_v1_milestone_batch.py` — 27 tests, CODE + UNIT:

| #417's requirement | test |
|---|---|
| N devices | `test_one_dispatch_across_three_devices_sends_exactly_one_summary` |
| queued ACK, not started | `test_a_queued_ack_is_not_a_start_and_not_an_end` |
| partial success | `test_partial_success_sends_one_summary_and_no_second_message` |
| UNKNOWN | `test_a_completed_run_without_native_output_is_unknown_not_verified` |
| `RUN_NOT_FOUND` | `test_run_not_found_keeps_the_batch_open_and_then_completes` |
| restart replay | `test_a_restart_replays_the_receipt_and_sends_nothing` |
| cross-boot runId collision | `test_the_same_run_id_cannot_be_declared_for_two_boots`, `test_a_payload_from_another_boot_never_verifies_a_member` |
| one-shot ledger receipt | `test_the_settled_batch_has_exactly_one_receipt_row`, `test_an_unconfirmed_summary_is_reconciled_not_resent` |
| offline | `test_an_offline_member_is_bounded_by_the_deadline` |

The suite was mutation-checked: dropping the "not a fact" states, forcing
`covered=False`, and removing the first-settlement freeze each turn exactly the
tests written for them red, and nothing else.

`tests/test_fleet_v1_milestone_batch_registration.py` — 24 tests + 1 skip,
CODE + UNIT. The skip is the live `#456` contract (`#456` is not on this base;
`skipUnless` on the real `batch_contract.py`).

| requirement | test |
|---|---|
| one durable document, one Batch ID, nothing temporary left behind | `test_a_registration_round_trips_through_one_file`, `test_the_write_is_one_step_and_leaves_no_temporary_behind` |
| re-registering the same dispatch writes once; a different one is refused | `test_saving_the_same_batch_twice_writes_once`, `test_a_second_registration_of_one_batch_may_not_change_a_member` |
| the Batch ID is path-safe | `test_a_batch_id_cannot_escape_or_leave_its_directory` |
| the ten columns are separate and none stands in for another | `test_every_identity_field_keeps_its_own_column`, `test_the_run_id_is_the_watch_ref_and_the_delivery_id_is_not`, `test_a_run_id_that_is_also_a_delivery_id_is_refused`, `test_a_session_ref_may_not_stand_in_for_an_id_either`, `test_the_provider_is_the_agent_runtime_and_the_source_is_the_reader` |
| an ACK is not a start | `test_an_ack_with_no_run_is_registered_but_not_watchable`, `test_ack_only_is_delivery_not_execution` |
| a queued task needs a deadline | `test_a_queued_member_without_a_deadline_is_refused` |
| a Run that appears later is adopted once, never twice | `test_a_queued_member_learns_its_run_without_a_second_registration`, `test_an_issued_run_can_never_be_amended_to_a_different_one`, `test_amending_an_unknown_task_or_the_same_values_is_not_a_write` |
| the commander contract is the only way in | `test_the_commander_validates_the_plan_before_anything_is_written`, `test_a_missing_commander_contract_is_refused_not_skipped` |
| an ACK cannot make a member verified | `test_the_ack_verdict_cannot_make_a_member_verified` — all `PENDING`, and the envelope has no verdict column |
| plan and ACK must agree | `test_an_ack_that_names_another_session_is_not_this_task_s_ack`, `test_an_ack_without_a_delivery_id_is_incomplete`, `test_a_missing_ack_or_an_unknown_ack_is_refused` |
| the file records which plan it came from | `test_the_registration_records_which_plan_it_validated` |
| a filed batch says so; a hand-built one does not | `test_a_hand_built_batch_carries_no_origin_and_a_registered_one_does` |
| the live `#456` contract | `test_a_plan_the_live_contract_accepts_becomes_a_registration` (skipped: `#456` is not on this base) |

`tests/test_fleet_v1_milestone_batch_integration.py` — 13 tests, CODE + UNIT.
Composition: real `#428` observer, real `#423` ledger, scripted sources,
`FakeLark` provider, sealed notifier stub.

| requirement | test |
|---|---|
| registration → `watches()` → observer → one summary | `test_one_registered_dispatch_is_one_summary_and_three_milestones` (3 milestones + 1 summary, no more) |
| delivery receipt | `test_every_batch_message_carries_a_provider_receipt` (provider `remote_id`, ≤50-char transport key) |
| restart dedupe with a reloaded registration | `test_a_restart_replays_the_receipt_and_sends_nothing` |
| queued is neither success nor silence | `test_a_queued_task_is_not_counted_as_done`, `test_a_queued_task_that_starts_is_read_from_then_on` |
| unobservable member → one actionable notice at the deadline, not a stream | `test_a_queued_task_that_never_starts_is_one_actionable_notice` |
| a late Run cannot rescue a settled member | `test_a_late_run_cannot_rescue_a_member_the_owner_was_told_about` |
| a RED is not a second alert | `test_a_failed_member_is_a_milestone_and_not_a_second_alert` |
| mixed failure | `test_a_mixed_batch_names_every_member_and_alerts_only_the_silent` |
| message budget + suppressed line | `test_the_alert_budget_is_finite_and_the_summary_carries_the_rest` (12 → 10 alerts + `另有 2 项`) |
| the seam, and its BLOCKED state | `test_the_real_notifier_without_the_seam_is_refused_up_front`, `test_the_sealed_stub_refuses_a_private_reach_through`, `test_the_production_modules_never_reach_into_a_private_member` |

Mutation-checked for this change (each turns exactly its own test red, then the
file is restored byte-identically):

| mutation | red |
|---|---|
| `MAX_ALERTS = 10` → `99` | the budget test |
| `UNOBSERVED` → `OFFLINE` at the deadline | the unobservable-notice test |
| the `TOO_LATE` guard → `False` | the late-Run test |
| `covered=` → `covered=True` | the mixed-batch test |
| `Receipt.confirmed` → `False` | the receipt test |
| `receipts` → `()` | the receipt test |
| the stub's seal → `False` | the sealed-stub test |

Totals on this branch: `64 passed, 1 skipped, 10 subtests passed` across the
three files, in ~8 s, on Windows, targeted (never the full suite — the two
pre-existing Windows baselines and the `#449` build failure are not this lane's
and are not counted here).

**Not run, not claimed:** `REAL_DEVICE`, `CLIENT_E2E`, `REAL_FEISHU_DELIVERY`,
`HOSTED_CI` for this branch. No network, no CLI, no credential, no service, no
model, no real Feishu send. Sending a test IM would need a separate owner
decision.
