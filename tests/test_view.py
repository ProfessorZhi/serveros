#!/usr/bin/env python3
"""Tests for the shared answer layer, and for the three surfaces over it.

Why this file exists, and what it is actually guarding
------------------------------------------------------
`serverosctl`, `serverosd` and the MCP adapter all answer the same questions.
The failure this file is written against is not "the function returns the wrong
number" — it is the class of defect that shipped three times in one session and
looked like something else each time:

  * A `Registry` object passed where a container-attribution index dict was
    expected did not raise a type error. It raised `UnknownResource` from
    inside the container module, which the caller caught and reported as
    "Docker could not be asked". The report blamed a component that was
    working, and the mistake was invisible because the failure looked like an
    outage. There is now a type check, and a test that it refuses by name.

  * `error_windows` was supplied only by tests. The detector's
    highest-confidence combination needs a third signal that no production
    caller could produce, so the combination was UNREACHABLE — and an
    unreachable rule reports exactly what a satisfied one does, which is
    nothing. The live host's 403%-CPU orphaned agent runtime classified as
    `SIGNALS_PRESENT`, which reads as "no staleness found".

  * Attributed containers with NO registry in hand come back `unknown_owner`,
    and `unknown_owner` is what makes a reclaim candidate. A PROTECTED
    container would be reported that way, because the document saying it is
    protected is the thing that was missing. The detector is now not fed an
    input whose ownership cannot be established, and says so.

So the assertions here are mostly about what the layer REFUSES to answer, and
about whether an absence is reported as an absence. A test that only checked
the happy path would have passed on all three of those builds.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


view = _load("serveros_runtime_view", "view.py")
registry = _load("serveros_runtime_registry", "registry.py")
attribution = _load("serveros_runtime_attribution", "attribution.py")
infra = _load("serveros_runtime_infrastructure", "infrastructure.py")
cli = _load("serveros_cli", "cli.py")
daemon = _load("serveros_runtime_daemon", "daemon.py")
mcp = _load("serveros_node_mcp", Path("..") / "mcp/node_mcp.py")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def stat_line(*, pid, comm, state="S", ppid=1, utime=0, stime=0, num_threads=1,
              starttime=0, rss_pages=0):
    values = {n: 0 for n in range(3, 25)}
    values.update({3: state, 4: ppid, 14: utime, 15: stime, 18: 20,
                   20: num_threads, 22: starttime, 24: rss_pages})
    return f"{pid} ({comm}) " + " ".join(str(values[n]) for n in range(3, 25)) + "\n"


def status_line(uid=1006, name="svc"):
    return f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"


class FakeProcHost:
    """A synthetic /proc, with the four methods `readable_host` requires."""

    name = "fake-proc-host"

    def __init__(self, files=None, links=None, uptime=1000.0, proc_entries=None):
        self.files = files or {}
        self.links = links or {}
        self._uptime = uptime
        self._proc = proc_entries

    def read_text(self, path):
        return self.files.get(path)

    def read_bytes(self, path):
        value = self.files.get(path)
        return value.encode() if isinstance(value, str) else None

    def listdir(self, path):
        if path != "/proc":
            return []
        if self._proc is not None:
            return list(self._proc)
        # Derived from the files, so a fixture that only declares a process's
        # /proc/<pid>/stat is still a process the host can enumerate. The first
        # version of this fake returned [] unless a separate list was passed,
        # which made every group empty and every finding vacuous — the tests
        # read as "the node has nothing running", which is a claim, not a
        # failure.
        seen: dict[str, None] = {}
        for entry in self.files:
            parts = entry.split("/")
            if len(parts) > 2 and parts[1] == "proc" and parts[2].isdigit():
                seen[parts[2]] = None
        return list(seen)

    def readlink(self, path):
        return self.links.get(path)

    def run(self, args, timeout=8.0):
        return 127, "", "not found"

    def uptime(self):
        return self._uptime

    def page_size(self):
        return 4096

    def uid_name(self, uid):
        return f"uid{uid}"


class FakeProbeHost:
    """The other `LiveHost`: journal, docker, clock. Never a process table."""

    name = "fake-probe-host"

    def __init__(self, journal=None, containers=None, now=1000.0):
        self.journal_lines = journal or {}
        self.containers = containers or {}
        self._now = now

    def journal(self, *, unit=None, since, grep=None):
        return list(self.journal_lines.get(unit, []))

    def now(self):
        return self._now

    def docker_ps(self, all_=True):
        return [{"Names": name, "ID": name} for name in self.containers]

    def docker_inspect(self, name):
        return self.containers[name]


def proc(pid, *, ppid=1, comm="prog", cgroup=None, argv=None, cpu_ticks=0,
         rss_pages=0, uid=1006):
    """Files for one synthetic process."""
    files = {
        f"/proc/{pid}/stat": stat_line(pid=pid, comm=comm, ppid=ppid,
                                       utime=cpu_ticks, stime=0, rss_pages=rss_pages),
        f"/proc/{pid}/cmdline": "\0".join(argv or [f"/usr/bin/{comm}"]) + "\0",
        f"/proc/{pid}/cgroup": f"0::/{cgroup}\n" if cgroup else "",
        f"/proc/{pid}/status": status_line(uid=uid, name=comm),
    }
    return files


def host_of(*processes, uptime=1000.0):
    files, links = {}, {}
    for pid, kwargs in processes:
        files.update(proc(pid, **kwargs))
        links[f"/proc/{pid}/cwd"] = "/home/hwz"
    files["/proc/mounts"] = "proc /proc proc rw,hidepid=invisible 0 0\n"
    return FakeProcHost(files=files, links=links, uptime=uptime)


def resource(rid="res-r", *, kind="process", lifecycle="persistent",
             protection="normal", criticality=None, probes=None):
    record = {
        "id": rid,
        "kind": kind,
        "owner": {"type": "project", "ref": "project:demo"},
        "scope": "user",
        "lifecycle": {"class": lifecycle},
        "desired": {"state": "running"},
        "protection": {"level": protection},
    }
    if criticality:
        record["criticality"] = criticality
    if probes is not None:
        record["probes"] = probes
    return record


def registry_of(*records):
    return registry.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": list(records),
    })


def group(ref="runtime-group:process-tree:demo.service", scope=None, members=None):
    scope = scope or "/user.slice/demo.service"
    return {
        "ref": ref, "id": "demo.service", "kind": "process-tree",
        "scope": scope, "root_pid": 100, "members": members or [100],
        "member_roles": {"100": "process"}, "session_ref": None,
        "container_ref": None,
        "aggregate": {"process_count": 1, "cpu_percent": 0.0,
                      "memory_rss_bytes": 0, "age_seconds": 10.0},
        "agent": {"value": None, "status": attribution.UNKNOWN, "why": "no rule"},
        "tree": {"root": 100, "root_ppid": 1, "reparented_to_init": True,
                 "chain_complete": False, "tree": {"pid": 100, "comm": "prog",
                                                   "state": "S", "children": []}},
    }


# ---------------------------------------------------------------------------
# The envelope — which measurement am I reading
# ---------------------------------------------------------------------------


def test_every_answer_says_who_produced_it_and_when():
    """The CLI and the daemon both answer these. They are different moments."""
    payload = view.runtime_groups(host=host_of((100, {"comm": "prog"})))
    assert payload["answered_by"] == view.DEFAULT_MEASURER
    assert payload["measured_at"]


def test_the_daemon_labels_its_answers_as_its_own():
    payload = view.runtime_groups(host=host_of((100, {"comm": "prog"})),
                                  answered_by="serverosd")
    assert payload["answered_by"] == "serverosd"


# ---------------------------------------------------------------------------
# §14 · process top
# ---------------------------------------------------------------------------


def test_top_sorts_by_cpu_and_reports_the_metric_it_used():
    host = host_of((100, {"comm": "idle"}), (200, {"comm": "busy", "cpu_ticks": 500}))
    payload = view.process_top(host=host, limit=10)
    assert [row["comm"] for row in payload["rows"]] == ["busy", "idle"]
    assert payload["rows"][0]["cpu_metric"] == "lifetime_average_percent_of_one_core"


def test_top_by_memory_uses_memory_not_cpu():
    host = host_of((100, {"comm": "hungry", "rss_pages": 1000}),
                   (200, {"comm": "busy", "cpu_ticks": 500}))
    payload = view.process_top(host=host, limit=10, by="mem")
    assert payload["rows"][0]["comm"] == "hungry"


def test_top_respects_the_limit_without_dropping_the_coverage():
    host = host_of(*[(pid, {"comm": f"p{pid}"}) for pid in range(100, 110)])
    payload = view.process_top(host=host, limit=3)
    assert payload["count"] == 3
    # The limit bounds the ROWS, never the coverage. A truncated list that
    # reported coverage equal to its own length would say "there are three
    # processes on this host".
    assert payload["coverage"]["visible_processes"] == 10


def test_top_refuses_a_sort_it_does_not_implement():
    with pytest.raises(ValueError, match="cpu"):
        view.process_top(host=host_of((100, {})), by="threads")


def test_every_row_carries_what_it_belongs_to():
    host = host_of((100, {"comm": "codex", "cgroup": "user.slice/session-7.scope/x"}))
    row = view.process_top(host=host, limit=1)["rows"][0]
    assert row["session_ref"] == "session:7"
    assert row["group_ref"]


# ---------------------------------------------------------------------------
# §3/§5 · groups, groups by member pid, and the chain
# ---------------------------------------------------------------------------


def test_a_group_can_be_found_by_any_member_pid_not_only_the_root():
    """`runtime tree <pid>` is what an operator types while looking at `ps`.

    `graph.find_group` matches ref, id and root pid. A member pid that is not
    the root is the case a reader is most likely to have in hand, and
    answering "no such group" for it would be a riddle rather than a refusal.
    """
    host = host_of((100, {"comm": "root", "cgroup": "user.slice/demo.service"}),
                   (101, {"comm": "child", "ppid": 100, "cgroup": "user.slice/demo.service"}))
    payload = view.runtime_tree("101", host=host)
    assert payload["group"]["root_pid"] == 100
    assert 101 in payload["group"]["members"]


def test_an_unknown_group_ref_is_refused_by_name():
    host = host_of((100, {"comm": "root"}))
    with pytest.raises(view.UnknownSubject) as excinfo:
        view.runtime_group("no-such-group", host=host)
    assert "no runtime group matches" in str(excinfo.value)


def test_the_chain_lists_every_hop_it_could_not_prove_with_its_reason():
    """§4. The Task hop is UNKNOWN on this host, and that is the answer.

    Asserted as a list with reasons rather than as a boolean, because "there
    is an unknown hop" is useful and "which one, and why" is what makes it
    actionable.
    """
    host = host_of((100, {"comm": "codex", "cgroup": "user.slice/session-7.scope"}))
    payload = view.runtime_tree(
        view.runtime_groups(host=host)["groups"][0]["ref"], host=host)
    links = {entry["link"]: entry["why"] for entry in payload["unknown_links"]}
    assert "task" in links
    assert "no task identifier" in links["task"]


def test_chain_rows_are_in_graph_order_not_alphabetical():
    chain = {
        "container": {"value": None, "status": attribution.FACT},
        "device": {"value": "ip:10.0.0.1", "status": attribution.INFERRED},
        "agent": {"value": "codex", "status": attribution.INFERRED},
        "task": {"value": None, "status": attribution.UNKNOWN},
    }
    assert [name for name, _, _ in view.chain_rows(chain)] == [
        "device", "agent", "task", "container"]


# ---------------------------------------------------------------------------
# §14 · process trace
# ---------------------------------------------------------------------------


def test_tracing_an_unreadable_pid_answers_rather_than_raising():
    """`attribute_process` returning UNKNOWN IS the answer to "what is pid N".

    It is not an error: on a host with hidepid=invisible, another user's
    process is expected. The record says `readable: false` so a surface can
    choose its exit code instead of having the choice made for it.
    """
    payload = view.process_trace(999999, host=host_of((100, {"comm": "prog"})))
    assert payload["readable"] is False
    assert "not readable" in payload["process"]["why"]


def test_a_trace_carries_the_lineage_its_group_and_its_scope():
    host = host_of((100, {"comm": "root", "cgroup": "user.slice/session-7.scope"}),
                   (101, {"comm": "child", "ppid": 100,
                          "cgroup": "user.slice/session-7.scope"}))
    payload = view.process_trace(101, host=host)
    assert payload["readable"] is True
    # The lineage continues to pid 1, which is not in the table: the walk stops
    # at the visibility boundary, not at the last process this uid can explain.
    assert [entry["pid"] for entry in payload["ancestry"]] == [101, 100, 1]
    assert payload["ancestry"][0]["is_self"] is True
    assert payload["process"]["session_ref"] == "session:7"
    assert payload["group"]["root_pid"] == 100


def test_a_trace_does_not_infer_a_task_from_the_command_line():
    """§1/§4. A UUID in argv is a UUID in argv."""
    host = host_of((100, {"comm": "codex",
                          "argv": ["codex", "run", "01a08f92-b7ce-7b00-a4f2-8074ec6752bc"]}))
    payload = view.process_trace(100, host=host)
    assert payload["process"]["origin"]["task_ref"] is None
    note = payload["process"]["attribution"]["note"]
    assert "NOT inferred here" in note
    assert "a command line is evidence rather than an owner" in note


# ---------------------------------------------------------------------------
# The two LiveHost classes, and the bug that hid behind them
# ---------------------------------------------------------------------------


def test_a_host_without_a_process_table_is_refused_with_a_named_reason():
    with pytest.raises(TypeError, match="attribution.LiveHost"):
        view.proc_host(FakeProbeHost())


def test_the_guard_is_the_same_one_infrastructure_uses():
    """A second copy of a capability check is how one copy goes stale."""
    assert view.proc_host is not infra.readable_host
    assert view.proc_host(None).__class__ is attribution.LiveHost


def test_the_attribution_index_refuses_a_registry_with_no_document_path():
    """The defect that made a type mistake look like a Docker outage.

    A `Registry` built from an in-memory document has no file to re-read. The
    previous code passed it to the container module anyway, and the resulting
    `UnknownResource` was caught by the caller and reported as "Docker could
    not be asked" — a lie about which component failed.
    """
    reg = registry_of(resource())
    with pytest.raises(TypeError, match="carries no document path"):
        view.attribution_index(reg)


def test_the_attribution_index_accepts_a_dict_or_a_path_or_none():
    assert view.attribution_index(None).get("registry_present") is False
    assert view.attribution_index({"registry_present": True})["registry_present"] is True


def test_the_attribution_index_reads_the_registrys_own_path(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": [resource()],
    }))
    reg = registry.Registry(path=path)
    assert reg.path == path
    assert view.attribution_index(reg)["registry_present"] is True


# ---------------------------------------------------------------------------
# §10 · the detector is not fed an input whose ownership is unknown
# ---------------------------------------------------------------------------


def test_containers_are_not_attributed_without_a_registry():
    """A PROTECTED container must not become a reclaim candidate.

    With no registry, every container comes back `unknown_owner`, and
    `unknown_owner` is what makes STOPPED_RECLAIM_CANDIDATE fire. The document
    that says a container is protected is exactly the document that is
    missing, so the safe answer is to evaluate nothing and say so.
    """
    containers = {"c1": {"Name": "c1", "State": {"Status": "exited",
                                                 "ExitCode": 0},
                         "Config": {"Labels": {}}}}
    payload = view.anomaly_list(host=host_of((100, {"comm": "prog"})),
                                docker_host=FakeProbeHost(containers=containers))
    codes = {signal["code"] for signal in payload["signals"]}
    assert "STOPPED_RECLAIM_CANDIDATE" not in codes
    reasons = {entry["input"]: entry["why"] for entry in payload["unchecked"]}
    assert "registry is absent" in reasons["containers"]


def test_a_docker_failure_is_reported_as_a_component_that_could_not_be_asked():
    class Broken(FakeProbeHost):
        def docker_ps(self, all_=True):
            raise OSError("docker daemon is not running")

    payload = view.anomaly_list(host=host_of((100, {"comm": "prog"})),
                                docker_host=Broken())
    reasons = {entry["input"]: entry["why"] for entry in payload["unchecked"]}
    assert "could not be asked" in reasons["containers"]


# ---------------------------------------------------------------------------
# §10 · error windows come from a declaration or from nowhere
# ---------------------------------------------------------------------------


def journal_resource(unit="demo.service", count=85):
    return resource("res-r", probes=[{
        "id": "p-log", "kind": "journal-diagnostic", "axis": "reason",
        "unit": unit, "since": "-30 min",
        # The reason has to be one `health.REASONS` declares. A made-up one is
        # refused by the Observation and the evidence comes back `{"type":
        # "none"}` — which this test caught, and which is the vocabulary doing
        # its job: a probe cannot invent a finding by naming it.
        "patterns": [{"match": "failed to refresh", "reason": "HEALTHCHECK_FAILING"}],
    }])


def test_a_declared_journal_source_becomes_an_error_window():
    reg = registry_of(journal_resource())
    probe_host = FakeProbeHost(journal={"demo.service": ["failed to refresh"] * 85})
    windows, coverage = view._error_windows(
        [group(scope="/user.slice/demo.service")], reg, probes_host=probe_host)
    assert windows["res-r"]["count"] == 85
    assert windows["runtime-group:process-tree:demo.service"]["count"] == 85
    assert coverage["subjects_with_a_source"] == 1


def test_an_unreadable_journal_is_not_a_count_of_zero():
    class Unreadable(FakeProbeHost):
        def journal(self, *, unit=None, since, grep=None):
            from importlib import import_module

            probes = import_module("serveros_runtime_probes")
            raise probes.ProbeUnavailable("journalctl is not installed")

    reg = registry_of(journal_resource())
    windows, _ = view._error_windows([group()], reg, probes_host=Unreadable())
    assert windows["res-r"]["count"] is None


def test_a_subject_with_no_declared_source_is_counted_as_a_blind_spot():
    """The gap is per-subject, not global.

    One declared journal source makes the whole `error_windows` input
    non-empty while every other group still cannot reach
    REPEATED_ERROR_LOOP, and the difference would be invisible.
    """
    reg = registry_of(journal_resource())
    probe_host = FakeProbeHost(journal={"demo.service": ["failed to refresh"]})
    payload = view.anomaly_list(
        host=host_of((100, {"comm": "codex", "cgroup": "user.slice/session-7.scope"})),
        registry=reg, docker_host=FakeProbeHost(), probes_host=probe_host)
    entry = next(item for item in payload["unchecked"]
                 if item["input"] == "error_windows")
    assert "UNREACHABLE rather than unsatisfied" in entry["why"]


def test_error_windows_declare_which_probe_produced_them():
    reg = registry_of(journal_resource())
    probe_host = FakeProbeHost(journal={"demo.service": ["failed to refresh"] * 3})
    windows, coverage = view._error_windows([group()], reg, probes_host=probe_host)
    assert windows["res-r"]["source"] == "journal-diagnostic:p-log"
    assert coverage["sources"][0]["pattern"] == "failed to refresh"


# ---------------------------------------------------------------------------
# §6/§10 · subjects resolve to their registrations
# ---------------------------------------------------------------------------


def test_a_registered_container_subject_is_reachable_from_the_detector():
    """A protected container that IS registered must not emit reclaim signals.

    This is the other half of the registry-absent test: with the document in
    hand, the disposition is PROTECTED and the detector emits nothing for it
    at all, because the guard lives in the detector rather than in a filter
    the caller has to remember.
    """
    reg = registry_of(resource("res-c", kind="container", protection="protected",
                               probes=[{"id": "p-c", "kind": "container-state",
                                        "axis": "liveness", "container": "litellm"}]))
    subjects = view._subject_registry(reg)
    entry = subjects["runtime:container:litellm"]
    assert entry["protection"] == "protected"
    assert entry["resource_id"] == "res-c"


def test_a_subject_that_is_not_registered_is_absent_rather_than_defaulted():
    assert view._subject_registry(registry_of(resource("res-r"))) == {
        "res-r": {"resource_id": "res-r", "criticality": "important",
                  "protection": "normal", "kind": "process"}}


def test_probe_results_carry_the_declared_kind_so_the_mcp_rule_can_fire():
    """MCP_PROCESS_ALIVE_BUT_UNAVAILABLE compared the literal "mcp".

    No kind in the contract's own list can equal `mcp`, so the branch was
    unreachable and reported nothing — which reads exactly like a rule that
    found nothing.
    """
    reg = registry_of(resource("res-m", kind="mcp-server", probes=[]))
    results = view._probe_results(reg, host=FakeProbeHost())
    assert results["res-m"]["kind"] == "mcp-server"
    assert "mcp-server" in view.anomaly.MCP_KINDS


def test_the_derived_state_survives_the_shim_that_renders_probe_results():
    """§15's comparison reads the DERIVED state, and the shim used to drop it.

    `Health.show()` carries `state` — the health model's classification, made
    with the registration's desired state in hand. `_probe_results` kept only
    the four axis WORDS and discarded it, so `desired.comparison` found
    `derived is None`, took its "no derived state to read" branch, and returned
    UNCHECKED whose `why` blamed a gap in the declaration.

    The failure was silent in the direction that matters: three live
    registrations declared `running` were measured STOPPED on 2026-09-14 and
    `desired_actual()["mismatches"]` was `[]` — which reads as "every
    registration is in its desired state".

    Both halves are asserted, because either alone passes in the broken world:
    the derived state reaches the shim, AND the comparison it feeds produces
    the mismatch rather than the UNCHECKED.
    """
    reg = registry_of(resource("res-down", kind="container", probes=[
        {"id": "state", "kind": "container-state", "axis": "liveness",
         "container": "res-down"},
    ]))
    results = view._probe_results(
        reg, host=FakeProbeHost(containers={"res-down": {"State": {"Status": "exited"}}}))
    assert results["res-down"]["derived_state"] == "NOT_RUNNING"
    assert results["res-down"]["liveness"] == "STOPPED"
    # The provider's own word rides along, which is what makes `actual_raw`
    # readable on the comparison row.
    assert results["res-down"]["evidence"]["container_status"] == "exited"

    document = view.desired_actual(registry_doc=reg, measured=results)
    assert [row["resource_id"] for row in document["mismatches"]] == ["res-down"]
    assert document["mismatches"][0]["actual_derived_state"] == "NOT_RUNNING"
    assert document["mismatches"][0]["actual_raw"] == "exited"


def test_a_measurement_that_is_running_still_compares_as_in_desired_state():
    """The strict direction: the fix above must not turn every resource red.

    §15's rule is about comparing a declaration against a measurement, and a
    resource that IS in its declared state has to keep reporting so. A test
    that only asserted the mismatch would pass on a comparator that reported
    everything as one.
    """
    reg = registry_of(resource("res-up", kind="container", probes=[
        {"id": "state", "kind": "container-state", "axis": "liveness",
         "container": "res-up"},
    ]))
    results = view._probe_results(
        reg, host=FakeProbeHost(containers={"res-up": {"State": {"Status": "running"}}}))
    document = view.desired_actual(registry_doc=reg, measured=results)
    assert [row["verdict"] for row in document["comparisons"]] == ["IN_DESIRED_STATE"]
    assert document["mismatches"] == []


# ---------------------------------------------------------------------------
# §10/§11 · explain carries the negative half
# ---------------------------------------------------------------------------


def test_explain_names_the_combination_that_did_not_fire_and_what_it_lacked():
    host = host_of((100, {"comm": "codex", "ppid": 1, "cpu_ticks": 4000,
                          "cgroup": "user.slice/session-7.scope"}), uptime=200000.0)
    ref = view.runtime_groups(host=host)["groups"][0]["ref"]
    payload = view.anomaly_explain(ref, host=host, docker_host=FakeProbeHost())
    assert payload["found"] is True
    unmet = {entry["combination"]: entry["missing"]
             for entry in payload["combinations_not_met"]}
    assert "REPEATED_ERROR_LOOP" in unmet["STALE_CANDIDATE"]
    # The high-confidence combination needs a task that closed, and this host
    # cannot establish a task at all. That single missing link is why a group
    # burning 403% of a core is not classified as stale — and the reason is
    # printed rather than left as an absence.
    assert unmet["STALE_AGENT_RUNTIME_HIGH_CONFIDENCE"] == ["TASK_CLOSED_RESOURCE_RUNNING"]


def test_explain_of_an_unknown_subject_lists_the_subjects_that_did_have_signals():
    host = host_of((100, {"comm": "codex", "cpu_ticks": 4000,
                          "cgroup": "user.slice/session-7.scope"}), uptime=200000.0)
    payload = view.anomaly_explain("no-such-subject", host=host,
                                   docker_host=FakeProbeHost())
    assert payload["found"] is False
    assert payload["subjects_with_signals"]
    assert "different answers" in payload["because"]


def test_a_single_signal_never_classifies_and_explain_says_so():
    payload = view.anomaly.classify(
        [view.anomaly.signal("HIGH_CPU_SUSTAINED", "s", {"cpu_percent": 300},
                             window="60s", why="test")],
        subject="s")
    assert payload["classification"] == "SIGNALS_PRESENT"


# ---------------------------------------------------------------------------
# §19 · the retry budget is read, never recomputed
# ---------------------------------------------------------------------------


def test_the_budget_is_read_from_disk_so_restarting_does_not_reset_it(tmp_path):
    path = tmp_path / "health/reconcile-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "res-r": {"attempts": [time.time()], "last_action": "restart"},
    }))
    payload = view.retry_budget(state_dir=tmp_path)
    assert payload["count"] == 1
    assert payload["resources"][0]["resource"] == "res-r"
    assert payload["resources"][0]["attempts_in_window"] == 1


def test_an_empty_budget_is_not_reported_as_a_healthy_node(tmp_path):
    payload = view.retry_budget(state_dir=tmp_path)
    assert payload["count"] == 0
    assert "not the same as a resource that is healthy" in payload["note"]


def test_an_exhausted_budget_reports_the_reason_and_the_wait(tmp_path):
    path = tmp_path / "health/reconcile-state.json"
    path.parent.mkdir(parents=True)
    now = time.time()
    path.write_text(json.dumps({
        "res-r": {"attempts": [now - 5, now - 3, now - 1], "last_action": "restart"},
    }))
    row = view.retry_budget(state_dir=tmp_path)["resources"][0]
    assert row["may_act_now"] is False
    assert row["blocked_by"] == "BUDGET_EXHAUSTED"
    assert row["retry_after_seconds"] > 0


def test_a_resource_acted_on_but_no_longer_registered_is_named(tmp_path):
    path = tmp_path / "health/reconcile-state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "gone": {"attempts": [time.time()], "last_action": "restart"},
    }))
    payload = view.retry_budget(state_dir=tmp_path, registry=registry_of(resource("res-r")))
    assert payload["unregistered_with_history"] == ["gone"]


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------


def write_registry(tmp_path):
    path = tmp_path / "resources.yaml"
    path.write_text(yaml.safe_dump({
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": [resource("res-r")],
    }))
    return path


def test_every_new_daemon_method_is_registered_in_both_places():
    """A handler that exists but is not in `_methods()` is unreachable.

    `handle()` looks up the map; the unknown-method error prints the tuple.
    The two lists drifting means a method that answers and a method nobody can
    discover, and the error message would name the wrong set.
    """
    instance = daemon.Daemon(registry_of(resource("res-r")), state_dir=Path("/tmp/x"))
    served = set(instance._methods())
    added = {"runtime.groups", "runtime.group", "runtime.tree", "runtime.budget",
             "process.top", "process.trace", "anomaly.list", "anomaly.explain",
             "container.attribution"}
    assert added <= served, sorted(added - served)
    for method in added:
        request = {"method": method, "params": {}}
        response = instance.handle(request)
        # Every one of them must refuse for a NAMED reason rather than
        # answering with an empty result, because an empty result from a
        # control plane reads as "nothing to report".
        if method not in ("runtime.groups", "runtime.budget", "process.top",
                          "anomaly.list", "container.attribution"):
            assert response["ok"] is False, method
            assert response["error"]["code"].startswith(("ValueError", "Unknown"))


def test_the_served_methods_and_the_handler_map_are_the_same_set():
    """The drift the test above describes but cannot see.

    `handle` looks up one map; the unknown-method error prints `_methods()`.
    When the map was a literal inside `handle`, nothing could enumerate it, so
    a method added to one place and not the other was undetectable by
    construction — and the two failure modes are opposite: a handler with no
    entry in `_methods()` is reachable but undiscoverable, and an entry with
    no handler is discoverable and refuses the caller who believed it.
    """
    instance = daemon.Daemon(registry_of(resource("res-r")), state_dir=Path("/tmp/x"))
    served = set(instance._methods())
    implemented = set(instance.handler_map())
    assert served == implemented, {
        "announced but not implemented": sorted(served - implemented),
        "implemented but not announced": sorted(implemented - served),
    }
    for method, handler in instance.handler_map().items():
        assert callable(handler), method


def test_a_missing_parameter_is_refused_in_a_sentence_not_a_code():
    instance = daemon.Daemon(registry_of(resource("res-r")), state_dir=Path("/tmp/x"))
    response = instance.handle({"method": "runtime.group", "params": {}})
    assert response["ok"] is False
    assert "missing required parameter 'ref'" in response["error"]["message"]


def test_an_unknown_daemon_method_lists_the_ones_that_exist():
    instance = daemon.Daemon(registry_of(resource("res-r")), state_dir=Path("/tmp/x"))
    response = instance.handle({"method": "runtime.destroy", "params": {}})
    assert response["error"]["code"] == "UNKNOWN_METHOD"
    assert "runtime.groups" in response["error"]["message"]


def test_the_mcp_surface_declares_exactly_the_contract_and_nothing_side_effecting():
    contract = yaml.safe_load((REPO / "fleet/mcp-contract.yaml").read_text())
    surface = contract["surfaces"]["serveros-node-mcp"]
    assert tuple(surface["tools"]) == mcp.tool_names()

    forbidden = ("stop", "kill", "remove", "prune", "restart", "delete", "reconcile")
    for name in mcp.tool_names():
        assert not any(word in name for word in forbidden), name


def test_an_mcp_tool_argument_that_does_not_exist_is_refused():
    """A misspelled argument must not be silently ignored.

    Ignoring it would run the tool with a default the caller did not choose —
    for `process.top` that is ten rows instead of the thousand they asked for,
    which looks like an answer.
    """
    class Recorder:
        def __init__(self):
            self.calls = []

        def call(self, method, params=None):
            self.calls.append((method, params))
            return {"ok": True}

    client = Recorder()
    server = mcp.NodeMcpServer(client=client)
    response = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "process.top", "arguments": {"limitt": 5}},
    })
    assert response["result"]["isError"] is True
    assert client.calls == []


def test_the_mcp_adapter_forwards_rather_than_computing():
    """The adapter holds no registry, no host and no policy. Asserted by
    counting forwards: one tool call is exactly one daemon call."""
    class Recorder:
        def __init__(self):
            self.calls = []

        def call(self, method, params=None):
            self.calls.append((method, params))
            return {"answered_by": "serverosd"}

    client = Recorder()
    server = mcp.NodeMcpServer(client=client)
    server.handle({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "runtime.groups", "arguments": {}},
    })
    assert client.calls == [("runtime.groups", {})]


# ---------------------------------------------------------------------------
# The CLI parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv", [
    ["runtime", "groups"],
    ["runtime", "group", "some-ref"],
    ["runtime", "tree", "some-ref"],
    ["runtime", "budget"],
    ["process", "top", "--limit", "5", "--by", "mem"],
    ["process", "trace", "1234"],
    ["anomaly", "list"],
    ["anomaly", "explain", "some-subject"],
    ["container", "attribution"],
])
def test_each_new_command_parses_to_a_handler(argv):
    args = cli.build_parser().parse_args(argv)
    assert callable(args.func)


def test_a_global_flag_after_the_subcommand_still_reaches_the_registry():
    """The SUPPRESS interaction is load-bearing and a new subparser is exactly
    where it gets undone."""
    args = cli.build_parser().parse_args(["process", "top", "--registry", "/tmp/x.yaml"])
    assert args.registry == "/tmp/x.yaml"


def test_a_bad_pid_is_refused_before_anything_is_measured(capsys):
    code = cli.main(["process", "trace", "not-a-pid"])
    assert code == cli.EXIT_COULD_NOT_ANSWER
    assert "is not a pid" in capsys.readouterr().err


def test_an_unknown_group_exits_two_and_says_where_to_look(capsys):
    code = cli.main(["runtime", "group", "no-such-group", "--json"])
    assert code == cli.EXIT_COULD_NOT_ANSWER
    assert "no runtime group matches" in capsys.readouterr().err


@pytest.mark.parametrize("seconds,expected", [
    (5, "5s"), (90, "1m"), (7200, "2h"), (172800, "2d"), (None, "UNKNOWN"),
])
def test_the_duration_formatter_divides(seconds, expected):
    """The first version printed `16397h` for four and a half hours.

    It appended the unit to the raw seconds value. Wrong in the direction that
    looks plausible, which is the kind of wrong a reader does not check.
    """
    assert cli._duration(seconds) == expected


def test_the_rendered_tree_keeps_its_branches_connected():
    node = {"pid": 1, "comm": "a", "children": [
        {"pid": 2, "comm": "b", "children": [{"pid": 4, "comm": "d", "children": []}]},
        {"pid": 3, "comm": "c", "children": []},
    ]}
    rendered = cli._render_tree(node)
    assert rendered[0].strip().startswith("1 a")
    assert any("├── 2 b" in line for line in rendered)
    assert any("└── 3 c" in line for line in rendered)
    assert any("└── 4 d" in line for line in rendered)
    # The child of a non-last node keeps a vertical bar; the child of the last
    # one does not. Without this the drawing is a ladder, not a tree.
    assert any(line.startswith("  │   └── 4 d") for line in rendered)


# ---------------------------------------------------------------------------
# Negative controls
#
# Each guard above is asserted by showing the input it refuses is DANGEROUS,
# not merely that it is refused. A guard whose input turns out to be harmless
# is theatre, and the way to tell the two apart is to feed the input in
# directly and watch what the layer below does with it.
# ---------------------------------------------------------------------------


def test_the_container_guard_prevents_a_real_classification_not_a_hypothetical():
    """Same container, fed straight to the detector: it IS a reclaim candidate.

    This is what makes the guard in `anomaly_list` load-bearing. An
    unattributed stopped container is not benign input that happens to be
    declined — it is the exact shape of STOPPED_RECLAIM_CANDIDATE, and the
    only thing standing between a PROTECTED container and that classification
    is the registry document the absent-registry path cannot read.
    """
    row = {"ref": "runtime:container:c1", "state": "exited", "unknown_owner": True,
           "disposition": "DEFER", "evidence": {"exit_code": 0}}
    codes = {signal["code"] for signal in view.anomaly.detect(containers=[row])}
    assert "STOPPED_RECLAIM_CANDIDATE" in codes


def test_a_protected_container_emits_nothing_even_when_passed_directly():
    """The other half: the disposition, not the caller, is what protects it."""
    row = {"ref": "runtime:container:c1", "state": "exited", "unknown_owner": True,
           "disposition": "PROTECTED", "evidence": {"exit_code": 0}}
    assert view.anomaly.detect(containers=[row]) == []


def test_the_old_mcp_comparison_was_provably_unreachable():
    """`kind == "mcp"` could never be true, and the vocabulary proves it.

    The registration is REFUSED before it reaches the detector: `mcp` is not a
    kind this contract declares. So the branch was dead on arrival, and a dead
    branch reports exactly what a satisfied one does — nothing. `MCP_KINDS`
    keeps the literal for tolerance while matching the kind that can exist.
    """
    with pytest.raises(registry.RegistryInvalid, match="unknown kind 'mcp'"):
        registry_of(resource("res-m", kind="mcp"))
    contract = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())
    assert "mcp-server" in contract["kinds"]
    assert "mcp" not in contract["kinds"]
    assert "mcp-server" in view.anomaly.MCP_KINDS
    assert view.anomaly.MCP_KINDS[0] == "mcp"  # tolerance, documented, unreachable


def test_an_mcp_server_that_fails_its_functional_probe_is_named_as_broken():
    subject = "res-m"
    results = {subject: {"kind": "mcp-server", "liveness": "OK", "functional": "FAILED"}}
    codes = {s["code"] for s in view.anomaly.detect(probe_results=results)}
    assert {"FUNCTIONAL_PROBE_FAILED", "MCP_PROCESS_ALIVE_BUT_UNAVAILABLE"} <= codes
    # And it classifies as BROKEN, not as stale: the process being alive is
    # precisely what makes this shape invisible to a process check.
    classification = view.anomaly.classify(
        view.anomaly.detect(probe_results=results), subject=subject)
    assert classification["classification"] == "BROKEN_NOT_STALE"


def test_a_duration_is_not_the_raw_seconds_with_a_unit_glued_on():
    """The shipped defect, stated as the value it produced.

    `16397` seconds is four and a half hours. The first formatter answered
    `16397h`, which is the seconds value with the unit appended — wrong in the
    direction that survives a glance.
    """
    assert cli._duration(16397) == "4h"
    assert cli._duration(16397) != "16397h"
    assert cli._duration(20550) == "5h"
    assert 16397 / 3600 == pytest.approx(4.55, abs=0.01)


def test_the_daemon_answers_which_device_this_is_from_the_registry():
    """`_r_device_show` used to answer `device:server-beijing` from a literal.

    On this host the registry declares that same string, so the bug was
    invisible in live output — the daemon and the registry agreed by
    coincidence, and would have kept agreeing right up until someone renamed
    the node, which is the moment the identity starts mattering. The registry
    here names a different device, which is the only way to see which of the
    two answers the daemon is giving.
    """
    document = {
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:somewhere-else",
        "resources": [resource("res-r")],
    }
    instance = daemon.Daemon(registry.Registry(document=document),
                             state_dir=Path("/tmp/x"))

    response = instance.handle({"method": "device.show", "params": {}})
    assert response["ok"] is True
    assert response["result"]["device_ref"] == "device:somewhere-else"


def test_a_registry_that_names_no_device_reports_none_rather_than_a_default():
    """No device_ref is a fact about the document, and inventing one here
    would be the same defect in the other direction."""
    instance = daemon.Daemon(registry_of(resource("res-r")), state_dir=Path("/tmp/x"))
    assert instance.handle({"method": "device.show", "params": {}})["result"]["device_ref"] is None


# ---------------------------------------------------------------------------
# §18/§19 — the resource anomaly view
# ---------------------------------------------------------------------------


class FakeAnomalyInventory:
    """A Docker provider that is reachable, and records what was asked of it.

    `docker_space` is the one read here that costs ~22s on the live host, so
    whether it was called is a fact worth asserting rather than a detail: an
    opt-in that is not actually opt-in is a tick that misses its deadline.
    """

    name = "fake-anomaly-inventory"

    def __init__(self, *, host=None, disk=None, sizes=None, logs=None, space=None):
        self._host = host or {"memory": {"total_bytes": 100, "used_bytes": 10,
                                        "available_bytes": 90,
                                        "swap": {"total_bytes": 100,
                                                 "used_bytes": 1}}}
        self._disk = disk or {"roles": [], "others": []}
        self._sizes = sizes if sizes is not None else {"count": 0, "containers": []}
        self._logs = logs if logs is not None else {"state": "PRESENT", "count": 0,
                                                    "unobservable": 0,
                                                    "containers": []}
        self._space = space if space is not None else {"state": "PRESENT",
                                                       "sections": {}}
        self.asked: list[str] = []

    def host_summary(self):
        self.asked.append("host_summary")
        return self._host

    def disk_status(self):
        self.asked.append("disk_status")
        return self._disk

    def docker_sizes(self):
        self.asked.append("docker_sizes")
        return self._sizes

    def container_logs(self):
        self.asked.append("container_logs")
        return self._logs

    def docker_space(self):
        self.asked.append("docker_space")
        return self._space


def test_the_anomaly_view_gathers_from_the_producers_and_names_which(
        monkeypatch):
    """§18/§19. Six inputs, and the report says which of them this call reached.

    `detect` cannot tell a document the caller supplied from one this layer
    measured, and a reader checking whether an answer rests on a fresh
    measurement or on somebody's snapshot needs to. That is what `gathering`
    is for, and the tunnel document is the honest case here: nothing is
    declared on this host, so the two tunnel codes come back as not-evaluated
    rather than as a healthy fleet.
    """
    inv = FakeAnomalyInventory()
    payload = view.resource_anomaly(
        inventory=inv, registry_doc=view.NO_REGISTRY,
        tunnel={"declared": [], "declared_count": 0})
    assert payload["gathering"]["inputs_reached"] == ["host", "disk", "docker",
                                                      "tunnel"]
    assert payload["gathering"]["inputs_supplied_by_the_caller"] == ["tunnel"]
    # No registry, so the two that need one are named with their reason instead
    # of being gathered against a registry nobody authored.
    assert sorted(payload["gathering"]["inputs_unreachable"]) == ["desired", "hygiene"]
    assert "no managed-runtime registry" in payload["gathering"]["inputs_unreachable"]["hygiene"]
    reasons = {row["code"] for row in payload["codes_not_evaluated"]}
    assert {"TUNNEL_UNHEALTHY", "TUNNEL_OWNER_MISSING"} <= reasons
    # §22 over the same call. No registry means nothing to classify as owned,
    # and the two codes that would have read that classification are named as
    # ungraded rather than reported as a host with no unowned containers.
    assert {"RUNTIME_UNOWNED", "RUNTIME_ORPHANED"} <= reasons


def test_the_reclaimable_picture_is_opt_in_at_this_layer_too():
    """The ~22s read is asked for by name, or it is not asked for at all."""
    EMPTY_TUNNEL = {"declared": [], "declared_count": 0}
    off = FakeAnomalyInventory()
    view.resource_anomaly(inventory=off, registry_doc=view.NO_REGISTRY,
                          tunnel=EMPTY_TUNNEL)
    assert "docker_space" not in off.asked

    on = FakeAnomalyInventory()
    payload = view.resource_anomaly(inventory=on, registry_doc=view.NO_REGISTRY,
                                    tunnel=EMPTY_TUNNEL, space=True)
    assert "docker_space" in on.asked
    assert payload["space_included"] is True


def test_the_docker_share_is_measured_against_the_filesystem_the_root_lands_on():
    """The denominator, and it is the one `inventory._mount` documents.

    DOCKER_FS is asked about `/var/lib/docker` and lands on `/`. A denominator
    taken from the path that was asked about would be `None` here — the path is
    not a filesystem this document describes — and the code would stay silent
    on a Docker store holding 90% of its disk.
    """
    inv = FakeAnomalyInventory(
        disk={"roles": [{"role": "DOCKER_FS", "path": "/var/lib/docker",
                         "state": "PRESENT", "mount_point": "/",
                         "total_bytes": 1000, "used_percent": 10.0,
                         "inodes": {"used_percent": 1.0}}], "others": []},
        space={"state": "PRESENT", "sections": {
            "images": {"size_bytes": 900, "reclaimable_bytes": 0},
            "build_cache": {"size_bytes": 100, "reclaimable_bytes": 0}}})
    payload = view.resource_anomaly(
        inventory=inv, registry_doc=view.NO_REGISTRY,
        tunnel={"declared": [], "declared_count": 0}, space=True)
    rows = [r for r in payload["signals"] if r["code"] == "DOCKER_STORAGE_HIGH"]
    assert len(rows) == 1
    assert rows[0]["measured"]["filesystem_bytes"] == 1000
    assert rows[0]["measured"]["used_percent"] == 100.0


def test_the_governance_document_is_attached_as_a_report_and_never_as_a_gate():
    """§22 on the §18 surface. The document states what an owner said.

    It is reported beside the measurements, with `destructive_reconcile`
    carried through as OFF, and it is read defensively: a governance document
    that moved or was made unparseable is a finding about the document, and it
    must not be able to fail the observation it is reported next to.
    """
    payload = view.resource_anomaly(
        inventory=FakeAnomalyInventory(), registry_doc=view.NO_REGISTRY,
        tunnel={"declared": [], "declared_count": 0})
    policy = payload["supported_by_resource_policy"]
    assert policy["state"] in ("DECLARED", "ABSENT", "UNREADABLE")
    assert policy["destructive_reconcile"]["enabled"] is False
    # And the coverage answer is the three-way one: a class the document does
    # not carry is ABSENT, never silently filled.
    assert {"declared", "absent", "unestablished"} <= set(policy["coverage"])
