"""The two X2 portability suites must not name or run a private legacy checkout.

This is the static half of the reproduction.  It fails on the RC 3f0240d files
because both define an external `WE_REPO` and one shells out with
`git -C <WE_REPO> archive|show`; it passes once the suites read only in-tree
workengine code and pinned public fixtures.
"""
from __future__ import annotations

import ast
import codecs
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import v1_portability_harness as H  # noqa: E402
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
    raw = path.read_bytes()
    canonical = H.canonical_fixture_bytes(name)
    # The only accepted on-disk representations are the canonical LF bytes and
    # a PURE CRLF transcription of them (Windows core.autocrlf). Mixed endings,
    # a BOM, a lone CR or a changed field are rejected by the canonicalizer or
    # by the pin below.
    assert raw in (canonical, canonical.replace(b"\n", b"\r\n")), (
        f"{name} has a non-portable byte representation")
    assert hashlib.sha256(canonical).hexdigest() == digest, (
        f"{name} changed; update the pin only with a reviewed contract change")


def test_crlf_is_normalized_to_the_pinned_canonical_bytes():
    canonical = H.fixture_bytes(H.T10_FIXTURE)
    crlf = canonical.replace(b"\n", b"\r\n")
    assert crlf != canonical
    assert H.canonical_json_bytes(crlf) == canonical
    assert hashlib.sha256(H.canonical_json_bytes(crlf)).hexdigest() == (
        PINNED_FIXTURES[H.T10_FIXTURE])


def test_changed_field_is_not_laundered_by_crlf():
    canonical = H.fixture_bytes(H.T10_FIXTURE)
    changed = canonical.replace(b'"verdict": "PASS"', b'"verdict": "FAIL"')
    assert changed != canonical
    crlf_changed = changed.replace(b"\n", b"\r\n")
    assert hashlib.sha256(H.canonical_json_bytes(crlf_changed)).hexdigest() != (
        PINNED_FIXTURES[H.T10_FIXTURE])


@pytest.mark.parametrize("transform,code", (
    (lambda canonical: codecs.BOM_UTF8 + canonical, "BOM"),
    (lambda canonical: codecs.BOM_UTF16_LE + canonical, "BOM"),
    (lambda canonical: canonical.replace(b"\n", b"\r\n").replace(
        b"\r\n", b"\r", 1), "LONE_CR"),
    (lambda canonical: canonical.replace(b"\n", b"\r\n", 1), "MIXED_EOL"),
))
def test_non_canonical_byte_forms_are_rejected(transform, code):
    canonical = H.fixture_bytes(H.T10_FIXTURE)
    with pytest.raises(H.FixtureBytesError) as exc:
        H.canonical_json_bytes(transform(canonical))
    assert code in str(exc.value), str(exc.value)


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


@pytest.mark.parametrize("candidate,root,expected", (
    # Windows drive paths, case-insensitive, with both separators.
    (r"D:\owner\private\root\evil", r"D:\owner\private\root", True),
    ("D:/owner/private/root/evil", r"D:\owner\private\root", True),
    (r"d:\OWNER\PRIVATE\ROOT\evil", r"D:\owner\private\root", True),
    (r"D:\owner\private\root2\evil", r"D:\owner\private\root", False),
    (r"D:\owner\private\root\..\other", r"D:\owner\private\root", False),
    # Long-path drive alias must not escape.
    (r"\\?\D:\owner\private\root\evil", r"D:\owner\private\root", True),
    # Drive-relative and bare drive are not the absolute root.
    (r"D:relative\evil", r"D:\owner\private\root", False),
    (r"D:", r"D:\owner\private\root", False),
    # A different drive is a different path family, not an alias.
    (r"E:\owner\private\root\evil", r"D:\owner\private\root", False),
    # UNC share, case-insensitive; sibling share prefix must not match.
    (r"\\server\share\private\evil", r"\\server\share\private", True),
    (r"\\SERVER\SHARE\PRIVATE\EVIL", r"\\server\share\private", True),
    (r"\\server\share\private2\evil", r"\\server\share\private", False),
    (r"\\server\share\private\..\other", r"\\server\share\private", False),
    (r"\\server2\share\private\evil", r"\\server\share\private", False),
    (r"\\server\share2\private\evil", r"\\server\share\private", False),
    # POSIX keeps working; case sensitivity is tested by path family below.
    ("/owner/private/root/evil", "/owner/private/root", True),
    ("/owner/private/root2/evil", "/owner/private/root", False),
    ("/owner/private/root/../other", "/owner/private/root", False),
))
def test_guard_normalizes_both_candidate_and_root(candidate, root, expected):
    import private_path_guard as guard

    assert guard._inside(candidate, root) is expected, (candidate, root)


def test_case_sensitivity_follows_the_path_family():
    """POSIX case rules apply to POSIX paths; Windows rules to drive paths.

    On Windows a string like `/OWNER/...` is current-drive-relative and
    case-insensitive, so asserting `False` for it on every host was wrong.
    """
    import private_path_guard as guard

    assert guard._inside(
        "/OWNER/PRIVATE/ROOT/evil", "/owner/private/root",
        windows=False) is False
    assert guard._inside(
        r"D:\OWNER\PRIVATE\ROOT\evil", r"D:\owner\private\root",
        windows=True) is True


def test_guard_catches_windows_argv_but_not_the_configured_env_value():
    """Synthetic Windows paths on Linux exercise the same rule as Windows."""
    import private_path_guard as guard

    root = r"D:\owner\private\root"
    event = ("subprocess.Popen", (
        "git.exe", ["git", "-C", root + r"\evil", "show"], r"C:\work",
        {guard.ROOTS_ENV: root}))
    values = guard._values(*event)
    assert any(guard._inside(value, root) for value in values), values

    clean = ("subprocess.Popen", (
        "python.exe", ["python.exe", "-c", "pass"], r"C:\work",
        {guard.ROOTS_ENV: root}))
    values = guard._values(*clean)
    assert not any(guard._inside(value, root) for value in values), values


# ---------------------------------------------------------------------------
# Q1 P2 — alias forms the raw-prefix version missed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("candidate,root,expected", (
    # `\\?\UNC\...` and `//?/UNC/...` are the long-path forms of the UNC root.
    (r"\\?\UNC\server\share\private\evil", r"\\server\share\private", True),
    ("//?/UNC/server/share/private/evil", r"\\server\share\private", True),
    (r"\\?\UNC\server\share\private2\evil", r"\\server\share\private", False),
    # `\\.\D:` is the device-namespace spelling of the same D drive.
    (r"\\.\D:\owner\private\root\evil", r"D:\owner\private\root", True),
    (r"\\.\D:\owner\private\root2\evil", r"D:\owner\private\root", False),
    (r"\\.\E:\owner\private\root\evil", r"D:\owner\private\root", False),
    # Mixed prefix and separator spelling still identifies the same path.
    ("\\\\?\\D:/owner/private/root/evil", r"D:\owner\private\root", True),
))
def test_guard_resolves_windows_alias_forms(candidate, root, expected):
    import private_path_guard as guard

    assert guard._inside(candidate, root) is expected, (candidate, root)


@pytest.mark.parametrize("value", (
    r"\\.\PhysicalDrive0",
    r"\\?\Volume{01234567-89ab-cdef-0123-456789abcdef}\evil",
    r"\\?\GLOBALROOT\Device\HarddiskVolume1\owner\private\root\evil",
))
def test_ambiguous_device_namespace_is_conservatively_refused(value):
    """A device path that is not a plain drive cannot be proven safe."""
    import private_path_guard as guard

    assert guard._must_refuse(value, (r"D:\owner\private\root",)) is True
    assert guard._must_refuse(
        r"E:\public\file", (r"D:\owner\private\root",)) is False


def test_guard_detects_a_posix_symlink_alias_into_the_private_root(tmp_path):
    import private_path_guard as guard

    root = tmp_path / "private"
    root.mkdir()
    (root / "secret.txt").write_text("private", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    candidate = str(alias / "secret.txt")
    assert guard._inside(candidate, str(root)) is True
    assert guard._must_refuse(candidate, (str(root),)) is True

    sibling = tmp_path / "private2"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("public", encoding="utf-8")
    assert guard._inside(str(sibling / "secret.txt"), str(root)) is False


def test_audit_hook_refuses_an_open_through_a_symlink_alias(tmp_path):
    import subprocess

    import private_path_guard as guard

    root = tmp_path / "private"
    root.mkdir()
    (root / "secret.txt").write_text("private", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(HERE)!r})\n"
        "import private_path_guard as guard\n"
        "guard.install_hook()\n"
        "try:\n"
        f"    open({str(alias / 'secret.txt')!r}, 'rb').read()\n"
        "except PermissionError:\n"
        "    print('BLOCKED')\n"
        "    raise SystemExit(0)\n"
        "print('NOT_BLOCKED')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8")
    env = {**os.environ, guard.ROOTS_ENV: str(root), guard.GUARD_ENV: "1"}
    completed = subprocess.run(
        [sys.executable, str(probe)], env=env, capture_output=True,
        text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "BLOCKED" in completed.stdout


# ---------------------------------------------------------------------------
# The Windows run's 19 skips are an explicit producer boundary, not silence.
# ---------------------------------------------------------------------------

SKIP_MANIFEST = HERE / "fixtures" / "windows-skip-manifest.v1.json"


def _suite_test_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node.name for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")]


def test_windows_skip_manifest_explains_every_suite_test():
    manifest = json.loads(SKIP_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema"] == "serveros/x2-windows-skip-manifest.v1"
    assert manifest["reason_code"] == "PRODUCER_BOUNDARY_POSIX_ONLY"
    assert manifest["measured_windows_run"]["skipped"] == 19
    assert manifest["measured_windows_run"]["failed"] == 4
    for suite, names in manifest["suites"].items():
        path = REPO / suite
        assert _suite_test_names(path) == names, suite
    legacy = manifest["suites"][
        "tests/test_fleet_v1_t10_legacy_compatibility.py"]
    assert len(legacy) == 19


def test_suite_skip_reason_is_the_manifest_reason():
    manifest = json.loads(SKIP_MANIFEST.read_text(encoding="utf-8"))
    for suite in manifest["suites"]:
        text = (REPO / suite).read_text(encoding="utf-8")
        assert manifest["reason_code"] in text, suite
        assert "pytest.xfail(" not in text, suite
        assert "pytest.skip(" not in text, suite


# ---------------------------------------------------------------------------
# The grant suite must skip on Windows BEFORE it imports POSIX modules.
# ---------------------------------------------------------------------------

GRANT_SUITE = REPO / "tests" / "test_v1_grant_os_principal_schema.py"


def _guard_test(node: ast.AST) -> bool:
    """`if not WINDOWS_PRODUCER_BOUNDARY:`"""
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "WINDOWS_PRODUCER_BOUNDARY")


def test_grant_suite_platform_guard_precedes_posix_imports():
    tree = ast.parse(GRANT_SUITE.read_text(encoding="utf-8"))
    guard_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Name)
                        and target.id == "WINDOWS_PRODUCER_BOUNDARY"):
                    guard_line = node.lineno
    assert guard_line is not None, "WINDOWS_PRODUCER_BOUNDARY is missing"

    harness_line = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "v1_portability_harness":
                    harness_line = node.lineno
                if alias.name in {"workengine", "fcntl"}:
                    assert node.lineno > guard_line, alias.name
        if (isinstance(node, ast.ImportFrom)
                and node.module in {"workengine", "fcntl"}):
            assert node.lineno > guard_line, node.module
    assert harness_line is not None, "the harness import is missing"
    assert harness_line > guard_line, (harness_line, guard_line)

    all_daemon = set()
    guarded_daemon = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "daemon_mod":
                    all_daemon.add(node.lineno)
        if _guard_test(node):
            for child in ast.walk(node):
                if isinstance(child, ast.Assign):
                    for target in child.targets:
                        if (isinstance(target, ast.Name)
                                and target.id == "daemon_mod"):
                            guarded_daemon.add(child.lineno)
    assert all_daemon, "daemon_mod is never loaded"
    assert all_daemon == guarded_daemon, (all_daemon, guarded_daemon)


def test_grant_suite_imports_under_win32_without_posix_modules():
    """Reproduce the Windows collection error without a Windows host.

    `sys.platform` is set before import and a meta-path finder raises if the
    module touches `workengine` or `fcntl`. Any OTHER import error is left
    uncaught: the subprocess must exit 0 or this test fails with its stderr.
    """
    import subprocess
    import textwrap

    script = textwrap.dedent(f"""
        import importlib.util
        import sys
        import pytest  # preload before the blocker

        sys.platform = "win32"
        BLOCKED = ("workengine", "fcntl")

        class Blocker:
            def find_spec(self, name, path=None, target=None):
                if name.split(".")[0] in BLOCKED:
                    raise AssertionError("POSIX import attempted: " + name)
                return None

        sys.meta_path.insert(0, Blocker())
        path = {str(GRANT_SUITE)!r}
        spec = importlib.util.spec_from_file_location("x2_grant_win32", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert getattr(module, "WINDOWS_PRODUCER_BOUNDARY", None) is True
        marks = getattr(module, "pytestmark", None)
        marks = marks if isinstance(marks, list) else [marks]
        reasons = []
        for mark in marks:
            inner = getattr(mark, "mark", mark)
            if getattr(inner, "name", None) == "skipif":
                reasons.append(getattr(inner, "kwargs", {{}}).get("reason", ""))
        assert any("PRODUCER_BOUNDARY_POSIX_ONLY" in r for r in reasons), reasons
        names = [n for n in vars(module)
                 if n.startswith("test_") and callable(getattr(module, n))]
        assert len(names) == 13, names
        print("WIN32_IMPORT_OK", len(names))
    """)
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True,
        timeout=60)
    assert completed.returncode == 0, completed.stderr
    assert "WIN32_IMPORT_OK 13" in completed.stdout
