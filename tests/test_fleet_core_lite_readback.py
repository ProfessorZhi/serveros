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
        """Three of four memory numbers must not be published for arithmetic."""
        whole = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300, "used_percent": 60}}]))
        self.assertEqual(whole["observations"]["memory"]["state"], "PRESENT")
        partial = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1000, "available_bytes": 400}}]))
        self.assertEqual(partial["observations"]["memory"]["state"], "PRESENT")
        broken = read(doc(observations=[
            {"name": "memory", "state": "PRESENT",
             "value": {"total_bytes": 1000, "available_bytes": 400,
                       "free_bytes": 300, "used_percent": 900}}]))
        self.assertEqual(broken["observations"]["memory"]["state"],
                         "UNAVAILABLE")

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
             "value": {"rows": [{"role": "root", "total_bytes": 10,
                                 "available_bytes": 4, "used_bytes": 6}]}}]))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertEqual(row["value"]["rows"][0]["role"], "root")

    def test_a_device_name_role_is_refused(self):
        """`vda2` is a raw hardware identifier the role exists to replace."""
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "value": {"rows": [{"role": "/dev/vda2", "total_bytes": 10}]}}]))
        self.assertEqual(summary["observations"]["storage.readonly"]["state"],
                         "UNAVAILABLE")

    def test_a_path_key_in_a_storage_row_is_stripped(self):
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "value": {"rows": [{"role": "root", "total_bytes": 10,
                                 "mount_point": "/", "path": "/dev/vda2"}]}}]))
        row = summary["observations"]["storage.readonly"]
        self.assertEqual(row["state"], "PRESENT")
        self.assertNotIn("mount_point", row["value"]["rows"][0])
        self.assertIn("UNKNOWN_FIELD", refusals(summary))

    def test_too_many_rows_makes_the_block_unavailable(self):
        rows = [{"role": f"r{i}", "total_bytes": 1}
                for i in range(rb.MAX_STORAGE_ROWS + 1)]
        summary = read(doc(observations=[
            {"name": "storage.readonly", "state": "PRESENT",
             "value": {"rows": rows}}]))
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
        self.assertEqual(TOOL_SURFACE_REVISION, "fleet-mcp-3")
        self.assertEqual(len(tools.TOOLS), 9)
        self.assertEqual(len(tools.NATIVE_MIRROR_TOOLS), 22)

    def test_the_module_exports_nothing_that_could_be_mistaken_for_a_tool(self):
        for name in rb.__all__:
            with self.subTest(name=name):
                self.assertNotIn(name, ("fleet_", "tool", "mcp"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
