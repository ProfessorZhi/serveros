"""The scope vocabulary, in one place.

Why this module exists
----------------------
Before it, the scope strings were written out literally in four files — the
service (`fleet.py`), the authorization server (`oauth.py`), the local binding
loader (`config.py`) and the HTTP transport's PAT branch (`http_transport.py`)
— and the sets they spelled disagreed: the loader's fallback default named
`fleet:create` as grantable while an operator who wrote no `grantable_scopes`
at all should get the narrower set, and the discovery documents advertised
whatever `oauth.py` happened to list. Two of those disagreements were harmless
spellings of the same word; one of them was a widening of the default grant,
which is the kind of harmless that shows up as a surprising token six months
later.

So: every scope this build knows is named exactly once, here, and everything
else imports. There is no second list to keep in step, and no place left where
a typo becomes a scope nobody implements but everybody issues.

The three sets, and why they are three
--------------------------------------
`IMPLEMENTED_SCOPES` is what this build actually enforces end to end — a
credential can carry it, a caller must present it, and every code path it
names exists. `ALLOWED_SCOPES` *is* that set: it is the ceiling the wire
advertises and the set `validate_authorize` will accept, because a scope no
code path enforces is not a scope, it is a promise. Advertising one would let
a client be granted authority that silently does nothing when exercised, which
reads to the client as "the device did not answer" rather than "you never had
this".

`PLANNED_SCOPES` is the other half of the roadmap — the verbs the contract
names as next (`fleet:control`, `fleet:approve`, `fleet:navigate`) and that
this build does not implement. They are listed so that the three names exist
in the vocabulary and a future revision extends `IMPLEMENTED_SCOPES` by moving
one across, rather than by inventing a spelling. They are deliberately **not**
in `ALLOWED_SCOPES`: naming a verb we owe is fine, issuing it is not.

`DEFAULT_GRANTABLE_SCOPES` is the ceiling an operator who writes nothing gets.
Read and send — **not** create. A deployment that has not said the word
"create" has not authorized creation, and the OAuth consent form is where the
operator says it per client. Adding `fleet:create` to `grantable_scopes` is
therefore a deliberate act, and the only thing that makes a create-capable
connector possible.
"""
from __future__ import annotations

SCOPE_READ = "fleet:read"
SCOPE_SEND = "fleet:send"
# Creating a session is a third question, and a third scope. It is not implied
# by `fleet:send`: a credential trusted to message sessions an operator already
# authorized is not thereby trusted to mint new ones, and folding the two would
# make the narrower grant unachievable.
SCOPE_CREATE = "fleet:create"

# Named, not implemented. These exist so the roadmap has one spelling each.
SCOPE_CONTROL = "fleet:control"
SCOPE_APPROVE = "fleet:approve"
SCOPE_NAVIGATE = "fleet:navigate"

IMPLEMENTED_SCOPES: tuple[str, ...] = (SCOPE_READ, SCOPE_SEND, SCOPE_CREATE)
PLANNED_SCOPES: tuple[str, ...] = (SCOPE_CONTROL, SCOPE_APPROVE, SCOPE_NAVIGATE)

# The wire-visible ceiling. Equal to the implemented set, on purpose: what is
# advertised is what is enforced.
ALLOWED_SCOPES: tuple[str, ...] = IMPLEMENTED_SCOPES

# What an operator gets without saying anything.
DEFAULT_GRANTABLE_SCOPES: tuple[str, ...] = (SCOPE_READ, SCOPE_SEND)


def requires_implemented_scope(scope: str) -> bool:
    """Whether this build has a code path that enforces `scope`."""
    return scope in IMPLEMENTED_SCOPES


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
