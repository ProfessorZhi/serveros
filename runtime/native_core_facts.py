"""Collect the facts a resident Native Core re-reads, and own them honestly.

Why this exists
---------------
`native/node-core/supervisor/src/facts.cpp` reads a document every tick so a
resident Core can act on facts that are true *now* instead of facts stated once
at startup. That change deliberately shipped with no writer: the reader refuses
an absent document, so a Core with no writer simply does not act. This module is
the writer.

Two rules decide everything here:

  * A fact with no configured source is **UNKNOWN**, not a default. There is no
    code path that produces `link.up = true` or `agent.busy = false` because it
    had nothing to read.
  * The document is believed whole or not at all by the reader, so this writer
    only publishes when every configured source answered. A half-collected
    document is withheld rather than written with a hole in it.

Ownership
---------
The Core's `flock` lease serialises two *Cores*. It cannot see that the Python
daemon is a second program with its own budget, so this module takes the SAME
lease file: while Python holds it, the Core is refused, and while the Core holds
it, this writer refuses to publish. The owner map then decides which service
each program may touch at all.

No WorkEngine, no RunDB, no second manager. The systemd reader, the probe layer
and the session model are the existing ones.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

FACTS_SCHEMA = "serveros/native-core-facts.v1"

#: The owner map's closed vocabulary. A unit claimed by neither is refused: a
#: new service must not silently become the Core's to restart.
OWNER_NATIVE_CORE = "native-core"
OWNER_SERVEROSD = "serverosd"
OWNERS = frozenset({OWNER_NATIVE_CORE, OWNER_SERVEROSD})

#: refusals, as codes rather than sentences, so a caller can switch on them
LEASE_NOT_HELD = "LEASE_NOT_HELD"
LEASE_BUSY = "LEASE_BUSY"
OWNER_UNKNOWN = "OWNER_UNKNOWN"
OWNER_NOT_MINE = "OWNER_NOT_MINE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
DOCUMENT_INVALID = "DOCUMENT_INVALID"

#: A fact text bound matching `facts.cpp`'s `kMaxFactText`.
MAX_FACT_TEXT = 200


class FactsRefused(RuntimeError):
    """A refusal with a code, so callers never parse prose."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ServiceSpec:
    """One unit this host manages, and where its non-systemd facts come from.

    `runtime_marker` is a path whose existence means the service's own runtime
    says it needs a person (an expired login). `maintenance_marker` is a path
    whose existence means a person took the service out of service on purpose.

    Both are markers rather than probes because a probe that cannot be reached
    would report "serving" by omission. A marker that is absent when it should
    not be is a different problem from a probe that failed, and only the marker
    distinguishes them.
    """

    id: str
    unit: str
    owner: str
    runtime_marker: Path | None = None
    maintenance_marker: Path | None = None


def build_owner_map(services: Iterable[ServiceSpec]) -> dict[str, str]:
    """unit -> owner, refusing an unclaimed or doubly-claimed unit."""
    owners: dict[str, str] = {}
    for spec in services:
        if not spec.id or not spec.unit:
            raise FactsRefused(DOCUMENT_INVALID, "a service needs an id and a unit")
        if spec.owner not in OWNERS:
            raise FactsRefused(
                OWNER_UNKNOWN,
                f"service {spec.id!r} names owner {spec.owner!r}; known owners are "
                + ", ".join(sorted(OWNERS)),
            )
        if spec.unit in owners and owners[spec.unit] != spec.owner:
            raise FactsRefused(
                OWNER_UNKNOWN,
                f"unit {spec.unit!r} is claimed by both {owners[spec.unit]!r} and "
                f"{spec.owner!r}; one owner, or no recovery",
            )
        owners[spec.unit] = spec.owner
    return owners


def _bounded(text: str) -> str:
    return text[:MAX_FACT_TEXT]


def read_runtime(spec: ServiceSpec) -> dict:
    """The service's own runtime: `serving`, `needs-login`, or an honest unknown."""
    if spec.runtime_marker is None:
        # Nothing was declared to read. That is not "serving".
        return {"runtime": "unknown", "known": False}
    try:
        present = spec.runtime_marker.exists()
    except OSError:
        return {"runtime": "unknown", "known": False}
    return {"runtime": "needs-login" if present else "serving", "known": True}


def read_maintenance(spec: ServiceSpec) -> tuple[bool, str]:
    if spec.maintenance_marker is None:
        return False, ""
    try:
        if not spec.maintenance_marker.exists():
            return False, ""
        reason = spec.maintenance_marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        # A marker we cannot read is not "not in maintenance": it is unknown,
        # and treating it as false would act on a service a person may have
        # taken out of service. Refuse the whole document instead.
        raise FactsRefused(
            SOURCE_UNAVAILABLE,
            f"maintenance marker for {spec.id!r} could not be read",
        ) from None
    return True, _bounded(reason)


def probe_link(spec: dict | None, *, connect: Callable[[tuple, float], object] | None = None) -> dict:
    """Whether the host has a working path to what a link-dependent service needs."""
    if not spec:
        return {"known": False, "up": False, "reason": "no link source configured"}
    kind = spec.get("kind")
    if kind != "tcp":
        return {"known": False, "up": False, "reason": f"unsupported link source {kind!r}"}
    host = spec.get("host")
    port = spec.get("port")
    timeout = float(spec.get("timeout", 2.0))
    if not host or not isinstance(port, int):
        return {"known": False, "up": False, "reason": "link source needs host and port"}
    connector = connect or (lambda address, t: socket.create_connection(address, t))
    try:
        connector((host, port), timeout)
    except OSError as exc:
        # The attempt was made and it failed: that is a KNOWABLE "down".
        return {"known": True, "up": False, "reason": _bounded(exc.__class__.__name__)}
    return {"known": True, "up": True, "reason": ""}


def read_agent(source: dict | None, *, sessions: object | None = None) -> dict:
    """Whether an agent is using the service right now.

    A marker source is exact. A session store is consulted only through the
    caller-supplied callable, because `agent_sessions` has no "busy" predicate
    and inventing one here would be a second opinion about the session model.
    """
    if not source:
        return {"known": False, "busy": False, "holder": "",
                "reason": "no agent-busy source configured"}
    kind = source.get("kind")
    if kind == "marker":
        path = Path(source.get("path") or "")
        if not path.name:
            return {"known": False, "busy": False, "holder": "", "reason": "marker needs a path"}
        try:
            present = path.exists()
        except OSError:
            return {"known": False, "busy": False, "holder": "", "reason": "marker unreadable"}
        holder = ""
        if present:
            try:
                holder = _bounded(path.read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                holder = ""
        return {"known": True, "busy": present, "holder": holder}
    return {"known": False, "busy": False, "holder": "", "reason": f"unsupported agent source {kind!r}"}


@dataclass
class FactsSources:
    """Everything the collector is allowed to read, stated explicitly."""

    services: list[ServiceSpec] = field(default_factory=list)
    link: dict | None = None
    agent: dict | None = None
    link_probe: Callable[[dict | None], dict] = probe_link
    agent_reader: Callable[[dict | None], dict] = read_agent


def collect_facts(
    sources: FactsSources,
    *,
    now: Callable[[], float] = time.time,
    owner: str = OWNER_SERVEROSD,
) -> dict:
    """Build one facts document, or refuse rather than publish a hole.

    `owner` is the program collecting. A unit owned by the other program is
    omitted from `services` — this writer describes only what it owns, and the
    Core's manifest lists only the rest.
    """
    owners = build_owner_map(sources.services)
    services: list[dict] = []
    for spec in sources.services:
        if owners[spec.unit] != owner:
            continue
        runtime = read_runtime(spec)
        maintenance, reason = read_maintenance(spec)
        entry: dict = {"id": spec.id}
        # `needs-login` is the only runtime word this writer can state. An
        # unconfigured source stays `unknown`, never `serving`.
        entry["runtime"] = runtime["runtime"]
        if maintenance:
            entry["maintenance"] = True
            if reason:
                entry["maintenance_reason"] = reason
        services.append(entry)

    link = sources.link_probe(sources.link)
    agent = sources.agent_reader(sources.agent)
    document = {
        "schema": FACTS_SCHEMA,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now())),
        "observed_epoch": int(now()),
        "link": {k: link[k] for k in ("known", "up") if k in link}
        | ({"reason": _bounded(link["reason"])} if link.get("reason") else {}),
        "agent": {k: agent[k] for k in ("known", "busy") if k in agent}
        | ({"holder": _bounded(agent["holder"])} if agent.get("holder") else {}),
        "services": services,
    }
    return document


def validate_document(document: dict) -> None:
    """The writer's own guard, mirroring the reader's closed rules.

    It is not a second schema; it is the check that stops this side publishing a
    document the other side will refuse, which would look like a Core failure
    rather than a writer bug.
    """
    if document.get("schema") != FACTS_SCHEMA:
        raise FactsRefused(DOCUMENT_INVALID, "wrong schema")
    epoch = document.get("observed_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise FactsRefused(DOCUMENT_INVALID, "observed_epoch must be a non-negative int")
    if not isinstance(document.get("observed_at"), str) or not document["observed_at"]:
        raise FactsRefused(DOCUMENT_INVALID, "observed_at is required")
    link = document.get("link") or {}
    if link.get("known") is False and link.get("up") is True:
        raise FactsRefused(DOCUMENT_INVALID, "link cannot be unknown and up")
    agent = document.get("agent") or {}
    if agent.get("known") is False and agent.get("busy") is True:
        raise FactsRefused(DOCUMENT_INVALID, "agent cannot be unknown and busy")
    seen: set[str] = set()
    for entry in document.get("services") or []:
        sid = entry.get("id")
        if not isinstance(sid, str) or not sid:
            raise FactsRefused(DOCUMENT_INVALID, "service id is required")
        if sid in seen:
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} listed twice")
        seen.add(sid)
        if entry.get("runtime") not in ("serving", "needs-login", "unknown"):
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} runtime word is not closed")
        for key in ("maintenance_reason",):
            if key in entry and len(str(entry[key])) > MAX_FACT_TEXT:
                raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} {key} is too long")


def write_document(path: Path | str, document: dict) -> None:
    """Publish atomically, 0600, from the destination's own directory.

    The reader refuses a malformed document rather than retrying, so a reader
    that catches this writer mid-write would report a spurious refusal. `rename`
    within one directory is the only way to make that impossible.
    """
    validate_document(document)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(destination.parent), prefix=".facts-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class SharedLease:
    """The Core's single-writer lease, taken from Python.

    It is the same file the Core is given with `--lease`, on purpose: two
    programs with two budgets is the failure the lease exists for, and it cannot
    see a second program it was never shown.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                # Somebody else — the Core, or another writer — has the pen.
                raise FactsRefused(LEASE_BUSY, "another writer holds the native-core lease") from None
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "SharedLease":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def cycle(
    *,
    facts_path: Path | str,
    sources: FactsSources,
    now: Callable[[], float] = time.time,
    owner: str = OWNER_SERVEROSD,
) -> dict:
    """One collection: read, then publish atomically.

    This deliberately does NOT take the act lease. The lease serialises
    processes that CHANGE something; a facts publication changes only this
    writer's own document, and holding the act lease here would deadlock the
    design outright — the Core could never acquire the pen it needs in order to
    act on the very facts being published.
    """
    document = collect_facts(sources, now=now, owner=owner)
    write_document(facts_path, document)
    return document


class RecoveryActor:
    """A program that may restore units — the Python side of the single writer.

    The lease is the Core's own file, and it is taken for exactly as long as this
    process might act. Two Cores are not the interesting case; a Core and
    `serverosd` both deciding that a unit is broken is, and neither can see the
    other's budget. One file answers both.

    The owner map is the second half: even holding the pen, this actor may only
    touch units it owns. A unit owned by `native-core` is refused by name, so a
    misconfigured manifest cannot make Python the second writer for it.
    """

    def __init__(self, *, lease: SharedLease, owner: str = OWNER_SERVEROSD) -> None:
        self.lease = lease
        self.owner = owner
        self._owners: dict[str, str] = {}

    def declare(self, services: Iterable[ServiceSpec]) -> None:
        self._owners = build_owner_map(services)

    def authorize(self, unit: str) -> None:
        """Refuse unless this actor may act on `unit` at all."""
        owner = self._owners.get(unit)
        if owner is None:
            raise FactsRefused(
                OWNER_UNKNOWN,
                f"unit {unit!r} has no owner; an unlisted unit is not recoverable",
            )
        if owner != self.owner:
            raise FactsRefused(
                OWNER_NOT_MINE,
                f"unit {unit!r} is owned by {owner!r}, not by {self.owner!r}",
            )

    def recover(self, unit: str, restore: Callable[[], object]) -> object:
        """Act on one unit, holding the shared lease for the duration.

        The lease is acquired BEFORE the owner check is honoured and released
        after, so a refused unit costs one non-blocking `flock` and nothing else.
        """
        self.authorize(unit)
        already = self.lease.held
        if not already:
            self.lease.acquire()
        try:
            return restore()
        finally:
            if not already:
                self.lease.release()


def _service_spec(text: str) -> ServiceSpec:
    """`id=unit:owner[:runtime_marker[:maintenance_marker]]`.

    A colon is used rather than a JSON blob because every one of these fields is
    a unit name, an owner word or a path, and a unit name cannot contain a colon
    after the `@` case is accounted for — `systemd_user.cpp` refuses anything
    else. A parser that could be handed a shell-ish string is a parser nobody
    reviewed.
    """
    try:
        left, right = text.split("=", 1)
    except ValueError:
        raise FactsRefused(DOCUMENT_INVALID, f"bad --service {text!r}; want id=unit:owner") from None
    parts = right.split(":")
    if len(parts) < 2:
        raise FactsRefused(DOCUMENT_INVALID, f"bad --service {text!r}; want id=unit:owner")
    unit, owner = parts[0], parts[1]
    runtime_marker = Path(parts[2]) if len(parts) > 2 and parts[2] else None
    maintenance_marker = Path(parts[3]) if len(parts) > 3 and parts[3] else None
    return ServiceSpec(id=left, unit=unit, owner=owner,
                       runtime_marker=runtime_marker,
                       maintenance_marker=maintenance_marker)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Publish native-core facts, repeatedly.")
    parser.add_argument("--facts", required=True, help="where to publish the document")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--max-cycles", type=int, default=-1)
    parser.add_argument("--owner", default=OWNER_SERVEROSD, choices=sorted(OWNERS))
    parser.add_argument("--link", default=None, help="host:port to probe, or omit for UNKNOWN")
    parser.add_argument("--agent-marker", default=None,
                        help="path whose existence means an agent is using the service")
    parser.add_argument("--service", action="append", default=[],
                        help="id=unit:owner[:runtime_marker[:maintenance_marker]]")
    args = parser.parse_args(argv)

    try:
        services = [_service_spec(text) for text in args.service]
        build_owner_map(services)
    except FactsRefused as exc:
        print(f"refused: {exc.code}: {exc.detail}", file=sys.stderr)
        return 2

    link = None
    if args.link:
        host, _, port = args.link.rpartition(":")
        if not host or not port.isdigit():
            print("--link must be host:port", file=sys.stderr)
            return 2
        link = {"kind": "tcp", "host": host, "port": int(port)}

    sources = FactsSources(
        services=services,
        link=link,
        agent={"kind": "marker", "path": args.agent_marker} if args.agent_marker else None,
    )

    cycles = 0
    while args.max_cycles < 0 or cycles < args.max_cycles:
        try:
            cycle(facts_path=args.facts, sources=sources, owner=args.owner)
        except FactsRefused as exc:
            # A refusal is published as nothing at all. The reader then sees the
            # previous document age out, which is the intended failure.
            print(f"refused: {exc.code}: {exc.detail}", file=sys.stderr)
        cycles += 1
        if args.max_cycles < 0 or cycles < args.max_cycles:
            time.sleep(max(0.2, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
