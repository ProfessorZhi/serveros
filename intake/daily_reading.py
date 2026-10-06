"""Join the finite perception recipe to pinned readings and the existing writer."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(relative):
    name = "serveros_daily_" + relative.replace("/", "_").replace(".py", "")
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _write(path, value):
    if path.is_symlink():
        raise ValueError("symlink reading artifact refused")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".reading-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def alignment(directory, entries, records_root=None):
    """Regenerable pending view; COMPLETE writer receipts do not erase proposals.

    Every acceptance must still use the semantic writer's version checks. This
    view neither confirms a claim nor creates a Requirement or Work Item.
    """
    bound = {(entry["card"], entry["manifest"]) for entry in entries}
    pending = []
    for path in sorted((directory / "readings").glob("*/resolution.json")):
        reading_path = path.with_name("reading.json")
        reading = json.loads(reading_path.read_text())
        source = reading["source"]
        if (source.get("card"), source.get("manifest")) not in bound:
            continue
        resolution = json.loads(path.read_text())
        awaiting = resolution.get("awaiting", [])
        if records_root is not None:
            semantic = _load("intake/understanding_semantic.py")
            receipt_path = semantic._receipt_path(semantic.understanding_root(records_root), reading)
            receipts = [receipt_path, *receipt_path.parent.glob(f"{receipt_path.stem}-acceptance-*.json")]
            committed = set()
            for receipt_file in receipts:
                receipt = semantic._read_receipt(receipt_file)
                if receipt and receipt.get("reading_digest") == semantic.reading_digest(reading):
                    committed.update(claim_id for entry in receipt.get("written", [])
                                     if entry.get("role") == semantic.OWNER
                                     for claim_id in entry.get("claims", []))
            awaiting = [claim for claim in awaiting if claim["id"] not in committed]
        if awaiting:
            pending.append({"reading": str(reading_path), "source": source,
                            "read": reading["read"], "claims": awaiting,
                            "state": "AWAITING_DECISION_REVALIDATE_BEFORE_ACCEPT"})
    target = directory / "alignment.json"
    _write(target, {"schema": "serveros/reading-alignment.v1", "pending": pending})
    return {"path": str(target), "pending_readings": len(pending)}


def scoped_report(records_root, entry):
    """Measure exactly the bound card/source pair through existing owner readers.

    A finite pass must not scan every chat and card once per selected source.
    Each write remeasures this pair, so scope reduction does not cache a stale
    source or weaken the semantic writer's version check.
    """
    capture = _load("intake/capture_coverage.py")
    delta = _load("intake/understanding_delta.py")
    root = Path(records_root).resolve()
    raw = capture.raw_root(root)
    base = delta.understanding_root(root)
    paths = ((base, root / entry["card"], entry["card"]),
             (raw, raw / entry["manifest"], entry["manifest"]))
    for boundary, path, locator in paths:
        if (Path(locator).is_absolute() or ".." in Path(locator).parts
                or path != path.resolve() or not path.resolve().is_relative_to(boundary)):
            raise ValueError("bound reading locator escapes its owner root")
    source = capture.read_manifest(raw / entry["manifest"])
    source["path"] = entry["manifest"]
    index = delta.source_index([source])
    card = delta.read_card(root / entry["card"], root)
    card["citations"] = [c for c in card.get("citations", []) if delta.expand(c, index)]
    if not card["citations"]:
        raise ValueError("bound understanding card does not cite this source")
    return delta.bounded_report(delta.reconcile([card], index, root), root)


def run(ctx, entries):
    """Only explicitly bound card/manifest pairs. Never promote a requirement."""
    if ctx.run_dir is None or not ctx.run_dir.is_absolute() or ctx.run_dir != ctx.run_dir.resolve():
        raise ValueError("understanding perception requires an absolute owned --run-dir")
    brief_mod = _load("intake/understanding_brief.py")
    capture = _load("intake/capture_coverage.py")
    delta = _load("intake/understanding_delta.py")
    semantic = _load("intake/understanding_semantic.py")
    native = _load("runtime/native_reader.py")
    cli = _load("runtime/cli.py")
    failures, results = [], []
    ctx.running["outcomes"]["readings"] = results
    ctx.running["outcomes"]["unread"] = failures
    for entry in entries:
        if not all(isinstance(entry.get(k), str) and entry[k] for k in ("id", "card", "manifest")):
            raise ValueError("understanding source requires id, card and manifest")
        assets = tuple(Path(p) for p in entry.get("source_asset_roots", []))
        if any(not p.is_absolute() for p in assets):
            raise ValueError("source asset roots must be absolute")
        try:
            report = scoped_report(ctx.records_root, entry)
            # Scope before materialization: unrelated private sources do not enter
            # the model or its prompt, and we do not read every packet to select one.
            selected = []
            for card in report.get("cards", []):
                if card.get("card") == entry["card"]:
                    judged = dict(card.get("judged") or {})
                    measured = [m for m in card.get("measurements", [])
                                if m.get("manifest") == entry["manifest"]]
                    if not measured or any(m.get("shape") in ("UNKNOWN_SHAPE", "UNREADABLE") for m in measured):
                        raise ValueError("bound source cannot be measured; absence is not no change")
                    judged["review_ranges"] = [r for r in judged.get("review_ranges", [])
                                               if r.get("manifest") == entry["manifest"]]
                    selected.append({**card, "judged": judged})
            if not selected:
                raise ValueError("bound understanding card is absent")
            packets, refused = brief_mod.briefs({**report, "cards": selected}, ctx.records_root,
                                               allowed_asset_roots=assets)
            failures.extend({"id": entry["id"], **row} for row in refused)
            for packet in packets:
                # The preceding batch may have updated this same card. Refresh
                # its context before spending another call; never reuse our own
                # obsolete card baseline for the next batch.
                current_report = scoped_report(ctx.records_root, entry)
                current_card = next(c for c in current_report["cards"] if c["card"] == entry["card"])
                source = packet["source"]
                current_range = next((r for r in current_card["judged"]["review_ranges"]
                                      if all(r.get(k) == source.get(k) for k in
                                             ("manifest", "from_position", "to_position"))), None)
                if current_range is None:
                    raise ValueError("source range changed before the next batch; remeasure on the next pass")
                packet = brief_mod.brief_for(current_card, current_range, ctx.records_root,
                                            allowed_asset_roots=assets)
                identity = hashlib.sha256(json.dumps({"reader": native.VERSION, "source": packet["source"], "read": packet["read"]},
                                                      sort_keys=True).encode()).hexdigest()
                directory = ctx.run_dir / "readings" / identity
                reading_path = directory / "reading.json"
                if reading_path.is_file():
                    reading = semantic.read_reading(reading_path)
                else:
                    reply = ctx.spend("refresh", ask={"kind": "understanding", "brief": packet,
                                                     "directory": str(directory)})
                    reading = native.reading_from(packet, reply)
                    _write(reading_path, reading)
                    # Invalid model output remains an artifact, never accepted.
                    reading = semantic.read_reading(reading_path)
                def measure():
                    current_report = scoped_report(ctx.records_root, entry)
                    return cli._reading_current(semantic, current_report, reading,
                                                allowed_asset_roots=assets)
                delegation, reason = None, "no recorded grant bound; prepared for alignment"
                if entry.get("grant_ref"):
                    delegation, reason = cli._delegation_from_grant(entry["grant_ref"], ctx.now())
                resolution = semantic.resolve(reading, measure(), delegation)
                _write(directory / "resolution.json", resolution)
                receipt = None
                if delegation is not None:
                    receipt = semantic.apply(reading_path, ctx.records_root, measure=measure,
                                             delegation=delegation, committed_at=ctx.now())
                result = {"id": entry["id"], "reading": str(reading_path),
                          "resolution": str(directory / "resolution.json"),
                          "claims": len(reading["claims"]),
                          "awaiting": resolution.get("awaiting", []),
                          "authority": reason, "receipt": receipt}
                results.append(result)
                # Budget exhaustion on the next batch must leave the already
                # produced decisions visible, not an absent alignment file.
                ctx.running["outcomes"]["alignment"] = alignment(ctx.run_dir, entries, ctx.records_root)
                ctx.step("semantic", "resolved a pinned reading through the semantic owner",
                         source=entry["id"], reading=str(reading_path),
                         committed=bool(receipt), awaiting=len(result["awaiting"]))
                # Meaning was read only after the writer resolved it. Pending
                # decisions remain in resolution.json and never become Items.
                if not result["awaiting"] and (receipt is None and not reading["claims"] or
                                                receipt and receipt.get("state") == "COMPLETE"):
                    ctx.refresh_cursor({**(ctx.running.get("cursor") or {}), entry["id"]: identity})
        except (ValueError, OSError, semantic.Refused, brief_mod.Refused, delta.Refused, capture.Refused) as exc:
            failures.append({"id": entry["id"], "why": str(exc)})
    if failures:
        ctx.running["outcomes"]["alignment"] = alignment(ctx.run_dir, entries, ctx.records_root)
        ctx.unbound("SOURCE_UNAVAILABLE", "one or more bound readings could not be completed")
    ctx.running["outcomes"]["alignment"] = alignment(ctx.run_dir, entries, ctx.records_root)
    if ctx.running["outcomes"]["alignment"]["pending_readings"]:
        ctx.stop("NEEDS_DECISION", "readings prepared; only the claims requiring decisions remain pending")
    if not results:
        ctx.stop("NO_CHANGE", "bound source revisions have no unread ranges")
