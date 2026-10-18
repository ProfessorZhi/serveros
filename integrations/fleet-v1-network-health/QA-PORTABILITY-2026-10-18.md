# N1 #270 scratch-root portability — independent N3 review

> **Status: INDEPENDENT_SOURCE_LOCAL_REVIEW · NOT_GLOBAL_QA · NOT_RELEASE.**
> This is a reviewer note from the N3 network/access source owner, not an
> author claim and not a V1 sign-off. The N1 owner owns the code and its final
> disposition. No N1 file, protected `main`, QA #256, G1, or other project was
> edited.

- **Date:** 2026-10-18 (UTC)
- **Reviewer lane:** N3 #247, Draft PR #259
- **N1 candidate under review:** Draft PR #270, head
  `9ef9f14f381d01e6dcd8bda67c6e017dd975c651`
- **N1 base:** PR #242, head
  `bec4835cf3c54066850808278d86de81ddedf561`
- **N3 evidence parent:** `f81ad9d5df80ac6f6b66e4d732a5194d93b7b548`
  (this document is committed on top of it; the PR #259 head is the exact
  evidence commit)
- **Host:** Tokyo shared Linux, `vince`
- **Method:** read-only source review of the exact SHA plus a local, sandboxed
  negative proof. No Dell/fjd hardware operation, no Windows native run, no
  live network or production service.

## Verdict

**`SOURCE_LOCAL_PASS_WITH_FINDINGS`**

The specific defect reported by the N1 author is fixed and independently
reproduced as fixed:

- the hardcoded `/data/home/vince/artifacts/lease-test-XXXXXX` is gone from
  `native/node-core/supervisor/tests/test_sup_lease.cpp`;
- the default base follows `SERVEROS_TEST_TMPDIR` or
  `std::filesystem::temp_directory_path()`;
- missing, final-component-symlink and (as a non-root user) unwritable bases
  refuse without creating a scratch root;
- two concurrent sessions create distinct `0700` roots and leave no directories.

However, the **"a symlink in that position is refused" claim is narrower than
the code and its comment imply**: only the final path component is checked.
An intermediate symlink component is accepted and redirects the scratch root
(F1). The `lstat`→`mkdtemp` sequence also has a TOCTOU window (F2). Those are
security-relevant for a fixture whose stated purpose is to refuse a redirecting
symlink, so the N1 owner should either harden the implementation or narrow the
documented guarantee before a "symlink-safe" claim is accepted.

No release/QA signature is implied. Cross-platform Dell/fjd and Windows native
remain **BLOCKED_BY_OWNER_PERMISSION / NOT_RUN**.

## Findings

| ID | Severity | Finding | Evidence / status | Owner disposition |
| --- | --- | --- | --- | --- |
| **F1** | **P2** | **Intermediate symlink components are accepted.** `lstat(base)` follows every component except the last; only a final-component symlink is refused. An owner-supplied override such as `.../outerlink/sub`, where `outerlink` is a symlink and `sub` is a real directory, is accepted and the leaf is created through the link. | Reproduced: `SERVEROS_TEST_TMPDIR=$SB/outerlink/sub` → `RC=0`, 62 checks, `mkdir("$SB/outerlink/sub/srvos-lease-fvMFky", 0700)=0`. The internal test only covers a final-component symlink. | N1 owner: use fd-relative creation (`open(base, O_DIRECTORY|O_NOFOLLOW)`, `mkdirat`/`mkdtemp` on the fd), reject any symlink component, or explicitly narrow the contract to "final component only". |
| **F2** | **P2** | **TOCTOU between `lstat` and `mkdtemp`.** The base can be replaced by a symlink after the check and before the create; the check is advisory, not atomic with the write. | Static code review; not raced in this run. The sandbox base is owner-chosen, so the practical threat is low, but it contradicts a security claim made for the helper. | N1 owner: same fd-relative mitigation as F1, or document the residual race. |
| **F3** | **P3** | **Relative `SERVEROS_TEST_TMPDIR` is accepted.** The base need not be absolute, so the scratch root is created relative to the test's current directory. | Reproduced: cwd `$SB`, `SERVEROS_TEST_TMPDIR=rel-base` → `RC=0`, `mkdir("rel-base/srvos-lease-J3uRpe", 0700)=0`. | N1 owner: require an absolute path, or document that a relative override is relative to cwd. |
| **F4** | **P3** | **The unwritable-base negative test assumes a non-root effective UID.** `mkdtemp` in a `0500` directory succeeds for root/CAP_DAC_OVERRIDE, so the internal `CHECK(!make_scratch_in(locked, ...))` would fail on a root CI runner. | Static review; Tokyo runs as `vince`, and the case passed here. No root run was attempted (no passwordless sudo). | N1 owner: skip or invert the expectation when `geteuid()==0`, or use a capability-independent refusal check. |
| **F5** | **P3** | **Stale documentation.** `native/node-core/supervisor/README.md:240` still says `test_sup_lease` "needs a writable `/data/home/vince/artifacts` and is therefore expected to fail on any host that is not Tokyo". That directly contradicts #270. | `git grep` at `9ef9f14f` finds the line; #270 changes only the test source. | N1 owner: update the README line. Reviewer does not edit N1 files. |
| **F6** | **P3** | **The test does not assert the `0700` mode, and "nothing created anywhere" is not globally asserted for the negative cases.** | External `strace` proves every `srvos-lease-*` `mkdir` is `0700`; sandbox emptiness proves no leftovers. The committed test itself only checks distinctness and empty refused roots. | N1 owner: add a `stat` mode check and a before/after "no new entries" assertion to the negative cases. |
| **F7** | **P4** | **A host-specific holder identity literal remains.** The test still writes `device:tokyo-shared-dev-01` as a lease holder identity. | Source review. It is a string written into the lock file, not a path and not an execution dependency, so it does not reproduce the portability bug. | Optional hygiene; no action required for portability. |

Non-finding, checked and dismissed: the two `/data/home/vince/.secrets/vault/key.pem`
strings in `core-lite/tests/test_collector_readings.cpp` (lines 487 and 517) are
**forged-payload canaries** inserted into a JSON document to prove the
projection drops them. They are never opened as paths. The N1 author correctly
reported them instead of editing another lane's file. They are not part of
#270 and are not a security bug.

## Positive evidence (what actually passed)

All runs used the exact `9ef9f14f` archive, a `g++ 13.3.0` build with
`-std=c++20 -O2 -Wall -Wextra -Wpedantic` (zero warnings), under:

```text
systemd-run --user --scope -q \
  -p MemoryMax=1073741824 -p MemorySwapMax=0 -p CPUQuota=25% \
  -- timeout 600 <case>
```

behind non-blocking `flock -n` on
`/tmp/fleet-v1-native-portability-review-20261018.lock`.

| Case (exact SHA) | Result | Evidence |
| --- | --- | --- |
| #242 old, **simulated** missing hardcoded parent | **RC=1**, `1 checks, 1 failures` | `FAIL ... test_sup_lease.cpp:38: !root.empty()`; `strace` contains **0** `mkdir` calls for `/data/home/vince/artifacts/lease-test-`. The shim returns `ENOENT` before the real `mkdtemp`, so nothing is created. |
| #270 new, default platform base (`TMPDIR` only) | **RC=0**, `62 checks, 0 failures` | `mkdir(".../default-tmp/srvos-lease-1DrK5t", 0700)=0`; directory removed. |
| #270 new, owner-supplied `SERVEROS_TEST_TMPDIR` | **RC=0**, `62 checks, 0 failures` | `mkdir(".../sandbox/srvos-lease-jt2rhn", 0700)=0`; sandbox empty afterwards. |
| #270 new, missing base | **RC=1** | `no scratch root: the scratch base does not exist: .../absent-base`; no directory created. |
| #270 new, final-component symlink base | **RC=1** | `no scratch root: the scratch base is a symlink, which would redirect what this test writes: .../link-base`; no directory created. |
| #270 new, unwritable base (`0500`, as `vince`) | **RC=1** | `no scratch root: a private directory could not be created under .../locked-base`; `strace` shows `mkdir(.../srvos-lease-…, 0700) = -1 EACCES`; no directory created. |
| #270 new, two concurrent sessions, same sandbox | **RC=0 and RC=0**, each `62 checks, 0 failures` | Distinct main roots `srvos-lease-MVbmNP` and `srvos-lease-1p6pP8`, both `0700`; all sandbox dirs empty afterwards. |
| Lease behavior | — | `flock(3, LOCK_EX|LOCK_NB)=0`; second writer `flock(4, LOCK_EX|LOCK_NB)=-1 EAGAIN`; matches the lease contract. |

Additional proof of the default path: with `SERVEROS_TEST_TMPDIR` unset and
`TMPDIR=$SB/default-tmp`, the internal equality check
`plain == temp_directory_path()` passed (part of the 62 checks), and the main
root was created at `.../default-tmp/srvos-lease-1DrK5t` with mode `0700`.

Leftover check: every sandbox (`sandbox`, `plain-tmp`, `conc-sandbox`,
`conc-tmp`, `default-tmp`) was empty after the runs; `find $SB -name
'srvos-lease-*'` returned nothing. The sandbox itself was removed after
capture.

## Negative-proof caveat

The #242 binary was **not** run normally on Tokyo. Its hardcoded parent
`/data/home/vince/artifacts` exists on this host, so a normal run would write
inside the production artifacts root; the instruction was sandbox-only, so
that path was not touched. Instead, a small `LD_PRELOAD` shim intercepts
`mkdtemp` for the exact old prefix and returns `ENOENT` before calling the real
libc function. That is a host-equivalent simulation of "the parent does not
exist", not Dell evidence. `strace` confirms no `mkdir` for that prefix
occurred. The real Dell/fjd reproduction remains **BLOCKED_BY_OWNER_PERMISSION
/ NOT_RUN**, and no Dell hardware was accessed.

## Host preflight (GREEN)

- 128 CPUs; load `13.20 / 13.34 / 13.68` (≈10%).
- PSI `some avg10`: cpu `0.09`, memory `0.00`, io `0.00`.
- Available memory `172,364,754,944 B` (≈160.5 GiB).
- `pswpout` delta `0` pages over 5 s.
- No `vince` pytest/cmake/g++/make process active.
- Independent non-blocking test lock
  `/tmp/fleet-v1-native-portability-review-20261018.lock`; the pre-existing
  `/tmp/serveros-v1-wave1-a-test.lock` and other locks were not touched.
- Verified cgroup inside the scope: `memory.max=1073741824`,
  `memory.swap.max=0`, `cpu.max=25000 100000` (25%).
- No full suite, no production filesystem write, no credentials, no raw
  process args/env from other sessions, no network probe, no service action.
  Other users' workload was considered only as aggregate host load.

## Collision / dependency check

- #270 changes exactly **one** file:
  `native/node-core/supervisor/tests/test_sup_lease.cpp`.
  `git log bec4835c..9ef9f14f` is one commit; `git merge-base --is-ancestor
  bec4835c 9ef9f14f` is true.
- Core latest #265 (`1aa3d400`) changes only `native/node-core/windows-core/**`.
  File-set intersection with #270: **empty**.
- N3 Draft #259 changes only `integrations/fleet-v1-network-health/**` and
  `tests/test_fleet_v1_network_health.py`. File-set intersection with #270:
  **empty**.
- Read-only `git merge-tree --write-tree`:
  - `9ef9f14f` vs `1aa3d400` → rc 0, **0 conflicts**;
  - `f81ad9d` (N3) vs `9ef9f14f` → rc 0, **0 conflicts**.
- No N3 source dependency on the N1 hardcoded path, `SERVEROS_TEST_TMPDIR`, or
  the lease test fixture. N3 consumes N1's service-readiness/refusal vocabulary
  as a design input only; the two packages share no code or mutable state.

## N3 → native readiness annotation

For the N3 dependency map: `native` readiness for N3 remains
`SOURCE_LOCAL / NOT_RUN` for real Linux portability, Dell/fjd and Windows. #270
is a test-fixture correction and does not change `lease.hpp`, `lease.cpp`, the
service-readiness contract or any N3 runtime input. N3 remains
`SOURCE_ONLY/OFFLINE`; it has no `native` LIVE dependency, and this review does
not promote it.

## Boundaries

- No edit to `native/node-core/**`, N1 PR #270, QA #256, G1, protected `main`,
  or any other project.
- No Dell/fjd write, build or test attempt; no Windows native run.
- No production service, credential, network, tunnel, Tailscale, RDC or
  provider account action.
- This is an independent source-local review, not a global QA signature and not
  a V1 release gate closure.
