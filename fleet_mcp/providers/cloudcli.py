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
from datetime import datetime, timezone
from typing import Any

from ..config import ProviderBinding
from .. import cloudcli_native
from ..errors import (
    BAD_SESSION_REF,
    BAD_REQUEST,
    INTERNAL,
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

#: The native identities a reply may carry, kept apart from one another.
#:
#: `deliveryId` names the REQUEST, `runId` names the work it produced,
#: `queuedMessageUuid` names a message still waiting, `blockedBy` names another
#: run this one waits behind, and `idKind` says which of the first two the device
#: answered by. Collapsing them — or turning any of them into `accepted: true` —
#: is how "the device took the message" comes to read as "the task ran".
RECEIPT_NATIVE_FIELDS = (
    "deliveryId", "runId", "queued", "queuedMessageUuid", "blockedBy",
    "idKind", "bootId",
)

#: The two identifier namespaces `run_get` accepts, and the argument each is sent
#: as. A caller names the kind; this layer never infers it from the string, and
#: never substitutes one for the other.
ID_KINDS = {"run": "runId", "delivery": "deliveryId"}

_COMPLETED_STATES = ("completed", "settled")
_FAILED_STATES = ("failed", "failure", "error")


def native_receipt(payload: Any) -> dict[str, Any]:
    """The native identities in `payload`, verbatim, and nothing else.

    Deliberately absent: any `accepted` key. The provider never synthesized one,
    and a receipt that carried one would make a local word look like a device
    acknowledgement.
    """
    if not isinstance(payload, dict):
        return {}
    return {name: payload[name] for name in RECEIPT_NATIVE_FIELDS if name in payload}


def receipt_truth(payload: Any) -> dict[str, Any]:
    """What the device's reply actually establishes, without upgrading it.

    The ladder is ordered so that the strongest negative wins:

      FAILED     an explicit failure flag/state;
      UNKNOWN    a refusal with no execution, or a `completed` that carries no
                 readable native activity — #394's shape, where a run reports
                 completion seconds after the send with an empty transcript.
                 "No evidence of execution" is never reported as DONE;
      REJECTED   the device said it did not accept;
      QUEUED     a queue reference exists (the work has not started);
      STARTED    a `runId` exists (the run was admitted; still not finished);
      ACCEPTED_NO_RUN  only a `sessionId` (a create with no first message);
      UNKNOWN    anything else — including `RUN_NOT_FOUND`, which says the id is
                 not readable here and not that the work finished.
    """
    if not isinstance(payload, dict):
        return {"acceptance": "UNKNOWN", "native": {}}
    native = native_receipt(payload)
    status = str(payload.get("status") or "").lower()
    if payload.get("failed") is True or status in _FAILED_STATES:
        acceptance = "FAILED"
    elif payload.get("code") == "RUN_NOT_FOUND":
        acceptance = "UNKNOWN"
    elif (status in _COMPLETED_STATES
          and "lastAssistantMessage" in payload
          and payload.get("lastAssistantMessage") in (None, "")):
        acceptance = "UNKNOWN"
    elif payload.get("accepted") is False:
        acceptance = "REJECTED"
    elif payload.get("queued") is True or payload.get("queuedMessageUuid"):
        acceptance = "QUEUED"
    elif payload.get("runId"):
        acceptance = "STARTED"
    elif payload.get("sessionId"):
        acceptance = "ACCEPTED_NO_RUN"
    else:
        acceptance = "UNKNOWN"
    return {"acceptance": acceptance, "native": native}


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
        self._tools_sampled_at: str | None = None
        self._coverage: dict[str, Any] = {}

    def native_coverage(self) -> dict[str, Any]:
        """The last live `tools/list` compared with the recorded 22.

        Read by `fleet_status` so a device that runs a different CloudCLI is
        visible as drift rather than as a verb that fails only when called.
        """
        return dict(self._coverage)

    def capability_snapshot(self) -> dict[str, Any]:
        """A READ-ONLY snapshot of what THIS device exposes, from its live list.

        Calls `health()` (the one place the live `tools/list` is fetched) and
        shapes the answer with `cloudcli_native.capability_snapshot`, so a verb
        this device does not declare is `UNSUPPORTED_BY_DEVICE` rather than
        assumed present. Nothing here reads a credential or mutates the device.
        """
        health = self.health()
        if health.error_code or self._tools is None:
            return cloudcli_native.capability_snapshot(
                device_ref=self.device_ref, source=self.kind,
                reason=health.error_code or "the device's tool list was not read")
        observed = {}
        for row in self._tools:
            if isinstance(row, dict) and isinstance(row.get("name"), str):
                observed[row["name"]] = {"annotations": row.get("annotations"),
                                         "schema": row.get("inputSchema"),
                                         "_meta": row.get("_meta")}
        return cloudcli_native.capability_snapshot(
            device_ref=self.device_ref, observed=observed, source=self.kind,
            version=health.server_version,
            sampled_at=self._tools_sampled_at)

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

    def create_project(self, *, root: str, name: str, project_path: str,
                       display_name: str | None = None):
        """An explicit extension; never REST auth or arbitrary native passthrough."""
        client = self._client()
        try:
            advertised = client.tools_list()
            tool = next((t for t in advertised if t.get("name") == "project_create"), {})
            fresh = tool.get("inputSchema", {}).get("properties", {}).get("requireFresh", {})
            if fresh.get("type") != "boolean":
                raise refuse("CAPABILITY_UNSUPPORTED",
                             "gateway does not advertise target-local fresh project creation")
            arguments = {"projectPath": project_path, "requireFresh": True}
            if display_name is not None:
                arguments["customName"] = display_name
            return structured(client.tools_call("project_create", arguments))
        except ProviderRefusal as exc:
            raise _translate(exc) from None

    def delete_project(self, *, project_id: str, force: bool):
        """Explicit extension: archive (default) or remove one project's rows.

        Checked against the device's OWN advertised schema before the call, for
        the reason `create_project` does it: a CloudCLI that predates
        `project_delete` must answer CAPABILITY_UNSUPPORTED here rather than
        have this surface forward a name the device never registered.
        """
        client = self._client()
        try:
            advertised = client.tools_list()
            tool = next((t for t in advertised if t.get("name") == "project_delete"), {})
            schema = tool.get("inputSchema") or {}
            required = schema.get("required") or []
            properties = schema.get("properties") or {}
            if "projectId" not in required or properties.get("projectId", {}).get("type") != "string":
                raise refuse("CAPABILITY_UNSUPPORTED",
                             "gateway does not advertise project deletion by id")
            return structured(client.tools_call(
                "project_delete", {"projectId": project_id, "force": bool(force)}))
        except ProviderRefusal as exc:
            raise _translate(exc) from None

    def native_call(self, tool: str, arguments: dict[str, Any], *,
                    session_ref: str | None = None) -> dict[str, Any]:
        """Call ONE native tool by a name this build records. Never a proxy.

        `_call` can forward any name the transport is handed; THIS method is the
        one place that decides which names are allowed to reach it, so the
        decision is in one place rather than at every call site. A name absent
        from `cloudcli_native`'s closed set is refused before a socket opens.

        Arguments are passed through UNCHANGED — `null` and omitted stay
        distinct, because the native tool's own schema is the authority on what
        it accepts, and re-encoding them here would be a second, drifting copy of
        that authority.
        """
        if cloudcli_native.NATIVE_BY_NAME.get(tool) is None:
            raise refuse(
                BAD_REQUEST,
                f"{tool!r} is not a CloudCLI native tool this build mirrors; the "
                f"mirror forwards only names it recorded.",
            )
        if tool == "run_get":
            problems = cloudcli_native.validate_arguments(arguments, cloudcli_native.input_schema(tool))
            if problems:
                raise refuse(BAD_REQUEST, "; ".join(problems))
            if "deliveryId" in arguments:
                session = arguments.get("session")
                if not isinstance(session, str) or not session.strip():
                    raise refuse(BAD_REQUEST, "delivery lookup requires its native session")
                key = (SessionKey.parse(session_ref) if session_ref else
                       SessionKey.compose(self.device_ref, self.binding.runtime, session))
                if key.device_ref != self.device_ref or key.native_session_id != session:
                    raise refuse(BAD_SESSION_REF, "delivery target contradicts its requested session")
                return cloudcli_native.validate_delivery(self._delivery_call(arguments), key,
                    arguments["deliveryId"], family=self.binding.family)
        # `session_ref` is accepted for parity with the node route and is not
        # used here: for a `cloudcli-mcp-http` device the ownership check runs
        # one layer up, against THIS device's own `approvals_list` register, so
        # the proof has already been spent by the time this is called.
        return self._call(tool, dict(arguments or {}))

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        client = self._client()
        try:
            info = client.initialize()
            tools = client.tools_list()
        except ProviderRefusal as exc:
            self._tools = None
            self._tools_sampled_at = None
            self._coverage = {}
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
        self._tools_sampled_at = datetime.now(timezone.utc).isoformat()
        # HIGH-7(f): the device's LIVE tool list is compared with the recorded
        # set here, so a device running a different CloudCLI is a fact in
        # `fleet_status` rather than a surprise at call time.
        self._coverage = cloudcli_native.coverage(
            [t.get("name") for t in tools if isinstance(t, dict)],
            {t.get("name"): (t.get("annotations") or {})
             for t in tools if isinstance(t, dict)})
        drift = ("" if self._coverage.get("agrees") else
                 f"; TOOL LIST DRIFT vs {cloudcli_native.NATIVE_TOOL_COUNT} "
                 f"recorded — missing={self._coverage.get('missing')} "
                 f"extra={self._coverage.get('extra')}")
        self._health = ProviderHealth(
            device_ref=self.device_ref,
            kind=self.kind,
            reachable=True,
            authorized=True,
            detail=f"gateway answered; {len(tools)} tools declared{drift}",
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
            family = row.get("provider")
            namespace = str(family or "claude")
            if self.binding.runtime != "claude":
                if family is not None and family != self.binding.family:
                    raise refuse(BAD_SESSION_REF, "listed native family contradicts its binding")
                namespace = self.binding.runtime
            elif self.binding.multiplex_native_families and namespace not in ("claude", "codex"):
                raise refuse(BAD_SESSION_REF, "listed native family is not declared by this gateway")
            summaries.append(
                SessionSummary(
                    key=SessionKey.compose(self.device_ref, namespace, str(native)),
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
        if "session" not in payload:
            # Preserve the flat native identity before attaching a routing key.
            # In particular, the legacy connector's synthesized Claude key
            # must not erase evidence that the native session is Codex.
            detail["session"] = dict(source)
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
        # Additive: the native identities stay exactly where the device put them,
        # and `receipt` states what they establish. No `accepted` key is added —
        # the surface above may still synthesize one (out of this file's scope),
        # but this layer will not make a device acknowledgement out of nothing.
        return {**payload, "receipt": receipt_truth(payload)}

    def interrupt(self, key: SessionKey) -> dict[str, Any]:
        """Call CloudCLI's `session_interrupt` with its one argument.

        The argument name is `session` and the value is the *native* id, because
        that is what the gateway's own `readSessionInterruptInput` reads — it
        validates exactly one field and throws `INVALID_ARGUMENT` on an empty
        one. Nothing else is sent: not the full Fleet ref, not a run id, not the
        caller's name. Passing the ref would fail validation, and adding fields
        the tool does not declare would be this surface deciding what the
        gateway's schema should be.

        The answer is returned as the provider shaped it. When the session has no
        run in flight CloudCLI answers `{"aborted": false, "message": "This
        session currently has no run in flight, so there is no run to abort."}`
        — a success carrying a fact — and that sentence is forwarded, not
        replaced. A refusal (an unknown session, an unreachable gateway) arrives
        as a `McpToolError` and is translated by `_translate`, so "I could not
        ask" keeps sending the operator somewhere different from "it is not
        there".
        """
        return self._call("session_interrupt", {"session": key.native_session_id}, session=key)

    def get_run(
        self, key: SessionKey, run_id: str, *, wait: bool = False,
        wait_seconds: float = 0.0, id_kind: str = "run",
    ) -> dict[str, Any]:
        """Read one run by a NAMED identifier namespace.

        `run_id` is the value; `id_kind` says which namespace it belongs to. The
        device accepts `runId` or `deliveryId` and answers an `idKind` naming the
        one it used. Neither this method nor the caller may infer the kind from
        the string: a run id and a delivery id are different identifiers, and
        silently trying one as the other would make "this id is unknown" and
        "this id names a different thing" the same answer.

        A reply whose `idKind` contradicts the requested kind, or names a kind
        this surface cannot place, is refused rather than returned. That is the
        whole point of the field: the device tells us which namespace it answered
        in, and a caller that ignores it would read the wrong object.
        """
        if id_kind not in ID_KINDS:
            raise refuse(
                BAD_REQUEST,
                f"`id_kind` must be one of {sorted(ID_KINDS)}; {id_kind!r} is "
                f"refused rather than guessed, because a run id and a delivery "
                f"id are different identifiers.",
            )
        field = ID_KINDS[id_kind]
        if id_kind == "delivery":
            arguments: dict[str, Any] = {"deliveryId": run_id,
                                         "session": key.native_session_id}
            if wait and wait_seconds:
                arguments["waitSeconds"] = float(wait_seconds)
            problems = cloudcli_native.validate_arguments(
                arguments, cloudcli_native.input_schema("run_get"))
            if problems:
                raise refuse(BAD_REQUEST, "; ".join(problems))
            payload = cloudcli_native.validate_delivery(
                self._delivery_call(arguments), key, run_id,
                family=self.binding.family)
        else:
            arguments = {"runId": run_id, "session": key.native_session_id}
            if wait and wait_seconds:
                arguments["waitSeconds"] = float(wait_seconds)
            payload = self._call("run_get", arguments, session=key)
        if not payload:
            raise refuse(RUN_NOT_FOUND, f"{field} {run_id!r} is not known for {key.ref}")
        answered = payload.get("idKind")
        if answered is not None:
            if answered not in ID_KINDS:
                raise refuse(
                    BAD_SESSION_REF,
                    f"the device answered an identifier kind this surface cannot "
                    f"place ({answered!r}); refusing rather than assuming which "
                    f"object it named.",
                )
            if answered != id_kind:
                raise refuse(
                    BAD_SESSION_REF,
                    f"the call asked by {field} and the device answered by "
                    f"{ID_KINDS[answered]} — two different identifiers, and "
                    f"neither is a substitute for the other.",
                )
        return payload

    def _delivery_call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Measure this gateway's support; a recorded Tokyo source is not its capability."""
        try:
            advertised = self._client().tools_list()
        except ProviderRefusal as exc:
            raise _translate(exc) from None
        schema = next((row.get("inputSchema") for row in advertised
                       if isinstance(row, dict) and row.get("name") == "run_get"), None)
        if (not isinstance(schema, dict)
                or (schema.get("properties") or {}).get("deliveryId", {}).get("type") != "string"
                or cloudcli_native.validate_arguments(arguments, schema)):
            raise refuse("CAPABILITY_UNSUPPORTED", "gateway does not advertise delivery-addressed run_get")
        return self._call("run_get", arguments)

    def get_delivery(self, key: SessionKey, delivery_id: str) -> dict[str, Any]:
        if key.device_ref != self.device_ref:
            raise refuse(BAD_SESSION_REF, "delivery lookup addresses another device")
        arguments = {"deliveryId": delivery_id, "session": key.native_session_id}
        problems = cloudcli_native.validate_arguments(arguments, cloudcli_native.input_schema("run_get"))
        if problems:
            raise refuse(BAD_REQUEST, "; ".join(problems))
        family = self.binding.family if self.binding.runtime == key.provider else None
        return cloudcli_native.validate_delivery(self._delivery_call(arguments), key, delivery_id, family=family)

    def create(
        self,
        *,
        project: str,
        message: str | None = None,
        provider: str = "claude",
        model: str | None = None,
        lifecycle_mode: str | None = None,
        permission_mode: str | None = None,
    ) -> dict[str, Any]:
        """Call the gateway's `session_create` with the gateway's own argument names.

        The shape is CloudCLI 1.39's `SESSION_CREATE_INPUT_SCHEMA`, carried
        unchanged: `project` (required, already an id — the gateway's target gate
        resolves a name to one before this line), and the optional `message`,
        `provider`, `model`, `lifecycleMode`, `permissionMode`.

        An omitted field is *absent*, never `null`: the gateway resolves what is
        left out against the user's own remembered session defaults, and sending
        an explicit null would be an argument where the caller meant a gap. That
        is also why there is no default value written here for anything but
        `provider`, which the gateway itself defaults to `claude`.

        No idempotency key is forwarded. CloudCLI has no such concept — a second
        `session_create` is a second session, and this adapter will not imply a
        guarantee the far side cannot make. The surface's replay guard runs
        above this line, in `fleet.py`.
        """
        arguments: dict[str, Any] = {"project": project, "provider": provider}
        for name, value in (
            ("message", message),
            ("model", model),
            ("lifecycleMode", lifecycle_mode),
            ("permissionMode", permission_mode),
        ):
            if value is not None:
                arguments[name] = value
        payload = self._call("session_create", arguments)
        if not (payload.get("sessionId") or payload.get("session_id")):
            raise refuse(
                INTERNAL,
                f"the gateway answered session_create without a sessionId "
                f"(keys: {sorted(payload)}). A create that reports success has to "
                f"name the session it made.",
            )
        return payload
