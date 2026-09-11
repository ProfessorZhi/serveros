#!/usr/bin/env python3
"""Build the derived relations index from canonical objects.

    python3 project.py --records ROOT --write relations/current.jsonl
    python3 project.py --records ROOT --verify relations/current.jsonl

The relations sidecar answers the questions a strict object schema cannot: an
object belongs to a project *and* an Item, a report discusses an Item *and* is
published as a deliverable. Those are many-to-many, and the federation object
schema has `additionalProperties: false`, so they have nowhere to live in the
object itself.

That makes the sidecar useful and dangerous in equal measure, and the danger is
the whole reason this module exists in this shape.

The rule
--------
    The sidecar is an INDEX, not a source of truth. It can be deleted and
    rebuilt at any time. It must never be the only place a relation is stored.

Three properties enforce that, rather than merely recommending it:

  1. **Every row names its own derivation.** `derived_from` lists the canonical
     objects the row was computed from. A row whose source no longer exists is
     therefore detectable as a row that should not exist — see `lint()`.

  2. **The file is reproducible.** `--verify` recomputes from the canonical
     objects and compares. If a hand-edited row survives into the file, verify
     fails and says which row.

  3. **Nothing is invented.** A relation this module cannot derive is simply not
     written. Absent is not the same as guessed: six of the eight predicates
     have no producer yet, and that is recorded in `relations/schema.yaml`
     rather than filled in with plausible-looking rows.

No database, no network, no clock: the projection is a pure function of the
records it reads, so two runs over the same inputs produce identical bytes.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

SCHEMA = "serveros/relations.v1"
INDEX_SCHEMA = "serveros/relations-index.v1"

# The closed predicate set. Kept in step with relations/relations.schema.json;
# `tests/test_relations.py` asserts the two agree.
PREDICATES = (
    "belongs_to",
    "derived_from",
    "supports",
    "discussed_in",
    "attached_to",
    "published_as",
    "verified_by",
    "canonical_for",
)

# Predicates with a real producer today. Anything outside this set is declared
# but unpopulated — deliberately, and visibly, rather than silently empty.
# `relations/schema.yaml` records the same split for a human reader.
PRODUCED = ("belongs_to", "derived_from")


class InvalidRow(ValueError):
    """A row that must never reach the index file."""


def _require(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRow(f"{label} must be a non-empty string")
    return value.strip()


def row(frm: str, rel: str, to: str, derived_from) -> dict:
    """Build and validate one index row."""
    if rel not in PREDICATES:
        raise InvalidRow(f"unknown relation {rel!r}; unlisted relations are not expressible here")
    if isinstance(derived_from, str):
        derived_from = [derived_from]
    if not derived_from:
        raise InvalidRow(
            f"{frm} {rel} {to}: no `derived_from` — a relation that exists nowhere "
            "else would make this index the source of truth, which it must not be"
        )
    return {
        "from": _require(frm, "from"),
        "rel": rel,
        "to": _require(to, "to"),
        "derived_from": sorted({_require(item, "derived_from item") for item in derived_from}),
    }


def normalise(rows) -> list[dict]:
    """Sort and de-duplicate, rejecting two rows that disagree about one edge.

    Determinism is not cosmetic: `--verify` compares bytes, so an unstable order
    would report drift that is not there. Sorting also makes the file diffable,
    which is what lets a human review a relation change.
    """
    merged: dict[tuple[str, str, str], set[str]] = {}
    for entry in rows:
        key = (entry.get("from"), entry.get("rel"), entry.get("to"))
        if key in merged:
            merged[key] |= set(entry.get("derived_from") or ())
        else:
            merged[key] = set(entry.get("derived_from") or ())
    out = []
    for (frm, rel, to), sources in merged.items():
        if rel not in PREDICATES:
            raise InvalidRow(f"unknown relation {rel!r}")
        if not sources:
            raise InvalidRow(f"{frm} {rel} {to}: no derivation")
        out.append({"from": frm, "rel": rel, "to": to, "derived_from": sorted(sources)})
    return sorted(out, key=lambda r: (r["from"], r["rel"], r["to"]))


# --------------------------------------------------------------------------
# Readers. Each yields rows derived from canonical objects that ALREADY carry
# the relation; none of them decides a relation on its own authority.
# --------------------------------------------------------------------------

_SCHEME_RE = __import__("re").compile(r"^[a-z][a-z0-9+.-]*:")


def _source_locator(ref, card_dir: Path, records_root: Path) -> str | None:
    """Type a source reference. Returns None for a form we refuse to guess at.

    A reference this function does not recognise is skipped and counted, not
    coerced into something plausible. A wrong locator is worse than a missing
    row, because a missing row is visible and a wrong locator is not.
    """
    # A structured reference names its own type; take the locator it carries
    # rather than inventing a scheme for it.
    if isinstance(ref, dict):
        inner = ref.get("locator") or ref.get("path") or ref.get("url")
        if inner is None:
            return None
        typed = _source_locator(inner, card_dir, records_root)
        if typed is not None and not _SCHEME_RE.match(str(inner)):
            kind = ref.get("type")
            if isinstance(kind, str) and kind:
                return f"{kind}:{inner}"
        return typed

    text = str(ref).strip()
    if not text:
        return None
    # Already typed: conversation:, item:, feishu:, https:, file: ...
    if _SCHEME_RE.match(text):
        return text
    if text.startswith("/"):
        return f"file://{text}"

    # A bare reference is card-relative, which is how the cards write them
    # (`source-events.jsonl#L1`). Anchoring it to the card, not to the process
    # cwd, is what keeps the projection reproducible from any working directory.
    resolved = (card_dir / text.split("#", 1)[0]).resolve(strict=False)
    try:
        relative = resolved.relative_to(records_root)
    except ValueError:
        return None  # escapes the records root: refuse rather than guess
    fragment = text.split("#", 1)[1] if "#" in text else ""
    suffix = f"#{fragment}" if fragment else ""
    return f"records://{relative}{suffix}"


def project_card(card_path: Path, records_root: Path) -> list[dict]:
    """Rows derivable from one Item card.

    The card already states its project and its sources; this reads them rather
    than inferring them. Everything the card does not state — who discussed the
    Item, what it was published as — is not produced here.
    """
    import yaml  # local: the resolver owns no YAML dependency, but records are YAML

    payload = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise InvalidRow(f"{card_path}: not an object")
    item_id = _require(payload.get("id"), "card id")
    project = _require(payload.get("project"), "card project")
    locator = f"records://{card_path.relative_to(records_root)}"

    rows = [row(f"item:{item_id}", "belongs_to", f"project:{project}", locator)]
    for ref in payload.get("source_refs") or []:
        target = _source_locator(ref, card_path.parent, records_root)
        if target is None:
            continue
        rows.append(row(f"item:{item_id}", "derived_from", target, locator))
    return rows


def project(records_root: Path | str) -> tuple[list[dict], list[str]]:
    """Project every Item card under a records root.

    Returns (rows, skipped). `skipped` is returned rather than logged so that a
    caller can surface it: a silently dropped source reference is the failure
    mode this pass exists to remove.
    """
    root = Path(records_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"records root is not a directory: {root}")
    rows: list[dict] = []
    skipped: list[str] = []
    for card in sorted(root.glob("projects/*/items/*/card.yaml")):
        rows.extend(project_card(card, root))
        for ref in _unrecognised_refs(card, root):
            skipped.append(f"{card.relative_to(root)}: untyped source reference {ref!r}")
    return normalise(rows), skipped


def _unrecognised_refs(card: Path, records_root: Path) -> list[str]:
    """Source references this projection refused to type, for honest reporting.

    Returned rather than logged. A source reference silently dropped is exactly
    the failure mode this pass exists to remove, so it is surfaced at the point
    where somebody is looking at the result.
    """
    import yaml

    payload = yaml.safe_load(card.read_text(encoding="utf-8")) or {}
    return [
        repr(ref) for ref in (payload.get("source_refs") or [])
        if _source_locator(ref, card.parent, records_root) is None
    ]


# --------------------------------------------------------------------------
# Index file I/O
# --------------------------------------------------------------------------

def dumps(rows: list[dict]) -> str:
    """Serialise deterministically: sorted keys, one object per line."""
    return "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(", ", ": ")) + "\n"
        for entry in normalise(rows)
    )


def write(rows: list[dict], path: Path | str) -> Path:
    """Write the index atomically, so a reader never sees a half-built projection."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(rows)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".relations-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        os.path.exists(temporary) and os.unlink(temporary)
        raise
    return target


def load(path: Path | str) -> list[dict]:
    """Read an index file, validating every row as it is read."""
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InvalidRow(f"{path}:{number}: invalid JSON") from exc
        if not isinstance(entry, dict):
            raise InvalidRow(f"{path}:{number}: row must be an object")
        unknown = set(entry) - {"from", "rel", "to", "derived_from"}
        if unknown:
            raise InvalidRow(f"{path}:{number}: unexpected fields {sorted(unknown)}")
        rows.append(row(entry.get("from"), entry.get("rel"), entry.get("to"),
                        entry.get("derived_from")))
    return normalise(rows)


def verify(records_root: Path | str, path: Path | str) -> list[str]:
    """Return the reasons the index is NOT a faithful projection of the records.

    An empty list is the only acceptable answer for a derived index. A hand-added
    row, a stale row, or a row that lost its derivation all show up here.
    """
    expected, _ = project(records_root)
    try:
        actual = load(path)
    except (OSError, InvalidRow) as exc:
        return [str(exc)]
    reasons = []
    expected_keys = {(r["from"], r["rel"], r["to"]): r for r in expected}
    actual_keys = {(r["from"], r["rel"], r["to"]): r for r in actual}
    for key in sorted(set(actual_keys) - set(expected_keys)):
        reasons.append(f"index holds a relation no record derives: {key[0]} {key[1]} {key[2]}")
    for key in sorted(set(expected_keys) - set(actual_keys)):
        reasons.append(f"index is missing a relation the records derive: {key[0]} {key[1]} {key[2]}")
    for key in sorted(set(expected_keys) & set(actual_keys)):
        if expected_keys[key] != actual_keys[key]:
            reasons.append(f"derivation differs for {key[0]} {key[1]} {key[2]}")
    return reasons


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Project or verify the relations index")
    ap.add_argument("--records", required=True, type=Path, help="records root")
    ap.add_argument("--write", type=Path, help="write the index here")
    ap.add_argument("--verify", type=Path, help="verify this index against the records")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.verify:
        reasons = verify(args.records, args.verify)
        print(json.dumps({"schema": INDEX_SCHEMA, "ok": not reasons, "reasons": reasons},
                         ensure_ascii=False, indent=2))
        return 0 if not reasons else 2
    rows, skipped = project(args.records)
    if args.write:
        write(rows, args.write)
    print(json.dumps({
        "schema": INDEX_SCHEMA,
        "rows": len(rows),
        "predicates": sorted({r["rel"] for r in rows}),
        "skipped": skipped,
        "locator": str(args.write) if args.write else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
