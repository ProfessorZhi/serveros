#!/usr/bin/env python3
"""Answer one question: is the runtime actually running the code we wrote?

Why this file exists
--------------------
Every other check in this repository verifies a *source* artifact: tests pass,
`build.py --check` is clean, the bundle is well formed. None of them can answer
"which revision is the Agent actually loading?", because that is a fact about
**this machine's installed state**, not about the repository.

The gap is not hypothetical. On 2026-09-11 the skeleton truth matrix found that
`space/` and `relations/` were implemented, tested and green, declared in
`plugin/bundle.yaml`, and asserted by `build.py`'s own tests to ship — while the
installed runtime contained neither. Nothing was broken and nothing reported
anything. The discovery took a hand-written `diff` against `site-packages`.

So the rule this module enforces:

    The drift detector must not run inside the component it inspects.

That is why this lives here and not as a `serverosctl` subcommand. `serverosctl`
*is* the drifting component on this machine — the installed `workengine` is a
non-editable copy in a virtualenv that no build step refreshes. A doctor command
shipped inside it would have reported its own stale opinion of itself. A verifier
has to stand outside the thing it verifies, which is why this file lives in the
source tree and reads the installed state as data.

What it does NOT do
-------------------
No network. No LLM. No database. No full-disk scan. No mutation of any kind —
every path below is opened read-only, and the one subprocess it runs is
`git rev-parse`. Output contains no clock reading, so two runs over an unchanged
machine produce identical bytes and can be compared.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

EXCLUDED_DIRS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

BUNDLE_NAME = "hwz-server-os"
CLAUDE_PLUGIN_KEY = "hwz-server-os@hwz-server-os"
CODEX_MARKETPLACE = "hwz-server-os-local"

OK = "OK"
SOURCE_RUNTIME_DRIFT = "SOURCE_RUNTIME_DRIFT"
BUILT_RUNTIME_DRIFT = "BUILT_RUNTIME_DRIFT"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def _load_build(serveros_repo: Path):
    """Import build.py so this file reuses ITS definition of artifact identity.

    Reimplementing the expected file set here would create a second definition
    of "what should be installed", and two definitions of one thing is the
    failure mode this whole checkpoint is about. `plan()` is the authority.

    Loaded from `serveros_repo` rather than from this file's own directory, so
    that the answer describes the CANONICAL source even when this script is run
    from a worktree. `build.repo_root()` then resolves to that checkout, which
    is what makes the recomputation meaningful — it must read what canonical
    says, not what the caller happens to have on disk.
    """
    path = serveros_repo / "scripts/build.py"
    spec = importlib.util.spec_from_file_location("serveros_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace").strip()


def checkout_state(repo: Path) -> tuple[bool, list[str]]:
    """(is_a_git_checkout, uncommitted_paths).

    A missing repository is a fact to REPORT, not an exception to raise. The
    first version of this file called `.splitlines()` on the None that `_git`
    returns for a non-repo and crashed — an inspector that dies on an unexpected
    machine tells you nothing about that machine.
    """
    status = _git(repo, "status", "--porcelain")
    if status is None:
        return False, []
    return True, status.splitlines()


def _iter_files(root: Path):
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            if Path(name).suffix in EXCLUDED_SUFFIXES:
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            yield path.relative_to(root), path


def tree_hash(root: Path) -> str | None:
    """Deterministic content hash of a directory tree.

    Hash of sorted (relative path, sha256 of bytes) pairs — so it is stable
    across machines, mtimes, and walk order, and two trees that differ only in
    a path name or a byte produce different values.
    """
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    found = False
    for rel, path in _iter_files(root):
        found = True
        digest.update(str(rel).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest() if found else None


def tree_drift(expected: dict[Path, bytes], root: Path) -> list[str]:
    """Name every difference between an expected file set and a directory.

    Content, not just presence: an installed copy with the right file names and
    stale bytes is exactly the failure this module exists to catch, and a
    presence-only check would call it clean.
    """
    if not root.is_dir():
        return [f"missing tree {root}"]
    reasons: list[str] = []
    actual = {rel for rel, _ in _iter_files(root)}
    expected_rels = set(expected)
    reasons += [f"missing {rel}" for rel in sorted(expected_rels - actual)]
    reasons += [f"unexpected {rel}" for rel in sorted(actual - expected_rels)]
    for rel in sorted(expected_rels & actual):
        if (root / rel).read_bytes() != expected[rel]:
            reasons.append(f"content {rel}")
    return reasons


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def read_claude_install(plugins_json: Path) -> dict:
    """Claude's own record of what is installed and where it was installed from."""
    if not plugins_json.is_file():
        return {}
    try:
        data = json.loads(plugins_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("plugins", {}).get(CLAUDE_PLUGIN_KEY) or []
    return entries[0] if entries else {}


def read_codex_install(config_toml: Path) -> dict:
    """Codex records activation in config.toml and materializes into a cache dir.

    Parsed by hand rather than with tomllib/tomli: this must run under the same
    interpreter that runs the rest of these scripts, and the two keys needed are
    unambiguous single-line assignments. A full TOML parser would be a new
    dependency for two string reads.
    """
    out = {"enabled": False, "marketplace_source": None}
    if not config_toml.is_file():
        return out
    section = None
    for raw in config_toml.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == f"marketplaces.{CODEX_MARKETPLACE}" and line.startswith("source"):
            out["marketplace_source"] = line.split("=", 1)[1].strip().strip('"')
        if section == f'plugins."{BUNDLE_NAME}@{CODEX_MARKETPLACE}"' and line.startswith("enabled"):
            out["enabled"] = line.split("=", 1)[1].strip() == "true"
    return out


def expected_artifact(build, ability_repo: Path, ability_rev: str, output: Path,
                      marketplace_path: Path) -> tuple[dict[Path, bytes], str] | None:
    """Recompute the artifact from current canonical source + the recorded pin.

    This is what makes the question answerable without the build recording its
    own source revision. If the recomputed bytes equal the installed bytes, the
    installed runtime *is* current canonical; if they differ, the difference is
    named path by path. Either way the answer is derived, not asserted.
    """
    try:
        files, manifests, readme, marketplace, version = build.plan(
            build.repo_root() / "plugin/bundle.yaml", ability_repo, ability_rev,
            output, marketplace_path)
    except (OSError, ValueError) as exc:
        return None
    payload = {rel: content for content, rel in files}
    payload.update({rel: build._json_bytes(value) for rel, value in manifests.items()})
    payload[Path("README.md")] = readme.encode()
    return payload, version


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def collect(args) -> dict:
    build = _load_build(args.serveros_repo)
    rows: list[dict] = []

    def row(component, canonical, built, installed, verdict, detail="", note=""):
        rows.append({"component": component, "canonical": canonical,
                     "built": built, "installed": installed,
                     "verdict": verdict, "detail": detail, "note": note})

    # --- 1. the bundle source repository --------------------------------
    serveros_head = _git(build.repo_root(), "rev-parse", "HEAD") or UNKNOWN
    serveros_is_repo, serveros_dirty = checkout_state(build.repo_root())
    if not serveros_is_repo:
        s_verdict, s_detail = UNKNOWN, f"not a git checkout: {build.repo_root()}"
    elif serveros_dirty:
        s_verdict = UNKNOWN
        s_detail = (f"{len(serveros_dirty)} uncommitted path(s) — the installed runtime "
                    "cannot be attributed to a clean revision")
    else:
        s_verdict, s_detail = OK, ""
    row("serveros (bundle source)", serveros_head[:12], "", f"{build.repo_root()}",
        s_verdict, s_detail)

    # --- 2. the control plane repository --------------------------------
    engine_head = _git(args.engine_repo, "rev-parse", "HEAD") or UNKNOWN
    engine_is_repo, engine_dirty = checkout_state(args.engine_repo)
    if not engine_is_repo:
        e_verdict, e_detail = UNKNOWN, f"not a git checkout: {args.engine_repo}"
    elif engine_dirty:
        e_verdict = UNKNOWN
        e_detail = f"{len(engine_dirty)} uncommitted path(s)"
    else:
        e_verdict, e_detail = OK, ""
    row("work-engine (canonical)", engine_head[:12], "", "", e_verdict, e_detail)

    # --- 3. installed workengine vs canonical workengine ----------------
    installed_engine = args.engine_installed
    engine_verdict, engine_detail = UNKNOWN, ""
    if not installed_engine.is_dir():
        engine_verdict = MISSING
        engine_detail = f"no installed package at {installed_engine}"
    else:
        inst_hash = tree_hash(installed_engine)
        canon_hash = tree_hash(args.engine_repo / "workengine")
        if canon_hash is None:
            engine_detail = "canonical workengine/ not found"
        elif inst_hash == canon_hash:
            engine_verdict = OK
        else:
            engine_verdict = SOURCE_RUNTIME_DRIFT
            diffs = tree_drift({rel: (args.engine_repo / "workengine" / rel).read_bytes()
                                for rel, _ in _iter_files(args.engine_repo / "workengine")},
                               installed_engine)
            # Only report the source-side files; an installed copy may simply be
            # older, in which case "unexpected" files are the older set and the
            # missing ones are the whole story.
            named = [d for d in diffs if d.startswith("missing")]
            engine_detail = (f"installed != canonical ({len(named)} missing path(s)"
                             f"{'; e.g. ' + ', '.join(named[:3]) if named else ''})")
    row("workengine (installed)", engine_head[:12], "",
        f"{installed_engine}  sha256={tree_hash(installed_engine) or 'NONE'}",
        engine_verdict, engine_detail)

    # --- 4/5/6. the artifact, at each hop of the chain -------------------
    claude = read_claude_install(args.claude_plugins_json)
    codex = read_codex_install(args.codex_config)

    ability_rev = args.ability_rev or claude.get("gitCommitSha") or ""
    pinned = ability_rev if len(ability_rev) == 40 else ""
    row("ability-management (build pin)", pinned[:12] or UNKNOWN, pinned[:12],
        (claude.get("gitCommitSha") or "")[:12] or UNKNOWN,
        OK if pinned else UNKNOWN,
        "" if pinned else "no full 40-hex pin recorded; cannot recompute the artifact")

    built_ver = installed_ver = UNKNOWN
    manifest_path = args.output / ".claude-plugin/plugin.json"
    if manifest_path.is_file():
        try:
            built_ver = json.loads(manifest_path.read_text()).get("version", UNKNOWN)
        except (OSError, json.JSONDecodeError):
            built_ver = UNKNOWN
    installed_ver = claude.get("version", UNKNOWN)

    row(f"{BUNDLE_NAME} (bundle identity)", "", built_ver, installed_ver,
        OK if built_ver == installed_ver and built_ver != UNKNOWN else SOURCE_RUNTIME_DRIFT,
        "" if built_ver == installed_ver else
        f"runtime-assets built {built_ver} but Claude has {installed_ver} installed")

    # --- 7. does the installed payload equal what canonical builds? ------
    expected = None
    if pinned and args.ability_repo.is_dir():
        got = expected_artifact(build, args.ability_repo, pinned, args.output,
                                args.marketplace_path)
        if got:
            expected, recomputed_version = got
            row(f"{BUNDLE_NAME} (payload reproducibility)", recomputed_version, "",
                installed_ver, OK if recomputed_version == built_ver else SOURCE_RUNTIME_DRIFT,
                "" if recomputed_version == built_ver else
                f"canonical source builds {recomputed_version}, disk has {built_ver} "
                "— the artifact is stale relative to source")

    # The two runtimes nest differently and neither is guessable from the other:
    # Claude's install record gives the payload leaf directly, while Codex adds
    # <cache>/<marketplace>/<plugin>/<version>/. Both levels were gotten wrong
    # once here — first a false MISSING, then a false drift — which is the
    # argument for resolving the Codex leaf by listing rather than by pattern.
    codex_leaf, codex_note = None, ""
    codex_plugin_dir = args.codex_cache / CODEX_MARKETPLACE / BUNDLE_NAME
    if codex_plugin_dir.is_dir():
        versions = sorted(p for p in codex_plugin_dir.iterdir() if p.is_dir())
        if len(versions) == 1:
            codex_leaf = versions[0]
        elif versions:
            # Codex is documented as single-slot: an old generation sitting
            # beside a new one is a fact worth naming, not silently taking the
            # newest of.
            codex_leaf = versions[-1]
            codex_note = (f"{len(versions)} version dirs present "
                          f"({', '.join(p.name for p in versions)}); Codex is single-slot")

    targets = [("runtime-assets (built)", args.output),
               ("Claude plugin cache", Path(claude.get("installPath", "")) if claude.get("installPath") else None),
               ("Codex plugin cache", codex_leaf)]
    for label, root in targets:
        if root is None or not Path(root).is_dir():
            row(f"{BUNDLE_NAME} @ {label}", "", "", MISSING, MISSING, f"not present: {root}")
            continue
        root = Path(root)
        if expected is None:
            row(f"{BUNDLE_NAME} @ {label}", "", "", f"sha256={tree_hash(root) or 'NONE'}",
                UNKNOWN, "cannot verify: artifact not recomputable")
            continue
        drift = tree_drift(expected, root)
        # Core modules are named separately because they are the specific thing
        # this checkpoint exists to land — but only as a NOTE. Whether the
        # absence is a defect depends on whether canonical has them at all, and
        # that is `drift`'s job. Conflating the two made a matching install read
        # as a failure on the first run of this file.
        absent = [m for m in ("context", "space", "relations") if not (root / m).is_dir()]
        note = f"core module(s) absent: {', '.join(absent)}" if absent else ""
        if codex_note and label.startswith("Codex"):
            note = f"{note}; {codex_note}" if note else codex_note
        verdict = OK if not drift else SOURCE_RUNTIME_DRIFT
        detail = f"{len(drift)} drifted path(s); e.g. {', '.join(drift[:3])}" if drift else ""
        row(f"{BUNDLE_NAME} @ {label}", "", "", f"sha256={tree_hash(root) or 'NONE'}",
            verdict, detail, note)

    verdicts = {r["verdict"] for r in rows}
    if SOURCE_RUNTIME_DRIFT in verdicts:
        overall = SOURCE_RUNTIME_DRIFT
    elif verdicts <= {OK}:
        overall = OK
    elif MISSING in verdicts:
        overall = MISSING
    else:
        overall = UNKNOWN

    return {"schema": "serveros/runtime-truth.v1", "verdict": overall, "rows": rows,
            "codex": {"enabled": codex.get("enabled"),
                      "marketplace_source": codex.get("marketplace_source")}}


def render(report: dict) -> None:
    width = max(len(r["component"]) for r in report["rows"])
    print(f"{'component':<{width}}  {'canonical':<14} {'built':<24} {'installed':<44} verdict")
    for r in report["rows"]:
        print(f"{r['component']:<{width}}  {r['canonical']:<14} {r['built']:<24} "
              f"{r['installed']:<44} {r['verdict']}")
        if r["detail"]:
            print(f"{'':<{width}}  !! {r['detail']}")
        if r.get("note"):
            print(f"{'':<{width}}  -- {r['note']}")
    print()
    print(f"VERDICT: {report['verdict']}")


def main() -> int:
    home = Path(os.path.expanduser("~"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serveros-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/serveros"),
                    help="canonical bundle-source checkout whose build.py defines "
                         "what the artifact should contain")
    ap.add_argument("--ability-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ABILITY_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/ability-management"))
    ap.add_argument("--ability-rev", default="",
                    help="build pin; default = the revision Claude's install record carries")
    ap.add_argument("--output", type=Path,
                    default=Path(os.environ.get("SERVEROS_OUTPUT")
                                 or "/opt/applications/data/home-hwz/runtime-assets/hwz-server-os/current"))
    ap.add_argument("--marketplace-path", type=Path,
                    default=Path(os.environ.get("SERVEROS_MARKETPLACE")
                                 or "/opt/applications/data/home-hwz/runtime-assets/hwz-server-os/.agents/plugins/marketplace.json"))
    ap.add_argument("--engine-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ENGINE_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/work-engine"))
    ap.add_argument("--engine-installed", type=Path,
                    default=Path(os.environ.get("SERVEROS_ENGINE_INSTALLED")
                                 or "/opt/applications/data/home-hwz/envs/python/serveros-control-plane/lib/python3.12/site-packages/workengine"))
    ap.add_argument("--claude-plugins-json", type=Path,
                    default=Path(os.environ.get("SERVEROS_CLAUDE_PLUGINS_JSON")
                                 or home / ".claude/plugins/installed_plugins.json"))
    ap.add_argument("--codex-config", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_CONFIG") or home / ".codex/config.toml"))
    ap.add_argument("--codex-cache", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_CACHE") or home / ".codex/plugins/cache"))
    args = ap.parse_args()

    report = collect(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        render(report)
    return 0 if report["verdict"] == OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
