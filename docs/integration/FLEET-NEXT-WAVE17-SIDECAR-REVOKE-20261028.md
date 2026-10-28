# Wave 17 · Sidecar as a revocable release unit (offline)

Date: 2026-10-28
Scope: `ProfessorZhi/serveros` source and offline tests only.
Role: continuation of the independent release-safety QA opened in #502, as its
original owner. This is **not** an RC integration, **not** a device pass, and
**not** a Legacy production operation.

Base: `main @ e69fc0337ae5d33000478e3d5162077f525bc8cc`

## 0. Read this first

Everything below was produced against fakes and Python temporary directories.
No sidecar was installed on any host, no service manager was addressed, no port
was bound, no socket was opened, no credential was read, no Feishu message was
sent, no device was contacted, and no real data was deleted. **A green result
here is not a real-device pass**, and the one thing it explicitly does not
certify is the thing #502 complained about: that the *real* sidecar can be
revoked today. See §8.

## 1. Verdict summary

| # | Item | Verdict |
|---|---|---|
| 1 | Release is pinned (version, 40-hex commit, per-file sha256, manifest) | **VERIFIED (offline)** |
| 2 | Manifest is reproducible from installed bytes | **VERIFIED** — recomputed from the files, twice |
| 3 | Install leaves evidence that a revoke can check | **VERIFIED** — receipt + independent ownership marker |
| 4 | Revoke deletes only receipt-owned files inside the receipt's root | **VERIFIED** — 26 refusal codes, all 26 asserted by tests |
| 5 | Revoke refuses a stranger's directory, a shared/Legacy root, a link, a traversal, a foreign file, a post-install edit, a live writer, another device/boot, and missing evidence | **VERIFIED (offline)** |
| 6 | Second revoke is idempotent — tree unchanged **and** refused, not `OK` | **VERIFIED** |
| 7 | Upgrade + restore, one level of history | **VERIFIED (offline)** |
| 8 | Legacy `get_session` / `get_run` / `send` unchanged across install → failed upgrade → upgrade → revoke | **VERIFIED with a fake; the positive control flips** |
| 9 | The **real** sidecar (`next_tailscale_gate.py` + plan example) is revocable | **BLOCKED_UPSTREAM** — no install receipt exists anywhere in the tree, so a revoke of it is **NOT VERIFIED** |
| 10 | Hosted CI for this PR | recorded in §9, not inferred |

The headline is #9 and it is a refusal, not a pass. #502's finding stands: what
was missing was a reviewed revoke entrypoint. This module supplies one and
proves it against synthetic releases; it cannot manufacture the record of an
install that never happened, so it says so and hands the fix to the file's
owner.

## 2. What #502 left, and what "revocable" means now

#502 measured three release units at `e69fc033`: Hub (BLOCKED, imports the Node
package), Node (BLOCKED, imports 10 Hub modules), Sidecar (**reproducible ✓,
independent ✓, revocable ✗**). The Sidecar's bytes are stdlib-only, so it is the
one unit that could be released on its own — and undoing it was `rm -rf` typed by
a person who remembered the path.

A descriptor and a delete call do not fix that. What fixes it is an artifact
that can *refuse*, so "revocable" here means four separate things, each with its
own test:

1. the thing to undo is **pinned**, so nobody argues about which bytes;
2. the install **left evidence**, so a revoke is checking a record rather than
   trusting a directory that happens to look right;
3. every condition under which deleting would be wrong is a **refusal with its
   own code**, not a warning;
4. undoing twice **deletes nothing and refuses**, rather than reporting success
   for work it did not do.

## 3. The pin, and why the manifest recipe is #473's

```bash
python integrations/fleet-next-wave17-sidecar-release/sidecar_release.py --describe --ref e69fc033
```

```
version: 0.1.0+wave17   commit: e69fc033   files: 2   check: OK
manifest sha256:9e40841c81cc39d919d013c974d6bc7a8c7ee1a5360888f066a0f6276ed09d7f
  fleet_mcp/deploy/next_tailscale_gate.py           sha256:4b8aa64cc9fe3afc0f12fcbed24ac70b7ccefe35c220ea067f6c666c6012b81d
  fleet_mcp/deploy/next_tailscale_plan.example.json sha256:991cc9c5163efe6a9c515bd16f3165ce6ca6ad60dfe0a7e652e0c425f0ef01e7
```

The same two digests come out at this PR's head — the pin is over the sidecar's
bytes, which this PR does not touch.

* `VERSION_RE` is #473's pattern, character for character, so a version one
  accepts the other accepts. Two spellings of "a version" would let a descriptor
  carry something the artifact reader would have refused.
* The manifest is #473's `_tree_digest` recipe —
  `"".join(f"{digest}  {path}\n")`, two spaces, hashed again.
* **Content** hashes, not git blob SHAs. This is the opposite choice from
  Wave 16, deliberately: Wave 16 was asking "did the release unit move between
  two commits", where a blob SHA is the right identity (it is the same on every
  checkout and immune to `core.autocrlf`). A revoke asks "are the bytes on disk
  still the bytes we installed", and only a content hash can answer that. The
  two modules therefore print different digests for the same files, and both are
  correct for their question. Saying so here so the difference does not get read
  as drift.

`BLOCKED_NOT_PINNED` covers `latest`, `*`, `1.0`, a trailing space, a short
commit, and an empty manifest.

## 4. The evidence an install leaves, and why there are two pieces

| file | answers |
|---|---|
| `.sidecar-install-receipt.json` | what was installed, where, by whose grant, on which device and boot, with every file's digest and size, plus one previous version after an upgrade |
| `.sidecar-owner.json` | whether *this* root was made by this tool: same install root, same device, same version, same receipt digest |

The receipt carries a digest over its own canonical body (sorted keys, no
whitespace, `,`/`:` separators). Editing a field without recomputing it is
`BLOCKED_RECEIPT_TAMPERED`, which is a different finding from a receipt that
describes other bytes (`BLOCKED_RECEIPT_MISMATCH`).

The marker is not redundancy. A receipt is a portable JSON file: copied into
someone else's directory it would otherwise claim that *this* tool created that
directory. Two independent pieces of evidence means a forged root has to forge
both, consistently — and even then §5 catches it by *where it points*, not by
what it says.

## 5. The refusal surface

| condition | code |
|---|---|
| no grant, or a grant for another action | `BLOCKED_NO_GRANT` |
| floating version, short commit, empty manifest | `BLOCKED_NOT_PINNED` |
| descriptor path that leaves its root | `BLOCKED_DESCRIPTOR_PATH` |
| relative root | `BLOCKED_ROOT_NOT_ABSOLUTE` |
| `/`, `$HOME` | `BLOCKED_ROOT_NOT_ISOLATED` |
| root is itself a link | `BLOCKED_ROOT_IS_SYMLINK` |
| root is / contains / sits inside a Legacy root | `BLOCKED_LEGACY_ROOT` |
| a directory that merely looks installed | `BLOCKED_NO_RECEIPT` |
| receipt body edited without its digest | `BLOCKED_RECEIPT_TAMPERED` |
| receipt for other bytes, another root, another version/commit, another previous version | `BLOCKED_RECEIPT_MISMATCH` |
| marker missing / disagrees with the receipt | `BLOCKED_OWNER_MARKER_MISSING` / `…_MISMATCH` |
| another device / another boot | `BLOCKED_IDENTITY_MISMATCH` / `BLOCKED_BOOT_MISMATCH` |
| lock present | `BLOCKED_CONCURRENT_WRITER` |
| link on the way to a file | `BLOCKED_SYMLINK_TRAVERSAL` |
| receipt path resolving outside the root | `BLOCKED_OUTSIDE_INSTALL_ROOT` |
| file edited after install / file gone | `BLOCKED_MODIFIED_AFTER_INSTALL` / `BLOCKED_FILE_ABSENT` |
| a file no receipt owns | `BLOCKED_FOREIGN_PRESENT` |
| upgrade that would overwrite the one remembered version | `BLOCKED_UPGRADE_CHAIN` |
| backup not the bytes backed up | `BLOCKED_BACKUP_MODIFIED` |

Two of these are worth spelling out, because the plausible implementation gets
both wrong:

* **Containment is checked in both directions.** A root *inside* the Legacy root
  is one takeover; a root that *contains* the Legacy root is the worse one —
  deleting "our" directory would take Legacy with it. Tests cover each
  direction separately (`test_an_install_that_contains_a_legacy_root_is_refused`,
  and the inside case for both install and restore).
* **A junction is a link.** On Windows a junction is a reparse point that
  `Path.is_symlink()` reports as `False`, so `_is_link()` also tests
  `st_file_attributes & 0x400`. The suite falls back to `mklink /J` when the host
  has no `SeCreateSymbolicLinkPrivilege`, which is exactly the case that proves
  the bit check: `is_symlink()` would have waved it through.

`_inside()` compares on slash boundaries, so `/opt/fleet-next` is not inside
`/opt/fleet`.

## 6. The guards are a list of names — and the probe found out why that matters

Guards are named module functions in one ordered tuple, so a reviewer reads the
whole refusal surface at once and a probe can disable exactly one. The tuple
holds **names**, resolved at call time (`revoke_guards()`, `restore_guards()`).

That was not the first draft. The first draft held function objects, and my own
first probe reported that **thirteen of the guards were not depended on by any
test** — a clean bill of health for the entire pipeline. It was measuring
nothing: a tuple of objects is bound once at import, so rebinding a guard left
the pipeline untouched and every mutation was a no-op. `revoke()` now resolves
its pipeline per call, and `TheGuardPipelineIsResolvedAtCallTime` asserts the
resolution rather than trusting it.

The same probe, run properly, then found a second real gap: with only the three
original restore tests, **12 of restore's 15 guards were executed but not
depended on** — delete one from `RESTORE_GUARDS` and no test changed its
outcome. Restore *writes*, so a restore talked into writing outside its root is
the same defect as a revoke talked into deleting outside it. `RestoreRefusesWhatRevokeRefuses`
now drives every restore guard to a refusal of its **own** code (15 tests plus a
test that the two pipelines cannot drift), and every removal is detected.

Why the exact code matters: several shapes are caught by a neighbouring guard if
the intended one is removed (a lock file becomes `BLOCKED_FOREIGN_PRESENT`; a
link becomes `BLOCKED_OUTSIDE_INSTALL_ROOT`). A test satisfied by "any refusal"
could not tell those guards apart, and the probe would report a false negative.

## 7. The reproducible experiment

A synthetic sidecar — two files with digests computed in the test, never copied
from a claim — is installed into a fresh temporary root, then driven through:

```
install v1  →  failed upgrade (bytes edited underneath)  →  upgrade v1→v2
            →  revoke  →  second revoke
```

with a fake Legacy gateway recording `get_session`, `get_run` and the
deliveries of `send` across the whole lifecycle. The measured result:

* every step that succeeded left `get_session`, `get_run` and the delivery
  ledger exactly where they were;
* the failed upgrade left the receipt at 0.1.0 and the bytes untouched;
* the revoke deleted exactly the receipt's files and removed the root (and the
  backup directory the upgrade created — otherwise a "revoked" upgrade leaves a
  trace, which was one of the defects the first run caught);
* the second revoke deleted **nothing** and returned `BLOCKED_NO_RECEIPT`, with
  `deleted == ()`.

`send` is compared as "one intent, one delivery" by key rather than by string,
because a string comparison passes on a gateway that quietly delivered twice
with identical bytes. The Legacy check has a **positive control**: driven against
a fake whose `get_run` answer really does change, it reports
`LEGACY_CONTRACT_DRIFT` naming `get_run`. A gate that can only say `SAME` is
indistinguishable from no gate.

## 8. The real sidecar: `BLOCKED_UPSTREAM`

The question "can we revoke the sidecar?" has two answers, and they must not be
blurred:

* **a sidecar released this way** — yes, verified offline, §3–§7;
* **the sidecar in the tree today** — **NOT VERIFIED**, because there is no
  record of an install to revoke.

The second answer is derived, not assumed:

```bash
git grep -l -F "serveros/fleet-sidecar-install-receipt.v1"   # no hits, at any ref
```

and `next_tailscale_gate.py` is referenced by exactly 3 files at `e69fc033` —

```
docs/fleet-v1-next-ingress-runbook.md
docs/plans/fleet-v1-next-tailnet-onboarding.md
tests/test_fleet_v1_next_tailscale_gate.py
```

— a runbook, an onboarding plan and a test. #502's branch head adds a fourth
(`wave16_preflight.py`, a reader). This PR adds four more mentions of its own
(the module, its README, its tests and this document), so at this PR's head the
same command counts **7**.

Every one of them is a document, a reader or a test. **None of them installs it
into a root**, and that is the whole point: no receipt was ever written, so
there is nothing for a revoke to verify against. A reader added by this PR does
not change that — it is the counting that changes, not the evidence. So
`upstream_verdict()` returns `BLOCKED / BLOCKED_UPSTREAM` with
`receipt_found=False`, and says in those words that a revoke of the real sidecar
is **not verified** and that this is **not a real-device pass**.

It does not invent a receipt, and it does not run a read-only simulation and
call it a device result. Fabricating the evidence would be the one thing that
makes this whole QA worthless, because the evidence *is* the artifact.

### Handoff to the sidecar's owner (#473 / #466 / #467 lineage)

`REQUIRED_CHANGES`, verbatim from the module:

1. write an install receipt (`serveros/fleet-sidecar-install-receipt.v1`) into
   the `install_root` at install time, before the sidecar serves anything;
2. write the ownership marker beside it, naming the same `install_root`,
   `device_ref` and receipt digest;
3. pin the release as a descriptor (version, 40-hex commit, per-file sha256,
   manifest digest) instead of a directory path;
4. install into its own root, and refuse a root that is, contains or is inside
   the Legacy root;
5. call `revoke()` from this module as the rollback, and keep no `rm -rf` path in
   the runbook.

Until (1) and (2) exist, "the sidecar is revocable" remains a sentence about a
runbook. Note also that #473's "sidecar" — `fleet_mcp/identity_adapter_port.py`
plus `l2_release.py` — is a **different artifact** from #502's two-file tailscale
gate; the L2 file is a release *builder*, not an installed unit. Nothing here
assumes #473 is mergeable: it carries a V2 draft (#407) ancestor and must not be
merged directly on that basis.

## 9. Source / CI / device — three separate readings

### Source — **GREEN, with the road to it recorded**

```
$ python -m pytest tests/test_fleet_next_wave17_sidecar_release.py -q
97 passed, 1 skipped, 17 subtests passed

$ python -m unittest discover -s tests -p 'test_fleet_next_wave17_sidecar_release.py'
Ran 98 tests
OK (skipped=1)
```

The skip is the symlink/junction case when the host forbids both; on the Linux
CI runner it runs.

**RED → GREEN.** The first full run of the suite against the first draft was
**5 failures**, and each one was a defect rather than a wording problem:

| red | cause | fix |
|---|---|---|
| `test_a_receipt_path_that_leaves_the_root`, `…_absolute` → `BLOCKED_OWNER_MARKER_MISMATCH` | the forgery rewrote the receipt but not the marker, so the marker guard fired first | `forge()` rewrites both and recomputes both digests — a *complete* forgery, so the test measures the intended guard |
| `test_a_linked_directory_on_the_way_is_refused` → `BLOCKED_OUTSIDE_INSTALL_ROOT` | containment ran before symlink, so the precise diagnosis was never reached | symlink moved ahead of containment, in both pipelines |
| `test_a_revoked_upgrade_still_leaves_no_trace` → root survived | the backup directory was never pruned | `_prune_empty_dirs()`, used by revoke and restore |
| `test_a_floating_version_is_refused` (subTest `01.0.0`) | `01.0.0` is **not** a floating version; #473's regex accepts a leading zero | the case was replaced with `"1.0.0 "`, and the regex left alone — it is #473's contract, not mine to tighten |

Three more defects were found later, by the probe and by a console run, and each
has its own regression test:

| finding | why it mattered | fix |
|---|---|---|
| the guard tuple held function objects | 13 guards reported "not depended on"; the probe was measuring nothing | names + call-time resolution + `TheGuardPipelineIsResolvedAtCallTime` |
| 12 of restore's 15 guards were executed but not depended on | restore writes; an unobservable guard can be deleted | 15 restore refusals + the no-drift test |
| the CLI emitted non-UTF-8 JSON on a GBK console | the payload was unparseable by its only consumer | `sys.stdout.reconfigure(encoding="utf-8")`, plus a test over real bytes whose positive control fails with `0x97` |

The probe also wrote into the repository once (disabling the root guard let
`relative-root/` be created under the working directory). The probe now runs with
its cwd inside a temporary directory, and the test that asserts a relative root
creates nothing chdirs into a temporary directory first — so the measurement
cannot leave a mark and cannot be confused by one.

### Mutation probe — every protection is load-bearing

Baseline first: `testsRun=98 flipped=0`.

Two mutations per protection, because they fail differently. **Body**: the guard
is replaced by a function that always refuses — this proves the *check* is what
the tests depend on. **Removal**: the guard's name is deleted from the declared
pipeline — this proves its *presence* is depended on. The first run of this
probe had thirteen guards at zero on both axes and the module's comment claimed
the opposite; §6 is what that cost.

Body mutations (`23` protections, `load-bearing=23`, `weak=[]`):

| protection | tests flipped |
|---|---:|
| `_guard_grant` | 48 |
| `_guard_descriptor` | 45 |
| `_guard_root` | 43 |
| `_guard_receipt_present` | 40 |
| `_guard_receipt_intact` | 37 |
| `_guard_owner_marker` | 31 |
| `_guard_identity` | 28 |
| `_guard_writer` | 23 |
| `_guard_receipt_match` | 22 |
| `_guard_symlink` | 21 |
| `_inside` (containment) | 20 |
| `_guard_containment` | 19 |
| `check_descriptor` | 18 |
| `_guard_files` | 16 |
| `_guard_install_root_match` | 16 |
| `_guard_previous_match` | 15 |
| `_guard_foreign` | 11 |
| `_root_refusal` | 10 |
| `_guard_backup` | 6 |
| `_rel_path_problem` | 5 |
| `_is_link` | 3 |
| `_prune_empty_dirs` | 3 |
| `_file_problems` | 1 |

Pipeline removals (`28` slots: 13 revoke + 15 restore, `detected=28`,
`undetected=0`).

**Read the zeros, not the counts.** A guard replaced by "always refuse" breaks
every test that drives a lifecycle, so the numbers are cascades: 48 for
`_guard_grant` does not mean 48 tests are about grants. Excluding the lifecycle
test and re-running the same 23 mutations gives 93 tests and still
`load-bearing=23` — but the counts barely move (47, 44, 42 …), because a second
lifecycle test cascades the same way. That is a property of the design, not a
gap: one end-to-end path is supposed to exercise the whole surface.

So the probe answers a binary question per protection — *does anything notice?* —
and the per-code evidence is separate and exact: all **26** refusal codes are
referenced by a test that asserts that code, which is what stops a test from
being satisfied by a neighbouring guard's refusal. The small counts are the ones
worth reading (`_file_problems` 1, `_is_link` 3, `_prune_empty_dirs` 3): each is
the single thing distinguishing a specific refusal.

### CI — read, not inferred

<!-- CI_SECTION -->

### Device — **NOT_RUN**

No real install, no service manager, no port, no socket, no OAuth round trip, no
Tailscale, no Feishu, no real deletion. Out of scope for this round and
forbidden by the task; only Python `tempfile` directories were deleted.

## 10. What a real revoke still needs

`owner_inputs()` names these; none can be measured offline and none may be
invented:

1. **grant** — an operator grant whose action is install / revoke / restore,
   with an id, an operator and a `device_ref`. This module never mints one.
2. **device_ref** — the reviewed identity of the host, recorded at install,
   re-checked at revoke.
3. **boot_id** — the boot the install happened on; a revoke from another boot is
   refused because it is not the machine that installed.
4. **install_root** — a controlled directory that is not, does not contain and
   does not sit inside a Legacy root.
5. **receipt + marker** — both, from the install. Without them a revoke is
   refused rather than guessed.
6. **legacy_roots** — supplied by the caller, not discovered.
7. **real co-install / real revoke on a device** — separate user permission.
   Nothing here performs one, and none of its output may be reported as a
   real-device pass.

## 11. Links

* #502 — this QA's first round: the Sidecar is reproducible and independent, and
  was **not revocable**; that conclusion is unchanged by this PR.
* #425 — Fleet Next release-readiness thread this feeds.
* #478 — dual-track Owner rules (two tracks, two placements, two answers).
* #473 / #466 / #467 — the sidecar's owning lineage; **not mergeable directly**
  (V2 draft #407 ancestry). See §8 for the changes it must make.
* Wave 16 — `docs/integration/FLEET-NEXT-WAVE16-RELEASE-PREFLIGHT-20261028.md`.
