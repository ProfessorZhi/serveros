# N1 #273 correction retest — independent N3 review

> **Status: INDEPENDENT_SOURCE_LOCAL_RETEST · NOT_GLOBAL_QA · REAL_DEVICE NOT_RUN.**
> This is a retest by the N3 network/access source owner as a reviewer of the
> N1 owner's correction. It is not an author claim, not a global QA signature and
> not a release gate closure. No N1 file, protected `main`, QA #256, G1 or other
> project was edited.

- **Date:** 2026-10-18 (UTC)
- **N1 correction under retest:** Draft PR #273, head
  `8186083439c077268b6c88a80ea6b6204e2d576e`
- **N1 parent:** #270 `9ef9f14f381d01e6dcd8bda67c6e017dd975c651`
  (stacked on #242 `bec4835cf3c54066850808278d86de81ddedf561`)
- **Prior N3 evidence:** #259 `e3451167fee1c311ed20c3eb99026bc30ccd481a`
  (`F1/F2 P2`, `F3–F6 P3`)
- **N3 evidence parent:** `e3451167fee1c311ed20c3eb99026bc30ccd481a`
  (this document is committed next; PR #259 head is the exact evidence commit)
- **Host:** Tokyo shared Linux, `vince`
- **Method:** exact-SHA `git archive` into a temporary sandbox, local `g++`
  build, adversarial negative tests, `strace` evidence. No live device, network,
  service, credential or production filesystem was used.

## Verdict

**`SOURCE_LOCAL_PASS_WITH_ACCEPTED_RESIDUAL`**

The N1 owner corrected the two P2 findings and the P3 findings from the prior
N3 review. The original F1/F2 repros are now red on #270 and green/refused on
#273. No RED security failure remains for F1–F7. One residual is real and is
classified precisely below: the **same-uid `WriterLease` path race**, which the
helper cannot promise away and which the PR correctly labels as not a privilege
boundary. Root/non-root 0500 logic is correctly qualified in source; the
non-root branch was executed, the root branch is **NOT_RUN** because this host
has no passwordless root.

## F1–F7 disposition

| ID | Prior severity | Retest result | Exact evidence |
| --- | --- | --- | --- |
| **F1** intermediate symlink | P2 | **PASS** — old #270 accepted and created through the link; new #273 refuses by name. | Old: `SERVEROS_TEST_TMPDIR=$SB/f1/outerlink/sub` → **RC=0**, `62 checks, 0 failures`; `strace`: `mkdir(".../f1/outerlink/sub/srvos-lease-y4Xm8R", 0700) = 0`. New: same env → **RC=1**, `59 checks, 1 failures`, `no scratch root: "outerlink" is a symlink...`; `strace`: `openat(5, "outerlink", O_RDONLY\|O_NOFOLLOW\|O_CLOEXEC\|O_DIRECTORY) = -1 ENOTDIR`. No leaf created; `f1/real-parent/sub` empty afterwards. |
| **F1 source** | — | — | Old #270 `make_scratch_in` (`test_sup_lease.cpp:73`) used `lstat` at line 80 and then `mkdtemp` at line 96. New #273 `open_absolute_directory` at line 117 opens every component with `openat(...O_NOFOLLOW)` at line 128 and inspects with `fstatat(fd, part, ...AT_SYMLINK_NOFOLLOW)` at line 136 **before** closing the parent. |
| **F2** TOCTOU / pinned cleanup | P2 | **PASS** — create and delete are on the pinned descriptor; replacing the parent path after open cannot redirect either. | New #273 internal F2 case is part of `98 checks, 0 failures`. Independent N3 harness `f2_pinned_fd` → `HARNESS OK failures=0`, **RC=0**. `strace` sequence: `openat(5, "victim", O_RDONLY\|O_NOFOLLOW\|O_CLOEXEC\|O_DIRECTORY) = 6` (pin), `renameat(3, "victim", 3, "victim-moved") = 0`, `symlinkat("redirected", 3, "victim") = 0`, `unlinkat(6, "srvos-lease-cc73025ce6c41a94", AT_REMOVEDIR) = 0`, then `newfstatat(3, "victim-moved/srvos-lease-...", AT_SYMLINK_NOFOLLOW) = -1 ENOENT`, `newfstatat(3, "victim", ...) = 0` (`S_IFLNK`), `newfstatat(3, "redirected", ...) = -1 ENOENT`. The original entry is removed; the symlink is untouched; the target was never created. |
| **F2 source** | — | — | Leaf creation uses `mkdirat(scratch.base_fd, ...)` at line 181; deletion uses `unlinkat(scratch.base_fd, scratch.leaf_name, AT_REMOVEDIR)` at line 289. Neither re-resolves the base path. |
| **F3** relative base | P3 | **PASS** — old #270 accepted a relative base; new #273 refuses it by name. | Old: cwd `$SB/relcwd`, `SERVEROS_TEST_TMPDIR=rel-base` → **RC=0**, `62 checks`; `strace`: `mkdir("rel-base/srvos-lease-74KxtG", 0700) = 0`. New: same → **RC=1**, `59 checks, 1 failures`, `no scratch root: the scratch base is not an absolute path: rel-base`. `rel-base` empty afterwards. |
| **F4** root vs 0500 | P3 | **PASS (non-root executed; root NOT_RUN, source-qualified)** — the 0500 expectation is inside `if (::geteuid() != 0)`, and a capability-independent regular-file refusal covers the property under any uid. | Non-root: external `SERVEROS_TEST_TMPDIR=$SB/locked-base` (0500) → **RC=1**, `59 checks, 1 failures`, `a private directory could not be created (Permission denied)`; `strace`: `mkdirat(6, "srvos-lease-552720eae7788bf0", 0700) = -1 EACCES`; no `root: the 0500 case is skipped` line. Regular-file base `$SB/notadir` → **RC=1**, `"notadir" is not a directory` (capability-independent). Root branch: **NOT_RUN** (no passwordless sudo), source-qualified only. |
| **F5** stale README | P3 | **PASS** — the "expected to fail on any host that is not Tokyo" premise is replaced with the fd-anchored behaviour. | `native/node-core/supervisor/README.md:240` now describes component-wise `openat(O_NOFOLLOW)`, `mkdirat`/`unlinkat`, absolute-only override, and asserted `0700`/no-leftovers. Remaining `/data/home/vince` lines 217–218 are pre-existing **build command examples**, not test IO; not part of F5, noted as non-blocking doc hygiene. |
| **F6** 0700 / no leftovers | P3 | **PASS** — the test now asserts `0700` via `fstat` and measures entry counts before/after every negative case. | `98 checks, 0 failures` for both owner sandbox and default base. `make_leaf` checks `(st_mode & 0777) == 0700` and refuses otherwise. `entry_count` at line 301 uses a **fresh** `openat(dir_fd, ".", ...)` at line 306, not `dup`. `strace`: 11 `mkdirat(..., 0700)` calls, **0** non-0700 `srvos-lease` creates; all sandbox dirs empty after the run; `find $SB -name 'srvos-lease-*'` empty. |
| **F7** holder literal | P4 | **PASS** — `device:tokyo-shared-dev-01` is replaced by `device:fixture-under-test`. | `test_sup_lease.cpp:543` and `:553`. |
| **Helper bug 1** `dup` offset | P3 | **PASS** — `entry_count` opens a fresh file description; the final count is 2 (`.`/`..`) and no leftovers remain. | Source line 306; 98 checks pass; leftovers empty. |
| **Helper bug 2** closed-fd probe | P3 | **PASS** — `fstatat` is called before `close(fd)`; the F1 refusal names `"symlink"` even though `openat` returned `ENOTDIR`. | Source lines 128–140; F1 output and `strace` above. |
| **98 lease checks** | — | **PASS** | `test_sup_lease: 98 checks, 0 failures` (owner sandbox and default base). |
| **54 recovery checks** | — | **PASS** | `test_sup_recovery: 54 checks, 0 failures`; file unchanged from #270. |

## Build and execution evidence

Exact SHAs were extracted with `git archive` into a temporary sandbox under
`/tmp/fleet-v1-native-portability-retest-SdtsRa`; the repository and N1 files
were read-only.

```text
g++ 13.3.0
-std=c++20 -O2 -Wall -Wextra -Wpedantic
```

All four builds (old #270 lease, new #273 lease, new #273 recovery, independent
F2 harness) exited 0 with **zero warnings** (empty build logs).

Execution wrapper:

```text
systemd-run --user --scope -q \
  -p MemoryMax=1073741824 -p MemorySwapMax=0 -p CPUQuota=25% \
  -- timeout 600 <case>
```

behind non-blocking `flock -n` on
`/tmp/fleet-v1-native-portability-retest-20261018.lock`.

| Case | Exact SHA | Exit / checks |
| --- | --- | --- |
| F1 old #270, intermediate symlink | `9ef9f14f` | **RC=0**, `62 checks, 0 failures`; `mkdir(".../outerlink/sub/srvos-lease-y4Xm8R", 0700)=0` |
| F1 new #273, same repro | `81860834` | **RC=1**, `59 checks, 1 failures`; `"outerlink" is a symlink`; `openat(...O_NOFOLLOW)= -1 ENOTDIR` |
| F3 old #270, relative base | `9ef9f14f` | **RC=0**, `62 checks`; `mkdir("rel-base/srvos-lease-74KxtG", 0700)=0` |
| F3 new #273, relative base | `81860834` | **RC=1**, `59 checks, 1 failures`; `not an absolute path` |
| New #273, owner sandbox | `81860834` | **RC=0**, `98 checks, 0 failures` |
| New #273, default platform base | `81860834` | **RC=0**, `98 checks, 0 failures` |
| New #273, missing base | `81860834` | **RC=1**, `59 checks, 1 failures`; `does not exist at "absent-base"` |
| New #273, final-component symlink | `81860834` | **RC=1**, `59 checks, 1 failures`; `"finallink" is a symlink` |
| New #273, regular-file base | `81860834` | **RC=1**, `59 checks, 1 failures`; `"notadir" is not a directory` |
| New #273, unwritable base 0500 (non-root) | `81860834` | **RC=1**, `59 checks, 1 failures`; `Permission denied`; `mkdirat(...)= -1 EACCES` |
| New #273 recovery control | `81860834` | **RC=0**, `54 checks, 0 failures` |
| Independent F2 pinned-fd harness | `81860834` | **RC=0**, `HARNESS OK failures=0` |

The `59 checks, 1 failures` on the external bad-base cases is expected: the
internal scratch-root contract checks pass, and the single main-root refusal is
the failure. The happy path is `98 checks, 0 failures`.

## Residual — same-uid `WriterLease` path race (classified)

The fd-anchored guarantee covers the **scratch root's create and delete**.
It does **not** cover the lease file path that the lease module consumes:

- `test_sup_lease.cpp:541` builds `root + "/a.lock"` and passes that path to
  `WriterLease::acquire`.
- `native/node-core/supervisor/src/lease.cpp:66` is
  `LeaseAttempt WriterLease::acquire(const std::string& path, ...)`, which
  re-resolves a path.

Between the fd-anchored leaf creation and `WriterLease::acquire`, a **same-uid**
process could replace the leaf or the lock path with a symlink. Classification:

- **Not a privilege boundary / not a privilege escalation.** A same-uid process
  can already open, read and write the user's files directly. The leaf is
  `0700` and owned by the test user, but `0700` does not exclude the same UID.
- **Owned by another interface.** `WriterLease` is not changed by #273 and its
  path-taking interface is outside the N1 owner's correction scope. A fully
  fd-based lease acquire would be a separate owner decision.
- **The PR states the limit correctly.** "Do not read this as fully
  symlink-safe against arbitrary same-uid attackers." The helper cannot by
  itself promise a same-uid symlink-safe lease file.
- **Different-uid:** the leaf is `0700`, and the default `/tmp` base is sticky;
  a different UID cannot write inside the leaf. The base traversal is
  `O_NOFOLLOW` at every component. An owner-supplied base that is
  world-writable **and non-sticky** would weaken the lease-path assumption
  (the fd-anchored create/delete still targets the pinned inode, but the lease
  path is only as safe as that base). The README does not assert base
  ownership/stickiness; this is a non-blocking precondition to document, not a
  RED failure of #273's stated scope.
- **Verdict on the residual:** `ACCEPTED_RESIDUAL_SAME_UID_ONLY`, not a
  security fail for the reviewed threat model. It must not be reported as
  arbitrary same-uid safety.

No other security failure remains for F1–F7.

## Host preflight / resource evidence

- 128 CPUs; load `23.51 / 16.08 / 14.79`; transient root CPU bursts were
  present (top entries ~1200% for `elapsed 00:00`), but PSI remained low and the
  run was CPU-bounded.
- PSI `some avg10`: cpu `2.22`, memory `0.00`, io `0.11`.
- Available memory `166,648,201,216 B` (≈155.2 GiB).
- `pswpout` delta `0` pages over 5 s.
- Independent non-blocking lock
  `/tmp/fleet-v1-native-portability-retest-20261018.lock`; other locks were not
  touched.
- Verified cgroup inside the scope: `memory.max=1073741824`,
  `memory.swap.max=0`, `cpu.max=25000 100000` (25%).
- No full suite, no subagents, no production filesystem write, no credentials,
  no network probe, no OS/service change.

## Collision check

Heads refreshed immediately before work:

```text
#273 8186083439c077268b6c88a80ea6b6204e2d576e
#270 9ef9f14f381d01e6dcd8bda67c6e017dd975c651
#265 1aa3d400a18f690ed3273578c723940f787807bd
#259 e3451167fee1c311ed20c3eb99026bc30ccd481a
```

- #273 changes exactly two files: `native/node-core/supervisor/tests/test_sup_lease.cpp`
  and `native/node-core/supervisor/README.md`; it is one commit on top of #270
  (`git merge-base --is-ancestor 9ef9f14f 81860834` = true).
- #265 changes only `native/node-core/windows-core/**`.
- N3 #259 changes only `integrations/fleet-v1-network-health/**` and
  `tests/test_fleet_v1_network_health.py`.
- File-set intersections: **empty**.
- Read-only `git merge-tree --write-tree`: #273 vs #265 → rc 0, **0 conflicts**;
  #273 vs N3 `e345116` → rc 0, **0 conflicts**.

## Boundaries

- **Dell/fjd:** not attempted; remains **BLOCKED_BY_OWNER_PERMISSION**.
- **Windows native / N2 #265:** **NOT_RUN**; out of scope.
- **Root 0500 branch:** **NOT_RUN** (no passwordless root); source-qualified and
  the non-root branch executed. The capability-independent regular-file refusal
  was executed.
- No production service, credential, network, tunnel, Tailscale, RDC, GPU or
  protected `main` action. No N1 file edited. No auto-fix of any finding.
- This is **not** a global QA signature and **not** a V1 release gate closure.
