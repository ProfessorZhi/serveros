#!/usr/bin/env python3
"""§16A/§16B · Filesystem and directory hygiene — where things ARE.

The question §16A asks is narrower than "is this tidy":

    can ServerOS detect that a file or a resource has been put in the wrong
    place?

Detection is the whole of it. Nothing here moves, renames, symlinks, chmods or
deletes anything, and §16B forbids the automatic path from doing so even where
the answer is obvious. A classifier that could act would be asked to act on the
strength of a name, which is the failure mode §17 spends its whole length
refusing one level down.

Several roots, grouped by the directory they resolve to
-------------------------------------------------------
HOME and DATA_ROOT are classified in one pass because the explanation for an
entry in one of them is often a fact in the other. `$DATA_ROOT/.vscode-server`
is a multi-gigabyte tree at DATA_ROOT's top level whose name is not in
`DATA_ROOT_TOP_LEVEL` — undeclared, apparently drift. It is not: the same
entry seen from HOME is a symlink pointing at it, which is a deliberate
relocation of a tool cache off HOME. A classifier that looked at DATA_ROOT alone
would report that as misplaced on every run, and a report that is wrong about
the same thing every run is a report nobody reads. So the symlink relation
between the roots is a first-class input here, not a footnote.

The root NAMES, though, are not the roots. This module used to scan `home` and
`data` as two directories, and on a host where `HOME == DATA_ROOT` — which is
this host, and is what the machine policy states — that meant one directory
scanned twice. The second pass had no vocabulary for `home`, so the same
entries came out once as UNKNOWN and once against DATA_ROOT's declared set;
`find_duplicate_semantic_roots` saw DATA_ROOT's twelve declared names under a
root that did not own them and reported twelve duplicates of a root with
itself; and §16B's three HOME exceptions, being found by two passes, counted
six times. Every one of those was an artefact of evaluating a name instead of a
place.

So the names are resolved to directories and grouped, and each group is
evaluated once. `paths.group_physical_roots` performs the grouping and
`paths.CO_BINDING_ROOT_GROUPS` declares which names may share a directory: two
roles answered by one place is a legitimate binding (§25 — a role is what
something IS, a root is where it is), while two roots merely comparing equal is
not automatically legitimate and is reported as `co_binding: false`. Rows carry
`bound_roles` and `alias_of` so a reader can see which names are one place
rather than two.

`TOOL_OWNED_DOTDIR` is a DECLARATION, not a dotfile rule
--------------------------------------------------------
Where a tool keeps its own state, the tool resolves the location — by
convention, by an environment variable with a documented default, or by a
published specification. Those entries are not drift and this module no longer
calls them so. What it must never do is decide that from the leading dot:
`name.startswith(".")` would exempt every dotfile that ever appears, including
the one invented tomorrow, silently and for good. Membership in
`TOOL_OWNED_DOTDIRS` requires a named owner, a named provider and the evidence
that establishes it, and a dotdir no declaration covers stays UNKNOWN. The
class is placement only: entries carry their protection attribute through
untouched, because "this is where the tool keeps its state" is not a statement
about whether anything may be read, moved or deleted.

`RETENTION_REQUIRED` is in the vocabulary because §16A names it, and it is NOT
a synonym for LEGACY
----------------------------------------------------------------------------
LEGACY here means exactly one thing: the entry's name is in
`paths.FORBIDDEN_SUFFIXES`, the near-canonical names that outlive the task that
created them, and whose disposition is "migrate the contents and retire the
path". RETENTION_REQUIRED means the opposite: the entry is correctly placed, it
was produced on purpose, and its consumers are real — `.prev-<version>` beside
a `current` pointer under `runtime-assets/hwz-server-os` is the rollback target
of the release that superseded it. Collapsing the two would label the rollback
target as debris, which is how a rollback target gets deleted.

What is NOT established here, and is said rather than implied
------------------------------------------------------------
`paths.py` declares no top-level vocabulary for HOME. It declares closed sets
for DATA_ROOT, WORKSPACE_ROOT and `state/serveros`, and for HOME only the
`~/.serveros` allow-list and a refusal of `~/files`. Where HOME is its own
directory, its entries are therefore largely unchecked by construction and an
entry there cannot be MISPLACED against a list that does not exist — this
module reports that gap rather than inventing a list to close it, because a
list invented here would be a second statement of HOME's shape authored by the
module that is supposed to be checking it.

Where HOME and DATA_ROOT resolve to ONE directory — this host — the gap
disappears for the reason the defect did: the directory is the DATA_ROOT, it
has a declared closed top level, and that is the vocabulary applied to it. The
report says so per role: `coverage['home']['declared_top_level']` is empty on a
host where HOME is its own directory and DATA_ROOT's twelve names on a host
where it is not, and either way the field states the rule that was actually
applied rather than the rule this module would have preferred.

There is likewise no per-directory size producer in this product, so `sizes` is
an INPUT. When none is supplied the row says `size_unmeasured` and the
OVERSIZED question is left open rather than answered with a zero — the same
distinction `inventory.py` makes between a measured zero and an unmeasured one.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths = _load("serveros_runtime_paths", "paths.py")

# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

CANONICAL = "CANONICAL"
ALLOWED_EXCEPTION = "ALLOWED_EXCEPTION"
TOOL_OWNED_DOTDIR = "TOOL_OWNED_DOTDIR"
LEGACY = "LEGACY"
MISPLACED = "MISPLACED"
OVERSIZED = "OVERSIZED"
DUPLICATE_SEMANTIC_ROOT = "DUPLICATE_SEMANTIC_ROOT"
RETENTION_REQUIRED = "RETENTION_REQUIRED"
UNKNOWN = "UNKNOWN"

CLASSES = (CANONICAL, ALLOWED_EXCEPTION, TOOL_OWNED_DOTDIR, LEGACY, MISPLACED,
           OVERSIZED, DUPLICATE_SEMANTIC_ROOT, RETENTION_REQUIRED, UNKNOWN)

# Classes this round may never act on. All of them, listed explicitly so that
# adding one forces the decision rather than inheriting it. §22 and §16B both
# forbid the move; this is the same fact written where a reader will look for
# it.
NEVER_ACTIONABLE = frozenset(CLASSES)

# ---------------------------------------------------------------------------
# The three HOME exceptions (§16B, by name)
#
# Not "the dotfiles we happened to find". §16B names exactly three tool roots
# that must be excluded from any HOME cleanup consideration, and each is a root
# a tool writes into by convention and that moving would break in a way the
# tool reports as its own missing state. The mapping carries WHY, because a
# bare allow-list is the thing that gets widened by whoever hits it next.
# ---------------------------------------------------------------------------

HOME_ALLOWED_EXCEPTIONS = {
    ".claude": ("Claude Code's own user root: settings, plugins, session "
                "archives and the memory directory. Relocating it under "
                "DATA_ROOT would be a change to a tool's contract, not a tidy-up"),
    ".codex": ("Codex's user root. Same shape as `~/.claude`, same reason: the "
               "tool owns the location and reports its absence as NODE_"
               "UNBOUND-style state rather than as a missing file"),
    ".config": ("The XDG config root, named by a published specification rather "
                "than by this product. Half the tools on this host resolve "
                "`$XDG_CONFIG_HOME` or default to it"),
}

# ---------------------------------------------------------------------------
# Tool-owned dot directories
# ---------------------------------------------------------------------------
#
# `~/.cache`, `~/.npm`, `~/.ssh` are not drift. They are where a tool keeps its
# own state, and the tool resolves the location itself — by convention, by an
# environment variable with a documented default, or by a published
# specification (`$XDG_CACHE_HOME`, `$XDG_DATA_HOME`, `$GNUPGHOME`, `~/.ssh`).
# A classifier that called them MISPLACED would be describing its own
# vocabulary rather than the filesystem.
#
# What this is NOT: `if name.startswith(".")`. That rule would pass every
# dotfile that ever appears, which is the exact failure this round exists to
# remove — a `.foo` invented tomorrow would be governance-exempt on sight,
# silently and for good. Membership here is a DECLARATION: a named owner, a
# named provider, the evidence that establishes it, and the reason the location
# is the tool's to choose rather than this product's. A dotdir this table does
# not name stays UNKNOWN, and
# `test_an_undeclared_dotdir_is_not_excused_by_being_a_dotdir` holds that line.
#
# `protection` is an ORTHOGONAL attribute carried through, never decided here.
# Placement says where a thing is; it says nothing about whether the thing may
# be read, moved or deleted, and the protection registry answers that question
# on its own. The field exists so that a reader of a placement row can see the
# row did NOT clear the entry — §E of this round's criteria — and so that
# nobody reads "this is where the tool keeps state" as "this is safe to touch".
#
# Protection is set from an OBSERVED artifact, never from convention. A tool
# whose directory this module listed without seeing a key, token, secret or
# device-binding file in it is UNPROTECTED here, and its `why` says what was
# observed. Guessing upward would make SECRET mean "somebody was cautious" and
# cost the mark the only thing it is worth.
#
# §16B's three (`.claude`, `.codex`, `.config`) are deliberately ABSENT. They
# already have a class of their own with its own reasons, and a second, weaker
# claim about the same fact is how two answers start disagreeing.
SECRET = "SECRET"
IDENTITY = "IDENTITY"
UNPROTECTED = "UNPROTECTED"

TOOL_OWNED_DOTDIRS = {
    ".bash_completion.d": {
        "owner": "the shell completion convention",
        "provider": "whichever CLI installs a per-user completion fragment; "
                    "`opencli` is the one on this host",
        "evidence": "holds exactly one entry, `opencli` — a completion "
                    "fragment, which a state directory would not resemble",
        "protection": UNPROTECTED,
        "why": "`/etc/bash_completion.d` is the system location and "
               "`~/.bash_completion.d` is its per-user counterpart, sourced by "
               "the completion loader; the loader names the directory, so "
               "relocating it disables completion silently instead of failing "
               "loudly",
    },
    ".botmux": {
        "owner": "Botmux",
        "provider": "the `botmux` plugin, which writes its registry and "
                    "dashboard state here",
        "evidence": "`bots.json`, `bots/`, `claude-plugin/`, `bin/`, and the "
                    "dashboard files beside them",
        "protection": SECRET,
        "protection_basis": "`.dashboard-token` and `.dashboard-secret` are "
                            "credential files for the plugin's own dashboard; "
                            "names observed, contents never read",
        "why": "one tool's complete user root — registry, plugins, fonts, "
               "logs, usage — named after the tool. Nothing here is a path "
               "this product chose or that this product may choose",
    },
    ".cache": {
        "owner": "the XDG cache root",
        "provider": "every tool honouring `$XDG_CACHE_HOME`; on this host at "
                    "least `claude-cli-nodejs` and `copilot`",
        "evidence": "`claude-cli-nodejs/` and `copilot/` — tool-named cache "
                    "subdirectories",
        "protection": UNPROTECTED,
        "why": "named by the XDG Base Directory specification rather than by "
               "this product, and by definition disposable state a tool may "
               "recreate. Its contents are NOT all cache — the listing carries "
               "a set of loose `*.sh` scratch scripts — and that is a separate "
               "finding about what was written INTO this directory. It is "
               "recorded here because the two would otherwise be confused, and "
               "it is not a reason to relocate a root the tools resolve "
               "themselves",
    },
    ".claude-server-commander": {
        "owner": "claude-server-commander",
        "provider": "that server's own runtime",
        "evidence": "`config.json`, `feature-flags.json`, "
                    "`claude_tool_call.log`, `tool-history.jsonl`, "
                    "`puppeteer-cache/`",
        "protection": UNPROTECTED,
        "why": "the tool names the directory after itself and keeps config, "
               "request history and its browser cache in it together. A name "
               "the tool chose for itself is not a path this product may "
               "rename under it",
    },
    ".copilot": {
        "owner": "GitHub Copilot CLI",
        "provider": "the Copilot CLI",
        "evidence": "`config.json`, `ide/`, `logs/`",
        "protection": UNPROTECTED,
        "why": "the same shape as `~/.claude` and `~/.codex`: a CLI's user "
               "root, resolved by the CLI, whose absence it reports as its own "
               "unconfigured state rather than as a missing file",
    },
    ".desktop-commander-device": {
        "owner": "desktop-commander",
        "provider": "that tool's device adapter",
        "evidence": "three entries, and all three are `device.json` or a "
                    "`*.beijing-20260918` / `*.beijing-20260919` sidecar of it "
                    "left by the migration — a directory whose entire contents "
                    "are one device binding, twice copied",
        "protection": IDENTITY,
        "protection_basis": "`device.json` is a device binding; §41 keeps "
                            "identity out of placement decisions, and this row "
                            "does not rule on it",
        "why": "the directory holds one file, and the file is this host's "
               "binding to the tool rather than a place a document lives. The "
               "name says `-device` because the tool resolved a per-device "
               "location, which is the tool's decision to make",
    },
    ".docker": {
        "owner": "the Docker CLI",
        "provider": "`docker login` and buildx",
        "evidence": "`buildx/`, `.token_seed`, `.token_seed.lock`",
        "protection": SECRET,
        "protection_basis": "`.token_seed` is registry-auth material and "
                            "`.token_seed.lock` is its lock; moving or "
                            "rotating these is a credential operation, which "
                            "this module may not perform",
        "why": "`$DOCKER_CONFIG` defaults here and the CLI reads it before any "
               "registry operation; the directory is the tool's, and it is the "
               "one this report can only describe",
    },
    ".dotnet": {
        "owner": ".NET SDK",
        "provider": "the `dotnet-install` script and the SDK",
        "evidence": "one subdirectory, `corefx/`, which is the SDK's own "
                    "name for its assembly cache and not a name this product "
                    "or a migration produced",
        "protection": UNPROTECTED,
        "why": "the install script and `DOTNET_CLI_HOME` both default here on "
               "Linux, and the SDK resolves it without being told",
    },
    ".gnupg": {
        "owner": "GnuPG",
        "provider": "`gpg`",
        "evidence": "`pubring.kbx`, `trustdb.gpg`",
        "protection": SECRET,
        "protection_basis": "the public keyring and the web-of-trust database "
                            "are the directory's whole purpose; `GNUPGHOME` "
                            "defaults here, so relocating it orphans every "
                            "existing signature check rather than reporting an "
                            "error",
        "why": "a published default of the tool itself. This is the shape the "
               "module must not call drift: nothing here was placed by this "
               "product, and nothing here is this product's to move",
    },
    ".hermes": {
        "owner": "Hermes, the agent runtime",
        "provider": "the Hermes gateway",
        "evidence": "`config.yaml`, `gateway_state.json`, `sessions/`, `cron/`, "
                    "`memories/`, `skills/`, `state.db`, `pairing/`",
        "protection": SECRET,
        "protection_basis": "`pairing/` holds paired-device trust material and "
                            "`gateway_state.json` the gateway's own state; "
                            "names observed, contents never read",
        "why": "the complete user root of a long-running service — config, "
               "session store, scheduler, memory and skills — all resolved by "
               "the service at startup. A directory this tool reconstructs its "
               "identity from is not a directory this product relocates",
    },
    ".lark-cli": {
        "owner": "the Lark/Feishu CLI",
        "provider": "that CLI's own installer and update check",
        "evidence": "`config.json`, `cache/`, `locks/`, `logs/`, "
                    "`update-state.json`",
        "protection": UNPROTECTED,
        "why": "a CLI's user root with the usual four parts — config, cache, "
               "locks, logs — plus its update state. No credential artifact was "
               "observed in the listing and the file contents were not read, "
               "so the protection mark is what was seen rather than what the "
               "tool's name might suggest",
    },
    ".local": {
        "owner": "the XDG data root and the user install prefix",
        "provider": "`pip install --user`, `cargo install`, `uv tool install`, "
                    "and every tool honouring `$XDG_DATA_HOME`",
        "evidence": "`bin/`, `lib/`, `share/`, `state/`",
        "protection": UNPROTECTED,
        "why": "`~/.local/bin` is on this host's PATH and is where user-level "
               "installs put their executables, and `share/`/`state/` are the "
               "XDG defaults. Relocating the directory does not move the PATH "
               "entry, so the tools would stop being found while still being "
               "installed",
    },
    ".npm": {
        "owner": "npm",
        "provider": "the `npm` CLI",
        "evidence": "`_cacache/`, `_logs/`, `_npx/`",
        "protection": UNPROTECTED,
        "why": "npm's documented cache and npx staging area. npm computes the "
               "location itself and there is no configuration on this host "
               "pointing it elsewhere, so a report that called it misplaced "
               "would be describing this module's vocabulary",
    },
    ".opencli": {
        "owner": "OpenCLI",
        "provider": "the `opencli` installer and its adapter set",
        "evidence": "`package.json`, `clis/`, `node_modules/`, "
                    "`adapter-manifest.json`, `cache/`, `locks/`",
        "protection": SECRET,
        "protection_basis": "`spotify.env` is an environment file holding a "
                            "service credential; name observed, contents never "
                            "read",
        "why": "a tool's own installation root — it owns an adapter manifest, "
               "a CLI collection and a node_modules beside its cache. A "
               "directory holding an installation is not state this product "
               "created, and the credential file inside it is why the row "
               "carries a protection mark a placement verdict cannot clear",
    },
    ".playwright-cli": {
        "owner": "the Playwright CLI",
        "provider": "`playwright-cli` browser sessions",
        "evidence": "`console-*.log` and `page-*.yml` at the top level",
        "protection": UNPROTECTED,
        "why": "session output the tool writes where it runs. The entries are "
               "the CLI's own dumps and are dated rather than named, which is "
               "what a per-session output directory looks like",
    },
    ".playwright-mcp": {
        "owner": "the Playwright MCP server",
        "provider": "the `playwright-mcp` server's sessions",
        "evidence": "223 entries, all `console-*.log` and `page-*.yml`",
        "protection": UNPROTECTED,
        "why": "the MCP server's per-session output directory, the same shape "
               "as `.playwright-cli` for a different consumer. Large and "
               "entirely reproducible, which makes it a retention question and "
               "not a placement one",
    },
    ".secrets": {
        "owner": "the credential vault",
        "provider": "the operator, who stores credentials here by policy",
        "evidence": "`vault/`, holding per-service `.env` files",
        "protection": SECRET,
        "protection_basis": "the machine policy names this exact path as where "
                            "credentials live — `~/.secrets/vault/` — so the "
                            "protection here is declared rather than inferred",
        "why": "the one entry on this list whose owner is stated by policy "
               "instead of deduced from its contents. The policy states it as "
               "a LOCATION, which is why it is also a placement fact, and "
               "stating where it is does not make it this product's to move",
    },
    ".serveros": {
        "owner": "the ServerOS node-root contract",
        "provider": "`fleet/node.py`, which reads `NODE_FILE` and `TARGETS_FILE`",
        "evidence": "`node.yaml` and `targets.yaml` — exactly "
                    "`paths.HOME_DOT_SERVEROS_ALLOWED` minus `controller.yaml`, "
                    "which no host has",
        "protection": IDENTITY,
        "protection_basis": "§38/§41 make this root identity and binding only, "
                            "and `paths.admit` refuses any other write to it; "
                            "its contents ARE the identity, so placement and "
                            "protection cannot be separated for this one",
        "why": "this is the entry whose authority is a file in this repository "
               "rather than a convention: `fleet/node-root-contract.yaml` "
               "declares what may live here, `paths.VOCABULARIES['home']` "
               "transcribes it, and `paths.admit` enforces it on write. A "
               "directory whose shape this product declares and enforces is "
               "owned by the tool that reads it",
    },
    ".ssh": {
        "owner": "OpenSSH",
        "provider": "`ssh`, `ssh-keygen`, `ssh-agent`",
        "evidence": "`authorized_keys`, `config`, and several private keys with "
                    "their `.pub` siblings",
        "protection": IDENTITY,
        "protection_basis": "`~/.ssh` holds private keys. §E of this round's "
                            "criteria: the placement verdict must not lower the "
                            "protection, and a classifier in the business of "
                            "saying where things belong is exactly the one that "
                            "would",
        "why": "a published default of the client, whose config, known-hosts "
               "and keys the client resolves itself. This entry is on the list "
               "to make the list explicit, not because its ownership was ever "
               "in question",
    },
    ".vscode-server": {
        "owner": "VS Code Remote server",
        "provider": "the server the IDE downloads and runs on this host",
        "evidence": "`cli/`, `code-<commit>/` per version, `data/`, "
                    "`extensions/`, and per-session `*.token` / `*.pid` files",
        "protection": SECRET,
        "protection_basis": "the server writes a connection token per running "
                            "session; they are ephemeral, but they are "
                            "credentials while the server is up and this "
                            "module will not be the thing that decides they are "
                            "not",
        "why": "the server names the directory, keys it by its own version "
               "hash, and will re-download into it. It is also the entry most "
               "likely to be mistaken for drift on a host with an IDE "
               "attached, which is why it is declared here by name",
    },
}

# Dot directories this module LOOKED AT and declined to declare.
#
# A name absent from `TOOL_OWNED_DOTDIRS` is an unanswered question. A name
# here is the answer, and it is recorded because the next reader will otherwise
# re-open it — or, worse, add it to the registry to clear a count. Declining is
# a decision and it belongs in the code beside the declarations it declines to
# make, not only in a report, which is the artifact nobody reads when they are
# editing this table.
#
# These stay UNKNOWN. They are not MISPLACED either: MISPLACED asserts that
# this product declares a shape the entry violates, and for these the honest
# statement is that no shape has been established at all.
UNDECLARED_ON_PURPOSE = {
    ".ability-management": (
        "nothing on this node declares what owns `~/.ability-management`. "
        "`src/ability-management` is the canonical clone and this is not it — "
        "it holds `keys/`, `snapshots/` and no `.git`, so it is neither a "
        "checkout nor a declared install location. It is UNKNOWN rather than "
        "TOOL_OWNED_DOTDIR deliberately: a placement ruling made here would be "
        "a ruling about a key store this module has not read, and declaring the "
        "name tool-owned to clear a count would spend the declaration on the "
        "entry that can least afford it. The ruler is not widened to fit the "
        "entry"
    ),
}

# A retained sibling of a `current` pointer: `.prev-<version>`, `.build-<name>`.
# Matched by pattern rather than by listing versions, because the version
# changes every release and a list transcribed here would be stale by the next
# one — which is the defect `paths.py` records against its own HOME allow-list.
RETAINED_PATTERN = re.compile(r"^\.(prev|build)-")

# Reporting threshold, not policy. Named as a default so a caller can see what
# produced a verdict and pass its own; a threshold silently baked into a
# comparison is a number nobody can review.
DEFAULT_OVERSIZED_BYTES = 1 << 30  # 1 GiB

# Which root name is governed by which declared vocabulary, where one exists.
#
# `home` is deliberately ABSENT. `paths.VOCABULARIES["home"]` is the `~/.serveros`
# allow-list, NOT the shape of `~/` — using it here would classify `~/.bashrc`
# as a misplaced entry, which would be wrong and would be wrong loudly enough
# that somebody would widen the list to shut it up. The absence is the finding;
# see the module docstring.
ROOT_VOCABULARY = {
    "data": "DATA_ROOT_TOP_LEVEL",
    "workspace": "WORKSPACE_TOP_LEVEL",
    "state": "STATE_SERVEROS_TOP_LEVEL",
}

# The three roots whose top level is declared a closed set. `state` is declared
# closed in policy but `paths.admit` does not enforce it the way it enforces
# `data` and `workspace`; the report says which of the two it is, because "a
# rule exists and nothing checks it" is a different finding from "there is no
# rule".
ENFORCED_BY_ADMIT = {"data", "workspace"}

# Names that are absent from a declared vocabulary ON THE RECORD, with the
# reason already written down elsewhere. These are UNKNOWN rather than
# MISPLACED, and the distinction is not cosmetic: `paths.py` says of both of
# these that they are "unresolved rather than designed", so calling them
# misplaced would be this module overruling a decision the contract states it
# has not made yet. A classifier that promotes "awaiting a ruling" to "in the
# wrong place" manufactures a finding out of somebody else's open question.
#
# Read from `paths.py` rather than re-argued here: the tuple below names them
# because the contract names them, and the reason string is the contract's.
AWAITING_A_RULING = {
    "document-stewardship": (
        "`paths.py` records this as deliberately NOT in the state vocabulary: "
        "its producer exists only in an unmerged worktree. UNKNOWN rather than "
        "MISPLACED, because the contract states it has not decided"
    ),
    "migrations": (
        "`paths.py` records this as expired evidence that two declarations "
        "disagree about. UNKNOWN rather than MISPLACED for the same reason: "
        "the open question is which declaration wins, not where the directory "
        "belongs"
    ),
}


def _vocabulary(root_name: str) -> tuple[str, ...]:
    attribute = ROOT_VOCABULARY.get(root_name)
    return tuple(getattr(paths, attribute)) if attribute else ()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _scan(directory: Path) -> list[dict]:
    """One level, never following a symlink and never recursing.

    Recursion is where a hygiene check becomes a filesystem walk over a 6.4 GB
    tree, and the questions §16A asks — is this in the right place, is it
    duplicated, is it retained — are all answerable from the top level.
    """
    out = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError as exc:
        return [{"name": None, "error": str(exc)}]
    for entry in entries:
        is_link = entry.is_symlink()
        target = None
        if is_link:
            try:
                target = os.readlink(entry.path)
            except OSError:
                target = None
        out.append({
            "name": entry.name,
            "path": entry.path,
            "is_dir": entry.is_dir(follow_symlinks=False),
            "is_file": entry.is_file(follow_symlinks=False),
            "is_symlink": is_link,
            "link_target": target,
            "resolved": os.path.realpath(entry.path) if is_link else None,
        })
    return out


def symlink_relations(*, home: Path, data: Path) -> dict:
    """HOME entries that are symlinks into DATA_ROOT, by resolved target.

    This is the fact that turns an undeclared DATA_ROOT entry into an
    explained one. Returned as a mapping rather than checked inline so that the
    reason can be carried into the row: a classifier that silently accepted
    `.vscode-server` would leave the next reader unable to tell "explained" from
    "not noticed".
    """
    relations: dict[str, dict] = {}
    for entry in _scan(home):
        if not entry.get("is_symlink") or not entry.get("resolved"):
            continue
        resolved = Path(entry["resolved"])
        if resolved == data or data in resolved.parents:
            relations[str(resolved)] = {
                "home_entry": entry["name"],
                "home_path": entry["path"],
                "target": str(resolved),
                "relative": str(resolved.relative_to(data)) if resolved != data else "",
                "why": (
                    f"HOME carries `{entry['name']}` as a symlink to this path, "
                    f"so the entry here is a relocation this host made on "
                    f"purpose rather than a directory that drifted. The link is "
                    f"the declaration, and it lives in the other root — which "
                    f"is why the two roots are classified together"
                ),
            }
    return relations


# ---------------------------------------------------------------------------
# Classifying one entry
# ---------------------------------------------------------------------------


def classify_entry(entry: dict, *, root_name: str, vocabulary: tuple[str, ...],
                   relations: dict, sizes: dict, oversized_bytes: int,
                   duplicates: set, is_role_location: bool = False,
                   roles: tuple = (), evaluating_role: str = None) -> dict:
    """One entry's class, plus every other class its facts support.

    `matches` keeps the weaker answers visible for the same reason §16 keeps
    `also_matches`: MISPLACED and UNKNOWN are routinely true of the same entry,
    and a single word that hid the second would hide the question.

    `roles` is every root name bound to the physical directory this entry was
    found in, and `evaluating_role` is the one whose vocabulary was applied.
    They are equal, one long, for an ordinary root. Where they differ, the two
    names are aliases of one directory and the row says so — because a row that
    named only the role that happened to sort first would leave the reader
    unable to tell an alias from a second, unexplained root.
    """
    name = entry["name"]
    matches: list[tuple] = []
    size = sizes.get(entry["path"])
    size_note = "size_unmeasured" if size is None else None

    is_retained = bool(RETAINED_PATTERN.match(name))
    # The relation is keyed by a path, and the entry being explained is usually
    # the one at the END of the symlink — a real directory whose own entry has
    # no `link_target` at all. Keying on `entry["resolved"]` alone reported
    # `$DATA_ROOT/.vscode-server` as MISPLACED while the same fact was being
    # used correctly for `~/.vscode-server`, which is the same entry seen from
    # the other root. So the lookup resolves the entry itself.
    explained = relations.get(os.path.realpath(entry["path"]))
    in_vocabulary = name in vocabulary
    awaiting = AWAITING_A_RULING.get(name)
    declined = UNDECLARED_ON_PURPOSE.get(name)
    tool_owned = TOOL_OWNED_DOTDIRS.get(name)

    if in_vocabulary:
        matches.append((CANONICAL, (
            f"`{name}` is one of the {len(vocabulary)} names this root declares "
            f"as its top level"
        )))

    if is_retained and is_role_location:
        matches.append((RETENTION_REQUIRED, (
            f"`{name}` matches the retained-sibling pattern beside a `current` "
            f"pointer, so it is a previous published artifact kept deliberately. "
            f"It is the rollback target of whatever superseded it, its consumers "
            f"are the release procedure, and this round may not verify or "
            f"retire it (§29). RETENTION_REQUIRED is the opposite of LEGACY: "
            f"the contents are not to be migrated anywhere"
        )))

    if name.lower() in paths.FORBIDDEN_SUFFIXES:
        matches.append((LEGACY, (
            f"`{name}` is one of {list(paths.FORBIDDEN_SUFFIXES)}. These "
            f"outlive the task that created them and become structure by "
            f"accident; the disposition is to migrate the contents and retire "
            f"the path, which is a job for a person"
        )))

    if explained:
        matches.append((ALLOWED_EXCEPTION, explained["why"]))

    if name in HOME_ALLOWED_EXCEPTIONS:
        matches.append((ALLOWED_EXCEPTION, HOME_ALLOWED_EXCEPTIONS[name]))

    if tool_owned:
        matches.append((TOOL_OWNED_DOTDIR, (
            f"`{name}` is a declared tool-owned directory: "
            f"{tool_owned['owner']}. {tool_owned['why']}. Declared in this "
            f"module's `TOOL_OWNED_DOTDIRS` with the evidence that establishes "
            f"it — {tool_owned['evidence']} — so the exemption is a statement "
            f"about a named tool and a named provider, not about the dot the "
            f"name begins with"
            + (f". Classifying it here moves where it sits in this report and "
               f"nothing else: it is {tool_owned['protection']} — "
               f"{tool_owned['protection_basis']}"
               if tool_owned["protection"] != UNPROTECTED else "")
        )))

    if name in duplicates:
        matches.append((DUPLICATE_SEMANTIC_ROOT, (
            f"`{name}` is a declared root name and it is materialized under "
            f"more than one root. §16A reports this and moves nothing: which of "
            f"the copies is canonical is a decision that needs the consumers of "
            f"both, and a move made without them is how a live path stops "
            f"resolving for a reason nobody connects to the move"
        )))

    if size is not None and size > oversized_bytes:
        matches.append((OVERSIZED, (
            f"measured at {size} bytes, above the {oversized_bytes}-byte "
            f"reporting threshold. OVERSIZED is a size, not a verdict: a large "
            f"directory is not thereby removable, and nothing here may remove it"
        )))

    if (entry["is_dir"] and vocabulary and not in_vocabulary
            and not explained and name not in HOME_ALLOWED_EXCEPTIONS
            and not is_retained and not awaiting and not declined
            and not tool_owned):
        matches.append((MISPLACED, (
            f"`{name}` is a DIRECTORY under a root whose top level this product "
            f"declares a closed set of {len(vocabulary)}, and it is not one of "
            f"them and nothing explains it"
            + (" — this root's vocabulary is enforced on write by `admit()`, so "
               "the entry predates the rule or was made outside it"
               if root_name in ENFORCED_BY_ADMIT else
               " — this root's vocabulary is declared in policy and NOT enforced "
               "by `admit()`, so nothing refused the write")
        )))

    if not matches:
        matches.append((UNKNOWN, (
            (awaiting + ". " if awaiting else "")
            + (declined + ". " if declined else "")
            + f"nothing established where `{name}` belongs: it is not in "
            + (f"this root's declared top level {list(vocabulary)}" if vocabulary
               else f"any declared top level for {root_name!r}, because this "
                    f"root has none")
            + (f", it is a {('directory' if entry['is_dir'] else 'file')}"
               + (" and the vocabularies here are lists of roots, so a file at "
                  "this level is not covered by them either way. Whether it is "
                  "an entry document or drift is a judgement this module does "
                  "not have the input to make" if entry["is_file"] else "")
               if entry["is_dir"] or entry["is_file"] else "")
            + f". §16A's rule: an entry no rule covers is UNKNOWN, and it is not "
              f"downgraded to something with a disposition attached just because "
              f"the report looks firmer that way"
        )))

    primary, why = matches[0]
    bound_roles = tuple(roles) or (root_name,)
    return {
        "name": name,
        "path": entry["path"],
        "root": root_name,
        "bound_roles": list(bound_roles),
        "alias_of": (evaluating_role
                     if evaluating_role and evaluating_role != root_name else None),
        "kind": ("symlink" if entry["is_symlink"] else
                 "directory" if entry["is_dir"] else
                 "file" if entry["is_file"] else "unknown"),
        "link_target": entry.get("link_target"),
        "class": primary,
        "why": why,
        "matches": [{"class": c, "why": w} for c, w in matches],
        "also_matches": [c for c, _ in matches[1:]],
        "in_declared_vocabulary": in_vocabulary,
        "tool_owned": ({"owner": tool_owned["owner"],
                        "provider": tool_owned["provider"],
                        "evidence": tool_owned["evidence"],
                        "protection": tool_owned["protection"]}
                       if tool_owned else None),
        "protected": bool(tool_owned and tool_owned["protection"] != UNPROTECTED),
        "size_bytes": size,
        "size_note": size_note,
        "actionable": False,
        "schema": "serveros/fs-hygiene-entry.v1",
    }


def find_duplicate_semantic_roots(groups) -> dict:
    """Declared root names materialized under a root that does not own them.

    `artifacts` appears in DATA_ROOT's vocabulary; if it also appears under
    WORKSPACE_ROOT, that is the substitute-root shape the layout forbids —
    and it is precisely what `~/files` and `workspace/artifacts` were. The
    check is by NAME against every declared vocabulary, so a new root name
    added to `paths.py` is covered without a second list here.

    The question is asked per PHYSICAL directory, of the union of the roles
    bound to it: does any role bound here declare this name? Asking it per role
    name instead is what produced twelve false findings on this host. `home`
    and `data` resolve to one directory, `home` is deliberately absent from
    `ROOT_VOCABULARY`, and so every name the DATA_ROOT vocabulary declares was
    reported as an impostor — under the same directory, seen from its other
    name. A duplicate is a real disagreement about WHERE a thing is; a name
    that is simply one of the scanned root's own top-level names is not one.

    `owned_by` maps a declared name to the root NAMES that declare it, so the
    intersection that decides this is against the group's roles — not against
    the names those roles declare. Getting that backwards reports every name
    in the OTHER roots' vocabularies, which is the same defect one level along.
    """
    # A name is only duplicated if it appears somewhere OTHER than a root that
    # declares it. Compiling ownership from the same attribute map keeps one
    # source: add a root to `paths.py` and this follows.
    owned_by = {}
    for root_name, attribute in ROOT_VOCABULARY.items():
        for name in getattr(paths, attribute):
            owned_by.setdefault(name, set()).add(root_name)
    declared = set(owned_by)

    found: dict[str, list[dict]] = {}
    for group in groups:
        root = Path(group["physical_root"])
        if not root.is_dir():
            continue
        for entry in _scan(root):
            name = entry["name"]
            if name not in declared or not entry["is_dir"]:
                continue
            owners = owned_by.get(name, set())
            if owners & set(group["roles"]):
                continue
            found.setdefault(name, []).append({
                "root": group["roles"][0],
                "bound_roles": list(group["roles"]),
                "physical_root": group["physical_root"],
                "path": entry["path"],
                "belongs_under": sorted(owners),
            })
    return found


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _home_exception_of(path: Path, home: Path):
    """Which of §16B's three, if any, this path is inside.

    Compared on the FIRST segment under HOME, not on the whole path: the
    question is whether the write is going into one of the three roots, and
    comparing the full path to the root's own path would only ever match a
    write to the directory itself.
    """
    try:
        relative = path.relative_to(home)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def placement(path, *, paths_registry=None) -> dict:
    """§16B · where a NEW write may go, and what may never be done about it.

    The gate itself is `paths.Paths.admit` and is not restated here: a second
    copy of a placement rule is a second copy that can disagree with the first,
    and `admit` is the one the producers actually call. What this adds is the
    verdict form — `admit` raises, which is right for a producer that must not
    proceed, and wrong for a report that has to classify many paths and say
    what happened to each.

    `automatic_action` is always None, on every path, admitted or refused.
    §16B is explicit that nothing may symlink or migrate a location, so the
    field is present and permanently empty rather than absent: a caller that
    wanted to act has to find a field saying there is nothing to act on, which
    is a harder thing to misread than a missing key.
    """
    registry = paths_registry or paths.Paths()
    home = Path(registry.roots["home"])
    exception = _home_exception_of(Path(str(path)), home)
    in_exception = exception in HOME_ALLOWED_EXCEPTIONS
    try:
        verdict = registry.admit(str(path), writing=True)
    except paths.PathRefused as refusal:
        return {
            "path": str(path),
            "admitted": False,
            "refused_because": str(refusal),
            "canonical": None,
            "existing_exception": in_exception,
            "automatic_action": None,
            "why": (
                "refused by the placement contract. §16B forbids resolving this "
                "by symlink or by migrating the location: the fix is to write "
                "where the contract says, not to make the wrong place work"
                + (f" (`{exception}` is one of the three permitted HOME roots, "
                   f"so the refusal here is about what is being written inside "
                   f"it, not about the root" if in_exception else "")
            ),
            "schema": "serveros/fs-placement.v1",
        }
    return {
        "path": str(path),
        "admitted": True,
        "refused_because": None,
        "canonical": verdict["canonical"],
        "root": verdict["root"],
        "existing_exception": in_exception,
        "automatic_action": None,
        "why": (
            "admitted, and nothing is done to it. "
            + (f"`{exception}` is one of the three HOME roots §16B permits by "
               f"name; the permission is to LEAVE IT ALONE, not to relocate it, "
               f"and a later round that wants it under DATA_ROOT needs the "
               f"consumer list first"
               if in_exception else
               f"the contract classifies this as {verdict['canonical']}")
        ),
        "schema": "serveros/fs-placement.v1",
    }


def report(*, home=None, data=None, sizes=None, oversized_bytes=None) -> dict:
    """Classify both roots, change nothing, and say which question is open.

    `sizes` maps an absolute path to bytes, produced by whoever already
    measures. Passing none is legitimate and results in `size_unmeasured` on
    every row rather than in a silent zero.

    A root that is not there is reported as `absent` with its reason rather
    than omitted: a report over one root that reads as a report over two is
    how a check stops covering half of what it claims.

    The four root NAMES are resolved to their directories and then grouped by
    the directory they resolve to, and each group is scanned and classified
    ONCE. Two names for one place are two names for one place: scanning twice
    produced a second, vocabulary-less observation of the same entries and, in
    `find_duplicate_semantic_roots`, twelve findings about a root disagreeing
    with itself. The grouping is `paths.group_physical_roots`, so which names
    may share a directory is the contract's statement and not this module's.
    """
    home = Path(home) if home else Path(paths.Paths().roots["home"])
    data = Path(data) if data else Path(paths.Paths().roots["data"])
    sizes = sizes or {}
    oversized_bytes = oversized_bytes if oversized_bytes is not None else DEFAULT_OVERSIZED_BYTES
    relations = symlink_relations(home=home, data=data)
    root_names = ("home", "data", "workspace", "state")
    locations = {"home": str(home), "data": str(data),
                 "workspace": str(data / "workspace"), "state": str(data / "state/serveros")}
    groups = paths.group_physical_roots(locations, names=root_names)
    duplicates = find_duplicate_semantic_roots(groups)

    classified = []
    coverage = {}
    physical_roots = []
    for group in groups:
        # Which bound role's vocabulary applies. The first role in declaration
        # order that HAS one, else the first role: deterministic, and it needs
        # no table of its own because the vocabularies are the contract's.
        evaluating_role = next(
            (role for role in group["roles"] if _vocabulary(role)), group["roles"][0])
        vocabulary = _vocabulary(evaluating_role)
        directory = Path(group["physical_root"])
        groupsummary = {
            "physical_root": group["physical_root"],
            "roles": list(group["roles"]),
            "co_binding": group["co_binding"],
            "co_binding_declared": group["co_binding_declared"],
            "evaluated_with_vocabulary_of": evaluating_role,
            "permitted_bindings": [list(g) for g in paths.CO_BINDING_ROOT_GROUPS],
            "why": (
                (f"{' and '.join('`' + r + '`' for r in group['roles'])} all resolve "
                 f"to {group['physical_root']}, so it is scanned and classified "
                 f"once. They are several semantic roles answered by one physical "
                 f"root, which §25 permits and `CO_BINDING_ROOT_GROUPS` declares")
                + (". This combination is NOT among the declared ones: it is "
                   "reported as a merge nobody declared rather than grouped "
                   "away, because `WORKSPACE_ROOT=$DATA_ROOT` would apply "
                   "WORKSPACE_TOP_LEVEL to the home directory and a rule that "
                   "accepted any coincidence could not say so"
                   if not group["co_binding_declared"] else "")
                if len(group["roles"]) > 1 else
                f"`{group['roles'][0]}` is the only role bound to this directory"),
        }
        physical_roots.append(groupsummary)

        if not directory.is_dir():
            for role in group["roles"]:
                coverage[role] = {"state": "absent", "path": str(directory),
                                  "entries": 0, "physical_root": group["physical_root"],
                                  "bound_roles": list(group["roles"]),
                                  "why": "this node has no such root; nothing here"}
            continue

        entries = []
        unreadable = None
        for entry in _scan(directory):
            if entry.get("name") is None:
                unreadable = entry.get("error")
                break
            entries.append(classify_entry(
                entry, root_name=evaluating_role, vocabulary=vocabulary,
                relations=relations, sizes=sizes, oversized_bytes=oversized_bytes,
                duplicates=set(duplicates), roles=group["roles"],
                evaluating_role=evaluating_role))
        if unreadable is not None:
            for role in group["roles"]:
                coverage[role] = {"state": "unreadable", "path": str(directory),
                                  "entries": 0, "physical_root": group["physical_root"],
                                  "bound_roles": list(group["roles"]),
                                  "why": unreadable}
            continue

        for role in group["roles"]:
            is_alias = role != evaluating_role
            coverage[role] = {
                "state": "read", "path": str(directory), "entries": len(entries),
                "physical_root": group["physical_root"],
                "bound_roles": list(group["roles"]),
                "alias_of": evaluating_role if is_alias else None,
                "declared_top_level": list(vocabulary),
                "vocabulary_enforced_on_write": evaluating_role in ENFORCED_BY_ADMIT,
                "why": (
                    f"`{role}` and `{evaluating_role}` resolve to one directory, so "
                    f"its {len(entries)} entries are scanned and classified once "
                    f"and reported once, under `{evaluating_role}`. This row is the "
                    f"other name for the same place: its count is NOT additional to "
                    f"`{evaluating_role}`'s, and the two must not be added together. "
                    f"The vocabulary applied is `{evaluating_role}`'s because "
                    f"`{role}` has none and one directory cannot have two shapes"
                    if is_alias else
                    None if vocabulary else
                    "this root has no declared top-level vocabulary, so an "
                    "entry here cannot be MISPLACED against a list that does "
                    "not exist. Recorded rather than closed: a list invented "
                    "here would be authored by the module that checks it"),
            }
        classified.extend(entries)

    # The declared semantic-role locations, one level in.
    #
    # Without this pass RETENTION_REQUIRED is unreachable: `.prev-<version>` and
    # `.build-<name>` live INSIDE `$DATA_ROOT/runtime-assets/hwz-server-os/`,
    # not at DATA_ROOT's top level, so a report that only classified root
    # children never saw the one entry class §16A names by example. It was
    # caught by running the report and reading counts, not by reading the code —
    # which is the same way the `stale_candidate` reachability defect surfaced.
    #
    # One level, declared locations only, no recursion: these are the paths
    # `paths.SEMANTIC_ROLES` points at, so the set is the contract's and a role
    # that moves is a change in one table.
    role_rows = []
    for role, declared in sorted(paths.SEMANTIC_ROLES.items()):
        location = Path(declared.replace("{DATA_ROOT}", str(data)))
        if not location.is_dir():
            continue
        for entry in _scan(location):
            if entry.get("name") is None:
                continue
            role_rows.append(classify_entry(
                entry, root_name=f"role:{role}", vocabulary=(), relations=relations,
                sizes=sizes, oversized_bytes=oversized_bytes, duplicates=set(),
                is_role_location=True))
    classified.extend(role_rows)

    counts = {name: 0 for name in CLASSES}
    for row in classified:
        counts[row["class"]] = counts.get(row["class"], 0) + 1

    open_questions = [row for row in classified
                      if row["class"] in (MISPLACED, DUPLICATE_SEMANTIC_ROOT, LEGACY)]
    by_role = {role: summary for summary in physical_roots
               for role in summary["roles"]}
    return {
        "schema": "serveros/fs-hygiene-report.v1",
        "roots": {
            role: {
                "path": locations[role],
                "physical_root": by_role[role]["physical_root"],
                "class": ("DECLARED_CLOSED"
                          if _vocabulary(by_role[role]["evaluated_with_vocabulary_of"])
                          else "NO_VOCABULARY"),
                "bound_roles": by_role[role]["roles"],
                "evaluated_with_vocabulary_of": by_role[role]["evaluated_with_vocabulary_of"],
                "co_binding": by_role[role]["co_binding"],
                "co_binding_declared": by_role[role]["co_binding_declared"],
            }
            for role in root_names
        },
        "physical_roots": physical_roots,
        "coverage": coverage,
        "entries": classified,
        "counts": counts,
        "role_locations": {
            "declared": sorted(paths.SEMANTIC_ROLES),
            "entries": len(role_rows),
            "why": ("the declared semantic-role locations, one level in. The "
                    "retained artifacts §16A names by example live here rather "
                    "than at a root's top level, so this pass is what makes "
                    "RETENTION_REQUIRED reachable at all"),
        },
        "duplicate_semantic_roots": duplicates,
        "open_questions": open_questions,
        "symlinks_into_data_root": relations,
        "thresholds": {"oversized_bytes": oversized_bytes,
                       "oversized_bytes_source": "this module's default; a "
                       "reporting threshold, not a policy"},
        "note": (
            "§16A/§16B: this report was produced by reading. Nothing was moved, "
            "renamed, linked, chmod'ed or deleted, and nothing downstream of it "
            "may be either — a move made on the strength of a classification is "
            "a move made on the strength of a name. `.botmux-clone.log` and "
            "`.ccbridge-bridge.log` are classified and left alone by name. "
            "Retained release artifacts are RETENTION_REQUIRED, which is the "
            "opposite of LEGACY. Root names are grouped by the directory they "
            "resolve to and each group is evaluated once, so `coverage` carries "
            "one row per name and the counts belong to the DIRECTORY, not to "
            "the name: add up `counts`, never `coverage`. TOOL_OWNED_DOTDIR "
            "entries are declared in `TOOL_OWNED_DOTDIRS` with an owner, a "
            "provider and the evidence for each, and their protection attribute "
            "is carried through rather than decided here"
        ),
    }
