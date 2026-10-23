"""The Core-Lite reader trusts the ROUTE, and nothing the document says about itself.

Every test here is offline and pure: no host is touched, no socket is opened, no
other device is contacted, and the clock is an argument. The fixtures are
documents, not machines.

The single idea under test: a device Node Adapter reaches a Core over a route
Fleet already authenticated, and the JSON that comes back is a CLAIM. It names
its own device, its own principal and its own `live` flag, and a reader that
believes any of those has let a document promote itself. So the summary's
`device_ref` is the adapter's, its verdict rests on the adapter's route report,
and the document's own fields are reported as *declarations to compare*.
"""
from __future__ import annotations

import copy
import datetime as dt
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import core_lite_readback as rb  # noqa: E402

NOW = dt.datetime(2026, 10, 15, 15, 15, 0, tzinfo=dt.timezone.utc)
DEVICE = "device:tokyo-shared-dev-01"
ACCOUNT = "vince"


def stamp(offset_seconds: float = -2.0) -> str:
    return (NOW + dt.timedelta(seconds=offset_seconds)).isoformat()


def doc(**over) -> dict:
    """A well-formed document that asserts as much about itself as it can.

    It claims `live: true` and names its own device and principal. Everything
    the summary concludes must be reachable WITHOUT believing those.
    """
    base = {
        "schema": rb.SOURCE_SCHEMA,
        "node": DEVICE,
        "principal": ACCOUNT,
        "platform": "linux",
        "generation": 1,
        "mode": "observe-only",
        "observed_at": stamp(),
        "freshness": "NOT_ASSERTED",
        "principal_identity": {"state": "MATCHED", "basis": "ATTESTED",
                               "claimed": ACCOUNT, "observed": ACCOUNT},
        "device_identity": {"state": "MATCHED", "basis": "LOCAL_DECLARATION",
                            "claimed": DEVICE},
        "live": True,
        "observations": [
            {"name": "cpu", "state": "PRESENT",
             "source": "nodecore/NodeResourceSnapshot v1",
             "observed_at": stamp(), "value": {"used_percent": 9.1}},
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300, "used_percent": 60}},
        ],
    }
    base.update(over)
    return base


def read(document: object, *, verified_route: bool = True,
         received_at: dt.datetime = NOW, device: str = DEVICE,
         account: str | None = ACCOUNT, **kw) -> dict:
    return rb.readback(document, expected_device_ref=device,
                       binding_account=account, received_at=received_at,
                       verified_route=verified_route, **kw)


def refusals(summary: dict) -> list[str]:
    return [item["kind"] for item in summary["refusals"]]


class IdentityIsTakenFromTheRouteAndNotTheDocumentTest(unittest.TestCase):
    def test_a_document_naming_another_device_is_refused(self):
        """Device spoof: the summary keeps the route's device, not the claim."""
        summary = read(doc(node="device:llm-lab-dell"))
        self.assertEqual(summary["device_ref"], DEVICE)
        self.assertEqual(summary["device"]["state"], "MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertIn("DEVICE_MISMATCH", refusals(summary))
        # ...and the readings it carried do NOT become this device's.
        self.assertEqual(summary["live"]["accepted"], False)

    def test_a_spoofed_device_name_is_not_echoed_at_all(self):
        """`MISMATCH` is the report; the claimed string is not carried out.

        The state says a mismatch happened, which is everything an operator
        needs. Echoing the claim as well would make the summary a channel the
        document writes into.
        """
        summary = read(doc(node="canary-device-DEADBEEF"))
        self.assertIsNone(summary["device"]["declared"])
        self.assertNotIn("canary-device-DEADBEEF", repr(summary))

    def test_a_document_naming_another_principal_is_refused(self):
        summary = read(doc(principal="fjd"))
        self.assertEqual(summary["principal"]["state"], "MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertIn("PRINCIPAL_MISMATCH", refusals(summary))

    def test_a_spoofed_principal_name_is_not_echoed_at_all(self):
        summary = read(doc(principal="canary-principal-DEADBEEF"))
        self.assertIsNone(summary["principal"]["declared"])
        self.assertNotIn("canary-principal-DEADBEEF", repr(summary))

    def test_an_absent_binding_account_is_unknown_and_never_matched(self):
        """No owner truth on the Fleet side means no verdict, not a pass."""
        summary = read(doc(principal=ACCOUNT), account=None)
        self.assertEqual(summary["principal"]["state"], "UNKNOWN")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertNotEqual(summary["connected"], "CONNECTED")

    def test_the_second_device_cannot_be_read_under_the_first_ones_row(self):
        """Two devices must not cross-map: the row is addressed by the route."""
        first = read(doc(node=DEVICE))
        second = read(doc(node="device:llm-lab-dell"), device="device:llm-lab-dell")
        self.assertEqual(first["device_ref"], DEVICE)
        self.assertEqual(second["device_ref"], "device:llm-lab-dell")
        self.assertEqual(second["device"]["state"], "MATCHED")
        self.assertEqual(second["connected"], "CONNECTED")
        # The same document, read for the other device, is a mismatch — which
        # is what makes the pairing a fact about the route.
        crossed = read(doc(node=DEVICE), device="device:llm-lab-dell")
        self.assertEqual(crossed["device"]["state"], "MISMATCH")

    def test_a_document_declaring_no_device_is_unknown_not_a_default(self):
        summary = read(doc(node=None))
        self.assertIsNone(summary["device"]["declared"])
        self.assertEqual(summary["device"]["state"], "UNKNOWN")
        self.assertEqual(summary["connected"], "UNKNOWN")


class LiveIsNeverRaisedByTheDocumentTest(unittest.TestCase):
    def test_a_claimed_live_without_a_trusted_route_is_not_accepted(self):
        """The document asserts `live: true`; an unverified route cannot carry it."""
        summary = read(doc(), verified_route=False)
        self.assertTrue(summary["live"]["claimed"])
        self.assertFalse(summary["live"]["accepted"])
        self.assertEqual(summary["connected"], "NOT_CONNECTED")

    def test_the_same_document_under_a_trusted_route_is_connected(self):
        """Both halves asserted, so the route flag is the thing doing the work."""
        untrusted = read(doc(), verified_route=False)
        trusted = read(doc(), verified_route=True)
        self.assertEqual(untrusted["connected"], "NOT_CONNECTED")
        self.assertEqual(trusted["connected"], "CONNECTED")
        self.assertTrue(trusted["live"]["accepted"])

    def test_a_local_declaration_basis_can_never_raise_the_verdict(self):
        """The contract's own point: the account agreeing with itself is not proof."""
        summary = read(doc())
        self.assertEqual(summary["source"]["declared_device_basis"],
                         "LOCAL_DECLARATION")
        self.assertEqual(summary["device"]["state"], "MATCHED")
        # `MATCHED` above is the ROUTE's verdict. The declared basis is carried
        # for context and contributes nothing to it.
        self.assertEqual(summary["connected"], "CONNECTED")

    def test_a_source_that_withholds_live_is_not_overridden_upwards(self):
        summary = read(doc(live=False))
        self.assertFalse(summary["live"]["claimed"])
        self.assertFalse(summary["live"]["accepted"])

    def test_a_connected_device_with_no_readings_is_not_unhealthy(self):
        """Connection and observation are different questions."""
        summary = read(doc(observations=[]))
        self.assertEqual(summary["connected"], "CONNECTED")
        self.assertEqual(summary["observations"], {})


class FreshnessIsTheCallersBoundTest(unittest.TestCase):
    def test_a_stale_stamp_is_refused(self):
        summary = read(doc(observed_at=stamp(-3600)))
        self.assertEqual(summary["freshness"]["state"], "STALE")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertIn("STALE_TIMESTAMP", refusals(summary))

    def test_a_future_stamp_is_refused(self):
        summary = read(doc(observed_at=stamp(3600)))
        self.assertEqual(summary["freshness"]["state"], "FUTURE")
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertIn("FUTURE_TIMESTAMP", refusals(summary))

    def test_a_missing_stamp_is_unknown_and_not_fresh(self):
        summary = read(doc(observed_at=None))
        self.assertEqual(summary["freshness"]["state"], "NO_TIMESTAMP")
        self.assertEqual(summary["connected"], "UNKNOWN")

    def test_a_stamp_without_a_timezone_is_not_a_time(self):
        """Reading a zoneless stamp as UTC would move it; as 'now' would never age."""
        summary = read(doc(observed_at="2026-10-15T15:14:58"))
        self.assertEqual(summary["freshness"]["state"], "NO_TIMESTAMP")

    def test_the_bound_is_an_argument(self):
        summary = read(doc(observed_at=stamp(-30)), max_age_seconds=10)
        self.assertEqual(summary["freshness"]["state"], "STALE")


class ObservationsAreProjectedNotForwardedTest(unittest.TestCase):
    def test_a_zero_is_a_measurement_and_survives(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 0}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "PRESENT")
        self.assertEqual(summary["observations"]["cpu"]["value"],
                         {"used_percent": 0})

    def test_a_negative_byte_count_makes_the_whole_block_unavailable(self):
        summary = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1000, "available_bytes": -1}}]))
        row = summary["observations"]["memory"]
        self.assertEqual(row["state"], "UNAVAILABLE")
        self.assertEqual(row["reason"], "NO_SOURCE")
        self.assertNotIn("value", row)
        self.assertIn("MALFORMED_VALUE", refusals(summary))

    def test_a_bool_is_not_a_percentage(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": True}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")

    def test_a_bool_is_not_a_byte_count_either(self):
        """`True` is an `int` in Python, and a byte count of 1 is a real reading.

        The two paths are separate, so both are asserted: a percentage goes
        through the range check and a byte count does not, which is exactly how
        a bool slips past one of them.
        """
        summary = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": True, "available_bytes": False}}]))
        self.assertEqual(summary["observations"]["memory"]["state"],
                         "UNAVAILABLE")
        self.assertIn("MALFORMED_VALUE", refusals(summary))

    def test_a_bool_in_a_storage_row_is_refused(self):
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "value": {"rows": [{"role": "root", "total_bytes": True}]}}]))
        self.assertEqual(summary["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")

    def test_a_nan_percent_is_refused(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT",
             "value": {"used_percent": float("nan")}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")

    def test_a_percent_over_one_hundred_is_refused(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 340}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")

    def test_a_float_byte_count_is_refused(self):
        """The contract's own example: a byte count written as 1.5."""
        summary = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1.5}}]))
        self.assertEqual(summary["observations"]["memory"]["state"],
                         "UNAVAILABLE")

    def test_a_partial_reading_is_all_or_nothing(self):
        """Three of four memory numbers must not be published for arithmetic.

        `cpu` and `memory` are the two observations the native adapter projects
        all-or-nothing: it either has every field or it has no reading. So a
        value carrying two of four is not a short reading, it is a document this
        reader does not recognise — and it must not be published as a whole one.
        """
        whole = read(doc(observations=[
            {"name": "memory", "state": "PRESENT", "completeness": "COMPLETE",
             "value": {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300, "used_percent": 60}}]))
        self.assertEqual(whole["observations"]["memory"]["state"], "PRESENT")
        for value in ({"total_bytes": 1000, "available_bytes": 400},
                      {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300},
                      {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300, "used_percent": 900}):
            with self.subTest(value=value):
                summary = read(doc(observations=[
                    {"name": "memory", "state": "PRESENT",
                     "completeness": "COMPLETE", "value": value}]))
                self.assertEqual(summary["observations"]["memory"]["state"],
                                 "UNAVAILABLE")

    def test_cpu_and_memory_may_not_carry_an_in_place_marker(self):
        """Only swap and psi.* do that; a marker here is a shape we do not know."""
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "completeness": "PARTIAL",
             "value": {"used_percent": {"state": "UNAVAILABLE",
                                        "reason": "NO_SOURCE"}}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")

    def test_an_unavailable_reading_keeps_its_closed_reason(self):
        summary = read(doc(observations=[
            {"name": "swap", "state": "UNAVAILABLE", "reason": "NOT_IMPLEMENTED"}]))
        row = summary["observations"]["swap"]
        self.assertEqual(row["state"], "UNAVAILABLE")
        self.assertEqual(row["reason"], "NOT_IMPLEMENTED")
        self.assertNotIn("value", row)
        self.assertIn("swap", summary["observations_unavailable"])

    def test_a_typo_in_a_reason_becomes_no_source(self):
        """A typo must not become a vocabulary word a reader has never seen."""
        summary = read(doc(observations=[
            {"name": "swap", "state": "UNAVAILABLE", "reason": "NOT_IMPLMENTED"}]))
        self.assertEqual(summary["observations"]["swap"]["reason"], "NO_SOURCE")
        self.assertIn("UNKNOWN_REASON", refusals(summary))

    def test_windows_psi_is_unsupported_platform_and_is_not_dropped(self):
        """The two words are deliberately different; a reader must keep them."""
        summary = read(doc(platform="windows", observations=[
            {"name": "psi.memory", "state": "UNAVAILABLE",
             "reason": "UNSUPPORTED_PLATFORM"},
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 4}}]))
        row = summary["observations"]["psi.memory"]
        self.assertEqual(row["reason"], "UNSUPPORTED_PLATFORM")
        self.assertNotEqual(row["reason"], "NOT_IMPLEMENTED")
        # An observation the profile did not grant does not appear at all; one
        # that appears is reported. Silence is never manufactured here.
        self.assertIn("psi.memory", summary["observations"])

    def test_an_unknown_state_is_refused_rather_than_guessed(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "FINE", "value": {"used_percent": 1}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")
        self.assertIn("UNKNOWN_STATE", refusals(summary))

    def test_a_duplicated_observation_name_is_refused(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 1}},
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 2}}]))
        self.assertEqual(summary["observations"]["cpu"]["state"], "UNAVAILABLE")
        self.assertEqual(summary["observations"]["cpu"]["reason"], "NO_SOURCE")
        self.assertIn("DUPLICATE_OBSERVATION", refusals(summary))

    def test_an_observation_name_outside_the_contract_is_dropped(self):
        summary = read(doc(observations=[
            {"name": "gpu.secret", "state": "PRESENT", "value": {"anything": 1}}]))
        self.assertNotIn("gpu.secret", summary["observations"])
        self.assertIn("UNKNOWN_OBSERVATION", refusals(summary))

    def test_a_missing_observation_is_absent_not_zero(self):
        """A fact nobody measured must not become 0 or a healthy row."""
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT", "value": {"used_percent": 3}}]))
        self.assertNotIn("swap", summary["observations"])
        self.assertNotIn("psi.io", summary["observations"])


class StorageRowsAreBoundedAndRoleCheckedTest(unittest.TestCase):
    def test_a_valid_role_row_survives(self):
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": [
                 {"role": "root", "total_bytes": 10,
                  "available_bytes": 4, "used_bytes": 6}]}}}]))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["value"]["filesystems"]["roles"][0]["role"], "root")

    def test_both_storage_blocks_survive_together(self):
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": [
                          {"role": "root", "total_bytes": 10,
                           "available_bytes": 4, "used_bytes": 6}]},
                       "disk_io": {"roles": [
                          {"role": "root", "read_bytes": 1, "write_bytes": 2}]}}}]))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(sorted(row["value"]), ["disk_io", "filesystems"])

    def test_a_device_name_role_is_refused(self):
        """`vda2` is a raw hardware identifier the role exists to replace."""
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": [
                 {"role": "/dev/vda2", "total_bytes": 10,
                  "available_bytes": 4, "used_bytes": 6}]}}}]))
        self.assertEqual(summary["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")

    def test_a_path_key_in_a_storage_row_is_stripped(self):
        """The keys the producer never forwards cannot survive a round trip."""
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": [
                 {"role": "root", "total_bytes": 10, "available_bytes": 4,
                  "used_bytes": 6, "mount_point": "/", "path": "/dev/vda2"}]}}}]))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertNotIn("mount_point", row["value"]["filesystems"]["roles"][0])
        self.assertNotIn("path", row["value"]["filesystems"]["roles"][0])
        self.assertIn("UNKNOWN_FIELD", refusals(summary))

    def test_a_row_missing_a_byte_count_makes_the_block_unavailable(self):
        """A partial row is a row the producer would have dropped whole."""
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": [
                 {"role": "root", "total_bytes": 10}]}}}]))
        self.assertEqual(summary["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")

    def test_too_many_rows_makes_the_block_unavailable(self):
        rows = [{"role": f"r{i}", "total_bytes": 1, "available_bytes": 1,
                 "used_bytes": 1} for i in range(rb.MAX_STORAGE_ROWS + 1)]
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "completeness": "COMPLETE",
             "value": {"filesystems": {"roles": rows}}}]))
        self.assertEqual(summary["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")


class NothingUntrustedRidesOutTest(unittest.TestCase):
    def test_a_token_or_config_path_in_the_document_is_stripped(self):
        """Projection, not filtering: only named keys survive."""
        summary = read(doc(ops_token="ccp_deadbeef",
                           config_path="/home/vince/.secrets/owner-pat",
                           argv=["--auth-key", "tskey-secret"]))
        rendered = repr(summary)
        self.assertNotIn("ccp_deadbeef", rendered)
        self.assertNotIn("owner-pat", rendered)
        self.assertNotIn("tskey-secret", rendered)
        self.assertIn("UNKNOWN_FIELD", refusals(summary))

    def test_an_extra_member_inside_a_reading_is_stripped(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT",
             "value": {"used_percent": 1, "token": "s3cr3t"},
             "argv": ["--x"]}]))
        rendered = repr(summary)
        self.assertNotIn("s3cr3t", rendered)
        self.assertEqual(summary["observations"]["cpu"]["value"],
                         {"used_percent": 1})

    def test_the_note_about_a_rejected_value_does_not_quote_it(self):
        summary = read(doc(observations=[
            {"name": "cpu", "state": "PRESENT",
             "value": {"used_percent": -999999}}]))
        for item in summary["refusals"]:
            self.assertNotIn("999999", item["note"])

    def test_a_non_object_document_is_unknown_rather_than_an_exception(self):
        for value in ("not json", [], 42, None):
            with self.subTest(value=value):
                summary = read(value)
                self.assertEqual(summary["connected"], "UNKNOWN")
                self.assertEqual(summary["live"]["accepted"], False)


class TheClosedDiscriminatorsFailTheWholeReadbackClosedTest(unittest.TestCase):
    """A refusal that does not reach the verdict is a note, not a gate.

    Each case below is otherwise PERFECT: a verified route, a device and
    principal that match, a fresh stamp, and `live: true`. Only the one
    discriminator is wrong — which is exactly the shape that used to come back
    `CONNECTED` with the source's own `live` accepted.
    """

    def test_a_foreign_schema_on_a_perfect_route_is_never_connected(self):
        summary = read(doc(schema="serveros/other-thing.v9"))
        self.assertNotEqual(summary["connected"], "CONNECTED")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])
        self.assertIn("UNKNOWN_SCHEMA", refusals(summary))

    def test_an_acting_mode_on_a_perfect_route_is_never_connected(self):
        """V1 has no acting mode; a document claiming one is not this reading."""
        summary = read(doc(mode="observe-and-act"))
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])
        self.assertIn("UNKNOWN_MODE", refusals(summary))

    def test_a_missing_mode_is_refused(self):
        summary = read(doc(mode=None))
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])

    def test_an_unrecognised_platform_is_refused(self):
        summary = read(doc(platform="plan9"))
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])
        self.assertIn("UNKNOWN_PLATFORM", refusals(summary))

    def test_the_recognised_values_are_the_only_ones_that_pass(self):
        """Both halves, so the closed sets are gates rather than decorations."""
        for schema in (rb.SOURCE_SCHEMA,):
            for mode in sorted(rb.MODES):
                for platform in sorted(rb.PLATFORMS):
                    with self.subTest(schema=schema, mode=mode, platform=platform):
                        summary = read(doc(schema=schema, mode=mode,
                                           platform=platform))
                        self.assertEqual(summary["connected"], "CONNECTED")
                        self.assertTrue(summary["live"]["accepted"])
        self.assertEqual(rb.MODES, {"observe-only"})
        self.assertEqual(rb.PLATFORMS, {"linux", "windows"})


class NoUntrustedStringRidesOutThroughTheMetadataTest(unittest.TestCase):
    """Five metadata fields, five distinct canaries, zero leakage.

    Every one of these used to be echoed verbatim, so a document could park a
    token, a credential path or an argv fragment in `platform` and have it ride
    out through the summary while the value projector closed the observations.
    """

    CANARIES = {
        "schema": "canary-schema-AAAAAAAA",
        "mode": "canary-mode-BBBBBBBB",
        "platform": "canary-platform-CCCCCCCC",
        "node": "canary-device-DDDDDDDD",
        "principal": "canary-principal-EEEEEEEE",
    }

    def test_not_one_canary_survives_into_the_summary(self):
        summary = read(doc(
            schema=self.CANARIES["schema"],
            mode=self.CANARIES["mode"],
            platform=self.CANARIES["platform"],
            node=self.CANARIES["node"],
            principal=self.CANARIES["principal"],
        ))
        rendered = repr(summary)
        for field, canary in self.CANARIES.items():
            with self.subTest(field=field):
                self.assertNotIn(canary, rendered)
        # And each is reported as an unrecognised/mismatched thing instead.
        self.assertIsNone(summary["source"]["schema"])
        self.assertIsNone(summary["source"]["mode"])
        self.assertIsNone(summary["source"]["platform"])
        self.assertIsNone(summary["device"]["declared"])
        self.assertIsNone(summary["principal"]["declared"])
        self.assertEqual(summary["device"]["state"], "MISMATCH")
        self.assertEqual(summary["principal"]["state"], "MISMATCH")
        self.assertEqual(summary["connected"], "NOT_CONNECTED")
        self.assertFalse(summary["live"]["accepted"])

    def test_a_canary_in_an_unknown_member_name_is_not_echoed(self):
        """The refusal's `where` is part of the summary too.

        `{"<canary>": 1}` would otherwise smuggle its own KEY out through the
        very record saying the key was dropped.
        """
        document = doc()
        document["canary-toplevel-FFFFFFFF"] = 1
        document["observations"][0]["value"]["canary-valuekey-GGGGGGGG"] = 1
        document["observations"][0]["canary-rowkey-HHHHHHHH"] = 1
        summary = read(document)
        rendered = repr(summary)
        for canary in ("canary-toplevel-FFFFFFFF", "canary-valuekey-GGGGGGGG",
                       "canary-rowkey-HHHHHHHH"):
            with self.subTest(canary=canary):
                self.assertNotIn(canary, rendered)
        self.assertIn("UNKNOWN_FIELD", refusals(summary))

    def test_a_canary_in_a_storage_row_key_is_not_echoed(self):
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "value": {"rows": [{"role": "root", "total_bytes": 1,
                                 "canary-rowkey-IIIIIIII": 1}]}}]))
        self.assertNotIn("canary-rowkey-IIIIIIII", repr(summary))

    def test_an_unknown_observation_name_is_not_echoed(self):
        summary = read(doc(observations=[
            {"name": "canary-obsname-JJJJJJJJ", "state": "PRESENT"}]))
        self.assertNotIn("canary-obsname-JJJJJJJJ", repr(summary))

    def test_a_canary_in_a_reason_is_not_echoed(self):
        summary = read(doc(observations=[
            {"name": "swap", "state": "UNAVAILABLE",
             "reason": "canary-reason-KKKKKKKK"}]))
        self.assertNotIn("canary-reason-KKKKKKKK", repr(summary))
        self.assertEqual(summary["observations"]["swap"]["reason"], "NO_SOURCE")

    def test_a_canary_in_a_device_identity_basis_is_not_echoed(self):
        document = doc()
        document["device_identity"] = {"state": "MATCHED",
                                       "basis": "canary-basis-LLLLLLLL"}
        document["principal_identity"] = {"state": "MATCHED",
                                          "basis": "canary-basis-MMMMMMMM"}
        summary = read(document)
        self.assertNotIn("canary-basis-LLLLLLLL", repr(summary))
        self.assertNotIn("canary-basis-MMMMMMMM", repr(summary))
        self.assertIsNone(summary["source"]["declared_device_basis"])
        self.assertIsNone(summary["principal"]["basis"])

    def test_the_recognised_values_are_what_gets_carried_out(self):
        """Not nothing: the point is that only recognised values come through."""
        summary = read(doc())
        self.assertEqual(summary["source"]["schema"], rb.SOURCE_SCHEMA)
        self.assertEqual(summary["source"]["mode"], "observe-only")
        self.assertEqual(summary["source"]["platform"], "linux")
        self.assertEqual(summary["device"]["declared"], DEVICE)
        self.assertEqual(summary["principal"]["declared"], ACCOUNT)



class TheRealServicesShapeIsReadTest(unittest.TestCase):
    """The service rows `native/node-core` PR #217 actually emits.

    Transcribed from `core-lite/src/service_readings.cpp` (the row builder and
    the state table) rather than from prose. Until #217 landed a real producer,
    this reader declared `services` with an empty value map — so a genuine
    service reading came back PRESENT with an EMPTY value: success-shaped, and
    silently empty. That is the failure these tests exist to keep visible.
    """

    #: The producer's own `kServiceStates` minus `UNKNOWN`, which it refuses
    #: rather than publishes, plus the row-level `UNAVAILABLE` marker.
    STATES = ("ACTIVE", "INACTIVE", "FAILED", "ACTIVATING", "DEACTIVATING",
              "NOT_INSTALLED", "UNAVAILABLE")

    ROWS = [
        {"id": "fleet-node", "unit": "serveros-fleet-node.service",
         "state": "ACTIVE", "sub_state": "running",
         "observed_at": "2026-10-16T01:00:00+00:00", "age_seconds": 3,
         "source": "systemd-user"},
        {"id": "other-node", "unit": "serveros-other-node.service",
         "state": "INACTIVE", "sub_state": "dead", "age_seconds": 900,
         "source": "systemd-user"},
        {"id": "not-there", "unit": "serveros-absent.service",
         "state": "NOT_INSTALLED", "source": "systemd-user"},
    ]

    def document(self, rows=None, **over) -> dict:
        document = doc()
        document["observations"] = [
            {"name": "services", "state": "PRESENT", "completeness": "COMPLETE",
             "source": "nodecore/NodeResourceSnapshot v1",
             "value": {"services": rows if rows is not None else self.ROWS}},
        ]
        document.update(over)
        return document

    def services(self, document) -> dict:
        return read(document)["observations"]["services"]

    def test_a_real_services_value_is_read_and_not_dropped(self):
        row = self.services(self.document())
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "COMPLETE")
        self.assertEqual([r["id"] for r in row["value"]["services"]],
                         ["fleet-node", "other-node", "not-there"])
        self.assertEqual(row["value"]["services"][0]["unit"],
                         "serveros-fleet-node.service")

    def test_every_state_word_survives_exactly(self):
        rows = []
        for i, word in enumerate(self.STATES):
            row = {"id": f"s{i}", "unit": f"unit-{i}.service", "state": word}
            if word == "UNAVAILABLE":
                # The producer's marker always carries the closed reason with it.
                row["reason"] = "NO_SOURCE"
            rows.append(row)
        row = self.services(self.document(rows))
        self.assertEqual([r["state"] for r in row["value"]["services"]],
                         list(self.STATES))

    def test_not_installed_is_not_inactive(self):
        """The distinction the producer added a word for."""
        rows = [{"id": "a", "unit": "a.service", "state": "NOT_INSTALLED"},
                {"id": "b", "unit": "b.service", "state": "INACTIVE"}]
        got = self.services(self.document(rows))["value"]["services"]
        self.assertEqual(got[0]["state"], "NOT_INSTALLED")
        self.assertEqual(got[1]["state"], "INACTIVE")
        self.assertNotEqual(got[0]["state"], got[1]["state"])

    def test_the_unknown_state_word_the_producer_refuses_is_refused(self):
        """`UNKNOWN` is in the producer's table and never in its output."""
        rows = [{"id": "a", "unit": "a.service", "state": "UNKNOWN"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_a_missing_state_is_refused(self):
        rows = [{"id": "a", "unit": "a.service"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_an_inline_unreadable_row_makes_the_reading_partial(self):
        """One service readable and another not is exactly the PARTIAL case."""
        rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                 "observed_at": "2026-10-16T01:00:00+00:00", "age_seconds": 1,
                 "source": "systemd-user"},
                {"id": "b", "unit": "b.service", "state": "UNAVAILABLE",
                 "reason": "NO_SOURCE"}]
        row = self.services(self.document(rows))
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertEqual(row["value"]["services"][1],
                         {"id": "b", "unit": "b.service", "state": "UNAVAILABLE",
                          "reason": "NO_SOURCE"})
        # The unreadable row carries nothing else — no stamp for a reading that
        # was never taken.
        self.assertNotIn("age_seconds", row["value"]["services"][1])

    def test_a_claim_of_complete_over_a_partial_list_is_not_believed(self):
        rows = [{"id": "a", "unit": "a.service", "state": "UNAVAILABLE",
                 "reason": "NO_SOURCE"}]
        document = self.document(rows)
        row = self.services(document)
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertIn("COMPLETENESS_MISMATCH", refusals(read(document)))

    def test_an_unreadable_row_must_carry_a_closed_reason(self):
        rows = [{"id": "a", "unit": "a.service", "state": "UNAVAILABLE"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")
        rows = [{"id": "a", "unit": "a.service", "state": "UNAVAILABLE",
                 "reason": "because the sky is blue"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_an_empty_list_is_a_reading_and_means_this_account_runs_none(self):
        """Distinct from the observation being absent, and from UNAVAILABLE."""
        row = self.services(self.document([]))
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "COMPLETE")
        self.assertEqual(row["value"], {"services": []})

    def test_the_observation_level_unavailable_is_not_an_empty_list(self):
        """Declared, and none of it is this account's — a different answer."""
        document = doc(observations=[
            {"name": "services", "state": "UNAVAILABLE",
             "reason": "IDENTITY_UNKNOWN"}])
        row = self.services(document)
        self.assertEqual(row["state"], "UNAVAILABLE")
        self.assertEqual(row["reason"], "IDENTITY_UNKNOWN")
        self.assertNotIn("value", row)
        self.assertNotEqual(row.get("value"), {"services": []})

    def test_a_cross_account_service_list_that_reads_as_unavailable(self):
        document = doc(observations=[
            {"name": "services", "state": "UNAVAILABLE", "reason": "NO_SOURCE"}])
        row = self.services(document)
        self.assertEqual(row["reason"], "NO_SOURCE")
        self.assertNotEqual(row["state"], "PRESENT")

    def test_a_duplicated_service_id_is_refused(self):
        """Two rows with one id is one of them unreachable."""
        rows = [{"id": "same", "unit": "a.service", "state": "ACTIVE"},
                {"id": "same", "unit": "b.service", "state": "INACTIVE"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_a_malicious_id_is_refused(self):
        for bad in ("../../etc/passwd", "a b", "a\u0000b", "a/b", "-x" * 40,
                    "id;rm -rf /", ""):
            with self.subTest(bad=bad):
                rows = [{"id": bad, "unit": "a.service", "state": "ACTIVE"}]
                self.assertEqual(self.services(self.document(rows))["state"],
                                 "UNAVAILABLE")

    def test_a_malicious_unit_is_refused(self):
        for bad in ("../../etc/passwd", "/bin/sh.service", "a.service-evil",
                    "a b.service", "a;b.service", "a.service ",
                    "x" * 130 + ".service", ".service", "a..b.service",
                    "a.service\u0000"):
            with self.subTest(bad=bad):
                rows = [{"id": "a", "unit": bad, "state": "ACTIVE"}]
                self.assertEqual(self.services(self.document(rows))["state"],
                                 "UNAVAILABLE")

    def test_the_accepted_unit_shape_is_the_producers_own_rule(self):
        for good in ("a.service", "serveros-fleet-node.service",
                     "a@b.service", "a_b-c.d:e.service"):
            with self.subTest(good=good):
                rows = [{"id": "a", "unit": good, "state": "ACTIVE"}]
                self.assertEqual(self.services(self.document(rows))["state"],
                                 "PRESENT")

    def test_sub_state_is_not_carried(self):
        """It is `SubState=` copied out of `systemctl show` — free text."""
        row = self.services(self.document())["value"]["services"][0]
        self.assertNotIn("sub_state", row)
        self.assertEqual(sorted(row), ["age_seconds", "id", "observed_at",
                                       "source", "state", "unit"])

    def test_an_unknown_source_word_is_refused(self):
        rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                 "source": "/usr/bin/systemctl"}]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_age_seconds_must_be_a_non_negative_integer(self):
        for bad in (-1, 1.5, True, "3", None):
            with self.subTest(bad=bad):
                rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                         "age_seconds": bad}]
                row = self.services(self.document(rows))
                if bad is None:
                    self.assertEqual(row["state"], "PRESENT")
                else:
                    self.assertEqual(row["state"], "UNAVAILABLE")

    def test_observed_at_must_be_an_absolute_timestamp(self):
        for bad in ("2026-10-16T01:00:00", "yesterday", "", 12345):
            with self.subTest(bad=bad):
                rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                         "observed_at": bad}]
                self.assertEqual(self.services(self.document(rows))["state"],
                                 "UNAVAILABLE")

    def test_too_many_rows_makes_the_reading_unavailable(self):
        rows = [{"id": f"s{i}", "unit": f"u{i}.service", "state": "ACTIVE"}
                for i in range(rb.MAX_SERVICE_ROWS + 1)]
        self.assertEqual(self.services(self.document(rows))["state"], "UNAVAILABLE")

    def test_the_row_bound_is_the_one_the_producer_uses(self):
        self.assertEqual(rb.MAX_SERVICE_ROWS, 64)
        rows = [{"id": f"s{i}", "unit": f"u{i}.service", "state": "ACTIVE"}
                for i in range(rb.MAX_SERVICE_ROWS)]
        self.assertEqual(self.services(self.document(rows))["state"], "PRESENT")

    def test_extra_fields_on_a_row_are_dropped_without_leaking(self):
        canary = "canary-service-77777777"
        rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                 canary: 1, "token": canary, "argv": [canary, "--auth-key", canary]}]
        summary = read(self.document(rows))
        text = repr(summary)
        self.assertNotIn(canary, text)
        self.assertNotIn("--auth-key", text)
        self.assertIn("UNKNOWN_FIELD", refusals(summary))
        self.assertEqual(summary["observations"]["services"]["state"], "PRESENT")

    def test_a_canary_in_sub_state_is_not_carried(self):
        canary = "canary-substate-88888888"
        rows = [{"id": "a", "unit": "a.service", "state": "ACTIVE",
                 "sub_state": canary}]
        summary = read(self.document(rows))
        self.assertNotIn(canary, repr(summary))
        self.assertEqual(summary["observations"]["services"]["state"], "PRESENT")

    def test_a_service_reading_does_not_raise_the_observation_ceiling(self):
        """A stale sample does not become a connected device."""
        stale = self.document(rows=self.ROWS, observed_at=stamp(-3600))
        summary = read(stale)
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertEqual(summary["observations"]["services"]["state"], "PRESENT")
        unverified = read(self.document(), verified_route=False)
        self.assertEqual(unverified["connected"], "NOT_CONNECTED")


class TheReaderStaysPureTest(unittest.TestCase):
    def test_no_source_is_wired_means_unknown_and_not_healthy(self):
        summary = rb.unconnected_summary(expected_device_ref=DEVICE,
                                         binding_account=ACCOUNT,
                                         received_at=NOW)
        self.assertEqual(summary["connected"], "UNKNOWN")
        self.assertFalse(summary["live"]["accepted"])
        self.assertEqual(summary["observations"], {})
        self.assertEqual(summary["source"]["verified_route"], False)

    def test_the_module_reaches_no_socket_no_process_and_no_file(self):
        """A static guard on the CODE, not the prose.

        The module's own docstring says it opens no socket, so a substring
        search would fail on the comment explaining the property. This reads the
        syntax tree instead: the names actually imported, and the calls actually
        made. A comment may promise anything; an import is what could deliver.
        """
        import ast
        path = ROOT / "fleet_mcp" / "core_lite_readback.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        banned = {"socket", "subprocess", "urllib", "http", "requests",
                  "os", "shutil", "pathlib", "ssl", "ftplib", "asyncio"}
        self.assertEqual(imported & banned, set(),
                         f"the reader imported something that can leave: {imported & banned}")
        # The only imports it has are the two the arithmetic needs.
        self.assertEqual(imported, {"__future__", "datetime", "math", "re",
                                    "dataclasses", "typing"})

        called: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
        for forbidden in ("open", "exec", "eval", "compile", "__import__"):
            with self.subTest(call=forbidden):
                self.assertNotIn(forbidden, called)

    def test_the_summary_never_uses_the_word_healthy(self):
        """There is no health verdict here, and the vocabulary says so.

        Checked against the module's string CONSTANTS rather than its text: the
        docstring says it never says "healthy", and a grep for the word would
        fail on the sentence explaining that. A vocabulary, though, is made of
        literals — and none of those may be it.
        """
        import ast
        path = ROOT / "fleet_mcp" / "core_lite_readback.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                first = node.body[0] if node.body else None
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))

        literals = [node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docstrings]
        for text in literals:
            with self.subTest(literal=text[:40]):
                self.assertNotIn("health", text.lower())

        summary = read(doc())
        self.assertNotIn("health", summary)
        self.assertNotIn("healthy", repr(summary["connected"]))

    def test_the_same_inputs_give_the_same_summary(self):
        """Purity: the clock is an argument, so nothing varies unstated."""
        first = read(doc())
        second = read(doc())
        self.assertEqual(first, second)


class NoFleetSurfaceIsTouchedTest(unittest.TestCase):
    def test_importing_this_module_does_not_change_the_tool_surface(self):
        from fleet_mcp import tools
        from fleet_mcp.version import TOOL_SURFACE_REVISION
        # Core-lite adds no tool. V5 includes the separate fresh-project entry.
        self.assertEqual(TOOL_SURFACE_REVISION, "fleet-mcp-7")
        self.assertEqual(len(tools.TOOLS), 11)
        self.assertEqual(len(tools.NATIVE_MIRROR_TOOLS), 31)

    def test_the_module_exports_nothing_that_could_be_mistaken_for_a_tool(self):
        for name in rb.__all__:
            with self.subTest(name=name):
                self.assertNotIn(name, ("fleet_", "tool", "mcp"))



class TheRealNativeProducerShapeIsReadTest(unittest.TestCase):
    """A document with the exact shapes `native/node-core` PR #215 emits.

    Transcribed from the producer's own test expectations
    (`core-lite/tests/test_collector_readings.cpp`) rather than from its prose,
    because the prose and the code disagreed about `swap` and `psi.*` once
    already: this reader's first revision expected a flat `some_avg10` and a
    storage `rows` list, and would have dropped every real reading as
    UNAVAILABLE while its own tests stayed green.

    If the producer changes shape again, THIS is the class that should fail.
    """

    #: The Core-Lite value shapes, key for key, as the Linux adapter writes them.
    OBSERVATIONS = [
        {"name": "cpu", "state": "PRESENT", "completeness": "COMPLETE",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"used_percent": 6.5}},
        {"name": "memory", "state": "PRESENT", "completeness": "COMPLETE",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"total_bytes": 270449774592, "available_bytes": 178577321984,
                   "free_bytes": 71849877504, "used_percent": 29.9}},
        {"name": "storage.readonly", "state": "PRESENT",
         "completeness": "COMPLETE",
         "value": {"filesystems": {"roles": [
                      {"role": "root", "total_bytes": 500000000000,
                       "available_bytes": 100000000000, "used_bytes": 400000000000},
                      {"role": "data", "total_bytes": 4000000000000,
                       "available_bytes": 3000000000000, "used_bytes": 1000000000000}]},
                   "disk_io": {"roles": [
                      {"role": "root", "read_bytes": 111, "write_bytes": 222}]}}},
        {"name": "swap", "state": "PRESENT", "completeness": "COMPLETE",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"total_bytes": 34359734272, "free_bytes": 5210218496,
                   "used_bytes": 29149515776,
                   "pages_in_per_second": 17.5, "pages_out_per_second": 22.5}},
        {"name": "psi.cpu", "state": "PRESENT", "completeness": "COMPLETE",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"some": {"avg10": 0.93, "avg60": 4.86, "avg300": 5.69},
                   "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0}}},
        {"name": "psi.memory", "state": "PRESENT", "completeness": "COMPLETE",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"some": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0},
                   "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0}}},
        # The one PARTIAL reading: `some` measured, `full` an explicit marker.
        {"name": "psi.io", "state": "PRESENT", "completeness": "PARTIAL",
         "source": "nodecore/NodeResourceSnapshot v1",
         "value": {"some": {"avg10": 0.61, "avg60": 0.22, "avg300": 0.67},
                   "full": {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}}},
        {"name": "services", "state": "UNAVAILABLE", "reason": "NOT_IMPLEMENTED"},
        {"name": "connector", "state": "UNAVAILABLE", "reason": "NOT_IMPLEMENTED"},
        {"name": "gpu.readonly", "state": "UNAVAILABLE", "reason": "NOT_IMPLEMENTED"},
    ]

    def document(self, **over) -> dict:
        base = doc()
        base["device_identity"] = {
            "state": "UNKNOWN", "basis": "NONE", "claimed": DEVICE,
            "reason": "the managed Node identity was not established"}
        # The producer is honest here: it cannot attest a device, so it says so.
        base["live"] = False
        # DEEP, not shallow: a `dict(row)` copy still shares each row's
        # `value`, so one test's hostile injection would rewrite the fixture
        # for every test that ran after it.
        base["observations"] = copy.deepcopy(self.OBSERVATIONS)
        base.update(over)
        return base

    def test_the_real_document_is_read_and_not_dropped(self):
        summary = read(self.document())
        self.assertEqual(summary["connected"], "CONNECTED")
        self.assertEqual(summary["freshness"]["state"], "FRESH")
        # The producer declined to claim `live`; the reader does not overrule it.
        self.assertFalse(summary["live"]["claimed"])
        self.assertFalse(summary["live"]["accepted"])
        for name in ("cpu", "memory", "storage.readonly", "swap", "psi.cpu",
                     "psi.memory", "psi.io"):
            with self.subTest(observation=name):
                self.assertEqual(summary["observations"][name]["state"], "PRESENT")
        for name in ("services", "connector", "gpu.readonly"):
            with self.subTest(observation=name):
                row = summary["observations"][name]
                self.assertEqual(row["state"], "UNAVAILABLE")
                self.assertEqual(row["reason"], "NOT_IMPLEMENTED")

    def test_the_swap_rates_survive_and_are_not_percentages(self):
        row = read(self.document())["observations"]["swap"]
        self.assertEqual(row["completeness"], "COMPLETE")
        self.assertEqual(row["value"]["pages_out_per_second"], 22.5)
        self.assertEqual(row["value"]["total_bytes"], 34359734272)
        # `used_percent` is deliberately absent from the producer's projection.
        self.assertNotIn("used_percent", row["value"])

    def test_a_measured_zero_rate_is_not_an_absence(self):
        """`0.0` pages out and "the interval was not measurable" differ."""
        document = self.document()
        for row in document["observations"]:
            if row["name"] == "swap":
                row["value"]["pages_out_per_second"] = 0.0
        summary = read(document)
        row = summary["observations"]["swap"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["value"]["pages_out_per_second"], 0.0)
        self.assertEqual(row["completeness"], "COMPLETE")

    def test_the_partial_psi_reading_keeps_its_measured_half(self):
        row = read(self.document())["observations"]["psi.io"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertEqual(row["value"]["some"]["avg10"], 0.61)
        # The absent line says so where it sits, and is not rendered as zero.
        self.assertEqual(row["value"]["full"],
                         {"state": "UNAVAILABLE", "reason": "NO_SOURCE"})

    def test_a_claimed_complete_over_a_partial_value_is_not_believed(self):
        """The word is checked against the value rather than carried."""
        document = self.document()
        for row in document["observations"]:
            if row["name"] == "psi.io":
                row["completeness"] = "COMPLETE"
        summary = read(document)
        row = summary["observations"]["psi.io"]
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertIn("COMPLETENESS_MISMATCH", refusals(summary))

    def test_a_claimed_partial_over_a_complete_value_is_not_believed(self):
        document = self.document()
        for row in document["observations"]:
            if row["name"] == "cpu":
                row["completeness"] = "PARTIAL"
        summary = read(document)
        self.assertEqual(summary["observations"]["cpu"]["completeness"], "COMPLETE")
        self.assertIn("COMPLETENESS_MISMATCH", refusals(summary))

    def test_storage_is_two_role_blocks_and_no_paths(self):
        row = read(self.document())["observations"]["storage.readonly"]
        self.assertEqual(sorted(row["value"]), ["disk_io", "filesystems"])
        self.assertEqual(row["value"]["filesystems"]["roles"][0]["role"], "root")
        rendered = repr(row)
        for leaked in ("/dev/", "/data", "mount_point", "fstype", "inodes"):
            with self.subTest(leaked=leaked):
                self.assertNotIn(leaked, rendered)

    def test_storage_with_only_one_block_is_still_a_reading(self):
        """Either block may be absent; the producer says so itself."""
        document = self.document()
        for row in document["observations"]:
            if row["name"] == "storage.readonly":
                del row["value"]["disk_io"]
        summary = read(document)
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(sorted(row["value"]), ["filesystems"])


class HostileValuesInsideTheRealShapeTest(unittest.TestCase):
    """Every nesting level the real shape introduced is a place to hide a string."""

    CANARY = "canary-NESTED-12345678"

    def _document_with(self, mutate) -> dict:
        document = TheRealNativeProducerShapeIsReadTest().document()
        mutate(document)
        return document

    def _assert_no_echo(self, summary):
        self.assertNotIn(self.CANARY, repr(summary))
        for item in summary["refusals"]:
            self.assertNotIn(self.CANARY, item["note"])
            self.assertNotIn(self.CANARY, item["where"])

    def test_a_canary_in_a_psi_window_is_refused(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "psi.cpu":
                    row["value"]["some"]["avg10"] = self.CANARY
        summary = read(self._document_with(mutate))
        self.assertEqual(summary["observations"]["psi.cpu"]["state"], "UNAVAILABLE")
        self._assert_no_echo(summary)

    def test_a_canary_as_a_whole_psi_line_is_refused(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "psi.cpu":
                    row["value"]["some"] = self.CANARY
        summary = read(self._document_with(mutate))
        self.assertEqual(summary["observations"]["psi.cpu"]["state"], "UNAVAILABLE")
        self._assert_no_echo(summary)

    def test_a_canary_in_a_psi_reason_becomes_no_source(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "psi.io":
                    row["value"]["full"]["reason"] = self.CANARY
        summary = read(self._document_with(mutate))
        row = summary["observations"]["psi.io"]
        self.assertEqual(row["value"]["full"]["reason"], "NO_SOURCE")
        self._assert_no_echo(summary)

    def test_a_swap_field_the_producer_could_not_measure_is_not_a_zero(self):
        """The stock and the flow each carry their own absence in place."""
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "swap":
                    row["value"]["used_bytes"] = {"state": "UNAVAILABLE",
                                                  "reason": "NO_SOURCE"}
                    row["completeness"] = "PARTIAL"
        summary = read(self._document_with(mutate))
        row = summary["observations"]["swap"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["completeness"], "PARTIAL")
        self.assertEqual(row["value"]["used_bytes"],
                         {"state": "UNAVAILABLE", "reason": "NO_SOURCE"})
        self.assertNotEqual(row["value"]["used_bytes"], 0)
        # The measured half is still there.
        self.assertEqual(row["value"]["pages_out_per_second"], 22.5)

    def test_a_canary_in_a_per_window_reason_becomes_no_source(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "psi.cpu":
                    row["value"]["some"]["avg300"] = {"state": "UNAVAILABLE",
                                                      "reason": self.CANARY}
        summary = read(self._document_with(mutate))
        row = summary["observations"]["psi.cpu"]
        self.assertEqual(row["value"]["some"]["avg300"]["reason"], "NO_SOURCE")
        self.assertEqual(row["completeness"], "PARTIAL")
        self._assert_no_echo(summary)

    def test_a_canary_in_a_swap_marker_reason_becomes_no_source(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "swap":
                    row["value"]["used_bytes"] = {"state": "UNAVAILABLE",
                                                  "reason": self.CANARY}
        summary = read(self._document_with(mutate))
        row = summary["observations"]["swap"]
        self.assertEqual(row["value"]["used_bytes"]["reason"], "NO_SOURCE")
        self._assert_no_echo(summary)

    def test_a_canary_in_a_swap_rate_is_refused(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "swap":
                    row["value"]["pages_out_per_second"] = self.CANARY
        summary = read(self._document_with(mutate))
        self.assertEqual(summary["observations"]["swap"]["state"], "UNAVAILABLE")
        self._assert_no_echo(summary)

    def test_a_path_shaped_storage_role_is_refused(self):
        """A role is the contract's one free-text field, and a path is not a role."""
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "storage.readonly":
                    row["value"]["filesystems"]["roles"][0]["role"] = "/dev/vda2"
        summary = read(self._document_with(mutate))
        self.assertEqual(
            summary["observations"]["storage.readonly"]["state"], "UNAVAILABLE")
        self.assertNotIn("/dev/vda2", repr(summary))

    def test_a_role_shaped_string_is_the_contracts_own_bounded_echo(self):
        """The ONE echo this reader has, recorded rather than left implicit.

        `role` is the source contract's single free-text field, and it is
        admitted when it matches `[A-Za-z0-9_-]{1,32}`. That bound excludes
        separators, so it cannot carry a path or an argv fragment — but it does
        admit a token-shaped string, and this reader does not invent a role
        vocabulary the contract never defined to close the last 32 characters.

        Asserted as ECHOED, so that tightening it later is a change this test
        reports rather than a silent narrowing someone discovers in production.
        """
        role_shaped = "Abc012_-" * 4          # 32 chars, matches the rule exactly
        self.assertEqual(len(role_shaped), 32)

        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "storage.readonly":
                    row["value"]["filesystems"]["roles"][0]["role"] = role_shaped
        summary = read(self._document_with(mutate))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["value"]["filesystems"]["roles"][0]["role"], role_shaped)
        # And a 33rd character is over the bound, so the row is refused.
        def mutate_long(document):
            for row in document["observations"]:
                if row["name"] == "storage.readonly":
                    row["value"]["filesystems"]["roles"][0]["role"] = role_shaped + "X"
        over = read(self._document_with(mutate_long))
        self.assertEqual(over["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")

    def test_a_canary_as_a_key_inside_a_psi_line_is_not_echoed(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "psi.cpu":
                    row["value"]["some"][self.CANARY] = 1
        summary = read(self._document_with(mutate))
        self.assertEqual(summary["observations"]["psi.cpu"]["state"], "PRESENT")
        self._assert_no_echo(summary)

    def test_a_canary_as_a_key_inside_a_storage_row_is_not_echoed(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "storage.readonly":
                    row["value"]["filesystems"]["roles"][0][self.CANARY] = 1
        summary = read(self._document_with(mutate))
        self.assertEqual(
            summary["observations"]["storage.readonly"]["state"], "PRESENT")
        self._assert_no_echo(summary)

    def test_a_canary_as_a_storage_block_name_is_not_echoed(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "storage.readonly":
                    row["value"][self.CANARY] = {"roles": []}
        summary = read(self._document_with(mutate))
        self._assert_no_echo(summary)

    def test_a_canary_as_a_swap_key_is_not_echoed(self):
        def mutate(document):
            for row in document["observations"]:
                if row["name"] == "swap":
                    row["value"][self.CANARY] = 1
        summary = read(self._document_with(mutate))
        self.assertEqual(summary["observations"]["swap"]["state"], "PRESENT")
        self._assert_no_echo(summary)

    def test_the_whole_real_document_with_canaries_everywhere_leaks_nothing(self):
        def mutate(document):
            for row in document["observations"]:
                row[self.CANARY] = 1
                if isinstance(row.get("value"), dict):
                    row["value"][self.CANARY] = 1
        summary = read(self._document_with(mutate))
        self._assert_no_echo(summary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
