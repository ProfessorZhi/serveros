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

The public method #459 calls did not exist on #423's class:

    def send_text(self, *, delivery_key, milestone_key, outcome, text, why)

#459's suite covered the batch side against a *subclass that writes the patch
out* (`SeamedNotifier`). That is the right test for the correlation logic and it
is not evidence that the seam exists: a subclass in a test file proves the
contract is writable, not that its owner wrote it.

**The seam landed**: #460 `4fc9fcbc` publishes `send_text` on
`milestone_notify.py#MilestoneNotifier`, and #464 `d72befe7` supplies the
durable registration and the batch side. Read against the composed tree, this
file's `expectedFailure` turned into an **unexpected success** — the signal it
was written to produce — so the decorator is gone and the class asserts the
seam's presence instead. The cross-PR requirement (measure the real class, not
a sealed stub) is met by loading #460's module and #459/#464's batch from the
same tree.

WHAT THIS FILE GATES
--------------------
1. The method's contract — keyword-only, five names — is the same three places
   it is written down: the batch's call site, the batch's README patch, and the
   real class. Drift between any two is a refusal here.
2. **The gate**: `MilestoneNotifier` defines `send_text` in its own class body
   (`__dict__`), keyword-only, exactly the five names. A subclass or an
   instance attribute would not satisfy it.
3. The real notifier is accepted by `BatchAggregator`'s construction-time
   check — i.e. #459's refusal is a bridge, not a second design.
4. Neither module reaches for the private sender: AST scans find no `_send`
   attribute access in `batch.py` (and the seam's own `_send` call stays inside
   #423's class). This is the guard that stops the seam being "fixed" by
   writing the second write path.

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
    # A synthetic name is process-wide, so the consult and the write have to be
    # one shape: `sys.modules.get(name)` alone does not stop a second writer
    # from displacing the first (tests/loader_graph.py#unguarded_registrants).
    if name in sys.modules:
        return sys.modules[name]
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


class TheSeamIsOnItsOwnersClass(unittest.TestCase):
    """The gate, in the state #460 left it: present, on the class, five names."""

    def test_the_notifier_defines_the_seam_itself(self):
        """Defined *in the class body* (`__dict__`), not inherited or attached.

        The seam is the owner's word, not a subclass's: #459's `SeamedNotifier`
        lives in a test file and proves the contract is writable, which is a
        different claim.
        """
        self.assertIn("send_text", notify.MilestoneNotifier.__dict__,
                      "#460 published the seam; if it moved to a subclass or an "
                      "instance attribute the batch is talking to something the "
                      "owner did not write")
        signature = inspect.signature(notify.MilestoneNotifier.send_text)
        keyword_only = tuple(
            name for name, parameter in signature.parameters.items()
            if parameter.kind is inspect.Parameter.KEYWORD_ONLY)
        self.assertEqual(tuple(sorted(keyword_only)), tuple(sorted(SEAM_KEYWORDS)),
                         "the seam is keyword-only and carries exactly the five "
                         "names the batch passes")

    def test_the_public_surface_carries_the_seam(self):
        """Companion reading: the seam is public, not a private alias."""
        public = sorted(name for name in vars(notify.MilestoneNotifier)
                        if not name.startswith("_"))
        self.assertEqual(public,
                         ["observe", "observe_payload", "reconcile", "send_text"],
                         "the notifier's public surface changed; re-read what the "
                         "batch calls before changing this expectation")


class TheRefusalWasABridge(unittest.TestCase):
    """What the refusal was for, and what it must still refuse."""

    def setUp(self):
        self.declaration = batch.Batch(
            batch_id="rc-next-seam-probe",
            members=(batch.BatchMember(source="cloudcli", ref="run-1",
                                       device_ref="device:tokyo",
                                       boot_id="boot-rc-next"),))

    def test_the_real_notifier_is_accepted(self):
        # An instance without `__init__`: attribute lookup still resolves on the
        # class, and no provider, ledger or transport is constructed — this test
        # asks what the class publishes, not what a send does.
        notifier = notify.MilestoneNotifier.__new__(notify.MilestoneNotifier)
        batch.BatchAggregator(self.declaration, notifier=notifier)
        self.assertIn("send_text", notify.MilestoneNotifier.__dict__,
                      "a seam that only a subclass supplies is not the seam")

    def test_the_durable_registration_side_is_present(self):
        """#464's half: the aggregator is not the only thing #459 needed."""
        self.assertTrue((BATCH_DIR / "registration.py").is_file(),
                        "#464 supplies durable Batch registration; without it the "
                        "#459 batch correlates in memory and forgets across restart")

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
