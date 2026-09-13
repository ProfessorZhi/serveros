#!/usr/bin/env python3
"""Registration-time admission: ownership and floating dependencies.

Two of the three live cases this round exists for are settled here, at the one
moment the question can be asked. Both are cases where the resource is FINE:

  Case A  a tunnel a task depended on carried `session:*` as its owner and
          `persistent` as its lifecycle. It was retired on time when an
          unrelated Agent session ended, and every observation between creation
          and retirement was correct. Nothing downstream could ever have caught
          it, because nothing was wrong until the exact instant it was wrong.

  Case C  one node reached Playwright through `npx @playwright/mcp@latest` and
          another through an absolute path to a versioned artifact. Both
          worked, the two ran "the same" toolchain and did not, and nothing in
          the catalogue could have said so.

Each test below has a counterpart that must NOT fail. The gate that refuses
every registration is as broken as the gate that refuses none, and only the
second one is obvious.

Two of these tests were written after the gate they cover was already wrong.
Both are marked where they sit, because the interesting part is not that the
code was once incorrect — it is that neither defect was reachable by reading
the gate, and both were reachable by trying to register the exact resource
Case C is about.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
RegistryInvalid = registry.RegistryInvalid


def a_resource(resource_id="thing", **overrides):
    """A minimal well-formed `tunnel` — Case A's resource, before the defect.

    `criticality` is a plain string here and `protection` is a `{"level": ...}`
    block, which is the schema's own inconsistency and is load-bearing: the
    gate that reads one as the other crashes only on the declared form.
    """
    record = {
        "id": resource_id,
        "kind": "tunnel",
        "owner": {"type": "project", "ref": "project:litellm"},
        "scope": "user",
        "lifecycle": {"class": "persistent"},
        "desired": {"state": "running"},
        "protection": {"level": "normal"},
        "transport": {"local": "127.0.0.1:17890", "remote": "litellm.internal:443"},
        "probes": [{"id": "p1", "kind": "socket-connect", "axis": "transport",
                    "path": "/tmp/x.sock"}],
        "not_applicable": {"functional": {"because":
            "the tunnel forwards and has no callable surface of its own; "
            "liveness is exactly what the transport probe measures"}},
    }
    record.update(overrides)
    return record


class OwnershipTests(unittest.TestCase):
    """Invariant 6. The refusal is narrow on purpose: it refuses the LIE, not
    the resource. A caller that genuinely wants a session-lifetime resource
    registers `disposable` and is admitted."""

    def test_a_session_may_not_own_a_persistent_resource(self):
        with self.assertRaises(RegistryInvalid) as caught:
            registry.validate(a_resource(
                owner={"type": "serveros", "ref": "session:abc123"}))
        message = str(caught.exception)
        self.assertIn("may not", message)
        self.assertIn("session:abc123", message)
        # The refusal has to name the repair, or the next caller guesses.
        self.assertIn("disposable", message)

    def test_a_session_may_not_own_a_task_scoped_resource(self):
        """A task-scoped resource is owned by the work item, never by the
        session working on it. Two sessions on one Item is a normal case, and
        the second one ending must not reclaim the first one's resources."""
        with self.assertRaises(RegistryInvalid):
            registry.validate(a_resource(
                owner={"type": "serveros", "ref": "session:abc123"},
                lifecycle={"class": "task-scoped"}))

    def test_an_agent_runtime_may_not_own_a_durable_resource(self):
        """The runtime is the natural owner and it is the wrong one. It is
        present, it is running, and it already supervises processes — which is
        exactly why Case A happened."""
        with self.assertRaises(RegistryInvalid):
            registry.validate(a_resource(
                owner={"type": "ability", "ref": "agent-runtime:codex-7"}))

    def test_a_session_MAY_own_a_disposable_resource(self):
        """The counterpart. Without this the gate would pass by refusing
        everything, and a gate that refuses everything gets switched off."""
        resource = registry.validate(a_resource(
            owner={"type": "serveros", "ref": "session:abc123"},
            lifecycle={"class": "disposable"}))
        self.assertEqual(resource.lifecycle, "disposable")

    def test_a_durable_owner_may_own_a_persistent_resource(self):
        for ref in ("project:litellm", "work-item:SRV-0007",
                    "serveros:node", "ability:runtime-management"):
            resource = registry.validate(a_resource(owner={"type": "project", "ref": ref}))
            self.assertEqual(resource.lifecycle, "persistent")

    def test_the_prefix_list_is_narrow(self):
        """A check that matches too much refuses legitimate registrations, and
        that is how it gets switched off. `project:session-manager` starts with
        the substring `session` and is not a session."""
        resource = registry.validate(a_resource(
            owner={"type": "project", "ref": "project:session-manager"}))
        self.assertEqual(resource.owner_ref, "project:session-manager")


class FloatingDependencyTests(unittest.TestCase):
    """Invariant 8. The asymmetry is the design: a floating dependency on a
    scratch helper is a finding, and on a critical or protected resource it
    blocks — because "it worked yesterday" and "it works today" are then
    statements about two different programs."""

    def test_a_critical_resource_may_not_float(self):
        """This is Case C, registered.

        It failed the first time it ran, with an `AttributeError` rather than a
        refusal: `criticality` is a plain string in this schema, and the gate
        read it as a `{"level": ...}` block. The crash was on the DECLARED
        form, so the only resources it ever crashed on were the ones that had
        explicitly been ranked — and an `AttributeError` is not a
        `RegistryInvalid`, so a caller catching the refusal type would have
        seen an unhandled exception instead of a decision.
        """
        with self.assertRaises(RegistryInvalid) as caught:
            registry.validate(a_resource(
                criticality="critical",
                note="started via npx @playwright/mcp@latest"))
        self.assertIn("PERSISTENT_RUNTIME_FLOATING_DEPENDENCY", str(caught.exception))

    def test_a_versionless_scoped_package_may_not_float_either(self):
        """The hole the first version of the escape hatch left open.

        `npx @playwright/mcp` has no version specifier, which the contract
        names explicitly, and it was ADMITTED on a critical resource — because
        the gate tested `"/" in text` to decide whether a path had been pinned,
        and the slash in a scoped package name is not a path. A scoped package
        with no version is the form most likely to drift, and it was the one
        form waved through.
        """
        with self.assertRaises(RegistryInvalid):
            registry.validate(a_resource(criticality="critical",
                                         note="npx @playwright/mcp"))

    def test_a_protected_resource_may_not_float(self):
        with self.assertRaises(RegistryInvalid):
            registry.validate(a_resource(
                protection={"level": "protected"},
                note="npx @playwright/mcp@latest"))

    def test_an_ordinary_resource_is_admitted_with_the_finding(self):
        """WARN, not blocking. A scratch helper that floats is worth knowing
        about and is not worth refusing a registration over."""
        self.assertEqual(registry.validate(a_resource(note="npx playwright@latest")).id,
                         "thing")

    def test_a_pinned_absolute_path_is_not_a_finding(self):
        """The repair, and the one the other node had already applied. A path
        that names a location is exactly what this check is asking for — note
        that this note also contains a scoped package name, and passes on the
        strength of the leading path, not of the slash in `@playwright`."""
        resource = registry.validate(a_resource(
            criticality="critical",
            note="/opt/applications/data/home-hwz/toolchains/npm-global/"
                 "lib/node_modules/@playwright/mcp/cli.js"))
        self.assertEqual(resource.id, "thing")

    def test_a_note_that_merely_mentions_the_floating_form_does_not_unpin_it(self):
        """Negative control for the escape hatch. The pinned-path test is the
        only reason a floating resource can be admitted, so it must not be
        satisfiable by a note that says 'we used to use @latest'."""
        with self.assertRaises(RegistryInvalid):
            registry.validate(a_resource(
                criticality="critical",
                note="/opt/toolchains/mcp/cli.js — migrated off npx @playwright/mcp@latest"))

    def test_the_severity_comes_from_the_resource_not_the_caller(self):
        """A caller that could choose its own severity would choose the one
        that lets it proceed. There is no parameter for it."""
        params = inspect.signature(registry._check_floating_dependency).parameters
        self.assertEqual(set(params), {"record", "where"})

    def test_an_undeclared_criticality_is_ranked_by_the_contract_not_by_the_gate(self):
        """The second defect, and the quieter one.

        Where the gate did not crash it silently re-derived the default as
        `or "normal"`, while the contract ranks an undeclared `persistent`
        resource `important`. The gate and `Resource.criticality` were two
        copies of one rule, so this asserts they are one lookup — checked
        through the public surface, since that is what a report reads.
        """
        undeclared = a_resource()  # persistent, no criticality
        resource = registry.validate(undeclared)
        self.assertEqual(resource.criticality, "important")
        self.assertFalse(resource.criticality_is_declared)
        self.assertEqual(registry._effective_criticality(undeclared),
                         registry._criticality_defaults()["persistent"])


class ContractAgreementTests(unittest.TestCase):
    """The two constant tables are one rule asserted at two moments —
    registration and observation — so a second copy is how they come to
    disagree without either changing visibly."""

    def test_registration_and_observation_share_one_definition(self):
        self.assertIs(registry.SESSION_OWNER_PREFIXES, anomaly.SESSION_OWNER_PREFIXES)
        self.assertIs(registry.FLOATING_DEPENDENCY_PATTERNS,
                      anomaly.FLOATING_DEPENDENCY_PATTERNS)

    def test_the_detector_fires_on_the_case_the_registry_refuses(self):
        """Same input, both moments: registration refuses it and the detector
        reports it. A rule enforced at one moment and not the other is a rule
        with a gap exactly where this round's cases live."""
        record = a_resource(owner={"type": "serveros", "ref": "session:abc123"},
                            lifecycle={"class": "persistent"})
        codes = {s["code"] for s in anomaly.detect(resources=[record])}
        self.assertIn("SESSION_OWNED_DURABLE_RESOURCE", codes)

    def test_the_detector_reports_a_floating_dependency_that_registration_admitted(self):
        """The observation half of the asymmetry: admitted as WARN at
        registration, reported as an anomaly later. Both are true statements
        about different questions — 'should this be registered' and 'is what is
        registered still what is running'."""
        signals = anomaly.detect(resources=[a_resource(note="npx playwright@latest")])
        codes = {s["code"] for s in signals}
        self.assertIn("PERSISTENT_RUNTIME_FLOATING_DEPENDENCY", codes)
        for signal in signals:
            self.assertFalse(signal["implies_stale"],
                             "a declaration finding must never imply stale")


class UncheckedTests(unittest.TestCase):
    def test_no_resources_means_the_three_codes_are_unchecked_not_absent(self):
        """An empty signal list has to be readable. Without this entry, a
        report over no resources says "no anomalies", which reads as "every
        registration is well-formed"."""
        entries = {e["input"]: e for e in anomaly.unchecked()}
        self.assertIn("resources", entries)
        self.assertEqual(entries["resources"]["state"], anomaly.UNKNOWN)
        self.assertIn("SESSION_OWNED_DURABLE_RESOURCE", entries["resources"]["why"])


if __name__ == "__main__":
    unittest.main()
