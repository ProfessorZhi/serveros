#!/usr/bin/env python3
"""Containment for the one file a test run must not be able to write.

`mcp/human_control.py --attach` records its outcome to this node's ingress
record, and since 27i it does so BY DEFAULT. That default is right and is not
up for renegotiation here: the verb's primary caller is a turn hook, a hook's
command string is read off a settings file the process running it may have read
before `--record` existed, and a default of off would therefore have made the
one turn that matters the one turn that left no trace — "the hook never ran"
and "the hook ran and minted nothing" being the same empty file.

The default is right for a hook and wrong for a test, and the difference is not
a matter of care. Measured 2026-09-28 on the installed 27i/27j payload: running

    pytest tests/test_actor_context.py

appended two lines to `{DATA_ROOT}/logs/serveros/human-control/ingress.jsonl` —
the second of them an `attached: true` carrying a `context_ref` that ServerOS
had never minted and never held. `serverosctl work delegate`'s own help says
that ref has exactly one producer, `agent.context.attach` inside a real turn.
A log any test run can append ATTACHED to is worse than no log: the file can no
longer tell a reader which of its lines describe a turn, and the whole reason
this record exists is that a turn is otherwise unobservable from outside one.

Two mechanisms here, because one of them can be forgotten and the other cannot:

  * every test gets `ingress_record_path` redirected to its own `tmp_path`, so
    the ordinary case writes only into the test's own directory; and
  * the session records the real file's size and mtime up front and fails at
    teardown if either moved, so a test that reaches the log by some path the
    first mechanism does not cover turns into a loud error rather than one more
    plausible line in an evidence file.

The first is hygiene. The second is the one that matters: it is a check that a
later edit cannot quietly opt out of, and it is stated here rather than in each
test because a test that has to remember is exactly the test that will not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: The name `tests/test_actor_context.py` loads `mcp/human_control.py` under.
#: Named rather than re-derived: `runtime/wake.py`'s loader comment records what
#: two names for one file cost, and the same applies to looking one up.
HUMAN_CONTROL_MODULE = "serveros_mcp_human_control"

#: The record's own ref, spelled here rather than imported, because importing
#: the module at conftest time is what the fixture below exists to avoid.
INGRESS_RECORD_REF = "logs/human-control/ingress.jsonl"


def _real_ingress_path() -> Path | None:
    """Where this node would write the ingress record, or None if unbound.

    Resolved here through `runtime/paths.py` rather than by asking the tool, and
    that is a correction rather than a preference. Asking the tool meant asking
    a module the suite may not have imported yet, so `before` was None for any
    run that did not happen to collect `test_actor_context.py` — and a baseline
    of None skipped the check. Measured 2026-09-28 with a one-file probe that
    deliberately bypassed the redirect: it appended to the real record and the
    run still passed, which is a guard that reports on the tests it was already
    safe for. Loading the contract directly gives every run a baseline.

    An unbound node is not an error: it is a node where the tool itself refuses
    to write, so there is nothing to protect.
    """
    try:
        paths = _load_paths()
        resolved = paths.Paths()
        if not resolved.bound:
            return None
        return Path(resolved.resolve(INGRESS_RECORD_REF)["path"])
    except Exception:
        return None


def _fingerprint(path: Path) -> tuple:
    """What a reader would have to compare to notice this file changed.

    `st_mtime_ns` and `st_size` together; a write of zero bytes moves the first
    and not the second, and the record is appended to, so a same-second write is
    the ordinary case rather than a corner. An absent file is its own value
    rather than `None`, so CREATING the record is caught and not read as
    "nothing to compare".
    """
    if not path.exists():
        return ("absent",)
    st = path.stat()
    return (st.st_size, st.st_mtime_ns)


def _load_paths():
    """`runtime/paths.py` under the name its other loaders use.

    One name, not two: two module objects for one file is the failure
    `runtime/wake.py` records for its own loader, and a second copy here would
    also mean this file could hold a `Paths` the tool never consults.
    """
    import importlib.util

    name = "serveros_runtime_paths"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "runtime" / "paths.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: A test that is ABOUT the resolver asks for this marker.
#:
#: `test_the_record_path_comes_from_the_root_contract` calls
#: `ingress_record_path` to assert what it returns, so redirecting that
#: function would replace the thing under test with the fixture and the test
#: would then pass or fail for the wrong reason. It writes no record — it only
#: resolves — so exempting it costs the guard nothing. Named as a marker rather
#: than left to a name check, because an opt-out that a reader can see on the
#: test is one they can tell from an oversight.
RESOLVER_MARKER = "reads_the_ingress_resolver"


#: Set by `scripts/hermetic_tests.py`: the private directory its roots live in.
HERMETIC_WORK_ROOT = "SERVEROS_HERMETIC_WORK_ROOT"


def _give_this_xdist_worker_its_own_roots() -> None:
    """Under `pytest -n`, rebind every node root to this worker's own copy.

    The hermetic runner binds one set of throwaway roots before pytest starts,
    and xdist workers inherit it — so two workers shared one daemon socket
    under `XDG_RUNTIME_DIR` and one state directory, and tests that pass alone
    failed together. Each worker now prepares its own set through the same
    seam the runner used. Outside the hermetic runner, or without xdist,
    nothing changes.
    """
    import importlib.util
    import os

    worker = os.environ.get("PYTEST_XDIST_WORKER")
    work_root = os.environ.get(HERMETIC_WORK_ROOT)
    if not worker or not work_root:
        return
    root = Path(work_root) / worker
    if root.exists():
        return
    root.mkdir(mode=0o700)
    spec = importlib.util.spec_from_file_location(
        "serveros_hermetic_runner", REPO / "scripts" / "hermetic_tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    os.environ.update(runner.bind_roots(root))


def pytest_configure(config):
    _give_this_xdist_worker_its_own_roots()
    config.addinivalue_line(
        "markers", f"{RESOLVER_MARKER}: this test exercises the record's "
                   "resolver and must see the real function")


@pytest.fixture(autouse=True)
def _ingress_record_writes_into_the_test(request, tmp_path, monkeypatch):
    """Point the record at this test's own directory.

    Patched on the already-loaded module rather than by importing it: a test
    module is imported at collection, before any fixture runs, and importing it
    here a second way would give the suite two module objects for one file —
    the failure `runtime/wake.py` spells out for its own loader.
    """
    if request.node.get_closest_marker(RESOLVER_MARKER) is not None:
        yield
        return
    module = sys.modules.get(HUMAN_CONTROL_MODULE)
    if module is None:
        yield
        return
    target = tmp_path / "ingress.jsonl"
    monkeypatch.setattr(module, "ingress_record_path",
                        lambda ref=INGRESS_RECORD_REF: target)
    yield


@pytest.fixture(scope="session", autouse=True)
def _the_nodes_ingress_record_is_not_written_by_this_run():
    """Fail the run if anything reached the real record anyway.

    `st_mtime_ns` and `st_size` together, because either alone misses a change:
    the record is appended to, so a same-second write is the common case and
    mtime alone can be too coarse to see it.
    """
    before = None
    path = _real_ingress_path()
    if path is not None:
        before = _fingerprint(path)

    yield

    # A node whose roots are UNBOUND has no record and nothing to protect; the
    # tool refuses to write there too. That is the only case this skips, and it
    # is a fact about the node rather than about which tests happened to run.
    if before is None:
        return
    path = _real_ingress_path()
    if path is None:
        return
    after = _fingerprint(path)
    assert after == before, (
        f"this test run wrote {path}. The ingress record is evidence about real "
        f"turns, and `serverosctl work delegate` names its only producer as "
        f"`agent.context.attach` inside one. Before: {before}, after: {after}. "
        f"A test that appends to it does not merely litter: it puts a line in a "
        f"file whose reader cannot afterwards tell that line from a turn.")
