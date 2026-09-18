#!/usr/bin/env python3
"""Tests for runtime attribution and the runtime graph.

Hermetic. Every host read is served from dictionaries, so nothing here touches
`/proc`, `loginctl`, or the network. The fixtures build synthetic `/proc`
lines rather than mocking the parser, because the parser is the part most
likely to be wrong and mocking it would test the mock.

Three of these are regression tests for defects found by running the code
against the live host — each names the incident:

  * a pid-1 root reported as `orphaned`, which would have flagged every
    correctly-daemonised service on the machine as abandoned;
  * six groups sharing one ref, so `runtime group <ref>` answered about
    whichever sorted first;
  * three MCP servers all named `python3`, because the executable basename
    of an interpreted script is a locator and not a name.

The fixture host also models this machine's real constraint: `/proc` is
mounted `hidepid=invisible`, so PID 1 is unreadable. Several tests exist
purely to assert that an unreadable ancestor is reported as UNKNOWN rather
than as an absence.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


att = _load("serveros_runtime_attribution", "attribution.py")
graph = _load("serveros_runtime_graph", "graph.py")


# ---------------------------------------------------------------------------
# A synthetic host
# ---------------------------------------------------------------------------


def stat_line(*, pid, comm, state="S", ppid, pgrp=0, session=0, utime=0,
              stime=0, num_threads=1, starttime=0, rss_pages=0):
    """Build one `/proc/<pid>/stat` line in the kernel's real layout.

    Built by field NUMBER, not by a hand-counted run of zeros. The first
    version of this helper counted wrong by one — the run of dead fields
    between `ppid` and `utime` is five, not four — and the parser correctly
    raised `IndexError` on the malformed line. The parser was right and the
    fixture was wrong, which is the direction that matters: a fixture that
    agrees with a broken parser is worse than no fixture.
    """
    values = {n: 0 for n in range(3, 25)}
    values.update({
        3: state, 4: ppid,                     # state, ppid
        # Defaulted to 0 rather than to a realistic value, because 0 is what
        # this builder produced before the parser read these two columns and
        # every fixture in the file predates them. The point of naming them is
        # that 0 is not a session: a raw line from `/proc` always carries the
        # pid of some session leader here, so a test that cares about the
        # distinction has to say so explicitly.
        5: pgrp, 6: session,                   # pgrp, session
        14: utime, 15: stime,                  # utime, stime
        18: 20,                                # priority
        20: num_threads,                       # num_threads
        22: starttime, 24: rss_pages,          # starttime, rss
    })
    return f"{pid} ({comm}) " + " ".join(str(values[n]) for n in range(3, 25)) + "\n"


class FakeHost:
    """A `/proc` served from dictionaries. Any unconfigured read is empty.

    Empty rather than plausible: a fake that invents an answer lets a test
    pass for the wrong reason, which is the failure mode this whole exercise
    is about.
    """

    def __init__(self, procs, *, sessions=None, uptime=100000.0,
                 net_tcp="", net_unix="", mounts="proc /proc proc rw,hidepid=invisible 0 0\n",
                 page_size=4096):
        self.procs = procs
        self.sessions = sessions or {}
        self._uptime = uptime
        self.net_tcp = net_tcp
        self.net_unix = net_unix
        self.mounts = mounts
        self._page_size = page_size

    def uptime(self):
        return self._uptime

    def page_size(self):
        return self._page_size

    def uid_name(self, uid):
        return {1006: "hwz", 0: "root"}.get(uid, str(uid))

    def listdir(self, path):
        if path == "/proc":
            return [str(p) for p in self.procs]
        if path.startswith("/proc/") and path.endswith("/fd"):
            pid = int(path.split("/")[2])
            return [str(i) for i in (self.procs.get(pid, {}).get("fds") or {})]
        return []

    def read_text(self, path):
        if path == "/proc/uptime":
            return f"{self._uptime} 0.0\n"
        if path == "/proc/mounts":
            return self.mounts
        if path == "/proc/net/tcp":
            return self.net_tcp
        if path == "/proc/net/unix":
            return self.net_unix
        if path.startswith("/proc/"):
            parts = path.split("/")
            pid = int(parts[2])
            record = self.procs.get(pid)
            if record is None:
                return None
            if parts[3] == "stat":
                return stat_line(pid=pid, **record["stat"])
            if parts[3] == "cmdline":
                return "\0".join(record.get("argv", [])) + "\0"
            if parts[3] == "cgroup":
                return f"0::{record.get('cgroup', '')}\n"
            if parts[3] == "status":
                return f"Name:\tx\nUid:\t{record.get('uid', 1006)}\t1\t1\t1\n"
        return None

    def readlink(self, path):
        if path.startswith("/proc/") and "/fd/" in path:
            pid = int(path.split("/")[2])
            fd = path.rsplit("/", 1)[-1]
            return (self.procs.get(pid, {}).get("fds") or {}).get(fd)
        if path.startswith("/proc/") and path.endswith("/cwd"):
            pid = int(path.split("/")[2])
            return self.procs.get(pid, {}).get("cwd")
        return None

    def run(self, args, timeout=8.0):
        if args and args[0] == "loginctl" and "show-session" in args:
            session = args[args.index("show-session") + 1]
            record = self.sessions.get(str(session))
            if record is None:
                return 1, "", f"Session {session} not found."
            body = "".join(f"{k}={v}\n" for k, v in record.items())
            return 0, body, ""
        return 127, "", "not configured"


# ---------------------------------------------------------------------------
# The case this module was written for
#
# Reproduced from the live host on 2026-09-13. The values are the real ones:
# a remote Codex app-server tree, 407% CPU, one day eleven hours old, whose
# origin session is closing.
# ---------------------------------------------------------------------------


CODEX_SCOPE = "/user.slice/user-1006.slice/session-2382.scope"
# uptime at the moment of measurement. The tree's measured life was
# 1d11h40m48s, so its starttime is uptime minus that, in clock ticks.
UPTIME = 128651.6
CODEX_LIFE_TICKS = 12864000       # 1d11h40m
CODEX_STARTTIME = int(UPTIME * 100) - CODEX_LIFE_TICKS
# 407.2% of one core sustained over that whole life, in clock ticks.
CODEX_UTIME = 42356480
CODEX_STIME = 10000000


@pytest.fixture
def codex_host():
    return FakeHost(
        {
            178963: {
                "stat": {"comm": "MainThread", "ppid": 1, "starttime": CODEX_STARTTIME,
                         "utime": 0, "stime": 0, "rss_pages": 700},
                "argv": ["node", "/x/codex.js", "app-server", "--listen", "unix://"],
                "cgroup": CODEX_SCOPE,
                "uid": 1006,
            },
            178978: {
                "stat": {"comm": "codex", "ppid": 178963, "starttime": CODEX_STARTTIME,
                         "utime": CODEX_UTIME, "stime": CODEX_STIME, "rss_pages": 80000},
                "argv": ["/x/codex-linux-x64/bin/codex", "app-server", "--listen", "unix://"],
                "cgroup": CODEX_SCOPE,
                "uid": 1006,
            },
            # Started a thousand seconds after its supervisor — which is what
            # makes the group-age test below mean something. A child that
            # started later must not make the group look younger than the
            # thing that started it.
            684890: {
                "stat": {"comm": "codex-code-mode", "ppid": 178978,
                         "starttime": CODEX_STARTTIME + 100000,
                         "utime": 0, "stime": 0, "rss_pages": 2900},
                "argv": ["/x/codex-code-mode-host"],
                "cgroup": CODEX_SCOPE,
                "uid": 1006,
            },
        },
        sessions={"2382": {"Id": "2382", "User": "1006", "Name": "hwz", "Remote": "yes",
                           "RemoteHost": "203.0.113.251", "Service": "sshd",
                           "Type": "tty", "Active": "yes", "State": "closing",
                           "TTY": ""}},
        uptime=UPTIME,
    )


# ---------------------------------------------------------------------------
# cgroup parsing — the load-bearing pure functions
# ---------------------------------------------------------------------------


def test_a_session_scope_is_read_from_the_cgroup():
    assert att.session_of_cgroup(CODEX_SCOPE) == "2382"
    assert att.session_of_cgroup("/user.slice/user@1006.service") is None


def test_a_container_id_is_read_from_the_cgroup():
    cid = "a" * 64
    assert att.container_of_cgroup(f"/system.slice/docker-{cid}.scope") == cid
    assert att.container_of_cgroup(CODEX_SCOPE) is None


def test_the_innermost_unit_wins_over_the_login_manager():
    """`user@1006.service` supervises every login on the host. Answering with
    it would name the login manager as the supervisor of dbus."""
    cgroup = "/user.slice/user-1006.slice/user@1006.service/app.slice/dbus.service"
    assert att.unit_of_cgroup(cgroup) == "dbus.service"


def test_a_bare_scope_is_not_reported_as_a_unit():
    """A `.scope` is the kernel's grouping, not a supervised unit. Calling one
    a unit would overstate what was recorded."""
    assert att.unit_of_cgroup(CODEX_SCOPE) is None


# ---------------------------------------------------------------------------
# stat parsing
# ---------------------------------------------------------------------------


def test_a_process_name_containing_spaces_and_parens_is_parsed():
    """A process can name itself `foo) bar`. Splitting on whitespace, which is
    the obvious implementation, misreads every field after the name."""
    raw = stat_line(pid=42, comm="foo) bar", ppid=7, utime=5, stime=3, starttime=100)
    parsed = att.parse_stat(raw)
    assert parsed["comm"] == "foo) bar"
    assert parsed["ppid"] == 7
    assert parsed["utime"] == 5
    assert parsed["starttime"] == 100


def test_the_parser_reads_the_process_group_and_the_session():
    """Fields 5 and 6. These are the two columns that answer "did this
    process detach on purpose", and they sit immediately after `ppid`, which
    is the field most likely to be read by hand and the reason they were
    skipped for so long. Read from the kernel's layout, not counted."""
    parsed = att.parse_stat(stat_line(pid=42, comm="x", ppid=1, pgrp=42,
                                      session=42))
    assert parsed["pgrp"] == 42
    assert parsed["session"] == 42

    # Still in the right columns when the name shifts everything a naive
    # split would have counted on.
    shifted = att.parse_stat(stat_line(pid=43, comm="my prog", ppid=1,
                                       pgrp=7, session=9))
    assert shifted["pgrp"] == 7
    assert shifted["session"] == 9


# ---------------------------------------------------------------------------
# CPU: what "sustained" is allowed to mean
# ---------------------------------------------------------------------------


def test_cpu_is_a_lifetime_average_not_a_sample():
    """A single sample of a busy process cannot support the word "sustained" —
    a build is busy for a minute and then stops. Lifetime average over a known
    long life can, and it needs no sampling loop."""
    # 500_000 ticks of CPU over ~1000s at 100 Hz = 5 cores' worth
    percent = att.lifetime_cpu_percent(utime=400000, stime=100000,
                                       starttime=0, uptime=1000.0)
    assert percent == pytest.approx(500.0, abs=0.5)


def test_a_process_that_has_not_used_cpu_reports_zero():
    assert att.lifetime_cpu_percent(0, 0, 0, 1000.0) == 0.0


def test_a_degenerate_elapsed_time_does_not_divide_by_zero():
    """`starttime` in the future is malformed, not a crash."""
    assert att.lifetime_cpu_percent(10, 10, 10 ** 9, 1.0) == 0.0


# ---------------------------------------------------------------------------
# §2/§4 · the attribution chain, on the real case
# ---------------------------------------------------------------------------


def test_the_remote_codex_tree_is_attributed_to_its_origin_session(codex_host):
    table = att.process_table(codex_host)
    record = att.attribute_process(178978, codex_host, table=table)

    assert record["runtime"]["pid"] == 178978
    assert record["runtime"]["ppid"] == 178963
    assert record["session_ref"] == "session:2382"
    assert record["owner"]["ref"] == "session:2382"
    # Read from the kernel's own grouping, so this is provenance, not a guess.
    assert "cgroup" in record["attribution"]["provenance"]
    assert record["attribution"]["confidence"] == "high"


def test_the_origin_device_is_inferred_from_the_ssh_peer_and_says_so(codex_host):
    """The address is a fact. Which DEVICE it is, is not — and the record has
    to keep those apart or the address becomes an identity by repetition."""
    table = att.process_table(codex_host)
    record = att.attribute_process(178978, codex_host, table=table)
    device = record["origin"]["device_ref"]

    assert device["status"] == "INFERRED"
    assert device["rule"] == "ssh-session-remote-host"
    assert "203.0.113.251" in device["value"]


def test_a_local_login_is_reported_as_local_not_as_unknown():
    host = FakeHost(
        {10: {"stat": {"comm": "bash", "ppid": 1, "starttime": 0},
              "argv": ["bash"], "cgroup": "/user.slice/user-1006.slice/session-5.scope",
              "uid": 1006}},
        sessions={"5": {"Id": "5", "User": "1006", "Name": "hwz", "Remote": "no",
                        "RemoteHost": "", "Service": "login", "Type": "tty",
                        "Active": "yes", "State": "active", "TTY": "pts/0"}},
    )
    table = att.process_table(host)
    record = att.attribute_process(10, host, table=table)
    assert record["origin"]["device_ref"]["value"] == "device:local"
    assert record["origin"]["device_ref"]["status"] == "FACT"


def test_a_process_that_vanished_is_unknown_rather_than_absent(codex_host):
    """A pid that exited between the listing and the read is not a process
    with no attributes."""
    table = att.process_table(codex_host)
    record = att.attribute_process(999999, codex_host, table=table)
    assert record["status"] == "UNKNOWN"
    assert record["attribution"]["confidence"] == "unknown"


# ---------------------------------------------------------------------------
# The visibility boundary
# ---------------------------------------------------------------------------


def test_hidepid_is_reported_so_an_absence_is_not_read_as_a_measurement(codex_host):
    """This host mounts /proc with hidepid=invisible. A process this code
    cannot find is UNKNOWN, never ABSENT — and a reader who is not told the
    boundary will read the empty list the other way."""
    table = att.process_table(codex_host)
    cov = att.coverage(codex_host, table)
    assert cov["hidepid_invisible"] is True
    assert "evidence of absence" in cov["blind_spot"].lower()


def test_without_hidepid_the_blind_spot_says_none_declared(codex_host):
    codex_host.mounts = "proc /proc proc rw,relatime 0 0\n"
    table = att.process_table(codex_host)
    assert att.coverage(codex_host, table)["hidepid_invisible"] is False


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def test_reparenting_to_init_is_reported_without_calling_it_abandoned(codex_host):
    """Regression: the first version returned `orphaned: true` for any pid-1
    root, which would have flagged every correctly-daemonised service on the
    host as abandoned. What the lineage proves is narrower than that, and the
    conclusion belongs to the anomaly layer where it can be combined with a
    session state and a task state."""
    table = att.process_table(codex_host)
    tree = att.process_tree(178963, table)
    assert tree["reparented_to_init"] is True
    assert "orphaned" not in tree


def test_the_chain_says_whether_it_reached_the_top_or_ran_out_of_permission(codex_host):
    """PID 1 is unreadable under hidepid. "We reached init" and "we could not
    read the ancestor" are different facts and must not render the same."""
    table = att.process_table(codex_host)
    tree = att.process_tree(178963, table)
    assert tree["chain_complete"] is False
    assert tree["chain_terminated_because"] == "ancestor_not_readable"


def test_a_daemonised_root_is_not_reparented_by_accident(codex_host):
    codex_host.procs[178963]["stat"]["ppid"] = 999
    table = att.process_table(codex_host)
    assert att.process_tree(178963, table)["reparented_to_init"] is False


def test_a_root_that_is_its_own_session_leader_is_reported_as_daemonised(codex_host):
    """The fact the lineage cannot supply, so the tree reports it.

    A `setsid()` caller is a session leader, which the kernel records by
    making its session id equal its own pid. A process whose session leader
    died keeps the DEAD leader's id instead. Together with `reparented_to_init`
    that is enough to separate a daemon from a leftover, and neither half is
    a process name.
    """
    codex_host.procs[178963]["stat"]["session"] = 178963
    table = att.process_table(codex_host)
    tree = att.process_tree(178963, table)
    assert tree["root_session"] == 178963
    assert tree["root_owns_its_session"] is True

    # The same root carrying somebody else's session id is the leftover
    # shape, and the reader gets that answer from the same two fields.
    codex_host.procs[178963]["stat"]["session"] = 999999
    table = att.process_table(codex_host)
    tree = att.process_tree(178963, table)
    assert tree["root_session"] == 999999
    assert tree["root_owns_its_session"] is False


INSTANCE = "/user.slice/user-1004.slice/user@1004.service"
TMUX_SCOPE = INSTANCE + "/tmux-spawn-abc.scope"
INSTANCE_INIT = INSTANCE + "/init.scope"


def instance_host(cgroup, *, live, root_pid=178963):
    """A host whose root sits in `cgroup`, and optionally a live instance.

    `live` adds one process sitting in the instance's own `init.scope`, which
    is what a running `user@<uid>.service` leaves behind in the cgroup tree.
    """
    # `pid` is deliberately not in the stat record: `FakeHost` already passes
    # it from the key, and repeating it is a TypeError rather than a wrong
    # answer, which is the good kind of fixture bug.
    procs = {
        root_pid: {"stat": {"comm": "tmux", "ppid": 1, "session": root_pid},
                   "cgroup": cgroup},
    }
    if live:
        procs[1] = {"stat": {"comm": "systemd", "ppid": 0, "session": 1},
                    "cgroup": INSTANCE_INIT}
    return FakeHost(procs)


def test_a_live_user_instance_is_read_from_the_cgroup_tree():
    """`init.scope` under the instance exists for as long as the instance does.

    Read from the process table rather than by asking `systemctl`, so the
    answer is the same observation the rest of the record came from and cannot
    be a second, later reading.
    """
    table = att.process_table(instance_host(TMUX_SCOPE, live=True))
    assert att.process_tree(178963, table)["owner_instance_live"] is True

    table = att.process_table(instance_host(TMUX_SCOPE, live=False))
    assert att.process_tree(178963, table)["owner_instance_live"] is False

    # A cgroup that is not under a user instance has no instance to be alive.
    # None rather than False, so "there is no instance" and "the instance is
    # gone" stay distinguishable to a reader of the record.
    table = att.process_table(instance_host("/system.slice/docker.service",
                                            live=True))
    assert att.process_tree(178963, table)["owner_instance_live"] is None


def test_a_short_table_does_not_read_as_a_live_instance():
    """Under `hidepid` a table can be missing rows. Missing is not alive.

    Reporting True here would exempt every runtime scope on the host the
    moment the instance's own processes became unreadable, which is the
    failure mode this file exists to avoid: an absence rendered as a pass.
    """
    host = instance_host(TMUX_SCOPE, live=True)
    del host.procs[1]
    table = att.process_table(host)
    assert att.process_tree(178963, table)["owner_instance_live"] is False


def test_the_process_tree_is_nested_and_complete(codex_host):
    table = att.process_table(codex_host)
    tree = att.process_tree(178963, table)["tree"]
    assert tree["pid"] == 178963
    assert tree["children"][0]["pid"] == 178978
    assert tree["children"][0]["children"][0]["pid"] == 684890


def test_an_ancestry_cycle_terminates_instead_of_hanging():
    """A racing read can produce ppid pointing at a descendant. An unbounded
    walk would hang the daemon on the one host state where it is most needed."""
    host = FakeHost({
        1: {"stat": {"comm": "a", "ppid": 2, "starttime": 0}, "argv": ["a"], "cgroup": "", "uid": 1006},
        2: {"stat": {"comm": "b", "ppid": 1, "starttime": 0}, "argv": ["b"], "cgroup": "", "uid": 1006},
    })
    table = att.process_table(host)
    chain = att.ancestry(1, table)
    assert len(chain) <= 64
    assert chain[0] == 1


# ---------------------------------------------------------------------------
# §5 · runtime groups
# ---------------------------------------------------------------------------


def test_the_codex_tree_forms_one_group_not_three_pids(codex_host):
    """The whole point. Read as three rows in `ps` this is three harmless
    processes; read as one group it is a remote agent runtime whose origin
    session is closing."""
    groups = graph.build_groups(att.process_table(codex_host))
    assert len(groups) == 1
    assert groups[0]["members"] == [178963, 178978, 684890]
    assert groups[0]["root_pid"] == 178963


def test_group_cpu_is_summed_not_averaged(codex_host):
    """Four processes at 100% is four cores' worth of a machine. Averaging
    hides exactly the case this is built to find."""
    groups = graph.build_groups(att.process_table(codex_host))
    agg = groups[0]["aggregate"]
    assert agg["cpu_is_a_sum"] is True
    assert agg["cpu_percent"] > 400


def test_group_age_is_the_oldest_member_not_the_youngest(codex_host):
    """A supervisor that restarts its worker every minute is a day-old group.

    The helper here started 1000s after its supervisor. Taking the youngest
    member would report a group that is a day and a half old as an hour old.
    """
    table = att.process_table(codex_host)
    groups = graph.build_groups(table)
    ages = {p: table[p].age_seconds for p in groups[0]["members"]}
    assert ages[684890] < ages[178963]          # the fixture must differ
    assert groups[0]["aggregate"]["age_seconds"] == max(ages.values())
    assert groups[0]["aggregate"]["age_seconds"] == pytest.approx(128640.0, abs=1.0)


def test_two_sessions_never_merge_into_one_group():
    """Two processes named `codex` in different login sessions are different
    groups. The kernel's scope is the boundary and it does not move."""
    host = FakeHost({
        10: {"stat": {"comm": "codex", "ppid": 1, "starttime": 0}, "argv": ["codex"],
             "cgroup": "/user.slice/user-1006.slice/session-1.scope", "uid": 1006},
        20: {"stat": {"comm": "codex", "ppid": 1, "starttime": 0}, "argv": ["codex"],
             "cgroup": "/user.slice/user-1006.slice/session-2.scope", "uid": 1006},
    })
    groups = graph.build_groups(att.process_table(host))
    assert len(groups) == 2
    assert {g["session_ref"] for g in groups} == {"session:1", "session:2"}


def test_several_roots_in_one_scope_get_distinct_refs():
    """Regression, found on the live host: six orphaned processes in one
    session scope each became a group sharing the ref
    `process-tree:session-106`, so `runtime group <ref>` answered about
    whichever sorted first and nothing reported the collision."""
    host = FakeHost({
        10: {"stat": {"comm": "a", "ppid": 1, "starttime": 0}, "argv": ["a"],
             "cgroup": "/user.slice/user-1006.slice/session-7.scope", "uid": 1006},
        11: {"stat": {"comm": "b", "ppid": 1, "starttime": 0}, "argv": ["b"],
             "cgroup": "/user.slice/user-1006.slice/session-7.scope", "uid": 1006},
    })
    groups = graph.build_groups(att.process_table(host))
    refs = [g["ref"] for g in groups]
    assert len(refs) == len(set(refs)) == 2
    assert all("pid" in r for r in refs)


def test_a_single_root_scope_keeps_the_clean_ref():
    """The disambiguating suffix is only added when it is needed."""
    host = FakeHost({
        10: {"stat": {"comm": "a", "ppid": 1, "starttime": 0}, "argv": ["a"],
             "cgroup": "/user.slice/user-1006.slice/session-7.scope", "uid": 1006},
        11: {"stat": {"comm": "b", "ppid": 10, "starttime": 0}, "argv": ["b"],
             "cgroup": "/user.slice/user-1006.slice/session-7.scope", "uid": 1006},
    })
    refs = [g["ref"] for g in graph.build_groups(att.process_table(host))]
    assert refs == ["runtime-group:process-tree:session-7"]


def test_a_group_is_named_after_its_agent_only_when_all_members_agree():
    host = FakeHost({
        10: {"stat": {"comm": "codex", "ppid": 1, "starttime": 0},
             "argv": ["/x/codex", "app-server"], "cgroup": "/user.slice/user-1006.slice/session-1.scope", "uid": 1006},
        11: {"stat": {"comm": "claude", "ppid": 10, "starttime": 0},
             "argv": ["/x/claude"], "cgroup": "/user.slice/user-1006.slice/session-1.scope", "uid": 1006},
    })
    groups = graph.build_groups(att.process_table(host))
    assert groups[0]["agent"]["value"] is None
    assert groups[0]["kind"] == "process-tree"


# ---------------------------------------------------------------------------
# MCP identification
# ---------------------------------------------------------------------------


def test_an_interpreted_mcp_server_is_named_by_its_script():
    """Regression: three MCP servers on this host all reported as `python3`,
    because the executable basename of an interpreted script is a locator and
    not a name."""
    host = FakeHost({
        10: {"stat": {"comm": "python3", "ppid": 1, "starttime": 0},
             "argv": ["/usr/bin/python3", "/home/hwz/.local/bin/browser-use", "--mcp"],
             "cgroup": "", "uid": 1006},
    })
    table = att.process_table(host)
    found = graph.identify_mcp(table[10])
    assert found["name"] == "browser-use"


def test_a_compiled_mcp_server_is_named_by_its_executable():
    host = FakeHost({
        10: {"stat": {"comm": "server", "ppid": 1, "starttime": 0},
             "argv": ["/opt/tools/archguard-server", "--mcp"], "cgroup": "", "uid": 1006},
    })
    table = att.process_table(host)
    assert graph.identify_mcp(table[10])["name"] == "archguard-server"


def test_a_plain_browser_is_not_an_mcp_server():
    host = FakeHost({
        10: {"stat": {"comm": "chrome", "ppid": 1, "starttime": 0},
             "argv": ["/x/chrome", "--headless"], "cgroup": "", "uid": 1006},
    })
    table = att.process_table(host)
    assert graph.identify_mcp(table[10]) is None


def test_the_mcp_name_is_labelled_as_a_locator_not_an_identity(codex_host):
    host = FakeHost({
        10: {"stat": {"comm": "python3", "ppid": 1, "starttime": 0},
             "argv": ["/usr/bin/python3", "/x/meta-cc", "--mcp"], "cgroup": "", "uid": 1006},
    })
    table = att.process_table(host)
    assert graph.identify_mcp(table[10])["name_is_a_locator_not_an_identity"] is True


# ---------------------------------------------------------------------------
# §4 · the chain, and the link that must stay UNKNOWN
# ---------------------------------------------------------------------------


def test_the_chain_carries_a_status_on_every_link(codex_host):
    """A link with a value and no status is the failure mode this design
    exists to prevent, so every one of them is checked."""
    table = att.process_table(codex_host)
    group = graph.find_group("session-2382", table)
    chain = graph.build_chain(group, table, host=codex_host)
    for name, link in chain.items():
        assert link["status"] in ("FACT", "INFERRED", "UNKNOWN"), name


def test_the_task_link_is_unknown_and_says_why(codex_host):
    """§4, and the point of it: this host's Codex app-server names no task
    anywhere a user-space reader can reach, so a task name would be a guess.
    A report that said `task: litellm-image-build v4` would be believed."""
    table = att.process_table(codex_host)
    chain = graph.build_chain(graph.find_group("session-2382", table), table,
                              host=codex_host)
    assert chain["task"]["value"] is None
    assert chain["task"]["status"] == "UNKNOWN"
    assert "guess" in chain["task"]["why"]


def test_a_task_id_in_argv_is_inferred_and_not_promoted_to_a_fact():
    host = FakeHost({
        10: {"stat": {"comm": "codex", "ppid": 1, "starttime": 0},
             "argv": ["/x/codex", "app-server", "--task",
                      "01a08f92-b7ce-7b00-a4f2-8074ec6752bc"],
             "cgroup": "/user.slice/user-1006.slice/session-1.scope", "uid": 1006},
    })
    table = att.process_table(host)
    chain = graph.build_chain(graph.build_groups(table)[0], table, host=host)
    assert chain["task"]["value"] == "01a08f92-b7ce-7b00-a4f2-8074ec6752bc"
    # A UUID in an argument is evidence, not a claim that it is a task id.
    assert chain["task"]["status"] == "INFERRED"


def test_the_chain_reports_the_origin_session_state(codex_host):
    """`closing` is the load-bearing value: the login is on its way out while
    its scope's processes are still burning CPU."""
    table = att.process_table(codex_host)
    chain = graph.build_chain(graph.find_group("session-2382", table), table,
                              host=codex_host)
    assert chain["origin_session_state"]["value"] == "closing"
    assert chain["origin_session_state"]["status"] == "FACT"


def test_an_unmapped_peer_address_stays_an_address(codex_host):
    """The address is a fact; which device it is, is not. Without a declared
    mapping the device is identified by address and nothing more."""
    table = att.process_table(codex_host)
    chain = graph.build_chain(graph.find_group("session-2382", table), table,
                              host=codex_host)
    assert chain["device"]["value"] == "ip:203.0.113.251"
    assert "no declared mapping" in chain["device"]["why"]


def test_a_declared_endpoint_map_resolves_the_device(codex_host):
    table = att.process_table(codex_host)
    chain = graph.build_chain(
        graph.find_group("session-2382", table), table, host=codex_host,
        endpoint_map={"203.0.113.251": "device:work-fj"},
    )
    assert chain["device"]["value"] == "device:work-fj"
    # Still INFERRED: a declaration is stronger evidence than a guess and
    # weaker than a measurement, and the record says which it is.
    assert chain["device"]["status"] == "INFERRED"


def test_a_group_with_no_session_reports_unknown_origin_rather_than_nothing():
    host = FakeHost({
        10: {"stat": {"comm": "x", "ppid": 1, "starttime": 0}, "argv": ["x"],
             "cgroup": "/system.slice/some.service", "uid": 1006},
    })
    table = att.process_table(host)
    chain = graph.build_chain(graph.build_groups(table)[0], table, host=host)
    assert chain["device"]["status"] == "UNKNOWN"
    assert chain["transport"]["status"] == "UNKNOWN"
