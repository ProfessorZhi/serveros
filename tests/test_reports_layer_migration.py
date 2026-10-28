#!/usr/bin/env python3
"""D3 stage 1: the business calendar leaves `runtime/`, with a facade and a gate.

D3 moves the product layer out of `runtime/` into `reports/`. Like D2, the move
cannot start at the top level — a new top-level directory has to be registered
in `docs/architecture/layers.yaml` and in `scripts/build.py`'s `CORE_MODULES`,
and both are held by in-flight changes. Stage 1 puts the file in
`runtime/reports/`, which travels with `runtime/` and needs no packaging change.

Unlike the audience rule (`#439`), this module gets a **forwarder at the old
path**, and the difference is the point rather than an inconsistency:

  * `fleet_audience` issues verdicts signed with a per-import key, so a second
    module object is a second key and its verdicts are refused. A forwarder
    there would not be compatible with old imports; it would be a way to mint
    verdicts nobody accepts. That is why the old path fails to import.
  * This module is constants and pure functions over a declaration document.
    Two objects would be interchangeable, so a facade costs nothing and buys
    the thing a migration needs: an out-of-tree caller that knows the old path
    keeps working while the tree moves.

What this file checks, in the order a reviewer would ask:

  1. The implementation moved and the old path is a facade, not a copy.
  2. Both routes reach ONE implementation object — same classes, same
     functions, one `sys.modules` entry — so an `except` written against one
     route catches a raise from the other.
  3. No stale load site survives, and the config that names this file by path
     agrees with where it now lives. That config is documentation; nothing
     else in the tree would notice it going stale, which is exactly why it is
     asserted here.
  4. The loader graph sees the real edge. It did NOT, before `#439` fixed
     `tests/loader_graph.py`'s "beside the caller" fallback: with only the
     basename kept, every caller resolved to `runtime/business_calendar.py` —
     the facade — so the graph named a thin stub and the implementation was
     invisible to `production_closure()`. The facade is what made that failure
     silent instead of loud, and this class is the reason to prefer a loud one.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
IMPL = RUNTIME / "reports" / "business_calendar.py"
FACADE = RUNTIME / "business_calendar.py"
CANONICAL = "serveros_runtime_business_calendar"

#: Callers that load the module. Measured, not assumed: the drift gate below
#: fails if the count drops, so a call site reverting to the old path is a red
#: rather than a smaller list that still passes.
CALLERS = (
    "runtime/card_day.py",
    "runtime/card_period.py",
    "runtime/card_scenarios.py",
    "runtime/card_source.py",
    "runtime/card_surface_runner.py",
    "runtime/surface_cli.py",
)

SELF = Path(__file__).resolve()

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _load(name: str, path: Path):
    """The repository's loader convention: consult the cache before overwriting."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tracked(*patterns: str) -> list[Path]:
    import subprocess
    out = subprocess.run(["git", "-C", str(REPO), "ls-files", *patterns],
                         capture_output=True, text=True, check=True).stdout
    return [REPO / rel for rel in out.splitlines() if rel]


class TestTheImplementationMoved:
    def test_the_module_lives_under_reports(self) -> None:
        assert IMPL.is_file(), (
            "stage 1 of D3 puts the product module in runtime/reports/, which "
            "ships with runtime/ and needs no CORE_MODULES entry")

    def test_the_moved_file_is_the_same_module_not_a_rewrite(self) -> None:
        """The rename carried the content; it did not take the chance to edit it.

        A move is reviewable only if the diff is a rename. The headline of the
        original docstring is the cheapest proof that this file is the module
        that was there before, under a different directory.
        """
        source = _text(IMPL)
        assert "Which days this instance broadcasts on, and in which hours." in source
        assert "def day(" in source and "class CalendarError" in source

    def test_the_old_path_is_a_forwarder_and_not_a_copy(self) -> None:
        """A second copy would drift; a forwarder cannot."""
        facade = _text(FACADE)
        assert facade, "the facade must exist; old imports are meant to keep working"
        assert not re.search(r"^\s*def day\(", facade, re.M), (
            "the old path defines the module's own functions — that is a copy, "
            "and a copy is the drift this facade exists to prevent")
        assert "__getattr__" in facade
        assert "reports" in facade and "business_calendar.py" in facade

        # Control: the scan above does find `def day(` where it does belong.
        assert re.search(r"^\s*def day\(", _text(IMPL), re.M)


class TestBothRoutesReachOneImplementation:
    def test_the_old_import_still_works(self) -> None:
        legacy = _load("legacy_route_probe", FACADE)
        for name in ("day", "routine_window", "CalendarError", "DAY_KINDS",
                     "WINDOW_FIRST_HOUR", "declaration", "read_declaration"):
            assert hasattr(legacy, name), name
        assert legacy.DAY_KINDS == ("WORKDAY", "NON_WORKING_DAY", "UNKNOWN")

    def test_the_new_import_works(self) -> None:
        impl = _load(CANONICAL, IMPL)
        assert Path(impl.__file__).resolve() == IMPL.resolve()
        assert impl.DAY_KINDS == ("WORKDAY", "NON_WORKING_DAY", "UNKNOWN")

    def test_both_routes_share_their_classes_and_functions(self) -> None:
        """The reason a facade is safe here and was not for the audience rule.

        `except facade.CalendarError` must catch a raise from the implementation.
        That holds only while both routes yield one module object's attributes.
        """
        impl = _load(CANONICAL, IMPL)
        legacy = _load("legacy_route_probe", FACADE)
        assert legacy.CalendarError is impl.CalendarError
        assert legacy.day is impl.day
        assert legacy.routine_window is impl.routine_window

    def test_one_implementation_object_is_registered(self) -> None:
        impl = _load(CANONICAL, IMPL)
        holders = [name for name, module in list(sys.modules.items())
                   if module is not None
                   and Path(getattr(module, "__file__", "") or "").resolve()
                   == IMPL.resolve()]
        assert holders == [CANONICAL], holders
        assert sys.modules[CANONICAL] is impl

    def test_the_old_route_still_publishes_the_design_sentences(self) -> None:
        """`__doc__` is the one attribute `__getattr__` cannot forward.

        A module's `__doc__` lives in its own `__dict__`, so attribute fallback
        never runs for it: a facade that only defines `__getattr__` answers old
        readers with its OWN docstring and the implementation's design
        sentences — §9/§10/§11 in this module — disappear. Nothing about the
        forwarder looks wrong while that happens.

        Found by measurement, not by reading: this move's first full run of
        `tests/test_business_calendar.py` failed exactly this assertion and
        nothing else, against a baseline whose failure set was identical.
        """
        impl = _load(CANONICAL, IMPL)
        legacy = _load("legacy_route_probe", FACADE)
        for section in ("§9", "§10", "§11"):
            assert section in (impl.__doc__ or ""), section
            assert section in (legacy.__doc__ or ""), (
                f"the old route lost {section}: the facade's own docstring "
                "replaced the implementation's instead of following it")
        # ... and the facade still explains itself. The composed text is a
        # superset, not a swap: whoever opens the old path needs to know why
        # what they are reading forwards.
        assert "Compatibility facade" in (legacy.__doc__ or "")

    def test_every_public_name_is_reachable_through_the_facade(self) -> None:
        """A facade that copied names would go stale; `__getattr__` cannot.

        Floor on the count so a scan that found nothing fails instead of
        passing: the module exports twenty public names today.
        """
        impl = _load(CANONICAL, IMPL)
        legacy = _load("legacy_route_probe", FACADE)
        public = [name for name in dir(impl) if not name.startswith("_")]
        assert len(public) >= 15, public
        missing = [name for name in public if not hasattr(legacy, name)]
        assert missing == [], missing

        # The only names the facade may add are the four it declares to do its
        # one job, plus the three it imports. Anything else is the facade
        # starting to be a module in its own right — which is how a forwarder
        # turns into a fork.
        def public_names(module) -> set[str]:
            return {name for name in dir(module) if not name.startswith("_")}

        assert public_names(legacy) - public_names(impl) == {
            "CANONICAL_NAME", "IMPLEMENTATION", "importlib", "sys"}, (
            sorted(public_names(legacy) - public_names(impl)))


class TestNoStaleLoadSiteSurvives:
    def test_no_loader_names_the_old_path(self) -> None:
        """The drift gate: a call site that kept pointing at the facade.

        It would still work — the facade forwards — and that is why nothing else
        would catch it. It also keeps the loader graph naming a stub.
        """
        patterns = (
            re.compile(r"_HERE\s*/\s*[\"']business_calendar\.py[\"']"),
            re.compile(r"_load\([^)\n]*[\"']runtime/business_calendar\.py[\"']"),
        )
        offenders: list[str] = []
        for path in _tracked("*.py"):
            if path.resolve() == SELF:
                continue  # it names the old path in order to assert it is gone
            text = _text(path)
            if any(p.search(text) for p in patterns):
                offenders.append(str(path.relative_to(REPO)))
        assert offenders == [], (
            f"these still load the facade instead of {IMPL.relative_to(REPO)}: "
            f"{offenders}")

        # Control: both shapes match where they are written, so the scan is
        # looking at the right thing rather than at nothing.
        assert patterns[0].search('_load("x", _HERE / "business_calendar.py")')
        assert patterns[1].search('_load("x", "runtime/business_calendar.py")')

    def test_every_caller_points_at_the_new_path(self) -> None:
        """The floor: six call sites, all naming the implementation."""
        for rel in CALLERS:
            text = _text(REPO / rel)
            assert "reports" in text and "business_calendar.py" in text, rel
            assert re.search(r"business_calendar\.py", text), rel

    def test_the_node_root_contract_names_where_the_file_now_lives(self) -> None:
        """A config that names a file by path, with no other check behind it.

        `opened_by` is documentation: no code reads it, so a stale value is
        invisible until someone follows it while debugging the very host state
        it describes.
        """
        document = yaml.safe_load(
            (REPO / "fleet" / "node-root-contract.yaml").read_text(encoding="utf-8"))
        opened_by = document["declarations"]["business-calendar.yaml"]["opened_by"]
        assert opened_by == "runtime/reports/business_calendar.py", opened_by
        assert (REPO / opened_by).is_file(), (
            f"{opened_by} is named as the module that opens this declaration "
            "and does not exist")

    def test_no_tracked_code_or_config_names_the_old_path(self) -> None:
        """One rule over everything that could go stale, self excluded."""
        pattern = re.compile(r"runtime/business_calendar\.py")
        offenders = sorted(
            str(p.relative_to(REPO)) for p in _tracked("*.py", "*.yaml", "*.yml",
                                                       "*.json", "*.sh", "*.ps1")
            if p.resolve() != SELF and pattern.search(_text(p)))
        assert offenders == [], offenders
        assert pattern.search("spec_from_file_location('x', 'runtime/business_calendar.py')")


class TestTheLoaderGraphSeesTheRealEdge:
    def test_every_caller_resolves_to_the_implementation(self) -> None:
        """The graph must name the file that does the work, not the stub.

        Measured before `#439`: all six resolved to `runtime/business_calendar.py`
        because the "beside the caller" fallback kept only the basename. A
        facade makes that failure quiet — the graph resolves to a real file, so
        nothing looks wrong — which is worse than resolving to nothing.
        """
        from loader_graph import closure, module_refs

        target = IMPL.relative_to(REPO).as_posix()
        facade = FACADE.relative_to(REPO).as_posix()
        for rel in CALLERS:
            refs = {p.as_posix() for p in module_refs(Path(rel))}
            assert target in refs, (rel, sorted(r for r in refs if "calendar" in r))
            assert facade not in refs, (
                f"{rel} still resolves to the facade; the implementation would "
                "be invisible to production_closure()")
            assert target in {p.as_posix() for p in closure(Path(rel))}, rel

    def test_a_module_that_loads_nothing_of_the_sort_has_no_such_edge(self) -> None:
        from loader_graph import module_refs
        refs = {p.as_posix() for p in module_refs(Path("runtime/paths.py"))}
        assert IMPL.relative_to(REPO).as_posix() not in refs
