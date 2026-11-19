#!/usr/bin/env python3
"""Capture the hub's material into OpenViking (Layer C), broadly, once a day.

    python3 capture.py --dir journal=~/workspace/fleet-journal \
        --dir records-understanding=~/workspace/records/information/understanding \
        --dir records-projects=~/workspace/records/projects \
        --feishu ~/workspace/records/information/raw/feishu/chats \
        [--state ~/state/serveros/openviking/capture.json] [--dry-run]

Owner, 2026-11-19: capture as much as possible, few restrictions. What it takes:

* every markdown/text page under each `--dir` (the Fleet journal — tasks, results,
  cards, the butler's exchanges; records understanding, requirements, reports);
* every Feishu chat under `--feishu`, rendered to one markdown page per chat per
  month (time · sender · text) from the already-redacted raw messages.

What it never takes: anything under a hidden directory, `.env`/key/db-shaped
files, files over 2 MB, and any line that matches a high-precision credential
pattern (dropped, counted, never printed). Other accounts' homes are never
named here — every path is one the hub's own user passes.

Each source is uploaded as one zip snapshot to `viking://resources/fleet/<name>`
only when its contents changed since the last run. Semantic processing (the
abstracts and overviews) uses the VLM configured in `ov.conf` — the production
gateway's small model; embeddings stay local.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fleet_mcp.archive_memory import DEFAULT_URL, _key, _loopback  # noqa: E402

TEXT = {".md", ".markdown", ".txt"}
MAX_FILE = 2 * 1024 * 1024
MAX_ZIP = 60 * 1024 * 1024
BAD_NAME = re.compile(r"(^|/)(\.env.*|.*\.(pem|key|p12|db|sqlite3?)|id_rsa.*|.*credential.*)$", re.I)
SECRET = re.compile(
    r"(?<![A-Za-z0-9_\-])(sk-[A-Za-z0-9_\-]{24,}|(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.)"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._\-]{32,}")


def clean(text: str) -> tuple[str, int]:
    """Drop every line that looks like a credential. PURE."""
    kept, dropped = [], 0
    for line in text.splitlines():
        if SECRET.search(line):
            dropped += 1
        else:
            kept.append(line)
    return "\n".join(kept) + ("\n" if kept else ""), dropped


def pages_of_dir(root: Path) -> dict[str, str]:
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if (not path.is_file() or path.is_symlink() or path.suffix.lower() not in TEXT
                or any(part.startswith(".") for part in rel.split("/"))
                or BAD_NAME.search(rel) or path.stat().st_size > MAX_FILE):
            continue
        out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def _text(record: dict) -> str:
    content = record.get("content") or record.get("text") or ""
    if isinstance(content, (dict, list)):
        content = json.dumps(content, ensure_ascii=False)
    return " ".join(str(content).split())


def pages_of_chats(root: Path) -> dict[str, str]:
    """`<kind>/<label>/<chat_id>/messages.jsonl` → `<kind>/<label>/<yyyy-mm>.md`. PURE of I/O
    beyond reading."""
    months: dict[str, list[str]] = {}
    for messages in sorted(root.glob("*/*/*/messages.jsonl")):
        kind, label = messages.parts[-4], messages.parts[-3]
        for line in messages.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            when = str(record.get("create_time") or "")[:16]
            sender = (record.get("sender") or {}) if isinstance(record.get("sender"), dict) else {}
            who = sender.get("name") or sender.get("id") or record.get("sender_name") or "?"
            body = _text(record)
            if not body:
                continue
            months.setdefault(f"{kind}/{label}/{when[:7] or 'undated'}.md", []).append(
                f"- {when} · {who}：{body[:2000]}")
    return {name: f"# 飞书 · {name[:-3]}\n\n" + "\n".join(lines) + "\n"
            for name, lines in months.items()}


def snapshot(pages: dict[str, str]) -> tuple[bytes, str, int]:
    """One zip of the cleaned pages, its digest, and the dropped-line count. PURE."""
    buffer, dropped, digest = io.BytesIO(), 0, hashlib.sha256()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(pages):
            text, gone = clean(pages[name])
            dropped += gone
            if not text.strip():
                continue
            digest.update(name.encode() + b"\0" + text.encode())
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            archive.writestr(info, text)
    return buffer.getvalue(), digest.hexdigest()[:16], dropped


def _multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: application/zip\r\n\r\n").encode()
    return body + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def upload(url: str, key: str, name: str, data: bytes, opener=urllib.request.urlopen) -> dict:
    body, kind = _multipart("file", f"{name}.zip", data)
    request = urllib.request.Request(f"{url}/api/v1/resources/temp_upload", data=body,
                                     method="POST", headers={"Content-Type": kind, "X-API-Key": key})
    with opener(request, timeout=300) as answer:
        temp = (json.loads(answer.read()).get("result") or {}).get("temp_file_id")
    if not temp:
        raise RuntimeError("temp_upload returned no temp_file_id")
    payload = json.dumps({"temp_file_id": temp, "to": f"viking://resources/fleet/{name}",
                          "reason": f"Fleet hub capture: {name}"}).encode()
    request = urllib.request.Request(f"{url}/api/v1/resources", data=payload, method="POST",
                                     headers={"Content-Type": "application/json", "X-API-Key": key})
    with opener(request, timeout=300) as answer:
        result = json.loads(answer.read()).get("result") or {}
    return {k: result.get(k) for k in ("task_id", "root_uri", "status") if result.get(k)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--feishu", type=Path)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--state", type=Path,
                    default=Path.home() / "state" / "serveros" / "openviking" / "capture.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not _loopback(args.url):
        print("refused: OpenViking is only reached on loopback", file=sys.stderr)
        return 2
    sources: dict[str, dict[str, str]] = {}
    for spec in args.dir:
        name, _, path = spec.partition("=")
        root = Path(path).expanduser()
        if not name or not root.is_dir():
            print(f"skipped {spec!r}: not a directory", file=sys.stderr)
            continue
        sources[name] = pages_of_dir(root)
    if args.feishu:
        root = args.feishu.expanduser()
        if root.is_dir():
            sources["feishu-chats"] = pages_of_chats(root)
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    key = None if args.dry_run else _key()
    if not args.dry_run and not key:
        print("refused: OPENVIKING_ROOT_API_KEY is not available (vault not read)", file=sys.stderr)
        return 2
    report, failed = {}, 0
    for name, pages in sources.items():
        data, digest, dropped = snapshot(pages)
        line = {"pages": len(pages), "bytes": len(data), "dropped_lines": dropped, "sha": digest}
        if len(data) > MAX_ZIP:
            line["result"] = "TOO_LARGE"
            failed += 1
        elif state.get(name, {}).get("sha") == digest:
            line["result"] = "UNCHANGED"
        elif args.dry_run:
            line["result"] = "WOULD_UPLOAD"
        else:
            try:
                line.update(upload(args.url.rstrip("/"), key, name, data), result="UPLOADED")
                state[name] = {"sha": digest, "at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds")}
            except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
                line["result"] = f"FAILED:{type(exc).__name__}"
                failed += 1
        report[name] = line
        print(json.dumps({"source": name, **line}, ensure_ascii=False), flush=True)
    if not args.dry_run:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.state, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({**state, "_last": {"at": time.time(), "report": report}}, handle,
                      ensure_ascii=False)
    return 1 if failed and failed == len(sources) else 0


if __name__ == "__main__":
    sys.exit(main())
