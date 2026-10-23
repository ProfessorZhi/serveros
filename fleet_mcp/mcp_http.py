"""A stdlib MCP Streamable-HTTP client.

Why this exists rather than a dependency
----------------------------------------
The same reason `mcp/node_mcp.py` implements its JSON-RPC framing by hand: the
thing this process talks to is a control plane, and a package resolution
between the Fleet surface and a device's provider fails in a way that reads as
"the device has nothing to say". The protocol this needs is four methods and
one framing, both of which are stable.

What it is careful about
------------------------
*   A transport failure and a protocol refusal are different facts and produce
    different typed errors. `PROVIDER_UNREACHABLE` is not `SESSION_NOT_FOUND`.
*   A `401`/`403` is `PROVIDER_UNAUTHORIZED`, which is a credential problem,
    not a reachability one.
*   A read timeout after a successful connect is `PROVIDER_TIMEOUT`, which is
    a retry, not an outage.
*   The credential is never logged, never echoed into an error, and never
    placed in a URL — it travels only in the `Authorization` header, so it
    cannot be captured by a proxy access log or a redirect.
*   **Remote text is never relayed.** An error body, an upstream JSON-RPC error
    message, a response that is not JSON — this process did not write any of it,
    and the far end is a gateway in front of a model provider, which is where an
    upstream API key gets quoted back. Every one of those strings is measured by
    `disclosure.remote_summary` and left behind; what crosses is a description
    written here. The earlier shape of this file truncated them to 200
    characters instead, which is a bound on how much of an unknown string is
    relayed rather than a decision not to relay it.
"""
from __future__ import annotations

import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .disclosure import remote_summary
from .errors import (
    BAD_REQUEST,
    INTERNAL,
    PROJECT_NOT_FOUND,
    PROVIDER_TIMEOUT,
    PROVIDER_UNAUTHORIZED,
    PROVIDER_UNREACHABLE,
    RUN_NOT_FOUND,
    SESSION_BUSY,
    SESSION_NOT_FOUND,
    refuse,
)

PROTOCOL_VERSION = "2024-11-05"

# Device envelope codes that name a condition this surface already has a word
# for. The mapping is deliberately one-directional and exact: a code is here
# only when the two words mean the same thing, because a translated refusal is
# a claim about *whose* problem it is.
#
# Everything else keeps the device's own word inside the detail and is reported
# as `BAD_REQUEST` — the way `providers/fleet_node._map_code` already reports
# connector codes it does not translate. What it must never be is `INTERNAL`:
# that code means "a defect in this surface", and a device saying
# `SESSION_HOST_NOT_FOUND` is the device's behaviour, not a bug here. Reporting
# it as INTERNAL on 2026-10-23 sent an operator looking at the wrong machine.
REMOTE_REFUSAL_CODES = {
    "SESSION_NOT_FOUND": SESSION_NOT_FOUND,
    "PROJECT_NOT_FOUND": PROJECT_NOT_FOUND,
    "RUN_NOT_FOUND": RUN_NOT_FOUND,
    "SESSION_BUSY": SESSION_BUSY,
}


class ProviderRefusal(Exception):
    """A provider answered, and the answer was an error."""

    def __init__(self, code: str, detail: str, *, status: int | None = None) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.status = status


class McpHttpClient:
    """One client per (endpoint, credential). Not thread-shared.

    The MCP Streamable HTTP transport is stateful per session (`Mcp-Session-Id`),
    so a client that is reused across threads would interleave two sessions'
    ids. Callers construct one per operation group.
    """

    def __init__(
        self,
        url: str,
        token: str | None,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 30.0,
        label: str = "provider",
    ) -> None:
        self.url = url
        self._token = token
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.label = label
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False
        # `urllib` applies one timeout to connect+read. To tell the two apart
        # the connect is done against a short deadline and a failure at that
        # deadline is reported as unreachable, while a failure later is
        # reported as a timeout. The distinction is approximate by nature; it
        # is still worth having, because the two send an operator to different
        # places.
        self._t0: float | None = None

    # -- transport ----------------------------------------------------------

    def _post(self, payload: dict[str, Any], *, notify: bool = False) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "User-Agent": "serveros-fleet-mcp/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        self._t0 = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.read_timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                sid = response.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                status = response.status
                ctype = response.headers.get("Content-Type", "") or ""
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = ""
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - the body is best-effort
                pass
            if status in (401, 403):
                raise ProviderRefusal(
                    PROVIDER_UNAUTHORIZED,
                    f"{self.label} rejected the credential (HTTP {status}). "
                    f"The device answered; the token is the problem.",
                    status=status,
                ) from None
            raise ProviderRefusal(
                INTERNAL,
                remote_summary(
                    self.label,
                    method=payload.get("method"),
                    status=status,
                    body=raw,
                    content_type=exc.headers.get("Content-Type") if exc.headers else None,
                ),
                status=status,
            ) from None
        except (socket.timeout, TimeoutError) as exc:
            elapsed = time.monotonic() - (self._t0 or time.monotonic())
            raise ProviderRefusal(
                PROVIDER_TIMEOUT,
                f"{self.label} connected and did not answer within "
                f"{self.read_timeout:.0f}s ({elapsed:.1f}s elapsed). The runtime "
                f"is up and busy, or the route is slow — not absent.",
            ) from None
        except (urllib.error.URLError, ConnectionError, ssl.SSLError, OSError) as exc:
            raise ProviderRefusal(
                PROVIDER_UNREACHABLE,
                f"cannot reach {self.label}: {type(exc).__name__}: {exc}",
            ) from None

        if notify or status == 202:
            return {"_accepted": True, "_status": status}
        return self._decode(raw, ctype, label=self.label)

    @staticmethod
    def _decode(raw: str, ctype: str, *, label: str = "provider") -> dict[str, Any]:
        """Accept both plain JSON and an SSE frame carrying one JSON message.

        CloudCLI's gateway answers plain JSON today. Handling SSE as well is not
        speculation: the transport is *defined* to allow either, and a client
        that only ever decoded JSON would start failing on an upgrade that
        nothing in this process decided to take.
        """
        stripped = raw.lstrip()
        if "text/event-stream" in ctype or stripped.startswith("event:") or "\ndata:" in raw:
            for line in raw.splitlines():
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk:
                        continue
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
            raise ProviderRefusal(
                INTERNAL, remote_summary(label, body=raw, content_type=ctype)
            )
        try:
            return json.loads(raw) if stripped else {}
        except json.JSONDecodeError:
            raise ProviderRefusal(
                INTERNAL, remote_summary(label, body=raw, content_type=ctype)
            ) from None

    # -- protocol -----------------------------------------------------------

    def _rpc(self, method: str, params: dict[str, Any] | None, *, notify: bool = False):
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            message["id"] = self._next_id
        reply = self._post(message, notify=notify)
        if notify:
            return reply
        if "error" in reply:
            err = reply["error"] or {}
            raise ProviderRefusal(
                INTERNAL,
                remote_summary(
                    self.label,
                    method=method,
                    rpc_code=err.get("code"),
                    error_message=err.get("message"),
                ),
            )
        return reply.get("result", {})

    def initialize(self) -> dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "serveros-fleet-mcp", "version": "0.1.0"},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        self._initialized = True
        return result

    def tools_list(self) -> list[dict[str, Any]]:
        if not self._initialized:
            self.initialize()
        result = self._rpc("tools/list", {})
        return list(result.get("tools") or [])

    def tools_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._initialized:
            self.initialize()
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})


def structured(result: dict[str, Any], *, label: str = "the provider") -> dict[str, Any]:
    """Pull the structured half of an MCP tool result.

    CloudCLI returns both a text block and `structuredContent`. The structured
    half is preferred because it is the one the provider committed to a shape
    for; the text half is a rendering. Where only the text half exists it is
    parsed as JSON, and a failure to parse is reported rather than swallowed —
    a caller silently handed `{}` would read it as "no sessions".

    A tool result flagged `isError` carries a message the far end wrote, and
    that message is not relayed: it is the one place a gateway quotes back a
    value it was configured with. Its length is reported and the text is left
    where it was produced. A result flagged `isError` with *no* text cannot be
    told from a provider that simply produced nothing, so it says exactly that
    rather than inventing a reason.

    The one part of an error envelope that *is* relayed is its `code`
    (`structuredContent.code`): an upper-case enum naming which failure this was
    (`NO_CLIENT`, `PROJECT_WORKSPACE_ROOT_REQUIRED`, `PROJECT_ALREADY_EXISTS`).
    Without it an operator learns only that "the provider answered unusably" and
    has to open the device's journal to find out why — which is how a missing
    `WORKSPACES_ROOT` read as a Fleet defect on 2026-10-23. `disclosure.
    remote_summary` re-checks the shape and withholds anything that is not an
    enum.

    The refusal's *code* follows the device unless the two vocabularies agree
    exactly (`REMOTE_REFUSAL_CODES`); otherwise it is `BAD_REQUEST` carrying the
    device's own word in the detail. Neither is `INTERNAL`: `INTERNAL` means a
    defect in this surface, and a device declining to do something is not one.
    """
    content = (result or {}).get("content") or []
    if result.get("isError"):
        text = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
        envelope = result.get("structuredContent")
        code = envelope.get("code") if isinstance(envelope, dict) else None
        if not text and code is None:
            raise refuse(INTERNAL, f"{label} reported an error and said nothing more")
        detail = remote_summary(
            label,
            error_message=text or None,
            error_code=code,
        )
        # The device refusing is the device's behaviour. It is translated only
        # where the two vocabularies already agree exactly; otherwise it keeps
        # its own word inside `detail` and is reported as a bad request.
        translated = REMOTE_REFUSAL_CODES.get(code) if isinstance(code, str) else None
        if translated:
            raise refuse(translated, detail)
        raise refuse(BAD_REQUEST, detail)
    if isinstance(result, dict) and "structuredContent" in result:
        value = result["structuredContent"]
        if isinstance(value, dict):
            return value
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                parsed = json.loads(block.get("text") or "")
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}
