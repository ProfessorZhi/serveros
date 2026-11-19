"""V1 deep memory: OpenViking capture (broad, secrets dropped) and recall (loopback, fenced)."""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import archive_memory  # noqa: E402
from fleet_mcp.archive_memory import Archive, hits  # noqa: E402

sys.path.insert(0, str(ROOT / "memory" / "openviking"))
import capture  # noqa: E402

FAKE_SECRET = "gh" + "p_" + "Z" * 36


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recall(unittest.TestCase):
    def test_hits_are_trimmed_scored_and_shape_tolerant(self):
        doc = {"result": {"resources": [
            {"uri": "viking://resources/fleet/journal/a.md", "score": 0.8, "abstract": "x " * 400},
            {"uri": "viking://r/b", "score": 0.2, "abstract": "too far"}],
            "memories": [{"uri": "viking://m/c", "score": 0.6, "overview": "腾讯云 不返回 cached"}]}}
        got = hits(doc, chars=50)
        self.assertEqual([h["uri"] for h in got],
                         ["viking://resources/fleet/journal/a.md", "viking://m/c"])
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0]["text"].endswith("…"))
        self.assertEqual(hits([]), [])

    def test_only_loopback_and_only_with_a_key(self):
        sent = []

        def opener(request, timeout):
            sent.append((request.full_url, request.headers.get("X-api-key")))
            return Answer(json.dumps({"result": {"resources": [
                {"uri": "u", "score": 0.9, "abstract": "a"}]}}).encode())
        self.assertEqual(Archive("http://10.0.0.5:1933", key=lambda: "k", opener=opener)
                         .find("q"), [])
        self.assertEqual(Archive(key=lambda: None, opener=opener).find("q"), [])
        self.assertEqual(sent, [])
        self.assertEqual(len(Archive(key=lambda: "k", opener=opener).find("q")), 1)
        self.assertTrue(sent[0][0].endswith("/api/v1/search/find"))

    def test_trouble_is_silence(self):
        def opener(request, timeout):
            raise OSError("down")
        self.assertEqual(Archive(key=lambda: "k", opener=opener).find("q"), [])

    def test_the_key_comes_from_env_or_the_one_vault_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "openviking.env"
            vault.write_text("OTHER=1\nOPENVIKING_ROOT_API_KEY='abc'\n")
            self.assertEqual(archive_memory._key({}, vault), "abc")
            self.assertEqual(archive_memory._key({"OPENVIKING_ROOT_API_KEY": "e"}, vault), "e")
            self.assertIsNone(archive_memory._key({}, Path(tmp) / "none"))


class Capture(unittest.TestCase):
    def test_pages_skip_hidden_and_credential_files_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir()
            (root / "runs/a.md").write_text(f"ok line\ntoken {FAKE_SECRET}\n")
            (root / ".git").mkdir()
            (root / ".git/x.md").write_text("no")
            (root / ".env").write_text("A=1")
            (root / "b.png").write_bytes(b"x")
            pages = capture.pages_of_dir(root)
            self.assertEqual(sorted(pages), ["runs/a.md"])
            data, digest, dropped = capture.snapshot(pages)
        self.assertEqual(dropped, 1)
        text = zipfile.ZipFile(io.BytesIO(data)).read("runs/a.md").decode()
        self.assertIn("ok line", text)
        self.assertNotIn(FAKE_SECRET, text)
        self.assertEqual(capture.snapshot(pages)[1], digest)        # stable: unchanged is skipped

    def test_chats_become_one_page_per_chat_per_month(self):
        with tempfile.TemporaryDirectory() as tmp:
            chat = Path(tmp) / "groups/tokyo-server/oc_1"
            chat.mkdir(parents=True)
            rows = [{"create_time": "2026-11-18 10:00", "sender": {"name": "Vince"},
                     "content": "部署好了"},
                    {"create_time": "2026-10-01 09:00", "sender": {"id": "ou_x"}, "content": "早"}]
            (chat / "messages.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                                           for r in rows) + "\n")
            pages = capture.pages_of_chats(Path(tmp))
        self.assertEqual(sorted(pages), ["groups/tokyo-server/2026-10.md",
                                         "groups/tokyo-server/2026-11.md"])
        self.assertIn("Vince：部署好了", pages["groups/tokyo-server/2026-11.md"])

    def test_dry_run_reads_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.md").write_text("x")
            self.assertEqual(capture.main(["--dir", f"j={tmp}", "--dry-run",
                                           "--state", str(Path(tmp) / "s.json")]), 0)
            self.assertFalse((Path(tmp) / "s.json").exists())
        self.assertEqual(capture.main(["--url", "http://10.1.1.1:1933", "--dry-run"]), 2)


class ProductionGateway(unittest.TestCase):
    def test_the_production_gateway_is_a_known_target_and_allowed_on_the_a6000(self):
        import yaml
        policy = yaml.safe_load((ROOT / "fleet" / "runtime-policy.yaml").read_text())
        self.assertIn("fjbigmodel.fjdac.cn", policy["gateway_hosts"]["target:fj-prod"])
        dell = policy["devices"]["device:llm-lab-dell"] if "devices" in policy else \
            policy["default"]["device:llm-lab-dell"]
        self.assertIn("target:fj-prod", dell["runtimes"]["claude-code"]["gateways"])


if __name__ == "__main__":
    unittest.main()


class OneStatus(unittest.TestCase):
    """serverosd convergence: `serverosctl status` names the pusher and the publisher too."""

    def test_components_read_from_their_own_files(self):
        import os
        import time
        sys.path.insert(0, str(ROOT / "runtime"))
        from runtime import cli
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual({k: v["state"] for k, v in cli.node_components(home).items()},
                             {"eventd": "NOT_RUNNING", "core-lite": "NOT_RUNNING"})
            state = home / ".local/state/serveros"
            (state / "core-lite").mkdir(parents=True)
            (state / "core-lite/status.json").write_text("{}")
            (state / "events.jsonl.status.json").write_text(json.dumps(
                {"ok": True, "version": "0.1.0", "events": 3,
                 "last_poll_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
            got = cli.node_components(home)
            self.assertEqual(got["eventd"]["state"], "OK")
            self.assertEqual(got["core-lite"]["state"], "OK")
            later = cli.node_components(home, now=time.time() + 3600)
            self.assertEqual((later["eventd"]["state"], later["core-lite"]["state"]),
                             ("STALE", "STALE"))
