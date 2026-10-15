"""Where a Core-Lite document is ADMITTED into Fleet — and where it is refused.

`fleet_mcp/core_lite_readback.py` reads one document and decides whether it may
be shown as one device's. This module decides something further upstream: *may
this document be attributed to that device at all?* The answer comes from the
provider route and the binding, and from nothing the document says.

The sentence this module exists to make impossible
--------------------------------------------------
**A provider answering `health().reachable == True` is not evidence that a
Core-Lite document came back over it, or that it describes the device the route
reaches.** Those are three different questions:

| question | answered by | never by |
|---|---|---|
| is the provider's transport up? | `ProviderHealth` | — |
| did a Core-Lite document arrive over an authenticated route? | this module, from `CoreLiteFetch.route` | the health probe |
| does that document describe this device? | `core_lite_readback`, from the route's `device_ref` | the document's `node` |

Collapsing the first into the second is how a device ends up reported as
Core-connected because a TCP connection succeeded. So `route.authenticated` is
an explicit field a source must set, and this module never derives it.

No default source, and no default YES
-------------------------------------
`admit_core_lite(source=None, ...)` returns UNKNOWN with no observations.
There is no configuration in which "nobody wired a source" reads as a device
being fine, and no live Node exposes a trusted Core API today, so the default is
the state every device is actually in.

`UNKNOWN` and `NOT_CONNECTED` are kept apart on purpose: the first is "nothing
was established about this device", the second is "something was established,
and it was not this". A route that authenticated to a different device is the
second, and reporting it as merely unknown would invite a retry that can only
fail the same way.

Nothing here opens a socket, runs a command, or builds a URL. A source is a
callable Fleet is handed; what it does inside is that provider's business, and
the seam is deliberately too narrow to grow an egress of its own.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from . import core_lite_readback as readback

#: Why a device has no admitted reading. Closed, and every member means a
#: different thing — a single "unavailable" would make a misconfigured source
#: look like an offline device, and an operator would go looking in the wrong
#: place.
ROUTE_STATES = (
    "NOT_CONFIGURED",    # no source was injected at all
    "SOURCE_FAILED",     # the source raised; self-inflicted, not the device's
    "UNAUTHENTICATED",   # the source has no authenticated route to this device
    "DEVICE_MISMATCH",   # the route says it reaches a different device
    "NO_DOCUMENT",       # authenticated, right device, nothing came back
    "VERIFIED",          # a route was authenticated AND named this device
)


#: Exception type names this module is willing to REPORT. A class name is chosen
#: by whoever writes the class, and a source is third-party code, so nothing off
#: this list is echoed: a source that raised
#: `type("https://host/?token=...", (Exception,), {})` must not reach Fleet's
#: output through the error path. The list is closed on purpose and an
#: unrecognised name is reported as `Exception`.
SAFE_FAILURE_NAMES = frozenset({
    "Exception", "RuntimeError", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "LookupError", "StopIteration",
    "ArithmeticError", "NotImplementedError", "OSError", "IOError",
    "TimeoutError", "ConnectionError", "FleetRefusal",
})


@dataclass(frozen=True)
class CoreLiteRoute:
    """What a provider says it actually established.

    `detail` is the provider's OWN diagnostic and is **never published**. It
    exists so a provider has somewhere to put a note for its own logs; this
    module drops it at the boundary in every branch, and a test puts a distinct
    canary in it for each outcome and requires zero bytes of any of them to
    appear in the summary. An earlier revision published it verbatim, which made
    the summary a second, unprojected channel beside the document reader — the
    leak the field now exists as a decoy for rather than a carrier of.

    `authenticated` is not a hint and not a synonym for reachable: it means the
    provider opened a route it can attribute to `device_ref`, and is willing to
    say so. It must be a real bool — a truthy string is refused rather than
    honoured, because this is an adapter boundary and "yes" is not a claim.
    """

    device_ref: str
    authenticated: bool
    detail: str = ""


@dataclass(frozen=True)
class CoreLiteFetch:
    """One source's answer: a document, and the route it arrived on.

    `route` is required even when `document` is `None`. A source that returns a
    document without saying which route carried it has not answered the
    attribution question, and this module treats that as a refusal rather than
    filling the gap with the document's own `node`.
    """

    document: Any | None
    route: CoreLiteRoute | None


@runtime_checkable
class CoreLiteStatusSource(Protocol):
    """A provider-side reader. Deliberately a callable, and deliberately narrow.

    One argument: the device the caller already has a route to. No URL, no
    command, no options — a source that needs those is a transport, and a second
    transport is a second management protocol wearing this seam's clothes.
    """

    def fetch(self, device_ref: str) -> CoreLiteFetch:
        ...


def _placeholder(device_ref: str, binding_account: str | None,
                 received_at: dt.datetime, *, state: str, reason: str,
                 connected: str = "UNKNOWN",
                 failure: str | None = None) -> dict[str, Any]:
    """One shape for every refusal, so a caller never has two to handle.

    `connected` distinguishes two different kinds of "no": `UNKNOWN` is "nothing
    was established about this device", and `NOT_CONNECTED` is "something was
    established, and it was not this". A route that authenticated to a different
    device is the second — reporting it as merely unknown would invite a retry
    that can only fail again.
    """
    summary = readback.unconnected_summary(
        expected_device_ref=device_ref, binding_account=binding_account,
        received_at=received_at, reason=reason)
    summary["connected"] = connected
    summary["route"] = {
        "state": state,
        "device_ref": device_ref,
        "authenticated": False,
        # Every value below is authored HERE or drawn from a closed set above.
        # None of them is a string a provider chose.
        "reason": reason,
        "failure": failure,
    }
    return summary


def admit_core_lite(
    source: CoreLiteStatusSource | None,
    *,
    device_ref: str,
    binding_account: str | None,
    received_at: dt.datetime,
    max_age_seconds: float = 60.0,
) -> dict[str, Any]:
    """Admit one device's Core-Lite reading, or return why it may not be shown.

    Every early return is a placeholder with `connected` UNKNOWN or
    NOT_CONNECTED and no observations. None of them is a zero, an empty reading,
    or a healthy device with nothing to say.

    `binding_account` is the OS account the Fleet binding declares — or `None`
    when the binding declares none, which is a real state and produces
    `principal.state == UNKNOWN` downstream rather than a pass.
    """
    if source is None:
        return _placeholder(
            device_ref, binding_account, received_at,
            state="NOT_CONFIGURED",
            reason="no Core-Lite source is wired for this surface")

    try:
        fetch = source.fetch(device_ref)
    except Exception as exc:  # noqa: BLE001 - a source is third-party code
        # A source that raises is a Fleet-side fault: it says nothing about the
        # device. Reported as such, and never as the device being down.
        #
        # The class name is REPORTED only if it is one this module names; a
        # class is free to be called anything, and `type(exc).__name__` is a
        # string its author chose.
        name = type(exc).__name__
        return _placeholder(
            device_ref, binding_account, received_at,
            state="SOURCE_FAILED",
            reason="the Core-Lite source failed to answer",
            failure=name if name in SAFE_FAILURE_NAMES else "Exception")

    if fetch is None or fetch.route is None:
        return _placeholder(
            device_ref, binding_account, received_at,
            state="UNAUTHENTICATED", connected="NOT_CONNECTED",
            reason="the source returned no route to attribute the reading to")

    route = fetch.route
    if not isinstance(route.device_ref, str) or not route.device_ref:
        return _placeholder(
            device_ref, binding_account, received_at,
            state="UNAUTHENTICATED", connected="NOT_CONNECTED",
            reason="the route did not name a device as a string")
    # A real bool, not a truthy one. `authenticated="yes"` is a string its author
    # chose, and honouring it would let a provider authenticate itself by
    # spelling.
    if not isinstance(route.authenticated, bool):
        return _placeholder(
            device_ref, binding_account, received_at,
            state="UNAUTHENTICATED", connected="NOT_CONNECTED",
            reason="the route did not state authenticity as a boolean")
    if not route.authenticated:
        return _placeholder(
            device_ref, binding_account, received_at,
            state="UNAUTHENTICATED", connected="NOT_CONNECTED",
            reason="the source has no authenticated route to this device")
    if route.device_ref != device_ref:
        # The route is real and it reaches something else. Reading the document
        # as this device's would be the substitution the whole module exists to
        # refuse.
        return _placeholder(
            device_ref, binding_account, received_at,
            state="DEVICE_MISMATCH", connected="NOT_CONNECTED",
            reason="the authenticated route reaches a different device")
    if fetch.document is None:
        return _placeholder(
            device_ref, binding_account, received_at,
            state="NO_DOCUMENT",
            reason="the authenticated route returned no document")

    # Only here — an authenticated route that NAMED this device — may the
    # document be read. `verified_route=True` is passed because the route, not
    # the document, established it.
    summary = readback.readback(
        fetch.document,
        expected_device_ref=device_ref,
        binding_account=binding_account,
        received_at=received_at,
        verified_route=True,
        max_age_seconds=max_age_seconds,
    )
    summary["route"] = {
        "state": "VERIFIED",
        "device_ref": device_ref,
        "authenticated": True,
        "reason": None,
        "failure": None,
    }
    return summary


__all__ = [
    "ROUTE_STATES",
    "SAFE_FAILURE_NAMES",
    "CoreLiteRoute",
    "CoreLiteFetch",
    "CoreLiteStatusSource",
    "admit_core_lite",
]
