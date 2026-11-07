"""A CloudCLI that runs Claude from its own config dir gets the family too."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("device_kit", ROOT / "distribution/device_kit.py")
dk = importlib.util.module_from_spec(spec)
sys.modules.setdefault("device_kit", dk)
spec.loader.exec_module(dk)


def _proc(root: Path, pid: str, cmd: str, env: dict) -> None:
    d = root / pid
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    (d / "environ").write_bytes("\0".join(f"{k}={v}" for k, v in env.items()).encode())


def test_config_dir_is_read_from_the_running_cloudcli(tmp_path):
    proc = tmp_path / "proc"
    _proc(proc, "101", "node /usr/lib/node_modules/@yalehwang/cloudcli/server/index.js",
          {"HOME": "/home/fjd", "CLAUDE_CONFIG_DIR": "/srv/cloudcli/claude"})
    _proc(proc, "102", "node /opt/other/app.js", {"CLAUDE_CONFIG_DIR": "/elsewhere"})
    _proc(proc, "103", "cloudcli serve", {"HOME": "/home/svc"})
    assert dk.cloudcli_claude_dirs(tmp_path, proc) == [Path("/srv/cloudcli/claude"),
                                                       Path("/home/svc/.claude")]


def test_dell_plan_rolls_out_into_cloudclis_dir(tmp_path):
    doc = dk.declaration()
    k = dk.kit(doc, "dell")
    ctx = dk.Context(ability_repo=tmp_path / "am", ability_rev="a" * 40, data_root=tmp_path / "data",
                     home=tmp_path / "home", python="python3", notify=None,
                     cloudcli_claude_dir=tmp_path / "cc-claude")
    argvs = [s.argv for s in dk.linux_steps(k, ctx) if s.layer == "family"]
    assert any("--claude-config-dir" in a and str(tmp_path / "cc-claude") in a
               and "rollout" in a for a in argvs)
    assert any("--claude-config-dir" in a and "readback" in a for a in argvs)


def test_same_dir_as_the_shell_is_not_rolled_out_twice(tmp_path):
    k = dk.kit(dk.declaration(), "dell")
    home = tmp_path / "home"
    ctx = dk.Context(ability_repo=tmp_path / "am", ability_rev="a" * 40, data_root=tmp_path / "data",
                     home=home, python="python3", notify=None, cloudcli_claude_dir=home / ".claude")
    argvs = [s.argv for s in dk.linux_steps(k, ctx) if s.layer == "family"]
    assert not any("--claude-config-dir" in a for a in argvs)


def test_an_already_released_revision_is_not_built_again(tmp_path):
    k = dk.kit(dk.declaration(), "dell")
    rev = "c" * 40
    data = tmp_path / "data"
    core = k["layers"]["core"]
    (data / "runtime-assets/hwz-server-os" / core["composition"] / "revisions"
     / f"am-{rev[:12]}").mkdir(parents=True)
    ctx = dk.Context(ability_repo=tmp_path / "am", ability_rev=rev, data_root=data,
                     home=tmp_path / "home", python="python3", notify=None)
    titles = [s.title for s in dk.linux_steps(k, ctx) if s.layer == "core"]
    assert any("already released" in t for t in titles)
    assert not any(t.startswith("build ") or t.startswith("promote") for t in titles)


def test_the_agent_cli_is_found_outside_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    bin_dir = tmp_path / ".local/bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "claude").write_text("#!/bin/sh\n")
    (bin_dir / "claude").chmod(0o755)
    assert dk.agent_bin("claude", tmp_path) == str(bin_dir / "claude")
    assert dk.agent_bin("codex", tmp_path) == "codex"


def test_the_family_rollout_starts_from_the_pinned_checkout(tmp_path):
    k = dk.kit(dk.declaration(), "dell")
    rev = "d" * 40
    ctx = dk.Context(ability_repo=tmp_path / "am", ability_rev=rev, data_root=tmp_path / "data",
                     home=tmp_path / "home", python="python3", notify=None)
    family = [s.argv for s in dk.linux_steps(k, ctx) if s.layer == "family"]
    assert family[0] == ["git", "-C", str(tmp_path / "am"), "checkout", "--detach", "-q", rev]
    assert "rollout" in family[1]
