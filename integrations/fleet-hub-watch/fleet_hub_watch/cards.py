"""The hub's Feishu cards (CardKit 2.0). Pure: models in, card JSON out.

Three cards, three levels (`levels.py`):

* **资源播报卡** (`resource_card`) — one per workday, patched in place. Header
  colour is the worst device level: green 正常 / yellow 注意 / red 报警, grey only
  when nothing is wrong but something could not be read.
* **报警卡** (`alert_card`) — one per episode (a reading on a device, or one
  run). Yellow 注意 or red 报警 while open; patched to green 已恢复 when it ends;
  patched to grey 已升级 when a yellow episode escalates and a new red card is
  sent in its place.
* **任务完成卡** (`completion_card`) — completions in a short window, one green
  card.

Rules followed (CARDS-STYLE.md): the header is the result, not the object (R1);
the title is not repeated in the body (R2); what needs attention is at the top
and normal detail is folded (R8/R10); "impact" says 待确认 unless it was checked
(R7); one "未自动操作" line, not one per section (R2); sample cards say so in the
title and the body (R13). Feishu limits respected: `update_multi: true` so the
card can be patched (required on both send and patch), every element_id is a
letter then ≤20 characters, and the whole card stays under 30 KB — all three are
checked by `runtime/fleet_monitor_card.validate` before a card leaves here.

Wording, headings and the daily report's sections come from the broadcast spec
(`spec.py`, `card-spec.yaml`); every function takes `spec=` and defaults to
`DEFAULT_SPEC`, which reproduces the cards exactly as they were before the
spec existed. Facts — levels, colours, which reading is wrong — stay here.
"""
from __future__ import annotations

import hashlib
import re
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .levels import ALERT, ICON, OK, RANK, TEMPLATE, UNCONFIRMED, WARN, WORD, worst
from .spec import DEFAULT_SPEC

CN = ZoneInfo("Asia/Shanghai")
SAMPLE_TITLE = "[示例]"
SAMPLE_LINE = "⚠️ 示例数据 · 非实时采集"
WEEKDAYS = "一二三四五六日"


def _fmc():
    name = "serveros_fleet_monitor_card"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[3] / "runtime" / "fleet_monitor_card.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _md(text: str) -> dict:
    return {"tag": "markdown", "content": _fmc()._wrap_lines(text)}


# -- the house style ------------------------------------------------------------
#
# Owner, 2026-11-10 («飞书卡片太丑了»), read on a desktop: every card uses the
# same few parts — a one-line verdict, grey rounded boxes for things that belong
# together, coloured inline tags for state, large numbers in tiles, small grey
# captions. Text is not hard-wrapped (that was for phones).

TAG_WORD_COLOR = {OK: "green", WARN: "orange", ALERT: "red", UNCONFIRMED: "neutral"}


def _text(content: str, *, size: str | None = None, align: str | None = None) -> dict:
    """A markdown block, not hard-wrapped. `size`: heading / normal / notation."""
    element: dict[str, Any] = {"tag": "markdown", "content": content}
    if size:
        element["text_size"] = size
    if align:
        element["text_align"] = align
    return element


def _itag(word: str, color: str) -> str:
    """A coloured tag inside a line of text."""
    return f"<text_tag color='{color}'>{word}</text_tag>"


def _caption(content: str) -> dict:
    return _text(f"<font color='grey'>{content}</font>", size="notation")


def _box(eid: str, columns: list[list[dict]], *, weights: list[int] | None = None,
         background: str = "grey") -> dict:
    """A row of rounded boxes, one per column."""
    weights = weights or [1] * len(columns)
    return {
        "tag": "column_set", "element_id": eid[:20], "flex_mode": "none",
        "horizontal_spacing": "8px", "margin": "4px 0px 4px 0px",
        "columns": [{"tag": "column", "width": "weighted", "weight": w,
                     "background_style": background, "padding": "10px 12px 10px 12px",
                     "vertical_spacing": "2px", "vertical_align": "top",
                     "elements": elements or [_text(" ")]}
                    for elements, w in zip(columns, weights)],
    }


def _tile(label: str, value: str, color: str = "default", note: str = "") -> list[dict]:
    """A number tile: small grey label, large value, optional grey note."""
    shown = f"<font color='{color}'>{value}</font>" if color != "default" else value
    out = [_caption(label), _text(f"**{shown}**", size="heading")]
    if note:
        out.append(_caption(note))
    return out


def plain(card: dict) -> dict:
    """The same card in the plainest vocabulary — no boxes, no chart, no inline
    tags or sizes — sent when Feishu refuses the styled one."""
    import copy
    card = copy.deepcopy(card)

    def flat(elements: list) -> list:
        out = []
        for e in elements:
            if not isinstance(e, dict):
                continue
            if e.get("tag") == "chart":
                continue
            if e.get("tag") == "column_set":
                parts = []
                for column in e.get("columns") or []:
                    parts += [c.get("content", "") for c in flat(column.get("elements") or [])
                              if c.get("tag") == "markdown"]
                text = "\n".join(p for p in parts if p.strip())
                if text:
                    out.append({"tag": "markdown", "content": text})
                continue
            e = {k: v for k, v in e.items() if k not in ("text_size", "text_align")}
            if e.get("tag") == "markdown":
                e["content"] = re.sub(r"<text_tag color='[^']*'>(.*?)</text_tag>", r"【\1】",
                                      e.get("content", ""))
            if e.get("tag") == "collapsible_panel":
                e["elements"] = flat(e.get("elements") or [])
            out.append(e)
        return out
    body = card.get("body") or {}
    body["elements"] = flat(body.get("elements") or [])
    return card


def _hr() -> dict:
    return {"tag": "hr"}


def _panel(eid: str, title: str, lines: list[str], *, expanded: bool) -> dict:
    return _fmc()._panel(eid, title, lines, expanded=expanded)


def _eid(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:10]
    return f"{prefix}{digest}"[:20]


def _local(moment: datetime | str | None, *, with_date: bool = False) -> str:
    if moment is None:
        return "—"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            return moment
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(CN)
    return local.strftime("%m-%d %H:%M") if with_date else local.strftime("%H:%M")


def _minutes(start: datetime | str | None, end: datetime) -> str:
    if start is None:
        return "—"
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
    total = max(0, int((end - start).total_seconds() // 60))
    return f"{total} 分钟" if total < 120 else f"{total // 60} 小时 {total % 60} 分钟"


#: The header tag's colour for each level. Feishu's `text_tag` palette has no
#: "yellow" that reads on a yellow header, so 注意 is orange.
TAG_COLOR = {OK: "green", WARN: "orange", ALERT: "red", UNCONFIRMED: "neutral"}


def _tag(word: str, color: str) -> dict:
    return {"tag": "text_tag", "text": {"tag": "plain_text", "content": word}, "color": color}


def _card(template: str, title: str, subtitle: str, elements: list[dict], *,
          tags: list[tuple[str, str]] = (), summary: str | None = None,
          width: str = "default") -> dict:
    """One CardKit 2.0 card.

    `tags` become the header's `text_tag_list` (Feishu shows up to three, after
    the title): the state lives there instead of an emoji in the title.
    `summary` is `config.summary`, the line the chat list shows for this
    message, so the state and the number are readable without opening it.
    `width` is `compact` (400 px) for a single alert, `default` otherwise.
    """
    config: dict[str, Any] = {"update_multi": True, "width_mode": width}
    if summary:
        config["summary"] = {"content": summary[:120]}
    header: dict[str, Any] = {"template": template,
                              "title": {"tag": "plain_text", "content": title},
                              "subtitle": {"tag": "plain_text", "content": subtitle}}
    if tags:
        header["text_tag_list"] = [_tag(word, color) for word, color in list(tags)[:3]]
    card = {
        "schema": "2.0",
        "config": config,
        "header": header,
        "body": {"direction": "vertical", "elements": elements},
    }
    return _fmc().validate(card)


# -- 资源播报卡 ---------------------------------------------------------------

def overall(views: list[dict]) -> str:
    """Worst device level, with UNCONFIRMED only when nothing is WARN or ALERT."""
    levels = [v["level"] for v in views]
    top = worst(levels)
    return top if RANK[top] >= RANK[WARN] else (UNCONFIRMED if UNCONFIRMED in levels else OK)


def _reading_text(reading: dict) -> str:
    mark = "" if reading["level"] == OK else f"{ICON[reading['level']]} "
    return f"{mark}{reading['label']} {reading['text']}"


def resource_card(views: list[dict], *, now: datetime, sample: bool = False,
                  spec: dict = DEFAULT_SPEC) -> dict:
    text = spec["resource_card"]
    level = overall(views)
    counts = {lv: sum(1 for v in views if v["level"] == lv) for lv in (ALERT, WARN, OK, UNCONFIRMED)}
    local = now.astimezone(CN)
    title = text["title"].format(icon=ICON[level], word=WORD[level])
    if sample:
        title += f" · {SAMPLE_TITLE}"
    subtitle = text["subtitle"].format(date=f"{local:%m-%d}", weekday=WEEKDAYS[local.weekday()],
                                       time=f"{local:%H:%M}")

    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md(text["summary"].format(
        devices=len(views), alert=counts[ALERT], warn=counts[WARN], ok=counts[OK],
        unknown=counts[UNCONFIRMED])))

    attention = sorted(
        ((v, r) for v in views for r in v["readings"] if r["level"] in (WARN, ALERT)),
        key=lambda pair: -RANK[pair[1]["level"]])
    if attention:
        lines = [f"**{text['attention_heading']}**"] + [
            f"- {ICON[r['level']]} {v['name']} · {r['label']}：{r['text']}" for v, r in attention]
        elements.append(_md("\n".join(lines)))

    elements.append(_hr())
    for view in views:
        # One reading per line: a phone wraps a joined line in the middle of a
        # number. A healthy connection is not a finding, so it is not a line (R1).
        lines = [_reading_text(r) for r in view["readings"]
                 if not (r["key"] == "reach" and r["level"] == OK)]
        if view.get("observed_at"):
            lines.append(text["reading_time"].format(time=_local(view["observed_at"], with_date=True)))
        elements.append(_panel(
            _eid("dev", view["device_ref"]),
            f"{ICON[view['level']]} {view['name']} · {WORD[view['level']]}",
            [line for line in lines if line],
            expanded=view["level"] in (WARN, ALERT)))
    elements.append(_hr())
    elements.append(_md(text["footer"]))
    flagged = counts[ALERT] + counts[WARN]
    summary = (f"{WORD[level]} · {len(views)} 台设备" if not flagged else
               f"{WORD[level]} · " + "；".join(f"{v['name']} {r['label']} {r['text']}"
                                               for v, r in attention[:2]))
    return _card(TEMPLATE[level], title, subtitle, elements,
                 tags=[(WORD[level], TAG_COLOR[level]), (f"{len(views)} 台", "neutral")],
                 summary=summary)


# -- 单台设备的资源卡 ---------------------------------------------------------

#: The colour a value is printed in, by its level (Feishu markdown colours).
VALUE_COLOR = {OK: "green", WARN: "orange", ALERT: "red", UNCONFIRMED: "grey"}
BAR_CELLS = 10


def _bar(value: Any) -> str:
    """A ten-cell bar for a percentage, or '' when the reading is not one."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    filled = max(0, min(BAR_CELLS, round(float(value) / 100 * BAR_CELLS)))
    return "▰" * filled + "▱" * (BAR_CELLS - filled)


def _cell(reading: dict, spark: str = "") -> str:
    """One metric tile: the name small and grey, the value large and coloured,
    a bar under it when the value is a share."""
    color = VALUE_COLOR.get(reading["level"], "grey")
    mark = "" if reading["level"] == OK else f"{ICON[reading['level']]} "
    lines = [f"<font color='grey'>{reading['label']}</font>",
             f"{mark}**<font color='{color}'>{reading['text']}</font>**"]
    bar = _bar(reading.get("value"))
    if bar:
        lines.append(f"<font color='{color}'>{bar}</font>")
    if spark:
        lines.append(f"<font color='grey'>{spark}</font>")
    return "\n".join(lines)


def _grid(prefix: str, cells: list[str], per_row: int = 3) -> list[dict]:
    rows = []
    for i in range(0, len(cells), per_row):
        row = cells[i:i + per_row]
        row += [""] * (per_row - len(row))           # tiles keep one width per card
        rows.append({
            "tag": "column_set", "element_id": _eid(prefix, i), "flex_mode": "none",
            "horizontal_spacing": "default", "background_style": "default",
            "columns": [{"tag": "column", "width": "weighted", "weight": 1,
                         "vertical_align": "top",
                         "elements": [{"tag": "markdown", "content": c or " "}]}
                        for c in row]})
    return rows


SPARK = "▁▂▃▄▅▆▇█"
#: Which readings get a line on the chart, and in which order.
CHART_KEYS = ("cpu", "memory", "gpu", "gpu_memory", "swap", "disk")


def sparkline(values: list[float], cells: int = 24) -> str:
    """The last `cells` points as block characters, scaled 0–100 for shares."""
    points = [float(v) for v in values][-cells:]
    if len(points) < 2:
        return ""
    high = max(100.0, max(points))
    return "".join(SPARK[min(7, int(v / high * 7.999))] for v in points)


def trend_chart(eid: str, history: dict[str, list[tuple[str, float]]],
                labels: dict[str, str], *, title: str) -> dict | None:
    """A CardKit line chart of the shares over time (VChart spec), or None
    when fewer than three points were recorded."""
    values = []
    for key in CHART_KEYS:
        for slot, value in history.get(key) or []:
            values.append({"time": _local(slot), "value": round(float(value), 1),
                           "type": labels.get(key, key)})
    if len({v["time"] for v in values}) < 3:
        return None
    return {
        "tag": "chart", "element_id": eid, "aspect_ratio": "16:9",
        "chart_spec": {
            "type": "line",
            "title": {"text": title},
            "data": {"values": values},
            "xField": "time", "yField": "value", "seriesField": "type",
            "point": {"visible": False},
            "line": {"style": {"curveType": "monotone", "lineWidth": 2}},
            "axes": [{"orient": "left", "min": 0, "max": 100},
                     {"orient": "bottom", "label": {"autoHide": True}}],
            "legends": {"visible": True, "orient": "bottom"},
        },
    }


def device_card(view: dict, *, now: datetime, running: int | None = None,
                history: dict[str, list[tuple[str, float]]] | None = None) -> dict:
    """One device's 资源播报, drawn by the hub from its own readings — for a
    device that does not make its own rich card (Windows), in the same spirit:
    the state first, then every reading as a tile, then what to look at."""
    level = view.get("level") or UNCONFIRMED
    name = view.get("name") or view.get("device_ref") or "设备"
    local = now.astimezone(CN)
    readings = [r for r in view.get("readings") or []
                if not (r["key"] == "reach" and r["level"] == OK)]
    flagged = sorted((r for r in readings if r["level"] in (WARN, ALERT)),
                     key=lambda r: -RANK[r["level"]])
    if flagged:
        headline = "　".join(f"{r['label']} {r['text']}" for r in flagged[:3])
    elif level == UNCONFIRMED:
        headline = "这次没读到完整的读数"
    else:
        headline = "各项读数都在正常范围"
    elements: list[dict] = [_md(f"**{ICON[level]} {WORD[level]}**　{headline}")]
    history = history or {}
    elements += _grid(_eid("grid", view.get("device_ref")),
                      [_cell(r, sparkline([v for _s, v in history.get(r["key"]) or []]))
                       for r in readings])
    chart = trend_chart(_eid("trend", view.get("device_ref")), history,
                        {r["key"]: r["label"] for r in readings}, title="近 6 小时（%）")
    if chart is not None:
        elements.append(chart)
    facts = []
    if running:
        facts.append(f"🛠 {running} 个任务在跑")
    if view.get("observed_at"):
        facts.append(f"🕒 读数 {_local(view['observed_at'], with_date=True)}")
    if facts:
        elements.append(_md("<font color='grey'>" + "　·　".join(facts) + "</font>"))
    elements.append(_hr())
    elements.append(_md("<font color='grey'>只读，不会停止、重启或清理任何东西 · "
                        "发「状态」看所有设备 · 「任务」看在跑的</font>"))
    tags = [(WORD[level], TAG_COLOR[level])]
    if flagged:
        tags.append((f"{len(flagged)} 项要看", "neutral"))
    return _card(TEMPLATE[level], f"{name} · 资源播报",
                 f"{local:%m-%d} 周{WEEKDAYS[local.weekday()]} · {local:%H:%M} 更新（北京时间）",
                 elements, tags=tags, summary=f"{name} · {WORD[level]} · {headline}")


# -- 报警卡 -------------------------------------------------------------------

def _suggestion(episode: dict, spec: dict) -> str:
    """The next thing a person could do. Advice, never a button (R5)."""
    advice = spec["alert_card"]["suggestions"]
    key = episode.get("kind") if str(episode.get("kind", "")).startswith("run.") else \
        str(episode.get("key", "")).split(":")[0]
    return advice.get(key) or advice.get("default", "")


def alert_card(episode: dict, *, now: datetime, sample: bool = False,
               spec: dict = DEFAULT_SPEC) -> dict:
    """One episode. `state` is OPEN, RECOVERED or ESCALATED (superseded)."""
    text = spec["alert_card"]
    state = episode.get("state", "OPEN")
    level = episode["level"]
    subject = episode["subject"]
    # The title names the object; the state is the header colour and its tag.
    head = subject
    if state == "RECOVERED":
        template, tag = TEMPLATE[OK], ("已恢复", TAG_COLOR[OK])
    elif state == "ESCALATED":
        template, tag = TEMPLATE[UNCONFIRMED], ("已升级为报警", "neutral")
    else:
        template, tag = TEMPLATE[level], (WORD[level], TAG_COLOR[level])
    if sample:
        head += f" · {SAMPLE_TITLE}"
    end = episode.get("recovered_at") or now
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    subtitle = (f"开始 {_local(episode.get('first_seen'), with_date=True)} · "
                f"{'持续' if state == 'OPEN' else '历时'} {_minutes(episode.get('first_seen'), end)}")

    # Owner, 2026-11-09: say it in one or two short lines — what, and what to do
    # (or that it is over); no 影响 待确认 line and no footer.
    face = [episode["text"]]
    if state == "RECOVERED":
        face = [f"已恢复 · {_local(episode.get('recovered_at'), with_date=True)} 读数回到正常"]
    elif state == "ESCALATED":
        face.append(text["escalated_line"])
    else:
        face.append(f"<font color='grey'>{_suggestion(episode, spec)}</font>")

    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md("\n".join(face)))
    detail = [f"设备 {episode.get('device_ref') or '—'}",
              f"对象 {episode.get('key') or '—'}",
              f"首次观测 {_local(episode.get('first_seen'), with_date=True)}",
              f"最近观测 {_local(episode.get('last_seen'), with_date=True)}"]
    if episode.get("evidence"):
        detail.append(f"证据 {episode['evidence']}")
    elements.append(_panel(_eid("det", episode["episode"]), text["details_title"], detail,
                           expanded=False))
    if state == "OPEN" and not sample:
        from .feishu import button
        elements.append(button("这件事 1 小时内别再提醒", {"fleet": "mute", "subject": episode["episode"],
                                                     "minutes": 60}))
    return _card(template, head, subtitle, elements, tags=[tag],
                 summary=f"{tag[0]} · {subject}：{episode['text']}", width="compact")


# -- 任务完成卡 ---------------------------------------------------------------

def completion_card(runs: list[dict], *, now: datetime, sample: bool = False,
                    spec: dict = DEFAULT_SPEC) -> dict:
    text = spec["completion_card"]
    title = text["title"].format(icon=ICON[OK], count=len(runs))
    if sample:
        title += f" · {SAMPLE_TITLE}"
    shown = runs[:text["max_rows"]]
    lines = [f"- **{r.get('label') or r.get('project') or '一个任务'}**　"
             f"<font color='grey'>{r.get('device_name') or r.get('device_ref')}</font>"
             + (f"\n  ↳ {r['excerpt']}" if r.get("excerpt") else "")
             for r in shown]
    if len(runs) > len(shown):
        lines.append("- " + text["more"].format(count=len(runs) - len(shown)))
    elements = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md("\n".join(lines)))
    elements.append(_md(text["basis"]))
    return _card(TEMPLATE[OK], title, f"{_local(now, with_date=True)}（北京时间）", elements,
                 tags=[(f"{len(runs)} 个", "green")],
                 summary=f"任务完成 {len(runs)} 个：" + "、".join(
                     str(r.get("label") or r.get("project") or "任务") for r in runs[:3]))


OUTCOME_WORD = {"completed": "✅", "failed": "❌ 失败", "unknown": "⚪ 没读到结果", None: "⏳ 未结束"}


def batch_card(event: dict, *, now: datetime, runs: list[dict] | None = None,
               opened_at: str | None = None,
               device_names: dict[str, str] | None = None) -> dict:
    """The one message a dispatched batch produces, when every task has an outcome.

    Written for the owner reading it on a phone: the title says how it went,
    each task is its own name (the instruction's first line), where it ran and
    what it answered. No run ids, no tool names, no codes on the face.
    """
    runs = runs or []
    name, _, rest = (event.get("summary") or "").partition("：")
    counted = re.search(r"(\d+) 个任务", rest)
    total = len(runs) or (int(counted.group(1)) if counted else 0)
    failed = sum(1 for r in runs if r.get("outcome") == "failed")
    unread = sum(1 for r in runs if r.get("outcome") == "unknown")
    if not runs:
        failed = 1 if event.get("level") == "decide" else 0
    pending = sum(1 for r in runs if r.get("outcome") is None)
    if pending:
        template, verdict, tag = "blue", f"进行中 {len(runs) - pending}/{len(runs)}", ("进行中", "blue")
    elif failed:
        template, verdict, tag = TEMPLATE[ALERT], f"{failed} 个失败", ("有失败", "red")
    elif unread:
        template, verdict, tag = TEMPLATE[WARN], f"{unread} 个没读到结果", ("待确认", "orange")
    else:
        template, verdict, tag = TEMPLATE[OK], "全部完成", ("完成", "green")
    took = f" · 用时 {_minutes(opened_at, now)}" if opened_at else ""
    subtitle = f"{total} 个任务{took} · {_local(now, with_date=True)}"
    names = device_names or {}
    elements: list[dict] = []
    for i, run in enumerate(runs, 1):
        device = str(run.get("session_ref") or "").split("|", 1)[0]
        where = names.get(device, device.removeprefix("device:"))
        head = f"**{i}. {run.get('label') or '任务 ' + str(i)}**　{OUTCOME_WORD.get(run.get('outcome'), '')}"
        body = run.get("excerpt")
        if not body:
            body = {"unknown": "任务结束了，但读不到它的输出；可以让指挥端读一下会话再判断",
                    "failed": "执行出错，没有完成"}.get(run.get("outcome"), "没有输出")
        elements.append(_md(f"{head}\n<font color='grey'>{where}</font>\n{body}"))
    if not runs:
        elements.append(_md(rest or event.get("summary") or ""))
    return _card(template, f"{name} · {verdict}", subtitle, elements,
                 tags=[tag], summary=f"{name} · {verdict}", width="default")


def _revert_button(event: dict) -> list[dict]:
    from .feishu import button
    device, _, runtime = (event.get("evidence") or "").partition("|tune|")
    if not device.startswith("device:") or not runtime:
        return []
    return [button("撤销这次调整", {"fleet": "revert_tune", "device_ref": device,
                                    "runtime": runtime}, kind="danger")]


def lark_card(state: dict, *, now: datetime) -> dict:
    """lark-cli's own login lapsed: the bot still sends, user calls do not."""
    return _card(TEMPLATE[WARN], "飞书 CLI 需要重新登录", f"{_local(now, with_date=True)}（北京时间）",
                 [_md(f"bot：{state.get('bot')}　user：{state.get('user')}"),
                  _md("在东京执行 `lark-cli auth login`。在那之前：按钮回调、找你本人的 ID、"
                      "按名字找群都不可用；卡片照常发送。")],
                 tags=[("需要处理", "orange")], summary="飞书 CLI 需要重新登录", width="compact")


def tuning_card(event: dict, *, now: datetime) -> dict:
    """An agent changed a setting inside the owner's bounds: say what and why."""
    return _card(TEMPLATE[WARN], "自调整 · " + (event.get("summary") or "")[:40],
                 f"{_local(now, with_date=True)}（北京时间）",
                 [_md(event.get("summary") or ""),
                  _md("在你定的边界内；不需要批准。")]
                 + _revert_button(event),
                 tags=[("已生效", "blue")], summary=f"自调整：{event.get('summary') or ''}",
                 width="compact")


# -- 每日日报 -----------------------------------------------------------------

def daily_card(day: dict, *, now: datetime, sample: bool = False,
               spec: dict = DEFAULT_SPEC) -> dict:
    """One card per workday, from the day's ledger.

    `day` is what the notifier counted: `runs` {completed, failed, unknown,
    stalled}, `problems` [{subject, text, level}], `resources` [{subject, text,
    level, recovered}], `open_now` [{subject, text, level}]. The header colour is
    the worst thing still open at report time, not the worst thing that
    happened: a red that recovered at noon is a line in the report, not a red
    report.
    """
    text = spec["daily_report"]
    local = now.astimezone(CN)
    still_open = [row["level"] for row in day.get("open_now", [])]
    level = worst(still_open) if still_open else OK
    title = text["title"].format(date=f"{local:%m-%d}", weekday=WEEKDAYS[local.weekday()])
    if sample:
        title += f" · {SAMPLE_TITLE}"
    subtitle = text["subtitle"].format(time=f"{local:%H:%M}")
    rows_max = text["max_rows"]

    def listing(rows: list[dict]) -> str:
        if not rows:
            return text["empty"]
        lines = [f"- {ICON.get(r.get('level'), '')} {r['subject']}：{r['text']}".replace("-  ", "- ")
                 for r in rows[:rows_max]]
        if len(rows) > rows_max:
            lines.append(f"- 另有 {len(rows) - rows_max} 条")
        return "\n".join(lines)

    runs = day.get("runs", {})
    sections = {
        "runs": (f"✅ 完成 {runs.get('completed', 0)} · 🚨 失败 {runs.get('failed', 0)}"
                 f" · ⚪ 无法确认 {runs.get('unknown', 0)} · ⏳ 超时 {runs.get('stalled', 0)}"),
        "problems": listing(day.get("problems", [])),
        "resources": listing([{**r, "text": r["text"] + ("（已恢复）" if r.get("recovered") else "")}
                              for r in day.get("resources", [])]),
        "open_now": listing(day.get("open_now", [])),
    }
    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    for name in text["sections"]:
        elements.append(_md(f"**{text['headings'][name]}**\n{sections[name]}"))
    elements.append(_hr())
    elements.append(_md(text["footer"]))
    open_count = len(day.get("open_now", []))
    return _card(TEMPLATE[level], title, subtitle, elements,
                 tags=[("仍有未结束" if open_count else "全部结束", TAG_COLOR[level])],
                 summary=(f"日报：完成 {runs.get('completed', 0)} · 失败 {runs.get('failed', 0)}"
                          f" · 仍未结束 {open_count}"))


# -- spec proof ---------------------------------------------------------------

def render_samples(spec: dict) -> list[dict]:
    """Render one of every card with `spec`; raises if any is invalid."""
    now = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    reading = {"key": "fs:DATA", "label": "磁盘 DATA", "text": "93%", "level": ALERT}
    view = {"device_ref": "device:x", "name": "X", "level": ALERT,
            "observed_at": "2026-10-29T09:59:00Z", "readings": [reading]}
    episode = {"episode": "device:x|fs:DATA", "key": "fs:DATA", "kind": "node.pressure",
               "device_ref": "device:x", "subject": "X · 磁盘 DATA", "text": "93%",
               "level": ALERT, "first_seen": "2026-10-29T09:00:00Z",
               "last_seen": "2026-10-29T09:59:00Z"}
    day = {"runs": {"completed": 3, "failed": 1, "unknown": 0, "stalled": 0},
           "problems": [{"subject": "任务失败 · p", "text": "r-1（X）执行失败", "level": ALERT}],
           "resources": [{"subject": "X · 磁盘 DATA", "text": "93%", "level": ALERT, "recovered": True}],
           "open_now": []}
    return [
        resource_card([view], now=now, spec=spec),
        alert_card(episode, now=now, spec=spec),
        alert_card({**episode, "state": "RECOVERED", "recovered_at": "2026-10-29T10:00:00Z"},
                   now=now, spec=spec),
        alert_card({**episode, "state": "ESCALATED"}, now=now, spec=spec),
        completion_card([{"run_id": "r-1", "project": "p", "device_name": "X"}], now=now, spec=spec),
        daily_card(day, now=now, spec=spec),
    ]


def without_charts(card: dict) -> dict:
    """The same card with its chart elements taken out — what is sent when a
    client refuses a chart, so the answer still arrives."""
    import copy
    card = copy.deepcopy(card)
    elements = (card.get("body") or {}).get("elements")
    if isinstance(elements, list):
        card["body"]["elements"] = [e for e in elements
                                    if not (isinstance(e, dict) and e.get("tag") == "chart")]
    return card


def with_chart(card: dict, chart: dict | None) -> dict:
    """A device-made card with the hub's trend chart placed after its first line."""
    if chart is None:
        return card
    import copy
    card = copy.deepcopy(card)
    elements = (card.get("body") or {}).get("elements")
    if not isinstance(elements, list):
        return card
    if any(isinstance(e, dict) and e.get("element_id") == chart["element_id"] for e in elements):
        return card
    elements.insert(min(1, len(elements)), chart)
    return card
