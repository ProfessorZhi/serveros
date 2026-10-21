"""One definition of "this carries a credential", for the two places that ask.

WHY THIS MODULE EXISTS, AND WHY IT LIVES IN `runtime/`
------------------------------------------------------
Two consumers need the same two answers, and they run in different places:

  * `scripts/scan_secrets.py` -- the §28 release gate. It runs from the
    REPOSITORY, over a tree that is about to be shipped.
  * `runtime/launcher_guard.py` -- the preflight a launcher runs before it
    hands an agent client a command line. It runs from an INSTALLED REVISION,
    where `scripts/` does not exist at all: `scripts/build.py`'s
    `CORE_MODULES` ships `runtime`, `fleet`, `mcp` and their neighbours, and
    `scripts` is not among them.

That asymmetry is why the definition cannot live in the scanner and be
imported from there. It is stated as a placement rule rather than discovered
in production, which is the difference between this and the `intake` drop the
builder's own comment records — a module that worked from a checkout and
raised `FileNotFoundError` on every installed node.

The rule the arrangement keeps is `fleet/controller.py`'s, written for its own
list and true of this one: "Reuses the transport contract's list rather than
keeping a second one: two lists of forbidden secret names drift, and the one
that drifts is the one nobody re-read." So this module is the single copy. The
scanner imports it; the guard imports it; neither keeps a private one.

WHAT IT IS MADE OF
------------------
Two independent questions, each answered from a source that already existed:

  * `SHAPES` -- VALUES. The prefix-bearing shapes the release gate has always
    used, moved here unchanged. A shape is something only a credential
    produces: `ghp_` and thirty-six more characters, an `AKIA` id, a PEM
    header. Bare high entropy is deliberately NOT a shape, because this
    product is largely made of content-addressed digests and every one of them
    would fire.
  * `forbidden_field_names()` -- KEYS. Read from
    `fleet/transport-contract.yaml#transport.forbidden_binding_fields`, the
    same contract `fleet/transport.py` derives `FORBIDDEN_BINDING_KEYS` from
    and `fleet/node.py` unions into its own set. A name says where a
    credential would live; that is a structural fact about a document and not
    a guess about its text.

`ARGV_ONLY_SHAPES` is the one addition, and it is here rather than in the gate
on purpose. `Authorization: Bearer <token>` is a HEADER, and a release tree is
allowed to discuss headers -- `intake/support-contract.yaml` names one in
order to redact it, and a gate that refused its own documentation would be
turned off. A command line handed to an agent client is not allowed to carry
one, and the launcher is the only caller that asks that narrower question.

EVERY FUNCTION RETURNS NAMES -- a rule's name, a dotted key path. Never a
value, and never a substring of one. A refusal that quoted what it refused
would move the credential out of the command line and into the journal, which
is the leak it was invoked to prevent.
"""
from __future__ import annotations

import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent

#: Values only a credential produces. Moved here from `scripts/scan_secrets.py`
#: unchanged, and that file now imports THIS tuple: one definition with two
#: readers rather than two definitions with one meaning.
SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key",
     re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("github-token",
     re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("aws-access-key-id",
     re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token",
     re.compile(r"\bxox[baprs]-[A-Za-z0-9][A-Za-z0-9-]{9,}\b")),
    ("anthropic-or-openai-key",
     # `sk-` followed by enough entropy to be a key rather than a hyphenated
     # word. 32 is below every vendor's real length and above any English word.
     re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
)

#: The header forms. See the module docstring for why these are separate from
#: `SHAPES`: the release gate may read prose about headers, a command line may
#: not carry one.
#:
#: The shape is anchored so that the WORD does not fire. The lookahead requires
#: the run to contain at least one character an English word does not reach for
#: in that position -- a digit, or one of `. _ ~ + / = -` -- so `Bearer tokens`
#: and `Bearer authentication`, which is how this repository writes the phrase
#: in its prose, are not credentials, while a JWT, a base64 blob and a
#: prefix-bearing key all are.
#:
#: The first version of this pattern wrote that requirement as a NEGATIVE
#: lookahead, `(?![A-Za-z]+\b)`, and a test caught it within the hour: `.` is a
#: word boundary, so `[A-Za-z]+` matched `eyJhbGciOi` and stopped there, and the
#: negative lookahead therefore blocked the JWT it existed to catch. Refusing
#: nothing looks exactly like refusing everything, from the outside.
ARGV_ONLY_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bearer-credential",
     re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])"
                r"[A-Za-z0-9._~+/=-]{8,}\b")),
)

def contract_path() -> Path:
    """Where the forbidden field names are declared.

    The contract and not a second list, and the contract rather than
    `fleet/transport.py`: importing the module would execute the whole fleet
    transport at every launch to read one tuple, and `fleet/` ships in the same
    revision `runtime/` does, so the file is there either way.
    """
    return _REPO / "fleet" / "transport-contract.yaml"


def forbidden_field_names() -> frozenset[str]:
    """Field names a credential may not be carried under.

    Lower-cased, because `Authorization` and `authorization` are one field and
    a check that only caught the spelling somebody happened to write is the
    check that passes review and misses the leak.
    """
    import yaml
    contract = yaml.safe_load(contract_path().read_text(encoding="utf-8"))
    declared = contract["transport"]["forbidden_binding_fields"]
    return frozenset(str(name).lower() for name in declared)


def shape_names_in(text: str, *, argv: bool = False) -> list[str]:
    """Names of the shapes `text` matches. Never the matched text.

    `argv` adds `ARGV_ONLY_SHAPES`. It is a parameter rather than a second
    function so that a caller cannot accidentally get the weaker set while
    believing it got the stronger one.
    """
    rules = SHAPES + ARGV_ONLY_SHAPES if argv else SHAPES
    return [name for name, pattern in rules if pattern.search(text)]


def key_paths_in(node, trail: str = "") -> list[str]:
    """Dotted paths of KEYS whose name says a credential lives under them.

    Recursive, dictionaries and lists both, because a config nests: the
    interesting key in an MCP config is four levels down
    (`mcpServers.<name>.env.AUTHORIZATION`) and a walk that stopped at the top
    would report a clean document.
    """
    names = forbidden_field_names()
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{trail}{key}"
            if str(key).lower() in names:
                found.append(path)
            found.extend(key_paths_in(value, f"{path}."))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(key_paths_in(item, f"{trail}[{index}]."))
    return found


def string_leaves_in(node, trail: str = "") -> list[tuple[str, str]]:
    """Every string value in a document, as `(dotted path, value)`.

    A value is returned to the CALLER and never to a message. The guard is the
    only caller, and it passes each one to `shape_names_in` and discards it.
    """
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(string_leaves_in(value, f"{trail}{key}."))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(string_leaves_in(item, f"{trail}[{index}]."))
    elif isinstance(node, str):
        found.append((trail.rstrip(".") or "<root>", node))
    return found
