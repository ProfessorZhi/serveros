# Fleet Next Wave 15 — prerequisite QA: the notifier seam is present, the QA pin was old

**Status: independent dependency gate. Draft evidence only.** No real
Legacy/Next co-install, Timer, App1 IM, `main` merge, install, release, service
change, OAuth/credential, Feishu/IM send, paid model or GPU. This is **not a
release**. The old Fleet **0.1.0** is preserved.

---

## 0. Identity

| | |
| --- | --- |
| gate base | `main` `e69fc0337ae5d33000478e3d5162077f525bc8cc` — independent; the RC is read by SHA, not merged into this branch |
| RC under test | #494 head `00503f91b019ce342af52f38b19bb3aa21dfff55` |
| composed source / tree | `7ff573db7300867fd37b673a59b0101e7f87dab6` / `4f28f4ef78e47790f874fe046a457c2d3defc7d0` |
| RC hosted CI | run `40669351645`, **4/5**; shard 4 `25 failing, 2 new, 1 flaky` |
| QA input | #495 head `75e25724424f430c9058638f059cc8787f4ab963` |
| #423 original head | `d9b2948a62609e16007f2fe0ae288f9652d64cd1` |
| #460 send seam | `4fc9fcbc677f278d10caeb34d55184545810a13a` |
| #460 final head | `1dbeda5f87bce23b2134f869f6379667bc9847b9` |
| #481 Batch CLI | `9b7710a26180e1e6d00ce597827d447d0d2348b7` |
| #488 activation | `009662f36a2a5f8e11c51ae517834379a91a7770` |

The machine-readable counterpart is
`integrations/fleet-next-wave15-prereq-qa/composition.json`; the claim is
asserted by `tests/test_fleet_next_wave15_prereq_qa.py`.

---

## 1. The finding: `NOTIFIER_SEAM_MISSING` is a QA pin artifact

#495 constructs #481's real `BatchAggregator` with the `MilestoneNotifier`
materialised from **#423's original head `d9b2948`**. That head has no public
`send_text` (`blob 72fcec8d5…`), so the aggregator refuses with
`NOTIFIER_SEAM_MISSING`.

The Wave 14 RC does not carry that notifier. It carries `4fc9fcb`
(`blob 7df27cb…`), #460's public send seam, which #481 merged at
`ba76a80411ca4fa3d94f05105877bc1e8fb377ff` (second parent `4fc9fcb`).
The same blob is present unchanged at #481 `9b7710a`, #488 `009662f`,
#492 `a00fabc` and #494 `00503f9`.

### Runnable RED / GREEN

`seam_probe.py` extracts each frozen commit with `git archive` into a
throw-away directory and runs the **same real** `BatchAggregator`:

```text
old  d9b2948  send_text False -> NOTIFIER_SEAM_MISSING   (RED, #495's pin)
new  4fc9fcb  send_text True  -> AGGREGATOR_OK            (GREEN, the RC)
```

The test asserts both readings, the exact blob ids, the `4fc9fcb` ancestry
into #481 and the RC, and that exactly one `milestone_notify.py` source is in
the RC.

### Verdict

**QA_PIN_ARTIFACT.** This is not a missing source dependency and no author
file is changed. The correction belongs to #495 QA: pin `4fc9fcb`
(`blob 7df27cb…`) or read the #494 tree, then re-run §4.3. If the QA keeps
`d9b2948` as its notifier, it is measuring #423 before the seam was added, not
the composition.

No source owner is asked to replay #423: the seam is already an ancestor.

---

## 2. #460: exact source, merge base and conflict

| | |
| --- | --- |
| `send_text` source | `4fc9fcb` — `feat(notify): a public send seam…` |
| its parent | `d9b2948` (#423 original head) |
| merged into #481 | `ba76a80`, second parent `4fc9fcb` |
| #460 final head | `1dbeda5` — adds the newline fix on top of `4fc9fcb` |
| `1dbeda5` in #494? | **No** |
| merge base with #494 | `4fc9fcb` |
| conflict probe | `git merge-tree --write-tree 00503f9 1dbeda5` → clean (rc 0) |

So `1dbeda5` is an **optional clean bump**, not the seam. A future RC that
needs the newline fix may compose it without conflict; a future RC that only
needs `send_text` does not. This gate records the option and does not compose
it, because composing it changes the notifier blob from `7df27cb…` to
`8ff93b0…` and would require a fresh author-owned decision and a fresh CI run.

The #425 issue still pins #423 as `eb794be…`. That commit is an ancestor of
the RC but is **stale** relative to the live #423 head `d9b2948` and the
composed notifier `4fc9fcb`; the issue input should be re-read, not trusted as
the current notifier identity.

---

## 3. Pins, dependency order and RC composition order

### Dependency order (what must be in the object graph)

| order | PR | commit | role | in RC |
| --- | --- | --- | --- | --- |
| 1 | #423 | `d9b2948` | notifier base | yes |
| 2 | #460 | `4fc9fcb` | public `send_text` seam | yes, via #481 `ba76a80` |
| 3 | #481 | `9b7710a` | Batch CLI | yes |
| 4 | #488 | `009662f` | activation / Batch adapter | yes |

Optional, not in RC:

| PR | commit | delta | merge into RC |
| --- | --- | --- | --- |
| #460 | `1dbeda5` | newline fix | clean |

### RC composition order (what #494 actually merged)

| step | PR | commit | merge |
| --- | --- | --- | --- |
| 0 | #492 | `a00fabc` | Wave 13 base |
| 1 | #493 | `4f5320d` | `b23cda6bb` |
| 2 | #488 | `009662f` | `efde75022` |
| 3 | #490 | `06a8be6` | `a5e55b93a` |
| 4 | #483 | `644ee4a` | `7ff573db7` |
| 5 | #494 | `00503f9` | ledger head |

The dependency order and the RC composition order are different questions.
`4fc9fcb` reached the RC through #481, which was already an ancestor of #492;
it did not need a separate #460 merge. The four author merges are on top of
#492, in the order above.

---

## 4. Pending interfaces — owned elsewhere, not patched here

This gate does not edit #493/#488/#490 source. The following are recorded as
owner handoffs with **no published fix SHA** at this run:

| id | owner | why |
| --- | --- | --- |
| #490 request naming | #490 author, with the separate fix Agent | two new `DeclaredFieldsHaveReaders` failures on #494 shard 4; Wave 14 `BLOCKED_OWNER` |
| #493 unconfirmed→settled | #493 author | #495 §4.1: `UNCONFIRMED` becomes `SETTLED` on the next tick |
| #493 CI `device_ref` | #493 and #423 authors | #495 §4.2: empty `device_ref` is refused by #423 |
| #490/#493 signature drift | #490 and #493 authors | #495 §4.4/4.5: kwargs and reason vocabulary |
| #488 activation false success | #488 author | #495 §4.6: a blocked tick becomes exit 0 |
| #495 notifier pin | #495 QA | this gate's RED/GREEN; pin `4fc9fcb`, not `d9b2948` |

### #415 Lark window

`dd5919a9256246570e611a7fa86aea28b46ef14d` **is an ancestor of #494**, and
`tests/test_direct_lark_notify.py` has the identical blob in #415 and #494.
The time-window fix is already in the RC; the remaining #415 status is a Draft
merge-process fact, not an RC content gap. It does not need a Wave 15 replay.

### The 4/5 limit

#494 is **4/5**, not 5/5. Shard 4's baseline gate is
`25 failing, 2 new, 1 flaky, 0 listed-but-passing`; the two new failures are
#490's request naming collision. This gate does **not** claim 5/5 and does not
add the two failures to `hermetic-known-failures.txt`.

### The main-based gate's external new failure

The first hosted run of this gate (`40675633332`) read 4/5 with two new
failures:

1. this gate's own `GATE_BASE` ancestor assertion — the hosted checkout is
   depth-1, so the base commit may be absent even though the fact is true. The
   assertion was removed; independence is asserted as the negative fact
   ("the RC is not an ancestor of this branch");
2. `tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`
   — the #415 Lark window. `main` does not contain #415 `dd5919a`; #494 does.

After the fix the gate's own tests are green. The only remaining new failure
on a main-based branch is **#415**, an external merge-order blocker owned by
that PR. This gate does not fix it, does not add it to
`hermetic-known-failures.txt`, and does not claim 5/5. The RC path is to merge
#415 (or base Wave 15 on #494, which already contains it).

---

## 5. Dual-track resource isolation (#478)

* The old Fleet **0.1.0** is preserved. No port, socket, service, MCP URL/ID,
  OAuth audience, credential, DB/state/lock or route is touched.
* `a00fabc..00503f9` contains no `fleet_mcp/`, `runtime/`, `services/`,
  `plugin/`, `deploy/`, `serverosd/` or `bin/` path; the `fleet_mcp` tree is
  byte-identical (`fcc3a9f59f5f50d675fa1da9d92df752ce2e3ef9`).
* #483 is additive: `git diff --name-status e69fc03..644ee4a` contains only
  `A` entries.
* #407 `9538238d…`, #473 `a097b27d…` and #474 `0ad3c078a…` are **not
  ancestors** of #494. Their V2/Legacy roots are not merged.
* Real Legacy/Next co-install, Timer install, App1 IM and `main` merge are
  **NOT_RUN** and need separate user authorization.

---

## 6. Release blocker matrix (#425 / #478)

### #425 — long-run integration campaign

| gate | status | evidence |
| --- | --- | --- |
| A. Legacy control preserved | SOURCE_ONLY | no `fleet_mcp` delta; real reachability NOT_RUN |
| B. Next side-by-side artifact | NOT_RUN | no real install or switch |
| C. Agent Monitor E2E | PARTIAL | #495 25 pass and 5 blocked; one blocked is the pin artifact above |
| D. real co-install / IM / switch | NOT_RUN | needs separate authorization |
| E. final RC preserves Legacy | SOURCE_GATE_PASS | #483 additive; tree unchanged |
| hosted 5/5 | **NOT_MET** | 4/5 at `00503f9`, run `40669351645`, 2 new |

### #478 — dual-track owner decision

| item | status |
| --- | --- |
| Legacy 0.1.0 preserved | yes |
| `fleet_mcp` tree unchanged | yes |
| #407/#473/#474 merged | no |
| real co-install / Timer / App1 IM / main merge | NOT_RUN |
| source gate | PASS: no Legacy delta, #483 only adds |

### Blocker list

| blocker | owner | status |
| --- | --- | --- |
| hosted 5/5 | #490 author with the separate fix Agent | BLOCKED |
| #495 seam | #495 QA | QA_ARTIFACT |
| #415 Lark window | #415 | RESOLVED_IN_RC / OPEN_ON_MAIN |
| #493/#488/#490 interfaces | their authors | PENDING, no published SHA |

---

## 7. Reproducible future composition plan

A future Wave 15 RC should be rebuilt from these exact commits, not from
patches:

```bash
# 1. Read-only base
git worktree add --detach /tmp/wave15-prep 00503f91b019ce342af52f38b19bb3aa21dfff55

# 2. The notifier question is already answered
git rev-parse 00503f9:integrations/fleet-v1-milestone-notify/milestone_notify.py
#   -> 7df27cb36b169134ff279e43c0b9c329cf919fef  (4fc9fcb)
git merge-base --is-ancestor 4fc9fcbc677f278d10caeb34d55184545810a13a 00503f9

# 3. Optional only: the newline fix, if an author decides it is required
git merge-tree --write-tree 00503f9 1dbeda5f87bce23b2134f869f6379667bc9847b9

# 4. Owner fixes, each in its own lane, before the RC is rebuilt:
#    #490 naming; #493 cursor/UNCONFIRMED; #493 CI device_ref;
#    #490/#493 signature; #488 activation exit mapping.
#    This gate does not patch any of them.

# 5. Rebuild by ancestry (not cherry-pick), in this order:
#    #492 a00fabc
#    + #493 4f5320d  (merge b23cda6)
#    + #488 009662f  (merge efde750)
#    + #490 06a8be6  (merge a5e55b9)
#    + #483 644ee4a  (merge 7ff573d)
#    + Wave 15 ledger commit

# 6. Gates on the combined tree
python3 -m pytest -q tests/test_fleet_next_wave15_prereq_qa.py
python3 -m pytest -q tests/test_fleet_next_rc_wave12_ledger.py \
  tests/test_fleet_next_rc_wave13_ledger.py tests/test_fleet_next_rc_wave14_ledger.py
python3 -m pytest -q tests/test_fleet_next_agent_monitor_core.py \
  tests/test_fleet_next_agent_monitor_activation.py \
  tests/test_fleet_next_agent_monitor_activation_batch.py \
  tests/test_fleet_next_monitor_source_adapter.py \
  tests/test_fleet_next_legacy_survival.py
```

A future claim of 5/5 is allowed only when the hosted five jobs pass **and**
the shard-4 baseline gate reports `0 new`; until then this matrix keeps
`NOT_MET`. No V2 root is merged; no real co-install, Timer, App1 IM or `main`
merge is performed without a separate authorization.

---

## 8. NOT_RUN

`real Legacy/Next co-install`, `Timer install`, `App1 IM`, `main merge`, real
install, release, service start, OAuth/credential, Feishu/IM send, paid model,
GPU: **NOT_RUN**. This is **not a release**. Old Fleet 0.1.0 is preserved.

Cross: #425 #478 #492 #493 #488 #490 #483 #495 #460 #423 #415 #407 #473 #474.
