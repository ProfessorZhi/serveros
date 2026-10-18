"""ServerOS v2 Item and Run transition rules."""

from __future__ import annotations

from .model import ItemState, RunState

MAINLINE = (
    ItemState.INBOX,
    ItemState.AVAILABLE,
    ItemState.READY,
    ItemState.ACTIVE,
    ItemState.DELIVERED,
    ItemState.CLOSED,
)
SIDE_STATES = frozenset({ItemState.WAITING, ItemState.NEEDS_HUMAN, ItemState.DELEGATED})


class IllegalTransition(ValueError):
    def __init__(self, from_state, to_state, allowed=()) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = tuple(allowed)
        super().__init__(
            f"illegal transition {from_state.value} -> {to_state.value}; "
            f"allowed: {[state.value for state in self.allowed]}"
        )


def allowed_transitions(state: ItemState | str) -> frozenset[ItemState]:
    state = ItemState(state)
    if state in {ItemState.CLOSED, ItemState.CANCELLED}:
        return frozenset()
    if state in SIDE_STATES:
        return frozenset(set(MAINLINE[:-1]) | SIDE_STATES | {ItemState.CANCELLED})
    index = MAINLINE.index(state)
    allowed = set(SIDE_STATES) | {ItemState.CANCELLED}
    if index + 1 < len(MAINLINE):
        allowed.add(MAINLINE[index + 1])
    return frozenset(allowed)


def can_transition(from_state: ItemState | str, to_state: ItemState | str) -> bool:
    return ItemState(to_state) in allowed_transitions(from_state)


def transition(
    from_state: ItemState | str,
    to_state: ItemState | str,
    *,
    reason: str = "",
    return_state: ItemState | str | None = None,
    unblock_condition: str = "",
) -> ItemState:
    from_state = ItemState(from_state)
    to_state = ItemState(to_state)
    allowed = allowed_transitions(from_state)
    if to_state not in allowed:
        raise IllegalTransition(from_state, to_state, allowed)
    if to_state in SIDE_STATES:
        if not reason.strip() or return_state is None or not unblock_condition.strip():
            raise ValueError("side-state transition requires reason, return_state, and unblock_condition")
        target = ItemState(return_state)
        if target not in MAINLINE[:-1]:
            raise ValueError("side-state return_state must be a live mainline state")
    return to_state


def reopen(state: ItemState | str) -> ItemState:
    state = ItemState(state)
    if state not in {ItemState.CANCELLED, ItemState.CLOSED}:
        raise IllegalTransition(state, ItemState.AVAILABLE, allowed_transitions(state))
    return ItemState.AVAILABLE


RUN_CHAIN = (
    RunState.ALLOCATED,
    RunState.PREPARING,
    RunState.RUNNING,
    RunState.FINALIZING,
)
RUN_TERMINAL = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INVALID})


def transition_run(from_state: RunState | str, to_state: RunState | str) -> RunState:
    current = RunState(from_state)
    target = RunState(to_state)
    allowed: set[RunState] = set()
    if current in RUN_CHAIN:
        index = RUN_CHAIN.index(current)
        if index + 1 < len(RUN_CHAIN):
            allowed.add(RUN_CHAIN[index + 1])
        else:
            allowed.update({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INVALID})
        allowed.update({RunState.CANCELLED, RunState.INVALID})
        if current in {RunState.PREPARING, RunState.RUNNING}:
            allowed.add(RunState.FAILED)
    if target not in allowed:
        raise ValueError(f"illegal run transition {current.value} -> {target.value}")
    return target
