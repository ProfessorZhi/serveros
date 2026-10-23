#!/usr/bin/env python3
"""§18 · one account's share of the machine, and the four things that must not
be said about it.

WHAT EACH SECTION KEEPS FALSE
-----------------------------
    producer    "somebody was told" (the account's share was never measured)
    identity    "it happened again" (a restarted pid is a second alert)
    escalation  "there are two problems" (one account, two tiers, two cards)
    recovery    "no news is good news" (an account that went unreadable was
                read as an account that came back down)
    §25         "the system handled it" (a threshold was crossed and something
                was stopped)

and the last section checks the module for the verbs that would make the last
line false. A code that cannot go red for the failure it names is a comment
with a green tick next to it.

THE INCIDENT THIS IS BUILT FROM, AND WHY ITS NUMBERS ARE LITERALS
----------------------------------------------------------------
On 2026-10-14 this host ran `vince` uid 1002's `python -m pytest -q
-p no:cacheprovider` as PID 288992 for about nineteen hours: anon RSS ~145 GiB
of 247 GiB total (58.7%), one core saturated, swap at 32 GiB nearly exhausted,
MemAvailable ~37 GiB. Every per-account view of this node was, correctly, about
somebody else. The fixture below is that machine.

The thresholds (30/50), the margin (5) and the percentages below are written
out as literals rather than read from `attribution-contract.yaml`. A fixture
that reads the policy it is testing asserts only that the policy equals itself
— move the margin to zero and every hysteresis test here still passes.
`TheContractOwnsTheNumbers` is the join that goes red if the two records ever
stop agreeing.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

DEVICE = "device:tokyo-shared-dev-01"
OTHER_DEVICE = "device:llm-lab-dell"
DETECTOR = "measurement-anomaly"

# -- the declared numbers, as literals --------------------------------------
SOFT = 30.0
CRITICAL = 50.0
MARGIN = 5.0
FLOOR = SOFT - MARGIN          # 25.0, and the sentence the hold describes

# The incident machine: 247 GiB total, and 145 GiB held by one uid.
TOTAL_KB = 247 * 1024 * 1024
INCIDENT_KB = 145 * 1024 * 1024
AVAILABLE_KB = 185 * 1024 * 1024
INCIDENT_PERCENT = round(100.0 * INCIDENT_KB / TOTAL_KB, 1)   # 58.7

T0 = "2026-10-14T02:00:00Z"
T1 = "2026-10-14T02:01:00Z"
T2 = "2026-10-14T02:02:00Z"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
ra = _load("serveros_runtime_resource_anomaly", "resource_anomaly.py")
an = _load("serveros_runtime_anomaly", "anomaly.py")
alerting = _load("serveros_runtime_alerting", "alerting.py")


# ---------------------------------------------------------------------------
# The host, scripted rather than faked
# ---------------------------------------------------------------------------
#
# An unscripted read RAISES. A double that answers `{}` to everything tests the
# double, and on this feature the read that matters most is the one nobody
# wrote a line for: `/proc/<pid>/cmdline`, `/proc/<pid>/environ` and
# `/proc/<pid>/cwd` are each one string away from the parser below, and each
# would publish somebody's arguments, environment or paths. This host makes
# that mistake a failure instead of a silent success.

class ScriptedHost:
    def __init__(self, files=None, commands=None):
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.slept = []
        self.reads = []
        self.read_paths = []

    def read(self, args, timeout):
        self.reads.append(list(args))
        for prefix, value in self.commands.items():
            if tuple(args[:len(prefix)]) != tuple(prefix):
                continue
            if isinstance(value, list):
                return value.pop(0)
            return value
        raise AssertionError(f"unscripted command: {list(args)!r}")

    def read_text(self, path):
        self.read_paths.append(path)
        if path not in self.files:
            raise AssertionError(f"unscripted read_text: {path!r}")
        value = self.files[path]
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def sleep(self, seconds):
        self.slept.append(seconds)


def meminfo(total_kb=TOTAL_KB, available_kb=AVAILABLE_KB):
    return (f"MemTotal:       {total_kb} kB\n"
            f"MemAvailable:   {available_kb} kB\n"
            f"SwapTotal:       33554432 kB\n"
            f"SwapFree:          131072 kB\n")


def status(rss_anon_kb, name="python"):
    return f"Name:\t{name}\nVmRSS:\t{rss_anon_kb + 1024} kB\nRssAnon:\t{rss_anon_kb} kB\n"


# The argument PREFIX `ScriptedHost.read` matches on, not the columns.
PS = ("ps", "-eo")


def ps(*rows) -> tuple:
    """One `ps -eo uid=,pid=,rss=,comm=` answer: (exit, stdout, stderr)."""
    return (0, "".join(f"{uid} {pid} {rss} {comm}\n" for uid, pid, rss, comm in rows), "")


def incident_host(**overrides):
    """The 2026-10-14 machine: one account at 145 GiB, plus the root account."""
    rows = ((1002, 288992, INCIDENT_KB, "python"),
            (0, 1, 12000, "systemd"))
    files = {"/proc/meminfo": meminfo(),
             "/proc/288992/status": status(INCIDENT_KB - 1024 * 1024),
             "/proc/1/status": status(8000, "systemd")}
    files.update(overrides.pop("files", {}))
    commands = {PS: [ps(*rows), ps(*rows)]}
    commands.update(overrides.pop("commands", {}))
    return ScriptedHost(files=files, commands=commands)


def report(host, interval=1.0):
    return inventory_mod.Inventory(host).users_memory(interval=interval)


def by_uid(document, uid):
    for row in document.get("users") or []:
        if row["uid"] == uid:
            return row
    raise AssertionError(f"no row for uid {uid}: {document.get('users')}")


# ---------------------------------------------------------------------------
# 1 · the producer: what one bounded scan can and cannot say
# ---------------------------------------------------------------------------

def test_the_incident_reads_as_58_7_percent_of_the_host():
    """145 GiB of 247 GiB, and the sum is over the account's own processes."""
    document = report(incident_host())

    assert document["state"] == "PRESENT"
    assert document["host_mem_total_bytes"] == TOTAL_KB * 1024
    vince = by_uid(document, 1002)
    assert vince["host_percent"] == INCIDENT_PERCENT == 58.7
    assert vince["rss_estimate_bytes"] == INCIDENT_KB * 1024
    assert vince["process_count"] == 1
    assert vince["user_name"] == "vince"


def test_two_accounts_are_two_rows_and_never_one():
    """The join this feature exists for, and its most likely corruption.

    A sum over the whole machine would have reported 58.7% as the host's
    figure — which is the number `MEMORY_PRESSURE` already refused to call a
    problem, because the machine was not out of memory. It was occupied.
    """
    document = report(incident_host())

    uids = sorted(row["uid"] for row in document["users"])
    assert uids == [0, 1002]
    assert by_uid(document, 0)["host_percent"] == round(
        100.0 * 12000 / TOTAL_KB, 1)
    assert by_uid(document, 1002)["host_percent"] == INCIDENT_PERCENT


def test_several_processes_of_one_account_are_one_row():
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/10/status": status(6 * 1024 * 1024, "python"),
               "/proc/11/status": status(2 * 1024 * 1024, "pytest")},
        commands={PS: [
            ps((1002, 10, 6 * 1024 * 1024, "python"),
               (1002, 11, 2 * 1024 * 1024, "pytest")),
            ps((1002, 10, 6 * 1024 * 1024, "python"),
               (1002, 11, 2 * 1024 * 1024, "pytest")),
        ]},
    )
    vince = by_uid(report(host), 1002)

    assert vince["process_count"] == 2
    assert vince["rss_estimate_bytes"] == 8 * 1024 * 1024 * 1024
    assert vince["anonymous_bytes"] == 8 * 1024 * 1024 * 1024
    assert [p["pid"] for p in vince["top"]] == ["10", "11"]


def test_the_rss_sum_says_what_it_double_counts():
    """The one thing this number must never be read as: exact."""
    document = report(incident_host())

    assert "shared" in document["provenance"]
    assert "over-estimates" in document["provenance"]
    assert "not the kernel's per-uid accounting" in document["provenance"]


def test_anonymous_memory_comes_from_rssanon_and_says_so():
    document = report(incident_host())
    vince = by_uid(document, 1002)

    assert vince["anonymous_state"] == "PRESENT"
    assert vince["anonymous_bytes"] == (INCIDENT_KB - 1024 * 1024) * 1024
    assert vince["anonymous_pids_read"] == vince["anonymous_pids_total"] == 1


def test_a_partially_read_account_is_partial_and_publishes_no_total():
    """A sum of the processes that WERE read is not the account's footprint.

    Publishing it under `anonymous_bytes` would be the whole defect: the field
    reads as a total, and a reader has no way to see that it is missing
    whichever processes were refused.
    """
    host = incident_host(files={"/proc/1/status": None})
    document = report(host)

    root = by_uid(document, 0)
    assert root["anonymous_state"] == "UNKNOWN"
    assert root["anonymous_bytes"] is None
    assert root["anonymous_measured_bytes"] == 0
    assert root["anonymous_pids_read"] == 0
    assert root["anonymous_pids_total"] == 1
    # The account that COULD be read is unaffected by its neighbour's refusal.
    assert by_uid(document, 1002)["anonymous_state"] == "PRESENT"


def test_an_account_whose_rssanon_field_is_absent_is_unknown_not_zero():
    """A kernel without `RssAnon` is not an account holding no anonymous pages."""
    host = incident_host(
        files={"/proc/288992/status": "Name:\tpython\nVmRSS:\t 1024 kB\n"})
    vince = by_uid(report(host), 1002)

    assert vince["anonymous_state"] == "UNKNOWN"
    assert vince["anonymous_bytes"] is None


def test_an_empty_process_listing_is_unavailable_not_a_machine_at_zero():
    """What `hidepid=2` produces for an account that may see only itself.

    Reading it as a measurement would recover every alert this feature exists
    to raise, on exactly the host whose visibility is worst.
    """
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo()},
        commands={PS: [(0, "", ""), (0, "", "")]},
    )
    document = report(host)

    assert document["state"] == inventory_mod.UNAVAILABLE
    assert "empty" in document["reason"]
    assert "users" not in document


def test_a_ps_that_refuses_is_unavailable():
    host = ScriptedHost(files={"/proc/meminfo": meminfo()},
                        commands={PS: (1, "", "ps: not permitted")})
    document = report(host)

    assert document["state"] == inventory_mod.UNAVAILABLE
    assert "users" not in document


def test_the_second_sample_failing_does_not_report_everyone_recovered():
    """Half a measurement is not a smaller measurement."""
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/288992/status": status(INCIDENT_KB)},
        commands={PS: [ps((1002, 288992, INCIDENT_KB, "python")),
                                  (1, "", "ps: interrupted")]},
    )
    document = report(host)

    assert document["state"] == inventory_mod.UNAVAILABLE
    assert "mid-sample" in document["reason"]


def test_a_command_name_with_a_space_does_not_shift_the_columns():
    """`comm` is not escaped, and the fourth field is a name and not a number."""
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/77/status": status(1024 * 1024, "my prog")},
        commands={PS: [
            ps((1002, 77, 4 * 1024 * 1024, "my prog")),
            ps((1002, 77, 4 * 1024 * 1024, "my prog")),
        ]},
    )
    vince = by_uid(report(host), 1002)

    assert vince["rss_estimate_bytes"] == 4 * 1024 * 1024 * 1024
    assert vince["top"][0]["comm"] == "my prog"


def test_an_unreadable_host_total_makes_the_share_unknown_not_zero():
    """No denominator is not a share of nought."""
    host = ScriptedHost(
        files={"/proc/meminfo": None,
               "/proc/288992/status": status(INCIDENT_KB)},
        commands={PS: [ps((1002, 288992, INCIDENT_KB, "python")),
                                  ps((1002, 288992, INCIDENT_KB, "python"))]},
    )
    document = report(host)

    assert document["state"] == "PRESENT"
    assert document["host_mem_total_bytes"] is None
    assert "host_mem_total_unknown" in document["degraded"]
    vince = by_uid(document, 1002)
    assert vince["host_percent"] is None
    assert vince["readings"] == [None, None]
    # The bytes were still measured, and are still reported: the missing half
    # is the denominator, and conflating the two would throw away a reading.
    assert vince["rss_estimate_bytes"] == INCIDENT_KB * 1024


def test_two_samples_of_one_account_are_reported_and_not_averaged():
    """`sustained` is a claim about two instants; the producer keeps both."""
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/10/status": status(25 * 1024 * 1024)},
        commands={PS: [
            ps((1002, 10, 20 * 1024 * 1024, "python")),
            ps((1002, 10, 25 * 1024 * 1024, "python")),
        ]},
    )
    document = report(host, interval=2.5)
    vince = by_uid(document, 1002)

    assert document["sample_seconds"] == 2.5
    assert document["samples"] == 2
    assert host.slept == [2.5]
    assert vince["readings"] == [round(100.0 * 20 * 1024 * 1024 / TOTAL_KB, 1),
                                 round(100.0 * 25 * 1024 * 1024 / TOTAL_KB, 1)]
    assert vince["host_percent"] == vince["readings"][1]


def test_an_account_that_starts_between_the_samples_is_not_yet_sustained():
    """A uid with no process in the first sample has one reading, not two."""
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/1/status": status(8000, "systemd"),
               "/proc/9/status": status(40 * 1024 * 1024)},
        commands={PS: [
            ps((0, 1, 12000, "systemd")),
            ps((0, 1, 12000, "systemd"), (1002, 9, 40 * 1024 * 1024, "python")),
        ]},
    )
    vince = by_uid(report(host), 1002)

    assert vince["readings"][0] is None
    assert vince["readings"][1] is not None


def test_one_ps_per_sample_and_a_bounded_number_of_status_reads():
    """The scan's cost, asserted rather than described.

    Two `ps` calls for two samples, and status reads that are bounded by the
    cap rather than by how many processes the machine happens to have.
    """
    rows = [(1002, 1000 + i, 1024, f"proc{i}") for i in range(400)]
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               **{f"/proc/{1000 + i}/status": status(512) for i in range(400)}},
        commands={PS: [ps(*rows), ps(*rows)]},
    )
    document = report(host)

    assert len(host.reads) == 2
    assert len([p for p in host.read_paths if p.endswith("/status")]) == \
        inventory_mod.Inventory.USER_MEMORY_ANON_READ_CAP
    assert "anonymous_read_cap_reached" in " ".join(document["degraded"])
    vince = by_uid(document, 1002)
    assert vince["anonymous_state"] == "PARTIAL"
    assert vince["anonymous_bytes"] is None
    assert vince["anonymous_pids_total"] == 400


def test_the_scan_reads_no_command_line_no_environment_and_no_cwd():
    """The disclosure boundary, checked where it would be crossed.

    `ScriptedHost` raises on an unscripted read, so a line that reached for
    `/proc/<pid>/cmdline` would fail the call above rather than quietly
    publishing a command line. This asserts the same thing a second way — on
    the source — because the first way only covers the paths a test happens to
    exercise.
    """
    source = (RUNTIME / "inventory.py").read_text()
    assert "cmdline" not in source
    assert "environ" not in source
    assert "/cwd" not in source

    document = report(incident_host())
    assert all(path.endswith("/status") or path == "/proc/meminfo"
               for path in incident_host().files or [])
    vince = by_uid(document, 1002)
    # What a reader gets about the process, and all of it.
    assert set(vince["top"][0]) == {"pid", "comm", "rss_bytes"}


# ---------------------------------------------------------------------------
# 2 · the detector: one code, two tiers, one subject
# ---------------------------------------------------------------------------

def uid_doc(rows, *, total_kb=TOTAL_KB, seconds=1.0):
    """A `users_memory` document, in the producer's own shape."""
    return {"state": "PRESENT", "samples": 2, "sample_seconds": seconds,
            "host_mem_total_bytes": total_kb * 1024,
            "host_mem_available_bytes": AVAILABLE_KB * 1024,
            "process_count": sum(r.get("process_count", 1) for r in rows),
            "users": rows, "degraded": []}


def uid_row(uid, *percentages, name="vince", rss_kb=1024, procs=1,
            anon_state="PRESENT", anon_kb=None, top=()):
    anon = (rss_kb * 1024 if anon_kb is None and anon_state == "PRESENT"
            else anon_kb)
    return {"uid": uid, "user_name": name, "process_count": procs,
            "rss_estimate_bytes": rss_kb * 1024,
            "host_mem_total_bytes": TOTAL_KB * 1024,
            "host_percent": percentages[-1], "readings": list(percentages),
            "anonymous_state": anon_state,
            "anonymous_bytes": anon if anon_state == "PRESENT" else None,
            "anonymous_measured_bytes": anon or 0,
            "anonymous_pids_read": procs if anon_state != "UNKNOWN" else 0,
            "anonymous_pids_total": procs,
            "top": list(top)}


def fire(document, **kwargs):
    return ra.detect(user_memory=document, observed_at=T0, now=T0, **kwargs)


def signals_for(out, code="USER_MEMORY_PRESSURE"):
    return [row for row in out["signals"] if row["code"] == code]


def test_the_contract_owns_the_numbers():
    """The literals at the top of this file are the contract's, and this is the
    join that fails if either record moves."""
    assert an.load_thresholds()["user_memory_percent_high"] == SOFT
    assert an.load_thresholds()["user_memory_percent_critical"] == CRITICAL
    assert an.load_thresholds()["user_memory_percent_recovery_margin"] == MARGIN
    assert ra.SEVERITY["USER_MEMORY_PRESSURE"] == "medium"
    assert ra._THRESHOLDS["USER_MEMORY_PRESSURE"] == "user_memory_percent_high"
    assert an.margin_key("user_memory_percent_high") == \
        "user_memory_percent_recovery_margin"
    assert an.margin_key("user_memory_percent_critical") == \
        "user_memory_percent_recovery_margin"


def test_the_incident_is_critical_and_immediate():
    """58.7% against a 50% critical tier, on one reading, at `high`."""
    out = fire(uid_doc([uid_row(1002, 57.0, INCIDENT_PERCENT)]))
    found = signals_for(out)

    assert len(found) == 1
    assert found[0]["severity"] == "high"
    assert found[0]["subject"] == "uid:1002"
    assert found[0]["measured"]["tier"] == "critical"
    assert found[0]["measured"]["host_percent"] == INCIDENT_PERCENT
    assert found[0]["measured"]["critical_threshold_percent"] == CRITICAL
    assert found[0]["measured"]["threshold_percent"] == SOFT


def test_the_soft_tier_needs_both_samples():
    """One reading over the line is not a sustained share."""
    out = fire(uid_doc([uid_row(1002, 31.0, 41.0)]))
    assert [r["severity"] for r in signals_for(out)] == ["medium"]
    assert signals_for(out)[0]["measured"]["tier"] == "sustained"

    # The same level reached only in the second sample is a spike.
    spike = fire(uid_doc([uid_row(1002, 12.0, 41.0)]))
    assert signals_for(spike) == []
    # ...and it is still a GRADED measurement, which is what keeps an alert
    # that is already open from closing on a single quiet sample.
    assert spike["levels"]["USER_MEMORY_PRESSURE|uid:1002"]["value"] == 41.0


def test_a_single_sample_cannot_claim_to_be_sustained():
    out = fire(uid_doc([uid_row(1002, 41.0)]))
    assert signals_for(out) == []


def test_a_brief_reading_below_the_soft_line_does_not_fire():
    out = fire(uid_doc([uid_row(1002, 29.9, 29.9)]))
    assert signals_for(out) == []
    assert "USER_MEMORY_PRESSURE" in out["codes_graded"]
    assert out["levels"]["USER_MEMORY_PRESSURE|uid:1002"]["value"] == 29.9


def test_the_two_tiers_are_one_code_and_one_subject():
    """The brief's "one escalating alert", asserted as a shape.

    Two codes would be two identities (`alerting.identity` is
    code + device + subject_ref), two register rows and two alerts for one
    account crossing one line.
    """
    soft = fire(uid_doc([uid_row(1002, 31.0, 31.0)]))
    hard = fire(uid_doc([uid_row(1002, 55.0, 55.0)]))

    assert {r["code"] for r in soft["signals"]} == \
        {r["code"] for r in hard["signals"]} == {"USER_MEMORY_PRESSURE"}
    assert {r["subject"] for r in hard["signals"]} == {"uid:1002"}
    assert soft["signals"][0]["severity"] == "medium"
    assert hard["signals"][0]["severity"] == "high"


def test_two_accounts_are_two_subjects_and_never_one_alert():
    out = fire(uid_doc([uid_row(1002, 55.0, 55.0, name="vince"),
                        uid_row(1001, 33.0, 33.0, name="alice")]))
    subjects = sorted(r["subject"] for r in signals_for(out))

    assert subjects == ["uid:1001", "uid:1002"]
    assert set(out["subjects"]) == {"uid:1001", "uid:1002"}


def test_an_account_that_could_not_be_expressed_is_enumerated_but_not_graded():
    """The subject is registered and the level is not, and the difference is
    the whole of "an alert is held, not closed"."""
    out = fire(uid_doc([uid_row(1002, INCIDENT_PERCENT),
                        {"uid": 1001, "user_name": "alice", "process_count": 3,
                         "rss_estimate_bytes": 0, "host_percent": None,
                         "readings": [None, None]}]))
    assert "uid:1001" in out["subjects"]
    assert "USER_MEMORY_PRESSURE|uid:1001" not in out["levels"]
    assert any("1001" in note for note in out["notes"])
    # And the account that WAS read is still graded.
    assert out["levels"]["USER_MEMORY_PRESSURE|uid:1002"]["value"] == \
        INCIDENT_PERCENT


def test_a_missing_inventory_is_not_a_quiet_machine():
    out = ra.detect(observed_at=T0, now=T0)

    assert out["signals"] == []
    listed = {row["code"] for row in out["codes_not_evaluated"]}
    assert "USER_MEMORY_PRESSURE" in listed
    assert "USER_MEMORY_PRESSURE" not in out["codes_graded"]


def test_an_unavailable_inventory_is_a_gap_and_not_an_empty_account_list():
    out = ra.detect(user_memory={"state": "UNAVAILABLE",
                                 "reason": "ps refused"},
                    observed_at=T0, now=T0)
    gap = next(row for row in out["codes_not_evaluated"]
               if row["code"] == "USER_MEMORY_PRESSURE")
    assert "ps refused" in gap["why"]


def test_the_level_row_closes_against_the_soft_threshold_whichever_tier_fired():
    """One place to close, so an escalation is not a second agreement to break."""
    out = fire(uid_doc([uid_row(1002, 55.0, 55.0)]))
    row = out["levels"]["USER_MEMORY_PRESSURE|uid:1002"]

    assert row["value"] == 55.0
    assert row["threshold"] == SOFT
    assert row["margin_key"] == "user_memory_percent_recovery_margin"


def test_a_declared_per_uid_exception_is_read_from_the_contract():
    """A build worker whose large use is its job, configured and not hardcoded."""
    thresholds = dict(an.load_thresholds())
    thresholds["user_memory_per_uid"] = {
        "1002": {"percent_high": 70.0, "percent_critical": 90.0}}
    out = ra.detect(user_memory=uid_doc([uid_row(1002, 55.0, 55.0)]),
                    thresholds=thresholds, observed_at=T0, now=T0)

    assert signals_for(out) == []
    assert out["levels"]["USER_MEMORY_PRESSURE|uid:1002"]["threshold"] == 70.0


def test_the_signal_carries_no_command_line_and_only_names_the_processes():
    out = fire(uid_doc([uid_row(1002, 57.0, INCIDENT_PERCENT,
                                top=({"pid": "288992", "comm": "python",
                                      "rss_bytes": INCIDENT_KB * 1024},))]))
    measured = signals_for(out)[0]["measured"]

    assert measured["top"] == [{"pid": "288992", "comm": "python",
                                "rss_bytes": INCIDENT_KB * 1024}]
    assert measured["account"] == "vince"
    assert measured["anonymous_state"] == "PRESENT"
    assert "GiB" in signals_for(out)[0]["why"]


def test_the_host_totals_beside_the_account_keep_swap_a_watermark():
    """A high swap figure beside an account's share is evidence, not a verdict.

    The producer carries the host's own swap watermark, and the code that
    reports an account's share must not read it as "this process is paging" —
    the same asymmetry `SWAP_PRESSURE` was renamed for.
    """
    out = fire(uid_doc([uid_row(1002, 57.0, INCIDENT_PERCENT)]))
    assert "swap" not in " ".join(signals_for(out)[0]["measured"])


# ---------------------------------------------------------------------------
# 3 · the alert register: identity, escalation, hysteresis, recovery
# ---------------------------------------------------------------------------

def store_at(tmp, name="state.json"):
    return alerting.AlertStore(Path(tmp) / "alerts" / name)


def detection_from(document, *, thresholds=None):
    out = ra.detect(user_memory=document, observed_at=T0, now=T0,
                    thresholds=thresholds)
    return out


def delivered(store, ref, *, at, kind="opened"):
    return alerting.record_delivery(store, alert_ref=ref, kind=kind, at=at,
                                    delivered=True, state="DELIVERED")


def test_an_account_over_the_line_opens_one_alert():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        out = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)

        assert len(out["alerts"]) == 1
        assert out["alerts"][0]["subject_ref"] == "uid:1002"
        assert out["alerts"][0]["state"] == "OPEN"
        # REVIEW is on the attention face, so a message is owed.
        assert [row["alert_ref"] for row in out["pending"]] == \
            [out["alerts"][0]["alert_ref"]]


def test_escalation_updates_the_same_alert_and_never_opens_a_second():
    """30% -> 55% is one account getting worse, and one register row for it."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        soft = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 31.0, 31.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = soft["alerts"][0]["alert_ref"]
        first_seen = soft["alerts"][0]["first_seen"]

        hard = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)

        assert len(hard["alerts"]) == 1
        assert hard["alerts"][0]["alert_ref"] == ref
        assert hard["alerts"][0]["severity"] == "high"
        assert hard["alerts"][0]["first_seen"] == first_seen
        assert hard["alerts"][0]["occurrence_count"] == 2
        assert store.summary()["total"] == 1


def test_a_restarted_process_is_not_a_second_alert():
    """`pid` is not in the identity, and this is the test that says so.

    The account is the subject. A supervisor that kills and relaunches the
    process every minute would otherwise produce one alert per restart, which
    is the failure `alerting.identity`'s docstring names.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        before = alerting.evaluate(
            store,
            detection=detection_from(uid_doc([
                uid_row(1002, 55.0, 55.0, top=({"pid": "288992",
                                                "comm": "python",
                                                "rss_bytes": 1},))])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        after = alerting.evaluate(
            store,
            detection=detection_from(uid_doc([
                uid_row(1002, 56.0, 56.0, top=({"pid": "331001",
                                                "comm": "python",
                                                "rss_bytes": 1},))])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)

        assert after["alerts"][0]["alert_ref"] == before["alerts"][0]["alert_ref"]
        assert store.summary()["total"] == 1


def test_the_same_uid_on_another_device_is_another_alert():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        for device in (DEVICE, OTHER_DEVICE):
            alerting.evaluate(
                store,
                detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
                device_ref=device, at=T0, detector=DETECTOR)

        subjects = {row["subject_ref"] for row in store.alerts().values()}
        devices = {row["device_ref"] for row in store.alerts().values()}
        assert subjects == {"uid:1002"}
        assert devices == {DEVICE, OTHER_DEVICE}
        assert store.summary()["total"] == 2


def test_the_alert_holds_until_the_share_comes_back_below_the_floor():
    """5 percentage points of hysteresis, measured against the soft tier."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        opened = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = opened["alerts"][0]["alert_ref"]
        delivered(store, ref, at=T0)

        # A held row is NOT touched: `alerts` is what the pass acted on, and
        # the answer to "why was I not told" is the register, not that list.
        alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 25.1, 25.1)])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)
        held = store.get(ref)
        assert held["state"] == "OPEN"
        assert held["hold_kind"] == "above_floor"
        assert str(FLOOR) in held["hold_reason"]

        closed = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 20.0, 20.0)])),
            device_ref=DEVICE, at=T2, detector=DETECTOR)
        assert closed["alerts"][0]["state"] == "RESOLVED"


def test_a_recovery_speaks_once_and_only_to_somebody_who_was_told():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        opened = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = opened["alerts"][0]["alert_ref"]

        # Nobody has been told yet: a recovery takes back an announcement, and
        # there is none to take back.
        silent = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 20.0, 20.0)])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)
        assert silent["pending"] == []
        assert any(row["stage"] == "notification" for row in silent["held"])

        # Same episode, told this time.
        store = store_at(tmp, "state2.json")
        opened = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = opened["alerts"][0]["alert_ref"]
        delivered(store, ref, at=T0)

        told = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 20.0, 20.0)])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)
        assert [row["pending"]["kind"] for row in told["pending"]] == ["resolved"]


def test_an_account_that_went_unreadable_holds_and_is_not_recovered():
    """The defect this feature would otherwise ship with.

    An account that stops being measurable is a LOSS OF OBSERVATION. Closing
    its alert would say "the account came back down", which nobody measured —
    and on a shared host the unreadable case is the ordinary one under
    `hidepid`.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        opened = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = opened["alerts"][0]["alert_ref"]
        delivered(store, ref, at=T0)

        # The account is still ENUMERATED and its share is no longer legible:
        # that is a loss of observation and not an ending.
        degraded = alerting.evaluate(
            store,
            detection=detection_from(uid_doc([
                {"uid": 1002, "user_name": "vince", "process_count": 3,
                 "host_percent": None, "readings": [None, None]}])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)
        assert degraded["alerts"] == []
        assert store.get(ref)["state"] == "OPEN"
        assert store.get(ref)["hold_kind"] == "unmeasured"

        # And when the inventory itself did not run, the code is ungraded and
        # every account's alert is held rather than resolved.
        offline = alerting.evaluate(
            store, detection=ra.detect(observed_at=T2, now=T2),
            device_ref=DEVICE, at=T2, detector=DETECTOR)
        assert offline["alerts"] == []
        assert store.get(opened["alerts"][0]["alert_ref"])["state"] == "OPEN"


def test_an_account_that_left_the_inventory_resolves_as_gone():
    """An account with no process is not an alert held open forever."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        store = store_at(tmp)
        opened = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1002, 55.0, 55.0)])),
            device_ref=DEVICE, at=T0, detector=DETECTOR)
        ref = opened["alerts"][0]["alert_ref"]
        delivered(store, ref, at=T0)

        gone = alerting.evaluate(
            store, detection=detection_from(uid_doc([uid_row(1001, 2.0, 2.0)])),
            device_ref=DEVICE, at=T1, detector=DETECTOR)
        assert gone["alerts"][0]["state"] == "RESOLVED"
        # GONE and UNMEASURED are the two answers the register must not
        # confuse: an account with no process has ENDED, and one that could not
        # be read has not. The first resolves and the second holds — which is
        # why the producer enumerates subjects before looking at figures.
        assert store.get(ref)["resolved_at"] == T1


def test_the_alert_never_reaches_an_action_that_touches_the_process():
    """§25, checked on the vocabulary rather than on a promise.

    An alert is a finding. The two forbidden responses are named in the
    contract's own list, and `alerting.admission` — the one surface in this
    tree that may produce a consequence — has no argument that names anything
    that already exists.
    """
    assert "high memory => kill" in ra.FORBIDDEN_RESPONSES
    assert "USER_MEMORY_PRESSURE" not in ra.ADMISSION_LEVELS
    source = (RUNTIME / "resource_anomaly.py").read_text()
    for verb in ("os.kill", "SIGKILL", "SIGTERM", "subprocess.Popen",
                 "os.setpriority", "signal."):
        assert verb not in source, verb
    assert "acts_on_existing_resources" in \
        str(ra.admission({"signals": [], "codes_not_evaluated": []}))

    alert_source = (RUNTIME / "alerting.py").read_text()
    for verb in ("os.kill", "SIGKILL", "os.system"):
        assert verb not in alert_source, verb


# ---------------------------------------------------------------------------
# 4 · the view layer: one scan, and the detector consults it
# ---------------------------------------------------------------------------

def test_the_view_reads_the_account_inventory_once_for_both_consumers():
    """§28's one-producer rule, in its most expensive spelling.

    `users_memory()` is the only input on this path that spends a whole sample
    window, so a card that re-derived it would pay that window twice for one
    number.
    """
    view_mod = _load("serveros_runtime_view", "view.py")

    class CountingInventory:
        def __init__(self):
            self.calls = 0

        def users_memory(self):
            self.calls += 1
            return uid_doc([uid_row(1002, 57.0, INCIDENT_PERCENT)])

        def __getattr__(self, name):
            # Every other producer is supplied by the caller below, so nothing
            # else may be reached. A `__getattr__` that answered would be the
            # double testing itself.
            raise AssertionError(f"unexpected producer call: inventory.{name}")

    inventory = CountingInventory()
    # `registry_doc` is a NODE input, not an argument of this test: left at
    # its default the view loads this machine's managed-runtime registry, so a
    # node without one fails a test that is about how many times an inventory
    # was read. `NO_REGISTRY` is the module's own sentinel for "the caller has
    # none and said so" — the answer `None` means "go and load it", which is
    # exactly the reach this fixture must not make.
    out = view_mod.resource_anomaly(
        inventory=inventory, host={"memory": {}}, disk={"roles": [], "others": []},
        docker={"sizes": {}, "logs": {}}, hygiene={"docker": {"containers": []}},
        desired={"signals": []}, tunnel={"tunnels": []},
        registry_doc=view_mod.NO_REGISTRY, space=False)

    assert inventory.calls == 1
    assert "user_memory" in out["gathering"]["inputs_reached"]
    assert "user_memory" not in out["gathering"]["inputs_supplied_by_the_caller"]
    assert [r["subject"] for r in out["signals"]
            if r["code"] == "USER_MEMORY_PRESSURE"] == ["uid:1002"]


def test_the_view_honours_a_caller_supplied_inventory():
    view_mod = _load("serveros_runtime_view", "view.py")

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError(f"the view re-measured {name}")

    # `registry_doc` is a NODE input, not an argument of this test: left at
    # its default the view loads this machine's managed-runtime registry, so a
    # node without one fails a test that is about how many times an inventory
    # was read. `NO_REGISTRY` is the module's own sentinel for "the caller has
    # none and said so" — the answer `None` means "go and load it", which is
    # exactly the reach this fixture must not make.
    out = view_mod.resource_anomaly(
        inventory=Exploding(), host={"memory": {}}, disk={"roles": [], "others": []},
        docker={"sizes": {}, "logs": {}}, hygiene={"docker": {"containers": []}},
        desired={"signals": []}, tunnel={"tunnels": []},
        user_memory=uid_doc([uid_row(1002, 31.0, 31.0)]),
        registry_doc=view_mod.NO_REGISTRY, space=False)

    assert "user_memory" in out["gathering"]["inputs_supplied_by_the_caller"]
    assert [r["severity"] for r in out["signals"]
            if r["code"] == "USER_MEMORY_PRESSURE"] == ["medium"]


# ---------------------------------------------------------------------------
# 5 · the closed vocabulary still agrees with itself
# ---------------------------------------------------------------------------

def test_the_code_is_declared_everywhere_the_vocabulary_is_closed():
    """A code that exists in code and not in a contract is the
    declared-but-absent pattern, one file over."""
    import yaml
    contract = yaml.safe_load(
        (RUNTIME / "attribution-contract.yaml").read_text())
    entry = next(row for row in contract["resource_anomalies"]
                 if row["code"] == "USER_MEMORY_PRESSURE")
    assert entry["emitted_by"] == "resource_anomaly._user_memory_signals"
    assert entry["threshold"] == "user_memory_percent_high"
    assert entry["implies_stale"] is False

    notifications = yaml.safe_load(
        (RUNTIME / "notification-contract.yaml").read_text())
    buckets = notifications["alerts"]["attention"]["codes"]
    assert buckets["boundary"]["USER_MEMORY_PRESSURE"] == "REVIEW"
    assert "REVIEW" in notifications["alerts"]["attention"]["on_the_face"]

    cards = yaml.safe_load((RUNTIME / "card-definitions.yaml").read_text())
    assert cards["codes"]["USER_MEMORY_PRESSURE"]["label"] == "账户内存占用"


if __name__ == "__main__":  # pragma: no cover - manual run only
    unittest.main()
