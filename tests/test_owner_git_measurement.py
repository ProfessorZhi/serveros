"""Real portable Git measurement: no authority or security emulation."""
import shutil
import subprocess
from pathlib import Path

import pytest

from workengine.owner_context import measure_git


@pytest.fixture
def repo(tmp_path):
    git = Path(shutil.which("git")).resolve()
    repo = tmp_path / "repo"
    repo.mkdir()
    def run(*args):
        return subprocess.run([str(git), "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()
    run("init")
    (repo / "fixture.txt").write_text("fixture")
    run("add", "fixture.txt")
    run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
    return repo, git, run, run("rev-parse", "HEAD")


def test_git_measurement_never_executes_repository_fsmonitor(repo, tmp_path):
    directory, git, run, revision = repo
    touched = tmp_path / "hook-effect"
    hook = directory / ".git" / "fixture-monitor"
    hook.write_text('#!/bin/sh\nprintf effect > "' + touched.as_posix() + '"\n')
    hook.chmod(0o700)
    run("config", "core.fsmonitor", hook.as_posix())
    run("status", "--porcelain")
    assert touched.exists(), "fixture must exercise a real Git fsmonitor"
    touched.unlink()
    measured = measure_git(directory, git)
    assert measured == {"revision": revision, "dirty": False}
    assert not touched.exists()


def test_git_measurement_ignores_inherited_repository_redirection(repo, tmp_path, monkeypatch):
    directory, git, run, revision = repo
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run([str(git), "-C", str(other), "init"], check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert measure_git(directory, git) == {"revision": revision, "dirty": False}


def test_git_measurement_ignores_inherited_config_and_path(repo, tmp_path, monkeypatch):
    directory, git, run, revision = repo
    touched = tmp_path / "environment-effect"
    hook = directory / ".git" / "environment-monitor"
    hook.write_text('#!/bin/sh\nprintf effect > "' + touched.as_posix() + '"\n')
    hook.chmod(0o700)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", hook.as_posix())
    monkeypatch.setenv("PATH", str(tmp_path / "missing-bin"))
    assert measure_git(directory, git) == {"revision": revision, "dirty": False}
    assert not touched.exists()
