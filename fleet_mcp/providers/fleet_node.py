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
    holds a secret that can ask seven named questions and nothing else.
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

from ..config import ProviderBinding
from ..errors import (
    BAD_REQUEST,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNREACHABLE,
    SESSION_NOT_FOUND,
    WRITE_NOT_GRANTED,
    refuse,
)
from ..identity import SessionKey
from ..node_protocol import PROTOCOL, PROTOCOL_HEADER, body_digest, sign, split_path_and_query
from .base import DeviceProvider, ProviderHealth, SessionSummary, summary_from_dict


class FleetNodeProvider(DeviceProvider):
    kind = "fleet-node"

    def __init__(self, binding: ProviderBinding) -> None:
        self.binding = binding
        self.device_ref = binding.device_ref

    # -- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
        headers = sign(secret, method=method, path=sign_path, body=body, device_ref=self.device_ref)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        url = self.binding.endpoint.rstrip("/") + path
        request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.binding.read_timeout) as response:
                raw = response.read().decode("utf-8", "replace")
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
        try:
            decoded = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            raise refuse(BAD_REQUEST, f"connector returned non-JSON: {raw[:200]!r}") from None
        if isinstance(decoded, dict) and "error" in decoded:
            err = decoded["error"] or {}
            raise self._map_code(err.get("code"), err.get("detail") or "")
        return decoded if isinstance(decoded, dict) else {}

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
            return refuse(PROVIDER_UNREACHABLE, f"connector returned HTTP {status}: {raw[:200]}")

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
        payload = self._request("GET", f"/v1/sessions/{urllib.parse.quote(key.native_session_id)}")
        if not payload:
            raise refuse(SESSION_NOT_FOUND, f"{key.ref} is not known on {key.device_ref}")
        return payload

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

    def get_run(
        self, key: SessionKey, run_id: str, *, wait: bool = False, wait_seconds: float = 0.0
    ) -> dict[str, Any]:
        query = {"session": key.native_session_id, "wait": "1" if wait else "0"}
        if wait and wait_seconds:
            query["waitSeconds"] = str(float(wait_seconds))
        path = f"/v1/runs/{urllib.parse.quote(run_id)}?" + urllib.parse.urlencode(query)
        return self._request("GET", path)


__all__ = ["FleetNodeProvider", "body_digest", "PROTOCOL", "PROTOCOL_HEADER"]
