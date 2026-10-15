"""Read a `serveros/core-lite-status.v1` document — as a CLAIM, never as evidence.

What this module is for
-----------------------
A device Node Adapter reaches a Core over a route Fleet already authenticated.
That route is the only thing here that knows *which device* answered. The
document that comes back is untrusted JSON: it carries its own `node`, its own
`principal`, and its own `live` flag, and none of those may be believed, because
a document that can name itself can name itself healthy.

So the two are kept apart on purpose:

* `expected_device_ref` is an ARGUMENT. It is supplied by the adapter from the
  route it already holds, and it is what the summary reports. The document's own
  `node` is reported only under `device.declared`, as a claim to compare, never
  as the identity of the row.
* `verified_route` is an ARGUMENT too, and it defaults to `False`. It is the one
  thing that lets a reading count as connected, and only a provider adapter may
  set it — after it has actually authenticated.

Everything is a pure function of its arguments. There is no clock read here (the
caller passes `now`), no network, no socket, no filesystem, and no import of
anything that could grow one.

What the summary says, and what it refuses to say
-------------------------------------------------
It never says *healthy*. It says whether a verified route reached a device whose
declaration matched, and then reports each observation with its own state. A
connected device with every observation `UNAVAILABLE` is a normal outcome: it
means the route is real and the numbers are missing, which is exactly the
distinction the source contract draws between "established" and "healthy".

`UNAVAILABLE` is never rendered as `0`, never as `null`, and never as healthy,
and its `reason` is drawn from the source contract's CLOSED set — an
unrecognised reason becomes `NO_SOURCE`, so a typo cannot invent a vocabulary
word a Fleet reader has never been taught.

Projection, not filtering
-------------------------
Only the keys this module names are emitted. A field it does not name cannot
appear in the summary even if the document carries it — which is how a stray
token, credential path or `argv` fragment is dropped rather than forwarded. A
filter would have to recognise what to remove and would be wrong the first time
something new appeared; this is a list of what may come through.
"""
from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any

#: The summary's own schema. Distinct from the source document's, because a
#: reader of this must not think it is holding the device's own words.
READBACK_SCHEMA = "serveros/fleet-core-lite-readback.v1"
SOURCE_SCHEMA = "serveros/core-lite-status.v1"

#: Closed, and copied from the source contract rather than invented. An
#: unrecognised reason is reported as NO_SOURCE — "the adapter was asked and had
#: nothing usable" — which is true of a reason nobody can interpret.
REASONS = frozenset({
    "UNSUPPORTED_PLATFORM", "NOT_IMPLEMENTED", "NO_SOURCE", "STALE",
    "IDENTITY_UNKNOWN",
})

OBSERVATION_STATES = frozenset({"PRESENT", "UNAVAILABLE"})

#: The observation names the source contract defines, and — for each — the value
#: keys this reader is willing to carry. Both lists are closed. `services`,
#: `connector` and `gpu.readonly` carry no numeric value in the contract, so
#: their permitted key set is empty: the row's STATE is the whole reading.
OBSERVATION_VALUE_KEYS: dict[str, frozenset[str]] = {
    "cpu": frozenset({"used_percent"}),
    "memory": frozenset({"total_bytes", "available_bytes", "free_bytes",
                         "used_percent"}),
    # No swap block exists in the source collector yet, so this is the smallest
    # set consistent with the vocabulary the contract already uses for memory.
    # Anything else is stripped and recorded, not forwarded.
    "swap": frozenset({"total_bytes", "used_bytes", "free_bytes",
                       "used_percent"}),
    "psi.cpu": frozenset({"some_avg10", "full_avg10"}),
    "psi.memory": frozenset({"some_avg10", "full_avg10"}),
    "psi.io": frozenset({"some_avg10", "full_avg10"}),
    # The one block whose value is a LIST. Its row keys are checked separately,
    # per row, because a role is per filesystem and not per reading.
    "storage.readonly": frozenset({"rows"}),
    "services": frozenset(),
    "connector": frozenset(),
    "gpu.readonly": frozenset(),
}

#: What one `storage.readonly` row may carry. `path`, `mount_point`, `fstype`,
#: `inodes` and the raw device name are deliberately absent: the role replaces
#: the device name, and the rest are the collector's business, not a reader's.
STORAGE_ROW_KEYS = frozenset({"role", "total_bytes", "available_bytes",
                              "used_bytes", "read_bytes", "write_bytes"})

#: The only top-level members carried out of an observation row.
_OBSERVATION_MEMBERS = frozenset({"name", "state", "reason", "source",
                                  "observed_at", "value"})

#: The only top-level members carried out of the document itself.
_DOCUMENT_MEMBERS = frozenset({"schema", "node", "principal", "platform",
                               "generation", "mode", "observed_at", "freshness",
                               "principal_identity", "device_identity", "live",
                               "observations"})

_BYTES_KEYS = frozenset({"total_bytes", "available_bytes", "free_bytes",
                         "used_bytes", "read_bytes", "write_bytes"})
_PERCENT_KEYS = frozenset({"used_percent", "some_avg10", "full_avg10"})

#: A role is the one free-text field in the contract, so it is the one that has
#: to be constrained rather than trusted.
_ROLE = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")

MAX_STORAGE_ROWS = 64

#: `device_identity.basis` values that may support a device verdict. Only
#: ATTESTED may, and nothing in V1 can produce it for a device reference — which
#: is why the summary's own verdict never rests on the document at all.
_BASES = frozenset({"ATTESTED", "LOCAL_DECLARATION", "CALLER_SUPPLIED", "NONE"})


@dataclass(frozen=True)
class Refusal:
    """One thing this reader would not carry, and why.

    `note` is the READER's sentence about what was wrong. It never quotes the
    value that was rejected: a note is written into a summary another surface
    may display, and echoing an untrusted string is how a document smuggles its
    own text out through a reader it did not convince.
    """

    kind: str
    where: str
    note: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "where": self.where, "note": self.note}


@dataclass
class _ObservationOut:
    state: str
    reason: str | None = None
    note: str | None = None
    value: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.state == "UNAVAILABLE":
            out["reason"] = self.reason
        else:
            out["value"] = dict(self.value)
        if self.note:
            out["note"] = self.note
        return out


def _as_iso(value: Any) -> dt.datetime | None:
    """Parse an ISO-8601 stamp, or `None`. Never guesses a timezone."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A stamp with no zone is not a time, it is a local convention. Reading
        # it as UTC would silently move it, and reading it as "now" would make
        # every such document fresh forever.
        return None
    return parsed


def _bytes_ok(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _percent_ok(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return 0 <= value <= 100


def _validate_numbers(name: str, value: dict[str, Any],
                      refusals: list[Refusal]) -> bool:
    """Whether every numeric in one observation's value is usable.

    The source contract's rule, inherited rather than softened: a block that is
    PRESENT but out of range or malformed makes the WHOLE reading unusable.
    Three of four memory numbers is a value a reader will do arithmetic on and
    get wrong, so no partial value is published.
    """
    ok = True
    for key, item in value.items():
        if key in _BYTES_KEYS:
            if not _bytes_ok(item):
                refusals.append(Refusal(
                    "MALFORMED_VALUE", f"{name}.{key}",
                    "not a non-negative integer"))
                ok = False
        elif key in _PERCENT_KEYS:
            if not _percent_ok(item):
                refusals.append(Refusal(
                    "MALFORMED_VALUE", f"{name}.{key}",
                    "not a percentage in 0..100"))
                ok = False
    return ok


def normalize_observations(
    document: object, *, refusals: list[Refusal] | None = None
) -> dict[str, _ObservationOut]:
    """Project the document's observation rows onto the keys this reader knows.

    Returns one entry per observation name the document declares. Names outside
    the contract's closed set are dropped and recorded; duplicates collapse to
    a single UNAVAILABLE row, because two rows for one name is a document
    disagreeing with itself and picking either would be inventing an answer.
    """
    out: dict[str, _ObservationOut] = {}
    sink = refusals if refusals is not None else []
    rows = document.get("observations") if isinstance(document, dict) else None
    if rows is None:
        return out
    if not isinstance(rows, list):
        sink.append(Refusal("MALFORMED_DOCUMENT", "observations",
                            "not a list"))
        return out

    seen: set[str] = set()
    for index, row in enumerate(rows):
        where = f"observations[{index}]"
        if not isinstance(row, dict):
            sink.append(Refusal("MALFORMED_DOCUMENT", where, "not an object"))
            continue
        name = row.get("name")
        if not isinstance(name, str) or name not in OBSERVATION_VALUE_KEYS:
            sink.append(Refusal("UNKNOWN_OBSERVATION", where,
                                "name is not in the source contract"))
            continue
        if name in seen:
            # First sighting keeps the name so the summary still reports the
            # observation; the duplicate is what is refused.
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "the document declared this name twice")
            sink.append(Refusal("DUPLICATE_OBSERVATION", where,
                                f"{name} already declared"))
            continue
        seen.add(name)

        for extra in sorted(set(row) - _OBSERVATION_MEMBERS):
            sink.append(Refusal("UNKNOWN_FIELD", f"{where}.{extra}",
                                "not a member this reader carries"))

        state = row.get("state")
        if state not in OBSERVATION_STATES:
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "state is not PRESENT or UNAVAILABLE")
            sink.append(Refusal("UNKNOWN_STATE", f"{where}.state",
                                "not PRESENT or UNAVAILABLE"))
            continue

        if state == "UNAVAILABLE":
            reason = row.get("reason")
            if reason not in REASONS:
                # A typo must not become a new vocabulary word. The contract
                # says an unrecognised reason is reported as NO_SOURCE.
                sink.append(Refusal("UNKNOWN_REASON", f"{where}.reason",
                                    "not in the source contract's closed set"))
                reason = "NO_SOURCE"
            out[name] = _ObservationOut("UNAVAILABLE", reason)
            continue

        value = row.get("value")
        if not isinstance(value, dict):
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "PRESENT without an object value")
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.value",
                                "not an object"))
            continue

        allowed = OBSERVATION_VALUE_KEYS[name]
        kept: dict[str, Any] = {}
        for key in sorted(value):
            if key not in allowed:
                sink.append(Refusal("UNKNOWN_FIELD", f"{where}.value.{key}",
                                    "not a key this reader carries"))
                continue
            kept[key] = value[key]

        if name == "storage.readonly":
            kept, detail = _normalize_storage(kept, where, sink)
            if kept is None:
                out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE", detail)
                continue
            out[name] = _ObservationOut("PRESENT", None, None, {"rows": kept})
            continue

        if not allowed:
            # The contract defines no value for this observation; PRESENT with
            # a payload is a document saying more than the vocabulary allows.
            if kept:
                out[name] = _ObservationOut(
                    "UNAVAILABLE", "NO_SOURCE",
                    "PRESENT carries a value the contract does not define")
                continue
            out[name] = _ObservationOut("PRESENT", None, None, {})
            continue

        if not kept:
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "PRESENT without any value")
            continue
        if not _validate_numbers(name, kept, sink):
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "a value was out of range or malformed")
            continue
        out[name] = _ObservationOut("PRESENT", None, None, kept)
    return out


def _normalize_storage(
    value: dict[str, Any], where: str, sink: list[Refusal]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """The one block that is a list of rows, so it is validated as one."""
    rows = value.get("rows")
    if not isinstance(rows, list):
        # `storage.readonly` is the only shape the contract defines for this
        # observation, and it is a list. A dict here means the document is not
        # the document this reader was written for.
        sink.append(Refusal("MALFORMED_VALUE", f"{where}.value",
                            "storage.readonly is not a list of rows"))
        return None, "storage.readonly is not a list of rows"
    if len(rows) > MAX_STORAGE_ROWS:
        sink.append(Refusal("MALFORMED_VALUE", f"{where}.value",
                            f"more than {MAX_STORAGE_ROWS} rows"))
        return None, "storage.readonly exceeds the row bound"

    clean: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        spot = f"{where}.value.rows[{index}]"
        if not isinstance(row, dict):
            sink.append(Refusal("MALFORMED_VALUE", spot, "not an object"))
            return None, "a storage row is not an object"
        role = row.get("role")
        if not isinstance(role, str) or not _ROLE.match(role):
            sink.append(Refusal("MALFORMED_VALUE", f"{spot}.role",
                                "not a role the contract admits"))
            return None, "a storage row has no admissible role"
        entry: dict[str, Any] = {"role": role}
        numerics = 0
        for key in sorted(row):
            if key == "role":
                continue
            if key not in STORAGE_ROW_KEYS:
                sink.append(Refusal("UNKNOWN_FIELD", f"{spot}.{key}",
                                    "not a key this reader carries"))
                continue
            if not _bytes_ok(row[key]):
                sink.append(Refusal("MALFORMED_VALUE", f"{spot}.{key}",
                                    "not a non-negative integer"))
                return None, "a storage row carries a malformed byte count"
            entry[key] = row[key]
            numerics += 1
        if not numerics:
            sink.append(Refusal("MALFORMED_VALUE", spot,
                                "row carries no byte counts"))
            return None, "a storage row carries no byte counts"
        clean.append(entry)
    if not clean:
        return None, "storage.readonly declared no rows"
    return clean, None


def readback(
    document: object,
    *,
    expected_device_ref: str,
    binding_account: str | None,
    received_at: dt.datetime,
    verified_route: bool = False,
    max_age_seconds: float = 60.0,
) -> dict[str, Any]:
    """Summarise one untrusted Core-Lite document against a verified route.

    `expected_device_ref` and `binding_account` come from the Fleet side — the
    device this route reaches and the OS account the binding declares. Neither
    is read from the document. `verified_route` may only be set by a provider
    adapter that has actually authenticated; it defaults to False, and with it
    False the summary is NOT_CONNECTED no matter what the document claims.

    `received_at` is the caller's clock, so a test can move time without
    patching this module. Nothing here reads a clock itself, which is what makes
    a freshness verdict reproducible.
    """
    refusals: list[Refusal] = []

    if not isinstance(document, dict):
        return {
            "schema": READBACK_SCHEMA,
            "device_ref": expected_device_ref,
            "source": {"schema": None, "verified_route": bool(verified_route),
                       "received_at": received_at.isoformat()},
            "device": {"declared": None, "state": "UNKNOWN"},
            "principal": {"declared": None, "expected": binding_account,
                          "state": "UNKNOWN", "basis": None},
            "connected": "UNKNOWN",
            "live": {"claimed": False, "accepted": False,
                     "why": "no document was read"},
            "freshness": {"state": "NO_TIMESTAMP", "age_seconds": None,
                          "bound_seconds": max_age_seconds},
            "observations": {},
            "observations_unavailable": [],
            "integrity": "DEGRADED",
            "refusals": [Refusal("MALFORMED_DOCUMENT", "document",
                                 "not a JSON object").as_dict()],
        }

    for extra in sorted(set(document) - _DOCUMENT_MEMBERS):
        refusals.append(Refusal("UNKNOWN_FIELD", extra,
                                "not a member this reader carries"))

    source_schema = document.get("schema")
    if source_schema != SOURCE_SCHEMA:
        refusals.append(Refusal("UNKNOWN_SCHEMA", "schema",
                                "not the source contract this reader reads"))

    # --- device: the document may declare one; only the route establishes it.
    declared_node = document.get("node")
    if not isinstance(declared_node, str) or not declared_node:
        declared_node = None
    if declared_node is None:
        device_state = "UNKNOWN"
    elif declared_node == expected_device_ref:
        device_state = "MATCHED"
    else:
        device_state = "MISMATCH"
        refusals.append(Refusal(
            "DEVICE_MISMATCH", "node",
            "the document names a device this route does not reach"))

    # The document's own device verdict is reported for context and used for
    # nothing. A `MATCHED` on a LOCAL_DECLARATION basis is the account agreeing
    # with itself, which is why the contract cannot raise `live` on it.
    device_identity = document.get("device_identity")
    declared_basis = None
    if isinstance(device_identity, dict):
        basis = device_identity.get("basis")
        declared_basis = basis if basis in _BASES else None

    # --- principal: compared against the binding, never against the document.
    declared_principal = document.get("principal")
    if not isinstance(declared_principal, str) or not declared_principal:
        declared_principal = None
    if binding_account is None or declared_principal is None:
        principal_state = "UNKNOWN"
    elif declared_principal == binding_account:
        principal_state = "MATCHED"
    else:
        principal_state = "MISMATCH"
        refusals.append(Refusal(
            "PRINCIPAL_MISMATCH", "principal",
            "the document names an account this binding does not own"))

    # --- freshness: the SOURCE's stamp, judged by the caller's clock.
    stamp = _as_iso(document.get("observed_at"))
    if stamp is None:
        freshness = {"state": "NO_TIMESTAMP", "age_seconds": None,
                     "bound_seconds": max_age_seconds}
        refusals.append(Refusal("NO_TIMESTAMP", "observed_at",
                                "absent or not an absolute timestamp"))
    else:
        age = (received_at - stamp).total_seconds()
        if age < 0:
            freshness = {"state": "FUTURE", "age_seconds": int(age),
                         "bound_seconds": max_age_seconds}
            refusals.append(Refusal("FUTURE_TIMESTAMP", "observed_at",
                                    "stamped after the reading was received"))
        elif age > max_age_seconds:
            freshness = {"state": "STALE", "age_seconds": int(age),
                         "bound_seconds": max_age_seconds}
            refusals.append(Refusal("STALE_TIMESTAMP", "observed_at",
                                    "older than the freshness bound"))
        else:
            freshness = {"state": "FRESH", "age_seconds": int(age),
                         "bound_seconds": max_age_seconds}

    observations = normalize_observations(document, refusals=refusals)

    # --- the verdict. Identity is the ONLY thing that can raise it, and the
    # document contributes nothing to it: a device reference cannot be attested
    # in V1, so the adapter's route report is the whole of the evidence.
    if device_state == "MISMATCH" or principal_state == "MISMATCH":
        connected = "NOT_CONNECTED"
    elif not verified_route:
        connected = "NOT_CONNECTED"
    elif device_state != "MATCHED" or principal_state != "MATCHED":
        connected = "UNKNOWN"
    elif freshness["state"] != "FRESH":
        connected = "UNKNOWN"
    else:
        connected = "CONNECTED"

    claimed_live = document.get("live")
    claimed_live = claimed_live if isinstance(claimed_live, bool) else False
    if connected != "CONNECTED":
        accepted = False
        why = {
            "NOT_CONNECTED": "no verified route, or the document disagrees "
                             "with the route this reader holds",
            "UNKNOWN": "the identity or the time could not be established",
        }[connected]
    elif not claimed_live:
        # The source is entitled to withhold `live`; accepting it is not this
        # reader's to do, but neither is overriding it upwards.
        accepted = False
        why = "the source did not claim this document is live"
    else:
        accepted = True
        why = "a verified route, a matching declaration and a fresh stamp"

    unavailable = sorted(name for name, row in observations.items()
                         if row.state == "UNAVAILABLE")

    return {
        "schema": READBACK_SCHEMA,
        "device_ref": expected_device_ref,
        "source": {
            "schema": source_schema if isinstance(source_schema, str) else None,
            "verified_route": bool(verified_route),
            "received_at": received_at.isoformat(),
            "mode": document.get("mode") if isinstance(document.get("mode"), str) else None,
            "platform": (document.get("platform")
                         if isinstance(document.get("platform"), str) else None),
            "declared_device_basis": declared_basis,
        },
        "device": {"declared": declared_node, "state": device_state},
        "principal": {"declared": declared_principal, "expected": binding_account,
                      "state": principal_state,
                      "basis": _principal_basis(document)},
        "connected": connected,
        "live": {"claimed": claimed_live, "accepted": accepted, "why": why},
        "freshness": freshness,
        "observations": {name: row.as_dict()
                         for name, row in sorted(observations.items())},
        "observations_unavailable": unavailable,
        "integrity": "OK" if not refusals else "DEGRADED",
        "refusals": [item.as_dict() for item in refusals],
    }


def _principal_basis(document: dict[str, Any]) -> str | None:
    """The basis the DOCUMENT declares for its principal verdict.

    Carried through because the contract says the same word on a different
    basis is a different fact — but it is reported, never used: the summary's
    own verdict comes from the binding comparison above.
    """
    identity = document.get("principal_identity")
    if not isinstance(identity, dict):
        return None
    basis = identity.get("basis")
    return basis if basis in _BASES else None


def unconnected_summary(
    *, expected_device_ref: str, binding_account: str | None,
    received_at: dt.datetime, reason: str = "no source is wired for this device",
) -> dict[str, Any]:
    """What a device with no Core-Lite route at all reports.

    Kept here rather than left to each caller so that "nobody connected a
    source" cannot be spelled as a zero, an empty reading, or — worst — a
    healthy device with nothing to say.
    """
    return {
        "schema": READBACK_SCHEMA,
        "device_ref": expected_device_ref,
        "source": {"schema": None, "verified_route": False,
                   "received_at": received_at.isoformat(), "mode": None,
                   "platform": None, "declared_device_basis": None},
        "device": {"declared": None, "state": "UNKNOWN"},
        "principal": {"declared": None, "expected": binding_account,
                      "state": "UNKNOWN", "basis": None},
        "connected": "UNKNOWN",
        "live": {"claimed": False, "accepted": False, "why": reason},
        "freshness": {"state": "NO_TIMESTAMP", "age_seconds": None,
                      "bound_seconds": None},
        "observations": {},
        "observations_unavailable": [],
        "integrity": "OK",
        "refusals": [],
    }


__all__ = [
    "READBACK_SCHEMA",
    "SOURCE_SCHEMA",
    "REASONS",
    "OBSERVATION_STATES",
    "OBSERVATION_VALUE_KEYS",
    "STORAGE_ROW_KEYS",
    "MAX_STORAGE_ROWS",
    "Refusal",
    "normalize_observations",
    "readback",
    "unconnected_summary",
]
