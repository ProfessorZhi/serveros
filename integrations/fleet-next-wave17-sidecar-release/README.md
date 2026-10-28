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

## The real sidecar: `BLOCKED_UPSTREAM`, not a fabricated receipt

```bash
python integrations/fleet-next-wave17-sidecar-release/sidecar_release.py --describe --ref HEAD
```

reports the two files pinned at the current commit, and then:

```
revoke: BLOCKED BLOCKED_UPSTREAM receipt_found= False
```

Because `git grep -l -F "serveros/fleet-sidecar-install-receipt.v1"` finds
nothing in the tree, and every reference to `next_tailscale_gate.py` is a
document, a reader or a test — none of them installs it into a root. An offline
reader cannot witness an install that has no record, so this module refuses to
sign one. It is **not** a real-device pass, and `upstream_verdict()` says so in
those words.

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
```

See `docs/integration/FLEET-NEXT-WAVE17-SIDECAR-REVOKE-20261028.md` for the
verdicts, the RED→GREEN record, the mutation-probe table and the handoff.
