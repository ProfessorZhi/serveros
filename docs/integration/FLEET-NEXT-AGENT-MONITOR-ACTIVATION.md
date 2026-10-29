# Fleet Next Agent Monitor — activation contract

> **Status: upstream entry found, wired, and still `NOT_RUN` on a device.**
> #481 (`9b7710a2`) published the real bounded Batch CLI, so the wrapper is no
> longer blocked on an absent entry — it has an adapter to it (this branch).
> Nothing is installed, enabled, started or sent: the unit remains a template.
>
> Layers: `CODE` ✅ · `UNIT` ✅ 49 activation tests (+24 of #481's, +10 layers) ·
> `HOSTED_CI` see the PR · `ACTIVATED_TIMER` **NOT_RUN** · `DEVICE` **NOT_RUN** ·
> `REAL_IM` **NOT_RUN**.

Tracking: #478 (owner decision, dual-track) · #424 · #417 (monitor) · #434 · #425.

---

## 1. The upstream entry, and how it was wired

#478 asks for a bounded one-shot process the owner can put behind a systemd
**user** unit, so the monitor keeps watching after the ChatGPT turn is gone.

When this record was first written, a unit had nothing to run. Measured:

| Candidate | Then |
|---|---|
| `#446` `runner.py` | **has `main()`** — the convention this wrapper copies |
| `#470 @ 56fcb927` `dispatch.py` | **no entry** — `tick_once`/`tick_from_file`, no `__main__` |
| Fleet Next one-shot CLI | **no published SHA** |

That is why the first version reported `BLOCKED_UPSTREAM_ENTRY`. **#481
(`9b7710a2`) then published `integrations/fleet-v1-milestone-batch/cli.py`**, so
there is now a real entry — but it is not the shape this wrapper takes, and the
adapter exists for that reason:

| #481's CLI | This wrapper's monitor entry |
|---|---|
| needs argv (`--registration`, `--ledger`, `--state`, factories) | a zero-argument callable |
| `main(argv)` returns only an `int` | needs a mapping |
| `_run(argv)` returns `(exit_code, document)` | the only shape carrying the answer |

`batch_adapter.py` builds that argv from operator configuration and carries the
document through. `BOUNDED_UPSTREAM_ENTRY` remains the answer whenever the CLI is
missing or has no `_run`.

### The entry rules, unchanged

| Candidate | State |
|---|---|
| `#446` `integrations/fleet-v1-milestone-runner/runner.py` | **has `main()`** — `argparse`, `--config`, `--provider-factory`, `--source-factory`, JSON result, exit codes. This is the reference convention. |
| `#470 @ 56fcb927` `fleet-v1-milestone-batch/dispatch.py` | **no entry**: `tick_once`, `tick_from_file`, `runner_document`, `_absolute`… and **no `__main__`** |
| `#459` / `#464` / `#470` / `#477` file lists | **no CLI module** in any of them |
| Fleet Next one-shot CLI (FJ lane, RUN `b21ff01c`) | **no published SHA** — nothing to quote, nothing to pin |

So the wrapper takes the monitor as an **injected** `module:callable` and refuses
when it cannot resolve it:

```
state = BLOCKED_UPSTREAM_ENTRY   exit = 3   ticks = 0
```

That is the honest current answer, and it is a *test*, not a comment — see
`test_an_unresolvable_monitor_entry_blocks_with_its_own_code`.

**A unit template with an `ExecStart` naming a module that does not exist would
be a lie that only fails at 03:00 on the machine.** This one cannot: its
`ExecStart` is entirely `@NEXT_...@` placeholders, and a test refuses a template
that types a real entry.

## 2. What was built

`integrations/fleet-next-agent-monitor-activation/`

| File | What it is |
|---|---|
| `activation.py` | the bounded one-shot wrapper + CLI |
| `serveros-next-agent-monitor.service.template` | systemd **user** unit template, source only, no `[Install]` |
| `../../tests/test_fleet_next_agent_monitor_activation.py` | 26 tests, offline, no service touched |

### The run order is the contract

```
configuration → entry → what may be read → what may not be written → N ticks → stop
```

A wrapper that ticked before those would be monitoring something it had no right
to look at. Each step has its own terminal state and exit code:

| Step | Failure | State | Exit |
|---|---|---|---|
| configuration | a shared path/port/name | `MISCONFIGURED` | 2 |
| entry | unresolvable | `BLOCKED_UPSTREAM_ENTRY` | 3 |
| legacy read | unavailable, **or the probe raises** | `LEGACY_UNREADABLE` | 2 |
| next write | reachable for writes | `NEXT_NOT_READ_ONLY` | 2 |
| a tick | raises | `TICK_FAILED` (after exactly one call) | 2 |
| finished | — | `BOUNDED_STOP` | 0 |

A probe that raises is treated as *unavailable*, never as *yes*. That single
choice is the difference between "we could not read the facts" and "we assumed
they were fine".

## 3. Isolation from Legacy is mechanical, field by field

`problems_with_activation()` refuses a configuration that would share anything
with the old track. Each refusal is tested on its own:

| Shared thing | Refusal |
|---|---|
| `next_root` inside `legacy_root` | the Next track may not live inside the Legacy checkout |
| `state` or `lock` inside `legacy_root` | its own state, its own lock |
| `state` or `lock` outside `next_root` | a stray state file is how two tracks end up sharing one |
| `port == legacy_port` | the Next track binds its own |
| `service_name == legacy_service_name` | and it must be namespaced `serveros-next-*` |
| `plugin_id == legacy_plugin_id` | a shared plugin id is a shared OAuth audience |
| a relative path | every path is absolute |

The template is checked the same way: it must not contain
`serveros-fleet-node-connector`, `3231`, `serveros-fleet-ingress` or
`serveros-fleet-owned`, must carry no literal `/home/fjd` or `/srv/`, must say
`TEMPLATE`, and must have **no `[Install]` section** — so `systemctl --user
enable` is not a one-word deployment.

## 4. The monitor watches itself

`health` carries the five things a stalled monitor hides: `last_tick`,
`cursor_age`, `backlog`, `unknown_receipts`, `stale`. When the cursor is older
than 3600 s the result gains a `STALE_MONITOR` warning — **a warning, not an
action**. The module imports no process-launching module, and a test asserts the
source contains no `subprocess`, `os.system`, `os.kill` or `systemctl `, so
"report the stall" cannot become "fix the stall".

That is #478's rule made structural: the monitor may tell you it is stuck; it may
not restart anything, least of all the Legacy track.

## 4b. The adapter's own floor

| Rule | Why |
|---|---|
| argv comes from `BatchCliConfig` only | #481 refuses to import anything named by the registration; an adapter that re-read it from the Batch JSON would reopen the hole from the other side |
| exactly one dynamic import — the operator's `cli_path` | no path from Batch JSON to a Python import |
| factory specs are handed over as strings | `#446`'s `load_factory` is the authority, not this module |
| one provider, one ledger | `_run` builds them; the adapter only reads its document |
| CLI states pass through unchanged | `SETTLED`/`UNCONFIRMED`/`BLOCKED_INPUT`/`TICKED`/`REFUSED`, never merged |
| none of them is `BOUNDED_STOP` | "the process finished" is not "the batch settled" — asserted |
| green = `SETTLED` **and** `DELIVERED`/`DUPLICATE` | `TICKED` is progress, `BLOCKED_INPUT` reached no run, `UNCONFIRMED` delivered nothing |
| unknown state → `UNRECOGNISED`, not green | an unknown word is not a status |

Verified on the real CLI, not a stub: #481's `registration` helper files an
ACK-only dispatch with a **future** deadline, and the real `cli.py` runs through
the adapter in two separate interpreters — `BLOCKED_INPUT`, exit 3, not green,
provider journal empty.

> A near-miss worth keeping: the first version of that test used #481's harness
> `DEADLINE`, which is `2026-10-27T11:00Z` — already past. A passed deadline
> legitimately settles a batch, so the run returned `SETTLED` and looked like a
> defect in #481. It was my fixture. The test now uses a future deadline and says
> why, because the next person will reach for the same constant.

## 5. What is deliberately NOT done

- **No real unit installed, enabled or started.** The file is `.template`, has no
  `[Install]`, and lives under `integrations/`, not `~/.config/systemd/user/`.
- **No `systemctl` invocation** anywhere — asserted on the module source.
- **No Legacy resource named, aliased or ordered against.**
- **No credential, OAuth, plugin id, database, or Legacy state touched.**
- **No real IM/Feishu send.** The provider belongs to the notifier the entry
  injects; the activation wrapper has no sender at all.
- **The cross-interpreter scenario** (#478 §2.6 — dispatcher exits, agent and CI
  settle later, monitor catches it, restart/repeat sends nothing, cross-boot does
  not cross wires) is **NOT_RUN**: it needs the upstream entry that is missing,
  and building it against a stub would prove the stub.

## 6. Exact handoff — what unblocks this

One thing, from the Fleet Next CLI owner:

```
a published one-shot entry, as `module:callable`, with a quotable SHA,
that performs exactly one bounded monitor tick and returns a mapping
optionally carrying `backlog` and `unknown_receipts`.
```

When that exists:

1. point `--monitor-entry` at it (the wrapper resolves `module:callable`);
2. point `--legacy-read` / `--next-write` at real read-only probes;
3. the template's `@NEXT_MONITOR_ENTRY@` gets that value, and nothing else changes.

Until then the correct result of an activation attempt is
`BLOCKED_UPSTREAM_ENTRY`, and this record says so instead of producing a green
that means "the stub ran".

## 7. Not to be confused with

- `integrations/fleet-next-agent-monitor/**` — the monitor body, owned by another
  lane (RUN `1494108b`). Not read, not edited, not double-written here.
- `integrations/fleet-v1-batch-activation/**` — a different, older directory. Not
  touched.
- `integrations/fleet-v1-milestone-batch/**` (#459/#464/#470) — the batch
  contract. Not touched; this wrapper only consumes an entry.
