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
    # §38: six children of this root existed on disk while this tuple omitted
    # them, so the conformance check reported each as an exemption rather than a
    # declaration. Four are added below; the other two are named and rejected at
    # the end of this comment. Their exemption entries classified them
    # CONTRACT_GAP, whose
    # definition in conformance.py is "the vocabulary is behind reality and the
    # fix is a ruling that widens it, not a move". This is that ruling. Each is
    # named by a document outside this tuple, which is why it was never free to
    # move in the first place:
    #   boards                derived Board current view, regenerated wholesale
    #                         by the work-engine control plane's `board render`,
    #                         never authoritative (HWZ-MAP.md). Not this repo's
    #                         `serverosctl`: two programs carry that name and
    #                         PATH gives it to this one, which has no `board`
    #                         subcommand — see the `boards` entry in
    #                         `fleet/state-root-contract.yaml` for the
    #                         measurement
    #   federation            locator-only observed projections for external
    #                         domains (feishu, github) — the L3 observed SSOT in
    #                         HWZ-MAP / FEISHU-MAP / GITHUB-MAP
    #   work-items            runtime stage / liveness for one Work Item,
    #                         explicitly not stored in Git (state README,
    #                         DEV-OS-SPEC)
    #   resource-leases       current resource occupancy, read by
    #                         ops/workspace/serveros/resources.yaml; being empty
    #                         is a state, not an absence
    #
    # Two neighbours are deliberately NOT here, because widening the vocabulary
    # is a claim that a namespace belongs to this root's design, and these two
    # are unresolved rather than designed: `migrations/` is expired evidence
    # that two declarations disagree about (see conformance.EXCEPTIONS), and
    # `document-stewardship/` has a producer that only exists in an unmerged
    # worktree. Declaring them would end the question by fiat instead of
    # answering it.
    "boards", "federation", "resource-leases", "work-items",
    #   requirement-board     §S's Global Requirement Board, derived from the
    #                         per-project `boards` above plus two joins (Item
    #                         cards for source_refs/aliases, the §17 binding
    #                         store for the execution-session column). It is a
    #                         projection with no authority and no readers: the
    #                         only way to produce it is
    #                         `serverosctl requirement board --write`, and
    #                         nothing consults it. Declared here so that
    #                         `resolve` can name it and `admit` can refuse a
    #                         second board somebody writes elsewhere under a
    #                         name of their own choosing.
    "requirement-board",
    #   verification          what was verified against which bytes, and
    #                         whether those bytes still stand. An authority for
    #                         the same reason an approval is one: the inputs a
    #                         test read when it passed cannot be re-derived
    #                         later, so the record is written down rather than
    #                         recomputed. Produced by `scripts/verify.py`, and
    #                         empty means nobody has recorded a verification —
    #                         which is why `status` answers COULD_NOT_ANSWER
    #                         for a record that is not there instead of OK.
    "verification",
    #   recipes               §7's three finite loops, one record per recipe per
    #                         scope per period. A record of a past act like
    #                         `verification`: whether a period was advanced, and
    #                         how far, is not recoverable from the tree
    #                         afterwards. The PATH is the single-flight key, so
    #                         a run of a period that already has a record finds
    #                         it rather than repeating the work. Produced by
    #                         `runtime/recipes.py`; the two authored files in it
    #                         (`schedules.yaml`, `model-binding.yaml`) are
    #                         written by an operator, and a loop refuses rather
    #                         than inventing a window or a model transport when
    #                         they are absent.
    "recipes",
)
STATE_SERVEROS_RUNTIME = (
    "registry", "observed", "health", "hygiene", "resources",
    # §23 adds the three namespaces the runtime-attribution work writes:
    # attribution (who a process belongs to), graph (the runtime groups) and
    # anomalies (the classifications, including the combinations that did NOT
    # fire). Declared here so `resolve` can name them before a producer
    # exists — the namespace is the contract, the directory is the state.
    "attribution", "graph", "anomalies",
    # §5/§6 adds one: the trusted actor contexts, human approvals and standing
    # delegations this node minted and decided. Declared before the directory
    # exists, because the
    # namespace is the contract and the directory is the state — and declared as
    # a CHILD of runtime rather than a top-level sibling because the daemon is
    # its only producer and `state_dir` is the one root every daemon write is
    # resolved through. A second root would be a second thing to keep correct.
    "agent-control",
    # §21 adds the alert state machine's one document. Declared here for the
    # same reason `agent-control` was: the namespace is the contract and the
    # directory is the state, and a daemon that answered "no alerts" from a
    # path nothing had declared would be reporting an absence it could not
    # distinguish from a typo.
    #
    # It is a CHILD of runtime rather than a top-level sibling because the
    # daemon is its only producer and `state_dir` is the one root every daemon
    # write is resolved through.
    "alerts",
)

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

# A semantic role is a name for WHAT something is; a root is where it is. They
# are declared apart because the two come apart — and when they do, the role is
# the stable one and the path is the fact that moves (§25).
#
# `runtime-assets/serveros` above is the role's DECLARED path, and nothing on
# this node has ever been there. The distribution that actually exists is
# `runtime-assets/hwz-server-os`, it has live consumers (the plugin cache and
# `workengine/context.py` both name it), and it must not be relocated while
# they do. So the role resolves to where the artifact IS, and the day the
# physical move happens this table changes and no consumer does.
#
# The alternative — leaving `runtime-assets` pointing at a directory that does
# not exist — is the standing defect of this codebase: a declared path with no
# producer, which reports as "nothing here" and is indistinguishable from an
# empty directory.
SEMANTIC_ROLES = {
    # The ServerOS distribution root: what `build.py` publishes and what a
    # runtime installs from.
    "runtime.serveros-distribution": "{DATA_ROOT}/runtime-assets/hwz-server-os",
    # Browser distributions: chromium + ffmpeg for Playwright, and the
    # chrome-for-testing bundle. ~1.7 GB. Classified in §25 as a tool runtime
    # asset rather than a ServerOS distribution, and NOT relocated this round:
    # the producer/consumer contract for it is not yet declared, and moving a
    # 1.7 GB tree out from under an undeclared consumer set is how a test
    # environment stops working for reasons nobody connects to a move.
    "runtime.browser-distribution": "{DATA_ROOT}/runtime-assets/browsers",
}

# Root names that may legitimately resolve to ONE physical directory.
#
# `HOME` and `DATA_ROOT` are two names for one fact on a single-root host, and
# this node is one: both are `/data/home/vince`. That is several semantic roles
# answered by one place, which §25 permits — a role is what something IS, a root
# is where it is, and nothing says two roles need two directories. The machine
# policy at `~/CLAUDE.md` states both bindings as one block, so this is the
# contract's own reading of this host rather than a concession made to it.
#
# It is NOT the substitute-root shape §16A forbids. That shape is one NAME
# materialized under two DIFFERENT roots — `workspace/artifacts` beside
# `$DATA_ROOT/artifacts` — and it stays reportable and stays reported.
#
# Declared, rather than derived from path equality, on purpose. Two roots that
# merely happen to compare equal are not thereby a legitimate binding:
# `WORKSPACE_ROOT=$DATA_ROOT` would apply WORKSPACE_TOP_LEVEL to the home
# directory, and `state` resolving onto `home` would make the runtime's own
# state a home directory entry. A rule that accepted any coincidence could not
# report either of those. Only the groups below co-bind, and a coincidence
# outside them is a conflict.
CO_BINDING_ROOT_GROUPS = (
    ("home", "data"),
)


def co_binding_roles(name: str) -> frozenset:
    """Every root name declared able to share one directory with `name`."""
    for group in CO_BINDING_ROOT_GROUPS:
        if name in group:
            return frozenset(group) - {name}
    return frozenset()


def group_physical_roots(locations: dict, *, names) -> list:
    """Root names grouped by the physical directory each one resolves to.

    A group holding one role is an ordinary root. A group holding several is
    several claims about one directory, and this function does not rule on
    whether that is legitimate: it reports the group, with `co_binding` saying
    only that several names are here, and `co_binding_declared` saying whether
    their combination is one the contract permits. The ruling stays where a
    reader looks for policy (`CO_BINDING_ROOT_GROUPS`) instead of hiding in a
    grouping helper — and a merge nobody declared stays visible as
    `co_binding: true, co_binding_declared: false` rather than being either
    silently accepted or silently dropped.

    Identity is the RESOLVED path, not the string. A role reaching the same
    directory through a symlink is the same directory, and treating it as a
    second one is how one place gets scanned twice under two names — which is
    the defect this function exists to remove, not a new spelling of it.
    """
    groups: dict = {}
    for name in names:
        location = str(locations[name])
        group = groups.setdefault(os.path.realpath(location), {
            "physical_root": os.path.realpath(location),
            "roles": [],
            "paths": {},
        })
        group["roles"].append(name)
        group["paths"][name] = location

    out = []
    for group in groups.values():
        roles = tuple(group["roles"])
        # Every ORDERED PAIR of distinct roles must be declared, not every role
        # against a flattened list of the others — the flattened form asks
        # whether `home` may share with `home`, which is false and would make
        # every group undeclared.
        declared = all(other in co_binding_roles(role)
                       for role in roles for other in roles if other != role)
        group["roles"] = roles
        group["co_binding"] = len(roles) > 1
        group["co_binding_declared"] = declared
        group["undeclared_with"] = [] if declared else sorted(set(roles))
        out.append(group)
    return out


# Suffixes that must never be created under DATA_ROOT (§34, §42): a
# near-canonical directory is worse than a wrong one, because it survives.
#
# Bare `old` and `new` are absent deliberately: they are plausible names for
# a real run or a real segment, and a check that refuses legitimate work gets
# switched off. The entries here are the ones that are unambiguous.
FORBIDDEN_SUFFIXES = ("legacy", "legacy-artifacts", "old-artifacts",
                      "backup", "backups", "compat")

# `.serveros` in HOME stays small (§38): identity and binding only.
#
# The vocabulary is NOT invented here. `fleet/node-root-contract.yaml` is the
# authority for this root — it is the file that says what may live in
# `~/.serveros/` and what may never, `fleet/node.py` reads it (`NODE_FILE`,
# `TARGETS_FILE`), and `fleet/README.md` and `docs/fleet-controller-adapter.md`
# both name the same three files. This tuple is a transcription of that
# contract, and a transcription is exactly the thing that goes stale: it said
# `identity.yaml` and `profile.yaml`, neither of which exists anywhere on this
# node or in any contract, while omitting `node.yaml` — the file that is there,
# that `node.py` opens to answer "which device is this host", and whose absence
# is the defined NODE_UNBOUND state.
#
# The consequence was not cosmetic: `admit()` REFUSED a write to
# `~/.serveros/node.yaml`, so the contract's own answer to a canonical path was
# wrong, and no test noticed because the one test covering this branch
# (`test_the_serveros_allowlist_admits_identity_files`) asserts `targets.yaml`,
# the single name both versions of the list happened to agree on. That is the
# same coincidence that hid the `A1` defect in `architecture_check.py`: a test
# that picks the intersection of two disagreeing vocabularies passes for as
# long as the disagreement is not the thing being tested.
#
# `README.md` is not here and does not belong here. This tuple is the write
# vocabulary AND the shape of the root: §41 allows bindings only, and gives
# documentation the disposition "repo/docs or artifacts", so a document beside
# these files is a second statement of what `node-root-contract.yaml` already
# states — inside the one root whose design principle is that a thing has one
# answer.
#
# §38 first read the standing `UNDECLARED_ENTRY / MEDIUM / OPEN` finding as a
# false alarm and classified the file as an entry document instead. That was
# wrong, and §41 is why: the finding was telling the truth. The file has since
# been retired (bytes preserved in
# artifacts/work-items/serveros/SRV-0007/RUN-0001/), so the report is quiet for
# the right reason rather than by exemption. `admit()` refuses the name in
# either case — it reads this tuple directly, never the exception table — so the
# write side needed no change to make the read side honest.
HOME_DOT_SERVEROS_ALLOWED = ("node.yaml", "targets.yaml", "controller.yaml")
# Names that are known landing spots for a root drifting into a second state
# root, and the reason this tuple is read rather than decorative: it existed
# from an earlier round with no consumer anywhere, so `admit()` refused these
# names without ever saying why. It is consulted below to name the specific
# rule, which is what the refusal message was already trying to do in prose.
HOME_DOT_SERVEROS_FORBIDDEN = ("reports", "observations", "registry", "proposals", "artifacts")

# The tuples above, addressed by the name of the area they govern, so that a
# caller reporting against them does not have to build its own mapping. `home`
# and `runtime` are not top-level areas of a root — they are the closed
# vocabularies of `~/.serveros/` and of `state/serveros/runtime/` — and they are
# here because both are closed sets that get checked the same way. This map sits
# below all six on purpose: a map that names them has to be defined after them.
VOCABULARIES = {
    "data": DATA_ROOT_TOP_LEVEL,
    "workspace": WORKSPACE_TOP_LEVEL,
    "state": STATE_SERVEROS_TOP_LEVEL,
    "runtime": STATE_SERVEROS_RUNTIME,
    "artifacts": ARTIFACT_NAMESPACES,
    "home": HOME_DOT_SERVEROS_ALLOWED,
}


class PathError(Exception):
    """A path could not be resolved or admitted. Never a fallback."""


class PathRefused(PathError):
    """A path is outside the contract. The caller must not fall back to cwd."""


# Where a root's value came from. A configured path and a path that happened to
# be the fallback are the SAME STRING and are NOT the same answer, and nothing
# in the string says which one it is.
#
# The last resort used to be a compiled-in literal — the FIRST NODE's paths, so
# on every other node a process the environment had not configured resolved to
# a directory that does not exist, or, worse because it is silent, to one that
# does. There is no last resort now. A node nobody identified has roots that
# are UNBOUND, and a caller about to say where a file belongs reads
# `Paths.bound` before it says anything.
INJECTED = "INJECTED"        # the caller supplied it
ENVIRONMENT = "ENVIRONMENT"  # the environment supplied it
PROFILE = "PROFILE"          # the device's authored Profile binding supplied it
DERIVED = "DERIVED"          # computed from a seed that was supplied
UNBOUND = "UNBOUND"          # nothing supplied it, and nothing is invented

# Which seed each root is built from, so provenance propagates along the same
# edge the value does. Derived from the template rather than declared beside
# it, because a second table would be a second thing to keep in step.
_SEED_ENV = {"home": "HOME", "data_root": "DATA_ROOT",
             "workspace_root": "WORKSPACE_ROOT",
             "artifacts_root": "ARTIFACTS_ROOT",
             "toolchains_root": "TOOLCHAINS_ROOT"}

# Which path ROLE in the device's Profile supplies each seed. `data_root` is
# the one that did not exist until this round: `fleet/profiles.yaml` could say
# where artifacts, state and toolchains live and not where the root they are
# built from lives, and a resolver with no way to read the seed reads a literal
# instead. The role exists now and the literal is gone.
#
# `home` is a role and NOT a derivation. On the node this contract came from,
# `HOME` is `/home/hwz` and `DATA_ROOT` is `/opt/applications/data/home-hwz` —
# different directories with different lifetimes — so deriving either from the
# other is the mistake §0.A names.
#
# The two roles stay separate even on a node where they resolve to the SAME
# directory, which is the node this contract is being read on now: both are
# `/data/home/vince` there. The distinction is not decorative. A contract that
# collapsed `home` into `data_root` because they coincide here would derive one
# from the other on the node above, where they do not — and it would have no
# way to name the case where a node's HOME is not its persistent root at all.
# Keeping both roles and recording that they MAY co-bind
# (`CO_BINDING_ROOT_GROUPS`) states the coincidence as a fact about a node
# instead of encoding it as a rule about the contract.
_SEED_ROLE = {"data_root": "data_root",
              "home": "home",
              "artifacts_root": "artifacts",
              "toolchains_root": "toolchains"}

# Seeds that a Profile MAY declare a role for, and that are DERIVED from
# `data_root` when it does not. `workspace` is not a declared role —
# `workspace.src` and `workspace.worktrees` are, and both profiles bind those
# two under `{DATA_ROOT}/workspace`.
#
# `artifacts` and `toolchains` ARE declared roles, so the Profile answers them
# first and this table answers only behind it. Both are relative derivations
# from a seed this node bound itself: `{data_root}/artifacts` is a statement
# about layout, not another device's absolute path, which is the difference
# between this table and the literal §0.A removed. Dropping them here left a
# node with no `artifacts` role in its Profile with nothing to resolve, and
# left a caller who injected a world inheriting this machine's instead.
_SEED_DEFAULT_SUFFIX = {"workspace_root": "workspace",
                        "artifacts_root": "artifacts",
                        "toolchains_root": "toolchains"}


class _LocalFleet:
    """Marker for "the fleet authority this installation ships with".

    A sentinel rather than `None`, because two different things are being
    asked for: `LOCAL_FLEET` means read the authority beside this file, and
    `None` means there is no authority to read — which is what an unattended
    node and a fail-closed test both have. Collapsing them would make the
    fail-closed case unreachable by construction.
    """
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "LOCAL_FLEET"


LOCAL_FLEET = _LocalFleet()

# `runtime/paths.py` and `fleet/` are siblings in the source tree and in an
# installed distribution alike, so this one hop is the same address in both.
_AUTHORITY_DIR = Path(__file__).resolve().parent.parent / "fleet"
NODE_FILE = "node.yaml"

# Parsed per (authority, mtime), so a long-lived process reads three small
# files once and a test that rewrites a fixture is not served a stale answer.
_AUTHORITY_CACHE: dict[tuple, dict] = {}


def _read_yaml(path: Path):
    """Parsed YAML, or None. A missing authority is not an error.

    PyYAML is imported here rather than at module scope so that this module
    stays importable — and therefore fail-closed rather than fail-to-load — in
    a process that cannot read the authority at all.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover - environment guarantee
        return None
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _node_file_for(fleet_dir: Path) -> Path:
    """Where this installation says the node root is.

    Read from `fleet/node-root-contract.yaml` rather than written down here, so
    that the control root has one address in the codebase. `fleet/node.py`
    resolves the same field the same way.
    """
    contract = _read_yaml(fleet_dir / "node-root-contract.yaml") or {}
    default = (contract.get("root") or {}).get("default") or "~/.serveros"
    return Path(os.path.expanduser(str(default))) / NODE_FILE


def _profile_roles(fleet_dir, node_file) -> dict:
    """role -> bound path, for the device this host claims to be.

    The chain is the authored one, and it is followed rather than
    reimplemented: `~/.serveros/node.yaml` says which device this host is,
    `fleet/registry.yaml` says which profile that device runs, and
    `fleet/profiles.yaml` says where the roles resolve. The node file carries
    the claim and nothing else, so a machine cannot hold its own copy of its
    identity.

    `fleet/transport.py` reads the same three files and is the surface a caller
    asking "where does role X live on device Y" goes through. This answers the
    narrower question a root resolver has — what is THIS host — and
    `tests/test_path_authority.py` pins the two to the same answer, so they
    cannot drift apart quietly.

    Empty is the answer in every case that is not a positive binding: no
    authority, no node file, no device claim, a device that is not enrolled, a
    profile that does not declare the role, a role that is UNBOUND or
    NOT_APPLICABLE. All of them mean the same thing to a caller — there is no
    authored answer — and none of them is a reason to invent one.
    """
    if fleet_dir is None or node_file is None:
        return {}
    fleet_dir = _AUTHORITY_DIR if fleet_dir is LOCAL_FLEET else Path(fleet_dir)
    node_file = (_node_file_for(fleet_dir) if node_file is LOCAL_FLEET
                 else Path(node_file))

    try:
        stamp = (str(fleet_dir), str(node_file),
                 (fleet_dir / "registry.yaml").stat().st_mtime_ns,
                 (fleet_dir / "profiles.yaml").stat().st_mtime_ns,
                 node_file.stat().st_mtime_ns)
    except OSError:
        return {}
    if stamp in _AUTHORITY_CACHE:
        return _AUTHORITY_CACHE[stamp]

    device_ref = (_read_yaml(node_file) or {}).get("device_ref")
    registry = _read_yaml(fleet_dir / "registry.yaml") or {}
    profiles = _read_yaml(fleet_dir / "profiles.yaml") or {}

    profile_name = None
    for device in registry.get("devices") or []:
        if isinstance(device, dict) and device.get("device_ref") == device_ref:
            profile_name = device.get("environment_profile")
            break
    profile = (profiles.get("profiles") or {}).get(profile_name) or {}

    roles = {}
    for role, entry in (profile.get("paths") or {}).items():
        if not isinstance(entry, dict):
            continue
        # BOUND is the only state that carries a path. UNBOUND is a declared
        # gap and NOT_APPLICABLE says the role has no meaning here; neither is
        # a path, and a resolver that answered with one anyway would be
        # answering a question the profile declined.
        if entry.get("state") == "BOUND" and entry.get("path"):
            roles[role] = str(entry["path"])

    _AUTHORITY_CACHE[stamp] = roles
    return roles


def _unbound(placeholder: str) -> str:
    """The value an unbound seed contributes, and it is its own template.

    Not `""`, which formats into `/workspace` and reads as an absolute path,
    and not a plausible default, which reads as a correct answer to a caller
    that never asked whether the node was identified. `{DATA_ROOT}/state` can
    be mistaken for neither, so the mistake this module exists to prevent is
    not available to make.
    """
    return "{" + placeholder + "}"


def expand_with_provenance(roots: dict | None = None, *,
                           fleet_dir=LOCAL_FLEET,
                           node_file=LOCAL_FLEET) -> tuple[dict, dict]:
    """(resolved roots by name, provenance by name) — roots and roles alike.

    Provenance is decided per SEED first and only then propagated to the roots
    built from it, because `workspace` is derived: it is an unbound node's only
    when `data_root` was, and deciding the derivation on its own would call a
    root unbound when its input was bound. A false alarm of that kind is how a
    true one gets ignored.

    Precedence, per seed, is the order §2.1 asks for:

        INJECTED     the caller named it
        ENVIRONMENT  the process environment named it
        PROFILE      the device's authored Profile binding says where it is
        UNBOUND      nobody did, and nothing is invented

    Seeds are resolved in dependency order — `data_root` before the roots
    derived from it, so the derivation reads a settled value.
    """
    # An injected mapping is the caller STATING the world, so it is closed:
    # neither this machine's Profile nor this process's environment is allowed
    # to fill a seed it omits. `Paths` documents exactly this of itself --
    # "Injectable so a test can exercise resolution against a tmp_path without
    # the machine's real roots, and so a wrong root can never be reached by
    # falling back to whatever the environment happened to say" -- and a
    # partly-injected world that silently inherits the rest is the same defect
    # as a compiled-in literal: it returns a plausible path nobody asked for.
    # What is left over is UNBOUND, which `Paths.bound` already reports.
    injected_world = roots is not None
    roots = roots or {}
    profile_roles = {} if injected_world else _profile_roles(fleet_dir, node_file)

    def configured(seed: str) -> tuple[str | None, str | None]:
        """(value, provenance) when the caller or the environment named it."""
        injected = roots.get(seed)
        if injected:
            return str(injected), INJECTED
        if injected_world:
            # Closed. The environment of the process running a fixture is not
            # part of the fixture.
            return None, None
        from_env = os.environ.get(_SEED_ENV[seed])
        if from_env:
            return from_env, ENVIRONMENT
        return None, None

    supplied: dict[str, str] = {}
    seed_provenance: dict[str, str] = {}

    # EVERY seed, in dependency order: `data_root` first because the derived
    # roots read it, then the rest as listed. ONE loop, because the rule is one
    # rule -- caller, environment, authored binding, and only then the structure
    # implied by a root that was bound.
    #
    # There were two walks over `_SEED_DEFAULT_SUFFIX`, and they disagreed. The
    # second ran after this one and assigned `{data_root}/{suffix}` without
    # asking whether the Profile had already answered, so `artifacts` and
    # `toolchains` -- both declared roles -- were COMPUTED rather than READ. On
    # this node the two coincide and only the provenance was wrong, which is
    # what made it survive: `probe_derivation_overwrite.py` binds both roles
    # somewhere else and the value is wrong too.
    #
    # `workspace_root` is in this list now. It is the seed with no role of its
    # own, and it lived in the second loop only because that loop used to hold
    # the whole table.
    for seed in ("data_root", "home", "artifacts_root", "toolchains_root",
                 "workspace_root"):
        value, source = configured(seed)
        if value:
            supplied[seed], seed_provenance[seed] = value, source
            continue
        role = _SEED_ROLE.get(seed)
        bound = profile_roles.get(role) if role else None
        if bound:
            supplied[seed], seed_provenance[seed] = bound, PROFILE
            continue
        # Behind the Profile, and only where a Profile declined to answer: the
        # structure implied by a data root that WAS bound. `data_root` is
        # resolved first in this loop, so the value it reads is settled, and a
        # derivation from an unbound root stays unbound rather than becoming a
        # longer string.
        suffix = _SEED_DEFAULT_SUFFIX.get(seed)
        if suffix and seed_provenance.get("data_root") != UNBOUND:
            supplied[seed] = f"{supplied['data_root']}/{suffix}"
            seed_provenance[seed] = DERIVED
            continue
        supplied[seed] = _unbound(_SEED_ENV[seed])
        seed_provenance[seed] = UNBOUND

    values = {
        "HOME": supplied["home"],
        "DATA_ROOT": supplied["data_root"],
        "WORKSPACE_ROOT": supplied["workspace_root"],
        "ARTIFACTS_ROOT": supplied["artifacts_root"],
        "TOOLCHAINS_ROOT": supplied["toolchains_root"],
    }
    # Roles are addressable through `resolve` too, so that a caller holding a
    # role name can use one code path. They are kept in their own table
    # because a role and a root are different claims: a root says where
    # something lives, a role says what it is, and a check that had to read
    # them out of one dictionary could not report a role whose path has moved.
    resolved = {name: template.format(**values)
                for name, template in {**ROOTS, **SEMANTIC_ROLES}.items()}

    # Every name inherits the WORST provenance among the seeds its template
    # names. Worst rather than best: a root built partly from an unconfigured
    # seed is not a configured root, and resolving to a plausible path does not
    # make the seed configured.
    order = {INJECTED: 0, ENVIRONMENT: 1, PROFILE: 1, DERIVED: 1,
             UNBOUND: 2}
    provenance: dict[str, str] = {}
    for name, template in {**ROOTS, **SEMANTIC_ROLES}.items():
        used = [seed for seed in _SEED_ENV
                if "{" + _SEED_ENV[seed] + "}" in template] or ["data_root"]
        provenance[name] = max((seed_provenance[s] for s in used),
                               key=lambda p: order[p])
    return resolved, provenance


def _expand(roots: dict | None = None, *, fleet_dir=LOCAL_FLEET,
            node_file=LOCAL_FLEET) -> dict:
    """Unchanged in what it returns. Kept as the narrow signature.

    Most callers want a path and would do nothing useful with the provenance. A
    caller that has to distinguish a bound node from an unbound one reads
    `Paths.bound` or `Paths.provenance`.
    """
    return expand_with_provenance(roots, fleet_dir=fleet_dir,
                                  node_file=node_file)[0]


class Paths:
    """The root contract for one node, resolved once.

    Injectable so a test can exercise resolution against a `tmp_path` without
    the machine's real roots, and so a wrong root can never be reached by
    falling back to whatever the environment happened to say.
    """

    def __init__(self, roots: dict | None = None, *,
                 fleet_dir=LOCAL_FLEET,
                 node_file=LOCAL_FLEET) -> None:
        self.roots, self.provenance = expand_with_provenance(
            roots, fleet_dir=fleet_dir, node_file=node_file)
        # Names no authority answered for. Empty on a node whose roots were
        # actually resolved, which is the state every placement answer depends
        # on. These values are their own templates — `{DATA_ROOT}/state` — and
        # are not paths a caller should ever use.
        self.unconfigured = tuple(sorted(
            name for name, source in self.provenance.items()
            if source == UNBOUND))

    @property
    def bound(self) -> bool:
        """Whether these roots describe a machine that was IDENTIFIED.

        False means at least one root is UNBOUND — nobody, and no authority,
        supplied it. A caller about to say where a file belongs has to check
        this: resolving to a path is not the same as resolving to a path ON
        THIS MACHINE, and the two are indistinguishable from the string alone
        when the string is plausible. They are no longer indistinguishable
        when it is not: an unbound root reads as `{DATA_ROOT}/...`.
        """
        return not self.unconfigured

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

    def role(self, name: str) -> dict:
        """A semantic role resolves to wherever its artifact currently is.

        Same shape as `resolve`, plus `role` and `is_role`, so a caller can
        always tell which of the two it used. A role that resolved to a bare
        path would be a role nobody could audit.
        """
        if name not in SEMANTIC_ROLES:
            raise PathRefused(
                f"{name!r} is not a declared semantic role. Known: "
                f"{list(SEMANTIC_ROLES)}. A role is what something IS; a root "
                f"is where it is, and asking for a role this node does not "
                f"declare must not fall back to a path."
            )
        resolved = self.resolve(name)
        return {
            **resolved,
            "role": name,
            "is_role": True,
            "note": (
                "a semantic role resolves to the CURRENT physical location of "
                "the artifact. The role is the stable name; the path is the "
                "fact that moves, and a consumer that hardcodes the path has "
                "chosen the unstable half."
            ),
        }

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

    def memory_ledger(self, project: str) -> dict:
        """`records/projects/<project>/memory/` — the Layer B ledger.

        A work Item's memory is filed under the project it is about, which is
        the shape `subject` already has ("project/repo/Item"), so the ledger is
        a child of a tree that exists rather than a new root beside it. The
        records tree is the `hwz-records` Git repository, and that is the whole
        reason it is the substrate: the ledger inherits `blame`, `diff`,
        `revert` and a history nobody has to be trusted to maintain, and
        cross-device sync is a `git pull` with no ServerOS-specific merge logic.

        Deliberately not a new root under DATA_ROOT. `records/` is already
        declared, already versioned and already synced; a parallel store would
        be a second answer to "where is this work's memory".
        """
        if not project or "/" in project or project in (".", ".."):
            raise PathRefused(f"project must be a single path segment, not {project!r}")
        return self.resolve(f"workspace/records/projects/{project}/memory")

    def memory_projection(self) -> dict:
        """`state/serveros/projections/memory/` — the Layer A projection.

        Under the one projections namespace, not beside it: see
        `fleet/state-root-contract.yaml#projections`, which is the authority
        here and forbids a second one. This replaces the path the memory
        contract originally declared (`state/serveros/memory/`), which was
        written without checking whether that root had an owner.
        """
        return self.resolve("state/projections/memory")

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

        # The `~/.serveros` allow-list is enforced in `_check_top_level`, which
        # is the only place it is checked. It used to be checked here as well,
        # against `Path.home()` rather than against `self.roots["home"]` — the
        # same rule, two copies, resolving the control root two different ways.
        # They agreed on the day they were written and would have had to be
        # changed together forever after; the copy here was also unreachable
        # for any injected root, so a test could not have caught the divergence.
        # One rule, one root, one place.

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
            if parts[0] == ".serveros":
                leaf = parts[1] if len(parts) > 1 else ""
                if leaf and leaf not in HOME_DOT_SERVEROS_ALLOWED:
                    if leaf in HOME_DOT_SERVEROS_FORBIDDEN:
                        raise PathRefused(
                            f"{path!r}: {leaf!r} is a known second-state-root "
                            f"landing spot and is refused by name (§41), not "
                            f"merely as a name outside the allow-list. Machine "
                            f"state belongs under DATA_ROOT/state/serveros; a "
                            f"durable document belongs in the repository, not "
                            f"beside a host binding."
                        )
                    raise PathRefused(
                        f"{path!r}: `~/.serveros` holds identity and binding "
                        f"only (§41). Allowed: {list(HOME_DOT_SERVEROS_ALLOWED)}. "
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
                # Being under the state root is not the same as being
                # authoritative. Authority is a namespace-level property (§22),
                # and the projections namespace is the one place inside this
                # root where a path is admitted and explicitly NOT an authority.
                # A classifier that returned CANONICAL_STATE for both would make
                # the two indistinguishable at exactly the seam where the
                # distinction is load-bearing.
                if parts[0] == "projections":
                    return "PROJECTION_NON_AUTHORITATIVE"
                return "CANONICAL_STATE"
            return "ADMITTED_NON_CANONICAL"
        if root_name == "workspace":
            if parts and parts[0] in WORKSPACE_TOP_LEVEL:
                return "CANONICAL_WORKSPACE"
            return "ADMITTED_NON_CANONICAL"
        return "ADMITTED"

    # -- the whole contract, for inspection --------------------------------

    def vocabulary(self, area: str) -> tuple[str, ...]:
        """The closed vocabulary for one area, by the name the area is known by.

        `admit()` and `_classify()` reach for these tuples directly, which is
        fine inside this module. A caller that also has to name them — the
        conformance check, which reports against the same lists — would
        otherwise re-derive the mapping, and a second mapping is a second thing
        that can disagree with the first. That is the failure this module exists
        to prevent, so it is not repeated at the seam where it would be most
        expensive.
        """
        try:
            return VOCABULARIES[area]
        except KeyError:
            raise PathRefused(
                f"{area!r} is not an area with a declared vocabulary. Known: "
                f"{sorted(VOCABULARIES)}."
            ) from None

    def contract(self) -> dict:
        return {
            "roots": {n: str(self.root(n)) for n in self.names},
            "data_root_top_level": list(DATA_ROOT_TOP_LEVEL),
            "workspace_top_level": list(WORKSPACE_TOP_LEVEL),
            "state_serveros_top_level": list(STATE_SERVEROS_TOP_LEVEL),
            "state_serveros_runtime": list(STATE_SERVEROS_RUNTIME),
            "artifact_namespaces": list(ARTIFACT_NAMESPACES),
            "forbidden_suffixes": list(FORBIDDEN_SUFFIXES),
            "provenance": dict(self.provenance),
            "bound": self.bound,
            "unconfigured": list(self.unconfigured),
            "note": (
                "Resolution is a lookup, not a join. There is no default root "
                "and no cwd fallback; an unresolvable reference is refused."
            ),
            "provenance_note": (
                "`bound` is false when a root fell back to the first node's "
                "literal, which is another machine's path. The resolved string "
                "looks identical either way, so a caller that needs to know "
                "whether this node was configured reads `provenance`, not the "
                "path."
            ),
        }
