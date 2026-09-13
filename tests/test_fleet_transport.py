"""Tests for the ServerOS fleet identity / availability / transport boundary.

Hermetic: the registry, profiles and contract are read from this repository;
no test touches the live fleet, a real transport, or another machine.
"""
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "fleet"


def _load(name: str, path: Path):
    # Cached by module name, like every other loader in this suite. This file
    # used to re-execute unconditionally, which meant whichever test file pytest
    # collected first decided which `transport` module object was current — and
    # the identity assertions in `test_target_resolver.py` compare class objects
    # with `is`. The note below about two names producing two exception classes
    # applies to one name loaded twice just as well.
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded under the module names the modules themselves use. Loading the same
# file twice under two names yields two distinct exception classes, and the
# resulting `except ControllerNotExposed` silently misses the raise.
transport = _load("serveros_fleet_transport", FLEET / "transport.py")
rdc = _load("serveros_fleet_remote_desktop_commander", FLEET / "remote_desktop_commander.py")
assert rdc.transport is transport, "the adapter must reuse the loaded transport module"

DeviceRef = transport.DeviceRef
DeviceRegistry = transport.DeviceRegistry
Profiles = transport.Profiles
Availability = transport.Availability


def contract(name: str) -> dict:
    return yaml.safe_load((FLEET / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. stable device identity != transport locator
# ---------------------------------------------------------------------------


def test_device_ref_is_typed_and_transport_names_do_not_resolve():
    """A transport's name for a machine is not an identity and must not key one."""
    registry = DeviceRegistry(contract("registry.yaml"))

    # The canonical form is a typed ref, same shape as `skill:<id>`.
    assert DeviceRef("work-fj").ref == "device:work-fj"
    assert registry.resolve("device:work-fj").display_name == "FJPCOS"

    # Names a transport would hand us must fail loudly rather than resolve to
    # some device that happens to look similar.
    for transport_name in ("FJ-NJ-CL-250028", "DESKTOP-JG2FFN3", "fj-cvm-bj-staging-public-other3-05"):
        try:
            registry.resolve(transport_name)
        except transport.UnknownDevice:
            pass
        else:
            raise AssertionError(f"{transport_name!r} resolved as an identity")

    # The id pattern cannot tell a hostname from a stable id — both are
    # lowercase-and-dashes. That is exactly why enrollment is a human decision
    # and why "it parses" is not evidence that it is an identity.
    enrolled = {d.device_ref.id for d in registry.all()}
    assert enrolled.isdisjoint(
        {"fj-nj-cl-250028", "desktop-jg2ffn3", "fj-cvm-bj-staging-public-other3-05"}
    ), "a transport name was enrolled as a device id"


def test_registry_public_surface_carries_no_transport_or_machine_identifiers():
    """The public registry is not a place transport facts can leak into."""
    reg = contract("registry.yaml")
    forbidden = set(contract("device-contract.yaml")["forbidden_public_fields"])

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    present = {k.lower() for k in keys(reg)}
    leaked = present & forbidden
    assert not leaked, f"public registry declares forbidden identity fields: {sorted(leaked)}"


def test_legacy_ids_migrate_rather_than_fork_identity():
    """A rename adds an alias; it does not strand earlier evidence."""
    registry = DeviceRegistry(contract("registry.yaml"))
    assert registry.resolve("linux-server").device_ref.ref == "device:server-beijing"
    assert registry.resolve("dell-work").device_ref.ref == "device:personal-dell"
    # The alias resolves to the SAME Device object, not a second one.
    assert registry.resolve("dell-work") is registry.resolve("device:personal-dell")


# ---------------------------------------------------------------------------
# 2. two Windows machines are two machines
# ---------------------------------------------------------------------------


def test_windows_devices_are_not_merged_by_shared_platform():
    registry = DeviceRegistry(contract("registry.yaml"))
    windows = registry.by_platform("windows")
    assert len(windows) == 2, "both Windows laptops must remain separately enrolled"

    fj, dell = (registry.resolve("device:work-fj"), registry.resolve("device:personal-dell"))
    assert fj.device_ref != dell.device_ref
    assert fj.environment_profile != dell.environment_profile
    assert fj.ownership == "company" and dell.ownership == "personal"
    assert set(fj.roles).isdisjoint(dell.roles)

    # The historical `dell-work` registration belongs to the Dell machine and
    # must never be inferred onto the corporate one by resemblance.
    assert "dell-work" not in fj.legacy_ids
    assert registry.resolve("dell-work") is dell


# ---------------------------------------------------------------------------
# 3. same semantic role, unrelated physical paths
# ---------------------------------------------------------------------------


def test_profiles_bind_one_role_to_different_physical_paths():
    registry = DeviceRegistry(contract("registry.yaml"))
    profiles = Profiles(contract("profiles.yaml"))

    beijing = profiles.resolve(registry.resolve("device:server-beijing"), "workspace.src")
    fj = profiles.resolve(registry.resolve("device:work-fj"), "workspace.src")
    dell = profiles.resolve(registry.resolve("device:personal-dell"), "workspace.src")

    assert beijing.role == fj.role == dell.role == "workspace.src"
    assert len({beijing.path, fj.path, dell.path}) == 3, "paths must differ per device"
    assert beijing.path.startswith("/") and ":" in fj.path, "platform-native forms are expected"


def test_unbound_and_not_applicable_are_different_facts():
    registry = DeviceRegistry(contract("registry.yaml"))
    profiles = Profiles(contract("profiles.yaml"))
    beijing = registry.resolve("device:server-beijing")

    # A role that exists here but has no path yet is a real gap.
    try:
        profiles.resolve(beijing, "downloads")
    except transport.PathRoleUnbound as exc:
        assert not isinstance(exc, transport.PathRoleNotApplicable)
    else:
        raise AssertionError("unbound role resolved anyway")

    # A role with no meaning on this device is not a gap, and saying so is
    # different from saying "we have not looked".
    try:
        profiles.resolve(beijing, "software.gui")
    except transport.PathRoleNotApplicable:
        pass
    else:
        raise AssertionError("not-applicable role resolved anyway")


# ---------------------------------------------------------------------------
# 4. controller not exposed != device unobserved
# ---------------------------------------------------------------------------


def test_missing_controller_reports_not_exposed_not_unobserved(tmp_path):
    """The distinction the whole module exists for."""
    adapter = rdc.RemoteDesktopCommanderTransport(declaration_path=tmp_path / "absent.yaml")
    assert adapter.controller_available() is False

    av = adapter.availability(DeviceRef("work-fj"), fleet_presence="KNOWN_ONLINE")
    assert av.local_agent_controller == "NOT_EXPOSED"
    assert av.remote_inventory_from_this_runtime == "UNAVAILABLE"
    assert av.fleet_presence == "KNOWN_ONLINE"
    assert av.state == "REMOTE_CONTROLLER_NOT_EXPOSED"
    assert av.state != "DEVICE_UNOBSERVED"
    assert av.observed is False


def test_presence_is_an_input_and_is_never_assumed(tmp_path):
    """A transport may not assert that a device is up.

    This adapter used to default `fleet_presence` to KNOWN_ONLINE whenever it
    found a local endpoint, and this test used to assert that. It was the same
    error one level up from the one the module exists to catch: a healthy
    endpoint says a device *might* be reachable, and on 2026-09-12 on this very
    host the endpoint process was alive, connected to its relay, and
    functionally offline for 35 minutes because its session could not be
    renewed. Nothing local distinguishes those cases.

    So presence has no default. A caller that knows supplies it and says how it
    knows; a caller that does not gets UNKNOWN.
    """
    adapter = rdc.RemoteDesktopCommanderTransport(declaration_path=tmp_path / "absent.yaml")
    default = adapter.availability(DeviceRef("work-fj"))
    assert default.fleet_presence == "UNKNOWN"
    assert default.state == "CONTROLLER_NOT_EXPOSED_AND_PRESENCE_UNKNOWN"
    assert default.state != "DEVICE_UNOBSERVED"

    # Offline + no controller is its own state. Before the table was made
    # total this combination matched no row and fell through to
    # DEVICE_UNOBSERVED — the forbidden word, produced silently, for a device
    # that was merely offline.
    offline = adapter.availability(DeviceRef("work-fj"), fleet_presence="KNOWN_OFFLINE")
    assert offline.state == "CONTROLLER_NOT_EXPOSED_AND_DEVICE_OFFLINE"
    assert offline.fleet_presence == "KNOWN_OFFLINE"
    assert offline.state != "DEVICE_UNOBSERVED"


def test_the_availability_table_classifies_every_combination():
    """A partial table has an implicit fallback, and this one's was forbidden.

    Enumerated from the contract's own value lists rather than from a written-
    out list here, so adding a value to either axis fails this test instead of
    silently reintroducing an uncovered row.
    """
    c = contract("transport-contract.yaml")
    fields = c["availability"]["fields"]
    table = c["availability"]["derived_states"]
    assert c["availability"]["rule_table_complete"] is True

    controllers = fields["local_agent_controller"]["values"]
    presences = fields["fleet_presence"]["values"]
    covered = 0
    for ctrl in controllers:
        for pres in presences:
            matches = [r for r in table if r["when"] == {
                "local_agent_controller": ctrl, "fleet_presence": pres}]
            assert len(matches) == 1, (
                f"{ctrl}+{pres} matches {len(matches)} rows; every combination "
                f"must be classified exactly once"
            )
            covered += 1
    assert covered == len(controllers) * len(presences)
    assert len(table) == covered, "no row may classify more than one combination"

    # And the forbidden word stays narrow: exactly one combination reaches it.
    unobserved = [r for r in table if r["state"] == "DEVICE_UNOBSERVED"]
    assert len(unobserved) == 1
    assert unobserved[0]["when"] == {
        "local_agent_controller": "UNKNOWN", "fleet_presence": "UNKNOWN"}


def test_no_device_is_reported_unobserved_by_a_controller_less_runtime():
    """The specific claim: NOT_EXPOSED never renders as DEVICE_UNOBSERVED."""
    adapter = rdc.RemoteDesktopCommanderTransport(declaration_path=Path("/nonexistent"))
    for presence in ("KNOWN_ONLINE", "KNOWN_OFFLINE", "UNKNOWN"):
        av = adapter.availability(DeviceRef("work-fj"), fleet_presence=presence)
        assert av.state != "DEVICE_UNOBSERVED", presence


def test_controller_gap_raises_instead_of_returning_an_empty_fleet(tmp_path):
    """An empty result is a claim about the fleet. We have no basis for it."""
    adapter = rdc.RemoteDesktopCommanderTransport(declaration_path=tmp_path / "absent.yaml")
    for call in (
        lambda: adapter.list_devices(),
        lambda: adapter.inspect_device(DeviceRef("work-fj")),
        lambda: adapter.list_directory(DeviceRef("work-fj"), "D:\\", depth=2),
        lambda: adapter.read_file(DeviceRef("work-fj"), "D:\\x"),
        lambda: adapter.run_command(DeviceRef("work-fj"), "whoami"),
    ):
        try:
            call()
        except transport.ControllerNotExposed:
            pass
        else:
            raise AssertionError("controller gap was not surfaced")


def test_declared_controller_flips_only_the_controller_fields(tmp_path):
    decl = tmp_path / "controller.yaml"
    decl.write_text(
        "kind: mcp-http\nsurface: {resource: 'https://example.invalid'}\n",
        encoding="utf-8",
    )
    adapter = rdc.RemoteDesktopCommanderTransport(declaration_path=decl)
    assert adapter.controller_available() is True
    av = adapter.availability(DeviceRef("work-fj"), fleet_presence="KNOWN_ONLINE")
    assert av.state == "CONTROLLER_AVAILABLE"
    assert av.observed is True
    # Without presence evidence the same adapter reports the controller, and
    # declines to claim the device is up.
    assert adapter.availability(DeviceRef("work-fj")).state == (
        "CONTROLLER_AVAILABLE_PRESENCE_UNKNOWN"
    )


def test_transport_never_silently_falls_back_to_another_kind():
    """No fallback path exists in code, and the contract forbids adding one."""
    c = contract("transport-contract.yaml")
    assert c["transport"]["no_silent_fallback"] is True
    source = (FLEET / "remote_desktop_commander.py").read_text(encoding="utf-8")
    for other in ("paramiko", "winrm", "subprocess.run([\"ssh\"", "ssh "):
        assert other not in source, f"adapter references another transport: {other!r}"


# ---------------------------------------------------------------------------
# 5. bindings carry no credentials
# ---------------------------------------------------------------------------


def test_transport_binding_rejects_credentials():
    for bad in (
        {"device_ref": "device:work-fj", "token": "x"},
        {"device_ref": "device:work-fj", "transport": {"access_token": "x"}},
        {"device_ref": "device:work-fj", "transport": {"auth": {"refresh_token": "x"}}},
        {"device_ref": "device:work-fj", "notes": [{"password": "x"}]},
    ):
        try:
            transport.validate_binding(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"binding accepted a credential: {bad}")

    ok = {"device_ref": "device:work-fj", "kind": "remote-desktop-commander", "locator": "opaque"}
    assert transport.validate_binding(ok) is ok


def test_binding_location_is_local_only():
    c = contract("transport-contract.yaml")
    assert c["transport"]["binding_location"].startswith("~/.serveros/")
    # Bindings must not be readable out of a committed path.
    assert not (ROOT / c["transport"]["binding_location"].lstrip("~/")).exists()


def test_registry_never_carries_a_binding():
    """Identity and route stay in different files, or they merge in practice.

    Checked structurally rather than by substring: the file's *comments*
    legitimately explain which fields are excluded, and a prose mention is not
    a leak. What matters is that no such key is declared, and that no transport
    name appears as a value.
    """
    reg = contract("registry.yaml")
    forbidden = {"locator", "observed_name", "transport_name", "hostname",
                 "computer_name", "transport_uuid"}

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    declared = {k.lower() for k in keys(reg)}
    assert not (declared & forbidden), f"registry declares {sorted(declared & forbidden)}"

    values = {str(v) for v in _values(reg)}
    for transport_name in ("FJ-NJ-CL-250028", "DESKTOP-JG2FFN3"):
        assert transport_name not in values, f"{transport_name} appears as a registry value"


def _values(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _values(v)
    elif isinstance(node, list):
        for item in node:
            yield from _values(item)
    else:
        yield node


# ---------------------------------------------------------------------------
# 6. node MCP and fleet MCP do not own the same things
# ---------------------------------------------------------------------------


def test_no_two_mcp_surfaces_own_or_serve_the_same_thing():
    """Ownership is a partition, pairwise and not just against the node.

    The earlier version compared the node surface with `serveros-fleet-mcp`,
    which was declared with no implementation and no tools — so half of every
    assertion was against an empty set and the test passed without comparing
    anything real. It now runs over whatever surfaces are declared, which is
    also what makes it fail loudly if a fourth is added that overlaps.
    """
    c = contract("mcp-contract.yaml")
    surfaces = c["surfaces"]
    assert len(surfaces) >= 2, "one surface overlaps with nothing"
    for name, surface in surfaces.items():
        assert surface.get("owns"), f"{name} owns nothing, so the check is vacuous"

    names = sorted(surfaces)
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = surfaces[left], surfaces[right]
            assert set(a["owns"]).isdisjoint(b["owns"]), (left, right)
            # A surface implemented elsewhere does not restate its tool list
            # here, so there is nothing in this file to compare — the overlap
            # that matters is the one between lists this file actually holds.
            if "tools" in a and "tools" in b:
                assert set(a["tools"]).isdisjoint(b["tools"]), (left, right)

    # The node surface's blind spot is named as the other surfaces' job rather
    # than left as a hole: another device is answered by the fleet-wide surface.
    node = surfaces["serveros-node-mcp"]
    assert "other devices" in node["does_not_own"]
    assert "cross-device routing" in node["does_not_own"]


def test_no_surface_exposes_a_generic_remote_shell():
    c = contract("mcp-contract.yaml")
    for name, surface in c["surfaces"].items():
        assert "remote.shell" in surface["explicitly_absent"], name
        assert not any("shell" in tool for tool in surface.get("tools", ())), name
        # A surface whose tool list lives in another repository cannot be
        # scanned here, and that is the one thing that may stand in for the
        # scan: it must say where the list is. Silence would read as a clean
        # result, which is the failure this whole test is about.
        if "tools" not in surface:
            assert surface.get("tools_source"), (
                f"{name} declares no tools and does not say where they live, so "
                f"this test cannot tell an empty surface from an unscanned one")

    # Remote execution, when added, is admitted by level — never by raw command.
    levels = transport.CAPABILITY_LEVELS
    default = c["remote_execution"]["default_level"]
    assert default in levels
    # The default must not be the weakest level. `discover` lists that devices
    # exist; defaulting to it would let an unnamed read be admitted as a listing.
    assert levels[0] == "discover" and default != levels[0]
    assert "admin" in levels and "read" in levels


def test_the_capability_vocabulary_is_declared_exactly_once():
    """Two declarations of one closed vocabulary drift, and drift silently.

    This was a real defect: the levels lived in `transport.py` AND in
    `mcp-contract.yaml`, agreeing by inspection. Admission would have kept
    accepting a level the transport contract no longer named.
    """
    mcp = contract("mcp-contract.yaml")
    assert "levels" not in mcp["remote_execution"], "the list came back"
    assert mcp["remote_execution"]["levels_source"].startswith("fleet/transport-contract.yaml")

    tc = contract("transport-contract.yaml")
    declared = tc["capability_levels"]["order"]
    assert list(transport.CAPABILITY_LEVELS) == declared

    # The brief's spelling maps onto the same list rather than extending it.
    for alias, canonical in tc["capability_levels"]["alias_of"].items():
        assert canonical in declared, f"{alias} maps to unknown level {canonical!r}"
        assert alias not in declared, f"{alias} is a second spelling in the canonical list"


def test_both_requested_capability_names_are_present():
    """`DISCOVER` and `GUI_CONTROL` were genuinely missing, not merely renamed."""
    for requested in ("DISCOVER", "INSPECT", "READ_FS", "WRITE_FS", "EXECUTE", "GUI_CONTROL", "ADMIN"):
        assert transport.canonical_level(requested) in transport.CAPABILITY_LEVELS, requested
    assert transport.canonical_level("READ_FS") == "read"
    assert transport.canonical_level("GUI_CONTROL") == "gui-control"
    # A level that was never requested keeps its name unchanged.
    assert transport.canonical_level("inspect") == "inspect"


# ---------------------------------------------------------------------------
# 7. profiles are not required to be isomorphic
# ---------------------------------------------------------------------------


def test_profiles_are_not_required_to_be_isomorphic():
    """Profiles share a role vocabulary, not a set of bindings.

    Declaring every vocabulary role everywhere and stating its state is more
    informative than omitting it, so the test asserts on BOUND sets and on the
    presence of non-applicable roles — not on which keys exist.
    """
    c = contract("profiles.yaml")
    profiles = c["profiles"]
    assert set(profiles) == {"linux-server", "windows-corporate", "windows-personal"}

    bound = {
        name: {role for role, e in p["paths"].items() if e.get("state", "BOUND") == "BOUND"}
        for name, p in profiles.items()
    }
    assert len(set(map(frozenset, bound.values()))) > 1, (
        "forcing identical bound sets would install Linux-shaped roles onto "
        "Windows laptops"
    )
    assert bound["linux-server"] != bound["windows-corporate"]

    non_applicable = {
        name: {role for role, e in p["paths"].items() if e.get("state") == "NOT_APPLICABLE"}
        for name, p in profiles.items()
    }
    assert any(non_applicable.values()), "a headless server has no GUI software role"

    # Every declared role is from the closed vocabulary.
    for name, profile in profiles.items():
        assert set(profile["paths"]) <= set(c["path_roles"]), f"{name} declares an unknown role"
        for role, entry in profile["paths"].items():
            assert entry.get("state") in set(c["binding_states"]), f"{name}.{role} bad state"
            assert entry.get("provenance"), f"{name}.{role} has no provenance"


def test_device_profiles_reference_enrolled_devices():
    registry = DeviceRegistry(contract("registry.yaml"))
    c = contract("profiles.yaml")
    for name, profile in c["profiles"].items():
        device = registry.resolve(profile["device_ref"])
        assert device.environment_profile == name
        assert device.platform == profile["platform"]


# ---------------------------------------------------------------------------
# Availability model is read from the contract, not duplicated in code
# ---------------------------------------------------------------------------


def test_availability_states_come_from_the_contract():
    c = contract("transport-contract.yaml")
    declared = [row["state"] for row in c["availability"]["derived_states"]]

    av = Availability(DeviceRef("work-fj"), fleet_presence="KNOWN_ONLINE",
                      local_agent_controller="NOT_EXPOSED")
    assert av.state in declared

    # The ordering is the contract's, so adding a state means editing one file.
    for row in c["availability"]["derived_states"]:
        probe = Availability(DeviceRef("work-fj"), **row["when"])
        assert probe.state == row["state"]


def test_availability_rejects_values_outside_the_declared_enums():
    for kwargs in (
        {"fleet_presence": "ONLINE"},
        {"local_agent_controller": "EXPOSED"},
        {"remote_inventory_from_this_runtime": "MAYBE"},
    ):
        try:
            Availability(DeviceRef("work-fj"), **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"availability accepted {kwargs}")
