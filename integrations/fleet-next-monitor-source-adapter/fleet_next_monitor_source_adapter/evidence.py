"""The monitor's native-evidence protocol, shared instead of re-invented.

The Fleet Next Agent Monitor (PR #493) reads a run fact through a *closed*
vocabulary — `integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/offline.py:27`:

    RUN_KEYS = {instanceId, deviceRef, sessionRef, sessionId, runId,
                deliveryId, bootId, status, lastAssistantMessage, artifacts,
                note}

A fact carrying any other key is refused by the monitor's own offline reader
(`ValueError("unknown fixture key(s): ...")`), which `sources.observe_run`
surfaces as `UNKNOWN` / `REMOTE_ValueError`. Its classifier
(`.../sources.py _has_native_activity`) credits native output from exactly two
places:

*   `lastAssistantMessage.content`, a non-empty string;
*   an `artifacts[]` entry marked `verified` **and** bound to the expected
    source sha. An artifact that names no sha, or another commit, is not
    evidence — `expected_source_sha` must be truthy *and* equal.

`transcript` / `messages` are not in that vocabulary. They are a *carrier* of
the same fact — the run's last assistant turn — so this module normalises a
carrier into the one shape the monitor reads, and drops every key outside
`RUN_KEYS`. PR #508 measured the divergence this removes: the same run facts
were `TERMINAL_VERIFIED` through this adapter and `UNKNOWN` through the
monitor's own path, because the adapter accepted a bare non-empty list as
"native activity" and the monitor refused the key outright.

Normalising is **not** accepting. A carrier yields a message only when its last
assistant-role turn carries non-empty text and no identity of its own that
contradicts the fact. Bare strings, other roles, an empty turn, a turn naming
another run — each produces no message at all, and the run stays `UNKNOWN`.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Mapping

__all__ = [
    "MONITOR_RUN_KEYS",
    "NOTE_VETO",
    "adopt_native_run_id",
    "native_evidence",
    "normalise_run_fact",
]

#: Every key the monitor's offline reader accepts, and nothing else
#: (`offline.py:27`, read at #493's head `6286bfbc`). Pinned by
#: `test_the_normaliser_emits_only_the_monitors_closed_vocabulary`, so a change
#: upstream is a red test here rather than a silent divergence.
MONITOR_RUN_KEYS = frozenset({
    "instanceId", "deviceRef", "sessionRef", "sessionId", "runId", "deliveryId",
    "bootId", "status", "lastAssistantMessage", "artifacts", "note",
})

#: The one shape native output is read from — `lastAssistantMessage`, with the
#: snake spelling the monitor also accepts, canonicalised onto it.
MESSAGE_KEYS = ("lastAssistantMessage", "last_assistant_message")

#: Keys whose value is the same fact under another spelling. Both are *read*
#: from an answer; only the monitor's spelling is *emitted*.
CARRIER_KEYS = ("transcript", "messages")

#: A device that says it produced no readable message is not evidence of
#: output, and its own words outrank a field a wrapper may have filled.
NOTE_VETO = "no readable assistant message"

#: Only an assistant turn is the run's output. A user prompt, a system
#: preamble or a tool result is not something the run produced.
ASSISTANT_ROLES = frozenset({"assistant"})

#: Identity a *turn* may carry, and the fact key it has to agree with. A turn
#: that names somebody else — another run, session, device, instance or boot —
#: is not this run's output.
_TURN_IDENTITY = (
    ("instanceId", "instance_id", "fleet_instance_id"),
    ("deviceRef", "device_ref"),
    ("sessionRef", "session_ref"),
    ("runId", "run_id"),
    ("deliveryId", "delivery_id"),
    ("bootId", "boot_id"),
)


def _first(payload: Mapping[str, Any], *names: str) -> Any:
    """The value under the first spelling that is *present*.

    Deliberately the same rule as `adapter._first`: a key that is present with
    a null value is an answer saying "none", not an absent key.
    """
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _is_own_turn(turn: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    """False when the turn names an identity that contradicts the fact's own."""
    for names in _TURN_IDENTITY:
        claimed = _first(turn, *names)
        if claimed is None:
            continue
        ours = _first(fact, *names)
        if ours is not None and claimed != ours:
            return False
    return True


def _message_from_carrier(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's last assistant turn, or nothing at all.

    The *last* assistant turn is the run's output, so it is the only candidate:
    an earlier turn cannot be upgraded over a later one. If that turn is not
    this run's own, or carries no text, the answer has no usable output — an
    empty or foreign tail is not replaced by a convenience fallback.
    """
    carrier = _first(payload, *CARRIER_KEYS)
    if not isinstance(carrier, (list, tuple)):
        return None
    for turn in reversed(carrier):
        if not isinstance(turn, Mapping):
            # A bare string is not a turn: it names no role, so it cannot be
            # shown to be assistant output. This is the case #508 caught.
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in ASSISTANT_ROLES:
            continue
        if not _is_own_turn(turn, payload):
            return None
        content = turn.get("content")
        if isinstance(content, str) and content.strip():
            # Only the text is emitted. The turn's own identifiers are not
            # forwarded: the observation's identity is the watch's, fenced by
            # `_identity_mismatch`, not whatever a transcript entry claimed.
            return {"content": content}
        return None
    return None


def normalise_run_fact(payload: Any) -> dict[str, Any]:
    """A run fact in the monitor's vocabulary, derived from an answer.

    Keys outside `MONITOR_RUN_KEYS` are dropped (they are exactly what the
    monitor's closed reader would refuse), and a carrier is folded into
    `lastAssistantMessage`.

    An answer that already declares a `lastAssistantMessage` — even a blank one
    — keeps *its text* and nothing else, and is never upgraded by a carrier it
    also shipped: the monitor reads that field first, and a carrier must not
    outvote the answer's own word about whether it produced anything. Only
    `content` is carried over; the rest of the object is remote content the
    monitor does not read, so it is not forwarded either.
    """
    if not isinstance(payload, Mapping):
        return {}
    fact = {key: payload[key] for key in payload if key in MONITOR_RUN_KEYS}
    for name in MESSAGE_KEYS:
        if name in payload:
            declared = payload[name]
            fact["lastAssistantMessage"] = (
                {"content": declared["content"]}
                if isinstance(declared, Mapping) and "content" in declared else {})
            return fact
    message = _message_from_carrier(payload)
    if message is not None:
        fact["lastAssistantMessage"] = message
    return fact


def _artifact_bound(artifact: Any, expected_source_sha: str | None) -> bool:
    """A verified artifact that belongs to the commit this read expects.

    Both halves are required, and the same way round as #493: no expected sha
    means nothing can be bound to it, so a verified artifact that names no
    commit never verifies a run.
    """
    if not isinstance(artifact, Mapping) or artifact.get("verified") is not True:
        return False
    if not isinstance(expected_source_sha, str) or not expected_source_sha:
        return False
    sha = _first(artifact, "sourceSHA", "source_sha", "sha")
    if not isinstance(sha, str) or not sha.strip():
        return False
    return sha.strip() == expected_source_sha


def native_evidence(fact: Mapping[str, Any],
                    expected_source_sha: str | None) -> bool:
    """The monitor's own predicate, on a normalised fact.

    Identical in every branch to `sources._has_native_activity`
    (#493 `6286bfbc`): the note veto first, then a non-empty
    `lastAssistantMessage.content`, then a verified artifact bound to the
    expected commit. Nothing else verifies a completed run.
    """
    note = fact.get("note")
    if isinstance(note, str) and NOTE_VETO in note.lower():
        return False
    message = _first(fact, *MESSAGE_KEYS)
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, str) and content.strip():
        return True
    artifacts = fact.get("artifacts")
    if isinstance(artifacts, (list, tuple)):
        for artifact in artifacts:
            if _artifact_bound(artifact, expected_source_sha):
                return True
    return False


def adopt_native_run_id(identity: Any, fact: Mapping[str, Any]) -> Any:
    """A delivery-only watch adopts the answer's own `runId`.

    #493's rule, verbatim: "A `delivery_id`-only watch has no `run_id` of its
    own. The far end may answer with the run's own `runId`; adopting *that* is
    not inventing one — it is the answer saying which run this delivery is. A
    watch that already named a run keeps its own id, and the mismatch check has
    already refused an answer that disagreed."

    Returns the identity unchanged when it already names a run, or when the
    answer names none.
    """
    if getattr(identity, "run_id", None) is not None:
        return identity
    native = _first(fact, "runId", "run_id")
    if not isinstance(native, str) or not native.strip():
        return identity
    return dataclasses.replace(identity, run_id=native.strip())
