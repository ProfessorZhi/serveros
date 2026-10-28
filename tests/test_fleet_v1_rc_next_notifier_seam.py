#!/usr/bin/env python3
"""RC-next integration QA — the one seam #459 needs from #423's notifier.

WHY THIS FILE EXISTS
--------------------
`integrations/fleet-v1-milestone-batch/` (#459) correlates one dispatch's refs
and sends one summary. It refuses to write a second outbound path: every message
goes through #423's notifier, the claim/receipt ledger and the transport key
that already exist. That leaves exactly one thing missing, and #459's own code
says so at construction time:

    BatchAggregator(..., notifier=<#423's MilestoneNotifier>)
    -> BatchRefused("NOTIFIER_SEAM_MISSING")

The public method #459 calls does not exist yet:

    def send_text(self, *, delivery_key, milestone_key, outcome, text, why)

#459's suite covers the batch side against a *subclass that writes the patch
out* (`SeamedNotifier`). That is the right test for the correlation logic and it
is not evidence that the seam exists: a subclass in a test file proves the
contract is writable, not that its owner wrote it. This file gates the other
half — the real class, in #423's module.

WHAT THIS FILE GATES
--------------------
1. The method's contract — keyword-only, five names — is the same three places
   it is written down: #459's call site, #459's README patch, and (once it
   lands) the real class. Drift between any two is a refusal here. Green today.
2. **The merge gate**: `integrations/fleet-v1-milestone-notify/
   milestone_notify.py#MilestoneNotifier` defines `send_text` itself. It is
   `expectedFailure` today, on purpose — a red would be a second copy of the
   blocker the RC document already names, and a green here after the seam lands
   surfaces as an "unexpected success" which tells the next reader to delete the
   decorator. Neither #423's module nor #459's batch code is edited by this lane.
3. The refusal is honest while it lasts: given the real notifier, the aggregator
   raises `NOTIFIER_SEAM_MISSING` rather than reaching for `_send`. Green today,
   and it must stay green in the "seam absent" state.
4. #459's module never touches the private sender: an AST scan of
   `batch.py` finds no `_send` attribute access at all. This is the guard that
   stops the blocker being "fixed" by writing the second path.

LAYERS: CODE (reads #423's and #459's modules) / UNIT (unittest; no provider, no
network, no service, no credential, no clock).
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTIFY = REPO / "integrations" / "fleet-v1-milestone-notify" / "milestone_notify.py"
BATCH_DIR = REPO / "integrations" / "fleet-v1-milestone-batch"
BATCH = BATCH_DIR / "batch.py"
README = BATCH_DIR / "README.md"

#: The seam, as #459's call site passes it. Every name is keyword-only: a
#: positional variant would let a caller put `text` where `outcome` belongs and
#: the ledger would record a summary as a milestone.
SEAM_KEYWORDS = ("delivery_key", "milestone_key", "outcome", "text", "why")


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: `@dataclass` resolves annotations through
    # `sys.modules[cls.__module__]` and reads None otherwise.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


notify = _load("rc_next_milestone_notify", NOTIFY)
batch = _load("rc_next_milestone_batch", BATCH)


def call_site_keywords() -> list[str]:
    """The keywords #459 actually passes, read from its own AST."""
    tree = ast.parse(BATCH.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "send_text"):
            if node.args:
                raise AssertionError(
                    "the batch passes send_text a positional argument; the seam "
                    "is keyword-only so the sentence cannot land in the wrong field")
            found.append(",".join(sorted(keyword.arg for keyword in node.keywords
                                         if keyword.arg)))
    return found


def readme_keywords() -> list[str]:
    """The signature #459's README proposes, from the fenced patch block."""
    text = README.read_text(encoding="utf-8")
    marker = "def send_text(self, *,"
    start = text.index(marker)
    # The closing paren is the one that ends the parameter list, before `->`;
    # a later one would belong to the body.
    end = text.index(")", start)
    head = text[start + len("def send_text(self, *,"):end]
    return sorted(part.split(":")[0].strip() for part in head.split(",")
                  if part.strip())


class TheContractIsWrittenTheSameWayEverywhere(unittest.TestCase):

    def test_the_batch_calls_five_keywords(self):
        calls = call_site_keywords()
        self.assertEqual(calls, [",".join(sorted(SEAM_KEYWORDS))],
                         "the aggregator's call site is the seam's definition of "
                         "record; a second call shape would be a second contract")

    def test_the_readme_patch_proposes_the_same_five(self):
        self.assertEqual(sorted(readme_keywords()), sorted(SEAM_KEYWORDS),
                         "the patch #423 is asked to apply and the call #459 "
                         "makes must name the same parameters")

    def test_the_proposed_body_goes_through_the_ledger(self):
        """The seam is `observe`'s outbound half, not a bypass of it."""
        text = README.read_text(encoding="utf-8")
        for required in ("transport_key_for(delivery_key)", "self.ledger.claim(",
                         "self.ledger.finish(", "self._send(text, transport)"):
            self.assertIn(required, text,
                          f"the proposed seam must reuse {required}; a seam that "
                          f"reaches the provider another way is the second path "
                          f"#459 exists to avoid")


class TheSeamIsNotYetOnItsOwnersClass(unittest.TestCase):
    """The BLOCKED_DEPENDENCY(#423) half, stated so it cannot go quiet."""

    @unittest.expectedFailure
    def test_the_notifier_defines_the_seam_itself(self):
        """BLOCKED_DEPENDENCY(#423): `MilestoneNotifier.send_text` is missing.

        This is the merge gate for the sender seam. It is `expectedFailure`
        today so the tree carries no deliberately-red test; when #423's owner
        lands the method this becomes an UNEXPECTED SUCCESS, the run fails, and
        the next reader deletes the decorator. Defined *in the class body*
        (`__dict__`), not inherited or attached: the seam is the owner's word,
        not a subclass's.
        """
        self.assertIn("send_text", notify.MilestoneNotifier.__dict__,
                      "#459's aggregator must be able to construct with the real "
                      "notifier; a subclass in a test file does not satisfy this")
        signature = inspect.signature(notify.MilestoneNotifier.send_text)
        keyword_only = tuple(
            name for name, parameter in signature.parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(tuple(sorted(keyword_only)), tuple(sorted(SEAM_KEYWORDS)),
                         "the seam is keyword-only and carries exactly the five "
                         "names #459 passes")

    def test_the_public_surface_today_has_no_text_seam(self):
        """Positive control for the gate above: it is red for the stated reason.

        If this ever passes while the gate above still xfails, the gate is
        failing for some other reason and its message is lying.
        """
        public = sorted(name for name in vars(notify.MilestoneNotifier)
                        if not name.startswith("_"))
        self.assertEqual(public, ["observe", "observe_payload", "reconcile"],
                         "the notifier's public surface changed; re-read why the "
                         "batch refuses it before changing this expectation")


class TheRefusalIsHonestWhileItLasts(unittest.TestCase):
    """A missing seam must be refused by name, up front, not at send time."""

    def setUp(self):
        self.declaration = batch.Batch(
            batch_id="rc-next-seam-probe",
            members=(batch.BatchMember(source="cloudcli", ref="run-1",
                                       device_ref="device:tokyo",
                                       boot_id="boot-rc-next"),))

    def test_the_real_notifier_is_accepted_only_when_the_seam_exists(self):
        # An instance without `__init__`: attribute lookup still resolves on the
        # class, and no provider, ledger or transport is constructed — this test
        # asks what the class publishes, not what a send does.
        notifier = notify.MilestoneNotifier.__new__(notify.MilestoneNotifier)
        has_seam = callable(getattr(notifier, "send_text", None))
        if has_seam:
            batch.BatchAggregator(self.declaration, notifier=notifier)
            self.assertIn("send_text", notify.MilestoneNotifier.__dict__,
                          "a seam that only a subclass supplies is not the seam")
        else:
            with self.assertRaises(batch.BatchRefused) as caught:
                batch.BatchAggregator(self.declaration, notifier=notifier)
            self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")

    def test_the_batch_never_touches_the_private_sender(self):
        tree = ast.parse(BATCH.read_text(encoding="utf-8"))
        private = [(node.lineno, node.attr) for node in ast.walk(tree)
                   if isinstance(node, ast.Attribute) and node.attr == "_send"]
        self.assertEqual(private, [], (
            "the batch reaches for the notifier's private sender; that is the "
            "second write path to the one provider, and the only acceptable "
            "resolution is #423 publishing `send_text`"))

    def test_a_duck_notifier_is_refused_by_the_same_name(self):
        with self.assertRaises(batch.BatchRefused) as caught:
            batch.BatchAggregator(self.declaration, notifier=object())
        self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")


if __name__ == "__main__":
    unittest.main()
