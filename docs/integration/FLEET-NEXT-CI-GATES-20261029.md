# Fleet Next — hosted CI gates: trigger, collection, composition (W19, 2026-10-29)

**Status: source and CI contract only.** No `main` merge, no force-push, no
deploy/install, no systemd/SCM/plugin Creator, no old-Fleet
route/port/auth/database change, no real Feishu/IM, no paid model, no secret, no
base-token pull-request trigger. Release artifact, real device and production
are separate Gate Matrix rows and stay **NOT_RUN**.

This is an independent CI-owner change (`#515` lane `fdbde933`, FJ original CI
Owner). It edits exactly three files and no other author's source, PR or branch:

| file | why |
| --- | --- |
| `.github/workflows/fleet-next-isolated-contracts.yml` | the gate itself: trigger, collection floor, composition subject |
| `tests/test_fleet_next_ci_path_coverage.py` | the check that the gate keeps covering what it owns, and keeps measuring the head |
| `docs/integration/FLEET-NEXT-CI-GATES-20261029.md` | this record |

---

## 0. What W19 verified about `main` before touching anything

| item | reading |
| --- | --- |
| latest protected `main` | `3bc7adfe40274741cc528c7dd96167d2b67b00ec` (#483) |
| #415 push CI | run `40802854642` @ `93d9a1d27d99fd1fac4531b6c8bc458feaf2fbc8` — **completed / success**, 5/5 jobs |
| #483 push CI | run `40803065087` @ `3bc7adfe…` — **completed / success**, 5/5 jobs |

Both are read from the **push** runs of the merge commits, not from the
`pull_request` runs of the source branches, because a `pull_request` run is a
merge commit and cannot stand in for the source.

**One defect this reading exposes:** the push at `3bc7adfe` did not trigger
`Fleet Next isolated canary contracts` even though #483 added
`integrations/fleet-next-legacy-safety/**` and
`docs/integration/FLEET-NEXT-LEGACY-SAFETY.md`. `main` still carries the
pre-#511 workflow, whose `paths:` names only the `fleet_mcp/deploy/next_*` era.
`main`'s 5/5 is therefore the five `Tests` jobs, and the Fleet Next contract is
**NOT_RUN on main** until this change lands.

`main` also does **not** carry the monitor stack (`git cat-file -e` at
`3bc7adfe`: `integrations/fleet-next-agent-monitor/…/runtime.py`,
`integrations/fleet-next-agent-monitor-activation/activation.py` and
`tests/test_fleet_next_agent_monitor_activation_runtime_tick.py` are all
absent). That fact is what the compatibility window in §4 exists for.

## 1. The trigger: families, not the whole repository

`paths:` names Fleet Next families and nothing else. The `pull_request` and
`push` lists are byte-identical, and the coverage test fails if they drift.

| family | pattern |
| --- | --- |
| deploy units and gates | `fleet_mcp/deploy/next_*`, `fleet_mcp/deploy/serveros-fleet-next-mcp.service` |
| runtime policy and server | `fleet_mcp/runtime_create_policy.py`, `fleet_mcp/config.py`, `fleet_mcp/fleet.py` |
| every Fleet Next contract | `tests/test_fleet_next_*.py` |
| create-session / policy tests | `tests/test_fleet_runtime_create_policy.py`, `tests/test_fleet_create_session_runtime.py` |
| **every Wave integration surface** | `integrations/fleet-next-*/**` |
| **every Wave integration record** | `docs/integration/FLEET-NEXT-*.md` |
| **W19 canonical doc tree** | `docs/fleet-next/**` |
| runtime defaults | `docs/fleet-next-runtime-model-defaults.md` |
| the gate itself | `.github/workflows/fleet-next-isolated-contracts.yml` |

Covered surfaces, asserted one by one in `REQUIRED_SURFACES` (families: Wave
16–18 RC, Wave 17 HubNode, Sidecar, Release Acceptance QA, **W19 mainline
landing**, **W19 canonical docs** — 42 paths):

| family | surfaces (excerpt) |
| --- | --- |
| Wave 16–18 RC | RC Wave 16 and Wave 18 records, `integrations/fleet-next-rc-wave18/composition.json`, the monitor package, the activation wrapper, the Wave 18 ledger test, the runtime-tick regression |
| Wave 17 HubNode | the cut record, `wave17_cut.py`, `evidence/wave17-evidence.json`, its test |
| Sidecar | the revoke record, `sidecar_release.py`, its test, `next_tailscale_gate.py` and its plan example |
| Release Acceptance QA | the Wave 16 preflight record + builder, the Wave 18 artifact-builder evidence, the release-captain manifest, both QA test files |
| **W19 mainline landing** | #493 monitor `contract.py`/`notifier.py`, #498 truth test, #513 `installer.py` + `probe_guards.py`, #509 `wave18_builder.py`, #510 `release_captain.py` + manifest, #512 `supervisor.py`, #514 `wave18_acceptance.py` + `release_pin.json`, and `integrations/fleet-next-legacy-safety/legacy_safety.py` (already on `main`) |
| **W19 canonical docs** | `docs/fleet-next/{architecture,operations,verification,history}/…` |

`push` runs on `main` and on `feat/fleet-next-*`, `qa/fleet-next-*`,
`fix/fleet-next-*`, `ci/fleet-next-*`. A push run of the source commit is what a
5/5 claim is read from; a `pull_request` run is a merge commit and cannot stand
in for it.

`concurrency.cancel-in-progress` is true for `pull_request` and false for
`push`, so superseded PR runs release their runner while a run against `main`
is never cancelled mid-flight.

## 2. Collection is part of the contract

| job | what it refuses |
| --- | --- |
| `offline-unit-contracts` | a gated file that is missing (`FAIL_NO_FILE`), a file that collects fewer than its floor (`FAIL_NO_COLLECTION`) — `Ran 0 tests ... OK` is a failure, not a pass — and a checkout whose `HEAD` is not the head the run names (`FAIL_HEAD_MISMATCH`) |
| `composition-runtime-tick` | a collection that is not exactly `3` (`FAIL_NO_COLLECTION`), any `skipped=` in the run (`FAIL_SKIPPED_COMPOSITION`), a run that does not end in a bare `OK`, a tree with no monitor package (`BLOCKED_COMPOSITION`), an unreachable subject (`BLOCKED_COMPOSITION_SHA`), and a stale witness (`COMPAT_WINDOW_EXPIRED`) |

`offline-unit-contracts` does not run a hand-written list. It gates the coverage
check first and then runs **every `tests/test_fleet_next_*.py` present in the
tree**, so a new Wave's test file is collected without editing the workflow.
`*_ledger.py` is excluded by name and says so in `gate.status`.

The two files that predate the families and match none of them —
`tests/test_fleet_runtime_create_policy.py` and
`tests/test_fleet_create_session_runtime.py` — are gated explicitly, and the
coverage test refuses an edit that drops them.

## 3. The composition subject: this run, not a museum piece

`#511` measured the `#493 × #498/#503` composition by checking out the literal
`05da04f8`. Reproducible — and frozen. `main` will never advance past a SHA, so
that gate would keep testing a tree nobody is reviewing, and say nothing about
the head under review.

| event | subject |
| --- | --- |
| `pull_request` | `github.event.pull_request.head.sha` |
| `push` | `github.sha` |
| `workflow_dispatch` | `inputs.candidate_sha` when given (write access required), else `github.sha` |

The job checks out that subject, proves `git rev-parse HEAD` is it, and then
either measures it or refuses. A reader can tell which from the log:
`COMPOSITION_MEASURED_SUBJECT=<sha>` (the head ran) or
`COMPOSITION_WITNESS_PIN=<sha>` (the dated witness ran, §4).

### The SHA cannot be escalated by a pull request

| vector | why it is closed |
| --- | --- |
| pull-request content | the subject comes from `github.event.pull_request.head.sha`, which GitHub sets, not from a body, label, title or file in the tree; the test refuses the workflow if it ever reads `pull_request.body`/`title`/`labels` or a repo pin file |
| `workflow_dispatch` input | only a maintainer with write access can dispatch; the step also refuses a non-empty candidate on any other event (`BLOCKED_CANDIDATE_NOT_ALLOWED`) and requires 40 lowercase hex (`BLOCKED_CANDIDATE_SHA`) |
| base-token execution | nothing in this workflow runs with the base repository's token; there is no `pull_request_target` and no `secrets.` reference; `permissions: contents: read` |
| the witness pin | it is the one literal SHA left, and §4 makes it conditional, dated and non-substitutable |

## 4. The compatibility window: short, dated, and self-closing

`main` does not carry the monitor stack yet (§0). A bare "measure the head" rule
would be red on every `main` push until W19 lands it — and a permanently red
gate is noise a reader learns to ignore, which is the same failure as a gate
that never runs.

| literal | value | meaning |
| --- | --- | --- |
| `COMPAT_PIN` | `05da04f8c78f302b029c6304ed96f4e8ded5fc9b` | last known composable tree |
| `COMPAT_PIN_EXPIRES` | `2026-11-19` | after this date an absent stack is **red** (`COMPAT_WINDOW_EXPIRED`) |
| `STACK_ABSORBED_ON` | `none` | set to the landing merge SHA once the stack is on `main`; from then on an absent stack is **red** (`BLOCKED_COMPOSITION_REMOVED`) |

Rules the workflow enforces, each pinned by a test:

* the witness runs **only** when the head does not carry the stack
  (`if: steps.presence.outputs.stack_present == '0'`), so it can never stand in
  for a head that was not measured;
* when it does run, it must be the pinned commit, must carry all three modules,
  must collect exactly `3`, must not skip, and must end in a bare `OK`;
* once the window closes, only two moves exist: land the stack and set
  `STACK_ABSORBED_ON`, or re-pin by reviewed diff. Both are diffs, so neither
  can happen by accident.

So there is one short-lived pin, it is dated, it is asserted, and the head path
is already the primary one — the pin cannot become a permanent fixture of
`main`.

## 5. Controls (observed, not asserted in prose)

Nine controls run the workflow's **own** `run:` bodies (extracted from the YAML,
not re-typed) in a throwaway directory:

| # | control | observed |
| --- | --- | --- |
| A | tree carries the stack | `exit=0 stack_present=1` |
| B | no stack, window open | `exit=0`, `COMPOSITION_SUBJECT_ABSENT` |
| C | no stack, `COMPAT_PIN_EXPIRES` = `2026-10-01` | `exit=1`, `COMPAT_WINDOW_EXPIRED` |
| D | no stack, `STACK_ABSORBED_ON` = 40-hex | `exit=1`, `BLOCKED_COMPOSITION_REMOVED` |
| E | `COMPAT_PIN_EXPIRES` = `soon` | `exit=1`, `COMPAT_PIN_UNPARSEABLE` |
| F | pull request supplies a candidate SHA | `exit=1`, `BLOCKED_CANDIDATE_NOT_ALLOWED` |
| G | dispatch, candidate = `zz` | `exit=1`, `BLOCKED_CANDIDATE_SHA` |
| H | dispatch, candidate = 40-hex | `exit=0 subject=<candidate>` |
| I | push | `exit=0 subject=<github.sha>` |

**The skipped composition, measured on the same tree.** The pinned tree at
`05da04f8` run on Windows answers `Ran 3 tests` / `OK (skipped=3)` — every skip
says `ModuleNotFoundError: No module named 'fcntl'`. That is exactly the reading
#503 was opened to remove, and the gate's `FAIL_SKIPPED_COMPOSITION` check turns
it red; on the hosted Linux runner the same three tests execute (`Ran 3 tests`;
`OK`) and the gate prints `PASS`.

These controls also found a defect in the first draft of this change: a `tee`
into `test-output/` that does not exist fails, and under `pipefail` that turned
a tolerated absence into a red. Every gate step now creates its own directory
and states its exit status outright.

## 6. Lark: #415 is now on `main`, and nothing is baselined

`dd5919a9256246570e611a7fa86aea28b46ef14d` is an ancestor of `main` `93d9a1d2`
and of the current `main` `3bc7adfe` (verified with `git merge-base
--is-ancestor`), so the `tests/test_direct_lark_notify.py:259` failure that read
as `13 failing, 1 new` on a `main`-derived branch in W18 is gone from `main`'s
reading: run `40803065087` is 5/5 without it.

This change adds nothing to `tests/hermetic-known-failures.txt` and disables
nothing. If a future wave's source commit shows a failure, the rule stays: name
the exact test id and hand it back to its owner — never widen the baseline to
reach a 5/5.

## 7. Gate chart

| # | gate | status | evidence |
| --- | --- | --- | --- |
| 1 | MAIN — #415 and #483 push CI are really 5/5 | **PASS** | `40802854642`, `40803065087`, both completed/success |
| 2 | TRIGGER — 42 surfaces across RC / HubNode / Sidecar / QA / W19 landing / W19 docs | **PASS** | `test_every_required_surface_is_triggered` |
| 3 | TRIGGER — unrelated paths excluded, no catch-all | **PASS** | `test_unrelated_paths_do_not_trigger`, `test_no_pattern_is_a_catch_all` |
| 4 | TRIGGER — PR/push parity, push bounded, concurrency | **PASS** | three trigger tests |
| 5 | COLLECT — no gate is green on `Ran 0 tests` | **PASS** | `FAIL_NO_COLLECTION` / `FAIL_NO_FILE`; per-file `exit=`/`collected=` |
| 6 | SUBJECT — the composition is this run's head, not a fixed SHA | **PASS** | `test_the_subject_is_the_run_itself`, controls H/I |
| 7 | SUBJECT — a pull request cannot name the commit | **PASS** | control F/G, `test_the_candidate_is_never_read_from_pull_request_content` |
| 8 | WITNESS — dated, conditional, non-substitutable | **PASS** | controls B/C/D/E, `test_the_witness_is_dated_and_expires` |
| 9 | COMPOSE — exactly 3 tests, no skip, on the subject | **TARGET** | hosted `composition-runtime-tick` on this head |
| 10 | LOGS — a red step still carries its reading | **PASS** | `set +e` + `mkdir -p` + `cat "$log"`; controls found the `tee` defect |
| 11 | SAFETY — `contents: read`, no secrets, bounded timeouts | **PASS** | three safety tests |
| 12 | HOSTED — this workflow green on this branch's exact head | **TARGET** | recorded in the Draft PR; a doc commit recording a run would move the head |
| 13 | RELEASE_ARTIFACT / REAL_DEVICE / PRODUCTION | **NOT_RUN** | no build, no device, no deploy, no `main` merge |

## 8. NOT_RUN

* No `main` merge, no force-push, no rebase of anyone's branch, no PR marked
  ready — that is the W19 RC Owner's call and only after this change's hosted
  run is green **and** `main`'s push CI is green after it merges.
* No deploy, install, systemd/SCM/plugin registration, no old-Fleet route, port,
  socket, service, credential, database, state or lock touched.
* No secret, token scope or workflow secret read or written; no
  `pull_request_target`; no timer, no scheduled task, no real IM.
* `integrations/fleet-next-*` content is read only by the composition job's own
  tests; nothing is installed or executed as a service.
