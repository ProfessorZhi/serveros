"""Subprocess entry for #488's activation wrapper.

The wrapper is a script, not an installed module, so a test that called
``activation.main`` in-process would share the test's ``sys.modules`` with the
monitor it imports. Each invocation here is a fresh interpreter: its own boot
epoch, its own flock, its own import graph. That is the unit the cross-process
claims are about.

``QA_ROOTS`` is a JSON array of the composed integration roots. The wrapper is
imported under its real module name because #488 resolves ``--legacy-read`` and
``--next-write`` through ``module:callable`` too.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    for root in json.loads(os.environ["QA_ROOTS"]):
        if root not in sys.path:
            sys.path.insert(0, root)
    import activation  # noqa: WPS433 - the pinned #488 module, by path

    return int(activation.main(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
