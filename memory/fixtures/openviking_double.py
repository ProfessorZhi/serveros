#!/usr/bin/env python3
"""A deterministic in-process double for the OpenViking memory provider.

    python3 openviking_double.py --selftest

`memory/provider-contract.yaml` names this file — "a deterministic in-process
double; see `memory/fixtures/`" — and `memory/fixtures/` did not exist. The
declaration is what makes the fixture part of the deliverable rather than a
test convenience: the contract's exit condition for an undeployed provider is
"the contract, the adapter and the fixture all exist and pass", and two of the
three were present.

What it is for
--------------
Not to stand in for OpenViking's relevance quality. A double that scored well
would be a claim about a product nobody has run. It is for the four properties
the contract states as rules, each of which is a way a retrieval index
acquires authority it should not have:

  * `index` is idempotent, and re-indexing a record at a new revision replaces
    the old one rather than accumulating both. An index that keeps the stale
    revision serves a superseded fact with a citation that resolves.
  * every hit carries its Layer B id AND the revision it was indexed from. A
    hit whose record cannot be cited is dropped by the provider, because the
    contract puts that rule on the provider's side of the line — a caller that
    has to remember to check will eventually not.
  * `invalidate` is correct for a record the index never held. Called with an
    unknown id it reports zero removed and does not error, because rebuild
    paths call it speculatively.
  * `health` reaches nothing. It answers from local state, because the daemon
    may ask it and the daemon's tick may not touch the network.

It is deterministic in the only sense that matters here: the same calls in the
same order produce the same results, with no clock, no randomness and no
scoring model. `search` matches on substrings of the indexed text and orders by
record id, so a test can assert an exact list.
"""
from __future__ import annotations

import argparse
import json
import sys


class OpenVikingDouble:
    """The four operations, and nothing else. No network, no clock, no state
    that outlives the object."""

    # Named so that anything reading a status knows which of the two it has.
    kind = "fixture"
    provider_id = "provider:openviking"

    def __init__(self):
        self._index: dict[str, dict] = {}

    # -- index --------------------------------------------------------------
    def index(self, records) -> dict:
        """Idempotent, and by `(id)` rather than by `(id, revision)`.

        Keeping both revisions would make the index a place where a superseded
        fact survives, which is the one thing an index may not be. Indexing
        the same record twice at the same revision is a no-op and counts as
        skipped rather than indexed, so a rebuild loop and an incremental
        update are distinguishable in the result.
        """
        indexed = skipped = 0
        for record in records:
            record_id = record.get("id")
            if not record_id:
                raise ValueError("a memory record without an id cannot be indexed")
            revision = record.get("revision")
            if revision is None:
                raise ValueError(
                    f"{record_id} has no revision; a hit with no revision "
                    "cannot be cited, and the contract requires every hit to "
                    "carry the revision it was indexed from")
            existing = self._index.get(record_id)
            if existing is not None and existing["revision"] == revision:
                skipped += 1
                continue
            self._index[record_id] = {
                "id": record_id,
                "revision": revision,
                "text": str(record.get("text") or record.get("summary") or ""),
            }
            indexed += 1
        return {"indexed": indexed, "skipped": skipped}

    # -- search -------------------------------------------------------------
    def search(self, query: str, *, limit: int = 10, filters: dict | None = None):
        """Substring match, ordered by record id. Deliberately not clever.

        A relevance model here would be a claim about ranking quality that this
        double has no basis for, and the contract says scores are comparable
        within one provider and nowhere else — so the fixture uses one score,
        and a caller that sorts by it learns nothing from the sort.
        """
        needle = (query or "").lower()
        hits = []
        for entry in self._index.values():
            for key, expected in (filters or {}).items():
                if key == "revision" and entry["revision"] != expected:
                    break
            else:
                if needle and needle not in entry["text"].lower():
                    continue
                hits.append({
                    "record_id": entry["id"],
                    "revision": entry["revision"],
                    "score": 1.0,
                    "excerpt": entry["text"][:80] or None,
                })
        return sorted(hits, key=lambda h: h["record_id"])[:limit]

    # -- invalidate ---------------------------------------------------------
    def invalidate(self, record_ids) -> dict:
        """Correct when called for a record the index never held.

        Rebuild and reconcile paths call this speculatively, with ids that may
        have been removed from the ledger already. An implementation that
        raised on an unknown id would make `invalidate` unusable as the first
        step of a rebuild, which is the operation the contract calls
        supported.
        """
        removed = 0
        for record_id in record_ids:
            if self._index.pop(record_id, None) is not None:
                removed += 1
        return {"removed": removed}

    # -- health -------------------------------------------------------------
    def health(self) -> dict:
        """Answers from local state and reaches nothing.

        The daemon may ask this and the daemon's tick may not touch the
        network. A health check that probed a remote service would put that
        service's availability on the path of a node whose design premise is
        that it answers without one.
        """
        return {"status": "AVAILABLE",
                "detail": f"fixture; {len(self._index)} records held locally"}

    # -- test affordance ----------------------------------------------------
    def held(self) -> list[tuple[str, str]]:
        """What the index holds, as `(id, revision)`. For assertions about
        idempotence and supersession, which are otherwise observable only
        through `search` and therefore only through its matching rules."""
        return sorted((e["id"], e["revision"]) for e in self._index.values())


def _selftest() -> int:
    """The four properties, run as a command so the fixture can be checked
    without a test runner — it is a deliverable named by a contract, not only
    a test helper."""
    double = OpenVikingDouble()
    records = [{"id": "mem:one", "revision": "r1", "text": "the tunnel is task scoped"},
               {"id": "mem:two", "revision": "r1", "text": "unrelated content"}]

    first = double.index(records)
    assert first == {"indexed": 2, "skipped": 0}, first
    again = double.index(records)
    assert again == {"indexed": 0, "skipped": 2}, again

    moved = double.index([{"id": "mem:one", "revision": "r2", "text": "the tunnel is task scoped"}])
    assert moved == {"indexed": 1, "skipped": 0}, moved
    assert ("mem:one", "r2") in double.held() and ("mem:one", "r1") not in double.held()

    hits = double.search("tunnel")
    assert [h["record_id"] for h in hits] == ["mem:one"], hits
    assert hits[0]["revision"] == "r2", hits

    assert double.invalidate(["mem:never-held", "mem:two"]) == {"removed": 1}
    assert double.invalidate(["mem:never-held"]) == {"removed": 0}

    health = double.health()
    assert health["status"] == "AVAILABLE", health

    for bad in ([{"revision": "r1", "text": "x"}], [{"id": "mem:three", "text": "x"}]):
        try:
            double.index(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"index accepted {bad}")

    print("openviking double: 4 contract properties hold")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    parser.error("--selftest is the only mode; this is a fixture")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
