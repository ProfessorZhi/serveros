"""The Fleet surface's scope vocabulary — defined once, never copied.

CloudCLI's own access-token module states the rule this module now obeys: the
scope list has a single definition and is never restated. It is worth obeying
here because the vocabulary had already been written twice — `fleet.py` named
the scopes a tool requires, `oauth.py` named the scopes the authorization
server will mint — and those two lists are the same list only for as long as
someone remembers to edit both. The failure they drift into is not symmetric
and neither half is loud: a scope added to the minting side alone is a grant the
consent form offers and no code honours, and one added to the requiring side
alone is a tool nobody can ever be given.

Two tiers, and the difference between them is the point
-------------------------------------------------------
`IMPLEMENTED_SCOPES` is exactly the set of scopes some tool requires.
`ALLOWED_SCOPES` *is* that tuple — not a copy of it, the same object — so a
scope can only become issuable by becoming consumed, and the authorization
server can never mint a grant that reaches no code.

`PLANNED_SCOPES` is the vocabulary the session-lifecycle design names for verbs
this surface does not implement yet. They are written down here so the shape of
the closed interface is reviewable in one screen, and — the part that matters —
so that "the design names it" cannot be quietly read as "the deployment grants
it". They are deliberately absent from `ALLOWED_SCOPES`, which makes the
separation a property of the tuple rather than a promise in a document:
`fleet:control`, `fleet:approve` and `fleet:navigate` cannot be requested on the
consent form, cannot be minted, and are refused by `validate_authorize` as
unsupported scopes. A test asserts the two tiers are disjoint.

Why a scope is not a capability
-------------------------------
Every tool this surface exposes is additionally gated below the scope — by the
device's local binding and by `policy`. A scope says what the *credential* may
ask for; it never says what the deployment has agreed to. `fleet:create` in
particular is necessary and never sufficient: the token must carry it, the
device must be authorizing creation, and the project must be allow-listed.
"""
from __future__ import annotations

# -- implemented ------------------------------------------------------------

SCOPE_READ = "fleet:read"
"""Read the fleet: devices, sessions, transcripts, runs, status."""

SCOPE_SEND = "fleet:send"
"""Deliver a message to a session the operator has already authorized."""

SCOPE_CREATE = "fleet:create"
"""Create a session in a project the operator has already authorized."""

IMPLEMENTED_SCOPES: tuple[str, ...] = (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)

# The one definition. `fleet.py` requires from it, `oauth.py` mints from it.
ALLOWED_SCOPES: tuple[str, ...] = IMPLEMENTED_SCOPES

# -- planned, and deliberately not granted ----------------------------------

SCOPE_CONTROL = "fleet:control"
"""Planned: interrupt a run, start/stop a session's resident host, stop a
background task, cancel a queued message, reconfigure a session."""

SCOPE_APPROVE = "fleet:approve"
"""Planned: answer a pending tool-approval request raised inside a session."""

SCOPE_NAVIGATE = "fleet:navigate"
"""Planned: ask a *connected browser* to open a session. Note the difference
from every other planned verb: the effect lands on a device's UI, not on the
agent, so it needs a connected client to exist before it can mean anything."""

PLANNED_SCOPES: tuple[str, ...] = (SCOPE_CONTROL, SCOPE_APPROVE, SCOPE_NAVIGATE)

# -- what a deployment grants when it says nothing ---------------------------
#
# Strictly smaller than `ALLOWED_SCOPES`, and staying that way is what makes a
# new scope opt-in. A binding that does not mention `grantable_scopes` issues
# this set: reading and sending, which is what every deployment in the field
# already issues, and nothing a later round added.
#
# It is one constant rather than two matching literals because it was written
# twice — once as the default on `AuthBinding` and once in `load_config` — and
# nothing kept them equal. The failure that produces is quiet and asymmetric:
# the loader's copy is the one that actually decides, so a dataclass default
# edited alone would be a comment.
DEFAULT_GRANTABLE_SCOPES: tuple[str, ...] = (SCOPE_READ, SCOPE_SEND)


def requires_implemented_scope(scope: str) -> bool:
    """Whether a tool may declare this scope.

    Used by the surface tests to hold the rule that a tool requires only
    scopes the authorization server will actually issue: a tool gated on a
    planned scope would be permanently unreachable, which reads to an operator
    as a broken tool rather than as an unwritten one.
    """
    return scope in ALLOWED_SCOPES


__all__ = [
    "ALLOWED_SCOPES",
    "DEFAULT_GRANTABLE_SCOPES",
    "IMPLEMENTED_SCOPES",
    "PLANNED_SCOPES",
    "SCOPE_APPROVE",
    "SCOPE_CONTROL",
    "SCOPE_CREATE",
    "SCOPE_NAVIGATE",
    "SCOPE_READ",
    "SCOPE_SEND",
    "requires_implemented_scope",
]
