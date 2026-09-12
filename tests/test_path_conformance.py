#!/usr/bin/env python3
"""Tests for the directory conformance check (§31–§42).

The subject of this file is a check that did not exist, and the reason it did
not exist is the reason it needs tests: nothing compared a declaration against
the disk, so a declaration could be wrong indefinitely while every run agreed
with itself. `paths.py` declared four closed vocabularies and the only test
touching them asserted the declaration equalled itself.

So these tests are mostly about the check being able to FAIL, and about the
three ways it could quietly stop failing:

  * reporting a clean result when the authority it compares against is
    unreadable — an authority that cannot be read is not an authority that
    forbids nothing;
  * absorbing an undeclared directory through the exception table, which would
    make the table a place findings go to die rather than a place they are
    accounted for;
  * reading the disk it was pointed at rather than this machine's. Every
    surface here is a throwaway tree; the real roots are never touched.

Hermetic: `tmp_path` throughout, and `repo_root` is a fixture directory rather
than this checkout, so the `~/.serveros` comparison is against a contract this
file wrote and not against the one on the machine.
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


paths_mod = _load("serveros_runtime_paths", "paths.py")
conformance = _load("serveros_runtime_conformance", "conformance.py")


def node_contract(files: dict) -> dict:
    return {"schema": "serveros/node-root.v1", "files": files}


@pytest.fixture
def world(tmp_path):
    """A node with its own roots, and a repo holding its own node contract.

    `home` is inside `tmp_path` as well. A check that read the real `~/.serveros`
    would pass or fail depending on what this machine happens to have bound,
    which is the difference between a test and a measurement.
    """

    def build(*, data=(), workspace=(), state=(), artifacts=(), home=(),
              runtime_state=(), node_files=None):
        root = tmp_path / "node"
        data_root = root / "data"
        workspace_root = data_root / "workspace"
        for name, entries in ((data_root, data), (workspace_root, workspace),
                              (data_root / "state/serveros", state),
                              (data_root / "state/serveros/runtime", runtime_state),
                              (data_root / "artifacts", artifacts),
                              (root / "home/.serveros", home)):
            name.mkdir(parents=True, exist_ok=True)
            for entry in entries:
                (name / entry).mkdir(exist_ok=True)

        repo = tmp_path / "repo"
        (repo / "fleet").mkdir(parents=True, exist_ok=True)
        contract = node_contract(node_files if node_files is not None
                                 else {"node.yaml": {}, "targets.yaml": {}, "controller.yaml": {}})
        (repo / conformance.NODE_ROOT_CONTRACT).write_text(
            yaml.safe_dump(contract, sort_keys=False))

        paths = paths_mod.Paths({
            "home": str(root / "home"),
            "data_root": str(data_root),
            "workspace_root": str(workspace_root),
        })
        return conformance.collect(paths, repo_root=repo, home=str(root / "home"))

    return build


def ids(report: dict) -> list[str]:
    return [f["id"] for f in report["findings"]]


def of(report: dict, fid: str, entry: str | None = None) -> list[dict]:
    rows = [f for f in report["findings"] if f["id"] == fid]
    if entry is not None:
        rows = [f for f in rows if f.get("entry") == entry]
    return rows


# ---------------------------------------------------------------------------
# both directions
# ---------------------------------------------------------------------------


def test_an_undeclared_directory_is_reported_not_absorbed(world):
    report = world(data=("appeared-one-day",))
    found = of(report, "UNDECLARED_ENTRY", "appeared-one-day")
    assert len(found) == 1
    assert found[0]["severity"] == "MEDIUM"
    assert found[0]["status"] == "OPEN"


def test_a_declared_directory_that_was_never_created_is_reported(world):
    """§35 names eight areas under `state/serveros`. Six of them do not exist on
    this node, and until this check nothing said so: `admit()` classifies a path
    under `state/serveros/context/` as CANONICAL_STATE whether or not there is a
    `context/` to put it in."""
    report = world(state=("abilities",))
    absent = {f["entry"] for f in of(report, "DECLARED_ENTRY_ABSENT")}
    assert "projections" in absent
    assert "locks" in absent
    assert "abilities" not in absent, "a declared entry that IS present must not be reported"


def test_a_conforming_node_reports_nothing_open(world):
    """The positive control. Every declared area present, nothing extra."""
    report = world(
        data=tuple(paths_mod.DATA_ROOT_TOP_LEVEL),
        workspace=tuple(paths_mod.WORKSPACE_TOP_LEVEL),
        state=tuple(paths_mod.STATE_SERVEROS_TOP_LEVEL),
        runtime_state=tuple(paths_mod.STATE_SERVEROS_RUNTIME),
        artifacts=tuple(paths_mod.ARTIFACT_NAMESPACES),
        home=tuple(paths_mod.HOME_DOT_SERVEROS_ALLOWED),
    )
    assert of(report, "UNDECLARED_ENTRY") == []
    assert of(report, "DECLARED_ENTRY_ABSENT") == []
    assert report["summary"]["blocking_open"] == 0


def test_the_exception_table_is_read_as_cited_not_as_silence(world):
    """An exemption is a finding too, at OBSERVED and with its authority.

    The table exists so that a known, explained entry does not drown the report.
    It must not exist so the entry disappears: a table that silently absorbs
    entries is how a closed vocabulary stops being closed, one plausible
    directory at a time.
    """
    report = world(workspace=("lit0104-build-evidence",))
    found = of(report, "EXEMPT_ENTRY", "lit0104-build-evidence")
    assert len(found) == 1
    assert found[0]["status"] == "OBSERVED"
    assert found[0]["exemption_class"] == "PROTECTED"
    assert found[0]["disposition"] == "FROZEN"
    assert "§3" in found[0]["authority"]


# ---------------------------------------------------------------------------
# the `~/.serveros` declaration conflict
# ---------------------------------------------------------------------------


def test_a_transcription_that_disagrees_with_its_authority_is_high_drift(world):
    """The defect this check was written after finding.

    `paths.py` carried `("identity.yaml", "targets.yaml", "profile.yaml",
    "config.yaml")` while `fleet/node-root-contract.yaml`, `fleet/node.py`,
    `fleet/README.md` and the disk all said `node.yaml`. `admit()` therefore
    REFUSED a write to `~/.serveros/node.yaml` — a canonical path, the file that
    answers "which device is this host" — and no test noticed, because the one
    test on that branch asserted `targets.yaml`: the single name both versions
    of the list happened to agree on.

    Asserting the intersection of two disagreeing declarations is how the
    disagreement survives, so this test asserts the whole set.
    """
    report = world(node_files={"node.yaml": {}, "targets.yaml": {}, "identity.yaml": {}})
    found = of(report, "DECLARATION_CONFLICT")
    assert len(found) == 1
    assert found[0]["severity"] == "HIGH"
    assert "identity.yaml" in found[0]["observed"]
    assert "controller.yaml" in found[0]["observed"]


def test_a_matching_transcription_is_not_reported(world):
    report = world(node_files={"node.yaml": {}, "targets.yaml": {}, "controller.yaml": {}})
    assert of(report, "DECLARATION_CONFLICT") == []


def test_an_unreadable_authority_is_not_an_authority_that_forbids_nothing(world, tmp_path):
    """No `node-root-contract.yaml` at all. The check must say it could not
    answer, not that the transcription is fine.

    This is the same conflation `_capability_view` refuses in `daemon.py`: an
    authority that could not be read and an authority that answered with an
    empty set are different facts. A checker that renders the first as the
    second reports a clean node whenever the contract file happens to be
    missing — and "the file is missing" is exactly the condition under which
    someone is most likely to believe the clean result.
    """
    paths = paths_mod.Paths({"home": "/tmp", "data_root": "/tmp",
                             "workspace_root": "/tmp"})

    # A repo with no contract file: not a match, and not silence either.
    empty_repo = tmp_path / "repo-without-contract"
    empty_repo.mkdir()
    found = conformance.declaration_conflicts(paths, repo_root=empty_repo)
    assert [f["id"] for f in found] == ["DECLARATION_AUTHORITY_UNREADABLE"]
    assert found[0]["severity"] == "MEDIUM"
    assert found[0]["status"] == "OPEN"
    # It must not read as a conflict either: there is nothing to conflict with.
    assert "DECLARATION_CONFLICT" not in [f["id"] for f in found]

    # A contract that exists but does not parse is the same fact, not a
    # different one: `files` was never read, so nothing was compared.
    broken_repo = tmp_path / "repo-with-broken-contract"
    (broken_repo / "fleet").mkdir(parents=True)
    (broken_repo / conformance.NODE_ROOT_CONTRACT).write_text("{ not: valid: yaml: [\n")
    broken = conformance.declaration_conflicts(paths, repo_root=broken_repo)
    assert [f["id"] for f in broken] == ["DECLARATION_AUTHORITY_UNREADABLE"]

    # And a contract that parses but declares no `files` mapping is likewise
    # unread rather than empty — an authority saying nothing and an authority
    # saying "nothing is allowed" must not collapse into one answer.
    empty_files_repo = tmp_path / "repo-with-empty-files"
    (empty_files_repo / "fleet").mkdir(parents=True)
    (empty_files_repo / conformance.NODE_ROOT_CONTRACT).write_text(
        yaml.safe_dump({"schema": "serveros/node-root.v1"}))
    no_files = conformance.declaration_conflicts(paths, repo_root=empty_files_repo)
    assert [f["id"] for f in no_files] == ["DECLARATION_AUTHORITY_UNREADABLE"]


# ---------------------------------------------------------------------------
# the properties the check has to hold to be usable at all
# ---------------------------------------------------------------------------


def test_the_check_creates_nothing(tmp_path):
    """Read-only, including the absence path.

    A checker that "repairs" a missing §35 area by creating it converts a
    finding into a fact about nothing — an empty `projections/` is
    indistinguishable from a populated one to every later check — and §42 puts
    migration behind an owner check, not in front of it.

    The tree is built here rather than by the fixture, because the property is
    about what `collect` does to a tree that already exists. A snapshot taken
    around the fixture would measure the fixture.

    The starting state is deliberately non-conforming in both directions: an
    undeclared `scratch/` on disk, and six declared areas that are absent. Both
    are exactly the conditions under which a self-repairing checker would write
    something, so both are conditions under which this one must not.
    """
    data_root = tmp_path / "data"
    (data_root / "state/serveros/abilities").mkdir(parents=True)
    (data_root / "workspace").mkdir()
    (data_root / "artifacts").mkdir()
    (data_root / "scratch").mkdir()
    home = tmp_path / "home"
    (home / ".serveros").mkdir(parents=True)
    repo = tmp_path / "repo"
    repo.mkdir()

    paths = paths_mod.Paths({"home": str(home), "data_root": str(data_root),
                             "workspace_root": str(data_root / "workspace")})
    snapshot = lambda: sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    before = snapshot()
    report = conformance.collect(paths, repo_root=repo, home=str(home))
    assert snapshot() == before, "the check wrote to the tree it was inspecting"

    # And the report is not silently empty — if it were, this test would pass
    # against a check that had stopped looking, which is the whole failure mode.
    assert of(report, "UNDECLARED_ENTRY", "scratch"), "the check found nothing to write about"
    assert of(report, "DECLARED_ENTRY_ABSENT", "projections")


def test_the_vocabulary_comes_from_the_contract_not_a_copy(world):
    """A second copy of a vocabulary is a second thing that can disagree.

    The check reads `paths.vocabulary(area)`. If it carried its own tuples it
    would report on those, and the report would keep agreeing with itself while
    the contract moved — which is the failure being fixed, restated one layer
    up.
    """
    surfaces = {s["surface"]: s for s in conformance.surfaces(
        paths_mod.Paths({"home": "/tmp", "data_root": "/tmp", "workspace_root": "/tmp"}),
        "/tmp")}
    assert surfaces["state/serveros"]["vocabulary"] == paths_mod.STATE_SERVEROS_TOP_LEVEL
    assert surfaces["~/.serveros"]["vocabulary"] == paths_mod.HOME_DOT_SERVEROS_ALLOWED
    assert surfaces["state/serveros/runtime"]["area"] == "state"


def test_every_vocabulary_is_addressable_by_name():
    """`VOCABULARIES` is what the check indexes. A tuple reachable by no name is
    a vocabulary nothing can be asked to conform to."""
    for area in ("data", "workspace", "state", "runtime", "artifacts", "home"):
        assert paths_mod.Paths().vocabulary(area)
    with pytest.raises(paths_mod.PathRefused):
        paths_mod.Paths().vocabulary("not-an-area")


# ---------------------------------------------------------------------------
# the write list and the read table are two sets (§38)
# ---------------------------------------------------------------------------


def test_no_exemption_shadows_a_declared_entry():
    """An exemption for an already-declared name can never be reached.

    `compare()` consults the table only for `present - vocabulary`, so a key
    that is also a vocabulary member is dead weight that reads as live. Same
    shape as a declaration with no producer: the entry looks like it is doing
    something and the branch that would use it cannot be taken. The two sets are
    allowed to differ — that is the point of having two — but they must not
    overlap, or the difference stops meaning anything.
    """
    for area in ("data", "workspace", "state", "home"):
        vocabulary = set(paths_mod.Paths().vocabulary(area))
        shadows = sorted(set(conformance.EXCEPTIONS.get(area, {})) & vocabulary)
        assert not shadows, f"{area}: exemption for a declared entry {shadows}"


def test_the_home_readme_is_classified_and_still_refused_a_write(world, tmp_path):
    """§38: the two halves of the `~/.serveros/README.md` ruling, held together.

    The file is a human-placed entry document, so the READ side classifies it
    rather than printing it as `UNDECLARED_ENTRY / MEDIUM / OPEN` on every run
    forever — an alarm that cannot clear is how a report teaches its readers to
    skim. The WRITE side is untouched: the allow-list is a different tuple,
    `admit()` reads that tuple directly, and it still refuses. Asserting only
    one half is how this drifts back — classifying it would be wrong if it
    blessed the write, and refusing it was never a reason to leave it
    unclassified.
    """
    report = world(home=("README.md",))
    found = of(report, "EXEMPT_ENTRY", "README.md")
    assert len(found) == 1
    assert found[0]["exemption_class"] == "ENTRY_DOCUMENT"
    assert found[0]["disposition"] == "KEEP"
    assert found[0]["status"] == "OBSERVED"
    assert of(report, "UNDECLARED_ENTRY") == []

    home = tmp_path / "home"
    paths = paths_mod.Paths({"home": str(home), "data_root": str(tmp_path / "data"),
                             "workspace_root": str(tmp_path / "data/workspace")})
    with pytest.raises(paths_mod.PathRefused):
        paths.admit(str(home / ".serveros" / "README.md"))
    # Positive control: all three of the list, not the intersection. A test that
    # asserted one shared name is what hid the A1 defect in architecture_check.py
    # and the disagreeing allow-list here.
    for name in paths_mod.HOME_DOT_SERVEROS_ALLOWED:
        paths.admit(str(home / ".serveros" / name))
