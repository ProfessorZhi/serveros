"""The fleet's deep memory: OpenViking on the hub, read for recall (V1).

Two layers of memory, one surface (`fleet_memory search`):

* **notes** — the inbox's short confirmed findings and rules (`EventInbox.remember`);
* **archive** — everything the hub captured into OpenViking (Feishu chats, the
  journal, records understanding and requirements, run results), searched by
  meaning with the local embedder.

This module is the archive half's client and nothing more: one `find` against
the loopback server, a short timeout, and answers trimmed to what fits a
prompt. A server that is down, slow or unconfigured gives `[]` — recall never
stops a dispatch or a search.

The key is read from `OPENVIKING_ROOT_API_KEY`, or else from that one line of
`~/.secrets/vault/openviking.env`; it is sent only to the loopback address and
never logged, returned or put in an error.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

KEY_VAR = "OPENVIKING_ROOT_API_KEY"
DEFAULT_URL = "http://127.0.0.1:1933"
VAULT = Path.home() / ".secrets" / "vault" / "openviking.env"
TIMEOUT = 1.5
#: Below this the hit is noise for a prompt (bge-small cosine).
MIN_SCORE = 0.45


def _key(env: dict[str, str] | None = None, vault: Path = VAULT) -> str | None:
    env = os.environ if env is None else env
    if env.get(KEY_VAR):
        return env[KEY_VAR]
    try:
        for line in vault.read_text(encoding="utf-8").splitlines():
            name, _, value = line.strip().partition("=")
            if name.strip() == KEY_VAR and value.strip():
                return value.strip().strip("'\"")
    except OSError:
        return None
    return None


def _loopback(url: str) -> bool:
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


class Archive:
    def __init__(self, url: str | None = None, *, key: Callable[[], str | None] = _key,
                 opener: Callable[..., Any] = urllib.request.urlopen,
                 timeout: float = TIMEOUT) -> None:
        self.url = (url or os.environ.get("SERVEROS_OPENVIKING_URL") or DEFAULT_URL).rstrip("/")
        self.key, self.opener, self.timeout = key, opener, timeout

    def find(self, query: str, *, limit: int = 5, chars: int = 300) -> list[dict[str, Any]]:
        """The closest captured passages, as {uri, score, text}. [] on any trouble."""
        if not query or not query.strip() or not _loopback(self.url):
            return []
        key = self.key()
        if not key:
            return []
        body = json.dumps({"query": query[:500], "limit": max(1, min(int(limit), 20))}).encode()
        request = urllib.request.Request(f"{self.url}/api/v1/search/find", data=body, method="POST",
                                         headers={"Content-Type": "application/json",
                                                  "X-API-Key": key})
        try:
            with self.opener(request, timeout=self.timeout) as answer:
                doc = json.loads(answer.read().decode("utf-8", "replace"))
        except (OSError, ValueError, urllib.error.URLError):
            return []
        return hits(doc, limit=limit, chars=chars)


def hits(doc: Any, *, limit: int = 5, chars: int = 300) -> list[dict[str, Any]]:
    """OpenViking's find answer → trimmed hits. Tolerates the shapes 0.4.x and
    later return (`result.{resources,memories}` lists, or a flat list). PURE."""
    result = doc.get("result", doc) if isinstance(doc, dict) else doc
    items: list[Any] = []
    if isinstance(result, dict):
        for key in ("memories", "resources", "skills", "items", "results"):
            if isinstance(result.get(key), list):
                items.extend(result[key])
    elif isinstance(result, list):
        items = result
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        text = (item.get("abstract") or item.get("overview") or item.get("content")
                or item.get("text") or "")
        if score < MIN_SCORE or not str(text).strip():
            continue
        text = " ".join(str(text).split())
        out.append({"uri": str(item.get("uri") or "")[:200], "score": round(score, 3),
                    "text": text[:chars] + ("…" if len(text) > chars else "")})
    out.sort(key=lambda h: -h["score"])
    return out[:limit]
