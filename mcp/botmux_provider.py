#!/usr/bin/env python3
"""BotMux AgentSessionProvider using BotMux's supported Dashboard API.

No `~/.botmux` session files are read here. BotMux owns native session truth;
this adapter consumes its external read model and trigger/poll/close contract.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import importlib.util
import sys
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
# Readiness is derived in `abilities/provider_instance.py`, which is the
# implementation of the `instance:` record `abilities/provider-contract.yaml`
# declared. Loaded rather than reimplemented: this adapter's job is to produce
# the observations, not to decide what they add up to.
instances = _load("serveros_abilities_provider_instance",
                  _ABILITIES / "provider_instance.py")
agent_ref = semantics.agent_ref
session_ref = semantics.session_ref
support = semantics.support


class BotMuxError(RuntimeError):
    pass


class BotMuxCredentialMissing(BotMuxError):
    pass


class BotMuxUnsupported(BotMuxError):
    pass


@dataclass(frozen=True)
class BotMuxConfig:
    base_url: str = "http://127.0.0.1:7891"
    dashboard_token: str | None = None
    timeout: float = 5.0

    @classmethod
    def from_env(cls) -> "BotMuxConfig":
        return cls(
            base_url=os.environ.get("SERVEROS_BOTMUX_URL", cls.base_url).rstrip("/"),
            dashboard_token=os.environ.get("SERVEROS_BOTMUX_DASHBOARD_TOKEN") or None,
            timeout=float(os.environ.get("SERVEROS_BOTMUX_TIMEOUT", "5")),
        )


def _iso_from_ms(value: Any) -> str | None:
    try:
        seconds = float(value) / 1000.0
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class BotMuxProvider:
    provider_id = "provider:botmux-agent-session"

    def __init__(self, config: BotMuxConfig | None = None, clock=None) -> None:
        self.config = config or BotMuxConfig.from_env()
        # The clock is injected, not read, so that freshness is testable without
        # waiting 15 minutes and so that a record's `observed_at` is a property
        # of the call rather than of whichever wall the host happens to have.
        self._clock = clock
        # Outcomes of turns this instance actually drove. In memory and
        # deliberately not on disk: a persisted readiness is a memory of a past
        # resolution read as a current one, and a persisted NEGATIVE keeps
        # refusing a provider that has since been repaired. The cost is named
        # rather than hidden — a restarted process reports UNKNOWN until it has
        # called the provider again, which is the honest value there.
        self._execution_log: list[dict] = []

    def _now(self) -> str:
        return instances.now_utc(self._clock)

    @staticmethod
    def _runtime_ref(native_runtime: str | None) -> str | None:
        runtime_id = {
            "claude-code": "claude-code",
            "codex": "codex",
            "codex-app": "codex",
        }.get(str(native_runtime or ""))
        return f"agent-runtime:{runtime_id}" if runtime_id else None

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 require_auth: bool = False) -> dict:
        token = self.config.dashboard_token
        if require_auth and not token:
            raise BotMuxCredentialMissing(
                "BotMux Dashboard authentication is required; inject "
                "SERVEROS_BOTMUX_DASHBOARD_TOKEN into the provider process"
            )
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
                raw = response.read()
                data = json.loads(raw.decode() or "{}")
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode() or "{}")
            except Exception:
                detail = {}
            reason = detail.get("errorCode") or detail.get("error") or exc.reason
            raise BotMuxError(f"BotMux {method} {path} refused ({exc.code}): {reason}") from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise BotMuxError(
                f"BotMux {method} {path} unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise BotMuxError(f"BotMux {method} {path} returned a non-object response")
        return data

    @staticmethod
    def _actions(*, closed: bool = False, quarantined: bool = False) -> dict:
        send_state = "UNSUPPORTED" if closed or quarantined else "SUPPORTED"
        send_reason = "session is closed or quarantined" if send_state == "UNSUPPORTED" else None
        return {
            "session.inspect": support("SUPPORTED"),
            "session.send": support(send_state, send_reason),
            "session.result": support("SUPPORTED"),
            # `botmux resume <id>` DOES exist — but as an IM/CLI verb, not as a
            # Dashboard API endpoint. This adapter speaks only the Dashboard
            # HTTP API, so the honest answer is "not over this transport", and
            # the reason says which transport it IS available over rather than
            # claiming the capability does not exist. A reason that overstated
            # the gap would send the next reader looking for a feature BotMux
            # already has.
            "session.resume": support(
                "UNSUPPORTED",
                "BotMux exposes resume as the IM/CLI verb `botmux resume <id>`; the "
                "Dashboard HTTP API this adapter speaks has no resume endpoint. A send "
                "to a dormant session cold-resumes it instead",
            ),
            "session.interrupt": support(
                "UNSUPPORTED",
                "BotMux v3.18 close terminates the whole session, not only the current turn",
            ),
            "session.stop": support("SUPPORTED"),
            "session.handoff": support(
                "UNKNOWN",
                "BotMux /relay exists at the IM command layer but no stable external handoff API is used here",
            ),
        }

    # BotMux reports a `status` string and several booleans BESIDE it. The
    # booleans are evidence about what the session is doing; none of them is a
    # status, and none may override one.
    #
    # The version before this treated `queued` as a status and returned PAUSED
    # for it. A row BotMux reports as `working` with a queued follow-up is a
    # session that is demonstrably running, and that row normalized to PAUSED —
    # a claim about the whole session manufactured from a field that says
    # nothing about the whole session, resting on a reading of BotMux internals
    # this adapter cannot verify. `queued` is preserved verbatim instead, and
    # the vertical slice is what settled it: the live session it drives is
    # `dormant` with `queued: false`, and a normalization that decides what a
    # field means without ever having seen it set is a normalization that has
    # not been tested against the thing it describes.
    #
    # The screen-derived states are INFERRED and say so. That distinction is
    # the point of `status_provenance`: a reader deciding whether to interrupt
    # a session is entitled to know that BotMux read `working` off a terminal
    # rather than off a process table.
    _STATUS_MAP: dict[str, tuple[str, str, str]] = {
        "starting": ("STARTING", "FACT", "provider worker is starting"),
        "working": ("BUSY", "INFERRED", "BotMux derives `working` from CLI screen content"),
        "analyzing": ("BUSY", "INFERRED", "BotMux derives `analyzing` from CLI screen content"),
        "idle": ("IDLE", "INFERRED", "BotMux derives `idle` from CLI screen content"),
        "dormant": ("DETACHED", "FACT", "logical session exists with no resident worker"),
        "limited": ("WAITING_INPUT", "INFERRED", "BotMux reports screen status `limited`"),
        "stalled": ("WAITING_INPUT", "INFERRED", "BotMux reports screen status `stalled`"),
    }

    @classmethod
    def _normalize_status(cls, row: dict) -> tuple[str, str, str]:
        raw = str(row.get("status") or "").lower()
        # Terminal and operator-held states come first: an attention flag on a
        # closed session does not make it interactive.
        if row.get("quarantined"):
            return "PAUSED", "FACT", "provider restore quarantine"
        if raw == "closed":
            return "CLOSED", "FACT", "provider session is closed"
        if row.get("pendingRepo") or row.get("tuiPromptActive") or row.get("agentAttention"):
            return "WAITING_INPUT", "FACT", "provider explicitly reports attention is required"
        if raw in cls._STATUS_MAP:
            return cls._STATUS_MAP[raw]
        return "UNKNOWN", "UNKNOWN", f"unmapped BotMux status {raw!r}"

    # The four states `/trigger-result` answers with describe ONE TURN that was
    # accepted by `/api/trigger`. They are not session states, and the names
    # say so rather than leaving a reader to conflate `completed` with "the
    # session finished". A session whose current turn completed is still a
    # session that exists and can be sent another turn.
    _TURN_MAP: dict[str, str] = {
        "running": "TURN_RUNNING",
        "completed": "TURN_COMPLETED",
        "failed": "TURN_FAILED",
        "not_found": "TURN_NOT_FOUND",
    }

    @classmethod
    def _normalize_turn(cls, raw: Any) -> tuple[str, str]:
        token = str(raw or "").strip().lower()
        if token in cls._TURN_MAP:
            return cls._TURN_MAP[token], "FACT"
        return "TURN_UNKNOWN", "UNKNOWN"

    # The fields BotMux reports BESIDE `status`. Copied verbatim and never
    # interpreted into a state — that rule has not changed. What has changed is
    # the record around them.
    _EVIDENCE_FIELDS = ("queued", "locked", "adopt", "agentAttention",
                        "tuiPromptActive", "pendingRepo", "hasHistory",
                        "lastInputFromBot")

    def _provider_evidence(self, row: dict) -> dict:
        """Structured evidence: what was read, from where, when, on what strength.

        This used to be a bare mapping of field -> value, which is real evidence
        with nothing around it. It could not answer which call produced it, when,
        or whether the reader was looking at a provider fact or at this adapter's
        interpretation — and the record it sat in carries `status_reason`, which
        is a SENTENCE. Two fields that look alike and mean different things is
        how "evidence" came to mean "whatever is in this slot".

        So the four are now separate and separately typed:

            status_reason      a sentence for a person
            evidence.source    the call that produced it
            evidence.observed_at   when, from an injectable clock
            evidence.provenance/authoritative  how strong, in health.py's words

        `evidence.type` is `runtime/health.py`'s EVIDENCE_STRENGTH key rather
        than a new one, so "is this authoritative" has one answer in one place.
        """
        return {
            "source": "botmux:/api/sessions",
            "observed_at": self._now(),
            "provenance": "FACT",
            "evidence_type": "provider-response",
            "authoritative": True,
            "fields": {key: row[key] for key in self._EVIDENCE_FIELDS if key in row},
        }

    def list_agents(self, *, device_ref: str) -> list[dict]:
        rows = self._request("GET", "/api/bots", require_auth=True).get("bots") or []
        if not isinstance(rows, list):
            raise BotMuxError("BotMux /api/bots response has no list-valued bots field")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            native_id = str(row.get("larkAppId") or "").strip()
            if not native_id:
                continue
            native_runtime = str(row.get("cliId") or "unknown")
            profile_ref = self._runtime_ref(native_runtime)
            out.append({
                "agent_id": native_id,
                "agent_ref": agent_ref(device_ref, self.provider_id, native_id),
                "device_ref": device_ref,
                "provider": self.provider_id,
                "provider_agent_id": native_id,
                "display_name": row.get("botName") or native_id,
                "agent_runtime": profile_ref or "UNKNOWN",
                "native_runtime": native_runtime,
                # `online: false` is BotMux stating a fact, and reporting it as
                # UNKNOWN would hide the one answer an operator most needs. Only
                # an ABSENT field is unknown.
                "availability": ("AVAILABLE" if row.get("online") is True
                                 else "OFFLINE" if row.get("online") is False
                                 else "UNKNOWN"),
                "availability_provenance": ("FACT" if isinstance(row.get("online"), bool)
                                            else "UNKNOWN"),
                "runtime_registered": profile_ref is not None,
                "action_support": {
                    "session.create": support("SUPPORTED"),
                    "session.list": support("SUPPORTED"),
                },
                "provenance": "botmux:/api/bots",
            })
        return out

    def list_sessions(self, *, device_ref: str) -> list[dict]:
        rows = self._request("GET", "/api/sessions").get("sessions") or []
        if not isinstance(rows, list):
            raise BotMuxError("BotMux /api/sessions response has no list-valued sessions field")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            native_id = str(row.get("sessionId") or "").strip()
            if not native_id:
                continue
            provider_agent_id = str(row.get("larkAppId") or "").strip()
            native_runtime = str(row.get("cliId") or "unknown")
            profile_ref = self._runtime_ref(native_runtime)
            status, provenance, status_reason = self._normalize_status(row)
            closed = status == "CLOSED"
            quarantined = bool(row.get("quarantined"))
            ref = session_ref(device_ref, self.provider_id, native_id)
            item = {
                "session_ref": ref,
                "device_ref": device_ref,
                "provider": self.provider_id,
                "provider_session_id": native_id,
                "provider_agent_id": provider_agent_id or None,
                "agent_ref": (agent_ref(device_ref, self.provider_id, provider_agent_id)
                              if provider_agent_id else None),
                "agent_runtime": profile_ref or "UNKNOWN",
                "native_runtime": native_runtime,
                "status": status,
                "status_provenance": provenance,
                "provider_status": row.get("status"),
                "status_reason": status_reason,
                "provider_evidence": self._provider_evidence(row),
                "can_accept_input": not closed and not quarantined,
                "action_support": self._actions(closed=closed, quarantined=quarantined),
                "transport": "botmux-dashboard-api",
            }
            optional = {
                "title": row.get("title"),
                "workspace": row.get("workingDir"),
                "cwd": row.get("workingDir"),
                "created_at": _iso_from_ms(row.get("spawnedAt")),
                "last_activity": _iso_from_ms(row.get("lastMessageAt")),
            }
            item.update({k: v for k, v in optional.items() if v is not None})
            out.append(item)
        return out

    # -----------------------------------------------------------------------
    # Readiness — the second axis.
    #
    # Everything above answers "does it answer". This answers "does it work",
    # and it is the question nothing in ServerOS could ask before the vertical
    # slice: BotMux listed, listed, and accepted a trigger with a real
    # sessionId, and every turn failed because the execution backend under it
    # was gone. `action_support = SUPPORTED` was true and useless.
    #
    # No `~/.botmux/data/*` is read here. No tmux socket is inspected. No
    # BotMux private state is opened. There is no second truth source: the two
    # inputs are the provider's own documented API and the outcomes of turns
    # this adapter actually drove, which BotMux itself reported.
    # -----------------------------------------------------------------------

    def _record_execution(self, *, outcome: str, error_code: str | None,
                          detail: str | None, provider_session_id: str) -> None:
        """Record a turn outcome this adapter observed, for readiness to derive from.

        Called only from `result()`, and only with what the provider's own
        `trigger-result` endpoint returned. This is the third of the three
        permitted sources — a real action/result evidence — and it is the only
        one available, because probing for a readiness endpoint found none.
        """
        self._execution_log.append({
            "state": outcome,
            "observed_at": self._now(),
            "error_code": error_code,
            "detail": detail,
            "provider_session_id": provider_session_id,
        })
        # Bounded so a long-lived process does not accumulate one record per
        # turn forever. The window keeps only what freshness could still use.
        if len(self._execution_log) > 64:
            del self._execution_log[:-64]

    def _execution_observation(self, *, observed_at: str):
        """The most recent execution outcome, or the honest absence of one."""
        if not self._execution_log:
            return instances.unknown_execution(observed_at=observed_at,
                                               provider_id=self.provider_id)
        latest = self._execution_log[-1]
        state = "OK" if latest["state"] == "TURN_COMPLETED" else "FAILED"
        reason = "EXECUTION_FAILED" if state == "FAILED" else None
        extra = {"error_code": latest.get("error_code"),
                 "provider_session_id": latest.get("provider_session_id"),
                 "detail": (latest.get("detail") or "")[:400] or None}
        return instances.observation(
            axis=instances.AXIS_EXECUTION, state=state,
            observer_type="provider-response", observer_id=self.provider_id,
            observed_at=latest["observed_at"], evidence_type="provider-response",
            reason=reason, extra=extra,
        )

    def observe(self, *, device_ref: str) -> dict:
        """This device's readiness for this provider, with the evidence behind it.

        Reachability is measured by calling, not assumed from configuration: the
        same `/api/sessions` call `list_sessions` makes, whose failure IS the
        observation. A provider that cannot be reached is not an error here —
        it is a STOPPED control plane, which is a finding rather than a crash,
        and the difference matters because a caller asked a question and
        deserves an answer rather than an exception.
        """
        observed_at = self._now()
        try:
            self._request("GET", "/api/sessions")
        except BotMuxError as exc:
            control = instances.observation(
                axis=instances.AXIS_CONTROL_PLANE, state="STOPPED",
                observer_type="host-measurement", observer_id=self.provider_id,
                observed_at=observed_at, evidence_type="host-measurement",
                extra={"why": f"{type(exc).__name__}: {exc}"[:300]},
            )
            execution = instances.unknown_execution(
                observed_at=observed_at, provider_id=self.provider_id)
        else:
            control = instances.observation(
                axis=instances.AXIS_CONTROL_PLANE, state="RUNNING",
                observer_type="provider-response", observer_id=self.provider_id,
                observed_at=observed_at, evidence_type="provider-response",
                extra={"source": "botmux:/api/sessions"},
            )
            execution = self._execution_observation(observed_at=observed_at)

        return instances.instance(
            provider=self.provider_id, device_ref=device_ref,
            observations=[control, execution], reference=observed_at,
        )

    def _bot_for_session(self, provider_session_id: str) -> str:
        rows = self._request("GET", "/api/sessions").get("sessions") or []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("sessionId") == provider_session_id:
                bot_id = str(row.get("larkAppId") or "").strip()
                if bot_id:
                    return bot_id
        raise BotMuxError(f"BotMux session {provider_session_id!r} is not in /api/sessions")

    @staticmethod
    def _trigger_body(*, bot_id: str, instruction: str, session_id: str | None,
                      idempotency_key: str, follow_up: bool) -> dict:
        target = {"kind": "turn", "botId": bot_id}
        if session_id:
            target["sessionId"] = session_id
        options = {"asyncReturnSessionId": True}
        key_name = "turnIdempotencyKey" if follow_up else "idempotencyKey"
        options[key_name] = idempotency_key
        return {
            "source": {"type": "webhook"},
            "target": target,
            "instruction": instruction,
            "envelope": {"format": "text", "sourceName": "serveros", "trusted": False},
            "options": options,
        }

    def create(self, *, device_ref: str, agent_id: str, instruction: str,
               idempotency_key: str, workspace: str | None = None) -> dict:
        if workspace:
            raise BotMuxUnsupported(
                "BotMux v3.18 /api/trigger cannot bind a fresh virtual session to an arbitrary workingDir; "
                "select an existing workspace-bound session or add a provider-native create adapter"
            )
        data = self._request(
            "POST", "/api/trigger", require_auth=True,
            body=self._trigger_body(
                bot_id=agent_id, instruction=instruction, session_id=None,
                idempotency_key=idempotency_key, follow_up=False,
            ),
        )
        native_id = str((data.get("async") or {}).get("sessionId")
                        or (data.get("target") or {}).get("sessionId") or "")
        if not native_id:
            raise BotMuxError("BotMux accepted create but returned no sessionId")
        ref = session_ref(device_ref, self.provider_id, native_id)
        return {
            "session_ref": ref,
            "provider": self.provider_id,
            "provider_session_id": native_id,
            "trigger_id": data.get("triggerId"),
            # The trigger was accepted; no turn result exists yet. Writing
            # "QUEUED" here would have been a session state invented by this
            # adapter — a claim BotMux never made.
            "turn_state": "TURN_UNKNOWN",
            "turn_state_provenance": "UNKNOWN",
            "turn_state_reason": "trigger accepted; read session.result for the turn result",
            "provider_async_status": (data.get("async") or {}).get("status"),
            "idempotent": bool(data.get("idempotent")),
        }

    def send(self, *, device_ref: str, provider_session_id: str, instruction: str,
             turn_idempotency_key: str) -> dict:
        bot_id = self._bot_for_session(provider_session_id)
        data = self._request(
            "POST", "/api/trigger", require_auth=True,
            body=self._trigger_body(
                bot_id=bot_id, instruction=instruction,
                session_id=provider_session_id,
                idempotency_key=turn_idempotency_key, follow_up=True,
            ),
        )
        ref = session_ref(device_ref, self.provider_id, provider_session_id)
        return {
            "session_ref": ref,
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            "trigger_id": data.get("triggerId"),
            "turn_state": "TURN_UNKNOWN",
            "turn_state_provenance": "UNKNOWN",
            "turn_state_reason": "trigger accepted; read session.result for the turn result",
            "provider_async_status": (data.get("async") or {}).get("status"),
            "idempotent": bool(data.get("idempotent")),
            "resume_semantics": "dormant sessions are cold-resumed by the accepted turn",
        }

    def result(self, *, provider_session_id: str,
               trigger_id: str | None = None) -> dict:
        path = f"/api/sessions/{provider_session_id}/trigger-result"
        if trigger_id:
            from urllib.parse import urlencode
            path += "?" + urlencode({"triggerId": trigger_id})
        data = self._request("GET", path, require_auth=True)
        turn_state, provenance = self._normalize_turn(data.get("state"))
        # A settleable turn outcome is readiness evidence, and this is the only
        # place one is produced. Recorded from the provider's own report and
        # nothing else: a turn that is still running says nothing about whether
        # execution works, so only the terminal states are written down.
        if turn_state in ("TURN_COMPLETED", "TURN_FAILED"):
            self._record_execution(
                outcome=turn_state, error_code=data.get("errorCode"),
                detail=data.get("error"), provider_session_id=provider_session_id)
        return {
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            # `scope` is not decoration. A caller that reads TURN_COMPLETED as
            # "this session is finished" will stop polling a session that is
            # still alive and still accepting turns.
            "scope": "current-turn",
            "trigger_id": data.get("triggerId") or trigger_id,
            "turn_state": turn_state,
            "turn_state_provenance": provenance,
            "provider_state": data.get("state"),
            "output": (data.get("output") or {}).get("content"),
            "finished_at": data.get("finishedAt"),
            "error_code": data.get("errorCode"),
            "error": data.get("error"),
            "usage": data.get("usage"),
        }

    def stop(self, *, provider_session_id: str) -> dict:
        data = self._request(
            "POST", f"/api/sessions/{provider_session_id}/close",
            require_auth=True,
        )
        return {
            "provider": self.provider_id,
            "provider_session_id": provider_session_id,
            "state": "CLOSED",
            "already_closed": bool(data.get("alreadyClosed")),
            "note": "BotMux close stops the whole session; it is not a turn-only interrupt",
        }
