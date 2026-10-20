# QA-TRIAGE-2026-10-20-ae — #353 host-privilege/alias guard; RC-v7 PENDING

**Status: #353 `PASS` at LOCAL for the stated guard properties; the real-Windows
receipt is accepted as `REAL_WINDOWS_READONLY`, not a Full V1 signoff. RC-v7
was not published after four polls → PENDING. No QA/V1 signature; V1 remains
NOT_RELEASE.**

Lane: independent Q1 QA (#244/#250, register #256). Own detached checkout at
the exact #353 head; no product source, I1/author worktree, production
Fleet/Daemon/Connector, OAuth or credential was edited. No RC-v7 checkout was
created because no ref exists.

---

## 1. Exact refs

| Item | Value |
|---|---|
| #353 Draft PR | `test/x2-win-symlink-privilege-20261020` |
| Head (local = remote = PR) | `21962267ca6c9c7166bb9fccfae3b2947ea21caf` |
| Base | #349 `558c32418346a6f9dd18cfd3659c513f58ea1479` |
| QA checkout | `q1-pr353-qa-20261020`, detached, **clean** |
| Diff | 3 files, +233/−4: guard, boundary tests, README; **test/doc only**; no product runtime/Grant/Effect |
| RC-v7 | no `rc/i1-v7*` branch/PR after 4 × 90 s polls (09:51–09:56Z) → **PENDING** |

All runs sequential, `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
TasksMax=64 / nice 10`, hard timeouts.

---

## 2. Independent results at `21962267`

| Gate | rc | Result | Level |
|---|---|---|---|
| Boundary suite, Linux | 0 | **58 passed, 0 skipped** | LOCAL |
| Boundary suite with pure-CRLF fixtures | 0 | **58 passed** | LOCAL |
| X2 three files under private-path guard | 0 | **90 passed, 0 skipped** | LOCAL |
| Independent guard probe | 0 | **36/36 PASS** | LOCAL/synthetic |

### 2.1 WinError 1314 is the only skip

- `symlink_privilege_reason(OSError(winerror=1314), platform="nt")` →
  `HOST_PRIVILEGE_NO_SYMLINK`; `platform="posix"`, `winerror=5`, and no
  `winerror` → `None` (caller re-raises).
- `make_directory_symlink_or_skip` with a fake 1314 on `nt` → pytest skip;
  fake 5 on `nt` and fake 1314 on `posix` → **OSError re-raised**.
- Only **two** live call sites use the helper; on Linux both live symlink tests
  run and the boundary suite reports 58 passed / **0 skipped**.
- No blanket skip; every other error remains a failure.

### 2.2 Conservative alias refusal

- `C:\PROGRA~1\private\evil` vs `C:\Program Files\private` →
  `_must_refuse=True`.
- `\\server\share\PRIVAT~1\evil` → refused.
- trailing-space (`root \`) and trailing-dot (`root.`) components → refused.
- `D:relative\path` is refused under **two different CWDs**; absolute
  `D:\public\file` and sibling `D:\owner\private\root2\evil` are **not**
  refused; inside `D:\owner\private\root\file` is refused.
- POSIX absolute context is unchanged: `/tmp/public/file` and
  `/tmp/private2/file` are not refused; `/tmp/private/file` and
  `///tmp/private/file` are refused; the live symlink alias is refused.

Residual, explicitly not hidden: this is a **check-time TEST guard, not a
filesystem sandbox**. TOCTOU and real NTFS 8.3/trailing alias mapping are
**NOT_RUN**; resolving them needs `GetLongPathName`/
`GetFinalPathNameByHandle`. The synthetic probe cannot prove real-volume
mapping.

### 2.3 Fixture bytes

LF and pure-CRLF canonical pins match (`81f51a85…`, 613 bytes; CRLF 633 bytes);
a same-length changed field, BOM, mixed EOL and lone CR are all rejected. The
boundary suite also passes with every fixture converted to pure CRLF (58).

### 2.4 Real Windows #353 — `REAL_WINDOWS_READONLY`

The independent FJPCOS receipt for this exact SHA reports:

- boundary: **56 passed + 2 skipped** (`HOST_PRIVILEGE_NO_SYMLINK`, no
  `SeCreateSymbolicLinkPrivilege`);
- grant: **13 skipped / 0 errors** (POSIX-only, NOT APPLICABLE);
- legacy: **19 skipped / 0 errors** (POSIX-only, NOT APPLICABLE).

This is **not rerun from Tokyo** and **not a Full V1/Windows signoff**; it is
read-only device evidence. The real NTFS 8.3/trailing mapping remains
NOT_VERIFIED.

---

## 3. RC-v7

Four read-only polls found no `rc/i1-v7*` branch or PR. RC-v7 is **PENDING**;
no checkout was created, no S1/T10/Core/X2/Fleet Next/REAL_TEMP spot-check was
claimed, and the I1 worktree was not accessed. Those gates remain **NOT_RUN**
for RC-v7 until a real ref exists.

---

## 4. Judgment

| Item | Verdict | Level |
|---|---|---|
| #353 WinError-1314 skip scoping | **PASS** | LOCAL/synthetic; real Windows receipt 2 skips |
| #353 drive-relative / 8.3 / trailing refusal | **PASS** | LOCAL/synthetic; real NTFS mapping NOT_RUN |
| #353 absolute/sibling non-over-block | **PASS** | LOCAL/synthetic |
| #353 fixture bytes | **PASS** | LOCAL |
| #353 real Windows | **REAL_WINDOWS_READONLY**, 56P/2S + 13/19 NOT-APPLICABLE | device-reported, not rerun Tokyo |
| RC-v7 | **PENDING** (4 polls, no ref) | — |

#353 changes no product runtime, Grant or Effect logic. Overall remains
**`QA_PARTIAL` / `NOT_RELEASE`**: trusted producer/OS Grant, T10
finalize/continuation wiring, live CC OAuth, Windows real SCM/REAL_DEVICE,
CLIENT_E2E and full T1–T12 are absent. **No QA_SIGNED, no merge, no
V1_RELEASE.**

## 5. Receipts

| Run | Result | Log |
|---|---|---|
| #353 boundary Linux | 58 passed / 0 skip | `r14-353-boundary.log` |
| #353 boundary CRLF fixtures | 58 passed | `r14-353-boundary-crlf.log` |
| #353 X2 three files | 90 passed | `r14-353-three.log` |
| #353 guard probe | 36/36 | `r14-353-guard-probe.log` |
| RC-v7 polls | 4 polls, no ref | `r14-rcv7-poll.log` |
