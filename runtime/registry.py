#!/usr/bin/env python3
"""The Managed Runtime Registry — what this node manages, and on whose behalf.

The rule the module enforces, in one line:

    Nothing is managed by being noticed.

A container that exists is an observation. A container ServerOS may stop is a
*registration*, with an owner, a scope, a lifecycle class and a protection
level, written down before anything touches it. Without that distinction the
first low-disk evening ends with someone's baseline database pruned, and the
post-mortem has no record to check the decision against.

Two vocabularies are load-bearing and neither is invented here:

  `scope: user | system`
      Everything on this node is `user` today. `system` is declarable and is
      NOT actionable in v1 — ServerOS performs no elevation, so a system-scope
      resource that is down reports NEEDS_HUMAN rather than reaching for
      sudo. Recording the scope now means the privilege story is a fact in the
      registry instead of an assumption made at the call site.

  `protection: protected | normal | disposable`
      `protected` is not "important"; it is "the automatic path does not
      reach it, ever". The distinction matters because the tempting version of
      resource management is a policy that is *usually* right, and the failures
      that hurt are the ones where it was right on average.

Validation rejects rather than coerces. A resource with an unknown `kind`, an
untyped owner ref or a lifecycle class outside the closed set is a hard error,
because the alternative is a registry that is silently a slightly different
registry than the one its author believed they wrote.
"""
from __future__ import annotations

import functools
import importlib.util
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read runtime contracts\n")
    raise SystemExit(2)

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent

CONTRACT = _HERE / "managed-runtime-contract.yaml"


def _sibling(name: str, filename: str):
    """Load a sibling module by path, the way the rest of this package does.

    Used to reach `anomaly.py`'s two constant tables rather than restating
    them. They are the SAME rule asserted at two moments — registration and
    observation — and a second copy is how the two would come to disagree
    about what "floating" means without either of them changing visibly.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_anomaly = _sibling("serveros_runtime_anomaly", "anomaly.py")
SESSION_OWNER_PREFIXES = _anomaly.SESSION_OWNER_PREFIXES
DURABLE_LIFECYCLE_CLASSES = _anomaly.DURABLE_LIFECYCLE_CLASSES
FLOATING_DEPENDENCY_PATTERNS = _anomaly.FLOATING_DEPENDENCY_PATTERNS

# The typed-ref shape used across ServerOS. Same convention as `skill:<id>`
# and `device:<id>`; kept as a pattern rather than a parser because it must
# agree with `fleet/transport.py`, not replace it.
TYPED_REF = re.compile(r"^(?P<type>[a-z][a-z0-9-]*):(?P<id>[A-Za-z0-9][A-Za-z0-9._-]*)$")

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

# Keys that must never appear anywhere in the registry. A registry is a
# declaration, and declarations get committed, mailed and pasted into issues.
# The scan is recursive and by key name (case-insensitive) — it is a
# structural check, not a search for something that looks like a secret.
FORBIDDEN_KEYS = frozenset(
    {
        "token", "access_token", "refresh_token", "id_token", "secret",
        "client_secret", "password", "passwd", "credential", "credentials",
        "api_key", "apikey", "private_key", "cookie", "session_key",
        "salt_key", "litellm_salt_key", "ui_password", "auth_value",
    }
)

# Suffixes that mark a value as a stored half of a credential rather than a
# name. `*_env` and `*_ref` are the permitted forms: a NAME or a locator, never
# the value. This is what lets the registry say "this container needs
# LITELLM_SALT_KEY" without the registry being where the salt lives.
_CREDENTIAL_KEY_SUFFIXES = ("_value", "_secret", "_token", "_password", "_key")


class RegistryError(Exception):
    """Base class for managed-runtime registry failures."""


class RegistryInvalid(RegistryError):
    """A registration is malformed. A bug in the author, not a runtime fact."""


class UnknownResource(RegistryError):
    """No registration carries this id.

    Distinct from "the resource is down". A caller asking for an unregistered
    id has made a mistake; a caller asking for a registered one has a fact.
    """


def _NAME_MATCH(slug: str, name: str) -> bool:
    return re.search(rf"(?:^|[^a-z0-9]){re.escape(slug)}(?:$|[^a-z0-9])", name, re.I) is not None


def _parse_ref_list(raw, where: str) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RegistryInvalid(f"{where}: expected a list of {{ref, because}} entries")
    out = []
    seen = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict) or not entry.get("ref"):
            raise RegistryInvalid(f"{where}[{i}]: each entry needs a typed `ref`")
        ref = entry["ref"]
        if not TYPED_REF.match(ref):
            raise RegistryInvalid(
                f"{where}[{i}]: {ref!r} is not a typed ref. `{{type, id}}` is the "
                f"identity contract; a bare name is how one workstream becomes "
                f"another workstream's cleanup candidate."
            )
        if ref in seen:
            raise RegistryInvalid(f"{where}[{i}]: duplicate ref {ref!r}")
        seen.add(ref)
        out.append({
            "ref": ref,
            "because": entry.get("because") or "",
            "state": entry.get("state"),
        })
    return out


@functools.lru_cache(maxsize=1)
def contract() -> dict:
    """The schema, parsed once per process.

    This started uncached, and the cost was not obvious until the test suite
    ran: validating one observation reads the contract, and `Health.state`
    reads it once per rule, so evaluating all 512 axis combinations re-parsed
    the same YAML thousands of times and took 72 seconds.

    A daemon evaluating health on a timer would have paid that on every tick.
    The contract is a compiled-in schema, not mutable state, so reading it once
    is correct as well as fast — a running process should NOT silently pick up
    an edited schema file, because that would make two running components
    disagree about the vocabulary with nothing to detect it.

    Returns the parsed document itself, not a copy. Callers treat it as
    read-only; `test_an_uncovered_combination_raises...` monkeypatches this
    function rather than mutating what it returns, which is the pattern to
    follow.
    """
    with CONTRACT.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# The one place that answers "where does this node's registry live".
#
# It used to be answered twice — `cli.default_registry_path` and the body of
# `daemon.main` each built the same expression out of the same two literals.
# Two producers of one fact agree until one of them is edited, and then the
# disagreement is invisible: `serverosctl` reports a resource the daemon has
# never heard of and both surfaces are internally consistent. §18 asks for one
# producer per fact, so the expression moved here, next to the document it
# names, and both surfaces now call it.
STATE_DIR_ENV = "SERVEROS_STATE_DIR"
REGISTRY_RELATIVE = "runtime/registry/resources.yaml"

# The qualified resolver for "where is this node's state root". Loaded the way
# this package loads every sibling, and under the same module name the rest of
# the tree uses, so this is the one module object and not a second copy of it.
_paths = _sibling("serveros_runtime_paths", "paths.py")


def default_path() -> Path:
    """This node's registry document. A path, whether or not it exists.

    `SERVEROS_STATE_DIR` is honoured: the unit the installer renders sets it,
    and a variable a node's own service file exports is a statement about that
    node. What is gone is the literal under it. It read
    `/opt/applications/data/home-hwz/state/serveros` on every node that had not
    exported `DATA_ROOT`, which is every node but one — and the way that
    surfaced was an error message naming a directory the operator had never
    heard of, from a host they were not on.
    """
    state = os.environ.get(STATE_DIR_ENV) or str(_paths.Paths().root("state"))
    return Path(state) / REGISTRY_RELATIVE


def load_default(path: str | Path | None = None) -> "Registry":
    """The registry, or a typed refusal naming where it was looked for.

    There is no discovery fallback and no empty-registry default. `Nothing is
    managed by being noticed` is the module's whole premise, so "I found no
    registry" must not be served as "nothing is registered" — the second is a
    claim about the node and the first is a fact about a file. Every caller
    that wants to *report* rather than *refuse* has to say so at the call site.
    """
    target = Path(path) if path is not None else default_path()
    if not target.exists():
        raise RegistryError(
            f"no managed-runtime registry at {target}. Nothing is managed "
            f"until something is registered; there is no discovery fallback."
        )
    return Registry(path=target)


def _values(section: str) -> tuple[str, ...]:
    c = contract()[section]
    if isinstance(c, dict):
        return tuple(c)
    return tuple(c)


def _axis_names() -> tuple[str, ...]:
    return tuple(contract()["health_axes"])


def _criticality_values() -> tuple[str, ...]:
    """`criticality` is a section, not a plain list, so `_values` would return
    its KEYS. Named accessors keep that mistake from being available."""
    return tuple(contract()["criticality"]["values"])


def _criticality_defaults() -> dict[str, str]:
    return dict(contract()["criticality"]["default_by_lifecycle"])


def _effective_criticality(record: dict) -> str:
    """The rank, computable from a raw record before a `Resource` exists.

    `criticality` is a plain string in this schema, not a `{"level": ...}`
    block like `protection`. The first version of the floating-dependency gate
    re-derived the default inline as `or "normal"`, which is wrong twice over:
    it crashes on the declared form (`.get` on a `str`), and where it does not
    crash it disagrees with the contract, which ranks an undeclared
    `persistent` resource `important`. Both are the same mistake — a second
    copy of a rule that lives in a file people review. So there is one lookup,
    and `Resource.criticality` is a caller of it rather than a peer.
    """
    declared = record.get("criticality")
    if declared:
        return declared
    lifecycle = (record.get("lifecycle") or {}).get("class") or ""
    return _criticality_defaults().get(lifecycle, "normal")


def _recovery_models() -> tuple[str, ...]:
    return tuple(m["id"] for m in contract()["recovery_models"])


def _reconcile_modes() -> tuple[str, ...]:
    return tuple(contract()["reconcile_policy"]["modes"])


def _reconcile_default() -> str:
    return contract()["reconcile_policy"]["default"]


def _axis_values(axis: str) -> tuple[str, ...]:
    return tuple(contract()["health_axes"][axis]["values"])


def _walk_keys(node, path="") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((str(k), path))
            out.extend(_walk_keys(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            out.extend(_walk_keys(item, f"{path}[{i}]"))
    return out


def _reject_credentials(record: dict, where: str) -> None:
    """A registry entry is a declaration of intent, never a credential store."""
    for key, path in _walk_keys(record):
        low = key.lower().strip("-")
        if low in FORBIDDEN_KEYS:
            raise RegistryInvalid(
                f"{where}: {path or key!r} carries a credential-shaped key. "
                f"Registrations name credentials; they never hold them. Use "
                f"the `*_env` form (the variable's NAME) or a typed ref."
            )
        if low.endswith(_CREDENTIAL_KEY_SUFFIXES) and low not in {
            "salt_key_name", "key_name", "key_ref",
        }:
            raise RegistryInvalid(
                f"{where}: {path or key!r} looks like a stored credential half. "
                f"Use `*_env` (the name) or `*_ref` (a locator)."
            )


class Resource:
    """One registered managed runtime.

    Frozen: a registration is read from disk and then reasoned about, and a
    mutable registration would let a caller "fix" a resource in memory and
    report a state the registry does not contain.
    """

    __slots__ = ("_r",)

    def __init__(self, record: dict) -> None:
        self._r = record

    # -- identity ----------------------------------------------------------
    @property
    def id(self) -> str:
        return self._r["id"]

    @property
    def kind(self) -> str:
        return self._r["kind"]

    @property
    def owner(self) -> dict:
        return self._r["owner"]

    @property
    def owner_ref(self) -> str:
        return self._r["owner"]["ref"]

    @property
    def owner_type(self) -> str:
        return self._r["owner"]["type"]

    @property
    def scope(self) -> str:
        return self._r["scope"]

    @property
    def lifecycle(self) -> str:
        return self._r["lifecycle"]["class"]

    @property
    def desired(self) -> str:
        return self._r["desired"]["state"]

    @property
    def protection(self) -> str:
        return self._r["protection"]["level"]

    @property
    def criticality(self) -> str:
        """Declared, or the lifecycle-derived default.

        Never UNKNOWN. An unranked resource would sort last, which is the one
        wrong answer available: something nobody ranked is not thereby
        unimportant.
        """
        return _effective_criticality(self._r)

    @property
    def criticality_is_declared(self) -> bool:
        """Whether the rank was written down or derived.

        A derived rank is still a rank, but a report that cannot tell the two
        apart cannot say which resources anybody actually made a judgement
        about.
        """
        return bool(self._r.get("criticality"))

    @property
    def recovery(self) -> str:
        """What restarting this would achieve. Defaults to `restart` — the
        reflex — and that default is only reached when nobody said otherwise."""
        return ((self._r.get("recovery") or {}).get("model")) or "restart"

    @property
    def reconcile(self) -> dict:
        """The per-resource reconcile policy (§11), normalised.

        Always returns a `mode`, and the mode is `observe` when the
        registration said nothing. That default is the whole design: silence
        is not "use the global setting", silence is "change nothing", so a
        resource nobody wrote a policy for can never be acted on by a
        component that read the absence as permission.

        `authorises_action` is the single question the reconciler asks, kept
        here rather than spelled out at the call site so that the call site
        cannot get it subtly wrong in one branch.
        """
        block = self._r.get("reconcile") or {}
        mode = block.get("mode") or _reconcile_default()
        return {
            "mode": mode,
            "max_attempts": block.get("max_attempts"),
            "backoff": block.get("backoff"),
            "declared": bool(block),
            "authorises_action": mode != _reconcile_default(),
        }

    @property
    def functional_probe_available(self) -> bool:
        """Whether anything here can ask this resource whether it works.

        §10 requires a functional probe before an automatic recovery, and the
        reason is not procedural: a restart whose effect cannot be measured is
        a restart that reports success because nothing was able to report
        failure. This is the same distinction `managed-runtime-contract.yaml`
        draws between `observe` and `probe`, applied as a gate rather than as
        a definition.
        """
        if self.not_applicable.get("functional"):
            # Explicitly declared as having no callable surface. That is a
            # decision, and it still does not give the reconciler anything to
            # check afterwards — so it does not satisfy the conjunct.
            return False
        return any(p.get("axis") == "functional" for p in self.probes)

    @property
    def not_applicable(self) -> dict:
        return self._r.get("not_applicable") or {}

    @property
    def observe(self) -> dict:
        return self._r.get("observe") or {}

    @property
    def probes(self) -> tuple[dict, ...]:
        return tuple(self._r.get("probes") or ())

    @property
    def note(self) -> str | None:
        return self._r.get("note")

    @property
    def record(self) -> dict:
        return dict(self._r)

    # -- derived questions -------------------------------------------------

    @property
    def is_automatable(self) -> bool:
        """May the reconcile path touch this at all?

        Defined as "no reason blocks it", rather than as a parallel list of
        conditions. The two used to be written twice — a boolean expression
        here and a list of reasons below — which is the same shape as every
        other defect this project has had to fix: two copies of one rule that
        agree until somebody edits one of them. There is now one rule, and the
        boolean is a read of it.

        Each conjunct is checked separately so the report can say *which* one
        applied. A single `if protected` would collapse "we must not" into
        "we chose not to".
        """
        return not self.why_not_automatable()

    def automatability(self) -> dict:
        """§10, with each failing conjunct assigned to what it actually blocks.

        §10 lists seven conditions that must ALL hold before an automatic
        restart or reconnect. They are not all the same kind of condition, and
        treating them as one set collapses §9's distinctions:

          * PERMISSION blocks say ServerOS must not act and nobody may act
            automatically — the resource is protected, system-scoped, or
            unowned. §9: "no action".
          * POLICY blocks say ServerOS may not act *automatically* and nothing
            is wrong with saying so out loud — the resource is task-scoped, or
            its policy is `observe`, or nothing can verify a recovery. §9:
            "recommendation only".

        Both make `is_automatable` False. Only the first makes the fault a
        NEEDS_HUMAN: a task-scoped container that is down is not a situation
        requiring a person, it is one ServerOS may report and not fix. Folding
        the two together is how a recommendation gets escalated into an alarm,
        and after that nobody reads the alarms.

        "Explicitly registered" is not checked: this method is reachable only
        through a `Resource`, and a `Resource` exists only if it was
        registered. The conjunct is satisfied by the type rather than by a test
        that could be forgotten.
        """
        permission, policy = [], []
        if self.owner_type == "unknown":
            # §10's "ServerOS-managed". An owner type that is not one of the
            # declared ServerOS owner types is not a resource ServerOS
            # manages; it is one it noticed.
            permission.append("owner=unknown (fails safe)")
        if self.protection == "protected":
            permission.append("protection=protected")
        if self.scope != "user":
            permission.append(f"scope={self.scope} (ServerOS does not elevate)")
        if self.desired != "running":
            permission.append(f"desired={self.desired}")
        if self.lifecycle != "persistent":
            # §10, and §9's "task-scoped → recommendation only". A restart
            # re-establishes something inside a lifetime that is already
            # ending; the recoverable thing is the work item, not the process.
            policy.append(
                f"lifecycle={self.lifecycle} (automatic recovery is admitted "
                f"for persistent resources only)"
            )
        if not self.reconcile["authorises_action"]:
            # §11. The default, and the reason no resource on this host is
            # automatable today: every registration predates the block, so
            # every one of them is `observe`.
            policy.append(
                "reconcile.mode=observe (no automatic recovery is enabled for "
                "this resource; §11 puts that decision on the resource, not on "
                "a daemon flag)"
            )
        elif not (self.reconcile.get("max_attempts") and self.reconcile.get("backoff")):
            # §10's last conjunct. A mode that permits an action without
            # bounding it is a retry loop wearing a policy's name.
            policy.append(
                "reconcile budget is incomplete (mode permits an action but "
                "max_attempts and backoff are not both declared)"
            )
        if not self.functional_probe_available:
            # §10. Not procedural: a restart whose effect cannot be measured
            # afterwards reports success because nothing could report failure.
            policy.append(
                "no functional probe (a restart that cannot be verified "
                "afterwards is not recovery)"
            )
        return {
            "permission": permission,
            "policy": policy,
            "automatable": not (permission or policy),
            # §9's "recommendation only", stated as a property rather than
            # left for each caller to re-derive from two lists.
            "recommendation_only": bool(policy) and not permission,
        }

    def why_not_automatable(self) -> list[str]:
        """Every §10 conjunct this resource fails, named.

        Complete rather than first-match, because an operator fixing one
        blocker needs to know whether it was the only one. Reporting the first
        would make clearing it look like progress and then fail again — and the
        two groups are concatenated in one list rather than merged, so the
        existing callers that read this as "reasons this is hands-off" keep
        seeing every reason.
        """
        split = self.automatability()
        return split["permission"] + split["policy"]

    def show(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "owner": dict(self.owner),
            "scope": self.scope,
            "lifecycle": self.lifecycle,
            "desired": self.desired,
            "protection": self.protection,
            "criticality": self.criticality,
            "criticality_declared": self.criticality_is_declared,
            "recovery": self.recovery,
            "reconcile": self.reconcile,
            "functional_probe": self.functional_probe_available,
            "automatable": self.is_automatable,
            "not_automatable_because": self.why_not_automatable(),
            "probes": [p.get("id") for p in self.probes],
            "note": self.note,
        }


def _dig(record: dict, dotted: str):
    """Walk a dotted path. Returns (found, value)."""
    node = record
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _not_applicable_reason(record: dict, axis: str) -> str | None:
    """The reason an axis is declared not-applicable, if it was.

    Returns None when the axis is NOT declared, and "" when it is declared
    without a reason — the caller distinguishes those, because "we decided this
    axis does not apply" and "we did not think about it" are different answers
    and only one of them is a decision.
    """
    block = record.get("not_applicable") or {}
    if axis not in block:
        return None
    entry = block[axis]
    if isinstance(entry, dict):
        return str(entry.get("because") or "")
    return ""


def _check_kind_requirements(record: dict, kind: str, where: str) -> None:
    """Registration-time gates for a kind, from `kind_requirements`.

    A kind may be declared in `kinds` and then registered by someone who never
    provides the one thing that kind exists for, and every subsequent run
    reports clean. This is where that stops being possible: the author is still
    present when this runs, so the missing field is a question they can answer
    rather than a footnote in an output nobody reads.

    Nothing here is inferred. Where a requirement genuinely does not apply, the
    contract's escape hatch is an explicit `not_applicable` block WITH A
    REASON — a written decision, not an omission.
    """
    reqs = (contract().get("kind_requirements") or {}).get(kind)
    if not reqs:
        return

    def fail(requirement, why):
        raise RegistryInvalid(
            f"{where} ({record.get('id')}): kind {kind!r} requires {requirement} "
            f"— {why}"
        )

    for req in reqs.get("requires") or ():
        if "field" in req:
            found, value = _dig(record, req["field"])
            if not found or value in (None, "", [], {}):
                fail(f"`{req['field']}`", req.get("because") or "missing")
            # A closed set for one field of one kind, so the constraint lives
            # in the contract rather than in prose beside it. §40 needed this:
            # "an MCP server registered here names a lifetime of persistent or
            # session-scoped" is a rule about a value, not about presence, and
            # without it the modes with no process behind them would be
            # registrable as runtimes.
            allowed = req.get("in")
            if allowed is not None and value not in allowed:
                fail(f"`{req['field']}` in {allowed}", req.get("because") or "out of range")
        elif "probe_axis" in req:
            axis = req["probe_axis"]
            have = any(p.get("axis") == axis for p in record.get("probes") or ())
            if have:
                continue
            excuse = req.get("unless_not_applicable")
            if excuse is not None:
                reason = _not_applicable_reason(record, excuse)
                if reason:
                    continue
                if reason == "":
                    fail(
                        f"a probe on the `{axis}` axis",
                        f"`not_applicable.{excuse}` is declared with no "
                        f"`because`. An axis nobody measures and nobody "
                        f"explains is indistinguishable from an oversight, and "
                        f"it is the axis whose absence caused the 2026-09-12 "
                        f"outage.",
                    )
            fail(f"a probe on the `{axis}` axis", req.get("because") or "missing")
        elif "decisive_owner" in req:
            owner = record.get("owner") or {}
            if not str(owner.get("because") or "").strip():
                fail(
                    "`owner.because`",
                    req.get("because")
                    or "an asserted owner with no stated evidence is a name",
                )
        else:
            raise RegistryInvalid(
                f"{where} ({record.get('id')}): contract declares an unknown "
                f"kind requirement {sorted(req)}. A requirement nothing "
                f"understands is a requirement nothing enforces."
            )

    one_of = reqs.get("requires_one_of") or ()
    if one_of:
        satisfied = []
        for req in one_of:
            if "probe_axis" in req:
                satisfied.append(
                    any(p.get("axis") == req["probe_axis"] for p in record.get("probes") or ())
                )
            elif "not_applicable" in req:
                satisfied.append(
                    bool(_not_applicable_reason(record, req["not_applicable"]))
                )
        if not any(satisfied):
            options = " or ".join(
                f"a probe on `{r['probe_axis']}`" if "probe_axis" in r
                else f"`not_applicable.{r['not_applicable']}` with a `because`"
                for r in one_of
            )
            fail(f"one of: {options}",
                 "neither was provided, so this resource could never be "
                 "reported healthy and could never be reported broken either")


def _check_ownership(record: dict, where: str) -> None:
    """ARCHITECTURE.md invariant 6, at the only moment it can be checked.

    Case A. A tunnel a task depended on was registered with `persistent`
    lifecycle and `session:*` as its owner. It was retired, on time and by
    design, when an unrelated Agent session ended. The far side stayed healthy
    throughout, and so did the tunnel — every observation between creation and
    retirement was correct. There is nothing to detect afterwards, which is why
    the question is asked here, while the author is still present to answer it.

    The repair is deliberately narrow: a caller that genuinely wants a
    session-lifetime resource registers `disposable`. The rule refuses the
    LIE, not the resource.
    """
    owner = (record.get("owner") or {}).get("ref") or ""
    lifecycle = (record.get("lifecycle") or {}).get("class") or ""

    if not any(owner.startswith(prefix) for prefix in SESSION_OWNER_PREFIXES):
        return
    if lifecycle not in DURABLE_LIFECYCLE_CLASSES:
        return

    raise RegistryInvalid(
        f"{where} ({record.get('id')}): a resource owned by {owner!r} may not "
        f"declare `lifecycle.class: {lifecycle}`. A session may cause creation; "
        f"it may not be the owner of record for anything that must outlive it — "
        f"this is the case where a required tunnel disappeared with an unrelated "
        f"Agent session while reporting healthy the entire time. Register it "
        f"`disposable` with a note, or give it an owner that outlives the "
        f"session (`project:`, `work-item:`, `serveros:`, `ability:`)."
    )


def _names_an_absolute_path(text: str) -> bool:
    """Whether the text names a real on-disk location, as opposed to merely
    containing a slash.

    The first version of the floating-dependency gate asked `"/" in text`.
    That is true of `npx @playwright/mcp`, whose slash belongs to a scoped
    package NAME, and the result was that a critical resource invoking a
    versionless scoped package was admitted — the one form the contract
    singles out ("npx <pkg> with no version specifier"), waved through by a
    false positive about a punctuation character. A scoped package is not a
    location, and the difference is exactly the difference this gate is for.

    So the slash has to START a token: preceded by nothing, whitespace, or an
    opening delimiter. `@playwright/mcp` fails that (the slash follows a
    letter) and `/opt/toolchains/...` passes it. A Windows drive path is
    accepted on the same grounds.
    """
    return bool(
        re.search(r"(?:^|[\s\"'=(:,])/(?![/\s])", text)
        or re.search(r"\b[A-Za-z]:\\", text)
    )


def _check_floating_dependency(record: dict, where: str) -> None:
    """ARCHITECTURE.md invariant 8, at registration.

    Case C. One node reached Playwright through `npx @playwright/mcp@latest`;
    the other through an absolute path to a versioned artifact. Both worked,
    the two ran "the same" toolchain and did not, and nothing in the catalogue
    could have said so.

    The severity is derived from the resource's OWN criticality and protection,
    never from anything the caller passes — a caller that could choose its own
    severity would choose the one that lets it proceed. A floating dependency
    on a scratch helper is a finding; on a `critical` or `protected` resource
    it blocks, because "it worked yesterday" and "it works today" are then
    statements about two different programs.
    """
    text = " ".join(str(record.get(key) or "") for key in
                    ("command", "observe", "materialization", "note"))
    if not text.strip():
        return

    # A pinned absolute path is the repair, and an `@latest` elsewhere in the
    # text does not un-pin it — without that, the detector fires on a resource
    # whose own note explains that it used to float.
    if _names_an_absolute_path(text) and "@latest" not in text:
        return

    hits = sorted({name for name, needle in FLOATING_DEPENDENCY_PATTERNS
                   if needle in text})
    if not hits:
        return

    # The severity threshold, from the contract's `admission.findings`:
    # `criticality is critical, or protection is protected`. The rank comes
    # from `_effective_criticality` rather than a `.get("level")` here —
    # `criticality` is a plain string in this schema, and reading it as a block
    # crashed on exactly the resource this gate exists to refuse.
    criticality = _effective_criticality(record)
    protection = (record.get("protection") or {}).get("level") or "normal"
    if criticality != "critical" and protection != "protected":
        return

    raise RegistryInvalid(
        f"{where} ({record.get('id')}): PERSISTENT_RUNTIME_FLOATING_DEPENDENCY "
        f"at HIGH severity — this resource is {criticality}/{protection} and "
        f"resolves through something that moves ({', '.join(hits)}). Pin it to "
        f"an absolute path to a versioned artifact, or record the resolved "
        f"revision alongside the resource."
    )


def validate(record: dict, where: str = "resource") -> Resource:
    """Reject a malformed registration rather than coercing it."""
    required = ("id", "kind", "owner", "scope", "lifecycle", "desired", "protection")
    for field in required:
        if field not in record:
            raise RegistryInvalid(f"{where}: missing required field {field!r}")

    rid = record["id"]
    if not isinstance(rid, str) or not ID_PATTERN.match(rid):
        raise RegistryInvalid(
            f"{where}: id {rid!r} is not a stable id. Ids are lowercase, "
            f"dash-separated, and must not encode a host, a port or a run."
        )

    kind = record["kind"]
    if kind not in _values("kinds"):
        raise RegistryInvalid(
            f"{where} ({rid}): unknown kind {kind!r}. Known: {list(_values('kinds'))}"
        )

    owner = record["owner"]
    if not isinstance(owner, dict) or "type" not in owner or "ref" not in owner:
        raise RegistryInvalid(f"{where} ({rid}): owner needs {{type, ref}}")
    if owner["type"] not in _values("owner_types"):
        raise RegistryInvalid(
            f"{where} ({rid}): unknown owner.type {owner['type']!r}. "
            f"Known: {list(_values('owner_types'))}"
        )
    m = TYPED_REF.match(str(owner["ref"]))
    if not m:
        raise RegistryInvalid(
            f"{where} ({rid}): owner.ref {owner['ref']!r} is not a typed ref. "
            f"Expected `<type>:<id>` — a ref that cannot be resolved is a "
            f"dependency nobody can check."
        )

    if record["scope"] not in _values("scopes"):
        raise RegistryInvalid(f"{where} ({rid}): unknown scope {record['scope']!r}")

    lc = record["lifecycle"]
    if not isinstance(lc, dict) or lc.get("class") not in _values("lifecycle_classes"):
        raise RegistryInvalid(
            f"{where} ({rid}): lifecycle.class must be one of "
            f"{list(_values('lifecycle_classes'))}"
        )

    ds = record["desired"]
    if not isinstance(ds, dict) or ds.get("state") not in _values("desired_states"):
        raise RegistryInvalid(
            f"{where} ({rid}): desired.state must be one of "
            f"{list(_values('desired_states'))}"
        )

    pl = record["protection"]
    if not isinstance(pl, dict) or pl.get("level") not in _values("protection_levels"):
        raise RegistryInvalid(
            f"{where} ({rid}): protection.level must be one of "
            f"{list(_values('protection_levels'))}"
        )

    # Credentials are scanned FIRST, before any other structural check.
    #
    # A registration with a credential-shaped key is wrong in a way the others
    # are not — it is the one defect that leaks the moment the file is committed
    # or pasted — so it is the one that must be reported even when the
    # registration is also malformed. Running this last meant a probe missing a
    # field could mask a stored secret, which is a worse error hiding a better
    # one.
    _reject_credentials(record, f"{where} ({rid})")

    claimed: dict[str, str] = {}
    for probe in record.get("probes") or ():
        if not isinstance(probe, dict):
            raise RegistryInvalid(f"{where} ({rid}): a probe must be a mapping")
        if probe.get("kind") not in _values("probe_kinds"):
            raise RegistryInvalid(
                f"{where} ({rid}): unknown probe kind {probe.get('kind')!r}. "
                f"Known: {list(_values('probe_kinds'))}"
            )
        # `reason` is not a health axis: it is the refinement a diagnostic-back
        # probe is allowed to contribute to an axis that some other probe
        # already decided. Allowing it here keeps that split explicit rather
        # than letting a diagnostic quietly declare itself an axis.
        if probe.get("axis") not in _axis_names() + ("reason",):
            raise RegistryInvalid(
                f"{where} ({rid}): probe {probe.get('id')!r} answers unknown axis "
                f"{probe.get('axis')!r}. Known: {list(_axis_names())} + ['reason']"
            )
        # One probe per axis, checked at REGISTRATION rather than at
        # observation time. Two probes answering `liveness` means one of them
        # silently wins, and which one depends on iteration order — a health
        # report that changes with dictionary ordering is worse than one that
        # refuses to load.
        if probe.get("axis") in claimed:
            raise RegistryInvalid(
                f"{where} ({rid}): probes {claimed[probe['axis']]!r} and "
                f"{probe.get('id')!r} both answer axis {probe.get('axis')!r}. "
                f"One probe per axis: two answers to one question means one of "
                f"them is discarded, and which one is not a decision this "
                f"registry makes by accident."
            )
        # A probe kind needs certain fields to run at all. Checked here, at
        # registration, because two of the probe classes index their spec
        # directly and a missing field is a KeyError or a TypeError inside the
        # runner rather than a clean report — and because a probe that cannot
        # run is a declared-but-absent measurement, which is the one failure
        # this whole file exists to make impossible.
        required_fields = (contract().get("probe_requirements") or {}).get(
            probe.get("kind"), ()
        )
        for field_name in required_fields:
            value = probe.get(field_name)
            if value in (None, "", [], {}):
                raise RegistryInvalid(
                    f"{where} ({rid}): probe {probe.get('id')!r} of kind "
                    f"{probe.get('kind')!r} needs `{field_name}`. Without it the "
                    f"probe is declared and cannot run, which reads as coverage "
                    f"and is the opposite of it."
                )
        claimed[probe.get("axis")] = probe.get("id")

    # Criticality. Declared or defaulted, and the default is READ from the
    # contract rather than pinned here, so that changing the policy is a one
    # line edit to a file people review.
    declared_criticality = record.get("criticality")
    if declared_criticality is not None and declared_criticality not in _criticality_values():
        raise RegistryInvalid(
            f"{where} ({rid}): unknown criticality {declared_criticality!r}. "
            f"Known: {list(_criticality_values())}. Criticality ranks attention "
            f"and grants nothing; a value outside this list is a word, not a rank."
        )

    # Recovery model (§20). A resource whose failure restarting cannot fix has
    # to say so, because restarting it is the default reflex and, on
    # 2026-09-12, the thing that lengthened the outage.
    recovery = record.get("recovery")
    if recovery is not None:
        if not isinstance(recovery, dict) or not recovery.get("model"):
            raise RegistryInvalid(
                f"{where} ({rid}): `recovery` needs a `model`. An empty recovery "
                f"block reads as `restart`, which is the assumption this field "
                f"exists to stop being the default."
            )
        if recovery["model"] not in _recovery_models():
            raise RegistryInvalid(
                f"{where} ({rid}): unknown recovery model {recovery['model']!r}. "
                f"Known: {list(_recovery_models())}"
            )
        if recovery["model"] != "restart" and not str(recovery.get("because") or "").strip():
            raise RegistryInvalid(
                f"{where} ({rid}): recovery {recovery['model']!r} needs a "
                f"`because`. Declaring that a resource cannot be fixed by "
                f"restarting is a claim about why, and the operator who would "
                f"otherwise restart it is the reader."
            )

    # Reconcile policy (§11). Absent is the normal case and means `observe`;
    # present is a decision to permit an operation, so it is validated the way
    # a permission is validated rather than the way a preference is.
    reconcile_block = record.get("reconcile")
    if reconcile_block is not None:
        if not isinstance(reconcile_block, dict):
            raise RegistryInvalid(
                f"{where} ({rid}): `reconcile` must be a mapping. A bare string "
                f"reads as a mode and is a mode nobody validated."
            )
        mode = reconcile_block.get("mode")
        if mode not in _reconcile_modes():
            raise RegistryInvalid(
                f"{where} ({rid}): unknown reconcile mode {mode!r}. Known: "
                f"{list(_reconcile_modes())}. An unrecognised mode is not a "
                f"reason to fall back to the default: the author wrote "
                f"something, and quietly running `observe` instead would make "
                f"a typo look like a policy."
            )
        if mode != _reconcile_default():
            # Any mode that permits an action must be bounded. Checked here,
            # where the author is still present, rather than at the moment the
            # action is due.
            budget = registry_contract_budget_requirements()
            missing = [f["field"] for f in budget if not reconcile_block.get(f["field"])]
            if missing:
                raise RegistryInvalid(
                    f"{where} ({rid}): reconcile.mode={mode!r} permits an "
                    f"operation and is missing {missing}. §10 admits automatic "
                    f"recovery only with a retry budget configured; an "
                    f"unbounded restart policy is a crash loop with a policy "
                    f"name."
                )
            if not record.get("recovery"):
                raise RegistryInvalid(
                    f"{where} ({rid}): reconcile.mode={mode!r} with no "
                    f"`recovery` block. The recovery model says what restarting "
                    f"would achieve; a resource that permits a restart without "
                    f"saying what it is for is the 2026-09-12 reflex "
                    f"re-instituted by omission."
                )

    _check_kind_requirements(record, kind, where)
    _check_ownership(record, where)
    _check_floating_dependency(record, where)
    return Resource(record)


def registry_contract_budget_requirements() -> list[dict]:
    return list(contract()["reconcile_policy"]["budget_requirements"])


class Registry:
    """Every registration on this node, loaded from one document.

    Injectable path so a test can exercise a registry without one existing.
    """

    def __init__(self, document: dict | None = None, *, path: str | Path | None = None):
        # Kept, not just read. Two other modules need the SAME document this
        # object was validated from — container attribution needs an index built
        # from it, and re-reading the file by a remembered path is how a
        # registry this object refused to load gets loaded anyway on the side.
        # `None` when constructed from a document, which is the honest answer to
        # "which file is this" and not a default that silently points somewhere.
        self.path = Path(path) if path is not None else None
        if document is None:
            if path is None:
                raise RegistryError("either `document` or `path` is required")
            document = load_yaml(Path(path))
        if document.get("schema") != "serveros/managed-runtime-registry.v1":
            raise RegistryInvalid(
                f"unexpected registry schema {document.get('schema')!r}; expected "
                f"'serveros/managed-runtime-registry.v1'"
            )
        # The node this registry describes. Kept, and NOT made a validation
        # requirement: the live document carries it and describes one host, but
        # a document built to exercise one resource's probes has no business
        # being refused for naming no device. `serverosctl device show` used to
        # hold a hard-coded copy of this string, which is the defect worth
        # fixing here — one fact, one place — not a new gate.
        device_ref = document.get("device_ref")
        self.device_ref = device_ref if isinstance(device_ref, str) and device_ref else None

        seen: dict[str, Resource] = {}
        for i, record in enumerate(document.get("resources") or ()):
            resource = validate(record, where=f"resources[{i}]")
            if resource.id in seen:
                raise RegistryInvalid(
                    f"duplicate resource id {resource.id!r}: two registrations for "
                    f"one thing is how one of them silently wins"
                )
            seen[resource.id] = resource
        self._by_id = seen
        self._order = [r["id"] for r in (document.get("resources") or ())]
        # Refs that are protected or deferred WITHOUT being registered.
        #
        # §3 of the brief protects LIT-0103, LIT-0104 and the active business
        # worktrees. LIT-0104 has registrations below; LIT-0103 has none, and
        # until now its protection existed only as a sentence in a comment —
        # which is how a classifier written by someone reading quickly came to
        # bucket `lit-0103-r3-*` as a reclaim candidate on this very host.
        #
        # So the protection list is a document, not prose. It is deliberately
        # separate from `resources` because it is a different kind of claim:
        # `resources` says "this is ours and here is what it should do";
        # `protected_refs` says "do not touch this, whatever you think it is".
        self._protected_refs = _parse_ref_list(document.get("protected_refs"), "protected_refs")
        self._defer_refs = _parse_ref_list(document.get("defer_refs"), "defer_refs")

    def protected_refs(self) -> list[dict]:
        """Refs no automatic path may touch, registered or not."""
        return list(self._protected_refs)

    def defer_refs(self) -> list[dict]:
        """Refs carrying an unknown owner or an active writer. DEFER, always."""
        return list(self._defer_refs)

    def refs_matching(self, name: str) -> dict:
        """Which protected/deferred refs a free-text name looks like.

        Name matching is used in exactly ONE direction and this is it: a hit
        means "do not touch", never "this is yours". A name is not an owner —
        but the cost of a false positive here is that something harmless is
        left alone, and the cost of a false negative is a protected parallel
        workstream behind a disk-pressure trigger. Those are not symmetric, so
        the loose direction is the safe one.

        The match is on the ref's id, delimited by non-alphanumerics, so
        `lit-0103-r3-litellm` matches `work-item:LIT-0103` and `lit-01030`
        does not.
        """
        hits = {"protected": [], "defer": []}
        for bucket, refs in (("protected", self._protected_refs), ("defer", self._defer_refs)):
            for entry in refs:
                slug = entry["ref"].split(":", 1)[-1]
                if _NAME_MATCH(slug, name):
                    hits[bucket].append(entry)
        return hits

    def all(self) -> list[Resource]:
        return [self._by_id[i] for i in self._order]

    def get(self, resource_id: str) -> Resource:
        try:
            return self._by_id[resource_id]
        except KeyError:
            raise UnknownResource(
                f"{resource_id!r} is not a registered managed runtime. "
                f"Registered: {sorted(self._by_id)}. A resource is managed by "
                f"being registered, never by being noticed."
            ) from None

    def by_kind(self, kind: str) -> list[Resource]:
        return [r for r in self.all() if r.kind == kind]

    def by_owner(self, ref: str) -> list[Resource]:
        return [r for r in self.all() if r.owner_ref == ref]

    def by_lifecycle(self, lifecycle_class: str) -> list[Resource]:
        return [r for r in self.all() if r.lifecycle == lifecycle_class]

    def protected(self) -> list[Resource]:
        return [r for r in self.all() if r.protection == "protected"]

    def automatable(self) -> list[Resource]:
        return [r for r in self.all() if r.is_automatable]

    def __len__(self) -> int:
        return len(self._by_id)
