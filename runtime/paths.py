#!/usr/bin/env python3
"""Path governance — so an agent stops inventing durable paths.

The problem this solves
----------------------
An agent writing an artifact picks a path. It picks a plausible one:
`data/serveros/observations.json`, `logs/health.log`, `artifacts/legacy/…`.
Every one of those is a decision that outlives the turn that made it, and
nothing checks it. The result on this machine is a set of near-canonical
directories — `artifacts/` beside `workspace/artifacts`, three places logs
could live, an `old-artifacts` nobody dares delete — because each was
individually reasonable and no one was comparing.

So resolution is not "join the strings". It is a lookup against a declared
root contract with a closed vocabulary, and a refusal when the answer is not
one the contract can give.

Two operations, deliberately different:

    resolve(...)  a NAME becomes a path. Deterministic: same input, same
                  output, no cwd, no environment guessing, no fallback root.
    admit(path)   a path is checked against the contract and classified, or
                  refused. This is the gate — the thing that runs before a
                  write rather than after a mistake.

`admit` is a decision, not a permission slip: it returns a verdict and it
writes nothing. The caller that acts on it is elsewhere, on purpose.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# The root contract.
#
# DATA_ROOT's top level is CLOSED (§31): no new top-level semantic roots. The
# list below is the complete set, and `ROOT_CONTRACT` is asserted against it
# in tests so that adding a root is a deliberate edit rather than a directory
# that appeared.
# ---------------------------------------------------------------------------

DATA_ROOT_TOP_LEVEL = (
    "artifacts", "caches", "data", "downloads", "envs", "logs",
    "runtime-assets", "software", "state", "tmp", "toolchains", "workspace",
)

# WORKSPACE_ROOT's top level is closed too (§32).
WORKSPACE_TOP_LEVEL = ("src", "worktrees", "test-envs", "ops", "knowledge", "records")

# `state/serveros/` is the shape §35 asks for, with the adjustment that the
# runtime subdirectories are declared here rather than discovered, so that
# `resolve` can name one that does not exist yet and `admit` can refuse a
# sibling somebody invented at 2am.
STATE_SERVEROS_TOP_LEVEL = (
    "node", "context", "abilities", "runtime", "fleet",
    "source-index", "locks", "projections",
)
STATE_SERVEROS_RUNTIME = ("registry", "observed", "health", "hygiene", "resources")

# Artifacts has exactly two canonical namespaces for NEW writes (§34).
ARTIFACT_NAMESPACES = ("work-items", "skills")

# Named roots. The keys are the vocabulary; there is no other way to name one.
ROOTS = {
    "home": "{HOME}",
    "data": "{DATA_ROOT}",
    "workspace": "{WORKSPACE_ROOT}",
    "artifacts": "{ARTIFACTS_ROOT}",
    "toolchains": "{TOOLCHAINS_ROOT}",
    "state": "{DATA_ROOT}/state/serveros",
    "logs": "{DATA_ROOT}/logs/serveros",
    "runtime-assets": "{DATA_ROOT}/runtime-assets/serveros",
    "tmp": "{DATA_ROOT}/tmp",
    "caches": "{DATA_ROOT}/caches",
}

# Suffixes that must never be created under DATA_ROOT (§34, §42): a
# near-canonical directory is worse than a wrong one, because it survives.
#
# Bare `old` and `new` are absent deliberately: they are plausible names for
# a real run or a real segment, and a check that refuses legitimate work gets
# switched off. The entries here are the ones that are unambiguous.
FORBIDDEN_SUFFIXES = ("legacy", "legacy-artifacts", "old-artifacts",
                      "backup", "backups", "compat")

# `.serveros` in HOME stays small (§38): identity and binding only.
HOME_DOT_SERVEROS_ALLOWED = ("identity.yaml", "targets.yaml", "profile.yaml", "config.yaml")
HOME_DOT_SERVEROS_FORBIDDEN = ("reports", "observations", "registry", "proposals", "artifacts")


class PathError(Exception):
    """A path could not be resolved or admitted. Never a fallback."""


class PathRefused(PathError):
    """A path is outside the contract. The caller must not fall back to cwd."""


def _expand(roots: dict | None = None) -> dict:
    roots = roots or {}
    home = roots.get("home") or os.environ.get("HOME") or "/home/hwz"
    data_root = roots.get("data_root") or os.environ.get("DATA_ROOT") or "/opt/applications/data/home-hwz"
    workspace = roots.get("workspace_root") or os.environ.get("WORKSPACE_ROOT") or f"{data_root}/workspace"
    artifacts = roots.get("artifacts_root") or os.environ.get("ARTIFACTS_ROOT") or f"{data_root}/artifacts"
    toolchains = roots.get("toolchains_root") or os.environ.get("TOOLCHAINS_ROOT") or f"{data_root}/toolchains"
    values = {
        "HOME": home, "DATA_ROOT": data_root, "WORKSPACE_ROOT": workspace,
        "ARTIFACTS_ROOT": artifacts, "TOOLCHAINS_ROOT": toolchains,
    }
    return {name: template.format(**values) for name, template in ROOTS.items()}


class Paths:
    """The root contract for one node, resolved once.

    Injectable so a test can exercise resolution against a `tmp_path` without
    the machine's real roots, and so a wrong root can never be reached by
    falling back to whatever the environment happened to say.
    """

    def __init__(self, roots: dict | None = None) -> None:
        self.roots = _expand(roots)

    # -- resolution --------------------------------------------------------

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self.roots))

    def root(self, name: str) -> Path:
        if name not in self.roots:
            raise PathRefused(
                f"{name!r} is not a declared root. Known roots: "
                f"{list(self.names)}. There is no default root, and an "
                f"unresolved path must not fall back to the working directory."
            )
        return Path(self.roots[name])

    def resolve(self, ref: str) -> dict:
        """A NAMED reference becomes an absolute path.

        `ref` is `<root>` or `<root>/<suffix>`. The first segment must name a
        declared root; everything after it is joined literally. Nothing here
        consults the cwd, expands `~`, or infers a root from a pattern — the
        same input always produces the same output because there is nothing
        else for it to depend on.
        """
        if not ref or not isinstance(ref, str):
            raise PathError("a path reference is required")
        if ref.startswith("/") or ref.startswith("~"):
            raise PathRefused(
                f"{ref!r} is already an absolute or HOME-relative path. "
                f"`resolve` names a root; use `admit` to check an absolute "
                f"path against the contract."
            )
        head, _, suffix = ref.partition("/")
        if head not in self.roots:
            raise PathRefused(
                f"{ref!r}: {head!r} is not a declared root. Known roots: "
                f"{list(self.names)}."
            )
        if ".." in Path(suffix).parts:
            raise PathRefused(f"{ref!r}: `..` is not part of a resolved path.")
        return {
            "ref": ref,
            "root": head,
            "path": str(self.root(head) / suffix) if suffix else str(self.root(head)),
            "exists": (self.root(head) / suffix).exists() if suffix else self.root(head).exists(),
        }

    # -- the leaf contract -------------------------------------------------

    def state_runtime(self, name: str) -> dict:
        """A `state/serveros/runtime/<name>` leaf, which is a closed set."""
        if name not in STATE_SERVEROS_RUNTIME:
            raise PathRefused(
                f"{name!r} is not a declared runtime state area. Known: "
                f"{list(STATE_SERVEROS_RUNTIME)}. Mutable durable machine "
                f"state lives here and nowhere else."
            )
        return self.resolve(f"state/runtime/{name}")

    def work_item_run(self, project: str, item: str, run: str) -> dict:
        """`artifacts/work-items/<project>/<ITEM>/<RUN>/` — the §34 shape."""
        for label, value in (("project", project), ("item", item), ("run", run)):
            if not value or "/" in value or value in (".", ".."):
                raise PathRefused(f"{label} must be a single path segment, not {value!r}")
        return self.resolve(f"artifacts/work-items/{project}/{item}/{run}")

    def skill_run(self, skill: str, run: str) -> dict:
        """`artifacts/skills/<skill>/<RUN>/` — for work with no business item."""
        for label, value in (("skill", skill), ("run", run)):
            if not value or "/" in value or value in (".", ".."):
                raise PathRefused(f"{label} must be a single path segment, not {value!r}")
        return self.resolve(f"artifacts/skills/{skill}/{run}")

    # -- admission ---------------------------------------------------------

    def admit(self, path: str, *, writing: bool = True) -> dict:
        """Check an absolute path against the contract.

        Refusals, in the order they are checked. Each one is a thing that has
        actually happened here, not a hypothetical:

          * relative path — resolution must have happened already; a relative
            path means somebody is about to write next to the cwd.
          * `..` — traversal out of a root is how a "safe" path escapes it.
          * outside every declared root — `~/files`, `workspace/artifacts`.
          * a symlink whose target leaves the root — `admit` re-checks after
            resolving, because a path can be inside a root and point outside.
          * a near-canonical name (`legacy`, `old`, `backup`) — these outlive
            the task that made them and become structure by accident.
          * a write into `workspace/src` — read-mostly (§33); normal changes
            belong in a worktree.
          * a write into `~/.serveros` beyond its small allowlist (§38).
        """
        if not path or not isinstance(path, str):
            raise PathError("a path is required")
        if not path.startswith("/"):
            raise PathRefused(
                f"{path!r} is not absolute. Resolve it against a declared root "
                f"first; a relative path would be joined to whatever the "
                f"working directory happens to be."
            )
        raw = Path(path)
        if ".." in raw.parts:
            raise PathRefused(f"{path!r} contains `..`. Traversal is not resolved here.")

        resolved = Path(os.path.realpath(path))
        best = None
        for name, root in self.roots.items():
            root_path = Path(os.path.realpath(root))
            if resolved == root_path or root_path in resolved.parents:
                if best is None or len(str(root_path)) > len(str(best[1])):
                    best = (name, root_path)

        if best is None:
            # Name the common wrong answers, because they are the ones people
            # actually type, and "refused" alone teaches nothing.
            hint = ""
            if "/workspace/artifacts" in str(raw) or "/workspace/toolchains" in str(raw):
                hint = (" This looks like a substitute root under workspace/. "
                        "artifacts/ and toolchains/ live under DATA_ROOT.")
            elif str(raw).startswith(str(Path.home())) and "/files" in str(raw):
                hint = " `~/files` is not a root."
            raise PathRefused(
                f"{path!r} is outside every declared root. Known roots: "
                f"{ {n: str(self.root(n)) for n in self.names} }.{hint}"
            )

        root_name, root_path = best
        rel = resolved.relative_to(root_path)
        parts = rel.parts

        # Checked at the FIRST level inside the root only. That is exactly
        # where a substitute root appears (`artifacts/legacy/…`), and checking
        # every segment would refuse legitimate deep names — a run genuinely
        # called `RUN-old` is not a near-canonical root.
        if parts and parts[0].lower() in FORBIDDEN_SUFFIXES:
            raise PathRefused(
                f"{path!r}: {parts[0]!r} is a near-canonical directory name. "
                f"`legacy/`, `old/` and `backup/` outlive the task that "
                f"created them and become structure by accident. Migrate the "
                f"contents and retire the path instead."
            )

        # Inside a root is not the same as in the right place. `workspace/
        # artifacts` and `~/files` are both inside a declared root and both
        # are substitute roots the layout forbids — being under a root is
        # necessary, not sufficient, and a check that stops at containment
        # admits exactly the drift it was written to prevent.
        if writing:
            self._check_top_level(path, root_name, parts)

        if writing and root_name == "workspace" and parts and parts[0] == "src":
            raise PathRefused(
                f"{path!r} is under workspace/src, which is read-mostly (§33). "
                f"A normal code change belongs in a worktree: "
                f"{self.root('workspace')}/worktrees/. Fetching, inspecting and "
                f"ref discovery are the permitted operations on src."
            )

        if writing and root_name == "home":
            dot = Path(os.path.realpath(Path.home() / ".serveros"))
            if resolved == dot or dot in resolved.parents:
                leaf = resolved.relative_to(dot).parts[0] if resolved != dot else ""
                if leaf and leaf not in HOME_DOT_SERVEROS_ALLOWED:
                    raise PathRefused(
                        f"{path!r}: `~/.serveros` holds identity and binding "
                        f"only (§38). Allowed: {list(HOME_DOT_SERVEROS_ALLOWED)}. "
                        f"Reports, proposals, large observations and machine "
                        f"state belong under DATA_ROOT/state/serveros. If this "
                        f"is drifting into a second state root, that is the "
                        f"signal to move it rather than to widen this list."
                    )

        return {
            "path": str(raw),
            "resolved": str(resolved),
            "root": root_name,
            "relative": str(rel) if str(rel) != "." else "",
            "canonical": self._classify(root_name, parts),
            "admitted": True,
        }

    def _check_top_level(self, path: str, root_name: str, parts: tuple[str, ...]) -> None:
        if not parts:
            return
        head = parts[0]
        if root_name == "workspace" and head not in WORKSPACE_TOP_LEVEL:
            raise PathRefused(
                f"{path!r}: workspace/{head} is not one of {list(WORKSPACE_TOP_LEVEL)}. "
                f"WORKSPACE_ROOT carries exactly those six, and a seventh is how "
                f"`workspace/artifacts` came to exist beside `artifacts/`."
            )
        if root_name == "data" and head not in DATA_ROOT_TOP_LEVEL:
            raise PathRefused(
                f"{path!r}: {head!r} is not one of DATA_ROOT's declared top-level "
                f"areas {list(DATA_ROOT_TOP_LEVEL)}. No new top-level semantic "
                f"root gets created by one write at a time."
            )
        if root_name == "home":
            # Checked before the existence test, so that the refusal names the
            # specific rule rather than the general one. A caller who writes
            # `~/.serveros/reports/` needs to hear "identity and binding
            # only", not "that parent does not exist".
            if head == "files":
                raise PathRefused(
                    f"{path!r}: `~/files` is a substitute root named in the "
                    f"layout policy. It is refused even when it exists, "
                    f"because the fix is to move what is in it, not to keep "
                    f"writing to it."
                )
            dot_serveros = Path(self.roots["home"]) / ".serveros"
            if parts[0] == ".serveros":
                leaf = parts[1] if len(parts) > 1 else ""
                if leaf and leaf not in HOME_DOT_SERVEROS_ALLOWED:
                    raise PathRefused(
                        f"{path!r}: `~/.serveros` holds identity and binding "
                        f"only (§38). Allowed: {list(HOME_DOT_SERVEROS_ALLOWED)}. "
                        f"Reports, proposals, large observations and machine "
                        f"state belong under DATA_ROOT/state/serveros. If this "
                        f"is drifting into a second state root, that is the "
                        f"signal to move it rather than to widen this list."
                    )
                return
            # HOME's shape is not ServerOS's to extend. A write whose first
            # segment does not already exist is a new top-level directory
            # under HOME, which is how `~/files` and `~/artifacts` appear.
            first = Path(self.roots["home"]) / head
            if not first.exists():
                raise PathRefused(
                    f"{path!r}: {first} does not exist, so this write would "
                    f"create a new top-level directory under HOME. HOME holds "
                    f"bootstrapping and binding; durable output belongs under "
                    f"DATA_ROOT."
                )

    def _classify(self, root_name: str, parts: tuple[str, ...]) -> str:
        """What KIND of path this is, so a caller can tell canonical from
        merely-permitted. Both are admitted; only one is where things go."""
        if root_name == "artifacts":
            if parts and parts[0] in ARTIFACT_NAMESPACES:
                if len(parts) >= 4 and parts[0] == "work-items":
                    return "CANONICAL_WORK_ITEM_RUN"
                if len(parts) >= 3 and parts[0] == "skills":
                    return "CANONICAL_SKILL_RUN"
                return "CANONICAL_NAMESPACE"
            return "ADMITTED_NON_CANONICAL"
        if root_name == "state":
            if parts and parts[0] in STATE_SERVEROS_TOP_LEVEL:
                return "CANONICAL_STATE"
            return "ADMITTED_NON_CANONICAL"
        if root_name == "workspace":
            if parts and parts[0] in WORKSPACE_TOP_LEVEL:
                return "CANONICAL_WORKSPACE"
            return "ADMITTED_NON_CANONICAL"
        return "ADMITTED"

    # -- the whole contract, for inspection --------------------------------

    def contract(self) -> dict:
        return {
            "roots": {n: str(self.root(n)) for n in self.names},
            "data_root_top_level": list(DATA_ROOT_TOP_LEVEL),
            "workspace_top_level": list(WORKSPACE_TOP_LEVEL),
            "state_serveros_top_level": list(STATE_SERVEROS_TOP_LEVEL),
            "state_serveros_runtime": list(STATE_SERVEROS_RUNTIME),
            "artifact_namespaces": list(ARTIFACT_NAMESPACES),
            "forbidden_suffixes": list(FORBIDDEN_SUFFIXES),
            "note": (
                "Resolution is a lookup, not a join. There is no default root "
                "and no cwd fallback; an unresolvable reference is refused."
            ),
        }
