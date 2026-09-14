#!/usr/bin/env python3
"""§16A/§16B · Filesystem and directory hygiene.

The detector, and the fact that it is only a detector.

Three of the tests below are regressions on defects this module had on the day
it was written, all three found by running it against the real roots and
reading the counts rather than by reading the code: `$DATA_ROOT/.vscode-server`
reported MISPLACED while the same fact was used correctly from the HOME side,
the two `state/serveros` names the contract has not ruled on reported
MISPLACED because the module knew better than the contract, and
RETENTION_REQUIRED was structurally unreachable because the retained artifacts
live one level below the root the report walked.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"

HOME = Path("/home/hwz")
DATA = Path("/opt/applications/data/home-hwz")


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fh = _load("serveros_runtime_fs_hygiene", "fs_hygiene.py")
paths = _load("serveros_runtime_paths", "paths.py")


# ---------------------------------------------------------------------------
# Fixtures: two roots that look like the real ones
# ---------------------------------------------------------------------------


def make_roots(tmp_path, *, home_entries=(), data_entries=(), workspace_entries=(),
               state_entries=()):
    home = tmp_path / "home"
    data = tmp_path / "data"
    for root, names in ((home, home_entries), (data, data_entries),
                        (data / "workspace", workspace_entries),
                        (data / "state/serveros", state_entries)):
        root.mkdir(parents=True, exist_ok=True)
        for name in names:
            (root / name).mkdir()
    return home, data


def entry(name, *, is_dir=True, is_file=False, is_symlink=False, resolved=None):
    return {"name": name, "path": f"/x/{name}", "is_dir": is_dir, "is_file": is_file,
            "is_symlink": is_symlink, "link_target": resolved,
            "resolved": resolved}


def classify(name, *, root_name="data", vocabulary=(), relations=None, sizes=None,
             duplicates=(), is_role_location=False, **entry_kwargs):
    return fh.classify_entry(
        entry(name, **entry_kwargs), root_name=root_name, vocabulary=tuple(vocabulary),
        relations=relations or {}, sizes=sizes or {}, oversized_bytes=1 << 30,
        duplicates=set(duplicates), is_role_location=is_role_location)


# ---------------------------------------------------------------------------
# §16A is a detector, not a cleaner
# ---------------------------------------------------------------------------


def test_the_module_contains_no_mutating_action():
    """§16B: no automatic symlink, no automatic migrate, and §22's "move no
    real files" is only true if the module could not move one anyway."""
    source = (RUNTIME / "fs_hygiene.py").read_text()
    for forbidden in ("os.rename", "os.replace", "shutil.move", "shutil.rmtree",
                      "os.unlink", "os.remove", "os.rmdir", "os.symlink",
                      "os.chmod", "os.chown", "subprocess", "os.kill", "pkill"):
        assert forbidden not in source, forbidden


def test_every_class_this_module_can_produce_is_never_actionable():
    """Asserted over the vocabulary rather than over the classes that happen to
    look dangerous, so that adding a class forces the decision here."""
    for name in fh.CLASSES:
        assert name in fh.NEVER_ACTIONABLE, name
    assert fh.NEVER_ACTIONABLE == frozenset(fh.CLASSES)
    assert len(fh.CLASSES) == 8


def test_no_entry_row_claims_to_be_actionable():
    for kwargs in ({}, {"is_role_location": True}):
        assert classify(".prev-3.1.0-srv0003.20260914a", **kwargs)["actionable"] is False


def test_the_report_says_what_it_did_not_do():
    note = fh.report(home=Path("/nonexistent-home"), data=Path("/nonexistent-data"))["note"]
    assert "Nothing was moved" in note
    assert ".botmux-clone.log" in note and ".ccbridge-bridge.log" in note


# ---------------------------------------------------------------------------
# The two names §16A says not to touch
# ---------------------------------------------------------------------------


def test_the_two_named_log_files_are_classified_and_not_special_cased_away():
    """They must appear in the report — §16A requires a classification — and
    they must not be moved or deleted, which is structural rather than a rule
    because nothing in this module can move anything."""
    home = Path("/home/hwz")
    for name in (".botmux-clone.log", ".ccbridge-bridge.log"):
        row = classify(name, root_name="home", is_dir=False, is_file=True)
        assert row["class"] == fh.UNKNOWN
        assert row["actionable"] is False
        assert row["size_note"] == "size_unmeasured"
    assert (home / ".botmux-clone.log").exists(), "this test must not have moved it"


def test_a_file_at_a_roots_top_level_is_unknown_and_not_misplaced():
    """The vocabularies are lists of ROOTS. A file at this level is outside
    them either way, and calling it misplaced would attach a disposition to a
    judgement about whether it is an entry document or drift — which is the
    judgement `paths.py` records itself making wrongly once already."""
    row = classify("README.md", root_name="state", vocabulary=("runtime",),
                   is_dir=False, is_file=True)
    assert row["class"] == fh.UNKNOWN
    assert "lists of roots" in row["why"]


def test_a_directory_outside_a_declared_vocabulary_is_misplaced_and_says_which_kind():
    """Enforced-on-write and declared-but-unenforced are different findings:
    the first means the entry predates the rule, the second means nothing
    refused the write."""
    enforced = classify("drifted", root_name="workspace",
                        vocabulary=("src", "worktrees", "ops"))
    unenforced = classify("drifted", root_name="state", vocabulary=("runtime",))
    assert enforced["class"] == unenforced["class"] == fh.MISPLACED
    assert "enforced on write" in enforced["why"]
    assert "NOT enforced" in unenforced["why"]


def test_a_directory_is_not_misplaced_against_a_vocabulary_that_does_not_exist():
    """HOME has no declared top-level vocabulary on this node, so this module
    cannot call anything in it misplaced — and it reports that gap rather than
    inventing a list to check against."""
    row = classify("Documents", root_name="home", vocabulary=())
    assert row["class"] == fh.UNKNOWN
    assert "has none" in row["why"]


# ---------------------------------------------------------------------------
# The names the contract has not ruled on
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(fh.AWAITING_A_RULING))
def test_a_name_the_contract_has_not_ruled_on_is_unknown_not_misplaced(name):
    """`paths.py` states these are "unresolved rather than designed". Promoting
    that to MISPLACED would be this module overruling a decision the contract
    says it has not made."""
    row = classify(name, root_name="state", vocabulary=("runtime", "boards"))
    assert row["class"] == fh.UNKNOWN
    assert "paths.py" in row["why"]


# ---------------------------------------------------------------------------
# Retained artifacts: the opposite of LEGACY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [".prev-3.1.0-srv0003.20260914a",
                                  ".prev-3.1.0-srv0003.20260910f",
                                  ".build-linux-work-v0100"])
def test_a_retained_release_artifact_is_retention_required(name):
    row = classify(name, root_name="role:runtime.serveros-distribution",
                   is_role_location=True)
    assert row["class"] == fh.RETENTION_REQUIRED
    assert row["actionable"] is False
    assert "rollback target" in row["why"]
    assert fh.LEGACY not in [m["class"] for m in row["matches"]]


def test_the_same_retained_name_outside_a_role_location_is_not_retention_required():
    """The class is about a retained artifact beside a `current` pointer, not
    about the spelling. A `.prev-*` name somewhere else has nothing to be
    retained FOR."""
    row = classify(".prev-3.1.0-srv0003.20260914a", root_name="data",
                   vocabulary=("artifacts",))
    assert row["class"] == fh.UNKNOWN


def test_legacy_is_only_ever_the_declared_forbidden_suffixes():
    """One meaning each: LEGACY means "migrate and retire the path", and it
    comes from `paths.FORBIDDEN_SUFFIXES` rather than from this module's
    opinion. Read through `paths` so the module cannot drift from the contract
    that owns the list."""
    for name in paths.FORBIDDEN_SUFFIXES:
        assert classify(name, root_name="data")["class"] == fh.LEGACY
    # A name outside the contract's list is never LEGACY, whatever it sounds
    # like — which is what keeps RETENTION_REQUIRED from collapsing into it.
    for name in ("old", "new", ".prev-1.0.0", "archive"):
        assert fh.LEGACY not in [
            m["class"] for m in classify(name, root_name="data")["matches"]], name


def test_a_retained_artifact_is_not_also_reported_as_legacy():
    """The two classes must never be true of one entry: a reader who saw both
    would have to decide which disposition applies, and the answer differs."""
    row = classify(".prev-9.9.9", root_name="role:x", is_role_location=True)
    assert row["class"] == fh.RETENTION_REQUIRED
    assert fh.LEGACY not in row["also_matches"]


# ---------------------------------------------------------------------------
# The symlink relation between the two roots
# ---------------------------------------------------------------------------


def test_a_home_symlink_explains_an_undeclared_entry_in_the_other_root(tmp_path):
    """The live defect this was written from: `$DATA_ROOT/.vscode-server` is
    6.4 GB at DATA_ROOT's top level and its name is not in the vocabulary, so a
    report over DATA_ROOT alone calls it misplaced every run. `~/.vscode-
    server` is a symlink to it — a deliberate relocation — and the link is the
    declaration."""
    home, data = make_roots(tmp_path, home_entries=[], data_entries=[".vscode-server"])
    (home / ".vscode-server").symlink_to(data / ".vscode-server")
    report = fh.report(home=home, data=data)
    row = next(r for r in report["entries"]
               if r["name"] == ".vscode-server" and r["root"] == "data")
    assert row["class"] == fh.ALLOWED_EXCEPTION
    assert "symlink" in row["why"]
    assert fh.MISPLACED not in row["also_matches"]
    assert report["symlinks_into_data_root"]


def test_the_home_side_of_the_same_entry_is_also_an_allowed_exception(tmp_path):
    home, data = make_roots(tmp_path, data_entries=[".vscode-server"])
    (home / ".vscode-server").symlink_to(data / ".vscode-server")
    report = fh.report(home=home, data=data)
    row = next(r for r in report["entries"]
               if r["name"] == ".vscode-server" and r["root"] == "home")
    assert row["class"] == fh.ALLOWED_EXCEPTION
    assert row["kind"] == "symlink"
    assert row["link_target"].endswith(".vscode-server")


def test_an_undeclared_data_root_entry_with_no_symlink_is_still_misplaced(tmp_path):
    """The explanation has to be earned. Without the link it is drift, and the
    classifier must not become a rubber stamp because one entry had a reason."""
    home, data = make_roots(tmp_path, data_entries=["some-other-cache"])
    report = fh.report(home=home, data=data)
    row = next(r for r in report["entries"] if r["name"] == "some-other-cache")
    assert row["class"] == fh.MISPLACED


# ---------------------------------------------------------------------------
# §16B's three, and only three
# ---------------------------------------------------------------------------


def test_the_three_home_exceptions_are_the_ones_the_brief_names():
    """Named exactly, because a bare allow-list is what gets widened by whoever
    hits it next. Each carries WHY."""
    assert set(fh.HOME_ALLOWED_EXCEPTIONS) == {".claude", ".codex", ".config"}
    for name, why in fh.HOME_ALLOWED_EXCEPTIONS.items():
        assert len(why) > 40, name


@pytest.mark.parametrize("name", [".claude", ".codex", ".config"])
def test_each_permitted_home_root_is_an_allowed_exception(name):
    row = classify(name, root_name="home")
    assert row["class"] == fh.ALLOWED_EXCEPTION
    assert row["why"] == fh.HOME_ALLOWED_EXCEPTIONS[name]


def test_a_dotfile_that_is_not_one_of_the_three_is_not_an_exception():
    """`~/.cache`, `~/.npm`, `~/.local` are real and are NOT on the list. If
    everything under HOME with a dot in front were an exception the list would
    mean nothing."""
    for name in (".cache", ".npm", ".local", ".docker", ".bashrc"):
        assert name not in fh.HOME_ALLOWED_EXCEPTIONS


def test_the_coverage_says_which_roots_have_a_vocabulary_and_which_do_not(tmp_path):
    home, data = make_roots(tmp_path, home_entries=[".bashrc"], data_entries=["artifacts"],
                            workspace_entries=["src"], state_entries=["runtime"])
    coverage = fh.report(home=home, data=data)["coverage"]
    assert coverage["home"]["declared_top_level"] == []
    assert "no declared top-level vocabulary" in coverage["home"]["why"]
    assert coverage["data"]["declared_top_level"]
    assert coverage["data"]["vocabulary_enforced_on_write"] is True
    assert coverage["state"]["vocabulary_enforced_on_write"] is False


def test_a_root_that_is_not_there_is_reported_rather_than_omitted(tmp_path):
    """A report over one root that reads as a report over two is how a check
    stops covering half of what it claims to."""
    home, _ = make_roots(tmp_path)
    coverage = fh.report(home=home, data=tmp_path / "absent-data")["coverage"]
    assert coverage["data"]["state"] == "absent"
    assert coverage["data"]["why"]


# ---------------------------------------------------------------------------
# Duplicates, sizes, and what is left open
# ---------------------------------------------------------------------------


def test_a_declared_root_name_under_the_wrong_root_is_a_duplicate(tmp_path):
    """The `workspace/artifacts` shape. Reported, never moved: which copy is
    canonical needs the consumers of both."""
    home, data = make_roots(tmp_path, data_entries=["artifacts"],
                            workspace_entries=["src", "artifacts"])
    report = fh.report(home=home, data=data)
    assert "artifacts" in report["duplicate_semantic_roots"]
    row = next(r for r in report["entries"]
               if r["root"] == "workspace" and r["name"] == "artifacts")
    assert row["class"] == fh.DUPLICATE_SEMANTIC_ROOT
    assert row["actionable"] is False
    assert "moves nothing" in row["why"]


def test_a_declared_root_name_under_its_own_root_is_not_a_duplicate(tmp_path):
    home, data = make_roots(tmp_path, data_entries=["artifacts", "toolchains"])
    report = fh.report(home=home, data=data)
    assert report["duplicate_semantic_roots"] == {}
    for row in report["entries"]:
        assert row["class"] == fh.CANONICAL


def test_an_unmeasured_size_leaves_the_question_open_rather_than_answering_zero():
    """A `0` in a size field reads as "tiny". It is not a measurement this
    module took, and `inventory.py` makes the same distinction between a
    measured zero and an unmeasured one."""
    row = classify("artifacts", root_name="data", vocabulary=("artifacts",))
    assert row["size_bytes"] is None
    assert row["size_note"] == "size_unmeasured"
    assert fh.OVERSIZED not in row["also_matches"]


def test_a_measured_oversized_directory_says_so_without_becoming_a_verdict():
    row = classify("artifacts", root_name="data", vocabulary=("artifacts",),
                   sizes={"/x/artifacts": 9 << 30})
    assert fh.OVERSIZED in row["also_matches"]
    assert row["size_bytes"] == 9 << 30
    assert row["actionable"] is False
    why = next(m["why"] for m in row["matches"] if m["class"] == fh.OVERSIZED)
    assert "not a verdict" in why


def test_a_correctly_placed_directory_stays_canonical_however_large_it_is():
    """Placement and size are different questions, and the primary word answers
    the placement one. A canonical root reported as OVERSIZED would make the
    report's "where does this belong" column answer "it is big", and the size
    is not lost — it is in `also_matches` and in `size_bytes`."""
    row = classify("artifacts", root_name="data", vocabulary=("artifacts",),
                   sizes={"/x/artifacts": 900 << 30})
    assert row["class"] == fh.CANONICAL
    assert fh.OVERSIZED in row["also_matches"]


def test_a_directory_under_the_threshold_is_not_oversized():
    row = classify("artifacts", root_name="data", vocabulary=("artifacts",),
                   sizes={"/x/artifacts": 1024})
    assert fh.OVERSIZED not in row["also_matches"]


def test_the_threshold_is_reported_beside_the_verdict_it_produced(tmp_path):
    home, data = make_roots(tmp_path, data_entries=["artifacts"])
    thresholds = fh.report(home=home, data=data, oversized_bytes=4096)["thresholds"]
    assert thresholds["oversized_bytes"] == 4096
    assert "reporting threshold" in thresholds["oversized_bytes_source"]


@pytest.mark.parametrize("class_name", list(fh.CLASSES))
def test_every_class_is_reachable(class_name):
    """Eight words, and a fact set that produces each. A vocabulary with a word
    no input reaches is the "declared but nothing emits it" shape this round
    keeps finding — RETENTION_REQUIRED was exactly that until the role-location
    pass was added."""
    REACHES = {
        fh.CANONICAL: dict(name="artifacts", vocabulary=("artifacts",)),
        fh.ALLOWED_EXCEPTION: dict(name=".claude", root_name="home"),
        fh.LEGACY: dict(name="legacy", root_name="data"),
        fh.MISPLACED: dict(name="drifted", root_name="workspace",
                           vocabulary=("src",)),
        # OVERSIZED is a size, and its own class only when placement
        # established nothing — see the ordering test below.
        fh.OVERSIZED: dict(name="huge-undeclared", root_name="home", vocabulary=(),
                           sizes={"/x/huge-undeclared": 9 << 30}),
        fh.DUPLICATE_SEMANTIC_ROOT: dict(name="artifacts", duplicates=["artifacts"]),
        fh.RETENTION_REQUIRED: dict(name=".prev-1.0.0", root_name="role:x",
                                    is_role_location=True),
        fh.UNKNOWN: dict(name="whatever", root_name="home", vocabulary=()),
    }
    kwargs = dict(REACHES[class_name])
    name = kwargs.pop("name")
    assert classify(name, **kwargs)["class"] == class_name


# ---------------------------------------------------------------------------
# §16B: the placement policy for a new write
# ---------------------------------------------------------------------------


def fixture_roots(tmp_path):
    """Every declared root redirected under `tmp_path`.

    The keys are `_expand`'s inputs — `home`, `data_root`, `workspace_root`,
    `artifacts_root`, `toolchains_root` — and NOT the root names `ROOTS` is
    keyed by. `Paths({"data": ...})` looks like it redirects DATA_ROOT and does
    not: `_expand` reads `roots.get("data_root")`, ignores the unknown key, and
    falls back to the real `/opt/applications/data/home-hwz`, so a fixture path
    is under no root and every write reads as refused. Passing the whole
    contract means a test asserts about the rule rather than about this host's
    layout.
    """
    base = tmp_path / "roots"
    return paths.Paths({
        "home": str(base / "home"),
        "data_root": str(base / "data"),
        "workspace_root": str(base / "data/workspace"),
        "artifacts_root": str(base / "data/artifacts"),
        "toolchains_root": str(base / "data/toolchains"),
    }), base


def test_a_canonical_write_is_admitted_and_nothing_is_done_to_it(tmp_path):
    registry, base = fixture_roots(tmp_path)
    (base / "data/artifacts").mkdir(parents=True)
    (base / "home").mkdir(parents=True)
    # The real §34 shape, not a shortened one: `CANONICAL_NAMESPACE` and
    # `CANONICAL_WORK_ITEM_RUN` are different answers and only the full path
    # gets the second, which is the difference between "somewhere under
    # artifacts" and "where an Item's evidence goes".
    verdict = fh.placement(base / "data/artifacts/work-items/litellm/LIT-0104/RUN-0019",
                           paths_registry=registry)
    assert verdict["admitted"] is True
    assert verdict["automatic_action"] is None
    assert verdict["refused_because"] is None
    assert verdict["canonical"] == "CANONICAL_WORK_ITEM_RUN"
    assert fh.placement(base / "data/artifacts/work-items/litellm",
                        paths_registry=registry)["canonical"] == "CANONICAL_NAMESPACE"


@pytest.mark.parametrize("substitute", ["artifacts", "toolchains"])
def test_a_substitute_root_under_workspace_is_refused_and_not_linked(substitute, tmp_path):
    """§16B's specific prohibition. The refusal must NOT be resolved by
    symlinking `workspace/artifacts` to `artifacts/` — that is the move the
    policy forbids, and it would leave the wrong place working, which is how a
    wrong place becomes permanent."""
    registry, base = fixture_roots(tmp_path)
    (base / "data/workspace").mkdir(parents=True)
    (base / "home").mkdir(parents=True)
    verdict = fh.placement(base / f"data/workspace/{substitute}/x", paths_registry=registry)
    assert verdict["admitted"] is False
    assert "symlink" in verdict["why"]
    assert verdict["automatic_action"] is None
    assert not (base / f"data/workspace/{substitute}").exists()


def test_a_home_exception_is_admitted_as_a_permission_to_leave_it_alone(tmp_path):
    registry, base = fixture_roots(tmp_path)
    (base / "home").mkdir(parents=True)
    for name in fh.HOME_ALLOWED_EXCEPTIONS:
        (base / "home" / name).mkdir(exist_ok=True)
        verdict = fh.placement(base / "home" / name / "settings.json",
                               paths_registry=registry)
        assert verdict["admitted"] is True, (name, verdict["refused_because"])
        assert verdict["automatic_action"] is None
        assert verdict["existing_exception"] is True
        assert "LEAVE IT ALONE" in verdict["why"]
        assert (base / "home" / name).is_dir()


def test_a_new_top_level_directory_under_home_is_refused(tmp_path):
    registry, base = fixture_roots(tmp_path)
    (base / "home").mkdir(parents=True)
    verdict = fh.placement(base / "home/some-new-place/x", paths_registry=registry)
    assert verdict["admitted"] is False
    assert not (base / "home/some-new-place").exists()


def test_no_placement_verdict_ever_offers_an_automatic_action(tmp_path):
    """Present and permanently empty, on every path. A caller looking for
    permission to act finds a field saying there is nothing to act on, which is
    harder to misread than a missing key."""
    registry, base = fixture_roots(tmp_path)
    (base / "home").mkdir(parents=True)
    (base / "data/artifacts").mkdir(parents=True)
    for candidate in (base / "data/artifacts/x/y", base / "home/newthing/x",
                      tmp_path / "elsewhere/x"):
        assert fh.placement(candidate, paths_registry=registry)["automatic_action"] is None


# ---------------------------------------------------------------------------
# The live node
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not DATA.is_dir(), reason="this node has no DATA_ROOT")
def test_the_live_roots_are_classified_and_the_counts_add_up():
    report = fh.report()
    assert sum(report["counts"].values()) == len(report["entries"])
    for name in ("home", "data", "workspace", "state"):
        assert report["coverage"][name]["state"] in ("read", "absent")
    assert report["coverage"]["data"]["state"] == "read"


@pytest.mark.skipif(not DATA.is_dir(), reason="this node has no DATA_ROOT")
def test_every_allowed_exception_in_the_data_root_is_explained_by_a_symlink():
    """The regression gate for the live defect: `$DATA_ROOT/.vscode-server` was
    reported MISPLACED while `~/.vscode-server` was reported ALLOWED_EXCEPTION,
    which is one entry seen from two roots. An ALLOWED_EXCEPTION in the data
    root that no relation explains would mean the two views have diverged
    again."""
    report = fh.report()
    relations = report["symlinks_into_data_root"]
    for row in report["entries"]:
        if row["root"] == "data" and row["class"] == fh.ALLOWED_EXCEPTION:
            assert any(row["path"] == target for target in relations), row["path"]


@pytest.mark.skipif(not DATA.is_dir(), reason="this node has no DATA_ROOT")
def test_every_misplaced_row_is_a_directory_under_an_enforced_vocabulary():
    """An invariant rather than a count, so the gate does not have to be
    rewritten when somebody moves the thing it currently reports."""
    report = fh.report()
    for row in report["entries"]:
        if row["class"] != fh.MISPLACED:
            continue
        assert row["kind"] == "directory", row["path"]
        assert report["coverage"][row["root"]]["declared_top_level"], row["root"]
        assert row["actionable"] is False


@pytest.mark.skipif(not DATA.is_dir(), reason="this node has no DATA_ROOT")
def test_the_live_retained_artifacts_are_reachable_and_untouched():
    """§16A names `.prev-*` by example. On this node four of them exist, plus
    two `.build-*`, and they must be reported as RETENTION_REQUIRED — not as
    LEGACY, and not deleted."""
    report = fh.report()
    retained = [row for row in report["entries"] if row["class"] == fh.RETENTION_REQUIRED]
    assert report["role_locations"]["entries"] > 0
    for row in retained:
        assert row["name"].startswith((".prev-", ".build-"))
        assert row["actionable"] is False
    distribution = (DATA / "runtime-assets/hwz-server-os")
    if distribution.is_dir():
        for name in [p.name for p in distribution.iterdir()
                     if fh.RETAINED_PATTERN.match(p.name)]:
            assert (distribution / name).exists(), f"{name} was moved or removed"
