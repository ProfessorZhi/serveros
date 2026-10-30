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

CN = ZoneInfo("Asia/Shanghai")
SAMPLE_TITLE = "[示例]"
SAMPLE_LINE = "⚠️ 示例数据 · 非实时采集"
READ_ONLY = "只读 · 未自动停止、重启或清理任何东西"
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


def resource_card(views: list[dict], *, now: datetime, sample: bool = False) -> dict:
    level = overall(views)
    counts = {lv: sum(1 for v in views if v["level"] == lv) for lv in (ALERT, WARN, OK, UNCONFIRMED)}
    local = now.astimezone(CN)
    title = f"{ICON[level]} Fleet 资源播报 · {WORD[level]}"
    if sample:
        title += f" · {SAMPLE_TITLE}"
    subtitle = f"{local:%m-%d} 周{WEEKDAYS[local.weekday()]} · {local:%H:%M} 更新（北京时间）"

    elements: list[dict] = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md(
        f"**{len(views)} 台设备** · 🚨 报警 {counts[ALERT]} · ⚠️ 注意 {counts[WARN]}"
        f" · ✅ 正常 {counts[OK]} · ⚪ 无法确认 {counts[UNCONFIRMED]}"))

    attention = sorted(
        ((v, r) for v in views for r in v["readings"] if r["level"] in (WARN, ALERT)),
        key=lambda pair: -RANK[pair[1]["level"]])
    if attention:
        lines = ["**需要关注**"] + [
            f"- {ICON[r['level']]} {v['name']} · {r['label']}：{r['text']}" for v, r in attention]
        elements.append(_md("\n".join(lines)))

    elements.append(_hr())
    for view in views:
        # One reading per line: a phone wraps a joined line in the middle of a
        # number. A healthy connection is not a finding, so it is not a line (R1).
        lines = [_reading_text(r) for r in view["readings"]
                 if not (r["key"] == "reach" and r["level"] == OK)]
        if view.get("observed_at"):
            lines.append(f"读数时间 {_local(view['observed_at'], with_date=True)}")
        elements.append(_panel(
            _eid("dev", view["device_ref"]),
            f"{ICON[view['level']]} {view['name']} · {WORD[view['level']]}",
            [line for line in lines if line],
            expanded=view["level"] in (WARN, ALERT)))
    elements.append(_hr())
    elements.append(_md(f"{READ_ONLY} · 工作日 08:00–20:00 每 30 分钟原位更新，出现报警立即更新"))
    return _card(TEMPLATE[level], title, subtitle, elements)


# -- 报警卡 -------------------------------------------------------------------

#: The next thing a person could do, by what went wrong. Advice, never a button
#: (R5): nothing here acts.
SUGGESTION = {
    "fs": "看该盘最近增长的目录，清理或扩容；确认没有任务在持续写日志",
    "memory": "看占用最高的进程与会话；确认是否有任务泄漏或并发过多",
    "swap": "Swap 是水位：结合「内存压力」判断是否真的在换页",
    "psi": "内存压力在升高：减少并发任务，或查看占用最高的会话",
    "svc": "查看该服务的 systemd 日志；确认是否需要人工重启",
    "reach": "确认设备在线、Tailscale/隧道连通、Fleet 节点连接器在运行",
    "run.failed": "用 fleet_read_session 查看该会话最后几轮，决定重试或修复",
    "run.unknown": "结果没能确认：读会话和产物判断是否完成，不要盲目重发",
    "run.stalled": "确认任务是否仍在推进；长时间无进展可考虑中断",
}


def _suggestion(episode: dict) -> str:
    key = episode.get("kind") if str(episode.get("kind", "")).startswith("run.") else \
        str(episode.get("key", "")).split(":")[0]
    return SUGGESTION.get(key, "查看详情中的证据，确认是否需要处理")


def alert_card(episode: dict, *, now: datetime, sample: bool = False) -> dict:
    """One episode. `state` is OPEN, RECOVERED or ESCALATED (superseded)."""
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

    face = [f"**现象** {episode['text']}"]
    if state == "RECOVERED":
        face.append(f"**恢复** {_local(episode.get('recovered_at'), with_date=True)} 读数回到正常")
    elif state == "ESCALATED":
        face.append("**状态** 已升级为报警，后续请看新的报警卡")
    else:
        face.append(f"**影响** {episode.get('impact') or '待确认'}")
        face.append(f"**建议** {_suggestion(episode)}")

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
    elements.append(_panel(_eid("det", episode["episode"]), "技术详情", detail, expanded=False))
    elements.append(_md(READ_ONLY))
    return _card(template, head, subtitle, elements)


# -- 任务完成卡 ---------------------------------------------------------------

def completion_card(runs: list[dict], *, now: datetime, sample: bool = False) -> dict:
    title = f"{ICON[OK]} 任务完成 · {len(runs)} 个"
    if sample:
        title += f" · {SAMPLE_TITLE}"
    shown = runs[:20]
    lines = [f"- {r.get('project') or '未分配项目'} · {r['run_id']}（{r.get('device_name') or r.get('device_ref')}）"
             for r in shown]
    if len(runs) > len(shown):
        lines.append(f"- 另有 {len(runs) - len(shown)} 个，见 fleet_inbox")
    elements = []
    if sample:
        elements.append(_md(SAMPLE_LINE))
    elements.append(_md("\n".join(lines)))
    elements.append(_md("判定依据：读到了 agent 的实际输出 · 详细结果用 fleet_read_session 查看"))
    return _card(TEMPLATE[OK], title, f"{_local(now, with_date=True)}（北京时间）", elements)
