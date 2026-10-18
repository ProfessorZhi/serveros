import unittest

from workengine.model import (
    ActivityState,
    ContextMode,
    ItemState,
    Outcome,
    RunState,
    WorkItem,
)
from workengine.state_machine import IllegalTransition, reopen, transition


class ServerOSModelTest(unittest.TestCase):
    def test_item_is_not_a_pr_or_worktree(self):
        item = WorkItem(
            id="LIT-1042",
            project="litellm",
            title="test merged revision",
            kind="test",
            card_locator="records/projects/litellm/items/LIT-1042/card.yaml",
        )
        self.assertEqual(item.state, ItemState.INBOX)
        self.assertEqual(item.outcome, None)
        self.assertFalse(hasattr(item, "worktree"))
        self.assertFalse(hasattr(item, "pr"))

    def test_mainline_is_one_step_and_cancel_requires_reopen(self):
        self.assertEqual(transition(ItemState.INBOX, ItemState.AVAILABLE), ItemState.AVAILABLE)
        with self.assertRaises(IllegalTransition):
            transition(ItemState.INBOX, ItemState.ACTIVE)
        with self.assertRaises(IllegalTransition):
            transition(ItemState.CANCELLED, ItemState.INBOX)
        self.assertEqual(reopen(ItemState.CANCELLED), ItemState.AVAILABLE)

    def test_side_state_requires_reason_and_return_state(self):
        with self.assertRaises(ValueError):
            transition(ItemState.ACTIVE, ItemState.WAITING)
        result = transition(
            ItemState.ACTIVE,
            ItemState.WAITING,
            reason="upstream unavailable",
            return_state=ItemState.ACTIVE,
            unblock_condition="upstream responds",
        )
        self.assertEqual(result, ItemState.WAITING)

    def test_closed_item_can_record_failed_test_outcome(self):
        item = WorkItem(
            id="LIT-1042",
            project="litellm",
            title="test merged revision",
            kind="test",
            card_locator="records/projects/litellm/items/LIT-1042/card.yaml",
            state=ItemState.CLOSED,
            outcome=Outcome.FAIL,
        )
        self.assertEqual(item.state, ItemState.CLOSED)
        self.assertEqual(item.outcome, Outcome.FAIL)

    def test_controlled_vocabularies_cover_non_code_work(self):
        self.assertEqual(ContextMode.TEST.value, "test")
        self.assertEqual(ActivityState.BLOCKED.value, "BLOCKED")
        self.assertEqual(RunState.FINALIZING.value, "FINALIZING")
        self.assertIn("support", WorkItem.ALLOWED_KINDS)


if __name__ == "__main__":
    unittest.main()
