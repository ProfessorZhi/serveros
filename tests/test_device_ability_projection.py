#!/usr/bin/env python3
"""G1 — the projection's four words, and the one it refuses to say.

WHY THIS FILE EXISTS
--------------------
`CAPABILITY-PROJECTION.md` says Fleet Next has no Device Ability projection, and names the
two disciplines one must obey: `effective` is null unless a device actually reported it, and
"callable" is a conjunction that a projection reporting only its first term would be read as
answering. Those are the two things this file exists to pin.

Hermetic: a synthetic manifest + synthetic devices. Nothing here contacts a device, invokes
an Ability, reads a credential or runs a GPU job. The one test that touches the real
authority manifest is skipped unless `SERVEROS_ABILITY_REPO` names a checkout, and even then
it only parses it.

THE ASYMMETRY THIS FILE PINS
----------------------------
`abilities/resolver.py` may answer AVAILABLE without a `health` or `policy` observation, and
that is correct for the resolver — `_input_policy`'s own comment: "policy is a filter over an
answer, not a capability input". The projection asks a narrower question, so the same inputs
must produce `effective: null`. A test that only asserted "the resolver said AVAILABLE" would
miss exactly the defect this module exists to prevent.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HERE = REPO / "integrations" / "device-ability-projection"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


effective = _load("device_ability_effective", HERE / "effective.py")

MANIFEST = json.loads((HERE / "fixtures" / "manifest.json").read_text(encoding="utf-8"))
DEVICES = json.loads((HERE / "fixtures" / "devices.json").read_text(encoding="utf-8"))
SOURCE_REVISION = "494f45293e53bc8e84e2025f7ac567e6cfb607d8"


def scenario(prefix: str) -> dict:
    for device in DEVICES:
        if str(device.get("_scenario", "")).startswith(prefix):
            return device
    raise KeyError(prefix)


def project(device: dict, **over) -> dict:
    return effective.project_device(manifest=MANIFEST,
                                    source_revision=SOURCE_REVISION,
                                    device=device, **over)


def row(projection: dict, name: str) -> dict:
    for ability in projection["abilities"]:
        if ability["ability"] == name:
            return ability
    raise KeyError(name)


# ---------------------------------------------------------------------------
# 正控：设备报全了，才有 effective
# ---------------------------------------------------------------------------

class TestADeviceThatReportedEverything:
    def test_installed_comes_from_the_authority_not_from_an_observation(self):
        projection = project(scenario("full"))
        names = [a["ability"] for a in projection["abilities"]]
        assert names == ["alpha-ability", "beta-ability"]
        # gamma is installed on a device this projection was not asked about.
        assert "gamma-ability" not in names

    def test_every_installed_ability_carries_its_source_revision(self):
        projection = project(scenario("full"))
        assert projection["source_revision"] == SOURCE_REVISION
        for ability in projection["abilities"]:
            assert ability["source_revision"] == SOURCE_REVISION
            assert ability["fingerprint"].startswith("sha256:")

    def test_a_full_report_yields_an_effective_provider_id(self):
        ability = row(project(scenario("full")), "alpha-ability")
        assert ability["effective"] == "provider:alpha"
        assert ability["status"] == "AVAILABLE"
        assert ability["supported"] == "SUPPORTED"
        assert ability["ready"] == "READY"
        assert ability["grant"] == "ALLOWED"

    def test_the_binding_carries_device_principal_and_runtime(self):
        ability = row(project(scenario("full")), "alpha-ability")
        assert ability["binding"] == {
            "device_ref": "device:tokyo-shared-dev-01",
            "device_ids": ["tokyo-shared-dev-01"],
            "principal": "person:vince",
            "runtime": "claude-code",
        }

    def test_the_semantic_family_comes_from_the_manifest(self):
        assert row(project(scenario("full")), "alpha-ability")["semantic_family"] == "dev-flow"

    def test_a_missing_semantic_family_is_stated_rather_than_invented(self):
        ability = row(project(scenario("full")), "beta-ability")
        assert ability["semantic_family"] is None
        assert "neither `family` nor `domain.primary`" in ability["semantic_family_why"]


# ---------------------------------------------------------------------------
# 负控 1：resolver 说 AVAILABLE，投影仍然不说 callable
# ---------------------------------------------------------------------------

class TestTheResolversYesIsNotEnough:
    def test_no_health_and_no_policy_means_not_effective(self):
        ability = row(project(scenario("no health")), "alpha-ability")
        # The resolver answered from the inputs it had...
        assert ability["status"] == "AVAILABLE"
        # ...and the projection still refuses the stronger word.
        assert ability["effective"] is None
        assert ability["ready"] == "UNKNOWN" and ability["grant"] == "UNKNOWN"
        assert "ready" in ability["effective_why"] and "grant" in ability["effective_why"]

    def test_the_resolvers_own_answer_is_kept_verbatim(self):
        ability = row(project(scenario("no health")), "alpha-ability")
        assert ability["status"] == "AVAILABLE"
        assert ability["reason"] is None
        assert isinstance(ability["candidates"], list) and ability["candidates"]

    def test_effective_is_never_a_boolean(self):
        for device in DEVICES:
            for ability in project(device)["abilities"]:
                assert ability["effective"] is None or isinstance(ability["effective"], str)


# ---------------------------------------------------------------------------
# 负控 2：授权被拒
# ---------------------------------------------------------------------------

class TestADeniedGrant:
    def test_a_denied_policy_is_denied_and_not_effective(self):
        ability = row(project(scenario("denied")), "alpha-ability")
        assert ability["grant"] == "DENIED"
        assert ability["effective"] is None
        assert ability["reason"] == "POLICY_DENIED"

    def test_the_other_ability_is_unaffected(self):
        projection = project(scenario("denied"))
        assert row(projection, "alpha-ability")["effective"] is None
        assert row(projection, "beta-ability")["effective"] == "provider:beta"


# ---------------------------------------------------------------------------
# 负控 3：健康
# ---------------------------------------------------------------------------

class TestAnUnhealthyProvider:
    def test_a_failed_probe_is_not_ready(self):
        ability = row(project(scenario("unhealthy")), "alpha-ability")
        assert ability["ready"] == "NOT_READY"
        assert ability["status"] == "UNAVAILABLE"
        assert ability["reason"] == "RUNTIME_UNHEALTHY"
        assert ability["effective"] is None

    def test_a_healthy_sibling_is_still_effective(self):
        assert row(project(scenario("unhealthy")), "beta-ability")["effective"] == "provider:beta"


# ---------------------------------------------------------------------------
# 负控 4：设备 / 主体 / 请求形状
# ---------------------------------------------------------------------------

class TestTheBindingItselfIsChecked:
    def test_a_device_the_manifest_does_not_know_reports_zero_abilities(self):
        projection = project(scenario("not this device"))
        assert projection["abilities"] == []
        assert projection["counts"]["installed"] == 0
        # Zero here is a fact about the manifest, not a claim that none exist anywhere.
        assert projection["device_ids"] == ["unheard-of"]

    def test_the_installation_list_is_per_device(self):
        """`some-other-device` is a device the manifest DOES know — and it sees only
        the ability installed there. A projection that listed the same set for every
        device would pass the zero-ability test above and still be wrong."""
        projection = project(scenario("another device"))
        assert [a["ability"] for a in projection["abilities"]] == ["gamma-ability"]
        assert projection["abilities"][0]["effective"] is None

    def test_a_binding_without_a_principal_is_refused(self):
        device = dict(scenario("full"))
        device.pop("principal")
        with pytest.raises(effective.Refused) as caught:
            project(device)
        assert "principal" in str(caught.value)

    def test_a_binding_without_a_runtime_is_refused(self):
        device = dict(scenario("full"))
        device.pop("runtime")
        with pytest.raises(effective.Refused) as caught:
            project(device)
        assert "runtime" in str(caught.value)

    def test_an_undeclared_observation_axis_is_refused(self):
        device = dict(scenario("full"))
        device["observations"] = dict(device["observations"], capabilities={"network": True})
        with pytest.raises(effective.Refused) as caught:
            project(device)
        assert "not declared axes" in str(caught.value)

    def test_a_projection_without_a_source_revision_is_refused(self):
        with pytest.raises(effective.Refused) as caught:
            effective.project_device(manifest=MANIFEST, source_revision="",
                                     device=scenario("full"))
        assert "source_revision" in str(caught.value)

    def test_a_projection_about_no_device_is_refused(self):
        with pytest.raises(effective.Refused) as caught:
            effective.project(manifest=MANIFEST, source_revision=SOURCE_REVISION, devices=[])
        assert "non-empty" in str(caught.value)


# ---------------------------------------------------------------------------
# 不造第二个 registry：输出里的每一项都来自 manifest / resolver
# ---------------------------------------------------------------------------

class TestItAddsNothingOfItsOwn:
    def test_every_projected_ability_exists_in_the_manifest(self):
        declared = {f"ability:{e['name']}" for e in MANIFEST["abilities"]}
        for device in DEVICES:
            for ability in project(device)["abilities"]:
                assert ability["ability_ref"] in declared

    def test_the_projection_is_deterministic(self):
        first = effective.project(manifest=MANIFEST, source_revision=SOURCE_REVISION,
                                  devices=[scenario("full")])
        second = effective.project(manifest=MANIFEST, source_revision=SOURCE_REVISION,
                                   devices=[scenario("full")])
        assert first == second

    def test_the_counts_agree_with_the_rows(self):
        projection = effective.project(manifest=MANIFEST, source_revision=SOURCE_REVISION,
                                       devices=DEVICES)
        rows = [a for d in projection["devices"] for a in d["abilities"]]
        assert projection["counts"]["installed"] == len(rows)
        assert projection["counts"]["effective"] == sum(1 for a in rows if a["effective"])


# ---------------------------------------------------------------------------
# The real authority, when a checkout is pointed at. Skipped otherwise.
# ---------------------------------------------------------------------------

AUTHORITY_REPO = os.environ.get("SERVEROS_ABILITY_REPO")
if not AUTHORITY_REPO or not Path(AUTHORITY_REPO).is_dir():
    AUTHORITY_REPO = None


@pytest.mark.skipif(AUTHORITY_REPO is None,
                    reason="set SERVEROS_ABILITY_REPO to an ability-management checkout")
class TestAgainstTheRealManifest:
    def test_the_authoritative_manifest_parses_and_carries_installations(self):
        manifest, resolved = effective.read_source(repository=AUTHORITY_REPO,
                                                   revision="origin/main")
        assert isinstance(manifest, dict)
        assert resolved and len(resolved) >= 7
        assert manifest.get("abilities")
        with_installations = [a for a in manifest["abilities"]
                              if any(rev.get("installations") for rev in a.get("revisions") or [])]
        assert with_installations, "the authority records at least one installation"
