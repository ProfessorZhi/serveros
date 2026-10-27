#!/usr/bin/env python3
"""Write `PACKAGE.json` for a built native runtime package.

This file runs nothing. It reads the binaries a build already produced, hashes
them, and writes down what they are. The provenance it records — the commit and
whether the tree was dirty — is passed in by the Makefile, which asks git; this
script is not allowed to, because the repository's actuator gate treats every
command a Python file runs as reachability that has to be declared, and a
manifest writer is not a place to add reachability.

That split is the whole design: the *recipe* (`Makefile`) says what to run, the
*recorder* (this file) says what came out, and the *verifier*
(`verify_native_runtime.py`) checks that the two agree with the bytes on disk.

Why the member table lives here rather than in the Makefile
------------------------------------------------------------
A role is a statement about authority, not about build order. `actuator` means a
binary may ask the service manager to change something. Putting that in a build
recipe would make it look like a build detail; it is the L1 rule that there is
exactly one, and `verify_native_runtime.py` holds a second copy of the constant on
purpose so that a test can assert the writer and the reader agree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path

PACKAGE_SCHEMA = "serveros/native-runtime-package.v1"
PACKAGE_NAME = "serverosd"

#: name -> (role, component, defining translation unit).
#:
#: Exactly one `actuator`. The reader (`verify_native_runtime.py`) declares the
#: same actuator path independently, so a build that promoted a different binary
#: is caught by the verifier rather than agreed with.
MEMBERS = {
    "serveros-native-core": (
        "actuator",
        "supervisor",
        "native/node-core/supervisor/src/main.cpp",
    ),
    "serveros-node-resource-snapshot": (
        "read-only",
        "collector",
        "native/node-core/src/main.cpp",
    ),
    "core-lite-status": (
        "read-only",
        "core-lite",
        "native/node-core/core-lite/src/status_main.cpp",
    ),
    "core-lite-profile-check": (
        "read-only",
        "core-lite",
        "native/node-core/core-lite/src/profile_check.cpp",
    ),
}

RESERVED_PATHS = ("bin/serverosd", "bin/serverosctl")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_from_source(repo: Path) -> str:
    """`kVersion` from the binary's own source; the package has no other version."""
    path = repo / "native/node-core/supervisor/src/main.cpp"
    text = path.read_text(encoding="utf-8")
    found = re.search(r'constexpr\s+const\s+char\s*\*\s*kVersion\s*=\s*"([^"]+)"', text)
    if found is None:
        raise SystemExit(f"{path} no longer declares kVersion; the package version has "
                         f"no source of truth")
    return found.group(1)


def inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def build_manifest(out: Path, repo: Path, commit: str, dirty: str,
                   cxx: str = "", cxx_version: str = "",
                   cxxflags: str = "") -> dict:
    # The Makefile refuses this too, but it can only normalise `..` and the
    # deepest existing parent; `resolve()` follows every symlink in a path that
    # already exists. This is the version that is checked by a test, so the
    # manifest cannot certify a package written into the source tree.
    if inside(out.resolve(), repo.resolve()):
        raise SystemExit(
            f"refusing to write a package manifest inside the repository: "
            f"{out.resolve()} is under {repo.resolve()}"
        )
    dirty_count = int(dirty) if dirty.strip().isdigit() else 0
    bin_dir = out / "bin"
    if not bin_dir.is_dir():
        raise SystemExit(f"{bin_dir} does not exist; build first (`make OUT=...`)")

    members = []
    for path in sorted(bin_dir.iterdir()):
        name = path.name
        if name not in MEMBERS:
            raise SystemExit(
                f"{path} is not a member this package declares. Add it to MEMBERS with "
                f"a role, or stop writing it into bin/ — a file the manifest cannot "
                f"describe is not packaged, it is just present."
            )
        role, component, source = MEMBERS[name]
        mode = stat.S_IMODE(path.stat().st_mode)
        members.append({
            "path": f"bin/{name}",
            "role": role,
            "component": component,
            "source": source,
            "sha256": sha256(path),
            "bytes": path.stat().st_size,
            "mode": f"0{mode:03o}",
            # The name it has always shipped under. A rename would be recorded
            # here as a difference, and the verifier refuses one.
            "legacy_path": f"bin/{name}",
        })

    declared = {member["path"] for member in members}
    missing = sorted(set(f"bin/{name}" for name in MEMBERS) - declared)
    if missing:
        raise SystemExit(f"declared members were not built: {missing}")

    tests_dir = out / "tests"
    tests = []
    if tests_dir.is_dir():
        tests = [{"name": entry.name, "path": f"tests/{entry.name}"}
                 for entry in sorted(tests_dir.iterdir()) if entry.is_file()]

    # `dirty` is the COUNT of modified paths, not a flag. `0` is a measurement
    # ("I asked git and it said nothing is modified"); an empty value means git
    # did not answer, which is why it is paired with an empty commit rather than
    # guessed at.
    provenance = (
        {"commit": commit, "dirty": dirty_count > 0, "dirty_paths": dirty_count}
        if commit else
        {"commit": None, "dirty": None, "dirty_paths": None,
         "reason": "git was not available at build time; provenance was not measured"}
    )

    return {
        "schema": PACKAGE_SCHEMA,
        "name": PACKAGE_NAME,
        "version": version_from_source(repo),
        "source": provenance,
        # `cmake: null` is stated rather than omitted: this build does not use
        # CMake, and a reader should not have to infer that from a missing key.
        "toolchain": {
            "cxx": cxx or None,
            "version": cxx_version or None,
            "flags": cxxflags.split() if cxxflags else [],
            "cmake": None,
        },
        "members": members,
        "tests": tests,
        "owner_map": {
            "schema": "serveros/native-core-owner-map.v1",
            "writers": ["runtime/native_core_facts.py"],
            "readers": [
                "native/node-core/supervisor/src/facts.cpp",
                "runtime/daemon.py",
            ],
            "actuator_for_units": "the map decides which units the one actuator may act on",
        },
        "compatibility": {
            "renames": [],
            "does_not_occupy": list(RESERVED_PATHS),
            "reason": ("`bin/serverosd` is the Python node runtime artifact's entry point "
                       "(scripts/release.py, kind `node-runtime`). This package is named "
                       "serverosd; it does not take that path."),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--repo", type=Path, default=None,
                        help="source tree (default: this script's repository)")
    parser.add_argument("--commit", default="", help="the commit the build came from")
    parser.add_argument("--dirty", default="",
                        help="the COUNT of modified paths, as `git status` reported it")
    parser.add_argument("--cxx", default="")
    parser.add_argument("--cxx-version", default="")
    parser.add_argument("--cxxflags", default="")
    args = parser.parse_args(argv)

    repo = args.repo or Path(__file__).resolve().parent.parent
    manifest = build_manifest(args.out.expanduser().resolve(), repo,
                              args.commit.strip(), args.dirty.strip(),
                              args.cxx.strip(), args.cxx_version.strip(),
                              args.cxxflags.strip())
    destination = args.out.expanduser().resolve() / "PACKAGE.json"
    destination.write_text(json.dumps(manifest, indent=2, sort_keys=True,
                                      ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
