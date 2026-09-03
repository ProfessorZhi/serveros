"""Tests for the Work Item state machine rule table."""

from __future__ import annotations

import unittest

from workengine.model import State
from workengine.state_machine import (
    FORWARD_CHAIN,
    IllegalTransition,
    allowed_transitions,
    can_transition,
    transition,
)


class StateMachineTest(unittest.TestCase):
    def test_forward_chain_order_and_completeness(self):
        # The canonical delivery pipeline, in order.
        chain = [s.value for s in FORWARD_CHAIN]
        self.assertEqual(
            chain,
            [
                "NEW",
                "TRIAGED",
                "READY",
                "IMPLEMENTING",
                "VERIFYING",
                "PR_OPEN",
                "REVIEWING",
                "READY_TO_MERGE",
                "MERGED",
            ],
        )

    def test_every_forward_step_is_legal(self):
        for i in range(len(FORWARD_CHAIN) - 1):
            self.assertTrue(can_transition(FORWARD_CHAIN[i], FORWARD_CHAIN[i + 1]))

    def test_forward_chain_is_one_step_only(self):
        # Skipping any state must be rejected.
        for i in range(len(FORWARD_CHAIN) - 2):
            with self.assertRaises(IllegalTransition):
                transition(FORWARD_CHAIN[i], FORWARD_CHAIN[i + 2])

    def test_backwards_is_illegal(self):
        with self.assertRaises(IllegalTransition):
            transition(State.MERGED, State.READY_TO_MERGE)
        with self.assertRaises(IllegalTransition):
            transition(State.TRIAGED, State.NEW)

    def test_terminal_states_have_no_outgoing(self):
        for terminal in (State.MERGED, State.CANCELLED):
            self.assertEqual(allowed_transitions(terminal), frozenset())
            for other in State:
                if other is not terminal:
                    self.assertFalse(can_transition(terminal, other))

    def test_bypass_entry_from_any_live_state(self):
        live = [s for s in FORWARD_CHAIN if s is not State.MERGED]
        for s in live:
            self.assertTrue(can_transition(s, State.BLOCKED))
            self.assertTrue(can_transition(s, State.NEEDS_HUMAN))

    def test_cancel_from_any_live_state(self):
        live = [s for s in FORWARD_CHAIN if s is not State.MERGED]
        for s in live:
            self.assertTrue(can_transition(s, State.CANCELLED))

    def test_bypass_return_to_forward_chain(self):
        for bypass in (State.BLOCKED, State.NEEDS_HUMAN):
            for target in [s for s in FORWARD_CHAIN if s is not State.MERGED]:
                self.assertTrue(can_transition(bypass, target))

    def test_bypass_cross_and_cancel(self):
        self.assertTrue(can_transition(State.BLOCKED, State.NEEDS_HUMAN))
        self.assertTrue(can_transition(State.NEEDS_HUMAN, State.BLOCKED))
        self.assertTrue(can_transition(State.BLOCKED, State.CANCELLED))
        self.assertTrue(can_transition(State.NEEDS_HUMAN, State.CANCELLED))

    def test_transition_returns_target(self):
        self.assertIs(transition(State.NEW, State.TRIAGED), State.TRIAGED)

    def test_transition_accepts_strings(self):
        self.assertIs(transition("NEW", "TRIAGED"), State.TRIAGED)

    def test_illegal_transition_message_includes_allowed(self):
        with self.assertRaises(IllegalTransition) as ctx:
            transition(State.NEW, State.MERGED)
        self.assertIn("NEW -> MERGED", str(ctx.exception))
        self.assertIn("TRIAGED", str(ctx.exception))  # allowed list surfaced


if __name__ == "__main__":
    unittest.main()
