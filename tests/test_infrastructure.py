#!/usr/bin/env python3
"""Tests for criticality, tunnels and the persistent-infrastructure registry.

Three groups, and each exists because of a specific way this goes wrong:

  * CRITICALITY. §9 is a field that ranks attention and must not confer
    authority. The assertion that matters is mechanical, not descriptive: a
    resource's `is_automatable` must be IDENTICAL before and after its
    criticality is raised. A test that only checked the `confers` paragraph
    would pass on a build where the code reads the field.

  * TUNNELS (§8). The failure being modelled is `liveness: RUNNING` read as
    healthy. So the assertions are about what the health machinery REFUSES to
    say — an unprobed tunnel is DEGRADED, and a tunnel registration that
    provides no way to measure it is rejected outright rather than accepted
    and reported DEGRADED forever.

  * PARTITION (§7). Permanent versus transient, derived from the lifecycle
    class and NOT from criticality, so that a ranking never quietly becomes a
    lifecycle claim.

The contract-agreement tests at the end fail loudly if the vocabulary in
`managed-runtime-contract.yaml` and `attribution-contract.yaml` diverges. Both
files declare `criticality`; two authorities for one word is how a rank becomes
two ranks.
"""
from __future__ import annotations

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
health = _load("serveros_runtime_health", "health.py")

MR_CONTRACT = yaml.safe_load((RUNTIME / "managed-runtime-contract.yaml").read_text())
AT_CONTRACT = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def resource(rid="res-r", *, kind="process", lifecycle="persistent", protection="normal",
             criticality=None, desired="running", probes=None, transport=None,
             not_applicable=None, recovery=None, reconcile=None, owner_ref="project:demo"):
    record = {
        "id": rid,
        "kind": kind,
        "owner": {"type": "project", "ref": owner_ref},
        "scope": "user",
        "lifecycle": {"class": lifecycle},
        "desired": {"state": desired},
        "protection": {"level": protection},
    }
    if criticality:
        record["criticality"] = criticality
    if probes is not None:
        record["probes"] = probes
    if transport:
        record["transport"] = transport
    if not_applicable:
        record["not_applicable"] = not_applicable
    if recovery is not None:
        record["recovery"] = recovery
    if reconcile is not None:
        record["reconcile"] = reconcile
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


def net_tcp(entries):
    """(inode, local, state) triples -> a /proc/net/tcp body."""
    lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
             "retrnsmt   uid  timeout inode"]
    for inode, local_hex, state in entries:
        lines.append(f"  0: {local_hex} 00000000:0000 {state} 00000000:00000000 "
                     f"00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0")
    return "\n".join(lines) + "\n"



def status_line(uid=1006, name="svc"):
    """The only part of /proc/<pid>/status this codebase reads."""
    return f"Name:\t{name}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"

LISTEN = "0A"


# ---------------------------------------------------------------------------
# §9 · Criticality confers attention and nothing else
# ---------------------------------------------------------------------------


def test_raising_criticality_does_not_change_automatability():
    """The mechanical form of §9.

    Not "the docstring says it confers nothing" — the actual computed answer
    for the same resource at every rank, asserted equal. If a future edit makes
    the registry consult criticality, this is the test that fails.
    """
    answers = {}
    for rank in MR_CONTRACT["criticality"]["values"]:
        reg = registry_of(resource("res-r", lifecycle="task-scoped", protection="normal",
                                   criticality=rank))
        answers[rank] = reg.get("res-r").is_automatable
    assert len(set(answers.values())) == 1, answers


def test_a_critical_protected_resource_is_still_not_automatable():
    """§9's last clause, and the one an operator is most likely to assume away."""
    reg = registry_of(resource("res-r", protection="protected", criticality="critical"))
    r = reg.get("res-r")
    assert r.criticality == "critical"
    assert r.is_automatable is False
    assert "protection=protected" in r.why_not_automatable()


def test_criticality_defaults_from_the_lifecycle_class():
    reg = registry_of(
        resource("perm", lifecycle="persistent"),
        resource("task", lifecycle="task-scoped"),
        resource("sess", lifecycle="session-scoped"),
    )
    assert reg.get("perm").criticality == "important"
    assert reg.get("task").criticality == "normal"
    assert reg.get("sess").criticality == "normal"


def test_an_undeclared_criticality_reports_itself_as_derived():
    reg = registry_of(resource("perm", lifecycle="persistent"),
                      resource("decl", lifecycle="persistent", criticality="critical"))
    assert reg.get("perm").criticality_is_declared is False
    assert reg.get("decl").criticality_is_declared is True


def test_an_unknown_criticality_is_rejected():
    """A word outside the closed set is a word, not a rank."""
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("res-r", criticality="urgent"))
    assert "unknown criticality" in str(exc.value)


def test_criticality_never_defaults_to_a_rank_that_sorts_first():
    """Something nobody ranked must not outrank something somebody did."""
    reg = registry_of(resource("unranked", lifecycle="task-scoped"))
    assert reg.get("unranked").criticality != "critical"


def test_the_criticality_report_orders_by_rank_and_carries_the_prohibition():
    reg = registry_of(
        resource("a-normal", criticality="normal"),
        resource("b-critical", criticality="critical"),
        resource("c-important", criticality="important"),
    )
    report = infra.criticality_report(reg)
    assert [r["id"] for r in report["order"]] == ["b-critical", "c-important", "a-normal"]
    assert "PRIORITY OF ATTENTION ONLY" in report["confers"]
    assert "never restarted" in report["confers"]


def test_the_criticality_report_shows_automatability_beside_the_rank():
    """A reader has to be able to see that `critical` changed nothing about
    what may be done, without taking anyone's word for it."""
    # `norm-open` now has to satisfy §10 to be automatable at all: persistent
    # (its default), an explicitly enabled reconcile policy, a budget, and a
    # functional probe. Under the pre-§10 definition the first two lines below
    # would have been enough, and the test would have kept passing while
    # asserting a gate wider than the contract.
    reg = registry_of(resource("crit-prot", criticality="critical", protection="protected"),
                      resource("norm-open", criticality="normal",
                               probes=[{"id": "p", "kind": "process-exists",
                                        "axis": "liveness", "pattern": "demo"},
                                       {"id": "f", "kind": "http-health",
                                        "axis": "functional", "url": "https://demo.invalid/"}],
                               recovery={"model": "restart"},
                               reconcile={"mode": "restart", "max_attempts": 1,
                                          "backoff": [60]}))
    rows = {r["id"]: r for r in infra.criticality_report(reg)["order"]}
    assert rows["crit-prot"]["automatable"] is False
    assert rows["norm-open"]["automatable"] is True


# ---------------------------------------------------------------------------
# §8 · A tunnel is never healthy on liveness
# ---------------------------------------------------------------------------


def test_a_tunnel_registration_must_name_both_endpoints():
    for missing in ("local", "remote"):
        transport = {"class": "ssh", "local": "a:1", "remote": "b:2"}
        del transport[missing]
        with pytest.raises(registry.RegistryInvalid) as exc:
            registry_of(tunnel_record(transport=transport))
        assert missing in str(exc.value)


def test_a_tunnel_with_no_way_to_measure_its_channel_is_rejected():
    """The registration-time cure for the recurring pathology: a kind declared,
    registered, and never made measurable — reported clean forever after."""
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(tunnel_record(probes=[
            {"id": "t-live", "kind": "process-exists", "axis": "liveness", "pattern": "ssh"},
            {"id": "t-func", "kind": "http-health", "axis": "functional", "url": "https://far.example/"},
        ]))
    assert "transport" in str(exc.value)


def test_a_tunnel_may_declare_a_reasoned_not_applicable_transport():
    """The escape hatch is a written decision, not an omission."""
    reg = registry_of(tunnel_record(
        probes=[{"id": "t-func", "kind": "http-health", "axis": "functional", "url": "https://far.example/"}],
        not_applicable={"transport": {"because": "the channel is a Unix socket"}},
    ))
    assert reg.get("tun-1").not_applicable["transport"]["because"]


def test_a_not_applicable_without_a_reason_does_not_satisfy_the_requirement():
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(tunnel_record(
            probes=[{"id": "t-func", "kind": "http-health", "axis": "functional", "url": "https://far.example/"}],
            not_applicable={"transport": {}},
        ))
    assert "no `because`" in str(exc.value)


def test_a_tunnel_must_be_measurable_on_the_functional_axis_by_one_of_two_routes():
    """Either a functional probe, or an explicit statement that there is no
    callable surface. Nothing else — because the third option, silence, is how
    a tunnel ends up permanently DEGRADED and nobody reads the report."""
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(tunnel_record(probes=[
            {"id": "t-live", "kind": "process-exists", "axis": "liveness", "pattern": "ssh"},
            {"id": "t-chan", "kind": "socket-connect", "axis": "transport", "path": "/run/t.sock"},
        ]))
    assert "functional" in str(exc.value)


def test_a_declared_tunnel_with_no_observations_is_degraded_not_healthy():
    """The 2026-09-12 shape, checked against the real rule table."""
    reg = registry_of(tunnel_record(probes=[
        {"id": "t-live", "kind": "process-exists", "axis": "liveness", "pattern": "ssh"},
        {"id": "t-chan", "kind": "socket-connect", "axis": "transport", "path": "/run/t.sock"},
    ], not_applicable={"functional": {"because": "raw TCP forward; no request to make"}}))
    t = reg.get("tun-1")

    h = infra.tunnel_health(t, {
        "liveness": health.Observation(
            axis="liveness", state="RUNNING",
            observed_by={"type": "probe", "id": "t-live"}, observed_at="now",
            evidence={"type": "host-measurement"}),
        "transport": health.Observation(
            axis="transport", state="CONNECTED",
            observed_by={"type": "probe", "id": "t-chan"}, observed_at="now",
            evidence={"type": "host-measurement"}),
    })
    assert h.liveness.state == "RUNNING"
    assert h.functional.state == "NOT_APPLICABLE"
    assert h.state == "DEGRADED"
    assert h.state != "HEALTHY"


def test_a_tunnel_that_is_up_and_answering_is_healthy():
    """The positive control. Without it the DEGRADED assertions above would
    pass on a build that never reports anything as healthy."""
    reg = registry_of(tunnel_record())
    h = infra.tunnel_health(reg.get("tun-1"), {
        "liveness": health.Observation(axis="liveness", state="RUNNING",
                                       observed_by={"type": "probe", "id": "t-live"},
                                       observed_at="now", evidence={"type": "host-measurement"}),
        "transport": health.Observation(axis="transport", state="CONNECTED",
                                        observed_by={"type": "probe", "id": "t-chan"},
                                        observed_at="now", evidence={"type": "host-measurement"}),
        "functional": health.Observation(axis="functional", state="OK",
                                         observed_by={"type": "probe", "id": "t-func"},
                                         observed_at="now", evidence={"type": "provider-response"}),
    })
    assert h.state == "HEALTHY"


def test_an_unobserved_tunnel_axis_stays_unknown_rather_than_being_assumed():
    reg = registry_of(tunnel_record())
    h = infra.tunnel_health(reg.get("tun-1"))
    assert h.liveness.state == "UNKNOWN"
    assert h.state in ("UNKNOWN", "DEGRADED")


def test_desired_stopped_tunnel_is_reported_at_rest_and_not_as_a_fault():
    reg = registry_of(tunnel_record(desired="stopped"))
    h = infra.tunnel_health(reg.get("tun-1"), {
        "liveness": health.Observation(axis="liveness", state="STOPPED",
                                       observed_by={"type": "probe", "id": "t-live"},
                                       observed_at="now", evidence={"type": "host-measurement"}),
    })
    assert h.state == "STOPPED"


# ---------------------------------------------------------------------------
# Forwarder detection — narrow by construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("argv,expected", [
    (["ssh", "-L", "8080:host:80", "me@far"], "ssh"),
    (["ssh", "-L8080:host:80", "-N", "me@far"], "ssh"),
    (["/usr/bin/ssh", "-R", "9000:localhost:22", "far"], "ssh"),
    (["ssh", "-D", "1080", "me@far"], "ssh"),
    (["autossh", "-M", "0", "-R", "1:2:3", "far"], "reverse-ssh"),
    (["socat", "TCP-LISTEN:9000,fork", "TCP:far:9000"], "socat"),
])
def test_forwarding_processes_are_recognised(argv, expected):
    assert infra._forwarder_kind(argv) == expected


@pytest.mark.parametrize("argv", [
    ["ssh", "me@far"],                                  # a login, not a tunnel
    ["ssh", "-o", "ServerAliveInterval=30", "me@far"],  # options, not forwards
    ["/usr/bin/python3", "-m", "http.server"],
    ["git", "clone", "ssh://git@host/repo"],
    # The flags this host actually has. `--start-server` and `-l` open with the
    # right letter and are not ports; a substring rule would call them tunnels.
    ["sh", "/home/hwz/.vscode-server/cli/servers/Stable-x/server/bin/code-server",
     "--agent-host-bridge-port=24991", "--start-server"],
    ["node", "server-main.js", "-L", "--label"],
])
def test_non_forwarding_processes_are_not_tunnels(argv):
    """The negative control. A detector that calls every `ssh` a tunnel reports
    a tunnel count that tracks the number of logins."""
    assert infra._forwarder_kind(argv) is None


def test_forwarders_are_found_from_a_synthetic_process_table():
    host = FakeHost(files={
        "/proc/500/stat": stat_line(pid=500, comm="ssh", ppid=1, starttime=90000),
        "/proc/500/cmdline": "ssh\x00-L\x008080:host:80\x00me@far\x00",
        "/proc/500/status": status_line(),
        "/proc/501/stat": stat_line(pid=501, comm="bash", ppid=1, starttime=90000),
        "/proc/501/cmdline": "/bin/bash\x00",
    })
    found = infra.observe_forwarders(host)
    assert [f["pid"] for f in found] == [500]
    assert found[0]["transport_class"] == "ssh"


# ---------------------------------------------------------------------------
# The blind spot is reported, not silently zero
# ---------------------------------------------------------------------------


def test_unreadable_listeners_are_counted_not_dropped():
    """A listener whose owner this uid cannot see is a fact about the
    measurement, and it must not disappear into an empty result."""
    host = FakeHost(
        files={
            "/proc/500/stat": stat_line(pid=500, comm="svc", ppid=1, starttime=90000),
            "/proc/500/cmdline": "/usr/bin/svc\x00",
            "/proc/500/status": status_line(),
            "/proc/net/tcp": net_tcp([
                ("111", "0100007F:2328", LISTEN),   # 127.0.0.1:9000, held by pid 500
                ("222", "0100007F:36A3", LISTEN),   # 127.0.0.1:13987, held by nobody visible
            ]),
        },
        fds={
            "/proc/500/fd": ["0", "1", "2", "3"],
            "/proc/500/fd/3": "socket:[111]",
        },
    )
    result = infra.unreadable_listeners(host)
    assert result["listening_total"] == 2
    assert result["attributed"] == 1
    assert result["unreadable_count"] == 1
    assert result["unreadable"] == ["127.0.0.1:13987"]


def test_a_single_visible_uid_produces_an_sshd_blind_spot():
    host = FakeHost(
        files={"/proc/500/stat": stat_line(pid=500, comm="svc", ppid=1, starttime=90000),
               "/proc/500/cmdline": "/usr/bin/svc\x00",
               "/proc/500/status": status_line(),
               "/proc/net/tcp": net_tcp([("222", "0100007F:36A3", LISTEN)])},
        fds={"/proc/500/fd": ["0"], "/proc/500/fd/0": "/dev/null"},
    )
    spots = infra.tunnel_blind_spots(host)
    names = [s["what"] for s in spots]
    assert "sshd-owned forwards" in names
    assert "listeners held by no visible process" in names
    sshd = next(s for s in spots if s["what"] == "sshd-owned forwards")
    assert sshd["state"] == "UNKNOWN"


def test_no_blind_spot_is_claimed_when_every_listener_is_attributed():
    """A blind spot that fires unconditionally is a blind spot nobody reads."""
    host = FakeHost(
        files={"/proc/500/stat": stat_line(pid=500, comm="svc", ppid=1, starttime=90000),
               "/proc/500/cmdline": "/usr/bin/svc\x00",
               "/proc/500/status": status_line(),
               "/proc/net/tcp": net_tcp([("111", "0100007F:2328", LISTEN)])},
        fds={"/proc/500/fd": ["3"], "/proc/500/fd/3": "socket:[111]"},
    )
    assert [s["what"] for s in infra.tunnel_blind_spots(host)] == [
        "sshd-owned forwards"
    ]


def test_an_undeclared_visible_forwarder_is_a_candidate_and_not_an_owner():
    reg = registry_of(resource("unrelated", lifecycle="persistent"))
    host = FakeHost(
        files={"/proc/500/stat": stat_line(pid=500, comm="ssh", ppid=1, starttime=90000),
               "/proc/500/cmdline": "ssh\x00-R\x009000:localhost:22\x00far\x00",
               "/proc/500/status": status_line(name="ssh"),
               "/proc/net/tcp": net_tcp([])},
        fds={"/proc/500/fd": ["0"], "/proc/500/fd/0": "/dev/null"},
    )
    inv = infra.tunnel_inventory(host, registry_doc=reg)
    assert inv["declared_count"] == 0
    assert [f["pid"] for f in inv["undeclared_forwarders"]] == [500]
    assert "candidate and never an owner" in inv["note"]


def test_a_declared_tunnel_is_returned_with_both_endpoints():
    reg = registry_of(tunnel_record())
    declared = infra.declared_tunnels(reg)
    assert len(declared) == 1
    assert declared[0]["local"] == "127.0.0.1:9000"
    assert declared[0]["remote"] == "far.example:80"
    assert declared[0]["transport_probe"] == "t-chan"
    assert declared[0]["functional_probe"] == "t-func"


# ---------------------------------------------------------------------------
# §7 · Permanent infrastructure versus expired development runtimes
# ---------------------------------------------------------------------------


def test_partition_uses_the_lifecycle_class_and_not_criticality():
    """The two are checked against each other on purpose: a `critical`
    task-scoped resource must still partition as transient, and the
    contradiction must be REPORTED rather than used to reclassify it."""
    reg = registry_of(
        resource("perm", lifecycle="persistent"),
        resource("task", lifecycle="task-scoped"),
        resource("crit-task", lifecycle="task-scoped", criticality="critical"),
    )
    part = infra.partition(reg)
    assert [r["id"] for r in part["permanent"]] == ["perm"]
    assert sorted(r["id"] for r in part["transient"]) == ["crit-task", "task"]
    assert [r["id"] for r in part["contradictions"]] == ["crit-task"]


def test_a_transient_resource_is_not_thereby_a_reclaim_candidate():
    reg = registry_of(resource("task", lifecycle="task-scoped", protection="protected"))
    row = infra.partition(reg)["transient"][0]
    assert row["protection"] == "protected"
    assert "not a" in infra.partition(reg)["note"].replace("not a reclaim candidate",
                                                           "not a reclaim candidate")


def test_never_reclaim_is_decided_by_protection_alone():
    """Not by criticality, not by age, not by size. A heuristic that is usually
    right is what removes somebody's baseline on the evening it is wrong."""
    reg = registry_of(
        resource("crit-open", criticality="critical", protection="normal"),
        resource("norm-prot", criticality="normal", protection="protected"),
    )
    report = infra.never_reclaim(reg)
    assert [e["id"] for e in report["entries"]] == ["norm-prot"]
    assert report["entries"][0]["because"] == "protection: protected"


def test_never_reclaim_names_the_lane_from_the_typed_ref():
    reg = registry_of(resource("wi-one", protection="protected", owner_ref="work-item:LIT-0104"))
    entry = infra.never_reclaim(reg)["entries"][0]
    assert entry["lane"] == "work-item LIT-0104"


# ---------------------------------------------------------------------------
# §20 · Recovery models
# ---------------------------------------------------------------------------


def test_an_undeclared_recovery_model_defaults_to_restart():
    reg = registry_of(resource("res-r"))
    row = infra.recovery_report(reg)["models"][0]
    assert row["recovery"] == "restart"
    assert row["declared"] is False
    assert row["automatic"] is True


def test_reauthorize_is_never_automatic():
    """§20 in one assertion. This is the RDC case: the supervisor restarted the
    endpoint every ~24 minutes and every restart spent a fresh device code."""
    reg = registry_of(resource("rdc", recovery={"model": "reauthorize",
                                                "because": "the refresh token was consumed"}))
    row = infra.recovery_report(reg)["models"][0]
    assert row["automatic"] is False
    assert "HUMAN" in row["meaning"]


def test_redeclare_and_none_are_never_automatic():
    reg = registry_of(
        resource("a-one", recovery={"model": "redeclare", "because": "declaration is stale"}),
        resource("b-one", recovery={"model": "none", "because": "no path exists"}),
    )
    rows = {r["id"]: r for r in infra.recovery_report(reg)["models"]}
    assert rows["a-one"]["automatic"] is False
    assert rows["b-one"]["automatic"] is False


def test_a_protected_resource_is_not_automatic_even_under_a_reconnect_model():
    reg = registry_of(resource("p-one", protection="protected",
                               recovery={"model": "reconnect", "because": "channel drops"}))
    assert infra.recovery_report(reg)["models"][0]["automatic"] is False


def test_a_recovery_model_without_a_reason_is_rejected():
    """Declaring that restarting will not help is a claim about why, and the
    operator who would otherwise restart it is the reader."""
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("res-r", recovery={"model": "reauthorize"}))
    assert "because" in str(exc.value)


def test_restart_does_not_need_a_reason_and_still_does_not_get_one():
    """The reflex is the default. It is allowed to be the default; it is not
    allowed to be a claim."""
    reg = registry_of(resource("res-r", recovery={"model": "restart"}))
    assert infra.recovery_report(reg)["models"][0]["recovery"] == "restart"


def test_an_empty_recovery_block_is_rejected_rather_than_read_as_restart():
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("res-r", recovery={}))
    assert "model" in str(exc.value)


# ---------------------------------------------------------------------------
# §7 · Per-kind registration requirements for the other two kinds
# ---------------------------------------------------------------------------


def test_an_agent_runtime_must_state_the_evidence_for_its_owner():
    """The 2026-09-13 case in registration form: an unowned long-lived agent
    runtime is exactly the thing that burned 407% CPU for a day and eleven
    hours with nothing on the machine able to say whose work it was."""
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("ar-one", kind="agent-runtime",
                             probes=[{"id": "p", "kind": "process-exists", "axis": "liveness",
                                   "pattern": "app-server"}]))
    assert "owner.because" in str(exc.value)


def test_an_agent_runtime_with_a_stated_owner_and_a_liveness_probe_is_accepted():
    record = resource("ar-one", kind="agent-runtime",
                      probes=[{"id": "p", "kind": "process-exists", "axis": "liveness",
                                   "pattern": "app-server"}])
    record["owner"]["because"] = "cgroup session-2382, which loginctl maps to sshd peer"
    reg = registry_of(record)
    assert reg.get("ar-one").owner["because"]


def test_an_agent_runtime_must_declare_a_liveness_probe():
    record = resource("ar-one", kind="agent-runtime")
    record["owner"]["because"] = "stated"
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(record)
    assert "liveness" in str(exc.value)


def test_a_runtime_group_without_members_is_refused():
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("grp-1", kind="runtime-group"))
    assert "members" in str(exc.value)


def test_a_runtime_group_with_members_is_accepted():
    record = resource("grp-1", kind="runtime-group")
    record["members"] = ["runtime-group:codex-task:01a08f92"]
    assert registry_of(record).get("grp-1").record["members"]


def test_a_kind_with_no_requirements_is_unaffected():
    """The gates must not leak onto the kinds that predate them."""
    reg = registry_of(resource("plain", kind="process"),
                      resource("cont", kind="container"))
    assert len(reg.all()) == 2


def test_an_unknown_requirement_shape_is_a_contract_defect_not_a_silent_pass(
        monkeypatch, tmp_path):
    """A requirement nothing understands is a requirement nothing enforces."""
    broken = dict(MR_CONTRACT)
    broken["kind_requirements"] = {"process": {"requires": [{"nonsense: 1": True}]}}
    monkeypatch.setattr(registry, "contract", lambda: broken)
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("p-one", kind="process"))
    assert "unknown kind requirement" in str(exc.value)


# ---------------------------------------------------------------------------
# A probe that cannot run is not coverage
# ---------------------------------------------------------------------------


def test_a_probe_missing_a_field_it_needs_to_run_is_refused():
    """Found by writing a registration rather than by reading the code.

    A `process-exists` probe with no `pattern` validated cleanly, was declared
    as liveness coverage, and reached `pgrep -f None` — a TypeError inside the
    runner, on the first run. The registration was accepted; the measurement
    was absent.
    """
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("res-r", probes=[
            {"id": "p", "kind": "process-exists", "axis": "liveness"},
        ]))
    assert "`pattern`" in str(exc.value)


@pytest.mark.parametrize("kind,field", [
    ("systemd-unit-state", "unit"),
    ("container-state", "container"),
    ("container-healthcheck", "container"),
    ("http-health", "url"),
    ("mcp-tools-list", "url"),
    ("socket-connect", "path"),
    ("journal-diagnostic", "unit"),
])
def test_every_probe_kind_refuses_a_registration_it_could_not_run(kind, field):
    axis = "reason" if kind == "journal-diagnostic" else "liveness"
    with pytest.raises(registry.RegistryInvalid) as exc:
        registry_of(resource("res-r", probes=[{"id": "p", "kind": kind, "axis": axis}]))
    assert f"`{field}`" in str(exc.value)


def test_relay_socket_needs_both_of_its_two_fields():
    for missing in ("pattern", "peer_host"):
        spec = {"id": "p", "kind": "relay-socket", "axis": "transport",
                "pattern": "x", "peer_host": "y"}
        del spec[missing]
        with pytest.raises(registry.RegistryInvalid) as exc:
            registry_of(resource("res-r", probes=[spec]))
        assert missing in str(exc.value)


def test_an_emptied_field_is_missing_not_present():
    """`pattern: ""` is a declaration that says nothing, and `pgrep -f ""`
    matches every process on the host — the answer would be YES always."""
    with pytest.raises(registry.RegistryInvalid):
        registry_of(resource("res-r", probes=[
            {"id": "p", "kind": "process-exists", "axis": "liveness", "pattern": ""},
        ]))


def test_relay_presence_is_declared_empty_on_purpose():
    """It genuinely needs nothing, and the empty list records that somebody
    considered it — an absent key and an empty one read differently."""
    assert MR_CONTRACT["probe_requirements"]["relay-presence"] == []


def test_every_probe_kind_the_contract_declares_has_a_requirements_entry():
    declared = set(MR_CONTRACT["probe_kinds"])
    covered = set(MR_CONTRACT["probe_requirements"])
    assert declared == covered, declared ^ covered


def test_the_requirements_only_name_fields_the_probe_classes_actually_read():
    """A required field nothing reads is a field an author fills in for
    nothing, and it teaches them the requirements are noise."""
    source = (RUNTIME / "probes.py").read_text()
    for kind, fields in MR_CONTRACT["probe_requirements"].items():
        for field in fields:
            assert f'"{field}"' in source, (kind, field)


# ---------------------------------------------------------------------------
# One vocabulary, two files
# ---------------------------------------------------------------------------


def test_the_two_contracts_agree_on_the_criticality_values():
    assert (MR_CONTRACT["criticality"]["values"]
            == AT_CONTRACT["criticality"]["values"])


def test_the_two_contracts_agree_on_the_criticality_meanings():
    assert (set(MR_CONTRACT["criticality"]["meaning"])
            == set(AT_CONTRACT["criticality"]["meaning"]))


def test_the_kinds_the_new_code_handles_are_declared():
    kinds = set(MR_CONTRACT["kinds"])
    assert {"tunnel", "agent-runtime", "runtime-group"} <= kinds


def test_every_kind_requirement_names_a_requirement_the_checker_implements():
    """The `kind_requirements` block is data the checker executes. A key it
    does not know would be silently ignored, which is the declared-but-absent
    pathology one more time — so the vocabulary is closed and asserted."""
    known = {"field", "probe_axis", "decisive_owner", "because", "unless_not_applicable"}
    for kind, spec in MR_CONTRACT["kind_requirements"].items():
        for req in spec.get("requires") or ():
            assert set(req) <= known, (kind, req)
        for req in spec.get("requires_one_of") or ():
            assert set(req) <= {"probe_axis", "not_applicable"}, (kind, req)


def test_every_recovery_model_names_whether_it_is_automatic():
    for model in MR_CONTRACT["recovery_models"]:
        assert model["id"]
        assert model["automatic"] in ("never", "allowed_unless_protected")


def test_the_recovery_model_ids_are_unique():
    ids = [m["id"] for m in MR_CONTRACT["recovery_models"]]
    assert len(ids) == len(set(ids))


def test_forbids_healthy_without_axis_is_backed_by_a_measurable_route():
    """`forbids_healthy_without: functional` is a claim about the rule table.
    It is only true if the same block also provides a way to measure or to
    exempt that axis; otherwise it is a sentence."""
    for kind, spec in MR_CONTRACT["kind_requirements"].items():
        axis = spec.get("forbids_healthy_without")
        if not axis:
            continue
        routes = [
            r.get("probe_axis") or r.get("not_applicable")
            for r in spec.get("requires_one_of") or ()
        ]
        assert axis in routes, (kind, axis, routes)
