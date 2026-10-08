#!/usr/bin/env python3
"""Codex AgentSessionProvider over the Codex app-server protocol.

WHAT WAS MEASURED, AND WHEN
---------------------------
The protocol in this file was not recalled. It was read off the installed
binary with `codex app-server generate-json-schema --out DIR`, and then
confirmed by driving a real app-server and reading its real answers:

    codex-cli 0.153.4      (userAgent: "<client>/0.153.4 (Ubuntu 24.4.0; x86_64) …")
    measured 2026-09-14    by scripts/codex_probe.py --live

Every method name, every parameter name, the notification names, the error
shape and the four observed status words in `_THREAD_STATUS` / `_TURN_STATUS`
come from that run. Anything NOT observed is not in those maps, and an unmapped
value is reported as UNKNOWN with the provider's own word carried verbatim
beside it — the alternative is a table of plausible-looking guesses that reads
exactly like the measured part.

WHY A PROTOCOL MODULE IS A WHOLE FILE
-------------------------------------
Three things must stay separate and this file is where they can be confused:

    ServerOS Agent Session   the logical Agent instance, alive across turns
    Codex thread             the provider's native session  -> provider_session_id
    Codex turn               ONE instruction                 -> turn_state
    app-server process       a child process with a pid      -> evidence, never identity

The process is deliberately NOT an identity anywhere below. It is started, it
is stopped, its pid is recorded as evidence, and no ref is ever minted from it.
A caller that reconnects starts a new process and finds the same sessions,
because the sessions live on the provider's side of the boundary — which is the
whole reason `thread/resume` exists and the reason this file can be tested with
a fake app-server.

APPROVALS: THIS PROVIDER NEVER GRANTS ONE
-----------------------------------------
Codex's own human-in-the-loop surface is `item/commandExecution/requestApproval`
and its siblings — server-to-client requests that a host is expected to put in
front of a person. ServerOS has no verified human transport for them in this
release: §67 measured `human.approval.transport = UNSUPPORTED` for the
HumanControlProvider on this deployment, and a provider that answered `allow`
on its own authority would be manufacturing consent nobody gave.

So this adapter answers every approval request with `decline`, records that it
did, and reports the count as evidence. That is a real limitation and it is
stated rather than engineered around: threads are created with an explicit
`sandbox` and `approvalPolicy` so that the ordinary path does not need a person,
and anything that does need one is refused rather than approved.
"""
from __future__ import annotations

import importlib.util
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_RUNTIME = _REPO / "runtime"
_ABILITIES = _REPO / "abilities"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


semantics = _load("serveros_runtime_agent_sessions", _RUNTIME / "agent_sessions.py")
instances = _load("serveros_abilities_provider_instance", _ABILITIES / "provider_instance.py")
agent_ref = semantics.agent_ref
session_ref = semantics.session_ref
support = semantics.support

PROVIDER_ID = "provider:codex-app-server"
RUNTIME_REF = "agent-runtime:codex"
AGENT_ID = "codex-app-server"

# The release this adapter was measured against. It is not a compatibility
# claim: it is the answer to "which version produced these method names", which
# is the first question anyone asks when a call stops working.
MEASURED_AGAINST = "codex-cli 0.153.4"
MEASURED_ON = "2026-09-14"


class CodexError(RuntimeError):
    pass


class CodexUnavailable(CodexError):
    """The app-server could not be started or did not answer."""


class CodexProtocolError(CodexError):
    """The app-server answered, and what it answered was an error."""

    def __init__(self, method: str, code: Any, message: str) -> None:
        self.method = method
        self.code = code
        self.message = message
        super().__init__(f"codex app-server {method} failed ({code}): {message}")


class CodexUnsupported(CodexError):
    """ServerOS asked for a verb this provider's native surface does not have."""


class CodexNoActiveTurn(CodexError):
    """An interrupt was asked for and no turn is running to interrupt."""


@dataclass(frozen=True)
class CodexConfig:
    """Everything this adapter is allowed to decide, in one frozen place.

    `sandbox` and `approval_policy` are here rather than defaulted at the call
    site because they are a POLICY the operator is choosing, not an
    implementation detail. `never` + `read-only` is the narrowest pair that
    still lets a prompt-only turn complete: nothing is written and no person is
    asked for anything, which is the only combination this release can honestly
    run with, given that no verified human approval transport exists yet.
    """

    binary: str = "codex"
    timeout: float = 30.0
    turn_timeout: float = 600.0
    sandbox: str = "read-only"
    approval_policy: str = "never"
    model: str | None = None
    ephemeral: bool = False
    cwd: str | None = None
    list_limit: int = 50

    @classmethod
    def from_env(cls) -> "CodexConfig":
        def _env(name: str, fallback):
            value = os.environ.get(name)
            return fallback if value in (None, "") else value

        return cls(
            binary=_env("SERVEROS_CODEX_BIN", cls.binary),
            timeout=float(_env("SERVEROS_CODEX_TIMEOUT", "30")),
            turn_timeout=float(_env("SERVEROS_CODEX_TURN_TIMEOUT", "600")),
            sandbox=_env("SERVEROS_CODEX_SANDBOX", cls.sandbox),
            approval_policy=_env("SERVEROS_CODEX_APPROVAL_POLICY", cls.approval_policy),
            model=os.environ.get("SERVEROS_CODEX_MODEL") or None,
            ephemeral=os.environ.get("SERVEROS_CODEX_EPHEMERAL") == "1",
            cwd=os.environ.get("SERVEROS_CODEX_CWD") or None,
        )

    def resolve_binary(self) -> str:
        """The path of the binary this adapter will run.

        `shutil.which` and not a hard-coded path: the deployed path on this
        machine is a wrapper, and pinning it would make the provider fail on the
        one machine that has no wrapper for a reason that looks like "codex is
        not installed". The resolved path is reported as evidence so the
        difference is visible rather than guessed at.
        """
        if os.path.sep in self.binary:
            return self.binary
        found = shutil.which(self.binary)
        if not found:
            raise CodexUnavailable(
                f"no {self.binary!r} on PATH. The Codex Agent Session provider "
                f"needs the codex CLI; set SERVEROS_CODEX_BIN to point at one.")
        return found


def _iso_from_ms(value: Any) -> str | None:
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_from_s(value: Any) -> str | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# The transport. One child process, newline-delimited JSON, nothing above it.
# ---------------------------------------------------------------------------

class AppServerProcess:
    """One `codex app-server` child, spoken to over its stdio transport.

    The wire format is a JSON object per line. Requests carry `id`, `method` and
    `params`; responses carry the same `id` and either `result` or `error`;
    notifications carry `method` and no `id`. Requests FROM the server carry
    both — those are the ones that need an answer, and a client that only ever
    waits for its own `id` will hang on the first approval request instead of
    noticing that it was asked something.

    This class owns the process and the framing. It decides nothing about
    sessions: no method name below this line is an Agent Session verb.
    """

    def __init__(self, config: CodexConfig, *, log=None) -> None:
        self.config = config
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._selector: selectors.BaseSelector | None = None
        self.handshake: dict = {}
        self.notifications: list[dict] = []
        self.approvals_declined: list[dict] = []
        self._log = log
        # Lines are reassembled here from raw `os.read` rather than by
        # `proc.stdout.readline()`. Two reasons, and the second is the real one:
        # a selector watches a file DESCRIPTOR while `readline` reads a user-
        # space buffer, so a line already sitting in that buffer with no new
        # bytes on the fd is a line this loop would wait for forever. Owning the
        # buffer makes "readable" and "has a line" the same fact.
        self._out_buf = ""
        self._err_tail: list[str] = []
        self._stdout_fd: int | None = None
        self._stderr_fd: int | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> dict:
        if self.running:
            return self.handshake
        binary = self.config.resolve_binary()
        self._proc = subprocess.Popen(
            [binary, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            # Its own process group, so that a timeout can end the server and
            # anything it started. Killing only the direct child leaves a
            # grandchild holding the pipe, which shows up as a client that
            # hangs after it has already given up.
            start_new_session=True,
            text=True, bufsize=1,
            env={**os.environ, "RUST_LOG": "error"},
        )
        self._stdout_fd = self._proc.stdout.fileno()
        self._stderr_fd = self._proc.stderr.fileno()
        self._out_buf = ""
        self._err_tail = []
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._stdout_fd, selectors.EVENT_READ)
        # stderr is watched rather than left as an unread pipe. An unread pipe
        # fills at 64 KiB, and a server that writes past that blocks forever on
        # a write nobody will ever read — which presents as "codex hangs", the
        # one diagnosis that sends an operator to the wrong machine.
        self._selector.register(self._stderr_fd, selectors.EVENT_READ)
        try:
            result = self.call("initialize", {
                "clientInfo": {"name": "serveros", "version": "0.1.0"},
            }, timeout=self.config.timeout)
        except Exception:
            self.close()
            raise
        self.handshake = {
            "user_agent": result.get("userAgent"),
            "codex_home": result.get("codexHome"),
            "platform_family": result.get("platformFamily"),
            "platform_os": result.get("platformOs"),
            "binary": binary,
            "pid": self.pid,
            "started_at": _now_utc(),
            "measured_against": MEASURED_AGAINST,
        }
        return self.handshake

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if self._selector is not None:
            try:
                self._selector.close()
            except Exception:
                pass
            self._selector = None
        if proc is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if proc.poll() is not None:
                break
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                time.sleep(0.5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass

    # -- framing -----------------------------------------------------------

    def _write(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CodexUnavailable("the codex app-server is not running")
        line = json.dumps(payload)
        if self._log:
            self._log("->", line)
        try:
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as exc:
            raise CodexUnavailable(
                f"the codex app-server stopped accepting input: {type(exc).__name__}: {exc}"
            ) from exc

    def _drain_stderr(self) -> None:
        try:
            chunk = os.read(self._stderr_fd, 65536)
        except OSError:
            return
        if not chunk:
            return
        text = chunk.decode("utf-8", errors="replace")
        self._err_tail.append(text)
        # Bounded on purpose: this is kept to explain a death, and an unbounded
        # copy of a chatty server's log is a memory leak with a good excuse.
        while sum(len(part) for part in self._err_tail) > 8192:
            self._err_tail.pop(0)

    def died_because(self, *, grace: float = 0.0) -> str:
        """What to say when the server is gone. Never guesses.

        `grace` exists because EOF on stdout and process exit are two events and
        the first is usually read first. Reporting the exit code of a process
        that has not been reaped yet yields `code=None`, which reads like "it
        died without a code" when the truth is "nobody has asked yet". So the
        EOF path waits briefly for the exit rather than describing the hole it
        is standing in.
        """
        if self._proc is None:
            return "the codex app-server was never started"
        if grace:
            try:
                self._proc.wait(timeout=grace)
            except Exception:
                pass
        code = self._proc.poll()
        tail = "".join(self._err_tail).strip()
        if code is None:
            return ("the codex app-server closed its output stream and is still "
                    "running; it has not exited, so there is no exit code to report")
        if tail:
            return f"the codex app-server exited (code={code}): {tail[-600:]}"
        return (f"the codex app-server exited (code={code}) and wrote nothing to "
                f"stderr; there is no evidence here about why")

    def _readline(self, deadline: float) -> str | None:
        """One line, or None if the deadline passed. Never blocks past it."""
        while True:
            if "\n" in self._out_buf:
                line, _, self._out_buf = self._out_buf.partition("\n")
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            if self._selector is None or self._proc is None:
                return None
            ready = self._selector.select(timeout=min(remaining, 0.25))
            if not ready:
                if self._proc.poll() is not None:
                    raise CodexUnavailable(self.died_because())
                continue
            for key, _events in ready:
                if key.fd == self._stderr_fd:
                    self._drain_stderr()
                    continue
                try:
                    chunk = os.read(self._stdout_fd, 65536)
                except OSError as exc:
                    raise CodexUnavailable(
                        f"reading the codex app-server's output failed: {exc}") from exc
                if not chunk:
                    # EOF on stdout is the server ending, whether or not it
                    # exited yet. Reported as a STOPPED provider, not as a
                    # session that closed — those are different claims.
                    raise CodexUnavailable(self.died_because(grace=2.0))
                self._out_buf += chunk.decode("utf-8", errors="replace")

    def _handle(self, message: dict) -> None:
        """Deal with one message that is not the response we are waiting for."""
        method = message.get("method")
        if method is None:
            return
        if self._log:
            self._log("<-", json.dumps(message))
        self.notifications.append(message)
        # Bounded: a long-lived process driving many turns would otherwise
        # accumulate one record per token, and the only ones anything reads back
        # are the terminal turn notifications and the status changes.
        if len(self.notifications) > 512:
            del self.notifications[:-512]
        if "id" in message:
            self._answer_server_request(message)

    def _answer_server_request(self, message: dict) -> None:
        """Answer a server-to-client request. Approvals are always declined.

        See the module docstring: this adapter cannot obtain a verified human
        decision, so it does not produce one. `decline` is the only answer here
        that is not a fabrication, and the request is recorded so that a turn
        which failed for want of an approval is distinguishable from one that
        failed for any other reason.
        """
        method = str(message.get("method"))
        if method == "currentTime/read":
            result: dict = {"currentTimeMs": int(time.time() * 1000)}
        elif ("requestApproval" in method
              or method in ("execCommandApproval", "applyPatchApproval")):
            self.approvals_declined.append({
                "method": method,
                "params": message.get("params"),
                "declined_at": _now_utc(),
                "because": ("no verified human approval transport exists in this "
                            "release (§67); this adapter never approves"),
            })
            result = {"decision": "decline"}
        else:
            self._write({
                "id": message["id"],
                "error": {"code": -32601,
                          "message": f"serveros client does not implement {method!r}"},
            })
            return
        self._write({"id": message["id"], "result": result})

    def call(self, method: str, params: dict | None = None, *,
             timeout: float | None = None) -> dict:
        """One request, one response. Raises rather than returning an error."""
        self._next_id += 1
        request_id = self._next_id
        self._write({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + (timeout or self.config.timeout)
        while True:
            line = self._readline(deadline)
            if line is None:
                raise CodexUnavailable(
                    f"the codex app-server did not answer {method!r} within "
                    f"{timeout or self.config.timeout}s")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue          # a diagnostic line on stdout is not a protocol error
            if message.get("id") == request_id and "method" not in message:
                if self._log:
                    self._log("<-", line)
                if "error" in message:
                    error = message["error"] or {}
                    raise CodexProtocolError(method, error.get("code"),
                                             str(error.get("message")))
                return message.get("result") or {}
            self._handle(message)

    def pump(self, seconds: float) -> None:
        """Read whatever arrives for a while, answering anything that asks."""
        deadline = time.monotonic() + seconds
        while True:
            line = self._readline(deadline)
            if line is None:
                return
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "method" in message:
                self._handle(message)
            else:
                # A response to a request this adapter is no longer waiting for.
                # Kept rather than dropped so that `close()` can report it.
                self.notifications.append(message)

    def wait_for(self, method: str, *, predicate=None, timeout: float = 600.0,
                 turn_id: str | None = None) -> dict:
        """Wait for a notification named `method`. Returns it, or raises.

        The buffer is scanned BEFORE waiting, and that is not an optimization.
        A turn that finishes instantly has its `turn/completed` notification
        written before the response to `turn/start` is — the server emits, then
        replies — so a waiter that only looked at what arrived after it started
        waiting would sit through its whole timeout on a turn that had already
        finished. Scanning first closes that race; `turn_id` narrows the match
        to the turn the caller actually started, so scanning cannot mistake an
        older turn's completion for this one's.
        """
        if turn_id is not None:
            def matches(message: dict) -> bool:
                params = message.get("params") or {}
                return str((params.get("turn") or {}).get("id")) == str(turn_id)
            predicate = matches if predicate is None else (
                lambda m: matches(m) and predicate(m))
        deadline = time.monotonic() + timeout
        seen = 0
        while True:
            for message in self.notifications[seen:]:
                if message.get("method") == method and (predicate is None
                                                        or predicate(message)):
                    return message
            seen = len(self.notifications)
            if time.monotonic() >= deadline:
                raise CodexUnavailable(
                    f"the codex app-server did not emit {method!r} within {timeout}s")
            self.pump(min(1.0, max(0.05, deadline - time.monotonic())))

    def turns_for(self, provider_session_id: str) -> list[dict]:
        """Turn notifications this process has seen for one thread.

        This is the adapter's own observation buffer and it is deliberately
        small: a restarted adapter has seen nothing, and says so, rather than
        inferring a turn outcome it did not witness.
        """
        found = []
        for message in self.notifications:
            params = message.get("params") or {}
            if params.get("threadId") != provider_session_id:
                continue
            method = message.get("method")
            if method == "turn/started":
                found.append({"event": "started", **(params.get("turn") or {})})
            elif method == "turn/completed":
                found.append({"event": "completed", **(params.get("turn") or {})})
        return found


# ---------------------------------------------------------------------------
# The provider.
# ---------------------------------------------------------------------------

class CodexAppServerProvider:
    """The Codex app-server, as an AgentSessionProvider.

    One connection per provider instance, started on first use and closed by
    `close()`. Lazily started rather than started here, because registering a
    provider must not spawn a process: an MCP that lists providers to answer
    `agent.list` would otherwise start a Codex server as a side effect of a
    read.
    """

    provider_id = PROVIDER_ID

    # Only what was measured. `failed` and `systemError` were observed by
    # driving a turn against a model the account does not support; `idle` and
    # `active` were observed on working threads; `interrupted` was observed by
    # calling turn/interrupt. Nothing here is inferred from a similar product.
    _THREAD_STATUS: dict[str, tuple[str, str]] = {
        "idle": ("IDLE", "FACT"),
        "active": ("BUSY", "FACT"),
        "systemError": ("FAILED", "FACT"),
    }
    _TURN_STATUS: dict[str, str] = {
        "inProgress": "TURN_RUNNING",
        "completed": "TURN_COMPLETED",
        "failed": "TURN_FAILED",
        # `interrupted` is a real provider word with no ServerOS equivalent. It
        # is NOT mapped to TURN_FAILED, which would say the turn broke when a
        # person stopped it, and not to TURN_COMPLETED, which would say it
        # succeeded. See `_turn_state` — the provider's word is carried instead.
        "interrupted": "TURN_INTERRUPTED_UNREPRESENTABLE",
    }

    def __init__(self, config: CodexConfig | None = None, clock=None,
                 process_factory=None) -> None:
        self.config = config or CodexConfig.from_env()
        self._clock = clock
        self._process_factory = process_factory or (
            lambda cfg: AppServerProcess(cfg))
        self._process: AppServerProcess | None = None
        # Outcomes of turns this adapter actually drove, in memory and never on
        # disk. Same rule as the BotMux adapter's execution log, for the same
        # reason: a persisted readiness is a memory of a past resolution read as
        # a current one, and a persisted NEGATIVE keeps refusing a provider that
        # has since been repaired.
        self._execution_log: list[dict] = []

    # -- the process -------------------------------------------------------

    def _now(self) -> str:
        return instances.now_utc(self._clock)

    def process(self) -> AppServerProcess:
        if self._process is None or not self._process.running:
            if self._process is not None:
                self._process.close()
            self._process = self._process_factory(self.config)
            self._process.start()
        return self._process

    def close(self) -> None:
        if self._process is not None:
            self._process.close()
            self._process = None

    def __enter__(self):
        self.process()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- action support ----------------------------------------------------

    @staticmethod
    def actions(*, ephemeral: bool = False,
                can_accept_input: bool | None = True) -> dict:
        """What the Codex app-server protocol can and cannot do, per verb.

        Two rows are SUPPORTED here that the BotMux adapter declares
        UNSUPPORTED, and neither is a copy of the other's answer: `thread/resume`
        is a real app-server method, and `turn/interrupt` is a real app-server
        method that stops one turn rather than the session. Those are measured
        facts about this provider, not a claim about BotMux.

        `session.stop` is UNSUPPORTED, and the reason names what DOES exist
        rather than saying the capability is missing. `thread/archive` is a
        reversible hide and `thread/delete` destroys history; neither ends a
        running session, and an adapter that reported archive as a stop would
        make a session look ended while it was still there.
        """
        inspect = support("SUPPORTED")
        if ephemeral:
            # Measured: `thread/read` with includeTurns answers -32600
            # "ephemeral threads do not support includeTurns". Declaring the
            # limitation here means a caller learns it from the provider's
            # capability table instead of from an error mid-flow.
            inspect = support(
                "SUPPORTED",
                "metadata only: codex refuses includeTurns on ephemeral threads")
        send_state = "SUPPORTED" if can_accept_input is not False else "UNSUPPORTED"
        send_reason = (None if send_state == "SUPPORTED"
                       else "the thread reports canAcceptDirectInput: false")
        return {
            "session.create": support(
                "SUPPORTED",
                "thread/start followed by a first turn; the sandbox and approval "
                "policy come from CodexConfig, not from the caller"),
            "session.inspect": inspect,
            "session.send": support(send_state, send_reason),
            "session.result": support("SUPPORTED"),
            "session.resume": support("SUPPORTED"),
            "session.interrupt": support(
                "SUPPORTED",
                "interrupts the CURRENT TURN via turn/interrupt; it does not end the session"),
            "session.stop": support(
                "UNSUPPORTED",
                "the app-server protocol has no session-stop. `thread/archive` is a "
                "reversible hide and `thread/delete` destroys history; neither ends "
                "a running session, and neither is a stop"),
            "session.handoff": support(
                "UNKNOWN",
                "handoff is a ServerOS-level semantics with no codex app-server verb; "
                "it is composed from create/resume/send across two providers"),
        }

    # -- normalization -----------------------------------------------------

    def _thread_status(self, thread: dict) -> tuple[str, str, str]:
        raw = thread.get("status")
        token = str((raw or {}).get("type") or "") if isinstance(raw, dict) else str(raw or "")
        if token in self._THREAD_STATUS:
            state, provenance = self._THREAD_STATUS[token]
            return state, provenance, f"codex reports thread status {token!r}"
        return "UNKNOWN", "UNKNOWN", f"unmapped codex thread status {token!r}"

    def _turn_state(self, turn: dict) -> tuple[str, str, str | None]:
        """(ServerOS turn state, provenance, provider's own word if unmapped)."""
        raw = str(turn.get("status") or "")
        mapped = self._TURN_STATUS.get(raw)
        if mapped == "TURN_INTERRUPTED_UNREPRESENTABLE":
            return ("TURN_UNKNOWN", "UNKNOWN",
                    "the turn was interrupted on request; the ServerOS turn "
                    "vocabulary has no value for a turn that was deliberately "
                    "stopped, so the outcome reads UNKNOWN and codex's own word "
                    "`interrupted` is carried in provider_state")
        if mapped:
            return mapped, "FACT", None
        if turn.get("error"):
            return "TURN_FAILED", "FACT", None
        return "TURN_UNKNOWN", "UNKNOWN", f"unmapped codex turn status {raw!r}"

    @staticmethod
    def _final_answer(items: Any) -> str | None:
        """The last agent message's text, verbatim.

        The last one and not the first: a turn may emit several, and the answer
        to the instruction is the one that closed it.
        """
        text = None
        for item in items or []:
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                if item.get("text"):
                    text = item["text"]
        return text

    def _session_row(self, thread: dict, *, device_ref: str) -> dict:
        native_id = str(thread.get("id") or thread.get("sessionId") or "")
        status, provenance, reason = self._thread_status(thread)
        ephemeral = bool(thread.get("ephemeral"))
        can_accept = thread.get("canAcceptDirectInput")
        row = {
            "session_ref": session_ref(device_ref, self.provider_id, native_id),
            "device_ref": device_ref,
            "provider": self.provider_id,
            "provider_session_id": native_id,
            "agent_runtime": RUNTIME_REF,
            "status": status,
            "status_provenance": provenance,
            "status_reason": reason,
            # The provider's own word, kept beside the normalized one. A reader
            # that trusts `status` alone cannot tell `systemError` from any other
            # FAILED, and the repairs differ.
            "provider_status": (thread.get("status") or {}).get("type")
            if isinstance(thread.get("status"), dict) else thread.get("status"),
            "action_support": self.actions(
                ephemeral=ephemeral,
                can_accept_input=can_accept if isinstance(can_accept, bool) else None),
            "title": (thread.get("preview") or "")[:200] or None,
            "cwd": thread.get("cwd"),
            "workspace": thread.get("cwd"),
            "created_at": _iso_from_s(thread.get("createdAt")),
            "last_activity": _iso_from_s(thread.get("updatedAt")),
            "provider_evidence": self._evidence(thread, source="codex:thread/list"),
        }
        if isinstance(can_accept, bool):
            row["can_accept_input"] = can_accept
        for key in ("model", "modelProvider", "cliVersion"):
            if thread.get(key) is not None:
                row[key] = thread[key]
        return row

    def _evidence(self, payload: dict, *, source: str) -> dict:
        return {
            "source": source,
            "observed_at": self._now(),
            "provenance": "FACT",
            "evidence_type": "provider-response",
            "authoritative": True,
            "fields": {k: payload[k] for k in
                       ("id", "status", "ephemeral", "cwd", "cliVersion",
                        "canAcceptDirectInput", "model", "modelProvider")
                       if k in payload},
        }

    # -- reads -------------------------------------------------------------

    def list_agents(self, *, device_ref: str) -> list[dict]:
        """One Agent, because the app-server exposes one Agent identity.

        This is not a placeholder for a richer registry that might come later.
        The `agent_id` is a ServerOS-side handle for "the Codex runtime on this
        device", and the provider's own model/provider names travel with it as
        observed configuration rather than as identity: `modelProvider: openai`
        is a fact about how a thread was configured, and minting a ServerOS
        Agent per model name would invent a fleet nobody enrolled.
        """
        return [{
            "agent_id": AGENT_ID,
            "agent_ref": agent_ref(device_ref, self.provider_id, AGENT_ID),
            "provider": self.provider_id,
            "device_ref": device_ref,
            "agent_runtime": RUNTIME_REF,
            "kind": "app-server",
            "identity_basis": "one app-server instance per device",
            "measured_against": MEASURED_AGAINST,
            "transport": "stdio JSONL (codex app-server --listen stdio://)",
            "detail": "session identity is the codex thread id, never this process",
        }]

    def list_sessions(self, *, device_ref: str) -> list[dict]:
        result = self.process().call("thread/list", {
            "limit": self.config.list_limit,
            "sortKey": "updated_at",
        })
        rows = result.get("data") or []
        if not isinstance(rows, list):
            raise CodexProtocolError("thread/list", None,
                                     f"expected a list, got {type(rows).__name__}")
        return [self._session_row(row, device_ref=device_ref)
                for row in rows if isinstance(row, dict)]

    def inspect(self, *, device_ref: str, provider_session_id: str,
                include_turns: bool = True) -> dict:
        """One thread, by its provider-native id.

        `includeTurns` is asked for and a refusal is REPORTED rather than
        retried without it: the refusal means this thread is ephemeral, which is
        a property of the session a caller needs to know. Silently dropping the
        parameter would return a thinner record that looks complete.
        """
        params: dict = {"threadId": provider_session_id}
        if include_turns:
            params["includeTurns"] = True
        try:
            result = self.process().call("thread/read", params)
        except CodexProtocolError as exc:
            if include_turns and "ephemeral" in exc.message:
                result = self.process().call(
                    "thread/read", {"threadId": provider_session_id})
                row = self._session_row(result.get("thread") or {},
                                        device_ref=device_ref)
                row["inspect_limitation"] = exc.message
                return row
            raise
        thread = result.get("thread") or {}
        row = self._session_row(thread, device_ref=device_ref)
        turns = thread.get("turns") or []
        if turns:
            row["turns"] = [self._turn_row(turn) for turn in turns]
        return row

    def _turn_row(self, turn: dict) -> dict:
        state, provenance, reason = self._turn_state(turn)
        row = {
            "turn_id": turn.get("id"),
            "turn_state": state,
            "turn_state_provenance": provenance,
            "provider_state": turn.get("status"),
            "output": self._final_answer(turn.get("items")),
            "started_at": _iso_from_s(turn.get("startedAt")),
            "finished_at": _iso_from_s(turn.get("completedAt")),
        }
        if reason:
            row["turn_state_reason"] = reason
        error = turn.get("error")
        if isinstance(error, dict):
            row["error"] = error.get("message")
            row["error_code"] = error.get("codexErrorInfo")
        return row

    # -- readiness ---------------------------------------------------------

    def _record_execution(self, *, turn_state: str, error: str | None,
                          provider_session_id: str) -> None:
        self._execution_log.append({
            "state": turn_state,
            "observed_at": self._now(),
            "error": error,
            "provider_session_id": provider_session_id,
        })
        if len(self._execution_log) > 64:
            del self._execution_log[:-64]

    def _execution_observation(self, *, observed_at: str):
        if not self._execution_log:
            return instances.unknown_execution(observed_at=observed_at,
                                               provider_id=self.provider_id)
        latest = self._execution_log[-1]
        state = "OK" if latest["state"] == "TURN_COMPLETED" else "FAILED"
        return instances.observation(
            axis=instances.AXIS_EXECUTION, state=state,
            observer_type="provider-response", observer_id=self.provider_id,
            observed_at=latest["observed_at"], evidence_type="provider-response",
            reason="EXECUTION_FAILED" if state == "FAILED" else None,
            extra={"error": (latest.get("error") or "")[:400] or None,
                   "provider_session_id": latest.get("provider_session_id"),
                   "turn_state": latest["state"]},
        )

    def observe(self, *, device_ref: str) -> dict:
        """Reachability is measured by starting and handshaking, not assumed.

        The handshake is the whole measurement: a process that starts and
        answers `initialize` is a control plane that is RUNNING, and one that
        does not is STOPPED rather than an exception, because a caller asked a
        question and a provider that is down is an answer to it.
        """
        observed_at = self._now()
        try:
            handshake = self.process().start()
        except (CodexError, OSError) as exc:
            control = instances.observation(
                axis=instances.AXIS_CONTROL_PLANE, state="STOPPED",
                observer_type="host-measurement", observer_id=self.provider_id,
                observed_at=observed_at, evidence_type="host-measurement",
                extra={"why": f"{type(exc).__name__}: {exc}"[:300]},
            )
            execution = instances.unknown_execution(observed_at=observed_at,
                                                    provider_id=self.provider_id)
        else:
            control = instances.observation(
                axis=instances.AXIS_CONTROL_PLANE, state="RUNNING",
                observer_type="provider-response", observer_id=self.provider_id,
                observed_at=observed_at, evidence_type="provider-response",
                extra={"source": "codex:initialize",
                       "user_agent": handshake.get("user_agent"),
                       "binary": handshake.get("binary")},
            )
            execution = self._execution_observation(observed_at=observed_at)
        return instances.instance(
            provider=self.provider_id, device_ref=device_ref,
            observations=[control, execution], reference=observed_at,
        )

    # -- mutations ---------------------------------------------------------

    def _thread_params(self, *, workspace: str | None) -> dict:
        params: dict = {
            "sandbox": self.config.sandbox,
            "approvalPolicy": self.config.approval_policy,
            "ephemeral": self.config.ephemeral,
        }
        cwd = workspace or self.config.cwd
        if cwd:
            params["cwd"] = cwd
        if self.config.model:
            params["model"] = self.config.model
        return params

    def _start_turn(self, *, device_ref: str, provider_session_id: str,
                    instruction: str, idempotency_key: str | None) -> dict:
        params: dict = {
            "threadId": provider_session_id,
            "input": [{"type": "text", "text": instruction}],
        }
        # `clientUserMessageId` is the only per-turn client identity the
        # protocol has, and it is FORWARDED, not called an idempotency key:
        # codex documents no deduplication for it, and reporting
        # `idempotent: true` would be this adapter claiming a guarantee it
        # cannot demonstrate. See `idempotency_basis` in the result.
        if idempotency_key:
            params["clientUserMessageId"] = idempotency_key
        result = self.process().call("turn/start", params,
                                     timeout=self.config.timeout)
        turn = result.get("turn") or {}
        state, provenance, reason = self._turn_state(turn)
        out = {
            "session_ref": session_ref(device_ref, self.provider_id,
                                       provider_session_id),
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            "turn_id": turn.get("id"),
            "turn_state": state,
            "turn_state_provenance": provenance,
            "provider_state": turn.get("status"),
            # Acceptance is not a session state. codex answered the request;
            # what it answered with is a turn that is `inProgress`, which is a
            # turn state and is carried as one.
            "idempotent": False,
            "idempotency_basis": (
                "codex app-server documents no turn deduplication; the key is "
                "forwarded as clientUserMessageId"),
        }
        if reason:
            out["turn_state_reason"] = reason
        return out

    def create(self, *, device_ref: str, agent_id: str, instruction: str,
               idempotency_key: str, workspace: str | None = None) -> dict:
        if agent_id not in (AGENT_ID, f"agent:{agent_id}"):
            raise CodexUnsupported(
                f"this provider exposes one Agent ({AGENT_ID!r}); {agent_id!r} is "
                f"not it. A second Codex Agent identity would need its own app-server "
                f"runtime, not a nickname on this one")
        result = self.process().call(
            "thread/start", self._thread_params(workspace=workspace),
            timeout=self.config.timeout)
        thread = result.get("thread") or {}
        native_id = str(thread.get("id") or "")
        if not native_id:
            raise CodexProtocolError("thread/start", None,
                                     "codex accepted thread/start and returned no thread id")
        started = self._start_turn(
            device_ref=device_ref, provider_session_id=native_id,
            instruction=instruction, idempotency_key=idempotency_key)
        return {
            **started,
            "agent_id": agent_id,
            "agent_runtime": RUNTIME_REF,
            "workspace": thread.get("cwd"),
            "provider_thread_status": (thread.get("status") or {}).get("type")
            if isinstance(thread.get("status"), dict) else None,
            "created_at": _iso_from_s(thread.get("createdAt")),
            "sandbox": self.config.sandbox,
            "approval_policy": self.config.approval_policy,
        }

    def send(self, *, device_ref: str, provider_session_id: str, instruction: str,
             turn_idempotency_key: str) -> dict:
        row = self.inspect(device_ref=device_ref,
                           provider_session_id=provider_session_id,
                           include_turns=False)
        if row.get("can_accept_input") is False:
            # Refused here rather than at the provider, so that the reason names
            # the field that says so. The BotMux slice paid for this once: the
            # first async session it created died and closed, and
            # `can_accept_input: false` sat in the very row `send` ignored.
            raise CodexUnsupported(
                f"thread {provider_session_id!r} reports canAcceptDirectInput: false; "
                f"codex will not accept a further turn on it")
        return self._start_turn(
            device_ref=device_ref, provider_session_id=provider_session_id,
            instruction=instruction, idempotency_key=turn_idempotency_key)

    def result(self, *, provider_session_id: str,
               trigger_id: str | None = None) -> dict:
        """The current turn's outcome, from what this adapter has observed.

        `trigger_id` is accepted for interface symmetry and is used as a
        TURN-id filter when given: the BotMux adapter's trigger ids and codex's
        turn ids are the same role, and a caller polling a turn gets the answer
        for THAT turn rather than for whatever ran last.

        Nothing is inferred from the absence of a notification. An adapter that
        restarted has witnessed no turns, and a thread read is the provider's
        answer rather than this adapter's memory — so the fallback is a real
        call, not a guess.
        """
        process = self.process()
        seen = [t for t in process.turns_for(provider_session_id)
                if t.get("event") == "completed"]
        if trigger_id:
            seen = [t for t in seen if t.get("id") == trigger_id]
        turn = seen[-1] if seen else None
        observation_source = "codex:turn/completed notification"

        if turn is None:
            # Falling back to `thread/read` rather than to memory: an adapter
            # that restarted has witnessed no notifications, and the thread's
            # own turns are the provider's answer rather than this process's
            # recollection, so the result survives a restart.
            stored = self._read_turns(provider_session_id)
            if trigger_id:
                stored = [t for t in stored if t.get("id") == trigger_id]
            if stored:
                turn = stored[-1]
                observation_source = "codex:thread/read"

        if turn is None:
            return {
                "provider": self.provider_id,
                "provider_session_id": provider_session_id,
                "scope": "current-turn",
                "turn_id": trigger_id,
                "turn_state": "TURN_NOT_FOUND",
                "turn_state_provenance": "FACT",
                "provider_state": None,
                "output": None,
                "observation_source": "neither a notification nor thread/read reported a turn",
            }

        state, provenance, reason = self._turn_state(turn)
        out = {
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            # `scope` is not decoration. A caller that reads TURN_COMPLETED as
            # "this session is finished" will stop polling a session that is
            # still alive and still accepting turns.
            "scope": "current-turn",
            "turn_id": turn.get("id"),
            "turn_state": state,
            "turn_state_provenance": provenance,
            "provider_state": turn.get("status"),
            "output": self._final_answer(turn.get("items")),
            "error": (turn.get("error") or {}).get("message")
            if isinstance(turn.get("error"), dict) else None,
            "error_code": (turn.get("error") or {}).get("codexErrorInfo")
            if isinstance(turn.get("error"), dict) else None,
            "finished_at": _iso_from_s(turn.get("completedAt")),
            "observation_source": observation_source,
        }
        if reason:
            out["turn_state_reason"] = reason
        if state in ("TURN_COMPLETED", "TURN_FAILED"):
            self._record_execution(turn_state=state, error=out.get("error"),
                                   provider_session_id=provider_session_id)
        return out

    def _read_turns(self, provider_session_id: str) -> list[dict]:
        try:
            result = self.process().call(
                "thread/read", {"threadId": provider_session_id, "includeTurns": True})
        except CodexProtocolError as exc:
            if "ephemeral" in exc.message:
                return []
            raise
        thread = result.get("thread") or {}
        return [t for t in (thread.get("turns") or []) if isinstance(t, dict)]

    # -- session control ---------------------------------------------------

    def interrupt(self, *, provider_session_id: str) -> dict:
        """Interrupt the turn that is running, and say which one it was.

        The turn id is resolved from provider evidence rather than from memory:
        an adapter that restarted has witnessed nothing, and `turn/interrupt`
        requires a `turnId`. Resolving it here means the verb works on the
        second process as well as on the first.
        """
        turns = self._read_turns(provider_session_id)
        active = [t for t in turns
                  if str(t.get("status") or "") in ("inProgress", "in_progress", "running")]
        if not active:
            raise CodexNoActiveTurn(
                f"thread {provider_session_id!r} has no turn in progress; "
                f"turn/interrupt is turn-scoped and there is nothing to interrupt. "
                f"This is not a session stop — see the session.stop support reason")
        turn = active[-1]
        self.process().call("turn/interrupt", {
            "threadId": provider_session_id, "turnId": turn.get("id"),
        }, timeout=self.config.timeout)
        return {
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            "action": "session.interrupt",
            "interrupted_turn_id": turn.get("id"),
            "scope": "current-turn",
            "note": ("the turn was interrupted; the thread is unchanged and still "
                     "accepts turns"),
        }

    def resume(self, *, provider_session_id: str, device_ref: str | None = None) -> dict:
        """Load a thread into this app-server instance and report what it is.

        `device_ref` is optional and the session_ref is absent without it, which
        is not the same as empty. A session ref names a device; a provider that
        mints one from a device it guessed at produces a well-formed string that
        addresses the wrong machine, and the failure surfaces two layers away.
        The service supplies no device to a provider — it hands over a native id
        and names the session itself — so this is the shape a direct caller uses
        and the service does not.
        """
        result = self.process().call(
            "thread/resume", {"threadId": provider_session_id,
                              "excludeTurns": True},
            timeout=self.config.timeout)
        thread = result.get("thread") or {}
        row = self._session_row(thread, device_ref=device_ref or "") if device_ref else {}
        row = {**row,
               "provider": self.provider_id,
               "provider_session_id": provider_session_id,
               "action": "session.resume",
               "note": ("the thread is loaded into this app-server instance; the "
                        "session_ref is unchanged because the thread id is")}
        if device_ref:
            row["session_ref"] = session_ref(device_ref, self.provider_id,
                                             provider_session_id)
            row["device_ref"] = device_ref
        return row

    def stop(self, *, provider_session_id: str) -> dict:
        raise CodexUnsupported(self.actions()["session.stop"]["reason"])

    def handoff(self, *, provider_session_id: str) -> dict:
        raise CodexUnsupported(self.actions()["session.handoff"]["reason"])


def main(argv: list[str] | None = None) -> int:
    """Probe the installed app-server and print what it reports. Read-only."""
    import argparse
    parser = argparse.ArgumentParser(prog="codex-app-server-provider",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("--device", default=None,
                        help="device ref to report against; default: fleet/node binding")
    parser.add_argument("--live", action="store_true",
                        help="actually start the app-server and list threads")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    device_ref = args.device
    if not device_ref:
        device_ref = os.environ.get("SERVEROS_DEVICE_REF")
    if not device_ref:
        sys.stderr.write("no --device and no SERVEROS_DEVICE_REF; "
                         "this command will not guess which machine it is on\n")
        return 2

    provider = CodexAppServerProvider()
    payload: dict = {"device_ref": device_ref, "provider": provider.provider_id,
                     "measured_against": MEASURED_AGAINST, "measured_on": MEASURED_ON}
    if args.live:
        try:
            payload["readiness"] = provider.observe(device_ref=device_ref)
            payload["threads"] = provider.list_sessions(device_ref=device_ref)
        finally:
            provider.close()
    else:
        payload["action_support"] = CodexAppServerProvider.actions()
        payload["agents"] = provider.list_agents(device_ref=device_ref)
    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
