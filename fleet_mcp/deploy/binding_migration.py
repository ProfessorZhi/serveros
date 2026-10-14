#!/usr/bin/env python3
"""Preflight a wildcard Fleet binding for the named-grant migration. READ-ONLY.

Why this exists
---------------
This branch refuses `policy.send_allow: ['*']` at load. The live Tokyo binding
carries exactly that, so the branch's service does not start against it, and the
refusal message already says what replaces it: a named table,

    policy.operator_grants: {<principal>: {<device_ref>: [send]}}

What the refusal CANNOT say is *which principals*, and no tool can work that
out. A wildcard is the absence of a principal list; the sessions it matched
carry no identity, and the credentials that used it are not recorded in the
binding. `oauth:<client_id>` comes from the authorization server's client
registry and `owner-pat` is the operator's own credential — neither is derivable
from the file being migrated.

So the honest deliverable is not a migration. It is a **worklist**: it reads the
real binding, derives the one thing that IS derivable (which devices, which
verbs, which of them are wildcarded), and states per device and verb what a
human still has to decide. Where it cannot establish something it says
NEEDS_HUMAN or DEFER rather than filling the gap with a plausible guess. It never
names a principal, never emits `*`, and never turns a wildcard into a default
allow.

Three refusals this module is built around
------------------------------------------
1. **It does not infer principals.** There is no code path from a wildcard to a
   principal name. The template it prints carries `<PRINCIPAL>` where a name
   belongs, and a test asserts that a binding whose only grant is `['*']`
   produces no principal and a NEEDS_HUMAN verdict.
2. **It does not grant anything.** Every verb it reports is reported as
   *required of a human*, never as granted. `create` and `control` are reported
   only where the live binding already set their device flag, because widening
   the migration beyond what the operator already asked for is itself an
   escalation.
3. **It does not write, and it does not restart.** The binding is opened for
   reading and nothing else; there is no write path, no `subprocess`, no
   `systemctl`, and no import of the installer. `writes` is `[]` in every
   report, and a test compares the file's bytes AND its mtime before and after a
   run, the way the runtime rollout plan proves the same claim.

What the report contains, and what it never reads
-------------------------------------------------
The report is built from an ALLOWLIST of non-sensitive fields — device refs,
boolean flags, the SHAPE of the old session lists, and registry ownership. It
never reads a `credential_anchor`, a `secret_anchor`, a `pat_anchor` or an
`endpoint` into the document at all. That is stricter than redacting after the
fact: a value that is never read cannot leak through a redaction miss. A test
puts a canary secret in a binding and asserts it appears nowhere in the output.

Verdicts
--------
`ALREADY_MIGRATED` the binding already carries a named grant covering the verbs
                   its device flags admit; there is nothing for a human to add
`NEEDS_HUMAN`      a principal must be named; the tool will not name it
`DEFER`            eligibility could not be established (registry unreadable or
                   ownership is not `company`), or no device flag is set at all
                   so there is nothing to migrate — and DEFER is not a grant

Per-row statuses additionally distinguish `ALREADY_GRANTED` (a named grant an
operator already wrote covers this device and verb) from `NOT_WANTED` (the
device flag is not set, so this verb was never asked for).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fleet_mcp.errors import FleetRefusal, refuse  # noqa: E402

SCHEMA = "serveros/fleet-binding-migration.v1"

#: Hard-coded, like the rollout plan's mode: a preflight that could be talked
#: into executing is a preflight nobody should run.
MODE = "DRY_RUN"

ALREADY_MIGRATED = "ALREADY_MIGRATED"
ALREADY_GRANTED = "ALREADY_GRANTED"
NEEDS_HUMAN = "NEEDS_HUMAN"
DEFER = "DEFER"
NOT_WANTED = "NOT_WANTED"

#: Where a principal name can legitimately come from. These are NAMES OF
#: SOURCES, not candidates — the tool does not read either registry, because
#: reading the OAuth client registry is not something a preflight should do and
#: the PAT principal is one value only its owner can confirm.
PRINCIPAL_SOURCES = (
    "oauth:<client_id> — read from the authorization server's client registry "
    "by whoever runs the consent flow",
    "owner-pat — the operator's own credential, confirmed by its holder",
)

#: (verb, per-device flag, the old per-session list that may carry the wildcard).
#: Only these three have a per-device flag; `approve` and `navigate` are
#: implemented scopes with no operator-grant verb for a device flag to pair with,
#: so a migration cannot express them and this tool does not invent a row.
VERB_FLAGS = (
    ("send", "allow_send", "send_allow"),
    ("create", "allow_create", "create_allow"),
    ("control", "allow_control", "control_allow"),
)

WILDCARD = "*"
PRINCIPAL_PLACEHOLDER = "<PRINCIPAL>"


def _read_binding(path: Path) -> dict:
    """Parse the binding as data. Deliberately NOT `load_config`.

    `load_config` is the right reader for a binding that is meant to work, and
    the whole point here is that this one does not: it would raise before we
    could report what is in it. So the file is read as YAML and the loader is
    asked separately, for its refusal.
    """
    if not path.is_file():
        raise refuse("BAD_REQUEST", f"no binding at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise refuse("BAD_REQUEST", f"{path} is not readable YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise refuse("BAD_REQUEST", f"{path} is not a mapping")
    return raw


def _loader_verdict(path: Path) -> dict:
    """Whether THIS branch's loader accepts the binding, and its refusal if not."""
    from fleet_mcp.config import load_config

    try:
        load_config(path)
    except FleetRefusal as exc:
        return {"loads": False, "code": exc.code, "detail": exc.detail}
    return {"loads": True, "code": None, "detail": None}


def _ownership(ref: str, registry_path: str | None) -> str:
    """`company` / `personal` / `UNKNOWN` for one device ref.

    UNKNOWN is returned rather than a default, and every caller turns UNKNOWN
    into DEFER. Guessing here would decide the one question — may this device be
    granted — that the registry exists to answer.
    """
    from fleet_mcp.registry import load_registry

    try:
        devices = load_registry(registry_path).all()
    except Exception:  # noqa: BLE001 - any failure means "not established"
        return "UNKNOWN"
    for device in devices:
        if str(device.device_ref) == ref:
            return str(getattr(device, "ownership", None) or "UNKNOWN")
    return "UNKNOWN"


def _list_shape(value: Any) -> dict:
    """The SHAPE of an old session list. Never the session refs themselves.

    A session ref embeds a device and a native session id; the migration does
    not need either, only whether the list is a wildcard, empty, or a named set.
    """
    if value is None:
        return {"present": False, "wildcard": False, "named_count": 0}
    if isinstance(value, dict):  # create_allow is a project mapping
        keys = [str(k) for k in value]
        return {"present": True, "wildcard": WILDCARD in keys, "named_count": len(keys)}
    if isinstance(value, (list, tuple)):
        entries = [str(v) for v in value]
        return {"present": True, "wildcard": WILDCARD in entries, "named_count": len(entries)}
    return {"present": True, "wildcard": False, "named_count": 0}


def _principals_already_named(policy: dict) -> int:
    raw = policy.get("operator_grants")
    if not isinstance(raw, dict):
        return 0
    return sum(1 for name in raw if str(name).strip() and str(name).strip() != WILDCARD)


def _granted_by_device(policy: dict) -> dict[str, set[str]]:
    """Verbs already granted per device, READ from the binding as written.

    This reads principals that a human already wrote; it never derives one. The
    distinction is the whole tool: naming a verb the operator already named is
    reporting, and inventing one they did not is the escalation this preflight
    exists to refuse.
    """
    raw = policy.get("operator_grants")
    if not isinstance(raw, dict):
        return {}
    granted: dict[str, set[str]] = {}
    for name, devices in raw.items():
        if not isinstance(name, str) or not name.strip() or name.strip() == WILDCARD:
            continue
        if not isinstance(devices, dict):
            continue
        for device_ref, verbs in devices.items():
            if not isinstance(verbs, (list, tuple)):
                continue
            granted.setdefault(str(device_ref), set()).update(str(v) for v in verbs)
    return granted


def preflight(binding_path: str | Path, registry_path: str | None = None) -> dict:
    """Read a binding and report what a human must decide. Writes nothing."""
    path = Path(binding_path).expanduser()
    raw = _read_binding(path)
    policy = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    providers = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}

    registry = registry_path or (
        str(raw.get("registry_path")) if raw.get("registry_path") else None)

    rows: list[dict] = []
    already_granted = _granted_by_device(policy)
    for device_ref in sorted(str(k) for k in providers):
        entry = providers[device_ref] if device_ref in providers else {}
        entry = entry if isinstance(entry, dict) else {}
        ownership = _ownership(device_ref, registry)
        eligible = ownership == "company"
        for verb, flag, old_key in VERB_FLAGS:
            flag_set = bool(entry.get(flag, False))
            shape = _list_shape(policy.get(old_key))
            if not flag_set:
                status, reason = NOT_WANTED, f"`{flag}` is not set on this device"
            elif not eligible:
                status = DEFER
                reason = (f"registry ownership is {ownership!r}, not 'company'; a "
                          f"grant naming this device would be inert, and this tool "
                          f"will not decide eligibility")
            elif verb in already_granted.get(device_ref, set()):
                # Read from what an operator ALREADY wrote, not derived. A named
                # grant that covers this device is the migrated state, and
                # reporting it as "needs a human" would make a finished migration
                # look unfinished — which is its own kind of wrong answer.
                status = ALREADY_GRANTED
                reason = (f"`{old_key}` is not needed: a named "
                          f"`operator_grants` entry already carries `{verb}` on "
                          f"this device")
            elif shape["wildcard"]:
                status = NEEDS_HUMAN
                reason = (f"`{old_key}` contains '*'. A wildcard is the absence of "
                          f"a principal list, so the principals cannot be derived "
                          f"from it and must be named by a human")
            elif shape["present"] and shape["named_count"]:
                status = NEEDS_HUMAN
                reason = (f"`{old_key}` names {shape['named_count']} session(s). "
                          f"Sessions are not principals: they say which sessions, "
                          f"not who may ask")
            else:
                status = NEEDS_HUMAN
                reason = (f"`{flag}` is set and `{old_key}` is empty — every "
                          f"credential can reach every session today, which is "
                          f"broader than `{old_key}` can express")
            rows.append({
                "device_ref": device_ref,
                "verb": verb,
                "device_flag": flag,
                "device_flag_set": flag_set,
                "old_list_key": old_key,
                "old_list": shape,
                "ownership": ownership,
                "status": status,
                "reason": reason,
            })

    named = _principals_already_named(policy)
    statuses = {row["status"] for row in rows}
    if not rows:
        verdict = DEFER
    elif NEEDS_HUMAN in statuses:
        verdict = NEEDS_HUMAN
    elif DEFER in statuses:
        verdict = DEFER
    elif ALREADY_GRANTED in statuses:
        verdict = ALREADY_MIGRATED
    else:
        # Every row is NOT_WANTED: no device flag is set, so there is nothing to
        # migrate. That is not "already migrated" — saying so would report a
        # binding that grants nothing as a finished migration.
        verdict = DEFER

    return {
        "schema": SCHEMA,
        "mode": MODE,
        "binding": str(path),
        "loader": _loader_verdict(path),
        "principals_already_named": named,
        "verdict": verdict,
        "principals_needed": [
            {"device_ref": row["device_ref"], "verb": row["verb"]}
            for row in rows if row["status"] == NEEDS_HUMAN
        ],
        "principal_sources": list(PRINCIPAL_SOURCES),
        "devices": rows,
        "template": _template(rows),
        "writes": [],
        "restarts": [],
        "notes": [
            "A wildcard cannot be converted: it recorded no principals, and this "
            "tool does not guess them.",
            "`approve` and `navigate` have no operator-grant row: they are "
            "implemented scopes with no per-device flag, so a grant cannot "
            "express them yet.",
            "Nothing here is applied. `writes` and `restarts` are empty by "
            "construction: this module has no write path.",
        ],
    }


def _template(rows: list[dict]) -> str:
    """A skeleton for a human to fill. Placeholders, never a value."""
    needed = [row for row in rows if row["status"] == NEEDS_HUMAN]
    if not needed:
        return ""
    lines = [
        "# Generated by fleet_mcp/deploy/binding_migration.py -- DRY RUN.",
        "# Nothing below is applied. Print it, fill in the real principal, review it.",
        "#",
        "# The principal name is NOT derivable from the old binding. Sources:",
    ]
    lines += [f"#   - {source}" for source in PRINCIPAL_SOURCES]
    lines += [
        "#",
        "# Replace <PRINCIPAL> with a real name. Do NOT write '*': the loader",
        "# refuses it, and a second wildcard would be a second way to say the same",
        "# thing this migration exists to remove.",
        "policy:",
        "  operator_grants:",
        f'    "{PRINCIPAL_PLACEHOLDER}":',
    ]
    by_device: dict[str, list[str]] = {}
    for row in needed:
        by_device.setdefault(row["device_ref"], []).append(row["verb"])
    for device_ref in sorted(by_device):
        verbs = ", ".join(sorted(by_device[device_ref]))
        lines.append(f"      {device_ref}: [{verbs}]")
    lines += [
        "  # Drop these once the named table above is in place:",
        "  #   allow_wildcard_send: true   (and the '*' it exists to permit)",
    ]
    # Once per KEY, not once per row: the same wildcard is reported against every
    # device it applies to, and repeating the line four times would read as four
    # separate things to remove.
    for key in dict.fromkeys(row["old_list_key"] for row in rows
                             if row["old_list"]["wildcard"]):
        lines.append(f"  #   {key}: contains '*' (REFUSED at load)")
    return "\n".join(lines) + "\n"


def render(report: dict) -> str:
    """A human-readable rendering. Same information as the JSON, no more."""
    lines = [f"binding: {report['binding']}", f"mode: {report['mode']}",
             f"verdict: {report['verdict']}",
             f"loads under this branch: {report['loader']['loads']}"]
    if not report["loader"]["loads"]:
        lines.append(f"  refusal: [{report['loader']['code']}] {report['loader']['detail']}")
    if report["principals_needed"]:
        lines.append("")
        lines.append("a human must name a principal for each of:")
        lines += [f"  - {row['device_ref']}  verb={row['verb']}"
                  for row in report["principals_needed"]]
        lines.append("")
        lines.append("where a principal can come from:")
        lines += [f"  - {source}" for source in report["principal_sources"]]
    if report["template"]:
        lines += ["", "template (fill in <PRINCIPAL>; nothing is applied):", "",
                  report["template"].rstrip()]
    lines += ["", f"writes: {report['writes']}  restarts: {report['restarts']}"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binding", required=True,
                        help="the Fleet binding to preflight (read-only)")
    parser.add_argument("--registry", default=None,
                        help="device registry; defaults to the binding's own registry_path")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    try:
        report = preflight(args.binding, args.registry)
    except FleetRefusal as exc:
        print(f"{exc.code}: {exc.detail}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    # 0 = the preflight ran. The verdict is in the document, not the exit code:
    # a nonzero exit here would read as "the tool failed" when the tool is the
    # one thing that worked.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
