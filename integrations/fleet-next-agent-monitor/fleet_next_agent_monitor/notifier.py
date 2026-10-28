"""The one notifier seam: #423's ledger, injected. Never a second one.

The Fleet Next Agent Monitor does not own a ledger, a sender, a Feishu credential
or a queue. It composes with the single existing notifier — #423's
`MilestoneNotifier` (durable sqlite claim/receipt ledger, <=50-char transport
dedupe) — through `observe_payload(source, payload)`.

That seam is *injected by module path* rather than imported, for one honest
reason: #423 has not merged, so its module is not on `main`. A monitor that
imported it directly would not import at all; a monitor that reimplemented it
would be the second ledger the owner decision forbids. So the rule here is:

    No binding, no delivery. And a missing binding is a *named* blocker
    (`BLOCKED_LEDGER`), not a silent success and not a private replacement.

The payloads built below are #423's own closed envelopes. The monitor's job is
to decide *whether a fact may be handed over at all* — it hands over only
`TERMINAL_VERIFIED` and `FAILED`, never a `completed` without native activity,
never a draft or off-commit pipeline.
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any, Callable, Mapping, Protocol

from .contract import (
    STATUS_FAILED,
    STATUS_TERMINAL_VERIFIED,
    VIEW_CI,
    VIEW_RUN,
    MonitorRefused,
)

BLOCKED_LEDGER = "BLOCKED_LEDGER"


class NotifierUnavailable(MonitorRefused):
    def __init__(self, why: str) -> None:
        super().__init__(BLOCKED_LEDGER, why)


class Notifier(Protocol):  # pragma: no cover - structural typing
    def observe_payload(self, source: str, payload: dict) -> Any: ...


def _accepts_one_positional(factory: Callable[..., Any]) -> bool:
    """Does this factory want the config mapping, or nothing at all?

    Asked by signature rather than by catching `TypeError`, because a factory
    that raises `TypeError` *inside* itself must be reported as a refusal, not
    silently retried with different arguments.
    """
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return False
    positional = [p for p in signature.parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 1


def load_notifier(spec: Any, *, config: Mapping[str, Any] | None = None) -> Notifier:
    """Resolve `module:callable` into #423's notifier, or refuse by name.

    `callable` is a zero-argument factory (or a factory taking one mapping) that
    returns an object exposing `observe_payload`. Resolving happens before any
    read: a monitor with no ledger must fail before it spends a provider call.
    """
    if not isinstance(spec, str) or ":" not in spec:
        raise NotifierUnavailable(
            f"{spec!r} is not a `module:callable` notifier binding; there is no "
            f"default ledger, on purpose")
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise NotifierUnavailable(f"{spec!r} does not name both a module and an attribute")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - any failure is "no binding"
        raise NotifierUnavailable(
            f"{module_name} cannot be imported ({type(exc).__name__}: {exc}); #423's "
            f"notifier is not available on this tree, so there is nothing to deliver to"
        ) from None
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise NotifierUnavailable(f"{module_name}.{attribute} is not callable")
    try:
        takes_an_argument = _accepts_one_positional(factory)
        candidate = factory(config) if takes_an_argument else factory()
    except Exception as exc:  # noqa: BLE001
        raise NotifierUnavailable(
            f"{module_name}.{attribute} refused to build a notifier "
            f"({type(exc).__name__}: {exc})") from None
    if not callable(getattr(candidate, "observe_payload", None)):
        raise NotifierUnavailable(
            f"{module_name}.{attribute} returned an object without "
            f"`observe_payload`; that is not #423's notifier")
    return candidate


# -- #423's closed envelopes ------------------------------------------------

def cloudcli_payload(observation: Mapping[str, Any], *, watch: Mapping[str, Any],
                     session_ref: str, device_ref: str,
                     bound_boot_id: str | None, observed_at: str) -> dict:
    """One run fact, in the envelope #423's `normalize_cloudcli_run` accepts.

    Only two statuses reach here (`core` guards it): completed-with-native-
    activity and failed. The `lastAssistantMessage.content` is deliberately a
    fixed marker, not the transcript: #423 reads the message only for *whether*
    one exists, and carrying the text would move prompts across a boundary that
    has no business holding them.
    """
    status = observation["status"]
    if status == STATUS_TERMINAL_VERIFIED:
        wire = "completed"
        message = {"content": "[native activity present; text not forwarded]"}
        note = None
    elif status == STATUS_FAILED:
        wire = "failed"
        message = None
        note = None
    else:  # pragma: no cover - guarded by the caller and asserted by a test
        raise MonitorRefused(
            "NOT_DISPATCHABLE",
            f"status {status!r} is never handed to the notifier")

    identity = observation.get("identity") or {}
    run = {
        "runId": identity.get("runId") or watch.get("run_id"),
        "status": wire,
        "sessionId": session_ref,
        "bootId": bound_boot_id,
    }
    if message is not None:
        run["lastAssistantMessage"] = message
    if note is not None:
        run["note"] = note
    gates = ["native-output", "boot-identity"]
    passed = []
    if message is not None:
        passed.append("native-output")
    if bound_boot_id:
        passed.append("boot-identity")
    return {
        "device_ref": device_ref,
        "session_ref": session_ref,
        "observed_at": observed_at,
        "bound_boot_id": bound_boot_id or "",
        "source_trusted": True,
        "milestone": bool(watch.get("major", False)),
        "gates_required": gates,
        "gates_passed": passed,
        "summary": None,
        "refs": [f"run:{run['runId']}"],
        "run": run,
    }


def github_payload(observation: Mapping[str, Any], *,
                   expected_source_sha: str, project: str | None,
                   observed_at: str) -> dict:
    """One pipeline fact, in the envelope #423's `normalize_github_actions` accepts."""
    status = observation["status"]
    identity = observation.get("identity") or {}
    name = project or "ci"
    run_id = identity.get("runId") or expected_source_sha[:12]
    if status == STATUS_TERMINAL_VERIFIED:
        conclusion = "success"
        phase = "completed"
    elif status == STATUS_FAILED:
        conclusion = "failure"
        phase = "completed"
    else:  # pragma: no cover
        raise MonitorRefused(
            "NOT_DISPATCHABLE", f"status {status!r} is never handed to the notifier")
    return {
        "device_ref": "",
        "observed_at": observed_at,
        "source_trusted": True,
        "milestone": True,
        "gates_required": ["exact-sha"],
        "gates_passed": ["exact-sha"],
        "summary": None,
        "refs": [f"sha:{expected_source_sha}"],
        "workflow_run": {
            "id": str(run_id),
            "name": name,
            "status": phase,
            "conclusion": conclusion,
            "head_sha": expected_source_sha,
        },
    }


def payload_for(observation: Mapping[str, Any], watch: Mapping[str, Any],
                *, observed_at: str) -> tuple[str, dict]:
    """The (source, payload) pair #423's `observe_payload` takes."""
    if observation.get("view") == VIEW_CI:
        return "github_actions", github_payload(
            observation, expected_source_sha=watch["expected_source_sha"],
            project=watch.get("project"), observed_at=observed_at)
    if observation.get("view") == VIEW_RUN:
        identity = observation.get("identity") or {}
        return "cloudcli", cloudcli_payload(
            observation, watch=watch, session_ref=watch["session_ref"],
            device_ref=watch["device_ref"],
            bound_boot_id=watch.get("expected_boot_id") or identity.get("bootId"),
            observed_at=observed_at)
    raise MonitorRefused("NOT_DISPATCHABLE",
                         f"view {observation.get('view')!r} has no envelope")
