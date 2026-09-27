#!/usr/bin/env python3
"""Decide whether a memory provider meets its contract, and whether it is running.

    python3 provider_status.py --candidate provider:openviking \
        --observations observations.json [--deployed]
    python3 provider_status.py --candidate provider:openviking   # decides nothing

`memory/provider-contract.yaml` declares the adoption conditions and a
`status_rule` that says the status "is decided by the conditions above and by
one observation outside them — whether the provider is running". Nothing
decided it. The rule was a sentence, and a sentence cannot fail.

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

Why AVAILABLE needs its own input and is not a condition
-------------------------------------------------------
`AVAILABLE` and `READY_TO_PROMOTE` differ in exactly one fact — whether the
provider is running — and that fact is not a property of the contract, so
turning it into a thirteenth condition would be a category error: the
conditions describe what the target must be, and this describes whether the
target is there. It is therefore its own argument, absent by default, and
absent means `READY_TO_PROMOTE`: the weaker of two true claims.

Why NEEDS_HUMAN is the default and not a failure
------------------------------------------------
The contract says so: "a contract with no deployment is worth more than a
deployment with no contract". A provider with no observations is in exactly the
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
#
# The first four are the ones version 1 of the contract would not have let
# move: the daemon boundary (dependencies, the tick loop) and the authority
# relationship (not a second catalogue, current records outrank hits). They are
# first because a regression in any of them is a different order of problem
# from a deployment that is merely incomplete.
CONDITIONS = (
    ("no_dependency_in_the_runtime_artifact",
     "the provider's dependencies live in its own environment, not in the node runtime artifact",
     "the provider's dependencies would enter the node runtime's artifact"),
    ("no_network_call_in_the_tick_loop",
     "no network call inside serverosd's tick loop",
     "the provider would put a network call on the daemon's tick path"),
    ("not_the_ability_catalogue",
     "the provider is not an Ability catalogue; ability-management remains the authority for abilities",
     "the provider would become a second catalogue of abilities"),
    ("current_records_outrank_retrieved_memory",
     "current Source, Claim, Requirement and Grant records override anything retrieved from the provider",
     "retrieved memory would outrank the current record, so a hit would be authority"),
    ("provider_is_independently_supervised",
     "the provider is supervised on its own, and serverosd neither starts nor stops it",
     "adopting it would leave the provider unsupervised, or make its liveness a function of serverosd"),
    ("provider_down_does_not_block_the_node",
     "a provider that is down does not block current records, checkpoints or the node's own answers",
     "a provider that is down would block the node's current records or checkpoints"),
    ("private_authenticated_endpoint",
     "the endpoint is private and authenticated, and is not reachable from the public internet",
     "the endpoint would be reachable without a credential, or from outside this node"),
    ("credential_outside_the_repository",
     "the provider's credential is read from the node's vault and appears in no Git tree",
     "the credential would exist inside a Git tree"),
    ("store_outside_the_repository",
     "the store is persistent and lives outside the repository",
     "the store would live in the repository, or would not survive a restart"),
    ("backup_and_restore_exercised",
     "backup and restore exist and have been exercised, not merely described",
     "the store would have a restore path that has never been run"),
    ("no_automatic_capture_of_sensitive_material",
     "secrets, credentials, production-sensitive and raw-private material are not captured automatically",
     "material whose scope nobody has decided would be captured by default"),
    ("scope_has_a_declared_acl",
     "project and global memory scopes carry an explicit ACL",
     "project and global scopes would be readable by whoever asks"),
)


def evaluate(observations: dict | None, *, deployed: bool | None = None) -> dict:
    """One status, from observations, by the contract's rule.

    `observations` is a mapping of the condition keys above to booleans:
    `True` means the condition HOLDS (adoption is not blocked by it). A key
    that is absent is not `False` — it is unobserved, and an unobserved
    condition cannot be satisfied by assumption. That distinction is the whole
    reason this returns NEEDS_HUMAN rather than READY_TO_PROMOTE when handed
    nothing.

    `deployed` is the one fact outside the conditions. `None` means nobody
    said, and nobody saying is not the same as it running: the weaker claim is
    returned.
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
                        "`READY_TO_PROMOTE` claims that every condition holds; "
                        "that is a claim about evidence, and there is none here"),
        }

    if deployed:
        return {
            "schema": SCHEMA,
            "status": "AVAILABLE",
            "decided_by": "every declared condition observed to hold, and the provider is running",
            "conditions": results,
            "unobserved": [],
            "because": ("the target is met and it is there. This says nothing "
                        "about authority: `authority: false` in the contract "
                        "still decides that a hit is not a fact"),
        }

    return {
        "schema": SCHEMA,
        "status": "READY_TO_PROMOTE",
        "decided_by": "every declared condition observed to hold, and the provider is not running",
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
    parser.add_argument("--deployed", action="store_true",
                        help="the provider is running on this node")
    args = parser.parse_args(argv)

    observations = (json.loads(Path(args.observations).read_text(encoding="utf-8"))
                    if args.observations else None)
    result = {"candidate": args.candidate,
              **evaluate(observations, deployed=True if args.deployed else None)}
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
