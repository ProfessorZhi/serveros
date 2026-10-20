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

## 4. The acting gate requires this file

`--enable-actions` requires `--state`. A caller that enables a side effect
without a persistent recovery budget is refused **before** the manifest is read
and before any observation or verb:

```
--enable-actions refused: an acting Core must name a persistent --state file … (STATE_REQUIRED)
```

The budget is the only thing that makes the repeated-restart cap outlive a
process. `--lease`, `--link-state` and `--agent-state` already have no safe
defaults; the state path is the fourth fact, and its absence is the same kind of
permissive-by-omission failure.

### The path must be one this account can durably own

`check_state_path` refuses, by name:

| refused | because |
|---|---|
| empty or relative | a relative path resolves against whatever directory the Core started in |
| the path names a directory | it is not a state file |
| the parent is missing / not a directory / a symlink / another account's | there is no safe place to put a first-run file |
| the parent is group- or other-writable | another account can replace the state by rename, defeating a 0600 file inside it |
| the file is a symlink | a state file reached through a link is not necessarily this account's |
| the file is not a regular file, or is another account's | it is not this Core's budget |
| the file is group- or other-writable | another account can rewrite the budget |
| the file cannot be opened `O_RDWR` | a budget that cannot be written cannot be spent from |
| an absent file in an unwritable parent | the first run could not persist its attempt |

The check **does not create the file**. Creating an empty file at startup would
make a later truncation indistinguishable from a legitimate first run, which is
exactly the budget reset this format exists to prevent. The parent directory is
checked as well as the file: a group-writable directory can replace a 0600 file
by rename.

The structural check is not the final authority. The post-action write still
runs; a failed write exits **2** and does not report a successful pass. A state
file whose version is not `v2` (including `v1`), a corrupt document, or a file
whose boot/clock anchor cannot be placed is refused before the pass begins — the
Core never degrades to "no budget, so allow".

The opt-in `tests/toy/service_recovery_toy.sh` passes `--state` on every
`--enable-actions` invocation. A source-level unit test reads the harness and
asserts that each invocation carries `--state`, `--lease`, `--link-state` and
`--agent-state`, so the documented manual recipe cannot drift away from the gate
without a failing test.

## 5. The effect boundary

The budget is persisted **at the effect boundary**, not once after the actions
loop. For each planned effect:

1. **Prewrite.** `ActionJournal::prewrite_attempt` records the attempt in the
   same `Recovery` ledger the planner consults, then replaces the state file
   atomically: a unique `0600` temp file in the same directory, `fsync` of the
   file, `rename` over the final path, `fsync` of the directory. If this fails,
   the provider is **not called**.
2. **Effect.** `execute_effect` invokes exactly one `start` / `restart` / `stop`
   on the provider. It refuses before the prewrite if the writer lease is not
   held or the verb is not one this stage implements.
3. **Determine the outcome.** The caller reprobes readiness and records the
   outcome in the ledger.
4. **Outcome write.** `ActionJournal::write_outcome` persists the post-effect
   ledger separately. If it fails, the attempt from step 1 is already durable;
   the outcome is **unknown**. The pass exits 2, reports the failure, and does
   **not** retry blindly.

A crash between 1 and 2 leaves an attempt with no effect: the budget is
conservatively spent. A crash between 2 and 4 leaves an attempt with an unknown
outcome: the attempt holds its place in the window, the breaker is neither
cleared nor advanced, and the next tick plans against the service's new
observation. There is no path that issues an effect without a durable attempt.

The old single write after the actions loop is gone. A run with no actions writes
nothing; an existing state file is left exactly as it was.

`StateDirectory::open` walks from `/` component by component with
`openat(current, component, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)`,
`fstat`s every opened inode, and keeps the immediate parent as a directory fd.
It refuses `.`/`..`, a symlink at any level, a non-directory, a missing
component, a **group- or world-writable ancestor without the sticky bit**, and
an immediate parent that is not owned by this effective uid or is
group/other-writable. A writable ancestor with the sticky bit is allowed only
when **both** the directory and the resolved entry are owned by this euid or
root: sticky then lets only the entry owner (or root) rename it, so another
group member cannot switch the path. `/tmp` (1777, root-owned) with an entry
owned by us is the safe case; a 0770 directory without sticky is refused even
when the entry is ours.

Each `fstatat` result is compared with the `openat` fd's `st_dev` and `st_ino`;
a mismatch is `STATE_*_IDENTITY_CHANGED`, never a policy applied to one inode
and a traversal of another. The state file's `fstatat` and writability probe are
checked the same way (`STATE_PATH_IDENTITY_CHANGED`).

The write is then at-relative: `openat(dirfd, temp, O_CREAT | O_EXCL |
O_NOFOLLOW | O_CLOEXEC, 0600)`, `write`, `fsync(temp)`, `renameat(dirfd, temp,
dirfd, basename)`, `fsync(dirfd)`. The **opened inode is the authority**: a path
string renamed or replaced after the open cannot redirect the write. A
post-write stability check compares `fstat(dirfd)` with `lstat(parent_path)` and
reports failure if they differ, so a path swap is fail-closed; the data still
went to the trusted inode. A final-path symlink is replaced by `renameat`, not
followed.

**Residual threat boundary.** The post-write stability check is itself
path-based and has a check-to-`lstat` window; it is detection, not the write
authority. A same-user attacker can rename the state directory (they own it):
the write still lands in the original inode and the run exits 2. Cross-user
protection relies on the state root being a **0700 directory owned by the
user**, with ancestors that are not writable by untrusted accounts. A
group-writable ancestor without the sticky bit is now **refused** rather than
accepted for host compatibility; a writable ancestor with sticky is accepted
only under the directory/entry owner rule above.

**Placement consequence on this host (measured):** the intended state root
`$HOME/.serveros` is `0700` and its ancestors (`/`, `/data`, `/data/home`,
`/data/home/vince`) are `0755`, so the realistic per-user path is allowed.
`/data/home/vince/artifacts` and `.../workspace` are `0775` **non-sticky**, so a
state file placed directly under them is refused with
`STATE_ANCESTOR_GROUP_WRITABLE`. That is an intentional tightening, not an
owner-policy blocker for the intended root: a deployer who wants the state under
a 0775 tree must create a 0700 root below it or have the ancestor tightened.
`/tmp` (`1777` sticky) remains usable for a 0700 test root.

**Windows differs:** there is no `openat`/`renameat`/`O_NOFOLLOW`; the
equivalent is a handle-relative `CreateFile`/`SetFileInformationByHandle`
design. That is **not implemented** here — N2 #309 remains
BUILD_UNVERIFIED/NOT_RUN, and this change makes no REAL_DEVICE or SCM claim.

## 6. What this does not do

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
