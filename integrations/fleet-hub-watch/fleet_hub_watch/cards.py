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


def _card(template: str, title: str, subtitle: str, elements: list[dict]) -> dict:
    card = {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": title},
                   "subtitle": {"tag": "plain_text", "content": subtitle}},
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
    return _card(TEMPLATE[level], title, subtitle, elements)


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
    if state == "RECOVERED":
        template, head = TEMPLATE[OK], f"{ICON[OK]} 已恢复 · {subject}"
    elif state == "ESCALATED":
        template, head = TEMPLATE[UNCONFIRMED], f"⬆️ 已升级为报警 · {subject}"
    else:
        template, head = TEMPLATE[level], f"{ICON[level]} {WORD[level]} · {subject}"
    if sample:
        head += f" · {SAMPLE_TITLE}"
    end = episode.get("recovered_at") or now
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))
    subtitle = (f"开始 {_local(episode.get('first_seen'), with_date=True)} · "
                f"{'持续' if state == 'OPEN' else '历时'} {_minutes(episode.get('first_seen'), end)}")

    face = [f"**{text['phenomenon']}** {episode['text']}"]
    if state == "RECOVERED":
        face.append(f"**{text['recovered']}** {_local(episode.get('recovered_at'), with_date=True)} 读数回到正常")
    elif state == "ESCALATED":
        face.append(f"**状态** {text['escalated_line']}")
    else:
        face.append(f"**{text['impact']}** {episode.get('impact') or text['impact_default']}")
        face.append(f"**{text['advice']}** {_suggestion(episode, spec)}")

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
    elements.append(_md(text["footer"]))
    return _card(template, head, subtitle, elements)


# -- 任务完成卡 ---------------------------------------------------------------

def completion_card(runs: list[dict], *, now: datetime, sample: bool = False,
                    spec: dict = DEFAULT_SPEC) -> dict:
    text = spec["completion_card"]
    title = text["title"].format(icon=ICON[OK], count=len(runs))
    if sample:
        title += f" · {SAMPLE_TITLE}"
    shown = runs[:text["max_rows"]]
    lines = [f"- {r.get('project') or '未分配项目'} · {r['run_id']}（{r.get('device_name') or r.get('device_ref')}）"
             for r in shown]
    if len(runs) > len(shown):
        lines.append("- " + text["more"].format(count=len(runs) - len(shown)))
    elements = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md("\n".join(lines)))
    elements.append(_md(text["basis"]))
    return _card(TEMPLATE[OK], title, f"{_local(now, with_date=True)}（北京时间）", elements)


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
    return _card(TEMPLATE[level], title, subtitle, elements)


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
