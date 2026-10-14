"""The Phase 3 shadow seam: PR#161's envelope, attached BESIDE the Python inventory.

PR#161 (branch `feat/native-node-core-cpp-20261013`) shipped a read-only C++20
collector and a bounded adapter that turns its document into
`serveros/native-node-measurement.v1`. PR#160 (branch
`feat/serveros-fleet-card-v1-20261013`) shipped a Fleet monitor card whose
`accept_native_measurement` turns that same envelope into a device entry. This
module is the seam between them, and it exists on neither branch.

WHAT IT IS
----------
A caller that has explicitly decided to measure the host calls `shadow()`. It
either hands in a parsed envelope (a fixture, a replay, a test) or names the
compiled collector, in which case the envelope is produced by PR#161's adapter
under the tree's one bound. What comes back is a SHADOW document: the native
reading, marked non-authoritative, together with a parity comparison against
the Python inventory that remains the only authority.

WHAT IT DELIBERATELY IS NOT
---------------------------
* It is NOT a producer of served values. `runtime/inventory.py` answers "how is
  this host"; a native reading answers "does the answer agree". `served()` is
  the only function here that returns anything servable, and it returns the
  Python values unchanged with the native block attached beside them.
* It is NOT a second subprocess runner. When it needs the collector it calls
  PR#161's `collect()`, which calls `runtime/bounded_invocation.invoke_once` —
  the tree's declared chokepoint, whose argv is a list and whose receipt shape
  is the same for every outcome. This module contains no exec call of its own,
  and a test parses its AST to keep that true.
* It is NOT wired in. Nothing under `runtime/`, `mcp/`, `context/`, `fleet/`,
  `abilities/`, `bin/`, `scripts/` or `intake/` mentions it, so it cannot enter
  `serverosd`'s loader closure that `tests/test_core_freeze.py` asserts. The
  same property PR#161 asserted for its adapter, asserted again for this file.
* It does NOT import or merge PR#160. The bridge lives on another branch; this
  module carries the CONTRACT the bridge enforces (`contract/`) and checks the
  envelope against it, so the two can be reconciled mechanically when the
  branches meet. See `README.md` for that step and what it is blocked on.
* It does NOT divide the host up by account. Every measurement in this envelope
  is machine-wide. Per-UID attribution is a different producer (ServerOS issue
  #167) and nothing here infers it from the collector's own uid.

THE FLAG
--------
`ENABLED_BY_DEFAULT` is `False` and `enabled` has no default, mirroring the
adapter's own `collect(..., enabled=True)`. A caller that has not decided to
measure the host cannot measure it by omission, and a disabled pass starts no
child and writes no file. `feature_enabled()` states which of the two
authorities answered.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

FLAG = "native-node-core-shadow"
ENV_VAR = "SERVEROS_NATIVE_NODE_CORE_SHADOW"
ENABLED_BY_DEFAULT = False

SOURCE = "native-node-core"
SHADOW_SCHEMA = "serveros/native-node-shadow.v1"
ENVELOPE_SCHEMA = "serveros/native-node-measurement.v1"
DEVICE_ENTRY_SCHEMA = "serveros/fleet-monitor-device.v1"

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
CONTRACT_PATH = _HERE / "contract" / "native-to-fleet-contract.v1.json"
ADAPTER_PATH = _REPO / "native" / "node-core" / "adapter" / "native_node_core.py"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}

# A quantity that cannot move between two instants must agree exactly; one that
# moves by design is allowed the churn. These are the tolerances of the CHECK,
# never of the reading — nothing here rounds a measured number.
_BYTE_TOLERANCE_FRACTION = 0.001   # 0.1% of the Python figure, or one byte
_PERCENT_TOLERANCE = 0.1


# --------------------------------------------------------------------------
# One module object tree-wide, loaded by path — the tree's convention for a
# runtime module, and the same names the rest of the tree already uses so that
# neither file is given a second copy by being loaded here.
# --------------------------------------------------------------------------

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _adapter():
    return _load("serveros_native_node_core_adapter", ADAPTER_PATH)


def _graph():
    return _load("serveros_runtime_graph", _REPO / "runtime" / "graph.py")


def _resolve_now(now):
    """The clock to age a reading with. ``None`` means this process's, not none.

    `runtime/graph.py` reads a missing clock as "this reader has no clock" and
    answers UNKNOWN. That is right for a reader that has none and wrong here:
    this seam is running, so it has one, and reporting an unmeasurable age for
    a reason that is simply untrue is worse than reporting no reason.
    """
    return datetime.now(timezone.utc) if now is None else now


# --------------------------------------------------------------------------
# The flag
# --------------------------------------------------------------------------

def feature_enabled(*, enabled=None, env=None) -> tuple[bool, str | None]:
    """Whether to measure. Returns ``(enabled, reason)``; the reason is always set
    when it is off, so a caller can print why nothing happened.

    Three authorities, in order: an explicit argument (which wins outright, in
    both directions), then the environment, then the module default. The default
    is off, so the last authority answer is "off" and says so.
    """
    if enabled is False:
        return False, ("the caller explicitly disabled the native-node-core shadow "
                       "seam; nothing was run and nothing was written")
    if enabled is True:
        return True, None
    environment = os.environ if env is None else env
    raw = str(environment.get(ENV_VAR, "")).strip().lower()
    if raw in _TRUTHY:
        return True, None
    return False, (
        f"{FLAG} is off: ENABLED_BY_DEFAULT is {ENABLED_BY_DEFAULT}, the caller "
        f"did not ask, and {ENV_VAR} is {raw or 'unset'}. Nothing was run and "
        f"nothing was written.")


# --------------------------------------------------------------------------
# The frozen contract with PR#160's bridge
# --------------------------------------------------------------------------

def load_contract() -> dict:
    """The seam contract, read rather than restated.

    The rules are a document because the bridge that enforces them is on another
    branch: a copy in code and a copy in that branch would disagree the first
    time either moved, and a rule that exists only here would be a rule this
    branch invented. `contract/native-to-fleet-contract.v1.json` records where
    each comes from and how the two are reconciled.
    """
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def _dig(document: dict, path: str):
    current = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def _rule_holds(rule: dict, value, present: bool) -> bool:
    op = rule["op"]
    if op == "is_object":
        return isinstance(value, dict)
    if op == "is_bool":
        return isinstance(value, bool)
    if op == "equals":
        return present and value == rule["value"]
    raise ValueError(f"the contract names an op this checker cannot read: {op!r}")


def envelope_violations(envelope) -> list[dict]:
    """Every rule of the seam contract this envelope breaks. Empty means it conforms.

    Returns violations rather than raising, because a shadow that raised on a
    bad envelope would be a second gate on a path that is already gated — and
    the caller still wants the parity block and the reason.
    """
    contract = load_contract()
    if not isinstance(envelope, dict):
        rule = next(r for r in contract["envelope_rules"] if r["rule"] == "shape")
        return [{"rule": "shape", "path": "$", "reason": rule["reason"]}]
    out = []
    for rule in contract["envelope_rules"]:
        if rule["rule"] == "shape":
            continue
        when = rule.get("when")
        if when is not None and any(envelope.get(key) != value for key, value in when.items()):
            # The rule is about a reading that exists; holding a failure
            # envelope to it would refuse the very shape the failure path is
            # required to produce.
            continue
        value, present = _dig(envelope, rule["path"])
        if not _rule_holds(rule, value, present):
            out.append({"rule": rule["rule"], "path": rule["path"],
                        "reason": rule["reason"]})
    return out


def device_entry_violations(entry) -> list[dict]:
    """Every rule of the seam contract a Fleet device entry breaks. Empty means it conforms.

    This is the other half of the seam: it is what lets a fixture recorded from
    PR#160's branch be checked here without that branch, its module or its
    working tree being touched.
    """
    contract = load_contract()
    if not isinstance(entry, dict):
        return [{"rule": "device_entry.shape", "path": "$",
                 "reason": "a device entry is a parsed JSON object"}]
    out = []
    if entry.get("schema") != contract["device_entry_schema"]:
        out.append({
            "rule": "device_entry.schema", "path": "schema",
            "reason": f"a device entry is {contract['device_entry_schema']!r}, "
                      f"got {entry.get('schema')!r}"})
    for key in contract["device_entry_required"]:
        if key not in entry:
            out.append({"rule": "device_entry.required", "path": key,
                        "reason": f"a device entry carries {key!r}"})
    readings = entry.get("readings")
    if isinstance(readings, list):
        for index, reading in enumerate(readings):
            if not isinstance(reading, dict):
                out.append({"rule": "device_entry.reading", "path": f"readings[{index}]",
                            "reason": "a reading is an object"})
                continue
            for key in contract["reading_requires"]:
                if key not in reading:
                    out.append({"rule": "device_entry.reading",
                                "path": f"readings[{index}].{key}",
                                "reason": f"a reading carries {key!r}"})
    return out


# --------------------------------------------------------------------------
# Parity: the native reading against the authority
# --------------------------------------------------------------------------

def _failure_reason(envelope) -> str:
    """Why there is no reading, from the envelope's own words. Never invented."""
    if not isinstance(envelope, dict):
        return "no envelope was produced"
    measurements = envelope.get("measurements")
    if isinstance(measurements, dict) and measurements.get("reason"):
        return str(measurements["reason"])
    refusals = envelope.get("refusals") or []
    if refusals and isinstance(refusals[0], dict) and refusals[0].get("reason"):
        return str(refusals[0]["reason"])
    unknowns = envelope.get("unknowns") or []
    if unknowns and isinstance(unknowns[0], dict) and unknowns[0].get("reason"):
        return str(unknowns[0]["reason"])
    return "the envelope declares readings_available=false and names no reason"


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _present(block) -> bool:
    """Whether a measurement block was measured at all.

    The collector's own rule: a block carries ``state`` PRESENT when it was read
    and UNAVAILABLE-with-a-reason when it was not. A block with no `state` is one
    the Python side writes, and is read as present.
    """
    return isinstance(block, dict) and block.get("state") in (None, "PRESENT")


def _row(quantity, *, role, python_field, native_field, python_value, native_value,
         allowed, note, informational=False, python_reason=None, native_reason=None):
    python_value = _number(python_value)
    native_value = _number(native_value)
    if python_value is None or native_value is None:
        verdict, delta = "NOT_COMPARABLE", None
        reason = (python_reason if python_value is None else native_reason) or (
            "one side did not answer")
    else:
        delta = native_value - python_value
        if delta == 0:
            verdict = "EXACT"
        elif abs(delta) <= allowed:
            verdict = "WITHIN"
        else:
            verdict = "DIFFERS"
        reason = None
    return {"quantity": quantity, "role": role,
            "python_field": python_field, "native_field": native_field,
            "python_value": python_value, "native_value": native_value,
            "delta": delta, "verdict": verdict, "informational": informational,
            "note": note, "reason": reason}


def parity(*, python_reading=None, envelope=None) -> list[dict]:
    """The native reading compared with the Python inventory, quantity by quantity.

    The Python side is the authority and the native side is the candidate; the
    comparison never decides which is right, and a quantity one side did not
    answer is NOT_COMPARABLE rather than a delta computed against a zero.

    The one join that would be silently wrong is named here rather than left to
    be discovered: `runtime/inventory.py`'s role row `free_bytes` is df's Avail
    column (`statvfs f_bavail`), and the collector's `free_bytes` is `f_bfree`,
    which includes the blocks reserved for root. On `/` on this host those are
    22 GiB apart and on `/data` (xfs, which reserves nothing) they are equal —
    the exact shape of a bug that survives review. The correct join is the one
    below, and a test refuses the row that names the other.
    """
    python_reading = python_reading if isinstance(python_reading, dict) else {}
    measurements = None
    if isinstance(envelope, dict) and envelope.get("readings_available") is True:
        candidate = envelope.get("measurements")
        if _present(candidate):
            measurements = candidate
    if measurements is None:
        return [{"quantity": "host_reading", "role": None, "python_field": None,
                 "native_field": None, "python_value": None, "native_value": None,
                 "delta": None, "verdict": "NOT_COMPARABLE", "informational": False,
                 "note": "没有可比的读数：原生侧没有可用读数",
                 "reason": _failure_reason(envelope)}]

    python_filesystems = python_reading.get("filesystems") or {}
    python_roles = {row.get("role"): row
                    for row in (python_filesystems.get("roles") or [])
                    if isinstance(row, dict) and row.get("role")}
    native_filesystems = measurements.get("filesystems") or {}
    native_roles = {row.get("role"): row
                    for row in (native_filesystems.get("roles") or [])
                    if isinstance(row, dict) and row.get("role")}

    out = []
    for role in list(native_roles) + [name for name in python_roles if name not in native_roles]:
        python_role = python_roles.get(role) or {}
        native_role = native_roles.get(role) or {}
        native_measured = _present(native_role)
        missing_role = (f"the Python inventory has no role row for {role}"
                        if role not in python_roles else None)
        unmeasured = (None if native_measured
                      else str(native_role.get("reason") or f"the collector did not "
                                                           f"measure role {role}"))

        def side(field, block, measured):
            return block.get(field) if measured else None

        total_python = side("total_bytes", python_role, _present(python_role))
        total_native = side("total_bytes", native_role, native_measured)
        out.append(_row(
            "filesystems.total_bytes", role=role,
            python_field="total_bytes", native_field="total_bytes",
            python_value=total_python, native_value=total_native,
            allowed=max(1.0, abs(total_python or 0) * _BYTE_TOLERANCE_FRACTION),
            note="容量不随时间变化，两侧应当逐字节相等",
            python_reason=missing_role, native_reason=unmeasured))

        left_python = side("free_bytes", python_role, _present(python_role))
        left_native = side("available_bytes", native_role, native_measured)
        out.append(_row(
            "filesystems.room_left_bytes", role=role,
            python_field="free_bytes", native_field="available_bytes",
            python_value=left_python, native_value=left_native,
            allowed=max(1.0, abs(left_python or 0) * _BYTE_TOLERANCE_FRACTION),
            note=("「还剩多少」两侧口径：Python 的 free_bytes 是 df 的 Avail 列"
                  "（statvfs f_bavail），原生侧的 available_bytes 是同一个数。"
                  "原生侧的 free_bytes 是 f_bfree（含 root 保留块），**不**参与这条比对"),
            python_reason=missing_role, native_reason=unmeasured))

        percent_python = side("used_percent", python_role, _present(python_role))
        percent_native = side("used_percent", native_role, native_measured)
        out.append(_row(
            "filesystems.used_percent", role=role,
            python_field="used_percent", native_field="used_percent",
            python_value=percent_python, native_value=percent_native,
            allowed=_PERCENT_TOLERANCE,
            note="百分比，两侧同一口径",
            python_reason=missing_role, native_reason=unmeasured))

    python_memory = python_reading.get("memory") or {}
    native_memory = measurements.get("memory") or {}
    memory_measured = _present(native_memory)
    memory_reason = (None if memory_measured
                     else str(native_memory.get("reason") or "the collector did not "
                                                            "measure memory"))
    total_python = python_memory.get("total_bytes") if _present(python_memory) else None
    total_native = native_memory.get("total_bytes") if memory_measured else None
    out.append(_row(
        "memory.total_bytes", role=None,
        python_field="total_bytes", native_field="total_bytes",
        python_value=total_python, native_value=total_native,
        allowed=max(1.0, abs(total_python or 0) * _BYTE_TOLERANCE_FRACTION),
        note="MemTotal 不随时间变化", native_reason=memory_reason))

    available_python = python_memory.get("available_bytes") if _present(python_memory) else None
    available_native = native_memory.get("available_bytes") if memory_measured else None
    out.append(_row(
        "memory.available_bytes", role=None,
        python_field="available_bytes", native_field="available_bytes",
        python_value=available_python, native_value=available_native,
        allowed=max(1.0, abs(available_python or 0) * _BYTE_TOLERANCE_FRACTION),
        note=("MemAvailable 是会动的：两次读数相隔多久，差额就有多大，"
              "所以这一行是参考而非断言"),
        informational=True, native_reason=memory_reason))
    return out


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------

def _shadow_block(envelope, *, available: bool, reason) -> dict:
    measured = envelope if isinstance(envelope, dict) else {}
    sample = measured.get("sample") or {}
    return {
        "readings_available": available,
        "reason": reason,
        "observed_at": sample.get("observed_at") if available else None,
        "boot_id": sample.get("boot_id") if available else None,
        "measurements": (measured.get("measurements")
                         or {"state": "UNAVAILABLE", "reason": reason or "no envelope"}),
        "provenance": measured.get("provenance") or {},
        "attribution": measured.get("attribution"),
        "unknowns": list(measured.get("unknowns") or ()),
        "refusals": list(measured.get("refusals") or ()),
        "field_semantics": list(measured.get("field_semantics") or ()),
    }


def shadow(*, enabled, envelope=None, executable=None, receipts=None, key=None,
           python_reading=None, device_ref=None, now=None, within_seconds=None,
           timeout: int = 15, interval_ms: int = 500, filesystems=None,
           interfaces=None, max_output_bytes: int = 1_000_000) -> dict:
    """One shadow pass. Returns a document; never raises for a bad reading.

    ``envelope`` given -> it is used as-is and no child is started. Omitted ->
    PR#161's adapter runs the compiled collector under the tree's bound and the
    envelope it returns is used. Either way the document says which happened
    (``ran``), so a fixture is never passed off as a reading of this host.

    ``within_seconds`` is the caller's freshness window, for the same reason the
    adapter takes one: `runtime/graph.py` owns the window table and this seam
    declares none of its own. With no window the freshness is UNKNOWN with the
    reason, never FRESH.
    """
    on, why = feature_enabled(enabled=enabled)
    if not on:
        # Nothing is opened, started or written on the way to this answer.
        return {"schema": SHADOW_SCHEMA, "state": "DISABLED", "ran": False,
                "source": SOURCE, "reason": why, "observed_at": None,
                "freshness": None, "shadow": None, "parity": [],
                "contract": {"violations": [],
                             "device_entry_schema": DEVICE_ENTRY_SCHEMA},
                "device_ref": device_ref, "served_authority": "python-inventory"}

    ran = False
    if envelope is None:
        envelope = _adapter().collect(
            executable=executable, enabled=True, key=key, receipts=receipts,
            interval_ms=interval_ms, filesystems=filesystems, interfaces=interfaces,
            timeout=timeout, max_output_bytes=max_output_bytes,
            within_seconds=within_seconds)
        ran = True

    # The caller's clock is read AFTER the reading, not before. A clock taken at
    # the start ages a reading the child stamped later, and a negative age is a
    # reading reported as coming from the future — measured on this host at
    # -3.8 s, because the bound's spawn, receipt write and reap take longer than
    # the sampling window. PR#161's adapter resolves its clock at the same point
    # for the same reason.
    resolved_now = _resolve_now(now)

    violations = envelope_violations(envelope)
    available = (isinstance(envelope, dict)
                 and envelope.get("readings_available") is True)
    reason = None if available else _failure_reason(envelope)
    block = _shadow_block(envelope, available=available, reason=reason)

    if violations and available:
        # It claims a reading and breaks a rule the Fleet bridge enforces, so
        # nothing in it may be read as a fact about the host.
        reason = "; ".join(v["reason"] for v in violations)
        block.update({"readings_available": False, "reason": reason,
                      "observed_at": None, "boot_id": None,
                      "measurements": {"state": "UNAVAILABLE", "reason": reason}})
        state = "REFUSED"
    elif not available:
        # No reading was taken. The reason is the reading's own — NOT the
        # contract's, because a violation list here is a separate fact about the
        # SHAPE and says nothing about why the host could not be read. The two
        # are reported side by side rather than collapsed.
        state = "UNKNOWN"
    else:
        state = "SHADOW"

    return {
        "schema": SHADOW_SCHEMA,
        "state": state,
        "ran": ran,
        "source": SOURCE,
        "reason": block["reason"],
        "observed_at": block["observed_at"],
        "freshness": _graph().freshness_of(block["observed_at"], now=resolved_now,
                                           source=SOURCE,
                                           within_seconds=within_seconds),
        "shadow": block,
        "parity": parity(python_reading=python_reading, envelope=envelope),
        "contract": {"violations": violations,
                     "device_entry_schema": DEVICE_ENTRY_SCHEMA},
        "device_ref": device_ref,
        "served_authority": "python-inventory",
    }


def served(*, python_reading, document) -> dict:
    """What a caller is allowed to serve, from a pass and the authority.

    One function, and it is the whole of the precedence rule: the values are the
    Python inventory's, unchanged, and the native reading is attached BESIDE
    them under its own key. There is no code path from here to a served value
    that came from the collector.
    """
    document = document if isinstance(document, dict) else {}
    return {"authority": "python-inventory",
            "values": python_reading,
            "shadow": document.get("shadow"),
            "parity": list(document.get("parity") or ()),
            "freshness": document.get("freshness")}
