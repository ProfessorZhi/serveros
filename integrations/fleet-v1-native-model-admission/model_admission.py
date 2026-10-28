"""What may a create name, and which answers count as evidence?

This module is a decision surface, not a client. It never calls a provider, never
reads a credential and never starts a session; its whole job is to answer three
questions from things a caller already holds, and to answer them *conservatively*:

1.  given what this runtime was observed to accept, is a named model id
    evidence-backed, merely schema-shaped, only policy-permitted, expired, or
    wholly unknown?
2.  may this project's grant name that id at all — and if not, is the refusal
    made before anything is asked of a provider?
3.  after a refusal, is a retry a second paid session, and is swapping the model
    for another one ever acceptable?

The vocabulary is fixed at five classes so that a caller cannot collapse
"nobody knows" into "available". They are, in descending strength:

``PROVIDER_ADVERTISED``
    The release's own capture *enumerates* the id for this device and runtime.
    This is the only class that may become an explicit model argument.

``SCHEMA_ONLY``
    A model field exists and is declared to take a string. It accepts anything,
    which is the opposite of announcing what it accepts.

``POLICY_ALLOWED_ONLY``
    Somebody's grant names the id. A grant is a ceiling on what may be *asked*,
    not evidence that a backend exists; naming is permission, not catalogue.

``STALE``
    An observation exists but its release pin or its age no longer supports it.
    An expired enumeration is not evidence, however explicit it looks.

``UNKNOWN``
    Nothing at all.

Why this matters on the release pinned here
-------------------------------------------
CloudCLI 1.41.0 declares ``session_create.model`` as a plain ``string`` with no
enum and requires only ``project``; ``session_reconfigure`` declares ``model``
the same way; no verb enumerates models or agents; and no roundtrip in the
capture ever passed ``model``. On this release, therefore, a named id can be at
most ``SCHEMA_ONLY`` and an explicit model argument can never be justified by
provider evidence. `choose_model` still returns the provider-default decision in
that case — a session may be created — but it labels the model identity
``UNKNOWN`` and never copies the requested string into the model it chose. When
the caller cannot proceed without a specific model, ``require_explicit=True``
turns the same situation into an honest refusal instead of a silent substitution.

The corresponding capability report is *blocked*, not complete: what the device
publishes does not support a model catalogue, and this module refuses to
manufacture one. See ``README.md`` in this directory for the evidence table.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "DELIVERY_FIELDS",
    "DEFAULT_MAX_AGE",
    "EVIDENCE_CLASSES",
    "MODEL_SOURCES_1_41",
    "PREFLIGHT_ORDER",
    "RELEASE",
    "RUN_FIELDS",
    "SESSION_FIELDS",
    "TOOLS_LIST_SHA256",
    "Choice",
    "CreateRequest",
    "ModelEvidence",
    "Preflight",
    "ProjectGrant",
    "RetryDecision",
    "as_grant",
    "choose_model",
    "classify",
    "dispatch_sources",
    "known_models",
    "preflight_create",
    "retry_decision",
]

#: The release everything here reasons about, and the pin of the capture that
#: describes it. A different release is not "close enough": `classify` reports
#: `STALE` for it, because the shape may have moved.
RELEASE = "1.41.0"
TOOLS_LIST_SHA256 = (
    "24e0bef7ce297e0c8a65fa6b7dc22c54c6ec12eb975339d4d31728461da21bbc"
)

#: Where a model id could legitimately come from on this release. Empty is the
#: finding, not an oversight: there is no enumerating verb to read.
MODEL_SOURCES_1_41: tuple[str, ...] = ()

EVIDENCE_CLASSES = (
    "PROVIDER_ADVERTISED",
    "SCHEMA_ONLY",
    "POLICY_ALLOWED_ONLY",
    "STALE",
    "UNKNOWN",
)

#: How old an observation may be before it stops being evidence. Callers that
#: need a different bound pass `max_age`; the default is deliberately short, and
#: a capture that is only *probably* still right is not fresh.
DEFAULT_MAX_AGE = dt.timedelta(hours=24)

#: The order in which a create is checked. The first two gates answer questions
#: about the caller's authority, and they are answered before any model question
#: is asked, because authority is the reason a request is refused at all.
PREFLIGHT_ORDER = (
    "PROJECT_NOT_AUTHORIZED",
    "CREATE_NOT_ALLOWED_FOR_DEVICE",
    "PERMISSION_MODE_NOT_GRANTED",
    "MODEL_POLICY_DENIED",
    "MODEL_ADMISSION",
)

#: Three objects, three field sets, three sources. A delivery is not a run and a
#: session is not a delivery; a payload carrying one must not be read as carrying
#: another.
DELIVERY_FIELDS = ("deliveryId", "queuedMessageUuid")
RUN_FIELDS = ("runId", "bootId")
SESSION_FIELDS = ("sessionId",)

_IDENTITY_ORDER = ("run", "delivery", "session")

_IDENTIFIER_MAX = 512

#: The device's own `source` label, reduced to the words this module is willing to
#: interpret. Anything else is carried verbatim and trusted for nothing.
_IDENTITY_WORDS = {"run": "run", "delivery": "delivery", "session": "session"}

#: Codes that mean "the backend you named does not exist". A retry is another
#: paid create, so these are terminal whatever the provider's `retryable` says.
_MODEL_REJECTION_CODES = frozenset((
    "MODEL_NOT_FOUND",
    "UNKNOWN_MODEL",
    "INVALID_MODEL",
    "MODEL_UNAVAILABLE",
    "UNSUPPORTED_MODEL",
))

#: Verbs that mint a paid session. Their failures are never retried from here.
_PAID_VERBS = frozenset(("session_create",))


@dataclass(frozen=True)
class ModelEvidence:
    """One observation of one runtime on one device, at one release.

    `schema_types` is what a tool shape *declares* (a field name to a type name);
    `declared` is what a shape *enumerates* (a field name to the finite values it
    lists). Only `declared` can produce `PROVIDER_ADVERTISED`. Keeping both lets
    the two be reported apart instead of the weaker one being upgraded.
    """

    device_ref: str
    runtime: str
    release: str
    captured_at: dt.datetime | None
    schema_types: Mapping[str, str] = field(default_factory=dict)
    declared: Mapping[str, Sequence[str]] = field(default_factory=dict)
    capture_sha256: str = ""


@dataclass(frozen=True)
class ProjectGrant:
    """What may be named on one project, once creation is on for the device.

    Both ceilings are empty by default, and empty means "the caller may name
    neither" — never "anything". A model is here for the same reason a permission
    mode is: naming a backend selects one the operator may not have considered.
    """

    models: tuple[str, ...] = ()
    permission_modes: tuple[str, ...] = ()

    def names(self, model: str | None) -> bool:
        return bool(model) and model in self.models

    def permits_mode(self, mode: str | None) -> bool:
        return bool(mode) and mode in self.permission_modes


@dataclass(frozen=True)
class Choice:
    """What was decided about the model argument, and who decided it.

    Two classes are carried, because they answer different questions and
    collapsing them is how "permitted" becomes "available":

    *   `evidence_class` — what the *device* published about this id;
    *   `support` — the strongest single thing warranting it overall. It equals
        `evidence_class` when that is `PROVIDER_ADVERTISED`, becomes
        `POLICY_ALLOWED_ONLY` when the grant is what names the id, and stays
        `UNKNOWN` when nothing does.

    `source` is never blank: ``provider`` when an explicit id came from an
    enumeration, ``grant`` when a grant refused, ``none`` when the decision was
    to let the provider choose. `model_identity` is the separate question of
    whether the chosen model is known — it is ``UNKNOWN`` whenever `model` is
    ``None``, even if the request named something.
    """

    model: str | None
    decision: str
    evidence_class: str
    support: str
    code: str
    source: str
    model_identity: str


@dataclass(frozen=True)
class CreateRequest:
    device_ref: str
    runtime: str
    project_id: str
    model: str | None = None
    permission_mode: str | None = None


@dataclass(frozen=True)
class Preflight:
    """The verdict on a create, plus the trail that produced it.

    `provider_call_required` is false for every refusal: a refused create is
    decided from local authority and evidence, so nothing was asked of a device.
    """

    allowed: bool
    code: str
    order: tuple[str, ...]
    choice: Choice | None
    project_id: str
    model: str | None
    provider_call_required: bool


@dataclass(frozen=True)
class RetryDecision:
    retry: bool
    code: str
    reason: str


class _Clock:
    """Freshness arithmetic in one place, so no caller can forget the timezone."""

    __slots__ = ()

    @staticmethod
    def age(evidence: ModelEvidence | None, now: dt.datetime | None) -> dt.timedelta | None:
        """How long ago the observation was made, or ``None`` if unknowable.

        An observation whose timestamp is missing, timezone-naive, or in the
        future cannot be aged, and an age that cannot be computed cannot support
        a claim of freshness. ``None`` is therefore a *failed* check.
        """
        if evidence is None:
            return None
        captured = evidence.captured_at
        if not isinstance(captured, dt.datetime):
            return None
        if captured.tzinfo is None or captured.utcoffset() is None:
            return None
        moment = now if now is not None else dt.datetime.now(dt.timezone.utc)
        if moment.tzinfo is None or moment.utcoffset() is None:
            return None
        return moment - captured

    @classmethod
    def fresh(
        cls,
        evidence: ModelEvidence | None,
        now: dt.datetime | None,
        max_age: dt.timedelta,
    ) -> bool:
        if evidence is None or evidence.release != RELEASE:
            return False
        age = cls.age(evidence, now)
        if age is None:
            return False
        return dt.timedelta(0) <= age <= max_age


def _advertised(evidence: ModelEvidence | None) -> tuple[str, ...]:
    if evidence is None:
        return ()
    return tuple(evidence.declared.get("model") or ())


def _declares_a_model_field(evidence: ModelEvidence | None) -> bool:
    return bool(evidence is not None and evidence.schema_types.get("model"))


def classify(
    evidence: ModelEvidence | None,
    model_id: str | None,
    *,
    now: dt.datetime | None = None,
    max_age: dt.timedelta = DEFAULT_MAX_AGE,
) -> str:
    """Which of the five classes one id sits in for one device and runtime.

    ``model_id=None`` asks a different question — "does this target enumerate
    anything at all?" — and answers `PROVIDER_ADVERTISED` when it does, so a
    caller reading a default decision is not told the weaker thing.
    """
    if evidence is None:
        return "UNKNOWN"
    if not _Clock.fresh(evidence, now, max_age):
        return "STALE"
    if model_id is None:
        return "PROVIDER_ADVERTISED" if _advertised(evidence) else (
            "SCHEMA_ONLY" if _declares_a_model_field(evidence) else "UNKNOWN")
    if model_id in _advertised(evidence):
        return "PROVIDER_ADVERTISED"
    if _declares_a_model_field(evidence):
        return "SCHEMA_ONLY"
    return "UNKNOWN"


def known_models(
    evidence: ModelEvidence | None,
    *,
    now: dt.datetime | None = None,
    max_age: dt.timedelta = DEFAULT_MAX_AGE,
) -> tuple[str, ...]:
    """The ids this target was *observed* to enumerate, and nothing else.

    Note what is not here: no schema fallback, no grant, no built-in list. A
    caller that wants a catalogue has to name a source, and on this release there
    is no source to name — the function returns ``()``.
    """
    if not _Clock.fresh(evidence, now, max_age):
        return ()
    return _advertised(evidence)


def _lookup(
    evidence_by_target: Mapping[tuple[str, str], ModelEvidence],
    device_ref: str,
    runtime: str,
) -> ModelEvidence | None:
    return evidence_by_target.get((device_ref, runtime))


def _advertised_elsewhere(
    evidence_by_target: Mapping[tuple[str, str], ModelEvidence],
    device_ref: str,
    runtime: str,
    model_id: str,
    now: dt.datetime | None,
    max_age: dt.timedelta,
) -> str | None:
    """Was this id enumerated on another target? Names the reason if so.

    A miss is reported as a targeting error rather than as an unknown model: the
    id is real, this device or this runtime is not where it lives. Downgrading
    that to "use the default" would hide a caller that picked the wrong machine.
    """
    if model_id is None:
        return None
    for (other_device, other_runtime), other in evidence_by_target.items():
        if (other_device, other_runtime) == (device_ref, runtime):
            continue
        if not _Clock.fresh(other, now, max_age):
            continue
        if model_id not in _advertised(other):
            continue
        if other_runtime == runtime:
            return "MODEL_DEVICE_MISMATCH"
        return "MODEL_RUNTIME_MISMATCH"
    return None


#: Codes for the non-advertised families, strict (refuse) and relaxed (default).
_FALLBACK_CODES = {
    "SCHEMA_ONLY": ("MODEL_NOT_VERIFIED", "MODEL_NOT_VERIFIED_DEFAULT_USED"),
    "STALE": ("MODEL_EVIDENCE_STALE", "MODEL_EVIDENCE_STALE_DEFAULT_USED"),
    "UNKNOWN": ("MODEL_UNKNOWN", "MODEL_UNKNOWN_DEFAULT_USED"),
}


def as_grant(value: ProjectGrant | Mapping[str, object] | None) -> ProjectGrant:
    """Accept either this module's grant or the plain mapping a policy exports.

    ``CreateGrant.as_dict()`` in ``fleet_mcp.config`` already yields exactly
    ``{"permission_modes": [...], "models": [...]}``, so a caller does not have to
    re-wrap it — and, more importantly, cannot accidentally pass a mapping that
    carries a different key name and have it read as "no ceiling".
    """
    if isinstance(value, ProjectGrant):
        return value
    if isinstance(value, Mapping):
        return ProjectGrant(models=tuple(value.get("models") or ()),
                            permission_modes=tuple(value.get("permission_modes") or ()))
    raise TypeError("a grant is a ProjectGrant or a mapping with models/permission_modes")


def choose_model(
    *,
    evidence_by_target: Mapping[tuple[str, str], ModelEvidence],
    device_ref: str,
    runtime: str,
    requested: str | None,
    grant_models: Sequence[str] = (),
    require_explicit: bool = False,
    now: dt.datetime | None = None,
    max_age: dt.timedelta = DEFAULT_MAX_AGE,
) -> Choice:
    """Decide the model argument for one create, refusing to guess.

    The grant is consulted first and *only* the grant: when it denies the id,
    `evidence_by_target` is never looked at, which is what makes the refusal
    something that necessarily happens before a provider is called.
    """
    allowed = tuple(grant_models or ())

    if requested is not None:
        if requested not in allowed:
            # Naming a model the grant does not list — including the empty case,
            # where the grant authorises no model at all.
            return Choice(model=None, decision="REFUSED", evidence_class="UNKNOWN",
                          support="UNKNOWN", code="MODEL_POLICY_DENIED",
                          source="grant", model_identity="UNKNOWN")
    elif require_explicit:
        return Choice(model=None, decision="REFUSED", evidence_class="UNKNOWN",
                      support="UNKNOWN", code="MODEL_NOT_NAMED", source="none",
                      model_identity="UNKNOWN")

    evidence = _lookup(evidence_by_target, device_ref, runtime)

    if requested is None:
        return Choice(model=None, decision="PROVIDER_DEFAULT",
                      evidence_class=classify(evidence, None, now=now,
                                              max_age=max_age),
                      support="UNKNOWN", code="PROVIDER_DEFAULT", source="none",
                      model_identity="UNKNOWN")

    class_ = classify(evidence, requested, now=now, max_age=max_age)
    if class_ == "PROVIDER_ADVERTISED":
        return Choice(model=requested, decision="EXPLICIT",
                      evidence_class=class_, support="PROVIDER_ADVERTISED",
                      code="MODEL_ADVERTISED", source="provider",
                      model_identity="EXPLICIT")

    # Past this point the grant is the only thing naming this id: it is listed in
    # `allowed` by the check above, and the device has not enumerated it.
    mismatch = _advertised_elsewhere(evidence_by_target, device_ref, runtime,
                                     requested, now, max_age)
    if mismatch is not None:
        return Choice(model=None, decision="REFUSED", evidence_class=class_,
                      support="POLICY_ALLOWED_ONLY", code=mismatch,
                      source="provider", model_identity="UNKNOWN")

    strict_code, relaxed_code = _FALLBACK_CODES[class_]
    if require_explicit:
        return Choice(model=None, decision="REFUSED", evidence_class=class_,
                      support="POLICY_ALLOWED_ONLY", code=strict_code,
                      source="none", model_identity="UNKNOWN")

    # The session may still be created, but not with the model that was named:
    # the identity is unknown, and the code says why rather than implying the
    # default *is* the request.
    return Choice(model=None, decision="PROVIDER_DEFAULT", evidence_class=class_,
                  support="POLICY_ALLOWED_ONLY", code=relaxed_code,
                  source="none", model_identity="UNKNOWN")


def preflight_create(
    request: CreateRequest,
    *,
    create_allow: Mapping[str, ProjectGrant],
    create_allowed_for_device: bool,
    evidence_by_target: Mapping[tuple[str, str], ModelEvidence],
    require_explicit_model: bool = False,
    now: dt.datetime | None = None,
    max_age: dt.timedelta = DEFAULT_MAX_AGE,
) -> Preflight:
    """Check a create against local authority, in the pinned order.

    Nothing here can reach a device, and every refusal says so with
    `provider_call_required=False`. The project gate comes first because a
    project id decides which working tree and which credentials a minted session
    inherits — it is the most consequential of the checks, and an empty allowlist
    means "no project", not "any".
    """
    named = create_allow.get(request.project_id)
    grant = as_grant(named) if named is not None else None
    if grant is None:
        return Preflight(allowed=False, code="PROJECT_NOT_AUTHORIZED",
                         order=("PROJECT_NOT_AUTHORIZED",), choice=None,
                         project_id=request.project_id, model=None,
                         provider_call_required=False)

    order = ["PROJECT_NOT_AUTHORIZED"]

    if not create_allowed_for_device:
        order.append("CREATE_NOT_ALLOWED_FOR_DEVICE")
        return Preflight(allowed=False, code="CREATE_NOT_ALLOWED_FOR_DEVICE",
                         order=tuple(order), choice=None,
                         project_id=request.project_id, model=None,
                         provider_call_required=False)
    order.append("CREATE_NOT_ALLOWED_FOR_DEVICE")

    if request.permission_mode is not None and not grant.permits_mode(
            request.permission_mode):
        order.append("PERMISSION_MODE_NOT_GRANTED")
        return Preflight(allowed=False, code="PERMISSION_MODE_NOT_GRANTED",
                         order=tuple(order), choice=None,
                         project_id=request.project_id, model=None,
                         provider_call_required=False)
    order.append("PERMISSION_MODE_NOT_GRANTED")

    if request.model is not None and not grant.names(request.model):
        order.append("MODEL_POLICY_DENIED")
        choice = choose_model(
            evidence_by_target=evidence_by_target, device_ref=request.device_ref,
            runtime=request.runtime, requested=request.model,
            grant_models=grant.models, require_explicit=require_explicit_model,
            now=now, max_age=max_age,
        )
        return Preflight(allowed=False, code="MODEL_POLICY_DENIED",
                         order=tuple(order), choice=choice,
                         project_id=request.project_id, model=None,
                         provider_call_required=False)
    order.append("MODEL_POLICY_DENIED")

    choice = choose_model(
        evidence_by_target=evidence_by_target, device_ref=request.device_ref,
        runtime=request.runtime, requested=request.model,
        grant_models=grant.models, require_explicit=require_explicit_model,
        now=now, max_age=max_age,
    )
    order.append("MODEL_ADMISSION")

    if choice.decision == "REFUSED":
        return Preflight(allowed=False, code=choice.code, order=tuple(order),
                         choice=choice, project_id=request.project_id,
                         model=None, provider_call_required=False)

    return Preflight(allowed=True, code="CREATE_PREFLIGHT_OK",
                     order=tuple(order), choice=choice,
                     project_id=request.project_id, model=choice.model,
                     provider_call_required=True)


def retry_decision(
    *,
    verb: str,
    code: str | None,
    retryable: bool | None,
    attempt: int,
    paid: bool,
    max_attempts: int = 2,
    first_model: str | None = None,
    next_model: str | None = None,
) -> RetryDecision:
    """Whether a failed dispatch may be repeated.

    Three rules, in order, because each one answers a question the next cannot:

    *   a retry that changes the model is a substitution, not a retry — it would
        pick a backend the caller never chose, so it is refused before anything
        else is considered;
    *   a model rejection is terminal: repeating it creates a second paid
        session to learn what the first one already said;
    *   the provider's own ``retryable`` flag is obeyed when it says no. When it
        says yes, it can still only authorise a repeat of something that minted
        nothing — never a second session.
    """
    if next_model is not None and first_model is not None and next_model != first_model:
        return RetryDecision(False, "MODEL_SUBSTITUTION_FORBIDDEN",
                             "a retry may repeat a request, not change its model")

    if code in _MODEL_REJECTION_CODES:
        return RetryDecision(False, "MODEL_REJECTION_NOT_RETRYABLE",
                             "the named backend was rejected; a repeat is a second charge")

    if retryable is False:
        return RetryDecision(False, "PROVIDER_SAYS_NOT_RETRYABLE",
                             "the provider marked this failure terminal")

    if paid or verb in _PAID_VERBS:
        return RetryDecision(False, "PAID_SESSION_RETRY_FORBIDDEN",
                             "a create mints a paid session; only a human retries it")

    if retryable is True:
        if attempt >= max_attempts:
            return RetryDecision(False, "ATTEMPTS_EXHAUSTED",
                                 "the attempt ceiling was reached")
        return RetryDecision(True, "PROVIDER_SAYS_RETRYABLE",
                             "the provider marked this failure transient")

    return RetryDecision(False, "NO_RETRY_EVIDENCE",
                         "no retryable verdict to act on")


def _identity_state(value: object) -> str:
    if not isinstance(value, str):
        return "malformed"
    if not value or len(value) > _IDENTIFIER_MAX:
        return "malformed"
    if value.strip() != value or not value.strip():
        return "malformed"
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return "malformed"
    return "provider"


def _presence(result: Mapping[str, object], fields: Sequence[str]) -> str:
    states = [_identity_state(result[name]) for name in fields if name in result]
    if not states:
        return "absent"
    return "provider" if all(state == "provider" for state in states) else "malformed"


def dispatch_sources(result: Mapping[str, object]) -> dict[str, object]:
    """Read a dispatch result into three separate identity sources.

    The field sets come from the pinned capture: a create without a message
    answers with ``sessionId`` alone, a create with one answers with
    ``deliveryId``, ``runId`` and ``sessionId``, and a send answers with
    ``blockedBy``, ``deliveryId``, ``queued``, ``queuedMessageUuid``, ``runId``
    and ``source``. Present is reported as ``provider`` because the device put it
    there; absent stays ``absent`` and is never filled in from a sibling field.

    ``declared_source`` is the device's own label. It is carried verbatim and
    interpreted only for the three identity words; if it names a kind whose
    identity is missing, that is returned as a ``disagreement`` rather than
    believed.
    """
    delivery = _presence(result, DELIVERY_FIELDS)
    run = _presence(result, RUN_FIELDS)
    session = _presence(result, SESSION_FIELDS)
    queued = "provider" if isinstance(result.get("queued"), bool) else "absent"

    raw_source = result.get("source")
    declared_source = raw_source if isinstance(raw_source, str) else None

    states = {"run": run, "delivery": delivery, "session": session}
    disagreement = []
    if declared_source is not None:
        for word in _IDENTITY_ORDER:
            dimension = _IDENTITY_WORDS.get(declared_source)
            if dimension != word:
                continue
            if states[word] != "provider":
                disagreement.append(word)

    return {
        "delivery": delivery,
        "run": run,
        "session": session,
        "queued": queued,
        "declared_source": declared_source,
        "disagreement": tuple(disagreement),
    }
