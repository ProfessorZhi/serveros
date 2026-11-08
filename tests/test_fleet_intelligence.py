"""fleet/intelligence: the cases come from the 2026-11-06 three-device audit."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fleet import intelligence as fi

# The repository defaults are what these tests read, whatever rules the host
# running them has in its own ~/.serveros/runtime-policy.yaml.
import os as _os
_os.environ["SERVEROS_RUNTIME_POLICY"] = "/nonexistent/serveros-runtime-policy.yaml"

NOW = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)
A6000 = "device:llm-lab-dell"
TOKYO = "device:tokyo-shared-dev-01"
A6000_PROFILE_GW = "profile:llm-lab-dell/main/claude-code/gateway0"
A6000_PROFILE_SUB = "profile:llm-lab-dell/main/claude-code/subscription"


def fact(subject, fact_type, value, status="OBSERVED", source="collector", age=0, **extra):
    return {"subject_ref": subject, "fact_type": fact_type, "value": value, "status": status,
            "source": source, "observed_at": (NOW - timedelta(seconds=age)).isoformat(), **extra}


class TheEnvelope(unittest.TestCase):
    def test_every_subject_kind_has_a_shape(self):
        self.assertEqual(fi.subject_kind(A6000), "device")
        self.assertEqual(fi.subject_kind("principal:llm-lab-dell/main"), "principal")
        self.assertEqual(fi.subject_kind("runtime:llm-lab-dell/main/codex"), "runtime")
        self.assertEqual(fi.subject_kind(A6000_PROFILE_GW), "profile")
        self.assertEqual(fi.subject_kind("path:llm-lab-dell/main/workspace.src"), "path")
        self.assertEqual(fi.device_of(A6000_PROFILE_GW), A6000)
        with self.assertRaises(fi.IntelligenceError):
            fi.subject_kind("llm-lab-dell")

    def test_a_declaration_cannot_claim_to_have_been_observed(self):
        with self.assertRaises(fi.IntelligenceError) as raised:
            fi.validate_fact(fact(A6000, "capability.docker", True, source="declaration"))
        self.assertIn("cannot claim OBSERVED", str(raised.exception))

    def test_verified_needs_evidence(self):
        with self.assertRaises(fi.IntelligenceError):
            fi.validate_fact(fact(A6000, "network.reach", {"target": "target:gw0", "result": "ok"},
                                  status="VERIFIED", source="verifier"))
        fi.validate_fact(fact(A6000, "network.reach", {"target": "target:gw0", "result": "ok"},
                              status="VERIFIED", source="verifier", evidence_ref="run:abc"))

    def test_a_fact_is_about_the_subject_kind_its_type_names(self):
        with self.assertRaises(fi.IntelligenceError) as raised:
            fi.validate_fact(fact(A6000, "runtime.version", "2.1.302"))
        self.assertIn("about a runtime", str(raised.exception))

    def test_a_secret_named_key_is_refused_and_not_echoed(self):
        with self.assertRaises(fi.IntelligenceError) as raised:
            fi.validate_fact(fact(A6000_PROFILE_GW, "profile.gateway",
                                  {"host": "gw0.internal", "auth": {"api_token": "sk-x"}}))
        self.assertIn("value.auth.api_token", str(raised.exception))
        self.assertNotIn("sk-x", str(raised.exception))

    def test_profile_models_name_their_source(self):
        fi.validate_fact(fact(A6000_PROFILE_GW, "profile.models",
                              [{"model": "deepseek-v4", "source": "gateway"}]))
        with self.assertRaises(fi.IntelligenceError):
            fi.validate_fact(fact(A6000_PROFILE_GW, "profile.models",
                                  [{"model": "deepseek-v4", "source": "somewhere"}]))


class Resolution(unittest.TestCase):
    def test_an_old_observation_reads_stale_not_true(self):
        old = fact(A6000, "resource.pressure", {"swap_used_pct": 73}, age=600)
        self.assertEqual(fi.current_status(old, NOW), "STALE")
        self.assertEqual(fi.resolve([old], A6000, "resource.pressure", NOW)["status"], "STALE")

    def test_nothing_known_is_unknown_never_absent(self):
        answer = fi.resolve([], A6000, "capability.docker", NOW)
        self.assertEqual((answer["status"], answer["value"]), ("UNKNOWN", None))

    def test_observation_beats_declaration_and_the_disagreement_is_a_finding(self):
        """The audit: Docker declared for A6000 by an old note, observed not installed."""
        facts = [fact(A6000, "capability.docker", True, status="DECLARED", source="declaration"),
                 fact(A6000, "capability.docker", False)]
        answer = fi.resolve(facts, A6000, "capability.docker", NOW)
        self.assertEqual((answer["status"], answer["value"]), ("OBSERVED", False))
        self.assertIn("DECLARED_OBSERVED_MISMATCH", answer["findings"])

    def test_a_declared_model_stays_declared(self):
        """Config says sonnet; nothing proves the provider until a call is verified."""
        declared = fact(A6000_PROFILE_GW, "profile.models",
                        [{"model": "sonnet", "source": "gateway"}],
                        status="DECLARED", source="declaration", age=10 ** 7)
        self.assertEqual(fi.resolve([declared], A6000_PROFILE_GW, "profile.models", NOW)["status"],
                         "DECLARED")


class PathBindings(unittest.TestCase):
    def test_the_audit_cases(self):
        self.assertEqual(fi.path_binding_state({"declared": True, "exists": True,
                                                "owner_matches": False}), "BLOCKED_IDENTITY")
        self.assertEqual(fi.path_binding_state({"declared": True, "exists": False}), "STALE_BINDING")
        self.assertEqual(fi.path_binding_state({"declared": True, "canonical_count": 2,
                                                "exists": True}), "CONFLICT")
        self.assertEqual(fi.path_binding_state({"declared": False, "in_use": True}),
                         "UNDECLARED_IN_USE")
        self.assertEqual(fi.path_binding_state({"declared": True, "exists": None}), "UNKNOWN")
        self.assertEqual(fi.path_binding_state({"declared": True, "exists": True,
                                                "owner_matches": True}), "BOUND")


class Candidates(unittest.TestCase):
    def test_choose_a_profile_by_model_source(self):
        gw = "profile:tokyo-shared-dev-01/main/claude-code/gateway0"
        sub = "profile:tokyo-shared-dev-01/main/claude-code/subscription"
        facts = [
            fact(gw, "profile.models", [{"model": "deepseek-v4", "source": "gateway"}]),
            fact(sub, "profile.models",
                 [{"model": "opus", "source": "official_subscription"},
                  {"model": "sonnet", "source": "official_subscription"}]),
        ]
        want = [{"fact_type": "profile.models", "min_status": "OBSERVED",
                 "model_source": "official_subscription"}]
        result = {r["subject_ref"]: r["eligible"] for r in fi.candidates(want, facts, [gw, sub], NOW)}
        self.assertEqual(result, {gw: False, sub: True})

    def test_unknown_fails_closed_with_a_reason(self):
        facts = [fact(TOKYO, "capability.docker", True)]
        want = [{"fact_type": "capability.docker", "min_status": "OBSERVED", "equals": True}]
        result = {r["subject_ref"]: r for r in fi.candidates(want, facts, [TOKYO, A6000], NOW)}
        self.assertTrue(result[TOKYO]["eligible"])
        self.assertFalse(result[A6000]["eligible"])
        self.assertIn("UNKNOWN < OBSERVED", result[A6000]["reasons"][0])


class TheContract(unittest.TestCase):
    def test_every_fact_type_scope_is_a_subject_kind(self):
        kinds = set(fi.contract()["subjects"])
        for name, spec in fi.contract()["fact_types"].items():
            with self.subTest(fact_type=name):
                self.assertIn(spec["scope"], kinds)
                self.assertGreater(spec["ttl_seconds"], 0)

    def test_assignment_is_not_a_fact(self):
        for name in fi.contract()["not_facts"]:
            self.assertNotIn(name, fi.contract()["fact_types"])


if __name__ == "__main__":
    unittest.main()


class TheOwnersPolicy(unittest.TestCase):
    """fleet/runtime-policy.yaml, as set on 2026-11-06.

    The A6000 pins the source and the gateway, not a model-name pattern: gateway
    0 serves DeepSeek under names that change. A name pattern belongs in the
    owner's local overlay when one is wanted.
    """

    def test_the_a6000_names_its_default_and_refuses_codex(self):
        choice = fi.default_choice("device:llm-lab-dell", "claude-code")
        self.assertEqual(choice["model"], "v4.1flash-anthropic")
        self.assertFalse(fi.default_choice("device:llm-lab-dell", "codex")["allowed"])
        self.assertEqual(fi.default_choice("device:tokyo-shared-dev-01", "claude-code")["model"], None)

    def test_the_a6000_runs_only_claude_code_through_gateway0_deepseek(self):
        ok, _ = fi.policy_allows(A6000_PROFILE_GW, model="deepseek-v4", source="gateway",
                                 gateway="target:gateway0")
        self.assertTrue(ok)
        for kwargs, words in [
            ({"model": "opus", "source": "official_subscription"}, "model source"),
            ({"model": "deepseek-v4", "source": "gateway", "gateway": "target:other"}, "gateway"),
            ({"model": "deepseek-v4", "source": "gateway"}, "UNKNOWN"),
        ]:
            with self.subTest(kwargs=kwargs):
                ok, reason = fi.policy_allows(A6000_PROFILE_GW, **kwargs)
                self.assertFalse(ok)
                self.assertIn(words, reason)
        ok, reason = fi.policy_allows("profile:llm-lab-dell/main/codex/default",
                                      model="gpt-6", source="official_subscription")
        self.assertFalse(ok)
        self.assertIn("codex is not allowed", reason)

    def test_gpt_uses_our_own_account_everywhere_else(self):
        tokyo_codex = "profile:tokyo-shared-dev-01/main/codex/default"
        self.assertTrue(fi.policy_allows(tokyo_codex, model="gpt-6",
                                         source="official_subscription")[0])
        self.assertFalse(fi.policy_allows(tokyo_codex, model="gpt-6", source="official_api")[0])

    def test_candidates_drop_a_profile_policy_forbids(self):
        sub = "profile:llm-lab-dell/main/claude-code/subscription"
        facts = [fact(sub, "profile.models", [{"model": "opus", "source": "official_subscription"}]),
                 fact(A6000_PROFILE_GW, "profile.models",
                      [{"model": "deepseek-v4", "source": "gateway"}]),
                 fact(A6000_PROFILE_GW, "profile.gateway", {"host": "gw0", "target": "target:gateway0"})]
        want_sub = [{"fact_type": "profile.models", "min_status": "OBSERVED",
                     "model_source": "official_subscription"}]
        [result] = fi.candidates(want_sub, facts, [sub], NOW)
        self.assertFalse(result["eligible"])
        self.assertIn("policy:", result["reasons"][0])
        want_gw = [{"fact_type": "profile.models", "min_status": "OBSERVED", "model_source": "gateway"}]
        [result] = fi.candidates(want_gw, facts, [A6000_PROFILE_GW], NOW)
        self.assertTrue(result["eligible"], result["reasons"])


class ThePolicyIsNotHardcoded(unittest.TestCase):
    def setUp(self):
        import os, tempfile
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "runtime-policy.yaml")
        self._old = os.environ.get("SERVEROS_RUNTIME_POLICY")  # the module default
        os.environ["SERVEROS_RUNTIME_POLICY"] = self.path

    def tearDown(self):
        import os
        if self._old is None:
            os.environ.pop("SERVEROS_RUNTIME_POLICY", None)
        else:
            os.environ["SERVEROS_RUNTIME_POLICY"] = self._old
        self._dir.cleanup()

    def write(self, text):
        import os, time
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(self.path, (time.time() + 1, time.time() + 1))

    def test_without_a_local_file_the_repository_default_applies(self):
        self.assertEqual(fi.runtime_policy()["source"], "repository-default")

    def test_a_local_file_changes_a_device_rule_without_a_code_change(self):
        self.write("devices:\n  device:llm-lab-dell:\n    runtimes:\n      claude-code:\n"
                   "        model_sources: [gateway, official_subscription]\n")
        ok, _ = fi.policy_allows(A6000_PROFILE_GW, model="opus", source="official_subscription")
        self.assertTrue(ok)
        self.assertEqual(fi.runtime_policy()["source"], self.path)

    def test_a_local_null_removes_a_device_rule_back_to_default(self):
        self.write("devices:\n  device:llm-lab-dell: null\n")
        self.assertTrue(fi.policy_allows("profile:llm-lab-dell/main/codex/default",
                                         model="gpt-6", source="official_subscription")[0])
