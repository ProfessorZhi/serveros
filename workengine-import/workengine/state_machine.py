"""Work Item state machine.

The rule table is the single authority for legal transitions. ``transition``
rejects any move not in the table, so an illegal state migration raises
``IllegalTransition`` rather than silently corrupting state.

Transition model
----------------
* Forward chain::

      NEW -> TRIAGED -> READY -> IMPLEMENTING -> VERIFYING
           -> PR_OPEN -> REVIEWING -> READY_TO_MERGE -> MERGED

  Legal moves are one step forward only (no skipping).

* Bypass states ``BLOCKED`` / ``NEEDS_HUMAN`` may be entered from any live
  state and exited back into any live forward-chain state, or into each
  other, or into ``CANCELLED``.

* ``MERGED`` and ``CANCELLED`` are terminal: no outgoing transitions.
"""

from __future__ import annotations

from .model import State

FORWARD_CHAIN: list[State] = [
    State.NEW,
    State.TRIAGED,
    State.READY,
    State.IMPLEMENTING,
    State.VERIFYING,
    State.PR_OPEN,
    State.REVIEWING,
    State.READY_TO_MERGE,
    State.MERGED,
]

BYPASS_STATES: frozenset[State] = frozenset({State.BLOCKED, State.NEEDS_HUMAN})
TERMINAL_STATES: frozenset[State] = frozenset({State.MERGED, State.CANCELLED})

# Live forward-chain states a bypass can return to (forward chain minus terminal).
_REFORWARD: frozenset[State] = frozenset(s for s in FORWARD_CHAIN if s not in TERMINAL_STATES)


class IllegalTransition(Exception):
    """Raised when a requested state move is not permitted by the rule table."""

    def __init__(self, from_state: State, to_state: State, allowed) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = sorted(allowed, key=lambda s: s.value)
        super().__init__(
            f"Illegal transition {from_state.value} -> {to_state.value}; "
            f"allowed from {from_state.value}: {[s.value for s in self.allowed]}"
        )


def _build_table() -> dict[State, frozenset[State]]:
    table: dict[State, frozenset[State]] = {}

    # Forward chain: one-step forward, plus bypass escape hatches, plus cancel.
    for i, state in enumerate(FORWARD_CHAIN):
        allowed: set[State] = set()
        if i + 1 < len(FORWARD_CHAIN):
            allowed.add(FORWARD_CHAIN[i + 1])
        allowed.update(BYPASS_STATES)
        allowed.add(State.CANCELLED)
        if state in TERMINAL_STATES:
            allowed = set()  # terminal: no outgoing
        table[state] = frozenset(allowed)

    # Bypass states: free re-entry into any live forward-chain state,
    # cross to the other bypass, or cancel.
    table[State.BLOCKED] = frozenset(_REFORWARD | {State.NEEDS_HUMAN, State.CANCELLED})
    table[State.NEEDS_HUMAN] = frozenset(_REFORWARD | {State.BLOCKED, State.CANCELLED})

    # Terminal states (explicit, in case not covered above).
    table[State.MERGED] = frozenset()
    table[State.CANCELLED] = frozenset()

    return table


TRANSITIONS: dict[State, frozenset[State]] = _build_table()


def allowed_transitions(state: State | str) -> frozenset[State]:
    """Return the set of states reachable from ``state`` (empty if terminal)."""
    state = State(state) if not isinstance(state, State) else state
    return TRANSITIONS.get(state, frozenset())


def can_transition(from_state: State | str, to_state: State | str) -> bool:
    """True iff ``from -> to`` is a legal move."""
    to_state = State(to_state) if not isinstance(to_state, State) else to_state
    return to_state in allowed_transitions(from_state)


def transition(from_state: State | str, to_state: State | str) -> State:
    """Validate and return ``to_state``; raise ``IllegalTransition`` if invalid."""
    from_state = State(from_state) if not isinstance(from_state, State) else from_state
    to_state = State(to_state) if not isinstance(to_state, State) else to_state
    allowed = allowed_transitions(from_state)
    if to_state not in allowed:
        raise IllegalTransition(from_state, to_state, allowed)
    return to_state
