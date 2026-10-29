# Wave 19 · A sidecar write that either happens or leaves nothing behind (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros` source and offline tests only.
Role: original author of #506 / #513, and the only writer allowed to change this
sidecar-release source this round (issue #515).
Base: stacked on **#513 @ `6602961d`**, which is itself stacked on **#506 @
`30d7c4d3`** (`qa/fleet-next-wave17-sidecar-revoke-20261028`).
Branch: `fix/fleet-next-wave19-sidecar-atomic-20261029`.

## 0. Read this first

Everything below was produced against Python `tempfile` directories and the two
real release files read from this checkout's `HEAD`. No sidecar was installed on
a host, no `systemd` unit was written or started, no Tailscale was installed or
configured, no port was bound, no socket was opened, no credential was read, no
IM message was sent, no GPU was touched, and nothing outside a `tempfile`
directory was created or deleted. **A green result here is not a real-device
pass.**

Two claims in this document are about *the code as written* and are worth
separating from the ones that are about *behaviour that was run*:

* "the receipt is a checksum, not a signature" — read from
  `RECEIPT_AUTHENTICATION` and demonstrated by a forgery the module accepts;
* everything else — measured by a test that fails if it stops being true.

## 1. Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | #514's `BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT` reproduced on the **pre-fix** engine: 10 orphan files, no receipt, later revoke `BLOCKED_FOREIGN_PRESENT` | **REPRODUCED** — replay of #514's own drill world against the pinned blob |
| 2 | The same world on the fixed engine: refusal **before** the first rename, zero files added or lost, receipt still v1 | **FIXED (offline)** — `BLOCKED_UPGRADE_OBSTACLE`, only remaining "orphan" is the file the drill itself planted |
| 3 | #513's F1 (`restore()` drops `next_backend_id`) reproduced, then fixed | **REPRODUCED, FIXED** — marker keys not owned by the restore survive it |
| 4 | A write is a transaction: pre-check → journal → in-root staging → rename | **VERIFIED (offline)** |
| 5 | A kill at **every** rename leaves either the exact pre-call tree or a journal `recover()` finishes | **VERIFIED** — sweep over all 6 rename points, plus a fault that outlives the undo |
| 5.1 | A lock is judged by process *identity*, not by a pid | **VERIFIED** — a corpse whose handle is still open, and a pid that now belongs to another process, are both refused the "running" verdict |
| 6 | `recover()` finishes / undoes / refuses, and a refusal deletes nothing | **VERIFIED** — every refusal shape in §4, each with the tree compared before and after |
| 7 | Two independent processes: partial upgrade → `recover` → `revoke` returns `OK`, no orphans | **VERIFIED** — three rename points (before, inside, after the commit), each a separate interpreter per verb, and the `recover` child is a different process from the test |
| 7.1 | The same, repeated: the crash → repair path is not intermittent | **VERIFIED** — five crash/repair pairs inside the suite (and 25 in a local loop after the §5.1 fix, no refusal); the two deterministic cases in §5.1 are what actually pin the behaviour |
| 8 | boot / device / operator grant, the cross-process lock, and `next_backend_id` are all enforced through the repair | **VERIFIED** |
| 9 | A second `recover()` is refused, not "successful" | **VERIFIED** — `BLOCKED_NO_JOURNAL`, tree unchanged |
| 10 | #506's 101 tests and #513's 142 tests still pass | **VERIFIED** — 101 (+1 skip, 17 subtests) and 145 (+22 subtests); Wave 19 is 43 (+15 subtests) |
| 11 | Withdrawing any guard or helper turns the suite red | **VERIFIED** — see §7 |
| 12 | The receipt authenticates the writer | **NOT CLAIMED** — checksum only, unsigned; a root writer can forge |
| 13 | The same on a real host, a real `systemd`, a real Tailscale | **REAL_DEVICE_NOT_RUN** |

## 2. The two failures, reproduced before anything was changed

Both were measured on the artifacts as they were, not on a mock and not by
grepping for a symbol.

**#514 — a partial upgrade traps the root.** `sidecar_release.install()` wrote
the release one file at a time and the receipt last. In #514's own drill world —
a v1 install, then a *regular file* left at `zz` where the v2 release needs a
directory — the pre-fix engine committed `next/aaa-extra.py`, then raised
`FileExistsError` on `zz/blocked.py`, leaving **10 files** and **no receipt**.
Every later `revoke()` then answered `BLOCKED_FOREIGN_PRESENT`, because a revoke
may not delete a file no receipt names — a correct refusal of an incorrect state.

**F1 — `restore()` forgets the backend identity.** `restore()` rebuilt the
ownership marker from its own seven keys, so `next_backend_id` — the field #513
hangs the Next backend on, which #506's marker guard tolerates as an extra — was
dropped, and the next revoke answered `BLOCKED_BACKEND_MISSING`.

## 3. The shape of the fix

Not "be more careful". Three properties, each of which can be pointed at:

| property | mechanism |
|---|---|
| nothing is written until everything is known to be writable | `_write_obstacles()` runs **before the first rename**: a link on the way is `BLOCKED_SYMLINK_TRAVERSAL`; a file where a directory is needed (or the reverse) is `BLOCKED_UPGRADE_OBSTACLE`, naming the path |
| every byte is staged inside the root and re-digested there | `.sidecar-staging/new/<rel>`, then `_verify_staged()` re-reads each copy; a short write is `BLOCKED_INSTALL_ABORTED`, never a smaller file |
| the only authority a compensation accepts is the journal | `.sidecar-journal.json` (schema `serveros/fleet-sidecar-txn-journal.v1`) is written first and names `new`, `undo`, `consume`, `backup`, `deletes`, `base_evidence`, `base_receipt_digest`, the receipt body and the marker body it intends to write, plus pid / boot / token. `journal_digest` covers the object |

Staging is a **sibling of the files inside the root**, not a system temp
directory, because `os.replace` is atomic only within one filesystem: across
mount points it degrades to copy-then-delete, which is the operation being
avoided.

Two further details that are not cosmetic:

* `_commit()` skips a target that already holds its new bytes. That is what makes
  a commit interrupted *after* its rename resumable: the staged copy was consumed
  by the rename, so "already done" is the only honest reading.
* The four files this module writes with `_write_atomic` (`journal`, `receipt`,
  `marker`, and their staging siblings' targets) leave `<name>.tmp-<pid>`
  siblings when a kill lands between the write and the rename. `_write_temps()`
  names exactly those three prefixes so `recover()` can remove this module's own
  litter — and nothing else — instead of leaving a file that makes every later
  revoke refuse.

## 4. `recover()`: finish, undo, or refuse

| shape | code | what it leaves |
|---|---|---|
| a journal whose `new` files are all committed or still staged | `OK` | the release finished; transients removed |
| a journal whose committed files all have their recorded old bytes staged | `OK` | the write undone; the root is the pre-write release |
| no journal, only this module's own transients, on a root it owns | `OK` | those transients removed |
| no journal and nothing left behind | `BLOCKED_NO_JOURNAL` | nothing — the second call is a refusal by name |
| no journal on a root nothing proves is this module's | `BLOCKED_NO_JOURNAL` | nothing |
| journal does not verify, or its receipt body does not | `BLOCKED_JOURNAL_UNREADABLE` | nothing |
| a journal path leaves the root | `BLOCKED_JOURNAL_PATH` | nothing; the outside file is compared byte for byte |
| journal names another root / another device | `BLOCKED_TXN_FOREIGN_STATE` | nothing |
| the interrupted write was on another boot | `BLOCKED_BOOT_MISMATCH` | nothing |
| lock names a process that is running **and** started when the lock says | `BLOCKED_CONCURRENT_WRITER` | nothing |
| lock names a pid that now belongs to another process, or to a corpse whose object is still open | — (not a writer) | the repair proceeds |
| lock carries no process identity | `BLOCKED_WRITER_UNKNOWN` | nothing |
| a file holds bytes that are neither end of the transaction | `BLOCKED_TXN_FOREIGN_STATE` | nothing — it is somebody's edit |
| neither direction provable | `BLOCKED_TXN_INCOMPLETE` | nothing |
| no grant whose action is `recover` | `BLOCKED_NO_GRANT` | nothing |

`revoke()`, `restore()` and `install()` all answer `BLOCKED_TXN_PENDING` on a
root with a journal: a repair is a deliberate act, and no other verb walks into
one. For `install()` this check runs **before** the lock check, because an
interrupted write leaves its own lock behind and "another writer holds this root"
would send an operator looking for a process that is already gone.

## 5. What was actually exercised, and how

| claim | how it is produced |
|---|---|
| a kill at each rename | a **separate interpreter** (`tests/wave19_child.py`) whose `os.replace` counts renames into the root and calls `os._exit(86)` *instead of* the N-th. No unwind, so the root holds exactly what the disk held |
| the six rename points | `UPGRADE_RENAMES`: the journal, `bin/sidecar.py`, `etc/added-in-020.json`, `etc/sidecar.json`, the marker, the receipt — `_commit()` moves one staged file at a time and the receipt is last |
| a fault the process survives | a one-shot `OSError` at rename *k*, swept over all six, plus a fault that stays broken through the undo (`stays_broken`), which is what a genuinely full disk does |
| two independent processes | install in the parent, `upgrade --die-at N` in one child, `recover` in a second, `revoke` in a third; the test asserts the repairing pid is not its own |
| "no orphans" | #514's own definition: files the receipt does not own, including the contents of the backup directory it names |
| an edit after the crash | a real `os.replace` crash, then the file rewritten by hand, then `recover()` — refused, and the edited bytes are compared before and after |

The tests never patch the module's *decision* — they patch `os.replace` and
`_stage_bytes`, i.e. the disk, and let the module answer.

## 5.1 A third defect the tests found: a pid is not an identity

The sweep in §5 was intermittently red — one run in ten, on a different test each
time — with the same reason every time:

```
decision: BLOCKED   code: BLOCKED_CONCURRENT_WRITER
reasons:  ['.sidecar-install.lock names pid 37048, which is running']
```

That was not a test artefact. `_pid_alive()` asked one question — does
`OpenProcess` succeed — and on Windows that question has two ways to answer
"yes" about a process that is gone:

* **the object outlives the process.** Windows keeps a process object alive
  while any handle to it is open. A killed writer whose handle somebody else
  still holds (a security product on this host holds them) stays addressable
  under its old pid **with its original start time**, so even adding a start
  time does not settle it. `GetExitCodeProcess` does: `STILL_ACTIVE` (259) is
  the only exit code that means a live process.
* **the pid is reused.** Windows hands a freed pid to the next process it
  creates, and the process that lands on it can be the repair itself, which then
  answered "another writer holds this root" about its own pid. The lock now
  records `process_start` and the repair compares it, so two processes that
  shared a pid are told apart by when they started.

Both are the same failure as #514's in a third place: an interrupted write that
traps the root. Here the only way out is an operator deleting a lock file by
hand, which is precisely what this module exists to make unnecessary.

Two tests hold this down, and neither depends on the machine misbehaving:

| shape | how it is produced |
|---|---|
| a corpse whose object is still open | the test opens the child's handle *itself*, waits for it to exit, asserts the object is still addressable, and requires `_pid_alive` to say no |
| a pid that now belongs to another process | the crash world's lock is rewritten to name **this** process with a start time it never had; the repair must finish, and the negative control — same pid, *true* start time — must still refuse |

## 6. #513 interop: one transaction, one repairer

#513's installer has a repair of its own (`recover()`): it puts bytes back from
`.sidecar-backup/<version>/` and rebuilds the marker. With a Wave-19 journal on
disk there are now two modules rewriting the same files from two different ideas
of what the transaction was. `installer.recover()` therefore checks **first** —
before it removes even its own sibling lock — and answers
`BLOCKED_TXN_PENDING` when `sidecar_release`'s journal or staging tree is
present, naming the verb that must run first. The names are read with `getattr`,
so a #506 revision that predates the journal is still installable rather than
`BLOCKED_INTERFACE`.

Three interop cases, run through the real crash driver: the refusal and what it
leaves, the code being spelled identically in both modules, and the sequence
"engine repairs → the installer's `recover()` is a clean `NOTHING_TO_RECOVER`
with the real two files at their v2 bytes".

## 7. The mutation probe

`probe_guards.py` now runs two case lists, because a mutation of the repair's
guards measured against the Wave-17 suite would report "nothing depends on this"
for the honest reason that nothing there writes a journal. Each run must be
silent on its own set, and both were run on this checkout:

| set | suite | mutations | load-bearing | silent | exit |
|---|---|---|---|---|---|
| `--set release` | Wave 17 | 54 (16 bodies, 7 helpers, 31 pipeline removals) | 54 | 0 | 0 |
| `--set transaction` | Wave 19 | 29 (8 bodies, 13 helpers, 8 pipeline removals) | 29 | 0 | 0 |

The Wave-19 run measures, one at a time: every repair guard's body, every helper
a guard delegates to (`_write_obstacles`, `_verify_staged`, `_forward_problem`,
`_undo_problem`, `_evidence_is_base`, `_lock_is_ours`, `_root_is_ours`,
`_pid_alive`, `_lock_writer_is_alive`, `_process_start`, `_write_temps`,
`_compensate`, `_take_lock`), and each of the eight names in `RECOVER_GUARDS`.
Three of them were **silent on the first run and are not any more**:
`_write_temps` (no test exercised a leftover `.tmp-<pid>`), and the removals of
`_guard_root` and `_guard_journal_evidence` (no test refused a declared-shared
root, or a receipt that verifies against its own digest while describing neither
end of the transaction). All three now have a test, and each is silent again only
if that test is deleted.

## 8. Finding handed to #514's owner: its closing condition cannot be met as written

`revoke_after_partial.code == OK` is not a satisfiable acceptance criterion for
the drill as it stands, and the reason is not the engine:

* the drill **plants** `zz` — a regular file where the upgrade needs a directory —
  and `zz` is named by no receipt;
* a revoke may not delete a file no receipt owns (that is the property #514 was
  written to complain about, and it is correct);
* so after the fix the drill's upgrade is `BLOCKED_UPGRADE_OBSTACLE` with nothing
  written, and the later revoke is `BLOCKED_FOREIGN_PRESENT` naming **only**
  `zz`.

Measured on #514's own world with the fixed engine:

```
install_code: BLOCKED_UPGRADE_OBSTACLE
install_reasons: ["zz is a file where zz/blocked.py needs a directory; nothing was written"]
files_added_by_the_upgrade: []
files_lost: []
orphans: ["zz"]
receipt_still_names: "1.0.0+wave18"
journal_left: false   staging_left: false
revoke_after: BLOCKED_FOREIGN_PRESENT ("… holds files this receipt does not own: zz")
```

Two consequences for #514:

1. its outcome must be read from `InstallResult.code`, not from "did an `OSError`
   escape" — `drill_step.step_failed_upgrade()` records `outcome = PARTIAL_WRITE`
   only when an exception escapes, so a fixed engine reports `NO_FAILURE`, which
   is the drill being unable to tell "wrote everything" from "wrote nothing";
2. the closing condition should be "the upgrade wrote nothing and left no
   transients; the only file the revoke names is the one the drill planted",
   which is what the reproduced run above shows.

## 9. What the receipt actually is

`RECEIPT_AUTHENTICATION`, in the module:

> checksum-only: sha256 over the canonical receipt body; NOT a signature and NOT
> authenticated against a key outside the root, so a writer with access to the
> root can forge a receipt and a marker that agree with each other and be
> undetected

`TheReceiptIsChecksumOnly` proves the sentence rather than repeating it: it
writes a stranger's file into the root, appends it to the receipt, recomputes the
receipt digest, points the marker at it, and the module accepts the result. What
the module refuses is *inconsistency* — an edited body, a marker naming a receipt
that is not there, a journal whose lists do not add up. Authentication needs a
trust root the writer cannot reach; that is a change to where the pin comes from,
not to this file.

## 10. Verification split

| what | how it was established |
|---|---|
| the code path (journal, staging, rename, compensation, repair) | run, in this checkout, on `tempfile` roots |
| the cross-process behaviour | separate interpreters, real `os._exit` |
| #506 / #513 regressions | their own suites, unmodified except for the pipeline-set assertion that now includes `_guard_obstacles` |
| the real sidecar's bytes | read from `HEAD` via `git cat-file`, as Wave 18 does |
| a real host, real `systemd`, real Tailscale, a real `/opt` root | **not run** — and not claimed anywhere |

## 11. Reproduce

```bash
python -m pytest tests/test_fleet_next_wave17_sidecar_release.py -q   # 101 + 1 skip
python -m pytest tests/test_fleet_next_wave18_sidecar_installer.py -q # 145
python -m pytest tests/test_fleet_next_wave19_sidecar_transactions.py -q  # 43
python integrations/fleet-next-wave17-sidecar-release/probe_guards.py
python integrations/fleet-next-wave17-sidecar-release/probe_guards.py \
    --set transaction --tests tests/test_fleet_next_wave19_sidecar_transactions.py
```

## 12. Handoff

* This branch is a **stacked Draft** on #513; #513 is stacked on #506. Nothing is
  merged, and the trunk merge is the RC Owner's to do after independent QA.
* #506's and #513's files are otherwise untouched: this round changed
  `sidecar_release.py`, its probe, its README, the new test file and the new
  child driver, and added three interop cases to #513's test file (plus one
  deferral check in `installer.py`, which is #513's own source).
* The one open question that is not mine to answer: whether the receipt should
  become *authenticated* (§9). That changes the pin's trust root.
