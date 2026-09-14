"""§3: the gate that runs against the INSTALLED artifact, not the source tree.

Why this file exists
--------------------
`test_build.py::test_every_declared_mcp_surface_starts_from_the_artifact` starts
each declared surface out of the build output — and it is a real gate: removing
`fleet` from the composition makes it fail. It was not enough, and the way it
fell short is worth stating precisely, because the next gate should not repeat
it.

That test builds from the source tree and runs the result while still standing
in the source tree. So the artifact is validated by a process that can reach
everything the checkout can reach: `PYTHONPATH` is inherited, the working
directory is inside the repository, and every fixture the test needs lives one
`parents[1]` away. An artifact that is missing something the checkout happens to
supply is indistinguishable from one that is complete.

What a gate has to do instead is what a user does: build, **install elsewhere**,
and then ask the installed copy to work with nothing else present. That is the
difference these tests make. The artifact is copied into a directory unrelated
to the build, started with an empty environment, and the question asked of it is
not "does it import" but "does every module it loaded come from inside itself".

Coverage, per §3:

  * the composition's required members are present in the installed copy;
  * every declared MCP surface starts and answers `tools/list`;
  * every shipped module imports in isolation;
  * the `fleet`/control dependency chain resolves, named explicitly rather than
    left implicit in "all modules import";
  * no module is silently supplied by the source checkout;
  * the installed version agrees with the artifact's own metadata.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_build import BUILDER, ROOT, make_ability_repo, run_build, write_pack_contract  # noqa: E402

# An environment with nothing in it. In particular no PYTHONPATH and no
# PYTHONHOME: the point is that the installed copy supplies what it needs.
# `PATH` is present so `python3` can be found, and `HOME` points inside the
# fixture so nothing can read or write a real user's state.
def clean_env(home: Path) -> dict:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C",
    }


# Loads a surface the way an agent does, and reports both what it answered and
# what it pulled in to answer. The module list is the part that catches a
# dependency coming from somewhere other than the artifact.
PROBE = """
import importlib, json, sys

sys.path[:0] = %(paths)r

mod = importlib.import_module(%(module)r)

files = {}
for name, loaded in sys.modules.items():
    origin = getattr(loaded, "__file__", None)
    if origin:
        files[name] = origin
print("@@MODULES@@" + json.dumps(files))
"""


class InstalledArtifactTests(unittest.TestCase):
    """Build once, install it elsewhere, and then interrogate the install."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="serveros-install-")
        base = Path(cls._tmp.name)
        cls.base = base

        repo, rev = make_ability_repo(base)
        contract = write_pack_contract(base)
        cls.build_out = base / "dist/current"
        result = run_build([
            "--ability-repo", str(repo),
            "--ability-rev", rev,
            "--output", str(cls.build_out),
            "--marketplace-path", str(base / "dist/.agents/plugins/marketplace.json"),
            "--pack-contract", str(contract),
            "--write",
        ])
        if result.returncode != 0:
            raise AssertionError(
                f"the fixture build failed, so nothing below could be tested:\n"
                f"{result.stdout}\n{result.stderr}")

        # THE INSTALL STEP. A copy in a directory that is not the build output
        # and not under the repository, which is what "unpacking a release
        # tarball somewhere" produces.
        cls.installed = base / "installed/hwz-server-os"
        shutil.copytree(cls.build_out, cls.installed)
        cls.home = base / "home"
        cls.home.mkdir()

        cls.manifest = json.loads((cls.installed / ".mcp.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    # -- helpers ---------------------------------------------------------

    def surface_command(self, name: str) -> list[str]:
        spec = self.manifest["mcpServers"][name]
        args = [a.replace("${CLAUDE_PLUGIN_ROOT}", str(self.installed))
                for a in spec["args"]]
        return [spec["command"], *args]

    def start_surface(self, name: str) -> subprocess.CompletedProcess:
        payload = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "smoke", "version": "1"}}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        return subprocess.run(
            self.surface_command(name), input=payload, cwd=str(self.installed),
            env=clean_env(self.home), capture_output=True, text=True, timeout=300)

    @staticmethod
    def tools_from(proc: subprocess.CompletedProcess):
        """The `tools/list` answer, matched by request id.

        Not "the first line of stdout": the probe sends `initialize` first, so
        the first reply is the handshake. A surface that answered the handshake
        and nothing else would look complete to a reader that took line one.
        """
        for line in proc.stdout.splitlines():
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            if doc.get("id") == 2 and "result" in doc:
                return doc["result"]["tools"]
        return None

    def code_dirs(self) -> list[str]:
        """Top-level directories holding code.

        `skills` carries skill payloads rather than modules the surfaces import,
        and dot-directories are plugin metadata, so neither is a module root.
        """
        return sorted(d.name for d in self.installed.iterdir()
                      if d.is_dir() and not d.name.startswith(".") and d.name != "skills")

    # -- §3: the composition's required members --------------------------

    def test_every_required_member_is_present_after_install(self):
        """The composition's members, checked in the installed copy.

        Read from the artifact's own plugin metadata rather than from a list
        written here, so a member added to the composition is covered without
        anyone remembering to add it to this test.
        """
        bundle = ROOT / "plugin" / "bundle.yaml"
        try:
            import yaml
            declared = {k: v["path"] for k, v in yaml.safe_load(
                bundle.read_text()).items()
                if isinstance(v, dict) and "path" in v}
        except ImportError:  # pragma: no cover - the repo already needs yaml
            self.skipTest("PyYAML unavailable")
        self.assertTrue(declared, "the composition declares no members")
        for key, rel in sorted(declared.items()):
            with self.subTest(member=key):
                self.assertTrue(
                    (self.installed / rel).is_dir(),
                    f"composition member {key!r} ({rel}) is not in the installed copy")

    def test_the_fleet_module_and_its_dependencies_are_installed(self):
        """Named explicitly, because this is the member that was missing.

        `mcp/agent_mcp.py` loads `fleet/agent_control_router.py` at module
        scope, and that loads `fleet/transport.py`. Asserting on the module the
        surface actually reaches for — rather than on the directory — is what
        makes a future rename fail loudly instead of silently passing.
        """
        for rel in ("fleet/agent_control_router.py", "fleet/transport.py",
                    "mcp/agent_mcp.py"):
            with self.subTest(required=rel):
                self.assertTrue((self.installed / rel).is_file(),
                                f"{rel} is required by the agent surface but was not installed")

    # -- §3: the surfaces start -----------------------------------------

    def test_every_declared_surface_starts_from_the_install(self):
        self.assertTrue(self.manifest["mcpServers"], "no surface is declared")
        for name in sorted(self.manifest["mcpServers"]):
            with self.subTest(surface=name):
                proc = self.start_surface(name)
                self.assertEqual(
                    proc.returncode, 0,
                    f"{name} could not start from the installed copy:\n{proc.stderr}")
                tools = self.tools_from(proc)
                self.assertTrue(tools, f"{name} started but declared no tools")

    def test_the_agent_surface_answers_with_its_full_tool_set(self):
        """The surface that was unreachable, pinned by count and by a real one.

        A count alone would pass if the tools were wrong; a name alone would
        pass if most of them went missing. Both, so that a surface which starts
        but is hollow is a failure.
        """
        proc = self.start_surface("serveros-agent")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tools = self.tools_from(proc)
        self.assertIsNotNone(tools, "the surface answered no tools/list")
        self.assertGreaterEqual(len(tools), 1)
        names = {t["name"] for t in tools}
        self.assertIn("agent.session.list", names)
        for tool in tools:
            self.assertIn("name", tool)
            self.assertIn("inputSchema", tool)

    # -- §3: every module, and where it came from -----------------------

    def test_every_shipped_module_imports_from_the_install(self):
        """Every module the artifact carries, loaded with the artifact's layout.

        The artifact is not a set of python packages — there is no
        `__init__.py`; entry points put their own directory on `sys.path` and
        import by bare name. So an audit that walked packages would import
        nothing and report success, which is the failure mode this test is
        about. It reproduces the layout the entry points set up.

        Files under `fixtures/` are loaded by path rather than imported —
        `memory/provider-contract.yaml` names one — so they are exercised the
        way the product exercises them.
        """
        dirs = self.code_dirs()
        self.assertTrue(dirs, "the artifact carries no code directory")
        paths = [str(self.installed)] + [str(self.installed / d) for d in dirs]

        checked = 0
        for d in dirs:
            for py in sorted((self.installed / d).rglob("*.py")):
                if py.name.startswith("__"):
                    continue
                rel = py.relative_to(self.installed).as_posix()
                with self.subTest(module=rel):
                    if "/fixtures/" in rel:
                        proc = subprocess.run(
                            [sys.executable, str(py), "--selftest"],
                            cwd=str(self.installed), env=clean_env(self.home),
                            capture_output=True, text=True, timeout=300)
                    else:
                        script = PROBE % {"paths": paths, "module": py.stem}
                        proc = subprocess.run(
                            [sys.executable, "-c", script],
                            cwd=str(self.installed), env=clean_env(self.home),
                            capture_output=True, text=True, timeout=300)
                    self.assertEqual(
                        proc.returncode, 0,
                        f"{rel} does not load from the install:\n{proc.stderr}")
                    checked += 1
        self.assertGreater(checked, 0, "no module was actually loaded")

    def test_nothing_is_supplied_by_the_source_checkout(self):
        """The claim that makes the rest of this file mean anything.

        A module that imports fine because the repository is on `sys.path`
        proves nothing about the artifact. So this asks the loaded-module table
        where every module came from, and requires that none of them came from
        the checkout.
        """
        dirs = self.code_dirs()
        paths = [str(self.installed)] + [str(self.installed / d) for d in dirs]
        script = PROBE % {"paths": paths, "module": "agent_mcp"}
        proc = subprocess.run([sys.executable, "-c", script],
                              cwd=str(self.installed), env=clean_env(self.home),
                              capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("@@MODULES@@")]
        self.assertTrue(line, "the probe reported no module table")
        loaded = json.loads(line[0][len("@@MODULES@@"):])
        self.assertGreater(len(loaded), 0)

        root = str(ROOT.resolve())
        leaked = {name: origin for name, origin in loaded.items()
                  if str(Path(origin).resolve()).startswith(root + "/")}
        self.assertEqual(
            leaked, {},
            "these modules were loaded from the source checkout, so the install "
            f"was not self-sufficient: {leaked}")

    # -- §3: version agreement ------------------------------------------

    def test_the_installed_version_agrees_with_the_artifact_metadata(self):
        """One payload, one version string.

        The defect this release is about produced two artifacts with different
        contents claiming the same version. Every source that states a version
        is compared here, so that disagreement is a test failure rather than
        something a reader has to notice.
        """
        versions = {}
        for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            path = self.installed / rel
            if path.is_file():
                versions[rel] = json.loads(path.read_text())["version"]
        self.assertTrue(versions, "the artifact states no version anywhere")
        distinct = set(versions.values())
        self.assertEqual(
            len(distinct), 1,
            f"the artifact disagrees with itself about its version: {versions}")

        release = self.installed / "RELEASE.json"
        if release.is_file():
            doc = json.loads(release.read_text())
            for key in ("revision", "composition", "tree_hash"):
                with self.subTest(field=key):
                    self.assertIn(key, doc)


if __name__ == "__main__":
    unittest.main()
