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
IMPACT_UNCONFIRMED = "影响尚未确认"

#: What a reader may do, when the catalogue names nothing for a code. It names
#: no operation, and it is the default on purpose: the catalogue is allowed to
#: be silent about a code, and silence must not read as "nothing to do".
DEFAULT_ACTION = "在 ServerOS 登记表里查看该条目；本卡不会停止、重启或清理任何东西"

#: §15, restated on every action because a card is the surface where it is
#: most easily lost. `recommendation()` already returns a suggestion; the word
#: travelling alone is what gets read as an instruction.
ACTION_AUTHORITY = "建议，不是授权；没有任何动作会因此发生"

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
        return {"state": ABSENT, "code": code, "what": None, "why": (
            f"card-definitions.yaml 没有为 {code!r} 声明含义；本卡照原名列出"
            f"该 code，不对它作任何断言")}
    return {"state": DECLARED, "code": code,
            "what": str(entry["what"]).strip(),
            "impact": (str(entry["impact"]).strip() if entry.get("impact") else None)}


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
# The subject. §5: "对象显示名"。
# ---------------------------------------------------------------------------

#: The prefixes `alerting.RESOURCE_PREFIXES` already treats as naming a
#: resource, each with the rule that turns the rest of the ref into something a
#: person recognises. Ordered longest-prefix-first by hand: `process-tree:session-`
#: and `process-tree:` both match a session tree, and the more specific one has
#: to win or every session tree renders as an opaque pid blob.
_SUBJECT_RULES = (
    ("runtime-group:process-tree:session-", lambda rest: _tree(rest, "会话")),
    # A container's process tree, which is the shape most of this node's
    # findings take. Named as a container rather than left as `system.slice:
    # docker:<id>`: the slice, the runtime and the id are three layers of where
    # the process happens to live, and the card is about one container.
    ("runtime-group:process-tree:system.slice:docker:",
     lambda rest: f"Docker 容器 {rest}"),
    ("runtime-group:process-tree:", lambda rest: f"进程组 {rest}"),
    ("runtime-group:claude-session:", lambda rest: f"Claude 会话 {rest}"),
    ("runtime-group:", lambda rest: f"运行时组 {rest}"),
    ("filesystem:", lambda rest: f"文件系统 {rest}"),
    ("host:", lambda rest: f"整机 {rest}"),
    ("device:", lambda rest: f"设备 {rest}"),
    ("tunnel:", lambda rest: f"隧道 {rest}"),
    ("container:", lambda rest: f"容器 {rest}"),
    ("runtime:", lambda rest: f"受管运行时 {rest}"),
)


def _tree(rest: str, what: str) -> str:
    """`<session>:pid<pid>` — the one shape a process tree ref has.

    The pid is kept in brackets rather than dropped. "会话 150 的进程组" names
    a session that may hold several trees, and the alert is about one of them.
    """
    session, _, pid = rest.partition(":pid")
    return (f"{what} {session} 的进程组（pid {pid}）" if pid
            else f"{what} {session} 的进程组")


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


def subject_display(subject_ref: str | None, *, owner: dict | None = None) -> dict:
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
    """
    ref = str(subject_ref or "")
    text = None
    rule = None
    for prefix, expand in _SUBJECT_RULES:
        if ref.startswith(prefix):
            text, rule = expand(ref[len(prefix):]), prefix
            break
    if text is None:
        # An unrecognised subject is printed whole. Guessing at its shape is
        # how `runtime-group:process-tree:session-150:pid28541` becomes
        # "会话 150" and stops naming the process the alert is actually about.
        text, rule = (ref or "（未记录对象）"), "verbatim"
    return {"ref": ref or None, "text": _abbreviate_ids(text), "rule": rule,
            "owner": dict(owner) if owner else None}


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
                sample: bool = False) -> dict:
    """At most `limit` events, each with the §4 field set, plus what was left out.

    §1 forbids the two shapes this is most likely to collapse into: the alert
    card must not reproduce the status card's three blocks, and it must not
    show the register's all-history OPEN/RESOLVED totals. What it may show is
    how much did NOT fit — §5: "静默不等于没事" is the misreading that a short
    card invites, and the count of omitted rows is the only thing standing
    between a person and that misreading.
    """
    rows = [r for r in (digest.get("rows") or []) if r.get("kind") in _ALERT_KINDS]
    counts = digest.get("counts") or {}
    events = [_event_of(row, at=at) for row in rows[:limit]]
    return {
        "schema": SCHEMA,
        "card": "alert",
        "sample": bool(sample),
        "sample_mark": SAMPLE_MARK if sample else None,
        "at": at,
        "device_ref": digest.get("device_ref"),
        "events": events,
        "omitted": {
            "rows": max(0, len(rows) - len(events)),
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


def _event_of(row: dict, *, at: str) -> dict:
    code = row.get("code")
    severity = str(row.get("severity") or "")
    definition = definition_for(code)
    duration = open_duration(row, at=at)
    return {
        "ref": row.get("key"),
        "code": code,
        "event": row.get("kind"),
        "what": {
            "text": definition["what"],
            "definition": definition["state"],
            # The code travels with every event whatever the catalogue says.
            # §5 removes the raw code from the card FACE, not from the record:
            # the detail keeps it, and it is the identity every other surface
            # keys on.
            "code": code,
            "why": definition.get("why"),
        },
        "subject": subject_display(row.get("subject_ref"), owner=row.get("owner")),
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


def _for_detail(row: dict, *, at: str) -> dict:
    """The row as `alerting.detail_for` needs it.

    `detail_for` reads `measured`, `provenance`, `window`, `occurrence_count`
    and `first_seen` — the fields the *register* carries. A digest row is the
    projected subset and does not carry `measured` at all, so the evidence
    block is rebuilt from what the row does have rather than being handed a
    shape it would silently read as empty. `resource_ref` and `owner` are
    passed through under their own names so the block can name them.
    """
    return {
        "code": row.get("code"),
        "severity": row.get("severity"),
        "first_seen": row.get("first_seen"),
        "occurrence_count": row.get("occurrence_count"),
        "window": None,
        "provenance": row.get("provenance") or {},
        "measured": {"ownership": row.get("owner")} if row.get("owner") else {},
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
        verdict, word = UNCONFIRMED, "有项目需要处理，且有项目未读到"
    elif faulted:
        verdict, word = ATTENTION, "有项目需要处理"
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
        "coverage": {
            # §3. The shared gateway and the personal container0 are different
            # things and the card says which of them each number came from.
            "personal_container0": "mine",
            "shared_gateway": "gateway",
            "note": ("共享网关与个人 container0 分开呈现；同一台机器上的两个"
                     "对象，不是同一件事"),
        },
    }


def _section(name: str, payload: dict | None, gaps: list) -> dict:
    payload = payload or {}
    state = payload.get("state")
    if state is None:
        return {"name": name, "state": UNKNOWN, "lines": [],
                "why": "这一栏没有被采集，因此不对它作任何断言"}
    return {"name": name, "state": state, "lines": list(payload.get("lines") or []),
            "why": payload.get("why") or "", "detail": payload.get("detail") or {}}


def _gateway_state(section: dict) -> dict:
    if section["state"] == GATEWAY_UNBOUND:
        return {"name": "gateway", "state": UNKNOWN, "lines": [],
                "why": section["why"], "detail": section}
    gateway = section
    probe = gateway.get("probe") or {}
    lines = []
    if gateway.get("endpoint"):
        lines.append(f"本机入口 {gateway['endpoint']}")
    if probe.get("text"):
        lines.append(str(probe["text"]))
    for provider in gateway.get("providers") or []:
        if provider.get("text"):
            lines.append(str(provider["text"]))
    if gateway.get("maintainer"):
        lines.append(f"负责人 {gateway['maintainer']}")
    return {"name": "gateway", "state": gateway.get("state") or UNKNOWN,
            "lines": lines, "why": "", "detail": gateway}


if __name__ == "__main__":  # pragma: no cover - a hand check, not an entry point
    print(f"{SCHEMA}: {len(known_codes())} codes, "
          f"{len(_catalogue().get('codes') or {})} defined")
