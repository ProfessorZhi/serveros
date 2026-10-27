"""#417 — the read-only SourceRead adapters: CloudCLI ``run_get`` and GitHub Actions.

WHAT THIS MODULE IS
-------------------
#423 delivered the delivery half of the milestone announcer and left exactly one
seam unwritten::

    class SourceRead(Protocol):
        def read(self, source: str, ref: str) -> Mapping[str, Any]: ...

This module is that seam's production half, and nothing else. It turns ONE
authorised read into ONE payload #423's ``normalize`` already accepts. It is not
a second event bus, not a CloudCLI client, and not a supervisor: there is no
thread, no timer, no queue and no subscriber. Whoever already owns an observer
tick — #407's ``RunWatcher.tick`` or ``runtime/daemon.py``'s tick — calls
``read`` and hands the result to ``MilestoneNotifier.observe_payload``.

WHAT IT REFUSES TO ASSUME
-------------------------
* A ``deliveryId`` is not re-readable. CloudCLI 1.41 answers
  ``RUN_NOT_FOUND``/never_issued for a queued send's deliveryId while the same
  send's ``runId`` reads fine (#416). So the binding here requires the ``runId``
  the owner's ACK committed; a delivery-only ACK is refused instead of guessed.
* ``completed`` is a process-lifecycle word, not a result. A run that reads
  ``completed`` with no readable assistant message, or one whose ``bootId`` is
  not the boot the dispatch was bound to, is passed through so #423's
  classifier settles it UNKNOWN. This module never upgrades either case.
* A gate is evidenced, not asserted. ``native-output`` and ``boot-identity``
  come from the run itself; a CI gate comes from a job whose own conclusion is
  ``success`` AND whose ``head_sha`` is the workflow's. A caller-claimed gate
  that conflicts with the derived fact is ignored.

WHAT IT NEVER TOUCHES
---------------------
No credential is read, no environment variable, no file, no socket. The
authorised transport is injected; it is the only thing that knows a token
exists, and it is called with a run id and a timeout — never with a secret.
Payloads are trimmed to the fields #423's normalizer reads, so a prompt, a
transcript or a token-shaped string cannot ride along. ``lastAssistantMessage``
survives only as the presence marker ``PRESENT``, because the normalizer needs
to know *whether* readable output exists, not what it said.

EVERY REFUSAL IS TYPED
----------------------
``SourceRefused`` carries a short code. A timeout, a permission denial, an
expired Run, a malformed sha, a session/run identity mismatch and a bad ref all
take that shape — so an observer settles them as UNKNOWN by construction, and no
refusal can ever look like a green. Provider messages are never copied into the
exception (they can contain credentials); only the exception's type name is.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

SCHEMA = "serveros/fleet-milestone-sources.v1"

#: The marker that stands in for assistant content. #423's normalizer only asks
#: whether ``lastAssistantMessage.content`` is a non-empty string.
PRESENT = "[present]"

# --------------------------------------------------------------------------- #
# refusal codes
# --------------------------------------------------------------------------- #
BAD_SOURCE = "BAD_SOURCE"
BAD_REF = "BAD_REF"
BAD_BINDING = "BAD_BINDING"
BAD_POLICY = "BAD_POLICY"
BAD_SHA = "BAD_SHA"
SHA_MISMATCH = "SHA_MISMATCH"
SESSION_MISMATCH = "SESSION_MISMATCH"
RUN_IDENTITY_MISMATCH = "RUN_IDENTITY_MISMATCH"
UNKNOWN_RUN = "UNKNOWN_RUN"
RUN_NOT_ACKNOWLEDGED = "RUN_NOT_ACKNOWLEDGED"
MALFORMED_PAYLOAD = "MALFORMED_SOURCE_PAYLOAD"
SOURCE_TIMEOUT = "SOURCE_TIMEOUT"
PERMISSION_REFUSED = "PERMISSION_REFUSED"
PROVIDER_REFUSED = "PROVIDER_REFUSED"

CLOUDCLI = "cloudcli"
GITHUB_ACTIONS = "github_actions"

#: Provider codes this boundary understands. Anything else collapses to
#: ``PROVIDER_REFUSED``: a code the boundary does not know must not be able to
#: arrive looking like an outcome word ("RUN_COMPLETED" is refused, not echoed).
_PROVIDER_CODES = {
    "RUN_NOT_FOUND": "RUN_NOT_FOUND",
    "PERMISSION_DENIED": PERMISSION_REFUSED,
    "PERMISSION_REFUSED": PERMISSION_REFUSED,
    "AUTH_REQUIRED": PERMISSION_REFUSED,
    "SCOPE_DENIED": PERMISSION_REFUSED,
    "CAPABILITY_UNSUPPORTED": "CAPABILITY_UNSUPPORTED",
    "BAD_SESSION_REF": SESSION_MISMATCH,
}

_SAFE_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NATIVE_ID = re.compile(r"^[^/\\]+$")
_REF = re.compile(r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/"
                  r"(?P<repo>[A-Za-z0-9._-]{1,100})#(?P<run_id>[1-9][0-9]{0,19})$")
_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_NO_READABLE = "no readable assistant message"

#: The two gates this module derives from CloudCLI facts rather than accepting
#: from a caller. #423's classifier independently re-derives both.
DERIVED_GATES = frozenset({"native-output", "boot-identity"})

_MAX_SIZE = 64 * 1024


class SourceRefused(ValueError):
    """The read did not establish a fact this boundary may report.

    A caller settles this as UNKNOWN. It never becomes a payload, and it never
    becomes a green.
    """

    def __init__(self, code: str, source: str, detail: str = ""):
        self.code = str(code)
        self.source = str(source)
        # Only a short type-name-shaped token is kept. Provider text is dropped
        # on purpose: refusal messages are a place credentials leak from.
        self.detail = str(detail) if _SAFE_TOKEN.match(str(detail or "")) else ""
        text = f"{self.source}: {self.code}"
        if self.detail:
            text = f"{text} ({self.detail})"
        super().__init__(text)

    def as_dict(self) -> dict:
        return {"schema": SCHEMA, "source": self.source, "code": self.code,
                "detail": self.detail or None,
                # The only outcome a refusal may be settled as.
                "settle_as": "UNKNOWN"}


# --------------------------------------------------------------------------- #
# injected read surfaces (the only things that hold authority)
# --------------------------------------------------------------------------- #
@runtime_checkable
class CloudCliRunRead(Protocol):
    """An authorised native ``run_get``. Read-only; never given a credential."""

    def __call__(self, *, run_id: str, timeout_seconds: float) -> Mapping[str, Any]: ...


@runtime_checkable
class GitHubActionsRead(Protocol):
    """An authorised ``GET /repos/{owner}/{repo}/actions/runs/{run_id}``."""

    def __call__(self, *, owner: str, repo: str, run_id: str,
                 timeout_seconds: float) -> Mapping[str, Any]: ...


@runtime_checkable
class GitHubJobsRead(Protocol):
    """An authorised ``GET .../runs/{run_id}/jobs``."""

    def __call__(self, *, owner: str, repo: str, run_id: str,
                 timeout_seconds: float) -> Any: ...


@runtime_checkable
class SourceRead(Protocol):
    """The seam #423 published. This module's adapters implement exactly it."""

    def read(self, source: str, ref: str) -> Mapping[str, Any]: ...


# --------------------------------------------------------------------------- #
# validated inputs
# --------------------------------------------------------------------------- #
def _text(value: Any, field: str, maximum: int, code: str) -> str:
    if not isinstance(value, str):
        raise SourceRefused(code, "-", f"bad_{field}")
    text = value.strip()
    if not text or len(text) > maximum or _CONTROL.search(text):
        raise SourceRefused(code, "-", f"bad_{field}")
    return text


def _optional_text(value: Any, field: str, maximum: int, code: str) -> str | None:
    if value in (None, ""):
        return None
    return _text(value, field, maximum, code)


@dataclass(frozen=True)
class RunBinding:
    """What the owner's ACK committed for one dispatch.

    ``run_id`` is mandatory and ``delivery_id`` is deliberately absent: a
    delivery id may answer RUN_NOT_FOUND after the fact while its run id still
    reads (#416), so this boundary reads the id the ACK itself supplied.
    """

    device_ref: str
    session_ref: str
    run_id: str | None = None
    bound_boot_id: str | None = None

    def __post_init__(self):
        device = _text(self.device_ref, "device_ref", 128, BAD_BINDING)
        if not device.startswith("device:"):
            raise SourceRefused(BAD_BINDING, "-", "untyped_device_ref")
        session = _text(self.session_ref, "session_ref", 256, BAD_BINDING)
        if not self.run_id:
            # An ACK that named only a deliveryId cannot be read back safely.
            raise SourceRefused(RUN_NOT_ACKNOWLEDGED, "-", "delivery_only_binding")
        run_id = _text(self.run_id, "run_id", 128, BAD_BINDING)
        if not _NATIVE_ID.match(run_id):
            raise SourceRefused(BAD_BINDING, "-", "path_shaped_run_id")
        object.__setattr__(self, "device_ref", device)
        object.__setattr__(self, "session_ref", session)
        object.__setattr__(self, "run_id", run_id)
        if self.bound_boot_id is not None:
            object.__setattr__(
                self, "bound_boot_id",
                _optional_text(self.bound_boot_id, "bound_boot_id", 128, BAD_BINDING))


def _gate_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise SourceRefused(BAD_POLICY, "-", f"bad_{field}")
    if len(value) > 50:
        raise SourceRefused(BAD_POLICY, "-", f"long_{field}")
    return tuple(_text(item, field, 128, BAD_POLICY) for item in value)


@dataclass(frozen=True)
class MilestonePolicy:
    """The caller's judgement, kept separate from the facts this module reads.

    ``source_trusted`` defaults to False: nothing is trusted unless the caller
    that owns the grant says so. ``supporting_gates`` are gates the caller
    evidenced elsewhere; they may never add a gate to ``gates_required`` and may
    never override one of :data:`DERIVED_GATES`.
    """

    source_trusted: bool = False
    milestone: bool = False
    gates_required: tuple[str, ...] = ()
    supporting_gates: tuple[str, ...] = ()
    summary: str | None = None
    refs: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.source_trusted, bool):
            raise SourceRefused(BAD_POLICY, "-", "bad_source_trusted")
        if not isinstance(self.milestone, bool):
            raise SourceRefused(BAD_POLICY, "-", "bad_milestone")
        required = _gate_tuple(self.gates_required, "gates_required")
        supporting = _gate_tuple(self.supporting_gates, "supporting_gates")
        if set(supporting) - set(required):
            raise SourceRefused(BAD_POLICY, "-", "supporting_gate_not_required")
        object.__setattr__(self, "gates_required", required)
        object.__setattr__(self, "supporting_gates", supporting)
        object.__setattr__(self, "summary",
                           _optional_text(self.summary, "summary", 280, BAD_POLICY))
        object.__setattr__(self, "refs", _gate_tuple(self.refs, "refs"))


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _iso(moment: Any) -> str:
    if isinstance(moment, bool):
        raise SourceRefused(BAD_REF, "-", "bad_clock")
    if isinstance(moment, datetime):
        stamp = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    elif isinstance(moment, (int, float)):
        stamp = datetime.fromtimestamp(moment, tz=timezone.utc)
    else:
        raise SourceRefused(BAD_REF, "-", "bad_clock")
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _refusal(source: str, exc: BaseException) -> SourceRefused:
    """Map an upstream failure to a typed refusal without copying its text."""
    if isinstance(exc, SourceRefused):
        return exc
    if isinstance(exc, TimeoutError):
        code = SOURCE_TIMEOUT
    elif isinstance(exc, PermissionError):
        code = PERMISSION_REFUSED
    else:
        provided = getattr(exc, "code", None)
        code = (_PROVIDER_CODES.get(provided, PROVIDER_REFUSED)
                if isinstance(provided, str) else PROVIDER_REFUSED)
    name = type(exc).__name__
    return SourceRefused(code, source, name if _SAFE_TOKEN.match(name) else "")


def _assistant_output_present(run: Mapping[str, Any]) -> bool:
    """Exactly #423's rule: a non-empty string, unless the device says otherwise."""
    message = run.get("lastAssistantMessage")
    content = message.get("content") if isinstance(message, Mapping) else None
    has_output = isinstance(content, str) and bool(content.strip())
    note = run.get("note")
    no_readable = isinstance(note, str) and _NO_READABLE in note.lower()
    return has_output and not no_readable


class _Adapter:
    """Timeout budget + refusal mapping, shared by both sources."""

    def __init__(self, *, clock: Callable[[], Any],
                 monotonic: Callable[[], float], timeout_seconds: float):
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise SourceRefused(BAD_POLICY, "-", "bad_timeout")
        if not 0 < float(timeout_seconds) <= 60:
            raise SourceRefused(BAD_POLICY, "-", "timeout_out_of_range")
        self._clock = clock
        self._monotonic = monotonic
        self._timeout = float(timeout_seconds)

    @staticmethod
    def _check_source(expected: str, source: Any) -> None:
        if source != expected:
            raise SourceRefused(BAD_SOURCE, expected, "wrong_source")

    @staticmethod
    def _ref(ref: Any, code: str = BAD_REF) -> str:
        text = _text(ref, "ref", 128, code)
        if any(part in text for part in ("/", "\\", "..")) or text.startswith("."):
            raise SourceRefused(code, "-", "path_shaped_ref")
        return text

    def _observed_at(self) -> str:
        return _iso(self._clock())

    def _deadline(self) -> float:
        return float(self._monotonic()) + self._timeout

    def _expired(self, deadline: float) -> bool:
        return float(self._monotonic()) > deadline

    def _paid(self, source: str, call: Callable[[], Any]) -> Any:
        try:
            answer = call()
        except BaseException as exc:  # noqa: BLE001 — mapped, never re-raised raw
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise _refusal(source, exc) from None
        if _size(answer) > _MAX_SIZE:
            raise SourceRefused(MALFORMED_PAYLOAD, source, "oversized_answer")
        return answer


def _size(value: Any) -> int:
    """A cheap size bound: counts string leaves and containers, never serialises."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(_size(key) + _size(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_size(item) for item in value)
    return 0


class CloudCliRunSource(_Adapter):
    """``read("cloudcli", runId)`` — one ACK-bound native ``run_get`` result."""

    source = CLOUDCLI

    def __init__(self, read_run: CloudCliRunRead, bindings: Mapping[str, RunBinding], *,
                 policy: MilestonePolicy | None = None,
                 clock: Callable[[], Any] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 timeout_seconds: float = 5.0):
        super().__init__(clock=clock or _wall, monotonic=monotonic or _mono_timer(),
                         timeout_seconds=timeout_seconds)
        if not callable(read_run):
            raise SourceRefused(BAD_BINDING, CLOUDCLI, "no_authorized_reader")
        if not isinstance(bindings, Mapping):
            raise SourceRefused(BAD_BINDING, CLOUDCLI, "bindings_must_be_a_mapping")
        for key, binding in bindings.items():
            if not isinstance(key, str) or not isinstance(binding, RunBinding):
                raise SourceRefused(BAD_BINDING, CLOUDCLI, "bad_binding_table")
            if key != binding.run_id:
                raise SourceRefused(BAD_BINDING, CLOUDCLI, "binding_key_mismatch")
        self._read_run = read_run
        self._bindings = dict(bindings)
        self._policy = policy if policy is not None else MilestonePolicy()
        if not isinstance(self._policy, MilestonePolicy):
            raise SourceRefused(BAD_POLICY, CLOUDCLI, "not_a_policy")

    def read(self, source: str, ref: str) -> dict[str, Any]:
        self._check_source(self.source, source)
        run_id = self._ref(ref)
        binding = self._bindings.get(run_id)
        if binding is None:
            # An unacknowledged id is not a milestone input. No read is made.
            raise SourceRefused(UNKNOWN_RUN, self.source, "not_acknowledged")
        deadline = self._deadline()
        raw = self._paid(self.source, lambda: self._read_run(
            run_id=run_id, timeout_seconds=self._timeout))
        if self._expired(deadline):
            raise SourceRefused(SOURCE_TIMEOUT, self.source, "deadline_exceeded")
        run = _trim_run(binding, run_id, raw, self.source)
        passed = self._gates(run, binding)
        return {
            "device_ref": binding.device_ref,
            "session_ref": binding.session_ref,
            "observed_at": self._observed_at(),
            "bound_boot_id": binding.bound_boot_id or "",
            "source_trusted": self._policy.source_trusted,
            "milestone": self._policy.milestone,
            "gates_required": list(self._policy.gates_required),
            "gates_passed": list(passed),
            "summary": self._policy.summary,
            "refs": list(self._policy.refs),
            "run": run,
        }

    def _gates(self, run: Mapping[str, Any], binding: RunBinding) -> tuple[str, ...]:
        derived = {
            "native-output": _assistant_output_present(run),
            "boot-identity": bool(binding.bound_boot_id)
                             and run.get("bootId") == binding.bound_boot_id,
        }
        # A caller-asserted gate never outranks a derived one, and a derived
        # gate that is False is never reported as passed.
        elsewhere = set(self._policy.supporting_gates) - DERIVED_GATES
        passed = []
        for gate in self._policy.gates_required:
            if gate in derived:
                if derived[gate]:
                    passed.append(gate)
            elif gate in elsewhere:
                passed.append(gate)
        return tuple(passed)


def _trim_run(binding: RunBinding, run_id: str, raw: Any,
              source: str) -> dict[str, Any]:
    """Keep only what #423's normalizer reads; drop prompt/transcript/ids."""
    if not isinstance(raw, Mapping):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "run_not_an_object")
    id_kind = raw.get("idKind")
    if id_kind is not None and id_kind != "run":
        raise SourceRefused(RUN_IDENTITY_MISMATCH, source, "wrong_namespace")
    if raw.get("runId") != run_id:
        raise SourceRefused(RUN_IDENTITY_MISMATCH, source, "run_id_mismatch")
    session = raw.get("sessionId")
    if isinstance(session, str) and session.strip() and session not in binding.session_ref:
        raise SourceRefused(SESSION_MISMATCH, source, "other_session")
    status = raw.get("status")
    if not isinstance(status, str) or not status.strip() or _CONTROL.search(status):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "missing_status")

    note = raw.get("note")
    trimmed_note: str | None = None
    if isinstance(note, str) and note.strip():
        if _NO_READABLE in note.lower():
            # Canonical, so the signal survives any length bound downstream.
            trimmed_note = _NO_READABLE
        else:
            trimmed_note = _CONTROL.sub(" ", note.strip())[:240]

    message = raw.get("lastAssistantMessage")
    content = message.get("content") if isinstance(message, Mapping) else None
    has_output = isinstance(content, str) and bool(content.strip())
    boot = raw.get("bootId")
    return {
        "idKind": "run",
        "runId": run_id,
        "sessionId": session.strip() if isinstance(session, str) else "",
        "status": status.strip()[:32],
        "bootId": boot.strip()[:128] if isinstance(boot, str) else "",
        "note": trimmed_note,
        # Presence only. The assistant's words stay on the device.
        "lastAssistantMessage": {"content": PRESENT} if has_output else None,
    }


class GitHubActionsSource(_Adapter):
    """``read("github_actions", "owner/repo#runId")`` — one finished workflow run.

    Gate evidence is derived from jobs whose ``head_sha`` is the workflow's own,
    so a green shard from another commit cannot be attributed to this run. With
    no jobs reader there is no gate evidence, and a milestone that requires a
    gate settles UNKNOWN rather than green.
    """

    source = GITHUB_ACTIONS

    def __init__(self, read_run: GitHubActionsRead, *, device_ref: str,
                 policy: MilestonePolicy | None = None,
                 jobs_reader: GitHubJobsRead | None = None,
                 clock: Callable[[], Any] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 timeout_seconds: float = 5.0):
        super().__init__(clock=clock or _wall, monotonic=monotonic or _mono_timer(),
                         timeout_seconds=timeout_seconds)
        if not callable(read_run):
            raise SourceRefused(BAD_BINDING, GITHUB_ACTIONS, "no_authorized_reader")
        device = _text(device_ref, "device_ref", 128, BAD_BINDING)
        if not device.startswith("device:"):
            raise SourceRefused(BAD_BINDING, GITHUB_ACTIONS, "untyped_device_ref")
        if jobs_reader is not None and not callable(jobs_reader):
            raise SourceRefused(BAD_BINDING, GITHUB_ACTIONS, "bad_jobs_reader")
        self._read_run = read_run
        self._read_jobs = jobs_reader
        self._device_ref = device
        self._policy = policy if policy is not None else MilestonePolicy()
        if not isinstance(self._policy, MilestonePolicy):
            raise SourceRefused(BAD_POLICY, GITHUB_ACTIONS, "not_a_policy")

    def read(self, source: str, ref: str) -> dict[str, Any]:
        self._check_source(self.source, source)
        match = _REF.match(_text(ref, "ref", 200, BAD_REF))
        if match is None:
            raise SourceRefused(BAD_REF, self.source, "ref_is_not_repo_hash_run")
        owner, repo, run_id = (match.group("owner"), match.group("repo"),
                               match.group("run_id"))
        if ".." in repo or repo.strip(".") == "":
            raise SourceRefused(BAD_REF, self.source, "path_shaped_repo")
        deadline = self._deadline()
        raw = self._paid(self.source, lambda: self._read_run(
            owner=owner, repo=repo, run_id=run_id, timeout_seconds=self._timeout))
        if self._expired(deadline):
            raise SourceRefused(SOURCE_TIMEOUT, self.source, "deadline_exceeded")
        run = _trim_workflow(raw, self.source)
        passed = self._gates(owner, repo, run_id, run, deadline)
        return {
            "device_ref": self._device_ref,
            "observed_at": self._observed_at(),
            "source_trusted": self._policy.source_trusted,
            "milestone": self._policy.milestone,
            "gates_required": list(self._policy.gates_required),
            "gates_passed": list(passed),
            "summary": self._policy.summary,
            "refs": list(self._policy.refs),
            "workflow_run": run,
        }

    def _gates(self, owner: str, repo: str, run_id: str,
               run: Mapping[str, Any], deadline: float) -> tuple[str, ...]:
        evidenced = set(self._policy.supporting_gates)
        if self._read_jobs is not None and self._policy.gates_required:
            answer = self._paid(self.source, lambda: self._read_jobs(
                owner=owner, repo=repo, run_id=run_id,
                timeout_seconds=self._timeout))
            if self._expired(deadline):
                raise SourceRefused(SOURCE_TIMEOUT, self.source, "deadline_exceeded")
            evidenced |= _green_jobs(answer, run["head_sha"], self.source)
        return tuple(gate for gate in self._policy.gates_required
                     if gate in evidenced)


def _trim_workflow(raw: Any, source: str) -> dict[str, Any]:
    """Keep conclusion, status and the sha; drop urls, actors and logs."""
    if not isinstance(raw, Mapping):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "run_not_an_object")
    run_id = raw.get("id")
    if isinstance(run_id, bool) or not isinstance(run_id, (int, str)):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "missing_run_id")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or _CONTROL.search(name):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "missing_name")
    status = raw.get("status")
    if not isinstance(status, str) or not status.strip() or _CONTROL.search(status):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "missing_status")
    conclusion = raw.get("conclusion")
    if conclusion is not None:
        if not isinstance(conclusion, str) or not conclusion.strip() \
                or _CONTROL.search(conclusion):
            raise SourceRefused(MALFORMED_PAYLOAD, source, "bad_conclusion")
        conclusion = conclusion.strip().lower()[:32]
    head_sha = raw.get("head_sha")
    if not isinstance(head_sha, str) or not _SHA.match(head_sha.strip().lower()):
        # A commit id is the identity of the evidence. A short or absent one is
        # not a weaker sha, it is not a sha.
        raise SourceRefused(BAD_SHA, source, "head_sha_not_a_commit")
    return {"id": run_id, "name": name.strip()[:128], "status": status.strip()[:32],
            "conclusion": conclusion, "head_sha": head_sha.strip().lower()}


def _green_jobs(answer: Any, head_sha: str, source: str) -> set[str]:
    items = answer.get("jobs") if isinstance(answer, Mapping) else answer
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise SourceRefused(MALFORMED_PAYLOAD, source, "jobs_not_a_list")
    if len(items) > 200:
        raise SourceRefused(MALFORMED_PAYLOAD, source, "too_many_jobs")
    passed = set()
    for job in items:
        if not isinstance(job, Mapping):
            raise SourceRefused(MALFORMED_PAYLOAD, source, "job_not_an_object")
        job_sha = job.get("head_sha")
        if isinstance(job_sha, str) and job_sha.strip():
            if job_sha.strip().lower() != head_sha:
                raise SourceRefused(SHA_MISMATCH, source, "job_from_another_commit")
        name = job.get("name")
        if not isinstance(name, str) or not name.strip() or _CONTROL.search(name):
            raise SourceRefused(MALFORMED_PAYLOAD, source, "job_without_name")
        status = job.get("status")
        conclusion = job.get("conclusion")
        if conclusion == "success" and (status in (None, "completed")):
            passed.add(name.strip()[:128])
    return passed


def _wall() -> float:
    return time.time()


def _mono_timer() -> Callable[[], float]:
    return time.monotonic


__all__ = [
    "SCHEMA", "PRESENT", "SourceRefused", "SourceRead", "RunBinding",
    "MilestonePolicy", "CloudCliRunSource", "GitHubActionsSource",
    "CloudCliRunRead", "GitHubActionsRead", "GitHubJobsRead",
    "BAD_SOURCE", "BAD_REF", "BAD_BINDING", "BAD_POLICY", "BAD_SHA",
    "SHA_MISMATCH", "SESSION_MISMATCH", "RUN_IDENTITY_MISMATCH", "UNKNOWN_RUN",
    "RUN_NOT_ACKNOWLEDGED", "MALFORMED_PAYLOAD", "SOURCE_TIMEOUT",
    "PERMISSION_REFUSED", "PROVIDER_REFUSED",
]
