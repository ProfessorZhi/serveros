#!/usr/bin/env python3
"""§10 — ServerOS semantic events, and getting them in front of a person.

WHO DECIDES WHAT
----------------
The event's MEANING belongs to ServerOS. `turn.failed` means a turn failed, and
that is a fact about an Agent Session, established here, from the provider's own
answer. The event's RENDERING belongs to the HumanControlProvider, which knows
whether a transport carries a card, a message, a thread reply or nothing at all.

So the split is: this module decides *what happened and what it is called*, and
the provider decides *how a person is told*. A provider that decided the first
would be deciding ServerOS truth from inside an adapter, and an adapter that
reclassified a provider refusal as a policy denial would do it here, where no
test in `mcp/` would look.

WHAT A CAPABILITY GAP LOOKS LIKE
--------------------------------
`human.notification.transport` can be SUPPORTED, UNSUPPORTED or UNKNOWN, and all
three are ordinary answers rather than errors. §21 requires the release to be
shippable with an UNSUPPORTED capability as long as it is honestly reported, so
an event that cannot be delivered returns `delivered: False` with the state that
stopped it — never an exception a caller has to catch, and never a silent
success. §10's rule follows from the same place: do not route around the
capability contract.

Note what is deliberately absent: no queue, no retry loop and no scheduler. A
notification that failed is reported as failed, and a caller that wants another
attempt asks for one. A retry loop inside this module would be a second, quieter
delivery path with its own opinion about how many times a person should be told.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

CONTRACT = _HERE / "notification-contract.yaml"
CAPABILITY = "human.notification.transport"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The device identity, borrowed rather than restated. `fleet/transport.py` owns
# what a device IS; a second pattern here would be a second fleet, and the two
# would disagree the first time an id form changed — with the notification path
# being the one nobody re-tests.
def _transport():
    return _load("serveros_fleet_transport", _ROOT / "fleet" / "transport.py")


def contract() -> dict:
    import yaml
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise NotificationError(f"{CONTRACT} must contain a mapping")
    return data


class NotificationError(RuntimeError):
    pass


def events() -> tuple[str, ...]:
    """The closed event vocabulary. §10 names nine and this is those nine."""
    return tuple(contract()["events"]["values"])


def support_states() -> tuple[str, ...]:
    return tuple(contract()["support"]["values"])


@dataclass(frozen=True)
class Event:
    """One thing that happened, in ServerOS's own words.

    `detail` is for a person and is never a place to put a raw provider payload:
    a notification leaves this machine and lands in a chat, so anything routed
    through it has left the evidence boundary. The fields that identify the
    subject — session, run, device, actor — are typed and separate for that
    reason, so a renderer never has to parse meaning out of prose.
    """

    event: str
    device_ref: str
    at: str
    correlation_id: str
    session_ref: str | None = None
    run_ref: str | None = None
    item_ref: str | None = None
    actor_ref: str | None = None
    reason: str | None = None
    detail: str | None = None
    # A subject that is none of the typed ones above and is still the most
    # specific stable thing this event is about — `filesystem:/` for a disk
    # alert, `runtime-group:codex-session:…` for a runaway. Added in W8 because
    # the alternative was worse: an alert about a full filesystem has no
    # session, no run and no Item, so `render()` fell back to the DEVICE and
    # every alert on one node read as if it were about the node. It is typed
    # rather than folded into `detail` because a renderer must never have to
    # parse meaning out of prose, and because the idempotency key has to
    # include it — two disks on one device are two notifications, not one.
    subject_ref: str | None = None

    def __post_init__(self) -> None:
        if self.event not in events():
            raise NotificationError(
                f"{self.event!r} is not a ServerOS semantic event; "
                f"known={list(events())}")
        text = str(self.device_ref or "")
        try:
            _transport().DeviceRef.parse(text)
        except Exception as exc:
            raise NotificationError(
                f"{self.device_ref!r} is not a device ref ({exc}); an event with "
                f"no stable subject cannot be reported about one") from None

    def idempotency_key(self) -> str:
        """One delivery per event occurrence, derived from the event itself.

        Derived rather than supplied, because a caller that minted a fresh key
        per attempt would turn every retry into a second notification — which is
        the failure the key exists to prevent, reintroduced by the caller. The
        correlation id is part of it: two turns that failed identically on the
        same session are two events, not one.
        """
        subject = (self.session_ref or self.run_ref or self.item_ref
                   or self.subject_ref or "-")
        return f"notify:{self.event}:{self.device_ref}:{subject}:{self.correlation_id}"

    def show(self) -> dict:
        return {
            "event": self.event, "device_ref": self.device_ref, "at": self.at,
            "correlation_id": self.correlation_id, "session_ref": self.session_ref,
            "run_ref": self.run_ref, "item_ref": self.item_ref,
            "actor_ref": self.actor_ref, "reason": self.reason, "detail": self.detail,
            "subject_ref": self.subject_ref,
        }


def severity(event: str) -> str:
    """INFO / ATTENTION / ACTION. Read from the contract, not decided here."""
    return contract()["events"]["severity"].get(event, "INFO")


def render(event: Event) -> str:
    """The sentence a person reads.

    ServerOS authors this because ServerOS knows what the event means. A
    provider left to compose the text would be writing the sentence that carries
    the claim, and a provider that softened "turn failed" into "the agent is
    thinking" would be editing the truth at the last hop.

    It contains no provider payload, no path, no credential and no raw error
    body. `reason` is a reason word from the closed vocabulary, and `detail` is
    a sentence the caller wrote for a person.
    """
    template = contract()["events"]["templates"].get(event.event)
    if not template:
        raise NotificationError(
            f"{event.event!r} is a declared event with no template; an event "
            f"nobody wrote a sentence for cannot be delivered as one")
    subject = (event.session_ref or event.run_ref or event.item_ref
               or event.subject_ref or event.device_ref)
    line = template.format(subject=subject, device=event.device_ref,
                           reason=event.reason or "no reason given")
    if event.detail:
        # A one-line detail is appended; a MULTI-line one gets its own block.
        #
        # A person reads this on a phone, and the alert detail is a short list
        # of labelled lines rather than prose. Joining that onto the headline
        # would put the first line after an em dash and the rest wherever the
        # wrapping happened to fall, which is how "CPU: 2.8 cores" ends up
        # sharing a line with the headline and "not auto-terminated" ends up
        # invisible at the bottom of a fold. The headline stays one line and the
        # block stays whole.
        separator = "\n" if "\n" in event.detail else " — "
        line = f"{line}{separator}{event.detail}"
    return line


@dataclass(frozen=True)
class Delivery:
    """What happened when we tried, including when we did not try."""

    delivered: bool
    event: str
    provider: str
    state: str
    reason: str | None = None
    detail: dict = field(default_factory=dict)

    def show(self) -> dict:
        return {"delivered": self.delivered, "event": self.event,
                "provider": self.provider, "capability": CAPABILITY,
                "state": self.state, "reason": self.reason,
                "detail": dict(self.detail)}


def capability_of(provider) -> tuple[str, str | None]:
    """(SUPPORTED | UNSUPPORTED | UNKNOWN, the provider's own reason).

    A provider that raises while being asked contributes UNKNOWN, not
    UNSUPPORTED. The two are different repairs — one is "this transport cannot",
    the other is "nobody established whether it can" — and collapsing them here
    would report an untested transport as a permanent limitation.
    """
    try:
        declared = provider.capabilities()
    except Exception as exc:
        return "UNKNOWN", f"the provider did not answer a capability probe: {type(exc).__name__}"
    entry = ((declared or {}).get("capabilities") or {}).get(CAPABILITY)
    if entry is None:
        # Absent from a table the provider DID publish is a "no" for the hole.
        # A missing table is handled above, where nobody asked.
        return "UNSUPPORTED", f"{CAPABILITY} is not in the provider's capability table"
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return str(entry.get("state") or "UNKNOWN"), entry.get("reason")
    return "UNKNOWN", f"unreadable capability entry for {CAPABILITY}"


def deliver(provider, event: Event, *, conversation_ref: str) -> Delivery:
    """Hand one rendered event to a HumanControlProvider. Never raises for "no".

    The capability is checked BEFORE the call, so an UNSUPPORTED transport is
    never asked to do something it has said it cannot, and a provider is never
    put in the position of failing at a thing its own table already declined.
    """
    state, reason = capability_of(provider)
    provider_id = str(getattr(provider, "provider_id", "provider:unknown"))

    if state != "SUPPORTED":
        # Not an error, and not a failure to report as one. The event happened;
        # this transport cannot carry it; the caller is told exactly that.
        return Delivery(False, event.event, provider_id, state,
                        reason or f"{CAPABILITY} is {state}",
                        {"rendered": render(event)})

    if not conversation_ref:
        return Delivery(False, event.event, provider_id, "SUPPORTED",
                        "no conversation_ref was supplied; a notification to "
                        "nobody is not a notification",
                        {"rendered": render(event)})

    try:
        answer = provider.notify(conversation_ref=conversation_ref,
                                 text=render(event),
                                 idempotency_key=event.idempotency_key())
    except Exception as exc:
        return Delivery(False, event.event, provider_id, "SUPPORTED",
                        f"{type(exc).__name__}: {exc}",
                        {"rendered": render(event)})
    return Delivery(True, event.event, provider_id, "SUPPORTED", None,
                    {"rendered": render(event), "provider_answer": answer})


def events_for(reason: str) -> tuple[str, ...]:
    """Which events a refusal reason can produce.

    The bridge from the router's vocabulary to notifications, so that a caller
    does not hand-map reason words onto events at each site. An unmapped reason
    produces nothing, which is the honest outcome: a refusal nobody wrote a
    notification for is a refusal nobody is told about, and inventing a generic
    "something went wrong" would be a sentence with the useful half removed.
    """
    mapping = contract()["from_reason"]
    return tuple(mapping.get(reason) or ())


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--event")
    parser.add_argument("--device")
    parser.add_argument("--reason")
    args = parser.parse_args(argv)
    if not args.event:
        parser.error("--event is required: this tool will not invent one")
    event = Event(event=args.event, device_ref=args.device or "", at="",
                  correlation_id="cli", reason=args.reason)
    json.dump({"rendered": render(event), "severity": severity(args.event),
               "idempotency_key": event.idempotency_key()},
              sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
