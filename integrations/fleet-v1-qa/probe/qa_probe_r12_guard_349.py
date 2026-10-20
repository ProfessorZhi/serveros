#!/usr/bin/env python3
"""Independent probe for #347/#349 X2 guard aliases and #349 Windows slice.

Grades the security claims, not the author's test count:
  * drive/UNC/device/long-path/symlink aliases must be refused;
  * POSIX case is strict with windows=False; Windows drive case folds;
  * 8.3 short names and unresolved drive-relative paths are ambiguous and must
    not be reported safe;
  * fixture canonical bytes still reject tamper/BOM/mixed/lone CR.

Read-only against the candidate checkout. Prints PASS/FAIL per check.
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


CASES = [
    ("longpath-unc", r"\\?\UNC\server\share\private\evil",
     r"\\server\share\private", True),
    ("longpath-unc-slash", "//?/UNC/server/share/private/evil",
     r"\\server\share\private", True),
    ("longpath-drive", r"\\?\D:\owner\private\root\evil",
     r"D:\owner\private\root", True),
    ("device-drive", r"\\.\D:\owner\private\root\evil",
     r"D:\owner\private\root", True),
    ("posix-triple", "///owner/private/root/evil",
     "/owner/private/root", True),
    ("posix-quad", "////owner/private/root/evil",
     "/owner/private/root", True),
    ("unc-sibling", r"\\server\share\private2\evil",
     r"\\server\share\private", False),
    ("unc-dotdot", r"\\server\share\private\..\other",
     r"\\server\share\private", False),
    ("drive-sibling", r"D:\owner\private\root2\evil",
     r"D:\owner\private\root", False),
    ("drive-dotdot", r"D:\owner\private\root\..\other",
     r"D:\owner\private\root", False),
]
for label, candidate, root, expected in CASES:
    got = G._inside(candidate, root)
    check(f"alias/{label}", got is expected,
          f"candidate={candidate!r} root={root!r} got={got} want={expected}")

# ambiguous device namespaces are refused even when they cannot be mapped
for label, value in (
        ("physicaldrive", r"\\.\PhysicalDrive0"),
        ("volume", r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\evil"),
        ("globalroot",
         r"\\?\GLOBALROOT\Device\HarddiskVolume1\owner\private\root\evil")):
    check(f"ambiguous/{label}",
          G._must_refuse(value, (r"D:\owner\private\root",)) is True)

# Host-aware case semantics.
check("case/posix-strict",
      G._inside("/OWNER/PRIVATE/ROOT/evil", "/owner/private/root",
                windows=False) is False)
check("case/windows-fold",
      G._inside(r"D:\OWNER\PRIVATE\ROOT\evil", r"D:\owner\private\root",
                windows=True) is True)

# 8.3 short name: on a Windows volume with short names enabled this is the SAME
# file as `C:\Program Files\private\evil`. It must be refused or explicitly
# marked ambiguous, never reported safe.
short = G._inside(r"C:\PROGRA~1\private\evil", r"C:\Program Files\private",
                  windows=True)
short_amb = G._forms(r"C:\PROGRA~1\private\evil", windows=True)[1]
check("ambiguous/8.3-short-name", short is True or short_amb is True,
      f"inside={short} ambiguous={short_amb}")

# A drive-relative path cannot be resolved without the per-drive CWD. It must
# not be reported safe on a host that cannot resolve it.
rel = G._inside(r"C:..\private\evil", r"C:\work\private", windows=True)
rel_amb = G._forms(r"C:..\private\evil", windows=True)[1]
check("ambiguous/drive-relative", rel is True or rel_amb is True,
      f"inside={rel} ambiguous={rel_amb}")

# POSIX symlink alias through the real audit hook.
base = Path(tempfile.mkdtemp(prefix="q1-r12-symlink-"))
real = base / "private"
real.mkdir()
(real / "secret.txt").write_text("private\n", encoding="utf-8")
alias = base / "alias"
alias.symlink_to(real, target_is_directory=True)
check("symlink/inside",
      G._inside(str(alias / "secret.txt"), str(real)) is True)
check("symlink/must-refuse",
      G._must_refuse(str(alias / "secret.txt"), (str(real),)) is True)

# Fixture canonicalization (same contract as #342/#343).
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
    check(f"fixture/{name}/sha",
          hashlib.sha256(canonical).hexdigest() == digest)
    crlf = canonical.replace(b"\n", b"\r\n")
    check(f"fixture/{name}/crlf", H.canonical_json_bytes(crlf) == canonical)

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
        ok = False
        detail = "accepted"
    except H.FixtureBytesError as exc:
        ok = code in str(exc)
        detail = str(exc)
    check(f"fixture/reject-{label}", ok, detail)

print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
