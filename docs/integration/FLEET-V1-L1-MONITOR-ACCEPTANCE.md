# FLEET-V1 L1 — monitor-only acceptance, offline

> **What this document is.** The record of an offline acceptance of the L1
> resident monitor, in **three judged runs plus two identity checks** — not one
> run that does everything:
>
> 1. **resident run** — five ticks against a fake service manager, status read
>    back while it is alive, exited by `--max-ticks`;
> 2. **stop run** — resident, then `SIGTERM`, judged through the same acceptance
>    with `stopped=True`;
> 3. **refusal runs** — each of the nine action switches, one process each;
> 4. **rollback identity** — two revision manifests compared, no process;
> 5. **package identity** — name, one actuator, every member's digest.
>
> It is a **CODE / UNIT** result.
> **No device was installed, started, stopped, reloaded or configured**, and
> nothing here is evidence about one.

## The claim under test

The owner boundary of 2026-10-28 (#434): V1 `serverosd` is a resident, read-only
monitor. It observes; it does not act, does not reason, does not talk to a model
and does not send messages. Any mutation capability stays behind its own gate.

That boundary is a sentence. This acceptance turns four parts of it into
measurements:

| boundary clause | what is measured |
|---|---|
| resident | it ticks on a schedule and answers a socket between ticks |
| read-only | the service manager is asked `show` and nothing else |
| cannot be talked into acting | all nine action switches are refused **by name** |
| cheap | RSS, cumulative CPU and per-tick time are read back, not asserted |

## Provenance

| fact | value |
|---|---|
| acceptance source | `integrations/fleet-v1-monitor-acceptance/acceptance.py` + `tests/test_fleet_v1_monitor_acceptance.py` |
| binary under test | built from this tree with `make -f serverosd/Makefile dist` |
| package identity | `PACKAGE.json` sha256 recorded in the receipt |
| binary identity | `serveros-native-core` sha256 recorded in the receipt |
| pinned stack | #412 `696e823f` → #419 `b798e32b` → #430 `5e9a5113` → #435 `673c766d` → #457 `b6169f19` |

The receipt carries the two digests on every run, so "which bytes were accepted"
is a value in the artefact rather than a claim in this paragraph.

## The result

One run on Dell/fjd, Linux, `-j2`, package built from this tree:

```
package      11 checks, 0 failures   (name, one actuator, every member's digest)
monitor-run  17 checks, 0 failures   (profile, mode, ticks, observations, verbs,
                                      costs ×3, plan floor, failed-unit observation)
monitor      rss_kb 4980–5016 · cpu_ms_total 3 · tick_ms 1–7   profile "monitor-only"
plan         actions 1, refusals []                         ← the planner SAW work
calls        5 × `--user --no-pager --quiet show --property=…`   ← and did none of it
services     toy.service → failed                            ← observed, not repaired
```

`plan.actions = 1` beside five `show` calls is the whole point: the planner is
allowed to see work, and the run executed none of it. Zero verbs and zero planned
actions together would prove less — it would be satisfied by a planner that saw
nothing.

## The negative controls

An acceptance that only ever sees a compliant binary cannot be told apart from
one that checks nothing. Five runs exist to fail:

| control | what it proves |
|---|---|
| a **stub binary** that issues `restart` | the acceptance rejects a binary that is not the monitor |
| one **appended verb** in a good run's call log | the verb check is not vacuous |
| an **acting profile** status document (`profile: serve`, `mode: act`) | a monitor acceptance does not accept a different profile |
| two **byte-identical revisions** | a rollback that cannot be told apart from what it replaced proves nothing |
| each of the **nine action switches** | the refusal is the profile's, by name, not an incidental parse error |

The stub control earned its keep immediately: the first version of the fake
service manager was written with Python `%`-formatting over a shell script that
itself contains `printf '%s'`, so the fake logged its own format string and
recorded no argv — the control could not fail, and said so. (The same mistake was
made twice, once in a scratch driver; it is invisible in review and obvious to a
negative control.)

Four more controls were added after a read-only review pointed out that the
first set could not fail for the reasons it named:

| added control | what it fixes |
|---|---|
| a status with a **failed row** but `profile: serve` | the old acting-profile control failed on its *empty* services list too, so it would have passed with the profile checks deleted |
| `stopped=False` | the stop clause was never fed a run that was not stopped |
| `plan.actions = 0` | the plan check was a type test, so "planned 0, executed 0" passed |
| `try-restart` / `reload-or-restart` / `mask` | the verb vocabulary knew only the short spellings |

and one assertion was split in two: the status document read *while resident*
reports the ticks finished at that instant (one), while the **five** are counted
from the service-manager calls — one `show` per tick — which the process's own
summary cannot fake.

## What a green run does **not** say

- **No device.** `REAL_DEVICE` is `NOT_RUN`. The package was unpacked into a
  temporary directory; no unit file was installed, no service started or stopped,
  no configuration on this machine was touched.
- **No production.** `PRODUCTION` is `NOT_RUN`.
- **The rollback here is identity, not history.** Two builds of the same source
  are byte-identical, and the acceptance says so rather than manufacturing a
  difference: `evaluate_rollback` **fails** on an identical pair. What passes is
  two revisions whose actuator digests differ — the property that makes a
  `current`/`previous` swap verifiable at all. A real version rollback needs two
  real builds and is a device-level acceptance.
- **"Facts" means the document this profile publishes.** The `--facts` *input*
  belongs to the acting profile and is one of the switches the monitor refuses;
  there is no facts file to read in monitor-only mode.
- **Windows: N/A.** The profile is supervised by systemd; the Windows core is a
  different lane with its own supervisor (SCM). Nothing here was run on Windows
  and no three-device install is claimed.

## Layers

| layer | state |
|---|---|
| CODE | **PASS** — the acceptance module and its tests are in this PR |
| UNIT | **PASS** — 8 tests, including the five controls above |
| HOSTED_CI | reported on the exact PR head; compared against the base, not assumed |
| REAL_DEVICE | **NOT_RUN** — no install, no service, no configuration |
| PRODUCTION | **NOT_RUN** |

## Running it

```sh
python3 -B -m unittest tests.test_fleet_v1_monitor_acceptance -v
```

`-B` matters: without it the interpreter writes
`tests/__pycache__/test_fleet_v1_monitor_acceptance.*.pyc` **inside the
repository**, because a module's bytecode is cached before its body runs — so the
`sys.dont_write_bytecode` line in the file cannot cover the file itself. It is
gitignored, and it does not make the tree dirty, but "everything it writes goes
to a temporary directory" is only true with `-B`.

It builds the package itself (a C++ toolchain is a CODE dependency, not a device
one) and skips with a named reason where `g++`/`make` are absent. Everything it
writes goes to a temporary directory; the fake service manager is a shell script
that records its argv.
