"""The local control root, the frozen identities, and the read-only interfaces.

Hermetic throughout. Every test that touches `~/.serveros` supplies a temporary
control root, and every test that touches a device supplies a registry — because
a suite that reads the live machine passes on the machine it was written on and
reports on nothing afterwards.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "fleet"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", FLEET / "transport.py")
node = _load("serveros_fleet_node", FLEET / "node.py")
interfaces = _load("serveros_fleet_interfaces", FLEET / "interfaces.py")

assert node.transport is transport, "node.py must reuse the loaded transport"
assert interfaces.transport is transport, "interfaces.py must reuse the loaded transport"


def contract(name: str) -> dict:
    return yaml.safe_load((FLEET / name).read_text(encoding="utf-8"))


REGISTRY = contract("registry.yaml")
PROFILES = contract("profiles.yaml")


def _registry() -> transport.DeviceRegistry:
    return transport.DeviceRegistry(REGISTRY)


def _profiles() -> transport.Profiles:
    return transport.Profiles(PROFILES)


def _root(tmp_path: Path, device_ref: str | None = "device:server-beijing") -> Path:
    """A temporary control root, optionally bound."""
    root = tmp_path / "serveros"
    root.mkdir(parents=True, exist_ok=True)
    if device_ref is not None:
        (root / "node.yaml").write_text(
            f"schema: serveros/node.v1\ndevice_ref: {device_ref}\n", encoding="utf-8"
        )
    return root


def _api(tmp_path: Path, **kwargs) -> "interfaces.Interfaces":
    kwargs.setdefault("registry", _registry())
    kwargs.setdefault("profiles", _profiles())
    # Never the live control root: a test that reads the real one asserts
    # something about whichever machine happens to run it.
    kwargs.setdefault("control_root", tmp_path / "absent-control-root")
    return interfaces.Interfaces(**kwargs)


# ---------------------------------------------------------------------------
# Phase A — the control root binds, and never declares
# ---------------------------------------------------------------------------


def test_an_unbound_host_is_a_state_not_an_error(tmp_path):
    """Absence is reported, not thrown, and never as an empty fleet."""
    root = _root(tmp_path, device_ref=None)
    with pytest.raises(node.NodeUnbound):
        node.local_node(root, _registry())

    state = node.node_state(root)
    assert state["binding"]["state"] == "NODE_UNBOUND"
    assert state["binding"]["device_ref"] is None
    assert state["controller"]["state"] == "REMOTE_CONTROLLER_NOT_EXPOSED"


def test_the_binding_resolves_through_the_registry_rather_than_declaring(tmp_path):
    """The file says which row to read. The registry says what the row means."""
    root = _root(tmp_path)
    local = node.local_node(root, _registry())

    assert local.device_ref.ref == "device:server-beijing"
    # Attributes come from the registry, and came from it whole.
    assert local.device.display_name == "Beijing-Server"
    assert local.device.environment_profile == "linux-server"
    assert local.device.platform == "linux"

    shown = local.show()
    assert shown["identity_source"] == "fleet/registry.yaml"


def test_a_binding_that_declares_an_attribute_is_rejected(tmp_path):
    """A silently ignored attribute looks exactly like one that took effect."""
    root = tmp_path / "serveros"
    root.mkdir()
    (root / "node.yaml").write_text(
        "schema: serveros/node.v1\n"
        "device_ref: device:server-beijing\n"
        "display_name: Something-Else\n"
        "roles: [personal-workstation]\n",
        encoding="utf-8",
    )
    with pytest.raises(node.NodeBindingInvalid) as exc:
        node.read_node_binding(root)
    assert "display_name" in str(exc.value) and "roles" in str(exc.value)


def test_a_binding_naming_an_unenrolled_device_does_not_enrol_it(tmp_path):
    """A file in a home directory is not an enrollment."""
    root = _root(tmp_path, device_ref="device:some-laptop-i-just-made-up")
    with pytest.raises(node.NodeBindingInvalid) as exc:
        node.local_node(root, _registry())
    assert "cannot enrol" in str(exc.value)
    # And the fleet is unchanged by the attempt. Five since the Dell LLM Lab
    # host was enrolled; this asserts the count did not move DURING the
    # attempt, so the number is only meaningful relative to the registry it is
    # read from.
    assert len(_registry().all()) == 5


def test_the_control_root_may_not_carry_a_credential(tmp_path):
    root = tmp_path / "serveros"
    root.mkdir()
    (root / "node.yaml").write_text(
        "schema: serveros/node.v1\n"
        "device_ref: device:server-beijing\n"
        "note: {access_token: 'x'}\n",
        encoding="utf-8",
    )
    with pytest.raises(node.NodeBindingInvalid) as exc:
        node.read_node_binding(root)
    assert "access_token" in str(exc.value)


def test_the_declaration_schema_has_nowhere_to_put_a_controller_token():
    """Not an oversight: the authorized client holds its own credential."""
    decl = contract("controller-contract.yaml")["declaration"]
    assert "token" not in " ".join(decl["required_fields"]).lower()
    # The two fields the schema demands are the two a declaration needs, and
    # neither can hold a credential — `token` is in forbidden_fields, which
    # `validate_controller_declaration` enforces by walking the whole document.
    assert set(decl["required_fields"]) == {"kind", "surface"}
    assert "token" in decl["forbidden_fields"]
    # The absence of a controller here is named, not left blank.
    assert "REMOTE_CONTROLLER_NOT_EXPOSED" in decl["absent_means"]
    # And the contract says what that absence is not.
    assert "never as a device that does not exist" in decl["absent_means"]


def test_transport_names_live_only_in_the_local_root():
    """`observed_name` never reaches a committed artifact."""
    root_contract = contract("node-root-contract.yaml")
    assert root_contract["files"]["targets.yaml"]["forbidden_fields_source"].endswith(
        "forbidden_binding_fields"
    )
    for name in ("FJ-NJ-CL-250028", "fj-cvm-bj-staging-public-other3-05"):
        blob = "\n".join(
            (FLEET / f).read_text(encoding="utf-8")
            for f in (
                "registry.yaml", "profiles.yaml", "transport-contract.yaml",
                "controller-contract.yaml", "node-interface-contract.yaml",
                "node-root-contract.yaml",
            )
        )
        assert name not in blob, f"{name} leaked into a committed contract"


def test_a_bound_control_root_reports_its_bindings_and_their_absence(tmp_path):
    root = _root(tmp_path)
    (root / "targets.yaml").write_text(
        "schema: serveros/transport-bindings.v1\n"
        "bindings:\n"
        "  device:work-fj:\n"
        "    kind: remote-desktop-commander\n"
        "    observed_name: SOME-RELAY-NAME\n",
        encoding="utf-8",
    )
    state = node.node_state(root)
    assert state["binding"]["state"] == "BOUND"
    assert state["transport_bindings"] == {"state": "BOUND", "count": 1}
    assert state["large_state_root"] == "DATA_ROOT/state/serveros"

    (root / "targets.yaml").unlink()
    assert node.node_state(root)["transport_bindings"]["state"] == "NO_BINDINGS"


def test_a_binding_carrying_a_credential_is_refused(tmp_path):
    root = _root(tmp_path)
    (root / "targets.yaml").write_text(
        "bindings:\n  device:work-fj:\n    kind: remote-desktop-commander\n"
        "    token: 'x'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        node.load_local_bindings(root)


# ---------------------------------------------------------------------------
# Phase C + D — the frozen identities
# ---------------------------------------------------------------------------


def test_beijing_is_frozen_as_the_fleet_controller_primary():
    dev = _registry().resolve("device:server-beijing")
    assert dev.device_ref.ref == "device:server-beijing"
    assert dev.environment_profile == "linux-server"
    assert "fleet-controller-primary" in dev.roles
    # The old role restated `platform`; the new one carries information.
    assert "server" not in dev.roles


def test_fj_is_frozen_as_a_corporate_windows_workstation():
    dev = _registry().resolve("device:work-fj")
    assert dev.device_ref.ref == "device:work-fj"
    assert dev.display_name == "FJPCOS"
    assert dev.environment_profile == "windows-corporate"
    assert "corporate-workstation" in dev.roles
    assert dev.platform == "windows"


def test_fj_has_exactly_the_three_known_volumes_and_invents_none():
    """Two Windows laptops that look alike are still two machines."""
    vols = PROFILES["profiles"]["windows-corporate"]["volumes"]
    assert {k for k, v in vols.items() if v["state"] == "BOUND"} == {"C:", "D:", "Z:"}

    # E: and F: are NOT_ASSERTED — nobody looked. Dell binds both; that is a
    # fact about Dell, and it stays on Dell's profile.
    for letter in ("E:", "F:"):
        assert vols[letter]["state"] == "NOT_ASSERTED"
        assert vols[letter]["state"] != "ABSENT", (
            "NOT_ASSERTED means nobody looked; ABSENT means someone did"
        )
        assert vols[letter]["provenance"] == "UNRECORDED"

    dell = PROFILES["profiles"]["windows-personal"]["paths"]
    dell_paths = {v.get("path") for v in dell.values()}
    assert any(str(p).startswith("F:") for p in dell_paths if p)
    assert any(str(p).startswith("E:") for p in dell_paths if p)


def test_fj_and_dell_remain_distinguishable_on_every_axis():
    registry = _registry()
    fj = registry.resolve("device:work-fj")
    dell = registry.resolve("device:personal-dell")

    assert fj.device_ref != dell.device_ref
    assert fj.display_name != dell.display_name
    assert fj.environment_profile != dell.environment_profile
    assert fj.ownership != dell.ownership
    assert set(fj.roles).isdisjoint(dell.roles)
    assert {d.device_ref.ref for d in registry.by_platform("windows")} == {
        "device:work-fj", "device:personal-dell"}


def test_volume_states_are_declared_where_they_are_used():
    """A value with no vocabulary is a value nobody can check."""
    declared = set(PROFILES["volume_states"])
    for profile in PROFILES["profiles"].values():
        for letter, vol in (profile.get("volumes") or {}).items():
            assert vol["state"] in declared, f"{letter}={vol['state']} is undeclared"


# ---------------------------------------------------------------------------
# Phase F — the read-only interfaces
# ---------------------------------------------------------------------------


def test_every_declared_interface_is_implemented_and_vice_versa():
    """Both directions, so neither list can drift from the other.

    A name in `not_implemented` that is not a handler, or a handler left listed
    as unimplemented, is a test failure rather than a surprise at runtime.
    """
    api = _api(Path("/tmp"))
    declared = set(interfaces.declared_interfaces())
    unimplemented = set(interfaces.not_implemented())

    assert not (declared & unimplemented), "a name may not be both"
    for name in declared:
        assert hasattr(api, name.replace(".", "_")), f"{name} is declared but absent"
    for name in unimplemented:
        with pytest.raises(interfaces.InterfaceNotImplemented):
            api.call(name)

    # The nine the brief asks for are present, under these exact names.
    assert declared == {
        "device.show", "environment.inspect", "context.resolve", "path.resolve",
        "ability.list", "ability.status",
        "fleet.devices", "fleet.device.inspect", "fleet.capability.find",
    }


def test_an_undeclared_call_fails_instead_of_improvising():
    api = _api(Path("/tmp"))
    with pytest.raises(interfaces.UnknownInterface):
        api.call("node.do-something-helpful")


def test_every_interface_is_answerable_with_no_controller(tmp_path):
    """Readiness of the local node must not depend on another machine.

    This is the property that keeps "the relay is down" and "this host is not
    prepared" from being debugged as one problem.
    """
    root = _root(tmp_path)
    api = _api(tmp_path, control_root=root)
    assert node.local_controller_declaration(root) is None

    for name in interfaces.declared_interfaces():
        kwargs = {
            "device.show": {}, "environment.inspect": {}, "context.resolve": {"role": "workspace.src"},
            "path.resolve": {"role": "workspace.src", "device_ref": "device:server-beijing"},
            "ability.list": {"device_ref": "device:work-fj"},
            "ability.status": {"device_ref": "device:work-fj", "ability": "feishu-report"},
            "fleet.devices": {}, "fleet.device.inspect": {"device_ref": "device:work-fj"},
            "fleet.capability.find": {"level": "inspect"},
        }[name]
        result = api.call(name, **kwargs)
        assert isinstance(result, dict), name


def test_device_show_returns_no_transport_name(tmp_path):
    root = _root(tmp_path)
    shown = _api(tmp_path, control_root=root).call("device.show")
    assert shown["device_ref"] == "device:server-beijing"
    blob = repr(shown).lower()
    for forbidden in ("hostname", "uuid", "fj-cvm-bj", "fj-nj-cl", "computer_name"):
        assert forbidden not in blob, forbidden


def test_context_resolve_carries_the_gap_rather_than_hiding_it(tmp_path):
    """A role with no binding is part of the capsule, not an exception."""
    api = _api(tmp_path, control_root=_root(tmp_path))
    capsule = api.call("context.resolve", role="workspace.src",
                       device_ref="device:work-fj")
    assert capsule["resolved"]["state"] == "BOUND"
    assert capsule["resolved"]["path"] == "D:\\projects"
    assert capsule["availability"] == "CONTROLLER_NOT_EXPOSED_AND_PRESENCE_UNKNOWN"

    unbound = api.call("context.resolve", role="state", device_ref="device:work-fj")
    assert unbound["resolved"]["state"] == "PathRoleUnbound"
    assert unbound["paths"]["state"]["state"] == "UNBOUND"


def test_unbound_and_not_applicable_stay_distinguishable_through_the_interface(tmp_path):
    """The caller must be able to tell 'wait' from 'stop asking'."""
    api = _api(tmp_path, control_root=_root(tmp_path))
    with pytest.raises(transport.PathRoleUnbound) as unbound:
        api.call("path.resolve", role="downloads", device_ref="device:server-beijing")
    assert not isinstance(unbound.value, transport.PathRoleNotApplicable)

    with pytest.raises(transport.PathRoleNotApplicable):
        api.call("path.resolve", role="software.gui", device_ref="device:server-beijing")


def test_ability_list_reports_unavailable_rather_than_empty(tmp_path):
    """An empty mapping says 'this device has no abilities'. Nobody checked."""
    api = _api(tmp_path, control_root=_root(tmp_path), observation_roots=())
    listing = api.call("ability.list", device_ref="device:work-fj")
    assert listing["state"] == "OBSERVATION_UNAVAILABLE"
    assert listing["groups"] is None

    status = api.call("ability.status", device_ref="device:work-fj", ability="feishu-report")
    assert status["state"] == "UNKNOWN_NOT_OBSERVED"


def test_ability_list_reads_a_real_observation_record(tmp_path):
    obs = tmp_path / "observations"
    (obs / "device-work-fj").mkdir(parents=True)
    (obs / "device-work-fj" / "a.observed.yaml").write_text(
        "device_ref: device:work-fj\n"
        "observed_at: '2026-09-12T04:05:19Z'\n"
        "provenance: server_local_agent\n"
        "observed_by: serveros-fleet-controller\n"
        "transport: remote-desktop-commander\n"
        "raw_input: PRESENT\n"
        "groups:\n  claude-code:\n    skill: [feishu-report]\n",
        encoding="utf-8",
    )
    api = _api(tmp_path, control_root=_root(tmp_path), observation_roots=(obs,))
    listing = api.call("ability.list", device_ref="device:work-fj")
    assert listing["state"] == "OBSERVED"
    assert listing["observed_by"] == "serveros-fleet-controller"

    present = api.call("ability.status", device_ref="device:work-fj", ability="feishu-report")
    assert present["state"] == "OBSERVED_PRESENT"
    absent = api.call("ability.status", device_ref="device:work-fj", ability="nope")
    assert absent["state"] == "OBSERVED_ABSENT"


def test_the_newest_observation_wins_by_recorded_time_not_by_mtime(tmp_path):
    """Evidence order must not be a property of the filesystem it sits on."""
    obs = tmp_path / "observations" / "device-work-fj"
    obs.mkdir(parents=True)
    older = obs / "a.observed.yaml"
    newer = obs / "b.observed.yaml"
    older.write_text(
        "device_ref: device:work-fj\nobserved_at: '2026-01-01T00:00:00Z'\n"
        "provenance: x\nraw_input: PRESENT\ngroups: {g: {k: [old-ability]}}\n",
        encoding="utf-8",
    )
    newer.write_text(
        "device_ref: device:work-fj\nobserved_at: '2026-09-12T00:00:00Z'\n"
        "provenance: x\nraw_input: PRESENT\ngroups: {g: {k: [new-ability]}}\n",
        encoding="utf-8",
    )
    # Make the OLDER record the most recently modified file.
    import os, time
    os.utime(newer, (1, 1))
    assert time.time() > 1

    api = _api(tmp_path, control_root=_root(tmp_path), observation_roots=(tmp_path / "observations",))
    assert api.call("ability.status", device_ref="device:work-fj",
                    ability="new-ability")["state"] == "OBSERVED_PRESENT"


# ---------------------------------------------------------------------------
# Phase G — an unavailable controller never renders as an empty fleet
# ---------------------------------------------------------------------------


def test_a_controller_less_runtime_still_lists_every_device(tmp_path):
    api = _api(tmp_path, control_root=_root(tmp_path))
    fleet = api.call("fleet.devices")

    assert fleet["count"] == 5
    assert {d["device_ref"] for d in fleet["devices"]} == {
        "device:server-beijing", "device:work-fj", "device:personal-dell",
        "device:tokyo-shared-dev-01", "device:llm-lab-dell"}
    assert fleet["controller"] == "NOT_EXPOSED"
    for device in fleet["devices"]:
        assert device["availability"]["local_agent_controller"] == "NOT_EXPOSED"
        assert device["availability"]["remote_inventory_from_this_runtime"] == "UNAVAILABLE"
        assert device["availability"]["state"] != "DEVICE_UNOBSERVED"


def test_fj_reports_the_three_axes_the_brief_requires(tmp_path):
    """The exact shape: presence, controller and inventory, never one word."""
    api = _api(
        tmp_path,
        control_root=_root(tmp_path),
        presence={"device:work-fj": {"state": "KNOWN_ONLINE", "provenance": "user_reported"}},
    )
    fj = api.call("fleet.device.inspect", device_ref="device:work-fj")
    av = fj["availability"]

    assert av["fleet_presence"] == "KNOWN_ONLINE"
    assert av["local_agent_controller"] == "NOT_EXPOSED"
    assert av["remote_inventory_from_this_runtime"] == "UNAVAILABLE"
    assert av["state"] == "REMOTE_CONTROLLER_NOT_EXPOSED"
    assert av["state"] != "DEVICE_UNOBSERVED"
    # And the machine is not silently merged with the other Windows laptop.
    assert fj["identity"]["display_name"] == "FJPCOS"
    assert fj["identity"]["environment_profile"] == "windows-corporate"
    assert fj["reachable_transport_kinds"] == []


def test_presence_without_evidence_is_unknown_not_online(tmp_path):
    """A default of 'online' is a claim this runtime has not earned."""
    api = _api(tmp_path, control_root=_root(tmp_path))
    fj = api.call("fleet.device.inspect", device_ref="device:work-fj")
    assert fj["availability"]["fleet_presence"] == "UNKNOWN"
    assert fj["availability"]["fleet_presence_provenance"] == "NOT_MEASURED"
    assert fj["availability"]["state"] == "CONTROLLER_NOT_EXPOSED_AND_PRESENCE_UNKNOWN"


def test_capability_find_reports_external_only_rather_than_dropping_devices(tmp_path):
    """Filtering EXTERNAL_ONLY out leaves an empty result, which reads as 'nowhere'."""
    root = _root(tmp_path)
    (root / "targets.yaml").write_text(
        "bindings:\n  device:work-fj:\n    kind: remote-desktop-commander\n",
        encoding="utf-8",
    )
    api = _api(tmp_path, control_root=root)

    for level in ("inspect", "INSPECT"):
        found = api.call("fleet.capability.find", level=level)
        assert found["level"] == "inspect" and found["aliased"] is (level != "inspect")
        assert {row["device_ref"] for row in found["devices"]} == {
            "device:server-beijing", "device:work-fj", "device:personal-dell",
            "device:tokyo-shared-dev-01", "device:llm-lab-dell"}
        assert found["summary"] == ["EXTERNAL_ONLY"]
        assert "unsupported" not in found["note"].split("It is not")[0]


def test_capability_find_refuses_a_level_that_does_not_exist(tmp_path):
    api = _api(tmp_path, control_root=_root(tmp_path))
    with pytest.raises(ValueError):
        api.call("fleet.capability.find", level="root-access")


# ---------------------------------------------------------------------------
# determinism — the property that makes two readings comparable
# ---------------------------------------------------------------------------


def test_every_interface_is_deterministic(tmp_path):
    """Same inputs, same output. No clock, no pid, no hostname, no mtime."""
    root = _root(tmp_path)
    obs = tmp_path / "observations"
    (obs / "device-work-fj").mkdir(parents=True)
    (obs / "device-work-fj" / "a.observed.yaml").write_text(
        "device_ref: device:work-fj\nobserved_at: '2026-09-12T04:05:19Z'\n"
        "provenance: p\nraw_input: PRESENT\ngroups: {g: {k: [a]}}\n", encoding="utf-8",
    )
    api = _api(tmp_path, control_root=root, observation_roots=(obs,))
    calls = {
        "device.show": {}, "environment.inspect": {},
        "context.resolve": {"role": "workspace.src", "device_ref": "device:work-fj"},
        "path.resolve": {"role": "workspace.src", "device_ref": "device:work-fj"},
        "ability.list": {"device_ref": "device:work-fj"},
        "ability.status": {"device_ref": "device:work-fj", "ability": "a"},
        "fleet.devices": {},
        "fleet.device.inspect": {"device_ref": "device:work-fj"},
        "fleet.capability.find": {"level": "read"},
    }
    assert set(calls) == set(interfaces.declared_interfaces())
    for name, kwargs in calls.items():
        first = api.call(name, **kwargs)
        second = api.call(name, **kwargs)
        assert first == second, f"{name} is not deterministic"


def test_no_interface_result_carries_a_credential(tmp_path):
    """Credentials never enter public state, including through a result."""
    root = _root(tmp_path)
    api = _api(tmp_path, control_root=root)
    forbidden = {"token", "access_token", "refresh_token", "secret", "password",
                 "private_key", "client_secret", "device_code", "authorization"}

    def keys(node_):
        if isinstance(node_, dict):
            for k, v in node_.items():
                yield str(k).lower()
                yield from keys(v)
        elif isinstance(node_, list):
            for item in node_:
                yield from keys(item)

    for name in interfaces.declared_interfaces():
        kwargs = {
            "path.resolve": {"role": "workspace.src", "device_ref": "device:work-fj"},
            "context.resolve": {"role": "workspace.src", "device_ref": "device:work-fj"},
            "ability.list": {"device_ref": "device:work-fj"},
            "ability.status": {"device_ref": "device:work-fj", "ability": "x"},
            "fleet.device.inspect": {"device_ref": "device:work-fj"},
            "fleet.capability.find": {"level": "read"},
        }.get(name, {})
        present = set(keys(api.call(name, **kwargs)))
        assert not (present & forbidden), f"{name}: {sorted(present & forbidden)}"
