#!/usr/bin/env python3
"""One model, two renderings: the Feishu card a person receives and the preview
a person can inspect before anything is sent.

Why the HTML exists at all
--------------------------
§6 of OBSERVABILITY-CARDS.md requires both, from the *same* renderer: "交付同一
renderer 生成的 JSON 与 HTML/可视预览". The requirement is not decoration. A card
is read on a phone, the phone is 390px wide, and there is no way to find out
whether the layout survives that width by reading the JSON. Every previous
attempt on this node to check a card meant sending one to a real conversation
and looking at the result — which is a production action taken to answer a
design question, and it is why the preview exists.

**The preview is not delivery evidence.** §6 says so in as many words: a
rendered HTML file proves what the renderer produces, not what Feishu displays,
and the two are different claims. Nothing in this module sends anything.

Element vocabulary
------------------
Deliberately tiny: `markdown`, `hr`, `collapsible_panel`, `column_set`. All
four are in use in the cards this node already sends, so they are known to be
accepted by the schema version in production. §6: "按当前 card schema/SDK 校验，
不从草图发明 JSON 属性" — inventing a plausible tag is how a card renders as a
blank rectangle in a conversation nobody is watching.

Coercion
--------
Everything a card shows came out of a model as a string, and every string came
out of a rule. Nothing here formats a number, chooses a colour from a threshold,
or decides what a field means. A renderer that decides is a second place the
decisions live.
"""
from __future__ import annotations

import html as _html
import importlib.util
import json
import sys
from datetime import datetime, timezone
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


presentation = _load("serveros_runtime_presentation", "presentation.py")

#: §6. Colour encodes the semantics of the summary and nothing else, and the
#: map is closed: a verdict this module has not been taught renders the neutral
#: template rather than a template chosen by a substring match.
#:
#: `UNCONFIRMED` is grey on purpose. §6: "未知用中性而非正常绿" — a summary that
#: could not read one of its three blocks has not earned green, and it has not
#: earned amber either: amber says a person should act, and "nobody looked" is
#: a different instruction.
TEMPLATES = {
    "OK": ("green", "✅"),
    "ATTENTION": ("orange", "⚠️"),
    "CRITICAL": ("red", "🚨"),
    "UNCONFIRMED": ("grey", "❔"),
}
NEUTRAL_TEMPLATE = ("grey", "❔")


def _template(verdict: str | None) -> tuple[str, str]:
    return TEMPLATES.get(str(verdict or ""), NEUTRAL_TEMPLATE)


def md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def panel(title: str, blocks: list[str], *, expanded: bool = False) -> dict:
    """The collapsible panel, matching the shape the live cards send."""
    return {
        "tag": "collapsible_panel",
        "expanded": expanded,
        "header": {
            "title": {"tag": "markdown", "content": f"**{title}**"},
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "size": "16px 16px"},
            "icon_position": "follow_text",
            "icon_expanded_angle": -180,
        },
        "border": {"color": "grey", "corner_radius": "5px"},
        "padding": "8px 8px 8px 8px",
        "vertical_spacing": "8px",
        "elements": [md("\n".join(blocks))],
    }


def columns(*blocks: list[dict]) -> dict:
    """Two columns on a desktop, one on a phone, without writing it twice."""
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "horizontal_spacing": "8px",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1,
             "vertical_align": "top", "elements": b}
            for b in blocks
        ],
    }


def _titled(title: str, model: dict) -> str:
    """The card's title, with the sample mark on it when there is one.

    On the title as well as in the body, for all three card kinds. The mark has
    to survive the two things people do with a card — screenshot it and forward
    it — and both of those keep the header and drop the body more often than
    the other way round. The first draft put it only on the status card's
    title, which meant the alert card's sample was one crop away from being
    read as a real alert.
    """
    if model.get("sample"):
        return f"{title} · {presentation.SAMPLE_MARK}"
    return title


def _sample_line(model: dict) -> list[dict]:
    """§2/§6. A sample says so, in the card, above everything else it says.

    A preview that is indistinguishable from a reading is worse than no
    preview: the whole reason to build one is to look at the card, and a person
    looking at a sample they cannot identify will read it as the machine's
    current state. The mark sits in the header as well — this line is what
    survives when a card is quoted, screenshotted or forwarded.
    """
    if not model.get("sample"):
        return []
    return [md(f"🧪 **{presentation.SAMPLE_MARK}** —— 以下内容为布局示例，不是本次采集")]


#: §15's `alert != authorization`, said once for the whole card.
#
# WHY ONE LINE AND NOT ONE PER ACTION
#
# Every action line and every action in the catalogue used to carry its own
# refusal ("本卡不会重建它", "本卡不会停止、重启或清理任何东西"). Repeating it
# beside every suggestion is what made a card of three events read as a
# disclaimer: by the third repetition a reader stops reading it, which is the
# opposite of what a refusal is for. It is stated once, at the foot, where a
# reader who has decided to act will look for it, and it is now the ONLY line on
# the face that names an operation at all — so it is also the only one that has
# to be reviewed for naming one.
FOOTNOTE_READ_ONLY = "只读告警 · 未执行终止、重启或清理操作"


def _footnote(model: dict) -> list[dict]:
    return [md(FOOTNOTE_READ_ONLY)]


# ---------------------------------------------------------------------------
# The alert card. §1 + §4.
# ---------------------------------------------------------------------------

def alert_card(model: dict) -> dict:
    """What a person may be interrupted for, and nothing else.

    The card this replaces printed the alert register. Its first three lines on
    this node were three `HIGH_MEMORY_SUSTAINED` warnings about process trees —
    all true, none of them a violated expectation, and the sentence that
    mattered was below the fold. What changed is not the styling: the events
    here have already been filtered by the declared attention axis
    (`presentation.alert_model`), so the card can be short without being
    selective.

    Three things a reader gets in the first screen, in this order: what is
    wrong, what was lost sight of, and what a person might do. The §4 field set
    is still complete — it is inside the fold, one tap down, where a person who
    wants the ref, the raw code and the evidence can get them.
    """
    events = model.get("events") or []
    integrity = model.get("integrity")
    watch = model.get("watch") or {}
    undecided = model.get("undecided") or {}
    recovery = model.get("recovery") or {}

    # Colour follows ATTENTION, not severity. A card whose colour came from
    # severity would paint a node red for a WARNING nobody may act on, which is
    # the reading that made the old card look alarming on a quiet hour.
    template, icon = _template(_face_verdict(events, integrity))
    title = _titled(_face_title(events, integrity), model)

    els: list[dict] = _sample_line(model)
    # The title is NOT repeated here. It is already the header, and the first
    # draft printed it twice — once in the coloured bar and once as the first
    # body line — which on a 390px screen spends the most valuable line of the
    # card saying what the line above it just said.
    #
    # AN EMPTY CARD IS A REAL ANSWER AND THE COUNTS ARE WHAT KEEP IT HONEST.
    # "没有新变化" over a register with 113 backlogged entries and 1112
    # never-announced resolutions is true and misleading, which is why a quiet
    # card still prints `_summary_line` below. It carries the qualification as
    # counts, so a quiet card needs no sentence of its own: "安静不等于没事"
    # once stood here, and a card that has to tell a reader how to read it is
    # a card that has not said the thing. CARDS-STYLE.md R2 / R8.
    for event in events:
        # THE FACE IS NOT THE PANEL HEADER. The first version of this put the
        # four face lines INSIDE the collapsible panel, which on a 390px screen
        # with nothing expanded means a person sees the header and nothing
        # else — the situation, the impact and the suggestion were all one tap
        # away and the card was as unreadable as the one it replaced, in a
        # tidier font. The face is markdown, always visible; the §4 detail is
        # the panel, and it is collapsed.
        els.append(md(_event_face(event)))
        els.append(panel(_detail_title(event, multi=len(events) > 1),
                         _event_detail(event), expanded=False))

    if integrity:
        # Same split as an event, and for the same reason: the finding is one
        # sentence a person has to be able to read without tapping anything.
        els.append(md(_integrity_face(integrity)))
        els.append(panel("技术详情 · 监控完整性", _integrity_detail(integrity),
                         expanded=False))

    other = _summary_line(model, watch, recovery)
    if other:
        els.append(md(other))
    if undecided.get("count"):
        els.append(md(_undecided_line(undecided)))
    els.append(md(f"采集 {_stamp(model)} · 设备 {model.get('device_ref') or '未知'}"))
    els.extend(_footnote(model))
    return _card(template, title, els, model)


def _face_levels(events: list[dict], integrity: dict | None) -> list[str]:
    """Every attention level the face is about to carry, sorted by the axis.

    The sort is `presentation.AXIS`'s order and not this module's: the contract
    says the axis order is the reading order, and a renderer that re-sorted
    would be re-ranking the axis at the point where the re-ranking is least
    visible.
    """
    levels = [str(e["attention"]["level"]) for e in events]
    if integrity:
        levels.append(str((integrity.get("attention") or {}).get("level") or ""))
    order = list(presentation.AXIS)
    return sorted((lvl for lvl in levels if lvl in order),
                  key=order.index)


def _face_verdict(events: list[dict], integrity: dict | None) -> str:
    """The card's colour, from the axis rather than from the severity levels.

    ACTION_REQUIRED is the only level that paints the card red, and it is
    reachable only through a declaration somebody marked critical. Everything
    else that reaches the face is orange, and a face with nothing on it is
    green — including a face whose news was entirely recoveries.
    """
    levels = _face_levels(events, integrity)
    if "ACTION_REQUIRED" in levels:
        return "CRITICAL"
    if levels:
        return "ATTENTION"
    return "OK"


def _face_title(events: list[dict], integrity: dict | None) -> str:
    """One sentence, carrying the WORST level's own word.

    Not a count with a fixed word beside it: "1 项需确认" over a row the
    contract levelled ACTION_REQUIRED would be the renderer softening the axis,
    and the word is the contract's precisely so that cannot happen here.

    The quantifier is 项 for findings and 个 for subjects, and the split is
    worth stating because the alternative is drift: 件 reads as a parcel of
    work rather than a count of findings, and a card that says 件 here and 条 in
    the summary line is counting the same rows in two units.
    """
    levels = _face_levels(events, integrity)
    parts = []
    if events:
        parts.append(f"{len(events)} 项")
    if integrity:
        parts.append(f"{integrity['count']} 个主体失去观测")
    if not parts:
        return "✅ 没有需要处理的变化"
    icon = "🚨" if _face_verdict(events, integrity) == "CRITICAL" else "⚠️"
    return f"{icon} {presentation.attention_word(levels[0])} · " + " · ".join(parts)


def _event_title(event: dict) -> str:
    """`<subject> · <what the catalogue calls this>` — the console's first line.

    The attention word is NOT here. The header carries it already ("⚠️ 需确认 ·
    1 项"), and repeating it on every event spends the widest line of a 390px
    card on a word the reader read six lines ago. What the line owes the reader
    instead is what the subject IS to this finding, and that is the catalogue's
    `label`: `登录会话 15413 · 遗留进程组` scans; `WARNING · 登录会话
    15413 的进程组（pid 1315723）` is an identifier with a severity glued on.

    A code with no declared `label` keeps the subject alone on its line. A noun
    invented here would be a second definition of a code that already has one,
    written by the renderer rather than reviewed with the catalogue.
    """
    subject = event["subject"]["text"]
    label = event["what"].get("label")
    return f"{subject} · {label}" if label else subject


def _detail_title(event: dict, *, multi: bool) -> str:
    """What the fold is called.

    Not the event's own title: a panel headed with the line directly above it
    puts the same sentence on the card twice, once visible and once as the
    thing you tap, and the reader who taps it finds what they already read.

    `multi` is the one case that forces the subject back in. With three events
    the card carries three collapsed panels, and three headers reading 技术详情
    are three folds a person cannot tell apart without opening them. With one
    event the position is the whole answer and the header stays bare — which is
    the common shape, decided at the call site where the count is known.
    """
    if multi:
        return f"技术详情 · {event['subject']['text']}"
    return "技术详情"


def _event_face(event: dict) -> str:
    """The first screen: what is wrong, what it costs, what to look at. Four lines.

    Four and not ten, and VISIBLE rather than folded. The field set §4 lists
    has ten members and a person on a phone reads four of them before the taxi
    arrives; the other six are in `_event_detail`, which is the same event one
    tap down. Nothing is removed — it is ordered.

    Line one is `_event_title`'s, because the subject is what makes the other
    three actionable. On a container finding it is the name the registration
    gave the service, and the twelve hex characters a person cannot use are in
    the detail with the ref.

    The label on line two is 状态 and not 发生了什么. 发生了什么 asks a question
    and invites a sentence in reply; 状态 is a field, and a console's field
    holds a state ("会话已结束，关联进程仍在运行") rather than a narrative. The
    two said the same thing and only one of them read as a console.
    CARDS-STYLE.md R1.

    The duration rides on the 首次观测 line instead of taking one of its own.
    It is the same fact — how long this has been true — and a fifth labelled
    line on a 390px screen costs more than it carries.
    """
    what = event["what"]
    times = event["times"]
    impact = event["impact"]
    # The local time, because a person reading this is in a place. The ISO
    # instant is in the detail for anything that has to be matched against a log.
    first = _local(times["first_seen"])
    lines = [
        f"**{_event_title(event)}**",
        f"**状态** {what['text'] or what['code']}",
        f"**影响** {_impact_text(impact['text'])}",
        f"**建议** {event['action']['text']}",
        f"**首次观测** {first or '未知'}"
        + (f" · 持续 {times['duration_text']}" if times["duration_text"] else ""),
    ]
    named = _owner_line(event["subject"].get("owner"))
    if named:
        lines.append(named)
    return "\n".join(lines)


def _impact_text(text: str) -> str:
    """The impact, said once, in the console's words.

    The sentence that stood here was a paragraph: "尚未确认 —— 本卡没有这条代码的
    影响证据，也不从代码本身推后果". It was written to repair a stutter (`**影响**
    影响尚未确认` printed the same word twice) and it repaired it by explaining
    the card to the reader instead of telling them the impact. The placeholder
    itself now reads `暂未发现明确影响`, so the line needs no repair and the
    second sentence has nothing left to say. CARDS-STYLE.md R2 / R7.

    The placeholder is still carried verbatim in the model, the JSON payload
    and the CLI, because that is the value §4 names.
    """
    return text


def _owner_line(owner: dict | None) -> str:
    """The producer's own attribution, when it said anything a person can use.

    Returns nothing rather than an empty labelled line. A `**归属** ` with
    nothing after it is on the face as a field a reader has to interpret as
    absent — which is what the first draft of this printed on every event whose
    owner block was present but empty.
    """
    if not owner:
        return ""
    named = " · ".join(str(owner.get(k)) for k in ("class", "role", "session", "unit")
                       if owner.get(k))
    return f"**归属** {named}" if named else ""


def _event_detail(event: dict) -> list[str]:
    """Everything §4 asks for, including what must never be on the first screen.

    The rule this satisfies is a test and not a convention: the face carries no
    raw pid, no alert ref, no anomaly code and no registry counter, and this
    carries all four. A card that leaked them to the face would be a card whose
    first line is an identifier nobody can read, which is the failure this
    round exists to remove.
    """
    what = event["what"]
    certainty = event["certainty"]
    times = event["times"]
    lines = []
    # The subject's precise form, when the face had to shorten it. On a session
    # finding the face says `登录会话 15413` and this says which of that
    # session's trees the alert is about; the pid is the only handle a person
    # has when they go to look, so it is never dropped, only moved.
    if event["subject"].get("precise"):
        lines.append(f"**对象** {event['subject']['precise']}")
    lines += [
        f"**异常代码** {what['code']}",
        f"**事件类型** {event['event']} · **机器严重度** {event['severity']['word']}"
        f"（{event['severity']['level']}）",
        f"**注意力** {event['attention']['level']}"
        + (f" —— {event['attention']['because']}" if event["attention"].get("because")
           else ""),
        f"**确定性** {certainty['state']}"
        + (f" —— {certainty['why']}" if certainty["why"] else "")
        + f" · 来源 {certainty['source'] or '未记录'}",
        f"**建议** {event['action']['word']} —— {event['action']['text']}",
        f"**建议的效力** {event['action']['authority']}",
        f"**首次观测** {times['first_seen'] or '未记录'}"
        f" · **最近** {times['last_seen'] or '未记录'}",
        f"**持续时间** {times['duration_text']}"
        + (f"（按 {times['duration']['basis']} 计）" if times["duration"]["basis"]
           else f"（{times['duration'].get('why') or '无法计算'}）"),
        f"**引用** {event['ref'] or '未记录'}"
        f" · **对象引用** {event['subject']['ref'] or '未记录'}",
    ]
    if event["subject"]["owner"]:
        owner = event["subject"]["owner"]
        raw = " · ".join(f"{k} {owner.get(k)}" for k in
                         ("class", "role", "lifecycle", "uid", "session", "unit")
                         if owner.get(k))
        # An owner block that arrived with none of the six fields is a block
        # that says nothing, and printing its label over an empty value puts a
        # question on the card that the reader has to answer themselves.
        if raw:
            lines.append("**归属（生产者原始字段）** " + raw)
    lines.append("**证据与详情**\n" + (event.get("evidence") or ""))
    return lines


#: How many distinct codes the integrity heading names before it stops. The
#: live finding this was written against was 542 rows; a heading that listed
#: every code behind them would be the register dump the aggregation rule
#: exists to prevent, one line further up the card.
INTEGRITY_CODES_SHOWN = 3


def _integrity_heading(finding: dict) -> str:
    """Which declarations lost observation — the one thing the header cannot say.

    The header already carries both the level and the count ("⚠️ 需确认 · 3 个
    主体失去观测"), so a first body line repeating either spends the most
    valuable line of a 390px card on what the line above it just said. The
    first draft of this face did exactly that — a bold count — and the render
    put the same number on the card three times in four lines. What the header
    cannot carry is WHICH expectations are now unverified, and that is what a
    heading is for.
    """
    counts = finding.get("by_code") or {}
    ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    # `×1` is dropped rather than printed. Every code carries a count and most
    # carry one, and three `×1`s on a heading cost a line of a 390px screen to
    # say nothing; a number appears exactly where it is the interesting part,
    # which is a code standing behind more than one subject.
    shown = " · ".join(f"{code} ×{n}" if n > 1 else code
                       for code, n in ordered[:INTEGRITY_CODES_SHOWN])
    rest = len(ordered) - INTEGRITY_CODES_SHOWN
    if rest > 0:
        shown += f" · 等 {rest} 类"
    return f"**监控完整性 · {shown}**" if shown else "**监控完整性**"


def _integrity_face(finding: dict) -> str:
    """The same labelled lines an event gets, for the same reason.

    The first version of this left the finding inside a collapsed panel, which
    on a 390px screen meant the card's own headline — "3 个主体失去观测" — was
    visible and every word explaining it was not. A person who reads only the
    header of that card learns that something is wrong and not what, which is
    the failure mode this round exists to remove. The face says which
    declarations went unverified and what that means; the panel below is the
    enumeration.
    """
    return "\n".join([
        _integrity_heading(finding),
        f"**状态** {finding['text']}",
        f"**影响** {finding['impact']}",
        f"**建议** {finding['action']}",
    ])


def _integrity_detail(finding: dict) -> list[str]:
    """One finding, however many subjects it is made of.

    §5's aggregation rule: 500 stale objects are one line, not 500 faults. What
    the card states is how many declared subjects lost observation and which
    kinds of declaration are therefore unverified — the enumeration belongs in
    `serverosctl alert list`, where a person who wants it can get it.
    """
    by_code = finding.get("by_code") or {}
    return [
        finding["text"],
        "**按异常代码** " + " · ".join(f"{code} ×{n}"
                                       for code, n in sorted(by_code.items())),
        "**这意味着** 本卡无法判断这些主体的当前状态。观测恢复前，"
        "「未再观测到」不等于「已恢复正常」。",
        f"**不作为故障计数** {finding['not_a_finding']['note']}"
        f"（{finding['not_a_finding']['count']} 项）",
        f"**注意力** {(finding.get('attention') or {}).get('because') or ''}"
        .rstrip(),
    ]


def _summary_line(model: dict, watch: dict, recovery: dict) -> str:
    """The line that keeps a short card honest.

    Every count here is a thing the card did NOT print. "静默不等于没事" is the
    misreading a short card invites, and a count is the cheapest thing that
    stops it without turning the card back into a register dump.

    THREE NUMBERS, NOT SIX, AND ONE NOUN EACH. The line that stood here read:

        另有 487 条低于打断级别（值得留意，未占卡面） · 4 条此前报告过的问题已恢复
        · 2807 条在更早的批次里已被计数 · 110 条在投递基线之前就已存在（积压，不重复
        播报） · 21 条从未告知过，不计入恢复

    Every clause was true and the whole thing was unreadable, because it is the
    alert ledger's own bookkeeping: `beyond_card_cap` versus `rows` is a
    distinction between two of this card's internal caps, and no reader outside
    this repository has any use for it. What a person needs off the first
    screen is "how much did you leave out", which is one number.

    THREE COUNTS, AND EACH ONE ANSWERS "how much did you leave out". The face
    keeps exactly three — how many were below the interruption level, how many
    were reported and have recovered, how many predate the delivery baseline.
    Everything else the ledger counts is arithmetic *about* those numbers and
    it belongs where the rest of the arithmetic already lives:

      * `beyond_card_cap` versus `rows` is a distinction between two of this
        card's own internal caps, and no reader outside this repository has any
        use for it.
      * `silent_resolutions` is the recovery ledger's own footnote (a resolution
        of a row nobody was told about). It has a line of its own on the recovery
        card, which is the card that is about recoveries; here it was a fourth
        clause on a line whose subject is "what did not fit".

    None of them is dropped from the payload or from `serverosctl alert digest`,
    which is where a reader who wants the arithmetic gets it. CARDS-STYLE.md R8.

    Empty when it has nothing to count, and the caller prints nothing rather
    than a sentence about the absence: "没有未进卡面的条目" is a line on a card
    whose whole purpose is to carry numbers, and a fallback that says "no
    numbers" costs a line to say nothing.
    """
    omitted = model.get("omitted") or {}
    parts = []
    if watch.get("count"):
        parts.append(f"{watch['count']} 项低优先级")
    if recovery.get("announced"):
        parts.append(f"{recovery['announced']} 项已恢复")
    if omitted.get("backlog"):
        parts.append(f"{omitted['backlog']} 项历史积压")
    return "**其他** " + " · ".join(parts) if parts else ""


def _undecided_line(undecided: dict) -> str:
    """A hole in the attention table, stated as a hole.

    The sentence that stood here ended "未决定不等于不重要——这是声明里的一个
    洞". The first half is the judgement the ⚠️ is for and it stays; the second
    half explains the card's own vocabulary back to a reader who is looking at
    a code list they cannot act on. What they need is the fact that these codes
    have no declared level, which is what the line now says.
    """
    return (f"⚠️ {undecided['count']} 项事件的 code 未在注意力表中定级"
            f"（{'、'.join(undecided.get('codes') or [])}）· 未定级不等于不重要")


def _stamp(model: dict) -> str:
    """When this card was made, in the form a person reads.

    `at` arrives as the ISO instant the ledger recorded — the same shape the
    digest publishes — and "采集 2026-09-21T18:41:33.327665+00:00" is the
    ledger's spelling of it, not a person's. Falls back to the raw value
    because a card with no timestamp at all is worse than an ugly one.
    """
    raw = model.get("at")
    return _local(raw) or str(raw or "未知")


def _local(instant: str | None) -> str:
    """An ISO instant as a local time a person reads, or nothing.

    Nothing rather than the raw string when it will not parse: a card that
    printed `2026-09-20T05:12:00+00:00` where a reader expects "13:12" has
    quietly moved the ISO instant onto the face, which is the thing the
    separation is for. The unparsed value is still in the detail, whole.
    """
    if not instant:
        return ""
    try:
        moment = datetime.fromisoformat(str(instant).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone().strftime("%m-%d %H:%M")


# ---------------------------------------------------------------------------
# The recovery card. §1 + §5.
# ---------------------------------------------------------------------------

def recovery_card(model: dict) -> dict:
    """Only what a person was told, and that has since recovered.

    One line per recovery and no panel: §5's rule that a recovered thing gets
    less space than an unrecovered one is implemented upstream by the count
    this model carries, and a card that gave each recovery a fold-out would
    make the card longer the better the news was.
    """
    items = model.get("items") or []
    counts = model.get("counts") or {}
    template, icon = _template("OK" if items else "UNCONFIRMED")
    title = _titled(f"{icon} 已恢复 · {counts.get('announced') or 0} 项"
                    if items else f"{icon} 本批没有已告知且已恢复的问题", model)

    els: list[dict] = _sample_line(model)
    for item in items:
        what = item["what"]
        els.append(md(
            f"✅ **{what['what'] or what['code']}**\n"
            f"**对象** {item['subject']['text']}\n"
            f"**恢复依据** {item['basis']['note']}（state {item['basis']['state']}）\n"
            f"**出现于** {item['times']['first_seen'] or '未记录'} · "
            f"**恢复于** {item['times']['resolved_at'] or '未记录'} · "
            f"**持续** {item['times']['open_for_text']}"))

    omitted = model.get("omitted") or {}
    tail = []
    if omitted.get("announced_beyond_limit"):
        tail.append(f"另有 {omitted['announced_beyond_limit']} 项已告知的恢复未进卡面")
    # `never_announced` is a resolution of an alert the register never delivered,
    # so nobody experienced it going wrong and "recovered" would be a word this
    # card put in their mouth. The count says which rows those are; the sentence
    # that once explained that here ("人不能被通知一个他从来没收到过的坏消息")
    # was the card arguing its own case to the reader, and the count carries it.
    if omitted.get("never_announced"):
        tail.append(f"{omitted['never_announced']} 项从未告知，不计入恢复")
    if tail:
        els.append(md(" · ".join(tail)))
    els.append(md(f"本批恢复总数 {counts.get('recovered') or 0} · "
                  f"其中已告知 {counts.get('announced') or 0} · 采集 {_stamp(model)}"))
    els.extend(_footnote(model))
    return _card(template, title, els, model)


# ---------------------------------------------------------------------------
# The status card. §2 + §3.
# ---------------------------------------------------------------------------

_SECTION_TITLES = {
    "mine": "我的资源 · 当前账户",
    "host": "整机资源",
    "gateway": "共享 AI 网关 · 与个人 container0 分开",
}


def status_card(model: dict) -> dict:
    """The three fixed blocks, each collapsible, under the one-line verdict.

    §6's acceptance is about this card specifically: on a 390px screen the
    first line of each of the three blocks has to be legible without expanding
    anything. That is why each block renders its first line outside the panel's
    fold and puts the rest inside.
    """
    health = model.get("env_health") or {}
    template, icon = _template(health.get("verdict"))
    # The title names the broadcast, not a finding. "环境状态" read as a
    # classification and sat beside an alert card whose title also read as one,
    # so the two faces looked like the same product saying two things. This one
    # is the hourly dashboard and it says so; silence about problems belongs to
    # the alert face, which exists for exactly that.
    title = _titled(f"{icon} {model.get('node_label') or '本机'} · 资源播报", model)

    els: list[dict] = _sample_line(model)
    els.append(md(f"**环境可用性** {health.get('verdict_text')}\n"
                  f"**观测** {health.get('observation_text')}"))
    # Why this card exists now, said on the face rather than in the log. §10
    # gives routine broadcasts a window and §11 gives each workday one card, and
    # neither is checkable by a reader who cannot see which day the card thinks
    # it is and whether it was inside the window — so the schedule line is
    # printed before the blocks and not folded into the footer with the
    # collection stamp, which answers a different question.
    schedule = model.get("schedule") or {}
    if schedule.get("says"):
        els.append(md(f"**播报日程** {schedule['says']}"))
    # §11: one card per workday, the latest fully expanded and the day's earlier
    # hours folded. The folded half is printed only when there IS one — a panel
    # that opens onto nothing is one tap away from the explanation it looks like
    # it is hiding, which is why the section blocks open one conditionally too.
    # The lines arrive already formatted in the model: a renderer that knew how
    # to read a history row would be a second reader of the row's schema.
    day = model.get("day") or {}
    if day.get("says"):
        els.append(md(f"**当天卡** {day['says']}"))
        if day.get("lines"):
            els.append(panel(f"今日历史（{len(day['lines'])} 小时）",
                             list(day["lines"]), expanded=False))
    for name in model.get("order") or presentation.SECTIONS:
        section = (model.get("sections") or {}).get(name) or {}
        lines = section.get("lines") or []
        # The first line stays outside the fold — §6's "三板块首行可辨".
        els.append(md(f"**{_SECTION_TITLES.get(name, name)}**\n"
                      + (lines[0] if lines
                         else f"未读到：{section.get('why') or '未采集'}")))
        # A panel only when there is something to put in it. The first draft
        # opened one whenever the section had a `why`, which for an unbound
        # gateway is the same sentence already printed above it — so the card
        # carried a fold that opened onto nothing, one tap away from the
        # explanation it appeared to be hiding.
        if len(lines) > 1:
            els.append(panel(_SECTION_TITLES.get(name, name), lines[1:],
                             expanded=False))
    if health.get("gaps"):
        els.append(md("**采集缺口** " + "；".join(str(g) for g in health["gaps"])))
    els.append(md(f"采集时间 {_stamp(model)} · 观测范围 "
                  f"{'、'.join(health.get('sections_read') or []) or '无'}"
                  + (f" · 未读到 {'、'.join(health.get('sections_unread') or [])}"
                     if health.get("sections_unread") else "")))
    els.append(md((model.get("coverage") or {}).get("note") or ""))
    els.extend(_footnote(model))
    return _card(template, title, els, model)


def _card(template: str, title: str, els: list[dict], model: dict) -> dict:
    """The envelope. `schema: 2.0` and the `body` wrapper — the shape the live
    status card sends, rather than the older flat `elements` one."""
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": f"{_stamp(model)} 更新"},
        },
        "body": {"direction": "vertical", "elements": els},
    }


# ---------------------------------------------------------------------------
# The HTML preview. §6.
# ---------------------------------------------------------------------------

#: The narrow width §6 names. Everything below is written so that this is a
#: measurement rather than a hope: no fixed widths, no tables, `overflow-wrap`
#: on every text container, and the two-column layout collapses to one.
MOBILE_WIDTH = 390

_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ color-scheme: light dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 12px;
       font: 15px/1.55 -apple-system, "Segoe UI", "Noto Sans CJK SC", sans-serif;
       overflow-wrap: anywhere; word-break: break-word; }}
.wrap {{ max-width: 720px; margin: 0 auto; }}
.frame {{ border: 1px solid #d0d4d9; border-radius: 10px; overflow: hidden;
          margin: 0 0 18px; background: #fff; }}
.frame.narrow {{ width: {mobile}px; max-width: 100%; }}
.hdr {{ padding: 12px 14px; color: #fff; }}
.hdr h2 {{ margin: 0; font-size: 16px; }}
.hdr .sub {{ opacity: .85; font-size: 12px; }}
.green {{ background: #2ea44f; }} .orange {{ background: #d9822b; }}
.red {{ background: #d13438; }} .grey {{ background: #6a737d; }}
.body {{ padding: 12px 14px; }}
.body p {{ margin: 0 0 10px; white-space: pre-wrap; }}
.panel {{ border: 1px solid #d0d4d9; border-radius: 6px; margin: 0 0 10px; }}
.panel > summary {{ padding: 8px 10px; cursor: pointer; font-weight: 600; }}
.panel > p {{ padding: 0 10px 8px; margin: 0; white-space: pre-wrap; }}
.cols {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.cols > div {{ flex: 1 1 260px; min-width: 0; }}
.sample {{ background: #fff4ce; border: 1px solid #d9b400; border-radius: 6px;
           padding: 8px 10px; margin: 0 0 12px; font-weight: 600; }}
.foot {{ color: #6a737d; font-size: 12px; }}
.cap {{ font-size: 12px; color: #6a737d; margin: 0 0 6px; }}
@media (max-width: {mobile}px) {{
  .cols > div {{ flex: 1 1 100%; }}
}}
</style></head><body><div class="wrap">
"""

_HTML_TAIL = """</div></body></html>\n"""


def _esc(text) -> str:
    """Escape everything. The card content includes ids, paths and process
    names off a shared machine, and a preview is an HTML document that some
    browser will render — `litellm-shared-postgres` is fine, `<script>` is
    not, and the difference is not one this module is entitled to guess at."""
    return _html.escape(str(text if text is not None else ""), quote=True)


def _html_body(els: list[dict]) -> str:
    """The element list as HTML, riding the same four tags the JSON uses.

    Written as a translation of `_card`'s element list rather than as a second
    layout, because §6 requires the two renderings to come from one renderer:
    a preview built from the model's fields separately would be a preview of a
    card that does not exist.
    """
    out: list[str] = []
    for el in els:
        tag = el.get("tag")
        if tag == "markdown":
            content = el.get("content") or ""
            # The footnote is the card's chrome, not its content, so it is grey
            # in the preview. Compared against the constant rather than matched
            # by prefix: the prefix this used to test ("本卡只报告") was the
            # OLD footnote's opening, and a sentinel that stops matching fails
            # silently — the line simply stops being grey, which no test sees.
            css = "foot" if content == FOOTNOTE_READ_ONLY else ""
            out.append(f'<p class="{css}">{_esc(content)}</p>')
        elif tag == "hr":
            out.append("<hr>")
        elif tag == "collapsible_panel":
            title = ((el.get("header") or {}).get("title") or {}).get("content") or ""
            inner = "\n".join(f"<p>{_esc(e.get('content'))}</p>"
                              for e in el.get("elements") or [])
            out.append(f'<details class="panel"{" open" if el.get("expanded") else ""}>'
                       f'<summary>{_esc(title.replace("**", ""))}</summary>{inner}</details>')
        elif tag == "column_set":
            cells = "".join(f"<div>{_html_body(c.get('elements') or [])}</div>"
                            for c in el.get("columns") or [])
            out.append(f'<div class="cols">{cells}</div>')
    return "".join(out)


def html(model: dict, *, card: dict | None = None, caption: str = "") -> str:
    """A standalone document showing the card twice: 390px and desktop.

    Two frames from one element list, side by side on a wide screen and stacked
    on a narrow one — the narrow frame is where §6's acceptance lives, and it
    is worth looking at it in a browser that is itself wide, which is the only
    way to see both widths at once.
    """
    card = card or card_for(model)
    header = card["header"]
    template = header["template"]
    title = header["title"]["content"]
    body = _html_body((card.get("body") or {}).get("elements") or [])
    sub = header.get("subtitle", {}).get("content", "")
    out = [_HTML_HEAD.format(title=_esc(title), mobile=MOBILE_WIDTH)]
    if caption:
        out.append(f'<p class="cap">{_esc(caption)}</p>')
    for css, label, width in ((("frame narrow"), f"手机 {MOBILE_WIDTH}px", "narrow"),
                              (("frame"), "桌面", "wide")):
        out.append(f'<p class="cap">{_esc(label)}</p>')
        out.append(f'<div class="{css}"><div class="hdr {template}">'
                   f'<h2>{_esc(title)}</h2><div class="sub">{_esc(sub)}</div></div>'
                   f'<div class="body">{body}</div></div>')
    out.append(_HTML_TAIL)
    return "".join(out)


#: Which model kind renders to which card. Kept as data so a new card cannot
#: reach a person without appearing here — an unregistered kind raises instead
#: of falling back to some other card's layout, which is how a recovery card
#: would end up with an alert card's colour.
_RENDERERS = {
    "alert": alert_card,
    "recovery": recovery_card,
    "status": status_card,
}


def card_for(model: dict) -> dict:
    kind = str(model.get("card") or "")
    renderer = _RENDERERS.get(kind)
    if renderer is None:
        raise presentation.PresentationError(
            f"{kind!r} is not a card this renderer knows ({sorted(_RENDERERS)}); "
            "falling back would render it as whichever card happened to be "
            "first, with that card's colour")
    return renderer(model)


def preview(model: dict, *, caption: str = "") -> dict:
    """Both renderings of one model, plus the JSON they share a source with."""
    card = card_for(model)
    return {"model": model, "card": card, "html": html(model, card=card, caption=caption),
            "json": json.dumps(card, ensure_ascii=False, indent=2)}


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - hand check
    """Render one card from a digest document, for looking at."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=sorted(_RENDERERS))
    parser.add_argument("--digest", required=True,
                        help="a `serverosctl alert digest --json` document")
    parser.add_argument("--snapshot", help="a status snapshot (status card only)")
    parser.add_argument("--at")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--html-out")
    parser.add_argument("--json-out")
    args = parser.parse_args(argv)

    document = json.loads(Path(args.digest).read_text(encoding="utf-8"))
    at = args.at or document.get("at") or ""
    if args.kind == "status":
        snapshot = (json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
                    if args.snapshot else {})
        model = presentation.status_model(snapshot, at=at,
                                          binding=snapshot.get("gateway_binding"),
                                          sample=args.sample)
    elif args.kind == "alert":
        model = presentation.alert_model(document, at=at, sample=args.sample)
    else:
        model = presentation.recovery_model(document, at=at, sample=args.sample)

    rendered = preview(model)
    if args.html_out:
        Path(args.html_out).write_text(rendered["html"], encoding="utf-8")
    if args.json_out:
        Path(args.json_out).write_text(rendered["json"], encoding="utf-8")
    if not (args.html_out or args.json_out):
        print(rendered["json"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
