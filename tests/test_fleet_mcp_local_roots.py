"""This surface keeps its own files, and does not write into a root it does not own.

The rule being checked is not a preference. `~/.serveros` admits five named
files and `runtime/paths.py#admit` refuses a write naming anything else; its own
stated remedy is "move it rather than widen this list". A surface that shipped a
default binding path inside that root would be printing an operator a path the
product then refuses — the exact failure `fleet/node-root-contract.yaml` records
having already happened once.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from fleet_mcp import local_roots as lr

REPO = Path(__file__).resolve().parents[1]


class TheOwnedRoots(unittest.TestCase):
    def test_the_binding_root_is_not_inside_the_closed_serveros_root(self):
        closed = Path(lr.CLOSED_ROOT).expanduser().resolve()
        root = lr.binding_root().resolve()
        self.assertFalse(
            str(root).startswith(str(closed) + os.sep),
            f"{root} is inside {closed}",
        )

    def test_the_state_root_is_not_inside_the_closed_serveros_root(self):
        closed = Path(lr.CLOSED_ROOT).expanduser().resolve()
        self.assertFalse(str(lr.state_root().resolve()).startswith(str(closed) + os.sep))

    def test_binding_and_state_are_separate_directories(self):
        """A binding is meant to be re-derivable; runtime state is not.

        Mixing them is how a disposable binding becomes a file nobody dares
        delete, which is how it becomes an authority by accident.
        """
        self.assertNotEqual(lr.binding_root(), lr.state_root())
        self.assertFalse(
            str(lr.binding_root()).startswith(str(lr.state_root()) + os.sep)
        )
        self.assertFalse(
            str(lr.state_root()).startswith(str(lr.binding_root()) + os.sep)
        )

    def test_the_binding_root_is_not_inside_a_repository(self):
        """`node-root-contract.yaml#root.inside_a_repository: forbidden`.

        Read from git rather than from a path prefix, because the point is that
        the file would be *commit-able*, not that it happens to sit under the
        checkout directory today.
        """
        root = lr.binding_root().resolve()
        inside = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root if root.exists() else REPO,
            capture_output=True, text=True,
        )
        if inside.returncode == 0 and inside.stdout.strip():
            top = Path(inside.stdout.strip()).resolve()
            # `root` may not exist yet; walk upward to the nearest real parent.
            probe = root
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
            self.assertFalse(
                str(probe.resolve()).startswith(str(top) + os.sep),
                f"{probe} is inside the checkout {top}",
            )

    def test_the_two_config_kinds_look_in_their_own_root(self):
        self.assertEqual(lr.binding_candidates("fleet-mcp.local.yaml"),
                         (lr.binding_root() / "fleet-mcp.local.yaml",))
        self.assertEqual(lr.binding_candidates("fleet-node.local.yaml"),
                         (lr.binding_root() / "fleet-node.local.yaml",))

    def test_the_working_directory_is_not_a_place_a_binding_is_found(self):
        """A binding that is picked up from the cwd makes "which devices does
        this runtime reach" depend on where a process was started."""
        candidates = lr.binding_candidates("x.yaml")
        self.assertNotIn(Path.cwd() / "x.yaml", candidates)
        self.assertEqual(len(candidates), 1)

    def test_the_home_override_relocates_both_roots(self):
        for key, value in (("binding_root", "bindings"), ("state_root", "state")):
            handle = os.environ.get("FLEET_MCP_HOME")
            os.environ["FLEET_MCP_HOME"] = os.path.join(os.sep, "tmp", "fleet-mcp-test")
            try:
                self.assertEqual(getattr(lr, key)(), Path("/tmp/fleet-mcp-test") / value)
            finally:
                if handle is None:
                    os.environ.pop("FLEET_MCP_HOME", None)
                else:
                    os.environ["FLEET_MCP_HOME"] = handle


class TheClosedRootRefusal(unittest.TestCase):
    def test_a_binding_in_the_closed_root_is_refused_by_name(self):
        message = lr.refusal_if_closed_root(Path(lr.CLOSED_ROOT) / "fleet-mcp.local.yaml")
        self.assertIsNotNone(message, "the closed root admitted a binding")
        assert message is not None
        self.assertIn("node-root-contract.yaml", message)
        self.assertIn("move it rather than widen this list", message)
        self.assertIn(str(lr.binding_root()), message)

    def test_the_five_admitted_names_are_not_refused(self):
        for name in lr.CLOSED_ROOT_ALLOWED:
            with self.subTest(name=name):
                self.assertIsNone(lr.refusal_if_closed_root(Path(lr.CLOSED_ROOT) / name))

    def test_an_ordinary_path_is_not_refused(self):
        self.assertIsNone(lr.refusal_if_closed_root(lr.binding_root() / "any.yaml"))
        self.assertIsNone(lr.refusal_if_closed_root("/tmp/elsewhere/fleet-mcp.local.yaml"))

    def test_the_allowed_list_matches_the_contract_it_quotes(self):
        """Transcribed names drift; this is the assertion that they have not.

        The names live in three places — `runtime/paths.py`, the node-root
        contract, and this module — and a copy that falls behind is a refusal
        that fires on a legitimate file.
        """
        import yaml

        contract = yaml.safe_load(
            (REPO / "fleet" / "node-root-contract.yaml").read_text(encoding="utf-8")
        )
        declared = tuple(contract["files"]) + tuple(contract["declarations"])
        self.assertEqual(tuple(lr.CLOSED_ROOT_ALLOWED), declared)

    def test_paths_py_agrees_with_the_contract(self):
        sys.path.insert(0, str(REPO))
        try:
            from runtime import paths
        except ImportError:  # pragma: no cover - the module is in this repo
            self.skipTest("runtime.paths not importable")
        self.assertEqual(tuple(paths.HOME_DOT_SERVEROS_ALLOWED), tuple(lr.CLOSED_ROOT_ALLOWED))


if __name__ == "__main__":
    unittest.main()
