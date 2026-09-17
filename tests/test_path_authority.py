#!/usr/bin/env python3
"""§3 BARE PROCESS + §4 FAIL-CLOSED — the two acceptance tests for path
authority.

§3 asks a question about INVOCATION CONTEXT: does a process that was started
without a login shell get the same roots as one that was not? It is not asking
whether the value is right — it is asking whether the answer depends on who
started the process. So the test runs one probe through every context §3 names
and requires one answer, then cross-checks that answer against an authority
reader that is NOT `paths.py` (`fleet/transport.py`), because a resolver that
agrees only with itself has proved nothing.

§4 asks the opposite question: with nothing supplied, does it refuse? The
failure this must not have is a plausible path, from anywhere — Beijing's, a
guess at `$HOME`, or a template that formats into `/workspace`. Each of those is
checked by name.

Nothing here is compared to a remembered value. The expected roots are read from
the Profile binding, so this file keeps working on a node it has never seen.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
FLEET = REPO / "fleet"

# The one node's paths that must never appear in an answer again. They are here
# as STRINGS TO REFUSE, which is what makes this the one place they belong.
BEIJING_LITERALS = ("/home/hwz", "/opt/applications/data/home-hwz")


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths_mod = _load("serveros_runtime_paths", "paths.py")


# ---------------------------------------------------------------------------
# §3 — one answer, whatever started the process
# ---------------------------------------------------------------------------

# A probe the SHELL runs, so that the shell's own environment is what the
# resolver sees. It prints the roots as JSON; the runner below parses it.
PROBE = (
    "import importlib.util, json;"
    f"s=importlib.util.spec_from_file_location('p',{str(RUNTIME / 'paths.py')!r});"
    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
    "p=m.Paths();"
    "print(json.dumps({'roots':{r:str(p.root(r)) for r in "
    "('workspace','data','state','artifacts','home','tmp','caches')},"
    "'bound':p.bound,'unconfigured':list(p.unconfigured),"
    "'provenance':p.provenance}))"
)

ROOT_OVERRIDES = ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT",
                  "TOOLCHAINS_ROOT", "SERVEROS_STATE_DIR")


def _run(argv: list[str], env: dict | None = None) -> dict:
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env)
    assert proc.returncode == 0, f"{argv} rc={proc.returncode}\n{proc.stderr}"
    return json.loads(proc.stdout)


def _bare_env() -> dict:
    """This process's environment with every root override removed.

    Removing them is the point: `bash -c` from cron, from a spawned MCP, or
    from `subprocess.run` gets no `~/.bashrc` and no systemd drop-in, so the
    only tiers left are the Profile binding and the OS observation.
    """
    env = {k: v for k, v in os.environ.items() if k not in ROOT_OVERRIDES}
    return env


def _contexts() -> dict:
    env = _bare_env()
    minimal = {"HOME": os.environ.get("HOME", ""), "PATH": "/usr/bin:/bin"}
    return {
        "no shell at all (direct python)":
            _run([sys.executable, "-c", PROBE], env=env),
        "bash -c (non-login)":
            _run(["bash", "-c", f"{sys.executable} -c {json.dumps(PROBE)}"], env=env),
        "env -i (nothing but HOME and PATH)":
            _run([sys.executable, "-c", PROBE], env=minimal),
        "env -i under bash -c":
            _run(["bash", "-c", f"{sys.executable} -c {json.dumps(PROBE)}"],
                 env=minimal),
    }


def test_every_bare_context_resolves_to_one_answer():
    """§3's actual claim: the answer is a property of the node, not the shell.

    A login-shell case is included so the comparison is to something known
    good. `bash -lc` sources the profile that exports the roots, so it is the
    context that always worked; the others are the ones that used to land on
    Beijing.
    """
    env = _bare_env()
    login = _run(["bash", "-lc", f"{sys.executable} -c {json.dumps(PROBE)}"], env=env)
    contexts = _contexts()
    for label, got in contexts.items():
        assert got["roots"] == login["roots"], (
            f"{label} resolved different roots from a login shell\n"
            f"  login: {login['roots']}\n  {label}: {got['roots']}")


def test_no_bare_context_resolves_into_a_beijing_path():
    """The defect, named. Not "the roots differ" — "they are that node's"."""
    for label, got in _contexts().items():
        for role, value in got["roots"].items():
            for literal in BEIJING_LITERALS:
                assert not value.startswith(literal), (
                    f"{label} resolved {role} into another node's path: {value}")


def test_no_bare_context_reports_itself_unbound():
    """`bound` is what a placement verb checks before it names a directory."""
    for label, got in _contexts().items():
        assert got["bound"] is True, (
            f"{label} is unbound: {got['unconfigured']}")


def test_the_answer_agrees_with_the_fleet_authority_not_just_with_itself():
    """Cross-check against the OTHER reader of the same three files.

    `paths.py` reads `node.yaml` -> `registry.yaml` -> `profiles.yaml` in
    process. `fleet/transport.py` reads them through `Profiles.resolve`, which
    is the surface a caller asking "where does role X live on device Y" uses.
    Two readers of one authority are a place they can drift apart, so the
    agreement is asserted rather than assumed.
    """
    if not (FLEET / "profiles.yaml").is_file():
        pytest.skip("no fleet authority to compare against")
    transport = _load("serveros_fleet_transport", "../fleet/transport.py")

    node_file = paths_mod._node_file_for(FLEET)
    if not node_file.is_file():
        pytest.skip(f"this host claims no device ({node_file} is absent)")
    device_ref = (paths_mod._read_yaml(node_file) or {}).get("device_ref")
    if not device_ref:
        pytest.skip("the node file names no device")

    registry = transport.DeviceRegistry()
    device = registry.resolve(device_ref)
    profiles = transport.Profiles()
    resolved = paths_mod.Paths()

    # The roles `paths.py` builds roots from, checked one by one against the
    # authority. `workspace_root` is absent on purpose: it has no role of its
    # own, and is derived from `data_root`.
    for role, root_name in (("data_root", "data"),
                            ("home", "home"),
                            ("artifacts", "artifacts"),
                            ("toolchains", "toolchains")):
        binding = profiles.resolve(device, role)
        assert str(resolved.root(root_name)) == binding.path, (
            f"{root_name} disagrees with {role} in the authority: "
            f"{resolved.root(root_name)} vs {binding.path}")


# ---------------------------------------------------------------------------
# §4 — nothing supplied, nothing invented
# ---------------------------------------------------------------------------

@pytest.fixture
def supplied_nothing(monkeypatch):
    """No environment tier, and no authority to fall back on."""
    for name in ROOT_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return paths_mod.Paths(fleet_dir=None)


def test_a_node_with_no_authority_is_unbound(supplied_nothing):
    assert supplied_nothing.bound is False
    assert supplied_nothing.unconfigured, "nothing was reported as unanswered"


def test_an_unbound_root_is_a_template_and_not_a_path(supplied_nothing):
    """The failure mode this replaces: a plausible path.

    `""` would format into `/workspace`, which reads as an absolute path, and a
    caller that never asked whether the node was identified would write there.
    A root that reads as `{DATA_ROOT}/state/serveros` cannot be mistaken for an
    answer, which is the whole reason the value is shaped that way.
    """
    for name in supplied_nothing.unconfigured:
        value = str(supplied_nothing.root(name))
        assert "{" in value and "}" in value, (
            f"unbound root {name} resolved to something path-shaped: {value}")


def test_no_unbound_root_falls_back_to_a_beijing_path(supplied_nothing):
    for name in supplied_nothing.provenance:
        value = str(supplied_nothing.root(name))
        for literal in BEIJING_LITERALS:
            assert not value.startswith(literal), f"{name} = {value}"


def test_home_is_observed_but_never_derived_into_the_data_root(monkeypatch):
    """§0.A's specific error: `$HOME` is not a data root.

    `HOME` is in the environment, so it may answer the `home` role. It must not
    answer `data_root` — on the node this contract came from those two are
    different directories, and on this one they coincide, which is exactly why
    the coincidence cannot be allowed to decide.

    The observation is driven through the ENVIRONMENT, because that is the
    claim. The first version of this test injected `{"HOME": ...}` instead,
    which reads as though it were doing the same thing and was not: injection
    is keyed by seed name (`home`), so that mapping was never consulted and the
    assertion held only because the real `HOME` of the process running this
    suite leaked in. A test that agrees with its own environment is not
    measuring anything.
    """
    monkeypatch.setenv("HOME", "/tmp/pretend-home")
    monkeypatch.delenv("DATA_ROOT", raising=False)

    resolved, provenance = paths_mod.expand_with_provenance(None, fleet_dir=None)

    # `resolved` and `provenance` are keyed by NAME, while `roots` is keyed by
    # SEED: `data_root` is the seed that answers for the name `data`.
    assert provenance["home"] == paths_mod.ENVIRONMENT, "HOME did not answer for `home`"
    assert resolved["home"] == "/tmp/pretend-home"
    assert provenance["data"] == paths_mod.UNBOUND, (
        "HOME answered for the data root, which is §0.A's error")
    for name, value in resolved.items():
        if name == "home":
            continue
        assert not value.startswith("/tmp/pretend-home"), (
            f"{name} was derived from $HOME: {value}")


def test_an_injected_world_is_closed_to_this_machine(monkeypatch):
    """An injected mapping is the caller stating the world, and nothing else
    fills in the rest.

    This is the property `Paths` documents about its injectable form —
    "Injectable so a test can exercise resolution against a tmp_path without
    the machine's real roots, and so a wrong root can never be reached by
    falling back to whatever the environment happened to say" — and it is the
    one that was missing.

    It is a REGRESSION test for a defect §11-E found rather than a rule
    invented here: `test_path_conformance.py`'s positive control failed with
    `DECLARED_ENTRY_ABSENT: 'skills' exists under ARTIFACTS_ROOT`, and
    `/data/home/vince/artifacts` really does hold `work-items` and not
    `skills`. The conformance check was reading this node while being handed a
    throwaway tree, because the fixture injected three seeds and the fourth
    resolved through this machine's Profile.
    """
    monkeypatch.setenv("HOME", "/tmp/leaked-home")
    monkeypatch.setenv("DATA_ROOT", "/tmp/leaked-data")

    resolved, provenance = paths_mod.expand_with_provenance(
        {"data_root": "/tmp/injected-data"}, fleet_dir=None)

    assert resolved["data"] == "/tmp/injected-data"
    assert provenance["data"] == paths_mod.INJECTED
    for name, value in resolved.items():
        if name == "data":
            continue
        assert "/tmp/leaked" not in value, (
            f"{name} came from this machine's environment: {value}")

    # The roots with no authority of their own are DERIVED from the seed the
    # caller supplied -- inside the caller's tree, which is the half that was
    # lost and is why the conformance check read this machine.
    assert resolved["artifacts"] == "/tmp/injected-data/artifacts"
    assert provenance["artifacts"] == paths_mod.DERIVED
    assert provenance["toolchains"] == paths_mod.DERIVED

    # What is left over is the honest answer, and the caller can see it.
    assert provenance["home"] == paths_mod.UNBOUND
    assert not paths_mod.Paths({"data_root": "/tmp/injected-data"},
                               fleet_dir=None).bound


def test_an_authority_naming_an_unenrolled_device_is_unbound(tmp_path):
    """A node file that claims a device nobody enrolled gets no binding.

    Not an error and not a guess: the claim does not resolve, so the roots do
    not resolve. This is the shape a half-migrated node has.
    """
    fleet = tmp_path / "fleet"
    fleet.mkdir()
    (fleet / "node-root-contract.yaml").write_text("root:\n  default: '~/.serveros'\n")
    (fleet / "registry.yaml").write_text("devices: []\n")
    (fleet / "profiles.yaml").write_text("path_roles: {}\nprofiles: {}\n")
    node = tmp_path / "node.yaml"
    node.write_text("schema: serveros/node.v1\ndevice_ref: device:not-enrolled\n")

    resolved = paths_mod.Paths(fleet_dir=fleet, node_file=node)
    assert resolved.bound is False
    assert "data_root" in resolved.unconfigured or "data" in resolved.unconfigured


def test_the_resolved_module_no_longer_carries_the_literal():
    """The check that outlives this round.

    Every other test here would pass if a fallback were reintroduced that
    happened to agree with this node's Profile. This one cannot: the module's
    STRING CONSTANTS are read, and the literal must not be among them.

    Constants, not raw text, because the file should be allowed to say what it
    removed. `paths.py` explains in a comment why `$HOME` is not a derivation of
    `DATA_ROOT`, and that sentence has to name the two directories to mean
    anything — a text search would forbid the explanation and leave the defect
    undocumented. A comment cannot be assigned to a variable, so reading the
    AST separates "mentions the path" from "can return the path", which is the
    distinction that matters.
    """
    tree = ast.parse((RUNTIME / "paths.py").read_text(encoding="utf-8"),
                     filename="runtime/paths.py")
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert strings, "no string constants were read; the check proved nothing"
    for literal in BEIJING_LITERALS:
        for value in strings:
            assert literal not in value, (
                f"`{literal}` is a string constant in runtime/paths.py: {value!r}. "
                f"A compiled-in fallback to another node's path is the defect "
                f"this round removed.")


def test_a_profile_binding_beats_the_derivation_it_resembles(
        tmp_path, monkeypatch):
    """§2.1's precedence, on the pair that was failing.

    `artifacts` and `toolchains` are declared roles AND have a default suffix,
    so both the Profile and the derivation can answer for them. The Profile is
    the authored answer and must win; the derivation is what a node gets when
    its Profile declined.

    This is the assertion that was missing. Every earlier test compared a
    derivation against the derivation -- on this node the Profile binds
    `/data/home/vince/artifacts`, `data_root` is `/data/home/vince`, and the
    derivation produces the same string -- so a resolver that ignored the
    Profile entirely passed all of them.

    The throwaway authority below binds both roles somewhere that is NOT
    `{data_root}/<suffix>`, which is the only way to tell "read it" from
    "computed it".
    """
    fleet = tmp_path / "fleet"
    fleet.mkdir()
    (fleet / "registry.yaml").write_text(
        "devices:\n"
        "  - device_ref: device:probe-node\n"
        "    environment_profile: probe-shape\n"
        "    enrollment: enrolled\n", encoding="utf-8")
    (fleet / "profiles.yaml").write_text(
        "profiles:\n"
        "  probe-shape:\n"
        "    device_ref: device:probe-node\n"
        "    paths:\n"
        "      data_root: {state: BOUND, path: /srv/data}\n"
        "      artifacts: {state: BOUND, path: /srv/elsewhere/artifact-store}\n"
        "      toolchains: {state: BOUND, path: /srv/elsewhere/toolchain-store}\n",
        encoding="utf-8")
    node_file = tmp_path / "node.yaml"
    node_file.write_text("device_ref: device:probe-node\n", encoding="utf-8")

    # The process running this suite must not be able to answer for a node the
    # caller named explicitly.
    for variable in ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT",
                     "TOOLCHAINS_ROOT"):
        monkeypatch.delenv(variable, raising=False)

    resolved, provenance = paths_mod.expand_with_provenance(
        None, fleet_dir=fleet, node_file=node_file)

    assert resolved["data"] == "/srv/data"
    assert provenance["data"] == paths_mod.PROFILE

    # The binding, not the derivation.
    assert resolved["artifacts"] == "/srv/elsewhere/artifact-store", (
        "the profile binds `artifacts` and the derivation overwrote it")
    assert provenance["artifacts"] == paths_mod.PROFILE
    assert resolved["toolchains"] == "/srv/elsewhere/toolchain-store", (
        "the profile binds `toolchains` and the derivation overwrote it")
    assert provenance["toolchains"] == paths_mod.PROFILE

    # And the seed with no role of its own is still derived, so the fix did not
    # remove the derivation to stop it overwriting.
    assert resolved["workspace"] == "/srv/data/workspace"
    assert provenance["workspace"] == paths_mod.DERIVED


def test_a_profile_that_declines_a_role_still_falls_back_to_the_derivation(
        tmp_path, monkeypatch):
    """The other half of the same rule: absence of a binding is not a refusal to
    resolve, it is a node whose Profile did not name that role."""
    fleet = tmp_path / "fleet"
    fleet.mkdir()
    (fleet / "registry.yaml").write_text(
        "devices:\n"
        "  - device_ref: device:probe-node\n"
        "    environment_profile: probe-shape\n"
        "    enrollment: enrolled\n", encoding="utf-8")
    (fleet / "profiles.yaml").write_text(
        "profiles:\n"
        "  probe-shape:\n"
        "    device_ref: device:probe-node\n"
        "    paths:\n"
        "      data_root: {state: BOUND, path: /srv/data}\n"
        "      artifacts: {state: UNBOUND}\n",
        encoding="utf-8")
    node_file = tmp_path / "node.yaml"
    node_file.write_text("device_ref: device:probe-node\n", encoding="utf-8")

    for variable in ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT",
                     "TOOLCHAINS_ROOT"):
        monkeypatch.delenv(variable, raising=False)

    resolved, provenance = paths_mod.expand_with_provenance(
        None, fleet_dir=fleet, node_file=node_file)

    # UNBOUND in the Profile carries no path, so it is not a binding and the
    # derivation answers -- which is the state the Tokyo artifacts role was in
    # before this round gave `data_root` a name to derive from.
    assert resolved["artifacts"] == "/srv/data/artifacts"
    assert provenance["artifacts"] == paths_mod.DERIVED
    assert provenance["toolchains"] == paths_mod.DERIVED
