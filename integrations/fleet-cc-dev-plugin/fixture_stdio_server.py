#!/usr/bin/env python3
"""TEST FIXTURE ONLY — a local stdio Fleet surface for Claude Code client tests.

This is **not** the product and **not** the development adapter that binds the
real Fleet Next ingress. It exists for one narrow job: let a real `claude`
client perform `initialize`, `tools/list` and a **read-only** `tools/call`
sequence against the *real* `fleet_mcp` protocol dispatcher, tools and service,
without a network, an OAuth consent, a credential, or a device.

What is real here:

* `fleet_mcp.server.McpServer` — the same JSON-RPC dispatcher the HTTP and
  production stdio surfaces use;
* `fleet_mcp.tools.SERVED_TOOLS` — every tool this build serves (44 at
  `fleet-mcp-7`), with their real schemas and `readOnlyHint` annotations;
* `fleet_mcp.fleet.FleetService` — the same scope checks, policy gates,
  refusals and audit records.

What is fake, and says so:

* the device: `device:fixture-node` is invented by this file;
* the provider: `FakeDeviceProvider` never opens a socket, never runs a
  process, and answers only what the fixture test asserts;
* the session: `fixture-session-0001` is not a conversation anywhere.

The principal is selected on the command line. The default, `read-only`, holds
exactly `fleet:read`. That is the point of the fixture: a client can discover
every served tool (discovery is not entitlement) and is then refused `SCOPE_DENIED`
when it tries any write, before the fake provider is reached.

Framing is one JSON-RPC message per line, the framing the MCP stdio transport
and the production `fleet_mcp.runtime.run_stdio` use. Every request and every
tool result is appended to `--log` as JSON lines, which is the evidence a real
client performed the handshake and the calls. Nothing is written to stdout
except protocol responses.
"""
from __future__ import annotations

import sys

# Importing `fleet_mcp` from a source checkout must not write `__pycache__` into
# the tree: the repository records tree hashes for installed artifacts, and a
# single import is enough to make a recorded hash unreachable. The generated
# `.mcp.json` also sets PYTHONDONTWRITEBYTECODE=1; this is the half that holds
# when someone runs the file by hand.
sys.dont_write_bytecode = True

import argparse
import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import cloudcli_native  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
)
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.identity import SessionKey  # noqa: E402
from fleet_mcp.providers.base import (  # noqa: E402
    MANAGED_IDLE,
    ProviderHealth,
    SessionSummary,
)
from fleet_mcp.registry import DeviceRegistry  # noqa: E402
from fleet_mcp.runtime_identity import BASE_RUNTIME  # noqa: E402
from fleet_mcp.server import McpServer  # noqa: E402
from fleet_mcp.tools import ToolContext  # noqa: E402

DEVICE = "device:fixture-node"
SESSION = SessionKey.compose(DEVICE, BASE_RUNTIME, "fixture-session-0001")
PROJECT = "fixture-project"
READ_ONLY = "read-only"
OWNER = "owner"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class FakeDeviceProvider:
    """A device that answers from constants, and records every send attempt.

    `health()` is the only method `fleet_list_devices` needs; the rest exist so
    the read tools have something to read. `send()` records rather than does
    anything — a write refused for scope must leave `sent` empty, which is how
    the negative test proves the device was never reached.
    """

    kind = "fixture"
    device_ref = DEVICE

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.created: list[dict[str, Any]] = []

    # -- reads ------------------------------------------------------------

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=DEVICE,
            kind=self.kind,
            reachable=True,
            authorized=True,
            detail="TEST_FIXTURE: no device was contacted and no socket was opened",
            server_name="serveros-fleet-fixture",
            server_version="0.0.0-test",
            tool_count=len(cloudcli_native.NATIVE_TOOLS),
            latency_ms=0.0,
        )

    def list_sessions(self, *, limit: int, project: str | None = None,
                      state: str | None = None) -> list[SessionSummary]:
        if project and project != PROJECT:
            return []
        if state and state != "managed":
            return []
        return [_summary()][:limit]

    def get_session(self, key: SessionKey) -> dict[str, Any]:
        return {
            "session_ref": key.ref,
            "title": "TEST_FIXTURE session — not a real conversation",
            "classification": MANAGED_IDLE,
            "lifecycle_mode": "managed",
            "project_id": PROJECT,
            "last_activity": {"iso": "2026-10-20T00:00:00Z", "kind": "TEST_FIXTURE"},
            "current_run": None,
            "evidence": {"source": "TEST_FIXTURE", "device_contacted": False},
        }

    def read_session(self, key: SessionKey, *, mode: str = "latest", limit: int = 20,
                     around_id: str | None = None, before: int | None = None,
                     after: int | None = None, cursor: str | None = None) -> dict[str, Any]:
        return {
            "mode": mode,
            "messages": [
                {
                    "id": "msg-fixture-1",
                    "role": "user",
                    "text": "TEST_FIXTURE: this text is a constant, not a transcript.",
                }
            ][:limit],
            "source": "TEST_FIXTURE",
        }

    def capability_snapshot(self) -> dict[str, Any]:
        # The device declares exactly two native verbs. The mirror count is not
        # evidence that every verb answers; this snapshot says OBSERVED with an
        # observed_count of two, and the rest are UNSUPPORTED_BY_DEVICE.
        return cloudcli_native.capability_snapshot(
            device_ref=DEVICE,
            observed={
                "sessions_list": {"annotations": {"readOnlyHint": True}, "schema": {},
                                 "_meta": {"cloudcli/handlerBound": True}},
                "session_get": {"annotations": {"readOnlyHint": True}, "schema": {},
                                 "_meta": {"cloudcli/handlerBound": True}},
            },
            source="TEST_FIXTURE",
            version="0.0.0-test",
            sampled_at=_now(),
        )

    def get_run(self, key: SessionKey, run_id: str, *, wait: bool = False,
                wait_seconds: float = 0.0) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "session_ref": key.ref,
            "state": "completed",
            "output": "TEST_FIXTURE: no run happened",
        }

    # -- writes (recorded, never performed) --------------------------------

    def send(self, key: SessionKey, message: str, *,
             idempotency_key: str | None = None) -> dict[str, Any]:
        self.sent.append((key.ref, message))
        return {"runId": f"run-fixture-{len(self.sent)}", "accepted": True,
                "source": "TEST_FIXTURE"}

    def create(self, *, project: str, message: str | None = None,
               provider: str = "claude", model: str | None = None,
               lifecycle_mode: str | None = None,
               permission_mode: str | None = None) -> dict[str, Any]:
        self.created.append({"project": project})
        return {"sessionId": "fixture-created-session", "runId": None,
                "source": "TEST_FIXTURE"}

    def interrupt(self, key: SessionKey) -> dict[str, Any]:
        return {"aborted": False,
                "message": "TEST_FIXTURE: there was no run to abort"}


def _summary() -> SessionSummary:
    return SessionSummary(
        key=SESSION,
        title="TEST_FIXTURE session — not a real conversation",
        classification=MANAGED_IDLE,
        running=False,
        resident_host=False,
        host_state=None,
        lifecycle_mode="managed",
        project_id=PROJECT,
        last_activity={"iso": "2026-10-20T00:00:00Z", "kind": "TEST_FIXTURE"},
        current_run=None,
        evidence={
            "provider.running": False,
            "provider.hostState": None,
            "provider.isArchived": False,
            "rule": "TEST_FIXTURE constant; no provider was read",
        },
    )


def build_service(state_dir: Path) -> tuple[FleetService, FakeDeviceProvider, Principal]:
    """The real service over the fake device, and the scoped principal."""
    state_dir.mkdir(parents=True, exist_ok=True)
    anchor = state_dir / "fixture-pat-anchor"
    anchor.write_text("TEST_FIXTURE: this is not a credential\n", encoding="utf-8")

    registry = DeviceRegistry({
        "devices": [
            {
                "device_ref": DEVICE,
                "device_id": "fixture-node",
                "display_name": "Fixture Node (TEST_FIXTURE)",
                "platform": "linux",
                "os_family": "linux",
                "ownership": "company",
                "roles": ["development-primary"],
                "environment_profile": "linux-server",
                "agents": ["claude-code"],
                "runtime_bindings": [
                    {"runtime_ref": "agent-runtime:claude-code",
                     "declared_at": "2026-10-20"}
                ],
                "legacy_ids": [],
                "enrollment": "enrolled",
            }
        ]
    })
    config = FleetConfig(
        source=None,
        local_device_ref=DEVICE,
        providers={
            DEVICE: ProviderBinding(
                device_ref=DEVICE,
                kind="cloudcli-mcp-http",
                endpoint="http://127.0.0.1:0/mcp",
                credential_anchor=anchor,
                allow_send=True,
            )
        },
        auth=AuthBinding(pat_anchor=anchor, oauth_enabled=False),
        server=ServerBinding(bind="127.0.0.1", port=0),
        # Empty on purpose. The read-only principal is refused at the scope
        # before any policy is read, and a fixture that named a sendable
        # session while reporting a read-only principal would make the listing
        # say `send_allowed: true` about a caller the send tool refuses. That
        # disagreement is a product question, reported by this lane, not
        # reproduced as the fixture's default state.
        policy=PolicyBinding(),
        audit=AuditBinding(path=state_dir / "audit.jsonl",
                           rate_limit_per_minute=10_000),
    )
    provider = FakeDeviceProvider()
    service = FleetService(config, registry=registry, providers={DEVICE: provider})
    return service, provider, _principal(READ_ONLY)


def _principal(kind: str) -> Principal:
    if kind == READ_ONLY:
        return Principal(name="cc-fixture-read-only",
                         scopes=frozenset({"fleet:read"}),
                         credential_kind="fixture")
    return Principal(name="cc-fixture-owner",
                     scopes=frozenset({"fleet:read", "fleet:send", "fleet:create"}),
                     credential_kind="fixture")


class _Log:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, entry: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, help="JSON-lines wire log")
    parser.add_argument("--state-dir", required=True, help="audit and anchor dir")
    parser.add_argument("--principal", choices=(READ_ONLY, OWNER), default=READ_ONLY)
    args = parser.parse_args(argv)

    state_dir = Path(args.state_dir)
    service, provider, principal = build_service(state_dir)
    if args.principal == OWNER:
        principal = _principal(OWNER)
    log = _Log(Path(args.log))
    log.write({"event": "start", "principal": principal.name,
               "scopes": sorted(principal.scopes), "device_ref": DEVICE,
               "session_ref": SESSION.ref, "source": "TEST_FIXTURE"})

    mcp = McpServer(lambda: ToolContext(service=service, principal=principal))
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            log.write({"event": "parse_error", "detail": str(exc)})
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }) + "\n")
            sys.stdout.flush()
            continue

        method = message.get("method")
        params = message.get("params") or {}
        request = {"event": "request", "method": method, "id": message.get("id")}
        if method == "tools/call":
            request["tool"] = params.get("name")
            request["arguments"] = params.get("arguments")
        log.write(request)

        response = mcp.handle(message)
        if response is None:
            continue
        result = response.get("result") or {}
        if method == "tools/list":
            log.write({"event": "response", "method": method,
                       "tool_count": len(result.get("tools") or [])})
        elif method == "tools/call":
            structured = result.get("structuredContent") or {}
            log.write({
                "event": "response",
                "method": method,
                "tool": params.get("name"),
                "isError": result.get("isError"),
                "code": structured.get("code"),
                "keys": sorted(structured),
            })
        elif method == "initialize":
            log.write({"event": "response", "method": method,
                       "server": (result.get("serverInfo") or {}).get("name"),
                       "protocol": result.get("protocolVersion")})
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    log.write({"event": "shutdown", "provider_sends": len(provider.sent),
               "provider_creates": len(provider.created)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
