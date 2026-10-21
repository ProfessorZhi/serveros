"""The preflight a ServerOS launcher runs before it hands an agent client a line.

WHY A LAUNCHER NEEDS ONE
------------------------
`--mcp-config` takes "JSON files or strings" — measured on this node,
`claude --help`, 2026-10-14. The STRING form puts the whole config into a
command line, and on a shared host a command line is readable by every account
with `ps`. That is issue #171: an MCP token that was visible in `ps aux` on the
Tokyo host.

ServerOS does not hold that token, and this module does not go looking for one.
`fleet/controller.py` states the boundary the product is built on —
"Authorization belongs to the MCP client that obtained it; ServerOS never holds
the token" — and the audit recorded in the pull request that carries this file
found no credential VALUE in any argv this repository builds. What ServerOS
DOES own is the launcher that builds the line, and a launcher is the last place
that can refuse before the line exists.

So this is a bound, not a cleanup, and it is placed where the bound is cheap.

WHAT IT ASKS, AND OF WHAT
-------------------------
Two questions, of every token of the command line and of the config that line
points at:

  * does this VALUE carry the shape of a credential?
    (`credential_shapes.shape_names_in`)
  * does this KEY name say a credential lives under it?
    (`credential_shapes.key_paths_in`, over
    `fleet/transport-contract.yaml#transport.forbidden_binding_fields`)

A FINDING NAMES A CODE, A RULE AND A POSITION. It never carries the value, and
no function here returns one. That is not politeness: a refusal that quoted
what it refused would move the credential from the command line into the
journal, which is the leak it was invoked to prevent. The launcher prints what
this returns verbatim.

FAIL CLOSED, AND SAY SO CLEARLY
-------------------------------
A config that will not parse is refused rather than passed through. A launcher
that cannot read the thing it is about to hand an agent does not know what it
is handing it, and "the check could not run" is not "the check passed".

The same reasoning covers false positives, which are expected and are the price
of the bound: a wake whose briefing happens to contain a credential-shaped
string is refused with a reason code rather than started with a token on its
command line. A refusal is legible, retryable and one line of output; a leak is
neither.

WHAT THIS DOES NOT DO
---------------------
It does not authenticate a grant, it does not prove the config is the RIGHT
one, and it does not close the client side of issue #171 — a `--mcp-config`
built by CloudCLI, Claude Code or a user's own settings file is outside this
repository and this bound does not reach it. See the pull request's
owner-of-code section. It also does not stop a credential arriving through the
environment, which is the broader surface the same audit named and a different
change.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _credential_shapes():
    """Loaded by path, not imported by name.

    `bin/serverosctl` puts `runtime/` on `sys.path` and imports by bare name;
    pytest imports the directory as a namespace package. A by-path load is the
    one form that is correct under both, and it is the idiom
    `runtime/bounded_invocation.py` already uses for the same reason.
    """
    if "credential_shapes" in sys.modules:
        return sys.modules["credential_shapes"]
    spec = importlib.util.spec_from_file_location(
        "credential_shapes", _HERE / "credential_shapes.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["credential_shapes"] = module
    spec.loader.exec_module(module)
    return module


#: Exit codes. `REFUSED` is distinct from a usage error so that a caller can
#: tell "the line was dangerous" from "the check did not run".
CLEAN = 0
USAGE = 1
REFUSED = 3

#: A token of the command line carries a credential shape.
ARGV_CARRIES_CREDENTIAL_SHAPE = "ARGV_CARRIES_CREDENTIAL_SHAPE"
#: The config declares a field a credential is not allowed to live under.
MCP_CONFIG_CARRIES_FORBIDDEN_KEY = "MCP_CONFIG_CARRIES_FORBIDDEN_KEY"
#: A value inside the config carries a credential shape.
MCP_CONFIG_CARRIES_CREDENTIAL_SHAPE = "MCP_CONFIG_CARRIES_CREDENTIAL_SHAPE"
#: The config could not be read or parsed. Refused, never passed through.
MCP_CONFIG_UNREADABLE = "MCP_CONFIG_UNREADABLE"


@dataclass(frozen=True)
class Finding:
    """One reason to refuse, expressed without the thing that caused it."""

    code: str
    rule: str
    where: str

    def line(self) -> str:
        return f"{self.code} rule={self.rule} where={self.where}"


def audit_token(token: str, position: str) -> list[Finding]:
    """One command-line token, judged as a value.

    `argv=True` asks the header question as well as the value question: a
    command line is exactly the place `Authorization: Bearer …` must not be.
    """
    shapes = _credential_shapes()
    return [Finding(ARGV_CARRIES_CREDENTIAL_SHAPE, name, position)
            for name in shapes.shape_names_in(token, argv=True)]


def audit_argv(argv) -> list[Finding]:
    """Every token of a command line, in order.

    The position is `argv[i]` and the value is dropped at the call — the index
    is what a person needs to find the argument, and one token is enough to
    leak a credential from a log line.
    """
    found: list[Finding] = []
    for index, token in enumerate(argv):
        found.extend(audit_token(str(token), f"argv[{index}]"))
    return found


def audit_mcp_config(text: str, where: str = "mcpConfig") -> list[Finding]:
    """The config, judged as a document.

    Two passes rather than one, because they fail for different reasons and a
    reader should not have to guess which: a forbidden KEY is the config
    declaring where a credential would live, and a forbidden VALUE is a
    credential that is already there.
    """
    shapes = _credential_shapes()
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        # Not a claim that it is dangerous — a claim that it could not be
        # checked, which for a launcher is the same answer.
        return [Finding(MCP_CONFIG_UNREADABLE, "not-json", where)]
    found = [Finding(MCP_CONFIG_CARRIES_FORBIDDEN_KEY, "forbidden-field", f"{where}:{path}")
             for path in shapes.key_paths_in(document)]
    for path, value in shapes.string_leaves_in(document):
        for name in shapes.shape_names_in(value, argv=True):
            found.append(Finding(MCP_CONFIG_CARRIES_CREDENTIAL_SHAPE, name,
                                 f"{where}:{path}"))
    return found


def audit_argv_stream(tokens, *, mcp_config: Path | None = None) -> list[Finding]:
    """The whole preflight: the line, and the file the line points at."""
    found = audit_argv(tokens)
    if mcp_config is None:
        return found
    try:
        text = mcp_config.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return found + [Finding(MCP_CONFIG_UNREADABLE, "unreadable", "mcpConfig")]
    return found + audit_mcp_config(text)


def report(findings, *, stream=None) -> None:
    """Print the refusal. Codes, rule names and positions — never a value."""
    stream = stream if stream is not None else sys.stderr
    for finding in findings:
        print(f"launcher_guard: REFUSED {finding.line()}", file=stream)


def _tokens_from_stdin() -> list[str]:
    """NUL-separated, and read from STDIN rather than from argv.

    A guard invoked as `guard <token>` would put the very value it is judging
    onto a command line of its own, where `ps` can read it — the check would
    leak exactly what it was called to catch. The launcher therefore pipes the
    line in and this reads it, and the guard's own argv carries only a path.
    """
    raw = sys.stdin.buffer.read()
    return [chunk.decode("utf-8", "surrogateescape")
            for chunk in raw.split(b"\x00") if chunk]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher_guard", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check-argv",
        help="read NUL-separated command-line tokens from stdin and refuse on a credential")
    check.add_argument("--mcp-config", default="",
                       help="also judge the JSON config this path points at")
    args = parser.parse_args(argv)

    if args.command != "check-argv":  # pragma: no cover - argparse constrains this
        parser.error(f"unknown command {args.command!r}")
        return USAGE

    config = Path(args.mcp_config) if args.mcp_config else None
    findings = audit_argv_stream(_tokens_from_stdin(), mcp_config=config)
    if not findings:
        return CLEAN
    report(findings)
    return REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
