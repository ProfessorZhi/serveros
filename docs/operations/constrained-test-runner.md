# The bounded remote test runner (§169)

How a managed test run is started on a test host so that the kernel — not the
caller, not the `ssh` session, and not a `trap` — is what bounds it.

## Why this exists, in one paragraph

On 2026-10-14 a bare `python -m pytest -q -p no:cacheprovider` started from
`$HOME` on this host collected the whole tree below it. PID 288992 held ~145 GiB
of a 247 GiB machine for about nineteen hours and exhausted swap. The launcher
believed it was protected twice: it wrapped the local `ssh` in `timeout`, and
the remote side had `trap 'rm -rf "$SB"' EXIT`. Neither is a bound. `timeout` on
the client bounds the client, and the remote process was reparented the moment
the session ended; the `trap` ran when the shell exited, and the shell exited
early — it removed a directory and left the process. A protection that is not
enforced by something that outlives the session is a story about a protection.

The kernel outlives the session. So the run is placed inside a systemd **user**
transient service — a cgroup — and the bound is read back out of
`/sys/fs/cgroup` while the run is alive.

## The command

```
serverosctl test run --worktree <absolute git worktree root> \
    (--tests <path> [<path> ...] | --all) \
    [--profile default|large-8|large-16] [--authorized] [--reason <text>] \
    [--pytest-bin <path>] [--receipt-dir <path>]
```

- `--worktree` is required and must be an absolute git worktree root. A run may
  not start in a home directory: `pytest.ini` declares `testpaths = tests`, and
  that declaration only takes effect from a repository root. From a home
  directory there is no root, so pytest walks the tree it is standing in.
- `--tests` names explicit paths under `tests/` — files or directories. An
  empty selection is refused, not widened.
- `--all` is the whole suite. It is a flag because it is the decision that has
  to be made on purpose; passing `--all` and a path together is refused.
- `--profile default` needs nothing. Anything larger needs `--authorized`, and
  the `--reason` text is recorded beside it.

Exit code is the runner's own: `0` only when the unit succeeded **and** every
claim about the cgroup held.

## The envelope

| profile | MemoryHigh | MemoryMax | CPUQuota | RuntimeMaxSec | TasksMax | grant |
|---|---|---|---|---|---|---|
| `default` | 3 GiB | 4 GiB | 400% | 600 s | 2048 | no |
| `large-8` | 6 GiB | 8 GiB | 400% | 1800 s | 4096 | yes |
| `large-16` | 12 GiB | 16 GiB | 400% | 1800 s | 8192 | yes |

`TasksMax` is not in the issue and is here on purpose: memory bounds how large a
runaway gets, and this bounds how many of them there can be. The incident's
descendants were exactly the thing the launcher could not see.

The numbers live in `runtime/constrained-test-runner-config.yaml` beside the
read interface for §167, and `tests/test_constrained_runner_contract.py` fails
if that file and `runtime/constrained_runner.py::PROFILES` disagree.

## What it verifies before it will call a run a success

1. **Preflight.** A user systemd exists, and the memory controller is delegated
   to the user manager (`/sys/fs/cgroup/user.slice/user-<uid>.slice/
   user@<uid>.service/cgroup.subtree_control` contains `memory`). Without the
   delegation systemd would accept the unit and silently not enforce
   `memory.max` — which is the failure this check exists to make impossible.
   Failure here is `EXIT_PREFLIGHT` (3) and **nothing is launched**.
2. **The kernel's own files**, read while the unit is alive:
   `memory.max`, `memory.high`, `cpu.max`, `pids.max` under the unit's
   `ControlGroup`. A value that disagrees is `LIMIT_NOT_APPLIED`; a file that
   could not be read is `LIMIT_UNREADABLE`. Both are failures — "I could not
   see it" is never agreement.
3. **The whole tree is in one cgroup.** `/proc/<MainPID>/cgroup` must be the
   unit's cgroup or a descendant of it. A process in a *parent* (`app.slice`)
   is not reached by the unit's limits and is a failure.
4. **The identity is replay-safe.** The receipt's `main_process` carries
   `(pid, starttime, cgroup)`, and the `starttime` is read from
   `/proc/<pid>/stat` **while the unit is alive** — the file is gone the moment
   the process is, so reading it afterwards records `None` on every run and
   cannot be told from an unreadable one.

A unit that finished before it could ever be observed alive is reported with
`envelope_observed: false` and `EXIT_UNVERIFIED` (7). It is not called a
success: nothing proved the bound. In practice the first poll happens
immediately and any run that matters lives for more than a few milliseconds.

## What it never does

- It never writes a property onto `user.slice` or `root.slice`. Those carry
  every other account on a shared host. `build_launch_argv` emits no property
  whose name or value mentions a slice, and a test asserts it across every
  profile.
- It never passes the caller's environment into the unit. systemd starts the
  service from the user manager's environment, so a token or cookie in the
  calling process cannot reach the payload or the receipt.
- It never falls back to an unbounded run. No systemd or no delegation is a
  refusal, not a plain `pytest`.
- It never leaves a finished unit behind: the transient unit is reset at the
  end of every run, and a run past its deadline is stopped by the runner as
  well as by `RuntimeMaxSec`.

## Evidence

Default location: `{ARTIFACTS_ROOT}/skills/constrained-test-runner/runs/`
(override with `--receipt-dir`).

```
<dir>/<run-id>/receipt.json    the run: request, what the kernel had, findings
<dir>/<run-id>/stdout.log      the tests' stdout
<dir>/<run-id>/stderr.log      the tests' stderr
<dir>/index.jsonl              one line per run, for §167 to read
```

The `launch_argv` and the `--reason` text are passed through a secret scrubber
before they are written. Nothing in a receipt is a credential.

### Exit codes

| code | meaning |
|---|---|
| 0 | success, and the envelope was observed to hold |
| 2 `EXIT_REFUSED` | the request is not one this runner may serve (worktree, selection, profile). Nothing was launched |
| 3 `EXIT_PREFLIGHT` | no user systemd, or no memory controller delegation. Nothing was launched |
| 4 `EXIT_ENVELOPE` | the kernel does not have the limits that were asked for |
| 5 `EXIT_TREE` | a process of this run was outside the unit's cgroup |
| 6 `EXIT_EVIDENCE` | the receipt directory is not usable |
| 7 `EXIT_UNVERIFIED` | the launch failed, or the run was never observed alive |
| 124 | the unit hit `RuntimeMaxSec` |
| 137 | OOM-killed or signal-terminated |

## The Windows FJPCOS → Tokyo caller

The launcher that starts tests over `ssh` is **not** part of this repository —
there is no `scp`/`ssh` runner anywhere in the ServerOS tree, and `fleet/`'s
`run_command` is `NotImplementedError`. So this wrapper is the remote half only,
and the far-side launcher has to be pointed at it.

What changes on the caller, and what does not:

- **Delete the remote scratch-dir + `trap EXIT`.** It never bounded anything.
  The remote command is one `serverosctl test run` invocation, and its exit code
  is the verdict.
- **Delete the local `timeout ssh` as a protection.** It may stay as a client
  courtesy, but it is no longer load-bearing: the unit's `RuntimeMaxSec` bounds
  the run whether or not the `ssh` is still connected.
- **Pass a worktree and explicit test paths.** The launcher must stop sending a
  bare pytest invocation. This is the single change that would have prevented
  the incident.
- **Read the exit code, not the stdout.** A run that fails verification exits
  non-zero while its stdout may be a green pytest summary.
- **Fetch the receipt** — `receipt.json` and `index.jsonl` under the run's
  `--receipt-dir` — as the evidence artifact.

Legitimate change points, in the order they matter:

1. The Windows FJPCOS test launcher (outside this repo): replace the
   remote command with `serverosctl test run` and drop the trap.
2. `scripts/verify.py:364` — the repository's own local pytest invocation. It is
   in-process and local, so it is not the incident's shape, but a caller that
   wants the same guarantee can route through the runner.
3. Any agent instruction or memory that tells an operator to run
   `python -m pytest` from a home directory.

## Rollback

Nothing here is deployed and nothing is enabled by default:

- No systemd unit, timer or service file is installed. The transient units the
  runner creates last for one run and are reset at its end.
- No daemon code path changed. `runtime/constrained_runner.py` is a new module;
  `runtime/cli.py` gained one verb under the existing `test` group.
- To stop using it, stop calling `serverosctl test run`. To remove it, revert
  this branch. Neither affects any running agent, CloudCLI, Fleet ingress or
  `serverosd`.
