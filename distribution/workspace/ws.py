#!/usr/bin/env python3
"""Device directory governance: scan, plan, migrate, reclaim (R-1..R-3).

    python3 distribution/workspace/ws.py scan  --device tokyo --out scan.json [--sizes]
    python3 distribution/workspace/ws.py plan  --device tokyo --scan scan.json --out plan.json
    python3 distribution/workspace/ws.py apply --device tokyo --plan plan.json --approve <plan_id> [--only MOVE,REMOVE_WORKTREE] [--apply]
    python3 distribution/workspace/ws.py check --device tokyo           # daily summary, JSON

Every device has a layout (`layouts/<device>.json`): a managed root `work/`
with zones (src, worktrees, scratch, artifacts, and the device's own role
zones), the places that are only *registered* (personal projects, app data),
the places that are *protected* (production services and data), and the
*legacy* rules that say what each old directory is, so it can be moved into
`work/` rather than only listed.

What the steps are, and why each is safe:

* `scan` only reads. For every old directory a legacy rule matches it records
  what is there: is it a repository or a worktree, its remote, branch,
  uncommitted files, commits that exist on no remote, when it was last
  touched, whether a service or a running process uses it.
* `plan` turns a scan into steps, each with its reason and its undo:
  one main clone per remote moves to `work/src/<repo>`; a duplicate clone with
  nothing of its own is removed, one with work of its own is moved beside it;
  a worktree is moved to `work/worktrees/<repo>/<item>` — or removed when it is
  idle past the zone's limit and holds nothing that is not on a remote;
  scratch is moved, or removed past its limit when nothing in it is unique.
  Anything in use by a service or a process is DEFER; anything the rules do not
  know is REPORT. Registered and protected places get no step at all.
* `apply` does nothing without `--apply` and the plan's own id. It re-checks
  every step against the disk before acting (a source that changed since the
  plan is skipped), never crosses a filesystem, never removes a worktree git
  would call dirty, leaves a link at the old path after a move, and writes a
  receipt with what was done and how to undo it.

Standard library only: it runs on Linux and on Windows (Python 3.10+).
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LAYOUTS = HERE / "layouts"
ALIASES = {"tokyo": "tokyo", "dell": "a6000", "a6000": "a6000", "llm-lab-dell": "a6000",
           "work-fj": "fjpcos", "fjpcos": "fjpcos", "windows": "fjpcos"}
ITEM_ID = re.compile(r"\b([A-Z][A-Z0-9]{1,7}-\d{2,5})\b")
SKIP_NAMES = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".mypy_cache"}
GIT_TIMEOUT = 30
DAY = 86400


class LayoutError(ValueError):
    pass


# -- layout ---------------------------------------------------------------------

def load_layout(device: str, path: str | None = None) -> dict[str, Any]:
    file = Path(path) if path else LAYOUTS / f"{ALIASES.get(device, device)}.json"
    if not file.is_file():
        raise LayoutError(f"no layout for {device!r} ({file})")
    layout = json.loads(file.read_text(encoding="utf-8"))
    if layout.get("schema") != "serveros/device-layout.v1":
        raise LayoutError(f"{file}: not a serveros/device-layout.v1 document")
    for key in ("home", "work_root", "scan_roots", "zones"):
        if key not in layout:
            raise LayoutError(f"{file}: missing {key}")
    return layout


def _windows(layout: dict) -> bool:
    return bool(re.match(r"^[A-Za-z]:\\", layout["home"]))


def pmod(layout: dict):
    return ntpath if _windows(layout) else posixpath


def absolute(layout: dict, rel: str) -> str:
    """A layout path as an absolute path: relative ones are under home."""
    p = pmod(layout)
    return p.normpath(rel if p.isabs(rel) else p.join(layout["home"], rel))


def _norm(layout: dict, path: str) -> str:
    p = pmod(layout)
    out = p.normpath(path)
    return out.lower() if _windows(layout) else out


def under(layout: dict, path: str, root: str) -> bool:
    a, b = _norm(layout, path), _norm(layout, root)
    sep = "\\" if _windows(layout) else "/"
    return a == b or a.startswith(b.rstrip(sep) + sep)


def zone_path(layout: dict, name: str) -> str:
    """`src` -> the absolute path of the zone named src under work_root."""
    p = pmod(layout)
    for zone in layout["zones"]:
        if p.basename(p.normpath(zone["path"])) == name:
            return absolute(layout, zone["path"])
    return p.join(layout["work_root"], name)


def retain_days(layout: dict, name: str, default: int) -> int:
    p = pmod(layout)
    for zone in layout["zones"]:
        if p.basename(p.normpath(zone["path"])) == name:
            return int(zone.get("retain_days") or default)
    return default


# -- reading the disk -------------------------------------------------------------

def _git(path: str, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def repo_name(remote: str | None, fallback: str) -> str:
    if not remote:
        return fallback
    tail = re.split(r"[/:]", remote.rstrip("/"))[-1]
    return tail[:-4] if tail.endswith(".git") else tail


def remote_key(remote: str | None) -> str | None:
    """owner/repo, whatever the host alias (`github-litellm:Owner/Repo.git`)."""
    if not remote:
        return None
    if remote.startswith(("/", ".")) or re.match(r"^[A-Za-z]:[\\/]", remote):
        return None                                   # a local path is not an identity
    parts = [x for x in re.split(r"[/:]", remote.rstrip("/")) if x]
    if len(parts) < 2:
        return None
    owner, name = parts[-2], parts[-1]
    name = name[:-4] if name.endswith(".git") else name
    return f"{owner}/{name}".lower()


def git_facts(path: str) -> dict[str, Any] | None:
    """What a checkout holds that would be lost if it were removed."""
    dot = os.path.join(path, ".git")
    if not os.path.exists(dot):
        return None
    kind = "worktree" if os.path.isfile(dot) else "repo"
    main = None
    if kind == "worktree":
        common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        main = os.path.dirname(common) if common else None
    remote = _git(path, "remote", "get-url", "origin")
    status = _git(path, "status", "--porcelain")
    if kind == "repo":
        # Commits on any local branch (or a detached HEAD) that no remote has.
        unique = _git(path, "rev-list", "--count", "HEAD", "--branches", "--not", "--remotes")
    else:
        # Removing a worktree keeps its branch; only a detached HEAD's own
        # commits would be lost.
        unique = _git(path, "rev-list", "--count", "HEAD", "--not", "--branches", "--remotes")
    stashes = _git(path, "stash", "list")
    last = _git(path, "log", "-1", "--format=%ct")
    branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    worktrees = []
    if kind == "repo":
        listing = _git(path, "worktree", "list", "--porcelain") or ""
        worktrees = [line[len("worktree "):] for line in listing.splitlines()
                     if line.startswith("worktree ")][1:]
    return {"kind": kind, "main": main, "remote": remote, "remote_key": remote_key(remote),
            "branch": branch,
            "dirty": None if status is None else len([x for x in status.splitlines() if x.strip()]),
            "unique_commits": None if unique is None else int(unique or 0),
            "stashes": 0 if not stashes else len(stashes.splitlines()),
            "last_commit": int(last) if last and last.isdigit() else None,
            "worktrees": worktrees}


def nested_git(path: str, depth: int = 3) -> list[str]:
    found: list[str] = []
    base = path.rstrip("\\/").count(os.sep)
    for root, dirs, _files in os.walk(path):
        if ".git" in dirs or os.path.isfile(os.path.join(root, ".git")):
            found.append(root)
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES]
        if root.count(os.sep) - base >= depth:
            dirs[:] = []
    return found


def last_touched(path: str, facts: dict | None) -> float:
    # File times, not commit times: a checkout of an old commit made yesterday
    # is in use; a recent commit in a tree nobody opened for a month is not.
    times = []
    try:
        times.append(os.stat(path).st_mtime)
    except OSError:
        pass
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.name in (".git",):
                    continue
                try:
                    times.append(entry.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    pass
    except OSError:
        pass
    return max(times) if times else 0.0


def size_kb(path: str, timeout: int = 60) -> int | None:
    if os.name != "nt" and shutil.which("du"):
        try:
            done = subprocess.run(["du", "-sk", "-x", path], capture_output=True, text=True,
                                  timeout=timeout)
            return int(done.stdout.split()[0]) if done.returncode == 0 and done.stdout else None
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return None
    total, deadline = 0, time.monotonic() + timeout
    for root, dirs, files in os.walk(path):
        if time.monotonic() > deadline:
            return None
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total // 1024


def users_of_paths(layout: dict) -> list[dict[str, str]]:
    """Who refers to a path: service units, scheduled tasks, cron, and the
    working directories of running processes."""
    refs: list[dict[str, str]] = []
    if _windows(layout):
        try:
            done = subprocess.run(["schtasks", "/query", "/fo", "LIST", "/v"], capture_output=True,
                                  text=True, timeout=60, errors="replace")
            refs.append({"by": "scheduled tasks", "text": done.stdout})
        except (OSError, subprocess.SubprocessError):
            pass
        return refs
    unit_dirs = [os.path.expanduser("~/.config/systemd/user"), "/etc/systemd/system"]
    for d in unit_dirs:
        try:
            for name in os.listdir(d):
                full = os.path.join(d, name)
                if os.path.isfile(full) and name.endswith((".service", ".timer", ".conf")):
                    try:
                        refs.append({"by": f"unit {name}", "text": Path(full).read_text(errors="replace")})
                    except OSError:
                        pass
        except OSError:
            pass
    try:
        done = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if done.returncode == 0:
            refs.append({"by": "crontab", "text": done.stdout})
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    refs.append({"by": f"process {pid} cwd", "text": os.readlink(f"/proc/{pid}/cwd")})
                except OSError:
                    pass
    except OSError:
        pass
    return refs


def in_use(layout: dict, path: str, refs: list[dict[str, str]]) -> list[str]:
    needle = _norm(layout, path)
    hits = []
    for ref in refs:
        text = ref["text"].lower() if _windows(layout) else ref["text"]
        if needle in text:
            hits.append(ref["by"])
    return sorted(set(hits))[:5]


# -- scan -------------------------------------------------------------------------

def _expand(layout: dict, pattern: str) -> list[str]:
    import glob
    full = absolute(layout, pattern)
    return sorted(x for x in glob.glob(full) if os.path.isdir(x) and not os.path.islink(x))


def _matches(layout: dict, path: str, patterns: list[str]) -> bool:
    n = _norm(layout, path)
    return any(fnmatch.fnmatchcase(n, _norm(layout, absolute(layout, pat))) for pat in patterns)


def scan(layout: dict, *, sizes: bool = False, refs: list | None = None,
         now: float | None = None) -> dict[str, Any]:
    now = now or time.time()
    refs = users_of_paths(layout) if refs is None else refs
    items: list[dict[str, Any]] = []
    claimed: list[str] = []

    def record(path: str, cls: str, **extra: Any) -> None:
        facts = git_facts(path)
        nested = [] if facts else nested_git(path)
        nested_facts = [dict(git_facts(n) or {}, path=n) for n in nested[:50]]
        item = {"path": path, "class": cls, "git": facts, "nested_git": nested_facts,
                "idle_days": int((now - last_touched(path, facts)) // DAY),
                "in_use": in_use(layout, path, refs), **extra}
        if sizes:
            item["size_kb"] = size_kb(path)
        items.append(item)
        claimed.append(path)

    for rule in layout.get("legacy", []):
        for path in _expand(layout, rule["from"]):
            if any(under(layout, path, c) for c in claimed):
                continue
            if under(layout, path, layout["work_root"]) and any(
                    _norm(layout, path) == _norm(layout, absolute(layout, z["path"])) for z in layout["zones"]):
                continue                                  # a zone itself is not legacy
            record(path, "legacy", rule=rule)
    protected = [absolute(layout, x["path"]) for x in layout.get("protected", [])]
    registered = [absolute(layout, x["path"]) for x in layout.get("registered", [])]
    for root in layout["scan_roots"]:
        try:
            children = sorted(os.listdir(root))
        except OSError:
            continue
        for name in children:
            path = os.path.join(root, name)
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            if any(under(layout, path, c) for c in claimed):
                continue
            if under(layout, layout["work_root"], path) or under(layout, path, layout["work_root"]):
                continue                                  # the managed root and its parents
            if any(under(layout, path, x) for x in protected):
                items.append({"path": path, "class": "protected"})
                continue
            if any(under(layout, path, x) for x in registered):
                items.append({"path": path, "class": "registered"})
                continue
            if any(under(layout, c, path) for c in claimed):
                # A container of legacy items: what is left in it is unknown.
                for sub in sorted(os.listdir(path)):
                    full = os.path.join(path, sub)
                    if os.path.isdir(full) and not os.path.islink(full) and \
                            not any(under(layout, full, c) or under(layout, c, full) for c in claimed):
                        record(full, "unregistered")
                continue
            if name.startswith("."):
                # Hidden directories are application data unless a rule names them.
                items.append({"path": path, "class": "registered", "why": "hidden application data"})
                continue
            record(path, "unregistered")
    return {"schema": "serveros/workspace-scan.v1", "device": layout["device"],
            "scanned_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            "layout": layout, "items": items}


# -- plan -------------------------------------------------------------------------

def _unique_work(facts: dict | None) -> bool | None:
    """True when removing this checkout would lose something; None when unknown."""
    if facts is None:
        return False
    if facts.get("dirty") is None or facts.get("unique_commits") is None:
        return None
    return bool(facts["dirty"] or facts["unique_commits"] or facts.get("stashes"))


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")[:80] or "unnamed"


def _worktree_name(path: str, facts: dict) -> str:
    base = os.path.basename(path.rstrip("\\/"))
    found = ITEM_ID.search(base) or ITEM_ID.search(facts.get("branch") or "")
    return found.group(1) if found else _safe_name(base)


def _step(action: str, item: dict, reason: str, to: str | None = None, **extra: Any) -> dict:
    step = {"action": action, "from": item["path"], "to": to, "reason": reason,
            "idle_days": item.get("idle_days"), "size_kb": item.get("size_kb"), **extra}
    if action.startswith("MOVE"):
        step["undo"] = f"move {to} back to {item['path']}"
    elif action.startswith("REMOVE"):
        step["undo"] = ("not reversible from disk; nothing in it is unique (every commit is on a "
                        "remote, no uncommitted change, no stash)")
    return step


def plan(scan_doc: dict[str, Any]) -> dict[str, Any]:
    layout = scan_doc["layout"]
    p = pmod(layout)
    src, worktrees, scratch = zone_path(layout, "src"), zone_path(layout, "worktrees"), zone_path(layout, "scratch")
    wt_days, scratch_days = retain_days(layout, "worktrees", 14), retain_days(layout, "scratch", 30)
    steps: list[dict[str, Any]] = []
    taken: set[str] = set()

    def target_free(path: str) -> str:
        """`path`, or `path.2`, `path.3` … when an earlier step or the disk has it."""
        candidate, n = path, 1
        while _norm(layout, candidate) in taken or os.path.exists(candidate):
            n += 1
            candidate = f"{path}.{n}"
        taken.add(_norm(layout, candidate))
        return candidate

    items = [i for i in scan_doc["items"] if i["class"] in ("legacy", "unregistered")]
    for item in items:
        if item.get("in_use"):
            steps.append(_step("DEFER", item, "in use by " + ", ".join(item["in_use"]) +
                               ": change that first, then move"))
    items = [i for i in items if not i.get("in_use")]

    # Repositories: one main clone per remote.
    repos = [i for i in items if i["class"] == "legacy" and i["git"] and i["git"]["kind"] == "repo"
             and i["rule"].get("as") in ("repo", "repo_or_worktree")]
    groups: dict[str, list[dict]] = {}
    for item in repos:
        key = item["git"]["remote_key"] or f"local:{item['path']}"
        groups.setdefault(key, []).append(item)
    for key, group in sorted(groups.items()):
        # The main clone is the one the worktrees hang from, then the most
        # recently worked one.
        group.sort(key=lambda i: (-len(i["git"]["worktrees"]), -(i["git"].get("last_commit") or 0)))
        name = repo_name(group[0]["git"]["remote"], p.basename(group[0]["path"]))
        main_target = p.join(src, _safe_name(name))
        existing = os.path.exists(main_target)
        for n, item in enumerate(group):
            unique = _unique_work(item["git"])
            if n == 0 and not existing:
                steps.append(_step("MOVE_REPO", item, f"main clone of {key}",
                                   to=target_free(main_target)))
            elif unique is False and not item["git"]["worktrees"]:
                steps.append(_step("REMOVE_CLONE", item,
                                   f"another clone of {key}; nothing in it is unique"))
            else:
                why = ("has work of its own" if unique else
                       "state unknown" if unique is None else "has worktrees")
                steps.append(_step("MOVE_REPO", item, f"another clone of {key} that {why}: kept beside the main one",
                                   to=target_free(p.join(src, _safe_name(f"{name}.{p.basename(item['path'])}")))))

    # Worktrees.
    for item in items:
        facts = item.get("git")
        # A worktree is moved with git, whichever rule found it: renaming it
        # by hand would cut it from its repository.
        if not (item["class"] == "legacy" and facts and facts["kind"] == "worktree"
                and item["rule"].get("as") != "report"):
            continue
        unique = _unique_work(facts)
        repo = repo_name(facts.get("remote"), p.basename(facts.get("main") or "repo"))
        if unique is False and item["idle_days"] >= wt_days:
            steps.append(_step("REMOVE_WORKTREE", item,
                               f"idle {item['idle_days']} days (limit {wt_days}); nothing in it is unique",
                               main=facts.get("main")))
        else:
            why = ("has work of its own" if unique else "state unknown" if unique is None
                   else f"active ({item['idle_days']} days)")
            steps.append(_step("MOVE_WORKTREE", item, why, main=facts.get("main"),
                               to=target_free(p.join(worktrees, _safe_name(repo),
                                                     _worktree_name(item["path"], facts)))))

    # Everything else a rule names.
    for item in items:
        if item["class"] != "legacy":
            continue
        facts, rule = item.get("git"), item["rule"]
        if facts and (facts["kind"] == "worktree" and rule.get("as") != "report" or
                      facts["kind"] == "repo" and rule.get("as") in ("repo", "worktree", "repo_or_worktree")):
            continue                                       # handled above
        nested_unique = [n for n in item.get("nested_git") or [] if _unique_work(n) is not False]
        if rule.get("as") == "report":
            steps.append(_step("REPORT", item, rule.get("why", "the rule asks for a look first")))
        elif rule.get("to"):
            to = absolute(layout, rule["to"].replace("{name}", p.basename(item["path"])))
            steps.append(_step("MOVE_DIR", item, rule.get("why", ""), to=target_free(to)))
        elif rule.get("as") in ("scratch", "repo_or_worktree", "repo", "worktree"):
            if not nested_unique and _unique_work(facts) is False and item["idle_days"] >= scratch_days:
                steps.append(_step("REMOVE_DIR", item,
                                   f"scratch idle {item['idle_days']} days (limit {scratch_days}); "
                                   f"no repository in it holds anything unique"))
            else:
                why = (f"holds {len(nested_unique)} checkout(s) with work of their own" if nested_unique
                       else f"scratch, active ({item['idle_days']} days)")
                steps.append(_step("MOVE_DIR", item, why,
                                   to=target_free(p.join(scratch, _safe_name(p.basename(item["path"]))))))
    for item in items:
        if item["class"] == "unregistered":
            steps.append(_step("REPORT", item, "not in the layout: register it, or give it a rule"))

    order = {"DEFER": 0, "REPORT": 1, "MOVE_WORKTREE": 2, "REMOVE_WORKTREE": 3, "MOVE_REPO": 4,
             "REMOVE_CLONE": 5, "MOVE_DIR": 6, "REMOVE_DIR": 7}
    steps.sort(key=lambda s: (order[s["action"]], s["from"]))
    for n, step in enumerate(steps, 1):
        step["id"] = n
    body = json.dumps([[s["action"], s["from"], s.get("to")] for s in steps], ensure_ascii=False)
    counts: dict[str, int] = {}
    for step in steps:
        counts[step["action"]] = counts.get(step["action"], 0) + 1
    return {"schema": "serveros/workspace-plan.v1", "device": layout["device"],
            "plan_id": hashlib.sha256(body.encode()).hexdigest()[:12],
            "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scanned_at": scan_doc.get("scanned_at"), "layout": layout,
            "counts": counts, "steps": steps}


# -- apply ------------------------------------------------------------------------

def _same_filesystem(a: str, b: str) -> bool:
    if os.name == "nt":
        return os.path.splitdrive(os.path.abspath(a))[0].lower() == \
            os.path.splitdrive(os.path.abspath(b))[0].lower()
    parent = b
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    return os.stat(a).st_dev == os.stat(parent).st_dev


def _link(old: str, new: str) -> str:
    """A link at the old path to the new one, so whatever still uses it works."""
    try:
        if os.name == "nt":
            done = subprocess.run(["cmd", "/c", "mklink", "/J", old, new], capture_output=True, text=True)
            return "junction" if done.returncode == 0 else "no link (mklink failed)"
        os.symlink(new, old, target_is_directory=True)
        return "symlink"
    except OSError as exc:
        return f"no link ({exc.__class__.__name__})"


def _recheck(step: dict, layout: dict, refs: list) -> str | None:
    """Why a step must not run now, or None."""
    src = step["from"]
    if not os.path.isdir(src) or os.path.islink(src):
        return "the source is gone or is already a link"
    for zone in layout.get("protected", []):
        if under(layout, src, absolute(layout, zone["path"])):
            return "protected"
    users = in_use(layout, src, refs)
    if users:
        return "in use by " + ", ".join(users)
    if step["action"].startswith("REMOVE"):
        facts = git_facts(src)
        if _unique_work(facts) is not False:
            return "it now holds something unique"
        for nested in nested_git(src):
            if _unique_work(git_facts(nested)) is not False:
                return f"{nested} now holds something unique"
    if step.get("to"):
        if os.path.exists(step["to"]):
            return "the target exists"
        if not _same_filesystem(src, step["to"]):
            return "the target is on another filesystem: move it by hand"
    return None


def apply(plan_doc: dict, *, approve: str, only: set[str] | None = None,
          ids: set[int] | None = None, really: bool = False,
          refs: list | None = None) -> dict[str, Any]:
    if approve != plan_doc["plan_id"]:
        raise LayoutError(f"--approve {approve!r} is not this plan's id ({plan_doc['plan_id']})")
    layout = plan_doc["layout"]
    refs = users_of_paths(layout) if refs is None else refs
    results = []
    for step in plan_doc["steps"]:
        if step["action"] in ("DEFER", "REPORT"):
            continue
        if only and step["action"] not in only:
            continue
        if ids and step["id"] not in ids:
            continue
        why_not = _recheck(step, layout, refs)
        result = {"id": step["id"], "action": step["action"], "from": step["from"], "to": step.get("to")}
        if why_not:
            results.append({**result, "result": "SKIPPED", "why": why_not})
            continue
        if not really:
            results.append({**result, "result": "WOULD_DO"})
            continue
        try:
            if step["action"] == "MOVE_WORKTREE":
                os.makedirs(os.path.dirname(step["to"]), exist_ok=True)
                done = subprocess.run(["git", "-C", step["main"], "worktree", "move", step["from"], step["to"]],
                                      capture_output=True, text=True, timeout=120)
                if done.returncode != 0:
                    raise RuntimeError(done.stderr.strip()[:300])
                result["link"] = _link(step["from"], step["to"])
            elif step["action"] in ("MOVE_REPO", "MOVE_DIR"):
                os.makedirs(os.path.dirname(step["to"]), exist_ok=True)
                os.rename(step["from"], step["to"])
                if os.path.isdir(os.path.join(step["to"], ".git")):
                    # Linked worktrees point back at the old .git: repair from the new place.
                    _git(step["to"], "worktree", "repair")
                result["link"] = _link(step["from"], step["to"])
            elif step["action"] == "REMOVE_WORKTREE":
                done = subprocess.run(["git", "-C", step["main"], "worktree", "remove", step["from"]],
                                      capture_output=True, text=True, timeout=120)
                if done.returncode != 0:
                    raise RuntimeError(done.stderr.strip()[:300])
            elif step["action"] in ("REMOVE_CLONE", "REMOVE_DIR"):
                result["contents"] = sorted(os.listdir(step["from"]))[:200]
                shutil.rmtree(step["from"])
            results.append({**result, "result": "DONE"})
        except Exception as exc:  # noqa: BLE001 - one step's failure does not stop the rest
            results.append({**result, "result": "FAILED", "why": f"{type(exc).__name__}: {exc}"[:300]})
    receipt = {"schema": "serveros/workspace-receipt.v1", "device": layout["device"],
               "plan_id": plan_doc["plan_id"], "applied": really,
               "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": results}
    if really:
        keep = os.path.join(layout["work_root"], ".governance", "receipts")
        os.makedirs(keep, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        with open(os.path.join(keep, f"{plan_doc['plan_id']}-{stamp}.json"), "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, ensure_ascii=False, indent=2)
    return receipt


# -- check ------------------------------------------------------------------------

def check(layout: dict, scan_doc: dict | None = None) -> dict[str, Any]:
    """The daily summary (R-1): what the layout does not know, and how much is
    still to migrate or reclaim."""
    scan_doc = scan_doc or scan(layout)
    made = plan(scan_doc)
    worktree_steps = [s for s in made["steps"] if s["action"] in ("MOVE_WORKTREE", "REMOVE_WORKTREE")]
    return {"schema": "serveros/workspace-check.v1", "device": layout["device"],
            "at": scan_doc["scanned_at"], "plan_id": made["plan_id"], "counts": made["counts"],
            "unregistered": [i["path"] for i in scan_doc["items"] if i["class"] == "unregistered"],
            "worktrees_outside_work": len(worktree_steps),
            "in_use_deferred": [s["from"] for s in made["steps"] if s["action"] == "DEFER"]}


# -- cli --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "plan", "apply", "check"):
        one = sub.add_parser(name)
        one.add_argument("--device", required=True)
        one.add_argument("--layout", help="a layout file instead of layouts/<device>.json")
        one.add_argument("--out")
        if name == "scan":
            one.add_argument("--sizes", action="store_true", help="also measure each item (slower)")
        if name == "plan":
            one.add_argument("--scan", required=True)
        if name == "apply":
            one.add_argument("--plan", required=True)
            one.add_argument("--approve", required=True, help="the plan_id the owner approved")
            one.add_argument("--only", help="comma-separated actions, e.g. MOVE_WORKTREE,REMOVE_WORKTREE")
            one.add_argument("--ids", help="comma-separated step ids")
            one.add_argument("--apply", action="store_true", help="really do it (default: say what would happen)")
    args = parser.parse_args(argv)
    try:
        layout = load_layout(args.device, args.layout)
        if args.command == "scan":
            out = scan(layout, sizes=args.sizes)
        elif args.command == "plan":
            out = plan(json.loads(Path(args.scan).read_text(encoding="utf-8")))
        elif args.command == "check":
            out = check(layout)
        else:
            out = apply(json.loads(Path(args.plan).read_text(encoding="utf-8")), approve=args.approve,
                        only=set(args.only.split(",")) if args.only else None,
                        ids={int(x) for x in args.ids.split(",")} if args.ids else None,
                        really=args.apply)
    except LayoutError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        summary = out.get("counts") or {k: v for k, v in out.items() if k in ("plan_id", "applied")}
        print(json.dumps({"wrote": args.out, **({"plan_id": out["plan_id"]} if "plan_id" in out else {}),
                          "summary": summary}, ensure_ascii=False))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
