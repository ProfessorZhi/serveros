"""Fleet's chain latencies, from the journal's own pages.

    python3 integrations/fleet-hub-watch/fleet_hub_watch/perf.py <fleet-journal dir> [--days N]

Owner, 2026-11-13: «效率和性能最重要 … 看看性能有没有提升». Every run and batch
page in `runs/` carries the trace's timeline; this reads those lines and gives
the P50 / P95 / max of each hop, so a change can be measured against the same
yardstick before and after. Read-only; stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

_LINE = re.compile(r"(\d\d:\d\d:\d\d)\s+(\w+)\s+(.*?)\s+\[[^\]]+\]\s+([0-9a-f-]{12})")

#: (mark, the step names that set it, first or last occurrence)
MARKS = {
    "sent": ("prompt sent",),
    "watch": ("watching",),
    "observed": ("observed",),
    "finished": ("device finished",),
    "result": ("result",),
    "card": ("no card: told on the batch card", "queued for the 任务完成 card",
             "its own card updated"),
}
HOPS = (
    ("派发 → Hub 开始盯", "sent", "watch"),
    ("派发 → 第一次读到状态", "sent", "observed"),
    ("设备做完 → Hub 取回结果", "finished", "result"),
    ("取回结果 → 卡片处理", "result", "card"),
    ("派发 → 取回结果（端到端，含任务本身）", "sent", "result"),
)


def marks_of(page: str) -> dict[str, dict[str, datetime]]:
    """{run id prefix: {mark: first time}} from one page's timeline."""
    timeline = page.split("时间线", 1)
    runs: dict[str, dict[str, datetime]] = {}
    if len(timeline) < 2:
        return runs
    for line in timeline[1].splitlines():
        found = _LINE.match(line)
        if not found:
            continue
        at = datetime.strptime(found[1], "%H:%M:%S")
        step, run = found[3].strip(), found[4]
        seen = runs.setdefault(run, {})
        for mark, names in MARKS.items():
            if mark not in seen and any(step.startswith(n) for n in names):
                seen[mark] = at
    return runs


def hops(pages: list[str]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {name: [] for name, _a, _b in HOPS}
    for page in pages:
        for seen in marks_of(page).values():
            for name, a, b in HOPS:
                if a in seen and b in seen:
                    delta = (seen[b] - seen[a]).total_seconds()
                    out[name].append(delta + 86400 if delta < 0 else delta)   # past midnight
    return out


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def report(journal: Path, days: int | None = None) -> str:
    pages = []
    since = (datetime.now() - timedelta(days=days)).strftime("%Y/%m/%d") if days else None
    for path in sorted((journal / "runs").glob("*/*/*/*.md")):
        if path.name == "INDEX.md":
            continue
        if since and "/".join(path.parts[-4:-1]) < since:
            continue
        pages.append(path.read_text(encoding="utf-8"))
    lines = [f"{len(pages)} 页", "", "| 环节 | n | P50 | P95 | 最大 |", "|---|---|---|---|---|"]
    for name, values in hops(pages).items():
        if values:
            lines.append(f"| {name} | {len(values)} | {quantile(values, .5):.0f} s | "
                         f"{quantile(values, .95):.0f} s | {max(values):.0f} s |")
        else:
            lines.append(f"| {name} | 0 | — | — | — |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("journal", type=Path)
    parser.add_argument("--days", type=int, default=None)
    args = parser.parse_args(argv)
    print(report(args.journal, args.days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
