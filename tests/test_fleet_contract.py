from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "fleet" / "device-contract.yaml"


def load():
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


def test_public_and_forbidden_device_fields_do_not_overlap():
    c = load()
    public = set(c["identity"]["public_fields"])
    forbidden = set(c["forbidden_public_fields"])
    assert public.isdisjoint(forbidden)


def test_dynamic_state_is_under_serveros_state_root():
    c = load()
    for key in ("current_template", "ability_snapshot_template"):
        assert c["dynamic_state"][key].startswith("<STATE_ROOT>/")


def test_device_id_contract_is_stable_and_path_safe():
    pattern = re.compile(load()["identity"]["id_pattern"])
    for good in ("linux-server", "dell-work", "device-3"):
        assert pattern.fullmatch(good)
    for bad in ("../server", "Dell Work", "10.0.0.1", ""):
        assert not pattern.fullmatch(bad)
