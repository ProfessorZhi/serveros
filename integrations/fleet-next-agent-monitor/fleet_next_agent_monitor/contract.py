"""The vocabulary the Fleet Next Agent Monitor may speak, and nothing else.

This module is deliberately a *vocabulary*, not a second adapter. It answers one
question — "what may a monitor tick say about a fact it read?" — and it is
shaped so that the answers it cannot give are the dangerous ones:

1.  There is no `DONE`, no `SUCCESS` and no `VERIFIED` status. The only positive
    terminal word is `TERMINAL_VERIFIED`, and `TERMINAL_VERIFIED` is not
    produced here at all: it is produced by a source that carried native
    activity (a transcript, or a verified artifact bound to the expected
    `sourceSHA`). A monitor that could mint the word by itself would eventually
    mint it for a `completed` run that never said anything.
2.  `UNKNOWN` and `OFFLINE` are first-class. A missing run, a boot mismatch, an
    unreadable conclusion and a draft pipeline are all *not success*, and a
    monitor that reports them as absence would be lying by omission.
3.  Identity is never folded. `instanceId`, `routeId`, `deviceRef`,
    `sessionRef`, `runId`, `deliveryId`, `bootId` and `sourceSHA` travel as
    separate fields, because "the same string appeared twice" is not evidence
    that two things are one thing.

The status and reason strings are byte-identical to the read-only Legacy source
adapter (`integrations/fleet-next-monitor-source-adapter`, PR #490) so that a
`#490` observation can be fed to this core unchanged, and so that a report which
says `NO_NATIVE_ACTIVITY` means the same thing in both lanes.
"""
from __future__ import annotations

SCHEMA_CONFIG = "serveros/fleet-next-agent-monitor-config.v1"
SCHEMA_STATE = "serveros/fleet-next-agent-monitor-state.v1"
SCHEMA_TICK = "serveros/fleet-next-agent-monitor-tick.v1"

# -- views ------------------------------------------------------------------
VIEW_RUN = "run"
VIEW_CI = "ci"
VIEWS = frozenset({VIEW_RUN, VIEW_CI})

# -- statuses (no DONE, by design) -----------------------------------------
STATUS_ACCEPTED = "ACCEPTED"
STATUS_QUEUED = "QUEUED"
STATUS_STARTED = "STARTED"
STATUS_RUNNING = "RUNNING"
STATUS_TERMINAL_VERIFIED = "TERMINAL_VERIFIED"
STATUS_FAILED = "FAILED"
STATUS_STALE = "STALE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_OFFLINE = "OFFLINE"
STATUS_DENIED = "DENIED"

STATUSES = frozenset({
    STATUS_ACCEPTED, STATUS_QUEUED, STATUS_STARTED, STATUS_RUNNING,
    STATUS_TERMINAL_VERIFIED, STATUS_FAILED, STATUS_STALE, STATUS_UNKNOWN,
    STATUS_OFFLINE, STATUS_DENIED,
})

#: The only statuses this core will ever hand to the notifier. A monitor tick
#: that dispatched `RUNNING` would turn "still working" into an alert; one that
#: dispatched `UNKNOWN` would turn "I could not see" into a claim.
DISPATCHABLE = frozenset({STATUS_TERMINAL_VERIFIED, STATUS_FAILED})

#: Statuses that must never be reported as success, named once so a test can
#: assert the whole set rather than the one case it happened to hit.
NOT_SUCCESS = frozenset({
    STATUS_ACCEPTED, STATUS_QUEUED, STATUS_STARTED, STATUS_RUNNING,
    STATUS_FAILED, STATUS_STALE, STATUS_UNKNOWN, STATUS_OFFLINE, STATUS_DENIED,
})

#: The statuses that mean *work is in flight right now*. They are the only
#: non-terminal statuses a tick may carry out of the door as `TICKED` at exit 0:
#: a run that is still running is not a failure, and a supervisor that is told
#: "still working" is not being lied to. Everything else that is not terminal —
#: `UNKNOWN`, `OFFLINE`, `STALE`, `DENIED` — is a fact the monitor could not
#: decide, and #495's Wave 16 §4.2 is the measurement of what happens when that
#: is handed to a supervisor as exit 0: nobody looks again.
LIVE_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_QUEUED, STATUS_STARTED,
                           STATUS_RUNNING})

# -- refusal words -----------------------------------------------------------
#: Named refusals. They live in the vocabulary rather than in one module,
#: because two layers must speak the same word: the *source* refuses an
#: unauthorized subject before it reads (zero provider calls) and the *notifier*
#: refuses it again before it sends (zero sends). A word defined in only one of
#: them would let the other layer invent its own.
BLOCKED_LEDGER = "BLOCKED_LEDGER"
BLOCKED_SUBJECT = "BLOCKED_SUBJECT"
BLOCKED_CI_SUBJECT = "BLOCKED_CI_SUBJECT"
BLOCKED_RUN_IDENTITY = "BLOCKED_RUN_IDENTITY"

BLOCKED_WORDS = frozenset({BLOCKED_LEDGER, BLOCKED_SUBJECT, BLOCKED_CI_SUBJECT,
                           BLOCKED_RUN_IDENTITY})

# -- reasons ----------------------------------------------------------------
REASON_NEVER_ISSUED = "NEVER_ISSUED"
REASON_EXPIRED = "EXPIRED"
REASON_RUN_NOT_FOUND = "RUN_NOT_FOUND"
REASON_NO_NATIVE_ACTIVITY = "NO_NATIVE_ACTIVITY"
REASON_BOOT_MISMATCH = "BOOT_MISMATCH"
REASON_INSTANCE_MISMATCH = "INSTANCE_MISMATCH"
REASON_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
REASON_ROUTE_OFFLINE = "ROUTE_OFFLINE"
REASON_READINGS_CAPPED = "READINGS_CAPPED"
REASON_REMOTE_REFUSED = "REMOTE_REFUSED"
REASON_UNKNOWN_STATUS = "UNKNOWN_STATUS"
REASON_UNVERIFIED_CI = "UNVERIFIED_CI"
REASON_DRAFT_CI = "DRAFT_CI"

#: Reasons this core adds, because they are about *its own* evidence rather
#: than about the far end.
REASON_NOT_GRANTED = "NOT_GRANTED"
REASON_NETWORK_UNREACHABLE = "NETWORK_UNREACHABLE"
REASON_SHA_MISMATCH = "SHA_MISMATCH"
REASON_BAD_PAYLOAD = "BAD_PAYLOAD"

REASONS = frozenset({
    REASON_NEVER_ISSUED, REASON_EXPIRED, REASON_RUN_NOT_FOUND,
    REASON_NO_NATIVE_ACTIVITY, REASON_BOOT_MISMATCH, REASON_INSTANCE_MISMATCH,
    REASON_IDENTITY_MISMATCH, REASON_ROUTE_OFFLINE, REASON_READINGS_CAPPED,
    REASON_REMOTE_REFUSED, REASON_UNKNOWN_STATUS, REASON_UNVERIFIED_CI,
    REASON_DRAFT_CI, REASON_NOT_GRANTED, REASON_NETWORK_UNREACHABLE,
    REASON_SHA_MISMATCH, REASON_BAD_PAYLOAD,
})

# -- exit codes (the same doors the batch CLI uses: #481, #488, #491) ------
EXIT_SETTLED = 0
EXIT_REFUSED = 2
EXIT_BLOCKED_INPUT = 3
EXIT_UNCONFIRMED = 4

STATE_SETTLED = "SETTLED"
STATE_TICKED = "TICKED"
STATE_BLOCKED_INPUT = "BLOCKED_INPUT"
STATE_UNCONFIRMED = "UNCONFIRMED"
STATE_REFUSED = "REFUSED"


class MonitorRefused(ValueError):
    """This monitor refused to act, with a code a caller can branch on.

    The sentence is written here, never quoted from a far end: a refusal that
    repeated remote text would be the leak the read-only boundary prevents.
    """

    def __init__(self, code: str, why: str) -> None:
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


def require_watch(watch: object) -> dict:
    """One watch, validated into a plain closed dict.

    A watch is a *question*, so its shape is closed: an unknown key would be a
    question this monitor silently ignores, which is worse than a refusal.
    """
    if not isinstance(watch, dict):
        raise MonitorRefused("BAD_WATCH", "a watch is a JSON object")
    allowed = {
        "kind", "device_ref", "session_ref", "run_id", "delivery_id",
        "expected_boot_id", "expected_source_sha", "project", "major", "label",
    }
    unexpected = set(watch) - allowed
    if unexpected:
        raise MonitorRefused("BAD_WATCH", f"unknown watch key(s): {sorted(unexpected)}")
    kind = watch.get("kind")
    if kind not in VIEWS:
        raise MonitorRefused("BAD_WATCH", f"kind {kind!r} is not one of {sorted(VIEWS)}")
    for name in ("device_ref", "session_ref", "run_id", "delivery_id",
                 "expected_boot_id", "expected_source_sha", "project", "label"):
        value = watch.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise MonitorRefused("BAD_WATCH", f"`{name}` must be a non-empty string or absent")
    for name in ("device_ref", "session_ref", "run_id", "delivery_id",
                 "expected_boot_id", "expected_source_sha", "project"):
        value = watch.get(name)
        if value is not None and any(ord(ch) < 32 for ch in value):
            raise MonitorRefused("BAD_WATCH", f"`{name}` is not a single-line value")
    if not isinstance(watch.get("major", False), bool):
        raise MonitorRefused("BAD_WATCH", "`major` must be a boolean")
    if kind == VIEW_RUN and not (watch.get("run_id") or watch.get("delivery_id")):
        raise MonitorRefused("BAD_WATCH", "a run watch needs a `run_id` or a `delivery_id`")
    if kind == VIEW_RUN and not (watch.get("device_ref") and watch.get("session_ref")):
        raise MonitorRefused("BAD_WATCH", "a run watch needs `device_ref` and `session_ref`")
    if kind == VIEW_CI and not watch.get("expected_source_sha"):
        raise MonitorRefused(
            "BAD_WATCH",
            "a CI watch needs the exact `expected_source_sha`; a pipeline name is "
            "not evidence for a commit")
    return dict(watch)


def watch_key(watch: dict) -> str:
    """The stable name of one question, across restarts and processes.

    Built from the *question*, not from the answer: a key that changed when the
    status changed would defeat the dedupe it exists for.
    """
    if watch["kind"] == VIEW_CI:
        return f"ci|{watch.get('project') or '-'}|{watch['expected_source_sha']}"
    ident = watch.get("run_id") or f"delivery:{watch.get('delivery_id')}"
    return f"run|{watch['device_ref']}|{watch['session_ref']}|{ident}"


def dispatch_fingerprint(observation: dict) -> str:
    """What a delivery is *about*, stable for one fact and different for another.

    Includes the identity and the reason: a run that changes from `UNKNOWN`
    (no native activity) to `TERMINAL_VERIFIED` is a different fact and must be
    allowed through, while the same `UNKNOWN` observed twice must not.
    """
    identity = observation.get("identity") or {}
    fields = [
        observation.get("view") or "",
        observation.get("status") or "",
        observation.get("reason") or "",
        str(identity.get("instanceId") or ""),
        str(identity.get("routeId") or ""),
        str(identity.get("deviceRef") or ""),
        str(identity.get("sessionRef") or ""),
        str(identity.get("runId") or ""),
        str(identity.get("deliveryId") or ""),
        str(identity.get("bootId") or ""),
        str(identity.get("sourceSHA") or ""),
    ]
    return "|".join(fields)
