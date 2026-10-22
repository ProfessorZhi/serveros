"""Fleet launcher owned by the full ServerOS runtime, over its existing ledger.

The standalone Fleet artifact never imports this factory. This launcher must
find a real owner register; absence is not permission to start a second one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fleet_mcp.errors import DISPATCH_STATE_UNAVAILABLE, refuse
from fleet_mcp.fleet import FleetService

_HERE = Path(__file__).resolve().parent


def _load(name, path):
    if name in sys.modules:
        module = sys.modules[name]
        if Path(module.__file__).resolve() != path.resolve():
            raise RuntimeError("the owner runtime contains mixed source revisions")
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def control_store_path():
    registry = _load("serveros_runtime_registry", _HERE / "registry.py")
    return registry.state_root() / "runtime" / "agent-control" / "store.json"


def build_service(config, *, providers=None):
    try:
        owner = _load("serveros_runtime_agent_control_store", _HERE / "agent_control_store.py")
        store = owner.AgentControlStore(control_store_path(), require_existing=True)
    except Exception as exc:
        raise refuse(DISPATCH_STATE_UNAVAILABLE,
                     "the existing ServerOS Agent Control owner is unavailable; "
                     "no empty store or process-local fallback was created",
                     retry_safe=False) from exc
    return FleetService(config, attempts=store, providers=providers)


def main(argv=None):
    from fleet_mcp.runtime import main as fleet_main
    return fleet_main(argv, service_factory=build_service)
