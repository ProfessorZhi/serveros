"""The daily directory check (R-1), kept in the journal and summed up weekly.

Each device runs `ws.py check --record` once a day (a user timer on Linux, a
scheduled task on Windows). The record travels with the device's versions
record (`fleet_device_versions`), so the hub reads all three in one call it
already makes. This module:

* once a day (China time, from `DAILY_AT`), writes `workspace/<yyyy>/<mm>/<dd>.md`
  in the journal and appends one line per device to `workspace/history.jsonl`;
* once a week (Monday, from `DAILY_AT`), sends one summary card: per device, what
  is left to migrate or reclaim, how that moved over the week, and what the
  layout does not know yet.

It reads and reports. Nothing here moves or deletes anything: that needs a plan
the owner approved, applied on the device itself (R-2).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .cards import CN

DAILY_AT = 9          # hour, China time: after the devices' 07:40 checks
WORK = ("MOVE_REPO", "MOVE_WORKTREE", "MOVE_DIR")
RECLAIM = ("REMOVE_CLONE", "REMOVE_WORKTREE", "REMOVE_DIR")
#: How old a device's own check may be before the page says it is stale.
STALE = timedelta(hours=36)


def rows_from(versions: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per device from a `fleet_device_versions` answer. PURE."""
    rows = []
    for device in versions.get("devices") or []:
        check = device.get("workspace") if isinstance(device.get("workspace"), dict) else None
        state = (check or {}).get("state") or (
            "UNREACHABLE" if device.get("state") == "UNREACHABLE" else "NOT_REPORTED")
        counts = (check or {}).get("counts") or {}
        rows.append({
            "device_ref": device.get("device_ref"),
            "name": device.get("display_name") or device.get("device_ref"),
            "state": state,
            "at": (check or {}).get("at"),
            "plan_id": (check or {}).get("plan_id"),
            "migrate": sum(int(counts.get(k) or 0) for k in WORK),
            "reclaim": sum(int(counts.get(k) or 0) for k in RECLAIM),
            "deferred": (check or {}).get("in_use_deferred") or 0,
            "unregistered": (check or {}).get("unregistered") or 0,
            "unregistered_names": (check or {}).get("unregistered_names") or [],
        })
    return rows


#: A device whose key sync is older than this, or failed, is told (S-3).
SECRETS_STALE = timedelta(hours=36)


def secrets_problems(versions: dict[str, Any], now: datetime) -> list[str]:
    """One line per device whose key-center sync has stopped or failed. Names
    and times only — the record never holds a value. PURE."""
    out = []
    for device in versions.get("devices") or []:
        name = device.get("display_name") or device.get("device_ref")
        secrets = device.get("secrets") if isinstance(device.get("secrets"), dict) else None
        if device.get("state") != "RECORDED" or secrets is None:
            continue                       # no record: the directory page already says so
        if secrets.get("state") != "SYNCED":
            out.append(f"{name}：密钥从没同步过（{secrets.get('state')}）")
        elif int(secrets.get("errors") or 0):
            out.append(f"{name}：最近一次同步有 {secrets['errors']} 个错误")
        elif _stale(secrets.get("synced_at"), now, SECRETS_STALE):
            out.append(f"{name}：上次同步在 {secrets.get('synced_at')}，超过 36 小时")
    return out


def _stale(at: str | None, now: datetime, limit: timedelta | None = None) -> bool:
    try:
        when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - when > (limit or STALE)


def page(rows: list[dict[str, Any]], now: datetime) -> str:
    """The day's journal page. PURE."""
    local = now.astimezone(CN)
    lines = [f"# {local:%Y-%m-%d} 目录检查（R-1，只读）", ""]
    for row in rows:
        if row["state"] != "RECORDED":
            why = {"NOT_RECORDED": "设备上还没跑过每日检查",
                   "NOT_REPORTED": "设备的连接器还不带检查结果（需要更新）",
                   "UNREACHABLE": "这次没连上设备"}.get(row["state"], row["state"])
            lines.append(f"- **{row['name']}**　无结果：{why}")
            continue
        stale = "（结果超过 36 小时，定时任务可能没跑）" if _stale(row["at"], now) else ""
        lines.append(f"- **{row['name']}**　待搬 {row['migrate']}　可回收 {row['reclaim']}　"
                     f"在用暂缓 {row['deferred']}　未登记 {row['unregistered']}{stale}")
        lines.append(f"  - plan_id `{row['plan_id']}`，检查于 {row['at']}")
        for name in row["unregistered_names"][:10]:
            lines.append(f"  - 未登记：`{name}`")
    lines += ["", "搬迁和回收要主人批准 plan_id 后在设备上执行（R-2）；这里只记录。", ""]
    return "\n".join(lines)


def weekly_lines(history: list[dict[str, Any]], now: datetime) -> tuple[list[str], str]:
    """The card's lines and its colour: this week's last reading per device,
    against the last one at least seven days older. PURE."""
    by_device: dict[str, list[dict]] = {}
    for line in history:
        by_device.setdefault(line.get("device_ref") or "?", []).append(line)
    out, template = [], "green"
    for ref, lines in sorted(by_device.items(), key=lambda kv: kv[1][-1].get("name") or kv[0]):
        last = lines[-1]
        if last.get("state") != "RECORDED":
            out.append(f"**{last.get('name')}**　本周没有检查结果")
            template = "yellow"
            continue
        week_ago = now - timedelta(days=7)
        before = next((x for x in reversed(lines)
                       if x.get("state") == "RECORDED" and _day(x) <= week_ago), None)

        def moved(key: str) -> str:
            if before is None:
                return ""
            delta = int(last.get(key) or 0) - int(before.get(key) or 0)
            return f"（{'+' if delta > 0 else ''}{delta}）" if delta else "（持平）"
        out.append(f"**{last.get('name')}**　待搬 {last.get('migrate')}{moved('migrate')}　"
                   f"可回收 {last.get('reclaim')}{moved('reclaim')}　"
                   f"未登记 {last.get('unregistered')}{moved('unregistered')}")
        if int(last.get("unregistered") or 0) or int(last.get("migrate") or 0):
            template = "yellow" if template == "green" else template
    if not out:
        out.append("这一周没有任何设备的目录检查结果。")
        template = "grey"
    out.append("")
    out.append("只读汇总。要搬或回收：挑一台设备的 plan_id 给我批准，再在那台设备上执行。")
    return out, template


def _day(line: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(line.get("noted_at")).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=CN)


class WorkspaceReport:
    """Called every tick; does its work once a day and once a week."""

    def __init__(self, root: Path, versions: Callable[[], dict[str, Any]], *,
                 send: Callable[[str, dict, datetime], bool] | None = None,
                 card: Callable[..., dict] | None = None,
                 state_path: Path | None = None) -> None:
        self.root = Path(root)
        #: Kept out of the journal's commits (the journal passes a path in .git).
        self.state_path = state_path or self.root / ".journal-state" / "fleet-workspace.json"
        self.versions = versions
        self.send = send
        self.card = card

    @property
    def _dir(self) -> Path:
        return self.root / "workspace"

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def history(self) -> list[dict]:
        try:
            text = (self._dir / "history.jsonl").read_text(encoding="utf-8")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def tick(self, now: datetime) -> dict[str, Any] | None:
        local = now.astimezone(CN)
        if local.hour < DAILY_AT:
            return None
        state = self._state()
        day = f"{local:%Y-%m-%d}"
        done: dict[str, Any] = {}
        if state.get("day") != day:
            versions = self.versions()
            rows = rows_from(versions)
            problems = secrets_problems(versions, now)
            path = self._dir / f"{local:%Y}" / f"{local:%m}" / f"{local:%d}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            text = page(rows, now)
            text += "\n## 密钥同步（S-3）\n\n" + ("\n".join(f"- {x}" for x in problems)
                                                if problems else "- 三台都在 36 小时内同步过，没有错误") + "\n"
            path.write_text(text, encoding="utf-8")
            if problems and self.send and self.card:
                self.send(f"keycenter:{day}", self.card(
                    "密钥同步有问题", problems + ["", "在那台设备上：`serveros-secret doctor`，"
                                              "再 `serveros-secret sync`。只看名字，不看值。"],
                    now=now, template="orange"), now)
                done["secrets"] = len(problems)
            with (self._dir / "history.jsonl").open("a", encoding="utf-8") as handle:
                for row in rows:
                    slim = {k: v for k, v in row.items() if k != "unregistered_names"}
                    handle.write(json.dumps({**slim, "noted_at": now.isoformat(timespec="seconds")},
                                            ensure_ascii=False) + "\n")
            state["day"] = day
            done["daily"] = str(path.relative_to(self.root))
        week = f"{local:%G-W%V}"
        if local.weekday() == 0 and state.get("week") != week and self.send and self.card:
            lines, template = weekly_lines(self.history(), now)
            sent = self.send(f"workspace-week:{week}",
                             self.card(f"目录治理周报 {week}", lines, now=now, template=template), now)
            if sent:
                state["week"] = week
            done["weekly"] = "SENT" if sent else "HELD"
        if done:
            self._save(state)
        return done or None
