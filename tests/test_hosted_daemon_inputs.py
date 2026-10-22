"""CI input authoring uses the real roots loader and never invents catalogue data."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
DRIVER = ROOT / ".github" / "scripts" / "hosted_daemon_inputs.py"
spec = importlib.util.spec_from_file_location("hosted_daemon_inputs_tests", DRIVER)
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


def directories(tmp_path):
    work, evidence = tmp_path / "work", tmp_path / "evidence"
    work.mkdir(mode=0o700)
    evidence.mkdir(mode=0o700)
    return work, evidence


def test_real_loader_is_bound_without_a_machine_profile_and_environment_is_restored(tmp_path):
    work, evidence = directories(tmp_path)
    previous = dict(os.environ)
    values, record = inputs.prepare(work, evidence)
    assert dict(os.environ) == previous
    assert "HOME" not in values
    assert record["home_changed"] is False
    assert record["profile_required"] is False
    assert record["root_contract"]["bound"] is True
    assert record["root_contract"]["unconfigured"] == []
    for key in ("DATA_ROOT", "WORKSPACE_ROOT", "ARTIFACTS_ROOT", "TOOLCHAINS_ROOT", "SERVEROS_STATE_DIR"):
        assert Path(values[key]).is_dir()
        assert Path(values[key]).is_relative_to(work)
    assert record["catalogue"]["present"] is False
    assert record["catalogue"]["entries"] is None
    assert not Path(record["catalogue"]["path"]).exists()
    assert json.loads((evidence / "bootstrap-inputs.json").read_text()) == record


def test_cli_authors_real_environment_for_a_separate_product_process(tmp_path):
    work, evidence = directories(tmp_path)
    command_file = tmp_path / "github.env"
    command_file.touch(mode=0o600)
    completed = subprocess.run([sys.executable, "-B", str(DRIVER), "--work-root", str(work),
        "--evidence-dir", str(evidence), "--github-env", str(command_file)],
        capture_output=True, text=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    values = dict(line.split("=", 1) for line in command_file.read_text().splitlines())
    probe = "import paths,json; p=paths.Paths(); print(json.dumps({'bound':p.bound,'state':str(p.root('state'))}))"
    env = {**os.environ, **values, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT / "runtime")}
    actual = subprocess.run([sys.executable, "-B", "-c", probe], env=env,
        capture_output=True, text=True, timeout=20)
    assert actual.returncode == 0, actual.stderr
    observed = json.loads(actual.stdout)
    assert observed == {"bound": True, "state": values["SERVEROS_STATE_DIR"]}


def test_existing_data_or_evidence_is_preserved(tmp_path):
    work, evidence = directories(tmp_path)
    (work / "d").mkdir()
    sentinel = work / "d" / "foreign"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        inputs.prepare(work, evidence)
    assert sentinel.read_text() == "preserve"
    assert not (evidence / "bootstrap-inputs.json").exists()


def test_public_or_symlinked_root_is_refused(tmp_path):
    work, evidence = directories(tmp_path)
    work.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        inputs.prepare(work, evidence)
    work.chmod(0o700)
    link = tmp_path / "alias"
    link.symlink_to(work, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        inputs.prepare(link, evidence)
