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

## Release ladder — what a candidate has actually achieved

The transport axis above says how evidence was gathered. This ladder says how
far a **candidate** has been accepted. They are separate; a candidate can be
`LOCAL` on the first axis and only `CODE_CANDIDATE` on this one.

| Class | Means | Does **not** mean |
|---|---|---|
| `CODE_CANDIDATE` | a reviewed source change at a pinned SHA | it was ever executed |
| `LOCAL_PASS` | the owner or QA ran it on this host at that SHA with a recorded exit | it joins another component |
| `REAL_READ_ONLY` | a real device/runtime was read through an existing read-only surface | anything was written, sent or created |
| `ACTUAL_DEV_PASS` | a real device performed the new action and produced a readback | a user-facing client path works |
| `OAUTH_CLIENT_E2E` | an authenticated owner client completed the workflow | production release |
| `FINAL_ACCEPTANCE` | every gate passed at its required class with a QA signature | — |

**A structural preflight cannot promote a candidate up this ladder.** It only
answers "do the branches conflict". Snapshot E is the worked example: the S1
pair is `LOCAL_PASS` on both sides and still fails to join.

## Owners

- **Integrator**: Tokyo Integration Owner Session. Owns this manifest, the
  conflict audit and the RC composition. Must not sign QA.
- **Independent QA**: **`INDEPENDENT_QA_PARTIAL`** — not `SIGNED`. Session
  `device:tokyo-shared-dev-01|claude|6955184a-f580-43cb-8ad4-2019980fbc02`,
  Run `8384fcda-97c0-4d38-820d-4778f8ce5c8d`; triage artifact is **PR #256**
  at `f5db800cb01d8352f8595d85b65a29e671728b2a` (**Snapshot F**, an offline
  plugin packaging build at #230 `a934bee`; Snapshots A `a4d7131`, B `2c4fe87`,
  C `a9c3ea5`, D `3ac74d5` and E `07e58ea` were superseded as snapshots, but
  **E's cross-repo P0 is still open**). It owns every PASS in this matrix and
  the `signature` field. A partial triage is **not** a PASS and **not** a
  signature.
- **Lane owners** (`S1`, `S2`, `F1`, `N1`, `N2`, `P1`, `M1`, `G1`, `R1`): own
  their source and their local receipts. The integrator does not re-run or
  re-sign them.

`qa.status` is `INDEPENDENT_QA_PARTIAL` with `signature: null`. The checker
refuses `COMPLETE_V1` unless `qa.status == SIGNED` (`E018`), the signature is
present (`E014`, `E016`), and an independent `qa_triage` object is `SIGNED`
(`E128`, `E129`).

## Independent QA findings (P0, not closed)

Snapshot C probed **`fdc223f`** at the real daemon boundary. It closes one
finding, downgrades another to partial, and opens two new P0s that no earlier
snapshot saw.

| ID | Sev | Candidate | Finding | State |
|---|---|---|---|---|
| QA-P0-1 | P0 | #252 | `intent_digest` omitted `session_ref`/`agent_runtime`, so a same-key send to another session/runtime was a replay | **CLOSED (LOCAL)** — target/runtime/principal in the digest; all three axes CONFLICT; no/expired context and self-asserted principal refused |
| QA-P0-2 | P0 | #252 | Caller `phase`/`principal` could write SETTLED; router records omitted `principal` | **CLOSED (LOCAL)** — D3/D4/D10 closed; regressions D1/D2/D5–D9/D11–D14 hold; residual is P1 below |
| QA-P0-7-E5 | **P0** | **#241 + WE#13** | The S1 pair is individually green but **not authoritatively joined at send admission**: `agent.binding.record` accepts a caller-forged writer (EVIL/999), then `LocalLoopbackTransport` sends **ADMITTED** while the live WE lease is held by a different owner/token (A/1). `held_leases` is not on the send path. | **OPEN** — Snapshot E exit 1, 22 held, 1 `SECURITY_FAIL` |
| QA-P0-5-D3 | P0 | #252 | Another trusted actor can settle A's attempt with A's copied digest and a fabricated result | **CLOSED (LOCAL)** — refused `UNTRUSTED_ACTOR`; the attempt is unchanged |
| QA-P0-6-D4 | P0 | #252 | A terminal SETTLED can be written back to `CALL_NOT_SENT`, after which the kernel returns `PROCEED` | **CLOSED (LOCAL)** — terminal moves are refused; `EFFECT_UNKNOWN` cannot move back; terminal retry is `REPLAY` |
| QA-P1-3 | P1 | #252 | Owner-observation reconcile has no production caller; grant enforcement not exercised | **OPEN** |
| QA-P1-4 | P1 | #230 | Offline plugin packaging: 70 checks, exit 0 — five Skills byte-identical, deterministic SHA-256 across two builds, no secret/path/env/argv/bypass, refusals for bad scope/endpoint/out-of-source | **OPEN** (packaging green) — authenticated `initialize`/`tools/list`/`tools/call` is `BLOCKED_BY_OWNER_OAUTH` |
| QA-P0-3 | P0 | #243 | `test_no_node_paths_in_product_data.py` failed at `494f452` on an absolute home literal | **CLOSED (LOCAL)** — guard re-run at `4a5d0a0`: 10 tests OK, exit 0 |
| QA-P1-1 | P1 | #255 | Present-but-null is read as affirmative: `{"health": null, "policy": null}` → `READY/ALLOWED` with a non-null `effective` | **OPEN** — uncorrected at `619cf63`; G1 owner dispatch platform-blocked |
| QA-P2-1 | P2 | #252 | `_same_writer` matches an empty/partial writer record | **CLOSED (LOCAL)** — empty writer, no-principal record and empty reconciler all refuse; the production-caller question moved to QA-P1-3 |
| QA-P1-2 | P1 | #254 | Manifest pinned stale heads and omitted new candidates | **CLOSED** — fixed in manifest `-e` |
| QA-P2-2 | P2 | #254 | `FLEET_NEXT_33_6` requires `REAL_DEVICE` while its rows are `SOURCE` | **OPEN by design** — a gate may require more than it has and then reports BLOCKED; it is not a near-PASS |

The author's 501-pass/171-subtest run is not independently rerun and does not
cover the two open negatives. Synthetic contexts and observations are `LOCAL`,
not `REAL_DEVICE`; provider observation source remains `BLOCKED` and
exactly-once is not asserted.

## P0 Work/effect safety — issue #237

| ID | Required test | Level | Candidate | Observed | Verdict |
|---|---|---|---|---|---|
| T1 | Hand off A→B, leave A alive, stale A writes | LOCAL | #241 | lane-reported closed; integrator has no receipt | NOT_RUN |
| T2 | A and B race the same write scope | LOCAL | #241 + WE#13 | **PARTIAL** — Snapshot E: no cross-layer atomicity between the binding record and the lease | PARTIAL |
| T3 | Expired/revoked grant or lease during a long turn | LOCAL | #241 + WE#13 | **PARTIAL** — lease expiry and refusal verified; no cross-layer atomicity, no revoked tombstone in the WE schema | PARTIAL |
| T4 | Duplicate delegation after unknown HTTP result + router restart | LOCAL | #252 `7a87e5c` | **CLOSED (LOCAL)** — Snapshot D: terminal retry is REPLAY; D3/D4/D10 closed with a red→green control | CLOSED (LOCAL) |
| T5 | Same idempotency key, modified payload | LOCAL | #252 `fdc223f` | **CLOSED (LOCAL)** — QA Snapshot C: digest carries target/runtime/principal and all three axes return CONFLICT; `7a87e5c` does not change the digest | CLOSED (LOCAL) |
| T6 | Work Engine lease expired but still in DB | LOCAL | WE#13 + #241 | **CLOSED (LOCAL)** — `held_leases` filters expired rows and keeps the row as evidence; WE exclusivity/token/TTL/release verified | CLOSED (LOCAL) |
| T7 | Cross-user / cross-runtime Session ref and stale grant | LOCAL | #241 | **OPEN** — the binding write accepts an unauthenticated caller-supplied writer | **BLOCKED** |
| T8 | Real Tokyo → Dell session send / get_run under grant | REAL_DEVICE | #241 + Fleet | no trusted transport observed | **BLOCKED** |
| T9 | Provider unreachable / partial dispatch | LOCAL | #252 | `EFFECT_UNKNOWN` path verified; provider observation source is synthetic | **BLOCKED** (`BLOCKED_PROVIDER_SOURCE`) |
| T10 | Child self-reports success while tests fail | LOCAL | #258 `5043ba1` | offline seam present, **not wired to the trusted runner** | **BLOCKED** |
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
| **0** | **S1 corrective #261 `a6bed15`** | **BLOCKS step 1 until QA verifies it.** Send admission must read the lease authority, and a legitimate session-create producer must acquire it |
| 1 | serveros **#241** then work-engine **#13** | structurally clean, **semantically `UNJOINED`** — blocked by step 0 |
| 2 | **#258** (T10) and **#259** (N3) | both base `main`, disjoint `integrations/**` directories; may land in parallel with step 1 |
| 3 | **#242** (N1) | 11-link chain, file-disjoint from every other lane |
| 4 | **#253** (N2) | one commit above #242; inherits its CMake layout |
| 5 | **#243** (F1) | 14-link chain; must precede G1 |
| 6 | **#255** (G1) | child of #243; re-pin after any F1 re-base |
| 7 | **#257** (R1) | base `main`, file-disjoint; deps are the F1/S1 contracts |
| 8 | **#230** (P1) | sibling of #243; depends on F1/R1/S1 contracts per the plan |
| 9 | **#233** (M1) | consumes F1 readback |

Steps 2–3 and 4 are file-disjoint, so they may be prepared in parallel, but
each must still be landed by its **owner**, never by I1.

## R1 runtime — `REAL_READ_ONLY`, not an accepted runtime

R1 is at **#257 `4045d16`** and now carries a bounded read-only observation
(`OBSERVED-2026-10-18.md`, snapshot `2026-10-17T22:16:25Z`–`22:17:00Z` on
`device:tokyo-shared-dev-01`, principal `vince`). It reads only existing
read-only surfaces: Agent MCP metadata, `serverosctl device agents --probe`,
`--version`, and session **metadata** (no transcript, no argv/environment, no
credential read, no send/model call).

| runtime | CLI | device binding | direct provider | metadata read | effective model | new work |
|---|---|---|---|---|---|---|
| Claude Code | `INSTALLED_ONLY` 2.1.272 | `BOUND` | `ABSENT` | `NEEDS_LOGIN` (BotMux) | `UNKNOWN` | `UNKNOWN` (`PROVIDER_ADAPTER_ABSENT` + `NEEDS_LOGIN`) |
| Codex | `INSTALLED_ONLY` 0.160.0 | **`UNBOUND`** | `READ_AUTHORIZED` (`provider:codex-app-server`) | `READ_AUTHORIZED` | `FACT` `gpt-6-astra` via `codex:thread/list` | **`REFUSED`** `PROVIDER_UNBOUND` |

Codex is **observed but unbound**: the registry declares only `claude-code`, so
every new-work verb is refused and there is no fallback. This is
`REAL_READ_ONLY` — **not** `ACTUAL_DEV_PASS`, not `OAUTH_CLIENT_E2E`. Nothing
was created, sent or resumed. `RUNTIME_CC_CODEX_CLOUDCLI` stays
`BLOCKED_PROVIDER_SOURCE`.

## T10 verdict evidence — offline seam, not a gate

**#258 `5043ba1`** adds `integrations/fleet-v1-verdict-evidence/**` plus
`bounded_provenance.py` and an expanded test suite: a pure verdict-evidence
acceptance seam and negative fixtures.

**#260 `ca1c05e`** (stacked on #258) adds a producer binding in `runtime/cli.py`.
But the **Work Engine finalization/continuation consumption is not wired**:
`mcp/continuation_mcp.py` was deliberately not edited because its wake envelope
lacks `work_run_id` / `commit_sha` / `runner_identity`. So the parent still has
no end-to-end acceptance path. `T10` stays `BLOCKED` (`BLOCKED_OWNER_ACTION`).

**#260 ↔ #240 overlap — 1 file.** Both modify `runtime/cli.py`; `#240` is the
journey B1 branch. It is a rebase question, not a semantic join, but one of the
two owners must move first.

## N3 network health — source-only profiles

**#259 `f81ad9d`** adds `integrations/fleet-v1-network-health/**` with six
dependency profiles, five separate axes and fail-closed verdicts, on synthetic
fixtures only. A profile that measures only local layers reports `LOCAL_ONLY`,
never `HEALTHY`. No listener, tunnel, Tailscale state or remote Selkies/RDC
application was observed, so `NETWORK_SSH_SELKIES_RDC` stays `BLOCKED_REAL_DEVICE`.

## Composition preflight — S1 pair: **BLOCKED_SEMANTIC**

Structural facts are clean. **Semantics are not, and that is what decides.**

| Fact | Value |
|---|---|
| merge-base(#241, serveros main) | `4a31628` (behind 0) |
| merge-base(#13, work-engine main) | `19d9520` (behind 0) |
| shared files | **0** — different repositories |
| dependency | database contract, not a module import |
| contract | `leases(resource, owner, token, expires_at)` + `%Y-%m-%dT%H:%M:%SZ`, `math.ceil` rounding, pinned identically on both sides |
| **semantic status** | **`UNJOINED`** |
| **work-lease authority** | **`SEND_ADMISSION_UNBOUND`** |
| Snapshot E evidence | **exit 1, 22 checks held, 1 `SECURITY_FAIL` (E5)** |
| S1 corrective | **#261 `a6bed15`** — send gate reads the WE lease; **QA retesting, PENDING** |
| session-create producer | **absent** — no legitimate producer acquires the authoritative lease |
| authenticated writer | **false** — the binding claim is still caller-writable |
| sibling overlap | **#261 ↔ #252 share 4 files** (see below) |
| order | work-engine `#13` (writer) → serveros `#241` (reader) → **#261 corrective verified by QA before any landing** |

What Snapshot E measured on the real daemon: `agent.binding.record` accepts a
caller-supplied writer (EVIL/999), and `LocalLoopbackTransport` returns
**ADMITTED** even though the live Work Engine lease on the same resource names a
different owner/token (A/1). `held_leases` is not on the send path.

Verified LOCAL and still true: WE lease exclusivity/token/TTL/release and
renewal fencing; `held_leases` filters expired rows and keeps the row as
evidence; a closed source thread refuses after handoff; same-Item token ordering
refuses a stale claim; an expired claim refuses.

**#261 is not a receipt.** It is a source corrective at `a6bed15`, stacked on
`#241`, touching `context/resolver.py`, `runtime/coordination.py`,
`runtime/daemon.py`, `fleet/agent-control-contract.yaml`, `mcp/agent_mcp.py`
and adding `tests/test_v1_lease_authority_gate.py`. QA is retesting it now.
Until that run exists, E5 stays open and everything downstream stays blocked.

**#261 ↔ #252 overlap — 4 files.** Both are stacked on `#241` and both rewrite
`runtime/daemon.py`, `fleet/agent_control_router.py`, `mcp/agent_mcp.py` and
`tests/test_coordination.py`. They are siblings, not a stack: neither order is
free. `#261` must land first because it changes the admission authority; `#252`
then re-bases. I1 records this and does not resolve it.

**Therefore:** the pair is not landable, and no source-only preflight can
promote it. The checker enforces this as `E169`/`E170` (an unbound lease cannot
be `CLEAN_PREFLIGHT` and cannot authorize a source merge), `E172` (an
`UNJOINED` pair cannot be `CLEAN_PREFLIGHT`), and `E176`/`E177`
(`SEND_ADMISSION_BOUND` requires a legitimate session-create producer with an
authenticated writer). It additionally requires a same-SHA negative proof with
`exit 0` and `security_fail 0` (`E165`–`E168`).

Next safe commands — only after the S1 corrective SHA exists and independent QA
has produced the passing negative proof (the owner runs them, not I1):

```sh
# 1. writer first, in its own worktree at 40b0622
cd <work-engine-worktree> && python3 -m unittest tests.test_lease -v

# 2. reader second, in its own worktree at d0754ed
cd <serveros-worktree> && python3 -m unittest tests.test_v1_write_fence tests.test_coordination -v

# 3. the join itself: forged writer refused, lease mismatch refused, exit 0
#    (this is the receipt the corrective milestone must produce)
```

## Work in flight — none at this snapshot

The two accepted Owner runs materialized as PRs: N3 as **#259** (`f81ad9d`) and
T10 as **#258** (`4156572`). They are source candidates now, not work in flight,
and neither is evidence. The checker still refuses to let any run be recorded as
a candidate, pinned, evidence or landable without a PR and a SHA
(`E143`–`E147`).

## Smallest next integration slice

1. Independent QA re-runs its triage against **this** snapshot — #252 `fdc223f`,
   #243 `4a5d0a0`, #257 `c2e2e8d`, #254 `5a07df2` — and signs it, or records a
   superseding one. `INDEPENDENT_QA_PARTIAL` is not a signature, and Snapshot B
   (`f2b89e1`) is not a statement about `fdc223f`.
2. **QA-P0-1 and QA-P0-2 are CLOSED at LOCAL** (Snapshot D, with a red→green
   control). T4 and T5 may move at LOCAL, not higher. **T9 stays
   `BLOCKED_PROVIDER_SOURCE`** (observation source is synthetic) and **T7 stays
   NOT_RUN** (grant enforcement was not exercised). Residual P1: reconcile has
   no production caller.
3. F1's guard re-run at `4a5d0a0` is already CLOSED at LOCAL; G1 must still fix
   present-null (QA-P1-1), and its owner dispatch is platform-blocked.
4. **The S1 pair is NOT landable.** Its structural preflight is clean and its
   semantics are `UNJOINED` (Snapshot E: exit 1, 22 held, 1 `SECURITY_FAIL`).
   Security A must land the isolated corrective first; independent QA must then
   reproduce the negative at the corrective SHA (forged writer refused, lease
   mismatch refused, `exit 0`, `security_fail 0`). Only then can the pair be
   scheduled. I1 does not merge, push or compose it.
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
