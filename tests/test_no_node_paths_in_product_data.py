"""A shipped module must not carry one node's absolute path as LIVE DATA.

WHY THIS IS NOT `test_no_node_absolute_paths.py`. That file scans TEXT and is
scoped to `skills/`, and it excludes `runtime/*.py` for a reason it states: those
files carry ARCHAEOLOGY -- comments quoting what a past revision did wrong --
and "rewriting it would delete the evidence". The reason is right about the
comments and false about the data. `runtime/workbench_sources.py` holds `PINS`,
`SAMPLES` and `DEPLOYMENT`, which are LIVE TABLES the renderer reads, and 13 of
their values were this machine's absolute paths -- under a header that says the
opposite in the module's own words:

    a distribution must not carry the path of the working copy it happened to be
    built in

A TEXT scanner cannot tell a comment from a value, so the only scope a text
scanner can honestly take is one where no archaeology lives -- which is exactly
what that file did. That is not a defect in it; it is the reason this file
exists. The rule needed an instrument that sees values, and the text scanner
cannot be that instrument at any scope.

NOR CAN A VALUE COMPARISON. Replacing a literal with a derivation from a root
that holds the same value produces the SAME STRING -- measured, on this node,
across all 23 rows of the three tables: zero differences. So "the values did not
change" is what both a correct fix and no fix at all produce, and any check that
reads values is blind to this defect by construction. (The same lesson arrived
in W6 from the other direction: a check that compares values could not see a row
that was computed rather than read, because the two coincided.)

THE INSTRUMENT IS AN AST, AND THAT IS THE WHOLE POINT. `ast.Constant` nodes are
values; comments and docstrings are not values, and a bare string expression --
a module docstring, a block of prose -- is excluded by the walk. So the scanner
needs no archaeology exemption, no `node-path-ok` marker and no directory
allowlist: prose is invisible to it by construction rather than by a list of
paths somebody has to keep current. `TheControlTests` pins exactly that
discrimination, because an instrument that cannot fail on the right input and
pass on the wrong one cannot be used to conclude either way.

WHAT IS STILL HERE, AND WHY. `KNOWN` names the files that still carry a node path
as live data, with the reason, so that the count is a DECLARATION rather than a
silence. A file that stops offending fails this test until its entry is removed;
a file that starts fails it by name. That is the shape W6 settled on for the
cross-copy gate, for the reason it settled on it there: an intersection nobody
declares is a difference nobody can see.

IT IS EMPTY NOW, AND THAT IS THE READING RATHER THAN THE ABSENCE OF ONE. The one
entry it carried was `runtime/workspace.py`, whose `SCRATCH_SURFACES` held
`/data/scratch/vince` with a `provenance` string naming where the value came
from -- a CLAIM about the contract standing in for a read of it. The entry said
it would go away "when the root does"; what it actually took was an accessor:
`runtime/paths.py` now serves a name the AUTHORITY binds even when it is in
neither the root vocabulary nor the semantic roles, and the scratch area is read
through it. Emptying this table is the consumer's result, not a tidier table.
"""
import ast
import pathlib
import re
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]

#: A path belonging to one machine, matched only at a real path start.
NODE_PATH = re.compile(
    r"(?<![\w./-])(?:/home|/opt/applications|/data/home|/data/scratch|/Users)/[A-Za-z0-9_.-]+"
)

SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "node_modules"})

#: `path -> (how many, why it is still there)`. Empty is the goal; an entry is a
#: record that somebody looked and decided, not a place to put a file until the
#: scanner is quiet. It is empty as of 2026-10-09, and the entry it held is
#: described in the module docstring rather than deleted quietly: the file that
#: was in it stopped offending because the value it carried became a read.
KNOWN: dict[str, tuple[int, str]] = {}


class _BareStrings(ast.NodeVisitor):
    """Ids of Constant nodes used as statements: docstrings and prose blocks."""

    def __init__(self) -> None:
        self.ids: set[int] = set()

    def visit_Expr(self, node: ast.Expr) -> None:
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self.ids.add(id(node.value))
        self.generic_visit(node)


def node_paths_in_source(source: str, filename: str = "<mem>") -> list[tuple[int, str]]:
    """`(line, path)` for every VALUE in the source that names one node.

    Comments are invisible (they are not in the tree at all), docstrings and
    bare prose strings are excluded by `_BareStrings`, and an implicit
    concatenation across lines is one value because the parser already joined
    it -- which is how the six `SAMPLES` rows were written.
    """
    # `dedent` so a fragment is written the way it appears inside a file; on a
    # whole module it is a no-op, because no line shares a leading prefix.
    tree = ast.parse(textwrap.dedent(source), filename=filename)
    bare = _BareStrings()
    bare.visit(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in bare.ids):
            match = NODE_PATH.search(node.value)
            if match:
                found.append((node.lineno, match.group(0)))
    return found


def census(root: pathlib.Path) -> tuple[dict[str, int], int]:
    """`(offenders by relative path -> count, how many files were parsed)`."""
    offenders: dict[str, int] = {}
    parsed = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "tests" or SKIP_DIRS & set(rel.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            hits = node_paths_in_source(source, str(rel))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        parsed += 1
        if hits:
            offenders[str(rel)] = len(hits)
    return offenders, parsed


class TheControlTests(unittest.TestCase):
    """The scanner has to fail on a value and pass on the same words in prose."""

    def test_it_catches_a_node_path_held_as_a_value(self):
        for line in (
            '        repo="/data/home/vince/workspace/worktrees/x",\n',
            'DATA_ROOT = "/opt/applications/data/home-hwz"\n',
            '    dict(path="/data/scratch/vince/y"),\n',
            "HOME = '/Users/someone'\n",
        ):
            with self.subTest(line=line.strip()):
                found = node_paths_in_source(line)
                self.assertEqual(len(found), 1, f"not caught: {line!r}")
                self.assertTrue(found[0][1].startswith("/"), found)

    def test_it_does_not_fail_on_the_same_words_in_prose(self):
        """The discrimination that lets this scanner skip no directory and need
        no marker: archaeology is not a value, so it is not a finding."""
        source = (
            '"""A module docstring naming /data/home/vince/workspace."""\n'
            "# a comment naming /data/home/vince/workspace\n"
            "def f():\n"
            '    """A function docstring naming /data/scratch/vince."""\n'
            "    return 1\n"
        )
        self.assertEqual(node_paths_in_source(source), [])

    def test_an_ASSIGNED_string_is_a_value_and_is_caught(self):
        """Where the boundary actually is, stated rather than assumed.

        The scanner excludes a string used as a STATEMENT (a docstring, a bare
        prose block) and a comment. It does NOT exclude a string assigned to a
        name, because that is a value -- and the scanner cannot tell a prose
        paragraph stored in a variable from data stored in one without guessing
        at intent. So it reports it, which is the safe direction: on this tree
        the census produced two files and neither was a false positive, so the
        alternative -- guessing which values are prose -- would buy nothing and
        cost a rule nobody could state.
        """
        found = node_paths_in_source('y = """prose naming /data/scratch/vince"""\n')
        self.assertEqual(len(found), 1, found)

    def test_it_joins_an_implicit_concatenation_into_one_value(self):
        """How the six `SAMPLES` rows were written: two lines, one value."""
        found = node_paths_in_source('p = ("/data/home/vince/artifacts/skills/"\n'
                                     '     "serveros/RUN-x/y")\n')
        self.assertEqual(len(found), 1, found)


class TheProductDataNamesNoNodeTests(unittest.TestCase):
    def test_the_scan_actually_reached_files(self):
        """An empty scan and a scan that read nothing are the same output.

        `test_no_node_absolute_paths.py` shipped with a check that walked a
        directory that did not exist and reported clean, so this asserts the
        count rather than trusting the empty list.
        """
        _, parsed = census(ROOT)
        self.assertGreaterEqual(parsed, 100, "the census read too few files to mean anything")

    def test_product_data_names_a_node_only_where_it_is_declared(self):
        offenders, _ = census(ROOT)
        declared = {rel: count for rel, (count, _) in KNOWN.items()}
        self.assertEqual(
            offenders, declared,
            "these differ from the declaration: a file that stopped offending "
            "must lose its entry, and a new one must be named here with a reason")

    def test_every_declaration_gives_a_reason_and_a_count(self):
        for rel, (count, reason) in KNOWN.items():
            with self.subTest(rel=rel):
                self.assertIsInstance(count, int)
                self.assertGreater(count, 0)
                self.assertGreater(len(reason.strip()), 60, "the absence is not explained")
                self.assertTrue((ROOT / rel).exists(), f"{rel} is declared but absent")


class TheDerivationFollowsTheRootTests(unittest.TestCase):
    """`_under` reads the contract; it does not fall back to a plausible path."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import sys
        runtime = str(ROOT / "runtime")
        if runtime not in sys.path:
            sys.path.insert(0, runtime)
        name = "workbench_sources_under_test"
        # Read `sys.modules` before writing to it: every module in this tree is
        # loaded under a synthetic name, so a second writer takes the name from
        # the first, and each copy carries its own classes. `test_loader_identity`
        # is the gate that says so, and it caught this file doing it the other way.
        module = sys.modules.get(name)
        if module is None:
            spec = importlib.util.spec_from_file_location(
                name, ROOT / "runtime" / "workbench_sources.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        cls.mod = module

    def setUp(self):
        self.mod._RESOLVED.clear()

    def test_it_returns_the_root_plus_the_position(self):
        from pathlib import Path
        import paths as paths_mod
        got = Path(self.mod._under("workspace", "worktrees", "quay", "foundation-skeleton"))
        self.assertEqual(got, paths_mod.Paths().root("workspace") / "worktrees"
                         / "quay" / "foundation-skeleton")
        self.assertTrue(got.is_absolute(), got)

    def test_it_refuses_a_root_no_contract_declares(self):
        """The value this replaced was a literal with no such guard: a typo in a
        root NAME would have been a plausible-looking path rather than an error."""
        import paths as paths_mod
        with self.assertRaises(paths_mod.PathRefused):
            self.mod._under("worktress", "quay")

    def test_the_runtime_root_is_asked_as_a_role_not_a_root(self):
        """`runtime-assets` is the ROOT, and the artifact the release rows name
        is a DISTRIBUTION inside it — `runtime-assets/hwz-server-os`, which is
        what the role resolves to. The two doors now agree about the parent and
        differ one level down, which is the difference this test asserts.

        It used to read differently: the root resolved to
        `{DATA_ROOT}/runtime-assets/serveros`, which nothing on this node has
        ever been, so the role was the only door that answered correctly.
        Asking the role was right; leaving the root wrong was RUN-0056's fix."""
        from pathlib import Path
        import paths as paths_mod
        got = Path(self.mod._under_role("runtime.serveros-distribution"))
        self.assertEqual(got, Path(paths_mod.Paths().role("runtime.serveros-distribution")["path"]))
        self.assertNotEqual(got, paths_mod.Paths().root("runtime-assets"))


if __name__ == "__main__":
    unittest.main()
