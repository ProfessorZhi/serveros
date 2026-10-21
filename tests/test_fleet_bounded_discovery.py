"""Slow inventory reads cannot occupy a commander or an unbounded worker queue."""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from fleet_mcp.config import PolicyBinding, SCHEMA, load_config
from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import FleetService
from test_fleet_mcp_surface import Harness, TOKYO, WINDOWS, _summary


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.h = Harness(self.root)

    def configure(self, *, workers=2):
        self.h.config = replace(self.h.config, policy=replace(
            self.h.config.policy, discovery_timeout_seconds=0.1, discovery_workers=workers))
        self.h.service = FleetService(self.h.config, providers=self.h.service.providers)

    def test_fast_device_returns_while_other_provider_is_still_blocked(self):
        self.configure()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def slow(**kwargs):
            entered.set()
            release.wait(3)
            finished.set()
            return []

        self.h.windows.list_sessions = slow
        self.h.tokyo._sessions = [_summary(TOKYO, "available")]
        result = self.h.service.list_sessions(self.h.principal("fleet:read"))
        self.assertTrue(entered.is_set())
        self.assertFalse(finished.is_set(), "caller waited for the blocked provider")
        self.assertEqual([s["native_session_id"] for s in result["sessions"]], ["available"])
        gap = next(g for g in result["devices_unreachable"] if g["device_ref"] == WINDOWS)
        self.assertEqual(gap["code"], "PROVIDER_TIMEOUT")
        release.set()
        self.assertTrue(finished.wait(2))

    def test_repeated_pending_query_is_shared_and_different_query_cannot_overflow(self):
        self.configure(workers=1)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        self.addCleanup(release.set)

        def slow(**kwargs):
            calls.append(kwargs)
            entered.set()
            release.wait(3)
            finished.set()
            return []

        self.h.windows.list_sessions = slow
        for limit in (3, 3, 4):
            result = self.h.service.list_sessions(
                self.h.principal("fleet:read"), device=WINDOWS, limit=limit)
            self.assertTrue(result["devices_unreachable"])
        self.assertTrue(entered.is_set())
        self.assertFalse(finished.is_set())
        self.assertEqual(len(calls), 1)
        release.set()
        self.assertTrue(finished.wait(2))

    def test_completed_queries_are_read_again_and_original_error_code_survives(self):
        self.configure()
        calls = []

        def counted(**kwargs):
            calls.append(True)
            return [_summary(TOKYO, str(len(calls)))]

        self.h.tokyo.list_sessions = counted
        for expected in ("1", "2"):
            result = self.h.service.list_sessions(
                self.h.principal("fleet:read"), device=TOKYO)
            self.assertEqual(result["sessions"][0]["native_session_id"], expected)

        def unauthorized(**kwargs):
            raise FleetRefusal("PROVIDER_UNAUTHORIZED", "fixture")

        self.h.tokyo.list_sessions = unauthorized
        result = self.h.service.list_sessions(self.h.principal("fleet:read"), device=TOKYO)
        self.assertEqual(result["devices_unreachable"][0]["code"], "PROVIDER_UNAUTHORIZED")

    def test_global_activity_sort_is_preserved_and_read_authority_precedes_queries(self):
        self.configure()
        first = replace(_summary(TOKYO, "older"), last_activity={"iso": "2026-10-20T01:00:00Z"})
        second = replace(_summary(WINDOWS, "newer"), last_activity={"iso": "2026-10-21T01:00:00Z"})
        self.h.tokyo._sessions = [first]
        self.h.windows._sessions = [second]
        result = self.h.service.list_sessions(self.h.principal("fleet:read"))
        self.assertEqual([s["native_session_id"] for s in result["sessions"]], ["newer", "older"])
        with self.assertRaises(FleetRefusal) as caught:
            self.h.service.list_sessions(self.h.principal("fleet:send"))
        self.assertEqual(caught.exception.code, "SCOPE_DENIED")


class DiscoveryPolicy(unittest.TestCase):
    def test_invalid_direct_policy_is_rejected(self):
        for timeout in (0, -1, float("nan"), float("inf"), True, "5", 61):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                PolicyBinding(discovery_timeout_seconds=timeout)
        for workers in (0, -1, True, 1.5, "2", 17):
            with self.subTest(workers=workers), self.assertRaises(ValueError):
                PolicyBinding(discovery_workers=workers)

    def test_yaml_uses_same_validation_and_reports_bad_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binding.yaml"
            for policy in ({}, {"discovery_timeout_seconds": 0},
                           {"discovery_workers": True}):
                path.write_text(json.dumps({"schema": SCHEMA, "device_ref": TOKYO,
                                            "policy": policy}))
                if not policy:
                    self.assertEqual(load_config(path).policy.discovery_workers, 4)
                else:
                    with self.assertRaises(FleetRefusal) as caught:
                        load_config(path)
                    self.assertEqual(caught.exception.code, "BAD_REQUEST")


if __name__ == "__main__":
    unittest.main()
