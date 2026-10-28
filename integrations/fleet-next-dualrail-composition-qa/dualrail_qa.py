"""Cross-PR QA for #480's parallel-plugin preflight and #483's Legacy safety.

Both producer modules are read-only dependencies. This module maps one
synthetic Legacy/Next placement into each producer's own input shape, calls
the producers' public functions, and reports where they agree — and where one
models a resource the other does not.

No Creator, network, filesystem, service, OAuth, credential, model or IM.
"""
from __future__ import annotations

import copy
import importlib.util
from typing import Any

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


def _load(name: str, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    __import__("sys").modules[name] = module
    spec.loader.exec_module(module)
    return module


parallel = _load(
    "qa_parallel_plugin",
    ROOT / "integrations" / "fleet-next-parallel-plugin" / "parallel_plugin.py")
legacy_safety = _load(
    "qa_legacy_safety",
    ROOT / "integrations" / "fleet-next-legacy-safety" / "legacy_safety.py")

LEGACY_SKILLS = frozenset(parallel.LEGACY_SKILLS)
NEXT_SKILLS = frozenset(parallel.NEXT_SKILLS)
SHARED_RESOURCES = (
    "backend_plugin_id", "mcp_url", "port", "datastore/state_root",
)
ONLY_480 = (
    "oauth_audience", "credential_anchor", "tool_namespace", "skills",
    "source_pin", "archive_digest",
)
ONLY_483 = (
    "socket_path", "install_root", "lock_root", "unit",
)


def legacy_record() -> dict[str, Any]:
    return {
        "track": "legacy",
        "backend_plugin_id": "plugin_legacy_fleet",
        "release_id": "PluginRelease_LEGACY_7",
        "name": "serveros-fleet-ingress",
        "version": "0.1.0",
        "mcp_url": "https://legacy-fleet.example/mcp",
        "oauth_audience": "legacy-fleet-audience",
        "credential_anchor": "vault:legacy-fleet",
        "port": 3411,
        "datastore": "/var/lib/serveros/legacy.db",
        "install_root": "/opt/serveros-fleet",
        "lock_root": "/run/serveros-fleet",
        "unit": "serveros-fleet-gateway.service",
        "socket_path": "/run/serveros-fleet/gateway.sock",
        "tool_namespace": "serveros-fleet",
        "skills": sorted(LEGACY_SKILLS),
        "source_pin": "legacy_install",
        "archive_sha256": "1" * 64,
        "content_sha256": "2" * 64,
        "members": 14,
        "fleet_instance_id": "legacy-instance-1",
        "route_id": "legacy-route-1",
        "boot_id": "legacy-boot-1",
    }


def next_record() -> dict[str, Any]:
    pin = parallel.TRUSTED_SOURCE_PINS["fleet_v1_9skill"]
    return {
        "track": "next",
        "backend_plugin_id": "plugin_next_fleet",
        "release_id": "PluginRelease_NEXT_1",
        "name": "serveros-fleet-next",
        "version": "1.0.0",
        "mcp_url": "https://next-fleet.example/mcp",
        "oauth_audience": "next-fleet-audience",
        "credential_anchor": "vault:next-fleet",
        "port": 3412,
        "datastore": "/var/lib/serveros/next.db",
        "install_root": "/opt/serveros-fleet-next",
        "lock_root": "/run/serveros-fleet-next",
        "unit": "serveros-fleet-next-gateway.service",
        "socket_path": "/run/serveros-fleet-next/gateway.sock",
        "tool_namespace": "serveros-fleet-next",
        "skills": sorted(NEXT_SKILLS),
        "source_pin": "fleet_v1_9skill",
        "archive_sha256": pin["archive_sha256"],
        "content_sha256": pin["content_sha256"],
        "members": pin["members"],
        "fleet_instance_id": "next-instance-1",
        "route_id": "next-route-1",
        "boot_id": "next-boot-1",
    }


def _placement(record: dict[str, Any]):
    return legacy_safety.Placement(
        track=record["track"],
        backend_plugin_id=record["backend_plugin_id"],
        mcp_url=record["mcp_url"],
        listen_port=record["port"],
        socket_path=record["socket_path"],
        install_root=record["install_root"],
        state_root=record["datastore"],
        lock_root=record["lock_root"],
        unit=record["unit"],
        version=record["version"],
        artifact_digest="sha256:" + record["archive_sha256"],
    )


def preflight_480(legacy: dict[str, Any], next_plugin: dict[str, Any], **kwargs):
    return parallel.preflight(
        legacy_baseline=copy.deepcopy(legacy),
        legacy_current=copy.deepcopy(legacy),
        next_plugin=copy.deepcopy(next_plugin),
        **kwargs)


def plan_483(legacy: dict[str, Any], next_plugin: dict[str, Any]):
    return legacy_safety.install_plan(
        legacy=_placement(legacy), next=_placement(next_plugin))


def evaluate(legacy: dict[str, Any], next_plugin: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Call both producer functions on the same placement and report the facts."""
    plan = plan_483(legacy, next_plugin)
    pre = preflight_480(legacy, next_plugin, **kwargs)
    blocked_483 = plan.decision == legacy_safety.BLOCKED
    blocked_480 = pre.get("verdict") == "BLOCKED"
    both_blocked = blocked_483 and blocked_480
    both_green = not blocked_483 and not blocked_480
    if both_blocked:
        shared = "BLOCKED"
    elif both_green:
        shared = "GREEN"
    else:
        shared = "COVERAGE_DIFFERENCE"
    if both_green:
        classification = "GREEN"
    elif both_blocked:
        classification = "BLOCKED"
    elif blocked_480:
        classification = "ONLY_480"
    else:
        classification = "ONLY_483"
    return {
        "plan_483": {
            "decision": plan.decision,
            "code": plan.code,
            "collisions": [c.resource for c in plan.collisions],
            "incomplete": list(plan.incomplete),
        },
        "preflight_480": {
            "verdict": pre.get("verdict"),
            "code": pre.get("code"),
        },
        "blocked_483": blocked_483,
        "blocked_480": blocked_480,
        "both_blocked": both_blocked,
        "both_green": both_green,
        "shared": shared,
        "combined_gate": "BLOCKED" if (blocked_483 or blocked_480) else "GREEN",
        "classification": classification,
        "codes_agree": both_green or plan.code == pre.get("code"),
        "creator_calls": pre.get("creator_calls", 0),
        "provider_calls": 0,
        "network": False,
    }


class FakeLegacyGateway:
    """Legacy fake controller: stable reads and an authorized fake send."""

    def __init__(self) -> None:
        self.identity = legacy_safety.Identity(
            track="legacy",
            fleet_instance_id="legacy-instance-1",
            route_id="legacy-route-1",
            boot_id="legacy-boot-1",
            schema_version="fleet-mcp-5",
            source_version="0.1.0",
        )
        self.calls: list[str] = []

    def call(self, verb: str) -> dict[str, Any]:
        self.calls.append(verb)
        if verb in ("get_run", "run_get"):
            return {"runId": "legacy-run-1", "status": "completed",
                    "source": "LEGACY_FAKE"}
        if verb in ("get_session", "session_get"):
            return {"sessionId": "legacy-session-1", "state": "MANAGED_IDLE",
                    "source": "LEGACY_FAKE"}
        if verb in ("sessions_list",):
            return {"sessions": [{"sessionId": "legacy-session-1"}],
                    "source": "LEGACY_FAKE"}
        if verb == "send":
            return {"accepted": True, "deliveryId": "legacy-delivery-1",
                    "source": "LEGACY_FAKE"}
        raise KeyError(verb)


def legacy_survival() -> dict[str, Any]:
    """Run Next install/upgrade/fail/rollback; Legacy answers must not drift."""
    gateway = FakeLegacyGateway()
    next_plugin = next_record()
    current = {"placement": _placement(next_plugin)}
    send_before = gateway.call("send")

    def installed():
        return current["placement"]

    def upgraded():
        result = legacy_safety.upgrade(
            current["placement"], version="1.0.1",
            artifact_digest="sha256:" + "3" * 64)
        current["placement"] = result.placement
        return result

    def failed():
        # A blocked step does not mutate the current Next placement.
        return legacy_safety.upgrade(
            current["placement"], version="1.0.0",
            artifact_digest="sha256:" + "4" * 64)

    def rolled_back():
        result = legacy_safety.rollback(current["placement"])
        current["placement"] = result.placement
        return result

    report = legacy_safety.run_lifecycle(
        gateway, ("get_run", "get_session", "sessions_list"),
        (("install", installed), ("upgrade", upgraded),
         ("failed", failed), ("rollback", rolled_back)))
    send_after = gateway.call("send")
    before = legacy_safety.snapshot(
        gateway, ("get_run", "get_session", "sessions_list"))
    after = legacy_safety.snapshot(
        gateway, ("get_run", "get_session", "sessions_list"))
    return {
        "legacy_before": before,
        "legacy_after": after,
        "all_same": all(s.decision == legacy_safety.SAME for _, s in report),
        "drifted": {name: list(s.drifted) for name, s in report if s.drifted},
        "fake_send_before": send_before,
        "fake_send_after": send_after,
        "creator_calls": 0,
        "provider_calls": 0,
        "network": False,
    }


def cross_scope_matrix() -> dict[str, Any]:
    """Same runId in another track/instance/boot is not a hit."""
    legacy = legacy_record()
    next_plugin = next_record()
    facts = [
        legacy_safety.Fact(track="legacy", instance_id=legacy["fleet_instance_id"],
                           route_id=legacy["route_id"], boot_id=legacy["boot_id"],
                           kind="run", fact_id="same-run",
                           payload={"status": "completed", "source": "legacy"}),
        legacy_safety.Fact(track="next", instance_id=next_plugin["fleet_instance_id"],
                           route_id=next_plugin["route_id"], boot_id=next_plugin["boot_id"],
                           kind="run", fact_id="same-run",
                           payload={"status": "completed", "source": "next"}),
    ]
    index = legacy_safety.index_facts(facts)
    owner = legacy_safety.Identity(
        track="legacy", fleet_instance_id=legacy["fleet_instance_id"],
        route_id=legacy["route_id"], boot_id=legacy["boot_id"],
        schema_version="fleet-mcp-5", source_version="0.1.0")
    ref = legacy_safety.Ref(
        track="legacy", instance_id=legacy["fleet_instance_id"],
        boot_id=legacy["boot_id"], kind="run", fact_id="same-run")
    return {
        "hit": legacy_safety.resolve(ref, owner=owner, index=index).code,
        "cross_track": legacy_safety.resolve(
            legacy_safety.Ref(track="next", instance_id=ref.instance_id,
                              boot_id=ref.boot_id, kind="run",
                              fact_id=ref.fact_id),
            owner=owner, index=index).code,
        "foreign_instance": legacy_safety.resolve(
            legacy_safety.Ref(track="legacy", instance_id="other-instance",
                              boot_id=ref.boot_id, kind="run",
                              fact_id=ref.fact_id),
            owner=owner, index=index).code,
        "stale_boot": legacy_safety.resolve(
            legacy_safety.Ref(track="legacy", instance_id=ref.instance_id,
                              boot_id="other-boot", kind="run",
                              fact_id=ref.fact_id),
            owner=owner, index=index).code,
        "legacy_state": legacy_safety.run_state(facts, ref),
        "next_state": legacy_safety.run_state(
            facts, legacy_safety.Ref(track="next",
                                     instance_id=next_plugin["fleet_instance_id"],
                                     boot_id=next_plugin["boot_id"], kind="run",
                                     fact_id="same-run")),
        "same_object_across_tracks": legacy_safety.same_object(
            ref, legacy_safety.Ref(track="next", instance_id=ref.instance_id,
                                   boot_id=ref.boot_id, kind="run",
                                   fact_id=ref.fact_id)),
    }


def shadow_consensus() -> dict[str, Any]:
    """Both producers refuse a shadow write; permission denial calls no gateway."""
    legacy = legacy_record()
    next_plugin = next_record()
    pre = preflight_480(legacy, next_plugin,
                        shadow={"requested_writes": ["send"]})
    fake = FakeLegacyGateway()
    called_before = len(fake.calls)
    shadow_write = legacy_safety.shadow_call(
        "send", granted={"send", "get_run"}, gateway=fake)
    denied = legacy_safety.shadow_call(
        "get_run", granted=set(), gateway=fake)
    read = legacy_safety.shadow_call(
        "get_run", granted={"get_run"}, gateway=fake)
    return {
        "preflight_480_code": pre.get("code"),
        "plan_483_shadow_code": shadow_write.code,
        "shadow_write_gateway_called": shadow_write.gateway_called,
        "denied_code": denied.code,
        "denied_gateway_called": denied.gateway_called,
        "read_state": read.state,
        "read_gateway_called": read.gateway_called,
        "gateway_calls_from_refusals": len(fake.calls) - called_before - 1,
        "gateway_calls": list(fake.calls),
        "network": False,
    }


def coverage_matrix() -> dict[str, Any]:
    """Which producer models which resource; this is not a pass/fail claim."""
    return {
        "shared": list(SHARED_RESOURCES),
        "only_480": list(ONLY_480),
        "only_483": list(ONLY_483),
        "note": ("A resource only one producer models is a coverage difference; "
                 "the owner decides whether to add it. A shared resource on "
                 "which the two disagree is a real contract incompatibility."),
    }
