# Wave 17 · A sidecar release that can be taken back, and whose revoke can say no

Wave 16 (#502) measured the sidecar — `fleet_mcp/deploy/next_tailscale_gate.py`
plus `fleet_mcp/deploy/next_tailscale_plan.example.json` — as reproducible and
import-independent, and **not revocable**: nothing in the tree undoes it except
a person typing `rm -rf` at a path they remember.

`sidecar_release.py` closes that gap by making the *refusal* the product. A
revoke that deletes whatever it finds under a path it was handed is `rm -rf`
with more typing, so every condition below is a refusal with its own code, and
the module deletes only files that a receipt it wrote itself names, inside the
root that receipt names.

It is offline. No socket, no port, no service manager, no `systemctl`, no
`shutil.rmtree`, no `os.system`. The only external process is `git`, read-only
(`rev-parse`, `cat-file`, `ls-tree`, `grep`), used to read the bytes a
descriptor pins; `install`/`revoke`/`restore` never reach it.

## The pin

A release is a `Descriptor`: version, 40-hex commit, one `sha256` per file, and
one manifest digest over all of them.

```json
{"name": "sidecar", "version": "0.1.0+wave17", "commit": "8e6be6b1…",
 "files": [["fleet_mcp/deploy/next_tailscale_gate.py", "sha256:4b8aa64c…"],
           ["fleet_mcp/deploy/next_tailscale_plan.example.json", "sha256:991cc9c5…"]],
 "entrypoint": "fleet_mcp/deploy/next_tailscale_gate.py"}
```

* `VERSION_RE` is the same pattern #473's artifact builder uses, so a version
  one accepts the other accepts. Two spellings of "a version" would let a
  descriptor carry something the artifact reader had refused.
* The manifest recipe is #473's: `"".join(f"{digest}  {path}\n")`, two spaces,
  hashed again. Content hashes, not git blob SHAs — a *content* hash can be
  recomputed from the installed files, which is what a revoke needs to check.
* `latest`, `1.0`, `1.0.0␠` and a 7-character commit are all `BLOCKED_NOT_PINNED`.

## Two pieces of evidence, deliberately

`install()` writes, into the root it is creating:

| file | what it is |
|---|---|
| `.sidecar-install-receipt.json` | schema, version, commit, manifest, install root, device ref, boot id, grant id, operator, every file with its digest and size, and (after an upgrade) the one previous version |
| `.sidecar-owner.json` | the ownership marker: same install root, same device, same version, same receipt digest |

Those two are the evidence. A third file exists only while a write is in flight
or after one died — `.sidecar-journal.json` — and `recover()` deletes it when the
transaction it names is finished or undone. It is not evidence about the
release; it is the *permission slip* for the writes that produced the receipt.

The receipt carries its own digest over canonical JSON (sorted keys, no
whitespace), so editing any field without recomputing it is *tampering* rather
than a different receipt. The marker is separate evidence for a different
question: a receipt copied into someone else's directory would otherwise be a
claim that this tool made that directory.

Both are checked, and a missing marker is its own code
(`BLOCKED_OWNER_MARKER_MISSING`) rather than a soft warning.

## What a revoke refuses

| shape | code |
|---|---|
| no grant, or a grant for another action | `BLOCKED_NO_GRANT` |
| floating version, short commit, empty manifest | `BLOCKED_NOT_PINNED` |
| a descriptor path that leaves its root | `BLOCKED_DESCRIPTOR_PATH` |
| a relative root | `BLOCKED_ROOT_NOT_ABSOLUTE` |
| `/`, `$HOME` | `BLOCKED_ROOT_NOT_ISOLATED` |
| a root that is itself a link | `BLOCKED_ROOT_IS_SYMLINK` |
| a root that is, contains, or is inside the Legacy root (**both directions**) | `BLOCKED_LEGACY_ROOT` |
| a directory that merely *looks* installed | `BLOCKED_NO_RECEIPT` |
| a receipt edited without its digest | `BLOCKED_RECEIPT_TAMPERED` |
| a receipt for other bytes, another root, another version/commit | `BLOCKED_RECEIPT_MISMATCH` |
| a missing marker / a marker that disagrees | `BLOCKED_OWNER_MARKER_MISSING`, `…_MISMATCH` |
| another device, or the same device after a reboot | `BLOCKED_IDENTITY_MISMATCH`, `BLOCKED_BOOT_MISMATCH` |
| a lock file present | `BLOCKED_CONCURRENT_WRITER` |
| a link on the way to a file | `BLOCKED_SYMLINK_TRAVERSAL` |
| a receipt path that resolves outside the root | `BLOCKED_OUTSIDE_INSTALL_ROOT` |
| a file edited after install | `BLOCKED_MODIFIED_AFTER_INSTALL` |
| a file already gone | `BLOCKED_FILE_ABSENT` |
| a file no receipt owns | `BLOCKED_FOREIGN_PRESENT` |

Two of these are worth stating explicitly because a plausible implementation
gets them wrong:

* **Legacy containment is checked in both directions.** A root inside the
  Legacy root, and a root that *contains* the Legacy root, are both refused. The
  second is the dangerous one: deleting "our" root would take Legacy with it.
* **A junction is a link.** On Windows a junction is a reparse point that
  `Path.is_symlink()` reports as `False`, so the guard tests
  `st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT` as well. A check written
  with `is_symlink()` alone would wave through the exact shape the traversal
  guard exists for — and the test suite falls back to `mklink /J` on hosts that
  cannot create symlinks, which is also the case that proves the bit check.

## The pipeline is a list of names, and that is a tested property

Guards are named functions in one ordered tuple, not inline `if`s, so a
reviewer reads the whole refusal surface at once and the mutation probe can
disable exactly one. The tuple holds **names**, resolved at call time by
`revoke_guards()` / `restore_guards()`.

That detail is not cosmetic. The first draft held function objects; a tuple of
objects is bound once at import, so rebinding a guard left the pipeline
unchanged and the probe reported "nothing depends on this guard" for all
thirteen at once — a clean bill of health that measured nothing.
`TheGuardPipelineIsResolvedAtCallTime` is the test that stops it coming back.

Symlink is judged **before** containment: a realpath that leaves the root is
also caught by containment, but "there is a link in the way" is the diagnosis an
operator can act on, so it wins.

## Idempotence means "the tree is unchanged", not `OK`

A second revoke deletes nothing and still **refuses** with
`BLOCKED_NO_RECEIPT`: the evidence was the receipt, and the receipt is now gone.
A revoke without evidence is exactly the operation this module exists to refuse,
so returning `OK` for it would report success for work it did not do. The
contract is `deleted == ()` **and** a refusal code.

## Upgrade, restore, and one level of history

`install(..., replace=receipt)` upgrades: it copies the current files into
`.sidecar-backup/<old-version>/` and records that one previous version in the
new receipt. `restore(previous_descriptor)` puts those bytes back and consumes
the backup.

Depth is exactly one, and a second upgrade is `BLOCKED_UPGRADE_CHAIN` rather
than an overwrite: the receipt can back one rollback, and a second one would
destroy the evidence for the first.

Restore runs revoke's guards plus three of its own (`install_root_match`,
`previous_match`, `backup`) minus one (`receipt_match`, which compares the
receipt with the descriptor being undone — during a restore the root holds the
*newer* version, so keeping it would refuse every correct restore).
`test_the_two_pipelines_do_not_drift` asserts that relationship, so a guard
added to one cannot be silently absent from the other.

Restore is covered shape by shape for the same reason revoke is: it *writes*.
A restore talked into writing outside its root is the same defect as a revoke
talked into deleting outside it.

## A write is a transaction, not a loop of writes

`install()` used to write the release one file at a time and the receipt last.
#514 measured what that costs: an upgrade that met a *regular file* where it
needed a directory wrote ten files, then failed — and the root, holding files no
receipt named, answered `BLOCKED_FOREIGN_PRESENT` to every later revoke. That
refusal was correct; the orphans were the bug.

The write is now three steps, and every one of them is a place a kill can land:

| step | what it does |
|---|---|
| **pre-check** | finds every obstacle *before* the first rename. A component that is a link is `BLOCKED_SYMLINK_TRAVERSAL`; a file where a directory is needed (or the reverse) is `BLOCKED_UPGRADE_OBSTACLE`. The refusal names the path |
| **journal** | writes `.sidecar-journal.json` — schema `serveros/fleet-sidecar-txn-journal.v1` — before anything else: `new`, `undo`, `consume`, `backup`, `deletes`, `base_evidence`, `base_receipt_digest`, the receipt body and the marker body it intends to write, plus pid, boot id and token. Its own digest covers the whole object |
| **staging** | copies the target bytes into `.sidecar-staging/new/<path>` *inside the root* and re-digests them there. A short write is a failure, not a smaller file |
| **commit** | `os.replace` from staging onto the release, one file at a time, then the marker, then the receipt |

Staging lives inside the root because `os.replace` is atomic only within one
filesystem: a system-temp sibling of a mount point would silently degrade to
copy-then-delete, which is the operation this is trying to avoid.

On any failure the module **undoes** what the journal names and reports
`BLOCKED_INSTALL_ABORTED` with the root exactly as it was. If the undo itself
cannot finish — the disk that failed once fails again — the journal stays and the
answer is `BLOCKED_TXN_INCOMPLETE`: `recover()` is the verb, and the module does
not pretend otherwise by raising.

## `recover()`: finish, undo, or refuse

`recover()` reads the journal and takes exactly one of three positions, gated on
evidence rather than on a timestamp:

* **finish** — every file is either already the new bytes or has a staged copy
  that still digests to them;
* **undo** — finishing is impossible, but every committed file still has its
  recorded old bytes in the staging tree;
* **refuse** — neither is provable. Nothing is deleted, and the code says which
  side is short.

| shape | code |
|---|---|
| no journal, and none of this module's transient files | `BLOCKED_NO_JOURNAL` |
| `recover()` called a second time after a successful repair | `BLOCKED_NO_JOURNAL` |
| the journal does not verify, or its receipt body does not | `BLOCKED_JOURNAL_UNREADABLE` |
| a path in the journal leaves the root, or is `..`/absolute | `BLOCKED_JOURNAL_PATH` |
| the journal names another root or another device | `BLOCKED_TXN_FOREIGN_STATE` |
| the interrupted write was on another boot | `BLOCKED_BOOT_MISMATCH` |
| the lock names a process that is still running, and started when the lock says | `BLOCKED_CONCURRENT_WRITER` |
| the lock names a pid that now belongs to another process, or to an exited one whose object is still held | not a writer — the repair proceeds |
| the lock carries no identity at all | `BLOCKED_WRITER_UNKNOWN` |
| a file holds bytes that are neither end of the transaction | `BLOCKED_TXN_FOREIGN_STATE` |
| neither direction is provable | `BLOCKED_TXN_INCOMPLETE` |
| no grant whose action is `recover` | `BLOCKED_NO_GRANT` |

The **journal is the only deletion authority**: a path it does not name is not in
reach, which is what makes a stranger's file in the root survive a repair. A
`recover()` with no journal cleans only this module's own transients
(`.sidecar-staging/`, `<name>.tmp-<pid>` siblings of the three files it writes
atomically, and its own lock), and only when the root carries an intact receipt
and marker naming it — otherwise `BLOCKED_NO_JOURNAL`, deleting nothing.

`revoke()`, `restore()` and `install()` all refuse a root with a journal
(`BLOCKED_TXN_PENDING`): a repair is deliberate, and no other verb gets to walk
into one.

## A pid is not an identity

The lock is a file, so `recover()` has to decide from it whether the writer that
left it is still running. `BLOCKED_CONCURRENT_WRITER` is the wrong answer for a
writer that is gone, because nothing but a person deleting a file by hand gets
the root unstuck — the same trap as #514, one layer down. Two questions decide
it, not one:

* **is the process running?** Windows keeps a process *object* alive while any
  handle to it is open, and a killed writer whose handle a security product still
  holds stays addressable under its old pid. `OpenProcess` succeeding therefore
  proves nothing; `GetExitCodeProcess` does (`STILL_ACTIVE` = 259 is the only
  live answer);
* **is it the same process?** A freed pid is handed to the next process the
  machine creates, and that process can be *the repair itself*. The lock records
  `process_start` (a creation timestamp, read on Windows through
  `GetProcessTimes` and on Linux from `/proc/<pid>/stat`), and a pid whose start
  time differs is somebody else.

A lock written before this existed has no `process_start`; it falls back to the
pid alone, which is the old behaviour rather than a new failure.

## This is a checksum, not a signature

`RECEIPT_AUTHENTICATION` says it in the module and
`TheReceiptIsChecksumOnly` keeps it honest: the receipt carries a sha256 over its
own canonical body, and the marker carries that digest. Nothing is signed and
nothing is keyed from outside the root. Anyone who can write the root can write
a receipt and a marker that agree with each other and with files they chose, and
this module will accept them.

What it does defend is *inconsistency* — a receipt edited without its digest, a
marker naming a receipt that is not there, a journal whose numbers do not add up.
Real authentication needs a trust root the writer cannot reach: a key, a
transparency log, or a pin delivered by another channel. That is a change to
where the pin comes from, not to this module.

## The real sidecar: `BLOCKED_UPSTREAM`, not a fabricated receipt

```bash
python integrations/fleet-next-wave17-sidecar-release/sidecar_release.py --describe --ref HEAD
```

reports the two files pinned at the current commit, and then:

```
revoke: BLOCKED BLOCKED_UPSTREAM receipt_found= False
```

because no file **is** an install receipt: `upstream_verdict()` looks for a path
named `.sidecar-install-receipt.json` whose body declares schema
`serveros/fleet-sidecar-install-receipt.v1`, and there is none. Every file that
mentions `next_tailscale_gate.py` is a document, a reader or a test — none of
them installs it into a root.

That check is deliberately about the artifact and not about a string. A
`git grep` for the schema finds this module, its tests and its README, because
they all name it; the first version of the check did exactly that and reported
"a receipt exists" for its own source code. It looked correct locally only
because the file was still untracked — `git grep <rev>` reads a commit, not a
worktree. `TheReceiptCheckLooksForAnArtifactNotAString` builds its own
repositories and pins both directions.

When a receipt *is* found the answer changes to `BLOCKED_NOT_VERIFIED`: the
missing piece is then the device rather than the artifact. An offline reader
cannot witness an install on a host, so this module refuses to sign one either
way, and `upstream_verdict()` says "not verified" and "not a real-device pass"
in those words.

`REQUIRED_CHANGES` names what the owning PR (#473 / the sidecar owner) has to
do before a revoke of the real sidecar is verified rather than described. Until
then the honest status of "we can revoke the sidecar" is **unverified**, and the
runbook's `rm -rf` is still the only thing in the world that does it.

## What this module does not do

* It does not install into `/opt`, start, stop, enable or reload anything. It
  installs into a root the caller names; in the tests, a `tempfile` directory.
* It does not shell out to `rm`, `del`, `rmdir` or `shutil.rmtree`; it unlinks
  named files, and an AST test asserts that `subprocess` appears nowhere outside
  `_git`, and that `_git` refuses any verb outside `READ_ONLY_GIT`.
* It does not read a credential, contact a device, or address the Legacy
  service. The Legacy contract (`get_session`, `get_run`, `send`) is held still
  against a fake, by driving the whole install → failed upgrade → upgrade →
  revoke lifecycle and comparing.
* It does not prove a real host is safe.

## Usage

```bash
python integrations/fleet-next-wave17-sidecar-release/sidecar_release.py --describe --ref HEAD
python -m pytest tests/test_fleet_next_wave17_sidecar_release.py -q
python -m pytest tests/test_fleet_next_wave19_sidecar_transactions.py -q
python integrations/fleet-next-wave17-sidecar-release/probe_guards.py
python integrations/fleet-next-wave17-sidecar-release/probe_guards.py \
    --set transaction --tests tests/test_fleet_next_wave19_sidecar_transactions.py
```

The CLI is a **read-only reporter** — `--describe` pins the sidecar and reports
whether a revoke of it is verified. `install`, `revoke`, `restore` and `recover`
are library verbs: every one of them needs a grant, and a command line is not a
grant.

`probe_guards.py` withdraws one protection at a time — replacing a guard's body,
breaking a helper it delegates to, or deleting a name from a declared pipeline —
and reports how many tests notice. It exits 1 if any protection can be withdrawn
without a red run. Withdrawing a guard lets the code *write*, so it runs with its
cwd inside a temporary directory; that is also why the tests that touch relative
paths chdir first.

The case list is split by suite (`--set release` / `--set transaction`), because a
mutation of the repair's guards run against the Wave-17 suite would report
"nothing depends on this" for the honest reason that nothing there writes a
journal. Each run must be silent on its own set; both are run above.

See `docs/integration/FLEET-NEXT-WAVE17-SIDECAR-REVOKE-20261028.md` for the
verdicts, the RED→GREEN record, the mutation-probe table and the handoff.
