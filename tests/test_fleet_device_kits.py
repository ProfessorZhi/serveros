"""Per-device kits: one declaration, four layers, generated and checked."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _kit_module():
    spec = importlib.util.spec_from_file_location("device_kit_under_test", ROOT / "distribution/device_kit.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


dk = _kit_module()


def test_committed_kits_match_the_declaration():
    assert dk.stale() == []


def test_every_device_has_core_family_and_monitor_and_only_the_hub_commands():
    doc = dk.declaration()
    for name in doc["devices"]:
        k = dk.kit(doc, name)
        assert {"core", "family", "monitor"} <= set(k["layers"])
        assert k["layers"]["monitor"], name
        assert ("commander" in k["layers"]) == (name == doc["hub"]["device"])
        assert ("hub_watch" in k["layers"]["monitor"]) == (name == doc["hub"]["device"])


def test_linux_devices_run_serverosd_and_native_core_lite_for_their_own_ref():
    doc = dk.declaration()
    for name, dev in doc["devices"].items():
        if dev["os"] != "linux":
            continue
        mon = dk.kit(doc, name)["layers"]["monitor"]
        assert "serverosd" in mon and "core_lite" in mon
        profile = json.loads((ROOT / mon["core_lite"]["profile"]["path"]).read_text())
        assert profile["node"] == dev["device_ref"] and profile["observe_only"] is True


def test_a_core_lite_profile_for_another_device_is_refused(monkeypatch):
    doc = dk.declaration()
    doc["devices"]["dell"]["monitor"]["core_lite"] = doc["devices"]["tokyo"]["monitor"]["core_lite"]
    with pytest.raises(dk.KitError, match="not device:llm-lab-dell"):
        dk.kit(doc, "dell")


def test_family_profiles_are_named_and_distinct():
    doc = dk.declaration()
    profiles = [d["family"]["profile"] for d in doc["devices"].values()]
    assert len(set(profiles)) == len(profiles)


def _args(**kw):
    base = dict(device="tokyo", ability_rev="fd0e98f0123456", ability_repo="/nonexistent/am",
                data_root="/srv/data", home="/home/vince", python="/usr/bin/python3",
                notify="journal", apply=False, enable=False)
    base.update(kw)
    return type("A", (), base)()


@pytest.mark.parametrize("device", ["tokyo", "dell"])
def test_linux_plan_renders_every_unit_without_placeholders(device):
    _, steps = dk.plan(_args(device=device))
    written = [s.write[1] for s in steps if s.write]
    assert written
    for body in written:
        assert not dk.re.findall(r"@[A-Z][A-Z_]*@", body)
    layers = [s.layer for s in steps]
    assert layers.index("core") < layers.index("family") < layers.index("monitor")
    assert any("rollout" in (s.argv or []) for s in steps)


def test_hub_monitor_carries_the_notify_target():
    _, steps = dk.plan(_args(notify="lark:user:ou_x"))
    unit = next(s.write[1] for s in steps if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert "--notify lark:user:ou_x" in unit


def test_a_first_install_without_a_destination_sends_to_the_owner(tmp_path):
    """Never a silent `journal`: with nothing chosen and nothing installed, the
    watcher sends to the owner lark-cli is logged in as (resolved at start, and
    refused loudly there if it cannot be)."""
    _, steps = dk.plan(_args(home=str(tmp_path), notify=None))
    unit = next(s.write[1] for s in steps if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert "--notify lark:owner" in unit


def test_an_update_keeps_the_destination_the_hub_was_installed_with(tmp_path):
    """The first install chose a destination; a later update without --notify
    re-renders the unit with that same one instead of refusing."""
    unit_dir = tmp_path / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    _, steps = dk.plan(_args(home=str(tmp_path), notify="lark:user:ou_first"))
    body = next(s.write[1] for s in steps if s.write and s.write[0].name == "serveros-fleet-watch.service")
    (unit_dir / "serveros-fleet-watch.service").write_text(body, encoding="utf-8")
    _, again = dk.plan(_args(home=str(tmp_path), notify=None))
    unit = next(s.write[1] for s in again if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert "--notify lark:user:ou_first" in unit


def test_an_explicit_destination_replaces_the_installed_one(tmp_path):
    unit_dir = tmp_path / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "serveros-fleet-watch.service").write_text(
        "ExecStart=/usr/bin/python3 -m fleet_hub_watch --config x --interval 30 --notify journal\n",
        encoding="utf-8")
    _, steps = dk.plan(_args(home=str(tmp_path), notify="lark:chat:oc_y"))
    unit = next(s.write[1] for s in steps if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert "--notify lark:chat:oc_y" in unit


def test_a_placeholder_in_an_installed_unit_is_not_a_destination(tmp_path):
    unit_dir = tmp_path / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "serveros-fleet-watch.service").write_text(
        "ExecStart=@HUB_PYTHON@ -m fleet_hub_watch --notify @HUB_NOTIFY@\n", encoding="utf-8")
    assert dk.installed_notify(unit_dir) is None


def test_the_same_ability_revision_builds_the_core_and_rolls_out_the_family():
    _, steps = dk.plan(_args())
    revs = {s.argv[s.argv.index(flag) + 1] for s in steps if s.argv
            for flag in ("--ability-rev", "--rev") if flag in s.argv}
    assert revs == {"fd0e98f0123456"}


def test_a_windows_kit_is_plan_only():
    k, steps = dk.plan(_args(device="work-fj"))
    assert isinstance(steps[0], str) and any("work-fj-windows" in line for line in steps)
    assert dk.main(["install", "--device", "work-fj", "--ability-rev", "abcdef12", "--apply"]) == 2


def test_missing_revision_or_data_root_is_refused():
    with pytest.raises(dk.KitError):
        dk.plan(_args(ability_rev=None))
    with pytest.raises(dk.KitError):
        dk.plan(_args(data_root=None))


def test_apply_runs_in_order_writes_units_and_enables_nothing_without_enable(tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.log"
    for tool in ("claude", "codex", "cmake", "install", "systemctl", "bash", "python3", "git"):
        exe = fakebin / tool
        exe.write_text(f"#!/bin/sh\necho \"{tool} $*\" >> {log}\n")
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    code = dk.main(["install", "--device", "dell", "--ability-rev", "fd0e98f0123456",
                    "--data-root", str(tmp_path / "data"), "--home", str(home),
                    "--python", str(fakebin / "python3"), "--apply"])
    assert code == 0
    calls = log.read_text().splitlines()
    assert not any("enable" in c for c in calls)
    assert (home / ".config/systemd/user/serveros-core-lite-publish.service").is_file()
    receipt = json.loads((home / ".local/state/serveros/device-kit/dell.receipt.json").read_text())
    assert any(s["result"].startswith("SKIPPED") for s in receipt["steps"])
    assert not any(s["result"] == "FAILED" for s in receipt["steps"])


def test_apply_stops_at_the_first_failure(tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    for tool, body in (("python3", "exit 1"), ("claude", "exit 0"), ("codex", "exit 0")):
        exe = fakebin / tool
        exe.write_text(f"#!/bin/sh\n{body}\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    code = dk.main(["install", "--device", "dell", "--ability-rev", "fd0e98f0123456",
                    "--data-root", str(tmp_path / "data"), "--home", str(home),
                    "--python", str(fakebin / "python3"), "--apply"])
    assert code == 1
    receipt = json.loads((home / ".local/state/serveros/device-kit/dell.receipt.json").read_text())
    assert receipt["steps"][-1]["result"] == "FAILED" and len(receipt["steps"]) == 1


def test_the_windows_device_gets_the_windows_collector_with_its_own_profile():
    k = dk.kit(dk.declaration(), "work-fj")
    core = k["layers"]["monitor"]["core_lite"]
    assert "Windows collector" in core["what"]
    profile = json.loads((ROOT / core["profile"]["path"]).read_text())
    assert profile["platform"] == "windows" and profile["node"] == "device:work-fj"
    plan = dk.windows_plan(k, "abc1234")
    assert any("register-publish-task.ps1" in line for line in plan)


def test_a_profile_for_another_platform_is_refused():
    doc = dk.declaration()
    doc["devices"]["work-fj"]["monitor"]["core_lite"] = "native/node-core/core-lite/profiles/dell-fjd.profile.json"
    doc["devices"]["work-fj"]["device_ref"] = "device:llm-lab-dell"
    with pytest.raises(dk.KitError, match="not windows"):
        dk.kit(doc, "work-fj")


def test_configuration_outside_the_repository_is_written_into_the_install_notes():
    doc = dk.declaration()
    work_fj = (ROOT / "distribution/devices/work-fj/README.md").read_text()
    assert "claude-vince.huang" in work_fj and "core_snapshot_path" in work_fj
    tokyo = (ROOT / "distribution/devices/tokyo/README.md").read_text()
    assert "discovery_timeout_seconds: 15" in tokyo
    assert dk.kit(doc, "work-fj")["principal"] == "Vince.Huang"


def test_update_without_apply_changes_nothing(tmp_path, capsys):
    code = dk.main(["update", "--device", "dell", "--home", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "pull --ff-only origin main" in out and "dry run" in out
    assert not (tmp_path / ".local/state/serveros/device-versions.json").exists()


def test_a_windows_device_is_sent_to_update_ps1():
    assert dk.main(["update", "--device", "work-fj"]) == 2


def test_update_apply_reinstalls_with_new_code_and_records_versions(tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.log"
    for tool, body in (("python3", f'echo "python3 $*" >> {log}'),
                       ("systemctl", f'echo "systemctl $*" >> {log}; exit 3')):
        exe = fakebin / tool
        exe.write_text(f"#!/bin/sh\n{body}\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    code = dk.main(["update", "--device", "dell", "--no-pull", "--ability-rev", "fd0e98f0123456",
                    "--home", str(home), "--python", str(fakebin / "python3"), "--apply"])
    assert code == 0
    calls = log.read_text()
    assert "device_kit.py install --device dell --ability-rev fd0e98f0123456" in calls
    # The update restarts nothing itself; it only asks what is running.
    assert "systemctl --user restart" not in calls
    assert "systemctl --user is-active" in calls
    record = json.loads((home / ".local/state/serveros/device-versions.json").read_text())
    assert record["schema"] == "serveros/device-versions.v1"
    assert record["ability_family"] == {"profile": "dell-gpu-compute", "revision": "fd0e98f0123456"}
    assert record["core_plugin"]["name"] == "hwz-server-os-linux-work"
    assert dk.main(["versions", "--device", "dell", "--home", str(home)]) == 0


def test_a_failed_install_records_no_versions(tmp_path, monkeypatch):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    exe = fakebin / "python3"
    exe.write_text("#!/bin/sh\nexit 1\n")
    exe.chmod(0o755)
    home = tmp_path / "home"
    code = dk.main(["update", "--device", "dell", "--no-pull", "--ability-rev", "fd0e98f0123456",
                    "--home", str(home), "--python", str(exe), "--apply"])
    assert code == 1
    assert not (home / ".local/state/serveros/device-versions.json").exists()


def test_alerts_can_go_to_a_named_group_and_an_update_keeps_it(tmp_path):
    unit_dir = tmp_path / ".config/systemd/user"
    unit_dir.mkdir(parents=True)
    args = _args(home=str(tmp_path), notify="lark:owner")
    args.alerts_to = "lark:chat-name:ServerOS 播报"
    _, steps = dk.plan(args)
    body = next(s.write[1] for s in steps if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert '--notify lark:owner --alerts-to "lark:chat-name:ServerOS 播报"' in body
    (unit_dir / "serveros-fleet-watch.service").write_text(body, encoding="utf-8")
    _, again = dk.plan(_args(home=str(tmp_path), notify=None))
    unit = next(s.write[1] for s in again if s.write and s.write[0].name == "serveros-fleet-watch.service")
    assert '--alerts-to "lark:chat-name:ServerOS 播报"' in unit
