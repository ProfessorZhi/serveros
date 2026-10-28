# Fleet V1 RC — Wave 7 (2026-10-28)

The measured record of one integration-only pass. Two things were asked for and
two things are answered here:

* **A.** compose a fresh, exact-SHA RC from **#469 + #470** and re-evaluate it:
  per-test regressions, the two merge gates, full hosted CI.
* **B.** map the *minimal V1 source deltas* of the three stacked V1 changes
  (`#461/#467` Node, `#466` Hub authority, `#465/#468` receipt truth) onto that
  RC — **without** merging their base, #407's 85-file V2 Draft, and **without**
  claiming V1 main readiness.

Nothing here merges to `main`, deploys, restarts a service, updates a plugin,
touches a credential, or talks to a real device. Every number below was produced
by a command in this document or is marked as unmeasured.

---

## 0. Identity and boundaries

| item | value |
| --- | --- |
| this branch | `integrate/fleet-v1-rc-wave7-20261028` |
| composed commit | `4847eb5a923e5dcf6bef769747b48358be6e0473` |
| composed tree | `4e31e4a2fb7870cd99cda52b64ba284b857fdea7` |
| parents | #469 `14306f3618f5acd8fb4a06b08f50f66acb5d295e` (RC round 2) + #470 `56fcb9276acc3a5228b79bbe27f32307f41f462e` |
| previous RC | #469 `14306f36` — hosted CI **4/7** |

Files owned by this lane, and the only ones this pass writes:

```
docs/integration/FLEET-V1-RC-WAVE7*.md          (this file, new)
tests/test_fleet_v1_rc_wave7*.py                (the landing ledger, new)
integrations/fleet-v1-rc-next/**                (receipt pointer)
```

Not edited: any product source, any test belonging to another PR, #469's own
branch, and `tests/hermetic-known-failures.txt` (the baseline is not widened —
not even by one line).

---

## 1. Inputs, verified live before composing

| PR | head | base |
| --- | --- | --- |
| #469 | `14306f3618f5acd8fb4a06b08f50f66acb5d295e` | `integrate/fleet-v1-rc-20261028` |
| #470 | `56fcb9276acc3a5228b79bbe27f32307f41f462e` | `feat/fleet-v1-batch-registration-20261028` (= #464's branch) |
| #461 | `b042f515b4` | #407 |
| #467 | `ccd045258e` | #461's branch |
| #466 | `263488289e` | `codex/fleet-v1-delivery-20261027` |
| #465 | `1a162c44b6` | #407 |
| #468 | `7d2278ecf7` | #465's branch |
| #407 | `9538238d6a` | — (the 85-file V2 Draft all three sit on) |

`merge-base(pr/407, composed) = 3f5ec20b`. That base matters in §5: it is what
makes the "did the RC touch this file" question answerable per file.

---

## 2. Composition

```
git merge-tree --write-tree 14306f36 refs/remotes/pr/470   # tree 4e31e4a2
git commit-tree 4e31e4a2 -p 14306f36 -p pr/470             # 4847eb5a
```

Zero conflicts. Diff against #469:

```
tests/test_fleet_v1_milestone_batch_e2e_integration.py | 873 +++++++++++
tests/test_fleet_v1_milestone_batch_registration.py    |  ... 
integrations/fleet-v1-milestone-batch/**               |  ...
6 files changed, 1399 insertions(+), 57 deletions(-)
```

The whole of #470 is additive to the RC: nothing in the RC was reverted, so any
red that survives on `4847eb5a` is a red on the composition, not a re-introduced
one.

---

## 3. What #470 actually repaired — the four NEW ids from #469's hosted run

#469's hosted CI was 4/7 with four NEW failures. On the composed tree, in one
process over the five batch/plugin files, the reading is **110 passed, 3 failed**
(`30 subtests passed`). Against the four ids:

| id (shard) | on `4847eb5a` | attribution |
| --- | --- | --- |
| `test_the_aggregator_refuses_a_notifier_without_the_seam` (shard2) | **passes** | #470 landed the aggregator side |
| `test_the_real_notifier_without_the_seam_is_refused_up_front` (shard3) | **passes** | same seam, real-class half |
| `test_the_clean_package_is_not_caught_by_the_layout_rule` (shard2, `7 != 6`) | still red | #456's plugin-package count, not the seam |
| `test_a_plan_the_live_contract_accepts_becomes_a_registration` (shard4) | still red | `COMMANDER_REFUSED: SESSION_NOT_FOUND` at `registration.py:498` |

The first two are the pair #467's commit `ccd0452` and #459's README both
predicted would go green the moment the seam landed on the owner's class — so
this is the expected flip, not a test relaxed to fit. **No test was edited to
make them pass.**

The two remaining ids are **not** this lane's files:

* `7 != 6` lives in `tests/test_fleet_next_plugin_package.py`, which counts the
  plugin package members; #470 adds two files to the package. Owner: #456.
* `SESSION_NOT_FOUND` is raised inside
  `integrations/fleet-v1-milestone-batch/registration.py` against #456's
  commander contract. Owner: #470/#464 with #456.

Local Windows adds a third red that hosted Linux does not see:
`TheSkillSourceIsComplete::test_a_symlinked_member_is_refused` fails with
`OSError: [WinError 1314]` — creating a symlink needs a privilege this machine
does not grant. **NOT_RUN on Windows**, expected green on Linux; it is not
counted against the composition.

**Dependency gate (precise, not a wait):** the composition is a valid RC at
`4847eb5a` the moment #456 publishes the package count that matches its own
added files, and #470/#456 publish the registration fixture with a commander
snapshot that carries the session. Until then the RC's block is *exactly* those
two ids, and the hosted run below reports them as the only NEW failures in the
batch/plugin family.

**Confirmed hosted** (run `40646194204`, §7): four shards, `NEW FAILURES` totals
**2**, and the bidirectional id diff against #469's run has an **empty**
regression side. The local reading above and the hosted reading agree id for id.

---

## 4. The two merge gates, re-evaluated on Wave 7

```
python -m pytest -q tests/test_fleet_v1_rc_next_packaging.py \
                    tests/test_fleet_v1_rc_next_notifier_seam.py
22 passed in 2.48s          (13 packaging + 9 seam)
```

Both gates were green on #469 and are still green after #470, which is the
reading that matters: #470 added a whole new integration file (873 lines) and a
registration module without moving either gate. Neither gate was edited in this
pass.

---

## 5. The three V1 stacks — minimal source delta onto this RC

### 5.1 Method, and why a patch does not work

Each stack is rooted on #407, whose tree is a Draft of 85 files. `git merge-tree`
against any of them would carry all 85 into the RC, so the stacks were *not*
merged. Two cheaper things were measured instead.

**First, the file-level question: did the RC touch the files these stacks own?**
For every file the three stacks modify, the composed tree's blob was compared to
`merge-base(pr/407, composed)`:

| file | base | RC | #407 |
| --- | --- | --- | --- |
| `fleet_mcp/fleet.py` | `d2241c0c` | `d2241c0c` | `8ca8a264` |
| `fleet_mcp/node/connector.py` | `614c6e4c` | `614c6e4c` | `368360ae` |
| `fleet_mcp/cloudcli_native.py` | `43d9980f` | `43d9980f` | `2b579657` |
| `fleet_mcp/providers/cloudcli.py` | `24986f48` | `24986f48` | `2e8445ca` |
| `fleet_mcp/mcp_http.py` | `688894a9` | `688894a9` | `688894a9` |
| `fleet_mcp/registry.py` | `86c72a81` | `86c72a81` | `86c72a81` |
| `fleet_mcp/owner_access.py` | `ef07b231` | `ef07b231` | `ef07b231` |

**The RC has not touched one of them.** At the file level the three stacks are
orthogonal to everything the RC's thirteen PRs did, which is why a bounded
extraction is possible at all.

**Second, the patch question:** `git diff pr/407 <stack head>` applied to the RC.
It fails on exactly the files #407 itself changed — the stacks' hunks are written
against #407's post-edit text, not against the base:

```
node.patch     fleet_mcp/fleet.py            patch does not apply
receipt.patch  fleet_mcp/cloudcli_native.py  patch does not apply
               fleet_mcp/fleet.py            patch does not apply
               fleet_mcp/providers/cloudcli.py  patch does not apply
hub.patch      (all three files apply cleanly)
```

So the extraction is **whole-file for a named, minimal file set**, and every file
added from #407 is justified by a failing test that names it. The closure was
found empirically (run → read the refusal/exception → add the one file it names →
run again), not guessed; §5.2–§5.4 give the ladder for each stack.

Everything below ran in a throwaway worktree at `4847eb5a` (test-only). Nothing
from it is committed.

### 5.2 Node stack — #461 `b042f515` ⊂ #467 `ccd045258e`

**#467 is the authoritative head, and #461's own test is deliberately gone.**
#467 contains #461 (`merge-base(461,467) = b042f515` = #461's head) and
`ccd0452` deletes `tests/test_fleet_v1_cut_a_node_standalone.py` (538 lines) with
a written reason:

> `test_fleet_v1_cut_a_node_standalone.py` loads the release tool and derefs it
> unconditionally; on the combined tree the loader yields None, so its
> cross-file closure check raises AttributeError instead of measuring. … Carrying
> someone else's gate into this PR would turn their pending edit into a red check
> that reads as mine.

That is also why #461 must not be extracted on its own: extracting it adds a
538-line test that its own successor removes.

**Own delta vs #407** — 9 files: `fleet_mcp/fleet.py`, `fleet_mcp/node/connector.py`,
`fleet_mcp/owner_access.py`, five `integrations/fleet-v1-l2-release/*`, and
`tests/test_fleet_v1_node_dist_pin.py`.

**Already in the RC:** `integrations/fleet-v1-l2-release/**` arrived with **#431**
(one of this RC's thirteen). Four of the five files are byte-identical to #467's;
`l2_release.py` differs by +9/−5 — the pin reduction.

**Missing dependencies found by running:**

| step | reading | file it named |
| --- | --- | --- |
| #467's 5 files only | `test_fleet_v1_node_dist_pin.py` **10 failed, 5 passed** — `Refused: the pin lists module(s) the closure no longer reaches: ['fleet_mcp.principals']` | `fleet_mcp/principals.py` (a **#407-only new file**, 85 lines, absent at the base and in the RC) |
| + `principals.py` | `3 failed, 12 passed` | — (the pin now measures) |
| + `fleet_mcp/config.py` (+7 in #407) | `12 passed` modulo the Windows-only defects below | `config.py` is the only importer of `principals`; without it the pin's new entry is unreachable |

`principals.py` is self-contained (imports only `errors`), so the closure stops
there. Final node closure: **5 files from #467 + 2 files from #407**.

**Windows-only defects in #467's own test** (both are the *test*, not the
product; hosted Linux CI cannot see either):

1. `tests/test_fleet_v1_node_dist_pin.py:110` (and the same line in a second
   class, `:124`) builds member names with
   `str(p.relative_to(ROOT))`. On Windows that yields `fleet_mcp\node\connector.py`,
   and `l2_release._module_of` splits on `"/"` (`l2_release.py:1477`), so every
   member becomes an unpinned "new hub coupling":
   `new hub coupling in the node artifact: ['fleet_mcp\\__init__', …]`.
   The builder itself names members with
   `path.relative_to(root).as_posix()` (`l2_release.py:428`) — the test
   reconstructs the name a second way. **Flip proof:** changing only those two
   lines to `.as_posix()` takes the file from 3 failed to 1.
2. `tests/test_fleet_v1_node_dist_pin.py:205` runs the installed payload with
   `env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}`. That
   replaces the environment instead of adding to it, so on Windows
   `Path.home()` raises `RuntimeError: Could not determine home directory.`
   **Flip proof:** `env={**os.environ, …}` in the same copy gives **15 passed,
   0 failed**. Both fixes together were measured; each was also measured alone.

**Cross-PR reds this extraction produces — both in #431's files, neither in the
RC's own test set today:**

| file | reading on RC+cut | cause |
| --- | --- | --- |
| `tests/test_l2_source_standalone_contract.py` | **3 failed** (`[XPASS(strict)]`) | the three `xfail(strict=True)` markers say "when it lands this XPASSes and the strict marker must be deleted in the same change that lands the cut". The cut landed → XPASS → strict ⇒ failure. Exactly as designed. |
| `tests/test_fleet_l2_release_separation.py` | **47–50 failed** (43 of them one message) | `allow_external_in_repo names ['fleet'], which the l2-node closure does not reach`; plus the "17 pinned modules" expectations, and `test_deleting_the_lazy_imports_on_a_copy_flips_the_packaging_flags` which asserts `cut, "edge A has moved; re-measure HANDOFF-407 before trusting its table"`. |

The second file **does not exist in #467's tree at all** — it is #431's, and #467's
commit message names only the first. So the "two tests go red" note in `ccd0452`
is one short: the real count is three files of expectation (`test_l2_source…`,
`test_fleet_l2_release_separation…`, and #461's deleted standalone test).

On the same eight-file node family, no regression was introduced outside #431's
files: `test_fleet_owner_access.py` and `test_node_mcp.py` are **7 failed / 41
passed with the identical seven ids** on both the clean RC and the extracted tree
(the three `LocalPlanning` symlink cases are Windows NOT_RUN; the two
`test_node_mcp` contract cases are inherited).

**Dependency gate:** the node cut cannot land until #431's owner (a) deletes the
three `xfail(strict=True)` markers in `test_l2_source_standalone_contract.py`,
and (b) updates `test_fleet_l2_release_separation.py` for a 13-entry pin, drops
the `("fleet",)` allowance, and replaces the "edge A is still uncut" guard with
an "edge A is cut" assertion. Those are four small edits in two files owned by
someone else; this lane will not make them.

### 5.3 Hub authority — #466 `263488289e`

Own delta vs #407: **3 files**, +613/−19 — `fleet_mcp/identity_adapter_port.py`
(new), `fleet_mcp/registry.py`, `tests/test_fleet_v1_hub_authority_standalone.py`.

`git apply --check` passes without `-3`: #407 did not touch `registry.py`
(its blob is the base blob), so there is nothing to reconcile.

| reading | result |
| --- | --- |
| `tests/test_fleet_v1_hub_authority_standalone.py` | **19 passed** |
| registry/identity family, extracted | 7 failed / 141 passed / 92 subtests |
| the same family, clean RC | 7 failed / 141 passed / 92 subtests |
| id-level symmetric difference | **empty in both directions** |

**Zero regressions, zero missing dependencies.** This is the one stack that is
ready to land on its own as soon as its owner asks for it; it needs no #407 file
and no RC file updated.

### 5.4 Receipt truth — #465 `1a162c44b6` ⊂ #468 `7d2278ecf7`

**#468 is the authoritative head** (`merge-base(465,468) = 1a162c44` = #465's
head) and it keeps both test files (#465's projection test is not deleted), so
the pair lands together.

Own delta vs #407: **6 files** — `fleet_mcp/fleet.py`, `fleet_mcp/cloudcli_native.py`,
`fleet_mcp/mcp_http.py`, `fleet_mcp/providers/cloudcli.py`, and the two tests.

**Missing dependencies, found by running:**

| step | reading | file it named |
| --- | --- | --- |
| #468's 6 files only | collection error — `ModuleNotFoundError: No module named 'fleet_mcp.principals'` (from `fleet.py:62`) | `fleet_mcp/principals.py` (#407-only, new) |
| + `principals.py` | **62 passed, 2 failed** — `delivery_receipt(record)` → `ValueError: send acknowledgement has no execution reference`; and a control-character `deliveryId` is not refused | `fleet_mcp/dispatch.py` (#407) — `delivery_receipt` lives there |
| + `dispatch.py` | **64 passed, 0 failed** — identical to the reading on #468's own tree | — |
| the 8-file family | 7 failed / 382 passed | `test_fleet_mcp_disclosure.py` red |
| + `disclosure.py` | disclosure green | — |

**And one dependency of the third kind — a test file the RC has forked.**
`tests/test_fleet_cloudcli_full_inheritance.py` exists in both trees but they
have diverged:

| | RC's copy | #407's copy |
| --- | --- | --- |
| expected authored tools | eleven (44 served) | twelve (45 served) |
| `_current_input_schema()` | absent | present — substitutes the AC-386 1.41 `run_get` contract (`sha256 eb8e8c12…`) for the 1.40 capture entry |
| `_missing_constraints()` | recurses into `anyOf/oneOf/allOf` branches | no branch recursion |

Neither copy is a superset. With #468's `cloudcli_native.py` (whose `run_get`
schema carries `oneOf: [{required:[runId]}, {required:[deliveryId]}]`) and the
**RC's** copy, three tests go red, all with the same message:
`run_get/required: device requires 'runId'; recorded does not`. Taking **#407's**
copy instead makes the file **145 passed** — but that copy expects twelve
authored tools, which the RC does not have, so it trades three reds for one
(`test_the_served_surface_is_the_twelve_plus_the_thirty_one`).

**`fleet_mcp/tools.py` is NOT required.** Probe: with #407's test file and the
RC's `tools.py` (eleven tools), only the count test fails; the three
`RecordedTable`/routing reds are gone. Taking #407's `tools.py` to satisfy the
count instead adds five RC-side reds (`test_fleet_mcp_surface`,
`test_fleet_create_session` ×2, `test_fleet_mcp_contract`), i.e. it is a worse
trade. So the minimal receipt closure is:

```
#468: fleet.py, cloudcli_native.py, mcp_http.py, providers/cloudcli.py,
      tests/test_fleet_v1_receipt_truth_projection.py,
      tests/test_fleet_v1_upper_receipt_truth.py
#407: principals.py (new), dispatch.py, disclosure.py,
      tests/test_fleet_cloudcli_full_inheritance.py
```

**Cross-PR contradiction with #458 — a product decision, not a test bug.**
`tests/test_l2_cloudcli_control_matrix.py` came with **#458** (also one of this
RC's thirteen). With #468's code two of its tests fail, and both failures are the
kind that needs an owner ruling:

| test | #458's claim | #468's behaviour |
| --- | --- | --- |
| `test_a_run_read_addresses_a_run_and_never_a_delivery` | a run read addresses a run **and never a delivery**: `set(arguments) == {"runId", "session", "wait"}` | `run_get` publishes `{deliveryId, runId, session, waitSeconds}` — `deliveryId` becomes a legal identifier for a run read |
| `test_a_run_not_found_keeps_its_code_and_loses_the_devices_reason` | the device's `retryable: false` and `details.reason` must be dropped (`extra == {}`) | they survive — receipt truth says a device fact is not discarded. #458's own assertion text anticipates it: *"update the `run.not_found` row instead of leaving it pessimistic"* |

Measured on the minimal closure above: `test_l2_cloudcli_control_matrix.py`
**1 inherited red + 2 from #468**.

**Dependency gate:** the receipt stack cannot land until (a) whoever owns
`tests/test_fleet_cloudcli_full_inheritance.py` reconciles the fork — the RC's
copy needs `_current_input_schema` (or the recorded table needs the 1.41
override) — and (b) #458's owner rules on `deliveryId` for `run_get` and on
`run.not_found`'s extras. Both are decisions about behaviour, which is why this
lane documents them rather than editing around them.

### 5.5 Summary of the mapping

| stack | files to take | files it needs from #407 | reading on the RC | lands alone? |
| --- | --- | --- | --- | --- |
| #466 Hub | 3 | none | 19 passed, 0 regression | **yes** |
| #461/#467 Node | 5 (of which 4 already in RC via #431) | `principals.py`, `config.py` | 15 passed after the two Windows-only test fixes; #431's two files red | after #431's four edits |
| #465/#468 Receipt | 6 | `principals.py`, `dispatch.py`, `disclosure.py`, `test_fleet_cloudcli_full_inheritance.py` | 64 passed (its own), 2 + 1 red on #458's matrix | after the test fork + #458's ruling |

**None of this is "V1 main readiness".** #407's 85 files remain unlanded and
unreviewed here; the three stacks each reach into #407 for a *named, minimal*
set (2, 2 and 4 files respectively), and each of those files is a piece of the
V2 Draft that a V1 cut would have to justify on its own.

---

## 6. Evidence ledger

| layer | reading |
| --- | --- |
| CODE | composed tree `4e31e4a2` on `4847eb5a`; three stacks extracted into a throwaway worktree with the file sets above; every dependency in §5 named by a failing test, then re-run |
| UNIT | gates 22 passed (13 + 9); batch/plugin family 110 passed / 3 failed; hub 19 passed with an empty symmetric difference against the clean RC; receipt 64 passed matching #468's own tree; node 12 passed (15 after the Windows-only test fixes) |
| HOSTED_CI | this branch's runs `40646194204` (`eea1ee5`) and `40647887120` (`d037588`, prose only) — §7: **4 → 2** baseline-external ids, zero regressions by bidirectional id diff, `+39` passed fully attributed, and the same two ids in both runs. Run colour is `failure` because the two remaining ids are owned elsewhere. |
| REAL_DEVICE | **not measured.** No device was contacted; the L2 artifacts were never installed on hardware. |
| CLIENT_E2E | **not measured.** No browser, no plugin update, no IM delivery. |
| PRODUCTION | **not measured, and out of scope for this lane.** |

Windows NOT_RUN (cannot be measured here, and are *not* claimed green):
`fcntl`-dependent collection (`tests/test_dispatch_attempts.py`,
`tests/test_dispatch_reconcile_gate.py` both abort at import with
`ModuleNotFoundError: No module named 'fcntl'`), symlink creation
(`WinError 1314`), and anything requiring systemd.

---

## 7. Hosted CI

Branch `integrate/fleet-v1-rc-wave7-20261028`. Four shards; the reading that
counts is the `== baseline gate ==` block per shard (`NEW FAILURES` are
baseline-external ids only).

Run `40646194204` (`Tests`, `pull_request`, head
`eea1ee591bdd2bad6e321f3ce5ab5ad138d016a2`). Overall conclusion **failure**, and
the only reason is the two baseline-external ids below — both owned elsewhere
(§3). The gate is the authority here, not the run colour.

| shard | job | pytest summary (main run) | `== baseline gate ==` | new ids |
| --- | --- | --- | --- | --- |
| 1/4 | `122675463946` | 16 failed, **2974 passed**, 42 skipped, 3341 subtests, 104.21s | `16 failing, 1 new` | `test_fleet_v1_milestone_batch_registration.py::TheLiveCommanderContractIsConsumedAsIs::test_a_plan_the_live_contract_accepts_becomes_a_registration` |
| 2/4 | `122675463997` | 13 failed, **3408 passed**, 9 skipped, 2812 subtests, 131.86s | `11 failing, 1 new` | `test_fleet_next_plugin_package.py::ThisIsNotTheNativeServerOSPlugin::test_the_clean_package_is_not_caught_by_the_layout_rule` |
| 3/4 | `122675464035` | 10 failed, **3338 passed**, 27 skipped, 1 xfailed, 2253 subtests, 263.78s | `10 failing, **0 new**` | — |
| 4/4 | `122675464141` | 22 failed, **3014 passed**, 37 skipped, 3 xfailed, 1125 subtests, 212.26s | `22 failing, **0 new**` | — |

### 7.1 The two `…_without_the_seam` ids are gone — hosted, not just locally

Compared against #469's own run `40638310862` (same workflow, head
`14306f36`), which reported the four ids the Wave 7 brief quoted:

| shard | #469 `14306f36` gate | wave 7 `eea1ee5` gate |
| --- | --- | --- |
| 1/4 | `9 failing, 0 new` | `16 failing, 1 new` |
| 2/4 | `17 failing, **2 new**` (clean-package 7≠6; `…_aggregator_refuses_a_notifier_without_the_seam`) | `11 failing, 1 new` |
| 3/4 | `17 failing, **1 new**` (`…_real_notifier_without_the_seam_is_refused_up_front`) | `10 failing, **0 new**` |
| 4/4 | `20 failing, **1 new**` (`…_a_plan_the_live_contract_accepts…`) + 2 flaky | `22 failing, **0 new**` |

**4 → 2.** Both repaired ids are `…_without_the_seam` bridge tests, which is what
`ccd0452` and #459's README predicted #470 would fix without touching a test:
nothing in §2's diff edits them. Per-shard gate counts are **not** comparable
across the two runs — the two new test files reshuffle `scripts/hermetic_tests.py`
shards (the registration id moved from shard 4 to shard 1) — so the comparison is
by id set, not by row.

### 7.2 Per-test regression check — the bidirectional id diff

Union of every `FAILED`/`ERROR` id across the four shards, prefix-stripped, both
trees: #469 **61** distinct, wave 7 **57**.

* **only on wave 7 (i.e. regressions): none.**
* only on #469: 4 — the two repaired seam bridge tests, plus
  `test_codex_app_server_provider.py::test_a_dead_server_is_a_stopped_provider_and_not_a_closed_session`
  and `test_fleet_core_lite_transport.py::TheRealConnectorRefusesOnItsOwnTerms::test_a_badly_signed_request_does_not_reach_the_snapshot`,
  the two shard-4 `FLAKY (passed on retry)` ids from #469's run. They passed
  outright in `40646194204` (`0 flaky` in all four shards) and failed once, then
  passed on retry, in `40647887120` (`24 failing, 0 new, **2 flaky**`, shard 4).
  Same ids as #469's flakes, on a tree that changes none of their files: they are
  **flaky, not fixed**, and the gate classifies them as such in both runs.

### 7.3 Positive control: the new tests really ran

Absence from `NEW FAILURES` is not evidence that a file executed, so the check is
the four-shard **passed** total:

```
#469 14306f36 : 3198 + 2828 + 3181 + 3488 = 12695
wave 7 eea1ee5: 2974 + 3408 + 3338 + 3014 = 12734      Δ = +39
```

and the Δ decomposes exactly, each term measured rather than assumed:

| term | count | how measured |
| --- | --- | --- |
| `tests/test_fleet_v1_milestone_batch_e2e_integration.py` (#470, new file) | +26 | `pytest --collect-only` on both worktrees |
| `tests/test_fleet_v1_rc_wave7_ledger.py` (this lane) | +9 | same |
| the two repaired seam ids | +2 | id diff §7.2 |
| #469's two flaky ids, passing outright in *this* run | +2 | gate line `0 flaky` |
| **sum** | **+39** | matches the observed Δ |

The last term is the reason the Δ is a positive control and not a regression
signal: in run `40647887120` the same two ids failed once and passed on retry,
so that run's Δ is `+37`. The **id** diff in §7.2, not the Δ, is what answers
"did anything break" — the Δ only answers "did the new files run".

### 7.4 The branch tip repeats the same two ids

The run above is on `eea1ee5`, the lane's last content commit. Run
`40647887120` is on the tip `d037588`, which adds only this section's prose:

| shard | summary | gate |
| --- | --- | --- |
| 1/4 | 16 failed, 2974 passed, 3341 subtests | `16 failing, 1 new` — registration id |
| 2/4 | 13 failed, 3408 passed, 2812 subtests | `11 failing, 1 new` — clean-package id |
| 3/4 | 10 failed, 3338 passed, 2253 subtests | `10 failing, 0 new` |
| 4/4 | 24 failed, 3012 passed, 1125 subtests | `24 failing, 0 new, 2 flaky` |

Two runs, same tree modulo prose, **the same two ids**. That is the stability
reading: the block is not noise, and neither run has a baseline-external id this
lane introduced.

The other three workflows did **not** run for this PR and are NOT_RUN, not green:
`serveros-native-linux-contract`, `serveros-native-windows-contract` and
`fleet-next-isolated-contracts` are all path-filtered, and the diff against this
PR's base (`integrate/fleet-v1-rc-next2-20261028`) touches none of their paths.
#469's branch does trigger the linux contract because it is diffed against `main`,
which is a different question from "did this composition break it".

---

## 8. What this pass did not do

* No `main` merge, no deployment, no service restart, no plugin or IM update, no
  OAuth flow, no GPU.
* No edit to `tests/hermetic-known-failures.txt`. The two ids that stay red in
  §3 are reported, not absorbed.
* No edit to #456's, #458's, #431's, #467's or #468's files. Where their
  expectations conflict with the cut, the conflict is named and left to its
  owner.
* No claim of REAL_DEVICE, CLIENT_E2E or PRODUCTION.

---

## 9. Replaying

```
git merge-tree --write-tree 14306f36 refs/remotes/pr/470   # 4e31e4a2
git commit-tree <tree> -p 14306f36 -p pr/470 -m "compose pr/470 (Wave7)"
python -m pytest -q tests/test_fleet_v1_rc_next_packaging.py \
                    tests/test_fleet_v1_rc_next_notifier_seam.py     # 22 passed
python -m pytest -q tests/test_fleet_v1_milestone_batch.py \
                    tests/test_fleet_v1_milestone_batch_integration.py \
                    tests/test_fleet_v1_milestone_batch_registration.py \
                    tests/test_fleet_v1_milestone_batch_e2e_integration.py \
                    tests/test_fleet_next_plugin_package.py          # 110 passed, 3 failed
```

For a stack, add its files into a clean worktree of that commit and run its own
test file first — the node and receipt closures were found exactly that way, one
named file at a time.
