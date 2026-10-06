"""Tests for the release/promotion path.

Hermetic: the artifact is synthetic, the runtime root is a temp directory, and
the unit and shim destinations are temp directories too, so running these never
touches the machine's real `~/.config/systemd/user` or `~/.local/bin`.

The defect these tests exist for is specific. A node whose supervisor and whose
CLI resolve different revisions is running two products at once, and nothing in
either program can see it — each one is internally consistent and points
somewhere that works. So the assertion that matters is not "promote returned 0";
it is that afterwards the unit and the shim name the same revision, and that a
rollback returns both.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts" / "release.py"

_spec = importlib.util.spec_from_file_location("serveros_release", RELEASE)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

UNIT_TEMPLATE = """\
[Unit]
Description=ServerOS node runtime (semantic controller)

[Service]
Type=simple
Environment=SERVEROS_STATE_DIR=@SERVEROS_STATE_DIR@
ExecStart=@SERVEROS_RUNTIME@/bin/serverosd
Restart=on-failure

[Install]
WantedBy=default.target
"""

CLI_SHIM = """\
#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runtime"))
import cli
if __name__ == "__main__":
    sys.exit(cli.main())
"""

CLI_MODULE = """\
import argparse
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", action="store_true")
    ap.parse_args()
    return 0
"""


def make_artifact(base: Path, marker: str, *, with_daemon: bool = True,
                  kinds: tuple = ("node-runtime",), manifest: bool = True) -> Path:
    """A minimal but structurally real artifact.

    The shim is the real three-line shim and the module it imports is real, so
    `bin/serverosctl --help` exercises the actual `bin/` <-> `runtime/`
    relationship that a revision can get wrong while both paths still exist.

    `ARTIFACT.json` is written by default because a real artifact has one: the
    build writes it, and it is what tells the release path which verifier to
    run. `manifest=False` produces the artifact of a build that predates the
    manifest, which is a state the release path still has to handle — a node
    already promoted from one can be rolled back to — so it is reachable here
    rather than only in a comment.
    """
    art = base / f"artifact-{marker}"
    (art / "bin").mkdir(parents=True)
    (art / "runtime").mkdir(parents=True)
    (art / "README.md").write_text(f"marker {marker}\n")
    (art / "runtime" / "serverosd.service").write_text(UNIT_TEMPLATE)
    (art / "runtime" / "cli.py").write_text(CLI_MODULE)
    (art / "runtime" / "daemon.py").write_text("# daemon\n")
    (art / "bin" / "serverosctl").write_text(CLI_SHIM)
    if with_daemon:
        (art / "bin" / "serverosd").write_text("# entry point\n")
    if manifest:
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps({
            "schema": release.ARTIFACT_MANIFEST_SCHEMA,
            "composition": "serveros-linux-work",
            "name": "hwz-server-os-linux-work",
            "version": "3.1.0+fixture0001",
            "cachebuster": "fixture0001",
            "kinds": list(kinds),
            "skills": [],
            "mcp_servers": [],
            "members": sorted(str(p.relative_to(art))
                              for p in art.rglob("*") if p.is_file())
                       + [release.ARTIFACT_MANIFEST],
        }, indent=2) + "\n")
    return art


def make_plugin_artifact(base: Path, marker: str, *,
                         skills: tuple = ("automated-testing", "test-management"),
                         servers: tuple = (), extra_files: tuple = (),
                         name: str = "serveros-testing",
                         cachebuster: str = "srvtest0001.fixture",
                         members: list | None = None) -> Path:
    """A minimal but structurally real PLUGIN artifact.

    Structurally real in the sense the verifier cares about: the two plugin
    manifests, the composed skills on disk, the MCP manifests when the
    composition declares servers, and a build manifest whose member list is the
    one the payload actually has.
    """
    art = base / f"plugin-{marker}"
    version = f"1.0.0+{cachebuster}"
    (art / ".claude-plugin").mkdir(parents=True)
    (art / ".codex-plugin").mkdir(parents=True)
    for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        (art / rel).write_text(json.dumps({
            "name": name, "version": version, "description": "fixture",
            "author": {"name": "test"}, "skills": "./skills/", "license": "MIT",
        }, indent=2) + "\n")
    for skill in skills:
        (art / "skills" / skill).mkdir(parents=True)
        (art / "skills" / skill / "SKILL.md").write_text(f"# {skill}\n")
    if servers:
        for rel in (".mcp.json", ".codex-mcp.json"):
            (art / rel).write_text(json.dumps(
                {"mcpServers": {s: {"command": "python3", "args": [f"bin/{s}"]}
                                for s in servers}}, indent=2) + "\n")
    (art / "README.md").write_text(f"marker {marker}\n")
    # The member list is taken BEFORE `extra_files` is written, which is the
    # only order that makes the field mean anything: a list computed afterwards
    # would declare whatever happened to be there, and the check it feeds would
    # be a tautology.
    if members is None:
        members = sorted(str(p.relative_to(art))
                         for p in art.rglob("*") if p.is_file())
        members.append(release.ARTIFACT_MANIFEST)
    for rel in extra_files:
        (art / rel).parent.mkdir(parents=True, exist_ok=True)
        (art / rel).write_text("undeclared\n")
    (art / release.ARTIFACT_MANIFEST).write_text(json.dumps({
        "schema": release.ARTIFACT_MANIFEST_SCHEMA,
        "composition": name,
        "name": name,
        "version": version,
        "cachebuster": cachebuster,
        "kinds": ["plugin"],
        "skills": sorted(skills),
        "mcp_servers": sorted(servers),
        "members": sorted(members),
    }, indent=2) + "\n")
    return art


def run_release(args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(RELEASE), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          env=env)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        # Seeded, because a node whose state root does not exist cannot start —
        # the daemon exits 2 without a registry under it — and `promote` now
        # refuses that rather than reporting success. The fixture models a
        # runnable node; the test below covers the unseeded one.
        self.state_dir.mkdir(parents=True)
        self.composition = "serveros-linux-work"
        self.comp_root = self.runtime_root / self.composition

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra: str) -> list[str]:
        return [
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(self.state_dir),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
            *extra,
        ]

    def promote(self, artifact: Path, revision: str, *extra: str, env: dict | None = None):
        return run_release(["promote", "--artifact", str(artifact),
                            "--revision", revision, *self.args(*extra)], env=env)

    def status(self):
        return run_release(["status", *self.args()])

    def test_promote_points_the_unit_and_the_cli_at_one_revision(self):
        """The assertion the whole tool exists for.

        Each entry point can be individually correct and still leave the node
        running two revisions. Only comparing them catches it.
        """
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)

        unit = (self.unit_dir / "serverosd.service").read_text()
        self.assertIn(str(self.comp_root / "current") + "/bin/serverosd", unit)
        self.assertIn(str(self.state_dir), unit)

        shim = (self.bin_dir / "serverosctl").resolve()
        self.assertEqual(shim, (self.comp_root / "current" / "bin" / "serverosctl").resolve())
        self.assertEqual(self.status().returncode, 0,
                         f"status reported divergence after a promote:\n{self.status().stdout}")

    def test_promote_renders_every_placeholder(self):
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        unit = (self.unit_dir / "serverosd.service").read_text()
        self.assertNotIn("@SERVEROS_RUNTIME@", unit)
        self.assertNotIn("@SERVEROS_STATE_DIR@", unit)
        self.assertIn(str(self.comp_root / "current"), unit)

    def wake_artifact(self, marker: str) -> Path:
        art = make_artifact(self.base, marker)
        for name in (release.WAKE_SERVICE_NAME, release.WAKE_TIMER_NAME,
                     release.WAKE_PARENT_NAME):
            (art / "runtime" / name).write_bytes((ROOT / "runtime" / name).read_bytes())
        launcher = art / release.WAKE_PARENT_TEMPLATE
        launcher.write_text(launcher.read_text() + f"\n# revision {marker}\n")
        return art

    def install_wake(self, *extra: str):
        return run_release(["continuation-wake", "--budget", "900",
                            *self.args(*extra)])

    def test_status_checks_entry_points_not_current_mentioned_in_comments(self):
        self.assertEqual(self.promote(make_artifact(self.base, "a"), "rev-a").returncode, 0)
        unit = self.unit_dir / release.UNIT_NAME
        original = unit.read_text()
        unit.write_text(original.replace(
            f"ExecStart={self.comp_root}/current/bin/serverosd",
            f"# expected {self.comp_root}/current/bin/serverosd\n"
            f"ExecStart={self.comp_root}/revisions/rev-a/bin/serverosd"))
        self.assertEqual(self.status().returncode, 1)
        unit.write_text(original)
        shim = self.bin_dir / "serverosctl"
        shim.unlink()
        shim.symlink_to(self.comp_root / "current-old" / "bin" / "serverosctl")
        self.assertEqual(self.status().returncode, 1)

    def test_status_detects_launcher_drift_after_promotion_and_rollback(self):
        self.assertEqual(self.promote(self.wake_artifact("a"), "rev-a").returncode, 0)
        self.assertEqual(self.install_wake().returncode, 0)
        self.assertEqual(self.status().returncode, 0)
        promoted = self.promote(self.wake_artifact("b"), "rev-b")
        self.assertEqual(promoted.returncode, 0, promoted.stderr)
        self.assertIn("continuation-wake", promoted.stdout)
        status = self.status()
        self.assertEqual(status.returncode, 1, status.stdout)
        self.assertIn("launcher differs", status.stdout)
        self.assertEqual(self.install_wake().returncode, 0)
        self.assertEqual(self.status().returncode, 0)
        rolled_back = run_release(["rollback", *self.args()])
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertIn("continuation-wake", rolled_back.stdout)
        self.assertEqual(self.status().returncode, 1)
        self.assertEqual(self.install_wake().returncode, 0)
        self.assertEqual(self.status().returncode, 0)

    def test_status_detects_missing_launcher_and_wrong_wake_runtime(self):
        self.assertEqual(self.promote(self.wake_artifact("a"), "rev-a").returncode, 0)
        self.assertEqual(self.install_wake().returncode, 0)
        launcher = self.bin_dir / release.WAKE_PARENT_NAME
        launcher.unlink()
        self.assertEqual(self.status().returncode, 1)
        self.assertEqual(self.install_wake().returncode, 0)
        launcher.chmod(0o644)
        self.assertEqual(self.status().returncode, 1)
        launcher.chmod(0o755)
        unit = self.unit_dir / release.WAKE_SERVICE_NAME
        unit.write_text(unit.read_text().replace(
            f"ExecStart={self.comp_root}/current/bin/serverosctl",
            f"ExecStart={self.comp_root}/revisions/rev-a/bin/serverosctl"))
        self.assertEqual(self.status().returncode, 1)

    def test_status_accepts_custom_parent_but_checks_it_exists(self):
        self.assertEqual(self.promote(self.wake_artifact("a"), "rev-a").returncode, 0)
        parent = self.base / "custom-parent"
        parent.write_text("#!/bin/sh\nexit 0\n")
        parent.chmod(0o755)
        self.assertEqual(self.install_wake("--parent", str(parent)).returncode, 0)
        self.assertEqual(self.status().returncode, 0)
        parent.unlink()
        self.assertEqual(self.status().returncode, 1)

    def test_the_default_state_dir_does_not_come_from_the_environment(self):
        """The regression, tested under the condition that produced it.

        The default used to be `SERVEROS_STATE_DIR`, else `DATA_ROOT`, else
        `$HOME`, with `state/serveros` appended. `DATA_ROOT` is a documented
        root rather than an exported variable, so on an ordinary shell the
        middle branch missed and the answer became `$HOME/state/serveros` —
        which does not exist. Two shells exported differently produced two
        different units from the same command.

        So this runs the resolver twice with different environments and requires
        the same answer, which is what makes the value a property of the node
        rather than of whoever typed the command.
        """
        probe = (
            "import importlib.util;"
            f"s=importlib.util.spec_from_file_location('r',{str(RELEASE)!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.default_state_dir())"
        )

        def resolve(**overrides) -> str:
            env = dict(os.environ)
            for key in ("SERVEROS_STATE_DIR", "DATA_ROOT"):
                env.pop(key, None)
            env.update(overrides)
            proc = subprocess.run([sys.executable, "-c", probe],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return proc.stdout.strip()

        bare = resolve()
        # The old expression appended `state/serveros` to `$HOME`, and the
        # check for it was "the answer must not be under $HOME". That check was
        # a fact about the node it was written on. On THIS node the data root
        # IS the home directory — `fleet/profiles.yaml` binds `data_root` and
        # `home` to the same path — so the correct answer is under `$HOME` and
        # the prefix test went red on the right value.
        #
        # What separates the bug from the fix is not the string; it is where
        # the string came from. So the assertion is that the answer is the
        # resolver's, which is what §18 asks for and what catches a
        # `release.py` that quietly stops delegating.
        self.assertEqual(bare, self.resolved_state_root())

        # An explicit DATA_ROOT IS honoured, and that distinction is the point:
        # a stated root is a statement about the node, while an absent one used
        # to become a guess. `paths.py` owns that call.
        self.assertEqual(resolve(DATA_ROOT="/tmp/somewhere-else"),
                         "/tmp/somewhere-else/state/serveros")

        # `SERVEROS_STATE_DIR` named the DAEMON's environment, not the
        # installer's input. Reading it here let a variable set for one purpose
        # decide where an install pointed, so it is no longer consulted.
        self.assertEqual(bare, resolve(SERVEROS_STATE_DIR="/tmp/ignored"))

    def resolved_state_root(self) -> str:
        """What `runtime/paths.py` says the state root is, in the bare env.

        The same environment `resolve()` uses, so the two answers are
        comparable: no `SERVEROS_STATE_DIR`, no `DATA_ROOT`.
        """
        probe = (
            "import importlib.util;"
            f"s=importlib.util.spec_from_file_location('p',"
            f"{str(ROOT / 'runtime' / 'paths.py')!r});"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
            "print(m.Paths().root('state'))"
        )
        env = dict(os.environ)
        for key in ("SERVEROS_STATE_DIR", "DATA_ROOT"):
            env.pop(key, None)
        proc = subprocess.run([sys.executable, "-c", probe],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_promote_refuses_a_state_root_that_does_not_exist(self):
        """A node pointed at a missing state root cannot start, so promoting it
        is not a success. The daemon exits 2 looking for a registry there, and
        this used to return 0 with the unit already written."""
        art = make_artifact(self.base, "a")
        missing = self.base / "never-seeded"
        proc = run_release([
            "promote", "--artifact", str(art), "--revision", "rev-a",
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(missing),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
        ])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("state root does not exist", proc.stderr)
        # And nothing was pointed anywhere: the failure is before the swap.
        self.assertFalse((self.comp_root / "current").exists())
        self.assertFalse((self.unit_dir / "serverosd.service").exists())

    def test_verification_does_not_write_into_the_artifact(self):
        """Promoting must not mutate what it promotes.

        `verify_revision` runs the revision's own entry point to prove the shim
        resolves the runtime it ships beside, and that import makes Python write
        `__pycache__` next to the modules. The tree hash is computed afterwards,
        so the node recorded an identity for files the verifier had just made:
        the same code hashed to 04e9956b clean and d6ea08ee promoted, and both
        numbers were correct.
        """
        art = make_artifact(self.base, "a")
        before = release.fingerprint(art)
        # Run with PYTHONDONTWRITEBYTECODE removed. Without this the test
        # inherits whatever the runner was launched with, and under
        # `PYTHONDONTWRITEBYTECODE=1` the mutation cannot happen however broken
        # the release tool is — the first version of this test was written that
        # way and passed against the defect it was written to catch. A test for
        # "the verifier must not write" has to be run in a world where writing
        # is allowed, so it depends on release.py and not on the ambient shell.
        env = {k: v for k, v in os.environ.items()
               if k != "PYTHONDONTWRITEBYTECODE"}
        self.assertEqual(self.promote(art, "rev-a", env=env).returncode, 0)

        staged = self.comp_root / "revisions" / "rev-a"
        self.assertEqual(list(staged.rglob("__pycache__")), [],
                         "promotion wrote bytecode into the staged revision")
        self.assertEqual(release.fingerprint(art), before,
                         "promotion mutated the artifact it was given")
        # The recorded identity is the artifact's, not the verifier's.
        self.assertEqual(
            json.loads((staged / "RELEASE.json").read_text())["tree_hash"], before)

    def test_the_recorded_hash_covers_the_artifact_not_the_record_of_it(self):
        """Which tree the recorded number is a hash of, pinned.

        `RELEASE.json` sits inside the revision it describes and carries the
        tree hash, so it cannot be covered by that hash — it is written after
        the hash is computed. Forced, not chosen. But the natural check gets it
        wrong: hash the staged directory and you get a different number from
        the one recorded in that same directory, which reads as tampering on a
        revision that is perfectly correct. Found exactly that way.

        So both halves are asserted: the artifact's hash is the recorded one,
        and the staged directory's is not. If a later change moved the manifest
        out of the revision, the second assertion fails and says so, which is
        the moment to update the docstring rather than leave a reader misled.
        """
        art = make_artifact(self.base, "a")
        before = release.fingerprint(art)
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)

        staged = self.comp_root / "revisions" / "rev-a"
        recorded = json.loads((staged / release.TREE_MANIFEST).read_text())["tree_hash"]
        self.assertEqual(recorded, before,
                         "the recorded hash is not the artifact's")
        self.assertNotEqual(
            release.fingerprint(staged), recorded,
            "the tree hash covers its own record, which no file can do")

    def test_rollback_returns_both_entry_points_to_the_previous_revision(self):
        a, b = make_artifact(self.base, "a"), make_artifact(self.base, "b")
        self.assertEqual(self.promote(a, "rev-a").returncode, 0)
        self.assertEqual(self.promote(b, "rev-b").returncode, 0)
        self.assertEqual(self.status().returncode, 0)

        self.assertEqual(
            run_release(["rollback", *self.args()]).returncode, 0)
        self.assertEqual(self.status().returncode, 0,
                         f"rollback left the node divergent:\n{self.status().stdout}")
        # Resolution, not text. The unit names `current` and keeps naming it
        # across every promotion and rollback; that stability is what lets a
        # rollback be one symlink swap instead of a re-render that could fail
        # halfway. So what a rollback must be judged by is where `current`
        # ends up, not which revision string the unit happens to contain.
        self.assertEqual(release.current_revision(self.comp_root), "rev-a")
        self.assertIn(str(self.comp_root / "current"),
                      (self.unit_dir / "serverosd.service").read_text())

    def test_rollback_keeps_the_revision_it_replaced(self):
        """A rollback is an exchange, not a discard.

        If rolling back deleted the revision it moved away from, a bad rollback
        would be unrecoverable — which is the one moment the tool is being used
        precisely because something is already wrong.
        """
        a, b = make_artifact(self.base, "a"), make_artifact(self.base, "b")
        self.promote(a, "rev-a")
        self.promote(b, "rev-b")
        self.assertTrue((self.comp_root / "revisions" / "rev-a").is_dir())
        run_release(["rollback", *self.args()])
        self.assertTrue((self.comp_root / "revisions" / "rev-b").is_dir(),
                        "rollback removed the revision it rolled back from")
        self.assertTrue((self.comp_root / "revisions" / "rev-a").is_dir())

    def test_promote_refuses_an_artifact_with_no_daemon(self):
        art = make_artifact(self.base, "a", with_daemon=False)
        result = self.promote(art, "rev-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bin/serverosd", result.stderr)
        self.assertFalse((self.comp_root / "current").is_symlink(),
                         "a refused artifact left the node pointed at something")

    def test_a_dry_run_writes_nothing(self):
        """A dry run that stages is a real run with the last step removed.

        Found the direct way: `--dry-run` staged a revision, and the real
        promotion that followed refused its own leftovers with "already
        staged". The check is a filesystem assertion rather than a return-code
        one, because a dry run returning 0 while having written is exactly the
        failure.
        """
        art = make_artifact(self.base, "a")
        before = sorted(p.name for p in self.runtime_root.rglob("*")) if self.runtime_root.exists() else []
        result = self.promote(art, "rev-a", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("would promote", result.stdout)

        after = sorted(p.name for p in self.runtime_root.rglob("*")) if self.runtime_root.exists() else []
        self.assertEqual(before, after, "a dry run created files")
        self.assertFalse((self.comp_root / "revisions").exists(),
                         "a dry run staged a revision")
        self.assertFalse((self.unit_dir / "serverosd.service").exists(),
                         "a dry run installed a unit")

    def test_a_dry_run_then_a_real_promote_succeeds(self):
        """The sequence the bug broke, asserted end to end."""
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a", "--dry-run").returncode, 0)
        self.assertEqual(self.promote(art, "rev-a").returncode, 0,
                         "a real promote failed after a dry run of the same revision")
        self.assertEqual(release.current_revision(self.comp_root), "rev-a")

    def test_promote_refuses_to_restage_a_revision_without_replace(self):
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        again = self.promote(art, "rev-a")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already staged", again.stderr)

    def test_a_failed_verify_leaves_current_untouched(self):
        """Verification happens while `current` still points at what is running."""
        good = make_artifact(self.base, "good")
        self.assertEqual(self.promote(good, "rev-good").returncode, 0)

        broken = make_artifact(self.base, "broken")
        # A shim whose runtime moved out from under it: both paths exist, and
        # the entry point still cannot run. This is the failure an existence
        # check cannot see.
        (broken / "runtime" / "cli.py").unlink()

        result = self.promote(broken, "rev-broken")
        self.assertEqual(result.returncode, 1)
        self.assertIn("REFUSED", result.stderr)
        self.assertEqual(release.current_revision(self.comp_root), "rev-good")

    def test_staged_revision_is_immutable_once_promoted(self):
        """Swapping `current` must not move or rewrite the revision itself."""
        art = make_artifact(self.base, "a")
        self.promote(art, "rev-a")
        rev = self.comp_root / "revisions" / "rev-a"
        before = release.fingerprint(rev)
        self.promote(make_artifact(self.base, "b"), "rev-b")
        self.assertEqual(release.fingerprint(rev), before)
        manifest = json.loads((rev / release.TREE_MANIFEST).read_text())
        self.assertEqual(manifest["revision"], "rev-a")

    def test_fingerprint_reproduces_the_recorded_tree_hash_recipe(self):
        """The hash must be the one the release records quote.

        Two hashes of the same directory that cannot be compared are worse than
        no hash, because a reader takes the agreement of two numbers as
        verification. So the shell recipe the records use is run here, and the
        Python must match it exactly — including `find`'s `./` prefix and
        `sha256sum`'s two-space separator.
        """
        art = make_artifact(self.base, "a")
        shell = subprocess.run(
            "find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum",
            shell=True, cwd=art, stdout=subprocess.PIPE, text=True,
            env={**os.environ, "LC_ALL": "C"})
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(release.fingerprint(art), shell.stdout.split()[0])

    def test_bytecode_is_not_part_of_the_payload(self):
        """A revision's hash must survive the revision being run.

        The staged directory is what the node executes out of, so the first
        start writes `__pycache__` into the tree whose hash the manifest quotes.
        If that changed the number, every healthy node would report itself as
        tampered with on the second measurement — which is what happened here on
        2026-09-26, against a revision that differed from its record by 34 `.pyc`
        files and by nothing else.

        The departure from the quoted shell pipeline is pinned rather than
        assumed: with the bytecode excluded, `find` produces the same number the
        Python does. So the recipe is unchanged for the payload, and the payload
        is what the records were hashed over.
        """
        art = make_artifact(self.base, "a")
        before = release.fingerprint(art)
        cache = art / "runtime" / "__pycache__"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "daemon.cpython-312.pyc").write_bytes(b"\x00derived\n")
        (art / "stray.pyc").write_bytes(b"\x00derived\n")
        self.assertEqual(release.fingerprint(art), before)
        # The literal pipeline, restricted to the files that ARE the payload.
        shell = subprocess.run(
            "find . -type f "
            "! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' "
            "-print0 | sort -z | xargs -0 sha256sum | sha256sum",
            shell=True, cwd=art, stdout=subprocess.PIPE, text=True,
            env={**os.environ, "LC_ALL": "C"})
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(release.fingerprint(art), shell.stdout.split()[0])

    def test_fingerprint_changes_when_the_tree_changes(self):
        art = make_artifact(self.base, "a")
        before = release.fingerprint(art)
        (art / "runtime" / "extra.py").write_text("# new\n")
        self.assertNotEqual(release.fingerprint(art), before)


ALERT_SERVICE_TEMPLATE = """\
[Unit]
Description=ServerOS alert delivery

[Service]
Type=oneshot
Environment=SERVEROS_STATE_DIR=@SERVEROS_STATE_DIR@
ExecStart=@SERVEROS_RUNTIME@/bin/serverosctl alert sync --conversation @SERVEROS_ALERT_CONVERSATION@
TimeoutStartSec=120

[Install]
WantedBy=default.target
"""

ALERT_TIMER_TEMPLATE = """\
[Unit]
Description=Say what ServerOS has found

[Timer]
OnCalendar=*-*-* *:4/5:00
Persistent=false
Unit=serveros-alert-sync.service

[Install]
WantedBy=timers.target
"""


class TheDeliveryWorkerIsInstalledByASeparateAct(unittest.TestCase):
    """§W's runtime, and the one property that keeps it from being a second
    daemon: it is NOT part of `promote`.

    That split is the whole design. `promote` places a revision and points the
    node at it, and must keep working on a node whose operator has not yet
    decided where alerts should go — otherwise every promotion depends on
    knowing a chat id, which is the same shape as blocking a core release on a
    delivery question. So the worker is one explicit act, and the act is the
    one a reboot does not undo.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        self.state_dir.mkdir(parents=True)
        self.composition = "serveros-linux-work"
        self.comp_root = self.runtime_root / self.composition

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra: str) -> list[str]:
        return [
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(self.state_dir),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
            *extra,
        ]

    def promote(self, art, revision, *extra):
        return run_release(["promote", "--artifact", str(art),
                            "--revision", revision, *self.args(*extra)])

    def worker(self, *extra: str):
        return run_release(["alert-worker", "--conversation", "oc_test_chat",
                            *self.args(*extra)])

    def artifact(self, *, with_worker=True, service=ALERT_SERVICE_TEMPLATE):
        art = make_artifact(self.base, "a")
        if with_worker:
            (art / "runtime" / "serveros-alert-sync.service").write_text(service)
            (art / "runtime" / "serveros-alert-sync.timer").write_text(
                ALERT_TIMER_TEMPLATE)
        return art

    def test_promote_alone_installs_the_daemon_and_never_the_worker(self):
        art = self.artifact()
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        installed = sorted(p.name for p in self.unit_dir.iterdir())
        self.assertEqual(installed, ["serverosd.service"],
                         "promote must not arm a timer that sends cards to a "
                         "chat nobody chose")

    def test_an_artifact_without_the_worker_still_promotes(self):
        """§24: the core must start without the provider. An artifact that
        omits the delivery worker is a runnable node, and refusing it would
        make the delivery question a precondition for every release."""
        self.assertEqual(self.promote(self.artifact(with_worker=False),
                                      "rev-a").returncode, 0)

    def test_the_worker_is_installed_from_the_promoted_revision(self):
        art = self.artifact()
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        self.assertEqual(self.worker("--no-enable").returncode, 0)

        service = (self.unit_dir / "serveros-alert-sync.service").read_text()
        self.assertIn(str(self.comp_root / "current") + "/bin/serverosctl", service)
        self.assertIn("alert sync", service)
        self.assertIn("oc_test_chat", service)
        self.assertNotIn("@", service.replace("ExecStart", ""),
                         "the installed unit must carry no placeholder")
        timer = (self.unit_dir / "serveros-alert-sync.timer").read_text()
        self.assertIn("Unit=serveros-alert-sync.service", timer)

    def test_the_worker_refuses_before_anything_is_promoted(self):
        proc = self.worker()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("nothing is promoted", proc.stderr)
        self.assertFalse(self.unit_dir.exists())

    def test_the_worker_refuses_an_artifact_built_before_it_existed(self):
        self.assertEqual(self.promote(self.artifact(with_worker=False),
                                      "rev-a").returncode, 0)
        proc = self.worker()
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing from revision", proc.stderr)
        self.assertEqual(sorted(p.name for p in self.unit_dir.iterdir()),
                         ["serverosd.service"])

    def test_a_dry_run_writes_no_unit(self):
        art = self.artifact()
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        before = sorted(p.name for p in self.unit_dir.iterdir())
        proc = self.worker("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would install", proc.stdout)
        self.assertIn("oc_test_chat", proc.stdout)
        self.assertEqual(sorted(p.name for p in self.unit_dir.iterdir()), before)

    def test_a_worker_template_with_a_placeholder_nobody_fills_is_refused(self):
        """The stray-placeholder hole, and why it is worth a test.

        `render_unit` fills three names and checks for those three, so a fourth
        placeholder added to a unit and not to the renderer would be installed
        as the literal — and systemd treats `@...@` as text, so the unit would
        run. That is a unit whose ExecStart reads correctly to everybody who
        does not know what `@SERVEROS_NODE_ROOT@` was supposed to be.
        """
        stray = ALERT_SERVICE_TEMPLATE.replace(
            "TimeoutStartSec=120", "TimeoutStartSec=@SERVEROS_TIMEOUT@")
        art = self.artifact(service=stray)
        proc = self.promote(art, "rev-a")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("@SERVEROS_TIMEOUT@", proc.stderr)
        self.assertFalse((self.comp_root / "current").exists(),
                         "the refusal is before the swap")

    def test_the_renderer_refuses_a_destination_it_was_not_given(self):
        with self.assertRaises(ValueError) as caught:
            release.render_unit(ALERT_SERVICE_TEMPLATE, self.base, self.state_dir)
        self.assertIn(release.CONVERSATION_PLACEHOLDER, str(caught.exception))
        # And the same template renders once a person has said where alerts go.
        text = release.render_unit(ALERT_SERVICE_TEMPLATE, self.base,
                                   self.state_dir, conversation="oc_real")
        self.assertIn("--conversation oc_real", text)
        self.assertNotIn("@", text.replace("TimeoutStartSec", ""))

    def test_the_units_this_tree_ships_are_the_ones_the_installer_names(self):
        """The templates are optional to `verify_revision` — it checks them
        WHEN PRESENT — so a rename would make the check quietly stop running
        rather than fail. This is the join between the two."""
        for name in (release.ALERT_SERVICE_NAME, release.ALERT_TIMER_NAME):
            self.assertTrue((ROOT / "runtime" / name).is_file(),
                            f"runtime/{name} is gone, and `verify_revision` "
                            f"would not have noticed")




class ArtifactKinds(unittest.TestCase):
    """§9: one lifecycle, two faces, and the kind chosen from a declaration.

    The defect these tests exist for: `promote` required `bin/serverosd` of
    every artifact, so the node's entry point decided whether ANYTHING could be
    released. That is a fact about one kind standing in for a fact about all of
    them, and it is why a plugin — which has no daemon and never will — could
    not be released through the path that already implements candidate,
    verify, `revisions/<revision>`, `current`, `previous`, receipt, rollback.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        self.state_dir.mkdir(parents=True)
        self.composition = "serveros-testing"
        self.comp_root = self.runtime_root / self.composition

    def tearDown(self):
        self._tmp.cleanup()

    def args(self, *extra: str) -> list[str]:
        return [
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(self.state_dir),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
            *extra,
        ]

    def promote(self, artifact: Path, revision: str, *extra: str):
        return run_release(["promote", "--artifact", str(artifact),
                            "--revision", revision, *self.args(*extra)])

    def status(self, *extra: str):
        return run_release(["status", *self.args(*extra)])

    # -- the kind is declared, and both faces share the lifecycle ------------

    def test_a_plugin_releases_through_the_same_lifecycle(self):
        """The whole point: candidate, verify, revision, current, receipt."""
        art = make_plugin_artifact(self.base, "t")
        result = self.promote(art, "srvtest-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kind=plugin (declared)", result.stdout)

        rev = self.comp_root / "revisions" / "srvtest-a"
        self.assertTrue(rev.is_dir())
        self.assertTrue((self.comp_root / "current").is_symlink())
        receipt = json.loads((rev / release.TREE_MANIFEST).read_text())
        self.assertEqual(receipt["kind"], "plugin")
        self.assertEqual(receipt["kind_basis"], "declared")
        self.assertIsNotNone(receipt["tree_hash"])

    def test_a_plugin_installs_nothing_outside_the_revision_directory(self):
        """No unit and no shim, because a plugin is not a daemon.

        Asserted as absence in the two real destinations rather than as a
        missing string in the output: a promotion that printed "plugin" and
        still wrote a unit would pass the weaker check.
        """
        art = make_plugin_artifact(self.base, "t")
        self.assertEqual(self.promote(art, "srvtest-a").returncode, 0)
        self.assertFalse((self.unit_dir / release.UNIT_NAME).exists())
        self.assertFalse((self.bin_dir / "serverosctl").exists())

    def test_the_shim_is_still_written_for_the_node_face(self):
        """The other half, so the test above cannot pass by writing nothing."""
        art = make_artifact(self.base, "n")
        self.assertEqual(self.promote(art, "rev-n").returncode, 0)
        self.assertTrue((self.unit_dir / release.UNIT_NAME).is_file())
        self.assertTrue((self.bin_dir / "serverosctl").is_symlink())

    def test_a_payload_that_is_both_faces_is_asked_which_one(self):
        """Core and linux-work are a node runtime AND a plugin. One payload,
        two releases, and a default would answer the caller's question."""
        art = make_plugin_artifact(self.base, "both")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["kinds"] = ["node-runtime", "plugin"]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")

        result = self.promote(art, "both-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("more than one kind", result.stderr)
        self.assertFalse((self.comp_root / "current").exists())

    def test_kind_narrows_and_does_not_override(self):
        """`--kind node-runtime` on a plugin-only payload must refuse.

        The flag says which release this is, not which rules to apply: letting
        it name a kind the composition does not claim would be a name
        special-case entered from the command line instead of from the payload.
        """
        art = make_plugin_artifact(self.base, "t")
        result = self.promote(art, "srvtest-a", "--kind", "node-runtime")
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing bin/serverosd", result.stderr)
        self.assertFalse((self.comp_root / "current").exists())

    def test_status_reports_the_kind_and_agrees_for_a_plugin(self):
        """A correct plugin install has no unit. Calling that DIVERGENT would
        report a healthy installation as a fault."""
        self.assertEqual(
            self.promote(make_plugin_artifact(self.base, "t"), "srvtest-a").returncode, 0)
        result = self.status()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("kind              plugin (recorded)", result.stdout)
        self.assertIn("agreement         AGREE", result.stdout)
        self.assertIn("n/a (plugin face)", result.stdout)

    def test_the_kind_recorded_at_promotion_is_the_one_rollback_uses(self):
        """Rollback cannot ask the artifact it is returning to — it has to read
        the receipt, which is why the receipt records the kind at all."""
        self.assertEqual(self.promote(make_artifact(self.base, "n"), "rev-n").returncode, 0)
        self.assertEqual(
            self.promote(make_plugin_artifact(self.base, "t"), "srvtest-b").returncode, 0)

        result = run_release(["rollback", *self.args()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kind=node-runtime", result.stdout)
        self.assertEqual(release.current_revision(self.comp_root), "rev-n")
        self.assertTrue((self.unit_dir / release.UNIT_NAME).is_file())

    # -- a revision that predates the manifest -------------------------------

    def test_a_revision_staged_before_the_manifest_resolves_structurally(self):
        """A node already promoted from a manifest-less artifact is one a
        rollback can still reach. It is resolved by STRUCTURE and says so —
        never by the revision's name."""
        art = make_artifact(self.base, "legacy", manifest=False)
        self.assertFalse((art / release.ARTIFACT_MANIFEST).exists())
        result = self.promote(art, "legacy-a", "--kind", "node-runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kind=node-runtime (chosen)", result.stdout)

        # And with no --kind at all, the structure alone settles it.
        self.assertEqual(
            self.promote(make_artifact(self.base, "legacy2", manifest=False),
                         "legacy-b").returncode, 0)

    def test_an_artifact_that_fits_neither_verifier_is_refused_by_name(self):
        art = make_plugin_artifact(self.base, "broken")
        (art / ".claude-plugin" / "plugin.json").unlink()
        (art / release.ARTIFACT_MANIFEST).unlink()
        result = self.promote(art, "broken-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot tell what kind", result.stderr)
        self.assertFalse((self.comp_root / "current").exists())

    # -- the plugin verifier's findings -------------------------------------

    def _refuses(self, art: Path, revision: str, needle: str):
        result = self.promote(art, revision)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(needle, result.stderr)
        self.assertFalse((self.comp_root / "current").exists(),
                         "a refused artifact left the composition pointed at something")
        return result

    def test_a_manifest_naming_another_artifact_is_refused(self):
        art = make_plugin_artifact(self.base, "wrongname")
        plugin = json.loads((art / ".claude-plugin/plugin.json").read_text())
        plugin["name"] = "something-else"
        (art / ".claude-plugin/plugin.json").write_text(json.dumps(plugin, indent=2) + "\n")
        self._refuses(art, "wrong-a", "names 'something-else'")

    def test_a_version_without_the_declared_cachebuster_is_refused(self):
        art = make_plugin_artifact(self.base, "nocache")
        for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            plugin = json.loads((art / rel).read_text())
            plugin["version"] = "1.0.0+someone-elses-cache"
            (art / rel).write_text(json.dumps(plugin, indent=2) + "\n")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["version"] = "1.0.0+someone-elses-cache"
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        self._refuses(art, "nocache-a", "does not end with the declared cachebuster")

    def test_a_composed_skill_with_no_payload_is_refused(self):
        art = make_plugin_artifact(self.base, "noskill")
        (art / "skills" / "test-management" / "SKILL.md").unlink()
        self._refuses(art, "noskill-a", "has no skills/test-management/SKILL.md")

    def test_mcp_declarations_that_do_not_match_the_composition_are_refused(self):
        art = make_plugin_artifact(self.base, "mcp", servers=("serveros-node",))
        mcp = json.loads((art / ".mcp.json").read_text())
        mcp["mcpServers"]["serveros-extra"] = {"command": "python3", "args": ["bin/x"]}
        (art / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n")
        self._refuses(art, "mcp-a", "declares MCP servers")

    def test_a_file_the_build_did_not_produce_is_refused(self):
        art = make_plugin_artifact(self.base, "extra",
                                   extra_files=("skills/leftover/NOTES.md",))
        self._refuses(art, "extra-a", "the build did not produce")

    def test_a_payload_with_no_member_list_is_refused(self):
        """Not "written before the manifest existed": a manifest that omits the
        list cannot say what its own payload is, and the file check would have
        nothing to compare against."""
        art = make_plugin_artifact(self.base, "nolist")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        del manifest["members"]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        self._refuses(art, "nolist-a", "records no member list")

    def test_an_unknown_kind_is_refused_rather_than_released_unverified(self):
        art = make_plugin_artifact(self.base, "unknown")
        manifest = json.loads((art / release.ARTIFACT_MANIFEST).read_text())
        manifest["kinds"] = ["service"]
        (art / release.ARTIFACT_MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        result = self.promote(art, "unknown-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown artifact kind", result.stderr)


class StagingIsNotADeletionOpportunity(unittest.TestCase):
    """§5: what may be done to a leftover `.staging-<revision>` is decided by
    what the leftover IS.

    The rule this replaces was `if staging.exists(): shutil.rmtree(staging)`.
    It was wrong for the reason the rest of this file exists: a `.staging-*`
    directory is not evidence of a failed run of the command that is running
    now. Measured on this machine, all three real ones were still referenced —
    one named by a live marketplace descriptor, one matching the revision the
    `previous` symlink points at — and the old line would have deleted any of
    them without looking.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.runtime_root = self.base / "runtime-assets"
        self.unit_dir = self.base / "systemd-user"
        self.bin_dir = self.base / "bin"
        self.state_dir = self.base / "state/serveros"
        self.state_dir.mkdir(parents=True)
        self.composition = "serveros-linux-work"
        self.comp_root = self.runtime_root / self.composition
        self.revisions = self.comp_root / "revisions"
        self.revisions.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def promote(self, artifact: Path, revision: str):
        return run_release([
            "promote", "--artifact", str(artifact), "--revision", revision,
            "--runtime-root", str(self.runtime_root),
            "--composition", self.composition,
            "--state-dir", str(self.state_dir),
            "--unit-dir", str(self.unit_dir),
            "--bin-dir", str(self.bin_dir),
            "--no-reload",
        ])

    def test_a_partial_copy_of_this_artifact_is_resumed(self):
        """The one case where removing it is this tool's own business: the same
        identity, left behind by an interrupted run of this same command."""
        art = make_artifact(self.base, "a")
        leftover = self.revisions / ".staging-rev-a"
        leftover.mkdir(parents=True)
        (leftover / release.ARTIFACT_MANIFEST).write_text(
            (art / release.ARTIFACT_MANIFEST).read_text())
        (leftover / "half-written").write_text("interrupted\n")

        result = self.promote(art, "rev-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        rev = self.revisions / "rev-a"
        self.assertTrue((rev / "bin" / "serverosd").is_file())
        self.assertFalse((rev / "half-written").exists(),
                         "the interrupted copy's residue was kept")
        self.assertFalse(leftover.exists(), "the staging directory was left behind")

    def test_a_staging_directory_of_unknown_intent_is_refused_untouched(self):
        """The case the old rule destroyed. A different payload, or another
        actor's: either way not this command's to remove."""
        art = make_artifact(self.base, "a")
        leftover = self.revisions / ".staging-rev-a"
        leftover.mkdir(parents=True)
        (leftover / "SOMETHING-ELSE").write_text("a finished release's payload\n")

        result = self.promote(art, "rev-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to reuse", result.stderr)
        self.assertIn("not a partial copy", result.stderr)
        self.assertTrue(leftover.exists(), "the refusal still deleted it")
        self.assertEqual((leftover / "SOMETHING-ELSE").read_text(),
                         "a finished release's payload\n")
        self.assertFalse((self.revisions / "rev-a").exists())

    def test_an_already_staged_revision_is_refused_without_a_delete_flag(self):
        """`--replace` is gone. Restaging over a revision deletes a directory
        whose intent has not been established — the same act as the staging
        rule above, on an object that is by definition already a release."""
        art = make_artifact(self.base, "a")
        self.assertEqual(self.promote(art, "rev-a").returncode, 0)
        artifact_id = release.fingerprint(self.revisions / "rev-a")

        result = self.promote(make_artifact(self.base, "b"), "rev-a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already staged", result.stderr)
        self.assertEqual(release.fingerprint(self.revisions / "rev-a"), artifact_id)

    def test_the_delete_flag_no_longer_exists(self):
        result = run_release(["promote", "--artifact", str(self.base), "--revision",
                              "x", "--replace", "--runtime-root", str(self.runtime_root),
                              "--composition", self.composition])
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments", result.stderr)


class TheTwoScriptsNameOneManifest(unittest.TestCase):
    """The build writes it and the release reads it. Two files, one key, and
    nothing but a test holding them together — a manifest written under one
    name and looked for under another refuses every artifact for a reason
    nobody can see."""

    def test_the_build_and_the_release_agree_on_the_manifest_name(self):
        spec = importlib.util.spec_from_file_location("serveros_build", ROOT / "scripts/build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(build.ARTIFACT_MANIFEST_NAME, release.ARTIFACT_MANIFEST)

    def test_the_release_reads_the_kind_vocabulary_the_compositions_declare(self):
        """Every composition in this repository declares kinds `release.py`
        implements. The vocabulary is owned by the release path and the
        declarations are the composition's, so the join is checked here."""
        import yaml
        for path in sorted((ROOT / "plugin").rglob("*.yaml")):
            bundle = yaml.safe_load(path.read_text())
            if not isinstance(bundle, dict) or "composition" not in bundle:
                continue
            with self.subTest(composition=path.name):
                kinds = bundle.get("artifacts")
                self.assertTrue(kinds, f"{path} declares no artifacts:")
                for kind in kinds:
                    self.assertIn(kind, release.ARTIFACT_KINDS)


if __name__ == "__main__":
    unittest.main()
