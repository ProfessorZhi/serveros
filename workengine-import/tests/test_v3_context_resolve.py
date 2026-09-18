"""Contract tests for `serverosctl context resolve`.

The Capsule itself is a ServerOS product component that is not importable from
this repository, so these tests pin the delegation contract instead: the roots
this CLI resolved must be the roots the resolver actually reads, and an absent
bundle must fail with an actionable error.

Hermetic. A stub resolver records the arguments it was handed, so nothing here
reads this machine's real bundle, records or state.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Records the arguments it receives, so the delegation contract is observable.
STUB = '''#!/usr/bin/env python3
import json, sys
argv = sys.argv[1:]
VALUED = {"--workspace-root", "--artifacts-root", "--state-dir", "--cwd"}
roots, positional, i = {}, [], 0
while i < len(argv):
    token = argv[i]
    if token in VALUED:
        roots[token.lstrip("-")] = argv[i + 1]
        i += 2
        continue
    if not token.startswith("--"):
        positional.append(token)
    i += 1
print(json.dumps({
    "schema": "stub",
    "roots": roots,
    "item": positional[0] if positional else None,
    "json": "--json" in argv,
}))
'''


class ContextResolveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.bundle = self.base / "bundle"
        (self.bundle / "context").mkdir(parents=True)
        (self.bundle / "context" / "resolver.py").write_text(STUB)

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, env_overrides, *args):
        env = os.environ.copy()
        for key in ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT", "SERVEROS_BUNDLE",
                    "SERVEROS_STATE_DIR", "SERVEROS_ARTIFACTS_ROOT", "SERVEROS_PYTHON",
                    "SERVEROS_HOST_PROFILE", "SERVEROS_RUNTIME_ASSETS"):
            env.pop(key, None)
        env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-m", "workengine", *args],
            text=True, capture_output=True, env=env, check=False,
        )

    def resolved(self, value: str) -> str:
        return str(Path(value).resolve())

    def test_adapter_passes_its_resolved_roots_to_the_resolver(self):
        """`run show` and `context resolve` must not be able to read two worlds."""
        state, artifacts, workspace = (self.base / n for n in ("state", "artifacts", "workspace"))
        result = self.run_cli(
            {"SERVEROS_BUNDLE": str(self.bundle), "SERVEROS_STATE_DIR": str(state),
             "SERVEROS_ARTIFACTS_ROOT": str(artifacts), "WORKSPACE_ROOT": str(workspace)},
            "context", "resolve", "LIT-0101",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        roots = json.loads(result.stdout)["roots"]
        self.assertEqual(roots["state-dir"], self.resolved(state))
        self.assertEqual(roots["artifacts-root"], self.resolved(artifacts))
        self.assertEqual(roots["workspace-root"], self.resolved(workspace))

    def test_roots_default_to_the_devices_own_profile(self):
        """With no environment at all, the roots follow the device's profile.

        This assertion used to read `== "/opt/applications/data/home-hwz/..."`,
        under the name "the canonical contract". It was not a contract; it was
        one machine's binding compiled into the binary. On the machine this CLI
        actually runs on, that path does not exist -- so `run begin` committed
        its Run row and *then* failed the artifacts mkdir. The profile below is
        synthetic: what is pinned is that the answer follows the profile rather
        than the binary.
        """
        profile = self.base / "host-profile.yaml"
        profile.write_text(
            "schema: serveros/host-profile.v1\n"
            "node:\n"
            "  device_ref: device:test-device\n"
            "  roots:\n"
            f"    state_root: {self.base}/device/state/serveros\n"
            f"    artifacts_root: {self.base}/device/artifacts\n"
            f"    workspace_root: {self.base}/device/workspace\n"
            f"    runtime_assets_root: {self.base}/device/runtime-assets\n",
            encoding="utf-8",
        )
        result = self.run_cli(
            {"SERVEROS_BUNDLE": str(self.bundle),
             "SERVEROS_HOST_PROFILE": str(profile)},
            "context", "resolve", "LIT-0101",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        roots = json.loads(result.stdout)["roots"]
        self.assertEqual(roots["state-dir"], self.resolved(self.base / "device/state/serveros"))
        self.assertEqual(roots["artifacts-root"], self.resolved(self.base / "device/artifacts"))
        self.assertEqual(roots["workspace-root"], self.resolved(self.base / "device/workspace"))

    def test_explicit_flags_win_over_the_environment(self):
        """A per-call root is honoured, not silently dropped."""
        flagged = self.base / "flagged"
        result = self.run_cli(
            {"SERVEROS_BUNDLE": str(self.bundle)},
            "context", "resolve", "LIT-0101",
            "--state-dir", str(flagged / "state"),
            "--artifacts-root", str(flagged / "artifacts"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        roots = json.loads(result.stdout)["roots"]
        self.assertEqual(roots["state-dir"], self.resolved(flagged / "state"))
        self.assertEqual(roots["artifacts-root"], self.resolved(flagged / "artifacts"))

    def test_item_is_forwarded_and_stays_optional(self):
        result = self.run_cli({"SERVEROS_BUNDLE": str(self.bundle)}, "context", "resolve", "LIT-0101")
        self.assertEqual(json.loads(result.stdout)["item"], "LIT-0101")
        result = self.run_cli({"SERVEROS_BUNDLE": str(self.bundle)}, "context", "resolve")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(json.loads(result.stdout)["item"])

    def test_resolver_always_asked_for_json(self):
        result = self.run_cli({"SERVEROS_BUNDLE": str(self.bundle)}, "context", "resolve", "LIT-0101")
        self.assertIs(json.loads(result.stdout)["json"], True)

    def test_absent_bundle_fails_with_an_actionable_error(self):
        empty = self.base / "empty"
        empty.mkdir()
        # All three candidates have to miss. The second is now addressed from
        # the device's `runtime_assets_root` role rather than from a data root,
        # so DATA_ROOT is no longer the knob that empties it -- and on a real
        # host the device binding would find a genuine bundle, which is exactly
        # what this test must not accidentally do.
        result = self.run_cli(
            {"SERVEROS_BUNDLE": str(empty), "SERVEROS_RUNTIME_ASSETS": str(empty),
             "WORKSPACE_ROOT": str(empty)},
            "context", "resolve", "LIT-0101",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("resolver not found", result.stderr)
        self.assertIn("SERVEROS_BUNDLE", result.stderr)


if __name__ == "__main__":
    unittest.main()
