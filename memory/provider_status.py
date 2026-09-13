#!/usr/bin/env python3
"""Decide whether a memory provider is adopted, deferred, or neither yet.

    python3 provider_status.py --candidate provider:openviking \
        --observations observations.json
    python3 provider_status.py --candidate provider:openviking   # decides nothing

`memory/provider-contract.yaml` declares four adoption conditions and a
`status_rule` that says the status "is decided by the conditions above and by
nothing else". Nothing decided it. The rule was a sentence, and a sentence
cannot fail.

So this module is the thing that decides, and it is deliberately the smallest
thing that can: it takes observations about a machine and returns one of the
contract's own status values, naming the first condition that failed if one
did.

Why the observations are an argument
------------------------------------
A module that looked at the machine itself would make the answer a property of
whichever host ran it, and this decision is meant to be the same decision on
three devices. It is the same discipline as `abilities/resolver.py`: everything
that touches the machine is a probe, performed by the caller, passed in as a
record — and a caller that passes nothing gets `NEEDS_HUMAN`, not a guess.

Why NEEDS_HUMAN is the default and not a failure
------------------------------------------------
The contract says so: "a contract with no deployment is worth more than a
deployment with no contract", and §60 says OpenViking may not become the reason
a round does not finish. A provider with no observations is in exactly the
state the contract calls deferred, and the honest answer names what would have
to be observed to change it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/memory-provider-status.v1"

# The contract's own vocabulary. Copied deliberately and asserted against the
# file by test, because two lists of status values is the defect this round has
# now found in four different places.
STATUSES = ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "READY_TO_PROMOTE",
            "NEEDS_HUMAN")

# One key per declared adoption condition, in the contract's order. The order
# is the contract's because the blocker reported is the FIRST one to fail, and
# reordering these silently changes which blocker a reader is told about.
CONDITIONS = (
    ("no_large_dependency_set",
     "no large dependency set pulled into the runtime artifact",
     "the provider's dependencies would enter the node runtime's artifact"),
    ("no_service_not_already_running",
     "no service that this machine does not already run",
     "adopting it would require running a service this machine does not run"),
    ("no_network_in_the_tick_loop",
     "no network call inside serverosd's tick loop",
     "the provider would put a network call on the daemon's tick path"),
    ("index_rebuildable_from_layer_b",
     "the index is rebuildable from Layer B alone",
     "deleting the index would lose information, so it is not an index"),
)


def evaluate(observations: dict | None) -> dict:
    """One status, from observations, by the contract's rule.

    `observations` is a mapping of the condition keys above to booleans:
    `True` means the condition HOLDS (adoption is not blocked by it). A key
    that is absent is not `False` — it is unobserved, and an unobserved
    condition cannot be satisfied by assumption. That distinction is the whole
    reason this returns NEEDS_HUMAN rather than READY_TO_PROMOTE when handed
    nothing.
    """
    observations = observations or {}
    results = []
    first_failure = None

    for key, text, blocker in CONDITIONS:
        if key not in observations:
            state = "UNOBSERVED"
        elif observations[key]:
            state = "HOLDS"
        else:
            state = "FAILED"
            if first_failure is None:
                first_failure = (key, blocker)
        results.append({"condition": key, "text": text, "state": state})

    unobserved = [r["condition"] for r in results if r["state"] == "UNOBSERVED"]

    if first_failure is not None:
        key, blocker = first_failure
        return {
            "schema": SCHEMA,
            "status": "NEEDS_HUMAN",
            "decided_by": f"{key} — {blocker}",
            "conditions": results,
            "unobserved": unobserved,
            "because": ("the contract's status rule decides this from the "
                        "conditions and from nothing else"),
        }

    if unobserved:
        return {
            "schema": SCHEMA,
            "status": "NEEDS_HUMAN",
            "decided_by": "unobserved conditions: " + ", ".join(unobserved),
            "conditions": results,
            "unobserved": unobserved,
            "because": ("no condition failed and none was observed either. "
                        "`READY_TO_PROMOTE` claims the contract, the adapter "
                        "and the fixture all exist and pass; that is a claim "
                        "about evidence, and there is none here"),
        }

    return {
        "schema": SCHEMA,
        "status": "READY_TO_PROMOTE",
        "decided_by": "every declared condition observed to hold",
        "conditions": results,
        "unobserved": [],
        "because": ("running the provider for real is a deployment decision, "
                    "and this decides only whether it may be proposed"),
        # Stated in the record. READY_TO_PROMOTE is the outcome the contract
        # calls "a deployment decision that has not been taken", and a reader
        # who takes it as "the provider is running" has read a proposal as a
        # fact.
        "not_yet_running": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--observations",
                        help="JSON mapping condition keys to booleans")
    args = parser.parse_args(argv)

    observations = (json.loads(Path(args.observations).read_text(encoding="utf-8"))
                    if args.observations else None)
    result = {"candidate": args.candidate, **evaluate(observations)}
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
