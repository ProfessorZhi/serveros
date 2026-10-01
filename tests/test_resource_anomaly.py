#!/usr/bin/env python3
"""§18 · The fifteen resource anomaly codes.

Three things are being tested here, and they are three different claims.

1. The vocabulary AGREES WITH ITSELF. The contract names fifteen codes; the
   module names fifteen; their severities match; none of them implies stale.
   That half is transcription and a test is the only thing that keeps a
   transcription honest.

2. Every code is REACHABLE. §18 says the system "must be able to represent"
   DISK_WARNING through TUNNEL_OWNER_MISSING, and a code that no input can
   produce satisfies the letter of that and none of its purpose. So one
   synthetic observation is built that fires all fifteen, and the test asserts
   the set is exactly the declared set.

3. Not-evaluated is not the same as clean. The distinction has a test per
   branch, because it is the one this round keeps having to make: an unreadable
   log is not a small log, an unreachable daemon is not an empty one, and a
   filesystem nobody measured is not below the threshold.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

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


ra = _load("serveros_runtime_resource_anomaly", "resource_anomaly.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
graph = _load("serveros_runtime_graph", "graph.py")
alert_digest = _load("serveros_runtime_alert_digest", "alert_digest.py")

CONTRACT_DOC = yaml.safe_load(
    (RUNTIME / "attribution-contract.yaml").read_text())


def contract_codes() -> dict:
    return {row["code"]: row for row in CONTRACT_DOC["resource_anomalies"]}


# ---------------------------------------------------------------------------
# A synthetic observation that fires all sixteen
# ---------------------------------------------------------------------------

OBSERVED_AT = "2026-09-14T12:00:00+08:00"


def full_observation() -> dict:
    """Every input, each chosen to cross exactly one threshold.

    Deliberately NOT the live host. A test that graded the real machine would
    pass or fail with the machine's disk usage, which is the shape of test
    that gets deleted the first time it is inconvenient.

    Two filesystems on purpose. DISK_WARNING and DISK_CRITICAL are tiers of
    one measurement, so no single mount can produce both: the critical tier
    wins and the warning tier goes unreported for that disk. Producing both
    from one document requires two disks, which is what the two entries below
    are.
    """
    return dict(
        observed_at=OBSERVED_AT,
        host={"memory": {"total_bytes": 100, "used_bytes": 95,
                         "available_bytes": 5,
                         "swap": {"total_bytes": 100, "used_bytes": 97}},
              # The pressure axis, and why MEMORY_PRESSURE_ACTIVE is a code of
              # its own: `some avg60` crosses its own 1.0 threshold here, and
              # any one of these three can cross without the other two. An
              # observation that has to fire every declared code must carry
              # input for the axis that is not a watermark.
              "pressure": {"provider": "PRESENT",
                           "memory": {"state": "PRESENT",
                                      "some": {"avg10": 4.0, "avg60": 3.0,
                                               "avg300": 2.0, "total": 900000},
                                      "full": {"avg10": 3.0, "avg60": 2.0,
                                               "avg300": 1.0, "total": 800000}}}},
        disk={"roles": [
            {"role": "ROOT_FS", "path": "/", "state": "PRESENT",
             "mount_point": "/", "used_percent": 93.4, "free_bytes": 10,
             "inodes": {"used_percent": 91.0}},
            {"role": "DOCKER_FS", "path": "/opt/docker", "state": "PRESENT",
             "mount_point": "/", "used_percent": 93.4,
             "inodes": {"used_percent": 91.0}},
        ], "others": [
            {"target": "/srv", "used_percent": "88%", "inodes_percent": "12%"},
        ]},
        docker={
            "space": {"state": "PRESENT", "sections": {
                "build_cache": {"size_bytes": 30 * 10 ** 9,
                                "reclaimable_bytes": 10 ** 9}}},
            "data_root_total_bytes": 1000,
            "sizes": {"containers": [
                {"name": "big", "state": "running",
                 "size_rw_bytes": 2 * 10 ** 9, "size_root_fs_bytes": 3 * 10 ** 9}]},
            "logs": {"state": "PRESENT", "count": 1, "unobservable": 0,
                     "containers": [{"name": "big", "driver": "json-file",
                                     "log_bytes": 2 * 10 ** 9}]},
        },
        hygiene={"docker": {"containers": [
            {"name": "nobody", "classification": "UNKNOWN_OWNER", "running": True},
            {"name": "container0", "classification": "PROTECTED", "running": True},
            {"name": "gone", "classification": "ORPHANED", "running": False},
        ]}},
        desired={"signals": [{"code": "DESIRED_ACTUAL_MISMATCH",
                              "subject": "runtime:z", "measured": {},
                              "window": "at observation time", "why": "m"}]},
        tunnel={"tunnels": [{"tunnel_ref": "t1", "owner_ref": None,
                             "ownership_confidence": "UNKNOWN",
                             "health": {"state": "FAILED"},
                             "actual_state": "DOWN"}]},
    )


# ---------------------------------------------------------------------------
# 1 · the vocabulary agrees with itself
# ---------------------------------------------------------------------------

def test_the_contract_declares_exactly_the_sixteen_codes():
    assert list(contract_codes()) == list(ra.CODES)


def test_the_severity_table_matches_the_contract():
    declared = {code: row["severity"] for code, row in contract_codes().items()}
    assert declared == ra.SEVERITY


def test_no_resource_code_claims_to_imply_staleness():
    for code, row in contract_codes().items():
        assert row["implies_stale"] is False, code


def test_every_code_names_the_producer_that_emits_it():
    for code, row in contract_codes().items():
        assert row.get("emitted_by"), code


def test_the_thresholds_the_module_reads_are_declared_in_the_contract():
    declared = CONTRACT_DOC["thresholds"]
    for key in ra.THRESHOLD_KEYS:
        assert key in declared, key
        assert anomaly.DEFAULT_THRESHOLDS[key] == declared[key], key


def test_the_critical_disk_tier_is_above_the_warning_one():
    t = anomaly.DEFAULT_THRESHOLDS
    assert t["filesystem_percent_critical"] > t["filesystem_percent_high"]


def test_the_graded_disk_codes_read_the_same_key_as_the_process_layer():
    """`alias_of` is a claim, and this is the test that makes it one.

    The contract says DISK_WARNING/DISK_CRITICAL are the graded spellings of
    ROOT_FS_PRESSURE/DATA_FS_PRESSURE. If the resource layer read a different
    threshold key, the two surfaces would disagree about the same filesystem
    while both describing themselves as the warning tier.
    """
    codes = contract_codes()
    assert codes["DISK_WARNING"]["alias_of"] == ["ROOT_FS_PRESSURE", "DATA_FS_PRESSURE"]
    assert codes["DISK_WARNING"]["threshold"] == "filesystem_percent_high"
    # And the process layer's own check reads the same key. Asserted against
    # the code rather than the contract, because the point is that the two
    # DETECTORS agree, and a contract cannot promise that about itself.
    source = (RUNTIME / "anomaly.py").read_text()
    assert 'used >= thresholds["filesystem_percent_high"]' in source


# ---------------------------------------------------------------------------
# 2 · every code is reachable
# ---------------------------------------------------------------------------

def test_the_synthetic_observation_fires_every_declared_code():
    out = ra.detect(**full_observation())
    assert set(out["codes_emitted"]) == set(ra.CODES), (
        sorted(set(ra.CODES) - set(out["codes_emitted"])),
        out["codes_not_evaluated"])
    assert out["codes_not_evaluated"] == []
    assert out["coverage"]["graded"] == out["coverage"]["of"] == len(ra.CODES)


def test_a_protected_resource_at_risk_is_named_by_its_protection():
    out = ra.detect(**full_observation())
    rows = [r for r in out["signals"] if r["code"] == "PROTECTED_RESOURCE_AT_RISK"]
    assert [r["subject"] for r in rows] == ["container:container0"]
    assert rows[0]["measured"]["protected"] is True
    assert rows[0]["severity"] == "high"


def test_every_signal_carries_its_threshold_its_source_and_its_time():
    out = ra.detect(**full_observation())
    for row in out["signals"]:
        assert row["severity"] in ("low", "medium", "high"), row["code"]
        assert row["source"], row["code"]
        assert row["window"], row["code"]
        assert row["why"], row["code"]
        # §26. A signal whose freshness cannot be computed says UNKNOWN with
        # the reason; none of these sources is allowed to be one of those,
        # which is what makes FRESH_WITHIN_SECONDS a policy and not a comment.
        assert row["freshness"]["state"] in ("FRESH", "STALE"), (
            row["code"], row["freshness"])


def test_the_memory_group_carries_no_field_the_card_could_never_receive():
    """A key outside `MEASUREMENT_FIELDS` is dropped by the digest projection,
    so the detector writing it is the detector writing to nobody — the number
    exists in the register and in no sentence a person reads.

    This is not hypothetical. `swap_used_percent` was added to
    MEMORY_PRESSURE_ACTIVE's `measured` by the watermark/pressure split, and the
    whitelist was not told, so the one reading that shows why the split exists —
    the stall fired while the swap watermark had NOT — was structurally
    unreachable from the card. Nothing failed; the field simply went nowhere.

    Scoped to the three codes whose `measured` this round touched. The other
    thirteen write 27 keys the whitelist does not carry, and deciding one at a
    time whether each is context no card needs or a reading with no reader is a
    real question — but it is a different question, about codes this change does
    not alter, and answering it by writing 27 exclusions here would bury the
    three that matter under a list nobody would re-derive.
    """
    out = ra.detect(**full_observation())
    codes = ("MEMORY_PRESSURE", "SWAP_PRESSURE", "MEMORY_PRESSURE_ACTIVE")
    carried = set(alert_digest.MEASUREMENT_FIELDS)
    unreachable = {
        row["code"]: sorted(set(row["measured"]) - carried)
        for row in out["signals"] if row["code"] in codes
        and set(row["measured"]) - carried
    }
    assert unreachable == {}, (
        f"these readings reach the register and no card, because "
        f"alert_digest.MEASUREMENT_FIELDS does not carry them: {unreachable}")


def test_a_low_severity_code_never_outranks_a_high_one():
    out = ra.detect(**full_observation())
    order = [r["severity"] for r in out["signals"]]
    assert order == sorted(order, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])


# ---------------------------------------------------------------------------
# 3 · not evaluated is not the same as clean
# ---------------------------------------------------------------------------

def test_an_unreachable_daemon_does_not_report_zero_docker_usage():
    """§27's invariant, one layer up from where the brief states it."""
    observation = full_observation()
    observation["docker"] = {"space": {"state": "UNAVAILABLE",
                                       "reason": "docker is not reachable"}}
    out = ra.detect(**observation)
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "DOCKER_STORAGE_HIGH" in reasons
    assert "docker is not reachable" in reasons["DOCKER_STORAGE_HIGH"]
    assert "DOCKER_STORAGE_HIGH" not in out["codes_emitted"]
    assert "BUILD_CACHE_HIGH" in reasons


def test_an_unreachable_docker_is_not_a_host_with_nothing_unowned():
    """§27, through `hygiene.py`, which is the path a live host takes.

    `Hygiene.report()` answers an unreachable Docker with
    `{"docker": {"state": "UNAVAILABLE", "reason": ...}}` — its own code says
    "docker is not reachable; no classification was made". The two runtime
    codes read `docker["containers"]`, and a missing key there iterates over
    nothing, so both landed in `codes_graded` with no signals: an unreadable
    container set served as "every container has an owner". The reason is
    carried through to `codes_not_evaluated` rather than replaced by a generic
    one, because which half of the pipeline was unavailable is the fact a
    reader needs.
    """
    out = ra.detect(hygiene={"docker": {
        "state": "UNAVAILABLE", "reason": "Cannot connect to the Docker daemon"}})
    assert "RUNTIME_UNOWNED" not in out["codes_graded"]
    assert "RUNTIME_ORPHANED" not in out["codes_graded"]
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "Cannot connect to the Docker daemon" in reasons["RUNTIME_UNOWNED"]
    assert "not the same as none being unowned" in reasons["RUNTIME_UNOWNED"]


def test_a_host_summary_without_totals_is_not_a_host_with_no_pressure():
    """§27 on the memory branch: the same shape, one producer over.

    A host document that arrives and carries no usable totals is a measurement
    that did not happen. It is the branch with the least dramatic failure —
    "0 memory pressure" is exactly what a healthy host reports — which is why
    it is asserted rather than assumed.
    """
    out = ra.detect(host={"memory": {"total_bytes": None, "swap": {}}})
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "MEMORY_PRESSURE" in reasons
    assert "SWAP_PRESSURE" in reasons
    assert "no usable totals" in reasons["MEMORY_PRESSURE"]
    assert "MEMORY_PRESSURE" not in out["codes_graded"]
    assert "SWAP_PRESSURE" not in out["codes_graded"]


def test_a_swap_watermark_reaches_the_card_with_the_pressure_beside_it():
    """The reading, not the policy. `ADMISSION_LEVELS` is asserted unchanged.

    SWAP_PRESSURE was graded on the watermark alone, and `ADMISSION_LEVELS`
    puts it at `refuse` — so the number that reached the register, and the
    card, and any future creation path, was the one this host measured as
    NOT pressure: 99.2% of swap with memory PSI at 0.00 across every window
    and `pswpout` unmoved over forty minutes (2026-09-15). `_swap_note` has
    separated the two on the advisory path since it was written; the signal
    did not carry the second reading at all, because the host document handed
    to `detect()` had no place to put it.

    What this asserts is that the delivery now happens. The policy moved in the
    round that made the delivery matter, and it was moved deliberately: a
    watermark that only rises is not grounds for refusing a creation, so
    `SWAP_PRESSURE` is `advisory` and the pressure half of this host was given
    `MEMORY_PRESSURE_ACTIVE` to refuse on instead. The last line is here so
    that the next move of this kind also has to be made on purpose.
    """
    out = ra.detect(host={
        "memory": {"total_bytes": 33285996544, "used_bytes": 20000000000,
                   "available_bytes": 9000000000,
                   "swap": {"total_bytes": 17179865088,
                            "used_bytes": 17040000000}},
        "pressure": {
            "provider": "PRESENT",
            "memory": {"state": "PRESENT",
                       "some": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0,
                                "total": 12345},
                       "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0,
                                "total": 0}},
        },
    })
    signal = [s for s in out["signals"] if s["code"] == "SWAP_PRESSURE"][0]
    measured = signal["measured"]
    assert measured["used_percent"] == 99.2
    assert measured["memory_pressure"]["state"] == "PRESENT"
    assert measured["memory_pressure"]["some"]["avg60"] == 0.0
    assert measured["psi_memory_some_avg60_percent_high"] == 1.0
    assert measured["psi_memory_full_avg60_percent_high"] == 0.5
    assert ra.ADMISSION_LEVELS["SWAP_PRESSURE"] == "advisory"


# ---------------------------------------------------------------------------
# The two axes, told apart on the machines that were actually measured
# ---------------------------------------------------------------------------


def test_active_pressure_is_reported_on_a_machine_neither_watermark_covers():
    """The 2026-09-29 machine, which the old pair of codes reported as green.

    Swap below its 90% line and memory available, so neither watermark fires,
    while memory PSI some avg60 sits at 6.8 against a threshold of 1.0 and the
    host is writing 53,238 pages a second to swap. Both old codes were silent
    on this machine, and that is the finding this code exists to make: the
    number that would have caught it was never read, not merely read and
    compared wrongly.
    """
    out = ra.detect(host={
        "memory": {"total_bytes": 1000, "used_bytes": 200,
                   "available_bytes": 800,
                   "swap": {"total_bytes": 1000, "used_bytes": 805}},
        "pressure": {"provider": "PRESENT",
                     "memory": {"state": "PRESENT",
                                "some": {"avg10": 7.0, "avg60": 6.8,
                                         "avg300": 3.0, "total": 9000000},
                                "full": {"avg10": 6.9, "avg60": 6.7,
                                         "avg300": 2.9, "total": 8900000}}},
        "paging": {"state": "PRESENT", "sample_seconds": 1.0,
                   "pswpin_per_second": 12.0, "pswpout_per_second": 53238.0,
                   "pgmajfault_per_second": 40.0,
                   "pgscan_direct_per_second": 5.0,
                   "pgsteal_direct_per_second": 3.0},
    })
    rows = [s for s in out["signals"] if s["code"] == "MEMORY_PRESSURE_ACTIVE"]
    assert len(rows) == 1, out["codes_emitted"]
    measured = rows[0]["measured"]
    # The provider's own shape, not a verdict derived from it.
    assert measured["memory_pressure"]["some"]["avg60"] == 6.8
    assert measured["psi_memory_some_avg60_percent_high"] == 1.0
    assert measured["psi_memory_full_avg60_percent_high"] == 0.5
    # The other two axes travel beside it, because a stall reading on its own
    # is the counter-intuitive half — 80% of memory was available.
    assert measured["available_percent"] == 80.0
    assert measured["swap_used_percent"] == 80.5
    assert measured["paging"]["pswpout_per_second"] == 53238.0
    assert rows[0]["subject"] == "host:memory-pressure"
    # And the two watermarks are below their lines, so this signal is the only
    # thing standing between the machine and a green board.
    assert "SWAP_PRESSURE" not in out["codes_emitted"]
    assert "MEMORY_PRESSURE" not in out["codes_emitted"]


def test_a_full_and_quiet_swap_file_does_not_become_a_pressure_finding():
    """The 2026-09-15 machine, and the reason the two codes are not one.

    Swap at 99.2% with memory PSI at 0.00 across every window and `pswpout`
    unmoved over forty minutes: a watermark at its highest, and no work
    stalling at all. The watermark is reported — it is a true reading and
    whoever is about to create a resource should see it — and the pressure
    code stays silent, because there is no pressure to report. One code could
    not say both, and the one that carried the name `SWAP_PRESSURE` said the
    half the name claims and the register did not.
    """
    out = ra.detect(host={
        "memory": {"total_bytes": 1000, "used_bytes": 200,
                   "available_bytes": 800,
                   "swap": {"total_bytes": 1000, "used_bytes": 992}},
        "pressure": {"provider": "PRESENT",
                     "memory": {"state": "PRESENT",
                                "some": {"avg10": 0.0, "avg60": 0.0,
                                         "avg300": 0.0, "total": 12345},
                                "full": {"avg10": 0.0, "avg60": 0.0,
                                         "avg300": 0.0, "total": 0}}},
        "paging": {"state": "PRESENT", "sample_seconds": 1.0,
                   "pswpin_per_second": 0.0, "pswpout_per_second": 0.0,
                   "pgmajfault_per_second": 0.0,
                   "pgscan_direct_per_second": 0.0,
                   "pgsteal_direct_per_second": 0.0},
    })
    assert "SWAP_PRESSURE" in out["codes_emitted"]
    assert "MEMORY_PRESSURE_ACTIVE" not in out["codes_emitted"]
    measured = [s for s in out["signals"]
                if s["code"] == "SWAP_PRESSURE"][0]["measured"]
    # The reading that keeps the watermark from being read as pressure, and the
    # activity figure that says the machine was not moving pages at all.
    assert measured["memory_pressure"]["some"]["avg60"] == 0.0
    assert measured["paging"]["pswpout_per_second"] == 0.0


def test_the_watermark_and_the_pressure_are_two_findings_and_not_one():
    """Both over their lines at once, and neither stands in for the other.

    A full machine that is also stalling produces two alerts with two
    subjects, and calling that a duplicate would be the mistake this whole
    round is about: they are separate measurements of the same host, and a
    reader who merged them would lose which half was true.
    """
    out = ra.detect(host={
        "memory": {"total_bytes": 1000, "used_bytes": 950,
                   "available_bytes": 50,
                   "swap": {"total_bytes": 1000, "used_bytes": 970}},
        "pressure": {"provider": "PRESENT",
                     "memory": {"state": "PRESENT",
                                "some": {"avg10": 9.0, "avg60": 8.0,
                                         "avg300": 5.0, "total": 1000000},
                                "full": {"avg10": 8.0, "avg60": 7.0,
                                         "avg300": 4.0, "total": 900000}}},
    })
    emitted = set(out["codes_emitted"])
    assert {"MEMORY_PRESSURE", "SWAP_PRESSURE",
            "MEMORY_PRESSURE_ACTIVE"} <= emitted, sorted(emitted)
    subjects = {s["subject"] for s in out["signals"]
                if s["code"] in ("MEMORY_PRESSURE", "SWAP_PRESSURE",
                                 "MEMORY_PRESSURE_ACTIVE")}
    assert subjects == {"host:memory", "host:swap", "host:memory-pressure"}
    # Distinct subjects is what makes these three rows three alerts rather than
    # one: `alert_ref` is derived from code, device and subject, so nothing
    # here can collide with anything else here.
    assert len(subjects) == 3


def test_an_unreadable_pressure_axis_does_not_unmeasure_the_watermarks():
    """The gap is per code, and this is the test that keeps it that way.

    A single gap string for the whole memory group — the shape this branch
    returned before — would report a host whose `/proc/pressure` could not be
    read as a host whose swap watermark nobody had looked at either. Those are
    two different sentences and the second one is false: the watermarks are
    read from `/proc/meminfo`, they were read this tick, and they are below
    their lines.
    """
    out = ra.detect(host={
        "memory": {"total_bytes": 1000, "used_bytes": 200,
                   "available_bytes": 800,
                   "swap": {"total_bytes": 1000, "used_bytes": 300}},
        "pressure": {"provider": "UNAVAILABLE",
                     "memory": {"state": "UNAVAILABLE",
                                "reason": "/proc/pressure/memory is not readable"}},
    })
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "MEMORY_PRESSURE_ACTIVE" in reasons
    assert "MEMORY_PRESSURE" not in reasons
    assert "SWAP_PRESSURE" not in reasons
    assert "not readable" in reasons["MEMORY_PRESSURE_ACTIVE"]
    # §27 in the direction this code exists for: a host nobody measured for
    # stalls is not a host with none, and saying so here is the whole point of
    # putting it in a gap rather than defaulting it to a value.
    assert "not a machine with none" in reasons["MEMORY_PRESSURE_ACTIVE"]
    assert "MEMORY_PRESSURE" in out["codes_graded"]
    assert "SWAP_PRESSURE" in out["codes_graded"]


def test_the_admission_tiers_were_regraded_on_what_each_code_measures():
    """§25's permission follows the measurement, not the code's history.

    `SWAP_PRESSURE` was `refuse` while it meant whatever it meant, and the
    round that narrowed it to the watermark alone had to re-answer the
    admission question rather than inherit the answer — a code that no longer
    measures pressure is not a code that should keep closing the host to new
    work. `MEMORY_PRESSURE` keeps its gate because its measurement is
    MemAvailable, and it is the stall code, not the swap one, that refuses now.
    """
    assert ra.ADMISSION_LEVELS["MEMORY_PRESSURE"] == "refuse"
    assert ra.ADMISSION_LEVELS["MEMORY_PRESSURE_ACTIVE"] == "refuse"
    assert ra.ADMISSION_LEVELS["SWAP_PRESSURE"] == "advisory"
    # And the refusal is still bounded to creation. §25's two forbidden
    # responses are asserted against the contract elsewhere; the point of
    # asking here is that the code moved INTO the refusing set arrives with no
    # wider permission than the one that left it.
    answer = ra.admission(ra.detect(host={
        "memory": {"total_bytes": 1000, "used_bytes": 950,
                   "available_bytes": 50,
                   "swap": {"total_bytes": 1000, "used_bytes": 970}},
        "pressure": {"provider": "PRESENT",
                     "memory": {"state": "PRESENT",
                                "some": {"avg10": 9.0, "avg60": 8.0,
                                         "avg300": 5.0, "total": 1000000},
                                "full": {"avg10": 8.0, "avg60": 7.0,
                                         "avg300": 4.0, "total": 900000}}},
    }))
    assert answer["decision"] == ra.REFUSE
    assert answer["acts_on_existing_resources"] is False
    assert sorted(row["code"] for row in answer["blocking"]) == [
        "MEMORY_PRESSURE", "MEMORY_PRESSURE_ACTIVE"]


def test_an_unreadable_psi_travels_as_unavailable_and_never_as_zero():
    """`pressure_stall` refuses to report "no stall" for "cannot tell", and the
    signal must not undo that on the way to the card by dropping the field."""
    out = ra.detect(host={
        "memory": {"total_bytes": 33285996544, "used_bytes": 20000000000,
                   "swap": {"total_bytes": 17179865088,
                            "used_bytes": 17040000000}},
        "pressure": {"provider": "UNAVAILABLE",
                     "memory": {"state": "UNAVAILABLE",
                                "reason": "/proc/pressure/memory is not readable"}},
    })
    measured = [s for s in out["signals"]
                if s["code"] == "SWAP_PRESSURE"][0]["measured"]
    assert measured["memory_pressure"]["state"] == "UNAVAILABLE"
    assert "not readable" in measured["memory_pressure"]["reason"]
    assert "some" not in measured["memory_pressure"]


def test_a_host_document_with_no_pressure_field_is_not_a_host_with_no_stall():
    """A caller that built its own summary — every stub in the suite — leaves
    the key out. The key is then present and null on the signal, which reads as
    "this code has no such measurement" rather than as a clean bill."""
    out = ra.detect(host={
        "memory": {"total_bytes": 33285996544, "used_bytes": 20000000000,
                   "swap": {"total_bytes": 17179865088,
                            "used_bytes": 17040000000}}})
    measured = [s for s in out["signals"]
                if s["code"] == "SWAP_PRESSURE"][0]["measured"]
    assert "memory_pressure" in measured
    assert measured["memory_pressure"] is None


def test_a_comparison_that_compared_nothing_is_not_graded_clean():
    """§27, through `desired.py` — the last branch of it this round found.

    A comparison document is TRUTHY whether or not any comparison in it was
    made, so `if desired:` graded this code on a node where every probe had
    failed and every row read UNCHECKED. `mismatches: []` then meant "every
    registration is in its desired state", which is the sentence §27 forbids:
    nobody had looked.

    Note what this document claims about itself, because it is the trap: the
    measurement provider says `AVAILABLE`, since a health record exists for the
    resource — the record whose every axis is UNAVAILABLE. Presence of a key is
    not success of a measurement, so the gap is decided from the coverage
    counters and not from that field.
    """
    out = ra.detect(desired={
        "providers": {"registry": {"state": "AVAILABLE", "registrations": 1},
                      "measurement": {"state": "AVAILABLE",
                                      "resources_measured": 1}},
        "coverage": {"comparisons": 1, "in_desired_state": 0, "in_mismatch": 0,
                     "unchecked": 1},
        "signals": [],
    })
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "DESIRED_ACTUAL_MISMATCH" in reasons
    assert "DESIRED_ACTUAL_MISMATCH" not in out["codes_graded"]
    assert "all 1 comparison(s) came back UNCHECKED" in reasons["DESIRED_ACTUAL_MISMATCH"]
    assert "Unmeasured is not in-state" in reasons["DESIRED_ACTUAL_MISMATCH"]


def test_a_comparison_that_reached_a_verdict_is_graded_with_or_without_a_finding():
    """The other side of the rule, so the fix cannot be "never grade this".

    Empty `mismatches` over rows that WERE compared is a real, correct clean
    reading — every registration is in its desired state — and a detector that
    stopped reporting it would be silent in the same way for the opposite
    reason. Both spellings are asserted here: compared-and-clean, and fired.
    """
    clean = ra.detect(desired={
        "coverage": {"comparisons": 3, "in_desired_state": 3, "in_mismatch": 0,
                     "unchecked": 0},
        "signals": []})
    assert "DESIRED_ACTUAL_MISMATCH" in clean["codes_graded"]
    assert "DESIRED_ACTUAL_MISMATCH" not in clean["codes_emitted"]

    fired = ra.detect(desired={"signals": [{
        "code": "DESIRED_ACTUAL_MISMATCH", "subject": "runtime:z",
        "measured": {}, "window": "w", "why": "m"}]})
    assert "DESIRED_ACTUAL_MISMATCH" in fired["codes_emitted"]
    assert "DESIRED_ACTUAL_MISMATCH" not in [
        row["code"] for row in fired["codes_not_evaluated"]]


def test_a_partly_compared_document_says_how_many_rows_it_could_not_compare():
    """Graded, and honest about the shortfall rather than rounded up to clean.

    A comparison can reach a verdict on some registrations and not others —
    one probe answers and another's provider is down. The code is graded in
    that case, so the only place the shortfall can live is a note beside the
    counts; without it a reader takes `graded` for a complete comparison.
    """
    out = ra.detect(desired={
        "coverage": {"comparisons": 3, "in_desired_state": 2, "in_mismatch": 0,
                     "unchecked": 1},
        "signals": []})
    assert "DESIRED_ACTUAL_MISMATCH" in out["codes_graded"]
    note = [n for n in out["notes"] if n.startswith("DESIRED_ACTUAL_MISMATCH")]
    assert len(note) == 1
    assert "1 of 3 registration(s) could not be compared" in note[0]


def test_a_comparison_with_no_registration_behind_it_says_which_half_was_missing():
    """Two different absences, and the reason names which one it was.

    No registry is "nobody declared anything to compare"; a registry with
    registrations and no comparisons is "the comparison was handed a
    registration and compared none of it". They call for different repairs, so
    the reason is not allowed to be one generic sentence.
    """
    no_registry = ra.detect(desired={
        "providers": {"registry": {"state": "UNAVAILABLE",
                                   "why": "no registry was resolved",
                                   "registrations": 0}},
        "coverage": {"comparisons": 0}, "signals": []})
    why = {row["code"]: row["why"] for row in no_registry["codes_not_evaluated"]}
    assert "no registry was resolved" in why["DESIRED_ACTUAL_MISMATCH"]
    assert "DESIRED_ACTUAL_MISMATCH" not in no_registry["codes_graded"]

    ignored = ra.detect(desired={
        "providers": {"registry": {"state": "AVAILABLE", "registrations": 4}},
        "coverage": {"comparisons": 0}, "signals": []})
    why = {row["code"]: row["why"] for row in ignored["codes_not_evaluated"]}
    assert "4 registration(s) and compared none of them" in why["DESIRED_ACTUAL_MISMATCH"]


def test_an_unreadable_log_is_not_a_small_log():
    observation = full_observation()
    observation["docker"]["logs"] = {
        "state": "PRESENT", "count": 38, "unobservable": 38,
        "containers": [{"name": f"c{i}", "log_bytes": None} for i in range(38)]}
    out = ra.detect(**observation)
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "CONTAINER_LOG_LARGE" in reasons
    assert "unknown rather than small" in reasons["CONTAINER_LOG_LARGE"]
    assert "CONTAINER_LOG_LARGE" not in out["codes_emitted"]


def test_a_filesystem_reported_without_a_figure_is_not_graded():
    observation = full_observation()
    observation["disk"] = {"roles": [
        {"role": "ROOT_FS", "path": "/", "state": "UNAVAILABLE",
         "reason": "statvfs failed"}], "others": []}
    out = ra.detect(**observation)
    codes = {row["code"] for row in out["codes_not_evaluated"]}
    assert {"DISK_WARNING", "DISK_CRITICAL", "INODE_CRITICAL",
            "PROTECTED_RESOURCE_AT_RISK"} <= codes
    # No disk figure was read, so no disk verdict was issued — the other codes
    # in this document are still graded, which is the point of keeping the
    # gap per-code rather than per-call.
    assert [c for c in out["codes_emitted"]
            if c in ("DISK_WARNING", "DISK_CRITICAL", "INODE_CRITICAL",
                     "PROTECTED_RESOURCE_AT_RISK")] == []
    assert "unread is not below the threshold" in (
        {row["code"]: row["why"] for row in out["codes_not_evaluated"]}["DISK_WARNING"]
        .lower())


def test_a_graded_code_that_found_nothing_is_separate_from_an_ungraded_one():
    out = ra.detect(disk={"roles": [
        {"role": "ROOT_FS", "path": "/", "state": "PRESENT",
         "mount_point": "/", "used_percent": 4.0, "free_bytes": 10 ** 12,
         "inodes": {"used_percent": 3.0}}], "others": []})
    assert "DISK_WARNING" in out["codes_graded"]
    assert "DISK_CRITICAL" in out["codes_graded"]
    assert "DISK_WARNING" not in out["codes_emitted"]
    assert "MEMORY_PRESSURE" not in out["codes_graded"]
    assert out["coverage"]["note"].startswith("a code in `codes_graded`")


def test_no_input_at_all_grades_nothing():
    out = ra.detect()
    assert out["codes_emitted"] == []
    assert out["coverage"]["graded"] == 0
    assert len(out["codes_not_evaluated"]) == len(ra.CODES)


def test_every_code_can_say_what_input_it_would_need():
    listed = {row["code"] for row in ra.inputs_needed()}
    assert listed == set(ra.CODES)


def test_a_docker_document_without_space_is_not_two_codes_graded_clean():
    """The defect this test was written for, found by running the CLI once.

    A caller that does not ask for `docker system df` supplies no `space` key.
    `_unavailable` recognized only a failure DICT, so `None` read as a
    successful measurement: `sections` came back empty, nothing crossed a
    threshold, and DOCKER_STORAGE_HIGH and BUILD_CACHE_HIGH landed in
    `codes_graded` — the list that means "looked for and not found". The
    command printed `codes graded 12 of 15` where two of the twelve had never
    been looked at, and "the Docker store is fine" is a claim nothing could
    support.

    Both halves are asserted: the two codes are absent from `codes_graded`, and
    they are present in `codes_not_evaluated` with the cost stated — a code that
    vanished from both lists would be a different bug with the same symptom.
    """
    observation = full_observation()
    observation["docker"].pop("space")
    out = ra.detect(**observation)
    assert "DOCKER_STORAGE_HIGH" not in out["codes_graded"]
    assert "BUILD_CACHE_HIGH" not in out["codes_graded"]
    reasons = {row["code"]: row["why"] for row in out["codes_not_evaluated"]}
    assert "22s" in reasons["DOCKER_STORAGE_HIGH"]
    assert "not the same as finding 0" in reasons["DOCKER_STORAGE_HIGH"]
    # And the other two Docker codes still grade, which is the reason the four
    # reads are one document rather than one call: the expensive one being
    # absent must not cost the caller the three cheap ones.
    assert "CONTAINER_WRITABLE_LAYER_LARGE" in out["codes_graded"]


def test_a_docker_document_that_was_never_supplied_grades_no_docker_code():
    out = ra.detect(host={"memory": {"total_bytes": 1, "used_bytes": 0}})
    for code in ("DOCKER_STORAGE_HIGH", "BUILD_CACHE_HIGH",
                 "CONTAINER_WRITABLE_LAYER_LARGE", "CONTAINER_LOG_LARGE"):
        assert code not in out["codes_graded"], code
        assert code in {row["code"] for row in out["codes_not_evaluated"]}, code


# ---------------------------------------------------------------------------
# 3b · freshness can go stale, which is the only thing that makes it a field
# ---------------------------------------------------------------------------

def test_a_signal_aged_against_a_later_clock_is_stale():
    """§26. Aged against its own instant, every signal is FRESH by construction.

    `detect` stamps `observed_at` when the measurement was taken and ages
    against `now`, the reader's clock. A caller that read a persisted
    observation passes its own clock — and if it does not, the field can only
    ever say FRESH or UNKNOWN, which is a constant wearing a window's name.
    """
    observation = full_observation()
    fresh = ra.detect(**observation)
    stale = ra.detect(**{**observation,
                         "now": "2026-09-14T18:00:00+08:00"})
    assert {row["freshness"]["state"] for row in fresh["signals"]} == {"FRESH"}
    states = {row["freshness"]["state"] for row in stale["signals"]}
    assert states == {"STALE"}, states
    # The window it was measured against is the source's own, not a default:
    # the Docker reads are allowed to be older than the host reads.
    row = next(r for r in stale["signals"] if r["source"] == "host-memory")
    assert row["freshness"]["within_seconds"] == graph.FRESH_WITHIN_SECONDS["host-memory"]


def test_the_measured_at_a_desired_row_carries_is_the_time_it_is_aged_from():
    """The `desired` branch, where the row's own time is the only real one.

    A daemon grades its last tick. Stamping that tick with the time the
    question was asked discards the one fact that makes the answer reviewable,
    so the row's `measured.measured_at` is the observation time and a tick
    older than the window reports STALE while the rest of the document — read
    now — is FRESH.
    """
    observation = full_observation()
    observation["desired"] = {"signals": [{
        "code": "DESIRED_ACTUAL_MISMATCH", "subject": "runtime:z",
        "measured": {"measured_at": "2026-09-14T04:00:00+08:00"},
        "window": "at observation time", "why": "m"}]}
    observation["observed_at"] = "2026-09-14T12:00:00+08:00"
    out = ra.detect(**observation)
    row = next(r for r in out["signals"] if r["code"] == "DESIRED_ACTUAL_MISMATCH")
    assert row["observed_at"] == "2026-09-14T04:00:00+08:00"
    assert row["freshness"]["state"] == "STALE"


def test_a_signal_with_no_clock_at_all_is_unknown_rather_than_fresh():
    out = ra.detect(desired={"signals": [{
        "code": "DESIRED_ACTUAL_MISMATCH", "subject": "runtime:z",
        "measured": {}, "window": "w", "why": "m"}]})
    assert out["signals"][0]["freshness"]["state"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# 4 · the host is graded once, not once per path that leads to it
# ---------------------------------------------------------------------------

def test_two_roles_landing_on_one_filesystem_are_graded_once():
    """ROOT_FS and DOCKER_FS both land on `/` here.

    `disk_status()` warns about exactly this one layer down: a threshold
    applied per role is applied twice to one filesystem. Two CRITICAL rows for
    one disk reads as two problems, and the second one is not even wrong.
    """
    out = ra.detect(disk={"roles": [
        {"role": "ROOT_FS", "path": "/", "state": "PRESENT", "mount_point": "/",
         "used_percent": 93.4, "inodes": {"used_percent": 12.0}},
        {"role": "DOCKER_FS", "path": "/var/lib/docker", "state": "PRESENT",
         "mount_point": "/", "used_percent": 93.4,
         "inodes": {"used_percent": 12.0}},
    ], "others": []})
    rows = [r for r in out["signals"] if r["code"] == "DISK_CRITICAL"]
    assert len(rows) == 1
    assert rows[0]["subject"] == "filesystem:/"
    assert rows[0]["measured"]["roles"] == ["ROOT_FS", "DOCKER_FS"]


def test_a_protected_resource_is_only_at_risk_when_the_docker_root_is():
    """The cross-check is anchored to the Docker root, not to any disk.

    A protected container does not stand on `/boot`. Naming it against every
    filesystem in a warning tier would turn one disk problem into a list of
    every protected resource on the host, which is how an alert becomes
    background noise.
    """
    out = ra.detect(
        disk={"roles": [
            {"role": "ROOT_FS", "path": "/", "state": "PRESENT",
             "mount_point": "/", "used_percent": 4.0,
             "inodes": {"used_percent": 1.0}},
            {"role": "DOCKER_FS", "path": "/opt/docker", "state": "PRESENT",
             "mount_point": "/srv", "used_percent": 4.0,
             "inodes": {"used_percent": 1.0}}],
            "others": [{"target": "/boot", "used_percent": "88%",
                        "inodes_percent": "1%"}]},
        hygiene={"docker": {"containers": [
            {"name": "container0", "classification": "PROTECTED",
             "running": True}]}})
    assert [r for r in out["signals"]
            if r["code"] == "PROTECTED_RESOURCE_AT_RISK"] == []
    assert [r["subject"] for r in out["signals"]
            if r["code"] == "DISK_WARNING"] == ["filesystem:/boot"]


# ---------------------------------------------------------------------------
# 5 · this module cannot act
# ---------------------------------------------------------------------------

def test_the_module_executes_nothing():
    guard = _load("serveros_mutation_guard",
                  str(REPO / "skills" / "serveros" / "scripts" / "mutation_guard.py"))
    source = (RUNTIME / "resource_anomaly.py").read_text()
    hits = guard.scan_file("runtime/resource_anomaly.py", source)["hits"]
    assert [h for h in hits if h["kind"] in ("mutation", "host_reader")] == []
    poisoned = source + (
        "\n\ndef _poison():\n"
        "    import subprocess\n"
        "    subprocess.run(['docker', 'prune', '-f'], check=False)\n"
    )
    assert [h for h in guard.scan_file("runtime/resource_anomaly.py", poisoned)["hits"]
            if h["kind"] == "mutation"]


_NO_WAY_TO_RUN_ANYTHING = (
    "subprocess", "os.system", "Popen", "shutil.rmtree", "os.remove",
    "os.unlink", "os.rmdir", "pathlib.Path.unlink",
)


def test_the_module_reads_no_host_of_its_own():
    """§28. Every input is a document another producer already measured.

    The module talks about Docker constantly — in prose, in key names and in
    the codes themselves — so "does it mention docker" is not the question.
    The question is whether there is a way to RUN one, and that is a small
    closed list of names.
    """
    source = (RUNTIME / "resource_anomaly.py").read_text()
    # The import block, not the prose: a docstring saying "there is no
    # `docker` call in this file" must not be able to fail this test.
    body = source.split('"""', 2)[2]
    for forbidden in _NO_WAY_TO_RUN_ANYTHING:
        assert forbidden not in body, forbidden


def test_the_no_way_to_run_anything_list_is_not_vacuous():
    """The list above is only worth asserting if it would catch the real thing."""
    poisoned = (RUNTIME / "resource_anomaly.py").read_text() + (
        "\n\ndef _poison():\n    import subprocess\n"
        "    subprocess.run(['df', '-h'], check=False)\n")
    for forbidden in ("subprocess",):
        assert forbidden in poisoned.split('"""', 2)[2]


# ---------------------------------------------------------------------------
# §25 · the consequence that is permitted, and the two that are not
# ---------------------------------------------------------------------------

def _detection(*, codes=(), ungraded=None, signals=None):
    """A `detect`-shaped document with only the parts `admission` reads."""
    return {
        "observed_at": OBSERVED_AT,
        "signals": signals if signals is not None else [
            {"code": code, "subject": "test", "severity": "high"} for code in codes],
        "codes_emitted": list(codes),
        "codes_graded": [c for c in ra.CODES if c not in codes],
        "codes_not_evaluated": ungraded if ungraded is not None else [],
    }


def test_the_admission_levels_match_the_contract():
    """The transcription is a transcription, and a test is what keeps it one."""
    gates = CONTRACT_DOC["resource_admission"]["gates"]
    assert {code: entry["level"] for code, entry in gates.items()} == ra.ADMISSION_LEVELS
    assert ra.load_admission() == ra.ADMISSION_LEVELS


def test_the_admission_table_names_a_code_that_exists():
    """A gate on a code that is never emitted is a closed door in an empty room."""
    assert set(ra.ADMISSION_LEVELS) <= set(ra.CODES)


def test_the_two_forbidden_responses_are_the_contracts_own_words():
    document = CONTRACT_DOC["resource_admission"]
    assert document["forbidden_responses"] == list(ra.FORBIDDEN_RESPONSES)
    assert document["acts_on_existing_resources"] is False


def test_four_codes_refuse_and_four_advise():
    """The split is the whole of §25's permission, so the membership is asserted.

    Not as trivia: a code promoted to `refuse` by an edit would close the host
    to new work, and a code demoted would open it under pressure. Either one
    should have to change this list on purpose.

    `SWAP_PRESSURE` crossed this line in the Semantics V2 round and
    `MEMORY_PRESSURE_ACTIVE` was added on the other side of it, so the two
    counts moved by one each and the total did not change. The pair is
    asserted together rather than as two numbers because the point is the
    trade: the code that measures pressure refuses, and the code that measures
    a watermark no longer does.
    """
    assert ra.REFUSING_CODES == ("DISK_CRITICAL", "INODE_CRITICAL",
                                 "MEMORY_PRESSURE", "MEMORY_PRESSURE_ACTIVE")
    assert ra.ADVISORY_CODES == ("BUILD_CACHE_HIGH", "DISK_WARNING",
                                 "DOCKER_STORAGE_HIGH", "SWAP_PRESSURE")


def test_a_fired_refusing_code_refuses_a_new_creation():
    answer = ra.admission(_detection(codes=["DISK_CRITICAL"]))
    assert answer["decision"] == ra.REFUSE
    assert [row["code"] for row in answer["blocking"]] == ["DISK_CRITICAL"]
    assert answer["acts_on_existing_resources"] is False


def test_a_fired_advisory_code_does_not_refuse():
    answer = ra.admission(_detection(codes=["BUILD_CACHE_HIGH", "DISK_WARNING"]))
    assert answer["decision"] == ra.ADMIT
    assert [row["code"] for row in answer["advisory"]] == ["BUILD_CACHE_HIGH",
                                                           "DISK_WARNING"]


def test_a_code_with_no_gate_entry_cannot_refuse_anything():
    """The list is closed on purpose. A code added later is advisory by default.

    The reverse default — unlisted means gating — would make every future code
    a decision nobody made.
    """
    answer = ra.admission(_detection(codes=["TUNNEL_UNHEALTHY",
                                            "PROTECTED_RESOURCE_AT_RISK"]))
    assert answer["decision"] == ra.ADMIT
    assert answer["blocking"] == []


def test_a_gate_that_was_never_graded_is_not_a_gate_that_passed():
    """§27's rule, one level up: an unmeasured disk is not a disk with room."""
    answer = ra.admission(_detection(ungraded=[
        {"code": "DISK_CRITICAL", "why": "the filesystem roles were not supplied"}]))
    assert answer["decision"] == ra.NOT_ASSESSED
    assert [row["code"] for row in answer["not_assessed"]] == ["DISK_CRITICAL"]
    assert "the filesystem roles were not supplied" in answer["not_assessed"][0]["why"]
    # `clear` means graded and did not fire — the gates this detection reports
    # as covered, and NOT the one that was never looked at. `SWAP_PRESSURE` is
    # absent from this list now because it is no longer a gate at all, which is
    # a different absence from `DISK_CRITICAL`'s.
    assert sorted(answer["clear"]) == ["INODE_CRITICAL", "MEMORY_PRESSURE",
                                       "MEMORY_PRESSURE_ACTIVE"]


def test_a_detection_with_no_coverage_report_is_not_assessed():
    """Absence of the key is absence of the evidence, and is spelled that way."""
    answer = ra.admission({"signals": []})
    assert answer["decision"] == ra.NOT_ASSESSED
    assert sorted(row["code"] for row in answer["not_assessed"]) == sorted(
        ra.REFUSING_CODES)


def test_a_fired_gate_outranks_an_ungraded_one():
    """A refusal does not become a question because another input is missing."""
    answer = ra.admission(_detection(
        codes=["MEMORY_PRESSURE_ACTIVE"],
        ungraded=[{"code": "DISK_CRITICAL", "why": "not measured"}]))
    assert answer["decision"] == ra.REFUSE
    assert [row["code"] for row in answer["not_assessed"]] == ["DISK_CRITICAL"]


def test_an_all_graded_clean_detection_is_the_only_plain_allow():
    answer = ra.admission(_detection())
    assert answer["decision"] == ra.ADMIT
    assert answer["not_assessed"] == []
    assert sorted(answer["clear"]) == sorted(ra.REFUSING_CODES)


def test_the_kind_of_thing_being_created_does_not_buy_a_cheaper_answer():
    """The contract refuses every kind at the same tier, so the label is a label."""
    detection = _detection(codes=["MEMORY_PRESSURE"])
    answers = {kind: ra.admission(detection, request=kind)
               for kind in CONTRACT_DOC["resource_admission"]["request_kinds"]
               if kind != "note"}
    assert {answer["decision"] for answer in answers.values()} == {ra.REFUSE}
    assert answers["persistent"]["request"] == "persistent"


def test_admission_has_no_argument_for_something_that_already_exists():
    """The forbidden responses are inexpressible here, not merely declined.

    A shape test rather than a behaviour test: if `admission` ever grows a
    `container`/`pid`/`runtime` parameter, the two forbidden answers become
    reachable by one edit, and this fails first.
    """
    import inspect

    parameters = set(inspect.signature(ra.admission).parameters)
    assert parameters == {"detection", "request", "levels"}
    assert not parameters & {"container", "containers", "pid", "runtimes",
                             "runtime_groups", "resources", "mounts"}
    answer = ra.admission(_detection(codes=["MEMORY_PRESSURE"]))
    assert answer["forbidden"] == list(ra.FORBIDDEN_RESPONSES)


def _code_references(path) -> str:
    """Every name and string literal in a file EXCEPT the docstrings.

    The distinction is the whole reason this helper exists. A file that says
    "`runtime/resource_anomaly.py` grades fifteen codes" in a sentence has not
    referenced the module in any sense a loader cares about, and a scanner that
    counts prose as a reference forces the prose to stop naming the thing it is
    about — which makes the documentation worse to satisfy the gate. So the
    docstrings are dropped and everything else is kept: string literals (which
    is how a dynamic `_load` names a file), imports, attribute names and bare
    names. There is no import path to this module — it is reached by filename
    or load name through `spec_from_file_location` — so those are the two
    spellings that can actually reach it, and both are literals.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            docstrings.add(id(body[0].value))
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and id(node) not in docstrings:
            parts.append(str(node.value))
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            parts.append(getattr(node, "module", "") or "")
    return "\n".join(parts)


def test_the_reference_scanner_reads_code_and_not_prose(tmp_path):
    """The positive control for the scanner above, in both directions.

    A control that only proved "it finds view.py" would pass just as happily if
    the docstring exclusion did not work — and then the next comment someone
    writes about this module turns into a phantom caller. So both directions
    are asserted: prose is invisible, and a literal next to prose is not.
    """
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""This module mentions runtime/resource_anomaly.py in prose."""\n'
        'load("serveros_runtime_resource_anomaly", "resource_anomaly.py")\n')
    found = _code_references(sample)
    assert "resource_anomaly.py" in found, found
    assert "in prose" not in found, found


def test_nothing_in_the_tree_yet_creates_through_the_admission_gate():
    """`wired_into_a_creation_path: false`, asserted instead of hoped for.

    §25 permits the contract and the tests when wiring would expand the round.
    That permission is only honest while the claim is true, so the claim is
    what is tested: no module outside this one and its tests calls `admission`.

    The loader list is CODE references only — see `_code_references`. A prose
    mention of this module is not a way to reach it, and `runtime/alerting.py`
    mentions it in a docstring while loading nothing.
    """
    loaders: list[str] = []
    callers: list[str] = []
    for path in sorted(REPO.rglob("*.py")):
        if "/.git" in str(path) or path.name == "resource_anomaly.py":
            continue
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith("tests/") or "worktrees" in relative:
            continue
        text = _code_references(path)
        # A file can only reach this module by naming the file or the load name
        # — there is no package import path to it. So "who could call it" has a
        # small answer, and a bare `admission(` elsewhere (`serverosd admission`,
        # the agent-control router's own admission) is a different word entirely.
        if "resource_anomaly.py" not in text and "serveros_runtime_resource_anomaly" not in text:
            continue
        loaders.append(relative)
        if "admission(" in path.read_text():
            callers.append(relative)
    assert callers == [], callers
    assert loaders == ["runtime/view.py"], loaders
    assert CONTRACT_DOC["resource_admission"]["wired_into_a_creation_path"] is False
    # The one loader uses DETECTION, which is the half that may be wired. If
    # this ever becomes `resource_anomaly_mod.admission(`, the claim above is
    # false in the same commit, and this fails with it.
    assert "resource_anomaly_mod.detect(" in (REPO / "runtime" / "view.py").read_text()


# ---------------------------------------------------------------------------
# §24 · the destructive API that must not exist
# ---------------------------------------------------------------------------

# The verbs §24 names, as name-shaped tokens rather than substrings: `_rm` in
# `_rmtree` is the thing, and `rm` inside `confirm` is not.
_DESTRUCTIVE_NAME = (
    re.compile(r"(^|_)(stop|kill|remove|rm|rmi|prune|delete|destroy|down|reclaim)"
               r"($|_)")
)


def _public_callables(module):
    return [name for name, value in vars(module).items()
            if callable(value) and getattr(value, "__module__", None) == module.__name__]


def test_no_resource_surface_offers_a_destructive_verb():
    """§24's second clause: there must be nothing to call.

    §24 does not say "do not call the destructive API" — it says the API should
    not exist this round. A promise not to call something is a promise; a
    surface with no such name is a property, and only the second one survives
    a later author who has not read the brief.
    """
    policy = _load("serveros_runtime_resource_policy", "resource_policy.py")
    for module in (ra, policy):
        names = _public_callables(module)
        assert names, module.__name__
        offenders = [name for name in names if _DESTRUCTIVE_NAME.search(name)]
        assert offenders == [], (module.__name__, offenders)


def test_the_destructive_name_pattern_is_not_vacuous():
    """The pattern above is only worth asserting if it catches the real thing."""
    for poisoned in ("reclaim_now", "prune_store", "docker_rm", "stop_container",
                     "kill_high_cpu"):
        assert _DESTRUCTIVE_NAME.search(poisoned), poisoned
    for innocent in ("confirm", "format_bytes", "reconciliation_required",
                     "request_kinds", "downtime_seconds_warning"):
        assert not _DESTRUCTIVE_NAME.search(innocent), innocent


def test_the_modules_imported_for_that_check_are_the_ones_this_round_added():
    """Guards the guard: an empty module would make the check above pass."""
    assert "detect" in _public_callables(ra)
    assert "resolve" in _public_callables(
        _load("serveros_runtime_resource_policy", "resource_policy.py"))
