"""Turn the pins file into the retire map, by measuring a real repository.

    python w19_measure.py --repo <path> --pin release_pin_w19.json [--out FILE]

Nothing here knows a pull request number. The pins file says which refs to look
at and which paths each one owns; this reads the refs and prints what it found.
That separation is the point: the numbers in the evidence file can be re-derived
by anyone who has the same commit, and a reopened question becomes one command
instead of one more conversation.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))

import w19_gate  # noqa: E402  (a sibling module, not an installed package)


class PinError(ValueError):
    """The pins file says something this tool will not guess at."""


def _require(value, what: str):
    if not value:
        raise PinError(f"pin: {what} is missing")
    return value


def measure(repo, pin: Mapping) -> dict:
    """Measure every PR in `pin` against `pin['main']`. Pure git, no network."""
    main = _require(pin.get("main"), "'main'")
    prs = pin.get("prs")
    if not prs:
        raise PinError("pin: 'prs' is empty; a retire map with no rows is not one")

    measured = []
    for entry in prs:
        number = _require(entry.get("number"), "a pr number")
        ref = _require(entry.get("ref"), f"#{number} ref")
        try:
            comparison = w19_gate.compare(repo, main=main, head=ref)
        except RuntimeError as exc:
            raise RuntimeError(f"#{number} ({ref}): {exc}") from exc
        head = w19_gate._git(repo, "rev-parse", "--verify",
                             f"{ref}^{{commit}}").strip()
        measured.append((entry, comparison, head, number))

    # Supersession is measured, never declared: a PR is superseded when another
    # PR's head literally contains its head. Lowest number wins, so the answer
    # does not depend on the order rows happen to appear in the pins file.
    rows = []
    for entry, comparison, head, number in measured:
        superseded_by = 0
        if not comparison.in_main:
            carriers = sorted(
                other_number for _e, other_comparison, other_head, other_number
                in measured
                if other_head != head and not other_comparison.in_main
                and w19_gate.is_ancestor(repo, head, other_head))
            superseded_by = carriers[0] if carriers else 0
        facts = w19_gate.PrFacts.from_comparison(
            comparison, number=number,
            own_prefixes=tuple(entry.get("own_prefixes") or ()),
            released=bool(entry.get("released", False)),
            superseded_by=superseded_by)
        row = {"number": number, "ref": entry["ref"], "head": head,
               "classification": facts.classify()}
        row.update(comparison.as_dict())
        row["successor"] = facts.successor()
        row["note"] = entry.get("note", "")
        rows.append(row)

    counts = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    return {"main": w19_gate._git(repo, "rev-parse", "--verify",
                                  f"{main}^{{commit}}").strip(),
            "main_ref": main,
            "prs": rows,
            "counts": dict(sorted(counts.items()))}


def load_pin(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pin", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)

    try:
        result = measure(Path(args.repo), load_pin(args.pin))
    except PinError as exc:
        print(f"pin refused: {exc}", file=sys.stderr)
        return 2

    body = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body)
    for row in result["prs"]:
        print(f"#{row['number']:<4} {row['classification']:<20} "
              f"absent={len(row['absent']):<3} differing={len(row['differing'])}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
