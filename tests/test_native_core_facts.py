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

    def test_every_managed_service_is_described_regardless_of_who_acts(self):
        # The Core needs needs-login/maintenance for ITS units, and those are the
        # units Python may not act on. Filtering them out by action owner would
        # leave the Core with `unknown` runtime for exactly the services it owns.
        sources = facts.FactsSources(services=[
            spec(id="mine", unit="mine.service", owner=facts.OWNER_SERVEROSD),
            spec(id="theirs", unit="theirs.service", owner=facts.OWNER_NATIVE_CORE),
        ])
        doc = facts.collect_facts(sources, now=lambda: 1.0)
        self.assertEqual([s["id"] for s in doc["services"]], ["mine", "theirs"])

    def test_a_core_owned_service_still_reports_needs_login(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td) / "login"
            marker.write_text("expired", encoding="utf-8")
            doc = facts.collect_facts(
                facts.FactsSources(services=[spec(unit="core.service",
                                                  owner=facts.OWNER_NATIVE_CORE,
                                                  runtime_marker=marker)]),
                now=lambda: 1.0)
        self.assertEqual(doc["services"][0]["runtime"], "needs-login")

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


class LeaseSafetyTest(unittest.TestCase):
    def test_a_symlinked_lease_path_is_refused(self):
        # Two owners each "holding" a different inode is exactly how a split
        # lock happens; the symlink is refused rather than followed.
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real.lease"
            real.write_text("", encoding="utf-8")
            link = Path(td) / "lease"
            link.symlink_to(real)
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.SafeLease(link).acquire()
            self.assertIn(caught.exception.code, (facts.LEASE_UNSAFE, facts.PATH_UNSAFE))

    def test_a_multiply_linked_lease_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            real = Path(td) / "real.lease"
            real.write_text("", encoding="utf-8")
            os.link(real, Path(td) / "second.lease")
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.SafeLease(real).acquire()
            self.assertEqual(caught.exception.code, facts.LEASE_UNSAFE)

    def test_a_world_writable_lease_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lease"
            path.write_text("", encoding="utf-8")
            os.chmod(path, 0o666)
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.SafeLease(path).acquire()
            self.assertEqual(caught.exception.code, facts.LEASE_UNSAFE)

    def test_a_traversal_is_refused(self):
        with self.assertRaises(facts.FactsRefused) as caught:
            facts.secure_parent(Path("/tmp/../etc/facts.json"))
        self.assertEqual(caught.exception.code, facts.PATH_UNSAFE)

    def test_a_symlinked_parent_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            inside = Path(td) / "inside"
            inside.mkdir()
            link = Path(td) / "linked"
            link.symlink_to(inside)
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.publish_facts(link / "facts.json", _doc())
            self.assertIn(caught.exception.code, (facts.PATH_UNSAFE, facts.LEASE_UNSAFE))


def _doc():
    return facts.collect_facts(facts.FactsSources(services=[spec()]), now=lambda: 1.0)


class OwnerMapTest2(unittest.TestCase):
    def test_the_map_round_trips_and_is_read_by_owner(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "owner-map.json"
            facts.OwnerMap({"toy.service": facts.OWNER_SERVEROSD}).write(path)
            loaded = facts.OwnerMap.load(path)
            self.assertEqual(loaded.owner_of("toy.service"), facts.OWNER_SERVEROSD)
            self.assertIsNone(loaded.owner_of("other.service"))

    def test_an_unknown_owner_in_the_map_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "owner-map.json"
            path.write_text(json.dumps({"schema": facts.OWNER_MAP_SCHEMA,
                                        "units": {"a.service": "someone"}}), encoding="utf-8")
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.OwnerMap.load(path)
            self.assertEqual(caught.exception.code, facts.OWNER_UNKNOWN)


class DocumentStrictnessTest(unittest.TestCase):
    def test_an_unknown_field_is_refused(self):
        doc = _doc()
        doc["extra"] = 1
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_a_non_bool_flag_is_refused(self):
        doc = _doc()
        doc["link"]["known"] = "yes"
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_a_text_over_its_utf8_byte_bound_is_refused(self):
        # Bytes, not characters: the C++ reader measures bytes, so a multi-byte
        # string under 200 chars can still be over the bound.
        doc = _doc()
        doc["agent"]["holder"] = "ü" * 150  # 300 bytes
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_an_unknown_response_field_is_refused(self):
        doc = _doc()
        doc["services"][0]["why"] = "because"
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)


class AgentSourceTest(unittest.TestCase):
    def test_a_process_source_reports_busy_from_a_real_observation(self):
        agent = facts.read_agent({"kind": "process", "pattern": "x"},
                                 process_pids=lambda _p: [1234])
        self.assertTrue(agent["known"])
        self.assertTrue(agent["busy"])

    def test_a_process_source_that_cannot_observe_is_unknown(self):
        def boom(_p):
            raise OSError("pgrep unavailable")
        agent = facts.read_agent({"kind": "process", "pattern": "x"}, process_pids=boom)
        self.assertFalse(agent["known"], "an unmeasurable host is not an idle one")

    def test_a_marker_whose_producer_is_gone_is_unknown_not_idle(self):
        agent = facts.read_agent({"kind": "marker", "path": "/nonexistent-dir-xyz/busy"})
        self.assertFalse(agent["known"])
        self.assertFalse(agent["busy"])



class DeepPathEscapeTest(unittest.TestCase):
    def test_a_symlink_deep_in_the_chain_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "outside"
            outside.mkdir()
            linked = Path(td) / "linked"
            linked.symlink_to(outside)
            # linked/inner/facts.json -- the escape is two components up, which
            # the old `realpath(parent) == parent.resolve()` check could not see
            # because both sides resolved identically.
            target = linked / "inner" / "facts.json"
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.publish_facts(target, _doc())
            self.assertEqual(caught.exception.code, facts.PATH_UNSAFE)
            self.assertFalse((outside / "inner").exists(), "nothing may be created outside")

    def test_no_directory_is_created_before_the_chain_is_verified(self):
        with tempfile.TemporaryDirectory() as td:
            outside = Path(td) / "outside"
            outside.mkdir()
            linked = Path(td) / "linked"
            linked.symlink_to(outside)
            with self.assertRaises(facts.FactsRefused):
                facts.open_secure_dir(linked / "a" / "b", create=True)
            self.assertFalse((outside / "a").exists())

    def test_a_relative_path_is_refused(self):
        with self.assertRaises(facts.FactsRefused) as caught:
            facts.open_secure_dir(Path("relative/dir"), create=True)
        self.assertEqual(caught.exception.code, facts.PATH_UNSAFE)

    def test_the_real_temp_directory_is_still_usable(self):
        # /tmp is sticky and owned by root; the policy allows it, and a rule
        # that refused it would be enforcing hygiene by breaking the host.
        with tempfile.TemporaryDirectory() as td:
            fd = facts.open_secure_dir(Path(td), create=False)
            os.close(fd)


class LeaseReplacementTest(unittest.TestCase):
    def test_revalidate_notices_the_path_was_replaced(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "lease"
            lease = facts.SafeLease(path)
            lease.acquire()
            try:
                # Rename the held file away and put a new one at the path: the
                # old descriptor still holds its lock, on a file nobody looks at.
                os.rename(path, Path(td) / "moved")
                path.write_text("", encoding="utf-8")
                os.chmod(path, 0o600)
                with self.assertRaises(facts.FactsRefused) as caught:
                    lease.revalidate()
                self.assertEqual(caught.exception.code, facts.LEASE_UNSAFE)
                # And the actor refuses the effect rather than running it.
                actor = facts.RecoveryActor(lease=lease)
                actor.declare([spec()])
                with self.assertRaises(facts.FactsRefused):
                    actor.recover("toy.service", lambda: self.fail("must not act"))
            finally:
                lease.release()


class RealReaderTest(unittest.TestCase):
    def test_the_default_process_reader_resolves_the_real_host_class(self):
        # `from probes import Host` used to raise ImportError, so a real busy
        # agent read as UNKNOWN. The canonical loader finds `probes.LiveHost`.
        pids = facts._default_process_pids("srv0011-definitely-not-running")
        self.assertIsInstance(pids, list)
        # Not asserted empty: `pgrep -f` matches this very test's argv, which
        # contains the pattern. The point here is that the reader RESOLVES and
        # measures — the old `from probes import Host` raised before it could.


class NullFieldTest(unittest.TestCase):
    def test_a_null_agent_holder_is_refused(self):
        doc = _doc()
        doc["agent"]["holder"] = None
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_a_null_maintenance_reason_is_refused(self):
        doc = _doc()
        doc["services"][0]["maintenance_reason"] = None
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)

    def test_a_unit_field_is_refused_because_cpp_has_no_such_key(self):
        doc = _doc()
        doc["services"][0]["unit"] = "toy.service"
        with self.assertRaises(facts.FactsRefused):
            facts.validate_document(doc)



class PrivateDirectoryBindingTest(unittest.TestCase):
    def test_shared_immediate_parent_remains_readable_but_cannot_publish_or_lock(self):
        with tempfile.TemporaryDirectory() as td:
            shared = Path(td) / "shared"
            shared.mkdir(mode=0o775)
            shared.chmod(0o775)
            fd = facts.open_secure_dir(shared)
            os.close(fd)
            with self.assertRaises(facts.FactsRefused):
                facts.publish_facts(shared / "facts.json", _doc())
            with self.assertRaises(facts.FactsRefused):
                facts.SafeLease(shared / "lease").acquire()
            self.assertEqual(list(shared.iterdir()), [])

    def test_private_ancestor_protects_shared_state_and_retains_lease_identity(self):
        with tempfile.TemporaryDirectory() as td:
            private = Path(td) / "private"
            private.mkdir(mode=0o700)
            shared = private / "shared"
            shared.mkdir(mode=0o775)
            shared.chmod(0o775)
            destination = shared / "current"
            destination.mkdir(mode=0o700)
            facts.publish_facts(destination / "facts.json", _doc())
            owners = facts.OwnerMap.from_services([spec()])
            owners.write(destination / "owners.json")
            self.assertEqual(facts.OwnerMap.load(destination / "owners.json").owner_of("toy.service"), facts.OWNER_SERVEROSD)
            lease = facts.SafeLease(destination / "lease")
            lease.acquire()
            try:
                lease.revalidate()
                private.chmod(0o755)
                with self.assertRaises(facts.FactsRefused):
                    lease.revalidate()
            finally:
                private.chmod(0o700)
                lease.release()

    def test_held_lease_refuses_ancestor_alias_to_same_inode(self):
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td) / "parent"
            parent.mkdir(mode=0o700)
            lease = facts.SafeLease(parent / "lease")
            lease.acquire()
            moved = Path(td) / "moved"
            try:
                parent.rename(moved)
                parent.symlink_to(moved, target_is_directory=True)
                with self.assertRaises(facts.FactsRefused):
                    lease.revalidate()
            finally:
                lease.release()

    def test_owner_map_read_refuses_alias_and_invalid_json_with_typed_refusal(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "owners.json"
            facts.OwnerMap.from_services([spec()]).write(path)
            alias = Path(td) / "alias.json"
            alias.symlink_to(path)
            with self.assertRaises(facts.FactsRefused):
                facts.OwnerMap.load(alias)
            path.write_text("{bad", encoding="utf-8")
            with self.assertRaises(facts.FactsRefused) as caught:
                facts.OwnerMap.load(path)
            self.assertEqual(caught.exception.code, facts.OWNER_UNKNOWN)

if __name__ == "__main__":
    unittest.main()
