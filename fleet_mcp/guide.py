"""The commander Skills, served over MCP (`fleet_guide`).

ChatGPT, Claude and the coding agents load the Fleet Skills as files. Chat
clients that only speak MCP — Grok, Gemini, Muse — cannot: all they see is the
server's `instructions` and the tool descriptions. So the same `SKILL.md` files
the plugins ship are readable here, one at a time and on demand (the index is a
line per topic; a topic's text only when asked) — one source, two ways in.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "distribution" / "mcp" / "skills"
MAX_BYTES = 16 * 1024
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def skills_dir() -> Path:
    return Path(os.environ.get("SERVEROS_FLEET_SKILLS") or DEFAULT_DIR)


def _split(text: str) -> tuple[dict[str, str], str]:
    match = _FRONT.match(text)
    if not match:
        return {}, text
    meta = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        if value:
            meta[key.strip()] = value.strip()
    return meta, text[match.end():]


def index(root: Path | None = None) -> list[dict[str, str]]:
    root = root or skills_dir()
    out = []
    for path in sorted(root.glob("*/SKILL.md")):
        meta, _ = _split(path.read_text(encoding="utf-8"))
        out.append({"topic": path.parent.name, "description": meta.get("description", "")[:300]})
    return out


def read(topic: str, root: Path | None = None) -> dict[str, Any] | None:
    if not _TOPIC.match(topic or ""):
        return None
    path = (root or skills_dir()) / topic / "SKILL.md"
    try:
        _, body = _split(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    data = body.strip().encode()
    return {"topic": topic, "text": data[:MAX_BYTES].decode("utf-8", "ignore"),
            "truncated": len(data) > MAX_BYTES}
