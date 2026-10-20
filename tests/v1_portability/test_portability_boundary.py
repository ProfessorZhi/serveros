"""The two X2 portability suites must not name or run a private legacy checkout.

This is the static half of the reproduction.  It fails on the RC 3f0240d files
because both define an external `WE_REPO` and one shells out with
`git -C <WE_REPO> archive|show`; it passes once the suites read only in-tree
workengine code and pinned public fixtures.
"""
from __future__ import annotations

import ast
import hashlib
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SUITES = (
    REPO / "tests" / "test_fleet_v1_t10_legacy_compatibility.py",
    REPO / "tests" / "test_v1_grant_os_principal_schema.py",
)
FORBIDDEN_NAMES = frozenset({"WE_REPO", "WE_HEAD", "WE14"})
FORBIDDEN_SUBPROCESS = frozenset({"run", "Popen", "check_output", "check_call",
                                  "call", "getoutput", "getstatusoutput"})


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: names {node.id}")
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "subprocess"
                and node.attr in FORBIDDEN_SUBPROCESS):
            found.append(
                f"line {node.lineno}: shells out with subprocess.{node.attr}")
    return found


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_suite_does_not_reference_a_private_legacy_checkout(path: Path) -> None:
    assert path.is_file(), path
    violations = _violations(path)
    assert not violations, (
        "a portability suite must not depend on an owner-private checkout:\n"
        + "\n".join(violations))


PINNED_FIXTURES = {
    "finalize_t10_verified.v1.json":
        "81f51a8580df37fff301e35a2a2c53e44c5fc9ed48ba0456183759629dfe0c95",
    "finalize_unsigned.v1.json":
        "5a7f55b85659e188e3c069afe848fccc0a6e11271901a7b802685bab4653de62",
    "lease_grant.v1.json":
        "cebab374e727adb60a8d0671f2f3092290dbeb5479bb62d8caccc3eaa8f10e8b",
}


@pytest.mark.parametrize("name,digest", PINNED_FIXTURES.items())
def test_public_fixture_is_pinned(name: str, digest: str) -> None:
    path = HERE / "fixtures" / name
    assert path.is_file(), path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, (
        f"{name} changed; update the pin only with a reviewed contract change")


def test_harness_loads_only_the_in_tree_workengine():
    import v1_portability_harness as harness

    package = harness.workengine()
    location = Path(package.__file__).resolve()
    assert location.is_relative_to(REPO), location
    authority = harness.in_tree_authority()
    assert authority is not None, (
        "rc/i1-v2-review-20261020 must ship public workengine/authority.py")
    assert Path(authority.__file__).resolve().is_relative_to(REPO)


@pytest.mark.parametrize("path", SUITES, ids=lambda p: p.name)
def test_suite_has_no_blanket_skip_or_xfail(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "pytest.xfail(" not in text, f"{path.name} still xfails a proof"
    assert "pytest.skip(" not in text, f"{path.name} still skips a proof"


def test_guard_catches_argv_but_not_the_configured_env_value():
    """The guard must not mistake its own configuration for a file access."""
    import private_path_guard as guard

    root = os.path.abspath("/owner/private/root")
    event = ("subprocess.Popen", (
        sys.executable, ["git", "-C", root, "show"], "/tmp",
        {guard.ROOTS_ENV: root}))
    values = guard._values(*event)
    assert any(guard._inside(value, root) for value in values), values

    clean = ("subprocess.Popen", (
        sys.executable, [sys.executable, "-c", "pass"], "/tmp",
        {guard.ROOTS_ENV: root}))
    values = guard._values(*clean)
    assert not any(guard._inside(value, root) for value in values), values
