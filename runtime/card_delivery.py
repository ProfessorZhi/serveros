#!/usr/bin/env python3
"""§3 — the version of a card that was sent, and the receipt that says so.

WHY THIS IS NOT `alert_digest`
------------------------------
§3 names two rhythms over one set of facts:

    Status Broadcast   定时状态摘要 — a clock produces the message
    Alert Digest       NEW / ESCALATED / RECOVERED / REOPENED — a DELTA does

They share the facts (the register, the observation, the presentation rules)
and they do not share the send trigger. A status broadcast that has nothing new
to say is still a status broadcast; a digest with nothing new to say is not a
digest, and the whole reason `alert_digest.decision` exists is to stop it being
one. Putting the two on one ledger would make the hourly card advance the alert
delta window — every broadcast would silently consume the escalations that
happened in the same hour, which is the failure the digest was written to
prevent, arriving through its own receipt.

So the ledger here is a SECOND document with its own file, and what it shares
with the digest is only the shape of the claim: a send is recorded against a
VERSION, not against a clock.

WHAT A VERSION IS
-----------------
`freeze_card` hashes the card that was rendered — the exact payload a transport
would carry — and names it `<kind>@<hash12>`. Nothing else goes in. A timestamp
cannot be the identity of a payload, because two cards an hour apart can be
byte-identical and a re-render a second later can differ; the hash answers the
only question a receipt is asked, which is "is this the card the person saw".
`payload_bytes` is carried beside it so that "the version changed" and "the
version changed by four bytes" are different sentences.

THE IDEMPOTENCY KEY IS NOT DERIVED HERE
---------------------------------------
The window a broadcast dedupes within is the CADENCE, and the cadence belongs
to whoever runs the clock — a systemd timer in this node's case, whose catch-up
behaviour is a property of the timer and not of ServerOS. So the key defaults
to a value derived from the kind and the instant the card was rendered at, and
the caller may override it. What this module refuses to do is invent a cadence:
a `top of the hour` rule in here would be a second clock beside the one that
actually drives the send, and the two would disagree the first time a timer ran
late.

WHAT IT IS NOT ALLOWED TO DO
----------------------------
It does not render, it does not send, and it holds no transport. It reads the
card it is handed and writes one line about what happened to it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/card-delivery.v1"
FROZEN_SCHEMA = "serveros/card-frozen.v1"

#: The three card kinds §3 and §1 name. Read as a closed set rather than as a
#: free string so that a receipt cannot be filed under a kind no renderer
#: produces — which is how "the status card has not been sent in three days"
#: becomes unanswerable.
STATUS = "status"
ALERT = "alert"
RECOVERY = "recovery"
KINDS = (STATUS, ALERT, RECOVERY)

#: How many sends are kept. A broadcast ledger is a receipt, not a historian:
#: its job is to answer "what did the last send carry and did it arrive", and
#: an unbounded list would put a growing document on the path of a command that
#: runs every hour forever.
KEEP = 20

DELIVERED = "DELIVERED"
OUTCOMES = (DELIVERED, "FAILED", "UNSUPPORTED", "UNKNOWN")

#: Ids that are NOT message ids, by the prefix Lark gives them. `oc_` names a
#: conversation, `ou_` a user and `on_` a union identity; only `om_` names one
#: message in one conversation. The list is a deny list rather than an `om_`
#: allow list because a receipt is written from what the transport answered:
#: refusing a form this tree has not seen would throw away a real send, while
#: accepting a chat id would file the receipt against a message that does not
#: exist and make every later patch aim at a conversation.
NOT_MESSAGE_PREFIXES = ("oc_", "ou_", "on_")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _store_io():
    # The same atomic write every other durable document in this tree uses —
    # one implementation of "write JSON without a torn read", not two.
    return _load("serveros_runtime_agent_control_store",
                 _HERE / "agent_control_store.py")


class CardDeliveryError(RuntimeError):
    pass


def canonical(blob) -> bytes:
    """One payload, one byte string, whatever the serialiser felt like."""
    return json.dumps(blob, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def check_kind(kind: str) -> str:
    kind = str(kind or "")
    if kind not in KINDS:
        raise CardDeliveryError(
            f"{kind!r} is not one of {', '.join(KINDS)} — a receipt filed "
            f"under a kind no renderer produces cannot be read back")
    return kind


def check_remote_id(value) -> str | None:
    """Which message this receipt is for, or `None` for "the sender has none".

    The receipt is the only place this id is kept, so it is also the place the
    two ways of getting it wrong have to be caught. An empty value is the
    transport answering without one — a fact the ledger is required to hold, so
    it is returned as `None` and not as `''`. A value that is present but is
    not a message id is REFUSED rather than stored: it is what a later patch
    would be aimed at, and a receipt pointing at a conversation or at a typo is
    a receipt for a send nobody can reach. Nothing raised here repeats the
    value; it is an identifier like any other.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        # Refused rather than stringified. `str(b"om_x")` is `"b'om_x'"`, which
        # is a well-formed-looking id naming nothing — the exact quiet failure
        # this function exists to stop, arriving through a missing `.decode()`
        # somewhere upstream.
        raise CardDeliveryError(
            f"a message id arrives as text, and this one is a "
            f"{type(value).__name__}. A receipt that stringified it would name "
            f"a message nobody can reach. The value is not repeated here")
    message_id = value.strip()
    if not message_id:
        return None
    if any(character.isspace() for character in message_id):
        raise CardDeliveryError(
            "a message id is one token, and this one contains whitespace, so "
            "it names no message. The value is not repeated here")
    for prefix in NOT_MESSAGE_PREFIXES:
        if message_id.startswith(prefix):
            raise CardDeliveryError(
                f"`{prefix}` names an identity that is not a message — `oc_` a "
                f"conversation, `ou_` and `on_` a person. Only `om_` names one "
                f"message, so a receipt filed against this value would make "
                f"every later update aim at the wrong thing. The value is not "
                f"repeated here")
    return message_id


def freeze_card(kind: str, card: dict, *, at: str,
                idempotency_key: str | None = None) -> dict:
    """The version of this rendered card, and what a receipt will name.

    The payload is the card and only the card. A version computed over the
    model, or over the model plus the card, would change when a field the
    transport never carries changed — and a receipt whose version moves for a
    reason nobody can see is a receipt nobody trusts.
    """
    kind = check_kind(kind)
    if not isinstance(card, dict) or not card:
        raise CardDeliveryError(
            f"a card version is a hash of the card, and this one is "
            f"{type(card).__name__}"
            + ("" if card else " and empty"))
    blob = canonical(card)
    payload_hash = hashlib.sha256(blob).hexdigest()
    return {
        "schema": FROZEN_SCHEMA,
        "kind": kind,
        "at": at,
        "payload_hash": payload_hash,
        "payload_bytes": len(blob),
        "version": f"{kind}@{payload_hash[:12]}",
        "idempotency_key": idempotency_key or f"card-{kind}-{at}",
    }


class CardLedger:
    """What this node has sent, by version. One document, one atomic write.

    An unreadable ledger is REFUSED rather than replaced, the same rule the
    alert store and the delivery cursor follow: starting from an empty receipt
    list would make every past send invisible, and the next diagnosis would
    conclude the broadcast had never run.
    """

    def __init__(self, path) -> None:
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": SCHEMA, "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CardDeliveryError(
                f"{self.path} exists and cannot be read ({type(exc).__name__}: "
                f"{exc}). Refusing to start with an empty ledger: every send "
                f"this node has recorded would become invisible, and the next "
                f"reader would conclude nothing had ever been sent.") from exc
        if not isinstance(data, dict):
            raise CardDeliveryError(f"{self.path} is not a JSON object")
        if not isinstance(data.get("entries"), list):
            raise CardDeliveryError(
                f"{self.path} has no `entries` list; a ledger that cannot say "
                f"what was sent is not one this module will overwrite")
        data["schema"] = SCHEMA
        return data

    # -- reads ------------------------------------------------------------

    def entries(self, kind: str | None = None) -> list[dict]:
        rows = [dict(row) for row in self._data["entries"]
                if isinstance(row, dict)]
        if kind is None:
            return rows
        return [row for row in rows if row.get("kind") == check_kind(kind)]

    def last(self, kind: str | None = None) -> dict | None:
        rows = self.entries(kind)
        return rows[-1] if rows else None

    def last_delivered(self, kind: str | None = None) -> dict | None:
        for row in reversed(self.entries(kind)):
            if row.get("state") == DELIVERED:
                return row
        return None

    def summary(self, kind: str | None = None) -> dict:
        rows = self.entries(kind)
        return {
            "schema": SCHEMA,
            "path": str(self.path),
            "kind": kind,
            "entries": len(rows),
            "last": rows[-1] if rows else None,
            "last_delivered": self.last_delivered(kind),
        }

    # -- the one write ----------------------------------------------------

    def record(self, kind: str, *, at: str, state: str, version: str,
               payload_hash: str | None = None, payload_bytes: int | None = None,
               idempotency_key: str | None = None, remote_id: str | None = None,
               reason: str | None = None) -> dict:
        """One line: which version this send carried, and what happened to it.

        A FAILED send is recorded WITH its version rather than without one. The
        version is what was attempted and that is a true statement about it; a
        null would make "the card that failed to arrive" and "a send nobody
        composed a card for" read the same, and only one of those is worth
        waking somebody about.

        `remote_id` is the message the send produced — the transport answers
        with one, and it is what makes a later update of THAT message possible
        instead of a second message. It is `None` when the transport answered
        without one, and that absence is kept rather than filled in: a receipt
        that guessed an id would point a later patch at a message nobody can
        name. It is not required for DELIVERED, because the transport itself
        reports that case rather than refusing it.
        """
        kind = check_kind(kind)
        # Checked here and not at the call sites: this is the only door the row
        # goes through, so one rule covers every caller — including the daemon,
        # which is the only writer over the socket.
        remote_id = check_remote_id(remote_id)
        if state not in OUTCOMES:
            raise CardDeliveryError(
                f"{state!r} is not one of {', '.join(OUTCOMES)} — an outcome "
                f"nobody can name is an outcome that will be read as a delivery")
        row = {
            "kind": kind,
            "at": at,
            "state": state,
            "version": version,
            "payload_hash": payload_hash,
            "payload_bytes": payload_bytes,
            "idempotency_key": idempotency_key,
            # The message this send produced, so that the NEXT update of the
            # same card can be a patch of it. Without this the identity is
            # dropped here and the only way forward is a second message.
            "remote_id": remote_id,
            "delivered": state == DELIVERED,
        }
        if state != DELIVERED:
            row["reason"] = reason or f"delivery answered {state}"
        entries = [dict(item) for item in self._data["entries"]
                   if isinstance(item, dict)]
        entries.append(row)
        self._data["entries"] = entries[-KEEP:]
        _store_io()._atomic_write(self.path, self._data)
        return {"recorded": True, "entry": row, "ledger": self.summary(kind)}


def ledger_path(state_path) -> Path:
    """The ledger beside the register and the alert cursor, never inside them.

    Sibling documents rather than keys in one: the alert register is rewritten
    whole on every evaluation pass, and a card receipt that lived inside it
    would be erased by a pass that has nothing to do with cards.
    """
    return Path(state_path).parent.parent / "cards" / "delivery.json"


def _require_frozen(document: dict, kind: str) -> dict:
    """A record must name the version the card it was handed actually is.

    The whole value of the receipt is that its version and the payload that
    went out are the same thing, so they are compared here rather than trusted.
    A caller that hands over a card and a version that does not hash to it is
    recording something nobody rendered.

    A card with NO frozen block is refused too, unlike the digest's legacy
    path: `card status` has always emitted one, so a missing block is not an
    older producer but a caller that built the document itself, and a receipt
    for that would name a version of nothing.
    """
    frozen = document.get("frozen") if isinstance(document, dict) else None
    if not isinstance(frozen, dict):
        raise CardDeliveryError(
            "the card handed over carries no `frozen` block. A receipt names "
            "the version that was sent, and a version cannot be recovered from "
            "a document that does not state one — re-render it with "
            "`serverosctl card <kind> --json`")
    if check_kind(kind) != frozen.get("kind"):
        raise CardDeliveryError(
            f"the card is a {frozen.get('kind')!r} and the receipt is being "
            f"filed under {kind!r}")
    return frozen


def verify(frozen: dict, card: dict) -> None:
    """The frozen block must be the version of THIS card.

    §3: every send saves the actual frozen payload, hash and version. That is
    only meaningful if the three belong to one another, so the hash is
    recomputed from the card and compared. A mismatch is a delivery being
    recorded against a card that was never rendered, and the receipt for it
    would be a receipt for nothing.
    """
    recomputed = hashlib.sha256(canonical(card)).hexdigest()
    if recomputed != frozen.get("payload_hash"):
        raise CardDeliveryError(
            f"the frozen block names version {frozen.get('version')} and the "
            f"card hashes to {recomputed[:12]}. These are two different "
            f"payloads, and a receipt naming the wrong one is a receipt for a "
            f"card nobody rendered.")


def main(argv: list[str] | None = None) -> int:
    """Read one ledger. A diagnostic, not the product surface."""
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--state", required=True, help="the alerts state file")
    parser.add_argument("--kind", default=None, choices=list(KINDS))
    args = parser.parse_args(argv)
    json.dump(CardLedger(ledger_path(args.state)).summary(args.kind),
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
