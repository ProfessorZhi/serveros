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
| a second state file | a verdict is recomputed from the report; what was already sent is the ledger's answer. The process persists nothing, so deleting it cannot resend. |
| a reporting system | two message templates, both deterministic, no model call. |

## The contract

```
Batch(batch_id, members=[BatchMember(source, ref, device_ref, boot_id|source_sha, role)], expires_at)
  └─ .watches()      -> the #428 Watch list, derived — never a second list
  └─ .summary_key    -> "batch:<batch_id>:summary"      one key for the whole batch
  └─ .alert_key(...)  -> "batch:<batch_id>:alert:<source:ref>:<VERDICT>"

report = MilestoneObserver.run_once()          # #428, unchanged
tick   = BatchAggregator(batch, notifier=…).absorb(report)
```

`BatchMember.__post_init__` validates itself by constructing a `#428` `Watch`,
so a member can never be something the tick would refuse to start with. A
`Batch` refuses two members that name the same `source:ref`, for the same reason
#428 refuses two watches for it: one tick cannot hold two facts about one ref.

`absorb()` never raises for a row. A row it cannot interpret settles nothing and
leaves the batch open; the batch deadline, not a guess, is what ends that wait.

## The verdict table

One #428 row settles one member. The order is the contract.

| row (`state`, `outcome`) | verdict | message raised by the batch |
|---|---|---|
| `NO_EVENT`, `SKIPPED_BACKOFF`, `RATE_LIMITED` | `PENDING` | none — the tick established nothing |
| `REF_MISMATCH` / `DEVICE_SCOPE_MISMATCH` / `BOOT_SCOPE_MISMATCH` / `SOURCE_SHA_MISMATCH` | `UNKNOWN` | **alert** |
| `NOT_QUALIFYING` | `SKIPPED` | none — the caller declared it non-qualifying |
| `EXPIRED`, or the batch deadline passed | `OFFLINE` | **alert** |
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

## The message budget

For one dispatch:

* **exactly one** summary, under one key, once (`batch:…:summary`);
* **one** alert per member that settled `FAILED`/`UNKNOWN`/`OFFLINE` **and that
  #423 never spoke about** (its row names no delivery key and it was not
  delivered).

The second clause is the anti-noise rule. #423 already delivers a `RED` and an
`UNKNOWN` as their own milestone messages, as they happen; a batch alert for
those would be the same fact twice. The members that would otherwise stay silent
for ever are the ones the tick could not observe at all — a scope mismatch, a
permanent refusal, an expired window, a batch deadline. Those get the alert. The
total is `1 + (members nothing else could speak about)`, and a test pins both
halves.

An unconfirmed send is **reconciled under the same transport key**, never resent
under a new one: that is #423's own window and #423's own rule.

## The one exact patch this needs from #423

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

**Not run, not claimed:** `REAL_DEVICE`, `CLIENT_E2E`, `REAL_FEISHU_DELIVERY`,
`HOSTED_CI` for this branch. No network, no CLI, no credential, no service, no
model, no real Feishu send. Sending a test IM would need a separate owner
decision.
