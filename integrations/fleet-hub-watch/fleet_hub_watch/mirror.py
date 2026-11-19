"""One-way mirrors of Git-side pages into Feishu docs (A-4 board, R-4 space map).

Git is canonical; Feishu is a published copy (records CLAUDE.md: 飞书 = 单向发布).
Each mirror is a local markdown file the fabric already writes — the global
requirement board (`state/serveros/requirement-board/current.md`), the space
map in records — and one Feishu doc that holds its latest text:

* the first time, the doc is created in the configured folder and its token is
  kept in the hub's state (never guessed again);
* afterwards, only when the file's bytes changed, and at most every
  `MIN_GAP` seconds, the doc is overwritten whole (`docs +update --command
  overwrite`) — the page says on top that it is generated and where its source
  lives, so nobody edits the copy.

Nothing is read back from Feishu into Git. A failed write is retried on the
next tick; three in a row and the hub's heartbeat says so.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .cards import CN

MIN_GAP = 600
Runner = Callable[[list[str], Path], subprocess.CompletedProcess]


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    from .feishu import _cli
    last = None
    for cli in _cli():
        try:
            return subprocess.run([cli, *argv], cwd=str(cwd), text=True, capture_output=True,
                                  timeout=120)
        except OSError as exc:
            last = exc
    raise RuntimeError(f"lark-cli not found ({type(last).__name__})")


def parse_spec(spec: str) -> tuple[str, str, Path]:
    """`name:title=path` → (name, title, path)."""
    left, _, path = spec.partition("=")
    name, _, title = left.partition(":")
    if not name or not path:
        raise ValueError(f"--mirror wants name:title=path, got {spec!r}")
    return name, title or name, Path(path).expanduser()


def page(title: str, source: Path, text: str, now: datetime) -> str:
    """What lands in Feishu: a header that says it is a copy, then the file. PURE."""
    local = now.astimezone(CN)
    return (f"> 自动生成 · {local:%Y-%m-%d %H:%M}（北京时间）· 源：Git `{source.name}`。"
            f"**这是只读副本，改这里不会回到 Git**；要改去源头或在 Fleet 里说。\n\n{text}")


class DocMirror:
    def __init__(self, specs: list[str], folder: str | None, state_path: Path, *,
                 run: Runner = _run, clock: Callable[[], float] | None = None) -> None:
        self.mirrors = [parse_spec(s) for s in specs]
        self.folder = folder
        self.state_path = Path(state_path)
        self.run = run
        import time
        self.clock = clock or time.time

    def _state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False)
        os.replace(tmp, self.state_path)

    def tick(self, now: datetime) -> dict[str, str] | None:
        if not self.mirrors:
            return None
        state, done = self._state(), {}
        for name, title, source in self.mirrors:
            mine = state.setdefault(name, {})
            try:
                text = source.read_text(encoding="utf-8")
            except OSError:
                mine["state"] = "SOURCE_MISSING"
                continue
            digest = hashlib.sha256(text.encode()).hexdigest()[:16]
            if digest == mine.get("sha") or self.clock() - mine.get("at", 0) < MIN_GAP:
                continue
            body = page(title, source, text, now)
            workdir = self.state_path.parent / "mirror"
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / f"{name}.md").write_text(body, encoding="utf-8")
            content = f"@./{name}.md"
            if mine.get("doc"):
                argv = ["docs", "+update", "--doc", mine["doc"], "--command", "overwrite",
                        "--doc-format", "markdown", "--content", content]
            else:
                argv = ["docs", "+create", "--doc-format", "markdown", "--title", title,
                        "--content", content]
                if self.folder:
                    argv += ["--parent-token", self.folder]
            try:
                answer = self.run(argv, workdir)
                ok = answer.returncode == 0
                doc = mine.get("doc") or _document(answer.stdout)
            except Exception as exc:  # noqa: BLE001 - retried next tick
                ok, doc = False, None
                mine["error"] = type(exc).__name__
            if ok and doc:
                mine.update(doc=doc, sha=digest, at=self.clock(), state="PUBLISHED",
                            failures=0, error=None)
                done[name] = "CREATED" if "+create" in argv else "UPDATED"
            else:
                mine["failures"] = int(mine.get("failures") or 0) + 1
                mine["state"] = "FAILING"
                mine["at"] = self.clock()          # wait MIN_GAP before trying again
                done[name] = "FAILED"
        self._save(state)
        return done or None

    def health(self) -> dict[str, Any]:
        return {name: {k: v for k, v in mine.items() if k in ("state", "failures", "doc")}
                for name, mine in self._state().items()}


def _document(stdout: str) -> str | None:
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return None
    doc = ((data.get("data") or {}).get("document") or {})
    return doc.get("url") or doc.get("document_id")
