#!/usr/bin/env python3
"""Offline, fail-closed evaluation of a Dell `vince` -> `fjd` runtime cutover.

The architecture decision is that `fjd` carries the formal ServerOS / Core /
Fleet / CloudCLI runtime on this machine and `vince` leaves the Next managed
matrix. This module is the *plan's* gate, not its executor.

It is pure and offline. It never reads a home directory, never resolves a
symlink outside the manifest, never opens a socket and never touches an
account. Everything it knows is what the manifest declares, and most of what it
does is refuse.

Three refusals carry the weight, and each exists because the plausible-looking
alternative is a real accident:

  * **A credential never migrates.** `.ssh`, `.config/gh`, `.claude`, `.codex`,
    `.cloudcli`, an OAuth store, a Tailscale state dir, a node secret, a live
    session database — these are `D`, they are refused even when a manifest
    labels them something else, and the refusal is by CLASS rather than by the
    declarer's word. A copy is refused too: **the source worktree stays where it
    is** and the target checks the code out from its own credentials.

  * **A whole HOME is never the unit.** `mv ~`, a bulk `rsync` of a home, or any
    asset whose path is at or above either home root is refused outright. The
    unit of migration is a reviewed asset, not a directory tree that happens to
    contain one.

  * **A legacy address keeps its meaning.** `device:llm-lab-dell|claude|<native>`
    was minted for `vince`. Changing which account carries the device does not
    and must not change what that address denotes, so a manifest that remaps the
    bare `claude` tag onto `fjd` is refused. The new account arrives under its
    own tag; a `preferred_runtime` may point at it as a UX default, and only when
    the manifest says in the same breath that the preference grants nothing.

`FJD_OWNER_AUTH_PENDING` is not an error state. It is the honest one: the
requester has said they want the migration, and nothing here establishes that
they can act as `fjd`'s OS owner. Until the manifest carries that confirmation,
no asset is executable and the decision says so.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import sys

SCHEMA = "serveros/fleet-v1-dell-fjd-cutover/v1"

#: The six classes. `A`, `B` and `C` are the only migratable ones, and each is
#: migratable in its own way — see the docstring: `B` must first become a
#: reviewable commit or patch authored under the source account.
CLASS_SOURCE = "A"        # pushed git source: clone/fetch/checkout into fjd's workspace
CLASS_WIP = "B"           # unpushed local WIP: commit or patch first, then fjd imports
CLASS_TEMPLATE = "C"      # credential-free templates / project records, owner-approved
CLASS_CREDENTIAL = "D"    # NEVER migrated
CLASS_IN_PLACE = "E"      # Strata / GPU / other accounts: untouched, in place
CLASS_LEGACY = "F"        # legacy vince connector/sessions: isolated, retired by owner later

CLASSES = (CLASS_SOURCE, CLASS_WIP, CLASS_TEMPLATE, CLASS_CREDENTIAL,
           CLASS_IN_PLACE, CLASS_LEGACY)
MIGRATABLE = frozenset({CLASS_SOURCE, CLASS_WIP, CLASS_TEMPLATE})

#: Path components that make an asset `D` no matter what the manifest says.
#: Matched on the whole component, so `my.ssh-notes` is not caught by `.ssh`.
CREDENTIAL_COMPONENTS = frozenset({
    ".ssh", ".config/gh", ".claude", ".codex", ".cloudcli", ".cursor",
    ".npm", ".nv", ".gitconfig", "gh", "oauth", "oauth-store",
    "tailscale", "tailscaled", "node-secret", "mcp-pat-claude-code.txt",
    "session.db", "sessions.sqlite", "sessions.sqlite3", "known_hosts",
})
#: And these, anywhere in the path, because a credentials *file* is not a
#: directory component.
CREDENTIAL_SUFFIXES = (
    ".pem", ".key", "id_ed25519", "id_rsa", "id_ecdsa", "hosts.yml",
    ".credentials.json", "credentials.json", "token", "token.txt", ".pat",
)

TOP_FIELDS = frozenset({"schema", "device_ref", "source", "target",
                        "owner_authorization", "assets", "session_refs",
                        "runtime_requirements"})
USER_FIELDS = frozenset({"os_user", "uid", "home"})
ASSET_FIELDS = frozenset({"id", "class", "path", "note"})
AUTH_FIELDS = frozenset({"fjd_owner_confirmed", "confirmed_by", "evidence"})
REF_FIELDS = frozenset({"legacy_base_tag", "legacy_base_owner",
                        "next_runtime_tags", "remap_legacy_tag"})
REQ_FIELDS = frozenset({"required_runtimes", "preferred_runtime",
                        "preferred_runtime_is_ux_only",
                        "multi_account_capable", "device_default_account"})

DEVICE_REF = "device:llm-lab-dell"
SOURCE_USER = "vince"
TARGET_USER = "fjd"
#: The tag the device's own account keeps. It is a SPELLING BASELINE: it is what
#: every existing ref says, not a claim about who serves the device today.
LEGACY_BASE_TAG = "claude"

DECISION_READY = "PLAN_READY"
DECISION_PENDING = "FJD_OWNER_AUTH_PENDING"
DECISION_BLOCKED = "BLOCKED"


def _norm(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    clean = posixpath.normpath(value)
    return clean if clean == value and clean != "/" else None


def _is_credential(path: str) -> str | None:
    """The first reason this path holds a credential, or None."""
    parts = [p for p in path.split("/") if p]
    for index, part in enumerate(parts):
        # A dotted directory anywhere, and the two-segment `config/gh` pair.
        joined = "/".join(parts[index:index + 2])
        if part in CREDENTIAL_COMPONENTS or joined in CREDENTIAL_COMPONENTS:
            return part if part in CREDENTIAL_COMPONENTS else joined
        for suffix in CREDENTIAL_SUFFIXES:
            if part == suffix or part.endswith(suffix):
                return suffix
    return None


def _is_home_or_above(path: str, root: str) -> bool:
    """True for the home ITSELF and for any ancestor of it (`/home`, `/`).

    Deliberately NOT true for a path *below* the home: a reviewed asset inside
    a home is the whole point of the exercise, and a rule that refused those
    would refuse every real migration while looking strict.
    """
    return path == root or root.startswith(path + "/")


def _shape(value: object, allowed: frozenset, label: str, problems: list) -> dict:
    if not isinstance(value, dict):
        problems.append(f"{label}: expected object")
        return {}
    unknown = set(value) - allowed
    if unknown:
        problems.append(f"{label}: unsupported field(s) {', '.join(sorted(unknown))}")
    return value


def evaluate(manifest: object, *, passwd_home: dict | None = None) -> dict:
    """Decide whether a cutover manifest is safe, and what it may not do.

    `passwd_home` is an injected `{user: (uid, home)}` map. It exists so the
    caller can supply what `getent`/`id` reported on the real host, and so the
    tests need no account. Absent, the manifest's own target is checked for
    internal consistency only, and that is reported as such.
    """
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return {"schema": SCHEMA, "decision": DECISION_BLOCKED,
                "blockers": ["manifest: expected object"], "writes": [],
                "executes": [], "assets": []}

    root = _shape(manifest, TOP_FIELDS, "manifest", problems)
    if root.get("schema") != SCHEMA:
        problems.append("schema: unsupported or missing")
    if root.get("device_ref") != DEVICE_REF:
        problems.append(f"device_ref: must be {DEVICE_REF} — one physical machine "
                        f"is one authenticated device, and the account rides in the tag")

    source = _shape(root.get("source"), USER_FIELDS, "source", problems)
    target = _shape(root.get("target"), USER_FIELDS, "target", problems)

    if source.get("os_user") != SOURCE_USER:
        problems.append(f"source.os_user: must be {SOURCE_USER!r}")
    if target.get("os_user") != TARGET_USER:
        problems.append(f"target.os_user: must be {TARGET_USER!r}")
    src_home, tgt_home = _norm(source.get("home")), _norm(target.get("home"))
    for label, value in (("source.home", src_home), ("target.home", tgt_home)):
        if value is None:
            problems.append(f"{label}: absolute normalized path required")

    if passwd_home is not None:
        for label, declared in (("source", source), ("target", target)):
            user = declared.get("os_user")
            held = passwd_home.get(user)
            if held is None:
                problems.append(f"{label}.os_user: {user!r} has no passwd entry "
                                f"on this host")
                continue
            uid, home = held
            if declared.get("uid") != uid:
                problems.append(f"{label}.uid: passwd says {uid}, manifest says "
                                f"{declared.get('uid')!r}")
            if _norm(declared.get("home")) != home:
                problems.append(f"{label}.home: passwd says {home}, manifest says "
                                f"{declared.get('home')!r}")

    # -- assets ------------------------------------------------------------
    assets = root.get("assets")
    if not isinstance(assets, list) or not assets:
        problems.append("assets: a nonempty list of reviewed assets is required")
        assets = []

    accepted: list[dict] = []
    for position, raw in enumerate(assets):
        label = f"assets[{position}]"
        item = _shape(raw, ASSET_FIELDS, label, problems)
        asset_id = item.get("id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            problems.append(f"{label}.id: required")
        path = _norm(item.get("path"))
        declared = item.get("class")
        if declared not in CLASSES:
            problems.append(f"{label}.class: must be one of {'/'.join(CLASSES)}")
            continue
        if path is None:
            problems.append(f"{label}.path: absolute normalized path required")
            continue

        # A whole home — or anything above one — is never the unit.
        for name, home in (("source.home", src_home), ("target.home", tgt_home)):
            if home and _is_home_or_above(path, home):
                problems.append(
                    f"{label}.path: {path} is at or above {name} ({home}); the "
                    f"unit of migration is a reviewed asset, never a home tree"
                )
        # A credential is refused by what it IS, not by what it is called.
        reason = _is_credential(path)
        if reason is not None:
            if declared != CLASS_CREDENTIAL:
                problems.append(
                    f"{label}: {path} is a credential path ({reason!r}) declared "
                    f"as class {declared}; it is class {CLASS_CREDENTIAL} and the "
                    f"target account initialises its own"
                )
            accepted.append({"id": asset_id, "class": CLASS_CREDENTIAL,
                             "path": path, "migratable": False,
                             "why": "credential — fjd logs in itself"})
            continue
        if declared == CLASS_CREDENTIAL:
            accepted.append({"id": asset_id, "class": CLASS_CREDENTIAL,
                             "path": path, "migratable": False,
                             "why": "declared credential"})
            continue
        if declared == CLASS_IN_PLACE:
            accepted.append({"id": asset_id, "class": CLASS_IN_PLACE, "path": path,
                             "migratable": False,
                             "why": "in place — Strata/GPU/other accounts are "
                                    "never moved or restarted"})
            continue
        if declared == CLASS_LEGACY:
            accepted.append({"id": asset_id, "class": CLASS_LEGACY, "path": path,
                             "migratable": False,
                             "why": "legacy vince connector/sessions — isolated "
                                    "until the owner retires them"})
            continue
        if declared == CLASS_WIP:
            accepted.append({"id": asset_id, "class": CLASS_WIP, "path": path,
                             "migratable": False,
                             "why": "unpushed WIP — vince commits or writes a "
                                    "credential-free patch first, then fjd imports"})
            continue
        accepted.append({"id": asset_id, "class": declared, "path": path,
                         "migratable": True, "why": "reviewed"})

    # -- addresses ---------------------------------------------------------
    refs = _shape(root.get("session_refs"), REF_FIELDS, "session_refs", problems)
    base_tag = refs.get("legacy_base_tag")
    if base_tag != LEGACY_BASE_TAG:
        problems.append(
            f"session_refs.legacy_base_tag: must stay {LEGACY_BASE_TAG!r} — it is "
            f"the spelling every existing ref already uses"
        )
    if refs.get("legacy_base_owner") != SOURCE_USER:
        problems.append(
            f"session_refs.legacy_base_owner: must be {SOURCE_USER!r}; the bare "
            f"{LEGACY_BASE_TAG!r} tag was minted for that account and keeps "
            f"denoting it"
        )
    if refs.get("remap_legacy_tag") is not None:
        problems.append(
            "session_refs.remap_legacy_tag: refused. Re-pointing the legacy "
            f"{LEGACY_BASE_TAG!r} tag at another account silently changes what "
            f"every historical ref denotes; a device changing which account it "
            f"runs does not change what an address meant"
        )
    tags = refs.get("next_runtime_tags")
    if not isinstance(tags, list) or not tags:
        problems.append("session_refs.next_runtime_tags: a nonempty list is required")
        tags = []
    for tag in tags:
        if not isinstance(tag, str) or not tag.startswith(f"{LEGACY_BASE_TAG}-"):
            problems.append(
                f"session_refs.next_runtime_tags: {tag!r} must be a suffixed tag "
                f"({LEGACY_BASE_TAG}-<account>); the bare tag is taken"
            )
        elif tag.endswith(f"-{SOURCE_USER}"):
            problems.append(
                f"session_refs.next_runtime_tags: {tag!r} names the account being "
                f"retired from the matrix"
            )

    # -- runtime requirements ---------------------------------------------
    req = _shape(root.get("runtime_requirements"), REQ_FIELDS,
                 "runtime_requirements", problems)
    required = req.get("required_runtimes")
    if not isinstance(required, list) or not required:
        problems.append("runtime_requirements.required_runtimes: nonempty list required")
        required = []
    if LEGACY_BASE_TAG in required:
        problems.append(
            f"runtime_requirements.required_runtimes: the bare {LEGACY_BASE_TAG!r} "
            f"runtime is the account leaving the matrix; naming it re-requires it"
        )
    for tag in required:
        if tag not in tags:
            problems.append(
                f"runtime_requirements.required_runtimes: {tag!r} is not among the "
                f"declared next_runtime_tags"
            )
    if req.get("multi_account_capable") is not True:
        problems.append(
            "runtime_requirements.multi_account_capable: must be true. Removing "
            "one account from the matrix must not remove the capability, which is "
            "generic code supporting 0..N runtimes per device"
        )
    preferred = req.get("preferred_runtime")
    if preferred is not None:
        if preferred not in tags:
            problems.append(
                f"runtime_requirements.preferred_runtime: {preferred!r} is not a "
                f"declared runtime tag"
            )
        if req.get("preferred_runtime_is_ux_only") is not True:
            problems.append(
                "runtime_requirements.preferred_runtime: declared without "
                "preferred_runtime_is_ux_only. A preference is a UX default; "
                "without that statement it reads as an alias or a grant"
            )
    if req.get("device_default_account") is not None:
        problems.append(
            "runtime_requirements.device_default_account: refused. The legacy tag "
            "is a spelling baseline, not a pointer to be repointed when the "
            "production account changes; model this as legacy_base_tag_owner plus "
            "preferred_runtime instead"
        )

    # -- owner authorization ----------------------------------------------
    auth = _shape(root.get("owner_authorization"), AUTH_FIELDS,
                  "owner_authorization", problems)
    owner_confirmed = auth.get("fjd_owner_confirmed") is True
    if not owner_confirmed:
        if auth.get("confirmed_by") is not None:
            problems.append(
                "owner_authorization.confirmed_by: named without "
                "fjd_owner_confirmed=true"
            )
    elif not isinstance(auth.get("evidence"), str) or not auth.get("evidence", "").strip():
        problems.append(
            "owner_authorization.evidence: a confirmation must carry what it "
            "rests on; 'the requester asked for it' is not the account owner "
            "saying they can act as fjd"
        )

    migratable = [a for a in accepted if a["migratable"]]
    if problems:
        decision = DECISION_BLOCKED
    elif not owner_confirmed:
        decision = DECISION_PENDING
    else:
        decision = DECISION_READY

    return {
        "schema": SCHEMA,
        "decision": decision,
        "blockers": sorted(set(problems)),
        "assets": accepted,
        "executable_now": [] if decision != DECISION_READY else [
            a["id"] for a in migratable if a["class"] == CLASS_SOURCE],
        "counts": {c: sum(1 for a in accepted if a["class"] == c) for c in CLASSES},
        "never_migrated": sorted(a["id"] for a in accepted
                                 if a["class"] == CLASS_CREDENTIAL),
        # This module has nothing to declare and says so, so a caller can assert
        # that evaluating a plan is not doing anything.
        "writes": [],
        "executes": [],
        "note": ("Planning only. The real change is made by fjd under its own "
                 "login, or by an administrator its owner authorises in a change "
                 "window — never by this tooling and never by the source account."),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--manifest", required=True,
                   help="non-secret JSON cutover manifest (no credential bytes)")
    args = p.parse_args(argv)
    try:
        with open(args.manifest, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"decision": DECISION_BLOCKED,
                          "blockers": [type(exc).__name__],
                          "writes": [], "executes": []}, sort_keys=True))
        return 2
    result = evaluate(manifest)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == DECISION_READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
