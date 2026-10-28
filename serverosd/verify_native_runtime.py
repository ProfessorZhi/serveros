#!/usr/bin/env python3
"""Verify a built native runtime package before anyone puts it near a device.

What this checks, and why each one is a refusal rather than a warning
---------------------------------------------------------------------
1. **It is the package it says it is.** The manifest's schema is this reader's
   one schema. A differently-shaped manifest parsed as if the fields meant the
   same is how a verifier starts agreeing with a build it cannot read.
2. **Every member is present, executable, and the bytes the manifest recorded.**
   A digest is the only statement about content that survives a copy.
3. **Exactly one actuator.** The single-executor rule is the whole L1 point: one
   program may ask the service manager to change something, and the owner map
   decides which units it may ask about. A package with two actuators is a
   package whose two binaries would each believe they owned the pen.
4. **Nothing was renamed.** Each member records the path it has always shipped
   under, and that path must equal its packaged path. The Python adapter, the
   status socket and the generation-fence contract all name these binaries; a
   rename would be a compatibility break that only shows up at exec time.
5. **It does not occupy the Python runtime's entry points.** `bin/serverosd` and
   `bin/serverosctl` belong to the `node-runtime` artifact. This package is named
   serverosd, and taking those paths would make two artifacts claim one name.
6. **No undeclared file in `bin/`.** A verifier that only checks what the
   manifest lists cannot see what somebody added.

It reads; it never builds, installs, enables, starts or stops anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_SCHEMA = "serveros/native-runtime-package.v1"
PACKAGE_NAME = "serverosd"

#: The manifest's `role` vocabulary. Closed, so an unknown role is refused by
#: name rather than treated as "not an actuator".
ROLES = ("actuator", "read-only")

#: The package's members, pinned HERE as well as in the writer.
#:
#: The writer derives `path` and `legacy_path` from one name, so neither can
#: detect a rename on its own — both are fields the same program wrote. A rename
#: is only visible against a name this reader knows independently, and this is
#: that table. It also makes the set exact: a package is four binaries, and a
#: manifest with three of them verifies nothing about the fourth.
EXPECTED_MEMBERS = {
    "bin/serveros-native-core": "actuator",
    "bin/serveros-node-resource-snapshot": "read-only",
    "bin/core-lite-status": "read-only",
    "bin/core-lite-profile-check": "read-only",
}

#: The one binary allowed to carry the actuator role, restated from the table
#: above so a reader looking for "who may act" finds one line.
ACTUATOR_PATH = "bin/serveros-native-core"

#: Paths that belong to the Python node runtime artifact. See
#: `scripts/release.py`'s `REQUIRED_MEMBERS[node-runtime]`.
RESERVED_PATHS = ("bin/serverosd", "bin/serverosctl")


@dataclass
class Report:
    checked: int = 0
    failures: list[str] = field(default_factory=list)

    def ok(self, statement: str) -> None:
        self.checked += 1
        print(f"PASS {statement}")

    def fail(self, statement: str, reason: str) -> None:
        self.checked += 1
        self.failures.append(statement)
        print(f"FAIL {statement}: {reason}")

    def require(self, condition: bool, statement: str, reason: str) -> bool:
        if condition:
            self.ok(statement)
        else:
            self.fail(statement, reason)
        return condition


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_version(repo: Path) -> str | None:
    """`kVersion` from the binary's own source, or None if the repo is absent."""
    path = repo / "native/node-core/supervisor/src/main.cpp"
    if not path.is_file():
        return None
    found = re.search(r'constexpr\s+const\s+char\s*\*\s*kVersion\s*=\s*"([^"]+)"',
                      path.read_text(encoding="utf-8"))
    return found.group(1) if found else None


def verify(package: Path, repo: Path | None = None) -> Report:
    report = Report()
    manifest_path = package / "PACKAGE.json"
    if not report.require(manifest_path.is_file(), "manifest is present",
                          f"{manifest_path} does not exist"):
        return report
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        report.fail("manifest is readable JSON", str(error))
        return report
    report.ok("manifest is readable JSON")

    if not report.require(isinstance(manifest, dict), "manifest is an object",
                          f"top level is {type(manifest).__name__}"):
        return report

    report.require(manifest.get("schema") == PACKAGE_SCHEMA, "manifest names this schema",
                   f"declares {manifest.get('schema')!r}, expected {PACKAGE_SCHEMA!r}")
    report.require(manifest.get("name") == PACKAGE_NAME, "package is named serverosd",
                   f"declares {manifest.get('name')!r}")

    members = manifest.get("members")
    if not report.require(isinstance(members, list) and bool(members),
                          "manifest lists members",
                          "`members` is missing or empty"):
        return report

    # `bin/` itself, before anything inside it. `Path.is_symlink()` on a member
    # only looks at the last component, so a `bin` that is a link to some other
    # directory would launder every file under it.
    bin_dir = package / "bin"
    report.require(bin_dir.is_dir() and not bin_dir.is_symlink(), "bin/ is a real directory",
                   "missing, or a symlink; a linked bin/ makes every member's own "
                   "check look at a file this package does not contain")

    seen_paths: set[str] = set()
    actuators: list[str] = []
    for member in members:
        if not isinstance(member, dict) or not isinstance(member.get("path"), str):
            report.fail("member is a shaped object", f"member entry is {member!r}")
            continue
        path = member["path"]
        # ONE name under bin/, not a path that starts with it. `bin/../../../bin/true`
        # satisfies a prefix check and resolves to a file outside the package —
        # which this verifier would then hash and bless. A separator after the
        # first `/` is refused, so `bin/../bin/x` and `bin/sub/x` are both
        # unrepresentable rather than filtered.
        if not report.require(path.startswith("bin/") and "/" not in path[4:]
                              and path[4:] not in ("", ".", ".."),
                              f"{path} is one name under bin/",
                              "a member path must be exactly bin/<name>"):
            continue
        if not report.require(path not in seen_paths, f"{path} is declared once",
                              "duplicate member row; one row per binary"):
            continue
        seen_paths.add(path)

        absolute = package / path
        if not report.require(absolute.is_file() and not absolute.is_symlink(),
                              f"{path} is a regular file",
                              "missing, or a symlink (a link is not the artifact)"):
            continue
        # Belt as well as braces: after resolution the member must still live
        # directly in this package's bin/.
        if not report.require(absolute.resolve().parent == bin_dir.resolve(),
                              f"{path} resolves inside the package",
                              f"resolves to {absolute.resolve()}"):
            continue

        mode = stat.S_IMODE(absolute.stat().st_mode)
        report.require(mode == 0o755, f"{path} is 0755",
                       f"mode is {mode:04o}; an artifact that is not executable is a "
                       f"service that fails with 203/EXEC")
        report.require(member.get("mode") == "0755", f"{path} records mode 0755",
                       f"manifest records {member.get('mode')!r}")

        recorded = member.get("sha256")
        actual = sha256(absolute)
        report.require(isinstance(recorded, str) and recorded == actual,
                       f"{path} matches its digest",
                       f"manifest {recorded!r} != file {actual!r}")

        size = absolute.stat().st_size
        report.require(member.get("bytes") == size, f"{path} matches its size",
                       f"manifest {member.get('bytes')!r} != file {size}")

        role = member.get("role")
        if report.require(role in ROLES, f"{path} has a known role",
                          f"role {role!r} is not one of {list(ROLES)}"):
            if role == "actuator":
                actuators.append(path)
            expected_role = EXPECTED_MEMBERS.get(path)
            if expected_role is not None:
                report.require(role == expected_role, f"{path} keeps its role",
                               f"this package declares {path} as {expected_role!r}, the "
                               f"manifest says {role!r}")

        report.require(member.get("legacy_path") == path, f"{path} keeps its shipped name",
                       f"manifest records legacy_path {member.get('legacy_path')!r}")

    # --- the member set is exact --------------------------------------------
    # Against the table this reader carries, not against the manifest's own
    # fields. Without this, a rename is invisible: `path` and `legacy_path` are
    # both written by the same program from the same name, so a hand-written
    # manifest naming a binary something else is consistent with itself.
    missing = sorted(set(EXPECTED_MEMBERS) - seen_paths)
    unexpected = sorted(seen_paths - set(EXPECTED_MEMBERS))
    report.require(not missing, "every expected member is declared",
                   f"missing: {missing}")
    report.require(not unexpected, "no member is declared that this package does not have",
                   f"unexpected: {unexpected}")

    # --- the single-executor rule -------------------------------------------
    report.require(len(actuators) == 1, "exactly one actuator",
                   f"found {len(actuators)}: {actuators or 'none'}")
    if len(actuators) == 1:
        report.require(actuators[0] == ACTUATOR_PATH, f"the actuator is {ACTUATOR_PATH}",
                       f"the actuator is {actuators[0]!r}; the manifest promoted a "
                       f"different binary to the pen")

    # --- does not take the Python runtime's paths ---------------------------
    for reserved in RESERVED_PATHS:
        report.require(reserved not in seen_paths, f"{reserved} is not claimed",
                       "that path belongs to the `node-runtime` artifact")
    compatibility = manifest.get("compatibility")
    if not report.require(isinstance(compatibility, dict), "compatibility is an object",
                          f"is {type(compatibility).__name__}"):
        compatibility = {}
    declared = compatibility.get("does_not_occupy")
    # A LIST of strings, not "anything containing the names". A single string
    # `"bin/serverosd bin/serverosctl"` satisfies a substring test while
    # declaring no such thing, and the reader would be agreeing with a document
    # it had not understood.
    report.require(isinstance(declared, list)
                   and all(isinstance(entry, str) for entry in declared),
                   "does_not_occupy is a list of paths", f"is {declared!r}")
    declared = declared if isinstance(declared, list) else []
    report.require(all(path in declared for path in RESERVED_PATHS),
                   "manifest declares the paths it does not occupy",
                   f"compatibility.does_not_occupy is {declared!r}")
    # `renames` is written by the build and never used to authorise anything: a
    # rename is refused outright, so the field may only ever be empty. Checked
    # rather than left as a field nobody reads, because a field nothing reads is
    # free to start meaning something.
    renames = compatibility.get("renames")
    report.require(renames == [], "manifest declares no renames", f"is {renames!r}")
    # The declaration and the directory are two statements about one path. A
    # manifest that says it left `bin/serverosd` to the Python runtime while a
    # file of that name sits in the package is the defect this check exists for:
    # it is how two artifacts come to claim one name.
    for reserved in RESERVED_PATHS:
        if reserved in declared:
            report.require(not (package / reserved).exists(),
                           f"{reserved} is absent, as declared",
                           "the manifest says this package does not occupy it")

    # --- nothing undeclared --------------------------------------------------
    actual_members = sorted(
        f"bin/{entry.name}" for entry in bin_dir.iterdir()
    ) if bin_dir.is_dir() else []
    undeclared = sorted(set(actual_members) - seen_paths)
    report.require(not undeclared, "no undeclared file under bin/",
                   f"undeclared: {undeclared}")

    # --- version is the binary's own ----------------------------------------
    if repo is not None:
        expected = source_version(repo)
        if expected is None:
            report.ok("source version is not checked (no source tree supplied)")
        else:
            report.require(manifest.get("version") == expected,
                           "manifest version is the binary's version",
                           f"manifest {manifest.get('version')!r} != kVersion {expected!r}")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="a directory containing PACKAGE.json")
    parser.add_argument("--repo", type=Path, default=None,
                        help="source tree to cross-check the version against "
                             "(default: this script's repository)")
    args = parser.parse_args(argv)

    repo = args.repo
    if repo is None:
        candidate = Path(__file__).resolve().parent.parent
        repo = candidate if (candidate / "native/node-core/supervisor/src/main.cpp").is_file() else None

    report = verify(args.package.expanduser().resolve(), repo)
    print(f"\n{report.checked} checks, {len(report.failures)} failures")
    return 0 if not report.failures else 1


if __name__ == "__main__":
    sys.exit(main())
