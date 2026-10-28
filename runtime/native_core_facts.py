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


def _check_owner_map_bounds(units: Iterable[str]) -> None:
    """The two bounds BOTH READERS enforce, applied to the thing that writes.

    `OwnerMap.load` (this file) and `load_owner_map` (the C++ Core,
    `native/node-core/supervisor/src/facts.cpp`) already refuse a map with more
    than `MAX_SERVICES` entries or a unit id longer than `MAX_FACT_TEXT`. The
    writer enforced neither, so it could emit a document its own reader would
    refuse — a map that exists and means nothing, discovered at the reader rather
    than at the write.

    Refused as `DOCUMENT_INVALID` rather than `OWNER_UNKNOWN`: the vocabulary is
    fine, the shape is not.
    """
    count = 0
    for unit in units:
        if not unit:
            raise FactsRefused(DOCUMENT_INVALID, "a unit with no name cannot own anything")
        if len(unit.encode("utf-8")) > MAX_FACT_TEXT:
            raise FactsRefused(
                DOCUMENT_INVALID,
                f"unit id {unit!r} exceeds its {MAX_FACT_TEXT}-byte bound; both readers "
                f"refuse this document, so writing it would produce a map nobody reads",
            )
        count += 1
    if count > MAX_SERVICES:
        raise FactsRefused(
            DOCUMENT_INVALID,
            f"the owner map names more than {MAX_SERVICES} units; both readers refuse "
            f"this document, so writing it would produce a map nobody reads",
        )


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
    _check_owner_map_bounds(owners)
    return owners


# --------------------------------------------------------------------------
# paths: nothing here may be redirected by a symlink or a traversal
# --------------------------------------------------------------------------


def _reject_traversal(path: Path) -> None:
    if any(part == ".." for part in path.parts):
        raise FactsRefused(PATH_UNSAFE, f"{path} contains a traversal component")


class _DirectoryBinding:
    """An owned no-follow FD chain, including any private writable tail."""
    def __init__(self, frames: list[tuple[int, str, os.stat_result]]) -> None:
        self.frames = frames
        self.principal = os.geteuid()

    @property
    def fd(self) -> int:
        return self.frames[-1][0]

    def validate(self) -> None:
        if os.geteuid() != self.principal or not self.frames:
            raise FactsRefused(PATH_UNSAFE, "directory principal or binding changed")
        previous = None
        for fd, name, captured in self.frames:
            current = os.fstat(fd)
            identity = lambda x: (x.st_dev, x.st_ino, x.st_uid, x.st_gid, x.st_mode)
            if identity(current) != identity(captured):
                raise FactsRefused(PATH_UNSAFE, "directory identity or permissions changed")
            if previous is not None:
                try:
                    entry = os.stat(name, dir_fd=previous, follow_symlinks=False)
                except OSError:
                    raise FactsRefused(PATH_UNSAFE, "directory namespace disappeared") from None
                if identity(entry) != identity(captured):
                    raise FactsRefused(PATH_UNSAFE, "directory namespace was replaced")
            previous = fd

    def close(self) -> None:
        frames, self.frames = self.frames, []
        for fd, _, _ in reversed(frames):
            os.close(fd)

    def detach_parent(self) -> int:
        fd = self.fd
        frames, self.frames = self.frames[:-1], []
        for ancestor, _, _ in reversed(frames):
            os.close(ancestor)
        return fd


def _open_directory_binding(path: Path | str, *, create: bool = False) -> _DirectoryBinding:
    target = Path(path)
    _reject_traversal(target)
    if not target.is_absolute():
        raise FactsRefused(PATH_UNSAFE, f"{target} is not absolute")
    parts = [part for part in target.parts if part not in ("/", "")]
    if not parts or len(parts) > 128:
        raise FactsRefused(PATH_UNSAFE, "directory path is empty or exceeds its 128-component budget")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    root = os.open("/", flags)
    frames = [(root, "", os.fstat(root))]
    private_tail = False
    principal = os.geteuid()
    try:
        for index, component in enumerate(parts):
            parent, _, parent_info = frames[-1]
            if parent_info.st_uid not in (principal, 0):
                raise FactsRefused(PATH_UNSAFE, "directory ancestor has another owner")
            if parent_info.st_uid == principal and not (parent_info.st_mode & 0o077):
                private_tail = True
            if parent_info.st_uid != principal:
                private_tail = False
            # A group-writable component is protected only behind the actual
            # current-user private barrier, or by trusted sticky ownership.
            if parent_info.st_mode & 0o022:
                sticky = bool(parent_info.st_mode & stat.S_ISVTX)
                if not (sticky or private_tail):
                    raise FactsRefused(PATH_UNSAFE, "writable ancestor has no private enclosure")
            try:
                entry = os.stat(component, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not (create and index == len(parts) - 1):
                    raise FactsRefused(PATH_UNSAFE, "directory ancestor is missing") from None
                os.mkdir(component, 0o700, dir_fd=parent)
                entry = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode) or entry.st_uid not in (principal, 0):
                raise FactsRefused(PATH_UNSAFE, "directory component is an alias or has another owner")
            if private_tail and entry.st_uid != principal:
                raise FactsRefused(PATH_UNSAFE, "private enclosure changed owner")
            try:
                child = os.open(component, flags, dir_fd=parent)
            except OSError as exc:
                raise FactsRefused(PATH_UNSAFE, "directory component could not be opened without following") from exc
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid, opened.st_mode) != (
                entry.st_dev, entry.st_ino, entry.st_uid, entry.st_gid, entry.st_mode
            ):
                os.close(child)
                raise FactsRefused(PATH_UNSAFE, "directory changed during open")
            frames.append((child, component, opened))
        leaf = frames[-1][2]
        if leaf.st_uid != principal:
            raise FactsRefused(PATH_UNSAFE, "destination directory belongs to another owner")
        if leaf.st_mode & 0o022 and not private_tail:
            raise FactsRefused(PATH_UNSAFE, "destination directory is writable without a private enclosure")
        binding = _DirectoryBinding(frames)
        binding.validate()
        return binding
    except BaseException as exc:
        for fd, _, _ in reversed(frames):
            os.close(fd)
        if isinstance(exc, OSError):
            raise FactsRefused(PATH_UNSAFE, "directory could not be opened consistently") from exc
        raise


def open_secure_dir(path: Path | str, *, create: bool = False) -> int:
    """Compatibility FD API; the caller owns the returned parent descriptor."""
    return _open_directory_binding(path, create=create).detach_parent()


def secure_parent(path: Path | str) -> int:
    destination = Path(path)
    _reject_traversal(destination)
    return open_secure_dir(destination.parent, create=True)


def _parent_binding(path: Path | str) -> _DirectoryBinding:
    destination = Path(path)
    _reject_traversal(destination)
    binding = _open_directory_binding(destination.parent, create=True)
    if os.fstat(binding.fd).st_mode & 0o022:
        binding.close()
        raise FactsRefused(PATH_UNSAFE, "write parent must be owned and nonshared")
    return binding


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


def _existing_file(parent: _DirectoryBinding, name: str) -> None:
    try:
        entry = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1 or entry.st_mode & 0o022):
        raise FactsRefused(PATH_UNSAFE, "destination is not an owned single-link private file")


def _publish_document(destination: Path, document: dict) -> None:
    parent = _parent_binding(destination)
    temporary = f".native-{os.getpid()}-{os.urandom(6).hex()}"
    try:
        parent.validate()
        _existing_file(parent, destination.name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600, dir_fd=parent.fd)
        created = os.fstat(fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            parent.validate()
            _existing_file(parent, destination.name)
            os.rename(temporary, destination.name, src_dir_fd=parent.fd, dst_dir_fd=parent.fd)
            os.fsync(parent.fd)
            parent.validate()
        except BaseException:
            try:
                parent.validate()
                current = os.stat(temporary, dir_fd=parent.fd, follow_symlinks=False)
                if (stat.S_ISREG(current.st_mode) and current.st_nlink == 1
                        and (current.st_dev, current.st_ino, current.st_uid, current.st_mode) ==
                            (created.st_dev, created.st_ino, created.st_uid, created.st_mode)):
                    os.unlink(temporary, dir_fd=parent.fd)
            except (FactsRefused, OSError):
                # Lost namespace/identity means this entry may belong to another
                # actor. Preserve it; refusal never authorizes unknown cleanup.
                pass
            raise
    finally:
        parent.close()


def publish_facts(path: Path | str, document: dict) -> None:
    """Validate and publish through the currently proven parent FD chain."""
    validate_document(document)
    _publish_document(Path(path), document)


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
        self._directory: _DirectoryBinding | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            self.revalidate()
            return
        parent = _parent_binding(self.path)
        fd = None
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                fd = os.open(self.path.name, flags, 0o600, dir_fd=parent.fd)
            except OSError as exc:
                raise FactsRefused(LEASE_UNSAFE, "lease could not be opened without following") from exc
            self._check_descriptor(fd, parent.fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    raise FactsRefused(LEASE_BUSY, "another writer holds the native-core lease") from None
                raise
            parent.validate()
            self._check_descriptor(fd, parent.fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            parent.close()
            raise
        self._fd = fd
        self._directory = parent

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
        self._directory.validate()
        self._check_descriptor(self._fd, self._directory.fd)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
            self._directory.close()
            self._directory = None

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
        destination = Path(path)
        parent = _open_directory_binding(destination.parent)
        fd = None
        try:
            parent.validate()
            _existing_file(parent, destination.name)
            fd = os.open(destination.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         dir_fd=parent.fd)
            opened = os.fstat(fd)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid()
                    or opened.st_nlink != 1 or opened.st_mode & 0o022 or opened.st_size > 1024 * 1024):
                raise FactsRefused(OWNER_UNKNOWN, "owner map is not an owned bounded regular file")
            data = bytearray()
            while len(data) <= 1024 * 1024:
                chunk = os.read(fd, min(65536, 1024 * 1024 + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if len(data) > 1024 * 1024:
                raise FactsRefused(OWNER_UNKNOWN, "owner map exceeds its byte budget")
            parent.validate()
            entry = os.stat(destination.name, dir_fd=parent.fd, follow_symlinks=False)
            after = os.fstat(fd)
            if ((entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino)
                    or (after.st_uid, after.st_gid, after.st_mode, after.st_nlink,
                        after.st_size, after.st_mtime_ns, after.st_ctime_ns) !=
                       (opened.st_uid, opened.st_gid, opened.st_mode, opened.st_nlink,
                        opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)):
                raise FactsRefused(OWNER_UNKNOWN, "owner map changed while reading")
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate owner map field")
                    result[key] = value
                return result
            document = json.loads(data, object_pairs_hook=unique)
        except (OSError, ValueError, UnicodeError) as exc:
            raise FactsRefused(OWNER_UNKNOWN, "owner map could not be read consistently") from exc
        finally:
            if fd is not None:
                os.close(fd)
            parent.close()
        if not isinstance(document, dict) or set(document) != {"schema", "units"}:
            raise FactsRefused(OWNER_UNKNOWN, "owner map fields do not match its schema")
        if document.get("schema") != OWNER_MAP_SCHEMA:
            raise FactsRefused(OWNER_UNKNOWN, "owner map names another schema")
        units = document.get("units")
        if not isinstance(units, dict) or len(units) > MAX_SERVICES:
            raise FactsRefused(OWNER_UNKNOWN, "owner map has no bounded units object")
        for unit, owner in units.items():
            if (not isinstance(unit, str) or not unit or len(unit.encode("utf-8")) > MAX_FACT_TEXT
                    or not isinstance(owner, str) or owner not in OWNERS):
                raise FactsRefused(OWNER_UNKNOWN, "owner map has an invalid unit or owner")
        return cls(units)

    def write(self, path: Path | str) -> None:
        for unit, owner in self._owners.items():
            if owner not in OWNERS:
                raise FactsRefused(OWNER_UNKNOWN, f"unit {unit!r} has unknown owner {owner!r}")
        # `OwnerMap({...})` is a public constructor, so a caller can build a map
        # that never went through `build_owner_map`. Both paths check.
        _check_owner_map_bounds(self._owners)
        document = {"schema": OWNER_MAP_SCHEMA, "units": dict(sorted(self._owners.items()))}
        _publish_document(Path(path), document)

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
