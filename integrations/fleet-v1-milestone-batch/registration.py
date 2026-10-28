#!/usr/bin/env python3
"""#417 — the durable registration one ChatGPT dispatch leaves behind.

WHAT THIS IS
------------
`batch.py` correlates the refs of one dispatch *while a process is alive*. The
owner's requirement is one step earlier and harder:

    没有持久登记就不宣称 chatGPT 退出后可追踪
    (without a persistent registration, do not claim the dispatch is still
     traceable after ChatGPT exits)

A ChatGPT turn splits a goal into tasks, dispatches them across devices, gets
one ACK per task and returns. If the only record of "these eight refs are one
dispatch, and this is its Batch ID" lives in that conversation, then the Tokyo
loop has no name to correlate under and no receipt key to send under — the
dispatch becomes N unrelated milestones. So the commander files ONE document
before it returns, and this module is the shape of that document plus the
interface #456's commander writes it through.

WHAT IT IS NOT
--------------
* **not delivery truth.** A registration says what the dispatcher asserted at
  ACK time. Whether a message reached the owner is #423's ledger's answer, and
  `batch.py` never reads this file to decide what was sent. Deleting this file
  cannot cause a resend, because the delivery keys are derived from the Batch
  ID it carries, and the ledger already owns those keys.
* **not a second writer for the ACK.** The ACK is a message #423 delivers like
  any other; this file only records the ids it named.
* **not a validator of the plan.** #456's `validate_batch` is the authority on
  whether a plan may be dispatched. This module calls it and refuses to file a
  plan it did not PASS — it does not re-implement the judgement.

THE SIX COLUMNS THAT ARE NEVER MIXED
------------------------------------
`device_ref`, `provider`, `session_ref`, `run_id`, `delivery_id`, `boot_id`.
Each has its own column and its own meaning:

    run_id       the Run a source can read            -> BatchMember.ref
    delivery_id  the IM receipt that carried the ACK  -> never a watch ref
    boot_id      the boot the Run is scoped to        -> #428's Watch scope
    session_ref  the session the task was dispatched into
    provider     the agent runtime on that device (claude/codex)
    device_ref   the machine

#416 established why the first two may never share a value: a `deliveryId` can
answer RUN_NOT_FOUND while the `runId` still reads, so one value in both
columns would erase exactly the distinction the reader exists to make. This
module refuses that conflation by name.

AN ACK IS DELIVERY, NOT EXECUTION
---------------------------------
#456 says it for one task ("ACK-only / `completed`-without-evidence stays
UNKNOWN"); this module says it for the set. A task whose ACK carries no Run —
or whose ACK is `ack_only` — is registered as a member with **no watch**. It
stays part of the dispatch (so a batch of three can never report "2/2"), it
contributes no read, and if no Run is ever issued it settles `UNOBSERVED` at
the batch deadline with one actionable message. That is the whole answer to
"queued ACK 未开始" and to "false completed": there is no column in this
document a verdict could be written into.

AUTHORITY BOUNDARY
------------------
No network, no Fleet call, no credential, no service, no model. One file is
written per registration, through the atomic writer this tree already declares
(`runtime/ability_projection.py`, reused for the reason #428 reuses it).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "serveros/fleet-milestone-batch-registration.v1"

REGISTERED = "REGISTERED"      # the file was written (or an amendment was)
UNCHANGED = "UNCHANGED"        # the same document is already on disk
UNFILED = "UNFILED"            # built and validated, but no path was given

#: Every column of one registered member, in document order. A file that
#: carries any other set is refused rather than read permissively: a new
#: column is a contract change and must be reviewed as one.
MEMBER_FIELDS = (
    "task_id", "source", "provider", "device_ref", "session_ref", "run_id",
    "delivery_id", "boot_id", "source_sha", "role",
)

#: Columns whose values identify something. Two of them holding one value means
#: two different things were called the same name.
IDENTITY_FIELDS = ("run_id", "delivery_id", "session_ref")

#: The identity a CloudCLI Run needs before it can be read at all.
WATCH_SOURCE_REQUIREMENTS = {
    "cloudcli": ("boot_id",),
    "github_actions": ("source_sha",),
}


def _batch():
    """`batch.py` beside this file, under the name its tests already use."""
    name = "serveros_fleet_v1_milestone_batch"
    module = sys.modules.get(name)
    if module is not None:
        return module
    path = Path(__file__).resolve().with_name("batch.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class RegistrationRefused(_batch().BatchRefused):
    """A registration this contract will not file. A closed code."""


def _text(value: Any, name: str, maximum: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistrationRefused(code, f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise RegistrationRefused(code, f"{name} is longer than {maximum}")
    return value


def _atomic_writer():
    """The atomic JSON write this tree has ALREADY declared.

    `runtime/ability_projection.py` owns `write_atomic`; reusing it keeps this
    directory free of a second raw `os.replace`/`unlink` mutation, exactly as
    #428's observer does for its state file.
    """
    name = "serveros_runtime_ability_projection"
    module = sys.modules.get(name)
    if module is None:
        path = (Path(__file__).resolve().parents[2]
                / "runtime" / "ability_projection.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.write_atomic


def path_for(directory, batch_id: str) -> Path:
    """Where a Batch ID lives. One batch, one file, named by the id."""
    ident = _text(batch_id, "batch_id", 128, code="BAD_BATCH_ID")
    if (ident != ident.strip() or any(ch.isspace() for ch in ident)
            or ":" in ident or "/" in ident or "\\" in ident
            or ident in (".", "..")):
        raise RegistrationRefused(
            "BAD_BATCH_ID", "a batch_id is also a file name: no whitespace, "
                            "colon or path separator")
    return Path(directory) / f"{ident}.json"


def digest(plan: Mapping) -> str:
    """The identity of the plan a registration was written against."""
    canonical = json.dumps(plan, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# one registered member
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RegisteredMember:
    """One task as it was filed: its identity, and the batch member it implies.

    The Run id lives ONLY on `member.ref`. There is no second field that could
    hold it, so "the run id and the receipt id were swapped" is not a bug this
    shape can express.
    """

    member: Any              # batch.BatchMember
    provider: str
    session_ref: str
    delivery_id: str

    def __post_init__(self):
        _text(self.provider, "provider", 32, code="BAD_MEMBER")
        _text(self.session_ref, "session_ref", 256, code="BAD_MEMBER")
        _text(self.delivery_id, "delivery_id", 256, code="BAD_MEMBER")
        values = {"run_id": self.member.ref, "delivery_id": self.delivery_id,
                  "session_ref": self.session_ref}
        seen: dict[str, str] = {}
        for name in IDENTITY_FIELDS:
            value = values[name]
            if value is None:
                continue
            other = seen.get(value)
            if other is not None:
                raise RegistrationRefused(
                    "ID_CONFLATION",
                    f"task {self.member.task_id!r} uses {value!r} as both "
                    f"{other} and {name}; a Run id, a delivery receipt and a "
                    f"session are three different things (#416: a deliveryId "
                    f"can answer RUN_NOT_FOUND while the runId still reads)")
            seen[value] = name

    # -- the six columns, read straight off the member ----------------------

    @property
    def task_id(self) -> str:
        return self.member.task_id

    @property
    def source(self) -> str:
        return self.member.source

    @property
    def run_id(self):
        return self.member.ref

    @property
    def device_ref(self) -> str:
        return self.member.device_ref

    @property
    def boot_id(self):
        return self.member.boot_id

    def document(self) -> dict:
        return {"task_id": self.task_id, "source": self.source,
                "provider": self.provider,
                "device_ref": self.member.device_ref,
                "session_ref": self.session_ref,
                "run_id": self.member.ref,
                "delivery_id": self.delivery_id,
                "boot_id": self.member.boot_id,
                "source_sha": self.member.source_sha,
                "role": self.member.role}

    @classmethod
    def from_document(cls, value: Any) -> "RegisteredMember":
        if not isinstance(value, Mapping):
            raise RegistrationRefused("BAD_REGISTRATION", "a member must be an object")
        if set(value) != set(MEMBER_FIELDS):
            raise RegistrationRefused(
                "BAD_REGISTRATION",
                f"a member carries exactly {list(MEMBER_FIELDS)}; this one "
                f"carries {sorted(value)}")
        try:
            member = _batch().BatchMember(
                source=value["source"], ref=value["run_id"],
                device_ref=value["device_ref"], boot_id=value["boot_id"],
                source_sha=value["source_sha"], role=value["role"] or "",
                task_id=value["task_id"] or "")
        except _batch().BatchRefused as exc:
            raise RegistrationRefused(
                "BAD_REGISTRATION",
                f"member {value.get('task_id')!r}: {exc.why}") from None
        return cls(member=member, provider=value["provider"],
                   session_ref=value["session_ref"],
                   delivery_id=value["delivery_id"])

    def amended(self, *, run_id: str, boot_id, source_sha, delivery_id: str):
        """The same task, now with a Run. Only an unissued task may be amended."""
        member = _batch().BatchMember(
            source=self.member.source, ref=run_id,
            device_ref=self.member.device_ref, boot_id=boot_id,
            source_sha=source_sha, role=self.member.role,
            expires_at=self.member.expires_at, task_id=self.member.task_id)
        return RegisteredMember(member=member, provider=self.provider,
                                session_ref=self.session_ref,
                                delivery_id=delivery_id)


# --------------------------------------------------------------------------- #
# the registration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Registration:
    """One dispatch's durable declaration. The file is this, in JSON."""

    batch_id: str
    members: tuple
    registered_at: str
    registered_by: str
    conversation_ref: str
    expires_at: str | None = None
    dispatch_ref: str | None = None
    plan_digest: str | None = None
    amendments: tuple = ()

    def __post_init__(self):
        _text(self.registered_at, "registered_at", 64, code="BAD_REGISTRATION")
        _text(self.registered_by, "registered_by", 64, code="BAD_REGISTRATION")
        _text(self.conversation_ref, "conversation_ref", 256,
              code="BAD_REGISTRATION")
        members = tuple(self.members)
        if not members:
            raise RegistrationRefused("NO_MEMBERS",
                                      "a registration needs at least one member")
        if not all(isinstance(member, RegisteredMember) for member in members):
            raise RegistrationRefused("BAD_REGISTRATION",
                                      "members must be RegisteredMember values")
        object.__setattr__(self, "members", members)
        # Validate the declaration now, not when a tick is due: a file that
        # cannot produce a Batch is a file nobody can act on.
        try:
            self.batch
        except _batch().BatchRefused as exc:
            raise RegistrationRefused(exc.code, exc.why) from None

    @property
    def batch(self):
        """The correlation declaration this file implies. Never stored twice."""
        return _batch().Batch(
            batch_id=self.batch_id,
            members=tuple(member.member for member in self.members),
            expires_at=self.expires_at,
            origin=_batch().ORIGIN_REGISTRATION)

    def member(self, task_id: str) -> RegisteredMember:
        for member in self.members:
            if member.task_id == task_id:
                return member
        raise RegistrationRefused("UNKNOWN_TASK",
                                  f"no task {task_id!r} in batch {self.batch_id!r}")

    def document(self) -> dict:
        return {"schema": SCHEMA, "batch_id": self.batch_id,
                "registered_at": self.registered_at,
                "registered_by": self.registered_by,
                "conversation_ref": self.conversation_ref,
                "dispatch_ref": self.dispatch_ref,
                "plan_digest": self.plan_digest,
                "expires_at": self.expires_at,
                "amendments": list(self.amendments),
                "members": [member.document() for member in self.members]}

    def amended(self, task_id: str, *, run_id: str, boot_id, source_sha,
                delivery_id: str, at: str) -> "Registration":
        """One task learns its Run. Everything else in the file is untouched."""
        current = self.member(task_id)
        if run_id is None:
            # Nothing to adopt: an amendment exists to fill an empty Run.
            return self
        _text(at, "at", 64, code="BAD_REGISTRATION")
        _text(run_id, "run_id", 128, code="ACK_INCOMPLETE")
        new_delivery = delivery_id if delivery_id is not None \
            else current.delivery_id
        if current.run_id is not None:
            if run_id != current.run_id:
                raise RegistrationRefused(
                    "ALREADY_ISSUED",
                    f"task {task_id!r} already names Run {current.run_id!r}; a "
                    f"registration may fill an empty Run, never replace one")
            if new_delivery != current.delivery_id:
                raise RegistrationRefused(
                    "ALREADY_ISSUED",
                    f"task {task_id!r} is issued; the receipt that carried its "
                    f"Run is {current.delivery_id!r} and cannot be rewritten")
            return self
        replaced = current.amended(run_id=run_id, boot_id=boot_id,
                                   source_sha=source_sha,
                                   delivery_id=new_delivery)
        members = tuple(replaced if member.task_id == task_id else member
                        for member in self.members)
        return Registration(
            batch_id=self.batch_id, members=members,
            registered_at=self.registered_at, registered_by=self.registered_by,
            conversation_ref=self.conversation_ref, expires_at=self.expires_at,
            dispatch_ref=self.dispatch_ref, plan_digest=self.plan_digest,
            amendments=tuple(self.amendments) + (
                {"at": at, "task_id": task_id, "from": None, "to": run_id},))


# --------------------------------------------------------------------------- #
# one file: write it once, read it back, amend it forward
# --------------------------------------------------------------------------- #
def dumps(registration_value: Registration) -> str:
    return json.dumps(registration_value.document(), ensure_ascii=False,
                      indent=2, sort_keys=True) + "\n"


def loads(text: str) -> Registration:
    try:
        document = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise RegistrationRefused("BAD_REGISTRATION",
                                  f"the registration is not JSON: {exc}") from None
    if not isinstance(document, Mapping):
        raise RegistrationRefused("BAD_REGISTRATION",
                                  "a registration is one JSON object")
    expected = {"schema", "batch_id", "registered_at", "registered_by",
                "conversation_ref", "dispatch_ref", "plan_digest", "expires_at",
                "amendments", "members"}
    if set(document) != expected:
        raise RegistrationRefused(
            "BAD_REGISTRATION",
            f"a registration carries exactly {sorted(expected)}; this one "
            f"carries {sorted(document)}")
    if document["schema"] != SCHEMA:
        raise RegistrationRefused(
            "BAD_REGISTRATION",
            f"schema {document['schema']!r} is not {SCHEMA!r}; a batch filed "
            f"under another schema is not this contract's to read")
    amendments = document["amendments"] or []
    if not isinstance(amendments, list):
        raise RegistrationRefused("BAD_REGISTRATION", "amendments must be a list")
    return Registration(
        batch_id=document["batch_id"],
        members=tuple(RegisteredMember.from_document(value)
                      for value in document["members"] or ()),
        registered_at=document["registered_at"],
        registered_by=document["registered_by"],
        conversation_ref=document["conversation_ref"],
        expires_at=document["expires_at"],
        dispatch_ref=document["dispatch_ref"],
        plan_digest=document["plan_digest"],
        amendments=tuple(amendments))


def load(path) -> Registration:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistrationRefused(
            "NO_REGISTRATION",
            f"{path.name}: {type(exc).__name__}: {exc}") from None
    return loads(text)


def save(path, registration_value: Registration) -> str:
    """Write the declaration, once per batch.

    `REGISTERED` for a new file, `UNCHANGED` for the same document again, and a
    refusal for a *different* document under one Batch ID: a second writer may
    not silently re-scope a dispatch that is already being correlated (its
    delivery keys are derived from the id it carries).
    """
    path = Path(path)
    document = registration_value.document()
    if path.exists():
        current = load(path)
        if current.document() == document:
            return UNCHANGED
        raise RegistrationRefused(
            "CONFLICTING_REGISTRATION",
            f"{path.name} is already filed for batch "
            f"{current.batch_id!r} with different members; a batch id names one "
            f"dispatch, so this needs its own id")
    _atomic_writer()(path, document)
    return REGISTERED


def amend(path, *, task_id: str, run_id, boot_id=None, source_sha=None,
          delivery_id=None, at: str):
    """A queued task learns its Run. Returns (registration, REGISTERED|UNCHANGED)."""
    path = Path(path)
    current = load(path)
    amended = current.amended(task_id, run_id=run_id, boot_id=boot_id,
                              source_sha=source_sha, delivery_id=delivery_id,
                              at=at)
    if amended.document() == current.document():
        return current, UNCHANGED
    _atomic_writer()(path, amended.document())
    return amended, REGISTERED


# --------------------------------------------------------------------------- #
# the way in: #456's plan plus its ACKs
# --------------------------------------------------------------------------- #
def from_commander(plan: Mapping, acks, *, contract, snapshot, source: str,
                   registered_at: str, registered_by: str = "chatgpt",
                   conversation_ref: str, dispatch_ref: str | None = None,
                   expires_at: str | None = None, path=None):
    """Turn one #456 dispatch plan plus its ACK rows into a filing.

    `contract` is #456's `batch_contract` module (anything exposing
    `validate_batch(plan, snapshot)`). It is required, and it is called with
    the plan and the live snapshot: this module files what the commander
    validated, never what it merely proposed. `path` files the result in the
    same step, so "registered" and "on disk" are one act.
    """
    if contract is None or not callable(getattr(contract, "validate_batch", None)):
        raise RegistrationRefused(
            "COMMANDER_CONTRACT_MISSING",
            "the #456 commander contract (validate_batch) was not provided; "
            "filing a plan this module validated itself would be a second, "
            "divergent copy of the dispatch judgement")
    if not isinstance(plan, Mapping) or not isinstance(plan.get("tasks"), (list, tuple)):
        raise RegistrationRefused("BAD_PLAN", "a plan is {batch_id, tasks: [...]}")

    verdict = contract.validate_batch(plan, snapshot)
    if not isinstance(verdict, Mapping) or verdict.get("verdict") != "PASS":
        blocked = verdict.get("blocked") if isinstance(verdict, Mapping) else None
        first = ""
        if isinstance(blocked, (list, tuple)) and blocked:
            first = str((blocked[0] or {}).get("code") or "")
        raise RegistrationRefused(
            "COMMANDER_REFUSED",
            f"#456 refused this plan ({first or 'no code'}); a batch is filed "
            f"only for a dispatch that was allowed")

    _text(registered_at, "registered_at", 64, code="BAD_REGISTRATION")
    _text(registered_by, "registered_by", 64, code="BAD_REGISTRATION")
    _text(conversation_ref, "conversation_ref", 256, code="BAD_REGISTRATION")
    _text(source, "source", 32, code="BAD_PLAN")

    by_task: dict[str, Mapping] = {}
    for row in (acks or ()):
        if not isinstance(row, Mapping):
            raise RegistrationRefused("ACK_INCOMPLETE", "an ACK must be an object")
        task_id = str(row.get("task_id") or "")
        if not task_id:
            raise RegistrationRefused("ACK_INCOMPLETE",
                                      "an ACK must name the task it acknowledges")
        if task_id in by_task:
            raise RegistrationRefused("ACK_INCOMPLETE",
                                      f"two ACKs for task {task_id!r}")
        by_task[task_id] = row

    planned = []
    for task in plan["tasks"]:
        if not isinstance(task, Mapping):
            raise RegistrationRefused("BAD_PLAN", "a task must be an object")
        task_id = str(task.get("task_id") or "")
        for field in ("task_id", "device_ref", "runtime"):
            if not task.get(field):
                raise RegistrationRefused(
                    "BAD_PLAN", f"task {task_id or '<no id>'!r} has no {field}")
        planned.append(task_id)

    extra = sorted(set(by_task) - set(planned))
    if extra:
        raise RegistrationRefused(
            "ACK_UNKNOWN_TASK",
            f"ACK for {extra[0]!r}, which is not a task in this plan")

    members = []
    for task in plan["tasks"]:
        task_id = str(task["task_id"])
        ack = by_task.get(task_id)
        if ack is None:
            raise RegistrationRefused(
                "ACK_MISSING",
                f"task {task_id!r} has no ACK; a dispatch that cannot say what "
                f"it issued is not a batch this contract can correlate")
        if not ack.get("delivery_id"):
            raise RegistrationRefused(
                "ACK_INCOMPLETE",
                f"task {task_id!r} names no delivery_id; the receipt that "
                f"carried the ACK is what ties the task to the chat")
        planned_session = str(task.get("session_ref") or "")
        acked_session = str(ack.get("session_ref") or "")
        if planned_session and acked_session and planned_session != acked_session:
            # The plan says where it dispatched; the ACK says what it answered.
            # Two different sessions is not a detail — it means this ACK is
            # about another dispatch, and filing it would put one task's Run
            # under another task's identity.
            raise RegistrationRefused(
                "ACK_INCOMPLETE",
                f"task {task_id!r}: the ACK names session {acked_session!r} but "
                f"the plan dispatched it into {planned_session!r}")
        # ACK-only is delivery without execution (#456): the run id, if any, is
        # not trusted, so the task is filed as unissued.
        run_id = None if ack.get("ack_only") is True else ack.get("run_id")
        boot_id, source_sha = ack.get("boot_id"), ack.get("source_sha")
        if run_id is not None:
            needed = WATCH_SOURCE_REQUIREMENTS.get(source, ())
            for name in needed:
                if not ack.get(name):
                    raise RegistrationRefused(
                        "ACK_INCOMPLETE",
                        f"task {task_id!r} names Run {run_id!r} but no {name}; "
                        f"#428 would refuse the watch, and a Run read without "
                        f"that scope is a fact about somebody else's dispatch")
        try:
            member = _batch().BatchMember(
                source=source, ref=run_id, device_ref=str(task["device_ref"]),
                boot_id=boot_id if run_id is not None else None,
                source_sha=source_sha if run_id is not None else None,
                role=str(ack.get("role") or ""), task_id=task_id)
        except _batch().BatchRefused as exc:
            raise RegistrationRefused(
                "ACK_INCOMPLETE", f"task {task_id!r}: {exc.why}") from None
        # Outside the handler above on purpose: an id conflation is its own
        # refusal and may not be rewritten as an incomplete ACK.
        members.append(RegisteredMember(
            member=member, provider=str(task["runtime"]),
            session_ref=planned_session or acked_session,
            delivery_id=str(ack["delivery_id"])))

    if not members:
        raise RegistrationRefused("NO_MEMBERS", "the plan carries no tasks")

    registration_value = Registration(
        batch_id=str(plan.get("batch_id") or ""), members=tuple(members),
        registered_at=registered_at, registered_by=registered_by,
        conversation_ref=conversation_ref, expires_at=expires_at,
        dispatch_ref=dispatch_ref, plan_digest=digest(plan))
    if path is None:
        return registration_value, UNFILED
    return registration_value, save(path, registration_value)


__all__ = [
    "SCHEMA", "REGISTERED", "UNCHANGED", "UNFILED", "MEMBER_FIELDS",
    "IDENTITY_FIELDS",
    "Registration", "RegisteredMember", "RegistrationRefused",
    "from_commander", "digest", "path_for", "dumps", "loads", "load", "save",
    "amend",
]
