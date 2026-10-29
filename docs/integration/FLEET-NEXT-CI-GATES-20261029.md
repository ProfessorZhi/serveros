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

### 0.1 W19E (2026-10-29): the stack is on `main`, so the window is closed

The readings above are the state this change was written against. They are
superseded on the stack question, and the record keeps both so a reader can see
which fact moved:

| item | reading at W19E |
| --- | --- |
| `main` | `44f039d70dc4f593ea9db73aae873be3455fc48c` (#521 landed the W19 mainline DAG) |
| `main` push Tests | run `40827808966` — **completed / success**, 5/5 |
| monitor stack first landed | `99c06fab07bd301e7df1ff7fa253f670b4d51d4d` (#520: `runtime.py`, `activation.py`, runtime-tick regression in one tree) |
| stack completed | `b27c93e346d5503a75f95ec068c386b416841a62` (#519) |
| stack present on this head | yes — all three files (`git cat-file -e HEAD`) |

So `STACK_ABSORBED_ON` is no longer `none`: it names `99c06fa`, the *first*
landing. Naming the first rather than the completion is the weaker claim and
therefore the stricter gate — every head carrying the complete stack also
carries `99c06fa`, so a head that lost the stack is red from the earlier commit
rather than excused until the later one.

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
| `offline-unit-contracts` | a gated file that is missing (`FAIL_NO_FILE`), a file that passes fewer than its floor (`FAIL_NO_COLLECTION`) — `Ran 0 tests ... OK` and `no tests ran` are failures, not passes — and a checkout whose `HEAD` is not the head the run names (`FAIL_HEAD_MISMATCH`) |
| `composition-runtime-tick` | a collection that is not exactly `3` (`FAIL_NO_COLLECTION`), any `skipped=` in the run (`FAIL_SKIPPED_COMPOSITION`), a run that does not end in a bare `OK`, a tree with no monitor package (`BLOCKED_COMPOSITION`), an unreachable subject (`BLOCKED_COMPOSITION_SHA`), and a stale witness (`COMPAT_WINDOW_EXPIRED`) |

`offline-unit-contracts` does not run a hand-written list. It gates the coverage
check first and then runs **every `tests/test_fleet_next_*.py` present in the
tree**, so a new Wave's test file is collected without editing the workflow.
`*_ledger.py` is excluded by name and says so in `gate.status`.

### The runner is `pytest`, and that was a measurement, not a preference

The first hosted run of this change (`40804892935`, head `0032028e`) was red
with a reading worth keeping:

```
tests/test_fleet_next_legacy_survival.py    exit=5 collected=0 floor=1
FAIL: 1 gated file(s) did not collect or did not pass
```

`exit=5` is pytest's "no tests collected" — but the job ran `unittest
discover`, and this file (`#478`/`#483`, already on `main`) is written as plain
`def test_*`. `unittest` imported it, found zero `TestCase`s, and the floor
correctly refused to call that green. The floor was right; the instrument was
wrong. Both test styles are live in this tree (every other Wave file is
`unittest`), so a runner that sees only one of them reports on this repository
incorrectly in *both* directions — red here, and silently blind to a
`unittest`-only file anywhere else.

The job now runs `python3 -m pytest -q -p no:cacheprovider`, the same command
`tests.yml` runs, at the same pinned version (`'pytest==9.1.1'`, asserted equal
to the `tests.yml` pin by a test so the two cannot drift). The floor counts
**passed**, not collected: a file whose every test skips has run nothing and
must not satisfy it, and `exit=5` is refused by name. Each line in
`gate.status` now reads `exit= passed= skipped= failed= errors= floor=`, so a
reader can tell a skip from an absence from a failure without opening the log.

There is also a live check in `tests/test_fleet_next_ci_path_coverage.py`:
`--collect-only` over every `tests/test_fleet_next_*.py` in the checkout, and
it fails naming any file the runner collects nothing from. That is the check
that would have caught this locally instead of on a runner.

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

`main` carried no monitor stack when this gate was written (§0), so a bare
"measure the head" rule would have been red on every `main` push until W19
landed it — and a permanently red gate is noise a reader learns to ignore,
which is the same failure as a gate that never runs. The window is **now
closed** (§0.1): the stack is on `main`, and the witness is what the switch
retires.

| literal | value | meaning |
| --- | --- | --- |
| `COMPAT_PIN` | `05da04f8c78f302b029c6304ed96f4e8ded5fc9b` | last known composable tree |
| `COMPAT_PIN_EXPIRES` | `2026-11-19` | after this date an absent stack is **red** (`COMPAT_WINDOW_EXPIRED`) |
| `STACK_ABSORBED_ON` | `99c06fab07bd301e7df1ff7fa253f670b4d51d4d` | the landing that put the stack on `main` (#520); from then on an absent stack is **red** (`BLOCKED_COMPOSITION_REMOVED`) |

Rules the workflow enforces, each pinned by a test:

* the witness runs **only** when the head does not carry the stack
  (`if: steps.presence.outputs.stack_present == '0' && env.STACK_ABSORBED_ON == 'none'`),
  so it can never stand in for a head that was not measured — and, once the
  stack is absorbed, it cannot run at all;
* when it does run, it must be the pinned commit, must carry all three modules,
  must collect exactly `3`, must not skip, and must end in a bare `OK`;
* once the window closes, only two moves exist: land the stack and set
  `STACK_ABSORBED_ON`, or re-pin by reviewed diff. Both are diffs, so neither
  can happen by accident.

**The revert this section exists to make loud.** `STACK_ABSORBED_ON` is a
literal in this file, so the quiet regression is not a missing feature but a
one-line edit back to `none`: on a tree that has already lost the stack, that
revives the dated witness and the gate measures `05da04f8` instead of reddening.
Three checks close it, and all three were observed to flip:

1. `test_a_tree_that_carries_the_stack_may_not_say_none` — the checkout runs
   this file, so it can see whether *this* tree carries the stack; if it does,
   the switch may not be `none`;
2. `test_the_declared_landing_is_a_real_ancestor_of_this_head` — the named SHA
   must be a commit in the clone and an ancestor of `HEAD`
   (`git merge-base --is-ancestor`), so the switch cannot be satisfied by a
   plausible-looking string;
3. the witness steps require `env.STACK_ABSORBED_ON == 'none'` in addition to
   `stack_present == '0'`, so even a tree that eluded the presence step cannot
   reach the pin.

With `STACK_ABSORBED_ON` reverted to `none` in a scratch edit, the file reads
`3 failed, 46 passed`; restored, it reads `49 passed`. The pin therefore cannot
become a permanent fixture of `main`, and it cannot be quietly re-opened
either.

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

### W19E: the removed-stack control, now executed on every run

Controls A–I were run by hand while writing the gate. The one that matters most
after absorption is now a test that re-runs on every CI run, because a
hand-run control is a memory and this one has to hold later:
`ThePresenceDecisionFailsClosedOnItsOwn` extracts the presence step's `run:`
body from the YAML and executes it under `bash -e` in a throwaway tree with
three stand-in files.

| control | tree | observed |
| --- | --- | --- |
| W19E-a | all three stack files present, switch = `99c06fa` | `exit=0`, `stack_present=1`, no `BLOCKED_COMPOSITION_REMOVED` |
| W19E-b | **the same tree with `activation.py` removed** | `exit=1`, `BLOCKED_COMPOSITION_REMOVED`, naming `99c06fa`, and **no** `COMPOSITION_SUBJECT_ABSENT` — the removed-stack tree is not excused as a pre-absorption head |
| W19E-c | no stack, switch `none`, expiry `2020-01-01` | `exit=1`, `COMPAT_WINDOW_EXPIRED` |
| W19E-d | no stack, switch `none`, expiry `not-a-date` | `exit=1`, `COMPAT_PIN_UNPARSEABLE` |
| W19E-e | no stack, switch `none`, expiry `2099-01-01` | `exit=0`, `COMPOSITION_SUBJECT_ABSENT` — the one honest use left: a maintainer measuring a pre-absorption commit on purpose |

W19E-b is the control that would have been missing: A and B differ by one
deleted file, so "the stack is gone" is measured rather than described. The
same edit reverted in the switch itself (`STACK_ABSORBED_ON: 'none'` on a tree
that carries the stack) reads `3 failed, 46 passed` in the offline file;
restored, `49 passed`.

### The offline gate, refused nine ways

The offline step body is extracted the same way and run under the runner's own
`bash -e -o pipefail -c`, with `python3` shimmed to this machine's interpreter.
Nine controls: two on the real tree, seven on throwaway trees.

| # | control | observed |
| --- | --- | --- |
| J | the real tree | `legacy_survival.py exit=0 passed=70 … floor=1` — the file that read `exit=5 collected=0` on the first hosted run is now measured as 70 passing tests |
| K | the real tree, reds only | `FAIL: 3 gated file(s)`, and **no** `FAIL_NO_COLLECTION`: the three reds are `canary_start` (60 failed), `install` (69 failed) and `plugin_package` (1 failed), all Windows-only (symlink/permission/POSIX) and green on the hosted Linux run of the same head |
| L | everything green | `exit=0`, `PASS: every gated Fleet Next contract collected and passed` — the probe can answer green, so the reds below mean something |
| M | a pytest-style file | `passed=2`, `PASS:` — the style that was invisible is now collected |
| N | every test skipped | `passed=0 skipped=2`, `FAIL_NO_COLLECTION … passed 0 of 2 collected, floor is 1` |
| O | a file with no tests at all | `exit=5`, `FAIL_NO_COLLECTION … collected nothing` |
| P | a file that fails | `FAIL: … failed=1 errors=0`, and specifically **not** `FAIL_NO_COLLECTION` |
| Q | a gated file missing from the tree | `FAIL_NO_FILE: tests/test_fleet_runtime_create_policy.py` |
| R | no `tests/test_fleet_next_*.py` at all | `FAIL_NO_FILE` for the coverage check **and** `FAIL_NO_COLLECTION: no tests/test_fleet_next_*.py in this tree` |

Control P found a second real defect. The first draft checked the floor before
the failure branch, so a file whose one test failed read
`FAIL_NO_COLLECTION … passed 0` — "collected nothing" — when in fact it was
collected and ran and failed. The verdict was right and the diagnosis was
wrong, which sends an owner looking for a missing test file. The order is now
`status == 5 or nothing ran` → `failed/errors` → `passed < floor`, and a test
pins that order.

### The trigger, mutated

Four controls copy the real test file and both workflows into a throwaway root
and mutate the workflow there:

| # | mutation | observed |
| --- | --- | --- |
| S | none (positive control) | `9 passed` |
| T | `pull_request.paths` loses `docs/fleet-next/**` | red, naming the four W19 canonical doc surfaces that "no longer trigger the workflow" |
| U | `push.paths` loses the same pattern | red on PR/push parity — the drift a push-only edit produces |
| V | a `**/*` catch-all is added | red on `test_no_pattern_is_a_catch_all` |

Control U first came back green: the `-k` selection used to run these controls
did not include `test_pull_request_and_push_agree_on_one_list`, so the mutation
was measured by nothing. A control that cannot see its own mutation is the same
false green this document is about; the selection now names it, and the reading
above is from the corrected one.

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
| 2 | TRIGGER — 42 surfaces across RC / HubNode / Sidecar / QA / W19 landing / W19 docs | **PASS** | `test_every_required_surface_is_triggered`; control T |
| 3 | TRIGGER — unrelated paths excluded, no catch-all | **PASS** | `test_unrelated_paths_do_not_trigger`, `test_no_pattern_is_a_catch_all`; control V |
| 4 | TRIGGER — PR/push parity, push bounded, concurrency | **PASS** | three trigger tests; control U |
| 5 | COLLECT — no gate is green on nothing collected | **PASS** | `FAIL_NO_COLLECTION` / `FAIL_NO_FILE`; per-file `exit= passed= skipped= failed= errors=`; controls N/O/Q/R |
| 5a | COLLECT — the runner sees both test styles, at the main suite's pin | **PASS** | controls J/M; `test_the_runner_version_matches_the_main_suite_pin`; live `--collect-only` check |
| 5b | COLLECT — a failing file is not called absent | **PASS** | control P; `test_a_failing_file_is_not_reported_as_absent` |
| 6 | SUBJECT — the composition is this run's head, not a fixed SHA | **PASS** | `test_the_subject_is_the_run_itself`, controls H/I |
| 7 | SUBJECT — a pull request cannot name the commit | **PASS** | control F/G, `test_the_candidate_is_never_read_from_pull_request_content` |
| 8 | WITNESS — dated, conditional, non-substitutable | **PASS** | controls B/C/D/E, `test_the_witness_is_dated_and_expires` |
| 8a | ABSORPTION — the stack is on `main`, and losing it is red | **PASS** | `STACK_ABSORBED_ON = 99c06fa` (ancestor of this head); `TheAbsorbedStackIsRealAndCannotBeRevived`; executed control W19E-a/b; the `none` revert reads `3 failed, 45 passed` |
| 8b | ABSORPTION — the witness cannot be revived by an edit to the pin | **PASS** | both witness steps require `env.STACK_ABSORBED_ON == 'none'`; `test_the_witness_cannot_run_once_the_stack_is_absorbed` |
| 9 | COMPOSE — exactly 3 tests, no skip, on the subject | **TARGET** | hosted `composition-runtime-tick` on this head; the subject now carries the stack, so presence is `1` and the three tests run on the head itself rather than on the witness |
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
