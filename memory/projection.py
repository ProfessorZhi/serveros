#!/usr/bin/env python3
"""Layer A — the local projection of the Layer B ledger.

    from projection import build, manifest_for, staleness
    python3 projection.py --ledger <files...> --revision <sha> --out <dir>
    python3 projection.py --status <dir> --revision <sha>

`memory/record-contract.yaml` declares `projection.location`,
`derived_from: ledger`, `regenerable: true`, `hand_edit: forbidden`, and —
the line this file exists for —

    a projection that cannot name its revision is reported STALE.

Nothing built a projection and nothing reported anything stale. Meanwhile
`fleet/state-root-contract.yaml` declared `state/serveros/projections/` to be
the ONLY projections namespace, gave it a mandatory manifest with six required
fields and five invariants, and `runtime/state_root.py` implemented every check
of a projection that is already there. There were no projections. The namespace
was a complete read side with no write side, which is the same defect this
round keeps finding with the halves swapped.

Two things follow, and the second one is why the location changed:

  * This file is the missing producer. It is the first thing in this product
    that writes a `projection.yaml`, so the five invariants stop being a
    description of an empty directory.

  * `record-contract.yaml` said `state/serveros/memory/`, and the state root
    contract says a second projections namespace is exactly the thing it
    forbids ("a second top-level root for derived material would itself become
    a durable-looking location, and the whole point is that this namespace's
    contents are disposable"). Two declarations, one fact. The state root
    contract is the authority for `state/serveros/`, so the memory projection
    lives at `state/serveros/projections/memory/` and the record contract now
    says so.

What the projection carries
---------------------------
Pointers, not content. Every record is represented by the fields needed to
CITE it — `id`, `kind`, `at`, `by`, `source`, `revision` — and no `body`. That
is the shape the invariant `NO_SOURCE_AUTHORITY` implies: nothing reads a
projection to decide what is true, so a projection that carried the text would
be a second copy of the ledger sitting in the one namespace declared to have no
authority, which is the worst available place for it.

The consequence, stated because it is a limitation and not an oversight: a node
offline with its projection can say *that* a decision exists, who made it and
what revision it was about. It cannot say what the decision was. Reading the
decision needs the ledger, and the ledger is Git, which is the substrate the
contract chose precisely so that reading it is not a network round trip to a
ServerOS service.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _record_module():
    """`memory/record.py`, loaded by path.

    Not a plain `import record`: these modules are loaded by path everywhere
    else in this product (see `runtime/cli.py`, `runtime/state_root.py`), the
    directory is not a package, and a bare import would work only when the
    caller happened to have this directory on `sys.path` — which is to say it
    would work from the CLI and fail from a test, or the other way round.
    """
    name = "serveros_memory_record"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "record.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _scope_module():
    """`memory/scope.py`, loaded by path. Same idiom, same reason.

    Loaded through the SAME synthetic name `record.py` uses
    (`serveros_memory_scope`), so a projection and the ledger agree about what
    an operator reading is; two module objects would mean two
    `RecallRefused` classes, and a `raise`/`except` pair that silently stops
    matching is a firewall that stops refusing without failing anything.
    """
    name = "serveros_memory_scope"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "scope.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCHEMA = "serveros/memory-projection.v1"
LEDGER_SCHEMA = "serveros/memory-record.v1"

# The manifest the state root contract requires, in its order. `authority`,
# `generated` and `reproducible` are constants here and not parameters: a
# projection that could be built with `authority: true` would make the
# namespace's one invariant a caller's choice.
MANIFEST_NAME = "projection.yaml"
MANIFEST_FIELDS = ("authority", "generated", "generator", "generated_at",
                   "source_refs", "reproducible")

# What a projection may say about a record. Everything needed to cite, and
# nothing needed to believe.
CITED_FIELDS = ("id", "kind", "at", "by", "source", "revision")


class ProjectionRefused(Exception):
    """The projection could not be built or could not be read."""


def _cited(record: dict) -> dict:
    return {field: record.get(field) for field in CITED_FIELDS}


def build(records: list[dict], *, ledger_ref: str, ledger_revision: str,
          generated_at: str, generator: str) -> dict:
    """The projection document. A pure function of its arguments.

    `generated_at` is an argument and not `now()` for the same reason
    `Daemon.SETTLE_SECONDS` is injectable: a value a test cannot fix is a value
    tests cannot assert, and the property this document has to have is that
    rebuilding it from the same ledger at the same revision produces the same
    bytes.
    """
    if not ledger_revision:
        raise ProjectionRefused(
            "a projection must name the ledger revision it was built from; a "
            "projection that cannot name its revision is reported STALE, and "
            "building one that will be stale the moment it exists is worse "
            "than building none")

    record_mod = _record_module()
    # The projection is a catalogue of the whole ledger, and a catalogue is
    # read by whoever holds the ledger — which is what `OPERATOR_READING`
    # names. It is passed rather than defaulted so that §18's firewall has a
    # reader for every read `heads()`/`current_value()` ever does, and so that
    # a future caller cannot inherit an admission it did not ask for. A
    # projection that silently narrowed itself to what some thread was allowed
    # to see would be a catalogue with a hole in it that reports no hole.
    operator = _scope_module().OPERATOR_READING
    by_subject: dict[str, list[dict]] = {}
    for one in records:
        by_subject.setdefault(str(one.get("subject")), []).append(one)

    subjects = {}
    for subject, group in sorted(by_subject.items()):
        current = record_mod.current_value(records, subject, reader=operator)
        kinds: dict[str, int] = {}
        for one in group:
            kinds[str(one.get("kind"))] = kinds.get(str(one.get("kind")), 0) + 1
        subjects[subject] = {
            "records": len(group),
            "kinds": dict(sorted(kinds.items())),
            "current": _cited(current) if current else None,
            # How many records currently stand as answers for this subject.
            # One means the chain has a single head; more means several records
            # stand unchallenged, which for a live work item is ordinary — the
            # first real ledger put eight under one subject — and which is NOT
            # a conflict report. See `record.heads`: nothing in a record says
            # which claim it is about, so no field can tell two answers to one
            # question from two answers to two.
            #
            # It is here because `current` picks the newest of them, and a
            # choice made on the reader's behalf has to be visible. A subject
            # with one head and a subject with eight render identically in
            # `current` alone.
            "heads": len(record_mod.heads(records, subject, reader=operator)),
            # Listed rather than counted. A question is the one kind whose
            # whole purpose is to be picked up by somebody, and a count of open
            # questions that does not say which ones is a count nobody can act
            # on. These are ids, so the entry stays a pointer.
            "open_questions": sorted(
                r["id"] for r in group
                if r.get("kind") == "question"
                and not any(o.get("supersedes") == r["id"] for o in records)),
        }

    return {
        "schema": SCHEMA,
        # The same denial the manifest carries, in the document as well. A
        # reader that opens only this file must not have to also find the
        # manifest to learn that it is holding derived material.
        "authority": False,
        "generated": True,
        "ledger": {"ref": ledger_ref, "revision": ledger_revision,
                   "schema": LEDGER_SCHEMA},
        "generated_at": generated_at,
        "generator": generator,
        "records": len(records),
        "subjects": subjects,
    }


def manifest_for(projection: dict, *, generated_at: str, generator: str) -> dict:
    """The `projection.yaml` that makes the directory a projection.

    Every field is derived from the document rather than passed again, so the
    two cannot disagree about what was built from what — which is the failure
    `source_refs` exists to make visible.
    """
    ledger = projection.get("ledger") or {}
    ref = f"{ledger.get('ref')}@{ledger.get('revision')}"
    return {
        "authority": False,
        "generated": True,
        "generator": generator,
        "generated_at": generated_at,
        "source_refs": [ref],
        "reproducible": True,
    }


def ledger_revision_of(manifest: dict) -> str | None:
    """The ledger revision a manifest names, or None if it names none.

    The first `source_ref` is the ledger by construction (`manifest_for` writes
    one), and the revision is everything after the last `@`. Splitting on the
    last one rather than the first because a ref path may legally contain an
    `@`, and a parser that split on the first would read half a path as a
    revision — which would still be a string, so the answer would look fine.
    """
    refs = (manifest or {}).get("source_refs") or []
    if not refs or not isinstance(refs[0], str) or "@" not in refs[0]:
        return None
    return refs[0].rsplit("@", 1)[1]


def staleness(manifest: dict | None, *, ledger_revision: str) -> dict:
    """`CURRENT`, `STALE` or `MISSING`, and never a fourth thing.

    `STALE` covers both "built from an older revision" and "cannot name a
    revision", because the contract collapses them: a projection that cannot
    name its revision is reported STALE. They are distinguished in `because`
    rather than in the status, because a caller that acts on this acts the same
    way either way — it regenerates.
    """
    if manifest is None:
        return {"status": "MISSING",
                "because": ("nothing is here. A node with no projection has "
                            "not lost memory; it has to read the ledger")}

    named = ledger_revision_of(manifest)
    if named is None:
        return {"status": "STALE", "ledger_revision": None,
                "because": ("the manifest names no ledger revision. It cannot "
                            "be shown to be current, and the contract reports "
                            "exactly this case as STALE rather than inventing "
                            "a third state for it")}
    if named != ledger_revision:
        # Both revisions are printed WHOLE. They were truncated to 12
        # characters, which is the length of a bare commit sha — and a
        # content-named revision is `sha+content:hex`, so the two names shared
        # their first twelve characters and the message read "built from
        # 2092223277eb, ledger is at 2092223277eb". A staleness report whose
        # whole purpose is to show that two things differ, printing them
        # identically, is the failure this module exists to detect, wearing the
        # report's own clothes.
        return {"status": "STALE", "ledger_revision": named,
                "because": (f"built from {named}, ledger is at "
                            f"{ledger_revision}. The repair is to "
                            "regenerate; editing the projection to match is "
                            "the reverse direction")}
    return {"status": "CURRENT", "ledger_revision": named,
            "because": "built from the ledger revision now on disk"}


def status(projection_dir: Path, *, ledger_revision: str) -> dict:
    """Read a projection directory and decide. Filesystem, but no network."""
    import yaml

    directory = Path(projection_dir)
    document_path = directory / "memory.json"
    manifest_path = directory / MANIFEST_NAME

    if not document_path.is_file() and not manifest_path.is_file():
        return {"directory": str(directory), **staleness(None, ledger_revision=ledger_revision)}

    manifest = None
    if manifest_path.is_file():
        try:
            loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            manifest = loaded if isinstance(loaded, dict) else None
        except Exception as exc:  # noqa: BLE001 - unparseable is a fact
            return {"directory": str(directory), "status": "MISSING",
                    "because": f"{MANIFEST_NAME} is not readable YAML: {exc}"}

    return {"directory": str(directory),
            **staleness(manifest, ledger_revision=ledger_revision)}


def write(projection: dict, manifest: dict, out: Path) -> list[Path]:
    """Write the pair. The only side effect in this file, kept in one place."""
    import yaml

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    document = out / "memory.json"
    document.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    manifest_path = out / MANIFEST_NAME
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False),
                             encoding="utf-8")
    return [document, manifest_path]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", nargs="*", type=Path, default=[])
    parser.add_argument("--ledger-ref", default="records/projects/serveros/memory")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--generated-at", required=True,
                        help="ISO-8601 UTC; an argument, never now()")
    parser.add_argument("--generator", default="serverosctl memory project")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--status", type=Path,
                        help="report on an existing projection instead")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.status:
        result = status(args.status, ledger_revision=args.revision)
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0 if result["status"] == "CURRENT" else 1

    records = _record_module().load(args.ledger) if args.ledger else []
    projection = build(records, ledger_ref=args.ledger_ref,
                       ledger_revision=args.revision,
                       generated_at=args.generated_at, generator=args.generator)
    manifest = manifest_for(projection, generated_at=args.generated_at,
                            generator=args.generator)
    if args.out:
        written = write(projection, manifest, args.out)
        json.dump({"written": [str(p) for p in written],
                   "records": projection["records"],
                   "subjects": len(projection["subjects"])},
                  sys.stdout, indent=2, sort_keys=True)
    else:
        json.dump(projection, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
