#!/usr/bin/env python3
"""Who may be shown which machine: the audience verdict, and nothing else.

WHY THIS IS ITS OWN MODULE
--------------------------
Two very different callers need this rule and neither may import the other:
`fleet_monitor_source` builds a card from readings and reaches I/O, and
`fleet_monitor_plan` is the place a send starts from and must stay free of I/O
(its own tests assert that). A rule living in either would drag the other's
dependencies across the line. So the rule lives here, imports only the standard
library, and both load it under ONE name.

WHAT WENT WRONG THE FIRST TIME, AND WHY THIS SHAPE FIXES IT
-----------------------------------------------------------
The first version let a caller satisfy the gate with a plain dictionary —
`{"ok": True}` — handed to `apply_plan` alongside a real transport. That is not
a gate, it is a suggestion: any caller, or any bug, could mint the word «yes»
and the private machine's row would go to a company channel. A boolean a caller
WRITES is not evidence that anybody CHECKED anything.

So a verdict is now a TYPED, SIGNED instance:

  * it is an `AudienceVerdict` and not a dict, so `apply_plan` can refuse
    anything else by type rather than by reading a field;
  * it carries the RECIPIENT it was issued for, the DEVICE SET it was issued
    over (with a canonical signature of that set), and the CANONICAL HASH OF THE
    CARD it authorizes — so a verdict for a harmless card cannot authorize the
    card with the private row on it, and a verdict for one recipient cannot be
    spent on another;
  * it is HMAC'd with a per-process key generated at import, so a hand-built
    instance is refused unless its author went to the module's private state to
    forge it — which is a deliberate act this file makes visible rather than a
    default it makes available.

HONEST LIMIT, STATED RATHER THAN PAPERED OVER. Python has no secrets from code
running in the same interpreter: a caller who imports `_VERDICT_KEY` can sign
anything. What this module removes is the ACCIDENTAL bypass — the `ok=True`
dictionary a helper handed out, and the corporate default that answered «yes» to
everyone. It does not pretend to stop a caller who deliberately forges a
verdict, and it does not have to: such a caller is not being stopped by a gate,
they are writing the bypass themselves, and the difference is exactly the one
this module exists to make legible.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field

#: The two recipient kinds, spelled the way `runtime/message_transport.py`'s
#: flags are. A test binds the two spellings rather than trusting this comment.
RECIPIENT_KINDS = ("user", "chat")

#: Who may issue a verdict, and what each issuer is FOR. `policy` is the real
#: one: an evaluated device×recipient decision. `preview` is the local-render
#: one and is accepted by `apply_plan` ONLY for the in-memory mock transport —
#: a preview that could authorize a real send would be the `ok=True` dictionary
#: again, wearing a class.
ISSUER_POLICY = "policy"
ISSUER_PREVIEW = "preview"
ISSUERS = (ISSUER_POLICY, ISSUER_PREVIEW)

#: The per-process signing key. Generated at import and never written down: a
#: verdict minted outside this module has to guess it, and cannot.
_VERDICT_KEY = secrets.token_bytes(32)


class AudienceRefused(RuntimeError):
    """A destination, a policy document, or a verdict this module cannot accept."""


def canonical(blob) -> bytes:
    """One object, one byte string. The same canonicalisation the tree uses."""
    return json.dumps(blob, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def device_set_signature(device_refs) -> str:
    """A stable signature of a device SET, order-insensitive.

    The device list is what the audience rule is about, so it has to be inside
    the verdict and comparable later. Sorted first, so two spellings of the same
    set agree; hashed, so the verdict binds the set without carrying a paragraph
    of a card's worth of identifiers around.
    """
    refs = sorted(str(r) for r in (device_refs or ()))
    return hashlib.sha256(canonical(refs)).hexdigest()


def parse_recipient(recipient) -> tuple:
    """`user:<open_id>` / `chat:<chat_id>` -> `(kind, value)`.

    THE REFUSAL DOES NOT ECHO WHAT IT WAS GIVEN. An earlier version put the raw
    recipient into the exception, and a caller that logged that exception put an
    unvalidated, possibly token-shaped identifier into a log or onto a card. The
    refused value is the very thing we have not established anything about, so
    the sentence describes the SHAPE that is expected and says nothing about the
    value that was seen.
    """
    kind, separator, value = str(recipient or "").partition(":")
    if not separator or kind not in RECIPIENT_KINDS or not value:
        raise AudienceRefused(
            "接收人必须写成 user:<open_id> 或 chat:<chat_id>，收到的值不符合这个"
            "形状。这里刻意不回显收到的原文——未经校验的标识会被日志和卡片原样复制"
            "出去，而它正是我们还没有对之建立任何结论的那个东西。不加前缀就等于让"
            "这里替调用方猜，而猜错的后果是把一台机器的读数发给了不该看到它的人。")
    return kind, value


def recipient_kind(recipient) -> str:
    """`user` / `chat` / `?` — the part safe to log. NEVER the value."""
    try:
        return parse_recipient(recipient)[0]
    except AudienceRefused:
        return "?"


@dataclass(frozen=True)
class AudienceVerdict:
    """A signed answer to «may this recipient be shown these devices».

    Not a dictionary, and not constructible by writing `ok=True`: the `mac` is
    checked in `__post_init__` against a key that only this module holds, so an
    instance that was not issued by `issue_verdict` raises where it is built
    rather than being believed where it is used.
    """

    audience: str
    device_refs: tuple
    device_signature: str
    allowed: tuple
    denied: tuple
    ok: bool
    issuer: str
    card_signature: str | None = None
    reason: str | None = None
    mac: str = field(default="", compare=False)

    def __post_init__(self):
        if self.issuer not in ISSUERS:
            raise AudienceRefused(
                f"issuer {self.issuer!r} is not one of {ISSUERS}")
        if not hmac.compare_digest(str(self.mac), _mac(self.payload())):
            raise AudienceRefused(
                "verdict 的签名不匹配：它不是一个由本模块签发的判决。用一个自己写的"
                "「通过」来满足发送前的门禁，等于把门禁变成建议——这正是这个类型"
                "存在的理由。请用 authorize_devices（或评测用的 preview_gate）。")

    def payload(self) -> dict:
        return {"audience": self.audience,
                "device_refs": list(self.device_refs),
                "device_signature": self.device_signature,
                "allowed": list(self.allowed), "denied": list(self.denied),
                "ok": bool(self.ok), "issuer": self.issuer,
                "card_signature": self.card_signature, "reason": self.reason}

    @property
    def audience_kind(self) -> str:
        """The only part of the recipient safe to render or log."""
        return recipient_kind(self.audience)

    def with_card_signature(self, card_signature: str) -> "AudienceVerdict":
        """The same verdict, bound to a card. Re-signed; not mutated."""
        return issue_verdict(audience=self.audience, device_refs=self.device_refs,
                             allowed=self.allowed, denied=self.denied,
                             issuer=self.issuer, card_signature=card_signature,
                             reason=self.reason)


def _mac(payload: dict) -> str:
    return hmac.new(_VERDICT_KEY, canonical(payload), hashlib.sha256).hexdigest()


def issue_verdict(*, audience, device_refs, allowed, denied, issuer,
                  card_signature=None, reason=None) -> AudienceVerdict:
    """THE constructor. Two producers call it and nothing else may.

    `authorize_devices` (a policy decision) and `preview_gate` (a local render)
    are those two; a third caller is a third rule, and the point of a single
    constructor is that there is one place a verdict comes from.
    """
    refs = tuple(sorted(str(r) for r in (device_refs or ())))
    allowed_t = tuple(sorted(str(r) for r in (allowed or ())))
    denied_t = tuple(sorted(str(r) for r in (denied or ())))
    card_sig = str(card_signature) if card_signature is not None else None
    # The payload is built in the SAME shape `AudienceVerdict.payload()` returns,
    # so the mac signed here is exactly the one `__post_init__` recomputes.
    payload = {"audience": str(audience or ""), "device_refs": list(refs),
               "device_signature": device_set_signature(refs),
               "allowed": list(allowed_t), "denied": list(denied_t),
               "ok": not denied_t, "issuer": issuer,
               "card_signature": card_sig, "reason": reason}
    return AudienceVerdict(
        audience=payload["audience"], device_refs=refs,
        device_signature=payload["device_signature"], allowed=allowed_t,
        denied=denied_t, ok=payload["ok"], issuer=issuer,
        card_signature=card_sig, reason=reason, mac=_mac(payload))


def preview_gate(device_refs=(), *, audience: str | None = None) -> AudienceVerdict:
    """A PREVIEW verdict: for rendering a card locally, never for a real send.

    It says «no audience rule applies because nothing is going anywhere» and it
    is accepted by `apply_plan` ONLY when the transport is the in-memory mock.
    Handed to a real or a custom transport it is refused by issuer, which is why
    the older `open_gate()` — a plain `{"ok": True}` any caller could spend on a
    real send — was removed rather than renamed.
    """
    refs = tuple(str(r) for r in device_refs or ())
    return issue_verdict(audience=audience or "", device_refs=refs,
                         allowed=refs, denied=(), issuer=ISSUER_PREVIEW,
                         card_signature=None,
                         reason="本地预览：不发送，因此不适用收件人授权")
