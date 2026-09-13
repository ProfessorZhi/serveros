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


def _load_readiness():
    name = "serveros_ability_provider_instance"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, _REPO / "abilities" / "provider_instance.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


semantics = _load_semantics()
readiness = _load_readiness()


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


class ProviderNotReady(AgentSessionError):
    """The provider is known, right now, not to be able to carry this out.

    The third refusal, and it is not either of the other two.

    `ActionNotSupported` means the provider has no way to do this at all — the
    instruction is meaningless over this transport and will stay meaningless.
    `ActionNotAdmitted` means nobody has approved it — a person resolves it.
    This one means the provider has the way, the caller may have the approval,
    and the thing is not going to work at the moment it is asked: the execution
    backend is gone, the credential is expired, the service is up and serving
    nothing.

    It is reported under its own name because a caller that is told
    "unsupported" stops trying, a caller that is told "not approved" asks a
    human, and a caller that is told "not ready" waits and asks the world what
    changed. Only the last of those is true here.

    Only a KNOWN NEGATIVE raises this. Readiness UNKNOWN does not: a provider
    with no readiness surface is always UNKNOWN before it is called, and
    refusing on that would make it permanently undrivable. The call is what
    resolves UNKNOWN, which is why this check can narrow the field but never
    become a reason not to work.
    """

    def __init__(self, provider_id: str, status: str, reason: str | None = None,
                 reason_text: str | None = None) -> None:
        self.provider_id = provider_id
        self.status = status
        self.reason = reason
        self.reason_text = reason_text
        super().__init__(
            f"{provider_id} is not ready: {status} ({reason or 'no reason supplied'}) — "
            f"{reason_text or 'no detail supplied'}")


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


def _readiness_summary(record: dict | None) -> dict:
    """The readiness part of a mutation result, without the evidence dump.

    A caller that just drove a provider gets told what the provider was known
    to be. The observations behind that verdict are not repeated here: a
    mutation result is not the place to paste a record that can be arbitrarily
    wide, and the full record already travels on `agent.list` under
    `providers[]`, which is where the finding this exists for — agents listed,
    provider unable to execute — is read.
    """
    if record is None:
        return {}
    return {key: record.get(key) for key in
            ("provider", "status", "status_provenance", "reason", "observed_at")
            if record.get(key) is not None}


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

    def readiness(self, provider_id: str) -> dict:
        """This device's readiness for one provider, with the evidence behind it.

        A provider that cannot be asked is not a provider that is ready. This
        returns a UNKNOWN record for one with no `observe`, and never an
        AVAILABLE one — an unimplemented observer used to be spelled by an
        absent method, and an absent method read as an absent problem.

        Both branches return the record through `abilities/provider_instance.py`,
        which is the one producer of the shape `abilities/provider-contract.yaml`
        declares. An earlier revision assembled the no-observer record inline as
        a dict literal, and it had already drifted: it was missing `observed_at`,
        which that contract lists as required. A second producer of a closed
        shape is a producer that disagrees with the first eventually, and the
        field it had lost was the one that says when any of it was true.
        """
        provider = self._provider(provider_id)
        observer = getattr(provider, "observe", None)
        if observer is not None:
            return observer(device_ref=self.device_ref)

        observed_at = readiness.now_utc()
        return readiness.instance(
            provider=provider_id, device_ref=self.device_ref,
            observations=[
                # The control-plane axis is observed to be UNKNOWN rather than
                # left out, so the record says which question went unasked
                # rather than merely that the answer is unknown. `none` is a
                # permitted observer type here precisely because the state is
                # UNKNOWN: `runtime/health.py` refuses a definite state carried
                # by an observer that did not observe anything.
                readiness.observation(
                    axis=readiness.AXIS_CONTROL_PLANE, state="UNKNOWN",
                    observer_type="none", observer_id="no-observer-implemented",
                    observed_at=observed_at, evidence_type="none",
                    reason="NOT_DECLARED",
                    extra={"why": (f"{provider_id} implements no observe(); "
                                   "nothing has established that it can execute")},
                ),
                readiness.unknown_execution(observed_at=observed_at,
                                            provider_id=provider_id),
            ],
            reference=observed_at,
        )

    def _preflight(self, provider_id: str) -> dict | None:
        """Refuse a call the provider is known not to be able to carry out.

        Ordered after support and before admission, on purpose:

          support    a capability FACT      — can this provider do it at all
          readiness  a runtime FACT         — would it work right now
          admission  a POLICY decision      — is this permitted

        Readiness sits before admission because it only ever narrows: it can
        refuse a call admission would have allowed, and it can never allow one
        admission would have refused. A check with that property cannot weaken
        authorization, and putting it first means a human is not asked to
        approve something that cannot run.

        It does not merge with admission. A caller is told which of the three
        stopped it, because the three have three different repairs.
        """
        record = self.readiness(provider_id)
        refusal = readiness.refusal(record)
        if refusal is None:
            return record
        raise ProviderNotReady(provider_id, refusal["status"], refusal.get("reason"),
                               refusal.get("reason_text"))

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
        providers: list[dict] = []
        for provider_id in sorted(self.providers):
            try:
                agents.extend(self.providers[provider_id].list_agents(device_ref=self.device_ref))
            except Exception as exc:  # provider failure is evidence, not an empty fleet
                errors.append({
                    "provider": provider_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
            # Readiness travels with the agent inventory rather than behind a
            # new tool, so that the surface stays exactly as wide as the
            # semantic contract declares it. The reason to put it here is that
            # this is the list an operator actually reads before driving
            # something, and the failure this models — agents listed, provider
            # unable to execute — is visible in exactly one place: here.
            try:
                providers.append(self.readiness(provider_id))
            except Exception as exc:
                errors.append({"provider": provider_id,
                               "error": f"readiness: {type(exc).__name__}: {exc}"})
        return {
            "device_ref": self.device_ref,
            "agents": agents,
            "providers": providers,
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
        ready = self._preflight(provider_id)
        admission = self._admit("session.create", context)
        result = self._provider(provider_id).create(
            device_ref=self.device_ref, agent_id=agent_id,
            instruction=instruction, idempotency_key=idempotency_key,
            workspace=workspace,
        )
        return {**result, "admission": admission,
                "provider_readiness": _readiness_summary(ready)}

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
        ready = self._preflight(provider_id)
        admission = self._admit("session.send", {
            "provider": provider_id, "session_ref": session_ref_value,
            "device_ref": self.device_ref,
        })
        result = provider.send(
            device_ref=self.device_ref, provider_session_id=native_id,
            instruction=instruction, turn_idempotency_key=turn_idempotency_key,
        )
        return {**result, "admission": admission,
                "provider_readiness": _readiness_summary(ready)}

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
        ready = self._preflight(provider_id)
        admission = self._admit(action, {
            "provider": provider_id, "session_ref": session_ref_value,
            "device_ref": self.device_ref,
        })
        result = method(provider_session_id=native_id)
        return {**result, "action": action, "admission": admission,
                "provider_readiness": _readiness_summary(ready)}

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
