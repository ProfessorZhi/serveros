# Fleet V1 — measured branch topology

All values below were read from the GitHub API on 2026-10-17. This file records
**ancestry**, not intent. Where a PR body disagrees with its own head, the head
wins and the disagreement is recorded.

## Four towers, one of them already on `main`

```text
serveros:main 4a31628
│
├─ S1/S2   #241 d0754ed ── #252 d49342b            base = main (behind 0)
│
├─ M1      #196 … ── #233 36a206d                  consumer of F1
│
├─ QA      #256 2c4fe87                            base = main; integrations/fleet-v1-qa/** only
│
├─ R1      #257 c2e2e8d                            base = main; integrations/fleet-v1-runtime-parity/** only
├─ T10     #258 4156572                            base = main; integrations/fleet-v1-verdict-evidence/** only
├─ N3      #259 f81ad9d                            base = main; integrations/fleet-v1-network-health/** only
│
├─ F1      #146 ─ #149 ─ #150 ─ #154 ─ #155 ─ #163 ─ #164 ─ #177
│          ─ #182 ─ #184 ─ #192 ─ #198 ─ #200 008a3360
│              ├─ #214 ─ #216 ─ #228 ─ #229 b0f5480 ─ #231 ─ #243 494f452
│              │                                          (+ #225 merged in)
│              │                                              └─ #255 619cf63   (G1)
│              └─ #227 7712e64d ─ … ─ #230 a934bee
│
└─ N1/N2   #161 9dca6fd ─ #176 ─ #180 ─ #206 ─ #213 ─ #215
           ─ #217 ─ #219 ─ #226 ─ #232 ─ #242 bec4835 ─ #253 5e7d074

work-engine:main 19d9520 ── #13 40b0622             cross-repo pair of #241
```

Depths, counted from the PR whose base is `main`:

| Tower | Links above `main` | Base of the deepest link |
|---|---|---|
| S1/S2 | 2 | `main` |
| F1 (#243) | 14 + 5 | `stack/fleet-v1-pre-core-lite-wiring` → see below |
| P1 (#230) | 14 + 2 | `fix/fleet-v1-build-outside-source-20261016` |
| N1 (#242) | 12 | `feat/native-bounded-recovery-20261016` |
| N2 (#253) | 13 | `feat/c-line-bounded-recovery-v1-20261016` (= #242 head) |
| G1 (#255) | 15 + 5 | `integration/fleet-v1-candidate-20261017` (= #243 head) |
| R1 (#257) | 1 | `main` |
| T10 (#258) | 1 | `main` |
| N3 (#259) | 1 | `main` |

`native/node-core` is **absent from `main`** (contents API 404). The whole N
tower is unlanded, not partially landed.

## F1 and P1 are siblings, not a stack

- `merge-base(#243, #230) = 008a3360` (= #200 head).
- #243 is **19** commits ahead of that point; #230 is **5**.
- Their own-commit file surfaces are **22** and **13** files with an **empty
  intersection**.
- The 163 files common to their net change sets are **inherited ancestry**, not
  collision. Reading that overlap as a conflict is the error this section exists
  to prevent.

Conclusion: F1 and P1 are textually composable. That does **not** authorize a
merge here — see the policy below.

## Declared bases that are not PR heads

`#231` declares `stack/fleet-v1-pre-core-lite-wiring` (`a11a9dd`). That branch is
not the head of any PR; it is **one commit behind** #229's head `b0f5480`, and
it is contained in #229, #231 and #243.

Two consequences, both easy to get wrong:

1. #243's **content** is complete relative to #229 (the compare against `b0f5480`
   reports `behind_by: 0`). The gap is in the **declared ref**, not the tree.
2. A PR whose base is a non-PR branch cannot merge into `main` as-is. The chain
   must be re-based by the lane owner, which changes the reviewed SHA. That
   directly tensions the "immutable verified PR" requirement in #250 and is
   recorded as P0 `B6-NON-PR-BASE`.

## Stale pins found in PR bodies

| PR | Body says | Real head | Delta |
|---|---|---|---|
| #230 | corrective head `43a9555f` | `a934bee` | 2 commits behind |
| #252 | milestone 1 `00bb701` | `7a87e5c` | moved five times: `00bb701 → d49342b → dac3088 → f2b89e1 → fdc223f → 7a87e5c` |
| #243 | (none) | `4a5d0a0` | advanced `494f452 → 4a5d0a0` (guard fix) |
| #256 (QA triage) | `a4d7131` named #252 `dac3088`, #243 `494f452`, #254 `07f44ad` | Snapshot D `3ac74d5` reran `7a87e5c` with a red→green control | current; verdict still QA_PARTIAL |

Never copy a SHA out of a PR body. Re-pin from the API immediately before any
composition.

## Merge policy for this directory

This branch is **additive only** (`integrations/fleet-v1-release/**`). It does
not merge, cherry-pick, rebase or rewrite any owner branch, and it does not
touch `fleet_mcp/`, `native/`, `runtime/`, `Skills/` or any other owner area.

`#255` (G1) is a **child**, not a sibling: its base is #243's head. Two
consequences that a naive merge order gets wrong:

1. G1 cannot land before F1.
2. Any re-base of #243 changes #255's base, so G1 must be re-pinned — and its
   reviewed SHA changes — after every F1 move.

When composition is eventually authorized, the order is forced by ancestry:

1. **S1 pair** — work-engine #13 (writer) then serveros #241 (reader). Both are
   based on current `main` (`behind_by: 0`) and the pair has a CLEAN preflight.
2. **T10 (#258)** and **N3 (#259)** — both base `main`, disjoint `integrations/**`
   directories; may land in parallel with step 1.
3. **N1 (#242)** — 11-link chain, file-disjoint from every other lane.
4. **N2 (#253)** — one commit above #242; depends on its CMake layout.
5. **F1 (#243)** — 14-link unlanded chain. The owner must re-base; I1 must not.
6. **G1 (#255)** — after F1, re-pinned against the F1 revision that landed.
7. **R1 (#257)** — base `main`, file-disjoint; deps are the F1/S1 contracts.
8. **P1 (#230)** — sibling of #243; deps are F1/R1/S1 contracts per the plan.
9. **M1 (#233)** — consumes F1 readback.

The S1 pair is a **cross-repo database contract**, not a module dependency:
`serveros/context/resolver.py::held_leases` reads `leases` with
`expires_at > ?`, and `work-engine/workengine/store.py` writes that column with
the same `%Y-%m-%dT%H:%M:%SZ` helper (`math.ceil` rounding). Neither imports the
other, so the pair has zero shared files and the writer must land first only so
the reader has real claims to filter.

Two accepted Owner runs (N3 network-source, T10 test-evidence) are **work in
flight**: sessions and Run ids exist, but no PR and no SHA. They are not on this
graph and must not be composed, cited or counted as evidence.

`source_merge.performed` stays `false` in the manifest until every gate that
authorizes composition has a PASS with a receipt. The checker rejects the
opposite as `E095`.

## Drift guard

`drift_guard.snapshot_time` is the instant all pins were read. Every candidate
carries the same `observed_at`. Re-read the provider before **push**,
**composition** and the **QA signature**; a mismatch is `W030` and blocks
`COMPLETE_V1` as `E115`. `#252` moved twice during this campaign, which is the
concrete reason this field exists.
