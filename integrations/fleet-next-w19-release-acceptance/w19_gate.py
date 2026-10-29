"""The W19 independent release gate: six layers, two verdicts, one retire map.

Why two verdicts. #515 says "开发合并 ≠ 正式 RELEASED" in its own words, and the
two questions have genuinely different answers today: nothing has run on a
device (`REAL_DEVICE = NOT_RUN`), which is enough to refuse to call anything
V1, and is not a reason to refuse a development merge. A gate with one verdict
would have to pick one of those to be wrong about.

Why findings carry a scope. A defect that is already in `main` — reachable
without this landing — must not be able to masquerade as a reason to reject the
landing, and must not be able to disappear behind a green landing either. So a
finding says which verdict it bears on, and the verdict functions read that
instead of guessing from severity.

Why the retire map is measured. "No own delta" is a claim about blobs, not
about a diff summary: two trees can show the same file list and still differ in
content, and `core.autocrlf` makes the worktree lie about content. Every fact
here comes from `git ls-tree`, and `compare()` is the only thing that produces
one.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence, Tuple

__all__ = [
    "LAYERS", "MERGE_LAYERS", "READY", "BLOCKED", "INCOMPLETE", "NOT_RUN",
    "BLOCKER", "NOTE", "LANDING", "RELEASE", "MAIN",
    "VERIFIED_AND_MERGED", "OWN_DELTA_PENDING", "SUPERSEDED_ANCESTOR",
    "BLOCKED_OWNER", "RELEASE_NOT_RUN", "RETIRE_CLASSES",
    "Finding", "Gate", "Comparison", "PrFacts", "compare", "is_ancestor",
]

# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #
LAYERS: Tuple[str, ...] = (
    "CODE", "UNIT", "CI", "INDEPENDENT_QA", "ARTIFACT", "REAL_DEVICE")

# The five a landing can be decided on. REAL_DEVICE is deliberately outside:
# nothing here has been installed on a device, and a missing device run must
# not be able to block a merge or be laundered into one.
MERGE_LAYERS: Tuple[str, ...] = LAYERS[:5]

READY = "READY"
BLOCKED = "BLOCKED"
INCOMPLETE = "INCOMPLETE"
NOT_RUN = "NOT_RUN"
VERDICTS = frozenset({READY, BLOCKED, INCOMPLETE, NOT_RUN})

BLOCKER = "BLOCKER"
NOTE = "NOTE"
SEVERITIES = frozenset({BLOCKER, NOTE})

LANDING = "LANDING"
RELEASE = "RELEASE"
SCOPES = frozenset({LANDING, RELEASE})

MAIN = "main"

VERIFIED_AND_MERGED = "VERIFIED_AND_MERGED"
OWN_DELTA_PENDING = "OWN_DELTA_PENDING"
SUPERSEDED_ANCESTOR = "SUPERSEDED_ANCESTOR"
BLOCKED_OWNER = "BLOCKED_OWNER"
RELEASE_NOT_RUN = "RELEASE_NOT_RUN"
RETIRE_CLASSES = (VERIFIED_AND_MERGED, OWN_DELTA_PENDING, SUPERSEDED_ANCESTOR,
                  BLOCKED_OWNER, RELEASE_NOT_RUN)


# --------------------------------------------------------------------------- #
# a finding
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Finding:
    """One measured defect. `scope` is which verdict it is allowed to move.

    A NOTE is never allowed to move a verdict, whatever its scope says — it is
    there to be read, not to be counted.
    """

    severity: str
    layer: str
    owner: str
    path: str
    summary: str
    scope: str

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.layer not in LAYERS:
            raise ValueError(f"unknown layer {self.layer!r}")
        if self.scope not in SCOPES:
            raise ValueError(f"unknown scope {self.scope!r}")

    def moves(self, verdict: str) -> bool:
        if self.severity != BLOCKER:
            return False
        return self.scope == verdict or self.scope == LANDING


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Gate:
    layers: Tuple[Tuple[str, str], ...]
    findings: Tuple[Finding, ...] = ()

    @classmethod
    def from_layers(cls, layers: Mapping[str, str],
                    findings: Iterable[Finding] = ()) -> "Gate":
        unknown = sorted(set(layers) - set(LAYERS))
        if unknown:
            raise ValueError(f"unknown layer(s): {unknown}")
        for name, verdict in layers.items():
            if verdict not in VERDICTS:
                raise ValueError(f"{name}: unknown verdict {verdict!r}")
        ordered = tuple((name, layers[name]) for name in LAYERS if name in layers)
        return cls(ordered, tuple(findings))

    # -- readings ----------------------------------------------------------- #
    def verdict_of(self, layer: str) -> str:
        for name, verdict in self.layers:
            if name == layer:
                return verdict
        return INCOMPLETE

    def missing_layers(self) -> Tuple[str, ...]:
        return tuple(name for name in LAYERS
                     if name not in {n for n, _ in self.layers})

    def blockers_for(self, verdict: str) -> Tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.moves(verdict))

    def _merge_layers_ready(self) -> bool:
        return all(self.verdict_of(name) == READY for name in MERGE_LAYERS)

    # -- the two decisions -------------------------------------------------- #
    def landing_verdict(self) -> str:
        if not self._merge_layers_ready():
            return BLOCKED
        if self.blockers_for(LANDING):
            return BLOCKED
        return READY

    def release_verdict(self) -> str:
        if self.landing_verdict() != READY:
            return BLOCKED
        if not all(self.verdict_of(name) == READY for name in LAYERS):
            return BLOCKED
        if self.blockers_for(RELEASE):
            return BLOCKED
        return READY

    def as_dict(self) -> dict:
        return {
            "layers": [{"layer": name, "verdict": verdict}
                       for name, verdict in self.layers],
            "missing_layers": list(self.missing_layers()),
            "findings": [{"severity": f.severity, "layer": f.layer,
                          "owner": f.owner, "path": f.path,
                          "summary": f.summary, "scope": f.scope}
                         for f in self.findings],
            "landing_verdict": self.landing_verdict(),
            "release_verdict": self.release_verdict(),
        }


# --------------------------------------------------------------------------- #
# measuring a tree against main
# --------------------------------------------------------------------------- #
_GIT_ENV = dict(
    os.environ,
    GIT_CONFIG_GLOBAL=os.devnull,
    GIT_CONFIG_SYSTEM=os.devnull,
    GIT_ATTR_NOSYSTEM="1",
)


def _git(repo, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          env=_GIT_ENV)
    if done.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8", "replace")


def _tree(repo, rev: str) -> dict:
    """path -> (mode, blob). The blob is what makes two trees comparable."""
    entries = {}
    for record in _git(repo, "ls-tree", "-r", "-z", rev).split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        mode, kind, blob = meta.split()
        entries[path] = (mode, blob)
    return entries


def is_ancestor(repo, ancestor: str, descendant: str) -> bool:
    """Commit ancestry, the one evidence that makes closing a PR lossless."""
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(repo), capture_output=True, env=_GIT_ENV)
    if done.returncode not in (0, 1):
        raise RuntimeError(
            f"git merge-base --is-ancestor failed: "
            f"{done.stderr.decode('utf-8', 'replace')}")
    return done.returncode == 0


@dataclass(frozen=True)
class Comparison:
    """What `compare()` measured. Only these four facts, nothing inferred."""

    in_main: bool
    base: str
    based_on_tip: bool
    absent: Tuple[str, ...]
    differing: Tuple[str, ...]
    touched: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {"in_main": self.in_main, "base": self.base,
                "based_on_tip": self.based_on_tip,
                "absent": list(self.absent), "differing": list(self.differing),
                "touched": list(self.touched)}


def compare(repo, *, main: str, head: str) -> Comparison:
    main_sha = _git(repo, "rev-parse", "--verify", f"{main}^{{commit}}").strip()
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    in_main = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head_sha, main_sha],
        cwd=str(repo), capture_output=True, env=_GIT_ENV).returncode == 0
    base = _git(repo, "merge-base", main_sha, head_sha).strip()
    theirs, ours, at_base = _tree(repo, main_sha), _tree(repo, head_sha), _tree(repo, base)
    absent = tuple(sorted(p for p in ours if p not in theirs))
    differing = tuple(sorted(p for p in ours
                             if p in theirs and ours[p] != theirs[p]))
    # `differing` says the trees disagree. It does not say who moved: a branch
    # that never touched a file still differs from a main that has since changed
    # it. Only a path the head itself changed, relative to the commit it forked
    # from, is the hand that wrote it.
    touched = tuple(sorted(p for p in differing
                           if p in at_base and ours[p] != at_base[p]))
    return Comparison(in_main=in_main, base=base, based_on_tip=base == main_sha,
                      absent=absent, differing=differing, touched=touched)


# --------------------------------------------------------------------------- #
# the retire map
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PrFacts:
    """One pull request, as measured. `classify()` reads only these fields.

    `based_on_tip` is carried for the report and is deliberately not read by
    `classify()`: a branch that only adds files cannot conflict with a file it
    never touched, so a moved base is not evidence against its delta. What the
    measurements showed is that the red job on those branches came from the
    merge ref they were run against — a property of CI, not of the PR.
    """

    number: int
    head: str
    in_main: bool
    released: bool
    absent: Tuple[str, ...] = ()
    differing: Tuple[str, ...] = ()
    own_prefixes: Tuple[str, ...] = ()
    based_on_tip: bool = False
    touched: Tuple[str, ...] = ()
    superseded_by: int = 0

    @classmethod
    def from_comparison(cls, comparison: Comparison, *, number: int,
                        own_prefixes: Sequence[str], released: bool = False,
                        superseded_by: int = 0) -> "PrFacts":
        return cls(number=number, head="", in_main=comparison.in_main,
                   released=released, absent=comparison.absent,
                   differing=comparison.differing,
                   own_prefixes=tuple(own_prefixes),
                   based_on_tip=comparison.based_on_tip,
                   touched=comparison.touched, superseded_by=superseded_by)

    def _is_own(self, path: str) -> bool:
        """A declared scope is a directory, an exact path, or a name stem.

        The pins file states what each PR claims to own, taken from its title
        and description; the tree states what it added. The check has bite
        because those two are written independently — a file smuggled in outside
        the claim is `BLOCKED_OWNER`, and a stem deliberately does not reach a
        neighbour with another stem.
        """
        return any(path == prefix.rstrip("/") or path.startswith(prefix)
                   for prefix in self.own_prefixes)

    def classify(self) -> str:
        if self.in_main:
            # In main is not the same as released; only `released` can say that.
            return VERIFIED_AND_MERGED if self.released else RELEASE_NOT_RUN
        if self.superseded_by:
            # A later lane's head contains this one: closing this PR loses no
            # commit, which is the only evidence that makes closing it safe.
            return SUPERSEDED_ANCESTOR
        if not self.absent and not self.differing:
            return SUPERSEDED_ANCESTOR
        if self.touched:
            # The head edited a file main already has. Whoever owns that file
            # has to see this as a change, not as a stale copy.
            return BLOCKED_OWNER
        if any(not self._is_own(path) for path in self.absent):
            return BLOCKED_OWNER
        return OWN_DELTA_PENDING

    def successor(self):
        return self.superseded_by or MAIN
