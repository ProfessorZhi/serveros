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
