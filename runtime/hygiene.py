#!/usr/bin/env python3
"""Classify what is on this host, and admit nothing by accident.

The rule this module is built around
-----------------------------------
**Age is not staleness, and a name is not an owner.**

The obvious hygiene policy is "remove containers stopped more than N hours
ago". On this host that policy deletes a production-parity baseline, an
active parallel workstream, and a different user's project — because what
those things have in common is that they are stopped, and what distinguishes
them is who owns them and what they were for. Age cannot see the difference.
Neither can a container name: `fjd-req1-e2e-litellm` reads as "fjd, request 1"
and is not evidence of either.

So classification uses only things that can be checked:

    is it in the registry?            → its declaration says what it is
    what do its docker labels say?    → project and working directory
    does that working directory still exist?  → or the thing that owned it is gone
    is it running?                    → a running thing is not a cleanup candidate

Everything else is `UNKNOWN_OWNER`, which is a terminal classification: it is
reported, it is never a candidate, and no action path reaches it.

What this module cannot do
--------------------------
It has no `remove`, `stop` or `prune` method, and it imports no mutating verb.
`admission()` returns a decision, not a permission slip — the caller that
executes is a different component, deliberately, so that "what should happen"
and "do it" are reviewable separately. In this round the caller does not exist
for anything above `STOP`, and `PRUNE` is denied outright with no override.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry = _load("serveros_runtime_registry", "registry.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
attribution_mod = _load("serveros_runtime_attribution", "attribution.py")

RegistryError = registry.RegistryError
Inventory = inventory_mod.Inventory


class HygieneError(RegistryError):
    """A hygiene question could not be answered."""


# ---------------------------------------------------------------------------
# Classification vocabulary (§13)
# ---------------------------------------------------------------------------

CLASSES = (
    # Declared protected, by registration or by `protected_refs`. No automatic
    # path reaches it, on any trigger.
    "PROTECTED",
    # Registered, running, desired running. In use.
    "ACTIVE",
    # Registered with a bounded lifecycle whose owning work item is known to
    # be closed. The ONLY class the automatic path may ever act on.
    "STALE_CANDIDATE",
    # Its owner's working directory no longer exists. ORPHANED says the owner
    # departed, not that the contents are worthless — those are different
    # claims and only one of them is checkable.
    "ORPHANED",
    # No owner could be confirmed from anything checkable. Terminal: reported,
    # counted, and never a candidate. This is the correct and expected answer
    # for most containers on a shared host.
    "UNKNOWN_OWNER",
    # Stopped, not registered, but carrying a typed owner ref — so an owner
    # can be named and asked. Still not actionable on its own: being nameable
    # is not the same as being abandoned.
    "STOPPED_RECLAIM_CANDIDATE",
)

# Classes the automatic path may ever act on, under policy. The complement is
# not merely "safe" — it is unreachable, which is a stronger property.
ACTIONABLE_CLASSES = frozenset({"STALE_CANDIDATE"})

# Classes that must never appear as a candidate in any report, listed
# explicitly so that adding a class forces a decision about it.
NEVER_ACTIONABLE = frozenset(
    {"PROTECTED", "ACTIVE", "ORPHANED", "UNKNOWN_OWNER", "STOPPED_RECLAIM_CANDIDATE"}
)

# The label a container uses to declare a typed owner. ServerOS defines this
# convention; nothing else writes it.
OWNER_LABEL = "serveros.owner"


# ---------------------------------------------------------------------------
# Risk tiers (§14)
# ---------------------------------------------------------------------------

# Ordered by blast radius. The default answer for the top of the list is "no",
# and that default is not configurable from a call site.
RISK_TIERS = ("STOP", "REMOVE_CONTAINER", "REMOVE_IMAGE", "REMOVE_VOLUME", "PRUNE")

TIER_POLICY = {
    # Stopping is reversible and cheap; it is still bounded to resources whose
    # owner registered them as disposable.
    "STOP": {"default": "policy", "requires": "registered + disposable/task-scoped + not protected"},
    "REMOVE_CONTAINER": {
        "default": "stricter",
        "requires": "the STOP policy, plus evidence the owning work item is closed",
        "note": "a stopped container may still be the only copy of its own state",
    },
    # Never automatic. An image is shared by every container that references
    # it, including ones this node cannot see.
    "REMOVE_IMAGE": {"default": "deny", "requires": "explicit admission decision"},
    # The volume is usually the irreplaceable half. The container can be
    # rebuilt from an image; the volume cannot be rebuilt from anything.
    "REMOVE_VOLUME": {"default": "deny", "requires": "explicit approval"},
    # Deliberately unavailable. `docker system prune` and `docker volume
    # prune` take everything not currently attached, which on this host
    # includes the production-parity baseline's volumes the moment its
    # containers are briefly down.
    "PRUNE": {"default": "deny", "requires": "never in v1", "implemented": False},
}


def admission(tier: str, classification: str, *, lifecycle=None, protection=None,
               owner_evidence: dict | None = None, approved: bool = False) -> dict:
    """Decide whether a tier of action is permitted. Returns a decision only.

    There is no override parameter for PRUNE and no code path in this module
    that acts on the answer. `approved=True` models a human decision that has
    already been made and recorded; it is not a way for a caller to grant
    itself permission, because each tier names what the approval has to be
    about.
    """
    if tier not in RISK_TIERS:
        raise HygieneError(f"unknown risk tier {tier!r}. Known: {list(RISK_TIERS)}")

    if tier == "PRUNE":
        return {
            "tier": tier, "allowed": False, "decision": "DENY_BY_DEFAULT",
            "because": (
                "`docker system prune` and `docker volume prune` reclaim "
                "everything not currently attached. On this host that includes "
                "the production-parity baseline's volumes during any moment "
                "its containers are down. ServerOS does not implement this, "
                "and there is no override."
            ),
        }

    if protection == "protected":
        return {
            "tier": tier, "allowed": False, "decision": "DENY_PROTECTED",
            "because": (
                "protection: protected is not a hint. No automatic path "
                "reaches it, on any trigger, including low disk."
            ),
        }

    if classification in NEVER_ACTIONABLE:
        return {
            "tier": tier, "allowed": False, "decision": f"DENY_CLASS_{classification}",
            "because": _why_class_blocks(classification),
        }

    if classification not in ACTIONABLE_CLASSES:
        return {
            "tier": tier, "allowed": False, "decision": "DENY_NOT_ACTIONABLE",
            "because": f"{classification} is not in the actionable set {sorted(ACTIONABLE_CLASSES)}",
        }

    if tier == "STOP":
        return {
            "tier": tier, "allowed": True, "decision": "ALLOW_UNDER_POLICY",
            "because": "registered, owned, not protected, and bounded lifecycle",
        }

    if tier in ("REMOVE_CONTAINER", "REMOVE_IMAGE", "REMOVE_VOLUME"):
        if not approved:
            return {
                "tier": tier, "allowed": False, "decision": "NEEDS_APPROVAL",
                "because": TIER_POLICY[tier]["requires"],
            }
        if tier == "REMOVE_VOLUME" and not (owner_evidence or {}).get("backup_verified"):
            return {
                "tier": tier, "allowed": False, "decision": "NEEDS_APPROVAL",
                "because": (
                    "a volume removal was approved but no verified backup of "
                    "its contents is recorded. The container can be rebuilt "
                    "from an image; the volume cannot be rebuilt from anything."
                ),
            }
        return {
            "tier": tier, "allowed": True, "decision": "ALLOW_WITH_APPROVAL",
            "because": TIER_POLICY[tier]["requires"],
        }

    return {"tier": tier, "allowed": False, "decision": "DENY_NOT_ACTIONABLE", "because": ""}


def _why_class_blocks(classification: str) -> str:
    return {
        "PROTECTED": "declared protected by its owner",
        "ACTIVE": "in use right now; hygiene does not stop working things",
        "ORPHANED": (
            "the owner is gone, which is not the same as the data being "
            "worthless. Whether to keep it is a person's decision."
        ),
        "UNMANAGED_ACTIVE": "running and unregistered — report it, then ask why",
        "UNKNOWN_OWNER": (
            "no owner could be confirmed. Guessing an owner from a name is how "
            "a live lane gets reclaimed. Fails safe: DEFER, always."
        ),
    }.get(classification, classification)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class Hygiene:
    """Turns docker's view of the host into classified facts.

    Read-only. Nothing here stops, removes, prunes or restarts anything.
    """

    def __init__(self, reg, inventory=None, *, known_work_items=None,
                 worktree_root=None) -> None:
        self.registry = reg
        self.inventory = inventory if inventory is not None else Inventory()
        # Optional: the set of work-item ids that are still open. Deliberately
        # injected rather than discovered, because deciding whether a work item
        # is closed is work-management's authority and not this module's.
        self.known_work_items = known_work_items
        self.worktree_root = worktree_root

    # -- containers --------------------------------------------------------

    def containers(self) -> dict:
        listed = self.inventory.docker_list(all_=True)
        if listed.get("state") == inventory_mod.UNAVAILABLE:
            return listed
        rows = []
        for container in listed["containers"]:
            rows.append(self.classify_container(container))
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["classification"]] = counts.get(row["classification"], 0) + 1
        return {
            "count": len(rows),
            "by_classification": counts,
            "containers": rows,
            "actionable": [r["name"] for r in rows if r["classification"] in ACTIONABLE_CLASSES],
        }

    def classify_container(self, container: dict) -> dict:
        name = container.get("Names") or container.get("Name") or "<unnamed>"
        labels = _parse_labels(container.get("Labels"))
        state = (container.get("State") or "").lower()
        running = state == "running"

        registered = self._registry_lookup(name)
        owner = self._owner_evidence(labels, name)
        ref_hits = self.registry.refs_matching(name)

        if ref_hits["protected"]:
            # Checked FIRST, before registration and before any ownership
            # reasoning. This is the §3 guard: LIT-0103 and LIT-0104 are off
            # limits regardless of what anything else concludes about them.
            classification = "PROTECTED"
            reason = _protected_reason(ref_hits["protected"])
        elif registered is not None:
            classification = self._classify_registered(registered, running)
            reason = self._reason_registered(registered, running)
        elif ref_hits["defer"]:
            classification = "UNKNOWN_OWNER"
            reason = (
                f"name matches {ref_hits['defer'][0]['ref']}, which carries an "
                f"unknown owner or an active writer. DEFER is the answer: "
                f"neither the name nor the match settles who owns it."
            )
        elif owner["orphaned"]:
            classification = "ORPHANED"
            reason = (
                f"its compose working directory {owner['working_dir']} no "
                f"longer exists. The owner departed; the contents are not "
                f"thereby worthless, and whether to keep them is a person's "
                f"decision."
            )
        elif owner["typed_ref"] and not running:
            classification = "STOPPED_RECLAIM_CANDIDATE"
            reason = (
                f"stopped, unregistered, and declaring a typed owner "
                f"{owner['typed_ref']} via `{OWNER_LABEL}`. Nameable, not "
                f"abandoned — someone has to answer. Not actionable on its own."
            )
        else:
            classification = "UNKNOWN_OWNER"
            reason = owner["why"]

        return {
            "name": name,
            "container_id": (container.get("ID") or "")[:12],
            "image": container.get("Image"),
            "state": state or "unknown",
            "status": container.get("Status"),
            "running": running,
            "registered_as": registered.id if registered else None,
            "owner": owner,
            "classification": classification,
            "reason": reason,
            # Reported for every container so a reader can see what the
            # decision had to work with, including when the answer is nothing.
            "evidence": {
                "compose_project": labels.get("com.docker.compose.project"),
                "compose_working_dir": labels.get("com.docker.compose.project.working_dir"),
                "typed_owner_label": labels.get(OWNER_LABEL),
                "registry_hit": registered.id if registered else None,
                "protected_ref_hit": [e["ref"] for e in ref_hits["protected"]] or None,
                "defer_ref_hit": [e["ref"] for e in ref_hits["defer"]] or None,
            },
            "actionable": classification in ACTIONABLE_CLASSES,
        }

    def _classify_registered(self, resource, running: bool) -> str:
        if resource.protection == "protected":
            return "PROTECTED"
        if running:
            return "ACTIVE"
        if resource.lifecycle in ("task-scoped", "disposable", "session-scoped"):
            # Registered with a bounded lifetime AND stopped. Whether the
            # owning work item is actually closed is a separate question the
            # caller must answer; this only says the declaration permits it.
            if self._owner_closed(resource):
                return "STALE_CANDIDATE"
            return "ACTIVE"
        # persistent, not protected, stopped. That is a fault, not a cleanup.
        return "ACTIVE"

    def _reason_registered(self, resource, running: bool) -> str:
        if resource.protection == "protected":
            return (
                f"registered as {resource.id} with protection=protected. No "
                f"automatic path reaches it, on any trigger."
            )
        if running:
            return f"registered as {resource.id}, desired running, and running"
        if resource.lifecycle in ("task-scoped", "disposable", "session-scoped"):
            if self._owner_closed(resource):
                return (
                    f"registered as {resource.id} with lifecycle="
                    f"{resource.lifecycle}, stopped, and its owning work item "
                    f"is closed. This is the one class policy may act on."
                )
            return (
                f"registered as {resource.id} with lifecycle={resource.lifecycle} "
                f"and stopped, but its owning work item is not known to be "
                f"closed. Not a candidate."
            )
        return (
            f"registered as {resource.id} with lifecycle=persistent and "
            f"stopped. That is a fault to report, not garbage to collect."
        )

    def _owner_closed(self, resource) -> bool:
        if self.known_work_items is None:
            # Not knowing is not permission. A caller that has not told us
            # which work items are open gets no candidates.
            return False
        if resource.owner_type == "work-item":
            return resource.owner_ref.split(":", 1)[-1] not in self.known_work_items
        return False

    def _registry_lookup(self, container_name: str):
        for resource in self.registry.all():
            for probe in resource.probes:
                if probe.get("container") == container_name:
                    return resource
        return None

    def _owner_evidence(self, labels: dict, name: str) -> dict:
        """What can actually be checked about who owns this container.

        The tempting answer — "it has a `com.docker.compose.project` label, so
        its owner is that project" — is WRONG, and this host demonstrates why.
        Measured here, `lit-0101`, `lit-0101-after`, `lit-0103-r3` and
        `fjd-req1-e2e` all carry the SAME
        `com.docker.compose.project.working_dir`:

            /opt/applications/data/home-hwz/workspace/test-envs/litellm-bridge/litellm-e2e

        Those are eight containers, three unrelated projects and one protected
        parallel workstream, sharing one directory. A compose working dir is
        where a config file lives. It is *provenance* — it says what produced
        the container — and provenance is not ownership. Treating it as
        ownership is what put `lit-0103-r3-*` in a reclaim bucket.
        """
        project = labels.get("com.docker.compose.project")
        working_dir = labels.get("com.docker.compose.project.working_dir")
        typed = labels.get(OWNER_LABEL)
        evidence = {
            "compose_project": project,
            "working_dir": working_dir,
            "typed_ref": None,
            "typed_ref_valid": False,
            "orphaned": False,
            "provenance": None,
            "why": (
                "unregistered, and no typed owner ref: its owner cannot be "
                "confirmed from anything checkable. A container name is a "
                "string, and a compose project label says which compose run "
                "created it — neither is an owner."
            ),
        }
        if project:
            evidence["provenance"] = f"compose-project:{project}"
        if typed:
            # Declared ownership, validated as a typed ref — the only label
            # that can promote a container out of UNKNOWN_OWNER.
            import re as _re

            if _re.match(r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._-]*$", typed):
                evidence["typed_ref"] = typed
                evidence["typed_ref_valid"] = True
                evidence["why"] = f"declares owner {typed}"
                return evidence
            evidence["why"] = (
                f"carries `{OWNER_LABEL}={typed}`, which is not a typed ref "
                f"(expected `<type>:<id>`). A malformed owner declaration is "
                f"not an owner; it is a reason to ask."
            )
            return evidence
        # Another user's directory is another actor's resource. Not "unknown":
        # known, and known not to be ours to classify.
        if working_dir and _foreign_home(working_dir):
            evidence["why"] = (
                f"its working directory {working_dir} belongs to a different "
                f"user. Another actor's resources are DEFER, always."
            )
            return evidence
        if working_dir and not Path(working_dir).exists():
            # The one ownership-adjacent fact that is genuinely checkable
            # without a ref: the thing that made it has been removed.
            evidence["orphaned"] = True
            evidence["why"] = f"its working directory {working_dir} no longer exists"
            return evidence
        return evidence

    # -- disk and processes ------------------------------------------------

    def disk(self) -> dict:
        status = self.inventory.disk_status()
        return {
            "roles": status["roles"],
            "advisory": self.inventory.pressure(),
        }

    def processes(self) -> dict:
        top = self.inventory.processes_top(limit=15, by="cpu")
        return {
            "top_cpu": top.get("rows", []),
            "scope_note": top.get("scope_note"),
            "managed_note": (
                "A process being long-running and owned by this user does not "
                "make it a managed resource. Only registry entries are managed."
            ),
        }

    # -- the report --------------------------------------------------------

    def report(self, *, observations=None) -> dict:
        """§15's hygiene report. Every section is a fact, not a verdict."""
        containers = self.containers()
        if containers.get("state") == inventory_mod.UNAVAILABLE:
            return {
                "docker": containers,
                "disk": self.disk(),
                "processes": self.processes(),
                "managed_runtime": observations or {},
                "summary": {"actionable_count": 0, "actionable": [],
                            "note": "docker is not reachable; no classification was made"},
            }
        rows = containers["containers"]
        by_class = containers["by_classification"]
        return {
            "docker": {
                "running": sum(1 for c in rows if c["running"]),
                "stopped": sum(1 for c in rows if not c["running"]),
                "by_classification": by_class,
                "protected": by_class.get("PROTECTED", 0),
                "active_task_owned": by_class.get("ACTIVE", 0),
                "stale_candidates": by_class.get("STALE_CANDIDATE", 0),
                "unknown_owner": by_class.get("UNKNOWN_OWNER", 0),
                "orphaned": by_class.get("ORPHANED", 0),
                # "Running and nobody registered it" is a facet of
                # UNKNOWN_OWNER, not a class of its own: §13's vocabulary is
                # closed, and inventing a seventh word to avoid reading two
                # fields would put a class in the report that no other
                # component knows how to interpret.
                "unregistered_running": sum(
                    1 for c in rows if c["running"] and not c["registered_as"]
                ),
                "actionable": containers["actionable"],
                "containers": rows,
            },
            "disk": self.disk(),
            "processes": self.processes(),
            "managed_runtime": observations or {},
            "summary": self.summary(containers),
        }

    def summary(self, containers: dict) -> dict:
        by_class = containers.get("by_classification", {})
        actionable = containers.get("actionable", [])
        unowned = by_class.get("UNKNOWN_OWNER", 0)
        return {
            "actionable_count": len(actionable),
            "actionable": actionable,
            "note": (
                "Nothing is removed by this command. Anything listed as "
                "actionable is a candidate only; the tier policy decides, and "
                "removal above STOP needs a person."
            ) if actionable else (
                "Nothing on this host is a cleanup candidate. This is not "
                "because the host is tidy — it is because every stopped "
                "container here is protected, owned by another actor, or "
                "unowned, and none of those is a cleanup category."
            ),
            "unowned_note": (
                f"{unowned} container(s) have no confirmable owner. That is "
                f"the expected result on a shared host and it is not a defect "
                f"in the classification: a container name is a string, and a "
                f"compose project label names a compose run, not an owner. "
                f"The fix is for owners to declare `{OWNER_LABEL}=<type>:<id>`, "
                f"not for the classifier to guess."
            ) if unowned else None,
            "classes_seen": sorted(by_class),
        }

    def unregistered_worth_asking_about(self, containers: dict) -> list[dict]:
        """Running things nobody registered. Not a cleanup list — a question.

        Every entry is presented as a question for a person, because that is
        what it is: the resource works, and nobody has said what it is for.
        """
        return [
            {"name": c["name"], "image": c["image"], "since": c.get("status"),
             "why": c["reason"]}
            for c in containers.get("containers", [])
            if c["running"] and not c["registered_as"] and c["classification"] != "PROTECTED"
        ]


def _protected_reason(entries: list[dict]) -> str:
    first = entries[0]
    why = f" matches protected ref {first['ref']}"
    if first.get("because"):
        why += f" ({first['because']})"
    return (
        f"name{why}. Protection is declared, not inferred: this is checked "
        f"before registration and before any ownership reasoning, so no other "
        f"conclusion can override it."
    )


def _count_stopped(containers: dict) -> int:
    return sum(1 for c in containers.get("containers", []) if not c.get("running"))


def _parse_labels(raw) -> dict:
    """Docker prints labels as `a=b,c=d` or as a dict depending on format."""
    if isinstance(raw, dict):
        return dict(raw)
    labels: dict[str, str] = {}
    if not raw:
        return labels
    for pair in str(raw).split(","):
        if "=" in pair:
            key, _, value = pair.partition("=")
            labels[key.strip()] = value.strip()
    return labels


def _foreign_home(path: str) -> bool:
    """A path under someone else's home is not ours to classify.

    Delegates to `attribution.foreign_home`. The rule used to live here and
    was going to be copied for container attribution; one copy in the shared
    vocabulary module means the classification ladder and the container
    classifier cannot drift into disagreeing about whose directory this is.
    """
    return attribution_mod.foreign_home(path)
