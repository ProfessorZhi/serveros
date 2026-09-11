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
    report = None
    if "--json" in argv:
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


def manifest_members(manifest: dict, plugin_id: str = "ProfessorZhi/hwz-server-os") -> dict:
    """Resolve ``provides`` to skill *names*.

    ``provides`` carries stable identities (``ProfessorZhi/workspace-maintainer``),
    while ``bundle.yaml`` carries skill names (``workspace-management``). They are
    not the same namespace and a naive set comparison between them produces a
    fake drift -- the rename is recorded in ``repository_aliases`` and the
    ``name``/``aliases`` fields. Resolution goes identity -> ability -> name.
    """
    abilities = {a.get("id"): a for a in (manifest.get("abilities") or [])}
    plugin = abilities.get(plugin_id) or {}
    names, unresolved, identities = [], [], []
    for entry in plugin.get("provides") or []:
        ident = entry.get("id")
        identities.append(ident)
        ability = abilities.get(ident)
        if ability is None:
            unresolved.append(ident)
        else:
            names.append(ability.get("name") or str(ident).split("/")[-1])
    return {
        "plugin_entry_found": bool(plugin),
        "identities": identities,
        "names": sorted(names),
        "unresolved_identities": unresolved,
    }


# --------------------------------------------------------------------------
# the check
# --------------------------------------------------------------------------


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

    # ---- A1: composition (bundle) vs registry (manifest.provides) ---------
    # The ownership question is under an AUTHORITY_CONFLICT and is NOT decided
    # here. The *sets* are comparable without taking a side: bundle members
    # whose source is ability-management must be exactly the manifest's
    # provides list; bundle members with source: local are local-only.
    bundle_skills = bundle.get("skills") or []
    bundle_members = sorted(s["name"] for s in bundle_skills)
    ability_members = sorted(s["name"] for s in bundle_skills if s.get("source") == "ability-management")
    local_members = sorted(s["name"] for s in bundle_skills if s.get("source") == "local")
    other_members = [s for s in bundle_skills
                     if s.get("source") not in ("local", "ability-management")]
    provides = manifest_members(manifest)

    if other_members:
        findings.append(finding(
            "BUNDLE_SOURCE_UNKNOWN", "composition", "every member has a known source",
            [s.get("name") for s in other_members],
            "plugin/bundle.yaml", "HIGH"))

    if provides["unresolved_identities"]:
        findings.append(finding(
            "PROVIDES_IDENTITY_UNRESOLVED", "registry",
            "every provides[].id resolves to an ability entry",
            provides["unresolved_identities"],
            "ability-management/abilities-manifest.yaml", "MEDIUM"))

    if bundle_members != provides["names"]:
        findings.append(finding(
            "BUNDLE_MANIFEST_DRIFT", "composition-vs-registry",
            "bundle members == manifest.provides resolved to skill names",
            f"bundle-only={sorted(set(bundle_members) - set(provides['names']))} "
            f"manifest-only={sorted(set(provides['names']) - set(bundle_members))}",
            "serveros owns the composition contract; ability-management owns the registry",
            "HIGH",
            observation_provenance={"bundle": _prov(bundle_obs), "manifest": _prov(manifest_obs)}))

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

    if set(ability_members) & set(local_members):
        findings.append(finding(
            "BUNDLE_DOUBLE_SOURCE", "composition",
            "a member is either local or ability-management, never both",
            sorted(set(ability_members) & set(local_members)),
            "plugin/bundle.yaml", "HIGH"))

    # ---- A2: contract self-consistency (reuse validate_contract) ----------
    # The existing validator compares the contract against hardcoded literals in
    # its own body, so it can only detect "the JSON was edited wrong". It is
    # still worth running: it is the gate that exists. What it CANNOT do is
    # recorded as a coverage limit below rather than silently trusted.
    vc = run_reused(serveros_repo / "scripts" / "validate_contract.py",
                    [str(CONTRACT_JSON)])
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
    expected_state_root = f"{args.data_root}/state/serveros"
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
    artifacts_skills = Path(args.data_root) / "artifacts" / "skills"
    if artifacts_skills.is_dir():
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
    rt = run_reused(serveros_repo / "scripts" / "runtime_truth.py", ["--json"])
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
        "coverage_limits": [
            "validate_contract.py compares the contract against literals in its "
            "own body (expected values written twice); running it proves the "
            "file was not corrupted, not that the world matches it.",
            "This check does not read MAP files (TREE-MAP / HWZ-MAP / PLUGIN-MAP). "
            "Maps are not an authority here and Phase 1 does not compile them.",
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
        print(f"  [{f['severity']:<6}] {f['status']:<12} {f['id']}")
    if report["namespace_collisions"]:
        print("name collisions:")
        for name, roots in report["namespace_collisions"].items():
            print(f"  {name}: {', '.join(roots)}")


def main(argv=None) -> int:
    home = Path(os.path.expanduser("~"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serveros-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/serveros"))
    ap.add_argument("--ability-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ABILITY_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/ability-management"))
    ap.add_argument("--ops-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_OPS_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/ops"))
    ap.add_argument("--canonical-ref", default=os.environ.get("SERVEROS_CANONICAL_REF") or "main")
    ap.add_argument("--ability-ref", default=os.environ.get("SERVEROS_ABILITY_REF") or "main")
    ap.add_argument("--ops-ref", default=os.environ.get("SERVEROS_OPS_REF") or "main")
    ap.add_argument("--data-root", type=Path,
                    default=Path(os.environ.get("DATA_ROOT")
                                 or "/opt/applications/data/home-hwz"))
    ap.add_argument("--workspace-root", type=Path,
                    default=Path(os.environ.get("WORKSPACE_ROOT")
                                 or "/opt/applications/data/home-hwz/workspace"))
    ap.add_argument("--runtime-root", action="append", default=[], metavar="NAME=PATH",
                    help="a runtime skill root to observe; repeatable")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)

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
