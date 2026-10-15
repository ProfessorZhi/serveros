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

#: The query vocabulary. These are SELECTORS, not tags: they name a *set* of
#: runtimes rather than one, and a caller asking for "everything on this device"
#: must not have to spell it by listing tags it may not know exist.
#:
#: They live here, beside `BASE_RUNTIME`, because "which string names a runtime"
#: and "which string selects a set of them" have to be answered by one module or
#: they stop agreeing. `all` is a legal *provider name* (the grammar is
#: `^[a-z][a-z0-9._-]{0,31}$`), so it could in principle be declared as a tag —
#: and a tag spelled `all` would be a runtime every listing silently swallowed.
#: The binding loader in the local-settings module therefore refuses a binding
#: whose runtime is one of these, which is what makes the two sets disjoint by
#: construction rather than by nobody having tried it yet. `*` cannot be a tag
#: at all; the loader's refusal is what covers `all` and `base`.
#:
#: Naming the module that enforces this is deliberately not done in this file:
#: `NamingIsIndependentOfAnythingBeingAliveTest` scans this source for the tokens
#: that would mean the naming rule had reached for something outside itself, and
#: a comment is not worth teaching that check to read prose.
SELECTOR_ALL = "all"
SELECTOR_ANY = "*"
SELECTOR_BASE = "base"
SELECTORS: tuple[str, ...] = (SELECTOR_ALL, SELECTOR_ANY, SELECTOR_BASE)


def is_a_selector(value: object) -> bool:
    """True for the query vocabulary, False for every runtime tag.

    `BASE_RUNTIME` ("claude") is deliberately NOT a selector: it is a real tag,
    and a caller asking for it is asking for one runtime — the same one
    `SELECTOR_BASE` names, arrived at from the other side.
    """
    return isinstance(value, str) and value in SELECTORS


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


#: The agent families a tag can be prefixed with. A family is what KIND of agent
#: runs — it is not a credential and not an account, and neither derives from the
#: other. Naming Codex with an Anthropic model id would be exactly the collapse
#: this tuple exists to keep apart.
FAMILIES: tuple[str, ...] = (BASE_RUNTIME, "codex")

#: A tag has to be a provider name, and the grammar allows 32 characters. The
#: ceiling is enforced here rather than discovered later, because a tag that is
#: silently truncated is a tag that can collide with another one.
TAG_MAX_LENGTH = 32


@dataclass(frozen=True)
class RuntimeIdentity:
    """One runtime on one physical device: its account, and its profile.

    `profile` and `family` are NAMES. Neither grants anything, neither is
    evidence of anything, and neither is checked against a binding here — this
    module is the naming rule, and a tag is a claim about which runtime is meant
    rather than a proof that it exists. Two profiles under one account are two
    identities; the account is the same and everything else about them differs.

    `family` defaults to the base family so a construction written before the
    axis existed keeps meaning what it meant.
    """

    device_ref: str
    user: str
    tag: str
    profile: str | None = None
    family: str = BASE_RUNTIME

    def session_ref(self, native_session_id: str) -> str:
        return SessionKey(self.device_ref, self.tag, native_session_id).ref


def _slug(text: str) -> str:
    """`text` reduced to the characters a provider tag may carry.

    Deliberately NOT a general sanitiser: it lowercases and keeps
    `[a-z0-9._-]`, and it refuses rather than invents when the result would be
    empty. A name that reduces to nothing has no tag this scheme can write down,
    and mapping it onto some other account's dotted name is the one outcome
    worse than a refusal.
    """
    kept = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "-"
                   for ch in text.strip().lower())
    kept = kept.strip("-.")
    if not kept:
        raise ValueError(
            f"{text!r} reduces to no tag characters, so this scheme cannot name "
            f"its runtime; it is refused rather than mapped onto another account"
        )
    return kept


def profile_slug(profile: str) -> str:
    """A credential profile reduced to the tag characters, or refused.

    Refused rather than repaired when it is empty, when it reduces to nothing,
    or when the reduction spells one of the query vocabulary words: a profile
    that normalised onto `all` would produce a tag a listing silently swallows.
    """
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("a profile name must be a non-empty string")
    slug = _slug(profile)
    if slug in SELECTORS:
        raise ValueError(
            f"profile {profile!r} normalises onto the reserved word {slug!r}, "
            f"which names a set of runtimes rather than one"
        )
    return slug


def runtime_tag(user: str, *, device_owner: str, profile: str | None = None,
                family: str = BASE_RUNTIME) -> str:
    """The provider tag one runtime is addressed by.

    Three axes, and the first two keep the spelling they always had:

        family    which kind of agent it is       the base family, or `codex`
        account   which OS account owns it        the device's own, or another
        profile   which credential inside it      none, or a named one

    With no profile the tag is exactly what it has always been — the bare base
    tag for the device's own account, `<base>-<account>` for another — so every
    session ref written before profiles existed still names the runtime it
    named. A second profile under one account is a different tag, which is the
    whole point: one account is not one credential.

    Validated by constructing a real `SessionKey`, so the rule cannot drift from
    the authority that parses refs — this module does not restate the provider
    grammar, it borrows it.
    """
    if family not in FAMILIES:
        raise ValueError(
            f"{family!r} is not an agent family this surface names; it is one "
            f"of {FAMILIES}. A family is the KIND of agent and is not a model, "
            f"an account or a credential."
        )
    if not isinstance(user, str) or not user.strip():
        raise ValueError("a runtime belongs to a named account")
    if not isinstance(device_owner, str) or not device_owner.strip():
        raise ValueError("the device's own account must be named, not assumed")

    own = user.strip() == device_owner.strip()
    if own and profile is None:
        tag = family
    else:
        parts = [family]
        if not own:
            parts.append(_slug(user))
        if profile is not None:
            parts.append(profile_slug(profile))
        tag = "-".join(parts)

    if len(tag) > TAG_MAX_LENGTH:
        raise ValueError(
            f"the tag {tag!r} is {len(tag)} characters and the grammar allows "
            f"{TAG_MAX_LENGTH}. It is refused rather than truncated: a shortened "
            f"tag is one that can collide with a different runtime's."
        )
    # Raises if the tag is not a provider name this surface can address.
    SessionKey(device_ref="device:validation", provider=tag,
               native_session_id="validation")
    return tag


def runtime_tags_are_distinct(identities) -> None:
    """Refuse a set in which two different runtimes derive one tag.

    The three axes share a character set with the separator between them, so two
    different accounts — or an account and a profile — can reduce to one
    spelling. Grammar cannot prevent that, and pretending otherwise would put
    the failure one layer down where it looks like a routing bug. So it is
    DETECTED: a set naming two runtimes under one tag is refused rather than
    resolved by whichever happened to be declared first.

    Takes identities rather than tags because the tag is the thing being
    questioned — comparing tags would only confirm they are equal.
    """
    seen: dict[str, tuple] = {}
    for identity in identities:
        key = (identity.family, identity.user.strip(), identity.profile)
        prior = seen.get(identity.tag)
        if prior is not None and prior != key:
            raise ValueError(
                f"two different runtimes derive the tag {identity.tag!r}: "
                f"{prior!r} and {key!r}. One tag addresses one runtime, so this "
                f"set is refused rather than routed by declaration order."
            )
        seen[identity.tag] = key


def runtime_for(device_ref: str, user: str, *, device_owner: str,
                profile: str | None = None,
                family: str = BASE_RUNTIME) -> RuntimeIdentity:
    """The identity of one runtime: an account, and a profile within it.

    A second profile under one account is a second runtime, so this is the
    constructor for "the same person, a different credential" as much as for
    "a different person". `profile=None` and the base family give exactly the
    identity this returned before the axes existed.
    """
    if not isinstance(device_ref, str) or not device_ref.startswith("device:"):
        raise ValueError(
            f"{device_ref!r} is not a typed device ref; one physical machine is "
            f"one AUTHENTICATED device, and the runtimes on it are told apart by "
            f"their provider tag rather than by a second device identity"
        )
    return RuntimeIdentity(
        device_ref=device_ref, user=user.strip(), profile=profile, family=family,
        tag=runtime_tag(user, device_owner=device_owner, profile=profile,
                        family=family),
    )


def tag_of(ref: str) -> str:
    """The provider tag of an existing session ref, parsed by the authority."""
    return SessionKey.parse(ref).provider


def is_the_base_runtime(tag: str) -> bool:
    """Whether `tag` is the device's own, unqualified runtime."""
    return tag == BASE_RUNTIME


def is_a_provider_tag(value: object) -> bool:
    """Whether `value` is a spelling this surface could address a runtime by.

    Asked by constructing the authority that parses refs rather than by
    restating its grammar here: a second copy of the pattern is a second answer
    to one question, and the copy is the one that drifts. The query vocabulary is
    excluded — those name sets, not runtimes.
    """
    if not isinstance(value, str) or not value or is_a_selector(value):
        return False
    try:
        SessionKey(device_ref="device:validation", provider=value,
                   native_session_id="validation")
    except Exception:  # noqa: BLE001 - any refusal means "this is not a tag"
        return False
    return True
