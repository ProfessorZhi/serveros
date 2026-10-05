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
import unicodedata
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


def _card_day():
    """The day store's reader, for the one question only it can answer.

    Loaded lazily rather than at import: `card_day` reads a business calendar
    and a delivery ledger off disk at module scope in some of its own paths, and
    this module is imported by every card render. The direction is one-way —
    `card_day` does not import this module — so the pairing is acyclical, and
    the lazy call is about import cost rather than about a cycle.
    """
    return _load("serveros_runtime_card_day", "card_day.py")
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
#:
#: WHY IT IS ONE WORD AND NOT THREE. The mark rides on the card TITLE, and the
#: title is the widest-cost row on the card: `.hdr h2` lays out at 16px/700,
#: which `fit-candidates.py` measures at 8.5–9.5px per half-width cell against
#: the 7.1–8.4px the 15px rows cost. `示例 / 尚未发送` is eight cells that the
#: body rows would not notice and the title cannot afford: with it the marked
#: title measured 407.9px in a 360px box and wrapped to two lines, while the
#: same card without the mark measured 275.2px and took one. A preview that
#: wraps where production does not is §16's 「预览与渲染完全一致» broken by the
#: one thing on the card that exists only in a preview — so the mark gives way,
#: and not the finding. `示例` is the same word `card_scenarios` prints in its
#: banner, and it is the word that carries the requirement: a reader must not
#: take a sample for a reading. The measured pool for every row this card has
#: is `_measure/fit-candidates.py`.
SAMPLE_MARK = "示例"

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


#: The two YAML documents this module reads, parsed once per REVISION of the
#: file rather than once per question asked of it.
#:
#: WHY THIS EXISTS. `card alert` spent **64 of its 65 seconds** re-parsing the
#: same 46 KB `notification-contract.yaml`. `alert_model` asks `attention_of`
#: once per row, and every question down that path — `attention_policy`,
#: `code_expectations`, `on_the_face`, `attention_word` — read the contract
#: again from the top. Measured on this node 2026-10-04 against the live
#: register: 832 rows entering the loop, **3,584 parses**, 17.9 ms each =
#: 64.15 s, for a card that came out with no events on it.
#:
#: This is the same defect `alert_digest.may_interrupt` records in its own
#: words — "Read once, not once per row" — found there on 2026-09-24, fixed
#: there, and never carried across to the module that asks the question most.
#:
#: KEYED ON IDENTITY, NOT ON TIME. The key is the file's inode, size and
#: nanosecond mtime, so a rewritten file IS re-read and a same-size rewrite is
#: not served a stale parse. One entry per path, so the cache cannot grow, and
#: what it holds is always the parse of the revision currently on disk.
#:
#: THE DOCUMENT RETURNED IS SHARED, and every reader here treats it as
#: read-only: `attention_policy`, `code_expectations`, `definition_for`,
#: `comparison_word` and `action_for` all read it with `.get` and none assigns
#: into it. A copy per call would put the whole cost back.
_PARSE_CACHE: dict[str, tuple[tuple, dict]] = {}


def _parsed(path: Path) -> dict:
    """One YAML document, parsed once per revision of the file on disk."""
    try:
        stat = path.stat()
        key: tuple | None = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
    except OSError:
        # An unreadable path is not a cacheable one. Fall through and let
        # `read_text` raise the error the callers are written against, rather
        # than answering from a parse that no longer describes anything.
        key = None
    entry = _PARSE_CACHE.get(str(path))
    if key is not None and entry is not None and entry[0] == key:
        return entry[1]
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if key is not None:
        _PARSE_CACHE[str(path)] = (key, document)
    return document


def _catalogue() -> dict:
    return _parsed(DEFINITIONS)


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
    return _parsed(CONTRACT)


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

    Four keys, all required. A contract missing one of them is a contract in
    which this finding would have to be described by whoever renders it — which
    is the failure the block exists to prevent — so it raises instead.

    `collector` is the fourth and it is not decoration. The action tells a
    person to check the collection side, and «采集侧» alone is the card
    pointing at something unnamed; §4 keeps the names off the first screen, so
    what the word refers to is declared here and printed one panel down, where
    the reader who wants to act on the sentence can find out what to look at.
    """
    block = ((attention_policy().get("monitoring_integrity") or {}))
    keys = ("what", "impact", "action", "collector")
    missing = [k for k in keys if not block.get(k)]
    if missing:
        raise PresentationError(
            "notification-contract.yaml alerts.attention.monitoring_integrity "
            f"does not declare {', '.join(missing)}; the finding cannot be "
            "described without it")
    return {k: str(block[k]) for k in keys}


def integrity_finding(held_rows: list[dict], *, at: str | None = None) -> dict | None:
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

    `at` is what makes §4's 「从何时起·持续多久」 answerable, and it is the card's
    own instant rather than `datetime.now()`: the age this block prints has to
    be measured from the same clock as the `采集` stamp two panels down, or a
    card read on a phone and a card read a minute later would disagree about
    how long the loss has lasted. Every row here carries a real `first_seen` —
    `alert_digest` only writes a held row from an alert that is OPEN in the
    register — so the earliest of them is an observation point and not an
    interval this function invented (§3).
    """
    findings, history, undecided = [], [], []
    for row in held_rows or []:
        kind = row.get("hold_kind")
        if kind is not None and kind not in LOST_OBSERVATION:
            # Somebody looked and the value is still up — the alert is held
            # because it has not come back below its floor, not because
            # anything became unobservable. We are being told the same thing we
            # were told last hour, and nothing is missing.
            continue
        found = expectation_of(row.get("code"))
        if not found["decided"]:
            # §八-B. NOT the same fact as `history`, and it was being filed as
            # one. `history` is "the contract decided this code carries no
            # expectation, so not seeing it is a fact about our looking".
            # An undecided code is "nobody decided", which is the sentence
            # `attention_of` refuses to collapse — and filing it under a note
            # that says 没有任何东西声明它们应当存在 makes the node ASSERT an
            # expectation-free status for a code it never looked at. That is
            # the incomplete admission catalogue the round is about, and it
            # belongs in its own bucket with its own wording.
            undecided.append(row)
            continue
        (findings if found["kind"] == "declared" else history).append(row)
    if not findings and not undecided:
        return None
    codes: dict[str, int] = {}
    for row in findings:
        code = str(row.get("code") or "")
        codes[code] = codes.get(code, 0) + 1
    undecided_codes = sorted({str(r.get("code") or "") for r in undecided})
    # The wording comes from the contract, not from here. This function decides
    # WHICH rows are a finding and HOW MANY; the sentence a person reads is
    # declared in `alerts.attention.monitoring_integrity`, so changing it is a
    # change in the contract and nowhere else. `_integrity_words()` refuses a
    # contract that has lost them rather than falling back to a string built
    # here, because a fallback is how the second authority gets created.
    words = _integrity_words()
    # §4's 「现在还存在吗」 and 「从何时起·持续多久」, as the two facts they are.
    #
    # The state is UNCONFIRMED and it is not a verdict this function reached:
    # every row here is OPEN in the register and held because nobody graded it,
    # which is `_now_line`'s UNCONFIRMED branch read on a set instead of on one
    # alert. It is NOT 「仍在发生» and the difference is the whole reason both
    # spellings exist — 仍在发生 would say the machine is still in some
    # condition, and what is true is that nothing looked.
    #
    # The instant is the EARLIEST `first_seen` of the rows being counted, and
    # the earliest is the one to print because it is the answer to «since
    # when». A maximum would date the newest subject and make a loss that
    # started a fortnight ago read as one that started this morning. Rows with
    # no parseable `first_seen` are skipped rather than dragging the answer to
    # a sentinel — an unparseable stamp is one row's bad data, not a reason to
    # print 未知 over thirteen rows that dated themselves.
    since, seconds = None, None
    for row in findings:
        moment = (row.get("first_seen") or "")
        if not moment:
            continue
        if since is None or str(moment) < since:
            since = str(moment)
    if since and at:
        opened = _parse(since)
        now = _parse(at)
        if opened and now:
            seconds = max(0, int((now - opened).total_seconds()))
    return {
        "kind": "monitoring-integrity",
        "current": {"state": "UNCONFIRMED", "since": since,
                    "since_seconds": seconds},
        "attention": attention_of(findings[0].get("code")) if findings else
                     {"level": None, "rule": "undecided", "expectation": None,
                      "escalated": False,
                      "because": ("这条发现本身来自未定级的 code，契约没有"
                                  "决定它的注意力级别")},
        "count": len(findings),
        "by_code": codes,
        "text": words["what"].format(count=len(findings)),
        "impact": words["impact"],
        "action": words["action"],
        "collector": words["collector"],
        "not_a_finding": {
            "count": len(history),
            "note": ("另有无预期声明的历史主体同样未被观测到。契约已经决定"
                     "它们不携带预期，因此「测不到」不是故障，不计入本条。"),
        },
        "undecided": {
            "count": len(undecided),
            "codes": undecided_codes,
            "note": ("这些 code 没有任何东西在 attention 表里决定过，因此"
                     "既不能说「测不到是故障」也不能说「测不到不是故障」。"
                     "未决定不等于不重要——它是准入目录上的一个洞，如实列出。"),
        },
    }


# ---------------------------------------------------------------------------
# Width. §2's line budget, measured rather than guessed.
# ---------------------------------------------------------------------------

def display_cells(text: str) -> int:
    """How many half-width cells a string takes up.

    THE UNIT IS THE CELL AND NOT THE CHARACTER, and the difference is the whole
    reason this exists. `len()` counts `磁盘 / 70.2%` as 11 and `磁盘使用率` as 5,
    and the second is wider on the screen than the first. A budget written in
    characters therefore lets the wide line through and cuts the narrow one,
    which is the failure this function was written for: the first version of
    §2's face line used `truncate: 34` characters and rendered `CPU 占整机 /
    1.6%` and `磁 / 盘 / 70.2%` — a unit broken off its number, which is the
    one shape §6 calls out by name.

    A cell is still not a pixel, and this is not a claim that it is. The
    budgets in `card-definitions.yaml` were calibrated against the real thing:
    `card_geometry.py` loads the rendered card in the browser the screenshots
    come from and reads back the width of every line. Measured there, the widest
    strings that fit their row were 304px of 358px at weight 600 and 338px of
    360px at weight 400, which is what puts 42 and 46 cells on those rows. The
    calibration lives with the numbers that came out of it.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
               for ch in text)


def fits(text: str, width: int) -> bool:
    """Whether `text` fits a row of `width` cells. No width, no opinion."""
    return not width or display_cells(text) <= width


def display_width(key: str) -> int:
    """One of `card-definitions.yaml`'s declared row widths, in cells."""
    sections = _catalogue().get("sections") or {}
    try:
        return int(sections.get(key) or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# The subject. §5: "对象显示名"。
# ---------------------------------------------------------------------------

#: The prefixes `alerting.RESOURCE_PREFIXES` already treats as naming a
#: resource, each with the word for the KIND of thing it names and the rule
#: that turns the rest of the ref into something a person recognises. Ordered
#: longest-prefix-first by hand: `process-tree:session-` and `process-tree:`
#: both match a session tree, and the more specific one has to win or every
#: session tree renders as an opaque pid blob.
#:
#: THE KIND WORD IS ITS OWN CELL, AND IT IS HERE FOR ONE REASON. Every rule
#: puts a class in front of a name — `受管运行时 container0-litellm`,
#: `文件系统 /data` — and that class is the first thing a row with one line to
#: spend can do without, because the name behind it is more specific than the
#: class in front of it. Writing it as its own entry means a surface that is
#: short of room can leave it off without a second table saying what the kind
#: words are; `bare` in the returned dict is that second form. `None` is a rule
#: that prefixes no kind word, and then the two forms are the same string.
_SUBJECT_RULES = (
    ("runtime-group:process-tree:session-", "会话",
     lambda rest: _session_tree(rest, "会话")),
    # A container's process tree, which is the shape most of this node's
    # findings take. Named as a container rather than left as `system.slice:
    # docker:<id>`: the slice, the runtime and the id are three layers of where
    # the process happens to live, and the card is about one container.
    ("runtime-group:process-tree:system.slice:docker:", "Docker 容器",
     lambda rest: f"Docker 容器 {rest}"),
    ("runtime-group:process-tree:", "进程组",
     lambda rest: f"进程组 {rest}"),
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
    ("runtime-group:claude-session:", "登录会话",
     lambda rest: _session_tree(rest, "登录会话")),
    ("runtime-group:codex-session:", "登录会话",
     lambda rest: _session_tree(rest, "登录会话")),
    ("runtime-group:", "运行时组", lambda rest: f"运行时组 {rest}"),
    # `filesystem:mount:/data` → `文件系统 /data`. The `mount:` in the middle
    # is the producer's own word for how it found the filesystem and it is not
    # part of the name of anything; a card that printed it reads as "mount
    # colon slash data", which is a ref, not a place.
    ("filesystem:", "文件系统",
     lambda rest: f"文件系统 {rest.split('mount:', 1)[-1]}"),
    ("host:", "整机", lambda rest: f"整机 {rest}"),
    ("device:", "设备", lambda rest: f"设备 {rest}"),
    ("tunnel:", "隧道", lambda rest: f"隧道 {rest}"),
    ("container:", "容器", lambda rest: f"容器 {rest}"),
    # A container the name index could not resolve. The ruling's last rung is
    # "缩短后的 opaque id", and without this rule the ref matched `runtime:`
    # below and rendered as 「受管运行时 container:08e734361c06…」 — the
    # producer's own `container:` token left inside a sentence, behind a word
    # that is about registrations. Nothing is invented here: `rest` is whatever
    # the ref carried, and `_abbreviate_ids` shortens it if it is a hex id.
    ("runtime:container:", "容器", lambda rest: f"容器 {rest}"),
    ("runtime:", "受管运行时", lambda rest: f"受管运行时 {rest}"),
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


def device_name(ref: str | None) -> str:
    """A device, said as the device's name.

    THE REF SCHEME IS NOT PART OF THE NAME. `device:tokyo-shared-dev-01` is a
    ref, and the `device:` in front of it is the half that says which table the
    rest keys into — the same kind of token as the `mount:` that `filesystem:`
    strips two paragraphs up. On the card's title it is seven characters of
    machinery in front of the one word that says which machine this is, and at
    16px on a 360px row it was seven characters of the overflow that wrapped
    the title onto a second line. §2 names 原始 schema on the face as the thing
    a reader scrolls past to reach a reading.

    A ref that is not a device ref is returned unchanged: this strips one
    declared prefix and does not guess at others.
    """
    text = str(ref or "").strip()
    return text[len("device:"):] if text.startswith("device:") else text


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

    THREE FORMS, AND EACH ONE HAS A READER. `text` is the face — what the name
    is called by default. `precise` is the same subject with the identifier
    that distinguishes it from its siblings (`_session_tree` carries the pid
    there), for a detail view where there is room. `bare` is `text` with the
    kind word taken off — `container0-litellm` rather than `受管运行时
    container0-litellm` — and it exists for exactly one reader: §3.4's problem
    line, which has one line of §2's twelve and must not spend two of them
    wrapping. A surface that takes `bare` is expected to keep `text` one tap
    down; the name is never lost, only moved.
    """
    ref = str(subject_ref or "")
    named = _lookup_name(ref, names)
    if named is not None:
        text = _abbreviate_ids(named["text"])
        return {"ref": ref or None, "text": text,
                # A name the resolver found is already the name; there was no
                # kind word in front of it and so there is none to leave off.
                "bare": text,
                "precise": None,
                "rule": named["rule"], "owner": dict(owner) if owner else None,
                "because": named.get("because"),
                "criticality": named.get("criticality")}
    text = None
    bare = None
    precise = None
    rule = None
    for prefix, kind, expand in _SUBJECT_RULES:
        if ref.startswith(prefix):
            rest = ref[len(prefix):]
            expanded = expand(rest)
            # A rule may return the more precise form alongside the face one
            # (`_session_tree` does; see the pid rule there). Everything else
            # returns a single string and has no second form.
            text, precise = expanded if isinstance(expanded, tuple) else (expanded, None)
            # The kind word comes off by the rule's own declaration and not by
            # cutting at the first space: `Docker 容器` is two words, and a
            # split would leave `容器 abc123` behind — a different kind word,
            # not no kind word.
            bare = text[len(kind) + 1:] if kind and text.startswith(kind + " ") else text
            rule = prefix
            break
    if text is None:
        # An unrecognised subject is printed whole. Guessing at its shape is
        # how `runtime-group:process-tree:session-150:pid28541` becomes
        # "会话 150" and stops naming the process the alert is actually about.
        text, rule = (ref or "（未记录对象）"), "verbatim"
        bare = text
    return {"ref": ref or None, "text": _abbreviate_ids(text),
            "bare": _abbreviate_ids(bare),
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


def age_says(seconds) -> str:
    """How long ago, said in one unit — «2 天前», not «距今 2 天 0 小时».

    TWO SPELLINGS OF AN INTERVAL ARE NOT A DUPLICATE, and the difference is what
    each one is for. `human_duration` above is the measurement: it appears where
    a duration IS the reading («已持续 1 小时 08 分», «本次持续 23 分钟») and it
    is exact because a reader is going to compare it with another duration.

    This one is an AGE beside an absolute timestamp, and its job is to save the
    reader arithmetic the timestamp already implies — so the units it cannot
    round to are units nobody asked for. On the alert face the pair sits on a
    row that measured 422.9px in a 360px box, and the five cells « 0 小时»
    spends there to say «and no hours» was a third of what the line had to give
    up to stop wrapping.
    """
    if seconds is None:
        return "未知"
    seconds = int(seconds)
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


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


#: The four answers §4's second question can have. `UNCONFIRMED` and `STILL`
#: are the pair that must never be merged: both are spelled `state: OPEN` in
#: the register, and only `hold_kind` separates "the last pass that graded this
#: still found it above its floor" from "no pass has graded it since".
STILL = "STILL"
UNCONFIRMED = "UNCONFIRMED"
RECOVERED = "RECOVERED"


def existence_of(row: dict, *, at: str) -> dict:
    """§4's second question — 「现在还存在吗」 — from the register, not the clock.

    THE BUG THIS REMOVES
    --------------------
    The model used to answer it with `row["resolved_at"] or at`, where `at` is
    the instant the card was rendered. That is not an observation. On this node
    it would print 「仍在发生」 on 1,091 rows whose last grading was 2026-09-21
    and which have been `hold_kind: unmeasured` ever since — the card asserting
    a current reading about a number thirteen days old. §4's own rule for the
    face ("no measurement ⇒ declaration wording only") is the same rule one
    level up: a state that was never observed must not be printed as one that
    was.

    THE THREE ANSWERS
    -----------------
      * `RECOVERED`   — the register closed it; `observed_at` is when.
      * `UNCONFIRMED` — open, and held: the last pass refused to grade it, so
                        nobody has looked. `observed_at` is the last time
                        anybody DID look, which may be the day it opened, and
                        `silent_seconds` is how long ago that was.
      * `STILL`       — open, not held: the last grading found it above its
                        floor. `observed_at` is that grading.

    A row with no state at all is `UNCONFIRMED` with no observation, because
    "the register did not tell me" and "somebody looked and it is still there"
    are different answers and the second one is the one a reader acts on.

    `silent_seconds` is computed against `at` here rather than in the renderer
    so that both registers — the face and the evidence block — say the same
    age. It is None whenever there is no observation to age.
    """
    state = str(row.get("state") or "").upper()
    hold = row.get("hold_kind") or None
    if state == "RESOLVED":
        return {"state": RECOVERED, "hold_kind": hold,
                "observed_at": row.get("resolved_at"),
                "silent_seconds": None,
                "basis": "resolved_at"}
    looked = row.get("last_seen")
    return {
        "state": UNCONFIRMED if (state != "OPEN" or hold) else STILL,
        "hold_kind": hold,
        "observed_at": looked,
        "silent_seconds": _seconds_since(looked, at),
        "basis": ("last_seen" if state == "OPEN"
                  else "the register row carried no state"),
    }


def _seconds_since(when, at: str) -> int | None:
    """Seconds from `when` to `at`, or None if either will not parse."""
    start = _parse(when)
    end = _parse(at)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds()))


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

    finding = integrity_finding(digest.get("held") or [], at=at)
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
    # Built once and read twice: the evidence block and the face's measurement
    # line are the same numbers, and assembling the row twice is how the two
    # would come to disagree about which fields the register carried.
    detail_row = _for_detail(row, at=at)
    return {
        "ref": row.get("key"),
        # §29. WHICH appearance this event is, in the vocabulary the delivery
        # ledger files receipts under — `alert_digest.episode_of`, called rather
        # than reimplemented here. The sender is the reader: before it sends, it
        # has to ask the ledger «may I change the message that already carries
        # this», and the ledger's answer is keyed on that token. Recomputing it
        # at the call site would be a second reading of one name, and the day
        # the two drift is the day one incident gets two cards.
        #
        # It rides on the EVENT rather than on the card because the episode is
        # a property of the finding: a card carries up to three events and the
        # receipt names the one the card is about.
        "episode": alert_digest.episode_of(row),
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
        # The reading the finding is ABOUT, as a field of the event rather than
        # as the first clause of an English sentence inside `evidence`.
        #
        # It is lifted out because the acceptance criterion names it: an
        # incident card's first screen answers what / how bad / what it costs /
        # what to do / since when, and a reader deciding whether to get up has
        # to have the number and the tier it crossed in that same screen. §4
        # always required the measurement — it required it one tap down, in a
        # block titled 技术详情, which is where a person who has not yet decided
        # to care will not go.
        #
        # Produced by `alerting.measurement_face`, not by this module: the
        # register's payload has exactly one reader that knows which of its keys
        # are readings, and a second one here would be a second answer about
        # what this code measures. Null on a code that measures nothing, which
        # is a real answer and not a gap.
        "measurement": alerting.measurement_face(detail_row,
                                                 detail_row.get("measured") or {}),
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
        # §4's second question, and the reason it is a field of the event and
        # not a clause of `状态`: 状态 is what the machine was doing when this
        # was written, and this is whether that is still true. On a phone those
        # are the two lines that decide whether the reader gets up.
        "existence": existence_of(row, at=at),
        "times": {"first_seen": row.get("first_seen"),
                  # The observation the register recorded, and NOT the render
                  # instant it used to be. `resolved_at or at` made every open
                  # finding claim a last reading taken at the moment somebody
                  # looked at the card; `existence_of` is the reader of this
                  # now, and it says what the register has.
                  "last_seen": row.get("last_seen"),
                  "duration": duration,
                  "duration_text": human_duration(duration["seconds"])},
        "occurrences": row.get("occurrence_count"),
        "evidence": alerting.detail_for(detail_row, at=at),
    }


#: The register fields `alerting.detail_for` reads out of `measured`, in the
#: shape the digest row carries them. Rebuilt here rather than passed through
#: whole because the digest row is a projection: `measured` on the register
#: holds whatever the producing detector put there, and handing that to
#: `detail_for` would make the evidence block depend on arbitrary per-code
#: payload rather than on a named set of fields.
#:
#: The numeric half is `alert_digest.MEASUREMENT_FIELDS` itself and not a second
#: list written out here. Two lists of the same keys is one list that will
#: drift, and the drift would be silent in the direction that matters: a key
#: carried by the row and dropped here renders exactly like a reading the
#: detector never took. State which side of that seam whichever way you like --
#: the point is that there is one list.
_DETAIL_MEASURED = ("desired_state", "actual_state", "actual_derived_state",
                    "actual_raw", "caveat") + tuple(alert_digest.MEASUREMENT_FIELDS)


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

#: The readings a section may carry, and the whole of them. Written once because
#: two readers need it and a second copy would drift silently: the clamp in
#: `_gateway_state` admits a reading only if it is named here, and `env_health`
#: files the faulted half as faulted. A state named in one and not the other is
#: not a hypothetical — it is exactly the defect this tuple was extracted for,
#: one layer apart (see `_gateway_state`).
SECTION_FAULTED = ("DEGRADED", "NOT_READY", "DOWN", "CRITICAL", "ATTENTION")
#: `GATEWAY_UNBOUND` is deliberately absent. It is a reading of the BINDING and
#: not of the gateway (see `gateway_section`), so a bound section whose
#: accounting came back saying «unbound» is saying something that cannot be true
#: of that axis — and admitting it here would route it into `env_health`'s
#: unbound branch, which exists to keep a declaration gap from being reported as
#: a fault in the environment.
SECTION_READINGS = (OK, UNKNOWN) + SECTION_FAULTED


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


#: §3.4's fourth block, and the one thing it may never do is render «no reading»
#: as «no problems». The register is the only document on this card that can
#: answer «现在有什么问题», and a node that cannot open it is not a node with
#: nothing wrong — it is a node whose monitor has stopped being able to tell.
#: That is why every branch below has a spelling for it and why there is no
#: default that arrives at an empty list.
_PROBLEM_STATES = {STILL: "仍在发生", UNCONFIRMED: "无法确认",
                   RECOVERED: "已恢复"}


def _problem_line(subject: dict, label: str, word: str, width: int) -> str:
    """`object · finding · state` on ONE line, or the kind word comes off.

    §2 gives the first screen ten to twelve lines and §3.4 spends one of them on
    a problem, so a problem that wraps costs two and reads as two problems. The
    live card had exactly that shape: the managed-runtime row measured 706px
    against a 360px body, and it was the widest of the three but not the only
    one over.

    SOMETHING IS GIVEN UP AND THE ORDER IS DECLARED. The kind word goes first —
    `受管运行时` in front of a container whose own name already says what it is —
    because it is the least specific part of the line. The finding and the state
    do not go: §3.4 puts both on this line by name («对象、目录把它叫什么、以及它
    是否仍在发生»). The full name is not lost either; it is one line down, in the
    fold the same block opens.
    """
    full = " · ".join(b for b in (subject["text"], label, word) if b)
    if fits(full, width):
        return full
    return " · ".join(b for b in (subject["bare"], label, word) if b)


def problems_block(reading: dict | None, *, at: str) -> dict:
    """§3.4: what is wrong RIGHT NOW, in a reader's words and nothing else.

    THE READING COMES FROM `alert_digest.current`, and this function is the
    reader's side of it: the register's own answer — which rows are open, which
    codes the contract puts on a face, how many are behind each code — arrives
    as data, and the words here are the card's. The split is the same one the
    other three blocks have: `card_source` gathers, `alert_digest` grades
    against the contract, and this decides what a person reads.

    WHY IT IS NOT A LIST OF EVENTS. §4's seven questions belong to the incident
    card, which exists to be opened when something happened. This block sits on
    a dashboard that is read in a glance and answers one question — «现在有
    问题吗，几件» — so every problem is ONE line: the object, what the catalogue
    calls the finding, and whether it is still happening. The number, the
    threshold and the impact are one tap away on the incident card, which is
    the card that has room for them; printing them here three times over is how
    the first screen stops being a first screen (§1: ≤10–12 行).

    `at` ages the readings, and it is the card's instant rather than the
    reading's, so that a problem listed here is aged the same way as every
    other time on this card. The two differ by the seconds between a subprocess
    and a render.

    TWO KINDS OF OMISSION COME BACK, AND ONLY ONE OF THEM IS THE FACE'S.
    `also` is about the problems — how many were not listed, which codes nobody
    has graded, what a person has already answered — and it is printed under
    the list. `below` is true of the register and is not about the problems,
    and the renderer folds it. The split is made here because this is the
    module that decides what the word «problem» covers; see the comment at the
    split itself.
    """
    reading = reading if isinstance(reading, dict) else {}
    if str(reading.get("state") or "") != "READ":
        # No reading, or a reading that says it could not be made. Both get the
        # same treatment and neither gets an empty list: `unreadable` in
        # `alert_digest` is the producer of the second, and a caller that handed
        # nothing at all — a fixture, an older `status_model` caller — is the
        # first. §13 fixes this block on the first screen, so it is printed
        # rather than dropped, and what it prints is that it cannot answer.
        #
        # THE FACE SENTENCE IS A PERSON'S AND THE TOOL'S OWN WORDS ARE NOT ON
        # IT. `why` arrives as whatever the subprocess last said — `card_source.
        # _default_run` keeps the final stderr line — so a refused socket puts
        # a command name and a status code on the first screen. That is the
        # boundary §R15 draws for every other read on this card, and the card
        # keeps it two blocks up: 采集缺口 names the readings that did not
        # arrive («本账户资源摘要…未读到（4 项）») and the command with its exit
        # code sits in 技术诊断. There is no reason the register should be the
        # one read that reports itself in tool vocabulary, so the raw reason
        # goes to `below` and the face says what the reader can act on.
        detail = " ".join(str(reading.get("why") or "").split())
        # ONE CLAUSE, BECAUSE THE HEADER ALREADY CARRIES THE OTHER. The face
        # reads «当前问题 · 无法确认» and then this line under it, so
        # «无法回答『现在有什么问题』» was the header said twice — and the pair
        # together measured 562.5px in a 360px row, which is a wrap bought to
        # repeat a word the reader had just read. What the header cannot say is
        # the half §3.4 spends its paragraph on, and that half is this one:
        # nobody looked is not the same as nothing is wrong. `note` and `below`
        # keep the longer sentence for the fold and the register's own account.
        unread_sentence = "没有读到登记表 · 空白不是「没有问题」"
        return {
            "state": "UNAVAILABLE",
            "level": "",
            "headline": "无法确认",
            "lines": [],
            "details": [],
            "counts": {},
            "shown": 0,
            # The read's own account, one tap down: which command was asked and
            # what it said. Empty when a caller handed nothing rather than a
            # failed read — there is no command to name in that case, and
            # inventing one would be a sentence about a read nobody made.
            "below": ([f"serverosctl alert current 未读到：{detail}"]
                      if detail else []),
            "why": unread_sentence,
            "note": str(reading.get("note") or
                        "没有读到告警登记表，所以这一块无法回答「现在有什么问题」；"
                        "空白不是「没有问题」。"),
        }
    counts = dict(reading.get("counts") or {})
    action = int(counts.get("ACTION_REQUIRED") or 0)
    review = int(counts.get("REVIEW") or 0)
    total = int(counts.get("open_on_face") or 0)
    # The level is the WORST one present, and it is a level and not a sentence
    # so that the icon is chosen in one place: the words in this function are
    # this module's, and the glyphs on this card are `cards`'. Passing the level
    # across keeps the two from having to agree about colours in two files.
    level = AXIS[0] if action else (AXIS[1] if review else "")
    if action and review:
        headline = f"{action} 项需要处理、{review} 项建议查看"
    elif action:
        headline = f"{action} 项需要处理"
    elif review:
        headline = f"{review} 项建议查看"
    else:
        headline = "没有需要处理的问题"

    # One line per problem, and the line is bounded by measurement rather than
    # by hope. `problems_width` is the number `card_geometry.py` put on this
    # row: the widest string that fitted the 360px body at weight 400 measured
    # 338px, and 46 cells is that string. A line that would exceed it gives up
    # the subject's kind word rather than wrapping — see `_problem_line`.
    width = display_width("problems_width")
    lines, details, covered = [], [], 0
    for row in reading.get("shown") or []:
        if not isinstance(row, dict):
            continue
        subject = subject_display(row.get("subject_ref"), owner=row.get("owner"))
        label = str(definition_for(row.get("code")).get("label") or "").strip()
        existence = existence_of(row, at=at)
        word = _PROBLEM_STATES.get(str(existence.get("state") or ""), "状态未知")
        # THE AGE IS THE FACT §4 TURNS ON AND IT IS NOT ON THE FACE ANY MORE.
        # «仍在发生» and «无法确认» are the same `state: OPEN` in the register,
        # and only the age of the last reading tells them apart — so a row
        # nobody has graded since the 21st must not read as a live fault, and a
        # live fault must not read as one nobody is looking at. That is why the
        # age cannot be dropped. It is why it is one line down instead: on the
        # face it made every problem two lines (§2 allows the whole first
        # screen twelve), and the state word it explains is still on the face.
        silence = existence.get("silent_seconds")
        age = ""
        if str(existence.get("state")) == UNCONFIRMED and silence:
            age = f"最后一次读数在 {human_duration(silence)}前"
        elif str(existence.get("state")) == STILL and row.get("duration_seconds"):
            age = f"已持续 {human_duration(row['duration_seconds'])}"
        of_code = int(row.get("of_code") or 1)
        covered += of_code
        named = " · ".join(b for b in (subject["text"], label) if b)
        lines.append(_problem_line(subject, label, word, width))
        extra = [age] if age else []
        if of_code > 1:
            extra.append(f"同类共 {of_code} 项")
        if extra:
            # The full name, spelled, with the two facts the face gave up
            # beside it — and it is `named` and not the face line, so a problem
            # whose face dropped its kind word gets it back on the first tap.
            details.append(f"{named}　{'　'.join(extra)}")

    parts = []
    codes = int(counts.get("codes") or 0)
    if total > covered:
        parts.append(f"另有 {total - covered} 项未逐条列出（共 {codes} 类问题）")
    undecided = reading.get("undecided") or {}
    if int(undecided.get("count") or 0):
        parts.append(f"{int(undecided['count'])} 项的 code 未在注意力表中定级，"
                     f"所以它们算不算问题这件事本身没有定论")
    acknowledged, snoozed = (int(counts.get("acknowledged") or 0),
                             int(counts.get("snoozed") or 0))
    if acknowledged or snoozed:
        said = []
        if acknowledged:
            said.append(f"已确认 {acknowledged} 项")
        if snoozed:
            said.append(f"已静默 {snoozed} 项")
        parts.append("、".join(said) + "，不再列在这里")
    # BELOW THE FOLD, AND THE SPLIT IS THE POINT
    # -----------------------------------------
    # The register carries a second population the face is not allowed to have
    # an opinion about: findings the contract has already graded as NOT a
    # problem — `watch`, whose codes sit under the face, and which this machine
    # holds 1,360 of at 2026-10-04. On the CLI they are disclosed, and that is
    # right: somebody who typed `alert current` asked the register a question
    # and §5 says a short answer is only honest with its omissions beside it.
    #
    # A card is asked a different question. It answers «现在有问题吗，几件», and
    # the contract has already answered «这 1,360 件不算» — so printing the
    # number here re-opens a question the contract closed, in the one place §13
    # keeps for what changes a judgement («旧账/恢复清单不塞日常首屏»). It also
    # does something worse than clutter: «没有需要处理的问题» followed by a
    # four-digit number teaches a reader that the green line is not to be
    # trusted, and the number drifts every tick while nothing acts on it.
    #
    # So it moves to the fold rather than being dropped, which is where the
    # block's own `note` already points at `alert list`. What stays on the face
    # is every sentence that is about the PROBLEMS: how many of them were not
    # listed, how many codes nobody has graded (which is the one gap that makes
    # the headline itself unreliable), how many a person has already answered.
    below = []
    watch = reading.get("watch") or {}
    if int(watch.get("count") or 0):
        below.append(f"登记表里还有 {int(watch['count'])} 项在「值得留意」「供参考」"
                     f"一档。它们不在脸面上，也不改变上面那句——合同已经判过"
                     f"它们不是当前问题，完整清单见 `serverosctl alert list`。")
    return {
        "state": "READ",
        "level": level,
        "headline": headline,
        "lines": lines,
        # WHAT THE FACE LINE COULD NOT HOLD, one line per problem and only for
        # the problems that had something to hold back: the age that separates
        # «仍在发生» from «无法确认», the number of siblings behind the same
        # code, and the subject's full name when the face dropped its kind word.
        # A problem with nothing held back contributes nothing, so the fold
        # opens onto nothing on a card where every line fitted — the renderer
        # skips an empty one rather than drawing an empty panel.
        "details": details,
        "counts": counts,
        "shown": len(lines),
        # The omissions, each in its own sentence, for the reason §5 gives
        # everywhere else: a block that prints three of thirteen and says
        # nothing about the other ten is read as a block about three.
        "also": parts,
        # True, worth keeping, and not about the machine's problems. The
        # renderer folds these instead of printing them, and the split is here
        # rather than in the renderer because this module is where the words
        # for «problem» are decided — a renderer sorting sentences it cannot
        # read would be a second answer about which codes are current problems.
        "below": below,
        "note": str(reading.get("note") or ""),
    }


def env_health(sections: dict, *, at: str, gaps: list | None = None,
               problems: dict | None = None) -> dict:
    """§2's top line: is this environment usable, and how good is the reading?

    Two separate answers, and §2 draws them as two — "环境可用性：有一项待确认"
    next to "观测：部分新鲜". Collapsing them into one word is the failure this
    function exists to prevent: an environment can be perfectly usable on a
    partial reading, and the two facts have different repairs.

    **UNKNOWN is never green.** §6: "未知用中性而非正常绿". An unread section
    makes the verdict `UNCONFIRMED`, not `OK` — the absence of a fault signal
    is not the presence of a good one, and this is the same rule
    `graph.freshness_of` applies to a single timestamp, applied to a summary.

    **UNBOUND is neither.** A third outcome, added because the other two both
    said something false about it. `GATEWAY_UNBOUND` is what
    `gateway_section` reports when no service binding names this node's shared
    gateway — an established absence, arrived at by looking, not a reading
    that did not happen. Filed as `unread` it turned the whole card grey and
    told a reader to go and get a reading that already existed; filed as
    `faulted` — which this function listed it as, in a branch it could never
    reach, because `_gateway_state` rewrote the value before it got here — it
    would report a declaration gap in this monitor as a fault in the
    environment. Both send somebody to repair the wrong thing, so it is
    carried as its own state and the verdict names it.

    **§3.4's 当前问题 has a vote, and it is a fourth input.** The three sections
    are readings of this node, and the register is the node's own list of
    findings about itself: an open alert whose code the contract puts on a face
    is a known active anomaly even when every section rendered fine, so it can
    take the verdict out of green — at `REVIEW` with 建议查看 and at
    `ACTION_REQUIRED` with 需要处理. A register that could not be read takes it
    out of green the other way, to `UNCONFIRMED`, because §3.4 lets green stand
    only on 足够的相关观测 and «nobody looked» is not that. Both directions are
    the same rule §6 states for one timestamp, applied to a fourth reading.
    """
    gaps = list(gaps or [])
    states = {name: (sections.get(name) or {}).get("state") for name in SECTIONS}
    # §3.4's fourth block, which is not a section: it is read from the register
    # rather than from this node's three subjects, and it is the ONE input here
    # that can say «there is something wrong» which no section would report —
    # an open alert is a finding about the node, and the three sections are
    # readings of it.
    #
    # `None` AND A BLOCK THAT SAYS `UNAVAILABLE` ARE DIFFERENT ANSWERS, and the
    # difference is the one `card_source.snapshot` already draws for the
    # calendar and the day store: «handed a reading that says it could not be
    # made» versus «handed nothing». A caller that never put a register in view
    # — this function's own unit callers, a fixture about three sections — is
    # asserting nothing about one, and leaving green unearned on its behalf
    # would be inventing an observation it was never given. `status_model`
    # always builds the block, so the CARD's verdict is the one §3.4 is about,
    # and it is decided by the block.
    handed = problems is not None
    problems = problems if isinstance(problems, dict) else {}
    problem_counts = dict(problems.get("counts") or {})
    problem_level = str(problems.get("level") or "")
    problem_action = int(problem_counts.get("ACTION_REQUIRED") or 0)
    problem_review = int(problem_counts.get("REVIEW") or 0)
    problem_unread = handed and str(problems.get("state") or "") != "READ"
    unread = sorted(n for n, s in states.items() if s in (None, UNKNOWN))
    unbound = sorted(n for n, s in states.items() if s == GATEWAY_UNBOUND)
    faulted = sorted(n for n, s in states.items() if s in SECTION_FAULTED)

    if faulted and unread:
        verdict, word = UNCONFIRMED, "有项目需处理，且有项目未读到"
    elif faulted:
        verdict, word = ATTENTION, "有项目需处理"
    elif problem_level == AXIS[0]:
        verdict, word = ATTENTION, f"有 {problem_action} 项需要处理"
    elif problem_unread and unread:
        # BOTH KINDS OF «NOBODY LOOKED», said as one thing. Either half on its
        # own is a complete answer to a different question — the sections are
        # «how much of this node did we read» and the register is «what is known
        # to be wrong» — and when both are missing, printing only the register's
        # half would drop the fact that two of the three blocks above are blank.
        # The shape is the one the `faulted and unread` branch already uses:
        # state both, and let the fold name which sections, which is where this
        # card already answers that (§2 keeps the names off the first screen).
        verdict, word = UNCONFIRMED, "有项目未读到，且现在有什么问题无法确认"
    elif problem_unread:
        # §3.4: 绿色必须有足够的相关观测. A register nobody could open is not
        # enough observation to say the environment is fine, and the whole
        # round is about the false green that says it is. The block itself
        # prints why one tap away; the verdict names the state.
        verdict, word = UNCONFIRMED, "现在有什么问题无法确认"
    elif problem_level == AXIS[1]:
        # REVIEW is not green either, and the reason is the same one: the
        # contract puts a code on the face only when a person may be
        # interrupted for it, so an open one is a known active anomaly — a
        # declared service whose probe is failing, a filesystem past its
        # declared threshold. It is not 需处理 and it does not say so; it says
        # what the contract's own word for this level says.
        verdict, word = ATTENTION, f"有 {problem_review} 项建议查看"
    elif unread:
        # Named rather than counted, because "一项未读到" sends the reader
        # hunting through three blocks for which one, and the card already
        # knows.
        verdict, word = UNCONFIRMED, "有项目尚未读到：" + "、".join(unread)
    elif unbound:
        # Green, and green is right: everything that could be read was read and
        # none of it is a fault. The clause after the semicolon is what keeps
        # the green honest — "三项均已读到" would be false, and §6's ban on
        # false green is not limited to the icon.
        verdict, word = OK, ("已读到的项目均无异常；" + "、".join(unbound)
                             + " 一项没有任何绑定声明它")
    else:
        verdict, word = OK, "三项均已读到且无异常"

    observation = ("部分新鲜" if gaps or unread or problem_unread else "新鲜")
    return {
        "verdict": verdict,
        "verdict_text": word,
        "observation": observation,
        "observation_text": (
            f"有 {len(gaps)} 项采集缺口" if gaps else
            ("有项目未读到" if unread else
             ("当前问题这一块没有读到" if problem_unread else
              ("已读到的项目无缺口" if unbound else "三项均已读到")))),
        # Read, not merely present. An unbound section is neither — it is a
        # declared absence — and counting it here would print "已读到 gateway"
        # about the one block the card says nothing was read for.
        "sections_read": sorted(n for n, s in states.items()
                                if s not in (None, UNKNOWN, GATEWAY_UNBOUND)),
        "sections_unread": unread,
        "sections_unbound": unbound,
        # WHICH ONES FAULTED, and it is carried for the same reason the two lists
        # above are. A block's line on the first screen says what state that
        # block is in, and until this line the renderer had no way to ask: the
        # three lists that decide the verdict were computed here and two of them
        # were returned. A renderer left to re-derive «did this block fault»
        # from its raw state would be testing one word that means a different
        # thing per block — a bound gateway's state is how the gateway IS, an
        # unbound one's is the absence of a declaration — which is the shape of
        # the bug that once made every bound gateway render as the word BOUND.
        "sections_faulted": faulted,
        # §3.4's block is not a section and is not counted among the three, so
        # what it contributed to the verdict is carried by name. A verdict that
        # came out ATTENTION because of an open alert must be distinguishable
        # from one that came out ATTENTION because a section is DEGRADED — the
        # two send a reader to different places, and a model that made them
        # identical would leave the alert card as the only way to tell.
        "problems_read": not problem_unread,
        "problem_level": problem_level or None,
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

    `snapshot["day"]` — today's card and the hours it folded — travels through
    unchanged when it is there and is ABSENT when it is not. Unlike the schedule
    this gets no degraded sentence of its own: the calendar's absence has an
    acceptance criterion that names the word `calendar=UNKNOWN`, and the day
    card's absence has none, so a fixture written without a day store renders
    without a day block rather than with an invented one.

    `snapshot["problems"]` — §3.4's fourth block — is the one input here that is
    NOT optional in the same sense: it gets a block and a vote on the verdict
    whether or not it is present, because §13 fixes the block on the first
    screen and a fixed block that vanishes is read as «nothing wrong». A
    snapshot without the key renders it as 无法确认, which is `problems_block`'s
    answer for a caller that handed nothing, and the verdict leaves green for
    the same reason.
    """
    gaps = list(snapshot.get("gaps") or [])
    sections = {
        "mine": _section("mine", snapshot.get("mine"), gaps),
        "host": _section("host", snapshot.get("host"), gaps),
        "gateway": _gateway_state(gateway_section(snapshot, binding=binding)),
    }
    mine = snapshot.get("mine") or {}
    day = snapshot.get("day") or None
    problems = problems_block(snapshot.get("problems"), at=at)
    health = env_health(sections, at=at, gaps=gaps, problems=problems)
    # WHICH READS, BESIDE THE SENTENCES THAT SAY WHY, and in the same dict so a
    # reader of one is a reader of the other. The gap sentence `card_source`
    # writes is «summary（resource summary）未读到：exit 2» — a schema key, a CLI
    # invocation and an exit status, all three of them §2's forbidden spelling
    # on a first screen — and it stays reachable in the engineering fold, where
    # 采集口径 belongs. `card_source` writes both from ONE list, so the names
    # cannot come out of step with the sentences; the renderer reads them rather
    # than parsing them back out of the sentence, which would be a second reader
    # of a shape and would drift the first time the sentence was reworded.
    health["gap_reads"] = list(
        (snapshot.get("coverage") or {}).get("reads_missing") or [])
    schedule = _schedule(snapshot)
    model = {
        "schema": SCHEMA,
        "card": "status",
        "sample": bool(sample),
        "sample_mark": SAMPLE_MARK if sample else None,
        "at": at,
        "node_label": snapshot.get("node_label"),
        "node_host": snapshot.get("node_host"),
        "env_health": health,
        "sections": sections,
        "order": list(SECTIONS),
        "schedule": schedule,
        "day": day,
        # §13 fixes the first screen's blocks as 我的资源、整机、关键基础设施、
        # 当前问题 — so `cards.status_card` prints this one after `order` and
        # before the engineering fold, and it reads a block that is always
        # there. It travels as its own key rather than as a fourth entry in
        # `order` because `order` is the list of the three sections and every
        # reader of it is asking about a section: `sections_read`, the coverage
        # note and the sample fixture all count three, and a fourth name in that
        # list would silently make them count four.
        "problems": problems,
        # The line the card leads with. Built from the day store, because that
        # is the only document that holds the hour before this one — the
        # snapshot carries the present and nothing of the past, and a delta
        # recomputed from two live readings would be a delta against an hour
        # nobody recorded. The schedule is needed beside it to tell «this is the
        # day's first hour» from «the earlier hours were never read», which the
        # store alone cannot distinguish: both are an empty history.
        "delta": _delta(day, health, sections, schedule),
        "coverage": {
            # §3. The shared gateway and the personal container0 are different
            # things and the card says which of them each number came from.
            "personal_container0": "mine",
            "shared_gateway": "gateway",
            "note": ("共享网关与个人 container0 分开呈现；同一台机器上的两个"
                     "对象，不是同一件事"),
        },
    }
    # AFTER `_delta`, and given the ORIGINAL day rather than the augmented one:
    # the delta's job is to compare now against the newest row on the record, and
    # handing it a timeline that already ends with now would make every hour
    # compare equal to itself.
    model["day"] = _day_through_this_instant(day, model, schedule=schedule)
    return model


def store_is_another_day(day, schedule: dict) -> bool:
    """True when the day store's rows belong to a day this card is not about.

    ONE PREDICATE, THREE READERS. Three things on the card are quietly about the
    store's day rather than the card's: the delta compares this instant against
    the store's newest row, the day panel lists the store's rows, and
    `_day_through_this_instant` decides whether this reading may join them. On a
    working day the two days are the same one and none of the three has to say
    so — which is exactly why each of them was written without a day in it, and
    why a rest day, where the store still holds the last working day, made all
    three false at once. Written once so the three cannot drift into disagreeing
    about WHICH day each is describing.

    Both refs must be present for the answer to be `True`. A schedule with no
    day reference is a calendar nobody imported — the honest answer there is
    that this card does not know which day it belongs to, and asserting that the
    store's day is a different one would be inventing the fact that is missing.
    """
    ref = day.get("day_ref") if isinstance(day, dict) else None
    sched_ref = schedule.get("day_ref")
    return bool(ref and sched_ref and ref != sched_ref)


def _day_through_this_instant(day, model: dict, *, schedule: dict):
    """The day's timeline with THIS reading as its last point.

    THE CONTRADICTION THIS CLOSES
    -----------------------------
    The delta and the timeline sat three lines apart on one screen and could
    disagree about whether anything had happened. `_delta` compares this instant
    against the store's newest row; `card_day.timeline` was built from the
    stored rows alone. A reading that had just changed printed «较 19:00 有 1 处
   变化 gateway OK→UNBOUND» over a panel whose title said «0 处变化» — two
    answers to one question, three lines apart, from the same comparison
    function, because one of them was given the present and the other was not.

    `card_day.summary`'s own docstring claims «the current hour is IN the list
    rather than only on the card's face», and it was not: `DayStore.advance`
    writes this hour AFTER the model is rendered, so the claim held only from
    the next tick onward. This makes it true at the moment it is read.

    The row is composed by `card_day.row`, the same function `advance` will
    compose it with, so the point the panel shows now and the row the store
    keeps next tick are the same reading rather than two composition rules that
    happen to agree today. A store that already holds this tick gets nothing
    appended — the hourly timer and a preview forty minutes later are one tick,
    and a second entry would be the double-count `card_day`'s tick arithmetic
    already refuses one layer down.

    Returns `day` unchanged when there is no day to extend, when the schedule is
    not an hour that can be a row, and when the schedule belongs to a DIFFERENT
    day than the store does. That last one is not defensive tidiness: a store
    holding `CN-Mainland/2026-09-30` that is handed a schedule for 09-29 would
    otherwise grow a `10:10` reading in the middle of a 09-30 timeline, which is
    a row nobody read and a day that never had that hour. The store's own
    `day_ref` is the authority for which day this panel is about.
    """
    if not isinstance(day, dict):
        return day
    if store_is_another_day(day, schedule):
        return day
    card_day = _card_day()
    try:
        now = card_day.row(model, schedule=schedule)
    except Exception:  # noqa: BLE001 - a schedule with no tick is not a row
        return day
    rows = list(day.get("history") or [])
    if isinstance(day.get("latest"), dict):
        rows.append(day["latest"])
    if rows and rows[-1].get("tick") == now["tick"]:
        # ONE HOUR, TWO READINGS. `advance` writes the row for this tick AFTER
        # the model is rendered, so a second render inside the same hour — the
        # hourly timer, then a preview forty minutes later — is looking at the
        # row its own successor will overwrite. Replacing it is what the panel
        # has to show: the reading in front of the reader, in the hour it
        # belongs to, and not two points in an hour that has one. `_delta`
        # compares against the hour BEFORE this one for the same reason, so the
        # line at the top and the last segment here are one comparison.
        rows[-1] = now
    else:
        rows.append(now)
    entries = card_day.timeline(rows)
    return {**day,
            "timeline": entries,
            # Recounted here rather than carried, for the same reason
            # `card_day.summary` counts them rather than storing them: a count
            # kept beside a list is a second answer to the same question, and
            # the panel's title would go on saying «0 处变化» over a list that
            # had grown one.
            "transitions": sum(1 for e in entries if e["kind"] == "transition"),
            "unobserved": sum(1 for e in entries if e["kind"] == "gap")}


#: The four things this line can say about a day that has no earlier reading to
#: stand on. They are four sentences and not one because they are four claims,
#: and three of them are easy to mistake for each other:
#:
#:   * `FIRST` -- the workday's first tick. Nothing precedes it because the day
#:     started here, which is what the schedule says, not what the store says.
#:   * `UNOBSERVED` -- the day does not start here. Earlier ticks exist on the
#:     calendar and none of them has a reading. This is the one that must not be
#:     rounded into `FIRST`: «nothing has changed since the day began» and «the
#:     morning was never looked at» are opposite claims about the same silence.
#:   * `NO_TICK` -- the calendar was not read, so the card cannot say whether
#:     this hour is the first. An unread calendar that came out as «first» is the
#:     false rhythm `_schedule` refuses one screen above.
#:   * `NO_DAY` -- no day store was attached to this reading at all.
#:
#: The delta's headline when the comparison came out even is `STEADY`, and it is
#: spelled with the hour it compared against rather than as «上一小时»: the row
#: before this one is not always the hour before this one.
DELTA_STEADY = "与上一时点相同"
DELTA_FIRST = "本工作日第一个时点"
DELTA_NO_TICK = "本时点没有可比较的更早时点"
DELTA_NO_DAY = "本次没有读当天卡，无可比较的更早时点"


def _delta(day: dict, health: dict, sections: dict, schedule: dict) -> dict:
    """What changed since the previous reading, as the card's first line.

    THE ONE THING THIS CARD LEADS WITH
    ----------------------------------
    A dashboard that opens with the whole state makes every reader compute the
    difference themselves, every hour, and the reader is a person on a phone.
    The comparison is mechanical and the card can do it; what the card cannot do
    is know which difference matters, so it names them all and lets the reader
    decide.

    WHERE THE COMPARISON COMES FROM
    -------------------------------
    `card_day.changes_between`, the same function the timeline uses — one
    author of «what counts as a change», so the line at the top and the folded
    panel cannot disagree about whether this hour moved. The row this instant
    would be written as is composed here by the same rules `card_day.row` uses,
    and `tests/test_cards.py` asserts the two agree rather than this comment
    promising they do.

    The comparison is over the CARD's vocabulary (verdict / observation / gaps /
    the three section states) and not over the meter readings. A numeric delta
    spans three blocks whose numbers are not in the same unit — percent beside
    gigabytes — and «Δ» on two of those would need a unit for the difference,
    which is how a card starts inventing readings. The cells carry the numbers;
    this line carries what moved.

    A day with no earlier hour answers `DELTA_FIRST` and not `DELTA_STEADY`.
    «Nothing changed» is a finding; «there was nothing to compare against» is
    the absence of one, and printing the first for the second is the
    substitution §6's ban on a false green is about.

    AND THE HOURS BETWEEN ARE NOT SKIPPED OVER
    ------------------------------------------
    The row before this one is not always the hour before this one. A store that
    holds 14:00 and then 17:00 has two ticks nobody read between them, and a line
    reading «与 14:00 相同» would fold those two into the middle of a comparison
    that never happened — the same compression `card_day.timeline` refuses to
    make one panel down. So when the two readings are not adjacent, the count of
    unobserved ticks is part of the answer and not a footnote to it.
    """
    history = list(day.get("history") or []) if isinstance(day, dict) else []
    latest = (day or {}).get("latest") if isinstance(day, dict) else None
    # The most recent thing the day store holds. `history` alone would miss the
    # case of a day that has been folded exactly once: `card_day` keeps the
    # newest row under `latest`, so the only earlier reading can be there.
    seen = history + ([latest] if isinstance(latest, dict) else [])
    tick = schedule.get("tick")
    if not seen:
        if not isinstance(day, dict):
            return {"headline": DELTA_NO_DAY, "from": None, "unobserved": 0,
                    "moved": [],
                    "detail": "本次采集没有带当天卡，因此这一行不声称与任何时点"
                              "比较过；下方的读数是这一个瞬间的"}
        if tick is None:
            return {"headline": DELTA_NO_TICK, "from": None, "unobserved": 0,
                    "moved": [],
                    "detail": "本次采集没有读工作日历，因此无法说这是不是今天的"
                              "第一个时点"}
        if tick <= 1:
            return {"headline": DELTA_FIRST, "from": None, "unobserved": 0,
                    "moved": [],
                    "detail": "当天卡从这一拍开始，因此这一行不是比较；它与下方的"
                              "读数描述同一个瞬间"}
        return {"headline": f"本工作日更早的 {tick - 1} 个时点没有读数",
                "from": None, "unobserved": tick - 1, "moved": [],
                "detail": "这一行不是「无变化」：今天第 1–"
                          f"{tick - 1} 个时点没有任何读数，因此没有可比的对象"}
    previous = seen[-1]
    # …unless that row is THIS hour's. Two renders inside one tick are one hour
    # and `advance` overwrites the row, so the reading that will survive is the
    # one in front of the reader and the hour to compare it against is the one
    # before — not the row it is about to replace. The timeline replaces that
    # row too (`_day_through_this_instant`), which is what keeps «较 18:00 有 1
    # 处变化» and the panel's own last segment one comparison instead of two
    # answers to the same question three lines apart.
    if (isinstance(tick, int) and previous.get("tick") == tick
            and len(seen) > 1):
        previous = seen[-2]
    hhmm = previous.get("hhmm") or "上一时点"
    earlier_tick = previous.get("tick")
    # …AND IT MAY NOT EVEN BE FROM THIS DAY.
    # A card that belongs to a day the store holds no row for — a rest day, or a
    # preview rendered at an instant the hourly routine never advanced — reaches
    # back to the newest row the store does hold, which belongs to the last day
    # it advanced. That is a real reading and this is a real comparison. What it
    # is not is «较 19:00» with no day in it beside a panel whose own title says
    # 当天: the two would then answer one question differently, because the panel
    # counts the transitions INSIDE a day and this line counts the one BETWEEN
    # two. So the day joins the name of the row being compared against.
    #
    # The hours-skipped count does not survive the boundary and is not silently
    # dropped: it counts the ticks of a working day, and across two days those
    # ticks were never scheduled, so a count here would report a gap in a rhythm
    # that was not promised. The clause that replaces it says the comparison
    # crossed a day, which is the same information stated truthfully.
    day_ref = day.get("day_ref") if isinstance(day, dict) else None
    cross_day = store_is_another_day(day, schedule)
    # THE DATE, NOT THE DAY REFERENCE. `day_ref` is `CN-Mainland/2026-09-30` —
    # the jurisdiction joined to the date, which is the store's key for a day and
    # not a way anybody writes one. §2 puts `day_ref` on the list of things that
    # must not reach the first screen, and this is the one place it did. The
    # jurisdiction is not dropped: it is in the engineering block with the rest
    # of the account of which day this card belongs to, and the clause below
    # says in words that the comparison crossed one.
    dated = str(day_ref).rsplit("/", 1)[-1] if day_ref else None
    if cross_day and dated:
        hhmm = f"{dated} {hhmm}"
    # Never negative, and the clamp is not defensive tidiness: a reading taken
    # twice inside one tick — the hourly timer, then a preview forty minutes
    # later, both of them tick 8 — compares against a row of its OWN tick, and
    # the arithmetic says «minus one hours went unobserved». A count that cannot
    # be true is not a small count; it is a sentence the card would be printing
    # about hours that do not exist. Clamped at what it can mean here: this
    # comparison skipped nothing.
    unobserved = (max(0, tick - earlier_tick - 1)
                  if isinstance(tick, int) and isinstance(earlier_tick, int)
                  else 0)
    if cross_day:
        unobserved = 0
    card_day = _card_day()
    # The same four fields `card_day.row` composes, from the same two functions
    # it reads them from. This instant has no row yet — `row` is built at write
    # time, from the model this function is part of — so the comparison stands
    # on a row-shaped dict assembled here, and a test asserts the two
    # compositions agree.
    row_ = {
        "verdict": health.get("verdict"),
        "observation": health.get("observation"),
        "gaps": len(health.get("gaps") or []),
        "sections": {name: (sections.get(name) or {}).get("state")
                     for name in card_day.ROW_SECTIONS},
    }
    # WHICH AXES MOVED, AND WHICH OF THEM MAY BE PRINTED
    # -------------------------------------------------
    # `changes_between` ORs the two lists into one, and the headline's count
    # needs that one: a verdict that moved because the freshness did is a change
    # the card states. The face's clause needs them apart. `named_changes` is
    # the blocks and the gap count — something a reader can go and look at — and
    # `derived_changes` is the roll-up, every member of which is a function of
    # the others. Printing `整体状态 正常→建议查看` under a headline that already
    # said «有 1 处变化» is one fact stated twice: on this machine it fires on
    # the ordinary hour where the verdict moved because the register's problem
    # count did, and the block stating that problem is two lines further down.
    # So the roll-up counts and is not spelled, and §2's ten-to-twelve lines are
    # spent on the block instead.
    changed = card_day.changes_between(previous, row_)
    named = card_day.named_changes(previous, row_)
    # The gap clause comes FIRST in both branches, before the reader is told
    # what changed: a reader who stops after the verb has still been told that
    # the comparison skipped hours, and one who stops before it has not. It gets
    # its own line rather than a parenthetical in front of the change list,
    # because a parenthetical is read as a qualifier on the list and this is a
    # separate claim about the same hour.
    #
    # THE CLAUSE CARRIES NO LINE FEED OF ITS OWN. It used to end in `\n` so that
    # the change list would start on the next line, and that made the `not
    # changed` branch — where `gap` is returned on its own — end in a break,
    # as did the `changed` branch on the hour where only the roll-up moved
    # (`named` excludes the derived changes, so the join can be empty).
    #
    # WHAT THAT BREAK COST, MEASURED AND NOT ASSUMED. An earlier note here said
    # it drew a blank line on the card and a second empty `<p>` in the preview.
    # Both were wrong, and the measurement is in this round's evidence: the
    # rendered box is 23.25px — exactly one line — with the break and without
    # it, Chromium trims trailing whitespace at the end of a block, and
    # photographing `day_gap` with only the break removed is pixel-identical to
    # the shot that still has it (`_measure/isolate-newline`). So this is a
    # latent hazard and not a visible defect: the string is one markdown block
    # handed to a transport whose renderer is not Chromium, and a trailing break
    # in a markdown block is a line break in some of them. Nothing on this
    # machine ever drew it. The defect it did have was real and is the reason
    # the rule is written down: 4 `<p>` elements across 2 scenarios ended in a
    # break that no rule asked for.
    #
    # A line's text is what it has to say; the separation between two lines is
    # the joiner's business, so it is done once, below, and only when there are
    # two lines to separate.
    if cross_day:
        # One clause, because the headline already carries the day it compared
        # against and §1's screenshot counts the lines a reader must get past.
        # The card's own day is named and not its reference — «本卡所属的
        # CN-Mainland/2026-10-04» was a second `day_ref` on the same line, which
        # is the spelling §2 forbids and a reader has no use for: 「本卡」 is
        # today by construction, and the day it belongs to is what the card's
        # own timestamp already says.
        gap = (f"（跨日比较：上一次读数是 {dated} 的）" if dated else
               "（跨日比较：上一次读数属于另一个工作日）")
    elif unobserved > 0:
        # SAYS «NOT READ» AND NOT «THERE WERE NONE», in the fewest cells that
        # still says it. «–本时点之间有…没有读数» measured 398.1px in a 360px row
        # and wrapped, which on a card whose whole point is that an unread hour
        # is not a quiet hour is the worst line to lose half of. The subject of
        # the sentence is the missing readings, so it leads with them.
        gap = f"（{hhmm}–现在缺 {unobserved} 个时点读数，不是「无变化」）"
    else:
        gap = ""
    if not changed:
        # The gap clause and nothing else. What stood here was «状态词、观测与
        # 三个板块与 {hhmm} 相比都没有变化» — a second sentence under a headline
        # that already says «与 {hhmm} 相同». It is not a shorter way of saying
        # the first line; the only thing it adds is the CARD'S OWN vocabulary,
        # the list of fields the comparison is made of, which is a sentence
        # about the comparing rather than about the machine. §2 sends that to
        # the back with the other design notes, and §1's screenshot is a count
        # of lines a reader has to get past: this was one of them, on every
        # quiet hour, to say a second time what the line above it said. The
        # clause that stays is the one carrying a fact the headline does not —
        # that hours between the two readings were never observed at all.
        return {"headline": f"与 {hhmm} 相同", "from": hhmm,
                "unobserved": unobserved, "detail": gap, "moved": []}
    return {"headline": f"较 {hhmm} 有 {len(changed)} 处变化", "from": hhmm,
            "unobserved": unobserved,
            # One line each, joined only when both are there: the gap clause is
            # a claim about the hour and the names are what moved in it. An
            # empty join, not a trailing separator, when one side is absent.
            "detail": "\n".join(p for p in (gap, "；".join(named)) if p),
            # The whole list, spelled, for a reader of the MODEL rather than of
            # the card. The face prints `detail`, which drops the roll-up; the
            # count above uses this list, and a caller that has to explain «1 处
            # 变化» beside a clause naming no block needs both to be readable.
            "moved": list(changed)}


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
        return {"name": name, "state": UNKNOWN, "lines": [], "metrics": [],
                "why": "这一栏没有被采集，因此不对它作任何断言"}
    return {"name": name, "state": state, "lines": list(payload.get("lines") or []),
            # The same readings as the lines above, in cells. Passed through
            # rather than derived here, because deriving them would mean this
            # function parsing the sentences back into numbers — see
            # `card_source.metric` for why that is the shape being avoided.
            "metrics": list(payload.get("metrics") or []),
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
        # UNBOUND is an ANSWER, and it is carried as itself for that reason.
        # It used to be rewritten to UNKNOWN on this line, which made
        # `env_health` file it under `unread` and turn the whole card grey over
        # a question nobody had failed to answer — the section had established
        # exactly what there was to establish, that no service binding names
        # this node's shared gateway.
        #
        # §6's rule is that UNKNOWN is never green. Reading an established
        # absence as UNKNOWN is the same mistake with the sign flipped: a
        # reader is told to go and get a reading that already exists, while the
        # card's own body says a reading is not what is missing. The two states
        # have different repairs and only one of them is a repair.
        return {"name": "gateway", "state": GATEWAY_UNBOUND, "lines": [],
                "metrics": [], "why": section["why"], "detail": section,
                # No endpoint and no `entry` with it: `gateway_section` returns
                # UNBOUND precisely when no binding named a gateway, so there is
                # no address to carry. Stated rather than left absent so the two
                # branches of this function return one shape.
                "entry": None}
    gateway = section
    probe = gateway.get("probe") or {}
    lines = []
    # THE LOOPBACK IS NOT ONE OF THE LINES, and the reason is where a line goes.
    # `cards.status_card` prints `lines[0]` on the face and folds the rest, so
    # an endpoint at the head of this list is the FIRST SCREEN of the gateway
    # block for every gateway whose readings are nothing but where it listens —
    # and §R15 names loopback, with UID and model number, among the things a
    # reader scrolls past before reaching a reading. It is carried as its own
    # field instead, so the face can only ever be a reading, and the fold gets
    # it by name rather than by its position in a list.
    entry = None
    if gateway.get("endpoint"):
        entry = f"本机入口 {gateway['endpoint']}"
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
    # Absent readings stay UNKNOWN and never OK — AND SO DO UNRECOGNISED ONES.
    # `or UNKNOWN` above catches the first case and did not catch the second,
    # and `UNAVAILABLE` is the second case, measured on this machine on
    # 2026-10-04. `monitoring_model` emits that word when the shared gateway's
    # own accounting cannot be read — the binding handed to the renderer that
    # day carried `state: UNAVAILABLE` beside `probe: {state: OK}`, which is two
    # axes reported honestly and collapsed here into one bad answer. It arrived
    # verbatim, matched neither `env_health`'s `faulted` nor its `unread`, and so
    # reached the else branch: the card said 「三项均已读到且无异常」 and this
    # block's lead said 就绪, over a reading that was never made. Green over a
    # failed reading is what §6 forbids, and the shape of it is «a word this card
    # was never taught», not this one word — so the gate is a whitelist of the
    # readings in `SECTION_READINGS` and the fallback is UNKNOWN. A state an
    # upstream producer invents later lands on the neutral side by default,
    # which is the side a reader can act on: `env_health` files it as unread and
    # `card_day._state_word` prints it as 无法确认.
    # No metric cells, and this is a decision rather than an omission. The two
    # numeric blocks lead with a percentage a reader scans; this block is a list
    # of NAMED THINGS and how each of them is, and every one of those sentences
    # is composed from the declared catalogue in this very function — the wording
    # authority for a gateway line is `card-definitions.yaml` and there is one of
    # it. A cell built here from the same provider dict would be a second
    # composition of the same reading, and the first reword of one of the two
    # templates would leave the face and the fold describing one gateway in two
    # vocabularies.
    accounting = gateway.get("observed_state") or UNKNOWN
    if accounting not in SECTION_READINGS:
        accounting = UNKNOWN
    return {"name": "gateway", "state": accounting,
            "lines": lines, "metrics": [], "why": "", "detail": gateway,
            "entry": entry}


if __name__ == "__main__":  # pragma: no cover - a hand check, not an entry point
    print(f"{SCHEMA}: {len(known_codes())} codes, "
          f"{len(_catalogue().get('codes') or {})} defined")
