# Agent Monitor — cross-process QA (#481 Batch CLI × #482 activation)

- Items **#478 / #424** · RC **#425** · campaign **#434**
- Reviewer: Dell / `fjd` (uid 1000), **not** an author of the monitor, the batch, or the wrapper
- Sources: **#481** `9b7710a26180e1e6d00ce597827d447d0d2348b7` · **#482** `0f581e722c81a20152d23e81ecb666869c4fb3d5`
- Stack reused unchanged: #423 `d9b2948a6260` · #428 `dde957775cb5` · #446 · #464 · #470 `56fcb9276acc`

## Two blocks, named rather than papered over

**`BLOCKED_UPSTREAM_SOURCE`** — `integrations/fleet-next-agent-monitor/` (the monitor
core) is still absent: not in `main`, not in #481 or #482, not in this tree. #482
ships `fleet-next-agent-monitor-activation/`, which is a **wrapper**, and a wrapper
is not the monitor.

**`BLOCKED_RUNTIME_ENTRY`** — the wrapper takes the monitor as an **injected**
`--monitor-entry module:callable` and **names the Batch CLI nowhere**. With no
monitor entry on this tree there is nothing to activate, so "the wrapper is wired
to #481" cannot be shown. The wrapper's own source is honest about this: an
unresolvable entry is refused rather than pretended.

Neither block is a stub. Both are `skipUnless`/`skipTest` with the reason attached,
so they report as **not run**.

## What #481 does resolve: `BLOCKED_ENTRY` is gone for Batch

`integrations/fleet-v1-milestone-batch/cli.py` is a real entry point: `argparse`,
`--registration --ledger --state [--lock] --provider-factory --source-factory`, and a
closed outcome set — `BLOCKED_INPUT` (3), `SETTLED` (0), `UNCONFIRMED` (4),
`TICKED` (0), `REFUSED` (2). One invocation is one tick and it exits.

## The four-process scenario, executed for real

Every process below is a separate `python3` start of the authors' own `cli.py`,
wired to the authors' own offline fakes (`file_lark`, `scripted_source`). No module
under test is re-implemented, and no fake stands in for one.

| Process | What it did | Measured |
|---|---|---|
| **A** file then exit | wrote a registration with one queued task (no Run), then stopped | file survives the process; `ref is None`; an `amendments` field is present |
| **B** fresh interpreter, all queued | ran the CLI | **`BLOCKED_INPUT`, exit 3**; `counts {PENDING:1}`; `watches {declared:1, issued:0}`; `summary: null`; member `PENDING`/`NEVER_ISSUED`, `run_id: null`; **journal 0 lines — nothing sent** |
| **C** amend, then a third interpreter | `registration.amend(...)` bound `run_qa_1`, then the CLI ran | **`SETTLED`, exit 0**; `counts {VERIFIED:1}`; member `VERIFIED_GREEN` with `run_id: run_qa_1` and `delivery_id: delivery:qa-1` kept distinct; summary `DELIVERED`, `delivery_key batch:batch-qa-1:summary`; **exactly two sends** |
| **D** fourth interpreter, same batch | ran the CLI again | **`SETTLED`**, summary state **`DUPLICATE`**, **same `remote_id`**, **journal unchanged**, member `why` says "already delivered; a replay does not resend" |

The two sends in C are not a double-delivery: the journal shows one per-member
milestone (`om_1`, a `RUN_COMPLETED` card) and one batch summary (`om_2`,
`批次 batch-qa-1 结束 …`), under two different keys. This was checked by reading the
journal rather than by counting it — a count of 2 looks identical to a resend.

## Inputs that must never be green — all measured not-green

| input | result |
|---|---|
| `RUN_NOT_FOUND` | member not `VERIFIED_GREEN`; `summary: null`; **zero sends** |
| completed **without native output** | `verdict: UNKNOWN`; **`counts` carries no `VERIFIED`** |
| `NETWORK` loss | not `VERIFIED_GREEN`; batch not green |
| completion under **another boot** | not `VERIFIED_GREEN` — another boot's completion is not this watch's milestone |

Note on `settled`: it means "every member reached a **terminal fact**", not "the
batch succeeded" — an UNKNOWN completion is terminal, so `settled` is `true` there.
The non-success signal is `counts`, and the suite asserts that instead of the
friendlier field. Asserting `settled == false` here was a mistake this review
made and corrected.

## Shadow writes only what it owns

- a tick does **not** mutate the registration (byte-identical before/after);
- ledger and state are **separate files** — one file cannot answer both questions;
- a second writer holding the lock is **refused**, and the refusal names the lock.

## Registration discipline the QA had to learn

- a queued task **requires a batch deadline**: `NO_DEADLINE` is refused at filing —
  an unissued task with no deadline is an unbounded wait;
- `save()` is write-once per Batch ID: the same document is `UNCHANGED`, a
  **different** member set is `CONFLICTING_REGISTRATION`, because a Batch ID names
  one dispatch and its delivery keys derive from it;
- an amendment goes through `registration.amend()`, not `save()`.

## Not verified

- the monitor core and its activation: **both blocked**, see above;
- `--summarizer-factory` (an optional model call) was **not** exercised — no paid
  model was invoked;
- `UNOBSERVED` alert caps, monitor `last_tick`/`backlog`/stale-source surfacing:
  **NOT_RUN**, they belong to the monitor core that does not exist;
- old-Fleet `get_session`/`get_run`/authorised `send` staying alive across a Next
  shadow start/fail/withdraw: **NOT_RUN** — there is no shadow to start.

## Status

| layer | state |
|---|---|
| UNIT | **16 tests: 14 pass, 2 skipped by name** |
| CODE | verified for #481's CLI; #482 wrapper present but unwired |
| CI | Draft PR |
| REAL_DEVICE | **NOT_RUN** |
| CLIENT_E2E | **NOT_RUN** |

No `systemctl`, no real CloudCLI send/stop, no Feishu, no Creator, no OAuth or
token, no paid model, no GPU, no `main` merge, and no edit to #481/#482, the
monitor core, any other author's PR, or the old Fleet's production configuration.
