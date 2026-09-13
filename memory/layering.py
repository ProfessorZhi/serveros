#!/usr/bin/env python3
"""The caller side of the Layer C boundary — and nothing else.

    from layering import usable_hits, derivable

`memory/provider-contract.yaml` states two rules about a retrieval hit, and
they sit on opposite sides of the same line:

    consequence:  "Every returned record carries its Layer B id and revision.
                   A result whose record cannot be cited is not a result."
    rules:        "A hit without a Layer B id is discarded by the caller, not
                   by the provider. The provider is the wrong place to enforce
                   the product's rule."

Both are good rules and neither had an implementation. The provider half is
defensible without one — a provider is third-party code and the contract is a
statement of what a conformant one does. The caller half is not: the contract
names the caller as the enforcement point and there was no caller. That is the
same shape as `abilities/resolver.py` before §53 and the disclosure kernel
before §55 — a rule assigned to whoever gets there first, and nobody does.

There is also a fixture whose own docstring claimed the opposite, that the
provider does the dropping. It never did — the fixture cannot produce an
unciteable hit, because `index` refuses a record with no id — so the sentence
described behaviour that did not exist and contradicted the contract it cited.
Corrected there; enforced here.

Why the filter is not in the provider
-------------------------------------
Because "citeable" is a property of the LEDGER, and the provider has never seen
the ledger. A provider can guarantee it echoes the id it was given; only
something holding Layer B can say whether that id is still there. This module
is that something, and `derivable` is the whole check the fabric's founding
rule needs: **if a fact exists only in Layer C, it is lost.**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/memory-layering.v1"

# Why a hit was dropped. Named rather than counted, because the two call for
# different repairs: one is a provider that ignored the contract, the other is
# a stale index that needs rebuilding.
NO_RECORD_ID = "NO_RECORD_ID"
NOT_IN_LEDGER = "NOT_IN_LEDGER"

REASONS = {
    NO_RECORD_ID: ("the hit carries no Layer B id. The contract says a provider "
                   "must return citeable ids; this one did not"),
    NOT_IN_LEDGER: ("the hit names a record the ledger does not have. Either "
                    "the index is ahead of the ledger — a rebuild — or the "
                    "provider invented a record, which is the one thing the "
                    "authority rule forbids outright"),
}


def usable_hits(hits: list[dict], ledger_ids) -> dict:
    """Split hits into what may be used and what may not, with reasons.

    `ledger_ids` is the set of `mem:<id>` the ledger holds. Passed in rather
    than read: this module must give the same answer on a device holding the
    whole ledger and on one holding a projection, and a function that read the
    ledger itself would answer "nothing is citeable" on the second.
    """
    known = set(ledger_ids or ())
    usable, discarded = [], []
    for hit in hits or []:
        record_id = (hit or {}).get("record_id")
        if not record_id:
            discarded.append({"hit": hit, "reason": NO_RECORD_ID,
                              "why": REASONS[NO_RECORD_ID]})
        elif record_id not in known:
            discarded.append({"hit": hit, "reason": NOT_IN_LEDGER,
                              "why": REASONS[NOT_IN_LEDGER],
                              "detail": f"{record_id} is not in the ledger"})
        else:
            usable.append(hit)
    return {"schema": SCHEMA, "usable": usable, "discarded": discarded,
            "considered": len(hits or ())}


def derivable(hits: list[dict], ledger_ids) -> dict:
    """The founding rule as a verdict: is this index rebuildable from Layer B?

    A single unciteable hit is enough to answer no, and the answer is not
    "mostly" — the rule is that a fact existing only in the index is lost, and
    one such hit is one such fact.
    """
    result = usable_hits(hits, ledger_ids)
    result["verdict"] = "DERIVABLE" if not result["discarded"] else "NOT_DERIVABLE"
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hits", required=True, type=Path,
                        help="JSON list of hits")
    parser.add_argument("--ledger", nargs="*", type=Path, default=[],
                        help="ledger record files; their ids are what hits must cite")
    args = parser.parse_args(argv)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "serveros_memory_record", Path(__file__).resolve().parent / "record.py")
    record_mod = importlib.util.module_from_spec(spec)
    sys.modules["serveros_memory_record"] = record_mod
    spec.loader.exec_module(record_mod)

    records = record_mod.load(args.ledger) if args.ledger else []
    hits = json.loads(args.hits.read_text(encoding="utf-8"))
    result = derivable(hits, [r.get("id") for r in records])
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result["verdict"] == "DERIVABLE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
