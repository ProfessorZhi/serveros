"""The root-resolution chain, pinned at every link.

`workengine` used to carry one machine's answer as a literal
(`/opt/applications/data/home-hwz/...`). A wrong default root is not a loud
failure: on a host that does not have the path, the run does not stop at the
start -- it stops *after* the store has already taken a Run row, leaving a
record pointing somewhere that does not exist.

The chain is, in order: an explicit argument from the caller, the root's own
environment variable, the device's host profile, and UNBOUND -- which raises.
There is no fallback to the other machine and no `$HOME` guess, because a
guessed root *resolves*, and that is what makes it worse than an error.

The last test reads this repository's own source, so the literal cannot come
back without turning something red.
"""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROFILE = """schema: serveros/host-profile.v1
node:
  device_ref: device:test-device
  roots:
    identity_home: {base}/home
    state_root: {base}/state/serveros
    artifacts_root: {base}/artifacts
    workspace_root: {base}/workspace
    runtime_assets_root: {base}/runtime-assets
"""

CLEARED = ("DATA_ROOT", "WORKSPACE_ROOT", "SERVEROS_BUNDLE", "SERVEROS_STATE_DIR",
           "SERVEROS_ARTIFACTS_ROOT", "SERVEROS_HOST_PROFILE",
           "SERVEROS_RUNTIME_ASSETS", "SERVEROS_PYTHON")


def _value_strings(path):
    """Every string constant in `path` that is not a docstring.

    Docstrings are excluded deliberately. `engine.py` and this file both
    *explain* the old Beijing default so the next reader does not have to
    rediscover why it was wrong, and a scan that could not tell an explanation
    from a binding would push that explanation out of the code -- trading the
    record away to keep a scanner happy. Comments never reach the AST;
    docstrings do, and are skipped by identity.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.value


class RootResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.profile = self.base / "host-profile.yaml"
        self.profile.write_text(PROFILE.format(base=self.base), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_python(self, code, env_overrides=None, minimal_env=False):
        if minimal_env:
            # What a non-login shell hands a process: no shell profile has run,
            # so nothing here may depend on one having set a root.
            env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}
        else:
            env = os.environ.copy()
        for key in CLEARED:
            env.pop(key, None)
        env["SERVEROS_HOST_PROFILE"] = str(self.profile)
        env["PYTHONPATH"] = str(REPO_ROOT)
        env.update(env_overrides or {})
        return subprocess.run([sys.executable, "-c", code], text=True,
                              capture_output=True, env=env, check=False)

    def resolved(self, value):
        return str(Path(value).resolve())

    def test_roots_follow_the_device_profile(self):
        result = self.run_python(
            "from workengine.engine import default_state_dir, default_artifacts_dir, "
            "default_workspace_root, default_runtime_assets_root as assets\n"
            "print(default_state_dir())\nprint(default_artifacts_dir())\n"
            "print(default_workspace_root())\nprint(assets())"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), [
            self.resolved(self.base / "state/serveros"),
            self.resolved(self.base / "artifacts"),
            self.resolved(self.base / "workspace"),
            self.resolved(self.base / "runtime-assets"),
        ])

    def test_the_environment_wins_over_the_device_profile(self):
        override = self.base / "env-state"
        result = self.run_python(
            "from workengine.engine import default_state_dir; print(default_state_dir())",
            {"SERVEROS_STATE_DIR": str(override)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.resolved(override))

    def test_an_explicit_root_wins_over_both(self):
        explicit = self.base / "explicit-artifacts"
        result = self.run_python(
            "from workengine.engine import ServerOS\n"
            f"s = ServerOS(state_dir={str(self.base / 'explicit-state')!r}, "
            f"artifacts_root={str(explicit)!r})\n"
            "print(s.artifacts_root)",
            {"SERVEROS_ARTIFACTS_ROOT": str(self.base / "env-artifacts")},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.resolved(explicit))

    def test_a_role_the_device_does_not_bind_raises_rather_than_defaults(self):
        thin = self.base / "thin-profile.yaml"
        thin.write_text(
            "schema: serveros/host-profile.v1\n"
            "node:\n"
            "  roots:\n"
            f"    state_root: {self.base}/state/serveros\n",
            encoding="utf-8",
        )
        result = self.run_python(
            "from workengine.engine import default_artifacts_dir; "
            "print(default_artifacts_dir())",
            {"SERVEROS_HOST_PROFILE": str(thin)},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RootUnbound", result.stderr)
        self.assertIn("artifacts_root", result.stderr)

    def test_an_unreadable_profile_is_unbound_not_silently_empty(self):
        result = self.run_python(
            "from workengine.engine import default_state_dir; print(default_state_dir())",
            {"SERVEROS_HOST_PROFILE": str(self.base / "does-not-exist.yaml")},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RootUnbound", result.stderr)
        self.assertIn("unreadable", result.stderr)

    def test_resolution_does_not_need_a_login_shell(self):
        result = self.run_python(
            "from workengine.engine import default_state_dir; print(default_state_dir())",
            minimal_env=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), self.resolved(self.base / "state/serveros"))

    def test_no_machine_is_compiled_into_the_package(self):
        """The literal must not come back as a *value*.

        What is forbidden is a machine path that is assigned, returned, or
        compared against -- the kind of occurrence that silently relocates work
        on every other host. Prose that records the history is not a binding.
        """
        offenders = []
        for path in sorted((REPO_ROOT / "workengine").glob("*.py")):
            for text in _value_strings(path):
                for needle in ("/opt/applications", "/home/hwz", "home-hwz"):
                    if needle in text:
                        offenders.append(f"{path.name}: {needle}")
        self.assertEqual(offenders, [], "a machine-specific root is back in the source")


if __name__ == "__main__":
    unittest.main()
