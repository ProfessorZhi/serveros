#!/usr/bin/env python3
"""What `runtime/cli.py` is made of, and what a split of it would have to keep.

D10 says: do not refactor a 16 000-line file until its surface is measured. This
script does the measuring and nothing else — it reads, it never writes to the
tree unless asked for a floor with `--write-floor`, and it imports nothing from
the repository except the graph it parses.

Three questions, each answered by the thing that decides it rather than by a
convention this script would have to agree with:

  * **Structure.** `build_parser()` is walked as the real argparse tree it is:
    which subcommand path binds which `cmd_*` handler, how deep the nesting
    goes, and how much of the file each part costs.
  * **Behaviour.** The exit-code vocabulary, where the two global flags are
    declared, and which verbs take an open tail (`accepts_unknown_argv`) — the
    three things a split can change without changing any command's name.
  * **Reach.** Every `_load_module_from` call site, grouped by the top-level
    directory it reaches into. This is the measurement that decides whether the
    file is a leaf or a composition root, and it is not a matter of taste.

`--with-calls` adds the process-boundary census: how many files outside
`runtime/cli.py` name the CLI, and which verbs they name. It shells out to
`git grep`, so it needs a checkout; on a tree without `.git` it reports nothing
rather than guessing.
"""
from __future__ import annotations

import argparse
import ast
import collections
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "runtime" / "cli.py"

#: Flags declared on the shared parent parser, and therefore inherited by every
#: subparser. Named here so the report can say which two they are rather than
#: counting `add_argument` calls.
GLOBAL_FLAGS = ("--json", "--registry")


def _owner(call: ast.Call) -> str:
    """The variable a `x.method(...)` call was made on, or ""."""
    if not isinstance(call.func, ast.Attribute):
        return ""
    value = call.func.value
    return value.id if isinstance(value, ast.Name) else ""


def _string_arg(call: ast.Call, index: int = 0, keyword: str | None = None) -> str:
    if len(call.args) > index and isinstance(call.args[index], ast.Constant):
        return str(call.args[index].value)
    for kw in call.keywords:
        if kw.arg == keyword and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return ""


def _div_parts(node: ast.AST) -> tuple[str, list[str]]:
    """`REPO_ROOT / "intake" / "x.py"` -> ("REPO_ROOT", ["intake", "x.py"])."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
        if isinstance(current.right, ast.Constant):
            parts.insert(0, str(current.right.value))
        current = current.left
    return (current.id if isinstance(current, ast.Name) else "?"), parts


def structure(tree: ast.Module) -> dict:
    """The parser tree: variable -> subcommand path, and every `add_parser`."""
    build = next(n for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name == "build_parser")
    parsers = {"parser": ""}         # parser variable -> "a b c"
    subparsers: dict[str, str] = {}  # subparsers variable -> parent path
    rows: list[dict] = []
    for node in ast.walk(build):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if not isinstance(target, ast.Name) or not isinstance(value, ast.Call):
            continue
        if not isinstance(value.func, ast.Attribute):
            continue
        owner = _owner(value)
        if value.func.attr == "add_subparsers":
            subparsers[target.id] = parsers.get(owner, owner)
        elif value.func.attr == "add_parser":
            name = _string_arg(value, keyword="name")
            if not name:
                continue
            path = f"{subparsers.get(owner, owner)} {name}".strip()
            parsers[target.id] = path
            rows.append({"path": path, "var": target.id, "line": node.lineno,
                         "aliases": _aliases(value)})
    return {"rows": rows, "spans": _spans(tree)}


def _aliases(call: ast.Call) -> list[str]:
    for kw in call.keywords:
        if kw.arg == "aliases" and isinstance(kw.value, (ast.List, ast.Tuple)):
            return [str(e.value) for e in kw.value.elts if isinstance(e, ast.Constant)]
    return []


def _spans(tree: ast.Module) -> dict[str, tuple[int, int]]:
    return {n.name: (n.lineno, n.end_lineno) for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def bindings(tree: ast.Module, rows: list[dict]) -> dict[str, dict]:
    """`var.set_defaults(func=cmd_x)` -> {subcommand path: {handler, line}}."""
    by_var = {r["var"]: r for r in rows}
    found: dict[str, dict] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "set_defaults":
            continue
        row = by_var.get(_owner(node))
        if row is None:
            continue
        for kw in node.keywords:
            if kw.arg == "accepts_unknown_argv" and isinstance(kw.value, ast.Constant):
                if kw.value.value:
                    row["accepts_unknown_argv"] = True
                continue
            # `func=cmd_x` is the common shape; `func=_wrapper(cmd_x)` is the
            # other one, and reading only the first dropped eight verbs from
            # the scan — a shortfall that reads exactly like "those verbs do
            # not exist" (measured here, and the reason the floor is taken
            # from the built parser instead).
            if kw.arg != "func":
                continue
            if isinstance(kw.value, ast.Name):
                found[row["path"]] = {"handler": kw.value.id, "line": node.lineno}
            elif isinstance(kw.value, ast.Call) and kw.value.args \
                    and isinstance(kw.value.args[0], ast.Name):
                found[row["path"]] = {"handler": kw.value.args[0].id,
                                      "wrapper": _owner(kw.value) or "?", "line": node.lineno}
    return found


def loads(tree: ast.Module) -> tuple[dict[str, int], dict[str, int]]:
    """`_load_module_from` reach, by top-level directory and by enclosing function."""
    by_dir: collections.Counter = collections.Counter()
    by_dir["runtime"] += 0
    per_function: collections.Counter = collections.Counter()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_load_module_from" or not node.args:
            continue
        base, parts = _div_parts(node.args[0])
        if base == "REPO_ROOT" and parts:
            # Some sites spell the whole relative path in one literal
            # (`REPO_ROOT / "context/resolver.py"`), so the first segment of
            # the joined path is the directory either way.
            by_dir[parts[0].split("/")[0]] += 1
        else:
            by_dir[f"({base})"] += 1
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name, func in funcs.items():
        for node in ast.walk(func):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_load_module_from" and node.args):
                base, parts = _div_parts(node.args[0])
                per_function[name] += 1
    return dict(by_dir.most_common()), dict(per_function.most_common())


def invocations() -> dict:
    """Which files name the CLI, and which verbs they name (needs a checkout)."""
    if not (REPO / ".git").exists():
        return {}
    # `errors="replace"` is load-bearing on a Windows host: the default text
    # mode decodes with the locale codec (cp936 here), and one non-UTF-8 byte
    # anywhere in the tree makes the reader thread raise and `stdout` come back
    # as None — a census that returns nothing looks exactly like a census that
    # found nothing. Measured on this checkout.
    proc = subprocess.run(
        ["git", "-C", str(REPO), "grep", "-n", "-I", "-E",
         r"serverosctl|runtime/cli\.py|(^|[\s\"'])cli\.py "],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode not in (0, 1) or proc.stdout is None:
        return {}
    token = re.compile(
        r"(?:serverosctl|runtime/cli\.py|(?<![/\w])cli\.py)\s+(?:--\S+\s+)*([a-z][a-z0-9-]*)")
    verbs: collections.Counter = collections.Counter()
    buckets: collections.Counter = collections.Counter()
    files: set[str] = set()
    for line in proc.stdout.splitlines():
        rel, _, rest = line.partition(":")
        if rel.startswith("runtime/cli.py"):
            continue
        for match in token.finditer(rest):
            verbs[match.group(1)] += 1
            files.add(rel)
            buckets[rel.split("/")[0] if "/" in rel else "(root)"] += 1
    return {"files": len(files), "invocations": sum(verbs.values()),
            "verbs": dict(verbs.most_common()),
            "buckets": dict(buckets.most_common())}


def inventory() -> dict:
    source = CLI.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.count("\n") + 1
    built = structure(tree)
    rows, spans = built["rows"], built["spans"]
    bound = bindings(tree, rows)
    for row in rows:
        binding = bound.get(row["path"])
        row["handler"] = binding["handler"] if binding else None
    parser_span = spans["build_parser"]
    reaches, per_function = loads(tree)
    real = surface()
    by_path = {r["path"]: r for r in rows}
    commands = []
    for path, handler in real.items():
        row = by_path.get(path, {})
        span = spans.get(handler or "")
        commands.append({
            "path": path,
            "handler": handler,
            "parser_line": row.get("line"),
            # The handler's SIZE is measured by name, not by the binding: the
            # 28 verbs whose binding the source scan cannot see still call
            # ordinary module-level `cmd_*` functions, and measuring those
            # keeps the 30%-of-the-file figure honest instead of reporting 0
            # for seven whole groups.
            "handler_lines": (span[1] - span[0] + 1) if span else None,
            "declared_in_source": path in by_path,
            "source_wrapper": (bound.get(path) or {}).get("wrapper"),
        })
    # Summed over DISTINCT handlers: three verbs share `cmd_work_trust` and five
    # share `cmd_workspace`, and counting a shared function once per verb would
    # inflate the total by the size of the overlap.
    handler_total = sum({c["handler"]: c["handler_lines"] for c in commands
                         if c["handler"] and c["handler_lines"]}.values())
    shared = {name: sorted({c["path"] for c in commands if c["handler"] == name})
              for name, times in collections.Counter(
                  c["handler"] for c in commands if c["handler"]).items() if times > 1}
    return {
        "file": {"path": "runtime/cli.py", "lines": lines,
                 "bytes": len(source.encode("utf-8"))},
        "parser_block": {"start": parser_span[0], "end": parser_span[1],
                         "lines": parser_span[1] - parser_span[0] + 1,
                         "share": round((parser_span[1] - parser_span[0] + 1) / lines, 4)},
        "handlers": {"distinct": len({c["handler"] for c in commands if c["handler"]}),
                     "lines": handler_total, "share": round(handler_total / lines, 4),
                     "shared": shared},
        "subcommands": {"verbs": len(real),
                        "top_level": len({p.split()[0] for p in real}),
                        "depth": max((len(p.split()) for p in real), default=0),
                        "source_declarations": len(rows),
                        "declared_in_source": sum(1 for c in commands if c["declared_in_source"]),
                        "with_aliases": [r["path"] for r in rows if r["aliases"]],
                        "open_tail": [r["path"] for r in rows if r.get("accepts_unknown_argv")]},
        "flags": {"global": list(GLOBAL_FLAGS),
                  "declared_on_shared_parent": _shared_parent_flags(tree),
                  "json_reads_in_handlers": source.count("args.json")},
        "exit_codes": {m.group(1): int(m.group(2)) for m in
                       re.finditer(r"^(EXIT_[A-Z_]+) = (\d+)$", source, re.M)},
        "reaches": reaches,
        "load_sites": sum(reaches.values()),
        "surface": {c["path"]: c["handler"] for c in commands},
        "source_scan": {r["path"]: r["handler"] for r in rows if r["handler"]},
        "not_in_source_scan": sorted(c["path"] for c in commands
                                     if not c["declared_in_source"]),
        "commands": sorted(commands, key=lambda c: c["path"]),
    }


def _shared_parent_flags(tree: ast.Module) -> list[str]:
    """Which of GLOBAL_FLAGS the `add_help=False` parent parser declares.

    The parent is created on one statement and populated on the next few, so
    the flags are NOT inside the assignment — walking the assignment finds an
    empty set, which reads identically to "the flags are declared elsewhere".
    """
    build = next((n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "build_parser"), None)
    if build is None:
        return []
    parent = None
    for node in ast.walk(build):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        call = node.value
        if not (isinstance(call.func, ast.Attribute)
                and call.func.attr == "ArgumentParser"):
            continue
        for kw in call.keywords:
            if kw.arg == "add_help" and isinstance(kw.value, ast.Constant) \
                    and kw.value.value is False:
                if isinstance(node.targets[0], ast.Name):
                    parent = node.targets[0].id
    if parent is None:
        return []
    declared = set()
    for node in ast.walk(build):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and _owner(node) == parent):
            declared.add(_string_arg(node))
    return sorted(declared & set(GLOBAL_FLAGS))


def _cli_module():
    spec = importlib.util.spec_from_file_location("serveros_cli_surface_target", CLI)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def surface() -> dict[str, str | None]:
    """Every leaf verb, walked from the parser `build_parser()` really returns.

    This — not the source — is the authority, and the two disagree by 28 verbs
    on this checkout. Three verbs groups (`lease`, `test`, `architecture`) are
    declared by a local helper that loops over a table, so their names are
    values, not literals: no regex, and no AST reader that does not execute the
    builder, can see them. A census that trusted the source would freeze a
    surface 17% smaller than the one users type.
    """
    parser = _cli_module().build_parser()
    found: dict[str, str | None] = {}

    def walk(node, prefix: str) -> None:
        for action in node._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for name, sub in action.choices.items():
                path = f"{prefix} {name}".strip()
                if any(isinstance(a, argparse._SubParsersAction) for a in sub._actions):
                    walk(sub, path)
                else:
                    func = sub.get_default("func")
                    found[path] = getattr(func, "__name__", None) if func else None

    walk(parser, "")
    return found


def floor(data: dict) -> dict:
    """The compatibility floor a split must not go below.

    Verb *removal* and handler *rebinding* are the two ways a split breaks an
    out-of-tree caller or a declared command. A verb *addition* never does, so
    it is outside the floor on purpose: a gate that also fails on additions
    would go red for every in-flight PR that adds a verb, and a gate that goes
    red for a reason nobody can fix from their own branch gets edited rather
    than obeyed.
    """
    return {"schema": "serveros/cli-surface-floor.v1",
            "measured_from": "runtime/cli.py build_parser()",
            "verbs": dict(sorted(data["surface"].items())),
            "top_level": sorted({path.split()[0] for path in data["surface"]})}


def _reach_label(key: str) -> str:
    """Name a reach whose target is not a static path.

    `(path)` and `(_HERE)` are calls whose first argument is computed at run
    time. They are reported rather than dropped: a census that silently omitted
    them would read as "the CLI loads nothing dynamically", which is the exact
    claim a split most needs to be true.
    """
    return f"动态路径{key}（静态不可知）" if key.startswith("(") else key


def render(data: dict, calls: dict) -> str:
    out: list[str] = []
    add = out.append
    f, p, h, s = data["file"], data["parser_block"], data["handlers"], data["subcommands"]
    add(f"# serverosctl 表面清单（D10 · 只读）")
    add("")
    add(f"- 文件：`{f['path']}` —— {f['lines']} 行 / {f['bytes']} 字节")
    add(f"- 解析器：`build_parser()` L{p['start']}–L{p['end']}，{p['lines']} 行"
        f"（全文 {p['share'] * 100:.1f}%）")
    add(f"- 处理函数：{h['distinct']} 个不同函数，{h['lines']} 行"
        f"（{h['share'] * 100:.1f}%）；其中 {len(h['shared'])} 个被多个子命令复用")
    add(f"- 动词：真实 parser 里 {s['verbs']} 个叶子动词，{s['top_level']} 个顶层，"
        f"最深 {s['depth']} 层")
    add(f"- 源码直读：{s['source_declarations']} 次 `add_parser`，其中 "
        f"{s['declared_in_source']} 个动词能在源码里按字面认出来；"
        f"**{len(data['not_in_source_scan'])} 个看不见**（名字来自数据表/循环："
        f"{'、'.join(sorted({p.split()[0] for p in data['not_in_source_scan']}))}）")
    declared = " ".join(data["flags"]["declared_on_shared_parent"]) or "（未识别）"
    add(f"- 全局开关：{declared} 声明在 `add_help=False` 的共享父 parser 上，"
        f"因此每个子命令都继承；handler 里读 `args.json` 共 "
        f"{data['flags']['json_reads_in_handlers']} 处")
    add(f"- 开放尾巴：{', '.join('`' + v + '`' for v in s['open_tail']) or '无'}"
        "（`accepts_unknown_argv`：其后所有 token 原样转交）")
    add(f"- 退出码：" + "、".join(f"{k}={v}" for k, v in data["exit_codes"].items()))
    add(f"- 跨层加载：{data['load_sites']} 处 `_load_module_from` —— " +
        "、".join(f"{_reach_label(k)} {v}" for k, v in data["reaches"].items()))
    if calls:
        add(f"- 调用面：{calls['files']} 个文件、{calls['invocations']} 次调用（不含 cli.py 自身）")
        add(f"  - 按目录：" + "、".join(f"{k} {v}" for k, v in calls["buckets"].items()))
    add("")
    add("## 每个顶层命令的字数与调用面")
    add("")
    add("handler 行数按**动词**累加，复用同一个函数的动词会重复计（上面的总数是按函数去重的）。")
    add("")
    add("| 命令 | 动词 | handler 行数 | 调用次数 |")
    add("|---|---:|---:|---:|")
    per: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for c in data["commands"]:
        top = c["path"].split()[0]
        per[top][0] += 1
        per[top][1] += c["handler_lines"] or 0
    verbs = calls.get("verbs", {}) if calls else {}
    for top in sorted(per, key=lambda t: -per[t][1]):
        add(f"| `{top}` | {per[top][0]} | {per[top][1]} | {verbs.get(top, 0)} |")
    add("")
    add("## 全部绑定路径")
    add("")
    add("| 路径 | handler | 解析器行 | handler 行 |")
    add("|---|---|---:|---:|")
    for c in data["commands"]:
        if not c["handler"]:
            continue
        add(f"| `{c['path']}` | `{c['handler']}` | {c['parser_line']} | "
            f"{c['handler_lines']} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--with-calls", action="store_true",
                        help="add the process-boundary census (needs a checkout)")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="write the report as UTF-8 instead of printing it")
    parser.add_argument("--floor", default=None,
                        help="compare this checkout against a committed floor file")
    parser.add_argument("--write-floor", default=None, metavar="PATH",
                        help="write the floor this checkout would freeze")
    args = parser.parse_args(argv)
    data = inventory()
    calls = invocations() if args.with_calls else {}
    if args.write_floor:
        Path(args.write_floor).write_text(
            json.dumps(floor(data), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    status = 0
    if args.floor:
        status = _compare(floor(data), json.loads(Path(args.floor).read_text(encoding="utf-8")))
    # Explicit UTF-8 on `--out`: this report is Chinese, and a Windows host's
    # default text mode is cp936, so printing it and redirecting with `>` writes
    # bytes that read back as mojibake — measured on this checkout, and a
    # committed artifact that cannot be read is worse than no artifact.
    if args.out:
        text = (json.dumps({"inventory": data, "calls": calls}, indent=2,
                           ensure_ascii=False) if args.json else render(data, calls))
        Path(args.out).write_text(text, encoding="utf-8")
    elif args.write_floor and not args.json:
        # `--write-floor` alone is the "refresh the baseline" incantation; the
        # report is available without it, and printing 190 lines of it on every
        # regeneration is how a regeneration gets piped into `tail -1`.
        pass
    elif args.json:
        json.dump({"inventory": data, "calls": calls}, sys.stdout, indent=2,
                  ensure_ascii=False)
        print()
    else:
        sys.stdout.write(render(data, calls))
    return status


def _compare(live: dict, frozen: dict) -> int:
    """Report a lost verb or a rebinding. Additions are printed, never failed."""
    lost = sorted(set(frozen["verbs"]) - set(live["verbs"]))
    rebound = sorted(k for k in set(frozen["verbs"]) & set(live["verbs"])
                     if frozen["verbs"][k] != live["verbs"][k])
    added = sorted(set(live["verbs"]) - set(frozen["verbs"]))
    for path in lost:
        print(f"LOST: `serverosctl {path}` no longer parses to a handler", file=sys.stderr)
    for path in rebound:
        print(f"REBOUND: `serverosctl {path}` was {frozen['verbs'][path]}, "
              f"now {live['verbs'][path]}", file=sys.stderr)
    for path in added:
        print(f"added: `serverosctl {path}` -> {live['verbs'][path]} "
              "(regenerate the floor to freeze it)", file=sys.stderr)
    if lost or rebound:
        print(f"{len(lost)} lost, {len(rebound)} rebound, {len(added)} added "
              f"(floor holds {len(frozen['verbs'])} paths)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
