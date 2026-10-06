"""Read captured source bytes at their recorded digest; never fetch or write.

Doc/Wiki's content.md is the layout written by records' expand_feishu_doc.py.
Web bodies are resolved only from declared verification_copies under an explicit
caller-owned asset root. Neither a timestamp nor a filename is a revision.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

SCHEMA = "serveros/source-reading-packet.v1"
MAX_BYTES = 8 * 1024 * 1024


class Refused(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> Path:
    if ".." in path.parts or not path.resolve().is_relative_to(root.resolve()):
        raise Refused("source path escapes its admitted root")
    return path.resolve()


def _bytes(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            body = stream.read(MAX_BYTES + 1)
    except OSError as exc:
        raise Refused(f"source bytes are unreadable: {exc}") from exc
    if len(body) > MAX_BYTES:
        raise Refused("source exceeds the 8 MiB reading budget; select smaller captured material")
    return body


def _digest(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise Refused("source has no exact SHA-256 content digest")
    return value


def _object(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise Refused(f"source {field} must be an object")
    return value


def _module(name: str):
    key = "serveros_intake_" + name
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def read_packet(manifest: str, records_root: Path | str, *,
                expected_identity: str | None = None,
                allowed_asset_roots: tuple[Path, ...] = ()) -> dict:
    """One fixed snapshot; explicit unknown revision and unread material coverage.

    `manifest` is relative to records/information/raw, like understanding_delta.
    External asset roots are supplied by the trusted caller, never the manifest.
    """
    root = Path(records_root)
    if not root.is_absolute():
        raise Refused("records root must be absolute")
    if any(not Path(allowed).is_absolute() for allowed in allowed_asset_roots):
        raise Refused("admitted asset roots must be absolute")
    key = Path(manifest)
    if key.is_absolute() or key.name != "manifest.json":
        raise Refused("a source manifest must be a raw-root-relative manifest.json")
    raw_root = root / "information/raw"
    path = _inside(raw_root / key, raw_root)
    try:
        document = json.loads(_bytes(path))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused("source manifest is not readable JSON") from exc
    if not isinstance(document, dict):
        raise Refused("source manifest is not an object")

    if "url" in document and "content_sha256" in document:
        kind = "DOCUMENT"
        identity = _digest(document.get("content_sha256"))
        body_path = _inside(path.with_name("content.md"), raw_root)
        media_count = document.get("media_count")
        coverage = {"body": "READ", "media_declared": media_count,
                    "media_read": 0}
    elif "source" in document and "fetch" in document:
        kind = "WEB"
        fetch = _object(document.get("fetch"), "fetch")
        identity = _digest(fetch.get("sha256"))
        if fetch.get("http_status") != 200:
            raise Refused("web snapshot has no successful response")
        external = _module("external_reference")
        try:
            quality = external.page_quality(document)
        except external.Refused as exc:
            raise Refused(str(exc)) from exc
        if quality["state"] != "CAPTURED":
            raise Refused("web snapshot has unresolved or detected page-shell evidence")
        copies = _object(document.get("body") or {}, "body").get("verification_copies") or []
        if not isinstance(copies, list):
            raise Refused("web verification_copies must be a list")
        candidates = []
        for copy in copies:
            if not isinstance(copy, dict) or copy.get("sha256") != identity:
                continue
            if not isinstance(copy.get("root"), str) or not isinstance(copy.get("file"), str):
                continue
            asset_root, filename = Path(copy["root"]), Path(copy["file"])
            if not asset_root.is_absolute() or filename.is_absolute() or ".." in filename.parts:
                raise Refused("web copy has an invalid asset locator")
            candidate = _inside(asset_root / filename, asset_root)
            if any(candidate.is_relative_to(Path(allowed).resolve())
                   for allowed in allowed_asset_roots if Path(allowed).is_absolute()):
                candidates.append(candidate)
        if not candidates:
            raise Refused("no current web body copy is inside an explicitly admitted asset root")
        body_path = candidates[0]
        materials = _object(document.get("materials") or {}, "materials")
        coverage = {"body": "READ", "media_declared": materials.get("count")
                    if materials.get("inventoried") is True else None,
                    "media_read": 0}
    else:
        raise Refused("this manifest declares no supported document body snapshot")
    if expected_identity is not None and identity != expected_identity:
        raise Refused("source digest changed after the review range was measured")
    body = _bytes(body_path)
    if hashlib.sha256(body).hexdigest() != identity:
        raise Refused("source body digest does not match its manifest; recapture before reading")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused("source body is not UTF-8 text") from exc
    return {"schema": SCHEMA, "kind": kind, "manifest": manifest,
            "path": str(body_path), "revision": None,
            "content_identity": identity,
            "coverage": {**coverage, "bytes": len(body)}, "text": text}


def meeting_packet(declaration: dict, *, observed_at: str) -> dict:
    """Read the existing bundle producer's available transcript, even before audio.

    This consumes its declaration API; it does not invent a meeting manifest or
    fetch an ASR job. Untied background segments are excluded by the existing
    relevance kernel. Declared ties locate evidence; they grant no authority.
    """
    name = "serveros_space_meeting_bundle"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "space/meeting_bundle.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    bundle = sys.modules[name]
    try:
        reading = bundle.assemble(declaration, observed_at=observed_at)["reading"]
        content = reading["readable"]["content"]
        transcript = reading["members"].get("transcript")
        if not transcript or transcript["state"] != "PRESENT":
            raise Refused("meeting transcript is not available yet")
        admitted, dropped, _ = bundle.kernel._segments(
            {"content": content}, "TRANSCRIPT",
            {"incidental_audio_to_understanding": "EXCLUDE"})
    except (bundle.Refused, bundle.kernel.Rejected) as exc:
        raise Refused(str(exc)) from exc
    segments = []
    for segment in content.get("transcript_segments") or []:
        locator = {key: segment.get(key) for key in ("start_s", "end_s", "provenance", "tie")}
        if locator in admitted:
            if not isinstance(segment.get("text"), str):
                raise Refused("meeting segment has no captured text")
            segments.append({**locator, "text": segment["text"],
                             "speaker": segment.get("speaker")})
    if not segments:
        raise Refused("meeting has no located transcript segment qualified for understanding")
    pinned = {"scope": reading["scope"], "object_ref": transcript["object_ref"],
              "revision": transcript["revision"], "segments": segments}
    identity = hashlib.sha256(json.dumps(pinned, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()
    return {"schema": SCHEMA, "kind": "MEETING", "manifest": None, "path": None,
            "source_ref": transcript["object_ref"], "revision": transcript["revision"],
            "content_identity": identity, "segments": segments,
            "coverage": {"body": "SELECTED_TRANSCRIPT", "segments_read": len(segments),
                         "segments_excluded": len(dropped),
                         "not_present": {k: v for k, v in reading["not_present"].items()
                                         if v != "PRESENT"}},
            "text": "\n".join(segment["text"] for segment in segments)}
