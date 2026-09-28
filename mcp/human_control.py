#!/usr/bin/env python3
"""HumanControlProvider V1 — `provider:botmux-human-control`, transport Feishu.

§4 draws the line and this module holds it:

    owns   transport ingress · human identity evidence · conversation context ·
           approval transport · notification transport · interactive rendering
    not    policy · Agent Session truth · Fleet truth · runtime selection ·
           Ability resolution · business orchestration

Everything here is transport. Where the transport cannot do something, the
answer is UNSUPPORTED or UNKNOWN with a reason — never a private implementation
that reaches around the provider. §4 is explicit that filling a gap by reading
`~/.botmux/data/*`, a private state file, or a tmux pane is the failure mode,
not the fallback.

What was measured on this device before any of it was written
------------------------------------------------------------
Not assumed, and not remembered — asked, from this process tree:

    GET  /api/sessions   -> 200, sessions with sessionId/chatId/chatType/status
    GET  /api/bots       -> 200 with the dashboard cookie, bots with `online`
    GET  /api/events     -> 404 not_found_yet
    GET  /api/messages   -> 404 not_found_yet
    GET  /api/approvals  -> 404 not_found_yet

    botmux actor current --json
        -> {"schema":"botmux.current-actor.v2",
            "status":"blocked","error":"current_actor_unverified"}

**The three 404s are dated, and the second pass on 2026-09-15 did not reproduce
them.** Every `/api/*` path now answers `401 Token expired`, including a
deliberately bogus one (`/api/definitely-not-a-real-route-xyz`), which is the
control that makes the measurement readable: the auth layer precedes routing, so
a 401 carries no information about whether a route exists, and neither the old
404 claim nor its negation may be asserted from here. The states resting on that
probe were re-derived from the **CLI** surface instead — which is documented,
reachable, and measured today — and two of them moved. See `capabilities()`.

The blocked actor answer is the interesting one, and it is why identity is the
only capability here whose live state is UNKNOWN rather than UNSUPPORTED. The
surface exists and is documented; BotMux declined to verify the sender of THIS
process, correctly, because this process is not inside a BotMux turn. Whether
it verifies inside one is not something this module may claim to know, so it
does not claim it. A provider status of BLOCKED produces no actor and no
context — see `Report.as_report()` below, which has no field in which to put an
actor it did not get.

The SUCCESS document was measured later, and it is narrower than this module
originally assumed. Read off upstream master and confirmed against the 3.23.1
binary running on this device, the verified answer is exactly

    {"schema":"botmux.current-actor.v2","status":"verified","actor":{"email":…}}

— three fields. It carries no `chatId`, no `messageId` and no `user`, so the two
locators the trusted-context contract requires of a provider (`conversation_ref`
and `message_ref`, both `required: true` in `context/actor-context-contract.yaml`)
have no producer on this surface at all. That is stated here because the earlier
version of this module read `status == "ok"` and pulled the identity from `user`
and the locations from `chatId`/`messageId` — every one of them a field the data
layer never produced, which is the failure this module's own docstring names
two paragraphs up. The consequence was not cosmetic: the report was missing
required evidence, so `build` refused and no context could ever be minted, on
any turn, verified or not. The reader now consumes the measured shape and
refuses by field name; what remains is a BotMux change, requested as item 3 of
`artifacts/srv-botmux-gate-20260928/UPSTREAM-PROPOSAL.md`, because no client-side
substitute is trustworthy — the environment is writable by whoever spawns the
probe.

`attach_context` is the other half of that sentence, and the producer §5 was
missing. It carries a VERIFIED report to serverosd over the socket and comes
back with the `context_ref` the control plane resolves the actor from. It
attaches only what this process observed — there is no parameter for evidence —
and it stamps the pid that ran the probe, which the daemon checks against the
kernel's answer for the connection. On this device, from a process that is not
inside a BotMux turn, the honest outcome is `attached: false` with
`state: BLOCKED`: the probe declined, so there is nobody for a context to be
about, and this module does not invent one.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_PROVIDER_ID = "provider:botmux-human-control"

# The four states a HumanControl capability can be in — the Agent Session
# vocabulary, reused rather than given a second spelling. `UNKNOWN` is a real
# answer and the one that matters most here: it is what an installed, callable,
# answering surface produces when it declines to verify.
SUPPORT_STATES = ("SUPPORTED", "UNSUPPORTED", "UNKNOWN")


class HumanControlError(RuntimeError):
    pass


class HumanControlUnsupported(HumanControlError):
    """The transport has no way to do this. Not a missing implementation."""


def support(state: str, reason: str | None = None) -> dict:
    if state not in SUPPORT_STATES:
        raise HumanControlError(f"unknown support state {state!r}")
    return {"state": state, "reason": reason}


# ---------------------------------------------------------------------------
# The report a provider gives back. This is the only shape that can become a
# trusted actor context, and it has no field for an actor the provider did not
# establish — which is a structural refusal, not a checked one.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActorReport:
    """What a HumanControlProvider established about the human behind an event.

    `actor_id` is None whenever `status` is not VERIFIED, and
    `context/actor.py` refuses to build a context from a report with no
    actor_ref. So a provider that could not verify produces no context, and the
    refusal happens at the constructor of the record rather than at each of the
    call sites that would have to remember to check.
    """

    provider_id: str
    transport: str
    status: str                      # VERIFIED | BLOCKED | UNSUPPORTED
    actor_id: str | None = None
    transport_identity: str | None = None
    conversation_ref: str | None = None
    message_ref: str | None = None
    observed_at: str | None = None
    detail: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def provenance(self) -> str:
        return "FACT" if self.status == "VERIFIED" else "UNKNOWN"

    def as_report(self) -> dict:
        """The provider-report shape `context/actor.py` consumes.

        Deliberately NOT tolerant. A BLOCKED report that carried an actor
        anyway — because the caller filled in a default, or because a client
        kept the last known name handy — would be exactly the forged-identity
        model §5 forbids, arriving through the one door that is supposed to be
        trustworthy. There is no branch here that supplies an actor; the
        downstream builder raises instead.
        """
        return {
            "human_control_provider": self.provider_id,
            "transport": self.transport,
            "transport_identity": self.transport_identity,
            "conversation_ref": self.conversation_ref,
            "message_ref": self.message_ref,
            "observed_at": self.observed_at,
            "provider_status": self.status,
            "provenance": self.provenance,
            "actor_ref": f"human:{self.actor_id}" if self.actor_id else None,
        }


def attach_context(provider: HumanControlProvider, *, daemon=None,
                   pid: int | None = None) -> dict:
    """Present what this process observed to serverosd, and get a context ref.

    This is the producer half of §5, and until it existed §5's only doorway had
    no caller anywhere in the repository: `agent.context.attach` was implemented,
    documented and tested, and nothing ever knocked. The consequence was not a
    cosmetic gap — every mutating Agent Session action (`session.create`,
    `session.send`, `session.resume`, `session.stop`, `session.handoff`) requires
    a `context_ref`, so the entire control plane was reachable only by tests that
    built the report by hand and passed the peer dict themselves.

    The three things this function must get right, and why each is not a choice:

    * **It attaches what it observed, not what it was told.** There is no
      `report` parameter. A caller that could hand in evidence would be exactly
      the forged-identity model §5 forbids — the door is meant to be trustworthy
      precisely because only the process that ran the probe may walk through it.

    * **Not even the conversation is the caller's to choose.** `identify` takes
      a `conversation_ref` and prefers it over what the probe answered, which is
      right for a caller asking a question about a conversation it already has.
      It is wrong here: the record says which turn this identity was observed
      in, and a caller able to write that field can steer an audit trail. So the
      mint passes nothing. There is no non-forgeable replacement for it either:
      the actor document carries no locator, an environment-derived one would be
      the caller's own choice, and the only per-turn-looking variable a client
      can read is frozen at process exec. So a probe that names no conversation
      produces a report missing a required field, which `build` refuses — no
      context, rather than a context with a plausible-looking location. This is
      item 3 of the upstream proposal: the daemon already verified a turn, so it
      should publish the locators it verified rather than leave every client to
      guess at them.

    * **The pid is this process, because this process ran the probe.** `identify`
      runs `botmux actor current --json` as a child of the calling process, so
      "who observed this" and "who is presenting this" are the same process by
      construction. `provider_pid` is that answer, and the daemon checks it
      against `SO_PEERCRED` — the kernel's answer, not ours. Supplying a
      different pid is not a bypass; it is the refusal at
      `runtime/daemon.py#_r_agent_context_attach` that names both pids.

    * **A provider that did not verify produces nothing.** BLOCKED and
      UNSUPPORTED return here without an attach attempt, and `detail` is the
      provider's own sentence. A refused context is not an empty one:
      `actor_ref` is None and no ref is minted, because a context is always
      ABOUT somebody.

    Four outcomes, kept apart because each sends a reader somewhere different:

      attached, with a `context_ref`   the control plane will resolve this actor
      BLOCKED / UNSUPPORTED            no human was identified; nobody to mint
      REFUSED                          serverosd answered and said no; read the
                                       reason, it names which mechanism
      UNREACHABLE                      serverosd did not answer at all

    `pid` exists so a test can drive the wrong-observer refusal through this
    path rather than around it.
    """
    report = provider.identify()
    base = {"provider": report.provider_id, "transport": report.transport,
            "attached": False, "actor_ref": None}
    if report.status != "VERIFIED":
        return {**base, "state": report.status, "detail": report.detail,
                "note": ("no context was minted and no attach was attempted: this "
                         "process was not identified as a human, and ServerOS "
                         "mints a context about somebody or mints nothing")}

    node_mcp = _node_mcp()
    client = daemon if daemon is not None else node_mcp.DaemonClient()
    evidence = {**report.as_report(),
                "provider_pid": os.getpid() if pid is None else int(pid)}
    try:
        result = client.call("agent.context.attach", {"report": evidence})
    except node_mcp.DaemonRefused as exc:
        return {**base, "state": "REFUSED", "observed_by_pid": evidence["provider_pid"],
                "detail": str(exc),
                "note": ("serverosd answered and said no. The refusals are the "
                         "mechanism, not an obstacle: each one names which of "
                         "observed-by-the-wrong-process, forged payload, or no "
                         "identified human it was, and this one is in `detail`.")}
    except node_mcp.DaemonUnavailable as exc:
        return {**base, "state": "UNREACHABLE", "observed_by_pid": evidence["provider_pid"],
                "detail": str(exc),
                "note": ("serverosd did not answer, so nothing was presented and "
                         "nothing was minted. This is a different repair from a "
                         "refusal: the control plane is not there.")}

    return {"provider": report.provider_id, "transport": report.transport,
            "attached": True, **result}


# ---------------------------------------------------------------------------
# What the ingress produced, written down
# ---------------------------------------------------------------------------

# The name of the record, resolved through `runtime/paths.py` rather than
# written as a path. Under `logs/` and not under `state/serveros/`: this is an
# append-only account of attempts, it is nobody's authority, and the whole
# reason `agent-control/` can be trusted as the authority for contexts is that
# nothing else claims to be. A record that sat inside that namespace would be a
# second thing to read before answering "what does this node believe about
# actors", which is the shape §22 rules out.
INGRESS_RECORD_REF = "logs/human-control/ingress.jsonl"


def ingress_record_path(ref: str = INGRESS_RECORD_REF) -> Path:
    """Where this node's ingress record goes, or an error saying why not.

    Resolved through the root contract, so the same reference answers with the
    same path from any working directory and no caller has to know that this
    node keeps its logs under `DATA_ROOT`. An unbound node raises: a record
    written to `{DATA_ROOT}/logs/...` is a plausible-looking path on a machine
    nobody identified, which is the value this contract refuses to produce.
    """
    paths = _load("serveros_runtime_paths", _REPO / "runtime" / "paths.py")
    resolved = paths.Paths()
    if not resolved.bound:
        raise HumanControlError(
            "this node's roots are UNBOUND "
            f"({', '.join(resolved.unconfigured)}), so there is no declared "
            "place for the ingress record and none is invented")
    return Path(resolved.resolve(ref)["path"])


def record_attempt(result: dict, *, path: Path) -> dict:
    """Append one line saying what an attach attempt produced. Never who.

    This exists because the outcome was being thrown away. The mount that
    answers a Feishu turn ran `--attach` with its output redirected to
    `/dev/null` and `|| true` after it, so every failure mode — a provider that
    identified nobody, a payload serverosd refused, a daemon that was not
    there — arrived as the same observable: nothing at all. On 2026-09-28 a
    real human message did arrive at the botmux session, the store still read
    `contexts: {}`, and there was no way to tell from this node which of the
    four outcomes it had been. That is the failure this function removes.

    **It records no identity, and the omission is the design.** The record is
    not evidence and must never become a way to read back who spoke: a file an
    attachment could be forged into would be a second identity door, and §5
    allows exactly one. So `actor_id` is never written — what is written is
    whether one was established (`actor_identified`), which is the part a
    diagnosis needs.

    Failure to write is returned, not raised. An ingress whose record could not
    be appended has still done whatever it did, and a hook that turned a
    logging problem into a failed turn would trade one silence for another.
    """
    line = {
        "schema": "serveros/human-control-ingress.v1",
        "at": datetime.now().astimezone().isoformat(),
        "provider": result.get("provider"),
        "transport": result.get("transport"),
        "attached": bool(result.get("attached")),
        "state": result.get("state", "ATTACHED" if result.get("attached") else None),
        "actor_identified": bool(result.get("actor_ref")),
        "detail": result.get("detail"),
        "context_ref": result.get("context_ref"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, sort_keys=True) + "\n")
    except OSError as exc:
        return {"recorded": False, "path": str(path), "detail": str(exc)}
    return {"recorded": True, "path": str(path), "state": line["state"]}


def _node_mcp():
    """`mcp/node_mcp.py`, loaded under the name `mcp/agent_mcp.py` gives it.

    One copy, not two: a second copy would give this module a different
    `DaemonClient` — and therefore a different `DaemonRefused`, which is caught
    by name below and would slip through as its parent. The two surfaces also
    share the socket they talk to, so there is nothing to gain by not sharing
    the class that talks to it.
    """
    return _load("serveros_mcp_node_shared", _REPO / "mcp" / "node_mcp.py")


class HumanControlProvider(Protocol):
    """§4's interface. Six responsibilities, one of them optional in v1."""

    provider_id: str

    def capabilities(self) -> dict: ...
    def observe(self, *, device_ref: str) -> dict: ...
    def conversations(self, *, device_ref: str) -> list[dict]: ...
    def identify(self, *, conversation_ref: str | None = None,
                 message_ref: str | None = None) -> ActorReport: ...
    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict: ...
    def request_approval(self, *, conversation_ref: str, question: dict,
                         idempotency_key: str) -> dict: ...


# ---------------------------------------------------------------------------
# BotMux / Feishu
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "http://127.0.0.1:7891"
ACTOR_SCHEMA = "botmux.current-actor.v2"

# The one word BotMux uses to say it verified this turn's sender — MEASURED off
# the shipped artifact, not assumed. The daemon builds the success document as
#     { ok: true, document: { schema, status: 'verified', actor: { email } } }
# and `src/cli.ts` writes that document verbatim. This is the shape in upstream
# master AND in the 3.23.1 binary running on this device; the previous reader
# required `status == 'ok'`, a literal this surface has never produced (the
# `status:"ok"` strings in the binary all belong to other subsystems — host
# executor, cost analysis, contact profile — and none is an actor document).
# A reader demanding `'ok'` refuses every genuine verification, which is the
# failure this module's own docstring names: a declaration layer reading a field
# the data layer never produced.
ACTOR_VERIFIED_STATUS = "verified"

# Where the daemon puts the actor it resolved. What crosses this boundary is an
# enterprise email, not the `ou_` open id: open ids are app-scoped, so the
# daemon reduces the caller's open id to the identity every app agrees on and
# publishes only that. Reading a name out of some other field would be reading
# an identity this surface never published.
ACTOR_IDENTITY_FIELD = "email"

# BotMux answers a refused turn with `status: 'blocked'` plus an `error`. The
# upstream proposal at artifacts/srv-botmux-gate-20260928/UPSTREAM-PROPOSAL.md
# adds a `reason` beside them (which of the three refusal layers answered). It is
# optional and absent today; when present it is copied into `detail` so the
# attribution reaches the person reading the ingress record instead of stopping
# at the machine-readable document. Bounded, because it is read for display.
ACTOR_REASON_MAX = 64

# Neither locator is read from the environment, and the reason is BotMux's own
# security model rather than a preference. BotMux does export `BOTMUX_CHAT_ID`
# and `BOTMUX_TURN_ID` into the CLI it spawns, but its actor gate never reads a
# peer's environment: it resolves the caller from the TCP socket inode and walks
# the process ancestry. That is deliberate, because an environment is writable
# by whoever spawns the process, so a value taken from it is a claim by the
# caller rather than evidence about the caller. ServerOS adopting
# `$BOTMUX_CHAT_ID` as `conversation_ref` would invert that model: any process
# in the turn — including the model itself, which can run this probe with any
# environment it likes — could then choose which conversation an audit record
# names, and could mint a fresh context per event by varying the field.
#
# The per-turn variable is worse still, and this half is measured rather than
# argued. On Linux `/proc/<pid>/environ` is the environment as of exec, so a
# long-lived CLI cannot have it updated: the claude CLI serving a BotMux session
# on this device had been up 32 hours and still carried the turn id it was
# spawned with. Substituting `BOTMUX_TURN_ID` for `message_ref` would give every
# turn after the first the SAME message reference — one message authorising many
# actions, which is the single thing `message_ref` exists to prevent.
#
# So both locators stay unsourced, and `build` refuses by name. That refusal is
# not a gap left open: it is the honest report of a BotMux surface that verifies
# a turn and publishes nothing that identifies it. See item 3 of
# artifacts/srv-botmux-gate-20260928/UPSTREAM-PROPOSAL.md.


def _document_locator(raw: dict, field: str) -> str | None:
    """A locator the daemon published in the actor document, or None.

    None is not a fallback value here — it is the answer "this surface does not
    publish that", which is what the mint then refuses by name. Returning an
    empty string would be indistinguishable from a locator and would reach
    `build` as a present-but-blank field.
    """
    value = raw.get(field)
    if not isinstance(value, str):
        return None
    return value.strip() or None


@dataclass(frozen=True)
class BotMuxHumanControlConfig:
    base_url: str = DEFAULT_BASE_URL
    dashboard_token: str | None = None
    timeout: float = 5.0
    actor_command: tuple[str, ...] = ("botmux", "actor", "current", "--json")

    @classmethod
    def from_env(cls) -> "BotMuxHumanControlConfig":
        return cls(
            base_url=os.environ.get("SERVEROS_BOTMUX_URL", cls.base_url).rstrip("/"),
            dashboard_token=os.environ.get("SERVEROS_BOTMUX_DASHBOARD_TOKEN") or None,
            timeout=float(os.environ.get("SERVEROS_BOTMUX_TIMEOUT", "5")),
        )


class BotMuxHumanControlProvider:
    """HumanControlProvider over BotMux's documented surfaces.

    Two of them, and they are different kinds of thing:

      * the Dashboard HTTP API, for what the transport KNOWS — conversations,
        sessions, bots — and for carrying a message back out;
      * `botmux actor current --json`, for what the transport VERIFIED about
        the sender. This one is a CLI, deliberately: it is bound to the process
        tree of the turn it is asked from, and it refuses outside one. That
        refusal is the security property, not an inconvenience to route around.
    """

    provider_id = _PROVIDER_ID
    transport = "feishu"

    def __init__(self, config: BotMuxHumanControlConfig | None = None, *,
                 clock=None, runner=None) -> None:
        self.config = config or BotMuxHumanControlConfig.from_env()
        self._clock = clock
        # Injected so the identity path is testable without a BotMux turn.
        # A default that shelled out at import time would make every test of
        # this class depend on the host's BotMux version.
        self._runner = runner or self._run_actor_command

    def _now(self) -> str:
        if self._clock is not None:
            return self._clock()
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    # -- HTTP --------------------------------------------------------------

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 require_auth: bool = False) -> dict:
        token = self.config.dashboard_token
        if require_auth and not token:
            raise HumanControlError(
                "BotMux Dashboard authentication is required to carry a message out; "
                "inject SERVEROS_BOTMUX_DASHBOARD_TOKEN into the provider process")
        headers = {"Accept": "application/json"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Cookie"] = f"botmux_dashboard_token={token}"
        request = Request(self.config.base_url + path, data=payload,
                          headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                data = json.loads(response.read().decode() or "{}")
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode() or "{}")
            except Exception:
                detail = {}
            reason = detail.get("errorCode") or detail.get("error") or exc.reason
            raise HumanControlError(
                f"BotMux {method} {path} refused ({exc.code}): {reason}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise HumanControlError(
                f"BotMux {method} {path} unavailable: {type(exc).__name__}: {exc}") from exc
        if not isinstance(data, dict):
            raise HumanControlError(f"BotMux {method} {path} returned a non-object")
        return data

    # -- identity ----------------------------------------------------------

    def _run_actor_command(self) -> dict:
        """Ask BotMux who the sender of this turn is. Never guess.

        The command is documented and bound to the caller's process tree. Its
        output is JSON with its own schema, and the schema is checked before
        the payload is read: a changed shape is a refusal, because reading
        `user` out of a document that no longer has one yields None, and None
        at the call site looks exactly like "no user" rather than "this is not
        the document I think it is".
        """
        try:
            completed = subprocess.run(
                list(self.config.actor_command), capture_output=True, text=True,
                timeout=self.config.timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"schema": None, "status": "unsupported",
                    "error": f"{type(exc).__name__}: {exc}"}
        text = (completed.stdout or "").strip()
        if not text:
            return {"schema": None, "status": "blocked",
                    "error": "botmux actor current produced no output",
                    "stderr": (completed.stderr or "").strip()[:300]}
        try:
            data = json.loads(text)
        except ValueError:
            return {"schema": None, "status": "blocked",
                    "error": "botmux actor current produced non-JSON output"}
        if not isinstance(data, dict):
            return {"schema": None, "status": "blocked",
                    "error": "botmux actor current produced a non-object"}
        return data

    def identify(self, *, conversation_ref: str | None = None,
                 message_ref: str | None = None) -> ActorReport:
        """What the transport verified about the human behind this turn.

        Three outcomes, kept apart:

          VERIFIED     BotMux named an enterprise user it verified
          BLOCKED      the surface answered and declined to verify this process
          UNSUPPORTED  the surface is not there at all

        Note that `status: blocked` arrives with exit code 0. A caller that
        read the exit code — or that read the absence of a `user` field — would
        have recorded a successful verification of nobody, which is the exact
        failure this repo keeps finding: a declaration layer reading a field
        that the data layer never produced.
        """
        raw = self._runner()
        observed_at = self._now()
        schema = raw.get("schema")
        status = str(raw.get("status") or "").lower()

        if schema is None:
            return ActorReport(
                provider_id=self.provider_id, transport=self.transport,
                status="UNSUPPORTED", observed_at=observed_at,
                conversation_ref=conversation_ref, message_ref=message_ref,
                detail=str(raw.get("error") or "the actor surface is unavailable"),
                raw=raw)

        if schema != ACTOR_SCHEMA:
            return ActorReport(
                provider_id=self.provider_id, transport=self.transport,
                status="BLOCKED", observed_at=observed_at,
                conversation_ref=conversation_ref, message_ref=message_ref,
                detail=(f"botmux actor current answered schema {schema!r}, not "
                        f"{ACTOR_SCHEMA!r}; refusing to read identity out of a "
                        "document whose shape has changed"),
                raw=raw)

        if status != ACTOR_VERIFIED_STATUS:
            reason = str(raw.get("reason") or "").strip()[:ACTOR_REASON_MAX]
            return ActorReport(
                provider_id=self.provider_id, transport=self.transport,
                status="BLOCKED", observed_at=observed_at,
                conversation_ref=conversation_ref, message_ref=message_ref,
                detail=(f"BotMux reports status {status or 'unknown'!r}"
                        + (f" ({raw.get('error')})" if raw.get("error") else "")
                        + (f" [{reason}]" if reason else "")),
                raw=raw)

        # Only reached when BotMux itself said it verified this turn. The actor
        # is required at this point: a verification carrying no identity is a
        # contradiction, and resolving it in favour of "no actor" here would
        # make a broken provider look like a private turn.
        actor = raw.get("actor")
        actor_id = ""
        if isinstance(actor, dict):
            actor_id = str(actor.get(ACTOR_IDENTITY_FIELD) or "").strip()
        if not actor_id:
            return ActorReport(
                provider_id=self.provider_id, transport=self.transport,
                status="BLOCKED", observed_at=observed_at,
                conversation_ref=conversation_ref, message_ref=message_ref,
                detail=(f"BotMux reported {ACTOR_VERIFIED_STATUS!r} with no "
                        f"actor.{ACTOR_IDENTITY_FIELD}"),
                raw=raw)

        # The document answers about a sender, and today it carries no locator
        # — `chatId` / `messageId` are the names BotMux already uses for these
        # two things on its other documented surface (`/api/sessions`), so they
        # are the names requested in item 3 of the upstream proposal and the
        # names read here. Reading them from the document and not from the
        # environment is the whole point: the document reaches a client only
        # through the daemon's own turn gate, so its contents are the daemon's
        # statement, while `$BOTMUX_CHAT_ID` is writable by whoever spawned the
        # process (see the note above the constants).
        #
        # Until BotMux publishes them the lookup finds nothing, and that is the
        # honest end state rather than a gap: a report with no locator is
        # refused by `build` **by field name**, instead of the mint inventing a
        # location or being handed one by the caller it is about to authorize.
        return ActorReport(
            provider_id=self.provider_id, transport=self.transport,
            status="VERIFIED", actor_id=actor_id,
            transport_identity="botmux-verified-enterprise-user",
            conversation_ref=(conversation_ref
                              or _document_locator(raw, "chatId")),
            message_ref=(message_ref
                         or _document_locator(raw, "messageId")),
            observed_at=observed_at, raw=raw)

    # -- transport ingress -------------------------------------------------

    def conversations(self, *, device_ref: str) -> list[dict]:
        """Conversations BotMux knows about, from its sessions.

        A conversation here is a chat, and it is read from `/api/sessions`
        because that is the only documented surface that names one. Nothing
        about message CONTENT is read — §24 forbids message bodies in tool
        results, and no transcript is fetched anywhere in this module.
        """
        rows = self._request("GET", "/api/sessions").get("sessions") or []
        if not isinstance(rows, list):
            raise HumanControlError("/api/sessions has no list-valued sessions field")
        seen: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            chat_id = str(row.get("chatId") or "").strip()
            if not chat_id:
                continue
            entry = seen.setdefault(chat_id, {
                "conversation_ref": chat_id,
                "transport": self.transport,
                "chat_type": row.get("chatType"),
                "bot_ids": [],
                "session_refs": [],
            })
            bot_id = str(row.get("larkAppId") or "").strip()
            if bot_id and bot_id not in entry["bot_ids"]:
                entry["bot_ids"].append(bot_id)
            session_id = str(row.get("sessionId") or "").strip()
            if session_id:
                entry["session_refs"].append(session_id)
        return [seen[k] for k in sorted(seen)]

    # -- transport egress --------------------------------------------------

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str, bot_id: str | None = None) -> dict:
        """Carry a ServerOS sentence into the conversation.

        Reuses `/api/trigger` — the documented way to put an instruction into a
        BotMux session — rather than any BotMux-internal send path. The text is
        ServerOS's; how it is rendered is BotMux's. That split is §19: ServerOS
        produces semantic events, the provider decides what they look like.

        `envelope.trusted` is False and stays False. This message is ServerOS
        speaking to a human, not a human speaking to ServerOS, and a transport
        that marked its own outbound traffic trusted would let a notification
        be quoted back as evidence of what a person said.
        """
        target_bot = bot_id
        if not target_bot:
            bots = self._request("GET", "/api/bots", require_auth=True).get("bots") or []
            online = [b for b in bots if isinstance(b, dict) and b.get("online") is True]
            if not online:
                raise HumanControlError(
                    "no online bot to carry a notification; a notification to "
                    "nobody is not a notification")
            target_bot = online[0].get("larkAppId")
        body = {
            "source": {"type": "webhook"},
            "target": {"kind": "turn", "botId": target_bot},
            "instruction": text,
            "envelope": {"format": "text", "sourceName": "serveros",
                         "trusted": False,
                         "conversationId": conversation_ref},
            "options": {"asyncReturnSessionId": True,
                        "idempotencyKey": idempotency_key},
        }
        data = self._request("POST", "/api/trigger", require_auth=True, body=body)
        return {
            "provider": self.provider_id,
            "transport": self.transport,
            "conversation_ref": conversation_ref,
            "delivered": bool(data.get("triggerId") or data.get("async")),
            "trigger_id": data.get("triggerId"),
            "idempotent": bool(data.get("idempotent")),
            "note": ("BotMux renders this; ServerOS supplied a sentence and a "
                     "semantic event, not a card"),
        }

    def request_approval(self, *, conversation_ref: str, question: dict,
                         idempotency_key: str) -> dict:
        """Ask a person to approve something.

        UNSUPPORTED, and this is a capability limitation rather than a missing
        implementation — §6 says to mark it as one and not to improvise.

        The reason is specific and checkable: the approval needs an ANSWER
        bound to the question, and BotMux's documented surface has no way to
        deliver one. `/api/sessions` is read-only; `/api/events`,
        `/api/messages` and `/api/approvals` are 404. The only inbound path is
        a human typing into the chat, and ServerOS has no documented way to
        receive that as a machine-readable event. What it can do is read the
        question out of the conversation, which is not an approval transport —
        it is a person hoping the agent notices.

        A plain-text question CAN be delivered (see `notify`). What cannot be
        delivered is the binding: an answer that names the request fingerprint
        it is about. Sending the question while calling the capability
        SUPPORTED is exactly the shape §6 forbids — a live approval that is not
        one — so the two are separated here: the notification is real, the
        approval channel is not.
        """
        raise HumanControlUnsupported(
            "BotMux v3.18 can DELIVER a question (`botmux ask buttons --options "
            "\"a,b\"` returns the chosen key) and cannot be shown to return an "
            "answer BOUND to a request fingerprint: the question is delivered, "
            "and nothing carries the answer back to this process, which does not "
            "run inside a BotMux turn and has no surface to receive one on. An "
            "unbound key is not an approval — it cannot be shown to be about "
            "this request, and §AA forbids treating a delivered message as one. "
            "So this provider does not carry approvals, and ServerOS's approval "
            "contract is implemented and tested independently of this "
            "transport.")

    # -- what this provider can do ----------------------------------------

    def capabilities(self) -> dict:
        """The six responsibilities, each with its own state and its own reason.

        Read this as the honest answer to "how much of Human Control V1 is
        live", with the two UNKNOWNs distinguished from the two UNSUPPORTEDs:
        an UNSUPPORTED transport has no way to do it, and an UNKNOWN one might
        and this process cannot establish it.
        """
        return {
            "provider": self.provider_id,
            "transport": self.transport,
            "capabilities": {
                "human.identity.verify": support(
                    "UNKNOWN",
                    "`botmux actor current --json` (schema botmux.current-actor.v2) is "
                    "documented and callable, and verifies only inside the process tree "
                    "of a BotMux turn. Measured from a non-turn process on this device it "
                    "answers status=blocked, error=current_actor_unverified. Whether it "
                    "verifies inside a turn is unverified from here, so it is not claimed. "
                    "Re-read 2026-09-28 against the binary rather than the record, and it "
                    "says less than a first pass made it say: the gate is "
                    "`managedTurnOrigin.callerOpenId` starting `ou_` plus a non-empty "
                    "`managedTurnOrigin.capability`, and `managedTurnOrigin` is in-memory "
                    "session state the daemon publishes per turn. It is never written to "
                    "the session row, so the three fields a record-reader looks for are "
                    "absent there BY DESIGN, and their absence is not evidence about a "
                    "turn. Nor is this session short of what the lookup needs: its "
                    "`turnReplyContexts` carries a caller per turn. So the honest reading "
                    "is narrower than 'it cannot verify here' — this is an in-turn, "
                    "in-memory check that no out-of-turn process can evaluate, and this "
                    "process is out of turn. It stays UNKNOWN rather than UNSUPPORTED for "
                    "the same reason it always did: the surface exists and is callable, "
                    "and what nobody has watched is one turn. See docs/human-control.md "
                    "§the ingress."),
                "human.conversation.context": support(
                    "SUPPORTED",
                    "BotMux /api/sessions reports chatId, chatType and sessionId per session"),
                "human.transport.ingress": support(
                    "UNKNOWN",
                    "BotMux ingests Feishu messages, and nothing pushes them to a "
                    "ServerOS process. A PULL surface is documented and reachable — "
                    "`botmux history --session-id <id>` and `botmux quoted`, both of "
                    "which accept a session id from outside a turn — so the transport "
                    "is not absent, it is unadapted, and this provider reads no "
                    "inbound content at all. Re-measured 2026-09-15 from a non-turn "
                    "process: the earlier /api/events and /api/messages 404s are NOT "
                    "reproducible, because every /api/* path including a deliberately "
                    "bogus one now answers 401 Token expired — the auth layer precedes "
                    "routing, so no route-existence claim can be made from here"),
                "human.approval.transport": support(
                    "UNKNOWN",
                    "a documented surface DELIVERS a decision question and RETURNS an "
                    "answer: `botmux ask buttons [--multi] --options \"a,b\" \"<q>\"` "
                    "hands the question to Feishu as a card and returns the chosen key, "
                    "and `send --plugin-card-action <plugin-id>` admits a declared "
                    "plugin's callback action. Neither has been observed to reach a "
                    "ServerOS process, and `ask` is env-bound to BOTMUX_SESSION_ID with "
                    "no --session-id escape hatch, so it runs only inside a "
                    "daemon-spawned turn. What is UNKNOWN is not whether a person can "
                    "answer — they can — but whether an answer can arrive BOUND to the "
                    "request fingerprint an approval is about; a returned key is bound "
                    "to nothing. See request_approval, which still refuses"),
                "human.notification.transport": support(
                    "UNKNOWN",
                    "Measured 2026-09-25 and corrected DOWNWARD from SUPPORTED, "
                    "which is the direction this table had not yet been corrected "
                    "in. `POST /api/trigger` with no `target.chatId` does not carry "
                    "a sentence into a conversation: the service creates a SYNTHETIC "
                    "session (`target.chatId` comes back as `http_async_…`) and hands "
                    "the text to a MODEL as a task, whose answer is returned over HTTP "
                    "to the caller. The header the service builds for that turn says "
                    "in as many words that the reply is 'not shown in a chat' and "
                    "instructs the model not to post to Feishu. So the path this "
                    "provider uses reaches no person, and a receipt read off it says "
                    "a model was asked. The surface does exist — a trigger that names "
                    "a real `target.chatId` routes into that chat — and it has never "
                    "been observed to deliver anything from here, so the state is "
                    "UNKNOWN rather than UNSUPPORTED: what would settle it is one "
                    "addressed trigger whose message is seen at the other end. Until "
                    "then `runtime/notifications.py#deliver` refuses before calling, "
                    "which is the intended reading of this word."),
                "human.interactive.rendering": support(
                    "UNKNOWN",
                    "`botmux send --card-file` and `--card-json` are documented, and "
                    "`botmux card patch --message-id` updates one in place; measured "
                    "2026-09-15, `send` accepts --session-id from a non-turn process "
                    "and gets past session resolution. Whether a card authored that way "
                    "arrives is UNOBSERVED — the probe fails closed before sending, so "
                    "no card was sent and none was seen. This is a correction of the "
                    "earlier UNSUPPORTED, whose reason (\"ServerOS authors none through "
                    "this surface\") was a statement about our implementation wearing a "
                    "capability claim's clothes. It is UNKNOWN and not SUPPORTED "
                    "because the console deliberately renders through BotMux-native "
                    "replies rather than authoring cards (§P), so no ServerOS process "
                    "has yet exercised the path"),
            },
            "measured_by": "mcp/human_control.py — see the module docstring for the probes",
        }

    def observe(self, *, device_ref: str) -> dict:
        """What this transport is right now, from calling it."""
        observed_at = self._now()
        try:
            self._request("GET", "/api/sessions")
        except HumanControlError as exc:
            return {"provider": self.provider_id, "device_ref": device_ref,
                    "transport": self.transport, "status": "STOPPED",
                    "observed_at": observed_at,
                    "reason": f"{type(exc).__name__}: {exc}"[:300]}
        report = self.identify()
        return {
            "provider": self.provider_id, "device_ref": device_ref,
            "transport": self.transport, "status": "RUNNING",
            "observed_at": observed_at,
            "identity_status": report.status,
            "identity_detail": report.detail,
            "identity_surface": " ".join(self.config.actor_command),
        }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--identify", action="store_true")
    parser.add_argument("--attach", action="store_true",
                        help="identify this process, then present that evidence "
                             "to serverosd for a trusted actor context")
    parser.add_argument("--socket", default=None,
                        help="serverosd socket to attach on; defaults to this "
                             "node's")
    # ON by default, and that default is load-bearing rather than a matter of
    # taste. This verb's primary caller is a turn hook, and a hook's command
    # string is read off a settings file that the process running it may have
    # read before this flag existed: measured 2026-09-28, the session then
    # holding the Feishu conversation carried `--attach` with no `--record`,
    # because that was the string when it started. Defaulting to OFF would
    # therefore have made the one turn that matters the one turn that left no
    # trace — and "the hook never ran" and "the hook ran and minted nothing"
    # would have been the same empty file. Defaulting to ON means both the old
    # and the new spelling record, and a caller that genuinely must not write
    # (the unit tests are the one such caller, because they run this as a pure
    # function and a default that wrote would litter a real log root) says so
    # with `--no-record`.
    parser.add_argument("--record", action=argparse.BooleanOptionalAction,
                        default=True,
                        help="append this attempt's outcome to the ingress "
                             "record before exiting, whatever the outcome was. "
                             "On by default; --no-record suppresses it. "
                             "A mount that answers a turn has no other way to "
                             "say what happened on a turn whose context was "
                             "not minted")
    parser.add_argument("--record-ref", default=INGRESS_RECORD_REF,
                        help="which declared path reference the record is "
                             "appended to; defaults to this product's")
    parser.add_argument("--device", default="device:server-beijing")
    args = parser.parse_args(argv)

    provider = BotMuxHumanControlProvider()
    if args.identify:
        report = provider.identify()
        json.dump({**report.as_report(), "detail": report.detail},
                  sys.stdout, indent=2, sort_keys=True)
    elif args.attach:
        client = None
        if args.socket:
            client = _node_mcp().DaemonClient(Path(args.socket))
        result = attach_context(provider, daemon=client)
        # Recorded BEFORE the exit code is decided, and for every outcome
        # including the ones that minted nothing. A record that only held
        # successes would be silent on exactly the turns somebody is asking
        # about — and it would answer "the store is empty" with the one
        # reading §5 forbids: "so nothing was tried".
        if args.record:
            try:
                result["record"] = record_attempt(
                    result, path=ingress_record_path(args.record_ref))
            except HumanControlError as exc:
                result["record"] = {"recorded": False,
                                    "detail": str(exc)}
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        # The verb is `attach`, so the exit code reports whether a context was
        # actually minted. The other modes here always exit 0 because they are
        # questions, and a question that was answered — even with "nobody was
        # identified" — succeeded. This one is an act, and a command that exits
        # 0 for an attach that did not happen is the declaration-read-as-fact
        # failure this repository keeps finding. The typed reason is in the
        # output either way.
        return 0 if result.get("attached") else 1
    elif args.observe:
        json.dump(provider.observe(device_ref=args.device), sys.stdout,
                  indent=2, sort_keys=True)
    else:
        json.dump(provider.capabilities(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
