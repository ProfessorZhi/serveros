#!/usr/bin/env python3
"""§13 · Tunnel observation and ownership.

The section has one invariant and it is not about tunnels being up:

    a task-scoped tunnel is NOT an agent-session-owned tunnel, and the
    session that caused its creation ending must not make it an orphan.

The incident behind it is adversarial rather than obvious. A tunnel a task
depended on was registered with session lifetime; an unrelated Agent session
ended; the task lost its transport mid-flight. The far side was healthy the
whole time and so was the tunnel. Every observation taken between creation and
retirement was CORRECT. There is nothing to detect afterwards, which is why the
tests below assert the pair — the ownership verdict AND what a session ending
means — because either one alone reads as fine.

Two more things this file pins, both of them about not knowing:

  * `hidepid=invisible` is not a down tunnel. A declared pid this uid cannot
    see is UNKNOWN with a reason, and a test asserts the branch never returns
    STOPPED. Reporting a live tunnel as down is the direction that gets it
    restarted.

  * a forwarding process nobody registered is a CANDIDATE. It gets a row so it
    is visible, and it gets no owner at all. `ps` and the environment are
    evidence; §13 says they are not authority.

Fixtures are copied from `test_infrastructure.py` rather than imported: the
suite loads modules by path and has no package for the tests directory, and a
cross-test import would make one file's fixture a dependency of another's
collection order.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import sys
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


infra = _load("serveros_runtime_infrastructure", "infrastructure.py")
registry = _load("serveros_runtime_registry", "registry.py")
graph = _load("serveros_runtime_graph", "graph.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")

MR_CONTRACT = yaml.safe_load((RUNTIME / "managed-runtime-contract.yaml").read_text())

FACT = infra.FACT
INFERRED = infra.INFERRED
UNKNOWN = infra.UNKNOWN


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def resource(rid="res-r", *, kind="process", lifecycle="persistent", protection="normal",
             desired="running", probes=None, transport=None, not_applicable=None,
             recovery=None, owner_ref="project:demo", owner_type="project",
             pids=None, runtime_ref=None, device_ref="device:server-beijing"):
    record = {
        "id": rid,
        "kind": kind,
        "owner": {"type": owner_type, "ref": owner_ref},
        "scope": "user",
        "lifecycle": {"class": lifecycle},
        "desired": {"state": desired},
        "protection": {"level": protection},
    }
    if probes is not None:
        record["probes"] = probes
    if transport:
        record["transport"] = transport
    if not_applicable:
        record["not_applicable"] = not_applicable
    if recovery is not None:
        record["recovery"] = recovery
    if pids is not None:
        record["pids"] = pids
    if runtime_ref:
        record["runtime_ref"] = runtime_ref
    if device_ref:
        record["device_ref"] = device_ref
    return record


def registry_of(*records):
    document = {
        "schema": "serveros/managed-runtime-registry.v1",
        "resources": list(records),
    }
    return registry.Registry(document=document)


def tunnel_record(rid="tun-1", **kw):
    kw.setdefault("kind", "tunnel")
    kw.setdefault("transport", {"class": "ssh", "local": "127.0.0.1:9000",
                                "remote": "far.example:80"})
    kw.setdefault("probes", [
        {"id": "t-live", "kind": "process-exists", "axis": "liveness", "pattern": "ssh"},
        {"id": "t-chan", "kind": "socket-connect", "axis": "transport", "path": "/run/t.sock"},
        {"id": "t-func", "kind": "http-health", "axis": "functional", "url": "https://far.example/"},
    ])
    return resource(rid, **kw)


class FakeHost:
    """A synthetic /proc. Files by absolute path; sockets by pid."""

    name = "fake-host"

    def __init__(self, files=None, fds=None, uptime=1000.0):
        self.files = files or {}
        self.fds = fds or {}
        self._uptime = uptime

    def read_text(self, path):
        return self.files.get(path)

    def read_bytes(self, path):
        value = self.files.get(path)
        return value.encode() if isinstance(value, str) else None

    def listdir(self, path):
        if path in self.fds:
            return list(self.fds[path])
        if path == "/proc":
            return list(self._proc_entries())
        return []

    def _proc_entries(self):
        for path in list(self.files) + list(self.fds):
            parts = path.split("/")
            if len(parts) > 2 and parts[1] == "proc" and parts[2].isdigit():
                yield parts[2]

    def readlink(self, path):
        return self.fds.get(path)

    def run(self, args, timeout=8.0):
        return 127, "", "not found"

    def uptime(self):
        return self._uptime

    def page_size(self):
        return 4096

    def uid_name(self, uid):
        return f"uid{uid}"


def stat_line(*, pid, comm, state="S", ppid, utime=0, stime=0, num_threads=1,
              starttime=0, rss_pages=0):
    values = {n: 0 for n in range(3, 25)}
    values.update({3: state, 4: ppid, 14: utime, 15: stime, 18: 20,
                   20: num_threads, 22: starttime, 24: rss_pages})
    return f"{pid} ({comm}) " + " ".join(str(values[n]) for n in range(3, 25)) + "\n"


def status_line(uid=1006, name="svc"):
    return f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"


def net_tcp(entries):
    """(inode, local_hex, state) triples -> a /proc/net/tcp body."""
    lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
             "retrnsmt   uid  timeout inode"]
    for inode, local_hex, state in entries:
        lines.append(f"  0: {local_hex} 00000000:0000 {state} 00000000:00000000 "
                     f"00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0")
    return "\n".join(lines) + "\n"


LISTEN = "0A"


def a_forwarding_host(pid=500, *, local=None):
    """One visible process, forwarding, and nothing else."""
    files = {
        f"/proc/{pid}/stat": stat_line(pid=pid, comm="ssh", ppid=1, starttime=90000),
        f"/proc/{pid}/cmdline": "ssh\x00-L\x008080:host:80\x00me@far\x00",
        f"/proc/{pid}/status": status_line(),
    }
    if local:
        files["/proc/net/tcp"] = net_tcp([("222", local, LISTEN)])
    return FakeHost(files=files, fds={f"/proc/{pid}/fd": ["0"]})


def a_quiet_host():
    """A visible process that is not forwarding, and no listeners."""
    return FakeHost(files={
        "/proc/500/stat": stat_line(pid=500, comm="svc", ppid=1, starttime=90000),
        "/proc/500/cmdline": "/usr/bin/svc\x00",
        "/proc/500/status": status_line(),
    }, fds={"/proc/500/fd": ["0"]})


def just_the_tunnel(**kw):
    return infra.tunnel_observation(a_quiet_host(), registry_doc=registry_of(tunnel_record(**kw)))


def the_row(**kw):
    return just_the_tunnel(**kw)["declared"][0]


# ---------------------------------------------------------------------------
# The field set — §13's list, on every row, declared and observed alike
# ---------------------------------------------------------------------------


BRIEF_FIELDS = (
    "tunnel_ref", "runtime_ref", "device_ref", "provider", "owner_ref",
    "owner_type", "scope", "direction", "local_endpoint", "remote_endpoint",
    "desired_state", "actual_state", "health", "last_healthy_at",
    "restart_policy", "criticality", "observed_at", "provenance", "freshness",
)


def test_the_normalized_field_set_contains_every_field_the_brief_names():
    missing = [f for f in BRIEF_FIELDS if f not in infra.TUNNEL_FIELDS]
    assert missing == [], missing


def test_a_declared_row_carries_every_field_even_the_empty_ones():
    row = the_row()
    missing = [f for f in infra.TUNNEL_FIELDS if f not in row]
    assert missing == [], missing


def test_an_observed_row_carries_the_same_field_set_as_a_declared_one():
    """The two halves of the answer are read by the same consumer.

    An undeclared forwarder that omitted the fields it has no value for would
    read as a different KIND of thing rather than as the same thing known less
    well — and the fields it is missing are exactly the ones a reader has to be
    told are missing.
    """
    out = infra.tunnel_observation(a_forwarding_host(), registry_doc=registry_of())
    row = out["undeclared"][0]
    missing = [f for f in infra.TUNNEL_FIELDS if f not in row]
    assert missing == [], missing


def test_a_field_this_host_cannot_fill_is_null_and_named_with_a_reason():
    """`null` and "we did not look" are different readings.

    Nothing on this host records when a tunnel was last healthy. Stamping the
    observation time there instead would make every tunnel, including one that
    has been down for a week, look like it was healthy a moment ago.
    """
    row = the_row()
    assert row["last_healthy_at"] is None
    assert row["unknowns"]["last_healthy_at"]


def test_a_runtime_is_never_inferred_from_a_nearby_port_or_a_similar_name():
    row = the_row()
    assert row["runtime_ref"] is None
    assert row["unknowns"]["runtime_ref"]


def test_a_declared_runtime_ref_is_reported_verbatim():
    row = the_row(runtime_ref="runtime:container:compose:lit-0104-dev100/litellm")
    assert row["runtime_ref"] == "runtime:container:compose:lit-0104-dev100/litellm"
    assert row["unknowns"]["runtime_ref"] is None


# ---------------------------------------------------------------------------
# The invariant · a session ending does not reach a task's tunnel
# ---------------------------------------------------------------------------


def test_a_session_owned_task_scoped_tunnel_is_a_violation_the_contract_refuses():
    verdict = infra.ownership_verdict("session:abc123", "task-scoped")
    assert verdict["state"] == "VIOLATION"
    assert verdict["disposition"] == "REFUSE"
    assert verdict["alternative"], "a refusal with no alternative is a dead end"
    assert "task-scoped" in verdict["because"]


def test_the_violation_quotes_the_contract_rather_than_restating_it():
    """The `because` is the contract's own prose about the incident.

    A reader comparing the verdict to the rule must find the same sentence in
    both, or the two will drift into two rules.
    """
    verdict = infra.ownership_verdict("session:abc123", "persistent")
    declared = (MR_CONTRACT["ownership"]["permitted"]["forbidden"]["persistent"] or "").strip()
    assert declared
    assert declared.split(".")[0] in verdict["because"]


def test_an_agent_session_ending_does_not_orphan_a_task_tunnel():
    """§13's most important invariant, asserted for every state of the question.

    `creating_session_present` is reported and is deliberately not enough to
    change the answer: the session that is gone may be an unrelated one, or the
    first of two working the same item. Only a declaration may make a tunnel an
    orphan, and `task-scoped` declares the opposite.
    """
    row = {"lifecycle_class": "task-scoped", "ownership": {"state": "VIOLATION"}}
    for present in (True, False, None):
        consequence = infra.session_end_consequence(row, creating_session_present=present)
        assert consequence["orphaned"] is False, present
        assert consequence["state"] == "UNAFFECTED", present


def test_the_same_invariant_holds_for_a_persistent_tunnel():
    row = {"lifecycle_class": "persistent", "ownership": {"state": "VIOLATION"}}
    consequence = infra.session_end_consequence(row, creating_session_present=False)
    assert consequence["orphaned"] is False
    assert consequence["state"] == "UNAFFECTED"


def test_a_session_scoped_tunnel_ending_with_its_session_is_its_lifetime():
    row = {"lifecycle_class": "session-scoped", "ownership": {"state": "CONSISTENT"}}
    gone = infra.session_end_consequence(row, creating_session_present=False)
    here = infra.session_end_consequence(row, creating_session_present=True)
    assert gone["state"] == here["state"] == "SESSION_LIFETIME"
    assert gone["orphaned"] is True
    assert here["orphaned"] is False


def test_a_tunnel_with_no_lifecycle_is_unknown_and_never_an_orphan():
    """Orphanhood is a claim about a declared lifetime.

    With no declaration there is nothing to claim it from, and `orphaned: True`
    here would license a cleanup pass to reclaim a tunnel on the strength of a
    missing field.
    """
    row = {"lifecycle_class": None, "ownership": {"state": UNKNOWN}}
    consequence = infra.session_end_consequence(row, creating_session_present=False)
    assert consequence["orphaned"] is False
    assert consequence["state"] == UNKNOWN


def test_an_unreadable_rule_makes_the_verdict_unknown_not_consistent():
    """The failure this whole shape exists to catch fails SILENTLY.

    A check that could not run and a check that passed are the same value to
    every caller that only reads the boolean.
    """
    assert infra.ownership_verdict("session:abc", "persistent", rule={})["state"] == UNKNOWN
    assert infra.ownership_verdict("session:abc", None)["state"] == UNKNOWN


def test_the_verdict_comes_from_the_rule_it_is_given_not_from_a_hardcoded_list():
    """Proof that this reads the contract rather than restating it.

    A rule that permits no lifecycle at all must flip `disposable` from
    CONSISTENT to VIOLATION. If it does not, the permitted list is hardcoded
    somewhere below and the contract's copy of it is decoration.
    """
    closed = {
        "session_owner_ids": ["session:*"],
        "permitted": {"session_owner_lifecycle": []},
        "on_violation": {"disposition": "REFUSE", "alternative": "register it durably"},
    }
    assert infra.ownership_verdict("session:a", "disposable", rule=closed)["state"] == "VIOLATION"
    assert infra.ownership_verdict("session:a", "disposable")["state"] == "CONSISTENT"


def test_a_work_item_owner_is_not_constrained_by_the_session_rule():
    assert infra.owner_is_session("work-item:LIT-0104") is False
    assert infra.ownership_verdict("work-item:LIT-0104", "task-scoped")["state"] == "CONSISTENT"


@pytest.mark.parametrize("owner_ref,expected", [
    ("session:abc", True),
    ("agent-runtime:codex/1", True),
    ("agent-session:device%3Aserver-beijing/codex/1", True),
    ("work-item:LIT-0104", False),
    ("project:litellm", False),
    (None, False),
])
def test_a_session_shaped_owner_is_recognised_from_the_contracts_own_globs(owner_ref, expected):
    assert infra.owner_is_session(owner_ref) is expected


def test_the_contract_and_the_detector_agree_about_what_a_session_is():
    """Two readers of one rule, and the divergence was real.

    `anomaly.SESSION_OWNER_PREFIXES` — which `registry._check_ownership` refuses
    on — lists three ids. The contract listed two. `agent-session:*` is a live
    ref form (`runtime/agent_sessions.py` builds it), so a document that reached
    the observer without passing through registration would have been reported
    CONSISTENT: the rule would have said "not a session" about `agent-session:`.
    Found by this test on 2026-09-14 and fixed in the contract, which is the
    SSOT — the code list is the copy.
    """
    globs = [str(p) for p in infra.ownership_rule()["session_owner_ids"]]

    uncovered = [p for p in anomaly.SESSION_OWNER_PREFIXES
                 if not any(fnmatch.fnmatchcase(p + "probe", g) for g in globs)]
    assert uncovered == [], (
        f"the detector calls {uncovered} session-shaped and the contract does "
        f"not ({globs})"
    )

    unbacked = [g for g in globs
                if not any(g.rstrip("*").startswith(p) or p.startswith(g.rstrip("*"))
                           for p in anomaly.SESSION_OWNER_PREFIXES)]
    assert unbacked == [], (
        f"the contract names {unbacked} session-shaped and nothing that refuses "
        f"a registration agrees"
    )


def test_an_agent_session_owner_is_refused_by_both_halves_of_the_system():
    """The write side refuses it; the read side, given one anyway, reports it.

    Both are asserted together because either alone is a half-answer: a gate
    that only the writer honours leaves the reader unable to say anything about
    a document that arrived another way.
    """
    with pytest.raises(registry.RegistryInvalid):
        registry_of(tunnel_record(owner_ref="agent-session:device%3Abeijing/codex/1",
                                  owner_type="agent-session"))
    verdict = infra.ownership_verdict("agent-session:device%3Abeijing/codex/1", "persistent")
    assert verdict["state"] == "VIOLATION"


# ---------------------------------------------------------------------------
# hidepid · not visible is not the same as not running
# ---------------------------------------------------------------------------


def test_a_declared_pid_this_uid_cannot_see_is_unknown_and_never_stopped():
    """A live root-owned forward is invisible here, and STOPPED is a lie about it."""
    out = infra.tunnel_observation(a_quiet_host(),
                                   registry_doc=registry_of(tunnel_record(pids=[4242])))
    row = out["declared"][0]
    assert row["actual_state"] == UNKNOWN
    assert row["actual_state_status"] == UNKNOWN
    assert "4242" in row["actual_state_why"]
    assert "hidepid" in row["actual_state_why"]
    assert "NOT stopped" in row["actual_state_why"]
    assert "STOPPED" not in row["actual_state"]


def test_a_visible_declared_forwarder_is_running_and_is_a_fact():
    out = infra.tunnel_observation(a_forwarding_host(pid=500),
                                   registry_doc=registry_of(tunnel_record(pids=[500])))
    row = out["declared"][0]
    assert row["actual_state"] == "RUNNING"
    assert row["actual_state_status"] == FACT
    assert out["undeclared_count"] == 0


def test_a_listener_serving_the_declared_endpoint_is_listening_not_healthy():
    """A bound socket proves a listener, not that the far end carried anything.

    `/proc/net/tcp` says nothing about the channel, so the honest axes are
    liveness RUNNING and transport UNKNOWN — which derives DEGRADED. Reporting
    HEALTHY here would be reading "something is listening" as "the tunnel
    works", which is the `liveness: RUNNING` trap one layer down.
    """
    host = a_forwarding_host(pid=500, local="0100007F:36A3")  # 127.0.0.1:13987
    doc = registry_of(tunnel_record(
        transport={"class": "ssh", "local": "127.0.0.1:13987", "remote": "far.example:80"}))
    row = infra.tunnel_observation(host, registry_doc=doc)["declared"][0]
    assert row["actual_state"] == "LISTENING"
    assert row["actual_state_status"] == FACT
    assert row["health"]["state"] == "DEGRADED"


def test_a_tunnel_with_nothing_to_look_for_says_so_rather_than_guessing():
    row = the_row()
    assert row["actual_state"] == UNKNOWN
    assert row["actual_state_why"]


def test_the_rows_health_is_the_health_models_verdict_and_not_a_second_one():
    """§13 says extend the existing health model, and this is the proof.

    Not "both happen to say DEGRADED today" — the same resource through the
    module's own entry point must give the same state the row carries, so a
    future second implementation cannot diverge unnoticed.
    """
    doc = registry_of(tunnel_record())
    row = infra.tunnel_observation(a_quiet_host(), registry_doc=doc)["declared"][0]
    verdict = infra.tunnel_health(doc.get("tun-1"))
    assert row["health"]["state"] == verdict.state


def test_both_observation_shapes_reach_the_health_model():
    """A string is wrapped; a hand-built Observation keeps its own provenance.

    The two callers existed side by side for months and never met: every test
    passed real `Observation`s and the sweep passed strings, so the string path
    was dead and raised `AttributeError` where the caller caught it. Both
    shapes are asserted here so the two cannot drift apart again.
    """
    resource = registry_of(tunnel_record()).get("tun-1")
    from_state = infra.tunnel_health(resource, {"liveness": "STOPPED"})
    assert from_state.liveness.state == "STOPPED"
    assert from_state.liveness.observed_by["id"] == "tunnel-observation"

    by_hand = infra.tunnel_health(resource, {"liveness": infra.health.Observation(
        axis="liveness", state="STOPPED",
        observed_by={"type": "probe", "id": "t-live"}, observed_at="now",
        evidence={"type": "host-measurement"})})
    assert by_hand.liveness.state == "STOPPED"
    assert by_hand.liveness.observed_by["id"] == "t-live", (
        "a caller that measured this axis knows who measured it; the wrap must "
        "not overwrite that with a generic observer")


def test_a_state_string_the_axis_does_not_accept_is_refused():
    """The wrap is not a permissive cast. `FINE` is a word, not a liveness."""
    with pytest.raises(infra.health.HealthError):
        infra.tunnel_health(registry_of(tunnel_record()).get("tun-1"),
                            {"liveness": "FINE"})


def test_the_coverage_block_counts_what_the_answer_could_not_settle():
    out = infra.tunnel_observation(a_quiet_host(),
                                   registry_doc=registry_of(tunnel_record(pids=[4242])))
    assert out["coverage"]["declared_tunnels"] == 1
    assert out["coverage"]["with_unknown_actual_state"] == 1
    assert out["coverage"]["with_authoritative_owner"] == 1
    assert out["coverage"]["rule_readable"] is True


def test_a_violating_registration_is_not_counted_as_an_authoritative_owner():
    """The v1.0.1 defect: an owned-looking resource that is owned by its session.

    `owner_ref` is present, so a naive confidence reads AUTHORITATIVE and the
    resource looks safely attributed right up to the moment it is destroyed.
    """
    assert infra._ownership_confidence(
        {"owner_ref": "session:a"},
        {"state": "VIOLATION"},
        UNKNOWN,
    ) == INFERRED
    assert infra._ownership_confidence({"owner_ref": None}, {"state": UNKNOWN}, UNKNOWN) == UNKNOWN


# ---------------------------------------------------------------------------
# Observed forwarders · a candidate is never an owner
# ---------------------------------------------------------------------------


def test_a_forwarding_process_nobody_registered_is_a_candidate_with_no_owner():
    out = infra.tunnel_observation(a_forwarding_host(pid=500), registry_doc=registry_of())
    row = out["undeclared"][0]
    assert row["tunnel_ref"] == "tunnel:observed:500"
    assert row["owner_ref"] is None
    assert row["owner_type"] is None
    assert row["ownership"]["state"] == UNKNOWN
    assert row["ownership_confidence"] == UNKNOWN
    assert row["actual_state"] == "RUNNING"
    assert row["actual_state_status"] == FACT
    assert out["coverage"]["with_authoritative_owner"] == 0


def test_declared_and_observed_are_two_halves_and_are_never_merged():
    """A registration naming pid 500 does not make the observed row `ours`.

    The declared row carries the owner and the undeclared list is empty; the
    observed process is still the same one that was there a moment ago. Merging
    them would let a process become a declared tunnel by being visible.
    """
    out = infra.tunnel_observation(a_forwarding_host(pid=500),
                                   registry_doc=registry_of(tunnel_record(pids=[500])))
    assert out["declared_count"] == 1
    assert out["undeclared_count"] == 0
    assert out["declared"][0]["owner_ref"] == "project:demo"
    assert not any("owner_ref" in r and r["owner_ref"] for r in out["undeclared"])


def test_a_registration_that_names_a_pid_makes_the_join_have_a_key():
    """The join could never match anything before 2026-09-14.

    `tunnel_inventory` consumed `pids` and `declared_tunnels` did not produce
    it, so every observed forwarder was reported `undeclared` and an empty
    result meant either "nothing is registered" or "the join is broken".
    """
    doc = registry_of(tunnel_record(pids=[500]))
    inventory = infra.tunnel_inventory(a_forwarding_host(pid=500), registry_doc=doc)
    assert inventory["join_coverage"]["join_had_a_key"] is True
    assert inventory["join_coverage"]["declared_pids"] == [500]
    assert inventory["join_coverage"]["matched"] == 1
    assert inventory["undeclared_forwarders"] == []


def test_a_registration_that_names_no_pid_reports_that_the_join_had_no_key():
    inventory = infra.tunnel_inventory(a_forwarding_host(pid=500),
                                       registry_doc=registry_of(tunnel_record()))
    assert inventory["join_coverage"]["join_had_a_key"] is False
    assert inventory["join_coverage"]["observed_forwarders"] == 1
    assert inventory["join_coverage"]["matched"] == 0
    assert len(inventory["undeclared_forwarders"]) == 1


def test_a_pid_declared_on_a_probe_counts_as_a_declaration():
    doc = registry_of(tunnel_record(probes=[
        {"id": "t-live", "kind": "process-exists", "axis": "liveness", "pid": 500,
         "pattern": "ssh"},
        {"id": "t-chan", "kind": "socket-connect", "axis": "transport", "path": "/run/t.sock"},
        {"id": "t-func", "kind": "http-health", "axis": "functional",
         "url": "https://far.example/"},
    ]))
    assert infra.declared_tunnels(doc)[0]["pids"] == [500]


def test_no_pid_is_guessed_from_a_command_line_or_a_port():
    """The join is by PID or not at all. Guessing is the mistake it avoids."""
    row = infra.tunnel_observation(a_forwarding_host(pid=500),
                                   registry_doc=registry_of(tunnel_record()))["declared"][0]
    assert row["declared_pids"] == []
    assert row["actual_state"] == UNKNOWN, "a visible forwarder is not this declaration's"


# ---------------------------------------------------------------------------
# Direction, desired state, freshness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("local,remote,expected", [
    ("127.0.0.1:17890", "far.example:443", "outbound"),
    ("10.0.0.5:9000", "127.0.0.1:443", "inbound"),
    ("127.0.0.1:1", "127.0.0.1:2", "local"),
    ("10.0.0.5:1", "10.0.0.9:2", UNKNOWN),
    ("not-an-endpoint", "far.example:80", UNKNOWN),
    (None, None, UNKNOWN),
])
def test_direction_is_read_from_the_declared_shape(local, remote, expected):
    direction = infra._tunnel_direction(local, remote)
    assert direction["value"] == expected
    assert direction["rule"]
    assert (direction["status"] == UNKNOWN) == (expected == UNKNOWN)


def test_a_shape_reading_is_inferred_and_never_a_measurement():
    """The endpoints are a declaration, so the direction inferred from them
    cannot be a fact about which side initiates."""
    assert infra._tunnel_direction("127.0.0.1:1", "far.example:2")["status"] == INFERRED


def test_a_declared_desired_state_comes_from_the_registration():
    row = the_row(desired="stopped")
    assert row["desired_state"] == "stopped"


def test_the_actual_state_is_measured_and_the_desired_state_is_not_guessed():
    """§13 asks for both, and this row is the pair: nothing declared what this
    tunnel should be, and the measurement is separate from that absence."""
    row = the_row()
    assert row["desired_state"] == "running", "the fixture declares one; the point is the source"
    assert row["actual_state"] in ("RUNNING", "LISTENING", UNKNOWN)


def test_an_observation_older_than_its_window_is_stale_not_absent():
    out = infra.tunnel_observation(
        a_quiet_host(), registry_doc=registry_of(tunnel_record()),
        observed_at="2026-09-14T00:00:00+00:00", now="2026-09-14T03:00:00+00:00")
    freshness = out["declared"][0]["freshness"]
    assert freshness["state"] == "STALE"
    assert freshness["age_seconds"] == 10800


def test_a_reader_with_no_clock_gets_unknown_freshness_and_never_fresh():
    out = infra.tunnel_observation(
        a_quiet_host(), registry_doc=registry_of(tunnel_record()),
        observed_at="2026-09-14T00:00:00+00:00", now=None)
    assert out["declared"][0]["freshness"]["state"] == UNKNOWN


def test_the_window_is_the_observation_window_and_not_the_registrations():
    """Two bars for two questions, and picking the wrong one is silent.

    The registration document changes on the scale of a day; the process table
    describes this second. A tunnel observation whose window was the registry's
    would report this morning's reading as current.
    """
    assert infra.TUNNEL_FRESH_WITHIN_SECONDS == graph.FRESH_WITHIN_SECONDS["docker-inspect"]
    assert infra.TUNNEL_FRESH_WITHIN_SECONDS < graph.FRESH_WITHIN_SECONDS["registry"]


# ---------------------------------------------------------------------------
# Empty, and the note
# ---------------------------------------------------------------------------


def test_an_empty_registry_reports_zero_and_says_this_was_an_observation():
    out = infra.tunnel_observation(a_quiet_host(), registry_doc=registry_of())
    assert out["declared"] == [] and out["declared_count"] == 0
    assert out["undeclared"] == []
    assert out["schema"] == "serveros/tunnel-observation.v1"
    assert "restarted" in out["note"]
    assert "observation" in out["note"]


def test_a_device_with_no_registry_entry_still_produces_a_row_per_tunnel():
    out = infra.tunnel_observation(a_quiet_host(),
                                   registry_doc=registry_of(tunnel_record(device_ref=None)))
    assert out["declared"][0]["device_ref"] is None


def test_the_observation_reports_the_blind_spot_it_cannot_remove():
    """An empty result is only useful if the reader knows a tunnel COULD have
    been found. On this host the answer is "this host cannot prove no tunnel
    exists", and it has to be in the payload to be readable."""
    out = infra.tunnel_observation(a_quiet_host(), registry_doc=registry_of())
    names = [s["what"] for s in out["blind_spots"]]
    assert "sshd-owned forwards" in names
