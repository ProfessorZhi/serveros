# Agent Monitor — independent QA readiness

- Item: **#478** (owner supersession) · **#424** (long-run observer) · RC **#425** · campaign **#434**
- Reviewer: Dell / `fjd` (uid 1000), independent QA — not an author of the monitor
- Date: 2026-10-28
- Stack reviewed: #423 `d9b2948a6260` · #426 `6781b3aa23d5` · #428 `dde957775cb5` · #446 `9b1b1ad1b6f6` · #464 `d72befe77172` · #470 `56fcb9276acc` · #472 `0c588896a7b4` · #417 · #424 · #478
- This document is **not** a V1 sign-off and does not sign the old Fleet off either.

## Headline: `BLOCKED_UPSTREAM_SOURCE`

`integrations/fleet-next-agent-monitor/` **does not exist**:

| where checked | result |
|---|---|
| `main` tree (`git/trees/main?recursive=1`) | 0 matches |
| #423, #426, #428, #446, #464, #470, #472 — all files, paginated | 0 matches each |
| any local checkout under `/home/fjd` | absent |

The monitor's own behaviour therefore **cannot be exercised**. This review does not invent a stub to make "monitor QA" green: a stub certifies the shape of the test rather than the shape of the product, and the two are indistinguishable to the next reader. The monitor tests are `skipUnless(‹source exists›)` with a named reason, so they read as **not run**, never as passed.

Second, separate block: **`BLOCKED_ENTRY`**. `integrations/fleet-v1-milestone-batch/dispatch.py` (on #470 `56fcb927acc`) has **no entry point** — no `argparse`, no `def main(`, no `__main__`, no `sys.argv`. It is a library (`tick_once`, `tick_from_file`, `Ticker`, `runner_document`). Nothing on this train can be scheduled, and **no Tokyo timer may be claimed**.

## What IS verified (landed code, real fixtures, injected provider, mock clock)

### Acceptance 1 — old Fleet not taken over
Neither `milestone_notify.py` nor `observer.py` imports `fleet_mcp` (checked by scanning both for `import fleet_mcp` / `from fleet_mcp`). `fleet_mcp/fleet.py` still exposes `get_run` and `get_session`, and `registry.py` is present. The notifier names `message_transport` — it reuses the existing sender rather than adding one.

*Limit:* this proves the monitor train does not reach into the control plane. It cannot prove old-Fleet `get_session`/`get_run`/authorised `send` still answer **after** a Next shadow start/fail/withdraw, because the shadow itself has no source. That half is `NOT_RUN` with the same block.

### Acceptance 2 — queued / RUN_NOT_FOUND / silent completion are never GREEN
Measured through `normalize()` + `decide()` on the authors' own fixtures:

| input | outcome |
|---|---|
| `completed-no-output.json` (gates: native-output missing) | **UNKNOWN**, `severity=ACTION`, `deliver=True` |
| `running.json` | **SILENT** (`RUN_NOT_TERMINAL`) |
| `running.json` with `status=queued` | not GREEN, not delivered |
| `failed.json` | **RED** |
| `completed.json` (all gates evidenced) | **VERIFIED_GREEN**, delivered |

Note the deliberate asymmetry: an unverified completion is still *delivered*, as an `ACTION` escalation whose text says "Do not treat this as success." Not-green is not the same as silent, and asserting "silent" here would have hidden real behaviour behind a passing test.

Identity separation is explicit in the source: `Watch` documents that `ref` is the native Run id, and that "#416 established that a deliveryId can answer RUN_NOT_FOUND while the runId still reads". A cloudcli watch must name its `boot_id`; a CI watch must name its `source_sha`; a payload answering for another device/boot/commit settles `UNKNOWN` before classification.

### Acceptance 3 — one delivery per key across interpreters
`delivery_key_for(decision)` is identical in two fresh `python -I` interpreters reading the same fixture. The ledger treats `DELIVERED` as final, and the transport passes `idempotency_key[:50]`.

A **real tick** was also driven: injected `FakeSource` returning `completed.json`, the real `MilestoneNotifier` with a temp ledger and a mock clock, and the real `MilestoneObserver` with a temp state file. Two consecutive `run_once()` calls produce one decision and **at most one** provider send, with no repeated key.

*Limit:* this is two in-process ticks plus cross-process key determinism. It is not the three-process scenario in the acceptance text (GPT exits → interpreter 2 → interpreter 3 on the same batch), because the batch dispatcher has no entry point to run twice.

### Acceptance 4 — RED vs VERIFIED_GREEN, and the observer's own health
`action_required` → **RED** with `action_required=True`; `green.json` → VERIFIED_GREEN only when its gates are evidenced, otherwise UNKNOWN. The observer publishes its own health in `run_once()`: `{schema, observed_at, reads, counts, rows}`, with per-entry `last_read_at`, `next_attempt_at`, `last_code`, and a `state_error` when the state file cannot be written. `reads` is a **per-source counter**, not a scalar.

### Acceptance 5 — no second bus, sender, resident LLM or remediation
Read from the **AST**, not the prose: neither module constructs a `Thread`, `Popen`, `Process`, `Timer` or `create_task`. A `while True` exists at `observer.py:623`, but it is inside the generator `run_periodic`, which yields, is bounded by `max_ticks`, takes `sleep` as a parameter, and whose docstring says the caller owns the loop — the rule refuses only a loop that actually spins on its own. Neither module defines or calls anything named `restart`/`remediate`/`recover`.

This is why the check is structural: a text search for `while True` matches the module's own docstring, which says it is *not* an event bus. Grepping prose produced exactly that false positive during this review.

## Test evidence

`tests/test_fleet_next_agentmonitor_readiness_qa.py` — **20 tests: 17 pass, 3 skipped** (2 × `BLOCKED_UPSTREAM_SOURCE`, 1 × `BLOCKED_ENTRY`). Run with `python3 -m unittest`; this host has no pytest.

## Status

| layer | state |
|---|---|
| CODE (landed notifier/observer properties) | **verified** |
| UNIT | **17 pass / 3 skip**, skips named |
| CI | Draft PR, see the PR checks |
| REAL_DEVICE (monitor running on Dell) | **NOT_RUN** — `BLOCKED_UPSTREAM_SOURCE` |
| CLIENT_E2E (GPT → monitor → GPT) | **NOT_RUN** — same block, plus no Live send was performed |
| Timer installed | **NOT CLAIMED** — `BLOCKED_ENTRY` |

## What this review did not do

No real CloudCLI send or stop, no systemd change, no Feishu delivery, no Plugin Creator, no OAuth or credential use, no GPU or model call, no `main` merge, and no writes to the monitor source directory, to #423/#426/#428/#446/#464/#470/#472, or to any other author's files.
