"""The Phase 3 shadow seam: the native envelope, off by default, wired into nothing.

What this file is testing, and what it is deliberately NOT testing.

PR#161 shipped the C++ collector and a bounded adapter that turns its document
into `serveros/native-node-measurement.v1`. It shipped with an architectural
test asserting the adapter is wired into nothing, because an adapter that can
measure the host must not be reachable from `serverosd`'s loader closure by
accident. PR#160 (another branch) turns that envelope into a Fleet monitor
device entry via `accept_native_measurement`.

The seam between them is what Phase 3 adds, and it is SHADOW-FIRST: the Python
inventory stays the only authority; a native reading may be attached beside it
to be compared with, and may never replace a value it did not verify. It is
also OFF BY DEFAULT, so the property PR#161 asserted survives — this module is
reached by an explicit caller, never by an import.

So these tests hold four things and each is a different kind of claim:

* the FLAG — disabled unless a caller says so, and a disabled pass runs no
  child and writes no file;
* the SEAM — one envelope, real or injected, becomes a shadow document whose
  values are marked non-authoritative and whose failures are UNKNOWN-with-a-
  reason rather than zero;
* the PRECEDENCE — the served values are the Python ones, unchanged;
* the CONTRACT — the envelope PR#160's bridge consumes, frozen as a document
  and checked against a fixture recorded read-only from PR#160's own branch,
  so the two sides can be reconciled when they meet without this branch
  importing, merging or editing that one.

The process half runs a real child through `tests/fake_native_collector.py`,
because what the seam does with a collector that exits 2 or hangs is the whole
of what it is for and is not a property of a mock. The clock is injected
everywhere it can be.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SHADOW = REPO / "native" / "node-core" / "shadow" / "native_shadow.py"
CONTRACT = (REPO / "native" / "node-core" / "shadow" / "contract"
            / "native-to-fleet-contract.v1.json")
PR160_ENVELOPE = (REPO / "native" / "node-core" / "shadow" / "fixtures" / "pr160"
                  / "native-node-measurement.fixture.json")
PR160_DEVICE = (REPO / "native" / "node-core" / "shadow" / "fixtures" / "pr160"
                / "fleet-monitor-device.fixture.json")
FAKE = HERE / "fake_native_collector.py"

_MODULE_NAME = "serveros_native_node_core_shadow"
DEVICE_REF = "device:tokyo-shared-dev-01"


def _shadow():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, SHADOW)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]


def _envelope() -> dict:
    return json.loads(PR160_ENVELOPE.read_text())


def _observed_at() -> str:
    return _envelope()["sample"]["observed_at"]


def _within() -> str:
    """A clock 5 s after the fixture's own reading — safely inside a 300 s window."""
    return (datetime.fromisoformat(_observed_at())
            + timedelta(seconds=5)).isoformat()


def _python_reading() -> dict:
    """The Python inventory's own shape, as `runtime/inventory.py` prints it.

    Deliberately the matching reading: the collector's `available_bytes` (f_bavail)
    and this row's `free_bytes` are the SAME number by construction (df's Avail
    column), while the collector's `free_bytes` (f_bfree) is the one that is 2 GB
    larger here. A parity that joins the wrong pair is therefore visible as a
    delta rather than as two identical values.
    """
    return {
        "filesystems": {"roles": [
            {"role": "ROOT_FS", "state": "PRESENT", "mount_point": "/",
             "total_bytes": 100_000_000_000, "free_bytes": 58_000_000_000,
             "used_percent": 40.8},
            {"role": "DATA_FS", "state": "PRESENT", "mount_point": "/data",
             "total_bytes": 100_000_000_000, "free_bytes": 58_000_000_000,
             "used_percent": 40.8},
        ]},
        "memory": {"state": "PRESENT", "total_bytes": 265_182_715_904,
                   "available_bytes": 181_079_306_240, "used_percent": 31.7},
    }


# ----------------------------------------------------------------- the flag

def test_the_flag_is_disabled_by_default():
    """A module that could measure the host by being imported would be the
    defect PR#161's architecture test exists to prevent."""
    module = _shadow()
    assert module.ENABLED_BY_DEFAULT is False
    enabled, reason = module.feature_enabled()
    assert enabled is False
    assert reason


def test_the_flag_enables_only_on_an_explicit_request():
    enabled, reason = _shadow().feature_enabled(enabled=True)
    assert enabled is True
    assert reason is None


def test_the_environment_can_ask_and_zero_is_not_yes():
    module = _shadow()
    assert module.feature_enabled(env={module.ENV_VAR: "1"})[0] is True
    assert module.feature_enabled(env={module.ENV_VAR: "true"})[0] is True
    assert module.feature_enabled(env={module.ENV_VAR: "0"})[0] is False
    assert module.feature_enabled(env={module.ENV_VAR: "off"})[0] is False
    assert module.feature_enabled(env={})[0] is False
    # An explicit refusal is not overridable by the environment.
    assert module.feature_enabled(enabled=False, env={module.ENV_VAR: "1"})[0] is False


def test_a_disabled_pass_runs_nothing_and_writes_nothing(tmp_path):
    receipts = tmp_path / "receipts"
    document = _shadow().shadow(enabled=False, executable=_fake_executable(tmp_path),
                                receipts=receipts, key="disabled-case",
                                python_reading=_python_reading())
    assert document["state"] == "DISABLED"
    assert document["ran"] is False
    assert document["shadow"] is None
    assert document["reason"]
    # Nothing was created on the way to that answer.
    assert not receipts.exists()


# ------------------------------------------------------------------ the seam

def test_the_shadow_names_its_source_and_its_clock():
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=_within(),
                                within_seconds=300, python_reading=_python_reading())
    assert document["schema"] == _shadow().SHADOW_SCHEMA
    assert document["state"] == "SHADOW"
    assert document["ran"] is False          # a mock envelope starts no child
    assert document["source"] == _shadow().SOURCE
    assert document["observed_at"] == _observed_at()
    assert document["freshness"]["state"] == "FRESH"
    assert document["freshness"]["within_seconds"] == 300


def test_an_injected_envelope_is_labelled_not_a_reading_of_this_host():
    """The fixture says live:false. Whether these facts are a reading of a host
    at all is not optional, and it is carried rather than normalised."""
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=_within(),
                                within_seconds=300)
    assert document["shadow"]["provenance"]["live"] is False


def test_the_shadow_reaches_the_collector_only_through_the_adapter(tmp_path):
    """A real child, through PR#161's adapter, through the tree's one bound."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "ok"
    try:
        document = _shadow().shadow(
            enabled=True, executable=_fake_executable(tmp_path), key="live-ok",
            receipts=_receipts(tmp_path), within_seconds=86400,
            python_reading=_python_reading())
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    assert document["state"] == "SHADOW"
    assert document["ran"] is True
    assert document["shadow"]["readings_available"] is True
    assert document["shadow"]["provenance"]["status"] == "EXITED"


def test_a_nonzero_child_is_a_failure_not_a_zero(tmp_path):
    document = _shadow_refusal(tmp_path, "exit2")
    assert document["shadow"]["readings_available"] is False
    assert "exited 2" in document["shadow"]["reason"]
    assert document["state"] == "UNKNOWN"


def test_a_hung_child_is_bounded_and_reported(tmp_path):
    document = _shadow_refusal(tmp_path, "hang", timeout=1)
    assert document["shadow"]["readings_available"] is False
    assert "TIMED_OUT" in document["shadow"]["reason"]
    assert document["state"] == "UNKNOWN"


def test_bytes_that_are_not_json_are_a_failure_not_a_zero(tmp_path):
    document = _shadow_refusal(tmp_path, "badjson")
    assert document["shadow"]["readings_available"] is False
    assert "not valid UTF-8 JSON" in document["shadow"]["reason"]


def test_a_failure_offers_no_numbers_and_keeps_its_reason(tmp_path):
    """The one rule: a fact that could not be measured is UNAVAILABLE with a
    reason. It is never 0 and never null."""
    document = _shadow_refusal(tmp_path, "exit2")
    assert document["shadow"]["measurements"]["state"] == "UNAVAILABLE"
    assert document["shadow"]["measurements"]["reason"] == document["shadow"]["reason"]
    # A failed reading has no time, because it has no reading.
    assert document["shadow"]["observed_at"] is None
    assert document["parity"][0]["verdict"] == "NOT_COMPARABLE"
    assert document["parity"][0]["native_value"] is None
    assert document["parity"][0]["reason"]


def test_an_unavailable_measurement_is_carried_not_flattened():
    envelope = _envelope()
    envelope["measurements"]["cpu"] = {"state": "UNAVAILABLE",
                                       "reason": "无法读取 /proc/stat"}
    document = _shadow().shadow(enabled=True, envelope=envelope, now=_within(),
                                within_seconds=300)
    assert document["shadow"]["measurements"]["cpu"]["state"] == "UNAVAILABLE"
    assert document["shadow"]["measurements"]["cpu"]["reason"] == "无法读取 /proc/stat"


def test_a_zero_length_window_is_not_an_idle_machine():
    """The collector refuses every delta when the two samples share an instant.
    The seam must carry that refusal rather than reading it as a quiet host."""
    envelope = _envelope()
    envelope["measurements"]["cpu"] = {"state": "UNAVAILABLE",
                                       "reason": "the sampling window did not advance"}
    document = _shadow().shadow(enabled=True, envelope=envelope, now=_within(),
                                within_seconds=300)
    assert document["shadow"]["measurements"]["cpu"]["state"] == "UNAVAILABLE"
    assert document["shadow"]["measurements"]["cpu"]["reason"] == \
        "the sampling window did not advance"


def test_the_seam_finds_that_a_failure_envelope_cannot_reach_the_fleet_bridge(tmp_path):
    """A SEAM FINDING, recorded rather than repaired.

    PR#161's `_failure_envelope` builds `provenance` from the invocation receipt
    and never adds `live`; only `translate()` sets it. PR#160's bridge checks
    `provenance.live` is a boolean BEFORE its own `readings_available is not True`
    branch, so the collector-failed envelope raises `NativeMeasurementRefused`
    instead of reaching the fail-closed device entry that branch exists to
    produce. Its own test misses this because it flips a SUCCESS envelope's
    `readings_available` rather than using one the adapter actually emits.

    Repairing it here would mean editing a file PR#161 owns or a file on another
    branch, so the seam reports it and the PR states it as a blocked piece.
    """
    document = _shadow_refusal(tmp_path, "exit2")
    assert document["state"] == "UNKNOWN"
    assert "exited 2" in document["shadow"]["reason"]
    assert "provenance.live" in {v["rule"] for v in document["contract"]["violations"]}


# -------------------------------------------------------------- freshness

def test_a_stale_envelope_is_stale_not_current():
    now = (datetime.fromisoformat(_observed_at())
           + timedelta(seconds=3600)).isoformat()
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=now,
                                within_seconds=300)
    assert document["freshness"]["state"] == "STALE"
    assert document["freshness"]["age_seconds"] == 3600.0


def test_an_undeclared_window_is_unknown_not_fresh():
    """§18's window belongs to `runtime/graph.py`. This seam declares none of
    its own, so with no window from the caller the answer is UNKNOWN."""
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=_within())
    assert document["freshness"]["state"] == "UNKNOWN"
    assert document["freshness"]["within_seconds"] is None
    assert document["freshness"]["why"]


def test_a_declared_window_is_read_from_the_graph_not_restated_here():
    source = SHADOW.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if {"host-filesystem", "host-memory"} & keys:
                pytest.fail("the seam must not restate graph.py's window table")


def test_the_clock_is_read_after_the_reading_not_before(tmp_path):
    """A clock read BEFORE the child runs ages a reading the child stamped
    afterwards, which is a reading reported as coming from the future —
    measured on this host at -3.8 s, because the bound's spawn, receipt write
    and reap outlast the sampling window.

    The committed fixture lives in 2026-01-01 and cannot show this: its age is
    positive whichever clock is used. So the child stamps its own (`live-now`).
    """
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "live-now"
    try:
        document = _shadow().shadow(enabled=True, executable=_fake_executable(tmp_path),
                                    key="clock-order", receipts=_receipts(tmp_path),
                                    within_seconds=300)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    assert document["state"] == "SHADOW"
    age = document["freshness"]["age_seconds"]
    assert age is not None and age >= 0, f"a reading cannot be from the future: {age}"
    assert age < 60, age
    assert document["freshness"]["state"] == "FRESH"


# -------------------------------------------------------------- precedence

def test_the_served_values_are_the_python_ones_unchanged():
    python_reading = _python_reading()
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=_within(),
                                within_seconds=300, python_reading=python_reading)
    served = _shadow().served(python_reading=python_reading, document=document)
    assert served["authority"] == "python-inventory"
    assert served["values"] == python_reading
    # The native numbers exist, and not at the served level.
    assert "measurements" not in served["values"]
    assert document["shadow"]["measurements"]["cpu"]["used_percent"] == 11.7


def test_a_native_reading_never_replaces_a_python_value():
    """Even when the native value is present and plausible. The shadow is a
    comparison, not a second producer."""
    python_reading = _python_reading()
    python_reading["filesystems"]["roles"][0]["used_percent"] = 41.0
    document = _shadow().shadow(enabled=True, envelope=_envelope(), now=_within(),
                                within_seconds=300, python_reading=python_reading)
    served = _shadow().served(python_reading=python_reading, document=document)
    assert served["values"]["filesystems"]["roles"][0]["used_percent"] == 41.0
    row = [r for r in document["parity"]
           if r["quantity"] == "filesystems.used_percent"
           and r["role"] == "ROOT_FS"][0]
    assert row["python_value"] == 41.0        # the authority's number
    assert row["native_value"] == 40.8        # the shadow's, reported and not served
    assert row["verdict"] == "DIFFERS"


# ----------------------------------------------------------------- parity

def test_parity_joins_python_free_bytes_to_native_available_bytes():
    """The one join that is right, and the one the Phase 2 shadow run found by
    watching two rows come back 22 GiB apart. `runtime/inventory.py`'s role row
    `free_bytes` is df's Avail column (f_bavail); the collector's
    `available_bytes` is the same number. Joining its `free_bytes` (f_bfree)
    instead is off by the root-reserved blocks."""
    rows = _shadow().parity(python_reading=_python_reading(), envelope=_envelope())
    room = [row for row in rows if row["quantity"] == "filesystems.room_left_bytes"
            and row["role"] == "ROOT_FS"][0]
    assert room["python_field"] == "free_bytes"
    assert room["native_field"] == "available_bytes"
    assert room["verdict"] == "EXACT"
    assert room["delta"] == 0
    assert "f_bavail" in room["note"]


def test_parity_never_joins_the_native_free_bytes_field():
    """A row naming `free_bytes` as the native side of the room-left quantity
    would be the 22 GiB bug, and it would look identical on filesystems that
    reserve nothing."""
    rows = _shadow().parity(python_reading=_python_reading(), envelope=_envelope())
    native_fields = {row["native_field"] for row in rows
                     if row["quantity"].endswith("room_left_bytes")}
    assert native_fields == {"available_bytes"}


def test_parity_is_exact_on_a_quantity_that_cannot_move():
    rows = _shadow().parity(python_reading=_python_reading(), envelope=_envelope())
    total = [row for row in rows if row["quantity"] == "filesystems.total_bytes"
             and row["role"] == "ROOT_FS"][0]
    assert total["verdict"] == "EXACT"
    memory = [row for row in rows if row["quantity"] == "memory.total_bytes"][0]
    assert memory["verdict"] == "EXACT"


def test_parity_reports_a_real_difference_as_a_difference():
    python_reading = _python_reading()
    python_reading["memory"]["total_bytes"] = 111_111_111_111
    rows = _shadow().parity(python_reading=python_reading, envelope=_envelope())
    memory = [row for row in rows if row["quantity"] == "memory.total_bytes"][0]
    assert memory["verdict"] == "DIFFERS"
    assert memory["delta"] == 265_182_715_904 - 111_111_111_111


def test_parity_is_not_comparable_when_a_side_did_not_answer():
    row = [r for r in _shadow().parity(python_reading={}, envelope=_envelope())
           if r["quantity"] == "filesystems.total_bytes"][0]
    assert row["verdict"] == "NOT_COMPARABLE"
    assert row["python_value"] is None
    assert row["reason"]


def test_parity_keeps_a_moving_gauge_informational():
    """MemAvailable churns between two instants by design. A byte-exact rule
    would call the two producers wrong on a host where both are right."""
    rows = _shadow().parity(python_reading=_python_reading(), envelope=_envelope())
    available = [row for row in rows if row["quantity"] == "memory.available_bytes"][0]
    assert available["verdict"] == "EXACT"
    assert available["informational"] is True


# ------------------------------------------------------------- the contract

def test_the_contract_names_the_two_schemas_and_the_bridge_it_is_frozen_against():
    contract = json.loads(CONTRACT.read_text())
    assert contract["schema"] == "serveros/native-to-fleet-contract.v1"
    assert contract["envelope_schema"] == "serveros/native-node-measurement.v1"
    assert contract["device_entry_schema"] == "serveros/fleet-monitor-device.v1"
    # The bridge lives on another branch; the contract is what this branch has
    # of it, and it says which file implements it.
    assert "accept_native_measurement" in contract["implemented_by"]


def test_the_recorded_pr160_envelope_conforms():
    assert _shadow().envelope_violations(_envelope()) == []


def test_the_recorded_pr160_device_entry_conforms():
    entry = json.loads(PR160_DEVICE.read_text())
    assert _shadow().device_entry_violations(entry) == []


def test_a_corrupted_envelope_is_refused_by_the_rule_that_caught_it():
    module = _shadow()
    cases = {
        "shape": ["not", "an", "object"],
        "schema": {**_envelope(), "schema": "serveros/whatever.v2"},
        "scope": {**_envelope(), "scope": "account"},
        "readings_available": {**_envelope(), "readings_available": "yes"},
        "attribution.scope": {**_envelope(), "attribution": {"scope": "user"}},
    }
    for expected, broken in cases.items():
        violations = module.envelope_violations(broken)
        assert expected in {v["rule"] for v in violations}, (expected, violations)


def test_the_live_predicate_must_be_a_boolean_not_absent():
    """Whether a document is a reading of a host or a fixture is not optional,
    so a missing `live` is a refusal rather than a default."""
    envelope = _envelope()
    del envelope["provenance"]["live"]
    violations = _shadow().envelope_violations(envelope)
    assert "provenance.live" in {v["rule"] for v in violations}


def test_the_attribution_rule_only_bites_when_there_is_a_reading():
    """A failed envelope carries an UNAVAILABLE attribution and never a scope;
    holding it to the self-scope rule would refuse the very shape the failure
    path is required to produce."""
    envelope = _envelope()
    envelope["readings_available"] = False
    envelope["measurements"] = {"state": "UNAVAILABLE", "reason": "collector 退出 2"}
    envelope["attribution"] = {"state": "UNAVAILABLE", "scope": "self"}
    assert _shadow().envelope_violations(envelope) == []


def test_the_shadow_document_reports_contract_violations_rather_than_raising():
    """A shadow that raised on a bad envelope would be a second gate on a path
    that is already gated. It records and returns."""
    envelope = _envelope()
    envelope["scope"] = "account"
    document = _shadow().shadow(enabled=True, envelope=envelope, now=_within(),
                                within_seconds=300)
    assert document["contract"]["violations"]
    assert document["state"] == "REFUSED"
    assert document["shadow"]["readings_available"] is False


# ------------------------------------------------------------ architecture

def test_the_seam_runs_nothing_itself():
    """It must add no reachability of its own. The tree's executor is
    `runtime/bounded_invocation.invoke_once`, reached through PR#161's adapter;
    this module supplies a caller and reads a document, and if it grew an exec
    call `mutation_guard.py` would report a new unreviewed actuator."""
    tree = ast.parse(SHADOW.read_text())
    exec_calls, shell_kwargs = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted, target = [], node.func
            while isinstance(target, ast.Attribute):
                dotted.insert(0, target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                dotted.insert(0, target.id)
            if ".".join(dotted) in {"subprocess.run", "subprocess.Popen", "subprocess.call",
                                    "subprocess.check_output", "subprocess.check_call",
                                    "os.system", "os.execv", "os.execvp", "os.spawnv"}:
                exec_calls.append(((".".join(dotted)), node.lineno))
            if any(kw.arg == "shell" for kw in node.keywords):
                shell_kwargs.append(node.lineno)
    assert exec_calls == [], f"the seam must not run anything itself: {exec_calls}"
    assert shell_kwargs == [], f"no call may take a shell keyword: {shell_kwargs}"
    text = SHADOW.read_text()
    assert "native_node_core" in text and "collect(" in text


def test_the_seam_is_wired_into_nothing_by_a_default():
    """PR#161's property, asserted again for the new file: nothing under
    `runtime/`, `mcp/`, `context/`, `fleet/`, `abilities/`, `bin/`, `scripts/`
    or `intake/` may mention it, so it cannot enter serverosd's loader closure
    that `tests/test_core_freeze.py` asserts."""
    wanted = ("native_shadow", "native-node-core-shadow", "native/node-core/shadow")
    offenders = []
    for directory in ("runtime", "mcp", "context", "fleet", "abilities", "bin",
                      "scripts", "intake"):
        for path in (REPO / directory).rglob("*.py"):
            text = path.read_text(errors="replace")
            if any(name in text for name in wanted):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"the seam must be invoked, not imported: {offenders}"


# ------------------------------------------------------------------ helpers

def _fake_executable(tmp_path: Path) -> Path:
    """A real executable wrapping the fake collector, reached the same way the
    adapter's own tests reach it."""
    wrapper = tmp_path / "fake-collector"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def _receipts(tmp_path: Path) -> Path:
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return directory


def _shadow_refusal(tmp_path, mode, **overrides):
    """One failing collection, with the fake collector's mode named."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = mode
    try:
        kwargs = dict(enabled=True, executable=_fake_executable(tmp_path),
                      key=f"case-{mode}", receipts=_receipts(tmp_path),
                      python_reading=_python_reading())
        kwargs.update(overrides)
        return _shadow().shadow(**kwargs)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
