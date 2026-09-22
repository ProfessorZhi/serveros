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
import hashlib
import json
import os
import re
import shutil
import subprocess
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
TREE_MANIFEST = "RELEASE.json"

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
# between the two faces of this product: a node runtime is a daemon with a unit
# and a CLI shim, a plugin is a payload a different Claude Code loads. They share
# the lifecycle — candidate, verify, `revisions/<revision>`, `current`,
# `previous`, receipt, rollback — and they differ in what "verified" means and in
# whether there is anything outside the revision directory to install.
#
# The set is closed and named, so a kind that is not one of these is refused by
# name rather than falling through to whichever branch happens to be last.
NODE_RUNTIME = "node-runtime"
PLUGIN = "plugin"
ARTIFACT_KINDS = (NODE_RUNTIME, PLUGIN)

# What each kind requires of a revision, as data rather than as an if-chain, so
# that `status` can answer "which of these is missing" for a kind it is only
# reading about. The node list is the one `verify_revision` has always checked;
# the plugin list is §9's, expressed in the layout `build.py` actually produces
# (the artifact root IS the plugin payload — there is no nested `dist/`, and
# inventing one here would put a path in the release tool that no build writes).
REQUIRED_MEMBERS = {
    NODE_RUNTIME: ("bin/serverosd", "bin/serverosctl", "runtime/daemon.py",
                   "runtime/cli.py", f"runtime/{UNIT_NAME}"),
    PLUGIN: (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"),
}


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
    """
    skip = set(ignore)
    entries = [p for p in root.rglob("*")
               if p.is_file() and p.relative_to(root).as_posix() not in skip]
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
                workbench_out: str | None = None) -> str:
    """Write a unit template out for THIS node.

    `conversation` is keyword-only and has no default because there is no
    honest one. A unit that names the alert destination and is rendered without
    one would either keep the literal placeholder — and be handed to a provider
    as a chat id — or expand to the empty string, which systemd turns into
    `--conversation` with nothing after it. Both produce a worker that runs,
    fails to send, and reports that as nothing to do. Raising here is the only
    outcome that says what is actually wrong.
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
    left = [p for p in (RUNTIME_PLACEHOLDER, STATE_PLACEHOLDER,
                        CONVERSATION_PLACEHOLDER, WORKBENCH_OUT_PLACEHOLDER)
            if p in rendered]
    if left:
        raise ValueError(f"unit still carries an unrendered placeholder: {left}")
    # And any OTHER placeholder, because the list above is a list of the three
    # this file knows how to fill, and a fourth was added to a unit and not
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


def verify_node_runtime(rev_dir: Path, state_dir: Path) -> dict:
    """Check a staged revision is runnable before anything points at it."""
    problems: list[str] = []

    # The revision is only half of "runnable". The rendered unit tells the
    # daemon to read its registry from `state_dir`, and the daemon exits 2 when
    # it is not there — so a promote onto a state root that does not exist
    # produces a node that cannot start, and this function used to call that a
    # success. Checking the revision and not the state root is precisely the gap
    # that let a HOME-fallback default install a broken unit and report AGREE.
    if not state_dir.is_dir():
        problems.append(
            f"state root does not exist: {state_dir}\n"
            f"  The rendered unit sets SERVEROS_STATE_DIR to this path, and the "
            f"daemon refuses to start without a registry under it. Seed the state "
            f"root before promoting, or pass --state-dir with this node's real root."
        )

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


def cmd_status(args) -> int:
    root = args.runtime_root / args.composition
    cur, prev = current_revision(root), None
    prev_link = root / "previous"
    if prev_link.is_symlink():
        prev = Path(os.readlink(prev_link)).name

    unit = args.unit_dir / UNIT_NAME
    shim = args.bin_dir / "serverosctl"

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

    if kind == PLUGIN:
        # The plugin face has nothing installed outside the revision directory:
        # the installed Claude Code points at `current` through its own plugin
        # registry, and that registry is not this tool's to write. So the
        # question "do the entry points agree" has one entry point here, and the
        # answer is whether `current` resolves at all.
        print("unit installed    n/a (plugin face)")
        print("cli shim          n/a (plugin face)")
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

    # What a reader actually wants from a status command: do the two entry
    # points agree? A node whose supervisor and whose CLI resolve different
    # revisions is the split brain this tool exists to remove, and it is
    # reported as the headline rather than left for the reader to diff.
    agree = False
    if cur and unit.is_file() and shim.is_symlink():
        agree = (str(root / "current") in unit.read_text()
                 and str(root / "current") in os.readlink(shim))
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

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
