"""The owner map's WRITER now enforces the bounds both READERS enforce.

Why this file exists
--------------------
`OwnerMap.load` (Python) and `load_owner_map` (C++) both refuse a map with more
than `MAX_SERVICES` entries or a unit id longer than `MAX_FACT_TEXT`. The writer
enforced neither, so it could produce a document its own reader rejects: a map
that exists, is written, and means nothing — and the discovery would happen at
the reader, hours later, as "no unit has an owner".

This is the third corner of one triangle. The readers were aligned in #412
(`the_cpp_bounds_are_the_python_readers_bounds`); the parity corpus in #430 picks
documents that sit exactly on both bounds; this closes the side that can create
them.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTS_HPP = ROOT / "native/node-core/supervisor/include/supervisor/facts.hpp"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.dont_write_bytecode = True
facts = _load("native_core_facts_writer", ROOT / "runtime" / "native_core_facts.py")


def specs(count: int, *, unit=lambda index: f"svc{index:03d}.service"):
    return [facts.ServiceSpec(id=f"s{index}", unit=unit(index),
                              owner=facts.OWNER_NATIVE_CORE)
            for index in range(count)]


class WriterBoundsTest(unittest.TestCase):
    def test_the_writer_refuses_more_units_than_its_reader_accepts(self):
        # At the bound: accepted. The accepting side is asserted too, because a
        # writer that refused everything would pass a one-sided test.
        at_bound = facts.build_owner_map(specs(facts.MAX_SERVICES))
        self.assertEqual(len(at_bound), facts.MAX_SERVICES)

        with self.assertRaises(facts.FactsRefused) as caught:
            facts.build_owner_map(specs(facts.MAX_SERVICES + 1))
        self.assertEqual(caught.exception.code, facts.DOCUMENT_INVALID)
        self.assertIn("more than", str(caught.exception))

    def test_the_writer_refuses_a_unit_id_longer_than_its_reader_accepts(self):
        at_bound = facts.build_owner_map(specs(1, unit=lambda _: "a" * facts.MAX_FACT_TEXT))
        self.assertEqual(len(at_bound), 1)

        with self.assertRaises(facts.FactsRefused) as caught:
            facts.build_owner_map(specs(1, unit=lambda _: "b" * (facts.MAX_FACT_TEXT + 1)))
        self.assertEqual(caught.exception.code, facts.DOCUMENT_INVALID)

    def test_a_constructed_map_is_checked_at_write_too(self):
        """`OwnerMap({...})` is public, so it is a second door to the same file."""
        oversized = {f"svc{index:03d}.service": facts.OWNER_NATIVE_CORE
                     for index in range(facts.MAX_SERVICES + 1)}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.OwnerMap(oversized).write(Path(td) / "owner-map.json")
            self.assertEqual(caught.exception.code, facts.DOCUMENT_INVALID)
            self.assertFalse((Path(td) / "owner-map.json").exists(),
                             "a refused map was written anyway")

    def test_an_empty_unit_is_refused_the_same_way_the_reader_refuses_it(self):
        with self.assertRaises(facts.FactsRefused):
            facts.build_owner_map(specs(1, unit=lambda _: ""))
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(facts.FactsRefused):
                facts.OwnerMap({"": facts.OWNER_NATIVE_CORE}).write(Path(td) / "m.json")

    def test_a_written_map_round_trips_through_the_reader(self):
        """The point of the bounds: what the writer emits, the reader accepts."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "owner-map.json"
            built = facts.build_owner_map(specs(3))
            facts.OwnerMap(built).write(path)
            loaded = facts.OwnerMap.load(path)
            self.assertEqual(loaded.owner_of("svc000.service"), facts.OWNER_NATIVE_CORE)


class BoundsAgreeWithTheOtherTwoCornersTest(unittest.TestCase):
    def test_the_writer_and_the_cpp_reader_state_the_same_numbers(self):
        header = FACTS_HPP.read_text(encoding="utf-8")
        entries = re.search(r"kMaxOwnerEntries\s*=\s*(\d+)", header)
        unit_bytes = re.search(r"kMaxOwnerUnitBytes\s*=\s*(\d+)", header)
        self.assertIsNotNone(entries, "facts.hpp no longer declares kMaxOwnerEntries")
        self.assertIsNotNone(unit_bytes, "facts.hpp no longer declares kMaxOwnerUnitBytes")
        self.assertEqual(facts.MAX_SERVICES, int(entries.group(1)))
        self.assertEqual(facts.MAX_FACT_TEXT, int(unit_bytes.group(1)))


if __name__ == "__main__":
    unittest.main()
