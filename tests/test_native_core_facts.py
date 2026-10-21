"""The facts writer: every fact it must refuse to invent.

The point of this file is not that a good document is produced. It is that each
unavailable source leaves the fact UNKNOWN rather than a comfortable default,
and that a writer without the lease publishes nothing at all. Those two are what
the C++ reader relies on; anything else here is decoration.

Runs under unittest: this host has no pytest.
    cd tests && PYTHONPATH=. python3 -m unittest test_native_core_facts -q
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import native_core_facts as facts


def spec(**overrides):
    base = dict(id="toy", unit="toy.service", owner=facts.OWNER_SERVEROSD)
    base.update(overrides)
    return facts.ServiceSpec(**base)


class OwnerMapTest(unittest.TestCase):
    def test_a_known_owner_is_accepted(self):
        owners = facts.build_owner_map([spec()])
        self.assertEqual(owners, {"toy.service": facts.OWNER_SERVEROSD})

    def test_an_unclaimed_owner_refuses(self):
        with self.assertRaises(facts.FactsRefused) as caught:
            facts.build_owner_map([spec(owner="whoever")])
        self.assertEqual(caught.exception.code, facts.OWNER_UNKNOWN)

    def test_one_unit_cannot_have_two_owners(self):
        with self.assertRaises(facts.FactsRefused) as caught:
            facts.build_owner_map(
                [spec(id="a", unit="u.service", owner=facts.OWNER_SERVEROSD),
                 spec(id="b", unit="u.service", owner=facts.OWNER_NATIVE_CORE)]
            )
        self.assertEqual(caught.exception.code, facts.OWNER_UNKNOWN)

    def test_a_service_without_a_unit_refuses(self):
        with self.assertRaises(facts.FactsRefused):
            facts.build_owner_map([spec(unit="")])


class RuntimeFactTest(unittest.TestCase):
    def test_no_runtime_source_is_unknown_not_serving(self):
        # The failure this exists for: "nobody configured a check" reading as
        # "the service is fine".
        self.assertEqual(facts.read_runtime(spec()), {"runtime": "unknown", "known": False})

    def test_absent_marker_is_serving(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "login.marker"
            self.assertEqual(facts.read_runtime(spec(runtime_marker=marker))["runtime"], "serving")

    def test_present_marker_is_needs_login(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "login.marker"
            marker.write_text("expired", encoding="utf-8")
            runtime = facts.read_runtime(spec(runtime_marker=marker))
            self.assertEqual(runtime["runtime"], "needs-login")
            self.assertTrue(runtime["known"])


class MaintenanceFactTest(unittest.TestCase):
    def test_no_marker_is_not_maintenance(self):
        self.assertEqual(facts.read_maintenance(spec()), (False, ""))

    def test_marker_carries_its_reason(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "maint"
            marker.write_text("deploy window", encoding="utf-8")
            self.assertEqual(facts.read_maintenance(spec(maintenance_marker=marker)),
                             (True, "deploy window"))


class LinkFactTest(unittest.TestCase):
    def test_no_link_source_is_unknown(self):
        link = facts.probe_link(None)
        self.assertFalse(link["known"])
        self.assertFalse(link["up"])

    def test_a_reachable_endpoint_is_up(self):
        link = facts.probe_link({"kind": "tcp", "host": "127.0.0.1", "port": 9},
                                connect=lambda addr, t: True)
        self.assertTrue(link["known"])
        self.assertTrue(link["up"])

    def test_an_unreachable_endpoint_is_a_known_down(self):
        def boom(addr, timeout):
            raise ConnectionRefusedError("refused")
        link = facts.probe_link({"kind": "tcp", "host": "127.0.0.1", "port": 9}, connect=boom)
        self.assertTrue(link["known"], "a failed attempt is knowledge, not ignorance")
        self.assertFalse(link["up"])

    def test_an_unsupported_source_is_unknown(self):
        self.assertFalse(facts.probe_link({"kind": "vibes"})["known"])


class AgentFactTest(unittest.TestCase):
    def test_no_source_is_unknown(self):
        agent = facts.read_agent(None)
        self.assertFalse(agent["known"])
        self.assertFalse(agent["busy"])

    def test_marker_means_busy_and_names_the_holder(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "busy"
            marker.write_text("cc-session-7", encoding="utf-8")
            agent = facts.read_agent({"kind": "marker", "path": str(marker)})
            self.assertTrue(agent["known"])
            self.assertTrue(agent["busy"])
            self.assertEqual(agent["holder"], "cc-session-7")

    def test_absent_marker_means_not_busy(self):
        with tempfile.TemporaryDirectory() as td:
            agent = facts.read_agent({"kind": "marker", "path": str(Path(td) / "nope")})
            self.assertTrue(agent["known"])
            self.assertFalse(agent["busy"])


class CollectTest(unittest.TestCase):
    def test_unknown_sources_stay_unknown_in_the_document(self):
        doc = facts.collect_facts(facts.FactsSources(services=[spec()]), now=lambda: 1800000000.0)
        self.assertEqual(doc["schema"], facts.FACTS_SCHEMA)
        self.assertEqual(doc["observed_epoch"], 1800000000)
        self.assertFalse(doc["link"]["known"])
        self.assertFalse(doc["agent"]["known"])
        self.assertEqual(doc["services"][0]["runtime"], "unknown")

    def test_a_unit_owned_by_the_other_program_is_omitted(self):
        sources = facts.FactsSources(services=[
            spec(id="mine", unit="mine.service", owner=facts.OWNER_SERVEROSD),
            spec(id="theirs", unit="theirs.service", owner=facts.OWNER_NATIVE_CORE),
        ])
        doc = facts.collect_facts(sources, owner=facts.OWNER_SERVEROSD, now=lambda: 1.0)
        self.assertEqual([s["id"] for s in doc["services"]], ["mine"])

    def test_maintenance_is_carried(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "maint"
            marker.write_text("window", encoding="utf-8")
            doc = facts.collect_facts(
                facts.FactsSources(services=[spec(maintenance_marker=marker)]),
                now=lambda: 1.0)
        self.assertTrue(doc["services"][0]["maintenance"])
        self.assertEqual(doc["services"][0]["maintenance_reason"], "window")


class DocumentTest(unittest.TestCase):
    def test_a_document_with_a_hole_is_refused(self):
        doc = facts.collect_facts(facts.FactsSources(services=[spec()]), now=lambda: 1.0)
        doc["services"][0]["runtime"] = "probably-fine"
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_duplicate_services_are_refused(self):
        doc = facts.collect_facts(facts.FactsSources(services=[spec()]), now=lambda: 1.0)
        doc["services"].append(dict(doc["services"][0]))
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_write_is_atomic_private_and_parsable(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "nested" / "facts.json"
            doc = facts.collect_facts(facts.FactsSources(services=[spec()]), now=lambda: 1.0)
            facts.write_document(target, doc)
            self.assertTrue(target.exists())
            mode = stat.S_IMODE(os.stat(target).st_mode)
            self.assertEqual(mode, 0o600, "facts are owner-only")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), doc)
            # nothing half-written is left behind
            self.assertEqual(list(target.parent.glob(".facts-*")), [])


class LeaseTest(unittest.TestCase):
    def test_the_second_writer_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lease"
            first = facts.SharedLease(path)
            first.acquire()
            try:
                second = facts.SharedLease(path)
                with self.assertRaises(facts.FactsRefused) as caught:
                    second.acquire()
                self.assertEqual(caught.exception.code, facts.LEASE_BUSY)
            finally:
                first.release()

    def test_a_released_lease_can_be_retaken(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lease"
            first = facts.SharedLease(path)
            first.acquire()
            first.release()
            second = facts.SharedLease(path)
            second.acquire()
            second.release()

    def test_publishing_facts_does_not_take_the_act_lease(self):
        # If observation took the act lease, the Core could never obtain the pen
        # it needs to act on the facts being published. Publication is a rename,
        # not an effect.
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "facts.json"
            core_pen = facts.SharedLease(Path(td) / "lease")
            core_pen.acquire()  # the Core is acting right now
            try:
                facts.cycle(facts_path=target, sources=facts.FactsSources(services=[spec()]))
                self.assertTrue(target.exists())
            finally:
                core_pen.release()


class ActorTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.lease_path = Path(self._td.name) / "lease"

    def actor(self, services):
        actor = facts.RecoveryActor(lease=facts.SharedLease(self.lease_path))
        actor.declare(services)
        return actor

    def test_an_owned_unit_may_be_recovered_and_releases_the_pen(self):
        actor = self.actor([spec()])
        calls = []
        actor.recover("toy.service", lambda: calls.append(1))
        self.assertEqual(calls, [1])
        self.assertFalse(actor.lease.held, "the pen is returned, not kept forever")
        # and it can be taken again
        other = facts.SharedLease(self.lease_path)
        other.acquire()
        other.release()

    def test_a_unit_owned_by_the_core_is_refused_by_name(self):
        actor = self.actor([spec(id="theirs", unit="theirs.service",
                                 owner=facts.OWNER_NATIVE_CORE)])
        with self.assertRaises(facts.FactsRefused) as caught:
            actor.recover("theirs.service", lambda: self.fail("must not act"))
        self.assertEqual(caught.exception.code, facts.OWNER_NOT_MINE)

    def test_an_unlisted_unit_is_refused_rather_than_defaulted(self):
        actor = self.actor([spec()])
        with self.assertRaises(facts.FactsRefused) as caught:
            actor.recover("mystery.service", lambda: self.fail("must not act"))
        self.assertEqual(caught.exception.code, facts.OWNER_UNKNOWN)

    def test_the_actor_refuses_while_the_core_holds_the_lease(self):
        # The whole point: two programs, one pen.
        core = facts.SharedLease(self.lease_path)
        core.acquire()
        try:
            actor = self.actor([spec()])
            with self.assertRaises(facts.FactsRefused) as caught:
                actor.recover("toy.service", lambda: self.fail("must not act"))
            self.assertEqual(caught.exception.code, facts.LEASE_BUSY)
        finally:
            core.release()


if __name__ == "__main__":
    unittest.main()
