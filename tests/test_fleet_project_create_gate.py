#!/usr/bin/env python3
"""`fleet_cloudcli_project_create`: a create floored over a path, not an id.

What this file is the admission question for
--------------------------------------------
`fleet_cloudcli_session_create` answers "may a session be minted in THIS
project", and `policy.create_allow` is a list of ids that exist. A project being
created has no id yet — deriving one would mean pre-listing a name by definition
not there — so the mirrored `project_create` is floored over the directory it
will make instead.

That makes it the ONE verb whose subject does not exist when the gate runs, and
the tests below exist because "does not exist yet" is exactly where a gate
degenerates into a formality:

1.  **The outer path is the address, and only the outer path is.** The caller
    names `project_path` at the Fleet layer and `arguments.projectPath` at the
    native layer. The two must be byte-equal, because a call authorized against
    one location and delivered to another is the whole of BLOCKER-1 on the path
    axis. Authorization is never read out of `arguments`.
2.  **Prefixes are component boundaries.** `/a/b` admits `/a/b/c`, not `/a/bc`.
    A string-prefix test that ignored the boundary would let one writable
    directory admit every sibling that merely spells it.
3.  **The wildcard is a second word, not a shorter spelling of the first.** A
    `"*"` prefix needs `allow_wildcard_project_create: true`; the loader refuses
    it otherwise, and the service refuses it again if a config is built in code.
4.  **Both floors still apply.** `create_allow` cannot stand in for
    `project_create_allow` and the reverse; the device's `allow_create` is
    checked before either; and the principal still needs its own `create` grant.

Nothing here contacts a device: the provider is fake and every assertion is
about what the service refused or forwarded before a socket would have opened.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import cloudcli_native, tools  # noqa: E402
from fleet_mcp.config import (  # noqa: E402
    AuditBinding,
    AuthBinding,
    FleetConfig,
    PolicyBinding,
    ProviderBinding,
    ServerBinding,
    load_config,
)
from fleet_mcp.errors import FleetRefusal  # noqa: E402
from fleet_mcp.fleet import FleetService, Principal  # noqa: E402
from fleet_mcp.providers.base import ProviderHealth  # noqa: E402
from fleet_mcp.registry import load_registry  # noqa: E402
from fleet_mcp.scopes import SCOPE_CREATE  # noqa: E402

TOKYO = "device:tokyo-shared-dev-01"
PERSONAL_DELL = "device:personal-dell"
OAUTH = "oauth:fleet-EXAMPLEprincipal01"
WORKSPACE = "/data/home/vince/workspace"
NEW_PATH = WORKSPACE + "/a-new-project"


class FakeProvider:
    """A device that records what it was asked and does nothing else."""

    kind = "cloudcli-mcp-http"

    def __init__(self, device_ref: str) -> None:
        self.device_ref = device_ref
        self.calls: list[dict] = []

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            device_ref=self.device_ref, kind=self.kind, reachable=True,
            authorized=True, detail="fake",
            server_name="claudecodeui-mcp-gateway", server_version="0.1.0",
            tool_count=cloudcli_native.NATIVE_TOOL_COUNT, latency_ms=1.0,
        )

    def native_call(self, tool: str, arguments: dict, *, session_ref=None):
        self.calls.append(
            {"tool": tool, "arguments": arguments, "session_ref": session_ref})
        return {"created": True, "projectPath": arguments.get("projectPath")}


def _registry_file(ownership: dict[str, str]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write("devices:\n")
    for ref, owner in ownership.items():
        name = ref.split(":", 1)[1]
        handle.write(f"  - device_ref: {ref}\n")
        handle.write(f"    display_name: {name}\n")
        handle.write("    platform: linux\n")
        handle.write("    environment_profile: linux-shared-multiuser\n")
        handle.write(f"    ownership: {owner}\n")
    handle.close()
    return Path(handle.name)


class Harness:
    """A service over two devices, with both project floors stated."""

    def __init__(self, tmp: Path, *, allow_create=True,
                 project_create_allow=(WORKSPACE,),
                 allow_wildcard_project_create=False, grants=None,
                 create_allow=None):
        self.tmp = tmp
        anchor = tmp / "pat"
        anchor.write_text("owner-token-value", encoding="utf-8")
        self.ownership = {TOKYO: "company", PERSONAL_DELL: "personal"}
        self.registry = load_registry(_registry_file(self.ownership))
        self.anchor = anchor
        self.config = FleetConfig(
            source=None,
            local_device_ref=TOKYO,
            providers={
                ref: ProviderBinding(
                    device_ref=ref, kind="cloudcli-mcp-http",
                    endpoint=f"http://{ref}/mcp", credential_anchor=anchor,
                    allow_send=True, allow_create=allow_create,
                )
                for ref in self.ownership
            },
            auth=AuthBinding(pat_anchor=anchor),
            server=ServerBinding(bind="127.0.0.1", port=0),
            policy=PolicyBinding(
                operator_grants=dict(grants or {}),
                create_allow=dict(create_allow or {}),
                project_create_allow=tuple(project_create_allow),
                allow_wildcard_project_create=allow_wildcard_project_create,
            ),
            audit=AuditBinding(path=tmp / "audit.jsonl",
                               rate_limit_per_minute=10_000),
        )
        self.providers = {ref: FakeProvider(ref) for ref in self.ownership}
        self.service = FleetService(self.config, registry=self.registry,
                                    providers=dict(self.providers))

    def principal(self, name=OAUTH, *scopes):
        return Principal(name=name,
                         scopes=frozenset(scopes or (SCOPE_CREATE,)),
                         credential_kind="oauth")

    def create(self, path=NEW_PATH, *, arguments=None, outer=None,
               principal=None, device=TOKYO):
        return self.service.native_call(
            principal if principal is not None else self.principal(),
            device_ref=device,
            native_tool="project_create",
            arguments={"projectPath": path} if arguments is None else arguments,
            project_path=path if outer is None else outer,
        )


def refusal(caught: unittest.case._AssertionContextManager) -> FleetRefusal:
    return caught.exception


class ThePathFloor(unittest.TestCase):
    def setUp(self):
        self.h = Harness(Path(tempfile.mkdtemp()),
                         grants={OAUTH: {TOKYO: ("create",)}})

    def test_a_path_under_an_allowed_prefix_is_created(self):
        result = self.h.create()
        self.assertTrue(result["created"])
        call = self.h.providers[TOKYO].calls[0]
        self.assertEqual(call["tool"], "project_create")
        self.assertEqual(call["arguments"], {"projectPath": NEW_PATH})

    def test_the_prefix_itself_is_admitted(self):
        self.h.create(WORKSPACE)

    def test_a_sibling_that_merely_spells_the_prefix_is_not_under_it(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(WORKSPACE + "-sibling/project")
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")
        self.assertIn("project_create_allow", refusal(caught).detail)
        self.assertEqual(self.h.providers[TOKYO].calls, [])

    def test_an_empty_allowlist_admits_nothing(self):
        h = Harness(Path(tempfile.mkdtemp()), project_create_allow=(),
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create()
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_named_prefix_does_not_admit_an_unrelated_tree(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create("/etc/whatever")
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")

    def test_the_wildcard_without_its_flag_is_refused_by_the_service(self):
        # Built in code rather than parsed: the loader refuses this shape, and
        # the service must not depend on the loader having run.
        h = Harness(Path(tempfile.mkdtemp()), project_create_allow=("*",),
                    allow_wildcard_project_create=False,
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create("/etc/whatever")
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_the_wildcard_with_its_flag_admits_outside_every_named_prefix(self):
        h = Harness(Path(tempfile.mkdtemp()), project_create_allow=("*",),
                    allow_wildcard_project_create=True,
                    grants={OAUTH: {TOKYO: ("create",)}})
        h.create("/etc/whatever")


class TheDeviceFloor(unittest.TestCase):
    def test_the_device_flag_is_required_even_with_a_grant(self):
        h = Harness(Path(tempfile.mkdtemp()), allow_create=False,
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create()
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")
        self.assertIn("allow_create", refusal(caught).detail)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_personal_device_cannot_hold_the_grant(self):
        """The eligibility rule is code, and a wildcard does not widen it."""
        h = Harness(Path(tempfile.mkdtemp()),
                    grants={"*": {"*": ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create(device=PERSONAL_DELL)
        self.assertEqual(refusal(caught).code, "WRITE_NOT_GRANTED")
        self.assertIn("company", refusal(caught).detail)
        self.assertEqual(h.providers[PERSONAL_DELL].calls, [])


class TheOperatorVerb(unittest.TestCase):
    def test_a_create_grant_is_required(self):
        h = Harness(Path(tempfile.mkdtemp()), grants={})
        with self.assertRaises(FleetRefusal) as caught:
            h.create()
        self.assertEqual(refusal(caught).code, "WRITE_NOT_GRANTED")
        self.assertIn("create", refusal(caught).detail)
        self.assertEqual(h.providers[TOKYO].calls, [])

    def test_a_send_only_grant_is_not_a_create_grant(self):
        h = Harness(Path(tempfile.mkdtemp()), grants={OAUTH: {TOKYO: ("send",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create()
        self.assertEqual(refusal(caught).code, "WRITE_NOT_GRANTED")

    def test_the_wildcard_grant_admits_an_eligible_device(self):
        h = Harness(Path(tempfile.mkdtemp()), grants={"*": {"*": ("create",)}})
        h.create()

    def test_create_allow_does_not_stand_in_for_the_path_floor(self):
        """The two allowlists answer different questions and neither widens."""
        h = Harness(Path(tempfile.mkdtemp()), project_create_allow=(),
                    create_allow={"*": {}},
                    grants={OAUTH: {TOKYO: ("create",)}})
        with self.assertRaises(FleetRefusal) as caught:
            h.create()
        self.assertEqual(refusal(caught).code, "APPROVAL_REQUIRED")
        self.assertIn("project_create_allow", refusal(caught).detail)


class TheAddressAxis(unittest.TestCase):
    def setUp(self):
        self.h = Harness(Path(tempfile.mkdtemp()),
                         grants={OAUTH: {TOKYO: ("create",)}})

    def test_no_outer_path_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(outer="")
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")
        self.assertIn("project_path", refusal(caught).detail)

    def test_no_native_path_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(arguments={})
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")
        self.assertIn("projectPath", refusal(caught).detail)

    def test_the_outer_and_inner_paths_must_agree(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(outer=NEW_PATH,
                          arguments={"projectPath": WORKSPACE + "/elsewhere"})
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")
        self.assertEqual(self.h.providers[TOKYO].calls, [])

    def test_the_inner_path_is_never_the_authorized_one(self):
        """A clean outer path beside a traversing inner one is a mismatch."""
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(outer=NEW_PATH,
                          arguments={"projectPath": WORKSPACE + "/../etc"})
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")

    def test_a_relative_path_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create("relative/project")
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")

    def test_an_unnormalised_path_is_refused(self):
        for path in (WORKSPACE + "/../etc", WORKSPACE + "/.", WORKSPACE + "/"):
            with self.subTest(path=path):
                with self.assertRaises(FleetRefusal) as caught:
                    self.h.create(path)
                self.assertEqual(refusal(caught).code, "BAD_REQUEST")

    def test_the_root_itself_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create("/")
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")

    def test_a_nul_byte_is_refused(self):
        with self.assertRaises(FleetRefusal) as caught:
            self.h.create(WORKSPACE + "/a\x00b")
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")


class TheParsedShape(unittest.TestCase):
    def config(self, policy: str) -> Path:
        body = (
            "schema: serveros/fleet-mcp.local/v1\n"
            "device_ref: " + TOKYO + "\n"
            "providers:\n"
            "  " + TOKYO + ":\n"
            "    kind: fleet-node\n"
            "    endpoint: http://127.0.0.1:3402\n"
            "    credential_anchor: /nonexistent/secret\n"
            "policy:\n" + policy
        )
        path = Path(tempfile.mkdtemp()) / "binding.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def assertRefused(self, policy: str, needle: str) -> None:
        with self.assertRaises(FleetRefusal) as caught:
            load_config(self.config(policy))
        self.assertEqual(refusal(caught).code, "BAD_REQUEST")
        self.assertIn(needle, refusal(caught).detail)

    def test_the_default_is_empty(self):
        config = load_config(self.config("  create_allow: {}\n"))
        self.assertEqual(config.policy.project_create_allow, ())
        self.assertFalse(config.policy.allow_wildcard_project_create)

    def test_a_named_prefix_loads(self):
        config = load_config(self.config(
            "  project_create_allow:\n    - " + WORKSPACE + "\n"))
        self.assertEqual(config.policy.project_create_allow, (WORKSPACE,))

    def test_a_bare_string_is_refused(self):
        self.assertRefused("  project_create_allow: " + WORKSPACE + "\n",
                           "must be a list")

    def test_a_relative_prefix_is_refused(self):
        self.assertRefused("  project_create_allow:\n    - workspace\n",
                           "absolute")

    def test_a_denormalised_prefix_is_refused(self):
        self.assertRefused(
            "  project_create_allow:\n    - /data/home/../home/vince\n",
            "normalised")

    def test_the_root_prefix_is_refused(self):
        self.assertRefused("  project_create_allow:\n    - /\n", "normalised")

    def test_the_wildcard_without_the_flag_is_refused(self):
        self.assertRefused("  project_create_allow:\n    - '*'\n",
                           "allow_wildcard_project_create")

    def test_the_wildcard_with_the_flag_loads(self):
        config = load_config(self.config(
            "  project_create_allow:\n    - '*'\n"
            "  allow_wildcard_project_create: true\n"))
        self.assertEqual(config.policy.project_create_allow, ("*",))
        self.assertTrue(config.policy.allow_wildcard_project_create)

    def test_mixing_the_wildcard_with_named_prefixes_is_refused(self):
        self.assertRefused(
            "  project_create_allow:\n    - '*'\n    - " + WORKSPACE + "\n"
            "  allow_wildcard_project_create: true\n",
            "must not mix")


class TheSurface(unittest.TestCase):
    def test_the_verb_is_served_and_annotated_as_a_create(self):
        tool = tools.SERVED_BY_NAME["fleet_cloudcli_project_create"]
        self.assertEqual(tool.annotations["readOnlyHint"], False)
        self.assertEqual(tool.annotations["destructiveHint"], False)
        self.assertEqual(tool.annotations["idempotentHint"], False)
        self.assertEqual(tool.annotations["openWorldHint"], False)
        self.assertIn("project_path", tool.input_schema["required"])

    def test_the_manifest_carries_its_governance_and_provenance(self):
        entry = {row["name"]: row for row in cloudcli_native.manifest()["tools"]}[
            "project_create"]
        self.assertEqual(entry["governance"], cloudcli_native.GOV_PROJECT_CREATE)
        self.assertIn("639ded6", entry["recorded_from"])
        self.assertNotEqual(entry["recorded_from"], "CloudCLI 1.39.0")

    def test_a_device_without_the_verb_reports_it_unsupported(self):
        """The mirror existing in Fleet does not claim the device answers it."""
        snapshot = cloudcli_native.capability_snapshot(
            device_ref=TOKYO, observed={}, source="fake", version="0.1.0",
            sampled_at="2026-10-22T00:00:00Z")
        self.assertEqual(snapshot["tools"]["project_create"]["state"],
                         cloudcli_native.UNSUPPORTED_BY_DEVICE)
        self.assertIn("project_create", snapshot["unsupported_by_device"])


if __name__ == "__main__":
    unittest.main()
