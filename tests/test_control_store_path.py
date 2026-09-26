#!/usr/bin/env python3
"""One state root, two compositions, one file.

WHY THIS FILE EXISTS
--------------------
`daemon.py` writes the agent-control store at
`default_state_dir()/agent-control/store.json`; `cli.py`'s
`_agent_control_store_path` reads it. `default_state_dir` honoured
`SERVEROS_STATE_DIR` and the CLI read the path contract and never looked at the
variable, so those two composed the same path from two different answers.

The disagreement is invisible on a node that does not export the variable,
because `registry.state_root()` falls back to the contract's answer and both
land on the same directory. IT IS NOT INVISIBLE ON A NODE THAT DOES — and every
node `scripts/release.py continuation-wake --state-dir <path>` installs on
exports it, because the unit the installer renders carries
`Environment=SERVEROS_STATE_DIR=<path>`. On such a node the daemon writes one
store, every verb reads another, and both answer as if the other did not exist:
the daemon's delegations and the CLI's delegations are two histories, and
`work continuations` reports «no Parent has checkpointed on this node» about a
node that has.

Found by installing the wake routine against an explicit state root and watching
the receiver read an empty store. The tests below assert the property that was
missing rather than the incident: the daemon's writer path and the CLI's reader
path are the SAME FILE, with the variable set and without it. The first test
also pins that the consolidation changed nothing when the variable is unset,
which is the only reason a fix like this is safe to make on a live node.

Hermetic: environment variables and imports only. Nothing here writes a store.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The names the product loads these under, and `tests/test_daemon.py` uses the
# same two, so what this file reads is the module the node runs.
registry_mod = _load("serveros_runtime_registry", RUNTIME / "registry.py")
paths_mod = _load("serveros_runtime_paths", RUNTIME / "paths.py")
daemon_mod = _load("serveros_runtime_daemon", RUNTIME / "daemon.py")
# `runtime/cli.py` is not importable as a module (it is a program that parses
# argv at the bottom), so its function is read out of the source instead — the
# same technique `tests/test_wake.py` uses for the daemon's tick. The assertion
# below is stronger than «the source says this», because the expression it reads
# is CALLED and its answer compared to the daemon's.
CLI_SOURCE = (RUNTIME / "cli.py").read_text()


def cli_store_path() -> Path:
    """`_agent_control_store_path`, evaluated rather than imported.

    Built from the same module objects the file's own globals hold: the body
    is `registry_mod.state_root() / "runtime" / "agent-control" / "store.json"`,
    and it is written out here rather than scraped so that a change to the
    composition fails THIS test by name instead of passing it by accident.
    """
    body = CLI_SOURCE.split("def _agent_control_store_path() -> Path:", 1)
    assert len(body) == 2, "cli.py no longer defines _agent_control_store_path"
    tail = body[1].split("\n\n\n", 1)[0]
    assert 'registry_mod.state_root() / "runtime" / "agent-control" / "store.json"' \
        in tail, (
        f"the store path is composed some other way than from the one state "
        f"root, so the daemon and this verb can disagree again:\n{tail}")
    return registry_mod.state_root() / "runtime" / "agent-control" / "store.json"


def daemon_store_path() -> Path:
    """The file the daemon writes, composed the way the daemon composes it."""
    return daemon_mod.default_state_dir() / "agent-control" / "store.json"


class TestOneStateRoot:
    """The two compositions, and the one answer they have to share."""

    def test_with_no_override_both_land_where_the_contract_says(
            self, monkeypatch):
        """Unset is the case every node but an installed routine is in.

        It is also the case that must not move: the consolidation below is only
        safe because this path is unchanged, and a reader who wants to know what
        the fix did can see it here — nothing, when nothing is exported.
        """
        monkeypatch.delenv("SERVEROS_STATE_DIR", raising=False)
        contract = Path(paths_mod.Paths().resolve("state/runtime/agent-control")["path"])
        assert cli_store_path() == contract / "store.json"
        assert daemon_store_path() == contract / "store.json"
        assert registry_mod.state_root() == Path(
            paths_mod.Paths().root("state"))

    def test_with_an_override_both_still_land_on_the_same_file(
            self, monkeypatch, tmp_path):
        """The property that was missing, stated as equality and not as prose.

        A node that exports the variable — which is what the wake routine's own
        unit file does — must not have one store for its writer and another for
        its readers.
        """
        root = tmp_path / "state" / "serveros"
        monkeypatch.setenv("SERVEROS_STATE_DIR", str(root))
        assert cli_store_path() == daemon_store_path()
        assert cli_store_path() == root / "runtime" / "agent-control" / "store.json"

    def test_the_registry_document_is_under_the_same_root(self, monkeypatch,
                                                          tmp_path):
        """Three consumers of one fact, and this is the third.

        `--registry` defaults to the state root's `runtime/registry/`, so a node
        that pointed its state somewhere else and kept reading the registry from
        the contract's answer would be reading a document about a different
        node's resources.
        """
        root = tmp_path / "state" / "serveros"
        monkeypatch.setenv("SERVEROS_STATE_DIR", str(root))
        assert registry_mod.default_path() == root / "runtime" / "registry" / "resources.yaml"
        assert registry_mod.state_root() == root

    def test_the_expression_is_written_once(self):
        """And no module recomposes it from the two literals.

        The daemon and the CLI both reach the answer through
        `registry.state_root()`; a third copy is how the first two drifted. The
        scan is over the modules that compose node paths, and it looks for the
        environment read rather than for the function call, because the call is
        the thing being asked for and the read is the thing that would be a
        second producer.
        """
        offenders = []
        for name in ("daemon.py", "cli.py"):
            for number, line in enumerate((RUNTIME / name).read_text().splitlines(), 1):
                if 'os.environ.get("SERVEROS_STATE_DIR")' in line \
                        or "os.environ.get(STATE_DIR_ENV)" in line:
                    offenders.append(f"{name}:{number}")
        assert offenders == [], offenders


class TestTheUnitTheInstallerRendersActuallyRedirectsIt:
    """The reason the defect mattered, asserted where an operator would see it.

    `scripts/release.py continuation-wake --state-dir X` renders
    `Environment=SERVEROS_STATE_DIR=X` into the unit. That line is a promise
    about where the routine reads and writes, and it is only kept if the verb
    the unit runs honours the variable. This test does not re-render the unit —
    `tests/test_release.py` pins the rendering — it pins that the promise
    resolves to the file the daemon writes.
    """

    def test_the_rendered_variable_names_the_store_the_verb_reads(
            self, monkeypatch, tmp_path):
        root = tmp_path / "exported-by-the-unit"
        monkeypatch.setenv("SERVEROS_STATE_DIR", str(root))
        assert cli_store_path() == root / "runtime" / "agent-control" / "store.json"

    def test_and_a_verb_run_without_it_reads_the_nodes_own(self, monkeypatch):
        """The other half: an unset variable is not a second, absent store."""
        monkeypatch.delenv("SERVEROS_STATE_DIR", raising=False)
        assert cli_store_path() == daemon_store_path()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
