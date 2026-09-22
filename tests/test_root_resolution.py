#!/usr/bin/env python3
"""Tests for the root resolver: what the roots source said, before what it gave.

`_ops_roots` read hwz-ops' machine instance and returned `{}` for every one of
these, and the finding downstream could not tell them apart:

  the locator was not reachable         the node cannot answer
  it was read and would not parse       a malformed document, at a position
  it was read, no `roots` map           the document's shape moved on
  it carries `roots`, not a mapping     a shape the reader cannot use
  it carries an empty `roots` map       genuinely nothing declared

On 2026-09-22 the third and the second both hold on this node, and the collapsed
reading came out as a HIGH finding naming five unresolved root tokens. Each of
those reads as a binding somebody forgot to write. The actual facts are one
locator that expects a schema the document no longer has, and one bad scalar --
and nothing in the report said either, because `_load` answers `None` for every
reason at once and `{}` was then indistinguishable from a measurement.

So these tests pin the *reading*, not the resolution. A resolver that reports
five tokens and cannot say why is a resolver whose output a reader has to guess
at, which is the failure this whole checker exists to find one level up.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills/serveros/scripts"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("serveros_architecture_check_roots", "architecture_check.py")


def instance(tmp_path: Path, body: str) -> Path:
    """A throwaway hwz-ops whose machine instance is ``body``, committed.

    Committed rather than left in the worktree on purpose: `_ops_roots` reads a
    pinned git object, so a file that exists only on disk is not what this is
    testing -- the checker would not see it either.
    """
    repo = tmp_path / "ops"
    (repo / "workspace").mkdir(parents=True, exist_ok=True)
    (repo / "workspace" / "environment.yaml").write_text(body, encoding="utf-8")
    for argv in (["init", "-q", "-b", "main", str(repo)],
                 ["-C", str(repo), "add", "-A"],
                 ["-C", str(repo), "-c", "user.email=t@example.invalid",
                  "-c", "user.name=t", "commit", "-q", "-m", "instance"]):
        subprocess.run(["git", *argv], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return repo


def read(tmp_path: Path, body: str) -> dict:
    """The source reading for a machine instance with this body."""
    repo = instance(tmp_path, body)
    _roots, _prov, source_read = check._ops_roots(repo, "main")
    return source_read


def v2_body(extra: str = "") -> str:
    """The shape this node actually has: roles bound by profile, no `roots`.

    Copied from the live document's own header rather than invented, because the
    point is that this shape is legitimate -- `schema: hwz/environment.v2` with
    `binding_authority` delegating role roots to the profile -- and the locator
    still expects the v1 `roots` map.
    """
    return ("schema: hwz/environment.v2\n"
            "device_ref: device:tokyo-shared-dev-01\n"
            "binding_authority: serveros:fleet/profiles.yaml#linux-shared-multiuser\n"
            "roles:\n"
            "  toolchains:      <PERSISTENT_ROOT>/toolchains\n"
            f"{extra}")


# ---------------------------------------------------------------------------
# the five readings
# ---------------------------------------------------------------------------


def test_a_document_with_no_roots_map_is_not_a_document_with_no_roots(tmp_path):
    """The regression. These were the same `{}` and they are not the same fact.

    A v2 machine instance answers every question about its roots by pointing at
    the profile. A locator that only reads `roots` therefore finds nothing --
    and "this document's shape moved" must not be reported as "the machine
    declares no roots", because the first is fixed by aligning the locator and
    the second by binding tokens.
    """
    source = read(tmp_path, v2_body())
    assert source["found"] is True
    assert source["roots_key"] == "absent"
    assert source["schema"] == "hwz/environment.v2"
    assert source["keys"] == 0


def test_a_document_that_will_not_parse_names_the_position(tmp_path):
    """`unparsed` without a location is not actionable, so the position is the test.

    A plain YAML scalar may not begin with a backtick -- it is a reserved
    indicator -- and this node's machine instance does exactly that in a `note`.
    The reader must be able to say where, or the finding sends its reader
    looking through a 200-line document for a character they cannot see.
    """
    source = read(tmp_path, v2_body("  note: `.build-*` is a hidden bucket\n"))
    assert source["found"] is True
    assert source["roots_key"] == "unparsed"
    # 1-based, and the body above puts the bad scalar on its sixth line with the
    # backtick in column 9 -- where a person opening the file would find it.
    assert source["parse_error"]["line"] == 6
    assert source["parse_error"]["column"] == 9
    assert "cannot start any token" in source["parse_error"]["problem"]


def test_a_document_without_the_key_is_distinguishable_from_a_broken_one(tmp_path):
    """`absent` and `unparsed` are one character apart and lead different places.

    Both mean no roots map was read. Only one of them means the document is
    wrong, and the other is a locator that is out of date with a schema change.
    """
    absent = read(tmp_path / "a", v2_body())
    broken = read(tmp_path / "b", v2_body("  note: `x`\n"))
    assert absent["roots_key"] != broken["roots_key"]
    assert absent["parse_error"] is None
    assert broken["parse_error"] is not None


def test_a_roots_map_that_is_not_a_mapping_says_so(tmp_path):
    """A list has a length; calling that a key count would be the old lie again."""
    source = read(tmp_path, "schema: hwz/environment.v2\nroots:\n  - DATA_ROOT\n")
    assert source["roots_key"] == "not-a-mapping"
    assert source["keys"] == 0


def test_an_unreachable_locator_is_not_a_document_with_a_problem(tmp_path):
    """No repo at all: the node cannot answer, and that is the whole reading."""
    repo = tmp_path / "empty"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _roots, _prov, source = check._ops_roots(repo, "main")
    assert source["found"] is False
    assert source["roots_key"] == "unreadable"
    assert source["parse_error"] is None


def test_a_present_roots_map_reads_as_present(tmp_path):
    """The one case that is not a finding, so it must be reachable."""
    source = read(tmp_path, "schema: hwz/environment.v1\n"
                            "roots:\n  SRC_ROOT: /srv/src\n  OPS_ROOT: /srv/ops\n")
    assert source["roots_key"] == "present"
    assert source["keys"] == 2


# ---------------------------------------------------------------------------
# what the reading turns into
# ---------------------------------------------------------------------------


def test_a_reading_that_yielded_a_map_produces_no_finding():
    assert check.roots_source_finding({"source_read": {"roots_key": "present"}}) is None


def test_a_document_that_was_read_is_opened_and_one_that_was_not_is_recorded():
    """The split is whether the bytes were read, not how bad the news is.

    A malformed document has a location and can be repaired, so it is OPEN. A
    locator nobody could reach is a statement about this node's reach -- the
    same status an absent registry gets -- so it is OBSERVED and carries a lower
    severity. Reversing these would put a note about reach on the blocking list
    and a repairable defect in the record.
    """
    read = check.roots_source_finding(
        {"source_read": {"found": True, "roots_key": "unparsed"}, "roots_source": "x"})
    unreached = check.roots_source_finding(
        {"source_read": {"found": False, "roots_key": "unreadable"}, "roots_source": "x"})
    assert (read["id"], read["status"], read["severity"]) == (
        "ROOTS_SOURCE_UNREAD", "OPEN", "HIGH")
    assert (unreached["status"], unreached["severity"]) == ("OBSERVED", "MEDIUM")


def test_the_consequence_carries_its_cause():
    """Two findings, one fact each, and the token list does not have to explain itself.

    `ROOT_TOKEN_UNRESOLVED` is still true when the source is unread -- those
    tokens really do not resolve. What must not stand is a reader having to
    infer, from their number, which of the five readings produced them.
    """
    topology = yaml.safe_load("""
roles:
  governance: {root: OPS_ROOT, template: "<root>/x"}
roots_source:
  required_keys: [DATA_ROOT, WORKSPACE_ROOT]
  locator: ops/workspace/environment.yaml
  derivations: {}
""")
    args = SimpleNamespace(ops_repo=Path("/nonexistent"), ops_ref="main",
                           data_root=Path("/d"), workspace_root=Path("/w"))
    root_map = check.root_tokens(topology, args)
    assert root_map["source_read"]["roots_key"] == "unreadable"
    found = check.roots_source_finding(root_map)
    assert found["observed"]["locator"] == "ops/workspace/environment.yaml"


# ---------------------------------------------------------------------------
# what the reading must not become
# ---------------------------------------------------------------------------


def test_a_parse_failure_carries_the_parsers_reason_and_nothing_else(tmp_path):
    """The parser's `problem` and position, not its `str()`.

    `str(error)` is the message *plus* a code frame, and a code frame is a copy
    of the document. A findings report gets read and pasted into places, and the
    machine instance is not a document this checker was invited to republish;
    the position is what the reader actually needs.

    Asserted by equality against the parser rather than by scanning for a
    planted marker, and that is the whole point of the shape. The first version
    of this test planted a sentinel and asserted it was absent -- and it passed
    against `str(error)`, because PyYAML truncates its code frame to a fixed
    width and the sentinel was cut off before it could appear. The assertion was
    green on a property of PyYAML's window, not on a property of this code:
    widen the sentinel enough and the same assertion would have gone red on
    correct code and green on the mutant. Comparing to the parser's own field
    has no window to hide in.
    """
    body = v2_body("  note: `x`\n")
    source = read(tmp_path, body)
    assert source["roots_key"] == "unparsed"
    assert set(source["parse_error"]) == {"type", "line", "column", "problem"}

    with pytest.raises(yaml.YAMLError) as raised:
        yaml.safe_load(body)
    error = raised.value
    assert source["parse_error"]["problem"] == error.problem
    assert source["parse_error"]["line"] == error.problem_mark.line + 1
    assert source["parse_error"]["column"] == error.problem_mark.column + 1


# ---------------------------------------------------------------------------
# the v2 delegation: where an instance that declares no `roots` says them anyway
# ---------------------------------------------------------------------------
#
# `hwz/environment.v2` answers "which role lives where" by naming a profile
# instead of stating paths, and the reader above already reads that shape
# correctly as `absent`. Absent is not the same as unanswered, so the second half
# of the reading is the delegate -- and it is read from ITS owner: the token
# vocabulary is the contract's, the paths are the profile's, and neither is
# copied into this file or into hwz-ops.

CONTRACT = json.loads(
    (REPO / "skills/serveros/references/topology-contract.json").read_text(encoding="utf-8"))

PROFILE_NAME = "linux-shared-multiuser"
BINDING = f"serveros:fleet/profiles.yaml#{PROFILE_NAME}"


def host() -> str:
    return f"schema: hwz/environment.v2\ndevice_ref: device:tokyo-shared-dev-01\nbinding_authority: {BINDING}\n"


def serveros_repo(tmp_path: Path, body: str) -> Path:
    """A throwaway serveros repo holding only the device profile, committed.

    Committed for the same reason `instance` is: the profile is read as a git
    object at the canonical ref, so a file left in the worktree is not what a
    consumer sees.
    """
    repo = tmp_path / "serveros"
    (repo / "fleet").mkdir(parents=True, exist_ok=True)
    (repo / "fleet" / "profiles.yaml").write_text(body, encoding="utf-8")
    for argv in (["init", "-q", "-b", "main", str(repo)],
                 ["-C", str(repo), "add", "-A"],
                 ["-C", str(repo), "-c", "user.email=t@example.invalid",
                  "-c", "user.name=t", "commit", "-q", "-m", "profile"]):
        subprocess.run(["git", *argv], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return repo


def profile_body(paths: dict, *, states=("BOUND", "UNBOUND", "NOT_APPLICABLE")) -> str:
    return yaml.safe_dump(
        {"schema": "serveros/fleet-profiles.v1",
         "binding_states": list(states),
         "profiles": {PROFILE_NAME: {"device_ref": "device:tokyo-shared-dev-01",
                                     "paths": paths}}},
        sort_keys=False)


VINCE = "/data/home/vince"
REAL_PATHS = {
    "data_root": {"state": "BOUND", "path": VINCE},
    "artifacts": {"state": "BOUND", "path": f"{VINCE}/artifacts"},
    "state": {"state": "BOUND", "path": f"{VINCE}/state/serveros"},
    "toolchains": {"state": "BOUND", "path": f"{VINCE}/toolchains"},
    "runtime-assets": {"state": "BOUND", "path": f"{VINCE}/runtime-assets"},
    "downloads": {"state": "BOUND", "path": f"{VINCE}/downloads"},
    "workspace.src": {"state": "BOUND", "path": f"{VINCE}/workspace/src"},
    "workspace.worktrees": {"state": "BOUND", "path": f"{VINCE}/workspace/worktrees"},
    "knowledge.corporate": {"state": "BOUND", "path": f"{VINCE}/workspace/knowledge"},
    "software.gui": {"state": "NOT_APPLICABLE"},
}


def bound(tmp_path: Path, *, paths=None, body=None, contract=None, serveros=True,
          level: str = "profile"):
    """(roots by token, reading) for a v2 instance delegating to a profile."""
    ops = instance(tmp_path / "o", host() if body is None else body)
    sr = serveros_repo(tmp_path / "s", body or profile_body(paths or REAL_PATHS)) if serveros else None
    args = SimpleNamespace(ops_repo=ops, ops_ref="main",
                           serveros_repo=sr, canonical_ref="main",
                           data_root=Path("/caller/d"), workspace_root=Path("/caller/w"),
                           data_root_level=level, workspace_root_level=level)
    root_map = check.root_tokens(CONTRACT if contract is None else contract, args)
    return root_map, root_map["binding_read"]


def test_a_v2_instance_that_delegates_yields_a_roots_map(tmp_path):
    """The sixth reading: a delegation that answers is not a finding.

    The instance carries no `roots` **on purpose** and names the profile that
    binds them. Calling that an unread source blames the v2 document for a reader
    that stopped at the v1 shape -- and on 2026-09-22 that is exactly what it did,
    as five unresolved root tokens reading like five forgotten bindings.
    """
    root_map, reading = bound(tmp_path)
    assert [t for t, p in root_map["paths"].items() if p is None] == []
    assert reading["profile"] == PROFILE_NAME
    assert reading["reason"] is None
    assert root_map["mode"]["KNOWLEDGE_ROOT"] == "bound"
    assert check.roots_source_finding(root_map) is None


def test_every_argument_the_contract_keys_on_is_actually_read(tmp_path):
    """The role vocabulary is the contract's and the paths are the profile's.

    Asserted against the REAL contract rather than a fixture, because the claim
    is that these two documents agree: a `role_to_token` entry naming a role the
    profile does not bind is a token that silently never resolves, and the only
    way that goes unnoticed is a test that reads a copy.
    """
    root_map, reading = bound(tmp_path)
    source = CONTRACT["roots_source"]["binding"]
    assert set(reading["bound_roles"]) >= set(source["role_to_token"])
    for role, token in source["role_to_token"].items():
        expected = REAL_PATHS[role]["path"]
        assert str(root_map["paths"][token]) == expected, f"{role} -> {token}"


def test_the_workspace_children_the_profile_has_no_role_for_are_derived(tmp_path):
    """ops/ records/ test-envs/ knowledge/ are contract positions, not bindings.

    The profile binds `workspace.src` and `workspace.worktrees` and carries no
    role for the other four children. They resolve anyway, because
    `workspace_root_children` already says where they sit -- and that is not a
    second absolute path: each is the BOUND parent plus the contract's own
    segment, which is why the assertion is about the parent they share.
    """
    root_map, reading = bound(tmp_path)
    parent = Path(root_map["paths"]["WORKSPACE_ROOT"])
    for token, child in (("OPS_ROOT", "ops"), ("RECORDS_ROOT", "records"),
                         ("TEST_ENVS_ROOT", "test-envs"), ("KNOWLEDGE_ROOT", "knowledge")):
        assert root_map["paths"][token] == parent / child
        assert root_map["mode"][token] == "bound"
        # The profile binds none of these roles -- it binds the two children
        # WORKSPACE_ROOT is the parent of, and the contract places the rest.
        semantic = CONTRACT["workspace_root_children"][child]
        assert semantic not in reading["bound_roles"]
    assert reading["derived_from_workspace_root"] == [
        "KNOWLEDGE_ROOT", "OPS_ROOT", "RECORDS_ROOT", "TEST_ENVS_ROOT"]


def test_a_role_the_profile_declines_is_not_a_path(tmp_path):
    """UNBOUND is a declared gap, not a path somebody forgot to write.

    `downloads` is the clean case: the contract has no position for it, so the
    profile binding is the ONLY source of DOWNLOADS_ROOT. Recording it UNBOUND
    then has to leave the token unresolved -- an UNBOUND role that resolves is
    how a directory nobody owns acquires a root token.

    Asserted through `mode` rather than through `tokens`, because the token has
    to be *asked for* before its absence means anything. The first version of
    this test read `"DOWNLOADS_ROOT" not in binding_read["tokens"]` and passed
    against a checker that never resolved the token at all: green on a vacancy,
    not on a decline.

    `knowledge.corporate` looks like the same case and is not: the contract
    positions the `knowledge` child of WORKSPACE_ROOT in its own right, so
    KNOWLEDGE_ROOT resolves whether or not the profile says anything. That is the
    difference between a role this contract places and a path the profile alone
    supplies.
    """
    paths = dict(REAL_PATHS, **{"downloads": {"state": "UNBOUND"}})
    root_map, reading = bound(tmp_path, paths=paths)
    assert "downloads" not in reading["bound_roles"]
    assert root_map["mode"]["DOWNLOADS_ROOT"] == "unresolved"
    assert root_map["paths"]["DOWNLOADS_ROOT"] is None
    # The same token, bound, resolves -- so the assertion above is about the
    # decline and not about a token this checker cannot reach.
    bound_map, _reading = bound(tmp_path / "bound")
    assert str(bound_map["paths"]["DOWNLOADS_ROOT"]) == f"{VINCE}/downloads"


def test_a_state_the_profile_never_declared_is_reported_not_skipped(tmp_path):
    """A vocabulary change must not read as a machine that binds nothing.

    `binding_states` is the profile's own list, so it is read rather than
    assumed. The day someone adds `PARTIAL` to it, every role using that state
    would otherwise stop being a path in silence, and the report would say the
    profile binds nothing instead of saying this reader is out of date.
    """
    paths = dict(REAL_PATHS, **{"artifacts": {"state": "PARTIAL", "path": f"{VINCE}/artifacts"}})
    _root_map, reading = bound(tmp_path, paths=paths)
    assert reading["states_undeclared"] == ["PARTIAL"]
    assert "artifacts" not in reading["bound_roles"]


def test_two_workspace_roles_that_disagree_leave_the_parent_unresolved(tmp_path):
    """Taking either one alone would accept a layout this contract denies.

    WORKSPACE_ROOT is defined as the parent `workspace.src` and
    `workspace.worktrees` SHARE. A profile binding them to unrelated places is
    not a profile whose WORKSPACE_ROOT can be picked from a coin -- and picking
    one is invisible, because the token still resolves and every derived child
    then hangs off the wrong tree. Asked of `_bound_roots` directly, because that
    is where the rule lives: `root_tokens` would fill the gap from the
    CLI-derived default, which is a different answer to a different question.
    """
    paths = dict(REAL_PATHS, **{"workspace.worktrees": {"state": "BOUND", "path": "/elsewhere/wt"}})
    _roots, reading = bound(tmp_path, paths=paths)
    assert reading["workspace_parent_agrees"] is False
    assert "WORKSPACE_ROOT" not in reading["tokens"]


def test_a_root_the_caller_passed_outranks_the_delegate(tmp_path):
    """The declared order is flag, then environment, then the profile.

    Before this, argparse's `default=` and a flag the caller typed were the same
    object by the time the resolver saw them, so a `--data-root` passed on
    purpose was outvoted by the machine instance -- and the run reported success
    about a tree nobody had asked about, which is the declared-vs-actual gap this
    checker exists to find.
    """
    root_map, _reading = bound(tmp_path, level="flag")
    assert str(root_map["paths"]["DATA_ROOT"]) == "/caller/d"
    assert root_map["mode"]["DATA_ROOT"] == "flag"
    assert root_map["root_levels"]["WORKSPACE_ROOT"] == "flag"
    # The derivations follow the caller's root rather than the profile's, or the
    # two halves of one run would describe different machines.
    assert str(root_map["paths"]["STATE_ROOT"]) == "/caller/d/state/serveros"


def test_a_binding_of_the_wrong_shape_is_reported_rather_than_searched_for(tmp_path):
    """`serveros:fleet/profiles.yaml` with no `#profile` names no profile.

    Guessing that the last path segment is the profile would send a reader into a
    file to look for a name nobody wrote, and would succeed often enough to look
    like it worked.
    """
    _root_map, reading = bound(tmp_path, body=(
        "schema: hwz/environment.v2\nbinding_authority: serveros:fleet/profiles.yaml\n"))
    assert reading["file"] is None
    assert reading["reason"] == "the binding is not of the declared form"
    assert reading["tokens"] == []


def test_a_delegate_with_no_repository_to_read_is_reach_not_a_defect(tmp_path):
    """The same split `_ops_roots` makes: unreachable is a reading, not a defect.

    Nothing about this node is wrong when the caller hands the checker no
    serveros repository to read the profile out of. Reporting it as a malformed
    binding would put a note about this run's inputs on the blocking list.
    """
    _root_map, reading = bound(tmp_path, serveros=False)
    assert reading["declared"] is True
    assert reading["reason"] == "no serveros repository was supplied to read the profile from"
    assert reading["tokens"] == []
