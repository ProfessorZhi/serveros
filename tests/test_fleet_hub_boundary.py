"""The L2 audience rule lives in a hub package, and the hub reaches nothing.

D2 of `docs/plans/fleet-v1-gap-and-work-packages.md` moves the hub assembly out
of `runtime/` (the L1 node daemon's directory) into a hub of its own. This file
holds the part of that move that can be done without touching the layer table or
the packaging lists, and it is written as the migration's evidence rather than
as a description of it:

  * WHERE THE FILE IS. The rule now lives in `runtime/fleet_hub/` and the old
    path does not exist. The old path is deliberately not left behind as a
    forwarder module, and `test_the_old_path_is_absent` states the reason: the
    verdict this module issues is an HMAC over a per-process key generated at
    import. A second copy of the file, loaded under a second path or a second
    name, is a second key — its verdicts are refused by the readers that hold
    the first copy's type. A forwarder would therefore not be "compatible with
    old imports"; it would be a silent way to mint verdicts nobody accepts.

  * ONE MODULE, ONE KEY. `fleet_monitor_plan` and `fleet_monitor_source` must
    resolve the SAME module object, and the test asserts identity rather than
    the name string, because the name is what makes identity work and the path
    is what changed.

  * THE HUB REACHES NOTHING. Every file in `runtime/fleet_hub/` imports only the
    standard library and loads nothing at all. That is what lets the package be
    promoted to a top-level `fleet_hub/` later without dragging `runtime/`
    along, and it is checkable now, while the package is still nested.

  * THE ARTIFACT STILL SHIPS IT. `tests/loader_graph.py` derives the packaged
    surface from what the entry points actually load, so the moved file must
    appear in `production_closure()`. If the move dropped out of the loader
    graph the artifact would omit a module its surfaces load — the C4 defect
    `plugin/bundle.yaml` documents — and that is a red here rather than a
    discovery on an installed node.

Two of these are guards rather than migrations: they pass before the move as
well. They are labelled in their docstrings, and each carries a positive
control proving the matcher can fail, so a guard that has stopped looking is
visible as a failure instead of as a pass.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
HUB = RUNTIME / "fleet_hub"
MODULE = HUB / "fleet_audience.py"
OLD_PATH = RUNTIME / "fleet_audience.py"

#: The synthetic name every loader registers the rule under. The NAME is what
#: makes two callers share one module object and therefore one HMAC key; the
#: PATH is what the move changed. Keeping the name is not a leftover — changing
#: it would be the very fork this file exists to prevent.
MODULE_NAME = "serveros_fleet_audience"

#: Top-level directories the hub must not reach into. `runtime` is on the list
#: even though the hub currently sits inside it: the package may not load its
#: way back out, which is what makes the later promotion a `git mv` plus two
#: one-line registrations rather than a rewiring.
FORBIDDEN_TOPS = ("runtime", "fleet_mcp", "mcp", "fleet", "workengine", "reports")

CODE_SUFFIXES = frozenset({".py"})
CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".service", ".timer", ".json",
                             ".sh", ".ps1", ".toml"})
SKIP_DIRS = frozenset({".git", "__pycache__", ".pytest_cache", "node_modules"})

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load(name: str, path: Path):
    """The repository's loader convention: consult the cache before overwriting.

    Written the same way every other loader in this tree writes it, because the
    test's own loading must not be the thing that creates the second copy.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _rebuild(module, verdict):
    """Reconstruct an instance from its own fields — a forgery, in effect.

    A verdict's `mac` is checked in `__post_init__`, so rebuilding one asks the
    module "would you believe this instance?" without touching its private key.
    That is the smallest honest probe of the two-copies hazard.
    """
    return module.AudienceVerdict(
        audience=verdict.audience, device_refs=verdict.device_refs,
        device_signature=verdict.device_signature, allowed=verdict.allowed,
        denied=verdict.denied, ok=verdict.ok, issuer=verdict.issuer,
        card_signature=verdict.card_signature, reason=verdict.reason,
        mac=verdict.mac)


#: This file names the old path and quotes a loader call on purpose — to assert
#: they are gone, and to prove the two matchers below can fail. A scan that
#: includes its own source reports those assertions as the violation they are
#: asserting against, so both scans exclude it. (This was a real failure, not a
#: hypothetical: it appeared the moment the file went from untracked to tracked,
#: because `git ls-files` is what the scans enumerate.)
SELF = Path(__file__).resolve()


def _tracked(suffixes, exclude_self: bool = False) -> list[Path]:
    import subprocess
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    return [REPO / rel for rel in out
            if Path(rel).suffix in suffixes
            and not any(part in SKIP_DIRS for part in Path(rel).parts)
            and not (exclude_self and (REPO / rel).resolve() == SELF)]


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# Loader calls that name this file, with the synthetic name they use.
_LOAD_CALL = re.compile(
    r"_load\(\s*[\"']([^\"']+)[\"']\s*,\s*[^)\n]*?[\"']fleet_audience\.py[\"']")


def _loader_names() -> list[tuple[str, str]]:
    """`(relative path, synthetic name)` for every loader call naming the rule."""
    found: list[tuple[str, str]] = []
    for path in _tracked(CODE_SUFFIXES, exclude_self=True):
        if "tests/data" in path.as_posix():
            continue
        for match in _LOAD_CALL.finditer(_text(path)):
            found.append((str(path.relative_to(REPO)), match.group(1)))
    return found


class TestTheRuleLivesInTheHubPackage:
    def test_the_module_is_in_the_hub_package(self) -> None:
        assert MODULE.is_file(), (
            "D2 moves the audience rule into the hub package; "
            f"{MODULE.relative_to(REPO)} is where the loaders point")

    def test_the_old_path_is_absent(self) -> None:
        """No forwarder at the old path — and the reason is the HMAC key.

        A module that re-exported the moved one would keep old imports working
        only if it were the SAME file; two files are two `_VERDICT_KEY`s, and a
        verdict issued by the copy a caller happened to reach is refused by the
        reader holding the other. Failing to import is the honest outcome.
        """
        assert not OLD_PATH.exists(), (
            f"{OLD_PATH.relative_to(REPO)} must not exist: a forwarder here "
            "would load a second copy with a second HMAC key, and verdicts "
            "would be refused for reasons no reader could see")

    def test_a_stale_consumer_fails_at_import_rather_than_quietly_forking(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            _load("serveros_fleet_audience_stale_probe", OLD_PATH)

    def test_the_hub_package_is_not_a_second_import_route(self) -> None:
        """Nobody reaches the hub as a package; the path loader is the one route.

        `import runtime.fleet_hub.fleet_audience` would work as a namespace
        package and produce a second module object under a second name — the
        exact fork `_load`'s single synthetic name prevents.
        """
        pattern = re.compile(r"^\s*(?:from|import)\s+runtime\.fleet_hub\b", re.M)
        offenders = sorted(str(p.relative_to(REPO)) for p in _tracked(CODE_SUFFIXES)
                           if pattern.search(_text(p)))
        assert offenders == [], offenders
        assert pattern.search("from runtime.fleet_hub import fleet_audience\n")
        assert pattern.search("import runtime.fleet_hub.fleet_audience\n")
        assert not pattern.search("from runtime import fleet_audience\n")


class TestEveryLoaderSharesOneModule:
    def test_the_plan_and_the_source_resolve_the_same_object(self) -> None:
        audience = _load(MODULE_NAME, MODULE)
        # The synthetic name is shared, so whichever loaded first wins; if a
        # stale entry from a different path were in the cache the origin check
        # below would catch it rather than the identity check silently passing.
        assert Path(audience.__file__).resolve() == MODULE.resolve()

        source = _load("serveros_fleet_monitor_source", RUNTIME / "fleet_monitor_source.py")
        plan = _load("serveros_fleet_monitor_plan", RUNTIME / "fleet_monitor_plan.py")

        assert source.fleet_audience is audience
        assert plan.fleet_audience is audience
        # And the type a verdict is checked against is one class.
        assert source.AudienceVerdict is plan.fleet_audience.AudienceVerdict

    def test_every_loader_names_it_the_same(self) -> None:
        sites = _loader_names()
        names = sorted({name for _, name in sites})
        assert len(sites) >= 3, (
            "floor: two runtime callers and one test load the rule; seeing "
            f"fewer means the scan stopped looking, not that they left: {sites}")
        assert names == [MODULE_NAME], (
            "two synthetic names are two module objects and two HMAC keys: "
            f"{sites}")

    def test_a_verdict_from_a_second_copy_is_refused(self) -> None:
        """The negative case for the forwarder that is not there.

        Loading the same file under a different name is what a compatibility
        shim would do. The verdict it issues must be refused by the module the
        readers hold, which is the measurement behind `test_the_old_path_is_absent`.
        """
        held = _load(MODULE_NAME, MODULE)
        second = _load("serveros_fleet_audience_second_copy", MODULE)
        assert second is not held

        issued_by_second = second.issue_verdict(
            audience="user:ou_alice0000000000000000000",
            device_refs=["dell-5820"], allowed=["dell-5820"], denied=[],
            card_signature="a" * 64, issuer=second.ISSUER_POLICY)

        # The control first: rebuilding a verdict the HELD copy issued does not
        # raise, so the refusal below is about the key and not about how the
        # probe rebuilds an instance.
        issued_by_held = held.issue_verdict(
            audience="user:ou_alice0000000000000000000",
            device_refs=["dell-5820"], allowed=["dell-5820"], denied=[],
            card_signature="a" * 64, issuer=held.ISSUER_POLICY)
        _rebuild(held, issued_by_held)

        with pytest.raises(held.AudienceRefused):
            _rebuild(held, issued_by_second)


class TestTheHubReachesNothing:
    def test_the_hub_imports_only_the_standard_library(self) -> None:
        files = sorted(HUB.glob("*.py"))
        assert files, "the hub package is empty; this test would certify nothing"

        offenders: list[str] = []
        imports_seen = 0
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports_seen += 1
                        if alias.name.split(".")[0] not in sys.stdlib_module_names:
                            offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    imports_seen += 1
                    root = (node.module or "").split(".")[0]
                    if node.level == 0 and root not in sys.stdlib_module_names:
                        offenders.append(f"{path.name}:{node.lineno} from {node.module}")

        assert offenders == [], offenders
        assert imports_seen > 0, "no import nodes parsed; the scan saw nothing"

    def test_the_hub_loads_no_other_file(self) -> None:
        """No `_load`, no division-chain path to another module.

        An import scan alone would miss `_load("x", _HERE / "y.py")`, which is
        this tree's other way of depending on something.
        """
        pattern = re.compile(r"_load\(|_" + "HERE|" + r"spec_from_file_location")
        offenders = sorted(p.name for p in HUB.glob("*.py") if pattern.search(_text(p)))
        assert offenders == [], offenders
        assert pattern.search('_load("x", _HERE / "y.py")')

    def test_no_hub_file_names_another_layer(self) -> None:
        pattern = re.compile(
            "|".join(rf"[\"']{top}/" for top in FORBIDDEN_TOPS)
            + rf"|/\s*[\"'](?:{'|'.join(FORBIDDEN_TOPS)})[\"']")
        for path in sorted(HUB.glob("*.py")):
            assert not pattern.search(_text(path)), path.name
        # Positive control: the shapes a hub file would use if it reached out.
        assert pattern.search('spec = ROOT / "runtime" / "daemon.py"')
        assert pattern.search('load("fleet_mcp/tools.py")')


class TestTheArtifactStillShipsIt:
    def test_the_loader_graph_follows_the_callers_to_the_nested_path(self) -> None:
        """The graph must see the edge through the subdirectory, not around it.

        `tests/loader_graph.py` is what derives the packaged surface, and its
        "beside the caller" fallback used to keep only the basename. With the
        rule flat that happened to resolve; one directory deeper it resolved to
        nothing, so the edge vanished and the closure stopped naming a module
        its callers load — C4 again, with the module present on disk. The
        scanner now tries the whole relative candidate first; this is the
        measurement that it does.
        """
        from loader_graph import closure, module_refs
        hub = MODULE.relative_to(REPO).as_posix()

        for caller in ("runtime/fleet_monitor_plan.py", "runtime/fleet_monitor_source.py"):
            refs = {p.as_posix() for p in module_refs(Path(caller))}
            assert hub in refs, (caller, sorted(refs))
            assert hub in {p.as_posix() for p in closure(Path(caller))}, caller

        # Negative control: a module that loads nothing of the sort.
        assert hub not in {p.as_posix() for p in module_refs(Path("runtime/card_delivery.py"))}

    def test_the_monitor_stack_is_not_a_shipped_surface_today(self) -> None:
        """Why this move changes no packaging list, stated as a checkable fact.

        `production_closure()` is derived from `PRODUCTION_ENTRIES` — the CLI,
        the daemon and the two MCP adapters. The monitor card stack is not
        reachable from any of them, so neither the rule nor its callers are on
        the artifact's required set, before or after the move. If that changes,
        this fails and the packaging question for the hub becomes live: its
        host directory would then have to be a `CORE_MODULES` entry of its own.
        """
        from loader_graph import production_closure
        needed = {p.as_posix() for p in production_closure()}
        callers = {"runtime/fleet_monitor_plan.py", "runtime/fleet_monitor_source.py"}
        assert callers.isdisjoint(needed), (
            "the monitor stack is now reachable from a production entry, so the "
            "hub package is now part of the artifact's required set: re-open "
            "the D2 packaging question (CORE_MODULES / bundle.yaml) before "
            "moving it to a top-level directory")
        assert MODULE.relative_to(REPO).as_posix() not in needed

    def test_the_hosting_directory_is_what_ships_it(self) -> None:
        """Why no packaging list has to learn a new name for this move.

        `scripts/build.py` copies each `CORE_MODULES` entry recursively, so a
        file under `runtime/` travels with `runtime/`. This is asserted rather
        than assumed: it is the difference between this move being shippable
        today and being blocked on a file another change owns.
        """
        sys.path.insert(0, str(REPO / "scripts"))
        try:
            import build
        finally:
            sys.path.pop(0)
        assert "runtime" in build.CORE_MODULES


class TestNoStalePathSurvives:
    def test_no_code_or_config_names_the_old_path(self) -> None:
        """The drift gate: a path that only some call sites were updated to.

        Scoped to executable and configured surfaces. `docs/` is out of scope on
        purpose — audit records are evidence of what was true when they were
        written, and rewriting them would delete that evidence rather than
        repair a caller.
        """
        pattern = re.compile(r"runtime/fleet_audience\.py")
        offenders = sorted(str(p.relative_to(REPO))
                           for p in _tracked(CODE_SUFFIXES | CONFIG_SUFFIXES,
                                             exclude_self=True)
                           if pattern.search(_text(p)))
        assert offenders == [], (
            "these still name the pre-move path and would break on an installed "
            f"node: {offenders}")
        assert pattern.search('spec_from_file_location("x", "runtime/fleet_audience.py")')

    def test_no_service_or_bundle_declares_the_rule_by_path(self) -> None:
        """Nothing configures the rule by path, so no unit can break on the move.

        The claim is the finding: if a unit or a composition named this file,
        the move would need that file changed too, and that changes who owns it.
        """
        units = sorted(p for p in _tracked(CONFIG_SUFFIXES)
                       if p.suffix in {".service", ".timer"} or "deploy" in p.parts)
        assert units, "no unit files found; the scan would certify nothing"
        named = sorted(str(p.relative_to(REPO)) for p in units if "fleet_audience" in _text(p))
        assert named == [], named
        assert "fleet_audience" in "ExecStart=/usr/bin/python3 runtime/fleet_audience.py"


class TestTheHubDoesNotBecomeAConsumerOfTheControlPlane:
    def test_the_control_plane_does_not_load_the_hub(self) -> None:
        """D2's acceptance rule, in its checkable form while the hub is nested."""
        pattern = re.compile(r"[\"']fleet_hub[\"'/]|[\"']fleet_hub[\"']\s*/")
        offenders = sorted(str(p.relative_to(REPO))
                           for p in (REPO / "fleet_mcp").rglob("*.py")
                           if pattern.search(_text(p)))
        assert offenders == [], offenders
        assert pattern.search('_load("x", _HERE / "fleet_hub" / "fleet_audience.py")')
