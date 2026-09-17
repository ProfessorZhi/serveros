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


def make_artifact(base: Path, marker: str, *, with_daemon: bool = True) -> Path:
    """A minimal but structurally real artifact.

    The shim is the real three-line shim and the module it imports is real, so
    `bin/serverosctl --help` exercises the actual `bin/` <-> `runtime/`
    relationship that a revision can get wrong while both paths still exist.
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


if __name__ == "__main__":
    unittest.main()
