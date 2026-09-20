"""SYS-02's kernel: the closed vocabulary and the release verdict.

Hermetic. The contract is read from the repo under test, and every world is
built in memory — nothing here reads or writes this node's live
`state/serveros/resource-leases/`, and nothing here stops, removes or restarts
anything.

The four counterexamples the acceptance row names for SYS-02 are:
last-reference-exit, crash/lease-expiry, external/protected, and
persistent/shared-must-not-be-stopped. Each has its own test below, and the
load-bearing assertion in each is the same shape: the answer that would let a
reader act is distinguishable from the answer that would let a reader act
WRONGLY.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

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


environment = _load("serveros_runtime_environment", "environment.py")
ownership_gate = _load("serveros_runtime_ownership_gate", "ownership_gate.py")

NOW = 1_800_000_000.0


def references(*rows):
    return list(rows)


# ---------------------------------------------------------------------------
# the closed vocabulary
# ---------------------------------------------------------------------------

class TestVocabulary:
    def test_the_module_and_the_contract_declare_one_schema(self):
        assert environment.SCHEMA == environment.contract()["schema"]

    def test_the_vocabularies_are_the_contracts_not_a_python_copy(self):
        contract = environment.contract()
        assert environment.scopes() == tuple(contract["scopes"])
        assert environment.sharing_modes() == tuple(contract["sharing"])
        assert environment.reference_states() == tuple(contract["reference_states"])
        assert environment.attachment_states() == tuple(contract["attachment_states"])
        assert environment.verdicts() == tuple(contract["verdicts"])

    def test_this_scope_is_not_another_modules_scope(self):
        """The contract's header warns about three homonyms; two are checkable.

        `managed-runtime-contract.yaml#scopes` is `user/system` and
        `memory/scope.py` is `(ITEM, PROJECT, USER)`. A resource-lease scope
        that accepted either would be attaching a reference in a vocabulary
        nobody else reads.
        """
        assert "user" not in environment.scopes()
        assert "system" not in environment.scopes()
        assert set(environment.scopes()) == {
            "session", "work-item", "project", "device-persistent", "external"}
        assert environment.scopes() != ("ITEM", "PROJECT", "USER")

    @pytest.mark.parametrize("value", ["", None, "SESSION", "cluster"])
    def test_an_invented_scope_is_refused(self, value):
        with pytest.raises(environment.Rejected):
            environment.parse_scope(value)

    @pytest.mark.parametrize("value", ["", None, "SHARED", "pooled"])
    def test_an_invented_sharing_mode_is_refused(self, value):
        with pytest.raises(environment.Rejected):
            environment.parse_sharing(value)

    def test_rejected_is_a_value_error(self):
        """Callers that catch `ValueError` keep working, as elsewhere."""
        assert issubclass(environment.Rejected, ValueError)

    def test_release_is_declared_destructive_rather_than_decided_per_call(self):
        """The tier is named in the contract so the verdict is written against it.

        An unnamed tier is how a removal action bypasses a policy that was
        written for removals: `ownership-contract.yaml#decision_table` files
        its rows under `action: destructive`.
        """
        assert environment.release_action() == {"kind": "destructive"}

    def test_a_fourth_verdict_is_not_reachable(self):
        assert set(environment.verdicts()) == {
            "ALLOW", "REQUIRE_APPROVAL", "DENY", "RETAIN"}

    @pytest.mark.parametrize("owner_class", ["SELF", "OURS", "UNMANAGED", "external"])
    def test_a_fourth_owner_class_is_refused(self, owner_class):
        """The classes are `ownership_gate`'s. This module adds none."""
        with pytest.raises(environment.Rejected):
            environment.release_verdict(
                sharing="exclusive", scope="session", owner_class=owner_class,
                protection=None, reference_count=0)

    def test_an_absent_owner_class_falls_back_to_unknown(self):
        """`None` and `""` reach the gate's own UNKNOWN — the conservative
        direction — and the provenance field is what keeps that from reading
        as a finding."""
        verdict = environment.release_verdict(
            sharing="exclusive", scope="session", owner_class=None,
            protection=None, reference_count=0)
        assert verdict["owner_class"] == "UNKNOWN"
        assert verdict["verdict"] == environment.DENY


# ---------------------------------------------------------------------------
# a reference's three-part life
# ---------------------------------------------------------------------------

class TestReferenceLifetime:
    def test_a_reference_with_no_expiry_is_a_declaration_not_a_missing_value(self):
        row = environment._reference_row(
            {"scope": "session", "holder": "conv-a"}, now=NOW)
        assert row["state"] == environment.LIVE
        assert row["expiry"] == "none"

    def test_a_reference_inside_its_ttl_is_live(self):
        row = environment._reference_row(
            {"scope": "work-item", "holder": "SRV-0011", "expires_at": NOW + 60},
            now=NOW)
        assert row["state"] == environment.LIVE
        assert row["expiry"] == "at"

    def test_a_reference_past_its_ttl_is_expired_and_says_why(self):
        row = environment._reference_row(
            {"scope": "work-item", "holder": "SRV-0011", "expires_at": NOW - 1},
            now=NOW)
        assert row["state"] == environment.EXPIRED
        # Reported WITH its holder: the whole use of an expired reference is
        # knowing who to ask. And with a reason that stops short of concluding:
        # a crash and a slow holder are indistinguishable from here.
        assert row["holder"] == "SRV-0011"
        assert "unbacked" in row["why"]
        assert row["state"] != environment.RETIRED

    def test_a_retired_reference_is_the_holders_own_act_and_not_an_expiry(self):
        row = environment._reference_row(
            {"scope": "work-item", "holder": "SRV-0011", "retired_at":
             "2026-09-20T00:00:00+00:00", "expires_at": NOW - 1}, now=NOW)
        assert row["state"] == environment.RETIRED

    def test_expiry_is_re_decided_on_every_read(self):
        """A stored state would be free to disagree with its own input."""
        reference = {"scope": "session", "holder": "c", "expires_at": NOW + 1}
        assert environment.reference_state(reference, now=NOW) == environment.LIVE
        assert environment.reference_state(reference, now=NOW + 2) == environment.EXPIRED

    @pytest.mark.parametrize("state", ["live", "gone", ""])
    def test_an_invented_reference_state_is_refused(self, state):
        with pytest.raises(environment.Rejected):
            environment.parse_reference_state(state)


# ---------------------------------------------------------------------------
# occupancy — and the two answers that must not be confused
# ---------------------------------------------------------------------------

class TestAttachmentState:
    def test_a_live_reference_holds_it(self):
        rows = [environment._reference_row(
            {"scope": "session", "holder": "c"}, now=NOW)]
        assert environment.attachment_state(rows, attached=None) == environment.HELD

    def test_a_live_declaration_outranks_an_observation_that_it_is_gone(self):
        """The declaration is the fact a holder is relying on. An observation
        that contradicts it is a question for a person, not a release."""
        rows = [environment._reference_row(
            {"scope": "session", "holder": "c"}, now=NOW)]
        assert environment.attachment_state(rows, attached=False) == environment.HELD

    def test_last_reference_exit_is_releasable_not_unattached(self):
        """Counterexample 1: the last reference exits.

        Nobody observed whether the resource is still there, and "not known to
        be gone" is not "gone" — reporting UNATTACHED here is the default
        value that deletes things.
        """
        assert environment.attachment_state([], attached=None) == environment.RELEASABLE

    def test_a_crashed_holders_claim_stays_visible(self):
        """Counterexample 2: crash / lease expiry.

        A holder whose claim stopped being backed is UNRESOLVED. It is
        RELEASABLE — so a person can resolve it — and never silently UNATTACHED,
        which would erase the only evidence that the holder ever existed.
        """
        rows = [environment._reference_row(
            {"scope": "work-item", "holder": "crashed", "expires_at": NOW - 3600},
            now=NOW)]
        state = environment.attachment_state(rows, attached=False)
        assert state == environment.RELEASABLE
        assert rows[0]["holder"] == "crashed"

    def test_unattached_is_reserved_for_a_caller_that_looked_and_found_nothing(self):
        """The narrowest of the three cases, and the only one that makes
        `absent_means: NO_LEASES_HELD` true."""
        assert environment.attachment_state([], attached=False) == environment.UNATTACHED


# ---------------------------------------------------------------------------
# the release verdict
# ---------------------------------------------------------------------------

class TestRetentionOrdering:
    def test_ownership_and_protection_precede_sharing_and_persistence(self):
        """The order is policy, not formatting.

        `ownership-contract.yaml`'s own discipline: once a rule fires, no later
        rule is consulted, because a reachable later rule is a rule that can
        overturn it. A shared resource of unknown ownership is therefore
        OWNER_UNKNOWN and not RETAIN — neither verdict touches the resource, so
        the question is which sentence a person reads next.
        """
        rules = environment.retention_rules()
        conditions = [rule["when"] for rule in rules]
        assert conditions.index('owner_class == "UNKNOWN"') < \
            conditions.index('sharing == "shared"')
        assert conditions.index('owner_class == "EXTERNAL"') < \
            conditions.index('sharing == "shared"')
        assert conditions.index("protection is True") < \
            conditions.index('sharing == "shared"')
        assert conditions.index('owner_class == "UNKNOWN"') == 0

    def test_unknown_ownership_beats_shared(self):
        verdict = environment.release_verdict(
            sharing="shared", scope="device-persistent", owner_class="UNKNOWN",
            protection=None, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.DENY, "OWNER_UNKNOWN")

    def test_external_ownership_is_refused_with_the_gates_own_code(self):
        verdict = environment.release_verdict(
            sharing="exclusive", scope="project", owner_class="EXTERNAL",
            protection=None, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.DENY, "OWNER_EXTERNAL")

    def test_a_self_declared_external_scope_is_refused_too(self):
        """"We are not its reclaimer" is reachable from the declaration alone."""
        verdict = environment.release_verdict(
            sharing="exclusive", scope="external", owner_class="MANAGED",
            protection=None, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.DENY, "OWNER_EXTERNAL")

    def test_protection_beats_shared(self):
        """Counterexample 3, protection half: a protected resource is never a
        release candidate, whatever its sharing mode says."""
        verdict = environment.release_verdict(
            sharing="shared", scope="device-persistent", owner_class="MANAGED",
            protection=True, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.DENY, "PROTECTED_SUBJECT")

    def test_shared_is_retained_rather_than_stopped(self):
        """Counterexample 4, first half: a shared instance is not started for
        any one reference, so stopping it when the last exits makes the next
        holder pay for the previous holder's ending."""
        verdict = environment.release_verdict(
            sharing="shared", scope="session", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.RETAIN, "SHARED_RESOURCE_NOT_REFERENCE_OWNED")

    def test_device_persistent_is_retained_rather_than_stopped(self):
        """Counterexample 4, second half."""
        verdict = environment.release_verdict(
            sharing="exclusive", scope="device-persistent", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert (verdict["verdict"], verdict["code"]) == \
            (environment.RETAIN, "DEVICE_PERSISTENT_NOT_REFERENCE_OWNED")

    def test_a_managed_unprotected_resource_is_allowed(self):
        verdict = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert verdict["verdict"] == environment.ALLOW
        assert verdict["code"] is None


class TestVerdictIsReadFromTheGate:
    def test_the_managed_rows_agree_with_the_gates_rows(self):
        """Two contracts state this verdict, so their agreement is CHECKED.

        `ownership-contract.yaml#decision_table` files MANAGED / protected-or-
        not / destructive. `environment-contract.yaml#retention` states the
        protected case again, because protection has to be decided BEFORE
        sharing and persistence for the ordering to mean anything. Overlap
        between two contracts is only safe while something notices the day they
        disagree.
        """
        table = ownership_gate.contract()["decision_table"]
        for protection, wanted in ((True, "protected"), (False, "not_protected")):
            row = next(r for r in table
                       if r.get("owner_class") == "MANAGED"
                       and r.get("protection") == wanted
                       and r.get("action") == "destructive")
            verdict = environment.release_verdict(
                sharing="exclusive", scope="work-item", owner_class="MANAGED",
                protection=protection, reference_count=0)
            assert verdict["verdict"] == row["decision"]
            # `.get`: the ALLOW row carries no code, and an absent code is the
            # absence, not an empty string a reader could mistake for a value.
            assert verdict["code"] == row.get("code")

    def test_the_ordering_section_answers_the_protected_case(self):
        """Which half answers, and why the reader is told."""
        protected = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=True, reference_count=0)
        assert protected["rule_from"] == "environment-contract.yaml#retention"

        unprotected = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert unprotected["rule_from"] == \
            "runtime/ownership-contract.yaml#decision_table"

    def test_the_managed_row_is_read_at_call_time_and_not_copied(self, monkeypatch):
        """Change the gate's table and this module's answer changes with it.

        A source grep for the right spelling would pass on a copy that happened
        to be spelled the same way. This is the property itself.
        """
        monkeypatch.setattr(ownership_gate, "contract", lambda: {"decision_table": [{
            "owner_class": "MANAGED", "protection": "not_protected",
            "action": "destructive", "decision": "REQUIRE_APPROVAL",
            "code": "FROM_THE_CONTRACT", "because": "a row that changed"}]})
        verdict = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert verdict["verdict"] == "REQUIRE_APPROVAL"
        assert verdict["code"] == "FROM_THE_CONTRACT"
        assert verdict["because"] == "a row that changed"

    def test_the_gate_is_not_called_for_a_lease_subject(self):
        """`decide` takes a subject with a resource kind and a trusted context,
        and a lease is neither. Calling it would mean inventing a kind to fit,
        which is how a rule acquires a subject it was not written for."""
        source = (RUNTIME / "environment.py").read_text(encoding="utf-8")
        assert ".decide(" not in source
        assert "decision_table" in source

    def test_undeclared_protection_is_not_unprotected(self):
        """The tri-state is carried, not collapsed.

        `None` and `False` reach the same verdict here, and they are not the
        same record: an undeclared protection reported as unprotected is a
        bypass performed by a default.
        """
        undeclared = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=None, reference_count=0)
        unprotected = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert undeclared["verdict"] == unprotected["verdict"] == environment.ALLOW
        assert undeclared["protection"] == {"protected": None, "declared": False}
        assert unprotected["protection"] == {"protected": False, "declared": True}

    def test_owner_class_provenance_is_carried(self):
        """OWNER_UNKNOWN is reached by a finding and by a gap. Both DENY, and
        they are not the same sentence to read."""
        gap = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="UNKNOWN",
            protection=None, reference_count=0, owner_class_source="not_gathered")
        finding = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="UNKNOWN",
            protection=None, reference_count=0, owner_class_source="gathered")
        assert gap["code"] == finding["code"] == "OWNER_UNKNOWN"
        assert gap["owner_class_source"] == "not_gathered"
        assert finding["owner_class_source"] == "gathered"

    def test_a_release_with_references_remaining_is_recorded_not_refused(self):
        """A holder may hand a reference back early. The note is what stops
        "released with two live references" from being reconstructed later."""
        verdict = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="MANAGED",
            protection=False, reference_count=2)
        assert verdict["verdict"] == environment.ALLOW
        assert verdict["reference_count"] == 2
        assert "2 live reference(s) remain" in verdict["note"]

    def test_a_missing_decision_table_row_is_a_gap_not_a_licence(self, monkeypatch):
        monkeypatch.setattr(ownership_gate, "contract", lambda: {"decision_table": []})
        with pytest.raises(environment.Rejected):
            environment.release_verdict(
                sharing="exclusive", scope="work-item", owner_class="MANAGED",
                protection=False, reference_count=0)

    def test_a_reason_from_yaml_is_collapsed_to_one_line(self):
        """Folded scalars carry the source's newlines, and a reason printed
        with them reads as several sentences that are not there."""
        verdict = environment.release_verdict(
            sharing="exclusive", scope="work-item", owner_class="UNKNOWN",
            protection=None, reference_count=0)
        assert "\n" not in verdict["because"]
        assert verdict["because"].strip() == verdict["because"]


# ---------------------------------------------------------------------------
# the §6 equation
# ---------------------------------------------------------------------------

class TestEffective:
    def _desired(self, **overrides):
        declaration = {"scope": "work-item", "sharing": "exclusive",
                       "references": references(
                           {"scope": "work-item", "holder": "SRV-0011"})}
        declaration.update(overrides)
        return {"port:9100": declaration}

    def test_an_unmeasured_reference_count_is_none_and_never_zero(self):
        """The one substitution §6 forbids, in the one field where it is
        cheapest to make: "no references" and "no evidence about references"
        are opposite decisions."""
        out = environment.effective(
            desired={"thing": {"scope": "project", "sharing": "exclusive",
                               "references": None}},
            observed={}, ownership={}, policy={}, context={}, now=NOW)
        row = out["resources"][0]
        assert row["references"] is None
        assert row["ref_count"] is None
        assert row["live_count"] is None
        assert row["attachment_state"] is None
        assert row["release"] is None
        assert row["releasable"] is None
        assert "not" in row["note"] and "free" in row["note"]
        assert out["counts"]["unmeasured"] == 1
        assert out["counts"]["held"] == 0
        assert out["counts"]["releasable"] == 0

    def test_an_observed_undeclared_resource_is_a_candidate_and_not_desired(self):
        out = environment.effective(
            desired={}, observed={"mystery:thing": {"attached": True}},
            ownership={}, policy={}, context={}, now=NOW)
        assert out["resources"] == []
        assert [row["resource"] for row in out["candidates"]] == ["mystery:thing"]
        assert out["candidates"][0]["state"] == "CANDIDATE"
        assert out["counts"] == {"declared": 0, "held": 0, "releasable": 0,
                                 "unmeasured": 0, "orphaned": 0, "candidates": 1}

    def test_authoritative_ownership_allows_a_releasable_resource(self):
        out = environment.effective(
            desired=self._desired(), observed={},
            ownership={"port:9100": {"class": "MANAGED"}},
            policy={"port:9100": {"protected": False}}, context={}, now=NOW)
        row = out["resources"][0]
        assert row["release"]["verdict"] == environment.ALLOW
        assert row["release"]["owner_class_source"] == "gathered"

    def test_releasable_needs_both_the_state_and_the_verdict(self):
        """`attachment_state: RELEASABLE` says the last reference exited.
        `releasable` says there is something to let go AND it may be let go."""
        unattributed = environment.effective(
            desired=self._desired(references=None), observed={},
            ownership={}, policy={}, context={}, now=NOW)
        assert unattributed["resources"][0]["releasable"] is None

        denied = environment.effective(
            desired=self._desired(), observed={}, ownership={}, policy={},
            context={}, now=NOW)
        row = denied["resources"][0]
        assert row["attachment_state"] == environment.HELD
        assert row["releasable"] is False

    def test_a_crashed_holder_is_counted_apart_from_the_held_count(self):
        """`held: 0` alone would report an idle node while a crashed holder's
        claim sat in the record."""
        out = environment.effective(
            desired=self._desired(references=references(
                {"scope": "work-item", "holder": "crashed",
                 "expires_at": NOW - 60})),
            observed={}, ownership={}, policy={}, context={}, now=NOW)
        assert out["counts"]["held"] == 0
        assert out["counts"]["orphaned"] == 1
        row = out["resources"][0]
        assert row["attachment_state"] == environment.RELEASABLE
        assert row["expired_count"] == 1

    def test_owner_class_provenance_separates_a_gap_from_a_finding(self):
        def source(ownership):
            return environment.effective(
                desired=self._desired(), observed={}, ownership=ownership,
                policy={}, context={}, now=NOW)["resources"][0]["release"]

        assert source({})["owner_class_source"] == "not_gathered"
        assert source({"other:thing": {"class": "MANAGED"}})[
            "owner_class_source"] == "not_attributed"
        assert source({"port:9100": {"class": "MANAGED"}})[
            "owner_class_source"] == "gathered"

    def test_every_unmeasured_owner_class_still_denies(self):
        """The conservative direction survives the honesty fix: a gap is not
        permission."""
        out = environment.effective(
            desired=self._desired(), observed={}, ownership={}, policy={},
            context={}, now=NOW)
        assert out["resources"][0]["release"]["verdict"] == environment.DENY

    def test_context_and_time_are_passed_through(self):
        out = environment.effective(
            desired={}, observed={}, ownership={}, policy={},
            context={"context_ref": "ctx:1", "actor_ref": "actor:1"}, now=NOW)
        assert out["context"] == {"context_ref": "ctx:1", "actor_ref": "actor:1"}
        assert out["as_of"] == NOW

    def test_the_resources_are_ordered_so_two_reads_agree(self):
        out = environment.effective(
            desired={"b:1": {"scope": "session", "sharing": "shared",
                             "references": []},
                     "a:1": {"scope": "session", "sharing": "shared",
                             "references": []}},
            observed={}, ownership={}, policy={}, context={}, now=NOW)
        assert [row["resource"] for row in out["resources"]] == ["a:1", "b:1"]


# ---------------------------------------------------------------------------
# it decides and records; it does not act
# ---------------------------------------------------------------------------

class TestNoActuator:
    def test_the_kernel_cannot_stop_or_remove_anything(self):
        """`daemon.reconcile` is the only code path in this product that stops
        or restarts anything, and it is gated on a budget and an explicit
        enable. A second actuator here is the mistake the `new-lease-system`
        non-goal was written about."""
        source = (RUNTIME / "environment.py").read_text(encoding="utf-8")
        for forbidden in ("subprocess", "shutil", "os.kill", "SIGTERM", "SIGKILL",
                          "os.remove", "os.unlink", "rmtree", "Popen"):
            assert forbidden not in source, forbidden

    def test_the_kernel_does_not_import_the_daemon(self):
        """Prose may name the daemon; importing it is how a second actuator
        gets within reach."""
        source = (RUNTIME / "environment.py").read_text(encoding="utf-8")
        assert "daemon.py" not in source
        assert "import daemon" not in source
        # `now` is an argument everywhere, never read from a clock, so a
        # verdict cannot depend on when it was asked for.
        assert "time." not in source

    def test_a_retain_verdict_releases_nothing(self):
        verdict = environment.release_verdict(
            sharing="shared", scope="session", owner_class="MANAGED",
            protection=False, reference_count=0)
        assert verdict["verdict"] == environment.RETAIN
        assert verdict["verdict"] != environment.ALLOW
