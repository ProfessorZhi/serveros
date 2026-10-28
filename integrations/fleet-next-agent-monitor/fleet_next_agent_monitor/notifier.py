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

The second rule is about *who the message is about*: #423's `_device` accepts
only a typed `device:<id>` subject, so a payload with `device_ref: ""` is not a
payload without a subject — it is a delivery that cannot happen. `delivery_subject`
resolves the subject from an authorized input or refuses by name
(`BLOCKED_SUBJECT` / `BLOCKED_CI_SUBJECT`); nothing here ever prefixes, repairs
or invents one.
"""
from __future__ import annotations

import importlib
import inspect
import re
from typing import Any, Callable, Mapping, Protocol

from .contract import (
    BLOCKED_CI_SUBJECT,
    BLOCKED_LEDGER,
    BLOCKED_RUN_IDENTITY,
    BLOCKED_SUBJECT,
    STATUS_FAILED,
    STATUS_TERMINAL_VERIFIED,
    VIEW_CI,
    VIEW_RUN,
    MonitorRefused,
)

#: Re-exported so a caller that has always imported these from `notifier` keeps
#: working; the words themselves now live in the shared vocabulary, because the
#: source refuses with the same ones before it reads.
__all__ = ["BLOCKED_CI_SUBJECT", "BLOCKED_LEDGER", "BLOCKED_RUN_IDENTITY",
           "BLOCKED_SUBJECT", "DEVICE_PREFIX", "CI_CONTAINER_PREFIX",
           "NotifierUnavailable", "cloudcli_payload", "delivery_subject",
           "github_payload", "load_notifier", "payload_for", "receipt_identity"]

#: #423's `normalize_*` functions accept only a typed `device:<id>` subject
#: (`milestone_notify._device`); a bare id is refused there. A monitor that sent
#: one learned about the mismatch only from a `DELIVERY_UNKNOWN` receipt, which
#: is the empty-string defect #495 §4.2 recorded.
DEVICE_PREFIX = "device:"

#: The typed subject a *CI* fact carries. A pipeline run belongs to a project,
#: never to a device, so this is the **project container's** subject:
#: `device:ci:<project>`. The `device:` prefix is #423's envelope requirement,
#: not a claim that a device exists — the `ci:` namespace says what it is.
#:
#: It is derived from exactly one authorized input: the watch's own `project`,
#: which `sources.observe_ci` authorizes *before* the read (an ungranted project
#: comes back `DENIED/NOT_GRANTED` and is never dispatchable). It is never
#: copied from another watch's `device_ref`, never read from a run fact, and
#: never synthesised from an empty or missing value. When there is no authorized
#: project and no explicitly declared typed `device_ref`, the hand-over is
#: refused by name (`BLOCKED_CI_SUBJECT`) and nothing is sent.
CI_CONTAINER_PREFIX = "device:ci:"

#: What #423 will accept as a subject: `device:` then a printable identifier.
#: The character class excludes non-ASCII on purpose — every comparison below
#: is a byte comparison, and #423 NFC-normalises the identity it writes.
_DEVICE_SUBJECT = re.compile(
    rf"^{re.escape(DEVICE_PREFIX)}[A-Za-z0-9][A-Za-z0-9._:-]{{0,126}}$")#: A project id, as it may appear in a container subject. `:` and `|` are
#: excluded so a project cannot forge a second separator inside the identity.
_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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

def delivery_subject(observation: Mapping[str, Any],
                     watch: Mapping[str, Any]) -> str:
    """The typed subject this fact is delivered under, or a refusal by name.

    There are exactly two authorized sources for a subject, and neither of them
    is an empty string or an invention:

    * a **run** watch's own `device_ref`, which the grant already authorized
      (a device outside the grant never produced an observation to send), and
    * a **CI** watch's `project`, which `sources.observe_ci` authorized before
      reading, carried into the project-container subject `device:ci:<project>`.

    A run watch must already carry the `device:` type, because prefixing a bare
    id here would mint a device identifier nobody granted. A CI watch may also
    declare its own typed `device_ref`; if it declares an untyped one that is a
    refusal, and if it declares a typed one the source must have *authorized*
    that exact device — a typed reference is a claim, and this function only
    hands over claims the grant has answered.
    """
    view = observation.get("view") or watch.get("kind")
    declared = watch.get("device_ref")
    declared = declared.strip() if isinstance(declared, str) else ""

    if view == VIEW_RUN:
        if not declared:
            raise MonitorRefused(
                BLOCKED_SUBJECT,
                "a run fact has no device_ref to be delivered under; #423 refuses a "
                "message with no typed subject and this monitor will not invent one")
        if not _DEVICE_SUBJECT.match(declared):
            raise MonitorRefused(
                BLOCKED_SUBJECT,
                f"{declared!r} is not a typed `device:<id>` subject, which is the only "
                f"form #423's ledger accepts. The monitor does not prefix, repair or "
                f"normalise it: a synthesised device ref would name a device nobody "
                f"granted. Declare the `device:` form in the watch and the grant.")
        return declared

    if view != VIEW_CI:
        raise MonitorRefused(
            BLOCKED_SUBJECT, f"view {view!r} has no delivery subject")

    if declared:
        if not _DEVICE_SUBJECT.match(declared):
            raise MonitorRefused(
                BLOCKED_CI_SUBJECT,
                f"{declared!r} is declared as the CI subject but is not a typed "
                f"`device:<id>` reference. A CI watch either declares a typed subject "
                f"or names the `project` that owns it; it does not get one made up.")
        # Typed is not authorized. The declaration is an authorization *claim*;
        # only the grant can settle it, so `sources.observe_ci` checks the
        # declared device against `grant.devices` before it reads and carries the
        # device it authorized into the observation's identity. This layer then
        # requires that the claim and the authorization be the *same device* —
        # #495 Wave 16 §4.1 measured what happens without it: a granted project
        # plus `device_ref: "device:victim"` produced a `DELIVERED` message whose
        # text and delivery key both named the victim.
        identity = observation.get("identity") or {}
        authorized = identity.get("deviceRef")
        authorized = authorized.strip() if isinstance(authorized, str) else ""
        if authorized != declared:
            seen = authorized or "no authorized device"
            raise MonitorRefused(
                BLOCKED_CI_SUBJECT,
                f"{declared!r} is a typed reference, and typed is not authorized: "
                f"this observation was authorized for {seen!r}, not for it. A CI "
                f"watch names a device the grant covers or the `project` that owns "
                f"the pipeline; it never names somebody else's device.")
        return declared

    project = watch.get("project")
    project = project.strip() if isinstance(project, str) else ""
    if not project:
        raise MonitorRefused(
            BLOCKED_CI_SUBJECT,
            "a CI event has no device of its own, so it needs an authorized subject: "
            "either a typed `device_ref` on the watch, or the `project` the pipeline "
            "belongs to (which the grant must authorize). Neither was given, and an "
            "empty subject is not a subject.")
    if not _PROJECT_ID.match(project):
        raise MonitorRefused(
            BLOCKED_CI_SUBJECT,
            f"project {project!r} is not a plain identifier, so folding it into "
            f"{CI_CONTAINER_PREFIX}<project> would put a separator inside a delivery "
            f"identity")
    return CI_CONTAINER_PREFIX + project


def receipt_identity(source: str, subject: str) -> str:
    """The prefix #423's `delivery_key` must carry to be *this* fact's receipt.

    #423 writes `milestone:<source>:<device>:<kind>:<key>:<outcome>` and
    NFC-normalises it. Both `source` and `subject` here are ASCII by
    construction (the subject is regex-checked above), so a byte prefix
    comparison is exact and a receipt naming another subject or another source
    can never settle this fact.
    """
    return f"milestone:{source}:{subject}:"


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
    run_id = identity.get("runId") or watch.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        # A `delivery_id`-only watch is a legal watch (`contract.require_watch`),
        # and a far end that answers with the run's own `runId` makes it
        # deliverable — `sources` adopts that id, and this function reads it off
        # the observation. When neither the watch nor the answer names a run,
        # #423's envelope has nothing to key on. That is a refusal with a name,
        # not a payload with `runId: null` that the ledger rejects *after* the
        # claim has been written: a fact that can never be delivered must say so
        # before it is attempted.
        raise MonitorRefused(
            BLOCKED_RUN_IDENTITY,
            "this run has no `runId`: neither the watch nor the observation names "
            "one, and #423's cloudcli envelope requires it. The monitor does not "
            "derive a run id from the delivery id — a synthesised id would name a "
            "run nobody issued. Declare `run_id`, or let the far end answer with "
            "the run's own `runId`.")
    run = {
        "runId": run_id.strip(),
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
                   device_ref: str, observed_at: str) -> dict:
    """One pipeline fact, in the envelope #423's `normalize_github_actions` accepts.

    `device_ref` is the resolved typed subject (`delivery_subject`), never the
    empty string this function used to hard-code. #423's `_device` refuses a
    bare or empty reference, so an empty one did not mean "no device": it meant
    a delivery that could not happen and was not reported as such.
    """
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
        "device_ref": device_ref,
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
    """The (source, payload) pair #423's `observe_payload` takes.

    Raises `MonitorRefused` (`BLOCKED_SUBJECT` / `BLOCKED_CI_SUBJECT`) when the
    watch has no authorized typed subject. That is a *refusal*, not a payload
    with an empty field: nothing is handed to the ledger and the tick says so.
    """
    if observation.get("view") == VIEW_CI:
        return "github_actions", github_payload(
            observation, expected_source_sha=watch["expected_source_sha"],
            project=watch.get("project"),
            device_ref=delivery_subject(observation, watch),
            observed_at=observed_at)
    if observation.get("view") == VIEW_RUN:
        identity = observation.get("identity") or {}
        return "cloudcli", cloudcli_payload(
            observation, watch=watch, session_ref=watch["session_ref"],
            device_ref=delivery_subject(observation, watch),
            bound_boot_id=watch.get("expected_boot_id") or identity.get("bootId"),
            observed_at=observed_at)
    raise MonitorRefused("NOT_DISPATCHABLE",
                         f"view {observation.get('view')!r} has no envelope")
