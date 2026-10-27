#!/usr/bin/env python3
"""Place a built artifact at a stable path and make a node run it.

Why this file exists
--------------------
`scripts/build.py` produces a runtime artifact for a composition. It has one
destination and it is a `current` directory, so before this script existed a
node could only be pointed at whatever directory last received a build — and
when the build was run from a worktree, that is what the systemd unit named.

That is not an installation. A worktree is per-Item and is retired when the
Item closes; a unit pointing at one is a service that stops starting on a day
nobody chose. The pilot unit said so in its own header, which is how this file
came to exist.

What it does instead
--------------------
    <runtime-root>/<composition>/revisions/<revision>/   the artifact, immutable
    <runtime-root>/<composition>/current                 a symlink to one revision
    <runtime-root>/<composition>/previous                a symlink to the last one

A release is a directory that already exists plus one symlink swap. Nothing is
edited in place, so a promotion that fails leaves the previous revision
untouched and running, and a rollback is the same operation in reverse.

Three properties are enforced rather than intended
--------------------------------------------------
1. Atomic promotion. The `current` symlink is replaced with `os.replace` over a
   fully-created temporary link, which is atomic within a filesystem. A reader
   that resolves `current` sees either the whole old revision or the whole new
   one, never a missing link.

2. Verified before activation. A revision is staged, its tree hash is recorded,
   and its entry point is checked to exist and to resolve the runtime it ships
   with — the pairing `bin/` and `runtime/` depend on. A revision that fails is
   left staged and NOT promoted, because the failure mode worth designing
   against is a promotion that half-succeeds.

3. Rollback is retained, not reconstructed. The promoted-away revision stays on
   disk and its identity is written to `previous`. A rollback points at a
   directory that is still there rather than at a hash somebody has to find.

Nothing here deletes. Retiring old revisions is a separate, deliberate act by a
person; a release tool whose failure mode is "the previous version is gone" has
removed the only thing that makes an atomic swap worth having.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
from fnmatch import fnmatchcase
import sys
from pathlib import Path

# The placeholders the units are written with. They are inert to systemd on
# purpose: systemd expands `%` and `$`, so a `$NAME` left unrendered would
# expand to the empty string and turn an ExecStart into a different, plausible
# path. `@...@` stays literal, so an unrendered unit fails naming the path it
# could not find instead of starting the wrong program.
RUNTIME_PLACEHOLDER = "@SERVEROS_RUNTIME@"
STATE_PLACEHOLDER = "@SERVEROS_STATE_DIR@"
# The alert delivery worker's destination, and the only placeholder whose
# unrendered form is not merely wrong but silently plausible: systemd would
# happily run `alert sync --conversation @SERVEROS_ALERT_CONVERSATION@`, and the
# provider would be asked to send to a chat named after a placeholder. So this
# one is REQUIRED rather than defaulted — `render_unit` raises when the template
# names it and no value is supplied, and `cmd_alert_worker` refuses to install
# without `--conversation`.
CONVERSATION_PLACEHOLDER = "@SERVEROS_ALERT_CONVERSATION@"
# The daily render's destination. Like the conversation, it has no honest
# default: the default output is the revision's own `workbench/`, which is a
# hash-verified directory the next update replaces. `render_unit` raises when the
# template names it and no value is supplied.
WORKBENCH_OUT_PLACEHOLDER = "@SERVEROS_WORKBENCH_OUT@"
# The evening brief's two. `@SERVEROS_EVENING_OUT_ROOT@` is the same kind of
# fact as the workbench destination — where a night's product lands — and is
# REQUIRED for the same reason. `@SERVEROS_INTAKE_RUN_DIR@` is different in kind
# and is the more dangerous of the two: it is not a destination at all but a
# directory ANOTHER UNIT owns, named here because this unit composes from it.
# Nothing in systemd relates the two, so a value that disagrees with
# `serveros-intake.service --run-dir` produces a brief composed from a directory
# nobody wrote. Also REQUIRED — a default would be this file inventing a
# directory another unit might not be using.
INTAKE_RUN_DIR_PLACEHOLDER = "@SERVEROS_INTAKE_RUN_DIR@"
EVENING_OUT_ROOT_PLACEHOLDER = "@SERVEROS_EVENING_OUT_ROOT@"
# The wake routine's three, and all three are REQUIRED for the same reason the
# conversation is: each has a plausible unrendered form. A unit that woke no
# Parent would run, exit 0, and report nothing to wake — indistinguishable from
# a node with no resumable checkpoints, which is the one reading §17.4 forbids.
WAKE_PARENT_PLACEHOLDER = "@SERVEROS_WAKE_PARENT@"
WAKE_BUDGET_PLACEHOLDER = "@SERVEROS_WAKE_BUDGET@"
WAKE_TIMEOUT_PLACEHOLDER = "@SERVEROS_WAKE_TIMEOUT@"
# The margin between the receiver's own bound on a Parent (`--budget`, which
# `runtime/wake.py` clamps each attempt to) and the unit's `TimeoutStartSec=`.
# `TimeoutStartSec` bounds the whole cgroup, so it has to be the LARGER of the
# two — it is the outer limit that catches a wake which never reaches its inner
# one, and the command needs those seconds to read the store, write a receipt
# and be killed cleanly if the Parent overran. Too small and systemd kills a
# Parent that was inside its grant; too large and it bounds nothing.
WAKE_MARGIN_SECONDS = 60

UNIT_NAME = "serverosd.service"
# §W's production delivery runtime. A oneshot and its timer, NOT a second
# resident unit — `tests/test_core_freeze.py` holds the distinction by reading
# `Type=` rather than by trusting this comment.
ALERT_SERVICE_NAME = "serveros-alert-sync.service"
ALERT_TIMER_NAME = "serveros-alert-sync.timer"
# §8's daily-task template. Shipped, installable, and NOT enabled by default —
# the install prints the one command that arms it and nothing arms it otherwise.
DAILY_SERVICE_NAME = "serveros-daily.service"
DAILY_TIMER_NAME = "serveros-daily.timer"
# §17.4's wake half. A oneshot and its timer, on the same footing as the two
# above and installed DISABLED: arming a routine that starts agent processes is
# an act somebody approves, not a side effect of a promotion.
WAKE_SERVICE_NAME = "serveros-continuation-wake.service"
WAKE_TIMER_NAME = "serveros-continuation-wake.timer"
# The second half of the day's work, and the one that was missing from this list
# until 2026-10-08. `serveros-intake.timer` prepares at 19:20 and sends nothing;
# this pair turns that preparation into a brief that leaves the machine at 20:00,
# and it ran on this node for as long as it had existed while appearing in no
# template, no check, no build and no test in this product. Measured on the day
# it was added, while it was still missing: the timer was `enabled`, it had fired
# at 20:00 that same day, and the receipt it wrote said `delivered True` — so
# what was undeclared was a live, person-facing path, not a dormant one.
#
# THE RULE THAT DECIDES WHAT JOINS THIS LIST, because it is not "every unit this
# node runs". A unit belongs here when its whole ExecStart chain is this
# product's to own. `serveros-evening-alignment`'s is one `serverosctl` verb, so
# it does. `serveros-intake`'s is three lines of which two run scripts under
# `runtime-assets/` that this repository does not build, and `serveros-work-board`
# runs one such script entirely — templating those would mean shipping one
# person's chat ids and one node's private scripts as if they were a build fact.
# They are recorded as live producers this product does not declare, which is a
# different and honest answer from declaring them wrongly.
EVENING_SERVICE_NAME = "serveros-evening-alignment.service"
EVENING_TIMER_NAME = "serveros-evening-alignment.timer"
TREE_MANIFEST = "RELEASE.json"

# The Parent launcher, and why it is shipped rather than supplied.
#
# `--parent` was required until 2026-10-06, on the argument that what a woken
# Parent IS is a fact about this node's agents rather than about a build. The
# argument was right and the conclusion did not follow: the posture it names — no
# shell, one Item's worth of adapter, `--strict-mcp-config` — is a SERVEROS
# decision about continuation, and it was living in a hand-written file under
# `~/.local/bin` on the one node that had got around to it. A posture that no
# release carries is a posture no test can hold and no other node can inherit.
#
# So the revision renders this template, installs it beside the operator's other
# binaries, and the unit points at THAT. `--parent` still exists for a node whose
# Parent is something else; when it is given, the template is not installed and
# the given path is used verbatim.
WAKE_PARENT_TEMPLATE = "runtime/serveros-wake-parent"
WAKE_PARENT_NAME = "serveros-wake-parent"

# Bytecode the interpreter writes into a directory it has RUN from. Excluded from
# `fingerprint` unconditionally, and the reason is not tidiness.
#
# A staged revision is a directory the node executes out of, so running it
# creates `__pycache__/*.pyc` inside the very tree whose hash was recorded. The
# hash therefore has to be a function of the PAYLOAD and not of the directory —
# otherwise it is not stable across the revision's own lifetime: `promote`
# computes one number before the daemon has ever run, a verifier recomputes
# another afterwards from an unmodified tree, and the second number is presented
# as evidence of tampering. That is not a hypothetical. It is what
# `runtime_truth.py` reported on 2026-09-26 for the live node, whose revision
# differed from its record by exactly 34 `.pyc` files and nothing else —
# measured, and reproduced to the byte by applying this exclusion.
#
# The exclusion cannot hide a real difference: the builder ships no bytecode.
# The 2026-09-24d candidate artifact contains 0 of them, as does every one of the
# twelve revisions under this node's distribution root that was never made
# current — the five that carry `.pyc` at all are exactly the five that have been
# RUN from. So on every input this tool hashes at build or promote time the
# exclusion is invisible, and it is visible only where it must be: on a directory
# something has since executed.
BYTECODE_DIRS = frozenset({"__pycache__"})
BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})

# The build's own statement about what it built, written by `build.py --write`
# beside the artifact's files. `release.py` reads it and never guesses: the two
# facts this path needs before it can verify anything are WHAT KIND of thing is
# being released and WHICH composition it came from, and neither is recoverable
# from a directory listing without resorting to a name.
#
# It carries the file list the build produced as well, which is what lets a
# release say "no undeclared files" as a comparison against a record rather than
# as a rule about which filenames look suspicious.
ARTIFACT_MANIFEST = "ARTIFACT.json"
ARTIFACT_MANIFEST_SCHEMA = "serveros/artifact.v1"

# Artifact kinds. A kind is a VERIFIER, and the verifier is the whole difference
# between the faces of this product: a node runtime is a daemon with a unit and a
# CLI shim, a plugin is a payload a different Claude Code loads, and a native
# runtime is a set of compiled binaries with no installed surface at all. They
# share the lifecycle — candidate, verify, `revisions/<revision>`, `current`,
# `previous`, receipt, rollback — and they differ in what "verified" means and in
# whether there is anything outside the revision directory to install.
#
# The set is closed and named, so a kind that is not one of these is refused by
# name rather than falling through to whichever branch happens to be last.
NODE_RUNTIME = "node-runtime"
PLUGIN = "plugin"
#: The per-device C++ runtime (`serverosd/`), packaged by that directory's own
#: `make dist` rather than by `build.py`. It is a
#: separate kind rather than a face of `node-runtime` because the two have
#: disjoint payloads: a node runtime is Python plus a unit plus a CLI shim, and
#: this ships four ELF binaries and one package manifest. Releasing it as
#: `node-runtime` would make the verifier demand `runtime/daemon.py` from a
#: payload that has never contained one.
NATIVE_RUNTIME = "native-runtime"
#: The two halves of L2, released apart because they answer to different
#: machines: the per-machine connector ships everywhere, and the Tokyo hub is one
#: host. Registering them here is what lets the release path READ their artifacts;
#: it is not a claim that either can be installed on its own — see
#: `verify_l2_layer`'s `installable` verdict and the NOT_STANDALONE refusal in
#: `cmd_promote`.
L2_NODE = "l2-node"
L2_HUB = "l2-hub"
L2_KINDS = (L2_NODE, L2_HUB)
ARTIFACT_KINDS = (NODE_RUNTIME, PLUGIN, NATIVE_RUNTIME, L2_NODE, L2_HUB)

#: The native package's own manifest and its one actuator. Named here because
#: the release path needs them as REQUIRED MEMBERS; the authority on what they
#: contain is `serverosd/verify_native_runtime.py`, and the verifier below
#: delegates to it rather than restating it.
PACKAGE_MANIFEST = "PACKAGE.json"
NATIVE_PACKAGE_SCHEMA = "serveros/native-runtime-package.v1"
NATIVE_ACTUATOR = "bin/serveros-native-core"

# What each kind requires of a revision, as data rather than as an if-chain, so
# that `status` can answer "which of these is missing" for a kind it is only
# reading about. The node list is the one `verify_revision` has always checked;
# the plugin list is §9's, expressed in the layout `build.py` actually produces
# (the artifact root IS the plugin payload — there is no nested `dist/`, and
# inventing one here would put a path in the release tool that no build writes).
#
# `workengine/` joined the node list when the Work Engine moved inside this
# repository (`work-engine@19d9520`). The requirement names its schema rather
# than the bare directory because `verify_revision` below compares against
# FILES; `schema.sql` is the package asset whose absence makes an installed
# engine unable to open its store, so it is the member that proves the
# directory actually shipped.
REQUIRED_MEMBERS = {
    NODE_RUNTIME: ("bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
                   "runtime/cli.py", f"runtime/{UNIT_NAME}",
                   "workengine/schema.sql"),
    PLUGIN: (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"),
    # Two members, and both are load-bearing for the same reason: the manifest
    # is what the verifier checks the payload AGAINST, and the actuator is the
    # one binary that may ask the service manager for anything. A payload with
    # the manifest and no actuator is a claim with nothing behind it; one with
    # the actuator and no manifest is a binary nobody can identify.
    NATIVE_RUNTIME: (PACKAGE_MANIFEST, NATIVE_ACTUATOR),
    # The file each kind exists for. Absence means the payload is not the thing
    # it claims to be, whichever verifier runs.
    L2_NODE: ("fleet_mcp/__init__.py", "fleet_mcp/node/__init__.py",
              "fleet_mcp/node/connector.py"),
    L2_HUB: ("fleet_mcp/__init__.py", "fleet_mcp/server.py"),
}

# ---------------------------------------------------------------- L2 contract
#
# These tables are COPIED from `integrations/fleet-v1-l2-release/l2_release.py`
# (PR #431, head 001eb7c2), which is the reference implementation and the thing
# that builds the payloads. Copying rather than importing is deliberate: an
# integration directory is not a library the release tool may depend on, and a
# release tool that imported its own input would be verifying a payload against
# code shipped beside it. The two must agree, and
# `tests/test_release_l2_layers.py` compares them directly when #431 is present
# in the tree.
L2_COMPOSITION = "fleet-l2"
L2_NAMES = {L2_NODE: "fleet-l2-node", L2_HUB: "fleet-l2-hub"}
L2_ENTRY = {L2_NODE: "fleet_mcp.node.connector", L2_HUB: "fleet_mcp.server"}
L2_PLATFORMS = {L2_NODE: ("linux", "windows"), L2_HUB: ("linux",)}

#: The hub's own ingress and operator face. A connector that carries these can
#: start an MCP server or run OAuth because it was imported.
L2_NODE_DENIED_MODULES = ("fleet_mcp.server", "fleet_mcp.tools",
                          "fleet_mcp.oauth", "fleet_mcp.owner_setup")
L2_NODE_DENIED_PREFIXES = ("fleet_mcp/deploy/", "fleet_mcp/bin/")

#: The measured coupling a node artifact is allowed to have today, frozen. A
#: module outside both this tuple and L2_NODE_EXCLUSIVE is new coupling.
L2_NODE_SHARED_SURFACE = (
    "fleet_mcp.audit", "fleet_mcp.cloudcli_native", "fleet_mcp.core_lite_readback",
    "fleet_mcp.core_lite_source", "fleet_mcp.disclosure", "fleet_mcp.discovery",
    "fleet_mcp.dispatch", "fleet_mcp.feishu_sso", "fleet_mcp.fleet",
    "fleet_mcp.mcp_http", "fleet_mcp.owner_access", "fleet_mcp.privacy",
    "fleet_mcp.providers.fleet_node", "fleet_mcp.registry",
    "fleet_mcp.runtime_create_policy", "fleet_mcp.scopes",
    "fleet_mcp.session_detail",
)
L2_NODE_EXCLUSIVE = frozenset({
    "fleet_mcp", "fleet_mcp.node", "fleet_mcp.node.connector",
    "fleet_mcp.node_protocol", "fleet_mcp.errors", "fleet_mcp.identity",
    "fleet_mcp.local_roots", "fleet_mcp.config", "fleet_mcp.runtime_identity",
    "fleet_mcp.providers", "fleet_mcp.providers.base",
    "fleet_mcp.providers.cloudcli", "fleet_mcp.version",
})

#: A hub release is software. Units, bindings, launchers and keys are properties
#: of the machine that has them.
L2_HUB_DENIED_PREFIXES = ("fleet_mcp/node/",)
L2_HUB_DENIED_PATTERNS = ("*.local.yaml", "*.local.yml", "bindings/*", "*.service",
                          "*.timer", "*.service.in", "*.ps1", "*.vbs", "*.pem",
                          "*.key", ".env")
#: Applied to non-source files only: `token_cache.py` is code, `tokens.json` is a
#: credential.
L2_HUB_SECRET_PATTERNS = ("*credential*", "*token*")

L2_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")
#: A payload that forwards the CloudCLI tool schema asks the running CLI; one that
#: carries a copy keeps answering after the CLI moves.
L2_SCHEMA_MARKER = "inputSchema"
L2_SCHEMA_TOOL_HINT = "run_get"


def default_state_dir() -> Path:
    """The node's state root, read from the project's own path contract.

    This replaced `Path(os.environ.get("DATA_ROOT", home)) / "state/serveros"`,
    which was wrong in a way nothing here could see. `DATA_ROOT` is a documented
    root, not an ambient variable, so on an ordinary shell the lookup misses and
    the expression falls back to `$HOME` — rendering a unit whose
    `SERVEROS_STATE_DIR` is `$HOME/state/serveros`, a path that does not exist.
    The daemon then exits 2 looking for a registry there, and the node is one
    restart away from being down.

    Nothing caught it when it happened. `promote` reloads units but does not
    restart the service, so the node kept running the process it already had;
    `status` reported `agreement AGREE` because it compares the symlink and the
    unit, not whether the state root exists; and `verify_revision` only asked
    that rendering succeed, which a wrong path does. So the defect was invisible
    at promote time and surfaced much later as a service that would not start —
    the worst shape for an installation bug to have.

    `runtime/paths.py` is the qualified resolver for this: the node's roots,
    read from the caller, the environment, or the device's authored Profile
    binding, and a refusal for an undeclared name instead of an invented one.
    Asking it means a wrong root cannot be reached by asking the environment a
    question it is permitted not to answer. (It carried a literal fallback when
    this note was written. That is gone: an unanswered root is UNBOUND now, and
    this call site reads the same resolver either way.)
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
    import paths  # noqa: PLC0415 - resolved from the repo, never installed

    return paths.Paths().root("state")


def fingerprint(root: Path, ignore: tuple[str, ...] = ()) -> str:
    """Hash a directory's whole file tree.

    This reproduces, byte for byte, the recipe the release records already
    quote:

        find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum

    Reproducing it rather than merely defining something reasonable is the
    point. A release document that quotes a hash, and a tool that computes a
    different one from the same directory, produce two numbers that are both
    correct and cannot be compared — which is worse than having no hash, because
    it looks like verification.

    The three details that make the output match: paths are rendered relative
    with a `./` prefix (that is what `find .` emits), lines are ordered by that
    path in plain byte order (`sort -z` under LC_ALL=C sorts the paths before
    `sha256sum` ever sees them), and each line is `sha256sum`'s own
    `"<hex>  <path>"` — two spaces.

    Ordering by path rather than by line is the part worth stating. Sorting the
    finished lines would order them by their leading hash, which is a different
    permutation of the same bytes and therefore a different digest — a hash that
    looks like verification and is not.

    What the recorded hash covers, and what it deliberately does not. A staged
    revision contains `RELEASE.json`, and that file holds the hash — so it is
    written after the hash is computed and is not itself covered by it. A file
    cannot contain its own digest, so this is forced rather than chosen, but it
    is worth saying out loud because the natural check gets it wrong:
    `fingerprint(revisions/<rev>)` returns a DIFFERENT number from the one
    recorded in that same directory, and a reader who does not know why will
    read a correct revision as tampered with.

    The hash that has to match is the artifact's. Fingerprinting the built
    artifact, or the published asset after extracting it, gives the recorded
    value; fingerprinting the staged directory gives that value plus one file,
    which is a different tree and correctly a different number.

    `ignore` exists so that the alternative to reading this paragraph is not
    writing a second hash function. A verifier standing outside this tool has
    only the STAGED directory to look at — it cannot fingerprint the artifact,
    because the artifact is gone by then — so it must ask for the recipe applied
    to the tree the recorded hash actually describes. Reimplementing the four
    details above in another file to get there is exactly the two-definitions
    failure this docstring warns about, so the recipe grew a filter instead.
    Relative paths, matching what `sort -z` would have seen; the default is
    empty and every existing caller is unaffected.

    One refinement of the quoted recipe, which is worth stating because the
    recipe above is quoted as if it were literal: bytecode is skipped, always
    (see `BYTECODE_DIRS`). The quoted command would have hashed it, and the two
    agree on every tree this tool has ever hashed, because the builder ships
    none — so the recorded values are exactly what the literal pipeline produces
    on the artifact. They part company only on a directory the interpreter has
    since run from, and there the literal pipeline is the one producing a number
    nobody should be asked to trust.
    """
    skip = set(ignore)
    entries = [p for p in root.rglob("*")
               if p.is_file()
               and p.relative_to(root).as_posix() not in skip
               and p.suffix not in BYTECODE_SUFFIXES
               and not BYTECODE_DIRS.intersection(p.relative_to(root).parts)]
    entries.sort(key=lambda p: f"./{p.relative_to(root).as_posix()}")
    stream = "".join(
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(root).as_posix()}\n"
        for p in entries
    )
    return hashlib.sha256(stream.encode()).hexdigest()


def _symlink_swap(link: Path, target: Path) -> None:
    """Atomically repoint `link` at `target`.

    A symlink is created beside the destination and renamed over it, because
    `os.replace` on a symlink replaces the link itself rather than writing
    through it. Removing the old link first would leave a window in which
    `current` does not exist, and a supervisor reading it in that window sees a
    missing installation rather than an old one.
    """
    tmp = link.with_name(link.name + ".swap")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(target)
    os.replace(tmp, link)


def render_unit(template: str, runtime_dir: Path, state_dir: Path, *,
                conversation: str | None = None,
                workbench_out: str | None = None,
                intake_run_dir: str | None = None,
                evening_out_root: str | None = None,
                wake_parent: str | None = None,
                wake_budget: str | None = None,
                wake_timeout: str | None = None) -> str:
    """Write a unit template out for THIS node.

    `conversation` is keyword-only and has no default because there is no
    honest one. A unit that names the alert destination and is rendered without
    one would either keep the literal placeholder — and be handed to a provider
    as a chat id — or expand to the empty string, which systemd turns into
    `--conversation` with nothing after it. Both produce a worker that runs,
    fails to send, and reports that as nothing to do. Raising here is the only
    outcome that says what is actually wrong.

    The wake routine's three are required for the same reason and are the reason
    this docstring is not one sentence longer: a waker with no Parent runs on
    cadence, wakes nothing, and exits 0 — a success report for a node whose
    checkpoints are not being resumed. See `WAKE_PARENT_PLACEHOLDER`.
    """
    rendered = template.replace(RUNTIME_PLACEHOLDER, str(runtime_dir))
    rendered = rendered.replace(STATE_PLACEHOLDER, str(state_dir))
    if CONVERSATION_PLACEHOLDER in template:
        if not conversation:
            raise ValueError(
                f"unit names {CONVERSATION_PLACEHOLDER} and no conversation was "
                f"supplied; alert delivery has no destination and must not be "
                f"installed as if it did"
            )
        rendered = rendered.replace(CONVERSATION_PLACEHOLDER, conversation)
    if WORKBENCH_OUT_PLACEHOLDER in template:
        if not workbench_out:
            raise ValueError(
                f"unit names {WORKBENCH_OUT_PLACEHOLDER} and no destination was "
                f"supplied; a daily render with no destination writes somewhere "
                f"nobody chose and reports success"
            )
        rendered = rendered.replace(WORKBENCH_OUT_PLACEHOLDER, workbench_out)
    for placeholder, value, why in (
            (INTAKE_RUN_DIR_PLACEHOLDER, intake_run_dir,
             "the unit would compose tonight's brief from a directory nobody "
             "named, which is either a composition that refuses at 20:00 or one "
             "from another evening's file"),
            (EVENING_OUT_ROOT_PLACEHOLDER, evening_out_root,
             "the night's brief would be written to a directory nobody chose "
             "and reported as sent")):
        if placeholder in template:
            if not value:
                raise ValueError(f"unit names {placeholder} and none was "
                                 f"supplied; {why}")
            rendered = rendered.replace(placeholder, str(value))
    for placeholder, value, why in (
            (WAKE_PARENT_PLACEHOLDER, wake_parent,
             "a wake routine with no Parent to start would fire on its timer, "
             "wake nothing and exit 0, which is a success report for a node "
             "whose checkpoints are not being resumed"),
            (WAKE_BUDGET_PLACEHOLDER, wake_budget,
             "a wake routine with no budget would spend whatever the grant said "
             "regardless of what the node's own timer can absorb"),
            (WAKE_TIMEOUT_PLACEHOLDER, wake_timeout,
             "the unit's outer bound would be unset, and an unbounded oneshot is "
             "a timer that silently stops firing")):
        if placeholder in template:
            if not value:
                raise ValueError(f"unit names {placeholder} and none was "
                                 f"supplied; {why}")
            rendered = rendered.replace(placeholder, str(value))
    left = [p for p in (RUNTIME_PLACEHOLDER, STATE_PLACEHOLDER,
                        CONVERSATION_PLACEHOLDER, WORKBENCH_OUT_PLACEHOLDER,
                        INTAKE_RUN_DIR_PLACEHOLDER, EVENING_OUT_ROOT_PLACEHOLDER,
                        WAKE_PARENT_PLACEHOLDER, WAKE_BUDGET_PLACEHOLDER,
                        WAKE_TIMEOUT_PLACEHOLDER)
            if p in rendered]
    if left:
        raise ValueError(f"unit still carries an unrendered placeholder: {left}")
    # And any OTHER placeholder, because the list above is only the placeholders
    # this file knows how to fill — a NEW one can be added to a unit and not
    # here. The symptom is quiet in exactly the way this project keeps paying
    # for: systemd treats `@...@` as text, so a unit carrying a placeholder
    # nobody registered installs cleanly and runs with the literal in it. The
    # check is a shape rather than a name so that the next placeholder is
    # caught by the person who typo'd it rather than by the person reading a
    # journal six weeks later.
    stray = sorted(set(re.findall(r"@[A-Z][A-Z0-9_]*@", rendered)))
    if stray:
        raise ValueError(
            f"unit carries a placeholder no renderer fills: {stray}. Add it to "
            f"`render_unit` and to the caller that supplies its value, or the "
            f"unit installs with the literal in it and systemd runs it as text"
        )
    return rendered


def read_artifact_manifest(artifact: Path) -> dict | None:
    """The build's statement about this artifact, or None if it carries none."""
    path = artifact / ARTIFACT_MANIFEST
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"{path} is not valid JSON: {exc}\n"
            f"The artifact manifest is what says which kind of thing this is and "
            f"which composition built it. A manifest that cannot be read is not a "
            f"weaker statement — it is no statement, and the release path has "
            f"nothing to verify against."
        )
    if value.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise SystemExit(
            f"{path} declares schema {value.get('schema')!r}, expected "
            f"{ARTIFACT_MANIFEST_SCHEMA!r}. This release path reads one schema; a "
            f"differently-shaped manifest would be parsed as if the fields meant "
            f"the same thing."
        )
    kinds = value.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        raise SystemExit(
            f"{path} declares no artifact kinds. A build that cannot say what it "
            f"produced cannot be released: the verifier is chosen by the kind, so "
            f"an empty list would have to be guessed at."
        )
    unknown = sorted(set(kinds) - set(ARTIFACT_KINDS))
    if unknown:
        raise SystemExit(
            f"{path} declares unknown artifact kind(s): {unknown}. This release "
            f"path implements {list(ARTIFACT_KINDS)}; a kind it does not implement "
            f"has no verifier, and releasing it unverified is what the verifier "
            f"exists to prevent."
        )
    return value


def resolve_kind(target: Path, *, declared: str | None = None) -> tuple[str, str]:
    """Which verifier applies, and on what basis. Never inferred from a name.

    The order is: an explicit choice the caller made, then the build's own
    declaration (in the artifact manifest for an artifact, in `RELEASE.json` for
    an already-staged revision), then — only for a revision staged before kinds
    were recorded — the structure of the thing itself.

    That last fallback is not a name special-case and it is not silent: it asks
    which verifier's structural requirements the directory actually satisfies,
    and it needs exactly one answer. A revision where both or neither applies is
    refused, because choosing then would be choosing on the reader's behalf. The
    basis is returned so every caller can print it: a reader is entitled to know
    whether the tool was told or worked it out.
    """
    if declared is not None:
        if declared not in ARTIFACT_KINDS:
            raise SystemExit(
                f"--kind {declared!r} is not a known artifact kind "
                f"({list(ARTIFACT_KINDS)})"
            )
        return declared, "chosen"

    manifest = read_artifact_manifest(target)
    if manifest is not None:
        kinds = list(manifest["kinds"])
        if len(kinds) == 1:
            return kinds[0], "declared"
        # The composition says this payload is released as more than one kind
        # (core and linux-work are both a node runtime AND a plugin). The
        # declaration cannot narrow itself, so the choice has to be made
        # explicitly — and it is refused rather than defaulted to the first,
        # because "which of these two things are you installing" is the caller's
        # question and a default would answer it in a log line nobody reads.
        raise SystemExit(
            f"artifact declares more than one kind: {kinds}\n"
            f"Pass --kind to say which one this release is."
        )

    receipt = target / TREE_MANIFEST
    if receipt.is_file():
        recorded = json.loads(receipt.read_text()).get("kind")
        if recorded in ARTIFACT_KINDS:
            return recorded, "recorded"
        if recorded:
            raise SystemExit(
                f"{receipt} records kind {recorded!r}, which this release path "
                f"does not implement ({list(ARTIFACT_KINDS)})"
            )

    # No declaration anywhere. Ask the structure, and require a single answer.
    fits = [k for k in ARTIFACT_KINDS
            if all((target / rel).is_file() for rel in REQUIRED_MEMBERS[k])]
    if len(fits) == 1:
        return fits[0], "structural"
    raise SystemExit(
        f"cannot tell what kind of artifact this is: {target}\n"
        f"  no {ARTIFACT_MANIFEST} and no kind in {TREE_MANIFEST}\n"
        f"  structural requirements satisfied by: {fits or 'neither kind'}\n"
        f"A revision staged before artifact kinds were recorded can still be "
        f"verified, but only when exactly one verifier's requirements fit. Pass "
        f"--kind if you know which one this is."
    )


def stage(artifact: Path, revisions_root: Path, revision: str) -> Path:
    """Copy a built artifact into an immutable revision directory.

    What may be done to a leftover `.staging-<revision>` is decided by what the
    leftover IS, not by the fact that it exists:

      * absent                       -> create it
      * present, and it is a partial copy of THIS artifact (the same identity,
        written by an interrupted run of this same command) -> resume, which in
        practice means remove and recopy, and say so
      * present and something else   -> REFUSE

    The rule this replaces was `if staging.exists(): shutil.rmtree(staging)`, and
    it was wrong for the reason the rest of this file exists: a `.staging-*`
    directory is not evidence of a failed run of the command that is running now.
    It can be a finished release's payload that something still points at, or
    another actor's half-written copy, and the old line deleted either of those
    without looking. `rmtree` was the default action on an object whose owner and
    intent the command had not established, which is the one classification this
    project does not let a tool make on its own.
    """
    dest = revisions_root / revision
    if dest.exists():
        raise SystemExit(
            f"revision already staged: {dest}\n"
            f"Releasing the same revision twice is not a no-op — it is either a "
            f"rebuild that produced different bytes, or the same bytes and a "
            f"wasted copy. Inspect it, remove it deliberately if that is right, "
            f"and stage again."
        )

    staging = revisions_root / f".staging-{revision}"
    if staging.exists():
        same_identity = (staging / ARTIFACT_MANIFEST).is_file() and (
            (artifact / ARTIFACT_MANIFEST).is_file()) and (
            (staging / ARTIFACT_MANIFEST).read_bytes()
            == (artifact / ARTIFACT_MANIFEST).read_bytes())
        if not same_identity:
            raise SystemExit(
                f"refusing to reuse {staging}\n"
                f"There is already a staging directory for this revision and it is "
                f"not a partial copy of the artifact being staged now. It may belong "
                f"to a finished release that something still points at, or to "
                f"another actor's run. Nothing here removes a directory whose owner "
                f"and intent have not been established. Inspect it, and if it is "
                f"stale remove it deliberately — then stage again."
            )
        # Same identity and left behind by an interrupted run of this same
        # command: the only case where removing it is this tool's own business.
        shutil.rmtree(staging)

    shutil.copytree(artifact, staging, symlinks=False)
    staging.rename(dest)
    return dest


def verify_revision(rev_dir: Path, state_dir: Path, *,
                    kind: str = NODE_RUNTIME) -> dict:
    """Check a staged revision before anything points at it.

    The verifier is selected by KIND, which is why this is a dispatcher rather
    than a longer list of requirements. `stage()` used to require
    `bin/serverosd` for every artifact, and that check made a node's entry point
    decide whether a plugin could be released — a fact about one kind standing in
    for a fact about all of them. It lives in the node verifier now, where it is
    a statement about a node rather than a toll on the path.
    """
    if kind not in ARTIFACT_KINDS:
        raise SystemExit(f"no verifier for artifact kind {kind!r}")
    for rel in REQUIRED_MEMBERS[kind]:
        if not (rev_dir / rel).is_file():
            problems = [f"missing {rel}"]
            return {"ok": False, "problems": problems, "tree_hash": None}
    if kind == PLUGIN:
        return verify_plugin(rev_dir)
    if kind == NATIVE_RUNTIME:
        return verify_native_runtime(rev_dir)
    if kind in L2_KINDS:
        # BEFORE the node fall-through. Adding a kind to ARTIFACT_KINDS makes it
        # reachable, and a payload with no daemon would otherwise be asked for
        # `bin/serverosd` and refused for a reason about another product.
        return verify_l2_layer(rev_dir, kind=kind)
    return verify_node_runtime(rev_dir, state_dir)


def verify_plugin(rev_dir: Path) -> dict:
    """Check a built plugin payload before a Claude Code is told to load it.

    A plugin has no daemon and no unit, so none of the node verifier applies. What
    it does have is a set of DECLARATIONS — the two plugin manifests, the MCP
    manifests, the composition's member list, the build's file list — and every
    check below compares one declaration against another or against the files
    actually present. That is the whole content of "verified" here: not that it
    loads (this tool cannot load it and does not pretend to), but that the
    statements it makes about itself are true.
    """
    problems: list[str] = []
    try:
        manifest = read_artifact_manifest(rev_dir)
    except SystemExit as exc:
        # A refusal about the manifest is a finding about this revision, so it is
        # reported the way every other finding is. Letting it escape as an
        # exception would make the same fact print as a traceback in one caller
        # and as "REFUSED ..." in another, which is the difference between a
        # verifier's verdict and a crash.
        return {"ok": False, "tree_hash": None, "problems": [str(exc)]}
    if manifest is None:
        # The shared required-member check already covers the two plugin
        # manifests, so this cannot happen via `verify_revision`. It is kept
        # because `verify_plugin` is also reachable directly, and a verifier that
        # silently checks less when its input is missing is the failure mode this
        # whole function is about.
        return {"ok": False, "tree_hash": None,
                "problems": [f"no {ARTIFACT_MANIFEST}; nothing declares what this is"]}

    # 1. The plugin manifest, and the identity it claims. `build.py` renders this
    #    from the composition, so a mismatch means the release is being handed a
    #    payload from a different composition than the one it was told about —
    #    which is the one confusion the revision name cannot detect, because the
    #    caller chooses that name.
    declared_name = manifest.get("name")
    declared_version = manifest.get("version")
    for rel in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        try:
            plugin = json.loads((rev_dir / rel).read_text())
        except json.JSONDecodeError as exc:
            problems.append(f"{rel} is not valid JSON: {exc}")
            continue
        if plugin.get("name") != declared_name:
            problems.append(
                f"{rel} names {plugin.get('name')!r} but the build manifest "
                f"names {declared_name!r}"
            )
        if plugin.get("version") != declared_version:
            problems.append(
                f"{rel} is version {plugin.get('version')!r} but the build "
                f"manifest says {declared_version!r}"
            )

    # 2. Cache identity. The version string is `<version>+<cachebuster>`, and the
    #    cachebuster is the part an installed cache keys on, so a version that
    #    does not carry the declared cachebuster is a payload that will be served
    #    from a cache belonging to a different payload.
    cachebuster = manifest.get("cachebuster")
    if not cachebuster:
        problems.append("build manifest declares no cachebuster; the installed "
                        "cache identity would be unstated")
    elif declared_version and not str(declared_version).endswith(f"+{cachebuster}"):
        problems.append(
            f"version {declared_version!r} does not end with the declared "
            f"cachebuster {cachebuster!r}"
        )

    # 3. Skills actually materialized. The composition's member list is what the
    #    build recorded; a name on it with no `SKILL.md` on disk is a plugin that
    #    lists a capability it cannot answer, and it would load cleanly.
    for name in manifest.get("skills") or []:
        skill = rev_dir / "skills" / name / "SKILL.md"
        if not skill.is_file():
            problems.append(f"composed skill {name!r} has no skills/{name}/SKILL.md")

    # 4. MCP server declarations exactly match the composition. The server name
    #    appears in an agent's tool namespace, so a manifest that declares a
    #    different set than the composition is an adapter reachable under a name
    #    nobody composed, or a composed name with nothing behind it.
    composed_servers = sorted(manifest.get("mcp_servers") or [])
    mcp_files = [rel for rel in (".mcp.json", ".codex-mcp.json")
                 if (rev_dir / rel).is_file()]
    if composed_servers and not mcp_files:
        problems.append(
            f"composition declares MCP servers {composed_servers} and the payload "
            f"carries no MCP manifest"
        )
    for rel in mcp_files:
        mcp = json.loads((rev_dir / rel).read_text())
        declared = sorted(mcp.get("mcpServers") or {})
        if declared != composed_servers:
            problems.append(
                f"{rel} declares MCP servers {declared}, composition declares "
                f"{composed_servers}"
            )

    # 5. No undeclared files. The build records what it produced; this is the
    #    difference between a payload whose contents were decided at build time
    #    and one that picked up something afterwards. `RELEASE.json` is written
    #    by the release path itself, so it is the one file legitimately present
    #    that the build did not put there — and it is named here rather than
    #    excluded by a wildcard, so the exemption cannot grow by accident.
    recorded = manifest.get("members")
    if not isinstance(recorded, list):
        problems.append("build manifest records no member list; undeclared files "
                        "in this payload cannot be distinguished from declared ones")
    else:
        expected = set(recorded) | {TREE_MANIFEST}
        actual = {str(p.relative_to(rev_dir)) for p in rev_dir.rglob("*") if p.is_file()}
        for rel in sorted(actual - expected):
            problems.append(f"file present that the build did not produce: {rel}")

    return {"ok": not problems, "problems": problems,
            "tree_hash": fingerprint(rev_dir) if not problems else None}


def _l2_module_of(name: str) -> str:
    """`fleet_mcp/node/connector.py` -> `fleet_mcp.node.connector`; else empty."""
    if not name.endswith(".py"):
        return ""
    parts = name[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _l2_matches(path: str, pattern: str) -> bool:
    if pattern.startswith("*") and "/" not in pattern:
        return fnmatchcase(path.rsplit("/", 1)[-1], pattern)
    return fnmatchcase(path, pattern) or fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def _l2_tree_digest(entries) -> str:
    stream = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return hashlib.sha256(stream.encode("utf-8")).hexdigest()


def _l2_layer_problems(kind: str, names) -> list[str]:
    """The separation rule, per kind — the whole point of releasing them apart."""
    problems: list[str] = []
    if kind == L2_NODE:
        denied = sorted({name for name in names
                         if _l2_module_of(name) in L2_NODE_DENIED_MODULES
                         or name.startswith(L2_NODE_DENIED_PREFIXES)})
        if denied:
            problems.append(f"a node artifact must not carry hub-side {denied}")
        modules = {_l2_module_of(name) for name in names}
        unpinned = sorted((modules - set(L2_NODE_EXCLUSIVE)
                           - set(L2_NODE_SHARED_SURFACE)) - {""})
        if unpinned:
            problems.append(f"new hub coupling in the node artifact: {unpinned}")
    else:
        leaked = sorted(name for name in names
                        if name.startswith(L2_HUB_DENIED_PREFIXES))
        if leaked:
            problems.append(f"a hub artifact must not carry device-side {leaked}")
        forbidden = sorted(name for name in names
                           if any(_l2_matches(name, pattern)
                                  for pattern in L2_HUB_DENIED_PATTERNS))
        if forbidden:
            problems.append(f"a hub artifact must not carry machine-specific {forbidden}")
        secrets = sorted(name for name in names
                         if not name.endswith((".py", ".pyi"))
                         and any(_l2_matches(name, pattern)
                                 for pattern in L2_HUB_SECRET_PATTERNS))
        if secrets:
            problems.append(f"a hub artifact must not carry credential-shaped {secrets}")
    return problems


def _l2_declared_modules(files: dict[str, bytes]) -> set[str]:
    """Every third-party top-level module the payload imports."""
    measured: set[str] = set()
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    for name, data in files.items():
        if not name.endswith(".py"):
            continue
        try:
            tree = ast.parse(data.decode("utf-8", "replace"))
        except SyntaxError as exc:
            raise ValueError(f"{name} does not parse: {exc}") from exc
        package = name.split("/")[:-1]
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(package[: len(package) - (node.level - 1)])
                    modules = [".".join(base + ([node.module] if node.module else []))]
                else:
                    modules = [node.module or ""]
            for module in modules:
                top = module.split(".")[0]
                if module.startswith("fleet_mcp") or not top:
                    continue
                if top in stdlib or top == "__future__":
                    continue
                measured.add(top)
    return measured


def verify_l2_layer(rev_dir: Path, *, kind: str) -> dict:
    """Check a staged L2 revision, and say separately whether it can stand alone.

    TWO VERDICTS, and the split is the point.

    `ok` answers "is this the artifact it claims to be": schema, one kind, the
    member list against the bytes on disk, every digest, the tree digest, the
    layer separation, the declared-vs-imported dependencies and the platforms.
    That is what `integrations/fleet-v1-l2-release/l2_release.py` (PR #431)
    implements, and the two must agree.

    `installable` answers a different question: "may a revision directory be
    pointed at this?" Both L2 payloads measured on 2026-10-28 import the
    repository's L0 `fleet/` package and do not carry it, so their manifests say
    `l2.standalone: false`. An artifact like that is *correct* and is not
    *releasable* — promoting it would install something that raises at import on
    a machine without this checkout. Registering the kinds must not turn that
    into a green light, so `cmd_promote` refuses on `installable`, by name, with
    NOT_STANDALONE.

    Reading the payload: this never extracts, never imports and never writes.
    """
    problems: list[str] = []

    try:
        manifest = read_artifact_manifest(rev_dir)
    except SystemExit as exc:
        # A refusal about the manifest is a finding about this revision, not a
        # crash — the same reasoning `verify_plugin` states.
        return {"ok": False, "problems": [str(exc)], "tree_hash": None,
                "installable": False, "standalone": False}

    kinds = manifest.get("kinds") if manifest else None
    if kinds != [kind]:
        problems.append(f"{ARTIFACT_MANIFEST} declares kinds {kinds!r}, expected [{kind!r}]")
    if not isinstance(manifest, dict):
        return {"ok": False, "problems": problems or ["manifest is not an object"],
                "tree_hash": None, "installable": False, "standalone": False}

    if manifest.get("composition") != L2_COMPOSITION:
        problems.append(f"composition is {manifest.get('composition')!r}, "
                        f"expected {L2_COMPOSITION!r}")
    if manifest.get("name") != L2_NAMES[kind]:
        problems.append(f"name is {manifest.get('name')!r}, expected {L2_NAMES[kind]!r}")
    version = str(manifest.get("version", ""))
    if not L2_VERSION_RE.match(version):
        problems.append(f"version {version!r} is not <major>.<minor>.<patch>[+<cachebuster>]")

    # --- the member list, against the bytes ---------------------------------
    listed = manifest.get("members")
    recorded = manifest.get("member_sha256")
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        problems.append("members is not a list of paths")
        listed = []
    if not isinstance(recorded, dict):
        problems.append("member_sha256 is missing; a payload whose bytes are not "
                        "recorded is not a weaker artifact, it is an unsigned one")
        recorded = {}
    if len(listed) != len(set(listed)):
        problems.append("members lists a path more than once")

    # Path safety BEFORE joining anything: a member list is a document, and a
    # document that says `../../etc/passwd` must not be resolved to see if it
    # exists.
    safe_listed: list[str] = []
    for name in listed:
        if not isinstance(name, str) or not name or name.startswith("/") \
                or ".." in Path(name).parts:
            problems.append(f"member name {name!r} is not a relative path inside the payload")
        else:
            safe_listed.append(name)

    on_disk = {str(p.relative_to(rev_dir)) for p in rev_dir.rglob("*") if p.is_file()}
    on_disk.discard(ARTIFACT_MANIFEST)
    on_disk.discard(TREE_MANIFEST)
    missing = sorted(set(safe_listed) - on_disk)
    extra = sorted(on_disk - set(safe_listed))
    if missing:
        problems.append(f"the payload is missing declared member(s) {missing}")
    if extra:
        problems.append(f"file present that the manifest did not declare: {extra}")

    files: dict[str, bytes] = {}
    for name in safe_listed:
        path = rev_dir / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        files[name] = data
        want = recorded.get(name)
        if not isinstance(want, str) or not want:
            problems.append(f"{name} has no recorded digest")
        elif hashlib.sha256(data).hexdigest() != want:
            problems.append(f"{name} does not match the manifest entry")

    tree_digest = _l2_tree_digest([{"path": name, "sha256": recorded.get(name, "")}
                                   for name in safe_listed])
    if (manifest.get("source") or {}).get("tree_sha256") != tree_digest:
        problems.append("source.tree_sha256 does not describe this member list")

    # --- separation, dependencies, platforms ---------------------------------
    problems.extend(_l2_layer_problems(kind, set(safe_listed)))

    # A vendored tool schema is a JSON DOCUMENT that answers what the running CLI
    # should be asked. Source files are deliberately out of scope: the provider's
    # own module names both markers because it is the thing that forwards the
    # schema, and a rule that flagged it would refuse every correct payload.
    for name in safe_listed:
        data = files.get(name)
        if data is None or not name.endswith(".json") or len(data) > 262_144:
            continue
        try:
            value = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        text = json.dumps(value)
        if L2_SCHEMA_MARKER in text and L2_SCHEMA_TOOL_HINT in text:
            problems.append(f"{name} looks like a vendored CloudCLI tool schema; the "
                            f"provider forwards the running CLI's schema and must not "
                            f"ship a copy that can disagree with it")

    l2 = manifest.get("l2") or {}
    declared = set((l2.get("requires") or {}).get("modules") or [])
    external = set(l2.get("external_in_repo") or [])
    try:
        imported = _l2_declared_modules(files)
    except ValueError as exc:
        problems.append(str(exc))
        imported = set()
    undeclared_imports = sorted(imported - declared - external)
    if undeclared_imports:
        problems.append(f"the payload imports {undeclared_imports}, which its manifest "
                        f"does not declare; it would install into an ImportError")

    for dependency in l2.get("depends_on") or []:
        wanted = f"{str(dependency).replace('.', '/')}.py"
        if wanted not in files:
            problems.append(f"depends_on names {dependency!r}, which the payload does not carry")

    platforms = list(l2.get("platforms") or [])
    unsupported = sorted(set(platforms) - set(L2_PLATFORMS[kind]))
    if unsupported:
        problems.append(f"{kind} declares unsupported platform(s) {unsupported}")
    if not set(L2_PLATFORMS[kind]) <= set(platforms):
        problems.append(f"{kind} must declare {list(L2_PLATFORMS[kind])}, declared {platforms}")

    # --- the second verdict, kept OUT of `problems` --------------------------
    #
    # `ok` answers "is this the artifact it claims to be"; `installable` answers
    # "may a revision directory be pointed at it". Folding the second into the
    # first would make a payload that is exactly what the build produces fail
    # verification — and would put the release path at odds with the build's own
    # verifier, which believes it. The two are separate fields because they are
    # separate facts.
    standalone = l2.get("standalone") is True and not external
    standalone_reasons: list[str] = []
    if not standalone:
        missing_packages = sorted(external)
        if missing_packages:
            standalone_reasons.append(
                f"the payload imports in-repo package(s) {missing_packages} that it "
                f"does not carry; it cannot run without this checkout")
        else:
            standalone_reasons.append(
                "the manifest does not claim standalone, so nothing here says the "
                "payload can run on its own")

    return {"ok": not problems, "problems": problems,
            "tree_hash": fingerprint(rev_dir) if not problems else None,
            "installable": standalone, "standalone": standalone,
            "standalone_reasons": standalone_reasons,
            "external_in_repo": sorted(external)}


def _refuse_if_not_installable(report: dict, revision: str) -> bool:
    """True when the caller must stop. The release path's own fail-closed point."""
    if report.get("installable", True):
        return False
    reasons = report.get("standalone_reasons") or []
    detail = reasons[0] if reasons else "the artifact is not installable on its own"
    print(f"REFUSED     {revision}: NOT_STANDALONE — {detail}", file=sys.stderr)
    print("            The artifact is verified and may be staged; it is not "
          "installable, so `current` is not moved.", file=sys.stderr)
    return True


def _native_package_verifier():
    """`serverosd/verify_native_runtime.py`, loaded from this checkout.

    NOT a second copy of it. That module already refuses a digest mismatch, a
    size mismatch, a member that is not 0755, a symlinked file or `bin/`, a
    duplicate row, a path outside `bin/`, a second actuator, a promoted
    actuator, a renamed member and a missing reserved path — checked by the
    tests that ship with it. A release-time re-implementation would be a second
    opinion free to disagree with the one the build ran, which is the failure
    this repository keeps finding under other names.

    Loaded by path rather than imported, because `serverosd/` is a build-side
    directory and not a package on `sys.path`. `release.py` already depends on
    this checkout for `runtime/paths.py`, so the dependency is the same shape as
    the one that was already there.
    """
    path = Path(__file__).resolve().parents[1] / "serverosd" / "verify_native_runtime.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "_serveros_native_package_verifier", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE it runs, and not decoratively. `module_from_spec` does
    # not insert the module into `sys.modules`, and the verifier defines a
    # `@dataclass` — which resolves its own module through `sys.modules` and
    # raises `AttributeError: 'NoneType' object has no attribute '__dict__'`
    # when the entry is absent. Caught by the first promote this path ran.
    sys.modules[spec.name] = module
    # Loading a source module writes a `.pyc` beside it unless told not to. That
    # is a write outside the runtime root, performed by the release tool, and
    # this file's own rule is that a native release touches nothing outside its
    # revision directory. Benign individually; not something to leave true by
    # accident.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def verify_native_runtime(rev_dir: Path) -> dict:
    """Check a staged native runtime revision before anything points at it.

    Two questions, and the first one is not asked here.

    1. *Is every member the bytes the package says it is?* — answered by
       `serverosd/verify_native_runtime.py`, which is the authority the build
       already ran. This function does not restate a digest, a mode or a role.

    2. *Is the revision the package and nothing else?* — asked here, from the
       package's own member list, the same rule `verify_plugin` applies to a
       plugin. A staging copy of a build directory carries `obj/` and `tests/`
       if the operator pointed at the build root instead of the release payload;
       that is a revision whose contents nobody declared, and the revision is
       immutable once staged, so it is refused before rather than deleted after.

    There is deliberately no third question. A native runtime has no unit, no
    CLI shim and no state root: nothing outside the revision directory is
    installed, so nothing outside it can be checked — and `cmd_promote` never
    renders a unit or calls `daemon-reload` for this kind.

    One asymmetry worth stating rather than discovering. `stage()` copies with
    `copytree(..., symlinks=False)`, so a symlink in the ARTIFACT becomes a
    regular file before this function ever runs. A `--dry-run` therefore refuses
    a linked member that a real promote accepts — with the same bytes, because
    the link was dereferenced. The promoted content is the same either way, but
    the two invocations do not agree, and that is `stage()`'s behaviour for
    every kind rather than something this clause introduces.
    """
    problems: list[str] = []

    manifest_path = rev_dir / PACKAGE_MANIFEST
    package = None
    try:
        # `encoding="utf-8"` explicitly, because the default is the locale's and
        # a payload built on one host must read the same on another. `ValueError`
        # rather than `json.JSONDecodeError`, because undecodable bytes raise
        # `UnicodeDecodeError` — a `ValueError` that is NOT a `JSONDecodeError`,
        # so the narrower except would let it escape as a traceback instead of a
        # refusal. Both were reachable: a `PACKAGE.json` containing `[]` or a
        # stray byte killed `promote` with a stack trace.
        package = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"{PACKAGE_MANIFEST} is not readable JSON: {exc}")
    if package is not None and not isinstance(package, dict):
        # Refused here rather than handed to the delegate: the delegate has its
        # own "is an object" check, but this function reads `schema` before it
        # gets there, and a verifier that crashes on a malformed input reports
        # nothing about it.
        problems.append(
            f"{PACKAGE_MANIFEST} is a JSON {type(package).__name__}, not an object")
        package = None

    if package is not None:
        declared_schema = package.get("schema")
        if declared_schema != NATIVE_PACKAGE_SCHEMA:
            problems.append(
                f"{PACKAGE_MANIFEST} declares schema {declared_schema!r}, expected "
                f"{NATIVE_PACKAGE_SCHEMA!r}; a differently-shaped package manifest "
                f"would be read as if its fields meant the same thing"
            )

        verifier = _native_package_verifier()
        if verifier is None:
            # Not a skip. A release path that cannot run the package's verifier
            # has no finding to report, and "no findings" is not a pass.
            problems.append(
                "serverosd/verify_native_runtime.py is missing from this checkout; "
                "the package cannot be verified and an unverified package is not "
                "a releasable one"
            )
        else:
            # The package verifier prints one line per check because it is
            # written to be read by a person at a terminal. The release path
            # reports findings, not a transcript.
            with contextlib.redirect_stdout(io.StringIO()):
                report = verifier.verify(rev_dir, repo=None)
            problems.extend(report.failures)

        members = package.get("members")
        if not isinstance(members, list):
            problems.append(
                f"{PACKAGE_MANIFEST} records no member list; files the package did "
                f"not declare cannot be told apart from ones it did"
            )
        else:
            # `ARTIFACT.json` is what told this path which verifier to run, and
            # `RELEASE.json` is written by the promotion that follows; both are
            # files this tool reads rather than files the package declared.
            declared = {PACKAGE_MANIFEST, ARTIFACT_MANIFEST, TREE_MANIFEST}
            for row in members:
                if isinstance(row, dict) and isinstance(row.get("path"), str):
                    declared.add(row["path"])
            # Directories a declared path lives under are implied by it; any
            # other entry present is not declared. A FILE-only sweep cannot see
            # an empty directory, and `stage()` copies one — `copytree` keeps
            # empty directories — straight into a revision that cannot be
            # edited afterwards.
            implied_dirs: set[str] = set()
            for rel in declared:
                parts = rel.split("/")[:-1]
                for depth in range(1, len(parts) + 1):
                    implied_dirs.add("/".join(parts[:depth]))
            files: set[str] = set()
            directories: set[str] = set()
            for entry in rev_dir.rglob("*"):
                relative = str(entry.relative_to(rev_dir))
                (directories if entry.is_dir() else files).add(relative)
            for rel in sorted(files - declared):
                problems.append(f"file present that the package did not declare: {rel}")
            for rel in sorted(directories - implied_dirs):
                problems.append(
                    f"directory present that the package did not declare: {rel}")

    return {"ok": not problems, "problems": problems,
            "tree_hash": fingerprint(rev_dir) if not problems else None}


def verify_node_runtime(rev_dir: Path, state_dir: Path) -> dict:
    """Check a staged revision is runnable before anything points at it."""
    problems: list[str] = []

    # The revision is only half of "runnable". The rendered unit tells the
    # daemon to read its registry from `state_dir`, and the daemon exits 2 when
    # it is not there. Existence alone is insufficient: accidentally passing
    # the existing runtime/ child adds runtime/ twice to the registry path.
    if not state_dir.is_dir():
        problems.append(
            f"state root does not exist: {state_dir}\n"
            f"  The rendered unit sets SERVEROS_STATE_DIR to this path, and the "
            f"daemon refuses to start without a registry under it. Seed the state "
            f"root before promoting, or pass --state-dir with this node's real root."
        )
    else:
        # Ask the staged runtime's own resolver and validator with the same
        # state override the unit will use. Do not start a daemon or run probes;
        # an import and read are sufficient to reject an unbootable registry.
        probe = """\
import sys
sys.path.insert(0, sys.argv[1])
import registry
target = registry.default_path()
try:
    registry.Registry(path=target)
except Exception as exc:
    print(f"{target}: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(2)
"""
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
               "SERVEROS_STATE_DIR": str(state_dir)}
        try:
            proc = subprocess.run(
                [sys.executable, "-c", probe, str(rev_dir / "runtime")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, timeout=15,
            )
            if proc.returncode != 0:
                problems.append(f"runtime registry preflight failed for {state_dir}: "
                                f"{proc.stderr.strip()[:400]}")
        except subprocess.TimeoutExpired:
            problems.append(f"runtime registry preflight timed out for {state_dir}")

    template = rev_dir / "runtime" / UNIT_NAME
    if template.is_file():
        try:
            render_unit(template.read_text(), rev_dir, state_dir)
        except ValueError as exc:
            problems.append(str(exc))

    # The delivery worker, checked WHEN PRESENT and not required to be. The
    # split is §24's: the core must start without it, so an artifact that omits
    # the worker is still a runnable node — but an artifact that ships one
    # carrying a placeholder nobody can render is a worker that cannot send, and
    # that is worth failing over before the revision is promoted rather than
    # when the first real alert fires.
    #
    # The conversation here is a sentinel and never reaches a file: this asks
    # whether the TEMPLATE renders, which is a question about the artifact. The
    # value that becomes a real destination is supplied by a person at install
    # time (`alert-worker --conversation`), because it is a fact about a chat
    # and not a fact about a build.
    alert_template = rev_dir / "runtime" / ALERT_SERVICE_NAME
    if alert_template.is_file():
        try:
            render_unit(alert_template.read_text(), rev_dir, state_dir,
                        conversation="template-check")
        except ValueError as exc:
            problems.append(f"runtime/{ALERT_SERVICE_NAME}: {exc}")

    # §8's daily-task template, checked the same way and for the same reason. It
    # is the second unit to carry a placeholder that has no honest default, and
    # the failure it guards against is the quiet one: a template whose
    # destination never renders installs cleanly and runs `--out` with nothing
    # after it.
    daily_template = rev_dir / "runtime" / DAILY_SERVICE_NAME
    if daily_template.is_file():
        try:
            render_unit(daily_template.read_text(), rev_dir, state_dir,
                        workbench_out="template-check")
        except ValueError as exc:
            problems.append(f"runtime/{DAILY_SERVICE_NAME}: {exc}")

    # §17.4's wake routine, checked the same way. It carries the most
    # placeholders of the three and the quietest failure of the three: an
    # unrendered `--parent` leaves the receiver starting a program named after a
    # placeholder, which fails as a `START_FAILED` receipt and reads as a broken
    # launcher rather than as an artifact that cannot be installed.
    wake_template = rev_dir / "runtime" / WAKE_SERVICE_NAME
    if wake_template.is_file():
        try:
            render_unit(wake_template.read_text(), rev_dir, state_dir,
                        wake_parent="/template-check", wake_budget="1",
                        wake_timeout=str(1 + WAKE_MARGIN_SECONDS))
        except ValueError as exc:
            problems.append(f"runtime/{WAKE_SERVICE_NAME}: {exc}")

    # The evening brief's pair, checked the same way. It is the fourth unit to
    # carry a placeholder with no honest default and the only one whose
    # placeholder names a directory ANOTHER unit owns — so the failure this
    # catches is not "a destination nobody chose" but "a chain whose two halves
    # name different directories", which no single file can notice on its own.
    # Both sentinels below are questions about the TEMPLATE and never reach a
    # file: the values that become the real directory and the real destination
    # are supplied by a person at install time.
    evening_template = rev_dir / "runtime" / EVENING_SERVICE_NAME
    if evening_template.is_file():
        try:
            render_unit(evening_template.read_text(), rev_dir, state_dir,
                        intake_run_dir="/template-check",
                        evening_out_root="/template-check")
        except ValueError as exc:
            problems.append(f"runtime/{EVENING_SERVICE_NAME}: {exc}")

    # The entry point must resolve the runtime it ships with. `bin/` and
    # `runtime/` find each other by relative path, so a revision carrying one
    # without the other is a shim that cannot import what it names — a file that
    # exists and still fails, which an existence check alone never catches.
    entry = rev_dir / "bin" / "serverosctl"
    if entry.is_file():
        # PYTHONDONTWRITEBYTECODE, because this probe imports the revision's own
        # modules and would otherwise leave `__pycache__` inside the artifact it
        # is checking. That is not a tidiness problem: `fingerprint` is computed
        # after this runs, so the revision's recorded tree hash was a hash of
        # files the VERIFIER had just created. The clean artifact hashed to
        # 04e9956b and the promoted one to d6ea08ee, both correct, describing
        # the same code — the two-incomparable-numbers failure the fingerprint
        # docstring exists to warn about, produced by the release tool itself.
        #
        # Verification must not alter what it verifies, and an artifact is
        # supposed to be immutable once staged.
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(
            [sys.executable, str(entry), "--help"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        if proc.returncode != 0:
            problems.append(
                f"bin/serverosctl --help exited {proc.returncode}: "
                f"{proc.stderr.strip()[:400]}"
            )

    return {"ok": not problems, "problems": problems,
            "tree_hash": fingerprint(rev_dir) if not problems else None}


def current_revision(composition_root: Path) -> str | None:
    link = composition_root / "current"
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def exec_start_commands(unit: Path) -> list[list[str]]:
    """Every ExecStart this unit declares, in order, each split into arguments.

    The reading half, extracted so that the two questions asked of a unit file
    are asked of ONE reader. A second parser would be a second answer to "what
    does this unit run", and it would fail quietly: the two agree on every unit
    until the one they don't, and the check that was supposed to catch a
    disagreement would be the check that missed it.

    Continuations are joined and comments are never read — the two ways a value
    can appear in a unit file without being the command. A blank `ExecStart=`
    CLEARS the list, which is systemd's own semantics: that is how a drop-in
    removes the command a base unit set, so a reader ignoring it would report a
    command the unit no longer runs.
    """
    if not unit.is_file():
        return []
    section, commands = "", []
    for line in re.sub(r"\\\n", " ", unit.read_text()).splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == "[Service]" and line.startswith("ExecStart="):
            value = line.split("=", 1)[1].strip()
            if not value:
                commands.clear()
                continue
            try:
                commands.append(shlex.split(value))
            except ValueError:
                return []
    return commands


def installed_exec_start(unit: Path) -> list[str]:
    """Read the installed service command, never a path mentioned in a comment.

    This inspects the files we install, not systemd's loaded state or drop-ins.
    Multiple commands or syntax outside our generated units are not agreement —
    so this is the "exactly one" view of `exec_start_commands`, which is the
    same reader and answers the same way about every unit that has one command.
    """
    commands = exec_start_commands(unit)
    if len(commands) != 1:
        return []
    return commands[0]


def wake_installation_problems(args) -> list[str]:
    """Optional until installed; then the receiver and launcher must agree."""
    unit = args.unit_dir / WAKE_SERVICE_NAME
    if not unit.exists() and not unit.is_symlink():
        timer = args.unit_dir / WAKE_TIMER_NAME
        return ["wake service missing"] if timer.exists() or timer.is_symlink() else []
    root = args.runtime_root / args.composition / "current"
    command = installed_exec_start(unit)
    problems = []
    if command[:3] != [str(root / "bin/serverosctl"), "work", "wake"]:
        problems.append("wake ExecStart does not use current/bin/serverosctl work wake")
    if command.count("--parent") != 1:
        return problems + ["wake service has no unambiguous --parent"]
    index = command.index("--parent") + 1
    if index >= len(command) or not Path(command[index]).is_absolute():
        return problems + ["wake service has no absolute Parent path"]
    parent = Path(command[index])
    if not parent.is_file() or not os.access(parent, os.X_OK):
        return problems + [f"wake Parent missing or not executable: {parent}"]
    # Custom --parent remains supported. Its code is owned by the operator;
    # only the launcher installed by this release has a template to compare.
    if parent == args.bin_dir / WAKE_PARENT_NAME:
        template = root / WAKE_PARENT_TEMPLATE
        if not template.is_file():
            problems.append("current revision has no managed wake launcher template")
        else:
            try:
                expected = render_unit(template.read_text(), root, args.state_dir)
                if parent.read_text() != expected:
                    problems.append("wake launcher differs from current revision")
            except (ValueError, OSError) as exc:
                problems.append(f"wake launcher cannot be compared: {exc}")
    return problems


def print_wake_followup(args) -> None:
    problems = wake_installation_problems(args)
    if problems:
        for problem in problems:
            print(f"  wake drift   {problem}")
        print("  action       rerun continuation-wake with this node's existing "
              "--budget and any custom --parent; promotion does not reinstall it")


def cmd_status(args) -> int:
    root = args.runtime_root / args.composition
    cur, prev = current_revision(root), None
    prev_link = root / "previous"
    if prev_link.is_symlink():
        prev = Path(os.readlink(prev_link)).name

    # The kind is read from the receipt the promotion wrote, so `status` reports

    # what was actually released rather than what the reader assumes. A
    # composition can be released as either face, and the two have different
    # meanings for everything below: a plugin has no unit and no shim, and
    # calling that DIVERGENT would report a correct installation as a fault.
    kind, basis = None, "unknown"
    if cur is not None:
        receipt = root / "revisions" / cur / TREE_MANIFEST
        if receipt.is_file():
            recorded = json.loads(receipt.read_text()).get("kind")
            if recorded in ARTIFACT_KINDS:
                kind, basis = recorded, "recorded"

    print(f"runtime root      {root}")
    print(f"current           {cur or '(none)'}")
    print(f"previous          {prev or '(none)'}")
    print(f"kind              {kind or '(unrecorded)'} ({basis})")

    if kind in (PLUGIN, NATIVE_RUNTIME) + L2_KINDS:
        # Neither of these faces installs anything outside the revision
        # directory, and for the same reason: a plugin is loaded by a Claude
        # Code from a registry that is not this tool's to write, and a native
        # runtime is four binaries that this tool stages and points `current` at
        # — it renders no unit, writes no shim and asks systemd for nothing. So
        # the question "do the entry points agree" has one entry point here, and
        # the answer is whether `current` resolves at all.
        #
        # Reporting DIVERGENT instead — which is what the node branch below
        # would do, because there is no unit and no `bin/serverosd` to compare —
        # would call a correct installation a fault.
        face = {PLUGIN: "plugin", NATIVE_RUNTIME: "native runtime",
                L2_NODE: "L2 node", L2_HUB: "L2 hub"}[kind]
        print(f"unit installed    n/a ({face} face)")
        print(f"cli shim          n/a ({face} face)")
        if kind in L2_KINDS:
            # A healthy L2 revision is still not installable while it depends on
            # this checkout. Reporting AGREE here would be the "recognised the
            # kind, called it ready" failure the handoff warns about.
            receipt = root / "revisions" / cur / TREE_MANIFEST if cur else None
            installable = True
            if receipt is not None and receipt.is_file():
                try:
                    staged = json.loads(receipt.read_text())
                    installable = bool((staged.get("installable", True)))
                except (OSError, ValueError):
                    installable = False
            print(f"installable       {'yes' if installable else 'NOT_STANDALONE'}")
        ok = cur is not None and (root / "current").is_dir()
        print(f"agreement         {'AGREE' if ok else 'DIVERGENT'}")
        return 0 if ok else 1

    unit = args.unit_dir / UNIT_NAME
    shim = args.bin_dir / "serverosctl"
    print(f"unit installed    {unit.is_file()}")
    if unit.is_file():
        first = next((l for l in unit.read_text().splitlines()
                      if l.startswith("ExecStart=")), "")
        print(f"  {first}")
    print(f"cli shim          {shim} -> "
          f"{os.readlink(shim) if shim.is_symlink() else '(not a symlink)'}")

    # Compare executable paths, not occurrences of "current" in comments or
    # longer paths. Stable current links are required for the next promotion.
    agree = False
    current = root / "current"
    if cur and current.is_dir() and shim.is_symlink():
        command = installed_exec_start(unit)
        shim_target = Path(os.path.abspath(shim.parent / os.readlink(shim)))
        agree = (command[:1] == [str(current / "bin/serverosd")]
                 and (current / "bin/serverosd").is_file()
                 and shim_target == current / "bin/serverosctl"
                 and shim.is_file())
    wake_problems = wake_installation_problems(args)
    for problem in wake_problems:
        print(f"wake drift        {problem}")
    if not wake_problems:
        wake_installed = (args.unit_dir / WAKE_SERVICE_NAME).is_file()
        print(f"wake installation {'AGREE' if wake_installed else 'not installed (optional)'}")
    agree = agree and not wake_problems
    print("scope             installed files; loaded units/processes not checked")
    print(f"agreement         {'AGREE' if agree else 'DIVERGENT'}")
    return 0 if agree else 1


def cmd_promote(args) -> int:
    root = args.runtime_root / args.composition
    revisions = root / "revisions"

    # Which verifier applies is settled BEFORE anything is copied. `--kind` is a
    # narrowing, not an override: when the artifact declares one kind, that is
    # what runs, and when it declares two the caller has to say which release
    # this is. Nothing here reads the artifact's NAME to decide.
    kind, basis = resolve_kind(args.artifact, declared=args.kind)

    # A dry run verifies the artifact where it was built and stops. It must not
    # stage first and check afterwards: staging writes a revision directory, so
    # a dry run that staged would be a real run with the last step removed —
    # and the next real run would then refuse its own leftovers with "already
    # staged", which is how this was found. Nothing below `--dry-run` is
    # reached, so `revisions/` is not even created.
    if args.dry_run:
        report = verify_revision(args.artifact, args.state_dir, kind=kind)
        if not report["ok"]:
            for problem in report["problems"]:
                print(f"REFUSED {problem}", file=sys.stderr)
            return 1
        # A dry run that answered "would promote" for a payload the real run
        # will refuse would be the same lie in a cheaper place.
        if _refuse_if_not_installable(report, args.revision):
            return 1
        print(f"would promote  {args.revision}  kind={kind} ({basis})  "
              f"tree={report['tree_hash']}")
        return 0

    revisions.mkdir(parents=True, exist_ok=True)
    rev_dir = stage(args.artifact, revisions, args.revision)
    report = verify_revision(rev_dir, args.state_dir, kind=kind)
    if not report["ok"]:
        for problem in report["problems"]:
            print(f"REFUSED {problem}", file=sys.stderr)
        print(f"staged but NOT promoted: {rev_dir}", file=sys.stderr)
        return 1
    if _refuse_if_not_installable(report, args.revision):
        print(f"staged but NOT promoted: {rev_dir}", file=sys.stderr)
        return 1

    # Render before the swap, so that everything which can fail has failed
    # while `current` still points at the revision that is running. Only the
    # node face has a unit to render: skipping this for a plugin is not an
    # omission to be restored later, it is the difference between the kinds.
    unit_text = None
    if kind == NODE_RUNTIME:
        unit_text = render_unit((rev_dir / "runtime" / UNIT_NAME).read_text(),
                                root / "current", args.state_dir)

    previous = current_revision(root)
    if previous and previous != args.revision:
        _symlink_swap(root / "previous", revisions / previous)
    _symlink_swap(root / "current", rev_dir)

    if unit_text is not None:
        args.unit_dir.mkdir(parents=True, exist_ok=True)
        (args.unit_dir / UNIT_NAME).write_text(unit_text)

        args.bin_dir.mkdir(parents=True, exist_ok=True)
        _symlink_swap(args.bin_dir / "serverosctl",
                      root / "current" / "bin" / "serverosctl")

    (rev_dir / TREE_MANIFEST).write_text(json.dumps({
        "composition": args.composition,
        "revision": args.revision,
        "kind": kind,
        "kind_basis": basis,
        "tree_hash": report["tree_hash"],
        # The second verdict, recorded so `status` can report it without
        # re-reading the payload. Absent for the kinds that do not have one.
        **({"installable": report.get("installable", True),
            "standalone": report.get("standalone", True)}
           if kind in L2_KINDS else {}),
        "runtime_root": str(root / "current"),
        "previous": previous,
    }, indent=2) + "\n")

    print(f"promoted       {args.revision}  kind={kind} ({basis})  "
          f"tree={report['tree_hash']}")
    print(f"  previous     {previous or '(none)'}")
    print(f"  current      {root / 'current'}")
    if unit_text is not None:
        print(f"  unit         {args.unit_dir / UNIT_NAME}")
        print(f"  cli shim     {args.bin_dir / 'serverosctl'}")
        print_wake_followup(args)
    if not args.no_reload and unit_text is not None:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        print("  daemon-reload  done")
    return 0


def cmd_rollback(args) -> int:
    root = args.runtime_root / args.composition
    revisions = root / "revisions"
    prev_link = root / "previous"
    if not prev_link.is_symlink():
        raise SystemExit("no previous revision recorded; nothing to roll back to")

    target = Path(os.readlink(prev_link))
    rev = revisions / target.name
    if not rev.is_dir():
        raise SystemExit(
            f"previous revision is recorded but absent: {rev}\n"
            f"A rollback that cannot resolve its target must say so rather than "
            f"leave `current` pointing wherever the failure landed."
        )

    # A rollback has to run the same verifier the promotion did, and it can only
    # learn which one from the receipt that promotion wrote. That is the whole
    # reason `kind` is recorded there: without it a rollback would have to guess,
    # and the guarantee "the previous revision is still promotable" would depend
    # on the guess being right.
    kind, basis = resolve_kind(rev, declared=args.kind)
    report = verify_revision(rev, args.state_dir, kind=kind)
    if not report["ok"]:
        for problem in report["problems"]:
            print(f"REFUSED {problem}", file=sys.stderr)
        return 1
    # The target is verified BEFORE `current` moves, and a target that cannot run
    # on its own is not a rollback — it is a second outage.
    if _refuse_if_not_installable(report, rev.name):
        return 1

    unit_text = None
    if kind == NODE_RUNTIME:
        unit_text = render_unit((rev / "runtime" / UNIT_NAME).read_text(),
                                root / "current", args.state_dir)
    current = current_revision(root)
    if args.dry_run:
        print(f"would roll back to {rev.name} (from {current or '(none)'})  "
              f"kind={kind} ({basis})")
        return 0

    _symlink_swap(root / "current", rev)
    if current:
        _symlink_swap(prev_link, revisions / current)
    if unit_text is not None:
        (args.unit_dir / UNIT_NAME).write_text(unit_text)
        _symlink_swap(args.bin_dir / "serverosctl",
                      root / "current" / "bin" / "serverosctl")

    print(f"rolled back    {current or '(none)'} -> {rev.name}  kind={kind}")
    if unit_text is not None:
        print_wake_followup(args)
    if not args.no_reload and unit_text is not None:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    return 0


def cmd_alert_worker(args) -> int:
    """Install §W's delivery runtime: a oneshot and the timer that fires it.

    Deliberately NOT part of `promote`, and the reason is the same one that
    keeps `--enable-reconcile` out of the daemon's ExecStart: promote places a
    revision and points the node at it, which must keep working on a node whose
    operator has not yet decided where alerts should go. Folding this in would
    either make every promotion depend on knowing a chat id — blocking the core
    release on a delivery question, which §AM refuses for repo governance and
    this file refuses for the same reason — or let promote install a worker with
    no destination and call it done.

    So it is one explicit act, and the act is the one that cannot be undone by a
    reboot: `enable --now` on the timer. Before it, nothing is delivered and the
    alerts stay pending, visible in `serverosctl alert list`. After it, a
    condition that opens is said out loud within five minutes.

    The unit is rendered from the revision `current` resolves to, not from
    wherever this script happens to run, so the worker runs the same code the
    daemon does — the split this whole file exists to remove, one hop further
    out.
    """
    root = args.runtime_root / args.composition
    current = current_revision(root)
    if not current:
        print(f"REFUSED nothing is promoted: {root / 'current'} is not a symlink.",
              file=sys.stderr)
        print("        The worker runs the revision's own `bin/serverosctl`; "
              "install one first.", file=sys.stderr)
        return 1
    rev_dir = root / "revisions" / current

    service_template = rev_dir / "runtime" / ALERT_SERVICE_NAME
    timer_template = rev_dir / "runtime" / ALERT_TIMER_NAME
    for path in (service_template, timer_template):
        if not path.is_file():
            print(f"REFUSED {path} is missing from revision {current}.", file=sys.stderr)
            print("        An artifact built before the delivery runtime existed "
                  "cannot install one.", file=sys.stderr)
            return 1

    try:
        service_text = render_unit(service_template.read_text(), root / "current",
                                   args.state_dir, conversation=args.conversation)
    except ValueError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    # The timer carries no placeholder, and running it through the same
    # renderer anyway is what keeps "which files are rendered" from becoming a
    # second list to maintain.
    timer_text = render_unit(timer_template.read_text(), root / "current", args.state_dir)

    if args.dry_run:
        print(f"would install  {args.unit_dir / ALERT_SERVICE_NAME}")
        print(f"would install  {args.unit_dir / ALERT_TIMER_NAME}")
        print(f"  revision     {current}")
        print(f"  destination  {args.conversation}")
        exec_line = next((l for l in service_text.splitlines()
                          if l.startswith("ExecStart=")), "")
        print(f"  {exec_line}")
        if not args.no_enable:
            print(f"would enable   {ALERT_TIMER_NAME}")
        return 0

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / ALERT_SERVICE_NAME).write_text(service_text)
    (args.unit_dir / ALERT_TIMER_NAME).write_text(timer_text)
    print(f"installed      {args.unit_dir / ALERT_SERVICE_NAME}")
    print(f"installed      {args.unit_dir / ALERT_TIMER_NAME}")
    print(f"  revision     {current}")
    print(f"  destination  {args.conversation}")

    if args.no_reload:
        return 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if args.no_enable:
        print("  not enabled. Nothing is delivered until you enable it:")
        print(f"    systemctl --user enable --now {ALERT_TIMER_NAME}")
        return 0
    # `enable --now` starts the timer AND runs nothing immediately: a timer is
    # armed for its next OnCalendar match. The first delivery therefore happens
    # at the next :04/:09/… and not at install time, which is the right default
    # — installing a delivery path should not send a card nobody asked for.
    done = subprocess.run(["systemctl", "--user", "enable", "--now", ALERT_TIMER_NAME],
                          check=False)
    print(f"  enabled      {ALERT_TIMER_NAME} "
          f"({'ok' if done.returncode == 0 else f'exit {done.returncode}'})")
    if done.returncode != 0:
        print("  The unit and the timer are installed and the timer is NOT "
              "running. Alerts stay pending — `serverosctl alert list` shows "
              "them — and nothing was sent.", file=sys.stderr)
        return 1
    return 0


def cmd_daily_task(args) -> int:
    """§8 — install the daily render, and leave it DISABLED.

    The template §8 asks for: installable, manually verifiable, off. It is one
    subcommand rather than two because the only difference between installing it
    and arming it is one flag, and splitting them would let an install path
    exist that nobody had ever seen arm.

    The polarity is the opposite of `alert-worker --no-enable`, deliberately.
    There, enabling is the act that starts telling a person things, so it is the
    default and NOT enabling is the flag. Here, enabling is the act §8 puts on
    the approved list, so the flag is `--enable` and its absence is the default —
    an install that arms a timer nobody approved would be this file doing the
    thing it exists to refuse.

    Manual verification, which is the point of shipping it this way:

        systemctl --user start serveros-daily.service     # run it once, now
        journalctl --user -u serveros-daily.service -n 40 # what it did
        ls <--out>                                        # the set it left

    and running it does not require the timer at all — the unit is a oneshot and
    the ExecStart line printed below can be typed.
    """
    root = args.runtime_root / args.composition
    current = current_revision(root)
    if not current:
        print(f"REFUSED nothing is promoted: {root / 'current'} is not a symlink.",
              file=sys.stderr)
        print("        The render runs the revision's own `bin/serverosctl`; "
              "install one first.", file=sys.stderr)
        return 1
    rev_dir = root / "revisions" / current

    service_template = rev_dir / "runtime" / DAILY_SERVICE_NAME
    timer_template = rev_dir / "runtime" / DAILY_TIMER_NAME
    for path in (service_template, timer_template):
        if not path.is_file():
            print(f"REFUSED {path} is missing from revision {current}.", file=sys.stderr)
            print("        An artifact built before the daily template existed "
                  "cannot install one.", file=sys.stderr)
            return 1

    try:
        service_text = render_unit(service_template.read_text(), root / "current",
                                   args.state_dir, workbench_out=str(args.out))
    except ValueError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    timer_text = render_unit(timer_template.read_text(), root / "current", args.state_dir)

    # The template's own claim, checked against the destination rather than
    # asserted in a comment: the renderer refuses to write into a directory that
    # is not a checkout, so an install pointing `--out` back inside the revision
    # would produce a unit that fails every morning. Caught here, where the
    # operator is still looking, instead of at 08:17 tomorrow.
    resolved_out = args.out.resolve()
    if resolved_out == rev_dir.resolve() or rev_dir.resolve() in resolved_out.parents:
        print(f"REFUSED --out {args.out} is inside the revision {rev_dir}.", file=sys.stderr)
        print("        A render is refused there by name, so the timer would fail "
              "every morning. Choose a directory outside the artifact.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"would install  {args.unit_dir / DAILY_SERVICE_NAME}")
        print(f"would install  {args.unit_dir / DAILY_TIMER_NAME}")
        print(f"  revision     {current}")
        print(f"  destination  {args.out}")
        exec_line = next((l for l in service_text.splitlines()
                          if l.startswith("ExecStart=")), "")
        print(f"  {exec_line}")
        print("  NOT enabled  (--enable arms it)")
        return 0

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / DAILY_SERVICE_NAME).write_text(service_text)
    (args.unit_dir / DAILY_TIMER_NAME).write_text(timer_text)
    print(f"installed      {args.unit_dir / DAILY_SERVICE_NAME}")
    print(f"installed      {args.unit_dir / DAILY_TIMER_NAME}")
    print(f"  revision     {current}")
    print(f"  destination  {args.out}")

    if args.no_reload:
        print("  not enabled. Nothing is rendered on a schedule until you arm it:")
        print(f"    systemctl --user enable --now {DAILY_TIMER_NAME}")
        print(f"  or run it once, by hand:")
        print(f"    systemctl --user start {DAILY_SERVICE_NAME}")
        return 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if not args.enable:
        print("  installed and NOT enabled. §8 lists enabling a daily task as an "
              "act needing separate approval, so this install does not do it:")
        print(f"    systemctl --user enable --now {DAILY_TIMER_NAME}")
        print(f"  run it once, by hand, without arming anything:")
        print(f"    systemctl --user start {DAILY_SERVICE_NAME}")
        return 0
    done = subprocess.run(["systemctl", "--user", "enable", "--now", DAILY_TIMER_NAME],
                          check=False)
    print(f"  enabled      {DAILY_TIMER_NAME} "
          f"({'ok' if done.returncode == 0 else f'exit {done.returncode}'})")
    if done.returncode != 0:
        print("  The unit and the timer are installed and the timer is NOT "
              "running. Nothing is rendered on a schedule; `systemctl --user "
              "status " + DAILY_TIMER_NAME + "` says why.", file=sys.stderr)
        return 1
    return 0


def intake_run_dir_of(unit: Path) -> str | None:
    """The `--run-dir` an installed `serveros-intake.service` runs with, or None.

    Read out of the installed FILE rather than asked of systemd, because the
    question is what the unit says and `systemctl show` answers with what systemd
    parsed — correct, but only on a node where the unit is loaded, and this has
    to answer about the file sitting beside the one being installed.

    THE COMMANDS COME FROM `exec_start_commands`, the reader this file already
    uses. It follows a continued ExecStart and never reads a path out of a
    comment — the two ways a unit file can name something without running it —
    so this question and the "is this exactly the command we install" question
    cannot answer differently about one unit.

    It asks about EVERY command and not about the first, because the unit this
    is pointed at has three: `serveros-intake.service` refreshes a document,
    syncs a chat, and THEN runs the recipe. A check that read only the first
    line would pass on a fixture and be wrong about the unit it exists for.

    None means "cannot be established", undivided between absent and ambiguous.
    A unit with no `--run-dir` and a unit whose `--run-dir` is two different
    directories are both units this command must refuse to compare against, and
    naming which of the two it is changes nothing about what the caller does.
    """
    found = []
    for tokens in exec_start_commands(unit):
        for index, token in enumerate(tokens):
            if token == "--run-dir" and index + 1 < len(tokens):
                found.append(tokens[index + 1])
            elif token.startswith("--run-dir="):
                found.append(token[len("--run-dir="):])
    if len(set(found)) != 1:
        return None
    return found[0]


def cmd_evening_brief(args) -> int:
    """§8's second half — install the evening alignment, and leave it DISABLED.

    `serveros-intake.timer` prepares at 19:20 and sends nothing; this pair turns
    that preparation into a brief that leaves the machine at 20:00. It ran on
    this node already and the product did not carry it, so this command's first
    job is to make it possible for a node that is not this one to have one at
    all.

    THE INSTALL IS INERT, like `daily-task` and unlike `alert-worker`: an
    enabled timer here sends a person a card every evening, which is an act
    somebody approves rather than a side effect of an install. `--enable` arms
    it and its absence is the default.

    TWO REQUIRED ARGUMENTS, AND THE SECOND IS THE INTERESTING ONE.
    `--out-root` is where the night's brief lands and has no honest default for
    the same reason `--out` on `daily-task` has none. `--intake-run-dir` is not a
    destination at all: it is the run directory `serveros-intake.service` writes,
    named again here because this unit composes tonight's brief out of it. The
    two units are related by that string and by nothing systemd knows about, so
    when the intake unit is installed beside this one this command READS it and
    refuses if the two disagree. That is the one check a single unit file cannot
    make about itself, and it is the reason this is a command rather than two
    files an operator copies.
    """
    root = args.runtime_root / args.composition
    current = current_revision(root)
    if not current:
        print(f"REFUSED nothing is promoted: {root / 'current'} is not a symlink.",
              file=sys.stderr)
        print("        The brief is composed by the revision's own "
              "`bin/serverosctl`; install one first.", file=sys.stderr)
        return 1
    rev_dir = root / "revisions" / current

    service_template = rev_dir / "runtime" / EVENING_SERVICE_NAME
    timer_template = rev_dir / "runtime" / EVENING_TIMER_NAME
    for path in (service_template, timer_template):
        if not path.is_file():
            print(f"REFUSED {path} is missing from revision {current}.", file=sys.stderr)
            print("        An artifact built before the evening template existed "
                  "cannot install one.", file=sys.stderr)
            return 1

    # The coupling, checked against the unit that is actually installed rather
    # than asserted in a comment. `serveros-intake.service` is NOT part of this
    # product and is not required to be installed — a node that never runs an
    # intake has no chain here to break — so this is a check WHEN PRESENT, on the
    # same footing as the alert template above.
    intake_unit = args.unit_dir / "serveros-intake.service"
    if intake_unit.is_file():
        theirs = intake_run_dir_of(intake_unit)
        if theirs is None:
            print(f"REFUSED {intake_unit} is installed and this command cannot "
                  f"find a --run-dir in it.", file=sys.stderr)
            print("        The two units are related by that directory and by "
                  "nothing systemd knows about, so an install that cannot "
                  "compare them cannot say the chain is intact. Read the unit, "
                  "or move its ExecStart onto one line.", file=sys.stderr)
            return 1
        if theirs != str(args.intake_run_dir):
            print(f"REFUSED --intake-run-dir {args.intake_run_dir} is not the "
                  f"directory {intake_unit.name} runs with.", file=sys.stderr)
            print(f"        That unit says: {theirs}", file=sys.stderr)
            print("        Nothing in systemd relates the two, so this would "
                  "install a unit composing from a directory nothing writes: at "
                  "20:00 it refuses an alignment.json that is not from today and "
                  "the night's brief is never sent.", file=sys.stderr)
            return 1

    try:
        service_text = render_unit(service_template.read_text(), root / "current",
                                   args.state_dir,
                                   intake_run_dir=str(args.intake_run_dir),
                                   evening_out_root=str(args.out_root))
    except ValueError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    timer_text = render_unit(timer_template.read_text(), root / "current", args.state_dir)

    # The template's own claim, checked against the destination: the brief is
    # composed out of the revision's entry point, and a destination inside the
    # revision would be replaced wholesale by the next update.
    resolved_out = args.out_root.resolve()
    if resolved_out == rev_dir.resolve() or rev_dir.resolve() in resolved_out.parents:
        print(f"REFUSED --out-root {args.out_root} is inside the revision {rev_dir}.",
              file=sys.stderr)
        print("        A hash-verified revision is replaced on the next update, "
              "so every back number of the brief would go with it.", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"would install  {args.unit_dir / EVENING_SERVICE_NAME}")
        print(f"would install  {args.unit_dir / EVENING_TIMER_NAME}")
        print(f"  revision     {current}")
        print(f"  composes from {args.intake_run_dir}")
        print(f"  destination  {args.out_root}")
        print(f"  destination class, asked of the contract rather than decided "
              f"here:")
        print(f"    serverosctl path admit {args.out_root}")
        exec_line = next((l for l in service_text.splitlines()
                          if l.startswith("ExecStart=")), "")
        print(f"  {exec_line}")
        if not args.enable:
            print("  NOT enabled  (--enable arms it)")
            print("  nothing is sent until the environment file names a "
                  "recipient:")
            print(f"    {args.unit_dir / 'evening-alignment.env'}")
        return 0

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    args.out_root.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / EVENING_SERVICE_NAME).write_text(service_text)
    (args.unit_dir / EVENING_TIMER_NAME).write_text(timer_text)
    print(f"installed      {args.unit_dir / EVENING_SERVICE_NAME}")
    print(f"installed      {args.unit_dir / EVENING_TIMER_NAME}")
    print(f"  revision     {current}")
    print(f"  composes from {args.intake_run_dir}")
    print(f"  destination  {args.out_root}")

    if args.no_reload:
        print("  not enabled. The brief is composed and sent once a day when "
              "the timer is armed:")
        print(f"    systemctl --user enable --now {EVENING_TIMER_NAME}")
        print(f"  or compose one now, without arming anything and without "
              f"sending (no recipient configured ⇒ it writes the brief and "
              f"exits 2):")
        print(f"    systemctl --user start {EVENING_SERVICE_NAME}")
        return 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if not args.enable:
        print("  installed and NOT enabled. Enabling this timer sends a person a "
              "card every evening, so it is an act somebody approves rather than "
              "a side effect of an install:")
        print(f"    systemctl --user enable --now {EVENING_TIMER_NAME}")
        print("  compose one now, without arming and without sending:")
        print(f"    systemctl --user start {EVENING_SERVICE_NAME}")
        return 0
    done = subprocess.run(["systemctl", "--user", "enable", "--now", EVENING_TIMER_NAME],
                          check=False)
    print(f"  enabled      {EVENING_TIMER_NAME} "
          f"({'ok' if done.returncode == 0 else f'exit {done.returncode}'})")
    if done.returncode != 0:
        print("  The unit and the timer are installed and the timer is NOT "
              "running. Nothing is composed on a schedule; `systemctl --user "
              "status " + EVENING_TIMER_NAME + "` says why.", file=sys.stderr)
        return 1
    return 0


def cmd_continuation_wake(args) -> int:
    """§17.4 · install the wake routine: a oneshot and its timer, DISABLED.

    THE HALF THAT WAS MISSING. `runtime/continuation.py` writes what a waiting
    Parent leaves behind, and until this command existed nothing on the node
    filled its `RECEIVERS` — so a checkpoint was durable, readable, and resumed
    only by a person. The product's sentence for that state («checkpoint已保存，
    待续») was true, and this is the act that changes what is true.

    IT IS NOT PART OF `promote`, for the same reason `alert-worker` is not: a
    promotion places a revision and points the node at it, and that must keep
    working on a node whose operator has not decided whether this node wakes
    anything at all. Arming a routine that starts agent processes is a separate
    act.

    A LAUNCHER IS INSTALLED, AND IT COMES FROM THE REVISION. `--parent` used to
    be required, on the argument that what a woken Parent is is a fact about this
    node's agents rather than about a build. That argument was right about a
    node's AGENT RUNTIME and wrong about everything else: the posture — no shell,
    this wake's adapter and nothing else, `--strict-mcp-config` — is a decision
    this product makes, and leaving it to be hand-written per node is how it ends
    up hand-written differently, or not at all. So the revision ships
    `runtime/serveros-wake-parent`, this command renders it and installs it to
    `--bin-dir`, and the unit points at that. `--parent` still names a different
    Parent for a node that has one, and is then used verbatim.

    ONE EXECUTABLE AND NOT A COMMAND LINE. Either path is validated as an
    absolute executable with no whitespace and no `@`. Two readers have to agree
    about it — systemd splits `ExecStart=` into argv, and `runtime/wake.py` reads
    the rendered unit back to decide whether a receiver is `wired` — and anything
    that has to survive both correctly is anything that will one day be read
    differently by one of them.

    TWO BOUNDS, AND THEY ARE THE SAME TWO `runtime/wake.py` APPLIES. `--budget`
    is the longest one Parent may run as far as this node is concerned. The
    grant's own `budget.wall_seconds` is the other, and the receiver uses the
    smaller. `TimeoutStartSec=` is rendered to `--budget` plus a margin, because
    it bounds the whole unit cgroup and therefore has to be the OUTER of the two:
    a systemd bound below the receiver's own would kill Parents the receiver
    considered to be inside their grant, and the receipt would say `TIMED_OUT`
    about a wake that never got to finish inside its own limits.

    DISABLED BY DEFAULT, like `daily-task` and unlike `alert-worker`. Arming a
    routine that starts agent processes is an act somebody approves; the install
    prints the one command that arms it and arms nothing itself.
    """
    root = args.runtime_root / args.composition
    current = current_revision(root)
    if not current:
        print(f"REFUSED nothing is promoted: {root / 'current'} is not a symlink.",
              file=sys.stderr)
        print("        The routine runs the revision's own `bin/serverosctl`; "
              "install one first.", file=sys.stderr)
        return 1
    rev_dir = root / "revisions" / current

    service_template = rev_dir / "runtime" / WAKE_SERVICE_NAME
    timer_template = rev_dir / "runtime" / WAKE_TIMER_NAME
    for path in (service_template, timer_template):
        if not path.is_file():
            print(f"REFUSED {path} is missing from revision {current}.", file=sys.stderr)
            print("        An artifact built before the wake half existed cannot "
                  "install one.", file=sys.stderr)
            return 1

    # THE LAUNCHER, WHEN ONE WAS NOT SUPPLIED. Rendered from the revision and
    # installed to `--bin-dir`, so that the posture a woken Parent runs under is
    # the one this release ships rather than one a node's disk remembers. Read
    # the constant's comment for why this is not `--parent` with a default
    # written into the argument parser: it is a file that has to be placed,
    # marked executable and pointed at, and pretending otherwise would put a
    # path in the unit that nothing ever wrote.
    launcher_text = None
    if args.parent is None:
        launcher_template = rev_dir / WAKE_PARENT_TEMPLATE
        if not launcher_template.is_file():
            print(f"REFUSED {launcher_template} is missing from revision "
                  f"{current}.", file=sys.stderr)
            print("        A revision built before the Parent launcher shipped "
                  "cannot install one, and installing the timer without a "
                  "launcher is the failure this command exists to refuse. Pass "
                  "--parent to name this node's own Parent.", file=sys.stderr)
            return 1
        launcher_text = render_unit(launcher_template.read_text(), root / "current",
                                    args.state_dir)
        parent_path = args.bin_dir / WAKE_PARENT_NAME
    else:
        parent_path = args.parent

    parent = str(parent_path)
    problem = None
    if not Path(parent).is_absolute():
        problem = ("the Parent path is not absolute. The unit runs it from a "
                   "user manager whose PATH is whatever the machine happens to "
                   "have, and a Parent found by luck is a receiver that works "
                   "until the day it does not")
    elif any(c.isspace() for c in parent) or "@" in parent:
        problem = ("the Parent path carries whitespace or `@`. Two readers have "
                   "to agree about this value — systemd splitting `ExecStart=` "
                   "and `runtime/wake.py` reading the unit back — so it is one "
                   "path and the arguments belong in a wrapper")
    elif args.parent is not None and not Path(parent).is_file():
        # Only for a path the operator named. The rendered launcher does not
        # exist yet on the first install, which is the whole point: this run is
        # what writes it.
        problem = "--parent is not a file that exists"
    elif args.parent is not None and not os.access(parent, os.X_OK):
        problem = "--parent is not executable"
    if problem:
        print(f"REFUSED {problem}.", file=sys.stderr)
        print("        A receiver that cannot start a Parent is one that fires, "
              "wakes nothing, and exits 0 — a success report for a node whose "
              "checkpoints are not being resumed. The install refuses instead.",
              file=sys.stderr)
        return 1

    budget = args.budget
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        print(f"REFUSED --budget {budget!r} is not a positive number of seconds.",
              file=sys.stderr)
        print("        A receiver with no bound on a Parent either spends "
              "without limit or has a deadline nobody chose.", file=sys.stderr)
        return 1

    try:
        service_text = render_unit(service_template.read_text(), root / "current",
                                   args.state_dir, wake_parent=parent,
                                   wake_budget=str(budget),
                                   wake_timeout=str(budget + WAKE_MARGIN_SECONDS))
    except ValueError as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 1
    # The timer carries no placeholder, and running it through the same renderer
    # anyway is what keeps "which files are rendered" from becoming a second list
    # to maintain.
    timer_text = render_unit(timer_template.read_text(), root / "current", args.state_dir)

    if args.dry_run:
        if launcher_text is not None:
            print(f"would install  {args.bin_dir / WAKE_PARENT_NAME}")
        print(f"would install  {args.unit_dir / WAKE_SERVICE_NAME}")
        print(f"would install  {args.unit_dir / WAKE_TIMER_NAME}")
        print(f"  revision     {current}")
        print(f"  parent       {parent}")
        print(f"  budget       {budget}s per Parent "
              f"(TimeoutStartSec {budget + WAKE_MARGIN_SECONDS}s)")
        exec_line = next((l for l in service_text.splitlines()
                          if l.startswith("ExecStart=")), "")
        print(f"  {exec_line}")
        print("  NOT enabled  (arm it yourself; nothing wakes a checkpoint until "
              "the timer is in the wanted set)")
        return 0

    if launcher_text is not None:
        # Written to a sibling and renamed, for the reason the store's own writer
        # does it: this file is EXECUTED BY A TIMER THAT IS ALREADY FIRING on a
        # node that has installed the routine before, and a Parent started
        # against a half-written launcher is a shell reading its own tail as
        # commands. `os.replace` is atomic within one filesystem, and the
        # temporary lives in the destination directory so it is the same one.
        args.bin_dir.mkdir(parents=True, exist_ok=True)
        target = args.bin_dir / WAKE_PARENT_NAME
        temporary = target.with_name(target.name + ".new")
        temporary.write_text(launcher_text)
        temporary.chmod(0o755)
        os.replace(temporary, target)
        print(f"installed      {target}")

    args.unit_dir.mkdir(parents=True, exist_ok=True)
    (args.unit_dir / WAKE_SERVICE_NAME).write_text(service_text)
    (args.unit_dir / WAKE_TIMER_NAME).write_text(timer_text)
    print(f"installed      {args.unit_dir / WAKE_SERVICE_NAME}")
    print(f"installed      {args.unit_dir / WAKE_TIMER_NAME}")
    print(f"  revision     {current}")
    print(f"  parent       {parent}")
    print(f"  budget       {budget}s per Parent")

    if args.no_reload:
        print("  not enabled. Nothing wakes a checkpoint until you arm it:")
        print(f"    systemctl --user enable --now {WAKE_TIMER_NAME}")
        print("  or run it once, by hand, and read what it decided:")
        print(f"    systemctl --user start {WAKE_SERVICE_NAME}")
        return 0
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    # `daemon-reload` is NOT a restart and this is not enabling: systemd has
    # re-read the unit files, no timer is in the wanted set, and every
    # checkpoint on this node stays exactly as it is.
    print("  installed and NOT enabled — daemon-reload happened, which is not a "
          "restart and not an arming. Arm it with:")
    print(f"    systemctl --user enable --now {WAKE_TIMER_NAME}")
    print("  and check it before trusting it:")
    print(f"    serverosctl work wake --dry-run      # what it would wake, now")
    print(f"    systemctl --user start {WAKE_SERVICE_NAME}")
    print(f"    journalctl --user -u {WAKE_SERVICE_NAME} -n 40")
    return 0


def main() -> int:
    home = Path.home()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--runtime-root", type=Path, required=True,
                       help="distribution root, e.g. $DATA_ROOT/runtime-assets/hwz-server-os")
        p.add_argument("--composition", required=True,
                       help="composition name, e.g. serveros-linux-work")
        # Which verifier applies, when the artifact cannot say on its own. A
        # composition may be released as either face (core and linux-work are
        # both a node runtime and a plugin), and the artifact declares the SET
        # it supports rather than the one this release is — so for those, this
        # is required rather than defaulted. It is checked against the declared
        # set and never against the artifact's name.
        p.add_argument("--kind", choices=list(ARTIFACT_KINDS), default=None,
                       help="which face this release is; must be one the artifact "
                            "declares. Required when it declares more than one.")
        # No environment lookup here, deliberately. The previous default read
        # `SERVEROS_STATE_DIR` then `DATA_ROOT` then `$HOME`, so the value an
        # install produced depended on which of those happened to be exported —
        # and the last of them is silently wrong rather than absent. A node's
        # state root is a property of the node, so it comes from the node's own
        # path contract, and an operator who wants a different one says so.
        p.add_argument("--state-dir", type=Path, default=default_state_dir(),
                       help="rendered into SERVEROS_STATE_DIR in the unit; "
                            "defaults to the path contract's `state` root")
        p.add_argument("--unit-dir", type=Path, default=home / ".config/systemd/user")
        p.add_argument("--bin-dir", type=Path, default=home / ".local/bin")
        p.add_argument("--no-reload", action="store_true",
                       help="skip systemctl --user daemon-reload")
        p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser(
        "promote",
        help="stage a built artifact (node runtime or plugin) and point the "
             "composition at it")
    common(p)
    p.add_argument("--artifact", type=Path, required=True)
    p.add_argument("--revision", required=True,
                   help="serveros revision or release label this artifact was built from")
    p.set_defaults(func=cmd_promote)

    p = sub.add_parser("rollback", help="point the node back at the previous revision")
    common(p)
    p.set_defaults(func=cmd_rollback)

    p = sub.add_parser("status", help="report what the node is actually pointed at")
    common(p)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "alert-worker",
        help="§W · install the alert delivery worker (a oneshot and its timer). "
             "Separate from promote: it needs a destination a person chooses")
    common(p)
    p.add_argument("--conversation", required=True,
                   help="where the worker sends. Required and not defaulted — "
                        "an install without a destination would produce a "
                        "worker that runs, sends nothing, and reports that as "
                        "nothing to do")
    p.add_argument("--no-enable", action="store_true",
                   help="write the units but do not enable or start the timer")
    p.set_defaults(func=cmd_alert_worker)

    p = sub.add_parser(
        "daily-task",
        help="§8 · install the daily workbench render (a oneshot and its timer), "
             "DISABLED. Separate from promote for the same reason, and off by "
             "default because §8 lists enabling a daily task as an approved act")
    common(p)
    p.add_argument("--out", type=Path, required=True,
                   help="where the daily render writes. Required and not "
                        "defaulted: the default is the revision's own "
                        "`workbench/`, which is a hash-verified directory the "
                        "next update replaces wholesale, and which the renderer "
                        "refuses to write into from an installation")
    p.add_argument("--enable", action="store_true",
                   help="ALSO enable and start the timer. Absent by default, so "
                        "the install is inert until somebody arms it — the "
                        "opposite polarity from `alert-worker --no-enable`, "
                        "because §8 puts a daily task on the approved list and "
                        "putting it on the list is the whole reason it exists")
    p.set_defaults(func=cmd_daily_task)

    p = sub.add_parser(
        "evening-brief",
        help="§8's second half · install the 20:00 evening alignment (a oneshot "
             "and its timer), DISABLED. Separate from promote because it needs "
             "two paths a person chooses, and off by default because arming it "
             "sends a person a card every evening")
    common(p)
    p.add_argument("--out-root", type=Path, required=True,
                   help="where the night's brief lands, one directory per "
                        "Beijing day beneath it. Required and not defaulted: a "
                        "destination this command chose would be a directory "
                        "nobody picked, written to every evening, reported as "
                        "sent. `serverosctl path admit <path>` says which class "
                        "the contract reads a candidate as")
    p.add_argument("--intake-run-dir", type=Path, required=True,
                   help="the run directory `serveros-intake.service` writes. NOT "
                        "a destination — a directory another unit owns, named "
                        "here because this unit composes out of it. Refused if "
                        "it disagrees with the installed intake unit, because "
                        "nothing in systemd relates the two")
    p.add_argument("--enable", action="store_true",
                   help="ALSO enable and start the timer. Absent by default: "
                        "this timer sends a card, so arming it is an approved "
                        "act and not a side effect of installing the files")
    p.set_defaults(func=cmd_evening_brief)

    p = sub.add_parser(
        "continuation-wake",
        help="§17.4 · install the continuation wake routine (a oneshot and its "
             "timer), DISABLED. Separate from promote because the Parent command "
             "line is a fact about this node and not about a build")
    common(p)
    p.add_argument("--parent", type=Path, default=None,
                   help="the executable a wake starts, as ONE absolute path. "
                        "Omit it and the revision's own `runtime/"
                        "serveros-wake-parent` is rendered and installed to "
                        "--bin-dir and used, which is the continuation posture "
                        "this product ships — no shell, this wake's adapter as "
                        "the only ServerOS surface. Pass a path only when this "
                        "node's Parent is something else. Either way the value "
                        "has to be ONE path: a waker with a Parent that cannot "
                        "be started fires on its timer, wakes nothing and exits "
                        "0, which reads exactly like a node with nothing to "
                        "resume. Arguments belong in a wrapper script")
    p.add_argument("--budget", type=int, required=True,
                   help="the longest one Parent may run, in seconds. Required: "
                        "the receiver clamps each attempt to the smaller of this "
                        "and the grant's own budget.wall_seconds, and the unit's "
                        "TimeoutStartSec is rendered from it")
    p.set_defaults(func=cmd_continuation_wake)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
