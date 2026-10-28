# Fleet Next V1 — Wave 14: #492 + #493 + #488 + #490 + #483

**Status: Draft RC, integration-only.** No `main` merge, no force-push, no
release, no install, no service change, no OAuth/credential/Feishu/model call.
CODE / UNIT / CI only; REAL_DEVICE and PRODUCTION are **not measured**.

---

## 0. Identity

| | |
| --- | --- |
| branch | `feat/fleet-next-rc-wave14-20261028` |
| base (#492 Wave 13 exact head) | `a00fabc87fff65aeb50a851536b3314be8399090` |
| base tree | `d84aef2566572353001ec50a60d33ac95c656281` |
| base hosted CI | run `40662311688`, 5/5 jobs |
| composed source commit (before this ledger) | `7ff573db7300867fd37b673a59b0101e7f87dab6` |
| composed source tree | `4f28f4ef78e47790f874fe046a457c2d3defc7d0` |
| ancestry, not patches | each author head is a **parent** of the merge that carries it |

The machine-readable counterpart is
`integrations/fleet-next-rc-wave14/composition.json`; the claim is asserted by
`tests/test_fleet_next_rc_wave14_ledger.py`. Neither file changes an author
file.

---

## 1. Inputs, in the order they were composed

| PR | head | merge base | what it is | own delta |
| --- | --- | --- | --- | --- |
| #493 | `4f5320de7e187daadd79adc92e7487facd7b1820` | `e69fc03` | Monitor body: one writer, one ledger, no word for "done" | 11 files, `+2577` |
| #488 | `009662f36a2a5f8e11c51ae517834379a91a7770` | `9b7710a` | activation wrapper wired to #481's real Batch CLI | 6 files, `+1504/−0` |
| #490 | `06a8be67422f1320e7bdfee38410f8b24d27b501` | `e69fc03` | read-only Legacy source adapter | 8 files, `+1766` |
| #483 | `644ee4a8e35b770426841929d5989bcfad128bef` | `e69fc03` | Legacy resource isolation (#478) | 4 files, `+1932` |

Measured, not assumed:

```text
git merge-base 4f5320d a00fabc  ->  e69fc03   (main)
git merge-base 009662f a00fabc  ->  9b7710a   (#481's Batch CLI, already an ancestor)
git merge-base 06a8be6 a00fabc  ->  e69fc03   (main)
git merge-base 644ee4a a00fabc  ->  e69fc03   (main)
```

#488 has two commits in its own increment: `4ddbcdc` (the adapter) and
`009662f` (the record that the upstream entry has landed). Its base
`9b7710a` is #481, which Wave 13 already carried; merging #488 therefore
brings only those two commits, not a second Batch CLI.

### The four merge commits

| step | merge commit | parents | conflicts |
| --- | --- | --- | --- |
| + #493 | `b23cda6bb` | `a00fabc87`, `4f5320de7` | none (`ort`) |
| + #488 | `efde75022` | `b23cda6bb`, `009662f36` | none (`ort`) |
| + #490 | `a5e55b93a` | `efde75022`, `06a8be674` | none (`ort`) |
| + #483 | `7ff573db7` | `a5e55b93a`, `644ee4a8e` | none (`ort`) |

No file from #492 or from the four authors was edited by the composition. The
delta `a00fabc..7ff573d` is **only additions**, and every addition belongs to
one of the four increments listed above.

### Per-PR own increments (full lists in `composition.json`)

* **#493** — `integrations/fleet-next-agent-monitor/**` (README, package
  `__init__`, `__main__`, `contract`, `core`, `notifier`, `offline`, `runtime`,
  `sources`, `state`) and `tests/test_fleet_next_agent_monitor_core.py`.
* **#488** — `integrations/fleet-next-agent-monitor-activation/**`
  (`activation.py`, `batch_adapter.py`, the systemd user-unit template),
  `docs/integration/FLEET-NEXT-AGENT-MONITOR-ACTIVATION.md`, and the two
  activation tests.
* **#490** — `integrations/fleet-next-monitor-source-adapter/**` and
  `tests/test_fleet_next_monitor_source_adapter.py`.
* **#483** — `integrations/fleet-next-legacy-safety/**` and
  `tests/test_fleet_next_legacy_survival.py`.

---

## 2. The single notifier (#423), present once and not re-imported

#423's head `d9b2948a62609e16007f2fe0ae288f9652d64cd1` is **already an
ancestor of the Wave 13 base**. The composed tree carries
`integrations/fleet-v1-milestone-notify/milestone_notify.py` unchanged; the
ledger asserts that the base and the RC hold the same blob.

The Monitor does not grow a second ledger. Its
`fleet_next_agent_monitor/notifier.py` resolves `module:callable` through
`importlib.import_module`, adopts `observe_payload`, and reports the honest
alternative as `BLOCKED_LEDGER`. The ledger asserts that this adapter contains
no `sqlite3`, no `CREATE TABLE` and no `MilestoneLedger` — the only file named
`milestone_notify.py` in the tree is #423's.

There is no default binding, on purpose: a monitor with no notifier entry must
fail before it spends a provider call. That is a composition property, not a
missing import.

---

## 3. Legacy boundary (#478)

* The composition delta `a00fabc..HEAD` contains no `fleet_mcp/`, `runtime/`,
  `services/`, `plugin/`, `deploy/`, `serverosd/` or `bin/` path. This is
  asserted by path prefix.
* The `fleet_mcp` tree object is byte-identical before and after:
  `fcc3a9f59f5f50d675fa1da9d92df752ce2e3ef9`.
* #483's own delta is purely additive (`git diff --name-status e69fc03..644ee4a`
  contains only `A` entries): it adds guards, placement checks, rollback and a
  survival harness, and edits no Legacy file.
* #490's adapter is read-only by construction: it reads a Legacy source and
  serialises an observation; it does not write, start, stop or configure the
  old Fleet.

### BLOCKED_OWNER

**None raised by this composition.** No path under `fleet_mcp/**` needed to
change, and no author file needed an edit. Both conditions that would have
required an owner handoff were asserted absent instead of worked around. If a
later gate forces either, the exact owner handoff must name the file, the
author commit and the failing assertion before the file is touched.

---

## 4. Wave 12 and Wave 13 ledgers are preserved, not weakened

`tests/test_fleet_next_rc_wave13_ledger.py` and
`tests/test_fleet_next_rc_wave12_ledger.py` are byte-identical to the base:

```text
wave13 blob 6d1be1e88698a1ee64930e80a7af2793b55ec6da
wave12 blob 1265351b07280ae2243f7f1897e58b6b22cdbab4
```

The ledger also re-reads Wave 13's `AUTHOR_ANCESTRY`, `LEGACY_PREFIXES`,
`fleet_mcp/`, `REAL_DEVICE`, `PRODUCTION` and `not measured` tokens, so the
refusal conditions cannot disappear while the file still exists. Wave 14 adds
a new scope; it does not re-aim or relax an old one.

---

## 5. Gates

### 5.1 RED first

The Wave 14 ledger was written before `composition.json` and this record
existed:

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave14_ledger.py
10 failed, 15 passed, 24 subtests passed
```

Every failure was `FileNotFoundError` for the manifest or the record — the
intended RED, not a product failure. No author test was touched to produce it.

### 5.2 GREEN on the composed tree

| suite | result |
| --- | --- |
| #493 core | included in the four-suite run below |
| #488 activation (2 files) | included below |
| #490 source adapter | included below |
| #483 survival | included below |
| **four new suites together** | **179 passed, 29 subtests passed** |
| Wave 12 + Wave 13 ledgers | **21 passed, 41 subtests passed** |
| Wave 13 targeted gate (producers, ledgers, Wave 7, plugin package, registration, batch family) | **238 passed, 114 subtests passed** |
| Wave 14 ledger | **25 passed, 75 subtests passed** |

### 5.3 Hosted CI, and the limit of "5/5"

The final head is checked by the repository's five hosted jobs: the `plugin`
job plus `suite` shards 1–4. The run URL and per-shard reading for the **exact
final HEAD** are recorded in the Draft PR description; the tree does not
duplicate that reading, because a doc commit recording a run would move the
head the run measured.

A 5/5 gate is **not zero baseline failures**. `tests/hermetic_tests.py` reads
`tests/hermetic-known-failures.txt`, which lists 56 tests that already fail on
`main` or cannot pass off a node. A green run means "no failure outside the
baseline", not "every test in the repository passed". This record keeps that
distinction instead of flattening it.

### 5.4 Failure attribution and unfinished gates

* No failure was observed in the four new suites, the two earlier ledgers or
  the Wave 13 targeted gate on the composed tree.
* The full four-shard hosted suite was **not** run locally; the targeted run
  above is a reading over the touched families, not the whole repository.
  Hosted CI is the authority for the full shard reading at the final head.
* #473 / #474 / #407 are **not merged** (see §6). Their V1-only extraction
  needs an owner-authored rebase; this is an unfinished input, not a failure
  of this composition.
* Sibling-repository tests (`ability-management`, `hwz-ops`) depend on the
  hosted `SIBLING_REPOS_TOKEN`; without it the hosted run reports them, and
  this record does not claim them as measured locally.

---

## 6. Not merged, by decision

* **#473 / #474** — a direct merge carries a Legacy-root V2 tree (95/88 files,
  including 20/18 under `fleet_mcp/`); the V1-only extraction needs an
  owner-authored rebase first.
* **#407** — the 85-file V2 Draft is not a dependency of this composition and
  was not consulted as one.

These are unchanged from the Wave 13 reading. No V2 root is carried into this
branch.

---

## 7. Evidence log

### 7.1 Wave 14 ledger after the manifest and record

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave14_ledger.py
25 passed, 75 subtests passed
```

### 7.2 Negative controls (mutation runs)

Each mutation is applied to a throwaway copy of this worktree and reverted; the
observed failures are recorded, not the expectation.

| mutation | expected | observed |
| --- | --- | --- |
| change one manifest digest | per-file manifest assertion fails | see §7.3 |
| rebuild an author by cherry-pick (byte-identical) | ancestry assertion fails | see §7.3 |
| add a second `milestone_notify.py` | single-notifier assertion fails | see §7.3 |
| append a byte to a Legacy `fleet_mcp/` file | Legacy delta assertion fails | see §7.3 |

### 7.3 Actual mutation readings

Each row was run in a throwaway worktree under
`ARTIFACTS_ROOT/tmp/` at this commit, then the worktree was removed. The point
is that a wrong tree turns the ledger red, so the green above is a reading, not
a constant.

| mutation | observed |
| --- | --- |
| change one manifest digest | **1 failed** — `test_every_recorded_file_holds_the_authors_own_blob`, subfail on #493's README: manifest `0000…` vs author blob `025964…` |
| rebuild all four by cherry-pick (byte-identical, no author ancestry) | **9 failed** — four `test_every_author_head_is_an_ancestor_of_the_rc` subfails, four `test_every_recorded_merge_carries_its_author_as_a_parent` subfails, and `test_the_composed_source_is_the_tree_the_record_names` |
| commit a second `milestone_notify.py` | **2 failed** — `test_there_is_exactly_one_notifier_source` and `test_the_composition_adds_nothing_it_did_not_record` |
| append one line to `fleet_mcp/__init__.py` and commit it | **3 failed** — `test_no_legacy_path_is_in_the_composition_delta`, `test_the_fleet_mcp_tree_is_byte_identical` and `test_the_composition_adds_nothing_it_did_not_record` |

### 7.4 Offline packaging

```text
python3 -I -c '<import the four packages with socket disabled>'
OFFLINE_IMPORT_OK
```

The four composed packages import with no network and no node runtime. This is
source-level importability, not an install, a service start or a packaged
artifact.

---

## 8. NOT_RUN

Creator, real device, real send/stop, live service, real plugin install,
OAuth/credential, Feishu/IM send, paid model, GPU: **NOT_RUN**. REAL_DEVICE is
not measured; PRODUCTION is not measured. The old Fleet's services are
untouched. This branch is **not a release** and is not merged to `main`.

Cross: #425 #478 #492 #493 #488 #490 #483 #423 #473 #474 #407.
