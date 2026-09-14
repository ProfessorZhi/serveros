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

  * Some of those nine are unattributed for a reason sharper than "we cannot
    tell": `new-api` and `new-api-postgres` mount `/home/szj/gpt-img-2/data`,
    which is another user's tree. They get `foreign_actor: true` beside an
    unchanged `unknown_owner: true` — evidence about who does NOT own a
    container is not an owner, and is not the same as having no evidence.

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


# The `serveros.*` label namespace, declared in one place. A container MAY
# carry any subset; none of them is required, and a missing one is not a
# defect — it means the container predates the convention or was created by
# something that does not know it. The reader must therefore treat every one
# of these as optional, which is why they are a table and not a schema.
#
# This label set is proposed, not observed. On this host there are currently
# ZERO `serveros.*` labels in the wild (surveyed 2026-09-14 across all 38
# containers); the existing conventions are `com.docker.compose.*`,
# `org.opencontainers.image.*`, and the product-owned `litellm.*`. The point
# of declaring the namespace now is that the day one appears it is decisive
# without a code change — the same argument that put OWNER_LABEL here.
RUNTIME_LABEL_NAMESPACE = "serveros"

# label key                     what it is, and what it is worth
RUNTIME_LABELS = {
    # The canonical identity, written by whoever created the container. When
    # present this is authoritative and the Docker name is decoration.
    f"{RUNTIME_LABEL_NAMESPACE}.runtime_ref": "runtime_ref",
    # Which device the runtime was authored for. Read-only here: this observer
    # reports what the label says and does not verify it against the host it
    # is actually running on. Those two differing is a drift finding, and
    # inventing it silently would hide it.
    f"{RUNTIME_LABEL_NAMESPACE}.device_ref": "device_ref",
    f"{RUNTIME_LABEL_NAMESPACE}.owner_ref": "owner_ref",
    f"{RUNTIME_LABEL_NAMESPACE}.owner_type": "owner_type",
    f"{RUNTIME_LABEL_NAMESPACE}.scope": "scope",
    # "true"/"false" as a string, because a label value is always a string.
    # Anything else is reported as an unparseable value rather than coerced;
    # `bool("false")` is True and that is a protection bypass.
    f"{RUNTIME_LABEL_NAMESPACE}.protected": "protected",
    f"{RUNTIME_LABEL_NAMESPACE}.work_item": "work_item",
    f"{RUNTIME_LABEL_NAMESPACE}.run_id": "run_id",
    f"{RUNTIME_LABEL_NAMESPACE}.session_ref": "session_ref",
    f"{RUNTIME_LABEL_NAMESPACE}.created_by": "created_by",
}

# The single label that says a container is under ServerOS management at all.
MANAGED_LABEL = f"{RUNTIME_LABEL_NAMESPACE}.managed"

# Booleans are parsed from an explicit pair. Everything else — including the
# plausible-looking `yes`, `1`, `Y` — is None, meaning "the label was there
# and its value could not be read as a boolean". A protection flag that
# silently reads as False is worse than an absent one.
_TRUE_VALUES = frozenset({"true", "yes"})
_FALSE_VALUES = frozenset({"false", "no"})


def _parse_bool(value):
    """True / False / None. None means "present but not parseable as a bool"."""
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return None


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
        if key in RUNTIME_LABELS or key == MANAGED_LABEL:
            # A key this observer has defined semantics for is not an opaque
            # ownership claim, and it must not reach rung 2. Rung 2 reads a
            # namespace and trusts the CONTRACT's answer for it, so leaving
            # `serveros.protected` in this list would let one declaration of
            # the `serveros` namespace assign every labelled container on the
            # host to the same owner — from a label that says nothing about
            # ownership at all.
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
            # A volume's Name is how the daemon refers to it — a bind mount's
            # Source is a path. §8 asks for `resource_refs`, and a volume is
            # the one Docker resource a container can hold, so the name has to
            # survive into evidence or the ref cannot be built from it.
            "name": mount.get("Name"),
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
        # The declarable identity namespace, read by NAME. A key that is
        # absent is absent from this dict — it is not present with a null
        # value, so a caller cannot mistake "not declared" for "declared
        # empty". `managed` is kept beside the table because False and
        # "not declared" are different claims and the reader needs both.
        "runtime_labels": {
            field: labels[key]
            for key, field in RUNTIME_LABELS.items() if key in labels
        },
        "managed_label": (
            _parse_bool(labels[MANAGED_LABEL]) if MANAGED_LABEL in labels else None
        ),
        "managed_label_raw": labels.get(MANAGED_LABEL),
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
      by_anchor          the registry declares an identity a rename cannot move
      by_ref             the owner ref is registered directly
      deferred/protected refs that must never be reached by an automatic path

    `by_anchor` is empty until a probe declares one. That is the current state
    and it is not a bug: the field exists so that a registry CAN pin a
    container by something other than its name, and adding a key to one probe
    is a data change rather than a code change.
    """
    document = document or {}
    by_container: dict[str, dict] = {}
    by_anchor: dict[str, dict] = {}
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
            # A probe may pin the container by an anchor instead of, or as
            # well as, a name. Both forms are accepted because a registry
            # author writing `runtime_ref: compose:p/s` is stating the
            # identity directly and should not have to spell the prefix.
            anchor = probe.get("anchor") or probe.get("runtime_ref")
            if anchor:
                ref = str(anchor)
                if not ref.startswith("runtime:"):
                    ref = f"runtime:container:{ref}"
                by_anchor[ref] = entry

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
        "by_anchor": by_anchor,
        # §8's `device_ref`. The registry already names the device it
        # describes, and it is read rather than re-derived: this observer IS
        # on that device, and a second way to compute its identity would be a
        # second answer waiting to disagree with the first.
        "device_ref": document.get("device_ref"),
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


def load_recognised_namespaces(contract_path=None) -> dict:
    """Label namespaces this host has an explicit recorded decision about.

    Not the same set as `owner_label_namespaces`, and the difference is the
    point. A DECLARED namespace resolves its containers to an owner. A
    RECOGNISED one is merely known: the contract records it, decides nothing,
    and says so in a note. `com.multiuserclaw` and `litellm` are both in that
    second list today.

    §11 needs the distinction because "labelled by a namespace that is not
    ours" is only evidence of a foreign system when the namespace is not one
    this host has already accounted for. Measured on the live inventory
    2026-09-14: reading any dotted non-ecosystem key as foreign classified 17
    of 37 containers EXTERNAL, and 10 of those 17 were OUR OWN — four
    `litellm.*` build-provenance containers and two `hwz.p18`. A cleanup pass
    that trusts that reading would treat our own test containers as somebody
    else's.

    Reads the keys from `owner_label_namespaces` and the `namespace` field
    from each `undeclared_owner_namespaces` entry, which is the form the
    contract already uses. An unreadable contract returns `{}` — and an empty
    set is the STRICTER answer here: with nothing recognised, more containers
    are called EXTERNAL, which is the branch that never reclaims anything.
    """
    path = Path(contract_path) if contract_path else CONTRACT
    try:
        import yaml

        document = yaml.safe_load(path.read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - an unreadable contract recognises nothing
        return {}
    section = document.get("container_attribution") or {}

    recognised: dict[str, str] = {}
    for key in (section.get("owner_label_namespaces") or {}):
        recognised[str(key)] = "declared as an owner namespace"
    for entry in section.get("undeclared_owner_namespaces") or []:
        namespace = (entry or {}).get("namespace")
        if namespace:
            recognised[str(namespace)] = "recorded and deliberately undeclared"
    runtime_namespace = (section.get("declared_runtime_namespace") or {}).get("namespace")
    if runtime_namespace:
        recognised[str(runtime_namespace)] = "the ServerOS runtime namespace"
    return recognised


def load_registry(path=None, *, contract_path=None) -> dict:
    """Read the managed-runtime registry. Absent is a valid state, not an error.

    A node with no registry has no registrations, so every container is
    UNKNOWN_OWNER and therefore DEFER. That is the correct outcome and it is
    reached by returning an empty index rather than by raising — but it is
    reported as `registry_present: false` so that "nothing is registered" is
    never mistaken for "everything is unowned".
    """
    namespaces = load_owner_namespaces(contract_path)
    recognised = load_recognised_namespaces(contract_path)
    if path is None:
        return {"registry_present": False,
                **build_registry_index({}), "owner_label_namespaces": namespaces,
                "recognised_namespaces": recognised}
    try:
        import yaml

        document = yaml.safe_load(Path(path).read_text()) or {}
    except (OSError, Exception):  # noqa: BLE001 - an unreadable registry is "none"
        return {"registry_present": False,
                **build_registry_index({}), "owner_label_namespaces": namespaces,
                "recognised_namespaces": recognised}
    return {"registry_present": True,
            **build_registry_index(document), "owner_label_namespaces": namespaces,
            "recognised_namespaces": recognised}


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


def stable_identity(evidence_record: dict) -> dict:
    """The container's identity, chosen so that a rename does not change it.

    §9: the canonical identity is `runtime_ref` and the Docker name is for
    humans. The previous version of this module built the ref as
    `runtime:container:<name>`, which made the NAME the identity — so a
    rename silently produced a different resource, and every downstream
    record that had stored the old ref (evidence, an anomaly subject, a
    reader's notes) pointed at something that no longer existed.

    Four anchors, strongest first, each reported so a reader can see which
    one was used. The anchor is not a confidence: a compose anchor and a
    container-id anchor are both stable across a rename, and they are ranked
    because a compose anchor additionally survives a recreate.

      declared-label   a `serveros.runtime_ref` label. The owner wrote it.
      compose          `com.docker.compose.project` + `.service`.
      container-id     the container's own id. Stable across a rename,
                       changes on recreate.
      name             nothing else was available. NOT stable, and the
                       result says so in a field rather than only in prose.

    `anchors` is the full set this container can be recognised by, strongest
    first, INCLUDING the name when a name exists. It is what a caller needs
    to re-key a record after a rename, and it is why the name is not simply
    discarded: discarding it would make a rename undetectable rather than
    survivable.
    """
    compose = evidence_record.get("compose") or {}
    declared = (evidence_record.get("runtime_labels") or {}).get("runtime_ref")
    container_id = evidence_record.get("id")
    name = evidence_record.get("name")

    anchors: list[dict] = []

    def _add(anchor: str, value, *, stable: bool, because: str):
        if value:
            anchors.append({
                "anchor": anchor,
                "runtime_ref": f"runtime:container:{value}",
                "stable_across_rename": stable,
                "because": because,
            })

    if declared:
        # A declared ref may already carry its own namespace. `runtime:container:x`
        # and a bare `x` are both accepted, but a declared ref naming a
        # DIFFERENT kind is refused rather than rewritten: `runtime:volume:v1`
        # is not a container, and silently re-prefixing it would create an
        # identity that no creator ever wrote.
        if declared.startswith("runtime:container:"):
            ref = declared
        elif ":" not in declared:
            ref = f"runtime:container:{declared}"
        else:
            ref = None
        if ref:
            anchors.append({
                "anchor": "declared-label",
                "runtime_ref": ref,
                "stable_across_rename": True,
                "because": f"{RUNTIME_LABEL_NAMESPACE}.runtime_ref was written by the owner",
            })

    project, service = compose.get("project"), compose.get("service")
    if project and service:
        _add("compose", f"compose:{project}/{service}", stable=True, because=(
            "the compose project and service labels identify the workload "
            "independently of the container's name"))

    _add("container-id", container_id, stable=True, because=(
        "the container's own id is unaffected by a rename; it changes when "
        "the container is recreated"))

    _add("name", name, stable=False, because=(
        "the Docker name is the only anchor available; renaming this "
        "container WOULD change its identity"))

    if not anchors:
        return {
            "runtime_ref": None,
            "anchor": None,
            "stable_across_rename": False,
            "anchors": [],
            "display_name": None,
            "why": "the container reported neither an id nor a name, so it has "
                   "no identity to record",
        }

    primary = anchors[0]
    return {
        "runtime_ref": primary["runtime_ref"],
        "anchor": primary["anchor"],
        "stable_across_rename": primary["stable_across_rename"],
        # Every ref this container answers to, primary first, each carrying
        # its own reason. Not a set of guesses: each entry is a measurement
        # of something the container actually said.
        "anchors": anchors,
        # The Docker name, kept because §9 makes it the human-readable label
        # rather than the identity. A reader showing a ref to a person should
        # show this beside it.
        "display_name": name,
        "why": primary["because"],
    }


def _protected_state(evidence_record: dict, matched_entry) -> bool | None:
    """§8's `protected`. True / False / None, never a coercion.

    Two sources can say a runtime is protected and they are read together, not
    in a ladder: the container's own `serveros.protected` label, and the
    protection level on the registration that matched it. A container that
    declares itself protected IS protected even if nothing registered it, and
    a registered protected resource is protected even if the container says
    nothing — which is the §11 defect that once reported the baseline LiteLLM
    container as NORMAL.

    None means "neither source declared anything", and it is NOT False: an
    undeclared protection reported as unprotected is a protection bypass
    performed by a default value.
    """
    if (matched_entry or {}).get("protection") == "protected":
        return True
    declared, _ = _declared_protection(evidence_record)
    return declared


def runtime_record(*, evidence_record: dict, identity: dict, owner_ref, owner_type,
                   protected_state, registry_device_ref, observed_at) -> dict:
    """§8. The runtime ownership model, one field per field.

    Every field in the brief's list is present. The ones this host cannot
    measure are `None` AND named in `unknowns` with a reason, so that "we did
    not observe this" and "this is null" are different readings of the output
    — which is the same rule the rest of this module follows for containers
    and daemons, applied to a schema.

    The two that are easiest to get wrong by inventing them are
    `desired_state` and `retention`, and both are deliberately UNKNOWN:

      desired_state   nothing on this host declares what state a container
                      SHOULD be in. Defaulting it to "running" would make
                      every intentionally-stopped container a §15
                      DESIRED_ACTUAL_MISMATCH — an anomaly generator built
                      out of a missing field. §15 needs a real source for
                      desired state before it can mean anything, and this
                      round does not have one.

      retention       likewise. A retention policy is a decision a human
                      makes about a class of resources; there is no per-
                      container declaration to read, and guessing "keep" for
                      everything would make the field decorative.
    """
    runtime_labels = evidence_record.get("runtime_labels") or {}
    unknowns: dict[str, str] = {}

    declared_device = runtime_labels.get("device_ref")
    if declared_device:
        device_ref = declared_device
        if registry_device_ref and declared_device != registry_device_ref:
            # Not corrected and not resolved: this observer is running on the
            # device the registry names, and the container says it was made
            # for a different one. That difference is a drift finding, and
            # quietly replacing either value with the other would delete it.
            unknowns["device_ref_drift"] = (
                f"the container declares {declared_device} and this node's "
                f"registry names {registry_device_ref}; both are reported and "
                f"neither is treated as authoritative over the other"
            )
    else:
        device_ref = registry_device_ref
        if not device_ref:
            unknowns["device_ref"] = (
                "the container declares no device and no registry on this "
                "node names one"
            )

    scope = runtime_labels.get("scope")
    if not scope:
        # Derived from the owner's kind, which is a reading of an observed
        # value rather than a guess — `work-item:LIT-0104` names a work item,
        # so the scope is the work item.
        scope = {
            "work-item": "work-item",
            "project": "project",
            "path": None,
            "person": None,
        }.get(owner_type)
        if scope is None:
            unknowns["scope"] = (
                "no scope was declared and the owner's kind does not imply one"
            )

    if protected_state is None:
        unknowns["protected"] = (
            "neither the container nor the registry declares a protection "
            "level; it is NOT reported as unprotected"
        )

    if not runtime_labels.get("created_by"):
        unknowns["created_by"] = (
            "docker inspect records when a container was created and never "
            "who or what created it; only a declared label could say so"
        )
    if "retention" not in runtime_labels:
        unknowns["retention"] = (
            "no retention is declared per container, and this round has no "
            "policy source to read one from"
        )
    unknowns["desired_state"] = (
        "nothing on this host declares the state this container should be in. "
        "It is reported as unknown rather than defaulted to running, because "
        "a default here would make every stopped container an anomaly"
    )

    return {
        "runtime_ref": identity.get("runtime_ref"),
        "device_ref": device_ref,
        "owner_ref": owner_ref,
        "owner_type": owner_type,
        "scope": scope,
        "desired_state": None,
        "actual_state": evidence_record.get("state"),
        "protected": protected_state,
        "retention": runtime_labels.get("retention"),
        "created_by": runtime_labels.get("created_by"),
        "created_at": evidence_record.get("created"),
        # The tick that produced this row, not a value read off the container.
        # §26 wants the observation time on the observation.
        "last_observed_at": observed_at,
        "provider": "docker",
        "resource_refs": _resource_refs(evidence_record),
        "unknowns": unknowns,
    }


def _resource_refs(evidence_record: dict) -> list:
    """Docker resources this container references. §8's `resource_refs`.

    Only what the container itself names — its volumes and its networks.
    Images are deliberately absent: the image is what the container IS, not
    something it holds, and listing it here would make an image look like a
    resource a cleanup pass could reach through the container.
    """
    refs: list[dict] = []
    for mount in evidence_record.get("mounts") or []:
        if mount.get("type") == "volume" and mount.get("name"):
            refs.append({
                "ref": f"volume:{mount['name']}",
                "kind": "volume",
                "because": f"mounted at {mount.get('destination')}",
            })
    for network in evidence_record.get("networks") or []:
        refs.append({
            "ref": f"network:{network}",
            "kind": "network",
            "because": "the container is attached to it",
        })
    return refs


def management_class(*, evidence_record: dict, owner_ref, registry_resource_id,
                     foreign_actor: bool, registry_present: bool,
                     recognised_namespaces=None) -> dict:
    """§11. MANAGED / EXTERNAL / UNKNOWN, with the confidence in the answer.

    Read-only. §11 forbids modifying a container's labels, and nothing here
    writes anything: this classifies what is already running and reports how
    strongly the host's own evidence supports the classification.

    The three classes are NOT a ranking of "how ours" a container is, and one
    of them being MANaged is not a safety claim. It answers exactly one
    question — is this container under ServerOS management — and a MANAGED
    container whose owner is unknown is still UNKNOWN_OWNER and still DEFER.
    Several of our own test containers land there. Merging the two axes would
    make the management label look like an ownership proof, which is the
    substitution this whole module exists to refuse.

    Confidence is about the EVIDENCE, not about the container:

      AUTHORITATIVE  the container declared its own status, or the system of
                     record names it. Both are statements rather than
                     inferences, and both can be wrong about the world — that
                     is what the class is for.
      INFERRED       read from a label namespace or a path. Consistent with
                     the class and not a statement of it.
      UNKNOWN        nothing was found. The class is UNKNOWN, which is a
                     result and not a failure to produce one.
    """
    runtime_labels = evidence_record.get("runtime_labels") or {}
    managed_label = evidence_record.get("managed_label")
    managed_raw = evidence_record.get("managed_label_raw")
    ownership_keys = evidence_record.get("ownership_label_keys") or []

    protected, protected_why = _declared_protection(evidence_record)

    base = {
        "declared": {
            "managed_label_raw": managed_raw,
            "runtime_labels": sorted(runtime_labels),
            "protected": protected,
            "protected_because": protected_why,
        },
        "note": (
            "MANAGED is not a safety claim. A MANAGED container with an "
            "unknown owner is still UNKNOWN_OWNER and still DEFER."
        ),
    }

    # -- MANAGED, authoritatively -------------------------------------------
    if managed_label is True:
        return {**base, "class": "MANAGED", "confidence": "AUTHORITATIVE",
                "because": f"{MANAGED_LABEL} was written by the creator",
                "decided_by": "docker-label"}
    if registry_resource_id:
        return {**base, "class": "MANAGED", "confidence": "AUTHORITATIVE",
                "because": (f"the managed-runtime registry names this "
                            f"container ({registry_resource_id})"),
                "decided_by": "registry-match"}

    # -- EXTERNAL, authoritatively ------------------------------------------
    # A container that says it is NOT managed has told us more than one that
    # says nothing, and the two must not collapse into the same UNKNOWN.
    if managed_label is False:
        return {**base, "class": "EXTERNAL", "confidence": "AUTHORITATIVE",
                "because": f"{MANAGED_LABEL} was written as {managed_raw!r}",
                "decided_by": "docker-label"}

    # -- MANAGED, inferred ---------------------------------------------------
    # Carries an identity from the ServerOS namespace without the managed flag.
    # The namespace is ours by construction, so a container using it is
    # ours by inference — and inference is what the confidence field says.
    if runtime_labels:
        return {**base, "class": "MANAGED", "confidence": "INFERRED",
                "because": (f"carries {RUNTIME_LABEL_NAMESPACE}.* identity "
                            f"labels but not {MANAGED_LABEL}"),
                "decided_by": "docker-label"}

    # -- EXTERNAL, inferred --------------------------------------------------
    if foreign_actor:
        return {**base, "class": "EXTERNAL", "confidence": "INFERRED",
                "because": "mounts another user's home directory",
                "decided_by": "bind-mount"}
    if ownership_keys:
        # Only an UNRECOGNISED namespace is evidence of another system. The
        # contract already records the namespaces this host has accounted for,
        # and a container labelled with one of those is not a stranger — it is
        # a container this host has already written a decision about. Getting
        # this wrong is not a rounding error: reading every dotted key as
        # foreign classified our own `litellm.*` build-provenance containers as
        # EXTERNAL, which is one step from "somebody else's, therefore not our
        # problem".
        recognised = recognised_namespaces or {}
        unknown_keys = [
            key for key in ownership_keys if key.rsplit(".", 1)[0] not in recognised
        ]
        if unknown_keys:
            return {**base, "class": "EXTERNAL", "confidence": "INFERRED",
                    "because": (f"labelled by a namespace this host does not "
                                f"recognise ({', '.join(unknown_keys[:3])})"),
                    "decided_by": "docker-label"}
        # Labelled ONLY by namespaces the contract records. That is not the
        # same as ours, and the difference is the whole reason this branch
        # falls through instead of returning MANAGED: `litellm.*` is our build
        # provenance, `com.multiuserclaw.hermes-runtime-config` is another
        # product's, and `hwz.p18` has no documented meaning at all. The
        # contract KNOWS about all three and deliberately resolves none of
        # them — so the management class is genuinely undetermined, and
        # recording it as MANAGED would import the same assumption in the
        # opposite direction.
        owned_keys = [k for k in ownership_keys]
        return {**base, "class": "UNKNOWN", "confidence": "UNKNOWN",
                "because": (f"labelled only by namespaces this host has "
                            f"recorded and deliberately not resolved "
                            f"({', '.join(sorted({k.rsplit('.', 1)[0] for k in owned_keys})[:3])})"),
                "decided_by": None}

    # -- UNKNOWN -------------------------------------------------------------
    # An absent registry makes every container UNKNOWN rather than EXTERNAL,
    # because with no system of record there is nothing to be external TO.
    if not registry_present:
        return {**base, "class": "UNKNOWN", "confidence": "UNKNOWN",
                "because": ("the managed-runtime registry is absent, so there "
                            "is no management state to classify against"),
                "decided_by": None}
    return {**base, "class": "UNKNOWN", "confidence": "UNKNOWN",
            "because": ("the container declares no management state and "
                        "carries no label namespace this host recognises"),
            "decided_by": None}


def _declared_protection(evidence_record: dict) -> tuple:
    """(True | False | None, why). None means "declared but unreadable".

    Never `bool(value)`. A protection flag whose value was written as
    something other than a boolean must not read as False: `bool("false")`
    is True and `bool("nope")` is also True, so the naive coercion is not
    merely wrong, it is wrong in both directions depending on the string.
    An unreadable value is reported as unreadable and relaxes nothing.
    """
    runtime_labels = evidence_record.get("runtime_labels") or {}
    if "protected" not in runtime_labels:
        return None, "no protection was declared on the container"
    parsed = _parse_bool(runtime_labels["protected"])
    if parsed is None:
        return None, (
            f"{RUNTIME_LABEL_NAMESPACE}.protected was declared as "
            f"{runtime_labels['protected']!r}, which is not a boolean; it is "
            f"NOT read as unprotected"
        )
    return parsed, f"{RUNTIME_LABEL_NAMESPACE}.protected was declared"


def attribute(evidence_record: dict, *, registry: dict | None = None,
              roots: dict | None = None, container_index: dict | None = None,
              observed_at: str | None = None) -> dict:
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
    runtime_labels = evidence_record.get("runtime_labels") or {}
    provenance: list[str] = []
    notes: list[str] = []

    owner_ref = None
    owner_type = None
    confidence = "unknown"
    decisive_by = None
    matched_entry = None

    # -- 1. the owner declared itself ---------------------------------------
    # `serveros.owner` is the original key. `serveros.owner_ref` is the same
    # claim under the §9 namespace, and `serveros.owner_type` may qualify it.
    # They are one rung, not two: a container carrying both is not better
    # attributed, it is the same owner written twice, and splitting them
    # would let the second one silently win.
    declared_owner = evidence_record.get("declared_owner") or runtime_labels.get("owner_ref")
    if declared_owner:
        owner_ref = declared_owner
        owner_type = (
            runtime_labels.get("owner_type")
            or (owner_ref.split(":", 1)[0] if ":" in owner_ref else None)
        )
        confidence = "high"
        provenance.append("docker-label")
        if evidence_record.get("declared_owner"):
            decisive_by = f"{OWNER_LABEL} label written by the owner"
        else:
            decisive_by = (
                f"{RUNTIME_LABEL_NAMESPACE}.owner_ref label written by the owner"
            )

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

    # -- 3b. registered by a STABLE ANCHOR -----------------------------------
    #
    # §10 requires that renaming a container does not break its attribution.
    # Rung 3 is a name lookup and a rename defeats it, so this rung repeats
    # the lookup using the anchors that a rename cannot move — and it is
    # placed AFTER rung 3 so that adding it cannot change the answer for any
    # container that was already matched by name.
    #
    # The registry has to have declared the anchor for this to fire: a probe
    # carrying `anchor: compose:p/s` or a `runtime_ref:`. Nothing on this host
    # declares one yet, which is why the miss path below is the interesting
    # one. What this rung changes today is not what an operator sees; it is
    # that a rename stops being UNREVIEWABLE.
    by_anchor = registry.get("by_anchor") or {}
    identity = stable_identity(evidence_record)
    anchors_tried: list[str] = []
    if owner_ref is None:
        for entry_anchor in identity["anchors"]:
            if entry_anchor["anchor"] == "name":
                continue
            anchors_tried.append(entry_anchor["runtime_ref"])
            entry = by_anchor.get(entry_anchor["runtime_ref"])
            if entry is None:
                continue
            owner_ref = entry.get("owner_ref")
            owner_type = entry.get("owner_type")
            matched_entry = entry
            confidence = "high"
            provenance.append("registry-match")
            decisive_by = (
                f"the registry declares {entry_anchor['anchor']} anchor "
                f"{entry_anchor['runtime_ref']} "
                f"({entry.get('resource_id')}) — matched without the "
                f"container's name"
            )
            break

    # A container the registry does not mention under ANY identity it carries
    # is what a rename looks like from the outside. The `tried` list is
    # assembled here, beside the loop that produced it, and only REPORTED
    # later — recording it now would claim a miss for a container that rung
    # 4 or below goes on to attribute.

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

    # -- whose directory is this, anyway -------------------------------------
    #
    # A container whose working directory or bind-mount source sits under
    # ANOTHER user's home is not "unknown". Something is known: whatever it
    # is, it is not this node's to classify.
    #
    # It is reported in its own field and it changes NOTHING else. The owner
    # stays None and `unknown_owner` stays true, because this evidence says
    # who the owner is NOT, and an owner field that accepts "probably
    # somebody else's" is the failure the whole ladder exists to prevent.
    #
    # The count is kept out of the headline on purpose. Moving containers
    # from UNKNOWN_OWNER to FOREIGN_ACTOR would make a report that knows
    # exactly as much as before look like one that had learned something.
    foreign_paths = [
        {"path": p, "from": src}
        for p, src in _paths_in(evidence_record)
        if attribution.foreign_home(p)
    ]
    foreign_actor = bool(foreign_paths)
    if foreign_actor:
        for item in foreign_paths:
            notes.append(
                f"the {item['from']} {item['path']} is under a different "
                f"user's home; that is evidence about who does NOT own this "
                f"container, not about who does"
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

    registry_anchors_tried = None
    if owner_ref is None and identity["runtime_ref"]:
        registry_anchors_tried = {
            "tried": [identity["runtime_ref"]] + anchors_tried,
            "matched": False,
            "why": (
                "no registered resource names this container, and none "
                "declares an anchor this container carries"
                if by_container or by_anchor else
                "the registry declares no container probes at all"
            ),
            "note": (
                "a container renamed since the registry was written lands "
                "here. The registry naming the OLD name is the signature of a "
                "rename rather than of an unregistered container — DEFER "
                "either way, but only one of them is a registry that needs a "
                "human to update it."
            ),
        }

    return {
        # §9: the canonical identity. Name-derived only when nothing more
        # stable existed, and `identity.stable_across_rename` says which.
        "ref": identity["runtime_ref"],
        # §9: the human-readable label, NOT the identity. Kept under the key
        # existing readers already use, because a rename should change what
        # is DISPLAYED without changing what is REFERRED TO.
        "container": name,
        "identity": identity,
        "registry_anchors_tried": registry_anchors_tried,
        # §11. Read-only classification of the existing inventory. Beside
        # `owner` and never merged into it: a container can be MANAGED with an
        # unknown owner, and this field is not a claim that it cannot.
        "management": management_class(
            evidence_record=evidence_record,
            owner_ref=owner_ref,
            registry_resource_id=(matched_entry or {}).get("resource_id"),
            foreign_actor=foreign_actor,
            registry_present=bool(registry.get("registry_present")),
            recognised_namespaces=registry.get("recognised_namespaces") or {},
        ),
        # §8. The ownership model, one field per field, including the ones
        # this host cannot measure — those are None AND named in `unknowns`.
        "runtime": runtime_record(
            evidence_record=evidence_record,
            identity=identity,
            owner_ref=owner_ref,
            owner_type=owner_type,
            protected_state=_protected_state(evidence_record, matched_entry),
            registry_device_ref=registry.get("device_ref"),
            observed_at=observed_at,
        ),
        "runtime_kind": "container",
        "state": evidence_record.get("state"),
        "health": evidence_record.get("health"),
        "owner": {
            "ref": owner_ref,
            "type": owner_type,
            "status": INFERRED if owner_ref else UNKNOWN,
        },
        "unknown_owner": unknown_owner,
        # Positive evidence that this is somebody ELSE's resource. It sits
        # beside `unknown_owner` and does not replace it: this says the
        # evidence identifies a non-owner, and a non-owner is still not an
        # owner. Both stay true, and both are DEFER.
        "foreign_actor": foreign_actor,
        "foreign_actor_evidence": foreign_paths,
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
            # Which registration answered, when one did. Exposed because a
            # reader reconciling this row against the registry needs the id
            # it matched, not only the owner ref — and because the identity
            # may have matched by an anchor rather than by the container's
            # name, which is exactly the case a reader cannot reconstruct.
            "registry_resource_id": (matched_entry or {}).get("resource_id"),
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


def _paths_in(evidence_record: dict) -> list[tuple[str, str]]:
    """Every path this container's evidence offers, and which class it is.

    Working directory first, then bind-mount sources, in the order the
    contract lists them. Returns both the path and WHICH piece of evidence it
    came from, because `working-directory` and `bind-mount` are different
    provenance classes and a report that blurs them cannot be argued with.

    Split out from `_first_under_root` so that the foreign-home check reads
    the same list. Two collectors would eventually disagree about what counts
    as a path, and the disagreement would show up as one rule seeing a
    directory the other called unowned.
    """
    candidates: list[tuple[str, str]] = []
    working_dir = (evidence_record.get("compose") or {}).get("working_dir")
    if working_dir:
        candidates.append((working_dir, "working-directory"))
    for mount in evidence_record.get("mounts") or []:
        if mount.get("type") == "bind" and mount.get("source"):
            candidates.append((mount["source"], "bind-mount"))
    return candidates


def _first_under_root(evidence_record: dict, roots: dict) -> dict | None:
    """The first path in this container's evidence that sits under a known root."""
    if not roots:
        return None

    for path, source in _paths_in(evidence_record):
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

    # §26 wants an observation time on the observation, and it is stamped
    # HERE rather than inside `attribute()` because that function is pure and
    # a clock would make it stop being so — the property that lets the whole
    # 77-test fixture set run without a daemon. One stamp per tick: every row
    # in one report carries the same time, so a reader can tell the rows were
    # observed together rather than a few hundred milliseconds apart.
    stamp = None
    clock = getattr(host, "now", None)
    if callable(clock):
        try:
            stamp = clock()
        except Exception:  # noqa: BLE001 - a host with no clock is a fact, not an error
            stamp = None

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
        attribute(ev, registry=registry, roots=roots,
                  container_index=container_index, observed_at=stamp)
        for ev in evidences
    ]

    by_disposition: dict[str, int] = {}
    for item in results:
        by_disposition[item["disposition"]] = by_disposition.get(item["disposition"], 0) + 1

    # §11. The distribution, counted here rather than left to a reader, because
    # the interesting number is the UNKNOWN one: on this host it is the
    # majority, and a summary that listed only the classes it could name would
    # render "we could not tell" as a shorter list rather than as the largest
    # bucket.
    by_management: dict[str, int] = {}
    by_management_confidence: dict[str, int] = {}
    for item in results:
        management = item.get("management") or {}
        klass = management.get("class") or "UNKNOWN"
        by_management[klass] = by_management.get(klass, 0) + 1
        key = f"{klass}/{management.get('confidence') or 'UNKNOWN'}"
        by_management_confidence[key] = by_management_confidence.get(key, 0) + 1

    name_derived = [
        r["container"] for r in results
        if (r.get("identity") or {}).get("stable_across_rename") is False
    ]

    return {
        "count": len(results),
        "unknown_owner_count": sum(1 for r in results if r["unknown_owner"]),
        "by_disposition": by_disposition,
        "by_management": by_management,
        "by_management_confidence": by_management_confidence,
        # §10. Empty on this host. A non-empty list is the finding: it names
        # the containers whose identity a rename would move.
        "identity_stable_across_rename": not name_derived,
        "name_derived_identities": sorted(name_derived),
        "registry_present": bool(registry.get("registry_present")),
        # §8's device_ref, once, for the whole tick. Every row already reads
        # it from the registry; a consumer that needs "which node was this
        # observed on" should not have to take it from whichever row happens
        # to carry one, and a container declaring a different device would
        # otherwise make the answer depend on row order.
        "device_ref": registry.get("device_ref"),
        "containers": sorted(results, key=lambda r: (r["container"] or "")),
        "note": (
            "reclaim_candidate is false for every container here. Attribution "
            "is not authorization: §18 forbids this layer from removing, "
            "pruning or stopping anything, and a container attributed to a "
            "protected ref would be refused by the registry even if it were."
        ),
    }
