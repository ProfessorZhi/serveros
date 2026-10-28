"""Hub CUT-B: one identity authority, reachable as a declared dependency.

The Hub (`fleet_mcp`) must be deployable without `fleet/` beside it, and it must
still have exactly ONE idea of what a device is. Those two requirements pull in
opposite directions, and the two shortcuts that appear to satisfy both are both
wrong:

*   copy the seven identity classes into `fleet_mcp` — a second implementation,
    which agrees on the day it is written and diverges on the first rename;
*   read `fleet/registry.yaml` locally as a fallback — a fleet that exists only
    inside the Hub.

So the seam is explicit: `fleet_mcp/identity_adapter_port.py` declares a
**versioned ABI**, verifies the seven symbols, and can load the canonical module
from a **verified payload** directory (manifest + digest) instead of a checkout.
No payload and no package means the Hub refuses; there is no third path.

Everything here is offline. The payload test copies the canonical
`fleet/transport.py` into a temporary directory — it does not reimplement it.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO / "fleet_mcp" / "registry.py"
PORT_PATH = REPO / "fleet_mcp" / "identity_adapter_port.py"
PORT_MODULE = "fleet_mcp.identity_adapter_port"
SEVEN = {
    "Availability": "class",
    "CapabilityAvailability": "class",
    "Device": "class",
    "DeviceRef": "class",
    "DeviceRegistry": "class",
    "UnknownDevice": "class",
    "canonical_level": "function",
}


def _port():
    """Imported inside tests so this file collects on a base without it."""
    return importlib.import_module(PORT_MODULE)


def _run(code: str, *, extra_path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    paths = [str(REPO)] + ([extra_path] if extra_path else [])
    env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)],
                          capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO))


def _payload(tmp: Path, *, abi: str | None = None, digest: str | None = None,
             manifest: bool = True) -> Path:
    """A payload carrying the CANONICAL authority files, not a copy of its logic.

    `fleet/transport.py` imports `fleet/transport-contract.yaml` at module scope,
    so the contract data is part of the authority payload. `fleet/registry.yaml`
    is authored fleet data and is deliberately NOT copied: a payload must not
    carry a fleet.
    """
    root = tmp / "payload"
    (root / "fleet").mkdir(parents=True)
    copied = {}
    for rel in ("fleet/transport.py", "fleet/transport-contract.yaml"):
        source = REPO / rel
        target = root / rel
        target.write_bytes(source.read_bytes())
        copied[rel] = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest is not None:
        copied["fleet/transport.py"] = digest
    if manifest:
        (root / "fleet-authority.json").write_text(json.dumps({
            "abi": abi or "serveros.fleet.transport/v1",
            "module": "fleet.transport",
            "files": copied,
        }), encoding="utf-8")
    return root


class ThePortDeclaresOneVersionedDependencyTest(unittest.TestCase):
    def test_the_port_declares_a_versioned_abi(self):
        port = _port()
        self.assertEqual(port.AUTHORITY_ABI, "serveros.fleet.transport/v1")
        self.assertEqual(port.AUTHORITY_MODULE, "fleet.transport")

    def test_the_port_names_exactly_the_seven_identity_symbols(self):
        port = _port()
        self.assertEqual(dict(port.REQUIRED_SYMBOLS), SEVEN)

    def test_standalone_requirements_are_explicit_and_have_no_fallback(self):
        port = _port()
        req = port.STANDALONE_REQUIREMENTS
        self.assertEqual(req["abi"], "serveros.fleet.transport/v1")
        self.assertEqual(req["module"], "fleet.transport")
        self.assertEqual(tuple(req["symbols"]), tuple(SEVEN))
        self.assertEqual(req["data_not_bundled"], "fleet/registry.yaml")
        self.assertIsNone(req["fallback_authority"])
        self.assertEqual(tuple(req["resolution_order"]),
                         ("payload", "importable canonical package"))


class TheHubDoesNotVendorIdentityTest(unittest.TestCase):
    def test_registry_does_not_import_fleet_directly(self):
        tree = ast.parse(REGISTRY_PATH.read_text(encoding="utf-8"))
        direct = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "fleet":
                direct.append(node.module)
            if isinstance(node, ast.Import):
                direct += [a.name for a in node.names if a.name.split(".")[0] == "fleet"]
        self.assertEqual(direct, [], "registry.py still imports the authority directly")

    def test_the_port_keeps_the_static_import_the_closure_measures(self):
        """#431 measures a loader closure. A dynamic import would hide the edge."""
        tree = ast.parse(PORT_PATH.read_text(encoding="utf-8"))
        found = [n.module for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module == "fleet.transport"]
        self.assertTrue(found, "the port lost its static `fleet.transport` import")

    def test_no_identity_class_is_defined_inside_fleet_mcp(self):
        offenders = []
        for path in sorted((REPO / "fleet_mcp").rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name in SEVEN:
                    offenders.append(f"{path.relative_to(REPO).as_posix()}:{node.name}")
        self.assertEqual(offenders, [], "a second identity authority was vendored in")

    def test_the_hub_does_not_read_the_registry_as_a_fallback(self):
        """The port may NAME the data file it will not bundle; it must not read it."""
        source = PORT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("safe_load", source)
        self.assertNotIn("def _load_registry", source)
        self.assertNotIn("registry_contract(", source)


class APayloadMustProveItselfTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_verified_payload_is_refused_when_the_digest_does_not_match(self):
        port = _port()
        root = _payload(self.tmp, digest="0" * 64)
        with self.assertRaises(port.AuthorityUnavailable) as caught:
            port.verify_payload(root)
        self.assertIn("digest", str(caught.exception))

    def test_an_abi_mismatch_is_refused(self):
        port = _port()
        root = _payload(self.tmp, abi="serveros.fleet.transport/v2")
        with self.assertRaises(port.AuthorityUnavailable) as caught:
            port.verify_payload(root)
        self.assertIn("ABI", str(caught.exception))

    def test_a_missing_manifest_is_refused(self):
        port = _port()
        root = _payload(self.tmp, manifest=False)
        with self.assertRaises(port.AuthorityUnavailable) as caught:
            port.verify_payload(root)
        self.assertIn("fleet-authority.json", str(caught.exception))

    def test_an_undeclared_module_in_the_payload_is_refused(self):
        """A file on `sys.path` that the manifest never named could be imported."""
        port = _port()
        root = _payload(self.tmp)
        (root / "fleet" / "smuggled.py").write_text("X = 1\n", encoding="utf-8")
        with self.assertRaises(port.AuthorityUnavailable) as caught:
            port.verify_payload(root)
        self.assertIn("does not declare", str(caught.exception))

    def test_a_module_missing_a_symbol_is_refused(self):
        port = _port()
        with self.assertRaises(port.AuthorityUnavailable) as caught:
            port.verify_authority(types.SimpleNamespace())
        self.assertIn("does not expose", str(caught.exception))

    def test_a_symbol_of_the_wrong_kind_is_refused(self):
        port = _port()
        fake = types.SimpleNamespace(**{name: (lambda: None) for name in SEVEN})
        fake.Availability = object()          # not a class
        fake.canonical_level = lambda x: x    # a function, as required
        with self.assertRaises(port.AuthorityUnavailable):
            port.verify_authority(fake)


class ThePayloadFeedsTheOneAuthorityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_verified_payload_is_used_instead_of_the_checkout(self):
        """The checkout's `fleet/` is made UNIMPORTABLE here.

        A test that merely put the payload first on `sys.path` would also pass
        on a Hub that still needed the checkout. This finder refuses any `fleet`
        spec whose origin is under the repository, so the only way the authority
        can load is from the verified payload.
        """
        root = _payload(self.tmp)
        result = _run(f"""
            import sys
            from importlib.machinery import PathFinder

            REPO = {str(REPO)!r}
            PAYLOAD = {str(root)!r}

            class OnlyThePayload:
                def find_spec(self, name, path=None, target=None):
                    if name != "fleet" and not name.startswith("fleet."):
                        return None
                    spec = PathFinder.find_spec(name, path, target)
                    origin = getattr(spec, "origin", None) or ""
                    if origin and not origin.lower().startswith(PAYLOAD.lower()):
                        raise ImportError(
                            "this test forbids resolving fleet from " + origin)
                    return spec

            sys.meta_path.insert(0, OnlyThePayload())
            import {PORT_MODULE} as port
            authority = port.load_authority(PAYLOAD)
            print("SOURCE=" + authority.source)
            print("ABI=" + authority.abi)
            print("SYMBOLS=" + ",".join(sorted(authority.symbols)))
        """)
        self.assertEqual(result.returncode, 0, result.stderr)
        source_line = next(l for l in result.stdout.splitlines() if l.startswith("SOURCE="))
        self.assertIn(str(root).lower(), source_line.lower(),
                      "the authority did not come from the verified payload")
        self.assertIn("ABI=serveros.fleet.transport/v1", result.stdout)
        self.assertIn("canonical_level", result.stdout.split("SYMBOLS=")[-1])


class TheHubFailsClosedTest(unittest.TestCase):
    def test_without_any_authority_the_hub_refuses_and_names_the_abi(self):
        """The control that must keep passing: no payload, no package, no Hub."""
        result = _run("""
            import sys

            class Block:
                def find_spec(self, name, path=None, target=None):
                    if name == "fleet" or name.startswith("fleet."):
                        raise ImportError("blocked for the fail-closed test")
                    return None

            sys.meta_path.insert(0, Block())
            sys.path.insert(0, %r)

            from fleet_mcp.errors import FleetRefusal
            try:
                import fleet_mcp.registry  # noqa: F401
            except FleetRefusal as exc:
                print("CODE=" + exc.code)
                print("DETAIL=" + exc.detail)
            else:
                print("CODE=NONE")
        """ % str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CODE=INTERNAL", result.stdout)
        self.assertIn("serveros.fleet.transport/v1", result.stdout)
        self.assertIn("second copy of device identity", result.stdout)


class RegistrySemanticsAreUnchangedTest(unittest.TestCase):
    def test_unknown_device_stays_unknown_device(self):
        registry_mod = importlib.import_module("fleet_mcp.registry")
        from fleet_mcp.errors import FleetRefusal

        registry = registry_mod.DeviceRegistry({"devices": []})
        with self.assertRaises(FleetRefusal) as caught:
            registry_mod.resolve_or_refuse(registry, "device:not-enrolled")
        self.assertEqual(caught.exception.code, "UNKNOWN_DEVICE")

    def test_a_missing_registry_override_is_a_bad_request(self):
        registry_mod = importlib.import_module("fleet_mcp.registry")
        from fleet_mcp.errors import FleetRefusal

        with self.assertRaises(FleetRefusal) as caught:
            registry_mod.load_registry("/nonexistent/registry.yaml")
        self.assertEqual(caught.exception.code, "BAD_REQUEST")

    def test_the_authority_module_is_the_canonical_one(self):
        registry_mod = importlib.import_module("fleet_mcp.registry")
        self.assertEqual(registry_mod.DeviceRegistry.__module__, "fleet.transport")
        self.assertEqual(registry_mod.DeviceRef.__module__, "fleet.transport")


class The431ClosureStaysTruthfulTest(unittest.TestCase):
    def test_the_only_fleet_edge_left_is_the_declared_module(self):
        modules: set[str] = set()
        for path in (REGISTRY_PATH, PORT_PATH):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.add(node.module)
                elif isinstance(node, ast.Import):
                    modules.update(a.name for a in node.names)
        fleet_edges = {m for m in modules if m.split(".")[0] == "fleet"}
        self.assertEqual(fleet_edges, {"fleet.transport"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
