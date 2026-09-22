#!/usr/bin/env python3
"""Observed-side architecture conformance check for ServerOS.

Answers a different question from ``runtime_truth.py``:

    runtime_truth : is the runtime running the code we wrote?
    architecture  : do the declarations agree with each other and with the world?

These are two questions and they get two answers, in two fields
(``runtime_status`` and ``architecture_status``). They were observed to differ
on 2026-09-11, and collapsing them into one verdict was the reason 36 drifts
went unnoticed behind three green axes.

Design rules
------------
1. **Every declaration is read via ``git show <ref>:<path>``.** A working tree
   is not an authority. The previous audit produced seven wrong conclusions by
   reading a stale checkout that answered silently. Readings from a working
   tree are labelled ``read_via: observed-worktree`` and are *excluded* from
   canonical equality assertions.
2. **No second parser.** ``validate_contract.py``, ``runtime_truth.py`` and
   ``map_check.py`` are invoked, not reimplemented.
3. **Read-only.** Nothing here writes, moves, or repairs.

This is a Phase-1 warn-only surface: it reports, it does not gate.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import types
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is present in the control-plane venv
    yaml = None

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
CONTRACT_JSON = SKILL_ROOT / "references" / "serveros-contract.json"
TOPOLOGY_JSON = SKILL_ROOT / "references" / "topology-contract.json"

RUNTIME_STATUS_VALUES = ("OK", "UNKNOWN", "DEGRADED")
ARCH_STATUS_VALUES = ("CONFORMANT", "NONCONFORMANT", "INCONCLUSIVE")


# --------------------------------------------------------------------------
# observation primitives
# --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str | None:
    """Run a read-only git query without letting git write anything.

    ``GIT_OPTIONAL_LOCKS=0`` is what makes the "observational read" claim true
    rather than aspirational: ``git status`` otherwise refreshes the index in
    place when stat data looks stale, which rewrites ``.git/index`` and bumps
    the mtime of ``.git`` itself. Caught by the dogfood mutation counter -- a
    checker that changes the thing it checks is not a checker.
    """
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def relation(repo: Path, ref: str, other: str = "HEAD") -> dict:
    """Describe how ``other`` relates to ``ref``.

    ``git rev-list --left-right --count A...B`` prints "<only-in-A>\\t<only-in-B>".
    That raw tuple was read backwards once during the previous audit, which
    turned "23 ahead" into "23 behind". The counts are therefore *named* here
    and the relation is *derived*, so the raw tuple is never exposed.
    """
    out = _git(repo, "rev-list", "--left-right", "--count", f"{ref}...{other}")
    if not out:
        return {"relation": "unknown"}
    try:
        ref_only, other_only = (int(x) for x in out.split())
    except ValueError:
        return {"relation": "unknown"}
    if ref_only == 0 and other_only == 0:
        rel = "equal"
    elif other_only == 0:
        rel = "behind"          # ref has commits the worktree lacks
    elif ref_only == 0:
        rel = "ahead"           # worktree has commits the ref lacks
    else:
        rel = "diverged"
    return {"relation": rel, "ref_only": ref_only, "other_only": other_only,
            "ref_only_means": f"commits in {ref} not in {other}"}


def worktree_state(repo: Path) -> dict:
    porcelain = _git(repo, "status", "--porcelain") or ""
    dirty = [line[3:] for line in porcelain.splitlines() if line.strip()]
    return {"dirty": len(dirty), "paths": dirty[:20]}


def observe_object(repo: Path, ref: str, path: str) -> dict:
    """Read a declaration out of a pinned git object, with full provenance."""
    resolved = (_git(repo, "rev-parse", ref) or "").strip() or None
    head = (_git(repo, "rev-parse", "HEAD") or "").strip() or None
    blob = _git(repo, "show", f"{ref}:{path}")
    wt = worktree_state(repo)
    obs = {
        "repo": str(repo),
        "requested_ref": ref,
        "resolved_sha": resolved,
        "path": path,
        "read_via": "git-object",
        "worktree_head": head,
        "worktree_dirty": wt["dirty"],
        "relation": relation(repo, ref),
        "found": blob is not None,
    }
    obs["content"] = blob
    if head and resolved:
        obs["worktree_is_pinned_revision"] = head.startswith(resolved)
    return obs


def observe_worktree_object(repo: Path, path: str) -> dict:
    """A workspace read. Never usable as a canonical authority."""
    full = repo / path
    try:
        content = full.read_text(encoding="utf-8")
        found = True
    except OSError:
        content, found = None, False
    wt = worktree_state(repo)
    return {
        "repo": str(repo),
        "requested_ref": None,
        "resolved_sha": None,
        "path": path,
        "read_via": "observed-worktree",
        "worktree_head": (_git(repo, "rev-parse", "HEAD") or "").strip() or None,
        "worktree_dirty": wt["dirty"],
        "relation": relation(repo, "HEAD"),
        "found": found,
        "content": content,
        "authority_eligible": False,
    }


def _load(text: str | None, fmt: str):
    if not text:
        return None
    try:
        return json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except Exception:
        return None


def _load_detailed(text: str | None, fmt: str) -> tuple[object, dict | None]:
    """``_load``, except a document that will not parse says why.

    ``_load`` answers ``None`` for every reason at once -- empty text, malformed
    YAML, a JSON body that is valid JSON but not the document expected. Callers
    that only want the parsed value keep using it. A caller that is *reporting*
    on a document cannot: "it did not parse" is not actionable, and on this node
    it is the whole answer. hwz-ops' ``environment.yaml`` fails on a plain
    scalar that begins with a backtick, which YAML reserves -- so the machine
    instance was unreadable, five root tokens were reported unresolved, and
    nothing in the report could distinguish that from five bindings nobody
    wrote.

    Only the parser's own ``problem`` and position are returned, never
    ``str(error)``: that string carries the offending source line, and copying
    document content into a findings report is not this checker's job. Position
    is 1-based here, because it is meant to be opened in an editor.
    """
    if not text:
        return None, {"type": "Empty", "line": None, "column": None,
                      "problem": "the document is empty"}
    try:
        return (json.loads(text) if fmt == "json" else yaml.safe_load(text)), None
    except Exception as error:  # noqa: BLE001 -- any parser failure is a reading
        mark = getattr(error, "problem_mark", None)
        if mark is not None:
            line, column = mark.line + 1, mark.column + 1
        else:
            line, column = getattr(error, "lineno", None), getattr(error, "colno", None)
        problem = getattr(error, "problem", None) or getattr(error, "msg", None)
        return None, {
            "type": type(error).__name__,
            "line": line,
            "column": column,
            # The parser's own words, copied and not interpreted.
            "problem": problem or "the parser gave no reason",
        }


# --------------------------------------------------------------------------
# reused checkers (invoked, never reimplemented)
# --------------------------------------------------------------------------


def run_reused(script: Path, argv: list[str], timeout: int = 300) -> dict:
    """Run an existing checker and return its JSON report, or a UNAVAILABLE note."""
    if not script.exists():
        return {"available": False, "reason": f"absent: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *argv],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    # Parsed unconditionally. This used to be gated on ``"--json" in argv``, and
    # the one caller did not pass that flag -- so ``report`` was always None,
    # ``validator["status"]`` was always None, and CONTRACT_SELFCHECK_FAIL fired
    # on every run regardless of the contract's real state. A gate that always
    # fails is as broken as one that never runs; both are silent about which.
    # The validator emits JSON on stdout whether or not it is asked to.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = None
    return {
        "available": True,
        "script": str(script),
        "exit_code": proc.returncode,
        "report": report,
        "stderr_tail": proc.stderr[-400:] if proc.stderr else "",
    }


def load_evaluator(obs: dict):
    """Execute the shared composition evaluator out of a pinned git object.

    Returns ``(module, None)`` or ``(None, reason)``. It is executed rather
    than imported because the file lives in another repository and is named by
    a *ref*: importing it would mean reading whatever checkout happens to be on
    disk, which is the failure design rule 1 exists to prevent.

    The reason it ships at all is the dependency direction. ``serveros ->
    ability-management``, never the reverse, so the one implementation of the
    composition rule can only live there. This function is the read side of
    that edge; it is deliberately the only way this checker can reach the rule,
    so that "serveros grew its own copy again" is not a state the code can
    quietly arrive at.
    """
    if not obs.get("found") or obs.get("content") is None:
        return None, (f"not present at {obs.get('requested_ref')}: "
                      f"{obs.get('path')} (repository "
                      f"{obs.get('repo')}, resolved "
                      f"{obs.get('resolved_sha')})")
    module = types.ModuleType("ability_composition_evaluator")
    module.__file__ = f"{obs['repo']}@{obs['requested_ref']}:{obs['path']}"
    try:
        exec(compile(obs["content"], module.__file__, "exec"), module.__dict__)
    except Exception as exc:  # a pin that cannot be executed is a finding, not a traceback
        return None, f"{type(exc).__name__}: {exc} (at {module.__file__})"
    if not hasattr(module, "CompositionError") or not hasattr(module, "judge"):
        return None, (f"executed, but it is not the composition evaluator "
                      f"(no `judge`/`CompositionError`): {module.__file__}")
    return module, None


# --------------------------------------------------------------------------
# assertion helpers
# --------------------------------------------------------------------------


def finding(fid, cls, desired, observed, authority, severity, status="OPEN",
            **extra) -> dict:
    row = {
        "id": fid,
        "class": cls,
        "desired": desired,
        "observed": observed,
        "authority": authority,
        "severity": severity,
        "status": status,
    }
    row.update(extra)
    return row


def skill_names(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "SKILL.md").exists())


def manifest_provides(manifest: dict) -> dict:
    """The registry's plugin records, indexed by the ARTIFACT each one is about.

    Three namespaces meet here and none of them is another's. ``provides``
    carries stable identities (``ProfessorZhi/workspace-maintainer``), a
    composition's ``name`` carries an ARTIFACT name (``hwz-server-os-linux-work``),
    and ``bundle.yaml`` carries skill names (``workspace-management``). A set
    comparison across them without resolution is a fake drift -- the rename is
    recorded in ``repository_aliases`` and the ``name``/``aliases`` fields. Each
    member resolves identity -> ability -> name.

    Returns::

        {"records": {artifact_name: [{"id": ..., "members": [...]}, ...]},
         "identities": [...], "names": [...], "unresolved": [...]}

    ``records`` maps a name to a LIST. Two records claiming one artifact
    identity is a fact the report has to be able to state; collapsing it here
    would decide that question by dict insertion order, in the one place a
    reader cannot see the decision being made.

    WHY THE RECORDS ARE INDEXED BY ARTIFACT. They used to be read through one
    hardcoded plugin id, and the union of every composition was compared against
    that one record. While the record happened to BE the union, "everything
    shipped is explainable by the registry" was true by construction -- so the
    comparison could not see either error it existed for: a record listing a
    member of a different artifact (explained away by that artifact) and a record
    omitting one of its own (covered by a sibling record). One union record
    describes three artifacts and is therefore false for each of them.

    The product-level ``identities``/``names`` are still returned, and they are
    still the right answer to "what can this node resolve". They are no longer
    the answer to "is this record true": that is a question about one artifact,
    and the evaluator's ``judge_records()`` decides it.
    """
    abilities = {a.get("id"): a for a in (manifest.get("abilities") or [])}
    records: dict[str, list[dict]] = {}
    identities, names, unresolved = [], [], []
    for entry in (manifest.get("abilities") or []):
        if (entry.get("type") or "") != "plugin":
            continue
        members = []
        for provided in entry.get("provides") or []:
            ident = provided.get("id")
            identities.append(ident)
            ability = abilities.get(ident)
            if ability is None:
                unresolved.append(ident)
            else:
                name = ability.get("name") or str(ident).split("/")[-1]
                members.append(name)
                names.append(name)
        artifact = (entry.get("name") or "").strip()
        if not artifact:
            # A record with no name has no artifact to pair with. It is not
            # silently skipped into the product union either: its members are
            # in `names`, and the pairing layer reports the compositions it
            # cannot be matched to. Falling back to the registry id would look
            # up one identity's record and report another identity's errors.
            continue
        records.setdefault(artifact, []).append(
            {"id": entry.get("id"), "members": sorted(members)})
    return {
        "records": records,
        "identities": identities,
        "names": sorted(set(names)),
        "unresolved": sorted(set(unresolved)),
    }


# --------------------------------------------------------------------------
# contract-driven root resolution
# --------------------------------------------------------------------------

# Roles in ``topology-contract.json`` name a root **token**, never a path:
#   ``{"root": "ARTIFACTS_ROOT", "template": "skills/<skill>/<RUN>"}``
# The instance lives in hwz-ops, and the contract says so in ``roots_source``
# ("Machine instance. Never duplicated here. If this file grew absolute paths,
# the ownership split would be violated."). So a consumer that concatenates
# ``<data_root>/artifacts/skills`` by hand is not reading the contract at all --
# it is reading its own assumption about the contract, and it will keep doing so
# after the contract changes. That is the same declared-vs-actual gap this
# checker exists to find, so the checker must not manufacture it.
#
# Resolution order, and the mode is always reported so a fallback is visible
# rather than silent:
#   declared   token -> ops/workspace/environment.yaml:roots
#   fallback   token -> --data-root / --workspace-root derived defaults
#
# STATE_ROOT is not in environment.yaml's ``roots``: it is contract-derived
# (``state_root.rule`` == ``<DATA_ROOT>/state/serveros``) and checked separately
# by the A4 block, so it is always computed here and never read from disk.

def _ops_roots(ops_repo: Path, ref: str) -> tuple[dict, dict, dict]:
    """Read the machine root instance from hwz-ops, as a git object only.

    Three values out, and the third is why. All four of these used to be the
    same empty dict, so every reader downstream was told the machine declares
    no roots:

      the locator could not be read        ``found: False``
      it was read and would not parse      ``roots_key: "unparsed"``
      it was read and carries no ``roots``  ``roots_key: "absent"``
      it carries ``roots`` but not a map    ``roots_key: "not-a-mapping"``
      it carries an empty ``roots`` map     ``roots_key: "present"``, 0 keys

    The first is a statement about this node's reach. The middle ones are
    statements about the document, and on 2026-09-22 the absent case is the one
    that holds here: hwz-ops' ``environment.yaml`` is ``schema:
    hwz/environment.v2``, it binds role roots through the profile
    (``binding_authority``) and it has no ``roots`` map at all. Collapsed into
    ``{}`` that reading came out as five unresolved tokens, each of which reads
    as a binding somebody forgot to write -- when the actual finding is one
    document whose shape moved and a locator that still expects the old one.
    """
    obs = observe_object(ops_repo, ref, "workspace/environment.yaml")
    if not obs.get("found"):
        return {}, _prov(obs), {"found": False, "roots_key": "unreadable",
                                "schema": None, "keys": 0, "parse_error": None}
    doc, parse_error = _load_detailed(obs.get("content"), "yaml")
    if not isinstance(doc, dict):
        return {}, _prov(obs), {"found": True, "roots_key": "unparsed",
                                "schema": None, "keys": 0,
                                "parse_error": parse_error}
    roots = doc.get("roots")
    if roots is None:
        state = "absent"
    elif not isinstance(roots, dict):
        state = "not-a-mapping"
    else:
        state = "present"
    read = {"found": True, "roots_key": state,
            "schema": doc.get("schema"),
            # Only a mapping has keys. A list here has a length, and calling
            # that a key count would be the same class of lie as `{}` was.
            "keys": len(roots) if isinstance(roots, dict) else 0,
            "parse_error": None}
    return (roots if isinstance(roots, dict) else {}), _prov(obs), read


def root_tokens(topology: dict | None, args) -> dict:
    """Resolve every root token a role may name, plus the mode that resolved it.

    Three sources, checked in this order, and the mode is reported per token:
      declared     the machine instance (hwz-ops environment.yaml#roots)
      derived      ``roots_source.derivations`` -- a RELATIVE rule over
                   DATA_ROOT / WORKSPACE_ROOT, so no absolute path is duplicated
      unresolved   neither; reported as a finding, never guessed
    """
    ops_repo, ref = args.ops_repo.resolve(), args.ops_ref
    declared_roots, ops_prov, source_read = _ops_roots(ops_repo, ref)

    source = (topology or {}).get("roots_source") or {}
    data_root = Path(args.data_root)
    workspace_root = Path(args.workspace_root)

    # ``environment.yaml`` is the machine instance; the two roots below are the
    # only ones the contract marks as required, so they are the resolution base
    # if the instance could not be read at all.
    base = {"DATA_ROOT": data_root, "WORKSPACE_ROOT": workspace_root}
    for key in source.get("required_keys") or ():
        raw = declared_roots.get(key)
        if isinstance(raw, str) and raw:
            base[key] = Path(raw)

    # STATE_ROOT is a contract assertion, not a machine fact: ``state_root.rule``
    # already says ``<DATA_ROOT>/state/serveros`` and A4 checks it separately.
    # Read it from there so the two cannot disagree; derivations is the fallback.
    root_rules = dict(source.get("derivations") or {})
    state_rule = ((topology or {}).get("state_root") or {}).get("rule")
    if state_rule:
        root_rules["STATE_ROOT"] = state_rule

    resolvable = set(base) | set(root_rules)
    for key in source.get("required_keys") or ():
        resolvable.add(key)
    # Every token actually named by a role counts, so a NEW role introducing a
    # NEW token is resolved (or reported) instead of being silently skipped.
    for entry in ((topology or {}).get("roles") or {}).values():
        if isinstance(entry, dict) and entry.get("root"):
            resolvable.add(str(entry["root"]))

    def _expand(expr: str) -> Path | None:
        text = str(expr)
        for name, value in base.items():
            text = text.replace(f"<{name}>", str(value)).replace(f"${name}", str(value))
        if "<" in text or "$" in text:
            return None            # an unexpanded placeholder is not a path
        return Path(text)

    resolved, mode = {}, {}
    for token in sorted(resolvable):
        raw = declared_roots.get(token)
        if isinstance(raw, str) and raw:
            resolved[token] = _expand(raw)
            mode[token] = "declared" if resolved[token] is not None else "unresolved"
            continue
        if token in base:
            resolved[token] = base[token]
            mode[token] = "declared" if token in declared_roots else "fallback"
            continue
        if token in root_rules:
            resolved[token] = _expand(root_rules[token])
            mode[token] = "derived" if resolved[token] is not None else "unresolved"
            continue
        # ``TOKEN/segment`` names a sub-root of TOKEN. Split rather than treat
        # the whole string as an unknown token -- ``DATA_ROOT/caches`` is not a
        # root, it is a directory inside one.
        head, _, rest = token.partition("/")
        if rest and resolved.get(head) is not None:
            resolved[token] = resolved[head] / rest
            mode[token] = mode.get(head, "unresolved")
            continue
        resolved[token] = None
        mode[token] = "unresolved"

    return {
        "paths": resolved,
        "mode": mode,
        "declared_roots": sorted(declared_roots),
        "roots_source": source.get("locator") or "ops/workspace/environment.yaml",
        "provenance": ops_prov,
        # What the source itself said, kept beside what was resolved from it.
        # `declared_roots: []` above cannot distinguish "the document declares
        # none" from "the document was never read"; this can.
        "source_read": source_read,
    }


def roots_source_finding(root_map: dict) -> dict | None:
    """One finding about the roots source, or ``None`` if it yielded a map.

    Five readings, and the status split is whether the bytes were read -- not
    how bad the news is. A document this checker reached and could not use
    (malformed, or carrying some other shape) is a defect with a location, and
    it opened the moment the locator resolved: OPEN / HIGH. A document it could
    not reach is a statement about this node's reach and is recorded rather
    than opened: OBSERVED / MEDIUM, the same way an absent registry is a
    reading and not a defect.

    The consequence -- `ROOT_TOKEN_UNRESOLVED` -- is emitted separately and
    still stands: those tokens really do not resolve. What must not stand is a
    reader having to infer the cause from their number.
    """
    source_read = root_map.get("source_read") or {}
    if source_read.get("roots_key") == "present":
        return None
    reachable = bool(source_read.get("found"))
    return finding(
        "ROOTS_SOURCE_UNREAD", "topology",
        "the contract's roots source yields a roots map",
        {"locator": root_map.get("roots_source"), **source_read},
        "topology-contract.json roots_source",
        "HIGH" if reachable else "MEDIUM",
        status="OPEN" if reachable else "OBSERVED",
        observation_provenance={"roots": root_map.get("provenance")})


def role_path(roots: dict, topology: dict | None, role: str, *,
              substitutions: dict | None = None) -> tuple[Path | None, str]:
    """Expand a role's template from the contract. Returns (path, why).

    This is the only place that turns a role into a filesystem location. A role
    whose token is unresolvable returns ``None`` and a reason -- it never falls
    back to a guessed path.
    """
    entry = ((topology or {}).get("roles") or {}).get(role) or {}
    token, template = entry.get("root"), entry.get("template")
    if not token or not template:
        return None, f"role {role!r} not declared in the topology contract"
    base = (roots.get("paths") or {}).get(token)
    if base is None:
        return None, (f"role {role!r} names root token {token!r}, which is neither in "
                      f"{roots.get('roots_source')} nor derivable from --data-root")
    relative = str(template)
    for name, (want, value) in (substitutions or {}).items():
        relative = relative.replace(f"<{name}>", str(value))
    # For a root-level query the caller wants the *parent* of the templated
    # leaves: strip trailing placeholders (skills/<skill>/<RUN> -> skills).
    parts = []
    for segment in relative.split("/"):
        if segment.startswith("<") and segment.endswith(">"):
            break
        parts.append(segment)
    return base.joinpath(*parts), "ok"


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


def map_projection_axis(args, findings: list[dict]) -> dict:
    """§15: maps are PROJECTIONS of the registries, so they are checked, not trusted.

    Until this axis existed, the check's own `coverage_limits` said in as many
    words: "This check does not read MAP files ... Phase 1 does not compile
    them." A declared limit is honest, but it also means the three maps that
    hand-copy machine facts had no reader anywhere. The compiler now supplies
    one, so the limit is replaced by an assertion.

    Grading, and why it is not uniform:
      MAP_VIEW_UNBOUND        MEDIUM/OBSERVED -- the map restates a machine fact
                              with no generated anchor. Real, but a map is not an
                              authority in this system; it does not deny.
      MAP_VIEW_DRIFT          MEDIUM/OPEN     -- the map states something the
                              registry contradicts. Still not an authority, so
                              still not blocking -- but it is wrong, not merely
                              unanchored.
      MAP_VIEW_SOURCE_*       HIGH/OPEN       -- the registry itself could not be
                              read, so drift CANNOT be assessed. This one blocks:
                              "could not look" must never read as "nothing found".
    """
    import argparse as _argparse
    import importlib.util

    ops_repo = getattr(args, "ops_repo", None)
    if ops_repo is None:
        return {"status": "SKIPPED", "why": "--ops-repo not supplied; maps are a machine-instance fact"}

    here = Path(__file__).resolve().parent
    script = here / "map_projection.py"
    config_path = here.parent / "references" / "map-projections.json"
    if not script.is_file() or not config_path.is_file():
        findings.append(finding(
            "MAP_VIEW_CHECK_UNAVAILABLE", "map-projection",
            "the map projection compiler and its config are present",
            {"script": str(script), "config": str(config_path)},
            "§15 requires regenerate -> diff -> MAP_VIEW_DRIFT; without the "
            "compiler that contract is unenforceable and maps go unread",
            "HIGH", status="OPEN"))
        return {"status": "UNAVAILABLE"}

    spec = importlib.util.spec_from_file_location("_serveros_map_projection", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["_args"] = _argparse.Namespace(
        serveros_repo=getattr(args, "serveros_repo", None),
        ability_repo=getattr(args, "ability_repo", None),
        ops_repo=ops_repo,
        canonical_ref=getattr(args, "canonical_ref", "HEAD"),
        ability_ref=getattr(args, "ability_ref", None),
        ops_ref=getattr(args, "ops_ref", None),
    )
    try:
        report = mod.check(config)
    except Exception as exc:  # a compiler crash must not look like a clean run
        findings.append(finding(
            "MAP_VIEW_CHECK_FAILED", "map-projection",
            "the map projection compiler runs to completion",
            f"{type(exc).__name__}: {exc}",
            "§15", "HIGH", status="OPEN"))
        return {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}

    for f in report["findings"]:
        unbound = f["code"] == "MAP_VIEW_UNBOUND"
        findings.append(finding(
            f["code"], "map-projection",
            "a map region a registry owns is generated and current",
            {"map": f["detail"], "probe": f.get("probe") or {}},
            "§15 machine contract/registries -> compiler -> human maps",
            f["severity"],
            status="OBSERVED" if unbound else "OPEN",
            subject=f.get("detail", ""),
            **({"why_not_denied": "a human map is a projection, not an authority; "
                                   "an unanchored region is reported so its owner "
                                   "can paste the generated block"} if unbound else {}),
            worktree_state=f.get("worktree_state") or {},
            proposed_action=f.get("proposed_action"),
            refresh_via="serverosctl architecture project --out <dir>"))
    return {"status": "CHECKED", "targets": report["targets"],
            "sources": report["sources"]}


def reachability_axis(args, serveros_repo: Path, ref: str,
                      findings: list[dict]) -> dict:
    """§18/§26: what this code can reach, and who produces what it claims to own.

    Two questions, and this axis answers both because they fail the same way —
    a declaration that nothing checks stops being read.

      §18  does the MCP layer touch the host here, and does every fact have one
           producer that can be resolved?
      §26  is there a code path that can destroy something this round must not?

    Grading:
      UNDECLARED_ACTUATOR     HIGH/OPEN   -- new reachability nobody reviewed
      MCP_LAYER_REACHES_HOST  HIGH/OPEN   -- a second producer of a host fact
      SOURCE_UNPARSEABLE      HIGH/OPEN   -- a hit could be hiding in it
      TEST_REACHES_*          HIGH/OPEN   -- a test running a state-changing verb
      PRODUCER_ABSENT         HIGH/OPEN   -- a producer named a path that is not
                                            there, so the namespace is unwritable
      STALE_ACTUATOR          MEDIUM/OPEN -- a permission that outlived its code
      PRODUCER_UNRESOLVABLE   MEDIUM/OPEN -- a producer written as a sentence
                                            that names nothing. Real but not a
                                            breach: the fact may well have a
                                            producer and nobody wrote down which
      MCP_FORBIDDEN_LIST_DRIFT MEDIUM/OPEN -- the policy list has drifted from
                                            the vocabulary it is a policy about

    The declarations are read from the pinned object, like every other
    declaration this checker reads. The CODE is read from the worktree, which is
    the one asymmetry here and it is deliberate: reachability is a property of
    the code being reviewed, and `worktree_head` / `worktree_dirty` are recorded
    in the axis report so a scan of uncommitted code is visible rather than
    implicit.

    Both come from the same repository — the one `--serveros-repo` names, which
    for a default run is the canonical checkout and NOT whichever worktree this
    script happens to be executed from. So a self-consistent run in a branch
    passes `--serveros-repo <worktree>` together with `--canonical-ref
    <that branch>`, and a default run against `main` will report
    REACHABILITY_CONTRACT_ABSENT until the round's contract is merged there.
    That is the honest answer rather than a bug: the pin does not contain the
    contract, so this checker cannot say the code is covered by it.
    """
    import importlib.util

    here = Path(__file__).resolve().parent
    script = here / "mutation_guard.py"
    if not script.is_file():
        findings.append(finding(
            "REACHABILITY_CHECK_UNAVAILABLE", "safety",
            "the mutation guard and its actuator contract are present",
            {"script": str(script)},
            "§26 — without the checker the prohibition is a sentence, and "
            "`runtime/actuator-contract.yaml` is a document nothing reads",
            "HIGH", status="OPEN"))
        return {"status": "UNAVAILABLE"}

    actuators_obs = observe_object(serveros_repo, ref,
                                   "runtime/actuator-contract.yaml")
    state_root_obs = observe_object(serveros_repo, ref,
                                    "fleet/state-root-contract.yaml")
    missing = [o["path"] for o in (actuators_obs, state_root_obs)
               if not o["found"]]
    if missing:
        findings.append(finding(
            "REACHABILITY_CONTRACT_ABSENT", "safety",
            "both contracts are readable from the pinned revision",
            {"absent": missing, "ref": ref},
            "§18 — a checker that cannot read its own contract has not thereby "
            "found the code clean", "HIGH", status="OPEN"))
        return {"status": "UNAVAILABLE"}

    import yaml
    spec = importlib.util.spec_from_file_location("_serveros_mutation_guard",
                                                  script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # The tree to scan is the checker's own repository worktree. `root` is
    # passed so that the module never reaches for the cwd on its own.
    report = mod.check(
        root=serveros_repo,
        actuators=yaml.safe_load(actuators_obs["content"]) or {},
        state_root=yaml.safe_load(state_root_obs["content"]) or {})

    for row in report["findings"]:
        findings.append(finding(
            row["id"], row["class"], row["desired"], row["observed"],
            row["authority"], row["severity"], status="OPEN",
            subject=row.get("subject", "")))

    return {
        "status": report["status"],
        "scanned_from": report["scanned_from"],
        "counts": report["audit"]["counts"],
        "producers": report["producers"]["counts"],
        "declaration_ref": ref,
        "worktree_head": (_git(serveros_repo, "rev-parse", "HEAD") or "").strip(),
        "worktree_dirty": worktree_state(serveros_repo)["dirty"],
        "coverage_limit": (
            "The scan reads the worktree, so it sees uncommitted reachability. "
            "The contracts are read from the pinned object. A run against a "
            "dirty worktree therefore checks a code surface the pin does not "
            "contain, and `worktree_dirty` above says so."),
    }


def collect(args) -> dict:
    findings: list[dict] = []
    observations: dict[str, dict] = {}

    serveros_repo = args.serveros_repo.resolve()
    ability_repo = args.ability_repo.resolve()
    ops_repo = args.ops_repo.resolve()
    ref = args.canonical_ref

    # ---- declaration side (git object only) ------------------------------
    bundle_obs = observe_object(serveros_repo, ref, "plugin/bundle.yaml")
    components_obs = observe_object(serveros_repo, ref, "integration/components.yaml")
    contract_obs = observe_object(serveros_repo, ref,
                                  "skills/serveros/references/serveros-contract.json")
    topology_obs = observe_object(serveros_repo, ref,
                                  "skills/serveros/references/topology-contract.json")
    manifest_obs = observe_object(ability_repo, args.ability_ref,
                                  "abilities-manifest.yaml")
    authority_obs = observe_object(ops_repo, args.ops_ref,
                                   "workspace/serveros/policies/authority.md")
    environment_obs = observe_object(ops_repo, args.ops_ref,
                                     "workspace/environment.yaml")
    observations["declarations"] = {
        "serveros/plugin/bundle.yaml": bundle_obs,
        "serveros/integration/components.yaml": components_obs,
        "serveros/skills/serveros/references/serveros-contract.json": contract_obs,
        "serveros/skills/serveros/references/topology-contract.json": topology_obs,
        "ability-management/abilities-manifest.yaml": manifest_obs,
        "hwz-ops/workspace/serveros/policies/authority.md": authority_obs,
        "hwz-ops/workspace/environment.yaml": environment_obs,
    }
    observations["checker_provenance"] = {
        "architecture_check": {
            "path": str(Path(__file__).resolve()),
            "read_via": "observed-worktree",
            "repo": str(serveros_repo),
            "worktree_head": (_git(serveros_repo, "rev-parse", "HEAD") or "").strip(),
            "worktree_dirty": worktree_state(serveros_repo)["dirty"],
        },
        "note": "The checker's own code is not a declaration and is not compared "
                "for equality; it is recorded so a stale checker is visible.",
    }

    bundle = _load(bundle_obs["content"], "yaml") or {}
    components = _load(components_obs["content"], "yaml") or {}
    manifest = _load(manifest_obs["content"], "yaml") or {}
    contract = _load(contract_obs["content"], "json") or {}

    # ---- A0: every declaration was actually readable ----------------------
    for label, obs in observations["declarations"].items():
        if not obs["found"]:
            findings.append(finding(
                "DECLARATION_UNREADABLE", "declaration-unreadable",
                f"{label} readable at {obs['requested_ref']}",
                "object not found", "§1 authority read discipline", "HIGH",
                observation_provenance=_prov(obs),
            ))

    # ---- A1: composition vs registry --------------------------------------
    # The rule enforced below is NOT implemented here. It is implemented once,
    # in `ability-management`, as `composition.py` -- and this checker EXECUTES
    # THAT FILE READ OUT OF THE PINNED ABILITY REVISION. Two reasons, and the
    # first is the whole point of §2:
    #
    #   * There were two implementations of one rule. This one unioned the
    #     profile files as written; `control_plane.plugin_composition_drift`
    #     read a single file and compared with set equality. They disagreed
    #     about the same inputs, and from inside either one there was no way to
    #     tell which was the rule. A rule stated twice is a rule that will
    #     eventually be stated two ways.
    #   * The dependency direction is serveros -> ability-management, so the
    #     shared implementation can only live in ability-management and be read
    #     from here. Vendoring a copy would rebuild the first problem on a
    #     slower clock; that is what design rule 2 of this file forbids.
    #
    # Reading it from the revision rather than a checkout is what keeps design
    # rule 1 true: a working tree is not an authority. It also means a verdict
    # can name WHICH evaluator produced it, because the revision is recorded in
    # the observation provenance.
    evaluator_obs = observe_object(
        ability_repo, args.ability_ref,
        "skills/ability-management/payload/composition.py")
    observations["declarations"][
        "ability-management/skills/ability-management/payload/composition.py"] = evaluator_obs
    evaluator, evaluator_error = load_evaluator(evaluator_obs)
    if evaluator is None:
        # No fallback to a local copy and no skipping the check. A checker that
        # cannot reach its rule has not thereby found the system correct, and a
        # silent fallback would let the two implementations diverge again the
        # first time this file moved.
        findings.append(finding(
            "COMPOSITION_EVALUATOR_ABSENT", "composition",
            "the pinned ability revision carries "
            "skills/ability-management/payload/composition.py",
            evaluator_error,
            "the composition rule is implemented once, in ability-management; "
            "serveros reads it from the pin and never vendors a copy",
            "HIGH",
            observation_provenance=_prov(evaluator_obs)))

    # `ls-tree` yields paths relative to the repository, not basenames.
    profile_obs: dict[str, dict] = {}
    profile_listing = (_git(serveros_repo, "ls-tree", "-r", "--name-only", ref,
                            "plugin/profiles/") or "")
    for line in sorted(profile_listing.splitlines()):
        rel = line.strip()
        if not rel.endswith(".yaml"):
            continue
        obs = observe_object(serveros_repo, ref, rel)
        profile_obs[rel] = obs
        observations["declarations"][f"serveros/{rel}"] = obs

    # The virtual filesystem the evaluator reads. It is built from the SAME
    # git-object reads the rest of this report uses, so the judgment and the
    # provenance cannot be about different bytes.
    composition_files: dict[str, str] = {}
    if bundle_obs["found"] and bundle_obs["content"] is not None:
        composition_files["plugin/bundle.yaml"] = bundle_obs["content"]
    for rel, obs in sorted(profile_obs.items()):
        if obs["found"] and obs["content"] is not None:
            composition_files[rel] = obs["content"]

    resolved_compositions: list[dict] = []
    composition_errors: list[str] = []
    if evaluator is not None and composition_files:
        for rel in sorted(composition_files):
            try:
                resolved_compositions.append(
                    evaluator.resolve(rel, composition_files))
            except evaluator.CompositionError as exc:
                composition_errors.append(f"{rel}: {exc}")

    if composition_errors:
        # Resolution refusing is not a problem with the registry, and it is not
        # reported as one: an unresolved composition means the question cannot
        # be answered at all, which is a different and louder statement.
        findings.append(finding(
            "COMPOSITION_ILLFORMED", "composition",
            "every composition resolves: supported schema, a declared "
            "identity, a present parent, no cycle, one source per member, "
            "valid recommended statuses, a profile cachebuster",
            composition_errors,
            "serveros owns the composition contract",
            "HIGH",
            observation_provenance={"bundle": _prov(bundle_obs),
                                    "manifest": _prov(manifest_obs)}))

    # Flattened for the report and for the findings below that are NOT part of
    # the shared rule (unknown source, registry scope). Derived from the
    # evaluator's output rather than recomputed, so there is still one place
    # that decides what a composition contains.
    all_skills = [{**s, "composition": c["chain"][0] if c.get("chain") else "?"}
                  for c in resolved_compositions for s in c["skills"]]
    bundle_members = sorted({s["name"] for s in all_skills})
    # `shipped` / `local` are already name lists; `skills` carries the entries.
    ability_members = sorted({n for c in resolved_compositions for n in c["shipped"]})
    local_members = sorted({n for c in resolved_compositions for n in c["local"]})
    # `recommended_members` / `promised_members` are deliberately NOT recomputed
    # here. The evaluator computes both to decide the unreachable and
    # resolvability directions; restating them would be the second
    # implementation this section exists to delete.

    provides = manifest_provides(manifest)

    if provides["unresolved"]:
        findings.append(finding(
            "PROVIDES_IDENTITY_UNRESOLVED", "registry",
            "every provides[].id resolves to an ability entry",
            provides["unresolved"],
            "ability-management/abilities-manifest.yaml", "MEDIUM"))

    # The verdict itself, from the one implementation. Each finding the
    # evaluator returns is rendered with the identifier this report has always
    # used, so a reader (and the tests) keep the same handle on it. The
    # evaluator's OWN id stays in `desired`, so a reader can still tell which
    # rule fired without a second id being invented for it here.
    #
    # The three pairing ids share the drift id deliberately: "this artifact's
    # record and this composition disagree" is one thing to act on, and which of
    # the three it is decides the repair, not the alarm. `composition` and
    # `observed` name the artifact, so the repair is still reachable from the row.
    _JUDGMENT_IDS = {
        "COMPOSITION_MEMBER_UNREGISTERED": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
        "COMPOSITION_MEMBER_UNREACHABLE": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
        "COMPOSITION_EXACT_MEMBER_MISMATCH": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
        "RECOMMENDATION_UNRESOLVABLE": ("RECOMMENDATION_UNRESOLVABLE", "MEDIUM"),
        "COMPOSITION_ARTIFACT_UNNAMED": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
        "ARTIFACT_RECORD_MISSING": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
        "ARTIFACT_RECORD_DUPLICATE": ("BUNDLE_MANIFEST_DRIFT", "HIGH"),
    }
    if evaluator is not None and resolved_compositions:
        if not hasattr(evaluator, "judge_records"):
            # A pin older than the pairing rule. Reported rather than skipped:
            # a checker that cannot reach its rule has not thereby found the
            # system correct, and falling back to the union comparison would
            # restore exactly the judgment this round removed -- silently, and
            # under the evaluator's authority, which is worse than not asking.
            findings.append(finding(
                "COMPOSITION_PAIRING_UNAVAILABLE", "composition",
                "the pinned ability revision's evaluator pairs each composition "
                "with its own artifact's registry record (`judge_records`)",
                f"{evaluator.__file__} exposes "
                f"{'judge' if hasattr(evaluator, 'judge') else 'neither judge nor judge_records'}"
                f"{', but not judge_records' if hasattr(evaluator, 'judge') else ''}",
                "the composition rule is implemented once, in ability-management; "
                "serveros reads it from the pin and never vendors a copy",
                "HIGH",
                observation_provenance=_prov(evaluator_obs)))
        else:
            for judged in evaluator.judge_records(
                    resolved_compositions, records_by_name=provides["records"]):
                fid, severity = _JUDGMENT_IDS.get(
                    judged["id"], (judged["id"], judged["severity"]))
                findings.append(finding(
                    fid, "composition-vs-registry", judged["rule"],
                    f"{judged['id']}={judged['observed']}",
                    judged["owner"], severity,
                    observation_provenance={"bundle": _prov(bundle_obs),
                                            "manifest": _prov(manifest_obs),
                                            "evaluator": _prov(evaluator_obs)}))

    # A member the bundle sources locally that the registry also lists in
    # `provides`.
    #
    # Phase 1 raised this as a HIGH authority conflict and deferred it. The
    # ownership ruling removed the premise: `source: local` in the bundle is a
    # claim about COMPOSITION (which serveros owns), while `provides` in the
    # registry is a record of IDENTITY and DISCOVERY (which ability-management
    # owns). A member can legitimately appear in both, because they are answers
    # to two different questions -- the old check compared them as though they
    # answered the same one.
    #
    # It is kept, at OBSERVED, rather than deleted. What remains genuinely open
    # is narrower and belongs to the registry's owner: whether the bootstrap
    # `serveros` Skill is a *reusable* ability at all, and so whether it belongs
    # in a registry of reusable abilities. That is a question about the registry's
    # scope, not a contest over who owns the composition.
    contested = sorted(set(local_members) & set(provides["names"]))
    if contested:
        findings.append(finding(
            "BOOTSTRAP_ABILITY_REGISTERED", "registry-scope",
            "the reusable-ability registry is scoped to reusable abilities",
            {"bundle.source=local": contested,
             "also_in_registry_provides": contested,
             "bundle_says": "local (composition decision, owned by serveros)",
             "registry_says": "provided (identity/discovery record, owned by "
                              "ability-management)",
             "not_a_canonical_conflict_because":
                "composition and discovery are different questions with "
                "different owners"},
            "plugin ownership ruling (Phase 1.1 A); registry scope belongs to "
            "ability-management",
            "LOW", status="OBSERVED",
            observation_provenance={"bundle": _prov(bundle_obs), "manifest": _prov(manifest_obs)}))

    # BUNDLE_DOUBLE_SOURCE used to be asserted here as well. The evaluator
    # raises on it during resolution (`one ability cannot have two sources`),
    # so it arrives above as COMPOSITION_ILLFORMED. Asserting it a second time
    # from a second computation is how the two implementations started to
    # differ in the first place.
    # ---- A2: contract self-consistency (reuse validate_contract) ----------
    # The existing validator compares the contract against hardcoded literals in
    # its own body, so it can only detect "the JSON was edited wrong". It is
    # still worth running: it is the gate that exists. What it CANNOT do is
    # recorded as a coverage limit below rather than silently trusted.
    # Pair reused checker code with this architecture-check revision. Declaration
    # files are read through explicit Git objects, but checker code is executable
    # machinery: calling <serveros_repo>/... would silently mix a candidate
    # contract/checker with the validator from a stale canonical checkout. That
    # produced a false HIGH finding during convergence dogfood.
    vc = run_reused(HERE / "validate_contract.py", [str(CONTRACT_JSON)])
    validator = {
        "available": vc["available"],
        "status": (vc.get("report") or {}).get("status") if vc.get("report") else None,
        "errors": (vc.get("report") or {}).get("errors") if vc.get("report") else None,
        "coverage_limit": (
            "Every assertion in validate_contract.py compares the contract JSON "
            "against a literal inside the validator. Expected values are written "
            "twice, so it detects a corrupted contract file but cannot detect a "
            "contract that disagrees with the world."),
    }
    if vc["available"] and validator["status"] != "PASS":
        findings.append(finding(
            "CONTRACT_SELFCHECK_FAIL", "contract", "serveros-contract.json validates",
            validator["errors"], "skills/serveros/scripts/validate_contract.py", "HIGH"))
    elif not vc["available"]:
        findings.append(finding(
            "CONTRACT_SELFCHECK_UNAVAILABLE", "contract",
            "skills/serveros/scripts/validate_contract.py is reachable and runs",
            "validate_contract.py could not be executed; the contract self-check "
            "did NOT run, so its silence must not be read as a pass",
            "architecture_check.py A2", "MEDIUM",
            why_not_denied="The validator is a skill script whose path is derived "
                           "from --serveros-repo. Moving it without updating the "
                           "caller turns this gate off silently.",
            retire_condition="removed once the path is contract-derived"))

    # ---- A3: contract vs the machine declaration (authority.md) -----------
    # forbidden_roots is the field with a known history: the contract lists 3,
    # authority.md declares 4 (the fourth being the cwd fallback).
    declared_forbidden = set(contract.get("forbidden_roots") or ())
    auth_text = authority_obs["content"] or ""
    authority_forbids_cwd = "cwd fallback" in auth_text
    if authority_forbids_cwd and "cwd-fallback" not in declared_forbidden:
        findings.append(finding(
            "FORBIDDEN_ROOTS_INCOMPLETE", "contract-vs-authority",
            "serveros-contract.json forbidden_roots covers every root authority.md forbids",
            f"contract={sorted(declared_forbidden)}; authority.md also forbids the cwd fallback",
            "ops/workspace/serveros/policies/authority.md#Root invariants", "MEDIUM",
            observation_provenance={"contract": _prov(contract_obs), "authority": _prov(authority_obs)}))

    # ---- A4: state root --------------------------------------------------
    declared_state_root = None
    for line in (auth_text or "").splitlines():
        if line.strip().startswith("STATE_ROOT="):
            declared_state_root = line.split("=", 1)[1].strip()
    topology = None
    if topology_obs["found"]:
        topology = _load(topology_obs["content"], "json")
    topology_state_root = ((topology or {}).get("state_root") or {}).get("rule")

    # Resolve every root token the contract's roles may name. Done once, here,
    # and every path below that used to be concatenated by hand comes from this.
    root_map = root_tokens(topology, args)
    source_read = root_map.get("source_read") or {}

    # The source, reported on its own, before its consequence. `unresolved`
    # below is a list of tokens that did not resolve; this says whether that is
    # because the machine declares no such roots or because the document the
    # locator names was never read in the first place. Those want opposite
    # actions -- bind tokens, or fix the locator -- so they are not one number.
    source_finding = roots_source_finding(root_map)
    if source_finding is not None:
        findings.append(source_finding)

    unresolved = sorted(t for t, m in root_map["mode"].items() if m == "unresolved")
    if unresolved:
        findings.append(finding(
            "ROOT_TOKEN_UNRESOLVED", "topology",
            "every root token named by a topology role resolves",
            {"unresolved": unresolved, "declared": root_map["declared_roots"],
             "roots_source": root_map["roots_source"],
             "source_read": source_read},
            "topology-contract.json roots_source", "HIGH",
            observation_provenance={"roots": root_map["provenance"]}))

    state_root = (root_map["paths"].get("STATE_ROOT") or Path(args.data_root) / "state/serveros")
    expected_state_root = str(state_root)
    if declared_state_root and declared_state_root.replace("$DATA_ROOT", str(args.data_root)) != expected_state_root:
        findings.append(finding(
            "STATE_ROOT_DECLARATION_DRIFT", "topology",
            f"state root is {expected_state_root}",
            f"authority.md declares {declared_state_root}",
            "authority.md#Root invariants", "HIGH",
            observation_provenance=_prov(authority_obs)))
    if topology_state_root and topology_state_root != "<DATA_ROOT>/state/serveros":
        findings.append(finding(
            "STATE_ROOT_SCHEMA_DRIFT", "topology",
            "topology contract declares state root <DATA_ROOT>/state/serveros",
            topology_state_root, "serveros topology contract schema", "HIGH"))

    # ---- A5: Core-2 and dispatcher ---------------------------------------
    comps = components or {}
    component_names = set((comps.get("components") or {}).keys())
    core = ["serveros", "ability-management"]
    missing_core = [c for c in core if c not in component_names]
    if missing_core:
        findings.append(finding(
            "CORE_ROLE_MISSING", "core",
            f"Core-2 components registered: {core}", f"missing={missing_core}",
            "srv-0003 Core-2 decision", "HIGH"))
    dispatcher = contract.get("dispatcher")
    if dispatcher != "serveros":
        findings.append(finding(
            "DISPATCHER_DRIFT", "core", "serveros is the single dispatcher",
            dispatcher, "serveros-contract.json", "HIGH"))

    # ---- A6: plugin ownership (RULED, so asserted, not deferred) ----------
    # Phase 1 could only report this conflict. The owner has since ruled, so the
    # ruled invariants are now asserted: the composition of the plugin belongs to
    # serveros, and the identity/version/discovery/distribution of reusable
    # abilities belongs to ability-management. One owner each, no overlap.
    #
    # The old detector keyed on the string `plugin-canonical-source`. That string
    # is gone now, and a detector that stops firing because a word vanished is
    # not a check — it is the same "declared but absent" shape this project keeps
    # finding. So the assertion is on the ruled ownership predicate itself.
    so = (comps.get("components") or {}).get("serveros") or {}
    am = (comps.get("components") or {}).get("ability-management") or {}

    def owners_of(concern):
        return sorted(
            name for name, comp in (comps.get("components") or {}).items()
            if concern in (comp.get("owns") or []))

    components_by_name = {
        "serveros": so, "ability-management": am,
    }
    ruled_owners = {
        # concern                          owner                 who may not claim it
        "plugin-composition-canonical":    ("serveros", "ability-management"),
        "plugin-bundle":                   ("serveros", "ability-management"),
        "bootstrap-skill-source":          ("serveros", "ability-management"),
        "reusable-skill-canonical-source": ("ability-management", "serveros"),
    }
    for concern, (want, must_not) in sorted(ruled_owners.items()):
        got = owners_of(concern)
        if got != [want]:
            findings.append(finding(
                "OWNERSHIP_DRIFT", "authority",
                f"{concern} is owned by exactly [{want}]",
                {"owners": got, "ruled": want},
                "plugin ownership ruling (Phase 1.1 A)",
                "HIGH",
                observation_provenance={"components": _prov(components_obs)}))
        # the exclusion is half the point: one owner each, no overlap
        if concern in (components_by_name[must_not].get("owns") or []):
            findings.append(finding(
                "OWNERSHIP_DRIFT", "authority",
                f"{must_not} must not claim {concern}",
                {"claimed_by": must_not},
                "plugin ownership ruling (Phase 1.1 A)", "HIGH"))

    # The ruling retired a word, not just a claim. `skill-source` did not say
    # whether it meant the bootstrap Skill or the reusable ones, and the two have
    # different owners -- that ambiguity is what produced the original conflict.
    retired = "skill-source"
    if any(retired in (comp.get(field) or [])
           for comp in (comps.get("components") or {}).values()
           for field in ("owns", "does_not_own")):
        findings.append(finding(
            "RETIRED_OWNERSHIP_TERM", "authority",
            f"{retired!r} is retired in favour of bootstrap-skill-source / "
            "reusable-skill-canonical-source",
            {name: {f: (comp.get(f) or []) for f in ("owns", "does_not_own")}
             for name, comp in (comps.get("components") or {}).items()
             if retired in (comp.get("owns") or []) + (comp.get("does_not_own") or [])},
            "plugin ownership ruling (Phase 1.1 A)", "HIGH"))

    # ---- A2: exactly one canonical composition, and the transitional surface
    # named as such. ability-management/plugins/hwz-server-os/ is a generated
    # compatibility path that still exists on disk; it must be legible as
    # non-canonical rather than silently countable as a second plugin source.
    bundle_name = bundle.get("name") or "hwz-server-os"
    transitional = ability_repo / "plugins" / bundle_name
    if transitional.is_dir():
        findings.append(finding(
            "TRANSITIONAL_GENERATED_SURFACE", "authority",
            "generated/transitional plugin surfaces are named and are not canonical",
            {"path": str(transitional),
             "canonical_composition": "ProfessorZhi/serveros",
             "status": "transitional — not a second canonical, not deleted this round"},
            "plugin ownership ruling (Phase 1.1 A2)", "MEDIUM", status="OBSERVED"))

    # ---- §12: the artifact template is declared once, and it is the ruled one
    #
    # Two templates, two shapes, and they are not interchangeable:
    #   with an Item   work-items/<project>/<ITEM>/<RUN>   three levels
    #   without one    skills/<skill>/<RUN>                two levels
    # The cancelled template skills/<skill>/<project>/<RUN> inserts a project
    # directory into the no-Item case. It is asserted here rather than trusted,
    # because the resolver that consumed it also declared it correctly -- the
    # declaration and the behaviour disagreed and nothing noticed.
    roles_decl = (topology or {}).get("roles") or {}
    for role_name, want_template, want_requires, forbidden_tokens in (
        ("run-output", "work-items/<project>/<ITEM>/<RUN>",
         ["project", "item", "run"], []),
        ("skill-run-output", "skills/<skill>/<RUN>",
         ["skill", "run"], ["<project>", "<ITEM>"]),
    ):
        entry = roles_decl.get(role_name) or {}
        got_template = entry.get("template")
        got_requires = entry.get("requires")
        token_violations = [t for t in forbidden_tokens if t in (got_template or "")]
        if got_template != want_template or got_requires != want_requires \
                or token_violations:
            findings.append(finding(
                "ARTIFACT_TEMPLATE_DRIFT", "contract-vs-authority",
                f"role {role_name!r} declares template {want_template!r} "
                f"requires {want_requires}",
                {"template": got_template, "requires": got_requires,
                 "forbidden_tokens_present": token_violations},
                "artifact layout ruling (§12)",
                "HIGH",
                observation_provenance={"topology": _prov(topology_obs)}))

    # ---- §12 observed side: runs on disk that the cancelled template would
    # have produced. One exists. It is reported, not moved: a read of an
    # existing Run is never denied, and retirement needs its owner, not a sweep.
    # §13: derived from the contract's ``skill-run-output`` role, not concatenated.
    # The old literal ``<data_root>/artifacts/skills`` was a second, independent
    # copy of the same fact -- if the role's root or template moved, the literal
    # would keep scanning the old tree and report "no project-level runs" forever.
    artifacts_skills, artifacts_skills_why = role_path(root_map, topology, "skill-run-output")
    if artifacts_skills is None:
        findings.append(finding(
            "SKILL_RUN_SCAN_UNAVAILABLE", "topology",
            "the skill-run-output role resolves to a directory that can be scanned",
            artifacts_skills_why, "topology-contract.json roles.skill-run-output",
            "MEDIUM", why_not_denied="An unresolvable role means this scan cannot "
                                     "run; it does not mean the tree is clean."))
    elif artifacts_skills.is_dir():
        project_level = []
        for skill_dir in sorted(artifacts_skills.iterdir()):
            if not skill_dir.is_dir():
                continue
            for inner in sorted(skill_dir.iterdir()):
                if inner.is_dir() and any(
                        g.is_dir() for g in inner.glob("RUN-*")):
                    project_level.append(str(inner.relative_to(artifacts_skills)))
        if project_level:
            findings.append(finding(
                "PROJECT_LEVEL_SKILL_RUN_PRESENT", "topology",
                "no-Item Skill Run sits at skills/<skill>/<RUN>",
                project_level,
                "artifact layout ruling (§12)",
                "MEDIUM", status="OBSERVED",
                why_not_denied="A read of an existing Run is never denied. These "
                               "are pre-ruling paths; retirement is a proposal to "
                               "their owner, not a sweep.",
                retire_condition="owner review; the corrected template is already "
                                 "in force for new runs"))

    # ---- runtime side (separate question, separate verdict) ---------------
    # The two roots this checker resolved are forwarded, so a run aimed at a
    # particular checkout asks about THAT checkout. Everything else the
    # delegate needs it resolves from the same profile this file just read;
    # both refuse rather than guess, so neither can answer about a path the
    # other never looked at.
    rt = run_reused(serveros_repo / "scripts" / "runtime_truth.py",
                    ["--json", "--serveros-repo", str(serveros_repo),
                     "--ability-repo", str(ability_repo)])
    runtime_status = "UNKNOWN"
    runtime_detail = {}
    if rt["available"] and rt.get("report"):
        rep = rt["report"]
        runtime_status = rep.get("release", {}).get("verdict") or rep.get("verdict") or "UNKNOWN"
        runtime_detail = {
            "schema": rep.get("schema"),
            "release_verdict": rep.get("release", {}).get("verdict"),
            "axes": rep.get("release", {}).get("axes"),
            "blocking": rep.get("release", {}).get("blocking"),
            "pin": rep.get("release", {}).get("pin"),
        }
    else:
        runtime_detail = {"reason": rt.get("reason"), "stderr_tail": rt.get("stderr_tail")}

    # ---- topology side: workspace stable roots ---------------------------
    ws_children = sorted(p.name for p in Path(args.workspace_root).iterdir() if p.is_dir()) \
        if Path(args.workspace_root).is_dir() else []
    declared_ws_children = sorted((topology or {}).get("workspace_root_children", {}).keys())
    if declared_ws_children:
        unregistered = [c for c in ws_children if c not in declared_ws_children and not c.startswith(".")]
        if unregistered:
            findings.append(finding(
                "UNREGISTERED_WORKSPACE_ROOT", "topology",
                f"WORKSPACE_ROOT children are {declared_ws_children}",
                unregistered, "serveros topology contract §workspace_root_children",
                "MEDIUM", status="LEGACY_COMPAT",
                why_not_denied="Pre-existing paths are LEGACY_COMPAT / DEFER. "
                               "NO NEW DEBT does not mean DELETE OLD DEBT."))

    # ---- topology side: DATA_ROOT stable roots ---------------------------
    # Was missing entirely, which is why four stable machine roots (logs/
    # software/ tmp/, plus the platform's .vscode-server) went undeclared and
    # unnoticed: the checker enforced the WORKSPACE declaration and left the
    # DATA_ROOT one decorative. A declaration nothing checks is a comment.
    dr_children = sorted(p.name for p in Path(args.data_root).iterdir() if p.is_dir()) \
        if Path(args.data_root).is_dir() else []
    declared_dr_children = sorted(k for k in (topology or {}).get("data_root_children", {})
                                  if not k.startswith("_"))
    if declared_dr_children:
        # WORKSPACE_ROOT physically sits under DATA_ROOT, so it shows up as a
        # child here while being a declared root in its own right. Skipped by
        # name, and the skip is stated rather than filtered silently.
        known_roots = {Path(args.workspace_root).name}
        unregistered_dr = [c for c in dr_children
                           if c not in declared_dr_children and c not in known_roots
                           and not c.startswith(".")]
        if unregistered_dr:
            findings.append(finding(
                "UNREGISTERED_DATA_ROOT", "topology",
                f"DATA_ROOT children are declared in {declared_dr_children}",
                unregistered_dr, "serveros topology contract §data_root_children",
                "MEDIUM", status="LEGACY_COMPAT",
                why_not_denied="§10: a stable root is not deleted because it is "
                               "rare. Declare it, do not remove it.",
                retire_condition="the contract gains the entry, or the root is "
                                 "retired by its owner"))
    # Platform-owned dot-directories (VS Code remote server and friends) are
    # not ServerOS roots and are excluded above by the leading dot. Named here
    # so the exclusion is deliberate rather than an accident of the filter.
    platform_owned = sorted(c for c in dr_children if c.startswith("."))

    # ---- forbidden substitute roots --------------------------------------
    substitute_probes = {
        "workspace/artifacts": Path(args.workspace_root) / "artifacts",
        "workspace/toolchains": Path(args.workspace_root) / "toolchains",
        "~/files": Path.home() / "files",
    }
    present = [name for name, p in substitute_probes.items() if p.exists()]
    if present:
        findings.append(finding(
            "SUBSTITUTE_ROOT_PRESENT", "topology",
            "no substitute root exists", present,
            "authority.md#Root invariants; serveros-contract.json#forbidden_roots",
            "HIGH"))

    # ---- name collisions, reported separately ----------------------------
    roots = {name: Path(p) for name, p in args.runtime_root}
    members_by_root = {name: skill_names(p) for name, p in roots.items()}
    # A name in both the Claude cache and the Codex cache is NORMAL: two
    # runtimes, one plugin. A collision means the same runtime can reach the
    # same name through two roots. Roots are therefore grouped per runtime
    # (the part of the root label before the first "-").
    collisions: dict[str, dict[str, list[str]]] = {}
    for runtime in sorted({name.split("-")[0] for name in roots}):
        seen: dict[str, list[str]] = {}
        for root_name, names in members_by_root.items():
            if not root_name.startswith(runtime + "-"):
                continue
            for n in names:
                seen.setdefault(n, []).append(root_name)
        dupes = {n: r for n, r in sorted(seen.items()) if len(r) > 1}
        if dupes:
            collisions[runtime] = dupes
    if collisions:
        findings.append(finding(
            "NAME_COLLISION", "materialization",
            "within one runtime, each skill name resolves to exactly one root",
            collisions,
            "reported separately from MATERIALIZATION_UNIQUE by design: the "
            "existing axis passes while a name is present in two roots",
            "MEDIUM", status="OBSERVED",
            reported_separately=True,
            note="This finding is intentionally NOT folded into the "
                 "MATERIALIZATION_UNIQUE axis, which answers a different question "
                 "(each member reaches every runtime once)."))

    # ---- §15 map projection drift -----------------------------------------
    map_projection = map_projection_axis(args, findings)

    # ---- §18/§26 reachability and producers -------------------------------
    reachability = reachability_axis(args, serveros_repo, ref, findings)

    # ---- verdicts ---------------------------------------------------------
    arch_findings = [f for f in findings if f["class"] != "materialization"]
    blocking = [f for f in arch_findings
                if f["severity"] == "HIGH" and f["status"] == "OPEN"]
    if not bundle_obs["found"] or not manifest_obs["found"]:
        arch_status = "INCONCLUSIVE"
    elif blocking:
        arch_status = "NONCONFORMANT"
    else:
        arch_status = "CONFORMANT"

    return {
        "schema": "serveros/architecture-check.v1",
        "mode": "warn-only",
        "runtime_status": runtime_status,
        "architecture_status": arch_status,
        "verdict_separation": (
            "runtime_status answers 'is the runtime running the code we wrote'; "
            "architecture_status answers 'do the declarations agree with each "
            "other and with the world'. They are independent on purpose."),
        "runtime_detail": runtime_detail,
        "findings": findings,
        "summary": {
            "findings": len(findings),
            "blocking_high_open": len(blocking),
            "by_status": {s: sum(1 for f in findings if f["status"] == s)
                          for s in sorted({f["status"] for f in findings})},
            "by_class": {c: sum(1 for f in findings if f["class"] == c)
                         for c in sorted({f["class"] for f in findings})},
        },
        "namespace_collisions": collisions,
        "members_by_runtime_root": members_by_root,
        "bundle": {
            "members": bundle_members,
            "ability_management_members": ability_members,
            "local_members": local_members,
            "manifest_provides_identities": provides["identities"],
            "manifest_provides_names": provides["names"],
            "registry_claims_local_members": contested,
        },
        "observations": observations,
        "map_projection": map_projection,
        "reachability": reachability,
        "coverage_limits": [
            "validate_contract.py compares the contract against literals in its "
            "own body (expected values written twice); running it proves the "
            "file was not corrupted, not that the world matches it.",
            "MAP files (TREE-MAP / HWZ-MAP / PLUGIN-MAP) ARE read, since §15 -- "
            "but only inside declared generated blocks, and only against the "
            "registries those blocks project. Map PROSE is not checked and is "
            "not an authority. A map region with no block marker is reported as "
            "MAP_VIEW_UNBOUND, which says 'unanchored', not 'wrong'.",
            "The map axis is a machine-instance check: with no --ops-repo it "
            "reports SKIPPED rather than passing, because there are no maps to "
            "read. Do not read SKIPPED as clean.",
            "Names outside the composed bundle (native ~/.claude/skills, "
            "~/.codex/skills) are reported as collisions only; ownership of a "
            "native-root skill is not decided.",
        ],
    }


def _prov(obs: dict) -> dict:
    return {
        "repo": obs["repo"],
        "requested_ref": obs["requested_ref"],
        "resolved_sha": obs["resolved_sha"],
        "read_via": obs["read_via"],
        "worktree_head": obs["worktree_head"],
        "worktree_dirty": obs["worktree_dirty"],
        "relation": obs.get("relation"),
    }


def render(report: dict) -> None:
    print(f"runtime_status      : {report['runtime_status']}")
    print(f"architecture_status : {report['architecture_status']}")
    print(f"findings            : {report['summary']['findings']} "
          f"(blocking HIGH/open: {report['summary']['blocking_high_open']})")
    for f in report["findings"]:
        line = f"  [{f['severity']:<6}] {f['status']:<12} {f['id']}"
        if f.get("subject"):
            # Several findings share a code once a check runs per-object (three
            # maps, three hits). Without the subject the render shows the same
            # line three times and the reader cannot tell them apart.
            line += f"  <- {f['subject']}"
        print(line)
    if report["namespace_collisions"]:
        print("name collisions:")
        for name, roots in report["namespace_collisions"].items():
            print(f"  {name}: {', '.join(roots)}")


# --------------------------------------------------------------------------
# where this checker looks when nobody says
# --------------------------------------------------------------------------

# There is no built-in path, and the absence is the point. Until §Q6 these
# five arguments fell back to `/opt/applications/data/home-hwz/...` — one
# machine's layout, compiled into a checker whose whole job is to find claims
# that do not describe the machine they are being read on. On Tokyo it did not
# fail loudly: it pointed at a directory that does not exist and reported on
# it, which is this checker manufacturing the exact declared-vs-actual gap it
# exists to find.
#
# The order is flag, then environment, then THIS HOST'S OWN PROFILE, then a
# refusal. The profile is the document the rest of the release already reads
# for this question — `serverosd` takes its network policy from one — and the
# profile's own `roots` block says of itself that these are "the numbers
# `serverosctl path resolve` returns and not a parallel description of them".
# Nothing below knows the name of a machine or of a user.

HOST_PROFILE_ENV = "SERVEROS_HOST_PROFILE"
DEFAULT_HOST_PROFILE = Path("~/.config/serveros/host-profile.yaml")

#: argument -> (environment variable, profile root keys, path under that root).
#:
#: `data_root` reads `identity_home` because this node's profile records
#: `data_root_equals_home` as a fact about itself, in its own words. Where that
#: note is false the profile would say so, and the environment names the root
#: instead — which is why the environment is consulted first.
ROOT_SOURCES = {
    "serveros_repo": ("SERVEROS_REPO", ("workspace_src",), ("serveros",)),
    "ability_repo": ("SERVEROS_ABILITY_REPO", ("workspace_src",), ("ability-management",)),
    "ops_repo": ("SERVEROS_OPS_REPO", ("workspace_root",), ("ops",)),
    "data_root": ("DATA_ROOT", ("identity_home",), ()),
    "workspace_root": ("WORKSPACE_ROOT", ("workspace_root",), ()),
}


def host_profile_roots() -> dict:
    """This node's declared roots, or {} — never a fabricated path.

    A profile that is absent, unreadable or shaped differently yields {}, and
    the caller then refuses. An empty answer is a fact about the machine; a
    guess would be a fact about this file.
    """
    if yaml is None:  # pragma: no cover - yaml is in the control-plane venv
        return {}
    raw = os.environ.get(HOST_PROFILE_ENV)
    path = Path(raw).expanduser() if raw else DEFAULT_HOST_PROFILE.expanduser()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(document, dict):
        return {}
    node = document.get("node")
    roots = node.get("roots") if isinstance(node, dict) else None
    return roots if isinstance(roots, dict) else {}


def default_root(argument: str):
    """The environment's answer, else this host's, else None. Never a guess."""
    env_name, profile_keys, suffix = ROOT_SOURCES[argument]
    raw = os.environ.get(env_name)
    if raw:
        base = Path(raw)
    else:
        roots = host_profile_roots()
        base = next((Path(roots[key]) for key in profile_keys
                     if isinstance(roots.get(key), str) and roots[key]), None)
    if base is None:
        return None
    return base.joinpath(*suffix) if suffix else base


def main(argv=None) -> int:
    home = Path(os.path.expanduser("~"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serveros-repo", type=Path, default=default_root("serveros_repo"))
    ap.add_argument("--ability-repo", type=Path, default=default_root("ability_repo"))
    ap.add_argument("--ops-repo", type=Path, default=default_root("ops_repo"))
    ap.add_argument("--canonical-ref", default=os.environ.get("SERVEROS_CANONICAL_REF") or "main")
    ap.add_argument("--ability-ref", default=os.environ.get("SERVEROS_ABILITY_REF") or "main")
    ap.add_argument("--ops-ref", default=os.environ.get("SERVEROS_OPS_REF") or "main")
    ap.add_argument("--data-root", type=Path, default=default_root("data_root"))
    ap.add_argument("--workspace-root", type=Path, default=default_root("workspace_root"))
    ap.add_argument("--runtime-root", action="append", default=[], metavar="NAME=PATH",
                    help="a runtime skill root to observe; repeatable")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)

    # Refused, not guessed. Every one of these five is compared against a real
    # tree below, and a wrong root produces findings about a directory nobody
    # asked about — which reads exactly like a finding about this one. The exit
    # code is 2 rather than the 0 this program otherwise always returns, because
    # "I could not answer" must not be reportable as "conformant".
    unset = [name for name in ROOT_SOURCES if getattr(args, name) is None]
    if unset:
        env_names = ", ".join(ROOT_SOURCES[name][0] for name in unset)
        flags = ", ".join("--" + name.replace("_", "-") for name in unset)
        sys.stderr.write(
            "architecture_check: no root for " + flags + ".\n"
            "  This checker carries no built-in path, because a path that is "
            "right on one machine is a wrong answer on the next one.\n"
            "  Pass the flag, set " + env_names + ", or point "
            + HOST_PROFILE_ENV + " at a host profile whose `node.roots` "
            "declares them.\n")
        return 2

    if not args.runtime_root:
        claude_root = home / ".claude/skills"
        codex_root = home / ".codex/skills"
        installed = home / ".claude/plugins/installed_plugins.json"
        args.runtime_root = [f"claude-native={claude_root}", f"codex-native={codex_root}"]
        try:
            record = json.loads(installed.read_text(encoding="utf-8"))
            for entry in (record.get("plugins") or {}).get("hwz-server-os@hwz-server-os", []):
                args.runtime_root.append(f"claude-plugin={entry['installPath']}/skills")
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        for entry in sorted((home / ".codex/plugins/cache").glob("*/hwz-server-os/*/skills")):
            args.runtime_root.append(f"codex-plugin={entry}")
    args.runtime_root = [tuple(item.split("=", 1)) for item in args.runtime_root]

    report = collect(args)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                               encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        render(report)
    # Warn-only in Phase 1: a NONCONFORMANT architecture does not fail the call.
    # Exit 0 always; the caller decides. Phase 2 may promote this to a gate.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
