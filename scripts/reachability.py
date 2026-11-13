#!/usr/bin/env python3
"""Which code in this repository is reachable from what actually runs.

    python3 scripts/reachability.py [--roots scripts/reachability-roots.yaml] [--json OUT]

The roots are the entry points observed running on the three devices
(systemd units, Windows scheduled tasks, plugin MCP servers), plus the
tooling that installs and releases them. From each root it follows:

- ``import a.b`` / ``from a.b import c``, resolved against the directories
  this code puts on ``sys.path`` (repo root, ``runtime/``, ``workengine``'s
  parent, each ``integrations/<x>/``, and the importing file's own directory);
- any string literal naming a ``*.py`` file (``spec_from_file_location`` and
  ``root / "runtime" / "x.py"`` are how much of this code loads its siblings);
- any string literal that names a module (``-m pkg.mod``, ``import_module``);
- ``*.py`` names inside shell, PowerShell, unit and workflow files.

Every resolution is generous: when a name could mean several files, all of
them count as reached. So "unreached" is a conservative claim — a file listed
as unreached had no static path from any root at all. Dynamic tricks that
build a path from variables at run time can still hide an edge; the report
says so instead of pretending otherwise.

Files under ``tests/`` are never roots. A file reached only from tests is
reported as TEST_ONLY.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_NAME = re.compile(r"[A-Za-z0-9_./\\-]*?([A-Za-z0-9_-]+\.py)\b")
MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
DASH_M = re.compile(r"-m\s+([A-Za-z_][A-Za-z0-9_.]*)")


def tracked() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout.split("\n")
    return [ROOT / p for p in out if p]


def search_roots() -> list[Path]:
    roots = [ROOT, ROOT / "runtime", ROOT / "mcp", ROOT / "native" / "node-core" / "core-lite"]
    roots += sorted(p for p in (ROOT / "integrations").iterdir() if p.is_dir())
    return [r for r in roots if r.is_dir()]


class Index:
    def __init__(self, files: list[Path]) -> None:
        self.py = [f for f in files if f.suffix == ".py"]
        self.by_name: dict[str, list[Path]] = defaultdict(list)
        for f in self.py:
            self.by_name[f.name].append(f)
        self.roots = search_roots()
        self.pyset = set(self.py)

    def module(self, dotted: str, near: Path | None) -> list[Path]:
        parts = dotted.split(".")
        bases = list(self.roots)
        if near is not None:
            bases = [near.parent, near.parent.parent] + bases
        found = []
        for base in bases:
            for n in range(len(parts), 0, -1):
                stem = base.joinpath(*parts[:n])
                for cand in (stem.with_suffix(".py"), stem / "__init__.py"):
                    if cand in self.pyset:
                        found.append(cand)
                if found:
                    break
        return found

    def py_literal(self, text: str, near: Path | None) -> list[Path]:
        hits = []
        for match in PY_NAME.finditer(text):
            whole, name = match.group(0).replace("\\", "/"), match.group(1)
            cands = self.by_name.get(name, [])
            narrowed = [c for c in cands if str(c).replace("\\", "/").endswith(whole.lstrip("./"))]
            if "/" not in whole.strip("./") and len(narrowed) > 1 and near is not None:
                # A bare file name: the sibling is meant, else the nearest relative.
                for scope in (near.parent, near.parent.parent):
                    close = [c for c in narrowed if scope in c.parents]
                    if close:
                        narrowed = close
                        break
            hits += narrowed or cands
        return hits


def edges_of_python(path: Path, index: Index) -> set[Path]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    out: set[Path] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.update(index.module(alias.name, path))
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # relative import
                base = path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                mod = (node.module or "").split(".") if node.module else []
                for alias in node.names:
                    for parts in (mod, mod + [alias.name]):
                        stem = base.joinpath(*parts) if parts else base
                        for cand in (stem.with_suffix(".py"), stem / "__init__.py"):
                            if cand in index.pyset:
                                out.add(cand)
            elif node.module:
                out.update(index.module(node.module, path))
                for alias in node.names:
                    out.update(index.module(f"{node.module}.{alias.name}", path))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            # Prose that mentions a file ("release.py promote reloads units") is
            # not a load. A path, or a command line that runs Python, is.
            prose = " " in value.strip() and not re.match(r"\s*(python3?|py)\b|.*\s-m\s", value)
            if ".py" in value and not prose and "\n" not in value:
                out.update(index.py_literal(value, path))
            if MODULE.match(value):
                out.update(index.module(value, path))
            for m in DASH_M.finditer(value):
                out.update(index.module(m.group(1), path))
    out.discard(path)
    return out


def edges_of_text(path: Path, index: Index) -> set[Path]:
    text = path.read_text(encoding="utf-8", errors="replace")
    out = set(index.py_literal(text, path))
    for m in DASH_M.finditer(text):
        out.update(index.module(m.group(1), path))
    return out


TEXT_SUFFIXES = {".sh", ".ps1", ".vbs", ".service", ".timer", ".yml", ".yaml", ".json", ""}


def build_graph(files: list[Path], index: Index) -> dict[Path, set[Path]]:
    graph: dict[Path, set[Path]] = {}
    for f in files:
        if f.suffix == ".py":
            graph[f] = edges_of_python(f, index)
        elif f.suffix in TEXT_SUFFIXES and f.is_file() and f.stat().st_size < 2_000_000:
            try:
                first = f.read_bytes()[:2]
            except OSError:
                continue
            if f.suffix == "" and first != b"#!":
                continue
            graph[f] = edges_of_text(f, index) | (
                edges_of_python(f, index) if f.suffix == "" and b"python" in f.read_bytes()[:80]
                else set())
    return graph


def reach(graph: dict[Path, set[Path]], starts: list[Path]) -> set[Path]:
    seen = set()
    queue = deque(s for s in starts if s in graph or s.exists())
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        queue.extend(graph.get(node, ()))
    return seen


def load_roots(path: Path) -> dict[str, list[str]]:
    """A tiny YAML subset: `group:` then `  - path` lines."""
    groups: dict[str, list[str]] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line:
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current = line[:-1]
            groups[current] = []
        elif line.strip().startswith("- ") and current:
            groups[current].append(line.strip()[2:].strip())
    return groups


def lines(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n")
    except OSError:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--roots", type=Path, default=ROOT / "scripts" / "reachability-roots.yaml")
    parser.add_argument("--json", type=Path)
    ns = parser.parse_args(argv)

    files = tracked()
    index = Index(files)
    graph = build_graph(files, index)
    groups = load_roots(ns.roots)
    missing = [p for ps in groups.values() for p in ps if not (ROOT / p).exists()]

    reached_by: dict[Path, list[str]] = defaultdict(list)
    for group, paths in groups.items():
        for f in reach(graph, [ROOT / p for p in paths]):
            reached_by[f].append(group)
    live = set(reached_by)
    tests = [f for f in files if f.parts[len(ROOT.parts)] == "tests"]
    test_reach = reach(graph, tests) - set(tests)

    rows = []
    for f in index.py:
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith("tests/"):
            continue
        state = "LIVE" if f in live else ("TEST_ONLY" if f in test_reach else "UNREACHED")
        rows.append({"path": rel, "state": state, "lines": lines(f),
                     "roots": sorted(set(reached_by.get(f, [])))})

    by_dir: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        parts = r["path"].split("/")
        top = "/".join(parts[:2]) if parts[0] in ("integrations", "native") and len(parts) > 2 \
            else parts[0] if len(parts) > 1 else "(root)"
        by_dir[top][r["state"]] += r["lines"]
        by_dir[top]["files_" + r["state"]] += 1

    total = defaultdict(int)
    for r in rows:
        total[r["state"]] += r["lines"]
    summary = {"python_files": len(rows), "lines": dict(total), "missing_roots": missing,
               "by_dir": {k: dict(v) for k, v in sorted(by_dir.items())}, "files": rows}
    if ns.json:
        ns.json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("files", "by_dir")},
                     ensure_ascii=False))
    width = max(len(k) for k in by_dir)
    print(f"{'dir':<{width}}  {'LIVE':>8} {'TEST_ONLY':>9} {'UNREACHED':>9}   lines")
    for k, v in sorted(by_dir.items(), key=lambda kv: -sum(kv[1].get(s, 0) for s in
                                                          ("LIVE", "TEST_ONLY", "UNREACHED"))):
        print(f"{k:<{width}}  {v.get('LIVE', 0):>8} {v.get('TEST_ONLY', 0):>9} "
              f"{v.get('UNREACHED', 0):>9}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
