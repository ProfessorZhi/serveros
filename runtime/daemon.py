#!/usr/bin/env python3
"""serverosd — the semantic controller.

What it is
----------
A small persistent user-space loop that loads the registry, observes, probes,
persists what it saw, detects drift, and reconciles the resources policy
allows it to. It serves those same answers over a Unix socket so that an
agent-facing adapter (§23) can be thin.

What it is NOT
--------------
**Not a supervisor.** systemd, Docker and the provider own process lifecycle;
serverosd owns the *meaning* of what they report. That is why the registry
has `desired` and not `restart_policy`, and why `reconcile` below shells out
to `systemctl --user` rather than forking anything. A process supervisor that
cannot renew a session will restart a broken endpoint forever and call it
recovery — this host did exactly that, five times in two hours.

**Not an OS.** §18: no git workflow, no Feishu, no knowledge ingestion, no
ability authoring, no test implementation, no browser automation. Those are
other components' authority and routing them through the control plane would
make every one of them a dependency of the daemon staying up.

**Not the source of truth for anything it serves.** The registry is authored
state on disk; capabilities belong to ability-management. serverosd reads and
reports. `ability.*` requests are refused *by name*, with the owner named —
because the failure mode of a control plane is not lying, it is answering.

The reconcile gate
------------------
This is the only component in the tree with a code path that changes
something, so it is worth being precise about the gate. An action runs only
when ALL of these hold:

    the resource is registered, owned, user-scope, unprotected, desired
      running, and its declared lifecycle is bounded   (`is_automatable`)
    the health model returned RECONCILE, not NEEDS_HUMAN or DEFER
    the daemon was started with --enable-reconcile
    the retry budget for that resource has not been exhausted

In this round the default is observe-only, and the fourth condition is the
one §19 asks for: a budget with backoff, persisted so that restarting the
daemon does not restart the count. An endpoint that has failed N times in a
window stays failed and reports NEEDS_HUMAN, rather than being restarted
until somebody notices. "Restart it again" is not a recovery strategy; it is
how the original incident stayed invisible for 54 minutes.
"""
from __future__ import annotations

import contextvars
import errno
import fcntl
import importlib.util
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Whose connection the request being handled arrived on.
#
# `handle` gives a handler its params and nothing else, and `agent.context.attach`
# needs the peer of *its own* caller — the pid on the other end of this
# connection is the whole reason a trusted context binds to a process rather
# than to a claim. This used to live on the instance, under a comment saying it
# was safe, and only safe, because the daemon serves one connection at a time
# on one thread. That sentence was true, and being true was the problem: an
# identity check rested on a scheduling property that nothing declared and any
# later edit — a thread pool for a slow probe, a handler that re-enters
# `handle` — could quietly withdraw. Carried per call instead, so the
# correctness does not depend on how the loop happens to be scheduled.
_CALLER_PEER: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "serveros_caller_peer", default=None)


def _pointer_state(root: Path) -> dict:
    """`current` against a running process, measured NOW.

    The identity above is frozen because "which bytes am I" does not change
    while a process runs. This is the other half, and it must NOT be frozen:
    `current` is a pointer a promote moves, and the whole failure this exposes
    is that a promote moves it while nothing restarts the daemon. An answer
    cached at startup would agree with itself forever and report a
    promote-without-restart as health.

    Read only where the layout says a pointer exists. A daemon started from a
    source checkout lives at `<repo>/runtime`, has no `revisions/` above it and
    no `current` to compare against — inventing one there would be the same
    false answer in a different costume.
    """
    parent = root.parent
    if parent.name != "revisions":
        return {
            "state": "NOT_APPLICABLE", "current": None, "agreement": None,
            "detail": (f"{root} is not a revision of an installed composition, so "
                       "there is no `current` pointer to compare this process "
                       "against"),
        }
    link = parent.parent / "current"
    try:
        target = link.resolve(strict=True)
    except OSError as exc:
        return {
            "state": "NO_POINTER", "current": None, "agreement": None,
            "detail": f"{link} is not a readable symlink ({exc.__class__.__name__})",
        }
    agrees = target == root
    return {
        "state": "AGREEMENT" if agrees else "DRIFT",
        "current": str(target),
        "agreement": agrees,
        "detail": (
            f"this process is running {root.name} and `current` names it"
            if agrees else
            f"this process is running {root.name}, but `current` points at "
            f"{target.name} — a promote moved the pointer and this process was "
            "not restarted, so it is executing the revision `current` used to "
            "name. `release.py promote` reloads units but does NOT restart"),
    }


def _read_identity_file(path: Path) -> dict:
    """One installer record, or an honest absence.

    Absence is a RESULT here, not an error: a daemon started straight out of a
    source checkout has no ARTIFACT.json beside it, and the useful answer to
    "which artifact are you" is then `nothing, and here is why` rather than a
    crash or a default that reads like a version.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"present": False, "why": "absent"}
    except OSError as exc:
        return {"present": False, "why": f"unreadable ({exc.__class__.__name__})"}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        return {"present": False, "why": f"not JSON ({exc})"}
    if not isinstance(value, dict):
        return {"present": False, "why": f"not an object ({type(value).__name__})"}
    return {"present": True, "value": value}


def _revision_root(here: Path | None = None) -> Path:
    """The revision directory this process is running out of.

    `here` is the directory the runtime modules live in (`<rev>/runtime`), and
    `_HERE` is exactly that. A caller that passes the FILE instead would get a
    root one level too deep — a silent, confident wrong answer of the kind the
    callers of this share a reason to remove — so both spellings are accepted.

    `.resolve()` is not tidiness: the root must be where the bytes ARE, and a
    path left pointing through `current` would make every read of it agree with
    the pointer by construction — the exact self-confirming answer that lets a
    promote-without-restart go unnoticed. Non-existent paths resolve to
    themselves, so this costs nothing in a test that builds a fake revision.
    """
    base = here or _HERE
    return (base.parent if base.name == "daemon.py" else base).parent.resolve()


def _installed_identity(here: Path | None = None) -> dict:
    """Who this process is, in the terms the installer wrote down.

    Read ONCE, from the revision this process was launched out of, and never
    re-read. That is the whole point: `current` is a pointer that moves, and a
    daemon that re-resolved it would answer "where the release is now" while
    being asked "which bytes am I running". Those are different questions, and
    the gap between them is exactly the drift this exists to expose — a promote
    swaps the symlink and nothing restarts, so the running process is the only
    witness to the older answer.

    The path is taken from `__file__`, already resolved, so it is the directory
    the interpreter actually loaded this module from — not a lookup of the
    `current` symlink, which would reintroduce the confusion above.

    The daemon does not run git, and must not: it holds no checkout, the build
    recorded the revision at assembly time, and a daemon that went looking for
    a repository would report whichever one happened to be nearest instead of
    the one it was built from.
    """
    root = _revision_root(here)
    artifact = _read_identity_file(root / "ARTIFACT.json")
    release = _read_identity_file(root / "RELEASE.json")

    def _get(doc: dict, *keys, default=None):
        node = doc
        for key in keys:
            if not isinstance(node, dict):
                return default
            node = node.get(key)
            if node is None:
                return default
        return node

    art = artifact.get("value") or {}
    rel = release.get("value") or {}

    # Flattened, because these are the questions actually asked of a running
    # node, and each one carries what it rests on. `None` here is never "same
    # as before"; it is "this build could not establish it".
    fields = {
        "release_id": _get(rel, "revision"),
        "tree_hash": _get(rel, "tree_hash"),
        "previous_release_id": _get(rel, "previous"),
        "composition": _get(rel, "composition") or _get(art, "composition"),
        "artifact_version": _get(art, "version"),
        "artifact_cachebuster": _get(art, "cachebuster"),
        "source_commit": _get(art, "source", "serveros", "commit"),
        "source_ref": _get(art, "source", "serveros", "ref"),
        "source_tree_clean": _get(art, "source", "serveros", "tree_clean"),
        "source_worktree_changes": _get(art, "source", "serveros", "worktree_changes"),
        "ability_management_revision": _get(
            art, "source", "ability_management", "revision"),
    }

    unestablished = []
    if not artifact["present"]:
        unestablished.append(
            f"artifact identity: ARTIFACT.json {artifact['why']} at {root}")
    elif fields["source_commit"] is None:
        unestablished.append(
            f"source commit: this artifact records none "
            f"({_get(art, 'source', 'serveros', 'basis') or 'no basis given'})")
    if not release["present"]:
        unestablished.append(
            f"release identity: RELEASE.json {release['why']} at {root}")
    if fields["artifact_version"] is None and artifact["present"]:
        unestablished.append("artifact version: ARTIFACT.json carries no `version`")

    # The two records are written by different steps and nothing forces them to
    # agree. Where they can be compared, they are: `composition` is the field
    # both carry, and a disagreement means these two files describe two
    # different artifacts. That is not a small inconsistency to average out —
    # it means neither record can be used to answer "which release is this",
    # so the answer is CONFLICT and not whichever one was read last.
    conflicts = []
    if artifact["present"] and release["present"]:
        a_comp, r_comp = _get(art, "composition"), _get(rel, "composition")
        if a_comp and r_comp and a_comp != r_comp:
            conflicts.append(
                f"ARTIFACT.json declares composition {a_comp!r} and RELEASE.json "
                f"declares {r_comp!r} — the two records in one revision describe "
                "two different artifacts, so neither can be trusted to say which "
                "release is installed")
    agreement = ("CONFLICT" if conflicts else
                 "AGREEMENT" if (artifact["present"] and release["present"]) else
                 "UNKNOWN")

    return {
        "root": str(root),
        "artifact": artifact,
        "release": release,
        "fields": fields,
        "agreement": agreement,
        "conflicts": conflicts,
        "unestablished": unestablished,
        "note": (
            "Read once, from the revision this process was launched out of — "
            "`root` is where this module was loaded from, NOT where the "
            "`current` pointer aims now. Comparing the two is how a promote "
            "that nobody restarted becomes visible. `fields.source_tree_clean` "
            "is the only thing connecting a commit to these bytes: false or "
            "null means the commit names the checkout, not the content."
        ),
    }


def declared_on_demand(here: Path | None = None) -> dict:
    """What this revision declares it does NOT carry, but that this node can resolve.

    This is the third source on the discovery surface, and it exists because the
    first two answer different questions and neither substitutes for the other:

      * the local capability projection (`_capability_view`) answers **what is
        installed on this device**, and it is built from installations the
        authority recorded. An ability this composition recommends but does not
        ship is not in it, and its absence there is not evidence of absence;
      * this function answers **what the artifact this process runs out of has
        already declared resolvable**, read from `ARTIFACT.json` — the build's
        own statement, written at assembly time, which is the one such statement
        a node without a checkout can read. The daemon must not go looking for
        an ability-management checkout to ask; §"the daemon holds no checkout"
        is not a style rule.

    The failure this removes, observed live: the linux-work composition ships 4
    members and recommends 6 more, and the installed `serveros` skill routes
    "that happened, record it" to `information-management`. A fresh Agent asked
    `ability.search information-management`, got `matched: 0` against a
    projection that had never heard of it, and had nothing anywhere telling it
    that the ability is one `resolve` away. `0` from one source is not `0` from
    the world.

    `entries: None` when there is no ARTIFACT.json, and never `[]`. `[]` would
    say "this artifact recommends nothing", which is a claim about a build
    nobody read — the same distinction `_capability_view` draws between
    `abilities: null` and `abilities: []`.
    """
    root = _revision_root(here)
    path = root / "ARTIFACT.json"
    record = _read_identity_file(path)
    if not record["present"]:
        return {
            "root": str(root),
            "source": str(path),
            "present": False,
            "why": record["why"],
            "composition": None,
            "artifact": None,
            "entries": None,
            "note": (
                f"no ARTIFACT.json {record['why']} at {root}, so this process "
                "cannot say what its own composition declared resolvable. "
                "`entries: null` rather than an empty list: an unreadable "
                "declaration is not a declaration that nothing is recommended."
            ),
        }
    value = record["value"]
    raw = value.get("recommended")
    entries = None
    if isinstance(raw, list):
        entries = [
            {"name": entry.get("name"), "status": entry.get("status")}
            for entry in raw
            if isinstance(entry, dict) and entry.get("name")
        ]
    return {
        "root": str(root),
        "source": str(path),
        "present": True,
        # Both carried, because `composition` is the canonical name of the
        # thing that decided and `artifact` is the name a person installed. A
        # finding that named only one of them would leave the reader guessing
        # which release to go and look at.
        "composition": value.get("composition"),
        "artifact": value.get("name"),
        "entries": entries,
        "why": ("this artifact was built before `ARTIFACT.json` carried a "
                "`recommended` list" if entries is None else None),
        "note": (
            "the build's own statement, read from the revision this process "
            "was launched out of. `installed: false` on a row from here means "
            "exactly that: the composition declares it resolvable and this "
            "device has not recorded an installation of it. It does NOT mean "
            "the ability is absent, and it does NOT mean it is present — "
            "`serverosctl ability resolve` is what answers the second question."
        ),
    }


def declared_catalogue(here: Path | None = None) -> dict:
    """The authority's catalogue as this node last materialized it — the WIDER view.

    The other two sources each answer a different question and neither can
    answer this one:

      * the local projection says what is **installed on this device**;
      * `ARTIFACT.json` says what **this composition declared resolvable**;
      * this says **what the authority's catalogue contains at all**, read from
        `state/serveros/abilities/catalogue.json` — written by
        `ability_projection.refresh` from the same manifest read that produces
        the projection, so the two are never two readings of two revisions.

    Why it must exist, measured rather than argued: of the 21 responsibilities
    the product tree names, 8 are reachable through the projection and the
    composition's own recommendations, and 13 are not — `ability-management`,
    `runtime-management`, `workspace-management` and `test-management` among
    them, all four shipped in the composition this node runs. Every one of the
    13 IS in the catalogue. What the surface could not say is that "absent from
    this device" and "absent from the world" are different sentences; this is
    the only source that can say it.

    §35 keeps the projection local on purpose, so the wider answer arrives as a
    second artifact rather than as a widened first one. The daemon still holds
    no checkout and does not go looking for one: this reads a file the producer
    wrote, and if the producer has never run, the honest answer is that nobody
    has asked — `entries: None`, never `[]`, the same distinction every source
    on this surface draws.
    """
    state = paths_mod.Paths().root("state") / "abilities"
    path = state / ability_projection_mod.CATALOGUE_NAME
    record = _read_identity_file(path)
    if not record["present"]:
        return {
            "source": str(path),
            "present": False,
            "why": record["why"],
            "registry_status": None,
            "generated_at": None,
            "source_revision": None,
            "entries": None,
            "note": (
                f"no catalogue {record['why']} at {path}, so this node cannot "
                "say what the authority's catalogue contains. `entries: null` "
                "rather than an empty list: nobody has asked is not the same "
                "as the catalogue is empty, and only one of them is a claim "
                "about ability-management."
            ),
        }
    value = record["value"]
    raw = value.get("entries")
    entries = None
    if isinstance(raw, list):
        entries = [entry for entry in raw
                   if isinstance(entry, dict) and entry.get("name")]
    return {
        "source": str(path),
        "present": True,
        "why": ("this catalogue carries no `entries` list" if entries is None
                else None),
        "registry_status": value.get("registry_status"),
        "generated_at": value.get("generated_at"),
        "source_revision": value.get("source_revision"),
        "entries": entries,
        "product_vocabulary": value.get("product_vocabulary"),
        "product_vocabulary_error": value.get("product_vocabulary_error"),
        "note": (
            "the authority's catalogue at a pinned revision, materialized by "
            "`serverosctl ability refresh`. It names NO device: an entry here "
            "says the ability exists, never that this node has it. Read beside "
            "the local projection — present here and absent there is the whole "
            "distinction, and it is not a defect in either file."
        ),
    }


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


registry_mod = _load("serveros_runtime_registry", "registry.py")
_paths = _load("serveros_runtime_paths", "paths.py")
health_mod = _load("serveros_runtime_health", "health.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
inventory_mod = _load("serveros_runtime_inventory", "inventory.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
agent_sessions_mod = _load("serveros_runtime_agent_sessions", "agent_sessions.py")
native_core_facts_mod = _load("serveros_runtime_native_core_facts", "native_core_facts.py")
# The shared answer layer (§3/§6/§10/§14). The daemon computes NOTHING of its
# own for these: it forwards to the same functions `serverosctl` calls, so the
# CLI answer and the socket answer can differ only in the moment they were
# taken — which `answered_by` and `measured_at` make visible rather than
# something a caller has to assume.
view_mod = _load("serveros_runtime_view", "view.py")
# §4/§5/§6. The trusted-actor and approval kernels, plus the store they rest on.
# The kernels are pure and the store is not, which is the split that lets the
# decision a security boundary rests on be tested without a filesystem.
agent_control_store_mod = _load("serveros_runtime_agent_control_store",
                                "agent_control_store.py")
# The owner-issued uid -> actor -> scope binding. A CONTRACT and pure check,
# not a registry: production binds no resolver and every issue/verify refuses
# BLOCKED_OWNER_POLICY. Loaded under the same name the store uses, so the
# `OwnerPolicy` class is one object.
owner_policy_mod = _load("serveros_runtime_owner_policy", "owner_policy.py")
# §21. The alert state machine. Loaded here for the same reason the control
# store is: the daemon is what runs the tick, and the tick is what keeps the
# register current. It is NOT loaded so the daemon can send anything — the
# module holds no transport and this daemon holds no provider, which is the
# split §18 requires and `_r_agent_control_status` explains.
alerting_mod = _load("serveros_runtime_alerting", "alerting.py")

# §Q2. The composer that turns several alerts into one message. Loaded the same
# way and for the same reason as the register it reads: both are plain modules
# in this directory, and neither is a package this daemon is allowed to import
# by name.
digest_mod = _load("serveros_runtime_alert_digest", "alert_digest.py")
# §3. The card receipt ledger. Loaded the same way, for the same reason: a
# plain module in this directory rather than a package the daemon imports by
# name. The digest and the card receipt are two rhythms over one set of facts,
# and the daemon is the only writer of both.
card_delivery_mod = _load("serveros_runtime_card_delivery", "card_delivery.py")
_ACTOR_PATH = _HERE.parent / "context" / "actor.py"
if "_serveros_context_actor" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_serveros_context_actor", _ACTOR_PATH)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["_serveros_context_actor"] = _module
    _spec.loader.exec_module(_module)
actor_mod = sys.modules["_serveros_context_actor"]
approval_mod = _load("serveros_runtime_approval", "approval.py")
# §17. What a thread is for. The rules are pure and live in `coordination.py`;
# the daemon contributes the two things only it has — the durable binding store
# and a way to reach the Item authority.
coordination_mod = _load("serveros_runtime_coordination", "coordination.py")
# #245 / S2. The dispatch-attempt kernel, under the same synthetic name every
# other loader uses, so the `AttemptRefused` caught here is the one raised there.
dispatch_attempts_mod = _load("serveros_runtime_dispatch_attempts",
                              "dispatch_attempts.py")
# §M. Which session a conversation is addressing. Pure rules and the level
# projection; the daemon contributes the durable selection store. Loaded here
# rather than restated because the console's `selection_record` is what refuses
# a selection carrying an Item, and a daemon that built the record itself would
# be a second place that decides what a selection may be.
human_console_mod = _load("serveros_runtime_human_console", "human_console.py")
# The Item-state reader. Loaded from `context/` rather than restated here for
# the reason `context/resolver.py` states in its own docstring: the CLI and the
# socket must not read two different worlds.
_RESOLVER_PATH = _HERE.parent / "context" / "resolver.py"
if "serveros_context_resolver" not in sys.modules:
    _resolver_spec = importlib.util.spec_from_file_location(
        "serveros_context_resolver", _RESOLVER_PATH)
    _resolver_module = importlib.util.module_from_spec(_resolver_spec)
    sys.modules["serveros_context_resolver"] = _resolver_module
    _resolver_spec.loader.exec_module(_resolver_module)
resolver_mod = sys.modules["serveros_context_resolver"]
# §4/§2. The tree ALGORITHM, loaded rather than restated. The projection writer
# and this surface must derive the same tree from the same catalogue, and a
# second implementation here is two spellings of one idea that agree on the day
# they are written. The synthetic name is the one `runtime/cli.py` uses for the
# same file on purpose: two names for one module is two module objects, which is
# how a `isinstance` check starts failing on a value that is the right type.
#
# Legitimate for the core to hold: it is under `runtime/`, it reads the LOCAL
# projection rather than the authority, and `domain_tree()` is pure. The daemon
# never calls `refresh()` — writing the projection is `serverosctl ability
# refresh`'s job (§28), and a daemon that could reach another repository's git
# objects would make this loop's latency depend on that repository.
ability_projection_mod = _load("serveros_ability_projection", "ability_projection.py")
# §45/§46. The ability RESOLVER, loaded rather than restated, and under the
# SAME synthetic name `runtime/cli.py` gives it on purpose: two names for one
# module are two module objects, which is how an `isinstance` check starts
# failing on a value that is the right type — and the arm below catches
# `Rejected` by name.
#
# LOADED ON FIRST USE, NOT AT IMPORT — and that is §24's property, not tidiness.
# §1 freezes this process as the core and §24 says it must START with none of
# the systems it governs. The lines that used to stand here executed the
# resolver's module body while `daemon.py` was still being imported, so a node
# whose `abilities/resolver.py` was missing or unreadable did not come up at
# all: the core depending on the fabric for its own existence, which is the
# one thing the boundary is drawn to prevent. Measured rather than argued —
# `tests/loader_graph.py` is a static scan and reads a loader call inside a
# function exactly as it reads one at module level, so laziness does not move
# that measurement, and
# `tests/test_core_ability_boundary.py::AStartedCoreHasLoadedNoFabric` is a
# second, dynamic check that importing this module executes nothing under
# `abilities/`.
#
# Legitimate for this process to hold once a caller asks, by the test the
# comment above states for the projection: `abilities/resolver.py` is PURE (its
# own docstring: "no filesystem, no network, no clock"), it is in
# `CORE_MODULES` so it is present in a built artifact, it consults no authority
# — and `fleet/mcp-contract.yaml` names THIS process as the one that runs it,
# because `mcp/node_mcp.py` forwards `ability.resolve` here over the socket
# rather than holding a second kernel of its own.
# `tests/test_core_ability_boundary.py::TheCoresCrossingsAreRecorded` records
# the edge, so it is a deliberate entry in a named constant and not a line
# nobody reviews.
_ABILITY_RESOLVER_PATH = _HERE.parent / "abilities" / "resolver.py"
#: The synthetic name, spelled once. `runtime/cli.py` uses this same string for
#: the same file, and that identity is load-bearing: two names are two module
#: objects, and the arm below catches `Rejected` by name.
_ABILITY_RESOLVER_NAME = "serveros_ability_resolver"


def _ability_resolver():
    """The ability resolution kernel, loaded once and held.

    A function rather than a module-level binding because the only caller is a
    request handler: the load happens at the first `ability.resolve` and not
    before it. Both halvings of the name are written to `sys.modules` before
    the body runs, which is the ordinary idiom and also what keeps a second
    caller from loading a second copy.
    """
    module = sys.modules.get(_ABILITY_RESOLVER_NAME)
    if module is None:
        spec = importlib.util.spec_from_file_location(
            _ABILITY_RESOLVER_NAME, _ABILITY_RESOLVER_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[_ABILITY_RESOLVER_NAME] = module
        spec.loader.exec_module(module)
    return module


class DaemonError(Exception):
    """The daemon cannot continue as configured."""


class DaemonAlreadyRunning(DaemonError):
    """Another daemon already owns this endpoint.

    Raised rather than logged, because the alternative is what was measured on
    2026-10-05: two daemons on one socket path do not take turns. Each one
    unlinks what it finds and binds its own, the other notices on its next
    one-second accept timeout and does the same, and both go on logging
    `re-bound … the socket file was removed while this daemon was running`
    — 46 and 45 times in fifteen seconds, with both processes still up. A
    client that connects lands on whichever of them won the last race.
    """


# ---------------------------------------------------------------------------
# Failure budget — §19's "no infinite restart loop"
# ---------------------------------------------------------------------------

# Deliberately small. The cost of a low budget is that a person is asked
# sooner; the cost of a high one is the incident this module was written
# after. Asymmetric, so it is set on the cautious side.
MAX_ATTEMPTS = 3
WINDOW_SECONDS = 3600
BACKOFF_SECONDS = (60, 300, 900)


class Budget:
    """How many times this resource may be acted on, and how far apart.

    Persisted, because a budget that resets when the supervisor restarts is
    not a budget — `Restart=always` on the daemon would silently grant it a
    fresh three attempts every time, which is the original bug wearing a
    different hat.
    """

    def __init__(self, path: Path, *, now=None) -> None:
        self.path = Path(path)
        self._now = now or time.time
        self._state: dict[str, dict] = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def _entry(self, resource_id: str) -> dict:
        now = self._now()
        entry = self._state.get(resource_id) or {"attempts": [], "last_action": None}
        # Prune anything outside the window, so a resource that failed three
        # times last month is not permanently barred.
        entry["attempts"] = [t for t in entry["attempts"] if now - t < WINDOW_SECONDS]
        self._state[resource_id] = entry
        return entry

    def may_act(self, resource_id: str, *, max_attempts: int | None = None,
                backoff: list | tuple | None = None) -> dict:
        """§11: the bounds come from the resource when it declares them.

        The module constants are the fallback, not the authority. A resource
        that says `max_attempts: 1` and is given three is a resource whose
        declaration nothing read — and a per-resource policy block that no
        code consults is the exact defect this round keeps finding, one layer
        down from where it was found last time.
        """
        limit = MAX_ATTEMPTS if max_attempts is None else int(max_attempts)
        ladder = tuple(backoff) if backoff else BACKOFF_SECONDS
        entry = self._entry(resource_id)
        used = len(entry["attempts"])
        if used >= limit:
            oldest = min(entry["attempts"])
            return {
                "allowed": False,
                "reason": "BUDGET_EXHAUSTED",
                "attempts_in_window": used,
                "max_attempts": limit,
                "window_seconds": WINDOW_SECONDS,
                "retry_after_seconds": int(WINDOW_SECONDS - (self._now() - oldest)),
                "note": (
                    f"{used} attempts within {WINDOW_SECONDS}s and this "
                    f"resource permits {limit}. One more would not be a new "
                    f"attempt at a fix, it would be the loop. Report "
                    f"DEGRADED and ask a person."
                ),
            }
        # The waiting period belongs to the attempt ABOUT to be made, so it is
        # indexed by how many have already happened: 0 attempts means the
        # first retry waits BACKOFF_SECONDS[0]. Indexing by `used` instead
        # would silently skip the shortest interval — the one that matters
        # most, because it is the one that runs while a person is still
        # looking at the screen.
        wait = ladder[min(used, len(ladder) - 1)] if used else 0
        if entry["attempts"] and used:
            wait = ladder[min(used - 1, len(ladder) - 1)]
            since = self._now() - max(entry["attempts"])
            if since < wait:
                return {
                    "allowed": False,
                    "reason": "BACKOFF",
                    "attempts_in_window": used,
                    "retry_after_seconds": int(wait - since),
                }
        return {"allowed": True, "attempts_in_window": used,
                "backoff_before_next_attempt_seconds": wait}

    def record(self, resource_id: str, action: str) -> dict:
        entry = self._entry(resource_id)
        entry["attempts"].append(self._now())
        entry["last_action"] = action
        self._save()
        return {"attempts_in_window": len(entry["attempts"])}

    def last_action(self, resource_id: str) -> str | None:
        return (self._state.get(resource_id) or {}).get("last_action")

    def snapshot(self) -> dict:
        return {rid: {"attempts": len(e.get("attempts", [])),
                      "last_action": e.get("last_action")}
                for rid, e in sorted(self._state.items())}


# ---------------------------------------------------------------------------
# Reconcile — the one place that changes anything
# ---------------------------------------------------------------------------


class SubprocessRunner:
    """The only thing in this tree that may write to the world.

    It is deliberately NOT `probes.LiveHost`. That class documents that none
    of its methods mutates, and every probe holds one; adding a mutating
    method to it would mean "observe only" stops being a property anything
    can check, because any probe could reach the actuator through its own
    host. Keeping the two objects separate means a probe cannot mutate even
    by mistake, which is the only version of that guarantee worth having.
    """

    name = "actuator-subprocess"

    def run(self, args: list[str], timeout: float) -> dict:
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"exit_code": 127, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
        return {"exit_code": out.returncode,
                "stdout": out.stdout[-2000:],
                "stderr": out.stderr[-2000:]}


class Actuator:
    """Executes a reconcile against the thing that actually supervises.

    serverosd never forks, never daemonizes and never signals a process
    directly. It asks systemd, because systemd is the supervisor and a second
    supervisor is how you get two components disagreeing about whether
    something is running.
    """

    def __init__(self, runner=None, *, dry_run: bool = True) -> None:
        self.runner = runner or SubprocessRunner()
        self.dry_run = dry_run
        self.performed: list[dict] = []

    def perform(self, operation: str, resource) -> dict:
        """Dispatch one §8 operation.

        This method is the whole of what the runtime can do to the machine.
        The vocabulary is closed by `managed-runtime-contract.yaml` and the
        mode→operation mapping is read from the same file, so adding a sixth
        operation is an edit to a reviewed document rather than a new branch
        somebody adds here.

        An operation with no actuator returns NO_ACTUATOR — the same answer
        the restart path already gives for a resource kind nobody wrote an
        actuator for. `RECONNECT` and `STOP` are declared and unimplemented
        on purpose: a declared operation with no implementation has to be
        visible, or the next person infers it works from its absence in a
        list of failures.
        """
        if operation == "RESTART":
            return self.restart(resource)
        if operation in ("RECONNECT", "STOP"):
            return {
                "performed": False,
                "reason": "NO_ACTUATOR",
                "operation": operation,
                "note": (
                    f"{operation} is declared in the operation vocabulary and "
                    f"has no actuator. It is not a lesser RESTART: for a "
                    f"transport-endpoint or a tunnel the channel is the thing "
                    f"that failed, and running the restart path against it "
                    f"would be acting on a different object and reporting it "
                    f"as this one."
                ),
            }
        return {
            "performed": False,
            "reason": "UNKNOWN_OPERATION",
            "operation": operation,
            "note": (
                "not in the declared operation vocabulary. Refused rather than "
                "defaulted, because the one thing a control plane must never "
                "do is turn an unrecognised instruction into the nearest "
                "recognised one."
            ),
        }

    def restart(self, resource) -> dict:
        unit = resource.record.get("unit") or next(
            (p.get("unit") for p in resource.probes if p.get("unit")), None)
        if unit:
            command = ["systemctl", "--user", "restart", unit]
        elif resource.kind == "container":
            container = _container_name(resource)
            if not container:
                return {
                    "performed": False,
                    "reason": "NO_TARGET",
                    "note": "registered as a container but no probe names which one",
                }
            command = ["docker", "restart", container]
        else:
            return {
                "performed": False,
                "reason": "NO_ACTUATOR",
                "note": (
                    f"serverosd has no way to restart a {resource.kind}. It "
                    f"does not implement one: guessing a command for a kind "
                    f"nobody wrote an actuator for is how a control plane "
                    f"becomes a footgun."
                ),
            }

        if self.dry_run:
            return {
                "performed": False,
                "dry_run": True,
                "would_run": command,
                "note": "observe-only; --enable-reconcile is required to act",
            }
        outcome = self.runner.run(command, 60.0)
        result = {"performed": outcome["exit_code"] == 0, "command": command,
                  "exit_code": outcome["exit_code"],
                  "stderr": (outcome.get("stderr") or "").strip()[:300]}
        self.performed.append(result)
        return result


def _operation_for_mode(mode: str) -> str:
    """The §8 operation a reconcile mode authorises.

    Read from `managed-runtime-contract.yaml` so the mapping is declared once.
    An unknown mode raises rather than falling back: the registry validates
    modes at registration, so reaching here with one means the contract and
    the validator have diverged, and the safe response to that is to stop
    rather than to perform the nearest recognised operation.
    """
    mapping = registry_mod.contract()["reconcile_policy"]["mode_operations"]
    if mode not in mapping:
        raise registry_mod.RegistryError(
            f"reconcile mode {mode!r} has no declared operation; the contract's "
            f"modes and its mode_operations disagree"
        )
    operation = mapping[mode]
    if operation is None:
        # `observe` authorises nothing. Returning a name here would be the
        # exact defect this whole block exists to prevent, so it is answered
        # as a refusal.
        return "NONE"
    return operation


def _container_name(resource) -> str | None:
    for probe in resource.probes:
        if probe.get("container"):
            return probe["container"]
    return None


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class Daemon:
    """Observe, probe, persist, detect drift, reconcile, serve.

    Every step is separately callable, so a test can run one tick without a
    socket, a timer or a signal handler.
    """

    def __init__(self, registry, *, state_dir: Path, socket_path: Path | None = None,
                 host=None, inventory=None, enable_reconcile: bool = False,
                 runner=None, now=None, sleep=None, log=None,
                 dispatch_observation_source=None,
                 dispatch_grant_verifier=None,
                 owner_policy_resolver=None,
                 native_lease_path=None, owner_map_path=None) -> None:
        self.registry = registry
        self.state_dir = Path(state_dir)
        self.socket_path = Path(socket_path) if socket_path else default_socket_path()
        # Set by `_open_socket`; the identity of the socket file this daemon
        # bound, so that teardown cannot act on a socket that is not ours.
        self._socket_ino: int | None = None
        # Set by `_hold_singleton`; the file descriptor whose `flock` makes
        # "one daemon per socket path" a property of the kernel rather than of
        # everyone remembering. `None` until `serve_forever` claims it, which
        # is why a test may build a `Daemon` — or call `_open_socket` — without
        # claiming anything.
        self._singleton_fd: int | None = None
        self.host = host or probes_mod.LiveHost()
        self.inventory = inventory or inventory_mod.Inventory()
        self.enable_reconcile = enable_reconcile
        self._now = now or time.time
        # Injectable alongside `now`, because the settle window below is a
        # real wait and a test that cannot compress it either sleeps for
        # seconds or does not test the window at all. Tests pass a clock and
        # a sleep that advance each other; the daemon passes neither.
        self._sleep = sleep or time.sleep
        self._log = log or (lambda message: None)

        # #245 / S2. The two authorities a reconciliation needs are DAEMON-
        # HELD, not request fields:
        #
        #   * the observation source is the provider read that can be asked by
        #     the client turn id this node still holds after the response was
        #     lost (`CLIENT_TURN_ID`); an adapter that can only be asked by the
        #     id that arrived in the lost response is not usable;
        #   * the grant verifier is the read that says the recorded grant is
        #     still current (the future #272 `agent.lease.authority.verify`
        #     adapter, once its parent is owner-approved).
        #
        # Production passes neither. `agent.dispatch.reconcile` then refuses
        # `BLOCKED_PROVIDER_SOURCE` / `BLOCKED_GRANT_UNKNOWN`; it never falls
        # back to a caller-supplied source, capability or owner, and it never
        # marks an effect settled. A request cannot register or replace either
        # object.
        self._dispatch_observation_source = dispatch_observation_source
        self._dispatch_grant_verifier = dispatch_grant_verifier
        # #237 B3. The OWNER'S uid -> actor -> scope binding. This is the
        # remaining fact serveros cannot derive: a kernel uid and a
        # provider-signed human delegation do not, by themselves, say the owner
        # authorized that uid to act as that actor for that project/Item/action.
        # Production binds no resolver. Both `agent.grant.issue` and
        # `agent.lease.authority.verify` then refuse `BLOCKED_OWNER_POLICY`;
        # nothing here infers a policy from a uid or a context, and no grant is
        # minted for an unknown user. A resolver is a READ of an owner-issued
        # fact, never a second grant store.
        self._owner_policy_resolver = owner_policy_resolver

        self.budget = Budget(self.state_dir / "health/reconcile-state.json", now=self._now)
        # Passed `runner`, not `host`: the actuator must not be reachable
        # through the object every probe holds. See SubprocessRunner.
        self.actuator = Actuator(runner=runner, dry_run=not enable_reconcile)
        # The single-writer lease the resident Native Core also takes, and the
        # owner map it also reads. This is the EFFECT seam: a restart is a
        # mutation, and two programs each with their own budget restarting the
        # same unit is the failure the lease exists for. It is deliberately not
        # applied to observation.
        #
        # `enable_reconcile` without a lease path is a wiring error, not a
        # licence: an acting daemon that cannot prove it is the only writer is
        # refused at the effect. A dry run needs neither.
        #: The lease is ALWAYS applied to an effect. A default path is used
        #: rather than "no lease", because "no lease" is the split-lock failure
        #: this exists to prevent. `$XDG_RUNTIME_DIR/serveros` is where the
        #: Core's own status socket lives, so a Core started with the default
        #: `--lease` and this daemon contend on one file by construction.
        resolved_lease = native_lease_path
        if resolved_lease is None:
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
            base = Path(runtime_dir) / "serveros" if runtime_dir else self.state_dir
            resolved_lease = base / "native-core.lease"
        self._native_lease = native_core_facts_mod.SafeLease(resolved_lease)
        self._owner_map = None
        if owner_map_path is not None:
            self._owner_map = native_core_facts_mod.OwnerMap.load(owner_map_path)
        self.last_tick: dict | None = None
        self.drift: list[dict] = []
        # The node runtime's own facts, for `node.status`. A control plane that
        # cannot say how long it has been up and when it last looked is asking
        # a reader to trust an answer without being able to date it.
        self.started_at = self._now()
        # §J09. Frozen at construction, reported read-only. A node runtime that
        # cannot say which release it is running is asking an operator to
        # identify it from the outside — from a symlink it may have stopped
        # following an hour ago.
        self.identity = _installed_identity()
        self.interval: float | None = None
        self.ticks = 0
        self.last_tick_monotonic: float | None = None
        self.tick_failures: list[dict] = []

        # Register the daemon's own directories through the path contract,
        # rather than mkdir-ing a path that looks right. A daemon that invents
        # its own state location is the exact drift §40 is about.
        self._paths = paths_mod.Paths()

        # §5/§6, §23. Trusted contexts and approvals survive a restart, and the
        # store is opened lazily so that `serverosd --check` and the many tests
        # that never touch agent control do not create a file they do not use.
        self._control_store: object | None = None
        # §21. The alert register. Lazy for the same reason: a daemon that
        # never detects anything should not create an empty register, because
        # an empty register is indistinguishable from one whose alerts were
        # all resolved and then forgotten.
        self._alert_store: object | None = None
        # §Q2's delivery ledger. Opened lazily beside the register, and None
        # rather than {} so that "never opened" and "opened and empty" cannot
        # be confused — an empty cursor is a real state and a loud one.
        self._delivery_cursor: object | None = None
        # §3's card receipt ledger. A SECOND document beside that cursor, not
        # a second key inside it: a status broadcast is produced by a clock and
        # a digest by a delta, and one ledger would let the hourly card advance
        # the alert delta window.
        self._card_ledger: object | None = None
        self.last_alert_pass: dict | None = None

    # A status answer older than this many tick intervals is labelled stale.
    # Three, not one: a single missed deadline is a slow machine, and calling
    # that stale would make the field useless on a busy host.
    STALE_TICKS = 3

    # -- one pass ----------------------------------------------------------

    def tick(self) -> dict:
        started = self._now()
        observations = self.observe_all()
        drift = self.detect_drift(observations)
        decisions = self.reconcile(observations)
        # §21. The alert register is kept current by the pass that just looked
        # at the world, using the observations it took — not by a second
        # measurement taken while the first is being reported.
        #
        # A failed alert pass does NOT fail the tick. The daemon loop dying is
        # a strictly larger problem than a stale register, and the failure is
        # recorded here rather than only logged, so "no alerts" and "the alert
        # pass has been raising for an hour" are different answers.
        try:
            alert_pass: dict = self.alert_pass(measured=observations)
        except Exception as exc:  # noqa: BLE001 - the loop outlives this
            alert_pass = {
                "error": f"{type(exc).__name__}: {exc}",
                "note": ("the alert register was NOT updated this tick. An "
                         "unchanged register does not mean nothing changed."),
            }
        report = {
            "tick_started_at": _iso(started),
            "duration_seconds": round(self._now() - started, 3),
            "registry_count": len(self.registry),
            "enable_reconcile": self.enable_reconcile,
            "observations": observations,
            "drift": drift,
            "decisions": decisions,
            "alerts": {"passes": [{"detector": p["detector"],
                                   "opened_or_updated": len(p["alerts"]),
                                   "pending": len(p["pending"]),
                                   "held": len(p["held"])}
                                  for p in alert_pass.get("passes", [])],
                       "at": alert_pass.get("at"),
                       "error": alert_pass.get("error")},
            "budget": self.budget.snapshot(),
        }
        self.last_tick = report
        self.last_tick_monotonic = self._now()
        self.drift = drift
        self.persist(report)
        return report

    def observe_all(self) -> dict:
        out: dict[str, dict] = {}
        for resource in self.registry.all():
            try:
                health = probes_mod.observe(resource, self.host)
            except registry_mod.RegistryError as exc:
                # One broken probe must not take down the loop. The resource
                # reports UNKNOWN with the reason, which is a fact about the
                # probe, and every other resource still gets measured.
                out[resource.id] = {
                    "error": str(exc),
                    "state": "UNKNOWN",
                    "reason": "NOT_OBSERVED",
                    "note": "the probe raised; the resource is not thereby fine",
                }
                continue
            decision = health_mod.action_for(resource, health)
            out[resource.id] = {
                # The state is lifted to the top level as well as living inside
                # `health`. Callers that only need "what is it" should not have
                # to know the shape of an axis record, and the drift detector
                # reads this key — a missing one there would have made every
                # resource look like it had no state rather than like it had a
                # bad one.
                "state": health.state,
                "health": health.show(),
                "action": decision,
                "protection": resource.protection,
                "lifecycle": resource.lifecycle,
                "owner": resource.owner_ref,
                "observed_at": health.observed_at,
            }
        return out

    # -- drift -------------------------------------------------------------

    def detect_drift(self, observations: dict) -> list[dict]:
        """Where the declaration and the world disagree.

        Drift is NOT a fault. A protected resource that is down is a fault; a
        registration naming a container that no longer exists is drift, and
        the response is to fix the registration, not to start something. The
        two are separated because conflating them is how a control plane
        starts recreating things people deliberately deleted.
        """
        drift = []
        for resource in self.registry.all():
            observed = observations.get(resource.id) or {}
            health = observed.get("health") or {}
            state = health.get("state") or observed.get("state")
            if state == "UNAVAILABLE":
                drift.append({
                    "resource": resource.id,
                    "kind": "DECLARED_BUT_NOT_OBSERVABLE",
                    "observed": "UNAVAILABLE",
                    "desired": f"{resource.desired}/{resource.protection}",
                    "proposed_action": "review the registration, not the world",
                    "note": (
                        "the registry names something this node cannot observe "
                        "at all — the container or unit is gone. This is a "
                        "stale declaration, and the fix is to edit the "
                        "declaration, not to recreate the thing."
                    ),
                })
                continue
            if state == "UNKNOWN":
                # Distinct from UNAVAILABLE and worth separating: UNAVAILABLE
                # means the probe reached the host and found nothing to
                # measure; UNKNOWN means no probe ran. The second is a defect
                # in the registry's probe coverage, and reporting it as an
                # outage would send somebody to look at a working resource.
                drift.append({
                    "resource": resource.id,
                    "kind": "REGISTERED_WITHOUT_A_PROBE",
                    "observed": "UNKNOWN",
                    "proposed_action": "add a probe, or the resource is unmanaged in practice",
                    "note": "a registration nobody can observe is not management",
                })
                continue
            if resource.protection == "protected" and state in ("NOT_RUNNING", "FUNCTIONALLY_FAILED"):
                drift.append({
                    "resource": resource.id,
                    "kind": "PROTECTED_AND_DOWN",
                    "observed": state,
                    "desired": "running",
                    "proposed_action": "NEEDS_HUMAN",
                    "note": (
                        "protected means ServerOS will not act. It does not "
                        "mean nobody should be told."
                    ),
                })
        return drift

    # -- reconcile ---------------------------------------------------------

    @staticmethod
    def _resource_unit(resource) -> str | None:
        """The systemd unit a resource is about, if it names one.

        The owner map is keyed by unit, because the Core's manifest is. A
        resource with no systemd probe has no unit to share, so there is nothing
        for the Core to contend with and nothing to key an owner on.
        """
        # The registry stores a probe's fields flat in the probe record (see
        # `SystemdUnitState.run`, which reads `self.spec.get("unit")` where
        # `spec` IS the probe dict). `Actuator.restart` reads the same shape.
        for probe in resource.probes:
            if probe.get("kind") != "systemd-unit-state":
                continue
            unit = probe.get("unit")
            if not isinstance(unit, str) or not unit:
                nested = probe.get("spec")
                unit = nested.get("unit") if isinstance(nested, dict) else None
            if isinstance(unit, str) and unit:
                return unit
        return None

    def reconcile(self, observations: dict) -> list[dict]:
        decisions = []
        for resource in self.registry.all():
            observed = observations.get(resource.id) or {}
            action = (observed.get("action") or {}).get("action")
            if action != "RECONCILE":
                continue
            # §8/§11. The operation is derived from the resource's own policy,
            # never from the caller. Recorded even when the decision is
            # "recommendation only", because a recommendation that does not
            # name the operation it recommends is not actionable by the person
            # it is addressed to.
            #
            # Recording the mode alongside the operation is what makes the
            # decision auditable: "RESTART" alone cannot distinguish a resource
            # whose policy says restart from one whose policy was bypassed.
            policy = resource.reconcile
            operation = _operation_for_mode(policy["mode"])
            split = resource.automatability()
            entry = {
                "resource": resource.id,
                "action": "RECONCILE",
                "automatable": split["automatable"],
                "recommendation_only": split["recommendation_only"],
                "reconcile_mode": policy["mode"],
                "operation": operation,
            }
            if not resource.is_automatable:
                entry.update({
                    "performed": False,
                    # `blocked_by` keeps its one meaning across the tree: these
                    # are reasons that FORBID the action. A resource whose
                    # policy is merely `observe` is not forbidden — it is
                    # permitted-and-not-enabled, which is a different fact and
                    # is reported as `blocked_by_policy` so that a reader is
                    # not told a policy question is a permission question.
                    "blocked_by": split["permission"],
                    "blocked_by_policy": split["policy"],
                    "note": (
                        "§9: recommendation only. The health model says this "
                        "is a candidate and nothing here authorises ServerOS "
                        "to carry the operation out by itself."
                    ) if split["recommendation_only"] else (
                        "the health model said RECONCILE; the registry says no"
                    ),
                })
                decisions.append(entry)
                continue
            budget = self.budget.may_act(resource.id,
                                         max_attempts=policy["max_attempts"],
                                         backoff=policy["backoff"])
            entry["budget"] = budget
            if not budget["allowed"]:
                # §54: an exhausted budget is NEEDS_HUMAN, and it has to say
                # so in the field a reader filters on. Leaving `action` at
                # RECONCILE with a prose `escalate_to` beside it produces a
                # decision record that goes on naming a reconcile candidate
                # every tick, forever, for a resource nothing will reconcile —
                # which is the restart loop reported instead of run.
                entry.update({
                    "action": "NEEDS_HUMAN",
                    "performed": False,
                    "budget_exhausted": True,
                    "escalate_to": "DEGRADED/NEEDS_HUMAN",
                    "because": (
                        f"the {policy['mode']} budget is spent: "
                        f"{budget['attempts_in_window']} attempt(s) in the "
                        f"window against max_attempts={policy['max_attempts']}"
                        + (
                            f", next permitted in "
                            f"{budget['backoff_before_next_attempt_seconds']}s"
                            if budget.get("backoff_before_next_attempt_seconds")
                            else ""
                        ) + ". Nothing here will act again until a person "
                        "does."
                    ),
                    "note": (
                        "the budget is the §19 guard. Repeatedly restarting a "
                        "resource that keeps failing is not recovery, it is the "
                        "loop."
                    ),
                })
                decisions.append(entry)
                # And the observation says the same thing. `observe_all` ran
                # before this point and could only ask the health model, which
                # does not know the budget — so without this, `observations[id]
                # .action.action` went on reading RECONCILE while the decision
                # beside it read NEEDS_HUMAN. Two fields disagreeing about one
                # resource is how a reader learns to trust neither.
                self._escalate_observation(observations, resource.id, entry)
                continue
            if not self.enable_reconcile:
                # The process-level interlock. Note what it does NOT do: it is
                # not what authorises a resource. Every resource whose policy
                # is `observe` has already been filtered out above, so this
                # switch can only ever *withhold* an action a resource already
                # permitted. §11 forbids one switch that turns recovery on for
                # everything; this is the opposite shape — it grants nothing on
                # its own.
                dry = self.actuator.perform(operation, resource)
                entry.update({
                    "performed": False,
                    "dry_run": True,
                    "would_run": dry.get("would_run"),
                    "dry_run_reason": dry.get("reason"),
                    "note": (
                        "the resource's policy permits this operation and the "
                        "process interlock withholds it. --enable-reconcile "
                        "clears the interlock; it does not clear "
                        "reconcile.mode=observe, which is the gate that applies "
                        "to every resource registered on this host."
                    ),
                })
                decisions.append(entry)
                continue
            # ---- THE EFFECT SEAM -------------------------------------------
            # Everything above decided WHAT should happen; this is where it
            # happens, and it is the only place in this daemon that changes
            # the machine. The resident Native Core can restart the same unit
            # with its own budget, and neither program can see the other, so
            # the pen has to be shared rather than assumed.
            unit = self._resource_unit(resource)
            if self._owner_map is not None and unit is not None:
                owner = self._owner_map.owner_of(unit)
                if owner is not None and owner != native_core_facts_mod.OWNER_SERVEROSD:
                    entry.update({
                        "performed": False,
                        "blocked_by": native_core_facts_mod.OWNER_NOT_MINE,
                        "because": (
                            f"{unit} is owned by {owner!r} in the shared owner "
                            f"map, so this daemon may not act on it"
                        ),
                    })
                    decisions.append(entry)
                    continue
            acquired = False
            if self._native_lease is not None:
                try:
                    self._native_lease.acquire()
                    acquired = True
                except native_core_facts_mod.FactsRefused as exc:
                    entry.update({
                        "performed": False,
                        "blocked_by": exc.code,
                        "because": exc.detail,
                    })
                    decisions.append(entry)
                    continue
            try:
                result = self.actuator.perform(operation, resource)
            finally:
                if acquired:
                    self._native_lease.release()
            self.budget.record(resource.id, operation.lower())
            entry["result"] = result
            entry["performed"] = bool(result.get("performed"))
            # Re-probe, letting the resource settle: "did the restart fix it"
            # is a question only a probe can answer, and assuming yes is how a
            # restart loop reports success.
            entry["reprobe"] = self._reprobe(resource, settle=True)
            # Whether the operation worked is a separate fact from whether it
            # was carried out, and the two are reported separately so that
            # "performed: true" cannot be read as "fixed".
            entry["recovered"] = entry["reprobe"]["settled"]
            decisions.append(entry)
        return decisions

    def _escalate_observation(self, observations: dict, resource_id: str,
                              entry: dict) -> None:
        """Keep `observations[id].action` and the reconcile decision in step.

        The observation is built before the budget is consulted, so on its own
        it can only ever report the health model's verdict. The budget is the
        other half of "may this be acted on", and it belongs to the same
        report.
        """
        row = observations.get(resource_id)
        if not isinstance(row, dict) or not isinstance(row.get("action"), dict):
            return
        row["action"] = {
            **row["action"],
            "action": entry["action"],
            "budget_exhausted": entry["budget_exhausted"],
            "because": entry["because"],
        }

    # How long a restarted resource is given to become answerable again, and
    # how often to ask in the meantime. Measured, not guessed: the §53
    # acceptance fixture is a trivial HTTP responder and still needed ~1s
    # after `systemctl restart` returned before it accepted a connection.
    SETTLE_SECONDS = 10.0
    SETTLE_INTERVAL = 0.5

    def _reprobe(self, resource, *, settle: bool = False) -> dict:
        """Ask the resource whether the operation worked.

        Measured on 2026-09-13: a `systemctl --user restart` that returned
        exit 0 on a trivial HTTP responder was recorded as `FUNCTIONALLY_FAILED`,
        because the re-probe ran in the window between the supervisor returning
        and the service listening. That is the restart's *latency* being
        reported as the restart's *effect*. §53 asks for a re-probe that is OK,
        so a resource that is not yet answering is asked again, for a bounded
        time, until it answers.

        It is deliberately not "sleep N, then ask once": a restart that did not
        help must be reported as not helping, and the budget must count it. The
        loop leaves as soon as the resource answers, and a resource that never
        answers returns its last real observation with `settled: false`. It
        never manufactures a success and never waits indefinitely.

        `settle=False` takes a single sample, which is the honest answer when
        no operation was performed and there is nothing to settle from.
        """
        started = self._now()
        deadline = started + self.SETTLE_SECONDS if settle else None
        samples = 0
        while True:
            try:
                health = probes_mod.observe(resource, self.host)
            except registry_mod.RegistryError as exc:
                return {"state": "UNKNOWN", "error": str(exc), "settled": False}
            samples += 1
            state = health.state
            answered = state in health_mod.HEALTHY_STATES
            if answered or deadline is None or self._now() >= deadline:
                result = {
                    "state": state,
                    "reason": health.reason,
                    "settled": answered,
                    "samples": samples,
                    "waited_seconds": round(self._now() - started, 2),
                }
                if not answered:
                    result["note"] = (
                        f"the resource did not answer within "
                        f"{self.SETTLE_SECONDS:g}s of the operation. Reported as "
                        "the last real observation rather than retried further: "
                        "the budget counts attempts, and a longer wait here "
                        "would be the restart loop wearing a timeout."
                    )
                return result
            self._sleep(self.SETTLE_INTERVAL)

    # -- persistence -------------------------------------------------------

    def persist(self, report: dict) -> list[str]:
        written = []
        observed = self.state_dir / "observed/current.json"
        health = self.state_dir / "health/current.json"
        hygiene = self.state_dir / "hygiene/current.json"
        for path, payload in (
            (observed, {"schema": "serveros/observed.v1", **report}),
            (health, {
                "schema": "serveros/health.v1",
                "observed_at": report["tick_started_at"],
                "resources": {
                    rid: (obs.get("health") or {"state": obs.get("state")})
                    for rid, obs in report["observations"].items()
                },
            }),
            (hygiene, self._hygiene_snapshot()),
        ):
            _atomic_write(path, payload)
            written.append(str(path))
        return written

    def _hygiene_snapshot(self) -> dict:
        try:
            hygiene = hygiene_mod.Hygiene(self.registry, self.inventory)
            report = hygiene.report()
        except Exception as exc:  # noqa: BLE001 - a failed snapshot is reported
            return {"schema": "serveros/hygiene.v1", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "schema": "serveros/hygiene.v1",
            "generated_at": _iso(self._now()),
            "docker": report["docker"].get("by_classification"),
            "actionable": report["docker"].get("actionable"),
            "summary": report["summary"],
        }

    # -- the served interface ----------------------------------------------

    def handler_map(self) -> dict:
        """The served methods, as one object.

        Separate from `handle` so that the map and `_methods()` can be
        compared. While this was a literal inside `handle`, nothing could
        enumerate it, and the docstring on the coverage test described a drift
        the test was structurally unable to detect.
        """
        return {
            "device.show": self._r_device_show,
            "context.current": self._r_context_current,
            "path.resolve": self._r_path_resolve,
            "path.admit": self._r_path_admit,
            "runtime.list": self._r_runtime_list,
            "runtime.status": self._r_runtime_status,
            "runtime.explain": self._r_runtime_explain,
            "resource.summary": self._r_resource_summary,
            "resource.hygiene": self._r_resource_hygiene,
            "observation.get": self._r_observation_get,
            "ability.list": self._r_ability_list,
            "ability.status": self._r_ability_status,
            # §4/§2. The discovery tree and the single-ability read, both
            # derived from the same projection `ability.list` reports. §11
            # names `ability.inspect` on this surface; before these two it was
            # served as an ALIAS of `ability.status`, which was true of nothing
            # a caller could observe and stopped being true the moment there
            # was a richer answer to give.
            "ability.tree": self._r_ability_tree,
            "ability.inspect": self._r_ability_inspect,
            # §45/§46, and the Agent fast path §24 asks for: find the few
            # abilities a task needs, and ask whether this device can do one of
            # them now. Both are READ-ONLY and both are pure-with-respect-to-
            # the-world: `ability.search` reads the local projection this node
            # already holds, and `ability.resolve` runs the pure kernel with
            # the inputs this process can supply and NAMES the ones it cannot.
            # Neither writes, neither reaches ability-management, and neither
            # is a new authority — the header of every answer still says whose
            # registry this is a projection of.
            "ability.search": self._r_ability_search,
            "ability.resolve": self._r_ability_resolve,
            # Internal policy primitive used by the dedicated Agent Session MCP.
            # The daemon decides intent only and never calls an Agent provider.
            "agent.action.admit": self._r_agent_action_admit,
            # §3/§5/§6/§10/§14. Read-only, and every one of them is a forward
            # to `view.py` — the daemon adds no logic of its own here, because
            # a daemon that answers a question differently from the CLI is a
            # daemon whose answer cannot be checked by running the CLI.
            "runtime.groups": self._r_runtime_groups,
            "runtime.group": self._r_runtime_group,
            "runtime.tree": self._r_runtime_tree,
            "runtime.budget": self._r_runtime_budget,
            "process.top": self._r_process_top,
            "process.trace": self._r_process_trace,
            "anomaly.list": self._r_anomaly_list,
            "anomaly.explain": self._r_anomaly_explain,
            "container.attribution": self._r_container_attribution,
            # §14/§15/§16/§16A/§17. Read-only, argument-free, and each one a
            # forward to `view.py`. Their names are separate namespaces rather
            # than more `resource.*`: `resource.hygiene` classifies CONTAINERS
            # (§7's subject) and `runtime.hygiene` classifies the REGISTRATIONS,
            # and one word for both questions is how a reader ends up holding
            # the answer to the other one.
            "ports.ownership": self._r_ports_ownership,
            "desired.actual": self._r_desired_actual,
            # SYS-02. Reads only; the two writes are the CLI's, on purpose —
            # `_methods()` above argues why.
            "environment.leases": self._r_environment_leases,
            "environment.effective": self._r_environment_effective,
            "runtime.hygiene": self._r_runtime_hygiene,
            "fs.hygiene": self._r_fs_hygiene,
            "protection.resolve": self._r_protection_resolve,
            # §6/§7/§13/§19. The two reads §19's suggested surface names that
            # had no tool at all. Both are forwards to `view.py` over
            # `inventory.py` and `infrastructure.py` — the producers §6 and §13
            # already have — so this adds a way to ASK for facts that existed
            # and adds no new measurement.
            "resource.docker": self._r_docker_observation,
            "tunnel.list": self._r_tunnel_observation,
            # §18/§19. The sixteen resource anomaly codes. A forward to
            # `view.resource_anomaly`, which gathers from the four producers
            # that already own these facts; the daemon contributes the one
            # thing only it has — its own last tick's observations, so the
            # declaration comparison is the one it is acting on rather than a
            # second probe taken while an answer is being assembled.
            "resource.anomaly.list": self._r_resource_anomaly,
            "node.status": self._r_node_status,
            # §5/§6. The trusted half of Agent Control. These are on the
            # NODE runtime plane, not on the control-agent MCP surface: an
            # Agent runtime is a caller of `agent.*`, never a source of the
            # identity behind one.
            "agent.context.attach": self._r_agent_context_attach,
            "agent.context.inspect": self._r_agent_context_inspect,
            "agent.context.resolve": self._r_agent_context_resolve,
            "agent.grant.issue": self._r_agent_grant_issue,
            # The trusted-parent verification READ that Work Engine #14's
            # `OwnerVerifiedLease` needs. Read-only: it mints nothing, acquires
            # nothing and mutates no lease. It is NOT a lease verb.
            "agent.lease.authority.verify": self._r_agent_lease_authority_verify,
            "agent.approval.inspect": self._r_agent_approval_inspect,
            "agent.approval.record": self._r_agent_approval_record,
            "agent.control.status": self._r_agent_control_status,
            # §17. What a thread is for. `agent.binding.record` and
            # `agent.binding.close` are the two writes; everything §17 refuses
            # is refused by READING through `agent.binding.resolve`, which is
            # why the read is the one with the Item in it.
            "agent.binding.resolve": self._r_agent_binding_resolve,
            "agent.binding.list": self._r_agent_binding_list,
            "agent.binding.record": self._r_agent_binding_record,
            "agent.binding.claim": self._r_agent_binding_claim,
            "agent.binding.close": self._r_agent_binding_close,
            # #245 / S2. The durable dispatch attempt. `agent.dispatch.resolve`
            # is the read the router's replay guard matches a retry against;
            # `agent.dispatch.record` is the write — CALL_NOT_SENT before the
            # send, SETTLED/EFFECT_UNKNOWN after — and it is the record that
            # makes a crash BETWEEN those two distinguishable from one after.
            "agent.dispatch.resolve": self._r_agent_dispatch_resolve,
            "agent.dispatch.record": self._r_agent_dispatch_record,
            # #245 / S2. The RECONCILIATION read/driver entry. It asks the
            # daemon-held observation source what happened to an EFFECT_UNKNOWN
            # attempt and records the answer in the SAME dispatch-attempt
            # ledger. It is not a send path and cannot mark success: with no
            # trusted source (the production state) it refuses
            # BLOCKED_PROVIDER_SOURCE and leaves the attempt exactly where it
            # was.
            "agent.dispatch.reconcile": self._r_agent_dispatch_reconcile,
            # §M. Which session a conversation is addressing. Two methods and
            # not four: a selection has no close, because selecting another
            # session is what supersedes one, and a conversation that has
            # stopped addressing anything is a state the resolve reports rather
            # than an act somebody performs. `agent.selection.record` is
            # therefore the ONLY write, and it can move a person's attention and
            # cannot move a thread — the two fields that would (§M's forbidden
            # `bound_item_ref` and `session_role`) are refused by the record
            # builder before this table is reached.
            "agent.selection.record": self._r_agent_selection_record,
            "agent.selection.resolve": self._r_agent_selection_resolve,
            # §21. The alert register. `alert.list` and `alert.inspect` are the
            # two reads; `ack` and `snooze` are what a PERSON does about an
            # alert and are therefore CLI-and-IPC only — see `alert.ack` for
            # why a chat message cannot be one of these. `pending` and
            # `record-delivery` are the two halves of the delivery hop, split
            # across the daemon boundary so that the daemon never holds a
            # transport (§2/§18). `alert.pass` exists because a caller testing
            # what the register would say should not have to wait for a tick.
            "alert.list": self._r_alert_list,
            "alert.current": self._r_alert_current,
            "alert.inspect": self._r_alert_inspect,
            "alert.ack": self._r_alert_ack,
            "alert.snooze": self._r_alert_snooze,
            "alert.pending": self._r_alert_pending,
            "alert.record-delivery": self._r_alert_record_delivery,
            "alert.pass": self._r_alert_pass,
            # §Q2. The batch hop. `digest` and `digest-baseline` are reads of
            # the register plus the DELIVERY ledger, and `digest-record` is the
            # write that says what a transport did — the same split as
            # `alert.pending`/`alert.record-delivery`, for the same reason:
            # the daemon decides and records, and it holds no transport.
            "alert.digest": self._r_alert_digest,
            "alert.digest-baseline": self._r_alert_digest_baseline,
            "alert.digest-record": self._r_alert_digest_record,
            # The OTHER half of one pass, and the reason the ledger moves at
            # all on a tick when nothing was sent. `digest-record` says what a
            # transport did with what it carried; this says what the pass
            # declined to carry and why. Without it the cursor only advances on
            # DELIVERED, so every declined row is re-derived as NEW forever —
            # measured 2026-10-04, 827 rows.
            "alert.digest-account": self._r_alert_digest_account,
            # §3. The OTHER rhythm over the same facts. A status broadcast is
            # produced by a clock and a digest by a delta, so the two keep
            # separate ledgers: one ledger would let an hourly status card
            # advance the alert delta window and silently consume the
            # escalations of the same hour. What they share is the shape of the
            # claim — a send is recorded against a VERSION, not a clock.
            "card.record-delivery": self._r_card_record_delivery,
            "card.deliveries": self._r_card_deliveries,
            # The incident half of the same ledger: whether the message that
            # carries one episode may still be patched, which is the question
            # V29 answers with «不重复 NEW» across hours and days.
            "card.incident": self._r_card_incident,
        }

    def handle(self, request: dict, *, peer: dict | None = None) -> dict:
        """Answer one IPC request. Unknown methods are refused by name.

        `peer` is who is on the other end of the connection — pid, uid, gid,
        from `SO_PEERCRED`. It is a parameter rather than something a handler
        fetches, because a handler that can go looking for its caller's
        identity can also go looking at the wrong moment. Only one method
        reads it (`agent.context.attach`), and that method's whole job is to
        bind an observation to the process that made it.
        """
        # These two readings happen BEFORE the `try` below, so a request whose
        # shape this method did not anticipate would not be an error response —
        # it would be an exception leaving `handle` entirely. A body that is
        # valid JSON but not an object (`[1, 2, 3]`, `123`, `"GET /"`) raised
        # `AttributeError: 'list' object has no attribute 'get'` out of
        # `_serve_connection`, out of `serve_forever`, and out of the process;
        # the observation daemon died and the node spent its restart interval
        # blind. A malformed request is a fact about one caller, and the cost
        # of saying so must not be the daemon. Named here rather than left to
        # the `except Exception` below, because "a request must be an object"
        # is the sentence the caller needs and `AttributeError` is not.
        if not isinstance(request, dict):
            return _error(
                None, "BAD_REQUEST",
                f"a request must be a JSON object; this one is a "
                f"{type(request).__name__}")
        method = request.get("method")
        if not isinstance(method, str):
            return _error(
                None, "BAD_REQUEST",
                f"a request's `method` must be a string; this one is a "
                f"{type(method).__name__}")
        params = request.get("params") or {}
        handler = self.handler_map().get(method)

        if handler is None:
            return _error(
                method, "UNKNOWN_METHOD",
                f"{method!r} is not served. Known: {sorted(self._methods())}",
            )
        # Set for the duration of the call and reset after, carried per call
        # rather than on the instance: see `_CALLER_PEER` for why the safety of
        # an identity check must not rest on this loop being single-threaded.
        token = _CALLER_PEER.set(peer)
        try:
            return {"ok": True, "method": method, "result": handler(params)}
        except Exception as exc:  # noqa: BLE001 - the caller needs the reason
            return _error(method, type(exc).__name__, str(exc))
        finally:
            _CALLER_PEER.reset(token)

    def _methods(self) -> tuple[str, ...]:
        return ("device.show", "context.current", "path.resolve", "path.admit",
                "runtime.list", "runtime.status", "runtime.explain",
                "resource.summary", "resource.hygiene", "observation.get",
                "ability.list", "ability.status", "ability.tree",
                "ability.inspect", "ability.search", "ability.resolve",
                "agent.action.admit",
                "runtime.groups", "runtime.group", "runtime.tree",
                "runtime.budget", "process.top", "process.trace",
                "anomaly.list", "anomaly.explain", "container.attribution",
                # §14/§15/§16/§16A/§17.
                "ports.ownership", "desired.actual", "runtime.hygiene",
                "fs.hygiene", "protection.resolve",
                # §6/§7/§13/§18.
                "resource.docker", "tunnel.list", "resource.anomaly.list",
                "node.status",
                # §5/§6. Trusted actor contexts and approvals. `agent.approval.record`
                # is deliberately NOT in the MCP surface — see `mcp/agent_mcp.py`.
                "agent.context.attach", "agent.context.inspect",
                "agent.context.resolve",
                "agent.grant.issue",
                # The trusted-parent grant verification READ (not a lease verb).
                "agent.lease.authority.verify",
                "agent.approval.inspect", "agent.approval.record",
                "agent.control.status",
                # §17. `agent.binding.resolve` and `agent.binding.list` are
                # reads; `record` and `close` are the two writes. The write
                # surface is the daemon's and not the Agent MCP's: a session
                # role is stated by the caller that starts the thread, and an
                # Agent runtime is not a source of a statement about its own
                # purpose.
                "agent.binding.resolve", "agent.binding.list",
                "agent.binding.record", "agent.binding.claim",
                "agent.binding.close",
                # #245 / S2. The durable dispatch attempt: `resolve` reads the
                # record a retry is matched against, `record` writes it. On this
                # socket (not the node MCP) for the same reason the bindings are:
                # the record is the daemon's store, and the router that dispatches
                # reaches it through here.
                "agent.dispatch.resolve", "agent.dispatch.record",
                "agent.dispatch.reconcile",
                # §M. Both, and reachable from a conversation by way of the
                # CLI. This is the one console operation no provider serves:
                # no provider has an opinion about which session a person is
                # looking at, so ServerOS decides and has to be able to record
                # the decision somewhere a restart does not lose. Naming no
                # provider is deliberate — this daemon holds none, and a
                # comment naming one would be the first step toward reaching
                # for it.
                "agent.selection.record", "agent.selection.resolve",
                # §21. All seven, and this list is the DAEMON SOCKET's surface
                # rather than the node MCP's — the same distinction the
                # `agent.approval.record` line above draws. `serverosctl alert
                # ack` is a local command that reaches the register through
                # this socket, so the two writes have to be here; what is
                # deliberately absent is their presence on the node MCP, which
                # declares `alert.list` and `alert.inspect` and nothing else,
                # because that surface holds no state and changes nothing.
                # Whether an acknowledgement may arrive over a chat is a
                # different and unanswered question: the inbound-identity one
                # `mcp/human_control.py` answers UNKNOWN for. Reading this list
                # as the MCP surface is how somebody later "corrects" the code
                # to match a comment and breaks the CLI.
                "alert.list", "alert.inspect", "alert.ack", "alert.snooze",
                "alert.pending", "alert.record-delivery", "alert.pass",
                # §3.4's block, and a READ of the register for the reason
                # `card.incident` is a read of the ledger: the status card's
                # 当前问题 must be the register's answer and not a second
                # computation of it, and the register has one writer. It adds a
                # lookup to the socket and no verb, so nothing here is reachable
                # from a chat message — the node MCP still declares `alert.list`
                # and `alert.inspect` and nothing else.
                "alert.current",
                # §Q2. The batch hop, and it is on this socket for the same
                # reason the seven above are: `serverosctl alert digest` is a
                # local command that reaches the ledger through it. What is
                # absent is again the point — the node MCP still declares
                # `alert.list` and `alert.inspect` and nothing else.
                "alert.digest", "alert.digest-baseline", "alert.digest-record",
                # And its other half. It is a WRITE, on the same ledger, for
                # the same reason — the daemon owns that document, and the
                # surface that decides whether the hour is worth a card is the
                # caller's.
                "alert.digest-account",
                # §3. The card receipt. On this socket for the same reason the
                # three above are: `serverosctl card record-delivery` is a local
                # command and the ledger it appends to lives beside the register
                # the daemon owns. The node MCP still declares `alert.list` and
                # `alert.inspect` and nothing else, so nothing here is reachable
                # from a chat message.
                # `card.incident` is the third: a READ of the same ledger, asked
                # there rather than opened by the caller because this daemon is
                # its only writer. It answers which message carries one episode
                # and whether it may still be patched; it sends and edits
                # nothing, so what it adds to the socket is a lookup.
                "card.record-delivery", "card.deliveries", "card.incident",
                # SYS-02. Two READS and no write, and that is a decision rather
                # than an unfinished edge. Declaring and releasing a desired
                # reference are acts a caller performs on its own behalf, and
                # both are `serverosctl lease declare|release` — which reach
                # this same namespace through `lease_store`. Putting a write on
                # the socket would give the control plane a second producer for
                # one record, and the two could then disagree about who holds
                # what. What the daemon is asked for here is the reading, so
                # that a question asked over the socket and the same question
                # asked by the CLI come from one implementation.
                "environment.leases", "environment.effective")

    # -- the local capability view (§27/§28) --------------------------------

    def _capability_view(self) -> dict:
        """The last-known local projection. Never the authority, always labelled.

        §27 freezes the Capability/Ability Authority as ability-management, and
        §29 puts `ability:*` in that authority's namespace. §24 nevertheless
        lists `ability.list` / `ability.status` on the node surface, and §28
        says the node must keep working — with a projection it is told may be
        stale — while the authority is unreachable.

        Those three are only compatible if the ANSWER says which of the two it
        is. So it does, in a field that cannot be missed: every response names
        `ability-management` as the authority, says this is a local observed
        projection, and carries `registry_status` with an age. A caller can
        then tell "the authority said nothing is installed" from "nobody has
        asked the authority in three days", which are the same empty list and
        completely different facts.

        Deliberately NOT a refusal. The first version of this refused
        `ability.*` outright, which is defensible and wrong: it makes every
        node surface depend on ability-management being reachable, which is
        the coupling §28 exists to remove.
        """
        state = paths_mod.Paths().root("state") / "abilities"
        for name in ("effective.json", "effective.yaml"):
            candidate = state / name
            try:
                raw = candidate.read_text()
            except OSError:
                continue
            try:
                if candidate.suffix == ".json":
                    document = json.loads(raw)
                else:
                    import yaml

                    document = yaml.safe_load(raw)
            except Exception as exc:  # noqa: BLE001 - a corrupt projection is a fact
                return {
                    "authority": "ability-management",
                    "this_is": "a local observed projection, not the authority",
                    "registry_status": "UNREADABLE",
                    "source": str(candidate),
                    "error": f"{type(exc).__name__}: {exc}",
                    "note": (
                        "the projection exists and cannot be parsed. Reporting "
                        "it as empty would say 'nothing is installed', which "
                        "is a different and much more confident claim."
                    ),
                }
            document = document or {}
            age = _age_seconds(document.get("generated_at"), self._now())
            status = str(document.get("registry_status") or "UNKNOWN")
            if status == "FRESH" and age is not None and age > MAX_PROJECTION_AGE_SECONDS:
                # The file says FRESH; the clock disagrees. The clock wins,
                # because `registry_status` is what the writer believed when it
                # wrote, and time has passed since.
                status = "STALE"
            return {
                "authority": "ability-management",
                "this_is": "a local observed projection, not the authority",
                "registry_status": status,
                "generated_at": document.get("generated_at"),
                "age_seconds": age,
                "stale_after_seconds": MAX_PROJECTION_AGE_SECONDS,
                "source_revision": document.get("source_revision"),
                "stale": status != "FRESH",
                "source": str(candidate),
                # Carried through so `ability.list` can return it. Dropping it
                # here and re-deriving it downstream is how a projection ends
                # up reported as FRESH with nothing in it.
                "abilities": document.get("abilities") or [],
                "note": (
                    "readable while ability-management is unreachable, which is "
                    "the point (§28). A stale projection is usable and is not "
                    "an installation authority: do not install from it."
                ),
            }
        return {
            "authority": "ability-management",
            "this_is": "a local observed projection, not the authority",
            "registry_status": "NOT_MATERIALIZED",
            "source": str(state / "effective.json"),
            "stale": True,
            "abilities": None,
            "note": (
                "no local capability projection has been materialized at "
                "state/serveros/abilities/effective.*. This is NOT the same as "
                "'no abilities are installed' — nobody has asked. The node "
                "still works; the answer is that it does not have one."
            ),
        }

    def _r_ability_list(self, params) -> dict:
        # No re-derivation: whatever `_capability_view` established about
        # whether a projection exists is the answer, and filling in `[]` for
        # the two states that mean "there is nothing to read" would collapse
        # them back into "there is nothing installed".
        return self._capability_view()

    def _r_ability_status(self, params) -> dict:
        view = self._capability_view()
        name = params.get("name")
        if name is None:
            return view
        abilities = view.get("abilities") or []
        match = next((a for a in abilities
                      if isinstance(a, dict) and a.get("name") == name), None)
        if match is not None:
            return {**view, "name": name, "installed": True,
                    "placement": "installed", "ability": match}
        # Not in the projection. Before answering that, ask the second source:
        # the two absences are different facts, and the caller cannot tell them
        # apart from the projection alone. `information-management` is the live
        # case — the installed `serveros` skill routes to it, and it is declared
        # on demand rather than installed.
        on_demand = declared_on_demand()
        declared = next((entry for entry in (on_demand.get("entries") or [])
                         if entry.get("name") == name), None)
        if declared is not None:
            return {**view, "on_demand": _on_demand_header(on_demand),
                    "name": name, "installed": False,
                    "placement": "on-demand", "declared": declared,
                    "declared_by": on_demand.get("composition"),
                    "note": (
                        f"{name!r} is NOT observed installed on this device, "
                        f"and the composition {on_demand.get('composition')!r} "
                        "that this revision was built from declares it "
                        "resolvable per node on demand. Those are two different "
                        "facts and neither implies the other: this is not a "
                        "claim that it will load here — `serverosctl ability "
                        "resolve` is what answers that."
                    )}
        # Not in the projection and not declared on demand. Before answering
        # that, ask the WIDER source: "absent from this device" and "absent from
        # the catalogue" are different facts, and a surface that can only say
        # the first reports the second by accident. `ability-management` is the
        # live case — shipped in the composition this node runs, never recorded
        # as an installation here, and therefore invisible to both sources above.
        catalogue = declared_catalogue()
        listed = next((entry for entry in (catalogue.get("entries") or [])
                       if entry.get("name") == name), None)
        if listed is not None:
            return {**view, "on_demand": _on_demand_header(on_demand),
                    "catalogue": _catalogue_header(catalogue),
                    "name": name, "installed": False,
                    "placement": "catalogue", "ability": listed,
                    "note": (
                        f"{name!r} is NOT observed installed on this device and "
                        "is not declared on demand by this revision's "
                        "composition — but the authority's catalogue lists it, "
                        "so it is absent HERE rather than absent. Those are "
                        "different facts: this is not a claim that it will load "
                        "here — `serverosctl ability resolve` answers that."
                    )}
        # Nothing in any source. What that means depends on whether the widest
        # source was readable at all, so the sentence is built from that rather
        # than asserting a catalogue nobody read was empty.
        beyond = ("the materialized catalogue is present and does not list it"
                  if catalogue.get("entries") is not None else
                  "the catalogue was not readable, so nothing here is a claim "
                  "about what the authority holds")
        return {**view, "on_demand": _on_demand_header(on_demand),
                "catalogue": _catalogue_header(catalogue),
                "name": name, "installed": None, "placement": None,
                "note": f"{name!r} is in no source this node can read: not the "
                        f"local projection ({view.get('registry_status')}), not "
                        f"this revision's declared recommendations, and {beyond}. "
                        f"Absence from all of them is still not proof of "
                        f"absence on the authority."}

    # -- §4/§2 · the discovery tree, over the same projection ----------------
    #
    # Both methods below are DERIVATIONS from `_capability_view()` and add no
    # fact of their own. That is deliberate: the projection is the one thing
    # this node knows about its own capabilities, and a second reader of the
    # authority — or a cached tree beside the projection — would be a second
    # answer to a question that already has one.

    # The projection's provenance, carried into every derived answer. Named
    # once so that a derivation cannot accidentally present a tree or a
    # placement without the staleness of the catalogue it came from. §28 is
    # explicit that a stale projection is USABLE and is not an installation
    # authority; both halves of that have to be readable from the answer.
    _PROJECTION_PROVENANCE = (
        "authority", "this_is", "registry_status", "generated_at",
        "age_seconds", "stale", "source", "source_revision",
    )

    def _projection_header(self, view: dict) -> dict:
        return {key: view.get(key) for key in self._PROJECTION_PROVENANCE}

    def _r_ability_tree(self, params) -> dict:
        """§4's tree, as this node currently believes it.

        The node renders the tree from classification metadata alone. §19 keeps
        the physical layout flat, so a tree derived from where files live would
        be a source index wearing the name of a taxonomy — which is why the
        algorithm is handed rows rather than ability records, and why the row
        shape is asserted in `test_ability_projection.py` rather than described
        here.

        `nodes: null` when there is no projection to derive from, and NOT `[]`.
        An empty node list is a claim about ability-management ("its catalogue
        has no classified abilities") made without having read it, and this is
        the same distinction `_capability_view` already draws with
        `abilities: null` versus `abilities: []`.
        """
        kind = params.get("view", "domain")
        if kind not in ("domain", "product"):
            raise ValueError("ability.tree view must be domain or product")
        if kind == "product":
            source = declared_catalogue()
            header = {"authority": "ability-management", "source": source.get("source"),
                      "source_revision": source.get("source_revision"),
                      "generated_at": source.get("generated_at"),
                      "registry_status": source.get("registry_status"),
                      "basis": "product_role.primary"}
            if not source.get("product_vocabulary") or source.get("entries") is None:
                return {**header, "nodes": None, "completion": "NOT_ASSESSED",
                        "note": source.get("product_vocabulary_error") or
                        "Product vocabulary is not materialized. Refresh from an authority revision containing product-tree.yaml."}
            return {**header, **ability_projection_mod.product_tree(
                source["product_vocabulary"], source["entries"],
                responsibility=params.get("responsibility"))}
        if params.get("responsibility") is not None:
            raise ValueError("responsibility requires view=product")
        view = self._capability_view()
        materials = view.get("abilities")
        if materials is None:
            return {
                **self._projection_header(view),
                "schema": ability_projection_mod.TREE_SCHEMA,
                "basis": "domain.primary",
                "nodes": None, "placements": None,
                "unplaced": None, "cross_refs": None,
                "note": (
                    "no local capability projection has been materialized, so "
                    "there is no catalogue to derive a tree from. This is NOT "
                    "an empty tree: nobody has asked ability-management. Run "
                    "`serverosctl ability refresh` to produce the projection "
                    "this is derived from."
                ),
            }
        tree = ability_projection_mod.domain_tree(
            ability_projection_mod.tree_rows(materials))
        unplaced = tree["unplaced"]
        return {
            **self._projection_header(view),
            **tree,
            # Abilities, not records. The projection carries one row per form,
            # and several abilities ship in two, so `len(materials)` would be a
            # larger number under this name and would leave `classified_count`
            # and `unplaced_count` not adding up to it — a reader checking the
            # arithmetic would find a gap the catalogue does not have.
            "ability_count": len(tree["placements"]) + len(unplaced),
            "classified_count": len(tree["placements"]),
            "unplaced_count": len(unplaced),
            "note": (
                "derived from `domain.primary` alone, so an ability appears "
                "exactly once and `taxonomy_refs` are reported separately as "
                "cross-references rather than as a second placement. An "
                "unplaced ability has no `domain.primary`; that is a fact about "
                "the catalogue and not a hole in this answer."
            ),
        }

    def _r_ability_inspect(self, params) -> dict:
        """One ability: what the projection says, and where the tree puts it.

        §11 lists `ability.inspect` on this surface, and this is the method
        that makes that name mean something rather than alias `ability.status`.

        It NEVER reports a resolution status. "Listed in the local projection"
        and "this device can execute it right now" are different questions: the
        first is a materialization fact this node observed, the second is the
        intersection of the projection with the providers this device actually
        has — which is `resolver.py`'s job and is reachable as `serverosctl
        ability resolve`. Answering the second question from the first would
        answer it with a union, and the resolver exists because the two are not
        the same set.
        """
        view = self._capability_view()
        header = self._projection_header(view)
        name = params.get("name")
        materials = view.get("abilities")
        match = next((a for a in (materials or [])
                      if isinstance(a, dict) and a.get("name") == name), None)
        if match is None:
            catalogue = declared_catalogue()
            listed = next((entry for entry in (catalogue.get("entries") or [])
                           if entry.get("name") == name), None)
            # The second source, exactly as in `ability.status`: "not installed
            # here" and "not declared anywhere" are different answers, and the
            # caller that asked about a routing target needs the first one.
            on_demand = declared_on_demand()
            declared = next((entry for entry in (on_demand.get("entries") or [])
                             if entry.get("name") == name), None)
            if declared is not None:
                return {
                    **header, "on_demand": _on_demand_header(on_demand),
                    "catalogue": _catalogue_header(catalogue),
                    "name": name, "installed": None if materials is None else False,
                    "placement": "on-demand", "ability": listed,
                    "declared": declared,
                    "declared_by": on_demand.get("composition"),
                    "tree": None,
                    "source": "ARTIFACT.json of the running revision",
                    "resolution": "not attempted",
                    "resolved_by": "serverosctl ability resolve",
                    "operations_declared": None,
                    "operations_declared_by": (
                        "serverosctl ability resolve --ability <id>#<verb>"),
                    "note": (
                        f"{name!r} is NOT observed installed on this device. "
                        f"The composition {on_demand.get('composition')!r} this "
                        "revision was built from declares it resolvable per "
                        "node on demand, which is why it is not in the "
                        "projection and is still reachable — `resolution: not "
                        "attempted` because whether it loads HERE is "
                        "`serverosctl ability resolve`'s answer and not this "
                        "one's."
                    ),
                }
        if match is None:
            if listed is not None:
                return {
                    **header, "catalogue": _catalogue_header(catalogue),
                    "name": name, "installed": None if materials is None else False,
                    "placement": "catalogue",
                    "ability": listed, "tree": None,
                    "source": "materialized catalogue", "resolution": "not attempted",
                    "resolved_by": "serverosctl ability resolve",
                    "operations_declared": None,
                    "operations_declared_by": (
                        "serverosctl ability resolve --ability <id>#<verb>"),
                    "note": "listed in the pinned catalogue; installation is not observed, and no execution permission is inferred",
                }
        tree = ability_projection_mod.domain_tree(
            ability_projection_mod.tree_rows(materials or []))
        placement = tree["placements"].get(name) if match is not None else None
        return {
            **header, "name": name,
            "installed": True if match is not None else None,
            "placement": "installed" if match is not None else None,
            "ability": match,
            "tree": {
                "placement": placement,
                "cross_refs": tree["cross_refs"].get(name, []),
                "unplaced": match is not None and placement is None,
            } if match is not None else None,
            "source": "local projection",
            "resolution": "not attempted",
            "resolved_by": "serverosctl ability resolve",
            # `null`, never
            # `false`. A resource that kept no provider record cannot say a
            # provider declares no operations.
            "operations_declared": None,
            "operations_declared_by": (
                "serverosctl ability resolve --ability <id>#<verb>"),
            "note": (
                f"{name!r} is not in the local projection and is not declared "
                f"on demand by this revision's composition. Absence from both "
                f"is still not proof of absence on the authority."
                if match is None else
                "this is what the node last OBSERVED as installed, plus where "
                "the tree places it. It is not a statement that this device "
                "can execute it now — `resolution: not attempted` — because "
                "that is the projection intersected with the providers this "
                "device has, and `serverosctl ability resolve` is what "
                "answers it."
            ),
        }

    def _r_ability_search(self, params) -> dict:
        """Which of these fifty-odd names is mine — asked without reading them all.

        This is the tool the fast path is built on, and the reason it exists
        rather than a catalogue read is the reason ServerOS itself exists: an
        Agent that loads every ability record to find the one it needs has
        spent its context answering a question it does not have. A search
        returns ROWS, and how many rows is the caller's to keep small —
        `matched` is reported beside them so a truncated list never reads as a
        complete one.

        `matched` is None rather than 0 in the cases where "no matches" would
        be a claim about a set nobody searched: an empty query, and no readable
        source at all. Everywhere else in this product `0` and `unknown` are
        kept apart, and a search is where collapsing them is easiest and most
        convincing.

        It searches THREE sources and labels every row with which one it came
        from, because they answer different questions and any one of them alone
        was wrong in a way nobody could see:

          * the local projection — what is **installed on this device**;
          * this revision's own `ARTIFACT.json` — what the composition it was
            built from declares **resolvable on demand**, which is a fact a
            node with no checkout can read and the projection cannot carry;
          * the materialized **catalogue** — what the authority lists at all,
            which is the only source that can tell "absent from this device"
            from "absent from the world".

        A row from the second source says `installed: false` and names the
        composition that declared it; a row from the third says `installed:
        false` too and carries no composition, because no build put it there.
        Before the second source existed, `information-management` — which the
        installed `serveros` skill routes to — returned `matched: 0` on a node
        where it is one `ability.resolve` away, and `0` there read as "this
        ability does not exist". Before the third, `ability-management` did the
        same, and it is installed in the very composition this node runs. The
        three counts are reported beside `matched` so they are never summed into
        one number the caller cannot take apart.

        A note on names: `catalogue_size` below predates this third source and
        is the size of the LOCAL PROJECTION, not of the catalogue. It is left
        as it was rather than renamed under a reader that already consumes it;
        the real catalogue's size is `catalogue.count`.
        """
        view = self._capability_view()
        header = self._projection_header(view)
        raw_query = params.get("query")
        query = str(raw_query or "").strip()
        limit = _bounded_int(params.get("limit"), DEFAULT_ABILITY_SEARCH_LIMIT,
                             MAX_ABILITY_SEARCH_LIMIT)
        materials = view.get("abilities")
        on_demand = declared_on_demand()
        catalogue = declared_catalogue()
        # Search is a compact pointer surface; keep the detailed source notes
        # on list/inspect while preserving every source and uncertainty field.
        on_demand_header = dict(_on_demand_header(on_demand), note=(
            "Composition recommendation; installation and readiness are not implied."))
        catalogue_header = dict(_catalogue_header(catalogue), note=(
            "Authority catalogue at source_revision; installation and readiness are not implied."))
        if not query:
            return {
                **header, "on_demand": on_demand_header,
                "catalogue": catalogue_header,
                "query": raw_query, "limit": limit,
                "matched": None, "returned": None, "matches": None,
                "truncated": None, "refused": "EMPTY_QUERY",
                "catalogue_size": None if materials is None else len(materials),
                "note": (
                    "Empty query refused. Use ability.list for the whole projection."
                ),
            }
        if (materials is None and on_demand.get("entries") is None
                and catalogue.get("entries") is None):
            return {
                **header, "on_demand": on_demand_header,
                "catalogue": catalogue_header,
                "query": query, "limit": limit,
                "matched": None, "returned": None, "matches": None,
                "truncated": None, "catalogue_size": None,
                "note": (
                    "No source is readable; matched is null: "
                    "nothing readable is not an empty catalogue."
                ),
            }
        needle = query.lower()
        l2_query = _is_l2_query(needle)
        hits = ([row for row in materials
                 if isinstance(row, dict) and (
                     bool(_link_matched_fields(row, needle)) if l2_query
                     else needle in _ability_searchable(row))]
                if materials is not None else [])
        rows = [_ability_row_compact(row, needle) for row in hits]
        # ARTIFACT recommendations contain names, not product roles. Exact L2
        # queries can find those abilities through their catalogue metadata.
        declared = [entry for entry in (on_demand.get("entries") or [])
                    if not l2_query and needle in _on_demand_searchable(entry)]
        declared_rows = [dict(entry, installed=None if materials is None else False,
                              placement="on-demand",
                              declared_by=on_demand.get("composition"))
                         for entry in declared]
        # Disjoint from the two stronger sources by construction. A name they
        # already matched is not counted again: two sources reporting one
        # ability would make `matched` exceed `returned` with `truncated:
        # false`, an answer no caller can take apart. What is worth reporting
        # here is exactly "the other sources did not have this", and a
        # duplicate row would bury it.
        already_matched = ({row.get("name") for row in hits}
                           | {entry.get("name") for entry in declared})
        listed = [entry for entry in (catalogue.get("entries") or [])
                  if entry.get("name") not in already_matched
                  and (bool(_link_matched_fields(entry, needle)) if l2_query
                       else needle in _catalogue_searchable(entry))]
        listed_rows = [dict(_catalogue_row_compact(entry, needle),
                            installed=None if materials is None else False,
                            placement="catalogue",
                            registered_here=None if materials is None else False)
                       for entry in listed]
        matches = rows + declared_rows + listed_rows
        if l2_query:
            # Stable partition before limiting: the primary owner must not be
            # crowded out by installed cross-references. Preserve source/row
            # order within each group; free-text ordering stays unchanged.
            matches.sort(key=lambda row: "product_role.primary" not in row.get("matched_fields", []))
        matches = matches[:limit]
        return {
            **header, "on_demand": on_demand_header,
            "catalogue": catalogue_header,
            "query": query, "limit": limit,
            "catalogue_size": None if materials is None else len(materials),
            "matched": len(hits) + len(declared) + len(listed),
            "matched_installed": len(hits),
            "matched_on_demand": len(declared),
            "matched_in_catalogue": len(listed),
            "returned": len(matches),
            "truncated": len(hits) + len(declared) + len(listed) > limit,
            "matches": matches,
            "note": (
                "Single L2: exact roles, primary first. Otherwise: case-insensitive "
                "substrings; avoid hints excluded. installed=false: not observed here; "
                "null: projection unreadable. Discovery grants no execution authority."
            ),
        }

    def _r_ability_resolve(self, params) -> dict:
        """Read-only evaluation using the node's pinned catalogue and optional inputs.

        Caller input is an evaluation scenario, not an observed installation or
        a grant. The resolver never executes anything; native tools do not need
        this evaluation before use. Missing documents remain explicitly missing.
        """
        ability = _required(params, "ability")
        if not params.get("runtime"):
            raise ValueError(
                "missing required parameter 'runtime'. This node holds no "
                "declaration of which Agent runtimes are installed; state the caller runtime."
            )
        request = _bounded_resolution_inputs(params.get("request"))
        request.update({
            "ability": ability,
            "runtime": _required(params, "runtime"),
            "device_ref": params.get("device") or self.registry.device_ref,
        })
        catalogue = declared_catalogue()
        revision = catalogue.get("source_revision")
        pinned = (isinstance(revision, str) and len(revision) in (40, 64)
                  and all(c in "0123456789abcdef" for c in revision.lower()))
        if catalogue.get("entries") is not None and pinned:
            request["abilities"] = ability_projection_mod.resolution_catalogue(catalogue["entries"])
        elif not pinned:
            catalogue = {**catalogue, "why": "no full source revision; catalogue not used for resolution"}
        resolver_mod = _ability_resolver()
        try:
            record = resolver_mod.resolve(request)
        except resolver_mod.Rejected as exc:
            raise ValueError(str(exc)) from exc
        return {
            **record,
            "catalogue": _catalogue_header(catalogue),
            "request_inputs_from": ("caller (unverified)" if params.get("request") else None),
            "authorizes_execution": False,
            "asked_as": {
                "device_ref": request["device_ref"],
                "device_ref_from": "caller" if params.get("device") else ANSWERED_BY,
                "runtime": request["runtime"],
                "runtime_from": "caller",
            },
            "note": (
                "catalogue metadata comes from the named source revision; other supplied "
                "inputs are caller claims, not verified node observations. Missing inputs "
                "are not inferred. This read-only assessment grants no execution permission; "
                "existing grants and provider admission still apply."
            ),
        }

    def _r_node_status(self, params) -> dict:
        """The node runtime, describing itself.

        The one method on this surface that is NOT a forward to `view.py`,
        because it is the only question `view.py` cannot answer: `view` reads
        the world from the calling process, and this is a fact about *this*
        process — how long it has been up, how many times it has looked, and
        whether any of those looks failed.

        `answer_is_stale` is the field that makes the rest readable. A status
        query answered by a daemon that stopped ticking ten minutes ago is not
        the same answer as one from a daemon that ticked a second ago, and the
        caller cannot tell them apart from `ticks` alone.
        """
        now = self._now()
        age = (None if self.last_tick_monotonic is None
               else round(now - self.last_tick_monotonic, 1))
        stale_after = (self.interval or 60.0) * self.STALE_TICKS
        return {
            "device_ref": self.registry.device_ref,
            "pid": os.getpid(),
            "started_at": _iso(self.started_at),
            "uptime_seconds": round(now - self.started_at, 1),
            "ticks": self.ticks,
            "tick_interval_seconds": self.interval,
            "last_tick_at": (self.last_tick or {}).get("tick_started_at"),
            "seconds_since_last_tick": age,
            "stale_after_seconds": stale_after,
            # A daemon that has not ticked yet is not stale — it is new. The
            # distinction matters because `--once` never enters this loop, and
            # reporting a fresh one-shot run as stale would be a false alarm
            # in the one place a false alarm costs the most.
            "answer_is_stale": bool(age is not None and age > stale_after),
            "tick_failures": list(self.tick_failures[-5:]),
            "tick_failure_count": len(self.tick_failures),
            "registry_count": len(self.registry),
            "reconcile_enabled": self.enable_reconcile,
            "state_dir": str(self.state_dir),
            "socket": str(self.socket_path),
            # J09: which bytes this process is. Frozen at startup, so it keeps
            # naming the release that was promoted when the process began even
            # after `current` has moved on — which is the fact an operator
            # needs and the symlink cannot supply.
            "identity": self.identity,
            # ...and the same question asked from the other side, live. The two
            # together are the whole answer: one says what this process is, the
            # other says whether the release it belongs to has moved on without
            # it.
            "pointer": _pointer_state(Path(self.identity["root"])),
            "note": (
                "`reconcile_enabled` is this process's interlock, not a "
                "resource's permission: every resource whose `reconcile.mode` "
                "is the default `observe` is filtered out before the interlock "
                "is consulted. A `false` here means this process would not "
                "act; it does not mean anything would otherwise."
            ),
        }

    def _r_device_show(self, params) -> dict:
        host = self.inventory.host_summary()
        return {
            # Read from the registry. This was a hard-coded string, so the
            # daemon answered "which device is this" from a literal while
            # `serverosctl device show` answered it from the registry — two
            # answers to one question, and the daemon's was the one an agent
            # over IPC would believe. They agreed only while nobody renamed
            # the node, which is precisely when an identity is load-bearing.
            "device_ref": self.registry.device_ref,
            "host": host,
            "managed": {"count": len(self.registry),
                        "protected": len(self.registry.protected()),
                        "automatable": len(self.registry.automatable())},
        }

    def _r_context_current(self, params) -> dict:
        """cwd, the Item inferred from it, and the roots — the CLI's answer.

        This served `roots` alone, and the missing half is the one an Agent
        needs first: which Item it is inside decides where its output is
        allowed to go. `serverosctl context current` has answered the whole
        question all along by running `context/resolver.py`, which this process
        already loads; the surface simply did not ask it.

        The caller's cwd is a PARAMETER and deliberately has no default. This
        process is not the caller: inferring the Item from the daemon's own
        working directory would answer "which Item am I in" with a directory
        the caller has never been in, and `cmd_context_current`'s own docstring
        names the consequence — a context guessed from a cwd is how an artifact
        lands in the wrong item. Absent, the Item is UNKNOWN with the reason,
        which is a fact the caller can branch on rather than a plausible lie.
        """
        paths = paths_mod.Paths()
        cwd = params.get("cwd")
        inferred_item = None
        if not cwd:
            note = ("UNKNOWN — the caller did not state a cwd, and this surface "
                    "will not infer one from the daemon's own directory")
        else:
            try:
                item = resolver_mod.infer_item(resolver_mod.Roots.from_env(),
                                               Path(str(cwd)))
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                note = f"UNKNOWN ({type(exc).__name__}: {exc})"
            else:
                inferred_item = item
                note = item or "UNKNOWN (no item inferred from this directory)"
        return {
            "cwd": str(cwd) if cwd else None,
            "inferred_item": inferred_item,
            "inference_note": note,
            "roots": {n: str(paths.root(n, unbound_ok=True)) for n in paths.names},
            "state_runtime": {
                name: str(paths.root("state", unbound_ok=True) / "runtime" / name)
                for name in paths_mod.STATE_SERVEROS_RUNTIME
            },
            "note": (
                "roots come from the root contract and never from cwd; the Item "
                "is inferred from the cwd the CALLER stated and is UNKNOWN when "
                "none was stated. Not guessed, because an artifact written to a "
                "guessed Item is the failure this answer exists to prevent."
            ),
        }

    def _r_path_resolve(self, params) -> dict:
        return paths_mod.Paths().resolve(_required(params, "ref"))

    def _r_path_admit(self, params) -> dict:
        return paths_mod.Paths().admit(_required(params, "path"),
                                       writing=params.get("writing", True))

    def _r_agent_action_admit(self, params) -> dict:
        # `context` is required, not optional-with-a-default. An admission
        # decision that silently ignores the subject it is adjudicating cannot
        # become a context-sensitive one later without every existing caller
        # having been wrong in the meantime; requiring it now means the callers
        # that will need it already send it.
        #
        # §5. The payload is refused if it carries identity. Not ignored —
        # refused by name, with the offending keys listed. A caller that sends
        # `{"actor": "hwz"}` and gets back a decision has learned the wrong
        # lesson about which fields ServerOS reads.
        actor_mod.refuse_if_forged(params)
        action = _required(params, "action")
        base = agent_sessions_mod.admission(action, _required(params, "context"))

        if base["decision"] != "REQUIRE_HUMAN":
            # Read-only and delegation actions are decided by the contract
            # alone. Nothing about who asked changes that, and consulting the
            # store here would make an ordinary observation depend on a
            # control-plane file being readable.
            return {**base, "approval": None}

        # Human-gated. The verdict below is assembled from three separate
        # facts, and every one of them is reported even when the answer is no:
        # whether a trusted context was presented at all, whether it still
        # authorizes, and whether an approval matches. Collapsing them into a
        # bare DENY is what sends a requester to ask for something they
        # already have.
        context_ref = params.get("context_ref")
        if not context_ref:
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": ("no trusted context was presented, so no approval can "
                           "be matched to anyone"),
                "repair": ("obtain a trusted context from a HumanControlProvider "
                           "and present its context_ref"),
            }}

        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            # A well-formed ref ServerOS never minted. Reported as MISS rather
            # than raised, and never substituted: a caller that invented a
            # handle learns that inventing one does not work.
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": resolved["because"],
                "context_ref": context_ref, "context_found": False,
            }}

        record = resolved["context"]
        authority = actor_mod.authorizes(record, _iso(self._now()))
        if not authority["authorizes"]:
            return {**base, "approval": {
                "decision": "APPROVAL_REQUIRED", "class": "human_gated",
                "reason": authority["because"],
                "context_ref": context_ref, "context_found": True,
                "context_freshness": authority["state"],
            }}

        held = self.control_store().for_actor_action(
            actor_ref=record["actor_ref"], action=action)
        verdict = approval_mod.decide(
            approvals=held,
            request={"action": action, "target": params.get("target") or {},
                     "params": params.get("args") or {},
                     "actor_ref": record["actor_ref"]},
            context_ref=context_ref,
            now=_iso(self._now()),
        )
        if verdict["decision"] != "ALLOW":
            return {**base, "approval": verdict, "context_ref": context_ref}

        # Approved. The decision is ALLOW, and the class changes from
        # `human_gated` to `human_approved`: `human_gated` names the rule that
        # applied, and leaving it on an allowed request would make an audit
        # unable to tell a request that was gated from one that is still
        # waiting. The approval is NOT consumed here — `consume` is a write
        # that happens after the action runs, so a caller that fails to
        # perform the action can retry without spending a person's decision.
        return {"action": action, "decision": "ALLOW", "class": "human_approved",
                "reason": verdict["reason"], "context": dict(params.get("context") or {}),
                "approval": verdict, "context_ref": context_ref}

    # -- §5: trusted actor contexts ----------------------------------------

    def control_store(self):
        """The persisted contexts and approvals. Opened on first use."""
        if self._control_store is None:
            self._control_store = agent_control_store_mod.AgentControlStore(
                Path(self.state_dir) / "agent-control" / "store.json",
                now=self._now)
        return self._control_store

    # -- §21: the alert register -------------------------------------------

    # Which detector produced which identity. The name is stored ON the alert
    # row and decides who may close it, so it is a stable name and not the
    # object or the class — see `alerting.evaluate`'s docstring for the bug
    # this prevents: `DESIRED_ACTUAL_MISMATCH` is declared by both detectors,
    # and one detector's silence about a code it never evaluates would close
    # the other's alert one tick after it opened.
    #
    # The names live in `notification-contract.yaml#alerts.detectors` rather
    # than here, and are read through `alerting.detectors()`. Two reasons, and
    # the second is the one that made it a change rather than a preference:
    # the name is durable state and must not be a file path a rename can
    # orphan — and the daemon does not LOAD either detector, it reads their
    # documents through `view`, so a module path here was a label that read
    # like a dependency.
    DECLARATION_DETECTOR = "declaration-anomaly"
    MEASUREMENT_DETECTOR = "measurement-anomaly"

    def alert_store(self):
        """The alert register. Opened on first use, like the control store."""
        if self._alert_store is None:
            self._alert_store = alerting_mod.AlertStore(
                Path(self.state_dir) / "alerts" / "state.json")
        return self._alert_store

    def detections(self, *, measured: dict | None = None) -> list[dict]:
        """Both detection documents, each paired with the detector that made it.

        Measured through this daemon's own observers — `self.inventory` and
        `self.host` — rather than through fresh ones. §28's rule: an answer
        assembled from two observers is two answers, and the one the daemon is
        acting on is the one it took itself.

        `measured` is this tick's observations when the caller already has
        them. The resource layer ages them rather than assuming they are
        current, so passing the tick's own comparison is what makes the alert
        about the comparison the daemon actually made instead of a second
        probe taken while the first is being reported.
        """
        declared = alerting_mod.detectors()
        missing = [name for name in (self.DECLARATION_DETECTOR,
                                     self.MEASUREMENT_DETECTOR)
                   if name not in declared]
        if missing:
            # Raised rather than worked around, and it does not kill the loop:
            # `tick()` records a failed alert pass as a failed alert pass, so
            # the report says the register was not updated and why. Rows this
            # node already wrote carry one of these names, and a name that is
            # no longer declared belongs to nobody — the "only the opener may
            # close it" rule would quietly match nothing, forever.
            raise alerting_mod.AlertError(
                f"{missing} are the detector names this daemon raises alerts "
                f"under, and {alerting_mod.CONTRACT} does not declare them")
        return [
            {"detector": self.DECLARATION_DETECTOR,
             "document": view_mod.anomaly_list(
                 registry=self.registry, inventory=self.inventory,
                 probes_host=self.host, answered_by=ANSWERED_BY)},
            {"detector": self.MEASUREMENT_DETECTOR,
             "document": view_mod.resource_anomaly(
                 inventory=self.inventory, registry_doc=self.registry,
                 measured=measured, probes_host=self.host,
                 answered_by=ANSWERED_BY)},
        ]

    def alert_pass(self, *, measured: dict | None = None) -> dict:
        """§21. One evaluation pass over both detectors. Sends NOTHING.

        The split is §2's, and it is the whole design: this method decides what
        the facts are and records what still has to be said. It cannot deliver,
        because it holds no provider — and it must not be given one. A daemon
        that can send is a daemon that cannot answer when the transport is
        broken, which is the moment its answer is wanted, and it is why
        `_r_agent_control_status` refuses to import a transport either.

        So `pending` comes out of here as an INTENT and the delivery hop
        happens on a surface allowed to know about transports. §12's honesty
        rule is enforced across that boundary: an intent is not a delivery, and
        only `record_delivery` can move `last_notified_at`.
        """
        at = _iso(self._now())
        device_ref = self.registry.device_ref
        store = self.alert_store()
        passes = []
        for entry in self.detections(measured=measured):
            passes.append({"detector": entry["detector"],
                           **alerting_mod.evaluate(
                               store, detection=entry["document"],
                               device_ref=device_ref, at=at,
                               detector=entry["detector"])})
        self.last_alert_pass = {"at": at, "passes": passes}
        return self.last_alert_pass

    def _r_alert_pass(self, params) -> dict:
        """Evaluate now, deliver nothing. The same pass the tick runs."""
        measured = None
        if getattr(self, "last_tick", None):
            measured = self.last_tick.get("observations")
        return self.alert_pass(measured=measured)

    def _r_alert_list(self, params) -> dict:
        """The register as a person scans it, most in need of attention first."""
        at = _iso(self._now())
        return {**alerting_mod.listing(self.alert_store(), at=at),
                "device_ref": self.registry.device_ref,
                "delivery_surface": (
                    "`serverosctl alert sync` performs the delivery. This "
                    "daemon records the intent and cannot send: it holds no "
                    "transport, and the surface that does is named by "
                    "`agent.control.status`.")}

    def _r_alert_current(self, params) -> dict:
        """§3.4. What is wrong NOW — the register, not the delta.

        Asked here rather than opened by the caller for the reason
        `card.incident` is: this daemon is the register's only writer, and a
        second reader that opened the file would be a second answer to «what
        does this node hold», which is the one thing a store's owner exists to
        prevent. It reads, it grades and it returns; nothing here announces,
        acknowledges or resolves anything.
        """
        at = _iso(self._now())
        return digest_mod.current(self.alert_store(), at=at)

    def _r_alert_inspect(self, params) -> dict:
        """One alert in full, with the sentence a person would have been sent."""
        ref = _required(params, "alert_ref")
        at = _iso(self._now())
        store = self.alert_store()
        alert = store.get(ref)
        if alert is None:
            # An answer, not an error: "this ref is not here" is a fact about
            # the register, and the refs it does hold are in its own listing.
            return {"found": False, "alert_ref": ref,
                    "note": ("no alert with this ref is held by this node. "
                             "`alert.list` shows the refs it does hold, and an "
                             "alert ref is derived from code, device and "
                             "subject — so a ref that never existed and one "
                             "whose alert was never opened look the same here.")}
        return {"found": True, "alert": alert,
                "rendered": alerting_mod.detail_for(alert, at=at),
                "recommendation": alerting_mod.recommendation(
                    str(alert.get("severity")))}

    def _r_alert_ack(self, params) -> dict:
        """A person has seen it. §11: this is NOT a resolution."""
        at = _iso(self._now())
        return alerting_mod.acknowledge(
            self.alert_store(), _required(params, "alert_ref"), at=at,
            note=params.get("note"))

    def _r_alert_snooze(self, params) -> dict:
        """Stop repeating it for a while. The observation keeps updating."""
        at = _iso(self._now())
        return alerting_mod.snooze(
            self.alert_store(), _required(params, "alert_ref"), at=at,
            seconds=params.get("seconds"))

    def _r_alert_pending(self, params) -> dict:
        """What still has to be said, and may be said now. Delivery free."""
        at = _iso(self._now())
        pending = alerting_mod.pending_for(self.alert_store(), at=at)
        return {"at": at, "count": len(pending), "pending": pending,
                "note": ("these are INTENTS. Nothing has been sent, and this "
                         "method cannot send. The caller performs the delivery "
                         "and reports the outcome through "
                         "`alert.record-delivery`; until it does, the intent "
                         "stays pending and no `last_notified_at` is set.")}

    def delivery_cursor(self):
        """The delivery ledger, beside the register and opened with it."""
        if self._delivery_cursor is None:
            path = Path(self.state_dir) / "alerts" / "state.json"
            self._delivery_cursor = digest_mod.DeliveryCursor(
                digest_mod.cursor_path(path))
        return self._delivery_cursor

    def _r_alert_digest_baseline(self, params) -> dict:
        """§Q2. Take the delivery baseline, once, and never again.

        A deliberate act and not a side effect of asking for a digest. The
        baseline is the instant this node started keeping a delivery ledger at
        all, and everything already in the register at that instant is
        declared not-news by it. A read that took it implicitly would let the
        answer to "has this always been the backlog" depend on which command
        somebody happened to type first.
        """
        at = _iso(self._now())
        return digest_mod.ensure_baseline(
            self.delivery_cursor(), self.alert_store(), at=at,
            device_ref=self.registry.device_ref)

    def _r_alert_digest(self, params) -> dict:
        """§Q2. What changed, batched, and whether it is worth saying now."""
        at = _iso(self._now())
        cursor = self.delivery_cursor()
        digest = digest_mod.compose(
            self.alert_store(), cursor, at=at,
            device_ref=self.registry.device_ref)
        verdict = digest_mod.decision(digest, cursor, at=at)
        return {**digest, "decision": verdict,
                "delivery_surface": (
                    "`serverosctl alert digest` decides and prints. The surface "
                    "that SENDS is a renderer holding a transport — this "
                    "daemon holds none, and `agent.control.status` names the "
                    "one it does not have."),
                "note": ("`decision.send` is the answer to 'is this worth a "
                         "message'; nothing has been sent and nothing is "
                         "marked announced until `alert.digest-record` says "
                         "so.")}

    def _r_alert_digest_record(self, params) -> dict:
        """§Q2. What the transport did with the digest it was handed."""
        at = _iso(self._now())
        state = _required(params, "state")
        generation = params.get("generation")
        if generation is None:
            raise DaemonError(
                "a delivery is recorded against a GENERATION. Without one the "
                "outcome cannot be attached to the document that was sent, and "
                "a cursor that advanced on an unnamed generation would mark "
                "announced whatever happened to be composed next")
        shown = params.get("shown") or []
        if not isinstance(shown, list):
            raise DaemonError(
                f"`shown` must be the list of rows the card carried, not "
                f"{type(shown).__name__}: the cursor records what a person was "
                f"actually told, and a scalar here would record something else")
        # The frozen version the payload carried, when the caller had one. It
        # is what makes the receipt name a version rather than a clock, and
        # what lets the severity recorded per row be the one that was ON the
        # card instead of the one the register holds by the time the transport
        # answered. Absent is allowed and means an older document.
        frozen = params.get("frozen")
        if frozen is not None and not isinstance(frozen, dict):
            raise DaemonError(
                f"`frozen` must be the frozen block the digest document "
                f"carried, not {type(frozen).__name__}")
        return digest_mod.record(
            self.delivery_cursor(), self.alert_store(), at=at,
            generation=int(generation), state=str(state), shown=shown,
            reason=params.get("reason"), frozen=frozen)

    def _r_alert_digest_account(self, params) -> dict:
        """§Q2. What this pass declined to carry, and the sentence saying why.

        The companion of `alert.digest-record`, and the reason the ledger is
        maintained on a tick where nothing was sent. A digest that is composed
        and declined leaves the register unchanged and the cursor advancing,
        and both halves of that sentence are needed: the register stays the
        truth about the machine, and the cursor stops re-deriving the same rows
        as new on the next tick.

        `owed` is required and has no default, exactly as it has none in
        `alert_digest.account`. The caller is the only party that knows which
        rows are still owed to somebody — an empty list when the decision was
        not to send, the shown keys when a card went out or a send failed and
        will be retried. A default here would be this daemon guessing at the
        caller's own message lifecycle.
        """
        at = _iso(self._now())
        digest = params.get("digest")
        if not isinstance(digest, dict) or not isinstance(digest.get("rows"), list):
            raise DaemonError(
                f"`digest` must be the composed document whose `rows` are being "
                f"accounted for, not {type(digest).__name__}: the rows to "
                f"account for are listed by the document that composed them, "
                f"and a caller supplying its own list would be describing a "
                f"pass this node never ran")
        owed = params.get("owed")
        if owed is None:
            raise DaemonError(
                "`owed` is required and has no default. It names the keys this "
                "pass is still owed to somebody — the rows the card carried, or "
                "would have carried and will be retried. Pass an empty list "
                "when the decision was not to send at all; omitting it would "
                "make this call account for rows that are still pending their "
                "first send")
        if not isinstance(owed, list):
            raise DaemonError(
                f"`owed` must be a list of alert keys or digest rows, not "
                f"{type(owed).__name__}")
        why = str(params.get("why") or "").strip()
        if not why:
            raise DaemonError(
                "`why` is required. Accounting for a row without saying why "
                "nobody was told is the silent drop this call exists to make "
                "impossible: the ledger would record that a finding was dealt "
                "with and lose the only sentence that explained it")
        return digest_mod.account(
            self.delivery_cursor(), at=at, digest=digest, why=why, owed=owed)

    def card_ledger(self):
        """The card receipt ledger, beside the register and the digest cursor."""
        if self._card_ledger is None:
            path = Path(self.state_dir) / "alerts" / "state.json"
            self._card_ledger = card_delivery_mod.CardLedger(
                card_delivery_mod.ledger_path(path))
        return self._card_ledger

    def _r_card_record_delivery(self, params) -> dict:
        """§3. What the transport did with the card version it was handed.

        The frozen block and the card are BOTH required and are checked
        against each other. A receipt's whole value is that its version names
        the payload that went out, so a caller that offers one without the
        other is asking this node to write down a claim it cannot verify.
        """
        at = _iso(self._now())
        kind = _required(params, "kind")
        state = _required(params, "state")
        card = params.get("card")
        frozen = params.get("frozen")
        if not isinstance(card, dict):
            raise DaemonError(
                f"`card` must be the rendered payload that was sent, not "
                f"{type(card).__name__}")
        if not isinstance(frozen, dict):
            raise DaemonError(
                "`frozen` is required: a delivery is recorded against a "
                "VERSION, and a receipt that cannot name one would make a "
                "re-send of one card and a send of the next one read the same")
        if card_delivery_mod.check_kind(kind) != frozen.get("kind"):
            raise DaemonError(
                f"the card version is a {frozen.get('kind')!r} and this "
                f"delivery is filed under {kind!r}")
        card_delivery_mod.verify(frozen, card)
        return self.card_ledger().record(
            kind, at=at, state=str(state), version=str(frozen.get("version")),
            payload_hash=frozen.get("payload_hash"),
            payload_bytes=frozen.get("payload_bytes"),
            idempotency_key=(params.get("idempotency_key")
                             or frozen.get("idempotency_key")),
            # The message this send produced, when the sender has it. Kept
            # rather than dropped: it is what lets a later update of the same
            # card be a patch of that message instead of a second one, and a
            # receipt that loses it is a receipt for a send nobody can reach.
            # Handed on as it arrived: what a message id IS is one rule, and it
            # lives in `card_delivery.check_remote_id`, on the ledger's side of
            # this call rather than here.
            remote_id=params.get("remote_id"),
            # Same rule for the episode: which kind of receipt must carry one
            # and which must not is `card_delivery.record`'s judgement, and a
            # second copy of it here would be the copy that drifts. This node
            # passes on what the sender said the card was about.
            episode=params.get("episode"),
            reason=params.get("reason"))

    def _r_card_deliveries(self, params) -> dict:
        """§3. What has been sent, by version. A read of the ledger."""
        kind = params.get("kind") or None
        if kind is not None:
            card_delivery_mod.check_kind(kind)
        limit = int(params.get("limit") or 10)
        ledger = self.card_ledger()
        rows = ledger.entries(kind)
        return {
            "kind": kind,
            "entries": len(rows),
            "recent": list(reversed(rows))[:max(0, limit)],
            "last": ledger.last(kind),
            "last_delivered": ledger.last_delivered(kind),
            # §7①'s finding, for EVERY kind rather than only the one this call
            # filtered to: «the card did not reach the chat» is the one answer a
            # reader must not have to ask a second question to get, and a
            # payload that carried it only under `--kind status` would make the
            # default dump the view in which a delivery failure is invisible.
            # Four small dictionaries, computed from the rows already read.
            "findings": {name: ledger.delivery_finding(name)
                         for name in card_delivery_mod.KINDS},
            "note": ("A receipt list, not a historian. `last_delivered` is the "
                     "answer to 'what did the last one that arrived carry'; a "
                     "FAILED entry keeps its version because that is what was "
                     "attempted, and an attempt nobody composed a card for "
                     "would have no version at all. `findings` is the other "
                     "question — what happened to the NEWEST attempt — which "
                     "`last_delivered` cannot answer after a failed patch."),
        }

    def _r_card_incident(self, params) -> dict:
        """§29's «同 episode 跨小时、跨天只 patch 一张», answered from the ledger.

        Read-only, and it comes through this node rather than being read by the
        caller for the reason `card.deliveries` does: the ledger has exactly one
        writer and this is it. A caller that opened the file itself would be
        reading a document somebody else may be rewriting, and the answer it
        needed — which message carries THIS episode — is a lookup, not a dump.

        Nothing here sends, edits or remembers: the answer is a judgement about
        a message that already exists, and the caller is the one that acts on it.
        """
        kind = _required(params, "kind")
        episode = _required(params, "episode")
        at = str(params.get("at") or "") or _iso(self._now())
        return card_delivery_mod.incident_verdict(
            self.card_ledger(), kind=kind, episode=episode, at=at)

    def _r_alert_record_delivery(self, params) -> dict:
        """§12. What actually happened when somebody tried to say it."""
        at = _iso(self._now())
        kind = _required(params, "kind")
        state = _required(params, "state")
        if kind not in ("opened", "resolved"):
            raise DaemonError(
                f"{kind!r} is not an alert notification kind; the two are "
                f"'opened' and 'resolved'")
        if state not in ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN"):
            raise DaemonError(
                f"{state!r} is not one of DELIVERED, FAILED, UNSUPPORTED, "
                f"UNKNOWN — an outcome nobody can name is an outcome that will "
                f"be read as a delivery")
        stored = alerting_mod.record_delivery(
            self.alert_store(), alert_ref=_required(params, "alert_ref"),
            kind=kind, at=at, delivered=state == "DELIVERED", state=state,
            reason=params.get("reason"))
        return {"alert_ref": stored["alert_ref"], "kind": kind,
                "state": state, "delivered": state == "DELIVERED",
                "last_notified_at": stored.get("last_notified_at"),
                "pending": stored.get("pending"),
                "delivery_failure": stored.get("delivery_failure")}

    def _r_agent_context_attach(self, params) -> dict:
        """Mint a trusted context from a provider report. §5's only doorway.

        Three refusals stand between an Agent runtime and a context that names
        a human, and this method is where all three are enforced:

          forged payload   the call carried `human` / `actor` / `approved`
          wrong observer   the report names a pid that is not this caller
          no human         the provider reported BLOCKED, so there is nobody

        The middle one is the one that makes this more than a schema check. A
        report is produced by running a probe inside the process tree of the
        turn being identified — that is the whole mechanism by which an
        identity provider decides who somebody is. So the report carries the
        pid that observed it, and
        this method requires that pid to be the process on the other end of
        this connection. An agent cannot present a report observed by somebody
        else's turn, because it cannot change which process it is.
        """
        actor_mod.refuse_if_forged(params)
        report = params.get("report")
        if not isinstance(report, dict):
            raise actor_mod.Rejected("agent.context.attach needs a `report` object "
                                     "from a HumanControlProvider")

        observed_by = report.get("provider_pid")
        peer = _CALLER_PEER.get() or {}
        peer_pid = peer.get("pid")
        if observed_by is None:
            raise actor_mod.Rejected(
                "the provider report does not say which process observed the "
                "actor. Without it ServerOS cannot tell an observation made in "
                "a real turn from one assembled by the caller.")
        if peer_pid is None:
            raise actor_mod.Rejected(
                "this request did not arrive on a socket, so there is no caller "
                "process to bind the observation to")
        if int(observed_by) != int(peer_pid):
            raise actor_mod.Rejected(
                f"the report was observed by pid {observed_by}, and this request "
                f"arrived from pid {peer_pid}. A trusted context binds to the "
                "process that established it; a report carried in from "
                "elsewhere is not evidence about this caller.")

        now = _iso(self._now())
        record = actor_mod.build(report, now=now)
        self.control_store().put_context(record)
        return {
            "context_ref": record["context_ref"],
            "actor_ref": record["actor_ref"],
            "observed_by_pid": int(observed_by),
            "provenance": record["provenance"],
            "provider_status": record["provider_status"],
            "freshness": actor_mod.freshness(record, now),
            "note": ("this ref is the caller's half of the protocol; ServerOS "
                     "resolves the actor from it and never from a field the "
                     "caller supplies"),
        }

    def _r_agent_grant_issue(self, params) -> dict:
        """Only a trusted provider's signed, exact human-approved scope can issue.

        This is the TRUSTED ISSUANCE BOUNDARY for the OS principal: the grant is
        bound to `uid:<n>` derived from the kernel peer of THIS call
        (`SO_PEERCRED`), which is the one identity in the exchange the caller
        cannot write. The signed payload is not consulted for it — the store
        refuses a payload that names one. A call that arrived with no peer is
        stored UNBOUND, and the verify read then refuses it; nothing here
        fills the field in from a request argument or from `getuid()`, because
        that would be this process's principal standing in for the caller's.
        """
        if set(params) != {"evidence"}:
            raise actor_mod.Rejected("agent.grant.issue accepts signed evidence only")
        if self._owner_policy_resolver is None:
            return {"issued": False, "reason": "BLOCKED_OWNER_POLICY",
                    "detail": ("no owner-issued uid->actor->scope policy "
                               "resolver is bound to this daemon; a "
                               "provider-signed delegation alone does not "
                               "authorize a kernel uid, and no grant is minted")}
        try:
            record = self.control_store().issue_grant(
                params["evidence"], now=_iso(self._now()),
                os_principal=agent_control_store_mod.os_principal_of(
                    _CALLER_PEER.get()),
                owner_policy_resolver=self._owner_policy_resolver)
        except agent_control_store_mod.StoreError as exc:
            # A typed refusal, not an error envelope: the idempotent readback
            # and the owner-policy checks must be readable as `issued: false`
            # with the refusal word (e.g. POLICY_DENIED), never as a success.
            text = str(exc)
            code, _, detail = text.partition(":")
            return {"issued": False,
                    "reason": (code.strip() or "INVALID_CONTEXT"),
                    "detail": detail.strip() or text}
        except actor_mod.Rejected as exc:
            return {"issued": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": str(exc)}
        return {"issued": True, "grant": record,
                "basis": "provider-signed-delegation"}

    def _r_agent_lease_authority_verify(self, params) -> dict:
        """May this caller act as this grant's principal, right now? (#237 B3)

        The trusted-parent READ Work Engine #14's `OwnerVerifiedLease` needs, and
        ONLY a read: it mints no grant, acquires no lease and mutates nothing.
        The context is resolved from what ServerOS minted — never from an
        `actor` / `os_user` field on the request — and the OS principal is the
        kernel's `SO_PEERCRED` peer on this socket, never a string.

        Refusals are answers (`{"verified": False, "reason": ...}`). An
        unanswerable question is a refusal, not a default.

        This is a MUTATION-ELIGIBILITY read: a consumer may use a positive
        answer to write a lease. The owner gate is point-in-time, so without a
        real owner-issued atomic policy/epoch commit fence a successful owner
        check is still `BLOCKED_OWNER_COMMIT_SEAM` (`verified: false`), with
        only a read-only point-in-time audit. Legacy #272 reads with no owner
        resolver keep their previous behavior.
        """
        ref = str(params.get("context_ref") or "").strip()
        if not ref:
            return {"verified": False, "reason": "UNTRUSTED_ACTOR",
                    "because": ("no context_ref was presented. The actor behind a "
                                "grant is resolved from a context ServerOS minted, "
                                "and a caller's word is not one")}
        store = self.control_store()
        resolved = actor_mod.resolve(ref, store.contexts())
        if not resolved["found"]:
            return {"verified": False, "reason": "UNTRUSTED_ACTOR",
                    "because": resolved["because"]}
        now_iso = _iso(self._now())
        peer = _CALLER_PEER.get()
        result = agent_control_store_mod.verify_live_grant(
            store.grants(),
            grant_ref=params.get("grant_ref"),
            context=resolved["context"],
            peer=peer,
            action=params.get("action"),
            project=params.get("project"),
            item_ref=params.get("item_ref"),
            expected_epoch=params.get("expected_epoch"),
            now=now_iso)
        if not result.get("verified"):
            # Preserve the #272 refusal word: the grant's own epoch, window,
            # os_principal, revocation and scope are checked there first.
            return result
        # The owner's uid -> actor -> scope binding. A grant with no binding,
        # or a daemon with no owner resolver, is BLOCKED_OWNER_POLICY: the
        # owner-to-uid relation is UNKNOWN and authorizes nothing. A resolver
        # is a read of the owner's fact; it never mints a grant.
        grant = store.get_grant(str(params.get("grant_ref") or "").strip()) or {}
        resolver = self._owner_policy_resolver
        if resolver is None:
            return {"verified": False, "reason": "BLOCKED_OWNER_POLICY",
                    "because": ("no owner-issued uid->actor->scope policy "
                                "resolver is bound to this daemon; the grant "
                                "cannot be tied to this kernel uid")}
        policy_ref = str(grant.get("owner_policy_ref") or "").strip()
        if not policy_ref:
            return {"verified": False, "reason": "BLOCKED_OWNER_POLICY",
                    "because": ("the grant carries no owner-policy binding; "
                                "an unbound uid->actor mapping is UNKNOWN and "
                                "authorizes nothing")}
        try:
            policy = resolver.resolve(
                policy_ref=policy_ref, grant=grant,
                context=resolved["context"], peer=peer,
                project=params.get("project"), item_ref=params.get("item_ref"),
                action=params.get("action"), now=now_iso)
        except Exception as exc:  # noqa: BLE001 - a resolver failure is a refusal
            return {"verified": False, "reason": "BLOCKED_OWNER_POLICY",
                    "because": (f"the owner-policy resolver did not answer "
                                f"({type(exc).__name__})")}
        refusal = owner_policy_mod.check_policy_binding(
            grant=grant, context=resolved["context"], peer=peer,
            project=params.get("project"), item_ref=params.get("item_ref"),
            action=params.get("action"), now=now_iso, policy=policy)
        if refusal is not None:
            return refusal
        # TOCTOU. The grant was read before `resolver.resolve`, and the
        # resolver is a read of another authority: a concurrent
        # `revoke_grant` during it would be invisible to the snapshot above.
        # Re-read the SAME Agent Control ledger and re-run the #272 checks,
        # then re-resolve the owner policy and require an equal snapshot.
        # This closes the observed revoke-during-resolve race; it is still a
        # point-in-time answer, not an atomic commit (see `atomicity` below),
        # and the Work Engine commit boundary must re-verify.
        second = agent_control_store_mod.verify_live_grant(
            store.grants(), grant_ref=params.get("grant_ref"),
            context=resolved["context"], peer=peer,
            action=params.get("action"), project=params.get("project"),
            item_ref=params.get("item_ref"),
            expected_epoch=params.get("expected_epoch"), now=now_iso)
        if not second.get("verified"):
            return {"verified": False, "reason": "BLOCKED_ATOMICITY",
                    "because": ("the grant changed or was revoked between the "
                                "first read and the owner-policy check; the "
                                "earlier verdict is not a current snapshot"),
                    "point_in_time": False}
        try:
            policy_again = resolver.resolve(
                policy_ref=policy_ref, grant=store.get_grant(
                    str(params.get("grant_ref") or "").strip()) or {},
                context=resolved["context"], peer=peer,
                project=params.get("project"), item_ref=params.get("item_ref"),
                action=params.get("action"), now=now_iso)
        except Exception as exc:  # noqa: BLE001 - a failed re-read is a refusal
            return {"verified": False, "reason": "BLOCKED_ATOMICITY",
                    "because": (f"the owner policy re-read failed "
                                f"({type(exc).__name__}); no positive verdict "
                                f"is issued"),
                    "point_in_time": False}
        if policy_again != policy:
            return {"verified": False, "reason": "BLOCKED_ATOMICITY",
                    "because": ("the owner policy changed (epoch, revocation "
                                "or window) during verification; the first "
                                "snapshot is stale"),
                    "point_in_time": False}
        # The owner answer authorizes NOTHING by itself. It is a point-in-time
        # read, and the only consumer that can turn it into a write
        # (`OwnerVerifiedLease.acquire`) does so after this reply. Without an
        # owner-issued atomic policy/epoch commit fence there is no way for
        # this route to know the owner is still current at that write. A
        # `verified: true` here would be consumed as a write grant, so the
        # mutation-eligibility answer is a refusal; the point-in-time audit is
        # informational only and carries no grant.
        return {"verified": False,
                "reason": "BLOCKED_OWNER_COMMIT_SEAM",
                "because": ("no owner-issued atomic policy/epoch commit fence "
                            "exists for the lease write. The owner checks "
                            "passed at this instant, but a revocation after "
                            "this read is invisible to the mutating consumer, "
                            "so a point-in-time read cannot authorize a "
                            "lease"),
                "point_in_time": True,
                "owner_policy": owner_policy_mod.audit(policy_again)}

    def _r_agent_context_inspect(self, params) -> dict:
        """What ServerOS recorded for a presented ref, aged now.

        Read-only, and it takes a REF rather than an actor. There is no method
        here that answers "who is this", because that question has no
        trustworthy answer outside the evidence that produced the ref.
        """
        ref = _required(params, "context_ref")
        resolved = actor_mod.resolve(ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"found": False, "context_ref": ref,
                    "because": resolved["because"]}
        record = resolved["context"]
        now = _iso(self._now())
        return {"found": True, "context_ref": ref,
                "actor_ref": record["actor_ref"],
                "human_control_provider": record["human_control_provider"],
                "transport": record["transport"],
                "observed_at": record["observed_at"],
                "expires_at": record["expires_at"],
                "freshness": actor_mod.freshness(record, now),
                "authorizes": actor_mod.authorizes(record, now)}

    def _r_agent_context_resolve(self, params) -> dict:
        """The stored record itself, for a caller that has to act on it.

        `agent.context.inspect` is the curated view — a person reading it wants
        `freshness` and `authorizes`, already decided. The Agent Control router
        is not a person reading: it hands the record to the resolver, which is
        what does the deciding, and a curated view would make the daemon and
        the resolver two places that decide the same thing.

        Still keyed by a ref and never by an actor. The caller can ask about a
        ref it was given; it cannot ask who somebody is.
        """
        ref = _required(params, "context_ref")
        resolved = actor_mod.resolve(ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"found": False, "context_ref": ref,
                    "because": resolved["because"]}
        return {"found": True, "context_ref": ref, "context": resolved["context"]}

    # -- §17: what a thread is for ------------------------------------------

    def _item_roots(self):
        """Where an Item's state is read from, derived from THIS daemon's state.

        `context/resolver.py#item_state` needs one thing: the machine's
        `serveros.db`. It is not read from the ambient environment, because a
        daemon launched with `--state-dir /tmp/...` would then answer about the
        live machine — the failure this repository has recorded three times
        under a different name (a test reading the real world and reporting it
        green).

        `<state_dir>/../serveros.db` is not an invented layout. `default_state_dir()`
        below composes the same pair the other way round (`$SERVEROS_STATE_DIR`
        + `/runtime`), and the live unit sets exactly that variable, so this
        derivation and the daemon's own state dir cannot come apart. When the
        file is not there, `item_state` answers UNKNOWN, `item_axis` answers
        UNEVALUABLE, and the caller is told which input was missing — which is
        the honest answer for a daemon pointed at a directory that holds no
        Item records.
        """
        return resolver_mod.Roots(
            workspace_root=Path(self.state_dir).parent,
            artifacts_root=Path(self.state_dir).parent,
            state_db=Path(self.state_dir).parent / "serveros.db")

    def _r_agent_binding_resolve(self, params) -> dict:
        """What this thread is for, and what the Item it is for looks like NOW.

        Two reads in one answer, deliberately. A caller that fetched the binding
        and then asked about the Item would be holding two records taken at two
        moments, and the refusal it is about to make joins them — so the join
        happens here, where both came from one call.

        The Item read is a FORWARD to `context/resolver.py#item_state`, the same
        function `serverosctl` calls, and this method classifies nothing: it
        reports the state it read and where it read it. `coordination.py#item_axis`
        turns that into a verdict. A daemon that decided READ_ONLY here would be
        a second place that decides it, and the CLI answer would then differ from
        the socket answer for a reason no caller could see.

        There is NO parameter for the Item. The Item read is the one the binding
        names, and a method that took an Item ref would be a second
        `work.item.get` — the Item authority read again, on the node surface,
        with none of the Item-scoped rules that authority applies. A caller can
        ask about a thread it holds a ref for; it cannot ask about an Item.
        """
        ref = _required(params, "session_ref")
        store = self.control_store()
        record = store.get_binding(ref)
        if record is None:
            return {"found": False, "session_ref": ref,
                    "binding": None, "item": None,
                    "because": ("no session_role was ever recorded for this "
                                "session, so ServerOS does not hold a statement "
                                "about what it is for")}
        bound = str(record.get("bound_item_ref") or "") or None
        item = None
        if bound:
            item = resolver_mod.item_state(self._item_roots(), bound)
            item["item_ref"] = bound
            item["source"] = "context/resolver.py#item_state"
            item["observed_at"] = _iso(self._now())
        return {"found": True, "session_ref": ref, "binding": record,
                "item": item,
                # #237 B1. The CURRENT WRITER of this thread's write scope, the
                # consumer half of the v2 writer-binding. Derived by the kernel's
                # ONE derivation function from this store's OWN binding records —
                # no second store, no copied authority. `None` means no thread
                # claims the scope: a reading, not a failure. The gate refuses a
                # thread that CLAIMS a superseded writer, and refuses a thread
                # that claims nothing while the scope is owned by another; an
                # unowned scope is left ungated so pre-existing threads keep
                # working.
                # The WORK ENGINE LEASE for this thread's Item, read from the
                # `leases` table the writer (work-engine #13) writes. This is the
                # authority the send admission checks the claim against (#237 B3
                # / QA Snapshot E): an expired, revoked or absent lease is `None`
                # and authorizes nothing. `current_writer` below is the
                # binding-store derivation, kept for callers that read it, but it
                # is not the authority — a claim is something a caller can write.
                "write_authority": {
                    "source": "work-engine:leases",
                    "lease": (resolver_mod.live_lease(self._item_roots(), bound)
                              if bound else None)},
                "current_writer": (coordination_mod.current_writer(
                    store.bindings(), bound) if bound else None),
                "item_axis": coordination_mod.item_axis(
                    (item or {}).get("state"), outcome=(item or {}).get("outcome")),
                "provenance": {
                    "store": str(store.path),
                    "state_db": str(self._item_roots().state_db),
                    "state_db_present": self._item_roots().state_db.is_file()}}

    def _r_agent_binding_record(self, params) -> dict:
        """Record what a thread is for. The one write path for §17 bindings.

        The rules are `coordination.binding()`'s, and they are applied HERE as
        well as at the call site that already applied them — not because two
        places decide, but because this is the write point and a write point
        that trusts its caller's shape is a write point that will eventually be
        handed a shape that was assembled somewhere else. The CLI reaches this
        method directly, and it must not be a way to create a binding the MCP
        path would have refused.

        The record is built here rather than accepted. `put_binding` refuses a
        second, different record for a session, so a caller cannot use this to
        change which Item a thread is for; it can only create one.

        #237 B1. This is also the PRODUCER of the write-scope claim: an
        execution binding records which thread owns its Item's write scope, so
        that the admission gate fires on the real path and not only in tests.
        `_writer_claim` states the three rules it applies.
        """
        ref = _required(params, "session_ref")
        store = self.control_store()
        try:
            record = coordination_mod.binding(
                session_role=params.get("session_role"),
                bound_item_ref=params.get("bound_item_ref"),
                work_item=params.get("work_item"),
                device_ref=params.get("device_ref"),
                recorded_at=_iso(self._now()),
                writer=self._writer_claim(store, ref, params))
        except coordination_mod.ThreadRefused as exc:
            return {"recorded": False, "session_ref": ref,
                    "reason": exc.reason, "detail": exc.detail}
        record["session_ref"] = ref
        try:
            stored = store.put_binding(record)
        except agent_control_store_mod.StoreError as exc:
            return {"recorded": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT", "detail": str(exc)}
        bound = str((stored or {}).get("bound_item_ref") or "") or None
        return {"recorded": True, "session_ref": ref, "binding": stored,
                # QA Snapshot G. The caller learns at CREATE time whether this
                # thread can send at all. The send admission reads the same
                # authority, so a create that reports no lease is a thread that
                # will refuse every write — said here rather than discovered on
                # the first `session.send`. The acquisition itself is
                # BLOCKED_PRODUCER_BOUNDARY: nothing in this product can call
                # work-engine's `acquire_lease` yet.
                "write_authority": {
                    "source": "work-engine:leases",
                    "lease": (resolver_mod.live_lease(self._item_roots(), bound)
                              if bound else None)}}

    def _writer_claim(self, store, ref, params):
        """The write-scope claim to record with this binding, or None.

        THE PRODUCER HALF of the v2 writer-binding (#237 B1), and since QA
        Snapshot E it mints from the WORK ENGINE LEASE — the same `leases` row
        `workengine/store.py#acquire_lease` writes — so the binding and the send
        admission name ONE authority instead of two that can disagree.

          * an EXPLICIT claim in `params["writer"]` is REFUSED. This RPC carries
            no trusted actor, and a caller-chosen owner/token is exactly the
            spoof E4 recorded: `agent.binding.record` accepted
            `writer={owner: agent:EVIL, token: 999}` with nothing behind it.
          * a RE-RECORD of the same session reuses the claim already stored, so
            `put_binding`'s idempotency holds.
          * a NEW session claims the scope ONLY when the Work Engine holds a live
            lease for that Item and no thread claims it yet. No lease ⇒ no claim,
            and admission will refuse the send — a scope nobody acquired
            authorizes nobody.
        """
        if params.get("writer") is not None:
            raise coordination_mod.ThreadRefused(
                "UNTRUSTED_ACTOR",
                "agent.binding.record does not accept a caller-supplied writer. "
                "The write scope's owner is the Work Engine lease; a writer this "
                "RPC was told to record would be a claim with no authority "
                "behind it")
        if str(params.get("session_role") or "") != "execution":
            return None
        item = str(params.get("bound_item_ref") or "").strip()
        if not item:
            return None
        existing = store.get_binding(ref)
        if existing is not None:
            return dict(existing.get("writer") or {}) or None
        bindings = store.bindings()
        if coordination_mod.scope_has_writer(bindings, item):
            return None
        lease = resolver_mod.live_lease(self._item_roots(), item)
        if lease is None:
            return None
        claim = coordination_mod.mint_writer(bindings, item, owner=lease["owner"])
        # The token is the LEASE's fence, not a second counter's. A binding that
        # minted its own token could never agree with the lease it is checked
        # against.
        claim["token"] = str(lease["token"])
        return claim

    def _trusted_actor_for_mutation(self, context_ref, store):
        """The STRONG admission verdict for a protected binding mutation.

        `validate` + `freshness == FRESH` is not enough: a fresh context with
        `provider_status != VERIFIED` or `provenance != FACT` passes both, and
        `actor.build` can mint exactly that. `authorizes` is the project's
        admission answer — FRESH **and** provenance FACT **and** provider
        VERIFIED — and it is the only verdict these mutation routes may use.
        """
        ref = str(context_ref or "").strip()
        if not ref:
            return None, ("UNTRUSTED_ACTOR",
                          "a protected mutation needs a trusted context_ref")
        resolved = actor_mod.resolve(ref, store.contexts())
        if not resolved["found"]:
            return None, ("UNTRUSTED_ACTOR", resolved["because"])
        trusted = resolved["context"]
        try:
            actor_mod.validate(trusted)
        except actor_mod.Rejected as exc:
            return None, ("UNTRUSTED_ACTOR", str(exc))
        verdict = actor_mod.authorizes(trusted, _iso(self._now()))
        if not verdict.get("authorizes"):
            return None, (
                "UNTRUSTED_ACTOR",
                (f"the presented context does not authorize a mutation: "
                 f"{verdict.get('because')}"))
        return trusted, None

    def _r_agent_binding_claim(self, params) -> dict:
        """Mint the handoff target's writer claim from the LIVE Work lease.

        #237 T1 / #310 F2. A handoff target that is already bound must become
        the current writer BEFORE its resume/send is admitted: admission fences
        a no-claim thread on an owned scope. This route is the trusted half: the
        caller presents a context_ref and the source ref; the daemon resolves
        the actor, checks the source is the current writer and the target is an
        open binding for the same Item, and mints the claim from the LIVE Work
        Engine lease. The claim's owner/token are the lease's, not a second
        counter's, and never a caller's string.

        If the lease has not moved (same owner and token as the source claim),
        there is no new authority to give the target: NOT_CURRENT_WRITER. If
        there is no live lease at all, the same refusal. The source binding is
        not closed here; the handoff closes it only after the target is current.
        """
        ref = _required(params, "session_ref")
        from_ref = str(params.get("from_ref") or "").strip()
        if not from_ref:
            return {"claimed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT",
                    "detail": ("a claim transfer names the source thread it takes "
                               "over from; none was presented")}
        store = self.control_store()
        trusted, refusal = self._trusted_actor_for_mutation(
            params.get("context_ref"), store)
        if refusal is not None:
            reason, detail = refusal
            return {"claimed": False, "session_ref": ref,
                    "reason": reason, "detail": detail}
        actor = trusted["actor_ref"]

        source = store.get_binding(from_ref)
        target = store.get_binding(ref)
        if source is None or target is None:
            return {"claimed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT",
                    "detail": ("both the source and the target must have recorded "
                               "bindings; a session with none is not a writer")}
        if (str(source.get("state") or "").upper() == "CLOSED"
                or str(target.get("state") or "").upper() == "CLOSED"):
            return {"claimed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT",
                    "detail": "a claim transfer needs two open bindings"}
        item = str(source.get("bound_item_ref") or "")
        if not item or str(target.get("bound_item_ref") or "") != item:
            return {"claimed": False, "session_ref": ref,
                    "reason": "THREAD_ITEM_MISMATCH",
                    "detail": ("the target is not bound to the source's Item; "
                               "write authority cannot cross an Item boundary")}
        if target.get("writer"):
            return {"claimed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT",
                    "detail": ("the target already records a writer; a claim "
                               "cannot overwrite an existing claim")}
        source_writer = source.get("writer")
        if not isinstance(source_writer, dict) or not source_writer.get("owner"):
            return {"claimed": False, "session_ref": ref,
                    "reason": "NOT_CURRENT_WRITER",
                    "detail": ("the source records no writer claim, so there is "
                               "no write authority to take over")}
        current = coordination_mod.current_writer(store.bindings(), item)
        same_source = (isinstance(current, dict)
                       and str(current.get("owner")) == str(source_writer.get("owner"))
                       and str(current.get("epoch")) == str(source_writer.get("epoch"))
                       and str(current.get("token")) == str(source_writer.get("token")))
        if not same_source:
            return {"claimed": False, "session_ref": ref,
                    "reason": "NOT_CURRENT_WRITER",
                    "detail": (f"the source is not the current writer of "
                               f"{item!r}; a handoff claim must take over the "
                               f"scope the source actually holds")}
        lease = resolver_mod.live_lease(self._item_roots(), item)
        if lease is None:
            return {"claimed": False, "session_ref": ref,
                    "reason": "NOT_CURRENT_WRITER",
                    "detail": (f"no live Work lease is recorded for {item!r}; a "
                               f"handoff cannot mint a writer claim without an "
                               f"active grant")}
        if str(lease.get("token")) == str(source_writer.get("token")):
            return {"claimed": False, "session_ref": ref,
                    "reason": "NOT_CURRENT_WRITER",
                    "detail": (f"the Work lease token for {item!r} did not move "
                               f"(still {lease.get('token')!r}); a handoff cannot "
                               f"mint a second claim at the same fence token. The "
                               f"owner must transfer the lease first")}
        writer = {"owner": str(lease["owner"]),
                  "epoch": int(source_writer.get("epoch") or 1),
                  "token": str(lease["token"])}
        now_iso = _iso(self._now())
        try:
            updated = store.claim_writer(
                ref, writer=writer, schema=coordination_mod.SCHEMA_WRITER,
                at=now_iso)
        except agent_control_store_mod.StoreError as exc:
            return {"claimed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT", "detail": str(exc)}
        current_after = coordination_mod.current_writer(store.bindings(), item)
        if (not isinstance(current_after, dict)
                or str(current_after.get("owner")) != str(writer["owner"])
                or str(current_after.get("epoch")) != str(writer["epoch"])
                or str(current_after.get("token")) != str(writer["token"])):
            return {"claimed": False, "session_ref": ref,
                    "reason": "NOT_CURRENT_WRITER",
                    "detail": ("the claim was written but the target is not the "
                               "current writer; write authority did not move"),
                    "binding": updated, "current_writer": current_after}
        return {"claimed": True, "session_ref": ref, "from_ref": from_ref,
                "binding": updated, "current_writer": current_after,
                "writer": writer, "actor": actor,
                "because": ("the target is now the current writer, minted from "
                            "the live Work lease")}

    def _r_agent_binding_close(self, params) -> dict:
        """Close a thread for new work. Never a deletion, and never a rebind.

        Closing is what makes `THREAD_CLOSED_FOR_NEW_WORK` answerable later, so
        the record stays and its Item stays with it. A caller that wanted the
        thread to be about a different Item is told to start one; there is no
        parameter here for a new `bound_item_ref`, and adding one would be the
        rebind that `put_binding` exists to refuse.

        A handoff may also TRANSFER the closed thread's write scope to an
        established successor (`successor_ref`). That is a protected write: it
        requires a trusted, fresh `context_ref`, the source must be the current
        writer, and the target must be an open binding for the SAME Item with no
        claim of its own. The successor's writer is minted HERE, by the kernel's
        one `mint_writer`, and never accepted from the caller. The close and the
        successor's claim are written in one store mutation, so the scope is
        never left with zero authorized writers.
        """
        ref = _required(params, "session_ref")
        successor_ref = str(params.get("successor_ref") or "").strip()
        store = self.control_store()
        successor = None
        actor = None
        source_item = None
        source_claim_expired = None
        target_current = False

        if successor_ref:
            trusted, refusal = self._trusted_actor_for_mutation(
                params.get("context_ref"), store)
            if refusal is not None:
                reason, detail = refusal
                return {"closed": False, "session_ref": ref,
                        "reason": reason, "detail": detail}
            actor = trusted["actor_ref"]

            source = store.get_binding(ref)
            target = store.get_binding(successor_ref)
            if source is None or target is None:
                return {"closed": False, "session_ref": ref,
                        "reason": "INVALID_CONTEXT",
                        "detail": ("both the source and the successor must have "
                                   "recorded bindings; a session with none is not "
                                   "a writer")}
            if (str(source.get("state") or "").upper() == "CLOSED"
                    or str(target.get("state") or "").upper() == "CLOSED"):
                return {"closed": False, "session_ref": ref,
                        "reason": "INVALID_CONTEXT",
                        "detail": ("a write-authority transfer needs two OPEN "
                                   "bindings; a closed thread takes no new work")}
            source_item = str(source.get("bound_item_ref") or "")
            if (not source_item
                    or str(target.get("bound_item_ref") or "") != source_item):
                return {"closed": False, "session_ref": ref,
                        "reason": "THREAD_ITEM_MISMATCH",
                        "detail": (f"the successor is not bound to the source's "
                                   f"Item {source_item!r}; write authority cannot "
                                   f"cross an Item boundary")}
            source_writer = source.get("writer")
            if not isinstance(source_writer, dict) or not source_writer.get("owner"):
                return {"closed": False, "session_ref": ref,
                        "reason": "NOT_CURRENT_WRITER",
                        "detail": ("the source records no writer claim, so there "
                                   "is no write authority to transfer")}
            current = coordination_mod.current_writer(store.bindings(), source_item)
            target_writer = target.get("writer")
            source_current = (isinstance(current, dict)
                              and str(current.get("owner")) == str(source_writer.get("owner"))
                              and str(current.get("epoch")) == str(source_writer.get("epoch"))
                              and str(current.get("token")) == str(source_writer.get("token")))
            target_current = (isinstance(target_writer, dict)
                              and isinstance(current, dict)
                              and str(current.get("owner")) == str(target_writer.get("owner"))
                              and str(current.get("epoch")) == str(target_writer.get("epoch"))
                              and str(current.get("token")) == str(target_writer.get("token")))
            now_iso = _iso(self._now())
            expires = source_writer.get("expires_at")
            if not expires:
                source_claim_expired = False
            else:
                # The claim's expiry and the daemon clock are compared as
                # aware instants, not as ISO text: `12:00+08:00` is 04:00Z, and
                # a string comparison would call it later than `06:00+00:00`.
                now_instant = _instant(now_iso)
                expires_instant = _instant(expires)
                source_claim_expired = (
                    None if now_instant is None or expires_instant is None
                    else expires_instant <= now_instant)
            successor = None
            expected_writer = None
            if target_current:
                # The handoff's claim route already minted the target from the
                # live Work lease. The close only closes the source; re-minting
                # here would be a second claim from the same transfer. The
                # target's claim must still be backed by the live lease.
                lease = resolver_mod.live_lease(self._item_roots(), source_item)
                if (lease is None
                        or str(lease.get("owner")) != str(target_writer.get("owner"))
                        or str(lease.get("token")) != str(target_writer.get("token"))):
                    return {"closed": False, "session_ref": ref,
                            "reason": "NOT_CURRENT_WRITER",
                            "detail": (f"the target's claim is not backed by the "
                                       f"live Work lease for {source_item!r}; a "
                                       f"handoff does not transfer a scope whose "
                                       f"grant is not active")}
                expected_writer = dict(target_writer)
            elif source_current:
                lease = resolver_mod.live_lease(self._item_roots(), source_item)
                if lease is None:
                    return {"closed": False, "session_ref": ref,
                            "reason": "NOT_CURRENT_WRITER",
                            "detail": (f"no live Work lease is recorded for "
                                       f"{source_item!r}; a handoff cannot mint a "
                                       f"successor claim without an active grant")}
                if str(lease.get("token")) == str(source_writer.get("token")):
                    return {"closed": False, "session_ref": ref,
                            "reason": "NOT_CURRENT_WRITER",
                            "detail": (f"the Work lease token for {source_item!r} "
                                       f"did not move (still "
                                       f"{lease.get('token')!r}); a handoff cannot "
                                       f"mint a successor claim at the same fence "
                                       f"token. The owner must transfer the lease "
                                       f"first")}
                writer = {"owner": str(lease["owner"]),
                          "epoch": int(source_writer.get("epoch") or 1),
                          "token": str(lease["token"])}
                successor = dict(target)
                successor["schema"] = coordination_mod.SCHEMA_WRITER
                successor["writer"] = writer
                successor["writer_recorded_at"] = now_iso
                expected_writer = dict(writer)
            else:
                return {"closed": False, "session_ref": ref,
                        "reason": "NOT_CURRENT_WRITER",
                        "detail": (f"neither the source nor the target is the "
                                   f"current writer of {source_item!r}; a handoff "
                                   f"cannot transfer a scope it does not hold")}

        try:
            closed = store.close_binding(
                ref, at=_iso(self._now()), reason=params.get("reason"),
                successor=successor, actor=actor)
        except agent_control_store_mod.StoreError as exc:
            return {"closed": False, "session_ref": ref,
                    "reason": "INVALID_CONTEXT", "detail": str(exc)}

        current_after = (coordination_mod.current_writer(
            store.bindings(), source_item) if successor_ref else None)
        if successor_ref:
            transferred = (isinstance(current_after, dict)
                           and isinstance(expected_writer, dict)
                           and str(current_after.get("owner")) == str(expected_writer.get("owner"))
                           and str(current_after.get("epoch")) == str(expected_writer.get("epoch"))
                           and str(current_after.get("token")) == str(expected_writer.get("token")))
            if not transferred:
                return {"closed": False, "session_ref": ref,
                        "reason": "NOT_CURRENT_WRITER",
                        "detail": ("the source was closed but the successor is not "
                                   "the current writer; write authority did not "
                                   "transfer"),
                        "binding": closed, "current_writer": current_after}
        successor_record = (closed.get("successor")
                            if isinstance(closed, dict) else None)
        if successor_record is None and target_current:
            successor_record = target
        return {"closed": True, "session_ref": ref, "binding": closed,
                "successor": successor_record,
                "current_writer": current_after,
                "source_claim_expired": source_claim_expired}

    def _r_agent_dispatch_resolve(self, params) -> dict:
        """The recorded dispatch attempt for a key, or None (#245).

        The READ half of the durable attempt. A retry is matched against this
        record, so it must be the one the write half put there — one store,
        `agent_control_store.py#dispatch_attempts`, reached by both.

        NOT found is an answer and not an error: it means nothing was ever
        dispatched under this key, which is what a FIRST dispatch looks like.
        """
        key = _required(params, "key")
        attempt = self.control_store().get_dispatch_attempt(key)
        return {"found": attempt is not None, "key": key, "attempt": attempt}

    def _r_agent_dispatch_record(self, params) -> dict:
        """Record one dispatch attempt (#245). The one write path for it.

        Validated HERE as well as at the router, for the reason
        `_r_agent_binding_record` states about its own shape: this is a write
        point, and a write point that trusts its caller is one that will
        eventually be handed a shape assembled somewhere else. The record is
        rebuilt by `dispatch_attempts.attempt_record`, so a phase nobody
        declared is refused here rather than stored as a word.

        IDENTITY IS RESOLVED, NEVER ASSERTED (independent-QA P0-2). The caller
        presents a `context_ref`; this method resolves it against what ServerOS
        actually minted, requires it to be FRESH, and takes the principal from
        THAT record. A record that claims a different principal is refused rather
        than quietly overwritten — a forged principal is worth saying out loud.
        With no resolvable, fresh context the write is refused as
        `UNTRUSTED_ACTOR`: an unattributed phase is exactly the self-asserted
        `SETTLED` the QA found.

        TRANSITIONS. Only `DISPATCH_SIDE_PHASES` may be written here —
        `SUPERSEDED` is a policy decision, not a dispatch outcome. A `SETTLED`
        must carry the provider's answer (a settle with nothing attached is not a
        settle) and must MOVE an existing `CALL_NOT_SENT`/`EFFECT_UNKNOWN`
        attempt; nothing is settled that was never dispatched.
        """
        context_ref = str(params.get("context_ref") or "").strip()
        if not context_ref:
            return {"recorded": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": ("a dispatch attempt is written against the trusted "
                               "context_ref of the call that dispatched it; none "
                               "was presented, so nobody can be attributed")}
        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"recorded": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": resolved["because"]}
        trusted = resolved["context"]
        try:
            actor_mod.validate(trusted)
        except actor_mod.Rejected as exc:
            return {"recorded": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": str(exc)}
        freshness = actor_mod.freshness(trusted, _iso(self._now()))
        if freshness.get("state") != "FRESH":
            return {"recorded": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": (f"the presented context is {freshness.get('state')!r}, "
                               f"which authorizes nothing: {freshness.get('because')}")}
        principal = trusted["actor_ref"]

        incoming = params.get("attempt")
        if not isinstance(incoming, dict):
            return {"recorded": False, "reason": "INVALID_CONTEXT",
                    "detail": ("a dispatch attempt is a record; there is nothing "
                               "here to store")}
        claimed = str(incoming.get("principal") or "").strip()
        if claimed and claimed != principal:
            return {"recorded": False, "reason": "UNTRUSTED_ACTOR",
                    "detail": (f"the record claims principal {claimed!r} and the "
                               f"trusted context is {principal!r}. A principal is "
                               f"taken from the resolved context, never from the "
                               f"payload")}
        try:
            record = dispatch_attempts_mod.attempt_record(
                key=incoming.get("key"), action=incoming.get("action"),
                device_ref=incoming.get("device_ref"), verb=incoming.get("verb"),
                intent_digest=incoming.get("intent_digest"),
                phase=incoming.get("phase"), work_item=incoming.get("work_item"),
                # The resolved actor, not the payload's string.
                principal=principal,
                runtime=incoming.get("runtime"),
                session_ref=incoming.get("session_ref"),
                grant_ref=incoming.get("grant_ref"),
                provider_locator=incoming.get("provider_locator"),
                result=incoming.get("result"), at=incoming.get("recorded_at"))
        except dispatch_attempts_mod.AttemptRefused as exc:
            return {"recorded": False, "reason": "INVALID_CONTEXT",
                    "detail": str(exc)}

        phase = record["phase"]
        if phase not in dispatch_attempts_mod.DISPATCH_SIDE_PHASES:
            return {"recorded": False, "reason": "INVALID_CONTEXT",
                    "detail": (f"{phase!r} is not a phase the dispatch side writes. "
                               f"A supersede is a policy decision about a newer "
                               f"attempt, not an outcome of this one")}
        store = self.control_store()
        if phase == dispatch_attempts_mod.SETTLED:
            if incoming.get("result") is None:
                return {"recorded": False, "reason": "INVALID_CONTEXT",
                        "detail": ("a SETTLED must carry the provider's answer. A "
                                   "settle with nothing attached is not a settle; it "
                                   "is a caller asserting success")}
            existing = store.get_dispatch_attempt(record["key"])
            if (existing is None or existing.get("phase") not in
                    (dispatch_attempts_mod.CALL_NOT_SENT,
                     dispatch_attempts_mod.EFFECT_UNKNOWN)):
                return {"recorded": False, "reason": "INVALID_CONTEXT",
                        "detail": ("a dispatch-side settle must move an existing "
                                   "CALL_NOT_SENT or EFFECT_UNKNOWN attempt; "
                                   "nothing was dispatched under this key")}
        try:
            # `actor` is what makes the store's identity check real: it compares
            # the resolved principal with the one recorded when the attempt was
            # first dispatched, so a different trusted actor cannot mutate it
            # even with the digest (QA D3). The transition table is checked in
            # the same mutation, so a terminal SETTLED cannot be reopened (D4).
            stored = store.put_dispatch_attempt(record, actor=principal)
        except agent_control_store_mod.AttemptRefused as exc:
            return {"recorded": False, "reason": exc.reason_code,
                    "detail": exc.detail}
        except agent_control_store_mod.StoreError as exc:
            return {"recorded": False, "reason": "INVALID_CONTEXT",
                    "detail": str(exc)}
        return {"recorded": True, "key": stored["key"], "attempt": stored}

    @staticmethod
    def _dispatch_answer(key: str, result: dict) -> dict:
        """The Core caller's answer, with no success boolean and no send claim."""
        return {
            "key": str(result.get("key") or key),
            "status": result.get("status"),
            "phase": result.get("phase"),
            "attempt": result.get("attempt"),
            "observation": result.get("observation"),
            "source_called": bool(result.get("source_called")),
            "source_capability": result.get("source_capability"),
            "because": result.get("because"),
            "dispatching": False,
        }

    @staticmethod
    def _dispatch_refusal(key: str, reason: str, attempt, detail: str) -> dict:
        return {
            "key": key,
            "status": reason,
            "phase": (attempt or {}).get("phase"),
            "attempt": attempt,
            "observation": None,
            "source_called": False,
            "source_capability": None,
            "because": detail,
            "dispatching": False,
        }

    def _r_agent_dispatch_reconcile(self, params) -> dict:
        """Ask the owner what happened to an EFFECT_UNKNOWN attempt (#245 / S2).

        READ, plus at most ONE write to the SAME `dispatch_attempts` ledger
        `agent.dispatch.record` writes. It never dispatches, never retries and
        never marks success: the Core caller can only turn an EFFECT_UNKNOWN
        into SETTLED / CALL_NOT_SENT, or leave it exactly where it was.

        AUTHORITY IS RESOLVED, NEVER ASSERTED. The request carries only a
        `context_ref` and a `key`. The principal comes from the resolved, FRESH
        context; the writer comes from the attempt's own session binding; the
        observation source and the grant verifier are daemon-held objects. A
        request that sends `principal`, `writer`, `source`, `capability`,
        `observation`, `grant_ref`, `phase` or `result` is refused rather than
        silently ignored, so a caller cannot believe it supplied an authority.

        PROVIDER CAPABILITY IS THE PRODUCTION ANSWER. R1 #308 and the #299
        capability table measure both current adapters as `RESPONSE_ID_ONLY`
        after a lost send response: BotMux needs the `trigger_id` that arrived
        in the lost response, and the Codex app-server read filters by the
        `turn.id` that arrived in it. With no `CLIENT_TURN_ID` source bound,
        this route refuses `BLOCKED_PROVIDER_SOURCE` and the attempt stays
        EFFECT_UNKNOWN. That is a gate, not a missing feature.
        """
        for forbidden in ("principal", "writer", "current_writer", "source",
                          "source_capability", "capability", "observation",
                          "reconciler", "verifier", "grant", "grant_ref",
                          "grant_verifier", "attempt", "phase", "result",
                          "success"):
            if forbidden in params:
                return self._dispatch_refusal(
                    str(params.get("key") or ""), "INVALID_CONTEXT", None,
                    (f"{forbidden!r} is not an input to this call. The principal "
                     f"is resolved from the trusted context, the writer from the "
                     f"attempt's own binding, and the observation source and "
                     f"grant verifier from the daemon's own registration"))

        context_ref = str(params.get("context_ref") or "").strip()
        if not context_ref:
            return self._dispatch_refusal(
                str(params.get("key") or ""), "UNTRUSTED_ACTOR", None,
                ("a reconciliation is attributed to a trusted context_ref; none "
                 "was presented, so nobody can be shown to be the actor"))
        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            return self._dispatch_refusal(
                str(params.get("key") or ""), "UNTRUSTED_ACTOR", None,
                resolved["because"])
        trusted = resolved["context"]
        try:
            actor_mod.validate(trusted)
        except actor_mod.Rejected as exc:
            return self._dispatch_refusal(
                str(params.get("key") or ""), "UNTRUSTED_ACTOR", None, str(exc))
        freshness = actor_mod.freshness(trusted, _iso(self._now()))
        if freshness.get("state") != "FRESH":
            return self._dispatch_refusal(
                str(params.get("key") or ""), "UNTRUSTED_ACTOR", None,
                (f"the presented context is {freshness.get('state')!r}, which "
                 f"authorizes nothing: {freshness.get('because')}"))
        principal = trusted["actor_ref"]

        key = str(params.get("key") or "").strip()
        if not key:
            return self._dispatch_refusal(
                key, "INVALID_CONTEXT", None,
                ("no key was presented; a reconciliation is about a recorded "
                 "dispatch attempt"))
        store = self.control_store()
        attempt = store.get_dispatch_attempt(key)
        if attempt is None:
            return self._dispatch_refusal(
                key, "NO_ATTEMPT", None,
                ("no attempt is recorded under this key, so there is no unknown "
                 "outcome to reconcile"))

        recorded_principal = str(attempt.get("principal") or "").strip()
        if recorded_principal != principal:
            return self._dispatch_refusal(
                key, "REFUSE_PRINCIPAL", attempt,
                (f"this attempt was dispatched by {recorded_principal!r} and the "
                 f"trusted context is {principal!r}. An observation from a "
                 f"different principal is not evidence about this effect"))

        # A terminal/in-flight attempt already has its answer. Do not read a
        # grant or ask a source for it: a late reply must not overwrite it, and
        # there is nothing here to authorize. This mirrors the driver's own
        # phase gate and keeps the refusal from depending on a grant it does
        # not need.
        phase = str(attempt.get("phase") or "").strip().upper()
        if phase != dispatch_attempts_mod.EFFECT_UNKNOWN:
            return self._dispatch_refusal(
                key, "NOT_UNKNOWN", attempt,
                (f"this attempt is {phase or 'unphased'}, not EFFECT_UNKNOWN. "
                 f"A late reply does not overwrite an attempt that already has "
                 f"its own answer"))

        grant_ref = str(attempt.get("grant_ref") or "").strip()
        if grant_ref:
            verifier = self._dispatch_grant_verifier
            if verifier is None:
                return self._dispatch_refusal(
                    key, "BLOCKED_GRANT_UNKNOWN", attempt,
                    (f"the attempt names grant {grant_ref!r} but no trusted grant "
                     f"verifier is bound to this daemon, so its current epoch and "
                     f"revocation cannot be proved"))
            try:
                # Copies: a verifier may read the record and the context, but
                # it must not be able to mutate the in-memory store through
                # them. The authoritative write is the store's, under its lock.
                verdict = verifier(dict(attempt), context=dict(trusted),
                                   now=_iso(self._now()))
            except Exception as exc:
                return self._dispatch_refusal(
                    key, "BLOCKED_GRANT_UNKNOWN", attempt,
                    (f"the grant verifier did not answer "
                     f"({type(exc).__name__}); an unproved grant authorizes "
                     f"nothing"))
            if not isinstance(verdict, dict) or verdict.get("verified") is not True:
                reason = (str(verdict.get("reason") or "BLOCKED_GRANT_UNKNOWN")
                          if isinstance(verdict, dict) else "BLOCKED_GRANT_UNKNOWN")
                because = (str(verdict.get("because") or "")
                           if isinstance(verdict, dict) else "")
                return self._dispatch_refusal(
                    key, reason, attempt,
                    because or (f"the grant verifier refused {grant_ref!r}"))

        # The writer is the claim recorded on THIS attempt's session binding,
        # never a request field. `current_writer` is the kernel's one
        # derivation from the same binding store the admission gate reads.
        writer = None
        session_ref = str(attempt.get("session_ref") or "").strip()
        if session_ref:
            binding = store.get_binding(session_ref)
            claimed = (binding or {}).get("writer")
            writer = dict(claimed) if isinstance(claimed, dict) else None
        work_item = str(attempt.get("work_item") or "").strip()
        current_writer = (coordination_mod.current_writer(
            store.bindings(), work_item) if work_item else None)

        # The provider-specific part is the observation source OBJECT injected
        # at construction; the Core owns the caller. `serverosd` does NOT
        # resolve a path into an integration directory: the decision lives in
        # `runtime/dispatch_attempts.py`, takes the source as an argument, and
        # records through the same Agent Control ledger. The source is reached
        # only after the trusted context, principal, phase and grant checks
        # above, so a request still cannot supply an authority.
        try:
            result = dispatch_attempts_mod.reconcile_attempt(
                store, key,
                principal=principal,
                observation_source=self._dispatch_observation_source,
                writer=writer,
                current_writer=current_writer,
                now=_iso(self._now()),
            )
        except agent_control_store_mod.AttemptRefused as exc:
            return self._dispatch_refusal(key, exc.reason_code, attempt, exc.detail)
        except agent_control_store_mod.StoreError as exc:
            return self._dispatch_refusal(
                key, "INVALID_CONTEXT", attempt, str(exc))
        except Exception as exc:
            return self._dispatch_refusal(
                key, "SOURCE_UNAVAILABLE", attempt,
                (f"the reconciliation driver raised "
                 f"({type(exc).__name__}); the attempt was not settled"))
        return self._dispatch_answer(key, result)

    def _r_agent_binding_list(self, params) -> dict:
        """Every binding this node holds, by role. Never by actor, never by Item.

        Counts and roles, for a status surface. The records themselves are
        reachable one at a time by a caller that holds a ref — which is the same
        shape as every other read here, and the reason this store is not a
        directory of anything.

        `session_refs` is the one filter, and it keeps that property rather than
        weakening it: a caller that names refs gets back exactly those records
        and cannot enumerate. Without the filter the answer stays counts, so
        there is still no way to dump this store from this surface. §M's console
        needs the second read because it draws an L1 card per session and would
        otherwise make one round trip per card.
        """
        store = self.control_store()
        bindings = store.bindings()
        wanted = params.get("session_refs")
        records = None
        if isinstance(wanted, (list, tuple)):
            records = {str(ref): bindings[str(ref)]
                       for ref in wanted if str(ref) in bindings}
        return {
            "schema": coordination_mod.SCHEMA,
            "count": len(bindings),
            "records": records,
            "by_role": {role: sorted(ref for ref, b in bindings.items()
                                     if b.get("session_role") == role)
                        for role in coordination_mod.ROLES},
            "closed": sorted(ref for ref, b in bindings.items()
                             if str(b.get("state") or "").upper() == "CLOSED"),
            "item_axis_values": list(coordination_mod.ITEM_AXIS),
            "read_only_item_states": list(coordination_mod.READ_ONLY_ITEM_STATES),
        }

    # -- §M: conversation selections ----------------------------------------

    def _r_agent_selection_record(self, params) -> dict:
        """Record which session a conversation is addressing. §M's one write.

        The record is built by `human_console.selection_record` and not here,
        for the reason `_r_agent_binding_record` states about its own shape: a
        write point that assembles the record is a write point that can be made
        to assemble one the rules would have refused. The console's builder is
        the only thing that knows a selection may not carry an Item, so it is
        the only thing that produces one — and `put_selection` refuses a
        smuggled field a second time, independently.

        Unlike a binding, this IS updatable, and the asymmetry is §M's: a
        binding says what a thread is FOR and is immutable; a selection says
        what a person is looking at, and moving it is the operation.
        """
        try:
            record = human_console_mod.selection_record(
                conversation_ref=params.get("conversation_ref"),
                session_ref=params.get("session_ref"),
                device_ref=params.get("device_ref"),
                at=_iso(self._now()))
        except human_console_mod.ConsoleRefused as exc:
            return {"selected": False, "reason": exc.reason, "detail": exc.detail}
        try:
            stored = self.control_store().put_selection(record)
        except agent_control_store_mod.StoreError as exc:
            return {"selected": False,
                    "conversation_ref": record.get("conversation_ref"),
                    "reason": "INVALID_CONTEXT", "detail": str(exc)}
        return {"selected": True, "conversation_ref": record["conversation_ref"],
                "session_ref": record["session_ref"], "selection": stored}

    def _r_agent_selection_resolve(self, params) -> dict:
        """What this conversation is addressing, and whether it is still there.

        `live_session_refs` is supplied by the CALLER, and that is deliberate
        rather than an inconvenience. The daemon holds no provider (§24 — a
        daemon that can reach one cannot tell you the provider is down, which
        is when its answer is wanted), so it cannot know which sessions exist,
        and it will not pretend to: with no list it reports `basis: stored-only`
        and says the staleness comparison was not made. This is the shape
        `memory/handoff.py#pending(records, device_ref)` already uses — the
        caller hands in the scope it has, and the function refuses to resolve
        one for itself.
        """
        conversation_ref = _required(params, "conversation_ref")
        store = self.control_store()
        record = store.get_selection(conversation_ref)
        live = params.get("live_session_refs")
        axis = human_console_mod.selection_axis(
            record, live_refs=list(live) if isinstance(live, (list, tuple)) else None)
        return {"conversation_ref": conversation_ref, "axis": axis,
                "selection": record,
                "basis": ("compared against the live sessions the caller "
                          "supplied" if isinstance(live, (list, tuple))
                          else "stored-only: no live session list was supplied, "
                               "so whether the selected session still exists "
                               "was NOT evaluated"),
                "provenance": {"store": str(store.path)}}

    # -- §6: approvals ------------------------------------------------------

    def _r_agent_approval_inspect(self, params) -> dict:
        """What a person has approved for this context, and whether it covers
        the request in hand.

        The actor comes from the context, never from the caller, and the
        request fingerprint is computed here rather than accepted — a
        caller-supplied fingerprint would let the requester choose what its
        approval was for.
        """
        context_ref = _required(params, "context_ref")
        resolved = actor_mod.resolve(context_ref, self.control_store().contexts())
        if not resolved["found"]:
            return {"found": False, "context_ref": context_ref,
                    "because": resolved["because"]}
        record = resolved["context"]
        action = _required(params, "action")
        target = params.get("target") or {}
        args = params.get("args") or {}
        held = self.control_store().for_actor_action(
            actor_ref=record["actor_ref"], action=action)
        out = {
            "found": True, "context_ref": context_ref, "action": action,
            "actor_ref": record["actor_ref"],
            "request_fingerprint": approval_mod.fingerprint(
                action=action, target=target, params=args),
            "held": [{"approval_ref": a.get("approval_ref"),
                      "state": a.get("state"),
                      "request_fingerprint": a.get("request_fingerprint"),
                      "expires_at": a.get("expires_at")} for a in held],
        }
        if action in approval_mod.human_gated_actions():
            out["verdict"] = approval_mod.decide(
                approvals=held,
                request={"action": action, "target": target, "params": args,
                         "actor_ref": record["actor_ref"]},
                context_ref=context_ref, now=_iso(self._now()))
        else:
            out["verdict"] = {
                "decision": "NOT_GATED", "action": action,
                "reason": (f"{action!r} is not a human-gated action, so there is "
                           "nothing for a person to approve"),
            }
        return out

    def _r_agent_approval_record(self, params) -> dict:
        """Record a human decision. Served, and not reachable by an Agent.

        This method exists, is tested, and is deliberately absent from the
        control-agent MCP surface. That absence is the enforcement, and it is
        the honest limit of what a single-user host can do: agents and the
        operator run as the same uid, so no permission bit separates them. What
        separates them is that the interface an agent speaks has no verb for
        granting approval, and a test asserts the tool list contains none.

        It refuses a provider that cannot carry approvals rather than
        recording a decision it did not receive. `human.approval.transport` is
        UNSUPPORTED on the configured provider — there is no callback surface,
        and §6 says to mark that as a limitation instead of improvising around
        it. Accepting a "decision" here would be exactly the improvised path.
        """
        actor_mod.refuse_if_forged(params)
        action = _required(params, "action")
        if action not in approval_mod.human_gated_actions():
            return {"recorded": False, "action": action,
                    "decision": "NOT_GATED",
                    "reason": (f"{action!r} is not a human-gated action; issuing "
                               "an approval for it would create authority the "
                               "contract never granted")}
        # Always refused, with or without an `inbound_event_ref`. Accepting a
        # reference to an event would be accepting the caller's word that the
        # event happened, which is the same as accepting the caller's word
        # about who approved — and that is what this whole section exists to
        # not do. The field is named in the refusal anyway, so that a client
        # which guessed at the shape learns why the shape does not exist yet.
        supplied = bool(params.get("inbound_event_ref"))
        return {"recorded": False, "action": action,
                "decision": "APPROVAL_TRANSPORT_UNSUPPORTED",
                "reason": (
                    ("an inbound_event_ref was supplied, and no provider on this "
                     "node can produce one: a reference is only evidence if "
                     "ServerOS received the event, and it has no surface that "
                     "could have. " if supplied else
                     "an approval must come from an inbound event the "
                     "HumanControlProvider attributed to a person and bound to "
                     "this request's fingerprint. Nothing supplied one. ")
                    + "The configured provider cannot: measured on 2026-09-14, "
                      "its event, message and approval endpoints all answer 404 "
                      "not_found_yet and its session list is read-only. ServerOS "
                      "reports a transport limitation rather than recording a "
                      "decision it did not receive (§6)."),
                "repair": ("record the decision deliberately as the operator "
                           "with `runtime/approval.py`, or wait for a provider "
                           "that carries approvals"),
                "implementation_status": ("the binding, the window, the "
                                          "consumption and the five verdicts are "
                                          "implemented and tested; what is "
                                          "missing is a transport, not a rule")}

    def _r_agent_control_status(self, params) -> dict:
        """How much of Agent Control is live on THIS node, and what it cannot see.

        Deliberately silent about which transport is in use, and about what that
        transport can do. §18 forbids the daemon from growing into a client of
        anything, and the first draft of this method broke that: it imported
        `mcp/human_control.py` to ask the provider for its own capability list.
        That import is a dependency, and a daemon that depends on a provider is
        a daemon that cannot answer when the provider is broken — which is the
        moment somebody needs the status.

        So the split is: the daemon answers for the control plane it OWNS (the
        store, the gated verbs, the decision it will make). The provider answers
        for the transport, on a surface that is allowed to know about transports
        — `python3 mcp/human_control.py --capabilities`. A caller that wants the
        whole picture asks both, and each answer names which it is.
        """
        store = self.control_store()
        return {
            "authority": "serverosd",
            "this_is": ("the node's own control-plane state. It says nothing "
                        "about which HumanControlProvider is configured or what "
                        "that provider can do; ask the provider surface for that."),
            "store": store.summary(),
            "human_gated_actions": list(approval_mod.human_gated_actions()),
            "identity_source": (
                "a trusted actor context can only be minted by "
                "`agent.context.attach`, from a report produced by a process "
                "the caller is. Nothing on this surface accepts an actor."),
            "approval_issuance": (
                "refused on every served method. Recording a human decision "
                "needs a transport that carries one, and no transport reachable "
                "from here does; see `agent.approval.record` for the reason."),
            "provider_surface": "mcp/human_control.py",
        }

    def _r_runtime_list(self, params) -> dict:
        return {"count": len(self.registry), "resources": [
            {"id": r.id, "kind": r.kind, "owner": r.owner_ref,
             "lifecycle": r.lifecycle, "desired": r.desired,
             "protection": r.protection, "automatable": r.is_automatable}
            for r in self.registry.all()
        ]}

    def _r_runtime_status(self, params) -> dict:
        resource = self.registry.get(_required(params, "id"))
        health = probes_mod.observe(resource, self.host)
        return {"id": resource.id, "health": health.show(),
                "action": health_mod.action_for(resource, health)}

    def _r_runtime_explain(self, params) -> dict:
        resource = self.registry.get(_required(params, "id"))
        return {"id": resource.id, "record": resource.record,
                "automatable": resource.is_automatable,
                "why_not_automatable": resource.why_not_automatable(),
                "budget": self.budget.may_act(resource.id),
                "last_action": self.budget.last_action(resource.id)}

    def _r_resource_summary(self, params) -> dict:
        # Every segment is measured by this process, not derived by whoever
        # reads the answer. The daemon tick is the one place a per-uid figure
        # can be taken without a renderer inventing a second source for it.
        return {"host": self.inventory.host_summary(),
                "disk": self.inventory.disk_status(),
                "advisory": self.inventory.pressure(),
                "cpu_usage": self.inventory.cpu_usage(),
                "user": {"summary": self.inventory.user_summary(),
                         "quota": self.inventory.user_quota()}}

    def _r_resource_hygiene(self, params) -> dict:
        hygiene = hygiene_mod.Hygiene(self.registry, self.inventory)
        return hygiene.report()

    # -- the shared answer layer (§3/§5/§6/§10/§14) --------------------------

    def _r_runtime_groups(self, params) -> dict:
        return view_mod.runtime_groups(answered_by=ANSWERED_BY)

    def _r_runtime_group(self, params) -> dict:
        return view_mod.runtime_group(_required(params, "ref"),
                                      answered_by=ANSWERED_BY)

    def _r_runtime_tree(self, params) -> dict:
        return view_mod.runtime_tree(_required(params, "ref"),
                                     answered_by=ANSWERED_BY)

    def _r_runtime_budget(self, params) -> dict:
        return view_mod.retry_budget(state_dir=self.state_dir, registry=self.registry,
                                     answered_by=ANSWERED_BY)

    def _r_process_top(self, params) -> dict:
        return view_mod.process_top(limit=int(params.get("limit", 10)),
                                    by=params.get("by", "cpu"),
                                    answered_by=ANSWERED_BY)

    def _r_process_trace(self, params) -> dict:
        return view_mod.process_trace(int(_required(params, "pid")),
                                      answered_by=ANSWERED_BY)

    def _r_anomaly_list(self, params) -> dict:
        return view_mod.anomaly_list(registry=self.registry, answered_by=ANSWERED_BY)

    def _r_anomaly_explain(self, params) -> dict:
        return view_mod.anomaly_explain(_required(params, "subject"),
                                        registry=self.registry,
                                        answered_by=ANSWERED_BY)

    def _r_container_attribution(self, params) -> dict:
        return view_mod.container_attribution(registry=self.registry,
                                              answered_by=ANSWERED_BY)

    # -- §14/§15/§16/§16A/§17. Five surfaces, five forwards. ------------------
    #
    # Each one takes NO caller-supplied evidence. The registry is this daemon's
    # own (`self.registry`, the document it refused to start without), the
    # container rows for §17 are read from the same inventory the tick uses, and
    # nothing a caller sends can add an identity, a protection claim or a
    # measurement. Over IPC a caller could otherwise say "container X is
    # protected" or "port 5432 is mine", and the daemon would be relaying a
    # claim as an observation — which is the one thing §14 and §17 forbid.
    #
    # `_required(params, ...)` is deliberately absent too: a method with no
    # required argument cannot be called with one that changes the answer.

    def _r_ports_ownership(self, params) -> dict:
        return view_mod.port_ownership(registry_doc=self.registry,
                                       answered_by=ANSWERED_BY)

    def _r_desired_actual(self, params) -> dict:
        return view_mod.desired_actual(registry_doc=self.registry,
                                       probes_host=self.host,
                                       answered_by=ANSWERED_BY)

    def _r_environment_leases(self, params) -> dict:
        """SYS-02 — what is recorded in `state/serveros/resource-leases/`.

        Forwarded to the shared answer layer, and the namespace is derived from
        THIS daemon's state dir rather than from the ambient environment, for
        the reason `_item_roots` below spells out: a daemon started with
        `--state-dir /tmp/...` must not answer about the live machine. A
        `LeaseError` is not caught here — `handle()` turns it into the
        `{ok: false, error: {...}}` envelope, so a namespace that could not be
        read is a refusal rather than the claim `NO_LEASES_HELD`.
        """
        return view_mod.environment_leases(state_dir=self.state_dir,
                                           answered_by=ANSWERED_BY)

    def _r_environment_effective(self, params) -> dict:
        """SYS-02 — the §6 equation over the recorded references.

        `observed`, `ownership` and `policy` are not gathered here this round,
        and the payload names them under `inputs_not_gathered` rather than
        leaving a reader to infer an absence from a verdict: with ownership
        ungathered every release reads DENY/OWNER_UNKNOWN, and that is a
        statement about the input, not about the resource.
        """
        return view_mod.environment_effective(state_dir=self.state_dir,
                                              answered_by=ANSWERED_BY)

    def _r_runtime_hygiene(self, params) -> dict:
        return view_mod.runtime_hygiene(registry_doc=self.registry,
                                        answered_by=ANSWERED_BY)

    def _r_fs_hygiene(self, params) -> dict:
        return view_mod.fs_hygiene(answered_by=ANSWERED_BY)

    def _r_protection_resolve(self, params) -> dict:
        """§17. Protection over every container this node can see.

        The rows come from the inventory rather than from the request: a
        caller who could supply the container set could supply one that is
        trivially unprotected, and §17's whole finding is about protection
        lapsing when an identity is spelled differently than expected.
        """
        listing = self.inventory.docker_list()
        return view_mod.protection(containers=listing.get("containers") or [],
                                   registry_doc=self.registry,
                                   answered_by=ANSWERED_BY)

    def _r_docker_observation(self, params) -> dict:
        """§6/§7 — the Docker inventory, over the daemon's own reader.

        `space` stays opt-in on this surface too, for the same reason it is
        opt-in in `view.docker_observation`: one `docker system df` measured at
        ~22s against a daemon whose tick has a deadline. A caller that passes
        `space: true` has asked to wait; a caller that does not is not made to.
        """
        return view_mod.docker_observation(
            inventory=self.inventory, space=bool(params.get("space")),
            answered_by=ANSWERED_BY)

    def _r_resource_anomaly(self, params) -> dict:
        """§18/§19. The resource anomaly catalogue, over this daemon's own tick.

        `measured` is the last completed tick's observations, passed rather
        than re-probed. Two reasons and both are correctness: the daemon is
        already acting on that comparison, and a second probe taken here could
        disagree with it while both surfaces describe themselves as current.
        When no tick has completed the argument is left out and the layer below
        probes — which is the honest answer for a daemon that has not looked
        yet, not an empty comparison. That probe goes through `self.host`, the
        same observer every other path on this daemon uses, rather than through
        a fresh `LiveHost` taken while the answer is being assembled.

        The tick's comparison is aged rather than assumed current: the layer
        stamps each signal with the time of the measurement it came from, and
        a tick older than the declared freshness window reports STALE. That is
        the case this handler exists to make reachable — a daemon whose last
        tick was four minutes ago answering "the node is fine" and "the node
        was fine four minutes ago" as the same JSON.
        `space` stays opt-in here for the same reason it is opt-in everywhere
        else: it is one `docker system df`, measured at ~22s, and the two codes
        that need it report as not-evaluated with that cost stated rather than
        as two zeros.
        """
        measured = None
        if getattr(self, "last_tick", None):
            measured = self.last_tick.get("observations")
        return view_mod.resource_anomaly(
            inventory=self.inventory, registry_doc=self.registry,
            measured=measured, probes_host=self.host,
            space=bool(params.get("space")),
            answered_by=ANSWERED_BY)

    def _r_tunnel_observation(self, params) -> dict:
        """§13. The tunnels this node can see, and the ones it cannot.

        No parameters, and the missing half is the answer: `blind_spots` is
        carried into the payload unchanged so a caller reading
        `declared_count: 0` also reads why that number is not a measurement of
        the world.
        """
        return view_mod.tunnel_observation(registry_doc=self.registry,
                                           answered_by=ANSWERED_BY)

    def _r_observation_get(self, params) -> dict:
        if self.last_tick is None:
            return {"tick": None,
                    "note": "no tick has completed yet. An empty answer here "
                            "means no observation, not a healthy node."}
        return {"tick": self.last_tick["tick_started_at"],
                "observations": self.last_tick["observations"],
                "drift": self.last_tick["drift"]}

    # -- the server --------------------------------------------------------

    def serve_forever(self, *, interval: float = 60.0, stop_after: int | None = None) -> None:
        """The loop. Deliberately single-threaded and deadline-driven.

        There is no thread pool and no work queue: the amount of work here is
        bounded by the registry, and concurrency would make "what did it see"
        depend on scheduling. Requests are served between ticks, on the same
        thread, so an IPC answer can never be computed against a half-updated
        observation set.

        Because it is one thread, the time spent inside one connection is time
        the next tick does not happen — measured on this host: a peer that
        delivered a request one byte per 3 seconds stopped the tick for 21.0
        seconds. That is what `REQUEST_READ_SECONDS` bounds, and it bounds it
        for the peer rather than for the daemon: reading is the part of a
        request a caller controls, and `_serve_connection` gives it a total
        that does not restart on every byte.
        """
        # Before `_open_socket`, not after, and the order is the whole point:
        # `_open_socket` begins by unlinking whatever is at the socket path,
        # so a daemon that is going to refuse must refuse before it touches a
        # live daemon's endpoint. A refusal that had already deleted the
        # socket would be the defect it is meant to prevent.
        self._hold_singleton()
        try:
            server = self._open_socket()
        except BaseException:
            # The lock is held from here on, so it has to be given back on
            # every way out of this method — including a bind that never
            # happened. A daemon restarted in a loop would otherwise leak one
            # descriptor per attempt, holding a lock on an endpoint it never
            # managed to serve.
            self._release_singleton()
            raise
        self._log(f"serverosd listening on {self.socket_path}")

        self.interval = interval
        deadline = self._now() + interval
        try:
            while True:
                now = self._now()
                if now >= deadline:
                    try:
                        report = self.tick()
                        self.ticks += 1
                        self._log(
                            f"tick {self.ticks}: {len(report['observations'])} observed, "
                            f"{len(report['drift'])} drift, "
                            f"{len(report['decisions'])} decisions"
                        )
                    except Exception as exc:  # noqa: BLE001 - the loop survives
                        # A failing tick must not kill the daemon: the next
                        # one may succeed, and a control plane that exits on
                        # the first bad measurement is an outage generator.
                        # Recorded as well as logged, because a log line is
                        # not something a status query can read.
                        self.tick_failures.append({
                            "at": _iso(now), "error": type(exc).__name__,
                            "message": str(exc),
                        })
                        self._log(f"tick failed: {type(exc).__name__}: {exc}")
                    deadline = now + interval
                    if stop_after is not None and self.ticks >= stop_after:
                        return
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    if not self._endpoint_live():
                        server = self._rebind(server)
                    continue
                except OSError:
                    continue
                try:
                    self._serve_connection(connection)
                except Exception as exc:  # noqa: BLE001 - the loop survives
                    # The same reason the tick above is wrapped, and the same
                    # sentence: one connection's failure is a fact about that
                    # caller, not a reason for the observation daemon to stop
                    # observing. Without this, an exception anywhere in the
                    # path between `accept` and the close leaves `serve_forever`
                    # entirely — closing the server and releasing the socket
                    # on the way out — and the node spends its whole restart
                    # interval blind. `handle` names the malformed requests it
                    # can name; this is the guarantee that no shape it did not
                    # anticipate can do worse than that.
                    self._log("serverosd dropped a connection: "
                              f"{type(exc).__name__}: {exc}")
                finally:
                    connection.close()
        finally:
            server.close()
            self._release_socket()
            self._release_singleton()

    # Two bounds on the same read, deliberately different numbers because they
    # answer different questions.
    #
    # `PER_READ_SECONDS` is a *per-recv* timeout: it is reset by every byte that
    # arrives, so on its own it bounds nothing about a whole request. A peer
    # that sent one byte every few seconds held this connection — and, because
    # `serve_forever` calls `_serve_connection` inline, the accept loop and the
    # tick with it — for as long as it chose. Measured on this host: a 41-byte
    # request delivered one byte per 3 seconds held the daemon for 21.0 seconds
    # and no tick happened in between.
    #
    # `REQUEST_READ_SECONDS` is that missing bound: one request line, from when
    # this side starts reading it, no matter how the bytes are paced. A peer
    # that stops sending entirely still trips the per-recv bound at ten
    # seconds; a peer that trickles forever now trips this one at thirty and
    # gets told why by name.
    #
    # It bounds the READ, which is the part a peer controls. It deliberately
    # does not bound `handle`: that is this daemon's own work, some of it slow
    # on purpose (a probe that shells out can take tens of seconds), and a
    # deadline there would need a watchdog thread and could tear a half-written
    # observation. That is a different decision from this one, and this one is
    # not enlarged to cover it.
    PER_READ_SECONDS = 10.0
    REQUEST_READ_SECONDS = 30.0

    def _refuse_a_slow_request(self, connection: socket.socket) -> None:
        """Tell a peer that ran out of time which bound it ran out of.

        A silent drop would be cheaper to write and would tell the caller
        nothing it can act on; the two ways a read ends here are different
        facts about the caller, and this one is not the one the peer can
        diagnose by itself.
        """
        try:
            connection.settimeout(self.PER_READ_SECONDS)
            connection.sendall(json.dumps(_error(
                None, "REQUEST_TIMEOUT",
                f"a request must arrive as one line, and this one was still "
                f"arriving after {self.REQUEST_READ_SECONDS:g} seconds. "
                f"A request is one line, written once",
            )).encode() + b"\n")
        except OSError:
            pass

    def _serve_connection(self, connection: socket.socket) -> None:
        connection.settimeout(self.PER_READ_SECONDS)
        buffer = b""
        deadline = time.monotonic() + self.REQUEST_READ_SECONDS
        try:
            while b"\n" not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # A request that is still arriving when its deadline passes.
                    # Not normally reached by falling round the loop — the wait
                    # below is clipped to `remaining`, so a wait that expires
                    # lands in the handler below — but a guard is what keeps
                    # `settimeout` from being handed a value <= 0, which would
                    # make this loop spin instead of refusing.
                    self._refuse_a_slow_request(connection)
                    return
                wait = min(self.PER_READ_SECONDS, remaining)
                connection.settimeout(wait)
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    # Which bound ran out is decided by which wait was shorter,
                    # not by comparing clocks against the deadline — so this is
                    # not a race between two timers. A clipped wait expiring is
                    # the deadline; the full per-recv wait expiring is a peer
                    # that went quiet, and that one is still dropped in silence.
                    if wait < self.PER_READ_SECONDS:
                        self._refuse_a_slow_request(connection)
                    return
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > 1_000_000:
                    connection.sendall(json.dumps(
                        _error(None, "REQUEST_TOO_LARGE", "request exceeded 1 MiB")
                    ).encode() + b"\n")
                    return
        except OSError:
            return
        line, _, _ = buffer.partition(b"\n")
        try:
            request = json.loads(line.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            response = _error(None, "BAD_REQUEST", f"{type(exc).__name__}: {exc}")
        else:
            response = self.handle(request, peer=_peer_credentials(connection))
        try:
            connection.sendall(json.dumps(response, default=str).encode() + b"\n")
        except OSError:
            pass

    def _lock_path(self) -> Path:
        # Beside the socket, not in the state dir: the contended thing is the
        # endpoint. Two daemons with the same --socket and different
        # --state-dir trample each other exactly as badly as two with the same
        # state dir, so keying the lock anywhere else would miss them.
        return self.socket_path.with_name(self.socket_path.name + ".lock")

    def _hold_singleton(self) -> None:
        """Claim this endpoint for the life of this process.

        MEASURED 2026-10-05, in isolation (temporary state dir, temporary
        socket, minimal registry): two daemons started on one socket path both
        came up. In fifteen seconds the socket file's inode changed seven
        times, both processes stayed alive, and both logged the `re-bound`
        line — 46 times and 45 times. "Remove the file at my path and bind my
        own" is right about a *stale* socket and wrong about a live one, and
        the daemon had no way to tell the two apart.

        The kernel can. An exclusive `flock` held for the process's life is
        released when the process exits, however it exits, so a lock file left
        on disk says nothing while a lock that is HELD says everything.

        Deliberately `LOCK_NB` with no retry and no wait: a second daemon is
        not a slow first one. Waiting would let it come up later and look
        healthy, which is the same defect arriving on a delay.
        """
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        path = self._lock_path()
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                # Not contention: the lock itself could not be taken. Saying
                # "another serverosd already holds this" here would be the
                # wrong-but-confident message this whole method exists to
                # replace, so the two cases are told apart by errno.
                os.close(fd)
                raise DaemonError(
                    f"could not lock {path} to claim {self.socket_path}: "
                    f"{exc.strerror or exc}. The lock file has to sit beside "
                    f"the socket, on a filesystem that supports flock(2)."
                ) from exc
            # `flock` only fails here while another process holds it, and the
            # kernel releases it when that process ends — so the holder is
            # alive and what it wrote in the file is a current pid, not a
            # leftover from a daemon that is already gone.
            holder = ""
            try:
                holder = os.pread(fd, 32, 0).decode().strip()
            except OSError:
                pass
            os.close(fd)
            raise DaemonAlreadyRunning(
                f"another serverosd already holds {self.socket_path}"
                + (f" (pid {holder})" if holder else "")
                + f"; {path} is locked. Two daemons on one socket path do not "
                  f"take turns — each removes the other's socket file about "
                  f"once a second and both stay up, and a client lands on "
                  f"whichever won the last race. Stop the one that holds it, "
                  f"or give this one a different --socket."
            )
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._singleton_fd = fd

    def _release_singleton(self) -> None:
        """Give the endpoint back; closing the descriptor releases the lock."""
        if self._singleton_fd is None:
            return
        try:
            os.close(self._singleton_fd)
        except OSError:
            pass
        self._singleton_fd = None

    def _prepare_socket(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            # A stale socket file outlives its daemon. Connecting to it fails
            # with ECONNREFUSED, which reads as "the daemon is down" — and it
            # IS down, so removing the file it left behind is not destructive.
            self.socket_path.unlink()

    def _open_socket(self) -> socket.socket:
        """Bind the listener and remember which socket file is ours.

        The inode is kept because the path alone does not identify a socket:
        a restart can leave two daemons naming the same file in sequence, and
        "remove the file at my path" is then an operation on somebody else's
        socket.
        """
        self._prepare_socket()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(8)
        server.settimeout(1.0)
        try:
            self._socket_ino = self.socket_path.stat().st_ino
        except OSError:
            self._socket_ino = None
        return server

    def _endpoint_live(self) -> bool:
        """Is the file this daemon bound still the file at that path?

        Asked between accepts, on the one-second timeout — a stat, once a
        second, on a daemon measured at 4 file descriptors and ~25 ms of CPU
        per minute-long tick.
        """
        if self._socket_ino is None:
            return True
        try:
            return self.socket_path.stat().st_ino == self._socket_ino
        except OSError:
            return False

    def _rebind(self, server: socket.socket) -> socket.socket:
        """Put the endpoint back after something removed it.

        MEASURED 2026-09-13: this happened for real. A piloted daemon logged
        `listening on /run/user/1006/serveros/serveros.sock`, ticked happily
        for minutes, and held an UNLINKED socket the whole time — `fd 3 ->
        socket:[69123718]` with no file to reach it by. Every client got
        ENOENT. The daemon had not noticed, had no reason to notice, and
        reported itself well while serving nobody.

        A control plane that cannot be reached must not be the last to know.
        Re-binding is safe precisely because we checked: the file at our path
        is gone, so there is nothing of anyone else's to overwrite.
        """
        server.close()
        server = self._open_socket()
        self._log(
            f"serverosd re-bound {self.socket_path}: the socket file was removed "
            f"while this daemon was running, so the previous endpoint was "
            f"unreachable and nothing could have said so"
        )
        return server

    def _release_socket(self) -> None:
        """Remove our socket file on the way out — only if it is still ours.

        During a restart the old daemon's teardown and the new daemon's bind
        meet on the same path. An unconditional unlink there deletes the
        SOCKET OF THE DAEMON THAT JUST STARTED, which is how a freshly
        restarted service comes up healthy and unreachable at the same time.
        The inode is the identity; the path is only an address.
        """
        if self._socket_ino is None:
            return
        try:
            if self.socket_path.stat().st_ino != self._socket_ino:
                return
            self.socket_path.unlink()
        except OSError:
            pass


def _peer_credentials(connection) -> dict | None:
    """Who is on the other end of an AF_UNIX connection, as the kernel tells it.

    `SO_PEERCRED` is filled in by the kernel, not sent by the client, which is
    the entire reason it is usable as evidence: a client cannot state its own
    pid here any more than it can state its own uid. Anyone can write
    `"pid": 1234` into a JSON body.

    Returns None when the platform has no such option (macOS, where the
    equivalent is `getpeereid` and the pid is not available at all). A caller
    that cannot be identified is reported as unidentified rather than guessed
    at — `agent.context.attach` refuses, by name, rather than proceeding on an
    assumption that happens to hold on Linux.
    """
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:  # pragma: no cover - Linux is the deployment target
        return None
    try:
        size = struct.calcsize("3i")
        raw = connection.getsockopt(socket.SOL_SOCKET, option, size)
        pid, uid, gid = struct.unpack("3i", raw)
    except OSError:  # pragma: no cover - the socket went away mid-request
        return None
    return {"pid": pid, "uid": uid, "gid": gid}


def _error(method, code: str, message: str) -> dict:
    return {"ok": False, "method": method, "error": {"code": code, "message": message}}


# What every daemon-served answer says about itself, so that a caller reading a
# socket answer and one reading `serverosctl` output can tell which they have.
ANSWERED_BY = "serverosd"


def _required(params: dict, name: str):
    """A parameter the method cannot answer without, refused by name.

    `params["ref"]` raises a bare `KeyError` which `handle()` turns into an
    error whose message is `'ref'` — a code, not a sentence. The caller of a
    control plane should be told which argument is missing and what a valid one
    looks like.
    """
    if name not in params or params[name] in (None, ""):
        raise ValueError(
            f"missing required parameter {name!r}. This method cannot answer "
            f"without it, and answering with an empty result would read as "
            f"'nothing to report'."
        )
    return params[name]


# How many rows `ability.search` returns when the caller does not say, and the
# most it will return when they do. Both are bounded because the point of the
# search is that the caller does NOT read the whole catalogue: an unbounded
# default would re-create, one call later, the cost the tool exists to remove.
# Truncation is reported (`matched` beside `returned`) rather than silent.
DEFAULT_ABILITY_SEARCH_LIMIT = 8
MAX_ABILITY_SEARCH_LIMIT = 50


def _bounded_int(raw, default: int, ceiling: int) -> int:
    """A caller-supplied limit, clamped. Garbage is the default, not an error.

    A malformed limit is not a reason to refuse a search that could have been
    answered; it is a reason not to honour it. The value actually used is
    reported back in the answer, so a caller that asked for
    `"limit": "many"` can see it got the default.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, ceiling))


RESOLUTION_INPUT_TYPES = {
    "providers": list, "instances": list,
    "device_capabilities": dict, "profile": dict,
    "runtime_record": dict, "runtime_binding": dict,
    "health": dict, "policy": dict, "task_context": dict,
}


def _bounded_resolution_inputs(value) -> dict:
    """Bound optional evaluation data; no files, commands or alternate catalogue.

    This is an ingress limit, not authorization or a second resolver. The
    existing resolver evaluates facts; their caller provenance remains visible.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("request must be an object")
    if set(value) - RESOLUTION_INPUT_TYPES.keys():
        raise ValueError("request contains unsupported fields; catalogue and identity cannot be overridden")
    for key, entry in value.items():
        if entry is not None and not isinstance(entry, RESOLUTION_INPUT_TYPES[key]):
            raise ValueError(f"request.{key} has the wrong type")
        if isinstance(entry, list) and (len(entry) > 32 or
                                       any(not isinstance(row, dict) for row in entry)):
            raise ValueError(f"request.{key} must contain at most 32 objects")
    pending, nodes = [(value, 0)], 0
    while pending:
        entry, depth = pending.pop()
        nodes += 1
        if nodes > 2048 or depth > 12:
            raise ValueError("request exceeds the structure limit")
        if isinstance(entry, dict):
            if len(entry) > 128 or any(not isinstance(k, str) or len(k) > 256 for k in entry):
                raise ValueError("request object exceeds the key limit")
            pending.extend((v, depth + 1) for v in entry.values())
        elif isinstance(entry, list):
            if len(entry) > 128:
                raise ValueError("request array exceeds the item limit")
            pending.extend((v, depth + 1) for v in entry)
        elif isinstance(entry, str):
            if len(entry) > 4096:
                raise ValueError("request string exceeds the length limit")
        elif entry is not None and not isinstance(entry, (int, float, bool)):
            raise ValueError("request must contain JSON values")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 65536:
        raise ValueError("request exceeds 64 KiB")
    for row in value.get("providers") or []:
        if any(not isinstance(row.get(k), str) or not row[k] for k in ("id", "ability", "kind")):
            raise ValueError("request.providers requires id, ability and kind strings")
        for key in ("materialization", "requirements"):
            if row.get(key) is not None and not isinstance(row[key], dict):
                raise ValueError(f"request.providers.{key} must be an object")
        for key in ("operations", "required_runtime_features"):
            if row.get(key) is not None and (not isinstance(row[key], list) or
                                            any(not isinstance(v, str) for v in row[key])):
                raise ValueError(f"request.providers.{key} must be a string list")
        preference = row.get("preference", 100)
        if isinstance(preference, bool) or not isinstance(preference, (int, float)):
            raise ValueError("request.providers.preference must be numeric")
    for row in value.get("instances") or []:
        if any(not isinstance(row.get(k), str) or not row[k] for k in ("provider", "device_ref")):
            raise ValueError("request.instances requires provider and device_ref strings")
    return dict(value)


def _link_search_fields(row: dict) -> dict[str, list[str]]:
    """Positive selection hints only; avoid_hints must never nominate a skill."""
    fields = {}
    for block, keys in (("product_role", ("primary", "subroles", "cross_refs")),
                        ("routing", ("trigger_hints",))):
        values = row.get(block)
        if not isinstance(values, dict):
            continue
        for key in keys:
            value = values.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                fields[f"{block}.{key}"] = [v for v in value if isinstance(v, str)]
    return fields


def _is_l2_query(query: str) -> bool:
    """Recognize the stable product vocabulary G1, A1-A10, B1-B10 only."""
    return query == "g1" or (query[:1] in ("a", "b")
                             and query[1:] in {str(n) for n in range(1, 11)})


def _link_matched_fields(row: dict, query: str) -> list[str]:
    fields = _link_search_fields(row)
    if _is_l2_query(query):
        return [field for field in ("product_role.primary", "product_role.cross_refs")
                if any(query == value.lower() for value in fields.get(field, []))]
    return [field for field, values in fields.items()
            if query and any(query in value.lower() for value in values)]


def _link_search_summary(row: dict, query: str) -> dict:
    role = row.get("product_role")
    if not isinstance(role, dict):
        return {}
    return {"semantic_owner": role.get("primary"),
            "matched_fields": _link_matched_fields(row, query)}


def _ability_searchable(row: dict) -> str:
    """The fields a query is matched against, as one lowercased string.

    `path` and `id` are in it because a caller often knows the repository name
    and not the ability name. The fingerprint is deliberately NOT: a query is
    not a digest, and matching one would return a row for a reason the caller
    cannot see in the result.
    """
    parts = [row.get("name"), row.get("id"), row.get("path"), row.get("type"),
             row.get("version")]
    domain = row.get("domain")
    if isinstance(domain, dict):
        parts.append(domain.get("primary"))
    for key in ("taxonomy_refs", "agents"):
        value = row.get(key)
        if isinstance(value, list):
            parts.extend(v for v in value if isinstance(v, str))
    parts.extend(value for values in _link_search_fields(row).values() for value in values)
    return " ".join(str(p) for p in parts if p).lower()


def _ability_row_compact(row: dict, query: str = "") -> dict:
    """One search hit, small enough that eight of them are still cheap.

    The projection row carries a fingerprint, a revision count and an
    installation count. None of those help a caller decide whether this is the
    ability it wants, and all of them cost context. `ability.inspect` serves
    the full row on purpose — one ability at a time, to a caller that has
    already decided which one it is asking about.
    """
    domain = row.get("domain")
    return {
        "name": row.get("name"),
        "id": row.get("id"),
        "type": row.get("type"),
        "path": row.get("path"),
        "domain": domain.get("primary") if isinstance(domain, dict) else None,
        "taxonomy_refs": row.get("taxonomy_refs") or [],
        "agents": row.get("agents") or [],
        **_link_search_summary(row, query),
    }


def _on_demand_searchable(entry: dict) -> str:
    """What a query is matched against on a declared-on-demand row.

    `name` only. A row from `ARTIFACT.json` carries a name and a status and
    nothing else — there is no path, no type, no domain there, because the
    build never read the ability record to write one. Matching against fields
    that do not exist would make every declared row match the empty string, and
    an empty needle is the one query this surface refuses, so the failure would
    land as "your query matched something you cannot see the reason for".
    """
    return str(entry.get("name") or "").lower()


def _on_demand_header(record: dict) -> dict:
    """The declared-on-demand source, small enough to sit in every answer.

    `count: null` when the declaration could not be read, for the same reason
    `matched` is null there: `0` would say "this composition recommends
    nothing", which is a claim about a build nobody read.
    """
    entries = record.get("entries")
    return {
        "source": record.get("source"),
        "present": record.get("present"),
        "why": record.get("why"),
        "composition": record.get("composition"),
        "artifact": record.get("artifact"),
        "count": None if entries is None else len(entries),
        "note": (
            "this revision's own ARTIFACT.json: what its composition declared "
            "resolvable per node rather than bundled. These are NOT installed "
            "here — `installed: false` is the whole point of the field — and "
            "they are not a promise: `serverosctl ability resolve` says whether "
            "this node can act on one now."
        ),
    }


def _catalogue_searchable(entry: dict) -> str:
    """What a query is matched against on a catalogue row.

    The same classification fields `_ability_searchable` uses, minus `version`
    and `agents`, which a catalogue entry does not carry — it is the manifest's
    own classification metadata and nothing else. Kept a separate function
    rather than folded into the other one so the two row shapes can diverge
    only deliberately: a catalogue row that silently began matching a field it
    does not have would be a match whose reason is invisible in the result.
    """
    parts = [entry.get("name"), entry.get("id"), entry.get("path"),
             entry.get("type")]
    domain = entry.get("domain")
    if isinstance(domain, dict):
        parts.append(domain.get("primary"))
    refs = entry.get("taxonomy_refs")
    if isinstance(refs, list):
        parts.extend(v for v in refs if isinstance(v, str))
    parts.extend(value for values in _link_search_fields(entry).values() for value in values)
    return " ".join(str(p) for p in parts if p).lower()


def _catalogue_row_compact(entry: dict, query: str = "") -> dict:
    """One catalogue hit. The classification fields, and no device in it.

    Deliberately NOT `_ability_row_compact`: that one emits `agents: []` for a
    row that does not carry agents, and an empty list there would read as "no
    agent has this" — a claim about installations this source never made. The
    catalogue knows where an ability sits and what it is; it does not know who
    has it, and the row says so by not carrying the field at all.
    """
    domain = entry.get("domain")
    return {
        "name": entry.get("name"),
        "id": entry.get("id"),
        "type": entry.get("type"),
        "path": entry.get("path"),
        "domain": domain.get("primary") if isinstance(domain, dict) else None,
        "taxonomy_refs": entry.get("taxonomy_refs") or [],
        **_link_search_summary(entry, query),
    }


def _catalogue_header(record: dict) -> dict:
    """The wider-catalogue source, small enough to sit in every answer.

    `count: null` when the catalogue could not be read, for the reason every
    count on this surface is null when its source is unread: `0` would say "the
    authority's catalogue is empty", which is a claim about a document nobody
    read and the most confident available way to be wrong.
    """
    entries = record.get("entries")
    return {
        "source": record.get("source"),
        "present": record.get("present"),
        "why": record.get("why"),
        "registry_status": record.get("registry_status"),
        "source_revision": record.get("source_revision"),
        "count": None if entries is None else len(entries),
        "note": (
            "the authority's catalogue at a pinned revision, as this node last "
            "materialized it. An entry here is NOT installed and NOT "
            "recommended by this composition — it is the authority's own "
            "statement that the ability exists, which is the one thing neither "
            "of the other two sources can say. `serverosctl ability resolve` "
            "still decides whether this node can act on one now."
        ),
    }


MAX_PROJECTION_AGE_SECONDS = 24 * 3600


def _age_seconds(generated_at, now: float) -> int | None:
    """How old a projection is, or None if it never said when it was made.

    `None` rather than 0: an undated projection is not a fresh one, and a
    missing timestamp defaulting to "just now" is how a three-day-old
    capability list gets read as current.
    """
    if not generated_at:
        return None
    import datetime

    try:
        stamp = datetime.datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return int(now - stamp.timestamp())


def _iso(epoch: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).isoformat()


def _instant(value):
    """A zoned instant, or None.

    The same rule `context/actor.py::_parse` and #307 state: an unreadable or
    NAIVE timestamp is not an instant. Comparing two ISO strings would make
    `12:00+08:00` (04:00Z) sort after `04:00+00:00`, which is the lexicographic
    trap this helper exists to remove from the handoff's expiry evidence.
    """
    import datetime
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    # Rename, so a reader never sees a half-written observation. The daemon
    # is the only writer here and the MCP adapter reads these files, so a
    # torn read is a real possibility on a machine this loaded.
    tmp.replace(path)


def default_socket_path() -> Path:
    """§22: a Unix socket in the runtime directory. Never TCP, never public."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime_dir) / "serveros/serveros.sock"


def default_state_dir() -> Path:
    """The state root's `runtime/` directory.

    The state root comes from `registry.state_root` and is not recomposed here.
    This function used to build the same pair itself, which is how the daemon
    and the verbs came to disagree about where the control store is: the
    daemon honoured `SERVEROS_STATE_DIR` and `cli._agent_control_store_path`
    did not, so a node whose service file exported it had two control stores.
    `registry.state_root` is the one producer of that answer now.
    """
    return registry_mod.state_root() / "runtime"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="serverosd",
        description="ServerOS node runtime. Observe-only unless "
                    "--enable-reconcile is passed.",
    )
    parser.add_argument("--registry", help="managed-runtime registry document")
    parser.add_argument("--state-dir", help="where observations are persisted")
    parser.add_argument("--socket", help="Unix socket path")
    parser.add_argument("--interval", type=float, default=60.0, help="seconds between ticks")
    parser.add_argument("--once", action="store_true", help="run one tick and exit")
    parser.add_argument("--enable-reconcile", action="store_true",
                        help="allow restarts of automatable resources. Off by default; "
                             "see the module docstring for the full gate.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    registry_path = Path(args.registry) if args.registry else registry_mod.default_path()
    if not registry_path.exists():
        sys.stderr.write(f"serverosd: no registry at {registry_path}\n")
        return 2
    registry = registry_mod.Registry(path=registry_path)
    daemon = Daemon(
        registry,
        state_dir=Path(args.state_dir) if args.state_dir else default_state_dir(),
        socket_path=Path(args.socket) if args.socket else default_socket_path(),
        enable_reconcile=args.enable_reconcile,
        log=lambda message: sys.stderr.write(f"serverosd: {message}\n"),
    )
    if args.once:
        report = daemon.tick()
        if args.json:
            json.dump(report, sys.stdout, indent=2, sort_keys=True, default=str)
            sys.stdout.write("\n")
        else:
            for rid, obs in report["observations"].items():
                state = (obs.get("health") or {}).get("state") or obs.get("state")
                print(f"{rid:<28} {state}")
            print(f"\n{len(report['drift'])} drift item(s), "
                  f"{len(report['decisions'])} decision(s)")
        return 0
    try:
        daemon.serve_forever(interval=args.interval)
    except DaemonAlreadyRunning as exc:
        # A distinct exit code, so a unit file or a person can tell "there is
        # already one of me" from "I could not start". `Restart=on-failure`
        # would otherwise retry this forever, and it would be right to: the
        # condition clears the moment the other daemon stops.
        sys.stderr.write(f"serverosd: {exc}\n")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
