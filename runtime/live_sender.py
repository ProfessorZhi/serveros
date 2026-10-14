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
#: «We asked the far end and the answer did not match what we sent» — the cursor
#: must not move, and nothing may be re-created on the strength of it.
UNKNOWN_PENDING = "UNKNOWN_PENDING"
WAITING_READBACK = "WAITING_READBACK"
#: The only evidence word that a REAL send may carry: a receipt whose claim was
#: checked against the card the far end actually stores.
REMOTE_VERIFIED = "remote_verified"


class LiveSenderError(RuntimeError):
    pass


class LiveSenderDisabled(LiveSenderError):
    """Raised when a real send is attempted without the explicit switch."""


def _declared_app(text: str) -> str:
    found = re.search(r"cli_[a-z0-9]{10,}", text or "")
    return found.group(0) if found else ""


def _stored_credential(credential_dir: Path) -> tuple[str, str]:
    """`(app_id, owner_open_id)` from the stored credential, or `('', '')`.

    The credential file name carries BOTH facts, and BOTH are returned: taking
    only the app — or only the alphabetically first file — is how a cross-check
    comes to pass while pointing at somebody else's account.
    """
    try:
        names = sorted(os.listdir(credential_dir))
    except OSError:
        return "", ""
    for name in names:
        match = re.match(r"(cli_[a-z0-9]+)_(ou_[a-z0-9]+)\.enc$", name)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def owner_binding(*, config_path=None, credential_dir=None) -> dict:
    """The verified `(app_id, owner_open_id)` — and it takes ALL of them.

    THREE local facts must be present and agree before a recipient exists at
    all: the app the config declares, the app the stored credential belongs to,
    and the USER that credential belongs to. Any one missing, or any
    disagreement, is a refusal — because «one of them was absent, so we let it
    through» is exactly how a send reaches an account nobody named.
    """
    config_path = Path(config_path or DEFAULT_CONFIG)
    credential_dir = Path(credential_dir or DEFAULT_CREDENTIAL_DIR)
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LiveSenderError(f"owner config is unreadable ({exc.__class__.__name__})")
    declared = _declared_app(text)
    if not declared:
        raise LiveSenderError(
            "the config declares no app id: refusing, because a recipient read "
            "from a config that does not name its app could belong to any app")
    found = re.search(r"ou_[A-Za-z0-9]{10,}", text)
    if not found:
        raise LiveSenderError(
            "the config declares no owner open_id: this module will not guess a "
            "recipient, and a guess is not recallable")
    owner_open_id = found.group(0)
    credential_app, credential_user = _stored_credential(credential_dir)
    if not credential_app or not credential_user:
        raise LiveSenderError(
            f"no `lark-cli` credential naming BOTH an app and a user was found in "
            f"{credential_dir}: without one there is nothing to cross-check against")
    if credential_app != declared:
        raise LiveSenderError(
            "the declared app and the stored credential belong to DIFFERENT apps")
    if credential_user != owner_open_id:
        raise LiveSenderError(
            "the stored credential belongs to a DIFFERENT user than the config "
            "declares: refusing to pick one, because a send would then reach "
            "whoever that mismatch happens to name")
    return {"app_id": declared, "open_id": owner_open_id,
            "recipient": RECIPIENT_PREFIX + owner_open_id}


def owner_recipient(*, config_path=None, credential_dir=None) -> str:
    """`user:<owner open_id>` from the verified binding. See `owner_binding`."""
    return owner_binding(config_path=config_path,
                         credential_dir=credential_dir)["recipient"]


class LiveSender:
    """`message_transport.LarkMessageSender`, enabled explicitly or not at all."""

    def __init__(self, *, enabled: bool = False, recipient=None, binding=None,
                 cli: str = LARK_CLI, timeout: int = 120):
        if not enabled:
            raise LiveSenderDisabled(
                "the real sender is OFF by default. Enabling it is a deliberate act "
                "(`enabled=True`) taken after the canary mode is entered, never a "
                "default some caller inherits")
        self.binding = binding or owner_binding()
        verified = str(self.binding.get("recipient") or "")
        if recipient is None:
            recipient = verified
        # A CALLER MAY NOT NAME ITS OWN RECIPIENT. Passing one is allowed only so
        # a test can hand in an injected binding; it must still EQUAL the verified
        # one, because «set the recipient to whatever you like» is the whole
        # failure this binding exists to prevent.
        if str(recipient) != verified:
            raise LiveSenderError(
                "the recipient does not equal the verified owner binding: a caller "
                "cannot redirect this sender, and a bare id could be a chat")
        self.recipient = verified
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


def card_fingerprint(card) -> str:
    """A structural fingerprint of a Card 2.0 document.

    NOT a hash of the bytes: the far end normalises what it stores (`config`,
    key order, and sometimes an `icon`), so a byte hash would report a mismatch
    for a card that is the same card. This folds the parts a reader actually
    sees — schema, header title/template/subtitle, and the ordered tag sequence
    with the KPI column widths — so «the far end holds a different card» is a
    claim about the card, not about a serialiser.
    """
    if not isinstance(card, dict):
        return ""
    header = card.get("header") if isinstance(card.get("header"), dict) else {}
    tags: list[str] = []
    columns: list[int] = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("tag"), str):
                tags.append(node["tag"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(card.get("body"))
    for element in ((card.get("body") or {}).get("elements") or []):
        if isinstance(element, dict) and element.get("tag") == "column_set":
            columns.append(len(element.get("columns") or []))
    view = {"schema": card.get("schema"),
            "title": ((header.get("title") or {}).get("content")),
            "template": header.get("template"),
            "subtitle": ((header.get("subtitle") or {}).get("content")),
            "tags": tags, "columns": columns}
    import hashlib as _hashlib
    return _hashlib.sha256(
        json.dumps(view, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


class ReadbackVerifiedSender:
    """A sender whose acknowledgements are checked against the FAR END.

    `send_card`/`update` are delegated, and then the message is READ BACK. A
    receipt only becomes `DELIVERED` + `remote_verified` when the far end's own
    Card 2.0 fingerprint equals the card this call handed over AND the id is the
    one the call aimed at. Anything else — a mismatched card, a wrong message, a
    refusal, a timeout, a message the API will not hand back — is
    `UNKNOWN_PENDING`: the caller must leave its cursors alone and retry the SAME
    message rather than treat a self-report as a confirmation.
    """

    def __init__(self, inner, *, readback, verify: bool = True):
        self.inner = inner
        self._readback = readback
        self.verify = verify

    def send_card(self, card, *, idempotency_key: str, **_):
        answer = self.inner.send_card(card, idempotency_key=idempotency_key)
        return self._confirm(answer, card, expect_id=None)

    def update(self, request: dict):
        answer = self.inner.update(request)
        return self._confirm(answer, request.get("card"),
                             expect_id=request.get("message_id"))

    def _confirm(self, answer: dict, card, *, expect_id):
        out = dict(answer or {})
        out["evidence"] = "remote"
        provider = out.get("provider") or {}
        if out.get("state") != "DELIVERED":
            return out
        remote_id = str(provider.get("remote_id") or "").strip()
        if not remote_id:
            out["state"] = DELIVERED_UNVERIFIED
            out["why"] = "传输说已送达但没有回消息 id：无法核对，按未确认处理"
            return out
        if expect_id and remote_id != str(expect_id):
            out["state"] = UNKNOWN_PENDING
            out["why"] = "回执指向的消息不是本次 patch 的目标：不推进游标"
            return out
        if not self.verify:
            out["state"] = WAITING_READBACK
            out["why"] = "未启用 readback 核对：停在 WAITING_READBACK，不推进游标"
            return out
        try:
            read = self._readback(remote_id)
        except Exception as exc:                       # noqa: BLE001 - any failure
            out["state"] = UNKNOWN_PENDING             # is «we could not check»
            out["why"] = f"readback 失败（{exc.__class__.__name__}）：不推进游标"
            return out
        if not isinstance(read, dict) or not read.get("available"):
            out["state"] = UNKNOWN_PENDING
            out["why"] = ("远端读回不可用或不是 Card 2.0："
                          + str((read or {}).get("reason") or "unknown"))
            return out
        if card_fingerprint(read.get("card")) != card_fingerprint(card):
            out["state"] = UNKNOWN_PENDING
            out["why"] = "远端持有的卡片与本次提交的结构不一致：不推进游标"
            return out
        out["state"] = "DELIVERED"
        out["evidence"] = REMOTE_VERIFIED
        out["verified_message_id"] = remote_id
        return out


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
