"""The broadcast spec: what the cards say and when they go out.

Facts stay in code — readings, levels, which run failed, which episode is
open. Presentation lives here — titles, section headings, the advice line,
which daily-report sections appear and in what order, the workday window and
the report time. The owner changes presentation by editing one YAML file
(`card-spec.yaml`, maintained through the `fleet-broadcast` skill), not code.

A spec is merged onto `DEFAULT_SPEC` and then proven: every card is rendered
from sample data and passed through the CardKit validator. A spec that fails
is refused by name, and the watcher keeps the last good one — a typo in a
heading must not stop the monitor.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

SCHEMA = "serveros/fleet-broadcast-spec.v1"

DEFAULT_SPEC: dict[str, Any] = {
    "schema": SCHEMA,
    "schedule": {
        # ISO weekdays, 1 = Monday. CN public holidays are not known here.
        "workdays": [1, 2, 3, 4, 5],
        "window": "08:00-20:00",
        "slot_minutes": 30,
        "completion_batch_seconds": 300,
        "daily_report_at": "18:00",
    },
    "resource_card": {
        "title": "{icon} Fleet 资源播报 · {word}",
        "subtitle": "{date} 周{weekday} · {time} 更新（北京时间）",
        "summary": "**{devices} 台设备** · 🚨 报警 {alert} · ⚠️ 注意 {warn} · ✅ 正常 {ok} · ⚪ 无法确认 {unknown}",
        "attention_heading": "需要关注",
        "reading_time": "读数时间 {time}",
        "footer": "只读 · 未自动停止、重启或清理任何东西 · 工作日 08:00–20:00 每 30 分钟原位更新，出现报警立即更新",
    },
    "alert_card": {
        "phenomenon": "现象",
        "impact": "影响",
        "impact_default": "待确认",
        "advice": "建议",
        "recovered": "恢复",
        "escalated_line": "已升级为报警，后续请看新的报警卡",
        "details_title": "技术详情",
        "footer": "只读 · 未自动停止、重启或清理任何东西",
        "suggestions": {
            "fs": "看该盘最近增长的目录，清理或扩容；确认没有任务在持续写日志",
            "memory": "看占用最高的进程与会话；确认是否有任务泄漏或并发过多",
            "swap": "Swap 是水位：结合「内存压力」判断是否真的在换页",
            "psi": "内存压力在升高：减少并发任务，或查看占用最高的会话",
            "svc": "查看该服务的 systemd 日志；确认是否需要人工重启",
            "reach": "确认设备在线、Tailscale/隧道连通、Fleet 节点连接器在运行",
            "run.failed": "用 fleet_read_session 查看该会话最后几轮，决定重试或修复",
            "run.unknown": "结果没能确认：读会话和产物判断是否完成，不要盲目重发",
            "run.stalled": "确认任务是否仍在推进；长时间无进展可考虑中断",
            "default": "查看详情中的证据，确认是否需要处理",
        },
    },
    "completion_card": {
        "title": "{icon} 任务完成 · {count} 个",
        "max_rows": 20,
        "more": "另有 {count} 个，见 fleet_inbox",
        "basis": "判定依据：读到了 agent 的实际输出 · 详细结果用 fleet_read_session 查看",
    },
    "daily_report": {
        "enabled": True,
        "title": "📋 Fleet 日报 · {date} 周{weekday}",
        "subtitle": "{time} 生成（北京时间） · 统计当天 00:00 起",
        "sections": ["runs", "problems", "resources", "open_now"],
        "headings": {
            "runs": "今日任务",
            "problems": "需要看一眼的任务",
            "resources": "今日资源报警",
            "open_now": "到现在仍未结束",
        },
        "empty": "无",
        "max_rows": 15,
        "footer": "数字由程序统计 · 只读，未自动处理任何事项",
    },
}

SECTIONS = ("runs", "problems", "resources", "open_now")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class SpecError(ValueError):
    """A spec this watcher will not use, with the reason."""


def _merge(base: dict, override: dict, where: str) -> dict:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key not in base:
            raise SpecError(f"unknown key {where}{key}")
        if isinstance(base[key], dict) and key != "suggestions":
            if not isinstance(value, dict):
                raise SpecError(f"{where}{key} must be a mapping")
            merged[key] = _merge(base[key], value, f"{where}{key}.")
        elif isinstance(base[key], dict):
            if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
                raise SpecError(f"{where}{key} must map names to text")
            merged[key] = {**base[key], **value}
        else:
            if type(value) is not type(base[key]) and not (
                    isinstance(base[key], list) and isinstance(value, list)):
                raise SpecError(f"{where}{key} must be {type(base[key]).__name__}")
            merged[key] = value
    return merged


def minutes(text: str) -> int:
    match = _TIME.match(text)
    if not match:
        raise SpecError(f"time {text!r} must be HH:MM")
    return int(match.group(1)) * 60 + int(match.group(2))


def _check_schedule(schedule: dict) -> None:
    if not schedule["workdays"] or not all(isinstance(d, int) and 1 <= d <= 7
                                           for d in schedule["workdays"]):
        raise SpecError("schedule.workdays must list ISO weekdays 1-7")
    start, _, end = schedule["window"].partition("-")
    if minutes(start) >= minutes(end):
        raise SpecError("schedule.window must be START-END with START before END")
    minutes(schedule["daily_report_at"])
    if not 5 <= schedule["slot_minutes"] <= 240:
        raise SpecError("schedule.slot_minutes must be 5-240")
    if not 0 <= schedule["completion_batch_seconds"] <= 3600:
        raise SpecError("schedule.completion_batch_seconds must be 0-3600")


def _check_daily(daily: dict) -> None:
    unknown = [s for s in daily["sections"] if s not in SECTIONS]
    if unknown:
        raise SpecError(f"daily_report.sections has unknown {unknown}; known: {list(SECTIONS)}")
    if len(set(daily["sections"])) != len(daily["sections"]):
        raise SpecError("daily_report.sections repeats a section")


def build(override: dict | None) -> dict:
    """Merge `override` onto the default and prove every card still renders."""
    override = dict(override or {})
    schema = override.pop("schema", SCHEMA)
    if schema != SCHEMA:
        raise SpecError(f"schema must be {SCHEMA}")
    spec = _merge(DEFAULT_SPEC, override, "")
    _check_schedule(spec["schedule"])
    _check_daily(spec["daily_report"])
    from . import cards  # a render proves the format strings and the size limit
    try:
        cards.render_samples(spec)
    except SpecError:
        raise
    except Exception as exc:  # noqa: BLE001 - any render failure refuses the spec, by name
        raise SpecError(f"a card does not render with this spec: {type(exc).__name__}: {exc}") from None
    return spec


def load(path: str | Path | None) -> dict:
    """The spec at `path`, merged and proven; the default when `path` is None."""
    if path is None:
        return copy.deepcopy(DEFAULT_SPEC)
    import yaml

    try:
        document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from None
    if not isinstance(document, dict):
        raise SpecError(f"{path} must be a mapping")
    return build(document)
