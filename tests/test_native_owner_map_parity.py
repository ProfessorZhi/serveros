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


def verdict(path: Path) -> bool:
    """Believed or refused, and nothing else — see the module docstring."""
    try:
        facts.OwnerMap.load(path)
        return True
    except facts.FactsRefused:
        return False


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
                self.assertEqual(verdict(path), case["usable"],
                                 f"{case['file']}: {case['why']}")

    def test_every_fixture_on_disk_is_in_the_index(self):
        on_disk = {p.name for p in CORPUS.glob("*.json") if p.name != INDEX.name}
        indexed = {case["file"] for case in cases()}
        self.assertEqual(on_disk, indexed,
                         "a fixture nobody indexed is a document neither reader is held to")

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


if __name__ == "__main__":
    unittest.main()
