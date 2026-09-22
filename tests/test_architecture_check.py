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
#
# `automated-testing` joined with A7's `plugin/profiles/testing.yaml`. It is the
# first member here that arrives from a composition a node installs INSTEAD of
# the workbench rather than alongside it, which is why the registry had to be
# told about it: `provides` is a union over the composition set, and the checker
# compares the registry against that whole set. The `world` fixture copies every
# profile for the same reason, so a fixture left behind here does not fail
# silently — it fails as COMPOSITION_MEMBER_UNREGISTERED, which is what
# happened, and this line is the decision that came out of it.
SHIPPED = ("ability-management", "runtime-management", "test-management",
           "workspace-management", "automated-testing")

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

# ---------------------------------------------------------------------------
# §18/§26 · the reachability axis, and the verdict it contributes to
# ---------------------------------------------------------------------------
#
# The axis is the glue between this gate and `mutation_guard.py`, and glue is
# where the two shapes that matter are decided: whether the contracts are read
# from the pin or from the worktree (the pin — like every other declaration
# here), and whether a run that cannot read them says so or shrugs. Both are
# asserted below, including the branch that reports UNAVAILABLE.
#
# The `world` fixture above supplies no contracts, so every one of its runs
# takes that UNAVAILABLE branch. That is correct — the fixture predates them —
# but it means the axis's successful path is exercised nowhere else, which is
# the whole reason for this section.
#
# These fixtures carry hand-written minimal contracts rather than copies of the
# real ones. A copy would declare twenty-three sites in `runtime/*.py` files
# that no fixture has, so every run would be dominated by `STALE_ACTUATOR`
# noise and the assertion under test would be a rounding error in it.

# The forbidden list is not decoration: an empty one is reported as
# MCP_FORBIDDEN_LIST_DRIFT, because a policy that has drifted from the
# vocabulary it is a policy about stops forbidding anything. So the fixture
# declares it exactly as the real contract does.
FORBIDDEN = "[" + ", ".join(guard_helpers := ["docker", "df", "free", "ss", "du", "find"]) + "]"
EMPTY_ACTUATORS = (
    "schema: serveros/actuator.v1\n"
    "scopes: []\n"
    f"mcp_layer_forbidden_commands: {FORBIDDEN}\n"
    "actuators: []\n")
EMPTY_STATE_ROOT = "schema: serveros/state-root.v1\nnamespaces: {}\n"

READ = "import subprocess\nsubprocess.run(['df', '-h'])\n"
DESTROY = "import subprocess\nsubprocess.run(['docker', 'rm', name])\n"


def _declaration(*, kind, verb):
    return ("schema: serveros/actuator.v1\nscopes: []\n"
            f"mcp_layer_forbidden_commands: {FORBIDDEN}\n"
            "actuators:\n  - id: fixture\n"
            f"    kind: {kind}\n    verb: {verb}\n"
            "    sites:\n      - file: runtime/thing.py\n"
            "        symbols: ['<module>']\n")


@pytest.fixture
def reachable(tmp_path):
    """A throwaway repo carrying both contracts, and a way to run the axis on it."""

    repo = _init(tmp_path / "serveros")

    def build(*, code=(), contract=None, contracts=True, worktree_contract=None):
        (repo / "runtime").mkdir(parents=True, exist_ok=True)
        (repo / "fleet").mkdir(parents=True, exist_ok=True)
        for rel, text in code:
            (repo / rel).write_text(text)
        if contracts:
            (repo / "runtime/actuator-contract.yaml").write_text(
                contract or EMPTY_ACTUATORS)
            (repo / "fleet/state-root-contract.yaml").write_text(EMPTY_STATE_ROOT)
        _commit(repo)
        if worktree_contract is not None:
            # Written AFTER the commit, so it exists in the worktree only. The
            # axis must not see it: declarations come from the pin.
            (repo / "runtime/actuator-contract.yaml").write_text(worktree_contract)
        findings: list[dict] = []
        axis = check.reachability_axis(SimpleNamespace(), repo, "HEAD", findings)
        return axis, findings

    return build


def ids(findings) -> list[str]:
    return [f["id"] for f in findings]


def test_a_clean_tree_with_both_contracts_is_checked(reachable):
    """The successful path, and the fields it reports so a run can be audited.

    `declaration_ref` and `worktree_head` are both here because the axis reads
    its declarations from one and its code from the other. A report naming only
    one of them leaves a reader unable to say which revision the answer is
    about."""
    axis, findings = reachable(code=[("runtime/thing.py", "x = 1\n")])
    assert ids(findings) == []
    assert axis["status"] == "CHECKED"
    assert axis["declaration_ref"] == "HEAD"
    assert axis["worktree_head"]
    assert axis["counts"]["undeclared"] == 0


def test_a_declared_read_passes_and_the_scan_is_not_vacuous(reachable):
    """Non-vacuity in both directions. A clean report is only worth reading if
    the scan found something, and an empty contract covering a real call site
    is itself a failure — so the same tree is run twice, with the read declared
    and undeclared, and the hit count is what separates the two."""
    undeclared_axis, undeclared = reachable(code=[("runtime/thing.py", READ)])
    assert ids(undeclared) == ["UNDECLARED_ACTUATOR"]
    assert undeclared_axis["counts"]["product_hits"] == 1

    declared_axis, declared = reachable(
        code=[("runtime/thing.py", READ)],
        contract=_declaration(kind="host_reader", verb="df"))
    assert ids(declared) == []
    assert declared_axis["status"] == "CHECKED"
    assert declared_axis["counts"]["product_hits"] == 1, (
        "the same single call site, now covered — not a scan that found nothing")
    assert declared_axis["counts"]["declared"] == 1


def test_a_destroyer_with_no_declaration_fails_the_axis(reachable):
    """The §26 claim in the only direction that can contradict it."""
    axis, findings = reachable(code=[("runtime/thing.py", DESTROY)])
    assert ids(findings) == ["UNDECLARED_ACTUATOR"]
    assert findings[0]["severity"] == "HIGH"
    assert findings[0]["status"] == "OPEN"
    assert axis["status"] == "FAILED"
    assert axis["counts"]["product_hits"] == 1


def test_declaring_the_destroyer_clears_it(reachable):
    """And the declaration is checked, not merely accepted: it has to name the
    verb that is actually there."""
    axis, findings = reachable(
        code=[("runtime/thing.py", DESTROY)],
        contract=_declaration(kind="mutation", verb="docker rm"))
    assert ids(findings) == [], [f["observed"] for f in findings]
    assert axis["status"] == "CHECKED"
    assert axis["counts"]["declared"] == 1


def test_declaring_the_wrong_verb_does_not_clear_it(reachable):
    """`docker` declared where `docker rm` runs is exactly the stale exemption
    this contract's two-directional check exists to catch. Both halves are
    reported, because neither alone describes the defect: the call site is
    undeclared AND the declaration covers nothing."""
    axis, findings = reachable(
        code=[("runtime/thing.py", DESTROY)],
        contract=_declaration(kind="host_reader", verb="docker"))
    assert ids(findings) == ["UNDECLARED_ACTUATOR", "STALE_ACTUATOR"]
    assert axis["status"] == "FAILED"


def test_contracts_missing_from_the_pin_are_reported_rather_than_passed(reachable):
    """The branch `world` takes. A checker that cannot read its own contract
    has not thereby found the code clean, and UNAVAILABLE is not a synonym for
    conformant — the finding is HIGH, so the gate is red until the pin carries
    the contract."""
    axis, findings = reachable(code=[("runtime/thing.py", "x = 1\n")], contracts=False)
    assert ids(findings) == ["REACHABILITY_CONTRACT_ABSENT"]
    assert findings[0]["severity"] == "HIGH"
    assert findings[0]["status"] == "OPEN"
    assert axis["status"] == "UNAVAILABLE"


def test_the_axis_reads_the_declaration_from_the_pin_not_the_worktree(reachable):
    """The rule this checker follows everywhere, applied to its own contract.

    The worktree here carries a declaration covering the destroyer; it was
    written after the commit, so the pin does not have it. If the axis read the
    worktree the destroyer would come back covered. It must come back
    undeclared — otherwise every run in progress is judged against a contract
    nobody has reviewed yet."""
    axis, findings = reachable(
        code=[("runtime/thing.py", DESTROY)],
        worktree_contract=_declaration(kind="mutation", verb="docker rm"))
    assert ids(findings) == ["UNDECLARED_ACTUATOR"], [f["observed"] for f in findings]
    assert axis["counts"]["declared"] == 0
    assert axis["worktree_dirty"], (
        "and the report says the scan is of a dirty worktree, so a reader can "
        "see the asymmetry rather than infer it")


def test_the_guard_missing_is_reported_rather_than_silently_skipped(reachable, monkeypatch):
    """The axis resolves `mutation_guard.py` relative to its own `__file__`.
    If that path is wrong the axis must say the check is unavailable instead of
    returning an empty, green-looking report."""
    axis, findings = reachable(code=[("runtime/thing.py", "x = 1\n")])
    assert axis["status"] == "CHECKED"

    monkeypatch.setattr(check, "__file__", "/nonexistent/dir/architecture_check.py")
    findings: list[dict] = []
    axis = check.reachability_axis(SimpleNamespace(), Path("/nonexistent"), "HEAD", findings)
    assert ids(findings) == ["REACHABILITY_CHECK_UNAVAILABLE"]
    assert findings[0]["severity"] == "HIGH"
    assert axis["status"] == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# §Q6 · where the checker looks when nobody says
# ---------------------------------------------------------------------------


def test_no_machine_path_is_compiled_into_the_checker():
    """The defaults used to be one machine's layout: `/opt/applications/...`.

    Not a style rule. This checker's whole job is to find claims that do not
    describe the machine they were read on, and a rooted default made it do the
    opposite — on a host where that path does not exist it reported on a
    directory nobody had asked about, which reads exactly like a finding about
    this one. The literal stays in the history comment above the resolver and
    is allowed nowhere else.
    """
    literal = "/opt/applications/data/home-hwz"
    source = (SCRIPTS / "architecture_check.py").read_text(encoding="utf-8")
    for number, line in enumerate(source.splitlines(), 1):
        if literal in line:
            assert line.lstrip().startswith("#"), (
                f"line {number} names {literal} outside a comment: {line!r}")


def test_the_environment_names_the_roots(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv(check.HOST_PROFILE_ENV, raising=False)
    assert check.default_root("workspace_root") == tmp_path / "ws"
    assert check.default_root("data_root") == tmp_path / "data"


def test_this_hosts_profile_answers_when_the_environment_does_not(monkeypatch, tmp_path):
    """The profile is the same document `serverosd` reads for the same reason.

    Which host this is, is a fact about a deployment, so the deployment states
    it in the one place that already exists for exactly this. Nothing in the
    checker knows a machine name, a user name, or a layout.
    """
    for env_name, _, _ in check.ROOT_SOURCES.values():
        monkeypatch.delenv(env_name, raising=False)
    profile = tmp_path / "host-profile.yaml"
    profile.write_text(
        "schema: serveros/host-profile.v1\n"
        "node:\n"
        "  roots:\n"
        f"    identity_home: {tmp_path}/home/x\n"
        f"    workspace_root: {tmp_path}/home/x/workspace\n"
        f"    workspace_src: {tmp_path}/home/x/workspace/src\n",
        encoding="utf-8")
    monkeypatch.setenv(check.HOST_PROFILE_ENV, str(profile))

    assert check.default_root("data_root") == tmp_path / "home/x"
    assert check.default_root("workspace_root") == tmp_path / "home/x/workspace"
    assert check.default_root("serveros_repo") == tmp_path / "home/x/workspace/src/serveros"
    assert check.default_root("ability_repo") == (
        tmp_path / "home/x/workspace/src/ability-management")
    assert check.default_root("ops_repo") == tmp_path / "home/x/workspace/ops"


def test_a_host_that_says_nothing_gets_a_refusal_not_a_guess(monkeypatch, tmp_path, capsys):
    """An unanswerable root is refused, and the refusal is not exit 0.

    This program returns 0 for every conformance verdict it reaches, so "I
    could not answer" has to be distinguishable from "conformant" — otherwise
    the one machine that has not been configured reports the cleanest result.
    """
    for env_name, _, _ in check.ROOT_SOURCES.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(check.HOST_PROFILE_ENV, str(tmp_path / "absent.yaml"))

    for argument in check.ROOT_SOURCES:
        assert check.default_root(argument) is None

    assert check.main(["--json"]) == 2
    err = capsys.readouterr().err
    assert "--data-root" in err
    assert check.HOST_PROFILE_ENV in err
