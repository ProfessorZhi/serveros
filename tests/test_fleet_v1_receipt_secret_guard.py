"""The receipt projection cannot carry remote text — by shape, not by care.

`test_fleet_secret_hygiene.py::test_no_remote_body_is_ever_sliced` was the
hosted-CI signal, and it is a source scanner: `raw[` appeared in `mcp_http.py`
when the projection read `details["reason"]`. Renaming the variable would have
satisfied the scanner and changed nothing, so the fix is structural:

*   every read of the remote envelope goes through `_scalar` / `_scalar_list`,
    whose RETURN TYPES cannot carry a container — a caller cannot slice or
    forward a value it never receives;
*   the projection is an immutable `RefusalProjection` of validated scalars, and
    the envelope is dropped once it is built;
*   only five names can appear in the refusal's `extra`, and every one is
    shape-checked.

These tests hold that structure in place, and they use freshly generated
secret-shaped values so a failure prints a value that never mattered.
"""
from __future__ import annotations

import ast
import json
import secrets
import traceback
import unittest
from pathlib import Path

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.mcp_http import (
    RefusalProjection,
    _scalar,
    _scalar_list,
    refusal_projection,
    structured,
)

REPO = Path(__file__).resolve().parent.parent
SOURCE_PATH = REPO / "fleet_mcp" / "mcp_http.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

SYNTHETIC = secrets.token_hex(24)


def _error(**envelope) -> dict:
    return {
        "isError": True,
        "content": [{"type": "text", "text": f"remote prose {SYNTHETIC}"}],
        "structuredContent": envelope,
    }


def _rendered(exc: FleetRefusal) -> str:
    return "\n".join((str(exc), exc.detail,
                       json.dumps(exc.extra, default=str, sort_keys=True),
                       "".join(traceback.format_exception(exc))))


class RemoteTextCannotCrossTest(unittest.TestCase):
    def test_message_and_secret_shaped_details_never_cross(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(
                code="RUN_NOT_FOUND",
                retryable=False,
                message=f"use {SYNTHETIC}",
                details={"reason": "never_issued",
                         "token": SYNTHETIC,
                         "stack": f"at remote({SYNTHETIC})",
                         "path": f"/home/remote/{SYNTHETIC}"}))
        rendered = _rendered(caught.exception)
        self.assertNotIn(SYNTHETIC, rendered)

    def test_a_malformed_reason_is_withheld_and_marked_unknown(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(
                code="RUN_NOT_FOUND",
                details={"reason": SYNTHETIC}))  # not never_issued / expired
        details = caught.exception.extra.get("details") or {}
        self.assertIs(details.get("reason_known"), False)
        self.assertNotIn("reason", details)
        self.assertNotIn(SYNTHETIC, _rendered(caught.exception))

    def test_a_non_mapping_details_is_not_projected(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(code="RUN_NOT_FOUND", details=SYNTHETIC))
        self.assertNotIn("details", caught.exception.extra)
        self.assertNotIn(SYNTHETIC, _rendered(caught.exception))

    def test_a_secret_shaped_supported_entry_withholds_the_whole_set(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(
                code="UNSUPPORTED_PERMISSION_MODE",
                details={"supported": ["default", SYNTHETIC]}))
        self.assertNotIn("supported", (caught.exception.extra.get("details") or {}))
        self.assertNotIn(SYNTHETIC, _rendered(caught.exception))

    def test_a_secret_shaped_boot_id_is_withheld(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(
                code="RUN_NOT_FOUND",
                details={"reason": "expired", "bootId": f"{SYNTHETIC}\n"}))
        self.assertNotIn("bootId", (caught.exception.extra.get("details") or {}))
        self.assertNotIn(SYNTHETIC, _rendered(caught.exception))

    def test_a_non_boolean_retryable_is_withheld(self):
        with self.assertRaises(FleetRefusal) as caught:
            structured(_error(code="RUN_NOT_FOUND", retryable=SYNTHETIC))
        self.assertNotIn("retryable", caught.exception.extra)


class TheWhitelistIsClosedTest(unittest.TestCase):
    def test_only_the_named_fields_can_appear(self):
        projection = refusal_projection({
            "code": "RUN_NOT_FOUND",
            "retryable": False,
            "details": {"reason": "never_issued", "bootId": "boot-1",
                        "supported": ["default"]},
        })
        self.assertEqual(set(projection.as_extra()),
                         {"device_code", "retryable", "details"})
        self.assertEqual(set(projection.as_extra()["details"]),
                         {"reason", "bootId", "supported"})

    def test_the_projection_is_an_immutable_scalar_record(self):
        self.assertTrue(RefusalProjection.__dataclass_params__.frozen)
        projection = refusal_projection({"code": "RUN_NOT_FOUND"})
        with self.assertRaises(Exception):
            projection.code = "OTHER"  # type: ignore[misc]

    def test_supported_longer_than_the_cap_is_refused_whole(self):
        projection = refusal_projection(
            {"details": {"supported": [f"m{i}" for i in range(65)]}})
        self.assertEqual(projection.supported, ())


class TheSourceReadsRemoteDataOnlyThroughScalarHelpersTest(unittest.TestCase):
    """The structural rule the old scanner was reaching for.

    `raw[:200]` looked like a bound. This asserts the stronger fact: inside
    `refusal_projection`, no subscript is taken on the envelope or on the
    details mapping at all — they are read only through helpers that return
    scalars (or a rebuilt tuple), so there is no expression that could carry a
    remote body.
    """

    def test_the_projection_function_takes_no_subscript_of_remote_values(self):
        tree = ast.parse(SOURCE)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "refusal_projection")
        offenders = [
            ast.unparse(node.value)
            for node in ast.walk(fn)
            if isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"envelope", "details"}
        ]
        self.assertEqual(offenders, [])

    def test_the_remote_helpers_exist_and_cannot_return_containers(self):
        self.assertTrue(callable(_scalar))
        self.assertEqual(_scalar({"x": {"nested": "no"}}, "x"), None)
        self.assertEqual(_scalar({"x": ["no"]}, "x"), None)
        self.assertEqual(_scalar_list({"x": ["ok"]}, "x", 4), ("ok",))
        self.assertEqual(_scalar_list({"x": [["no"]]}, "x", 4), ())

    def test_the_old_shapes_stay_absent(self):
        for shape in ("raw[", "body[", "error_message[", "[:200]", "[:100]"):
            self.assertNotIn(shape, SOURCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
