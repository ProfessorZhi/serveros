# QA-TRIAGE-2026-10-20-ac — #347 / #349 / #345 independent audit; RC-v6 PENDING

**Status: #347 `PASS` at LOCAL for the named Q1 aliases; #349 `PARTIAL PASS` at
LOCAL (Windows slice passes, 8.3/drive-relative ambiguity remains); #345
`PASS` at LOCAL with one P3 human-output observation. RC-v6 not published
after four polls → PENDING. No QA/V1 signature; V1 remains NOT_RELEASE.**

Lane: independent Q1 QA (#244/#250, register #256). Three separate clean
detached checkouts at the exact PR heads; no author/integration worktree,
product source, production Fleet/Daemon/Strata/Connector, OAuth or credential
was edited. Windows REAL execution was not available from Tokyo; a Windows
Agent is separately retesting #349.

---

## 1. Exact refs

| PR | Head (local = remote = PR) | Base | QA checkout | clean |
|---|---|---|---|---|
| #347 guard alias P2 | `a0053c9836ba9fe42213258830c1a625baf2886d` | #342 `eff4c38` | `q1-347-qa-20261020` | yes |
| #349 Windows slice | `558c32418346a6f9dd18cfd3659c513f58ea1479` | #347 `a0053c9` | `q1-pr349-qa-20261020` | yes |
| #345 CLI status | `ef8a360a71fd60d3f08a7a66601548537ee4e810` | RC-v4 `40c4b1c` | `q1-pr345-qa-20261020` | yes |
| RC-v6 | no `rc/i1-v6*` branch/PR after 4 × 90 s polls | — | not created | **PENDING** |

Runs were sequential, `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% /
TasksMax=64 / nice 10`, hard timeouts.

---

## 2. #347 — `PASS` at LOCAL for the Snapshot AA aliases

Diff: 3 files (guard, boundary tests, README), test-only.

- Boundary suite: **49 passed** (author: 49).
- Independent alias probe: **69/69 PASS** — `\\?\UNC\…` and `//?/UNC/…`,
  `\\.\D:\…`, POSIX `///`/`////` (real hook), `//`, symlink alias via
  realpath, and ambiguous device namespaces (`PhysicalDrive0`, `Volume{…}`,
  `GLOBALROOT`) are refused; sibling/traversal/different-drive remain outside.
- This closes the 12 Snapshot AA findings in #342.

Residual (carried into #349, see below): 8.3 short names and unresolved
drive-relative paths are not marked ambiguous. #347's own scope did not claim
them, but the guard header does not list them either.

---

## 3. #349 — `PARTIAL PASS` at LOCAL

Diff: 4 files (guard case semantics, grant-suite import order, boundary tests,
README), test-only.

### Passes

- Boundary suite: **51 passed**.
- X2 three files (boundary + grant + legacy) under the private-path guard:
  **83 passed, 0 skipped**.
- Linux Grant 13: all real tests run and pass (part of the 83).
- Synthetic win32 import (`sys.platform="win32"`, meta-path blocker on
  `workengine`/`fcntl`): the grant suite imports, exposes
  `WINDOWS_PRODUCER_BOUNDARY=True`, 13 test functions and the
  `PRODUCER_BOUNDARY_POSIX_ONLY` reason; no POSIX import is attempted and no
  collection error occurs. This is the test that pins the Windows pre-import
  ordering; it passes.
- Host-aware case: `windows=False` keeps POSIX case strict
  (`/OWNER/...` ≠ `/owner/...`); `windows=True` folds drive case
  (`D:\OWNER\...` = `D:\owner\...`).
- Fixtures: canonical LF / pure CRLF pins match; same-length changed field,
  added/removed field, BOM (UTF-8/16/32), mixed EOL, lone CR, invalid UTF-8 and
  invalid JSON all fail the pin or raise `FixtureBytesError`.

### P2 residual — not a sandbox

Independent probe: **30 checks, 2 FAIL**:

1. **8.3 short-name alias not detected.** `C:\PROGRA~1\private\evil` against
   root `C:\Program Files\private` gives `_inside=False`, `_forms(...)[1]`
   ambiguous `False` (`must_refuse=False`). On an NTFS volume with 8.3 names
   enabled this is the same file; the guard neither resolves nor conservatively
   refuses it. A test-guard bypass only; no product authority.
2. **Drive-relative unresolved is reported safe.** `C:..\private\evil` against
   `C:\work\private` with `windows=True` yields no form and `ambiguous=False`
   on a POSIX host. On a real Windows host `ntpath.abspath` resolves the
   per-drive CWD and may catch it, but this synthetic mode cannot; the guard
   should refuse when it cannot resolve, or state the limit. Windows real
   behaviour is **NOT_RUN from Tokyo** and must come from the Windows Agent.

Both are consistent with the guard's "TEST guard, not a filesystem sandbox"
header, but the README does not name 8.3/drive-relative. Either add a
conservative refusal/ambiguity flag and tests, or document the limit. No
production trust change.

### Windows/legacy classification

- Windows execution: **NOT_RUN from Tokyo**; do not read the synthetic win32
  import as a Windows pass. FJPCOS is separately retesting this exact SHA.
- Legacy 19: on Windows = **NOT_APPLICABLE / skipped** with
  `PRODUCER_BOUNDARY_POSIX_ONLY`; on Linux all 19 run (inside the 83). No
  `pytest.skip`/`xfail` in the suite.

---

## 4. #345 — `PASS` at LOCAL with one P3 observation

Diff: `runtime/cli.py` + one new test file; read-only status boundary. No
authentication, authorization, uid, socket fallback or permission change.

- New test file: **10 passed**.
- Independent probe: **20/21 PASS**:
  - without `os.getuid` and without `XDG_RUNTIME_DIR`, `daemon_socket_path`
    raises `PlatformUnsupported`; message contains no `/run/user/` and names
    XDG/getuid/POSIX-only; no uid or fallback path is invented;
  - `status --json` returns **rc 2**, `ok=false`, `runtime=NOT_RUNNING`,
    `platform=UNSUPPORTED_PLATFORM`, ASCII-only JSON, no traceback;
  - human `status` returns **rc 2** and no traceback;
  - `XDG_RUNTIME_DIR` still wins on Linux; `getuid()` fallback still yields
    `/run/user/1006/serveros/serveros.sock`;
  - `PYTHONIOENCODING=gbk` help exits 0, output decodes as UTF-8 and retains
    `§`; `emit` JSON is ASCII-safe and round-trips non-ASCII.
- **P3:** human-mode stderr does not contain the literal token
  `UNSUPPORTED_PLATFORM` (the JSON payload does). Exit code and refusal are
  correct; this is a wording/observability nit, not an authority issue.
- Windows real execution **NOT_RUN**; this is the Python CLI's read-only
  boundary, not the native Windows V1 Core.

---

## 5. Judgment

| Item | Verdict | Level |
|---|---|---|
| #347 alias fixes | **PASS** | LOCAL |
| #349 Windows slice (case + grant import order) | **PASS** | LOCAL/synthetic |
| #349 8.3 / drive-relative ambiguity | **PARTIAL (P2)** | LOCAL/synthetic; Windows REAL NOT_RUN |
| #345 CLI read-only boundary | **PASS** (1 P3 human-token note) | LOCAL |
| RC-v6 | **PENDING** (4 polls, no ref) | — |

No production authority or trust-boundary change is claimed by any of the
three. Positive trusted Producer/OS Grant, T10 finalize/continuation wiring,
live CC OAuth, Windows real SCM/REAL_DEVICE, CLIENT_E2E and full T1–T12 remain
open. **QA_PARTIAL / NOT_RELEASE; no QA/V1 signature, no merge.**

## 6. Receipts

| Run | Result | Log |
|---|---|---|
| #347 boundary | 49 passed | `r12-q1-347-qa-20261020-boundary.log` |
| #347 alias probe | 69/69 | `r12-347-alias.log` |
| #349 boundary | 51 passed | `r12-q1-pr349-qa-20261020-boundary.log` |
| #349 X2 three files | 83 passed | `r12-349-x2-three.log` |
| #349 guard probe | 28/30 (2 P2) | `r12-q1-pr349-qa-20261020-guard.log` |
| #345 tests | 10 passed | `r12-345-cli.log` |
| #345 CLI probe | 20/21 (1 P3) | `r12-345-probe.log` |
| RC-v6 polls | 4 polls, no ref | `r12-rcv6-poll.log` |
