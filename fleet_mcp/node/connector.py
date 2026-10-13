"""The connector a Windows workstation runs. It dials out; it accepts nothing in.

Why this side exists at all
---------------------------
The Windows CloudCLI personal access token is a credential for everything
CloudCLI can do on that machine, and the machine is a corporate workstation. The
token never leaves it. What crosses the tunnel instead is a signed, closed set
of session operations, served by this process on the far end of a reverse SSH
forward that this machine owns and can drop.

Two independent gates, on purpose
---------------------------------
The Fleet surface decides whether a *caller* may drive this device. This
connector decides whether *anything* may drive the sessions on it. They are
different questions asked by different parties on different machines, and a
connector that trusted the far end's decision would be a connector whose
security is one configuration file away from being someone else's configuration
file.

So `allow_send_to` here is empty by default. Empty means every send is refused,
including from the Fleet owner. Turning it on is a deliberate edit to a file on
this machine, naming the sessions it applies to — and it can be withdrawn here,
by the person at this keyboard, without touching the other end.

What is not here
----------------
No `exec`. No shell. No path parameter. No generic "call any CloudCLI tool"
route. The route table in `fleet_mcp.node_protocol.ROUTES` is the whole surface,
and an unlisted route is refused *by name* before any dispatch happens, so
adding one is an edit to that tuple rather than a default a framework chose.

The grant that names no session
-------------------------------
`allow_send_to` is a list of sessions. It has one shape it cannot express, and
it is the shape the Fleet owner actually asked for: *any* session on this
machine, including one that does not exist yet. A named list cannot say that,
because it cannot name a session that has not been created — so keeping it
current means editing this file on every creation, and a list that has to be
edited to stay true stops being a record of what was authorized. The list that
was audited and the list in the file drift, silently, in the direction of
granting more than was read.

So there is a second grant here, `allow_all_sessions_for_owner`: a list of
*verbs*, empty by default, that the person at this keyboard may open for every
session on this device at once. It is separate from `allow_send_to` rather than
a wildcard entry inside it, because the two are read differently — the named
list is read against the sessions it covers, and a floor is read against the
machine. Which of the two admitted a given message is reported (`send_grant`)
and, for the floor, journalled, since a floor send is the only one that leaves
no trace in this binding.

It is not a claim about **who** the caller is. Nothing on this side can see a
remote principal: the signature proves a holder of the far end's secret, not a
person, and the Fleet host's scope-and-device decision was made on another
machine by a process this one cannot interrogate. A floor is therefore the
owner of *this* machine stating what may be driven *here* — the same kind of
statement `allow_send_to` is, made at a wider width. It is revocable by the
person who granted it, in this file, without the other end's cooperation; and
the vocabulary it can be written in is closed (`OWNER_FLOOR_VERBS`), so that
widening it is an edit to a tuple in this file rather than a word a caller or
an operator can invent. `bypassPermissions` is a CloudCLI session's own mode
and is not, and does not become, this.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

from ..errors import BAD_REQUEST, FleetRefusal
from ..identity import SessionKey
from ..local_roots import binding_candidates, refusal_if_closed_root
from ..node_protocol import (
    PROTOCOL,
    ROUTES,
    ReplayCache,
    SignatureError,
    error_payload,
    split_path_and_query,
    verify,
)
from ..providers.cloudcli import CloudCliProvider
from ..config import ProviderBinding

SCHEMA = "serveros/fleet-node.local/v1"

# Signature failures that mean "no authenticated peer here", as opposed to a
# peer whose request was malformed (wrong device, stale, replayed).
_PEER_REJECTIONS = frozenset(
    {"PROTOCOL_MISMATCH", "MISSING_DEVICE", "MISSING_SIGNATURE", "BAD_SIGNATURE"}
)

DEFAULT_CONFIG_PATHS = binding_candidates("fleet-node.local.yaml")

# What an owner floor may be a floor *for*. Closed on purpose, because of the
# shape of the failure it prevents. `allow_send_to` names sessions, so a typo in
# it refuses a send loudly at the session it names. A floor names none, so a
# floor that silently meant nothing would refuse every send with nothing to
# point at but a binding that reads as correct — and, in the other direction, an
# operator who wrote `"*"` or `"all"` or `"root"` and got the widest grant this
# file can express would have skipped every question this gate exists to ask.
# So a word outside this tuple is refused when the binding is read, not ignored
# when a caller arrives.
#
# Adding `"control"` here would NOT implement control: it would only let a
# binding claim it, and the verb still has to refuse until a route, a provider
# method and a scope exist for it. That is the intended order.
OWNER_FLOOR_VERBS: tuple[str, ...] = ("send",)


@dataclass
class NodeConfig:
    device_ref: str
    secret_anchor: Path
    cloudcli_endpoint: str
    cloudcli_credential_anchor: Path
    bind: str = "127.0.0.1"
    port: int = 3402
    connect_timeout: float = 5.0
    read_timeout: float = 45.0
    # Native session ids or full `device:<id>|<provider>|<id>` refs. Empty is
    # the default and means "no session on this machine may be sent to".
    allow_send_to: tuple[str, ...] = ()
    max_body_bytes: int = 1_000_000
    notes: str = ""
    # Owner-granted floors: verbs the person at this keyboard has granted for
    # EVERY session on this machine, the ones that exist and the ones that do
    # not yet. Deliberately a separate field from `allow_send_to` rather than a
    # wildcard entry inside it — see the module docstring, "The grant that names
    # no session". Empty is the default, and empty means exactly what this file
    # meant before the field existed.
    #
    # Withdrawing a floor is removing its verb here and restarting, the same
    # latency and the same place as every other binding in this system. Nothing
    # else reads this file, so nothing else can re-grant it.
    allow_all_sessions_for_owner: tuple[str, ...] = ()

    def secret(self) -> str | None:
        try:
            return self.secret_anchor.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def send_grant(self, key: SessionKey) -> str | None:
        """Which gate admits a send to `key`: `"named"`, `"owner-floor"`, `None`.

        The named list is asked first, so a session an operator wrote down stays
        attributed to that list even while a floor is open. Withdrawing a floor
        must not silently move a send that was named into the wider bucket, or
        the wider bucket's audit line would grow entries that were never the
        floor's to admit.

        `key` is always composed from *this* config's `device_ref` (see
        `Connector._key`), so neither gate can be satisfied on behalf of another
        device: a caller cannot name a foreign device into existence here.
        """
        if key.ref in self.allow_send_to or key.native_session_id in self.allow_send_to:
            return "named"
        if "send" in self.allow_all_sessions_for_owner:
            return "owner-floor"
        return None

    def send_default(self) -> str:
        """One sentence, for an operator, on a send to a session this file does
        not name. Reported rather than inferred: three distinct states, because
        "nothing is named" and "nothing may be sent" are not the same answer."""
        floor = "send" in self.allow_all_sessions_for_owner
        if floor and self.allow_send_to:
            return (
                "the sessions named in allow_send_to, and every other session on "
                "this device (owner floor: send)"
            )
        if floor:
            return "every session on this device (owner floor: send); none is named here"
        if self.allow_send_to:
            return "only the sessions named in allow_send_to"
        return "refused"

    def permits_send(self, key: SessionKey) -> bool:
        return self.send_grant(key) is not None


def _owner_floors(value: Any) -> tuple[str, ...]:
    """Read the owner floor, refusing anything this file cannot mean.

    Refused at load rather than dropped at use, for the reason in
    `OWNER_FLOOR_VERBS`: the failure a floor has to be protected from is not
    "an attacker widened it" but "nobody noticed it was never on". A binding
    that says something this connector cannot honour must fail where an operator
    is watching, which is startup, not at the first caller.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_all_sessions_for_owner must be a list of verbs, not a "
            f"{type(value).__name__}. It names no session, so there is no "
            f"per-session form of it to accept instead.",
        )
    unknown = [v for v in value if v not in OWNER_FLOOR_VERBS]
    if unknown:
        raise FleetRefusal(
            BAD_REQUEST,
            f"allow_all_sessions_for_owner names {unknown!r}, which is not a "
            f"floor this connector has. It knows exactly "
            f"{list(OWNER_FLOOR_VERBS)}. Refusing rather than ignoring: a floor "
            f"that silently meant nothing would look exactly like a floor that "
            f"was never needed, and this binding would still read as correct.",
        )
    # One grant repeated is still one grant; the list is read by a person, so
    # its shape is kept as written and only made hashable/stable.
    return tuple(dict.fromkeys(str(v) for v in value))


def load_node_config(path: str | Path | None = None) -> NodeConfig:
    if path is None:
        for candidate in DEFAULT_CONFIG_PATHS:
            expanded = Path(candidate).expanduser()
            if expanded.exists():
                path = expanded
                break
    if path is None:
        raise FleetRefusal(
            "BAD_REQUEST",
            "no connector binding found; looked for "
            + ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
            + ". The connector refuses to start rather than running with no "
            "device identity.",
        )
    source = Path(str(path)).expanduser()
    closed = refusal_if_closed_root(source)
    if closed:
        raise FleetRefusal(BAD_REQUEST, closed)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise FleetRefusal(BAD_REQUEST, f"connector binding not found at {source}") from None
    except yaml.YAMLError as exc:
        raise FleetRefusal(
            BAD_REQUEST, f"connector binding at {source} is not valid YAML: {exc}"
        ) from None
    if not isinstance(raw, dict):
        raise FleetRefusal("BAD_REQUEST", "connector binding must be a mapping")
    if raw.get("schema") != SCHEMA:
        raise FleetRefusal(
            "BAD_REQUEST",
            f"connector binding declares schema {raw.get('schema')!r}; this reads {SCHEMA!r}",
        )
    device_ref = str(raw.get("device_ref") or "")
    if not device_ref.startswith("device:"):
        raise FleetRefusal(
            "BAD_REQUEST",
            "`device_ref` must be this machine's typed fleet ref (`device:<id>`). "
            "A hostname would give two machines the same identity the moment one "
            "was renamed.",
        )
    return NodeConfig(
        device_ref=device_ref,
        secret_anchor=Path(str(raw["secret_anchor"])).expanduser(),
        cloudcli_endpoint=str(raw["cloudcli_endpoint"]),
        cloudcli_credential_anchor=Path(str(raw["cloudcli_credential_anchor"])).expanduser(),
        bind=str(raw.get("bind", "127.0.0.1")),
        port=int(raw.get("port", 3402)),
        connect_timeout=float(raw.get("connect_timeout", 5.0)),
        read_timeout=float(raw.get("read_timeout", 45.0)),
        allow_send_to=tuple(raw.get("allow_send_to") or ()),
        max_body_bytes=int(raw.get("max_body_bytes", 1_000_000)),
        notes=str(raw.get("notes") or ""),
        allow_all_sessions_for_owner=_owner_floors(raw.get("allow_all_sessions_for_owner")),
    )


class Connector:
    """The closed API, over an upstream CloudCLI gateway on this machine."""

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.replay = ReplayCache()
        self.provider = CloudCliProvider(
            ProviderBinding(
                device_ref=config.device_ref,
                kind="cloudcli-mcp-http",
                endpoint=config.cloudcli_endpoint,
                credential_anchor=config.cloudcli_credential_anchor,
                connect_timeout=config.connect_timeout,
                read_timeout=config.read_timeout,
                # Inert here — nothing above this line reads it — but carried
                # honestly: a binding that claimed `allow_send: True` on a
                # connector that would refuse every send would be a document
                # that disagrees with the machine it describes. Either gate
                # opening makes a send possible, so either one makes this true.
                allow_send=bool(config.allow_send_to or config.allow_all_sessions_for_owner),
            )
        )

    # -- helpers -----------------------------------------------------------

    def _secret(self) -> str:
        secret = self.config.secret()
        if not secret:
            raise FleetRefusal(
                "BAD_REQUEST",
                f"no node secret at {self.config.secret_anchor}; this connector "
                f"cannot verify anyone and will not answer",
            )
        return secret

    def _key(self, native: str) -> SessionKey:
        return SessionKey.compose(self.config.device_ref, "claude", native)

    @staticmethod
    def _upstream_code(exc: FleetRefusal) -> tuple[int, str]:
        """Map a CloudCLI refusal onto the node protocol's closed code set."""
        mapping = {
            "PROVIDER_UNAUTHORIZED": (502, "UPSTREAM_UNAUTHORIZED"),
            "PROVIDER_UNREACHABLE": (504, "UPSTREAM_UNREACHABLE"),
            "PROVIDER_TIMEOUT": (504, "UPSTREAM_UNREACHABLE"),
            "SESSION_NOT_FOUND": (404, "SESSION_NOT_FOUND"),
            "RUN_NOT_FOUND": (404, "RUN_NOT_FOUND"),
            # This machine's own refusal, lifted out of the generic 400 on
            # purpose: it is the one failure here whose fix is an edit to a file
            # on *this* device, and a caller that saw BAD_REQUEST would go
            # looking at its own arguments.
            "WRITE_NOT_GRANTED": (403, "WRITE_NOT_GRANTED"),
        }
        status, code = mapping.get(exc.code, (400, "BAD_REQUEST"))
        return status, code

    # -- routes ------------------------------------------------------------

    def route(self, method: str, target: str, body: bytes, headers: dict[str, str]):
        """Return (status, payload). Raises nothing a caller should see raw."""
        route = f"{method} {split_path_and_query(target)[0]}"
        if not self._is_declared_route(route):
            return 404, error_payload(
                "UNKNOWN_ROUTE",
                f"{route!r} is not one of the {len(ROUTES)} routes this connector "
                f"serves. It serves exactly: {list(ROUTES)}",
            )
        try:
            device_ref = verify(
                self._secret(),
                method=method,
                path=split_path_and_query(target)[0],
                body=body,
                headers=headers,
                expected_device_ref=self.config.device_ref,
                replay=self.replay,
            )
        except SignatureError as exc:
            # 401 is "this connector does not accept you as a peer at all"; 400
            # is "you are a peer and this particular message is not usable".
            # A caller that acted on one of these by retrying would be wrong to
            # retry the first and wrong to change its secret for the second.
            status = 401 if exc.code in _PEER_REJECTIONS else 400
            return status, error_payload(exc.code, exc.detail)

        try:
            return self._dispatch(method, target, body, device_ref)
        except FleetRefusal as exc:
            status, code = self._upstream_code(exc)
            return status, error_payload(code, exc.detail)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return 500, error_payload("INTERNAL", f"{type(exc).__name__}: {exc}")

    def _is_declared_route(self, route: str) -> bool:
        method, _, path = route.partition(" ")
        segments = [s for s in path.split("/") if s]
        for declared in ROUTES:
            declared_method, _, declared_path = declared.partition(" ")
            if declared_method != method:
                continue
            declared_segments = [s for s in declared_path.split("/") if s]
            if len(declared_segments) != len(segments):
                continue
            # A `{...}` segment is a placeholder for exactly one path part — the
            # session id, and nothing that could carry a path of its own.
            if all(seg.startswith("{") or seg == actual
                   for seg, actual in zip(declared_segments, segments)):
                return True
        return False

    def _dispatch(self, method: str, target: str, body: bytes, device_ref: str):
        path, query = split_path_and_query(target)
        # Percent-decoded *after* verification, never before: the signature
        # covers the target as it came off the wire, so decoding first would
        # make two different byte strings with one signature.
        parts = [urllib.parse.unquote(p) for p in path.split("/") if p]
        args = urllib.parse.parse_qs(query)
        payload: dict[str, Any] = {}
        if body.strip():
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FleetRefusal("BAD_REQUEST", f"body is not JSON: {exc}") from None
            if not isinstance(payload, dict):
                raise FleetRefusal("BAD_REQUEST", "body must be a JSON object")

        if parts == ["v1", "health"]:
            return 200, self._health()
        if parts == ["v1", "self"]:
            return 200, self._self(device_ref)
        if parts == ["v1", "sessions"]:
            return 200, self._sessions(args)
        if len(parts) == 3 and parts[:2] == ["v1", "sessions"]:
            return 200, self.provider.get_session(self._key(parts[2]))
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "read":
            return 200, self._read(parts[2], payload)
        if len(parts) == 4 and parts[:2] == ["v1", "sessions"] and parts[3] == "send":
            return 200, self._send(parts[2], payload)
        if len(parts) == 3 and parts[:2] == ["v1", "runs"]:
            return 200, self._run(parts[2], args)
        return 404, error_payload("UNKNOWN_ROUTE", f"{method} {path} is not routed")

    # -- handlers ----------------------------------------------------------

    def _health(self):
        started = time.monotonic()
        health = self.provider.health()
        measured = round((time.monotonic() - started) * 1000, 1)
        return {
            "device_ref": self.config.device_ref,
            "detail": health.detail,
            "reachable": health.reachable,
            "authorized": health.authorized,
            # The provider's own measurement when it took one; otherwise the
            # wall clock around it, which includes the connector's overhead.
            "latency_ms": health.latency_ms if health.latency_ms is not None else measured,
            "measured_ms": measured,
            "upstream_error_code": health.error_code,
            "upstream": {
                "server_name": health.server_name,
                "server_version": health.server_version,
                "tool_count": health.tool_count,
            },
        }

    def _self(self, device_ref: str):
        """What this connector is, and what it will refuse.

        Reported rather than assumed: an operator asking "why can't ChatGPT
        message my laptop" needs to see that the answer is a file on the laptop.
        """
        return {
            "device_ref": self.config.device_ref,
            "authenticated_as": device_ref,
            "protocol": PROTOCOL,
            "routes": list(ROUTES),
            "control": {
                "send_allow_list": list(self.config.allow_send_to),
                # Reported beside the named list rather than folded into it, and
                # as its own key rather than as a `"*"` entry inside it: the two
                # are read differently. "May this session be sent to" is answered
                # by the list; "may anything on this machine be" is answered
                # here, and an operator auditing the first needs to be able to
                # see which of the two actually admitted a message.
                "send_owner_floor": list(self.config.allow_all_sessions_for_owner),
                "send_default": self.config.send_default(),
            },
            "upstream_endpoint": self.config.cloudcli_endpoint,
            "credential_stays_here": True,
            "notes": self.config.notes,
        }

    def _sessions(self, args: dict[str, list[str]]):
        limit = int((args.get("limit") or ["20"])[0])
        project = (args.get("project") or [None])[0]
        state = (args.get("state") or [None])[0]
        summaries = self.provider.list_sessions(limit=limit, project=project, state=state)
        return {"sessions": [s.as_dict() for s in summaries], "total": len(summaries)}

    def _read(self, native: str, payload: dict[str, Any]):
        return self.provider.read_session(
            self._key(native),
            mode=str(payload.get("mode") or "latest"),
            limit=int(payload.get("limit") or 20),
            around_id=payload.get("aroundId"),
            before=payload.get("before"),
            after=payload.get("after"),
            cursor=payload.get("cursor"),
        )

    def _send(self, native: str, payload: dict[str, Any]):
        key = self._key(native)
        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise FleetRefusal("BAD_REQUEST", "message must be a non-empty string")
        grant = self.config.send_grant(key)
        if grant is None:
            raise FleetRefusal(
                "WRITE_NOT_GRANTED",
                f"this connector is not authorized to send to {key.ref}. Its "
                f"allow_send_to list is {list(self.config.allow_send_to)} and its "
                f"owner floor is "
                f"{list(self.config.allow_all_sessions_for_owner) or 'closed'}; "
                f"both live on this machine, not on the caller's.",
            )
        if grant == "owner-floor":
            # The only send this machine admits without naming the session, and
            # therefore the only one whose admission is explained nowhere in the
            # binding. The Fleet host records the send itself, but nothing over
            # there can record that this machine had not named the session, so
            # this line is what an operator reads to answer "what went out under
            # the floor, and when it started".
            #
            # The ref is the one the request line above already carries (see
            # `_Handler.log_message`); what is added here is the *gate*, not the
            # identity. Repeated rather than left implicit so that the pair of
            # lines cannot be mismatched by two concurrent handlers: the request
            # line is interleavable, this one is self-contained. One `write` call
            # for the same reason. No message body, no header, no signature.
            sys.stderr.write(
                f"connector: send admitted by the owner floor, session not named "
                f"in this binding: {key.ref}\n"
            )
        # The caller's idempotency key is not forwarded: CloudCLI has no such
        # concept, and passing one along would imply a guarantee this hop cannot
        # make. The replay guard is the Fleet surface's, above this line.
        return self.provider.send(key, message)

    def _run(self, run_id: str, args: dict[str, list[str]]):
        session = (args.get("session") or [None])[0]
        if not session:
            raise FleetRefusal("BAD_REQUEST", "`session` is required: a run id alone "
                                              "does not identify a run")
        wait = (args.get("wait") or ["0"])[0] in ("1", "true", "True")
        wait_seconds = float((args.get("waitSeconds") or ["0"])[0] or 0)
        return self.provider.get_run(self._key(session), run_id, wait=wait,
                                     wait_seconds=wait_seconds)


class _Handler(BaseHTTPRequestHandler):
    server_version = "serveros-fleet-node-connector/0.1.0"
    protocol_version = "HTTP/1.1"
    connector: Connector

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > self.connector.config.max_body_bytes:
            self.close_connection = True
            self._respond(413, error_payload("BAD_REQUEST", "body too large"))
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        try:
            status, payload = self.connector.route(self.command, self.path, body, headers)
        except FleetRefusal as exc:
            status, payload = 400, error_payload("BAD_REQUEST", exc.detail)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            status, payload = 500, error_payload("INTERNAL", f"{type(exc).__name__}: {exc}")
        self._respond(status, payload)

    do_GET = do_POST = _handle

    def log_message(self, fmt: str, *args: Any) -> None:
        # `log_request`/`log_error` hand us the whole request line, query string
        # and all. The query carries filter values the caller chose, and a log
        # line is a copy that outlives the request — so this writes the path and
        # stops. Nothing here logs a body, a header or a session id.
        path, _ = split_path_and_query(self.path or "")
        sys.stderr.write("connector: %s %s\n" % (self.command or "-", path))


def serve(config: NodeConfig, *, connector: Connector | None = None) -> ThreadingHTTPServer:
    connector = connector or Connector(config)
    handler = type("NodeHandler", (_Handler,), {"connector": connector})
    server = ThreadingHTTPServer((config.bind, config.port), handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    server.timeout = 30
    return server


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config_path = None
    if "--config" in argv or "-c" in argv:
        flag = "--config" if "--config" in argv else "-c"
        index = argv.index(flag)
        if index + 1 >= len(argv):
            sys.stderr.write("connector: --config needs a path\n")
            return 2
        config_path = argv[index + 1]
    try:
        config = load_node_config(config_path)
    except FleetRefusal as exc:
        sys.stderr.write(f"connector: {exc.code}: {exc.detail}\n")
        return 3
    if not config.secret():
        sys.stderr.write(f"connector: no secret at {config.secret_anchor}\n")
        return 3
    if not config.cloudcli_credential_anchor.exists():
        sys.stderr.write(
            f"connector: no CloudCLI credential at {config.cloudcli_credential_anchor}\n"
        )
        return 3
    server = serve(config)
    sys.stderr.write(
        f"connector: {config.device_ref} serving {len(ROUTES)} routes on "
        f"http://{config.bind}:{config.port}\n"
        f"connector: upstream CloudCLI {config.cloudcli_endpoint}\n"
        f"connector: send allow list "
        f"{list(config.allow_send_to) or 'empty'}"
        f"; owner floor {list(config.allow_all_sessions_for_owner) or 'closed'}"
        f" — a session not named here: {config.send_default()}\n"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("connector: interrupted\n")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
