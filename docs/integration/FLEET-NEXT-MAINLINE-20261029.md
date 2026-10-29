# Fleet Next V1 — W19 mainline landing ledger

**Status: one narrow source landing merged; release/device/production remain
blocked.** No install, timer, service, OAuth, Feishu/IM, paid model or GPU. The
old Fleet **0.1.0** control API is untouched.

---

## 0. Main identity

| | |
| --- | --- |
| main before | `3bc7adfe40274741cc528c7dd96167d2b67b00ec` (Push CI `40803065087` 5/5) |
| landed PR | **#520**, exact head `90b6671c9bfecd31d72163e2f5a86d68f991cff9`, tree `c7ca9cc37ded86351df660ff11103ce0dec938a1` |
| merge commit | `99c06fab07bd301e7df1ff7fa253f670b4d51d4d` |
| main after | `99c06fa` (Push CI `40806145632` 5/5) |
| independent QA | #517, head `823a38198322c90c30381758befd4e208ba85c2a`, `landing_check.py` verdict **SIGNED_PASS / OK** |

The machine-readable DAG is
`integrations/fleet-next-mainline/dag.json`; the claim is asserted by
`tests/test_fleet_next_mainline_ledger.py`.

---

## 1. The landed batch

#520 is exactly the 47-file candidate #517 built from main + #507 tree
`05da04f8c78f302b029c6304ed96f4e8ded5fc9b`:

* 8 Next-only integration directories: monitor, activation, source adapter,
  milestone notify/batch/observer/runner/sources;
* the #503 runtime-tick test.

**Delta `3bc7adf..99c06fa`: 47 additions, 0 deletions, 0 renames, 0 forbidden
paths.** Every blob equals the #507 source blob. `fleet_mcp` tree is unchanged
(`fcc3a9f59…`), `tests/hermetic-known-failures.txt` is unchanged, and there is
no `runtime/`, `plugin/`, `plugins/`, `serverosd/`, `native/`, `fleet/`,
`scripts/`, `.github/`, `bin/` or `deploy/` path.

| gate | reading |
| --- | --- |
| PR #520 hosted CI `40805524991` | **5/5**, all four shards `0 new` |
| main Push CI `40806145632` | **5/5**, all four shards `0 new` |
| #517 independent QA | **SIGNED_PASS**, 50 passed / 33 subtests / 0 xfail / 0 skip; runtime-tick 3/3 twice; #483 survival 70 passed; two mutation controls red |
| author composite on the same blobs | 211 passed / 53 subtests (test files not in this PR) |

A 5/5 shard gate is **not** a whole-repository zero-failure claim: the
baseline-listed failures remain, one codex-app-server test was flaky, and no
new baseline entry was added.

---

## 2. PR DAG

| PR | head | classification | next action |
| --- | --- | --- | --- |
| #415 | `dd5919a` → merge `93d9a1d` | merged canonical | none; Lark test-only |
| #483 | `644ee4a` → merge `3bc7adf` | merged canonical | none; 70 survival tests |
| #520 | `90b6671` → merge `99c06fa` | **merged landing** | none; 47 files |
| #517 | `823a381` | independent QA gate | keep Draft; `SIGNED_PASS` for #520; release gate BLOCKED |
| #507 | `05da04f` | canonical source, **wholesale blocked** | 214 commits/270 files vs main, 21 Legacy-path crossings; narrow extraction only |
| #508 | `fb8c435` | QA composition, **extract only** | 215/274; 43 QA assertions extracted to #517; P1 transcript open |
| #516 | `15d82ab` | broader draft, **not QA-allowlisted** | CI 5/5 but 39 files incl. author tests + chatgpt contract; keep open |
| #519 | `cd8b875` | broader stacked draft, **not QA-allowlisted** | CI 5/5; keep open; source shape superseded by #520 |
| #431 | `517ef91` | release-lane candidate | D7; separate owner, 8/12 |
| #505 | `3512c55` | release-lane candidate | depends on #431, 15/21 |
| #509 | `3520d0c` | **release artifact BLOCKED** | 1/5 digests equal per #514 |
| #506 | `30d7c4d` | **sidecar BLOCKED** | partial-upgrade orphan/revoke |
| #513 | `6602961` | sidecar candidate, blocked | depends on #506 atomic upgrade |
| #510 | `ea779d9` | release captain PRE_FLIGHT | no executor; not source |
| #511 | `6b98412` | CI gate candidate | pin points at old RC; rebase/collect |
| #512 | `d3c7c39` | local supervisor candidate | observe-only; `BLOCKED_ACTIVATION` |
| #514 | `30c4581` | **release acceptance BLOCKED** | #506 + #509 |
| #518 | `d704561` | artifact reproducibility candidate | separate release lane; 5/5 digests, not source |
| #407 | `9538238d` | **NO DIRECT MERGE** | 106 files/28 Legacy paths; V2 root |
| #473 | `a097b27d` | **NO DIRECT MERGE** | 116/31 Legacy; V2-rooted |
| #474 | `0ad3c078` | **NO DIRECT MERGE** | 109/29 Legacy; V2-rooted |
| #425 / #478 | issues | control matrix | #478 dual-track preserved; #425 source/CI/real gates still separate |

`mergeable: true` is not treated as release-ready. The only `SIGNED_PASS`
source landing is #520; the release gate stays BLOCKED.

---

## 3. Path and Legacy analysis

* #507 vs main: **214 commits / 270 files / 21 Legacy-path crossings**
  (`runtime/`, `plugin/`, `serverosd/`, `native/`, etc.). It cannot be
  wholesale-merged; its old-tree 5/5 cannot substitute for a main-based CI.
* #508 vs main: **215 commits / 274 files**; only its QA-owned assertions are
  extracted; the branch is not a landing.
* #407/#473/#474: 106/116/109 files with 28/31/29 Legacy crossings; keep out.
* The landed 47 paths are exactly the QA allowlist; every path's owner lane is
  in `dag.json` (`#493/#497/#504`, `#488/#498/#503`, `#490/#499`,
  `#423/#460`, `#481`, `#424`, `#417`, `#446`).

---

## 4. Gate Matrix

| row | status | evidence |
| --- | --- | --- |
| CODE | **PASS** | 47 exact blobs merged as `99c06fa` |
| UNIT | **PASS_TARGETED** | #517 QA 50/33/0 xfail/0 skip; runtime-tick 3/3 twice; author composite 211/53 |
| CI | **PASS** | PR `40805524991` 5/5; main Push `40806145632` 5/5; every shard `0 new` |
| RELEASE_ARTIFACT | **BLOCKED** | #509 1/5 digests; #506 partial upgrade; #514 BLOCKED |
| REAL_DEVICE | **NOT_RUN** | no device access |
| PRODUCTION | **NOT_RUN** | no deploy/install/main service change |

### #425 / #478 / #434

* **#425**: C (Agent Monitor source) has landed; A/B/D/E remain source/real
  gates, with real co-install and IM still **NOT_RUN**.
* **#478**: old Fleet 0.1.0 preserved; `fleet_mcp` tree unchanged; #407/#473/
  #474 not merged; real Legacy/Next co-install, Timer, App1 IM and main merge
  of release artifacts **NOT_RUN**.
* **#434**: no release artifact, real device, OAuth or four-line L4 closure;
  **NOT_RUN**.

---

## 5. Unmerged candidates and exact next steps

| candidate | reason | next owner |
| --- | --- | --- |
| #516 / #519 | broader drafts with author tests/contract outside the #517 allowlist; CI 5/5 but not the signed shape | keep Draft; propose a QA-approved test-only follow-up |
| #507 | wholesale 270-file/Legacy-crossing tree | extract only; no merge |
| #508 | 274-file QA composition; P1 transcript shape | #493/#499 agree on native-evidence shape; QA re-run |
| #509 | artifact digests 1/5 | release owner fixes reproducibility |
| #506/#513 | partial upgrade/revoke | sidecar owner fixes atomic install/revoke |
| #514 | two release blockers | release gate owner after #506/#509 |
| #407/#473/#474 | V2 roots | owner-only forward port, never direct merge |
| #511 | old RC pin | rebase to current main and collect triggered tests |
| #512 | observe-only / no verified provider | owner decides activation provider |
| #510 | PRE_FLIGHT | release captain owner |

No PR is closed as Superseded yet: #516/#519 have unique test deltas, and
#507/#508 retain source/QA material. Only #415/#483/#520 are already merged.

---

## 6. Canonical docs index (non-destructive)

Plan only; no physical move and no import change in this landing:

```text
docs/fleet-next/architecture   module and ownership index
docs/fleet-next/operations     run/observe/recovery index
docs/fleet-next/verification   QA, CI, gate matrix
docs/fleet-next/history        wave SHA/ledger index; old waves retained
```

Old wave ledgers and SHAs are kept; renaming/redirection will be a separate
non-destructive migration PR.

---

## 7. NOT_RUN

`real Legacy/Next co-install`, `Timer install`, `App1 IM`, main merge of
release artifacts, release artifact, real install, service start,
OAuth/credential, Feishu/IM send, paid model, GPU: **NOT_RUN**. This is source
landing, not RELEASED; real device and production are not measured.

Cross: #425 #478 #434 #515 #517 #520 #507 #508 #516 #519 #509 #506 #513 #510
#511 #512 #514 #518 #407 #473 #474.
