"""WHY THIS MODULE EXISTS

`intake/document_publish.py` holds the discipline of publishing one section of
one document — scope, receipts, readback, one record per consequence — and
deliberately knows nothing about how a document is reached. This is the other
half: the surface adapter that reaches one.

It is here rather than inside the publish module because the separation is the
point. `space/resolver.py` already carries the lesson in its own header: a
kernel primitive that learns one tenant's cloud paths stops being a kernel
primitive. So the rules live in `intake/`, and the only file that knows what
`lark-cli` is, is this one.

WHAT IT REFUSES TO DO

- **It holds no credential and reads none.** It runs an already-authenticated
  CLI and passes through what that CLI returns. Nothing here reads a token, a
  cookie, or the CLI's own config.
- **It does not decide anything.** It does not ask whether a write is allowed,
  whether a revision is current, or whether a section may be touched — all of
  that is the caller's, and a decision made here would be a decision invisible
  to the module that documents the decisions.
- **It does not invent an audience.** If the membership read does not work, the
  audience is reported as `None` — unobserved — and never as an empty list,
  because an empty list means "nobody" and `None` means "we did not look".
- **It does not fall back to Markdown.** The XML the service returns is read as
  XML; a block element whose type is not in the modelled set keeps its own tag
  as its type, so it is refused downstream rather than flattened here.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree
from pathlib import Path

SCHEMA = "serveros/document-transport.v1"

#: The CLI this adapter drives. Named rather than assumed on PATH so a host
#: without it fails with a sentence instead of a `FileNotFoundError` traceback.
LARK_CLI = "lark-cli"

#: An explicit answer for a node that keeps the CLI somewhere the list below has
#: never heard of. That list is a list of *this* node's conventions, and a node
#: is entitled to a convention this file does not know; naming it once in the
#: environment is how a node says so without teaching this file about itself.
CLI_OVERRIDE_ENV = "SERVEROS_LARK_CLI"

#: Where a user-level install of `lark-cli` is, on a node shaped like this one.
#: Each entry is a convention that already exists elsewhere in this tree rather
#: than a guess: `~/.local/bin` is the prefix `scripts/release.py` installs this
#: node's own launchers into (`--bin-dir` defaults to it) and the directory
#: `runtime/fs_hygiene.py` describes as where "user-level installs put their
#: executables"; `~/.local/lib/node_modules/@larksuite/cli/bin/` is npm's layout
#: for the same tool, and the file there is the CLI itself rather than the
#: wrapper that re-executes it, so it is the entry that does not also need
#: `node`.
CLI_FALLBACKS = (
    ".local/bin",
    ".local/lib/node_modules/@larksuite/cli/bin",
)


def _cli_candidates(home: Path) -> tuple[Path, ...]:
    """`CLI_FALLBACKS`, as paths under `home`, most-preferred first."""
    return tuple(home / part / LARK_CLI for part in CLI_FALLBACKS)


def resolve_cli(*, environ=None, home=None, find=None) -> str:
    """The file to execute for `LARK_CLI`, or the bare name if there is none.

    `PATH` is consulted FIRST, and that order is the safety argument: on a host
    where the bare name already resolved, this returns exactly the binary that
    host ran before this function existed. A fix that silently changed which
    `lark-cli` a working node used would be a second defect wearing this one's
    coat.

    It is here because `PATH` is a property of the CALLER, and the caller is a
    systemd user unit, which is not a login shell. Measured on this node,
    2026-10-06 — `command -v lark-cli` inside `systemd-run --user` prints
    nothing, and `systemctl --user show-environment` gives a `PATH` with no
    `~/.local/bin` in it, while the CLI is installed there and runs at 1.0.96
    from any shell. `serveros-evening-alignment` therefore exited 2 every night
    saying "nothing was attempted", and the first evening it had a recipient it
    would have refused at the send step with "lark-cli is not on this host" —
    about a host that has it.

    The sibling unit `runtime/serveros-alert-sync.service` carries the
    measurement this repairs: no `PATH=` is needed, it says, because ExecStart is
    absolute and `lark-cli` "lives in /usr/local/bin". Both halves are true of
    the program that unit starts and neither is true of the CLI that program
    then runs — on this node `/usr/local/bin/lark-cli` does not exist and
    `/usr/local/bin` is not writable by the unit's user. A unit whose ExecStart
    is absolute needs no `PATH=` for itself and every `PATH=` for what it spawns.
    """
    env = os.environ if environ is None else environ
    override = (env.get(CLI_OVERRIDE_ENV) or "").strip()
    if override:
        return override
    finder = shutil.which if find is None else find
    found = finder(LARK_CLI)
    if found:
        return found
    base = Path(home) if home is not None else Path(env.get("HOME") or Path.home())
    for candidate in _cli_candidates(base):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return LARK_CLI


def _cli_resolved(argv: list[str]) -> list[str]:
    """`lark-cli` → this node's file; anything else passed through untouched.

    A caller that already named a file has decided, and this does not overrule
    it. The substitution is here rather than at each call site because the call
    sites are the thing that would drift — this is the one function every
    `lark-cli` invocation in this tree goes through, including the ones
    `runtime/message_transport.py` borrows from it.
    """
    if argv and argv[0] == LARK_CLI:
        return [resolve_cli(), *argv[1:]]
    return argv

#: DocxXML tag → the block type the publish module reasons about. A tag not in
#: here keeps its own name as its type, which is how an unmodelled block becomes
#: a refusal instead of a silent flattening.
BLOCK_TYPES = {
    "title": "page",
    "p": "text",
    "h1": "heading1", "h2": "heading2", "h3": "heading3", "h4": "heading4",
    "h5": "heading5", "h6": "heading6", "h7": "heading7", "h8": "heading8",
    "h9": "heading9",
    "ul": "bullet", "ol": "ordered", "li": "text",
    "code": "code", "quote": "quote", "todo": "todo", "divider": "divider",
    "table": "table", "img": "image", "image": "image", "grid": "grid",
    "whiteboard": "whiteboard", "iframe": "iframe", "file": "file",
    "cite": "text", "callout": "callout",
}

#: Elements that only ever appear inside a block and are not blocks themselves.
INLINE_TAGS = ("b", "i", "u", "s", "span", "a", "cite", "code", "em", "strong",
               "del", "mark", "mention", "text_run", "equation", "br")


class TransportFault(Exception):
    """The CLI could not be run, or answered with a failure. Nothing implied."""


def run(argv: list[str], *, timeout: int = 120, cwd: str | None = None) -> dict:
    """One CLI call, its JSON answer, or a fault that carries the CLI's words.

    The CLI's own `ok`/`error` envelope is honoured rather than the exit code
    alone: a call can exit zero and still have failed, and reading only the exit
    code is how "the document was not touched" becomes "the document was
    updated".

    `argv[0]` is resolved here rather than left to `PATH` — see `resolve_cli` —
    so that "on PATH" means what this node has and not what the caller's
    environment happens to contain. That distinction is the whole reason this
    adapter had a defect: its callers are often systemd units, which are not
    login shells. An `argv` that already names a file is passed through.
    """
    argv = _cli_resolved(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd)
    except FileNotFoundError as exc:
        raise TransportFault(f"{argv[0]} is not on this host: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TransportFault(
            f"{' '.join(argv[:3])} … did not answer within {timeout}s. Whether it "
            f"took effect is not known from here") from exc

    try:
        answer = json.loads(proc.stdout)
    except ValueError:
        raise TransportFault(
            f"{' '.join(argv[:3])} … did not answer with JSON "
            f"(exit {proc.returncode}): {(proc.stderr or proc.stdout)[:400]}")
    if not isinstance(answer, dict):
        raise TransportFault(f"the CLI answered a {type(answer).__name__}, not a mapping")
    if answer.get("ok") is False:
        raise TransportFault(f"the CLI refused: {json.dumps(answer.get('error') or answer)[:400]}")
    return answer


# ────────────────────────────────────────────────────────────────────────────
# the document's own block model
# ────────────────────────────────────────────────────────────────────────────

def parse_blocks(xml: str) -> list[dict]:
    """The document as a flat, ordered list of blocks that carry an `id`.

    Only elements with an id are blocks: an element without one has no name to
    patch it by, and inventing a positional name for it is how a patch lands on
    the wrong paragraph after somebody else's insert.
    """
    root = ElementTree.fromstring(f"<document>{xml}</document>")
    blocks: list[dict] = []

    def walk(element) -> None:
        for child in element:
            if not isinstance(child.tag, str):
                continue
            tag = child.tag
            if tag in INLINE_TAGS:
                continue
            block_id = child.get("id")
            if block_id:
                blocks.append({"id": block_id,
                               "type": BLOCK_TYPES.get(tag, tag),
                               "text": _text(child)})
            walk(child)

    walk(root)
    return blocks


def _text(element) -> str:
    """The block's visible text, with inline containers flattened."""
    return "".join(element.itertext())


# ────────────────────────────────────────────────────────────────────────────
# the reads
# ────────────────────────────────────────────────────────────────────────────

def fetch(url: str, scope: str | None = None) -> dict:
    """The document as the publish module's `fetch` wants it.

    `audience` is `None` when the membership read did not work. That is not the
    same as an empty list, and the publish module reports the difference: an
    unobserved audience is a check that did not happen, not a check that passed.
    """
    answer = run([LARK_CLI, "docs", "+fetch", "--doc", url, "--detail", "with-ids"])
    document = (answer.get("data") or {}).get("document") or {}
    content = document.get("content")
    if content is None:
        raise TransportFault("the CLI's answer carried no document content")
    return {"revision": document.get("revision_id"),
            "blocks": parse_blocks(content),
            "audience": audience(url)}


def audience(url: str) -> list[str] | None:
    """Who can read this document, or `None` if that could not be read.

    Reproduced as `name:perm` rather than as identifiers. What the caller needs
    is whether the set changed across a write, and a permission level is part of
    that answer; the open id is not, and it is the one part of the answer that
    has no business travelling further than it has to. A member with no name is
    identified by a short digest so the set is still comparable.

    Read on every fetch rather than cached: the publish module compares the
    fingerprint before and after a write, and a cached answer would compare the
    write against itself.
    """
    try:
        answer = run([LARK_CLI, "drive", "+member-list", "--token", url,
                      "--fields", "*"])
    except TransportFault:
        return None
    members = (answer.get("data") or {}).get("items")
    if members is None:
        return None
    seen = []
    for member in members:
        if not isinstance(member, dict):
            continue
        who = str(member.get("name") or "")
        if not who:
            who = "id-" + hashlib.sha256(
                str(member.get("member_id") or "").encode("utf-8")
            ).hexdigest()[:8]
        seen.append(f"{who}:{member.get('perm') or 'unknown'}")
    return sorted(seen)


# ────────────────────────────────────────────────────────────────────────────
# the writes
# ────────────────────────────────────────────────────────────────────────────

def body_writer(url: str):
    """Write one section's body, with the service doing the currency check.

    Two shapes, because a section with no body is a section with nothing to
    replace: a body that exists is replaced as a range, and an empty one is
    filled by inserting after the heading. Choosing the wrong one is not a
    near miss — `block_replace` over an empty range deletes the heading, which
    moves the anchor the next patch looks for.

    `revision_id` is passed through on both, so a write built on a revision that
    is no longer current is refused by the service as well as by the caller. Two
    checks, and the one that matters is the one that is not local.
    """

    def write(document: str, section: dict, content: str, revision: int) -> dict:
        heading_id = section.get("heading_id")
        body_ids = section.get("body_ids") or []
        if not heading_id:
            raise TransportFault("the section has no heading block to anchor to")
        argv = [LARK_CLI, "docs", "+update", "--doc", url, "--content", content,
                "--revision-id", str(revision)]
        if body_ids:
            argv += ["--command", "block_replace",
                     "--start-block-id", str(body_ids[0]),
                     "--end-block-id", str(body_ids[-1])]
        else:
            argv += ["--command", "block_insert_after",
                     "--block-id", str(heading_id)]
        answer = run(argv)
        return {"document": answer.get("data") or {},
                "shape": "block_replace" if body_ids else "block_insert_after"}

    return write


def media_writer(url: str):
    """Insert one local file at the end of the document.

    Deliberately the crudest available verb: a media effect that can decide
    where to place things is a second patch path, and a second patch path is a
    second scope nobody checked.

    The call is made **from the file's own directory**. The CLI reads a local
    path only if it is under its working directory, `/tmp`, or `~/files`, and
    this host's policy puts temp files under the artifacts root rather than
    `/tmp` and does not create `~/files` at all — so the default working
    directory would refuse every media file this path is allowed to send. The
    narrowing is not lost by moving: the file being sent is the caller's
    choice, and a caller who can name a path can name one in that directory.
    """

    def write(document: str, request: dict) -> dict:
        results = []
        for item in request.get("media") or []:
            source = Path(item.get("path") if isinstance(item, dict) else item)
            if not source.is_file():
                raise TransportFault(f"media source not found: {source}")
            argv = [LARK_CLI, "docs", "+media-insert", "--doc", url,
                    "--file", str(source),
                    "--type", (item.get("type") if isinstance(item, dict)
                               else None) or "image"]
            if isinstance(item, dict) and item.get("caption"):
                argv += ["--caption", str(item["caption"])]
            answer = run(argv, timeout=600, cwd=str(source.parent))
            results.append({"file": source.name,
                            "document": answer.get("data") or {}})
        return {"inserted": results}

    return write


def transport(url: str, *, with_media: bool = False) -> dict:
    """Everything the publish module asks the caller to supply."""
    writers = {"body": body_writer(url)}
    if with_media:
        writers["media"] = media_writer(url)
    return {"fetch": lambda document, scope=None: fetch(url, scope),
            "writers": writers}
