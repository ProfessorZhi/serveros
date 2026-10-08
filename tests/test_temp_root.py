#!/usr/bin/env python3
"""Temporary bytes land where this node declared, or the run says why not.

WHY THIS FILE EXISTS
--------------------
`tempfile` answers two different questions with one name. With `TMPDIR` set it
answers the machine's; with `TMPDIR` unset it answers `/tmp`. On this node
`/tmp` is `/dev/vda2` — the 50 GB system disk the machine policy excludes from
working storage — and `TMPDIR` is unset in every systemd USER unit
(`systemctl --user show-environment` carries none). So the SAME command staged
its bytes in two different places depending on whether a timer or a login shell
started it, and nothing in either run's output said which. Measured 2026-10-09:
`/tmp` held 15 GB, of which `/tmp/pytest-of-vince` was 178 MB from 682 runs.

The two producers fixed here are the two that stage the most:

  * `scripts/verify.py` — one census scratch per test file, plus pytest's own
    `tmp_path` base inside the same run.
  * `scripts/runtime_truth.py` — a checkout-sized extraction of a commit, the
    largest single transient write in this tree.

WHAT IS ASSERTED, AND WHAT IS NOT
---------------------------------
The ORDER is what is under test here: an explicit answer wins, the declared
root answers when nobody said anything, and an unbound contract is refused by
name rather than papered over. Whether `Paths.root("tmp")` names the right
directory is `runtime/paths.py`'s question and is tested there — a stub stands
in for it below, so that a change to the root contract cannot make these tests
pass or fail for the wrong reason.

The last test is the one that matters most: both producers are asked the same
question on the machine the suite is running on, and their answers are compared.
A rule stated twice is two rules, and the only thing that keeps them one is a
test that reads both.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    """The tree's own idiom: read the cache before writing to it.

    Without the early return this is an UNGUARDED registration, and
    `tests/test_loader_identity.py` says why that is not a style point: the
    name is process-wide, so the second writer of it takes it from the first,
    and each copy carries its own classes — which is how a `Refused` gets
    raised as one class and caught as another. This file loads two modules
    under synthetic names; the guard is what keeps that from costing
    `scripts/verify.py`'s exception classes their identity in a suite that
    runs both this file and everything else in one process.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _load("serveros_scripts_verify_temp_root", "scripts/verify.py")
TRUTH = _load("serveros_scripts_runtime_truth_temp_root", "scripts/runtime_truth.py")


class _FakePaths:
    """A stand-in for `runtime/paths.Paths` with one answer set by the test."""

    def __init__(self, *, bound: bool, tmp: Path | None = None,
                 unconfigured: tuple = ()) -> None:
        self.bound = bound
        self._tmp = tmp
        self.unconfigured = unconfigured
        self.asked: list[str] = []

    def root(self, name: str, *, unbound_ok: bool = False) -> Path:
        self.asked.append(name)
        assert name == "tmp", name
        assert self._tmp is not None
        return self._tmp


def _with_paths(**kwargs):
    """Patch the module loader `verify.py` uses, for one call."""
    fake = _FakePaths(**kwargs)
    return mock.patch.object(
        VERIFY, "_paths_module", lambda: SimpleNamespace(Paths=lambda: fake)), fake


class VerifyStagesUnderTheDeclaredRoot(unittest.TestCase):

    def test_TMPDIR_wins_when_the_machine_gave_one(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"TMPDIR": td}):
                with mock.patch.object(
                        VERIFY, "_paths_module",
                        side_effect=AssertionError(
                            "the declared root must not be consulted when the "
                            "machine answered")):
                    self.assertEqual(VERIFY.temp_root(), Path(td))

    def test_no_TMPDIR_asks_the_root_contract_for_its_tmp_root(self):
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
            patcher, fake = _with_paths(bound=True, tmp=Path(td))
            with mock.patch.dict(os.environ, env, clear=True), patcher:
                self.assertEqual(VERIFY.temp_root(), Path(td))
                self.assertEqual(fake.asked, ["tmp"])

    def test_an_unbound_contract_is_refused_by_name(self):
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        patcher, _ = _with_paths(bound=False, unconfigured=("data", "home"))
        with mock.patch.dict(os.environ, env, clear=True), patcher:
            with self.assertRaises(VERIFY.VerifyError) as caught:
                VERIFY.temp_root()
        message = str(caught.exception)
        self.assertIn("UNBOUND", message)
        # The two things a reader needs: which roots could not be resolved, and
        # that the system temp directory was declined rather than used.
        self.assertIn("data", message)
        self.assertIn("home", message)
        self.assertIn("Refusing the system temp directory", message)

    def test_the_system_temp_directory_is_never_the_answer(self):
        """The named limit: no branch of `temp_root()` can return it.

        Not a paraphrase of the code — it drives the one path that used to
        reach `tempfile.gettempdir()` (TMPDIR unset, contract unbound) and
        asserts the answer is a refusal rather than `/tmp`. On a machine whose
        declared root happened to BE `/tmp` this would be a false pass, so the
        assertion is on the ENV-less, contract-less path only, where the old
        code's answer is knowable.
        """
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        patcher, _ = _with_paths(bound=False, unconfigured=("data",))
        with mock.patch.dict(os.environ, env, clear=True), patcher:
            with self.assertRaises(VERIFY.VerifyError):
                VERIFY.temp_root()


class TheRunAndItsChildStageInOnePlace(unittest.TestCase):
    """`verify.py run` states where it staged, and the census child goes there.

    This is the end-to-end half: a real census child is spawned, and pytest's
    own `tmp_path` base — which is `tempfile.gettempdir()` inside that child —
    is checked to have followed the parent's answer. Without the child's
    `TMPDIR` the report and the test's temp files would be written to two
    different places by one run, and the record would show only its own.
    """

    def test_pytests_tmp_path_base_follows_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            (repo / "tests").mkdir(parents=True)
            (repo / "tests" / "test_tiny.py").write_text(
                "def test_tmp_path(tmp_path):\n"
                "    assert tmp_path.is_dir()\n", encoding="utf-8")
            staged = Path(td) / "staged"
            staged.mkdir()
            with mock.patch.dict(os.environ, {"TMPDIR": str(staged)}):
                report = VERIFY.measure(repo, "tests/test_tiny.py",
                                        python=sys.executable)

            self.assertEqual(report["scratch_root"], str(staged))
            self.assertEqual(report["exit_code"], 0)
            self.assertTrue(
                any(p.name.startswith("pytest-of-") for p in staged.iterdir()),
                f"pytest staged nowhere under {staged}: "
                f"{[p.name for p in staged.iterdir()]}")


class BothProducersGiveOneAnswer(unittest.TestCase):

    def test_the_declared_temp_root_is_asked_for_by_the_same_name(self):
        """One rule, reached two ways — compared on the machine running this.

        `verify.py` asks the root contract directly; `runtime_truth.py` loads
        that same module and asks it. The assertion is that the two land on one
        directory, which is the only property that makes "the rule is stated
        once" a fact rather than a claim.
        """
        env = {k: v for k, v in os.environ.items() if k != "TMPDIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            try:
                mine = VERIFY.temp_root()
            except VERIFY.VerifyError as exc:
                self.skipTest(f"this node's roots are unbound: {exc}")
            theirs = TRUTH.declared_temp_root()
        self.assertIsNotNone(
            theirs, "verify.py resolved a tmp root and runtime_truth.py did not")
        self.assertEqual(mine, theirs)

    def test_the_source_cache_sits_under_whichever_temp_root_answered(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(os.environ, {"SERVEROS_SOURCE_CACHE": td}):
                self.assertEqual(TRUTH.default_source_cache(), Path(td))
            env = {k: v for k, v in os.environ.items()
                   if k not in ("TMPDIR", "SERVEROS_SOURCE_CACHE")}
            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(TRUTH, "declared_temp_root",
                                       return_value=Path(td)):
                    self.assertEqual(TRUTH.default_source_cache(),
                                     Path(td) / TRUTH.SOURCE_CACHE_NAME)

    def test_an_unresolvable_temp_root_returns_none_rather_than_a_temp_dir(self):
        """`default_source_cache()` answers None, and main() refuses on it.

        Returning `/tmp/...` here would be the same defect one layer up: the
        caller cannot tell a decided path from an accidental one, so the
        decision is not made at the bottom.
        """
        env = {k: v for k, v in os.environ.items()
               if k not in ("TMPDIR", "SERVEROS_SOURCE_CACHE")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(TRUTH, "declared_temp_root", return_value=None):
                self.assertIsNone(TRUTH.default_source_cache())


if __name__ == "__main__":
    unittest.main()
