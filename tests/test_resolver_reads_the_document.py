#!/usr/bin/env python3
"""Two authored documents, and a row asked for in the other one's words.

`context/resolver.py` and `runtime/paths.py` read two DIFFERENT documents:
the node's `~/.config/serveros/host-profile.yaml`, and the checkout's
`fleet/profiles.yaml`. Each row has a name in each of them, and the two names
are not the same word:

    host profile    node.roots.artifacts_root   /data/home/vince/artifacts
    fleet profiles  paths.artifacts             (a role, bound per profile)

`context/resolver.py` read the HOST PROFILE and asked it for the FLEET's word,
`"artifacts"`. Nothing answered, the `or` fell through, and the root was
COMPUTED from `data_root` rather than READ. On this node the computed value and
the declared one are the same directory, which is what let it survive: measured
2026-10-09 in `probe/row-read-not-computed-ab.py`, moving `artifacts_root` to a
sentinel moved the baseline resolver by nothing at all.

`runtime/paths.py` records the same reading about the same word, one table over
("`artifacts` and `toolchains` ... were COMPUTED rather than READ. On this node
the two coincide and only the provenance was wrong, which is what made it
survive"). That copy was fixed. This file holds the other one.

NOT HERMETIC, AND WHY THAT IS NAMED HERE RATHER THAN LEFT TO BE FOUND
--------------------------------------------------------------------
`tests/test_resolver.py` says of itself "Nothing here reads this machine's real
records, worktrees, artifacts or state", and keeps that. Two questions below
cannot be asked of a synthetic world, because both are about THIS NODE'S OWN
TWO DOCUMENTS:

  * is every row the tree asks for a row this node's document declares?
  * do the two documents currently agree about the same root?

Both SKIP on a node with no host profile rather than passing. An unanswerable
question is not a satisfied one, and a test that reports "fine" wherever it
could not look is the shape this whole package is about.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
RESOLVER = REPO / "context" / "resolver.py"
PATHS = REPO / "runtime" / "paths.py"
TRUTH = REPO / "scripts" / "runtime_truth.py"
ARCH = REPO / "skills" / "serveros" / "scripts" / "architecture_check.py"

DOCUMENT = Path("~/.config/serveros/host-profile.yaml").expanduser()
FLEET_PROFILES = REPO / "fleet" / "profiles.yaml"

ROOT_ENVIRONMENT = ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT",
                    "SERVEROS_ARTIFACTS_ROOT")


def _load(name: str, path: Path):
    """The tree's own idiom: read the cache before writing to it.

    `tests/test_loader_identity.py` is why. The name is process-wide, so a
    second loader that registers a name the first one used does not create a
    second name — it takes the name and leaves the first loader's readers
    holding an object the name no longer reaches, each copy carrying its own
    classes.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load("serveros_context_resolver", RESOLVER)
paths = _load("serveros_runtime_paths", PATHS)


def _declared_rows() -> set[str] | None:
    """`node.roots` of this node's profile, or None when there is no document."""
    if not DOCUMENT.is_file():
        return None
    import yaml
    document = yaml.safe_load(DOCUMENT.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        return None
    node = document.get("node")
    roots = node.get("roots") if isinstance(node, dict) else None
    return set(roots) if isinstance(roots, dict) else None


class TheRowIsReadNotComputed(unittest.TestCase):
    """The reading that came back False before the fix, kept as the control."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _profile(self, **rows) -> Path:
        path = self.base / "host-profile.yaml"
        body = "".join(f"    {key}: {value}\n" for key, value in rows.items())
        path.write_text("schema: serveros/host-profile.v1\nnode:\n  roots:\n" + body,
                        encoding="utf-8")
        return path

    def _environment(self, **extra):
        """Every root named by the environment, removed — this machine's too."""
        keep = {key: value for key, value in os.environ.items()
                if key not in ROOT_ENVIRONMENT + (resolver.HOST_PROFILE_ENV,)}
        keep.update({key: str(value) for key, value in extra.items()})
        return mock.patch.dict(os.environ, keep, clear=True)

    def _artifacts_root(self, profile: Path, **environment) -> Path:
        with self._environment(**environment), \
                mock.patch.dict(os.environ,
                                {resolver.HOST_PROFILE_ENV: str(profile)}):
            return resolver.Roots.from_env().artifacts_root

    def test_the_documents_spelling_is_the_one_that_answers(self):
        """NEGATIVE CONTROL. This is the assertion that was false before.

        A profile that declares `artifacts_root` — the spelling this node's own
        document uses, and the only one it uses — must move the answer. It did
        not, and the test that was supposed to notice wrote its fixture in the
        other spelling, so it agreed with the code rather than with the
        document. Nothing here reads the machine's profile: the sentinel is a
        directory this test made up, and the reading is that the resolver
        followed it.
        """
        sentinel = self.base / "declared-by-the-document"
        profile = self._profile(identity_home=self.base, artifacts_root=sentinel)
        self.assertEqual(self._artifacts_root(profile), sentinel)

    def test_the_other_spelling_still_answers(self):
        """A profile that spells the row `artifacts` keeps working.

        Kept deliberately, for the reason `_data_root` lists `data_root` and
        `identity_home`: a root may be named more than one way and the caller
        tries them in order. No document on this node spells it this way — the
        name is the fleet table's role name — but a node that does must not
        lose its answer to this fix.
        """
        sentinel = self.base / "declared-by-the-other-spelling"
        profile = self._profile(identity_home=self.base, artifacts=sentinel)
        self.assertEqual(self._artifacts_root(profile), sentinel)

    def test_the_documents_spelling_wins_when_both_are_present(self):
        """Order is a claim, so the claim is asserted rather than assumed."""
        document, other = self.base / "from-the-document", self.base / "from-the-role"
        profile = self._profile(identity_home=self.base, artifacts_root=document,
                                artifacts=other)
        self.assertEqual(self._artifacts_root(profile), document)

    def test_the_environment_still_outranks_both_documents(self):
        """The fix added a key; it did not reorder the precedence.

        Asserted because the cheap way to make the test above pass is to move
        the profile branch up, and that would take `ARTIFACTS_ROOT` away from
        the caller who set it on purpose.
        """
        profile = self._profile(identity_home=self.base,
                                artifacts_root=self.base / "from-the-document")
        chosen = self.base / "from-the-environment"
        self.assertEqual(self._artifacts_root(profile, ARTIFACTS_ROOT=chosen), chosen)

    def test_an_absent_row_is_still_derived_from_the_data_root(self):
        """The derived branch is not deleted — it is the branch that was reached.

        `{data_root}/artifacts` is relative layout, not another device's
        absolute path, and `runtime/paths.py` keeps the same derivation in
        `_SEED_DEFAULT_SUFFIX`. A node whose profile declares no artifacts row
        must still resolve, or this fix would refuse a layout that worked.
        """
        profile = self._profile(identity_home=self.base)
        self.assertEqual(self._artifacts_root(profile), self.base / "artifacts")


class EveryRowTheTreeAsksFor(unittest.TestCase):
    """The general form, so the next wrong row name goes red where it is written.

    Pinning `artifacts` alone would fix one word. The property that was broken
    is "a branch may be named in words the document does not use", and it is
    held per ASK: whatever keys a call site tries, at least one of them has to
    be a row this node's document declares, or that branch can never answer and
    its value is derived or refused instead. Both legitimate shapes pass it —
    `("data_root", "identity_home")` is answered by its second key, and
    `("artifacts_root", "artifacts")` by its first.
    """

    def _rows_asked_for_by_the_resolver(self) -> list[tuple[str, tuple[str, ...]]]:
        """Every `_profile_root(...)` call in `context/resolver.py`, with its keys.

        Read from the source rather than the loaded module: a call site that
        does not exist yet is exactly what this is meant to catch.
        """
        tree = ast.parse(RESOLVER.read_text(encoding="utf-8"))
        asks = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "_profile_root"):
                continue
            keys = tuple(argument.value for argument in node.args
                         if isinstance(argument, ast.Constant)
                         and isinstance(argument.value, str))
            asks.append((f"line {node.lineno}", keys))
        return asks

    def _rows_asked_for_by_the_two_copies(self) -> list[tuple[str, tuple[str, ...]]]:
        """The tables `runtime_truth.py` and `architecture_check.py` carry."""
        asks = []
        for label, path, name in (
            ("scripts/runtime_truth.py", TRUTH, "serveros_runtime_truth_for_rows"),
            ("skills/serveros/scripts/architecture_check.py", ARCH,
             "serveros_architecture_check_for_rows"),
        ):
            module = _load(name, path)
            for argument, (_, keys, _) in sorted(module.ROOT_SOURCES.items()):
                asks.append((f"{label} ROOT_SOURCES[{argument!r}]", tuple(keys)))
        return asks

    def test_at_least_one_key_per_ask_is_a_row_this_nodes_document_declares(self):
        declared = _declared_rows()
        if declared is None:
            self.skipTest(f"no readable host profile at {DOCUMENT}; nothing to check against")

        unanswerable = []
        for where, keys in (self._rows_asked_for_by_the_resolver()
                            + self._rows_asked_for_by_the_two_copies()):
            if not keys:
                unanswerable.append(f"{where}: asks for nothing at all")
            elif not (set(keys) & declared):
                unanswerable.append(
                    f"{where}: asks for {list(keys)}, and this node's document "
                    f"declares none of them")

        self.assertEqual(
            unanswerable, [],
            "A profile key no document declares cannot ever answer. The branch "
            "behind it is unreachable and the value is derived from another "
            "root instead, which is invisible on a node where the two layouts "
            "coincide — this is how `artifacts` sat unread while "
            "`node.roots.artifacts_root` went unasked. Either fix the key, or "
            "declare the row in " + str(DOCUMENT) + ".")

    def test_the_census_can_see_the_asks_it_is_supposed_to_see(self):
        """A negative control for the scan, so an empty list is not read as clean.

        `_profile_root` is the only reader of the profile in this file, and if
        the walk stopped matching — a rename, a rewrite to `getattr` — every
        assertion above would pass by finding nothing to complain about.
        """
        asks = self._rows_asked_for_by_the_resolver()
        self.assertGreaterEqual(len(asks), 2)
        keys = {key for _, asked in asks for key in asked}
        self.assertIn("identity_home", keys)
        self.assertIn("artifacts_root", keys)
        for _, asked in self._rows_asked_for_by_the_two_copies():
            self.assertTrue(asked, "a ROOT_SOURCES entry names no profile key at all")


class TheTwoAuthoredDocuments(unittest.TestCase):
    """Both read with the environment held out of the way, so both must be
    answering from their document. A difference is the documents' and not the
    runner's."""

    def test_they_agree_on_this_node_or_this_fails_naming_the_question(self):
        if not DOCUMENT.is_file():
            self.skipTest(f"no host profile at {DOCUMENT}; nothing to compare")

        keep = {key: value for key, value in os.environ.items()
                if key not in ROOT_ENVIRONMENT + (resolver.HOST_PROFILE_ENV,)}
        with mock.patch.dict(os.environ, keep, clear=True):
            try:
                from_the_host_profile = resolver.Roots.from_env().artifacts_root
            except resolver.RootsUnresolved as exc:
                self.skipTest(f"the resolver has no answer on this node: {exc}")
            try:
                from_the_fleet_table = Path(paths.Paths().root("artifacts"))
            except Exception as exc:                            # noqa: BLE001
                self.skipTest(f"the root contract has no answer here: {exc!r}")

        if not from_the_fleet_table.is_absolute():
            self.skipTest(f"this node's root contract is unbound: {from_the_fleet_table}")

        self.assertEqual(
            from_the_host_profile, from_the_fleet_table,
            "Two authored documents answer one question and now disagree:\n"
            f"  {DOCUMENT}  node.roots.artifacts_root  -> {from_the_host_profile}\n"
            f"  {FLEET_PROFILES}  paths.artifacts      -> {from_the_fleet_table}\n"
            "Neither is derived from the other, so this is a finding and not a "
            "defect this test can fix: one document was changed and the other "
            "was not, or this node means to override its profile and has not "
            "said so anywhere the other reader can see. Which document settles "
            "it is a question for a person — the same question "
            "`PENDING-DECISIONS.md` carries about the two root authorities.")


if __name__ == "__main__":
    unittest.main()
