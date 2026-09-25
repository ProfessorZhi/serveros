#!/usr/bin/env python3
"""One meeting's declared sidecars, assembled into the bundle a kernel can be asked about.

WHY THIS MODULE EXISTS
----------------------
`space/meeting_relevance.py` answers what a MeetingBundle contributes to work,
and until this module existed nothing in the tree produced a MeetingBundle.
`serverosctl space meeting --request …` needs a `bundle` object, and the only
way to get one was to hand-author the JSON — which means the kernel was
reachable and unreached, the same shape `work admit` had before §9.13. The
plan's P3 completion line (会议复用既有 Minutes/Bundle, metadata→必要摘要/转录/片段)
is about a meeting's own material getting there.

WHAT IT IS
----------
A pure function over a declaration. `assemble(declaration, observed_at=…)`
returns two documents and writes nothing:

  * `reading` — what the declaration turned out to be: which required members
    are present, which are not and what each of them IS, which of them may be
    read now and what their content is, the projects the members name, and the
    declared signals. This is the auditable half.
  * `bundle` — the `bundle` object `meeting_relevance.classify()` takes, or
    `None` when the reading refused one. This is the half the kernel sees.

The pair is deliberate: a reader must be able to tell "the kernel was asked and
said X" from "no question was put", and merging the two into one object would
make a refused bundle look like a classified one.

The reading's own two answers are equally deliberate. `state` is about the unit
— one meeting's minute, audio, transcript and summary together — and `readable`
is about material: which members can be read this minute, with their content.
The plan's P2 line (已有合法 transcript 可先读，不等 MP3 或全套成员) is exactly the
answer `state` cannot give, because "not a bundle yet" was being read as
"nothing here is readable yet". Handing material over does not make a unit, and
`readable` is never the object the kernel is asked about.

WHAT IT REFUSES TO DO
---------------------
It does not decide which members a bundle is. That sentence is quoted from the
machine policy in `meeting-bundle-contract.yaml`, verbatim, and this module
carries it rather than restating it. A meeting missing its audio is not a
partial bundle of this kind — it is not this kind of thing — and a completeness
percentage would invite "90% is fine" about a requirement that does not admit
one.

It does not collapse "not present". `PROCESSING`, `FAILED`, `ABSENT` and
`UNREADABLE` are four facts and are reported as four; the plan's own test list
asks for the distinction by name (会议 processing / 缺音频). "Wait for the
transcript" and "this meeting has no transcript" are different next actions.

It does not choose a project. A meeting that touched three projects is ordinary
(多项目). The set is reported and no member's project is promoted to the
bundle's.

It does not read a clock. `observed_at` is a parameter and the caller states it,
for the same reason `meeting_relevance` takes `as_of`: a reading whose answer
depends on when it ran cannot be compared against a recorded one.

It does not invent a tie. Transcript segments are passed through exactly as the
declaration carries them; whether a segment is tied to work is the producer's
declaration and never this reader's inference.

Read-only. It has no write path and imports nothing that mutates.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
CONTRACT_PATH = _HERE / "meeting-bundle-contract.yaml"
CONTRACT_SCHEMA = "serveros/meeting-bundle-contract.v1"
BUNDLE_SCHEMA = "serveros/meeting-bundle.v1"

#: The five readings one declared sidecar can have. The four that are not
#: `PRESENT` are the whole point: see `presence` in the contract.
PRESENT, PROCESSING, FAILED, ABSENT, UNREADABLE = (
    "PRESENT", "PROCESSING", "FAILED", "ABSENT", "UNREADABLE")
NOT_PRESENT_STATES = (PROCESSING, FAILED, ABSENT, UNREADABLE)

READINGS = ("COMPLETE", "INCOMPLETE", "MIXED_SCOPE")
PROJECT_READINGS = ("SINGLE", "MULTIPLE", "UNDECLARED")

#: What the reading says for an axis the declaration did not carry. The word is
#: this contract's and never reaches the kernel: the kernel's own word for
#: "nobody said" is `UNKNOWN`, and it applies that from `None`. See `axes` in
#: the contract for why the producer does not carry a second one.
UNDECLARED = "UNDECLARED"


class Refused(ValueError):
    """The declaration could not be read, or could not be assembled.

    Argument-level, like `support.Refused` and `meeting_relevance.Rejected`: a
    declaration that is malformed is the caller's mistake. A declaration that
    read fine and says the meeting lacks its audio is NOT this — that is a
    reading, and it comes back as `state: INCOMPLETE`.
    """


def _load(name: str, filename: str):
    """The by-path loader idiom, with the `sys.modules` cache.

    Without the cache, `meeting_relevance` loaded here would be a second module
    object from the one the CLI loaded, and `except Kernel.Rejected` in a caller
    would not catch what this one raises.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kernel = _load("serveros_space_meeting_relevance", "meeting_relevance.py")

# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
_CONTRACT_CACHE: dict | None = None


def load_contract(path: str | Path = CONTRACT_PATH) -> dict:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if document.get("schema") != CONTRACT_SCHEMA:
        raise Refused(f"{path} declares schema {document.get('schema')!r}, "
                      f"not {CONTRACT_SCHEMA!r}")
    return document


def contract() -> dict:
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        _CONTRACT_CACHE = load_contract()
    return _CONTRACT_CACHE


def _values(block_name: str) -> object:
    block = contract().get(block_name)
    if not isinstance(block, dict) or "values" not in block:
        raise Refused(f"the bundle contract declares no {block_name}.values")
    return block["values"]


def required_members() -> tuple[str, ...]:
    values = _values("required_members")
    if not isinstance(values, list) or not values:
        raise Refused("required_members.values must be a non-empty list")
    return tuple(str(v) for v in values)


def member_kinds(vocabulary: dict) -> dict[str, str]:
    """The machine policy's four words, each mapped onto the kernel's own set.

    The mapping is checked against the vocabulary's declared `object_type`
    values on every read. A vocabulary that dropped `recording` would otherwise
    turn "the audio is present" into a silent miss, and the bundle would read
    COMPLETE while naming a member the kernel does not know.
    """
    mapping = _values("member_kind")
    if not isinstance(mapping, dict):
        raise Refused("member_kind.values must be an object")
    declared = kernel._values(vocabulary, "object_type")
    for word, kind in mapping.items():
        if kind not in declared:
            raise Refused(
                f"the bundle contract maps {word!r} to {kind!r}, which the meeting "
                f"vocabulary does not declare as an object_type; the declared set is "
                f"{sorted(declared)}")
    return {str(k): str(v) for k, v in mapping.items()}


def content_members() -> dict[str, str]:
    """Each `declaration.content` key, and the member whose presence gates it.

    A content key is not a member: `transcript_segments` is the transcript's
    content, and whether it may be handed over is decided by whether the machine
    policy's `transcript` member is PRESENT — never by whether the declaration
    attached segments. A declaration carrying segments for a transcript it
    declared PROCESSING has content for material it did not declare present, and
    the reader reports that rather than either handing it over or dropping it.

    Every mapped word is checked against `required_members()` on each read, so a
    contract edited to gate a key on a word the unit is not made of is a refusal
    here and not a key nobody can ever read.
    """
    mapping = _values("content_member")
    if not isinstance(mapping, dict) or not mapping:
        raise Refused("content_member.values must be a non-empty object")
    unit = required_members()
    for key, word in mapping.items():
        if word not in unit:
            raise Refused(
                f"the bundle contract gates the content key {key!r} on {word!r}, "
                f"which is not one of the members this unit is made of; the "
                f"declared set is {list(unit)}")
    return {str(k): str(v) for k, v in mapping.items()}


def axes_names() -> tuple[str, ...]:
    """Which dimensions of the vocabulary the two axes live in.

    Declared by the contract, resolved against the vocabulary by
    `_declared_axis`, and never spelled out as a literal in this module.
    """
    values = _values("axes")
    if not isinstance(values, list) or not values:
        raise Refused("axes.values must be a non-empty list")
    return tuple(str(v) for v in values)


def presence_states() -> tuple[str, ...]:
    values = _values("presence")
    if not isinstance(values, dict) or not values:
        raise Refused("presence.values must be a non-empty object")
    return tuple(str(k) for k in values)


# ---------------------------------------------------------------------------
# Reading the declaration
# ---------------------------------------------------------------------------
def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{label} must be a non-empty string")
    return value.strip()


def _stable_id(value, label: str) -> str:
    """The kernel's own wildcard rule, read from the kernel.

    Not re-implemented here: a second copy of a refusal drifts from the one the
    kernel enforces, and this producer would then build bundles the kernel
    rejects for a reason it could have caught.
    """
    text = _require(value, label)
    if text.strip().lower() in kernel.WILDCARDS or "*" in text or "?" in text:
        raise Refused(f"{label} is a wildcard ({text!r}); a wildcard must not "
                      f"match everything")
    return text


def _iso8601(value, label: str) -> str:
    text = _require(value, label)
    shape = "2026-09-20T00:00:00Z"
    if len(text) != len(shape) or text[-1] != "Z" or text[10] != "T":
        raise Refused(f"{label} is not an ISO-8601 UTC instant ({text!r}); "
                      f"the expected shape is {shape!r}")
    return text


def _members(declaration: dict, vocabulary: dict) -> dict[str, dict]:
    """Every declared sidecar, keyed by the machine policy's own four words.

    A member whose `kind` is not one of the four is REPORTED rather than
    dropped: `document`, `attachment`, `cache` and `export` are declared object
    types and a meeting may well carry them, but none of them is a member of
    this unit, and silently ignoring one would make an unexpectedly large
    declaration look like a small one.
    """
    raw = declaration.get("members")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise Refused("declaration.members must be a list")
    mapping = member_kinds(vocabulary)
    by_kind = {kind: word for word, kind in mapping.items()}
    seen: dict[str, dict] = {}
    other: list[dict] = []
    for number, member in enumerate(raw, 1):
        if not isinstance(member, dict):
            raise Refused(f"members[{number}] is not an object")
        kind = _require(member.get("kind"), f"members[{number}].kind")
        word = by_kind.get(kind)
        if word is None:
            other.append({"kind": kind,
                          "object_ref": member.get("object_ref"),
                          "because": (f"{kind!r} is a declared object_type and not one of "
                                      f"the four members this unit is made of")})
            continue
        state = _require(member.get("state"), f"members[{number}].state")
        if state not in presence_states():
            raise Refused(f"members[{number}].state is not a declared presence: {state!r}; "
                          f"the declared set is {sorted(presence_states())}")
        entry = {
            "kind": kind,
            "state": state,
            "object_ref": member.get("object_ref"),
            "revision": member.get("revision"),
            "because": (member.get("because") if isinstance(member.get("because"), str)
                        else None),
        }
        if state == PRESENT:
            # `PRESENT` is a claim about material that can be read, so it carries
            # the two things that make it one. A member declared PRESENT with no
            # revision is refused rather than recorded: "present" with nothing to
            # read is the claim this module exists to not make.
            entry["revision"] = _require(member.get("revision"),
                                         f"members[{number}].revision (a PRESENT member)")
            entry["object_ref"] = _require(member.get("object_ref"),
                                           f"members[{number}].object_ref (a PRESENT member)")
        if member.get("meeting_ref") is not None:
            entry["meeting_ref"] = _stable_id(member["meeting_ref"],
                                              f"members[{number}].meeting_ref")
        if member.get("project_ref") is not None:
            entry["project_ref"] = _require(member["project_ref"],
                                            f"members[{number}].project_ref")
        if word in seen:
            raise Refused(f"two members are declared as {kind!r} ({word!r}); a bundle has "
                          f"one of each member, and which of the two was meant is not "
                          f"this reader's to pick")
        seen[word] = entry
    return {"members": seen, "other_members": other}


def _scope(declaration: dict, members: dict) -> dict:
    """The bundle's own scope, and a refusal when the members disagree with it.

    `meeting_relevance` refuses a wildcard in a scope id, so this reads them the
    same way. A member carrying another meeting's id is `MIXED_SCOPE`: the
    declaration is not a bundle with a defect, it is two meetings, and picking
    one would attribute material to a meeting it does not belong to.
    """
    raw = declaration.get("scope")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise Refused("declaration.scope must be an object")
    scope = {}
    for kind in kernel.SCOPE_KINDS:
        if raw.get(kind) is not None:
            scope[kind] = _stable_id(raw[kind], f"scope.{kind}")
    meeting = scope.get("meeting_ref")
    stray = sorted({entry["meeting_ref"] for entry in members["members"].values()
                    if entry.get("meeting_ref") and entry["meeting_ref"] != meeting})
    return {"scope": scope, "stray_meeting_refs": stray}


def _projects(members: dict) -> dict:
    named = sorted({entry["project_ref"] for entry in members["members"].values()
                    if entry.get("project_ref")})
    reading = "UNDECLARED" if not named else ("SINGLE" if len(named) == 1 else "MULTIPLE")
    return {"projects": named, "project_reading": reading}


def _declared_axis(declaration: dict, name: str, vocabulary: dict) -> str | None:
    """One of the two axes, or `None` because the declaration did not carry it.

    `None` is the whole design. The kernel reads an absent axis as `UNKNOWN`
    and validation happens against the vocabulary's own set, so the honest
    thing for a producer that was told nothing is to say nothing — a value
    invented here is a claim about who owns a meeting, and `UNDECLARED` is not
    one the kernel declares. A value the declaration DID carry is checked here
    as well, so a declaration the kernel would reject is refused while the
    reading is still the thing being reported.
    """
    declared = kernel._values(vocabulary, name)
    if not declared:
        raise Refused(
            f"the meeting vocabulary declares no `{name}` dimension, and the kernel "
            f"validates every bundle's {name} against it: a vocabulary without it "
            f"rejects every bundle for a reason this producer can see")
    value = declaration.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or value not in declared:
        raise Refused(
            f"declaration.{name} is {value!r}; the kernel's `{name}` dimension declares "
            f"{sorted(declared)}. This producer will not hand the kernel a bundle it "
            f"rejects for a word that could have been checked here")
    return value


def _signals(declaration: dict, vocabulary: dict) -> dict:
    """The declared signals, carried one for one into the kernel's fields.

    `meeting_relevance` records every signal and marks the set
    RECORDED_INSUFFICIENT; nothing reads one. What matters here is that a signal
    the declaration did not carry stays `None` rather than becoming a default —
    an `audio_duration_s` of 0 is a claim about a recording that does not exist,
    and the duration is NOT read off the recording member either: a member being
    present says nothing about how long it is, and a producer that filled this in
    from the member would be inventing a measurement.
    """
    raw = declaration.get("signals")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise Refused("declaration.signals must be an object")
    return {name: raw.get(name) for name in (kernel._block(vocabulary, "signals") or [])}


def _content_value(declaration: dict, key: str) -> list:
    """One content key's value, shape-checked. The only place this is done.

    Both halves of the answer read content through here — the bundle under the
    key the kernel reads, the readable half under the key the contract maps to a
    member — so the two cannot come to different conclusions about what a
    declaration's segments are, and a malformed one is refused on either path
    rather than being refused on one and quietly reshaped on the other.
    """
    content = declaration.get("content")
    if content is None:
        return []
    if not isinstance(content, dict):
        raise Refused("declaration.content must be an object")
    raw = content.get(key)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Refused(f"declaration.content.{key} must be a list")
    return raw


def _segments(declaration: dict) -> list:
    """The transcript's own segments, passed through unchanged.

    A `tie` is declared by the producer of the transcript and never inferred
    here: `meeting_relevance` treats an untied segment as incidental audio, and
    a reader that invented a tie would silently promote a aside into work.

    The key is spelled here because it is the KERNEL's (`meeting_relevance` reads
    `bundle.content.transcript_segments`) and not this contract's: a key that
    drifted from the kernel's spelling would build bundles whose segments the
    kernel reads as none, and every segment would go untied without a word.
    """
    return _content_value(declaration, "transcript_segments")


def _readable(declaration: dict, present: list[str], state: str) -> dict:
    """What may be read now, apart from whether the unit is complete.

    The plan's P2 line — 已有合法 transcript 可先读，不等 MP3 或全套成员 — is about an
    answer the reading did not have: `COMPLETE`/`INCOMPLETE` answers "is this one
    unit", and a transcript that is already there was reported as nothing more
    than a member marked PRESENT, with its own segments unreachable because
    `bundle` is None for every reading that is not COMPLETE. Handing the unit
    over and handing its material over are two different acts, and only the
    second one is available while the audio is still being produced.

    Handover does not make a unit: `readable` is not a bundle, carries no
    `bundle_ref`, and is never the object the kernel is asked about. A caller
    that wants a decision still waits for four members; a caller that wants to
    read the transcript now does not.

    `MIXED_SCOPE` hands nothing over and says so. The refusal there is about
    scope — the members name more than one meeting and which was meant is not
    this reader's to pick — and material whose meeting is undetermined is not
    material this reader reads out under a meeting of its own choosing.
    """
    content: dict = {}
    withheld: dict = {}
    if state == "MIXED_SCOPE":
        return {
            "present": list(present),
            "content": content,
            "withheld": withheld,
            "because": ("the scope was refused, so nothing is handed over: the "
                        "segments belong to a meeting this reader will not name, "
                        "and the members present are listed without their content"),
        }
    for key, word in content_members().items():
        value = _content_value(declaration, key)
        if word in present:
            content[key] = value
        elif value:
            # Attached, but for a member the declaration did not call present.
            # Reported rather than dropped: a reader told only "no content" would
            # take away that there was none.
            withheld[key] = word
    return {
        "present": list(present),
        "content": content,
        "withheld": withheld,
        "because": (
            "these members are present and readable now; the unit is not complete, "
            "so this is not a bundle and no question was put to the kernel. Reading "
            "this is reading material, not a decision about the meeting"
            if state != "COMPLETE" else
            "every required member is present, so what is readable here is the same "
            "material the bundle carries below"),
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def assemble(declaration: dict, *, observed_at: str, vocabulary: dict | None = None) -> dict:
    """One declaration in, the reading and the kernel's bundle out.

    `bundle` is `None` for every reading that is not COMPLETE. That is not a
    convenience: a bundle missing a member is not a bundle this kernel may be
    asked about, and handing one over would produce a decision record about
    material that is not there.
    """
    if not isinstance(declaration, dict):
        raise Refused("the declaration must be an object")
    vocab = vocabulary if vocabulary is not None else kernel.load_vocabulary()
    observed = _iso8601(observed_at, "observed_at")

    members = _members(declaration, vocab)
    scope_reading = _scope(declaration, members)
    projects = _projects(members)
    signals = _signals(declaration, vocab)
    axes = {name: _declared_axis(declaration, name, vocab) for name in axes_names()}

    required = required_members()
    not_present = {}
    for word in required:
        entry = members["members"].get(word)
        not_present[word] = entry["state"] if entry else ABSENT

    if scope_reading["stray_meeting_refs"]:
        state = "MIXED_SCOPE"
    elif [w for w in required if not_present[w] != PRESENT]:
        state = "INCOMPLETE"
    else:
        state = "COMPLETE"

    reading = {
        "schema": BUNDLE_SCHEMA,
        "state": state,
        "observed_at": observed,
        "scope": scope_reading["scope"],
        "required_members": list(required),
        "members": {word: members["members"].get(word) for word in required},
        "not_present": not_present,
        "other_members": members["other_members"],
        "projects": projects["projects"],
        "project_reading": projects["project_reading"],
        "axes": {name: value if value is not None else UNDECLARED
                 for name, value in axes.items()},
        "signals": signals,
        "stray_meeting_refs": scope_reading["stray_meeting_refs"],
        # Both halves come from ONE `not_present` map, so a member this half
        # hands over can never be one `state` called absent.
        "readable": _readable(declaration, [w for w in required
                                            if not_present[w] == PRESENT], state),
        "because": _because(state, not_present, scope_reading, members),
    }

    if state != "COMPLETE":
        return {"reading": reading, "bundle": None}

    bundle = {
        "bundle_ref": _bundle_ref(declaration, scope_reading["scope"]),
        "scope": scope_reading["scope"],
        "signals": {name: signals.get(name)
                    for name in (kernel._block(vocab, "signals") or [])},
        "content": {"transcript_segments": _segments(declaration)},
        "work_relation": _work_relation(declaration),
        "dependants": _dependants(declaration),
    }
    # Two axes that relevance reports and never consults. Present only when the
    # declaration carried one: the kernel's `_axes` reads an absent axis as
    # UNKNOWN, and that default belongs at the kernel and not here.
    for name, value in axes.items():
        if value is not None:
            bundle[name] = value
    return {"reading": reading, "bundle": bundle}


def _bundle_ref(declaration: dict, scope: dict) -> str:
    given = declaration.get("bundle_ref")
    if given is not None:
        return _stable_id(given, "bundle_ref")
    meeting = scope.get("meeting_ref")
    if not meeting:
        raise Refused(
            "the declaration carries no bundle_ref and no scope.meeting_ref, so there "
            "is nothing stable to name this bundle by. A generated id would change on "
            "every run and the kernel's record would not be comparable between two "
            "readings of the same meeting")
    return f"bundle:{meeting}"


def _work_relation(declaration: dict) -> dict:
    raw = declaration.get("work_relation")
    if raw is None:
        return {"references": []}
    if not isinstance(raw, dict):
        raise Refused("declaration.work_relation must be an object")
    given = raw.get("references")
    if given is None:
        return {"references": []}
    if not isinstance(given, list):
        raise Refused("declaration.work_relation.references must be a list")
    # Shape is checked by the kernel on the way in. Reported unchanged here so a
    # reference the declaration got wrong is a kernel refusal naming the field
    # rather than a silent drop in a builder nobody reads.
    return {"references": list(given)}


def _dependants(declaration: dict) -> list:
    raw = declaration.get("dependants")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise Refused("declaration.dependants must be a list")
    out = []
    for number, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise Refused(f"dependants[{number}] is not an object")
        out.append({
            "kind": _require(item.get("kind"), f"dependants[{number}].kind"),
            "ref": _require(item.get("ref"), f"dependants[{number}].ref"),
            # A dependant nobody declared as either is not a machine draft: the
            # kernel never silently rewrites a human's text, and saying "the
            # declaration was silent" must not read as "no human owns this".
            "human_approved": item.get("human_approved"),
            "published": item.get("published"),
        })
    return out


def _because(state: str, not_present: dict, scope_reading: dict, members: dict) -> str:
    if state == "MIXED_SCOPE":
        return (f"the declaration names members for more than one meeting "
                f"({', '.join(scope_reading['stray_meeting_refs'])} beside "
                f"{scope_reading['scope'].get('meeting_ref')!r}). That is two meetings "
                f"and not one bundle with a defect, and which was meant is not this "
                f"reader's to pick")
    if state == "INCOMPLETE":
        parts = []
        for word, value in not_present.items():
            if value == PRESENT:
                continue
            parts.append(f"{word} is {value}"
                         + (f" ({members['members'][word].get('because')})"
                            if members["members"].get(word, {}).get("because") else ""))
        return ("this is not one unit: the machine policy requires minute, audio, "
                "transcript and summary together, and " + "; ".join(parts))
    return ("every member the machine policy requires is present: "
            + ", ".join(f"{word} at {members['members'][word]['revision']}"
                        for word in required_members()))


def render(payload: dict) -> str:
    """The reading as lines. The kernel's own record is rendered by the kernel's verb."""
    reading = payload["reading"]
    lines = [f"state       {reading['state']}"]
    scope = " ".join(f"{kind}={reading['scope'][kind]}"
                     for kind in kernel.SCOPE_KINDS if kind in reading["scope"])
    lines.append(f"scope       {scope or '(none declared)'}")
    for word in reading["required_members"]:
        entry = reading["members"].get(word)
        if entry is None:
            lines.append(f"  {word:<10} {ABSENT}  ·  never declared")
            continue
        detail = entry.get("revision") or entry.get("because") or ""
        lines.append(f"  {word:<10} {entry['state']:<10} {detail}".rstrip())
    for entry in reading["other_members"]:
        lines.append(f"  {entry['kind']:<10} (not a member of this unit: {entry['kind']!r} "
                     f"is a declared object_type and not one of the four)")
    lines.append(f"projects    {reading['project_reading']}"
                 + (f"  {', '.join(reading['projects'])}" if reading["projects"] else ""))
    lines.append("axes        " + "  ".join(f"{name}={value}"
                                            for name, value in reading["axes"].items()))
    lines.append(f"observed    {reading['observed_at']}")
    readable = reading.get("readable")
    if readable is None:
        # Stated, not defaulted: an empty list here would say "nothing is
        # present" about a reading this renderer knows nothing about.
        lines.append("readable    (this reading carries no readable half)")
    else:
        words = readable.get("present") or []
        lines.append("readable    " + (", ".join(words) if words else
                                       "(nothing is present to read yet)"))
        for key, word in (readable.get("withheld") or {}).items():
            lines.append(f"  withheld  {key} -> the declaration calls {word} "
                         f"{reading['not_present'].get(word, ABSENT)}")
    lines.append(f"because     {reading['because']}")
    if payload["bundle"] is None:
        lines.append("bundle      (none — no question was put to the kernel)")
    return "\n".join(lines)
