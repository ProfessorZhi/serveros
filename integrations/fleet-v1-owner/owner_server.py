"""Explicit owner composition: connect the core daemon to its Work owner.

Ordinary core startup does not import Work Engine. Deployment chooses this
entry and supplies both the private issuer file and existing Work state path.
No service, policy, credential or network operation runs at module import.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from workengine.store import ServerOSStore

    name = "serveros_runtime_daemon"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / "runtime" / "daemon.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].main(argv, owner_work_factory=ServerOSStore)


if __name__ == "__main__":
    raise SystemExit(main())
