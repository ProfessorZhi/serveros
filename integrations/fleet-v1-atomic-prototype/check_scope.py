#!/usr/bin/env python3
"""Static diff check: this prototype must not touch production files.

Usage:
  python integrations/fleet-v1-atomic-prototype/check_scope.py <base-ref>

Exit 0 iff every path changed between <base-ref> and HEAD is under
integrations/fleet-v1-atomic-prototype/ or is the dedicated prototype test.
Exit 1 (and print the offending paths) otherwise. This is a scope receipt, not
a CI authorization.
"""
from __future__ import annotations

import subprocess
import sys

ALLOWED_PREFIXES = ("integrations/fleet-v1-atomic-prototype/",)
ALLOWED_EXACT = ("tests/test_s1_atomic_prototype.py",)
ALLOWED_TEST_PREFIX = "tests/test_s1_atomic_prototype"


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else "31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd"
    committed = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base}..HEAD"], text=True
    ).splitlines()
    working = []
    for line in subprocess.check_output(
            ["git", "status", "--porcelain"], text=True).splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            working.append(path)
    changed = sorted(set(committed) | set(working))
    def _allowed(path):
        return (path in ALLOWED_EXACT
                or (path.startswith(ALLOWED_TEST_PREFIX)
                    and path.endswith(".py"))
                or any(path.startswith(p) for p in ALLOWED_PREFIXES))
    bad = [path for path in changed if not _allowed(path)]
    print(f"base={base} changed={len(changed)} allowed={len(changed) - len(bad)} "
          f"forbidden={len(bad)}")
    for path in changed:
        print(("FORBIDDEN " if path in bad else "ok        ") + path)
    if bad:
        print("SCOPE_VIOLATION: production/other paths were changed", file=sys.stderr)
        return 1
    print("SCOPE_OK: prototype and dedicated test only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
