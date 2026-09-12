#!/usr/bin/env python3
"""Tests for `architecture_check`, starting with the composition/registry gate.

This checker had no tests at all, and that is how it came to contain a rule its
own comment contradicted. `A1` compared the bundle's FULL member list against
the registry's `provides`, while the comment two lines above said only the
ability-management-sourced members had to match. Nothing compared the two
statements, so nothing noticed.

It stayed invisible because the bundle had exactly one locally-sourced skill —
`serveros` — and `serveros` happens to be in `provides`, so the two rules agreed
by coincidence. Adding a second local skill (`runtime-management`) ended the
coincidence and turned the contradiction into a HIGH finding about a bundle that
was correct.

The tests below pin the gate in both directions, because a check repaired in one
direction only is a check that has been switched off rather than fixed:

  * a distributed ability missing from `provides`  -> HIGH  (must still fire)
  * a provided ability composed into nothing       -> HIGH  (must still fire)
  * a locally-sourced member, registered or not    -> no drift either way

Hermetic: three throwaway git repositories. The declaration files are copied
from this repository rather than hand-written, so these tests are about the
comparison and not about whether the fixture happens to resemble a real bundle.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills/serveros/scripts"
OPS_SOURCE = Path("/opt/applications/data/home-hwz/workspace/ops")

# What the real compositions ship from ability-management, held as a literal so
# that a change to `plugin/bundle.yaml` or `plugin/profiles/` shows up here as a
# decision rather than silently redefining what these tests assert.
#
# `runtime-management` joined on §24. It was a serveros-local member before, and
# the fixtures below still exercise that shape deliberately — they pass
# `local=(...)` explicitly — so this literal tracks the real compositions
# without removing the case that made the A1 contradiction visible.
SHIPPED = ("ability-management", "runtime-management", "test-management",
           "workspace-management")

# Recommended on demand by `serveros-linux-work`, and deliberately NOT shipped
# by any composition. The registry provides these too, and that asymmetry is the
# point of §28's on-demand layer: the registry says what a node can resolve, not
# what an artifact carries.
#
# The set is "the registry, minus what is shipped", so it grows when the
# registry does and the profile has to answer for it. That is the intended
# coupling: a newly registered ability that no composition ships and no
# composition recommends is the A1 drift this list exists to make visible.
RECOMMENDED = ("information-management", "work-management",
               "feishu-space-management", "feishu-report")

# Everything the registry lists in `provides` for the plugin.
DISTRIBUTED = SHIPPED + RECOMMENDED
PLUGIN_ID = "ProfessorZhi/hwz-server-os"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("serveros_architecture_check", "architecture_check.py")


# ---------------------------------------------------------------------------
# where the composition rule is read from
# ---------------------------------------------------------------------------

# The path the pin names, relative to the ability-management repository root.
EVALUATOR_REL = "skills/ability-management/payload/composition.py"


def _workspace_roots(start: Path) -> list[Path]:
    """Ancestors that look like a workspace: they hold both `src/` and `worktrees/`."""
    found = []
    for parent in [start, *start.parents]:
        if (parent / "src").is_dir() and (parent / "worktrees").is_dir():
            found.append(parent)
            break
    return found


def evaluator_source() -> Path:
    """The real `composition.py`, read from the ability-management side.

    NOT hand-written into this fixture. The rule is implemented once, in
    ability-management, and serveros executes it out of the pinned ability
    revision; a fixture carrying its own copy would be testing a stand-in that
    agrees with the real rule only until the first edit of either one, which is
    exactly the divergence this design exists to make impossible.

    The search order is explicit and the absence of a hit is a failure rather
    than a skip: a test that quietly stops comparing is indistinguishable from
    one that found nothing to compare.
    """
    explicit = os.environ.get("SERVEROS_COMPOSITION_EVALUATOR")
    candidates: list[Path] = [Path(explicit)] if explicit else []
    for ws in _workspace_roots(REPO):
        # The canonical checkout first: once the rule is merged there, the
        # fixture stops depending on which worktree happens to exist.
        candidates.append(ws / "src" / "ability-management" / EVALUATOR_REL)
        candidates.extend(sorted(
            (ws / "worktrees" / "ability-management").glob(f"*/{EVALUATOR_REL}")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise AssertionError(
        "the composition evaluator was not found; this suite cannot exercise "
        "the composition rule without it. Set SERVEROS_COMPOSITION_EVALUATOR "
        f"to a `composition.py`. Looked in:\n  "
        + "\n  ".join(str(c) for c in candidates))


PAYLOAD = evaluator_source().parent


def _commit(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
         "-c", "user.name=t", "commit", "-m", "fixture", "--allow-empty"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _registry(provides, *, extra=()):
    """A manifest whose plugin entry provides exactly `provides`.

    Each provided identity needs an ability entry to resolve to a NAME, because
    `provides` carries identities and `bundle.yaml` carries names — an
    unresolvable identity is reported separately as a registry problem, and
    letting it stand in here would make every other assertion ambiguous.
    """
    abilities = [{"type": "skill", "id": PLUGIN_ID, "name": "hwz-server-os",
                  "path": "plugins/hwz-server-os",
                  "provides": [{"type": "skill", "id": f"ProfessorZhi/{n}"} for n in provides]}]
    abilities += [{"type": "skill", "id": f"ProfessorZhi/{n}", "name": n,
                   "path": f"skills/{n}"} for n in provides]
    abilities += [{"type": "skill", "id": f"ProfessorZhi/{n}", "name": n,
                   "path": f"skills/{n}"} for n in extra]
    return {"schema_version": 3, "abilities": abilities}


@pytest.fixture
def world(tmp_path):
    """Three hermetic repositories, and a way to re-run the checker against them."""

    def build(*, provides=DISTRIBUTED, extra=()):
        root = tmp_path / "world"
        serveros = _init(root / "serveros")
        # `plugin/profiles/` is copied too: the checker compares the registry
        # against the whole composition SET, so a fixture that carried only the
        # core file would be measuring a product that ships less than this one.
        rels = ["plugin/bundle.yaml", "integration/components.yaml"]
        rels += [str(p.relative_to(REPO))
                 for p in sorted((REPO / "plugin" / "profiles").glob("*.yaml"))]
        for rel in rels:
            dst = serveros / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / rel, dst)
        refs = serveros / "skills/serveros/references"
        refs.mkdir(parents=True, exist_ok=True)
        for name in ("serveros-contract.json", "topology-contract.json"):
            source = REPO / "skills/serveros/references" / name
            if source.is_file():
                shutil.copy2(source, refs / name)
        _commit(serveros)

        ability = _init(root / "ability-management")
        (ability / "abilities-manifest.yaml").write_text(
            yaml.safe_dump(_registry(provides, extra=extra), sort_keys=False))
        # The composition rule itself, at the path the pin names. The checker
        # executes it out of the ability revision rather than importing a local
        # copy, so a fixture that omitted it would exercise only the
        # COMPOSITION_EVALUATOR_ABSENT branch and never the comparison.
        payload = ability / "skills/ability-management/payload"
        payload.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PAYLOAD / "composition.py", payload / "composition.py")
        _commit(ability)

        ops = _init(root / "ops")
        for rel in ("workspace/serveros/policies/authority.md", "workspace/environment.yaml"):
            source = OPS_SOURCE / rel
            if source.is_file():
                dst = ops / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dst)
        _commit(ops)

        # Everything a default run would scan is pointed at empty directories.
        # The defaults reach into HOME and into this machine's real ops repo,
        # so a test that let them stand would be measuring the host.
        empty = root / "empty"
        empty.mkdir(exist_ok=True)
        args = SimpleNamespace(
            serveros_repo=serveros, ability_repo=ability, ops_repo=ops,
            canonical_ref="HEAD", ability_ref="HEAD", ops_ref="HEAD",
            data_root=empty, workspace_root=empty,
            runtime_root=[("fixture", empty)], output=None,
        )
        return check.collect(args)

    return build


def drifts(report: dict) -> list[dict]:
    # `collect` labels findings with `id`; the map-projection sub-report uses
    # `code` and is merged in with its own key. Both reach `report["findings"]`.
    return [f for f in report["findings"] if f.get("id") == "BUNDLE_MANIFEST_DRIFT"]


# ---------------------------------------------------------------------------
# the two directions that must still fail
# ---------------------------------------------------------------------------


def test_a_distributed_ability_missing_from_the_registry_is_high_drift(world):
    """The regression this gate exists for.

    `workspace-management` is composed with `source: ability-management` but
    the registry's plugin entry no longer provides it. Ability-management
    expands a plugin install into its `provides` members, so a member it cannot
    see is a member silently left out of its own accounting — the failure is
    in the registry, and it must be reported loudly."""
    report = world(provides=[n for n in DISTRIBUTED if n != "workspace-management"])
    found = drifts(report)
    assert len(found) == 1, [f["observed"] for f in found]
    assert found[0]["severity"] == "HIGH"
    assert "workspace-management" in str(found[0]["observed"])
    assert "manifest-only" not in str(found[0]["observed"]).replace(
        "manifest-only=[]", "")


def test_a_provided_ability_that_no_composition_ships_is_high_drift(world):
    """The other direction: the registry claims an ability that the bundle does
    not compose, so the registry describes a distribution that does not exist.
    A repair that only ever subtracts from one side would let this through."""
    report = world(provides=list(DISTRIBUTED) + ["ghost-ability"])
    found = drifts(report)
    assert len(found) == 1, [f["observed"] for f in found]
    assert found[0]["severity"] == "HIGH"


def test_a_registry_that_agrees_with_the_bundle_reports_no_drift(world):
    assert drifts(world()) == []


# ---------------------------------------------------------------------------
# the direction that was wrong
# ---------------------------------------------------------------------------


def test_a_locally_sourced_member_is_not_drift_whether_registered_or_not(world):
    """The rule the comment always stated and the code did not implement.

    `serveros` is sourced locally and is also in `provides`; `runtime-management`
    is sourced locally and is not. Both are legitimate, and the difference
    between them is a *registry scope* question that belongs to
    ability-management — which the checker already reports separately at
    OBSERVED. Asserting it here as HIGH drift made the gate unsatisfiable: a
    local member was wrong if registered and wrong if not.

    Both halves are asserted in one test on purpose. Testing only the
    unregistered case would pass against a gate that had simply been disabled
    for local members, and the registered case is what proves the two findings
    can coexist.
    """
    unregistered = world(provides=DISTRIBUTED)
    assert drifts(unregistered) == [], "a locally-sourced member with no registry record"

    registered = world(provides=list(DISTRIBUTED) + ["serveros"])
    assert drifts(registered) == [], "a locally-sourced member with a registry record"


def test_the_registry_scope_question_is_still_reported_at_observed(world):
    """Removing the HIGH finding must not remove the observation.

    `serveros` being both local-sourced and registered is a real open question
    for the registry's owner — whether the bootstrap skill is a *reusable*
    ability at all. It is reported at OBSERVED, and it must stay reported."""
    report = world(provides=list(DISTRIBUTED) + ["serveros"])
    observed = [f for f in report["findings"]
                if f.get("id") == "BOOTSTRAP_ABILITY_REGISTERED"]
    assert len(observed) == 1
    assert observed[0]["severity"] == "LOW"
    assert observed[0]["status"] == "OBSERVED"


# ---------------------------------------------------------------------------
# §28 · on-demand members are reachable without being shipped
# ---------------------------------------------------------------------------


def test_a_recommended_ability_the_registry_provides_is_not_drift(world):
    """What makes the containment rule necessary rather than a loosening.

    `information-management` is recommended by `serveros-linux-work` and shipped
    by no composition. Under the old set-equality rule that asymmetry was
    reported as HIGH drift against a product that is behaving exactly as §28
    describes. The registry says what a node can RESOLVE; a composition says
    what an artifact CARRIES. Those are different questions, and the old rule
    compared them as though they were the same one.
    """
    assert drifts(world()) == [], [f["observed"] for f in drifts(world())]


def test_a_recommended_ability_the_registry_does_not_provide_is_medium(world):
    """The other half: containment must not become "anything goes".

    A recommendation is a promise that a node can resolve the ability on demand,
    and the registry is what it resolves against. Dropping the registry entry
    leaves the promise with nothing behind it, so it is reported — at MEDIUM,
    because the product is still coherent, one door is just missing."""
    report = world(provides=[n for n in DISTRIBUTED if n != "information-management"])
    found = [f for f in report["findings"] if f.get("id") == "RECOMMENDATION_UNRESOLVABLE"]
    assert len(found) == 1, [f["id"] for f in report["findings"]]
    assert found[0]["severity"] == "MEDIUM"
    assert "information-management" in str(found[0]["observed"])
    # The registry no longer listing it is exactly the case this finding is for;
    # it must not ALSO be reported as composition drift, or one cause is billed
    # twice and the second bill is the one a reader acts on.
    assert drifts(report) == []
