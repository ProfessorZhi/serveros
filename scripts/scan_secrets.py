#!/usr/bin/env python3
"""Scan a tree for credential shapes, so a release can fail instead of shipping one.

§28 asks for this as a GATE, and the hard part of a gate is not finding things --
it is being wrong in the safe direction. A scanner that fires on ordinary text is
a scanner someone disables, and a disabled scanner is worse than none because the
release still claims it ran. So every rule below is a *prefix-bearing* shape: a
value that is unmistakably a credential and cannot be produced by prose, a
placeholder, or a path.

Deliberately NOT scanned, and why:

  * bare high-entropy strings -- they match hashes, fingerprints and the
    content-addressed digests this product is largely made of;
  * `password = "..."` and friends -- that shape is exactly what a redaction
    test needs to write in order to prove redaction works, so it would fail the
    build that added the test;
  * private IPs, emails, hostnames -- identifier disclosure is a different
    question with a different owner, and folding it in here would make one
    failure mean two things.

The exclusion list is not a loophole: it is a short, named set of paths whose
whole purpose is to contain fake credentials. Anything else is reported.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Each rule: a name, and a pattern anchored on a prefix that only a real
# credential carries. The trailing length requirement keeps prose like
# "ghp_ tokens" from matching.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key",
     re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("github-token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("aws-access-key-id",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9][A-Za-z0-9-]{9,}\b")),
    ("anthropic-or-openai-key",
     # `sk-` followed by enough entropy to be a key rather than a hyphenated
     # word. 32 is below every vendor's real length and above any English word.
     re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
)

# Paths whose purpose is to hold fake credentials, so a match there is the
# fixture working rather than a leak.
#
# Named individually and never by wildcard or by directory. `tests/` is
# deliberately NOT exempt wholesale: a credential committed under `tests/` is
# exactly as committed as one committed anywhere else, and the reason these two
# files are listed is that they assert the *refusal* of credentials -- removing
# the fixture would remove the test. Every addition to this list is a visible
# decision in a diff rather than a directory someone stopped looking at.
FIXTURE_PATHS: tuple[str, ...] = (
    "tests/test_container_attribution.py",
    "tests/test_memory_ledger.py",
    "tests/test_secret_scan.py",
)

TEXT_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".json", ".toml", ".txt", ".sh",
                 ".cfg", ".ini", ".service", ".env")
MAX_BYTES = 4 * 1024 * 1024


def is_fixture(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            rel = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if rel in FIXTURE_PATHS:
            return True
    return False


def scan_file(path: Path) -> list[dict]:
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        # A binary or unreadable file is not evidence either way. The tree this
        # runs on is text by construction, so reporting here would be noise.
        return []
    found = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in RULES:
            if pattern.search(line):
                found.append({"rule": name, "line": lineno})
    return found


# Trees that are never part of a release and are large enough to matter.
PRUNED_DIRS = (".git", "__pycache__", "node_modules", ".venv", "venv")


def candidate_files(root: Path):
    """Every file a scan of `root` should consider.

    A file is a legitimate argument — a release body, a manifest, a config — and
    `os.walk` on a file yields nothing, which would report CLEAN after reading
    zero files. That is the exact failure this whole class of checker exists to
    prevent, so the single-file case is handled rather than inherited.
    """
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def scan(root: Path) -> dict:
    roots = [root]
    single = root.is_file()
    hits, scanned, skipped = [], 0, 0
    for path in candidate_files(root):
        if path.suffix not in TEXT_SUFFIXES:
            skipped += 1
            continue
        if is_fixture(path, roots):
            skipped += 1
            continue
        scanned += 1
        rel = path.name if single else path.relative_to(root).as_posix()
        for hit in scan_file(path):
            hits.append({"path": rel, **hit})
    return {
        "root": str(root),
        "scanned_files": scanned,
        "skipped_files": skipped,
        "rules": [name for name, _ in RULES],
        "findings": hits,
        "verdict": "CLEAN" if not hits else "CREDENTIAL_SHAPE_FOUND",
    }


def render(report: dict) -> None:
    print(f"scanned {report['scanned_files']} file(s) under {report['root']} "
          f"({report['skipped_files']} skipped: non-text or named fixture)")
    for hit in report["findings"]:
        print(f"  {hit['path']}:{hit['line']}  {hit['rule']}")
    print(f"VERDICT: {report['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="+", type=Path,
                    help="tree(s) to scan; a release artifact directory, or the repository")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    reports = []
    for path in args.paths:
        if not path.exists():
            print(f"ERROR: no such path: {path}", file=sys.stderr)
            return 2
        reports.append(scan(path))

    if args.json:
        print(json.dumps(reports if len(reports) > 1 else reports[0],
                         indent=2, ensure_ascii=False))
    else:
        for report in reports:
            render(report)
    return 0 if all(r["verdict"] == "CLEAN" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
