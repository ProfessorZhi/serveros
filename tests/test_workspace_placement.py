#!/usr/bin/env python3
"""Tests for workspace-management (§I).

Two halves, and the second is the one worth having.

The first half checks the six placement classes: that `locate` has no default,
that `inspect` distinguishes a worktree from a record, that `reconcile` reports
a declared area as absent rather than omitting it.

The second half checks the REFUSAL. `paths.py` falls back to compiled-in
literals when nothing configures the node, and those literals are another
machine's paths. Every placement verb here would otherwise answer from them —
`locate` by naming a directory to write to, `overview` by naming one that
simply does not exist — and both answers look exactly like correct ones. The
tests below assert the refusal on an unbound process and the answer on a bound
one, because a guard that refuses in both cases passes half of them and is not
a guard.

Hermetic: every path is built under `tmp_path`, and the git checkouts are
created with `git init` inside it. Nothing here reads or writes this node's
real workspace.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ROOT_ENV = ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT", "TOOLCHAINS_ROOT")


def _load(name: str, relative: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


workspace = _load("serveros_runtime_workspace", "runtime/workspace.py")
# Loaded directly rather than through `workspace._load_paths()`, which reads
# the environment and returns an instance. These tests need to CONSTRUCT a
# `Paths` with explicit roots, and to construct one with none at all — the
# unbound case cannot be reached through a helper that resolves the node.
Paths = _load("serveros_runtime_paths", "runtime/paths.py").Paths


@pytest.fixture
def unbound(monkeypatch):
    """A process with nothing configured and no authored binding to fall back on.

    Both halves are needed and they are different things. Deleting the
    environment variables removes the two tiers above the Profile; passing
    `fleet_dir=None` removes the Profile tier itself, which is what makes this
    the fail-closed case rather than a description of a non-login shell. A
    non-login shell on an enrolled node is now a BOUND process — that is the
    whole point of the path-authority fix — so a fixture that only cleared the
    environment would be asserting the refusal against a node that no longer
    refuses.
    """
    for name in ROOT_ENV:
        monkeypatch.delenv(name, raising=False)
    return Paths(fleet_dir=None)


@pytest.fixture
def node(tmp_path):
    """A node with a real declared shape, so the verbs have something to read."""
    data = tmp_path / "data"
    for area in ("src", "worktrees", "test-envs", "records", "knowledge", "ops"):
        (data / "workspace" / area).mkdir(parents=True)
    (data / "artifacts" / "work-items").mkdir(parents=True)
    (data / "artifacts" / "skills").mkdir(parents=True)
    (data / "tmp").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    return Paths({"home": str(home), "data_root": str(data)})


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _codes(payload: dict) -> set[str]:
    return {f["code"] for f in payload["findings"]}


# ---------------------------------------------------------------------------
# The refusal — the half that has to fail closed
# ---------------------------------------------------------------------------


def test_an_unconfigured_process_reports_itself_unbound(unbound):
    assert unbound.bound is False
    assert unbound.unconfigured, "every data-derived root went unanswered"
    # `home` still came from the environment, so it is NOT among the
    # unconfigured — the distinction is per root, and a blanket "unconfigured"
    # flag would report a root that was actually resolved as unresolved.
    assert "home" not in unbound.unconfigured


@pytest.mark.parametrize("verb,call", [
    ("overview", lambda m, p: m.overview(p)),
    ("reconcile", lambda m, p: m.reconcile(p)),
    ("drift", lambda m, p: m.drift(p)),
    ("locate", lambda m, p: m.locate(p, "record")),
    ("inspect", lambda m, p: m.classify(p, "/anything")),
])
def test_every_placement_verb_refuses_an_unbound_process(unbound, verb, call):
    with pytest.raises(workspace.WorkspaceError) as raised:
        call(workspace, unbound)
    message = str(raised.value)
    # The message has to name the verb — a caller with six verbs in a script
    # needs to know which one refused — and the remedy, or the refusal is a
    # wall rather than an instruction.
    assert verb in message
    assert "DATA_ROOT" in message


def test_policy_still_answers_but_withholds_the_paths(unbound):
    """The one exception, and it has to be visible rather than assumed."""
    payload = workspace.policy(unbound)
    assert payload["bound"] is False
    assert payload["paths_resolved"] is False
    assert payload["unresolved_because"]
    assert all(entry["paths"] is None for entry in payload["classes"])
    # The rules themselves are the reason this verb stays readable.
    assert payload["rules"]
    assert {entry["class"] for entry in payload["classes"]} == set(
        workspace.PLACEMENT_CLASSES)


def test_the_same_verbs_answer_once_the_node_is_configured(node):
    assert node.bound is True
    assert node.unconfigured == ()
    payload = workspace.locate(node, "record")
    assert payload["path"].startswith(node.roots["workspace"])
    assert workspace.overview(node)["areas"]


# ---------------------------------------------------------------------------
# The six classes
# ---------------------------------------------------------------------------


def test_locate_has_no_default_class(node):
    with pytest.raises(workspace.WorkspaceError) as raised:
        workspace.locate(node, "notes")
    assert "no default class" in str(raised.value)


def test_locate_names_the_class_it_was_asked_for(node):
    payload = workspace.locate(node, "worktree", "feature-x")
    assert payload["class"] == "worktree"
    assert payload["path"].endswith("worktrees/feature-x")
    assert payload["exists"] is False
    # Reported before anything is created: `locate` answers where a thing
    # belongs, and creating it would make the check a producer.
    assert not Path(payload["path"]).exists()


def test_the_six_classes_are_the_ones_the_spec_names():
    assert set(workspace.PLACEMENT_CLASSES) == {
        "src", "worktree", "test-env", "record", "artifact", "scratch"}


def test_inspect_separates_the_classes_that_look_alike(node):
    cases = {
        "src": "src",
        "worktrees/thing": "worktree",
        "test-envs/thing": "test-env",
        "records/thing": "record",
        "knowledge/thing": "record",
    }
    for relative, expected in cases.items():
        target = str(Path(node.roots["workspace"]) / relative)
        assert workspace.classify(node, target)["class"] == expected, relative
    artifacts = str(Path(node.roots["artifacts"]) / "work-items" / "SRV-1")
    assert workspace.classify(node, artifacts)["class"] == "artifact"


def test_a_record_is_durable_and_an_artifact_is_not(node):
    """The distinction the two classes exist to draw."""
    assert workspace.PLACEMENT_CLASSES["record"]["durable"] is True
    assert workspace.PLACEMENT_CLASSES["artifact"]["durable"] is False
    # Neither is editable in place, and neither is rebuildable — an artifact
    # cannot be recomputed and must not be edited, which is why the single
    # "temporary" flag the naive version used cannot express it.
    assert workspace.PLACEMENT_CLASSES["artifact"]["rebuildable"] is False
    assert workspace.PLACEMENT_CLASSES["artifact"]["mutable"] is False


def test_the_most_specific_claim_wins_when_two_cover_the_same_path(node, monkeypatch):
    """The nesting case, which is the real one on this node.

    `TMPDIR` here is the host's scratch area, so every declared root sits
    under `/data/scratch/<account>` — and a first-match-wins loop called a
    path inside `workspace/src` "scratch" purely because the scratch table was
    consulted first. Durable-and-not-editable and droppable are not
    interchangeable answers.
    """
    scratch = Path(node.roots["data"]).parent / "host-scratch"
    scratch.mkdir()
    monkeypatch.setattr(workspace, "SCRATCH_SURFACES", (
        {"path": str(scratch), "class": "scratch",
         "provenance": "test", "note": "stands in for the host scratch area"},
    ))
    nested_node = Paths({"home": node.roots["home"], "data_root": str(scratch)})
    for area in ("src", "worktrees", "test-envs", "records", "knowledge", "ops"):
        (scratch / "workspace" / area).mkdir(parents=True)
    (scratch / "artifacts" / "work-items").mkdir(parents=True)
    (scratch / "tmp").mkdir()

    inside_src = str(scratch / "workspace" / "src" / "demo")
    assert workspace.classify(nested_node, inside_src)["class"] == "src"
    # A path that only the scratch claim covers is still scratch.
    assert workspace.classify(nested_node, str(scratch / "loose"))["class"] == "scratch"


def test_an_undeclared_path_is_named_as_external_rather_than_guessed(node):
    payload = workspace.classify(node, "/etc/passwd")
    assert payload["class"] == "external"
    assert payload["durable"] is None, "unknown is not false"


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


def test_reconcile_reports_a_declared_area_as_absent_not_omitted(node):
    payload = workspace.reconcile(node)
    assert payload["ok"] is True
    assert payload["summary"]["declared_absent"] == 0
    assert set(payload["present"]) == set(payload["declared"])


def test_reconcile_reports_a_missing_workspace_root_with_its_full_shape(node):
    """The regression this file was written for.

    The early-return branch omitted `root`, and the renderer reads it
    unconditionally — so the command crashed on the exact state it exists to
    name.
    """
    broken = Paths({"home": node.roots["home"],
                    "data_root": str(Path(node.roots["data"]))})
    for area in Path(broken.roots["workspace"]).iterdir():
        if area.is_dir():
            area.rmdir()
    Path(broken.roots["workspace"]).rmdir()
    payload = workspace.reconcile(broken)
    assert payload["ok"] is False
    assert payload["root"] == broken.roots["workspace"]
    assert _codes(payload) == {"WORKSPACE_ROOT_MISSING"}
    # The shape is the same as the full return, so one renderer serves both.
    for key in ("declared", "present", "findings", "summary"):
        assert key in payload


def test_an_undeclared_top_level_entry_is_blocking(node):
    (Path(node.roots["workspace"]) / "artifacts").mkdir()
    payload = workspace.reconcile(node)
    assert payload["ok"] is False
    assert "UNDECLARED_WORKSPACE_ENTRY" in _codes(payload)


def test_reconcile_creates_nothing(node):
    declared_before = sorted(p.name for p in Path(node.roots["workspace"]).iterdir())
    workspace.reconcile(node)
    assert sorted(p.name for p in Path(node.roots["workspace"]).iterdir()) == declared_before


# ---------------------------------------------------------------------------
# Drift
# ---------------------------------------------------------------------------


def test_drift_skips_git_internals_and_finds_nested_worktrees(node):
    """Both halves of a measured defect.

    A flat scan reported `test-envs/.git` as a dirty checkout — it is inside a
    repository, so `rev-parse --git-dir` succeeds — and reported the eight
    worktree containers as "not a git checkout" while missing every worktree
    beneath them.
    """
    src = Path(node.roots["workspace"]) / "src" / "demo"
    src.mkdir()
    _git(src, "init", "-q")
    container = Path(node.roots["workspace"]) / "worktrees" / "demo"
    nested = container / "feature-x"
    nested.mkdir(parents=True)
    _git(nested, "init", "-q")
    # A directory under a worktree container that is not a checkout.
    (container / "not-a-checkout").mkdir()

    payload = workspace.drift(node)
    paths = {c["path"] for c in payload["checkouts"]}
    assert str(src) in paths
    assert str(nested) in paths, "the nested worktree was not found"
    assert not any(p.endswith(".git") for p in paths)
    assert payload["summary"]["worktrees"] == 1
    assert "NOT_A_CHECKOUT" in _codes(payload)
    # Informational, not blocking: a worktree container may hold something
    # that is not a checkout. Under src the same fact is a contract breach.
    assert payload["ok"] is True


def test_a_dirty_canonical_checkout_is_blocking_and_a_dirty_worktree_is_not(node):
    src = Path(node.roots["workspace"]) / "src" / "demo"
    src.mkdir()
    _git(src, "init", "-q")
    (src / "scratch.txt").write_text("work in progress\n")

    worktree = Path(node.roots["workspace"]) / "worktrees" / "demo" / "wt"
    worktree.mkdir(parents=True)
    _git(worktree, "init", "-q")
    (worktree / "scratch.txt").write_text("work in progress\n")

    payload = workspace.drift(node)
    assert "SRC_DIRTY" in _codes(payload)
    assert payload["ok"] is False
    # The worktree's dirt is the expected state and is reported as such: it is
    # in `checkouts`, and it is not a finding.
    assert payload["summary"]["dirty_worktrees"] == 1
    assert payload["summary"]["dirty_src"] == 1


def test_drift_reports_the_workspace_root_for_relative_rendering(node):
    assert workspace.drift(node)["workspace_root"] == node.roots["workspace"]
