#!/usr/bin/env python3
"""§17 · Protection — declared, never inferred, and never lost on a rename.

The rule
--------
Protection is a DECLARATION. Nothing here infers it, and the specific
inference this module exists to refuse is the one everybody makes: a name
containing `prod`, `production`, `critical`, `important`, `live` or `primary`
looks like something that must not be touched, and a resolver that treats that
appearance as protection has built a permission model out of substring
matching. It is worse than no protection at all, because it produces a list of
protected things that nobody wrote down and that changes when somebody renames
a container.

So `guessed_from` is returned on every resolution, naming every identity whose
TEXT would have been read that way — and those identities contribute nothing.
Reporting the temptation rather than silently ignoring it is the point: the
next person to read this output can see the near-miss and say whether the
declaration is simply missing.

The hazard this module really exists for
----------------------------------------
§17's second half, and the one with a live failure behind it: **protection
must not lapse when a resource's identity changes.** §9 moved the canonical
identity of a container from its Docker NAME to `runtime:container:<id>`.
Protection on this node can be declared against either — `protected_refs`
matches a ref's id against a free-text name, `Resource.protection` matches a
registration, a container can carry `serveros.protected=true` — and those
sources are keyed by different spellings of the same thing. A container
renamed, or an identity switched to the container id, silently stops matching
an entry that named it, and *silently* is the whole problem: a protection that
lapses produces no error, no anomaly, and no line in any report. It produces a
container that is now reclaimable and looks exactly like one that always was.

So `resolve()` takes EVERY identity a container has and unions what each of
them finds, and reports per identity which source matched. The regression gate
is a test that resolves this node's real registry through the container-id
identity alone and asserts protection is unchanged — see
`tests/test_protection.py::test_the_live_registry_protects_by_every_identity`.
"""
from __future__ import annotations

import importlib.util
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


registry = _load("serveros_runtime_registry", "registry.py")
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")

PROTECTED = "protected"
NORMAL = "normal"
UNKNOWN = "unknown"

# The words that make a string LOOK protected. Kept here so that the refusal is
# legible and reviewable rather than an absence of code: the module docstring
# names this list as the thing it will not do, and a reader who wants to add a
# word can see exactly what would change if it were ever consulted.
GUESS_WORDS = ("prod", "production", "critical", "important", "live", "primary")

# A finding a caller can act on, and a code an anomaly catalogue could carry.
LOST_ON_IDENTITY_SWITCH = "PROTECTION_LOST_ON_IDENTITY_SWITCH"

# The label a container uses to declare itself protected. Same convention as
# `hygiene.OWNER_LABEL`; a container's own declaration is a declaration.
PROTECTED_LABEL = "serveros.protected"


def looks_protected(text) -> list[str]:
    """The guess words in this text, if any. Used to REPORT, never to decide."""
    if not isinstance(text, str):
        return []
    lowered = text.lower()
    return [w for w in GUESS_WORDS if re.search(rf"(^|[^a-z]){re.escape(w)}([^a-z]|$)", lowered)]


def parse_labels(text) -> dict:
    """Docker's `{{json .}}` `Labels` field, as a mapping.

    `docker ps --format '{{json .}}'` reports labels as one comma-separated
    `k=v,k=v` string, not as an object. A caller that wants to ask whether a
    container declares itself protected therefore has to parse it, and this is
    where that happens rather than at each caller: the label namespace, its
    spelling and the container's right to declare itself protected are this
    module's subject, and a second parser is a second opinion about what a
    declaration means.

    A value may itself contain `=`, so the split is on the FIRST one. A pair
    with no `=` is kept with an empty value rather than dropped — `Labels` can
    carry bare flags, and dropping one would make a container look like it
    declared less than it did. A mapping is passed through unchanged, so a
    caller that already parsed it is not asked to stringify it first.
    """
    if text is None:
        return {}
    if isinstance(text, dict):
        return dict(text)
    out: dict[str, str] = {}
    for pair in str(text).split(","):
        pair = pair.strip()
        if not pair:
            continue
        key, sep, value = pair.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip() if sep else ""
    return out


def _as_registry(registry_doc):
    if registry_doc is None:
        return None
    if isinstance(registry_doc, registry.Registry):
        return registry_doc
    if isinstance(registry_doc, (str, Path)):
        return registry.Registry(path=Path(registry_doc))
    return registry.Registry(document=registry_doc)


def identities_of(identity: dict | None = None, *, name=None, container_id=None,
                  labels=None, ref=None) -> list[dict]:
    """Every spelling of one container, in the order they should be trusted.

    All of them are checked. The order decides nothing about the answer — the
    union does — and is reported so that a reader can see which spelling a
    given protection entry happened to match.
    """
    out: list[dict] = []

    def add(kind, value, why):
        if value and not any(e["kind"] == kind and e["value"] == value for e in out):
            out.append({"kind": kind, "value": value, "why": why})

    if identity:
        declared = (identity.get("runtime_ref") or "").split(":", 2)[-1]
        add("runtime_ref", identity.get("runtime_ref"),
            "the canonical identity §9 assigns this container")
        if identity.get("source") == "container-id":
            add("container_id", declared,
                "the id the runtime_ref was built from")
        if identity.get("declared_name"):
            add("declared_name", identity["declared_name"],
                "the name the owner wrote in a label")
    add("container_id", container_id,
        "the Docker id, which a rename does not change")
    add("container_name", name, "the Docker name, which a rename does change")
    add("runtime_ref", ref, "a ref supplied by the caller")
    if labels:
        # The compose project and the container name as COMPOSE spells them.
        # These are provenance, not ownership — but a protection entry may
        # legitimately have been written against one, and a resolver that
        # ignores them would drop protection it never looked for.
        add("compose_service", labels.get("com.docker.compose.service"),
            "the compose service name")
        add("compose_project",
            (labels.get("com.docker.compose.project") or "") or None,
            "the compose project name")
    return out


def _matching_resources(reg, identity) -> list:
    """Registrations this identity names, by id or by a probe's container."""
    value = identity["value"]
    found = []
    for resource in reg.all():
        if resource.id == value:
            found.append(resource)
            continue
        for probe in resource.probes:
            if probe.get("container") == value:
                found.append(resource)
                break
    return found


def resolve(*, identities, registry_doc=None, protected_refs=None, labels=None,
            declared=None) -> dict:
    """Whether this container is protected, from every declared source.

    Returns `True`, `False`, or `None`. `None` is the honest answer when no
    source on this node DECLARED anything: `protected_refs` is a list a person
    maintains, and a resolution that reported `False` because the list was
    empty would be reporting "not protected" on the strength of never having
    been told. It is the same distinction `containers._protected_state` makes
    between `False` and `None`, and it is the one that matters most here,
    because the wrong `False` is a protection bypass performed by a default.
    """
    identities = identities or []
    reg = _as_registry(registry_doc)
    sources: list[dict] = []
    guessed: list[dict] = []

    for identity in identities:
        words = looks_protected(identity["value"])
        if words:
            guessed.append({
                "identity": identity,
                "words": words,
                "why": (
                    f"{identity['kind']} {identity['value']!r} contains "
                    f"{words}. It was NOT treated as protection. §17 forbids "
                    f"reading a name as a permission level: a name is a "
                    f"string, the list of words that look dangerous is a list "
                    f"nobody reviewed, and the day somebody renames this "
                    f"container the protection disappears with no error, no "
                    f"anomaly and no line in any report. Declare it, or accept "
                    f"that it is not protected"
                ),
            })

        if labels and labels.get(PROTECTED_LABEL) is not None:
            raw = str(labels[PROTECTED_LABEL]).strip().lower()
            if raw in ("true", "1", "yes"):
                sources.append({
                    "source": "container-label",
                    "matched_identity": identity["kind"],
                    "matched_value": identity["value"],
                    "ref": None,
                    "why": (
                        f"the container writes {PROTECTED_LABEL}={raw} on "
                        f"itself. A container's own declaration is a "
                        f"declaration, and it survives a rename because it "
                        f"travels with the container"
                    ),
                })

        if reg is not None:
            for resource in _matching_resources(reg, identity):
                if resource.protection == PROTECTED:
                    sources.append({
                        "source": "registration",
                        "matched_identity": identity["kind"],
                        "matched_value": identity["value"],
                        "ref": f"runtime:{resource.id}",
                        "why": (
                            f"the registration {resource.id!r} declares "
                            f"protection.level: protected"
                        ),
                    })

        for entry in (protected_refs if protected_refs is not None
                      else (reg.protected_refs() if reg is not None else [])):
            slug = str(entry.get("ref", "")).split(":", 1)[-1]
            if slug and registry._NAME_MATCH(slug, identity["value"]):
                sources.append({
                    "source": "protected_refs",
                    "matched_identity": identity["kind"],
                    "matched_value": identity["value"],
                    "ref": entry.get("ref"),
                    "why": (
                        f"protected_refs names {entry.get('ref')!r}, which "
                        f"matches this {identity['kind']}"
                        + (f" because {entry['because']}" if entry.get("because") else "")
                    ),
                })

    # A caller may declare protection outright for one resource whose
    # registration is not on this node — an active worktree, a test
    # environment, a container that predates the registry.
    if declared is True:
        sources.append({
            "source": "declared-by-caller",
            "matched_identity": None,
            "matched_value": None,
            "ref": None,
            "why": "the caller declared this resource protected",
        })
    elif declared is False:
        pass

    deduped = []
    seen = set()
    for source in sources:
        key = (source["source"], source["ref"], source["matched_identity"])
        if key not in seen:
            seen.add(key)
            deduped.append(source)

    if deduped:
        protected = True
    elif declared is False:
        protected = False
    elif reg is not None or protected_refs is not None:
        # A registry, or a protection list, was available and named nothing.
        # That is a declaration of "not protected", and it is the only case in
        # which this module says False.
        protected = False
    else:
        protected = None

    return {
        "protected": protected,
        "sources": deduped,
        "guessed_from": guessed,
        "identities_checked": identities,
        "why": (
            "; ".join(s["why"] for s in deduped) if deduped else
            "no declaration on this node protects this resource. Protection is "
            "declared, never inferred: a name that looks dangerous is not a "
            "permission level"
            if protected is False else
            "nothing on this node could be asked whether this resource is "
            "protected, so the answer is unknown rather than false. A `False` "
            "here would be a protection bypass performed by a default"
        ),
    }


def identity_switch_findings(*, by_identity: dict[str, dict]) -> list[dict]:
    """§17's gate: protection whose answer depends on the spelling asked.

    `by_identity` maps an identity kind to the result of resolving that
    identity ALONE. The finding is any spelling that is protected while the
    container's Docker id is not, because §17's sentence is about the identity
    switching to container-id and the protection not surviving it.

    TWO THINGS THIS GATE DOES NOT SAY, both measured on 2026-09-14 before the
    first finding was written:

      it does not say the automatic path resolves by id. It does not:
      `containers.attribute` matches a registration by container name (rung 3)
      and by the §9 anchors a registration declares (rung 3b). So this is not
      a finding about the reconcile path.

      it does not say these containers are unprotected. Every container it
      fires for today is protected under a name that the registration and the
      `protected_refs` entry both name it by.

    What is left, and what the finding is FOR, is that the answer to "is this
    protected?" is a function of the spelling the caller holds. Live, the gate
    fires for all eight protected containers on this node (2026-09-14) and for
    no unprotected one, which is the shape a gate should have: silent where
    nothing diverges.
    """
    canonical = by_identity.get("container_id")
    if canonical is None or canonical.get("protected") is not False:
        return []
    findings = []
    for kind, result in sorted(by_identity.items()):
        if kind == "container_id" or result.get("protected") is not True:
            continue
        findings.append({
            "code": LOST_ON_IDENTITY_SWITCH,
            # Through the catalogue, not typed here, so this finding carries the
            # same severity the contract assigns it and the `implies_stale:
            # false` every signal in this catalogue carries.
            "severity": anomaly.SEVERITY.get(LOST_ON_IDENTITY_SWITCH, "unknown"),
            "implies_stale": False,
            "identity_that_protects": kind,
            "identity_that_does_not": "container_id",
            "sources": result.get("sources") or [],
            "why": (
                f"the answer for this container depends on which spelling is "
                f"asked: through its {kind} a declaration protects it, through "
                f"its Docker id nothing on this node does. §17 asks for exactly "
                f"this not to happen. What it does NOT say, and what was "
                f"measured before this sentence was written, is that the "
                f"automatic path resolves by id: `containers.attribute` "
                f"matches a registration by container NAME (rung 3) and then "
                f"by the §9 anchors a registration declares (rung 3b), so "
                f"today's reconcile path does not reach a container by its raw "
                f"id at all. The caller this finding is about is the one "
                f"holding only the id — an operator running `docker rm <id>`, "
                f"an external tool, an audit that keyed its records by id. "
                f"Deciding to declare the protection against a stable anchor, "
                f"or to accept the exposure, is a decision for a person. What "
                f"must not happen is for two callers to get two answers and "
                f"neither of them be told"
            ),
        })
    return findings


def resolve_by_identity(*, identities, registry_doc=None, protected_refs=None,
                        labels=None) -> dict:
    """`resolve` once per identity kind, plus the switch findings over them."""
    by_identity = {}
    for identity in identities:
        by_identity[identity["kind"]] = resolve(
            identities=[identity],
            registry_doc=registry_doc,
            protected_refs=protected_refs,
            # Labels travel with the container, so they belong to every
            # identity rather than to one of them.
            labels=labels,
        )
    return {
        "by_identity": by_identity,
        "findings": identity_switch_findings(by_identity=by_identity),
    }
