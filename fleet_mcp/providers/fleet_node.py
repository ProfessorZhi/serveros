"""The Fleet node provider: a device reached through an outbound node connector.

A Windows workstation behind a corporate network cannot accept an inbound
connection, and should not have to. The connector on that machine dials out to
this surface — an SSH reverse tunnel it owns and can drop — and serves the
closed API in `node_protocol.py` on the far end.

Why this is not just "CloudCLI over a tunnel"
---------------------------------------------
It could have been: forward the CloudCLI port and point a `CloudCliProvider` at
it. That was rejected because it would put the device's CloudCLI personal
access token on the Fleet host. The token is a credential for *everything*
CloudCLI can do on that machine, and the machine is a corporate workstation.
Instead the token stays on the workstation, and what crosses the tunnel is a
narrow, signed, allow-listed set of session operations.

Consequences that are the point of the design:

*   This surface holds no credential that can drive the Windows machine. It
    holds a secret that can ask eight named questions and nothing else.
*   Withdrawing access is one file on one machine, and works whether or not the
    other machine is up.
*   The connector being down is `PROVIDER_UNREACHABLE` for that device only.
    Every other device is unaffected, because nothing about this path is
    shared.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .. import cloudcli_native
from ..config import ProviderBinding
from ..core_lite_source import CoreLiteFetch, CoreLiteRoute
from ..disclosure import remote_summary
from ..errors import (
    BAD_REQUEST,
    FleetRefusal,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNREACHABLE,
    SESSION_NOT_FOUND,
    WRITE_NOT_GRANTED,
    refuse,
)
from ..identity import SessionKey
from ..runtime_identity import BASE_RUNTIME

from ..node_protocol import (PROTOCOL, PROTOCOL_HEADER, CORE_SIGNATURE_HEADER,
                             NONCE_HEADER, body_digest, sign, split_path_and_query,
                             verify_core_response)
from .base import DeviceProvider, ProviderHealth, SessionSummary, summary_from_dict


#: The largest Core-Lite document this surface will read off the node channel.
#: The producer's shape is a fixed set of observations with bounded rows; a body
#: past this is not that shape, and reading it anyway would let a device decide
#: how much memory Fleet spends answering a read.
CORE_LITE_MAX_BYTES = 512 * 1024


class NodeCoreLiteSource:
    """The node channel as a `CoreLiteStatusSource`. One method, by design.

    Attribution is grounded in the CHANNEL, not in the document. The request is
    signed with a secret bound to one device and — on the v2 protocol — one
    runtime; the connector answers with its own Core status, and the document's
    `node` field is then checked against the same device by `readback`. So
    `authenticated=True` here means "this surface holds a verified route to that
    device", never "the document said so".

    `route.device_ref` is the PROVIDER'S OWN device, not the device the caller
    asked about. Naming the caller's argument here would make `admit_core_lite`'s
    device check compare a value with itself and pass for every input — a guard
    that cannot fire is not a guard. This way, asking provider A about device B
    is refused as `DEVICE_MISMATCH` instead of published as B's reading.
    """

    def __init__(self, provider: "FleetNodeProvider") -> None:
        self._provider = provider

    def fetch(self, device_ref: str) -> CoreLiteFetch:
        try:
            document = self._provider.core_status()
        except FleetRefusal as exc:
            # "No secret" is not a broken source, it is an absent credential, and
            # the two send an operator to different files. Reported as the
            # unauthenticated route it is, so the summary says UNAUTHENTICATED
            # rather than blaming the code.
            if exc.code == PROVIDER_UNAUTHORIZED:
                return CoreLiteFetch(
                    document=None,
                    route=CoreLiteRoute(device_ref=self._provider.device_ref,
                                        authenticated=False))
            raise
        return CoreLiteFetch(
            document=document,
            route=CoreLiteRoute(device_ref=self._provider.device_ref,
                                authenticated=True))


class FleetNodeProvider(DeviceProvider):
    kind = "fleet-node"

    def __init__(self, binding: ProviderBinding) -> None:
        self.binding = binding
        self.device_ref = binding.device_ref

    # -- transport ---------------------------------------------------------

    def _signed_runtime(self) -> str | None:
        """The runtime to sign under, or `None` for v1.

        The device's own runtime signs EXACTLY as it always has — v1, no runtime
        in the message — because every connector in the field speaks that
        version and a machine nobody has touched must keep answering. A second
        runtime signs v2, which puts the runtime inside the signature so a
        secret belonging to one runtime cannot produce a request that verifies
        for another.
        """
        runtime = getattr(self.binding, "runtime", BASE_RUNTIME)
        return None if runtime == BASE_RUNTIME else runtime

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                 *, max_bytes: int | None = None, nonce: str | None = None,
                 capture: dict[str, Any] | None = None) -> dict[str, Any]:
        secret = self.binding.credential()
        if not secret:
            raise refuse(
                PROVIDER_UNAUTHORIZED,
                f"{self.device_ref} has no node secret at "
                f"{self.binding.credential_anchor}. The connector is not the "
                f"problem; this runtime cannot prove itself to it.",
            )
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        # The query string is not signed — see `node_protocol.split_path_and_query`.
        # Signing the raw target would make a reordered filter a signature
        # failure, which is a refusal about nothing the caller was authorized to do.
        sign_path, _ = split_path_and_query(path)
        headers = sign(secret, method=method, path=sign_path, body=body,
                       device_ref=self.device_ref, nonce=nonce,
                       runtime=self._signed_runtime())
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        url = self.binding.endpoint.rstrip("/") + path
        request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.binding.read_timeout) as response:
                if max_bytes is None:
                    blob = response.read()
                    raw = blob.decode("utf-8", "replace")
                else:
                    # One byte past the cap is read on purpose: it is what tells
                    # a body that fits from one that was truncated, and the two
                    # must not look the same. A capped read that then refuses is
                    # a refusal about the answer's SIZE, which the connector can
                    # act on; a silently truncated JSON body would instead fail
                    # to parse and be reported as the connector answering badly.
                    payload_bytes = response.read(max_bytes + 1)
                    if len(payload_bytes) > max_bytes:
                        raise refuse(
                            BAD_REQUEST,
                            f"the {self.device_ref} connector answered with more "
                            f"than {max_bytes} bytes; a Core-Lite document is a "
                            f"bounded shape and a body this size is not one",
                        )
                    blob = payload_bytes
                    raw = payload_bytes.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass
            raise self._refusal_from_body(exc.code, raw) from None
        except (socket.timeout, TimeoutError):
            raise refuse(
                PROVIDER_TIMEOUT,
                f"the {self.device_ref} connector accepted the connection and did "
                f"not answer within {self.binding.read_timeout:.0f}s",
            ) from None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            raise refuse(
                PROVIDER_UNREACHABLE,
                f"no route to the {self.device_ref} connector at "
                f"{self.binding.endpoint} ({type(exc).__name__}). The device may "
                f"be up with its connector down — this is a statement about the "
                f"tunnel, not about the machine.",
            ) from None
        if capture is not None:
            # The RAW bytes, because the response signature is over what the
            # connector actually sent — re-serialising the parsed object would
            # sign a different byte string and fail for no real reason.
            capture["body"] = blob
            capture["headers"] = {k.lower(): v for k, v in response.headers.items()}
        try:
            decoded = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            # MEDIUM-11: the connector's bytes are measured, never relayed — the
            # same `disclosure.remote_summary` the HTTP provider uses, so the
            # node path is not a wider egress than the MCP path (#157).
            raise refuse(BAD_REQUEST, remote_summary(
                f"the {self.device_ref} connector", method=method, body=raw)) from None
        if isinstance(decoded, dict) and "error" in decoded:
            err = decoded["error"] or {}
            raise self._map_code(err.get("code"), err.get("detail") or "")
        return decoded if isinstance(decoded, dict) else {}

    def core_status(self) -> dict[str, Any]:
        """Ask this device for its Core-Lite status — and check that IT answered.

        The route is a NAME, not a parameter: this method takes no path, no
        command and no options, so nothing a caller sends can decide what the
        device runs. The producer is chosen by the CONNECTOR from its own
        configuration — the property that makes a monitoring read safe to expose.

        **The response is verified, and that is not a formality.** Signing the
        request proves who WE are; it says nothing about who answered. Any
        process listening on that URL can reply 200 with a plausible document,
        and a wrong `endpoint` in a binding is a configuration slip nobody would
        notice. So the connector signs its Core answer over a canonical string
        that includes THE NONCE THIS CALL GENERATED, and this method refuses the
        answer unless that signature verifies: a replayed earlier answer is
        signed over a different nonce, and a stranger never had the secret at
        all. Holding the secret and talking to loopback is not peer
        authentication; this is.
        """
        import secrets

        nonce = secrets.token_hex(16)
        capture: dict[str, Any] = {}
        document = self._request("GET", "/v1/core/status", max_bytes=CORE_LITE_MAX_BYTES,
                                 nonce=nonce, capture=capture)
        secret = self.binding.credential()
        if not secret:
            raise refuse(PROVIDER_UNAUTHORIZED, "no node secret is configured")
        if not verify_core_response(
                secret,
                device_ref=self.device_ref,
                runtime=getattr(self.binding, "runtime", BASE_RUNTIME),
                request_nonce=nonce,
                body=capture.get("body") or b"",
                signature=capture.get("headers", {}).get(CORE_SIGNATURE_HEADER.lower())):
            # Refused as an authorization failure rather than as a bad document:
            # the document may be perfectly well formed, and that is the point —
            # what is missing is any evidence that THIS machine produced it.
            raise refuse(
                PROVIDER_UNAUTHORIZED,
                f"the answer from {self.binding.endpoint} carries no valid Core "
                f"response signature bound to this request's nonce. A body that "
                f"parses is not evidence that the device sent it.")
        return document

    def core_lite_source(self) -> "NodeCoreLiteSource | None":
        """This device as a `CoreLiteStatusSource`, or `None` when it cannot be.

        A device reached over the node channel has a route whose authenticity is
        established by the signature, so it gets a source. A device reached any
        other way has no such route yet and returns `None` — which
        `admit_core_lite` reports as `NOT_CONFIGURED`. That is the honest answer
        for the CloudCLI path today: this surface holds a PAT that can drive a
        session, and a credential that can drive a session is not evidence of
        what the machine's Core measured.
        """
        return NodeCoreLiteSource(self)

    def native_call(self, tool: str, arguments: dict[str, Any], *,
                    session_ref: str | None = None) -> dict[str, Any]:
        """Call ONE mirrored native tool on this node, over the signed route.

        The path is fixed (`/v1/native/call`) and the tool — and, for an
        approval, the session PROOF — are named in the SIGNED body, so there is
        no URL, path segment, shell or redirect for a caller to shape, and the
        device refuses any name outside the recorded CloudCLI set.

        `session_ref` is the Fleet address the HOST already verified the floor
        over. It rides in the signed body as a PROOF the device can re-check
        against its own register: the device does not take the caller's word for
        what an approval request belongs to.
        """
        if cloudcli_native.NATIVE_BY_NAME.get(tool) is None:
            raise refuse(
                BAD_REQUEST,
                f"{tool!r} is not a CloudCLI native tool this build mirrors; the "
                f"node route refuses names it did not record.",
            )
        payload: dict[str, Any] = {"tool": tool, "arguments": dict(arguments or {})}
        if session_ref:
            payload["session_ref"] = session_ref
        return self._request("POST", "/v1/native/call", payload)

    def capability_snapshot(self) -> dict[str, Any]:
        """The node route cannot enumerate verbs: an honest UNKNOWN.

        There is no `tools/list` on the signed node route — the connector's
        closed table is a set of ROUTES, not a CloudCLI verb inventory — so this
        device's 22-verb coverage is not observable from here. It is reported
        UNKNOWN with that reason rather than assumed complete, and rather than
        reached by a credential this surface does not hold.
        """
        return cloudcli_native.capability_snapshot(
            device_ref=self.device_ref, source=self.kind,
            reason=("the signed node route exposes no tools/list; this device's "
                    "verb coverage is verified per call, not enumerated here"))

    def _refusal_from_body(self, status: int, raw: str):
        try:
            parsed = json.loads(raw)
            err = parsed.get("error") or {}
            return self._map_code(err.get("code"), err.get("detail") or "")
        except Exception:  # noqa: BLE001
            if status == 401:
                return refuse(
                    PROVIDER_UNAUTHORIZED,
                    "the connector rejected this runtime's signature. The tunnel "
                    "is up and the secret is wrong — check both copies.",
                )
            return refuse(PROVIDER_UNREACHABLE, remote_summary(
                f"the {self.device_ref} connector", status=status, body=raw))

    @staticmethod
    def _map_code(code: str | None, detail: str):
        code = code or "INTERNAL"
        if code in ("UPSTREAM_UNAUTHORIZED",):
            return refuse(PROVIDER_UNAUTHORIZED, detail)
        if code in ("UPSTREAM_UNREACHABLE",):
            return refuse(PROVIDER_UNREACHABLE, detail)
        if code == "SESSION_NOT_FOUND":
            return refuse(SESSION_NOT_FOUND, detail)
        if code == "WRITE_NOT_GRANTED":
            # The connector on the far side refused on its own authority. It is
            # not this surface's decision and must not be reported as one: the
            # fix is a file on that device, and the detail says which list.
            return refuse(WRITE_NOT_GRANTED, detail)
        # Everything else the connector can say — PROTOCOL_MISMATCH, STALE_REQUEST,
        # DEVICE_MISMATCH, REPLAY, UNKNOWN_ROUTE and the rest — is a refusal about
        # the request itself, and it is reported under the connector's own word
        # rather than translated into this surface's vocabulary. Translating would
        # mean two names for one condition, and the far side is where the fix is.
        # The closed set of words is defined where they are raised, in
        # `node_protocol.py` and `node/connector.py`; a copy here would be a third
        # place to drift.
        return refuse(BAD_REQUEST, f"{code}: {detail}")

    # -- DeviceProvider ----------------------------------------------------

    def health(self) -> ProviderHealth:
        try:
            payload = self._request("GET", "/v1/health")
        except Exception as exc:  # noqa: BLE001 - mapped refusal
            code = getattr(exc, "code", "INTERNAL")
            detail = getattr(exc, "detail", str(exc))
            return ProviderHealth(
                device_ref=self.device_ref,
                kind=self.kind,
                reachable=code not in (PROVIDER_UNREACHABLE, PROVIDER_TIMEOUT),
                authorized=code != PROVIDER_UNAUTHORIZED,
                detail=detail,
                error_code=code,
            )
        upstream = payload.get("upstream") or {}
        return ProviderHealth(
            device_ref=self.device_ref,
            kind=self.kind,
            reachable=True,
            authorized=True,
            detail=payload.get("detail") or "connector answered",
            server_name=upstream.get("server_name"),
            server_version=upstream.get("server_version"),
            tool_count=upstream.get("tool_count"),
            latency_ms=payload.get("latency_ms"),
        )

    def list_sessions(
        self, *, limit: int, project: str | None = None, state: str | None = None
    ) -> list[SessionSummary]:
        query: dict[str, str] = {"limit": str(int(limit))}
        if project:
            query["project"] = project
        if state:
            query["state"] = state
        path = "/v1/sessions?" + urllib.parse.urlencode(query)
        payload = self._request("GET", path)
        return [summary_from_dict(row) for row in (payload.get("sessions") or []) if isinstance(row, dict)]

    def get_session(self, key: SessionKey) -> dict[str, Any]:
        from ..session_detail import normalize_detail, validate_binding_target
        validate_binding_target(key, self.binding)
        payload = self._request("GET", f"/v1/sessions/{urllib.parse.quote(key.native_session_id)}")
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return normalize_detail(key, payload, self.binding, repair_legacy_claude=True)

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
        body: dict[str, Any] = {"mode": mode, "limit": int(limit)}
        for name, value in (("aroundId", around_id), ("cursor", cursor)):
            if value:
                body[name] = value
        for name, value in (("before", before), ("after", after)):
            if value is not None:
                body[name] = int(value)
        payload = self._request("POST", f"/v1/sessions/{urllib.parse.quote(key.native_session_id)}/read", body)
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return payload

    def send(
        self, key: SessionKey, message: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = {"message": message}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        payload = self._request("POST", f"/v1/sessions/{urllib.parse.quote(key.native_session_id)}/send", body)
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return payload

    def interrupt(self, key: SessionKey) -> dict[str, Any]:
        """POST to the connector's `/v1/sessions/{id}/interrupt`, signed.

        The body is empty, and that is the shape rather than an oversight: the
        connector's own handler reads no field, so there is nothing here for a
        caller to smuggle in. A body carrying a field the far side ignores would
        read, in this file, as though the far side honoured it.

        The connector has its own control floor, separate from the Fleet
        surface's and closed by default. A refusal from it arrives as
        `WRITE_NOT_GRANTED` and is reported as *that machine's* answer — which
        is the point of having two gates: the far side's "no" is not this
        side's "no", and an operator diagnosing a refused interrupt needs to
        know which door was shut.

        Unlike `send`, an empty answer is not read here as "no such session".
        `send` may, because it has no other way to say so; an interrupt has an
        answer shape of its own, and a connector that replied `{}` would be
        replying with nothing — which `FleetService.interrupt_session` refuses
        as `INTERNAL` rather than translating into the confident claim that no
        run was in flight.
        """
        return self._request(
            "POST",
            f"/v1/sessions/{urllib.parse.quote(key.native_session_id)}/interrupt",
            {},
        )

    def get_run(
        self, key: SessionKey, run_id: str, *, wait: bool = False, wait_seconds: float = 0.0
    ) -> dict[str, Any]:
        query = {"session": key.native_session_id, "wait": "1" if wait else "0"}
        if wait and wait_seconds:
            query["waitSeconds"] = str(float(wait_seconds))
        path = f"/v1/runs/{urllib.parse.quote(run_id)}?" + urllib.parse.urlencode(query)
        return self._request("GET", path)

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
        """Create a session through the connector's `POST /v1/sessions`.

        The body carries the provider's own field names — the same spellings the
        connector hands to `session_create` — so there is one translation of the
        tool's arguments, in the far side's adapter, and not a second one here.
        The connector makes its own creation decision before it looks at any of
        this; a refusal from it arrives as `WRITE_NOT_GRANTED` and is reported
        as the machine's answer rather than this surface's.
        """
        body: dict[str, Any] = {"project": project, "provider": provider}
        for name, value in (
            ("message", message),
            ("model", model),
            ("lifecycleMode", lifecycle_mode),
            ("permissionMode", permission_mode),
        ):
            if value is not None:
                body[name] = value
        return self._request("POST", "/v1/sessions", body)


__all__ = ["FleetNodeProvider", "body_digest", "PROTOCOL", "PROTOCOL_HEADER"]
