# `serveros/native-core-recovery.v2` — the persisted budget

The file `--state` names is the Native Core's only cross-process record of what
it has already attempted. v1 wrote `steady_clock` seconds and nothing else, so a
Core that restarted after a reboot read every stored attempt as a time in the
future: `windowed()` kept them and `circuit_open()` stayed true for the previous
uptime. The budget was not merely stale; it was **unreadable as a duration**.

v2 keeps the decision engine in steady-clock seconds and adds the two facts a
later process needs to place those seconds: **which boot** they belong to, and
**what the wall clock said** when they were written.

## 1. The document

```jsonc
{
  "schema": "serveros/native-core-recovery.v2",
  "boot_id": "9f3c…",                    // the kernel's boot id
  "anchor": {
    "monotonic": 12345,                  // steady-clock seconds at write time
    "wall_epoch": 1770000000             // wall-clock epoch at the same instant
  },
  "services": [
    {
      "id": "fleet-node",
      "consecutive_failures": 2,
      "opened_until": 14145,             // steady-clock deadline, same boot
      "attempts": [12000, 12300]         // steady-clock seconds, same boot
    }
  ]
}
```

**The rows stay in monotonic seconds.** `wall_epoch` appears ONCE, in the
anchor, and is never used as a duration. The window, the backoff ladder and the
cooldown remain the policy's steady-clock numbers.

The document contains no key, token, password, credential or account name. The
Core holds no signing key; this is an integrity record of what it did, not a
proof of who wrote it.

## 2. Loading: one boot or two

The caller supplies `{monotonic, wall_epoch, boot_id}` for the current run. The
loader compares the stored `boot_id` with it.

### Same boot

The stored monotonic stamps are in the SAME domain as `now.monotonic`, so they
are used **directly**. A wall-clock step — forward or backward, NTP or a person
— has no effect on the budget. This is the normal crash-restart case:
`Restart=on-failure` gets the same budget, not a fresh one.

A stored anchor later than `now.monotonic` is refused: the file was not written
by this boot's clock (a copy, a restore, a tamper). It is not clamped into a
guess.

### Different boot

The old monotonic stamps are placed on the wall-clock timeline using the anchor
written beside them, then placed in THIS boot's monotonic domain:

```
stamp_wall = anchor.wall_epoch - (anchor.monotonic - stamp)
elapsed    = now.wall_epoch - stamp_wall
elapsed    = max(elapsed, 0)          // a BACKWARD step does not age anything out
placed     = now.monotonic - elapsed
```

The clamp is the anti-expansion rule. When the wall clock steps **backwards**,
`elapsed` would be negative; subtracting it would move attempts into the future
and erase the budget. Treating them as "now" preserves the budget: the Core
does not get a restart because the clock moved. A **forward** step ages attempts
normally; a full window of real (or wall-clock) time is the only thing that
makes an old attempt stop counting.

The breaker's `opened_until` is converted the same way, in the other direction:
a backward step makes `remaining` larger, so an open breaker stays open longer,
never shorter. A forward step past the deadline closes it, which is the same
reading as waiting out the cooldown.

## 3. Refusals, and why they are refusals

The loader is fail-closed. A present file that cannot be placed in a domain is
an error, never an empty budget:

| refused | because |
|---|---|
| v1 schema | it has no boot id or anchor; guessing which boot its stamps belong to is the defect v2 removes. The file is left untouched for a person — **no automatic migration, consumption or clearing** |
| no `boot_id` in the file | there is no boot the stamps belong to |
| the current run has no boot id | there is no domain in which to place them; the caller must read `/proc/sys/kernel/random/boot_id` or refuse |
| non-positive / out-of-range `wall_epoch` | a wall epoch is not a duration and an unusable one cannot bridge boots |
| a monotonic anchor later than the current same-boot clock | the file is from a future this boot did not write |
| an attempt later than the file's own anchor | the file is internally inconsistent |
| an attempt outside `[-10 years, anchor]` | a bound before any arithmetic, so a corrupted value cannot overflow the conversion |
| `consecutive_failures` outside `[0, 1_000_000]` | a bound before the `int` cast |
| `opened_until` outside `[-10 years, anchor + 10 years]` | a cooldown beyond that is not a policy this Core implements |

A corrupt JSON document, an unreadable file, or a wrong schema keeps the
existing rule: `ok = false` and the caller refuses to act. The file is never
rewritten or removed by the loader.

## 4. What this does not do

- **It does not use the wall clock for durations.** `window_seconds`,
  `backoff_seconds`, `cooldown_seconds` and every `may_act` comparison stay in
  monotonic seconds.
- **It does not trust a forward wall step as evidence of a reboot.** A forward
  step can age attempts; that is the host clock's trust problem, and it is
  bounded by the policy window. The one direction that could manufacture a
  budget is backwards, and that is clamped.
- **It does not migrate v1.** A person must inspect or remove a v1 file. The
  Core will not consume it, guess its age, or clear it.
- **It does not persist anything in `--serve`.** The resident Core is
  observe-only and refuses `--enable-actions`; the state is written only by the
  single-pass acting path.
