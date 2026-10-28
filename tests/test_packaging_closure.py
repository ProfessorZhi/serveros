"""The packaging lists must be closed under loading.

WHY THIS FILE EXISTS
--------------------
`scripts/build.py` decides what an artifact contains from two lists that have to
agree:

  * `CORE_MODULES` decides which directories are visited, and
  * `plugin/bundle.yaml` supplies each one's source path.

A module that is in the first but not the second is **silently skipped**
(`bundle.get(module, {}).get("path")` is `None`, the loop says `continue`), and
nothing downstream complains: the source checkout runs, the tests pass, and the
installed node fails later with a `FileNotFoundError`. `plugin/bundle.yaml`
records that defect three times. Commenting it is what this repository has done
so far; the comment does not fail when the next directory is added.

So the property is written down as one fixpoint instead:

    every top-level directory that a PACKAGED module loads from is PACKAGED.

That is not a claim about what should ship. It is the weaker, checkable claim
that the artifact never contains code whose load targets are absent from the
artifact — which is the only reason "it ships" and "it runs when installed" can
be the same statement. A directory that is reached by packaged code and is not a
`CORE_MODULES` member is red here, whether it was added yesterday or is about to
be.

Measured on the tree this was written against: 218 packaged `.py` files, no
directory reached that is not a member, and all thirteen members declare a
bundle path. So a red from this file is attributable to a change, not to
long-standing noise — which is the difference between a gate and a warning.

WHAT THIS FILE DELIBERATELY DOES NOT SAY
----------------------------------------
`CORE_MODULES` is a PACKAGING list, not a layer authority. Six of its members
(`context`, `space`, `relations`, `memory`, `intake`, `documents`) are `product`
in `docs/architecture/layers.yaml`, which is correct: they ship, they are not
core. The last test states that as a premise instead of leaving it to be
rediscovered, because "add it to CORE_MODULES" is exactly the shortcut somebody
would reach for to make a layer gate green — and it would make the packaging
list mean two things at once.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
LAYERS = REPO / "docs" / "architecture" / "layers.yaml"
BUNDLE = REPO / "plugin" / "bundle.yaml"

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _build():
    """`scripts/build.py` without running it.

    Imported rather than re-implemented so this file cannot drift from the loop
    it is checking — a second copy of the rule is a second thing to be wrong.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import build
    finally:
        sys.path.pop(0)
    return build


def _bundle() -> dict:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def _packaged_python_sources(build) -> list[Path]:
    """Every `.py` the artifact actually receives, as repo-relative paths.

    `local_skill_files` is the same function the copy loop calls, so this sees
    the exclusions (`__pycache__`, `.pyc`) exactly as the artifact does.
    """
    found: list[Path] = []
    for module in build.CORE_MODULES:
        for src, _rel in build.local_skill_files(REPO / module):
            if src.suffix == ".py":
                found.append(src.resolve().relative_to(REPO))
    return found


def _reached_tops(build) -> dict[str, list[str]]:
    """`{top-level directory: [packaged file that loads into it]}`."""
    from loader_graph import module_refs
    reached: dict[str, list[str]] = {}
    for source in _packaged_python_sources(build):
        for ref in module_refs(source):
            parts = ref.parts
            # A root-level module (`transport.py`) has no directory, so it is
            # not covered by any CORE_MODULES member and could never ship with
            # one. Reported rather than skipped: the fixpoint would otherwise
            # be quiet about the one shape it cannot express.
            top = parts[0] if len(parts) > 1 else f"<repository root>/{parts[0]}"
            reached.setdefault(top, []).append(source.as_posix())
    return reached


def test_packaged_code_loads_only_packaged_directories() -> None:
    """The fixpoint. This is the whole file."""
    build = _build()
    reached = _reached_tops(build)
    assert reached, "no load edges were parsed; the scan would certify nothing"

    missing = sorted(top for top in reached if top not in build.CORE_MODULES)
    assert missing == [], (
        "these directories are loaded by modules the artifact ships, but are "
        "not in CORE_MODULES, so the artifact does not contain them and the "
        "installed copy fails with FileNotFoundError:\n"
        + "\n".join(
            f"  {top}\n      loaded by "
            + ", ".join(sorted(reached[top])[:3])
            + (" …" if len(reached[top]) > 3 else "")
            for top in missing)
        + "\nAdding the directory to CORE_MODULES is only half the fix: see "
          "test_every_core_module_declares_its_bundle_path, and note that "
          "CORE_MODULES is a packaging list, NOT a layer claim.")


def test_every_core_module_declares_its_bundle_path() -> None:
    """The other half of the pair, and the silent `continue` made loud.

    `build.py` visits `CORE_MODULES` and asks the bundle for each member's path;
    a member without one is skipped without an error. This asserts the key
    exists and names the same directory, which is what the loop's own
    `ValueError` checks only after the `continue` has already let it through.
    """
    build = _build()
    bundle = _bundle()

    undeclared = [m for m in build.CORE_MODULES if not (bundle.get(m) or {}).get("path")]
    assert undeclared == [], (
        "CORE_MODULES members with no `path` in plugin/bundle.yaml; the build "
        f"loop skips these silently: {undeclared}")

    mismatched = {m: (bundle.get(m) or {}).get("path") for m in build.CORE_MODULES
                  if (bundle.get(m) or {}).get("path") not in (None, m)}
    assert mismatched == {}, (
        f"bundle path must equal the module name: {mismatched}")


def test_a_bundle_path_names_a_directory_the_build_visits() -> None:
    """The reverse direction, and it is deliberately not the same failure.

    A `CORE_MODULES` member with no `path` is harmful: the artifact silently
    loses the directory. A `path` with no member is only dead config — the loop
    never asks for it, so an entry pointing at a retired directory is never read
    and never raises. It is checked anyway because that is exactly how a
    retirement becomes invisible: dropping a directory from `CORE_MODULES` and
    leaving its bundle entry behind changes nothing observable, and the next
    reader takes the surviving entry as evidence that the directory still ships.

    Keys that are not module entries (`skills`, `default_prompt`, …) carry no
    `path` and are out of scope. Measured: no orphan before the D2 stage-2 move,
    and exactly one on it (`fleet_hub`) — red on purpose until `#413` lands, for
    the same missing line as the other two, not for a fourth reason.
    """
    build = _build()
    bundle = _bundle()
    with_path = {k for k, v in bundle.items() if isinstance(v, dict) and "path" in v}
    assert with_path, "no `path` entries parsed; the scan would certify nothing"

    orphans = sorted(with_path - set(build.CORE_MODULES))
    assert orphans == [], (
        "these declare a source path in plugin/bundle.yaml but no CORE_MODULES "
        "member ever asks for it, so nothing ships from them and nothing fails "
        f"when the directory is retired: {orphans}")


def test_the_packaging_list_is_not_a_layer_claim() -> None:
    """`CORE_MODULES` contains product directories, and that is correct.

    Stated as a premise so the shortcut is unavailable: if a layer gate is ever
    made green by adding a directory to `CORE_MODULES`, this fails first and
    says why. A packaging list that also meant "core" would make §24 answerable
    by editing a build script.
    """
    build = _build()
    layers = yaml.safe_load(LAYERS.read_text(encoding="utf-8"))

    def layer_of(name: str):
        entry = (layers.get("directories") or {}).get(name)
        return entry if isinstance(entry, str) else "split"

    product_members = sorted(m for m in build.CORE_MODULES if layer_of(m) == "product")
    assert product_members, (
        "no product directory is in CORE_MODULES any more. If that was a "
        "deliberate change to make CORE_MODULES mean 'core', every layer rule "
        "in this tree that could be written against it must be re-read first: "
        "packaging and layering would have been merged into one list.")

    # The layer table and the packaging list are answered by different files,
    # and the §24 tables live in a third place. None of them may be derived
    # from another, or one edit would silently change three answers.
    #
    # The two tables already disagree about `context` today, and the
    # disagreement is load-bearing rather than a mistake to tidy: §24's
    # CORE_DIRS is a whitelist of where the core's code may live and what it
    # may load from — the daemon loads `context/actor.py`, so `context` is
    # there — while layers.yaml answers which layer a directory belongs to, and
    # says `product`. One table is about reach, the other about ownership.
    from test_core_ability_boundary import CORE_DIRS
    assert set(CORE_DIRS) != set(build.CORE_MODULES), (
        "the §24 core-directory table has become the packaging list; one of "
        "the two answered a different question before this")
    assert set(build.CORE_MODULES) - set(CORE_DIRS), (
        "CORE_MODULES is now a subset of the §24 core table, so the two could "
        "be conflated without anything failing — which is the shape that lets "
        "'add it to CORE_MODULES' look like a layer decision")
    assert not (set(CORE_DIRS) - set(build.CORE_MODULES)), (
        "a directory the core is allowed to live in does not ship, so the "
        f"artifact cannot start: {sorted(set(CORE_DIRS) - set(build.CORE_MODULES))}")


def test_a_new_top_level_directory_must_be_registered_in_the_layer_table() -> None:
    """The other list a new top-level directory needs, pointed at its owner.

    Not a duplicate of `tests/test_repository_layers.py` (which owns the rule):
    this exists so that a packaging-side change learns the layer-side
    requirement before it is merged, instead of after a red on somebody else's
    PR.
    """
    build = _build()
    layers = yaml.safe_load(LAYERS.read_text(encoding="utf-8"))
    registered = set((layers.get("directories") or {}))
    unregistered = [m for m in build.CORE_MODULES if m not in registered]
    assert unregistered == [], (
        "these are packaged but absent from docs/architecture/layers.yaml: "
        f"{unregistered}")
