#!/usr/bin/env python3
"""Read-only audit: current MCP tool descriptions vs the actual mirror count.

This is a finding script, not a CI gate. The affected file is
`fleet_mcp/tools.py`, outside the L4 packaging lane's writable ownership; the
fix is a patch proposal to the root owner (see the adjacent .patch file).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fleet_mcp import tools  # noqa: E402


def main() -> int:
    failures = []
    actual = len(tools.NATIVE_MIRROR_TOOLS)
    if actual != 31:
        failures.append(f"expected 31 mirror verbs at fleet-mcp-7, got {actual}")
    if "22 inherited verbs" in tools.NATIVE_CAPABILITY_TOOL.description:
        failures.append(
            "fleet_cloudcli_native_capability still says '22 inherited verbs' "
            f"while the surface mirrors {actual}")
    if "(22 verbs)" in tools.NATIVE_MANIFEST_TOOL.title:
        failures.append(
            "fleet_cloudcli_native_manifest title still says '(22 verbs)' "
            f"while the surface mirrors {actual}")
    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("PASS: current tool descriptions match the served mirror")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
