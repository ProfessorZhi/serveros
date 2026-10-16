#!/usr/bin/env python3
"""`gpu.readonly` is read as the producer writes it — or not reported at all.

The sentence these tests keep false:

    "a PRESENT gpu reading with an empty value"

`OBSERVATION_VALUE_KEYS["gpu.readonly"]` was `{}`. `_project_value` iterates the
spec, so an empty spec meant the loop body never ran: a genuine `gpus` array was
stripped to `{}`, every one of its keys was logged as an unknown field this
reader does not carry, and the result was `PRESENT` with `COMPLETE` — a
success-shaped nothing. The gap was invisible for as long as it was because no
producer emitted the observation: it answered `UNAVAILABLE / NOT_IMPLEMENTED`.

The shape read here is the one in `core-lite-status.v1.schema.json`
(`$defs.gpu_value` / `$defs.gpu_row`) and in the producer's own `GpuRow`, and
the two agree on every name: `index`, `label`, `gpu_util_percent`,
`memory_used_bytes`, `memory_total_bytes`, `temperature_c`, plus `gpus` and the
optional `sample_time`.

Everything here is a fixture. No device, no route, no `nvidia-smi`, no model
call: the reader is handed documents and asked what it makes of them.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for entry in (str(ROOT), str(HERE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from fleet_mcp import core_lite_readback as rb  # noqa: E402
import test_fleet_core_lite_readback as base  # noqa: E402

DEVICE = base.DEVICE
ACCOUNT = base.ACCOUNT


# --------------------------------------------------------------------------
# A producer-shaped row and value. Field names and bounds come from
# `core-lite/contract/core-lite-status.v1.schema.json`, not from this file.
# --------------------------------------------------------------------------

def row(**over) -> dict:
    card = {"index": 0, "label": "NVIDIA RTX A6000", "gpu_util_percent": 37.5,
            "memory_used_bytes": 2147483648, "memory_total_bytes": 51539607552,
            "temperature_c": 41.0}
    card.update(over)
    return card


def gpu_value(rows=None, *, sample_time=base.stamp()) -> dict:
    value: dict = {"gpus": [row()] if rows is None else rows}
    if sample_time is not None:
        value["sample_time"] = sample_time
    return value


def document(value=None, *, state="PRESENT", reason=None, completeness="COMPLETE",
             **row_over) -> dict:
    observation: dict = {
        "name": "gpu.readonly", "state": state,
        "source": "nodecore/core-lite nvidia-smi v1",
        "observed_at": base.stamp(),
    }
    if state == "PRESENT":
        observation["value"] = gpu_value(**row_over) if value is None else value
        observation["completeness"] = completeness
    else:
        observation["reason"] = reason or "NO_SOURCE"
    return base.doc(observations=[observation])


def gpu(document: dict) -> dict:
    return base.read(document)["observations"]["gpu.readonly"]


class TheRealProducerShapeIsReadAndKept(unittest.TestCase):
    def test_a_gpu_reading_is_present_with_its_rows(self):
        got = gpu(document())
        self.assertEqual(got["state"], "PRESENT")
        self.assertEqual(got["completeness"], "COMPLETE")
        rows = got["value"]["gpus"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["index"], 0)
        self.assertEqual(rows[0]["gpu_util_percent"], 37.5)
        self.assertEqual(rows[0]["memory_used_bytes"], 2147483648)
        self.assertEqual(rows[0]["memory_total_bytes"], 51539607552)
        self.assertEqual(rows[0]["temperature_c"], 41.0)

    def test_the_card_counters_survive_and_are_not_rounded(self):
        got_gpu = gpu(document(rows=[row(index=0, gpu_util_percent=0.0,
                                         temperature_c=-5.25)]))
        card = got_gpu["value"]["gpus"][0]
        # A measured zero is a reading, not an absence.
        self.assertEqual(card["gpu_util_percent"], 0.0)
        self.assertEqual(card["temperature_c"], -5.25)

    def test_two_cards_are_both_carried_in_order(self):
        rows = [row(index=0, label="NVIDIA RTX A6000"),
                row(index=1, label="NVIDIA GeForce RTX 4090",
                    gpu_util_percent=99.0)]
        got = gpu(document(rows=rows))
        self.assertEqual(got["state"], "PRESENT")
        self.assertEqual([c["index"] for c in got["value"]["gpus"]], [0, 1])

    def test_ordinary_model_names_are_not_mistaken_for_identifiers(self):
        for label in ("NVIDIA RTX A6000", "RTX 8000", "Tesla T4",
                      "NVIDIA A100-SXM4-80GB", "GeForce RTX 3090 (24GB)"):
            with self.subTest(label=label):
                got = gpu(document(rows=[row(label=label)]))
                self.assertEqual(got["state"], "PRESENT", label)
                self.assertEqual(got["value"]["gpus"][0]["label"], label)

    def test_sample_time_is_carried_and_is_not_observed_at(self):
        stamp_value = base.stamp(offset_seconds=-1.0)
        got = gpu(document(sample_time=stamp_value))
        self.assertEqual(got["value"]["sample_time"], stamp_value)

    def test_a_missing_sample_time_is_still_a_complete_reading(self):
        """The wire schema marks it optional and the producer omits it when its
        own clock gave it nothing — so its absence is not a partial reading."""
        got = gpu(document(sample_time=None))
        self.assertEqual(got["state"], "PRESENT")
        self.assertNotIn("sample_time", got["value"])
        self.assertEqual(got["completeness"], "COMPLETE")

    def test_the_value_the_old_empty_spec_used_to_strip_is_carried(self):
        """Before the fix this exact value came back PRESENT with `{}`.

        It is asserted positively here as well as across the class above,
        because "refused" and "carried" are different answers and the
        regression was the second one being reported as the first."""
        got = gpu(document(value={"gpus": [row()]}))
        self.assertEqual(got["state"], "PRESENT")
        self.assertEqual(len(got["value"]["gpus"]), 1)

    def test_a_real_value_logs_no_unknown_field(self):
        """The old spec's second symptom, and the one a status page would show.

        With `{}` for a spec, `_project_value` logged EVERY member of a genuine
        value as "a key this reader does not carry" — so a correct document
        produced a refusal on every read. A spec that is right for the shape is
        what stops that, and this is the assertion that fails without it.
        """
        for value in ({"gpus": [row()]},
                      {"gpus": [row()], "sample_time": base.stamp()}):
            with self.subTest(value=sorted(value)):
                refusals = base.refusals(base.read(document(value=value)))
                self.assertNotIn("UNKNOWN_FIELD", refusals, refusals)
                self.assertEqual(refusals, [])

    def test_the_projected_value_holds_nothing_the_schema_does_not(self):
        got = gpu(document())
        self.assertLessEqual(set(got["value"]), rb.GPU_VALUE_KEYS)
        self.assertLessEqual(set(got["value"]["gpus"][0]), rb.GPU_ROW_KEYS)


class AValueThisReaderCannotCarryIsNotReportedAsAReding(unittest.TestCase):
    """Every refusal below makes the WHOLE observation UNAVAILABLE.

    Not "PARTIAL", and never a zero: the contract has the producer refuse the
    sample when one card's answer does not survive, because a reader comparing
    two cards has no way to know a row went missing.
    """

    def assert_refused(self, value):
        document = base.doc(observations=[{
            "name": "gpu.readonly", "state": "PRESENT", "value": value,
            "completeness": "COMPLETE", "observed_at": base.stamp(),
        }])
        got = gpu(document)
        self.assertEqual(got["state"], "UNAVAILABLE", got)
        self.assertNotIn("value", got)
        self.assertTrue(base.refusals(base.read(document)),
                        "a refused value must leave a record")

    def test_an_unknown_member_on_the_value_is_refused(self):
        value = gpu_value()
        value["processes"] = [{"pid": 1}]
        self.assert_refused(value)

    def test_an_unknown_member_on_a_row_is_refused(self):
        self.assert_refused({"gpus": [row(uuid="GPU-1a2b3c4d-5e6f")]})

    def test_a_row_missing_a_required_field_is_refused(self):
        incomplete = row()
        del incomplete["temperature_c"]
        self.assert_refused({"gpus": [incomplete]})

    def test_an_empty_row_list_is_not_a_reading_of_zero_cards(self):
        self.assert_refused({"gpus": []})

    def test_gpus_must_be_a_list(self):
        self.assert_refused({"gpus": {"0": row()}})

    def test_more_rows_than_the_schema_allows_is_refused(self):
        rows = [row(index=i) for i in range(rb.GPU_MAX_ROWS + 1)]
        self.assert_refused({"gpus": rows})

    def test_two_answers_for_one_card_are_refused(self):
        self.assert_refused({"gpus": [row(index=3), row(index=3)]})

    def test_a_bad_index_is_refused(self):
        for bad in (-1, rb.GPU_MAX_INDEX + 1, 1.5, "0", None, True):
            with self.subTest(index=bad):
                self.assert_refused({"gpus": [row(index=bad)]})

    def test_a_label_that_is_not_a_model_name_is_refused(self):
        for bad in ("", "x" * (rb.GPU_LABEL_MAX + 1), "GPU-1a2b3c4d",
                    "nvidia;rm -rf /", "C:\\GPU", "/dev/nvidia0", "a:b",
                    "quote\"d", "line\nbreak", "café", "GPU-UUID-0123456789ab"):
            with self.subTest(label=bad):
                self.assert_refused({"gpus": [row(label=bad)]})

    def test_utilisation_outside_the_schema_range_is_refused(self):
        for bad in (-0.01, 100.01, "50", None, True, float("nan"),
                    float("inf")):
            with self.subTest(util=bad):
                self.assert_refused({"gpus": [row(gpu_util_percent=bad)]})

    def test_memory_that_is_not_a_non_negative_integer_is_refused(self):
        for bad in (-1, 1.5, "100", None, True):
            with self.subTest(memory=bad):
                self.assert_refused({"gpus": [row(memory_used_bytes=bad)]})
            with self.subTest(total=bad):
                self.assert_refused({"gpus": [row(memory_total_bytes=bad)]})

    def test_used_memory_above_total_is_refused(self):
        self.assert_refused({"gpus": [row(memory_used_bytes=2, memory_total_bytes=1)]})

    def test_a_temperature_the_driver_could_not_mean_is_refused(self):
        for bad in (-273.0, 9000.0, rb.GPU_TEMP_MIN - 1, rb.GPU_TEMP_MAX + 1,
                    "41", None, True, float("nan")):
            with self.subTest(temperature=bad):
                self.assert_refused({"gpus": [row(temperature_c=bad)]})

    def test_an_in_place_unavailable_marker_is_not_this_producers_shape(self):
        """The producer turns an `N/A` into a whole-sample UNAVAILABLE rather
        than a marker, so a marker here is a document this reader does not know
        — and carrying it would publish a card whose numbers nobody measured."""
        marker = {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}
        for field in ("gpu_util_percent", "memory_used_bytes",
                      "memory_total_bytes", "temperature_c"):
            with self.subTest(field=field):
                self.assert_refused({"gpus": [row(**{field: marker})]})

    def test_a_sample_time_this_reader_cannot_place_is_refused(self):
        for bad in ("", "   ", "not-a-time", "2026-10-16T10:00:00", 12345, ["x"]):
            with self.subTest(sample_time=bad):
                self.assert_refused(gpu_value(sample_time=bad))

    def test_a_second_bad_row_refuses_the_whole_sample(self):
        """One bad card is not a one-card reading."""
        rows = [row(index=0), row(index=1, temperature_c=9000.0)]
        self.assert_refused({"gpus": rows})


class AnAbsentReadingIsNeverAShapedEmptyOne(unittest.TestCase):
    def test_an_unavailable_row_carries_a_reason_and_no_value(self):
        document = base.doc(observations=[
            {"name": "gpu.readonly", "state": "UNAVAILABLE",
             "reason": "NO_SOURCE", "observed_at": base.stamp()}])
        got = gpu(document)
        self.assertEqual(got["state"], "UNAVAILABLE")
        self.assertEqual(got["reason"], "NO_SOURCE")
        self.assertNotIn("value", got)
        # Nothing was invented in its place — no zeros, no empty row list.
        self.assertNotIn("gpus", got)

    def test_not_implemented_is_still_reported_as_the_document_says(self):
        document = base.doc(observations=[
            {"name": "gpu.readonly", "state": "UNAVAILABLE",
             "reason": "NOT_IMPLEMENTED"}])
        got = gpu(document)
        self.assertEqual((got["state"], got["reason"]),
                         ("UNAVAILABLE", "NOT_IMPLEMENTED"))


class TheOtherShapesDoNotRegress(unittest.TestCase):
    """The six observations that were already aligned keep their own readings."""

    def test_the_five_scalar_shapes_still_project(self):
        document = base.doc(observations=[
            {"name": name, "state": "PRESENT", "value": value,
             "completeness": "COMPLETE", "observed_at": base.stamp()}
            for name, value in (
                ("cpu", {"used_percent": 9.1}),
                ("memory", {"total_bytes": 1000, "available_bytes": 400,
                            "free_bytes": 300, "used_percent": 60}),
                ("swap", {"total_bytes": 1, "free_bytes": 0, "used_bytes": 1,
                          "pages_in_per_second": 0.0,
                          "pages_out_per_second": 22.5}),
                ("psi.cpu", {"some": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0},
                             "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0}}),
            )])
        summary = base.read(document)["observations"]
        for name in ("cpu", "memory", "swap", "psi.cpu"):
            with self.subTest(observation=name):
                self.assertEqual(summary[name]["state"], "PRESENT")

    def test_gpu_is_the_only_shape_whose_spec_changed(self):
        self.assertEqual(rb.OBSERVATION_VALUE_KEYS["gpu.readonly"],
                         {"gpus": "gpu_rows", "sample_time": "sample_time"})
        self.assertEqual(rb.OBSERVATION_VALUE_KEYS["connector"], {})

    def test_the_row_bounds_mirror_the_wire_schema(self):
        self.assertEqual(rb.GPU_MAX_ROWS, 16)
        self.assertEqual(rb.GPU_MAX_INDEX, 4096)
        self.assertEqual(rb.GPU_LABEL_MAX, 64)
        self.assertEqual((rb.GPU_TEMP_MIN, rb.GPU_TEMP_MAX), (-40.0, 150.0))


if __name__ == "__main__":
    unittest.main()
