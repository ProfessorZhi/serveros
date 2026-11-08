"""The key center on SOPS + age: the owner encrypts, a device reads only its own.

Runs against the real `sops` and `age` when they are installed; the pure parts
run everywhere.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("serveros_secret", ROOT / "distribution/keycenter/serveros_secret.py")
ss = importlib.util.module_from_spec(spec)
sys.modules.setdefault("serveros_secret", ss)
spec.loader.exec_module(ss)

REAL = all(shutil.which(b) for b in ("sops", "age-keygen", "git"))


def test_dotenv_quotes_what_systemd_reads():
    assert ss.dotenv({"B": 'a"b', "A": "x\\y"}) == 'A="x\\\\y"\nB="a\\"b"\n'


def test_names_never_carry_values(tmp_path):
    state = tmp_path / "s.json"
    state.write_text(json.dumps({"schema": "serveros/secret-names.v1", "names": ["GW0_TOKEN", "bad name"],
                                 "bundles": [{"names": ["GW0_TOKEN"]}], "synced_at": "t"}))
    out = ss.read_names(state)
    assert out["names"] == ["GW0_TOKEN"] and "value" not in json.dumps(out)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def world(tmp_path):
    if not REAL:
        pytest.skip("sops/age not installed")
    owner = tmp_path / "owner.key"
    dell_key = tmp_path / "dell.key"
    tokyo_key = tmp_path / "tokyo.key"
    pubs = {k: ss.keygen(k) for k in (owner, dell_key, tokyo_key)}
    repo = tmp_path / "secrets"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t"); _git(repo, "config", "user.name", "t")
    (repo / "recipients.yaml").write_text(
        f"owner: {pubs[owner]}\ndevices:\n  device:llm-lab-dell: {pubs[dell_key]}\n"
        f"  device:tokyo-shared-dev-01: {pubs[tokyo_key]}\n")
    ss.write_sops_config(repo)
    (repo / "devices/llm-lab-dell").mkdir(parents=True)
    (repo / "devices/llm-lab-dell/cloudcli.target.yaml").write_text(
        f"env_file: {tmp_path}/cloudcli.env\nrestart: [cloudcli.service]\n")
    return SimpleNamespace(repo=repo, owner=owner, dell=dell_key, tokyo=tokyo_key, tmp=tmp_path)


def test_owner_sets_device_syncs_and_restarts_once(world):
    out = ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "sk-secret-1",
                       world.owner)
    assert out["action"] == "added"
    blob = (world.repo / out["bundle"]).read_text()
    assert "sk-secret-1" not in blob and "GW0_TOKEN" in blob     # value encrypted, name visible
    _git(world.repo, "add", "."); _git(world.repo, "commit", "-qm", "x")
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[0] == "git":
            return subprocess.run(argv, capture_output=True, text=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    cfg = {"repo": world.repo, "device": "device:llm-lab-dell", "key": world.dell}
    state = world.tmp / "names.json"
    first = ss.sync(cfg, pull=False, state=state, runner=runner)
    env = (world.tmp / "cloudcli.env")
    assert env.read_text() == 'GW0_TOKEN="sk-secret-1"\n'
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    assert first["restarted"] == ["cloudcli.service"]
    assert ss.read_names(state)["names"] == ["GW0_TOKEN"]
    assert "sk-secret-1" not in state.read_text()
    again = ss.sync(cfg, pull=False, state=state, runner=runner)
    assert again["restarted"] == [] and again["changed"] == []   # nothing changed, nothing restarted


def test_another_device_cannot_read_this_devices_bundle(world):
    out = ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "sk-2", world.owner)
    with pytest.raises(ss.SecretError, match="could not decrypt"):
        ss.decrypt_bundle(world.repo / out["bundle"], world.tokyo)
    assert ss.decrypt_bundle(world.repo / out["bundle"], world.dell) == {"GW0_TOKEN": "sk-2"}


def test_rotation_replaces_and_rm_removes(world):
    ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "old", world.owner)
    out = ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "new", world.owner)
    assert out["action"] == "replaced"
    assert ss.decrypt_bundle(world.repo / out["bundle"], world.dell)["GW0_TOKEN"] == "new"
    gone = ss.remove_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", world.owner)
    assert gone["names"] == []


def test_a_readable_key_is_refused(world):
    os.chmod(world.dell, 0o644)
    with pytest.raises(ss.SecretError, match="0600"):
        ss.sync({"repo": world.repo, "device": "device:llm-lab-dell", "key": world.dell},
                pull=False, state=world.tmp / "s.json")


# -- taking in what the owner collected by hand ---------------------------------

def test_a_messy_collection_reads_into_names(tmp_path):
    src = tmp_path / "collected"
    (src / "vault").mkdir(parents=True)
    (src / "vault" / "gw0-token").write_text("sk-gw0\n")
    (src / "vault" / "feishu.app_secret").write_text("fs-1")
    (src / "a6000.env").write_text('# comment\nexport GW0_TOKEN="sk-gw0"\nOPENAI_KEY=\'o-1\'\nnot a line\n')
    (src / "more.yaml").write_text("MINIMAX_KEY: mm-1\nnested: {a: 1}\n")
    values, skipped = ss.read_collected(src)
    assert values == {"GW0_TOKEN": "sk-gw0", "FEISHU_APP_SECRET": "fs-1",
                      "OPENAI_KEY": "o-1", "MINIMAX_KEY": "mm-1"}
    assert any("nested" in s for s in skipped) and any("not NAME=value" in s for s in skipped)
    assert "sk-gw0" not in json.dumps(skipped)


def test_import_to_the_vault_then_assign_to_a_device(world):
    src = world.tmp / "collected.env"
    src.write_text("GW0_TOKEN=sk-1\nFEISHU_SECRET=fs-1\n")
    values, _ = ss.read_collected(src)
    out = ss.import_values(world.repo, None, "main", values, world.owner)
    assert out["bundle"] == "vault/main.sops.yaml" and out["added"] == ["FEISHU_SECRET", "GW0_TOKEN"]
    # the vault is the owner's alone
    with pytest.raises(ss.SecretError):
        ss.decrypt_bundle(world.repo / out["bundle"], world.dell)
    given = ss.assign(world.repo, ["GW0_TOKEN"], "vault/main", "device:llm-lab-dell", "cloudcli",
                      world.owner)
    assert ss.decrypt_bundle(world.repo / given["bundle"], world.dell) == {"GW0_TOKEN": "sk-1"}
    inv = {r["bundle"]: r["names"] for r in ss.inventory(world.repo)}
    assert inv == {"devices/llm-lab-dell/cloudcli.sops.yaml": ["GW0_TOKEN"],
                   "vault/main.sops.yaml": ["FEISHU_SECRET", "GW0_TOKEN"]}
    assert "sk-1" not in json.dumps(inv)


def test_reimport_reports_replaced_and_unchanged(world):
    ss.import_values(world.repo, None, "main", {"A_KEY": "1", "B_KEY": "2"}, world.owner)
    out = ss.import_values(world.repo, None, "main", {"A_KEY": "1", "B_KEY": "3"}, world.owner)
    assert out["unchanged"] == ["A_KEY"] and out["replaced"] == ["B_KEY"]


def test_shred_removes_what_was_imported(tmp_path):
    src = tmp_path / "c"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "x").write_text("secret")
    (src / "y.env").write_text("A=1\n")
    assert ss.shred(src) == 2 and not src.exists()
