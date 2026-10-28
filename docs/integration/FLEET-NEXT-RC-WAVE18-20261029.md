# Fleet Next V1 — Wave 18 RC: #500 + #503 + #504

**Status: Draft RC, source/CI only.** No `main` merge, force-push, deploy,
install, systemd/SCM/plugin Creator, old-Fleet route/port/auth/database change,
real Feishu/IM, paid model or GPU. Release artifact, real device and production
are separate Gate Matrix rows and remain **NOT_RUN**.

---

## 0. Identity

| | |
| --- | --- |
| base | #500 head `80220a4ad1b009946f11bda0ce96dd8ff8e05468` (tree `42a2f984…`) |
| composed source | `1543d1782aba9b01a4e6ec6952fd085eae0c95ac` |
| composed tree | `5d6d478419294742f8b071cc4f2a119fbf3a4428` |
| #503 | `b7f1f6134a1e3dcee4b916068c693ff059c097bd`, base #498 `0096ba6`, merge `f94b9c7` |
| #504 | `6286bfbc885771a45415efcb33405bb21a841fde`, base #497 `3db8ebf`, merge `1543d17` |
| #501 QA input | `5ef2f42a5f1a7b24b40cca36252038ac19a289f3`, independent, not edited |
| #495 QA | `f600fe6622b1e6942de79c90dca66abfa5f2b7ff`, independent, not edited |

The machine-readable counterpart is
`integrations/fleet-next-rc-wave18/composition.json`; the claim is asserted by
`tests/test_fleet_next_rc_wave18_ledger.py`.

---

## 1. Ancestry and own increments

Measured:

```text
git merge-base 80220a4 b7f1f61  -> 0096ba6   (#498, already in #500)
git merge-base 80220a4 6286bfb  -> 3db8ebf   (#497, already in #500)
```

| PR | files | delta | merge |
| --- | --- | --- | --- |
| #503 | 1 | `+132/−15` — the runtime-tick test's clock ABI | `f94b9c7` |
| #504 | 6 | `+600/−17` — monitor contract/core/notifier/sources + core test | `1543d17` |

Both merges are `--no-ff` with no conflicts; each author head is a parent of
its merge. #503 touches only
`tests/test_fleet_next_agent_monitor_activation_runtime_tick.py`; #504 touches
the monitor package and its core test, so there is no path overlap. No V2 root
(#407 `9538238d…`, #473 `a097b27d…`, #474 `0ad3c078a…`) was merged.

---

## 2. Historical ledgers and the Wave 16 tree-scoping

Wave 12, Wave 13 and Wave 14 are byte-identical to the base:

```text
wave12 1265351b07280ae2243f7f1897e58b6b22cdbab4
wave13 6d1be1e88698a1ee64930e80a7af2793b55ec6da
wave14 ee4e64a20638982e3bfd90e58ad2d25d60a182e8
```

Wave 18 legitimately changes files Wave 16 recorded (#503's test file and
#504's monitor files), so the Wave 16 ledger's HEAD-relative byte claims would
call a legitimate P0 fix an unrecorded edit. The correction is **tree-scoped**
to the Wave 16 head, not a new number in an old table.

**One existing path was modified**:
`tests/test_fleet_next_rc_wave16_ledger.py`:

* its author-blob comparison is re-scoped from `HEAD` to
  `WAVE16_HEAD = 80220a4`;
* its composition delta is read as `BASE..WAVE16_HEAD`;
* the Wave 16 manifest's historical head/blob evidence, the four author heads,
  the #498 block record and the refusal conditions remain.

Original blob `c00d4c0…`, corrected blob `245679b…`. Result on this tree:
Wave 12+13+14+16 **70 passed, 196 subtests**.

---

## 3. P0 gates on the composed source

These are composite readings, not a sum of single-PR unit claims.

| gate | reading | what it covers |
| --- | --- | --- |
| #503 runtime tick | **3 passed** | the three #500 failures now measure the tick's clock and are green; no skip |
| #504 monitor core | **52 passed, 4 subtests** | unauthorized CI device blocked; UNKNOWN/EXPIRED never exit 0; deliveryId-only uses the native run id; one blind watch makes the tick unconfirmed |
| #499 adapter | **80 passed, 21 subtests** | compat denied zero reads; CI SHA mismatch; explicit boot expectation; `read_request` |
| #480/#483 Legacy | **86 passed, 11 subtests** | every collision blocked, never renamed aside; shadow write refusal |
| historical ledgers | **70 passed, 196 subtests** | Wave 12/13/14/16 all on their own trees |
| Wave 13 targeted family | **287 passed, 269 subtests** | producers, ledgers incl. Wave 18, Wave 7, plugin package, registration, batch family |
| `test_declared_implementations` | 3 failed, all baseline entries 51–53 | the two #490 reader-field failures stay gone |
| `test_loader_identity` | 2 failed, baseline entries 66–67 | no new failure |
| `test_repository_layers` | 10 passed, 37 subtests | no new failure |
| `test_no_node_absolute_paths` | 5 passed, 5 subtests | no new failure |

### #501 findings addressed by #504

`#501` reported unauthorized CI device delivery, UNKNOWN/EXPIRED exit 0, and
the deliveryId-only P1. #504's added tests include:

```text
test_a_ci_watch_must_not_deliver_under_an_ungranted_device
test_a_typed_ci_subject_is_not_authorized_by_being_typed
test_an_expired_run_never_leaves_the_tick_at_exit_zero
test_an_unobservable_run_never_leaves_the_tick_at_exit_zero
test_a_delivery_id_only_watch_is_delivered_under_the_native_run_id
test_one_blind_watch_makes_the_whole_tick_unconfirmed
test_the_real_wrapper_never_reports_an_unobserved_watch_as_exit_zero
```

---

## 4. Notifier and Lark fixes are not reintroduced

* #496's evidence stands: #460's `send_text` reached #500 through #481; the
  single notifier blob is still `7df27cb36…`, one `milestone_notify.py`.
* #415's Lark fix `dd5919a` is an ancestor of #500 and the test blob is
  unchanged.
* No second notifier, no replayed #423 head, no re-applied Lark patch.

---

## 5. Legacy boundary (#478)

* `80220a4..HEAD` contains no `fleet_mcp/`, `runtime/`, `services/`,
  `plugin/`, `deploy/`, `serverosd/` or `bin/` path.
* `fleet_mcp` tree is byte-identical (`fcc3a9f59…`); #483 remains additive.
* Old Fleet **0.1.0** is preserved; no port, socket, service, MCP URL/ID,
  OAuth audience, credential, database, state or lock is touched.
* #407/#473/#474 are not ancestors.

---

## 6. Gate Matrix (#425 / #478 / #434)

| row | status | evidence |
| --- | --- | --- |
| CODE | **PASS** | composed source `1543d17`; both author heads are merge parents |
| UNIT | **PASS_TARGETED** | 297 P0 tests + 287 targeted; baseline differential declared, no baseline added |
| CI | **TARGET 5/5** | exact final head run is recorded in the Draft PR description; not claimed before it exists |
| RELEASE_ARTIFACT | **NOT_RUN** | separate release lane; no package build in this task |
| REAL_DEVICE | **NOT_RUN** | no device access; needs separate authorization |
| PRODUCTION | **NOT_RUN** | no deploy/install/main merge |

### #425 / #478 / #434

* **#425**: A SOURCE_ONLY; B NOT_RUN; C PASS_SOURCE (#503 3/3, #504 52/4,
  #499 80/21); D NOT_RUN; E SOURCE_GATE_PASS; hosted 5/5 **TARGET** until the
  exact final head run exists.
* **#478**: Legacy 0.1.0 preserved; `fleet_mcp` unchanged; #407/#473/#474 not
  merged; real co-install, Timer install, App1 IM and `main` merge **NOT_RUN**.
* **#434**: no release artifact, real device, model, OAuth or four-line L4
  closure measured; **NOT_RUN**.

No BLOCKED source handoff is open at this HEAD. The remaining gates are
authorization rows, not code rows.

---

## 7. RED → GREEN and mutation controls

The Wave 18 ledger was written before its manifest and record existed:

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave18_ledger.py
10 failed, 11 passed, 18 subtests passed   # FileNotFoundError only
```

After the manifest and record (throwaway worktree at the composed source):

```text
python3 -m pytest -q tests/test_fleet_next_rc_wave18_ledger.py
21 passed, 62 subtests passed
```

Mutation controls (throwaway worktrees, reverted; observations recorded from
the actual runs):

| mutation | observed |
| --- | --- |
| change one manifest digest | **1 failed** — `test_every_recorded_file_holds_the_authors_blob`, subfail on #503's runtime-tick file |
| add a second `milestone_notify.py` | **2 failed** — the Wave 18 delta and `test_the_notifier_is_still_the_composed_seam` |
| append one line to `fleet_mcp/__init__.py` | **2 failed** — the Wave 18 delta and `test_no_legacy_path_moved` |
| rebuild both authors by cherry-pick (no ancestry) | **2 failed** — both `test_each_author_head_is_a_merge_parent_and_an_ancestor` subfails |

---

## 8. Hosted CI

The exact final head is checked by the repository's five jobs. The run URL and
per-shard reading are in the Draft PR description; the tree does not duplicate
them because a doc commit recording a run would move the head the run measured.
A 5/5 claim is allowed only when the five jobs pass and the shard-4 baseline
gate reports `0 new`. If the run fails, every NEW id and the baseline
differential are listed there; no test is added to
`hermetic-known-failures.txt`.

---

## 9. NOT_RUN

`real Legacy/Next co-install`, `Timer install`, `App1 IM`, `main merge`,
release artifact, real install, service start, OAuth/credential, Feishu/IM
send, paid model, GPU: **NOT_RUN**. Real device and production are not
measured. This is **not a release**, and this is **not 5/5** until the exact
final head run says so. Old Fleet 0.1.0 is preserved.

Cross: #425 #478 #434 #500 #503 #504 #501 #495 #496 #497 #498 #499 #494 #483
#407 #473 #474.
