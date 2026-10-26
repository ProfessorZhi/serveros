"""Explicit owner composition: connect the core daemon to its Work owner.

Ordinary core startup does not import Work Engine. Deployment chooses this
entry and supplies both the private issuer file and existing Work state path.
No service, policy, credential or network operation runs at module import.
"""
from __future__ import annotations

import importlib.util
import argparse
import sys
from pathlib import Path


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from workengine.store import ServerOSStore
    from workengine.owner_artifacts import real_path
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--owner-artifacts-root")
    parser.add_argument("--owner-workspace-root")
    parser.add_argument("--owner-continuation-receipts-root")
    locations, remaining = parser.parse_known_args(argv)
    def work_factory(path):
        store = ServerOSStore(path)
        for field in ("owner_artifacts_root", "owner_workspace_root", "owner_continuation_receipts_root"):
            raw = getattr(locations, field)
            if raw is not None:
                setattr(store, field, real_path(raw))
        return store

    name = "serveros_runtime_daemon"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / "runtime" / "daemon.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].main(remaining, owner_work_factory=work_factory)


if __name__ == "__main__":
    raise SystemExit(main())
