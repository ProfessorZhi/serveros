#!/usr/bin/env python3
"""R1 #248 / S2 #245 — the client turn id, and what a provider readback can prove.

THE QUESTION
------------
After a send response is lost, S2 still holds the **client** turn id it
generated before sending. It does not hold the provider-native turn id, because
that id was in the response that was lost. Can any current adapter be asked by
the client id, and does its answer have provider-owned provenance?

THE ANSWER, IN TWO PARTS
------------------------
1. **Codex app-server, send to an already-known thread: schema-supported.**
   The installed `codex-cli 0.160.0` stable v2 schema declares:

   * `TurnStartParams.clientUserMessageId: string|null` — the pre-send client
     identity (`stable/v2/TurnStartParams.json`).
   * `ThreadItem.userMessage.clientId: string|null` — the provider-owned
     readback on the user-message item (`stable/v2/ThreadReadResponse.json`,
     `stable/v2/ThreadTurnsListResponse.json`).
   * `thread/turns/list` takes `threadId`, `itemsView` and a cursor;
     `itemsView: "full"` returns the items, including the user-message item
     (`stable/v2/ThreadTurnsListParams.json`).
   * `thread/read` with `includeTurns: true` also returns turns and items, but
     its schema says full-history hydration is deprecated for paginated
     threads and recommends `thread/turns/list` + `thread/items/list`
     (`stable/v2/ThreadReadParams.json`).

   The read path is not filtered by client id; the caller pages the known
   thread and matches `userMessage.clientId`. The enclosing `Turn.id` is the
   provider-native turn id and is returned with the match.

2. **Production is still `BLOCKED_PROVIDER_SOURCE` until a live echo is
   observed.** The schema is the provider's own contract, but it does not
   state that `clientUserMessageId` is copied into `clientId`; it only permits
   both fields. No live send was executed in this lane. This module therefore
   produces `SCHEMA_ONLY` evidence; the S2 owner must keep the production
   source `BLOCKED_PROVIDER_SOURCE` until one owner-approved bounded echo test
   proves the mapping.

WHAT THE READBACK CAN AND CANNOT PROVE
--------------------------------------
* A match with the same client id **and** the same intent digest proves
  `APPLIED`, with `source="codex:thread/turns/list"` and `provenance="FACT"`.
* A miss is `UNKNOWN`, never `NOT_APPLIED`. Absence from a paginated or
  retained history is not proof that the send did not happen; a miss cannot
  free the scope for a retry.
* A client-id match with a different intent is `UNKNOWN` (`CONFLICT`), never
  `APPLIED`.
* A duplicate client id is `UNKNOWN`, never an arbitrary pick.
* `itemsView` other than `full`, incomplete pagination, an absent thread, or a
  malformed response is `UNKNOWN`.
* `negative_proof` is always false. `exactly_once` and `retry_allowed` are
  always false. This module never invents a guarantee.

SCOPE LIMITS
------------
* **Codex create** loses the thread id with the response. A client id alone
  cannot locate the thread without an unbounded scan, so create stays
  `RESPONSE_ID_ONLY`.
* **BotMux** sends `turnIdempotencyKey` but its read path
  (`/trigger-result?triggerId=...`) needs the response id; it stays
  `RESPONSE_ID_ONLY`.
* **CloudCLI MCP** `session_send` exposes no client id parameter and
  `run_get` needs a `runId`; it is `NO_SOURCE`.
* **Human control** `/api/trigger` also returns `triggerId`; it stays
  `RESPONSE_ID_ONLY`.

This module is pure: no socket, file, process, clock, environment, credential
or transcript value. The readback is injected as data.
"""
from __future__ import annotations

import hashlib
import json
import re

SCHEMA = "serveros/fleet-v1-runtime-client-turn-id.v1"
EVIDENCE_LEVEL = "SCHEMA_ONLY"
CLIENT_TURN_ID = "CLIENT_TURN_ID"
RESPONSE_ID_ONLY = "RESPONSE_ID_ONLY"
NO_SOURCE = "NO_SOURCE"
BLOCKED_PROVIDER_SOURCE = "BLOCKED_PROVIDER_SOURCE"

# The only two states this source may produce. `NOT_APPLIED` is deliberately
# absent: a provider history miss is not proof of non-application.
OBSERVATION_STATES = ("APPLIED", "UNKNOWN")

READ_PATHS = ("codex:thread/turns/list", "codex:thread/read")

REASONS = (
    "CLIENT_ID_NOT_FOUND",
    "CLIENT_ID_DUPLICATE",
    "CLIENT_ID_MATCH_INTENT_MISMATCH",
    "INTENT_UNVERIFIABLE",
    "INVALID_CLIENT_TURN_ID",
    "INVALID_INTENT_DIGEST",
    "INVALID_THREAD_ID",
    "NO_TURNS",
    "SOURCE_MALFORMED",
    "THREAD_ITEMS_UNAVAILABLE",
    "THREAD_MISMATCH",
    "THREAD_NOT_FOUND",
)

_SECRET_SHAPED = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|Bearer\s+\S{8,})",
    re.IGNORECASE,
)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _secret_like(value: str) -> bool:
    return bool(_SECRET_SHAPED.search(value))


def intent_digest(text: str) -> str:
    """The intent digest for one text instruction. Pure and stable."""
    return intent_digest_of_texts([str(text)])


def intent_digest_of_texts(texts) -> str:
    """The intent digest for the text parts of one user message.

    The caller must use THIS function before sending and on the readback. A
    digest computed a different way is not a comparison; it is two unrelated
    strings that both look like hashes.
    """
    payload = json.dumps(list(texts), ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capability_for(adapter: str, verb: str, *, thread_known: bool = False) -> str:
    """What the adapter can be asked by after a lost response.

    `CLIENT_TURN_ID` is returned only for the one case the schema supports:
    a Codex app-server send to a thread whose id is already known. Everything
    else is the honest weaker capability.
    """
    if adapter == "codex-app-server" and verb == "send" and thread_known:
        return CLIENT_TURN_ID
    if adapter in ("codex-app-server", "botmux-agent-session", "human-control"):
        return RESPONSE_ID_ONLY
    return NO_SOURCE


def _unknown(reason: str, *, detail: str, client_turn_id=None, thread_id=None,
             intent_digest_value=None, lookup_miss: bool, scan_complete: bool,
             conflict: bool = False, read_path=None) -> dict:
    shown = client_turn_id
    if _text(shown) and _secret_like(shown):
        shown = "[REDACTED]"
    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "state": "UNKNOWN",
        "source": read_path if state_source_known(read_path) else None,
        "provenance": "UNKNOWN",
        "reason": reason,
        "detail": detail,
        "client_turn_id": shown if _text(shown) else None,
        "thread_id": thread_id,
        "intent_digest": intent_digest_value,
        "matched_on": None,
        "provider_turn_id": None,
        "provider_item_id": None,
        "lookup_miss": bool(lookup_miss),
        "scan_complete": bool(scan_complete),
        "conflict": bool(conflict),
        "negative_proof": False,
        "exactly_once": False,
        "retry_allowed": False,
        "source_capability": CLIENT_TURN_ID,
        "not_claimed": ("a history miss or a malformed/incomplete read is not "
                        "proof that the effect was not applied"),
    }


def state_source_known(read_path) -> bool:
    return read_path in READ_PATHS


def _texts_from_item(item: dict):
    content = item.get("content")
    if not isinstance(content, list):
        return None
    texts = [part["text"] for part in content
             if isinstance(part, dict)
             and part.get("type") == "text"
             and isinstance(part.get("text"), str)]
    return texts or None


def _walk(readback: dict):
    """Yield (turn, item) pairs, or raise ValueError for a malformed shape."""
    pages = readback.get("pages")
    if not isinstance(pages, list):
        raise ValueError("pages must be a list")
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("each page must be an object")
        data = page.get("data")
        if not isinstance(data, list):
            raise ValueError("each page.data must be a list")
        for turn in data:
            if not isinstance(turn, dict):
                raise ValueError("each turn must be an object")
            items = turn.get("items")
            if not isinstance(items, list):
                raise ValueError("each turn.items must be a list")
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("each item must be an object")
                yield turn, item


def observe_readback(*, client_turn_id: str, intent_digest: str,
                     thread_id: str, readback: dict,
                     read_path: str = "codex:thread/turns/list") -> dict:
    """Project one injected Codex readback into an S2 observation shape.

    The function performs no IO. `readback` is a synthetic or captured response
    from `thread/turns/list` (preferred) or `thread/read`; the caller supplies
    it. The output is the S2 observation form, with the R1 provenance and the
    explicit negative-proof refusal.
    """
    if not _text(client_turn_id) or _secret_like(str(client_turn_id)):
        return _unknown(
            "INVALID_CLIENT_TURN_ID",
            detail="client_turn_id must be a non-empty, non-credential-shaped id",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)
    if not _text(intent_digest):
        return _unknown(
            "INVALID_INTENT_DIGEST",
            detail="intent_digest is required; it is the pre-send intent hash",
            client_turn_id=client_turn_id, thread_id=thread_id,
            lookup_miss=True, scan_complete=False, read_path=read_path)
    if not _text(thread_id):
        return _unknown(
            "INVALID_THREAD_ID",
            detail="the provider thread id must be known before the read; a "
                   "create whose response was lost does not have one",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)
    if not isinstance(readback, dict):
        return _unknown(
            "SOURCE_MALFORMED", detail="readback must be an object",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)

    if readback.get("thread_found") is False:
        return _unknown(
            "THREAD_NOT_FOUND",
            detail="the provider says the thread does not exist",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=True, read_path=read_path)

    readback_thread = readback.get("thread_id")
    if _text(readback_thread) and readback_thread != thread_id:
        return _unknown(
            "THREAD_MISMATCH",
            detail="the readback belongs to a different thread; a match from "
                   "another thread is not a match for this attempt",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)

    if readback.get("items_view") != "full":
        return _unknown(
            "THREAD_ITEMS_UNAVAILABLE",
            detail="itemsView must be `full`; summary/notLoaded cannot carry the "
                   "user-message clientId or its content",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)

    try:
        pairs = list(_walk(readback))
    except ValueError as exc:
        return _unknown(
            "SOURCE_MALFORMED", detail=str(exc),
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=False, read_path=read_path)

    pages = readback.get("pages") or []
    scan_complete = all(
        page.get("nextCursor") in (None, "") for page in pages
        if isinstance(page, dict))
    if not pages:
        return _unknown(
            "NO_TURNS",
            detail="the thread returned no turn pages; absence is not proof",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=True, read_path=read_path)

    matches = [(turn, item) for turn, item in pairs
               if item.get("type") == "userMessage"
               and item.get("clientId") == client_turn_id]
    if len(matches) > 1:
        return _unknown(
            "CLIENT_ID_DUPLICATE",
            detail="more than one user message carries this client id; the "
                   "source will not pick one",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=False,
            scan_complete=scan_complete, conflict=True, read_path=read_path)
    if not matches:
        return _unknown(
            "CLIENT_ID_NOT_FOUND",
            detail=("no user message carries this client id"
                    + ("" if scan_complete else "; the scan was incomplete")),
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=True,
            scan_complete=scan_complete, read_path=read_path)

    turn, item = matches[0]
    provider_turn_id = turn.get("id")
    provider_item_id = item.get("id")
    if not _text(provider_turn_id) or not _text(provider_item_id):
        return _unknown(
            "SOURCE_MALFORMED",
            detail="the matched turn or item has no provider id",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=False,
            scan_complete=scan_complete, read_path=read_path)

    texts = _texts_from_item(item)
    if texts is None:
        return _unknown(
            "INTENT_UNVERIFIABLE",
            detail="the matched user message has no text content, so the "
                   "intent digest cannot be compared",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=False,
            scan_complete=scan_complete, read_path=read_path)
    if intent_digest_of_texts(texts) != intent_digest:
        return _unknown(
            "CLIENT_ID_MATCH_INTENT_MISMATCH",
            detail="the client id matches but the intent digest does not; this "
                   "is a conflict, not an applied effect",
            client_turn_id=client_turn_id, thread_id=thread_id,
            intent_digest_value=intent_digest, lookup_miss=False,
            scan_complete=scan_complete, conflict=True, read_path=read_path)

    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "state": "APPLIED",
        "source": read_path,
        "provenance": "FACT",
        "reason": None,
        "detail": "client id and intent digest both match a provider user message",
        "client_turn_id": client_turn_id,
        "thread_id": thread_id,
        "intent_digest": intent_digest,
        "matched_on": "userMessage.clientId",
        "provider_turn_id": provider_turn_id,
        "provider_item_id": provider_item_id,
        "lookup_miss": False,
        "scan_complete": scan_complete,
        "conflict": False,
        "negative_proof": False,
        "exactly_once": False,
        "retry_allowed": False,
        "source_capability": CLIENT_TURN_ID,
        "not_claimed": ("a positive match proves this turn was applied; it does "
                        "not prove a miss was not applied and does not authorize "
                        "a retry"),
    }


class SyntheticCodexReadbackSource:
    """A synthetic S2 observation source over an injected Codex readback.

    This is NOT a production source. It declares `CLIENT_TURN_ID` because the
    stable schema supports it, and it returns `SCHEMA_ONLY` evidence. The S2
    owner keeps the production source `BLOCKED_PROVIDER_SOURCE` until a live
    echo test proves the `clientUserMessageId` → `clientId` mapping.
    """

    capability = CLIENT_TURN_ID

    def __init__(self, *, readback: dict, intent_digest_value: str,
                 thread_id: str, read_path: str = "codex:thread/turns/list") -> None:
        self.readback = readback
        self.intent_digest_value = intent_digest_value
        self.thread_id = thread_id
        self.read_path = read_path

    def observe(self, attempt: dict, *, key: str | None = None) -> dict:
        client_turn_id = (attempt.get("client_turn_id")
                          or attempt.get("turn_idempotency_key")
                          or "")
        intent = attempt.get("intent_digest") or self.intent_digest_value
        thread_id = (attempt.get("provider_session_id")
                     or attempt.get("thread_id")
                     or self.thread_id)
        return observe_readback(
            client_turn_id=client_turn_id, intent_digest=intent,
            thread_id=thread_id, readback=self.readback,
            read_path=self.read_path)


def capability_matrix() -> list[dict]:
    """The per-adapter answer, as data for the report and the S2 owner."""
    return [
        {
            "adapter": "codex-app-server",
            "verb": "send",
            "precondition": "thread id already known",
            "capability": CLIENT_TURN_ID,
            "read_path": "codex:thread/turns/list#itemsView=full",
            "positive": "APPLIED on exact clientId + intent digest",
            "negative": "UNKNOWN; no NOT_APPLIED",
            "live_proof": "NOT_RUN",
            "production": BLOCKED_PROVIDER_SOURCE,
            "why": "stable v2 schema declares clientUserMessageId and "
                   "userMessage.clientId; echo mapping not live-proven",
        },
        {
            "adapter": "codex-app-server",
            "verb": "create",
            "precondition": "thread id is in the lost response",
            "capability": RESPONSE_ID_ONLY,
            "read_path": None,
            "positive": None,
            "negative": None,
            "live_proof": "NOT_RUN",
            "production": BLOCKED_PROVIDER_SOURCE,
            "why": "a client id cannot locate an unknown thread without an "
                   "unbounded scan",
        },
        {
            "adapter": "botmux-agent-session",
            "verb": "send",
            "precondition": "triggerId is in the lost response",
            "capability": RESPONSE_ID_ONLY,
            "read_path": "GET /api/sessions/{id}/trigger-result?triggerId=...",
            "positive": None,
            "negative": None,
            "live_proof": "NOT_RUN",
            "production": BLOCKED_PROVIDER_SOURCE,
            "why": "turnIdempotencyKey is sent but no read path accepts it",
        },
        {
            "adapter": "cloudcli-mcp",
            "verb": "send",
            "precondition": "no client id parameter on session_send",
            "capability": NO_SOURCE,
            "read_path": "run_get requires runId",
            "positive": None,
            "negative": None,
            "live_proof": "NOT_RUN",
            "production": BLOCKED_PROVIDER_SOURCE,
            "why": "the MCP surface exposes no pre-send client turn id",
        },
        {
            "adapter": "human-control",
            "verb": "send",
            "precondition": "triggerId is in the lost response",
            "capability": RESPONSE_ID_ONLY,
            "read_path": "BotMux /api/trigger -> triggerId",
            "positive": None,
            "negative": None,
            "live_proof": "NOT_RUN",
            "production": BLOCKED_PROVIDER_SOURCE,
            "why": "idempotencyKey is sent but the response is the only id reader",
        },
    ]
