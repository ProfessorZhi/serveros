#!/usr/bin/env python3
"""§18 · one account holding the machine, and the six sentences this keeps false.

WHAT EACH SECTION KEEPS FALSE
-----------------------------
    producer    "somebody was told" (the account's share was never measured)
    estimate    "this is exactly what they hold" (summed RSS counts a shared
                page once per mapper)
    unknown     "the account is fine now" (a refused read is not a low reading)
    identity    "it happened again" (it is the same account, and a pid is not
                a name)
    escalation  "there are two problems" (one account, two tiers, two cards)
    recovery    "no news is good news" (an account that went unreadable was
                read as an account that came back down)
    §25         "the system handled it" (a threshold was crossed and something
                was stopped)

THE INCIDENT, AND WHY THE NUMBERS ARE LITERALS
----------------------------------------------
On 2026-10-14 this host ran `vince` (uid 1002)'s `python -m pytest -q
-p no:cacheprovider` as PID 288992 for about nineteen hours: anon RSS ~145 GiB
of 247 GiB total — 58.7% — one core saturated, swap nearly exhausted at 32 GiB,
MemAvailable down to 37 GiB. Every per-account view of this node was, at the
time, correctly about somebody else. The fixture below is that machine.

The thresholds (30/50) and the margin (5) are written out as literals rather
than read from `attribution-contract.yaml`. A fixture that reads the policy it
is testing asserts only that the policy equals itself — move the margin to zero
and every hysteresis test here still passes. `test_the_contract_owns_the_numbers`
below is the join that goes red if the two records stop agreeing.

THE HOST IS SCRIPTED, NOT FAKED
-------------------------------
An unscripted read RAISES. A double that answers `{}` to everything tests the
double, and the read this feature must never make — a process's argument
vector, its `env` block, its working directory — is exactly the one worth
failing on rather than returning an empty string for.

WHAT THIS FILE IS NOT
---------------------
The producer's own tests are `tests/test_user_memory.py`, and the module they
cover is `runtime/user_memory.py`. This file covers the half that turns a
reading into a finding: the detector in `resource_anomaly._user_memory_signals`,
the contract entries, the register, and the view wiring. The one producer this
file tests directly is `inventory.users_memory`, because that is the document
the detector is actually handed on the card path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"


def _load(name, filename):
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

# -- the declared numbers, as literals ---------------------------------------
SOFT = 30.0
CRITICAL = 50.0
MARGIN = 5.0
FLOOR = SOFT - MARGIN          # 25.0, and the sentence a held alert carries
GIB = 1024 ** 3
TOTAL_KB = 247 * 1024 * 1024
INCIDENT_KB = 145 * 1024 * 1024
INCIDENT_PERCENT = 58.7        # the two integers an operator read off `top`

DEVICE = "device:tokyo-shared-dev-01"
OTHER_DEVICE = "device:llm-lab-dell"
DETECTOR = "measurement-anomaly"

T0 = "2026-10-14T02:00:00Z"
T1 = "2026-10-14T02:01:00Z"
T2 = "2026-10-14T02:02:00Z"

CODE = "USER_MEMORY_PRESSURE"
SUBJECT = "uid:1002"


# ---------------------------------------------------------------------------
# The scripted host, for the one producer this file exercises
# ---------------------------------------------------------------------------

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


PS = ("ps", "-eo")
PS_ARGV = ["ps", "-eo", "uid=,pid=,rss=,comm="]


def meminfo(total_kb=TOTAL_KB, available_kb=185 * 1024 * 1024):
    return (f"MemTotal:       {total_kb} kB\n"
            f"MemAvailable:   {available_kb} kB\n"
            f"SwapTotal:       33554432 kB\n"
            f"SwapFree:           65536 kB\n")


def status(rss_anon_kb, name="python"):
    return (f"Name:\t{name}\nState:\tS (sleeping)\n"
            f"VmRSS:\t{rss_anon_kb + 1024} kB\n"
            f"RssAnon:\t{rss_anon_kb} kB\n")


def ps(*rows):
    return (0, "".join(f"{uid} {pid} {rss} {comm}\n"
                       for uid, pid, rss, comm in rows), "")


def incident_host(**overrides):
    """247 GiB of host, one account at 145 GiB, and a root account beside it."""
    rows = ((1002, 288992, INCIDENT_KB, "python"),
            (0, 1, 12000, "systemd"))
    files = {"/proc/meminfo": meminfo(),
             "/proc/288992/status": status(INCIDENT_KB - 1024 * 1024),
             "/proc/1/status": status(8000, "systemd")}
    files.update(overrides.pop("files", {}))
    commands = {PS: [ps(*rows), ps(*rows)]}
    commands.update(overrides.pop("commands", {}))
    return ScriptedHost(files=files, commands=commands)


def produced(host, interval=1.0):
    return inventory_mod.Inventory(host).users_memory(interval=interval)


# ---------------------------------------------------------------------------
# The detector's input, in the producer's own shape
# ---------------------------------------------------------------------------

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


def document(rows, *, total_kb=TOTAL_KB, seconds=1.0):
    return {"state": "PRESENT", "samples": 2, "sample_seconds": seconds,
            "host_mem_total_bytes": total_kb * 1024,
            "host_mem_available_bytes": 185 * 1024 * 1024,
            "process_count": sum(r.get("process_count", 1) for r in rows),
            "users": rows, "degraded": [],
            "provenance": "test fixture"}


def fire(doc, **kwargs):
    return ra.detect(user_memory=doc, observed_at=T0, now=T0, **kwargs)


def signals(out, code=CODE):
    return [row for row in out["signals"] if row["code"] == code]


# ---------------------------------------------------------------------------
# 1 · the producer, on the two facts the detector depends on
# ---------------------------------------------------------------------------

def test_the_incident_reads_as_58_7_percent_of_the_host():
    """145 GiB of 247 GiB, summed over one account's own processes."""
    out = produced(incident_host())
    vince = next(r for r in out["users"] if r["uid"] == 1002)

    assert out["state"] == "PRESENT"
    assert out["host_mem_total_bytes"] == TOTAL_KB * 1024
    assert vince["host_percent"] == INCIDENT_PERCENT == 58.7
    assert vince["rss_estimate_bytes"] == INCIDENT_KB * 1024
    assert vince["process_count"] == 1
    assert vince["user_name"] == "vince"


def test_two_accounts_are_two_rows_and_never_one():
    """A sum over the machine is the number `MEMORY_PRESSURE` already had."""
    out = produced(incident_host())
    assert sorted(r["uid"] for r in out["users"]) == [0, 1002]


def test_the_rss_sum_says_what_it_double_counts():
    """The one thing this number must never be read as: exact."""
    provenance = produced(incident_host())["provenance"]

    assert "shared" in provenance
    assert "counted twice" in provenance
    assert "not the kernel's per-uid accounting" in provenance
    assert "smaps_rollup" in provenance


def test_two_samples_travel_and_are_never_averaged():
    """`sustained` is a claim about two instants, so both are reported."""
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(),
               "/proc/10/status": status(25 * 1024 * 1024)},
        commands={PS: [ps((1002, 10, 20 * 1024 * 1024, "python")),
                       ps((1002, 10, 25 * 1024 * 1024, "python"))]})
    out = inventory_mod.Inventory(host).users_memory(interval=2.5)
    vince = out["users"][0]

    assert out["sample_seconds"] == 2.5 and out["samples"] == 2
    assert host.slept == [2.5]
    assert vince["readings"][0] < vince["readings"][1]
    assert vince["host_percent"] == vince["readings"][1]


def test_a_ps_that_refuses_is_unavailable_and_never_zero_memory():
    host = ScriptedHost(files={"/proc/meminfo": meminfo()},
                        commands={PS: (1, "", "ps: not permitted")})
    out = produced(host)

    assert out["state"] == inventory_mod.UNAVAILABLE
    assert "users" not in out


def test_an_empty_table_is_not_an_idle_machine():
    """`hidepid=2` produces exactly this for an account that sees only itself."""
    host = ScriptedHost(files={"/proc/meminfo": meminfo()}, commands={PS: (0, "", "")})
    out = produced(host)

    assert out["state"] == inventory_mod.UNAVAILABLE
    assert "empty" in out["reason"]
    assert "users" not in out


def test_the_scan_reads_no_argument_vector_no_env_and_no_working_directory():
    """The disclosure boundary, asserted on the source.

    `ScriptedHost` raises on an unscripted read, so a line that reached for one
    of these would fail the calls above — this asserts it a second way, on the
    module, because the first way only covers the paths a test happens to
    exercise.
    """
    source = (RUNTIME / "inventory.py").read_text()

    assert "cmdline" not in source
    assert "environ" not in source
    assert "/cwd" not in source
    # What a reader gets about a process, and all of it.
    vince = next(r for r in produced(incident_host())["users"] if r["uid"] == 1002)
    assert set(vince["top"][0]) == {"pid", "comm", "rss_bytes"}


# ---------------------------------------------------------------------------
# 2 · the detector: one code, two tiers, one subject
# ---------------------------------------------------------------------------

def test_the_contract_owns_the_numbers():
    """The literals at the top of this file are the contract's, and this is the
    join that fails if either record moves."""
    thresholds = an.load_thresholds()

    assert thresholds["user_memory_percent_high"] == SOFT
    assert thresholds["user_memory_percent_critical"] == CRITICAL
    assert thresholds["user_memory_percent_recovery_margin"] == MARGIN
    assert ra.SEVERITY[CODE] == "medium"
    assert ra._THRESHOLDS[CODE] == "user_memory_percent_high"
    assert an.margin_key("user_memory_percent_high") == \
        "user_memory_percent_recovery_margin"
    assert an.margin_key("user_memory_percent_critical") == \
        "user_memory_percent_recovery_margin"


def test_the_incident_is_critical_and_immediate():
    """58.7% against a 50% critical tier, on ONE reading, at `high`."""
    out = fire(document([uid_row(1002, 57.0, INCIDENT_PERCENT)]))
    found = signals(out)

    assert len(found) == 1
    assert found[0]["severity"] == "high"
    assert found[0]["subject"] == SUBJECT
    assert found[0]["measured"]["tier"] == "critical"
    assert found[0]["measured"]["host_percent"] == INCIDENT_PERCENT
    assert found[0]["measured"]["critical_threshold_percent"] == CRITICAL
    assert found[0]["measured"]["threshold_percent"] == SOFT


def test_the_soft_tier_needs_both_samples():
    out = fire(document([uid_row(1002, 31.0, 41.0)]))
    assert [r["severity"] for r in signals(out)] == ["medium"]
    assert signals(out)[0]["measured"]["tier"] == "sustained"
    assert signals(out)[0]["measured"]["sustained_readings"] == 2


def test_one_high_sample_is_a_spike_and_not_a_sustained_share():
    spike = fire(document([uid_row(1002, 12.0, 41.0)]))

    assert signals(spike) == []
    # …and it is still a GRADED measurement, which is what keeps an alert that
    # is already open from closing on a single quiet sample.
    assert spike["levels"][f"{CODE}|{SUBJECT}"]["value"] == 41.0


def test_a_single_sample_cannot_claim_to_be_sustained():
    assert signals(fire(document([uid_row(1002, 41.0)]))) == []


def test_a_reading_below_the_soft_line_does_not_fire():
    out = fire(document([uid_row(1002, 29.9, 29.9)]))

    assert signals(out) == []
    assert CODE in out["codes_graded"]
    assert out["levels"][f"{CODE}|{SUBJECT}"]["value"] == 29.9


def test_the_two_tiers_are_one_code_and_one_subject():
    """The brief's "one escalating alert", asserted as a shape.

    A second code would be a second identity — `alerting.identity` is
    code + device + subject_ref — and therefore a second register row and a
    second card for one account crossing one line.
    """
    soft = fire(document([uid_row(1002, 31.0, 31.0)]))
    hard = fire(document([uid_row(1002, 55.0, 55.0)]))

    assert {r["code"] for r in soft["signals"]} == \
        {r["code"] for r in hard["signals"]} == {CODE}
    assert {r["subject"] for r in soft["signals"]} == \
        {r["subject"] for r in hard["signals"]} == {SUBJECT}
    assert soft["signals"][0]["severity"] == "medium"
    assert hard["signals"][0]["severity"] == "high"


def test_two_accounts_are_two_subjects_and_never_one_alert():
    out = fire(document([uid_row(1002, 55.0, 55.0, name="vince"),
                         uid_row(1001, 33.0, 33.0, name="alice")]))

    assert sorted(r["subject"] for r in signals(out)) == ["uid:1001", "uid:1002"]
    assert set(out["subjects"]) == {"uid:1001", "uid:1002"}


def test_the_subject_is_the_kernel_uid_and_not_the_name():
    """A name is a string an administrator can change; the number is not."""
    out = fire(document([uid_row(1002, 55.0, 55.0, name="renamed-by-hand")]))
    found = signals(out)[0]

    assert found["subject"] == "uid:1002"
    assert found["measured"]["account"] == "renamed-by-hand"


def test_an_account_that_could_not_be_expressed_is_enumerated_but_not_graded():
    """The subject is registered and the level is not, and the difference is
    the whole of "an alert is held, not closed"."""
    out = fire(document([
        uid_row(1002, INCIDENT_PERCENT),
        {"uid": 1001, "user_name": "alice", "process_count": 3,
         "rss_estimate_bytes": 0, "host_percent": None, "readings": [None, None]}]))

    assert "uid:1001" in out["subjects"]
    assert f"{CODE}|uid:1001" not in out["levels"]
    assert any("1001" in note for note in out["notes"])
    assert out["levels"][f"{CODE}|{SUBJECT}"]["value"] == INCIDENT_PERCENT


def test_an_account_with_one_unreadable_row_is_not_graded_at_all():
    """An account the pass could not fully measure is not a measured account.

    A document carrying two rows for one uid is one account, and grading the
    readable row would report a share for an account the pass demonstrably
    could not express.
    """
    out = fire(document([
        uid_row(1002, 57.0, 57.0),
        {"uid": 1002, "user_name": "vince", "process_count": 0,
         "host_percent": None, "readings": [None, None]}]))

    assert signals(out) == []
    assert f"{CODE}|{SUBJECT}" not in out["levels"]
    assert SUBJECT in out["subjects"]


def test_a_missing_inventory_is_not_a_quiet_machine():
    out = ra.detect(observed_at=T0, now=T0)

    assert out["signals"] == []
    listed = {row["code"] for row in out["codes_not_evaluated"]}
    assert CODE in listed and CODE not in out["codes_graded"]


def test_an_unavailable_inventory_is_a_gap_and_not_an_empty_account_list():
    out = ra.detect(user_memory={"state": "UNAVAILABLE", "reason": "ps refused"},
                    observed_at=T0, now=T0)
    gap = next(row for row in out["codes_not_evaluated"] if row["code"] == CODE)

    assert "ps refused" in gap["why"]


def test_the_level_row_closes_against_the_soft_threshold_whichever_tier_fired():
    """One place to close, so an escalation is not a second agreement to break."""
    out = fire(document([uid_row(1002, 55.0, 55.0)]))
    row = out["levels"][f"{CODE}|{SUBJECT}"]

    assert row["value"] == 55.0
    assert row["threshold"] == SOFT
    assert row["margin_key"] == "user_memory_percent_recovery_margin"


def test_a_declared_per_uid_exception_is_read_from_the_contract():
    """A build worker whose large use is its job, configured and not hardcoded."""
    thresholds = dict(an.load_thresholds())
    thresholds["user_memory_per_uid"] = {
        "1002": {"percent_high": 70.0, "percent_critical": 90.0}}
    out = ra.detect(user_memory=document([uid_row(1002, 55.0, 55.0)]),
                    thresholds=thresholds, observed_at=T0, now=T0)

    assert signals(out) == []
    assert out["levels"][f"{CODE}|{SUBJECT}"]["threshold"] == 70.0
    # …and the exception is DISPLAYED, so a reader sees which line it crossed.
    raised = ra.detect(user_memory=document([uid_row(1002, 75.0, 75.0)]),
                       thresholds=thresholds, observed_at=T0, now=T0)
    assert signals(raised)[0]["measured"]["threshold_percent"] == 70.0


def test_the_signal_names_the_processes_and_carries_no_command_line():
    out = fire(document([uid_row(1002, 57.0, INCIDENT_PERCENT,
                                 top=({"pid": "288992", "comm": "python",
                                       "rss_bytes": INCIDENT_KB * 1024},))]))
    measured = signals(out)[0]["measured"]

    assert measured["top"] == [{"pid": "288992", "comm": "python",
                                "rss_bytes": INCIDENT_KB * 1024}]
    assert measured["account"] == "vince"
    assert "GiB" in signals(out)[0]["why"]


def test_the_host_totals_beside_the_account_keep_swap_a_watermark():
    """A high swap figure beside an account's share is evidence, not a verdict.

    The producer carries the host's own swap watermark, and the code that
    reports an account's share must not read it as "this process is paging" —
    the asymmetry `SWAP_PRESSURE` was renamed for.
    """
    measured = signals(fire(document([uid_row(1002, 57.0, INCIDENT_PERCENT)])))[0]

    assert "swap" not in " ".join(measured["measured"])


# ---------------------------------------------------------------------------
# 3 · the register: identity, escalation, hysteresis, recovery
# ---------------------------------------------------------------------------

def store_at(tmp, name="state.json"):
    return alerting.AlertStore(Path(tmp) / "alerts" / name)


def detection_of(doc, **kwargs):
    return ra.detect(user_memory=doc, observed_at=T0, now=T0, **kwargs)


def delivered(store, ref, *, at, kind="opened"):
    """The notification path, and the only thing that may set `last_notified_at`."""
    return alerting.record_delivery(store, alert_ref=ref, kind=kind, at=at,
                                    delivered=True, state="DELIVERED")


def opened_once(*, percent, at=T0, device=DEVICE, tmp=None, name="state.json",
                readings=None):
    """A store with one alert about uid:1002, opened and delivered."""
    store = store_at(tmp, name)
    rows = uid_row(1002, *(readings or (percent,)))
    out = alerting.evaluate(store, detection=detection_of(document([rows])),
                            device_ref=device, at=at, detector=DETECTOR)
    delivered(store, out["alerts"][0]["alert_ref"], at=at)
    return store, out["alerts"][0]["alert_ref"]


def test_an_account_over_the_line_opens_one_alert(tmp_path):
    store = store_at(tmp_path)
    out = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 55.0, 55.0)])), device_ref=DEVICE, at=T0,
        detector=DETECTOR)

    assert len(out["alerts"]) == 1
    assert out["alerts"][0]["subject_ref"] == SUBJECT
    assert out["alerts"][0]["state"] == "OPEN"
    # REVIEW is on the attention face, so a message is owed.
    assert [row["alert_ref"] for row in out["pending"]] == \
        [out["alerts"][0]["alert_ref"]]


def test_escalation_updates_the_same_alert_and_never_opens_a_second(tmp_path):
    """31% → 55% is one account getting worse, and one register row for it."""
    store = store_at(tmp_path)
    soft = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 31.0, 31.0)])), device_ref=DEVICE, at=T0,
        detector=DETECTOR)
    ref, first_seen = soft["alerts"][0]["alert_ref"], soft["alerts"][0]["first_seen"]

    hard = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 55.0, 55.0)])), device_ref=DEVICE, at=T1,
        detector=DETECTOR)

    assert len(hard["alerts"]) == 1
    assert hard["alerts"][0]["alert_ref"] == ref
    assert hard["alerts"][0]["severity"] == "high"
    assert hard["alerts"][0]["first_seen"] == first_seen
    assert hard["alerts"][0]["occurrence_count"] == 2
    assert store.summary()["total"] == 1


def test_a_restarted_process_is_not_a_second_alert(tmp_path):
    """`pid` is not in the identity, and this is the test that says so.

    A supervisor that kills and relaunches the process every minute would
    otherwise produce one alert per restart, which is the failure
    `alerting.identity`'s docstring names.
    """
    store = store_at(tmp_path)
    before = alerting.evaluate(store, detection=detection_of(document([
        uid_row(1002, 55.0, 55.0,
                top=({"pid": "288992", "comm": "python", "rss_bytes": 1},))])),
        device_ref=DEVICE, at=T0, detector=DETECTOR)
    after = alerting.evaluate(store, detection=detection_of(document([
        uid_row(1002, 56.0, 56.0,
                top=({"pid": "331001", "comm": "python", "rss_bytes": 1},))])),
        device_ref=DEVICE, at=T1, detector=DETECTOR)

    assert after["alerts"][0]["alert_ref"] == before["alerts"][0]["alert_ref"]
    assert store.summary()["total"] == 1


def test_the_same_uid_on_another_device_is_another_alert(tmp_path):
    """The device is half the identity, and this is the cross-device rule."""
    store = store_at(tmp_path)
    for device in (DEVICE, OTHER_DEVICE):
        alerting.evaluate(store, detection=detection_of(
            document([uid_row(1002, 55.0, 55.0)])), device_ref=device, at=T0,
            detector=DETECTOR)

    assert {row["subject_ref"] for row in store.alerts().values()} == {SUBJECT}
    assert {row["device_ref"] for row in store.alerts().values()} == \
        {DEVICE, OTHER_DEVICE}
    assert store.summary()["total"] == 2


def test_the_alert_holds_until_the_share_comes_back_below_the_floor(tmp_path):
    """Five percentage points of hysteresis, measured against the soft tier."""
    store, ref = opened_once(percent=55.0, tmp=tmp_path, readings=(55.0,))

    held = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 25.1, 25.1)])), device_ref=DEVICE, at=T1,
        detector=DETECTOR)
    # `alerts` lists the rows this pass TOUCHED — a signal fired or a row
    # resolved. A row held because it is still above its floor was neither, so
    # it is read back from the register; and it is in `held` with the reason,
    # which is the answer to "why was I not told".
    assert held["alerts"] == []
    assert store.get(ref)["state"] == "OPEN"
    assert store.get(ref)["hold_kind"] == "above_floor"
    assert str(FLOOR) in store.get(ref)["hold_reason"]
    assert any(row["alert_ref"] == ref and row["stage"] == "resolution"
               for row in held["held"])

    closed = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 20.0, 20.0)])), device_ref=DEVICE, at=T2,
        detector=DETECTOR)
    assert closed["alerts"][0]["state"] == "RESOLVED"


def test_a_recovery_speaks_once_and_only_to_somebody_who_was_told(tmp_path):
    """A recovery takes back an announcement, and there is one only if there was."""
    never_told = store_at(tmp_path, "never.json")
    alerting.evaluate(never_told, detection=detection_of(
        document([uid_row(1002, 55.0, 55.0)])), device_ref=DEVICE, at=T0,
        detector=DETECTOR)
    silent = alerting.evaluate(never_told, detection=detection_of(
        document([uid_row(1002, 20.0, 20.0)])), device_ref=DEVICE, at=T1,
        detector=DETECTOR)

    assert silent["pending"] == []
    assert any(row["stage"] == "notification" for row in silent["held"])

    store, _ = opened_once(percent=55.0, tmp=tmp_path, name="told.json")
    told = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1002, 20.0, 20.0)])), device_ref=DEVICE, at=T1,
        detector=DETECTOR)

    assert [row["pending"]["kind"] for row in told["pending"]] == ["resolved"]


def test_an_account_that_went_unreadable_holds_and_is_not_recovered(tmp_path):
    """The defect this feature would otherwise ship with.

    An account that stops being measurable is a LOSS OF OBSERVATION. Closing
    its alert would say "the account came back down", which nobody measured —
    and on a shared host the unreadable case is the ordinary one under
    `hidepid`.
    """
    store, ref = opened_once(percent=55.0, tmp=tmp_path)

    degraded = alerting.evaluate(store, detection=detection_of(document([
        uid_row(1002, 57.0, 57.0),
        {"uid": 1002, "process_count": 0, "host_percent": None,
         "readings": [None, None]}])), device_ref=DEVICE, at=T1, detector=DETECTOR)
    assert degraded["alerts"] == []

    offline = alerting.evaluate(store, detection=ra.detect(observed_at=T2, now=T2),
                                device_ref=DEVICE, at=T2, detector=DETECTOR)
    assert offline["alerts"] == []
    assert store.get(ref)["state"] == "OPEN"
    assert store.get(ref)["hold_kind"] == "not_evaluated"


def test_an_account_that_left_the_inventory_resolves_as_gone(tmp_path):
    """An account with no process is an ending, not an alert held open forever."""
    store, ref = opened_once(percent=55.0, tmp=tmp_path)

    gone = alerting.evaluate(store, detection=detection_of(
        document([uid_row(1001, 2.0, 2.0)])), device_ref=DEVICE, at=T1,
        detector=DETECTOR)

    assert gone["alerts"][0]["state"] == "RESOLVED"
    assert gone["alerts"][0]["alert_ref"] == ref


def test_an_alert_in_another_detector_is_not_closed_by_this_one(tmp_path):
    """`DESIRED_ACTUAL_MISMATCH` is graded by both detectors; this one is not
    allowed to rule on a row it never graded."""
    store = store_at(tmp_path)
    ref = alerting.identity(code=CODE, device_ref=DEVICE, subject_ref=SUBJECT)
    alerts = store.alerts()
    alerts[alerting.alert_ref(ref)] = {
        "alert_ref": alerting.alert_ref(ref), "device_ref": DEVICE,
        "detector": "declaration-anomaly", "code": CODE, "subject_ref": SUBJECT,
        "severity": "medium", "state": "OPEN", "first_seen": T0, "last_seen": T0,
        "occurrence_count": 1, "margin_key": "user_memory_percent_recovery_margin",
        "last_notified_at": None}
    store.commit(alerts)

    out = alerting.evaluate(store, detection=detection_of(document([])),
                            device_ref=DEVICE, at=T1, detector=DETECTOR)

    assert out["alerts"] == []
    assert store.get(alerting.alert_ref(ref))["state"] == "OPEN"


# ---------------------------------------------------------------------------
# 4 · §25: nothing on this path can stop, signal or isolate anything
# ---------------------------------------------------------------------------

def test_the_alert_never_reaches_an_action_that_touches_the_process():
    """§25, checked on the vocabulary rather than on a promise."""
    assert "high memory => kill" in ra.FORBIDDEN_RESPONSES
    assert CODE not in ra.ADMISSION_LEVELS
    assert CODE not in ra.REFUSING_CODES
    assert signals(fire(document([uid_row(1002, 57.0, INCIDENT_PERCENT)])))[0][
        "implies_stale"] is False

    source = (RUNTIME / "resource_anomaly.py").read_text()
    for verb in ("os.kill", "SIGKILL", "SIGTERM", "subprocess.Popen",
                 "os.setpriority", "import signal"):
        assert verb not in source, verb

    alert_source = (RUNTIME / "alerting.py").read_text()
    for verb in ("os.kill", "SIGKILL", "os.system"):
        assert verb not in alert_source, verb


def test_the_admission_gate_still_has_no_argument_that_names_an_existing_resource():
    answered = ra.admission({"signals": [], "codes_not_evaluated": []})

    assert answered["acts_on_existing_resources"] is False
    assert answered["forbidden"] == list(ra.FORBIDDEN_RESPONSES)
    assert answered["decision"] == ra.ADMIT


# ---------------------------------------------------------------------------
# 5 · the view: one scan, and the detector consults it
# ---------------------------------------------------------------------------

def all_inputs(**overrides):
    """Every input `view.resource_anomaly` takes, truthy.

    `gathered.take` reads an empty mapping as "not supplied", so a fixture that
    left one at `{}` would have the view reach for its producer — and a test
    about which producer was reached would then be about which branch ran.
    """
    payload = dict(
        host={"memory": {"total_bytes": TOTAL_KB * 1024, "used_bytes": GIB},
              "cpu": {"logical_cpus": 4}, "pressure": {}, "paging": {},
              "load": {}, "uptime_seconds": 1},
        disk={"roles": [], "others": []},
        docker={"sizes": {"containers": []}, "logs": {"containers": []},
                "data_root_total_bytes": None},
        hygiene={"docker": {"containers": []}},
        desired={"signals": []},
        tunnel={"tunnels": []},
    )
    payload.update(overrides)
    return payload


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

        def users_memory(self, interval=None):
            self.calls += 1
            return document([uid_row(1002, 57.0, INCIDENT_PERCENT)])

        def __getattr__(self, name):
            raise AssertionError(f"unexpected producer call: inventory.{name}")

    inventory = CountingInventory()
    # `registry_doc` is a NODE input, not an argument of this test: left at
    # its default the view loads this machine's managed-runtime registry, so a
    # node without one fails a test that is about how many times an inventory
    # was read. `NO_REGISTRY` is the module's own sentinel for "the caller has
    # none and said so" — the answer `None` means "go and load it", which is
    # exactly the reach this fixture must not make.
    out = view_mod.resource_anomaly(inventory=inventory, **all_inputs(),
                                    registry_doc=view_mod.NO_REGISTRY, space=False)

    assert inventory.calls == 1
    assert "user_memory" in out["gathering"]["inputs_reached"]
    assert "user_memory" not in out["gathering"]["inputs_supplied_by_the_caller"]
    assert [r["subject"] for r in out["signals"] if r["code"] == CODE] == [SUBJECT]


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
        inventory=Exploding(), user_memory=document([uid_row(1002, 31.0, 31.0)]),
        **all_inputs(), registry_doc=view_mod.NO_REGISTRY, space=False)

    assert "user_memory" in out["gathering"]["inputs_supplied_by_the_caller"]
    assert [r["severity"] for r in out["signals"]
            if r["code"] == CODE] == ["medium"]


# ---------------------------------------------------------------------------
# 6 · the closed vocabulary still agrees with itself
# ---------------------------------------------------------------------------

def test_the_code_is_declared_everywhere_the_vocabulary_is_closed():
    """A code that exists in code and not in a contract is the
    declared-but-absent pattern, one file over."""
    import yaml

    contract = yaml.safe_load((RUNTIME / "attribution-contract.yaml").read_text())
    entry = next(row for row in contract["resource_anomalies"]
                 if row["code"] == CODE)
    assert entry["emitted_by"] == "resource_anomaly._user_memory_signals"
    assert entry["threshold"] == "user_memory_percent_high"
    assert entry["implies_stale"] is False
    # The threshold the entry names is DECLARED, and so is the margin its
    # `_high` suffix derives — an undeclared margin is an alert that can never
    # close, and that failure reads as a problem nobody is dealing with.
    assert contract["thresholds"]["user_memory_percent_high"] == SOFT
    assert contract["thresholds"]["user_memory_percent_critical"] == CRITICAL
    assert contract["thresholds"]["user_memory_percent_recovery_margin"] == MARGIN

    notifications = yaml.safe_load(
        (RUNTIME / "notification-contract.yaml").read_text())
    buckets = notifications["alerts"]["attention"]["codes"]
    assert buckets["boundary"][CODE] == "REVIEW"
    assert "REVIEW" in notifications["alerts"]["attention"]["on_the_face"]

    cards = yaml.safe_load((RUNTIME / "card-definitions.yaml").read_text())
    assert cards["codes"][CODE]["label"] == "账户内存占用"


def test_the_new_code_is_in_every_table_the_module_reads():
    assert CODE in ra.CODES
    assert CODE in ra.SEVERITY
    assert CODE in ra._THRESHOLDS
    assert CODE in ra._INPUTS
    assert "user_memory_percent_high" in ra.THRESHOLD_KEYS
    assert "user_memory_percent_critical" in ra.THRESHOLD_KEYS
    assert "user_memory_percent_recovery_margin" in ra.THRESHOLD_KEYS
    assert CODE in [row["code"] for row in ra.inputs_needed()]
