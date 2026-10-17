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
- **Independent QA**: assigned but **PENDING**. Session
  `device:tokyo-shared-dev-01|claude|6955184a-f580-43cb-8ad4-2019980fbc02`,
  Run `8384fcda-97c0-4d38-820d-4778f8ce5c8d`. It owns every PASS in this matrix
  and the `signature` field. Until it produces receipts it is **PENDING** — not
  a PASS, not an independent signature, and not a reason to advance a gate.
- **Lane owners** (`S1`, `S2`, `F1`, `N1`, `N2`, `P1`, `M1`, `G1`, `R1`): own
  their source and their local receipts. The integrator does not re-run or
  re-sign them.

`qa.independent_of_integrator` is `true` because the assigned session is a
different session, and `qa.status` is `PENDING` with `signature: null`. The
checker refuses `COMPLETE_V1` unless `qa.status == SIGNED` (`E018`) and the
signature is present (`E014`, `E016`).

## P0 Work/effect safety — issue #237

| ID | Required test | Level | Candidate | Observed | Verdict |
|---|---|---|---|---|---|
| T1 | Hand off A→B, leave A alive, stale A writes | LOCAL | #241 | lane-reported closed; integrator has no receipt | NOT_RUN |
| T2 | A and B race the same write scope | LOCAL | #241 | lane-reported closed | NOT_RUN |
| T3 | Expired/revoked grant or lease during a long turn | LOCAL | #241 + WE#13 | lane-reported closed | NOT_RUN |
| T4 | Duplicate delegation after unknown HTTP result + router restart | LOCAL | #252 `dac3088` | lane-reported closed | NOT_RUN |
| T5 | Same idempotency key, modified payload | LOCAL | #252 `dac3088` | lane-reported closed (`_replay` now compares payload) | NOT_RUN |
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
| 6 | **#230** (P1) | sibling of #243; depends on F1/S1 contracts per the plan |
| 7 | **#233** (M1) | consumes F1 readback |

Steps 2–3 and 4 are file-disjoint, so they may be prepared in parallel, but
each must still be landed by its **owner**, never by I1.

## Smallest next integration slice

1. The assigned QA session signs a **snapshot**, or records a superseding one
   with fresh `observed_at` values. PENDING is not a signature.
2. Collect **LOCAL** receipts for T1–T7 and T9 from the lane owners at the exact
   SHAs in the manifest (runner, command, environment, exit code, per row).
3. Re-pin every head and base immediately before composition.
4. Land step 1 above: serveros #241 + work-engine #13, in that order.
5. Only then prepare F1 → G1 → P1. Do not merge, cherry-pick or rewrite an owner
   branch inside I1.

## Reviewer actions

- Confirm each `PASS` cites a receipt path and an evidence level.
- Reject any row whose achieved level is below its required level.
- Reject any manifest that sets `source_merge.performed = true` while a gate is
  not PASS — the checker enforces this as `E095`.
- Confirm the QA signature comes from a Session that is not the integrator.
