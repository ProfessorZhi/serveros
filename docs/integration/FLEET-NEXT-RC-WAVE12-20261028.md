# Fleet Next V1 — Wave 12: the RC composition of #471 + #472 on #479's exact head

**Status: Draft RC, integration-only.** No `main` merge, no release, no install,
no service change, no OAuth/credential/Feishu/model call. CODE / UNIT / CI only;
REAL_DEVICE and PRODUCTION are recorded as *not measured* (§10).

---

## 0. Identity

| | |
| --- | --- |
| branch | `integrate/fleet-v1-rc-wave12-20261028` |
| **composed RC commit** | **`26ad7245`** |
| **composed tree digest** | **`f64d7af1`** |
| first composition step | `d38d47a` (tree `03538c5f`) = base + #471 |
| base | #479's exact head `cd1485f` (= Wave 7 RC + three doc commits) |
| parents of `26ad7245` | `d38d47a` (RC + #471) and `0c588896` (#472) |
| ancestry, not patches | `git merge-tree --write-tree` + `git commit-tree` — the authors' commits are *parents* of the result |

The Wave 7 RC underneath is `4847eb5a` (tree `4e31e4a2`) = #469 `14306f36` +
#470 `56fcb927`; see `FLEET-V1-RC-WAVE7-20261028.md`.

---

## 1. Inputs

| input | SHA | what it is | its own delta |
| --- | --- | --- | --- |
| base | `cd1485f` | #479 tip, Wave 7 RC + §7 CI readings | — |
| **#471** | `7b4b9d66` | `fix(fleet-next): pin the ChatGPT payload member set` | 2 files, `+59/−1` |
| **#472** | `0c588896` | `test(fleet-v1): give the live #456 contract a real session list, and keep its refusals` | 2 files, `+156/−17` |
| #484 (reference) | `94a3c7bb` | QA acceptance on a Wave 7-derived branch, 5/5 hosted at that commit | 8 files vs `cd1485f` |

Both author commits are **single commits whose parent is already in the RC**:

```
7b4b9d6^ = 14306f36  (in the RC: one of 4847eb5a's parents)
0c58889^ = 56fcb927  (in the RC: the other parent of 4847eb5a)
```

That is what makes "stacked ancestry" exact rather than approximate: the
merge-base of each author head with the RC is *the author's own parent*, so the
three-way merge can only add that author's own delta. Measured, not assumed:

```
git merge-base pr/471 cd1485f  ->  14306f3618f5...   (= pr/471^)
git merge-base pr/472 cd1485f  ->  56fcb9276acc3a... (= pr/472^)
```

---

## 2. Why this is a composition and not #484 with a new name

#484 is a QA branch. Its history is:

```
94a3c7b (acceptance test)
da91300 "Wave 7 §7: the tip run repeats..."      <- a re-created copy of this RC's ed0b404
2fd55ae "test(fleet-v1): the live #456 ..."      <- #472's change, re-applied as a new commit
debea46 "fix(fleet-next): pin the ChatGPT ..."   <- #471's change, re-applied as a new commit
d037588 (a real Wave 7 commit)
```

So #484 **re-materialises** both deltas as new commits and rewrites a Wave 7
commit with a new SHA. The content is faithful — the blob table proves it — but
the history is not: `7b4b9d66` and `0c588896` do not appear in it, and the 5/5
green cannot be attributed to the authors' commits.

| file | #471's blob | #472's blob | #484's blob | **Wave 12 blob** |
| --- | --- | --- | --- | --- |
| `integrations/chatgpt-fleet-next/build_zip.py` | `92830e1b` | `88d414fd` | `92830e1b` | **`92830e1b`** |
| `tests/test_fleet_next_plugin_package.py` | `0c5648da` | `37828d59` | `0c5648da` | **`0c5648da`** |
| `integrations/fleet-v1-milestone-batch/README.md` | `aad3702d` | `ba6eb2ac` | `ba6eb2ac` | **`ba6eb2ac`** |
| `tests/test_fleet_v1_milestone_batch_registration.py` | `33f34cb1` | `5bb39720` | `5bb39720` | **`5bb39720`** |

Wave 12's four files are byte-identical to #484's, and the *history* is the
authors'. Both properties hold at once, which is the point: the reviewable
artifact is the merge, and #484's hosted green becomes corroboration rather than
evidence (this branch runs its own CI — §7).

---

## 3. The composition

```
git worktree add -b integrate/fleet-v1-rc-wave12-20261028 cd1485f
t1=$(git merge-tree --write-tree cd1485f pr/471)
c1=$(git commit-tree $t1 -p cd1485f    -p pr/471 -m "compose pr/471 ...")
t2=$(git merge-tree --write-tree $c1    pr/472)
c2=$(git commit-tree $t2 -p $c1        -p pr/472 -m "compose pr/472 ...")
```

| step | result | tree | conflicts |
| --- | --- | --- | --- |
| RC + #471 | `d38d47a` | `03538c5f` | **none** |
| + #472 | **`26ad7245`** | **`f64d7af1`** | **none** |

`git diff --stat cd1485f 26ad7245` — **4 files, +215/−18**, and nothing else:

```
integrations/chatgpt-fleet-next/build_zip.py                   |  25 ++++
integrations/fleet-v1-milestone-batch/README.md                |  56 ++++++--
tests/test_fleet_next_plugin_package.py                        |  35 +++++-
tests/test_fleet_v1_milestone_batch_registration.py            | 117 +++++++++++--
```

**No `fleet_mcp/**` file is in the delta.** The only non-test source change is
`build_zip.py`, the ChatGPT-plugin *builder* — a declaration plus a refusal, not
a control-surface change. Everything else is one document and two test files.

---

## 4. The two reds, before and after

Both ids are the ones #479's hosted CI reported as its only NEW failures
(Wave 7 record §7), reproduced here on `cd1485f` first.

| id | on `cd1485f` | on `26ad7245` |
| --- | --- | --- |
| `tests/test_fleet_next_plugin_package.py::ThisIsNotTheNativeServerOSPlugin::test_the_clean_package_is_not_caught_by_the_layout_rule` | **FAILED** `AssertionError: 7 != 6` at `test_fleet_next_plugin_package.py:241` | **passes** |
| `tests/test_fleet_v1_milestone_batch_registration.py::TheLiveCommanderContractIsConsumedAsIs::test_a_plan_the_live_contract_accepts_becomes_a_registration` | **FAILED** `RegistrationRefused: COMMANDER_REFUSED: #456 refused this plan (SESSION_NOT_FOUND); a batch is filed only for a dispatch that was allowed` at `registration.py:498` | **passes** |

Measured as a flip, in one command, on both trees:

```
BEFORE (cd1485f):  2 failed in 1.28s
AFTER  (26ad7245): 2 passed in 0.44s
```

**Neither red was repaired by relaxing a test.** The two mechanisms:

* **#471** replaces `assertEqual(len(members), 6)` with a declared
  `PAYLOAD_MEMBERS` set (7 entries: `plugin.json`, `icon.svg`, `README.md`, four
  `skills/*/SKILL.md`) and makes `collect()` raise `Refused` on any member the
  declaration does not list, or any declared member the tree does not have. The
  count was the symptom; "a new Skill cannot enter the archive silently" is the
  rule.
* **#472** replaces the fixture's `"sessions": []` with the sessions the plan
  actually names (same device, runtime and project), and **adds** the fail-closed
  half: empty list → `SESSION_NOT_FOUND`, wrong project → `SESSION_PROJECT_MISMATCH`,
  other device → `SESSION_PROJECT_MISMATCH`, busy → `SESSION_BUSY`, missing grant
  → `GRANT_DENIED`, and every one of them asserts that no file was filed. The
  fixture is not why the accepting case passes.

---

## 5. Gates and regression on the composition

Single-process, CPU-bounded, target scope only (no `-n`, no service, no network).

| suite | reading |
| --- | --- |
| Wave 7 merge gates (packaging + notifier seam) + Wave 7 ledger | **31 passed** |
| batch family (`milestone_batch`, `…_integration`, `…_e2e_integration`, `…_registration`) | **97 passed**, 15 subtests |
| `test_fleet_next_plugin_package.py` (whole file) | **56 passed**, 22 subtests, **1 NOT_RUN** |
| the two target ids alone | 2 failed → **2 passed** |

The one non-green in the plugin package file is
`TheSkillSourceIsComplete::test_a_symlinked_member_is_refused`, which fails with
`OSError: [WinError 1314]` — creating a symlink needs a privilege this Windows
account does not hold. It is **NOT_RUN, not a claim**: the same test is green on
the Linux runner (it was green in #479's three hosted runs under a different
node id), and nothing in this composition touches it.

---

## 6. Composition fidelity — the two guardrails

`tests/test_fleet_next_rc_wave12_ledger.py` (11 tests) pins what a green run
cannot show:

1. **Fidelity.** LF-normalised sha256 per file, recorded from tree `f64d7af1`:

   | file | sha256 |
   | --- | --- |
   | `integrations/chatgpt-fleet-next/build_zip.py` | `f1fd2ab580f2c5a1…` |
   | `tests/test_fleet_next_plugin_package.py` | `672a0591bd4b6e72…` |
   | `integrations/fleet-v1-milestone-batch/README.md` | `d3b5c1e59daca7da…` |
   | `tests/test_fleet_v1_milestone_batch_registration.py` | `a685ea678071b561…` |
   | `integrations/fleet-v1-milestone-batch/registration.py` **(must not move)** | `f226860cc58893b5…` |

   The last row is the anti-hack guard: `registration.py:498` is where the red
   was raised, and the fix belongs on the fixture side. If somebody teaches the
   registerer to tolerate a snapshot that does not name the plan's sessions, that
   digest moves and the ledger goes red.

2. **Mechanism.** The declaration must equal the source tree in both directions;
   the hardcoded `6` must not come back; the accepting case must be handed
   `live_snapshot()` and an empty list must remain a *refusal* input; and the
   five refusal cases with their codes and their "nothing was filed" assertion
   must all still be there.

Both are one-directional by construction, so they were measured in **both**
directions rather than assumed:

```
on cd1485f   (pre-composition):  11 failed, 4 passed
  - all four fidelity sub-cases red (the files are the pre-#471/#472 bytes)
  - PAYLOAD_MEMBERS does not exist; the count is 6; collect() has no refusal
  - no live_sessions()/live_snapshot(); the accepting case passes "sessions": []
  - zero self.assertRefused() calls
on 26ad7245  (composition):      11 passed
```

The four that pass on both trees are the record checks and the untouched-product
digest — they are supposed to be stable, which is what makes them usable as
controls.

---

## 7. Hosted CI

Branch `integrate/fleet-v1-rc-wave12-20261028`, run **`40653792777`** (`Tests`,
`pull_request`, head `d8dc1f9`). **Conclusion: `success`.**

| shard | job | pytest summary (main run) | `== baseline gate ==` |
| --- | --- | --- | --- |
| 1/4 | `122699859321` | 13 failed, **2746 passed**, 32 skipped, 3 xfailed, 660 subtests | `13 failing, **0 new**, 0 flaky` |
| 2/4 | `122699859328` | 19 failed, **3166 passed**, 45 skipped, 4261 subtests | `17 failing, **0 new**, 0 flaky` |
| 3/4 | `122699859265` | 9 failed, **3482 passed**, 5 skipped, 1 xfailed, 3637 subtests | `9 failing, **0 new**, 0 flaky` |
| 4/4 | `122699859243` | 18 failed, **3363 passed**, 33 skipped, 989 subtests | `18 failing, **0 new**, 0 flaky` |

Plus `Claude Code plugin matches its source` — `success`. **5/5 jobs green**, and
the gate reading is the one that counts: **0 new, 0 flaky** in every shard. The
57 remaining gate failures are baseline entries, present on `cd1485f` too.

### 7.1 Against #479's own runs, the two ids are closed

| | #479 tip (`40649149374`) | Wave 12 (`40653792777`) |
| --- | --- | --- |
| shard 1 | `16 failing, **1 new**` — the registration id | `13 failing, **0 new**` |
| shard 2 | `11 failing, **1 new**` — the clean-package id | `17 failing, **0 new**` |
| shard 3 | `10 failing, 0 new` | `9 failing, **0 new**` |
| shard 4 | `22 failing, 0 new` | `18 failing, **0 new**` |
| run colour | `failure` | **`success`** |

Per-shard counts move because two test files change size and
`scripts/hermetic_tests.py` reshuffles shards by file set; the **id** reading is
what transfers, and it is empty.

### 7.2 Positive control — the new tests actually ran

Absence from `NEW FAILURES` is not evidence that a file executed, so the check is
the four-shard passed total, and its decomposition:

```
#479 tip (40649149374) : 2974 + 3408 + 3338 + 3014 = 12734
Wave 12  (40653792777) : 2746 + 3166 + 3482 + 3363 = 12757      Δ = +23
```

| term | count | how measured |
| --- | --- | --- |
| `tests/test_fleet_next_rc_wave12_ledger.py` (this lane, new) | +11 | `--collect-only` on both worktrees |
| `tests/test_fleet_next_plugin_package.py` (#471's four new cases) | +4 | 22 → 26 collected |
| `tests/test_fleet_v1_milestone_batch_registration.py` (#472's six new cases) | +6 | 25 → 31 collected |
| the two ids this branch was built to close, now passing | +2 | §4 |
| **sum** | **+23** | equals the observed Δ |

Both runs report `0 flaky`, so the Δ has no flake term to explain away.

### 7.3 Not run

The other three workflows are **NOT_RUN, not green**:
`serveros-native-linux-contract`, `serveros-native-windows-contract` and
`fleet-next-isolated-contracts` are path-filtered on paths this PR's diff does not
touch (it is diffed against `integrate/fleet-v1-rc-wave7-20261028`). #479's
earlier branches trigger the Linux contract because they are diffed against
`main`; that is a different question from "did this composition break it", and
this lane does not inherit that green.

---

## 8. Legacy Fleet 0.1.0 stays where it is (#478)

Per #478, Legacy Fleet **0.1.0** keeps carrying GPT's current device Session /
Agent / Run control; Fleet Next runs independently and in parallel and does not
replace the legacy plugin ID, the MCP URL, the port, the DB or the locks. NEXT
remains **shadow, zero write**.

This lane is consistent with that and does not need a new permission to be:

* the composition's only non-test source change is the **NEXT plugin's own
  builder** (`integrations/chatgpt-fleet-next/build_zip.py`) — it changes what
  the *package* may contain, not what any endpoint does;
* nothing under `fleet_mcp/**`, no service unit, no port, no schema, no lock file
  and no legacy identifier appears in `git diff cd1485f 26ad7245`;
* nothing was installed, started, restarted or published; the payload set is a
  declaration checked at build time and never reaches a device here.

---

## 9. Phase 2 — the next five, and what each actually depends on

Measured with the same two git commands against the Wave 12 RC (`26ad7245`), not
inferred from branch names. "own delta" is `git diff pr/N^ pr/N`; "merge-base" is
with the RC.

| PR | base | merge-base with RC | own delta | merge result on the RC | its tests on that tree |
| --- | --- | --- | --- | --- | --- |
| **#477** model constraints | #471 | `7b4b9d66` **(in RC)** | 3 files, `+608/−28` | **clean, 3 files** — exactly its own | **76 passed** |
| **#481** bounded CLI | #472 | `0c588896` **(in RC)** | 4 files, `+1432/−6` | **clean, 4 files** — exactly its own | **51 passed** |
| **#480** dual-track plugin | #477 | `7b4b9d66` | 4 files, `+637/−0` | clean, but **7 files** — it carries #477 too | **69 passed** *after* #477 |
| **#473** sidecar | #467 | `3f5ec20b` **(#407's tree)** | 5 files, `+875/−23` | would carry **95 files, +11470/−619** (20 under `fleet_mcp/`) | **not attempted** |
| **#474** receipt | #465 | `3f5ec20b` **(#407's tree)** | 2 files, `+291/−43` | would carry **88 files, +8394/−566** (18 under `fleet_mcp/`) | **not attempted** |

Read as gates:

* **#477 is ready to stack next** and #481 is ready *independently* — both merge
  as their own delta only, because their parents are already in the RC. #477's
  tests (including #472's registration file, which is where the two stacks touch)
  are green on the composition: **76 passed**.
* **#481 merges as its own delta and its own tests pass — 51 passed.** One
  semantic overlap to eyeball at landing time: #481 edits
  `integrations/fleet-v1-milestone-batch/README.md`, which #472 also edited. The
  merge is clean and the two README hunks are about different sections, but it is
  the one place where "clean" does not mean "independent".
* **#480 depends on #477 being landed first.** Merged alone it brings #477's 3
  files along (7 files instead of 4). Sequenced (#477 → #480) the result is green
  at **69 passed**.
* **#473 and #474 must not be merged at all** on this branch. Both are rooted on
  #407 through #467 / #465, whose merge-base with the RC is `3f5ec20b` — #407's
  own tree — so `merge-tree` would carry the V2 Draft wholesale. Their real
  dependency is the Wave 7 bounded extraction (§5.2 node closure for #473,
  §5.4 receipt closure for #474), plus #431's four edits (#473) and #458's ruling
  on `deliveryId` / `run.not_found` (#474). **Nothing here merges #407.**

---

## 10. What this pass did not do

* No `main` merge, no release, no install, no service start/restart, no plugin or
  IM update, no OAuth flow, no credential, no Feishu call, no model call, no GPU.
* No edit to `tests/hermetic-known-failures.txt`.
* No edit to #471's, #472's, #456's, #431's, #458's or #484's files: the four
  composed files are the authors' bytes, and five digests pin that.
* No merge of #407, and no file copied out of it. #473 and #474 are described, not
  attempted.
* No product source written by this lane. Everything under
  `integrations/fleet-v1-rc-next/wave12/`, `docs/integration/FLEET-NEXT-RC-WAVE12*`
  and `tests/test_fleet_next_rc_wave12*.py` is documentation or a test.

| layer | reading |
| --- | --- |
| CODE | composed tree `f64d7af1`; 4 files, `+215/−18`, zero conflicts; blob-identical to #484's union while carrying the authors' commits as ancestry |
| UNIT | 31 gate/ledger tests, 97 batch-family tests, 56 plugin-package tests, 2 red→green ids, 11 wave-12 ledger tests |
| CI | run `40653792777`: **5/5 jobs success**, four shards `0 new / 0 flaky`, `+23` passed fully attributed (§7) |
| REAL_DEVICE | **not measured.** No device contacted; no payload built onto hardware; the symlink case is NOT_RUN on Windows. |
| PRODUCTION | **not measured.** NEXT stays shadow; nothing was started. |

---

## 11. Replaying

```
git worktree add -b integrate/fleet-v1-rc-wave12-20261028 <dir> cd1485f
cd <dir>
t1=$(git merge-tree --write-tree cd1485f pr/471)
c1=$(git commit-tree $t1 -p cd1485f -p pr/471 -m "compose pr/471")
t2=$(git merge-tree --write-tree $c1 pr/472)
c2=$(git commit-tree $t2 -p $c1 -p pr/472 -m "compose pr/472")   # 26ad7245, tree f64d7af1
```

The composition is an object in the object database; the branch is what makes it
reviewable. `git diff cd1485f $c2` must show exactly four files.
