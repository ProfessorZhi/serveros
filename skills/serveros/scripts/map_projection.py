#!/usr/bin/env python3
"""§15: compile machine registries into human maps, and detect the drift.

A human map that restates a machine fact is a second copy of that fact, and a
second copy drifts. This compiler emits the machine-owned REGION of a map from
the registries themselves, and reports where a map's copy has gone stale.

Three properties this deliberately has:

  pure        the regenerated block is a function of the sources alone -- no
              timestamps, no ordering by mtime, no environment. Two runs on the
              same inputs produce a byte-identical block, so "the diff is empty"
              is a meaningful statement rather than a coincidence.

  read-only   it never writes a map. It emits a candidate and a finding. The
              maps have concurrent writers, so an in-place rewrite by a tool
              that does not own them is a data-loss mechanism, not a feature.

  declaration reads via git objects. Reading a map from a dirty working tree
  reports the tree, not the architecture; this round has already watched five
  readers draw confident wrong conclusions from a stale or dirty checkout.
  Working-tree state is reported SEPARATELY, as `worktree_state`, and never
  folded into the verdict.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "references" / "map-projections.json"

# A block body is compared after line-level right-stripping, so trailing
# whitespace introduced by an editor is not reported as architectural drift.
_NORMALISE = re.compile(r"[ \t]+$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# git-object access
# --------------------------------------------------------------------------- #

def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin", "HOME": str(Path.home())}
    return subprocess.run(
        ["git", "-C", str(repo), *argv],
        capture_output=True, text=True, timeout=120, env=env,
    )


def read_object(repo: Path | None, ref: str, path: str) -> dict:
    """Read `path` at `ref` as a git object. Never falls back to the worktree."""
    prov: dict[str, Any] = {"repo": str(repo) if repo else None, "ref": ref,
                            "path": path, "read_via": "git-object"}
    if repo is None or not (repo / ".git").exists():
        prov.update({"found": False, "why": "repo not a git checkout"})
        return prov
    proc = _git(repo, "show", f"{ref}:{path}")
    if proc.returncode != 0:
        prov.update({"found": False, "why": proc.stderr.strip()[:200] or "git show failed"})
        return prov
    prov.update({"found": True, "content": proc.stdout,
                 "blob": _git(repo, "rev-parse", f"{ref}:{path}").stdout.strip()})
    return prov


def worktree_state(repo: Path | None, path: str) -> dict:
    """Is this map being edited right now? Reported, never used as a verdict."""
    if repo is None or not (repo / ".git").exists():
        return {"known": False}
    proc = _git(repo, "status", "--porcelain", "--", path)
    dirty = bool(proc.stdout.strip())
    return {"known": True, "dirty": dirty,
            "has_active_writer": dirty,
            "note": ("uncommitted changes present; apply a candidate only through the "
                     "writer's own review") if dirty else "clean at observation time"}


def _load(content: str, kind: str) -> Any:
    if kind == "json":
        return json.loads(content)
    import yaml  # imported lazily: only yaml sources need it
    return yaml.safe_load(content)


# --------------------------------------------------------------------------- #
# renderers -- pure functions of the registry document
# --------------------------------------------------------------------------- #

def render_plugin_composition(bundle: dict) -> str:
    """Bundle members are an ordered list; the order is the composition order."""
    members = bundle.get("skills") or []
    lines = [
        "| fact | value |",
        "|---|---|",
        f"| bundle | `{bundle.get('name', '?')}` |",
        f"| version | `{bundle.get('version', '?')}` |",
        f"| cachebuster | `{bundle.get('cachebuster', '?')}` |",
        f"| members | {len(members)} |",
        "",
        f"members, in bundle order ({len(members)}):",
        "",
    ]
    for i, m in enumerate(members, 1):
        name = m.get("name", "?")
        source = m.get("source", "?")
        lines.append(f"{i}. `{name}` — source: `{source}`")
    lines += [
        "",
        "Source: `plugin/bundle.yaml` (`serveros` repository). This table is generated;",
        "editing it here has no effect on the bundle and will be reported as drift.",
    ]
    return "\n".join(lines)


def render_topology_children(topology: dict) -> str:
    """Children are a SET; sorted, because the contract's listing order is not a fact."""
    ws = topology.get("workspace_root_children") or {}
    dr = topology.get("data_root_children") or {}
    roles = topology.get("roles") or {}

    def table(children: dict, root_token: str) -> list[str]:
        out = [f"| child | role | root |", "|---|---|---|"]
        for child in sorted(children):
            role = children[child]
            resolved = (roles.get(role) or {}).get("root", "?")
            out.append(f"| `{child}` | `{role}` | `{resolved}` |")
        return out

    lines = [
        f"**`${{WORKSPACE_ROOT}}` children** ({len(ws)})",
        "",
        *table(ws, "WORKSPACE_ROOT"),
        "",
        f"**`${{DATA_ROOT}}` children** ({len(dr)})",
        "",
        *table(dr, "DATA_ROOT"),
        "",
        "Source: `topology-contract.json` (`serveros` repository). Roles resolve their",
        "root token through `roles{}`; the root token's VALUE comes from the machine",
        "instance (`hwz-ops`). Generated — edit the contract, not this table.",
    ]
    if topology.get("data_root_children_note"):
        lines += ["", "Not listed above, on purpose: `.vscode-server` (platform-owned) and",
                  "`workspace` (it IS `WORKSPACE_ROOT`, not a child of one)."]
    return "\n".join(lines)


RENDERERS = {
    "plugin_composition": render_plugin_composition,
    "topology_children": render_topology_children,
}


# --------------------------------------------------------------------------- #
# probes -- what does the map CLAIM about a fact the registry owns?
#
# "block absent" alone is a weak finding: it says the map is unanchored, not that
# it is wrong. A probe names the disagreement, which is what a map owner needs in
# order to decide whether to paste the block or fix the prose. Probes are
# deliberately conservative -- they report what the text contains, and never
# assert that a coincidental string is a claim.
# --------------------------------------------------------------------------- #

_SEMVER = re.compile(r"\bv?(\d+\.\d+\.\d+(?:[+-][0-9A-Za-z.]+)?)\b")


def probe_versions(text: str, bundle: dict) -> dict:
    """Versions a map states, against the one the bundle declares."""
    found = sorted(set(_SEMVER.findall(text)))
    declared = str(bundle.get("version") or "")
    return {"registry_version": declared, "versions_in_map": found,
            "declared_version_present": declared in found if declared else None}


def probe_children(text: str, topology: dict) -> dict:
    """Declared root children the map never mentions."""
    missing = {}
    for section, key in (("workspace_root_children", "WORKSPACE_ROOT"),
                         ("data_root_children", "DATA_ROOT")):
        declared = sorted((topology.get(section) or {}).keys())
        missing[key] = [c for c in declared if not re.search(rf"\b{re.escape(c)}\b", text)]
    return {"declared_children_absent_from_map": missing}


PROBES = {"plugin_composition": probe_versions, "topology_children": probe_children}


# --------------------------------------------------------------------------- #
# block handling
# --------------------------------------------------------------------------- #

def _begin(config: dict, block_id: str) -> str:
    return config["markers"]["begin"].format(id=block_id)


def _end(config: dict, block_id: str) -> str:
    return config["markers"]["end"].format(id=block_id)


def extract_block(text: str, config: dict, block_id: str) -> str | None:
    """Body between the markers, or None when the block is absent."""
    begin, end = _begin(config, block_id), _end(config, block_id)
    start = text.find(begin)
    if start < 0:
        return None
    start += len(begin)
    stop = text.find(end, start)
    if stop < 0:
        # A BEGIN with no END cannot be safely reconstructed -- everything after
        # it is ambiguous. Treat as absent rather than guess a boundary.
        return None
    return text[start:stop].strip("\n")


def compare(generated: str, present: str | None) -> dict:
    if present is None:
        return {"equal": False, "why": "block absent"}
    a, b = _NORMALISE.sub("", generated).strip("\n"), _NORMALISE.sub("", present).strip("\n")
    if a == b:
        return {"equal": True}
    gl, pl = a.splitlines(), b.splitlines()
    for i in range(max(len(gl), len(pl))):
        g = gl[i] if i < len(gl) else "<missing>"
        p = pl[i] if i < len(pl) else "<missing>"
        if g != p:
            return {"equal": False, "why": "content differs", "line": i + 1,
                    "generated": g[:160], "present": p[:160]}
    return {"equal": False, "why": "content differs"}


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #

def project(config: dict, sources: dict[str, dict]) -> dict[str, str]:
    """Every projection's freshly rendered body, keyed by id."""
    out = {}
    for pid, spec in (config.get("projections") or {}).items():
        src = sources.get(spec["source"]) or {}
        if not src.get("found"):
            continue
        doc = _load(src["content"], src.get("kind", "yaml"))
        out[pid] = RENDERERS[spec["renderer"]](doc)
    return out


def check(config: dict, findings: list[dict] | None = None) -> dict:
    """Compare every declared target block against a fresh render.

    Returns {"blocks": {id: body}, "targets": [...], "findings": [...]}.
    A source that cannot be read produces a finding, never a silent pass --
    'no drift detected' and 'could not look' are different sentences.
    """
    args = config["_args"]
    repos = {
        "serveros": Path(args.serveros_repo) if args.serveros_repo else None,
        "ability-management": Path(args.ability_repo) if args.ability_repo else None,
        "hwz-ops": Path(args.ops_repo) if args.ops_repo else None,
    }
    # Each repo is read at its own ref. A single shared ref would silently read
    # ability-management's main while the serveros side was pinned elsewhere --
    # a cross-repo comparison that looks like a comparison and is not.
    ref_for = {
        "serveros": args.canonical_ref,
        "ability-management": getattr(args, "ability_ref", None) or args.canonical_ref,
        "hwz-ops": getattr(args, "ops_ref", None) or args.canonical_ref,
    }

    sources: dict[str, dict] = {}
    out_findings: list[dict] = list(findings or [])
    for sid, spec in (config.get("sources") or {}).items():
        repo = repos.get(spec["repo"])
        ref = ref_for.get(spec["repo"], args.canonical_ref)
        obs = read_object(repo, ref, spec["path"])
        sources[sid] = {**obs, "kind": spec.get("kind", "yaml")}
        if not obs.get("found"):
            out_findings.append({
                "code": "MAP_VIEW_SOURCE_UNAVAILABLE", "severity": "HIGH",
                "detail": f"projection source '{sid}' unreadable at {ref}:{spec['path']}",
                "provenance": obs, "impact": "map projection drift CANNOT be assessed",
            })

    docs = {sid: _load(s["content"], s.get("kind", "yaml"))
            for sid, s in sources.items() if s.get("found")}
    blocks = {pid: RENDERERS[spec["renderer"]](docs[spec["source"]])
              for pid, spec in (config.get("projections") or {}).items()
              if spec["source"] in docs}

    targets = []
    for tgt in config.get("targets") or []:
        repo = repos.get(tgt["repo"])
        ref = ref_for.get(tgt["repo"], args.canonical_ref)
        obs = read_object(repo, ref, tgt["path"])
        if not obs.get("found"):
            out_findings.append({
                "code": "MAP_VIEW_TARGET_UNAVAILABLE", "severity": "MEDIUM",
                "detail": f"declared map target unreadable at {ref}:{tgt['path']}",
                "provenance": obs,
            })
            continue
        text = obs["content"]
        record = {"map": tgt["path"], "blocks": {}, "worktree_state": worktree_state(repo, tgt["path"])}
        for bid in tgt.get("blocks") or []:
            if bid not in blocks:
                record["blocks"][bid] = {"status": "NO_RENDER",
                                         "why": "source unavailable; see findings"}
                continue
            present = extract_block(text, config, bid)
            cmp = compare(blocks[bid], present)
            if cmp["equal"]:
                record["blocks"][bid] = {"status": "FRESH"}
                continue
            code = "MAP_VIEW_UNBOUND" if present is None else "MAP_VIEW_DRIFT"
            spec = (config.get("projections") or {}).get(bid) or {}
            doc = docs.get(spec.get("source"))
            probe = (PROBES[spec["renderer"]](text, doc)
                     if doc is not None and spec.get("renderer") in PROBES else {})
            record["blocks"][bid] = {"status": code, **cmp, **({"probe": probe} if probe else {})}
            out_findings.append({
                "code": code,
                "severity": "MEDIUM" if code == "MAP_VIEW_UNBOUND" else "HIGH",
                "detail": f"{tgt['path']} :: {bid} :: {cmp['why']}",
                "drift": cmp,
                "probe": probe,
                "worktree_state": record["worktree_state"],
                "proposed_action": "generate candidate; READY_TO_APPLY, do not overwrite",
                "map_owner_note": tgt.get("why", ""),
            })
        targets.append(record)

    return {"blocks": blocks, "targets": targets, "findings": out_findings,
            "sources": {k: {kk: vv for kk, vv in v.items() if kk != "content"}
                        for k, v in sources.items()}}


def render_candidate(config: dict, map_text: str, blocks: dict[str, str],
                     block_ids: list[str]) -> tuple[str, bool]:
    """Return (candidate_text, placed).

    placed=True  every block had markers; the candidate is the map with those
                 regions replaced.
    placed=False at least one block has no anchor. The candidate then carries the
                 block(s) in a clearly-marked appendix, because guessing an
                 insertion point inside someone else's prose is how you corrupt a
                 map. The owner moves it.
    """
    text, placed = map_text, True
    missing = []
    for bid in block_ids:
        if bid not in blocks:
            continue
        body = blocks[bid]
        begin, end = _begin(config, bid), _end(config, bid)
        if begin in text and end in text:
            start = text.index(begin) + len(begin)
            stop = text.index(end, start)
            text = text[:start] + "\n" + body + "\n" + text[stop:]
        else:
            placed = False
            missing.append(bid)
    if missing:
        head = ("\n\n<!-- NOT APPLIED: the blocks below have no anchor in this map. -->\n"
                "<!-- The map restates a machine fact by hand, so the compiler cannot -->\n"
                "<!-- know where the generated region belongs. Move these into place, -->\n"
                "<!-- then keep them fresh with: -->\n"
                "<!--   serverosctl architecture project --out <dir> -->\n"
                "<!-- This comment said `--apply` until 2026-09-22. No such flag has -->\n"
                "<!-- ever existed on either side, and this compiler never writes a -->\n"
                "<!-- map: it emits a candidate for a person to paste, because the -->\n"
                "<!-- maps have concurrent writers. The advice a reader is handed -->\n"
                "<!-- inside a generated comment is a promise like any other. -->\n")
        text = text.rstrip("\n") + head
        for bid in missing:
            text += f"\n\n{_begin(config, bid)}\n{blocks[bid]}\n{_end(config, bid)}\n"
    return text, placed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _load_config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="compile machine registries into human maps (§15)")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--serveros-repo", default=None, help="checkout of ProfessorZhi/serveros")
    ap.add_argument("--ability-repo", default=None, help="checkout of ProfessorZhi/ability-management")
    ap.add_argument("--ops-repo", default=None, help="checkout of the hwz-ops governance repo")
    ap.add_argument("--canonical-ref", default="HEAD",
                    help="ref for the serveros sources (default HEAD, never the worktree)")
    ap.add_argument("--ability-ref", default=None,
                    help="ref for ability-management sources (defaults to --canonical-ref)")
    ap.add_argument("--ops-ref", default=None,
                    help="ref for the hwz-ops map targets (defaults to --canonical-ref)")
    ap.add_argument("--out", default=None,
                    help="write regenerated candidates here instead of printing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    config = _load_config(Path(args.config))
    config["_args"] = args
    report = check(config)

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        manifest = {"schema": "serveros/map-projection-candidates.v1",
                    "candidates": [], "unreadable": []}
        for tgt in config.get("targets") or []:
            obs = read_object(Path(args.ops_repo) if args.ops_repo else None,
                              args.ops_ref or args.canonical_ref, tgt["path"])
            if not obs.get("found"):
                # Never `continue` silently. A run that writes zero candidates
                # because it could not read the maps must not be
                # indistinguishable from a run that found nothing to do.
                manifest["unreadable"].append({"map": tgt["path"], "provenance": obs})
                continue
            text, placed = render_candidate(config, obs["content"], report["blocks"],
                                            tgt.get("blocks") or [])
            name = Path(tgt["path"]).name
            (out / name).write_text(text, encoding="utf-8")
            manifest["candidates"].append({
                "map": tgt["path"], "candidate": str(out / name),
                "applied_in_place": False, "anchor_found": placed,
                "state": "READY_TO_APPLY",
                "note": ("annotated candidate; markers present, safe to paste"
                         if placed else
                         "map has no markers: block appended under NOT APPLIED for the "
                         "owner to place"),
            })
        (out / "candidates.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.json:
            print(json.dumps({**report, "candidates": manifest}, ensure_ascii=False, indent=2))
        else:
            print(f"wrote {len(manifest['candidates'])} candidate(s) to {out}")
            for u in manifest["unreadable"]:
                print(f"  UNREADABLE {u['map']}: {u['provenance'].get('why')}")
        return 1 if manifest["unreadable"] else 0

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for t in report["targets"]:
        for bid, st in t["blocks"].items():
            print(f"{st['status']:18s} {t['map']} :: {bid}")
    for f in report["findings"]:
        print(f"  {f['severity']:6s} {f['code']:32s} {f['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
