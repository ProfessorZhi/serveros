"""The three levels a person sees, and the one word that is not a level.

    OK     正常   green   ✅
    WARN   注意   yellow  ⚠️   worth a look; nothing has broken yet
    ALERT  报警   red     🚨   something is broken or about to be

`UNCONFIRMED` (无法确认, grey) is not a fourth level. It is what a status card
says when the reading is missing or stale: an unmeasured value is never shown as
green and never as an alarm (CARDS-STYLE R7).

Feishu header templates are from the CardKit 2.0 title component reference
(blue, wathet, turquoise, green, yellow, orange, red, carmine, violet, purple,
indigo, grey, default).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

OK, WARN, ALERT, UNCONFIRMED = "OK", "WARN", "ALERT", "UNCONFIRMED"
LEVELS = (OK, WARN, ALERT)
RANK = {OK: 0, UNCONFIRMED: 1, WARN: 2, ALERT: 3}

WORD = {OK: "正常", WARN: "注意", ALERT: "报警", UNCONFIRMED: "无法确认"}
ICON = {OK: "✅", WARN: "⚠️", ALERT: "🚨", UNCONFIRMED: "⚪"}
TEMPLATE = {OK: "green", WARN: "yellow", ALERT: "red", UNCONFIRMED: "grey"}


def worst(levels) -> str:
    found = [level for level in levels if level in RANK]
    return max(found, key=RANK.__getitem__) if found else OK


def private_file(path: str | Path) -> Path:
    """Create `path` owner-only if absent; refuse a symlink or a widened one."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"refusing a symlinked state file: {path}")
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"state file is readable by others: {path}")
    return path
