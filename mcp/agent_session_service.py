#!/usr/bin/env python3
"""Provider-neutral Agent and Agent Session semantics.

This module owns normalization only. Providers own their native session
protocols; ServerOS owns device identity and policy. Mutating calls are refused
unless an admission callback supplied by the ServerOS authority allows them.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Protocol

_REPO = Path(__file__).resolve().parent.parent
_RUNTIME = _REPO / "runtime"


def _load_semantics():
    name = "serveros_runtime_agent_sessions"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _RUNTIME / "agent_sessions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


semantics = _load_semantics()


class AgentSessionError(RuntimeError):
    """Base class for session-provider composition failures."""


class UnknownProvider(AgentSessionError):
    pass


class UnknownSession(AgentSessionError):
    pass


class ActionNotAdmitted(AgentSessionError):
    """ServerOS admission answered something other than ALLOW."""


class ActionNotSupported(AgentSessionError):
    """The provider cannot perform this action at all; there is nothing to admit.

    Kept distinct from `ActionNotAdmitted` because the two refusals mean
    different things to a human. "Nobody has approved this yet" is resolved by
    a person. "This provider has no way to do this" is not, and reporting it as
    a pending approval would park a request in front of a human who cannot
    grant it.
    """

    def __init__(self, action: str, state: str, reason: str | None = None) -> None:
        self.action = action
        self.state = state
        self.reason = reason
        super().__init__(f"{action} unavailable: provider reports {state} — {reason or 'no reason supplied'}")


class AgentSessionProvider(Protocol):
    provider_id: str

    def list_agents(self, *, device_ref: str) -> list[dict]: ...
    def list_sessions(self, *, device_ref: str) -> list[dict]: ...
    def create(self, *, device_ref: str, agent_id: str, instruction: str,
               idempotency_key: str, workspace: str | None = None) -> dict: ...
    def send(self, *, device_ref: str, provider_session_id: str, instruction: str,
             turn_idempotency_key: str) -> dict: ...
    def result(self, *, provider_session_id: str, trigger_id: str | None = None) -> dict: ...
    def stop(self, *, provider_session_id: str) -> dict: ...


# ServerOS control verbs mapped to the provider method that would serve them.
# A verb is ABSENT here when ServerOS has no provider-native meaning for it, so
# no adapter can satisfy it by renaming a different mechanism.
CONTROL_VERBS: dict[str, str] = {
    "session.resume": "resume",
    "session.interrupt": "interrupt",
    "session.stop": "stop",
    "session.handoff": "handoff",
}


Admission = Callable[[str, dict], dict]


class SessionService:
    """One normalized session fabric over one device's provider set.

    Reads go straight to providers. Mutations first ask the injected ServerOS
    admission function. No provider can make itself authorized merely by
    advertising an action.
    """

    def __init__(self, *, device_ref: str, providers: list[AgentSessionProvider],
                 admission: Admission | None = None) -> None:
        self.device_ref = device_ref
        self.providers = {provider.provider_id: provider for provider in providers}
        self.admission = admission

    def _provider(self, provider_id: str) -> AgentSessionProvider:
        try:
            return self.providers[provider_id]
        except KeyError as exc:
            raise UnknownProvider(
                f"provider {provider_id!r} is not configured; known={sorted(self.providers)}"
            ) from exc

    def _admit(self, action: str, context: dict) -> dict:
        if self.admission is None:
            raise ActionNotAdmitted(
                f"{action} refused: no ServerOS admission authority was supplied"
            )
        decision = self.admission(action, context)
        if decision.get("decision") != "ALLOW":
            raise ActionNotAdmitted(
                f"{action} refused: {decision.get('decision', 'UNKNOWN')} — "
                f"{decision.get('reason', 'no reason supplied')}"
            )
        return decision

    def agent_list(self) -> dict:
        agents: list[dict] = []
        errors: list[dict] = []
        for provider_id in sorted(self.providers):
            try:
                agents.extend(self.providers[provider_id].list_agents(device_ref=self.device_ref))
            except Exception as exc:  # provider failure is evidence, not an empty fleet
                errors.append({
                    "provider": provider_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        return {
            "device_ref": self.device_ref,
            "agents": agents,
            "provider_errors": errors,
            "complete": not errors,
        }

    def agent_inspect(self, agent_id: str) -> dict:
        view = self.agent_list()
        matches = [a for a in view["agents"]
                   if a.get("agent_id") == agent_id or a.get("agent_ref") == agent_id]
        if not matches:
            raise AgentSessionError(f"no Agent matches {agent_id!r}")
        if len(matches) > 1:
            raise AgentSessionError(f"Agent identity is ambiguous: {agent_id!r}")
        return {"agent": matches[0], "complete": view["complete"],
                "provider_errors": view["provider_errors"]}

    def session_list(self) -> dict:
        sessions: list[dict] = []
        errors: list[dict] = []
        for provider_id in sorted(self.providers):
            try:
                sessions.extend(self.providers[provider_id].list_sessions(device_ref=self.device_ref))
            except Exception as exc:
                errors.append({"provider": provider_id,
                               "error": f"{type(exc).__name__}: {exc}"})
        sessions.sort(key=lambda s: (str(s.get("last_activity") or ""),
                                     str(s.get("session_ref") or "")), reverse=True)
        return {"device_ref": self.device_ref, "sessions": sessions,
                "provider_errors": errors, "complete": not errors}

    def session_inspect(self, ref: str) -> dict:
        device_ref, provider_id, native_id = semantics.parse_session_ref(ref)
        if device_ref != self.device_ref:
            raise UnknownSession(
                f"{ref!r} belongs to {device_ref}, not local device {self.device_ref}"
            )
        rows = self._provider(provider_id).list_sessions(device_ref=self.device_ref)
        matches = [s for s in rows if s.get("provider_session_id") == native_id]
        if not matches:
            raise UnknownSession(f"provider {provider_id!r} has no session {native_id!r}")
        return matches[0]

    def create(self, *, provider_id: str, agent_id: str, instruction: str,
               idempotency_key: str, workspace: str | None = None) -> dict:
        context = {"provider": provider_id, "agent_id": agent_id,
                   "workspace": workspace, "device_ref": self.device_ref}
        admission = self._admit("session.create", context)
        result = self._provider(provider_id).create(
            device_ref=self.device_ref, agent_id=agent_id,
            instruction=instruction, idempotency_key=idempotency_key,
            workspace=workspace,
        )
        return {**result, "admission": admission}

    def send(self, *, session_ref_value: str, instruction: str,
             turn_idempotency_key: str) -> dict:
        """Hand one follow-up turn to an existing session.

        The session's own declared support is consulted before admission, the
        same way `session_control` does it and for the same reason. A session
        that has reached CLOSED declares `session.send` UNSUPPORTED, and without
        this check the request travels all the way to the provider, which
        answers with a provider-specific error about a session it has already
        torn down. The live slice is what surfaced it: the first async session it
        created died and closed, and `can_accept_input: false` sat in the very
        same normalized row that `send` was ignoring.

        Support narrows; it does not authorize. A SUPPORTED send still needs its
        own ALLOW below.
        """
        device_ref, provider_id, native_id = semantics.parse_session_ref(session_ref_value)
        if device_ref != self.device_ref:
            raise UnknownSession(f"cannot drive remote session {session_ref_value!r} locally")
        provider = self._provider(provider_id)
        declared = self._declared_support(provider, "session.send", native_id)
        if declared.get("state") != "SUPPORTED":
            raise ActionNotSupported("session.send", str(declared.get("state") or "UNKNOWN"),
                                     declared.get("reason"))
        admission = self._admit("session.send", {
            "provider": provider_id, "session_ref": session_ref_value,
            "device_ref": self.device_ref,
        })
        result = provider.send(
            device_ref=self.device_ref, provider_session_id=native_id,
            instruction=instruction, turn_idempotency_key=turn_idempotency_key,
        )
        return {**result, "admission": admission}

    def result(self, *, session_ref_value: str, trigger_id: str | None = None) -> dict:
        device_ref, provider_id, native_id = semantics.parse_session_ref(session_ref_value)
        if device_ref != self.device_ref:
            raise UnknownSession(f"cannot inspect remote session {session_ref_value!r} locally")
        return self._provider(provider_id).result(
            provider_session_id=native_id, trigger_id=trigger_id,
        )

    def session_control(self, *, action: str, session_ref_value: str) -> dict:
        """Run a session-control verb through provider support, then admission.

        Support is checked FIRST and can only narrow. A provider's own declaration
        that it cannot interrupt a turn is an answer, not a gate to route around,
        and an unsupported verb is reported as unsupported rather than parked as
        a pending human approval that no human could grant.

        Support is also not authorization: a SUPPORTED verb still has to pass
        admission, so a provider cannot authorize its own mutation by
        advertising a capability.
        """
        if action not in CONTROL_VERBS:
            raise AgentSessionError(f"{action!r} is not a ServerOS session-control verb")
        device_ref, provider_id, native_id = semantics.parse_session_ref(session_ref_value)
        if device_ref != self.device_ref:
            raise UnknownSession(f"cannot drive remote session {session_ref_value!r} locally")
        provider = self._provider(provider_id)
        declared = self._declared_support(provider, action, native_id)
        if declared.get("state") != "SUPPORTED":
            raise ActionNotSupported(action, str(declared.get("state") or "UNKNOWN"),
                                     declared.get("reason"))
        method = getattr(provider, CONTROL_VERBS[action], None)
        if method is None:
            raise ActionNotSupported(action, "UNKNOWN",
                                     f"provider {provider_id!r} adapter implements no "
                                     f"{CONTROL_VERBS[action]}()")
        admission = self._admit(action, {
            "provider": provider_id, "session_ref": session_ref_value,
            "device_ref": self.device_ref,
        })
        result = method(provider_session_id=native_id)
        return {**result, "action": action, "admission": admission}

    def _declared_support(self, provider, action: str, provider_session_id: str) -> dict:
        rows = provider.list_sessions(device_ref=self.device_ref)
        for row in rows:
            if row.get("provider_session_id") == provider_session_id:
                return dict((row.get("action_support") or {}).get(action) or {})
        raise UnknownSession(
            f"provider {provider.provider_id!r} has no session {provider_session_id!r}"
        )

    def interrupt(self, *, session_ref_value: str) -> dict:
        return self.session_control(action="session.interrupt",
                                    session_ref_value=session_ref_value)

    def resume(self, *, session_ref_value: str) -> dict:
        return self.session_control(action="session.resume",
                                    session_ref_value=session_ref_value)

    def handoff(self, *, session_ref_value: str) -> dict:
        return self.session_control(action="session.handoff",
                                    session_ref_value=session_ref_value)

    def stop(self, *, session_ref_value: str) -> dict:
        return self.session_control(action="session.stop",
                                    session_ref_value=session_ref_value)
