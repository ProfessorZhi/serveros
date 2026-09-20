#!/usr/bin/env python3
"""SYS-02 的持久那一半 — `state/serveros/resource-leases/` 的 producer。

契约说这个 namespace 是什么
----------------------------
`fleet/state-root-contract.yaml` 里逐字写着：

    resource-leases:
      class: authority
      holds: current resource occupancy
      producer: runtime/
      required: false
      absent_means: NO_LEASES_HELD

一个 `authority`，一个**点名的 producer**，而 `runtime/` 里此前没有它。这个模块
就是那个 producer。它不是在造第二套 lease 系统——`integration/components.yaml`
把 `new-lease-system` 列为拒绝项，而拒绝的是「第二套」，不是「把这一个做出来」；
真正的消费者 `ops/workspace/serveros/resources.yaml` 已经把动态占用指向这里，
`runtime/paths.py` 也已经把这个 namespace 放进封闭词表。

和 `serveros.db#leases` 的关系，说清楚，因为两份记录读起来很像
------------------------------------------------------------
work-engine 的 `leases` 表（`resource PRIMARY KEY, owner, token, expires_at`）
问的是「谁声称自己占着它」——互斥声明。`context/resolver.py#held_leases` 读它，
并把它算进 `protected`。本模块问的是「哪些域要着它、各自几个引用、最后一个走了
以后谁有权动资源」。两者**不是**同一份事实的两种存法，所以本模块：

  * 不写 `serveros.db`，一个字段都不写；
  * 不把 `leases` 表的一行当作自己的引用。有人在那边声明独占，不使任何人在这边
    多出一个 desired 引用——那正是「观测静默变成 desired」；
  * 反过来，本模块的占用也不是那边互斥的依据。谁要用谁去问 work-engine。

一个文件一个资源，空目录就是空闲
--------------------------------
`absent_means: NO_LEASES_HELD` 说的是：**没有文件 = 没有占用**。所以一个既不
被引用、也没有附着的东西**不留文件**。这带来一个必须写下来的区分：引用退光但
裁决说「留着」（shared / device-persistent）或「不许动」（protected / 别人的）
时，文件**留着**，`references` 是空的——因为「没人要了但还挂着」不是
「没人占用」，把它删掉就是让下一个读者看不见一件还在的事。

文件名是可逆的转义，不是把资源名直接当路径
------------------------------------------
资源名长成 `browser:chromium`、`hosting_docker:MUC-0101`。直接拼进路径会得到一个
带冒号的文件名和一个可以 `../` 的名字。`quote(resource, safe="")` 是单射的，
所以两个不同的资源不会撞到同一个文件名，而记录里还带着 `resource` 字段——
读者不必反解文件名。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

_HERE = Path(__file__).resolve().parent

SCHEMA = "serveros/resource-lease.v1"

# The named reference the state root contract gives this namespace. Spelled as
# a REF and resolved through `paths.Paths`, never joined here: a producer that
# builds its own durable path is the thing `runtime/paths.py` was written to
# stop, and it would resolve to a plausible directory on a node whose roots are
# not the ones this process thinks they are.
NAMESPACE_REF = "state/resource-leases"

# How many transitions one resource keeps. Bounded because this is authority
# for CURRENT occupancy, not an audit log: the transition record exists so that
# "the last reference exited and nothing removed it" is legible to the next
# reader, not so that the file grows with every declare. The unbounded version
# of this is a file that eventually cannot be read by a person.
HISTORY_LIMIT = 20


class LeaseError(RuntimeError):
    """The namespace could not be read or written."""


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Under the canonical synthetic name, the one `runtime/cli.py` and
# `runtime/daemon.py` use for the same file. Two names would be two module
# objects, and the verdict this module records would then be produced by a
# module the caller never sees.
environment = _load("serveros_runtime_environment", "environment.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")


def default_root() -> Path:
    """Where this namespace is, on THIS node, per the root contract.

    `bound` is checked before the path is returned. An unbound root is a
    template string (`{DATA_ROOT}/...`), and a producer that wrote into one
    would create a directory that looks like a name and is not a path — on a
    node whose roots were never resolved.
    """
    paths = paths_mod.Paths()
    if not paths.bound:
        raise LeaseError(
            "this node's roots are UNBOUND, so there is no resource-leases "
            f"namespace to write: {list(paths.unconfigured)}. Refusing to "
            "compose a durable path from string parts")
    return Path(paths.resolve(NAMESPACE_REF)["path"])


def namespace_root(state_dir=None, *, root=None) -> Path:
    """THE one place that says where this namespace is, from what a caller has.

    Three callers arrive with three different things, and before this function
    existed each of them composed the path itself:

      * a daemon, holding its RUNTIME state dir (`state/serveros/runtime`);
      * `serverosctl`, which in an isolated run is handed the same thing;
      * a test, which names the namespace directly.

    The middle one is why this exists. `serverosctl lease declare --state-dir X`
    and `serverosctl lease list --state-dir X` used to disagree: the write verbs
    took X as the namespace root while the reads derived `<X>/../resource-leases`
    from it. Both answered without complaint and the declared resource was
    invisible to the list — a writer and a reader of one record composing the
    path two ways, which is the failure `runtime/paths.py` was written to stop.

    `<state_dir>/../resource-leases` is not an invented layout. `default_state_dir()`
    composes `$SERVEROS_STATE_DIR + /runtime`, `daemon._item_roots` reads
    `serveros.db` at `<state_dir>/../serveros.db`, and on a correctly configured
    node this derivation lands on the same directory
    `paths.Paths().resolve(NAMESPACE_REF)` names. They agree because they are the
    same fact read from the same two inputs — and they diverge when an operator
    points a daemon somewhere else, which is the point: a daemon answering about
    the live machine from `--state-dir /tmp/x` is the failure this repository has
    recorded under a different name. `None` means no override: the root contract.
    """
    if root is not None:
        return Path(root)
    if state_dir is not None:
        return Path(state_dir).parent / "resource-leases"
    return default_root()


def open_store(state_dir=None, *, root=None, now=None) -> "LeaseStore":
    """A store over `namespace_root(...)`. Read and write callers both come here.

    Both halves of every verb go through this function so that a writer and a
    reader cannot address different directories — see `namespace_root`.
    """
    return LeaseStore(namespace_root(state_dir, root=root), now=now)


def path_for(root, resource: str) -> Path:
    """One resource, one file. Injective, so two resources never collide."""
    if not resource or not isinstance(resource, str):
        raise environment.Rejected("a resource name is required")
    if resource != resource.strip():
        raise environment.Rejected(
            f"resource {resource!r} has leading or trailing whitespace. Two "
            "names that differ only by a space would be two files holding one "
            "resource, and the second reader would take whichever it found")
    return Path(root) / (quote(resource, safe="") + ".json")


def _atomic_write(path: Path, payload: dict) -> None:
    """Temp file in the same directory, then `os.replace`.

    Same reasoning as `runtime/agent_control_store.py#_atomic_write`, and the
    same failure it prevents: a half-written record is read back by the next
    caller as a resource with no references, which is the record for "free".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=path.name + ".", suffix=".tmp", delete=False)
    try:
        json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now))


class LeaseStore:
    """`state/serveros/resource-leases/` — current occupancy, nothing else."""

    def __init__(self, root=None, *, now=None) -> None:
        self.root = Path(root) if root is not None else default_root()
        self._now = now or time.time

    # -- reading -----------------------------------------------------------

    def records(self) -> list[dict]:
        """Every record, resource-ascending. Absent namespace is empty, not error."""
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.json")):
            out.append(self._read(path))
        return sorted(out, key=lambda row: row["resource"])

    def get(self, resource: str) -> dict | None:
        path = path_for(self.root, resource)
        return self._read(path) if path.is_file() else None

    def _read(self, path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Refused by name rather than skipped. A record that cannot be read
            # is a resource whose occupancy is unknown, and returning the rest
            # of the list without it would report the node as having one fewer
            # thing in use than it has.
            raise LeaseError(
                f"{path} exists and cannot be read ({type(exc).__name__}: {exc}). "
                "Refusing to report the namespace as if this resource were free"
            ) from exc
        if not isinstance(data, dict) or not data.get("resource"):
            raise LeaseError(f"{path} is not a resource-lease record")
        data.setdefault("references", [])
        data.setdefault("history", [])
        data["schema"] = SCHEMA
        return data

    # -- writing -----------------------------------------------------------

    def declare(self, resource: str, *, scope: str, holder: str,
                sharing: str, ttl_s: float | None = None,
                note: str = "") -> dict:
        """Declare that `holder`, in `scope`, needs `resource`.

        Idempotent on `(scope, holder)`: a second declare by the same holder
        REFRESHES its reference instead of adding one. This is the property a
        retried request depends on — a "declare" that added a reference per
        attempt would inflate the count until a legitimately shared resource
        looked like it had eleven owners, and the last-reference-exit question
        would then be answered against a number nobody meant.
        """
        scope = environment.parse_scope(scope)
        sharing = environment.parse_sharing(sharing)
        if not holder or not isinstance(holder, str):
            raise environment.Rejected(
                "a holder is required. A reference nobody holds cannot be "
                "released by anybody, which makes it a leak with a name")

        now = self._now()
        record = self.get(resource) or {
            "schema": SCHEMA,
            "resource": resource,
            "scope": scope,
            "sharing": sharing,
            "references": [],
            "history": [],
            "created_at": _stamp(now),
        }

        # Sharing and scope are PROPERTIES OF THE RESOURCE, not of the holder.
        # A caller that declares the browser `exclusive` because its own case
        # wants exclusivity would silently make every other holder's `shared`
        # declaration false, and the record would carry whichever declaration
        # was written last.
        if record["sharing"] != sharing:
            raise environment.Rejected(
                f"{resource!r} is declared {record['sharing']!r} and this "
                f"request declares {sharing!r}. The sharing mode is the "
                "resource's, not the holder's: changing it here would "
                "retroactively change what every existing reference means")
        if record["scope"] != scope:
            raise environment.Rejected(
                f"{resource!r} is declared under scope {record['scope']!r} and "
                f"this request declares {scope!r}. A reference's scope is what "
                "its release default is read from, so moving it would move the "
                "policy that governs it")

        existing = self._find(record, scope=scope, holder=holder)
        expires_at = None if ttl_s is None else now + float(ttl_s)
        if existing is not None:
            # A holder that declares again is REFRESHING, not adding a second
            # claim. See the docstring: this is what makes a retried request
            # safe, and without it the reference count inflates per attempt.
            existing["expires_at"] = expires_at
            existing["note"] = note
            act = "REFRESH"
        else:
            record["references"].append({
                "scope": scope,
                "holder": holder,
                "declared_at": _stamp(now),
                "expires_at": expires_at,
                "retired_at": None,
                "note": note,
            })
            act = "DECLARE"

        self._append_history(record, act, scope=scope, holder=holder,
                             at=now, note=note, verdict=None)
        record["updated_at"] = _stamp(now)
        _atomic_write(path_for(self.root, resource), record)
        return {"act": act, "record": record}

    def release(self, resource: str, *, scope: str, holder: str,
                owner_class: str = "MANAGED", protection=None,
                at=None) -> dict:
        """Retire one reference, and decide what happens to the resource.

        The verdict is NOT computed here — `runtime/environment.py` owns it, and
        this method calls it with the record's own `sharing`/`scope` plus the
        ownership facts the caller resolved. Two places computing "may this be
        released" would be two answers to the one question SYS-02's
        counterexamples are about.

        The record is REMOVED only when the verdict is ALLOW and no live
        reference remains. Otherwise it stays, with its retired references and
        the verdict beside them, because "the last reference exited and this
        resource is still here" is exactly what the next reader has to be able
        to see.
        """
        scope = environment.parse_scope(scope)
        now = self._now() if at is None else at
        record = self.get(resource)
        if record is None:
            return {"released": False, "changed": False,
                    "reason": "NO_SUCH_RESOURCE", "resource": resource,
                    "known_holders": [],
                    "note": ("nothing is declared for this resource, so there is "
                             "nothing to release. This is an answer and not an "
                             "error: it is the ordinary result of releasing "
                             "twice")}

        reference = self._find(record, scope=scope, holder=holder)
        if reference is None:
            # Released twice, or named a holder that never held it. The two are
            # different facts and the `history` list tells them apart: an
            # ALREADY_RETIRED answer is what makes a retried release safe to
            # repeat, and NO_SUCH_REFERENCE names the holders that DO hold it so
            # that a typo is distinguishable from an absent holder.
            already = self._retired_at(record, scope=scope, holder=holder)
            if already:
                return {"released": False, "changed": False,
                        "reason": "ALREADY_RETIRED", "resource": resource,
                        "retired_at": already, "known_holders": [],
                        "note": ("this reference was already retired. Reported "
                                 "as a finding rather than refused, because "
                                 "releasing twice is what a retry looks like")}
            return {"released": False, "changed": False,
                    "reason": "NO_SUCH_REFERENCE", "resource": resource,
                    "known_holders": sorted(
                        f"{row['scope']}:{row['holder']}"
                        for row in record["references"]),
                    "note": ("this holder has no reference to this resource. "
                             "The holders that do are named, so a typo and a "
                             "genuinely absent holder are distinguishable")}

        reference["retired_at"] = _stamp(now)

        # RETIRED references leave the list; EXPIRED ones stay in it.
        #
        # The asymmetry is the point. A retired reference is resolved — its
        # holder handed it back, and the transition is recorded in `history`. An
        # expired one is UNRESOLVED: somebody claimed this resource and their
        # claim stopped being backed by anything. Dropping it would erase the
        # only evidence that a holder ever existed, which is exactly the crash
        # case SYS-02 asks to be answerable.
        record["references"] = [row for row in record["references"]
                                if not row.get("retired_at")]

        reference_rows = [environment._reference_row(row, now=now)
                          for row in record["references"]]
        live_count = sum(1 for row in reference_rows if row["state"] == environment.LIVE)
        verdict = environment.release_verdict(
            sharing=record["sharing"], scope=scope, owner_class=owner_class,
            protection=protection, reference_count=live_count)
        # `attached=None`: this module did not observe whether the resource is
        # present, and asserting `False` here would be an observation it never
        # made — the shape of claim that gets a thing deleted.
        state = environment.attachment_state(reference_rows, attached=None)

        self._append_history(record, "RELEASE", scope=scope, holder=holder,
                             at=now, note="", verdict=verdict["verdict"])
        # Removed only when nothing is left to resolve AND the verdict permits
        # it. An expired reference left behind keeps the file, which is why a
        # crashed holder is visible to the next reader instead of looking like
        # an idle node — and why `absent_means: NO_LEASES_HELD` is still true
        # when the file is gone.
        if not record["references"] and verdict["verdict"] == environment.ALLOW:
            path = path_for(self.root, resource)
            try:
                path.unlink()
            except OSError as exc:
                raise LeaseError(f"{path} could not be removed: {exc}") from exc
            return {"released": True, "changed": True, "removed": True,
                    "resource": resource, "verdict": verdict,
                    # The file is gone, so nothing is recorded against this
                    # resource any more. Reporting the pre-removal state here
                    # would describe a record the caller can no longer read.
                    "attachment_state": environment.UNATTACHED,
                    "note": ("the last reference was retired and nothing retains "
                             "the resource")}

        record["release_verdict"] = {
            "verdict": verdict["verdict"], "code": verdict["code"],
            "because": verdict["because"], "rule_from": verdict["rule_from"],
        }
        record["state"] = state
        record["orphaned"] = [
            {"scope": row["scope"], "holder": row["holder"],
             "why": row.get("why") or "the reference is still live"}
            for row in reference_rows if row["state"] != environment.LIVE]
        record["updated_at"] = _stamp(now)
        _atomic_write(path_for(self.root, resource), record)
        if verdict["verdict"] == environment.ALLOW:
            note = ("the reference is retired and the record is kept, because "
                    "something is still recorded against this resource")
        else:
            note = f"the reference is retired and the resource stays: {verdict['because']}"
        return {"released": True, "changed": True, "removed": False,
                "resource": resource, "verdict": verdict,
                "attachment_state": state,
                "orphaned": record["orphaned"], "note": note}

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _find(record: dict, *, scope: str, holder: str):
        for row in record["references"]:
            if row.get("scope") == scope and row.get("holder") == holder:
                return row
        return None

    @staticmethod
    def _retired_at(record: dict, *, scope: str, holder: str):
        """When this holder retired its reference, from the transition record.

        Read from `history` rather than from `references`, because a retired
        reference is deliberately not in `references` any more — the whole point
        of retiring it is that it stopped being a claim.
        """
        for row in reversed(record.get("history") or []):
            if (row.get("act") == "RELEASE" and row.get("scope") == scope
                    and row.get("holder") == holder):
                return row.get("at")
        return None

    @staticmethod
    def _append_history(record: dict, act: str, *, scope, holder, at,
                        note, verdict) -> None:
        record.setdefault("history", []).append({
            "at": _stamp(at), "act": act, "scope": scope, "holder": holder,
            "note": note or "", "verdict": verdict,
        })
        del record["history"][:-HISTORY_LIMIT]
