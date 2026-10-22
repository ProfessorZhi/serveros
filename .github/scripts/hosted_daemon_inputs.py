#!/usr/bin/env python3
"""Author isolated HOSTED_DAEMON inputs through runtime.paths' existing env seam.

This creates data, not mocked method answers. The global catalogue is deliberately
unmaterialized; tests which need one already write their own real catalogue file.
It does not enroll a node, install a service, grant an ability or change HOME.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

CHECKOUT = Path(__file__).resolve().parents[2]


def private_directory(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise ValueError("hosted input directory has a symlink")
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("hosted input directory must be owned and private")
    return path


def mkdir(path: Path) -> None:
    path.mkdir(mode=0o700)
    private_directory(path)


@contextlib.contextmanager
def environment(values):
    before = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def prepare(work_root: Path, evidence_dir: Path) -> tuple[dict, dict]:
    work_root, evidence_dir = private_directory(work_root), private_directory(evidence_dir)
    output = evidence_dir / "bootstrap-inputs.json"
    if output.exists() or output.is_symlink():
        raise ValueError("bootstrap evidence already exists; choose a fresh root")
    data = work_root / "d"
    mkdir(data)  # Never blend a previous node's state into this fixture.
    for name in ("workspace", "artifacts", "toolchains", "state", "logs",
                 "runtime-assets", "tmp", "caches"):
        mkdir(data / name)
    for name in ("src", "worktrees", "test-envs", "ops", "knowledge", "records"):
        mkdir(data / "workspace" / name)
    mkdir(data / "state" / "serveros")
    mkdir(data / "state" / "serveros" / "abilities")
    for name in ("t", "r"):
        path = work_root / name
        if not path.exists():
            mkdir(path)
        private_directory(path)
    values = {
        "DATA_ROOT": str(data), "WORKSPACE_ROOT": str(data / "workspace"),
        "ARTIFACTS_ROOT": str(data / "artifacts"), "TOOLCHAINS_ROOT": str(data / "toolchains"),
        "XDG_STATE_HOME": str(data / "state"), "SERVEROS_STATE_DIR": str(data / "state" / "serveros"),
        "TMPDIR": str(work_root / "t"), "XDG_RUNTIME_DIR": str(work_root / "r"),
    }
    module_file = CHECKOUT / "runtime" / "paths.py"
    spec = importlib.util.spec_from_file_location("hosted_daemon_input_paths", module_file)
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    with environment(values):
        actual = paths.Paths()
        # A hosted node must not require a Tokyo/local node profile. This is
        # the loader's declared "no authority supplied" seam, not a fake Paths.
        without_profile = paths.Paths(fleet_dir=None, node_file=None)
        contract = actual.contract()
        if not actual.bound or not without_profile.bound or actual.roots != without_profile.roots:
            raise ValueError("environment fixture did not close the real root contract")
        if actual.root("state") != Path(values["SERVEROS_STATE_DIR"]):
            raise ValueError("runtime.paths and daemon state disagree")
        resolved = actual.resolve("state/runtime/observed")
    revision = subprocess.check_output(
        ["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], text=True).strip()
    source_pin = evidence_dir / "source.sha"
    if source_pin.exists() and source_pin.read_text().strip() != revision:
        raise ValueError("bootstrap and checkout source pins disagree")
    catalogue = data / "state" / "serveros" / "abilities" / "catalogue.json"
    record = {
        "schema": "serveros/hosted-daemon-inputs.v1", "kind": "HOSTED_FIXTURE_ONLY",
        "source_revision": revision,
        "paths_module_sha256": hashlib.sha256(module_file.read_bytes()).hexdigest(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": values, "root_contract": contract,
        "profile_required": False, "home_changed": False,
        "resolved_state_observed": resolved,
        "catalogue": {"path": str(catalogue), "present": catalogue.exists(),
            "registry_status": "NOT_MATERIALIZED", "entries": None,
            "meaning": "no catalogue was authored; not an empty authority or an installation"},
        "existing_pilot_checks": {"modified": False, "observed_here": "not probed; see pytest -ra/JUnit",
            "not_installed": ["test_an_installed_unit_is_byte_identical_to_the_reviewed_artefact",
                              "test_the_installed_unit_does_not_name_a_source_checkout"],
            "not_running": ["test_the_running_daemon_is_not_in_a_private_user_namespace"]},
    }
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    return values, record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--github-env", type=Path, required=True)
    args = parser.parse_args(argv)
    values, record = prepare(args.work_root, args.evidence_dir)
    # This is the runner-owned command file, not any user/global configuration.
    with args.github_env.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError("root value cannot contain a command-file newline")
            stream.write(f"{key}={value}\n")
    print(json.dumps({"source_revision": record["source_revision"],
                      "bound": record["root_contract"]["bound"],
                      "profile_required": False, "catalogue": "NOT_MATERIALIZED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
