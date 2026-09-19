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


def _footnote(model: dict) -> list[dict]:
    return [md("本卡只报告 ServerOS 已经记录的事实。没有终止、没有重启、没有清理 —— "
               "需要动作的条目在 ServerOS 里带建议，不在这里。")]


# ---------------------------------------------------------------------------
# The alert card. §1 + §4.
# ---------------------------------------------------------------------------

def alert_card(model: dict) -> dict:
    """At most three events, each with the §4 fields, and what did not fit.

    §1's two prohibitions shape this: the card carries no copy of the status
    card's three blocks, and no all-history OPEN/RESOLVED totals. What it
    carries instead of the second is how much was left out, because a short
    card is otherwise read as a quiet machine.
    """
    events = model.get("events") or []
    omitted = model.get("omitted") or {}
    template, icon = _template(
        "CRITICAL" if any(e["severity"]["level"] == "high" for e in events)
        else ("ATTENTION" if events else "OK"))
    title = _titled(f"{icon} 需要关注 · {len(events)} 件" if events
                    else f"{icon} 没有需要处理的新变化", model)

    els: list[dict] = _sample_line(model)
    # The title is NOT repeated here. It is already the header, and the first
    # draft printed it twice — once in the coloured bar and once as the first
    # body line — which on a 390px screen spends the most valuable line of the
    # card saying what the line above it just said.
    if not events:
        # An empty alert card is a real answer and it is only honest with the
        # counts beside it. "没有新变化" over a register with 113 backlogged
        # entries and 1112 never-announced resolutions is true and misleading.
        els.append(md(_quiet_line(omitted)))

    for event in events:
        els.append(panel(_event_title(event), _event_lines(event), expanded=True))

    if events:
        els.append(md(_omitted_line(model)))
    els.append(md(f"采集 {model.get('at')} · 设备 {model.get('device_ref') or '未知'}"))
    els.extend(_footnote(model))
    return _card(template, title, els, model)


def _event_title(event: dict) -> str:
    severity = event["severity"]["word"]
    subject = event["subject"]["text"]
    return f"{severity} · {subject}"


def _event_lines(event: dict) -> list[str]:
    """The §4 field set, in the order §4 lists it, one field per line.

    "发生了什么 · 对象显示名 · 事件类型 · 原严重度 · 影响 · 确定性/证据质量 ·
    建议 · 首次/最近观测 · 持续时间 · 引用" — the order is kept because it is
    the order a reader needs it in: what, about what, then how bad, then
    whether to believe it.
    """
    what = event["what"]
    certainty = event["certainty"]
    times = event["times"]
    lines = [
        f"**发生了什么** {what['text'] or what['code']}",
        f"**事件类型** {event['event']} · **原严重度** {event['severity']['word']}"
        f"（{event['severity']['level']}）",
        f"**影响** {event['impact']['text']}",
        f"**确定性** {certainty['state']}"
        + (f" —— {certainty['why']}" if certainty["why"] else "")
        + f" · 来源 {certainty['source'] or '未记录'}"
        + (f" · 观测于 {certainty['observed_at']}" if certainty["observed_at"] else ""),
        f"**建议** {event['action']['word']} —— {event['action']['text']}",
        f"**建议的效力** {event['action']['authority']}",
        f"**首次观测** {times['first_seen'] or '未记录'}"
        f" · **最近** {times['last_seen'] or '未记录'}",
        f"**持续时间** {times['duration_text']}"
        + (f"（按 {times['duration']['basis']} 计）" if times["duration"]["basis"]
           else f"（{times['duration'].get('why') or '无法计算'}）"),
        f"**引用** {event['ref'] or '未记录'}",
    ]
    if event["subject"]["owner"]:
        owner = event["subject"]["owner"]
        lines.append("**归属（生产者原始字段）** " + " · ".join(
            f"{k} {owner.get(k)}" for k in
            ("class", "role", "lifecycle", "uid", "session", "unit")
            if owner.get(k)))
    lines.append("**证据与详情**\n" + (event.get("evidence") or ""))
    return lines


def _omitted_line(model: dict) -> str:
    omitted = model.get("omitted") or {}
    parts = []
    if omitted.get("rows"):
        parts.append(f"另有 {omitted['rows']} 条同类事件未进卡面")
    if omitted.get("beyond_card_cap"):
        parts.append(f"{omitted['beyond_card_cap']} 条在更早的批次里已被计数")
    if omitted.get("backlog"):
        parts.append(f"{omitted['backlog']} 条在投递基线之前就已存在（积压，不重复播报）")
    if omitted.get("silent_resolutions"):
        parts.append(f"{omitted['silent_resolutions']} 条从未告知过，不计入恢复")
    return " · ".join(parts) if parts else "没有未进卡面的条目"


def _quiet_line(omitted: dict) -> str:
    return (f"本批没有新增、升级或复发。登记表中仍有 "
            f"{omitted.get('backlog') or 0} 条积压未播报 —— 安静不等于没事。")


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
    title = _titled(f"{icon} 已恢复 · {counts.get('announced') or 0} 件"
                    if items else f"{icon} 本批没有已告知且已恢复的问题", model)

    els: list[dict] = _sample_line(model)
    for item in items:
        what = item["what"]
        els.append(md(
            f"✅ **{what['what'] or what['code']}**\n"
            f"对象：{item['subject']['text']}\n"
            f"恢复依据：{item['basis']['note']}（state {item['basis']['state']}）\n"
            f"出现于 {item['times']['first_seen'] or '未记录'} · "
            f"恢复于 {item['times']['resolved_at'] or '未记录'} · "
            f"持续 {item['times']['open_for_text']}"))

    omitted = model.get("omitted") or {}
    tail = []
    if omitted.get("announced_beyond_limit"):
        tail.append(f"另有 {omitted['announced_beyond_limit']} 件已告知的恢复未进卡面")
    if omitted.get("never_announced"):
        tail.append(f"{omitted['never_announced']} 件从未告知过，不计入恢复"
                    "（人不能被通知一个他从来没收到过的坏消息）")
    if tail:
        els.append(md(" · ".join(tail)))
    els.append(md(f"本批恢复总数 {counts.get('recovered') or 0} · "
                  f"其中已告知 {counts.get('announced') or 0} · 采集 {model.get('at')}"))
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
    title = _titled(f"{icon} {model.get('node_label') or '本机'} · 环境状态", model)

    els: list[dict] = _sample_line(model)
    els.append(md(f"**环境可用性** {health.get('verdict_text')}\n"
                  f"**观测** {health.get('observation_text')}"))
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
    els.append(md(f"采集时间 {model.get('at')} · 观测范围 "
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
            "subtitle": {"tag": "plain_text", "content": f"{model.get('at')} 更新"},
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
            css = "foot" if content.startswith("本卡只报告") else ""
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
