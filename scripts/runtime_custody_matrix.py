#!/usr/bin/env python3
"""Who consumes each file in `runtime/`, and what a directory move would break.

WHY THIS IS A PROGRAM AND NOT A TABLE
-------------------------------------
D2/D3/D4 of `docs/plans/fleet-v1-gap-and-work-packages.md` move 44 modules out of
`runtime/`, and every one of those moves is a bet about who loads the file. A
hand-written table of that bet goes stale the first time somebody adds a loader,
and a stale inventory is worse than none: it reads as coverage. So the census is
computed from the tree on every run and the committed matrix is only a snapshot
of its output, with the command in the header.

WHAT IS COUNTED, AND WHY THOSE SHAPES
-------------------------------------
Inbound edges come from `tests/loader_graph.py` — the same scanner the packaging
gate uses — inverted. That matters more than it sounds: the graph is the tree's
one statement of "who loads whom", so a census built on a second, private
regex would agree with itself and with nothing else. Using it means a module
whose only consumer is a loader form the graph cannot see (an f-string path, a
config file) shows up here as *unconsumed*, which is a claim a reader can check
against the graph rather than trust.

Two reference shapes are counted separately because they fail differently when a
file moves:

  loader   `_load("name", <path>)` — a code dependency. Moving the file breaks
           the caller at import time.
  text     a path string in a config, a unit or a document. Moving the file
           breaks nothing at import and everything at run time — an `ExecStart`,
           a bundle, a skill. This is the shape the C4 defect took.

Directories are classified from the plan's own work packages, not from a guess
about a file's name: `D2_MODULES`, `D3_PATTERNS` and `D4_MODULES` below are
transcribed from the plan and from `docs/architecture/layers.yaml`'s `parts`
list for `runtime/`. A module in none of them is reported as `L1?` — the parts
list calls the remainder L1 by default, and this tool will not pretend to have
decided it.

Output is Markdown on stdout so the matrix can be diffed, pasted into an issue,
or committed. `--json` emits the same data for a consumer that wants numbers.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# --- the plan's work packages, transcribed ----------------------------------
#
# `docs/plans/fleet-v1-gap-and-work-packages.md` §4. Names are module stems.
D2_MODULES = {
    "fleet_mcp_owner", "fleet_core_bridge", "fleet_monitor_card", "fleet_monitor_plan",
    "fleet_monitor_source", "fleet_recovery_events", "fleet_audience", "agent_event_bridge",
}
D4_MODULES = {
    "agent_control_store", "coordination", "continuation", "continuation_control",
    "dispatch_attempts", "approval", "owner_policy", "agent_sessions", "wake",
    "review_receipt",
}
#: D3 names topics rather than files. These are the patterns
#: `layers.yaml#directories.runtime.parts.product` names for the same set.
D3_PREFIXES = ("card", "cards", "daily_report", "report_", "alert_", "workbench",
               "presentation", "business_calendar")
D3_MODULES = {"notifications", "direct_lark_notify", "live_sender"}

TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".sh", ".ps1", ".toml",
                 ".service", ".timer", ".cfg", ".ini"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}

CATEGORY_ORDER = ("runtime", "tests", "scripts", "bin", "deploy", "control-plane",
                  "workengine", "plugins", "integrations", "native", "docs", "ci", "other")

#: What a move to a NEW top-level directory costs, and why. Each of these is a
#: file the repository's own gates read; a move that skips one of them is either
#: red or — worse, because the gate that would catch it derives its requirement
#: from `CORE_MODULES` — silently missing from an installed artifact.
MOVE_PREREQUISITES = {
    "docs/architecture/layers.yaml": "register the directory; the layer guard reads it",
    "scripts/build.py": "`CORE_MODULES` decides what an artifact copies at all",
    "plugin/bundle.yaml": "declares the shipped path; the packaging loop reads it",
    "scripts/runtime_truth.py": "reports which core modules a node actually has",
    "scripts/release.py": "the release path verifies the same composition",
    "tests/test_repository_layers.py": "asserts the registered set and the layer rules",
}


def tracked(*patterns: str) -> list[str]:
    command = ["git", "-C", str(REPO), "ls-files", *patterns]
    return [line for line in subprocess.run(command, capture_output=True, text=True,
                                           check=True).stdout.splitlines() if line]


def category(rel: str) -> str:
    top = rel.split("/", 1)[0]
    if rel.startswith("tests/"):
        return "tests"
    if rel.startswith(".github/"):
        return "ci"
    if rel.startswith("docs/") or rel.endswith(".md"):
        return "docs"
    if rel.startswith("bin/"):
        return "bin"
    if rel.startswith("scripts/"):
        return "scripts"
    if rel.startswith("runtime/"):
        return "runtime"
    if rel.startswith("deploy/"):
        return "deploy"
    if top in {"fleet_mcp", "mcp", "fleet"}:
        return "control-plane"
    if top == "workengine":
        return "workengine"
    if top in {"plugins", "plugin", "skills"}:
        return "plugins"
    if top in {"integrations", "native"}:
        return top
    return "other"


def layer(leaf: str) -> str:
    if leaf in D2_MODULES:
        return "L2-hub"
    if leaf in D4_MODULES:
        return "L0"
    if leaf in D3_MODULES or leaf.startswith(D3_PREFIXES):
        return "product"
    return "L1?"


def read(rel: str) -> str:
    try:
        return (REPO / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def loader_graph():
    """The repo's own scanner, loaded rather than reimplemented.

    Same shape `scripts/verify.py` uses to reach the same file: a path load
    under a synthetic name, because nothing in this tree is a package. Loading
    it by `sys.path` would work too and would put a second name on the module
    in a long-lived interpreter — see the identity hazard `loader_graph`'s own
    header documents.
    """
    name = "loader_graph"
    if name in sys.modules:
        return sys.modules[name]
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, REPO / "tests" / "loader_graph.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--detail", action="store_true",
                        help="one line per consumer, not only counts")
    parser.add_argument("--claims", type=Path, default=None,
                        help="JSON map of path -> [PR numbers], from `gh pr view "
                             "--json files` over every open PR. Volatile by nature; "
                             "the matrix prints the file it read.")
    args = parser.parse_args()
    claims: dict[str, list[int]] = {}
    if args.claims:
        claims = {k: list(v) for k, v in
                  json.loads(args.claims.read_text(encoding="utf-8")).items()}

    graph = loader_graph()
    runtime_files = tracked("runtime/")
    modules = [rel for rel in runtime_files if rel.endswith(".py")]
    other = [rel for rel in runtime_files if not rel.endswith(".py")]

    py_files = [rel for rel in tracked("*.py")]
    inbound: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for rel in py_files:
        try:
            refs = graph.module_refs(Path(rel))
        except SyntaxError:
            continue
        except OSError:
            continue
        for target in refs:
            target = target.as_posix()
            if target.startswith("runtime/"):
                inbound[target][rel].append("loader")

    # Text references: the shape that breaks a unit rather than an import.
    text_refs: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    texts = {rel: read(rel) for rel in tracked("*")
             if Path(rel).suffix in TEXT_SUFFIXES
             and not any(part in SKIP_DIRS for part in Path(rel).parts)}
    for target in runtime_files:
        needle = target
        bare = Path(target).name
        for rel, text in texts.items():
            if rel == target:
                continue
            if needle in text or (bare in text and f"runtime/{bare}" in text):
                kind = "loader" if rel.endswith(".py") else "text"
                if kind == "loader" and rel in inbound.get(target, {}):
                    continue
                text_refs[target][rel].append(kind)

    rows = []
    for rel in sorted(runtime_files):
        leaf = Path(rel).stem
        consumers = dict(inbound.get(rel, {}))
        for rel2, kinds in text_refs.get(rel, {}).items():
            consumers.setdefault(rel2, []).extend(kinds)
        by_cat = Counter(category(c) for c in consumers)
        try:
            outbound = sorted(p.as_posix() for p in graph.module_refs(Path(rel)))
        except (SyntaxError, OSError):
            outbound = ["<unparsed>"]
        outbound = [p for p in outbound if p != rel]
        rows.append({
            "path": rel,
            "leaf": leaf,
            "kind": "module" if rel.endswith(".py") else Path(rel).suffix.lstrip(".") or "file",
            "layer": layer(leaf) if rel.endswith(".py") else "-",
            "lines": len(read(rel).splitlines()),
            "inbound": {c: sorted(v) for c, v in sorted(consumers.items())},
            "inbound_by_category": dict(sorted(by_cat.items(),
                                               key=lambda kv: CATEGORY_ORDER.index(kv[0])
                                               if kv[0] in CATEGORY_ORDER else 99)),
            "outbound": outbound,
            "move": move_verdict(outbound, consumers),
            "claimed_by": sorted(claims.get(rel, [])),
        })

    if args.json:
        json.dump({"modules": len(modules), "files": len(runtime_files), "rows": rows},
                  sys.stdout, indent=1, sort_keys=True)
        print()
        return 0

    print(f"# runtime/ custody matrix\n")
    print(f"`runtime/` holds **{len(runtime_files)} tracked files**, "
          f"**{len(modules)}** of them Python modules and {len(other)} contracts/configs.")
    print("Regenerate with `python scripts/runtime_custody_matrix.py`.\n")
    print("## Work-package reconciliation\n")
    print("| group | plan says | this census finds |")
    print("|---|---|---|")
    print(f"| D2 hub (`fleet_hub/`) | 8 | {sum(1 for r in rows if r['layer'] == 'L2-hub')} |")
    print(f"| D3 product (`reports/`) | 26 | {sum(1 for r in rows if r['layer'] == 'product')} |")
    print(f"| D4 work control (`workengine/agent_control/`) | 10 | {sum(1 for r in rows if r['layer'] == 'L0')} |")
    print(f"| L1 remainder | — | {sum(1 for r in rows if r['layer'] == 'L1?')} |")
    print()

    if claims:
        collided = {rel: n for rel, n in claims.items() if rel.startswith("runtime/")}
        print(f"## Ownership\n\nFrom `{args.claims.as_posix()}`: "
              f"{len(collided)} `runtime/` path(s) are claimed by an open PR.\n")
        print("| file | open PR(s) |")
        print("|---|---|")
        for rel in sorted(collided):
            print(f"| `{rel}` | {', '.join('#' + str(n) for n in collided[rel])} |")
        print()

        print("### What a new top-level directory costs\n")
        print("| file | why a move needs it | open PR(s) |")
        print("|---|---|---|")
        for rel, why in MOVE_PREREQUISITES.items():
            held = ", ".join("#" + str(n) for n in sorted(claims.get(rel, []))) or "—"
            print(f"| `{rel}` | {why} | {held} |")
        print()
    print("## Modules\n")
    print("`in` = inbound consumers by category (loader edges from `tests/loader_graph.py`,"
          " inverted, plus text references); `out` = modules this one loads;"
          " `move` = what a move costs by dependency shape alone.\n")
    header = "| module | layer | lines | in | out | move |"
    rule = "|---|---|---:|---|---|---|"
    if claims:
        header = header[:-1] + " claimed |"
        rule = rule[:-1] + "---|"
    print(header)
    print(rule)
    for row in sorted(rows, key=lambda r: (-len(r["inbound"]), r["path"])):
        if row["kind"] != "module":
            continue
        cats = ", ".join(f"{k} {v}" for k, v in row["inbound_by_category"].items()) or "—"
        out = ", ".join(Path(p).stem for p in row["outbound"]) or "—"
        if len(out) > 90:
            out = out[:87] + "…"
        line = f"| `{row['leaf']}` | {row['layer']} | {row['lines']} | {cats} | {out} | {row['move']} |"
        if claims:
            claimed = ", ".join('#' + str(n) for n in row["claimed_by"]) or "—"
            line = line[:-1] + f" {claimed} |"
        print(line)

    alone = [r for r in rows if r["kind"] == "module" and not r["outbound"]]
    print(f"\n## Ready to move alone\n\n"
          f"{len(alone)} module(s) load no other module, so a move rewires only their "
          f"consumers. Ranked by consumer count; the ownership column, when given, is "
          f"the gate that comes before dependency shape.\n")
    print("| module | layer | consumers | claimed |")
    print("|---|---|---:|---|")
    for row in sorted(alone, key=lambda r: -len(r["inbound"])):
        claimed = ", ".join('#' + str(n) for n in row["claimed_by"]) or "—"
        print(f"| `{row['leaf']}` | {row['layer']} | {len(row['inbound'])} | {claimed} |")
    print("\n## Contracts and configs beside them\n")
    print("| file | lines | in | move |")
    print("|---|---:|---|---|")
    for row in sorted(rows, key=lambda r: r["path"]):
        if row["kind"] == "module":
            continue
        cats = ", ".join(f"{k} {v}" for k, v in row["inbound_by_category"].items()) or "—"
        print(f"| `{row['path']}` | {row['lines']} | {cats} | {row['move']} |")

    if args.detail:
        print("\n## Consumers\n")
        for row in sorted(rows, key=lambda r: r["path"]):
            if not row["inbound"]:
                continue
            print(f"\n### `{row['path']}`")
            for consumer, kinds in row["inbound"].items():
                print(f"- `{consumer}` ({', '.join(sorted(set(kinds)))})")
    return 0


def move_verdict(outbound: list[str], consumers: dict) -> str:
    """What a move costs by dependency shape, not by who else is editing.

    Ownership collisions are a separate question and deliberately not answered
    here: they change by the hour, and a committed answer would be stale before
    it was read.
    """
    if not outbound:
        return "alone (loads nothing)"
    external = [p for p in outbound if not p.startswith("runtime/")]
    if external:
        return "with " + ", ".join(sorted({p.split("/", 1)[0] for p in external}))
    if len(outbound) == 1:
        return f"with {Path(outbound[0]).stem}"
    return f"with {len(outbound)} runtime modules"


if __name__ == "__main__":
    raise SystemExit(main())
