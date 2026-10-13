"""Typed refusals for the Fleet MCP surface.

Why a closed set of codes rather than free-form strings
-------------------------------------------------------
A caller that has to pattern-match on prose is a caller that will silently
stop matching. Every refusal this surface can produce names one of the codes
below, and the code is the part a caller branches on; `detail` is for a human.

The set is deliberately small and each entry distinguishes a *different
response*: an unknown device is a registry question, an unreachable provider is
a transport question, a denied scope is a policy question, and a BUSY session
is a retry question. Collapsing any two of them is how "I may not" becomes
"I cannot", or how "try again later" becomes "this is broken".
"""
from __future__ import annotations

from typing import Any

# -- input shape ------------------------------------------------------------
BAD_REQUEST = "BAD_REQUEST"
"""The call did not name something this surface can act on (missing argument,
malformed session ref, unknown tool)."""

# -- identity ---------------------------------------------------------------
UNKNOWN_DEVICE = "UNKNOWN_DEVICE"
"""The device ref does not resolve to an enrolled device in the fleet registry.

A hostname, computer name or transport locator produces this too: those are
routes, and a route is not an identity. `fleet_list_devices` is the authority
on what this surface will accept."""

BAD_SESSION_REF = "BAD_SESSION_REF"
"""A session reference did not carry all three parts of
`device_ref + provider + native_session_id`."""

SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
"""The device resolved and answered, and it does not know that session.

Different from UNKNOWN_DEVICE and from PROVIDER_UNREACHABLE by construction:
here we asked and got a real 'no'."""

# -- reachability -----------------------------------------------------------
PROVIDER_UNREACHABLE = "PROVIDER_UNREACHABLE"
"""The device's CloudCLI provider did not answer.

This is a statement about the route this runtime has, not about the device:
the session may be healthy and visible from another plane."""

PROVIDER_UNAUTHORIZED = "PROVIDER_UNAUTHORIZED"
"""The provider answered 401/403. The device is reachable; the credential is
not usable. Two different people fix these."""

PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
"""The provider accepted the connection and did not answer in time.

Kept apart from PROVIDER_UNREACHABLE for the reason `mcp/node_mcp.py` keeps
`DaemonBusy` apart from `DaemonUnavailable`: a busy runtime is not a missing
one, and a caller that retries the first and re-enrols the second needs them
to be different words."""

# -- state ------------------------------------------------------------------
SESSION_BUSY = "SESSION_BUSY"
"""The session is executing a run and refused a new message.

A retry question, not an error. `session_send` on CloudCLI returns a queued
message id in this case; this surface reports that rather than pretending the
send landed."""

RUN_NOT_FOUND = "RUN_NOT_FOUND"
"""No run with that id is known for that session."""

RUN_UNSETTLED = "RUN_UNSETTLED"
"""The run exists and has not reached a terminal state within the wait."""

# -- policy -----------------------------------------------------------------
SCOPE_DENIED = "SCOPE_DENIED"
"""The presented credential does not carry the scope this tool requires.

Never rendered as 'not found'. A caller that cannot tell a denial from an
absence will go looking for the wrong thing."""

WRITE_NOT_GRANTED = "WRITE_NOT_GRANTED"
"""A control call was admitted to the surface and refused by policy.

The surface always distinguishes this from a provider-level failure: a
provider refusal is the device's answer, this is ours."""

APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
"""The target session is not one this surface is authorized to drive.

Control into a session is granted per session, by an operator, in the local
binding — never by the caller naming a session id."""

RATE_LIMITED = "RATE_LIMITED"
"""The credential exceeded its admitted request rate for this window."""

IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
"""This `idempotency_key` already produced a run; the recorded run is returned
instead of sending a second message.

A distinct code rather than a silent success, because a caller must be able to
tell "I sent it once and this is that run" from "I just sent it now"."""

# -- surface ----------------------------------------------------------------
INTERNAL = "INTERNAL"
"""A defect in this surface. Never used for a provider's behaviour."""

OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
"""A mirrored CloudCLI answer was larger than this surface will relay.

It is a refusal, not a truncation: a silently shortened answer looks complete
and is not, so a caller could act on a transcript that is missing its middle.
The error names the device, the verb and the measured size, and carries NO
remote text — the oversized body is measured and dropped, never copied into
the error or the audit record."""


class FleetRefusal(Exception):
    """A typed, expected refusal. Carries a code a caller can branch on."""

    def __init__(self, code: str, detail: str, **extra: Any) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.extra = extra

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.extra:
            payload.update(self.extra)
        return payload


def refuse(code: str, detail: str, **extra: Any) -> FleetRefusal:
    return FleetRefusal(code, detail, **extra)
