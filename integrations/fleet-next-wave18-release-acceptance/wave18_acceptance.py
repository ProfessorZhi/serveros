#!/usr/bin/env python3
"""Wave 18 release acceptance: six layers, and a drill that runs in six processes.

The claim under test
--------------------
A Fleet Next release candidate — six pinned PR heads, one composed package — may
be installed *beside* Legacy and be revoked again without moving anything
Legacy promised: the Fleet instance, the route, the boot, the port, the backend
URL, the OAuth audience, the database, the state root, the lock and the clock,
plus the three verbs the connector drives (`get_session`, `get_run`, `send`).

What each layer is allowed to prove, and what it is not
-------------------------------------------------------
SOURCE          every pinned head exists, descends from the recorded base, and
                carries the files the release package takes from it.
UNIT            the pinned unit tests pass *on this machine*, from a clone at
                that exact commit. A unit that is Linux-only is `NOT_RUN_PLATFORM`
                here, never silently counted as passing.
HOSTED_CI       the hosted run at that exact head. A shard whose baseline gate
                did not print is `BLOCKED_CI_UNPARSED`; a moved head is
                `BLOCKED_HEAD_MOVED`; a failure not in the known set is
                attributed to nobody and blocks.
BUILT_ARTIFACT  bytes, found by name, hashed here. With no published bytes this
                is `BLOCKED_ARTIFACT` — a rebuild made by the reviewer is not
                the release and is never labelled as one.
LEGACY_SURVIVAL the drill: install -> tick -> failed upgrade -> revoke, in six
                separate operating-system processes, with a Legacy snapshot
                before and after every one of them.
REAL_DEVICE     always `NOT_RUN`. There is no argument that turns it green.

Readiness is `READY` only when the five provable layers pass *and* no layer
raised a BLOCKER finding. A finding is how a measured defect travels with the
verdict instead of being averaged into it: `PARTIAL_UPGRADE_TRAPS_ROOT` does not
make the drill machinery fail (it worked — it found the trap), it makes the
*release* fail, and both statements are in the same evidence object.

Everything happens in a disposable directory
--------------------------------------------
The drill writes only under its `workdir`, which is refused if it resolves
inside the repository. It binds no port, opens no socket, starts no service,
reads no credential, and calls no model. `REAL_DEVICE` exists precisely so that
a green offline run cannot be mistaken for a device pass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as platform_module
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
DRILL = HERE / "drill"
DRILL_STEP = DRILL / "drill_step.py"
FIXTURE_ENGINE = DRILL / "fixture_engine.py"
PIN_PATH = HERE / "release_pin.json"

SCHEMA_PIN = "serveros/fleet-next-release-pin.v1"

#: The six layers, in the order an operator reads them. The order is the
#: escalation: a repository claim, a test claim, a CI claim, a bytes claim, a
#: behaviour claim, and the one that is not made at all.
LAYERS = ("SOURCE", "UNIT", "HOSTED_CI", "BUILT_ARTIFACT", "LEGACY_SURVIVAL",
          "REAL_DEVICE")

#: The five layers that must pass before `READY`. `REAL_DEVICE` is not among
#: them and never will be: it is a separate authorization, not a build step.
PROVABLE = LAYERS[:-1]

EXIT_READY = 0
EXIT_BLOCKED = 2
EXIT_INCOMPLETE = 3

#: The hosted baseline gate is allowed to be red with *known* failures. One is
#: a fixture that reads the wall clock and only fails inside a date window, so
#: it appears as a NEW FAILURE on runs that touch nothing near it. It stays on
#: this list with its reason, and it is attributed rather than waved through:
#: the CI layer prints it, so a reader can see what was excused and why.
KNOWN_FLAKY = {
    "tests/test_direct_lark_notify.py::CardCreateThenPatchTest::"
    "test_create_returns_an_id_and_patch_names_it":
        "reads the wall clock; fails only inside a date window (see #425)",
}

DRILL_ORDER = ("seed", "install", "observe", "failed-upgrade", "rollback", "probe")

#: One refusal per injected fault, and one control that must *not* refuse. A
#: gate that blocks everything fails the control; a gate that blocks nothing
#: fails every other row.
EXPECTED_INJECTIONS = {
    "bad_sha": "BLOCKED_SOURCE_DIGEST",
    "foreign_boot": "BLOCKED_BOOT_MISMATCH",
    "unauthorized_device": "BLOCKED_IDENTITY_MISMATCH",
    "missing_receipt": "BLOCKED_NO_RECEIPT",
    "legacy_root_collision": "BLOCKED_LEGACY_ROOT",
    "resource_collision": "BLOCKED_RESOURCE_COLLISION",
    "resource_clean": "OK",
    "offline": "OFFLINE_REFUSED",
    "no_grant": "BLOCKED_NO_GRANT",
    "not_pinned": "BLOCKED_NOT_PINNED",
}

VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BARE_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NEW_FAILURE_RE = re.compile(r"^\s{2,}(\S+::\S+)\s*$")
SUMMARY_RE = re.compile(
    r"^(\d+)\s+failing,\s+(\d+)\s+new,\s+(\d+)\s+flaky,\s+"
    r"(\d+)\s+listed-but-passing\s*$", re.MULTILINE)

OK = "OK"

#: Layer states. `NOT_RUN` is only ever allowed on `REAL_DEVICE`; anywhere else
#: it means the layer could not be measured, which is `INCOMPLETE`.
PASS = "PASS"
BLOCKED = "BLOCKED"
NOT_RUN = "NOT_RUN"

#: The hosted run's own word for "the baseline gate read clean at this head".
#: It is deliberately not `PASS`: a classifier answer is not a layer state, and
#: mixing the two is how a new classifier word ends up folded as a pass.
CI_GREEN = "GREEN"

BLOCKED_PIN = "BLOCKED_PIN"
BLOCKED_SHARED_RESOURCE = "BLOCKED_SHARED_RESOURCE"
BLOCKED_MISSING_OBJECT = "BLOCKED_MISSING_OBJECT"
BLOCKED_MISSING_PATH = "BLOCKED_MISSING_PATH"
BLOCKED_ANCESTRY = "BLOCKED_ANCESTRY"
BLOCKED_UNIT_TEST = "BLOCKED_UNIT_TEST"
NOT_RUN_PLATFORM = "NOT_RUN_PLATFORM"
BLOCKED_CI_NOT_MEASURED = "BLOCKED_CI_NOT_MEASURED"
BLOCKED_CI_UNPARSED = "BLOCKED_CI_UNPARSED"
BLOCKED_CI_NO_GATE = "BLOCKED_CI_NO_GATE"
BLOCKED_CI_MISSING = "BLOCKED_CI_MISSING"
BLOCKED_CI_CHECK = "BLOCKED_CI_CHECK"
BLOCKED_CI_NEW_FAILURE = "BLOCKED_CI_NEW_FAILURE"
BLOCKED_HEAD_MOVED = "BLOCKED_HEAD_MOVED"
BLOCKED_ARTIFACT = "BLOCKED_ARTIFACT"
BLOCKED_ARTIFACT_UNPINNED = "BLOCKED_ARTIFACT_UNPINNED"
BLOCKED_DIGEST = "BLOCKED_DIGEST"
BLOCKED_DRILL_FAILED = "BLOCKED_DRILL_FAILED"
BLOCKED_DRILL_NOT_ISOLATED = "BLOCKED_DRILL_NOT_ISOLATED"
BLOCKED_DRILL_EVIDENCE = "BLOCKED_DRILL_EVIDENCE"
BLOCKED_MISSING_LAYER = "BLOCKED_MISSING_LAYER"
BLOCKED_LAYER_STATE = "BLOCKED_LAYER_STATE"
BLOCKED_NO_EVIDENCE = "BLOCKED_NO_EVIDENCE"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"
NOT_RUN_REAL_DEVICE = "NOT_RUN_REAL_DEVICE"

#: Every subprocess that yields text is decoded as UTF-8 with replacement. The
#: host's locale is not the child's encoding: on Windows a `gbk` decoder raises
#: inside the reader thread, the thread dies, and the layer loses the very output
#: it was measuring while `returncode` still looks meaningful.
_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Finding:
    """A measured defect that travels with the verdict instead of into it."""

    code: str
    severity: str                      # BLOCKER or NOTE
    summary: str
    owner: str                         # the PR number whose Owner must act
    file: str
    condition: str                     # what has to be true to close it
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity,
                "summary": self.summary, "owner": self.owner,
                "file": self.file, "condition": self.condition,
                "evidence": dict(self.evidence)}


@dataclass(frozen=True)
class LayerResult:
    name: str
    state: str
    code: str
    reasons: tuple[str, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()

    def as_dict(self) -> dict:
        return {"layer": self.name, "state": self.state, "code": self.code,
                "reasons": list(self.reasons), "evidence": dict(self.evidence),
                "findings": [finding.as_dict() for finding in self.findings]}


@dataclass(frozen=True)
class Verdict:
    state: str
    code: str
    reasons: tuple[str, ...] = ()
    layers: tuple[LayerResult, ...] = ()
    findings: tuple[Finding, ...] = ()

    @property
    def exit_code(self) -> int:
        return {"READY": EXIT_READY, "BLOCKED": EXIT_BLOCKED}.get(
            self.state, EXIT_INCOMPLETE)

    def as_dict(self) -> dict:
        return {"state": self.state, "code": self.code,
                "reasons": list(self.reasons), "exit_code": self.exit_code,
                "layers": [layer.as_dict() for layer in self.layers],
                "findings": [finding.as_dict() for finding in self.findings]}


@dataclass(frozen=True)
class Artifact:
    name: str
    path: Path
    sha256: str
    pinned: bool = True


# --------------------------------------------------------------------------- #
# the pin
# --------------------------------------------------------------------------- #
def load_pin(path: Path | str = PIN_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rel_path_problem(path: Any) -> str:
    if not isinstance(path, str) or not path:
        return "an empty path is not a file"
    if "\\" in path:
        return f"{path!r} contains a backslash"
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        return f"{path!r} is absolute"
    if ".." in path.split("/"):
        return f"{path!r} leaves the package"
    return ""


def validate_pin(pin: Mapping[str, Any]) -> tuple[str, ...]:
    """Every way this document is not a release claim. Empty means it is one."""
    problems: list[str] = []
    if not isinstance(pin, Mapping):
        return ("the pin is not an object",)
    if pin.get("schema") != SCHEMA_PIN:
        problems.append(f"schema {pin.get('schema')!r} is not {SCHEMA_PIN!r}")
    if not isinstance(pin.get("wave"), str) or not pin.get("wave"):
        problems.append("wave is missing")
    if not DATE_RE.match(str(pin.get("created", ""))):
        problems.append(f"created {pin.get('created')!r} is not YYYY-MM-DD")

    base = pin.get("base") or {}
    if not isinstance(base, Mapping):
        problems.append("base is not an object")
        base = {}
    if not COMMIT_RE.match(str(base.get("commit", ""))):
        problems.append(f"base.commit {base.get('commit')!r} is not a 40-hex commit")
    if not str(base.get("branch") or ""):
        problems.append("base.branch is missing")

    units = pin.get("units")
    ids: list[str] = []
    if not isinstance(units, Sequence) or not units:
        problems.append("units is empty")
        units = []
    for index, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            problems.append(f"units[{index}] is not an object")
            continue
        uid = str(unit.get("id") or "")
        if not uid:
            problems.append(f"units[{index}].id is missing")
        elif uid in ids:
            problems.append(f"duplicate unit id {uid!r}")
        else:
            ids.append(uid)
        if not COMMIT_RE.match(str(unit.get("commit", ""))):
            problems.append(f"{uid}: commit {unit.get('commit')!r} is not a "
                            f"40-hex commit")
        if not isinstance(unit.get("pr"), int):
            problems.append(f"{uid}: pr is not a number")
        paths = unit.get("paths") or []
        if not isinstance(paths, Sequence) or not paths:
            problems.append(f"{uid}: paths is empty")
        for path in paths:
            problem = _rel_path_problem(path)
            if problem:
                problems.append(f"{uid}: {problem}")
        platforms = unit.get("platforms") or []
        if not isinstance(platforms, Sequence) or not platforms:
            problems.append(f"{uid}: platforms is empty")
        for entry in unit.get("unit_tests") or []:
            problem = _rel_path_problem(entry)
            if problem:
                problems.append(f"{uid}: unit test {problem}")

    package = pin.get("release_package") or {}
    if not isinstance(package, Mapping):
        problems.append("release_package is not an object")
        package = {}
    if not str(package.get("name") or ""):
        problems.append("release_package.name is missing")
    version = str(package.get("version") or "")
    if not VERSION_RE.match(version):
        problems.append(f"release_package.version {version!r} is not a pinned "
                        f"semantic version")
    members = package.get("files") or []
    if not isinstance(members, Sequence) or not members:
        problems.append("release_package.files is empty")
        members = []
    seen: set[str] = set()
    for index, member in enumerate(members):
        if not isinstance(member, Mapping):
            problems.append(f"release_package.files[{index}] is not an object")
            continue
        path = member.get("path")
        problem = _rel_path_problem(path)
        if problem:
            problems.append(f"release_package.files[{index}].path: {problem}")
        elif path in seen:
            problems.append(f"release_package.files[{index}].path {path!r} is "
                            f"listed twice")
        else:
            seen.add(path)
        source = member.get("source")
        if source not in ids:
            problems.append(f"release_package.files[{index}].source {source!r} "
                            f"is not a pinned unit id")
        origin = member.get("from")
        if origin is not None:
            problem = _rel_path_problem(origin)
            if problem:
                problems.append(f"release_package.files[{index}].from: {problem}")
    entrypoint = package.get("revoke_entrypoint")
    if entrypoint not in seen:
        problems.append(f"release_package.revoke_entrypoint {entrypoint!r} is "
                        f"not one of the packaged files")

    legacy = pin.get("legacy") or {}
    if not isinstance(legacy, Mapping):
        problems.append("legacy is not an object")
        legacy = {}
    for key in ("fleet_instance_id", "route_id", "boot_id", "backend_url",
                "oauth_audience", "clock"):
        if not str(legacy.get(key) or ""):
            problems.append(f"legacy.{key} is missing")
    if not isinstance(legacy.get("port"), int):
        problems.append("legacy.port is not a number")
    contract = legacy.get("contract") or []
    if not isinstance(contract, Sequence) or not contract:
        problems.append("legacy.contract is empty: the contract that must not "
                        "move is not named")
    else:
        for verb in ("get_session", "get_run", "send"):
            if verb not in contract:
                problems.append(f"legacy.contract does not name {verb!r}")

    device = pin.get("device") or {}
    if not str(device.get("device_ref") or ""):
        problems.append("device.device_ref is missing")
    if not str(device.get("boot_id") or ""):
        problems.append("device.boot_id is missing")

    artifact = pin.get("artifact") or {}
    if not isinstance(artifact, Mapping):
        problems.append("artifact is not an object")
        artifact = {}
    if not isinstance(artifact.get("published"), bool):
        problems.append("artifact.published is not a boolean")
    names = artifact.get("expected_names")
    if not isinstance(names, Sequence):
        problems.append("artifact.expected_names is not a list")
        names = []
    digests = artifact.get("expected_sha256")
    if not isinstance(digests, Mapping):
        problems.append("artifact.expected_sha256 is not an object")
        digests = {}
    for name in names:
        if name not in digests:
            problems.append(f"artifact {name!r} has no expected digest")
    for name, digest in digests.items():
        if not BARE_SHA_RE.match(str(digest)):
            problems.append(f"artifact {name!r} digest is not 64 hex characters")
        if name not in names:
            problems.append(f"artifact {name!r} has a digest but is not named")
    if artifact.get("evidence") is not None and not isinstance(
            artifact.get("evidence"), Mapping):
        problems.append("artifact.evidence is not an object")
    return tuple(problems)


# --------------------------------------------------------------------------- #
# the hosted baseline gate
# --------------------------------------------------------------------------- #
def strip_runner_prefix(text: str) -> str:
    """Drop what the runner adds to every log line: ANSI colour and a timestamp.

    `gh api .../logs` returns the raw job log, where each line begins with
    `2026-10-29T13:12:14.8643423Z `. The gate's parser anchors on the start of a
    line, so without this a perfectly parsed summary becomes "printed no gate
    summary" — a false negative that reads exactly like a broken shard.
    """
    text = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    return re.sub(r"(?m)^\d{4}-\d{2}-\d{2}T[\d:.]+Z ", "", text)


def parse_baseline_gate(text: str) -> dict:
    """Read `scripts/hermetic_tests.py`'s gate summary. Absent is not green."""
    text = text or ""
    match = SUMMARY_RE.search(text)
    if match is None:
        return {"parsed": False, "failing": None, "new": (), "new_count": None,
                "flaky": None, "listed_but_passing": None, "sections": {}}
    failing, new_count, flaky, listed = (int(value) for value in match.groups())
    # The entries sit in sections above the summary, one section per verdict:
    # `NEW FAILURES:` and `FLAKY (passed on retry):` can both be present, and
    # only the first is a claim that something new broke. Reading "the lines
    # just above the summary" instead of "the lines under the NEW FAILURES
    # header" turns a green shard with one flaky test into a contradiction
    # ("counted more NEW FAILURES than it listed").
    block = text[:match.start()]
    if "== baseline gate ==" in block:
        block = block.rsplit("== baseline gate ==", 1)[1]
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        found = NEW_FAILURE_RE.match(line)
        if found is not None and current is not None:
            sections.setdefault(current, []).append(found.group(1))
            continue
        if stripped.endswith(":") or (stripped.isupper() and stripped):
            current = stripped.rstrip(":").upper()
            continue
        current = None
    listed_new = sections.get("NEW FAILURES", [])
    return {"parsed": True, "failing": failing, "new": tuple(listed_new),
            "new_count": new_count, "flaky": flaky,
            "listed_but_passing": listed,
            "sections": {name: tuple(entries)
                         for name, entries in sorted(sections.items())}}


def attribute_new_failures(new: Iterable[str], *, known: Mapping[str, str]) -> dict:
    """Split new failures into explained ones and ones nobody owns."""
    attributed: dict[str, str] = {}
    unattributed: list[str] = []
    for test in new:
        if test in known:
            attributed[test] = known[test]
        else:
            unattributed.append(test)
    return {"attributed": attributed, "unattributed": tuple(unattributed)}


def classify_ci_report(report: Mapping[str, Any], *,
                       known: Mapping[str, str]) -> dict:
    """One unit's hosted run. `GREEN` requires a parsed gate at the right head."""
    head = str(report.get("head") or "")
    expected = str(report.get("expected_head") or head)
    if head != expected:
        return {"state": BLOCKED, "code": BLOCKED_HEAD_MOVED,
                "reasons": [f"the report is for {head!r}, the pin for {expected!r}"],
                "new": (), "attributed": {}}
    gates = report.get("gates")
    if not gates:
        return {"state": BLOCKED, "code": BLOCKED_CI_NO_GATE,
                "reasons": ["the run carried no baseline-gate output"],
                "new": (), "attributed": {}}
    unparsed = [str(gate.get("shard", "?")) for gate in gates
                if not gate.get("parsed")]
    if unparsed:
        return {"state": BLOCKED, "code": BLOCKED_CI_UNPARSED,
                "reasons": [f"shard(s) {', '.join(unparsed)} printed no gate "
                            f"summary, so their result is unknown"],
                "new": (), "attributed": {}}
    miscounted = [str(gate.get("shard", "?")) for gate in gates
                  if gate.get("new_count") is not None
                  and len(gate.get("new") or ()) != int(gate["new_count"])]
    if miscounted:
        return {"state": BLOCKED, "code": BLOCKED_CI_UNPARSED,
                "reasons": [f"shard(s) {', '.join(miscounted)} counted more NEW "
                            f"FAILURES than it listed, so the list is not the "
                            f"whole story"],
                "new": (), "attributed": {}}
    # A shard job exits non-zero precisely when the baseline has failures in it:
    # the gate's job is to distinguish "old failures" from "new ones", so a
    # shard's job conclusion is the wrong question for it. Its own parsed
    # summary is the right one, and every other job has no summary, so there the
    # conclusion is all there is. This runs after attribution because a shard
    # whose only new failures are listed ones is explained by that fact.
    new: list[str] = []
    for gate in gates:
        for test in gate.get("new") or ():
            if test not in new:
                new.append(test)
    split = attribute_new_failures(new, known=known)
    if split["unattributed"]:
        return {"state": BLOCKED, "code": BLOCKED_CI_NEW_FAILURE,
                "reasons": [f"NEW FAILURE not in the known set: {test}"
                            for test in split["unattributed"]],
                "new": tuple(new), "attributed": split["attributed"]}
    explained = {str(gate.get("shard")) for gate in gates
                 if all(str(test) in split["attributed"]
                        for test in (gate.get("new") or ()))}
    for check in report.get("checks") or ():
        if str(check.get("conclusion") or check.get("status") or "") in (
                "success", "skipped", "neutral"):
            continue
        shard = re.search(r"shard\s+(\d+/\d+)", str(check.get("name")), re.I)
        if shard and shard.group(1) in explained:
            continue
        return {"state": BLOCKED, "code": BLOCKED_CI_CHECK,
                "reasons": [f"check {check.get('name')!r} is "
                            f"{check.get('conclusion')!r}"],
                "new": tuple(new), "attributed": split["attributed"]}
    return {"state": CI_GREEN, "code": OK, "reasons": [],
            "new": tuple(new), "attributed": split["attributed"],
            "failing": sum(int(gate.get("failing") or 0) for gate in gates)}


# --------------------------------------------------------------------------- #
# artifacts
# --------------------------------------------------------------------------- #
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def discover_artifacts(pin: Mapping[str, Any], *, repo: Path | None = None,
                       artifact_dir: Path | None = None) -> tuple[Artifact, ...]:
    """Find the release's bytes by their pinned names, wherever they landed."""
    spec = pin.get("artifact") or {}
    names = [str(name) for name in spec.get("expected_names") or ()]
    roots: list[Path] = []
    for candidate in [artifact_dir, *(spec.get("search_paths") or ())]:
        if candidate is None:
            continue
        path = Path(candidate)
        if path.is_dir() and path not in roots:
            roots.append(path)
    found: dict[str, Artifact] = {}
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if names and path.name not in names:
                continue
            if not names and not re.fullmatch(r"fleet-.+\.(tar\.gz|tgz)", path.name):
                continue
            artifact = Artifact(name=path.name, path=path,
                                sha256=_sha256_file(path),
                                pinned=path.name in names)
            found.setdefault(f"{path.name}|{artifact.sha256}", artifact)
    ordered = sorted(found.values(), key=lambda item: item.name)
    return tuple(ordered)


def verify_artifact_digest(path: Path, expected: str) -> bool:
    want = str(expected).strip().lower()
    want = want[len("sha256:"):] if want.startswith("sha256:") else want
    if not BARE_SHA_RE.match(want):
        return False
    return _sha256_file(Path(path)) == want


def parse_checksum_file(path: Path) -> dict[str, str]:
    """`<hex>  <name>` lines, as `sha256sum` writes them."""
    out: dict[str, str] = {}
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and BARE_SHA_RE.match(parts[0]):
            out[parts[1]] = parts[0]
    return out


def reproduce_artifacts(pin: Mapping[str, Any], repo: Path | str,
                        workdir: Path | str, *,
                        timeout: float = 1800) -> dict | None:
    """Re-run the builder's own code at its recorded source commit.

    This is a *reproducibility* check, never an independent artifact: the code
    doing the building is the code that made the claim, so agreement means "the
    checksums and the commit agree", not "the bytes are the release". The layer
    state does not depend on it; a disagreement is a finding, because a digest
    file that its own builder cannot reproduce from the commit it names is a
    claim about a tree state that was never published.
    """
    spec = pin.get("artifact") or {}
    plan = spec.get("rebuild") or {}
    if not plan:
        return None
    repo = Path(repo)
    base = Path(workdir) / "rebuild"
    base.mkdir(parents=True, exist_ok=True)
    source = str(plan["source_commit"])
    builder_commit = str(plan["builder_commit"])
    builder_rel = str(plan["builder_path"])
    version = str(pin["release_package"]["version"])
    record: dict[str, Any] = {"status": "UNAVAILABLE", "matches": None,
                              "source_commit": source,
                              "builder_commit": builder_commit,
                              "claimed": dict((spec.get("evidence") or {}).get(
                                  "claimed_sha256") or {})}
    try:
        clone = base / "clone"
        if clone.exists():
            shutil.rmtree(clone)
        subprocess.run(["git", "clone", "--quiet", "--no-local",
                        "--no-checkout", str(repo), str(clone)],
                       check=True, capture_output=True, timeout=timeout)
        # A clone copies branches, not the shared repo's remote-tracking refs;
        # the recorded commit usually lives on one of those.
        subprocess.run(["git", "-C", str(clone), "fetch", "--quiet", str(repo),
                        "+refs/remotes/origin/*:refs/remotes/origin/*"],
                       check=True, capture_output=True, timeout=timeout)
        _git(clone, "config", "core.autocrlf", "false")
        _git(clone, "config", "core.eol", "lf")
        subprocess.run(["git", "-C", str(clone), "checkout", "--quiet", source],
                       check=True, capture_output=True, timeout=timeout)
        builder = base / "builder" / Path(builder_rel).name
        builder.parent.mkdir(parents=True, exist_ok=True)
        builder.write_bytes(_show(repo, builder_commit, builder_rel))
        outdir = base / "out"
        if outdir.exists():
            shutil.rmtree(outdir)
        started = time.time()
        proc = subprocess.run(
            [sys.executable, str(builder), "--repo", str(clone),
             "--commit", source, "--version", version, "--out", str(outdir),
             "--json"],
            cwd=str(base), capture_output=True, **_TEXT, timeout=timeout,
            env=_unit_env())
        record["seconds"] = round(time.time() - started, 2)
        record["builder_returncode"] = proc.returncode
        record["builder_stderr"] = (proc.stderr or "").strip()[-600:]
        produced = parse_checksum_file(outdir / "ARTIFACTS.sha256")
        record["produced"] = produced
        if proc.returncode != 0 or not produced:
            record["status"] = "UNAVAILABLE"
            record["matches"] = None
            return record
        claimed = record["claimed"]
        mismatched = sorted(name for name, digest in produced.items()
                            if name in claimed and claimed[name] != digest)
        absent = sorted(name for name in claimed if name not in produced)
        record["mismatched"] = mismatched
        record["missing_from_build"] = absent
        record["matches"] = not mismatched and not absent
        record["status"] = "REPRODUCED" if record["matches"] else "MISMATCH"
        return record
    except (OSError, subprocess.SubprocessError) as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
        return record


# --------------------------------------------------------------------------- #
# SOURCE
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str, timeout: float = 120, text: bool = True):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=text, timeout=timeout)
    return proc


def _has_commit(repo: Path, commit: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def _is_ancestor(repo: Path, ancestor: str, commit: str) -> bool:
    return _git(repo, "merge-base", "--is-ancestor", ancestor, commit).returncode == 0


def _ls_tree(repo: Path, commit: str) -> set[str]:
    proc = _git(repo, "ls-tree", "-r", "--name-only", commit)
    if proc.returncode != 0:
        return set()
    return {line for line in proc.stdout.splitlines() if line}


def _tree_of(repo: Path, commit: str) -> str:
    return _git(repo, "rev-parse", f"{commit}^{{tree}}").stdout.strip()


def _diff_names(repo: Path, base: str, commit: str) -> list[str]:
    proc = _git(repo, "diff", "--name-only", base, commit)
    return [line for line in proc.stdout.splitlines() if line]


def _blob_sha(repo: Path, commit: str, path: str) -> str:
    proc = _git(repo, "rev-parse", f"{commit}:{path}")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _show(repo: Path, commit: str, path: str) -> bytes:
    proc = _git(repo, "show", f"{commit}:{path}", text=False)
    if proc.returncode != 0:
        raise SystemExit(f"git show {commit}:{path} failed: {proc.stderr!r}")
    return proc.stdout


def build_source_layer(repo: Path | str, pin: Mapping[str, Any], *,
                       workdir: Path | str | None = None) -> LayerResult:
    """Every pinned head exists, descends from the base, and carries its files."""
    repo = Path(repo)
    base = str(pin["base"]["commit"])
    if not _has_commit(repo, base):
        return LayerResult("SOURCE", BLOCKED, BLOCKED_MISSING_OBJECT,
                           (f"base commit {base} is not in {repo}",))
    units: dict[str, dict] = {}
    for unit in pin["units"]:
        uid = str(unit["id"])
        commit = str(unit["commit"])
        if not _has_commit(repo, commit):
            return LayerResult("SOURCE", BLOCKED, BLOCKED_MISSING_OBJECT,
                               (f"{uid}: commit {commit} is not in {repo}",))
        if not _is_ancestor(repo, base, commit):
            return LayerResult("SOURCE", BLOCKED, BLOCKED_ANCESTRY,
                               (f"{uid}: {commit} does not descend from the base "
                                f"{base}",))
        present = _ls_tree(repo, commit)
        missing = [path for path in unit["paths"] if path not in present]
        if missing:
            return LayerResult("SOURCE", BLOCKED, BLOCKED_MISSING_PATH,
                               tuple(f"{uid}: {path} is not in {commit}"
                                     for path in missing))
        recipe = "".join(f"{_blob_sha(repo, commit, path)}  {path}\n"
                         for path in sorted(unit["paths"]))
        units[uid] = {
            "pr": unit["pr"],
            "commit": commit,
            "tree": _tree_of(repo, commit),
            "files": len(unit["paths"]),
            "changed": len(_diff_names(repo, base, commit)),
            "manifest": "sha256:" + hashlib.sha256(
                recipe.encode("utf-8")).hexdigest(),
        }
    return LayerResult("SOURCE", PASS, OK, (), {
        "base": base,
        "repo": str(repo),
        "units": units,
        "ancestry": "every pinned head descends from the base",
    })


# --------------------------------------------------------------------------- #
# UNIT
# --------------------------------------------------------------------------- #
def _unit_env() -> dict:
    keep = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
            "HOME", "USERPROFILE", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in keep if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Built from an allow-list, so a result cannot depend on what the caller
    # happened to export (FLEET_NEXT_*, proxies, fake-green flags).
    return env


def _clone_at(repo: Path, commit: str, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    subprocess.run(["git", "clone", "--quiet", "--no-local", "--no-checkout",
                    str(repo), str(target)],
                   check=True, capture_output=True, timeout=300)
    # A clone copies branches, not the source repo's remote-tracking refs — and
    # a PR head usually lives in `refs/remotes/origin/pr/<n>`. Without this the
    # checkout of the pinned commit fails while `git cat-file -e` in the source
    # repo succeeds, which reads like "the pin is wrong" when it is not.
    subprocess.run(["git", "-C", str(target), "fetch", "--quiet", str(repo),
                    "+refs/remotes/origin/*:refs/remotes/origin/*"],
                   check=True, capture_output=True, timeout=300)
    # This tree hash is compared against a git blob digest; `core.autocrlf=true`
    # would check out CRLF and move every one of them.
    _git(target, "config", "core.autocrlf", "false")
    _git(target, "config", "core.eol", "lf")
    subprocess.run(["git", "-C", str(target), "checkout", "--quiet", commit],
                   check=True, capture_output=True, timeout=300)
    return target


def _junit_counts(path: Path) -> dict:
    if not path.is_file():
        return {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key) or 0)
    return {"passed": totals["tests"] - totals["failures"] - totals["errors"]
            - totals["skipped"],
            "failed": totals["failures"], "errors": totals["errors"],
            "skipped": totals["skipped"]}


def build_unit_layer(repo: Path | str, pin: Mapping[str, Any], *,
                     workdir: Path | str, platform: str,
                     timeout: float = 1800) -> LayerResult:
    """Run each pinned unit's own tests, from a clone at that exact commit."""
    repo = Path(repo)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"platform": platform, "units": {}}
    ran = 0
    for unit in pin["units"]:
        uid = str(unit["id"])
        if platform not in (unit.get("platforms") or ()):
            evidence["units"][uid] = {"state": NOT_RUN,
                                      "code": NOT_RUN_PLATFORM,
                                      "platforms": list(unit["platforms"])}
            continue
        tests = list(unit.get("unit_tests") or ())
        if not tests:
            evidence["units"][uid] = {"state": NOT_RUN,
                                      "code": "NO_UNIT_TESTS"}
            continue
        checkout = _clone_at(repo, str(unit["commit"]),
                             workdir / f"unit-{uid}")
        report = checkout / "junit.xml"
        started = time.time()
        proc = subprocess.run(
            [sys.executable, "-I", "-m", "pytest", *tests, "-q",
             "-p", "no:cacheprovider", "-o", "addopts=",
             f"--junitxml={report}"],
            cwd=str(checkout), capture_output=True, **_TEXT,
            timeout=timeout, env=_unit_env())
        counts = _junit_counts(report)
        evidence["units"][uid] = {
            "state": BLOCKED if counts["failed"] or counts["errors"] else PASS,
            "code": BLOCKED_UNIT_TEST if counts["failed"] or counts["errors"]
                    else OK,
            "commit": unit["commit"],
            "tests": tests,
            "returncode": proc.returncode,
            "seconds": round(time.time() - started, 2),
            **counts,
            "tail": (proc.stdout or "")[-600:] if proc.returncode else "",
        }
        ran += 1
        if counts["failed"] or counts["errors"]:
            return LayerResult("UNIT", BLOCKED, BLOCKED_UNIT_TEST,
                               (f"{uid}: {counts['failed']} failed, "
                                f"{counts['errors']} errors",), evidence)
    if ran == 0:
        return LayerResult("UNIT", NOT_RUN, NOT_RUN_PLATFORM,
                           (f"no pinned unit runs on {platform}",), evidence)
    return LayerResult("UNIT", PASS, OK, (), evidence)


# --------------------------------------------------------------------------- #
# HOSTED_CI
# --------------------------------------------------------------------------- #
def build_ci_layer(pin: Mapping[str, Any], *,
                   ci_reader: Callable[[Mapping[str, Any]], Mapping[str, Any]]
                   | None) -> LayerResult:
    if ci_reader is None:
        return LayerResult("HOSTED_CI", BLOCKED, BLOCKED_CI_NOT_MEASURED,
                           ("no CI reader was supplied: a green claim about a "
                            "hosted run needs that run's output",))
    try:
        reports = ci_reader(pin) or {}
    except Exception as exc:  # noqa: BLE001 - a failed read is a result
        return LayerResult("HOSTED_CI", BLOCKED, BLOCKED_CI_NOT_MEASURED,
                           (f"the CI reader failed: {type(exc).__name__}: {exc}",))
    known = dict(KNOWN_FLAKY)
    known.update(pin.get("known_flaky") or {})
    units: dict[str, Any] = {}
    blocked: list[str] = []
    not_measured: list[str] = []
    for unit in pin["units"]:
        uid = str(unit["id"])
        report = dict(reports.get(uid) or {})
        if not report:
            units[uid] = {"state": NOT_RUN, "code": BLOCKED_CI_MISSING}
            not_measured.append(uid)
            continue
        report.setdefault("expected_head", str(unit["commit"]))
        verdict = classify_ci_report(report, known=known)
        units[uid] = {**verdict, "pr": unit["pr"], "head": str(unit["commit"]),
                      # What was actually read, kept even when the classifier
                      # said no: a blocked layer still has to show its work.
                      "gates": list(report.get("gates") or ()),
                      "checks": list(report.get("checks") or ())}
        # Anything that is not the classifier's explicit green word is blocked:
        # a word this gate has not read before must not be folded as a pass.
        if verdict["state"] != CI_GREEN:
            blocked.append(uid)
    if blocked:
        return LayerResult("HOSTED_CI", BLOCKED,
                           str(units[blocked[0]]["code"]),
                           tuple(reason for uid in blocked
                                 for reason in units[uid]["reasons"]),
                           {"units": units})
    if not_measured:
        return LayerResult("HOSTED_CI", NOT_RUN, BLOCKED_CI_MISSING,
                           (f"no report for {', '.join(not_measured)}",),
                           {"units": units})
    return LayerResult("HOSTED_CI", PASS, OK, (), {"units": units})


# --------------------------------------------------------------------------- #
# BUILT_ARTIFACT
# --------------------------------------------------------------------------- #
def build_artifact_layer(pin: Mapping[str, Any], *, repo: Path | str | None = None,
                         artifact_dir: Path | str | None = None,
                         rebuild: Mapping[str, Any] | None = None) -> LayerResult:
    """The release bytes, or the absence of them.

    A `rebuild` result is recorded and can raise a finding, but it can never
    move this layer to PASS: the builder re-running its own code is the
    claimant checking itself, and a claim that agrees with its own author is
    still not a published artifact with a digest someone else can check.
    """
    spec = dict(pin.get("artifact") or {})
    names = [str(name) for name in spec.get("expected_names") or ()]
    expected = {str(name): str(digest)
                for name, digest in (spec.get("expected_sha256") or {}).items()}
    found = discover_artifacts(pin, repo=Path(repo) if repo else None,
                               artifact_dir=Path(artifact_dir)
                               if artifact_dir else None)
    by_name = {artifact.name: artifact for artifact in found}
    evidence: dict[str, Any] = {
        "mode": "ARTIFACT_BYTES",
        "published": bool(spec.get("published")),
        "pin_evidence": dict(spec.get("evidence") or {}),
        "artifacts": {name: f"sha256:{artifact.sha256}"
                      for name, artifact in by_name.items()},
        "missing": [name for name in names if name not in by_name],
        "searched": [str(path) for path in
                     [artifact_dir, *(spec.get("search_paths") or ())]
                     if path],
    }
    findings: list[Finding] = []
    if rebuild is not None:
        evidence["rebuild"] = dict(rebuild)
        if rebuild.get("status") == "MISMATCH":
            findings.append(Finding(
                code="BLOCKED_ARTIFACT_NOT_REPRODUCIBLE", severity="BLOCKER",
                summary=("the committed checksum file cannot be reproduced from "
                         "the commit it names, so its digests describe no "
                         "published tree"),
                owner="#509", file="evidence/ARTIFACTS.sha256",
                condition=("rebuilding at the recorded source commit with the "
                           "committed builder reproduces every claimed digest"),
                evidence={"mismatched": list(rebuild.get("mismatched") or ()),
                          "missing_from_build": list(
                              rebuild.get("missing_from_build") or ()),
                          "source_commit": rebuild.get("source_commit"),
                          "builder_commit": rebuild.get("builder_commit")}))
    if not spec.get("published") and not names:
        evidence["mode"] = "NOT_PUBLISHED"
        if by_name:
            return LayerResult(
                "BUILT_ARTIFACT", BLOCKED, BLOCKED_ARTIFACT_UNPINNED,
                ("bytes were found where the pin expects none, so nothing can "
                 "say which release they are: "
                 + ", ".join(sorted(by_name)),), evidence, findings)
        return LayerResult(
            "BUILT_ARTIFACT", BLOCKED, BLOCKED_ARTIFACT,
            ("no published release bytes exist, and the pin carries no digest "
             "to check one against; the builder's checksums are a claim about "
             "bytes it did not publish",), evidence, findings)
    missing = evidence["missing"]
    if missing:
        return LayerResult("BUILT_ARTIFACT", BLOCKED, BLOCKED_ARTIFACT,
                           tuple(f"{name}: not found" for name in missing),
                           evidence, findings)
    bad = [name for name, artifact in by_name.items()
           if name in expected and not verify_artifact_digest(
               artifact.path, expected[name])]
    if bad:
        return LayerResult("BUILT_ARTIFACT", BLOCKED, BLOCKED_DIGEST,
                           tuple(f"{name}: the bytes do not hash to the pinned "
                                 f"digest" for name in bad), evidence, findings)
    evidence["verified"] = sorted(by_name)
    return LayerResult("BUILT_ARTIFACT", PASS, OK, (), evidence, findings)


# --------------------------------------------------------------------------- #
# LEGACY_SURVIVAL
# --------------------------------------------------------------------------- #
def _compose_package(repo: Path, pin: Mapping[str, Any], world: Path) -> dict:
    """Write the release package from the pinned blobs, then commit it once."""
    package = world / "package"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    units = {str(unit["id"]): unit for unit in pin["units"]}
    per_file: dict[str, str] = {}
    for member in pin["release_package"]["files"]:
        unit = units[str(member["source"])]
        origin = member.get("from") or member["path"]
        body = _show(repo, str(unit["commit"]), str(origin))
        target = package / member["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        per_file[str(member["path"])] = "sha256:" + hashlib.sha256(body).hexdigest()
    env = _unit_env()
    env.update({
        "GIT_AUTHOR_NAME": "Wave18 Acceptance",
        "GIT_AUTHOR_EMAIL": "acceptance@example.invalid",
        "GIT_COMMITTER_NAME": "Wave18 Acceptance",
        "GIT_COMMITTER_EMAIL": "acceptance@example.invalid",
        "GIT_AUTHOR_DATE": "2026-10-29T00:00:00 +0000",
        "GIT_COMMITTER_DATE": "2026-10-29T00:00:00 +0000",
    })
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(package)],
                   check=True, capture_output=True, env=env, timeout=120)
    _git(package, "config", "core.autocrlf", "false")
    _git(package, "config", "core.eol", "lf")
    subprocess.run(["git", "-C", str(package), "add", "-A"], check=True,
                   capture_output=True, env=env, timeout=120)
    subprocess.run(["git", "-C", str(package), "commit", "--quiet",
                    "--no-gpg-sign", "-m", "wave18 composed release package"],
                   check=True, capture_output=True, env=env, timeout=120)
    commit = _git(package, "rev-parse", "HEAD").stdout.strip()
    (world / "package.commit").write_text(commit + "\n", encoding="utf-8")
    recipe = "".join(f"{digest}  {path}\n"
                     for path, digest in sorted(per_file.items()))
    return {"commit": commit, "files": len(per_file), "per_file": per_file,
            "manifest": "sha256:" + hashlib.sha256(
                recipe.encode("utf-8")).hexdigest()}


def _run_step(step: str, *, world: Path, pin_path: Path, engine_path: Path,
              preflight_path: Path, repo: Path, timeout: float) -> dict:
    env = _unit_env()
    command = [sys.executable, "-I", str(DRILL_STEP), "--step", step,
               "--world", str(world), "--pin", str(pin_path),
               "--engine", str(engine_path),
               "--preflight", str(preflight_path), "--repo", str(repo)]
    started = time.time()
    proc = subprocess.run(command, cwd=str(world), capture_output=True,
                          **_TEXT, timeout=timeout, env=env)
    record = {"step": step, "exit": proc.returncode,
              "seconds": round(time.time() - started, 2),
              "pid": None, "report": {}, "stdout": proc.stdout[-4000:],
              "stderr": proc.stderr[-4000:]}
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        record["report"] = payload
        record["pid"] = payload.get("pid")
        break
    return record


def _default_engine(pin: Mapping[str, Any], repo: Path, world: Path) -> Path:
    """The pinned engine, extracted from the commit the pin names."""
    spec = pin.get("drill") or {}
    unit_id = spec.get("engine_unit")
    if not unit_id:
        return FIXTURE_ENGINE
    units = {str(unit["id"]): unit for unit in pin["units"]}
    unit = units[str(unit_id)]
    target = world / "modules" / Path(str(spec["engine_path"])).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_show(repo, str(unit["commit"]), str(spec["engine_path"])))
    return target


def _default_preflight(pin: Mapping[str, Any], repo: Path, world: Path) -> Path:
    spec = pin.get("drill") or {}
    unit_id = spec.get("preflight_unit")
    if not unit_id:
        return FIXTURE_ENGINE
    units = {str(unit["id"]): unit for unit in pin["units"]}
    unit = units[str(unit_id)]
    target = world / "modules" / Path(str(spec["preflight_path"])).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_show(repo, str(unit["commit"]),
                             str(spec["preflight_path"])))
    return target


def build_survival_layer(repo: Path | str, pin: Mapping[str, Any], *,
                         workdir: Path | str, pin_path: Path | str,
                         engine_path: Path | str,
                         preflight_path: Path | str,
                         timeout: float = 300) -> LayerResult:
    """The drill: six processes, one disposable world, one Legacy snapshot each."""
    repo = Path(repo).resolve()
    world = Path(workdir).resolve()
    try:
        world.relative_to(repo)
    except ValueError:
        pass
    else:
        return LayerResult("LEGACY_SURVIVAL", BLOCKED, BLOCKED_SHARED_RESOURCE,
                           (f"{world} is inside the repository {repo}; the drill "
                            f"may not write where the release lives",))
    world.mkdir(parents=True, exist_ok=True)
    # Every step runs with `cwd` in the world, so a relative pin path is not the
    # same file inside the step as it was outside it.
    pin_path = Path(pin_path).resolve()
    package = _compose_package(repo, pin, world)

    records: list[dict] = []
    for step in DRILL_ORDER:
        record = _run_step(step, world=world, pin_path=Path(pin_path),
                           engine_path=Path(engine_path),
                           preflight_path=Path(preflight_path), repo=repo,
                           timeout=timeout)
        records.append(record)
        if record["exit"] != 0 or record["report"].get("error"):
            return LayerResult(
                "LEGACY_SURVIVAL", BLOCKED, BLOCKED_DRILL_FAILED,
                (f"{step} exited {record['exit']}"
                 + (f": {record['report']['error']}" if record["report"].get(
                     "error") else "")
                 + (f" | stderr: {record['stderr'][-600:].strip()}"
                    if record["stderr"].strip() else ""),),
                {"processes": [{"step": r["step"], "pid": r["pid"],
                                "exit": r["exit"], "seconds": r["seconds"]}
                               for r in records],
                 "package": package, "engine": str(engine_path)})

    by = {record["step"]: record["report"] for record in records}
    processes = [{"step": record["step"], "pid": record["pid"],
                  "exit": record["exit"], "seconds": record["seconds"]}
                 for record in records]
    pids = [process["pid"] for process in processes]
    if not pids or any(pid is None for pid in pids) or len(set(pids)) < 3:
        return LayerResult("LEGACY_SURVIVAL", BLOCKED, BLOCKED_DRILL_NOT_ISOLATED,
                           (f"the drill claims {len(records)} steps but ran in "
                            f"{len([pid for pid in pids if pid is not None])} "
                            f"process(es)",), {"processes": processes})

    problems: list[str] = []
    install = by["install"]
    if install.get("code") != OK:
        problems.append(f"install refused: {install.get('code')}")
    if not install.get("receipt_intact"):
        problems.append("the installed receipt does not verify")

    observe = by["observe"]
    if observe.get("completed_without_activity", {}).get("status") != "UNKNOWN":
        problems.append("an observer with nothing to observe did not say UNKNOWN")
    if observe.get("offline", {}).get("status") != "OFFLINE":
        problems.append("an observer whose source is offline did not say OFFLINE")
    if observe.get("native_activity", {}).get("status") != "TERMINAL_VERIFIED":
        problems.append("an observer with native activity did not say "
                        "TERMINAL_VERIFIED")
    if observe.get("typed_subject_without_authorization", {}).get("code") != \
            "BLOCKED_SUBJECT":
        problems.append("a typed subject with no authorization was not refused")
    if observe.get("native_activity", {}).get("status") == \
            observe.get("completed_without_activity", {}).get("status"):
        problems.append("the observer cannot tell native activity from silence")

    legacy_steps = [record["step"] for record in records]
    baseline = by["seed"].get("baseline") or {}
    snapshots: dict[str, Any] = {"seed": baseline}
    for record in records:
        if record["step"] != "seed":
            snapshots[record["step"]] = record["report"].get("snapshot")
    for step, snap in snapshots.items():
        if snap != baseline:
            differences = sorted(key for key in set(snap or {}) | set(baseline)
                                 if (snap or {}).get(key) != baseline.get(key))
            problems.append(f"the Legacy snapshot moved after {step}: "
                            f"{', '.join(differences) or 'absent'}")
    if list(baseline.get("deliveries") or ()) != ["delivery-1"]:
        problems.append(f"the Legacy ledger is {baseline.get('deliveries')!r}; "
                        f"an offline drill may not add a delivery")

    failed = by["failed-upgrade"]
    if failed.get("refused", {}).get("code") != "BLOCKED_SOURCE_DIGEST":
        problems.append(f"an upgrade with unpinned bytes was not refused as "
                        f"BLOCKED_SOURCE_DIGEST "
                        f"({failed.get('refused', {}).get('code')})")
    if not failed.get("installed_bytes_unchanged"):
        problems.append("a refused upgrade changed the installed bytes")
    version = str(pin["release_package"]["version"])
    if failed.get("receipt_still_names") != version:
        problems.append(f"the receipt after a refused upgrade names "
                        f"{failed.get('receipt_still_names')!r}, not {version!r}")

    partial = failed.get("partial") or {}
    if partial.get("outcome") != "PARTIAL_WRITE":
        problems.append("the mid-write failure did not happen, so the drill "
                        "cannot say what it leaves behind")
    if partial.get("receipt_written"):
        problems.append("a half-written upgrade wrote a receipt: the root now "
                        "claims an install it does not contain")

    rollback = by["rollback"]
    if rollback.get("code") != OK:
        problems.append(f"the revoke was refused: {rollback.get('code')}")
    if list(rollback.get("deleted") or ()) != list(rollback.get("owned") or ()):
        problems.append(f"the revoke deleted {rollback.get('deleted')!r} but the "
                        f"receipt owned {rollback.get('owned')!r}")
    if not rollback.get("receipt_gone"):
        problems.append("the receipt survived the revoke")
    if not rollback.get("backup_gone"):
        problems.append("the backup directory survived the revoke")
    if rollback.get("left_behind"):
        problems.append(f"the revoke left {rollback['left_behind']!r} behind")

    injections = dict(by["probe"].get("injections") or {})
    injections["partial_write"] = {
        "outcome": partial.get("outcome"),
        "orphans": partial.get("orphans"),
        "receipt_written": partial.get("receipt_written"),
        "receipt_still_names": partial.get("receipt_still_names"),
        "revoke_after_partial": partial.get("revoke_after_partial") or {},
        "error": partial.get("error"),
    }
    for name, expected_code in EXPECTED_INJECTIONS.items():
        measured = (injections.get(name) or {}).get("code")
        if measured != expected_code:
            problems.append(f"injection {name!r} answered {measured!r}, "
                            f"expected {expected_code!r}")

    findings: list[Finding] = []
    trapped = (partial.get("outcome") == "PARTIAL_WRITE"
               and bool(partial.get("orphans"))
               and not partial.get("receipt_written")
               and (partial.get("revoke_after_partial") or {}).get("code")
               == "BLOCKED_FOREIGN_PRESENT")
    if trapped:
        findings.append(Finding(
            code="BLOCKED_PARTIAL_UPGRADE_TRAPS_ROOT",
            severity="BLOCKER",
            summary=("an upgrade that fails mid-write leaves files the old "
                     "receipt does not own, and revoke then refuses "
                     "BLOCKED_FOREIGN_PRESENT — the root cannot be recovered "
                     "without deleting files by hand"),
            owner="#506",
            file="integrations/fleet-next-wave17-sidecar-release/sidecar_release.py",
            condition=("install() either writes to a staging directory and "
                       "renames, or removes what it wrote when a write fails, "
                       "or revoke treats files written by a failed upgrade as "
                       "its own; the drill must then record "
                       "revoke_after_partial.code == OK"),
            evidence={"orphans": partial.get("orphans"),
                      "receipt_still_names": partial.get("receipt_still_names"),
                      "revoke_after_partial":
                          partial.get("revoke_after_partial"),
                      "engine_under_test": "the pinned engine, at the pin's "
                                           "commit",
                      "obstacle": "a regular file where the upgrade needs a "
                                  "directory"}))
    elif partial.get("outcome") == "PARTIAL_WRITE":
        problems.append("the documented partial-upgrade trap did not reproduce: "
                        "the measured behaviour and the release evidence "
                        "disagree, so neither can be trusted")

    evidence = {
        "processes": processes,
        "package": package,
        "engine": {"path": str(engine_path), "digest": _sha256_file(
            Path(engine_path))},
        "preflight": {"path": str(preflight_path), "digest": _sha256_file(
            Path(preflight_path))},
        "legacy": {
            "baseline": baseline,
            "snapshots": snapshots,
            "tree_digest_unchanged":
                (snapshots.get("probe") or {}).get("tree_digest")
                == baseline.get("tree_digest")
                and all((snap or {}).get("tree_digest") == baseline.get(
                    "tree_digest") for snap in snapshots.values()),
            "deliveries": baseline.get("deliveries"),
            "steps": legacy_steps,
        },
        "install": {key: install.get(key) for key in
                    ("code", "written", "manifest", "files",
                     "receipt_intact")},
        "observer": {key: observe.get(key) for key in
                     ("fake_observer", "vocabulary",
                      "completed_without_activity", "offline",
                      "native_activity",
                      "typed_subject_without_authorization",
                      "typed_subject_with_authorization", "receipt_intact")},
        "failed_upgrade": {"refused": failed.get("refused"),
                           "receipt_still_names": failed.get(
                               "receipt_still_names"),
                           "installed_bytes_unchanged": failed.get(
                               "installed_bytes_unchanged")},
        "rollback": {key: rollback.get(key) for key in
                     ("code", "deleted", "owned", "receipt_gone",
                      "backup_gone", "left_behind")},
        "injections": injections,
    }
    if problems:
        return LayerResult("LEGACY_SURVIVAL", BLOCKED, BLOCKED_DRILL_EVIDENCE,
                           tuple(problems), evidence, tuple(findings))
    return LayerResult("LEGACY_SURVIVAL", PASS, OK, (), evidence,
                       tuple(findings))


# --------------------------------------------------------------------------- #
# REAL_DEVICE
# --------------------------------------------------------------------------- #
def build_real_device_layer(*, allow: bool = False,
                            authorization: str | None = None) -> LayerResult:
    """Always `NOT_RUN`. A device pass is a separate authorization, not a flag.

    `allow` and `authorization` are accepted so that a caller who tries to turn
    this green gets a `NOT_RUN` back rather than a `TypeError`: the refusal is
    the answer, and it should be legible at the call site.
    """
    return LayerResult(
        "REAL_DEVICE", NOT_RUN, NOT_RUN_REAL_DEVICE,
        ("no device was addressed; installing on a real host, touching "
         "systemd, OAuth, credentials or the Legacy Fleet needs a separate "
         "authorization from the Owner, and this gate is not that "
         "authorization"
         + ("" if not (allow or authorization) else
            " (the request to run it was recorded and refused)"),),
        {"executed": False,
         "requested": bool(allow or authorization),
         "requires": ["Owner authorization for REAL_DEVICE",
                      "a maintenance window on the named device",
                      "a rollback path that has been exercised on that device"],
         "device": None})


# --------------------------------------------------------------------------- #
# the matrix
# --------------------------------------------------------------------------- #
def aggregate(layers: Sequence[LayerResult]) -> Verdict:
    """Fold the six layers into one verdict. Silence is never green."""
    by_name = {layer.name: layer for layer in layers}
    reasons: list[str] = []
    missing = [name for name in LAYERS if name not in by_name]
    if missing:
        reasons.extend(f"MISSING layer {name}" for name in missing)
    unknown = [layer.name for layer in layers
               if layer.state not in (PASS, BLOCKED, NOT_RUN)]
    if unknown:
        reasons.extend(f"{name} reports an unknown state "
                       f"{by_name[name].state!r}" for name in unknown)
    for name in LAYERS:
        layer = by_name.get(name)
        if layer is None:
            continue
        if (layer.name == "LEGACY_SURVIVAL" and layer.state == PASS
                and "processes" in (layer.evidence or {})
                and not layer.evidence["processes"]):
            reasons.append(f"{name} reports PASS with no process evidence")
            layer = LayerResult(name, BLOCKED, BLOCKED_NO_EVIDENCE,
                                layer.reasons, layer.evidence, layer.findings)
            by_name[name] = layer
        if layer.state == BLOCKED:
            reasons.append(f"{name}: {layer.code}")
            reasons.extend(layer.reasons)
    findings: list[Finding] = [finding for name in LAYERS
                               for finding in (by_name.get(name).findings
                                               if by_name.get(name) else ())]
    blockers = [finding for finding in findings
                if finding.severity.upper() == "BLOCKER"]
    if missing:
        return Verdict("INCOMPLETE", BLOCKED_MISSING_LAYER, tuple(reasons),
                       tuple(layers), tuple(findings))
    if unknown:
        # A state this gate does not know is not a pass it can read: a new
        # vocabulary word means the layer is measuring something the matrix
        # was not written to fold.
        return Verdict("INCOMPLETE", BLOCKED_LAYER_STATE, tuple(reasons),
                       tuple(layers), tuple(findings))
    blocked = [name for name in LAYERS if by_name[name].state == BLOCKED]
    if blocked:
        return Verdict(BLOCKED, by_name[blocked[0]].code, tuple(reasons),
                       tuple(layers), tuple(findings))
    if blockers:
        return Verdict(BLOCKED, blockers[0].code,
                       tuple(reasons + [f"BLOCKER finding {finding.code} "
                                        f"(owner {finding.owner})"
                                        for finding in blockers]),
                       tuple(layers), tuple(findings))
    not_run = [name for name in PROVABLE if by_name[name].state == NOT_RUN]
    if not_run:
        return Verdict("INCOMPLETE", BLOCKED_INCOMPLETE,
                       tuple(reasons + [f"{name} was not measured"
                                        for name in not_run]),
                       tuple(layers), tuple(findings))
    if by_name["REAL_DEVICE"].state != NOT_RUN:
        return Verdict("INCOMPLETE", BLOCKED_LAYER_STATE,
                       tuple(reasons + ["REAL_DEVICE reported a state other "
                                        "than NOT_RUN"]),
                       tuple(layers), tuple(findings))
    return Verdict("READY", OK, tuple(reasons), tuple(layers), tuple(findings))


def matrix_rows(layers: Sequence[LayerResult]) -> list[dict]:
    by_name = {layer.name: layer for layer in layers}
    rows: list[dict] = []
    for name in LAYERS:
        layer = by_name.get(name)
        rows.append({"layer": name,
                     "state": layer.state if layer else "MISSING",
                     "code": layer.code if layer else BLOCKED_MISSING_LAYER,
                     "findings": len(layer.findings) if layer else 0,
                     "evidence": dict(layer.evidence) if layer else {}})
    return rows


# --------------------------------------------------------------------------- #
# the whole run
# --------------------------------------------------------------------------- #
def run_acceptance(repo: Path | str, *, pin_path: Path | str = PIN_PATH,
                   workdir: Path | str | None = None, online: bool = False,
                   engine_path: Path | str | None = None,
                   preflight_path: Path | str | None = None,
                   ci_reader: Callable[[Mapping[str, Any]], Mapping[str, Any]]
                   | None = None,
                   platform: str | None = None,
                   unit_timeout: float = 1800,
                   artifact_dir: Path | str | None = None,
                   rebuild: bool = False) -> Verdict:
    """Measure every layer that can be measured here, then fold them into one."""
    repo = Path(repo)
    platform = platform or sys.platform
    pin = load_pin(pin_path)
    problems = validate_pin(pin)
    if problems:
        return Verdict("INCOMPLETE", BLOCKED_PIN, tuple(problems), (), ())

    owned_temp = workdir is None
    world = Path(workdir) if workdir is not None else Path(
        tempfile.mkdtemp(prefix="wave18-acceptance-"))
    try:
        engine = Path(engine_path) if engine_path else _default_engine(
            pin, repo, world)
        preflight = Path(preflight_path) if preflight_path else _default_preflight(
            pin, repo, world)
        rebuilt = reproduce_artifacts(pin, repo, world) if rebuild else None
        layers = [
            build_source_layer(repo, pin, workdir=world / "source"),
            build_unit_layer(repo, pin, workdir=world / "units",
                             platform=platform, timeout=unit_timeout),
            build_ci_layer(pin, ci_reader=ci_reader),
            build_artifact_layer(pin, repo=repo, artifact_dir=artifact_dir,
                                 rebuild=rebuilt),
            build_survival_layer(repo, pin, workdir=world / "drill",
                                 pin_path=Path(pin_path), engine_path=engine,
                                 preflight_path=preflight),
            build_real_device_layer(),
        ]
        return aggregate(layers)
    finally:
        if owned_temp:
            shutil.rmtree(world, ignore_errors=True)


REPO_SLUG = "ProfessorZhi/serveros"


def _gh(*args: str, timeout: float = 180) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", "api", *args], capture_output=True,
                          **_TEXT, timeout=timeout)


def _gh_ci_reader(pin: Mapping[str, Any]):
    """Read the hosted run for each unit through `gh`, exactly at its head.

    One check-run per job is not enough: the baseline gate is a line *inside* a
    job's log, so the log is fetched and parsed (`--allow-escape-sequences`,
    then the ANSI is stripped — `gh` refuses to print logs to a pipe without
    it). A job whose log cannot be read contributes no gate, and
    `classify_ci_report` turns "no gate" into `BLOCKED_CI_NO_GATE` rather than
    into a pass.

    Two things in the log are the runner's, not the gate's: the ANSI colour and
    the `2026-10-29T13:12:14.8643423Z ` timestamp every line carries. Both are
    stripped before parsing, or an anchored summary regex never matches and a
    clean run reads as unparsed.
    """
    def read(_pin: Mapping[str, Any]) -> Mapping[str, Any]:
        reports: dict[str, Any] = {}
        for unit in pin["units"]:
            uid, sha = str(unit["id"]), str(unit["commit"])
            runs = _gh(f"repos/{REPO_SLUG}/actions/runs?head_sha={sha}",
                       "--jq", '.workflow_runs[] | "\\(.id) \\(.name)"')
            if runs.returncode != 0:
                reports[uid] = {"head": sha, "expected_head": sha, "gates": [],
                                "error": runs.stderr.strip()[-400:]}
                continue
            checks: list[dict] = []
            gates: list[dict] = []
            for line in (runs.stdout or "").splitlines():
                parts = line.strip().split(" ", 1)
                if not parts or not parts[0].isdigit():
                    continue
                run_id = parts[0]
                jobs = _gh(f"repos/{REPO_SLUG}/actions/runs/{run_id}/jobs",
                           "--jq", '.jobs[] | "\\(.id) \\(.name) \\(.conclusion)"')
                for job_line in (jobs.stdout or "").splitlines():
                    fields = job_line.strip().split(" ")
                    if len(fields) < 3:
                        continue
                    job_id, name, conclusion = fields[0], " ".join(fields[1:-1]), \
                        fields[-1]
                    if "shard" not in name.lower():
                        checks.append({"name": name, "status": "completed",
                                       "conclusion": conclusion})
                        continue
                    logs = _gh(f"repos/{REPO_SLUG}/actions/jobs/{job_id}/logs",
                               "--allow-escape-sequences", timeout=300)
                    parsed = parse_baseline_gate(
                        strip_runner_prefix(logs.stdout or ""))
                    match = re.search(r"shard\s+(\d+/\d+)", name, re.I)
                    gates.append({"shard": match.group(1) if match else name,
                                  "job": job_id, "conclusion": conclusion,
                                  **parsed})
            reports[uid] = {"head": sha, "expected_head": sha,
                            "checks": checks, "gates": gates}
        return reports
    return read


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wave 18 Fleet Next release acceptance")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--pin", default=str(PIN_PATH))
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--artifact-dir", default=None)
    parser.add_argument("--engine", default=None)
    parser.add_argument("--preflight", default=None)
    parser.add_argument("--offline", action="store_true",
                        help="do not read the hosted run through gh")
    parser.add_argument("--platform", default=None)
    parser.add_argument("--unit-timeout", type=float, default=1800)
    parser.add_argument("--rebuild", action="store_true",
                        help="re-run the builder's own code at its recorded "
                             "source commit (reproducibility, not independence)")
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--table", action="store_true")
    args = parser.parse_args(argv)

    ci_reader = None if args.offline else _gh_ci_reader(load_pin(args.pin))
    verdict = run_acceptance(
        args.repo, pin_path=args.pin, workdir=args.workdir,
        online=not args.offline, engine_path=args.engine,
        preflight_path=args.preflight, ci_reader=ci_reader,
        platform=args.platform, unit_timeout=args.unit_timeout,
        artifact_dir=args.artifact_dir, rebuild=args.rebuild)
    body = json.dumps(verdict.as_dict(), sort_keys=True, indent=2,
                      ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(body + "\n", encoding="utf-8")
    if args.table:
        for row in matrix_rows(verdict.layers):
            print(f"{row['layer']:<16} {row['state']:<9} {row['code']}")
        for finding in verdict.findings:
            print(f"\n[{finding.severity}] {finding.code} owner {finding.owner}\n"
                  f"  {finding.summary}\n  close when: {finding.condition}")
        print(f"\n{verdict.state} {verdict.code} (exit {verdict.exit_code})")
    else:
        print(body)
    return verdict.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
