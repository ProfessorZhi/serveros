#!/usr/bin/env python3
"""The Remote Console: how a person reaches a session, and how much is said.

§L names the channel (Feishu App1 → BotMux → HumanControlProvider → ServerOS),
§M names the operations, §N names how much of them to say at each level, §P
names what may NOT be copied from a client, and §AA names what a person may and
may not authorise from the other end.

Three planes meet here and this module is the join, so what it owns is stated
first, because a join that owns one of its inputs is not a join:

    owns      the SEMANTIC OPERATION vocabulary · the level projection over
              sessions · the conversation-scoped selection
    not       session truth — that is the Agent Session plane's, read here
    not       device or runtime truth — the fleet's, read here
    not       rendering — §P: a UI implementation is not a semantic operation
    not       transport — reaching Feishu is the HumanControlProvider's

**Why a module and not a client.** §M's first sentence is a prohibition: do not
clone the Claude Code or Codex UI pixel for pixel, and do not copy every button
and flag. What is unified is the *operation* — select a session, continue work,
send an instruction, close a session, see what it is waiting for — and what is
deliberately NOT unified is the way any of them looks. So this module produces
operations and levels, and produces no markup: a caller renders it, and the
runtime-specific extras live at the levels §P assigns them.

**The rule §M states twice, and why it is data here.** §M says interrupt is
available only when the provider really supports it, that close must never be
passed off as interrupt, and that a transport which supports close and not
interrupt must SAY `interrupt = UNSUPPORTED`. The session contract already
states the same rule in one line — *"a stronger provider verb never satisfies a
narrower ServerOS verb; close-whole-session is not interrupt-turn"* — and
`fleet/agent_control_router.py` already refuses to translate one into the
other. What is new here is that the console's own vocabulary carries it:
`session.close` names `session.interrupt` in `never_satisfied_by`, and
`operation_states()` reads the provider's support for **the action the
operation actually is**. A console that showed interrupt as available because
close was is the failure this encodes, and it cannot be reached by accident
because no code path computes one from the other.

**Selection is not a binding.** §M: selecting is the current HumanControl
conversation choosing a target `session_ref`, and it is *not* changing a
session's identity or moving an execution thread to another Item. Those are
different objects and they are kept apart by shape rather than by discipline —
`SELECTION_FIELDS` and `THREAD_BINDING_FIELDS` are disjoint and a test asserts
it, so a selection record has no field in which a `bound_item_ref` could be
carried. The rebind §M forbids is not refused here; it is unrepresentable, and
`runtime/agent_control_store.py#put_binding` refuses it as well for the caller
who goes around this module.

**An unverified person may select.** `human.identity.verify` is `UNKNOWN` (see
`mcp/human_control.py`), so there is no actor to record on a selection, and one
is not invented. §AA permits exactly this and no more: a non-destructive
console may run on the existing authenticated transport policy, while a
destructive act may not treat a delivered message as an approval. That is why
every operation here carries `destructive`, why selection carries none, and why
`selection_record()` writes `identity: UNVERIFIED` rather than leaving a field
it cannot fill — an absent actor and an unverified actor look the same in a
record with no field for either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Levels. The same four §N names, and the same four
# `context/disclosure-contract.yaml` and `fleet/view-contract.yaml` already
# price. §12's "no fourth coordinate system" applies here too: the session
# console is another SUBJECT of the existing levels and not a new scale, so the
# names are read from the disclosure kernel rather than spelled again.
# ---------------------------------------------------------------------------
LEVELS = ("L0", "L1", "L2", "L3")

# What a caller may answer about one operation, read from the provider's own
# per-session `action_support`. `LOCAL` is the fourth and it is not a support
# state: it says ServerOS itself decides this one and no provider was asked, so
# it can never be confused with a provider having said yes.
AVAILABLE = "AVAILABLE"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"
LOCAL = "LOCAL"

# The words a provider's `action_support` may carry, and what each becomes. A
# translation table and not a passthrough, because the two vocabularies answer
# different questions: a provider says whether it has the verb, the console
# says whether a person may use it.
SUPPORT_STATES = {
    "SUPPORTED": AVAILABLE,
    "UNSUPPORTED": UNSUPPORTED,
    "UNKNOWN": UNKNOWN,
}


class ConsoleRefused(RuntimeError):
    """The console will not carry this, and `reason` is the word for why."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Operation:
    """One thing a person can ask a session to do.

    `action` is the closed verb from `runtime/agent-session-contract.yaml
    #support.actions` that this operation IS, or None when ServerOS decides it
    and no provider is involved. Keeping the mapping explicit rather than
    making the names identical is what lets §M's word (`session.close`) and the
    contract's word (`session.stop`) be one operation with one support state,
    instead of two names that drift apart one provider at a time.
    """

    name: str
    action: str | None
    level: str
    aliases: tuple[str, ...] = ()
    # The operation this one may never be REPORTED AS. §M's sentence, as data.
    never_satisfied_by: tuple[str, ...] = ()
    # True when ServerOS decides and records it: no provider is asked, so it
    # has no support state to be in.
    local: bool = False
    # True when the session contract puts the verb in `admission.human_gated`.
    # §AA's line runs exactly here: a destructive act may not treat a delivered
    # message as an approval, and the console has to be able to say which of
    # its operations that is.
    destructive: bool = False
    # A runtime feature this operation is meaningless without, from
    # `abilities/agent-runtime-contract.yaml#runtime_features`. Named rather
    # than derived from the operation's spelling, because §P requires the
    # vocabulary to decide what a runtime is offered and nothing else.
    requires_feature: str | None = None

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"{self.name} names a level that is not one: {self.level!r}")
        if self.local and self.action is not None:
            raise ValueError(
                f"{self.name} is local and names provider action "
                f"{self.action!r}; a local operation asks no provider, so it "
                f"cannot claim one")


# ---------------------------------------------------------------------------
# The vocabulary. Every entry below is §M's or the session contract's; nothing
# was added because it seemed likely to be wanted.
#
# §M names eight: list, inspect, select, send, resume/continue, close,
# interrupt, and the report of whether interrupt is available at all (which is
# a property of the row rather than an operation). The contract's closed list
# is nine and adds create, result and handoff, so those three are here too —
# a console over a vocabulary that silently drops a third of it would be the
# narrower surface §M complains about, one plane up.
# ---------------------------------------------------------------------------
OPERATIONS: tuple[Operation, ...] = (
    Operation(name="session.list", action="session.list", level="L1"),
    Operation(name="session.inspect", action="session.inspect", level="L3"),
    Operation(name="session.result", action="session.result", level="L2"),
    Operation(
        name="session.select", action=None, level="L1", local=True,
    ),
    Operation(name="session.create", action="session.create", level="L2",
              destructive=True),
    Operation(name="session.send", action="session.send", level="L2"),
    Operation(
        name="session.resume", action="session.resume", level="L2",
        # §M spells it both ways and the contract spells it once. The alias is
        # carried rather than declared as a second operation, so the two
        # spellings cannot acquire two support states.
        aliases=("session.continue",),
        requires_feature="session.lifecycle",
    ),
    Operation(
        name="session.close", action="session.stop", level="L2",
        # §M's own word for the verdict, and the contract's word for the verb.
        aliases=("session.stop",),
        # The sentence §M writes twice, carried where it can be checked.
        never_satisfied_by=("session.interrupt",),
        destructive=True,
    ),
    Operation(
        name="session.interrupt", action="session.interrupt", level="L2",
        # A turn-scoped act, and the contract's `human_gated` set is why it is
        # destructive rather than merely provider-flavoured: it stops work in
        # flight.
        destructive=True,
    ),
    Operation(
        name="session.handoff", action="session.handoff", level="L3",
        destructive=True,
        requires_feature="session.lifecycle",
    ),
)

OPERATION_NAMES: tuple[str, ...] = tuple(op.name for op in OPERATIONS)

# Every spelling a person might use for an operation, including the aliases,
# pointing at the one operation it is. Built rather than written so an alias
# added above cannot be forgotten here.
_ALIASES: dict[str, str] = {}
for _op in OPERATIONS:
    _ALIASES[_op.name] = _op.name
    for _alias in _op.aliases:
        _ALIASES[_alias] = _op.name


def resolve(name: str) -> Operation:
    """The operation a person named, under any of its spellings.

    Refuses rather than returning None: a console asked for `session.kill`
    must say it has no such operation, not quietly do nothing and let the
    caller report that nothing happened.
    """
    canonical = _ALIASES.get(str(name or "").strip())
    if canonical is None:
        raise ConsoleRefused(
            "ACTION_UNSUPPORTED",
            f"{name!r} is not an operation this console has. It has: "
            f"{', '.join(OPERATION_NAMES)}")
    for op in OPERATIONS:
        if op.name == canonical:
            return op
    raise AssertionError(f"{canonical} resolved but is not declared")


def operations_at(level: str) -> tuple[Operation, ...]:
    """What a caller may offer at one level, and nothing above it.

    Cumulative, like every other level in this tree: L2 offers what L1 offers
    and more. A level that dropped an operation its predecessor offered would
    make disclosure a lossy step, and a caller would learn to ask for the
    highest level every time — which is the opposite of what levels are for.
    """
    if level not in LEVELS:
        raise ConsoleRefused(
            "INVALID_CONTEXT",
            f"{level!r} is not a level; there are {', '.join(LEVELS)}")
    ceiling = LEVELS.index(level)
    return tuple(op for op in OPERATIONS if LEVELS.index(op.level) <= ceiling)


def non_destructive(names: list[str]) -> tuple[list[str], list[str]]:
    """Split operations into what may run on an unverified identity and what
    may not. §AA's line, computed rather than restated.

    Returns `(permitted, refused)`. The caller is expected to show the second
    list rather than drop it: an operation a person asked for and did not get
    is a thing they must be told, and a console that silently omits it is the
    console §M refuses.
    """
    permitted: list[str] = []
    refused: list[str] = []
    for name in names:
        (refused if resolve(name).destructive else permitted).append(name)
    return permitted, refused


# ---------------------------------------------------------------------------
# Support. Where the provider's answer becomes the console's answer, and the
# only place a `never_satisfied_by` rule is enforced.
# ---------------------------------------------------------------------------
def operation_states(action_support: dict | None,
                     *, features: dict | None = None) -> dict[str, dict]:
    """One state per operation, each derived from its OWN action.

    `action_support` is the per-session map a provider publishes
    (`mcp/botmux_provider.py#_actions`): one entry per closed action, carrying
    a state and a reason. This function never widens one entry into another —
    that is what `never_satisfied_by` names, and the check below is what makes
    the name mean something rather than describe an intention.

    A local operation is `LOCAL`. It is deliberately not `AVAILABLE`: a caller
    that read `AVAILABLE` would conclude a provider was asked and agreed, and
    for `session.select` no provider is asked at all.

    `features` is the runtime feature map for this session's binding, when the
    caller has one. An operation naming a `requires_feature` is reported
    `UNSUPPORTED`/`INCOMPATIBLE` when that feature is `UNAVAILABLE` or
    `INCOMPATIBLE`, and is left alone when the feature state is `UNKNOWN` — a
    feature nobody established may not withdraw an operation the provider
    supports, which is §P's vocabulary deciding disclosure rather than
    overriding a measurement.
    """
    support = action_support if isinstance(action_support, dict) else {}
    feature_states = features if isinstance(features, dict) else {}
    states: dict[str, dict] = {}
    for op in OPERATIONS:
        if op.local:
            states[op.name] = {
                "state": LOCAL,
                "because": ("ServerOS records this one; no provider is asked, "
                            "so there is no provider support to report"),
                "action": None,
            }
            continue
        entry = support.get(op.action)
        if not isinstance(entry, dict):
            # Absent is not unsupported. A provider whose map does not mention
            # the action has not declined it; it has not been asked, and the
            # two call for opposite actions.
            states[op.name] = {
                "state": UNKNOWN,
                "because": (f"provider action {op.action!r} is not in this "
                            f"session's action_support map, so nobody "
                            f"established whether it is supported"),
                "action": op.action,
            }
            continue
        word = str(entry.get("state") or "").strip().upper()
        state = SUPPORT_STATES.get(word, UNKNOWN)
        because = str(entry.get("reason") or "").strip() or (
            f"provider reported {word or 'nothing'} for {op.action}")
        states[op.name] = {"state": state, "because": because,
                           "action": op.action, "provider_state": word}

    _apply_feature_gate(states, feature_states)
    _assert_no_borrowed_support(states)
    return states


def _apply_feature_gate(states: dict[str, dict], features: dict) -> None:
    """Let the feature vocabulary narrow, and never widen — §P."""
    for op in OPERATIONS:
        if not op.requires_feature:
            continue
        feature = features.get(op.requires_feature)
        state = None
        if isinstance(feature, dict):
            state = str(feature.get("state") or "").strip().upper()
        elif isinstance(feature, str):
            state = feature.strip().upper()
        if state in ("UNAVAILABLE", "INCOMPATIBLE"):
            entry = states[op.name]
            entry["state"] = UNSUPPORTED
            entry["because"] = (
                f"the runtime for this session is {state} on "
                f"{op.requires_feature}, which {op.name} needs; the provider's "
                f"own answer ({entry.get('provider_state') or 'unknown'}) is "
                f"kept here rather than replaced: "
                f"{entry.get('because', '')}")
            entry["provider_state_kept"] = entry.get("provider_state")


def _assert_no_borrowed_support(states: dict[str, dict]) -> None:
    """Refuse to report an operation on an answer that is not its own.

    §M: *"不要把 close 冒充 interrupt"*. The table above already keeps the two
    apart by construction — each is priced on its own action, and no two
    operations share one — so this is not where the rule is *decided*; it is
    where the construction is CHECKED. The failure it refuses is a state map
    that could only have been produced by reading one operation's answer for
    another, which is exactly what somebody "simplifying" the loop above would
    write, and which no type or spelling would catch.

    Stated over the finished map rather than inside the loop, so a future
    operation is covered without anyone remembering to add a branch here.
    """
    by_name = {op.name: op for op in OPERATIONS}
    for op in OPERATIONS:
        entry = states.get(op.name)
        if entry is None:
            continue
        if op.local:
            # A local operation asks no provider, so an action on its entry is
            # a claim that somebody was asked.
            if entry.get("action") is not None:
                raise ConsoleRefused(
                    "INVALID_CONTEXT",
                    f"{op.name} is ServerOS's own decision and carries provider "
                    f"action {entry['action']!r}; no provider was asked, so no "
                    f"provider's answer may be attached to it")
            continue
        if entry.get("action") != op.action:
            raise ConsoleRefused(
                "INVALID_CONTEXT",
                f"{op.name} is priced on {entry.get('action')!r} and its own "
                f"action is {op.action!r}. A state produced from a different "
                f"operation's answer is precisely one operation reported as "
                f"another, which is the close-as-interrupt failure §M forbids")
    # And the relation itself has to stay a relation between two different
    # verbs. If a future edit gave `session.close` and `session.interrupt` the
    # same provider action, the check above would still pass on both and the
    # forbidding would become decorative.
    for op in OPERATIONS:
        for forbidden in op.never_satisfied_by:
            other = by_name.get(forbidden)
            if other is None:
                raise ConsoleRefused(
                    "INVALID_CONTEXT",
                    f"{op.name} forbids being reported as {forbidden}, which is "
                    f"not an operation this console has; the forbidding would "
                    f"never be checked")
            if not op.local and not other.local and other.action == op.action:
                raise ConsoleRefused(
                    "INVALID_CONTEXT",
                    f"{op.name} and {other.name} are both priced on provider "
                    f"action {op.action!r}, so {op.name} could not be told "
                    f"apart from {other.name} even in principle — the mapping "
                    f"that exists to keep them separate has stopped doing it")


# ---------------------------------------------------------------------------
# The level projection. §N's four payloads, and what each may NOT carry.
# ---------------------------------------------------------------------------
def unknown(reason: str) -> dict:
    """An absence that says what it is.

    A missing bound Item and an Item nobody looked up are different facts, and
    a caller that cannot tell them apart will read the second as the first.
    """
    return {"state": "UNKNOWN", "reason": reason}


def level_l0(rows: list[dict], *, devices: dict | None = None) -> dict:
    """Counts, and nothing that could be a list of sessions.

    §N's L0 is *"北京 / Claude Code · 2 sessions"* — the shape a person reads
    before deciding to look closer. §AH adds the constraint that makes this a
    rule rather than a rendering choice: L0 must not load full session history,
    and the cheapest way to be sure is to return no session at all. So this
    function counts and never returns a row, and what it counts by is read from
    the rows rather than asked for separately.
    """
    by_device: dict[str, dict] = {}
    for row in rows:
        device = str(row.get("device_ref") or "UNKNOWN")
        runtime = str(row.get("agent_runtime") or "UNKNOWN")
        entry = by_device.setdefault(device, {
            "device_ref": device,
            "display_name": (devices or {}).get(device),
            "total": 0,
            "by_runtime": {},
        })
        entry["total"] += 1
        entry["by_runtime"][runtime] = entry["by_runtime"].get(runtime, 0) + 1
    return {
        "level": "L0",
        "state": "KNOWN",
        "sessions_total": len(rows),
        "by_device": [by_device[k] for k in sorted(by_device)],
        "note": ("counts only. L0 does not carry a session row — ask for L1 to "
                 "see one, which is what makes L0 cheap enough to be the "
                 "default"),
    }


# The fields a card carries, which is §N's L1 list. Written as a tuple because
# a test asserts the card has exactly these and no more: "bound_item" being
# present is the point, and a card that quietly grew a transcript field would
# be L0's prohibition arriving one level down.
#
# `title` is the one field past §N's enumeration, and it is here because §N's
# own L1 text is a list rather than a closed set and because a list of opaque
# session refs does not let a person pick one. It is a name the provider
# already gives the session, not content read out of it — the prohibition
# above is about volume, and a label is not a transcript. A test asserts the
# card equals this tuple exactly, so the next field added has to be argued for
# here rather than arriving silently.
CARD_FIELDS = ("session_ref", "runtime", "device", "bound_item", "status",
               "last_activity", "title", "level")


def level_l1(rows: list[dict], *, bindings: dict | None = None) -> dict:
    """One card per session: §N's six facts, and the join this plane exists for.

    `bound_item` comes from the §17 thread binding, which lives in the agent
    control store and not in any provider. Neither plane can produce this card
    alone — the provider knows the session and not what it is for, and the
    binding store knows what it is for and not whether it is alive — and the
    join is done here, on rows the caller already fetched, so the console owns
    neither half.
    """
    bound = bindings if isinstance(bindings, dict) else {}
    cards = []
    for row in rows:
        ref = str(row.get("session_ref") or "")
        binding = bound.get(ref)
        cards.append({
            "level": "L1",
            "session_ref": ref,
            "runtime": row.get("agent_runtime") or "UNKNOWN",
            "device": row.get("device_ref") or "UNKNOWN",
            "bound_item": _bound_item(binding),
            "status": row.get("status") or "UNKNOWN",
            "last_activity": row.get("last_activity"),
            "title": row.get("title"),
        })
    return {"level": "L1", "state": "KNOWN", "sessions": cards,
            "count": len(cards)}


def _bound_item(binding: dict | None) -> dict | None:
    """What §17 says this thread is for, or a state word saying why not."""
    if not isinstance(binding, dict):
        return None
    bound = str(binding.get("bound_item_ref") or "").strip()
    if bound:
        return {"item_ref": bound, "session_role": binding.get("session_role"),
                "state": binding.get("state")}
    return {"item_ref": None,
            "session_role": binding.get("session_role"),
            "state": binding.get("state"),
            "because": ("this thread is a coordinator or was never bound to an "
                        "Item; that is a fact about the thread and not a "
                        "missing read")}


def level_l2(rows: list[dict], *, bindings: dict | None = None,
             action_support: dict | None = None,
             features: dict | None = None,
             selected: str | None = None) -> dict:
    """The cards, plus what may be done — and only what may actually be done.

    §N's L2 is the level a person acts from: send, continue, see what it is
    waiting on, close, and the provider-specific extras. §P says the extras are
    decided by the feature vocabulary, which is `operation_states`'s
    `requires_feature` gate rather than a list written here.

    An operation the provider does not support is RETURNED and marked, not
    omitted. §M requires the console to show `interrupt = UNSUPPORTED` where
    the transport supports close and not interrupt, and a console that dropped
    the entry would leave a person unable to tell "this cannot be done" from
    "nobody thought of it".
    """
    card_level = level_l1(rows, bindings=bindings)
    states = operation_states(action_support, features=features)
    offered = [op for op in operations_at("L2") if not op.local]
    return {
        "level": "L2",
        "state": "KNOWN",
        "sessions": card_level["sessions"],
        "selected": selected,
        "operations": [
            {"name": op.name, "level": op.level,
             "destructive": op.destructive,
             **states.get(op.name, {"state": UNKNOWN,
                                    "because": "no state was computed"})}
            for op in offered
        ] + [
            {"name": op.name, "level": op.level, "destructive": op.destructive,
             **states[op.name]}
            for op in operations_at("L2") if op.local
        ],
        "note": ("`UNSUPPORTED` here is the transport's answer about itself and "
                 "not a missing implementation; `AVAILABLE` is that the "
                 "provider has the verb, not that this call is admitted — "
                 "admission is decided per call and by the router"),
    }


def level_l3(rows: list[dict], *, bindings: dict | None = None,
             action_support: dict | None = None,
             features: dict | None = None,
             selected: str | None = None) -> dict:
    """Diagnostics: who is behind this session, and how it came to say so.

    §N's L3 is provider, backend session, provenance and support state. All
    four are copied from the row the provider produced and none is
    interpreted — the same rule `agent-session-contract.yaml#evidence` states,
    for the same reason: a diagnostic that has been normalised is a fresh
    claim, and the caller asking at L3 is asking precisely because they no
    longer trust the summary.
    """
    l2 = level_l2(rows, bindings=bindings, action_support=action_support,
                  features=features, selected=selected)
    by_ref = {str(r.get("session_ref") or ""): r for r in rows}
    diagnostics = []
    for card in l2["sessions"]:
        row = by_ref.get(card["session_ref"], {})
        diagnostics.append({
            "session_ref": card["session_ref"],
            "provider": row.get("provider"),
            "provider_session_id": row.get("provider_session_id"),
            "provider_agent_id": row.get("provider_agent_id"),
            "native_runtime": row.get("native_runtime"),
            "transport": row.get("transport"),
            "provider_status": row.get("provider_status"),
            "status_provenance": row.get("status_provenance"),
            "status_reason": row.get("status_reason"),
            "provider_evidence": row.get("provider_evidence"),
            "can_accept_input": row.get("can_accept_input"),
            "workspace": row.get("workspace"),
            "created_at": row.get("created_at"),
        })
    return {**l2, "level": "L3", "diagnostics": diagnostics,
            "features": _feature_rows(features),
            "note": ("every field here is the provider's own, copied verbatim "
                     "and never interpreted")}


def _feature_rows(features: dict | None) -> dict:
    if not features:
        return unknown("no runtime binding was read for these sessions")
    return {str(k): (v.get("state") if isinstance(v, dict) else v)
            for k, v in features.items()}


def levels(level: str, rows: list[dict], **kwargs: Any) -> dict:
    """The payload for one level, dispatched by name.

    One entry point so that a caller cannot reach L3's diagnostics by asking
    for a payload shape of its own, and so that an unknown level fails here —
    where the levels are — rather than at the caller.
    """
    if level == "L0":
        return level_l0(rows, devices=kwargs.get("devices"))
    if level == "L1":
        return level_l1(rows, bindings=kwargs.get("bindings"))
    if level == "L2":
        return level_l2(rows, bindings=kwargs.get("bindings"),
                        action_support=kwargs.get("action_support"),
                        features=kwargs.get("features"),
                        selected=kwargs.get("selected"))
    if level == "L3":
        return level_l3(rows, bindings=kwargs.get("bindings"),
                        action_support=kwargs.get("action_support"),
                        features=kwargs.get("features"),
                        selected=kwargs.get("selected"))
    raise ConsoleRefused(
        "INVALID_CONTEXT",
        f"{level!r} is not a level; there are {', '.join(LEVELS)}")


# ---------------------------------------------------------------------------
# Selection. The one thing §M adds that no provider action covers.
# ---------------------------------------------------------------------------
# What a selection IS. The two fields below are §17's binding fields, and the
# disjointness is the whole design rather than a tidiness: a selection record
# carries no field naming an Item or a role, so "selecting a session moved the
# thread to another Item" is not a rule that has to hold — it is a sentence
# that cannot be written from this record.
SELECTION_FIELDS = ("conversation_ref", "session_ref", "device_ref",
                    "selected_at", "identity")
THREAD_BINDING_FIELDS = ("session_role", "bound_item_ref")

# There is no verified actor to record, and inventing one is the single thing
# §AA forbids most plainly. The word says which absence it is.
#
# Both words exist even though only one can be produced today, because a
# vocabulary with one member cannot express the difference between "nobody
# verified this" and "verification is not a thing that could happen" — and
# §AA's whole instruction is that the console must not collapse those.
IDENTITY_UNVERIFIED = "UNVERIFIED"
IDENTITY_VERIFIED = "VERIFIED"
IDENTITIES = (IDENTITY_UNVERIFIED, IDENTITY_VERIFIED)


def selection_record(*, conversation_ref: str, session_ref: str,
                     device_ref: str | None = None, at: str,
                     identity: str | None = None) -> dict:
    """Build a selection, or refuse and say which word the refusal is.

    A selection is a pointer FROM a conversation TO a session, and both ends
    have to be named. A pointer from nowhere is not an empty selection; it is a
    record no reader can ever match, and storing one would leave a later reader
    unable to tell it from a conversation that never selected anything.

    `identity` is what the TRANSPORT established about who is asking, and this
    function does not establish it — §AA's `human.identity.verify` is UNKNOWN
    upstream, so a caller that passes nothing gets `UNVERIFIED` and that is the
    ordinary case today. It is a parameter rather than a literal because a
    literal would keep writing `UNVERIFIED` on the day verification exists,
    with nothing to notice: the record would go on being wrong in the one
    direction that looks like success.
    """
    conversation = str(conversation_ref or "").strip()
    if not conversation:
        raise ConsoleRefused(
            "INVALID_CONTEXT",
            "a selection names the conversation it is for; without one it is a "
            "pointer from nowhere, and no later read could ever find it")
    session = str(session_ref or "").strip()
    if not session:
        raise ConsoleRefused(
            "INVALID_CONTEXT",
            "a selection names the session it points at; selecting nothing is "
            "not a selection")
    who = str(identity).strip() if identity is not None else IDENTITY_UNVERIFIED
    if who not in IDENTITIES:
        raise ConsoleRefused(
            "INVALID_CONTEXT",
            f"identity {identity!r} is not one of {IDENTITIES}; whether the "
            f"transport verified the person is a word a reader has to be able "
            f"to compare, not free text")
    return {
        "conversation_ref": conversation,
        "session_ref": session,
        "device_ref": (str(device_ref).strip() or None) if device_ref else None,
        "selected_at": at,
        "identity": who,
    }


def selection_axis(record: dict | None, *, live_refs: list[str] | None = None) -> dict:
    """What a stored selection is worth NOW, which is not what it was worth then.

    Durability is not authority — the rule `runtime/agent_control_store.py`
    states for approvals applies here in the same shape: a selection read back
    from disk is not re-verified by having been read. The session it names may
    have been closed, or may belong to a conversation that has moved on, and
    the reader is told which of those it is looking at.
    """
    if not isinstance(record, dict):
        return {"state": "NONE",
                "because": ("this conversation has selected nothing; that is "
                            "the state a console starts in, not an error")}
    ref = str(record.get("session_ref") or "")
    if live_refs is not None and ref not in live_refs:
        return {"state": "STALE",
                "session_ref": ref,
                "selected_at": record.get("selected_at"),
                "identity": record.get("identity"),
                "because": ("the session this conversation selected is not in "
                            "the list the provider just gave; it was closed, "
                            "or it is on a device this call did not read. The "
                            "selection is kept rather than cleared, so the "
                            "person is told what happened to what they chose "
                            "instead of finding nothing chosen")}
    return {"state": "SELECTED", "session_ref": ref,
            "selected_at": record.get("selected_at"),
            "identity": record.get("identity")}
