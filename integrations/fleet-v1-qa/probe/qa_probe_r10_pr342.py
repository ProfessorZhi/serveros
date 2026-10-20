#!/usr/bin/env python3
"""Independent probe for PR #342 (X2 Windows fixture/guard portability).

Checks the two security-relevant claims:
  * canonical fixture bytes accept ONLY pure CRLF; changed JSON, BOM, mixed
    endings and a lone CR never pass the pinned SHA;
  * private_path_guard refuses Windows drive/UNC/drive-relative/.. forms and
    does not let an alias (long-path UNC, device namespace, symlink) escape.

Read-only against the candidate checkout.  Prints PASS/FAIL per check.
"""
from __future__ import annotations

import codecs
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

TREE = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(TREE / "tests"))
sys.path.insert(0, str(TREE / "tests" / "v1_portability"))

import v1_portability_harness as H  # noqa: E402
import private_path_guard as G  # noqa: E402

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


def raises_code(raw: bytes, code: str) -> tuple[bool, str]:
    try:
        H.canonical_json_bytes(raw)
    except H.FixtureBytesError as exc:
        return code in str(exc), str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return False, "no exception"


PIN = {
    "finalize_t10_verified.v1.json":
        "81f51a8580df37fff301e35a2a2c53e44c5fc9ed48ba0456183759629dfe0c95",
    "finalize_unsigned.v1.json":
        "5a7f55b85659e188e3c069afe848fccc0a6e11271901a7b802685bab4653de62",
    "lease_grant.v1.json":
        "cebab374e727adb60a8d0671f2f3092290dbeb5479bb62d8caccc3eaa8f10e8b",
}

# ---- fixture canonicalization ------------------------------------------------

for name, digest in PIN.items():
    raw = (TREE / "tests" / "v1_portability" / "fixtures" / name).read_bytes()
    canonical = H.canonical_fixture_bytes(name)
    check(f"pin/{name}/lf-raw", raw == canonical, f"{len(raw)} vs {len(canonical)}")
    check(f"pin/{name}/sha", hashlib.sha256(canonical).hexdigest() == digest)
    crlf = canonical.replace(b"\n", b"\r\n")
    check(f"pin/{name}/crlf-accepted", H.canonical_json_bytes(crlf) == canonical)
    check(f"pin/{name}/crlf-sha",
          hashlib.sha256(H.canonical_json_bytes(crlf)).hexdigest() == digest)

canonical = H.fixture_bytes(H.T10_FIXTURE)
pin = PIN[H.T10_FIXTURE]

# semantic changes, with and without CRLF, must never hash to the pin
semantic = {
    "field-value-same-length": canonical.replace(b'"verdict": "PASS"',
                                                 b'"verdict": "FAIL"'),
    "field-added":
        canonical.replace(b"{", b'{"qa_extra": true,', 1),
    "field-removed":
        canonical.replace(b'  "cases": [\n    "one"\n  ],\n', b"", 1),
    "whitespace-added": canonical.replace(b"\n", b" \n", 1),
    "duplicate-key": canonical.replace(
        b'"verdict": "PASS"',
        b'"verdict": "PASS", "verdict": "FAIL"', 1),
}
for label, changed in semantic.items():
    check(f"semantic/{label}/changed", changed != canonical)
    check(f"semantic/{label}/lf-pin",
          hashlib.sha256(H.canonical_json_bytes(changed)).hexdigest() != pin)
    check(f"semantic/{label}/crlf-pin",
          hashlib.sha256(H.canonical_json_bytes(
              changed.replace(b"\n", b"\r\n"))).hexdigest() != pin)

bom_utf8 = codecs.BOM_UTF8 + canonical
bom_utf16le = codecs.BOM_UTF16_LE + canonical
bom_utf16be = codecs.BOM_UTF16_BE + canonical
bom_utf32le = codecs.BOM_UTF32_LE + canonical
mixed = canonical.replace(b"\n", b"\r\n", 1)
lone_cr = canonical.replace(b"\n", b"\r\n").replace(b"\r\n", b"\r", 1)
for label, raw, code in (
        ("bom-utf8", bom_utf8, "BOM"),
        ("bom-utf16le", bom_utf16le, "BOM"),
        ("bom-utf16be", bom_utf16be, "BOM"),
        ("bom-utf32le", bom_utf32le, "BOM"),
        ("mixed-eol", mixed, "MIXED_EOL"),
        ("lone-cr", lone_cr, "LONE_CR"),
        ("invalid-utf8", b"\xff" + canonical, "UTF8"),
        ("invalid-json", b"not json\n", "JSON"),
):
    ok, detail = raises_code(raw, code)
    check(f"reject/{label}", ok, detail)

check("canonical/idempotent-crlf",
      H.canonical_json_bytes(canonical.replace(b"\n", b"\r\n")) == canonical)
check("canonical/pure-lf-no-cr", b"\r" not in canonical)

print("fixture_sha_t10=" + hashlib.sha256(canonical).hexdigest())
print("fixture_bytes_t10=%d" % len(canonical))

# ---- private path guard ------------------------------------------------------

CASES = [
    # family: description, candidate, root, must_block
    ("drive-backslash", r"D:\owner\private\root\evil",
     r"D:\owner\private\root", True),
    ("drive-slash", "D:/owner/private/root/evil",
     r"D:\owner\private\root", True),
    ("drive-case", r"d:\OWNER\PRIVATE\ROOT\evil",
     r"D:\owner\private\root", True),
    ("drive-sibling", r"D:\owner\private\root2\evil",
     r"D:\owner\private\root", False),
    ("drive-dotdot", r"D:\owner\private\root\..\other",
     r"D:\owner\private\root", False),
    ("longpath-drive", r"\\?\D:\owner\private\root\evil",
     r"D:\owner\private\root", True),
    ("drive-relative", r"D:relative\evil", r"D:\owner\private\root", False),
    ("bare-drive", "D:", r"D:\owner\private\root", False),
    ("other-drive", r"E:\owner\private\root\evil",
     r"D:\owner\private\root", False),
    ("unc-inside", r"\\server\share\private\evil",
     r"\\server\share\private", True),
    ("unc-case", r"\\SERVER\SHARE\PRIVATE\EVIL",
     r"\\server\share\private", True),
    ("unc-share-sibling", r"\\server\share\private2\evil",
     r"\\server\share\private", False),
    ("unc-dotdot", r"\\server\share\private\..\other",
     r"\\server\share\private", False),
    ("unc-server-sibling", r"\\server2\share\private\evil",
     r"\\server\share\private", False),
    ("unc-share-sibling2", r"\\server\share2\private\evil",
     r"\\server\share\private", False),
    ("longpath-unc", r"\\?\UNC\server\share\private\evil",
     r"\\server\share\private", True),
    ("longpath-unc-slash", "//?/UNC/server/share/private/evil",
     r"\\server\share\private", True),
    ("device-drive", r"\\.\D:\owner\private\root\evil",
     r"D:\owner\private\root", True),
    # Volume-to-drive mapping is not knowable lexically; record, do not grade.
    ("globalroot", r"\\?\GLOBALROOT\Device\HarddiskVolume1\owner\private\root\evil",
     r"D:\owner\private\root", None),
    ("posix-inside", "/owner/private/root/evil", "/owner/private/root", True),
    ("posix-sibling", "/owner/private/root2/evil", "/owner/private/root", False),
    ("posix-dotdot", "/owner/private/root/../other",
     "/owner/private/root", False),
    ("posix-case", "/OWNER/PRIVATE/ROOT/evil", "/owner/private/root", False),
    # POSIX treats 2+ leading slashes as the same root (Linux collapses them).
    # A Windows-UNC pre-classification must not turn these into another family.
    ("posix-double-slash", "//owner/private/root/evil",
     "/owner/private/root", True),
    ("posix-triple-slash", "///owner/private/root/evil",
     "/owner/private/root", True),
    ("posix-quad-slash", "////owner/private/root/evil",
     "/owner/private/root", True),
]
for label, candidate, root, must_block in CASES:
    got = G._inside(candidate, root)
    if must_block is None:
        print(f"INFO guard/{label} got={got} (volume mapping not graded)")
        continue
    check(f"guard/{label}", got is must_block,
          f"candidate={candidate!r} root={root!r} got={got} want={must_block}")

# Symlink alias to the private root: a lexical guard lets it through.
base = Path(tempfile.mkdtemp(prefix="q1-r10-symlink-"))
real = base / "real-root"
real.mkdir()
(real / "secret.txt").write_text("owner-private\n", encoding="utf-8")
link = base / "alias"
link.symlink_to(real, target_is_directory=True)
check("guard/symlink-candidate-string",
      G._inside(str(link / "secret.txt"), str(real)) is True,
      f"lexical result={G._inside(str(link / 'secret.txt'), str(real))}")
check("guard/symlink-root-string",
      G._inside(str(real / "secret.txt"), str(link)) is True,
      f"lexical result={G._inside(str(real / 'secret.txt'), str(link))}")

# Actual audit-hook enforcement, same process (hook is permanent; last checks).
os.environ[G.GUARD_ENV] = "1"
os.environ[G.ROOTS_ENV] = str(real)
G.install_hook()


def read_ok(path) -> bool:
    try:
        with open(os.fspath(path), encoding="utf-8") as fh:
            fh.read()
        return True
    except PermissionError:
        return False
    except OSError:
        return False


check("guard/hook-direct-blocked", read_ok(real / "secret.txt") is False)
check("guard/hook-symlink-blocked",
      read_ok(link / "secret.txt") is False,
      "symlink alias read the private root despite the guard")
rel = str(real / "secret.txt").lstrip("/")
for prefix in ("//", "///", "////"):
    check(f"guard/hook-{len(prefix)}slash-blocked",
          read_ok(prefix + rel) is False,
          f"{prefix + rel} read the private root despite the guard")
real_ok = read_ok(real / "secret.txt")
print(f"hook_direct_read_allowed={real_ok}")

print(f"TOTAL {TOTAL} FAIL {FAIL}")
raise SystemExit(1 if FAIL else 0)
