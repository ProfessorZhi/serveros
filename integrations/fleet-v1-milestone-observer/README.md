# Fleet V1 milestone observer (#424)

The tick that keeps working after the GPT/ChatGPT turn is gone.

This directory is a **runtime/observe composition**, not another delivery
system. It owns one question: *given the refs somebody asked us to watch, what
may be read now, what does a read mean, and may it be delivered?* The answer is
handed to the one Tokyo notifier built by #423
(`integrations/fleet-v1-milestone-notify/`). Nothing here sends, reads a
credential, or decides what a milestone is.

## What it is not

| Not this | Because |
| --- | --- |
| A second ledger | Delivery receipts live in #423's SQLite ledger. The observer's JSON file holds scheduling facts only; deleting it cannot resend, and a test proves it. |
| An event bus / queue / thread | One `run_once` reads due refs in order; the caller owns the loop. |
| A source client | `SourceRead` is injected. The production half is `integrations/fleet-v1-milestone-sources/` (Windows lane): `CloudCliRunSource`, `GitHubActionsSource`. |
| A provider or credential store | `MilestoneNotifier` is injected, already bound to the single Tokyo `HumanControlProvider` (`runtime/direct_lark_notify.py` + `runtime/message_transport.py`). |
| An always-on LLM | The summarizer is #423's optional, once-per-first-delivery callable. The deterministic template is written first, so a model outage cannot suppress a RED. |
| A service | Starting it, rendering a unit and choosing a restart policy is a separate release. This PR ships `run_once` and `run_periodic`, tested with an injected clock and sleep. |

## Where it sits

```
        dispatch (GPT returns immediately)
                  │  caller registers Watch(source, ref, device, boot/sha)
                  ▼
 ┌────────────────────────────┐        ┌──────────────────────────────┐
 │ MilestoneObserver.run_once │  read  │ SourceRead (injected)        │
 │  scope / backoff / bounds  │ ─────► │  cloudcli | github_actions   │
 └─────────────┬──────────────┘        └──────────────────────────────┘
               │ payload (already #423-shaped)
               ▼
 ┌────────────────────────────┐
 │ MilestoneNotifier.observe  │  claim → deliver → receipt (SQLite)
 │  (#423, injected)          │  provider = the ONE Tokyo provider
 └────────────────────────────┘
```

## The watch entry is the dispatch's durable claim

A `Watch` names what would make a fact *about this task*: the native ref, the
device, the boot the dispatch was bound to (CloudCLI, **required** — a watch
without it cannot tell this boot's run from a previous one) and the exact
commit a CI green may qualify (GitHub, required). Those are checked **before** `observe_payload`, so
the classifier cannot turn another ref's green into this watch's milestone:

| Mismatch | Settled as | Delivered? |
| --- | --- | --- |
| payload answers for another run/workflow id | `REF_MISMATCH` | no (terminal) |
| payload is for another device | `DEVICE_SCOPE_MISMATCH` | no (terminal) |
| dispatch bound to a different boot | `BOOT_SCOPE_MISMATCH` | no (terminal) |
| CI ran on another commit | `SOURCE_SHA_MISMATCH` | no (terminal) |
| the caller marked it non-qualifying (Draft PR) | `NOT_QUALIFYING` | no, and not even read |
| run still running / CI queued | `NO_EVENT` | no |
| yellow / ordinary green | `CARD_ONLY` | no |
| completed with no trusted native output | #423 `UNKNOWN` | yes, as ACTION |

The boot check has two halves: the *payload's binding* must equal the watch's
boot (above), and the *run's own* `bootId` must equal that binding — the second
is #423's classifier, which yields `RUN_OUTCOME_UNKNOWN` rather than success.

## Reads, retries and bounds

* **Backoff** for reads that can clear (`RUN_NOT_FOUND` while a run is still
  being indexed, timeouts, transport errors): `base * 2^(attempt-1)`, capped.
* **Terminal** for configuration mismatches and payloads the boundary refuses:
  retrying cannot change them, so the ref is reported once and not re-read.
* **Per tick**: at most `max_reads_per_source` reads of one source, and at
  least `min_interval_seconds` between them (measured from the persisted
  `last_read_at`). The read cursor orders refs least-recently-read first, so a
  budget rotates across a source's refs instead of starving the tail.
* **Window**: `expires_at` turns a stale watch into `EXPIRED` (UNKNOWN), never
  a late green.
* **Errors never become success**: a refused read or a refused milestone is an
  UNKNOWN row; the tick continues for the other refs.

## Restart semantics

`ObserverState` is a small JSON file of `attempts`, `next_attempt_at`,
`last_read_at`, `last_code`, `last_delivery_key`, `terminal`. It is written
through `runtime/ability_projection.write_atomic` — the atomic writer whose
`os.replace`/`unlink` sites are already declared in
`runtime/actuator-contract.yaml`. No new mutation verb is introduced.

The **ledger** decides what was delivered. On restart, or with the state file
deleted, the observer reads again and #423 answers `DUPLICATE` /
`UNKNOWN_REPLAY` without sending a new message.

A delivery left `DELIVERY_UNKNOWN`/`IN_FLIGHT` is recovered by the tick itself:
on a later tick the observer re-asks the **same delivery key** through #423's
`reconcile`, inside the transport's dedupe window. That is not a resend — the
transport deduplicates the key — and outside the window #423 refuses, so a
recovery can never become a second message. `Options(reconcile_unknown=False)`
turns the automatic retry off.

A watch may be redefined without changing its ref (a corrected boot, a longer
window, a Draft moved to ready, a new source SHA). The persisted scheduling
entry carries a scope fingerprint; a changed fingerprint clears the previous
`terminal` state so the corrected watch is observed. The ledger is untouched.

## Integration handoff (no dual-write)

```
SourceRead.read(source, ref) -> Mapping   # #423's published seam
```

The production adapters are the Windows lane's
`integrations/fleet-v1-milestone-sources/milestone_sources.py` (branch
`feat/fleet-v1-milestone-sources-20261027`). They return exactly the envelopes
#423's `normalize*` accepts and raise a typed `SourceRefused(code)`; this module
reads `.code` by duck-typing and never imports that file, so the two can land in
either order. The observer adds no field to the payload and no second read
path.

The provider injection point is #423's `MilestoneNotifier`. Production wiring
constructs it once with the Tokyo provider and the receipt ledger path; this
module neither constructs nor imports a provider.

## Test matrix (all offline)

`tests/test_fleet_v1_milestone_observer.py` — 31 tests, scripted `SourceRead`,
`FakeLark`, fake clock:

* GPT-gone end-to-end: running → terminal after the chat closed → one green;
  restart → `DUPLICATE`; run already terminal before the observer started.
* Two replicas / duplicate callbacks share one receipt (one DELIVERED, one
  DUPLICATE).
* Deleting the state file does not resend (the ledger decides).
* Backoff, per-tick read bound, min interval, expiry → UNKNOWN.
* Boot scope (both halves), ref mismatch, `RUN_NOT_FOUND`/TTL.
* `completed` without native output → UNKNOWN; a later read with output is a
  correction delivered once under a new key.
* CI SHA lock, Draft/non-qualifying watch (not read), yellow and ordinary green
  silent, `action_required` RED.
* Provider failure → `DELIVERY_UNKNOWN`; replay does not send a new message;
  the next tick recovers the same key in-window; out-of-window refuses;
  UNSUPPORTED capability never becomes DELIVERED.
* Boot required for a CloudCLI watch; a permission refusal is terminal; a
  redefined watch clears its previous terminal; corrupt scheduling state does
  not wedge the tick; zero ticks yields nothing.
* RED template survives a raising summarizer.
* Static: no IO client imports, no notifier/ledger construction in this file.

Run:

```bash
python3 -m unittest -v tests.test_fleet_v1_milestone_observer
python3 -m unittest -v tests.test_fleet_v1_milestone_notify   # the stack base
```

## Evidence levels

| Level | State |
| --- | --- |
| CODE | this directory |
| UNIT | the 31 tests above (and #423's 31 on the base) |
| HOSTED_CI | the stacked PR's workflow |
| REAL_DEVICE | **NOT_RUN** |
| CLIENT_E2E / REAL_FEISHU_DELIVERY | **NOT_RUN** — `FakeLark` only; no CLI, socket, credential or owner approval |

## Future launch / restart / rollback recipe (NOT executed)

A separate release may run one tick per minute on the Tokyo hub, under the
operator's user systemd — **not** inside the device C++ `serverosd`, and with no
resident LLM:

```ini
# ~/.config/systemd/user/serveros-milestone-observer.service   (NOT installed)
[Unit]
Description=ServerOS Fleet milestone observer (one tick)
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/serveros@<pinned-sha>
Environment=SERVEROS_OBSERVER_STATE=%S/serveros/milestone-observer/state.json
Environment=SERVEROS_MILESTONE_LEDGER=%S/serveros/milestone-deliveries.sqlite
ExecStart=/usr/bin/python3 /opt/serveros@<pinned-sha>/integrations/fleet-v1-milestone-observer/tick.py
```

```ini
# ~/.config/systemd/user/serveros-milestone-observer.timer   (NOT installed)
[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
Persistent=true
```

The tiny `tick.py` named above is the **next** release's wiring: construct
`CloudCliRunSource`/`GitHubActionsSource` from `milestone_sources`, one
`MilestoneNotifier` with the existing Tokyo provider, and the watches owned by
the dispatcher. It is deliberately absent here so this PR cannot imply a
production path that was not reviewed.

* **Restart**: the timer runs a fresh process; state and ledger are on disk; a
  crash mid-send leaves `IN_FLIGHT`, which is UNKNOWN (no blind resend).
* **Rollback**: point `WorkingDirectory`/`ExecStart` at the previous pinned SHA;
  the observer state is plain JSON and the receipt ledger is #423's
  additive SQLite schema, so the previous tick reads the same files. Keep both
  SHAs checked out until the previous tick has run once.
* **No coupling**: the device `serverosd` never imports this module; a Tokyo
  outage stops notifications, not node health/recovery.
* **Approval boundary**: installing the units, choosing the recipient, and the
  first real send are owner actions, evidenced separately. This PR stops here.
