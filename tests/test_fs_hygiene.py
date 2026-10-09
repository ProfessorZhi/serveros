#!/usr/bin/env python3
"""§16A/§16B · Filesystem and directory hygiene.

The detector, and the fact that it is only a detector.

Four of the tests below are regressions on defects this module had, all four
found by running it against the real roots and reading the counts rather than
by reading the code: `$DATA_ROOT/.vscode-server` reported MISPLACED while the
same fact was used correctly from the HOME side, the two `state/serveros` names
the contract has not ruled on reported MISPLACED because the module knew better
than the contract, RETENTION_REQUIRED was structurally unreachable because the
retained artifacts live one level below the root the report walked, and — the
fourth, and the largest — a host where `HOME == DATA_ROOT` had that one
directory scanned twice, once under each name, which produced twelve
`DUPLICATE_SEMANTIC_ROOT` findings about a root disagreeing with itself, six
§16B exceptions instead of three, and a whole second copy of the top level
classified against no vocabulary at all.

The four share a cause worth naming, because the fifth will look like
something else: each was found by the report disagreeing with the filesystem,
and each was fixed in the model rather than in the output. The alternative —
filtering the duplicate rows, or widening a list until the count fell — leaves
the model wrong and the next question wrong with it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"

# This node's roots, resolved below from the contract rather than written down.


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
conformance = _load("serveros_runtime_conformance", "conformance.py")

# This node's roots, RESOLVED FROM THE CONTRACT rather than written down.
#
# These two lines used to be `/home/hwz` and `/opt/applications/data/home-hwz`
# — the other node's — and every live test below is gated on `DATA.is_dir()`.
# On this node that path does not exist, so five live gates reported SKIP and
# the suite reported success. A gate that silently skips on the node it exists
# to guard is worse than no gate, and it is the same defect the report had: a
# fact about a device written where a fact about the contract belongs.
_LIVE = paths.Paths()
HOME = Path(_LIVE.root("home"))
DATA = Path(_LIVE.root("data"))


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
             duplicates=(), is_role_location=False, roles=(), evaluating_role=None,
             **entry_kwargs):
    return fh.classify_entry(
        entry(name, **entry_kwargs), root_name=root_name, vocabulary=tuple(vocabulary),
        relations=relations or {}, sizes=sizes or {}, oversized_bytes=1 << 30,
        duplicates=set(duplicates), is_role_location=is_role_location,
        roles=tuple(roles), evaluating_role=evaluating_role)


def make_single_root(tmp_path, *, entries=()):
    """A host of the shape this one actually has: `HOME == DATA_ROOT == one
    directory`. The other fixture keeps the two apart, and both shapes need
    covering — a report that only worked for one of them is the defect here."""
    root = tmp_path / "single"
    (root / "workspace").mkdir(parents=True)
    (root / "state/serveros").mkdir(parents=True)
    for name in entries:
        (root / name).mkdir()
    return root


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
    assert len(fh.CLASSES) == 9


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
    names = (".botmux-clone.log", ".ccbridge-bridge.log")
    for name in names:
        row = classify(name, root_name="home", is_dir=False, is_file=True)
        assert row["class"] == fh.UNKNOWN
        assert row["actionable"] is False
        assert row["size_note"] == "size_unmeasured"

    # The guard below is "this test must not have moved it", and it is about the
    # node it runs on. §16A named the two files where they exist; a node that
    # never had them has nothing to have moved, and asserting the literal would
    # be asserting a fact about Beijing from Tokyo — true there, meaningless
    # here. The classification above still ran, so only the guard is skipped.
    home = Path(paths.Paths().root("home"))
    absent = [name for name in names if not (home / name).exists()]
    if absent:
        pytest.skip(f"§16A's files are not on this node (home={home}): "
                    f"{', '.join(absent)}")


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


def test_every_name_awaiting_a_ruling_is_one_conformance_already_covers():
    """The third copy of one list, and the direction the copies may differ in.

    Three tables name the `state/serveros` entries that are absent from the
    vocabulary on purpose: `paths.py`'s comment, `conformance.EXCEPTIONS["state"]`,
    and this module's `AWAITING_A_RULING`. Two of them are pinned to each other
    (`test_state_root.py`); this one was not, and this is the list the report
    actually reads.

    A subset, not an equality, and the difference is the point. `conformance`
    also carries entries that are exceptions because they PREDATE the rule:
    `README.md` and the three `serveros.db` files were already there, and no
    ruling about them is pending. Listing them here would claim one is; dropping
    them from there would turn a preexisting file into a contract gap. What must
    hold is the one direction — a name this module refuses to call misplaced is
    a name the conformance table already covers — so the two cannot disagree
    about a single entry.

    What this does NOT catch is stated rather than implied: the list that
    drifted and let `archive` and a running service's socket root go unreported
    is prose in `paths.py`, and no test reads a comment. This catches the
    repair being made in one table instead of both.
    """
    awaiting = set(fh.AWAITING_A_RULING)
    covered = set(conformance.EXCEPTIONS["state"])
    assert awaiting <= covered, (
        "ruled on here but not covered by conformance: "
        f"{sorted(awaiting - covered)}"
    )


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
    """`~/.cache`, `~/.npm`, `~/.local` are real and are NOT on the §16B list.
    If everything under HOME with a dot in front were an exception the list
    would mean nothing.

    They are accounted for elsewhere — `TOOL_OWNED_DOTDIRS` — and that being a
    DIFFERENT table with different reasons is the point: §16B's three are
    excluded from HOME cleanup consideration, while a tool-owned dotdir is a
    placement fact about a named tool. `.bashrc` is in neither, because a file
    is not covered by either list and stays UNKNOWN."""
    for name in (".cache", ".npm", ".local", ".docker", ".bashrc"):
        assert name not in fh.HOME_ALLOWED_EXCEPTIONS
    for name in (".cache", ".npm", ".local", ".docker"):
        assert name in fh.TOOL_OWNED_DOTDIRS
    assert ".bashrc" not in fh.TOOL_OWNED_DOTDIRS


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


def test_every_root_the_contract_declares_is_either_applied_or_explained():
    """The index over the contract must be complete, and completeness is the
    half a reader cannot check by looking.

    `ROOT_VOCABULARY` says which declared closed set applies to which root;
    `VOCABULARY_NOT_APPLIED` says which ones deliberately do not. Together they
    have to account for every name that is BOTH a root (`paths.ROOTS`) and a
    declared closed set (`paths.VOCABULARIES`).

    The failure this exists for is not a WRONG entry but a MISSING one.
    `ARTIFACTS_ROOT`'s vocabulary has been in `paths.py` all along and was
    absent here, and this module's docstring said the contract declared no such
    set — so the module whose whole subject is where things ARE could not answer
    for the root with the most findings in the product, and the sentence it
    answered with was false. A missing entry is invisible in a report; a name in
    `VOCABULARY_NOT_APPLIED` with a reason is not.

    The set is DERIVED from both contract tables, so adding a root to `paths.py`
    fails here until somebody says which of the two it is.
    """
    both = set(paths.ROOTS) & set(paths.VOCABULARIES)
    accounted = set(fh.ROOT_VOCABULARY) | set(fh.VOCABULARY_NOT_APPLIED)
    assert both == accounted, (
        f"declared a root and a closed set but not accounted for here: "
        f"{sorted(both - accounted)}; named here but not a declared root that "
        f"also has a declared closed set: {sorted(accounted - both)}")

    # A reason, not a placeholder: an entry that says nothing is the missing
    # entry one line later, and a map of reasons is worth only what its reasons
    # are.
    for name, reason in fh.VOCABULARY_NOT_APPLIED.items():
        assert len(reason.strip()) > 40, f"{name}'s absence is not explained"
        assert name not in fh.ROOT_VOCABULARY, (
            f"{name} is listed as both applied and not applied, so which one is "
            f"true would depend on the order the two maps are read in")

    # `artifacts` is the case the pair was separated for: a vocabulary that
    # EXISTS and is deliberately not applied. Asserted in both maps because
    # applying it changes what this report can call MISPLACED — it turns 109
    # entries at that root into the class with a move behind it — and that has
    # to be a decision somebody makes, not a consequence of a root name joining
    # a scan.
    assert "artifacts" not in fh.ROOT_VOCABULARY
    assert "artifacts" in fh.VOCABULARY_NOT_APPLIED


def test_every_declared_root_is_either_given_an_entry_document_or_explained():
    """D07 needs a reader, and completeness is the half a reader cannot check.

    §13 of the directory contract asks that every major entry say briefly what
    it is for, who owns it and where to read more. Nothing in this product
    answered for that sentence, so a root could be declared, scanned and
    reported on for as long as it existed while the first question a person
    landing in it asks went unasked.

    `ENTRY_DOCUMENT_ROOTS` names the roots that carry one and which document
    answers for each; `ENTRY_DOCUMENT_NOT_REQUIRED` names the ones that
    deliberately do not, with the reason. Together they must account for every
    name in `paths.ROOTS` — the contract's own list of entry points, not a list
    authored here — so a root added to `paths.py` fails this until somebody says
    which of the two it is.

    The failure this exists for is a MISSING entry, not a wrong one: a root that
    is in neither map reads exactly like a root nobody has thought about, and
    those are not the same finding.
    """
    both = set(paths.ROOTS)
    accounted = set(fh.ENTRY_DOCUMENT_ROOTS) | set(fh.ENTRY_DOCUMENT_NOT_REQUIRED)
    assert both == accounted, (
        f"declared a root but not accounted for here: {sorted(both - accounted)}; "
        f"named here but not a declared root: {sorted(accounted - both)}")

    for name, reason in fh.ENTRY_DOCUMENT_NOT_REQUIRED.items():
        assert len(reason.strip()) > 40, f"{name}'s absence is not explained"
        assert name not in fh.ENTRY_DOCUMENT_ROOTS, (
            f"{name} is listed as both carrying an entry document and not "
            f"needing one, so which is true would depend on the order the two "
            f"maps are read in")

    for name, document in fh.ENTRY_DOCUMENT_ROOTS.items():
        assert document and not document.startswith("."), (
            f"{name} names {document!r} as its entry document; an entry "
            f"document is a file a person opens, not a hidden state file")
        assert "/" not in document, (
            f"{name}'s entry document {document!r} is expected at that root's "
            f"top level, which is where a person lands")

    # `data` is the case the pair exists for: the root that is also HOME, whose
    # entry document is the machine policy a runtime opens there rather than a
    # second `README.md` stating the same thing one directory below it.
    assert fh.ENTRY_DOCUMENT_ROOTS["data"] == "CLAUDE.md"
    assert "home" in fh.ENTRY_DOCUMENT_NOT_REQUIRED


def test_a_root_declared_to_carry_an_entry_document_is_reported_when_it_has_none(tmp_path):
    """The reading has to be able to FAIL, and to tell three states apart.

    Hermetic: the roots are passed in, so this never reads this machine's
    directories. What is asserted is that the reading distinguishes a document
    that is there, one that is missing from a root that exists, and a root that
    does not exist at all — because "no document at this root" and "no such
    root" are different findings, and a reading that merged them would report a
    document missing from a directory that was never created.
    """
    world = tmp_path / "world"
    (world / "artifacts").mkdir(parents=True)
    (world / "toolchains").mkdir()
    (world / "toolchains" / "README.md").write_text("what this root is for\n")
    # `data`'s entry document is `CLAUDE.md`, not a second `README.md` — the
    # root that is also HOME is answered by the machine policy a runtime opens.
    (world / "CLAUDE.md").write_text("the machine entry\n")
    roots = {"data": str(world), "artifacts": str(world / "artifacts"),
             "toolchains": str(world / "toolchains"),
             "tmp": str(world / "tmp")}

    reading = fh.entry_documents(roots=roots)
    rows = reading["rows"]

    # A required root with its document: present, and not a complaint.
    assert rows["toolchains"]["present"] is True
    assert "states this root's purpose" in rows["toolchains"]["why"]

    # `data` is answered by `CLAUDE.md`, and the check looks for THAT name.
    assert rows["data"]["required"] is True
    assert rows["data"]["document"] == "CLAUDE.md"
    assert rows["data"]["present"] is True

    # A required root without it: absent, named, with the path it looked in.
    assert rows["artifacts"]["present"] is False
    assert rows["artifacts"]["document"] == "README.md"
    assert rows["artifacts"]["path"] == str(world / "artifacts")
    assert "no `README.md`" in rows["artifacts"]["why"]

    # A required root that is NOT THERE: unknown, never `False`.
    rows2 = fh.entry_documents(roots={**roots, "state": str(world / "nowhere")})["rows"]
    assert rows2["state"]["present"] is None
    assert "no such root" in rows2["state"]["why"]

    # A required root the node resolves no directory for at all: also unknown.
    assert rows["workspace"]["present"] is None
    assert "resolves no directory" in rows["workspace"]["why"]

    # A root declared not to need one is a DECISION, reported with its reason
    # rather than omitted.
    assert rows["tmp"]["required"] is False
    assert rows["tmp"]["present"] is None
    assert "scratch by definition" in rows["tmp"]["why"]

    # The complaint list names exactly the required roots whose document is
    # missing — not the ones that are merely unknown, and not `data`, whose
    # document is there under the other name.
    assert reading["missing"] == ["artifacts"]


def test_nothing_this_module_scans_is_a_root_the_contract_does_not_name(tmp_path):
    """The report's root NAMES are the contract's, not the module's.

    `report()` has its own list of which roots to scan, and a name in it that
    `paths.ROOTS` does not declare would be this module inventing a root — the
    same shape as inventing a vocabulary for HOME, one level up. The four it
    scans today are asserted as a subset of the contract's ten, which is the
    claim that survives a fifth being added deliberately and fails the moment
    one is added by typing."""
    home, data = make_roots(tmp_path)
    scanned = set(fh.report(home=home, data=data)["coverage"])
    assert scanned <= set(paths.ROOTS), (
        f"scanned but not a declared root: {sorted(scanned - set(paths.ROOTS))}")
    assert scanned, "a report that scans nothing is not a passing subset"


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
    """Nine words, and a fact set that produces each. A vocabulary with a word
    no input reaches is the "declared but nothing emits it" shape this round
    keeps finding — RETENTION_REQUIRED was exactly that until the role-location
    pass was added."""
    REACHES = {
        fh.CANONICAL: dict(name="artifacts", vocabulary=("artifacts",)),
        fh.ALLOWED_EXCEPTION: dict(name=".claude", root_name="home"),
        fh.TOOL_OWNED_DOTDIR: dict(name=".ssh", root_name="home"),
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
    falls back to whatever THIS host's DATA_ROOT is, so a fixture path is under
    no root and every write reads as refused — a failure that does not announce
    itself as a wiring mistake, because it reads as "the rule refused it", which
    is the answer the test was hoping for. Passing the whole contract means a
    test asserts about the rule rather than about this host's layout.
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
def test_every_allowed_exception_on_the_live_node_is_explained_by_something():
    """The regression gate for the live defect: `$DATA_ROOT/.vscode-server` was
    reported MISPLACED while `~/.vscode-server` was reported ALLOWED_EXCEPTION,
    which is one entry seen from two roots.

    There are exactly two declared mechanisms that may produce the class — a
    symlink relation, or §16B naming the entry — and every exception must be
    accounted for by one of them, with the row saying which. The row's `why`
    for a §16B name must be §16B's own reason verbatim: a class that appeared
    with a reason nobody wrote is the divergence this test exists to catch, and
    on a host where HOME and DATA_ROOT are one directory it is also what the
    second, vocabulary-less scan used to produce — six exceptions where three
    exist."""
    report = fh.report()
    relations = report["symlinks_into_data_root"]
    for row in report["entries"]:
        if row["class"] != fh.ALLOWED_EXCEPTION:
            continue
        by_link = any(row["path"] == target for target in relations)
        by_name = row["name"] in fh.HOME_ALLOWED_EXCEPTIONS
        assert by_link or by_name, row["path"]
        if by_name:
            assert row["why"] == fh.HOME_ALLOWED_EXCEPTIONS[row["name"]], row["name"]


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


# ---------------------------------------------------------------------------
# A · several semantic roles, one physical root
# ---------------------------------------------------------------------------


def test_two_names_for_one_directory_are_scanned_once_and_are_not_duplicates(tmp_path):
    """The defect, stated as the property that fixes it.

    `home` and `data` are two roles. On a host where they resolve to one
    directory, the report must produce ONE observation per entry, apply ONE
    vocabulary, and report no duplicate — a root cannot disagree with itself.
    Before this, the same directory was read twice: once with no vocabulary
    (every entry UNKNOWN) and once against DATA_ROOT's declared set (every
    declared name CANONICAL, everything else MISPLACED), and the twelve
    declared names seen through the vocabulary-less pass were reported as
    duplicates of the root that owns them."""
    root = make_single_root(tmp_path, entries=["artifacts", "downloads"])
    report = fh.report(home=root, data=root)

    assert report["duplicate_semantic_roots"] == {}
    for name in ("artifacts", "downloads"):
        rows = [r for r in report["entries"] if r["name"] == name]
        assert len(rows) == 1, f"{name} was observed {len(rows)} times"

    # `state` and `workspace` are declared names too: three entries, one row
    # each, from one scan of one directory — not three from two.
    assert len(report["entries"]) == 4

    for name in ("artifacts", "downloads"):
        row = next(r for r in report["entries"] if r["name"] == name)
        assert row["class"] == fh.CANONICAL
        assert row["bound_roles"] == ["home", "data"]
        assert row["root"] == "data", "the role with a vocabulary evaluates"
        assert row["alias_of"] is None, "the evaluating role is not an alias"

    groups = report["physical_roots"]
    assert [g["roles"] for g in groups] == [["home", "data"], ["workspace"], ["state"]]
    assert groups[0]["co_binding"] is True
    assert groups[0]["co_binding_declared"] is True
    assert groups[0]["evaluated_with_vocabulary_of"] == "data"


def test_the_alias_is_reported_per_name_without_double_counting(tmp_path):
    """`coverage` is keyed by name because callers ask by name, so both names
    keep a row — and the alias row must say that its count is the shared
    directory's and is not additional to the other's. A reader who added the
    two would otherwise get twice the top level of one directory, which is how
    the previous version's 197 entries became 123."""
    root = make_single_root(tmp_path, entries=["artifacts"])
    report = fh.report(home=root, data=root)

    assert report["coverage"]["home"]["alias_of"] == "data"
    assert report["coverage"]["data"]["alias_of"] is None
    assert report["coverage"]["home"]["entries"] == report["coverage"]["data"]["entries"] == 3
    assert report["coverage"]["home"]["physical_root"] == report["coverage"]["data"]["physical_root"]
    assert "NOT additional" in report["coverage"]["home"]["why"]
    assert "NOT additional" not in (report["coverage"]["data"]["why"] or "")

    # And the report says the same thing about the two names at the top.
    assert report["roots"]["home"]["bound_roles"] == report["roots"]["data"]["bound_roles"]
    assert report["roots"]["home"]["class"] == report["roots"]["data"]["class"] == "DECLARED_CLOSED"
    assert report["roots"]["home"]["evaluated_with_vocabulary_of"] == "data"

    # The counts are over entries, so they must add up to what was emitted.
    assert sum(report["counts"].values()) == len(report["entries"]) == 3


# ---------------------------------------------------------------------------
# B · two different physical roots claiming one name is still a conflict
# ---------------------------------------------------------------------------


def test_two_physical_roots_claiming_one_declared_name_is_still_a_duplicate(tmp_path):
    """The false-negative risk of the alias rule: once `home` and `data` may
    share a directory, a fix that shrugged at any coincidence would stop
    reporting the substitute-root shape. `artifacts` declared under DATA_ROOT
    and materialized under HOME is that shape and must still be reported."""
    home, data = make_roots(tmp_path, home_entries=["artifacts"],
                            data_entries=["artifacts"])
    report = fh.report(home=home, data=data)

    assert "artifacts" in report["duplicate_semantic_roots"]
    found = report["duplicate_semantic_roots"]["artifacts"]
    assert len(found) == 1, "only the root that does not declare it is an impostor"
    assert found[0]["root"] == "home"
    assert found[0]["bound_roles"] == ["home"]
    assert found[0]["belongs_under"] == ["data"]

    row = next(r for r in report["entries"] if r["root"] == "home" and r["name"] == "artifacts")
    assert row["class"] == fh.DUPLICATE_SEMANTIC_ROOT
    assert row["actionable"] is False


def test_a_merge_the_contract_does_not_permit_is_reported_as_undeclared(tmp_path):
    """`home`/`data` may share a directory because the contract says so. Two
    roots that merely compare equal may not, and the report says which it is
    looking at — `WORKSPACE_ROOT=$DATA_ROOT` would apply WORKSPACE_TOP_LEVEL to
    the home directory, and a grouping rule that accepted any coincidence could
    not say so."""
    groups = paths.group_physical_roots(
        {"home": "/x", "data": "/y", "workspace": "/y", "state": "/z"},
        names=("home", "data", "workspace", "state"))
    by_roles = {tuple(g["roles"]): g for g in groups}

    assert by_roles[("home",)]["co_binding"] is False
    assert by_roles[("home",)]["co_binding_declared"] is True
    merged = by_roles[("data", "workspace")]
    assert merged["co_binding"] is True
    assert merged["co_binding_declared"] is False
    assert merged["undeclared_with"] == ["data", "workspace"]


def test_the_report_of_a_merge_nobody_declared_says_so(tmp_path):
    root = make_single_root(tmp_path, entries=["artifacts"])
    report = fh.report(home=root, data=root)
    declared = [g for g in report["physical_roots"] if g["co_binding"]][0]
    assert declared["co_binding_declared"] is True
    assert "NOT among the declared ones" not in declared["why"]
    assert declared["permitted_bindings"] == [["home", "data"]]

    merged = paths.group_physical_roots(
        {"home": "/x", "data": "/y", "workspace": "/y", "state": "/z"},
        names=("home", "data", "workspace", "state"))
    group = [g for g in merged if g["co_binding"]][0]
    assert group["co_binding_declared"] is False
    assert group["undeclared_with"] == ["data", "workspace"]


# ---------------------------------------------------------------------------
# C · a declared tool-owned dotdir is not drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(fh.TOOL_OWNED_DOTDIRS))
def test_every_declared_tool_owned_dotdir_is_declared_and_not_misplaced(name):
    """Each entry is a DECLARATION, so each is held to what a declaration needs:
    an owner, a provider, the evidence establishing it, and a reason the
    location is the tool's. A stub entry fails here, which is the point — the
    table is the ruler and a ruler with an unexplained mark on it is a ruler
    nobody can use to measure."""
    declaration = fh.TOOL_OWNED_DOTDIRS[name]
    assert name.startswith(".")
    for field in ("owner", "provider", "evidence", "why"):
        assert declaration[field].strip(), f"{name} has no {field}"
        assert declaration[field].strip().lower() not in ("todo", "tbd", "?", "n/a"), \
            f"{name}'s {field} is a placeholder"
    # No length floor on `owner`: "npm" is the correct owner and a three-letter
    # name is not a stub. The floors are where a stub would actually show — a
    # `why` that does not argue and an `evidence` that does not name anything.
    assert len(declaration["why"]) > 60, f"{name}'s why does not argue"
    assert len(declaration["evidence"]) > 20, f"{name}'s evidence names nothing"
    assert declaration["protection"] in (fh.SECRET, fh.IDENTITY, fh.UNPROTECTED)
    if declaration["protection"] != fh.UNPROTECTED:
        assert declaration["protection_basis"], name

    row = classify(name, root_name="data", vocabulary=("artifacts", "caches"))
    assert row["class"] == fh.TOOL_OWNED_DOTDIR
    assert fh.MISPLACED not in row["also_matches"]
    assert row["tool_owned"]["owner"] == declaration["owner"]
    assert "start with" not in row["why"]


def test_the_two_tables_do_not_overlap():
    """§16B's three and the tool-owned dotdirs are different claims about
    different things: the first is an exclusion from HOME cleanup
    consideration, the second a statement about which tool owns a location. A
    name in both would be two answers to one question, and the §16B answer
    would win in the match order — so the second would be a claim nothing reads
    while looking like a claim that holds."""
    assert not (set(fh.TOOL_OWNED_DOTDIRS) & set(fh.HOME_ALLOWED_EXCEPTIONS))
    assert not (set(fh.TOOL_OWNED_DOTDIRS) & set(fh.AWAITING_A_RULING))
    assert not (set(fh.TOOL_OWNED_DOTDIRS) & set(fh.UNDECLARED_ON_PURPOSE))


# ---------------------------------------------------------------------------
# D · an undeclared dotdir does not become an exception by being a dotdir
# ---------------------------------------------------------------------------


def test_an_undeclared_dotdir_is_not_excused_by_being_a_dotdir():
    """The line the whole table exists to hold. A `.foo` invented tomorrow must
    be exactly as undeclared tomorrow as `.npm` would be without its entry:
    MISPLACED under a declared vocabulary, UNKNOWN where there is none, and in
    neither case an exemption."""
    row = classify(".unknown-tool", root_name="data", vocabulary=("artifacts",))
    assert row["class"] == fh.MISPLACED
    assert row["tool_owned"] is None
    assert row["protected"] is False

    no_vocabulary = classify(".unknown-tool", root_name="home", vocabulary=())
    assert no_vocabulary["class"] == fh.UNKNOWN
    assert no_vocabulary["tool_owned"] is None

    # And the exemption is reached through the declaration, not through the dot.
    assert ".unknown-tool" not in fh.TOOL_OWNED_DOTDIRS
    for name in (".bashrc", ".profile", ".gitconfig", ".vimrc"):
        assert name not in fh.TOOL_OWNED_DOTDIRS, name
        assert classify(name, root_name="data", vocabulary=("artifacts",))["tool_owned"] is None


def test_an_undeclared_dotdir_under_a_declared_vocabulary_says_nothing_explains_it():
    row = classify(".unknown-tool", root_name="data", vocabulary=("artifacts",))
    assert "nothing explains it" in row["why"]


def test_a_declined_dotdir_is_unknown_and_says_the_declining_was_deliberate():
    """`.ability-management` is the entry that must NOT be declared to make a
    count fall. It stays UNKNOWN, it is not MISPLACED either, and the reason it
    is undeclared is in the code rather than only in a report — because the
    next reader's instinct will be to add it to the registry."""
    for name in sorted(fh.UNDECLARED_ON_PURPOSE):
        assert name not in fh.TOOL_OWNED_DOTDIRS
        row = classify(name, root_name="data", vocabulary=("artifacts",))
        assert row["class"] == fh.UNKNOWN
        assert fh.MISPLACED not in row["also_matches"]
        assert "declined" in row["why"] or "not read" in row["why"]


# ---------------------------------------------------------------------------
# E · the protection survives the placement verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [(".secrets", fh.SECRET), (".ssh", fh.IDENTITY),
                                           (".gnupg", fh.SECRET), (".docker", fh.SECRET),
                                           (".serveros", fh.IDENTITY),
                                           (".desktop-commander-device", fh.IDENTITY)])
def test_a_protected_dotdir_keeps_its_protection_through_the_placement_verdict(name, expected):
    """§E: reclassifying an entry's PLACEMENT must not lower its PROTECTION. The
    two are orthogonal, and a classifier in the business of saying where things
    belong is exactly the one that would confuse them — "this is where the tool
    keeps its state" reads like "this is safe to touch".

    The row carries the mark, the row says the verdict did not clear it, and
    the SAME entry is still refused by §16B when a write into it is proposed."""
    row = classify(name, root_name="data", vocabulary=("artifacts",))
    assert row["class"] == fh.TOOL_OWNED_DOTDIR
    assert row["protected"] is True
    assert row["tool_owned"]["protection"] == expected
    assert expected in row["why"], "the reason must carry the protection it does not clear"
    assert "nothing else" in row["why"]
    assert row["actionable"] is False, "protected and actionable cannot both be true"


def test_the_placement_gate_governs_homes_shape_and_not_a_protected_dotdirs_contents(tmp_path):
    """What §16B's gate actually protects, measured rather than assumed.

    Writing this test the obvious way failed, and the failure is worth keeping:
    a write into `~/.secrets/addendum` is ADMITTED. `admit` refuses a NEW
    top-level name under HOME — HOME's shape is not ServerOS's to extend — and
    it consults a vocabulary only for `~/.serveros`. A path inside an existing
    top-level directory is inside a shape it already has, so there is nothing
    for that rule to refuse.

    So the protection on `.secrets`, `.ssh` and `.gnupg` is NOT this gate. It
    is the protection registry, the file modes, and the fact that nothing in
    this module can act. A round that wants §16B's gate to protect them has to
    declare a top-level vocabulary for HOME, which is the open question the
    report already records — and it must not be closed by assuming the gate
    covers it, because it does not. Both directions are asserted here so the
    assumption cannot come back."""
    registry, base = fixture_roots(tmp_path)
    (base / "home").mkdir(parents=True)
    for name in (".secrets", ".ssh", ".gnupg", ".serveros", ".npm"):
        (base / "home" / name).mkdir()

    # The one HOME root with a declared vocabulary is the one the gate refuses
    # inside of.
    refused = fh.placement(base / "home/.serveros/not-declared", paths_registry=registry)
    assert refused["admitted"] is False
    assert refused["automatic_action"] is None

    # The rest are admitted, and the verdict does not pretend to be a
    # protection decision: it reports what the contract classified and nothing
    # about whether the write is safe.
    for name in (".secrets", ".ssh", ".gnupg", ".npm"):
        verdict = fh.placement(base / "home" / name / "addendum", paths_registry=registry)
        assert verdict["admitted"] is True, name
        assert verdict["automatic_action"] is None
        assert "protection" not in verdict, name
        assert not (base / "home" / name / "addendum").exists(), (
            "a verdict is not a write, and this module cannot write")


def test_an_unprotected_tool_dotdir_is_not_marked_protected():
    """The mark has to mean something: an entry whose declaration observed no
    credential or identity artifact must not carry it, or SECRET degrades into
    "somebody was cautious" and stops being worth reading."""
    for name in (".npm", ".cache", ".local", ".dotnet"):
        row = classify(name, root_name="data", vocabulary=("artifacts",))
        assert row["protected"] is False, name
        assert row["tool_owned"]["protection"] == fh.UNPROTECTED, name
        assert "nothing else" not in row["why"], name


# ---------------------------------------------------------------------------
# F · one physical path, one row, for every class
# ---------------------------------------------------------------------------


def test_an_allowed_exception_on_a_shared_physical_root_is_reported_once(tmp_path):
    """The class that was doubled: §16B's three were found by both scans, so a
    host with one directory reported six exceptions where three entries exist.
    The count is over entries, and there are three."""
    root = make_single_root(tmp_path, entries=[".claude", ".codex", ".config", ".bashrc"])
    report = fh.report(home=root, data=root)

    for name in (".claude", ".codex", ".config"):
        rows = [r for r in report["entries"] if r["name"] == name]
        assert len(rows) == 1, f"{name} was observed {len(rows)} times"
        assert rows[0]["class"] == fh.ALLOWED_EXCEPTION
    assert report["counts"][fh.ALLOWED_EXCEPTION] == 3
    assert report["counts"][fh.DUPLICATE_SEMANTIC_ROOT] == 0
    assert report["counts"][fh.TOOL_OWNED_DOTDIR] == 0


def test_no_entry_name_is_emitted_twice_from_one_physical_root(tmp_path):
    """The general form, over every class rather than the ones that happened to
    be wrong: entries are keyed by (physical root, name), and nothing may
    appear twice under one physical root. A `set(path)` over the output would
    hide a real conflict between two observations of two DIFFERENT roots; this
    asserts the property that makes deduplication unnecessary in the first
    place."""
    root = make_single_root(tmp_path,
                            entries=["artifacts", ".claude", ".ssh", "drifted-thing", ".npm"])
    report = fh.report(home=root, data=root)
    seen = {}
    for row in report["entries"]:
        key = (report["roots"][row["root"]]["physical_root"], row["name"])
        assert key not in seen, f"{key} emitted twice: {seen[key]} vs {row['class']}"
        seen[key] = row["class"]
    assert len(seen) == len(report["entries"])


def test_an_independent_root_is_still_scanned_separately(tmp_path):
    """The alias must not collapse the roots that are NOT aliases: `workspace`
    and `state` are their own directories with their own vocabularies, and
    folding them into the home group would silence their findings."""
    root = make_single_root(tmp_path, entries=["artifacts"])
    (root / "workspace" / "substitute").mkdir()
    (root / "state/serveros" / "stray").mkdir()
    report = fh.report(home=root, data=root)

    assert report["coverage"]["workspace"]["physical_root"] == str(root / "workspace")
    assert report["coverage"]["state"]["physical_root"] == str(root / "state/serveros")
    found = {r["name"]: r["class"] for r in report["entries"]}
    assert found["substitute"] == fh.MISPLACED
    assert found["stray"] == fh.MISPLACED
    duplicate_roots = {tuple(g["roles"]) for g in report["physical_roots"]}
    assert duplicate_roots == {("home", "data"), ("workspace",), ("state",)}


# ---------------------------------------------------------------------------
# G · a host is not hardcoded, and this host's shape is covered
# ---------------------------------------------------------------------------


def test_neither_the_module_nor_the_contract_carries_a_device_path_in_its_logic():
    """This release is one artifact for a fleet. A root is resolved from the
    environment and the device's Profile; a device's absolute path in the
    module is a fact about a host where a fact about the contract belongs — and
    it is the shape that made the live gates below skip on this node for as
    long as they did."""
    for filename in ("fs_hygiene.py", "paths.py"):
        source = (RUNTIME / filename).read_text()
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            for device_path in ("/data/home/vince", "/home/hwz", "/opt/applications"):
                assert device_path not in stripped, f"{filename}: {stripped}"


def test_a_host_whose_home_is_its_persistent_root_is_handled_and_says_so(tmp_path):
    """The shape this node actually has, asserted as the shape rather than as
    this node: HOME == DATA_ROOT, one directory, and the report must work from
    the paths it is handed. Nothing in the check may key on the host, and the
    single-root case must not be a special case — the grouping comes from the
    contract's declaration, so a host of this shape needs no code that knows
    about it."""
    root = make_single_root(tmp_path, entries=["artifacts", "downloads"])
    report = fh.report(home=root, data=root)
    assert len(report["physical_roots"]) == 3
    assert report["physical_roots"][0]["roles"] == ["home", "data"]
    assert report["coverage"]["home"]["declared_top_level"], (
        "the shared directory has a declared vocabulary and the home row must "
        "say which one was applied")
    assert report["coverage"]["home"]["declared_top_level"] == \
        report["coverage"]["data"]["declared_top_level"]
    assert not [r for r in report["entries"] if r["class"] == fh.MISPLACED]


def test_a_host_whose_home_is_not_its_persistent_root_still_reports_no_vocabulary(tmp_path):
    """The other shape, which must not regress: where HOME is its own
    directory, `paths.py` declares no top level for it, and the report says so
    instead of inventing a list. Both shapes are in the suite because a report
    that worked for one of them is what the defect was."""
    home, data = make_roots(tmp_path, home_entries=["Documents"],
                            data_entries=["artifacts"])
    report = fh.report(home=home, data=data)
    assert report["coverage"]["home"]["declared_top_level"] == []
    assert "no declared top-level vocabulary" in report["coverage"]["home"]["why"]
    assert report["coverage"]["home"]["alias_of"] is None
    row = next(r for r in report["entries"] if r["name"] == "Documents")
    assert row["class"] == fh.UNKNOWN
    assert report["duplicate_semantic_roots"] == {}


# ---------------------------------------------------------------------------
# D03 · the navigation shape: an entry meets its shape or has a named reason
# ---------------------------------------------------------------------------


def test_every_shape_row_is_either_checked_or_named_as_a_check_not_made():
    """D03 needs a criterion, and completeness is the half a reading cannot see.

    §3 of the directory contract is a table: a governed entry, the shape it is
    declared to have, and the depth it is declared to reach. Nothing in this
    product read that table, so any row of it could be violated for as long as the
    tree existed and no reading would differ. §8 makes it a completion criterion
    of the round in as many words — 「深度统计排除有理由的 native/tool/vendor/
    release 子树；被治理入口符合目标或有具名保留理由」.

    `NAVIGATION_SHAPE` is the table carried into code; `SHAPE_CHECKS_NOT_MADE`
    names the slots this reading deliberately does NOT judge, each with the
    measurement that decided it. Together they must account for every row, so a
    seventh row fails this until somebody says which of the two it is.

    The failure this exists for is an UNJUDGED row, not a wrong one: a row that is
    in neither map reads exactly like a row nobody has looked at, and those are
    not the same finding. It is the same shape as
    `test_every_declared_root_is_either_given_an_entry_document_or_explained`
    above, and deliberately so — this module now carries three such pairs, and the
    gate for the third is written the same way as for the first two.

    There is a second, outer equality here that the two gates above do not need
    and this one cannot do without. §3's table is MARKDOWN, so nothing derives it:
    `SECTION_3_ROWS` is a dated transcription in the runtime module, and the
    equality is between THAT and the rows carried plus the rows declined. Without
    it, a reading carrying six rows of a nine-row table would report exactly like
    one carrying all nine — which is this ledger's own recurring finding, and the
    reason the first half of this package was rewritten once already.
    """
    rows = set(fh.NAVIGATION_SHAPE)

    def names_a_row(key: str) -> bool:
        return any(key == name or key.startswith(name + "/") for name in rows)

    for key in fh.SHAPE_CHECKS_NOT_MADE:
        assert names_a_row(key), (
            f"{key!r} names no row of the shape table, so it explains a silence "
            f"about nothing")
    judged = {name for name, row in fh.NAVIGATION_SHAPE.items()
              if row["check"] is not None}
    named = {name for name in rows
             if any(key == name or key.startswith(name + "/")
                    for key in fh.SHAPE_CHECKS_NOT_MADE)}
    accounted = judged | named
    assert rows == accounted, (
        f"declared a row but neither checked nor accounted for here: "
        f"{sorted(rows - accounted)}; accounted for here but not a declared row: "
        f"{sorted(accounted - rows)}")

    # the outer equality: §3's own table, row by row
    carried = [row["section_3_row"] for row in fh.NAVIGATION_SHAPE.values()]
    assert len(carried) == len(set(carried)), (
        f"two rows carry one §3 row: "
        f"{sorted({c for c in carried if carried.count(c) > 1})}")
    everything = set(fh.SECTION_3_ROWS)
    here = set(carried) | set(fh.SHAPE_ROWS_NOT_CARRIED)
    assert everything == here, (
        f"§3 declares a row this reading neither carries nor declines: "
        f"{sorted(everything - here)}; this reading names a §3 row the table does "
        f"not declare: {sorted(here - everything)}")
    assert not (set(carried) & set(fh.SHAPE_ROWS_NOT_CARRIED)), (
        "a row is both carried and declined, so which is true would depend on the "
        "order the two maps are read in")
    for label, reason in fh.SHAPE_ROWS_NOT_CARRIED.items():
        assert len(reason.strip()) > 40, (
            f"§3's row {label!r} is declined without a reason")

    for name, row in fh.NAVIGATION_SHAPE.items():
        assert row["pattern"], f"{name} declares a shape with no levels"
        assert row["target_depth"] >= len(row["pattern"]), (
            f"{name} declares a target depth of {row['target_depth']} for a "
            f"shape {len(row['pattern'])} levels long; the entry cannot sit "
            f"above the shape that describes it")
        assert row["shape"], f"{name} carries no shape for a person to read"
        assert row["why"], f"{name} is declared with no reason"
        if row["check"] is None:
            assert row["form"] is None, (
                f"{name} declares no check, so a form for it would be a rule "
                f"nothing applies")
            continue
        assert 0 <= row["check"] < len(row["pattern"]), (
            f"{name} checks level {row['check']} of a "
            f"{len(row['pattern'])}-level shape")
        assert row["form"], (
            f"{name} checks a level and declares no form for it, so nothing "
            f"could ever fail it")
        assert row["form"] in (fh._GIT_WORKING_DIRECTORY, fh._DIRECTORY,
                               fh._ITEM_ID), (
            f"{name} declares a form this module has no predicate for")

    for key, reason in fh.SHAPE_CHECKS_NOT_MADE.items():
        assert len(reason.strip()) > 40, (
            f"{key}'s silence is not explained; a silent entry IS the next "
            f"unjudged row")


def test_every_shape_exception_family_has_members_and_a_reason():
    """The declared exceptions, and the two ways such a declaration goes stale.

    An entry that occupies a governed slot without being the thing the row
    declares is either named in `SHAPE_EXCEPTIONS` with the reason it was admitted,
    or it is a finding for a person. That makes the list the whole difference
    between "registered" and "new", so the list itself has to be sound:

      * a family with NO members is a declaration covering nothing — the same
        staleness shape §9.69 recorded for a declared list that reads as a rule
        and is in fact a rule over the empty set;
      * a member claimed by two families would take its reason from whichever
        family the loop reached first, so the reason would depend on iteration
        order rather than on the declaration;
      * a member that climbs out of its root would make the list a way to name
        something outside the tree it governs.

    Read the reasons as REGISTRATIONS, not as rulings: §3 gives identity to
    owners, and whether `marketops/§2` should have been an Item is a question
    about that programme, which this module does not answer.
    """
    rows = set(fh.NAVIGATION_SHAPE)
    for row, families in fh.SHAPE_EXCEPTIONS.items():
        assert row in rows, f"{row} is not a row of the shape table"
        for family in families:
            assert family["family"], f"a family under {row} has no name"
            assert family["members"], (
                f"the family {family['family']!r} under {row} names no members, "
                f"so it is a declaration covering nothing")
            assert len(family["reason"].strip()) > 40, (
                f"the family {family['family']!r} under {row} is not explained, "
                f"so its members would be excepted without a reason")
            for member in family["members"]:
                assert member and member.strip() == member
                assert not member.startswith("/"), member
                assert ".." not in member.split("/"), (
                    f"{member!r} climbs out of the row it is declared under")

    declared = fh._declared_exceptions()
    twice = sorted({d for d in declared if declared.count(d) > 1})
    assert not twice, (
        f"a member is claimed by two families: {twice} — its reason would then "
        f"depend on which family was read first")


def test_the_shape_forms_are_form_predicates_and_not_identity_predicates():
    """Which slots may be checked at all, and why the third one is `<ITEM>`.

    §3's own warning is that a depth past its target needs a real identity or a
    tool-format reason, not an automatic move. The same line decides this: "is
    spelled like an Item id" is answerable from a name, and "is a legitimate
    entry" is not. So the predicates are FORM predicates, and the slot that could
    not be checked by form is recorded instead of guessed at.

    Measured 2026-10-09, `<RUN>` as `RUN-<digits>` was DROPPED — 110 entries in
    that slot fail it (`build`, `_recon`, `pre-tokyo-dump`, `RUN-0004-original`,
    `RUN-0005-deepseek`) — because a criterion with 110 exceptions is the same as
    no criterion, at the cost of 110 rows nobody will read. This test pins both
    halves: the predicate accepts what the tree holds, and it refuses the shapes
    that would have turned it into a naming contest.
    """
    for an_id in ("SRV-0011", "LIT-0103", "LIT-0104", "MUC-0007", "HWZ-0042"):
        assert fh._ITEM_ID_RE.match(an_id), an_id
    for not_an_id in ("marketops", "TARGET-CONVERGENCE-B01B02", "slice-2026-09-17",
                      "CCN-20260930", "serveros", "build", "_recon",
                      "RUN-0004-original", "SRV-11", "SRV0011"):
        assert not fh._ITEM_ID_RE.match(not_an_id), (
            f"{not_an_id!r} is not an Item id and must not be accepted as one")

    # No row checks a Run slot, and the reason is recorded rather than encoded.
    assert "artifacts/work-items/<RUN>" in fh.SHAPE_CHECKS_NOT_MADE
    for name, row in fh.NAVIGATION_SHAPE.items():
        if row["check"] is not None:
            assert row["pattern"][row["check"]] != "<RUN>", name


def test_the_rows_whose_form_cannot_fail_are_named_as_such():
    """A row may declare a form that cannot fail. If it does, say so.

    `_DIRECTORY` is a form predicate and an honest transcription of §3, which for
    three of its rows names a directory and gives no naming rule. It is also the
    one form here that a directory occupant cannot miss: `_occupants_at` returns
    directories only, because §3 counts depth 「不计文件名」. So a row carrying it
    reads every occupant as `conforming`, contributes its depth, and can never
    contribute a finding.

    That is a weaker thing than a check, and the difference matters: `conforming`
    on such a row means "this is a directory and §3 says a directory belongs
    here", never "this is the right directory". This test enumerates the rows in
    that condition, so the set is a declaration a reader can see and a new row
    cannot join it by accident.

    A fourth row here is not a defect in itself — it is a decision to be made
    deliberately, which is exactly what this assertion forces.
    """
    cannot_fail = sorted(name for name, row in fh.NAVIGATION_SHAPE.items()
                         if row["form"] == fh._DIRECTORY)
    assert cannot_fail == [
        "records/information/raw",
        "records/information/understanding",
        "test-envs",
    ], (f"these rows declare a form no directory occupant can miss and must be "
        f"named here on purpose: {cannot_fail}")

    # The other two forms CAN fail — one on a name, one on `.git` — so on these
    # four rows `conforming` is a reading rather than a restatement. Enumerated
    # for the same reason: this is the set where a red result is possible.
    checkable = sorted(name for name, row in fh.NAVIGATION_SHAPE.items()
                       if row["form"] in (fh._ITEM_ID, fh._GIT_WORKING_DIRECTORY))
    assert checkable == ["artifacts/work-items", "records/projects",
                         "src", "worktrees"]


def test_navigation_shape_classifies_every_occupant_into_exactly_one_of_five_states(tmp_path):
    """Hermetic, and the reason this reading is worth having.

    The roots are passed in, so this never reads this machine's directories. What
    is asserted is that the reading tells five states apart and merges none of
    them: an occupant that IS the thing the row declares, one the contract
    excludes from the depth statistic, one the row declares NO form for, one named
    in the exception list, and one that is none of those — which is the only state
    that is a finding.
    """
    world = tmp_path / "world"
    work_items = world / "work-items"
    for name in ("SRV-0001", "CCN-20260930", "newarrival"):
        (work_items / "marketops" / name).mkdir(parents=True)
    (work_items / ".pytest_cache").mkdir()
    (world / "skills" / "alpha" / "run1").mkdir(parents=True)
    (world / "skills" / "beta" / "run2").mkdir(parents=True)
    (world / "repos" / "no-git-here").mkdir(parents=True)
    (world / "repos" / "some-repo" / ".git").mkdir(parents=True)

    reading = fh.navigation_shape(roots={
        "artifacts/work-items": str(work_items),
        "artifacts/skills": str(world / "skills"),
        "src": str(world / "repos"),
        "test-envs": str(world / "nowhere"),
    })
    rows = reading["rows"]
    assert set(rows) == set(fh.NAVIGATION_SHAPE), (
        "every row of the table is reported, including the ones this node "
        "cannot resolve: a row that disappears is not the same as a row that "
        "passed")

    # 1. it IS the thing the row declares — and the form is a real predicate, so
    #    `no-git-here` beside it is NOT a pass (see the src row below).
    work = rows["artifacts/work-items"]
    assert work["state"] == "read"
    assert work["conforming"] == ["marketops/SRV-0001"]
    # 2. it is a REGISTERED exception, and it carries the reason it was admitted
    #    under rather than a bare acknowledgement.
    assert [e["path"] for e in work["excepted"]] == ["marketops/CCN-20260930"]
    assert "the date is the identity" in work["excepted"][0]["why"]
    # 3. the contract excludes its name from the depth statistic, and it is
    #    REPORTED rather than dropped — an exclusion a reader cannot see is
    #    indistinguishable from a walk that stopped early.
    assert [e["path"] for e in work["not_counted"]] == [".pytest_cache"]
    assert len(work["not_counted"][0]["why"]) > 40
    # 5. none of the above. The only state that fails the gate.
    assert [e["path"] for e in work["unclassified"]] == ["marketops/newarrival"]
    assert "NEW arrival" in work["unclassified"][0]["why"]
    assert work["not_judged"] == []

    # 4. this row declares NO form, so there is nothing for its occupants to
    #    conform to. Not a failure, and not a pass: counting them either way
    #    would be a claim the contract never made.
    skills = rows["artifacts/skills"]
    assert skills["form"] is None
    assert skills["not_judged"] == ["alpha/run1", "beta/run2"]
    assert skills["conforming"] == [] and skills["excepted"] == []
    assert skills["unclassified"] == []

    # the same row shape on a root where the declared form is a checkout
    src = rows["src"]
    assert src["conforming"] == ["some-repo"]
    assert [e["path"] for e in src["unclassified"]] == ["no-git-here"]
    assert fh._GIT_WORKING_DIRECTORY in src["unclassified"][0]["why"]
    # §3's exclusion applies to the depth statistic itself and not only to the
    # slot reading: `some-repo/.git` is a tool subtree, so level 2 is empty.
    assert src["by_depth"] == {"1": 2}
    assert src["counted_directories"] == 2

    # A root with no directory is UNRESOLVED, never an empty pass — and a row
    # absent from `roots` altogether is the same reading. "Nothing here conforms"
    # and "nothing here was read" are different findings.
    for name in ("test-envs", "worktrees"):
        assert rows[name]["state"] == "unresolved", name
        assert rows[name]["conforming"] == [] and rows[name]["not_judged"] == []
        assert rows[name]["by_depth"] == {}

    assert reading["unclassified"] == ["artifacts/work-items/marketops/newarrival",
                                       "src/no-git-here"]
    assert "moves, renames and deletes nothing" in reading["note"]


def test_a_family_with_no_reason_does_not_excuse_its_member(tmp_path, monkeypatch):
    """A declared member with an empty reason must not read as a pass.

    `SHAPE_EXCEPTIONS` is what separates "registered" from "new", so the reason IS
    the registration. If an empty one were accepted, a member could be listed
    without being explained and the reading would report it as excepted — the same
    gateway `ENTRY_DOCUMENT_NOT_REQUIRED` and `VOCABULARY_NOT_APPLIED` each carry
    against a silent entry, and here it is what stops the exception list decaying
    into a place a name is put to make a finding go away.
    """
    work_items = tmp_path / "work-items"
    (work_items / "proj" / "an-undocumented-arrival").mkdir(parents=True)
    monkeypatch.setattr(fh, "SHAPE_EXCEPTIONS", {
        "artifacts/work-items": [{"family": "a family said nothing about",
                                  "members": ["proj/an-undocumented-arrival"],
                                  "reason": "   "}],
    })

    row = fh.navigation_shape(
        roots={"artifacts/work-items": str(work_items)})["rows"]["artifacts/work-items"]
    assert row["conforming"] == []
    assert row["excepted"] == []
    assert [e["path"] for e in row["unclassified"]] == \
        ["proj/an-undocumented-arrival"]


def test_the_live_governed_entries_are_every_one_of_them_classified():
    """The criterion, read on this node: nothing is left unclassified.

    §8 states the round's completion condition as 「被治理入口符合目标或有具名保留
    理由」 — a governed entry either meets its target or carries a named retention
    reason. This is that sentence evaluated against the real tree, which is also
    the reading that must go red when a new arrival lands unregistered: that is
    what the exception list is for.

    It does not pin WHICH entries are exceptions or exclusions, because those are
    this machine's material and another participant may legitimately retire one —
    a gate that failed on somebody else's cleanup would be a gate about them
    rather than about the contract. What it pins is the structural claim: every
    row resolves, nothing is unclassified, and every entry that is excluded from
    the depth statistic is excluded by a declared name with a reason a person can
    read.
    """
    reading = fh.navigation_shape()
    for name, row in reading["rows"].items():
        assert row["state"] == "read", f"{name} did not resolve on this node"
        assert row["by_depth"], f"{name} counted no directories at all"
    assert reading["unclassified"] == [], (
        "a governed entry is neither the declared shape nor a named exception: "
        f"{reading['unclassified']}")

    declared = fh.depth_not_counted()
    for name, row in reading["rows"].items():
        for entry in row["not_counted"]:
            leaf = entry["path"].rsplit("/", 1)[-1]
            assert leaf in declared, (
                f"{name}/{entry['path']} is excluded from the depth statistic "
                f"without a declaration anyone can read")
            assert len(entry["why"].strip()) > 40, f"{name}/{entry['path']}"
