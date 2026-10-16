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

#: Closed sets for the three document-level discriminators. Each is a value the
#: reader must RECOGNISE rather than a string it may pass along: a summary that
#: echoes whatever `mode` a document declared has handed an untrusted string a
#: seat in Fleet's own output, which is the same channel the value projector
#: exists to close.
MODES = frozenset({"observe-only"})
PLATFORMS = frozenset({"linux", "windows"})

#: What is written in a refusal's `where` when the offending member's own NAME
#: came from the document. The name is not echoed: a refusal is part of the
#: public summary, and `{"<canary>": 1}` would otherwise smuggle its key out
#: through the very record that says the key was dropped.
UNNAMED = "<unnamed>"

#: Closed, and copied from the source contract rather than invented. An
#: unrecognised reason is reported as NO_SOURCE — "the adapter was asked and had
#: nothing usable" — which is true of a reason nobody can interpret.
REASONS = frozenset({
    "UNSUPPORTED_PLATFORM", "NOT_IMPLEMENTED", "NO_SOURCE", "STALE",
    "IDENTITY_UNKNOWN",
})

OBSERVATION_STATES = frozenset({"PRESENT", "UNAVAILABLE"})

#: Scalar KINDS a projected leaf may be. Each is its own check because the
#: producer applies a different one to each, and collapsing them is how a byte
#: count ends up validated as a percentage.
K_BYTES = "bytes"      # non-negative integer; a real is refused, not rounded
K_RATE = "rate"        # non-negative finite number — 0 is a MEASURED zero
K_PERCENT = "percent"  # a share of wall-clock time, so [0, 100]
K_ROLE = "role"        # the contract's one free-text field, tightly bounded

#: The shape of one observation's value, as the native producer actually emits
#: it. Closed: a key not named here cannot appear in a summary. `services`,
#: `connector` and `gpu.readonly` carry no value in the contract, so their map
#: is empty — the row's STATE is the whole reading.
#:
#: Note what changed from the first revision of this reader: the shapes below
#: are the ones the Linux adapter in `native/node-core` really writes, confirmed
#: against its source, not the ones a reader might have guessed. `swap` carries
#: paging RATES and no `used_percent`; `psi.*` nests `some`/`full`; storage is
#: two role blocks rather than one row list.
OBSERVATION_VALUE_KEYS: dict[str, dict[str, str]] = {
    "cpu": {"used_percent": K_PERCENT},
    "memory": {"total_bytes": K_BYTES, "available_bytes": K_BYTES,
               "free_bytes": K_BYTES, "used_percent": K_PERCENT},
    # The STOCK (bytes in use) and the FLOW (pages per second). The producer
    # deliberately withholds `used_percent` here: it is the field most likely to
    # be mistaken for a paging signal, and the two rates answer that question.
    "swap": {"total_bytes": K_BYTES, "free_bytes": K_BYTES, "used_bytes": K_BYTES,
             "pages_in_per_second": K_RATE, "pages_out_per_second": K_RATE},
    "psi.cpu": {"some": "psi_line", "full": "psi_line"},
    "psi.memory": {"some": "psi_line", "full": "psi_line"},
    "psi.io": {"some": "psi_line", "full": "psi_line"},
    "storage.readonly": {"filesystems": "fs_block", "disk_io": "disk_block"},
    # The native adapter answers `services` with one object holding a list of
    # rows. Leaving this map empty — which this reader did until PR #217 landed
    # a real producer — made a genuine service reading look like PRESENT with an
    # empty value: success-shaped, and silently empty.
    "services": {"services": "service_rows"},
    "connector": {},
    # The card counters and the instant this process asked for them. `gpus` is
    # the reading; `sample_time` is NOT `observed_at` — nvidia-smi stamps
    # nothing, so this is the caller's own clock at the moment of the question,
    # and the document's `freshness` stays NOT_ASSERTED either way. The rows are
    # projected by `_gpu_value` (all-or-nothing, like `storage.readonly`), and
    # this map is what tells the reader which members are ITS shape, so a
    # document that grew a `processes` list is refused rather than carried.
    "gpu.readonly": {"gpus": "gpu_rows", "sample_time": "sample_time"},
}

#: A card counter. `index` and `label` identify a card to a human without naming
#: an instance — no UUID, no serial, no bus id — which is why `label` is
#: charset-bounded below rather than carried as free text.
GPU_ROW_KEYS = frozenset({"index", "label", "gpu_util_percent",
                          "memory_used_bytes", "memory_total_bytes",
                          "temperature_c"})

#: What a `gpu.readonly` value may hold. `sample_time` is optional: the producer
#: omits it when its own clock gave it nothing, and its absence is not a
#: malformed document.
GPU_VALUE_KEYS = frozenset({"gpus", "sample_time"})

#: Mirrors of the wire schema's own bounds. Named here so a refusal can quote
#: the number rather than describe it: `kMaxGpuRows` and the row schema in
#: `core-lite-status.v1.schema.json`.
GPU_MAX_ROWS = 16
GPU_MAX_INDEX = 4096
GPU_LABEL_MAX = 64
GPU_TEMP_MIN, GPU_TEMP_MAX = -40.0, 150.0

#: The one free-text field on a row, and the charset a model name is allowed.
#: No `/`, no `:`, no quote, no backslash and no shell metacharacter, so a
#: forged answer cannot dress a command line as a label.
GPU_LABEL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .-_+()"
)

#: A run this long is an IDENTIFIER, not a marketing name: a UUID segment is
#: eight, a serial is a long digit run. The longest run in `A6000` is five and in
#: `RTX 8000` is four, so ordinary names pass. Refusing the RUN rather than the
#: whole shape is what keeps model names working while no instance id travels
#: here; and the direction of the guess is deliberate — a name wrongly refused
#: costs a label, an identifier wrongly published is a hardware identifier
#: leaked.
GPU_IDENTIFIER_RUN = 8

#: The words one service row's `state` may hold. Closed, and copied from the
#: native adapter's own `kServiceStates` with `UNKNOWN` deliberately EXCLUDED:
#: that adapter refuses `UNKNOWN` rather than publishing it ("a measured reading
#: never carries this word"), so a document carrying one is not its output.
#: `NOT_INSTALLED` is here and is NOT a synonym for `INACTIVE`: a unit name
#: systemd cannot find is a different fact from a unit that is present and
#: stopped, and collapsing them reports an installed-but-stopped service for a
#: service that is not installed at all.
SERVICE_ROW_STATES = frozenset({
    "ACTIVE", "INACTIVE", "FAILED", "ACTIVATING", "DEACTIVATING",
    "NOT_INSTALLED", "UNAVAILABLE",
})

#: What a service row may carry. An unreadable row is the producer's in-place
#: marker (`state: UNAVAILABLE` + `reason`) and carries NOTHING else — no
#: `observed_at` for a reading that was never taken.
SERVICE_ROW_KEYS = frozenset({"id", "unit", "state", "reason", "observed_at",
                              "age_seconds", "source"})

#: Fields the native producer emits that this reader deliberately does NOT carry.
#: `sub_state` is `SubState=` copied verbatim out of `systemctl show` — free text
#: this reader has no closed vocabulary for, so it is dropped rather than
#: bounded-and-hoped. Named here so dropping it is a decision on the page rather
#: than a gap someone finds later.
SERVICE_UNPROJECTED_KEYS = frozenset({"sub_state"})

#: The only `source` the native adapter writes. A closed set of one: a document
#: claiming another source is not that producer's output.
SERVICE_SOURCES = frozenset({"systemd-user"})

#: Rows per observation, matching the bound the native adapter applies to every
#: other block it projects.
MAX_SERVICE_ROWS = 64

#: The three PSI windows. All three keys are always written by the producer; one
#: that is unreadable is an explicit marker in place, never an omitted key.
PSI_WINDOWS = ("avg10", "avg60", "avg300")

#: `storage.readonly` is two independent role blocks. Either may be absent — the
#: collector may have one and not the other — and at most 64 rows each.
STORAGE_BLOCK_KEYS = frozenset({"filesystems", "disk_io"})
FILESYSTEM_ROW_KEYS = frozenset({"role", "total_bytes", "available_bytes",
                                 "used_bytes"})
DISK_ROW_KEYS = frozenset({"role", "read_bytes", "write_bytes"})

#: Observations whose value may carry the producer's in-place UNAVAILABLE marker
#: (`{"state": "UNAVAILABLE", "reason": ...}`) where a number would otherwise go.
#: `cpu` and `memory` are absent on purpose: the native adapter projects them
#: all-or-nothing, so a marker there is a document this reader does not know.
INLINE_UNAVAILABLE_OBSERVATIONS = frozenset({"swap", "psi.cpu", "psi.memory",
                                             "psi.io"})

#: What a partly-measured reading says about itself.
COMPLETENESS_WORDS = frozenset({"COMPLETE", "PARTIAL"})

#: The only top-level members carried out of an observation row.
_OBSERVATION_MEMBERS = frozenset({"name", "state", "reason", "source",
                                  "observed_at", "value", "completeness"})

#: The only top-level members carried out of the document itself.
_DOCUMENT_MEMBERS = frozenset({"schema", "node", "principal", "platform",
                               "generation", "mode", "observed_at", "freshness",
                               "principal_identity", "device_identity", "live",
                               "observations"})

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
    #: Only on a PRESENT reading, and only ever the word DERIVED from the value
    #: this reader actually projected — never the document's own claim.
    completeness: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.state == "UNAVAILABLE":
            out["reason"] = self.reason
        else:
            out["value"] = dict(self.value)
            if self.completeness:
                out["completeness"] = self.completeness
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


def _rate_ok(value: Any) -> bool:
    """A paging rate. Zero here is a measured zero, not an absence.

    "no page moved in a measurable window" and "the interval was not measurable"
    are different statements, and the producer keeps them apart by writing `0`
    for the first and a marker for the second. So a non-negative finite number
    is accepted as-is.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return value >= 0


def _percent_ok(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if math.isnan(value) or math.isinf(value):
        return False
    return 0 <= value <= 100


def _is_inline_unavailable(node: Any) -> bool:
    """The producer's in-place marker for one field it could not measure.

    Recognised by the `state` word alone, and only ever *inside* a PRESENT
    value. Its `reason` is validated separately: the adapter writes its own
    closed word there, never the collector's prose, and a reader that echoed
    whatever it found would hand the producer a channel.
    """
    return isinstance(node, dict) and node.get("state") == "UNAVAILABLE"


#: The three answers a scalar leaf can give. `UNAVAILABLE` is a legitimate
#: reading of "not measured" in the places the producer allows it, and is never
#: folded into a number — least of all into zero, which is a measurement.
_OK, _UNAVAIL, _BAD = "ok", "unavailable", "bad"


def _scalar(kind: str, node: Any) -> tuple[str, Any]:
    if _is_inline_unavailable(node):
        return _UNAVAIL, None
    if kind == K_BYTES:
        return (_OK, node) if _bytes_ok(node) else (_BAD, None)
    if kind == K_RATE:
        return (_OK, node) if _rate_ok(node) else (_BAD, None)
    if kind == K_PERCENT:
        return (_OK, node) if _percent_ok(node) else (_BAD, None)
    return _BAD, None


def _unit_id_ok(unit: Any) -> bool:
    """The supervisor's `valid_unit_id`, restated rather than invented.

    Length 9..128, ends in `.service`, no `..`, first character alphanumeric,
    every character alphanumeric or one of `_ . @ : -`. That set is what makes a
    path, a traversal and a shell metacharacter unrepresentable, which is the
    property the producer's reader relies on — so this is the same rule, not a
    second and more permissive one.
    """
    if not isinstance(unit, str) or not 9 <= len(unit) <= 128:
        return False
    if not unit.endswith(".service") or ".." in unit:
        return False
    if not (unit[0].isascii() and unit[0].isalnum()):
        return False
    return all(c.isascii() and (c.isalnum() or c in "_.@:-") for c in unit)


def _service_id_ok(value: Any) -> bool:
    """A service id: the same character class as a unit stem, bounded.

    The producer's profile reader does not shape-check `id` at all, so this is
    the one place a service name could carry something else. Bounding it to the
    characters a unit may already use keeps it from being a second, looser
    allowance for arbitrary text.
    """
    return (isinstance(value, str) and 1 <= len(value) <= 64
            and all(c.isascii() and (c.isalnum() or c in "_.@:-") for c in value))


def _service_rows(value: dict[str, Any], sink: list[Refusal]) -> tuple[str, Any]:
    """`services`: one list of rows, all-or-nothing like every other block.

    A row this reader cannot validate is not a row it may skip — the producer's
    own rule for its tables is that one unusable row makes the whole aggregate
    unusable, "because silently skipping it would report a machine with fewer
    filesystems than it has". A service list is the same claim about a machine.
    """
    unknown = set(value) - {"services"}
    if unknown:
        sink.append(Refusal("UNKNOWN_FIELD", "services.value",
                            f"{len(unknown)} key(s) this reader does not carry "
                            f"were dropped"))
    rows = value.get("services")
    if not isinstance(rows, list) or len(rows) > MAX_SERVICE_ROWS:
        return _BAD, None

    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    measured = 0
    all_measured = True
    for row in rows:
        if not isinstance(row, dict):
            return _BAD, None
        dropped = len(set(row) - SERVICE_ROW_KEYS - SERVICE_UNPROJECTED_KEYS)
        if dropped:
            sink.append(Refusal("UNKNOWN_FIELD", "services.value.row",
                                f"{dropped} key(s) this reader does not carry "
                                f"were dropped"))
        ident, unit, state = row.get("id"), row.get("unit"), row.get("state")
        if not _service_id_ok(ident) or not _unit_id_ok(unit):
            return _BAD, None
        if state not in SERVICE_ROW_STATES:
            return _BAD, None
        # A duplicated id is a document disagreeing with itself about which
        # service is which, and the producer's own reader refuses it for the
        # same reason: two rows with one id is one of them unreachable.
        if ident in seen:
            return _BAD, None
        seen.add(ident)

        entry: dict[str, Any] = {"id": ident, "unit": unit, "state": state}
        if state == "UNAVAILABLE":
            reason = row.get("reason")
            if reason not in REASONS:
                return _BAD, None
            entry["reason"] = reason
            all_measured = False
            clean.append(entry)
            continue

        measured += 1
        age = row.get("age_seconds")
        if age is not None:
            # A bool is an int in Python and an age of `True` is not an age.
            if not _bytes_ok(age):
                return _BAD, None
            entry["age_seconds"] = age
        stamp = row.get("observed_at")
        if stamp is not None:
            # Only an absolute timestamp; a zoneless one is a local convention,
            # and carrying it would invite a reader to place it somewhere.
            if _as_iso(stamp) is None:
                return _BAD, None
            entry["observed_at"] = stamp
        source = row.get("source")
        if source is not None:
            if source not in SERVICE_SOURCES:
                return _BAD, None
            entry["source"] = source
        clean.append(entry)
    return _OK, (clean, all_measured)


def _reason_of(node: dict[str, Any]) -> str:
    """The closed reason for an in-place marker, or NO_SOURCE.

    A reason outside the contract's closed set is reported as NO_SOURCE rather
    than carried: a typo must not become a vocabulary word no reader was taught,
    and an echoed string would be one.
    """
    reason = node.get("reason")
    return reason if reason in REASONS else "NO_SOURCE"


def _marker(node: dict[str, Any]) -> dict[str, str]:
    """The marker as this reader re-emits it: closed shape, closed reason."""
    return {"state": "UNAVAILABLE", "reason": _reason_of(node)}


def _psi_line(node: Any, sink: list[Refusal], label: str) -> tuple[str, Any]:
    """One `some`/`full` line: three windows, each a percent or a marker.

    Returns `(_OK, (windows, all_measured))`, `(_UNAVAIL, None)` when the whole
    line is the producer's marker, or `(_BAD, None)` when it is not a shape this
    reader knows. A line whose three windows are all markers is the producer's
    own "not a reading", so it becomes one here too.
    """
    if _is_inline_unavailable(node):
        return _UNAVAIL, None
    if not isinstance(node, dict):
        return _BAD, None
    extra = set(node) - set(PSI_WINDOWS)
    if extra:
        # `total_microseconds` is deliberately not projected by the producer, and
        # anything else here is a document that is not its output. Counted and
        # dropped at every level, never named and never carried.
        sink.append(Refusal("UNKNOWN_FIELD", f"psi.{label}",
                            f"{len(extra)} key(s) this reader does not carry "
                            f"were dropped"))
    windows: dict[str, Any] = {}
    measured = 0
    all_measured = True
    for window in PSI_WINDOWS:
        if window not in node:
            # The producer always writes all three keys; one absent means this
            # is not the document this reader was written for.
            return _BAD, None
        state, value = _scalar(K_PERCENT, node[window])
        if state == _OK:
            windows[window] = value
            measured += 1
        elif state == _UNAVAIL:
            windows[window] = _marker(node[window])
            all_measured = False
        else:
            return _BAD, None
    if not measured:
        return _UNAVAIL, None
    return _OK, (windows, all_measured)


def _role_rows(node: Any, allow: frozenset[str], need_bytes: int,
               sink: list[Refusal], label: str) -> tuple[str, Any]:
    """One `{"roles": [...]}` block of role rows.

    The producer drops a whole block when any single row fails to project —
    "silently skipping it would report a machine with fewer filesystems than it
    has" — so a row this reader cannot validate is not a row it may skip either.
    """
    if _is_inline_unavailable(node):
        return _UNAVAIL, None
    if not isinstance(node, dict) or set(node) != {"roles"}:
        return _BAD, None
    rows = node["roles"]
    if not isinstance(rows, list) or len(rows) > MAX_STORAGE_ROWS:
        return _BAD, None
    if not rows:
        return _BAD, None
    clean: list[dict[str, Any]] = []
    unknown = 0
    for row in rows:
        if not isinstance(row, dict):
            return _BAD, None
        role = row.get("role")
        if not isinstance(role, str) or not _ROLE.match(role):
            return _BAD, None
        entry: dict[str, Any] = {"role": role}
        measured = 0
        for key, value in row.items():
            if key == "role":
                continue
            if key not in allow:
                # `path`, `mount_point`, `source`, `fstype`, `inodes` and the raw
                # device name live here in a document that is not the producer's
                # projection. Dropped and counted, never named and never carried.
                unknown += 1
                continue
            if not _bytes_ok(value):
                return _BAD, None
            entry[key] = value
            measured += 1
        if measured < need_bytes:
            return _BAD, None
        clean.append(entry)
    if unknown:
        sink.append(Refusal("UNKNOWN_FIELD", f"storage.readonly.{label}",
                            f"{unknown} key(s) this reader does not carry were "
                            f"dropped"))
    return _OK, clean


def _storage_value(value: dict[str, Any], sink: list[Refusal]) -> tuple[str, Any]:
    """`storage.readonly`: two independent role blocks, either one optional."""
    unknown = set(value) - STORAGE_BLOCK_KEYS
    if unknown:
        sink.append(Refusal("UNKNOWN_FIELD", "storage.readonly.value",
                            f"{len(unknown)} key(s) this reader does not carry "
                            f"were dropped"))
    out: dict[str, Any] = {}
    bad = False
    # A filesystem row must carry all three byte counts; a disk row must carry
    # both. Anything less is a partial row, which the producer never writes.
    for key, allow, need in (("filesystems", FILESYSTEM_ROW_KEYS, 3),
                             ("disk_io", DISK_ROW_KEYS, 2)):
        if key not in value:
            continue
        state, rows = _role_rows(value[key], allow, need, sink, key)
        if state == _OK:
            out[key] = {"roles": rows}
        elif state == _UNAVAIL:
            bad = True
        else:
            # A malformed row is not skipped; the reading it belonged to is.
            sink.append(Refusal("MALFORMED_VALUE", f"storage.readonly.{key}",
                                "a role row did not survive validation"))
            bad = True
    if bad:
        return _BAD, None
    if not out:
        return _BAD, None
    return _OK, out


def _finite_number(node: Any) -> bool:
    """A real number, and not a bool. `bool` is an `int` in Python, so a
    document that wrote `true` where a percentage belongs would otherwise be
    read as `1` — a measured-looking value for a field nobody measured."""
    if isinstance(node, bool) or not isinstance(node, (int, float)):
        return False
    return node == node and node not in (float("inf"), float("-inf"))


def _gpu_label_ok(text: Any) -> bool:
    """Whether a row's `label` looks like a model name and nothing else.

    The same rule the producer applies before it will emit a label, restated
    here because a reader may not take a producer's word for it: a document is
    free to write anything, and this is the one field on the row that is text.
    """
    if not isinstance(text, str) or not text or len(text) > GPU_LABEL_MAX:
        return False
    if any(character not in GPU_LABEL_CHARS for character in text):
        return False
    run = 0
    for character in text:
        hex_digit = character in "0123456789abcdefABCDEF"
        run = run + 1 if hex_digit else 0
        if run >= GPU_IDENTIFIER_RUN:
            return False
    return True


def _gpu_rows(rows: Any, sink: list[Refusal]) -> tuple[str, Any]:
    """Validate the card rows. Returns (state, rows) with state in _OK/_BAD.

    All-or-nothing, like `storage.readonly` and for the same reason: the
    contract has the producer refuse the WHOLE sample when one card's answer
    does not survive, because a reader comparing two cards has no way to know
    one row was silently dropped. A marker in a numeric field is refused here
    rather than carried, matching the producer's own rule that an `N/A` in any
    numeric field makes the sample UNAVAILABLE — and matching this reader's
    habit of trusting a document's markers only where the producer writes them
    (`INLINE_UNAVAILABLE_OBSERVATIONS`).
    """
    if not isinstance(rows, list):
        sink.append(Refusal("MALFORMED_VALUE", "gpu.readonly.gpus",
                            "not a list of rows"))
        return _BAD, None
    if not rows:
        # A host with no card is `UNAVAILABLE` in the contract, and the producer
        # says so by not being PRESENT at all. An empty list is therefore not a
        # reading of "zero cards" — it is a document this reader does not know,
        # and reporting it PRESENT would be a success-shaped nothing.
        sink.append(Refusal("MALFORMED_VALUE", "gpu.readonly.gpus",
                            "an empty row list is not a reading"))
        return _BAD, None
    if len(rows) > GPU_MAX_ROWS:
        sink.append(Refusal("MALFORMED_VALUE", "gpu.readonly.gpus",
                            f"more than {GPU_MAX_ROWS} rows"))
        return _BAD, None

    out: list[dict[str, Any]] = []
    seen_index: set[int] = set()
    for position, row in enumerate(rows):
        where = f"gpu.readonly.gpus[{position}]"
        if not isinstance(row, dict):
            sink.append(Refusal("MALFORMED_VALUE", where, "not an object"))
            return _BAD, None
        unknown = set(row) - GPU_ROW_KEYS
        if unknown:
            sink.append(Refusal("UNKNOWN_FIELD", where,
                                f"{len(unknown)} key(s) this reader does not "
                                f"carry were dropped"))
            return _BAD, None
        missing = GPU_ROW_KEYS - set(row)
        if missing:
            # Every member is required on every row: a row without one is not
            # this shape, and filling it in would be inventing a measurement.
            sink.append(Refusal("MALFORMED_VALUE", where,
                                f"{len(missing)} required field(s) absent"))
            return _BAD, None

        index = row["index"]
        if not _bytes_ok(index) or index > GPU_MAX_INDEX:
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.index",
                                "not an integer in range"))
            return _BAD, None
        if index in seen_index:
            # Two answers for one card is a contradiction, and picking one is
            # picking silently.
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.index",
                                "duplicate card index"))
            return _BAD, None
        seen_index.add(index)

        if not _gpu_label_ok(row["label"]):
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.label",
                                "not a bounded model name"))
            return _BAD, None

        used, total = row["memory_used_bytes"], row["memory_total_bytes"]
        if not _bytes_ok(used) or not _bytes_ok(total):
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.memory",
                                "not a non-negative integer"))
            return _BAD, None
        if used > total:
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.memory",
                                "used memory exceeds total"))
            return _BAD, None

        util, temperature = row["gpu_util_percent"], row["temperature_c"]
        if not _finite_number(util) or not 0 <= util <= 100:
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.gpu_util_percent",
                                "not a number in [0, 100]"))
            return _BAD, None
        if not _finite_number(temperature) or not (GPU_TEMP_MIN <= temperature <= GPU_TEMP_MAX):
            sink.append(Refusal("MALFORMED_VALUE", f"{where}.temperature_c",
                                f"not a number in [{GPU_TEMP_MIN:g}, "
                                f"{GPU_TEMP_MAX:g}]"))
            return _BAD, None

        out.append({"index": index, "label": row["label"],
                    "gpu_util_percent": util, "memory_used_bytes": used,
                    "memory_total_bytes": total, "temperature_c": temperature})
    return _OK, out


def _gpu_value(value: dict[str, Any], sink: list[Refusal]) -> tuple[str, Any, bool]:
    """Project a `gpu.readonly` value. Returns (state, value, all_measured).

    `sample_time` is validated when present and its ABSENCE does not make the
    reading partial: the wire schema marks it optional, the producer omits it
    when its own clock gave it nothing, and the document's `freshness` is
    NOT_ASSERTED whether it is there or not. It is never read as `observed_at`.
    """
    state, rows = _gpu_rows(value["gpus"], sink)
    if state != _OK:
        return _BAD, None, False
    out: dict[str, Any] = {"gpus": rows}
    if "sample_time" in value:
        stamp = _as_iso(value["sample_time"])
        if stamp is None:
            # A time this reader cannot place is not a time: reading it as UTC
            # would move it, and reading it as "now" would make the reading look
            # fresh forever.
            sink.append(Refusal("MALFORMED_VALUE", "gpu.readonly.sample_time",
                                "not a timestamp this reader can place"))
            return _BAD, None, False
        out["sample_time"] = value["sample_time"]
    return _OK, out, True


def _project_value(name: str, value: dict[str, Any], sink: list[Refusal]
                   ) -> tuple[str, Any, bool]:
    """Project one PRESENT value. Returns (state, value, all_measured)."""
    spec = OBSERVATION_VALUE_KEYS[name]
    unknown = set(value) - set(spec)
    if unknown:
        sink.append(Refusal("UNKNOWN_FIELD", f"{name}.value",
                            f"{len(unknown)} key(s) this reader does not carry "
                            f"were dropped"))
    if name == "gpu.readonly":
        # Its members are not scalars, so it is projected here rather than
        # through the generic loop below — the same shape as
        # `storage.readonly`, and for the same reason.
        unknown = set(value) - GPU_VALUE_KEYS
        if unknown:
            return _BAD, None, False
        return _gpu_value(value, sink)

    if name == "storage.readonly":
        state, projected = _storage_value(value, sink)
        # The native adapter assembles storage from two blocks that are each
        # all-or-nothing and never carries an in-place marker, so a storage
        # reading is COMPLETE whenever it exists at all.
        return state, projected, True

    if name == "services":
        state, payload = _service_rows(value, sink)
        if state != _OK:
            # An empty list is a READING — "this account runs no services" — and
            # is deliberately not the same answer as a list this reader could
            # not validate, which is no reading at all.
            return _BAD, None, False
        rows, all_measured = payload
        return _OK, {"services": rows}, all_measured

    out: dict[str, Any] = {}
    measured = 0
    all_measured = True
    allow_inline = name in INLINE_UNAVAILABLE_OBSERVATIONS
    for key, kind in spec.items():
        if key not in value:
            return _BAD, None, False
        if kind in ("psi_line",):
            state, payload = _psi_line(value[key], sink, key)
            if state == _OK:
                windows, line_complete = payload
                out[key] = windows
                measured += 1
                all_measured = all_measured and line_complete
            elif state == _UNAVAIL and key == "full":
                # `full` is the line a kernel may legitimately not provide. The
                # producer states that absence in place, and so does this.
                out[key] = _marker(value[key])
                all_measured = False
            elif state == _UNAVAIL and key == "some":
                # "`some` is the line every kernel that has PSI at all
                # provides. Without it there is no reading here, whatever
                # `full` said." The producer refuses the whole observation, and
                # so does this reader.
                return _BAD, None, False
            else:
                return _BAD, None, False
            continue

        state, scalar = _scalar(kind, value[key])
        if state == _OK:
            out[key] = scalar
            measured += 1
        elif state == _UNAVAIL and allow_inline:
            out[key] = _marker(value[key])
            all_measured = False
        else:
            # A number that is out of range or malformed, or an in-place marker
            # where the producer never writes one. The contract's rule: the
            # WHOLE reading becomes UNAVAILABLE, because three of four memory
            # numbers is a value a reader will do arithmetic on and get wrong.
            if state == _BAD:
                sink.append(Refusal("MALFORMED_VALUE", f"{name}.{key}",
                                    "not a value this reader can carry"))
            return _BAD, None, False

    if not measured:
        return _BAD, None, False
    return _OK, out, all_measured


def normalize_observations(
    document: object, *, refusals: list[Refusal] | None = None
) -> dict[str, _ObservationOut]:
    """Project the document's observation rows onto the shapes this reader knows.

    Returns one entry per observation name the document declares. Names outside
    the contract's closed set are dropped and recorded; duplicates collapse to a
    single UNAVAILABLE row, because two rows for one name is a document
    disagreeing with itself and picking either would be inventing an answer.
    """
    out: dict[str, _ObservationOut] = {}
    sink = refusals if refusals is not None else []
    rows = document.get("observations") if isinstance(document, dict) else None
    if rows is None:
        return out
    if not isinstance(rows, list):
        sink.append(Refusal("MALFORMED_DOCUMENT", "observations", "not a list"))
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
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "the document declared this name twice")
            sink.append(Refusal("DUPLICATE_OBSERVATION", where,
                                f"{name} already declared"))
            continue
        seen.add(name)

        dropped = len(set(row) - _OBSERVATION_MEMBERS)
        if dropped:
            # Counted, never named: the names are the document's, and this
            # record is part of a summary another surface displays.
            sink.append(Refusal("UNKNOWN_FIELD", where,
                                f"{dropped} member(s) this reader does not "
                                f"carry were dropped"))

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

        verdict, projected, all_measured = _project_value(name, value, sink)
        if verdict != _OK:
            out[name] = _ObservationOut("UNAVAILABLE", "NO_SOURCE",
                                        "a value was out of range or malformed")
            continue

        # `completeness` is CHECKED against the value rather than believed. The
        # producer's word is a summary of what it just built; a document is free
        # to write any word it likes, and the one thing a reader must never do
        # is let a wrong COMPLETE make an absent field look measured.
        declared = row.get("completeness")
        derived = "COMPLETE" if all_measured else "PARTIAL"
        if declared is None:
            sink.append(Refusal("MISSING_COMPLETENESS", f"{where}.completeness",
                                "a PRESENT reading must state its completeness"))
        elif declared not in COMPLETENESS_WORDS:
            sink.append(Refusal("UNKNOWN_COMPLETENESS", f"{where}.completeness",
                                "not COMPLETE or PARTIAL"))
        elif declared != derived:
            sink.append(Refusal("COMPLETENESS_MISMATCH", f"{where}.completeness",
                                "disagrees with what the value actually holds"))
        out[name] = _ObservationOut("PRESENT", None, None, projected,
                                    completeness=derived)
    return out


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

    dropped = len(set(document) - _DOCUMENT_MEMBERS)
    if dropped:
        refusals.append(Refusal("UNKNOWN_FIELD", "document",
                                f"{dropped} member(s) this reader does not "
                                f"carry were dropped"))

    # --- the three closed discriminators. Each must be RECOGNISED, and a
    # failure here fails the whole readback closed: a payload that is not the
    # document this reader reads, or that declares a mode V1 does not have, is
    # not this device's reading, whatever else it got right.
    source_schema = document.get("schema")
    schema_ok = source_schema == SOURCE_SCHEMA
    if not schema_ok:
        refusals.append(Refusal("UNKNOWN_SCHEMA", "schema",
                                "not the source contract this reader reads"))

    source_mode = document.get("mode")
    mode_ok = isinstance(source_mode, str) and source_mode in MODES
    if not mode_ok:
        # V1 has no acting mode, and a reader that had to infer whether the
        # Core is willing to act would infer wrong.
        refusals.append(Refusal("UNKNOWN_MODE", "mode",
                                "not a mode this reader recognises"))

    source_platform = document.get("platform")
    platform_ok = isinstance(source_platform, str) and source_platform in PLATFORMS
    if not platform_ok:
        refusals.append(Refusal("UNKNOWN_PLATFORM", "platform",
                                "not a platform this reader recognises"))

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
    #
    # The schema/mode/platform gates are checked here rather than left as
    # refusals alone. A refusal that does not reach the verdict is a note, not a
    # gate: a document could carry a foreign schema, match every string, and
    # still be published as CONNECTED with the source's own `live` accepted.
    if not verified_route:
        connected = "NOT_CONNECTED"
    elif not (schema_ok and mode_ok and platform_ok):
        connected = "NOT_CONNECTED"
    elif device_state == "MISMATCH" or principal_state == "MISMATCH":
        connected = "NOT_CONNECTED"
    elif device_state != "MATCHED" or principal_state != "MATCHED":
        connected = "UNKNOWN"
    elif freshness["state"] != "FRESH":
        connected = "UNKNOWN"
    else:
        connected = "CONNECTED"

    claimed_live = document.get("live")
    claimed_live = claimed_live if isinstance(claimed_live, bool) else False
    if connected == "NOT_CONNECTED":
        accepted = False
        why = ("no verified route, or the document is not one this reader may "
               "report as this device's reading")
    elif connected == "UNKNOWN":
        accepted = False
        why = "the identity or the time could not be established"
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
        # Every free-text member below is either a RECOGNISED constant or the
        # trusted value it was compared against. None of them echoes a string
        # the document chose: a token, a path or an argv fragment parked in
        # `platform` would otherwise ride out through the metadata while the
        # value projector was busy closing the observations.
        "source": {
            "schema": SOURCE_SCHEMA if schema_ok else None,
            "verified_route": bool(verified_route),
            "received_at": received_at.isoformat(),
            "mode": source_mode if mode_ok else None,
            "platform": source_platform if platform_ok else None,
            "declared_device_basis": declared_basis,
        },
        # `declared` carries the TRUSTED value when the two agreed, and null
        # when they did not. The state already says a mismatch happened; the
        # claimed string would add nothing an operator needs and everything a
        # smuggler wants.
        "device": {"declared": expected_device_ref if device_state == "MATCHED" else None,
                   "state": device_state},
        "principal": {"declared": binding_account if principal_state == "MATCHED" else None,
                      "expected": binding_account,
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
    "PSI_WINDOWS",
    "STORAGE_BLOCK_KEYS",
    "FILESYSTEM_ROW_KEYS",
    "DISK_ROW_KEYS",
    "INLINE_UNAVAILABLE_OBSERVATIONS",
    "SERVICE_ROW_STATES",
    "SERVICE_ROW_KEYS",
    "SERVICE_UNPROJECTED_KEYS",
    "SERVICE_SOURCES",
    "MAX_SERVICE_ROWS",
    "GPU_ROW_KEYS",
    "GPU_VALUE_KEYS",
    "GPU_MAX_ROWS",
    "GPU_MAX_INDEX",
    "GPU_LABEL_MAX",
    "GPU_TEMP_MIN",
    "GPU_TEMP_MAX",
    "GPU_IDENTIFIER_RUN",
    "COMPLETENESS_WORDS",
    "K_BYTES",
    "K_RATE",
    "K_PERCENT",
    "K_ROLE",
    "MAX_STORAGE_ROWS",
    "Refusal",
    "normalize_observations",
    "readback",
    "unconnected_summary",
]
