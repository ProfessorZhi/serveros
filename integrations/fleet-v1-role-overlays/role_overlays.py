#!/usr/bin/env python3
"""L3 role overlays — selection and admission, not a catalogue or an installer.

WHAT THIS IS
------------
`role-overlays.yaml` says which already-catalogued Abilities a device ATTACHES
for one task, which native observations it already has, and which GPU
capabilities are still gaps. This module resolves that policy:

    plan(role, task, catalogue, catalogue_revision, acceptance=..., ...)

and refuses anything the data does not support. It does not install, does not
claim a device state, and does not create Work Items.

BOUNDARIES (why nothing here is a second authority)
---------------------------------------------------
* **ability-management** owns Artifact Identity/Revision/Compatibility/Adoption/
  Profile. A role names Abilities; the caller passes the catalogue, and a name
  that does not resolve is refused. There is no embedded ability definition.
* **ServerOS Work Engine** owns Requirement/Work Item/Run/Outcome/authorization.
  A `work-engine` task class still creates a canonical Work Item ONLY when the
  caller presents an acceptance record from that engine. The overlay cannot
  mint one, and a foreign authority is refused.
* **Fleet/CloudCLI** owns observed installed/loaded/ready/granted. A source-level
  plan reports `device_state` as given (default UNKNOWN) and never fills
  installed/ready/effective.
* **Native capabilities are observations.** `gpu.readonly`, `core-lite.status`
  and the Windows adapter come from L1's contract; resolving a native id as a
  Skill is refused, because wrapping an observation in a Skill makes a second
  identity for one provider.
* **Company load first.** A personal idle GPU task is deferred while company
  work is active; approval is required; and it produces no Work Item, no company
  report, no Feishu message and no cross-device sync. That is enforced here, not
  left to the caller's discipline.

SOURCE-LEVEL, NOT INSTALLED
---------------------------
Every plan carries `observation.basis = "source-level overlay..."` and
`installed/ready = UNKNOWN`, `effective = null`. A green unit test says the
policy is implementable, not that a device is ready.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

SCHEMA = "serveros/ability-role-overlays.v1"
PLAN_SCHEMA = "serveros/ability-role-plan.v1"

PERSONAL = "personal-idle"
COMPANY = "company"
WORK_ENGINE = "work-engine"

#: The repository-local bootstrap Skills. `authority.local_skills` may not
#: introduce a name outside this set: a free-form local list would be a second
#: catalogue smuggled in through the authority block.
LOCAL_BOOTSTRAP = frozenset({"serveros"})

#: What a personal idle task may attach. Report/work abilities are excluded so
#: the attach set cannot contradict the task's own `forbid` list.
PERSONAL_ATTACH = frozenset({
    "serveros", "runtime-management", "workspace-management", "test-management",
})

#: Words a caller may echo as a device observation. They are not claims this
#: module makes, and `installed/ready/effective` stay UNKNOWN/null regardless.
DEVICE_STATES = frozenset({"UNKNOWN", "OBSERVED", "NOT_OBSERVED"})

#: A personal idle task must say all of these, in data, or loading fails.
PERSONAL_FORBID = frozenset({
    "b3_requirement_board",
    "b4_canonical_work_item",
    "company_report",
    "feishu_report",
    "cross_device_sync",
})


class Refused(ValueError):
    """The plan cannot be made from this data. 'No' is an answer, not a state."""


def load(path) -> dict:
    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refused(f"cannot read role overlays: {exc}") from None
    except yaml.YAMLError as exc:
        raise Refused(f"role overlays are not YAML: {exc}") from None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise Refused(f"role overlays must declare schema {SCHEMA}")
    return document


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{field} must be a non-empty string")
    return value.strip()


def _names(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip()
                                              for v in value):
        raise Refused(f"{field} must be a list of names")
    if len(set(value)) != len(value):
        raise Refused(f"{field} repeats a name")
    return list(value)


def validate(document: Mapping, catalogue: Iterable[str]) -> dict:
    """Check the data against one authority revision's catalogue.

    Returns the role table. Raises `Refused` for every disagreement, including a
    catalogue that is empty (a validator that saw nothing must not certify).
    """
    if not isinstance(document, Mapping):
        raise Refused("role overlays must be a mapping")
    if document.get("schema") != SCHEMA:
        raise Refused(f"role overlays must declare schema {SCHEMA}")
    authority = document.get("authority") or {}
    if authority.get("catalogue") != "ability-management":
        raise Refused("the catalogue authority must be ability-management")
    if authority.get("work_item") != WORK_ENGINE:
        raise Refused("the Work Item authority must be the Work Engine")
    names = frozenset(str(name) for name in catalogue)
    if not names:
        raise Refused("the catalogue is empty; nothing can be resolved")
    local = frozenset(_names(authority.get("local_skills"),
                             "authority.local_skills"))
    outside = sorted(local - LOCAL_BOOTSTRAP)
    if outside:
        raise Refused(
            f"authority.local_skills adds {outside}; only the repository "
            f"bootstrap {sorted(LOCAL_BOOTSTRAP)} may be local, or the "
            "catalogue is no longer the one authority")
    policy = document.get("policy") or {}
    personal_attach = frozenset(_names(
        policy.get("personal_attach_allowed") or sorted(PERSONAL_ATTACH),
        "policy.personal_attach_allowed"))
    if not personal_attach <= LOCAL_BOOTSTRAP | names:
        raise Refused("policy.personal_attach_allowed names an unresolvable ability")
    forbidden_everywhere = _names(document.get("forbidden_everywhere"),
                                  "forbidden_everywhere")
    known = names | local

    native_ids = set((document.get("native_observations") or {}))
    gaps = {row.get("id") for row in document.get("gaps") or []}
    if None in gaps or "" in gaps:
        raise Refused("every gap needs an id")

    roles = document.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise Refused("role overlays declare no roles")
    for role_name, role in roles.items():
        _text(role_name, "role")
        if not isinstance(role, dict):
            raise Refused(f"{role_name}: a role must be a mapping")
        device = _text(role.get("device"), f"{role_name}.device")
        if device not in (document.get("devices") or {}):
            raise Refused(f"{role_name}: unknown device {device!r}")
        native = _names(role.get("native"), f"{role_name}.native")
        for observation in native:
            if observation not in native_ids:
                raise Refused(f"{role_name}: undeclared native observation {observation!r}")
        tasks = role.get("tasks")
        if not isinstance(tasks, dict) or not tasks:
            raise Refused(f"{role_name}: no tasks")
        for task_name, task in tasks.items():
            _text(task_name, f"{role_name}.task")
            if not isinstance(task, dict):
                raise Refused(f"{role_name}/{task_name}: a task must be a mapping")
            attach = _names(task.get("attach"), f"{role_name}/{task_name}.attach")
            unknown = sorted(set(attach) - known)
            if unknown:
                raise Refused(
                    f"{role_name}/{task_name}: attaches {unknown}, which the "
                    f"catalogue does not resolve; a role may not invent a name")
            if len(attach) >= len(known):
                raise Refused(
                    f"{role_name}/{task_name}: attaches {len(attach)} of "
                    f"{len(known)} resolvable abilities; a role attaches per "
                    "task, not the whole registry")
            for gap in _names(task.get("gaps"), f"{role_name}/{task_name}.gaps"):
                if gap not in gaps:
                    raise Refused(f"{role_name}/{task_name}: undeclared gap {gap!r}")
            work_item = task.get("work_item")
            if work_item not in (WORK_ENGINE, "none"):
                raise Refused(f"{role_name}/{task_name}: work_item must be "
                              f"{WORK_ENGINE!r} or 'none'")
            priority = task.get("priority")
            if priority not in (COMPANY, PERSONAL, "idle"):
                raise Refused(f"{role_name}/{task_name}: unknown priority {priority!r}")
            reports = _names(task.get("reports"), f"{role_name}/{task_name}.reports")
            if priority == PERSONAL:
                if work_item != "none":
                    raise Refused(f"{role_name}/{task_name}: a personal idle task "
                                  "must not create a Work Item")
                if reports:
                    raise Refused(f"{role_name}/{task_name}: a personal idle task "
                                  "must not report")
                required = set(_names(task.get("require"),
                                      f"{role_name}/{task_name}.require"))
                if not {"owner_approval", "company_idle"} <= required:
                    raise Refused(f"{role_name}/{task_name}: a personal idle task "
                                  "needs owner_approval and company_idle")
                forbids = set(_names(task.get("forbid"),
                                     f"{role_name}/{task_name}.forbid"))
                if not PERSONAL_FORBID <= forbids:
                    raise Refused(
                        f"{role_name}/{task_name}: a personal idle task must "
                        f"forbid {sorted(PERSONAL_FORBID - forbids)}")
                if task.get("data_boundary") != "local_only":
                    raise Refused(f"{role_name}/{task_name}: personal data must be "
                                  "local_only")
                if not set(attach) <= personal_attach:
                    raise Refused(
                        f"{role_name}/{task_name}: a personal task may attach "
                        f"only {sorted(personal_attach)}; got "
                        f"{sorted(set(attach) - personal_attach)}")
                if set(attach) & set(reports):
                    raise Refused(f"{role_name}/{task_name}: a personal task "
                                  "attaches an ability it forbids reporting with")
            if task.get("attach_all") or "*" in attach:
                raise Refused(f"{role_name}/{task_name}: attach-all is not a role")
    for observation in native_ids:
        if observation in known:
            raise Refused(
                f"native observation {observation!r} is also a resolvable Skill "
                "(catalogue or local); a native capability must not be packaged "
                "as a second identity")
    return {"roles": roles, "gaps": gaps, "native": native_ids,
            "catalogue": names, "local": local,
            "forbidden_everywhere": forbidden_everywhere,
            "personal_attach": personal_attach}


def plan(document: Mapping, *, role: str, task: str, catalogue: Iterable[str],
         catalogue_revision: str, acceptance: Mapping | None = None,
         approval: bool | None = None, company_active: bool | None = None,
         requirement_ref: str | None = None,
         device_state: str = "UNKNOWN", requested_sync: Iterable[str] = (),
         ) -> dict:
    """Resolve one role/task into an attach set and an admission answer.

    It never creates a Work Item on its own. For a `work-engine` task the caller
    must present the engine's acceptance; for a personal task acceptance is
    ignored by design.
    """
    checked = validate(document, catalogue)
    revision = _text(catalogue_revision, "catalogue_revision")
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise Refused("catalogue_revision must be the full pinned 40-hex revision")
    roles = checked["roles"]
    if role not in roles:
        raise Refused(f"{role!r} is not a declared role")
    tasks = roles[role]["tasks"]
    if task not in tasks:
        raise Refused(f"{role!r} has no task class {task!r}")
    spec = tasks[task]
    priority = spec["priority"]

    sync = sorted(str(target) for target in requested_sync)
    if sync:
        raise Refused(
            f"cross-device sync to {sync} is not a role side effect; data moves "
            "only by an explicit, separately authorized operation")

    if device_state not in DEVICE_STATES:
        raise Refused(f"device_state must be one of {sorted(DEVICE_STATES)}")
    # -- admission: company load first; personal needs approval + idle --------
    if priority == PERSONAL:
        if company_active is None:
            decision, why = "REFUSED_COMPANY_LOAD_UNKNOWN", (
                "whether company work is idle must be stated, not assumed")
        elif company_active:
            decision, why = "DEFER_COMPANY_FIRST", (
                "company GPU work is active; a personal idle job waits")
        elif approval is not True:
            decision, why = "REFUSED_APPROVAL_REQUIRED", (
                "owner approval is required for personal use of a company node")
        else:
            decision, why = "ALLOWED_PERSONAL_IDLE", (
                "approved and company work is idle")
    else:
        decision, why = "ALLOWED_COMPANY", "company work has priority"

    # -- Work Item: only the Work Engine's acceptance creates one -------------
    work_item = {"creates": False, "authority": WORK_ENGINE,
                 "canonical_ref": None, "why": "", "acceptance_ignored": False}
    if spec["work_item"] == "none":
        work_item["why"] = "this task class does not create a Work Item"
        work_item["acceptance_ignored"] = acceptance is not None
    elif decision.startswith("DEFER") or decision.startswith("REFUSED"):
        work_item["why"] = "admission did not allow the task"
    elif acceptance is None:
        work_item["why"] = "no acceptance from the Work Engine was presented"
    elif not isinstance(acceptance, Mapping):
        raise Refused("acceptance must be a mapping")
    elif not isinstance(requirement_ref, str) or not requirement_ref.strip():
        work_item["why"] = ("this task needs the requirement_ref it is planned "
                            "for; an acceptance cannot be checked without it")
    elif acceptance.get("authority") != WORK_ENGINE:
        work_item["why"] = (f"acceptance came from {acceptance.get('authority')!r}, "
                            "not the Work Engine")
    elif acceptance.get("accepted") is not True:
        work_item["why"] = "the Work Engine did not accept the requirement"
    elif acceptance.get("requirement_ref") != requirement_ref:
        work_item["why"] = (
            f"the acceptance names {acceptance.get('requirement_ref')!r}, not "
            f"the requirement {requirement_ref!r} being planned")
    else:
        ref = acceptance.get("canonical_ref")
        if not isinstance(ref, str) or not ref.strip():
            work_item["why"] = "the acceptance names no canonical Work Item"
        else:
            work_item.update(creates=True, canonical_ref=ref.strip(),
                             why="the Work Engine accepted this requirement")

    reports = [] if priority == PERSONAL else _names(
        spec.get("reports"), "reports")
    forbidden = sorted(set(_names(spec.get("forbid"), "forbid"))
                       | set(checked["forbidden_everywhere"])
                       | {"install_registry", "automatic_cross_device_sync",
                          "automatic_cross_domain_data_sync"})

    return {
        "schema": PLAN_SCHEMA,
        "role": role,
        "task": task,
        "device": roles[role]["device"],
        "device_class": (document.get("devices") or {}).get(
            roles[role]["device"], {}).get("class"),
        "priority": priority,
        "requirement_ref": requirement_ref,
        "catalogue_revision": revision,
        "attach": sorted(spec["attach"]),
        "native": sorted(_names(roles[role].get("native"),
                                "role.native") + _names(spec.get("native"),
                                                        "task.native")),
        "gaps": sorted(_names(spec.get("gaps"), "task.gaps")),
        "reports": reports,
        "forbidden": forbidden,
        "cross_device_sync": [],
        "data_boundary": spec.get("data_boundary", "company"),
        "admission": {"decision": decision, "why": why},
        "work_item": work_item,
        # Source level is not a device state. Installed/ready/effective are
        # observations the Fleet observer owns; this plan does not answer them.
        "observation": {
            "device_state": str(device_state or "UNKNOWN"),
            "installed": "UNKNOWN",
            "ready": "UNKNOWN",
            "effective": None,
            "basis": ("source-level overlay; a Fleet device report is required "
                      "before any installed/ready/effective claim"),
        },
        "evidence": "SOURCE_CODE",
    }


__all__ = ["Refused", "load", "validate", "plan", "SCHEMA", "PLAN_SCHEMA",
           "PERSONAL", "COMPANY", "WORK_ENGINE", "PERSONAL_FORBID"]
