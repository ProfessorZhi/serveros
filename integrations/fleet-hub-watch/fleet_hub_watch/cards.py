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
import json
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


def is_styled(card: dict) -> bool:
    """Whether the card uses anything `plain` would take out."""
    blob = json.dumps(card, ensure_ascii=False)
    return any(mark in blob for mark in ('"column_set"', '"chart"', "<text_tag", '"text_size"'))


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
    said = " · ".join(f"{ICON[lv]} {WORD[lv]} {counts[lv]}"
                     for lv in (ALERT, WARN, OK, UNCONFIRMED) if counts[lv])
    elements.append(_md(text["summary"].format(
        devices=len(views), alert=counts[ALERT], warn=counts[WARN], ok=counts[OK],
        unknown=counts[UNCONFIRMED], counts=said)))

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
        lines = [_reading_text(r) for r in shown(view["readings"])]
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
#: Owner, 2026-11-10: a card where the healthy numbers are large and green
#: «shouts» everywhere; only a problem carries a colour.
VALUE_COLOR = {OK: "default", WARN: "orange", ALERT: "red", UNCONFIRMED: "grey"}


def _colored(text: str, color: str) -> str:
    return text if color == "default" else f"<font color='{color}'>{text}</font>"


def number_of(reading: dict) -> str:
    """The bare number of a reading: '88% / 16 GiB' -> '88%'."""
    if reading.get("short"):
        return str(reading["short"])
    text = re.sub(r"（[^）]*）", "", str(reading.get("text") or ""))
    return text.split(" / ")[0].split(" · ")[0].strip()


def say(reading: dict) -> str:
    """One reading as a person would say it: 「内存用了 87%」."""
    key, label, text = str(reading.get("key") or ""), reading.get("label") or "", str(reading.get("text") or "")
    gap = " " if label and label[-1].isascii() else ""      # 「Swap 用了」, 「内存用了」
    if key in ("memory", "swap"):
        return f"{label}{gap}用了 {number_of(reading)}"
    if key == "psi":
        return f"{label}（{reading['short']} 的时间在等内存）" if reading.get("short") else f"{label}，{text}"
    if key.startswith("fs:"):
        _, _, left = text.partition(" · 余 ")
        return f"{label}{gap}用了 {number_of(reading)}" + (f"，只剩 {left}" if left else "")
    return f"{label} {text}".strip()


def shown(readings: list[dict]) -> list[dict]:
    """The readings worth a tile or a line: a healthy connection and a healthy
    「内存吃紧」 are not findings."""
    return [r for r in readings if not (r.get("key") in ("reach", "psi") and r.get("level") == OK)]
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
             f"{mark}**{_colored(reading['text'], color)}**"]
    bar = _bar(reading.get("value"))
    if bar:
        lines.append(_colored(bar, "grey" if color == "default" else color))
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
CHART_COLORS = ["#3370FF", "#F5A623", "#00B2A9", "#7F3BF5", "#8F959E", "#F54A45"]
#: Which readings get a line on the chart, and in which order.
CHART_KEYS = ("cpu", "memory", "gpu", "gpu_memory", "swap", "disk")


def sparkline(values: list[float], cells: int = 24) -> str:
    """The last `cells` points as block characters, scaled 0–100 for shares."""
    points = [float(v) for v in values][-cells:]
    if len(points) < 2:
        return ""
    high = max(100.0, max(points))
    return "".join(SPARK[min(7, int(v / high * 7.999))] for v in points)


#: Points on a curve. Owner, 2026-11-11: 72 five-minute points made a wall of
#: vertical time labels; six hours read fine as thirteen half-hour points.
CHART_BUCKET_MINUTES = 30
CHART_MAX_POINTS = 13


def _bucketed(points: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Half-hour means of (slot, value) points, labelled HH:MM Beijing time."""
    sums: dict[datetime, list[float]] = {}
    for slot, value in points:
        try:
            at = datetime.fromisoformat(str(slot).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        at = at.replace(minute=at.minute - at.minute % CHART_BUCKET_MINUTES, second=0, microsecond=0)
        sums.setdefault(at, []).append(float(value))
    return [(_local(at), round(sum(v) / len(v), 1)) for at, v in sorted(sums.items())]


def line_chart(eid: str, series: list[tuple[str, list[tuple[str, float]]]], *,
               title: str) -> dict | None:
    """A compact CardKit line chart (VChart spec) of shares over time, one line
    per (name, points), or None when fewer than three points were recorded."""
    # A short history is drawn as it is; a long one as half-hour means.
    dense = len({slot for _, points in series for slot, _ in points}) > CHART_MAX_POINTS
    values = [{"time": time, "value": value, "type": name}
              for name, points in series
              for time, value in (_bucketed(points) if dense else
                                  [(_local(slot), round(float(v), 1)) for slot, v in points])]
    if len({v["time"] for v in values}) < 3:
        return None
    return {
        "tag": "chart", "element_id": eid[:20], "aspect_ratio": "2:1",
        "chart_spec": {
            "type": "line",
            "title": {"text": title},
            "data": {"values": values},
            "xField": "time", "yField": "value", "seriesField": "type",
            "point": {"visible": False},
            "label": {"visible": False},
            "line": {"style": {"curveType": "monotone", "lineWidth": 2}},
            "axes": [{"orient": "left", "min": 0, "max": 100, "tick": {"tickCount": 3}},
                     {"orient": "bottom", "label": {"autoRotate": False, "autoHide": True,
                                                    "autoLimit": True}}],
            "legends": {"visible": True, "orient": "bottom"},
            # Far apart on purpose: two blues could not be told apart.
            "color": CHART_COLORS,
        },
    }


def trend_chart(eid: str, history: dict[str, list[tuple[str, float]]],
                labels: dict[str, str], *, title: str, every: bool = False) -> dict | None:
    """One device's readings over time, a line per reading. `every` draws each
    key in `history` (a question about disks draws the disks), not only the
    usual ones."""
    keys = [key for key in CHART_KEYS if history.get(key)]
    if every:
        keys += sorted(key for key in history if key not in keys and history[key])
    return line_chart(eid, [(labels.get(key, key), history[key]) for key in keys], title=title)


def _reading_tile(reading: dict) -> list[dict]:
    color = VALUE_COLOR.get(reading["level"], "grey")
    text = str(reading["text"])
    value, _, note = text.partition(" / ")
    if not note:
        value, _, note = text.partition(" · ")
    if reading.get("short"):                    # the text is a sentence: number large, rest small
        value, note = str(reading["short"]), text.replace(str(reading["short"]), "").replace("  ", " ").strip()
    tile = _tile(reading["label"], value, color, note)
    bar = _bar(reading.get("value"))
    if bar:
        tile.append(_text(_colored(bar, "grey" if color == "default" else color), size="notation"))
    return tile


def device_card(view: dict, *, now: datetime, running: int | None = None,
                history: dict[str, list[tuple[str, float]]] | None = None,
                focus: str | None = None) -> dict:
    """One device's 资源播报, drawn by the hub: one sentence, every reading as a
    tile in a grey box, the six-hour curve, and one grey caption.

    The state is said once — the header's colour and its tag. Only what is
    wrong is coloured; a healthy number is plain."""
    level = view.get("level") or UNCONFIRMED
    name = view.get("name") or view.get("device_ref") or "设备"
    local = now.astimezone(CN)
    readings = shown(view.get("readings") or [])
    flagged = sorted((r for r in readings if r["level"] in (WARN, ALERT)),
                     key=lambda r: -RANK[r["level"]])
    if flagged:
        plain_head = "；".join(say(r) for r in flagged[:3])
        headline = "；".join(_colored(say(r), VALUE_COLOR[r["level"]]) for r in flagged[:3])
    elif level == UNCONFIRMED:
        plain_head = headline = "这次没读到完整的读数"
    else:
        plain_head = headline = "各项读数都在正常范围"
    elements: list[dict] = [_text(f"**{headline}**")]
    # What was asked about is the first tile.
    tiles = [_reading_tile(r) for r in sorted(readings, key=lambda r: not _is_focus(r["key"], focus))]
    for i in range(0, len(tiles), 3):
        row = tiles[i:i + 3]
        row += [[]] * (3 - len(row))
        elements.append(_box(_eid("tile", view.get("device_ref"), i), row))
    history = history or {}
    if focus:               # only the curve that was asked about
        history = {key: points for key, points in history.items() if _is_focus(key, focus)}
    chart = trend_chart(_eid("trend", view.get("device_ref")), history,
                        {r["key"]: r["label"] for r in readings}, every=bool(focus),
                        title=(f"{FOCUS[focus][1]} · " if focus else "") + "近 6 小时（%）")
    if chart is not None:
        elements.append(chart)
    facts = []
    if running:
        facts.append(f"{running} 个任务在跑")
    if view.get("observed_at"):
        facts.append(f"读数 {_local(view['observed_at'], with_date=True)}")
    facts.append("只读，不会动任何东西")
    elements.append(_caption(" · ".join(facts)))
    return _card(TEMPLATE[level], f"{name} · 资源",
                 f"{local:%m-%d} 周{WEEKDAYS[local.weekday()]} {local:%H:%M}",
                 elements, tags=[(WORD[level], TAG_COLOR[level])],
                 summary=f"{name} · {WORD[level]} · {plain_head}")


# -- 所有设备的资源卡 --------------------------------------------------------

#: The readings a device's line lists, in this order, when they are healthy.
ROW_KEYS = ("cpu", "memory", "swap")


#: A question's subject -> (reading keys it means, the chart's title word).
FOCUS = {"cpu": (("cpu",), "CPU"), "memory": (("memory",), "内存"), "swap": (("swap",), "交换区"),
         "disk": (("fs:",), "磁盘"), "gpu": (("gpu",), "显存")}


def _is_focus(key: str, focus: str | None) -> bool:
    return bool(focus) and any(key == k or (k.endswith(":") and key.startswith(k))
                               or key.startswith(k + ":") for k in FOCUS[focus][0])


def _focus_points(history: dict, focus: str) -> list[tuple[str, float]]:
    """One device's curve for the asked reading; for disks, the fullest one."""
    series = [points for key, points in (history or {}).items() if _is_focus(key, focus) and points]
    return max(series, key=lambda points: points[-1][1]) if series else []


def fleet_resource_card(views: list[dict], *, now: datetime,
                        running: dict[str, int] | None = None,
                        histories: dict[str, dict] | None = None,
                        focus: str | None = None) -> dict:
    """「资源」 with no device named: every device on ONE card.

    Owner, 2026-11-11 («多卡片 很丑陋 很大»): one large card per device, each
    with its own tall chart, filled the screen. Here a device is one grey row —
    what is wrong as a coloured sentence, every other reading in one grey line —
    and there is one small chart: memory, a line per device. A single device's
    full card and curves are 「资源 东京」."""
    order = {ALERT: 0, WARN: 1, UNCONFIRMED: 2, OK: 3}
    level = worst(v.get("level") for v in views) if views else UNCONFIRMED
    flagged_devices = sum(1 for v in views if v.get("level") in (ALERT, WARN))
    head = f"{flagged_devices} 台要看" if flagged_devices else "全部正常"
    elements: list[dict] = []
    for view in sorted(views, key=lambda v: order.get(v.get("level"), 4)):
        name = view.get("name") or view.get("device_ref")
        readings = shown(view.get("readings") or [])
        bad = sorted((r for r in readings if r.get("level") in (WARN, ALERT)),
                     key=lambda r: order.get(r["level"], 4))
        listed = [r for r in readings if r.get("level") == OK
                  and (r["key"] in ROW_KEYS or r["key"].startswith(("fs:", "gpu:")))]
        # What was asked about leads the line, in plain ink rather than grey.
        listed.sort(key=lambda r: not _is_focus(r["key"], focus))
        asked = [f"{r['label']} {number_of(r)}" for r in listed if _is_focus(r["key"], focus)]
        rest = [f"{r['label']} {number_of(r)}" for r in listed if not _is_focus(r["key"], focus)]
        n = (running or {}).get(view.get("device_ref"), 0)
        if n:
            rest.append(f"{n} 个任务在跑")
        if bad:
            said = "；".join(_colored(say(r), VALUE_COLOR[r["level"]]) for r in bad[:3])
            rows = [_text(f"**{name}**　{said}")]
        elif (view.get("level") or UNCONFIRMED) == UNCONFIRMED:
            why = next((r["text"] for r in view.get("readings") or []
                        if r.get("level") == UNCONFIRMED), "这次没读到读数")
            rows = [_text(f"**{name}**　<font color='grey'>{why}</font>")]
        else:
            rows = [_text(f"**{name}**　<font color='grey'>正常</font>")]
        if asked:
            rows.append(_text(" · ".join(asked)))
        if rest:
            rows.append(_caption(" · ".join(rest)))
        elements.append(_box(_eid("dev", view.get("device_ref")), [rows]))
    if not views:
        elements.append(_text("还没有设备读数"))
    # The curve is of what was asked: «CPU 怎么样» is not answered with memory.
    drawn = focus or "memory"
    chart = line_chart(_eid("fleet" + drawn, len(views)),
                       [(v.get("name") or v.get("device_ref"),
                         _focus_points((histories or {}).get(v.get("device_ref")) or {}, drawn))
                        for v in views], title=f"{FOCUS[drawn][1]} · 近 6 小时（%）")
    if chart is not None:
        elements.append(chart)
    elements.append(_caption("「资源 东京」看单台的全部读数和曲线 · 只读，不会动任何东西"))
    return _card(TEMPLATE[level], f"Fleet 资源 · {len(views)} 台",
                 _local(now, with_date=True), elements,
                 tags=[(head, TAG_COLOR[level] if flagged_devices else "green")],
                 summary=f"Fleet 资源 · {head}")


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

OUTCOME_TAG = {"completed": ("完成", "green"), "failed": ("失败", "red"),
               "unknown": ("结果不明", "orange"), None: ("还在跑", "blue")}
#: What is said when a task left no text of its own.
NO_OUTPUT = {"unknown": "任务结束了，但读不到它的输出；可以让指挥端读一下会话再判断",
             "failed": "执行出错，没有完成", "completed": "没返回文字", None: "还没结束"}


def _task_box(i: int, run: dict, where: str, outcome: str | None, *,
              show_label: bool = True) -> dict:
    """One task in a grey box: where it ran and its state, the task in grey
    (unless the card already said it once), then what the device answered."""
    word, color = OUTCOME_TAG.get(outcome, OUTCOME_TAG[None])
    rows = [_text(f"**{where}**　{_itag(word, color)}")]
    if show_label:
        rows.append(_caption(run.get("label") or run.get("project") or f"任务 {i}"))
    if run.get("excerpt"):
        rows.append(_text(run["excerpt"]))
    else:
        rows.append(_caption(NO_OUTPUT.get(outcome, NO_OUTPUT[None])))
    return _box(_eid("task", run.get("session_ref"), run.get("run_id"), i), [rows])


def _shared_label(runs: list[dict]) -> str | None:
    """The task every run was given, when it is the same one."""
    labels = {str(r.get("label") or r.get("project") or "") for r in runs}
    return labels.pop() if len(runs) > 1 and len(labels) == 1 and "" not in labels else None


def completion_card(runs: list[dict], *, now: datetime, sample: bool = False,
                    spec: dict = DEFAULT_SPEC) -> dict:
    text = spec["completion_card"]
    title = text["title"].format(icon=ICON[OK], count=len(runs))
    if sample:
        title += f" · {SAMPLE_TITLE}"
    shown_runs = runs[:text["max_rows"]]
    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    same = _shared_label(shown_runs)
    if same:
        elements.append(_text(f"<font color='grey'>任务</font>　{same}"))
    for i, run in enumerate(shown_runs, 1):
        elements.append(_task_box(i, run, run.get("device_name") or run.get("device_ref") or "",
                                  "completed", show_label=not same))
    if len(runs) > len(shown_runs):
        elements.append(_caption(text["more"].format(count=len(runs) - len(shown_runs))))
    elements.append(_caption("要看全文：问指挥端，或在飞书发「追踪 任务id」"))
    return _card(TEMPLATE[OK], title, f"{_local(now, with_date=True)}（北京时间）", elements,
                 summary=f"任务完成 {len(runs)} 个：" + "、".join(
                     str(r.get("label") or r.get("project") or "任务") for r in runs[:3]))


#: Worst first: what a reader has to act on is the first row.
OUTCOME_ORDER = {"failed": 0, "unknown": 1, None: 2, "completed": 3}


def batch_card(event: dict, *, now: datetime, runs: list[dict] | None = None,
               opened_at: str | None = None,
               device_names: dict[str, str] | None = None) -> dict:
    """The one message a dispatched batch produces, when every task has an outcome.

    The title is the batch and how it went (the header is the result); that
    is said once, not again in a tag or a body line. A task every device was given is written once, above the rows;
    the rows are worst first. No run ids, no tool names, no codes on the face.
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
    tags: list[tuple[str, str]] = []
    if pending:
        template = "blue"
        tags.append((f"进行中 {len(runs) - pending}/{len(runs)}", "blue"))
    if failed:
        tags.append((f"{failed} 个失败", "red"))
    if unread:
        tags.append((f"{unread} 个结果不明", "orange"))
    if not pending:
        template = TEMPLATE[ALERT] if failed else TEMPLATE[WARN] if unread else TEMPLATE[OK]
    if not tags:
        tags.append(("全部完成", "green"))
    verdict = " · ".join(word for word, _ in tags)
    took = f" · 用时 {_minutes(opened_at, now)}" if opened_at else ""
    subtitle = f"{total} 个任务{took} · {_local(now, with_date=True)}"
    names = device_names or {}
    elements: list[dict] = []
    same = _shared_label(runs)
    if same:
        elements.append(_text(f"<font color='grey'>任务</font>　{same}"))
    ordered = sorted(enumerate(runs, 1), key=lambda pair: OUTCOME_ORDER.get(pair[1].get("outcome"), 2))
    for i, run in ordered:
        device = str(run.get("session_ref") or "").split("|", 1)[0]
        where = names.get(device, device.removeprefix("device:"))
        elements.append(_task_box(i, run, where, run.get("outcome"), show_label=not same))
    if not runs:
        elements.append(_md(rest or event.get("summary") or ""))
    return _card(template, f"{name} · {verdict}", subtitle, elements,
                 summary=f"{name} · {verdict}", width="default")


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

def _dedupe(rows: list[dict]) -> list[dict]:
    """The same subject and text once, with how many times it happened."""
    out: dict[tuple, dict] = {}
    for row in rows:
        key = (row.get("subject"), row.get("text"))
        if key in out:
            out[key]["times"] += 1
        else:
            out[key] = {**row, "times": 1}
    return list(out.values())


def _short(text: str, limit: int = 36) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def daily_card(day: dict, *, now: datetime, sample: bool = False,
               spec: dict = DEFAULT_SPEC) -> dict:
    """One card per workday, from the day's ledger.

    `day` is what the notifier counted: `runs` {completed, failed, unknown,
    stalled}, `problems` [{subject, text, level}], `resources` [{subject, text,
    level, recovered}], `open_now` [{subject, text, level}]. The header colour is
    the worst thing still open at report time, not the worst thing that
    happened: a red that recovered at noon is a line in the report, not a red
    report.

    Owner, 2026-11-10 («日报烂完了»): one sentence first; the counts as tiles;
    the same problem once with ×N; what recovered folded away.
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
    runs = {k: int(day.get("runs", {}).get(k, 0) or 0)
            for k in ("completed", "failed", "unknown", "stalled")}
    total = sum(runs.values())
    resources = day.get("resources", [])
    recovered = [r for r in resources if r.get("recovered")]
    unrecovered = [r for r in resources if not r.get("recovered")]
    # One list of what to look at: what is open now, then the day's task
    # problems, then resources that have not come back — each thing once.
    listed = list(day.get("problems", [])) + unrecovered
    told = {(r.get("subject"), r.get("text")) for r in listed}
    look = _dedupe(listed + [r for r in day.get("open_now", [])
                             if (r.get("subject"), r.get("text")) not in told])
    look.sort(key=lambda r: -RANK.get(r.get("level"), 0))
    to_look = sum(r["times"] for r in look)

    def line(r: dict) -> str:
        times = f"　×{r['times']}" if r.get("times", 1) > 1 else ""
        return (f"- {ICON.get(r.get('level'), '')} {_short(r['subject'])}"
                f"<font color='grey'>　{r['text']}</font>{times}")

    def listing(rows: list[dict]) -> str:
        lines = [line(r) for r in rows[:rows_max]]
        if len(rows) > rows_max:
            lines.append(f"- 另有 {len(rows) - rows_max} 条")
        return "\n".join(lines)

    # The sentence a reader gets if they read nothing else. Only what happened
    # is counted: a kind of outcome with none is not mentioned.
    if total:
        parts = [f"{runs[k]} 个{word}" for k, word in
                 (("completed", "完成"), ("failed", "失败"), ("unknown", "结果不明"),
                  ("stalled", "跑了很久")) if runs[k]]
        said = f"今天 {total} 个任务：" + "，".join(parts)
    else:
        said = "今天没有派发任务"
    if resources:
        said += f"。资源波动 {len(resources)} 次，" + (
            "都已恢复" if not unrecovered else f"{len(unrecovered)} 次还没恢复")
    headline = f"**{said}**"

    look_block = ([_text(f"**{text['headings']['open_now']}**\n{listing(look)}")] if look
                  else [_caption("没有要看的")])
    blocks: dict[str, list[dict]] = {"runs": [], "problems": [], "open_now": [], "resources": []}
    # 「现在仍要看」 and 「需要看一眼的任务」 were two lists of the same things:
    # they are one list, placed where the first of the two sections is.
    first = next((name for name in text["sections"] if name in ("open_now", "problems")), None)
    if first:
        blocks[first] = look_block
    if recovered:
        blocks["resources"].append(_panel(
            _eid("dayrec", local.date()), f"已恢复的资源波动 · {len(recovered)} 次",
            [line({**r, "text": r["text"] + "（已恢复）"}) for r in recovered[:rows_max]],
            expanded=False))
    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_text(headline))
    for name in text["sections"]:
        elements += blocks.get(name, [])
    elements.append(_caption(text["footer"]))
    tag = (f"{to_look} 项要看", TAG_COLOR[level] if still_open else "orange") if to_look \
        else ("没有要看的", "green")
    return _card(TEMPLATE[level], title, subtitle, elements, tags=[tag],
                 summary=f"日报：{said.removeprefix('今天 ')}" + (f" · {to_look} 项要看" if to_look else ""))


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


def with_words(card: dict, words: str) -> dict:
    """The card with the butler's sentences as its first lines. The sentences
    are a model's reading of the facts; the rest of the card is the facts."""
    words = (words or "").strip()
    if not words:
        return card
    import copy
    card = copy.deepcopy(card)
    elements = card.setdefault("body", {}).setdefault("elements", [])
    elements.insert(0, {"tag": "markdown", "content": words})
    summary = card.get("config", {}).get("summary")
    if isinstance(summary, dict):
        summary["content"] = words.replace("\n", " ")[:120]
    return card


def card_text(card: dict) -> str:
    """What a card says, as plain text — for the journal, so the owner can read
    later exactly what was sent. Markup is dropped; a chart is named, not drawn."""
    def clean(text: str) -> str:
        text = re.sub(r"<text_tag color='[^']*'>(.*?)</text_tag>", r"【\1】", text or "")
        text = re.sub(r"</?font[^>]*>|</?at[^>]*>", "", text)
        return text.replace("**", "").strip()

    out: list[str] = []

    def walk(elements: list) -> None:
        for e in elements or []:
            if not isinstance(e, dict):
                continue
            tag = e.get("tag")
            if tag == "markdown" and e.get("content", "").strip():
                out.append(clean(e["content"]))
            elif tag == "column_set":
                parts = []
                for column in e.get("columns") or []:
                    inner: list[str] = []
                    for x in column.get("elements") or []:
                        if isinstance(x, dict) and x.get("tag") == "markdown" and x.get("content", "").strip():
                            inner.append(clean(x["content"]))
                    if inner:
                        parts.append(" ".join(inner))
                if parts:
                    out.append(" ｜ ".join(parts))
            elif tag == "collapsible_panel":
                title = clean(((e.get("header") or {}).get("title") or {}).get("content", ""))
                out.append(f"▸ {title}（折叠）")
                walk(e.get("elements") or [])
            elif tag == "chart":
                spec = e.get("chart_spec") or {}
                series = list(dict.fromkeys(v.get("type") for v in (spec.get("data") or {}).get("values") or []))
                out.append(f"[曲线：{(spec.get('title') or {}).get('text', '')}；{'、'.join(map(str, series))}]")
            elif tag == "button":
                out.append(f"[按钮：{((e.get('text') or {}).get('content') or '')}]")
            elif tag == "hr":
                out.append("—")
    header = card.get("header") or {}
    tags = "".join(f"【{(t.get('text') or {}).get('content', '')}】" for t in header.get("text_tag_list") or [])
    head = [f"{((header.get('title') or {}).get('content') or '').strip()} {tags}".strip(),
            ((header.get("subtitle") or {}).get("content") or "").strip()]
    walk((card.get("body") or {}).get("elements") or [])
    return "\n".join(x for x in head + out if x)


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
