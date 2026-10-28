#!/usr/bin/env python3
"""Wave 7 landing ledger — the three V1 stacks have NOT landed, and here is how
that is measured rather than remembered.

WHY THIS FILE EXISTS
--------------------
Wave 7 mapped the minimal V1 source delta of three stacked changes onto the RC
(`docs/integration/FLEET-V1-RC-WAVE7-20261028.md`, §5):

* Node   — #461 `b042f515` ⊂ #467 `ccd045258e`
* Hub    — #466 `263488289e`
* Receipt— #465 `1a162c44` ⊂ #468 `7d2278ec`

None of them may be merged into this RC: each is rooted on #407, whose tree is an
85-file V2 Draft, and a `git merge-tree` against any of them would carry all 85
files. What Wave 7 did instead was extract a named, minimal file set per stack
into a throwaway worktree and measure what happened.

That measurement decays the moment somebody lands *part* of a stack. A half-landed
stack is the worst state available: `fleet_mcp/principals.py` without the node cut
is a module nothing imports, the node cut without `principals.py` refuses to build
its own pin, and the receipt cut without `dispatch.py` raises
`ValueError: send acknowledgement has no execution reference` on a delivery-only
reply. Every one of those states is silent until a build runs.

So this file pins the *pre-landing* state of each stack, one assertion per
observable. Each test names what turns it red. They are not a wish that the
stacks never land — they are the tripwire that says "re-read §5 and update it in
the same change that lands this", which is the only thing that keeps a dependency
list honest.

WHAT THIS FILE DOES NOT DO
--------------------------
It does not run the product. It reads the tree: one module's pin, the presence of
four files, and four sentences in other authors' test files. Nothing here is a
claim about behaviour on a device, and nothing here is REAL_DEVICE, CLIENT_E2E or
PRODUCTION evidence.

LAYERS: CODE (reads the composition's own bytes) / UNIT (unittest; no provider,
no network, no service, no credential, no clock).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

L2_RELEASE = REPO / "integrations" / "fleet-v1-l2-release" / "l2_release.py"

#: #431's gate, the one that says `fleet_mcp/**` is untouched by this release.
L2_SOURCE_CONTRACT = REPO / "tests" / "test_l2_source_standalone_contract.py"
#: #431's other gate: the 17-entry pin, the `("fleet",)` allowance, and
#: "edge A is still uncut". All three become false when the cut lands.
L2_SEPARATION = REPO / "tests" / "test_fleet_l2_release_separation.py"
#: #458's capability matrix, which pins a run read to run identifiers only.
L2_CONTROL_MATRIX = REPO / "tests" / "test_l2_cloudcli_control_matrix.py"

PRINCIPALS = REPO / "fleet_mcp" / "principals.py"
ADAPTER_PORT = REPO / "fleet_mcp" / "identity_adapter_port.py"


def _load(name: str, path: Path):
    """Load a module that is *not* on `fleet_mcp`'s import path.

    The synthetic name is process-wide, so the consult and the write have to be
    one shape: `sys.modules.get(name)` alone does not stop a second writer from
    displacing the first (`tests/loader_graph.py#unguarded_registrants`). And the
    registration has to happen *before* `exec_module`: `@dataclass` resolves its
    annotations through `sys.modules[cls.__module__]` and reads `None` otherwise
    — which is exactly the AttributeError #467 cites when it removes #461's
    standalone test.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TheNodeCutHasNotLanded(unittest.TestCase):
    """#461 ⊂ #467. Three observables, each false once the cut lands."""

    def test_the_pin_is_the_uncut_one(self):
        """17 entries, and `fleet_mcp.fleet` among them.

        The cut removes five edges (`audit`, `dispatch`, `fleet`, `privacy`,
        `registry`) and adds one (`principals`): 17 − 5 + 1 = 13. So both numbers
        and the membership are the tripwire. **Turns red when** #467's
        `l2_release.py` (+9/−5 against #431's copy) is landed.
        """
        module = _load("wave7_l2_release", L2_RELEASE)
        pins = tuple(module.NODE_SHARED_SURFACE)
        self.assertEqual(len(pins), 17,
                         "the pin changed size; if the cut landed, update §5.2 of "
                         "the Wave 7 record in the same change")
        self.assertIn("fleet_mcp.fleet", pins,
                      "the node closure still reaches the hub's service through "
                      "`connector`; the cut removes that edge")
        self.assertNotIn("fleet_mcp.principals", pins,
                         "`principals` is reached only once `config` reads it — "
                         "that is #407's config.py, which this RC does not have")

    def test_the_module_the_cut_needs_is_absent(self):
        """`fleet_mcp/principals.py` is a #407-only file, 85 lines, self-contained.

        Measured ladder: #467's five files alone → the pin refuses with
        `the pin lists module(s) the closure no longer reaches:
        ['fleet_mcp.principals']`; add this file → the pin measures. Landing the
        cut without this file is therefore a build that cannot build itself.
        **Turns red when** a stack brings `principals.py` in without the cut (a
        module nothing imports) or with it.
        """
        self.assertFalse(PRINCIPALS.exists(),
                         "`fleet_mcp/principals.py` arrived; the node cut's "
                         "dependency landed — re-measure §5.2 before trusting it")

    def test_edge_a_is_still_uncut_in_the_source(self):
        """`connector` still imports from the `fleet` package.

        #431's `test_deleting_the_lazy_imports_on_a_copy_flips_the_packaging_flags`
        makes the same observation and calls its absence
        "edge A has moved; re-measure HANDOFF-407 before trusting its table".
        This is the cheaper copy of it: the import exists, or the cut landed.
        """
        connector = REPO / "fleet_mcp" / "node" / "connector.py"
        source = _read(connector)
        self.assertTrue(re.search(r"^\s*from \.\.fleet import", source,
                                  re.MULTILINE),
                        "edge A moved: `connector` no longer imports the hub's "
                        "service — the node cut landed and #431's guard "
                        "`test_deleting_the_lazy_imports_on_a_copy_flips_the_"
                        "packaging_flags` will now fail with 'edge A has moved'")


class TheHubAuthorityHasNotLanded(unittest.TestCase):
    """#466. The one stack Wave 7 measured as landable on its own — as long as it
    lands whole."""

    def test_the_adapter_port_is_absent(self):
        """`fleet_mcp/identity_adapter_port.py` is #466's new module (+613/−19
        across three files, `registry.py` the only edit).

        **Turns red when** #466 lands. That is fine and expected — the point is
        that it turns red *here*, in a test named after the landing, rather than
        as an unexplained red in the registry family.
        """
        self.assertFalse(ADAPTER_PORT.exists(),
                         "`identity_adapter_port.py` arrived; #466 landed — "
                         "re-run its standalone suite (19 passed at Wave 7) and "
                         "update §5.3")


class TheReceiptTruthHasNotLanded(unittest.TestCase):
    """#465 ⊂ #468. Two observables: what `run_get` accepts, and what
    `delivery_receipt` does with a delivery-only reply."""

    def test_run_get_does_not_accept_a_delivery_identifier(self):
        """The RC publishes `{runId, session, waitSeconds}` for `run_get`, with no
        `deliveryId`; landing #468 adds it (measured: `{deliveryId, runId,
        session, waitSeconds}`).

        This is the same disagreement #458's `test_a_run_read_addresses_a_run_
        and_never_a_delivery` records from the other side, and the Wave 7 record
        leaves the ruling to #458's owner (document §5.4). **Turns red when**
        #468's `cloudcli_native.py` lands — at which point that test goes red
        too, and the pairing must be decided, not papered over.
        """
        from fleet_mcp import cloudcli_native as native

        schema = native.NATIVE_INPUT_SCHEMAS["run_get"]
        properties = set((schema.get("properties") or {}))
        self.assertNotIn("deliveryId", properties,
                         "`run_get` now accepts a delivery identifier; #468 "
                         "landed. Its pairing with #458's 'never a delivery' "
                         "test must be decided in the same change")
        self.assertIn("runId", properties)

    def test_the_receipt_projection_is_absent(self):
        """"The receipt says WHERE the reading came from" is #468's whole point,
        and it arrives as `receipt_truth` / `RECEIPT_NATIVE_FIELDS` on the CloudCLI
        provider (measured: absent on the RC, present on the extracted tree).

        **Turns red when** #468's `providers/cloudcli.py` lands. Prefer this over
        a text match on `dispatch.delivery_receipt`: a bare
        `{"deliveryId": "d-1"}` raises `ValueError("provider receipt has no
        session address")` on *both* sides, so a call-level probe there would be
        a gate that cannot tell the two states apart — the sort of probe that
        reports agreement because it never looked.
        """
        from fleet_mcp.providers import cloudcli as provider

        self.assertFalse(hasattr(provider, "receipt_truth"),
                         "`receipt_truth` arrived; #468 landed — re-read §5.4, "
                         "including the two #458 conflicts it brings")
        self.assertFalse(hasattr(provider, "RECEIPT_NATIVE_FIELDS"))


class TheOtherAuthorsGatesStillSayWhatTheySaid(unittest.TestCase):
    """Four sentences in files this lane does not own.

    They are the reason the three stacks cannot land yet, and each one is a
    *premise* rather than an assertion about this branch: if a premise moves,
    the dependency gate in §5 is stale and must be re-read. Text-level on
    purpose — these are other authors' files, and a structural assertion here
    would be a second copy of their gate.
    """

    def test_the_strict_xfails_are_still_strict(self):
        """#431's three `xfail(strict=True)` cases XPASS the moment the cut
        lands, and strict turns that into a failure — `ccd0452` says so in its
        own words. **Turns red when** #431's owner deletes them, which is the
        first of the two edits §5.2 asks for."""
        source = _read(L2_SOURCE_CONTRACT)
        self.assertEqual(source.count("xfail(strict=True, reason=BLOCKED)"), 3,
                         "#431's strict markers moved; re-read the node "
                         "dependency gate in §5.2")

    def test_the_fleet_allowance_is_still_declared(self):
        """#431 builds its node artifact with `ALLOW = ("fleet",)`.

        After the cut the closure no longer reaches `fleet`, and the builder
        refuses with `allow_external_in_repo names ['fleet'], which the l2-node
        closure does not reach` — 43 of the 47–50 failures the extraction
        produced. **Turns red when** #431's owner trims the allowance, i.e. when
        the second edit §5.2 asks for has happened."""
        self.assertIn('ALLOW = ("fleet",)', _read(L2_SEPARATION),
                      "#431's allowance moved; its 47–50 reds are no longer "
                      "explained by a stale permit — re-measure §5.2")

    def test_the_run_not_found_row_is_still_pessimistic(self):
        """#458 asserts the device's `retryable`/`details.reason` do not survive,
        and its own message asks its author to update the row when they do:
        *"update the `run.not_found` row instead of leaving it pessimistic"*.

        **Turns red when** #458's owner takes that advice — which is the second
        half of the receipt gate in §5.4."""
        source = _read(L2_CONTROL_MATRIX)
        self.assertIn("assert caught.value.extra == {}", source,
                      "#458's run.not_found expectation changed; the receipt "
                      "stack's second conflict is resolved — re-read §5.4")
        self.assertIn('{"runId", "session", "wait"}', source,
                      "#458's run_get argument set changed; re-read the "
                      "deliveryId disagreement in §5.4")


if __name__ == "__main__":
    unittest.main()
