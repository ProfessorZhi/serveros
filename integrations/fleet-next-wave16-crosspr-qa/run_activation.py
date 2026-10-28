"""Subprocess entry for #498's activation wrapper.

Each invocation is a fresh interpreter: its own boot epoch, flock and import
graph. That is the unit the cross-process claims are about.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    for root in json.loads(os.environ["QA_ROOTS"]):
        if root not in sys.path:
            sys.path.insert(0, root)
    import activation

    return int(activation.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
