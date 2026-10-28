#!/usr/bin/env python3
"""#417 — one dispatch, N devices, still exactly one event system.

WHAT THIS IS
------------
#417's owner scenario: a ChatGPT turn dispatches several Runs across devices
and returns without waiting. #423 built the delivery half (normalize -> decide
-> deterministic template -> one injected provider -> claim/receipt ledger),
#426 the source adapters, #428 the tick that keeps reading a ref after the chat
is gone, #446 the bounded runner. Nothing in that stack knows that several refs
were dispatched by ONE turn, and #417's owner asked for exactly that:

    "到全部 VERIFIED terminal 后去重通过现有 Product IM/Feishu 发一条批次摘要，
     遇 FAILED/UNKNOWN/离线出有限 actionable 告警。"

So this module owns the CORRELATION and nothing else:

  * a `Batch` declaration — the refs one dispatch created, each with the device
    and the boot (for a Run) or the commit (for CI) that may qualify it;
  * `verdict_for_row` — how one #428 row settles one member;
  * `BatchAggregator.absorb(report)` — the pure step from an observer tick to a
    batch tick, which sends the summary once and each member's alert once.

It is deliberately not:

  * a second bus — there is no queue, no subscriber, no thread and no event. The
    input is #428's `run_once()` return value; the output is a frozen value;
  * a second outbound provider — every message goes through #423's notifier
    with #423's claim/receipt ledger and #423's transport key. This module owns
    no credential, no provider, no capability probe and no transport;
  * a scheduler — it does not read a source, wake itself, or decide when to
    look. The bounded runner (#446) or any caller drives it after a tick;
  * a second DELIVERY state — a member's verdict is recomputed from the report,
    and what was already SENT is the ledger's answer, so this module persists
    nothing about delivery. Deleting the process must not resend, and a test
    proves it.

The one thing that IS durable is the DECLARATION, and it belongs to
`registration.py` beside this file: the Batch ID and its members are what the
dispatcher asserted at ACK time, and without that record a dispatch is only
knowable to the chat that issued it. That is an input, never a claim about
delivery, and the aggregator never reads it to decide what was sent.

WHY THE SETTLEMENT IS BY OUTCOME, NOT BY DELIVERY STATE
-------------------------------------------------------
#428's row separates two questions: what the evidence says (`outcome`), and
whether #423 got the message out (`state`). A Run that failed is a failed Run
whether or not the phone was offline. If this module waited for delivery to
settle a member, a provider outage would hold the whole batch open — the one
thing the batch exists to avoid. So `outcome` decides the verdict, and the
delivery state is carried beside it for the summary's accounting.

WHY IT READS #428's REPORT AND NOT ITS STATE FILE
--------------------------------------------------
The report is the tick's whole public answer: which refs it read, what each row
settled to. The observer's state file is scheduling facts ("when may I read this
again"), and #428 is explicit that it must not be consulted to decide what was
delivered. This module inherits that rule: the ledger decides, the report
informs.

THE ONE SEAM IT NEEDS FROM #423
-------------------------------
#423 has no public way to send a caller-authored sentence: `observe()` derives
the whole message from a payload, and `_send` is private. A batch summary is not
a milestone — it is a sentence about several of them — so this module needs one
public method and refuses to reach past it:

    def send_text(self, *, delivery_key, milestone_key, outcome, text, why)

The exact patch is in this directory's README. Until its owner lands it,
`BatchAggregator` raises `NOTIFIER_SEAM_MISSING` at construction rather than
writing a second path to the one provider. That absence is a BLOCKED state and
is recorded as one: this module never calls `_send`, `_now` or
`_prior_observation`, and a test pins that no private name appears here.

AUTHORITY BOUNDARY
------------------
No network, no lark-cli, no credential, no service, no model, no clock other
than the injected one. This module never claims REAL_FEISHU_DELIVERY; the
provider receipt is the only word for that, and it stays #423's.
"""
from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "serveros/fleet-milestone-batch.v1"

#: The two words a batch row's `outcome` column may carry. They are NOT
#: milestone outcomes: a reader filtering `milestone_deliveries` by #423's
#: vocabulary can never mistake a summary for a milestone, and a test pins that
#: neither word is in #423's outcome set.
BATCH_SUMMARY = "BATCH_SUMMARY"
BATCH_ALERT = "BATCH_ALERT"

# -- what one member settled to -------------------------------------------- #

PENDING = "PENDING"        # no fact yet: still running, refused-and-retryable
VERIFIED = "VERIFIED"      # #423's VERIFIED_GREEN
GREEN = "GREEN"            # ORDINARY_GREEN: green, but the gates were not earned
ATTENTION = "ATTENTION"    # YELLOW: decided, needs a person, not an alert
FAILED = "FAILED"          # RED
UNKNOWN = "UNKNOWN"        # decided, but the evidence does not establish an outcome
OFFLINE = "OFFLINE"        # the window or the batch deadline ended with no fact
UNOBSERVED = "UNOBSERVED"  # the deadline passed and no Run was ever issued for it
SKIPPED = "SKIPPED"        # the caller declared this ref non-qualifying

VERDICTS = (PENDING, VERIFIED, GREEN, ATTENTION, FAILED, UNKNOWN, OFFLINE,
            UNOBSERVED, SKIPPED)
SETTLED_VERDICTS = frozenset(VERDICTS) - {PENDING}
ACTIONABLE = frozenset({FAILED, UNKNOWN, OFFLINE, UNOBSERVED})

# -- #428's row words this module branches on ------------------------------- #
#
# Restated rather than imported because the classifier is a pure function of a
# row dict, and the observer module is loaded lazily. `test_the_words_this_
# module_branches_on_are_theirs` fails loudly if any of these drifts.
NO_EVENT = "NO_EVENT"
SKIPPED_BACKOFF = "SKIPPED_BACKOFF"
RATE_LIMITED = "RATE_LIMITED"
NOT_QUALIFYING = "NOT_QUALIFYING"
EXPIRED = "EXPIRED"
REFUSED = "REFUSED"
MILESTONE_REFUSED = "MILESTONE_REFUSED"
SCOPE_MISMATCH_CODES = frozenset({
    "REF_MISMATCH", "DEVICE_SCOPE_MISMATCH", "BOOT_SCOPE_MISMATCH",
    "SOURCE_SHA_MISMATCH",
})

#: Rows that say "this tick established nothing about the member". The observer
#: stamps two of them with `outcome=UNKNOWN` (backoff, rate limit), so they must
#: be tested BEFORE the outcome is read — otherwise a rate limit would settle a
#: member UNKNOWN and raise an alert for a Run that is still running.
NOT_A_FACT_STATES = frozenset({NO_EVENT, SKIPPED_BACKOFF, RATE_LIMITED})

# -- #423's outcome words this module branches on --------------------------- #
VERIFIED_GREEN = "VERIFIED_GREEN"
ORDINARY_GREEN = "ORDINARY_GREEN"
YELLOW = "YELLOW"
RED = "RED"
UNKNOWN_OUTCOME = "UNKNOWN"   # #423's UNKNOWN; the verdict of the same name is ours
SILENT = "SILENT"

# -- #423's delivery states this module reacts to --------------------------- #
#: Confirmed by the provider, or confirmed by the ledger's own row.
DELIVERED = "DELIVERED"
DUPLICATE = "DUPLICATE"
UNKNOWN_DELIVERY = "DELIVERY_UNKNOWN"

#: A refusal with one of these codes is a read that can clear (#428 retries it
#: with backoff). Every other refusal is a configuration mismatch and is
#: settled on the spot. Kept in step with `observer._TRANSIENT_CODES` by a test.
RETRYABLE_REFUSALS = frozenset({
    "RUN_NOT_FOUND", "UNKNOWN_RUN", "SOURCE_TIMEOUT", "SOURCE_UNAVAILABLE",
    "NETWORK", "DEADLINE", "TIMEOUT", "OSError", "ConnectionError",
    "TimeoutError", "socket.timeout",
})

# -- the tick's state ------------------------------------------------------ #
OPEN = "OPEN"                  # at least one member has no fact yet
SENT = "SENT"                  # this tick's summary reached the provider
REPLAY = "REPLAY"              # already delivered; the ledger said so
UNCONFIRMED = "UNCONFIRMED"    # claimed, not confirmed; reconcile, never resend

#: The most member lines a summary renders. A dispatch is a person's decision
#: and is small; the cap is here so a corrupted batch cannot become a wall of
#: text on someone's phone.
MAX_SUMMARY_LINES = 30

#: #417 asks for *finite* actionable alerts ("有限 actionable 告警"). One alert
#: per silent member is already bounded by the batch size, but a 40-device
#: dispatch that lost half its fleet would still be 20 notifications. Past this
#: cap the remainder is counted in the one summary instead, so the owner always
#: gets a bounded number of messages and never a stream.
MAX_ALERTS = 10

#: Where a declaration came from. `None` means the Batch was built by hand in a
#: process and never filed: usable for a live tick, never a basis for saying a
#: dispatch is still traceable after the chat that issued it exited.
ORIGIN_REGISTRATION = "registration"

_LABEL = {
    VERIFIED: "已验证", GREEN: "绿灯（门禁未取得）", ATTENTION: "需注意",
    FAILED: "失败", UNKNOWN: "未确立", OFFLINE: "失联", UNOBSERVED: "未观测",
    SKIPPED: "不适用", PENDING: "未结束",
}


class BatchRefused(ValueError):
    """A declaration or a step this contract will not perform. A closed code."""

    def __init__(self, code: str, why: str):
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


def _text(value: Any, name: str, maximum: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchRefused(code, f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise BatchRefused(code, f"{name} is longer than {maximum}")
    return value


def _observer():
    """#428's module, loaded once under the name the lane already uses.

    `Watch` is the authority on what a watchable ref is; this module asks it
    rather than restating its rules, so a member can never be something the
    tick would refuse to construct at start time.
    """
    name = "serveros_fleet_v1_milestone_observer"
    module = sys.modules.get(name)
    if module is not None:
        return module
    path = (Path(__file__).resolve().parents[2] / "integrations"
            / "fleet-v1-milestone-observer" / "observer.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BatchRefused("OBSERVER_UNAVAILABLE", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _as_timestamp(value: Any, name: str) -> float:
    """A clock may answer a datetime or epoch seconds; #423 accepts both."""
    if isinstance(value, bool):
        raise BatchRefused("BAD_CLOCK", f"{name} returned a bool")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, str) and value.strip():
        parsed = _parse_when(value)
        if parsed is not None:
            return parsed
    raise BatchRefused("BAD_CLOCK", f"{name} must return datetime or epoch seconds")


def _parse_when(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# the declaration: what one dispatch created
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BatchMember:
    """One task of one dispatch, and the scope that may qualify it.

    `ref` is the Run (or workflow-run) id, and it is `None` when the task was
    dispatched but no Run exists for it yet — an ACK, a queued job, a task
    whose dispatch the commander could not confirm. Such a member is still part
    of the dispatch: the alternative, dropping it, would let a batch of three
    report "2/2 verified" while the third was never issued, which is the
    false-completion shape #456 refuses for one task and this module refuses
    for a set of them.

    `role` is a person-facing label ("tokyo", "dell") and never reaches a
    payload: the summary names a member by role and ref, and carries no body.
    """

    source: str
    ref: str | None
    device_ref: str
    boot_id: str | None = None
    source_sha: str | None = None
    role: str = ""
    expires_at: str | None = None
    #: The dispatcher's own task identity (#456's `task_id`). It is the only
    #: stable name a member has before a Run exists, so it is what the key and
    #: the alert key are built from in that window.
    task_id: str = ""

    def __post_init__(self):
        if self.watchable:
            # Validated by #428's own `Watch`: a member is a watch plus a label.
            self.watch()
            return
        _text(self.task_id, "task_id", 128, code="BAD_MEMBER")
        if self.source not in getattr(_observer(), "WATCH_SOURCES", ()):
            raise BatchRefused("BAD_MEMBER", f"source {self.source!r} is not one "
                                             f"#428 watches")

    @property
    def watchable(self) -> bool:
        return self.ref is not None

    @property
    def key(self) -> str:
        """Stable for the member's whole life, and it changes only once.

        Before a Run exists the key is the dispatcher's task id; once one does
        it is `source:ref`, exactly the observer's key. `refresh` is the only
        thing that may cross that line, and it refuses to cross it backwards.
        """
        if self.watchable:
            return f"{self.source}:{self.ref}"
        return f"{self.source}:task:{self.task_id}"

    def watch(self, expires_at: str | None = None):
        """The one watch this member implies. There is no second declaration."""
        if not self.watchable:
            raise BatchRefused(
                "NO_RUN", f"task {self.task_id!r} has no Run; there is nothing "
                          f"to watch and inventing a ref would watch a stranger")
        deadline = expires_at if expires_at is not None else self.expires_at
        try:
            return _observer().Watch(
                source=self.source, ref=self.ref, device_ref=self.device_ref,
                boot_id=self.boot_id, source_sha=self.source_sha,
                expires_at=deadline)
        except ValueError as exc:
            raise BatchRefused("BAD_MEMBER", str(exc)) from None


@dataclass(frozen=True)
class Batch:
    """Everything one dispatch is waiting for, plus the deadline that bounds it.

    `batch_id` is the dispatch's own identifier, passed in. This module never
    derives it from a clock or a run id: a batch that renames itself on restart
    would send a second summary, which is the one thing the receipt exists to
    prevent.
    """

    batch_id: str
    members: tuple[BatchMember, ...]
    expires_at: str | None = None
    #: `ORIGIN_REGISTRATION` when this Batch was rebuilt from a filed
    #: registration, `None` when the caller built it in-process. It is the only
    #: word this contract has for "this dispatch outlives the chat".
    origin: str | None = None

    def __post_init__(self):
        ident = _text(self.batch_id, "batch_id", 128, code="BAD_BATCH_ID")
        if ident != ident.strip() or any(ch.isspace() for ch in ident):
            raise BatchRefused(
                "BAD_BATCH_ID", "batch_id may not contain whitespace; it is "
                "rendered into the message head and into every delivery key")
        if ":" in ident:
            raise BatchRefused(
                "BAD_BATCH_ID", "batch_id may not contain ':'; the delivery "
                "keys use it as the separator, so a colon could make two "
                "different batches share one receipt")
        if ident in (".", "..") or "/" in ident or "\\" in ident:
            raise BatchRefused(
                "BAD_BATCH_ID", "batch_id is also the registration's file name, "
                "so it may not be a path")
        members = tuple(self.members)
        if not members:
            raise BatchRefused("NO_MEMBERS", "a batch needs at least one member")
        if not all(isinstance(member, BatchMember) for member in members):
            raise BatchRefused("BAD_MEMBER", "members must be BatchMember values")
        keys = Counter(member.key for member in members)
        repeated = [key for key, count in keys.items() if count > 1]
        if repeated:
            raise BatchRefused(
                "DUPLICATE_REF",
                f"two members name the same source:ref ({repeated[0]!r}); one "
                f"tick cannot hold two facts about one ref, which is why #428 "
                f"refuses two watches for it as well")
        waiting = [member for member in members if not member.watchable]
        if waiting and self.expires_at is None:
            raise BatchRefused(
                "NO_DEADLINE",
                f"task {waiting[0].task_id!r} has no Run yet and the batch has "
                f"no deadline; an unissued task with no deadline is an "
                f"unbounded wait, which is the silence this contract exists to "
                f"remove")
        object.__setattr__(self, "members", members)

    # -- keys are the whole dedupe design ----------------------------------

    @property
    def milestone_key(self) -> str:
        """The one identity this batch is a delivery about."""
        return f"batch:{self.batch_id}"

    @property
    def summary_key(self) -> str:
        """ONE key for the whole batch: that is what makes the summary one-shot."""
        return f"{self.milestone_key}:summary"

    def alert_key(self, member_key: str, verdict: str) -> str:
        """ONE key per (member, verdict): an alert is never a stream."""
        if verdict not in ACTIONABLE:
            raise BatchRefused(
                "BAD_VERDICT",
                f"{verdict!r} is not actionable; an alert key exists for "
                f"{sorted(ACTIONABLE)} only")
        key = f"{self.milestone_key}:alert:{member_key}:{verdict}"
        if len(key) > 512:
            raise BatchRefused("KEY_TOO_LONG", f"{len(key)} characters, max 512")
        return key

    def watches(self, expires_at: str | None = None):
        """The observer's watches, derived — never a second list to keep in step.

        A member with no Run contributes no watch. That is the whole difference
        between "queued" and "running": the dispatch is the same size either
        way, but only the issued tasks are readable.
        """
        return tuple(member.watch(member.expires_at or expires_at or self.expires_at)
                     for member in self.members if member.watchable)

    @property
    def unissued(self) -> tuple[BatchMember, ...]:
        """Members the dispatcher declared but never issued."""
        return tuple(member for member in self.members if not member.watchable)


# --------------------------------------------------------------------------- #
# one row -> one verdict
# --------------------------------------------------------------------------- #
def verdict_for_row(row: Mapping) -> tuple[str, str | None]:
    """Settle one member from one #428 row.

    The order is the contract: "this tick established nothing" is tested before
    the outcome (two of those rows carry `outcome=UNKNOWN`), and a scope
    mismatch is tested before everything — a payload that answers for another
    ref, device, boot or commit never becomes this member's fact.
    """
    if not isinstance(row, Mapping):
        raise BatchRefused("BAD_ROW", "a row must be a mapping")
    state = str(row.get("state") or "")
    outcome = str(row.get("outcome") or "")
    raw_code = row.get("code")
    code = str(raw_code) if raw_code else None

    if state in NOT_A_FACT_STATES:
        return PENDING, code
    if state == NOT_QUALIFYING:
        return SKIPPED, code
    if state == EXPIRED:
        return OFFLINE, code
    if state in SCOPE_MISMATCH_CODES:
        return UNKNOWN, code
    if state == REFUSED:
        if code in RETRYABLE_REFUSALS:
            return PENDING, code
        return UNKNOWN, code
    if state == MILESTONE_REFUSED:
        return UNKNOWN, code

    if outcome == VERIFIED_GREEN:
        return VERIFIED, code
    if outcome == ORDINARY_GREEN:
        return GREEN, code
    if outcome == YELLOW:
        return ATTENTION, code
    if outcome == RED:
        return FAILED, code
    if outcome == UNKNOWN_OUTCOME:
        return UNKNOWN, code
    # SILENT, or a row shape a later observer added: nothing is established.
    # The batch deadline, not a guess, is what eventually bounds this.
    return PENDING, code


# --------------------------------------------------------------------------- #
# the step
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MemberState:
    member: BatchMember
    verdict: str
    code: str | None = None
    #: #423's outcome word for this row, when the row reached #423 at all. It is
    #: a closed vocabulary and carries no payload text, which is why it — and
    #: never the row's `why` — is what a summary line may repeat.
    outcome: str | None = None
    delivery_state: str | None = None
    #: True when #423 carried a message about THIS member (the row names a
    #: delivery key, or says it was delivered). It is what stops the batch from
    #: sending a second message about a fact the owner already received.
    covered: bool = False
    why: str = ""

    @property
    def key(self) -> str:
        return self.member.key

    @property
    def settled(self) -> bool:
        return self.verdict in SETTLED_VERDICTS

    @property
    def token(self) -> str:
        """The one machine word a line may show: the refusal, else the outcome."""
        return self.code or self.outcome or self.verdict


@dataclass(frozen=True)
class Receipt:
    """What the provider said about one batch message. The only delivery claim.

    It is a projection of #423's `Observation`, not a second record: the ledger
    row is the fact, this is the shape the batch layer may state. `remote_id`
    is the provider's message id and the only thing that may be called
    delivered — a batch never reports a send as done on its own authority.
    """

    delivery_key: str | None
    outcome: str | None
    state: str | None            # #423's DELIVERED / DELIVERY_UNKNOWN / DUPLICATE
    remote_id: str | None
    transport_key: str | None
    delivered: bool

    @property
    def confirmed(self) -> bool:
        return self.state in (DELIVERED, DUPLICATE)


@dataclass(frozen=True)
class BatchTick:
    """What one correlation step established. A value, not an event."""

    batch_id: str
    state: str
    settled: bool
    members: tuple[MemberState, ...]
    counts: Mapping[str, int]
    summary: Any = None            # #423's Observation, when the batch settled
    alerts: tuple = ()
    observed_at: str = ""
    #: Because the batch was rebuilt from a filed registration, or `None`.
    origin: str | None = None
    #: Silent members past `MAX_ALERTS`: counted in the summary, not sent.
    suppressed: tuple = ()

    @property
    def verdicts(self) -> dict:
        return {state.member.key: state.verdict for state in self.members}

    @property
    def verified(self) -> int:
        return self.counts.get(VERIFIED, 0)

    @property
    def receipts(self) -> tuple:
        """Every message THIS TICK caused, with the provider's own answer."""
        answers = (() if self.summary is None else (self.summary,)) + tuple(self.alerts)
        return tuple(Receipt(
            delivery_key=getattr(answer, "delivery_key", None),
            outcome=getattr(answer, "outcome", None),
            state=getattr(answer, "state", None),
            remote_id=getattr(answer, "remote_id", None),
            transport_key=getattr(answer, "transport_key", None),
            delivered=bool(getattr(answer, "delivered", False)))
            for answer in answers)


def _label(verdict: str) -> str:
    return _LABEL.get(verdict, verdict)


def _member_line(state: MemberState) -> str:
    member = state.member
    who = member.role or member.device_ref
    return f"- {who}:{member.ref} {_label(state.verdict)} ({state.token})"


def summary_text(batch: Batch, members, counts, total: int,
                 suppressed: int = 0) -> str:
    """The one sentence about the whole dispatch. Deterministic, no model.

    It carries verdicts, refs, roles and codes — never a payload body, never a
    `why` string (a refusal's detail can quote the payload it refused). The
    absence of payload text is structural, not a scrub that could be forgotten.

    `suppressed` is the number of silent members past `MAX_ALERTS`: the summary
    is where the owner learns they exist, so the budget never becomes silence.
    """
    head = (f"批次 {batch.batch_id} 结束：{counts.get(VERIFIED, 0)}/{total} 已验证")
    rest = [f"{_label(verdict)} {counts[verdict]}" for verdict in VERDICTS
            if verdict != VERIFIED and counts.get(verdict)]
    lines = [head]
    if rest:
        lines.append("其余：" + "、".join(rest))
    for state in tuple(members)[:MAX_SUMMARY_LINES]:
        lines.append(_member_line(state))
    if len(members) > MAX_SUMMARY_LINES:
        lines.append(f"…等 {total} 项")
    if suppressed:
        lines.append(f"另有 {suppressed} 项未单独告警，见上行明细")
    return "\n".join(lines)


def alert_text(batch: Batch, state: MemberState) -> str:
    """One member, one reason. An alert is not a smaller batch summary."""
    member = state.member
    who = member.role or member.device_ref
    return (f"[批次 {batch.batch_id}] {who}:{member.ref} "
            f"{_label(state.verdict)}（{state.verdict}, {state.token}）")


class BatchAggregator:
    """Correlate one dispatch's refs from #428's report; send each message once.

    Construction refuses a notifier that has no public `send_text`: reaching
    into #423's `_send` would be a second write path to the one provider, and
    the point of this contract is that there is exactly one.
    """

    def __init__(self, batch: Batch, *, notifier, clock: Callable[[], Any] | None = None):
        if not isinstance(batch, Batch):
            raise BatchRefused("BAD_BATCH", "the aggregator needs a Batch declaration")
        if not callable(getattr(notifier, "send_text", None)):
            raise BatchRefused(
                "NOTIFIER_SEAM_MISSING",
                "#423's notifier has no public send_text seam; a batch summary "
                "is not a milestone, so this contract needs one public method "
                "rather than a second write path to the one provider. See the "
                "patch proposal in integrations/fleet-v1-milestone-batch/README.md")
        self.batch = batch
        self.notifier = notifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        #: First settlement wins for the life of the process. A member that has
        #: already answered must not be un-answered by a later read that
        #: refuses — that would turn a known fact into an alert.
        self._frozen: dict[str, MemberState] = {}
        #: Keys the ledger confirmed. A restart forgets this set, re-asks the
        #: ledger, and gets DUPLICATE without touching the provider.
        self._confirmed: set[str] = set()
        #: Keys claimed but not confirmed. The next tick reconciles them under
        #: the SAME transport key; nothing is ever resent under a new one.
        self._unconfirmed: set[str] = set()
        #: Alert keys this process has issued, and the members past the budget.
        #: Both are recomputed from the same states after a restart, so the
        #: budget cuts the same members in the same order every time.
        self._alerted: set[str] = set()
        self._suppressed: set[str] = set()

    # -- a registration that learned a Run --------------------------------

    def refresh(self, batch: "Batch") -> None:
        """Adopt a declaration in which a queued task has learned its Run.

        This is the ONLY change a live batch may undergo: a member that had no
        Run may get one, and a member that had one may not become something
        else. Everything else — the batch id, the member set, the order, the
        deadline — is fixed, because the delivery keys and the summary's
        identity are derived from them.

        A member that already settled can never be refreshed: the owner has
        been told what happened to it, and a later registration may not
        retroactively make that untrue.
        """
        if not isinstance(batch, Batch):
            raise BatchRefused("BAD_BATCH", "refresh needs a Batch declaration")
        if batch.batch_id != self.batch.batch_id:
            raise BatchRefused(
                "BATCH_CHANGED",
                f"this aggregator is correlating batch {self.batch.batch_id!r}; "
                f"{batch.batch_id!r} is a different dispatch")
        if batch.expires_at != self.batch.expires_at:
            raise BatchRefused(
                "BATCH_CHANGED", "the batch deadline is part of its identity")
        if [member.task_id for member in batch.members] != \
                [member.task_id for member in self.batch.members]:
            raise BatchRefused(
                "BATCH_CHANGED", "a refresh may not add, drop or reorder tasks")
        for old, new in zip(self.batch.members, batch.members):
            if (old.source, old.device_ref, old.role) != \
                    (new.source, new.device_ref, new.role):
                raise BatchRefused(
                    "BATCH_CHANGED",
                    f"task {old.task_id!r} changed its device, source or role")
            if old.ref is not None and new.ref != old.ref:
                raise BatchRefused(
                    "BATCH_CHANGED",
                    f"task {old.task_id!r} already names Run {old.ref!r}; a "
                    f"refresh may only fill an empty ref")
            if old.ref is None and new.ref is not None and old.key in self._frozen:
                raise BatchRefused(
                    "TOO_LATE",
                    f"task {old.task_id!r} already settled {self._frozen[old.key].verdict}; "
                    f"its Run cannot be adopted after the owner was told")
        self.batch = batch

    # -- the step ----------------------------------------------------------

    def absorb(self, report: Mapping) -> BatchTick:
        """One observer report in, one batch tick out. Never raises for a row.

        A malformed report is a caller bug and is refused; a row this contract
        cannot interpret settles nothing and keeps the batch open.
        """
        if not isinstance(report, Mapping):
            raise BatchRefused("BAD_REPORT", "absorb needs #428's run_once() report")
        rows = report.get("rows")
        if not isinstance(rows, (list, tuple)):
            raise BatchRefused("BAD_REPORT", "the report carries no rows list")
        return self.absorb_rows(rows)

    def absorb_rows(self, rows) -> BatchTick:
        now = _as_timestamp(self.clock(), "clock")
        by_ref: dict[tuple[str, str], Mapping] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            # Last row for a ref wins. #428 cannot emit two (its watches are
            # keyed by source:ref), so this only decides what a hand-fed report
            # means, deterministically.
            by_ref[(str(row.get("source") or ""), str(row.get("ref") or ""))] = row

        states = tuple(
            self._state_for(member,
                            by_ref.get((member.source, member.ref))
                            if member.watchable else None,
                            now)
            for member in self.batch.members)
        counts = Counter(state.verdict for state in states)
        settled = all(state.settled for state in states)

        # Alerts are per member and as soon as the member is known: a device
        # that failed at 10:05 must not wait for the last device at 17:00.
        alerts = self._alerts(states)

        summary = None
        tick_state = OPEN
        if settled:
            summary, tick_state = self._summarise(states, counts)

        return BatchTick(batch_id=self.batch.batch_id, state=tick_state,
                         settled=settled, members=states, counts=dict(counts),
                         summary=summary, alerts=alerts, observed_at=_iso(now),
                         origin=self.batch.origin,
                         suppressed=tuple(sorted(self._suppressed)))

    def _state_for(self, member: BatchMember, row: Mapping | None,
                   now: float) -> MemberState:
        frozen = self._frozen.get(member.key)
        if frozen is not None:
            return frozen
        if not member.watchable:
            # Nothing was ever issued for this task. That is not a failure and
            # it is not a success: it is a hole the deadline has to close.
            state = MemberState(
                member, PENDING, code="NEVER_ISSUED",
                why=f"task {member.task_id!r} was dispatched but no Run was "
                    f"ever issued, so no source can speak about it")
        elif row is None:
            state = MemberState(member, PENDING,
                                why="no row for this ref in this tick")
        else:
            verdict, code = verdict_for_row(row)
            state = MemberState(
                member, verdict, code=code,
                outcome=str(row.get("outcome") or "") or None,
                delivery_state=row.get("state"),
                covered=bool(row.get("delivery_key") or row.get("delivered") is True),
                why=str(row.get("why") or "")[:200])
        if state.verdict == PENDING and self.batch.expires_at is not None:
            deadline = _parse_when(self.batch.expires_at)
            if deadline is not None and now >= deadline:
                if member.watchable:
                    state = MemberState(
                        member, OFFLINE, code="BATCH_DEADLINE",
                        why=f"the batch deadline {self.batch.expires_at} passed "
                            f"with no terminal fact for this ref")
                else:
                    state = MemberState(
                        member, UNOBSERVED, code="NEVER_ISSUED",
                        why=f"the batch deadline {self.batch.expires_at} passed "
                            f"and task {member.task_id!r} never got a Run")
        if state.settled:
            self._frozen[member.key] = state
        return state

    # -- sending: one path, one key per message ----------------------------

    def _alerts(self, states) -> tuple:
        """One message per member nothing else has spoken about, up to a budget.

        #423 already delivers a RED and an UNKNOWN as their own milestone
        messages, as they happen. Raising a batch alert for those would be the
        same fact twice, which is the noise this contract exists to remove. The
        members that would otherwise stay silent for ever are the ones the tick
        could not observe at all — a scope mismatch, a permanent refusal, an
        expired window, a batch deadline.

        Past `MAX_ALERTS` the remaining members are NOT dropped: they are named
        in the one summary, which is why the cap can never become silence.
        """
        out = []
        for state in states:
            if state.verdict not in ACTIONABLE or state.covered:
                continue
            key = self.batch.alert_key(state.key, state.verdict)
            if key in self._confirmed:
                continue
            if key not in self._alerted and len(self._alerted) >= MAX_ALERTS:
                self._suppressed.add(state.key)
                continue
            self._alerted.add(key)
            self._suppressed.discard(state.key)
            answer = self._deliver(
                key=key, outcome=BATCH_ALERT,
                text=alert_text(self.batch, state),
                why=f"member {state.key} settled {state.verdict}")
            self._note(key, answer)
            out.append(answer)
        return tuple(out)

    def _summarise(self, states, counts) -> tuple:
        answer = self._deliver(
            key=self.batch.summary_key, outcome=BATCH_SUMMARY,
            text=summary_text(self.batch, states, counts, len(states),
                              len(self._suppressed)),
            why=f"{counts.get(VERIFIED, 0)}/{len(states)} members verified")
        self._note(self.batch.summary_key, answer)
        state = getattr(answer, "state", None)
        if state == DELIVERED:
            return answer, SENT
        if state == DUPLICATE:
            return answer, REPLAY
        return answer, UNCONFIRMED

    def _deliver(self, *, key: str, outcome: str, text: str, why: str):
        if key in self._unconfirmed:
            answer = self.notifier.reconcile(key)
            self._note(key, answer)
            return answer
        answer = self.notifier.send_text(
            delivery_key=key, milestone_key=self.batch.milestone_key,
            outcome=outcome, text=text, why=why)
        self._note(key, answer)
        return answer

    def _note(self, key: str, answer) -> None:
        if answer is None:
            return
        state = getattr(answer, "state", None)
        if state in (DELIVERED, DUPLICATE):
            self._confirmed.add(key)
            self._unconfirmed.discard(key)
        else:
            self._unconfirmed.add(key)
            self._confirmed.discard(key)


__all__ = [
    "Batch", "BatchAggregator", "BatchMember", "BatchRefused", "BatchTick",
    "MemberState", "Receipt", "verdict_for_row", "summary_text", "alert_text",
    "SCHEMA", "BATCH_SUMMARY", "BATCH_ALERT",
    "PENDING", "VERIFIED", "GREEN", "ATTENTION", "FAILED", "UNKNOWN", "OFFLINE",
    "UNOBSERVED", "SKIPPED", "VERDICTS", "SETTLED_VERDICTS", "ACTIONABLE",
    "OPEN", "SENT", "REPLAY", "UNCONFIRMED", "MAX_ALERTS",
    "ORIGIN_REGISTRATION",
    "NOT_A_FACT_STATES", "SCOPE_MISMATCH_CODES", "RETRYABLE_REFUSALS",
]
