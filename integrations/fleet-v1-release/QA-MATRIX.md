# Fleet V1 — independent QA acceptance matrix

Status date: 2026-10-17. Every row is a **claim about evidence**, not a claim
about intent. A row may only move to PASS when a receipt exists at or above its
required evidence level.

## Evidence levels

| Level | Means | Does **not** mean |
|---|---|---|
| `SOURCE` | The code or contract was read at a pinned SHA | it compiles, runs, or is installed |
| `MOCK` | A fake/simulated dependency produced the behaviour | a real provider or device was reached |
| `LOCAL` | A real process ran on this host at a pinned SHA, with runner, command, environment and exit code recorded | a device, client or production path works |
| `REAL_DEVICE` | A named device performed the action and produced a readback | the client-facing path works end to end |
| `CLIENT_E2E` | A real authenticated client discovered and invoked the surface | production release or ChatGPT packaging |

A higher level never substitutes for a different axis. `LOCAL` cannot satisfy a
`REAL_DEVICE` row; `REAL_DEVICE` cannot satisfy `CLIENT_E2E`.

## Owners

- **Integrator**: Tokyo Integration Owner Session. Owns this manifest, the
  conflict audit and the RC composition. Must not sign QA.
- **Independent QA**: **`INDEPENDENT_QA_PARTIAL`** — not `SIGNED`. Session
  `device:tokyo-shared-dev-01|claude|6955184a-f580-43cb-8ad4-2019980fbc02`,
  Run `8384fcda-97c0-4d38-820d-4778f8ce5c8d`; triage artifact is **PR #256**
  at `2c4fe87b5776202b19b4cdaeb682bbaa048b2a55` (Snapshot B; the earlier
  `a4d7131` was superseded). It is now rechecking **`fdc223f`** on its own
  independent run — that recheck is **PENDING**. It owns every PASS in this
  matrix and the `signature` field. A partial triage is **not** a PASS and
  **not** a signature.
- **Lane owners** (`S1`, `S2`, `F1`, `N1`, `N2`, `P1`, `M1`, `G1`, `R1`): own
  their source and their local receipts. The integrator does not re-run or
  re-sign them.

`qa.status` is `INDEPENDENT_QA_PARTIAL` with `signature: null`. The checker
refuses `COMPLETE_V1` unless `qa.status == SIGNED` (`E018`), the signature is
present (`E014`, `E016`), and an independent `qa_triage` object is `SIGNED`
(`E128`, `E129`).

## Independent QA findings (P0, not closed)

Snapshot B re-audited `f2b89e1` / `4a5d0a0` / `619cf63`. S2 has since advanced
to **`fdc223f`**, which changes the picture at source but has **no independent
receipt yet**.

| ID | Sev | Candidate | Finding | State |
|---|---|---|---|---|
| QA-P0-1 | P0 | #252 | `intent_digest` omitted `session_ref`/`agent_runtime`, so a same-key send to another session/runtime was a replay | **FIXED_PENDING_RECHECK** — `fdc223f` adds session_ref, agent_runtime and principal to the digest; no independent run at that head |
| QA-P0-2 | P0 | #252 | Caller `phase`/`principal` could write SETTLED; router records omitted `principal` | **FIXED_PENDING_RECHECK** — `fdc223f` resolves principal from a fresh trusted `context_ref`, refuses a forged principal and an unattributed phase; no independent run |
| QA-P0-3 | P0 | #243 | `test_no_node_paths_in_product_data.py` failed at `494f452` on an absolute home literal | **CLOSED (LOCAL)** — full guard re-run at `4a5d0a0`: 10 tests OK, exit 0 |
| QA-P1-1 | P1 | #255 | Present-but-null is read as affirmative: `{"health": null, "policy": null}` → `READY/ALLOWED` with a non-null `effective` | **OPEN** — uncorrected at `619cf63`; the G1 owner dispatch is platform-blocked |
| QA-P2-1 | P2 | #252 | `_same_writer` matches an empty/partial writer record | **OPEN** |
| QA-P1-2 | P1 | #254 | Manifest pinned stale heads and omitted new candidates | **CLOSED** — fixed in manifest `-d` |
| QA-P2-2 | P2 | #254 | `FLEET_NEXT_33_6` requires `REAL_DEVICE` while its rows are `SOURCE` | **OPEN by design** — a gate may require more than it has and then reports BLOCKED; it is not a near-PASS |

A claimed fix is not a closed finding. QA-P0-1 and QA-P0-2 move only when the
independent session reproduces the negative tests at `fdc223f` and records a
receipt.

## P0 Work/effect safety — issue #237

| ID | Required test | Level | Candidate | Observed | Verdict |
|---|---|---|---|---|---|
| T1 | Hand off A→B, leave A alive, stale A writes | LOCAL | #241 | lane-reported closed; integrator has no receipt | NOT_RUN |
| T2 | A and B race the same write scope | LOCAL | #241 | lane-reported closed | NOT_RUN |
| T3 | Expired/revoked grant or lease during a long turn | LOCAL | #241 + WE#13 | lane-reported closed | NOT_RUN |
| T4 | Duplicate delegation after unknown HTTP result + router restart | LOCAL | #252 `fdc223f` | QA-P0-2 claimed fixed at this head; no independent receipt | NOT_RUN |
| T5 | Same idempotency key, modified payload | LOCAL | #252 `fdc223f` | QA-P0-1 claimed fixed (digest now carries session/runtime/principal); no independent receipt | NOT_RUN |
| T6 | Work Engine lease expired but still in DB | LOCAL | WE#13 + #241 | lane-reported closed (`held_leases` filters `expires_at`) | NOT_RUN |
| T7 | Cross-user / cross-runtime Session ref and stale grant | LOCAL | #241 | lane-reported **half** | NOT_RUN |
| T8 | Real Tokyo → Dell session send / get_run under grant | REAL_DEVICE | #241 + Fleet | no trusted transport observed | **BLOCKED** |
| T9 | Provider unreachable / partial dispatch | LOCAL | #252 | lane-reported `EFFECT_UNKNOWN` path; no receipt here | NOT_RUN |
| T10 | Child self-reports success while tests fail | LOCAL | none | no candidate owns this row | **BLOCKED** |
| T11 | Local bounded recovery during control-plane disconnection | LOCAL | #242 | source only; no local run | **BLOCKED** |
| T12 | Tokyo loss, Dell continues bounded work, reconnect | REAL_DEVICE | #241 + WE#13 | requires T8 transport | **BLOCKED** |

Rows T10 and T11 have **no named candidate**. That is a coverage gap, not a
scheduling detail: a matrix with an unowned row cannot pass as a whole.

## Fleet Next 33/6

| Item | Level | Observed |
|---|---|---|
| Catalogue size | SOURCE | **33 is a catalogue**: 9 authored Fleet + 22 CloudCLI native mirror + 2 capability reads |
| Scopes | SOURCE | six: read, send, create, control, approve, navigate |
| Authored tools actually served | SOURCE | **9** on #243 (`fleet-mcp-4`); the release-plan doc records the live surface as 7 → 9 |
| Installed revision | REAL_DEVICE | installed canary is `fleet-mcp-20261016@008a336` (A line) → **none of #243 is installed** |
| Denied-grant negative tests | SOURCE | present as tests; not executed here |

Reading "33" as "33 tools served by this candidate" is the specific mistake this
row exists to prevent.

## Local Claude Code plugin — #230

| Item | Level | Observed |
|---|---|---|
| Plugin generated from the one Skills source | SOURCE | yes; Skills bytes pinned by test |
| MCP `tools/list` from an authenticated client | CLIENT_E2E | **BLOCKED_CLIENT_E2E** — gate is owner consent |
| Supported workflow through the client | CLIENT_E2E | not attempted |

## C++ Core — Linux and Windows

| Item | Level | Candidate | Observed |
|---|---|---|---|
| Linux core builds and reads CPU/RAM/disk/PSI | LOCAL | #242 | source only |
| Linux GPU readback | REAL_DEVICE | #242 lineage (#228) | source only |
| Bounded self-healing on a real host | REAL_DEVICE | #242 | toy proves mechanism only |
| Windows SCM status source | SOURCE | **#253** (new) | `native/node-core/windows-core/**`, one commit above #242 |
| Windows build / SCM probe / run | REAL_DEVICE | #253 | **none** |

`#253` materially changes the picture from the previous audit: Windows Core is
no longer "no candidate", it is "source only". It is still not a Windows result.

## Device Abilities — G1

| Item | Level | Observed |
|---|---|---|
| Canonical registry pinned | SOURCE | ability-management `27df5748`, `abilities-manifest.yaml`, `schema_version: 3` |
| Effective-ability projection source | SOURCE | **#255 `619cf63`**, child of #243; adds `integrations/device-ability-projection/**` + one test |
| Projection disagrees with the resolver by being narrower | LOCAL | not run here |
| Ability bound and executed on a device | REAL_DEVICE | **BLOCKED** — #255 sits on an unlanded F1, and no device executed anything |

Two facts that must not be merged: #255 is a **source** candidate and a **child**
of #243. A re-base of #243 changes #255's base and forces a G1 re-pin. And a
pinned registry SHA is a locator — not an installation, readiness or execution
receipt.

## Version drift guard

The manifest records one `drift_guard.snapshot_time` and one `observed_at` per
candidate. Every pin must be re-read from the provider API:

- **before push** of a manifest change,
- **before composition** of any lane,
- **before the QA signature**.

A candidate whose `observed_at` differs from the snapshot earns `W030`; a
`COMPLETE_V1` resting on stale pins is rejected (`E115`). The point is that
`#252` moved `00bb701 → d49342b → dac3088` during this campaign, and `#230`'s own
body still names a head two commits behind its branch. A pin that is not
re-read is a guess.

## Minimal source-composition order

Order is forced by ancestry and by the parent/child relation, not by lane
importance. Only step 1 is immediately landable.

| Step | Composition | Why here |
|---|---|---|
| 1 | serveros **#241** then work-engine **#13** | only tower based on current `main` (`behind_by: 0`); cross-repo pair |
| 2 | **#242** (N1) | 11-link chain, file-disjoint from every other lane |
| 3 | **#253** (N2) | one commit above #242; inherits its CMake layout |
| 4 | **#243** (F1) | 14-link chain; must precede G1 |
| 5 | **#255** (G1) | child of #243; re-pin after any F1 re-base |
| 6 | **#257** (R1) | base `main`, file-disjoint; deps are the F1/S1 contracts |
| 7 | **#230** (P1) | sibling of #243; depends on F1/R1/S1 contracts per the plan |
| 8 | **#233** (M1) | consumes F1 readback |

Steps 2–3 and 4 are file-disjoint, so they may be prepared in parallel, but
each must still be landed by its **owner**, never by I1.

## R1 runtime — source candidate, still not a receipt

R1 materialized as **PR #257 `c2e2e8d`** (base `main`): a pure CC/Codex runtime
parity projection under `integrations/fleet-v1-runtime-parity/**` plus one test.
The lane reports 34 local fixture tests. That is `SOURCE`/offline evidence —
**not** a provider, session or device result. It is recorded in the manifest as
a dependency opportunity with `is_release_receipt: false`, and the checker
rejects promoting it (`E132`).

`RUNTIME_CC_CODEX_CLOUDCLI` stays `BLOCKED_PROVIDER_SOURCE` until an ambiguous
effect can be resolved by the provider's own native turn id.

## Work in flight — accepted runs with no artifact

Two Tokyo independent Owner runs were accepted on 2026-10-18. Neither has a PR
or a SHA, so neither is a candidate, evidence or landable:

| Lane | Owner session | Run | Status |
|---|---|---|---|
| N3 network-source | `1b485a39-f480-4c67-9c75-64473adad088` | `f1d6172d-d6e0-4898-bc10-2a5c2913aab4` | WORK_IN_FLIGHT |
| T10 test-evidence | `cce896af-43e2-4c15-81f3-30b6a0f5f8f1` | `1f529276-42b1-47fd-8946-4dd6f04bf2ee` | WORK_IN_FLIGHT |

An accepted run is not an artifact, and an artifact is not evidence. The checker
enforces this as `E143`–`E147`.

## Smallest next integration slice

1. Independent QA re-runs its triage against **this** snapshot — #252 `fdc223f`,
   #243 `4a5d0a0`, #257 `c2e2e8d`, #254 `5a07df2` — and signs it, or records a
   superseding one. `INDEPENDENT_QA_PARTIAL` is not a signature, and Snapshot B
   (`f2b89e1`) is not a statement about `fdc223f`.
2. S2's claimed fixes of **QA-P0-1** (digest target/runtime/principal) and
   **QA-P0-2** (trusted `context_ref`, forged-principal refusal, unattributed
   phase refused) are at `fdc223f` but unverified. T5 and T4 stay NOT_RUN until
   the independent receipt exists.
3. F1's guard re-run at `4a5d0a0` is already CLOSED at LOCAL; G1 must still fix
   present-null (QA-P1-1), and its owner dispatch is platform-blocked.
4. Collect **LOCAL** receipts for T1–T7 and T9 from the lane owners at the exact
   SHAs in the manifest (runner, command, environment, exit code, per row).
5. Re-pin every head and base immediately before composition.
6. Land step 1 above: serveros #241 + work-engine #13, in that order.
7. Only then prepare F1 → G1 → P1. Do not merge, cherry-pick or rewrite an owner
   branch inside I1.

## Reviewer actions

- Confirm each `PASS` cites a receipt path and an evidence level.
- Reject any row whose achieved level is below its required level.
- Reject any manifest that sets `source_merge.performed = true` while a gate is
  not PASS — the checker enforces this as `E095`.
- Confirm the QA signature comes from a Session that is not the integrator.
