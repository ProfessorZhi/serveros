#!/usr/bin/env python3
"""A file-backed stand-in for the Legacy Fleet gateway, for the Wave 18 drill.

What it is
----------
Legacy's contract with the connector is three verbs — `get_session`, `get_run`,
`send` — plus the identity of the thing answering them: which Fleet instance,
which route, which boot, which port, which backend URL, which OAuth audience,
where its database, state root and lock live, and what clock it reports. The
release claim is that none of those move while a Next package is installed,
upgraded (and fails), and is revoked.

This module is the drill's *witness* to that claim. It holds no socket, binds no
port, and starts no service: the "gateway" is a directory of files read and
appended by ordinary file I/O, so a snapshot can be taken from a different
process than the one that last touched it. Anything that opens a port to prove
"Legacy survived" would be proving it about something this drill started, which
is not the Legacy the operator runs.

`send` is idempotent by key
---------------------------
A drill that repeated a write to check it was stable would itself become the
second delivery it was written to detect. `send` therefore returns the delivery
already recorded for a key, and appends only for a key it has not seen. The
`deliveries` ledger is append-only, and its length is part of every snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

STATE_REL = "state/fleet.json"
DELIVERIES_REL = "deliveries.jsonl"
DB_REL = "state/fleet.sqlite"
STATE_ROOT_REL = "state"
LOCK_REL = "fleet-legacy.lock"

#: The ten identity fields a snapshot must carry. `db`, `state_root` and `lock`
#: are relative so that a snapshot taken in one temp directory compares equal to
#: one taken in another; absolutes would make every run differ for no reason.
IDENTITY_FIELDS = (
    "fleet_instance_id", "route_id", "boot_id", "port", "backend_url",
    "oauth_audience", "db", "state_root", "lock", "clock",
)

CONTRACT_VERBS = ("get_session", "get_run", "send")

SESSION_KEY = "session-1"
RUN_KEY = "run-1"
DELIVERY_KEY = "run-1"


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _digest(value) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return "sha256:" + _sha(canonical)


class LegacyGateway:
    """Reads and appends the fake gateway's files. No network, no threads."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    # -- state ------------------------------------------------------------- #
    def state(self) -> dict:
        return json.loads((self.root / STATE_REL).read_text(encoding="utf-8"))

    def identity(self) -> dict:
        raw = self.state()
        return {key: raw[key] for key in IDENTITY_FIELDS}

    def get_session(self, key: str) -> dict | None:
        found = (self.state().get("sessions") or {}).get(key)
        return None if found is None else dict(found)

    def get_run(self, key: str) -> dict | None:
        found = (self.state().get("runs") or {}).get(key)
        return None if found is None else dict(found)

    # -- deliveries -------------------------------------------------------- #
    def events(self) -> tuple[dict, ...]:
        path = self.root / DELIVERIES_REL
        if not path.is_file():
            return ()
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return tuple(out)

    def deliveries(self) -> tuple[str, ...]:
        return tuple(event["id"] for event in self.events())

    def send(self, key: str, body: dict | None = None) -> dict:
        """Return the delivery for `key`, appending only a key never seen."""
        for event in self.events():
            if event.get("key") == key:
                return {"id": event["id"], "key": key,
                        "body": event.get("body"), "idempotent": True}
        next_id = f"delivery-{len(self.events()) + 1}"
        event = {"id": next_id, "key": key, "body": body or {}}
        path = self.root / DELIVERIES_REL
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return {"id": next_id, "key": key, "body": event["body"],
                "idempotent": False}

    # -- snapshot ---------------------------------------------------------- #
    def tree(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                rel = path.relative_to(self.root).as_posix()
                files[rel] = _sha(path.read_bytes())
        return files

    def tree_digest(self) -> str:
        recipe = "".join(f"{digest}  {rel}\n"
                         for rel, digest in sorted(self.tree().items()))
        return "sha256:" + _sha(recipe.encode("utf-8"))

    def snapshot(self) -> dict:
        """Everything the release promised would not move, in one object.

        The three contract verbs are exercised and digested rather than assumed:
        a snapshot that only listed files would not notice a `get_run` that
        started answering `None`.
        """
        snap = dict(self.identity())
        snap["contract"] = {
            "get_session": _digest(self.get_session(SESSION_KEY)),
            "get_run": _digest(self.get_run(RUN_KEY)),
            "send": _digest(self.send(DELIVERY_KEY)),
        }
        snap["addressed"] = {
            "session": SESSION_KEY, "run": RUN_KEY, "delivery": DELIVERY_KEY,
        }
        snap["deliveries"] = list(self.deliveries())
        snap["tree_digest"] = self.tree_digest()
        return snap


def seed(root: Path | str, legacy_pin: dict) -> dict:
    """Create the Legacy tree once, with one session, one run, one delivery."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / STATE_ROOT_REL).mkdir()
    (root / DB_REL).write_bytes(b"SQLite format 3\x00wave18-drill-fixture\n")

    identity = {
        "fleet_instance_id": str(legacy_pin["fleet_instance_id"]),
        "route_id": str(legacy_pin["route_id"]),
        "boot_id": str(legacy_pin["boot_id"]),
        "port": int(legacy_pin["port"]),
        "backend_url": str(legacy_pin["backend_url"]),
        "oauth_audience": str(legacy_pin["oauth_audience"]),
        "db": DB_REL,
        "state_root": STATE_ROOT_REL,
        "lock": LOCK_REL,
        "clock": str(legacy_pin["clock"]),
    }
    state = dict(identity)
    state["sessions"] = {
        SESSION_KEY: {"id": SESSION_KEY, "status": "RUNNING",
                      "route": identity["route_id"],
                      "device": str(legacy_pin["device_ref"]),
                      "boot": identity["boot_id"]},
    }
    state["runs"] = {
        RUN_KEY: {"id": RUN_KEY, "session": SESSION_KEY, "status": "RUNNING",
                  "delivery": "delivery-1"},
    }
    (root / STATE_REL).write_bytes(
        json.dumps(state, sort_keys=True, indent=2,
                   ensure_ascii=False).encode("utf-8"))
    (root / LOCK_REL).write_bytes(b"fleet-legacy=held\n")
    (root / DELIVERIES_REL).write_bytes(
        (json.dumps({"id": "delivery-1", "key": DELIVERY_KEY,
                     "body": {"native_activity": True, "verified": True}},
                    sort_keys=True) + "\n").encode("utf-8"))
    return LegacyGateway(root).snapshot()


def snapshot(root: Path | str) -> dict:
    return LegacyGateway(root).snapshot()
