"""Captured-body admission and existing partial-meeting handoff, without a provider."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_reading_test", REPO / "intake/source_reading.py")
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def web_snapshot(tmp_path):
    records = tmp_path / "records"
    manifest = records / "information/raw/web/example/manifest.json"
    manifest.parent.mkdir(parents=True)
    assets = tmp_path / "assets"
    assets.mkdir()
    body = assets / "snapshot.html"
    body.write_text("<article>A fixed captured body.</article>\n")
    digest = hashlib.sha256(body.read_bytes()).hexdigest()
    data = {"source": {"requested_url": "https://example.test/article"},
            "fetch": {"sha256": digest, "http_status": 200},
            "page_kind": {"checked_markers": ["sign in", "captcha", "enable javascript", "read more"],
                          "shell_markers_found": []},
            "body": {"verification_copies": [{"root": str(assets), "file": body.name,
                                               "sha256": digest}]},
            "materials": {"inventoried": False, "count": 0}}
    manifest.write_text(json.dumps(data))
    return records, assets, manifest, data


def test_web_uses_only_the_current_digest_copy_under_explicit_asset_root(tmp_path):
    records, assets, manifest, data = web_snapshot(tmp_path)
    key = "web/example/manifest.json"
    with pytest.raises(reader.Refused, match="explicitly admitted"):
        reader.read_packet(key, records)
    packet = reader.read_packet(key, records, allowed_asset_roots=(assets,))
    assert packet["kind"] == "WEB" and packet["revision"] is None
    assert packet["coverage"]["media_declared"] is None
    assert packet["content_identity"] == data["fetch"]["sha256"]
    Path(packet["path"]).write_text("replaced")
    with pytest.raises(reader.Refused, match="digest"):
        reader.read_packet(key, records, allowed_asset_roots=(assets,))


def test_web_delta_and_cli_hand_the_same_verified_snapshot_to_the_writer(tmp_path):
    records, assets, manifest, data = web_snapshot(tmp_path)
    card = records / "information/understanding/projects/web.md"
    card.parent.mkdir(parents=True)
    card.write_text("# Web\n\n## Sources\n- `information/raw/web/example/manifest.json` sha deadbeef\n")
    command = [sys.executable, "-B", str(REPO / "runtime/cli.py"), "work"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(command + ["understanding-brief", "--records-root", str(records),
                            "--source-asset-root", str(assets), "--json"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)["briefs"][0]
    assert packet["source"]["kind"] == "WEB"
    assert packet["read"]["content_identity"] == data["fetch"]["sha256"]
    document = {"schema": packet["reading"]["schema"], "source": packet["source"],
                "read": packet["read"], "claims": [{
                    "id": "web1", "kind": "OBSERVED_FACT", "subject": "example",
                    "assertion": {"was": None, "now": "A fixed body was captured."},
                    "support": {"positions": [], "excerpt": "A fixed captured body."},
                    "applies_to": None, "touches": [], "supersedes": [], "conflicts": [],
                    "card": packet["source"]["card"], "views": []}]}
    reading = tmp_path / "reading.json"
    reading.write_text(json.dumps(document))
    result = subprocess.run(command + ["understanding-semantic", "--records-root", str(records),
                            "--reading", str(reading), "--source-asset-root", str(assets), "--json"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    # A verified excerpt does not provide a grant.
    assert json.loads(result.stdout)["committed"] == []


@pytest.mark.parametrize("escape", ["traversal", "symlink"])
def test_web_copy_cannot_escape_the_explicit_asset_root(tmp_path, escape):
    records, assets, manifest, data = web_snapshot(tmp_path)
    outside = tmp_path / "outside.html"
    outside.write_text("<article>A fixed captured body.</article>\n")
    if escape == "symlink":
        link = assets / "escape.html"
        link.symlink_to(outside)
        data["body"]["verification_copies"][0]["file"] = link.name
    else:
        data["body"]["verification_copies"][0]["file"] = "../outside.html"
    manifest.write_text(json.dumps(data))
    with pytest.raises(reader.Refused, match="escape|invalid"):
        reader.read_packet("web/example/manifest.json", records, allowed_asset_roots=(assets,))


def test_partial_meeting_reads_tied_segments_without_waiting_for_audio():
    declaration = {
        "scope": {"meeting_ref": "meeting:synthetic", "tenant_ref": "tenant:synthetic"},
        "members": [{"kind": "transcript", "state": "PRESENT", "revision": "transcript:r7",
                     "object_ref": "transcript:synthetic"},
                    {"kind": "recording", "state": "PROCESSING"}],
        "content": {"transcript_segments": [
            {"start_s": 0, "end_s": 2, "provenance": "transcript:r7", "text": "background audio"},
            {"start_s": 3, "end_s": 5, "provenance": "transcript:r7", "speaker": "speaker:1",
             "text": "The test failed.", "tie": {"kind": "support_request", "ref": "request:1"}}]},
    }
    before = reader.meeting_packet(declaration, observed_at="2026-10-06T00:00:00Z")
    assert before["text"] == "The test failed."
    assert before["coverage"]["segments_excluded"] == 1
    assert before["coverage"]["not_present"]["audio"] == "PROCESSING"
    assert before["revision"] == "transcript:r7"
    declaration["members"][1].update(state="PRESENT", revision="recording:r1", object_ref="recording:1")
    after = reader.meeting_packet(declaration, observed_at="2026-10-06T01:00:00Z")
    assert after["content_identity"] == before["content_identity"]
    assert "audio" not in after["coverage"]["not_present"]
