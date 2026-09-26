#!/usr/bin/env python3
"""From what ServerOS recorded to what a person reads — deterministically.

Where this sits
---------------
OBSERVABILITY-CARDS.md §4 draws one line and this module is the box under it:

    Alert Fact + 生命周期事件 + subject resolver + 定义目录
                              ↓
                  确定性 presentation model        ← here
                              ↓
                       飞书 / CLI / HTML           ← runtime/cards.py

**ServerOS owns the facts.** This module reads `alerting` and `alert_digest`
answers and settles nothing it was not told: no root cause, no reclassification,
no severity adjustment, no retirement.

**This module owns the sentences**, and it owns them by *rule* rather than by
generation. §4 is explicit about both halves — "不能靠 LLM 自由生成根因，不能把
重启失败自动解释成网络故障" — and each half is enforced somewhere concrete:

  * no generation: every sentence comes from `card-definitions.yaml`, a declared
    table with no model anywhere near it;
  * no invented connection between two facts: the catalogue is forbidden cause
    words, and `test_presentation` enforces the ban rather than trusting review.

The three cards
---------------
§1 of the same document names three, and this module produces the model for
each. They are three functions and not one with a mode, because a card that can
be asked for the wrong shape is a card that will be:

    status_model()    the three blocks + the one-line environment verdict
    alert_model()     at most three events, each with the §4 field set
    recovery_model()  only what a person was TOLD, and that has since recovered

What this module will not do
----------------------------
It will not render. `cards.py` does that, from this model, and the fact that
two renderers (Feishu JSON, HTML) exist for one model is the entire reason the
model is a separate thing: §6 requires the JSON and the visual preview to come
from *the same renderer*, and a preview generated from different code is not
evidence about the card.

It also holds no state, opens no store and writes nothing.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    """Same idiom as the rest of `runtime/`: a sibling loaded by path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


alerting = _load("serveros_runtime_alerting", "alerting.py")
alert_digest = _load("serveros_runtime_alert_digest", "alert_digest.py")
# Neither detector is loaded here, and the omission is load-bearing.
# `resource_anomaly.py` sits behind
# `tests/test_resource_anomaly.py::test_nothing_in_the_tree_yet_creates_through_the_admission_gate`,
# which asserts the set of files that can reach it is exactly
# `{runtime/view.py}` — because reaching it is how a detector would get wired
# into a creation path, and §25 says that wiring has not happened. Loading it
# here for its `CODES` tuple would make that assertion false in the same commit
# that the safety claim became false, which is exactly what the gate exists to
# catch. `anomaly.py` is not gated, but it would be here for the same reason
# and the same answer applies: this module presents what a detector found, it
# does not ask a detector anything. The closed set of codes is
# `card-definitions.yaml`'s own business, and whether that catalogue covers
# both detectors is asserted in `tests/test_presentation.py`, on the tests side
# of the same boundary — which is the side that is allowed to load them.

SCHEMA = "serveros/presentation.v1"
DEFINITIONS = _HERE / "card-definitions.yaml"

#: §2/§6. A preview is not a measurement, and every model carries the mark.
#: §6 puts it in the card title as well; this is the model-level half, so that
#: a consumer which renders its own layout still cannot present a sample as a
#: reading.
SAMPLE_MARK = "示例 / 尚未发送"

DECLARED = "DECLARED"
ABSENT = "ABSENT"

#: §4: "没有影响证据时明确影响尚未确认". One sentence, so that an impact the
#: catalogue does not state and an impact nobody measured cannot be confused
#: with an impact that was measured and found slight.
#:
#: The wording is the console's, not an explainer's. The sentence that stood
#: here first was the label read back to the reader ("影响尚未确认"), which
#: rendered as `**影响** 影响尚未确认` — the same word twice — and the repair
#: for that put a paragraph of self-description on the first screen. This says
#: the same thing in the words a NOC would use and stops there. See
#: CARDS-STYLE.md R2 and R7.
IMPACT_UNCONFIRMED = "暂未发现明确影响"

#: What a reader may do, when the catalogue names nothing for a code. It names
#: no operation, and it is the default on purpose: the catalogue is allowed to
#: be silent about a code, and silence must not read as "nothing to do".
#:
#: It names a thing to look AT ("查看该条目的证据与归属") rather than a place to
#: go ("在 ServerOS 登记表里查看该条目"). The second form is what the first
#: version of this card said on every code the catalogue was silent about, and
#: it is not an action: it sends a person to a surface without saying what they
#: are looking for. CARDS-STYLE.md R5.
DEFAULT_ACTION = "查看该条目的证据与归属"

#: §15, restated on every action because a card is the surface where it is
#: most easily lost. `recommendation()` already returns a suggestion; the word
#: travelling alone is what gets read as an instruction.
#:
#: The refusal half of §15 is NOT repeated here. "没有任何动作会因此发生" used
#: to ride along with this line on every event, and the card states the same
#: thing once at the foot (`cards.FOOTNOTE_READ_ONLY`), where a reader who has
#: decided to act will look for it. Two statements of one refusal is how the
#: third event's version stops being read. CARDS-STYLE.md R2.
ACTION_AUTHORITY = "建议，不是授权"

UNKNOWN = "UNKNOWN"

#: §6's verdict words, named here because `runtime/cards.py` keys its colour
#: map on them and a colour map keyed on bare string literals in two files is a
#: colour map that renders grey the day somebody fixes a typo in one of them.
#: The names are chosen so that a missing one fails loudly: `_template()` has no
#: entry for a word it was not taught and renders the neutral card, which is
#: the right failure for a card and the wrong one for a typo.
OK = "OK"
ATTENTION = "ATTENTION"
UNCONFIRMED = "UNCONFIRMED"
CRITICAL = "CRITICAL"


class PresentationError(Exception):
    """A model that cannot be built from what was handed in."""


def _catalogue() -> dict:
    return yaml.safe_load(DEFINITIONS.read_text(encoding="utf-8")) or {}


def known_codes() -> frozenset:
    """Every code this catalogue defines — the model's own closed set.

    Not "every code a detector can raise". The difference is deliberate and it
    is the whole reason the two are allowed to disagree somewhere. The
    detectors cannot be read from here (see the note beside `anomaly` above):
    doing so would put this file inside the admission gate's loader set. So
    this returns what the declaration contains, and the equality between that
    and the detectors is asserted in `tests/test_presentation.py`, which may
    load both.

    What it buys here is the ability to tell a defined code from an undefined
    one *without* reaching for a detector — which is what `definition_for`
    needs in order to render a hole as a hole.
    """
    return frozenset(_catalogue().get("codes") or {})


def definition_for(code: str | None) -> dict:
    """What this code means, in one sentence, or a named absence.

    `ABSENT` is a first-class answer and not a fallback. A code the catalogue
    does not define still has to reach the card — it is the thing that fired —
    so the card carries the code verbatim and says the catalogue has a hole.
    Rendering the code and rendering nothing look almost identical on a phone,
    and only one of them is a statement about the catalogue.
    """
    codes = _catalogue().get("codes") or {}
    entry = codes.get(str(code or ""))
    if not isinstance(entry, dict) or not str(entry.get("what") or "").strip():
        return {"state": ABSENT, "code": code, "what": None, "label": None, "why": (
            f"card-definitions.yaml 没有为 {code!r} 声明含义；本卡照原名列出"
            f"该 code，不对它作任何断言")}
    return {"state": DECLARED, "code": code,
            "what": str(entry["what"]).strip(),
            # The short noun the event line wears after the subject. Optional:
            # a code with no `label` keeps the subject alone on its line rather
            # than having one invented for it, because an invented label is a
            # second definition of a code that already has one.
            "label": (str(entry["label"]).strip() if entry.get("label") else None),
            "impact": (str(entry["impact"]).strip() if entry.get("impact") else None)}


def comparison_word(code: str | None, *, desired, actual) -> str | None:
    """The word this code's catalogue entry declares for one comparison, or None.

    AC19: a service the registration says should be running and which is not
    has to be named as 不可用 or 未就绪 on the first screen, not left as a raw
    code or a sentence that is equally true of every other kind of mismatch.

    Both sides are read and both are required. `STOPPED` is "at rest, and that
    is the desired state" for a resource that declares `stopped`, and the same
    word against a declaration of `running` is a fault; a rule keyed on the
    measured word alone would make one spelling mean two things.

    `None` is the answer for a pair nobody wrote down, and the caller keeps the
    catalogue's generic sentence. That is the strict direction and the same one
    the attention table takes for an undecided code: an undeclared combination
    must not be given a word that nobody reviewed.
    """
    entry = (_catalogue().get("codes") or {}).get(str(code or "")) or {}
    for rule in entry.get("says") or []:
        if not isinstance(rule, dict):
            continue
        if str(desired or "") in [str(d) for d in rule.get("desired") or ()] and \
                str(actual or "") in [str(a) for a in rule.get("actual") or ()]:
            return str(rule.get("word") or "").strip() or None
    return None


def action_for(code: str | None, severity: str | None) -> dict:
    """What a person might look at, and the word that says it is a suggestion.

    The catalogue may name one; when it does not, the answer is still a
    sentence and not an empty field, because an empty action on a critical
    alert reads as "nothing to do about this one".
    """
    named = (_catalogue().get("actions") or {}).get(str(code or ""))
    return {"word": alerting.recommendation(str(severity or "")),
            "text": str(named).strip() if named else DEFAULT_ACTION,
            "named_by_catalogue": bool(named),
            "authority": ACTION_AUTHORITY}


# ---------------------------------------------------------------------------
# Attention. The axis that decides whether any of this is worth interrupting
# somebody for, and the one place it is decided.
# ---------------------------------------------------------------------------

CONTRACT = _HERE / "notification-contract.yaml"

#: The five levels, most urgent first. Present here so that a caller can sort
#: without reading the contract, and asserted equal to the contract's own
#: `axis` by `test_attention.py` — a constant that could drift from the
#: declaration it mirrors is exactly the constant this tree keeps deleting.
AXIS = ("ACTION_REQUIRED", "REVIEW", "WATCH", "INFORMATIONAL", "NONE")

#: §20.3's sentence, carried on the model rather than left in a document. A
#: consumer that reads a level off a card can read what the level meant without
#: opening this repository.
ATTENTION_RULE = "实际状态违反了一个明确的预期状态，而且这个偏差值得人知道"


def _contract() -> dict:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}


def attention_policy() -> dict:
    """The declared attention axis, read whole and restated nowhere.

    Read rather than transcribed. A copy of this table in Python would be the
    second authority over one axis that the contract forbids, and the two would
    disagree the first time somebody made the card quieter by editing the copy.

    A missing or malformed block raises rather than defaulting to something
    calm. A default of `NONE` here would turn a contract that failed to parse
    into a node with no problems, which is the reading this whole section was
    written to make impossible.
    """
    block = ((_contract().get("alerts") or {}).get("attention"))
    if not isinstance(block, dict):
        raise PresentationError(
            f"{CONTRACT} has no `alerts.attention` block; the attention axis is "
            "declared there and a missing one is not an axis at NONE")
    axis = tuple(block.get("axis") or ())
    if axis != AXIS:
        raise PresentationError(
            f"alerts.attention.axis is {axis!r}, not {AXIS!r}; the levels are a "
            "closed ordered set and a renderer sorts on this order")
    codes = block.get("codes")
    if not isinstance(codes, dict) or not codes:
        raise PresentationError(
            "alerts.attention.codes is missing; every code has to be decided "
            "about somewhere or the ones nobody decided about are the quiet ones")
    face = tuple(block.get("on_the_face") or ())
    for level in face:
        if level not in axis:
            raise PresentationError(
                f"alerts.attention.on_the_face names {level!r}, which is not on "
                f"the axis {AXIS!r}")
    return block


def code_expectations() -> dict:
    """`{code: {kind, level, bucket}}` — the closed mapping, flattened.

    Every code in the contract appears in exactly one bucket. `test_attention`
    asserts that against both detectors' own code sets, so a code added to
    `anomaly.SEVERITY` without a decision here fails the suite rather than
    reaching a card at whatever level the default happened to be.
    """
    block = attention_policy()
    out: dict[str, dict] = {}
    for bucket, entries in (block.get("codes") or {}).items():
        if not isinstance(entries, dict):
            continue
        for code, level in entries.items():
            out[str(code)] = {"kind": str(bucket), "level": str(level),
                              "bucket": str(bucket)}
    return out


def expectation_of(code: str | None) -> dict:
    """Which kind of expectation, if any, this code is the violation of.

    The three kinds are the contract's. A code in no bucket answers `None`
    rather than being given a default: an undecided code is a hole in the
    declaration, and `attention_of` reports it as one.
    """
    found = code_expectations().get(str(code or ""))
    if found is None:
        return {"kind": None, "level": None, "decided": False,
                "why": (f"notification-contract.yaml#alerts.attention.codes "
                        f"does not decide {code!r}")}
    return {**found, "decided": True, "why": None}


def attention_of(code: str | None, *, criticality: str | None = None) -> dict:
    """One code's attention level, and the rule that produced it.

    `criticality` is the ONLY input besides the code, and it can only ever
    escalate a `declared` finding — never a consumption one, never a history
    one. That asymmetry is the point: a critical registration is a statement
    somebody wrote down in advance, and a process consuming memory is not.

    An unknown criticality leaves the level alone. Absence of the field is not
    evidence of importance and not evidence of unimportance; escalating on it
    would make every unregistered subject read as critical.
    """
    found = expectation_of(code)
    if not found["decided"]:
        # An undecided code is not quietly levelled. NONE would say "we looked
        # and it does not matter", which is a different sentence from "nobody
        # decided", and only the second one is true.
        return {"level": None, "rule": "undecided", "expectation": None,
                "escalated": False, "because": found["why"]}
    level = found["level"]
    block = attention_policy().get("escalation") or {}
    escalated = False
    if (found["kind"] == "declared" and level == str(block.get("from"))
            and str(criticality or "") == "critical"):
        level = str(block.get("to"))
        escalated = True
    return {"level": level, "rule": found["kind"], "expectation": found["kind"],
            "escalated": escalated,
            "because": ("该 code 的预期来自声明，且主体登记为 criticality: critical"
                        if escalated else f"该 code 的预期类型是 {found['kind']}")}


def on_the_face(level: str | None) -> bool:
    """Whether a level may occupy a line on a card, per the contract."""
    return str(level or "") in tuple(attention_policy().get("on_the_face") or ())


def attention_word(level: str | None) -> str:
    """What a level is called on a card, or the level itself when undeclared.

    The fallback is the level's own name and not a friendlier one: a word
    invented here would be a second vocabulary for one axis, and the card would
    then be the only place the level was ever spelled that way.
    """
    words = attention_policy().get("words") or {}
    return str(words.get(str(level or "")) or level or "")


#: The `hold_kind`s that mean NOBODY LOOKED, as opposed to a value that is
#: still up. `alerting._graded_measurement` is the only producer of these, and
#: it names them so that this set does not have to be inferred from prose.
LOST_OBSERVATION = frozenset({"unmeasured", "not_evaluated"})


def _integrity_words() -> dict:
    """The declared sentence for the aggregate monitoring-integrity finding.

    Three keys, all required. A contract missing one of them is a contract in
    which this finding would have to be described by whoever renders it — which
    is the failure the block exists to prevent — so it raises instead.
    """
    block = ((attention_policy().get("monitoring_integrity") or {}))
    missing = [k for k in ("what", "impact", "action") if not block.get(k)]
    if missing:
        raise PresentationError(
            "notification-contract.yaml alerts.attention.monitoring_integrity "
            f"does not declare {', '.join(missing)}; the finding cannot be "
            "described without it")
    return {k: str(block[k]) for k in ("what", "impact", "action")}


def integrity_finding(held_rows: list[dict]) -> dict | None:
    """The held rows that are a monitoring-integrity finding, aggregated to one.

    AC6 and AC20 are the same rule read from two sides, and this function is
    where both are implemented:

      * a held row whose code carries a `declared` expectation is a finding —
        something the node said should exist has stopped being observable, and
        the loss blocks a judgement about it;
      * a held row whose code carries no expectation is NOT a finding. Nothing
        declares it should still be there, so "we cannot see it" is a fact
        about our looking rather than about the world.

    These are returned as ONE dict and never as N rows. 542 held entries with
    one cause are one line on a card; the alternative is the dump this whole
    round exists to remove.
    """
    findings, history = [], []
    for row in held_rows or []:
        kind = row.get("hold_kind")
        if kind is not None and kind not in LOST_OBSERVATION:
            # Somebody looked and the value is still up — the alert is held
            # because it has not come back below its floor, not because
            # anything became unobservable. We are being told the same thing we
            # were told last hour, and nothing is missing.
            continue
        (findings if expectation_of(row.get("code"))["kind"] == "declared"
         else history).append(row)
    if not findings:
        return None
    codes: dict[str, int] = {}
    for row in findings:
        code = str(row.get("code") or "")
        codes[code] = codes.get(code, 0) + 1
    # The wording comes from the contract, not from here. This function decides
    # WHICH rows are a finding and HOW MANY; the sentence a person reads is
    # declared in `alerts.attention.monitoring_integrity`, so changing it is a
    # change in the contract and nowhere else. `_integrity_words()` refuses a
    # contract that has lost them rather than falling back to a string built
    # here, because a fallback is how the second authority gets created.
    words = _integrity_words()
    return {
        "kind": "monitoring-integrity",
        "attention": attention_of(findings[0].get("code")),
        "count": len(findings),
        "by_code": codes,
        "text": words["what"].format(count=len(findings)),
        "impact": words["impact"],
        "action": words["action"],
        "not_a_finding": {
            "count": len(history),
            "note": ("另有无预期声明的历史主体同样未被观测到。没有任何东西声明"
                     "它们应当存在，因此「测不到」不是故障，不计入本条。"),
        },
    }


# ---------------------------------------------------------------------------
# The subject. §5: "对象显示名"。
# ---------------------------------------------------------------------------

#: The prefixes `alerting.RESOURCE_PREFIXES` already treats as naming a
#: resource, each with the rule that turns the rest of the ref into something a
#: person recognises. Ordered longest-prefix-first by hand: `process-tree:session-`
#: and `process-tree:` both match a session tree, and the more specific one has
#: to win or every session tree renders as an opaque pid blob.
_SUBJECT_RULES = (
    ("runtime-group:process-tree:session-",
     lambda rest: _session_tree(rest, "会话")),
    # A container's process tree, which is the shape most of this node's
    # findings take. Named as a container rather than left as `system.slice:
    # docker:<id>`: the slice, the runtime and the id are three layers of where
    # the process happens to live, and the card is about one container.
    ("runtime-group:process-tree:system.slice:docker:",
     lambda rest: f"Docker 容器 {rest}"),
    ("runtime-group:process-tree:", lambda rest: f"进程组 {rest}"),
    # The one the first NOC-style card was written against, and still the
    # shape most of this node's findings about a working session take. Same
    # split as the session tree above: the face is the session, the pid is in
    # the detail.
    #
    # THE WORD IS `登录会话` AND IT USED TO BE `Claude 会话` UNTIL 2026-09-24.
    # The kind in the ref is `f"{agent}-session"` where `agent` comes from
    # `graph.identify_agent`, which is a REGEX OVER PROCESS NAMES
    # (`graph.AGENT_RULES`) carrying `status: INFERRED`. CARDS-STYLE R16: a
    # `session-15413.scope` out of loginctl or a cgroup may only be called an
    # OS login session, and only an `AgentSessionProvider`-proven provider or
    # native identity may be displayed as 「Claude 会话」/「Codex 会话」 —
    # deciding an agent type from a ref prefix or a process name is forbidden
    # outright.
    #
    # That is not a technicality, and this node has the measurement. The live
    # `session-15413` group renders as `claude-session` because one process in
    # its 45 matched `/claude(-code)?$/`, and the tree it names is a VS Code
    # Server — so the old face printed a specific false statement about what
    # the subject was, and the operator reading the card asked which Claude
    # session it meant. What the rule says now is the half the kernel wrote
    # into the cgroup path: a login session, identified by its number.
    #
    # Both agent kinds get the same word on purpose. Naming Claude's sessions
    # wrongly and Codex's not at all would be one rule applied to two refs and
    # disagreeing with itself; `test_no_display_name_is_derived_from_an_agent`
    # enumerates `graph.AGENT_RULES` so a third agent added there fails that
    # test rather than silently falling through to the opaque `runtime-group:`
    # rule below.
    ("runtime-group:claude-session:",
     lambda rest: _session_tree(rest, "登录会话")),
    ("runtime-group:codex-session:",
     lambda rest: _session_tree(rest, "登录会话")),
    ("runtime-group:", lambda rest: f"运行时组 {rest}"),
    # `filesystem:mount:/data` → `文件系统 /data`. The `mount:` in the middle
    # is the producer's own word for how it found the filesystem and it is not
    # part of the name of anything; a card that printed it reads as "mount
    # colon slash data", which is a ref, not a place.
    ("filesystem:", lambda rest: f"文件系统 {rest.split('mount:', 1)[-1]}"),
    ("host:", lambda rest: f"整机 {rest}"),
    ("device:", lambda rest: f"设备 {rest}"),
    ("tunnel:", lambda rest: f"隧道 {rest}"),
    ("container:", lambda rest: f"容器 {rest}"),
    # A container the name index could not resolve. The ruling's last rung is
    # "缩短后的 opaque id", and without this rule the ref matched `runtime:`
    # below and rendered as 「受管运行时 container:08e734361c06…」 — the
    # producer's own `container:` token left inside a sentence, behind a word
    # that is about registrations. Nothing is invented here: `rest` is whatever
    # the ref carried, and `_abbreviate_ids` shortens it if it is a hex id.
    ("runtime:container:", lambda rest: f"容器 {rest}"),
    ("runtime:", lambda rest: f"受管运行时 {rest}"),
)


def _session_tree(rest: str, what: str) -> tuple[str, str]:
    """`<session>:pid<pid>` — the one shape a session tree ref has.

    Two forms, and the split between them is §5's: **the face names the thing a
    person acts on, the detail keeps the identifier that says which one.**

    The pid is not dropped — it is what distinguishes one of a session's trees
    from another, and it is the only handle a person has when they go to look.
    It is not on the face either. `登录会话 15413 的进程组（pid 1315723）` is 28
    characters of which the readable part is 10, and on a 390px screen it pushes
    the state line off the first screen. So the face carries `登录会话 15413`
    and the detail carries this same sentence with the pid in it. CARDS-STYLE.md
    R3. (Both forms shortened on 2026-09-24 when the agent word became
    `登录会话`; the old pair measured 15 and 33.)

    `what` is the caller's word for the kind of session, and the callers pass
    one word for every agent kind — see the R16 note at the rules above for why
    it may not be an agent's name.
    """
    session, _, pid = rest.partition(":pid")
    session = session.removeprefix("session-")
    face = f"{what} {session}"
    precise = (f"{what} {session} 的进程组（pid {pid}）" if pid
               else f"{what} {session} 的进程组")
    return face, precise


#: How much of an opaque id a card shows before eliding it. A docker container
#: id is 64 hex characters and a person reads the first twelve; the rest is
#: scannable-looking noise in the middle of a sentence. The full ref travels in
#: `subject.ref` and every other surface keys on that, not on this.
_ID_VISIBLE = 12
#: An opaque id, as opposed to a name. Two conditions, and both are needed: at
#: least 20 characters with no separator, and at least one digit. The length
#: alone would catch a long English word; the digit alone would catch
#: `session-15314`. `litellm-shared-postgres` fails on the separator and is
#: left alone, which is right — it is a name a person chose. This covers both
#: the 64-character hex docker id and the 26-character base32 form the same
#: runtime sometimes reports.
_OPAQUE_ID = re.compile(r"\b(?=[a-z0-9]*[0-9])[a-z0-9]{20,}\b")


def _abbreviate_ids(text: str) -> str:
    """Elide opaque ids, wherever they ended up.

    Applied to the finished display rather than to each rule's input, so an id
    embedded inside a longer ref is shortened by the same rule that shortens
    one standing alone — one rule, one place, and no rule that quietly misses
    the ids it was written for.
    """
    return _OPAQUE_ID.sub(lambda m: m.group(0)[:_ID_VISIBLE] + "…", text)


def subject_display(subject_ref: str | None, *, owner: dict | None = None,
                    names: dict | None = None) -> dict:
    """A subject ref, said in words, without inventing anything.

    The ref is the identity and it is what every other surface keys on; the
    display name is a convenience for one card and is never a substitute for
    it, so the ref travels alongside. `rule` records which expansion fired —
    a card whose subject came out as the raw ref should be visibly different
    from one that was understood, and only the rule name makes that checkable
    without diffing strings.

    The owner, when the producer attributed one, is carried through as the
    producer's own words. It is not used to *choose* the display name: §17
    forbids resolving a subject by anything but the subject, and a display name
    derived from a working directory or a home directory would be a claim about
    a login rather than a reading of the ref.

    `names` is an index built by `name_index` from evidence this node has
    already collected. When it has an answer, that answer wins and `rule` says
    which rung produced it; when it has none, the prefix table below applies
    and the subject falls back to the shortened ref. Nothing here reads a
    string's shape and calls the result a name — an id that no evidence
    explains stays an id, and the card says so by rendering it.
    """
    ref = str(subject_ref or "")
    named = _lookup_name(ref, names)
    if named is not None:
        return {"ref": ref or None, "text": _abbreviate_ids(named["text"]),
                "precise": None,
                "rule": named["rule"], "owner": dict(owner) if owner else None,
                "because": named.get("because"),
                "criticality": named.get("criticality")}
    text = None
    precise = None
    rule = None
    for prefix, expand in _SUBJECT_RULES:
        if ref.startswith(prefix):
            expanded = expand(ref[len(prefix):])
            # A rule may return the more precise form alongside the face one
            # (`_session_tree` does; see the pid rule there). Everything else
            # returns a single string and has no second form.
            text, precise = expanded if isinstance(expanded, tuple) else (expanded, None)
            rule = prefix
            break
    if text is None:
        # An unrecognised subject is printed whole. Guessing at its shape is
        # how `runtime-group:process-tree:session-150:pid28541` becomes
        # "会话 150" and stops naming the process the alert is actually about.
        text, rule = (ref or "（未记录对象）"), "verbatim"
    return {"ref": ref or None, "text": _abbreviate_ids(text),
            "precise": _abbreviate_ids(precise) if precise else None,
            "rule": rule,
            "owner": dict(owner) if owner else None, "because": None,
            "criticality": None}


#: The rungs of the name resolver, most authoritative first. The order is from
#: the CC execution contract: a logical service name beats a container name
#: beats a readable runtime/session name beats a shortened opaque id. It is
#: fixed here and the resolver cannot reorder it locally, because a surface that
#: preferred a container name over the registration would be a second answer to
#: "what is this".
NAME_RULES = ("declared-service", "container-name", "runtime-name")

#: Every spelling a container id reaches a subject ref in. Three are the cgroup
#: forms `attribution.container_of_cgroup` matches (`docker-<hex>.scope` under
#: `/system.slice`, `docker/<hex>`, `cri-containerd-<hex>.scope`); the fourth is
#: `docker:<hex>`, which is what `graph._describe` writes into a subject ref and
#: is therefore the one this resolver is actually asked about on this node. It
#: is written out rather than imported because `attribution.py` is not loadable
#: from this module (see the note beside `alerting` above), and a lookup that
#: missed the spelling the producers use would silently fall back to an id on
#: every alert — the failure that looks like "there was no name to find".
_CONTAINER_ID = re.compile(r"(?:docker[-/:]|cri-containerd-)([0-9a-f]{12,64})")

#: How much of a hex id is enough to identify a container. Docker's own short
#: form, and the length `containers.py` keeps in its evidence record.
_SHORT_HEX = 12


def _container_hex(ref: str) -> str | None:
    match = _CONTAINER_ID.search(ref)
    return match.group(1) if match else None


def _lookup_name(ref: str, names: dict | None) -> dict | None:
    """One subject, named from evidence — or not named, and saying so.

    Three ways in, tried in this order and no fourth:

      1. the ref as written is a key, which covers the two spellings the
         producers actually emit (`runtime:container:<name>` and `container:<name>`);
      2. the ref carries a cgroup hex id and a SHORTER id in the index is its
         unambiguous prefix — a 64-character scope id against the 12-character
         record `containers.py` keeps;
      3. nothing, which is an answer.

    The prefix case refuses on ambiguity rather than picking. Two containers
    whose ids share twelve characters are two containers, and a resolver that
    chose one would put a real name on the wrong thing — the failure mode that
    is worse than showing an id, because an id at least looks unresolved.
    """
    if not names:
        return None
    hit = names.get(ref)
    if isinstance(hit, dict):
        return hit
    hexed = _container_hex(ref)
    if not hexed:
        return None
    candidates = [entry for spelling, entry in names.items()
                  if spelling.startswith("runtime:container:")
                  and hexed.startswith(spelling[len("runtime:container:"):])]
    return candidates[0] if len(candidates) == 1 else None


def name_index(*, attribution: dict | None = None,
               registry: dict | None = None) -> dict:
    """Map container refs onto names, from evidence already collected.

    **This function runs nothing.** It reads two documents that some other
    surface collected — the `container attribution` document and the runtime
    registry — and joins them. A renderer that shelled out to `docker inspect`
    to decorate an alert would be a renderer whose card depends on the host
    being reachable at the moment somebody reads it, and would be a second,
    uncounted reader of the same truth.

    The rungs, and why the first is the first
    -----------------------------------------
    A container that a registration names takes the REGISTRATION's id as its
    display name, not the container's own. `litellm-shared-postgres` is what
    the declaration calls it; `docker-08e734361c06` is only where it happens to
    be running. The registration is the logical service and the container is an
    implementation of it, so the registration wins — and `resource_id` travels
    in the entry so a reader can get back to it.

    A container nothing registers keeps its Docker name, which is still a
    string a person typed, and is still an improvement on twelve hex
    characters. A container with neither is absent from the index, and the
    caller falls back to the ref.
    """
    index: dict[str, dict] = {}

    def put(spelling: str, name: str, rule: str, because: str,
            criticality=None, resource_id=None) -> None:
        index.setdefault(spelling, {
            "text": name, "rule": rule, "because": because,
            "criticality": criticality, "resource_id": resource_id})

    registered: dict[str, dict] = {}
    for resource in (registry or {}).get("resources") or []:
        if not isinstance(resource, dict):
            continue
        entry = {"resource_id": resource.get("id"),
                 "criticality": resource.get("criticality"),
                 "kind": resource.get("kind")}
        # The registration's own id is itself a spelling the register uses.
        # MEASURED, and the reason this line exists: this node's live alert row
        # for `container0-litellm` carries `subject_ref: "container0-litellm"`
        # with `resource_ref: null` — a BARE resource id, no `runtime:` prefix
        # and no `container:` prefix. Neither spelling the index used to build
        # matched it, so `_criticality_of` returned None for a registered
        # resource and the escalation rule could not fire on the live data it
        # was written for. Registered before the attribution pass so the
        # declaration wins the key over a bare container name.
        if entry["resource_id"]:
            put(str(entry["resource_id"]), str(entry["resource_id"]),
                "declared-service",
                f"登记表里的资源 id {entry['resource_id']}，主体引用用了它的"
                f"原样拼写",
                criticality=entry.get("criticality"),
                resource_id=entry.get("resource_id"))
        for probe in resource.get("probes") or []:
            if isinstance(probe, dict) and probe.get("container"):
                registered[str(probe["container"])] = entry

    for row in (attribution or {}).get("containers") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("container") or "").strip()
        if not name:
            # No name AND no hex is not a thing to be named. A row that got
            # here without one is left out rather than given a placeholder:
            # `<unnamed>` on a card is a claim, and the honest answer is the
            # ref the card already carries.
            continue
        registration = registered.get(name)
        if registration and registration.get("resource_id"):
            put(f"runtime:container:{name}", str(registration["resource_id"]),
                "declared-service",
                f"登记表把容器 {name} 声明为资源 "
                f"{registration['resource_id']}",
                criticality=registration.get("criticality"),
                resource_id=registration.get("resource_id"))
        else:
            put(f"runtime:container:{name}", name, "container-name",
                "容器自身的名字；没有任何登记表声明它", None, None)
        # The second spelling: `resource_anomaly` emits `container:<name>`
        # while the process layer emits `runtime:container:<name>`, and both
        # reach the same card. Aliasing them here keeps the two producers from
        # rendering the same container two different ways on one card.
        index.setdefault(f"container:{name}", index[f"runtime:container:{name}"])
        for anchor in (row.get("identity") or {}).get("anchors") or []:
            if isinstance(anchor, dict) and anchor.get("runtime_ref"):
                index.setdefault(str(anchor["runtime_ref"]),
                                 index[f"runtime:container:{name}"])
        hexed = str((row.get("evidence") or {}).get("id") or "").strip()
        if len(hexed) >= _SHORT_HEX:
            put(f"runtime:container:{hexed[:_SHORT_HEX]}",
                index[f"runtime:container:{name}"]["text"],
                index[f"runtime:container:{name}"]["rule"],
                index[f"runtime:container:{name}"]["because"],
                index[f"runtime:container:{name}"]["criticality"],
                index[f"runtime:container:{name}"]["resource_id"])
    return index


# ---------------------------------------------------------------------------
# Durations. The trap §4 warns about, in one function.
# ---------------------------------------------------------------------------

def open_duration(row: dict, *, at: str) -> dict:
    """How long the thing was (or has been) actually broken.

    Not `duration_seconds`. That field is `at - first_seen` — the age of the
    *alert*, which for a recovered row keeps growing after it recovered. On
    this node a live RECOVERED row reads `duration_seconds: 53358` (14.8 hours)
    for an alert that was open for 60 seconds, and a card that printed it would
    tell a person a minute-long flapping process ran for most of a day.

    So: a recovered row is measured to its `resolved_at`, an open one to now,
    and when neither end is parseable the answer is `None` and the card says so
    rather than falling back to the number that is wrong.
    """
    first = _parse(row.get("first_seen"))
    if first is None:
        return {"seconds": None, "basis": None,
                "why": "这一行没有可解析的 first_seen"}
    end = _parse(row.get("resolved_at")) if row.get("state") == "RESOLVED" else _parse(at)
    if end is None:
        return {"seconds": None, "basis": None,
                "why": ("这一行已恢复，却没有可解析的 resolved_at"
                        if row.get("state") == "RESOLVED"
                        else "没有可解析的 at 作为计量终点")}
    seconds = (end - first).total_seconds()
    if seconds < 0:
        # A negative duration is a clock disagreeing with a ledger, not an
        # event that ended before it began. Reported as unmeasured.
        return {"seconds": None, "basis": None,
                "why": "resolved_at 早于 first_seen：两个时钟不一致"}
    return {"seconds": seconds,
            "basis": ("resolved_at - first_seen" if row.get("state") == "RESOLVED"
                      else "now - first_seen")}


def _parse(when) -> datetime | None:
    if not when:
        return None
    try:
        parsed = datetime.fromisoformat(str(when))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def human_duration(seconds) -> str:
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分"
    if seconds < 86400:
        return f"{seconds // 3600} 小时 {(seconds % 3600) // 60:02d} 分"
    return f"{seconds // 86400} 天 {(seconds % 86400) // 3600} 小时"


# ---------------------------------------------------------------------------
# Certainty. §15 / `graph.freshness_of`: not knowing is never the same as
# knowing something good.
# ---------------------------------------------------------------------------

def certainty_of(row: dict) -> dict:
    """How much the row's own evidence is worth, as the producer recorded it.

    `provenance.freshness` is frequently null on this node — the live register
    is full of rows whose producer recorded a timestamp and no freshness — and
    the answer for those is UNKNOWN. It is NOT FRESH: `graph.freshness_of`'s
    rule is that a window that was never declared cannot make something fresh,
    and the same rule applies one layer up.
    """
    provenance = row.get("provenance") or {}
    stated = str(provenance.get("freshness") or "").upper() or None
    if stated in ("FRESH", "STALE"):
        return {"state": stated, "source": provenance.get("source"),
                "observed_at": provenance.get("observed_at"), "why": ""}
    return {"state": UNKNOWN, "source": provenance.get("source"),
            "observed_at": provenance.get("observed_at"),
            "why": ("生产者在这一行没有记录 freshness，因此这次读数是否仍然"
                    "新鲜无法判定" if not stated
                    else f"{stated!r} 不是本模型认识的 freshness 取值")}


# ---------------------------------------------------------------------------
# The alert card. §1 + §4.
# ---------------------------------------------------------------------------

#: §1: "最多三条高价值事件". Three because a card is read on a phone and the
#: fourth item is the one that makes the first three unscannable.
DEFAULT_LIMIT = 3

#: What is qualified to occupy one of the three. A recovered thing is not an
#: event a person must act on; it belongs on the recovery card, and letting it
#: into the alert card is how "已恢复" pushes "还没好" off the screen —
#: `alert_digest.py`'s `_KIND_ORDER` already ranks it last for this reason.
_ALERT_KINDS = (alert_digest.NEW, alert_digest.ESCALATED, alert_digest.REOPENED)


def alert_model(digest: dict, *, at: str, limit: int = DEFAULT_LIMIT,
                sample: bool = False, names: dict | None = None) -> dict:
    """At most `limit` events, each with the §4 field set, plus what was left out.

    §1 forbids the two shapes this is most likely to collapse into: the alert
    card must not reproduce the status card's three blocks, and it must not
    show the register's all-history OPEN/RESOLVED totals. What it may show is
    how much did NOT fit — §5: "静默不等于没事" is the misreading that a short
    card invites, and the count of omitted rows is the only thing standing
    between a person and that misreading.

    What qualifies as an EVENT changed, and that is the point of this round
    ----------------------------------------------------------------------
    `kind in (NEW, ESCALATED, REOPENED)` used to be the whole filter, and on
    this node that produced a card whose first three lines were three
    `HIGH_MEMORY_SUSTAINED` warnings about process trees — true readings, none
    of them a violated expectation, none of them actionable. The filter is now
    the declared attention axis: a row reaches the face only if a person may be
    interrupted for it.

    Everything the old filter admitted is still carried, under two counts that
    do not print events — `watch` (expectation present, below the face
    threshold) and `undecided` (a code the contract does not decide about,
    which is a hole in the declaration and is reported as one rather than
    levelled). §5's rule is unchanged: a short card is only honest with its
    omissions beside it.
    """
    rows = [r for r in (digest.get("rows") or []) if r.get("kind") in _ALERT_KINDS]
    events, watch, undecided = [], [], []
    for row in rows:
        attention = attention_of(row.get("code"),
                                 criticality=_criticality_of(row, names))
        if attention["level"] is None:
            undecided.append(row)
        elif on_the_face(attention["level"]):
            events.append((attention, row))
        else:
            watch.append((attention, row))
    # The axis order IS the sort order — the contract says so, and a card that
    # printed a REVIEW above an ACTION_REQUIRED would be re-ranking the axis at
    # the point where it is least visible.
    events.sort(key=lambda pair: AXIS.index(pair[0]["level"]))
    events = events[:limit]

    finding = integrity_finding(digest.get("held") or [])
    return {
        "schema": SCHEMA,
        "card": "alert",
        "sample": bool(sample),
        "sample_mark": SAMPLE_MARK if sample else None,
        "at": at,
        "device_ref": digest.get("device_ref"),
        "attention_rule": ATTENTION_RULE,
        "events": [_event_of(row, at=at, names=names, attention=attention)
                   for attention, row in events],
        "integrity": finding,
        "watch": {
            "count": len(watch),
            "by_code": _count_by(watch),
            "note": ("预期仍在，但级别不到打断人的程度；计数在此，不占卡面。"
                     "完整条目在 ServerOS 登记表里可查。"),
        },
        "undecided": {
            "count": len(undecided),
            "codes": sorted({str(r.get("code")) for r in undecided}),
            "note": ("这些 code 没有在 notification-contract.yaml 的 attention "
                     "表里被决定过。未决定不等于不重要，也不等于有事——它是"
                     "声明里的一个洞，如实列出。"),
        },
        "recovery": _recovery_summary(digest),
        "omitted": {
            "rows": max(0, len(rows) - len(events) - len(watch) - len(undecided)),
            # `more` is the digest's own count of rows past the card's own cap;
            # kept separate from the rows this model dropped, because the two
            # caps are different numbers and adding them would invent a third.
            "beyond_card_cap": int(digest.get("more") or 0),
            "backlog": int(digest.get("backlog") or 0),
            "silent_resolutions": int(digest.get("silent_resolutions") or 0),
            "note": ("未进卡面不等于不存在；全部条目在 ServerOS 登记表里可查"),
        },
        "severity_counts": dict(digest.get("severity_counts") or {}),
        "limit": limit,
    }


def _count_by(pairs: list[tuple]) -> dict:
    out: dict[str, int] = {}
    for _, row in pairs:
        code = str(row.get("code") or "")
        out[code] = out.get(code, 0) + 1
    return out


def _criticality_of(row: dict, names: dict | None) -> str | None:
    """The registration's own criticality for this row's subject, or nothing.

    Reached through the name index because that is the only thing in this
    module that has already joined a subject ref to a registration. It returns
    `None` for a subject nothing registers, which the escalation rule reads as
    "do not escalate" — never as "not critical".
    """
    found = _lookup_name(str(row.get("subject_ref") or ""), names)
    return (found or {}).get("criticality")


def _recovery_summary(digest: dict) -> dict:
    """One line's worth of recovery, so the alert face is the lifecycle face.

    Three cards became two: the recovery card still exists and is still what a
    quiet batch of recoveries renders as, but a recovery that arrives in a
    batch which also has news is a line on that card rather than a second
    message. The counting rule is `recovery_model`'s and is not restated here —
    only rows a person was actually told about are recoveries at all.
    """
    recovered = [r for r in (digest.get("rows") or [])
                 if r.get("kind") == alert_digest.RECOVERED]
    announced = [r for r in recovered if r.get("was_announced")]
    return {"announced": len(announced),
            "never_announced": len(recovered) - len(announced),
            "subjects": [subject_display(r.get("subject_ref"))["text"]
                         for r in announced[:DEFAULT_LIMIT]]}


def _event_of(row: dict, *, at: str, names: dict | None = None,
              attention: dict | None = None) -> dict:
    code = row.get("code")
    severity = str(row.get("severity") or "")
    definition = definition_for(code)
    duration = open_duration(row, at=at)
    subject = subject_display(row.get("subject_ref"), owner=row.get("owner"),
                              names=names)
    # AC19. `<服务名> 不可用` instead of a sentence that is true of every
    # mismatch. The name comes from `subject_display`, so it is the same name
    # the title line above carries and the same one the resolver produced from
    # collected evidence — never a string this function composed.
    #
    # A caveated comparison does NOT get the declared word. 不可用 asserts a
    # direction — the thing is not there — and the caveat on a process match is
    # exactly the statement that this comparison cannot tell "gone" from
    # "running behind a visibility boundary this uid cannot cross"
    # (`desired.py::_process_match_caveat`). The card would be resolving, in one
    # word, the ambiguity the evidence block spends a paragraph refusing to
    # resolve. A reader gets the generic sentence and the caveat in the detail.
    caveat = row.get("caveat")
    said = None if caveat else comparison_word(
        code, desired=row.get("desired_state"),
        actual=row.get("actual_derived_state"))
    what_text = (f"{subject['text']} {said}" if said else definition["what"])
    return {
        "ref": row.get("key"),
        "code": code,
        "event": row.get("kind"),
        "attention": attention or attention_of(code),
        "what": {
            "text": what_text,
            "definition": definition["state"],
            # The short noun the event line wears after its subject
            # (`登录会话 15413 · 遗留进程组`). Carried here and not looked up
            # again in the renderer: the renderer may not read the catalogue
            # (that is `presentation`'s job), and a second reader of one file is
            # a second place its contents can be interpreted. Null on a code the
            # catalogue does not define, which is what makes `_event_title` fall
            # back to the subject alone rather than to an invented noun.
            "label": definition.get("label"),
            # The comparison's two sides, when the row carries them, so a
            # reader can check what was compared against what without opening
            # the register. Null on every row that has no comparison. The
            # caveat rides along so that "why is this one generic when that one
            # says 不可用" is answerable from the model rather than from the
            # register.
            "compared": ({"desired": row.get("desired_state"),
                          "actual": row.get("actual_derived_state"),
                          "actual_raw": row.get("actual_raw"),
                          "caveat": caveat}
                         if row.get("desired_state") else None),
            # The code travels with every event whatever the catalogue says.
            # §5 removes the raw code from the card FACE, not from the record:
            # the detail keeps it, and it is the identity every other surface
            # keys on.
            "code": code,
            "why": definition.get("why"),
        },
        "subject": subject,
        "severity": {"level": severity,
                     "word": row.get("severity_word")
                             or alerting.severity_words().get(severity, severity.upper())},
        "impact": {
            "text": definition.get("impact") or IMPACT_UNCONFIRMED,
            "confirmed": bool(definition.get("impact")),
            "measured": row.get("resource_ref"),
        },
        "certainty": certainty_of(row),
        "action": action_for(code, severity),
        "times": {"first_seen": row.get("first_seen"),
                  "last_seen": row.get("resolved_at") or at,
                  "duration": duration,
                  "duration_text": human_duration(duration["seconds"])},
        "occurrences": row.get("occurrence_count"),
        "evidence": alerting.detail_for(_for_detail(row, at=at), at=at),
    }


#: The register fields `alerting.detail_for` reads out of `measured`, in the
#: shape the digest row carries them. Rebuilt here rather than passed through
#: whole because the digest row is a projection: `measured` on the register
#: holds whatever the producing detector put there, and handing that to
#: `detail_for` would make the evidence block depend on arbitrary per-code
#: payload rather than on a named set of fields.
_DETAIL_MEASURED = ("desired_state", "actual_state", "actual_derived_state",
                    "actual_raw", "caveat")


def _for_detail(row: dict, *, at: str) -> dict:
    """The row as `alerting.detail_for` needs it.

    `detail_for` reads `measured`, `provenance`, `window`, `occurrence_count`
    and `first_seen` — the fields the *register* carries. A digest row is the
    projected subset, so the evidence block is rebuilt from what the row does
    have rather than being handed a shape it would silently read as empty.
    `resource_ref` and `owner` are passed through under their own names so the
    block can name them.

    The comparison is in that rebuild because dropping it was not neutral.
    `_measurement_line` falls back to the alert's `why` when `measured` holds
    none of the numeric keys, and for §15's code the fallback sentence says the
    two sides disagree without saying what either of them said — on the one
    code whose entire content is those two words. The fields are listed rather
    than copied wholesale so that a detector adding a key to `measured` does
    not thereby add a line to the card.
    """
    measured = {"ownership": row.get("owner")} if row.get("owner") else {}
    for field in _DETAIL_MEASURED:
        if row.get(field) is not None:
            measured[field] = row[field]
    return {
        "code": row.get("code"),
        "severity": row.get("severity"),
        "first_seen": row.get("first_seen"),
        "occurrence_count": row.get("occurrence_count"),
        "window": None,
        "provenance": row.get("provenance") or {},
        "measured": measured,
        "resource_ref": row.get("resource_ref"),
        "subject_ref": row.get("subject_ref"),
        "state": row.get("state"),
    }


# ---------------------------------------------------------------------------
# The recovery card. §1 + §5.
# ---------------------------------------------------------------------------

def recovery_model(digest: dict, *, at: str, limit: int = DEFAULT_LIMIT,
                   sample: bool = False) -> dict:
    """Problems a person was TOLD about, and that have since recovered.

    The rule is §5's — "已发送的问题后来有肯定恢复证据，才发恢复" — and it has a
    sharp edge that this function exists to hold:

    **`kind == RECOVERED` is not the same as "a problem you were told about
    recovered".** `alert_digest.deltas` reports a RECOVERED row in two cases:
    the identity was announced, or it opened and closed entirely after the
    baseline. The second case is a real event and the digest is right to carry
    it — it is the only chance anybody gets to hear about that alert at all —
    but a person cannot be told a thing recovered when they were never told it
    was broken, and a recovery card built from the first reading would open
    with a wall of ✅ about incidents nobody ever saw.

    On this node that distinction is not theoretical: of 1124 RECOVERED rows in
    the live register, 13 were announced. The other 1111 are counted here and
    kept off the card.
    """
    recovered = [r for r in (digest.get("rows") or [])
                 if r.get("kind") == alert_digest.RECOVERED]
    announced = [r for r in recovered if r.get("was_announced")]
    never = [r for r in recovered if not r.get("was_announced")]

    items = [_recovery_of(row, at=at) for row in announced[:limit]]
    return {
        "schema": SCHEMA,
        "card": "recovery",
        "sample": bool(sample),
        "sample_mark": SAMPLE_MARK if sample else None,
        "at": at,
        "device_ref": digest.get("device_ref"),
        "items": items,
        "counts": {"recovered": len(recovered), "announced": len(announced),
                   "never_announced": len(never),
                   "shown": len(items)},
        "omitted": {
            "announced_beyond_limit": max(0, len(announced) - len(items)),
            "never_announced": len(never),
            "note": ("从未告知过的条目不计入恢复：人不能被通知一个他从来没收到过"
                     "的坏消息"),
        },
        "limit": limit,
    }


def _recovery_of(row: dict, *, at: str) -> dict:
    duration = open_duration(row, at=at)
    return {
        "ref": row.get("key"),
        "code": row.get("code"),
        "what": definition_for(row.get("code")),
        "subject": subject_display(row.get("subject_ref"), owner=row.get("owner")),
        "severity": {"level": row.get("severity"), "word": row.get("severity_word")},
        "times": {"first_seen": row.get("first_seen"),
                  "resolved_at": row.get("resolved_at"),
                  "open_for": duration,
                  "open_for_text": human_duration(duration["seconds"])},
        # What makes this a recovery rather than a disappearance. §5: "观测失联
        # 或权限丢失不是恢复" — a resolved row is one the detector closed on
        # positive evidence, and `state` is the field that says so.
        "basis": {"state": row.get("state"),
                  "note": "由探测器在有肯定证据时关闭，不是观测失联"},
        "occurrences": row.get("occurrence_count"),
    }


# ---------------------------------------------------------------------------
# The status card. §2 + §3.
# ---------------------------------------------------------------------------

#: §2. The three blocks, in this order, and the order is fixed: the sections
#: a person can act on come before the machine's totals, and the shared gateway
#: — which is nobody's to restart — comes last and is labelled as shared.
SECTIONS = ("mine", "host", "gateway")

#: §3. What the exporter is allowed to be. A gateway binding names a service;
#: it is not a container name and not an owner, because §2 forbids selecting
#: the shared gateway either way.
GATEWAY_BOUND = "BOUND"
GATEWAY_UNBOUND = "UNBOUND"


def gateway_section(snapshot: dict, *, binding: dict | None) -> dict:
    """The shared gateway, chosen by binding and by nothing else.

    §2 is unusually specific here — "共享网关由 service binding 选择，不因名称或
    owner=ubuntu 排除" — and the reason is that both of the convenient
    selectors are wrong in a way that shows up later: a container name is
    reused across deployments, and an owner's home directory says who happens
    to have started it rather than what the service is. The live monitor
    selects the shared database by compose project coinciding with a working
    directory prefix, which is the shape this rule exists to replace.

    With no binding the answer is `UNBOUND`: *named*, and not guessed at. §3
    also requires the personal container0 to stay out of this section — it
    belongs under "my resources" with the rest of what this account runs — so
    an unbound gateway renders as a hole rather than being filled from
    whatever container happens to look like a gateway.
    """
    if not binding:
        return {
            "state": GATEWAY_UNBOUND,
            "why": ("没有任何 service binding 指明本节点的共享网关。§2 禁止按"
                    "容器名或按 owner 选它，因此这一栏报为未绑定，而不是拿一个"
                    "长得像的对象填上"),
            "endpoint": None, "probe": None, "providers": [], "maintainer": None,
        }
    gateway = snapshot.get("gateway") or {}
    return {
        # `state` here answers ONE question — is there a binding — and it must
        # keep answering only that. Two keys, two questions, and conflating
        # them was a real defect rather than a hypothetical one: `_gateway_state`
        # used to read the card's state out of this key, so a bound gateway
        # rendered the word "BOUND" as its state. `env_health` does not know
        # that word, so a bound gateway was neither faulted nor unread — it fell
        # through to "三项均已读到且无异常" and a gateway in ATTENTION reported
        # as fine. Seven words of green over a measured fault, which is exactly
        # the false-green §6 forbids.
        "state": GATEWAY_BOUND,
        "why": "",
        "service_ref": binding.get("service_ref"),
        "binding_source": binding.get("source"),
        # §2: "也不因为'负责人是我'获得重启权". The maintainer is a display
        # field. It is not an authorization and it is not an owner.
        "maintainer": gateway.get("maintainer") or binding.get("maintainer"),
        "endpoint": gateway.get("endpoint"),
        "probe": gateway.get("probe"),
        "providers": list(gateway.get("providers") or []),
        # What was OBSERVED about the gateway, as distinct from whether anyone
        # wrote down which gateway it is. OK / ATTENTION / UNKNOWN, computed by
        # whoever could reach the gateway's own accounting.
        "observed_state": gateway.get("state"),
        # The window every count above is a count over. Carried because the
        # renderer prints it and, before this line, could not: `_gateway_state`
        # read `window_minutes` from the section it is handed, and this function
        # did not put it there, so `窗口 N 分钟` was a line that could never
        # appear on any card. Dead code that renders as a shorter card rather
        # than as an error is the kind that stays dead.
        "window_minutes": gateway.get("window_minutes"),
    }


def env_health(sections: dict, *, at: str, gaps: list | None = None) -> dict:
    """§2's top line: is this environment usable, and how good is the reading?

    Two separate answers, and §2 draws them as two — "环境可用性：有一项待确认"
    next to "观测：部分新鲜". Collapsing them into one word is the failure this
    function exists to prevent: an environment can be perfectly usable on a
    partial reading, and the two facts have different repairs.

    **UNKNOWN is never green.** §6: "未知用中性而非正常绿". An unread section
    makes the verdict `UNCONFIRMED`, not `OK` — the absence of a fault signal
    is not the presence of a good one, and this is the same rule
    `graph.freshness_of` applies to a single timestamp, applied to a summary.
    """
    gaps = list(gaps or [])
    states = {name: (sections.get(name) or {}).get("state") for name in SECTIONS}
    unread = sorted(n for n, s in states.items() if s in (None, UNKNOWN))
    faulted = sorted(n for n, s in states.items()
                     if s in ("DEGRADED", "NOT_READY", "DOWN", "CRITICAL",
                              "ATTENTION", GATEWAY_UNBOUND))

    if faulted and unread:
        verdict, word = UNCONFIRMED, "有项目需处理，且有项目未读到"
    elif faulted:
        verdict, word = ATTENTION, "有项目需处理"
    elif unread:
        # Named rather than counted, because "一项未读到" sends the reader
        # hunting through three blocks for which one, and the card already
        # knows.
        verdict, word = UNCONFIRMED, "有项目尚未读到：" + "、".join(unread)
    else:
        verdict, word = OK, "三项均已读到且无异常"

    observation = ("部分新鲜" if gaps or unread else "新鲜")
    return {
        "verdict": verdict,
        "verdict_text": word,
        "observation": observation,
        "observation_text": (
            f"有 {len(gaps)} 项采集缺口" if gaps else
            ("有项目未读到" if unread else "三项均已读到")),
        "sections_read": sorted(n for n, s in states.items() if s not in (None, UNKNOWN)),
        "sections_unread": unread,
        "gaps": gaps,
        "at": at,
    }


def status_model(snapshot: dict, *, at: str, binding: dict | None = None,
                 sample: bool = False) -> dict:
    """The three blocks and the verdict above them.

    `snapshot` is already-gathered facts and this function gathers nothing —
    the gathering is `serverosctl`'s and stays there. What the model adds is
    the part that was missing: the one-line verdict, the binding-selected
    gateway, and the refusal to let a section that was not read render as a
    section that was fine.
    """
    gaps = list(snapshot.get("gaps") or [])
    sections = {
        "mine": _section("mine", snapshot.get("mine"), gaps),
        "host": _section("host", snapshot.get("host"), gaps),
        "gateway": _gateway_state(gateway_section(snapshot, binding=binding)),
    }
    mine = snapshot.get("mine") or {}
    return {
        "schema": SCHEMA,
        "card": "status",
        "sample": bool(sample),
        "sample_mark": SAMPLE_MARK if sample else None,
        "at": at,
        "node_label": snapshot.get("node_label"),
        "node_host": snapshot.get("node_host"),
        "env_health": env_health(sections, at=at, gaps=gaps),
        "sections": sections,
        "order": list(SECTIONS),
        "schedule": _schedule(snapshot),
        "coverage": {
            # §3. The shared gateway and the personal container0 are different
            # things and the card says which of them each number came from.
            "personal_container0": "mine",
            "shared_gateway": "gateway",
            "note": ("共享网关与个人 container0 分开呈现；同一台机器上的两个"
                     "对象，不是同一件事"),
        },
    }


def _schedule(snapshot: dict) -> dict:
    """Which day this card belongs to, and why it was allowed to exist now.

    The snapshot carries this block when `card_source.snapshot` computed it from
    an instance's calendar declaration. When it does not — a fixture written
    before this block existed, or a caller that gathered without reading the
    calendar at all — the answer is UNKNOWN and NOT "a workday", so the card
    prints the same `calendar=UNKNOWN` a node with no imported calendar prints.
    Two different causes, one honest sentence each: the alternative is a card
    that silently implies a rhythm nobody verified, which is the thing
    `docs/MONITORING-SCOPE-RECOVERY-CARDS.md` §9 forbids in the words «不冒充
    Mon–Fri 官方工作日».
    """
    payload = snapshot.get("schedule")
    if isinstance(payload, dict) and payload.get("says"):
        return payload
    return {
        "kind": UNKNOWN,
        "calendar": "UNREAD",
        "day_ref": None,
        "in_window": None,
        "route": "NO_ROUTINE_BROADCAST",
        "reason": "CALENDAR_UNKNOWN",
        "says": ("本次采集没有读工作日历，因此不判断今天是不是工作日、也不给出"
                 "播报窗口（calendar=UNKNOWN）"),
    }


def _section(name: str, payload: dict | None, gaps: list) -> dict:
    payload = payload or {}
    state = payload.get("state")
    if state is None:
        return {"name": name, "state": UNKNOWN, "lines": [],
                "why": "这一栏没有被采集，因此不对它作任何断言"}
    return {"name": name, "state": state, "lines": list(payload.get("lines") or []),
            "why": payload.get("why") or "", "detail": payload.get("detail") or {}}


def _gateway_language() -> dict:
    """The declared wording for the gateway block, or an empty declaration.

    Same shape as `codes:` above and for the same reason: the sentence a person
    reads is declared, not generated, and it lives next to the other declared
    sentences rather than inside whichever module happened to measure the
    number.
    """
    return _catalogue().get("gateway") or {}


def _gateway_provider_line(provider: dict) -> str | None:
    """One provider family, as a sentence, from the declared wording.

    An evidence provider hands over MEASUREMENTS -- a family, four counts, a
    p95, a state word. The sentence is built here, from `card-definitions.yaml`,
    so that the wording of a gateway line has exactly one home. The shape this
    replaces was one `text` string per provider, written by the measurer, which
    is where two presenters begin to disagree about the same number.

    Two fallbacks, both deliberate and both narrow:

      * a provider with no `family` is an older or differently-shaped provider,
        and whatever `text` it carries is returned as-is. Dropping it would
        hide a reading to win an argument about formatting;
      * a template with a slot this module does not fill is a bug in the
        catalogue, and a card is the wrong place to raise it. The measurement
        itself is returned, which is at least true.

    No cause words: the same ban as `codes:`, enforced by the same test. In
    particular `requests == 0` must not become "the provider is down" -- this
    node has had a gateway whose container was Up while every route was dead.
    """
    if not isinstance(provider, dict):
        return None
    language = _gateway_language()
    templates = language.get("providers") or {}
    family = str(provider.get("family") or "").strip()
    if not family:
        text = provider.get("text")
        return str(text) if str(text or "").strip() else None
    state = str(provider.get("state") or "").strip() or UNKNOWN
    state_word = (language.get("state_words") or {}).get(state) or state

    def _count(name: str) -> int:
        try:
            return int(provider.get(name) or 0)
        except (TypeError, ValueError):
            return 0

    requests = _count("requests")
    # A window with no traffic is its own sentence rather than the line above
    # with a zero in it: "nobody used it" and "every request worked" are
    # different readings, and only one of them is good news.
    template = (templates.get("no_traffic") if requests <= 0
                else templates.get("line")) or "{family}"
    p95 = ""
    ms = provider.get("p95_ms")
    if requests > 0 and isinstance(ms, int) and ms > 0 and templates.get("p95"):
        p95 = str(templates["p95"]).format(ms=ms)
    try:
        return str(template).format(
            family=family, state=state_word, requests=requests,
            ok=_count("ok"), failed=_count("failed"), p95=p95)
    except (KeyError, IndexError, ValueError):
        return f"{family}：{requests} 次请求"


def _gateway_probe_line(probe: dict) -> str | None:
    """The functional probe's line, from the declared wording.

    The declared `probes` table is keyed by the probe's own state word. A state
    word the table does not declare is rendered verbatim rather than blanked:
    an undeclared word is still a reading, and dropping it would turn "the
    probe said something I do not know" into "the probe said nothing".
    """
    if not isinstance(probe, dict):
        return None
    declared = (_gateway_language().get("probes") or {})
    state = str(probe.get("state") or "").strip()
    if state and declared.get(state):
        return str(declared[state])
    text = probe.get("text")
    if str(text or "").strip():
        return str(text)
    # The word itself, not None: an undeclared state word is still a reading,
    # and returning nothing would render "the probe said something this card
    # does not know" as "the probe said nothing".
    return state or None


def _gateway_state(section: dict) -> dict:
    if section["state"] == GATEWAY_UNBOUND:
        return {"name": "gateway", "state": UNKNOWN, "lines": [],
                "why": section["why"], "detail": section}
    gateway = section
    probe = gateway.get("probe") or {}
    lines = []
    if gateway.get("endpoint"):
        lines.append(f"本机入口 {gateway['endpoint']}")
    probe_line = _gateway_probe_line(probe)
    if probe_line:
        lines.append(probe_line)
    for provider in gateway.get("providers") or []:
        line = _gateway_provider_line(provider)
        if line:
            lines.append(line)
    # The window is what every count above is a count *over*, so it is printed
    # once, under them. A rate with no window is a number with no denominator.
    window_template = (_gateway_language().get("providers") or {}).get("window")
    window = gateway.get("window_minutes")
    if window and window_template:
        lines.append(str(window_template).format(minutes=window))
    if gateway.get("maintainer"):
        lines.append(f"负责人 {gateway['maintainer']}")
    # `observed_state`, NOT `state`. `state` on this section says whether a
    # binding named a gateway; this one says how the gateway is. Reading the
    # first here made every bound gateway report the word "BOUND" as its state,
    # which `env_health` does not recognise and therefore never faulted.
    # Absent readings stay UNKNOWN and never OK.
    return {"name": "gateway", "state": gateway.get("observed_state") or UNKNOWN,
            "lines": lines, "why": "", "detail": gateway}


if __name__ == "__main__":  # pragma: no cover - a hand check, not an entry point
    print(f"{SCHEMA}: {len(known_codes())} codes, "
          f"{len(_catalogue().get('codes') or {})} defined")
