"""Real portable Git measurement: no authority or security emulation."""
import shutil
import os
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


@pytest.mark.parametrize("helper", ["clean", "process"])
def test_git_measurement_refuses_content_filters_before_helper_execution(repo, tmp_path, helper):
    directory, git, run, revision = repo
    tracked = directory / "fixture.txt"
    (directory / ".gitattributes").write_text("fixture.txt filter=fixture\n")
    run("add", ".gitattributes")
    run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "attributes")
    marker = tmp_path / (helper + "-effect")
    hook = directory / ".git" / (helper + "-helper")
    hook.write_text('#!/bin/sh\nprintf effect > "' + marker.as_posix() + '"\n' + ("cat\n" if helper == "clean" else "exit 1\n"))
    hook.chmod(0o700)
    run("config", "filter.fixture." + helper, hook.as_posix())
    # Same byte count forces content conversion, unlike a size-only dirty test.
    tracked.write_text("changed")
    os.utime(tracked, (1700000000, 1700000000))
    try:
        measure_git(directory, git)
    except ValueError as exc:
        refusal = str(exc)
    else:
        refusal = ""
    assert not marker.exists(), "Git content conversion executed the repository helper"
    assert "OWNER_REPOSITORY_HELPER_REFUSED" in refusal


@pytest.mark.parametrize("key", ["remote.fixture.promisor", "extensions.partialClone", "core.alternateRefsCommand", "diff.fixture.textconv"])
def test_git_measurement_refuses_lazy_or_helper_configuration(repo, key):
    directory, git, run, revision = repo
    run("config", key, "true")
    with pytest.raises(ValueError, match="OWNER_REPOSITORY_HELPER_REFUSED"):
        measure_git(directory, git)


@pytest.mark.parametrize("boundary", ["config", "diff-index"])
def test_config_change_after_precheck_cannot_enable_helper_execution(repo, tmp_path, monkeypatch, boundary):
    directory, executable, run, revision = repo
    marker = tmp_path / "raced-filter-effect"
    hook = directory / ".git" / "raced-filter"
    hook.write_text('#!/bin/sh\nprintf effect > "' + marker.as_posix() + '"\ncat\n')
    hook.chmod(0o700)
    original = subprocess.run
    changed = False
    def after_config(args, *extra, **kwargs):
        nonlocal changed
        answer = original(args, *extra, **kwargs)
        if not changed and ((boundary == "config" and "config" in args and "--list" in args)
                            or (boundary == "diff-index" and "diff-index" in args)):
            changed = True
            run("config", "filter.raced.clean", hook.as_posix())
            (directory / ".gitattributes").write_text("fixture.txt filter=raced\n")
            (directory / "fixture.txt").write_text("changed")
            os.utime(directory / "fixture.txt", (1700000000, 1700000000))
        return answer
    monkeypatch.setattr(subprocess, "run", after_config)
    try:
        result = measure_git(directory, executable)
    except ValueError:
        result = None
    assert changed
    assert not marker.exists(), "mutable configuration enabled a helper after the precheck"
    assert result is None or result["dirty"] is True


@pytest.mark.parametrize("change", ["clean", "dirty", "staged", "deleted", "untracked"])
def test_safe_plumbing_preserves_tracked_staged_and_untracked_dirty_contract(repo, change):
    directory, executable, run, revision = repo
    if change in {"dirty", "staged"}:
        (directory / "fixture.txt").write_text("changed")
    if change == "staged": run("add", "fixture.txt")
    if change == "deleted": (directory / "fixture.txt").unlink()
    if change == "untracked": (directory / "new.txt").write_text("untracked")
    assert measure_git(directory, executable) == {"revision":revision, "dirty": change != "clean"}


@pytest.mark.parametrize("conversion", ["text", "eol=crlf", "working-tree-encoding=UTF-16", "ident"])
def test_raw_measurement_refuses_unsupported_attribute_conversion(repo, conversion):
    directory, executable, run, revision = repo
    (directory / ".gitattributes").write_text("fixture.txt " + conversion + "\n")
    if conversion in {"text", "eol=crlf"}:
        (directory / "fixture.txt").write_bytes(b"fixture\r\n")
    with pytest.raises(ValueError, match="OWNER_REPOSITORY_UNSUPPORTED"):
        measure_git(directory, executable)


def test_raw_measurement_refuses_submodule_index_entries(repo):
    directory, executable, run, revision = repo
    run("update-index", "--add", "--cacheinfo", "160000," + revision + ",submodule")
    with pytest.raises(ValueError, match="OWNER_REPOSITORY_UNSUPPORTED"):
        measure_git(directory, executable)


def test_raw_measurement_bounds_tracked_content_before_reading_it(repo):
    directory, executable, run, revision = repo
    with (directory / "fixture.txt").open("r+b") as handle:
        handle.truncate(129 * 1024 * 1024)
    with pytest.raises(ValueError, match="OWNER_REPOSITORY_BOUND"):
        measure_git(directory, executable)


@pytest.mark.parametrize("key,value", [("core.autocrlf", "true"), ("core.autocrlf", "input"), ("core.eol", "crlf")])
def test_raw_measurement_refuses_repository_eol_conversion(repo, key, value):
    directory, executable, run, revision = repo
    run("config", key, value)
    (directory / ".gitattributes").write_text("fixture.txt text\n")
    (directory / "fixture.txt").write_bytes(b"fixture\r\n")
    with pytest.raises(ValueError, match="OWNER_REPOSITORY_UNSUPPORTED"):
        measure_git(directory, executable)


def test_declared_lf_json_fixture_layout_has_identity_conversion(repo):
    directory, executable, run, revision = repo
    fixtures = directory / "tests" / "v1_portability" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / ".gitattributes").write_bytes(b"*.json text eol=lf\n")
    for index in range(4):
        (fixtures / f"fixture-{index}.json").write_bytes(b'{"fixture":true}\n')
    run("add", "tests")
    run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "declared LF fixtures")
    revision = run("rev-parse", "HEAD")
    assert measure_git(directory, executable) == {"revision":revision, "dirty":False}
    (fixtures / "fixture-0.json").write_bytes(b'{"fixture":false}\n')
    assert measure_git(directory, executable) == {"revision":revision, "dirty":True}
