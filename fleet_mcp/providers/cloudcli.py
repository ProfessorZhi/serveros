"""The CloudCLI provider: a device whose sessions are served by Yale CloudCLI.

It speaks to CloudCLI's **native MCP gateway**, not to its database, its
filesystem or its REST facade. That is deliberate and it is the whole reason
this adapter is small: CloudCLI already owns session truth on its device, and a
second reader of `~/.claude/projects/*.jsonl` would be a second answer to the
same question — one that disagrees the first time session storage changes.

The gateway's own tool annotations are carried into `fleet_status` rather than
restated here; if CloudCLI marks a verb destructive, that is CloudCLI's fact and
this surface is not going to have its own opinion about it.
"""
from __future__ import annotations

import time
from typing import Any

from ..config import ProviderBinding
from ..errors import (
    BAD_REQUEST,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNREACHABLE,
    RUN_NOT_FOUND,
    SESSION_NOT_FOUND,
    FleetRefusal,
    refuse,
)
from ..identity import SessionKey
from ..mcp_http import McpHttpClient, ProviderRefusal, structured
from .base import DeviceProvider, ProviderHealth, SessionSummary, classify

# Which CloudCLI session states map onto the `state` filter `sessions_list`
# accepts. Kept as the provider's own vocabulary, passed through unchanged: a
# translation layer here would silently drop the states it did not know.
_SESSION_STATES = ("running", "idle", "resident")


def _translate(exc: ProviderRefusal, *, session: SessionKey | None = None) -> FleetRefusal:
    """Map a provider failure onto this surface's closed error set.

    The mapping is not mechanical. `SESSION_NOT_FOUND` is only produced when the
    provider genuinely answered "no such session" — a 404 from the gateway, or
    an error body that says so. A transport failure during a session read stays
    a transport failure, because "I could not ask" and "it is not there" send an
    operator to different places.
    """
    if exc.code == PROVIDER_UNREACHABLE:
        return refuse(PROVIDER_UNREACHABLE, exc.detail)
    if exc.code == PROVIDER_TIMEOUT:
        return refuse(PROVIDER_TIMEOUT, exc.detail)
    if exc.code == PROVIDER_UNAUTHORIZED:
        return refuse(PROVIDER_UNAUTHORIZED, exc.detail)
    if exc.status == 404 and session is not None:
        return refuse(
            SESSION_NOT_FOUND,
            f"{session.ref} is not known to the provider on {session.device_ref}",
        )
    return refuse(BAD_REQUEST, exc.detail)


class CloudCliProvider(DeviceProvider):
    kind = "cloudcli-mcp-http"

    def __init__(self, binding: ProviderBinding) -> None:
        self.binding = binding
        self.device_ref = binding.device_ref
        self._health: ProviderHealth | None = None
        self._health_at: float = 0.0
        self._tools: list[dict[str, Any]] | None = None

    # -- plumbing ----------------------------------------------------------

    def _client(self) -> McpHttpClient:
        return McpHttpClient(
            self.binding.endpoint,
            self.binding.credential(),
            connect_timeout=self.binding.connect_timeout,
            read_timeout=self.binding.read_timeout,
            label=f"CloudCLI on {self.device_ref}",
        )

    def _call(self, tool: str, arguments: dict[str, Any], *, session: SessionKey | None = None):
        client = self._client()
        try:
            result = client.tools_call(tool, arguments)
        except ProviderRefusal as exc:
            raise _translate(exc, session=session) from None
        return structured(result)

    # -- health ------------------------------------------------------------

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        client = self._client()
        try:
            info = client.initialize()
            tools = client.tools_list()
        except ProviderRefusal as exc:
            self._health = ProviderHealth(
                device_ref=self.device_ref,
                kind=self.kind,
                reachable=exc.code not in (PROVIDER_UNREACHABLE, PROVIDER_TIMEOUT),
                authorized=exc.code != PROVIDER_UNAUTHORIZED,
                detail=exc.detail,
                error_code=exc.code,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
            )
            self._health_at = time.monotonic()
            return self._health
        server = (info or {}).get("serverInfo") or {}
        self._tools = tools
        self._health = ProviderHealth(
            device_ref=self.device_ref,
            kind=self.kind,
            reachable=True,
            authorized=True,
            detail=f"gateway answered; {len(tools)} tools declared",
            server_name=server.get("name"),
            server_version=server.get("version"),
            tool_count=len(tools),
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        )
        self._health_at = time.monotonic()
        return self._health

    def tool_annotations(self) -> dict[str, dict[str, Any]]:
        """The gateway's own read/write annotations, as reported.

        Returned rather than interpreted: this surface does not have a second
        opinion about which CloudCLI verbs mutate.
        """
        if self._tools is None:
            try:
                self._tools = self._client().tools_list()
            except ProviderRefusal:
                return {}
        return {t["name"]: (t.get("annotations") or {}) for t in self._tools}

    # -- sessions ----------------------------------------------------------

    def list_sessions(
        self, *, limit: int, project: str | None = None, state: str | None = None
    ) -> list[SessionSummary]:
        arguments: dict[str, Any] = {"limit": int(limit)}
        if project:
            arguments["project"] = project
        if state:
            if state not in _SESSION_STATES:
                raise refuse(
                    BAD_REQUEST,
                    f"state must be one of {list(_SESSION_STATES)}; got {state!r}",
                )
            arguments["state"] = state
        payload = self._call("sessions_list", arguments)
        rows = payload.get("sessions") or []
        summaries: list[SessionSummary] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            native = row.get("id")
            if not native:
                continue
            verdict, running, resident, evidence = classify(row)
            summaries.append(
                SessionSummary(
                    key=SessionKey.compose(self.device_ref, str(row.get("provider") or "claude"), str(native)),
                    title=str(row.get("title") or ""),
                    classification=verdict,
                    running=running,
                    resident_host=resident,
                    host_state=row.get("hostState"),
                    lifecycle_mode=row.get("lifecycleMode"),
                    project_id=row.get("projectId"),
                    last_activity=row.get("lastActivity"),
                    evidence=evidence,
                )
            )
        return summaries

    def get_session(self, key: SessionKey) -> dict[str, Any]:
        payload = self._call("session_get", {"session": key.native_session_id}, session=key)
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return self._normalize_detail(key, payload)

    def _normalize_detail(self, key: SessionKey, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep the provider's own fields, but attach the identity and verdict.

        The verdict is read from the session object, which is where the fields
        it is computed from actually live. `session_get` nests the session under
        `session` and puts the host beside it; `sessions_list` returns the same
        fields flat. Classifying the envelope instead would find no `hostState`
        at the top level and quietly fall through to MANAGED_IDLE, so a session
        with a lingering host would be RESIDENT_HOST in a listing and
        MANAGED_IDLE when fetched — two answers from one surface, about one
        session, with nothing in either to say which was wrong.
        """
        source = payload.get("session")
        if not isinstance(source, dict):
            source = payload
        verdict, running, resident, evidence = classify(source)
        run = payload.get("run") or payload.get("currentRun") or source.get("run")
        detail = dict(payload)
        detail.update(
            {
                **key.as_dict(),
                "classification": verdict,
                "running": running,
                "resident_host": resident,
                "current_run": run,
                "evidence": {**evidence, "detail_keys": sorted(payload.keys())},
            }
        )
        if isinstance(run, dict):
            detail["current_run"] = {**run, "run_id": run.get("id") or run.get("runId")}
        return detail

    def read_session(
        self,
        key: SessionKey,
        *,
        mode: str = "latest",
        limit: int = 20,
        around_id: str | None = None,
        before: int | None = None,
        after: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {"session": key.native_session_id, "mode": mode, "limit": int(limit)}
        for name, value in (("aroundId", around_id), ("cursor", cursor)):
            if value:
                arguments[name] = value
        for name, value in (("before", before), ("after", after)):
            if value is not None:
                arguments[name] = int(value)
        payload = self._call("session_read", arguments, session=key)
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return payload

    # -- control -----------------------------------------------------------

    def send(
        self, key: SessionKey, message: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        # CloudCLI has no idempotency key of its own; the surface's replay
        # guard runs above this line, in `fleet.py`. Passing the key through
        # here would be pretending the provider honours it.
        payload = self._call("session_send", {"session": key.native_session_id, "message": message}, session=key)
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return payload

    def get_run(
        self, key: SessionKey, run_id: str, *, wait: bool = False, wait_seconds: float = 0.0
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "runId": run_id,
            "session": key.native_session_id,
            "wait": bool(wait),
        }
        if wait and wait_seconds:
            arguments["waitSeconds"] = float(wait_seconds)
        payload = self._call("run_get", arguments, session=key)
        if not payload:
            raise refuse(RUN_NOT_FOUND, f"run {run_id!r} is not known for {key.ref}")
        return payload
