"""A read-only facts client over the hub's own FleetService.

The Agent Monitor's classifier (`fleet_next_agent_monitor.sources`) reads a run
through a client with one method, `read_run`. This is that client, backed by
`FleetService.get_run`, so the hub watcher classifies a run with exactly the
monitor's standard of evidence: no second rule, no second reading of "done".

Only reads are reachable from here. The service is called with a principal that
carries `fleet:read` and nothing else.
"""
from __future__ import annotations

from typing import Any, Mapping

from fleet_mcp.errors import (
    FleetRefusal, PROVIDER_TIMEOUT, PROVIDER_UNREACHABLE, RUN_NOT_FOUND, RUN_UNSETTLED,
)
from fleet_mcp.identity import SessionKey
from fleet_next_agent_monitor.sources import RemoteRefused, RouteOffline, RunNotFound


class FleetFactsClient:
    """`LegacyReadOnlyClient` for the hub: reads runs through FleetService."""

    provider = "fleet"
    boot_id = None

    def __init__(self, service: Any, principal: Any, *, instance_id: str,
                 route_id: str = "fleet-service") -> None:
        self._service = service
        self._principal = principal
        self.fleet_instance_id = instance_id
        self.route_id = route_id

    def read_run(self, *, device_ref: str, session_ref: str,
                 run_id: str | None = None,
                 delivery_id: str | None = None) -> Mapping[str, Any]:
        if run_id is None:
            raise RunNotFound()
        key = SessionKey.parse(session_ref)
        try:
            payload = self._service.get_run(self._principal, key, run_id)
        except FleetRefusal as exc:
            if exc.code == RUN_UNSETTLED:
                return {"status": "running", "runId": run_id}
            if exc.code == RUN_NOT_FOUND:
                raise RunNotFound() from None
            if exc.code in (PROVIDER_UNREACHABLE, PROVIDER_TIMEOUT):
                raise RouteOffline() from None
            raise RemoteRefused(exc.code) from None
        if not isinstance(payload, Mapping):
            return payload
        fact = dict(payload)
        # The native run answer's field name for its state is not yet observed
        # on a live CloudCLI (UNVERIFIED until the Tokyo bring-up). Both names
        # are read; neither is invented when both are absent.
        if "status" not in fact and isinstance(fact.get("state"), str):
            fact["status"] = fact["state"]
        # The service appends routing keys of its own; they are not the run's
        # own identity claims and must not be compared as such.
        for routing in ("device_ref", "native_session_id", "session_ref", "run_id", "provider"):
            fact.pop(routing, None)
        # A native answer names its session by the provider's own id; the
        # monitor compares against the full reference. The same session is
        # restated in the monitor's form; a different one is left as it came,
        # so the monitor still refuses it as an identity mismatch.
        native_session = fact.get("sessionId")
        if native_session == key.native_session_id:
            fact.pop("sessionId")
            fact["sessionRef"] = key.ref
        fact["instanceId"] = self.fleet_instance_id
        return fact

    def read_ci(self, *, source_sha: str, project: str | None = None) -> Mapping[str, Any]:
        raise RemoteRefused("CI_NOT_READ_BY_THE_HUB_WATCHER")
