# QA-TRIAGE-2026-10-20-aa — #342 / #343 independent audit; RC-v5 PENDING

**Status: #342 `PARTIAL PASS` at LOCAL (fixture pin PASS; path guard has P2
aliases, including one regression); #343 `PASS` at LOCAL /
`PUBLIC_REIMPLEMENTATION_LOCAL`. RC-v5 is not published (PENDING after four
polls). No QA_SIGNED, no V1 signature; V1 remains NOT_RELEASE.**

Lane: independent Q1 QA (#244/#250, register #256). Independent Session; no
product source edited. Both candidate checkouts are my own detached worktrees
at the exact PR heads; the author/I1 worktrees were not modified.

---

## 1. Exact refs and local/remote consistency

| Candidate | PR head (local = remote = PR) | Base | QA checkout | Status |
|---|---|---|---|---|
| #342 X2 Windows fixture/guard | `eff4c38081818c2b569fd4a995b7f168067fe885` | `40c4b1c` (RC-v4) | `q1-pr342-qa-20261020` | clean |
| #343 public T10 fixtures | `032d4698d0d308efc53788bc4f1a3e686ca7ccfe` | `40c4b1c` (RC-v4) | `q1-pr343-qa-20261020` | clean |
| RC-v5 | **no `rc/i1-v5*` branch or PR after 4 polls** | — | not created | **PENDING** |

`git rev-parse HEAD` in each checkout equals the PR head above; `git status
--porcelain` is empty. The RC-v4 base `40c4b1c` and I1 worktree were not
touched.

---

## 2. #342 — fixture canonicalization (PASS)

Independent probe (`qa_probe_r10_pr342.py`) drives `canonical_json_bytes`
directly with adversarial bytes. Result: **69 checks, 12 FAIL**; **all 12
failures are path-guard aliases**, the fixture/canonicalization half is green.

- Canonical LF fixture: `finalize_t10_verified.v1.json` is **613 bytes**,
  SHA-256 `81f51a8580df37fff301e35a2a2c53e44c5fc9ed48ba0456183759629dfe0c95`
  (matches the pin).
- Pure CRLF transcription: **633 bytes**, SHA-256
  `7a8e0cd56a979fa3…`; canonicalizes to the same 613 canonical bytes and the
  same pin.
- Pinned SHA is over canonical bytes, and a semantic change is not laundered:
  - changed value with the same length (`PASS` → `FAIL`), LF and CRLF: **pin
    differs**;
  - added field, removed field, added whitespace, duplicate key: **pin
    differs** in both LF and CRLF forms.
- Rejected with `FixtureBytesError`: UTF-8 BOM, UTF-16LE/BE BOM, UTF-32LE BOM
  (`BOM`); mixed LF/CRLF (`MIXED_EOL`); lone CR (`LONE_CR`); invalid UTF-8
  (`UTF8`); non-JSON (`JSON`).
- Canonicalization is idempotent for pure CRLF; a canonical file contains no CR.

Suite evidence at `eff4c380`:

| Run | Result |
|---|---|
| X2 three files (boundary + legacy + grant), unguarded | **69 passed, 0 skipped, 0 xfailed** |
| Same three files under `private_path_guard` | **69 passed, 0 skipped, 0 xfailed** |
| Boundary suite with every fixture converted to pure CRLF (633-byte form) | **37 passed** |

The 19 legacy + 13 grant cases execute on Linux; no `pytest.skip(` or
`pytest.xfail(` appears in either suite, and the manifest's AST name lists match
the suites exactly. The skip is a module-level `sys.platform != "linux"`
`pytestmark` with `PRODUCER_BOUNDARY_POSIX_ONLY` and the manifest path; no fake
Windows producer is introduced. **Windows execution of that skip path is
NOT_RUN locally** (source/manifest evidence only; the RC-v4 Windows CI run is
author-reported).

---

## 3. #342 — private path guard (PARTIAL; P2 findings)

The new normalization is correct for the tested drive/UNC/traversal forms:
drive backslash/slash/case, sibling `root2`, `..`, drive-relative and bare
drive, UNC case, sibling server/share, UNC traversal, long-path **drive**
(`\\?\D:\…`), POSIX case/sibling/`..`. The following aliases are **not
blocked**; each was measured through `_inside` and, where noted, through the
real audit hook:

1. **P2 REGRESSION — POSIX 3+ leading slashes.** `///owner/private/root/evil`
   and `////owner/private/root/evil` return `_inside=False`, while
   `os.path.abspath` (and the RC-v4 base guard) collapse these to
   `/owner/private/root/evil` and return `True`. The real hook allowed
   `open("///data/scratch/.../real-root/secret.txt")`; the same file via the
   one-slash path is refused. `//owner/private/root/evil` also bypasses, but
   that one is pre-existing on RC-v4.
   Cause: `_windows_form()` treats every string starting with `//` as UNC or
   device before POSIX normalization.
2. **P2 — extended-length UNC.** `\\?\UNC\server\share\private\evil` and
   `//?/UNC/server/share/private/evil` return `False` against
   `\\server\share\private`; the same path without the prefix returns `True`.
   The code strips `\\?\` and leaves `UNC\server\…` instead of rebuilding
   `\\server\…`.
3. **P2 — device alias.** `\\.\D:\owner\private\root\evil` is classified as a
   different family and not blocked, although on Windows `\\.\D:\path` names
   the same volume file.
4. **P2 — symlink alias (lexical design, pre-existing).** With
   `alias -> real-root`, `_inside("alias/secret", "real-root")` is `False` and
   the audit hook allowed reading the private file through the alias. The
   module documents lexical resolution; this is not a sandbox. If the guard is
   meant to stop accidental private-root reads, it needs a realpath check (and
   a test), or the claim must be narrowed.

These are **test-guard** findings, not a product/trust-boundary change. No
current suite uses these aliases. The README §9.2 sentence "The long-path
`\\?\` prefix names the same file" is not true for `\\?\UNC\…` today.

---

## 4. #343 — public in-tree Work Engine T10 fixtures (PASS at LOCAL)

At `032d4698` (base `40c4b1c`), with `private_path_guard` and a configured
private root, one process, nine files:

```
T10 authority_bridge + continuation_provenance + legacy_compatibility +
producer_binding + signed_finalization + finalize_consistency +
runner_receipt + finalize_entry + core_ability_boundary
→ 136 passed, 0 skipped, 0 xfailed (79.50s)
```

The five explicit refusal cases run and pass (not skipped): caller-JSON fake
label, T10 binding mismatch, receipt exit disagreement, forged
`trusted-parent:` label, and real-exit-1-with-PASS-text.

Independent identity/ReceiptError probe: **25/25 PASS**.

- Both fixtures import the in-tree `workengine`; `workengine.authority.__file__`
  and `workengine.store.__file__` resolve under the candidate tree; only one
  `workengine` package object exists in `sys.modules`.
- `ReceiptError` from the store path, `validate_verified_receipt`,
  `validate_t10_binding`, and both fixture namespaces is the **same class**
  `workengine.authority.ReceiptError`; a missing finalize record raises
  `EVIDENCE_MISSING`, a mismatched binding raises `T10_ACTOR_MISMATCH`, and a
  non-hex receipt anchor raises `INVALID_RECEIPT` — all with that type.
- No private-checkout path, `tarfile`, `WE_REPO`/`WE_HEAD`, or `workengine`
  `sys.modules` purge remains; the old #14/#15 SHAs are historical labels only.

**Evidence boundary:** the positive producer is `PUBLIC_REIMPLEMENTATION_LOCAL`
with a locally controlled receipt authority; it is **not** a production
trusted T10/OS Grant source. Old private #14/#15 source equality is
`HISTORICAL_PRIVATE_SOURCE_UNVERIFIED` / **NOT_RUN**, never PASS. The local
fake provider is not production trust.

---

## 5. RC-v5

Four read-only polls at 90 seconds (08:59Z–09:04Z) found **no
`rc/i1-v5*` branch and no matching PR**. RC-v5 is **PENDING**; no RC-v5
checkout was created and no S1/T10/Core/Native/Fleet Next/REAL_TEMP spot-check
was run against it. The I1 worktree was not accessed.

---

## 6. Judgment

| Item | Verdict | Level |
|---|---|---|
| #342 fixture canonicalization / pinned SHA | **PASS** | LOCAL |
| #342 X2 three-file + CRLF run | **PASS** (69 / 37) | LOCAL |
| #342 skip manifest (19+13, no skip/xfail) | **PASS** on Linux/source; Windows execution NOT_RUN | SOURCE/LOCAL |
| #342 private path guard | **PARTIAL** — drive/UNC/`..` forms pass; P2 aliases (long-path UNC, device, symlink) and P2 `///` regression | LOCAL |
| #343 public T10 fixtures | **PASS** (136 + 5 refusals; 25/25 identity) | LOCAL / PUBLIC_REIMPLEMENTATION_LOCAL |
| #343 private history equality | **NOT_VERIFIED / NOT_RUN** | — |
| RC-v5 | **PENDING** (4 polls, no ref) | — |

**No V1 signature, no merge, no release.** These are test-only changes; neither
PR closes the production trust gaps. RC-v5, Windows execution, REAL_DEVICE,
CLIENT_E2E, live provider/OAuth and the full T1–T12 matrix remain open.

## 7. Receipts

| Run | Result | Log |
|---|---|---|
| #342 probe (69 checks) | 12 FAIL, all guard aliases | `r10-pr342-probe.log` |
| #342 X2 three files | 69 passed | `r10-pr342-x2.log` |
| #342 X2 three files guarded | 69 passed | `r10-pr342-x2-guard.log` |
| #342 boundary with CRLF fixtures | 37 passed | `r10-pr342-crlf.log` |
| #343 nine files, guarded | 136 passed, 0 skip | `r10-pr343-nine.log` |
| #343 explicit refusals | 5 passed | `r10-pr343-refusals.log` |
| #343 identity/ReceiptError probe | 25/25 | `r10-pr343-probe.log` |
| RC-v5 polls | 4 polls, no ref | `r10-rcv5-poll.log` |
