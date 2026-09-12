"""Where the Fleet controller is, and the boundary ServerOS keeps to it.

The finding these tests pin down is narrow and load-bearing: the endpoint role
and the controller role are different roles, shipped by different parties, and
one being present says nothing about the other. Every test below is a way that
conflation could re-enter the code — as an empty device list, as a boolean, as
a fallback transport, or as an identity taken from whatever the controller
happens to call a machine.

Hermetic. Nothing here contacts the controller, the relay, or another device,
and no test reads a credential. The external surface is asserted on as a
*description* read from the contract, which is what makes those assertions
runnable without an authorization.
"""
import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLEET = ROOT / "fleet"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", FLEET / "transport.py")
controller = _load("serveros_fleet_controller", FLEET / "controller.py")
rdc = _load("serveros_fleet_remote_desktop_commander", FLEET / "remote_desktop_commander.py")

# The double-load trap, asserted rather than assumed: if any module ends up
# loaded twice, `except ControllerNotExposed` silently stops matching.
assert rdc.transport is transport, "the adapter must reuse the loaded transport module"
assert controller.transport is transport, "the controller module must reuse it too"

DeviceRef = transport.DeviceRef
CapabilityAvailability = transport.CapabilityAvailability


def contract(name: str) -> dict:
    return yaml.safe_load((FLEET / name).read_text(encoding="utf-8"))


def _no_controller(tmp_path: Path, **kwargs) -> "rdc.RemoteDesktopCommanderTransport":
    """An adapter with no controller declared, at a path that does not exist.

    The declaration path is explicit and inside the sandbox: a helper that
    defaulted to the real `~/.serveros/controller.yaml` would make this suite's
    result depend on the machine that ran it.
    """
    return rdc.RemoteDesktopCommanderTransport(
        declaration_path=tmp_path / "no-such-controller.yaml", **kwargs
    )


def _authorized(tmp_path: Path, granted: tuple[str, ...] = (), **kwargs):
    decl = tmp_path / "controller.yaml"
    decl.write_text(
        "kind: mcp-http\nsurface:\n  resource: 'https://example.invalid'\n",
        encoding="utf-8",
    )
    return rdc.RemoteDesktopCommanderTransport(
        declaration_path=decl, granted_levels=granted, **kwargs
    )


# ---------------------------------------------------------------------------
# §11.1 — an endpoint present is not a controller available
# ---------------------------------------------------------------------------


def test_an_endpoint_present_is_not_a_controller_available(tmp_path):
    """The single most load-bearing distinction in this round.

    The endpoint has been running on the server node for days. It is not, and
    was never, something this runtime can call. One boolean for both facts
    cannot express that, which is why there is no longer one boolean.
    """
    adapter = _no_controller(tmp_path, endpoint_present=True)

    assert adapter.endpoint_present() is True
    assert adapter.controller_available() is False

    ca = adapter.capability_availability(DeviceRef("work-fj"))
    assert ca.endpoint == "AVAILABLE"
    assert ca.reachable_from_this_runtime() is False
    assert set(ca.external_only()) == set(transport.CAPABILITY_LEVELS)
    assert ca.available_here() == ()


def test_endpoint_absence_and_controller_absence_are_also_separate(tmp_path):
    """Neither field may be derived from the other, in either direction."""
    absent = _no_controller(tmp_path, endpoint_present=False)
    assert absent.capability_availability(DeviceRef("work-fj")).endpoint == "ABSENT"

    # Controller available while the endpoint probe says absent: still a
    # declared, callable surface, because the two roles live on different planes.
    authorized = _authorized(tmp_path, granted=("inspect",), endpoint_present=False)
    ca = authorized.capability_availability(DeviceRef("work-fj"))
    assert ca.endpoint == "ABSENT"
    assert ca.state("inspect") == "AVAILABLE"


# ---------------------------------------------------------------------------
# §11.2 — fleet visibility is not local controller availability
# ---------------------------------------------------------------------------


def test_fleet_visibility_is_not_local_controller_availability(tmp_path):
    """A device can be KNOWN_ONLINE and unreachable from here, simultaneously.

    The failure this prevents is a reader seeing "online" and concluding
    "available", then filing the resulting empty inventory as a real one.
    """
    adapter = _no_controller(tmp_path, endpoint_present=True)
    av = adapter.availability(DeviceRef("work-fj"))

    assert av.fleet_presence == "KNOWN_ONLINE"
    assert av.local_agent_controller == "NOT_EXPOSED"
    assert av.remote_inventory_from_this_runtime == "UNAVAILABLE"
    assert av.state == "REMOTE_CONTROLLER_NOT_EXPOSED"
    assert av.observed is False
    assert av.state != "DEVICE_UNOBSERVED"


def test_the_two_layers_do_not_imply_each_other_in_the_contract(tmp_path):
    """The roles are separately declared, and neither implies the other."""
    roles = contract("controller-contract.yaml")["roles"]
    assert roles["endpoint"]["implies_controller"] is False
    assert roles["controller"]["implies_endpoint"] is False
    assert roles["discovery"]["implies_controller"] is False

    # And the package really does ship only one of them.
    assert roles["endpoint"]["shipped_in_package"] is True
    assert roles["controller"]["shipped_in_package"] is False


# ---------------------------------------------------------------------------
# §11.3 — unavailable yields an error, never an empty list
# ---------------------------------------------------------------------------


def test_an_unavailable_controller_never_renders_as_an_empty_fleet(tmp_path):
    """An empty list is a claim about the fleet; we have no basis for one."""
    adapter = _no_controller(tmp_path, endpoint_present=True)
    try:
        result = adapter.list_devices()
    except transport.ControllerNotExposed:
        return
    raise AssertionError(f"the controller gap produced a value instead of raising: {result!r}")


def test_every_operation_fails_at_the_boundary_not_at_the_caller(tmp_path):
    adapter = _no_controller(tmp_path, endpoint_present=True, granted_levels=transport.CAPABILITY_LEVELS)
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


# ---------------------------------------------------------------------------
# §11.4 — a transport uuid never becomes a device identity
# ---------------------------------------------------------------------------


def test_controller_device_names_never_resolve_to_an_identity(tmp_path):
    """`os.hostname()` on the device side is a route's name, not an identity."""
    registry = transport.DeviceRegistry(contract("registry.yaml"))
    for name in ("FJ-NJ-CL-250028", "DESKTOP-JG2FFN3", "fj-cvm-bj-staging-public-other3-05"):
        try:
            registry.resolve(name)
        except transport.UnknownDevice:
            continue
        raise AssertionError(f"a controller-side name resolved to a device: {name}")


def test_the_controller_may_never_supply_an_identity():
    """The registry's forbidden keys and the controller's must agree."""
    never = set(contract("transport-contract.yaml")["identity"]["never_identity"])
    controller_never = set(contract("controller-contract.yaml")["identity_mapping"]["never_infer_identity_from"])

    assert controller_never, "the controller can name devices; it must not name identities"
    assert controller_never <= never, (
        "a name the controller may produce is not forbidden as an identity upstream"
    )
    assert contract("controller-contract.yaml")["identity_mapping"]["may_key"] == ["device_ref"]


# ---------------------------------------------------------------------------
# §11.5 — no silent fallback to another transport
# ---------------------------------------------------------------------------


def test_neither_module_can_reach_another_transport(tmp_path):
    """The fallback does not exist in code, and the contract forbids adding one."""
    assert contract("transport-contract.yaml")["transport"]["no_silent_fallback"] is True

    forbidden = ("paramiko", "winrm", "smbclient", "pysmb", "requests", "httpx", "urllib", "supabase")
    for module in ("remote_desktop_commander.py", "controller.py"):
        source = (FLEET / module).read_text(encoding="utf-8")
        for other in forbidden:
            assert other not in source, f"{module} references {other!r}"

    adapter = _no_controller(tmp_path, endpoint_present=True, granted_levels=transport.CAPABILITY_LEVELS)
    try:
        adapter.read_file(DeviceRef("work-fj"), "D:\\x")
    except transport.ControllerNotExposed:
        pass
    else:
        raise AssertionError("a transport without a controller served a call anyway")


def test_the_contract_names_the_substitution_as_forbidden():
    forbidden = controller.boundary_rules()
    assert "silent_transport_substitution" in forbidden
    assert "SSH" in forbidden["silent_transport_substitution"]
    assert "relay_reverse_engineering" in forbidden
    assert "credential_capture" in forbidden
    assert "identity_impersonation" in forbidden


# ---------------------------------------------------------------------------
# §11.6 — credentials are rejected, and there is nowhere to put one
# ---------------------------------------------------------------------------


def test_a_controller_declaration_may_not_carry_a_credential():
    for bad in (
        {"kind": "mcp-http", "surface": "x", "token": "y"},
        {"kind": "mcp-http", "surface": "x", "auth": {"access_token": "y"}},
        {"kind": "mcp-http", "surface": "x", "auth": {"client_secret": "y"}},
        {"kind": "mcp-http", "surface": "x", "oauth": {"refresh_token": "y"}},
        {"kind": "mcp-http", "surface": "x", "session": {"code_verifier": "y"}},
        {"kind": "mcp-http", "surface": "x", "notes": [{"private_key": "y"}]},
    ):
        try:
            controller.validate_controller_declaration(bad)
        except ValueError:
            continue
        raise AssertionError(f"declaration accepted a credential: {sorted(bad)}")

    ok = {"kind": "mcp-http", "surface": {"resource": "https://example.invalid"}}
    assert controller.validate_controller_declaration(ok) is ok


def test_the_contracts_themselves_carry_no_credential():
    """A contract is the object most likely to be copied into a repository."""
    forbidden = {k.lower() for k in transport.FORBIDDEN_BINDING_KEYS} | {
        "client_secret", "code_verifier", "device_code", "session", "bearer", "api_key",
    }

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield str(key)
                yield from keys(value)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    for name in ("controller-contract.yaml", "transport-contract.yaml"):
        present = {k.lower() for k in keys(contract(name))}
        assert not (present & forbidden), f"{name} declares a credential field: {sorted(present & forbidden)}"


def test_the_declaration_schema_has_nowhere_to_put_a_token(tmp_path):
    """A schema with no credential field cannot leak one."""
    decl = contract("controller-contract.yaml")["declaration"]
    assert set(decl["required_fields"]) == {"kind", "surface"}
    assert not (set(decl["forbidden_fields"]) & set(decl["required_fields"]))
    assert decl["location"].startswith("~/.serveros/")
    assert "REMOTE_CONTROLLER_NOT_EXPOSED" in decl["absent_means"]


def test_a_declaration_in_a_vocabulary_of_its_own_is_not_honoured(tmp_path):
    """`kind` is checked against the contract, not accepted on trust."""
    decl = tmp_path / "controller.yaml"
    decl.write_text("kind: remote-desktop-commander\nsurface: something\n", encoding="utf-8")
    assert controller.declared_controller(decl) is None

    decl.write_text("kind: mcp-http\n", encoding="utf-8")
    assert controller.declared_controller(decl) is None, "surface is required"


# ---------------------------------------------------------------------------
# §11.7 — per-capability availability is deterministic and per-capability
# ---------------------------------------------------------------------------


def test_per_capability_availability_is_deterministic(tmp_path):
    """Same inputs, same answer, and every level answered."""
    adapter = _authorized(tmp_path, granted=("inspect", "read"), endpoint_present=True)
    ref = DeviceRef("work-fj")

    first = adapter.capability_availability(ref).states()
    second = adapter.capability_availability(ref).states()
    assert first == second
    assert set(first) == set(transport.CAPABILITY_LEVELS)

    assert first["inspect"] == "AVAILABLE"
    assert first["read"] == "AVAILABLE"
    # Callable surface, withheld level. NOT the same as EXTERNAL_ONLY.
    assert first["admin"] == "NOT_GRANTED"
    assert first["write-workspace"] == "NOT_GRANTED"


def test_external_only_and_not_granted_are_different_values(tmp_path):
    """Collapsing them erases the step that would close the gap."""
    unavailable = _no_controller(tmp_path, endpoint_present=True).capability_availability(DeviceRef("work-fj"))
    withheld = _authorized(tmp_path, granted=(), endpoint_present=True).capability_availability(DeviceRef("work-fj"))

    assert unavailable.state("read") == "EXTERNAL_ONLY"
    assert withheld.state("read") == "NOT_GRANTED"
    assert unavailable.state("read") != withheld.state("read")


def test_an_unstated_level_is_unknown_not_a_no(tmp_path):
    """Absence of measurement must not be reported as absence of capability."""
    ca = CapabilityAvailability(device_ref=DeviceRef("work-fj"), transport_kind="test")
    assert ca.state("read") == "UNKNOWN"
    assert ca.reachable_from_this_runtime() is False
    assert ca.external_only() == ()
    # ...and the filling-in happens without inventing an entry per level.
    assert set(ca.states()) == set(transport.CAPABILITY_LEVELS)


def test_capability_availability_rejects_values_outside_the_enums():
    for kwargs in (
        {"endpoint": "PRESENT"},
        {"capabilities": (("read", "MAYBE"),)},
        {"capabilities": (("sudo", "AVAILABLE"),)},
    ):
        try:
            CapabilityAvailability(device_ref=DeviceRef("work-fj"), transport_kind="test", **kwargs)
        except ValueError:
            continue
        raise AssertionError(f"capability availability accepted {kwargs}")


def test_an_unknown_level_is_rejected_rather_than_defaulted():
    adapter = rdc.RemoteDesktopCommanderTransport(granted_levels=("read",))
    assert adapter.granted_levels == ("read",)
    try:
        rdc.RemoteDesktopCommanderTransport(granted_levels=("read", "sudo"))
    except ValueError:
        return
    raise AssertionError("a transport accepted a capability level the contract does not name")


def test_the_brief_s_spelling_is_accepted_and_stored_canonically():
    adapter = rdc.RemoteDesktopCommanderTransport(granted_levels=("INSPECT", "READ_FS"))
    assert adapter.granted_levels == ("inspect", "read")
    adapter.require("READ_FS")


# ---------------------------------------------------------------------------
# §11.8 — no bridge, and nothing that talks to the controller
# ---------------------------------------------------------------------------


def test_no_bridge_is_implemented_and_none_is_lying_around():
    """Case A did not apply: the package has no controller role to wrap.

    The integration path is a standard MCP client, which is configuration, not
    code. A bridge would exist only to re-implement a protocol that already
    has an implementation, so its absence is asserted rather than assumed.
    """
    connector = contract("controller-contract.yaml")["connector"]
    assert connector["status"] == "NOT_IMPLEMENTED"
    assert connector["blocked_on"], "an unimplemented thing states why it is unimplemented"
    assert "reimplement the relay protocol" in connector["must_not"]

    for path in sorted(FLEET.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "desktopcommander.app" not in source, f"{path.name} hardcodes the cloud endpoint"


def test_the_adapter_may_only_name_public_call_surfaces():
    """The checkable form of "the bridge only calls the official API"."""
    boundary = contract("controller-contract.yaml")["boundary"]
    assert set(boundary["adapter_may_call"]) == {"mcp-http", "local_mcp_stdio"}

    for module in ("remote_desktop_commander.py", "controller.py", "transport.py"):
        source = (FLEET / module).read_text(encoding="utf-8")
        for scheme in ("ws://", "wss://", "postgres://", "supabase.co"):
            assert scheme not in source, f"{module} reaches for {scheme}"


def test_the_external_surface_is_a_description_not_a_handle():
    """The adapter can say WHERE the capability is; it cannot become a client."""
    adapter = rdc.RemoteDesktopCommanderTransport(granted_levels=("read",))
    surface = adapter.external_surface()

    assert surface["kind"] == "mcp-http"
    assert surface["resource"].startswith("https://")
    assert surface["auth"]["flow"] == "authorization_code"
    assert surface["auth"]["pkce"] == "S256"
    assert surface["auth"]["requires_human_consent"] is True
    # Nothing is callable here yet, and the contract says so.
    assert surface["granted_to_this_runtime"] == []
    # It names the standards it follows, which is why integrating is legitimate
    # where reverse-engineering the relay would not be.
    assert {"rfc7636", "rfc8414", "rfc7591"} <= set(surface["auth"]["standards"])


def test_the_outstanding_step_is_named_and_is_a_human_one(tmp_path):
    assert controller.authorization_outstanding(None) is True
    assert controller.authorization_outstanding({"kind": "mcp-http"}) is False

    surface = controller.external_controller()
    assert surface["auth"]["requires_human_consent"] is True


# ---------------------------------------------------------------------------
# the diagnostic reports evidence, and cannot be mistaken for a handle
# ---------------------------------------------------------------------------


def test_the_diagnostic_is_not_shaped_like_a_controller_handle(tmp_path):
    observation = rdc.observe_local_endpoint()
    assert set(observation) == {
        "endpoint_present",
        "endpoints",
        "listening_sockets",
        "controller_declaration",
        "controller_declared",
        "note",
    }
    # A value the adapter could accept would be a value someone would pass to
    # it. `declared_controller` accepts `kind`+`surface`; this has neither.
    assert "kind" not in observation and "surface" not in observation


def test_the_diagnostic_reports_listening_sockets_per_endpoint():
    """The measurement that separates dialling out from being callable."""
    observation = rdc.observe_local_endpoint()
    for endpoint in observation["endpoints"]:
        assert isinstance(endpoint["listening_sockets"], int)
        assert endpoint["mode"] in {"remote", "local"}
    assert observation["listening_sockets"] == sum(e["listening_sockets"] for e in observation["endpoints"])


def test_a_shell_that_merely_names_the_path_is_not_an_endpoint():
    """Observed live: a `bash -c` from an earlier session was counted as one.

    Substring-matching the marker across all of argv reports an endpoint
    process on any machine where an unrelated command once mentioned the path.
    The diagnostic then says the endpoint is present — a false positive in
    exactly the direction this whole round is about.
    """
    shell_payload = [
        "bash", "-c",
        'echo "=== remote help ==="; ls /home/hwz/.local/share/desktop-commander/',
    ]
    assert rdc.classify_argv(shell_payload) == (False, None)

    # ...while the three argv shapes that ARE invocations still match.
    assert rdc.classify_argv(["/home/hwz/.local/share/desktop-commander/node_modules/.bin/desktop-commander", "remote"]) == (True, "remote")
    assert rdc.classify_argv(["/home/hwz/.local/share/desktop-commander/node_modules/.bin/desktop-commander"]) == (True, "local")
    assert rdc.classify_argv(["node", "/home/hwz/.local/share/desktop-commander/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"]) == (True, "local")

    # The role comes from the arguments after the binary, not from anywhere in
    # the string: a local invocation of a path containing "remote" is still local.
    assert rdc.classify_argv(["/opt/remote/desktop-commander"]) == (True, "local")
    assert rdc.classify_argv([]) == (False, None)


def test_the_probe_does_not_observe_itself():
    """A probe that counted itself would report an endpoint on every machine."""
    mine = rdc._ancestors()
    import os

    assert os.getpid() in mine
    assert 0 not in mine and 1 not in mine


def test_the_external_endpoint_is_recorded_without_a_credential():
    """The resource is public; the authorization is not, and is not recorded."""
    text = (FLEET / "controller-contract.yaml").read_text(encoding="utf-8")
    assert "mcp.desktopcommander.app" in text
    # A protocol version and a resource URL are public. A token is not, and
    # the file must not have grown a place to put one.
    for pattern in (r"client_secret:\s*\S", r"access_token:\s*\S", r"refresh_token:\s*\S"):
        assert not re.search(pattern, text), pattern
