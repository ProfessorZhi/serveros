#!/usr/bin/env python3
"""Independent probe for #353 (Windows host-privilege skip + alias refusal).

Security properties graded:
  * WinError 1314 skips ONLY on os.name=="nt"; every other OSError re-raises;
  * drive-relative / 8.3 / trailing-space-or-dot are refused conservatively,
    independent of CWD, while absolute/sibling/inside classification is intact;
  * fixture LF/CRLF pins still reject changed bytes, BOM, mixed EOL, lone CR.

This is a LOCAL synthetic check. It does not resolve real NTFS 8.3 names and
does not claim a Windows pass; the FJPCOS receipt is REAL_WINDOWS_READ_ONLY.
"""
from __future__ import annotations

import codecs
import hashlib
import os
import sys
import tempfile
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE / "tests"))
sys.path.insert(0, str(TREE / "tests" / "v1_portability"))

import private_path_guard as G  # noqa: E402
import v1_portability_harness as H  # noqa: E402

FAIL = 0
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global FAIL, TOTAL
    TOTAL += 1
    if cond:
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {detail}")


def err(winerror=None):
    exc = OSError(22, "not permitted")
    if winerror is not None:
        exc.winerror = winerror
    return exc


# ---- WinError 1314 is the only skip -----------------------------------------
reason = G.symlink_privilege_reason(err(1314), platform="nt")
check("privilege/nt-1314-skip", reason is not None and
      G.HOST_PRIVILEGE_NO_SYMLINK in reason, str(reason))
check("privilege/posix-1314-no-skip",
      G.symlink_privilege_reason(err(1314), platform="posix") is None)
check("privilege/nt-5-no-skip",
      G.symlink_privilege_reason(err(5), platform="nt") is None)
check("privilege/nt-no-winerror-no-skip",
      G.symlink_privilege_reason(err(None), platform="nt") is None)


class FakeLink:
    def __init__(self, code):
        self.code = code

    def symlink_to(self, *args, **kwargs):
        raise err(self.code)


import pytest  # noqa: E402

try:
    G.make_directory_symlink_or_skip(FakeLink(1314), "target", platform="nt")
    skipped_1314 = False
except pytest.skip.Exception:
    skipped_1314 = True
except OSError:
    skipped_1314 = False
check("privilege/make-symlink-1314-skips", skipped_1314)
try:
    G.make_directory_symlink_or_skip(FakeLink(5), "target", platform="nt")
    other_reopened = False
except OSError:
    other_reopened = True
check("privilege/make-symlink-other-oserror-reraises", other_reopened)
try:
    G.make_directory_symlink_or_skip(FakeLink(1314), "target", platform="posix")
    posix_reopened = False
except OSError:
    posix_reopened = True
check("privilege/make-symlink-posix-1314-reraises", posix_reopened)

# ---- conservative refusal of unresolvable Windows aliases --------------------
WIN_ROOT = r"D:\owner\private\root"
check("refuse/8.3-drive",
      G._must_refuse(r"C:\PROGRA~1\private\evil",
                     (r"C:\Program Files\private",), windows=True) is True)
check("refuse/8.3-unc",
      G._must_refuse(r"\\server\share\PRIVAT~1\evil",
                     (r"\\server\share\private",), windows=True) is True)
check("refuse/trailing-space",
      G._must_refuse(r"D:\owner\private\root \evil", (WIN_ROOT,),
                     windows=True) is True)
check("refuse/trailing-dot",
      G._must_refuse(r"D:\owner\private\root.\evil", (WIN_ROOT,),
                     windows=True) is True)

base = Path(tempfile.mkdtemp(prefix="q1-r14-cwd-"))
for name in ("a", "b"):
    (base / name).mkdir()
    old = os.getcwd()
    try:
        os.chdir(base / name)
        check(f"refuse/drive-relative-cwd-{name}",
              G._must_refuse(r"D:relative\path", (WIN_ROOT,),
                             windows=True) is True)
        check(f"allow/drive-absolute-cwd-{name}",
              G._must_refuse(r"D:\public\file", (WIN_ROOT,),
                             windows=True) is False)
        check(f"allow/drive-sibling-cwd-{name}",
              G._must_refuse(r"D:\owner\private\root2\evil", (WIN_ROOT,),
                             windows=True) is False)
        check(f"refuse/drive-inside-cwd-{name}",
              G._must_refuse(r"D:\owner\private\root\file", (WIN_ROOT,),
                             windows=True) is True)
    finally:
        os.chdir(old)

# POSIX classification unchanged: no over-blocking of absolute/sibling paths.
check("posix/allow-outside",
      G._must_refuse("/tmp/public/file", ("/tmp/private",)) is False)
check("posix/allow-sibling",
      G._must_refuse("/tmp/private2/file", ("/tmp/private",)) is False)
check("posix/refuse-inside",
      G._must_refuse("/tmp/private/file", ("/tmp/private",)) is True)
check("posix/refuse-multislash",
      G._must_refuse("///tmp/private/file", ("/tmp/private",)) is True)

# POSIX symlink alias is still refused (live test runs on Linux).
real = base / "private"
real.mkdir()
(real / "secret.txt").write_text("private\n", encoding="utf-8")
alias = base / "alias"
alias.symlink_to(real, target_is_directory=True)
check("symlink/inside", G._inside(str(alias / "secret.txt"), str(real)) is True)
check("symlink/refuse",
      G._must_refuse(str(alias / "secret.txt"), (str(real),)) is True)

# ---- fixture bytes ----------------------------------------------------------
PIN = {
    "finalize_t10_verified.v1.json":
        "81f51a8580df37fff301e35a2a2c53e44c5fc9ed48ba0456183759629dfe0c95",
    "finalize_unsigned.v1.json":
        "5a7f55b85659e188e3c069afe848fccc0a6e11271901a7b802685bab4653de62",
    "lease_grant.v1.json":
        "cebab374e727adb60a8d0671f2f3092290dbeb5479bb62d8caccc3eaa8f10e8b",
}
for name, digest in PIN.items():
    canonical = H.canonical_fixture_bytes(name)
    check(f"fixture/{name}/lf-sha",
          hashlib.sha256(canonical).hexdigest() == digest)
    check(f"fixture/{name}/crlf-ok",
          H.canonical_json_bytes(canonical.replace(b"\n", b"\r\n")) == canonical)

canonical = H.fixture_bytes(H.T10_FIXTURE)
pin = PIN[H.T10_FIXTURE]
changed = canonical.replace(b'"verdict": "PASS"', b'"verdict": "FAIL"')
check("fixture/changed-field",
      hashlib.sha256(H.canonical_json_bytes(changed)).hexdigest() != pin)
check("fixture/changed-field-crlf",
      hashlib.sha256(H.canonical_json_bytes(
          changed.replace(b"\n", b"\r\n"))).hexdigest() != pin)
for label, raw, code in (
        ("bom", codecs.BOM_UTF8 + canonical, "BOM"),
        ("mixed", canonical.replace(b"\n", b"\r\n", 1), "MIXED_EOL"),
        ("lone-cr", canonical.replace(b"\n", b"\r\n").replace(
            b"\r\n", b"\r", 1), "LONE_CR")):
    try:
        H.canonical_json_bytes(raw)
        ok, detail = False, "accepted"
    except H.FixtureBytesError as exc:
        ok, detail = code in str(exc), str(exc)
    check(f"fixture/reject-{label}", ok, detail)

print("REAL_NTFS_8.3_MAPPING=NOT_RUN (synthetic only; FJPCOS receipt is REAL_WINDOWS_READ_ONLY)")
print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
