#!/usr/bin/env python3
"""Regressions for the client-runner boundary: env, framing, fresh output, symlinks.

These are deliberately small and protocol-level. They do not start a model, a
real Codex session or a real owned runtime; the real-client receipts are a
separate, human-run observation.
"""
from __future__ import annotations

import io
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
sys.path.insert(0, str(HERE))


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codex = load("hardening_codex_runner", "run_codex_client_e2e.py")
cc = load("hardening_cc_runner", "run_cc_client_e2e.py")
fixture = load("hardening_codex_fixture", "build_codex_local_fixture.py")
probe = load("hardening_owned_probe", "fleet_owned_probe.py")
framing = load("hardening_stdio_framing", "stdio_framing.py")


class TheCodexEnvironmentIsExplicit(unittest.TestCase):
    def test_provider_credentials_are_not_inherited(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="codex-env-"))
        old = os.environ
        original_popen = codex.subprocess.Popen
        captured: dict = {}
        try:
            os.environ = {"PATH": "/usr/bin", "OPENAI_API_KEY": "synthetic-only",
                          "CODEX_API_KEY": "synthetic-only"}
            codex.subprocess.Popen = (
                lambda *a, **k: captured.update(k) or SimpleNamespace())
            codex.CodexServer("codex", tmp / "home", tmp, 1)
        finally:
            os.environ = old
            codex.subprocess.Popen = original_popen
        self.assertNotIn("OPENAI_API_KEY", captured["env"])
        self.assertNotIn("CODEX_API_KEY", captured["env"])

    def test_tmpdir_is_the_evidence_directory_not_ambient(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="codex-tmp-"))
        old = os.environ
        original_popen = codex.subprocess.Popen
        captured: dict = {}
        try:
            os.environ = {"PATH": "/usr/bin", "TMPDIR": "/ambient-not-evidence"}
            codex.subprocess.Popen = (
                lambda *a, **k: captured.update(k) or SimpleNamespace())
            codex.CodexServer("codex", tmp / "home", tmp, 1)
        finally:
            os.environ = old
            codex.subprocess.Popen = original_popen
        self.assertEqual(captured["env"]["TMPDIR"], str(tmp / "tmp"))


class BurstFramesAreNotLost(unittest.TestCase):
    def test_codex_server_reads_a_buffered_reply_after_a_notification(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="codex-burst-"))
        child = tmp / "fake-codex"
        child.write_text(
            "#!" + sys.executable + "\n"
            "import json,sys\n"
            "for line in sys.stdin:\n"
            "    request=json.loads(line)\n"
            "    sys.stdout.write(json.dumps({'method':'notification'})+'\\n')\n"
            "    sys.stdout.write(json.dumps({'id':request['id'],"
            "'result':{'ok':True}})+'\\n')\n"
            "    sys.stdout.flush()\n")
        child.chmod(0o700)
        server = codex.CodexServer(str(child), tmp / "home", tmp, 0.5)
        try:
            self.assertTrue(server.request(1, "initialize", {})["result"]["ok"])
        finally:
            server.close()

    def test_owned_probe_call_reads_a_buffered_reply_after_a_notification(self) -> None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd,
                 b'{"method":"notification"}\n'
                 b'{"id":1,"result":{"ok":true}}\n')
        os.close(write_fd)
        stream = os.fdopen(read_fd, "rb", buffering=0)
        self.addCleanup(stream.close)
        reader = framing.LineFramer(stream)
        proc = SimpleNamespace(stdin=io.BytesIO())
        message = probe._call(proc, reader, 1, "initialize", {}, 1.0)
        self.assertTrue(message["result"]["ok"])

    def test_a_line_over_the_bound_is_refused(self) -> None:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"x" * 64 + b"\n")
        os.close(write_fd)
        stream = os.fdopen(read_fd, "rb", buffering=0)
        self.addCleanup(stream.close)
        reader = framing.LineFramer(stream, max_line_bytes=8)
        with self.assertRaises(framing.FramingError):
            reader.next_line(1.0)


class FreshOutputIsPreserved(unittest.TestCase):
    def test_cc_prepare_refuses_an_existing_root(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="cc-fresh-"))
        sentinel = tmp / "belongs-to-someone-else"
        sentinel.write_text("preserve", encoding="utf-8")
        runner = cc.Runner(tmp, False)
        with self.assertRaises(RuntimeError):
            runner.prepare()
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_cc_build_does_not_remove_an_existing_plugin_before_validation(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="cc-build-"))
        runner = cc.Runner(
            tmp, False,
            owned_entry=str(tmp / "missing-entry"),
            owned_config=str(tmp / "missing-config"))
        runner.plugin.mkdir()
        sentinel = runner.plugin / "unrelated-wip"
        sentinel.write_text("preserve", encoding="utf-8")
        calls: list[str] = []
        original = cc.shutil.rmtree
        cc.shutil.rmtree = lambda path, *a, **k: calls.append(str(path))
        try:
            with self.assertRaises(Exception):
                runner.build()
        finally:
            cc.shutil.rmtree = original
        self.assertEqual(calls, [])
        self.assertTrue(sentinel.exists())


class SkillsSourceSymlinksAreRefused(unittest.TestCase):
    def test_a_symlinked_skill_directory_never_reaches_the_copy(self) -> None:
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="codex-symlink-"))
        source = tmp / "skills"
        source.mkdir()
        outside = tmp / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text(
            "---\nname: one\ndescription: synthetic\n---\ncanary\n",
            encoding="utf-8")
        (source / "one").symlink_to(outside, target_is_directory=True)
        out = tmp / "fixture"
        with self.assertRaises(fixture.Refused):
            fixture.generate(out=out, skills_source=source)
        self.assertFalse(out.exists())


class TheSkillDistinguishesExistingAndNewProjects(unittest.TestCase):
    def test_new_project_is_bound_to_the_owner_root_not_a_static_allow_row(self) -> None:
        def binds_new_project_to_static_allow(text: str) -> bool:
            return "Which **project** may be created on?" in text

        old = ("| Which **project** may be created on? | the machine's project "
               "list *and* the surface's `create_allow`, both |")
        self.assertTrue(binds_new_project_to_static_allow(old))
        current = (ROOT / "integrations" / "fleet-v1-first-install" / "plugin"
                   / "skills" / "fleet-create-with-grants" / "SKILL.md"
                   ).read_text(encoding="utf-8")
        self.assertFalse(binds_new_project_to_static_allow(current))
        flattened = " ".join(current.split())
        self.assertIn("project_roots", flattened)
        self.assertIn("new project is authorized by the owner-configured project",
                      flattened)


if __name__ == "__main__":
    unittest.main()
