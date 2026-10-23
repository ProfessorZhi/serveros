#!/usr/bin/env python3
"""Run the Fleet MCP surface end to end over its own protocol, and receipt it.

Why this exists, and why it is not the test suite
-------------------------------------------------
The unit tests call `call_tool` directly. `call_tool` is where the tool
handlers live, and it is NOT what a client speaks: a client speaks JSON-RPC to
`McpServer.handle`. Between the two sit `initialize`, the negotiated protocol
version, `tools/list`, and the JSON-RPC error envelope — and a defect in that
layer is invisible to every test that starts one level below it.

So this runs the REAL `McpServer` over real JSON-RPC messages, against a fake
device with full permission, and prints one machine receipt. It is the entry a
release candidate runs before Next goes live, and it does not need a ChatGPT
icon, an account, or a browser: the whole point is that code acceptance is not
gated on a login.

What is faked and what is real
------------------------------
Real: the server object, the protocol dispatch, the tool registry and schemas,
every authorization gate, the routing service. Fake: the DEVICE behind them —
one in-process provider that records what it was asked. No socket, no
credential, no device, no model call. Nothing here opens a port or starts a
service.

The real Next origin is a SEPARATE section
------------------------------------------
`--next-readonly <base-url>` performs unauthenticated GETs against a real
origin and reports them under their own key. It is `NOT_RUN` unless asked for,
so a receipt from this runner can never be read as "the real route was
exercised" when it was not. It never POSTs, never authenticates, and never
touches the V1 or CloudCLI endpoints.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
for _path in (_ROOT, _HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from authority_matrix import summary as authority_summary  # noqa: E402
from fleet_mcp import tools as tools_mod  # noqa: E402
from fleet_mcp.config import (AuditBinding, AuthBinding, CreateGrant,  # noqa: E402
                              FleetConfig, PolicyBinding, ProviderBinding,
                              ServerBinding, provider_key)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import (SCOPE_CONTROL, SCOPE_CREATE, SCOPE_READ,  # noqa: E402
                             SCOPE_SEND, FleetService, Principal, SessionKey)
from fleet_mcp.providers.base import ProviderHealth, SessionSummary  # noqa: E402
from fleet_mcp.server import PREFERRED_PROTOCOL_VERSION, McpServer  # noqa: E402
from fleet_mcp.version import (SERVER_NAME, SERVER_VERSION,  # noqa: E402
                               TOOL_SURFACE_REVISION)

SCHEMA = "serveros/fleet-mcp-conformance/v1"
DEVICE = "device:tokyo-shared-dev-01"
UNROUTED = "device:not-enrolled-at-all"
SECOND = "claude-official"
PRINCIPAL = "oauth:conformance-client"
PROJECT = "conformance-project"
SESSION = "11111111-2222-3333-4444-555555555555"
SESSION_REF = f"{DEVICE}|claude|{SESSION}"


class FakeDevice:
    """One device with every permission. It records and answers; it decides
    nothing — which is the property under test, because every authorization
    decision must belong to the surface above it."""

    def __init__(self, device_ref: str, runtime: str = "claude") -> None:
        self.device_ref = device_ref
        self.runtime = runtime
        self.calls: list[tuple] = []
        #: Rows the listing answers with. Empty unless a check installs one.
        self.sessions: list[SessionSummary] = []

    def health(self) -> ProviderHealth:
        self.calls.append(("health",))
        return ProviderHealth(device_ref=self.device_ref, kind="cloudcli-mcp-http",
                              reachable=True, authorized=True, detail="fake",
                              tool_count=len(tools_mod.NATIVE_MIRROR_TOOLS))

    def list_sessions(self, *, limit: int, project=None, state=None) -> list:
        self.calls.append(("list_sessions", limit, project, state))
        return list(self.sessions)

    def get_session(self, key) -> dict:
        self.calls.append(("get_session", key.ref))
        return {"sessionId": SESSION, "state": "idle", "served_by": self.runtime}

    def send(self, key, message, *, idempotency_key=None) -> dict:
        self.calls.append(("send", key.ref, message))
        return {"ok": True, "served_by": self.runtime,
                "runId": f"{self.runtime}-fixture-run-{len(self.calls)}"}

    def interrupt(self, key) -> dict:
        self.calls.append(("interrupt", key.ref))
        # `aborted` is the field the service reads to distinguish "stopped a
        # run" from "there was no run in flight". A fixture that omitted it
        # would be testing the service's refusal of a malformed answer, not a
        # successful control path.
        return {"aborted": True, "served_by": self.runtime}

    def create(self, *, project, message=None, provider="claude", model=None,
               lifecycle_mode=None, permission_mode=None) -> dict:
        self.calls.append(("create", project, provider))
        return {"sessionId": SESSION, "runId": None, "served_by": self.runtime}

    def native_call(self, tool, arguments, *, session_ref=None) -> dict:
        self.calls.append(("native_call", tool, session_ref))
        return {"ok": True, "served_by": self.runtime}

    def capability_snapshot(self) -> dict:
        self.calls.append(("capability_snapshot",))
        return {"device_ref": self.device_ref, "runtime": self.runtime}


def build(tmp: Path, *, grants, create_allow, send_allow=()):
    anchor = tmp / "conformance.pat"
    anchor.write_text("conformance-owner-token\n", encoding="utf-8")
    registry = tmp / "registry.yaml"
    registry.write_text(
        "devices:\n"
        f"  - device_ref: {DEVICE}\n"
        "    display_name: conformance\n"
        "    platform: linux\n"
        "    environment_profile: linux-shared-multiuser\n"
        "    ownership: company\n",
        encoding="utf-8")
    second_anchor = tmp / "conformance-second.pat"
    second_anchor.write_text("conformance-second-token\n", encoding="utf-8")

    config = FleetConfig(
        source=None, local_device_ref=DEVICE,
        providers={
            DEVICE: ProviderBinding(
                device_ref=DEVICE, kind="cloudcli-mcp-http",
                endpoint="http://127.0.0.1:0/mcp", credential_anchor=anchor,
                allow_send=True, allow_create=True, allow_control=True),
            provider_key(DEVICE, SECOND): ProviderBinding(
                device_ref=DEVICE, runtime=SECOND, kind="cloudcli-mcp-http",
                endpoint="http://127.0.0.1:0/mcp", credential_anchor=second_anchor,
                allow_send=True, allow_create=True, allow_control=True),
        },
        auth=AuthBinding(pat_anchor=anchor),
        server=ServerBinding(bind="127.0.0.1", port=0),
        policy=PolicyBinding(send_allow=tuple(send_allow),
                             create_allow=dict(create_allow),
                             operator_grants=dict(grants)),
        audit=AuditBinding(path=tmp / "conformance-audit.jsonl",
                           rate_limit_per_minute=10_000))
    from fleet_mcp.registry import load_registry
    devices = {DEVICE: FakeDevice(DEVICE),
               provider_key(DEVICE, SECOND): FakeDevice(DEVICE, SECOND)}
    service = FleetService(config, registry=load_registry(registry),
                           providers=devices)
    return service, devices


class Receipt:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def record(self, ident: str, klass: str, ok: bool | None, detail: str = "") -> None:
        outcome = "NOT_RUN" if ok is None else ("PASS" if ok else "FAIL")
        self.checks.append({"id": ident, "class": klass, "outcome": outcome,
                            "detail": detail})

    def counted(self) -> dict:
        return {word.lower(): sum(1 for c in self.checks if c["outcome"] == word)
                for word in ("PASS", "FAIL", "NOT_RUN")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--next-readonly", default=None,
                        help="a real origin to GET read-only; omit and that "
                             "section is NOT_RUN")
    parser.add_argument("--receipt", default=None, help="write the receipt here")
    args = parser.parse_args(argv)

    import tempfile
    receipt = Receipt()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        service, devices = build(
            tmp,
            grants={PRINCIPAL: {DEVICE: ("send", "create", "control"),
                                provider_key(DEVICE, SECOND): ("send",)}},
            create_allow={PROJECT: CreateGrant()},
            send_allow=(SESSION_REF,))
        device = devices[DEVICE]

        def context(scopes=None, name: str = PRINCIPAL):
            principal = Principal(
                name=name,
                scopes=frozenset(scopes if scopes is not None
                                 else {SCOPE_READ, SCOPE_SEND, SCOPE_CREATE}),
                credential_kind="oauth")
            return tools_mod.ToolContext(service=service, principal=principal)

        server = McpServer(context)

        # -- 1. the handshake ------------------------------------------------
        init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                              "params": {"protocolVersion": PREFERRED_PROTOCOL_VERSION}})
        negotiated = (init or {}).get("result", {}).get("protocolVersion")
        receipt.record("initialize.negotiates_a_supported_version", "protocol",
                       negotiated == PREFERRED_PROTOCOL_VERSION,
                       f"negotiated={negotiated!r}")
        meta = (init or {}).get("result", {}).get("_meta", {})
        receipt.record("initialize.advertises_the_surface_revision", "contract",
                       meta.get("tool_surface_revision") == TOOL_SURFACE_REVISION,
                       f"revision={meta.get('tool_surface_revision')!r}")
        receipt.record("initialize.names_the_server", "protocol",
                       (init or {}).get("result", {}).get("serverInfo", {}).get("name")
                       == SERVER_NAME, SERVER_NAME)

        # -- 2. the surface --------------------------------------------------
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                "params": {}})
        tools = (listed or {}).get("result", {}).get("tools", [])
        receipt.record("tools_list.matches_the_served_set", "contract",
                       [t["name"] for t in tools] == [t.name for t in tools_mod.SERVED_TOOLS],
                       f"{len(tools)} tools")
        closed = all(t.get("inputSchema", {}).get("additionalProperties") is False
                     for t in tools)
        receipt.record("tools_list.every_schema_is_closed", "contract", closed)
        shaped = all({"name", "title", "description", "inputSchema"} <= set(t)
                     for t in tools)
        receipt.record("tools_list.every_tool_is_fully_described", "contract", shaped)

        # A capability the surface does not have must be answered, not implied.
        for method in ("resources/list", "prompts/list"):
            answer = server.handle({"jsonrpc": "2.0", "id": 3, "method": method,
                                    "params": {}})
            key = method.split("/")[0]
            receipt.record(f"{method.replace('/', '_')}.is_an_empty_answer", "protocol",
                           (answer or {}).get("result", {}).get(key) == [])
        ping = server.handle({"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}})
        receipt.record("ping.answers", "protocol", "result" in (ping or {}))

        def call(name, arguments, ident=5):
            return server.handle({"jsonrpc": "2.0", "id": ident, "method": "tools/call",
                                  "params": {"name": name, "arguments": arguments}})

        # -- 3. a read, through the protocol ---------------------------------
        read = call("fleet_list_sessions", {"device": DEVICE})
        receipt.record("read.list_sessions_is_served", "grant",
                       "result" in (read or {}) and not (read or {}).get("result", {}).get("isError"),
                       json.dumps((read or {}).get("result", {}))[:120])

        # -- 4. a write with its own floor -----------------------------------
        #
        # `fleet_send_to_session` takes no `runtime`: the runtime arrives in the
        # `session_ref` (its middle segment is the runtime tag), and the surface
        # refuses an argument it does not know rather than ignoring it. Both
        # facts are asserted below, because "the runtime axis is carried" and
        # "the runtime axis is carried HERE" are different claims.
        device.calls.clear()
        sent = call("fleet_send_to_session",
                    {"session_ref": SESSION_REF, "message": "conformance"})
        delivered = any(c[0] == "send" for c in device.calls)
        receipt.record("write.send_reaches_the_device_when_granted", "grant", delivered,
                       f"device_calls={len(device.calls)}")
        stray_runtime = call("fleet_send_to_session",
                             {"session_ref": SESSION_REF, "message": "x",
                              "runtime": "claude"})
        receipt.record("write.a_send_refuses_an_argument_it_does_not_take", "contract",
                       bool((stray_runtime or {}).get("result", {}).get("isError")))

        # -- 5. the gates, exercised through the SAME protocol ----------------
        device.calls.clear()
        ungranted = call("fleet_send_to_session",
                         {"session_ref": f"{DEVICE}|claude|not-granted",
                          "message": "x", "runtime": "claude"})
        payload = (ungranted or {}).get("result", {})
        receipt.record("grant.an_ungranted_session_is_refused", "grant",
                       bool(payload.get("isError")) and not device.calls,
                       json.dumps(payload)[:120])

        unrouted = call("fleet_list_sessions", {"device": UNROUTED})
        receipt.record("route.an_unenrolled_device_is_refused", "grant",
                       bool((unrouted or {}).get("result", {}).get("isError")))

        unknown = call("fleet_not_a_tool", {})
        receipt.record("protocol.an_unknown_tool_is_an_answer_not_a_crash", "protocol",
                       bool((unknown or {}).get("result", {}).get("isError")))

        stray = call("fleet_list_sessions", {"device": DEVICE, "model": "x"})
        receipt.record("contract.a_stray_argument_is_refused", "contract",
                       bool((stray or {}).get("result", {}).get("isError")))

        # -- 5b. scope, control and protocol negatives ------------------------
        #
        # `tools/list` is discovery and stays open; every live read and every
        # write is gated. The checks below pin the difference, because a suite
        # that only counted 34 tools would be reporting discovery as
        # entitlement.
        def refusal_code(answer) -> str:
            if answer is None:
                return "NO_RESPONSE"
            if "error" in answer:
                return f"RPC_{answer['error'].get('code')}"
            result = answer.get("result", {})
            if not result.get("isError"):
                return "OK"
            return result.get("structuredContent", {}).get("code", "UNKNOWN")

        status_without_read = McpServer(lambda: context({SCOPE_SEND})).handle(
            {"jsonrpc": "2.0", "id": 20, "method": "tools/call",
             "params": {"name": "fleet_status", "arguments": {}}})
        receipt.record("scope.status_without_read_is_refused", "grant",
                       refusal_code(status_without_read) == "SCOPE_DENIED",
                       refusal_code(status_without_read))

        status_with_read = McpServer(lambda: context({SCOPE_READ})).handle(
            {"jsonrpc": "2.0", "id": 21, "method": "tools/call",
             "params": {"name": "fleet_status", "arguments": {}}})
        receipt.record("scope.status_with_read_is_served", "grant",
                       refusal_code(status_with_read) == "OK",
                       refusal_code(status_with_read))

        device.calls.clear()
        stopped = McpServer(lambda: context({SCOPE_CONTROL})).handle(
            {"jsonrpc": "2.0", "id": 22, "method": "tools/call",
             "params": {"name": "fleet_interrupt_session",
                        "arguments": {"session_ref": SESSION_REF}}})
        receipt.record("write.interrupt_reaches_the_device_when_granted", "grant",
                       refusal_code(stopped) == "OK"
                       and any(row[0] == "interrupt" for row in device.calls),
                       f"device_calls={device.calls}")

        no_grant = McpServer(lambda: context({SCOPE_CONTROL},
                                             name="oauth:conformance-no-grant")).handle(
            {"jsonrpc": "2.0", "id": 23, "method": "tools/call",
             "params": {"name": "fleet_interrupt_session",
                        "arguments": {"session_ref": SESSION_REF}}})
        receipt.record("grant.control_without_operator_grant_is_refused", "grant",
                       refusal_code(no_grant) == "APPROVAL_REQUIRED",
                       refusal_code(no_grant))

        unknown_method = server.handle(
            {"jsonrpc": "2.0", "id": 24, "method": "tools/does-not-exist",
             "params": {}})
        receipt.record("protocol.an_unknown_method_is_minus_32601", "protocol",
                       (unknown_method or {}).get("error", {}).get("code") == -32601,
                       json.dumps(unknown_method)[:120])

        device.calls.clear()
        silent = server.handle(
            {"jsonrpc": "2.0", "method": "tools/call",
             "params": {"name": "fleet_send_to_session",
                        "arguments": {"session_ref": SESSION_REF,
                                      "message": "notification-write"}}})
        receipt.record("protocol.a_notification_call_has_no_effect", "protocol",
                       silent is None and not device.calls,
                       f"response={silent!r} device_calls={device.calls}")

        empty = McpServer(lambda: context(set()))
        manifest = empty.handle(
            {"jsonrpc": "2.0", "id": 25, "method": "tools/call",
             "params": {"name": "fleet_cloudcli_native_manifest",
                        "arguments": {}}})
        receipt.record("contract.discovery_needs_no_scope", "contract",
                       refusal_code(manifest) == "OK", refusal_code(manifest))
        capability = empty.handle(
            {"jsonrpc": "2.0", "id": 26, "method": "tools/call",
             "params": {"name": "fleet_cloudcli_native_capability",
                        "arguments": {"device_ref": DEVICE}}})
        receipt.record("grant.live_capability_needs_read", "grant",
                       refusal_code(capability) == "SCOPE_DENIED",
                       refusal_code(capability))

        # -- 5c. the read surface and the gate share one upper bound ----------
        #
        # A read-only credential on a send-enabled device with an allowlisted
        # session used to be told `send_allowed: true` and then refused
        # `SCOPE_DENIED`. The listing must report the same caller scope the
        # execution path checks first — the read surface may not promise a
        # capability stronger than the gate.
        device.sessions = [SessionSummary(
            key=SessionKey.compose(DEVICE, "claude", SESSION),
            title="conformance", classification="RUNNING_AGENT", running=True,
            resident_host=False, host_state=None, lifecycle_mode=None,
            project_id=None, last_activity=None)]
        read_only = McpServer(lambda: context({SCOPE_READ}))
        listed = read_only.handle(
            {"jsonrpc": "2.0", "id": 27, "method": "tools/call",
             "params": {"name": "fleet_list_sessions",
                        "arguments": {"device": DEVICE}}})
        rows = (listed or {}).get("result", {}).get(
            "structuredContent", {}).get("sessions", [])
        reported = bool(rows and rows[0]["control_allowed"]["send_allowed"])
        refused_send = read_only.handle(
            {"jsonrpc": "2.0", "id": 28, "method": "tools/call",
             "params": {"name": "fleet_send_to_session",
                        "arguments": {"session_ref": SESSION_REF,
                                      "message": "read-only"}}})
        receipt.record("contract.a_read_only_caller_is_not_told_it_may_send",
                       "grant",
                       len(rows) == 1 and reported is False
                       and refusal_code(refused_send) == "SCOPE_DENIED",
                       f"rows={len(rows)} reported={reported} "
                       f"actual={refusal_code(refused_send)}")

        device.calls.clear()
        sender = McpServer(lambda: context({SCOPE_READ, SCOPE_SEND}))
        listed_sender = sender.handle(
            {"jsonrpc": "2.0", "id": 29, "method": "tools/call",
             "params": {"name": "fleet_list_sessions",
                        "arguments": {"device": DEVICE}}})
        rows_sender = (listed_sender or {}).get("result", {}).get(
            "structuredContent", {}).get("sessions", [])
        reported_sender = bool(
            rows_sender and rows_sender[0]["control_allowed"]["send_allowed"])
        sent = sender.handle(
            {"jsonrpc": "2.0", "id": 30, "method": "tools/call",
             "params": {"name": "fleet_send_to_session",
                        "arguments": {"session_ref": SESSION_REF,
                                      "message": "with-scope"}}})
        receipt.record("contract.a_send_scoped_caller_is_told_it_may_send",
                       "grant",
                       reported_sender is True and refusal_code(sent) == "OK"
                       and any(row[0] == "send" for row in device.calls),
                       f"reported={reported_sender} actual={refusal_code(sent)} "
                       f"device_calls={device.calls}")
        device.sessions = []

        # -- 6. the runtime axis, both directions -----------------------------
        device.calls.clear()
        second_device = devices[provider_key(DEVICE, SECOND)]
        second_device.calls.clear()
        routed = call("fleet_cloudcli_session_get",
                      {"device_ref": DEVICE, "runtime": SECOND,
                       "arguments": {"session": SESSION}})
        receipt.record("runtime_a_mirrored_verb_addresses_the_named_runtime", "runtime",
                       bool(second_device.calls) and not device.calls,
                       f"second={len(second_device.calls)} first={len(device.calls)}")

        blank = call("fleet_cloudcli_session_get",
                     {"device_ref": DEVICE, "runtime": "", "arguments": {"session": SESSION}})
        receipt.record("runtime_a_blank_runtime_is_refused", "runtime",
                       bool((blank or {}).get("result", {}).get("isError")))

        # -- 7. a defect is reported, never a traceback ------------------------
        def exploding():
            raise RuntimeError("conformance: deliberate defect")

        broken = McpServer(exploding).handle(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "fleet_list_sessions", "arguments": {}}})
        receipt.record("protocol.a_defect_becomes_a_jsonrpc_error", "protocol",
                       "error" in (broken or {}) and "traceback" not in json.dumps(broken).lower(),
                       json.dumps(broken)[:120])

    # -- 8. the real origin, in its own section ------------------------------
    if args.next_readonly is None:
        real = {"outcome": "NOT_RUN",
                "reason": "no --next-readonly origin was given; this receipt "
                          "says nothing about any real route"}
    else:
        real = probe_real(args.next_readonly)

    receipt_doc = {
        "schema": SCHEMA,
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION,
                   "surface_revision": TOOL_SURFACE_REVISION},
        "mode": "FAKE_DEVICE",
        "counts": receipt.counted(),
        "checks": receipt.checks,
        "authority_matrix": authority_summary(),
        "real_next_readonly": real,
        "note": "Every check above ran against an in-process fake device. "
                "`real_next_readonly` is the only section that touches a real "
                "origin, and it is NOT_RUN unless explicitly asked for.",
    }
    text = json.dumps(receipt_doc, indent=2, sort_keys=True)
    if args.receipt:
        Path(args.receipt).write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 2 if receipt.counted()["fail"] else 0


def probe_real(base: str) -> dict:
    """Unauthenticated, read-only GETs against a real origin.

    Read-only in the strict sense: `GET`, no body, no credential, no auth
    header. A non-200 is reported as what it is — including the 502 that a
    working Funnel route returns when its backend is deliberately not running.
    """
    import urllib.error
    import urllib.request

    base = base.rstrip("/")
    out: dict = {"outcome": "RUN", "base": base, "probes": {}}
    for path in ("/health", "/.well-known/oauth-protected-resource",
                 "/.well-known/oauth-authorization-server"):
        try:
            with urllib.request.urlopen(base + path, timeout=8) as response:
                out["probes"][path] = {"status": int(response.status)}
        except urllib.error.HTTPError as exc:
            out["probes"][path] = {"status": int(exc.code)}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            out["probes"][path] = {"status": None, "error": type(exc).__name__}
    return out


if __name__ == "__main__":
    raise SystemExit(main())
