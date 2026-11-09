"""The hub's binding to Feishu through lark-cli, beyond sending a card.

Three things, all through the lark-cli that is already logged in on the hub,
so the owner never looks up an id:

*   **destinations** — `owner` (whoever lark-cli is logged in as),
    `chat-name:<name>` (a group found by name), or an explicit
    `user:<open_id>` / `chat:<chat_id>`; resolved at start, refused loudly if
    they cannot be;
*   **self-check** — `lark-cli auth status`, read at start and periodically,
    written into the heartbeat; a lapsed user login is told to the owner once a
    day by card (the bot identity still sends);
*   **buttons** — `lark-cli event consume card.action.trigger` in a background
    thread. A click is acted on only when the operator is the owner, and only
    for a closed set of actions: mute an episode, pause the broadcast, revert
    the last tuning. Everything else is ignored and logged.

Requires, once, in the Feishu developer console: App → Events & Callbacks →
Callback configuration → long connection, with `card.action.trigger`. Without
it the consumer runs and receives nothing; the self-check says so.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

Runner = Callable[[list[str]], Any]

ACTIONS = ("mute", "pause_broadcast", "revert_tune")


def _cli() -> list[str]:
    for candidate in ("lark-cli", str(Path.home() / ".local/bin/lark-cli")):
        if candidate == "lark-cli" or Path(candidate).is_file():
            yield candidate


def _run(argv: list[str], timeout: int = 60):
    return subprocess.run(argv, text=True, capture_output=True, timeout=timeout)


def _first(text: str, key: str, prefix: str) -> str | None:
    try:
        stack = [json.loads(text)]
    except (ValueError, TypeError):
        match = re.search(rf"\b{prefix}[0-9a-zA-Z]{{16,}}\b", text or "")
        return match.group(0) if match else None
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            value = item.get(key)
            if isinstance(value, str) and value.startswith(prefix):
                return value
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def _ask(args: list[str], run: Runner | None) -> str | None:
    run = run or _run
    for cli in _cli():
        try:
            done = run([cli, *args])
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            return done.stdout
    return None


def owner_open_id(*, run: Runner | None = None) -> str | None:
    named = os.environ.get("SERVEROS_NOTIFY_OWNER", "").strip()
    if named:
        return named.split(":", 1)[-1]
    out = _ask(["contact", "+get-user", "--as", "user"], run)
    return _first(out, "open_id", "ou_") if out else None


def chat_by_name(name: str, *, run: Runner | None = None) -> str | None:
    """The chat_id of the group whose name is exactly `name` (bot can see it)."""
    out = _ask(["im", "+chat-search", "--query", name, "--as", "bot"], run)
    if not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    stack = [data]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            if item.get("name") == name and str(item.get("chat_id", "")).startswith("oc_"):
                return item["chat_id"]
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def resolve(spec: str, *, run: Runner | None = None) -> str:
    """A `--notify lark:<spec>` destination as lark-cli's `user:`/`chat:` form."""
    if spec.startswith(("user:", "chat:")):
        return spec
    if spec == "owner":
        found = owner_open_id(run=run)
        if found:
            return f"user:{found}"
        raise RuntimeError(
            "lark:owner: could not read the owner's open_id from `lark-cli contact +get-user "
            "--as user` (is lark-cli logged in as the owner? `lark-cli auth status`). Set "
            "SERVEROS_NOTIFY_OWNER=<open_id>, or pass lark:user:<open_id> / lark:chat:<chat_id>.")
    if spec.startswith("chat-name:"):
        name = spec.split(":", 1)[1]
        found = chat_by_name(name, run=run)
        if found:
            return f"chat:{found}"
        raise RuntimeError(f"lark:chat-name:{name}: no group by that exact name is visible to "
                           f"the bot; add the bot to the group, or pass lark:chat:<chat_id>.")
    raise RuntimeError(f"unknown Feishu destination {spec!r}")


def health(*, run: Runner | None = None) -> dict[str, Any]:
    """`lark-cli auth status`, read as bot/user readiness. Never prints a token."""
    out = _ask(["auth", "status"], run)
    if out is None:
        return {"cli": "UNAVAILABLE", "bot": "UNKNOWN", "user": "UNKNOWN"}
    state: dict[str, Any] = {"cli": "OK"}
    try:
        data = json.loads(out)
        if not isinstance(data, dict):
            raise ValueError("auth status is not an object")
        # Both identities live under `identities`; a flat {"bot": …} shape is
        # still read, so an older lark-cli keeps reporting correctly.
        people = data.get("identities")
        if not isinstance(people, dict):
            people = data
        for who in ("bot", "user"):
            value = people.get(who)
            if isinstance(value, dict):
                value = value.get("status") or value.get("state")
            state[who] = value if isinstance(value, str) and value else "UNKNOWN"
    except (ValueError, AttributeError):
        for who in ("bot", "user"):
            match = re.search(rf"^\s*{who}\s*[=:]\s*(\S+)", out, re.MULTILINE)
            state[who] = match.group(1) if match else "UNKNOWN"
    return state


# -- buttons ------------------------------------------------------------------

def button(text: str, value: dict[str, Any], *, kind: str = "default") -> dict:
    """A CardKit 2.0 callback button carrying a closed action."""
    if value.get("fleet") not in ACTIONS:
        raise ValueError(f"unknown button action {value.get('fleet')!r}")
    return {"tag": "button", "text": {"tag": "plain_text", "content": text}, "type": kind,
            "behaviors": [{"type": "callback", "value": value}]}


def decide(event: dict[str, Any], owner: str | None) -> dict[str, Any] | None:
    """A click, as the one action it may cause — or None (ignored)."""
    payload = event.get("event", event)
    operator = payload.get("operator_id") or (payload.get("operator") or {}).get("open_id")
    if not owner or operator != owner:
        return None
    value = payload.get("action_value")
    if value is None:
        value = (payload.get("action") or {}).get("value")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or value.get("fleet") not in ACTIONS:
        return None
    return value


CONSUME_CALLBACKS = ["event", "consume", "card.action.trigger", "--as", "bot"]


def _spawn(argv: list[str]):
    """Start lark-cli, trying every candidate `_cli()` offers.

    `_ask` already walks them because a service's PATH usually has no
    `~/.local/bin`; the listener used to take the first name only and give up,
    which reported CLI_MISSING on a hub where every other lark-cli call worked.

    `stdin` is a pipe we hold and never write: `event consume` treats stdin EOF
    as "shut down", and under systemd the unit's stdin is /dev/null, so the
    consumer would exit — cleanly, and at once — with the subscription ready
    and never used. Holding the pipe open is the documented way to keep it
    (SIGTERM is the other).
    """
    for cli in _cli():
        try:
            return subprocess.Popen([cli, *argv], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True)
        except OSError:
            continue
    return None


class ButtonListener:
    """`lark-cli event consume card.action.trigger --as bot`, one thread, restarted."""

    def __init__(self, owner: str | None, act: Callable[[dict[str, Any]], str],
                 argv: list[str] | None = None) -> None:
        self.owner = owner
        self.act = act
        self.argv = argv or CONSUME_CALLBACKS
        self.state = "NOT_STARTED"
        self.handled = 0
        self.last = ""
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.owner is None:
            self.state = "NO_OWNER"
            return
        self._thread = threading.Thread(target=self._loop, name=f"feishu-{self.argv[2]}",
                                        daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        import time

        while True:
            proc = _spawn(self.argv)
            if proc is None:
                self.state = "CLI_MISSING"
                return
            self.state = "STARTING"
            self.last = ""
            # stderr is drained beside stdout: it is where readiness and the
            # reason for an exit are written, and a child filling an unread
            # stderr pipe would block with the state still saying LISTENING.
            pump = threading.Thread(target=self._drain, args=(proc.stderr,), daemon=True)
            pump.start()
            for line in proc.stdout:
                self.feed(line)
            proc.wait()
            pump.join(timeout=2)
            self.state = f"EXITED_{proc.returncode}" + (f": {self.last}" if self.last else "")
            time.sleep(30)

    def _drain(self, stream) -> None:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            self.last = line[:160]
            if "[event] ready" in line:
                self.state = "LISTENING"

    def feed(self, line: str) -> str | None:
        try:
            event = json.loads(line)
        except ValueError:
            return None
        value = decide(event, self.owner)
        if value is None:
            return None
        self.handled += 1
        return self.act(value)
