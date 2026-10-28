# Wave 18 · The install writer whose receipt #506 can revoke (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros` source and offline tests only.
Role: original #506 / sidecar-owner QA, continuing the release-safety line opened
in #502 and left `BLOCKED_UPSTREAM` by Wave 17.
Base: stacked on **#506 @ `30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7`**
(`qa/fleet-next-wave17-sidecar-revoke-20261028`).

## 0. Read this first

Everything below was produced against Python temporary directories and a git
repository this round created inside one of them. No sidecar was installed on a
host, no `systemd` unit was written or started, no Tailscale was installed, no
port was bound, no socket was opened, no credential was read, no IM message was
sent, no device or GPU was touched, and nothing outside a `tempfile` directory
was created or deleted. **A green result here is not a real-device pass.**

The one thing this round closes is the hole Wave 17 named: the tree now contains
a writer that produces the receipt `sidecar_release.revoke()` verifies against,
so the question "can the real sidecar be revoked" has evidence behind it instead
of a synthetic release. It still has not happened on a device — see §9.

## 1. Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | The installed artifact is the **real** two-file sidecar (`fleet_mcp/deploy/next_tailscale_gate.py`, `next_tailscale_plan.example.json`), read from a pinned commit | **VERIFIED** — real gate >20 KB, plan is the real schema, no stand-in file anywhere in the suite |
| 2 | Version + source SHA + two sha256 provenance pins; bytes re-digested from `git cat-file blob` | **VERIFIED** — worktree edited after the pin cannot be installed |
| 3 | Allowed-root namespacing (`<prefix>/sidecar`), operator identity, boot, device **and a separate Next backend id** | **VERIFIED** |
| 4 | Owner marker + checksum-valid receipt of schema `serveros/fleet-sidecar-install-receipt.v1` | **VERIFIED** — body from #506's `_receipt_body`, digest from #506's `receipt_digest`, shape equality asserted |
| 5 | The receipt is **not** a signature and is not described as one | **VERIFIED** — `checksum-only…NOT a signature` in every report; a test asserts the module never claims "signed" |
| 6 | Atomic writes, cross-process lock, crash before/after the receipt, failed-upgrade rollback | **VERIFIED (offline)** |
| 7 | Denies: changed original bytes, symlink/junction root, filesystem root, home, parent-chain link, Legacy containment | **VERIFIED** — each with its own code |
| 8 | **#506's revoke consumes the receipt this installer wrote**, in an independent Python process | **VERIFIED** — install in one interpreter, revoke in another, `OK`; every file gone, root gone |
| 9 | Second revoke: idempotent **and** refused by name (`BLOCKED_NO_RECEIPT`), not `OK` | **VERIFIED** |
| 10 | install → new process (restart) → upgrade failure → rollback → revoke, with Legacy state unchanged | **VERIFIED (offline)** |
| 11 | Every refusal's negative control turns **red** when its guard is withdrawn | **VERIFIED** — 39 mutations, 39 flipped, 0 silent |
| 12 | The same on a real host, real backend, real `systemd`/Tailscale | **REAL_DEVICE_NOT_RUN** |

## 2. The hole #506 left, and what closes it

Wave 17's module can revoke a sidecar **if** a receipt exists, and it proved that
against releases it minted itself. Its own verdict line for the production
artifact was `BLOCKED_UPSTREAM`: nothing in the tree had ever written the receipt
a revoke verifies, so "the real sidecar is revocable" was **NOT VERIFIED**, and
no amount of revoke testing could change that.

This round is the write side of the same interface:

```
integrations/fleet-next-wave18-sidecar-installer/
  installer.py        the writer: guards, plan, atomic apply, recover, CLI
  probe_guards.py     withdraw one guard at a time; require a test to go red
  README.md           the module's own surface (refusal table, pipelines, limits)
tests/test_fleet_next_wave18_sidecar_installer.py
```

Nothing in `fleet_mcp/**`, nothing in #506's directory and no Legacy
configuration was modified. The revoker is consumed through its real API
(`revoke()`, `restore()`, `check_descriptor()`, `read_receipt()`,
`receipt_digest()`, `observe_legacy()`, `legacy_drift()`), and the module
declares the names it needs; when one is missing the answer is
`BLOCKED_INTERFACE` plus a minimal patch, never a local copy of the format.

## 3. The pin, and what "installed" means

| | |
|---|---|
| files | `fleet_mcp/deploy/next_tailscale_gate.py`, `fleet_mcp/deploy/next_tailscale_plan.example.json` |
| version | `1.0.0+test` / `2.0.0+test` (two pinned commits, so upgrade is a real upgrade) |
| source | `git cat-file blob <commit>:<path>`, re-digested here |
| `git` verbs this module may run | `rev-parse`, `cat-file` — and only those; `git()` raises `ValueError` on anything else, asserted by AST and by call |
| provenance record | commit + per-file sha256, cross-checked against the descriptor before any write |

`_guard_descriptor` refuses a descriptor that pins anything other than those two
paths, or names anything other than `sidecar`, as `BLOCKED_NOT_THE_SIDECAR` — so
"a receipt for some other pair" cannot be presented as evidence about the
sidecar.

## 4. Conformance with #506's interface, measured both ways

* **Shape:** for the same install, the body this module writes has exactly the
  keys `sidecar_release._receipt_body()` produces (twelve, `previous` always
  present, `files` as dicts), plus `receipt_digest`; and
  `up.receipt_digest(their_body) == this_module.receipt_digest(our_body)`.
* **Behaviour, in both directions:** seven conditions (non-empty root, Legacy
  root, bytes that do not match the pin, no grant, a root that is a link, an
  unpinned version, a relative root) are asserted with `m.install(...)` and with
  `up.install(...)`, and the two answers are compared rather than eyeballed.
* **Where this module is deliberately stricter:** namespacing
  (`BLOCKED_ROOT_NOT_NAMESPACED`), the grant's **operator**, the separate backend
  identity, the sibling cross-process lock, and the staging-directory check have
  no counterpart upstream. A test asserts the namespacing one is this module's
  guard and not the upstream's, so the difference cannot be mistaken for
  conformance.

## 5. Refusals

38 codes are declared. **24 are #506's spelling**, re-exported where the
condition is the same condition, so an operator reading both reports reads one
vocabulary; **14 are new**, because the install side asks questions the revoke
side never had to ask (`BLOCKED_NOT_THE_SIDECAR`, `BLOCKED_NO_PROVENANCE`,
`BLOCKED_PROVENANCE_MISMATCH`, `BLOCKED_IDENTITY_INCOMPLETE`,
`BLOCKED_IDENTITY_CONFLATED`, `BLOCKED_ROOT_NOT_NAMESPACED`,
`BLOCKED_PARENT_ESCAPE`, `BLOCKED_STALE_WRITER`, `BLOCKED_STALE_STAGING`,
`BLOCKED_BACKEND_MISSING`, `BLOCKED_BACKEND_MISMATCH`, `BLOCKED_ABORTED`,
`BLOCKED_VERIFY_FAILED`, `BLOCKED_INTERFACE`).

29 are emitted by code in this module; the other nine arrive as **#506's own
verdicts** through the delegated guards (`_root_refusal`, `check_descriptor`,
`revoke`, `restore`) and are declared here only so a test can name the refusal it
expects.

**34 of the 38 are asserted by name in the suite.** The four that are not are
backstops rather than measured refusals, and are recorded as such:
`BLOCKED_FILE_ABSENT`, `BLOCKED_OWNER_MARKER_MISMATCH` and
`BLOCKED_SYMLINK_TRAVERSAL` are #506's refusals inside delegated guards (covered
by #506's own suite, not re-asserted here), and `BLOCKED_VERIFY_FAILED` is this
module's post-staging check, which the digest guards refuse before it can fire —
no test drives it, so it is a backstop and not evidence.

The full table lives in
`integrations/fleet-next-wave18-sidecar-installer/README.md`; this document does
not duplicate it, because a refusal table repeated in two places stops matching
itself.

## 6. Atomicity, death and rollback — what was actually exercised

| scenario | evidence |
|---|---|
| fresh install | the tree is staged in `<root>.wave18-staging-<pid>-<ms>` **beside** the root, verified there, then appears in one `os.rename`; the test asserts the root has no partial state and that nothing is left beside it |
| refusal after an empty pre-existing root | refused at `os.rename`, not by a byte check that would pass and leave debris |
| death at `before-rename` | no root, one staging directory, `recover()` discards exactly that and names nothing else |
| death during upgrade, four points | `after-backup`, `after-files`, `after-marker`, `after-receipt` — for each, the receipt that survives is asserted, then `recover()` + revoke of whichever version survives |
| failed upgrade (`fail_at`) | synchronous rollback, `BLOCKED_ABORTED`, root byte-identical to before, retryable |
| two writers | `O_CREAT\|O_EXCL` sibling lock; a live holder is `BLOCKED_CONCURRENT_WRITER`, a dead one `BLOCKED_STALE_WRITER`; a real second process is used for one of these |
| rollback | one level, restore grant, backend identity re-attached after #506's seven-key marker rewrite (F1), then a revoke that succeeds |

The `IndependentProcesses` class repeats the lifecycle as **separate Python
interpreters**: install → revoke → second revoke (`BLOCKED_NO_RECEIPT`,
nothing deleted); install → `--fail-at after-files` → revoke; install →
`--crash-at after-files` (exit 1) → lock reconstructed → `--recover` → revoke;
`--plan` writes nothing; `--interface` reports compatible. That is what "restart"
means for this artifact: a process that has only the files on disk.

## 7. The mutation probe: the first run was red, and that is the result

`probe_guards.py` runs the suite once (baseline), then for every declared guard
withdraws exactly one thing — the guard's body, or its name from its pipeline —
and for three helpers replaces them with a plausible wrong answer. A mutation no
test notices is printed as SILENT and the probe exits non-zero.

**First run, against the module as first written: 41 mutations, 31 flipped, 10
SILENT.** The ten were not noise; each named a hole in the evidence:

| silent mutation | why nothing noticed | change made |
|---|---|---|
| `body:_guard_grant_revoke` | #506 compares a grant's `device_ref`, never its `operator`; with the guard gone another operator's grant revoked successfully, and no test asked | `RevokeThroughUpstream.test_a_revoke_grant_for_another_operator_is_refused` |
| `body:_guard_grant_restore` | same, on the rollback path | `UpgradeAndRestore.test_a_rollback_grant_for_another_operator_is_refused` |
| `tuple:restore:_guard_interface` | the rollback path had tests for each success and none for a refusal | `…test_a_rollback_against_an_incompatible_upstream_is_a_named_refusal` |
| `tuple:restore:_guard_identity` | an empty `operator` sails past `restore()`, which checks device and boot only | `…test_a_rollback_with_an_incomplete_identity_is_refused` |
| `tuple:restore:_guard_lock` | the sibling lock is this module's; #506's `_guard_writer` looks at the in-root lock | `…test_a_rollback_while_a_writer_holds_the_root_is_refused` |
| `tuple:restore:_guard_receipt_first` | order between two absences was never asserted | `…test_a_rollback_without_a_receipt_is_refused_by_name` |
| `tuple:restore:_guard_backend` | `restore()` never needs the old marker — it writes a fresh seven-key one, so a rollback over a markerless root **succeeded** | `…test_a_rollback_will_not_mint_a_root_with_no_backend` |
| `body:_guard_root_revoke`, `tuple:revoke:_guard_root_revoke` | the check was a verbatim copy of #506's — same function, same arguments, same code | **removed**; `revoke_verified` hands `legacy_roots` to `revoke()`, whose `_guard_root` runs it. A second identical check is not a control |

**Second run: 39 mutations, 39 flipped, 0 silent, exit 0** (baseline 134 selected
tests green in 48.4 s). Every campaign, and the test that caught it:

| axis | guard or helper | first test that went red |
|---|---|---|
| body | `_guard_interface` | `InterfaceShape.test_a_missing_name_is_named_with_a_patch` |
| body | `_guard_identity` | `IdentityAndGrant.test_an_incomplete_identity_is_refused` |
| body | `_guard_grant` | `IdentityAndGrant.test_a_grant_for_another_device_is_refused` |
| body | `_guard_descriptor` | `ProvenanceAndDescriptor.test_a_descriptor_that_pins_more_than_the_sidecar_is_refused` |
| body | `_guard_provenance` | `ProvenanceAndDescriptor.test_descriptor_and_provenance_must_name_the_same_commit` |
| body | `_guard_root` | `RootAndNamespace.test_a_legacy_root_is_refused_when_it_is_the_root` |
| body | `_guard_lock` | `LockingAndConcurrency.test_a_lock_from_a_dead_process_is_named_not_ignored` |
| body | `_guard_destination` | `DeathAndRecovery.test_a_death_at_each_upgrade_step_still_revokes_something_honest` |
| body | `_guard_upgrade` | `DryRunAndInstall.test_a_second_install_of_the_same_version_is_refused` |
| body | `_guard_sources` | `ProvenanceAndDescriptor.test_caller_supplied_bytes_are_checked_against_the_pin` |
| body | `_guard_grant_revoke` | `RevokeThroughUpstream.test_a_revoke_grant_for_another_operator_is_refused` |
| body | `_guard_receipt_first` | `DeathAndRecovery.test_a_death_before_the_rename_leaves_no_root` |
| body | `_guard_backend` | `RevokeThroughUpstream.test_a_marker_without_a_backend_is_refused` |
| body | `_guard_grant_restore` | `UpgradeAndRestore.test_a_rollback_grant_for_another_operator_is_refused` |
| tuple:install | `_guard_interface` … `_guard_sources` (10 names) | same tests as their bodies, except `_guard_lock` → `InterfaceShape.test_pipelines_resolve_names_at_call_time` (the pipeline no longer resolves it) |
| tuple:revoke | `_guard_interface`, `_guard_identity` | `InterfaceShape.test_the_three_pipelines_ask_different_questions` |
| tuple:revoke | `_guard_grant_revoke` | `RevokeThroughUpstream.test_a_revoke_grant_for_another_operator_is_refused` |
| tuple:revoke | `_guard_lock` | `InterfaceShape.test_pipelines_resolve_names_at_call_time` |
| tuple:revoke | `_guard_receipt_first` | `DeathAndRecovery.test_a_death_before_the_rename_leaves_no_root` |
| tuple:revoke | `_guard_backend` | `RevokeThroughUpstream.test_a_marker_without_a_backend_is_refused` |
| tuple:restore | `_guard_interface` | `UpgradeAndRestore.test_a_rollback_against_an_incompatible_upstream_is_a_named_refusal` |
| tuple:restore | `_guard_identity` | `UpgradeAndRestore.test_a_rollback_with_an_incomplete_identity_is_refused` |
| tuple:restore | `_guard_grant_restore` | `InterfaceShape.test_the_three_pipelines_ask_different_questions` |
| tuple:restore | `_guard_lock` | `UpgradeAndRestore.test_a_rollback_while_a_writer_holds_the_root_is_refused` |
| tuple:restore | `_guard_receipt_first` | `UpgradeAndRestore.test_a_rollback_without_a_receipt_is_refused_by_name` |
| tuple:restore | `_guard_backend` | `UpgradeAndRestore.test_a_rollback_will_not_mint_a_root_with_no_backend` |
| helper | `_is_link` → `Path.is_symlink()` (cannot see a junction) | `RootAndNamespace.test_a_link_on_the_way_to_the_root_is_refused` |
| helper | `_staging_candidates` → `()` | `DeathAndRecovery.test_a_death_before_the_rename_leaves_no_root` |
| helper | `pid_alive` → `True` | `DeathAndRecovery.test_a_death_at_each_upgrade_step_still_revokes_something_honest` |

Two limits, stated rather than glossed: tests that run the module in a **separate
interpreter** (`IndependentProcesses`, three others) cannot observe an in-process
rebinding and are excluded and named; and the probe prints at most the *first*
test that caught a mutation, so the table is a lower bound on coverage per guard.

## 8. Findings handed to #506's owner

Neither is fixed by editing #506's files; both are in `FINDINGS` and in the
`--interface` report.

* **F1** — `sidecar_release.restore()` builds the ownership marker from its own
  seven keys and drops everything else, so the `next_backend_id` this installer
  records is lost by a restore. `rollback_to_previous()` re-attaches it from the
  surviving marker, which is a repair on *this* side of the interface; a direct
  call to `restore()` leaves a root whose next revoke says
  `BLOCKED_BACKEND_MISSING`. Minimal patch: build the marker in `restore()` from
  the existing marker and overwrite the seven known keys instead of constructing
  a fresh dict.
* **F2** — `sidecar_release.install()` writes files one at a time with the
  receipt last, so its own crash window leaves a root that a revoke refuses as
  `BLOCKED_MODIFIED_AFTER_INSTALL`. Not a defect in `install()`, which never
  claimed atomicity — it is why this writer does not reuse it.

## 9. Verification split

| | |
|---|---|
| **SOURCE_VERIFIED** | The real two files install, upgrade, roll back and revoke inside `tempfile` roots; the receipt is consumed by #506's real `revoke()` across process boundaries; every refusal flips when its guard is withdrawn; a second revoke is refused by name and deletes nothing; Legacy `get_session` / `get_run` / deliveries are byte-identical before and after install → upgrade → revoke, and the drift detector is shown to notice a real change. |
| **REAL_DEVICE_NOT_RUN** | No Tailscale, no `systemd`, no service start/stop, no real `sidecar` root, no real Next backend, no real operator grant, no real host boot id. The installer has never written outside a temporary directory; the receipt has never been read by a revoker on a host; and because the receipt is **checksum-only**, an attacker who can write the root is not detected without a root of trust outside it — that is true here and would remain true on a device. |

## 10. What this round did not do

No Tailscale or `systemd` installed on any machine; no file deleted outside a
`tempfile` directory; no real service, dataset, plugin, IM channel or GPU
touched; nothing merged to `main`, no production or secret path written, no
Legacy configuration or `fleet_mcp/**` file modified; no credential read. The
only git verbs the module runs are `rev-parse` and `cat-file`.

## 11. Reproduce

```bash
python -m pytest tests/test_fleet_next_wave18_sidecar_installer.py -q
python -m unittest tests.test_fleet_next_wave18_sidecar_installer      # 142 tests, OK
python integrations/fleet-next-wave18-sidecar-installer/probe_guards.py
```

The suite builds its own two-commit repository in a temporary directory from the
real blobs at `HEAD`, so it needs no network, no fixture in the tree and no
device. The probe chdirs into a temporary directory before it starts, because
withdrawing a guard lets the code write.

## 12. Handoff

* **#506** (`sidecar_release.py` owner) — findings **F1** and **F2**; both are
  one-hunk changes on their side and neither is patched here.
* **#502** (release-unit measurement) — the sidecar's `revocable` cell no longer
  has to read `no receipt exists`; there is now a writer whose output the revoker
  verifies, in-process and across processes.
* **#478** (Legacy dual-track / Agent Monitor acceptance) — the install →
  upgrade → revoke cycle leaves the Legacy `get_session` / `get_run` /
  deliveries observation byte-identical, and the drift detector is shown to
  notice a real change, which is the dual-track evidence this round can offer
  offline. It is a fake, so it is not a Legacy acceptance pass.
* **#425** (device acceptance) — this is the work that must be run **on a
  device** to move `REAL_DEVICE_NOT_RUN`: a real root under an allowed prefix, a
  real boot id, a real backend id, a real grant, and a revoke afterwards. Until
  that happens, a green run here certifies the source, not the host.
