"""Write this machine's 资源播报 card where its node connector can serve it.

Run every five minutes by `serveros-status-card.timer` (device kit), as the
account that runs serverosd:

    python3 -m fleet_mcp.node.status_card --serverosctl <current>/bin/serverosctl \
        --device-ref device:<id> [--out ~/.local/state/serveros/status-card.json]

It asks the node runtime for the rich status card (`serverosctl card status
--json`: the same card Tokyo's own broadcast showed — today's timeline, this
account's resources, the whole machine, AI Gateway, open problems) and writes
it, owner-only, with the time it was made. The hub fetches it through the
connector's `GET /v1/core/card` and is the ONE sender: Feishu credentials stay
on the hub, and each device is one card a day, patched in place.

It sends nothing and reads no secret. A failure is written as a document that
says so, so the hub shows "无法读取" instead of an old card.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA = "serveros/status-card.v1"
#: Optional, this machine's own: the same inputs its earlier broadcast used, so
#: the card shows the same blocks (AI Gateway, workday schedule, today's line).
#:     gateway_binding: /path/to/gateway-binding.yaml
#:     business_calendar: /path/to/calendar.yaml
#:     day_store: /path/to/day-store.json
OPTIONS = Path("~/.config/serveros/status-card.yaml").expanduser()
_OPTION_FLAGS = {"gateway_binding": "--gateway-binding",
                 "business_calendar": "--business-calendar", "day_store": "--day-store"}
DEFAULT_OUT = Path("~/.local/state/serveros/status-card.json").expanduser()
MAX_CARD_BYTES = 30 * 1024          # Feishu's limit for one card


def _options(path: Path = OPTIONS) -> list[str]:
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ImportError, ValueError):
        return []
    extra: list[str] = []
    for key, flag in _OPTION_FLAGS.items():
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            extra += [flag, str(Path(value).expanduser())]
    return extra


def build(serverosctl: str, device_ref: str, *, run=subprocess.run,
          now: float | None = None, options: list[str] | None = None) -> dict:
    made_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time()))
    base = {"schema": SCHEMA, "device_ref": device_ref, "made_at": made_at}
    try:
        done = run([serverosctl, "card", "status", "--json", "--device-ref", device_ref,
                    *(options if options is not None else _options())],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        return {**base, "state": "ERROR", "reason": f"serverosctl did not run ({type(exc).__name__})"}
    if done.returncode != 0:
        return {**base, "state": "ERROR",
                "reason": f"serverosctl card status exited {done.returncode}"}
    try:
        card = json.loads(done.stdout)
    except ValueError:
        return {**base, "state": "ERROR", "reason": "serverosctl card status did not print JSON"}
    if isinstance(card, dict) and isinstance(card.get("card"), dict):
        card = card["card"]
    if not isinstance(card, dict) or card.get("schema") != "2.0" or "header" not in card:
        return {**base, "state": "ERROR", "reason": "the answer is not a CardKit 2.0 card"}
    if len(json.dumps(card, ensure_ascii=False).encode("utf-8")) > MAX_CARD_BYTES:
        return {**base, "state": "ERROR", "reason": "the card is larger than Feishu accepts"}
    return {**base, "state": "OK", "card": name_accounts(card)}


_UID = re.compile(r"uid:(\d+)")


def name_accounts(card: dict, lookup=None) -> dict:
    """`uid:1007` -> `alice（uid 1007）`: the account names this machine knows.
    Done here because only the device can resolve its own uids."""
    if lookup is None:
        import pwd

        def lookup(uid: int) -> str | None:
            try:
                return pwd.getpwuid(uid).pw_name
            except (KeyError, OverflowError):
                return None

    def swap(match: re.Match) -> str:
        name = lookup(int(match.group(1)))
        return f"{name}（uid {match.group(1)}）" if name else match.group(0)
    return json.loads(_UID.sub(swap, json.dumps(card, ensure_ascii=False)))


def write(document: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=f".{out.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(document, fh, ensure_ascii=False)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serverosctl", required=True)
    parser.add_argument("--device-ref", required=True)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    document = build(args.serverosctl, args.device_ref)
    write(document, Path(args.out).expanduser())
    print(json.dumps({k: document[k] for k in ("state", "made_at")} |
                     ({"reason": document["reason"]} if "reason" in document else {})))
    return 0 if document["state"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
