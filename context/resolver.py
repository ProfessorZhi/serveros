#!/usr/bin/env python3
"""Resolve a Context Capsule for a ServerOS Item.

    python3 resolver.py LIT-0101
    python3 resolver.py LIT-0104 --json
    python3 resolver.py                 # infer the Item from the current directory

Roots are explicit-argument first, then environment, then THIS NODE'S OWN
PROFILE, and then a refusal — there is no built-in default. A caller that has
already resolved its own roots hands them over:

    python3 resolver.py LIT-0101 --workspace-root ... --artifacts-root ... --state-dir ...

A Capsule carries the minimum correct context required to start working, plus
locators. It carries no bodies, no full history and no secret values, and it
never triggers a broad scan: every field is read from a known locator.

Read-only by construction. This script never writes to a business worktree,
a test environment, a Run directory or ServerOS machine state.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to resolve a Context Capsule\n")
    raise SystemExit(2)

SCHEMA = "serveros/context-capsule.v1"

# Deliverable kinds that are publications, and therefore route to feishu-report.
PUBLICATION_DELIVERABLES = {
    "report", "milestone-report", "daily-report", "weekly-report", "feishu-report",
}

# A Run in any other state is still in flight. See workengine.model.RunState.
RUN_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "INVALID"}

KIND_CAPABILITIES = {
    "feature": ["work-management", "test-management"],
    "test": ["test-management", "work-management"],
    "investigation": ["information-management", "work-management"],
    "analysis": ["information-management", "work-management"],
    "ops": ["workspace-management", "work-management"],
    "report": ["feishu-report", "information-management"],
}


# --------------------------------------------------------------------------
# where this node's roots are, when nobody says
# --------------------------------------------------------------------------
#
# §Q6. This file used to answer `/opt/applications/data/home-hwz` — one
# machine's layout — as the data root. On any other node that made
# `infer_item` compare the caller's directory against a root that does not
# exist there, find nothing, and report "no item inferred from this
# directory": the same sentence a correct empty answer produces. A wrong
# answer that reads like a right one is worse than a refusal, so an
# unanswerable root raises into the handlers the callers already have.
#
# The precedence is `runtime/paths.py`'s, which is the authority for every
# other root in the system: `DATA_ROOT` is a seed, the device's own Profile may
# declare the role, and nothing is invented when neither answers. The two
# derivations kept here (`workspace`, `artifacts` under the data root) are the
# same ones `paths.py` keeps in `_SEED_DEFAULT_SUFFIX` — relative layout, not
# another device's absolute path.

HOST_PROFILE_ENV = "SERVEROS_HOST_PROFILE"
DEFAULT_HOST_PROFILE = Path("~/.config/serveros/host-profile.yaml")


class RootsUnresolved(RuntimeError):
    """No authority could say where this node's data root is."""


def _host_profile_roots() -> dict:
    """This node's declared roots, or {} — never a fabricated path."""
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


def _profile_root(*keys) -> Path | None:
    roots = _host_profile_roots()
    for key in keys:
        value = roots.get(key)
        if isinstance(value, str) and value:
            return Path(value)
    return None


def _data_root() -> Path:
    raw = os.environ.get("DATA_ROOT")
    if raw:
        return Path(raw)
    # `identity_home` answers too: a node whose home IS its data root says so
    # in its own Profile, in those words, rather than restating the path.
    found = _profile_root("data_root", "identity_home")
    if found is not None:
        return found
    raise RootsUnresolved(
        f"no data root: set DATA_ROOT, or point {HOST_PROFILE_ENV} at a host "
        "profile whose `node.roots` declares `data_root` or `identity_home`. "
        "This resolver carries no built-in path, because a path that is right "
        "on one machine is a wrong answer on the next one")


class Roots:
    """ServerOS root contract. Every locator is derived from a root, never from cwd.

    Precedence is explicit-argument first: a caller that already resolved its own
    roots (the `serverosctl` adapter does) must be able to hand them over, so that
    the CLI and this resolver cannot end up reading two different worlds.
    """

    def __init__(
        self,
        workspace_root: Path,
        artifacts_root: Path,
        state_db: Path,
        data_root: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.artifacts_root = artifacts_root
        self.state_db = state_db
        # Only used to shorten locators; never used to derive a path we read.
        self.data_root = data_root

    @classmethod
    def from_env(cls) -> "Roots":
        data_root = _data_root()
        workspace_root = Path(
            os.environ.get("WORKSPACE_ROOT")
            # The same reading as `artifacts_root` below, one row over. This
            # node's profile declares `workspace_root`, and it was COMPUTED from
            # `data_root` rather than READ. `architecture_check.py` reads that
            # row; `runtime/paths.py` derives `{data_root}/workspace` because the
            # FLEET document has no `workspace` role -- it binds `workspace.src`
            # and `workspace.worktrees`, and `_SEED_ROLE` is deliberately without
            # this seed for that reason. That exclusion is right about the fleet
            # document and says nothing about the host profile, which binds the
            # parent as a row of its own. An implementation that already asks the
            # host profile for `data_root` and `artifacts_root` should ask it for
            # this too. On this node the declared and the derived value coincide,
            # which is what let the omission survive.
            or str(_profile_root("workspace_root") or data_root / "workspace")
        )
        artifacts_root = Path(
            os.environ.get("SERVEROS_ARTIFACTS_ROOT")
            or os.environ.get("ARTIFACTS_ROOT")
            # The document's spelling first, then the older one.
            # `artifacts_root` is the row this node's profile declares.
            # `artifacts` is the ROLE name in `fleet/profiles.yaml`'s table,
            # borrowed here under the wrong document: the row was asked for in
            # the words of the file next door, so nothing answered, and this
            # root was COMPUTED from `data_root` rather than READ. On this node
            # the two coincide, which is what let it survive. `runtime/paths.py`
            # records the same reading about the same word one table over.
            #
            # Both keys are listed for the reason `_data_root` lists
            # `data_root` and `identity_home`: a profile that spells the row
            # either way answers, and the one the document uses is asked first.
            or str(_profile_root("artifacts_root", "artifacts")
                   or data_root / "artifacts")
        )
        state_dir = os.environ.get("SERVEROS_STATE_DIR")
        state_db = (
            Path(state_dir) / "serveros.db"
            if state_dir
            else data_root / "state/serveros/serveros.db"
        )
        return cls(workspace_root, artifacts_root, state_db, data_root=data_root)

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Roots":
        """Explicit arguments win; anything unnamed still falls back to the environment."""
        base = cls.from_env()
        workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else base.workspace_root
        artifacts_root = Path(args.artifacts_root).resolve() if args.artifacts_root else base.artifacts_root
        state_db = Path(args.state_dir).resolve() / "serveros.db" if args.state_dir else base.state_db
        return cls(workspace_root, artifacts_root, state_db, data_root=base.data_root)

    @property
    def projects_yaml(self) -> Path:
        return self.workspace_root / "ops/workspace/serveros/projects.yaml"

    @property
    def testing_dir(self) -> Path:
        """Project Testing Contract 与全局测试政策所在。与 projects.yaml 同源。

        projects.yaml 是项目的**身份**（它叫什么、仓库在哪），契约是项目的
        **测试语义**（它该怎么被测）。两者都是控制面的受 schema 约束的文档，
        所以它们放一起，而不是一个在控制面、一个在代码里。
        """
        return self.workspace_root / "ops/workspace/serveros/testing"

    @property
    def schema_dir(self) -> Path:
        """控制面自己声明的 schema。契约文档的合法取值（mutability、criticality、
        violation code）从那里读，不在 runtime 里另抄一份词表。"""
        return self.workspace_root / "ops/workspace/serveros/schemas"

    @property
    def records(self) -> Path:
        return self.workspace_root / "records/projects"

    @property
    def records_root(self) -> Path:
        """The records checkout root — the anchor for root-relative source refs."""
        return self.workspace_root / "records"

    @property
    def worktrees(self) -> Path:
        return self.workspace_root / "worktrees"

    @property
    def test_envs(self) -> Path:
        return self.workspace_root / "test-envs"

    @property
    def src(self) -> Path:
        return self.workspace_root / "src"


def _git(path: Path, *args: str) -> str | None:
    """Read-only git query. Returns None when the path is not a usable repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), *args],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace").strip()


def _read_yaml(path: Path) -> dict:
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def load_projects(roots: Roots) -> dict:
    return (_read_yaml(roots.projects_yaml).get("projects") or {})


def load_project_aliases(roots: Roots) -> tuple[dict, str]:
    """`project_aliases` 段 — 别名 → `{project, declared_by}`，以及为什么读不到。

    别名是一个已经在跑的项目的**另一个名字**（`litellm-observability` 就是
    `litellm`）。它不签发 ID，也不带 prefix，所以查它的人要的答案是「归谁」，
    不是「还有哪些项目」—— 这也是它不并进 `load_projects` 的原因：并进去会让
    「别名」和「项目」在调用方眼里变成同一种东西，而它们下游的处置完全不同。

    和 `load_projects` 一样，读不到就是空表。**但这里把空表分成了两种**，因为
    这两种在下游长得不一样：注册表里没有 `project_aliases` 时，一个别名会原样
    落到 `load_projects` 手里、在那里成为一行 UNREADABLE（「控制面还没写这份
    投影」）；而注册表**根本读不出来**时，所有别名都会变成 UNREADABLE —— 那时
    下游说的是真话但指错了原因，`why` 就是给那一刻准备的。`load_projects`
    不需要这个区分，因为它的空表和「没注册任何项目」是同一件事。
    """
    path = roots.projects_yaml
    try:
        document = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return {}, f"没有注册表：{path}"
    except OSError as exc:
        return {}, f"注册表读不出来：{path}（{exc}）"
    except yaml.YAMLError as exc:
        return {}, f"注册表不是 YAML：{path}（{exc.__class__.__name__}）"
    if not isinstance(document, dict):
        return {}, f"注册表解析成 {type(document).__name__}，不是映射：{path}"
    return (document.get("project_aliases") or {}), ""


def _query_state(roots: Roots, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Read ServerOS machine state read-only. Absent DB is not an error."""
    if not roots.state_db.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{roots.state_db}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        return list(conn.execute(sql, params))
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def find_item(roots: Roots, item_id: str) -> tuple[str, Path] | None:
    """Locate an Item's records directory across projects."""
    if not roots.records.is_dir():
        return None
    for project_dir in sorted(roots.records.iterdir()):
        candidate = project_dir / "items" / item_id
        if candidate.is_dir():
            return project_dir.name, candidate
    return None


def item_state(roots: Roots, item_id: str) -> dict:
    for row in _query_state(roots, "SELECT state, outcome FROM items_index WHERE item_id=?", (item_id,)):
        return {"state": row["state"], "outcome": row["outcome"]}
    return {"state": "UNKNOWN", "outcome": None}


def active_items(roots: Roots, exclude: str) -> list[str]:
    """Items currently in an active lifecycle state — resolved from live state."""
    return sorted(
        row["item_id"]
        for row in _query_state(roots, "SELECT item_id FROM items_index WHERE state='ACTIVE'")
        if row["item_id"] != exclude
    )


def held_leases(roots: Roots) -> list[str]:
    return sorted(row["resource"] for row in _query_state(roots, "SELECT resource FROM leases"))


def _development(roots: Roots, project_cfg: dict, item_id: str) -> dict:
    """The repo that owns this Item's development worktree, and its live Git state."""
    repo = project_cfg.get("worktrees_under")
    if not repo:
        canonical = project_cfg.get("canonical_repos") or []
        repo = canonical[0] if canonical else None
    out = {
        "repo": repo, "worktree": None, "branch": None,
        "revision": None, "detached": None, "dirty": None,
    }
    if not repo:
        return out
    worktree = roots.worktrees / repo / item_id
    if not worktree.is_dir():
        return out
    out["worktree"] = str(worktree)
    branch = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    # A detached worktree is normal for test and investigation Items, so report
    # it explicitly instead of surfacing the literal "HEAD" as a branch name.
    out["detached"] = branch == "HEAD"
    out["branch"] = None if out["detached"] else branch
    out["revision"] = _git(worktree, "rev-parse", "HEAD")
    status = _git(worktree, "status", "--porcelain")
    out["dirty"] = None if status is None else bool(status)
    return out


def _delivery(roots: Roots, project_cfg: dict) -> dict:
    """The delivery-target repo and, only when determinable, its integration branch."""
    repo = project_cfg.get("enterprise_source")
    if not repo:
        return {"repo": None, "target_branch": None}
    recorded = project_cfg.get("delivery_branch")
    if recorded:
        return {"repo": repo, "target_branch": recorded}
    # Fall back only when the answer is unambiguous: exactly one update/* branch.
    listing = _git(roots.src / repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    if listing:
        candidates = [b for b in listing.splitlines() if b.startswith("update/")]
        if len(candidates) == 1:
            return {"repo": repo, "target_branch": candidates[0]}
    return {"repo": repo, "target_branch": None}


def _is_title_line(line: str) -> bool:
    """Headings and bold-only lines are titles, not the objective sentence."""
    if line.startswith("#"):
        return True
    if line.startswith("**") and line.endswith("**") and line.count("**") == 2:
        return True
    return len(line) < 12


def _objective(card: dict, item_dir: Path) -> dict:
    summary = ""
    brief = item_dir / "brief.md"
    if brief.is_file():
        for line in brief.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not _is_title_line(line):
                summary = line
                break
    return {"summary": summary, "acceptance": list(card.get("acceptance") or [])}


# URI-shaped locators name a thing rather than a file, so they are carried
# verbatim. Kept as an explicit scheme list instead of a general URI parser:
# adding a scheme should be a deliberate act, not an accident of colon-splitting.
_TYPED_SCHEMES = ("conversation", "feishu", "github", "http", "https")

# Prefixes that card authors write relative to the records checkout, not to the
# Item's own directory.
_RECORDS_RELATIVE_PREFIXES = ("projects/", "information/")


def _is_typed_locator(ref: str) -> bool:
    if "://" in ref:
        return True
    scheme, sep, _ = ref.partition(":")
    return bool(sep) and scheme.lower() in _TYPED_SCHEMES


def _resolve_source_ref(roots: Roots, ref: str, item_dir: Path) -> str:
    """Resolve one `source_refs` entry into the locator the Capsule reports.

    `source_refs` is a mixed namespace, so the anchor depends on the form. Getting
    this wrong hands an Agent a locator that does not exist, which is worse than
    handing it none:

        /abs/path                    -> normalized absolute (shortened to a root)
        projects/... information/... -> relative to the records checkout
        conversation: feishu:// ...  -> carried verbatim
        anything else                -> relative to the Item's own directory
    """
    if _is_typed_locator(ref):
        return ref
    path, sep, fragment = ref.partition("#")
    if not path.startswith("/"):
        if path.startswith(_RECORDS_RELATIVE_PREFIXES):
            anchor = roots.records_root / path
        else:
            anchor = item_dir / path
    else:
        anchor = Path(path)
    resolved = relative_locator(roots, str(anchor.resolve()))
    return resolved + (sep + fragment if sep else "")


def _relevant_sources(roots: Roots, card: dict, item_dir: Path) -> list[str]:
    return [_resolve_source_ref(roots, str(ref), item_dir)
            for ref in card.get("source_refs") or []]


def relative_locator(roots: Roots, path: str) -> str:
    """Prefer root-relative locators so the Capsule is not machine-bound."""
    for root in (roots.workspace_root, roots.data_root):
        prefix = str(root) + "/"
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _runs(roots: Roots, project: str, item_id: str) -> dict:
    """Run safety: which Run is latest, and which are still in flight.

    `latest` is ordering only. It is never a writable target: a Run may be
    written to only by the execution that explicitly attached to it via
    `serverosctl context attach`. Every other in-flight Run is listed under
    `active` so a new Agent treats it as protected context instead of a place
    to write. This grants nothing — hence `writable` is false for the Capsule
    as a whole.
    """
    root = roots.artifacts_root / "work-items" / project / item_id
    rows = [
        row
        for row in _query_state(
            roots,
            "SELECT run_id, state, artifact_dir FROM runs WHERE item_id=? ORDER BY run_id",
            (item_id,),
        )
    ]
    if not rows:
        # No state records. Fall back to the directory listing, which cannot
        # carry a state — so never claim one.
        latest = None
        if root.is_dir():
            dirs = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("RUN-"))
            if dirs:
                latest = {"id": dirs[-1].name, "state": None, "locator": str(dirs[-1])}
        return {"latest": latest, "active": [], "writable": False}
    return {
        "latest": _run_entry(rows[-1]),
        "active": [_run_entry(row) for row in rows if row["state"] not in RUN_TERMINAL],
        "writable": False,
    }


def _run_entry(row: sqlite3.Row) -> dict:
    return {"id": row["run_id"], "state": row["state"], "locator": row["artifact_dir"] or None}


def _latest_deliverable(roots: Roots, item_id: str) -> str | None:
    rows = _query_state(
        roots, "SELECT locator FROM deliverables WHERE item_id=? ORDER BY rowid DESC LIMIT 1", (item_id,)
    )
    return rows[0]["locator"] if rows else None


def resolve(roots: Roots, item_id: str) -> dict:
    found = find_item(roots, item_id)
    if not found:
        raise ValueError(f"unknown Item: {item_id}")
    project, item_dir = found
    card = _read_yaml(item_dir / "card.yaml")
    project_cfg = load_projects(roots).get(project) or {}
    state = item_state(roots, item_id)

    test_env_root = project_cfg.get("test_envs")
    test_env = None
    if test_env_root:
        candidate = roots.workspace_root / test_env_root / item_id
        if candidate.is_dir():
            test_env = str(candidate)

    evidence_root = roots.artifacts_root / "work-items" / project / item_id

    protected = active_items(roots, exclude=item_id)
    protected += [r for r in held_leases(roots) if r not in protected]

    kind = card.get("kind") or ""
    hints = list(KIND_CAPABILITIES.get(kind, ["work-management"]))
    # Only publication deliverables route to feishu-report. `test-report` is a
    # test artifact owned by test-management, not a Feishu publication.
    deliverables = {str(d) for d in (card.get("expected_deliverables") or [])}
    if deliverables & PUBLICATION_DELIVERABLES and "feishu-report" not in hints:
        hints.append("feishu-report")

    return {
        "schema": SCHEMA,
        "project": project,
        "item": {"id": item_id, "title": card.get("title") or "", "kind": kind},
        "objective": _objective(card, item_dir),
        "development": _development(roots, project_cfg, item_id),
        "test": {"environment": test_env},
        "evidence": {"root": str(evidence_root)},
        "runs": _runs(roots, project, item_id),
        "delivery": _delivery(roots, project_cfg),
        "current": {
            "state": state["state"],
            "outcome": state["outcome"],
            "latest_deliverable": _latest_deliverable(roots, item_id),
        },
        "relevant_sources": _relevant_sources(roots, card, item_dir),
        "protected": protected,
        "capability_hints": hints,
    }


def infer_item(roots: Roots, cwd: Path) -> str:
    """Infer the Item from cwd, but only when it is unambiguous."""
    cwd = cwd.resolve()
    candidates: set[str] = set()
    # The depth is how many parts lie between the base and the Item, counting
    # the Item itself. All three layouts have the same shape, so all three are
    # 2: `worktrees/<repo>/<ITEM>`, `test-envs/<repo>/<ITEM>` and
    # `artifacts/work-items/<project>/<ITEM>`. The third said 3 until §Q6 —
    # one more than its own layout has — so it matched nothing, ever, on any
    # node. It was invisible beside the Beijing root in this file: that root
    # made the first two patterns fail as well, so a caller reading "cannot
    # infer an Item" had one cause to blame, and it was the other one.
    #
    # Deeper paths still resolve, because the check is on position rather than
    # on the path ending there: `.../MUC-0101/RUN-0001` infers MUC-0101.
    patterns = (
        (roots.worktrees, 2),      # worktrees/<repo>/<ITEM>
        (roots.test_envs, 2),      # test-envs/<repo>/<ITEM>
        (roots.artifacts_root / "work-items", 2),  # artifacts/work-items/<project>/<ITEM>
    )
    for base, depth in patterns:
        try:
            rel = cwd.relative_to(base)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) >= depth and parts[depth - 1].startswith(("LIT-", "MUC-", "SRV-")):
            candidates.add(parts[depth - 1])
    if len(candidates) == 1:
        return candidates.pop()
    if not candidates:
        raise ValueError(
            f"cannot infer an Item from {cwd}; pass an explicit Item id "
            "(expected to be inside worktrees/<repo>/<ITEM>, test-envs/<repo>/<ITEM> "
            "or artifacts/work-items/<project>/<ITEM>)"
        )
    raise ValueError(f"ambiguous Item for {cwd}: {', '.join(sorted(candidates))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resolve a ServerOS Context Capsule")
    ap.add_argument("item", nargs="?", help="Item id, e.g. LIT-0101; inferred from cwd if omitted")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of YAML")
    ap.add_argument("--cwd", type=Path, default=Path.cwd(), help="directory used for Item inference")
    ap.add_argument("--workspace-root", help="explicit workspace root; wins over the environment")
    ap.add_argument("--artifacts-root", help="explicit artifacts root; wins over the environment")
    ap.add_argument("--state-dir", help="explicit state directory; wins over the environment")
    args = ap.parse_args()
    try:
        roots = Roots.from_args(args)
        item_id = args.item or infer_item(roots, args.cwd)
        capsule = resolve(roots, item_id)
    except (OSError, ValueError, RootsUnresolved) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(capsule, ensure_ascii=False, indent=2))
    else:
        print(yaml.safe_dump(capsule, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
