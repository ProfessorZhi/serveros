#!/usr/bin/env python3
"""The woken Parent's posture — what ONE wake is allowed to start (J6, §2, §3).

WHY THIS FILE EXISTS, AND WHY IT IS NOT A GREP
----------------------------------------------
Until 2026-10-06 the posture lived in `~/.local/bin/serveros-wake-parent`: a
hand-written shell script on one node, outside every repository, holding the
entire permission surface of a continuation Parent. §2 of the convergence
mandate forbids two things at once — «bypassPermissions» and «relaxing Bash» —
and that file was where the second one lived, for a reason it stated itself:
the briefing told the Parent to reconcile through `serverosctl`, and a Parent
with no shell cannot do that.

Both halves moved in the same commit, and this file is what keeps them moved:

  * `runtime/serveros-wake-parent` is the launcher, in the tree, rendered and
    installed by `scripts/release.py continuation-wake`.
  * the briefing names the continuation adapter's operations instead of
    `serverosctl`, so the reason Bash was named back is gone.

A test that only grep'd the launcher for the word `Bash` would pass on a
launcher that had quietly grown a second code-running route. So the classes
below go further in the two directions that can actually be wrong:

  * `TheInstallRendersAndPointsAtIt` runs the REAL release path against a
    synthetic composition root and asserts the unit it writes names the
    launcher it wrote, with no placeholder left in either.
  * `TheLauncherStartsAParentOnlyWhenThereIsAWakeToCarry` runs the INSTALLED
    launcher — the rendered artifact, not the template — under a stand-in for
    `claude` that records its argv, and asserts both what it refuses and the
    exact command line it produces when it does not.

WHAT THIS FILE DOES NOT PROVE. That a real Claude Code session honours
`--restricted`, `--tools` and the MCP config. That is a property of the client
and it is measured by running one; the acceptance run does that and records the
artifacts. What is asserted here is the part this repository owns: the command
line, the file it comes from, and the refusals before it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
RELEASE = REPO / "scripts" / "release.py"

_spec = importlib.util.spec_from_file_location("serveros_release_for_wake_parent",
                                               RELEASE)
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

TEMPLATE = RUNTIME / "serveros-wake-parent"

#: A stand-in for `claude`, and what this file needs from it: the argv it was
#: handed, the stdin that came with it, and the MCP config it was pointed at —
#: appended as one JSON line. Nothing here runs a language model — the question
#: is what the LAUNCHER does before it starts anything, and a real session would
#: answer a different one.
#:
#: The config is read HERE, during the run, and not by the test afterwards,
#: because the launcher's own trap removes it on the way out. That the file is
#: gone by the time the test looks is asserted separately; that it EXISTED, was
#: 0600, and held this wake's envelope is only observable from inside the run.
FAKE_CLAUDE = '''\
#!/bin/bash
mcp_file=""; previous=""
for token in "$@"; do
  if [ "${previous}" = "--mcp-config" ]; then mcp_file="${token}"; fi
  previous="${token}"
done
mcp_mode="none"; mcp_text=""
if [ -n "${mcp_file}" ] && [ -f "${mcp_file}" ]; then
  mcp_mode="$(stat -c '%a' -- "${mcp_file}")"
  mcp_text="$(cat -- "${mcp_file}")"
fi
{
  printf '{"argv": %s, "stdin": %s, "cwd": %s, "mcp_file": %s, "mcp_mode": %s, "mcp_text": %s}\\n' \\
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")" \\
    "$(cat | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \\
    "$(python3 -c 'import json,os; print(json.dumps(os.getcwd()))')" \\
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${mcp_file}")" \\
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${mcp_mode}")" \\
    "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "${mcp_text}")"
} >> "${FAKE_CLAUDE_LOG}"
exit "${FAKE_CLAUDE_EXIT:-0}"
'''


def code_lines(text: str) -> list[str]:
    """The lines a shell would RUN, and not the paragraphs around them.

    The launcher's header is most of the file and it discusses `Bash`,
    `bypassPermissions` and `serverosctl` at length — because that is what the
    posture is, and a prohibition nobody can read is a prohibition that has
    already been forgotten. Every claim below is therefore about code, and the
    prose is off limits to all of them.
    """
    return [line for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def parse_launcher(text: str) -> dict:
    """The launcher read as what it IS — the argv it will run — not as prose.

    This reads the one command the file ends in, which is the only part of it a
    shell and a reader have to agree about. `test_a_real_wake_starts_a_parent…`
    then executes the rendered artifact and reads the SAME argv out of what it
    actually handed to `claude`, because a parse can only ever confirm that the
    file says what this test expects it to say.

    Until 2026-10-14 this read an `exec` line. It now reads `claude_argv`, the
    array that line was flattened into, because the launcher no longer `exec`s:
    the MCP config it hands over lives in a 0600 file, and a process that
    replaced itself runs no trap — so an `exec`ed launcher would leave that file
    on disk for the life of the host, which is the opposite of the property the
    file exists to provide.
    """
    joined = " ".join(line.rstrip("\\").strip() for line in code_lines(text))
    joined = " ".join(joined.split())
    marker = "claude_argv=("
    start = joined.rindex(marker) + len(marker)
    end = joined.index(")", start)
    argv = shlex.split(joined[start:end])
    return {"argv": argv, "flags": argv}


def flag(argv: list[str], name: str):
    """The value after `--name`, or None. `--name value` and `--name=value`."""
    for i, tok in enumerate(argv):
        if tok == name:
            return argv[i + 1] if i + 1 < len(argv) else None
        if tok.startswith(name + "="):
            return tok[len(name) + 1:]
    return None


class TheLauncherIsAPosture(unittest.TestCase):
    """The file itself, read as its own exec line."""

    def setUp(self):
        self.text = TEMPLATE.read_text(encoding="utf-8")
        self.parsed = parse_launcher(self.text)
        self.argv = self.parsed["flags"]

    def test_the_template_is_in_the_tree_and_not_only_on_a_node(self):
        self.assertTrue(TEMPLATE.is_file(), f"{TEMPLATE} is missing")

    def test_no_code_running_tool_is_named_and_no_bypass_is_offered(self):
        """§2's two prohibitions, as one assertion about one list."""
        tools = flag(self.argv, "--tools") or ""
        named = [t.strip() for t in tools.split(",") if t.strip()]
        self.assertEqual(named, ["Read", "Edit", "Write", "Glob", "Grep"])
        for forbidden in ("Bash", "PowerShell", "REPL", "WebFetch", "WebSearch"):
            with self.subTest(tool=forbidden):
                self.assertNotIn(forbidden, named)
        for switcher in ("--dangerously-skip-permissions",
                         "--allow-dangerously-skip-permissions"):
            with self.subTest(switch=switcher):
                self.assertNotIn(switcher, self.argv)
        body = "\n".join(code_lines(self.text))
        self.assertNotIn("bypassPermissions", body)
        self.assertNotIn("permission-mode", body)

    def test_it_is_restricted_and_the_mcp_surface_is_closed_to_everything_else(self):
        self.assertIn("--restricted", self.argv)
        self.assertIn("--strict-mcp-config", self.argv)

    def test_the_only_mcp_server_is_this_wake_s_adapter(self):
        """One server, one env key, and the adapter inside the revision.

        The BotMux control plane is why `--strict-mcp-config` is here at all: a
        woken Parent inheriting the node's plugin fleet would hold the fleet's own
        MCP surface, and §2 forbids handing a continuation Parent the node
        mutation surface in ANY spelling — this one included.

        The envelope is interpolated rather than left to be inherited. The
        failure that prevents has no symptom: an adapter that resolved ANOTHER
        wake's envelope would run with that wake's Item and that wake's grant,
        and every check inside it would pass, because they would all be checks
        against the wrong wake's truth.
        """
        # The config is BUILT here, not written literally — a `printf` format with
        # exactly two substitutions. Parsing it as JSON after filling them in is
        # what makes "one server, one env key" an assertion rather than a reading.
        line = next(l for l in code_lines(self.text) if '"mcpServers"' in l)
        skeleton = json.loads(line.split("'", 2)[1].replace("%s", "X"))
        self.assertEqual(skeleton, {"mcpServers": {"continuation": {
            "command": "X",
            "env": {"SERVEROS_CONTINUATION_ENVELOPE": "X"}}}})
        self.assertIn(
            'adapter="@SERVEROS_RUNTIME@/mcp/bin/serveros-continuation-mcp"',
            code_lines(self.text))
        self.assertIn("--mcp-config", self.argv)
        # THE VALUE IS A PATH, and that is the whole of issue #171's answer on
        # this side of the boundary. An inline JSON value puts the config — and
        # whatever an MCP server entry later comes to hold, an Authorization
        # header being exactly what one holds — onto a command line every
        # account on a shared host can read with `ps`. The file it points at is
        # 0600 in a private directory, and the tests below this one assert both.
        self.assertEqual(flag(self.argv, "--mcp-config"), "${config_file}")
        self.assertNotIn("mcpServers", " ".join(self.argv))
        # The value is a shell variable holding a path, not a document. The
        # distinction is the whole change: an inline config is a literal here.
        value = flag(self.argv, "--mcp-config") or ""
        self.assertTrue(value.startswith("${") and value.endswith("}"),
                        f"{value!r} is not a variable reference")

    def test_the_launcher_never_invokes_serverosctl(self):
        """The route that made Bash necessary, gone from what actually runs.

        Not from the prose: the header says the word several times, because the
        header is the argument for why it is gone — and one of the refusals below
        names the command it answers to, which is information a reader needs.
        What is asserted is that no COMMAND mentions it: every double-quoted
        string is removed first, because those are the messages, and what remains
        is what a shell would run.
        """
        body = re.sub(r'"[^"]*"', '""', "\n".join(code_lines(self.text)))
        self.assertNotIn("serverosctl", body)
        self.assertFalse([tok for tok in self.argv if "serverosctl" in tok],
                         "the exec'd command line names serverosctl")

    def test_it_refuses_before_it_starts_anything(self):
        """Three refusals, and each names what is missing rather than exiting 0.

        A launcher that started a Parent with no envelope would produce a session
        whose whole continuation surface is a server that failed to load, and the
        Parent would then do whatever a model does when its tools are missing.
        """
        self.assertIn("SERVEROS_CONTINUATION_ENVELOPE", self.text)
        for word in ("empty briefing", "does not exist", "not executable"):
            with self.subTest(refusal=word):
                self.assertIn(word, self.text)
        self.assertIn("exit 2", self.text)


class TheInstallRendersAndPointsAtIt(unittest.TestCase):
    """`scripts/release.py continuation-wake`, run for real on a fake revision."""

    REVISION = "rev-fixture0001"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.root = self.base / "runtime-assets" / "serveros-linux-work"
        self.unit_dir = self.base / "units"
        self.bin_dir = self.base / "bin"
        revision = self.root / "revisions" / self.REVISION
        (revision / "runtime").mkdir(parents=True)
        # The REAL templates, copied. A test against a fixture template would
        # prove the renderer works on a file nobody ships.
        for name in (release.WAKE_SERVICE_NAME, release.WAKE_TIMER_NAME,
                     release.WAKE_PARENT_NAME):
            (revision / "runtime" / name).write_bytes((RUNTIME / name).read_bytes())
        (self.root / "current").symlink_to(Path("revisions") / self.REVISION)

    def tearDown(self):
        self._tmp.cleanup()

    def install(self, *extra: str):
        return subprocess.run(
            [sys.executable, str(RELEASE), "continuation-wake",
             "--runtime-root", str(self.base / "runtime-assets"),
             "--composition", "serveros-linux-work",
             "--state-dir", str(self.base / "state"),
             "--unit-dir", str(self.unit_dir),
             "--bin-dir", str(self.bin_dir),
             "--budget", "900", "--no-reload", *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_it_installs_the_launcher_and_the_unit_names_that_file(self):
        proc = self.install()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        launcher = self.bin_dir / release.WAKE_PARENT_NAME
        self.assertTrue(launcher.is_file(), proc.stdout)
        mode = stat.S_IMODE(launcher.stat().st_mode)
        self.assertTrue(mode & stat.S_IXUSR, oct(mode))

        unit = (self.unit_dir / release.WAKE_SERVICE_NAME).read_text()
        exec_line = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
        self.assertIn(f"--parent {launcher} ", exec_line + " ")

    def test_neither_the_launcher_nor_the_unit_keeps_a_placeholder(self):
        """A placeholder that survives is a Parent started at a path with an `@`."""
        self.assertEqual(self.install().returncode, 0)
        launcher = (self.bin_dir / release.WAKE_PARENT_NAME).read_text()
        self.assertNotIn("@SERVEROS_RUNTIME@", launcher)
        self.assertIn(str(self.root / "current"), launcher)
        unit = (self.unit_dir / release.WAKE_SERVICE_NAME).read_text()
        for placeholder in ("@SERVEROS_RUNTIME@", "@SERVEROS_STATE_DIR@",
                            "@SERVEROS_WAKE_PARENT@", "@SERVEROS_WAKE_BUDGET@",
                            "@SERVEROS_WAKE_TIMEOUT@"):
            with self.subTest(placeholder=placeholder):
                self.assertNotIn(placeholder, unit)

    def test_the_rendered_launcher_is_the_same_posture_as_the_template(self):
        """Rendering must not be where the tool list changes."""
        self.assertEqual(self.install().returncode, 0)
        rendered = (self.bin_dir / release.WAKE_PARENT_NAME).read_text()
        self.assertEqual(parse_launcher(rendered)["flags"],
                         parse_launcher(TEMPLATE.read_text())["flags"])

    def test_a_dry_run_writes_neither_file(self):
        proc = self.install("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would install", proc.stdout)
        self.assertFalse((self.bin_dir / release.WAKE_PARENT_NAME).exists())
        self.assertFalse(self.unit_dir.exists())

    def test_a_revision_without_the_launcher_refuses_rather_than_arming_a_dead_timer(self):
        """The failure this command exists to refuse, kept refusable.

        An artifact built before the launcher shipped can still be promoted and
        rolled back to. Installing the timer against it would arm a routine whose
        Parent does not exist — which fires every minute, wakes nothing and exits
        0, the exact shape §17.4 forbids.
        """
        (self.root / "revisions" / self.REVISION / "runtime"
         / release.WAKE_PARENT_NAME).unlink()
        proc = self.install()
        self.assertEqual(proc.returncode, 1)
        self.assertIn(release.WAKE_PARENT_NAME, proc.stderr)
        self.assertFalse(self.unit_dir.exists())
        # And the way out is named rather than left to be guessed.
        self.assertIn("--parent", proc.stderr)

    def test_a_node_may_still_name_its_own_parent_and_then_nothing_is_installed(self):
        """The escape hatch, and what it costs: the shipped posture is not used."""
        mine = self.base / "my-parent"
        mine.write_text("#!/bin/bash\nexit 0\n")
        mine.chmod(0o755)
        proc = self.install("--parent", str(mine))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.bin_dir / release.WAKE_PARENT_NAME).exists())
        unit = (self.unit_dir / release.WAKE_SERVICE_NAME).read_text()
        self.assertIn(f"--parent {mine} ", unit)


class TheLauncherStartsAParentOnlyWhenThereIsAWakeToCarry(unittest.TestCase):
    """The INSTALLED launcher, executed, against a stand-in for `claude`."""

    REVISION = "rev-fixture0001"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.log = self.base / "claude-argv.jsonl"
        self.fake = self.base / "claude"
        self.fake.write_text(FAKE_CLAUDE)
        self.fake.chmod(0o755)
        self.launcher = self.base / "serveros-wake-parent"
        self.launcher.write_text(
            TEMPLATE.read_text().replace("@SERVEROS_RUNTIME@", str(self.base)))
        self.launcher.chmod(0o755)
        (self.base / "mcp" / "bin").mkdir(parents=True)
        self.parent_dir = self.base / "item-worktree"
        self.parent_dir.mkdir()
        (self.base / "mcp" / "bin" / "serveros-continuation-mcp").write_text(
            "#!/bin/bash\nprintf '%s\\n' " + shlex.quote(str(self.parent_dir)) + "\n")
        (self.base / "mcp" / "bin" / "serveros-continuation-mcp").chmod(0o755)
        # The revision's own preflight, and the contract it reads. `scripts/` is
        # deliberately NOT here, and its absence is the point: `scripts/build.py`'s
        # `CORE_MODULES` ships `runtime` and `fleet`, so a guard that reached for
        # `scripts/scan_secrets.py` would work from a checkout and refuse every
        # wake on every installed node — the `intake` drop the builder's own
        # comment records. This fixture is the installed shape, not a convenience.
        (self.base / "runtime").mkdir()
        (self.base / "fleet").mkdir()
        for name in ("launcher_guard.py", "credential_shapes.py"):
            shutil.copy2(RUNTIME / name, self.base / "runtime" / name)
        shutil.copy2(REPO / "fleet" / "transport-contract.yaml",
                     self.base / "fleet" / "transport-contract.yaml")
        # The launcher makes its MCP config under `${TMPDIR:-/tmp}`, so pointing
        # TMPDIR at a directory this test owns turns "nothing was left behind"
        # into an assertion about a directory rather than a guess about `/tmp`.
        self.scratch = self.base / "scratch-tmp"
        self.scratch.mkdir()
        self.envelope = self.base / "envelope.json"
        self.envelope.write_text("{}\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_launcher(self, *, briefing="a briefing\n", envelope=...):
        env = {k: v for k, v in os.environ.items()
               if k not in ("SERVEROS_CONTINUATION_ENVELOPE",)}
        env["SERVEROS_WAKE_CLAUDE"] = str(self.fake)
        env["FAKE_CLAUDE_LOG"] = str(self.log)
        env["TMPDIR"] = str(self.scratch)
        if envelope is not ...:
            if envelope is not None:
                env["SERVEROS_CONTINUATION_ENVELOPE"] = str(envelope)
        else:
            env["SERVEROS_CONTINUATION_ENVELOPE"] = str(self.envelope)
        return subprocess.run([str(self.launcher)], input=briefing,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env, timeout=60)

    def records(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def started(self) -> list[list[str]]:
        return [record["argv"] for record in self.records()]

    def sole_record(self) -> dict:
        records = self.records()
        self.assertEqual(len(records), 1, "expected exactly one started client")
        return records[0]

    def test_an_empty_briefing_starts_nothing(self):
        proc = self.run_launcher(briefing="")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("empty briefing", proc.stderr)
        self.assertEqual(self.started(), [])

    def test_no_envelope_starts_nothing_and_names_the_variable(self):
        proc = self.run_launcher(envelope=None)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("SERVEROS_CONTINUATION_ENVELOPE", proc.stderr)
        self.assertEqual(self.started(), [])

    def test_an_envelope_that_is_not_there_starts_nothing(self):
        proc = self.run_launcher(envelope=self.base / "nope.json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not exist", proc.stderr)
        self.assertEqual(self.started(), [])

    def test_an_absent_adapter_starts_nothing(self):
        """The revision's own adapter, and a revision that lacks it says so."""
        (self.base / "mcp" / "bin" / "serveros-continuation-mcp").unlink()
        proc = self.run_launcher()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("continuation-mcp", proc.stderr)
        self.assertEqual(self.started(), [])

    def test_a_real_wake_starts_a_parent_holding_this_wake_and_no_shell(self):
        """The positive control, and the whole point of the file.

        If this test is green and the refusals above are green, then the only
        way this launcher starts a Parent is with an envelope in hand, a
        briefing to carry, and a tool list with no code-running entry in it.
        """
        proc = self.run_launcher(briefing="resume SRV-0001\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = self.sole_record()
        self.assertEqual(record["cwd"], str(self.parent_dir))
        argv = record["argv"]
        self.assertNotIn("Bash", " ".join(argv))
        self.assertIn("resume SRV-0001", argv)
        config = json.loads(record["mcp_text"])
        self.assertEqual(
            config["mcpServers"]["continuation"]["env"],
            {"SERVEROS_CONTINUATION_ENVELOPE": str(self.envelope)})
        self.assertTrue(config["mcpServers"]["continuation"]["command"].endswith(
            "/mcp/bin/serveros-continuation-mcp"))

    # -- the config is a private file, and it does not outlive the wake ---------

    def test_the_config_is_a_0600_file_and_never_an_argument(self):
        """Issue #171, asserted from inside the run.

        0600 rather than merely "not world-readable": this file is what the
        client reads, and a mode that let another account on this host read it
        would have moved the leak from `ps` to `cat` rather than closing it.
        """
        proc = self.run_launcher()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = self.sole_record()
        self.assertEqual(record["mcp_mode"], "600")
        self.assertEqual(flag(record["argv"], "--mcp-config"), record["mcp_file"])
        # Nothing that looks like the config itself is on the command line.
        self.assertNotIn("{", flag(record["argv"], "--mcp-config") or "")

    def test_nothing_is_left_behind_when_the_wake_ends(self):
        """Cleanup, and the narrowest claim that can be made about it.

        `TMPDIR` is pointed at a directory this test owns, so "nothing is left"
        is an assertion about a directory rather than a guess about `/tmp`. Two
        paths are removed and both were made by this process inside a directory
        `mktemp -d` created for it alone, which is the whole of the guarantee
        that it is not deleting anybody else's file.
        """
        proc = self.run_launcher()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [],
                         "the wake left its MCP config behind")

    def test_a_refusal_cleans_up_too(self):
        """The cleanup is on the way out, and a refusal is a way out."""
        fake = self.base / ("sk-" + "c" * 40)
        fake.mkdir()
        envelope = fake / "envelope.json"
        envelope.write_text("{}\n")
        proc = self.run_launcher(envelope=envelope)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(list(self.scratch.iterdir()), [],
                         "a refused wake left its MCP config behind")

    # -- the preflight ---------------------------------------------------------

    def test_a_credential_shaped_envelope_is_refused_before_anything_starts(self):
        """The bound, exercised through the real code path with a fake value.

        The value is synthetic and assembled here — a real one is the thing this
        whole change exists to keep off a command line. What is asserted is the
        SHAPE of the answer: a rule name, a position, and no value anywhere; and
        that the client was never started.
        """
        fake = self.base / ("sk-" + "c" * 40)
        fake.mkdir()
        envelope = fake / "envelope.json"
        envelope.write_text("{}\n")
        proc = self.run_launcher(envelope=envelope)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("REFUSED", proc.stderr)
        self.assertIn("MCP_CONFIG_CARRIES_CREDENTIAL_SHAPE", proc.stderr)
        self.assertIn("anthropic-or-openai-key", proc.stderr)
        self.assertEqual(self.started(), [], "the client was started anyway")
        # A rule and a position, and never the value.
        for stream in (proc.stdout, proc.stderr):
            self.assertNotIn("sk-", stream)
            self.assertNotIn("c" * 40, stream)

    def test_a_bearer_on_the_briefing_is_refused_and_never_echoed(self):
        """The other half of the line: the briefing is an argv token too.

        `-p "${briefing}"` is the largest thing on this command line, and a
        briefing is assembled from records this launcher did not write. A token
        that reached it would be on the line before any of the checks below.
        """
        token = "A1bC2dE3fG4hI5jK"
        proc = self.run_launcher(briefing="context follows: Bearer " + token + "\n")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("ARGV_CARRIES_CREDENTIAL_SHAPE", proc.stderr)
        self.assertIn("bearer-credential", proc.stderr)
        self.assertIn("argv[", proc.stderr)
        self.assertEqual(self.started(), [], "the client was started anyway")
        self.assertNotIn(token, proc.stdout + proc.stderr)

    def test_a_revision_without_the_preflight_refuses_rather_than_starts(self):
        """A bound that can be left out of a revision is not a bound.

        The file is part of the promoted payload, and this asserts the launcher
        treats its absence as a refusal rather than as a check it can skip.
        """
        (self.base / "runtime" / "launcher_guard.py").unlink()
        proc = self.run_launcher()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("launcher_guard.py", proc.stderr)
        self.assertEqual(self.started(), [])


if __name__ == "__main__":
    unittest.main()
