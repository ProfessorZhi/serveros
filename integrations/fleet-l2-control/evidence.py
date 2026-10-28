#!/usr/bin/env python
"""Re-derive `EVIDENCE-1.41.0.json` from the verbatim CloudCLI captures.

Why this exists
---------------
The matrix in `CAPABILITY-MATRIX.md` makes claims about what the device
declares and what it answers. A claim about a device that nobody can re-check
is a story, so the fixture beside it is generated from #411's captures by this
script and nothing else — and `tests/test_l2_cloudcli_control_matrix.py`
re-runs the same derivation whenever the captures are on disk, failing if the
committed fixture and the captures disagree.

What crosses into the fixture
-----------------------------
Shapes only: tool names, `required` lists, property names and their declared
types, and per roundtrip the argument names, the error `code`, `retryable`, and
the key sets of the result and of `details`. **No message text, no transcript,
no identifier** — the captures redact identifiers already, but they still carry
the message bodies that were sent, and a second copy of those in another
directory is a copy nobody asked for.

Usage
-----
    python integrations/fleet-l2-control/evidence.py \
        --captures tests/data \
        --out integrations/fleet-l2-control/EVIDENCE-1.41.0.json

`--captures` is the directory holding
`capture-cloudcli-1.41.0-tools-list-2026-10-27.json` and
`capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json`. The two files are
pinned by sha256 in the output, so the fixture says where it came from in a way
that can be checked rather than trusted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

TOOLS_NAME = "capture-cloudcli-1.41.0-tools-list-2026-10-27.json"
CASES_NAME = "capture-cloudcli-1.41.0-v1-roundtrips-2026-10-27.json"

#: Every `tools/list` capture in the directory, by release. A verb that is
#: declared by one release and not another is a different claim from a verb that
#: never existed, and the matrix has to be able to say which it is — so the
#: declared NAME SET of each release is carried, names only.
TOOLS_GLOB = "capture-cloudcli-*-tools-list-*.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_shapes(document: dict) -> dict[str, dict]:
    """Each declared tool's name, required arguments, and argument names."""
    shapes: dict[str, dict] = {}
    for row in document["tools"]:
        schema = row.get("inputSchema") or {}
        properties = schema.get("properties") or {}
        shapes[row["name"]] = {
            "required": sorted(schema.get("required") or []),
            "properties": sorted(properties),
            "types": {name: (spec or {}).get("type")
                      for name, spec in sorted(properties.items())},
        }
    return shapes


def case_shape(case: dict) -> dict:
    """One roundtrip reduced to what a claim can be checked against.

    `message` is dropped from `result_keys` on purpose: on an error it is the
    only field that carries prose, and every claim this fixture supports is
    about the envelope, not the sentence.
    """
    result = case.get("result")
    shape: dict = {
        "tool": case["tool"],
        "arguments": sorted(case.get("arguments") or {}),
        "isError": bool(case.get("isError")),
    }
    if isinstance(result, dict):
        shape["result_keys"] = sorted(key for key in result if key != "message")
        if "code" in result:
            shape["code"] = result["code"]
            shape["retryable"] = result.get("retryable")
            details = result.get("details")
            shape["details_keys"] = sorted(details) if isinstance(details, dict) else []
    return shape


def release_of(path: Path) -> str:
    """`capture-cloudcli-1.40.0-tools-list-2026-10-23.json` -> `1.40.0`."""
    return path.name.split("cloudcli-", 1)[1].split("-tools-list", 1)[0]


def declared_by_release(directory: Path) -> dict[str, list[str]]:
    """Each release's declared verb NAMES, read from its own `tools/list`."""
    declared: dict[str, list[str]] = {}
    for path in sorted(directory.glob(TOOLS_GLOB)):
        document = json.loads(path.read_text(encoding="utf-8"))
        declared[release_of(path)] = sorted(row["name"] for row in document["tools"])
    return declared


def derive(directory: Path) -> dict:
    tools_capture = directory / TOOLS_NAME
    cases_capture = directory / CASES_NAME
    tools_document = json.loads(tools_capture.read_text(encoding="utf-8"))
    cases_document = json.loads(cases_capture.read_text(encoding="utf-8"))
    return {
        "schema": "serveros/cloudcli-native-evidence.v1",
        "note": (
            "SHAPES ONLY. Derived from #411's verbatim captures; no message text, "
            "transcript or identifier is carried. Re-derive with "
            "integrations/fleet-l2-control/evidence.py."
        ),
        "source": {
            "package": cases_document["cloudcli_package_version"],
            "protocol_version": tools_document.get("protocolVersion"),
            "captured_at_utc": tools_document["captured_at_utc"],
            "tools_list_capture": tools_capture.name,
            "tools_list_sha256": digest(tools_capture),
            "roundtrips_capture": cases_capture.name,
            "roundtrips_sha256": digest(cases_capture),
        },
        "tools": tool_shapes(tools_document),
        "declared_by_release": declared_by_release(directory),
        "cases": {name: case_shape(case)
                  for name, case in sorted(cases_document["cases"].items())},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--captures", type=Path, default=Path("tests/data"))
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).with_name("EVIDENCE-1.41.0.json"))
    args = parser.parse_args(argv)
    evidence = derive(args.captures)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=1) + "\n",
                        encoding="utf-8", newline="\n")
    releases = ", ".join(f"{name}:{len(names)}"
                         for name, names in evidence["declared_by_release"].items())
    print(f"wrote {args.out}: {len(evidence['tools'])} tools, "
          f"{len(evidence['cases'])} cases, releases {releases}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
