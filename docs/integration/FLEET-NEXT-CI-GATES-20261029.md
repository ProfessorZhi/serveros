# Fleet Next — hosted CI gates: trigger, collection, composition (2026-10-29)

**Status: source and CI contract only.** No `main` merge, no force-push, no
deploy/install, no systemd/SCM/plugin Creator, no old-Fleet route/port/auth/
database change, no real Feishu/IM, no paid model, no secret, no
`pull_request_target`. Release artifact, real device and production are
separate Gate Matrix rows and stay **NOT_RUN**.

This is an independent CI-owner change. It edits exactly three files and no
other author's source, PR or branch:

| file | why |
| --- | --- |
| `.github/workflows/fleet-next-isolated-contracts.yml` | the gate itself: trigger, collection floor, composition pin |
| `tests/test_fleet_next_ci_path_coverage.py` | the check that the gate keeps covering what it owns |
| `docs/integration/FLEET-NEXT-CI-GATES-20261029.md` | this record |

---

## 1. The defect #502 and #506 measured: a path filter fails silently

`.github/workflows/fleet-next-isolated-contracts.yml` is the only hosted job that
runs Fleet Next's contracts off the Tokyo host. It is gated by `paths:`, and a
path filter is the one gate that reports *nothing* when it does not match —
GitHub runs no job and shows no row, so "not triggered" and "passed" are
indistinguishable to a reader.

Measured on the two PRs that added Wave 16/17 QA surfaces:

| PR | added (excerpt) | check rows on the PR | Fleet Next workflow |
| --- | --- | --- | --- |
| #502 | `integrations/fleet-next-wave16-release-preflight/**`, `tests/test_fleet_next_wave16_release_preflight.py` | the five `Tests` jobs only (`40698454647`) | **NOT_RUN** |
| #506 | `integrations/fleet-next-wave17-sidecar-release/**`, `tests/test_fleet_next_wave17_sidecar_release.py` | the five `Tests` jobs only (`40715906054`) | **NOT_RUN** |

Neither directory is named by the old `paths:` list, which covered only the
`fleet_mcp/deploy/next_*` era. The tests those PRs ship were therefore never
run by the job that exists to run them; the shard-4 red on both PRs is the
*inherited* failure of section 6, not evidence about the new work.

The second half of the same false green is collection. `unittest discover`
answers `Ran 0 tests ... OK` for a file that is not in the tree, so a job can
trigger, collect nothing, and print `OK`. Both halves are gated below.

## 2. The trigger: families, not the whole repository

`paths:` now names Fleet Next families and nothing else. The `pull_request` and
`push` lists are byte-identical, and the coverage test fails if they drift.

| family | pattern |
| --- | --- |
| deploy units and gates | `fleet_mcp/deploy/next_*`, `fleet_mcp/deploy/serveros-fleet-next-mcp.service` |
| runtime policy and server | `fleet_mcp/runtime_create_policy.py`, `fleet_mcp/config.py`, `fleet_mcp/fleet.py` |
| every Fleet Next contract | `tests/test_fleet_next_*.py` |
| create-session / policy tests | `tests/test_fleet_runtime_create_policy.py`, `tests/test_fleet_create_session_runtime.py` |
| **every Wave integration surface** | `integrations/fleet-next-*/**` |
| **every Wave integration record** | `docs/integration/FLEET-NEXT-*.md` |
| runtime defaults | `docs/fleet-next-runtime-model-defaults.md` |
| the gate itself | `.github/workflows/fleet-next-isolated-contracts.yml` |

Covered surfaces, asserted one by one in `REQUIRED_SURFACES`:

| family | surfaces (all asserted) |
| --- | --- |
| Wave 16–18 RC | RC Wave 16 and Wave 18 records, `integrations/fleet-next-rc-wave18/composition.json`, the monitor package, the activation wrapper, the Wave 18 ledger test, the runtime-tick regression |
| Wave 17 HubNode | the cut record, `wave17_cut.py`, `evidence/wave17-evidence.json`, its test |
| Sidecar | the revoke record, `sidecar_release.py`, its test, `next_tailscale_gate.py` and its plan example |
| Release Acceptance QA | the Wave 16 preflight record + builder, the Wave 18 artifact-builder evidence, the release-captain manifest, both QA test files |

`push` runs on `main` and on `feat/fleet-next-*`, `qa/fleet-next-*`,
`fix/fleet-next-*`, `ci/fleet-next-*`. A push run of the source commit is what a
5/5 claim is read from; a `pull_request` run is a merge commit and cannot stand
in for it.

`concurrency.cancel-in-progress` is true for `pull_request` and false for
`push`, so superseded PR runs release their runner while a run against `main`
is never cancelled mid-flight.

## 3. Collection is part of the contract

| job | what it refuses |
| --- | --- |
| `offline-unit-contracts` | a gated file that is missing (`FAIL_NO_FILE`), a file that collects fewer than its floor (`FAIL_NO_COLLECTION`) — `Ran 0 tests ... OK` is a failure, not a pass — and a checkout whose `HEAD` is not the head the run names (`FAIL_HEAD_MISMATCH`) |
| `composition-runtime-tick` | a collection that is not exactly `3` (`FAIL_NO_COLLECTION`), any `skipped=` in the run (`FAIL_SKIPPED_COMPOSITION`), a run that does not end in a bare `OK`, and a pin that cannot be reached or checked out (`BLOCKED_COMPOSITION_SHA`) |

`offline-unit-contracts` does not run a hand-written list. It gates the coverage
check first and then runs **every `tests/test_fleet_next_*.py` present in the
tree**, so a new Wave's test file is collected without editing the workflow.
`*_ledger.py` is excluded by name and says so in `gate.status`: those record
author commits that live on other branches and are gated by the RC's own ledger
run, not by a single-ref checkout.

Every checkout asks for the exact head
(`${{ github.event.pull_request.head.sha || github.sha }}`), because the default
`pull_request` checkout is the merge commit — a green on the merge commit is not
a green on the source.

## 4. The composition pin

`#493`'s monitor package and `#498`/`#503`'s activation wrapper only sit beside
each other in a composed tree. On #503's own branch the regression *skips* —
by design, naming the missing package — which is exactly the reading #503 was
opened to remove.

| | |
| --- | --- |
| pin | `05da04f8c78f302b029c6304ed96f4e8ded5fc9b` |
| that tree is | Wave 18 RC = #500 `80220a4a` + #503 `b7f1f613` + #504 `6286bfbc` |
| required modules | `integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/runtime.py`, `integrations/fleet-next-agent-monitor-activation/activation.py` |
| required regression | `tests/test_fleet_next_agent_monitor_activation_runtime_tick.py` (3 tests) |

It is a commit and not a branch. A branch moves, and a gate that follows one
measures whatever it points at that day; the job also proves `git rev-parse
HEAD` equals the pin. If the pin is unreachable (force-push, deleted branch,
GC) or either module is absent, the job prints `BLOCKED_COMPOSITION_SHA` /
`BLOCKED_COMPOSITION` and **exits 1**. It never substitutes a stub, never sets
`SERVEROS_FLEET_NEXT_MONITOR_ROOT` to something else, never clones a second
repository and never fetches code from outside this repository. Re-pinning is a
reviewed diff.

## 5. Controls (observed, not asserted in prose)

Each row is a real run on a throwaway copy; the file was restored afterwards.

| control | mutation | observed |
| --- | --- | --- |
| negative | delete `integrations/fleet-next-*/**` from both lists | `FAILED (failures=1)` — `test_every_required_surface_is_triggered` names 8 surfaces across all four families |
| negative | `EXPECTED_TESTS: '3'` → `'2'` | `FAILED (failures=1)` — `test_the_composition_job_requires_the_three_tests_and_refuses_skips` |
| negative | the #503-alone shape: the regression in a tree **without** #493 | `Ran 3 tests`, `OK (skipped=3)` → the job's checker prints `FAIL_SKIPPED_COMPOSITION` and exits **1** |
| negative | gate a file that is not in the tree | `FAIL_NO_FILE`, `failures=1`, exit **1** |
| positive | collection count on the pinned regression | `collected=3 expected=3`, exit **0**, all three ids listed |
| positive | the gate function on the coverage check | `exit=0 collected=17 floor=10` |

## 6. Lark: #415's fix is in the #500 tree, not in `main`

`tests/test_direct_lark_notify.py:259` fails on every `main`-derived PR with
`AssertionError: 'FAILED' != 'DELIVERED'` (observed in `40698454647`, shard 4,
on #502; same row on #506). The cause is a fixture pinned to a literal
`sent_at` that crossed the transport's 14-day card-update window on 2026-10-27 —
documented by #415 itself.

| reading | value |
| --- | --- |
| #415 fix | `dd5919a9256246570e611a7fa86aea28b46ef14d` |
| ancestor of `main` `e69fc033` | **no** |
| ancestor of #500 head `80220a4a` | **yes** |
| ancestor of Wave 18 RC `05da04f8` | **yes** |

So the composed tree's shard 4 is a different measurement from `main`'s, and a
5/5 on one is not a 5/5 on the other. This change does **not** add the test to
`tests/hermetic-known-failures.txt` and does **not** disable or baseline it —
the product guard is right and the fixture is wrong. A wave's 5/5 counts only
when the five jobs pass on its **source commit** run.

## 7. Gate chart

| # | gate | status | evidence |
| --- | --- | --- | --- |
| 1 | TRIGGER — Wave 16–18 RC / Wave 17 HubNode / Sidecar / Release Acceptance QA all match | **PASS** | `test_every_required_surface_is_triggered`, 21 surfaces |
| 2 | TRIGGER — unrelated paths do not match, no catch-all | **PASS** | `test_unrelated_paths_do_not_trigger`, `test_no_pattern_is_a_catch_all` |
| 3 | TRIGGER — `pull_request` and `push` agree, push bounded, concurrency cancels stale PR runs | **PASS** | `test_pull_request_and_push_agree_on_one_list`, `test_push_is_bounded_to_main_and_fleet_next_branches`, `test_concurrency_cancels_stale_pull_request_runs` |
| 4 | COLLECT — no gate is green on `Ran 0 tests` | **PASS** | `FAIL_NO_COLLECTION` / `FAIL_NO_FILE` branches; observed exit 1 |
| 5 | COMPOSE — #493 + #498/#503 at `05da04f8` runs the 3 runtime-tick tests, none skipped | **TARGET** | hosted `composition-runtime-tick` on this head's run; local #503-alone control is red by design |
| 6 | HEAD — the run measures the exact head, not the merge commit | **PASS** | `FAIL_HEAD_MISMATCH` branch; `ref:` set to the head SHA |
| 7 | SAFETY — `contents: read`, no secrets, no `pull_request_target`, bounded timeouts | **PASS** | `test_permissions_are_read_only`, `test_no_pull_request_target`, `test_every_job_is_bounded` |
| 8 | HOSTED — the workflow itself runs green on this branch's exact head | **TARGET** | recorded in the Draft PR; a doc commit recording a run would move the head |
| 9 | LEGACY LARK — #415 in tree, never baselined | **PASS (documented)** | section 6 |
| 10 | RELEASE_ARTIFACT / REAL_DEVICE / PRODUCTION | **NOT_RUN** | no build, no device, no deploy, no `main` merge |

## 8. NOT_RUN

* No `main` merge, no force-push, no rebase of anyone's branch.
* No deploy, install, systemd/SCM/plugin registration, no old-Fleet route, port,
  socket, service, credential, database, state or lock touched.
* No secret, token scope or workflow secret read or written.
* No real Feishu/IM delivery, no paid model, no GPU.
* `integrations/fleet-next-*` content is read by the composition job only for
  the pinned commit's own tests; nothing is installed or executed as a service.
