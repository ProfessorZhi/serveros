#!/usr/bin/env python3
"""A fake `codex app-server`, speaking the wire protocol the real one speaks.

WHY A FAKE AND NOT A MOCK
-------------------------
`unittest.mock` would let these tests assert that the adapter called
`turn/interrupt` with a dict. That is a test of the adapter's *idea* of the
protocol, and the idea is exactly what was uncertain: the 0.153.4 wire format
was established by reading the installed binary's own JSON Schema and then
driving it, and none of it could have been recalled correctly. A mock would
have frozen the same guess the adapter was written from and reported agreement.

So this is a process. It is spawned by `subprocess.Popen` with the argv the
adapter really uses, it reads newline-delimited JSON from a real stdin, and it
answers on a real stdout. The framing, the server-to-client requests, the
notification ordering, and what happens when the server dies are all exercised
rather than described.

WHAT IT MODELS, AND WHAT IT DOES NOT
------------------------------------
Modeled, because these were observed: the request/response shape with no
`jsonrpc` field, `initialize` answering with `userAgent`, `thread/start` /
`thread/list` / `thread/read` / `thread/resume` / `turn/start` /
`turn/interrupt`, the `turn/started` and `turn/completed` notifications with
their `params.turn`, the error object `{code, message}`, `currentTime/read` as
a server-to-client request, ephemeral threads refusing `includeTurns`, and the
four turn words `inProgress` / `completed` / `failed` / `interrupted`.

Not modeled: model output. Turns here are decided by a magic word in the
instruction, because the thing under test is the adapter's handling of the
provider's answers, and a real model would make these tests slow, flaky and
dependent on an account. The live smoke at the bottom of the test file is where
a real model gets to answer.

The instruction decides the behaviour, which is how a test says what it wants
without reaching inside the adapter:

    SLOW     stay inProgress until interrupted
    FAIL     finish with status=failed and an error
    ASK      emit an approval request and wait for the answer
    (other)  finish with status=completed and an echoed agentMessage
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

HOME = os.environ.get("FAKE_CODEX_HOME") or "."
STORE = os.path.join(HOME, "threads.json")


def now_s() -> float:
    return time.time()


def load() -> dict:
    try:
        with open(STORE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"threads": {}, "turns": {}}
    if not isinstance(data, dict):
        return {"threads": {}, "turns": {}}
    data.setdefault("threads", {})
    data.setdefault("turns", {})
    return data


def save(state: dict) -> None:
    os.makedirs(HOME, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, STORE)


class Server:
    def __init__(self) -> None:
        self.state = load()
        self.pending_answers: dict = {}
        self.next_server_id = 1000
        self.active_turn: str | None = None

    # -- wire ---------------------------------------------------------------

    def send(self, payload: dict) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    def reply(self, request_id, result=None, error=None) -> None:
        message: dict = {"id": request_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result if result is not None else {}
        self.send(message)

    def notify(self, method: str, params: dict) -> None:
        self.send({"method": method, "params": params,
                   "emittedAtMs": int(now_s() * 1000)})

    def ask(self, method: str, params: dict):
        """A server-to-client request. Returns the answer, or None on timeout.

        This is the half of the protocol a client written from the client's own
        method list never implements: the server asks questions too, and a
        client that only matches its own response ids will sit here while the
        server waits. `ASK` exists in the instruction vocabulary so a test can
        put the adapter in exactly that position.
        """
        self.next_server_id += 1
        self.send({"id": self.next_server_id, "method": method, "params": params})
        deadline = now_s() + 15
        while now_s() < deadline:
            if self.next_server_id in self.pending_answers:
                return self.pending_answers.pop(self.next_server_id)
            line = sys.stdin.readline()
            if not line:
                return None
            try:
                incoming = json.loads(line)
            except ValueError:
                continue
            if "method" not in incoming and "id" in incoming:
                self.pending_answers[incoming["id"]] = incoming
        return None

    # -- methods ------------------------------------------------------------

    def initialize(self, params: dict) -> dict:
        # A real app-server asks its client what time it is during startup. The
        # adapter must answer it; if it did not, `start()` would time out here
        # and every test below would fail for a reason that looks like "codex
        # is slow" rather than "the client ignores server requests".
        answer = self.ask("currentTime/read", {})
        if isinstance(answer, dict) and answer.get("result"):
            self.clock = answer["result"].get("currentTimeMs")
        # The shape the real 0.153.4 answered with, verbatim in structure:
        #   {clientName}/{codexVersion} ({os}; {arch}) {originator}/{ver} ({clientName}; {clientVersion})
        # The real one echoed the client name back — `serveros/0.153.4 (Ubuntu
        # 24.4.0; x86_64) vscode/1.135.0 (serveros; 0.1.0)` — so a test can
        # assert the initialize it sent was received by noticing its own name in
        # the answer. `fake-fixture` is here so a live run and a hermetic run are
        # never mistaken for each other in a transcript.
        return {
            "userAgent": ("serveros/0.153.4 (Ubuntu 24.4.0; x86_64) "
                          "fake-fixture/1.0.0 (serveros; 0.1.0)"),
            "codexHome": HOME,
            "platformFamily": "unix",
            "platformOs": "linux",
        }

    def thread_start(self, params: dict) -> dict:
        thread_id = str(uuid.uuid4())
        thread = {
            "id": thread_id,
            "createdAt": now_s(),
            "updatedAt": now_s(),
            "status": {"type": "idle"},
            "cwd": params.get("cwd") or HOME,
            "ephemeral": bool(params.get("ephemeral")),
            "canAcceptDirectInput": True,
            "preview": "",
            "model": params.get("model") or "gpt-5.3-codex-spark",
            "modelProvider": "openai",
            "cliVersion": "0.153.4",
        }
        self.state["threads"][thread_id] = thread
        self.state["turns"].setdefault(thread_id, [])
        save(self.state)
        return {"thread": thread}

    def thread_get(self, thread_id: str) -> dict | None:
        return self.state["threads"].get(thread_id)

    def thread_list(self, params: dict) -> dict:
        threads = sorted(self.state["threads"].values(),
                         key=lambda t: t.get("updatedAt") or 0, reverse=True)
        limit = int(params.get("limit") or len(threads) or 1)
        return {"data": threads[:limit]}

    def turn_start(self, params: dict) -> dict:
        thread_id = params.get("threadId")
        thread = self.thread_get(thread_id)
        if thread is None:
            raise RpcError(-32600, "thread not found")
        if not thread.get("canAcceptDirectInput", True):
            raise RpcError(-32600, "thread does not accept direct input")
        text = ""
        for item in params.get("input") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text") or "")
        turn = {
            "id": str(uuid.uuid4()),
            "status": "inProgress",
            "startedAt": now_s(),
            "items": [{"type": "userMessage",
                       "text": text,
                       "clientId": params.get("clientUserMessageId")}],
            "clientUserMessageId": params.get("clientUserMessageId"),
        }
        self.state["turns"].setdefault(thread_id, []).append(turn)
        thread["status"] = {"type": "active"}
        thread["updatedAt"] = now_s()
        save(self.state)

        self.notify("turn/started", {"threadId": thread_id, "turn": turn})
        self.active_turn = turn["id"]
        if "ASK" in text:
            self._needs_approval = turn
            return {"turn": turn}
        if "SLOW" in text:
            # Left inProgress on purpose: `turn/interrupt` needs something to
            # interrupt, and the only way to test that is to have one running.
            return {"turn": turn}
        return {"turn": self.finish(thread_id, turn, text)}

    def finish(self, thread_id: str, turn: dict, text: str) -> dict:
        if "FAIL" in text:
            turn["status"] = "failed"
            turn["error"] = {"message": "model backend unavailable",
                             "codexErrorInfo": "internal"}
        else:
            turn["status"] = "completed"
            turn["items"].append({"type": "agentMessage", "text": f"echo: {text}"})
        turn["completedAt"] = now_s()
        thread = self.thread_get(thread_id)
        if thread:
            thread["status"] = {"type": "idle"}
            thread["updatedAt"] = now_s()
        save(self.state)
        self.active_turn = None
        self.notify("turn/completed", {"threadId": thread_id, "turn": turn})
        return turn

    def turn_interrupt(self, params: dict) -> dict:
        thread_id = params.get("threadId")
        turns = self.state["turns"].get(thread_id) or []
        target = None
        for turn in turns:
            if turn.get("id") == params.get("turnId"):
                target = turn
        if target is None:
            raise RpcError(-32600, "no such turn")
        if target.get("status") != "inProgress":
            raise RpcError(-32600, f"turn is not running: {target.get('status')}")
        target["status"] = "interrupted"
        target["completedAt"] = now_s()
        target["items"].append({"type": "agentMessage", "text": "partial output"})
        thread = self.thread_get(thread_id)
        if thread:
            thread["status"] = {"type": "idle"}
        save(self.state)
        self.active_turn = None
        self.notify("turn/completed", {"threadId": thread_id, "turn": target})
        return {}

    def maybe_approval(self) -> None:
        """Emit the approval request an `ASK` turn was waiting to make."""
        turn = getattr(self, "_needs_approval", None)
        if turn is None:
            return
        self._needs_approval = None
        thread_id = None
        for tid, turns in self.state["turns"].items():
            if any(t.get("id") == turn.get("id") for t in turns):
                thread_id = tid
        answer = self.ask("item/commandExecution/requestApproval", {
            "threadId": thread_id, "turnId": turn.get("id"),
            "command": ["rm", "-rf", "/tmp/example"],
        })
        decision = (answer or {}).get("result", {}).get("decision")
        turn["approval_decision_seen"] = decision
        thread = self.thread_get(thread_id)
        if decision == "allow":
            self.finish(thread_id, turn, "ALLOWED-RUN")
        else:
            turn["status"] = "failed"
            turn["error"] = {"message": f"command not approved ({decision})",
                             "codexErrorInfo": "sandboxError"}
            turn["completedAt"] = now_s()
            if thread:
                thread["status"] = {"type": "idle"}
            save(self.state)
            self.notify("turn/completed", {"threadId": thread_id, "turn": turn})


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] != ["app-server"]:
        sys.stderr.write(f"unexpected argv: {argv!r}\n")
        return 2
    server = Server()
    while True:
        line = sys.stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "method" not in request:
            # An answer to one of this server's own requests.
            server.pending_answers[request.get("id")] = request
            continue
        # The store on disk is re-read before each request, so that a test can
        # write a status this build of codex has never heard of and have the
        # adapter actually see it. There is no protocol method for "pretend you
        # are a future version", and inventing one would be testing the
        # invention rather than the adapter's handling of an unmapped word.
        server.state = load()
        method = request["method"]
        params = request.get("params") or {}
        try:
            if method == "initialize":
                server.reply(request["id"], server.initialize(params))
            elif method == "thread/start":
                server.reply(request["id"], server.thread_start(params))
            elif method == "thread/list":
                server.reply(request["id"], server.thread_list(params))
            elif method == "thread/read":
                thread = server.thread_get(params.get("threadId"))
                if thread is None:
                    raise RpcError(-32600, "thread not found")
                if params.get("includeTurns") and thread.get("ephemeral"):
                    # Observed on the real server, verbatim. The adapter's
                    # handling of this refusal is a behaviour under test.
                    raise RpcError(-32600, "ephemeral threads do not support includeTurns")
                full = dict(thread)
                if params.get("includeTurns"):
                    full["turns"] = server.state["turns"].get(thread["id"], [])
                server.reply(request["id"], {"thread": full})
            elif method == "thread/resume":
                thread = server.thread_get(params.get("threadId"))
                if thread is None:
                    raise RpcError(-32600, "thread not found")
                server.reply(request["id"], {"thread": thread})
            elif method == "turn/start":
                started = server.turn_start(params)
                server.reply(request["id"], started)
                server.maybe_approval()
            elif method == "turn/interrupt":
                server.reply(request["id"], server.turn_interrupt(params))
            else:
                raise RpcError(-32601, f"unknown method: {method}")
        except RpcError as exc:
            server.reply(request["id"], error={"code": exc.code,
                                               "message": exc.message})


if __name__ == "__main__":
    raise SystemExit(main())
