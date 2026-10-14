"""One physical device, several runtimes — the naming rule, and what it is not.

Why this exists
---------------
`fleet/registry.yaml` already models a device as *owning several runtimes*
(`runtime_bindings`: `agent-runtime:claude-code`, `agent-runtime:codex`), and
`identity.SessionKey` already addresses a session as a triple
(`device_ref · provider · native_session_id`). What was missing is the rule that
joins the two when the runtimes belong to **different OS accounts on the same
machine** — the Dell A6000 case, where `vince` and `fjd` each run their own
CloudCLI, under their own account, with their own credential and their own
state.

The rule this module fixes, and nothing else:

    the device's own account keeps the unqualified tag     "claude"
    every other account gets a suffixed one                "claude-fjd"

That choice is not cosmetic. `device:llm-lab-dell|claude|<uuid>` is what every
existing session ref already says, so the second runtime is added **without
re-addressing the first** — no migration, and no window in which a live
session's address is ambiguous.

What this module is NOT
-----------------------
It authenticates nothing, and it does not make two runtimes isolated. A tag is a
NAME. Isolation is four separate facts on the device — a different OS account, a
different CloudCLI credential, a different state root, a different node secret —
and a request that merely *says* `provider=claude-fjd` is a claim, not a proof.
`ISOLATION_CONTRACT` lists the four and who has to make each one true;
`docs/fleet-multi-runtime-design.md` carries the protocol design and the gap
list for the two layers that do not yet carry a runtime.

Nothing here is wired into the running surface. This is the naming rule and its
invariants, so that the routing change which consumes it can be reviewed against
something that already has a shape.
"""
from __future__ import annotations

from dataclasses import dataclass

from .identity import SessionKey

#: The tag the device's OWN account keeps. Unqualified so that every session ref
#: written before a second runtime existed stays exactly as it was.
BASE_RUNTIME = "claude"

#: The four facts that make two runtimes on one machine isolated, and the party
#: that has to make each one true. Three of the four are the DEVICE's to
#: arrange — this host cannot verify them, and must not pretend to. The host's
#: one job is the fourth: route by the tag it was given, and refuse a tag it was
#: not.
ISOLATION_CONTRACT: tuple[dict[str, str], ...] = (
    {
        "fact": "os_account",
        "required": "each runtime runs as its own OS account; the connector never "
                    "reads another account's home, environment, or process tree",
        "owner": "device",
    },
    {
        "fact": "cloudcli_credential",
        "required": "each runtime holds its own CloudCLI credential, readable only "
                    "by its own account; nothing shares or proxies another's",
        "owner": "device",
    },
    {
        "fact": "state_root",
        "required": "each runtime's state (sessions, queue, SQLite) lives under "
                    "its own account's root and is not world-readable",
        "owner": "device",
    },
    {
        "fact": "node_secret",
        "required": "each runtime signs with its own node secret; a secret for one "
                    "runtime cannot authenticate a request for the other",
        "owner": "device + host — see the gap list in the design note",
    },
)


@dataclass(frozen=True)
class RuntimeIdentity:
    """One runtime on one physical device, and the account that owns it."""

    device_ref: str
    user: str
    tag: str

    def session_ref(self, native_session_id: str) -> str:
        return SessionKey(self.device_ref, self.tag, native_session_id).ref


def _slug(user: str) -> str:
    """`user` reduced to the characters a provider tag may carry.

    Deliberately NOT a general sanitiser: it lowercases and keeps
    `[a-z0-9._-]`, and it refuses rather than invents when the result would be
    empty. A user name that reduces to nothing has no tag this scheme can write
    down, and mapping it onto some other account's dotted name is the one
    outcome worse than a refusal.
    """
    kept = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "-"
                   for ch in user.strip().lower())
    kept = kept.strip("-.")
    if not kept:
        raise ValueError(
            f"{user!r} reduces to no tag characters, so this scheme cannot name "
            f"its runtime; it is refused rather than mapped onto another account"
        )
    return kept


def runtime_tag(user: str, *, device_owner: str) -> str:
    """The provider tag one OS account's runtime is addressed by.

    Validated by constructing a real `SessionKey`, so the rule cannot drift from
    the authority that parses refs — this module does not restate the provider
    grammar, it borrows it.
    """
    if not isinstance(user, str) or not user.strip():
        raise ValueError("a runtime belongs to a named account")
    if not isinstance(device_owner, str) or not device_owner.strip():
        raise ValueError("the device's own account must be named, not assumed")
    tag = (BASE_RUNTIME if user.strip() == device_owner.strip()
           else f"{BASE_RUNTIME}-{_slug(user)}")
    # Raises if the tag is not a provider name this surface can address.
    SessionKey(device_ref="device:validation", provider=tag,
               native_session_id="validation")
    return tag


def runtime_for(device_ref: str, user: str, *, device_owner: str) -> RuntimeIdentity:
    """The identity of one account's runtime on one device."""
    if not isinstance(device_ref, str) or not device_ref.startswith("device:"):
        raise ValueError(
            f"{device_ref!r} is not a typed device ref; one physical machine is "
            f"one AUTHENTICATED device, and the runtimes on it are told apart by "
            f"their provider tag rather than by a second device identity"
        )
    return RuntimeIdentity(device_ref=device_ref, user=user.strip(),
                           tag=runtime_tag(user, device_owner=device_owner))


def tag_of(ref: str) -> str:
    """The provider tag of an existing session ref, parsed by the authority."""
    return SessionKey.parse(ref).provider


def is_the_base_runtime(tag: str) -> bool:
    """Whether `tag` is the device's own, unqualified runtime."""
    return tag == BASE_RUNTIME
