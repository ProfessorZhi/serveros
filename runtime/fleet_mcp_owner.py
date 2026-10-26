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
from fleet_mcp.events import EventStore
from fleet_mcp.run_watcher import RunWatcher

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


def _owner_notification_transport():
    """Explicit deployment only: verified owner metadata, bot transport, no env target."""
    live = _load("serveros_runtime_live_sender", _HERE / "live_sender.py")
    transport = _load("serveros_runtime_message_transport", _HERE / "message_transport.py")
    binding = live.owner_binding()
    sender = transport.sender(binding["recipient"], identity="bot", timeout=20)
    return binding, sender


def configure_notifications(service, *, enabled=False, cutover=None):
    if not enabled:
        return
    if cutover is None:
        raise ValueError("--notify-owner requires an explicit --notification-cutover")
    delivery = _load("serveros_runtime_fleet_event_delivery", _HERE / "fleet_event_delivery.py")
    try:
        binding, sender = _owner_notification_transport()
    except Exception:
        binding, sender = {}, None
    service.event_delivery = delivery.FleetEventDelivery(service.events, sender, binding, cutover=cutover)


def build_service(config, *, providers=None, notify_owner=False, notification_cutover=None):
    try:
        owner = _load("serveros_runtime_agent_control_store", _HERE / "agent_control_store.py")
        path = control_store_path()
        store = owner.AgentControlStore(path, require_existing=True)
    except Exception as exc:
        raise refuse(DISPATCH_STATE_UNAVAILABLE,
                     "the existing ServerOS Agent Control owner is unavailable; "
                     "no empty store or process-local fallback was created",
                     retry_safe=False) from exc
    try:
        events = EventStore(path.parent / "fleet-events.sqlite3")
    except Exception as exc:
        raise refuse(DISPATCH_STATE_UNAVAILABLE,
                     "the owned Fleet event projection is unavailable", retry_safe=False) from exc
    service = FleetService(config, attempts=store, events=events, providers=providers)
    service.run_watcher = RunWatcher(service, events, store)
    configure_notifications(service, enabled=notify_owner, cutover=notification_cutover)
    return service


def main(argv=None):
    import argparse
    from fleet_mcp.runtime import main as fleet_main
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--notify-owner", action="store_true")
    parser.add_argument("--notification-cutover", type=float)
    args, remaining = parser.parse_known_args(sys.argv[1:] if argv is None else argv)
    if args.notify_owner != (args.notification_cutover is not None):
        parser.error("--notify-owner and --notification-cutover must be supplied together")
    def factory(config):
        return build_service(config, notify_owner=args.notify_owner,
                             notification_cutover=args.notification_cutover)
    return fleet_main(remaining, service_factory=factory)
