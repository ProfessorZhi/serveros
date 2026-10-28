# Wave 18 · Independent release acceptance (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros` source, offline, one disposable temporary directory
per run.
Role: **independent acceptance Owner** for Wave 18. Strictly separated from the
Hub/Node packaging Owner (#505), the sidecar installer Owner (#506), the RC/QA
Owner (#500) and the artifact builder Owner (#509): this work modifies none of
their sources, branches, PRs or worktrees, and it consumes only their published
commits by SHA.
Base: `main @ e69fc0337ae5d33000478e3d5162077f525bc8cc`

## 0. Read this first

Everything below was produced against Python temporary directories and a
file-backed Legacy witness. No release was installed on any host, no service
manager was addressed, no port was bound, no socket was opened, no credential or
OAuth setting was read or written, no Tailscale configuration was touched, no
Feishu or IM message was sent, no model endpoint was called, and no Legacy Fleet
process was started, stopped or reconfigured. The one thing this document cannot
certify is the thing a release most needs: that the bytes boot on a real device.
That gate is `NOT_RUN` and stays `NOT_RUN` until the Owner authorizes it
separately (§6).

## 1. Verdict

```
BLOCKED   BLOCKED_CI_NEW_FAILURE   exit 2
```

| # | Layer | State | Code |
|---|---|---|---|
| 1 | `SOURCE` | **PASS** | `OK` |
| 2 | `UNIT` | **PASS** | `OK` |
| 3 | `HOSTED_CI` | **BLOCKED** | `BLOCKED_CI_NEW_FAILURE` |
| 4 | `BUILT_ARTIFACT` | **BLOCKED** | `BLOCKED_ARTIFACT` |
| 5 | `LEGACY_SURVIVAL` | **PASS** | `OK` |
| 6 | `REAL_DEVICE` | **NOT_RUN** | `NOT_RUN_REAL_DEVICE` |

Two `BLOCKER` findings travel with that verdict, and each would block on its own
even if every layer had passed:

| Finding | Owner | File |
|---|---|---|
| `BLOCKED_ARTIFACT_NOT_REPRODUCIBLE` | #509 | `integrations/fleet-next-wave18-artifact-builder/evidence/ARTIFACTS.sha256` |
| `BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT` | #506 | `integrations/fleet-next-wave17-sidecar-release/sidecar_release.py` |

**The drill working and the release being blocked are both true.** Layer 5 is a
`PASS` because the drill did what it was built to do — install, observe, fail an
upgrade on purpose, revoke, and prove Legacy never moved — and in the course of
doing that it found a trap in the installer. That is a passing drill reporting a
blocked release, not a contradiction.

Verdict JSON: `integrations/fleet-next-wave18-release-acceptance/evidence/wave18-acceptance-20261029.json`
(host paths replaced by `<repo>`, `<world>`, `<temp>`).

## 2. The pin is the claim

`release_pin.json` is the only input; the gate measures the document against the
repository and says which of its assertions are false.

| Unit | PR | Commit | Tree |
|---|---|---|---|
| `rc-wave16` | #500 | `80220a4ad1b009946f11bda0ce96dd8ff8e05468` | `42a2f984bb1ab54623c30b8a01a14bc93b729599` |
| `runtime-abi` | #503 | `b7f1f6134a1e3dcee4b916068c693ff059c097bd` | `dfab4d38ce1a1615e85061460d234504d10163c3` |
| `monitor-p0` | #504 | `6286bfbc885771a45415efcb33405bb21a841fde` | `c008528b0a87cc3e97b8817a08656a648719ff8c` |
| `hub-node-cut` | #505 | `3512c551574a94ad360f8bde6435bdf07c729167` | `41e5840e2ec7699577dcdc29676ae780dc5f0d05` |
| `sidecar-revoke` | #506 | `30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7` | `7a076ceaa6a12640764012ce46c7e283c1831f21` |
| `release-preflight` | #502 | `8e6be6b1177405c58ebf980dc68c0f0475b76e89` | `d2ffc504055916e739992a244e22b3cae2e7628a` |

The release package claim is `fleet-next-sidecar 1.0.0+wave18`, eight members
drawn from four of those units, with `sidecar/sidecar_release.py` as its own
revoke entrypoint. `validate_pin` refuses the document if a version floats, a
commit is short, a unit repeats, a package file has no pinned source, a package
path escapes the package, or the Legacy contract does not name
`get_session`/`get_run`/`send`. It does not refuse it here.

## 3. Layer 2 — `UNIT` (PASS)

Each unit is a fresh `git clone`, `checkout <sha>`, then `pytest` in a scrubbed
environment with `-I` — not a worktree, so nothing another Owner has staged can
be mistaken for the unit's own bytes.

| Unit | Result | Wall |
|---|---|---|
| `rc-wave16` | 104 passed | 10.87 s |
| `hub-node-cut` | 13 passed | 49.52 s |
| `sidecar-revoke` | 118 passed, 1 skipped | 11.66 s |
| `release-preflight` | 60 passed | 3.38 s |
| `monitor-p0` | `NOT_RUN_PLATFORM` (`linux`) | — |
| `runtime-abi` | `NOT_RUN_PLATFORM` (`linux`) | — |

The two `NOT_RUN`s are declared, not hidden: both import `fcntl`, so a `win32`
run of them would be a pass over a skipped file. The pin deliberately records
`measured.passed: null` for `monitor-p0` rather than inventing a count it never
took.

A `UNIT` pass means *that unit's own tests pass at that unit's own commit*. The
negative control for this layer is in the suite: a broken test committed on the
unit branch turns the layer red, so the layer is not "green because it ran
nothing".

## 4. Layer 3 — `HOSTED_CI` (BLOCKED)

Read through `gh api` at each pinned `head_sha`, from the baseline gate's own
summary line (`N failing, N new, N flaky, N listed-but-passing`), not from the
job's conclusion. A shard job exits non-zero merely because the baseline has
failures, so the parsed summary — and only that — decides; a shard whose every
`NEW FAILURE` is attributed is allowed to have a red job.

| Unit | Head | Shards (failing / new / flaky) | Verdict |
|---|---|---|---|
| `rc-wave16` | `80220a4a` | 1/4: 8/0/0 · 2/4: 11/0/0 · 3/4: 17/**3**/0 · 4/4: 25/0/1 | **BLOCKED** |
| `runtime-abi` | `b7f1f613` | 1/4: 26/**14**/0 · 2/4: 73/**49**/0 · 3/4: 72/**62**/0 · 4/4: 73/**58**/0 | **BLOCKED** |
| `monitor-p0` | `6286bfbc` | 8/0/0 · 19/0/0 · 18/0/0 · 13/1/0 | OK (attributed) |
| `hub-node-cut` | `3512c551` | 14/0/0 · 12/0/0 · 14/0/0 · 18/1/0 | OK (attributed) |
| `sidecar-revoke` | `30d7c4d3` | 8/0/0 · 19/0/0 · 18/0/0 · 13/1/0 | OK (attributed) |
| `release-preflight` | `8e6be6b1` | 8/0/0 · 19/0/0 · 18/0/0 · 13/1/0 | OK (attributed) |

The single known failure
`tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`
is attributed, in all four clean units, to #425: it reads the wall clock and
fails only inside a date window. It is counted as `new` because the baseline file
does not contain it, which is exactly what `NEW FAILURE` means here — *outside
the baseline*, not *caused by this head*.

**`rc-wave16` (#500 @ `80220a4a`) — 3 new failures, all one file:**

```
tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::
  TheRealRefusalReachesTheSupervisor::test_three_processes_report_the_blocked_tick_and_none_of_them_flattens_it
tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::
  TheRealVitalsSurviveTheWrapper::test_a_configured_tick_carries_its_vitals_through_the_wrapper
tests/test_fleet_next_agent_monitor_activation_runtime_tick.py::
  TheRealVitalsSurviveTheWrapper::test_a_second_process_does_not_inherit_the_first_ones_verdict
```

That is precisely the file #503 exists to fix, and #503 is the newer head for
that unit. #500's head is superseded, so this is a `NOTE` for #500 rather than a
new defect: the composition ledger at `80220a4a` should not be read as CI-green.

**`runtime-abi` (#503 @ `b7f1f613`) — 183 new failures across all four shards**,
and they are one repository-state failure, not 183:

```
ValueError: .../serveros/plugin/bundle.yaml: does not declare `l2-node` yet the
payload carries ['fleet_mcp/__init__.py', 'fleet_mcp/node/__init__.py',
'fleet_mcp/node/connector.py']; the two faces are one payload again, and a reader
would have to guess which one it is from the artifact's name
  scripts/build.py:956
```

Read from shard 2 of job `122865683097`. At that head the repo's own
`serveros/plugin/bundle.yaml` does not declare the `l2-node` face while the
payload carries node code — the Hub/Node seam #505 is about. This is measured
repo state at the pinned commit, reproducible in the log, not a CI-environment
artifact; `build.py` raising it is the intended guard doing its job. The unit
whose whole purpose is to make the tick's clock measurable cannot be called
green while the build it sits on refuses to build.

Consequence for the release: `HOSTED_CI` is `BLOCKED`, and no amount of
re-running changes that — the fix is a source change at #503/#505, not a retry.

## 5. Layer 4 — `BUILT_ARTIFACT` (BLOCKED)

Nothing is published: the pin records `published: false`, no expected names, no
expected digests. #509's own evidence says `decision=BLOCKED_OWNER`,
`release_permitted=false`, `baseline_decision=BLOCKED_CROSS_COMPONENT` — its
producer already refuses to call it a release, and its
`fleet-next-composition` member count is **0** (a name, not bytes).

Independently of that, the gate re-ran #509's own builder at its recorded source
commit `ff514f4b` with its committed builder (`sha256:34cc5c50…`, 65.1 s, exit
0, empty stderr) and compared digests:

| Artifact | Claimed | Produced | |
|---|---|---|---|
| `fleet-authority-1.0.0+wave18.tar.gz` | `ace00295…` | `ace00295…` | matches |
| `fleet-hub-1.0.0+wave18.tar.gz` | `4ca314f5…` | `d9f57a3d…` | **differs** |
| `fleet-next-composition-1.0.0+wave18.tar.gz` | `2a0933b9…` | `1156a806…` | **differs** |
| `fleet-node-1.0.0+wave18.tar.gz` | `381dbd8e…` | `5a2f3389…` | **differs** |
| `fleet-runtime-base-1.0.0+wave18.tar.gz` | `333caea1…` | `38316cf9…` | **differs** |

So the committed `evidence/ARTIFACTS.sha256` does not describe a tree anyone can
reproduce from the commit it names. That is `BLOCKED_ARTIFACT_NOT_REPRODUCIBLE`
against #509, and it is a stronger result than "not published": a checksum file
that cannot be reproduced is not a pin, it is a claim about bytes that do not
exist.

`--rebuild` is a **reproducibility** check and never independence — the same code
that made the claim is doing the comparing — so it can never turn this layer
green. Independence needs published bytes plus a digest someone else checked.

## 6. Layer 5 — `LEGACY_SURVIVAL` (PASS)

Six OS processes, six distinct pids, one disposable directory, no shared
resource:

| Step | pid | exit | s |
|---|---|---|---|
| `seed` | 33304 | 0 | 0.37 |
| `install` | 16736 | 0 | 0.17 |
| `observe` | 13584 | 0 | 0.16 |
| `failed-upgrade` | 11692 | 0 | 0.37 |
| `rollback` | 29752 | 0 | 0.29 |
| `probe` | 10324 | 0 | 0.21 |

**Install.** 8 files, manifest `sha256:8506840d…`, receipt intact, `code=OK`.

**Observer.** The shipped vocabulary, not an invented one: completed without
activity → `UNKNOWN`, offline → `OFFLINE`, a real terminal → `TERMINAL_VERIFIED`.
A typed subject with authorization → `OK` with `provider_calls=0`; the same
subject without authorization → `BLOCKED_SUBJECT`, also with `provider_calls=0`.
"Typed" is not "authorized", and "blind" is not "zero".

**Failed upgrade.** An upgrade whose bytes do not match the pin is refused with
`BLOCKED_SOURCE_DIGEST`; the installed bytes are unchanged and the receipt still
names `1.0.0+wave18`.

**Revoke.** Deleted exactly the receipt-owned list, `left_behind=[]`,
`receipt_gone=true`, `backup_gone=true`.

**Legacy, after all of it, byte-identical to the seed snapshot:**

| field | value |
|---|---|
| `fleet_instance_id` | `fleet-legacy-01` |
| `route_id` | `route-legacy-01` |
| `boot_id` | `boot-legacy-0.1.0` |
| `port` / `backend_url` | `8100` / `http://127.0.0.1:8100/mcp` |
| `oauth_audience` | `https://legacy.invalid/mcp` |
| `db` / `state_root` / `lock` | `state/fleet.sqlite` / `state` / `fleet-legacy.lock` |
| `clock` | `2026-10-29T00:00:00Z` |
| deliveries | `["delivery-1"]` |
| tree digest | `sha256:5953c54a32084d53863eab2498f64b3fc7df33ac97dce8a403d554ea7199582c` |

`get_session` `sha256:30c82e80…`, `get_run` `sha256:55199df2…`,
`send` `sha256:c0259e7e…` — unchanged, and `send` is idempotent by key so a
repeated check cannot become the second delivery it was meant to notice.

**Eleven injections, each on a fresh root, each with exactly one expected
answer.** A refusal with the wrong code fails, and so does an `OK` where a
refusal was expected:

| Injection | Answer |
|---|---|
| `bad_sha` | `BLOCKED_SOURCE_DIGEST` |
| `foreign_boot` | `BLOCKED_BOOT_MISMATCH` |
| `unauthorized_device` | `BLOCKED_IDENTITY_MISMATCH` |
| `missing_receipt` | `BLOCKED_NO_RECEIPT` |
| `legacy_root_collision` | `BLOCKED_LEGACY_ROOT` |
| `resource_collision` | `BLOCKED_RESOURCE_COLLISION` |
| `resource_clean` | `OK` |
| `offline` | `OFFLINE_REFUSED` |
| `no_grant` | `BLOCKED_NO_GRANT` |
| `not_pinned` | `BLOCKED_NOT_PINNED` |
| `partial_write` | measured, not refused — see below |

## 7. The two blockers, by owner

### 7.1 `BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT` → #506

`integrations/fleet-next-wave17-sidecar-release/sidecar_release.py`

Reproduced against the **unmodified pinned engine**, no monkeypatching: a regular
file `partial-root/zz` is pre-created, the upgrade descriptor contains
`zz/blocked.py`, and `install()` writes files in descriptor order, so it writes
`next/aaa-extra.py` first and then raises `FileExistsError`. Result:

- `outcome=PARTIAL_WRITE`, `receipt_written=false`, receipt still names the old
  `1.0.0+wave18`;
- **10 orphans** left behind, including all eight
  `.sidecar-backup/1.0.0+wave18/...` files;
- revoke then refuses `BLOCKED_FOREIGN_PRESENT`.

The root cannot be recovered without deleting files by hand, which is the exact
failure mode #506's revoke entrypoint was written to prevent: a revoke that only
knows what the *receipt* owns cannot clean up after a write that died before the
receipt existed.

**Close when** `install()` either stages and renames, or removes what it wrote
when a write fails, or `revoke` treats files written by a failed upgrade as its
own — and the drill then records `revoke_after_partial.code == OK`.

Note the shape of this one: #506's own guard tests do not cover a write that
fails mid-install, and they pass. The gap was found by *doing* the upgrade, not
by reading the code.

### 7.2 `BLOCKED_ARTIFACT_NOT_REPRODUCIBLE` → #509

`integrations/fleet-next-wave18-artifact-builder/evidence/ARTIFACTS.sha256`

**Close when** rebuilding at the recorded source commit `ff514f4b` with the
committed builder reproduces every claimed digest (see §5 for the four that do
not). Until then the checksum file cannot be used as a pin by anyone.

### 7.3 Notes, not blockers

- `#500` @ `80220a4a`: three CI new failures, all in
  `tests/test_fleet_next_agent_monitor_activation_runtime_tick.py`, superseded by
  #503. Its ledger should not be cited as CI-green.
- `#503`/`#505`: the hosted shards at `b7f1f613` fail on
  `plugin/bundle.yaml` not declaring `l2-node` (`scripts/build.py:956`). The fix
  belongs to the Hub/Node owners; this gate does not touch their sources.
- `#425`: the date-window test `test_direct_lark_notify.py::…::test_create_returns_an_id_and_patch_names_it`
  is the only `new` entry in the four clean units; attributed, unchanged.
- `#509`'s `fleet-next-composition` has 0 members. It is a name, not an artifact.

## 8. Layer 6 — `REAL_DEVICE` (NOT_RUN)

No device was addressed. Installing on a real host, touching systemd, OAuth,
credentials or the Legacy Fleet needs a separate authorization from the Owner,
and this gate is not that authorization. The layer is a constant:
`build_real_device_layer(allow=True, authorization="…")` still returns
`NOT_RUN`, so a caller cannot turn it green by passing a flag.

## 9. Re-running this

```bash
# offline: SOURCE + UNIT + BUILT_ARTIFACT + LEGACY_SURVIVAL, no network
python integrations/fleet-next-wave18-release-acceptance/wave18_acceptance.py \
    --repo /path/to/serveros \
    --pin  integrations/fleet-next-wave18-release-acceptance/release_pin.json \
    --offline --table --json-out /tmp/verdict.json

# plus the hosted runs read through gh, and the builder's code re-run
python integrations/fleet-next-wave18-release-acceptance/wave18_acceptance.py \
    --repo /path/to/serveros --pin … --rebuild --table --json-out /tmp/verdict.json
```

Exit codes: `0` `READY`, `2` `BLOCKED`, `3` `INCOMPLETE`. The verdict is
re-runnable and self-updating: when #509 publishes real bytes, `BUILT_ARTIFACT`
picks them up from the artifact directory without a code change, and when #506
fixes `install()`, the drill's `revoke_after_partial.code` turns `OK` and that
finding closes on its own. Neither Owner working in parallel is counted as
`DONE`; until their fix lands in a pinned commit, the finding stands.

The gate's own tests are `tests/test_fleet_next_wave18_release_acceptance.py`
(70 tests, Windows and Linux, ~42 s). They include the negative controls that
make the layers mean something: a fake-but-green engine makes the drill go red, a
broken unit test makes `UNIT` go red, an unknown layer state cannot aggregate to
`READY`, the source tree is not written by a run, and a readiness flag cannot be
forced through the environment.

## 10. CI for this PR

Head `cce5f669aa843631adbb4aad7cf33147bf509ad6`, run
[`40759176350`](https://github.com/ProfessorZhi/serveros/actions/runs/40759176350),
read from each shard's own baseline-gate summary rather than its conclusion:

| Shard | Gate summary | Job |
|---|---|---|
| 1/4 | 8 failing, **0 new**, 0 flaky | pass (5 m 18 s) |
| 2/4 | 19 failing, **0 new**, 0 flaky | pass (1 m 19 s) |
| 3/4 | 19 failing, **0 new**, 1 flaky | pass (2 m 32 s) |
| 4/4 | 13 failing, **1 new**, 0 flaky | fail (2 m 16 s) |
| `Claude Code plugin matches its source` | — | pass (17 s) |

Shard 4's job is red and its gate is clean: `shard_files("4/4")` selects
`files[3::4]` of the sorted `tests/test_*.py` list, and this PR's new test file
sorts at index 143, so it runs in shard 4 — whose **only** new failure is the
attributed date-window test
`tests/test_direct_lark_notify.py::CardCreateThenPatchTest::test_create_returns_an_id_and_patch_names_it`.
The file is not in the baseline, so any failure *or collection error* from it
would have been counted as `new`; there is exactly one, and it is not this file.
That is the same distinction this gate applies to the six pinned units in §4,
applied to itself — a red job is not a red gate.

The docs-only commit after that head reproduces the reading:
`6b18fee0e582707aae2c5df09074957cbb708902` in run
[`40760772780`](https://github.com/ProfessorZhi/serveros/actions/runs/40760772780)
reports 0 / 0 / 0 / 1 new failures for shards 1–4, the same `1` being the same
date-window test. Documenting a measurement cannot change what it measures.

The 60 baseline failures across the four shards are pre-existing: shard 1's
`tests/test_verification_tool.py` reports `tests/test_coordination.py` missing
from the tree, and the rest are the file's known entries. Nothing in this PR
touches them.

## 11. What this does not certify

- That the release boots anywhere. `REAL_DEVICE` is `NOT_RUN`.
- That the Legacy Fleet keeps serving under a *real* Next install. Layer 5 runs
  against a file-backed witness in a temporary directory; it proves the
  contract's answers and identity fields do not move, not that a real Legacy
  process survives a real install.
- That any artifact is the release. Nothing is published, and the published
  checksums do not reproduce.
- That CI is green at any of the six heads. It is green at four of six, and red
  for reasons recorded above.
- That the package is installable on a host. The drill installs into a temporary
  root with a synthetic device and boot; no system manager, no service, no port.
