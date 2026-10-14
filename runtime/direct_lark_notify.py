#!/usr/bin/env python3
"""A HumanControlProvider whose OUTBOUND notification transport is lark-cli.

WHY THIS EXISTS
---------------
`runtime/cli.py#_alert_provider` used to construct a BotMux provider for the
alert delivery hop, and that provider declares `human.notification.transport` as
UNKNOWN — `POST /api/trigger` with no `target.chatId` is a SYNTHETIC session
whose reply is handed to a model and explicitly not shown in a chat
(`mcp/human_control.py#notify`, and the measured note on its own capability
table). `runtime/notifications.py#deliver` refuses to call a transport it was
told is not SUPPORTED, so every alert the daemon opened stayed pending and was
recorded UNKNOWN: a receipt that says a model was asked, never that a person was
told. This provider is the transport that can say the second thing.

WHAT IT IS NOT
--------------
It is NOT a replacement for `mcp/human_control.py`. The INBOUND half of human
control — who a message is FROM, and whether an approval is bound to a request
fingerprint — is a different migration with a different precondition: it needs a
Feishu callback whose signature can be verified against a tenant, and until that
exists, treating «we sent a message» as «a person is authenticated» would be the
forgery this boundary exists to refuse. So this provider implements the OUTBOUND
responsibilities and marks the three inbound ones UNSUPPORTED, honestly, rather
than improvising them.

WHAT IT HOLDS
-------------
No credential, and it reads none. It runs the already-authenticated `lark-cli`
through `runtime/message_transport.py`, whose own header says the one file that
knows what `lark-cli` is must not also be the file that decides whether a
delivery is allowed. This module is the wiring between that transport and the
`HumanControlProvider` shape `runtime/notifications.py` calls: it adds no send
path, no retry and no card logic of its own.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

#: The capability name `runtime/notifications.py` probes for. Restated rather
#: than imported only because that module is loaded by name elsewhere; a test
#: asserts the two strings are equal so the spelling cannot drift.
CAPABILITY = "human.notification.transport"

PROVIDER_ID = "provider:direct-lark-human-control"
TRANSPORT = "feishu-direct"

#: The environment key a destination is read from. NOTHING is sent without it:
#: a notification with no recipient is not a notification, and an unaddressed
#: send is exactly the synthetic-session failure this provider replaces. The
#: value carries the same `user:<open_id>` / `chat:<chat_id>` prefix rule the
#: transport enforces.
RECIPIENT_ENV = "SERVEROS_ALERT_RECIPIENT"
IDENTITY_ENV = "SERVEROS_ALERT_IDENTITY"


def _transport():
    """`runtime/message_transport.py`, loaded once under the name it is looked
    up by. Cached BEFORE execution so a caller that pre-registered the module
    (a test standing in for the CLI) gets the same object and not a second copy
    — one file under two names is two classes, the hazard the tree's other
    loaders document."""
    cached = sys.modules.get("serveros_runtime_message_transport")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_message_transport", _HERE / "message_transport.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_runtime_message_transport"] = module
    spec.loader.exec_module(module)
    return module


class DirectLarkUnsupported(RuntimeError):
    """A responsibility this outbound provider does not carry."""


class DirectLarkNotifyFault(RuntimeError):
    """A notification that did not reach the service.

    Raised rather than returned so that `runtime/notifications.py#deliver` records
    `delivered: False`. A provider that returned a failure as a value would have
    its caller read the call itself as the delivery — the false green this whole
    hop exists to remove.
    """


def support(state: str, reason: str | None = None) -> dict:
    entry: dict = {"state": state}
    if reason:
        entry["reason"] = reason
    return entry


class DirectLarkHumanNotifyProvider:
    """`HumanControlProvider`, OUTBOUND only, over the authenticated lark-cli."""

    provider_id = PROVIDER_ID
    transport = TRANSPORT

    def __init__(self, recipient: str, *, identity: str = "bot",
                 cli: str | None = None, timeout: int = 120):
        self.recipient = recipient
        transport = _transport()
        kwargs = {"identity": identity, "timeout": timeout}
        if cli:
            kwargs["cli"] = cli
        # Constructed eagerly so a malformed recipient fails at build time with
        # the transport's own sentence, rather than at the first alert.
        self.sender = transport.sender(recipient, **kwargs)

    @classmethod
    def from_env(cls, environ=None) -> "DirectLarkHumanNotifyProvider":
        """Build from the environment, or raise naming the missing variable.

        A missing destination is a refusal and not a default: this provider has
        no way to guess who should be told, and the alternative — sending to
        whoever happens to be configured on the host — is how an alert about one
        node's disk lands in an unrelated chat.
        """
        environ = environ if environ is not None else __import__("os").environ
        recipient = str(environ.get(RECIPIENT_ENV) or "").strip()
        if not recipient:
            raise DirectLarkUnsupported(
                f"{RECIPIENT_ENV} is not set, so this provider has no destination. "
                f"A notification to nobody is not a notification; set it to "
                f"user:<open_id> or chat:<chat_id>.")
        identity = str(environ.get(IDENTITY_ENV) or "bot").strip() or "bot"
        return cls(recipient, identity=identity)

    # -- the outbound responsibility ---------------------------------------

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict:
        """Carry ServerOS's sentence to the receiver. The one thing it does.

        On success it returns the send's own receipt (the message id the service
        assigned). On any failure the transport raises and this re-raises a
        `DirectLarkNotifyFault`, so the caller records a delivery that did not
        arrive as one that did not arrive.
        """
        try:
            answer = self.sender.send_text(text, idempotency_key=idempotency_key)
        except Exception as exc:  # noqa: BLE001 - the transport's reason is the answer
            raise DirectLarkNotifyFault(
                f"{type(exc).__name__}: {exc}") from exc
        if answer.get("state") != "DELIVERED":
            raise DirectLarkNotifyFault(answer.get("why") or "the send did not arrive")
        provider = answer.get("provider") or {}
        return {
            "provider": self.provider_id,
            "transport": self.transport,
            "conversation_ref": conversation_ref,
            "delivered": True,
            "remote_id": provider.get("remote_id"),
            "note": ("lark-cli carried the sentence; ServerOS supplied the words, "
                     "the service rendered them"),
        }

    # -- what this provider cannot do (inbound) ----------------------------

    def _inbound_gap(self, what: str) -> "DirectLarkUnsupported":
        return DirectLarkUnsupported(
            f"{what} is INBOUND and this provider is outbound-only. Who a message "
            f"is FROM and whether an approval is bound to a request fingerprint "
            f"cannot be established by sending a message, and treating a sent "
            f"message as an authenticated person is the forgery this boundary "
            f"refuses. Inbound identity stays with the reviewed BotMux "
            f"HumanControlProvider until a Feishu callback whose signature can be "
            f"verified replaces it.")

    def identify(self, *, conversation_ref=None, message_ref=None):
        raise self._inbound_gap("human.identity.verify")

    def observe(self, *, device_ref: str) -> dict:
        raise self._inbound_gap("observe")

    def conversations(self, *, device_ref: str) -> list[dict]:
        raise self._inbound_gap("conversations")

    def request_approval(self, *, conversation_ref: str, question: dict,
                         idempotency_key: str) -> dict:
        raise self._inbound_gap("approval transport")

    # -- what this provider can do -----------------------------------------

    def capabilities(self) -> dict:
        """The six responsibilities, each with its own state and its own reason.

        `human.notification.transport` is SUPPORTED and that is a measured
        claim, not a hopeful one: it is the `im +messages-send` path the node's
        own status card and alert digest already use, observed to deliver. The
        inbound three are UNSUPPORTED rather than UNKNOWN because this provider
        deliberately carries no inbound surface at all — the difference between
        «we did not implement it» and «nobody established it» is kept where the
        caller can see it.
        """
        return {
            "provider": self.provider_id,
            "transport": self.transport,
            "capabilities": {
                CAPABILITY: support(
                    "SUPPORTED",
                    "carried by lark-cli `im +messages-send`, the path this node's "
                    "status card and alert digest already send through; the "
                    "credential is the CLI's own and this provider holds none"),
                "human.identity.verify": support(
                    "UNSUPPORTED",
                    "outbound-only: verifying a SENDER needs an inbound surface "
                    "this provider does not have"),
                "human.approval.binding": support(
                    "UNSUPPORTED",
                    "outbound-only: an approval needs an ANSWER bound to a "
                    "request fingerprint, which sending cannot produce"),
                "human.interactive.rendering": support(
                    "UNSUPPORTED",
                    "this provider sends a sentence; card rendering is "
                    "`runtime/message_transport.py` + `runtime/cards.py`"),
            },
            "measured_by": "runtime/direct_lark_notify.py — see the module docstring",
        }


def notify_via(recipient: str, **kwargs) -> DirectLarkHumanNotifyProvider:
    """The provider, ready to hand to `runtime/notifications.py#deliver`."""
    return DirectLarkHumanNotifyProvider(recipient, **kwargs)


def main(argv=None):  # pragma: no cover - hand check
    import argparse
    import json
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--recipient", default="")
    args = parser.parse_args(argv)
    if args.capabilities:
        provider = (DirectLarkHumanNotifyProvider(args.recipient)
                    if args.recipient else DirectLarkHumanNotifyProvider.from_env())
        json.dump(provider.capabilities(), sys.stdout, ensure_ascii=False,
                  indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    parser.error("nothing to do; pass --capabilities")


if __name__ == "__main__":
    raise SystemExit(main())
