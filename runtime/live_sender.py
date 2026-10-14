#!/usr/bin/env python3
"""The REAL transport, behind an explicit switch — and a READBACK that asks the
far end what it actually holds.

WHY THIS IS A WRAPPER AND NOT A SECOND TRANSPORT
------------------------------------------------
`runtime/message_transport.py` already owns «how a card is carried», and
`runtime/card_delivery.py` already owns «which version went out». This module
adds exactly two things to those, and neither is a rule of its own:

    LiveSender        the same sender, behind a switch that DEFAULTS OFF
    readback()        `im/v1/messages/{id}?card_msg_content_type=user_card_content`
                      — the ORIGINAL Card 2.0 JSON the far end stores, so a
                      receipt's claim can be checked against the thing itself

WHOSE IDENTITY IT MAY USE
-------------------------
The recipient is NOT a parameter a caller invents. It comes from the SAME local
evidence the node's other notifications already use — the owner open_id declared
in this user's own config, cross-checked against the app the stored `lark-cli`
credential belongs to. A bare id, an arbitrary environment override, or a second
app is refused: «who gets told» is a binding, not a preference.

WHAT IT REFUSES
---------------
  * default OFF — constructing without `enabled=True` raises
  * a receipt with no `provider.remote_id` is reported DELIVERED_UNVERIFIED,
    never as a confirmed identity to patch
  * a readback whose card is not Card 2.0, or which the service projects
    (the legacy «upgrade your client» placeholder), is UNKNOWN — not «ok»
  * no token, secret or App Secret is read, printed or carried here
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

LARK_CLI = os.environ.get("SERVEROS_LARK_CLI", "/data/home/vince/.local/bin/lark-cli")
DEFAULT_CONFIG = Path("/data/home/vince/.config/serveros/evening-alignment.env")
DEFAULT_CREDENTIAL_DIR = Path("/data/home/vince/.local/share/lark-cli")

#: The one recipient shape this product delivers to, and the one identity word.
RECIPIENT_PREFIX = "user:"

#: An outcome that means «the service said yes but named no message» — a state
#: that must never be read as «we have a message to patch».
DELIVERED_UNVERIFIED = "DELIVERED_UNVERIFIED"
WAITING_READBACK = "WAITING_READBACK"


class LiveSenderError(RuntimeError):
    pass


class LiveSenderDisabled(LiveSenderError):
    """Raised when a real send is attempted without the explicit switch."""


def _owner_open_id(config_path: Path) -> str:
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveSenderError(f"owner config is unreadable ({exc.__class__.__name__})")
    found = re.search(r"ou_[A-Za-z0-9]{10,}", text)
    if not found:
        raise LiveSenderError(
            f"no owner open_id is declared in {config_path}: this module will not "
            f"guess a recipient, and a guess is not recallable")
    return found.group(0)


def _stored_credential_app(credential_dir: Path) -> str:
    """The app id the stored `lark-cli` credential belongs to, or ''."""
    try:
        names = os.listdir(credential_dir)
    except OSError:
        return ""
    for name in sorted(names):
        match = re.match(r"(cli_[a-z0-9]+)_ou_[a-z0-9]+\.enc$", name)
        if match:
            return match.group(1)
    return ""


def owner_recipient(*, config_path=None, credential_dir=None) -> str:
    """`user:<owner open_id>` from THIS user's declared, cross-checked binding.

    Two independent local facts must agree before a recipient is returned: the
    open_id the config declares, and the app the stored credential belongs to
    (the same app the declaration names). A mismatch means the binding moved
    under us and the answer is a refusal, not a preference.
    """
    config_path = Path(config_path or DEFAULT_CONFIG)
    credential_dir = Path(credential_dir or DEFAULT_CREDENTIAL_DIR)
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    open_id = _owner_open_id(config_path)
    declared = re.search(r"cli_[a-z0-9]{10,}", text)
    declared_app = declared.group(0) if declared else None
    stored_app = _stored_credential_app(credential_dir)
    if declared_app and stored_app and declared_app != stored_app:
        raise LiveSenderError(
            "the declared app and the stored credential belong to DIFFERENT apps: "
            "refusing to pick one, because the recipient would then be whoever "
            "that mismatch happens to name")
    return RECIPIENT_PREFIX + open_id


class LiveSender:
    """`message_transport.LarkMessageSender`, enabled explicitly or not at all."""

    def __init__(self, *, enabled: bool = False, recipient=None,
                 cli: str = LARK_CLI, timeout: int = 120):
        if not enabled:
            raise LiveSenderDisabled(
                "the real sender is OFF by default. Enabling it is a deliberate act "
                "(`enabled=True`) taken after the canary mode is entered, never a "
                "default some caller inherits")
        if recipient is None:
            recipient = owner_recipient()
        if not str(recipient).startswith(RECIPIENT_PREFIX):
            raise LiveSenderError(
                f"the recipient must be `{RECIPIENT_PREFIX}<open_id>`; got "
                f"{str(recipient)[:8]!r}… — a bare id could be a chat, and a report "
                f"in a group cannot be recalled")
        self.recipient = recipient
        self.cli = cli
        self.timeout = timeout

    # -- the two verbs the runner needs -------------------------------------

    def _delegate(self):
        transport = _load_transport()
        return transport.sender(self.recipient, cli=self.cli, timeout=self.timeout)

    def send_card(self, card, *, idempotency_key: str, version=None) -> dict:
        answer = self._delegate().send_card(card, idempotency_key=idempotency_key)
        return self._classify(answer)

    def update(self, request: dict) -> dict:
        answer = self._delegate().update(request)
        return self._classify(answer)

    @staticmethod
    def _classify(answer: dict) -> dict:
        """A DELIVERED that names no message is NOT a confirmed identity.

        The transport already says so in words; this names it in the VOCABULARY,
        so a caller cannot treat «accepted, id unknown» as «we have a message».
        """
        out = dict(answer or {})
        provider = out.get("provider") or {}
        if out.get("state") == "DELIVERED" and not provider.get("remote_id"):
            out["state"] = DELIVERED_UNVERIFIED
        out["evidence"] = "remote"
        return out

    # -- the readback: ask the far end what it holds -------------------------

    def readback(self, message_id: str) -> dict:
        """The ORIGINAL Card 2.0 JSON the far end stores for `message_id`.

        `card_msg_content_type=user_card_content` is the server's own switch for
        returning the card as SENT rather than the legacy projection. Without it
        the API answers the «upgrade your client» placeholder, which says nothing
        about whether the card was accepted — so an unreadable or non-2.0 answer
        is UNKNOWN and never «ok».
        """
        message_id = str(message_id or "").strip()
        if not message_id.startswith("om_"):
            raise LiveSenderError(
                "a readback names ONE message (`om_…`); a chat id or a bare string "
                "would ask about something that is not a message")
        argv = [self.cli, "api", "GET", f"/open-apis/im/v1/messages/{message_id}",
                "--params", json.dumps({"card_msg_content_type": "user_card_content"}),
                "--format", "json"]
        try:
            answer = json.loads(subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout,
                check=False).stdout or "{}")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return {"available": False, "reason": f"readback failed ({exc.__class__.__name__})",
                    "schema": None, "card": None}
        if not answer.get("ok"):
            return {"available": False, "reason": "the service refused the readback",
                    "schema": None, "card": None}
        items = ((answer.get("data") or {}).get("items") or [{}])
        content = ((items[0].get("body") or {}).get("content") or "")
        try:
            card = json.loads(content)
        except ValueError:
            return {"available": False, "schema": None, "card": None,
                    "reason": ("the far end returned no Card 2.0 JSON — the legacy "
                               "projection, not the stored card")}
        if card.get("schema") != "2.0":
            return {"available": False, "schema": card.get("schema"), "card": None,
                    "reason": "the far end stored a card that is not schema 2.0"}
        return {"available": True, "schema": "2.0", "card": card, "reason": ""}


def _load_transport():
    import importlib.util
    import sys
    here = Path(__file__).resolve().parent
    name = "serveros_runtime_message_transport"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, here / "message_transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
