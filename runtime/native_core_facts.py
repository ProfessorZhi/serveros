"""Collect the facts a resident Native Core re-reads, and own them honestly.

Why this exists
---------------
`native/node-core/supervisor/src/facts.cpp` reads a document every tick so a
resident Core can act on facts that are true *now* instead of facts stated once
at startup. That change deliberately shipped with no writer; this is the writer.

Three rules decide everything here:

  * A fact with no configured source is **UNKNOWN**, not a default. There is no
    path that produces `link.up = true` or `agent.busy = false` because it had
    nothing to read.
  * The reader believes a document whole or not at all, so this writer publishes
    only what it can defend, and refuses to publish a hole.
  * **Observation is not action.** The facts writer does not take the act lease:
    holding it would stop the Core from ever acquiring the pen it needs to act
    on the very facts being published. The lease guards programs that CHANGE
    something, and `RecoveryActor` is the Python side of it.

Ownership
---------
The Core's `flock` lease serialises two *Cores*. It cannot see that the Python
daemon is a second program with its own budget, so both take the SAME lease file
for the duration of an effect. An owner map (unit -> `native-core` | `serverosd`)
is written to disk and read by **both** programs, so the split is machine-checked
on each side rather than being a review rule.

Nothing here creates a WorkEngine, a RunDB or a second manager.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import socket
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

FACTS_SCHEMA = "serveros/native-core-facts.v1"
OWNER_MAP_SCHEMA = "serveros/native-core-owner-map.v1"

OWNER_NATIVE_CORE = "native-core"
OWNER_SERVEROSD = "serverosd"
OWNERS = frozenset({OWNER_NATIVE_CORE, OWNER_SERVEROSD})

LEASE_NOT_HELD = "LEASE_NOT_HELD"
LEASE_BUSY = "LEASE_BUSY"
LEASE_UNSAFE = "LEASE_UNSAFE"
OWNER_UNKNOWN = "OWNER_UNKNOWN"
OWNER_NOT_MINE = "OWNER_NOT_MINE"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
DOCUMENT_INVALID = "DOCUMENT_INVALID"
PATH_UNSAFE = "PATH_UNSAFE"

#: Matches `facts.cpp`'s `kMaxFactText`: the bound is on UTF-8 BYTES because that
#: is what the C++ side measures. A character count here would let a multi-byte
#: string through and be refused on the far side.
MAX_FACT_TEXT = 200
MAX_SERVICES = 64

#: Exactly the keys `facts.cpp#parse_service` accepts. `unit` is NOT one of
#: them: the C++ reader refuses an unknown service field, so emitting one
#: would make this writer produce a document the reader rejects.
SERVICE_KEYS = frozenset({"id", "runtime", "maintenance", "maintenance_reason"})
RUNTIME_WORDS = frozenset({"serving", "needs-login", "unknown"})


class FactsRefused(RuntimeError):
    """A refusal with a code, so callers never parse prose."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class ServiceSpec:
    id: str
    unit: str
    owner: str
    runtime_marker: Path | None = None
    maintenance_marker: Path | None = None


def build_owner_map(services: Iterable[ServiceSpec]) -> dict[str, str]:
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


# --------------------------------------------------------------------------
# paths: nothing here may be redirected by a symlink or a traversal
# --------------------------------------------------------------------------


def _reject_traversal(path: Path) -> None:
    if any(part == ".." for part in path.parts):
        raise FactsRefused(PATH_UNSAFE, f"{path} contains a traversal component")


def _trusted_dir(info: os.stat_result) -> bool:
    """The same rule `state_directory.cpp` applies.

    A directory we own is fine. A writable one is fine only with the sticky bit
    AND a trusted owner: sticky is what stops another principal replacing the
    entry inside it. `/tmp` passes; a 0777 scratch directory does not.
    """
    trusted_owner = info.st_uid in (os.geteuid(), 0)
    if not trusted_owner:
        return False
    if info.st_mode & 0o022:
        return bool(info.st_mode & stat.S_ISVTX)
    return True


def open_secure_dir(path: Path | str, *, create: bool = False) -> int:
    """Walk `path` one component at a time with `O_NOFOLLOW`, return a held fd.

    The check is per component, from the root down, on a descriptor that is
    already open — so a symlink anywhere in the chain is refused at the moment
    it is traversed, and a directory swapped in after the check cannot redirect
    the write. `create` makes the FINAL component only after every ancestor has
    been verified, via `mkdirat` on the verified parent.

    A relative path is refused: resolving it is the caller's ambiguity, and a
    path resolved twice is two different paths.
    """
    target = Path(path)
    _reject_traversal(target)
    if not target.is_absolute():
        raise FactsRefused(PATH_UNSAFE, f"{target} is not absolute")
    parts = [part for part in target.parts if part not in ("/", "")]
    if not parts:
        raise FactsRefused(PATH_UNSAFE, f"{target} names no directory")

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    current = os.open("/", flags)
    try:
        for index, component in enumerate(parts):
            last = index == len(parts) - 1
            try:
                child = os.open(component, flags, dir_fd=current)
            except FileNotFoundError:
                if not (last and create):
                    raise FactsRefused(PATH_UNSAFE, f"{component} does not exist under {target}") from None
                os.mkdir(component, 0o700, dir_fd=current)
                child = os.open(component, flags, dir_fd=current)
            except OSError as exc:
                # ELOOP here is a symlink, caught by O_NOFOLLOW.
                raise FactsRefused(PATH_UNSAFE, f"{component}: {exc.strerror}") from None
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise FactsRefused(PATH_UNSAFE, f"{target} component {component} is not a directory")
            if not _trusted_dir(info):
                os.close(child)
                raise FactsRefused(PATH_UNSAFE, f"{target} component {component} is not a trusted directory")
            os.close(current)
            current = child
        handle = current
        current = -1
        return handle
    finally:
        if current != -1:
            os.close(current)


def secure_parent(path: Path | str) -> int:
    """The parent directory of `path` as a held fd, refusing an escape.

    Kept as the name callers already use; it now returns a descriptor rather
    than a path, because a checked path is not a checked target.
    """
    destination = Path(path)
    _reject_traversal(destination)
    return open_secure_dir(destination.parent, create=True)


def _bounded(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_FACT_TEXT:
        return text
    return encoded[:MAX_FACT_TEXT].decode("utf-8", "ignore")


# --------------------------------------------------------------------------
# fact readers
# --------------------------------------------------------------------------


def read_runtime(spec: ServiceSpec) -> dict:
    """The service's own runtime: `serving`, `needs-login`, or an honest unknown."""
    if spec.runtime_marker is None:
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
        # Unreadable is not "not in maintenance". Treating it as false would
        # bring back a service a person may have taken out of service.
        raise FactsRefused(
            SOURCE_UNAVAILABLE, f"maintenance marker for {spec.id!r} could not be read"
        ) from None
    return True, _bounded(reason)


def probe_link(spec: dict | None, *, connect: Callable[[tuple, float], object] | None = None) -> dict:
    if not spec:
        return {"known": False, "up": False, "reason": "no link source configured"}
    kind = spec.get("kind")
    if kind != "tcp":
        return {"known": False, "up": False, "reason": f"unsupported link source {kind!r}"}
    host, port = spec.get("host"), spec.get("port")
    timeout = float(spec.get("timeout", 2.0))
    if not host or not isinstance(port, int):
        return {"known": False, "up": False, "reason": "link source needs host and port"}
    connector = connect or (lambda address, t: socket.create_connection(address, t))
    try:
        connector((host, port), timeout)
    except OSError as exc:
        # The attempt WAS made and it failed: a knowable DOWN, not ignorance.
        return {"known": True, "up": False, "reason": _bounded(exc.__class__.__name__)}
    return {"known": True, "up": True, "reason": ""}


def _runtime_module(name: str, filename: str):
    """The canonical loader idiom `runtime/daemon.py` uses.

    Imported by path rather than by name so this module works whether it is run
    as a script, imported by the daemon, or loaded from a revision directory. A
    plain `import probes` finds nothing: the runtime modules are not a package.
    """
    import importlib.util
    import sys

    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    if spec is None or spec.loader is None:
        raise FactsRefused(SOURCE_UNAVAILABLE, f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _ancestor_pids() -> set[int]:
    """Every process that spawned this one, up to init.

    `pgrep -f` matches command lines, so a wrapper shell, a test runner or a
    `systemd-run` invocation that merely QUOTES the pattern matches itself. A
    process that spawned this writer is not a user of the service it is
    watching, and treating it as one is a permanent false BUSY.
    """
    seen: set[int] = set()
    pid = os.getpid()
    for _ in range(64):
        try:
            with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as handle:
                parent = None
                for line in handle:
                    if line.startswith("PPid:"):
                        parent = int(line.split()[1])
                        break
        except (OSError, ValueError, IndexError):
            break
        if parent is None or parent <= 1 or parent in seen or parent == pid:
            break
        seen.add(parent)
        pid = parent
    return seen


def _default_process_pids(pattern: str) -> list[int]:
    """The real host reader the daemon's probes already use (`probes.LiveHost`).

    Ancestors are removed because they match by quoting the pattern, not by
    being the thing the pattern describes.
    """
    probes = _runtime_module("serveros_runtime_probes", "probes.py")
    found = probes.LiveHost().process_pids(pattern)
    ancestors = _ancestor_pids()
    return [pid for pid in found if pid not in ancestors]


def read_agent(source: dict | None, *, process_pids: Callable[[str], list[int]] | None = None) -> dict:
    """Whether an agent is using the service right now.

    Three sources, in order of directness:

      `process` — a read-only process-name observation. CONSERVATIVE: any matching
                  process counts as busy, including one unrelated to this service,
                  because the safe error for "do not interrupt an agent" is to
                  think one is present.
      `session` — a callable supplied by the caller, for a typed status surface.
      `marker`  — an explicit file. Exact, but only as current as its producer; a
                  marker with no producer is a claim nobody refreshes, so a
                  missing marker directory is UNKNOWN rather than idle.

    Anything else, or an observation that cannot be made, is UNKNOWN.
    """
    if not source:
        return {"known": False, "busy": False, "holder": "",
                "reason": "no agent-busy source configured"}
    kind = source.get("kind")

    if kind == "process":
        pattern = source.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return {"known": False, "busy": False, "holder": "", "reason": "process source needs a pattern"}
        reader = process_pids or _default_process_pids
        try:
            pids = reader(pattern)
        except Exception:  # noqa: BLE001 - an unmeasurable host is UNKNOWN, not idle
            return {"known": False, "busy": False, "holder": "",
                    "reason": "process observation unavailable"}
        return {"known": True, "busy": bool(pids), "holder": f"{len(pids)} process(es)" if pids else ""}

    if kind == "session":
        resolver = source.get("resolve")
        if not callable(resolver):
            return {"known": False, "busy": False, "holder": "", "reason": "session source needs a resolver"}
        try:
            answer = resolver()
        except Exception:  # noqa: BLE001
            return {"known": False, "busy": False, "holder": "", "reason": "session source failed"}
        if not isinstance(answer, dict) or "busy" not in answer:
            return {"known": False, "busy": False, "holder": "", "reason": "session source gave no busy fact"}
        busy = bool(answer["busy"])
        return {"known": True, "busy": busy,
                "holder": _bounded(str(answer.get("holder") or "")) if busy else ""}

    if kind == "marker":
        path = Path(source.get("path") or "")
        if not path.name:
            return {"known": False, "busy": False, "holder": "", "reason": "marker needs a path"}
        try:
            parent = os.lstat(path.parent)
        except OSError:
            # The producer's directory is gone. That is not evidence that nobody
            # is busy; it is evidence that nobody is maintaining this marker.
            return {"known": False, "busy": False, "holder": "",
                    "reason": "marker directory is unreadable"}
        if not stat.S_ISDIR(parent.st_mode):
            return {"known": False, "busy": False, "holder": "", "reason": "marker parent is not a directory"}
        try:
            present = path.exists()
        except OSError:
            return {"known": False, "busy": False, "holder": "", "reason": "marker unreadable"}
        holder = ""
        if present:
            try:
                holder = _bounded(path.read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                return {"known": False, "busy": False, "holder": "", "reason": "marker unreadable"}
        return {"known": True, "busy": present, "holder": holder}

    return {"known": False, "busy": False, "holder": "", "reason": f"unsupported agent source {kind!r}"}


@dataclass
class FactsSources:
    services: list[ServiceSpec] = field(default_factory=list)
    link: dict | None = None
    agent: dict | None = None
    link_probe: Callable[[dict | None], dict] = probe_link
    agent_reader: Callable[[dict | None], dict] = read_agent


def collect_facts(
    sources: FactsSources,
    *,
    now: Callable[[], float] = time.time,
) -> dict:
    """Build one facts document.

    Every managed service is described, INCLUDING units owned by the other
    program: the Core needs `needs-login` and `maintenance` for the services it
    acts on, and those services are the ones Python may not act on. Filtering
    them out of the document would leave the Core with `unknown` runtime for
    exactly the units it owns — and an unknown runtime is recoverable.
    """
    build_owner_map(sources.services)
    services: list[dict] = []
    for spec in sources.services:
        runtime = read_runtime(spec)
        maintenance, reason = read_maintenance(spec)
        entry: dict = {"id": spec.id, "runtime": runtime["runtime"]}
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
    """The writer's guard, mirroring `facts.cpp`'s closed rules exactly.

    Not a second schema: it is the check that stops this side publishing a
    document the C++ reader will refuse, which would look like a Core failure
    rather than a writer bug. Field sets, bool types and UTF-8 byte lengths are
    enforced here because the C++ side enforces them there.
    """
    if not isinstance(document, dict):
        raise FactsRefused(DOCUMENT_INVALID, "document is not an object")
    if set(document) - {"schema", "observed_at", "observed_epoch", "link", "agent", "services"}:
        raise FactsRefused(DOCUMENT_INVALID, "unknown top-level field")
    if document.get("schema") != FACTS_SCHEMA:
        raise FactsRefused(DOCUMENT_INVALID, "wrong schema")
    epoch = document.get("observed_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise FactsRefused(DOCUMENT_INVALID, "observed_epoch must be a non-negative int")
    observed_at = document.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        raise FactsRefused(DOCUMENT_INVALID, "observed_at is required")
    if len(observed_at.encode("utf-8")) > MAX_FACT_TEXT:
        raise FactsRefused(DOCUMENT_INVALID, "observed_at exceeds its byte bound")

    link = document.get("link")
    if not isinstance(link, dict):
        raise FactsRefused(DOCUMENT_INVALID, "link is required")
    if set(link) - {"known", "up", "reason"}:
        raise FactsRefused(DOCUMENT_INVALID, "unknown link field")
    for key in ("known", "up"):
        if not isinstance(link.get(key), bool):
            raise FactsRefused(DOCUMENT_INVALID, f"link.{key} must be a bool")
    if link["known"] is False and link["up"] is True:
        raise FactsRefused(DOCUMENT_INVALID, "link cannot be unknown and up")
    if "reason" in link:
        if not isinstance(link["reason"], str):
            raise FactsRefused(DOCUMENT_INVALID, "link.reason must be a string")
        if len(link["reason"].encode("utf-8")) > MAX_FACT_TEXT:
            raise FactsRefused(DOCUMENT_INVALID, "link.reason exceeds its byte bound")

    agent = document.get("agent")
    if not isinstance(agent, dict):
        raise FactsRefused(DOCUMENT_INVALID, "agent is required")
    if set(agent) - {"known", "busy", "holder"}:
        raise FactsRefused(DOCUMENT_INVALID, "unknown agent field")
    for key in ("known", "busy"):
        if not isinstance(agent.get(key), bool):
            raise FactsRefused(DOCUMENT_INVALID, f"agent.{key} must be a bool")
    if agent["known"] is False and agent["busy"] is True:
        raise FactsRefused(DOCUMENT_INVALID, "agent cannot be unknown and busy")
    if "holder" in agent:
        if not isinstance(agent["holder"], str):
            raise FactsRefused(DOCUMENT_INVALID, "agent.holder must be a string")
        if len(agent["holder"].encode("utf-8")) > MAX_FACT_TEXT:
            raise FactsRefused(DOCUMENT_INVALID, "agent.holder exceeds its byte bound")

    services = document.get("services")
    if not isinstance(services, list):
        raise FactsRefused(DOCUMENT_INVALID, "services must be a list")
    if len(services) > MAX_SERVICES:
        raise FactsRefused(DOCUMENT_INVALID, "too many services")
    seen: set[str] = set()
    for entry in services:
        if not isinstance(entry, dict):
            raise FactsRefused(DOCUMENT_INVALID, "service entry is not an object")
        if set(entry) - SERVICE_KEYS:
            raise FactsRefused(DOCUMENT_INVALID, "unknown service field")
        sid = entry.get("id")
        if not isinstance(sid, str) or not sid:
            raise FactsRefused(DOCUMENT_INVALID, "service id is required")
        if len(sid.encode("utf-8")) > MAX_FACT_TEXT:
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} id exceeds its byte bound")
        if sid in seen:
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} listed twice")
        seen.add(sid)
        if entry.get("runtime") not in RUNTIME_WORDS:
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} runtime word is not closed")
        if "maintenance" in entry and not isinstance(entry["maintenance"], bool):
            raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} maintenance must be a bool")
        if "maintenance_reason" in entry:
            # `null` is not "absent" to the C++ reader: it checks the type of
            # every field that is present. An explicit null is therefore a
            # document the far side refuses, so it is refused here first.
            reason = entry["maintenance_reason"]
            if not isinstance(reason, str):
                raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} reason must be a string")
            if len(reason.encode("utf-8")) > MAX_FACT_TEXT:
                raise FactsRefused(DOCUMENT_INVALID, f"service {sid!r} reason exceeds its byte bound")


def publish_facts(path: Path | str, document: dict) -> None:
    """Validate, then publish atomically inside a verified directory fd.

    Every operation is relative to the descriptor, so the directory that was
    checked is the directory that is written to. A path re-resolved between the
    check and the write is exactly the escape this refuses.
    """
    validate_document(document)
    destination = Path(path)
    _reject_traversal(destination)
    name = destination.name
    if not name:
        raise FactsRefused(PATH_UNSAFE, "facts path names no file")
    parent_fd = secure_parent(destination)
    temporary = f".facts-{os.getpid()}-{os.urandom(6).hex()}"
    try:
        # Refuse to replace a symlink: `rename` would clobber the link's target
        # path rather than the link, which is a write somewhere else.
        try:
            existing = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and stat.S_ISLNK(existing.st_mode):
            raise FactsRefused(PATH_UNSAFE, f"{destination} is a symlink")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    finally:
        os.close(parent_fd)


# Backwards-compatible name used by earlier callers/tests.
write_document = publish_facts


# --------------------------------------------------------------------------
# the single-writer lease
# --------------------------------------------------------------------------


class SafeLease:
    """The Core's single-writer lease, taken without being redirectable.

    `flock` on an `O_CREAT` open is not enough on its own: if the path is a
    symlink, two owners can each "hold" a different inode and both believe they
    have the pen. So the open refuses a symlink, and the resulting descriptor is
    checked to be a regular file this user owns, with one link, not writable by
    anyone else — and then re-bound to the path to prove the fd IS that path.
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
        _reject_traversal(self.path)
        parent_fd = secure_parent(self.path)
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path.name, flags, 0o600, dir_fd=parent_fd)
            except OSError as exc:
                raise FactsRefused(LEASE_UNSAFE, f"lease could not be opened: {exc.strerror}") from None
            try:
                self._check_descriptor(fd, parent_fd)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        raise FactsRefused(LEASE_BUSY, "another writer holds the native-core lease") from None
                    raise
            except BaseException:
                os.close(fd)
                raise
        finally:
            os.close(parent_fd)
        self._fd = fd

    def _check_descriptor(self, fd: int, parent_fd: int) -> None:
        """Everything that makes this descriptor a safe lease, checked anew.

        Called at acquire AND again immediately before every effect. Between
        those two moments the path can be renamed away and replaced, and a
        replacement inode is a second pen: the first writer still holds a lock
        on a file nobody else can find, and the second holds one on the file
        everybody finds.
        """
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise FactsRefused(LEASE_UNSAFE, "lease is not a regular file")
        if info.st_uid != os.geteuid():
            raise FactsRefused(LEASE_UNSAFE, f"lease is owned by uid {info.st_uid}")
        if info.st_nlink != 1:
            raise FactsRefused(LEASE_UNSAFE, f"lease has {info.st_nlink} hard links")
        if info.st_mode & 0o022:
            raise FactsRefused(LEASE_UNSAFE, "lease is group- or world-writable")
        try:
            on_path = os.stat(self.path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise FactsRefused(LEASE_UNSAFE, f"lease path vanished: {exc.strerror}") from None
        if stat.S_ISLNK(on_path.st_mode):
            raise FactsRefused(LEASE_UNSAFE, "lease path is a symlink")
        if (on_path.st_dev, on_path.st_ino) != (info.st_dev, info.st_ino):
            raise FactsRefused(
                LEASE_UNSAFE, "the lease path no longer names the file this descriptor holds"
            )

    def revalidate(self) -> None:
        """Re-check fd against the path, immediately before an effect.

        A held lock proves a descriptor, not a path. If the path was replaced
        after `acquire`, another writer can take the new inode while this one
        still reports `held` — so the claim is re-established rather than
        remembered.
        """
        if self._fd is None:
            raise FactsRefused(LEASE_NOT_HELD, "no lease is held")
        parent_fd = secure_parent(self.path)
        try:
            self._check_descriptor(self._fd, parent_fd)
        finally:
            os.close(parent_fd)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "SafeLease":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


#: The old name, kept so existing callers and the previous tests still import.
SharedLease = SafeLease


# --------------------------------------------------------------------------
# the shared owner map (read by both programs)
# --------------------------------------------------------------------------


class OwnerMap:
    """unit -> program, written by Python and read by the C++ Core as well."""

    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = dict(owners)

    @classmethod
    def from_services(cls, services: Iterable[ServiceSpec]) -> "OwnerMap":
        return cls(build_owner_map(services))

    @classmethod
    def load(cls, path: Path | str) -> "OwnerMap":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != OWNER_MAP_SCHEMA:
            raise FactsRefused(OWNER_UNKNOWN, "owner map names another schema")
        units = data.get("units")
        if not isinstance(units, dict):
            raise FactsRefused(OWNER_UNKNOWN, "owner map has no units")
        for unit, owner in units.items():
            if owner not in OWNERS:
                raise FactsRefused(OWNER_UNKNOWN, f"owner map names unknown owner {owner!r}")
        return cls(units)

    def write(self, path: Path | str) -> None:
        for unit, owner in self._owners.items():
            if owner not in OWNERS:
                raise FactsRefused(OWNER_UNKNOWN, f"unit {unit!r} has unknown owner {owner!r}")
        document = {"schema": OWNER_MAP_SCHEMA, "units": dict(sorted(self._owners.items()))}
        destination = Path(path)
        _reject_traversal(destination)
        parent_fd = secure_parent(destination)
        temporary = f".owner-map-{os.getpid()}-{os.urandom(6).hex()}"
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temporary, flags, 0o600, dir_fd=parent_fd)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.rename(temporary, destination.name,
                          src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except OSError:
                    pass
                raise
        finally:
            os.close(parent_fd)

    def owner_of(self, unit: str) -> str | None:
        return self._owners.get(unit)


# --------------------------------------------------------------------------
# acting
# --------------------------------------------------------------------------


def cycle(
    *,
    facts_path: Path | str,
    sources: FactsSources,
    now: Callable[[], float] = time.time,
) -> dict:
    """One collection and publication. Does not take the act lease."""
    document = collect_facts(sources, now=now)
    publish_facts(facts_path, document)
    return document


class RecoveryActor:
    """A program that may restore units — the Python side of the single writer."""

    def __init__(self, *, lease: SafeLease, owner: str = OWNER_SERVEROSD,
                 owner_map: OwnerMap | None = None) -> None:
        self.lease = lease
        self.owner = owner
        self._map = owner_map or OwnerMap({})

    def declare(self, services: Iterable[ServiceSpec]) -> None:
        self._map = OwnerMap.from_services(services)

    def authorize(self, unit: str) -> None:
        owner = self._map.owner_of(unit)
        if owner is None:
            raise FactsRefused(
                OWNER_UNKNOWN,
                f"unit {unit!r} has no owner; an unlisted unit is not recoverable",
            )
        if owner != self.owner:
            raise FactsRefused(
                OWNER_NOT_MINE, f"unit {unit!r} is owned by {owner!r}, not by {self.owner!r}"
            )

    def recover(self, unit: str, restore: Callable[[], object]) -> object:
        self.authorize(unit)
        already = self.lease.held
        if not already:
            self.lease.acquire()
        try:
            # The lock was taken for the descriptor, and the path may have been
            # replaced since. Re-establish the claim immediately before acting.
            self.lease.revalidate()
            return restore()
        finally:
            if not already:
                self.lease.release()


def _service_spec(text: str) -> ServiceSpec:
    try:
        left, right = text.split("=", 1)
    except ValueError:
        raise FactsRefused(DOCUMENT_INVALID, f"bad --service {text!r}; want id=unit:owner") from None
    parts = right.split(":")
    if len(parts) < 2:
        raise FactsRefused(DOCUMENT_INVALID, f"bad --service {text!r}; want id=unit:owner")
    unit, owner = parts[0], parts[1]
    return ServiceSpec(
        id=left, unit=unit, owner=owner,
        runtime_marker=Path(parts[2]) if len(parts) > 2 and parts[2] else None,
        maintenance_marker=Path(parts[3]) if len(parts) > 3 and parts[3] else None,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Publish native-core facts, repeatedly.")
    parser.add_argument("--facts", required=True)
    parser.add_argument("--owner-map", default=None)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--max-cycles", type=int, default=-1)
    parser.add_argument("--link", default=None, help="host:port to probe, or omit for UNKNOWN")
    parser.add_argument("--agent-marker", default=None)
    parser.add_argument("--agent-process", default=None,
                        help="process-name pattern whose presence means an agent is busy")
    parser.add_argument("--agent-process-file", default=None,
                        help="a file holding that pattern; use this when the pattern "
                             "itself would otherwise appear in this process's argv")
    parser.add_argument("--service", action="append", default=[])
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

    pattern = args.agent_process
    if args.agent_process_file:
        try:
            pattern = Path(args.agent_process_file).read_text(encoding="utf-8").strip()
        except OSError:
            print("--agent-process-file could not be read", file=sys.stderr)
            return 2
        if not pattern:
            print("--agent-process-file is empty", file=sys.stderr)
            return 2
    if pattern:
        agent = {"kind": "process", "pattern": pattern}
    elif args.agent_marker:
        agent = {"kind": "marker", "path": args.agent_marker}
    else:
        agent = None

    sources = FactsSources(services=services, link=link, agent=agent)

    if args.owner_map:
        try:
            OwnerMap.from_services(services).write(args.owner_map)
        except FactsRefused as exc:
            print(f"refused: {exc.code}: {exc.detail}", file=sys.stderr)
            return 2

    cycles = 0
    while args.max_cycles < 0 or cycles < args.max_cycles:
        try:
            cycle(facts_path=args.facts, sources=sources)
        except FactsRefused as exc:
            # Nothing is published. The reader then sees the previous document
            # age out, which is the intended failure rather than a hole.
            print(f"refused: {exc.code}: {exc.detail}", file=sys.stderr)
        cycles += 1
        if args.max_cycles < 0 or cycles < args.max_cycles:
            time.sleep(max(0.2, args.interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
