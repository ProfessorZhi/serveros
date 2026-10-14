"""One reading of the machine, grouped by the account that holds the memory.

WHY THIS EXISTS, IN ONE SENTENCE AND WITH ITS EVIDENCE
------------------------------------------------------
On 2026-10-14 a single process on this shared host held 145 GiB of a 247 GiB
machine (58.7%) for about nineteen hours under one account, and nothing in the
product said so: the per-account reading that exists (`inventory.user_summary`)
measures only the uid of the process that asks, and the per-process reading is
`ps`-sorted by memory for a card, not a threshold that can be crossed. A person
found it with `top`. This module is the missing measurement and nothing else —
it is a producer, it returns a document, and no branch below acts on it.

WHAT IT MEASURES, AND WHAT IT DOES NOT
--------------------------------------
ONE bounded `ps` scan, four columns, grouped by numeric uid:

    ps -eo uid=,pid=,rss=,comm=

* `uid` is the kernel's numeric uid. The user NAME is never used as identity,
  and a name is not even collected: §7 of the issue asks for a stable account
  identity and a name is a string an administrator can change. The name can be
  joined from `id -un` by a caller that has a person to render for.
* `pid` and `comm` name ONE process — the largest for that uid — because the
  first screen owes a reader "which process" without printing a process table.
  They are not the subject of anything: the subject of an alert about this is
  `uid:<number>`, so a process that restarts under a new pid is the same
  finding, which is the whole reason the pid is not part of the identity.
* `rss` is `ps`'s resident set size, in 1024-byte units on Linux.
* `comm` is the kernel's command name, last in the line ON PURPOSE. It is not
  escaped by the kernel and may contain spaces, so every parse below splits
  with a maximum field count rather than splitting the whole line — the same
  hazard `tests/test_user_observation.py` documents for `/proc/<pid>/stat`,
  and here it is worse because a naive split of a name with a space silently
  drops the process from `users` while the total still looks plausible.

NOT read, and this is structural rather than careful: `argv`, `environ`, the
cwd, open files, and anything under another account's `/proc` entry. The four
columns above are the entire input. What a person typed into a command line is
not evidence about memory and is exactly the material §4 of the issue forbids
a shared-host card from showing.

THE ESTIMATE, NAMED WHERE IT IS PRODUCED
----------------------------------------
Summed RSS **double-counts every shared page**: two processes mapping one
1 GiB library each report 1 GiB, and the machine has one. The number below is
therefore an ESTIMATE and every row says so in its own `provenance`. It is not
PSS and it is not the kernel's per-uid accounting, and a caller that needs
either must go and read it rather than relabel this one — `inventory.py` already
takes that position for its own RSS sum, and two modules disagreeing about what
one unit means is how a threshold acquires two meanings.

The estimate errs HIGH on a host full of forking workers, which for a pressure
threshold is the direction that reports rather than the direction that hides.
That is a defensible bias and it is stated rather than left to be discovered.

UNKNOWN IS AN ANSWER AND ZERO IS NOT
------------------------------------
§27's rule, which this tree enforces by test elsewhere: a measurement that did
not happen must never be spelled the same way as a measurement of nothing.
So `ps` failing, timing out, or listing nothing at all are all `UNKNOWN` with a
reason — a snapshot with an empty `users` list is never returned, because a
caller reading one would conclude the machine is idle. Likewise a row that
cannot be parsed is counted in `coverage.unparsed` and contributes no bytes; it
is never read as a process using zero memory.

The same rule covers a missing `MemTotal`: without a host total there is no
percentage, and a percentage is the only unit a threshold is written in here, so
the snapshot is `UNKNOWN` rather than a byte figure a caller would have to
divide by something it does not have.

WHAT THIS MODULE CANNOT SEE, STATED RATHER THAN DISCOVERED LATER
----------------------------------------------------------------
On a host mounted with `hidepid=2`, `ps -eo` returns only the caller's OWN
processes and reports them as if they were the whole table. Nothing visible from
inside the process distinguishes that from a machine where one account is the
only one running anything, so this module does not claim to detect it and does
not guess. It reports `coverage` — how many rows arrived, how many parsed, how
many distinct uids — so that a caller who knows the host's mount options, or who
compares two devices, has the numbers to tell. What it will NOT do is call a
filtered table a complete one or, worse, read it as a machine that got quieter.

Read-only by construction: there is no function below with a verb in it, no
`kill`, no signal, no `sudo`, and no path from any value here to a process. A
threshold crossing is a sentence for a person, which is `resource_anomaly`'s
business and not this module's.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

# One budget for one scan. TRANSCRIBED from `inventory.BUDGET["fast"]` rather
# than imported, and the transcription is checked by a test that reads both —
# the same arrangement `anomaly.DEFAULT_THRESHOLDS` has with the contract, and
# for the same reason: these modules ship as artifacts rather than as an
# installed package, so an import here would be a module-level dependency on
# another module's file location, and a test that compares the two fails loudly
# instead of the pair drifting apart in silence.
#
# `fast` is 8.0s: `ps -eo` over a few thousand processes measured well inside a
# second on this host, so the budget is the ceiling for a pathological machine
# and not the expectation.
SAMPLE_TIMEOUT = 8.0

# Suppressed headers, and the four columns and nothing else. `comm` is last so
# that the one field which can contain a space is the one field with nothing to
# its right.
PS_ARGV = ("ps", "-eo", "uid=,pid=,rss=,comm=")

MEMINFO_PATH = "/proc/meminfo"

PRESENT = "PRESENT"
UNKNOWN = "UNKNOWN"

#: Says what the summed number is, and in the same sentence what it is not.
#: Carried on every row because the row is what a caller renders; a provenance
#: only on the envelope is one a consumer can lose on the way to a card.
PROVENANCE = (
    "sum of RSS over this uid's processes from one `ps -eo uid,pid,rss,comm` "
    "scan; an ESTIMATE that double-counts every page shared between processes "
    "and is not kernel per-uid accounting and not PSS"
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _unavailable(why: str, **extra) -> dict:
    """The UNKNOWN document. Never carries `users`, on purpose.

    A caller that reached for `users` here and found `[]` would have been handed
    "the machine is idle" spelled as a failure. There is no key to reach for.
    """
    return {"state": UNKNOWN, "reason": why, "source": " ".join(PS_ARGV),
            "provenance": PROVENANCE, **extra}


class LiveHost:
    """Shell and file access with a budget. No method here can write.

    The same two methods `inventory.LiveHost` exposes, and deliberately not the
    same object: this module must be loadable on its own (tests do exactly
    that) and importing `inventory` for a shell wrapper would drag a Docker
    client into a test that reads three integers.
    """

    name = "live-host"

    def read(self, args, timeout) -> tuple[int, str, str]:
        # A list, never a string, and no `shell=True` anywhere in this file: the
        # kernel gets an argv and there is no shell to interpret a byte of what
        # came back from the previous read.
        try:
            out = subprocess.run(list(args), capture_output=True, text=True,
                                 timeout=timeout)
        except subprocess.TimeoutExpired:
            return -1, "", f"timed out after {timeout}s"
        except (OSError, subprocess.SubprocessError) as exc:
            return -2, "", f"{type(exc).__name__}: {exc}"
        return out.returncode, out.stdout, out.stderr

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text()
        except OSError:
            return None


def parse_meminfo(text: str | None) -> int | None:
    """`MemTotal` in bytes, or None when the kernel did not say.

    `MemTotal` and not `MemAvailable`: the threshold this feeds is "this
    account holds more than a share of the machine", and the machine's size is
    not a moving quantity. Available memory moves every second and a percentage
    of a moving denominator is a number nobody can compare across two cards.
    """
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("MemTotal:"):
            continue
        fields = line.split()
        if len(fields) < 2:
            return None
        try:
            kb = int(fields[1])
        except ValueError:
            return None
        return kb * 1024 if kb > 0 else None
    return None


def parse_rows(text: str) -> tuple[list[dict], int]:
    """`(rows, unparsed)`. A row that cannot be read is counted, not zeroed.

    `split(None, 3)` and not `split()`: `comm` is the last column and the
    kernel does not escape it, so a command name containing a space would
    otherwise produce five fields and either raise or shift the numbers. With a
    maximum field count the space stays inside the name where it belongs.

    A row with fewer than four fields is `unparsed` rather than a process with a
    missing name: an incomplete line is an incomplete table, and the caller can
    see the count in `coverage` instead of receiving a number that was quietly
    built from the rows that happened to be whole.
    """
    rows: list[dict] = []
    unparsed = 0
    for line in (text or "").splitlines():
        fields = line.split(None, 3)
        if len(fields) < 4:
            if line.strip():
                unparsed += 1
            continue
        try:
            uid = int(fields[0])
            pid = int(fields[1])
            rss_kb = int(fields[2])
        except ValueError:
            unparsed += 1
            continue
        if uid < 0 or pid <= 0 or rss_kb < 0:
            unparsed += 1
            continue
        # `fields[3]` is a name the kernel chose and is used ONLY as a label on
        # a row. It is never an argument to anything, never a path, and never
        # a key — see the module docstring on what is not read.
        rows.append({"uid": uid, "pid": pid, "rss_kb": rss_kb,
                     "comm": fields[3].strip()})
    return rows, unparsed


def group_by_uid(rows: list[dict], total_bytes: int) -> list[dict]:
    """One entry per numeric uid, most memory first.

    The grouping is by the integer and nothing else, so two accounts can never
    merge because their names collided and one account can never split because
    a name was printed two ways.

    The largest process is chosen by rss and broken by pid, and the tie-break is
    not decoration: without it the "top process" of two equal-sized processes
    is whichever `ps` happened to print first, so the same machine sampled twice
    names two different processes and a card appears to have changed subject
    when nothing changed at all.
    """
    buckets: dict[int, dict] = {}
    for row in rows:
        bucket = buckets.get(row["uid"])
        if bucket is None:
            bucket = buckets[row["uid"]] = {
                "uid": row["uid"], "process_count": 0, "rss_kb": 0,
                "top": None,
            }
        bucket["process_count"] += 1
        bucket["rss_kb"] += row["rss_kb"]
        top = bucket["top"]
        if top is None or (row["rss_kb"], -row["pid"]) > (top["rss_kb"], -top["pid"]):
            bucket["top"] = row

    users: list[dict] = []
    for bucket in buckets.values():
        rss_bytes = bucket["rss_kb"] * 1024
        top = bucket["top"]
        users.append({
            "uid": bucket["uid"],
            "process_count": bucket["process_count"],
            "rss_estimate_bytes": rss_bytes,
            "percent_of_host": (
                round(100.0 * rss_bytes / total_bytes, 1) if total_bytes else None),
            "top_pid": top["pid"],
            "top_comm": top["comm"],
            "top_rss_bytes": top["rss_kb"] * 1024,
            "provenance": PROVENANCE,
        })
    # Sorted so that two reads of an unchanged machine produce byte-identical
    # documents: a caller diffing two snapshots should see a change only when
    # something changed.
    users.sort(key=lambda row: (-row["rss_estimate_bytes"], row["uid"]))
    return users


def snapshot(host=None, *, total_bytes: int | None = None,
             sampled_at: str | None = None, self_pid: int | None = None) -> dict:
    """One bounded reading of every visible account's resident memory.

    `host` is anything with `read(argv, timeout) -> (code, out, err)` and
    `read_text(path) -> str | None`; omitting it uses the live machine. A test
    passes a scripted one, which is the only way this is checkable at all: the
    alternative is a test that asserts whatever the host running the suite
    happens to be doing.

    `total_bytes` is the host's memory size when the caller already knows it,
    and reading `/proc/meminfo` again would be a second answer to a question the
    caller has just answered. `sampled_at` is passed by a caller that is
    recording a reading rather than taking one.

    Returns `PRESENT` with `users`, or `UNKNOWN` with a `reason`. There is no
    third shape and no partial document: a caller that has to ask which fields a
    given failure left standing is a caller that will eventually treat a missing
    one as zero.
    """
    at = sampled_at or _now_iso()
    if host is None:
        host = LiveHost()

    code, out, err = host.read(list(PS_ARGV), SAMPLE_TIMEOUT)
    if code != 0:
        # Non-zero from `ps` is the whole of a permission refusal, a missing
        # binary and a kernel that would not answer, and they are the same
        # sentence to a reader: this table was not read.
        return _unavailable(
            f"`ps` did not answer, so no account's memory was measured"
            + (f": {err.strip()}" if (err or "").strip() else f" (exit {code})"),
            sampled_at=at)

    rows, unparsed = parse_rows(out)
    if not rows:
        # An empty table is not an idle machine. `ps -eo` always lists at least
        # this process and pid 1, so zero rows means the read produced nothing —
        # and returning `users: []` here is the exact failure the module
        # docstring opens with.
        return _unavailable(
            "`ps` listed no processes at all, which is a read that produced "
            "nothing rather than a machine with nothing running",
            sampled_at=at,
            coverage={"rows": 0, "unparsed": unparsed, "uids": 0})

    if total_bytes is None:
        total_bytes = parse_meminfo(host.read_text(MEMINFO_PATH))
    if not total_bytes:
        return _unavailable(
            "the host's total memory could not be read, so no account's share "
            "of it can be stated; a byte total without its denominator is a "
            "number a caller would divide by something it does not have",
            sampled_at=at)

    users = group_by_uid(rows, total_bytes)
    pids = {row["pid"] for row in rows} if self_pid is not None else None
    coverage = {
        "rows": len(rows),
        "unparsed": unparsed,
        "uids": len(users),
        # PARTIAL is a statement about THIS table: rows arrived that could not
        # be read, so the totals above are built from the rows that were whole.
        # It is not a claim about hidepid — see the module docstring, which says
        # why a filtered table cannot be told from a small one from in here and
        # what a caller does about it instead.
        "state": "PARTIAL" if unparsed else "FULL",
        # Whether the process that took this reading appears in it. `None` when
        # the caller supplied no pid to look for, which is a third answer and
        # not a false one: "not checked" and "checked and absent" are different
        # facts and this host has been bitten by exactly that conflation.
        "self_visible": (self_pid in pids) if self_pid is not None else None,
    }
    return {
        "state": PRESENT,
        "sampled_at": at,
        "source": " ".join(PS_ARGV),
        "total_bytes": total_bytes,
        "users": users,
        "process_count": len(rows),
        "coverage": coverage,
        "provenance": PROVENANCE,
    }
