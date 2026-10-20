"""Read-only adapter: the compiled native collector, bounded and translated.

This module does not measure anything itself. It runs
`native/node-core`'s already-compiled `serveros-node-resource-snapshot`
binary under a bound, checks what came back, and translates the result into a
measurement envelope the existing Python core can consume.

Three rules shape everything below.

**It is invoked, never wired in.** Nothing in `runtime/` imports this module, it
is not in `serverosd`'s loader closure, and it schedules nothing. A caller must
pass ``enabled=True`` explicitly, which exists so that a caller which forgot to
decide cannot accidentally measure the host. `runtime/card_source.py` assembles
and does not re-measure the whole machine (docs/NODE-OBSERVABILITY.md §10), so it
must not call this either; when a real consumer exists it belongs to a service
adapter that says so in review.

**The binding is somebody else's.** `runtime/bounded_invocation.invoke_once` is
the tree's bounded-run seam: it takes an argv LIST (so there is no shell and no
`shell=True` to disable), bounds wall time and each output stream, kills the
process group on timeout, and returns one receipt shape for every outcome. This
module supplies the argv and reads the receipt; it does not grow a second
subprocess runner beside it.

**A reading that could not be taken is not a reading of zero.** Every failure
below produces an envelope with ``readings_available: False`` and a reason, in
the same shape as a success, so a caller reads one shape.

What this module deliberately refuses to do, and says so in ``refusals``:

* divide a host-wide figure into one account's share. The snapshot describes the
  machine. Nothing readable on this host supports that division, and
  docs/NODE-OBSERVABILITY.md §3 states the rule ("无配额时明确占整机，不把320%当整机320%").
* sum interface counters across interfaces. Bridge, veth and physical interfaces
  carry the same frames; the same document says so ("不把tailscale/bridge/physical
  相同流量重复汇总").
* treat a link speed as public bandwidth. A local link rate is not the internet.
  `eth0` on this host is virtio_net and reports no speed at all, so its link
  utilisation is UNAVAILABLE rather than 0%.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_SCHEMA = "serveros/node-resource-snapshot.v1"
ENVELOPE_SCHEMA = "serveros/native-node-measurement.v1"
COLLECTOR_NAME = "serveros-node-core-native"
SOURCE = "native-node-core"

# The binary's own bound is far below this; the cap exists so a collector that
# behaved unexpectedly cannot make this module read without limit.
MAX_SNAPSHOT_BYTES = 4_000_000

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]

_ROLE = re.compile(r"[A-Z][A-Z0-9_]{0,31}")
_KEY = re.compile(r"[a-zA-Z0-9_-]{1,100}")


def _load(name: str, path: Path):
    """One module object tree-wide, loaded by path.

    The names are the ones the rest of the tree already uses for these two files
    (`runtime/native_reader.py` loads the first under this name, `runtime/view.py`
    and five others load the second), so loading them here does not give the
    fabric a second copy of either.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _invocation():
    return _load("serveros_runtime_bounded_invocation",
                 _REPO / "runtime" / "bounded_invocation.py")


def _graph():
    return _load("serveros_runtime_graph", _REPO / "runtime" / "graph.py")


def validated_executable(candidate) -> Path:
    """The collector binary, or a refusal. Never a path that resolves elsewhere.

    A path whose realpath differs from itself reaches a file other than the one
    written down, and "run this instead of what you named" is not a property a
    bound is supposed to have.
    """
    path = Path(candidate)
    if not path.is_absolute():
        raise ValueError("collector executable must be an absolute path")
    if os.path.islink(path):
        raise ValueError("collector executable must not be a symlink")
    resolved = os.path.realpath(path)
    if resolved != str(path):
        raise ValueError(
            f"collector executable path resolves elsewhere ({resolved}); a path "
            f"that is not its own realpath is refused rather than followed")
    if not path.is_file():
        raise ValueError("collector executable must be an existing regular file")
    if not os.access(path, os.X_OK):
        raise ValueError("collector executable is not executable")
    return path


def collector_argv(executable: Path, *, interval_ms: int = 500, filesystems=None,
                   interfaces=None, compact: bool = True) -> list[str]:
    """The argv this adapter will run. A list, so there is no shell to escape."""
    if type(interval_ms) is not int or interval_ms < 0:
        raise ValueError("interval_ms must be a non-negative integer")
    argv = [str(executable), "--interval-ms", str(interval_ms)]
    for entry in filesystems or ():
        role, _, path = str(entry).partition("=")
        if not _ROLE.fullmatch(role) or not path.startswith("/"):
            raise ValueError(
                f"filesystem entries are ROLE=/absolute/path with an upper-case "
                f"role, got {entry!r}")
        argv += ["--filesystem", f"{role}={path}"]
    for name in interfaces or ():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", name):
            raise ValueError(f"interface name is not an interface name: {name!r}")
        argv += ["--interface", name]
    if compact:
        argv += ["--compact"]
    return argv


def _recorded_window(source: str):
    """The window the tree already declares for this kind of evidence, if any.

    Read rather than restated: `runtime/graph.py`'s `FRESH_WITHIN_SECONDS` holds
    §18's resource-observation windows (`host-filesystem`, `host-memory`: 300s)
    and this adapter must not declare a second opinion about them. A source with
    no entry returns None, which makes the freshness UNKNOWN rather than fresh —
    a window nobody declared is not a window.
    """
    return _graph().FRESH_WITHIN_SECONDS.get(source)


def _resolve_now(now):
    """The clock to age a reading with. ``None`` means this process's, not none.

    `runtime/graph.py` reads a missing clock as "this reader has no clock" and
    answers UNKNOWN. That is the right answer for a reader that has none, and the
    wrong one here: this adapter is running, so it has one. Passing None through
    would make every envelope report an unmeasurable age for a reason that is
    simply untrue, and an untrue reason is worse than a missing one.
    """
    return datetime.now(timezone.utc) if now is None else now


def _freshness(observed_at, *, now, within_seconds):
    """`runtime/graph.py`'s own `freshness_of`, with this adapter's source name.

    Reused rather than reimplemented because its rule is the one this envelope
    needs: FRESH or STALE only when there is a window, a time and a clock, and
    UNKNOWN with the reason otherwise — never FRESH. The clock wins over any
    claim in the payload (`runtime/daemon.py:1704` makes the same call).
    """
    if within_seconds is None:
        within_seconds = _recorded_window(SOURCE)
    return _graph().freshness_of(observed_at, now=now, source=SOURCE,
                                 within_seconds=within_seconds)


# Two producers of "how much room is left on this filesystem" that spell the
# answer differently. This was not reasoned about in advance; it was found by
# running the shadow comparison in Phase 2 against `runtime/inventory.py` and
# watching two rows come back 22 GiB apart on `/`. Neither name is wrong — the
# collector uses the POSIX names, and the inventory's `free_bytes` is df's Avail
# column, which is `f_bavail` — but a consumer that joins them by name gets a
# number that is wrong by the root-reserved blocks, silently, and only on
# filesystems that reserve some.
_FIELD_SEMANTICS = [
    {"field": "measurements.filesystems.roles[].free_bytes",
     "means": "statvfs f_bfree: every free block, including those reserved for root",
     "conflicts_with": "runtime/inventory.py's role row `free_bytes`, which is df's Avail "
                       "column (statvfs f_bavail)",
     "join_on": "measurements.filesystems.roles[].available_bytes, which is f_bavail and "
                "lines up with it"},
    {"field": "measurements.filesystems.roles[].available_bytes",
     "means": "statvfs f_bavail: the free blocks a non-root writer can actually use",
     "joins_with": "runtime/inventory.py's role row `free_bytes`"},
    {"field": "measurements.interfaces[].speed_mbps",
     "means": "the local link rate in Mb/s, or UNAVAILABLE",
     "not": "public or internet bandwidth; see the refusal of the same name"},
]


def _refusals(snapshot: dict) -> list[dict]:
    """What this envelope will not be used to say, stated where a reader sees it."""
    out = [
        {"subject": "host_wide_share",
         "reason": "this reading describes the machine, not one account's share of it; "
                   "no fact in it would support dividing a host-wide figure by account "
                   "(docs/NODE-OBSERVABILITY.md §3)"},
        {"subject": "interface_counter_aggregation",
         "reason": "interface counters are per-interface; bridge, veth and physical "
                   "interfaces carry the same frames, so summing them double-counts "
                   "(docs/NODE-OBSERVABILITY.md §3)"},
        {"subject": "interface_link_speed_as_wan_bandwidth",
         "reason": "an interface's link speed is the local link rate, not public or "
                   "internet bandwidth; no field here is a bandwidth measurement"},
        {"subject": "filesystem_role_dedup",
         "reason": "roles may share one filesystem; two roles on one device are one "
                   "filesystem reported twice, not twice the space "
                   "(docs/NODE-OBSERVABILITY.md §3)"},
    ]
    for row in snapshot.get("interfaces") or ():
        speed = row.get("speed_mbps")
        if isinstance(speed, dict) and speed.get("state") == "UNAVAILABLE":
            out.append({
                "subject": f"interfaces.{row.get('name')}.link_utilisation",
                "reason": "no denominator, so no link utilisation is reported: "
                          + str(speed.get("reason")),
            })
    return out


def translate(snapshot: dict, *, provenance: dict, now=None, within_seconds=None) -> dict:
    """One parsed snapshot document into one measurement envelope. Pure.

    Fields inside `cpu`, `memory`, `filesystems`, `disk_io` and `interfaces` are
    carried through unchanged, including every {"state": "UNAVAILABLE", ...}
    object. They are not renamed, because a rename is an interpretation and the
    collector's names were already chosen to match this core's
    (`*_bytes`, `*_per_second`, `sample_seconds`, role names, `observed_at`).
    What this function adds is the part the collector cannot state: whose
    authority this is, whether it is fresh, and what it must not be read as.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a parsed JSON object")
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(
            f"snapshot schema is {snapshot.get('schema')!r}, this adapter reads "
            f"{SNAPSHOT_SCHEMA!r}")
    collector = snapshot.get("collector") or {}
    if collector.get("name") != COLLECTOR_NAME:
        raise ValueError(
            f"snapshot was written by {collector.get('name')!r}, not {COLLECTOR_NAME!r}")

    now = _resolve_now(now)
    envelope = {
        "schema": ENVELOPE_SCHEMA,
        "readings_available": True,
        "source": SOURCE,
        "provenance": {
            **provenance,
            "collector": COLLECTOR_NAME,
            "collector_version": collector.get("version"),
            "source_schema": SNAPSHOT_SCHEMA,
            "language": collector.get("language"),
            # False means the facts came from a fixture and this is not a reading
            # of any host. Carried through rather than dropped.
            "live": collector.get("live"),
            "read_only_note": (
                "the collector is read-only by construction — it opens nothing for "
                "writing; the invocation's readonly flag is a recorded request, not "
                "an enforcement, and bounded_invocation sandboxes nothing"),
            "operator_disclosure": (
                "the command line is machine-facing; a human surface must not render "
                "provider command lines (docs/NODE-OBSERVABILITY.md §10)"),
        },
        "scope": "host-wide",
        "sample": {
            "observed_at": snapshot.get("observed_at"),
            "sample_started_at": snapshot.get("sample_started_at"),
            "sample_seconds": snapshot.get("sample_seconds"),
            "monotonic_seconds": snapshot.get("monotonic_seconds"),
            "boot_id": snapshot.get("boot_id"),
            # Collection time is not render time is not the time a person read it
            # (docs/NODE-OBSERVABILITY.md §10). This is when the reader looked.
            "enveloped_at": (now if isinstance(now, str) else now.isoformat()),
        },
        "freshness": _freshness(snapshot.get("observed_at"), now=now,
                                within_seconds=within_seconds),
        "measurements": {
            "cpu": snapshot.get("cpu"),
            "memory": snapshot.get("memory"),
            "filesystems": snapshot.get("filesystems"),
            "disk_io": snapshot.get("disk_io"),
            "interfaces": snapshot.get("interfaces"),
        },
        # Not host-wide, and kept out of `measurements` for that reason: this is
        # the collector's own cgroup, so it is the one part of the document that
        # is about one account.
        "attribution": {**snapshot.get("attribution", {}), "scope": "self"},
        "unknowns": list(snapshot.get("unknowns") or ()),
        "refusals": _refusals(snapshot),
        # The one place a consumer joining these measurements to the Python
        # inventory by field name can go wrong, named rather than left to be
        # discovered as a number that is quietly off by the reserved blocks.
        "field_semantics": [dict(row) for row in _FIELD_SEMANTICS],
    }
    # The core's own rule (docs/MONITORING-PRODUCT-ARCHITECTURE.md:79): a reading
    # about which something essential is not known is not a good reading. An
    # unmeasurable age is exactly that, so it joins `unknowns` — and only when it
    # genuinely is unknown, because an entry for a freshness that was measured is
    # a false alarm that teaches a reader to skim the list.
    if envelope["freshness"].get("state") == "UNKNOWN":
        envelope["unknowns"].append({
            "field": "freshness",
            "reason": envelope["freshness"].get("why")
                      or "the age of this reading could not be established",
        })
    return envelope


def _failure_envelope(*, reason: str, provenance: dict, now=None,
                      within_seconds=None) -> dict:
    """A reading that was not taken, in the shape of one that was."""
    now = _resolve_now(now)
    return {
        "schema": ENVELOPE_SCHEMA,
        "readings_available": False,
        "source": SOURCE,
        "provenance": {**provenance, "collector": COLLECTOR_NAME},
        "scope": "host-wide",
        "sample": {"observed_at": None, "sample_started_at": None, "sample_seconds": None,
                   "monotonic_seconds": None, "boot_id": None,
                   "enveloped_at": (now if isinstance(now, str) else now.isoformat())},
        "freshness": _freshness(None, now=now, within_seconds=within_seconds),
        "measurements": {"state": "UNAVAILABLE", "reason": reason},
        "attribution": {"state": "UNAVAILABLE", "reason": reason, "scope": "self"},
        "unknowns": [{"field": "measurements", "reason": reason}],
        # One entry, not the success path's four: with no reading there is nothing
        # for the other three to be about, and a refusal about a measurement that
        # does not exist reads as though one had been taken.
        "refusals": [
            {"subject": "no_reading",
             "reason": "no measurement was taken, so nothing in this envelope is a "
                       "fact about the host and nothing may be read as one"},
        ],
        # Present and empty rather than absent, so that a caller reads one key
        # set whether or not a reading came back. There are no measurement fields
        # here, so there is nothing to say about how to join them.
        "field_semantics": [],
    }


def collect(*, executable, enabled, key: str, receipts, interval_ms: int = 500,
            filesystems=None, interfaces=None, timeout: int = 15,
            max_output_bytes: int = 1_000_000, within_seconds=None, now=None,
            compact: bool = True) -> dict:
    """Run the collector once, bounded, and return one envelope.

    ``enabled`` has no default. It exists so that a caller which has not decided
    to measure the host cannot measure it by omission, and so that this module
    can never be reached by an import-time side effect.

    ``key`` is the caller's, because one reading versus a replay of a previous
    one is a caching decision, and this adapter does not make caching decisions.
    A repeat under the same key replays the recorded run instead of executing
    again, and the envelope always reports which of the two happened
    (``executed_now`` / ``replayed``), so a replayed reading is never passed off
    as a new one.
    """
    if enabled is not True:
        raise ValueError(
            "collect() requires enabled=True: this adapter is never wired into "
            "serverosd's run loop, so a caller has to say it means to measure the "
            "host. Nothing was run.")
    if not _KEY.fullmatch(str(key)):
        raise ValueError("action key must match [a-zA-Z0-9_-]{1,100}")
    if type(timeout) is not int or timeout <= 0:
        raise ValueError("timeout must be a positive integer")
    if type(max_output_bytes) is not int or max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be a positive integer")

    executable = validated_executable(executable)
    receipts = Path(receipts)
    if not receipts.is_absolute():
        raise ValueError("receipts directory must be an absolute path")

    argv = collector_argv(executable, interval_ms=interval_ms, filesystems=filesystems,
                          interfaces=interfaces, compact=compact)
    base = {
        "executable": str(executable),
        "argv": argv,
        "action_key": key,
        "timeout_seconds": timeout,
        "max_output_bytes_per_stream": max_output_bytes,
    }

    receipt = _invocation().invoke_once(
        key=key, argv=argv, cwd=executable.parent, receipts=receipts,
        input_text="", timeout=timeout, readonly=True,
        max_output_bytes=max_output_bytes)
    provenance = {
        **base,
        "status": receipt.get("status"),
        "process_exit_code": receipt.get("process_exit_code"),
        "direct_process_exited": receipt.get("direct_process_exited"),
        "effect": receipt.get("effect"),
        "executed_now": receipt.get("executed_now"),
        "replayed": receipt.get("replayed"),
        "read_only_requested": receipt.get("read_only_requested"),
        "output_sha256": receipt.get("output_sha256"),
        "receipt": str(receipts / (key + ".json")),
    }

    status = receipt.get("status")
    if status != "EXITED":
        # TIMED_OUT, OUTPUT_LIMIT, START_FAILED, IO_FAILED and REFUSED all land
        # here. The receipt's own sentence is carried through rather than
        # replaced, because it says which of them it was.
        return _failure_envelope(
            reason=f"the collector did not complete: {status} "
                   f"({receipt.get('reason') or receipt.get('error_type') or 'no further detail'})",
            provenance=provenance, now=now, within_seconds=within_seconds)
    if receipt.get("direct_process_exited") is not True:
        return _failure_envelope(
            reason="the collector's direct process was not observed to exit, so its "
                   "output is not a completed reading",
            provenance=provenance, now=now, within_seconds=within_seconds)
    if receipt.get("process_exit_code") != 0:
        return _failure_envelope(
            reason=f"the collector exited {receipt.get('process_exit_code')}",
            provenance=provenance, now=now, within_seconds=within_seconds)

    stdout_path = receipts / (key + ".stdout")
    if stdout_path.is_symlink():
        return _failure_envelope(reason="the recorded stdout is a symlink; refused",
                                 provenance=provenance, now=now,
                                 within_seconds=within_seconds)
    try:
        raw = stdout_path.read_bytes()
    except OSError as exc:
        return _failure_envelope(
            reason=f"the recorded stdout could not be read ({type(exc).__name__})",
            provenance=provenance, now=now, within_seconds=within_seconds)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        return _failure_envelope(
            reason=f"the recorded stdout is {len(raw)} bytes, above this adapter's "
                   f"{MAX_SNAPSHOT_BYTES}-byte cap",
            provenance=provenance, now=now, within_seconds=within_seconds)
    provenance["stdout_bytes"] = len(raw)

    try:
        snapshot = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _failure_envelope(
            reason=f"the collector's output is not valid UTF-8 JSON "
                   f"({type(exc).__name__}: {exc})",
            provenance=provenance, now=now, within_seconds=within_seconds)

    try:
        return translate(snapshot, provenance=provenance, now=now,
                         within_seconds=within_seconds)
    except ValueError as exc:
        return _failure_envelope(reason=str(exc), provenance=provenance, now=now,
                                 within_seconds=within_seconds)


def main(argv=None) -> int:
    """Explicit opt-in command line. Nothing calls this on its own."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="native_node_core",
        description="Run the native node-core collector once and print a "
                    "measurement envelope. Read-only; nothing is scheduled.")
    parser.add_argument("--enabled", action="store_true",
                        help="required: states that this run intends to measure the host")
    parser.add_argument("--executable", required=True,
                        help="absolute path to serveros-node-resource-snapshot")
    parser.add_argument("--receipts", required=True,
                        help="absolute, private (0700) directory for invocation receipts")
    parser.add_argument("--key", default=None,
                        help="action key; a repeat under the same key replays the "
                             "recorded run. Default is unique per run, which always "
                             "executes a fresh reading.")
    parser.add_argument("--interval-ms", type=int, default=500)
    parser.add_argument("--filesystem", action="append", default=[],
                        metavar="ROLE=/path")
    parser.add_argument("--interface", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--max-output-bytes", type=int, default=1_000_000)
    parser.add_argument("--within-seconds", type=int, default=None,
                        help="the declared freshness window; without one the "
                             "freshness is UNKNOWN rather than fresh")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    if not args.enabled:
        parser.error("--enabled is required: this adapter is never run implicitly")

    key = args.key or ("native-node-core-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    envelope = collect(
        executable=args.executable, enabled=True, key=key, receipts=args.receipts,
        interval_ms=args.interval_ms,
        filesystems=[f.split("=", 1)[0] + "=" + f.split("=", 1)[1]
                     for f in args.filesystem if "=" in f] or None,
        interfaces=args.interface or None, timeout=args.timeout,
        max_output_bytes=args.max_output_bytes, within_seconds=args.within_seconds)
    print(json.dumps(envelope, indent=args.indent, sort_keys=True, default=str))
    return 0 if envelope["readings_available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
