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
    for tool in ("claude", "codex", "cmake", "install", "systemctl", "bash", "python3"):
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


def test_a_device_without_a_resource_collector_says_so_instead_of_claiming_one():
    k = dk.kit(dk.declaration(), "work-fj")
    assert k["layers"]["monitor"]["core_lite"]["what"].startswith("GAP:")
