"""CUT-A: the node payload stops reaching the `fleet/` package beside it.

Why one package matters
-----------------------
An L2 artifact is defined by the transitive `fleet_mcp` closure of ONE
entrypoint, so "what is in this artifact" is computed rather than listed. Until
this change the node's closure reached `fleet_mcp.fleet` through three lazy
`from ..fleet import FleetService` calls in `fleet_mcp/node/connector.py`, and
`fleet_mcp.fleet` reaches `fleet_mcp.registry`, which does `from fleet.transport
import ...` at module import. `fleet/` is a checkout, not released software: a
node payload carrying that edge cannot start on a device where only the
payload was installed.

The cut is NOT a deleted import
-------------------------------
Deleting the three imports alone leaves `connector.py` calling
`FleetService._verify_owner_project` / `._project_catalogue_row` with no name to
call: importing still succeeds (the imports were lazy), and the failure lands in
the one destructive route — `POST /v1/projects/delete` — as `NameError`. So the
three staticmethods move into `fleet_mcp/owner_access.py` as narrow
FleetService-free helpers, `FleetService` keeps one-line delegations, and the
three call sites name the helpers. The refusal codes and wording are unchanged;
`tests/test_fleet_mcp_node.py::DeleteProjectHere` is the behavioural control.

What these tests assert
-----------------------
CODE/UNIT only, offline and device-free: the measured closure, the frozen
membership, the connector no longer naming the service, and the helpers refusing
with the same code and the same sentence as the service they came from — the
last of those exercised in a payload that physically has no `fleet/` beside it.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from fleet_mcp import owner_access
from fleet_mcp.errors import WRITE_NOT_GRANTED, FleetRefusal

REPO = Path(__file__).resolve().parents[1]

#: One entrypoint per kind, the same two the release tool names.
NODE_ENTRY = "fleet_mcp.node.connector"
HUB_ENTRY = "fleet_mcp.server"

#: The node's payload after CUT-A, frozen on purpose. This is the same ratchet
#: `NODE_SHARED_SURFACE` applies to the release build: a module that arrives here
#: without a reviewed change is new coupling, and a module that leaves is a cut
#: that must be recorded. `fleet_mcp.fleet` and the five modules only it reached
#: (`audit`, `dispatch`, `privacy`, `registry`, and itself) are the difference
#: from the 31 modules this entrypoint closed over before the change.
NODE_PAYLOAD = (
    "fleet_mcp",
    "fleet_mcp.cloudcli_native",
    "fleet_mcp.config",
    "fleet_mcp.core_lite_readback",
    "fleet_mcp.core_lite_source",
    "fleet_mcp.disclosure",
    "fleet_mcp.discovery",
    "fleet_mcp.errors",
    "fleet_mcp.feishu_sso",
    "fleet_mcp.identity",
    "fleet_mcp.local_roots",
    "fleet_mcp.mcp_http",
    "fleet_mcp.node",
    "fleet_mcp.node.connector",
    "fleet_mcp.node_protocol",
    "fleet_mcp.owner_access",
    "fleet_mcp.principals",
    "fleet_mcp.providers",
    "fleet_mcp.providers.base",
    "fleet_mcp.providers.cloudcli",
    "fleet_mcp.providers.fleet_node",
    "fleet_mcp.runtime_create_policy",
    "fleet_mcp.runtime_identity",
    "fleet_mcp.scopes",
    "fleet_mcp.session_detail",
    "fleet_mcp.version",
)


# ---------------------------------------------------------------------------
# the closure of one entrypoint, measured the way the release tool measures it
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _Closure:
    modules: tuple[str, ...]
    external_in_repo: tuple[str, ...]
    third_party: tuple[str, ...]


def _resolve(dotted: str, root: Path) -> Path | None:
    base = root.joinpath(*dotted.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _imports_of(data: bytes, member: str) -> list[str]:
    """Every module name this source imports, relative imports made absolute.

    `member` is the payload-relative path, which is what a relative import is
    resolved against; reading bytes rather than a file is what lets the same walk
    run over a payload that was never imported.
    """
    tree = ast.parse(data.decode("utf-8", "replace"))
    package = member.split("/")[:-1]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package[: len(package) - (node.level - 1)])
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            out.append(module)
            out.extend(f"{module}.{alias.name}" for alias in node.names)
    return out


def _in_repo_packages(root: Path) -> dict[str, Path]:
    """Top-level packages other than `fleet_mcp` that live in this repository.

    An import of one of these is a dependency on a checkout rather than on
    released software, which is the whole of "the artifact is standalone".
    `fleet/` has no `__init__.py` and is still importable — a namespace package
    works exactly as long as its parent is on `sys.path` — so both flavours
    count.
    """
    found: dict[str, Path] = {}
    for child in root.iterdir():
        if child.name.startswith((".", "_")) or child.name in ("fleet_mcp", "tests"):
            continue
        if not child.is_dir():
            continue
        if (child / "__init__.py").is_file() or any(child.glob("*.py")):
            found[child.name] = child
    return found


def _closure(entry: str, root: Path = REPO) -> _Closure:
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    in_repo = _in_repo_packages(root)
    seen: dict[str, Path] = {}
    third: set[str] = set()
    external: set[str] = set()
    stack = [entry]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        path = _resolve(name, root)
        if path is None:
            top = name.split(".")[0]
            if top and top != "fleet_mcp" and top not in stdlib:
                third.add(top)
            continue
        seen[name] = path
        # A module cannot be imported without its packages: `a.b.c` needs both
        # `__init__.py` files, which never appear in an import statement.
        parts = name.split(".")
        for depth in range(1, len(parts)):
            stack.append(".".join(parts[:depth]))
        for imported in _imports_of(path.read_bytes(),
                                    path.relative_to(root).as_posix()):
            if imported.startswith("fleet_mcp"):
                stack.append(imported)
            elif imported:
                top = imported.split(".")[0]
                if top in in_repo:
                    external.add(top)
                elif top not in stdlib and top != "__future__":
                    third.add(top)
    return _Closure(tuple(sorted(seen)), tuple(sorted(external)),
                    tuple(sorted(third)))


def _import_sources(path: Path, root: Path = REPO) -> list[tuple[str, tuple[str, ...]]]:
    """Every `from ... import ...` in one file as (absolute module, names)."""
    tree = ast.parse(path.read_bytes().decode("utf-8", "replace"))
    member = path.relative_to(root).as_posix()
    package = member.split("/")[:-1]
    out: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package[: len(package) - (node.level - 1)])
                absolute = ".".join(base + ([node.module] if node.module else []))
            else:
                absolute = node.module or ""
            out.append((absolute, tuple(alias.name for alias in node.names)))
    return out


# ---------------------------------------------------------------------------
# the measurement
# ---------------------------------------------------------------------------
def test_the_node_payload_carries_no_module_that_lives_outside_it():
    """The acceptance criterion: `external_in_repo == ()`.

    Before CUT-A this was `('fleet',)`, reached from `fleet_mcp.registry` — the
    module `fleet_mcp.fleet` imports, which is why a service edge was also a
    checkout edge.
    """
    found = _closure(NODE_ENTRY)
    assert found.external_in_repo == (), (
        "the node payload still needs "
        f"{found.external_in_repo} beside it, so it is not installable alone")


def test_the_node_payload_is_exactly_these_modules():
    """Twenty-six modules, named one by one, rather than a count.

    A count says "nothing was added" and nothing about what was: the tuple is
    what makes a new edge a reviewed change.
    """
    assert _closure(NODE_ENTRY).modules == NODE_PAYLOAD
    assert len(NODE_PAYLOAD) == 26


def test_the_service_and_its_own_dependencies_left_the_node_payload():
    modules = set(_closure(NODE_ENTRY).modules)
    for gone in ("fleet_mcp.fleet", "fleet_mcp.registry", "fleet_mcp.audit",
                 "fleet_mcp.dispatch", "fleet_mcp.privacy"):
        assert gone not in modules, gone


def test_the_hub_payload_still_carries_the_service_it_serves():
    """CUT-A is the node's cut. The hub is CUT-B's, and it still needs `fleet/`.

    Asserted rather than assumed so that this file cannot be read as "the
    repository is standalone now": it is one payload out of two, and the other
    one still imports `fleet.transport` through `fleet_mcp.registry`.
    """
    hub = _closure(HUB_ENTRY)
    assert hub.external_in_repo == ("fleet",)
    assert "fleet_mcp.fleet" in hub.modules
    assert "fleet_mcp.registry" in hub.modules


# ---------------------------------------------------------------------------
# the connector names the helpers, not the service
# ---------------------------------------------------------------------------
def test_the_connector_never_names_the_service():
    """No reference to `FleetService` survives, in any form.

    This is the shape that made the naive fix fail: the imports were lazy, so
    removing them kept `import fleet_mcp.node.connector` green while the
    project-delete route raised `NameError` on the name it no longer had.
    """
    path = REPO / "fleet_mcp/node/connector.py"
    tree = ast.parse(path.read_bytes().decode("utf-8", "replace"))

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
    assert "FleetService" not in names | attributes

    absolute = [module for module, _ in _import_sources(path)]
    assert "fleet_mcp.fleet" not in absolute, absolute


def test_the_call_sites_reach_the_narrow_helpers():
    """The three former `from ..fleet import` sites now import from owner_access."""
    path = REPO / "fleet_mcp/node/connector.py"
    imported: set[str] = set()
    for module, names in _import_sources(path):
        if module == "fleet_mcp.owner_access":
            imported.update(names)
    assert {"verify_owner_project", "project_catalogue_row"} <= imported
    # The delete route's local check travels with them, from the same module.
    assert "project_path_within_roots" in imported


def test_the_service_keeps_its_names_and_delegates():
    """`FleetService._project_row` and friends still exist, and still delegate.

    A caller outside this change (the hub's own create/delete paths, and the
    tests that drive them) keeps working: the behaviour moved, the entry points
    did not disappear.
    """
    path = REPO / "fleet_mcp/fleet.py"
    tree = ast.parse(path.read_bytes().decode("utf-8", "replace"))
    service = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == "FleetService")
    statics: dict[str, ast.FunctionDef] = {}
    for node in service.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        decorators = {getattr(d, "id", None) for d in node.decorator_list}
        if node.name in {"_project_row", "_project_catalogue_row",
                         "_verify_owner_project"} and "staticmethod" in decorators:
            statics[node.name] = node
    assert set(statics) == {"_project_row", "_project_catalogue_row",
                            "_verify_owner_project"}
    for name, node in statics.items():
        called = {n.func.id for n in ast.walk(node)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert called, f"{name} delegates to nothing"
        assert all(isinstance(stmt, (ast.Return, ast.Expr))
                   for stmt in node.body), f"{name} is not a one-line delegation"


# ---------------------------------------------------------------------------
# the payload that has no `fleet/` beside it
# ---------------------------------------------------------------------------
def _write_payload(closure: _Closure, destination: Path) -> Path:
    for module in closure.modules:
        source = _resolve(module, REPO)
        assert source is not None, module
        target = destination.joinpath(*module.split("."))
        if source.name == "__init__.py":
            target = target / "__init__.py"
        else:
            target = target.with_suffix(".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    return destination


#: Runs inside the payload with the repository nowhere on `sys.path`. It answers
#: through the same functions the delete and create routes call, because
#: "importable" is not the question — "can the destructive route run" is.
_PROBE = r"""
import importlib.util, json, sys
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec("fleet") is None, "the fleet package is present"
import fleet_mcp.node.connector  # noqa: F401  (the payload's entrypoint)
from fleet_mcp import owner_access

class Catalogue:
    def __init__(self, rows):
        self.rows = rows
    def native_call(self, tool, arguments):
        assert tool == "projects_list", tool
        return {"projects": self.rows}

class Mute:
    pass

answers = {}
try:
    owner_access.project_catalogue_row(Mute(), "p-1", include_archived=True)
except Exception as exc:  # the provider cannot answer, so this is a refusal
    answers["no_caller"] = [type(exc).__name__, getattr(exc, "code", None)]
row = owner_access.project_catalogue_row(
    Catalogue([{"projectId": "p-1", "path": "/home/fjd/projects/p-1"}]),
    "p-1", include_archived=True)
answers["row"] = row["path"]
try:
    owner_access.verify_owner_project(
        Catalogue([{"projectId": "p-2", "path": "/home/fjd/projects/p-2"}]), "p-1")
except Exception as exc:
    answers["absent"] = [type(exc).__name__, getattr(exc, "code", None)]
owner_access.verify_owner_project(
    Catalogue([{"projectId": "p-1", "path": "/home/fjd/projects/p-1"}]), "p-1")
answers["present"] = True
print(json.dumps(answers))
"""


def test_the_payload_answers_with_the_fleet_package_absent(tmp_path):
    """Copy the closure out, leave the repository behind, run the routes.

    `-I` is what makes this evidence rather than a coincidence: the payload is
    the only thing on `sys.path` besides the interpreter's own libraries, so a
    surviving `from ..fleet import FleetService` would be a `ModuleNotFoundError`
    here — which is exactly what a device sees when only the payload is
    installed.
    """
    payload = _write_payload(_closure(NODE_ENTRY), tmp_path / "payload")
    probe = tmp_path / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    done = subprocess.run([sys.executable, "-I", str(probe), str(payload)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    answers = json.loads(done.stdout.strip().splitlines()[-1])
    assert answers["no_caller"] == ["FleetRefusal", WRITE_NOT_GRANTED]
    assert answers["row"] == "/home/fjd/projects/p-1"
    assert answers["absent"] == ["FleetRefusal", WRITE_NOT_GRANTED]
    assert answers["present"] is True


# ---------------------------------------------------------------------------
# the behaviour the helpers inherited, refusal for refusal
# ---------------------------------------------------------------------------
_EXPECTED = {
    "no_catalogue": "provider cannot verify its current project catalogue",
    "not_a_list": "provider did not return a current project catalogue",
    "absent": "project does not exist in the selected runtime's active catalogue",
}


class _Provider:
    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple] = []

    def native_call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        return self.answer


def _refusal(provider, project_id="p-1"):
    with pytest.raises(FleetRefusal) as caught:
        owner_access.verify_owner_project(provider, project_id)
    return caught.value


def _answer(reader, provider) -> tuple:
    """A row read as `("row", row)` or a refusal as `("refusal", code, detail)`."""
    try:
        return ("row", reader(provider, "p-1", include_archived=True))
    except FleetRefusal as refusal:
        return ("refusal", refusal.code, refusal.detail)


def test_a_provider_that_cannot_read_a_catalogue_is_refused():
    with pytest.raises(FleetRefusal) as caught:
        owner_access.project_catalogue_row(object(), "p-1", include_archived=False)
    assert caught.value.code == WRITE_NOT_GRANTED
    assert caught.value.detail == _EXPECTED["no_catalogue"]


def test_a_catalogue_that_is_not_a_list_is_not_an_empty_one():
    """An unreadable catalogue is a refusal, never "there is nothing there".

    The difference is the one that matters for a delete: "the row is not there"
    when the question was never answered is the blind destructive act this read
    exists to prevent.
    """
    for answer in ({"projects": "p-1"}, {"data": {"projects": 7}}, 42):
        provider = _Provider(answer)
        with pytest.raises(FleetRefusal) as caught:
            owner_access.project_catalogue_row(provider, "p-1",
                                               include_archived=False)
        assert caught.value.code == WRITE_NOT_GRANTED
        assert caught.value.detail == _EXPECTED["not_a_list"]


def test_the_delete_route_still_asks_for_archived_rows():
    """`include_archived` reaches the device as a different question."""
    provider = _Provider({"projects": []})
    owner_access.project_catalogue_row(provider, "p-1", include_archived=True)
    assert provider.calls == [("projects_list", {"includeArchived": True})]
    owner_access.project_catalogue_row(provider, "p-1", include_archived=False)
    assert provider.calls[-1] == ("projects_list", {"includeArchived": False})


def test_a_row_whose_ids_disagree_is_ambiguous_and_not_a_row():
    rows = [{"projectId": "p-1", "id": "p-2", "path": "/x"}]
    assert owner_access.project_row(rows, "p-1") is None
    assert owner_access.project_row(rows, "p-2") is None
    assert owner_access.project_row([{"id": "p-1"}], "p-1") == {"id": "p-1"}


def test_an_archived_row_is_visible_to_a_delete_and_not_to_a_create():
    archived = [{"projectId": "p-1", "path": "/x", "isArchived": True}]
    assert owner_access.project_catalogue_row(_Provider({"projects": archived}),
                                              "p-1", include_archived=True) == archived[0]
    with pytest.raises(FleetRefusal) as caught:
        owner_access.verify_owner_project(_Provider({"projects": archived}), "p-1")
    assert caught.value.code == WRITE_NOT_GRANTED
    assert caught.value.detail == _EXPECTED["absent"]


def test_the_refusal_reads_the_same_as_the_one_the_service_raised():
    """The wording is part of the contract this change promised not to touch.

    `_verify_owner_project` used to build this sentence in `fleet.py`; a caller
    that matches on it (or an operator reading a device's log) must not see a
    different one because the code moved house.
    """
    from fleet_mcp.fleet import FleetService

    provider = _Provider({"projects": []})
    with pytest.raises(FleetRefusal) as via_service:
        FleetService._verify_owner_project(provider, "p-1")
    with pytest.raises(FleetRefusal) as via_helper:
        owner_access.verify_owner_project(provider, "p-1")
    assert via_service.value.code == via_helper.value.code == WRITE_NOT_GRANTED
    assert via_service.value.detail == via_helper.value.detail


def test_the_service_answers_through_the_helper_for_every_shape():
    """Same answer, same call to the device, whichever name is used."""
    from fleet_mcp.fleet import FleetService

    shapes = (
        {"projects": [{"projectId": "p-1", "path": "/x"}]},
        {"projects": [{"projectId": "p-1", "isArchived": True, "path": "/x"}]},
        {"projects": []},
        {"projects": "not a list"},
        [{"projectId": "p-1", "path": "/x"}],
    )
    for answer in shapes:
        one, two = _Provider(answer), _Provider(answer)
        left = _answer(FleetService._project_catalogue_row, one)
        right = _answer(owner_access.project_catalogue_row, two)
        assert left == right, answer
        assert one.calls == two.calls == [("projects_list", {"includeArchived": True})]
        rows = answer if isinstance(answer, list) else answer.get("projects")
        if isinstance(rows, list):
            assert (FleetService._project_row(rows, "p-1")
                    == owner_access.project_row(rows, "p-1"))


def test_the_release_tool_and_this_file_measure_the_same_closure():
    """The mirror above, pinned against the tool when the tool is in the tree.

    `integrations/fleet-v1-l2-release/` travels with its own PR, so this check
    skips on a branch that stacks on a tree without it — and then it says so,
    rather than passing on a comparison that never ran.
    """
    source = REPO / "integrations/fleet-v1-l2-release/l2_release.py"
    if not source.is_file():
        pytest.skip(f"{source.relative_to(REPO).as_posix()} is not in this tree")
    import importlib.util

    spec = importlib.util.spec_from_file_location("_l2_release_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for kind, entry in ((module.L2_NODE, NODE_ENTRY), (module.L2_HUB, HUB_ENTRY)):
        assert module.ENTRY[kind] == entry
        found = module.closure(entry, REPO)
        mine = _closure(entry)
        assert tuple(found.modules) == mine.modules
        assert tuple(found.external_in_repo) == mine.external_in_repo
