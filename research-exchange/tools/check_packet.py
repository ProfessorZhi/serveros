#!/usr/bin/env python3
"""Validate a ServerOS research-exchange packet (and optionally a result).

Standard library only. Structural rules come from the JSON Schema files in
``contracts/``; path safety, input hashing and result/revision correspondence
are enforced here because they are cross-file semantics.

What this tool does NOT do, on purpose:

* it does not grant or infer permission to share material.  ``data_policy``
  labels and ``permission_ref`` are format-checked only.  A well-formed label
  is never treated as authorization.
* it performs no redaction and never claims redaction succeeded.
* it never claims a result matches a reviewed version unless the caller
  supplies the actual ``--input-revision`` and it matches.

Exit code is 0 when every requested check passes, non-zero otherwise.
Output is a short single-line JSON object on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path, PurePosixPath

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "contracts"
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_REPORT_PREFIX = "results"


class JsonReject(ValueError):
    """Raised when a document contains a value JSON does not allow us to trust."""


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _reject_constant(name: str):
    raise JsonReject(f"non-finite JSON constant not allowed: {name}")


def load_json(path: Path):
    """Parse JSON, rejecting NaN/Infinity constants outright."""
    path = Path(path)
    if ".." in path.parts or path.absolute() != path.resolve():
        raise JsonReject("manifest path must not traverse symlinks or '..'")
    text = Path(path).read_text(encoding="utf-8")
    return json.loads(text, parse_constant=_reject_constant)


def _find_nonfinite(obj, path: str = "$"):
    """Reject finite-looking literals that overflow to inf (e.g. 1e999)."""
    problems: list[str] = []
    if isinstance(obj, float) and not math.isfinite(obj):
        problems.append(f"{path}: non-finite number")
    elif isinstance(obj, dict):
        for key, value in obj.items():
            problems += _find_nonfinite(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            problems += _find_nonfinite(value, f"{path}[{index}]")
    return problems


# --------------------------------------------------------------------------- #
# Minimal JSON Schema validator (draft 2020-12 subset used by contracts/)
# --------------------------------------------------------------------------- #

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_integer(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    return False


def _resolve_ref(ref: str, root):
    if not ref.startswith("#"):
        raise JsonReject(f"unsupported external $ref: {ref}")
    node = root
    if len(ref) > 1:
        for raw in ref[2:].split("/"):
            part = raw.replace("~1", "/").replace("~0", "~")
            node = node[part]
    return node


def _check_type(instance, expected, path: str, errors: list[str]) -> None:
    names = expected if isinstance(expected, list) else [expected]
    ok = False
    for name in names:
        if name == "object" and isinstance(instance, dict):
            ok = True
        elif name == "array" and isinstance(instance, list):
            ok = True
        elif name == "string" and isinstance(instance, str):
            ok = True
        elif name == "boolean" and isinstance(instance, bool):
            ok = True
        elif name == "integer" and _is_integer(instance):
            ok = True
        elif name == "number" and _is_number(instance):
            ok = True
        elif name == "null" and instance is None:
            ok = True
    if not ok:
        errors.append(f"{path}: expected type {expected!r}, got {type(instance).__name__}")


def _valid(instance, schema, root, path: str = "$") -> bool:
    probe: list[str] = []
    validate(instance, schema, root, path, probe)
    return not probe


def validate(instance, schema, root, path: str = "$", errors: list[str] | None = None):
    if errors is None:
        errors = []
    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        return validate(instance, _resolve_ref(schema["$ref"], root), root, path, errors)

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']}")

    if "type" in schema:
        _check_type(instance, schema["type"], path, errors)

    for sub in schema.get("allOf", []):
        validate(instance, sub, root, path, errors)
    if "anyOf" in schema and not any(_valid(instance, s, root, path) for s in schema["anyOf"]):
        errors.append(f"{path}: does not satisfy anyOf")
    if "oneOf" in schema:
        matches = sum(1 for s in schema["oneOf"] if _valid(instance, s, root, path))
        if matches != 1:
            errors.append(f"{path}: must satisfy exactly one oneOf branch (matched {matches})")

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, value in instance.items():
            child = f"{path}.{key}"
            if key in props:
                validate(value, props[key], root, child, errors)
            elif extra is False:
                errors.append(f"{path}: unexpected property '{key}'")
            elif isinstance(extra, dict):
                validate(value, extra, root, child, errors)

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: expected at least {schema['minItems']} items, got {len(instance)}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: expected at most {schema['maxItems']} items, got {len(instance)}")
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in instance]
            if len(seen) != len(set(seen)):
                errors.append(f"{path}: items must be unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, value in enumerate(instance):
                validate(value, items, root, f"{path}[{index}]", errors)
        elif isinstance(items, list):
            for index, sub in enumerate(items):
                if index < len(instance):
                    validate(instance[index], sub, root, f"{path}[{index}]", errors)

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']}")

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMaximum" in schema and instance >= schema["exclusiveMaximum"]:
            errors.append(f"{path}: must be less than {schema['exclusiveMaximum']}")

    return errors


def _load_schema(name: str):
    return load_json(SCHEMA_DIR / name)


# --------------------------------------------------------------------------- #
# Path safety
# --------------------------------------------------------------------------- #

def safe_relative(base: Path, rel) -> tuple[Path | None, list[str]]:
    """Resolve a packet-relative path, rejecting absolute paths, ``..`` and
    symlinked components.  Returns (target_or_None, problems).
    """
    problems: list[str] = []
    if not isinstance(rel, str) or not rel.strip():
        return None, ["path must be a non-empty string"]
    if "\0" in rel:
        return None, ["path must not contain a NUL byte"]
    if "\\" in rel:
        problems.append("backslashes are not allowed")
    posix = PurePosixPath(rel)
    if posix.is_absolute() or rel.startswith("/"):
        problems.append("absolute paths are not allowed")
    if any(part == ".." for part in posix.parts):
        problems.append("'..' segments are not allowed")
    if problems:
        return None, problems

    target = base.joinpath(*posix.parts)
    cursor = base
    for part in posix.parts:
        cursor = cursor / part
        if os.path.islink(cursor):
            problems.append(f"symlink component rejected: {part}")
            return None, problems
    return target, problems


def _within(base: Path, target: Path) -> bool:
    base_real = Path(os.path.realpath(base))
    try:
        Path(os.path.realpath(target)).relative_to(base_real)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def check(packet_path, result_path=None, input_revision=None):
    packet_path = Path(packet_path)
    base = packet_path.parent

    errors: list[str] = []
    packet_schema_errors: list[str] = []
    input_errors: list[str] = []
    result_schema_errors: list[str] = []
    result_ref_errors: list[str] = []
    report_errors: list[str] = []

    report: dict = {
        "packet": str(packet_path),
        "result": str(result_path) if result_path else None,
        "checks": {
            "packet_schema": False,
            "packet_inputs": False,
            "result_schema": None,
            "result_references": None,
            "report_path": None,
            "version_match_verified": False,
            "data_policy": "format_only",
            "authorization_asserted": False,
            "redaction_verified": False,
        },
        "notes": [
            "data_policy labels and permission_ref are format-checked only; "
            "they grant no sharing permission.",
            "this tool performs no redaction and asserts no authorization.",
            "fixtures and templates are mechanism checks, not business evidence.",
            "version matching compares the caller-supplied commit; this offline tool does not fetch Git or verify citation truth.",
        ],
    }

    # ---- packet load + schema -------------------------------------------- #
    packet = None
    try:
        packet = load_json(packet_path)
    except JsonReject as exc:
        packet_schema_errors.append(f"packet: {exc}")
    except FileNotFoundError:
        packet_schema_errors.append(f"packet: file not found: {packet_path}")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        packet_schema_errors.append(f"packet: cannot read/parse: {exc}")

    if not packet_schema_errors:
        packet_schema_errors += _find_nonfinite(packet, "$")
        validate(packet, _load_schema("research-packet.schema.json"), _load_schema("research-packet.schema.json"), "$", packet_schema_errors)
    report["checks"]["packet_schema"] = not packet_schema_errors

    # ---- inputs: paths + hashes ------------------------------------------ #
    if isinstance(packet, dict) and isinstance(packet.get("inputs"), list):
        for index, item in enumerate(packet["inputs"]):
            label = f"inputs[{index}]"
            if not isinstance(item, dict):
                input_errors.append(f"{label}: not an object")
                continue
            target, problems = safe_relative(base, item.get("path"))
            input_errors += [f"{label}.path: {p}" for p in problems]
            if target is None:
                continue
            if not _within(base, target):
                input_errors.append(f"{label}.path: escapes the packet directory")
                continue
            if not target.is_file():
                input_errors.append(f"{label}.path: file not found: {item.get('path')}")
                continue
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            declared = item.get("sha256")
            if isinstance(declared, str) and digest != declared.lower():
                input_errors.append(
                    f"{label}: sha256 mismatch (declared {declared.lower()}, actual {digest})"
                )
    report["checks"]["packet_inputs"] = not input_errors

    # ---- result load + schema -------------------------------------------- #
    result = None
    if result_path is not None:
        try:
            relative_result = Path(result_path).absolute().relative_to(base.absolute())
            target, problems = safe_relative(base, relative_result.as_posix())
            if problems or not relative_result.parts or relative_result.parts[0] != DEFAULT_REPORT_PREFIX:
                raise JsonReject("result manifest must be inside this packet's results/ without symlinks")
            result = load_json(Path(result_path))
        except JsonReject as exc:
            result_schema_errors.append(f"result: {exc}")
        except FileNotFoundError:
            result_schema_errors.append(f"result: file not found: {result_path}")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            result_schema_errors.append(f"result: cannot read/parse: {exc}")
        except ValueError as exc:
            result_schema_errors.append(f"result: invalid manifest placement: {exc}")
        if not result_schema_errors:
            result_schema_errors += _find_nonfinite(result, "$")
            result_schema_errors += validate(
                result,
                _load_schema("research-result.schema.json"),
                _load_schema("research-result.schema.json"),
                "$",
                [],
            )
        report["checks"]["result_schema"] = not result_schema_errors

        # ---- result references: request + revision ----------------------- #
        caller_rev = (input_revision or "").lower() or None
        if isinstance(result, dict):
            if isinstance(packet, dict) and packet.get("request_id") != result.get("request_id"):
                result_ref_errors.append(
                    "result.request_id does not match packet.request_id"
                )
            if caller_rev is None:
                result_ref_errors.append(
                    "--input-revision is required with --result; without the actual "
                    "reviewed revision the version match cannot be verified"
                )
            else:
                if not HEX40_RE.match(caller_rev):
                    result_ref_errors.append("--input-revision must be 40 lowercase hex characters")
                elif result.get("input_revision") != caller_rev:
                    result_ref_errors.append(
                        "result.input_revision does not match the caller-supplied --input-revision"
                    )
                elif isinstance(packet, dict) and "input_revision" in packet \
                        and str(packet["input_revision"]).lower() != caller_rev:
                    result_ref_errors.append(
                        "packet.input_revision does not match the caller-supplied --input-revision"
                    )
        report["checks"]["result_references"] = not result_ref_errors
        report["checks"]["version_match_verified"] = (
            bool(result_ref_errors) is False
            and caller_rev is not None
            and not result_schema_errors
            and not packet_schema_errors and not input_errors
            and isinstance(result, dict)
        )

        # ---- report_path -------------------------------------------------- #
        if isinstance(result, dict):
            rel = result.get("report_path")
            target, problems = safe_relative(base, rel)
            report_errors += [f"result.report_path: {p}" for p in problems]
            if target is not None:
                parts = PurePosixPath(rel).parts
                handoff = packet.get("handoff") if isinstance(packet, dict) else None
                prefix = handoff.get("result_prefix", "results/") if isinstance(handoff, dict) else "results/"
                _, prefix_problems = safe_relative(base, prefix)
                prefix_parts = PurePosixPath(prefix).parts if isinstance(prefix, str) else ()
                if prefix_problems or parts[:len(prefix_parts)] != prefix_parts:
                    report_errors.append("result.report_path is outside the bound result_prefix")
                if (len(parts) < 3 or parts[0] != DEFAULT_REPORT_PREFIX
                        or parts[1] != result.get("result_id")):
                    report_errors.append(
                        f"result.report_path must live under '{DEFAULT_REPORT_PREFIX}/<result_id>/'"
                    )
                elif relative_result.parts[:2] != parts[:2]:
                    report_errors.append("result manifest and report must share the same result_id directory")
                elif not _within(base, target):
                    report_errors.append("result.report_path escapes the packet directory")
                elif not target.is_file():
                    report_errors.append(f"result.report_path does not exist: {rel}")
        report["checks"]["report_path"] = not report_errors

    errors += packet_schema_errors + input_errors + result_schema_errors \
        + result_ref_errors + report_errors

    report["errors"] = errors
    report["ok"] = not errors
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a research-exchange packet and optional result manifest."
    )
    parser.add_argument("--packet", required=True, help="path to packet.json")
    parser.add_argument("--result", default=None, help="path to result.json")
    parser.add_argument(
        "--input-revision",
        default=None,
        help="the 40-hex commit the caller actually reviewed; required with --result",
    )
    args = parser.parse_args(argv)

    report = check(args.packet, args.result, args.input_revision)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
