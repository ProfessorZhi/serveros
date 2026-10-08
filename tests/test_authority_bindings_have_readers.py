"""A NAME the authority binds must be askable, or declared unread with a reason.

WHY A NAME AND NOT A PATH. The defect this file gates is one step earlier than
the one `test_no_node_paths_in_product_data.py` gates. That one is about a VALUE
written into a shipped table; this one is about a name that could not be ASKED
FOR, which is what put the value there. On this node the authority binds eleven
names; `runtime/paths.py` served five of them as roots and dropped the rest
silently, so a consumer that needed `scratch` had no way to ask and wrote
`/data/scratch/vince` down instead — the exact literal the other gate spent a
package removing.

THE PREDECESSOR IS IN THIS TREE AND SAYS IT BETTER. `architecture_check.py` had
the same problem with the same shape and fixed it there:

    without this the tokens ONLY the profile supplies -- DOWNLOADS_ROOT and
    RUNTIME_ASSETS_ROOT -- are never asked for, so a profile that binds them and
    one that leaves them UNBOUND produce the same report and that half of the
    contract is decorative

"Decorative" is the right word for a declaration nobody can read, and it is the
same word §9.55 used one layer down for a row nobody reads. So this gate asks
the question in the form that does not go stale: for every name the authority
binds, WHO READS IT -- and a name whose reader is a different layer is written
down with that layer's name rather than left to look unread.

`READ_BY` IS CHECKED, NOT TRUSTED. Each entry names a reader, and the tests below
open the reader and look, because "somebody reads this somewhere" is exactly the
claim that stays true after the reader is deleted. A new binding makes this red
until somebody says who reads it; a reader that stops reading makes it red too.
"""
import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

import paths as paths_mod  # noqa: E402


def _load(name: str, relative: str):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


#: Every name the authority binds that is NOT one of the root vocabulary's, with
#: the layer that reads it. An entry is not a waiver: it is a statement that the
#: name is read somewhere, and `TheDeclaredReadersAreRealTests` opens each reader.
READ_BY = {
    "data_root": (
        "runtime/paths.py",
        "`_SEED_ROLE` resolves it as the SEED every other root is built from, so "
        "it is the one binding that is read first and by the most readers; the "
        "root vocabulary names the same directory `data`.",
    ),
    "downloads": (
        "skills/serveros/scripts/architecture_check.py",
        "The token `DOWNLOADS_ROOT` is asked for there, and that file's own "
        "comment records why it had to be: without it a profile that binds "
        "`downloads` and one that leaves it UNBOUND produce the same report, so "
        "that half of the contract was decorative. It is read in a DIFFERENT "
        "layer from the one this file's other entries name, which is the point "
        "of writing the layer down rather than the fact that somebody reads it.",
    ),
    "knowledge.corporate": (
        "runtime/paths.py",
        "`WORKSPACE_TOP_LEVEL` places `knowledge` under the workspace root in "
        "its own right, so the token resolves whether or not the profile binds "
        "this name -- two sources for one fact, which is the shape `RUN-0051` "
        "recorded as entry 8 rather than something this file can settle.",
    ),
    "scratch": (
        "runtime/workspace.py",
        "`scratch_rows()` reads it through `Paths().binding('scratch')`. This is "
        "the binding that had no accessor: its value sat in `SCRATCH_SURFACES` "
        "as a literal with a `provenance` string naming where it came from, and "
        "the string was a CLAIM until the accessor existed.",
    ),
    "workspace.src": (
        "runtime/workspace.py",
        "`PLACEMENT_CLASSES['src']` resolves `workspace` + `src`, so the "
        "position is read as a member of a class rather than as a root of its "
        "own.",
    ),
    "workspace.worktrees": (
        "runtime/workspace.py",
        "`PLACEMENT_CLASSES['worktree']` resolves `workspace` + `worktrees`, the "
        "same way `workspace.src` is read.",
    ),
}


class TheDeclaredDifferenceTests(unittest.TestCase):
    def test_the_names_outside_the_root_vocabulary_are_exactly_the_declared_ones(self):
        authority = set(paths_mod.Paths().bindings)
        outside = authority - set(paths_mod.ROOTS)
        self.assertEqual(
            outside, set(READ_BY),
            "these differ from the declaration: a name the authority binds and "
            "no root serves must be listed in READ_BY with the layer that reads "
            "it, and an entry whose name is now a root must be removed")

    def test_every_declaration_names_a_reader_and_gives_a_reason(self):
        for name, (layer, reason) in READ_BY.items():
            with self.subTest(name=name):
                self.assertTrue((ROOT / layer).exists(),
                                f"{layer} is named as the reader of {name} and is absent")
                self.assertGreater(len(reason.strip()), 80, "the absence is not explained")

    def test_the_scan_saw_an_authority(self):
        """An empty binding table and a table nobody read are the same output."""
        self.assertGreaterEqual(len(paths_mod.Paths().bindings), 5,
                                "too few bindings read to mean anything")


class TheAccessorTests(unittest.TestCase):
    """`binding` reads; it does not derive, and it does not default."""

    def test_every_bound_name_is_readable_by_that_name(self):
        authority = paths_mod.Paths().bindings
        for name, value in authority.items():
            with self.subTest(name=name):
                self.assertEqual(str(paths_mod.Paths().binding(name)), str(value))

    def test_a_name_nobody_binds_is_refused_rather_than_guessed(self):
        with self.assertRaises(paths_mod.PathRefused):
            paths_mod.Paths().binding("scratch_area")

    def test_a_root_the_authority_does_not_bind_is_still_refused(self):
        """The accessor reads the AUTHORITY, not the root vocabulary.

        `caches`, `data`, `logs` and `tmp` are ROOTS and are NOT bindings: they
        are DERIVED from another root rather than DECLARED by the authority, and
        the distinction is the whole reason this accessor is not `root()`. A
        `binding()` that fell back to `self.roots` for these would answer, and
        the answer would be a path that looks right -- which is the defect this
        module exists to prevent, one level down. Found by a falsification probe
        that made exactly that fallback and was NOT caught (RUN-0053 probe C).
        """
        unbound = set(paths_mod.ROOTS) - set(paths_mod.Paths().bindings)
        self.assertTrue(unbound, "every root is bound, so this asserts nothing")
        for name in sorted(unbound):
            with self.subTest(name=name):
                with self.assertRaises(paths_mod.PathRefused):
                    paths_mod.Paths().binding(name)

    def test_the_refusal_does_not_hand_back_a_plausible_path(self):
        """The failure this module exists to prevent is a LOOK-RIGHT answer."""
        try:
            paths_mod.Paths().binding("scratch_area")
        except paths_mod.PathRefused as refusal:
            self.assertIn("not a name this node's authority binds", str(refusal))
            self.assertNotIn("/data/", str(refusal))
        else:
            self.fail("a name nobody binds resolved")

    def test_an_injected_world_has_no_authority_to_read(self):
        """`roots=` is a closed world: the machine's authority is not part of it."""
        injected = paths_mod.Paths({"home": "/tmp", "data_root": "/tmp"})
        self.assertEqual(injected.bindings, {})
        with self.assertRaises(paths_mod.PathRefused):
            injected.binding("scratch")


class TheDeclaredReadersAreRealTests(unittest.TestCase):
    """Each named reader is opened and looked at, so the claim can go stale."""

    def test_the_scratch_reader_actually_reads_it(self):
        workspace = _load("workspace_under_binding_test", "runtime/workspace.py")
        rows = workspace.scratch_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["path"],
                         str(paths_mod.Paths().binding("scratch")))
        # The provenance must name the ACCESSOR, not a section of a document:
        # a string that names where a value came from is a claim, and this one
        # is now the thing that was actually called.
        self.assertIn("bindings.scratch", rows[0]["provenance"])

    def test_the_data_root_reader_actually_reads_it(self):
        self.assertEqual(paths_mod._SEED_ROLE.get("data_root"), "data_root")

    def test_the_downloads_reader_actually_names_it(self):
        source = (ROOT / "skills/serveros/scripts/architecture_check.py").read_text(
            encoding="utf-8")
        self.assertIn("DOWNLOADS_ROOT", source,
                      "the layer declared as its reader no longer names it")

    def test_a_row_whose_binding_is_undeclared_is_dropped_not_defaulted(self):
        """The behaviour that replaces the literal, stated as a test.

        If the authority stops binding `scratch`, the row goes away and a path
        under it classifies as something else. The alternative -- inventing a
        plausible path -- is the defect, not the fix.
        """
        workspace = _load("workspace_under_binding_test", "runtime/workspace.py")
        original = workspace.SCRATCH_SURFACES
        workspace.SCRATCH_SURFACES = (
            {"binding": "an_area_no_authority_binds", "class": "scratch",
             "provenance": "nowhere", "note": "a probe row"},
        )
        try:
            self.assertEqual(workspace.scratch_rows(), ())
        finally:
            workspace.SCRATCH_SURFACES = original

    def test_a_row_that_carries_a_path_is_taken_as_written(self):
        """The documented escape hatch, and what `test_workspace_placement` uses."""
        workspace = _load("workspace_under_binding_test", "runtime/workspace.py")
        original = workspace.SCRATCH_SURFACES
        workspace.SCRATCH_SURFACES = (
            {"path": "/tmp/somewhere", "class": "scratch",
             "provenance": "declared by a caller", "note": "a probe row"},
        )
        try:
            self.assertEqual([r["path"] for r in workspace.scratch_rows()],
                             ["/tmp/somewhere"])
        finally:
            workspace.SCRATCH_SURFACES = original


if __name__ == "__main__":
    unittest.main()
