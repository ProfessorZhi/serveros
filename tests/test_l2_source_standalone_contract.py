"""RED → GREEN contract for the L2 V1-only source cut, which #407 owns.

The three tests below describe the property the *source tree* must have before
`l2-node` stops needing a checkout: the connector reaches no `fleet` module, the
closure stays inside `fleet_mcp`, and the artifact that follows is independently
installable. They are **RED today** and are marked `xfail(strict=True)`, so they
neither fail CI while the patch is blocked nor stay quiet when it lands: the day
`fleet_mcp/` is cut, each one XPASSes, a strict marker turns that into a failure,
and whoever merged the cut deletes the marker in the same change.

The patch itself is published, measured and unapplied —
`integrations/fleet-v1-l2-release/L2-CUT-A.patch`, three files, +70/−52, checked
with `git apply --check` against #407's head `9538238d`. `HANDOFF-407.md` carries
the numbers; this file carries the acceptance.

Why a behavioural contract and not a packaging one: deleting the connector's
three `from ..fleet import FleetService` statements makes the artifact's flags
flip while leaving three call sites named `FleetService` with nothing to resolve
them. Measured on a copy at `9538238d`, that cut turns
`tests/test_fleet_mcp_node.py::DeleteProjectHere` red with
`NameError: name 'FleetService' is not defined` — 8 tests, packaging clean, the
product broken. The last test here asserts the *name* is gone, not just the
import, because that is the difference between the two.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-v1-l2-release"


def _load(name: str, path: Path):
    """Load a module by path, registering it before `exec_module` (dataclasses
    read `sys.modules` for their own module)."""
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


l2 = _load("l2_release_source_contract", PACKAGE / "l2_release.py")

NODE_VERSION = "1.4.0+n20261028a"

BLOCKED = (
    "BLOCKED_KEEP_AND_GUARD: #407 owns fleet_mcp/**. The patch is published as "
    "integrations/fleet-v1-l2-release/L2-CUT-A.patch and measured on a copy at "
    "9538238d; when it lands this XPASSes and the strict marker must be deleted "
    "in the same change that lands the cut."
)


@pytest.mark.xfail(strict=True, reason=BLOCKED)
def test_the_node_source_closure_no_longer_leaves_the_repository():
    """The property, measured on the real tree rather than on a copy of it."""
    closure = l2.closure(l2.ENTRY[l2.L2_NODE], ROOT)
    assert closure.external_in_repo == (), (
        "the node closure still needs the repository-root package(s) "
        f"{closure.external_in_repo}; see HANDOFF-407.md")


@pytest.mark.xfail(strict=True, reason=BLOCKED)
def test_the_connector_names_no_fleet_service():
    """An import deleted without its call sites is a `NameError` at the first
    delete, not a cut — so the contract is about the *name*, not the statement."""
    source = (ROOT / "fleet_mcp" / "node" / "connector.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    froms = [node for node in ast.walk(tree)
             if isinstance(node, ast.ImportFrom)
             and (node.module or "").split(".")[0] == "fleet"]
    plains = [alias.name for node in ast.walk(tree)
              if isinstance(node, ast.Import)
              for alias in node.names
              if alias.name.split(".")[0] == "fleet"]
    assert froms == [] and plains == [], (
        "the connector still imports the repository package: "
        f"{[ast.unparse(n) for n in froms]} {plains}")
    assert "FleetService" not in source, (
        "a `FleetService` reference survives the import that defined it — the "
        "artifact would build and then raise NameError on the first call")


@pytest.mark.xfail(strict=True, reason=BLOCKED)
def test_the_node_artifact_becomes_independently_installable(tmp_path):
    """Closure cut + pin trimmed to what the closure still reaches ⇒ installable.

    The pin is trimmed *in the test* because the trim is mechanical: the build
    already refuses a pin entry the payload no longer reaches, and names the
    entries that leave (`audit`, `dispatch`, `fleet`, `privacy`, `registry` at
    `9538238d`). Nothing here edits `fleet_mcp/`.
    """
    closure = l2.closure(l2.ENTRY[l2.L2_NODE], ROOT)
    declared = l2.NODE_SHARED_SURFACE
    # What the pin must say after the cut: exactly the shared surface this
    # closure measures — modules that left are dropped, modules that joined
    # (`fleet_mcp.principals`, added on #407's head) are taken in.
    trimmed = tuple(sorted(set(closure.modules) - set(l2.NODE_EXCLUSIVE)))
    try:
        l2.NODE_SHARED_SURFACE = trimmed
        manifest, members = l2.manifest_for(l2.L2_NODE, NODE_VERSION, root=ROOT)
        out = l2.write_artifact(manifest, members, tmp_path / "node.tar.gz")
        report = l2.verify(l2.read_artifact(out), expect_kind=l2.L2_NODE)
    finally:
        l2.NODE_SHARED_SURFACE = declared

    assert manifest["l2"]["external_in_repo"] == []
    assert manifest["l2"]["import_standalone"] is True
    assert manifest["l2"]["runtime_standalone"] is True, (
        "the node imports without a checkout but its runtime path still does not")
    assert manifest["l2"]["install"]["independently_installable"] is True
    assert manifest["l2"]["install"]["blocked_by"] == []
    assert report["independently_installable"] is True


def test_the_published_patch_is_the_cut_it_claims_to_be():
    """The handoff artifact is checkable without a checkout of #407's branch.

    It must touch exactly three files, delete no `owner_access` behaviour, and —
    the point of the whole exercise — leave no `FleetService` reference in the
    connector. A patch that only deleted imports would fail this last clause,
    which is what the `DeleteProjectHere` measurement taught.
    """
    patch = (PACKAGE / "L2-CUT-A.patch").read_text(encoding="utf-8")
    touched = {line.split(" b/", 1)[1] for line in patch.splitlines()
               if line.startswith("diff --git ")}
    assert touched == {"fleet_mcp/fleet.py", "fleet_mcp/node/connector.py",
                       "fleet_mcp/owner_access.py"}, touched
    added = [line[1:] for line in patch.splitlines() if line.startswith("+")
             and not line.startswith("+++")]
    removed = [line[1:] for line in patch.splitlines() if line.startswith("-")
               and not line.startswith("---")]
    assert not any("FleetService" in line for line in added), (
        "the patch reintroduces FleetService into the connector")
    # the three lifted helpers land in owner_access and leave fleet.py
    for name in ("project_row", "project_catalogue_row", "verify_owner_project"):
        assert any(line.startswith(f"def {name}(") for line in added), name
    assert not any("from ..fleet import" in line for line in added), (
        "the cut must not add a lazy import to the module it is cutting")
    assert len(removed) <= 60 and len(added) <= 80, (
        f"the patch grew: {len(added)} added / {len(removed)} removed lines")
