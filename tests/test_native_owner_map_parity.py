"""One owner map, two readers: the Python half of the parity corpus.

Why this file exists
--------------------
`native/node-core/supervisor/tests/test_sup_owner_map_parity.cpp` asserts that the
C++ Core reaches the recorded verdict for every fixture in
`native/node-core/supervisor/contract/owner-map/`. This asserts that
`runtime/native_core_facts.py` reaches the **same** verdict for the **same**
files, read from the **same index**.

Neither side runs the other. The comparison is against a recorded answer, which
is what makes it a test that can run on a host with no compiler and still mean
something: a fixture can only change one reader's answer by changing the shared
index, and that changes both suites at once.

What "parity" is asserted on, and what it is not
------------------------------------------------
`usable` — believed or refused — and nothing else. The two implementations use
different refusal vocabularies on purpose (`FACTS_MALFORMED`/`FACTS_BAD_SCHEMA`
on the C++ side, `OWNER_UNKNOWN` on the Python side), and neither is more right.
The fact that decides who may act on a unit is whether the map is believed.

The lease half
--------------
The single-writer property is `flock` on one file, taken by whichever program is
acting. This file asserts the Python side refuses a second holder; the C++ suite
(`test_sup_lease`) asserts the same for `WriterLease`. Both are `LOCK_EX|LOCK_NB`
on a path, so the kernel is what serialises them across languages — the part that
is NOT proven here, and cannot be without running both, is that a deployment
gives them the same path. That gap is named in `serverosd/contract/single-writer.v1.md`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "native/node-core/supervisor/contract/owner-map"
INDEX = CORPUS / "expectations.json"
CPP_PARITY = ROOT / "native/node-core/supervisor/tests/test_sup_owner_map_parity.cpp"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.dont_write_bytecode = True
facts = _load("native_core_facts_parity", ROOT / "runtime" / "native_core_facts.py")


def cases() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8"))["cases"]


def verdict(path: Path) -> tuple[bool, str | None]:
    """Believed, or refused and WHY.

    The code matters, not just the boolean. `OwnerMap.load` refuses for two
    unrelated reasons: the document is not one it will believe (`OWNER_UNKNOWN`),
    or the file itself is not one it will read — a symlink, another uid, a
    group-writable mode, a path that escapes its binding (`PATH_UNSAFE` and
    friends). Returning the boolean alone meant a checkout with different file
    modes could turn the two `usable: true` cases red for a harness reason while
    the eight refusals kept matching, and the suite would look like it was
    testing the document.
    """
    try:
        facts.OwnerMap.load(path)
        return True, None
    except facts.FactsRefused as exc:
        return False, exc.code


class OwnerMapParityTest(unittest.TestCase):
    def test_the_index_names_this_readers_schema(self):
        # A fixture written against an older schema would be refused by both
        # readers for the wrong reason and the corpus would still look green.
        for case in cases():
            document = json.loads((CORPUS / case["file"]).read_text(encoding="utf-8"))
            if isinstance(document, dict) and "schema" in document:
                if case["file"] in ("wrong-schema.json",):
                    continue    # the one fixture that is deliberately foreign
                self.assertEqual(document["schema"], facts.OWNER_MAP_SCHEMA, case["file"])

    def test_every_case_reaches_its_recorded_verdict(self):
        listed = cases()
        self.assertGreaterEqual(len(listed), 10)
        for case in listed:
            with self.subTest(case=case["file"]):
                path = CORPUS / case["file"]
                self.assertTrue(path.is_file(), f"{case['file']} is indexed but missing")
                usable, code = verdict(path)
                self.assertEqual(usable, case["usable"],
                                 f"{case['file']}: {case['why']}")
                if not usable:
                    self.assertEqual(code, facts.OWNER_UNKNOWN,
                                     f"{case['file']} was refused for a reason about the "
                                     f"FILE, not the document; this case proves nothing")

    def test_every_fixture_on_disk_is_in_the_index(self):
        """EVERY entry, not only the ones this glob would have read.

        A fixture in a subdirectory, or named `X.JSON`, is invisible to both
        completeness checks and unread by both suites — listed by nobody and
        tested by nobody, with everything green.
        """
        on_disk = {p.name for p in CORPUS.iterdir()}
        indexed = {case["file"] for case in cases()} | {INDEX.name}
        self.assertEqual(on_disk, indexed,
                         "an entry nobody indexed is a document neither reader is held to")

    def test_the_two_readers_are_pointed_at_one_corpus(self):
        """The drift this prevents: each suite quietly testing its own copy."""
        source = CPP_PARITY.read_text(encoding="utf-8")
        self.assertIn('"/contract/owner-map"', source,
                      "the C++ parity suite no longer reads this corpus")
        self.assertIn("expectations.json", source,
                      "the C++ parity suite no longer reads this index")

    def test_the_boundary_fixtures_sit_exactly_on_the_bound(self):
        many = json.loads((CORPUS / "too-many-units.json").read_text(encoding="utf-8"))
        self.assertEqual(len(many["units"]), facts.MAX_SERVICES + 1)
        single = json.loads((CORPUS / "long-unit.json").read_text(encoding="utf-8"))
        (unit,) = single["units"]
        self.assertEqual(len(unit.encode("utf-8")), facts.MAX_FACT_TEXT + 1)


class OneWriterMechanismTest(unittest.TestCase):
    """The Python half of the lease. The C++ half is `test_sup_lease`."""

    def test_the_lease_refuses_a_second_holder(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "act.lease"
            first = facts.SafeLease(path)
            first.acquire()
            self.assertTrue(first.held)
            try:
                second = facts.SafeLease(path)
                with self.assertRaises(facts.FactsRefused) as caught:
                    second.acquire()
                self.assertEqual(caught.exception.code, facts.LEASE_BUSY)
                self.assertFalse(second.held)
            finally:
                first.release()

    def test_the_lease_is_free_again_after_release(self):
        # Releasing must actually release: a lease that only ever refused would
        # pass the test above while making the node single-use.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "act.lease"
            first = facts.SafeLease(path)
            first.acquire()
            first.release()
            second = facts.SafeLease(path)
            second.acquire()
            self.assertTrue(second.held)
            second.release()


class SingleWriterRefusalTest(unittest.TestCase):
    """The acting path, driven end to end through a FAKE service manager.

    The property: a Core that acts must consult the shared owner map, and a unit
    the map gives the Python daemon must not be touched. Proven here by running
    the real binary with `--systemctl` pointing at a script that records its argv
    and answers `show` with a failed unit — so an action is *attempted* and
    nothing on this host is ever asked to change.

    This is a regression test for a real gap. Before 2026-10-28 the one-shot
    form (`--manifest M --enable-actions`, no `--serve`) could not even be GIVEN
    an owner map — the flag was refused outside `--serve` — and the ownership
    check only ran in the resident path. A single pass could therefore restart a
    unit the map assigns to `serverosd`, while `--capability` reported
    `NOT_MINE`. The gate is now `act`, in both modes.

    Skipped when no binary is built, because a compiler is not a test dependency:
    set SERVEROS_NATIVE_CORE_BIN, or run
    `make -f serverosd/Makefile OUT=<dir> all` first.
    """

    BINARY_ENV = "SERVEROS_NATIVE_CORE_BIN"

    def setUp(self):
        raw = os.environ.get(self.BINARY_ENV)
        if not raw or not Path(raw).is_file():
            self.skipTest(f"no native binary; set {self.BINARY_ENV} to run this "
                          f"(see the class docstring)")
        self.binary = Path(raw)
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # -- fixtures ---------------------------------------------------------
    def _manifest(self, unit: str) -> Path:
        path = self.dir / "manifest.json"
        path.write_text(json.dumps({
            "schema": "serveros/desired-state.v1",
            "node": "test-node", "principal": "test",
            "observe_only": False, "generation": 1,
            "services": [{
                "id": "svc", "owner": "test", "unit": unit,
                "desired": "running", "start_on_boot": False,
                "recovery": {"allowed_verbs": ["restart"], "offline_recoverable": True},
            }],
        }), encoding="utf-8")
        return path

    def _map(self, unit: str, owner: str) -> Path:
        path = self.dir / f"map-{owner}.json"
        path.write_text(json.dumps({"schema": "serveros/native-core-owner-map.v1",
                                    "units": {unit: owner}}), encoding="utf-8")
        return path

    def _fake_systemctl(self) -> tuple[Path, Path]:
        """A service manager that records argv and reports one failed unit."""
        log = self.dir / "calls.log"
        script = self.dir / "systemctl"
        script.write_text(
            "#!/bin/sh\n"
            f'printf \'%s\\n\' "$*" >> "{log}"\n'
            "for a in \"$@\"; do\n"
            "  case \"$a\" in\n"
            "    show) printf 'LoadState=loaded\\nActiveState=failed\\nSubState=failed\\n'; exit 0 ;;\n"
            "    restart|start|stop) exit 0 ;;\n"
            "  esac\n"
            "done\n"
            "exit 0\n", encoding="utf-8")
        script.chmod(0o755)
        return script, log

    def _run(self, manifest: Path, *extra: str) -> subprocess.CompletedProcess:
        systemctl, _ = self._fake_systemctl()
        return subprocess.run(
            [str(self.binary), "--manifest", str(manifest), "--allow-unit", "theirs.service",
             "--enable-actions", "--lease", str(self.dir / "lease"),
             "--state", str(self.dir / "recovery.json"),
             "--link-state", "up", "--agent-state", "idle",
             "--systemctl", str(systemctl), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)

    # -- the assertions ---------------------------------------------------
    def test_acting_without_an_owner_map_is_refused(self):
        proc = self._run(self._manifest("theirs.service"))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("--enable-actions requires --owner-map", proc.stderr)

    def test_a_unit_the_map_gives_the_python_daemon_is_refused(self):
        manifest = self._manifest("theirs.service")
        proc = self._run(manifest, "--owner-map", str(self._map("theirs.service", "serverosd")))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("is owned by serverosd in the shared owner map", proc.stderr)
        # And nothing was asked of the service manager at all.
        _, log = self._fake_systemctl()
        self.assertEqual(log.read_text() if log.exists() else "", "",
                         "the fake systemctl was called for a unit this Core does not own")

    def test_the_same_unit_is_acted_on_when_the_map_gives_it_to_this_core(self):
        """The control: the refusal above is about ownership, not about acting."""
        manifest = self._manifest("theirs.service")
        proc = self._run(manifest, "--owner-map", str(self._map("theirs.service", "native-core")))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("is owned by", proc.stderr)
        # It reached the service manager — the FAKE one.
        _, log = self._fake_systemctl()
        calls = log.read_text() if log.exists() else ""
        self.assertIn("restart", calls)

    def test_the_capability_document_agrees_with_the_refusal(self):
        """The document and the acting path are the same verdict, or this fails."""
        manifest = self._manifest("theirs.service")
        owner_map = self._map("theirs.service", "serverosd")
        document = subprocess.run(
            [str(self.binary), "--manifest", str(manifest),
             "--owner-map", str(owner_map), "--capability"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(document.returncode, 0, document.stderr)
        parsed = json.loads(document.stdout)
        (claim,) = parsed["claims"]
        self.assertEqual(claim["verdict"], "NOT_MINE")
        self.assertFalse(claim["this_core_may_act"])

        acting = self._run(manifest, "--owner-map", str(owner_map))
        self.assertEqual(acting.returncode, 2,
                         "the document says NOT_MINE and the acting path acted anyway")


if __name__ == "__main__":
    unittest.main()
