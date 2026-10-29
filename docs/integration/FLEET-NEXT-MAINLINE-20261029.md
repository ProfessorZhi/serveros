# Fleet Next V1 — W19 mainline landing ledger

**Status: one narrow source landing merged; release/device/production remain
blocked.** No install, timer, service, OAuth, Feishu/IM, paid model or GPU. The
old Fleet **0.1.0** control API is untouched.

---

## 0. Main identity

| | |
| --- | --- |
| main before | `3bc7adfe40274741cc528c7dd96167d2b67b00ec` (Push CI `40803065087` 5/5) |
| first landing | **#520**, head `90b6671c9bfecd31d72163e2f5a86d68f991cff9`, tree `c7ca9cc37ded86351df660ff11103ce0dec938a1`, merge `99c06fab07bd301e7df1ff7fa253f670b4d51d4d` (Push CI `40806145632` 5/5) |
| second landing | **#519**, head `cd8b875b0722718b31750da9beee1c4df072b415`, tree `206a4f4583162204cc5172e928f7ab243da73eaf`, merge `b27c93e346d5503a75f95ec068c386b416841a62` |
| main after | `b27c93e` (Push CI `40811251506` 5/5, all shards `0 new`) |
| #520 independent QA | W19B review comment [`6486841401`](https://github.com/ProfessorZhi/serveros/issues/515#issuecomment-6486841401), `SIGNED_PASS`; this is the reviewer's own signoff, not the earlier #517 NO_LANDING_PR verdict |
| #519 independent QA | #524 PR `https://github.com/ProfessorZhi/serveros/pull/524`, `LANDING_VERDICT READY`; evidence `integrations/fleet-next-w19-release-acceptance/evidence/w19-acceptance-20261029.json` blob `096986b7…` / sha256 `06e490cc…`; release verdict BLOCKED |

The machine-readable DAG is
`integrations/fleet-next-mainline/dag.json`; the claim is asserted by
`tests/test_fleet_next_mainline_ledger.py`.

---

## 1. The two landed batches

#520 is the 47-file source candidate from #507 tree
`05da04f8c78f302b029c6304ed96f4e8ded5fc9b`: 8 Next-only integration
directories plus the #503 runtime-tick test. **Delta `3bc7adf..99c06fa`: 47
additions, 0 deletions, 0 renames, 0 forbidden paths.**

#519 then landed the author tests/docs/contract: **delta `99c06fa..b27c93e`:
20 additions, 0 modifications, 0 deletions**, preflighted with
`git merge-tree` and verified after merge. Its PR CI `40810813359` is 5/5 with
every shard `0 new`.

| gate | reading |
| --- | --- |
| #520 PR CI `40805524991` | **5/5**, four shards `0 new` |
| #519 PR CI `40810813359` | **5/5**, four shards `0 new` |
| main Push CI `40811251506` | **5/5**, four shards `0 new` |
| #520 independent QA | W19B review `6486841401` **SIGNED_PASS**; #517 QA gate 50 passed / 33 subtests / 0 xfail / 0 skip |
| #519 independent QA | #524 `LANDING_READY`; #519 merge-tree added exactly 20 files |

`fleet_mcp` tree is unchanged (`fcc3a9f59…`), `tests/hermetic-known-failures.txt`
is unchanged, and neither landing touched `runtime/`, `plugin/`, `plugins/`,
`serverosd/`, `native/`, `fleet/`, `scripts/`, `.github/`, `bin/` or `deploy/`.
A 5/5 shard gate is **not** a whole-repository zero-failure claim.

### W19C PR closeout

24 PRs were closed as `CLOSED_SUPERSEDED` after the #515 reviewer matrix
(`6486928239` / `6486978745`):

```text
423 426 446 459 464 470 472 481 482 488 490 493 497 498 499 503 504
449 469 479 487 492 494 500
```

The first group is absorbed by main (`99c06fa` source / `b27c93e` tests);
the second group is retained in the **still-open #507** (`05da04f`) and was
**not** merged into main. Open PR count: **90 → 79** before #521 merge
(**78** after). #511 waits for #523. No branch was deleted.

---

## 2. PR DAG

| PR | head | classification | next action |
| --- | --- | --- | --- |
| #415 | `dd5919a` → `93d9a1d` | merged canonical | none |
| #483 | `644ee4a` → `3bc7adf` | merged canonical | none |
| #520 | `90b6671` → `99c06fa` | **merged landing** | none; 47 source files |
| #519 | `cd8b875` → `b27c93e` | **merged landing** | none; 20 test/docs/contract files |
| #516 | `15d82ab` | **merged via #519** | ancestor, GitHub MERGED |
| #521 | `6db7ddb` (updated) | READY_PENDING | this ledger; fresh CI + re-sign |
| #523 | `83b5392` | READY_PENDING | `STACK_ABSORBED_ON` stale; owner fix then QA |
| #517 | `823a381` | independent QA gate | keep Draft |
| #524 | `3e6a4ee` | independent release gate | keep Draft; LANDING READY, RELEASE BLOCKED |
| #507 | `05da04f` | canonical source, wholesale blocked | keep OPEN; 24 closed PRs' commits preserved here |
| #508 | `fb8c435` | QA composition, extract only | keep; P1 #522 |
| #509/#506/#513/#514/#518 | various | release/sidecar blocked | keep; release_permitted:false |
| #511 | `6b98412` | superseded by #523 pending | keep until #523 lands |
| #428/#460 | various | unique tests not in main | keep |
| #407/#473/#474 | various | **NO DIRECT MERGE** | keep; V2 roots |
| closed 24 | W19C | `CLOSED_SUPERSEDED` | per-PR comment; branch/ref retained |

`mergeable: true` is not release-ready. `CLOSED_SUPERSEDED` is not `merged`.

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
