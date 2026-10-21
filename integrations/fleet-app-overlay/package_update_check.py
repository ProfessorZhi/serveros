#!/usr/bin/env python3
"""Offline same-ID update, readback and rollback checks for the Fleet overlay.

This is the checkable half of `UPDATE-MANUAL.md`. It reads local overlay
archives and release records, never a Creator, a token or a network. Two
properties are deliberate:

* **The identity and protected configuration are compared, not assumed.** The
  plugin id, manifest name, `displayName` and `apps` reference must be
  identical; version/keywords/onboarding/local-executor are the only manifest
  changes; the complete protected root/compat manifest projection, MCP
  structures and referenced files, and non-Skill member bytes must match. The
  version must advance and a previous Skill may not disappear. A release record
  whose archive hash does not match the bytes on disk is refused before any
  plan is emitted.
* **A readback is a claim with provenance.** A receipt is labelled
  `SYNTHETIC_LOCAL_REHEARSAL` or `OPERATOR_SUPPLIED_RECEIPT`; the result always
  says `production_verified: false`. A receipt labelled `LIVE` is refused here
  because this tool did not observe a live Creator and will not authenticate
  someone else's claim.

A rollback check produces a target, never an action. The rehearsal validates
source, shared-Skill and output paths before any copy/write and never removes a
caller-owned directory. Nothing here uploads, installs, creates a plugin,
changes OAuth/services/routes or writes outside the caller's output directory.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"

PLAN_SCHEMA = "serveros/fleet-app-update-plan.v1"
READBACK_SCHEMA = "serveros/fleet-app-update-readback.v1"
READBACK_RESULT_SCHEMA = "serveros/fleet-app-update-readback-result.v1"
ROLLBACK_SCHEMA = "serveros/fleet-app-rollback-check.v1"
REHEARSAL_SCHEMA = "serveros/fleet-app-update-rehearsal.v1"
OVERLAY_SCHEMA = "serveros/fleet-app-overlay.v1"
READBACK_SOURCES = ("SYNTHETIC_LOCAL_REHEARSAL", "OPERATOR_SUPPLIED_RECEIPT")
SOURCE_SYNTHETIC = "SYNTHETIC_LOCAL_REHEARSAL"
SOURCE_OPERATOR = "OPERATOR_SUPPLIED_RECEIPT"
SEMVER_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")

# The update reference permits only version, Skills/supporting files, keywords,
# onboarding selection and the local-executor requirement to change. Everything
# else in the manifest is App-/Creator-owned and must remain effective.
ROOT_ALLOWED_PATHS = frozenset({
    ("version",),
    ("keywords",),
    ("onboardingSkill",),
    ("requires_local_executor",),
    ("localExecutor",),
    ("extensions", "com.openai", "onboardingSkill"),
    ("extensions", "com.openai", "requires_local_executor"),
    ("extensions", "com.openai", "localExecutor"),
})
COMPAT_ALLOWED_PATHS = frozenset({
    ("version",),
    ("keywords",),
    ("onboardingSkill",),
    ("requires_local_executor",),
    ("localExecutor",),
    ("interface", "onboardingSkill"),
    ("interface", "requires_local_executor"),
    ("interface", "localExecutor"),
})

OLD_SKILLS = (
    "fleet-create-with-grants",
    "fleet-device-and-session-discovery",
    "fleet-evidence-review",
    "fleet-reuse-idle-session",
    "fleet-safety-boundaries",
)
NEW_SKILLS = (
    "fleet-async-dispatch",
    "fleet-commander-orchestration",
    "fleet-plugin-update",
)


class Refused(Exception):
    """A local package or receipt does not satisfy the update contract."""


def _read_json(path: Path, what: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused(f"{what} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise Refused(f"{what} must be a JSON object")
    return value


def _write_json(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _semver(value: object, what: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise Refused(f"{what} {value!r} is not MAJOR.MINOR.PATCH")
    match = SEMVER_RE.match(value)
    if not match:
        raise Refused(f"{what} {value!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _load_record(path: Path) -> dict:
    record = _read_json(Path(path), "release record")
    if record.get("schema") != OVERLAY_SCHEMA:
        raise Refused(f"release record schema is {record.get('schema')!r}, not {OVERLAY_SCHEMA!r}")
    if record.get("distribution") != "PRIVATE_APP_BACKED_OVERLAY_ONLY":
        raise Refused(f"release record distribution is {record.get('distribution')!r}; "
                      f"this workflow only checks the private App-backed overlay path")
    for field in ("plugin_id", "name", "version", "archive", "archive_sha256"):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise Refused(f"release record names no {field}")
    return record


def _archive_path(record: dict, archive: Path | None) -> Path:
    if archive is not None:
        return Path(archive)
    return Path(record["archive"])


def _member_sha(zf, name: str) -> str | None:
    try:
        return hashlib.sha256(zf.read(name)).hexdigest()
    except KeyError:
        return None


def _read_member_json(zf, name: str, archive_name: str):
    if name not in zf.namelist():
        return None
    try:
        value = json.loads(zf.read(name).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused(f"{archive_name}: {name} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise Refused(f"{archive_name}: {name} is not a JSON object")
    return value


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _drop_paths(value: dict, paths) -> dict:
    clone = copy.deepcopy(value)
    for path in paths:
        current = clone
        for key in path[:-1]:
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if isinstance(current, dict):
            current.pop(path[-1], None)
    return clone


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)


def _mcp_references(config: dict, member_sha256: dict[str, str]) -> dict[str, str]:
    """Local files named by an MCP config, by plugin-relative path."""
    found: dict[str, str] = {}
    for text in _iter_strings(config):
        candidate = text
        if candidate.startswith("${PLUGIN_ROOT}/"):
            candidate = candidate[len("${PLUGIN_ROOT}/"):]
        elif candidate.startswith("./"):
            candidate = candidate[2:]
        else:
            if "://" in candidate or candidate.startswith("/"):
                continue
        if candidate in member_sha256:
            found[candidate] = member_sha256[candidate]
    return found


def archive_view(record: dict, archive: Path) -> dict:
    """Read one archive and prove it is the archive its record names."""
    archive = Path(archive)
    if not archive.is_file():
        raise Refused(f"archive is absent: {archive}")
    actual = _sha256(archive)
    if actual != record.get("archive_sha256"):
        raise Refused(f"archive_sha256 for {archive.name} is {actual}, not the "
                      f"recorded {record.get('archive_sha256')}")
    try:
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            if "plugin.json" not in names:
                raise Refused(f"{archive.name} carries no plugin.json")
            manifest = json.loads(zf.read("plugin.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                raise Refused(f"{archive.name} plugin.json is not an object")
            openai = ((manifest.get("extensions") or {}).get("com.openai") or {})
            if not isinstance(openai, dict):
                raise Refused(f"{archive.name} has no extensions.com.openai block")
            # The overlay fixture writes the presentation fields directly under
            # the namespace; the first-install builder writes them under
            # `interface`. Both are local shapes this checker may see, so it
            # reads the documented nested object when present and otherwise the
            # namespace object itself. It does not invent a field.
            interface = openai.get("interface") if isinstance(openai.get("interface"), dict) else openai
            skills = sorted({
                name.split("/")[1]
                for name in names
                if name.startswith("skills/") and name.endswith("/SKILL.md")
                and name.count("/") == 2
            })
            if not skills:
                raise Refused(f"{archive.name} carries no skills/<name>/SKILL.md")
            skill_sha256 = {
                name: _member_sha(zf, f"skills/{name}/SKILL.md") for name in skills
            }
            icons: dict[str, str] = {}
            for slot in ("composerIcon", "logo"):
                value = interface.get(slot)
                if isinstance(value, str) and value.startswith("./"):
                    digest = _member_sha(zf, value[2:])
                    if digest is not None:
                        icons[slot] = digest
            apps_path = interface.get("apps")
            apps_sha256 = None
            if isinstance(apps_path, str) and apps_path.startswith("./"):
                apps_sha256 = _member_sha(zf, apps_path[2:])
            member_sha256 = {
                name: hashlib.sha256(zf.read(name)).hexdigest()
                for name in names if not name.endswith("/")
            }
            compat_manifest = _read_member_json(
                zf, ".codex-plugin/plugin.json", archive.name)
            mcp = _read_member_json(zf, "mcp.json", archive.name)
            legacy_mcp = _read_member_json(zf, ".mcp.json", archive.name)
    except Refused:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise Refused(f"{archive.name} is not a readable overlay archive: {exc}") from exc
    if manifest.get("name") != record["name"]:
        raise Refused(f"{archive.name} plugin.json name {manifest.get('name')!r} "
                      f"does not match the release record {record['name']!r}")
    if manifest.get("version") != record["version"]:
        raise Refused(f"{archive.name} plugin.json version {manifest.get('version')!r} "
                      f"does not match the release record {record['version']!r}")
    return {
        "plugin_id": record["plugin_id"],
        "name": manifest.get("name"),
        "version": manifest.get("version"),
        "display_name": interface.get("displayName"),
        "apps": interface.get("apps"),
        "skills": skills,
        "skill_sha256": skill_sha256,
        "icon_sha256": icons,
        "app_sha256": apps_sha256,
        "members": sorted(member_sha256),
        "member_sha256": member_sha256,
        "root_manifest": manifest,
        "compat_manifest": compat_manifest,
        "mcp": mcp,
        "legacy_mcp": legacy_mcp,
        "archive_sha256": actual,
    }


def _check(checks: list[dict], name: str, ok: bool, detail: str) -> None:
    checks.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    if not ok:
        raise Refused(f"{name}: {detail}")


def plan_update(*, previous_record: dict, previous_archive: Path,
                next_record: dict, next_archive: Path) -> dict:
    """Compare two local overlay releases under the same plugin identity."""
    previous = archive_view(previous_record, previous_archive)
    following = archive_view(next_record, next_archive)
    checks: list[dict] = []

    _check(checks, "plugin_id",
           previous["plugin_id"] == following["plugin_id"],
           f"{previous['plugin_id']} -> {following['plugin_id']}")
    _check(checks, "name", previous["name"] == following["name"],
           f"{previous['name']!r} -> {following['name']!r}")
    _check(checks, "displayName",
           previous["display_name"] == following["display_name"],
           f"{previous['display_name']!r} -> {following['display_name']!r}")
    _check(checks, "apps", previous["apps"] == following["apps"],
           f"{previous['apps']!r} -> {following['apps']!r}")
    _check(checks, "app_binding_bytes",
           previous["app_sha256"] == following["app_sha256"],
           "the file the apps reference names is unchanged" if
           previous["app_sha256"] == following["app_sha256"] else
           f"{previous['apps']} content changed; the App binding is not ours to move")

    # App-backed updates may change version, Skills/supporting files, keywords,
    # onboarding selection and the local-executor requirement. Everything else
    # in the effective manifest is protected: title, descriptions, prompts,
    # presentation, App/connection fields and any unknown future field.
    prev_root = previous["root_manifest"]
    next_root = following["root_manifest"]
    prev_compat = previous["compat_manifest"]
    next_compat = following["compat_manifest"]
    _check(checks, "manifest_root_protected",
           _canonical_json(_drop_paths(prev_root, ROOT_ALLOWED_PATHS))
           == _canonical_json(_drop_paths(next_root, ROOT_ALLOWED_PATHS)),
           "root manifest protected configuration changed")
    _check(checks, "compat_manifest_presence",
           (prev_compat is None) == (next_compat is None),
           "compat manifest presence changed")
    for label, root, compat in (("previous", prev_root, prev_compat),
                                ("next", next_root, next_compat)):
        if compat is not None:
            _check(checks, f"compat_version_{label}",
                   compat.get("version") == root.get("version"),
                   f"{label} compat manifest version does not match the root manifest")
    if prev_compat is not None and next_compat is not None:
        _check(checks, "compat_manifest_protected",
               _canonical_json(_drop_paths(prev_compat, COMPAT_ALLOWED_PATHS))
               == _canonical_json(_drop_paths(next_compat, COMPAT_ALLOWED_PATHS)),
               "compat manifest protected configuration changed")

    # MCP is App-owned. Compare the effective structures and every local file
    # they reference; a server, URL, transport or referenced script change is a
    # server deployment, not an overlay update.
    for label in ("mcp", "legacy_mcp"):
        left = previous[label]
        right = following[label]
        _check(checks, f"{label}_presence", (left is None) == (right is None),
               f"{label} presence changed")
        if left is not None and right is not None:
            _check(checks, f"{label}_configuration",
                   _canonical_json(left) == _canonical_json(right),
                   f"{label} configuration changed")
            left_refs = _mcp_references(left, previous["member_sha256"])
            right_refs = _mcp_references(right, following["member_sha256"])
            _check(checks, f"{label}_references", left_refs == right_refs,
                   f"{label} referenced-file set changed")
            drifted = sorted(path for path in left_refs
                             if right_refs.get(path) != left_refs[path])
            _check(checks, f"{label}_referenced_bytes", not drifted,
                   f"{label} referenced file(s) changed: {drifted}")

    # A non-skill member is not an editable supporting file in this workflow:
    # adding, removing or changing one is drift. Skills and their supporting
    # files are the only member changes the update path permits.
    def protected_members(view: dict) -> set[str]:
        return {name for name in view["members"] if not name.startswith("skills/")}

    prev_members = protected_members(previous)
    next_members = protected_members(following)
    _check(checks, "member_set", prev_members == next_members,
           "non-skill member set changed: "
           f"added {sorted(next_members - prev_members)} "
           f"removed {sorted(prev_members - next_members)}")
    manifest_members = {"plugin.json", ".codex-plugin/plugin.json",
                        "mcp.json", ".mcp.json"}
    changed_members = sorted(
        name for name in prev_members & next_members
        if name not in manifest_members
        and previous["member_sha256"][name] != following["member_sha256"][name]
    )
    _check(checks, "member_bytes", not changed_members,
           f"protected non-skill member(s) changed: {changed_members}")

    from_version = _semver(previous["version"], "previous version")
    to_version = _semver(following["version"], "next version")
    _check(checks, "version", to_version > from_version,
           f"{previous['version']} -> {following['version']} must advance")

    removed = sorted(set(previous["skills"]) - set(following["skills"]))
    _check(checks, "skills_removed", not removed,
           f"the next release removes previous skill(s): {removed}" if removed
           else "all previous skills are still present")
    added = sorted(set(following["skills"]) - set(previous["skills"]))
    changed = sorted(
        name for name in set(previous["skills"]) & set(following["skills"])
        if previous["skill_sha256"][name] != following["skill_sha256"][name]
    )

    return {
        "schema": PLAN_SCHEMA,
        "plugin_id": previous["plugin_id"],
        "name": previous["name"],
        "display_name": previous["display_name"],
        "apps": previous["apps"],
        "from_version": previous["version"],
        "to_version": following["version"],
        "from_archive_sha256": previous["archive_sha256"],
        "to_archive_sha256": following["archive_sha256"],
        "previous_release_id": previous_record.get("release_id"),
        "skills": following["skills"],
        "skills_added": added,
        "skills_changed": changed,
        "skills_removed": removed,
        "icons": following["icon_sha256"],
        "protected": {
            "manifest_root": "identical except allowed version/keywords/onboarding/local-executor",
            "manifest_compat": (
                "absent" if prev_compat is None else
                "identical except allowed version/keywords/onboarding/local-executor"),
            "mcp": "absent" if previous["mcp"] is None and following["mcp"] is None
                   else "structurally identical with identical referenced files",
            "members": sorted(prev_members),
        },
        "note": ("Only version, Skills/supporting files, keywords, onboarding "
                 "selection and the local-executor requirement are overlay "
                 "updates. MCP/backend/tool behavior is server deployment."),
        "expected": {
            "plugin_id": previous["plugin_id"],
            "version": following["version"],
            "archive_sha256": following["archive_sha256"],
            "skills": following["skills"],
            "icons": following["icon_sha256"],
        },
        "verdict": "PASS",
        "checks": checks,
        "writes": [],
    }


def verify_readback(plan: dict, observed: dict) -> dict:
    """Compare a readback claim with the local plan; never call it production."""
    if plan.get("schema") != PLAN_SCHEMA:
        raise Refused(f"plan schema is {plan.get('schema')!r}, not {PLAN_SCHEMA!r}")
    if observed.get("schema") != READBACK_SCHEMA:
        raise Refused(f"readback schema is {observed.get('schema')!r}, not {READBACK_SCHEMA!r}")
    source = observed.get("source")
    if source == "LIVE":
        raise Refused("source LIVE: this offline checker did not observe a live "
                      "Creator and cannot authenticate that claim. Use "
                      "OPERATOR_SUPPLIED_RECEIPT with the operator's provenance.")
    if source not in READBACK_SOURCES:
        raise Refused(f"readback source is {source!r}, not one of {READBACK_SOURCES}")

    checks: list[dict] = []
    _check(checks, "plugin_id", observed.get("plugin_id") == plan["plugin_id"],
           f"{observed.get('plugin_id')!r} vs {plan['plugin_id']!r}")
    previous_release = observed.get("previous_release_id")
    release = observed.get("release_id")
    _check(checks, "previous_release_id",
           isinstance(previous_release, str) and bool(previous_release.strip()),
           "the receipt names no previous release id")
    _check(checks, "release_id",
           isinstance(release, str) and bool(release.strip()) and release != previous_release,
           "the release id is missing or reuses the previous release id")
    if plan.get("previous_release_id"):
        _check(checks, "previous_release_matches_plan",
               previous_release == plan["previous_release_id"],
               f"{previous_release!r} vs {plan['previous_release_id']!r}")
    _check(checks, "version", observed.get("version") == plan["to_version"],
           f"{observed.get('version')!r} vs {plan['to_version']!r}")
    _check(checks, "archive_sha256",
           observed.get("archive_sha256") == plan["to_archive_sha256"],
           f"{observed.get('archive_sha256')!r} vs {plan['to_archive_sha256']!r}")

    skills = observed.get("skills")
    if (not isinstance(skills, list) or any(not isinstance(s, str) for s in skills)
            or len(skills) != len(set(skills))):
        raise Refused("skills: the receipt carries no unique skill-name list")
    _check(checks, "skills", sorted(skills) == sorted(plan["skills"]),
           f"observed {sorted(skills)} vs planned {sorted(plan['skills'])}")

    icons_status = "NOT_READ"
    observed_icons = observed.get("icons")
    if observed_icons:
        if not isinstance(observed_icons, dict):
            raise Refused("icons: the receipt's icon readback is not an object")
        expected_icons = plan.get("icons") or {}
        if set(observed_icons) != set(expected_icons):
            raise Refused(f"icons: observed {sorted(observed_icons)} vs expected "
                          f"{sorted(expected_icons)}")
        bad = [slot for slot, digest in observed_icons.items()
               if expected_icons.get(slot) != digest]
        _check(checks, "icons", not bad,
               f"icon hash mismatch for {bad}" if bad else "icon hashes match")
        icons_status = "READ"
    else:
        checks.append({"check": "icons", "status": "NOT_READ",
                       "detail": "the receipt did not return presentation assets"})

    return {
        "schema": READBACK_RESULT_SCHEMA,
        "verdict": "PASS" if icons_status == "READ" else "PARTIAL",
        "source": source,
        "production_verified": False,
        "icons_status": icons_status,
        "checks": checks,
        "plugin_id": plan["plugin_id"],
        "version": plan["to_version"],
        "release_id": release,
        "writes": [],
        "uploads": [],
        "installs": [],
        "note": ("A local/operator readback is not a Creator observation. "
                 "production_verified is false until a live readback is separately "
                 "authorised and authenticated."),
    }


def check_rollback(*, previous_record: dict, previous_archive: Path,
                   current_record: dict | None = None) -> dict:
    """Prove the previous archive is a valid target; never execute a rollback."""
    previous = archive_view(previous_record, previous_archive)
    checks: list[dict] = []
    if current_record is not None:
        _check(checks, "plugin_id", previous["plugin_id"] == current_record.get("plugin_id"),
               f"{previous['plugin_id']} vs {current_record.get('plugin_id')}")
        _check(checks, "version_order",
               _semver(previous["version"], "previous version")
               < _semver(current_record.get("version"), "current version"),
               f"rollback target {previous['version']} is not below "
               f"{current_record.get('version')}")
    return {
        "schema": ROLLBACK_SCHEMA,
        "verdict": "PASS",
        "rollback_ready": True,
        "plugin_id": previous["plugin_id"],
        "restores_version": previous["version"],
        "archive_sha256": previous["archive_sha256"],
        "checks": checks,
        "writes": [],
        "uploads": [],
        "installs": [],
        "note": "This is a rollback target and a check, not a rollback action.",
    }


# -- local rehearsal ------------------------------------------------------

def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "fleet_overlay_builder_for_rehearsal", HERE / "build_overlay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _abs_path(path: Path) -> Path:
    """Absolute lexical path; do not follow a symlink before it is checked."""
    return Path(os.path.abspath(os.fspath(path)))


def _is_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([os.fspath(child), os.fspath(parent)]) == os.fspath(parent)
    except ValueError:
        return False


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path)
    for candidate in (current, *current.parents):
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise Refused(f"{label} path has a symlink component: {candidate}")


def _preflight_tree(root: Path, label: str, builder) -> None:
    """Refuse symlinks, forbidden members and non-regular files before any copy."""
    root = _abs_path(root)
    if os.path.islink(root):
        raise Refused(f"{label} root is a symlink: {root}")
    if not os.path.isdir(root):
        raise Refused(f"{label} is not a directory: {root}")
    forbidden_parts = set(builder.FORBIDDEN_PARTS)
    forbidden_suffixes = tuple(builder.FORBIDDEN_SUFFIXES)
    forbidden_names = set(builder.FORBIDDEN_NAMES)
    for base, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs.sort()
        files.sort()
        for name in list(dirs):
            path = Path(base) / name
            rel = path.relative_to(root)
            if os.path.islink(path):
                raise Refused(f"{label} contains a symlink: {rel.as_posix()}")
            if set(rel.parts) & forbidden_parts:
                raise Refused(f"{label} contains a forbidden member: {rel.as_posix()}")
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(root)
            if os.path.islink(path):
                raise Refused(f"{label} contains a symlink: {rel.as_posix()}")
            if (set(rel.parts) & forbidden_parts
                    or path.suffix.lower() in forbidden_suffixes
                    or path.name in forbidden_names):
                raise Refused(f"{label} contains a forbidden member: {rel.as_posix()}")
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode):
                raise Refused(f"{label} contains a non-regular file: {rel.as_posix()}")


def _preflight_output(out_dir: Path, source_root: Path) -> Path:
    out_dir = _abs_path(out_dir)
    source_root = _abs_path(source_root)
    _reject_symlink_components(out_dir, "output")
    if (out_dir == source_root or _is_within(out_dir, source_root)
            or _is_within(source_root, out_dir)):
        raise Refused("source-output overlap: the output must be outside the source tree")
    if os.path.lexists(out_dir):
        info = os.lstat(out_dir)
        if not stat.S_ISDIR(info.st_mode):
            raise Refused("output is not a directory")
        if info.st_uid != os.geteuid():
            raise Refused("output is not owned by the current user")
        if stat.S_IMODE(info.st_mode) & 0o002:
            raise Refused("output is world-writable")
        if any(Path(out_dir).iterdir()):
            raise Refused("output must be a fresh empty directory")
    return out_dir


def _make_fresh_dir(path: Path) -> None:
    if os.path.lexists(path):
        raise Refused(f"output component already exists: {path.name}")
    os.mkdir(path, 0o700)
    if os.path.islink(path) or not os.path.isdir(path):
        raise Refused(f"output component is not a real directory: {path.name}")


def _stage(source_root: Path, dest: Path, version: str, skills) -> None:
    if os.path.lexists(dest):
        raise Refused(f"stage destination already exists: {dest.name}")
    if os.path.islink(dest.parent) or not os.path.isdir(dest.parent):
        raise Refused(f"stage parent is not a real directory: {dest.parent.name}")
    shutil.copytree(source_root, dest, symlinks=False,
                    ignore=shutil.ignore_patterns("skills"))
    skills_dir = dest / "skills"
    if os.path.lexists(skills_dir):
        raise Refused("the source skills directory was not ignored")
    os.mkdir(skills_dir, 0o700)
    for name in skills:
        source = SHARED_SKILLS / name
        if os.path.islink(source) or not os.path.isdir(source):
            raise Refused(f"shared skill is not a real directory: {name}")
        shutil.copytree(source, skills_dir / name, symlinks=False)
    for rel in ("plugin.json", ".codex-plugin/plugin.json"):
        path = dest / rel
        data = json.loads(path.read_text(encoding="utf-8"))
        data["version"] = version
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
def rehearse(source_root: Path, baseline_path: Path, out_dir: Path) -> dict:
    """Build v1 and v2 locally, then run plan/readback/rollback checks.

    Every source, shared-skill and output boundary is checked before the first
    mkdir/copytree. No caller-owned directory is removed.
    """
    source_root = _abs_path(source_root)
    out_dir = _abs_path(out_dir)
    baseline_path = _abs_path(baseline_path)
    if os.path.islink(baseline_path):
        raise Refused("baseline path is a symlink")
    baseline = _read_json(baseline_path, "baseline")
    builder = _load_builder()
    _preflight_tree(source_root, "source", builder)
    _preflight_tree(SHARED_SKILLS, "shared skills", builder)
    out_dir = _preflight_output(out_dir, source_root)
    if not os.path.exists(out_dir):
        os.makedirs(out_dir, mode=0o700, exist_ok=False)
    if os.path.islink(out_dir) or not os.path.isdir(out_dir):
        raise Refused("output is not a real directory")
    _make_fresh_dir(out_dir / "v1")
    _make_fresh_dir(out_dir / "v2")

    v1_src = out_dir / "v1" / "src"
    v2_src = out_dir / "v2" / "src"
    _stage(source_root, v1_src, "0.1.1", OLD_SKILLS)
    _stage(source_root, v2_src, "0.1.2", OLD_SKILLS + NEW_SKILLS)
    v1_dist = out_dir / "v1" / "dist"
    v2_dist = out_dir / "v2" / "dist"
    v1_record = builder.build(v1_src, v1_dist, baseline)
    v2_record = builder.build(v2_src, v2_dist, baseline)
    v1_archive = v1_dist / v1_record["archive"]
    v2_archive = v2_dist / v2_record["archive"]

    plan = plan_update(previous_record=v1_record, previous_archive=v1_archive,
                       next_record=v2_record, next_archive=v2_archive)
    view = archive_view(v2_record, v2_archive)
    observed = {
        "schema": READBACK_SCHEMA,
        "source": SOURCE_SYNTHETIC,
        "plugin_id": v2_record["plugin_id"],
        "previous_release_id": "PluginRelease_SYNTHETIC_V1",
        "release_id": "PluginRelease_SYNTHETIC_V2",
        "version": v2_record["version"],
        "skills": view["skills"],
        "archive_sha256": v2_record["archive_sha256"],
        "icons": view["icon_sha256"],
    }
    readback = verify_readback(plan, observed)
    rollback = check_rollback(previous_record=v1_record, previous_archive=v1_archive,
                              current_record=v2_record)
    result = {
        "schema": REHEARSAL_SCHEMA,
        "distribution": "SYNTHETIC_LOCAL_REHEARSAL_ONLY",
        "production_verified": False,
        "plan": plan,
        "observed_receipt": observed,
        "readback": readback,
        "rollback": rollback,
        "v1_record": v1_record,
        "v2_record": v2_record,
        "archives": [str(v1_archive.relative_to(out_dir)),
                     str(v2_archive.relative_to(out_dir))],
        "writes": [],
        "uploads": [],
        "installs": [],
        "note": ("Synthetic local rehearsal. No Creator call, upload, install, "
                 "OAuth change, service change or route change was performed."),
    }
    _write_json(out_dir / "rehearsal.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("plan")
    p.add_argument("--previous", required=True)
    p.add_argument("--next", required=True)
    p.add_argument("--previous-archive")
    p.add_argument("--next-archive")
    p.add_argument("--out")

    r = sub.add_parser("readback")
    r.add_argument("--plan", required=True)
    r.add_argument("--observed", required=True)
    r.add_argument("--out")

    b = sub.add_parser("rollback")
    b.add_argument("--previous", required=True)
    b.add_argument("--previous-archive")
    b.add_argument("--current")
    b.add_argument("--out")

    h = sub.add_parser("rehearse")
    h.add_argument("--source", required=True)
    h.add_argument("--baseline", default=str(HERE / "baseline.json"))
    h.add_argument("--out", required=True)

    args = parser.parse_args(argv)
    try:
        if args.action == "plan":
            previous_path = Path(args.previous)
            next_path = Path(args.next)
            previous = _load_record(previous_path)
            following = _load_record(next_path)
            result = plan_update(
                previous_record=previous,
                previous_archive=(Path(args.previous_archive) if args.previous_archive
                                  else previous_path.parent / previous["archive"]),
                next_record=following,
                next_archive=(Path(args.next_archive) if args.next_archive
                              else next_path.parent / following["archive"]),
            )
        elif args.action == "readback":
            result = verify_readback(_read_json(Path(args.plan), "plan"),
                                     _read_json(Path(args.observed), "readback"))
        elif args.action == "rollback":
            previous_path = Path(args.previous)
            previous = _load_record(previous_path)
            current = _load_record(Path(args.current)) if args.current else None
            result = check_rollback(
                previous_record=previous,
                previous_archive=(Path(args.previous_archive) if args.previous_archive
                                  else previous_path.parent / previous["archive"]),
                current_record=current,
            )
        else:
            result = rehearse(Path(args.source), Path(args.baseline), Path(args.out))
    except Refused as refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 2

    # `rehearse --out` is a directory and writes its own `rehearsal.json`;
    # the other verbs take `--out` as the result file.
    if args.action != "rehearse" and getattr(args, "out", None):
        _write_json(Path(args.out), result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
