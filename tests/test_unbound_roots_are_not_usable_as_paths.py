"""A root this node does not bind must not be handed back as a place to write.

THE DEFECT, MEASURED RATHER THAN ARGUED. `Paths.root()` returned the contract's
template for an unbound root — `{WORKSPACE_ROOT}/records` — and left the caller
to check `Paths.bound` first. `_unbound`'s own docstring drew the conclusion:
the template "can be mistaken for neither, so the mistake this module exists to
prevent is not available to make". The mistake was available:

    Path("{WORKSPACE_ROOT}/records").mkdir(parents=True)   # succeeds
    Path("{WORKSPACE_ROOT}/records").is_dir()              # True

A directory named after a template appears in the process's working directory,
and from then on every check passes, because it exists. Shaping a value so a
reader cannot misread it does nothing about a program that never reads it. The
shape was standing in for a guard.

AND THE GUARD WAS NOMINALLY ELSEWHERE. `Paths.bound` is the real question, and
this tree answers it in three modules. Measured on the tree before the fix:
`root()` had 41 call sites in 13 files and `resolve()` — the wider door to the
same template — had 106 in `runtime/`; `paths.py` itself is the only file that
mentions `bound`. A rule kept by one file out of thirteen is not a rule.

SO THE REFUSAL MOVED TO THE SOURCE. `root()`, `resolve()` and `role()` now
refuse an unbound root unless the caller passes `unbound_ok=True`, and `resolve`
and `role` delegate to `root`, so all 147 doors close with one refusal. What
needed editing was the small set of surfaces whose JOB is to report that this
node was never identified, and that set is what `REPORTING` declares below.

'NO CONSUMER CALL SITE NEEDED EDITING' WAS WRITTEN HERE FIRST, AND THE FULL
SUITE FALSIFIED IT. `runtime/workbench_sources.py` resolved a role at IMPORT
time, and its own comment says an unbound node here should get the template,
with a named renderer downstream for exactly that. The refusal made the module
unimportable on a node with no profile — which is the SHIPPED case and not a
corner: `test_artifact_install_smoke` installs the artifact under a scratch HOME
and imports every module in it, and it went red twice. "Zero blast radius" is a
measurement, and this one was taken from the wrong sample: it counted call sites
and asked which of them check `bound`, and never asked which of them WANT the
template.

`REPORTING` IS CHECKED, NOT TRUSTED, AND THE INSTRUMENT IS AN AST. A text scan
for `unbound_ok=True` would count the docstring you are reading, the ledger
entry describing this package, and every comment quoting either — so the census
walks the tree instead and counts KEYWORD ARGUMENTS of calls, which prose cannot
be. That is the same discrimination `test_no_node_paths_in_product_data.py`
needed, for the same reason: a scanner that cannot tell a value from a sentence
cannot report on either.
"""
import ast
import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

import paths as paths_mod  # noqa: E402


#: `path -> (how many call sites, why this surface may name a template)`.
#: A report surface is one whose product is what a PERSON reads about this
#: node's contract; on an unidentified node the honest answer is the template.
#: Everything else computes over a real path and gets the refusal. An entry is
#: not a waiver — `TheDeclaredReportersAreRealTests` re-runs the census.
REPORTING: dict[str, tuple[int, str]] = {
    "runtime/cli.py": (
        3,
        "`context.current`'s payload and its printed lines: the command exists "
        "to answer 'what does this node declare', and on an unidentified node "
        "the answer is the template. A person reading it learns the node is "
        "unidentified, which is the reading the command is for.",
    ),
    "runtime/conformance.py": (
        4,
        "`surfaces()` pairs every closed vocabulary with the directory it "
        "governs. The template goes into a comparison that then finds no such "
        "directory and reports it — a finding, where a refusal here would be a "
        "crash in a report tool and an invented path would be a fabrication.",
    ),
    "runtime/daemon.py": (
        2,
        "The daemon's `context.current` answer, the same report as the CLI's, "
        "reached over the control plane instead of a terminal. Two spellings of "
        "one answer must not disagree about whether a template may be named.",
    ),
    "runtime/workbench_sources.py": (
        2,
        "`_under` and `_under_role` fill the tables that become the rendered "
        "workbench documents, so their product is TEXT A PERSON READS — and the "
        "unresolved case is already rendered: `workbench.py:_reachable` turns a "
        "repo path with no `.git` into `**repo 不可读**`, which is what a "
        "template produces. This file's own comment states the convention it "
        "relies on. It is also the shipped case: the module is read at import "
        "time, so a refusal here makes the artifact unimportable on a node with "
        "no profile — measured by `test_artifact_install_smoke`, which installs "
        "under a scratch HOME and imports every module. Found by that test after "
        "this package had already recorded 'no consumer call site needed "
        "editing', which was wrong.",
    ),
}

SKIP_DIRS = frozenset({"__pycache__", ".git", ".venv", "node_modules"})


def unbound_ok_call_sites(root: pathlib.Path) -> tuple[dict[str, int], int]:
    """`(sites by relative path -> count, files parsed)` for `unbound_ok=True`.

    Comments and docstrings are not in the tree, so neither is counted; a
    keyword argument of a call is, which is the only thing that changes
    behaviour.
    """
    sites: dict[str, int] = {}
    parsed = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if rel.parts[0] == "tests" or SKIP_DIRS & set(rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        parsed += 1
        found = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "unbound_ok"
            and isinstance(kw.value, ast.Constant) and kw.value.value is True
        )
        if found:
            sites[str(rel)] = found
    return sites, parsed


def _load(name: str, relative: str):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


class TheControlTests(unittest.TestCase):
    """The census has to count a keyword argument and not the same words in prose."""

    def test_it_counts_a_keyword_argument(self):
        tree = ast.parse(
            "def f(p):\n"
            "    return p.root('workspace', unbound_ok=True)\n")
        found = sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Call)
            for kw in node.keywords if kw.arg == "unbound_ok")
        self.assertEqual(found, 1)

    def test_it_does_not_count_the_same_words_in_a_docstring_or_a_comment(self):
        """This module's own docstring names the flag, so this is not academic."""
        tree = ast.parse(
            '"""A docstring naming unbound_ok=True as the escape."""\n'
            "# a comment naming unbound_ok=True\n"
            "def f():\n"
            '    """unbound_ok=True is described here."""\n'
            "    return 1\n")
        found = sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Call)
            for kw in node.keywords if kw.arg == "unbound_ok")
        self.assertEqual(found, 0)

    def test_it_does_not_count_the_flag_written_as_False(self):
        """Only the permissive spelling is a declaration."""
        tree = ast.parse("f(x, unbound_ok=False)\n")
        found = sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "unbound_ok" and isinstance(kw.value, ast.Constant)
            and kw.value.value is True)
        self.assertEqual(found, 0)


class TheRefusalTests(unittest.TestCase):
    """An ordinary read refuses; a declared report may name the template."""

    def setUp(self):
        self.unbound = paths_mod.Paths({})

    def test_the_world_is_actually_unbound(self):
        """An empty scan and a scan that found nothing are the same output."""
        self.assertFalse(self.unbound.bound)
        self.assertGreaterEqual(len(self.unbound.unconfigured), 5)

    def test_root_refuses_an_unbound_root(self):
        for name in self.unbound.unconfigured:
            if name not in self.unbound.roots:
                continue
            with self.subTest(name=name):
                with self.assertRaises(paths_mod.PathRefused):
                    self.unbound.root(name)

    def test_resolve_refuses_through_the_same_door(self):
        """`resolve` is the WIDER spelling and must not be the softer one."""
        with self.assertRaises(paths_mod.PathRefused):
            self.unbound.resolve("workspace/records")
        with self.assertRaises(paths_mod.PathRefused):
            self.unbound.resolve("state/boards")

    def test_role_refuses_too(self):
        with self.assertRaises(paths_mod.PathRefused):
            self.unbound.role("runtime.serveros-distribution")

    def test_the_refusal_names_the_template_rather_than_a_plausible_path(self):
        """The failure mode is a LOOK-RIGHT answer, so the message must not be one."""
        try:
            self.unbound.root("workspace")
        except paths_mod.PathRefused as refusal:
            text = str(refusal)
            self.assertIn("{WORKSPACE_ROOT}", text, "the template is not named")
            self.assertNotIn("/data/home", text)
            self.assertIn("unbound_ok=True", text, "the way to report is not offered")
        else:
            self.fail("an unbound root resolved")

    def test_the_declared_report_can_still_name_the_template(self):
        value = str(self.unbound.root("workspace", unbound_ok=True))
        self.assertEqual(value, "{WORKSPACE_ROOT}")

    def test_no_directory_named_after_a_template_can_be_created(self):
        """The measurement this package exists for, as a test.

        Before the fix this was the failing half: the call did not raise, and
        `mkdir` then produced a real directory called `{WORKSPACE_ROOT}` in the
        working directory. The assertion is not "the function raises" but "the
        filesystem is untouched", because the raise is only a means to that.
        """
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                try:
                    records = pathlib.Path(self.unbound.root("workspace")) / "records"
                    records.mkdir(parents=True, exist_ok=True)
                except paths_mod.PathRefused:
                    pass
                created = sorted(
                    str(p.relative_to(tmp)) for p in pathlib.Path(tmp).rglob("*"))
            finally:
                os.chdir(cwd)
        self.assertEqual(created, [], f"a template became a directory: {created}")

    def test_a_bound_node_is_untouched_by_all_of_this(self):
        """The refusal must cost nothing on a node that IS identified."""
        live = paths_mod.Paths()
        if not live.bound:  # pragma: no cover - this node's roots are bound
            self.skipTest("this node is unbound, so it cannot show the bound case")
        self.assertTrue(live.root("workspace").is_absolute())
        self.assertTrue(str(live.root("workspace")).startswith("/"))


class TheDeclaredReportersAreRealTests(unittest.TestCase):
    """The declaration is re-derived, so it cannot go stale."""

    def test_the_census_actually_reached_files(self):
        _, parsed = unbound_ok_call_sites(ROOT)
        self.assertGreaterEqual(parsed, 100, "the census read too few files to mean anything")

    def test_the_reporting_sites_are_exactly_the_declared_ones(self):
        sites, _ = unbound_ok_call_sites(ROOT)
        declared = {rel: count for rel, (count, _) in REPORTING.items()}
        self.assertEqual(
            sites, declared,
            "these differ from the declaration: a surface that stopped naming a "
            "template must lose its entry, and a new one must be declared here "
            "with a reason for why its job is to report")

    def test_every_declaration_gives_a_reason(self):
        for rel, (count, reason) in REPORTING.items():
            with self.subTest(rel=rel):
                self.assertGreater(count, 0)
                self.assertGreater(len(reason.strip()), 100, "the permission is not explained")
                self.assertTrue((ROOT / rel).exists(), f"{rel} is declared but absent")

    def test_the_narrow_module_is_not_among_them(self):
        """`paths.py` refuses; it never declares a reason to be exempt from itself."""
        self.assertNotIn("runtime/paths.py", REPORTING)


if __name__ == "__main__":
    unittest.main()
