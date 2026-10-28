# Fleet Next V1 — Wave 16 RC: #494 + #497 + #498 + #499

**Status: Draft RC, integration-only.** No `main` merge, no install/deploy, no
systemd/SCM/plugin Creator, no old-Fleet route/port/auth/database change, no
real IM/Feishu, no paid model or GPU. Next and Legacy real co-install is
**NOT_RUN** and needs separate authorization. This is **not a release**.

---

## 0. Identity

| | |
| --- | --- |
| base | #494 head `00503f91b019ce342af52f38b19bb3aa21dfff55` (tree `1c58a459…`) |
| composed source | `f02fa1688827111698062813eb94d1e86040cfd5` |
| composed tree | `d45cf478bcf829ae3d851974a1e7da018de58989` |
| #497 | `3db8ebf107f83981965cc888269b3b90d89ecb72`, base #493 `4f5320d`, merge `9c85f68` |
| #498 | `0096ba695f6c7bcd35dbb5a82957a1f5631d9058`, branch base #488 `009662f`, merge `af6bd42` |
| #499 | `02027c53f59100fbf43e0ac1f6d84f58561ee91c`, base #490 `06a8be6`, merge `f02fa16` |
| #495 QA | latest `f600fe6622b1e6942de79c90dca66abfa5f2b7ff`, independent, not edited |
| #496 evidence | `a34b811f11fe9b4a2db535a933309b47813c5811`, not merged |

The machine-readable counterpart is
`integrations/fleet-next-rc-wave16/composition.json`; the claim is asserted by
`tests/test_fleet_next_rc_wave16_ledger.py`.

---

## 1. Dependency verification and the #498 parent question

Measured:

```text
git merge-base 00503f9 3db8ebf  -> 4f5320d   (#493, already in #494)
git merge-base 00503f9 0096ba6  -> 009662f   (#488, already in #494)
git merge-base 00503f9 02027c5  -> 06a8be6   (#490, already in #494)
```

#497 and #499 are one commit each. **#498 is not a one-commit head whose parent
is #488**: it is a three-commit branch on #488:

```text
6aefafd  fix(next-monitor): carry the tick's verdict, so a stop is never a completion
f8e1b6f  fix(next-monitor): a stale cursor outranks any success word
0096ba6  test(next-monitor): register the stub module behind a cache read
```

Its immediate parent is `f8e1b6f`; its branch base is `009662f`. That is not a
conflict and it is not repaired by a merge/squash of one commit: the own delta
is **all three commits**, `009662f..0096ba6`, five files. The Wave 16 ledger
asserts the three-commit count and the exact base.

The three P0 heads were merged by ancestry, in the order #497 → #498 → #499,
with no conflicts (`git merge --no-ff`; merges `9c85f68`, `af6bd42`,
`f02fa16`). No V2 root was merged: #407 `9538238d…`, #473 `a097b27d…` and #474
`0ad3c078a…` are not ancestors.

### Own increments

| PR | files | delta |
| --- | --- | --- |
| #497 | 4 | `+948/−80` — monitor core / notifier / state + core test |
| #498 | 5 | `+994/−68` — activation + its four test files |
| #499 | 9 | `+964/−44` — source adapter + compat view + tests/doc |

Every file is compared to its author commit's own blob; the full list and
sha256 are in the manifest.

---

## 2. Historical ledgers, and the one tree-scoped correction

Wave 12 and Wave 13 are byte-identical to the base:

```text
wave12 1265351b07280ae2243f7f1897e58b6b22cdbab4
wave13 6d1be1e88698a1ee64930e80a7af2793b55ec6da
```

Wave 14's original ledger blob at `00503f9` is
`dc54f477fd0ff33304909587e15981731ffdb4f2`. Wave 16 legitimately changes
files that Wave 14 recorded (#497/#498/#499 are P0 fixes to #493/#488/#490),
so a HEAD-relative reading of the Wave 14 byte claim would call a legitimate
author change an unrecorded edit.

**One existing path was modified**:
`tests/test_fleet_next_rc_wave14_ledger.py`. Its byte claim is re-scoped from
"the working tree" to `WAVE14_HEAD = 00503f9`, and its composition delta is
read as `BASE..WAVE14_HEAD`. Nothing is deleted or weakened:

* the four Wave 14 author heads and their blobs are still asserted;
* the Wave 14 merge commits and the tree-scoped notifier evidence are kept;
* Wave 13's refusal tokens and `LEGACY_PREFIXES` remain;
* the original blob is recorded in the Wave 16 manifest.

The corrected file's blob is `ee4e64a20638982e3bfd90e58ad2d25d60a182e8`.
Result on this tree: Wave 12+13 **21 passed, 41 subtests**; Wave 14
**25 passed, 75 subtests**.

---

## 3. P0 gates on the composed source

These are integration readings, not single-PR unit claims.

| gate | reading | what it covers |
| --- | --- | --- |
| #497 core | **37 passed** | fake completed without native activity is UNKNOWN; an UNKNOWN receipt retries non-green; only a confirmed receipt reaches `DELIVERED`; typed CI subject; boot mismatch fails closed; CI draft/wrong SHA never reach the notifier |
| #499 adapter | **80 passed, 21 subtests passed** | compat view: denied grant costs zero reads, CI SHA mismatch speaks the monitor's word, explicit boot expectation is honoured, `read_request` rename |
| #480/#483 Legacy | **86 passed, 11 subtests passed** | every collision is blocked and never renamed aside; shadow write refusal |
| Wave 13 targeted family | **263 passed, 189 subtests passed** | producers, ledgers incl. Wave 16, Wave 7, plugin package, registration, batch family |
| `test_declared_implementations` | 3 failed, all baseline entries 51–53 | the two #490 reader-field failures are **gone** (#499 renames the envelope key to `read_request`) |
| `test_loader_identity` | 2 failed, baseline entries 66–67 | no new failure |
| `test_repository_layers` | 10 passed, 37 subtests | no new failure |
| `test_no_node_absolute_paths` | 5 passed, 5 subtests | no new failure |

### The #497 × #498 block — 3 exact failures

#498's runtime-tick tests **skip on the author head** because #493 is absent,
so the author's own CI never exercised them with the monitor body. On the
composed tree they execute and fail:

```text
FAILED tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealRefusalReachesTheSupervisor::test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it
FAILED tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealVitalsSurviveTheWrapper::test_a_configured_tick_carries_its_vitals_through_the_wrapper
FAILED tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::TheRealVitalsSurviveTheWrapper::test_a_second_process_does_not_inherit_the_first_ones_verdict
```

Two distinct causes, both reproducible:

1. `test_a_configured_tick_carries_its_vitals_through_the_wrapper` raises
   `KeyError: 'backlog'` at `document["health"]["reported"]["backlog"]`.
   #497's core `health` carries `last_tick`, `last_ok`, `cursor_age`,
   `consecutive_failures`, `stale`; `backlog` and `unknown_receipts` are
   row-level fields. The activation wrapper copies the producer's health
   unchanged into `reported`, so the test expects a key the producer never
   promised.
2. The other two compare full documents or `ticks_report` across processes,
   including wall-clock `last_tick`/`last_ok` (observed `2827.496` vs
   `2827.537`, and `0.804` vs `0.862`). Two interpreter runs cannot be equal
   on a real clock; the test needs a fixed clock or a stable-field comparison.

**BLOCKED — owner #498 author, with #497 author consulted.** The fix belongs
in #498's test/wrapper contract (use the actual vitals shape, or inject a
fixed clock), or in #497's health shape if the owner decides `reported` must
carry `backlog`. This RC does **not** edit either author file, does **not**
move these tests to `hermetic-known-failures.txt`, and does **not** claim 5/5
— this is **not 5/5**.

---

## 4. Notifier and Lark fixes are not reintroduced

* #496's evidence stands: #460's `send_text` is already in #494 through #481.
  This RC keeps the single notifier blob `7df27cb36…`; no second copy is
  added and #423's old head is not replayed.
* #415's Lark window fix `dd5919a` is already an ancestor of #494 and the test
  blob is unchanged; it is not reintroduced.
* #494's manifest still records the historical `notifier.head` (`d9b2948`)
  beside the composed `blob` (`7df27cb`). That is a historical record; Wave 16
  reads the blob and does not rewrite Wave 14.

---

## 5. Legacy boundary (#478)

* `00503f9..HEAD` contains no `fleet_mcp/`, `runtime/`, `services/`,
  `plugin/`, `deploy/`, `serverosd/` or `bin/` path.
* The `fleet_mcp` tree is byte-identical (`fcc3a9f59…`).
* #483 is additive (`e69fc03..644ee4a` only `A` entries).
* #407/#473/#474 are not ancestors; their V2/Legacy roots stay out.
* Old Fleet **0.1.0** is preserved. No port, socket, service, MCP URL/ID,
  OAuth audience, credential, database, state or lock is touched.

---

## 6. Release blocker matrix (#425 / #478 / #434)

### #425

| gate | status | evidence |
| --- | --- | --- |
| A. Legacy control preserved | SOURCE_ONLY | no `fleet_mcp` delta; real reachability NOT_RUN |
| B. Next side-by-side artifact | NOT_RUN | no install/switch |
| C. Agent Monitor E2E | PARTIAL | #497/#499 P0 green; #498 three runtime-tick tests blocked |
| D. real co-install / IM / switch | NOT_RUN | needs separate authorization |
| E. final RC preserves Legacy | SOURCE_GATE_PASS | #483 additive; tree unchanged |
| hosted 5/5 | **NOT_MET** | final head reading is in the PR description; #498 block is a known new failure |

### #478

| item | status |
| --- | --- |
| Legacy 0.1.0 preserved | yes |
| `fleet_mcp` tree unchanged | yes |
| #407/#473/#474 merged | no |
| real Legacy/Next co-install, Timer, App1 IM, `main` merge | NOT_RUN |

### #434

No real device, model, OAuth or four-line L4 closure was measured. **NOT_RUN**.

### Blockers

| blocker | owner | status |
| --- | --- | --- |
| #497×#498 runtime-tick | #498 author, with #497 author | BLOCKED |
| hosted 5/5 | Wave 16 integrator + #498 author | BLOCKED |
| #495 QA re-check | #495 QA | INDEPENDENT; `f600fe6` predates the three new P0 heads |
| real co-install / Timer / App1 IM / main merge | user authorization | NOT_RUN |

---

## 7. Reproducible build order

```bash
# 1. Base
git worktree add --detach /tmp/wave16 rc 00503f91b019ce342af52f38b19bb3aa21dfff55

# 2. Merge the three author heads by ancestry, never by patch
git merge --no-ff 3db8ebf107f83981965cc888269b3b90d89ecb72   # #497
git merge --no-ff 0096ba695f6c7bcd35dbb5a82957a1f5631d9058   # #498 (3 commits)
git merge --no-ff 02027c53f59100fbf43e0ac1f6d84f58561ee91c   # #499

# 3. Run the composite gates
python3 -m pytest -q tests/test_fleet_next_rc_wave16_ledger.py
python3 -m pytest -q tests/test_fleet_next_agent_monitor_core.py \
  tests/test_fleet_next_monitor_source_adapter.py \
  tests/test_fleet_next_parallel_plugin.py \
  tests/test_fleet_next_legacy_survival.py
python3 -m pytest -q tests/test_fleet_next_rc_wave12_ledger.py \
  tests/test_fleet_next_rc_wave13_ledger.py \
  tests/test_fleet_next_rc_wave14_ledger.py
```

A 5/5 claim is allowed only when the hosted five jobs pass and the shard-4
baseline gate reports `0 new`. The #498 runtime-tick block must be repaired by
its owner first; no baseline entry may be added for it.

---

## 8. NOT_RUN

`real Legacy/Next co-install`, `Timer install`, `App1 IM`, `main merge`, real
install, release, service start, OAuth/credential, Feishu/IM send, paid model,
GPU: **NOT_RUN**. Real device and production are not measured. This is **not a
release**. Old Fleet 0.1.0 is preserved.

Cross: #425 #478 #434 #494 #497 #498 #499 #495 #496 #493 #488 #490 #483 #407
#473 #474.
