"""The native collector adapter: its bound, its translation, and its silence.

Three kinds of test live here and they are deliberately separable.

* The pure half exercises `translate()` against a committed document that came
  from the real binary (redacted), so shape compatibility is asserted without
  needing a compiler at test time.
* The process half runs a real child through
  `runtime/bounded_invocation.invoke_once` and checks every way it can fail:
  nonzero exit, a hang, bytes that are not JSON, an oversized stream, and a
  document whose schema this adapter does not read.
* Two architectural tests hold the property the whole design rests on — that
  this adapter adds no reachability of its own and is wired into nothing.

The clock is injected everywhere it can be. A test that read the wall clock
would pass or fail depending on when it ran.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ADAPTER = REPO / "native" / "node-core" / "adapter" / "native_node_core.py"
FIXTURE = (REPO / "native" / "node-core" / "adapter" / "fixtures"
           / "snapshot.v1.example.json")
FAKE = HERE / "fake_native_collector.py"

# The tree's convention for a runtime module: one module object tree-wide,
# loaded by path under a fixed name (tests/conftest.py:97 does the same).
_MODULE_NAME = "serveros_native_node_core_adapter"


def _adapter():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, ADAPTER)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]


def _fixture_document() -> dict:
    return json.loads(FIXTURE.read_text())


def _fake_executable(tmp_path: Path) -> Path:
    """A real executable wrapping the fake, which is how the other process
    fakes in this directory are reached (tests/test_codex_app_server_provider.py:67)."""
    wrapper = tmp_path / "fake-collector"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)
    return wrapper


def _receipts(tmp_path: Path) -> Path:
    """The private receipt directory. Idempotent, because one test may take two
    readings through it and a second `mkdir` would be an error rather than a
    no-op."""
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    return directory


def _collect_failure(tmp_path, mode, **overrides):
    """One failing collection, with the mode named rather than the reason."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = mode
    try:
        kwargs = dict(executable=_fake_executable(tmp_path), enabled=True,
                      key=f"case-{mode}", receipts=_receipts(tmp_path))
        kwargs.update(overrides)
        return _adapter().collect(**kwargs)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)


# ---------------------------------------------------------------- the bound

def test_a_relative_executable_is_refused():
    with pytest.raises(ValueError, match="absolute path"):
        _adapter().validated_executable("serveros-node-resource-snapshot")


def test_a_symlinked_executable_is_refused(tmp_path):
    real = tmp_path / "real"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        _adapter().validated_executable(link)


def test_a_missing_executable_is_refused(tmp_path):
    with pytest.raises(ValueError, match="regular file"):
        _adapter().validated_executable(tmp_path / "not-here")


def test_a_non_executable_file_is_refused(tmp_path):
    plain = tmp_path / "plain"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    with pytest.raises(ValueError, match="not executable"):
        _adapter().validated_executable(plain)


def test_a_valid_executable_is_accepted(tmp_path):
    assert _adapter().validated_executable(_fake_executable(tmp_path)).is_file()


def test_collect_without_enabled_refuses_and_runs_nothing(tmp_path):
    """The opt-in has no default. A caller that did not decide cannot measure."""
    with pytest.raises(ValueError, match="enabled=True"):
        _adapter().collect(executable=_fake_executable(tmp_path), enabled=False,
                           key="k", receipts=tmp_path / "never-made")
    # Nothing was written on the way to that refusal.
    assert not (tmp_path / "never-made").exists()


def test_collect_requires_a_positive_timeout_and_bound(tmp_path):
    adapter = _adapter()
    with pytest.raises(ValueError, match="timeout"):
        adapter.collect(executable=_fake_executable(tmp_path), enabled=True, key="k",
                        receipts=_receipts(tmp_path), timeout=0)
    with pytest.raises(ValueError, match="max_output_bytes"):
        adapter.collect(executable=_fake_executable(tmp_path), enabled=True, key="k",
                        receipts=_receipts(tmp_path), max_output_bytes=0)


def test_a_malformed_action_key_is_refused(tmp_path):
    with pytest.raises(ValueError, match="action key"):
        _adapter().collect(executable=_fake_executable(tmp_path), enabled=True,
                           key="not a key", receipts=_receipts(tmp_path))


def test_the_argv_is_a_list_and_names_no_shell(tmp_path):
    """A list argv is the structural reason there is no shell to escape.

    `runtime/bounded_invocation` hands this straight to `Popen`, so an argv that
    is a list cannot be re-split by anything.
    """
    argv = _adapter().collector_argv(_fake_executable(tmp_path), interval_ms=250,
                                     filesystems=["ROOT_FS=/", "DATA_FS=/data"],
                                     interfaces=["eth0", "lo"])
    assert isinstance(argv, list)
    assert all(isinstance(part, str) and part for part in argv)
    assert argv[1:] == ["--interval-ms", "250", "--filesystem", "ROOT_FS=/",
                        "--filesystem", "DATA_FS=/data", "--interface", "eth0",
                        "--interface", "lo", "--compact"]
    assert "shell" not in argv


def test_argv_validation_refuses_a_vague_role_or_interface(tmp_path):
    adapter = _adapter()
    executable = _fake_executable(tmp_path)
    with pytest.raises(ValueError, match="ROLE="):
        adapter.collector_argv(executable, filesystems=["root=/"])
    with pytest.raises(ValueError, match="ROLE="):
        adapter.collector_argv(executable, filesystems=["ROOT_FS=relative"])
    with pytest.raises(ValueError, match="interface name"):
        adapter.collector_argv(executable, interfaces=["eth0; rm -rf /"])
    with pytest.raises(ValueError, match="non-negative"):
        adapter.collector_argv(executable, interval_ms=-1)


# ------------------------------------------------------------- translation

def test_the_fixture_is_a_document_this_adapter_reads():
    document = _fixture_document()
    assert document["schema"] == _adapter().SNAPSHOT_SCHEMA
    assert document["collector"]["name"] == _adapter().COLLECTOR_NAME


def test_translate_carries_the_measurements_through_unchanged():
    """No field inside a measurement block is renamed: the collector's names were
    chosen to match this core's, and a rename here would be an interpretation."""
    document = _fixture_document()
    envelope = _adapter().translate(document, provenance={"status": "EXITED"})
    assert envelope["schema"] == _adapter().ENVELOPE_SCHEMA
    assert envelope["readings_available"] is True
    for block in ("cpu", "memory", "filesystems", "disk_io", "interfaces"):
        assert envelope["measurements"][block] == document[block]


def test_translate_keeps_an_unavailable_field_unavailable():
    """The one invariant. A nested UNAVAILABLE must not be flattened to a value."""
    document = _fixture_document()
    eth0 = [row for row in document["interfaces"] if row["name"] == "eth0"][0]
    assert eth0["speed_mbps"]["state"] == "UNAVAILABLE"      # the fixture is a host with virtio_net
    envelope = _adapter().translate(document, provenance={})
    carried = [row for row in envelope["measurements"]["interfaces"]
               if row["name"] == "eth0"][0]
    assert carried["speed_mbps"]["state"] == "UNAVAILABLE"
    assert carried["speed_mbps"]["reason"]
    assert any(u["field"] == "interfaces.eth0.speed_mbps" for u in envelope["unknowns"])


def test_translate_scopes_host_facts_and_account_facts_apart():
    envelope = _adapter().translate(_fixture_document(), provenance={})
    assert envelope["scope"] == "host-wide"
    # The attribution block is the only part about one account, and it says so.
    assert envelope["attribution"]["scope"] == "self"
    assert "attribution" not in envelope["measurements"]


def test_translate_refuses_to_lend_host_facts_to_one_account():
    """The refusals are the envelope's half of the honesty, and three of them are
    unconditional because the misreading is always available."""
    subjects = {row["subject"] for row in
                _adapter().translate(_fixture_document(), provenance={})["refusals"]}
    assert "host_wide_share" in subjects
    assert "interface_counter_aggregation" in subjects
    assert "interface_link_speed_as_wan_bandwidth" in subjects
    assert "filesystem_role_dedup" in subjects


def test_translate_names_each_interface_that_has_no_denominator():
    """eth0 is virtio_net: no speed, so its utilisation is unavailable. The
    envelope says which interface and why, rather than reporting 0%."""
    refusals = {row["subject"]: row["reason"] for row in
                _adapter().translate(_fixture_document(), provenance={})["refusals"]}
    reason = refusals["interfaces.eth0.link_utilisation"]
    assert "no denominator" in reason
    assert "no link speed" in reason


def test_the_free_bytes_collision_with_the_python_inventory_is_stated():
    """The Phase 2 shadow run found that the two producers spell "room left"
    differently: `runtime/inventory.py`'s role row `free_bytes` is df's Avail
    column (`f_bavail`), while the collector's `free_bytes` is `f_bfree`. A
    consumer joining them by name is off by the root-reserved blocks — 22 GiB on
    `/` on this host — so the envelope names the join that is right."""
    envelope = _adapter().translate(_fixture_document(), provenance={})
    rows = {row["field"]: row for row in envelope["field_semantics"]}
    collided = rows["measurements.filesystems.roles[].free_bytes"]
    assert "f_bfree" in collided["means"]
    assert "f_bavail" in collided["conflicts_with"]
    assert "available_bytes" in collided["join_on"]
    assert "f_bavail" in rows["measurements.filesystems.roles[].available_bytes"]["means"]


def test_translate_refuses_a_document_from_another_schema():
    document = _fixture_document()
    document["schema"] = "serveros/node-resource-snapshot.v2"
    with pytest.raises(ValueError, match="schema"):
        _adapter().translate(document, provenance={})


def test_translate_refuses_a_document_from_another_collector():
    document = _fixture_document()
    document["collector"]["name"] = "some-other-collector"
    with pytest.raises(ValueError, match="some-other-collector"):
        _adapter().translate(document, provenance={})


def test_translate_refuses_something_that_is_not_a_document():
    with pytest.raises(ValueError, match="parsed JSON object"):
        _adapter().translate(["not", "a", "document"], provenance={})


# --------------------------------------------------------------- freshness

def test_freshness_is_unknown_without_a_declared_window():
    """`runtime/graph.py` has no window for this source, and this adapter does
    not invent one: a window nobody declared is not a window."""
    envelope = _adapter().translate(_fixture_document(), provenance={})
    assert envelope["freshness"]["state"] == "UNKNOWN"
    assert envelope["freshness"]["within_seconds"] is None
    assert "no freshness window" in envelope["freshness"]["why"]
    # And the unknown-ness is in the list a reader scans, not only in the block.
    assert any(u["field"] == "freshness" for u in envelope["unknowns"])


def test_freshness_is_fresh_inside_a_declared_window_and_stale_after():
    document = _fixture_document()
    observed = datetime.fromisoformat(document["observed_at"])
    adapter = _adapter()
    fresh = adapter.translate(document, provenance={}, now=observed + timedelta(seconds=5),
                              within_seconds=60)
    assert fresh["freshness"]["state"] == "FRESH"
    assert fresh["freshness"]["age_seconds"] == 5.0
    assert not any(u["field"] == "freshness" for u in fresh["unknowns"])

    stale = adapter.translate(document, provenance={}, now=observed + timedelta(seconds=600),
                              within_seconds=60)
    assert stale["freshness"]["state"] == "STALE"
    assert stale["freshness"]["age_seconds"] == 600.0


def test_freshness_uses_this_processes_clock_when_none_is_injected(tmp_path):
    """`now=None` means "this reader's clock", not "no clock". Passing None
    through would report an unmeasurable age for a reason that is untrue."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "ok"
    try:
        envelope = _adapter().collect(
            executable=_fake_executable(tmp_path), enabled=True, key="clock",
            receipts=_receipts(tmp_path), within_seconds=86400)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    # The fixture's observed_at is in 2026; aged against a real clock it is
    # either FRESH within a day or STALE, but never UNKNOWN-for-want-of-a-clock.
    assert envelope["freshness"]["state"] in {"FRESH", "STALE"}
    assert envelope["freshness"]["age_seconds"] is not None


# ---------------------------------------------------------------- the child

def test_a_good_run_becomes_a_reading(tmp_path):
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "ok"
    try:
        envelope = _adapter().collect(
            executable=_fake_executable(tmp_path), enabled=True, key="ok",
            receipts=_receipts(tmp_path), timeout=20, within_seconds=86400)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    assert envelope["readings_available"] is True
    assert envelope["provenance"]["status"] == "EXITED"
    assert envelope["provenance"]["process_exit_code"] == 0
    assert envelope["provenance"]["direct_process_exited"] is True
    assert envelope["provenance"]["executed_now"] is True
    assert envelope["provenance"]["replayed"] is False
    assert envelope["provenance"]["stdout_bytes"] > 0
    # The committed fixture is a redacted copy of a real reading, so it says
    # live:true and the envelope repeats it rather than second-guessing it. What
    # matters is that the flag is carried, not what it says — the fixture's
    # corpus is the shape, and the flag is one more field in it.
    assert envelope["provenance"]["live"] is True
    assert envelope["measurements"]["cpu"]["state"] == "PRESENT"


def test_translate_carries_the_live_flag_both_ways():
    """`collector.live` is the one field that says whether a document is a
    reading of a host at all, so it must survive translation, not be normalised."""
    adapter = _adapter()
    document = _fixture_document()
    for claimed in (True, False):
        document["collector"]["live"] = claimed
        envelope = adapter.translate(document, provenance={})
        assert envelope["provenance"]["live"] is claimed


def test_a_nonzero_exit_is_a_failure_with_the_code_in_it(tmp_path):
    envelope = _collect_failure(tmp_path, "exit2", timeout=20)
    assert envelope["readings_available"] is False
    assert "exited 2" in envelope["measurements"]["reason"]
    assert envelope["provenance"]["status"] == "EXITED"   # it exited; it just failed


def test_a_hang_is_bounded_and_reported_as_a_timeout(tmp_path):
    envelope = _collect_failure(tmp_path, "hang", timeout=1)
    assert envelope["readings_available"] is False
    assert "TIMED_OUT" in envelope["measurements"]["reason"]
    assert envelope["provenance"]["status"] == "TIMED_OUT"
    # A timed-out child is not a completed one, and the envelope says so.
    assert envelope["sample"]["observed_at"] is None


def test_bytes_that_are_not_json_are_a_failure_not_a_zero(tmp_path):
    envelope = _collect_failure(tmp_path, "badjson", timeout=20)
    assert envelope["readings_available"] is False
    assert "not valid UTF-8 JSON" in envelope["measurements"]["reason"]
    assert envelope["provenance"]["status"] == "EXITED"


def test_a_plain_string_is_a_failure(tmp_path):
    envelope = _collect_failure(tmp_path, "notjson", timeout=20)
    assert envelope["readings_available"] is False
    assert "not valid UTF-8 JSON" in envelope["measurements"]["reason"]


def test_an_empty_stdout_is_a_failure(tmp_path):
    envelope = _collect_failure(tmp_path, "empty", timeout=20)
    assert envelope["readings_available"] is False


def test_a_schema_from_another_version_is_refused_by_the_adapter(tmp_path):
    """The child succeeds and its document is well-formed JSON. The refusal is
    this adapter declining to read a contract it does not implement."""
    envelope = _collect_failure(tmp_path, "wrong-schema", timeout=20)
    assert envelope["readings_available"] is False
    assert "schema" in envelope["measurements"]["reason"]
    assert envelope["provenance"]["status"] == "EXITED"


def test_a_document_from_another_collector_is_refused(tmp_path):
    envelope = _collect_failure(tmp_path, "wrong-collector", timeout=20)
    assert envelope["readings_available"] is False
    assert "some-other-collector" in envelope["measurements"]["reason"]


def test_the_output_bound_is_enforced_by_the_bound(tmp_path):
    """A bound small enough to be exceeded is hit by `invoke_once`, not by this
    module, and the envelope reports the bound's own status."""
    envelope = _collect_failure(tmp_path, "huge", timeout=20, max_output_bytes=1000)
    assert envelope["readings_available"] is False
    assert "OUTPUT_LIMIT" in envelope["measurements"]["reason"]


def test_a_failure_envelope_has_the_same_shape_as_a_success(tmp_path):
    """One shape for a caller to read, whether or not a reading came back."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "ok"
    try:
        good = _adapter().collect(executable=_fake_executable(tmp_path), enabled=True,
                                  key="shape-ok", receipts=_receipts(tmp_path), timeout=20)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    bad = _collect_failure(tmp_path, "exit2", timeout=20)
    assert set(good) == set(bad)
    assert set(good["sample"]) == set(bad["sample"])
    assert bool(good["readings_available"]) is True
    assert bool(bad["readings_available"]) is False
    # A failure states nothing about the host, so it carries no host refusals.
    assert [row["subject"] for row in bad["refusals"]] == ["no_reading"]


def test_a_replay_is_labelled_rather_than_passed_off_as_a_new_reading(tmp_path):
    """One reading versus a replay of a previous one is a caching decision, and
    this adapter does not make it — but it never hides which happened."""
    os.environ["SERVEROS_FAKE_NATIVE_MODE"] = "ok"
    try:
        adapter = _adapter()
        kwargs = dict(executable=_fake_executable(tmp_path), enabled=True,
                      key="replay-case", receipts=_receipts(tmp_path), timeout=20)
        first = adapter.collect(**kwargs)
        second = adapter.collect(**kwargs)
    finally:
        os.environ.pop("SERVEROS_FAKE_NATIVE_MODE", None)
    assert (first["provenance"]["executed_now"], first["provenance"]["replayed"]) == (True, False)
    assert (second["provenance"]["executed_now"], second["provenance"]["replayed"]) == (False, True)


# ------------------------------------------------------------ architecture

def test_the_adapter_reaches_the_collector_through_the_declared_chokepoint():
    """It must add no reachability of its own.

    `runtime/actuator-contract.yaml` declares every place this product can run a
    command, and `mutation_guard.py` checks both directions — a hit with no
    declaration is UNDECLARED_ACTUATOR and a declaration with no hit is
    STALE_ACTUATOR. `runtime/bounded_invocation.invoke_once` is the declared
    chokepoint; this adapter delegates to it and therefore must contain no exec
    call of its own. If it grew one, the guard would report a new unreviewed
    reachability, and declaring it here instead would not be an option — a
    declaration for a file the scanner finds no hit in is the stale direction.
    """
    tree = ast.parse(ADAPTER.read_text())
    exec_calls = []
    shell_kwargs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            dotted = []
            target = node.func
            while isinstance(target, ast.Attribute):
                dotted.insert(0, target.attr)
                target = target.value
            if isinstance(target, ast.Name):
                dotted.insert(0, target.id)
            name = ".".join(dotted)
            if name in {"subprocess.run", "subprocess.Popen", "subprocess.call",
                        "subprocess.check_output", "subprocess.check_call",
                        "os.system", "os.execv", "os.execvp", "os.spawnv"}:
                exec_calls.append((name, node.lineno))
            if any(kw.arg == "shell" for kw in node.keywords):
                shell_kwargs.append(node.lineno)
    assert exec_calls == [], f"the adapter must not run anything itself: {exec_calls}"
    assert shell_kwargs == [], f"no call may take a shell keyword: {shell_kwargs}"
    # The delegation is what makes that safe, so assert it is really there.
    text = ADAPTER.read_text()
    assert "bounded_invocation" in text and "invoke_once" in text


def test_the_adapter_is_wired_into_nothing():
    """The run-loop property, asserted rather than intended.

    Nothing under `runtime/`, `mcp/`, `context/`, `fleet/` or `abilities/` may
    mention this module: it is invoked, never imported, so it cannot enter
    serverosd's loader closure that tests/test_core_freeze.py asserts.
    """
    wanted = ("native_node_core", "native-node-core", "native/node-core")
    offenders = []
    for directory in ("runtime", "mcp", "context", "fleet", "abilities", "bin",
                      "scripts", "intake"):
        for path in (REPO / directory).rglob("*.py"):
            text = path.read_text(errors="replace")
            if any(name in text for name in wanted):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"the adapter must be invoked, not imported: {offenders}"


def test_the_adapter_declares_no_window_of_its_own():
    """`runtime/graph.py` owns the window table. A second one here would be a
    second opinion about when a measurement stops being current."""
    source = ADAPTER.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if {"host-filesystem", "host-memory"} & keys:
                pytest.fail("the adapter must not restate graph.py's window table")
