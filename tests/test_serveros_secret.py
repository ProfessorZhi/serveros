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


def test_device_bundles_name_their_recipients_explicitly(world):
    # even with no .sops.yaml at all, a device bundle reaches owner + device
    (world.repo / ".sops.yaml").unlink()
    out = ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "v", world.owner)
    assert ss.decrypt_bundle(world.repo / out["bundle"], world.dell) == {"GW0_TOKEN": "v"}
    vault = ss.import_values(world.repo, None, "main", {"X_KEY": "1"}, world.owner)
    with pytest.raises(ss.SecretError):
        ss.decrypt_bundle(world.repo / vault["bundle"], world.dell)


def test_per_file_import_keeps_same_names_apart(world):
    src = world.tmp / "collected"
    src.mkdir()
    (src / "prod.env").write_text("LITELLM_MASTER_KEY=prod\n")
    (src / "lit-test.env").write_text("LITELLM_MASTER_KEY=test\n")
    out = ss.import_per_file(world.repo, src, None, world.owner)
    got = {b["bundle"]: ss.decrypt_bundle(world.repo / b["bundle"], world.owner)["LITELLM_MASTER_KEY"]
           for b in out["bundles"]}
    assert got == {"vault/lit-test-env.sops.yaml": "test", "vault/prod-env.sops.yaml": "prod"}


def test_the_sync_unit_can_find_sops_in_local_bin():
    unit = (ROOT / "distribution/keycenter/serveros-secrets-sync.service.template").read_text()
    assert "Environment=PATH=%h/.local/bin" in unit


# -- comparing and cleaning up without anyone seeing a value ---------------------

def test_fingerprints_match_equal_values_and_hide_them():
    a = ss.fingerprint("GW0_TOKEN", "sk-abcdefgh")
    assert a == ss.fingerprint("GW0_TOKEN", "sk-abcdefgh") and a.startswith("fp1:")
    assert a != ss.fingerprint("GW0_TOKEN", "sk-abcdefgX")
    assert a != ss.fingerprint("OTHER", "sk-abcdefgh")        # keyed by name
    assert "abcdefgh" not in a


def test_scan_then_redact_in_place_never_prints_a_value(tmp_path):
    entries = [{"name": "FJ_TOKEN", "bundle": "devices/work-fj/claude", "value": "sk-fj-123456"},
               {"name": "QUOTE_KEY", "bundle": "vault/main", "value": 'ab"cd\\efgh'},
               {"name": "PIN", "bundle": "vault/main", "value": "1234"}]
    root = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("sk-fj-123456")      # never touched
    settings = root / "settings.json.bak"
    settings.write_text('{"env": {"ANTHROPIC_AUTH_TOKEN": "sk-fj-123456"}, "k": "ab\\"cd\\\\efgh"}')
    log = root / "s.jsonl"
    log.write_bytes(b"x sk-fj-123456 y sk-fj-123456\r\n")
    dry = ss.scan([root], entries)
    out = json.dumps(dry)
    assert "sk-fj" not in out and "efgh" not in out
    hits = {Path(f["file"]).name: {h["name"]: h["count"] for h in f["hits"]} for f in dry["files"]}
    assert hits == {"settings.json.bak": {"FJ_TOKEN": 1, "QUOTE_KEY": 1}, "s.jsonl": {"FJ_TOKEN": 2}}
    assert dry["too_short_to_search"] == ["vault/main:PIN"] and not dry["redacted"]
    assert "sk-fj-123456" in settings.read_text()             # dry run wrote nothing
    done = ss.scan([root], entries, write=True)
    assert done["redacted"] and done["occurrences"] == 4
    assert settings.read_text() == ('{"env": {"ANTHROPIC_AUTH_TOKEN": "<redacted:FJ_TOKEN>"}, '
                                    '"k": "<redacted:QUOTE_KEY>"}')
    assert log.read_bytes() == b"x <redacted:FJ_TOKEN> y <redacted:FJ_TOKEN>\r\n"
    assert (root / ".git" / "config").read_text() == "sk-fj-123456"
    assert ss.scan([root], entries)["files"] == []


def test_values_come_from_the_device_bundles_or_the_owner_vault(world, tmp_path):
    ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "sk-device-1", world.owner)
    ss.import_values(world.repo, None, "main", {"VAULT_KEY": "vault-value-1"}, world.owner)
    _git(world.repo, "add", "."); _git(world.repo, "commit", "-qm", "x")
    cfg = tmp_path / "secrets.yaml"
    cfg.write_text(f"repo: {world.repo}\ndevice: device:llm-lab-dell\nkey: {world.dell}\n")
    mine, unopened = ss.held_values(config=cfg)
    assert [(e["name"], e["bundle"]) for e in mine] == [("GW0_TOKEN", "devices/llm-lab-dell/cloudcli")]
    owner, unopened = ss.held_values(repo=world.repo, key=world.owner)
    assert {e["name"] for e in owner} == {"GW0_TOKEN", "VAULT_KEY"} and unopened == []
    # the device key opens its own bundle and not the vault
    _, unopened = ss.held_values(repo=world.repo, key=world.dell)
    assert unopened == ["vault/main"]
    env = tmp_path / "a.env"
    env.write_text("GW0_TOKEN=sk-device-1\n")
    from_file, _ = ss.held_values(file=env)
    assert ss.fingerprint("GW0_TOKEN", from_file[0]["value"]) == ss.fingerprint("GW0_TOKEN", mine[0]["value"])


def test_the_fingerprint_command_prints_names_and_fingerprints_only(tmp_path, capsys):
    env = tmp_path / "x.env"
    env.write_text("GW0_TOKEN=sk-secret-value-9\n")
    assert ss.main(["fingerprint", "GW0_TOKEN", "MISSING", "--of", str(env)]) == 0
    out = capsys.readouterr().out
    assert "sk-secret" not in out
    data = json.loads(out)
    assert data["fingerprints"][0]["fingerprint"] == ss.fingerprint("GW0_TOKEN", "sk-secret-value-9")
    assert data["not_found"] == ["MISSING"]


# -- Windows: line ends, ACLs, scheduled tasks, a bare environment ---------------

def test_sync_writes_lf_and_rewrites_a_crlf_copy_once(world):
    ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "v1", world.owner)
    env = world.tmp / "cloudcli.env"
    env.write_bytes(b'GW0_TOKEN="v1"\r\n')
    ok = lambda argv: SimpleNamespace(returncode=0, stdout="", stderr="")
    cfg = {"repo": world.repo, "device": "device:llm-lab-dell", "key": world.dell}
    first = ss.sync(cfg, pull=False, state=world.tmp / "s.json", runner=ok)
    assert first["changed"] == ["cloudcli"] and env.read_bytes() == b'GW0_TOKEN="v1"\n'
    assert ss.sync(cfg, pull=False, state=world.tmp / "s.json", runner=ok)["changed"] == []


def test_a_failed_restart_is_reported_not_claimed(world):
    ss.set_value(world.repo, "device:llm-lab-dell", "cloudcli", "GW0_TOKEN", "v1", world.owner)
    fail = lambda argv: SimpleNamespace(returncode=5, stdout="", stderr="")
    cfg = {"repo": world.repo, "device": "device:llm-lab-dell", "key": world.dell}
    out = ss.sync(cfg, pull=False, state=world.tmp / "s.json", runner=fail)
    assert out["restarted"] == [] and "restart cloudcli.service failed" in out["errors"][0]["error"]


def test_scheduled_tasks_are_a_restart_form(tmp_path):
    target = tmp_path / "claude.target.yaml"
    target.write_text('env_file: ~/x.env\nrestart: ["task:\\\\ServerOS\\\\ServerOS Secrets sync"]\n')
    got = ss._target(tmp_path / "claude.sops.yaml")
    assert ss.restart_commands(got["restart"][0]) == [
        ["schtasks", "/End", "/TN", "\\ServerOS\\ServerOS Secrets sync"],
        ["schtasks", "/Run", "/TN", "\\ServerOS\\ServerOS Secrets sync"]]
    target.write_text('env_file: ~/x.env\nrestart: ["task:a & calc"]\n')
    with pytest.raises(ss.SecretError, match="scheduled tasks"):
        ss._target(tmp_path / "claude.sops.yaml")


def _fake_windows(monkeypatch, acl_lines):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        if argv[0] == "whoami":
            return SimpleNamespace(returncode=0, stdout='"corp\\vince","S-1-5-21-1-2-3-1001"\r\n', stderr="")
        if argv[0] == "powershell":
            return SimpleNamespace(returncode=0, stdout="\n".join(acl_lines), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    ss._windows_sid.cache_clear()
    monkeypatch.setattr(ss, "IS_WINDOWS", True)
    monkeypatch.setattr(ss, "_run", run)
    return calls


def test_windows_key_check_reads_the_acl_by_sid(monkeypatch, tmp_path):
    key = tmp_path / "age.key"
    key.write_text("AGE-SECRET-KEY-1")
    me = "S-1-5-21-1-2-3-1001"
    calls = _fake_windows(monkeypatch, [f"owner {me}", f"allow {me}", "allow S-1-5-18"])
    ss._check_key(key)                                           # owner + SYSTEM: fine
    ps = [kw for a, kw in calls if a[0] == "powershell"]
    assert ps[0]["env"] == {"KC_ACL_PATH": str(key)}            # path passed by env, not quoted in
    _fake_windows(monkeypatch, [f"owner {me}", f"allow {me}", "allow S-1-5-32-545"])
    with pytest.raises(ss.SecretError, match="S-1-5-32-545.*icacls"):
        ss._check_key(key)
    _fake_windows(monkeypatch, [])
    with pytest.raises(ss.SecretError, match="ACL could not be read"):
        ss._check_key(key)
    ss._windows_sid.cache_clear()


def test_windows_private_files_get_an_owner_only_acl_before_content(monkeypatch, tmp_path):
    calls = _fake_windows(monkeypatch, [])
    target = tmp_path / "claude.env"
    ss._atomic_write(target, "A=1\nB=2\n", 0o600)
    icacls = [a for a, _ in calls if a[0] == "icacls"]
    assert len(icacls) == 1 and icacls[0][2:] == ["/inheritance:r", "/grant:r", "*S-1-5-21-1-2-3-1001:F"]
    assert target.read_bytes() == b"A=1\nB=2\n"
    calls.clear()
    ss._atomic_write(tmp_path / "bundle.sops.yaml", "x: 1\n", 0o644)   # ciphertext: no ACL change
    assert not [a for a, _ in calls if a[0] == "icacls"]
    ss._windows_sid.cache_clear()


def test_tool_env_puts_configured_dirs_first_and_keeps_path(monkeypatch, tmp_path):
    extra = tmp_path / "bin"
    extra.mkdir()
    monkeypatch.setenv("PATH", "/opt/a")
    ss.tool_env([str(extra)])
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[:2] == [str(extra), "/opt/a"] and os.environ.get("HOME")


def test_subprocess_output_is_read_as_utf8():
    done = ss._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write('密钥 ok'.encode())"])
    assert done.stdout == "密钥 ok"


def test_the_windows_kit_ships_the_sync_task_it_names():
    kit = json.loads((ROOT / "distribution/devices/work-fj/DEVICE.json").read_text(encoding="utf-8"))
    sync = kit["layers"]["monitor"]["secrets_sync"]
    paths = [t["path"] for t in sync["templates"]]
    assert paths == ["distribution/keycenter/windows/register-sync-task.ps1",
                     "distribution/keycenter/windows/sync-secrets.ps1",
                     "distribution/keycenter/windows/run-hidden-wait.vbs"]
    task = (ROOT / paths[0]).read_text(encoding="ascii")
    assert "wscript.exe" in task and "IgnoreNew" in task       # no console window, no pile-up
    update = (ROOT / "distribution/windows/update.ps1").read_text(encoding="utf-8")
    assert "register-sync-task.ps1" in update
