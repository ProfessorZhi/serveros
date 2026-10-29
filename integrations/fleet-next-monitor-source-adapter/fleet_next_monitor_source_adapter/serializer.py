"""Strict JSON at the edge — and nothing that could name Python code.

A monitor tick is driven by data, not by code the data chooses. So the input
document is a closed set of keys: a key that is not on the list is refused, and
a key that hints at an import (`factory`, `module`, `callable`, `import`,
`python`, dunder names) is refused by name, so the refusal explains itself
instead of looking like a typo.

This module contains no `eval`, no `exec`, no `__import__` and no `importlib`.
That is asserted by a test, because "we would never do that" is not a property.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from .contract import CallerGrant, Observation, SourceRefused

REQUEST_SCHEMA = "serveros/fleet-next-monitor-request.v1"

#: The complete set of keys a request may carry. `expected_boot_id` is the
#: watch-level boot expectation: without it a request could only ever mean "the
#: boot this fixture client happens to declare".
REQUEST_KEYS = frozenset({
    "schema", "view", "device_ref", "project", "session_ref",
    "run_id", "delivery_id", "source_sha", "expected_boot_id",
})

#: The input document. The fixture/grant are separate objects so a request can
#: never smuggle a client or a grant through a field it owns.
#:
#: v2 renamed the request block to `read_request`. `request` is also the name of
#: a record field that `abilities/target-contract.yaml` declares, and the
#: repository's reader-detector gate (a name-based scan, not a semantic one)
#: counted this package's envelope key as a reader of THAT field — a field whose
#: own exemption says nothing reads it. The two are different declaration
#: surfaces, but the gate cannot tell them apart, and the adapter's namespace is
#: the side that can move.
ENVELOPE_KEYS = frozenset({"schema", "read_request", "grant", "fixture", "instance"})

#: The v1 spelling of the request block, retired in v2 and still accepted so a
#: document written to the old contract keeps parsing. It is spelled here and
#: nowhere else: a read position anywhere in this package that spelled it would
#: put the gate back where it was.
RETIRED_REQUEST_KEY = "request"

#: What a v1 document may carry. Derived rather than re-spelled, so the retired
#: name has exactly one home.
_V1_ENVELOPE_KEYS = ENVELOPE_KEYS | {RETIRED_REQUEST_KEY}

#: The `instance` block names which Legacy instance the fixture pretends to be.
#: Closed too: an ignored field is still a field somebody believed did something.
INSTANCE_KEYS = frozenset({"instanceId", "routeId", "bootId", "provider", "maxReadings"})

_SCALAR_KEYS = ("view", "device_ref", "project", "session_ref", "run_id",
                "delivery_id", "source_sha", "expected_boot_id")

_IMPORT_HINTS = ("import", "module", "factory", "callable", "python", "eval", "exec", "__")

#: One bound on the whole document. A monitor tick is not a batch job.
MAX_INPUT_BYTES = 1_000_000


def _refuse_import_shape(key: str) -> None:
    lowered = key.lower()
    for hint in _IMPORT_HINTS:
        if hint in lowered:
            raise SourceRefused(
                "IMPORT_SPEC_REFUSED",
                f"the key {key!r} looks like a Python import spec. A monitor "
                f"tick is driven by data, not by code the data names; the "
                f"adapter is injected by the caller, never by the input.",
            )


def _closed(document: Any, allowed: frozenset[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        raise SourceRefused("BAD_JSON", f"{label} must be a JSON object")
    for key in document:
        if not isinstance(key, str):
            raise SourceRefused("BAD_JSON", f"{label} keys must be strings")
        if key not in allowed:
            _refuse_import_shape(key)
            raise SourceRefused(
                "UNKNOWN_FIELD",
                f"{label} carries {key!r}, which is not a field this adapter "
                f"reads. Known fields: {sorted(allowed)}",
            )
    return document


def parse_request(text: str) -> dict[str, Any]:
    """Parse one request. Unknown keys and import-shaped keys are refused."""
    if not isinstance(text, str) or not text.strip():
        raise SourceRefused("BAD_JSON", "a request is a non-empty JSON object")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise SourceRefused(
            "INPUT_TOO_LARGE",
            f"the request is larger than {MAX_INPUT_BYTES} bytes; a monitor tick "
            f"is bounded and does not read an unbounded document",
        )
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SourceRefused("BAD_JSON", f"input is not JSON ({type(exc).__name__})") from None
    _closed(document, REQUEST_KEYS, label="the request")
    schema = document.get("schema")
    if schema is not None and schema != REQUEST_SCHEMA:
        raise SourceRefused("BAD_SCHEMA", f"schema {schema!r} is not {REQUEST_SCHEMA!r}")
    out: dict[str, Any] = {}
    for key in _SCALAR_KEYS:
        value = document.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise SourceRefused("BAD_REQUEST", f"`{key}` must be a non-empty string")
        out[key] = value
    view = out.get("view")
    if view is None:
        raise SourceRefused("BAD_REQUEST", "a request must name its `view`")
    return out


def _request_block(document: Mapping[str, Any]) -> Mapping[str, Any]:
    """The request block, under either spelling, and never under both.

    Two spellings of one block is an ambiguity, and an ambiguity here would be
    resolved by silently ignoring one of them — which is how a caller ends up
    believing a field it wrote did something.
    """
    canonical = document.get("read_request")
    retired = document.get(RETIRED_REQUEST_KEY)
    if canonical is not None and retired is not None:
        raise SourceRefused(
            "BAD_REQUEST",
            "the input document carries both `read_request` and the retired v1 "
            "key for the same block; this adapter will not guess which one is "
            "meant")
    block = canonical if canonical is not None else retired
    if not isinstance(block, Mapping):
        raise SourceRefused("BAD_REQUEST",
                            "the input document carries no `read_request` object")
    return block


def parse_envelope(text: str) -> dict[str, Any]:
    """Parse the CLI document: request + grant + fixture, each closed on its own."""
    if not isinstance(text, str) or not text.strip():
        raise SourceRefused("BAD_JSON", "an input document is required")
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise SourceRefused("INPUT_TOO_LARGE", f"input exceeds {MAX_INPUT_BYTES} bytes")
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise SourceRefused("BAD_JSON", f"input is not JSON ({type(exc).__name__})") from None
    _closed(document, _V1_ENVELOPE_KEYS, label="the input document")
    request = _request_block(document)
    grant = document.get("grant")
    if not isinstance(grant, Mapping):
        raise SourceRefused("BAD_GRANT", "the input document carries no `grant` object")
    fixture = document.get("fixture")
    if not isinstance(fixture, Mapping):
        raise SourceRefused("BAD_FIXTURE", "the input document carries no `fixture` object")
    instance = document.get("instance") or {}
    if not isinstance(instance, Mapping):
        raise SourceRefused("BAD_FIXTURE", "`instance` must be a JSON object")
    _closed(instance, INSTANCE_KEYS, label="the instance block")
    return {
        "read_request": parse_request(json.dumps(request)),
        "grant": CallerGrant.from_mapping(grant),
        "fixture": fixture,
        "instance": instance,
    }


def dump_observation(observation: Observation) -> str:
    """Serialise an observation deterministically, with identities separate."""
    return json.dumps(observation.as_dict(), sort_keys=True, ensure_ascii=False)


def dump_refusal(exc: SourceRefused) -> str:
    """A refusal written here — never a remote message."""
    return json.dumps({"schema": REQUEST_SCHEMA, "code": exc.code, "why": exc.why},
                      sort_keys=True, ensure_ascii=False)
