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
Deliberately tiny: `markdown`, `hr`, `collapsible_panel`. All three are in use
in the cards this node already sends, so they are known to be accepted by the
schema version in production. §6: "按当前 card schema/SDK 校验，不从草图发明
JSON 属性" — inventing a plausible tag is how a card renders as a blank
rectangle in a conversation nobody is watching.

`column_set` was the fourth and is gone from the status card. It held the
readings two abreast on the face, and the layout that replaced it puts every
reading down a single column inside its block's fold — with the block's face
reduced to one summary line, there is nothing left for a column set to be
abreast of. The primitive went with it rather than staying as an unused export:
a vocabulary member nothing writes is a member the next author will assume is
supported by something.

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
import re
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
#:
#: The mark is ⚪ and no longer ❔, and the reason is the same one that makes the
#: colour grey. A question mark asks something — it reads as «this machine wants
#: an answer from you», which is the instruction amber carries and this state
#: does not. A white circle states a fact about the reading: it came out blank.
#: Two of the three other marks are also circles' worth of colour (the green
#: tick and the amber warning are not), so the four now read as one family at a
#: glance down a chat, which ❔ never did — a question mark among three
#: pictograms is the one that looks like a different product's icon.
TEMPLATES = {
    "OK": ("green", "✅"),
    # Yellow, not orange (owner, 2026-10-30): the three levels a person sees
    # are green 正常 / yellow 注意 / red 报警, on every card this product sends.
    "ATTENTION": ("yellow", "⚠️"),
    "CRITICAL": ("red", "🚨"),
    "UNCONFIRMED": ("grey", "⚪"),
}
NEUTRAL_TEMPLATE = ("grey", "⚪")


def _template(verdict: str | None) -> tuple[str, str]:
    return TEMPLATES.get(str(verdict or ""), NEUTRAL_TEMPLATE)


def md(content: str) -> dict:
    return {"tag": "markdown", "content": content}


#: The four words a face uses for the four summary states — one vocabulary for
#: the status card, the incident card and the notice, so that a reader looking
#: at any two of them is reading one product.
#:
#: Spelled out rather than derived from `TEMPLATES`, because the colour, the
#: mark and the word are three answers to the same question and a map that
#: answered only one of them would leave the other two to be invented at a call
#: site — which is how the same state ends up «需处理» on one face and «有异常»
#: on another. A verdict this map has not been taught gets the neutral word,
#: never a green one: an unrecognised reading is not a good reading.
FACE_WORDS = {
    "OK": "正常",
    "ATTENTION": "建议查看",
    "CRITICAL": "需要处理",
    "UNCONFIRMED": "无法确认",
}
NEUTRAL_WORD = "无法确认"


def face_word(verdict: str | None) -> str:
    return FACE_WORDS.get(str(verdict or ""), NEUTRAL_WORD)


#: THE TWO AXES A STATE WORD AND A READING WERE SHARING ONE LABEL FOR.
#:
#: 「现在」 used to head a row that carried both answers at once —
#: `**现在** 无法确认 · 最后读数 09-18 14:15（2 天前）` — and the row next to it
#: carried a figure with a threshold it had crossed (`测量 92.0%（≥ 阈值
#: 90.0%）`). A reader had no way to tell whether the threshold was crossed NOW
#: or was crossed at the reading named two words earlier, and the two sentences
#: pulled in opposite directions: one said the condition holds, the other said
#: nobody can say whether it does.
#:
#: They are two facts and they get two names, so that neither can borrow the
#: other's weight. 最新观测 is what was read and when it was read; it is true
#: regardless of anything that happened after. 当前确认状态 is what may be
#: asserted about now, given those readings — and it is the one that goes quiet
#: when nobody has looked.
#:
#: ONE SPELLING, EVERYWHERE. `_integrity_now` is the block one card over and
#: carries the same word for the same answer, which is what its own docstring
#: asks for: a reader who has learned what this label means must not find it
#: meaning something else on the next block.
CONFIRM_LABEL = "当前确认状态"
OBSERVED_LABEL = "最新观测"


def panel(title: str, blocks: list[str], *, expanded: bool = False) -> dict:
    """The collapsible panel, matching the shape the live cards send.

    THE CARET IS DECLARED HERE AND NOT SPELLED IN `title`. `icon` below is the
    affordance, and a title that also began with `▸` made every fold carry two
    of them — one from the text, one from the widget — which the 2026-10-04
    previews drew as `▶▸` and Feishu draws as a bullet beside a chevron. The
    titles no longer carry one (`_face_prefix`), and `_fold_icon` draws this
    declaration in the preview.
    """
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


def _titled(title: str, model: dict) -> str:
    """The card's title, with the sample mark on it when there is one.

    ON THE TITLE AND NOWHERE ELSE, and the history is the reason this docstring
    is longer than the function. The mark started on the status card's title
    alone, which meant the alert card's sample was one crop away from being read
    as a real alert; it was then also given a line of its own at the top of the
    body, for all three kinds, on the argument that a body line survives being
    quoted.

    §2 answered that: the body line put a third copy of the same mark on a
    card whose first screen §2 caps at ten to twelve lines, and the copy it
    bought protected nothing — a card is forwarded with its coloured header,
    which is where the mark lives now. The preview's own source line (§6's
    「SAMPLE 标识与源数据») is rendered above the frames by
    `card_scenarios.rendered`, outside the card, and is unaffected by this.

    The flag still has to be reversible, which is what
    `test_every_scenario_still_says_a_sample_without_the_mark` is for.
    """
    if model.get("sample"):
        return f"{title} · {presentation.SAMPLE_MARK}"
    return title


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
    els: list[dict] = []
    integrity = model.get("integrity")
    watch = model.get("watch") or {}
    undecided = model.get("undecided") or {}
    recovery = model.get("recovery") or {}

    # §八-B. `integrity_finding` can now return a document with a COUNT OF ZERO
    # when the held rows it was given include codes nothing has decided about.
    # Such a document is not a finding — there is nothing to say "N 个主体失去
    # 观测" about — so it must not reach the face, the title or the level
    # computation. What it IS is a hole in the admission catalogue, and it
    # takes the same line as the event-row hole below, in the same words: an
    # undecided code reported one way for events and another way for held rows
    # would be two vocabularies for one fact.
    if integrity and not integrity.get("count"):
        held_undecided = integrity.get("undecided") or {}
        if held_undecided.get("count"):
            merged_codes = sorted(set(undecided.get("codes") or ())
                                  | set(held_undecided.get("codes") or ()))
            undecided = {"count": len(merged_codes), "codes": merged_codes}
        integrity = None

    # Colour follows ATTENTION, not severity. A card whose colour came from
    # severity would paint a node red for a WARNING nobody may act on, which is
    # the reading that made the old card look alarming on a quiet hour.
    template, icon = _template(_face_verdict(events, integrity))
    title = _titled(_face_title(events, integrity), model)

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

    # What did not fit, the counts of it, and the two lines of chrome that used
    # to stand on the face — §九 folds background into one panel rather than
    # deleting it, and the counts are why the fold can be closed.
    #
    # THE TITLE CARRIES THE NUMBERS, so nothing is hidden by folding: 「背景与
    # 历史 · 113 项历史积压」 is on the face whether or not anybody opens it, and
    # §5's 「静默不等于没事」 is answered by a reader who never taps anything.
    # This is `_timeline_title`'s rule, which counts a day's gaps in the header
    # of the panel that holds them, applied to the alert ledger.
    #
    # `_undecided_line` does NOT fold, and the difference is not its size. An
    # undecided code is a hole in the attention table: it says the contract
    # failed to level something, which is a statement about this system rather
    # than about the machine, and a reader who has to open a panel to find it is
    # a reader who will never find it. §八-B files it in its own bucket for the
    # same reason — it is not background.
    if undecided.get("count"):
        els.append(md(_undecided_line(undecided)))
    background = _background_blocks(model, watch, recovery)
    if background:
        els.append(panel(_background_title(model, watch, recovery), background,
                         expanded=False))
    return _card(template, title, els, model)


def _background_title(model: dict, watch: dict, recovery: dict) -> str:
    """「背景与历史」, plus the counts, because a closed fold may not hide one.

    The counts are `_summary_line`'s three and they are put here rather than on
    a line of their own above the fold. §2 caps the first screen at ten to
    twelve lines and names the reason: a reader who has to get past the card's
    account of itself has not been told anything. Three numbers inside a header
    that has to be there anyway cost no line of their own.
    """
    omitted = model.get("omitted") or {}
    counts = []
    if watch.get("count"):
        counts.append(f"{watch['count']} 项低优先级")
    if recovery.get("announced"):
        counts.append(f"{recovery['announced']} 项已恢复")
    if omitted.get("backlog"):
        counts.append(f"{omitted['backlog']} 项历史积压")
    return "背景与历史" + (" · " + " · ".join(counts) if counts else "")


def _background_blocks(model: dict, watch: dict, recovery: dict) -> list[str]:
    """The three things that were on the face and are not readings.

    One of them is a count and two are chrome, and they share a fold because
    they share a reader: somebody who has finished with the findings and wants
    to know what else is in the ledger, or on what evidence. A reader who wants
    neither now spends no line on either.

    `_summary_line` still builds the sentences — it is the same three counts and
    the reasoning in its docstring about which three is unchanged — it is the
    position that moved.
    """
    blocks = []
    other = _summary_line(model, watch, recovery)
    if other:
        # The counts are in the panel title; repeating them as a sentence under
        # a header that already listed them is the same number twice, which is
        # the defect §6's «去掉重复标题» names one level up.
        blocks.append("这些条目未进卡面，全部在 ServerOS 登记表里可查。")
    blocks.append(f"采集 {_stamp(model)} · 设备 {model.get('device_ref') or '未知'}")
    blocks.append(FOOTNOTE_READ_ONLY)
    return blocks


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


def _now_line(event: dict) -> str:
    """§4's second question — 「现在还存在吗」 — in one line, or nothing.

    A field and not a sentence, for the reason 状态 is: the register's answer
    is one of three states, and a narrative around it would be this renderer
    deciding how much a reader should worry. The four spellings below are the
    whole of it:

      * `STILL`       「仍在发生」 — the last grading found it above its floor.
      * `UNCONFIRMED` 「无法确认」 — open, and nobody has graded it since. The
                       reason is `hold_kind` and it is in the fold; what the
                       face owes is that this reading is not current.
      * `RECOVERED`   「已恢复」.
      * anything else 「状态未知」.

    「无法确认」 and 「仍在发生」 are the pair that must never be merged. Both
    are `state: OPEN` in the register and they call for opposite things from
    the reader: one is a machine still in the condition, the other is a
    monitoring system that stopped looking. On this node the second is not
    hypothetical — it is 1,091 rows.

    NO CLOCK ON THIS LINE, AND THAT IS THE REPAIR. The observation time used to
    ride here — `无法确认 · 最后读数 09-18 14:15（2 天前）` — and the row beneath
    it carried a figure against a threshold it had crossed
    (`**测量** 92.0%（≥ 阈值 90.0%）`). Two sentences, adjacent, pulling opposite
    ways, and nothing in either of them told a reader whether the threshold was
    crossed NOW or was crossed at a reading named two words earlier. The clock is
    a fact about the READING and this line is a fact about NOW; `_observed_line`
    carries the clock now, and 首次观测 still says when it STARTED, which is the
    third instant and a third question.

    THE LABEL IS `CONFIRM_LABEL` AND NOT 现在. 现在 named a row that held two
    answers at once, and the name went with the row: what a reader learned from
    it was «the clock lives here», which is no longer true. What this line
    answers is narrower and it says so — what may be asserted about now, given
    what was read. `_integrity_now` answers the same question on the incident
    card's own face and uses the same word, which is what its docstring asks
    for.
    """
    existence = event.get("existence") or {}
    state = str(existence.get("state") or "")
    if state == presentation.RECOVERED:
        return f"**{CONFIRM_LABEL}** 已恢复"
    if state == presentation.UNCONFIRMED:
        return f"**{CONFIRM_LABEL}** 无法确认"
    if state == presentation.STILL:
        return f"**{CONFIRM_LABEL}** 仍在发生"
    return f"**{CONFIRM_LABEL}** 状态未知"


def _observed_line(event: dict) -> str:
    """§4's reading under its own name: when this row was last measured.

    ITS OWN ROW AND NOT A CLAUSE OF `_now_line`, which is the repair this pair
    of functions exists for. The clock used to ride on the state row —
    `**现在** 无法确认 · 最后读数 09-18 14:15（2 天前）` — and the row directly
    beneath it carried a figure against a threshold it had crossed
    (`**测量** 92.0%（≥ 阈值 90.0%）`). Two sentences, adjacent, pulling opposite
    ways, and nothing in either told a reader whether the threshold was crossed
    NOW or was crossed at a reading named two words earlier. The clock is a fact
    about the READING; `_now_line` is a fact about NOW. They go stale
    separately, a reader acts on them separately, and so they get two names.

    THE AGE IS PRINTED BESIDE THE STAMP because the stamp alone still needs
    arithmetic from the reader, and the arithmetic is the whole point. Said in
    one unit (`presentation.age_says`) rather than exactly, because the pair
    measured 422.9px in a 360px row and « 0 小时» was a third of what the row had
    to give up; the absolute stamp beside it is what keeps that a rounding and
    not a loss.

    The age is suppressed at exactly zero and nowhere else. Zero is not a
    threshold this renderer chose — it is the reading having been taken as this
    sentence was being written, and `age_says(0)` spells that "刚刚", which is
    arithmetic nobody needs. Everywhere else it is printed, including for a
    reading a minute old, because the whole point of the row is that a card can
    carry a current reading and a stale one and they must not read alike.

    `未记录` AND NOT AN ABSENT ROW. A register entry with no `observed_at` has an
    answer on this axis and it is not «nothing to say»: the condition is real
    and nobody recorded when it was last read. Dropping the row would leave
    `无法确认` standing alone above the measurement, which reads as a monitoring
    problem with no shape — and the clause this row replaced said exactly this,
    as `未记录最后读数`, on the same branch of the same function.
    """
    existence = event.get("existence") or {}
    when = _local(existence.get("observed_at"))
    if not when:
        return f"**{OBSERVED_LABEL}** 未记录"
    seconds = existence.get("silent_seconds")
    age = "" if not seconds else f"（{presentation.age_says(seconds)}）"
    return f"**{OBSERVED_LABEL}** {when}{age}"


def _event_face(event: dict) -> str:
    """The first screen: the seven questions §4 fixes, one line each.

    Seven and not ten, and VISIBLE rather than folded. The field set §4 lists
    has more members than a person on a phone reads before the taxi arrives;
    the rest are in `_event_detail`, which is the same event one tap down.
    Nothing is removed — it is ordered.

    THE ORDER IS THE QUESTIONS', and it is the order §4 names them in: 发生了
    什么 (the title and 状态) / 现在还存在吗 (`_now_line`, labelled
    `当前确认状态`) / 最近一次读到什么 (`_observed_line`, labelled `最新观测`) /
    当前测量与阈值 (测量) / 影响 / 建议 / 从何时起·持续多久 (首次观测). The
    measurement sits between the state and the reading because it is the only one
    of the seven that is evidence: what is wrong is a sentence somebody wrote,
    what was read is the thing that made the sentence true, and whether it is
    still true is a fact about a reading. Its line is absent on a code that
    measures nothing — a declaration mismatch, a lifecycle event — because a
    field printed with no reading in it is a field a reader learns to skip, and
    it would sit exactly where the real figures need to be noticed.

    EIGHT LINES FOR SEVEN QUESTIONS, and the eighth is not a new question. §4's
    second question used to be answered by one row carrying two answers —
    `**现在** 无法确认 · 最后读数 09-18 14:15（2 天前）` — directly above the 测量
    row and its crossed threshold, and the two said opposite things without
    either of them saying which fact the threshold belonged to. The reading and
    the state are two rows under two names now, which is why `_now_line` prints
    no clock and `_observed_line` exists. Measured with
    `presentation.display_cells`: `**当前确认状态** 无法确认` is 25 cells and
    `**最新观测** 09-18 14:15（2 天前）` is 34, so each fits the 46-cell body
    budget on its own; the one-row form this split replaces —
    `**当前确认状态** 无法确认 · **最新观测** 09-18 14:15（2 天前）` — is 62 and
    wraps.

    Line one is `_event_title`'s, because the subject is what makes the other
    six actionable. On a container finding it is the name the registration gave
    the service, and the twelve hex characters a person cannot use are in the
    detail with the ref.

    The label on line two is 状态 and not 发生了什么. 发生了什么 asks a question
    and invites a sentence in reply; 状态 is a field, and a console's field
    holds a state ("会话已结束，关联进程仍在运行") rather than a narrative. The
    two said the same thing and only one of them read as a console.
    CARDS-STYLE.md R1.

    The duration rides on the 首次观测 line instead of taking one of its own.
    It is the same fact — how long this has been true — and a labelled line of
    its own on a 390px screen costs more than it carries.
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
        _now_line(event),
        _observed_line(event),
    ]
    # The number and the tier it crossed, directly under the reading it came
    # from and above the cost. §4 always required the measurement; it required
    # it inside the fold, and the fold is where a reader who has not yet decided
    # to care will not go. A finding whose whole content is «this reading passed
    # this threshold» is the one case where the face without the figure answers
    # nothing — a reader told only «建议查看» and «影响：磁盘可能写满» cannot tell
    # whether to get up now or at the next hour.
    #
    # It is a LABELLED line and not a clause of 状态, because the two are
    # different kinds: 状态 is what the machine is doing, 测量 is what was read
    # and against what, and a card that folded the second into the first would
    # have no field left to name the threshold.
    measurement = event.get("measurement") or {}
    face = _measurement_row(measurement)
    if face:
        lines.append(f"**测量** {face}")
    lines += [
        f"**影响** {_impact_text(impact['text'])}",
        f"**建议** {event['action']['text']}",
        f"**首次观测** {first or '未知'}"
        + (f" · 持续 {times['duration_text']}" if times["duration_text"] else ""),
    ]
    named = _owner_line(event["subject"].get("owner"))
    if named:
        lines.append(named)
    return "\n".join(lines)


def _measurement_row(measurement: dict) -> str:
    """The measurement line, in the longest form this row can hold.

    WHY THE ROW IS MEASURED WITH ITS LABEL
    --------------------------------------
    `**测量** ` is on the same line as the reading and spends the same width, so
    a budget on the reading alone is a budget on a line nobody prints — the same
    mistake `_block_readings` documents for the block headings, in the one other
    place a label shares a line with a value. Measured in the browser at 390px,
    `测量 声明 running · 实测 NOT_RUNNING（生产者说 exited）` is 419.5px of the
    360px box: it breaks, and `**测量**` is left alone at the end of the first
    line, which is a value with no name. The same sentence at 356.1px, with
    「生产者说」 dropped, is one line.

    WHY LONGEST-FIRST AND NOT SHORTEST-SUFFICIENT
    ---------------------------------------------
    The tiers are the producer's, and they are ordered by how much they say:
    the full form names who said the raw word, the middle form keeps the word
    and drops who said it, the last form is the comparison alone. Trying
    shortest-first would give up the provider's word on every card that had room
    for it, and `restarting` against `dead` is exactly the difference a reader
    of a stopped service needs.

    The last tier losing the provider's word is a RELOCATION and not a
    deletion: the evidence block under 技术诊断 prints the same part's `says`,
    which carries it in every tier. `alerting.measurement_parts` builds both.

    Falls back to `face` when nothing fits. A row that breaks is worse than a
    row that is short, but a measurement silently shortened past what the
    renderer was given would be a card asserting something the register did not.
    """
    tiers = tuple(measurement.get("faces") or ())
    if not tiers:
        tiers = (measurement.get("face") or "",)
    width = presentation.display_width("measurement_width")
    for text in tiers:
        if text and presentation.fits(f"测量 {text}", width):
            return text
    return measurement.get("face") or ""


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


def _integrity_word(code: str) -> str:
    """The word a reader sees for `code`, and never the code itself.

    §2 keeps the machine's own vocabulary off the first screen, and the codes
    behind a monitoring-integrity finding are exactly that vocabulary: a
    heading reading «监控完整性 · DESIRED_ACTUAL_MISMATCH ×12 · TASK_IDLE_PROCESS_ACTIVE»
    names the fault in a spelling the reader has to translate. `definition_for`
    is `card-definitions.yaml`'s own door and the catalogue is the only
    authority for what a code means, so this asks it and does not build a
    second table beside it.

    A code the catalogue has not defined is still rendered. It is the thing
    that fired, and `definition_for`'s answer for that case is to carry the
    code verbatim while saying the catalogue has a hole; a heading has no room
    for the sentence, so the hole is the word — two undefined codes fold into
    one «未登记的 code» entry and their count is what says how big the hole is.
    Rendered as nothing instead, the heading would silently drop the codes it
    is made of, which is the one failure a heading about missing observation
    must not commit.
    """
    label = str(presentation.definition_for(code).get("label") or "").strip()
    return label or "未登记的 code"


def _integrity_kinds(finding: dict) -> str:
    """The kinds of declaration that lost observation, in the catalogue's words.

    THE LABEL AND NEVER THE CODE. This is a list of the codes one held row at a
    time, and §4 sends the machine's classification — `code` first among the
    nine it names — into 技术详情. So the list is spelled the way a reader can
    read it and it is printed one panel down; what the face keeps is the name
    of the section (§4's Layer 2) and the sentence about the condition.
    Measured at 390px, this list on the face was 446.9px of a 360px row: it
    broke, and the break put a `·` at the start of a line where it read as one
    more item. It is not shorter in the fold; it is in the place §4 named, and
    it is complete there.

    Counted by the WORD and not by the code, because the word is the part that
    is read. `未声明归属` is the declared label of two different codes
    (`RUNTIME_UNOWNED` and `TUNNEL_OWNER_MISSING`); a line that printed the
    word twice under two counts would ask the reader to hold apart two codes it
    has just told them they cannot see. Summed, the word says the thing this
    line is for — how many subjects stand behind it.

    `×1` is dropped rather than printed. Every entry carries a count and most
    carry one, and three `×1`s cost a line of a 390px screen to say nothing; a
    number appears exactly where it is the interesting part, which is a word
    standing behind more than one subject.
    """
    counts = finding.get("by_code") or {}
    by_word: dict[str, int] = {}
    for code, n in counts.items():
        word = _integrity_word(str(code))
        by_word[word] = by_word.get(word, 0) + n
    ordered = sorted(by_word.items(), key=lambda pair: (-pair[1], pair[0]))
    shown = " · ".join(f"{word} ×{n}" if n > 1 else word
                       for word, n in ordered[:INTEGRITY_CODES_SHOWN])
    rest = len(ordered) - INTEGRITY_CODES_SHOWN
    if rest > 0:
        shown += f" · 等 {rest} 类"
    return shown


def _integrity_face(finding: dict) -> str:
    """The same labelled lines an event gets, for the same reason.

    The first version of this left the finding inside a collapsed panel, which
    on a 390px screen meant the card's own headline — "3 个主体失去观测" — was
    visible and every word explaining it was not. A person who reads only the
    header of that card learns that something is wrong and not what, which is
    the failure mode this round exists to remove.

    THE FIRST LINE IS THE SECTION, NOT THE ENUMERATION. It was the code list
    until this round; §4 folds that, and what a face owes a reader first is
    which block of the card they are looking at — an event block opens with
    `对象 · 一句话` and this one opens with the name of the thing that is
    wrong. The header above already carries the level and the count, so the
    heading repeating either would spend the most valuable line of a 390px card
    on what the line above it just said; the first draft of this face did
    exactly that and the render put the same number on the card three times in
    four lines.

    SIX LINES, AND THE TWO THIS FACE GAINED ARE §4'S SECOND AND SEVENTH
    QUESTIONS. It answered 发生了什么 / 影响 / 建议 and stopped — so on a block
    whose whole content is «nobody looked», the reader could not tell whether
    anyone had looked since, and could not tell whether this started an hour or
    a fortnight ago. Those are the two questions this block is worst at
    answering from the surrounding card: the events beside it carry their own
    现在 and 首次观测 lines, and a block that skipped both would read as the one
    finding there is no clock on.

    The order is §5's Layer 3 and not the order they were added in: 当前, then
    影响 and 建议, then the clock. 现在 sits directly under 状态 because the two
    are one sentence read twice — what was lost, and whether it is still lost —
    and a reader who has both does not need the panel.
    """
    current = finding.get("current") or {}
    lines = [
        "**监控完整性**",
        f"**状态** {finding['text']}",
        _integrity_now(current),
        f"**影响** {finding['impact']}",
        f"**建议** {finding['action']}",
    ]
    first = _integrity_first_seen(current)
    if first:
        lines.append(first)
    return "\n".join(lines)


def _integrity_now(current: dict) -> str:
    """§4's 「现在还存在吗」 for a set of subjects nobody is measuring.

    UNCONFIRMED and never STILL. Every row behind this finding is OPEN in the
    register and held because no grading pass reached it, so what is true is
    that nothing looked — 「仍在发生» would say the machine is still in some
    condition, and that is the one thing this finding exists to say we cannot
    know. A block that printed 仍在发生 here would be the monitoring system
    answering a question it just said it could not answer.

    ONE WORD AND NO CLOCK, which is the shape §5 asks for and the shape this
    line had to be argued back into. It first carried the start as well —
    `无法确认 · 自 09-20 06:10（8 小时前）` — which answered §4's second and
    seventh questions in one row and was still wrong, because the incident
    block one card over answers them with a state word and `**首次观测**` and a
    reader who has learned what those labels mean would find the clock moved
    inside the first one on this block alone.

    THE STATE WORD IS `_now_line`'S WORD, TO THE LETTER. The incident block
    renamed that row to `当前确认状态` so it could stop carrying a clock, and
    `_observed_line` carries the clock there now. This block carries no clock
    either and for a stronger reason, so it takes the same label — a reader who
    has learned that `当前确认状态` answers «what may be asserted about now, given
    what was read» must not find it answering something narrower one card over.

    NO `最新观测` ROW HERE, and the omission IS the finding. The incident block
    can date its reading; these rows were never read at all, so the observation
    axis would print `未记录` on every card this face ever renders. A field whose
    value is constant is not a reading — §2 is what happens to it: the reader
    learns to skip the row, and skips the state word with it.
    """
    return f"**{CONFIRM_LABEL}** 无法确认"


def _integrity_first_seen(current: dict) -> str:
    """§4's 「从何时起·持续多久」, in the incident card's own spelling.

    The same label, the same two clauses, the same order: an absolute local
    instant, then the duration in the one unit `human_duration` picks. The
    instant is the EARLIEST `first_seen` among the rows this finding counts and
    not the latest, because the question is «since when» and a maximum would
    date the newest subject — a loss that began a fortnight ago would read as
    one that began this morning.

    Empty rather than a row of 未知 when no row dated itself: a labelled line
    holding nothing but a placeholder is the field a reader learns to skip, and
    the 现在 line above has already told them the reading is not current.
    """
    when = _local(current.get("since"))
    if not when:
        return ""
    seconds = current.get("since_seconds")
    if not seconds:
        return f"**首次观测** {when}"
    return f"**首次观测** {when} · 持续 {presentation.human_duration(seconds)}"


def _integrity_detail(finding: dict) -> list[str]:
    """One finding, however many subjects it is made of.

    §5's aggregation rule: 500 stale objects are one line, not 500 faults. What
    the card states is how many declared subjects lost observation and which
    kinds of declaration are therefore unverified — the enumeration belongs in
    `serverosctl alert list`, where a person who wants it can get it.

    THE KINDS COME FIRST AND IN TWO SPELLINGS, which is what a machine layer is
    for. The first line is the catalogue's words — the same list the face used
    to carry, so nothing left the card, it moved one tap down. The second is
    the codes themselves, which is what an operator greps for and what
    `serverosctl alert list` will accept. §4's nine — code, 事件类型, 严重度,
    注意力类别, 新鲜度, 来源, 引用, 回执, 权威 — are all here, and the codes
    are not on the face.

    `采集侧` is named here for the reason `collector` exists in the contract:
    the action on the face tells a person to check the collection side, and a
    sentence that points at an unnamed thing is a sentence nobody can act on.
    """
    by_code = finding.get("by_code") or {}
    kinds = _integrity_kinds(finding)
    blocks = []
    if kinds:
        blocks.append(f"**不成立的声明** {kinds}")
    blocks += [
        finding["text"],
        "**按异常代码** " + " · ".join(f"{code} ×{n}"
                                       for code, n in sorted(by_code.items())),
    ]
    collector = str(finding.get("collector") or "").strip()
    if collector:
        blocks.append(f"**采集侧** {collector}")
    blocks += [
        "**这意味着** 本卡无法判断这些主体的当前状态。观测恢复前"
        "「未再观测到」不等于「已恢复正常」，恢复后本条自行消失。",
        f"**不作为故障计数** {finding['not_a_finding']['note']}"
        f"（{finding['not_a_finding']['count']} 项）",
        f"**注意力** {(finding.get('attention') or {}).get('because') or ''}"
        .rstrip(),
    ]
    return blocks


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
    els: list[dict] = []
    template, icon = _template("OK" if items else "UNCONFIRMED")
    title = _titled(f"{icon} 已恢复 · {counts.get('announced') or 0} 项"
                    if items else f"{icon} 本批没有已告知且已恢复的问题", model)

    # §10's five lines per recovery, and the order is the design's: what
    # recovered / when / how long it was wrong / what it was / what it is now.
    # The subject leads because it is the same subject the incident card led
    # with, so a person who saw the ⚠️ recognises the ✅ rather than having to
    # match two codes.
    #
    # THE FIFTH LINE IS THE CURRENT STATE AND NOT THE START TIME. It read
    # `**出现于** 09-19 10:17` — the instant the register first saw the row —
    # which answers §4's «从何时起» and not its «现在还存在吗»; §10's recovery
    # shape closes on `现在 ✅ Ready` and leaves the start off entirely. The row
    # is labelled `当前确认状态` and not `现在`: it is §4's «现在还存在吗» and it is
    # the same question the incident and integrity blocks answer, so it takes the
    # same word — one question, one label, wherever it is printed. Nothing
    # is lost: the start is `恢复` minus `本次持续`, the two lines above it, and
    # the reader who wants it subtracts. What was lost by printing the start
    # instead is the one question a person asks a card that says ✅ — is it
    # still clear — which is the whole reason §5 asks both cards to answer it.
    #
    # The word is `face_word` and not the literal `Ready` §10's example uses.
    # `Ready` is the gateway's own word for its own probe, and §10's example is
    # a gateway; this card is every code, and the sentence over it has to be
    # true of «进程组尚未归属到任何受管工作» as well. The shared vocabulary
    # already has the word for it, and §5's whole point is that the two cards
    # do not each invent one. The mark stays on the block's first line alone:
    # a second ✅ inside five lines is the same fact stated twice, and §6 asks
    # a normal card to be light.
    #
    # THREE THINGS THIS FACE NO LONGER SAYS, and each was a defect rather than a
    # saving. `（state RESOLVED）` put the register's own spelling of a state in
    # parentheses after a sentence that had already said it — §2 names raw
    # fields as what a reader scrolls past. `**恢复依据**` repeated, word for
    # word, the definition of what a recovery is on every item; it is a fact
    # about the card's construction and not about the thing that recovered, so
    # it is stated once at the foot. And the two timestamps were the ISO
    # instants the register writes, which put `2026-09-19T02:15:41+00:00` on a
    # card read on a phone.
    for item in items:
        what = item["what"]
        els.append(md(
            f"✅ **{item['subject']['text']} · 已恢复**\n"
            f"**恢复** {_local(item['times']['resolved_at']) or '未记录'}\n"
            f"**本次持续** {item['times']['open_for_text']}\n"
            f"**恢复前** {what['what'] or what['code']}\n"
            f"**{CONFIRM_LABEL}** {face_word('OK')}"))

    omitted = model.get("omitted") or {}
    # `announced_beyond_limit` is a capacity note and keeps a line of its own when
    # there is one; it is not the same kind of fact as the two clauses below.
    if omitted.get("announced_beyond_limit"):
        els.append(md(f"另有 {omitted['announced_beyond_limit']} 项已告知的恢复未进卡面"))
    # §5's rule rides on the closing line rather than on one of its own. It has
    # to be somewhere — 「观测失联或权限丢失不是恢复」 is the misreading a list of
    # ✅ invites, and a reader who has not read OBSERVABILITY-CARDS.md has no
    # other way to know that a ✅ here means positive evidence rather than «we
    # stopped hearing about it». It does not get a line because a line spent
    # explaining the card is the 自我解释 §2 names.
    #
    # `never_announced` JOINS IT, and the two are one fact read twice: a
    # resolution of an alert the register never delivered is the case that
    # looks like a recovery and is not. Stated together, the line answers the
    # only question a reader asks a list of ✅ — what counts here — and that is
    # why the count rides on this clause instead of taking a row beside it. The
    # sentence that once explained it in full ("人不能被通知一个他从来没收到过
    # 的坏消息") was the card arguing its own case; the count carries it.
    #
    # `本批恢复 N 项 · 已告知 M 项` USED TO OPEN THIS LINE AND IS GONE. `已告知
    # M 项` was the header's own «已恢复 · M 项» printed a second time eight
    # lines below itself — §2's 一个概念只出现一次 — and the batch total is
    # M plus the count in the clause that replaced it, so the one reader who
    # wants it subtracts rather than scrolling. That is the same argument this
    # card's docstring already makes for the counts it does not print, and the
    # figures are in `serverosctl alert digest` either way.
    # The collection stamp that used to close this line is gone too, and it is
    # the one thing on the recovery card §10 asks for and does not need: the
    # card's own subtitle already dates it («09-20 14:20 更新») and
    # `_engineering` carries 采集时间 with the reading it belongs to. Four facts
    # on one row measured 486.2px in a 360px row; the pair that replaced them
    # measures 318.4px and neither of them is the stamp.
    closed = "均为肯定证据关闭"
    if omitted.get("never_announced"):
        closed += f" · {omitted['never_announced']} 项从未告知，不计入恢复"
    els.append(md(closed))
    els.extend(_footnote(model))
    return _card(template, title, els, model)


# ---------------------------------------------------------------------------
# The status card. §2 + §3.
# ---------------------------------------------------------------------------

#: The three blocks, short enough to fit one line on a 390px screen — which is
#: why they lost their qualifiers. «我的资源 · 当前账户» and «整机资源» and
#: «共享 AI 网关 · 与个人 container0 分开» were sentences doing a heading's job,
#: and the two clauses they carried («当前账户», «与 container0 分开») are already
#: answered by the block's own first cell (it names the uid) and by the gateway
#: block's coverage note. A heading that argues is a heading nobody finishes.
#:
#: ONE dict and not a title plus an icon: the heading above the fold and the
#: panel's own label are these same strings, so they cannot come out as two
#: different names for one section.
_SECTION_TITLES = {
    "mine": "👤 我的资源",
    "host": "🖥 整机",
    "gateway": "🛰 AI Gateway",
}

#: The same three blocks with no icon, for a line that is read as a sentence.
#: §M4's 「去scope英文key»: the account line used to say «观测范围 host、mine»,
#: which are the model's keys and not words this product uses anywhere a person
#: reads. An icon mid-sentence reads as a bullet, so the names are here without
#: one — and they are the SAME names, so the account and the headings above it
#: cannot drift into two vocabularies for one block.
_SCOPE_NAMES = {
    "mine": "我的资源",
    "host": "整机",
    "gateway": "AI Gateway",
}

#: How many cells stand on the card's face before the rest fold away.
#:
#: THERE IS NO LONGER SUCH A NUMBER, and the constant is gone rather than set to
#: a value nobody reads. The face used to carry six cells — three rows of two —
#: and the surplus folded; the re-layout behind §1/§2's ten-to-twelve-line budget
#: moved EVERY reading into its block's fold and left one summary line on the
#: face. A bound that no longer bounds anything is the kind of dead code that
#: renders as a shorter card instead of as an error, so it does not stay.

#: How a section with no cells and no lines opens, in one word.
#:
#: Almost always "未读到" — the reading did not happen. The gateway block is the
#: exception, and §M4 gives it three states with three different repairs, so it
#: gets a word for each rather than one word covering three situations:
#:
#:   * `未配置监控绑定` — no service binding names this node's shared gateway.
#:     An ESTABLISHED ABSENCE, arrived at by looking. There is nothing to go and
#:     read; what is missing is a declaration, and telling a reader to fetch a
#:     reading that already happened sends them to repair the wrong thing.
#:   * `无法确认` — a binding named a gateway and nobody could read it. That one
#:     IS a missing reading, and it is the only one of the three where "go and
#:     look again" is the right advice.
#:   * `Ready` and friends — the gateway's own probe said something, and it is
#:     printed as itself.
#:
#: This block used to be headed "未读到" over a body that said "这一栏报为未绑定",
#: so the face and the sentence under it named two different problems and only
#: one of them existed.
_EMPTY_LEAD_UNBOUND = "未配置监控绑定"
_EMPTY_LEAD_UNREADABLE = "无法确认"
_EMPTY_LEAD = "未读到"


#: What a collection gap is CALLED on the face. The snapshot names its reads
#: `summary` / `top` / `user_services` / `runtime_list` — the keys of
#: `card_source.DEFAULT_READS` — plus the two the snapshot adds itself, and the
#: face used to print those keys with the command that failed and its exit code
#: behind them: «summary（resource summary）未读到：exit 2». All three parts are
#: §2's forbidden spelling on a first screen (原始 schema, 采集口径), and none of
#: them answers the question a reader has, which is WHICH reading did not arrive.
#: 「进程占用排行」 says that; `top` does not.
#:
#: A read this map has not been taught is printed as itself rather than dropped:
#: an unmapped read is still the reason the number is missing, and a blank would
#: read as «nothing is missing». `tests/test_cards.py` asserts every read
#: `card_source` can report has a name here, so this fallback covers a read added
#: in one file and not the other — not a case anybody should meet.
#: The reads the producer can report as missing, each in the words a person
#: knows it by. A name this table does not carry is not dropped — `_gap_line`
#: falls back to the name itself — so the table and `card_source`'s gap sources
#: have to cover each other both ways, and
#: `test_every_read_the_producer_can_name_has_a_word_on_the_face` is what holds
#: that: it collects the names from the producer and compares the two sets.
GAP_READS = {
    "summary": "本账户资源摘要",
    "top": "进程占用排行",
    "user_services": "用户单元",
    "runtime_list": "运行时清单",
    "business_calendar": "工作日历",
    "card_day_store": "当天卡",
}


def _gap_line(health: dict, gaps: list) -> str:
    """The face's line for a collection gap: which readings, and how many.

    WHY THIS IS NOT THE SENTENCE IN `gaps`. Those sentences are the account of
    how the reading was taken — the command, the exit status — and `_engineering`
    carries them one fold down. A gap is a finding and not an engineering note,
    which is why the face keeps a line for it; but a finding on the first screen
    is «本账户资源摘要 未读到», and the command belongs with the account of the
    reading, one tap away.

    THE COUNT IS DROPPED AT ONE, because a list of one name is its own count and
    «（1 项）» beside it is the same fact twice — the defect this card keeps
    having to have removed from it.

    A gap list shorter than the names, or longer, is a producer that wrote one
    without the other — and the answer is NOT to print the sentences here as a
    fallback: those carry the command and the exit status, and a fallback that
    puts §2's forbidden spelling on the first screen is not a safe default. It
    says how many readings did not arrive and stops.
    """
    named = list(health.get("gap_reads") or [])
    if len(named) != len(gaps):
        return f"**采集缺口** {len(gaps)} 项读数未到"
    names = "、".join(GAP_READS.get(str(n), str(n)) for n in named)
    # THE COUNT IS DROPPED WHENEVER THE NAMES ARE SHOWN, and both halves of that
    # are measured. A list of names is its own count — «（1 项）» beside one name
    # is the same fact twice, which is the defect this card keeps having to have
    # removed from it, and «（2 项）» beside two is the same defect one name
    # later: measured at 390px, `采集缺口 本账户资源摘要、进程占用排行 未读到`
    # is 321.7px and one line, and the same sentence with «（2 项）» on the end is
    # 378.4px of a 360px row and wraps. Dropping it is what lets the second name
    # onto the face at all.
    #
    # AND THE NAMES ARE THE FINDING UNTIL THERE ARE TOO MANY OF THEM. Three names
    # is 396.7px and wraps, and what wraps is a list of inventory sections —
    # §2's 「原始 schema» arriving on the first screen one human label at a time.
    # Past that point the count IS the finding and the names are the account of
    # the reading, which is what the engineering fold is for: it already carries
    # each one beside the command and the exit status that produced it. So the
    # line says how many and says where the names are, rather than wrapping to
    # name them all.
    width = presentation.display_width("problems_width")
    if presentation.fits(f"采集缺口 {names} 未读到", width):
        return f"**采集缺口** {names} 未读到"
    return f"**采集缺口** {len(named)} 项读数未读到（名称见技术诊断）"


#: The glyph each attention level wears, and the only place a level becomes an
#: icon. The words come from the contract (`presentation.problems_block`) and
#: the glyphs are the card's, so the mapping lives here beside `_TEMPLATES`,
#: which is where this card already decides what a verdict looks like.
#:
#: KEYED ON `presentation.AXIS` AND NOT ON ITS SPELLINGS. The axis is the
#: contract's, ordered worst first — the same order `alert_digest.current`
#: sorts by and `cards._face_levels` walks — so the first two members are «the
#: level a person may be interrupted for» and «the level below it», and this
#: file never has to hold a second copy of the contract's vocabulary. A level
#: the table has no glyph for renders without one rather than with a question
#: mark: an icon asserts how bad something is, and inventing one for a spelling
#: this renderer does not know is the same failure as inventing a colour for an
#: unknown verdict.
_PROBLEM_ICONS = {presentation.AXIS[0]: "🚨", presentation.AXIS[1]: "⚠️"}


def _problems_blocks(problems: dict) -> list[dict]:
    """§3.4's 当前问题 as a block: one bold line, then one line per problem.

    THE HEADING IS THE ANSWER. «当前问题 · 1 项需要处理» is one line where the
    other blocks spend two (a heading, then a sentence), and it is one line
    because this block has no second sentence to spare on a 390px screen: what
    follows it is the list, and a heading that only named the block would push
    the list one line further down for a reader who has already read the
    heading's own label. §6's «去掉重复标题» is the same rule.

    THE LIST LINES ARRIVE FORMATTED, and the renderer does not know how to read
    a register row — `presentation.problems_block` does, and it is the module
    that owns the words for what a code means and what a subject is called. A
    second reader here would be a second answer about which codes are current
    problems, and it would be the one nobody updates.

    THE OMISSION LINES ARE PART OF THE BLOCK and not of the fold. Each one is a
    statement about the count above it — how many were not listed, how many
    codes nobody has graded, how many a person has already answered — and a
    reader who saw «1 项需要处理» without them would have the number without
    the scale. They are the reason `alert_digest.current` counts what it does
    not show.

    AND THEY ARE ONLY THE ONES ABOUT THE PROBLEMS. `presentation.problems_block`
    hands back a second list, `below`, holding what is true of the register and
    not about the machine's problems — the count of findings the contract has
    already graded as not problems. That one goes to the fold (`_tech_panel`),
    because a card answering «现在有问题吗» may not re-open a question the
    contract closed, and because this machine holds 1,360 of them: the number
    would sit under «没有需要处理的问题» and teach a reader not to believe it.
    The split is the presenter's; this function prints `also` and does not
    re-sort either list.
    """
    if not problems:
        # No model, no block. `status_model` always builds one, so this is the
        # shape a fixture written against an older model takes — and printing
        # nothing is right there, because the alternative is inventing a
        # «无法确认» about a card that never asked.
        return []
    icon = _PROBLEM_ICONS.get(str(problems.get("level") or ""), "")
    headline = f"{icon} 当前问题 · {problems.get('headline') or ''}".strip()
    els = [md(f"**{headline}**")]
    if problems.get("state") != "READ":
        # The why, said once. It is the reading's own sentence for why there is
        # no reading — a daemon that did not answer, a register that would not
        # parse — and it stays as prose rather than becoming a count, because
        # there is no count to give.
        els.append(md(str(problems.get("why") or "没有读到告警登记表")))
        return els
    els.extend(md(line) for line in problems.get("lines") or [])
    els.extend(md(line) for line in problems.get("also") or [])
    # The age, the sibling count and the full subject name, for the problems
    # that had one to hold back. It is a fold rather than three more face lines
    # for §2's reason and not for tidiness: the face is `object · finding ·
    # state` and nothing else, and every one of these facts is *about* a line
    # that is already there. `presentation` decides which problems have one; a
    # renderer deciding that would be a second answer about what the face kept.
    extra = [str(line) for line in problems.get("details") or [] if str(line).strip()]
    if extra:
        els.append(panel("逐条明细", extra, expanded=False))
    return els


def _block_fold(section: dict) -> list[str]:
    """Everything the block measured, one reading per line, for its fold.

    ONE READING PER LINE AND NOT A TABLE. The readings used to be laid out two
    abreast as `column_set` cells on the FACE, and the fold held only the
    surplus; now they are all here and they are read down a column. That removes
    the wrap the two-column layout could not avoid at 390px — «67.3 GiB /
    247.0 GiB» came out as «67.3 GiB / 247.0» over «GiB», a unit broken off its
    number — and it is the shape a reader on a phone reads in one scan.

    The label and the value are copies of what `card_source.metric` produced, in
    the order the section function emitted them, which is the order it chose to
    be read in. `note` follows on the same line: it is the unit, the threshold
    or the denominator that makes the number checkable, and it is the reason the
    face's summary can be as short as it is.

    The prose lines come after the readings and in full — including the first,
    which the face used to show. A sentence shown twice on one card is the
    duplication §6's «去掉重复标题» names, and the face has the summary now.
    """
    inside: list[str] = []
    for cell in section.get("metrics") or []:
        if not isinstance(cell, dict):
            continue
        label = str(cell.get("label") or "").strip()
        value = str(cell.get("value") or "").strip()
        note = str(cell.get("note") or "").strip()
        if not (label or value):
            continue
        # An absent value is spelled, not left blank: a cell whose reading did
        # not arrive must not read as a cell whose reading is zero.
        line = "　".join(part for part in (label, value or "未读到") if part)
        inside.append(f"{line}　{note}" if note else line)
    inside += [str(line) for line in (section.get("lines") or [])]
    # §R15's loopback, carried by name and not by position. It is LAST in the
    # fold because it is where the gateway IS and not how it is, and a locator
    # that opened the panel would read as the reading the panel is one tap away
    # from.
    if section.get("entry"):
        inside.append(str(section["entry"]))
    if not inside and section.get("why"):
        # The reasoning behind a block with nothing to show. It says WHY there is
        # no reading, which is the one question a one-word face cannot answer and
        # a reader will otherwise answer by going to look for a reading that does
        # not exist.
        inside.append(str(section["why"]))
    return inside


def _section_language() -> dict:
    """The declared face line for a block: which readings, and which words.

    Same shape as `_gateway_language` above and for the same reason — the
    sentence a person reads is declared in `card-definitions.yaml`, next to the
    other declared sentences, and not generated in whichever module happened to
    hold the number.
    """
    return presentation._catalogue().get("sections") or {}


def _face_prefix(name: str) -> str:
    """The heading a block's face line starts with, icon and all.

    One function so that the width the heading spends and the string the card
    prints are computed from the same characters. Two spellings of `🖥 整机　`
    would agree until one of them was edited.

    NO CARET HERE. This string is the header of a fold when the block has
    something to open (`panel`) and a plain bold line when it does not
    (`md(f"**{label}**")`), and it used to begin with `▸` in both cases. Two
    consequences, both measured on the 2026-10-04 390px screenshots: the fold's
    header carried a caret character AND the `collapsible_panel` it is put into
    declares one (`panel()`'s `down-small-ccm_outlined` icon), so the
    affordance was drawn twice; and because the same `▸` was on the blocks that
    do NOT open, the marker that was supposed to say «there is more behind
    this» said it about everything, which is the same as saying nothing.

    The caret now comes from the widget, which is the only place that knows
    whether there is anything behind it, and its absence is information: a
    heading without one is a heading with nothing under it.
    """
    return f"{_SECTION_TITLES.get(name, name)}　"


#: One implementation, two modules: `presentation` owns the rule because it is
#: the module that composes the lines, and this is the name this module has
#: always called it by. A second copy here would be a second answer about how
#: wide a line is, and the two would agree until one of them was edited.
_cells = presentation.display_cells


def _block_readings(name: str, section: dict, used: int = 0) -> str:
    """The declared readings of one block, joined, inside the width budget.

    THE LABEL IS MATCHED, THE VALUE IS COPIED
    -----------------------------------------
    A declared label that no cell carries is SKIPPED, not invented and not
    defaulted: a block whose producer stopped emitting `配额` renders one reading
    short rather than a line reading `配额 未读到` about a reading nobody tried to
    take. The value is copied character for character out of the cell, so the
    number on the face and the number in the fold are the same string and cannot
    drift — reformatting here (dropping a decimal, converting a unit) is how a
    card comes to print a figure nobody measured.

    AND IT STOPS AT THE BUDGET
    --------------------------
    A summary that wraps costs two lines of a ten-line first screen, and where
    the wrap lands is the font's decision, not this one — measured at 390px the
    header's text box is 358px and one line is 23.25px, so a line that overruns
    breaks wherever it runs out. So the joining stops before the budget is
    exceeded, and what did not fit is only in the fold — one tap away on the same
    line, holding every reading with its unit and its threshold. Nothing is
    hidden; the face simply says less than the fold.

    `used` is the width the caller has already spent on the heading that shares
    this line. It is a parameter rather than something counted here because the
    heading is chosen elsewhere; a budget that ignored it would be a budget on
    the readings alone, which is not the line that wraps.
    """
    language = _section_language()
    budget = int(language.get("width") or 0)
    wanted = ((language.get("readings") or {}).get(name) or [])
    by_label = {str(m.get("label") or "").strip(): m
                for m in (section.get("metrics") or []) if isinstance(m, dict)}
    parts: list[str] = []
    for label in wanted:
        cell = by_label.get(str(label).strip())
        if cell is None:
            continue
        piece = f"{cell.get('label')} {cell.get('value')}".strip()
        joined = " · ".join(parts + [piece])
        if budget and used + _cells(joined) > budget:
            break
        parts.append(piece)
    return " · ".join(parts)


def _block_summary(name: str, section: dict, health: dict) -> str:
    """One block's line on the face: what state it is in, and its readings.

    THE STATE WORD READS THE VERDICT'S OWN LISTS, not the section's raw state.
    Which sections faulted, which were never read and which were an established
    absence are decided once, in `env_health`, and this reads those three lists
    rather than re-deriving them from states that mean different things per
    block — a bound gateway's state is how the gateway IS, an unbound one's is
    the absence of a declaration, and a test of `== "OK"` over both is the bug
    that once made every bound gateway render as the word BOUND.

    UNBOUND AND UNREADABLE STAY APART. §2 names them among the states that must
    not be merged, and they are two entries in one table rather than one branch:
    an established absence and a missing reading send a reader to two different
    places, and only one of them is a place to go.
    """
    language = _section_language()
    states = language.get("states") or {}
    state = str(section.get("state") or "")
    if name == "gateway" and state == presentation.GATEWAY_UNBOUND:
        return str(states.get("UNBOUND") or _EMPTY_LEAD_UNBOUND)
    if name in (health.get("sections_unread") or []):
        return str(states.get("UNREADABLE") or _EMPTY_LEAD_UNREADABLE)
    if name in (health.get("sections_faulted") or []):
        return str(states.get("FAULTED") or _EMPTY_LEAD)
    readings = _block_readings(name, section, used=_cells(_face_prefix(name)))
    if readings:
        return readings
    if name == "gateway":
        # No cells at all, this block: it is a list of named things and how each
        # of them is, and §2 gives the bound-and-healthy case its own word. §M4
        # already anticipated it — `_EMPTY_LEAD_*`'s docstring names «Ready and
        # friends» as the third of the gateway's three states.
        return str(states.get("OK") or "")
    # A block that was read, is not faulted, and yet matched none of the
    # readings declared for it. There is nothing true to say here, so it says
    # nothing and the heading stands alone — `未读到` would be false (the block
    # was read) and `就绪` would be this layer deciding that a block with no
    # readings is a healthy one, which is the substitution §6's false-green ban
    # is about. The fold under the heading still holds whatever the block did
    # produce, and a test asserts no scenario reaches this branch.
    return ""


def status_card(model: dict) -> dict:
    """Delta first, three blocks of cells, and the day's timeline.

    §6's acceptance is about this card specifically: on a 390px screen the
    first screen has to answer «is anything different» without expanding
    anything. The order below is that answer, top to bottom:

      1. the line that changed since the last hour, or the line saying nothing
         did — this is the first thing on the card because a reader who gets one
         screen wants the delta, and a dashboard that leads with the full state
         makes them compute it themselves;
      2. the whole state, as cells two abreast, so the delta can be checked;
      3. the day as intervals, folded;
      4. the engineering account — schedule, collection stamp, coverage,
         provenance — behind one fold at the bottom.

    The engineering block is folded and not deleted. Every sentence in it is
    what makes the numbers above checkable, and the previous shape printed it
    all inline between the blocks, so the card read as a monitor explaining
    itself to its author. It is one tap away now, under a label that says what
    it is.
    """
    health = model.get("env_health") or {}
    template, icon = _template(health.get("verdict"))
    # The title names the broadcast, not a finding. "环境状态" read as a
    # classification and sat beside an alert card whose title also read as one,
    # so the two faces looked like the same product saying two things. This one
    # is the hourly dashboard and it says so; silence about problems belongs to
    # the alert face, which exists for exactly that.
    # The node label arrives as the ref the rest of the product keys on
    # (`device:tokyo-shared-dev-01`), and the scheme in front of it is not part
    # of the name — see `presentation.device_name`, which is also where the
    # seven characters the title no longer spends are accounted for.
    title = _titled(f"{icon} {presentation.device_name(model.get('node_label')) or '本机'}"
                    f" · 资源播报", model)

    delta = model.get("delta") or {}
    els: list[dict] = []
    # §2's first screen opens with one line that answers two questions at once:
    # what state the environment is in, and what moved since the previous
    # reading. The state word was not on this line before — the reader had to
    # infer it from `整体状态 正常→建议查看` one line down, which is a sentence
    # about a comparison rather than about the machine. Both are the same
    # vocabulary the two faces share (`FACE_WORDS`), so the word here and the
    # word on an incident card are one word.
    els.append(md(f"**{icon} {face_word(health.get('verdict'))}**　"
                  f"{delta.get('headline') or '本次读数'}"))
    if delta.get("detail"):
        # The detail keeps its own line and is NOT folded. It carries the one
        # thing the headline cannot: which fields moved (`整体状态 正常→建议查看`)
        # or, when nothing moved, that hours in between were never read at all.
        # §6 counts the lines a reader must get past to reach a reading, and
        # this one is a reading.
        els.append(md(str(delta["detail"])))
    # §10 gives routine broadcasts a window and §11 gives each workday one card,
    # and the card used to open its first screen by answering both: «**播报日程**
    # 2026-10-04 是休息日，不创建例行状态卡，也不补发；事故卡另按 P1/P2 策略» and
    # «**当天卡** CN-Mainland/2026-09-30 已更新 12/13 个时点». Those are the
    # monitor's own bookkeeping — §1 names 首屏UID/型号/端口 as the thing a
    # reader scrolls past to reach a reading, and a day reference and a workday
    # rule are two more of the same kind — so both move to `_engineering` at the
    # back with the rest of the account of how this card came to exist. The
    # machine's state, which the DAY is about, stays.
    # A collection gap is not an engineering account and does not fold with one.
    # «This hour was read» and «this hour could not be read» are different
    # findings, and a reader who has to open a panel to learn which one they are
    # looking at has been told neither. It is printed only when it is non-empty,
    # so a clean hour gains no line.
    gaps = list(health.get("gaps") or [])
    if gaps:
        els.append(md(_gap_line(health, gaps)))
    # §11: one card per workday, and the day's hours as intervals, folded. The
    # panel is printed only when there IS one — a panel that opens onto nothing
    # is one tap away from the explanation it looks like it is hiding, which is
    # why the section blocks open one conditionally too.
    #
    # The timeline stays on the face while its own sentence does not, and the
    # distinction is the point: the sentence is how the day was SCHEDULED and the
    # panel is how the machine WAS across it. A reader who opens one fold should
    # find the other, and hiding the day's actual shape behind a fold while the
    # reason for the fold stood on the face would have the card arguing with
    # itself. The lines arrive already formatted in the model: a renderer that
    # knew how to read a history row would be a second reader of the row's schema.
    day = model.get("day") or {}
    entries = list(day.get("timeline") or [])
    if entries:
        els.append(panel(_timeline_title(day, model), [e["says"] for e in entries],
                         expanded=False))
    for name in model.get("order") or presentation.SECTIONS:
        section = (model.get("sections") or {}).get(name) or {}
        heading = _SECTION_TITLES.get(name, name)
        summary = _block_summary(name, section, health)
        # `label` and NOT `title`: this function already has a `title`, and the
        # card's own header is built from it on the last line. A local of the
        # same name shadows it for the whole function, so the card went out
        # titled with whichever block happened to be written last — which the
        # escaping test caught, because the hostile node label it plants stopped
        # appearing anywhere on the card at all.
        label = (f"{_face_prefix(name)}{summary}" if summary
                 else heading)
        inside = _block_fold(section)
        if inside:
            # ONE LINE PER BLOCK, AND THE LINE IS THE FOLD.
            #
            # The block used to print its name, then up to six cells two
            # abreast, then a separate «明细» fold holding the surplus. Four
            # blocks of that is about twenty-four lines, and §1/§2 cap the first
            # screen at ten to twelve — the shape read as a dashboard, not as a
            # message, and a reader looking for one number had to read the whole
            # machine first.
            #
            # Now the summary line IS the panel header: the block says what
            # state it is in and its two or three load-bearing readings, and the
            # tap that used to open 明细 opens the same readings with the units,
            # thresholds and provenance that make them checkable. Nothing moved
            # out of reach; the same readings are one tap down instead of on the
            # face, which is the trade §2's line budget forces and the one a
            # reader on a phone makes anyway.
            els.append(panel(label, inside, expanded=False))
        else:
            # A block with nothing to put in a fold is a line and not a fold.
            # A panel that opens onto nothing is one tap away from the
            # explanation it looks like it is hiding, which is why the block
            # headings opened one conditionally before this change too.
            els.append(md(f"**{label}**"))

    # No `_footnote`. §M4: 「状态卡无"只读告警"» — the sentence is the ALERT
    # card's promise, and it says something about a card that proposes nothing.
    # On the status card it promised not to do a thing the status card never
    # could do, and §1's screenshot names it as one of the four lines a reader
    # has to get past before reaching a reading. The two faces have different
    # chrome now; sharing one sentence is what put it here.
    # §3.4's fourth block, and §13 fixes it LAST of the first screen's blocks —
    # 我的资源、整机、关键基础设施、当前问题 — which is why it sits here rather than
    # with them in `order`: the three above are readings of this node, and this
    # is the node's own list of findings about itself, so a reader who has just
    # seen that everything measured is fine wants it answered before the fold.
    #
    # It is printed even when it could not be read, and that is the whole point
    # of it being a block rather than a conditional line. A block that vanishes
    # when the register is unreadable is read as a block that found nothing, and
    # «nobody could look» and «nothing is wrong» are the two statements §3.4
    # spends its paragraph keeping apart. §13 fixes the block's place on the
    # first screen; a fixed block has to be able to say «不知道».
    els.extend(_problems_blocks(model.get("problems") or {}))
    els.extend(_engineering(health, model))
    return _card(template, title, els, model)


def _timeline_title(day: dict, model: dict) -> str:
    """The day panel's label, and it counts what the list actually holds.

    Three numbers, because a day has three shapes a reader wants before opening
    anything: how many hours were recorded, how many of them were a change, and
    how many stretches nobody observed. The third one is why this is not called
    「今日历史» any more — a history is a list of hours that happened, and the
    thing this panel must not hide is the hours that did not.

    WHICH DAY IT NAMES, it names from the rows it is about. The panel lists the
    day store's rows, and on a working day those are today's — but a card
    rendered on a rest day still finds the last working day's rows in the store,
    and a title reading 「当天» over them would be the one claim on this card
    that no reading supports. The same predicate the delta and the timeline
    extension use decides it, so the three cannot describe two different days.
    """
    updates = day.get("updates") or 0
    transitions = day.get("transitions") or 0
    unobserved = day.get("unobserved") or 0
    # THE COUNTS ARE THE TITLE; THE DAY IS A LABEL ON THEM. Each candidate below
    # is a whole title and the first one that fits the row is the one returned,
    # which is the same trade `_problem_line` makes one line at a time. What
    # gives way, in order, is what costs a reader least:
    #
    #   1. the day label — «当天» is a scope word and not a finding, and it is
    #      the first thing to go when the three numbers need the row;
    #   2. the measure words «个» and «处», five cells together;
    #   3. the gap clause — and this one only as a last resort, because §3 makes
    #      an unread hour a hard boundary and not a footnote.
    #
    # The day is named WHEN IT IS NOT TODAY, and named from a row that says so
    # — see the docstring above. None of the candidates carries a caret: the
    # fold's own widget draws it (see `_face_prefix`), and the two cells `▸ `
    # used to spend are the two the numbers below contest for on a 390px
    # screen — which is why this function exists at all.
    if presentation.store_is_another_day(day, model.get("schedule") or {}):
        day_label = str(day.get("day_ref")).rsplit("/", 1)[-1]
    else:
        day_label = "当天"
    counted = f"{updates} 个时点 · {transitions} 处变化"
    bare = f"{updates} 时点 · {transitions} 变化"
    # Named only when it is nonzero, and named LOUDLY: a panel whose title says
    # «无缺口» on a quiet day would make the reader check it on the loud day, and
    # the loud day is exactly when they should not have to.
    #
    # COUNTED IN 段 IN BOTH FORMS. The long form always counted STRETCHES
    # («⚠️ 2 段无观测»); the compressed one said «缺口 2», and on the card that
    # needs it most — `day_gap`, where the delta four lines above the panel is
    # reading «缺 1 个时点读数» — that put two counts of two different things
    # («2 stretches nobody observed» and «1 tick skipped since the last
    # reading») under one word, with nothing on the face to tell them apart. 段
    # is the unit the delta does not use, and the rest of the phrase is the long
    # form's own, so the two spellings of this title are now one sentence. One
    # author for the phrase, so the loud form and the compressed one differ by
    # the ⚠️ and nothing else.
    gap_words = f"{unobserved} 段无观测" if unobserved else ""
    gap_clause = f" · {gap_words}" if gap_words else ""
    if gap_words:
        counted += f" · ⚠️ {gap_words}"
    width = presentation.display_width("width")
    for text in (
        f"{day_label} · 变化时间线（{counted}）",
        f"变化时间线（{counted}）",
        f"变化时间线（{bare}{gap_clause}）",
        # The gap clause is the LAST thing to give way and not the first (§3
        # makes an unread hour a hard boundary), so this candidate is named
        # rather than reached by splitting the one above on its own wording —
        # a sentinel that stops matching fails silently, and that failure would
        # be this candidate quietly becoming a duplicate of the one before it.
        f"变化时间线（{bare}）",
        "变化时间线",
    ):
        if presentation.fits(text, width):
            return text
    return "变化时间线"


def _engineering(health: dict, model: dict) -> list[dict]:
    """The account a reader needs to check the numbers, behind one fold.

    Everything here was inline on the face before. None of it is deleted,
    because each sentence answers «why should I believe that number»: when the
    reading was taken, what was read and what was not, which sections no binding
    named, and which of the two objects on this machine each number belongs to.
    The rule this fold implements is about ORDER and not about honesty — the card
    leads with what changed and keeps its self-account at the back.

    COLLECTION GAPS ARRIVE HERE IN BOTH FORMS, and the two are not a
    duplication. `status_card` prints WHICH readings did not arrive, as a
    finding, just above this fold — «this hour could not be read» is a fact
    about the machine. The per-gap sentences are here, as an account of how the
    reading was taken: `summary（resource summary）未读到：exit 2` names the
    read, the command and the exit status, which is 采集口径 and belongs with
    the rest of it (§R15). The face used to carry that sentence directly, which
    put a schema key, a CLI invocation and an exit code on the first screen.

    Everything else the card used to say about itself is here, including the two
    sentences that opened its first screen: which workday it belongs to and how
    the broadcast window was decided. §1's screenshot lists 首屏UID/型号/端口 as
    the lines a reader has to get past before reaching a reading, and a day
    reference and a workday rule are two more of the same kind.
    """
    inside = [f"采集时间 {_stamp(model)} · 观测范围 "
              f"{'、'.join(_SCOPE_NAMES.get(n, n)
                          for n in (health.get('sections_read') or [])) or '无'}"
              + (f" · 未读到 {'、'.join(_SCOPE_NAMES.get(n, n)
                                       for n in health['sections_unread'])}"
                 if health.get("sections_unread") else "")
              + (f" · {_EMPTY_LEAD_UNBOUND} "
                 f"{'、'.join(_SCOPE_NAMES.get(n, n)
                             for n in health['sections_unbound'])}"
                 if health.get("sections_unbound") else "")]
    note = (model.get("coverage") or {}).get("note")
    if note:
        inside.append(note)
    # Where the 当前问题 block's list can be seen whole. The block counts what it
    # does not show — «另有 12 项未逐条列出» — and a count with no way to reach
    # the rest of them is a dead end; the reach is a command, and §2 keeps the
    # command off the first screen the same way `_gap_line` does. It is here and
    # not on the face for the same reason, and it says which list it is talking
    # about rather than repeating the sentence for a reader who has scrolled.
    problems = model.get("problems") or {}
    if problems.get("state") == "READ" and problems.get("note"):
        # The register's own note: this list is the register's current state
        # and not a delta since the last broadcast.
        inside.append(problems["note"])
    # Whatever the presenter put below the fold, and it is appended whatever
    # state the block is in, because both states have something here. Read, it
    # is how much of the register the face deliberately did not carry; unread,
    # it is which command was asked and what it said — the same account every
    # other failed read on this card gives, in the same panel, for the reason
    # §R15 gives: the tool's own words are 采集口径 and belong with the rest of
    # it rather than on the first screen.
    inside.extend(str(line) for line in problems.get("below") or [])
    gaps = list(health.get("gaps") or [])
    if gaps:
        # The sentences the face no longer prints, one per gap and not joined
        # by «；»: on the face they were one line read as one missing thing, and
        # here each is its own account of its own read.
        inside.append("采集缺口\n" + "\n".join(f"· {g}" for g in gaps))
    schedule = model.get("schedule") or {}
    if schedule.get("says"):
        inside.append(f"播报日程 {schedule['says']}")
    day = model.get("day") or {}
    if day.get("says"):
        inside.append(f"当天卡 {day['says']}")
    return [panel("技术诊断", inside, expanded=False)]


# ---------------------------------------------------------------------------
# The Status Update Notice. Deliberately NOT a card.
# ---------------------------------------------------------------------------

def notice_lines(decision: dict, *, node_label: str | None = None) -> list[str]:
    """What an update notice says, as lines. The one set of words.

    WHY THIS IS TEXT AND NOT A CARD
    -------------------------------
    The notice exists because **a patched message raises no notification**: on
    2026-09-30 the day's card was patched seven times between 08:00 and 14:00,
    every patch arrived, and the reader's phone was silent for all seven. So the
    notice's entire job is to BE a new message, and it has to be unmistakably
    not the card. A card shape would be read as «the monitor sent me a second
    status card» — which is precisely what §11's one-per-workday rule exists to
    prevent, and a reader who believes it has two dashboards stops trusting
    either. Three lines of text cannot be mistaken for one.

    WHY IT NAMES THE CARD'S VERSION
    -------------------------------
    A notice owns nothing. Every word above the third line is QUOTED — the
    verdict, the observation and the gap count come out of the same day-store
    row the card was built from, and `monitoring_policy.notice` passes that row
    through rather than recomputing it. The third line is what makes the first
    two checkable: it names the version of the card the reader can open, so «is
    this notice telling me something new» is answerable by looking rather than
    by trusting. A notice that could not be checked against a card would be a
    second source of truth about «现在怎么样», which is the role the card
    occupies alone.

    A NOT-DUE DECISION HAS NO WORDS, AND THIS REFUSES TO INVENT THEM
    ----------------------------------------------------------------
    `decision["due"]` is false for a reason, and the reason is in
    `decision["says"]` — that sentence is the answer to «why did nothing
    arrive», not a message to send. Composing how it *would* have read is how a
    preview comes to look like a send, and the one thing this file must not do
    is hand a transport something that was never due.
    """
    if not (decision or {}).get("due"):
        raise presentation.PresentationError(
            f"这一拍不提醒（{ (decision or {}).get('reason') }："
            f"{(decision or {}).get('says')}），因此没有话要说。"
            f"把「如果提醒会说什么」渲染出来，正是让预览看起来像一次发送的做法")

    template, icon = _template(decision.get("verdict"))
    # Same strip as the card's title, for the same reason: the notice names the
    # device in words, not in the ref the product keys on.
    device = presentation.device_name(node_label or decision.get("node_label"))
    head = f"{icon} {device or '本机'}"
    hhmm = str(decision.get("hhmm") or "").strip()
    if hhmm:
        head += f" · 状态更新 {hhmm}"
    tick, total = decision.get("tick"), decision.get("ticks_per_workday")
    if isinstance(tick, int) and isinstance(total, int) and total:
        head += f" · 第 {tick}/{total} 小时"

    middle = " · ".join(part for part in (
        face_word(decision.get("verdict")),
        str(decision.get("observation") or "").strip(),
        # Only when there is one. «缺口 0» on every quiet hour is the kind of
        # line a reader learns to skip, and it would sit exactly where the
        # nonzero case needs to be noticed.
        (f"缺口 {decision['gaps']}" if decision.get("gaps") else ""),
    ) if part)

    version = str(decision.get("card_version") or "").strip()
    return [head, middle, f"当天卡已更新 · {version}"]


def notice_text(decision: dict, *, node_label: str | None = None) -> str:
    """The same words as one message body, for a transport that carries text."""
    return "\n".join(notice_lines(decision, node_label=node_label))


# ---------------------------------------------------------------------------
# The delivery fault. The message a FAILED patch gets INSTEAD of a notice.
# ---------------------------------------------------------------------------

def fault_lines(finding: dict, *, node_label: str | None = None) -> list[str]:
    """What is said when a card did NOT reach the chat. Three lines, no more.

    WHY THIS EXISTS AT ALL
    ----------------------
    §7① is two requirements and the notice only answers one of them. «PATCH
    FAILED 不得发送「状态已更新」» is the refusal, and a refusal is silent by
    nature — so on its own it recreates the exact defect the notice was invented
    to fix: something happened, and the phone said nothing for another hour. The
    second half is «应形成投递 finding/incident», and this is that half as
    words: the finding `card_delivery.delivery_finding` states, said to the one
    person who can act on it.

    WHY IT CARRIES NO READING
    -------------------------
    It quotes nothing about the machine. The reading the reader is missing is
    the one they cannot have — it did not arrive — and a fault message that
    reproduced the card's numbers would be a second copy of a card that is, by
    construction, not the card in the chat. What it names instead is WHICH
    version failed to arrive and WHEN it was attempted, because those are the
    two facts a repair needs and the two a reader can check against the ledger.

    WHAT IT DOES NOT MAKE THE READER DO
    -----------------------------------
    Every refusal to state has a residue and this one's is small: a reader of
    these three lines knows the card in the chat is not current. What they do
    NOT know is whether the machine is fine, and the message must not imply it
    either way — which is why the third line points at the ledger rather than
    at a verdict this message has no standing to give.
    """
    finding = finding or {}
    if not finding.get("fault"):
        # Same rule as `notice_lines`: a message that was never due has no
        # words, and composing how it *would* have read is how a preview comes
        # to look like a send.
        raise presentation.PresentationError(
            f"这不是一次投递失败（{finding.get('finding') or '没有发现'}），"
            f"因此没有话要说。把「如果是失败会说什么」渲染出来，正是让预览看起"
            f"来像一次发送的做法")
    # A fault is not a reading, so it does not take the card's state glyph —
    # 🚨 is «需要处理», and this is exactly that: a card that did not arrive
    # stays not-arrived until somebody looks.
    device = presentation.device_name(node_label)
    head = f"🚨 {device or '本机'} · 当天卡没有送达"
    version = str(finding.get("version") or "").strip()
    instant = str(finding.get("at") or "").strip()
    tail = " · ".join(part for part in (
        f"那一版 {version}" if version else "",
        f"{instant} 投递" if instant else "",
    ) if part)
    lines = [head, str(finding.get("says") or "").strip()]
    if tail:
        lines.append(tail)
    return lines


def fault_text(finding: dict, *, node_label: str | None = None) -> str:
    """The same words as one message body, for a transport that carries text."""
    return "\n".join(fault_lines(finding, node_label=node_label))


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
#: measurement rather than a hope: no fixed widths, no tables and `overflow-wrap`
#: on every text container.
#:
#: What a narrow screen does to `column_set` is Feishu's decision and not this
#: file's, and this file had it wrong: the preview used to stack the columns
#: below 390px. Feishu stacks only under `flex_mode: "stretch"` — under the
#: `"none"` this product sets, 「在窄屏幕下按比例压缩列宽度」, so a phone shows
#: the same two columns narrower. The stacked preview was the flattering one,
#: which is the direction that survives review and then differs from the
#: conversation. See `_flex`.
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
.panel > summary {{ padding: 8px 10px; cursor: pointer; font-weight: 600;
                    list-style: none; }}
.panel > summary::-webkit-details-marker {{ display: none; }}
.panel > summary .caret {{ display: inline-flex; align-items: center;
                           justify-content: center; vertical-align: -4px;
                           font-size: 12px; color: #6a737d; }}
.panel > summary .caret.far {{ float: right; }}
.panel[open] > summary .caret {{ transform: rotate(var(--open-angle, 0deg)); }}
.panel > p {{ padding: 0 10px 8px; margin: 0; white-space: pre-wrap; }}
.cols {{ display: flex; flex-wrap: nowrap; gap: 8px; }}
.cols > div {{ min-width: 0; }}
.sample {{ background: #fff4ce; border: 1px solid #d9b400; border-radius: 6px;
           padding: 8px 10px; margin: 0 0 12px; font-weight: 600; }}
.foot {{ color: #6a737d; font-size: 12px; }}
.cap {{ font-size: 12px; color: #6a737d; margin: 0 0 6px; }}
</style></head><body><div class="wrap">
"""

_HTML_TAIL = """</div></body></html>\n"""


def _esc(text) -> str:
    """Escape everything. The card content includes ids, paths and process
    names off a shared machine, and a preview is an HTML document that some
    browser will render — `litellm-shared-postgres` is fine, `<script>` is
    not, and the difference is not one this module is entitled to guess at."""
    return _html.escape(str(text if text is not None else ""), quote=True)


#: The one piece of markdown this product puts in a card, and the one a preview
#: therefore has to draw. Feishu's `lark_md` renders `**bold**` — every heading
#: on every card is one — so a preview that printed the asterisks was answering
#: «what is the structure» while looking like it answered «how does this read on
#: a phone». Those are different questions and the second one is the acceptance
#: criterion; a screenshot of the first is a screenshot of the wrong thing.
#:
#: Deliberately not a markdown implementation. The card uses exactly one span
#: and adding a second is a decision about the card, not about the preview.
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


#: Feishu's `standard_icon` tokens this product can be handed, as something a
#: browser draws. Same reasoning as `_BOLD` one paragraph up, applied to the
#: other thing the card declares and the preview has to draw: a fold header is
#: a `collapsible_panel` with an icon, and a preview that drew only the title
#: was answering «what does the text say» while looking like it answered «what
#: does the header look like» — which is where §2's line budget is spent.
#:
#: A token nobody listed is drawn as `?` rather than as a chevron, the rule
#: `face_word` follows for a verdict nobody taught it. An icon the preview
#: invents agrees with nothing.
_FOLD_ICONS = {
    "down-small-ccm_outlined": "▾",
    "up-small-ccm_outlined": "▴",
    "right-small-ccm_outlined": "▸",
    "left-small-ccm_outlined": "◂",
}
UNKNOWN_FOLD_ICON = "?"
#: What Feishu draws when a panel declares no icon: its own caret, at the far
#: edge. The preview draws that rather than nothing, so a fold with no declared
#: icon is still a fold in the picture.
DEFAULT_FOLD_ICON = "▾"


def _fold_icon(header: dict) -> str:
    """The fold's affordance as HTML, from the header's own declaration.

    The DECLARED position and angle are honoured, because they are what decides
    what the header looks like: `follow_text` puts the icon after the title
    (Feishu's default, `right`, puts it at the far edge) and
    `icon_expanded_angle` is the rotation an open fold gets. Drawing it in one
    fixed place would be a picture of a fold nobody sends — and the folds here
    are `follow_text`, so a preview that drew a LEADING caret was drawing a
    shape that does not exist. That is exactly what the 2026-10-04 screenshots
    showed: `<details>`'s own marker at the left beside the title's own `▸`,
    two triangles where the card has one chevron on the right.

    `float: right` is an approximation of Feishu's `right`, which centres the
    icon on the header rather than on its first line. Nothing this product
    emits asks for it; it is here so that the branch is drawn rather than
    silently misplaced if something ever does.
    """
    header = header or {}
    icon = header.get("icon") or {}
    token = icon.get("token")
    if token is None:
        glyph, far = DEFAULT_FOLD_ICON, True
    else:
        glyph, far = _FOLD_ICONS.get(str(token), UNKNOWN_FOLD_ICON), \
            header.get("icon_position") != "follow_text"
    size = str(icon.get("size") or "16px 16px").split()
    angle = header.get("icon_expanded_angle")
    style = (f"width:{size[0]};height:{size[-1]};"
             f"--open-angle:{angle if angle is not None else 0}deg")
    return (f'<span class="caret{" far" if far else ""}" '
            f'style="{_esc(style)}">{_esc(glyph)}</span>')


def _inline(text) -> str:
    """Card text as HTML: escaped first, then the one span drawn.

    Escaping happens BEFORE the substitution and not after, so a node label
    containing `<script>` is still inert when it lands inside `<strong>`. The
    escape does not touch `*`, so the pattern still finds the spans it is meant
    to find, and the substitution introduces no character the escape would have
    had to be applied to.
    """
    return _BOLD.sub(r"<strong>\1</strong>", _esc(text))


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
            out.append(f'<p class="{css}">{_inline(content)}</p>')
        elif tag == "hr":
            out.append("<hr>")
        elif tag == "collapsible_panel":
            header = el.get("header") or {}
            title = (header.get("title") or {}).get("content") or ""
            inner = "\n".join(f"<p>{_inline(e.get('content'))}</p>"
                              for e in el.get("elements") or [])
            out.append(f'<details class="panel"{" open" if el.get("expanded") else ""}>'
                       f'<summary>{_esc(title.replace("**", ""))}'
                       f'{_fold_icon(header)}</summary>{inner}</details>')
        elif tag == "column_set":
            cells = "".join(f'<div style="{_flex(c)}">'
                            f"{_html_body(c.get('elements') or [])}</div>"
                            for c in el.get("columns") or [])
            out.append(f'<div class="cols">{cells}</div>')
    return "".join(out)


def _flex(column: dict) -> str:
    """One Feishu column as the CSS that gives it the same width.

    The width rule has to be repeated here because a preview is not Feishu and
    there is no way to ask it: the schema layer is the only place the two
    agree, so the values it carries are translated rather than guessed. What
    the translation must NOT do is change under a narrow screen, which is
    exactly what the `@media` rule this replaced did — see `MOBILE_WIDTH`.
    """
    width = column.get("width")
    weight = column.get("weight")
    if width == "auto":
        return "flex: 0 0 auto"
    if isinstance(weight, (int, float)) and weight > 0:
        return f"flex: {weight:g} 1 0"
    # `fill`, and anything this translation does not recognise: the column takes
    # an equal share of what is left, which is also what Feishu does with a
    # weighted column whose weight is 1 — the only shape this product writes.
    return "flex: 1 1 0"




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
