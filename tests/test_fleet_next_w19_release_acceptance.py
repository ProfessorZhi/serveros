"""W19 independent release gate: the decisions, and the facts they are decided on.

Two verdicts, because #515 itself separates them ("开发合并 ≠ 正式 RELEASED"):

*   `landing_verdict` — may this tree be merged into `main`?
*   `release_verdict` — may this be called V1?

A real-device gate that has never run does not stop a development merge, and
does stop a release. Everything here is decided from measured facts, so the
tests build the facts rather than reading a report.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (ROOT / "integrations" / "fleet-next-w19-release-acceptance"
               / "w19_gate.py")


def gate_module():
    spec = importlib.util.spec_from_file_location("w19_gate", str(MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules["w19_gate"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def w19():
    return gate_module()


def all_ready(w19, **overrides):
    return {name: overrides.get(name, w19.READY) for name in w19.LAYERS}


def build(w19, findings=(), **overrides):
    return w19.Gate.from_layers(all_ready(w19, **overrides), findings=list(findings))


# --------------------------------------------------------------------------- #
# the shape of a gate
# --------------------------------------------------------------------------- #
def test_the_layers_are_the_six_the_directive_names(w19):
    assert w19.LAYERS == ("CODE", "UNIT", "CI", "INDEPENDENT_QA", "ARTIFACT",
                          "REAL_DEVICE")


def test_a_gate_with_a_layer_missing_is_not_ready(w19):
    layers = all_ready(w19)
    del layers["UNIT"]
    gate = w19.Gate.from_layers(layers, findings=[])
    assert gate.landing_verdict() == w19.BLOCKED
    assert "UNIT" in gate.missing_layers()


def test_an_unknown_verdict_word_is_refused_rather_than_read_as_pass(w19):
    layers = all_ready(w19)
    layers["CI"] = "GREEN"
    with pytest.raises(ValueError):
        w19.Gate.from_layers(layers, findings=[])


# --------------------------------------------------------------------------- #
# landing vs release
# --------------------------------------------------------------------------- #
def test_a_ready_landing_with_real_device_not_run_is_signed(w19):
    gate = build(w19, REAL_DEVICE=w19.NOT_RUN)
    assert gate.landing_verdict() == w19.READY


def test_the_same_gate_does_not_release(w19):
    gate = build(w19, REAL_DEVICE=w19.NOT_RUN)
    assert gate.release_verdict() == w19.BLOCKED


def test_a_released_gate_needs_the_real_device_layer_ready(w19):
    gate = build(w19)
    assert gate.release_verdict() == w19.READY


@pytest.mark.parametrize("layer", ["CODE", "UNIT", "CI", "INDEPENDENT_QA",
                                   "ARTIFACT"])
def test_one_unready_merge_layer_blocks_the_landing(w19, layer):
    gate = build(w19, REAL_DEVICE=w19.NOT_RUN, **{layer: w19.BLOCKED})
    assert gate.landing_verdict() == w19.BLOCKED


def test_an_incomplete_merge_layer_blocks_the_landing_too(w19):
    gate = build(w19, REAL_DEVICE=w19.NOT_RUN, UNIT=w19.INCOMPLETE)
    assert gate.landing_verdict() == w19.BLOCKED


# --------------------------------------------------------------------------- #
# findings travel with the verdict
# --------------------------------------------------------------------------- #
def test_a_release_scoped_blocker_leaves_the_landing_signed(w19):
    finding = w19.Finding(w19.BLOCKER, "CODE", "owner:monitor",
                          "a.py:1", "the two readers disagree", w19.RELEASE)
    gate = build(w19, findings=[finding], REAL_DEVICE=w19.NOT_RUN)
    assert gate.landing_verdict() == w19.READY
    assert gate.release_verdict() == w19.BLOCKED
    assert [f.summary for f in gate.blockers_for(w19.RELEASE)] == [
        "the two readers disagree"]


def test_a_landing_scoped_blocker_blocks_the_release_as_well(w19):
    finding = w19.Finding(w19.BLOCKER, "CI", "owner:ci", "x.yml",
                          "the gate is red", w19.LANDING)
    gate = build(w19, findings=[finding])
    assert gate.landing_verdict() == w19.BLOCKED
    assert gate.release_verdict() == w19.BLOCKED


def test_a_note_never_moves_a_verdict(w19):
    note = w19.Finding(w19.NOTE, "CODE", "owner:x", "p", "a portability note",
                       w19.RELEASE)
    gate = build(w19, findings=[note], REAL_DEVICE=w19.NOT_RUN)
    assert gate.landing_verdict() == w19.READY
    assert gate.release_verdict() == w19.BLOCKED
    assert gate.blockers_for(w19.RELEASE) == ()


def test_a_finding_naming_an_unknown_layer_is_refused(w19):
    with pytest.raises(ValueError):
        w19.Finding(w19.BLOCKER, "SECURITY", "o", "p", "s", w19.RELEASE)


# --------------------------------------------------------------------------- #
# the PR retire map
# --------------------------------------------------------------------------- #
def test_a_head_that_is_an_ancestor_of_main_is_not_a_candidate(w19):
    record = w19.PrFacts(number=1, head="a", in_main=True, released=False,
                         absent=(), differing=(), own_prefixes=("integrations/x/",))
    assert record.classify() == w19.RELEASE_NOT_RUN


def test_a_merged_and_released_pr_is_verified_and_merged(w19):
    record = w19.PrFacts(number=1, head="a", in_main=True, released=True,
                         absent=(), differing=(), own_prefixes=("integrations/x/",))
    assert record.classify() == w19.VERIFIED_AND_MERGED


def test_a_pr_with_no_own_delta_is_superseded_by_its_successor(w19):
    record = w19.PrFacts(number=2, head="b", in_main=False, released=False,
                         absent=(), differing=(), own_prefixes=("integrations/x/",))
    assert record.classify() == w19.SUPERSEDED_ANCESTOR
    assert record.successor() == w19.MAIN


def test_a_main_based_additive_pr_is_own_delta_pending(w19):
    record = w19.PrFacts(number=3, head="c", in_main=False, released=False,
                         absent=("tests/t.py",), differing=(),
                         own_prefixes=("tests/t.py",), based_on_tip=True)
    assert record.classify() == w19.OWN_DELTA_PENDING


def test_an_addition_from_a_base_behind_the_tip_still_has_its_own_delta(w19):
    """#519's measured shape: merge-base behind main, but nothing of main's changed.

    A branch that only adds files cannot conflict with what it never touched, so
    "its base has moved" is not a reason to refuse it. The red `pull_request` job
    those branches show is a property of the merge ref they were run against, not
    of their delta — measured separately, not asserted here.
    """
    record = w19.PrFacts(number=3, head="c", in_main=False, released=False,
                         absent=("tests/t.py",), differing=(),
                         own_prefixes=("tests/t.py",), based_on_tip=False)
    assert record.classify() == w19.OWN_DELTA_PENDING


def test_the_base_position_is_reported_but_does_not_decide(w19):
    common = dict(number=3, head="c", in_main=False, released=False,
                  absent=("tests/t.py",), differing=(), own_prefixes=("tests/t.py",))
    on_tip = w19.PrFacts(based_on_tip=True, **common)
    behind = w19.PrFacts(based_on_tip=False, **common)
    assert on_tip.classify() == behind.classify() == w19.OWN_DELTA_PENDING


def test_an_addition_outside_the_prs_own_paths_is_blocked_owner(w19):
    record = w19.PrFacts(number=6, head="f", in_main=False, released=False,
                         absent=("tests/t.py", "runtime/paths.py"), differing=(),
                         own_prefixes=("tests/t.py",), based_on_tip=True)
    assert record.classify() == w19.BLOCKED_OWNER


def test_a_file_the_pr_edited_outside_its_own_paths_is_blocked_owner(w19):
    record = w19.PrFacts(number=4, head="d", in_main=False, released=False,
                         absent=(), differing=("tests/test_direct_lark_notify.py",),
                         touched=("tests/test_direct_lark_notify.py",),
                         own_prefixes=("integrations/x/",), based_on_tip=True)
    assert record.classify() == w19.BLOCKED_OWNER


# --------------------------------------------------------------------------- #
# the measurement is real git, not a hand-written table
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_AUTHOR_NAME="w19", GIT_AUTHOR_EMAIL="w19@invalid",
               GIT_COMMITTER_NAME="w19", GIT_COMMITTER_EMAIL="w19@invalid")
    done = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, env=env, check=True)
    return done.stdout.strip()


def _commit(repo: Path, message: str, files: dict) -> str:
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def small_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(repo, "base", {"a.txt": b"one\n"})
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    added = _commit(repo, "feature", {"b.txt": b"two\n", "a.txt": b"one\n"})
    _git(repo, "checkout", "-q", branch)
    return repo, branch, added


def test_the_comparison_sees_an_added_file(w19, small_repo):
    repo, branch, added = small_repo
    facts = w19.compare(repo, main=branch, head=added)
    assert facts.absent == ("b.txt",)
    assert facts.differing == ()


def test_the_comparison_sees_a_modified_file(w19, small_repo):
    repo, branch, _added = small_repo
    _git(repo, "checkout", "-q", "-b", "edit")
    edited = _commit(repo, "edit a", {"a.txt": b"one changed\n"})
    facts = w19.compare(repo, main=branch, head=edited)
    assert facts.differing == ("a.txt",)


def test_a_head_inside_main_is_reported_as_in_main(w19, small_repo):
    repo, branch, added = small_repo
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    facts = w19.compare(repo, main=branch, head=added)
    assert facts.in_main is True
    # ...and it stops being a delta at the same moment: the merge took it.
    assert facts.absent == ()


def test_a_head_outside_main_is_not(w19, small_repo):
    repo, branch, _added = small_repo
    _git(repo, "checkout", "-q", "-b", "open")
    open_head = _commit(repo, "open", {"c.txt": b"three\n"})
    facts = w19.compare(repo, main=branch, head=open_head)
    assert facts.in_main is False


def test_the_base_is_compared_to_the_tip_not_to_a_branch_name(w19, small_repo):
    repo, branch, added = small_repo
    facts = w19.compare(repo, main=branch, head=added)
    assert facts.based_on_tip is True


# --------------------------------------------------------------------------- #
# the pins file -> the retire map
# --------------------------------------------------------------------------- #
def measure_module(w19):
    directory = MODULE_PATH.parent
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    path = directory / "w19_measure.py"
    spec = importlib.util.spec_from_file_location("w19_measure", str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["w19_measure"] = module
    spec.loader.exec_module(module)
    return module


def test_a_pin_is_turned_into_a_retire_map(w19, small_repo):
    measure = measure_module(w19)
    repo, branch, _added = small_repo
    pin = {"main": branch,
           "prs": [{"number": 7, "ref": "feature", "own_prefixes": ["b.txt"]}]}
    result = measure.measure(repo, pin)
    assert [pr["classification"] for pr in result["prs"]] == [
        w19.OWN_DELTA_PENDING]
    assert result["prs"][0]["absent"] == ["b.txt"]


def test_the_map_names_the_main_commit_it_was_measured_against(w19, small_repo):
    measure = measure_module(w19)
    repo, branch, _added = small_repo
    result = measure.measure(repo, {"main": branch,
                                    "prs": [{"number": 7, "ref": "feature",
                                             "own_prefixes": ["b.txt"]}]})
    assert len(result["main"]) == 40
    assert len(result["prs"][0]["head"]) == 40


def test_a_pin_that_names_no_prs_is_refused(w19, small_repo):
    measure = measure_module(w19)
    repo, branch, _added = small_repo
    with pytest.raises(ValueError):
        measure.measure(repo, {"main": branch, "prs": []})


def test_a_pin_naming_a_ref_that_does_not_exist_is_an_error_not_a_zero(w19,
                                                                      small_repo):
    measure = measure_module(w19)
    repo, branch, _added = small_repo
    with pytest.raises(RuntimeError):
        measure.measure(repo, {"main": branch,
                               "prs": [{"number": 8, "ref": "no-such-ref",
                                        "own_prefixes": []}]})


def test_a_file_main_moved_on_that_the_branch_never_touched_is_not_its_delta(
        w19, small_repo):
    repo, branch, added = small_repo
    _commit(repo, "main moves on", {"a.txt": b"one, moved on\n"})
    facts = w19.compare(repo, main=branch, head=added)
    assert facts.differing == ("a.txt",)
    assert facts.touched == ()
    record = w19.PrFacts.from_comparison(facts, number=9, own_prefixes=("b.txt",))
    assert record.classify() == w19.OWN_DELTA_PENDING


def test_a_file_the_branch_did_edit_is_its_own_delta_and_not_landable(w19,
                                                                     small_repo):
    repo, branch, _added = small_repo
    _git(repo, "checkout", "-q", "feature")
    edited = _commit(repo, "feature edits a.txt", {"a.txt": b"feature's own\n"})
    facts = w19.compare(repo, main=branch, head=edited)
    assert facts.touched == ("a.txt",)
    record = w19.PrFacts.from_comparison(facts, number=10,
                                         own_prefixes=("b.txt", "a.txt"))
    assert record.classify() == w19.BLOCKED_OWNER


def test_a_pr_whose_head_sits_inside_another_prs_head_is_superseded(w19,
                                                                   small_repo):
    measure = measure_module(w19)
    repo, branch, added = small_repo
    _git(repo, "checkout", "-q", "feature")
    later = _commit(repo, "the later lane adds more",
                    {"b.txt": b"two\n", "c.txt": b"three\n"})
    _git(repo, "checkout", "-q", branch)
    pin = {"main": branch,
           "prs": [{"number": 11, "ref": added, "own_prefixes": ["b.txt"]},
                   {"number": 12, "ref": later,
                    "own_prefixes": ["b.txt", "c.txt"]}]}
    result = measure.measure(repo, pin)
    by_number = {pr["number"]: pr for pr in result["prs"]}
    assert by_number[11]["classification"] == w19.SUPERSEDED_ANCESTOR
    assert by_number[11]["successor"] == 12
    assert by_number[12]["classification"] == w19.OWN_DELTA_PENDING


def test_a_declared_stem_covers_the_files_it_starts(w19):
    record = w19.PrFacts(number=13, head="g", in_main=False, released=False,
                         absent=("tests/test_fleet_v1_milestone_batch.py",
                                 "tests/test_fleet_v1_milestone_runner.py"),
                         differing=(),
                         own_prefixes=("tests/test_fleet_v1_milestone_",))
    assert record.classify() == w19.OWN_DELTA_PENDING


def test_a_declared_stem_does_not_cover_a_neighbour_with_another_stem(w19):
    record = w19.PrFacts(number=14, head="h", in_main=False, released=False,
                         absent=("tests/test_fleet_v1_stage_manifest.py",),
                         differing=(),
                         own_prefixes=("tests/test_fleet_v1_milestone_",))
    assert record.classify() == w19.BLOCKED_OWNER


# --------------------------------------------------------------------------- #
# the evidence document the verdict is read from
# --------------------------------------------------------------------------- #
def evidence_module(w19):
    directory = MODULE_PATH.parent
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    path = directory / "w19_evidence.py"
    spec = importlib.util.spec_from_file_location("w19_evidence", str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules["w19_evidence"] = module
    spec.loader.exec_module(module)
    return module


def a_measured_row(w19, classification=None):
    return {"number": 99, "ref": "p99", "head": "0" * 40,
            "classification": classification or w19.OWN_DELTA_PENDING,
            "in_main": False, "base": "0" * 40, "based_on_tip": False,
            "absent": ["a.txt"], "differing": [], "touched": [],
            "successor": w19.MAIN, "note": ""}


def test_the_evidence_document_signs_the_landing_and_refuses_the_release(w19):
    evidence = evidence_module(w19)
    document = evidence.build({"main": "1" * 40, "prs": [a_measured_row(w19)],
                               "counts": {w19.OWN_DELTA_PENDING: 1}})
    assert document["gate"]["landing_verdict"] == w19.READY
    assert document["gate"]["release_verdict"] == w19.BLOCKED


def test_the_document_carries_the_retire_map_it_was_built_from(w19):
    evidence = evidence_module(w19)
    row = a_measured_row(w19)
    document = evidence.build({"main": "1" * 40, "prs": [row], "counts": {
        w19.OWN_DELTA_PENDING: 1}})
    assert [pr["number"] for pr in document["retire_map"]["prs"]] == [99]
    assert document["retire_map"]["main"] == "1" * 40


def test_the_document_names_the_real_device_gate_as_not_run(w19):
    evidence = evidence_module(w19)
    document = evidence.build({"main": "1" * 40, "prs": [a_measured_row(w19)],
                               "counts": {}})
    layers = {item["layer"]: item["verdict"] for item in document["gate"]["layers"]}
    assert layers["REAL_DEVICE"] == w19.NOT_RUN


def test_every_recorded_finding_names_an_owner_and_a_path(w19):
    evidence = evidence_module(w19)
    for finding in evidence.RECORDED_FINDINGS:
        assert finding.owner and finding.path and finding.summary
    assert any(f.severity == w19.BLOCKER for f in evidence.RECORDED_FINDINGS)
