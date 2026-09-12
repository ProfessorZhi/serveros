#!/usr/bin/env python3
"""Container attribution: whose container is this, and what does the evidence prove.

Why this is not "read the name"
-------------------------------
`lit-0104-dev100-litellm` looks like it belongs to work item LIT-0104. It does.
On this host that reading happens to be correct — and the same reading applied
to `fjd-req1-e2e-litellm` produces a work item that does not exist, or worse,
one that does. A container name is a string somebody typed; it is a locator.
The registry already says so, in its own words:

    project:fjd-req1-e2e → DEFER, because "fjd" + "req1" is a plausible
    reading, not an owner. Guessing here is how a live lane gets reclaimed.

So this module separates two things that a name-based reader cannot:

    EVIDENCE      what is on the container — labels, mounts, paths, image,
                  the name the owner chose, the ports, the environment
                  VARIABLE NAMES
    ATTRIBUTION   which of those evidence classes decides an owner, and how
                  much the answer is worth

and it only calls something an owner when a decisive source fires.

The evidence that exists on this host (measured 2026-09-13, 25 containers)
------------------------------------------------------------------------
Every claim below was read off the live machine, not assumed.

  * There is NO `serveros.owner=` label anywhere. The decisive docker-label
    source is implemented and currently has zero instances, which is exactly
    why it must not be the only path — a design that would work if owners
    cooperated is a design that does nothing today.

  * `com.docker.compose.project` + `.project.working_dir` are near-universal.
    The working directory is the strongest path evidence available and it is
    NOT reliable on its own: the `openclaw` compose project has five
    containers whose working_dir values DISAGREE —

        openclaw-postgres    /home/ubuntu/Project/MultiUserClaw
        openclaw-frontend    /opt/applications/data/home-ubuntu/Project/MultiUserClaw
        (three more agree with the latter)

    Same compose project, two different directories, one of them a path that
    does not exist on this node. Reading either as "the" project path is a
    coin flip that looks like a measurement. This module detects the
    disagreement and reports it rather than picking a winner.

  * Four containers carry exactly one label, `com.multiuserclaw.hermes-runtime-config`.
    Its value is runtime configuration. This module reads the KEY and NEVER
    the value: the key namespace is the ownership signal, the value is data
    that may contain anything, and §30 forbids credentials reaching a report.

  * Nine containers carry no attribution label of any kind: `xpra-gui-hwz`,
    `new-api-postgres`, `litellm-minio`, `litellm-os`, `new-api`, the
    buildkit workers. These stay UNKNOWN_OWNER and therefore DEFER.

  * `xpra-gui-hwz` bind-mounts `/` to `/host`. It is also unlabelled. The
    correct output is UNKNOWN_OWNER with a recorded blast radius — not a
    guess, and not silence.

What this module will not do
----------------------------
It does not read `.Config.Env` values. It reads the variable NAMES, which
§6 permits ("environment metadata names only where safe") and which are
genuinely useful — a container whose env names a work item is evidence — and
it never opens the values. The name is the metadata; the value is the secret.

It does not touch, stop, prune or restart anything. §18. Nothing in this file
has a side effect at all: `attribute()` is a pure function of an inspect
record, which is what lets it be tested against the real 25-container shapes
without a Docker daemon.
"""
from __future__ import annotations

import importlib.util
import json
import re
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


attribution = _load("serveros_runtime_attribution", "attribution.py")

FACT = attribution.FACT
INFERRED = attribution.INFERRED
UNKNOWN = attribution.UNKNOWN


class DockerUnavailable(Exception):
    """The daemon could not be asked. Distinct from "there are no containers".

    The same rule as everywhere else in this codebase: failing to look and
    looking and finding nothing must never render the same way, because the
    first is a blind spot and the second is an answer.
    """


CONTRACT = _HERE / "attribution-contract.yaml"

# A container may declare its own owner. This is the strongest evidence there
# is — the owner wrote it — and there are currently zero instances of it on
# this host. Recorded here so that the day one appears, it is decisive without
# a code change.
OWNER_LABEL = "serveros.owner"

# Label keys that belong to a container ecosystem and therefore say nothing
# about WHO owns the container. Nearest-match prefixes.
_ECOSYSTEM_LABEL_PREFIXES = (
    "com.docker.", "org.opencontainers.", "org.label-schema.", "dev.chainguard.",
    "io.buildah.", "io.k8s.", "io.openshift.", "com.redhat.", "io.containerd.",
)

# Bare label keys that are image metadata rather than ownership. Listed
# individually because they have no shared namespace to match on.
_IMAGE_METADATA_LABELS = frozenset({
    "maintainer", "dockerfile", "name", "version", "release", "vendor", "url",
    "description", "summary", "architecture", "build-date", "vcs-ref",
    "vcs-type", "distribution-scope", "license", "title",
})

# A path segment that could name a work item, e.g. `LIT-0104`, `MUC-0032`.
# A CANDIDATE only — it is never an owner unless the registry confirms it.
_WORK_ITEM_SEGMENT = re.compile(r"^[A-Z][A-Z0-9]{1,9}-\d{2,5}$")


# ---------------------------------------------------------------------------
# Evidence. Pure: inspect record in, facts out. No host, no daemon.
# ---------------------------------------------------------------------------


def evidence(record: dict) -> dict:
    """Everything the container itself says, with nothing concluded from it.

    Deliberately a separate function from `attribute()`. The separation is the
    design: evidence is what was observed, attribution is what was decided,
    and collapsing them into one function is how a conclusion becomes
    indistinguishable from the observation it was drawn from.

    `.Config.Env` is read for NAMES ONLY. The values are never touched, never
    copied into the returned record, and never logged.
    """
    config = record.get("Config") or {}
    labels = {str(k): str(v) for k, v in (config.get("Labels") or {}).items()}
    state = record.get("State") or {}
    health = state.get("Health") or {}

    # -- which label keys could be an ownership claim ------------------------
    ownership_labels: list[str] = []
    for key in labels:
        if key == OWNER_LABEL:
            continue
        lowered = key.lower()
        if any(lowered.startswith(p) for p in _ECOSYSTEM_LABEL_PREFIXES):
            continue
        if lowered in _IMAGE_METADATA_LABELS:
            continue
        if "." not in key:
            # A bare word is image metadata by convention, not a namespace
            # claim. `maintainer` is not an owner.
            continue
        ownership_labels.append(key)

    # -- environment: names only --------------------------------------------
    env_names = []
    for entry in config.get("Env") or []:
        name = str(entry).split("=", 1)[0].strip()
        if name:
            env_names.append(name)

    mounts = []
    for mount in record.get("Mounts") or []:
        mounts.append({
            "type": mount.get("Type"),
            "source": mount.get("Source"),
            "destination": mount.get("Destination"),
            "mode": mount.get("Mode"),
        })

    ports = []
    for container_port, bindings in (record.get("NetworkSettings") or {}).get("Ports", {}).items() or {}:
        if bindings:
            for binding in bindings:
                ports.append({
                    "container": container_port,
                    "host_ip": binding.get("HostIp"),
                    "host_port": binding.get("HostPort"),
                })
        else:
            ports.append({"container": container_port, "host_ip": None, "host_port": None})

    networks = sorted(((record.get("NetworkSettings") or {}).get("Networks") or {}).keys())

    return {
        "name": (record.get("Name") or "").lstrip("/") or None,
        "id": (record.get("Id") or "")[:12] or None,
        "image": config.get("Image") or record.get("Image"),
        "image_id": (record.get("Image") or "")[:19] or None,
        "created": record.get("Created"),
        "state": state.get("Status"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
        "health": health.get("Status"),
        "restart_count": record.get("RestartCount"),
        "labels": labels,
        "compose": {
            "project": labels.get("com.docker.compose.project"),
            "service": labels.get("com.docker.compose.service"),
            "working_dir": labels.get("com.docker.compose.project.working_dir"),
            "config_files": labels.get("com.docker.compose.project.config_files"),
        },
        "declared_owner": labels.get(OWNER_LABEL),
        "ownership_label_keys": sorted(ownership_labels),
        "env_var_names": sorted(env_names),
        "mounts": mounts,
        "ports": ports,
        "networks": networks,
    }


# ---------------------------------------------------------------------------
# Registry index
# ---------------------------------------------------------------------------


def build_registry_index(document: dict | None) -> dict:
    """Turn the registry document into lookup tables.

    Three ways in, because a container reaches its registration by three
    different routes and they are not interchangeable:

      by_container       the registry names this container in a probe
      by_ref             the owner ref is registered directly
      deferred/protected refs that must never be reached by an automatic path
    """
    document = document or {}
    by_container: dict[str, dict] = {}
    by_ref: dict[str, dict] = {}

    for resource in document.get("resources") or []:
        owner = resource.get("owner") or {}
        entry = {
            "resource_id": resource.get("id"),
            "kind": resource.get("kind"),
            "owner_ref": owner.get("ref"),
            "owner_type": owner.get("type"),
            "protection": (resource.get("protection") or {}).get("level"),
            "lifecycle": (resource.get("lifecycle") or {}).get("class"),
        }
        if entry["owner_ref"]:
            by_ref.setdefault(entry["owner_ref"], entry)
        for probe in resource.get("probes") or []:
            container = probe.get("container")
            if container:
                by_container[container] = entry

    protected = {
        item.get("ref"): item.get("because")
        for item in document.get("protected_refs") or []
        if item.get("ref")
    }
    deferred = {
        item.get("ref"): item.get("because")
        for item in document.get("defer_refs") or []
        if item.get("ref")
    }

    return {
        "by_container": by_container,
        "by_ref": by_ref,
        "protected_refs": protected,
        "defer_refs": deferred,
    }


def load_owner_namespaces(contract_path=None) -> dict:
    """Declared owner-label namespaces, from the attribution contract.

    Returns `{}` when nothing is declared, which is the state on this host and
    the safe default: an undeclared namespace leaves its containers
    UNKNOWN_OWNER and therefore DEFER. Declaring one asserts who owns a set of
    running containers, which changes what may be reclaimed, so it is a human
    decision rather than one this code makes by finding a plausible label.
    """
    path = Path(contract_path) if contract_path else CONTRACT
    try:
        import yaml

        document = yaml.safe_load(path.read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - an unreadable contract declares nothing
        return {}
    section = document.get("container_attribution") or {}
    return {
        str(k): str(v)
        for k, v in (section.get("owner_label_namespaces") or {}).items()
    }


def load_registry(path=None, *, contract_path=None) -> dict:
    """Read the managed-runtime registry. Absent is a valid state, not an error.

    A node with no registry has no registrations, so every container is
    UNKNOWN_OWNER and therefore DEFER. That is the correct outcome and it is
    reached by returning an empty index rather than by raising — but it is
    reported as `registry_present: false` so that "nothing is registered" is
    never mistaken for "everything is unowned".
    """
    namespaces = load_owner_namespaces(contract_path)
    if path is None:
        return {"registry_present": False,
                **build_registry_index({}), "owner_label_namespaces": namespaces}
    try:
        import yaml

        document = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - an unreadable registry is "none"
        return {"registry_present": False,
                **build_registry_index({}), "owner_label_namespaces": namespaces}
    return {"registry_present": True,
            **build_registry_index(document), "owner_label_namespaces": namespaces}


# ---------------------------------------------------------------------------
# Attribution. Pure: evidence + registry in, a decision out.
# ---------------------------------------------------------------------------


def _work_item_from_token(token: str) -> str | None:
    """`lit-0104-dev100` -> `LIT-0104`. A CANDIDATE, never an owner.

    Deliberately narrow. The second hyphen-separated part must be ALL digits,
    which was checked against the live host rather than imagined:

        lit-0104-dev100    -> LIT-0104     (registered, protected)
        lit-0101-after     -> LIT-0101     (registered, deferred)
        fjd-req1-e2e       -> no match     (`req1` is not all digits)
        openclaw           -> no match     (no second part)
        litellm-container0 -> no match     (`container0` is not all digits)

    A normalization loose enough to match everything is a guess wearing a
    rule's clothing, and `fjd-req1-e2e` is on this host specifically to catch
    a rule that does that.
    """
    parts = (token or "").split("-")
    if len(parts) >= 2 and parts[0].isalpha() and parts[1].isdigit():
        return f"{parts[0].upper()}-{parts[1]}"
    return None


def _candidate_refs(evidence_record: dict) -> list[dict]:
    """Refs this container's own evidence suggests, strongest first.

    These are CANDIDATES. Every one must still be confirmed by the registry
    before it names an owner — which is the entire difference between this
    module and reading the container name.
    """
    out: list[dict] = []
    compose = evidence_record.get("compose") or {}

    project = compose.get("project")
    if project:
        out.append({"ref": f"project:{project}", "from": "compose-project-name"})
        # The project name is also run through the work-item normalization,
        # because on this host the containers serving LIT-0103 and LIT-0101
        # declare no work item in their path at all — their working directory
        # is the shared `litellm-e2e` — and the ONLY thing connecting them to
        # a protected lane is the project name. Leaving that out reported a
        # protected work item's containers as NORMAL, which is the precise
        # failure the registry exists to prevent. The registry still decides:
        # this produces a candidate, and an unregistered one is discarded.
        normalized = _work_item_from_token(project)
        if normalized:
            out.append({
                "ref": f"work-item:{normalized}",
                "from": "compose-project-name-normalised",
            })

    working_dir = compose.get("working_dir")
    if working_dir:
        for segment in Path(working_dir).parts:
            if _WORK_ITEM_SEGMENT.match(segment):
                out.append({
                    "ref": f"work-item:{segment}",
                    "from": "compose-working-directory-segment",
                })
                break

    # An environment VARIABLE NAME may carry a work item. Only the name; the
    # value is never read. e.g. `LIT_0104_ENV_FILE` does not match, but a bare
    # `LIT-0104` would.
    for name in evidence_record.get("env_var_names") or []:
        if _WORK_ITEM_SEGMENT.match(name):
            out.append({"ref": f"work-item:{name}", "from": "env-var-name"})

    return out


def attribute(evidence_record: dict, *, registry: dict | None = None,
              roots: dict | None = None, container_index: dict | None = None) -> dict:
    """Decide who owns this container, and how much the answer is worth.

    Ordered by evidence class, strongest first. The loop stops at the first
    DECISIVE source, which is why `disposition` is a single value rather than
    a score: an owner is either proven or it is UNKNOWN_OWNER, and
    plausibility is not a provenance class.
    """
    registry = registry or {}
    roots = roots or {}
    by_container = registry.get("by_container") or {}
    by_ref = registry.get("by_ref") or {}
    protected_refs = registry.get("protected_refs") or {}
    defer_refs = registry.get("defer_refs") or {}

    name = evidence_record.get("name")
    compose = evidence_record.get("compose") or {}
    provenance: list[str] = []
    notes: list[str] = []

    owner_ref = None
    owner_type = None
    confidence = "unknown"
    decisive_by = None
    matched_entry = None

    # -- 1. the owner declared itself ---------------------------------------
    if evidence_record.get("declared_owner"):
        owner_ref = evidence_record["declared_owner"]
        owner_type = owner_ref.split(":", 1)[0] if ":" in owner_ref else None
        confidence = "high"
        provenance.append("docker-label")
        decisive_by = f"{OWNER_LABEL} label written by the owner"

    # -- 2. a label in a declared owner namespace ---------------------------
    # The KEY is the signal. The value is never read.
    if owner_ref is None:
        namespaces = (registry.get("owner_label_namespaces") or {})
        for key in evidence_record.get("ownership_label_keys") or []:
            namespace = key.rsplit(".", 1)[0]
            declared = namespaces.get(namespace)
            if declared:
                owner_ref = declared
                owner_type = declared.split(":", 1)[0] if ":" in declared else None
                confidence = "high"
                provenance.append("docker-label")
                decisive_by = (
                    f"label key {key} is in the declared owner namespace "
                    f"{namespace}; the label VALUE was not read"
                )
                break

    # -- 3. registered by container name ------------------------------------
    if owner_ref is None and name and name in by_container:
        entry = by_container[name]
        owner_ref = entry.get("owner_ref")
        owner_type = entry.get("owner_type")
        matched_entry = entry
        confidence = "high"
        provenance.append("registry-match")
        decisive_by = f"the registry names this container in a probe ({entry.get('resource_id')})"

    # -- 4. a candidate ref the registry confirms ---------------------------
    # This is the step that separates `lit-0104-dev100` (correct) from
    # `fjd-req1-e2e` (a plausible reading). Both produce a candidate; only one
    # is registered.
    if owner_ref is None:
        for candidate in _candidate_refs(evidence_record):
            ref = candidate["ref"]
            if ref in by_ref:
                owner_ref = ref
                matched_entry = by_ref[ref]
                owner_type = (matched_entry or {}).get("owner_type") or ref.split(":", 1)[0]
                confidence = "high"
                provenance.append("registry-match")
                decisive_by = f"{ref} is registered ({candidate['from']})"
                break
            if ref in protected_refs:
                owner_ref = ref
                owner_type = ref.split(":", 1)[0]
                confidence = "high"
                provenance.append("registry-match")
                decisive_by = f"{ref} is a protected ref declared by the registry ({candidate['from']})"
                break
            if ref in defer_refs:
                owner_ref = ref
                owner_type = ref.split(":", 1)[0]
                confidence = "high"
                provenance.append("registry-match")
                decisive_by = f"{ref} is a deferred ref declared by the registry ({candidate['from']})"
                break

    # -- 5/6. strong and weak evidence: a LOCATOR, never an owner -----------
    #
    # The contract puts `working-directory`, `bind-mount` and `container-name`
    # below `decisive_provenance` and says a record whose only evidence is
    # below that line "is UNKNOWN_OWNER no matter how plausible it looks".
    # So neither a path nor a project name may reach the owner field, and the
    # first version of this function let both of them.
    #
    # They are still worth recording, and dropping them would make "we saw a
    # path and declined to call it an owner" indistinguishable from "we saw
    # nothing". So they come back as a `locator`: what the evidence points AT,
    # with its own strength, kept strictly apart from what was PROVEN.
    unconfirmed: list[str] = []
    locator = None

    if owner_ref is None:
        located = _first_under_root(evidence_record, roots)
        if located:
            locator = {
                "kind": "path",
                "value": located["path"],
                "root": located["root"],
                "status": INFERRED,
                "confidence": "medium",
                "rule": (
                    f"the {located['from']} resolves under the {located['root']} "
                    f"root, but no owner registered for it matches"
                ),
            }
            provenance.append(located["from"])
            notes.append(
                f"the {located['from']} resolves under the {located['root']} root "
                f"and names no registered owner; it is recorded as a locator and "
                f"is NOT promoted to an owner or a work item"
            )

    if owner_ref is None and locator is None and compose.get("project"):
        locator = {
            "kind": "project-name",
            "value": f"project:{compose['project']}",
            # Status describes whether the locator ESTABLISHES AN OWNER, and a
            # project name does not — the label is a fact, the owner is not.
            "status": UNKNOWN,
            "confidence": "low",
            "rule": "compose project label; a name somebody typed, not an owner",
        }
        unconfirmed.append(f"project:{compose['project']}")
        provenance.append("container-name")
        notes.append(
            "only the compose project name is available; a project name is a "
            "string somebody typed and is not sufficient to name an owner, so "
            "it is recorded as an unconfirmed locator"
        )

    # -- the answer when nothing fired --------------------------------------
    unknown_owner = owner_ref is None
    if unknown_owner:
        if not provenance:
            provenance.append("container-name")
        notes.append(
            "no decisive evidence: no owner label, no registry entry, and "
            "nothing a declared rule can resolve to an owner. A name that "
            "merely looks meaningful is not evidence, so this stays "
            "UNKNOWN_OWNER."
        )
        if evidence_record.get("ownership_label_keys"):
            notes.append(
                "this container carries label(s) in an undeclared namespace "
                f"({', '.join(evidence_record['ownership_label_keys'])}). They "
                "look like ownership declarations; declaring the namespace in "
                "the contract would resolve this owner without any other change."
            )
        if name is None and evidence_record.get("id"):
            name = evidence_record["id"]

    # -- conflicts in the evidence itself -----------------------------------
    conflicts = _conflicts(evidence_record, container_index)
    if conflicts:
        notes.extend(conflicts)
        if confidence in ("high", "medium"):
            confidence = "low"

    # -- host exposure ------------------------------------------------------
    exposure = _host_exposure(evidence_record, roots)
    for item in exposure:
        notes.append(
            f"mounts {item['source']} into the container at "
            f"{item['destination']} — {item['why']}"
        )

    # -- disposition --------------------------------------------------------
    #
    # Protection comes from TWO places in the registry and both must be read.
    # `protected_refs` lists owner refs; a resource's own `protection.level`
    # applies to that specific resource and need not appear in the ref list.
    #
    # Reading only the ref list is a real defect, not a hypothetical one: it
    # shipped in the first version of this function and the live smoke test
    # caught it. `litellm-container0-litellm` is registered as
    # `litellm-baseline-litellm` with `protection: protected`, and its owner
    # `project:litellm` is NOT in `protected_refs` — so the baseline LiteLLM
    # container, the one thing on this host that must never be reclaimed,
    # reported `disposition: NORMAL`. The registry had said otherwise all
    # along and this function was not reading it.
    disposition = "NORMAL"
    because = None
    entry_protection = (matched_entry or {}).get("protection")
    if entry_protection == "protected":
        disposition = "PROTECTED"
        because = (
            f"registered as {matched_entry.get('resource_id')} with "
            f"protection: protected"
        )
    elif owner_ref and owner_ref in protected_refs:
        disposition = "PROTECTED"
        because = protected_refs[owner_ref]
    elif entry_protection == "disposable":
        disposition = "DISPOSABLE"
        because = f"registered as {matched_entry.get('resource_id')} with protection: disposable"
    elif owner_ref and owner_ref in defer_refs:
        disposition = "DEFER"
        because = defer_refs[owner_ref]
    elif unknown_owner:
        disposition = "DEFER"
        because = "unknown owner fails safe; §0 and §18 make this unreclaimable"

    return {
        "ref": f"runtime:container:{name}" if name else None,
        "container": name,
        "runtime_kind": "container",
        "state": evidence_record.get("state"),
        "health": evidence_record.get("health"),
        "owner": {
            "ref": owner_ref,
            "type": owner_type,
            "status": INFERRED if owner_ref else UNKNOWN,
        },
        "unknown_owner": unknown_owner,
        # What the evidence points at, when it does not reach an owner. Kept
        # beside `owner` rather than merged into it, because a locator that a
        # reader mistakes for an owner is the whole failure this module exists
        # to avoid.
        "locator": locator,
        "disposition": disposition,
        "disposition_because": because,
        # A container that cannot be attributed is never a reclaim candidate,
        # and this is a constant rather than a judgement made at the call site.
        "reclaim_candidate": False,
        "attribution": {
            "confidence": confidence,
            "provenance": provenance,
            "decided_by": decisive_by,
            "registry_present": bool(registry.get("registry_present")),
            # Locators that were CONSIDERED and did not reach an owner. Kept
            # because "we saw a name that looked meaningful and declined to
            # use it" is the reasoning an operator needs to review, and
            # dropping it would make the refusal indistinguishable from
            # having found nothing.
            "unconfirmed_candidates": unconfirmed,
        },
        "notes": notes,
        "evidence": {
            "name": evidence_record.get("name"),
            "image": evidence_record.get("image"),
            "created": evidence_record.get("created"),
            "finished_at": evidence_record.get("finished_at"),
            "exit_code": evidence_record.get("exit_code"),
            "restart_count": evidence_record.get("restart_count"),
            "compose": compose,
            "mounts": evidence_record.get("mounts"),
            "ports": evidence_record.get("ports"),
            "networks": evidence_record.get("networks"),
            "env_var_names": evidence_record.get("env_var_names"),
            "declared_owner": evidence_record.get("declared_owner"),
            "ownership_label_keys": evidence_record.get("ownership_label_keys"),
        },
    }


def _first_under_root(evidence_record: dict, roots: dict) -> dict | None:
    """The first path in this container's evidence that sits under a known root.

    Working directory first, then bind-mount sources, in the order the
    contract lists them. Returns both the path and WHICH piece of evidence it
    came from, because `working-directory` and `bind-mount` are different
    provenance classes and a report that blurs them cannot be argued with.
    """
    if not roots:
        return None

    candidates: list[tuple[str, str]] = []
    working_dir = (evidence_record.get("compose") or {}).get("working_dir")
    if working_dir:
        candidates.append((working_dir, "working-directory"))
    for mount in evidence_record.get("mounts") or []:
        if mount.get("type") == "bind" and mount.get("source"):
            candidates.append((mount["source"], "bind-mount"))

    for path, source in candidates:
        # The MOST SPECIFIC root wins, not the first one in iteration order.
        # These roots nest: `/…/workspace/src/x` is under both `data` and
        # `workspace`, and reporting it as `data` throws away the only part
        # that says which area of the node it belongs to. Same rule as
        # `unit_of_cgroup`, which returns the innermost unit for the same
        # reason — a coarse answer where a precise one was available is not a
        # conservative answer, it is a wrong one.
        best_name, best_root = None, None
        for root_name, root_path in (roots or {}).items():
            if not root_path:
                continue
            root = str(root_path).rstrip("/")
            # STRICTLY below the root. A path equal to the root names the root,
            # not a project inside it, and accepting it produced a real defect
            # on the live host: `xpra-gui-hwz` bind-mounts the whole home
            # directory, so `/home/hwz` == ROOTS['home'] and the container was
            # reported as owned by `path:/home/hwz` with medium confidence.
            # A container that mounts a directory wholesale has told us
            # nothing about whose work it is.
            if path.startswith(root + "/") and (best_root is None or len(root) > len(best_root)):
                best_name, best_root = root_name, root
        if best_root:
            return {"path": path, "root": best_name, "from": source}
    return None


# Paths whose presence inside a container is worth reporting regardless of who
# owns it: a root filesystem, a root itself, or the Docker control socket — a
# mount that reaches the node rather than a project.
_HOST_EXPOSURE_TARGETS = ("/var/run/docker.sock", "/run/docker.sock")


def _host_exposure(evidence_record: dict, roots: dict) -> list[dict]:
    """Bind mounts that expose the node itself, so a blast radius is recorded.

    This is a measurement about the CONTAINER, not a judgement about its owner,
    and it is reported even when the owner is known. `xpra-gui-hwz` mounts `/`
    to `/host`; a reclaim recommendation for it that did not say so would be
    asking an operator to consent to a consequence nobody described, which
    §"recommendations" explicitly forbids.
    """
    root_paths = {str(v).rstrip("/") for v in (roots or {}).values() if v}
    found: list[dict] = []
    for mount in evidence_record.get("mounts") or []:
        if mount.get("type") != "bind":
            continue
        source = str(mount.get("source") or "")
        if not source:
            continue
        if source in _HOST_EXPOSURE_TARGETS:
            found.append({
                "source": source,
                "destination": mount.get("destination"),
                "why": "this is the Docker control socket; the container can control the host daemon",
            })
        elif source == "/":
            found.append({
                "source": source,
                "destination": mount.get("destination"),
                "why": "the entire host filesystem is mounted into the container",
            })
        elif source.rstrip("/") in root_paths:
            found.append({
                "source": source,
                "destination": mount.get("destination"),
                "why": "a whole ServerOS root is mounted into the container, so it is not evidence of any one project",
            })
    return found


def _conflicts(evidence_record: dict, container_index: dict | None) -> list[str]:
    """Disagreements among containers that claim to be the same thing.

    Found on the live host: the `openclaw` compose project has five containers
    and two different `working_dir` values. Each container read on its own
    looks fine. Read together, one of the two is wrong and nothing says which,
    so the honest output is to say so rather than to pick one.

    This is a fact about the EVIDENCE, not about the container — it is
    reported as a note and it downgrades confidence, it does not by itself
    withhold an owner.
    """
    notes: list[str] = []
    if not container_index:
        return notes
    compose = evidence_record.get("compose") or {}
    project = compose.get("project")
    working_dir = compose.get("working_dir")
    if not project or not working_dir:
        return notes

    siblings = container_index.get(project) or {}
    others = {d for n, d in siblings.items()
              if n != evidence_record.get("name") and d and d != working_dir}
    if others:
        notes.append(
            f"compose project {project!r} declares more than one working "
            f"directory across its containers ({len(others) + 1} distinct "
            f"values); path evidence from this project is therefore not "
            f"reliable and the attribution is downgraded"
        )
    return notes


# ---------------------------------------------------------------------------
# Host access
# ---------------------------------------------------------------------------


def inspect_all(host) -> list[dict]:
    """Every container on this host, via `docker ps -a` then `docker inspect`.

    One inspect per container rather than `docker inspect $(docker ps -aq)`,
    because the bulk form returns a bare list with no names attached when a
    container disappears mid-call, and matching positionally is how a report
    attributes one container's evidence to another.
    """
    if not hasattr(host, "docker_ps"):
        raise DockerUnavailable("host cannot list containers")
    try:
        listing = host.docker_ps(all_=True)
    except Exception as exc:  # noqa: BLE001 - any failure here is "cannot ask"
        raise DockerUnavailable(str(exc)) from exc

    records = []
    for row in listing:
        name = row.get("Names") or row.get("ID")
        if not name:
            continue
        try:
            records.append(host.docker_inspect(name))
        except Exception:  # noqa: BLE001 - one vanished container is not fatal
            continue
    return records


def attribute_all(host, *, registry: dict | None = None, roots: dict | None = None) -> dict:
    """Attribute every container on the host, plus a coverage statement."""
    registry = registry or load_registry()
    records = inspect_all(host)
    evidences = [evidence(r) for r in records]

    # Built from the same measure so that the conflict check compares this
    # container against what is actually on the machine, not against a
    # remembered list.
    container_index: dict[str, dict[str, str]] = {}
    for ev in evidences:
        project = (ev.get("compose") or {}).get("project")
        if project and ev.get("name"):
            container_index.setdefault(project, {})[ev["name"]] = (
                ev.get("compose") or {}
            ).get("working_dir")

    results = [
        attribute(ev, registry=registry, roots=roots, container_index=container_index)
        for ev in evidences
    ]

    by_disposition: dict[str, int] = {}
    for item in results:
        by_disposition[item["disposition"]] = by_disposition.get(item["disposition"], 0) + 1

    return {
        "count": len(results),
        "unknown_owner_count": sum(1 for r in results if r["unknown_owner"]),
        "by_disposition": by_disposition,
        "registry_present": bool(registry.get("registry_present")),
        "containers": sorted(results, key=lambda r: (r["container"] or "")),
        "note": (
            "reclaim_candidate is false for every container here. Attribution "
            "is not authorization: §18 forbids this layer from removing, "
            "pruning or stopping anything, and a container attributed to a "
            "protected ref would be refused by the registry even if it were."
        ),
    }
