# Fleet Next Wave 18 — the install writer whose receipt #506 can revoke

Wave 17 (`integrations/fleet-next-wave17-sidecar-release/`) answered *can this
sidecar be revoked* with a revoker, an ownership marker and a refusal surface —
and then refused to paper over the hole it had found: **nothing in the tree had
ever written the receipt that revoke verifies against**. Its `upstream_verdict()`
said `BLOCKED_UPSTREAM`, and it was right; the evidence did not exist.

This directory is the write side of that same interface.

It installs the **real two files** — not a stand-in, not a copy under a new
name:

```
fleet_mcp/deploy/next_tailscale_gate.py
fleet_mcp/deploy/next_tailscale_plan.example.json
```

The bytes come from `git cat-file blob <pinned commit>:<path>`, re-digested
here. A worktree edited after the pin cannot be installed by naming the
worktree, and a different pair of files cannot be installed by naming a
different descriptor — `_guard_descriptor` compares the pinned paths against
`SIDECAR_FILES` and refuses anything else as `BLOCKED_NOT_THE_SIDECAR`.

## The receipt is Wave 17's, byte for byte

There is one definition of "a receipt", and it lives in
`sidecar_release.py`; this module does not carry a second one:

| piece | who builds it |
|---|---|
| the 12-key receipt body | `sidecar_release._receipt_body()` (via `receipt_body_of()`, which also accepts a public `receipt_body` alias) |
| the digest over that body | `sidecar_release.receipt_digest()` |
| the schema string | `sidecar_release.RECEIPT_SCHEMA` → `serveros/fleet-sidecar-install-receipt.v1` |
| the ownership marker's schema | `sidecar_release.MARKER_SCHEMA` → `serveros/fleet-sidecar-owner-marker.v1` |

`UpstreamConformance` in the test suite asserts this directly: for the same
install, the body this module writes has exactly the keys #506's
`_receipt_body()` produces (plus `receipt_digest`), and
`up.receipt_digest(their_body) == this_module.receipt_digest(our_body)`.

The marker is #506's four compared keys (`install_root`, `device_ref`,
`version`, `receipt_digest`) plus the facts #506's marker guard tolerates and a
revoke needs: `next_backend_id`, `installed_by`, `installer_schema`, `name`,
`manifest_digest`, `schema` — ten keys in one file rather than a second schema
beside it.

## What the receipt authenticates — and what it does not

The receipt carries a sha256 over its own canonical body. That is a
**checksum**, not a signature.

Anyone who can write the root can write a receipt and recompute the checksum,
and this module will verify it. Without a trusted root of trust — a key held
outside the root, a transparency log, a pin delivered by another channel — an
attacker with write access to the root is **not detected**. What the checksum
does buy is that an ordinary edit, a half-written file, or a receipt describing
a different version cannot pass as *this* install.

`RECEIPT_AUTHENTICATION` says exactly this, `VERIFICATION["receipt_authentication"]`
repeats it in every JSON report, and no test in this directory asserts the word
"signed" anywhere in the module's output — because it never claims to be.

## Writes are atomic, and an upgrade rolls back

| step | how |
|---|---|
| fresh install | the whole tree is staged in a **sibling** directory `<root>.wave18-staging-<pid>-<ms>`, verified there, then appears at the root in one `os.rename`; there is no instant at which the root holds half an install |
| upgrade | old bytes are copied into `<root>/.sidecar-backup/<old.version>/`, the old receipt is held in memory, each file is replaced with `os.replace` (atomic per file), then marker, then receipt |
| injected write failure (`fail_at`) | `InjectedFailure` unwinds the same path a crash would: the rollback runs synchronously, the old bytes and old receipt come back, the run reports `BLOCKED_ABORTED`, and the root is byte-identical to before |
| simulated death (`crash_at`) | `SimulatedCrash(BaseException)` — no `except Exception` anywhere can swallow it, no cleanup runs — and `recover()` is what repairs the root afterwards |
| two writers | a cross-process `O_CREAT\|O_EXCL` lock beside the root; a live holder is `BLOCKED_CONCURRENT_WRITER`, a dead one is named `BLOCKED_STALE_WRITER` rather than ignored, and only `recover()` clears a stale lock |

`crash_at` and `fail_at` accept the same eight points:
`staged-files`, `staged-marker`, `staged-receipt`, `before-rename`,
`after-backup`, `after-files`, `after-marker`, `after-receipt`. The test suite
runs the upgrade subset of them and asserts, for each, which receipt survives
and whether `recover()` restores or discards.

## Refusals, by name

Every refusal is a named code. Codes reused from #506 keep #506's spelling, so
an operator reading both reports reads one vocabulary.

**Interface and preconditions**

| code | when |
|---|---|
| `BLOCKED_INTERFACE` | `sidecar_release` is missing a name this module needs; the report carries a minimal patch, not a workaround |
| `BLOCKED_IDENTITY_INCOMPLETE` | `device_ref`, `boot_id`, `next_backend_id` or `operator` is empty |
| `BLOCKED_IDENTITY_CONFLATED` | `next_backend_id == device_ref` — one host identity and one backend identity are two facts |
| `BLOCKED_NO_GRANT` | no grant, or a grant whose `action` does not cover the verb, or a grant with an empty `grant_id` |
| `BLOCKED_IDENTITY_MISMATCH` | the grant names another device or another operator |
| `BLOCKED_BOOT_MISMATCH` | the receipt on disk was written on another boot |
| `BLOCKED_NO_PROVENANCE` | no provenance, or it names no commit |
| `BLOCKED_PROVENANCE_MISMATCH` | the provenance commit differs from the descriptor's, or the bytes at that commit do not have the pinned digest |
| `BLOCKED_NOT_THE_SIDECAR` | the descriptor pins something other than the two files above, or names something other than `sidecar` |
| `BLOCKED_NOT_PINNED` / `BLOCKED_DESCRIPTOR_PATH` | a descriptor without a commit, or with an absolute/traversing path (#506's own codes, via `check_descriptor`) |

**Where the root may be**

| code | when |
|---|---|
| `BLOCKED_ROOT_NOT_ABSOLUTE` | a relative root |
| `BLOCKED_ROOT_NOT_ISOLATED` | the filesystem root, the home directory, or an ancestor of home |
| `BLOCKED_ROOT_IS_SYMLINK` | the root itself is a link (symlink *or* Windows junction) |
| `BLOCKED_ROOT_NOT_NAMESPACED` | no allowed prefix supplied; the root is the prefix itself; the root is outside every prefix; or the leaf is not named `sidecar` |
| `BLOCKED_PARENT_ESCAPE` | a link anywhere in the parent chain — a junction up there silently redirects every write and a resolved path would not show it |
| `BLOCKED_LEGACY_ROOT` | the root is a Legacy root, is inside one, or contains one |

**What is already there**

| code | when |
|---|---|
| `BLOCKED_ROOT_NOT_EMPTY` | the root exists, is not a directory, or holds files and no receipt |
| `BLOCKED_STALE_STAGING` | a staging directory from an interrupted run sits beside the root; `recover()` decides what it is |
| `BLOCKED_ALREADY_INSTALLED` | that version is already installed |
| `BLOCKED_UPGRADE_CHAIN` | the receipt already remembers a previous version; a second upgrade would overwrite the only rollback target |
| `BLOCKED_FOREIGN_PRESENT` | the root holds files the receipt does not own — carrying them forward would make the result unrevocable |
| `BLOCKED_RECEIPT_TAMPERED` / `BLOCKED_RECEIPT_MISMATCH` | the receipt does not verify, or names a different root/version/commit/manifest |
| `BLOCKED_MODIFIED_AFTER_INSTALL` | a file in the root is not the byte the receipt records |
| `BLOCKED_BACKUP_MODIFIED` | a file in `.sidecar-backup/` is not the byte it should be |
| `BLOCKED_FILE_ABSENT` | a file the receipt records is gone |

**Revoke and rollback only**

| code | when |
|---|---|
| `BLOCKED_NO_RECEIPT` | there is no receipt to revoke against; deleting without one is the operation this whole surface exists to refuse |
| `BLOCKED_BACKEND_MISSING` | the marker is gone, or records no `next_backend_id` |
| `BLOCKED_BACKEND_MISMATCH` | the install was made for another backend |
| `BLOCKED_OWNER_MARKER_MISSING` / `..._MISMATCH` | #506's own marker guard |
| `BLOCKED_ABORTED` | a write failed mid-run; the rollback ran and the root is byte-identical to before |
| `BLOCKED_VERIFY_FAILED` | the staged tree does not hold the pinned bytes |

## Guards are declared as names

`INSTALL_GUARDS`, `REVOKE_GUARDS` and `RESTORE_GUARDS` are tuples of **names**,
resolved at call time by `_pipeline()`. A tuple of function objects is bound at
import, so rebinding one guard would leave the pipeline unchanged and "withdraw
exactly one guard and see what turns red" would measure nothing. `_pipeline()`
raises `AssertionError` for a name that is not a callable in this module, so a
typo is a loud failure rather than a silently shorter pipeline. The test suite
asserts all of the above, and that the three lists are not the same list.

A rollback needs a `restore` grant and a receipt that remembers a previous
version, which is a different question from a revoke — hence the third list
rather than borrowing the second.

## Negative controls: withdraw one guard, watch it go red

`probe_guards.py` makes that mechanical. It runs the suite once to establish a
green baseline, then for every declared guard changes exactly one thing and
requires at least one previously-green test to turn red.

Three axes, because a guard can go missing three ways:

* **body** — the function is replaced by one that returns `None` ("does anything
  depend on this check?").
* **tuple** — the name is dropped from its pipeline. This catches a guard that
  is correct but no longer *called*, which the body axis cannot see.
* **helper** — `_is_link` (→ `Path.is_symlink()`, which cannot see a junction),
  `_staging_candidates` (→ `()`), `pid_alive` (→ `True`). Guards can all be
  present and still be wrong if what they ask is wrong.

A mutation nothing notices prints `SILENT` and the probe exits non-zero. Tests
that run the module in a **separate interpreter** cannot observe an in-process
rebinding, so they are excluded and named in the output. The probe chdirs into a
temporary directory first: withdrawing a guard lets the code write, and a
relative root that slips through must not land in the checkout.

### What the first run changed

The probe was not written to confirm the module; it was written to find out. Its
first run, on the pre-review module, reported **41 mutations, 31 flipped, 10
SILENT** — and every one of the ten was a real defect in the *evidence*, not in
the code:

| silent mutation | why nothing noticed | what changed |
|---|---|---|
| `body:_guard_grant_revoke`, `body:_guard_grant_restore` | #506 compares a grant's `device_ref` and never its `operator`; with the guard withdrawn a grant issued to somebody else simply worked, and no test asked | two tests: a revoke and a rollback with another operator's grant, both refused `BLOCKED_IDENTITY_MISMATCH`, root untouched |
| `tuple:restore:_guard_interface`, `…identity`, `…lock`, `…receipt_first`, `…backend` | the rollback path had one test per *success*, and none per refusal, so a guard could leave the pipeline unnoticed | five refusal tests: a shim missing a name, an incomplete identity, a live writer's lock, no receipt, and a deleted marker |
| `body:_guard_root_revoke`, `tuple:revoke:_guard_root_revoke` | the check was a verbatim copy of #506's: same function, same arguments, same code — nothing to observe | **removed**. Delegated instead: `revoke_verified` hands `legacy_roots` to `revoke()`, whose `_guard_root` already runs it |

After that work: **39 mutations, 39 flipped, 0 silent** (`exit 0`). Two of the
levers were the tests above; one was deleting a check that refused nothing the
upstream did not already refuse. A second identical check is not a control, and
keeping it would have left the probe reporting a guard that could not be
measured — the exact failure mode the probe exists to catch.

## CLI

Dry run is the default; `--apply` is the only mode that touches the disk.

```bash
# which names this module needs from #506, and which are present
python integrations/fleet-next-wave18-sidecar-installer/installer.py --interface

# plan (writes nothing)
python integrations/fleet-next-wave18-sidecar-installer/installer.py \
    --repo . --ref <commit> --root <prefix>/sidecar --prefix <prefix> \
    --device-ref dev-1 --boot-id <boot> --backend-id backend-A --operator op-1 \
    --grant-id g-install

# the same call, writing
python ... --apply

# revoke through #506's revoke(), using the receipt this install wrote
python ... --revoke --ref <installed commit> --grant-id g-revoke ...

# repair what a crashed run left
python ... --recover --root <prefix>/sidecar
```

Every verb prints one JSON document with `schema`, `verb`, `decision`, `code`,
`reasons`, and `verification` (`source: SOURCE_VERIFIED`,
`device: REAL_DEVICE_NOT_RUN`, `receipt_authentication: checksum-only …`).
Exit status is `0` for `OK`/`DRY_RUN`/`NOTHING_TO_RECOVER`, `2` for a refusal.

## Verification split

| | status | what it means |
|---|---|---|
| **SOURCE_VERIFIED** | ✅ | The two real files install, upgrade, roll back and revoke inside `tempfile` roots, with refusals that flip when their guard is withdrawn. Nothing was executed against `/opt`, a systemd unit, or any device. |
| **REAL_DEVICE_NOT_RUN** | ⛔ | No Tailscale, no `systemd`, no service start, no real `sidecar` root, no real backend. The installer has never written outside a temporary directory, and the receipt has never been read by a real revoker on a real host. |

## Findings this round reported to #506's owner

Neither is fixed by editing #506's files; both are named in `FINDINGS` and in the
PR body.

* **F1** — `sidecar_release.restore()` builds the ownership marker from its own
  seven keys, dropping everything else, so the `next_backend_id` this installer
  records is lost by a restore. `rollback_to_previous()` re-adds it from the
  surviving marker, which is a patch on *this* side of the interface; a direct
  call to `restore()` leaves a root whose backend identity is unrecorded, and
  the next revoke says `BLOCKED_BACKEND_MISSING`. Minimal fix for the owner:
  build the marker in `restore()` from the existing marker and overwrite the
  seven known keys, instead of constructing a fresh dict.
* **F2** — `sidecar_release.install()` writes files one at a time and the
  receipt last, so its own crash window leaves a root a revoke refuses as
  `BLOCKED_MODIFIED_AFTER_INSTALL`. Not a defect in `install()`, which never
  claimed atomicity — it is why this writer does not reuse it.

## Running it

```bash
python -m pytest tests/test_fleet_next_wave18_sidecar_installer.py -q
python -m unittest tests.test_fleet_next_wave18_sidecar_installer -v
python integrations/fleet-next-wave18-sidecar-installer/probe_guards.py
```

The tests build their own two-commit git repository in a temporary directory
from the real blobs, so they need no network, no device and no fixture checked
into the tree. See `docs/integration/FLEET-NEXT-WAVE18-SIDECAR-20261029.md` for
the full evidence record.
