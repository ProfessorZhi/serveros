"""One account's share of a shared machine, measured without asking it.

The reading under test is the one that would have caught the 2026-10-14
process: one account holding 145 GiB of a 247 GiB host for nineteen hours while
the product reported nothing. So the first test below is that machine, stated
as the two numbers an operator read off `top`, and everything after it is a
way the reading can be WRONG rather than missing.

Two of those hazards are worth more than the rest of the file, and both are a
number that comes out plausible:

* `ps` does not escape `comm`, so a command name containing a space shifts
  every field if the line is split whole. The number that survives is a sum
  over fewer processes, which reads as a machine that got quieter.
* `hidepid`, a permission refusal and an empty table all produce a table this
  code could sum to zero. Zero is a measurement of an idle machine, and "we
  could not look" is not it — §27's rule, which the module answers by returning
  no `users` key at all when it has not measured.

The host is scripted rather than faked. An unscripted read raises, because a
double that answers `{}` to everything tests the double — and a read this code
did not expect is exactly the thing worth failing on here.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


user_memory = _load("serveros_runtime_user_memory", "user_memory.py")


class ScriptedHost:
    """A host that answers from a fixture, and records what it was asked.

    `commands` is keyed by argument prefix and `files` by path. Either value may
    be a list, consumed one call at a time. Everything that was asked is kept,
    because the only structural guarantee this module can offer about what it
    does NOT do is the shape of the questions it asked.
    """

    def __init__(self, files=None, commands=None):
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.reads: list[tuple] = []
        self.read_texts: list[str] = []

    def read(self, args, timeout):
        self.reads.append(tuple(args))
        for prefix, value in self.commands.items():
            if tuple(args[:len(prefix)]) != tuple(prefix):
                continue
            if isinstance(value, list):
                return value.pop(0)
            return value
        raise AssertionError(f"unscripted command: {list(args)!r}")

    def read_text(self, path):
        self.read_texts.append(path)
        if path not in self.files:
            raise AssertionError(f"unscripted read_text: {path!r}")
        value = self.files[path]
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value


def memory_of(*rows: str) -> str:
    """A `ps -eo uid=,pid=,rss=,comm=` table from `uid pid rss comm` triples."""
    return "".join(f"{line}\n" for line in rows)


def meminfo(total_kb: int) -> str:
    return f"MemTotal:       {total_kb} kB\nMemAvailable:   {total_kb // 4} kB\n"


# 247 GiB of host, one process of 145 GiB. The accident, as two integers.
HOST_KB = 247 * 1024 * 1024
ACCIDENT_KB = 145 * 1024 * 1024
ACCIDENT = f"{1002} 288992 {ACCIDENT_KB} python3"


def test_the_accident_is_one_account_holding_58_percent_of_the_host():
    """145 of 247 GiB is 58.7%, and it is one row about one uid.

    The number is stated to one decimal because that is the precision a
    threshold in percentage points can act on; a raw byte sum invites a reader
    to compare two estimates to the byte.
    """
    host = ScriptedHost(files={"/proc/meminfo": meminfo(HOST_KB)},
                        commands={("ps",): (0, memory_of(ACCIDENT), "")})
    out = user_memory.snapshot(host)

    assert out["state"] == "PRESENT"
    assert out["total_bytes"] == HOST_KB * 1024
    assert len(out["users"]) == 1
    row = out["users"][0]
    assert row["uid"] == 1002
    assert row["percent_of_host"] == 58.7
    assert row["rss_estimate_bytes"] == ACCIDENT_KB * 1024
    assert row["process_count"] == 1
    assert row["top_pid"] == 288992
    assert row["top_comm"] == "python3"


def test_two_accounts_are_two_entries_and_never_merge():
    """A per-uid reading that merged two accounts would be a host reading.

    Sorted by memory, so the account a person needs to look at is the first
    line rather than a row they have to find.
    """
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(1024 * 1024)},
        commands={("ps",): (0, memory_of("1002 10 524288 pytest",
                                         "1010 20 131072 node",
                                         "1002 11 262144 python3"), "")})
    out = user_memory.snapshot(host)

    assert [row["uid"] for row in out["users"]] == [1002, 1010]
    big = out["users"][0]
    assert big["process_count"] == 2
    assert big["rss_estimate_bytes"] == (524288 + 262144) * 1024
    assert big["top_pid"] == 10          # 512 MiB beats the 256 MiB sibling
    assert big["top_comm"] == "pytest"


def test_a_command_name_with_a_space_stays_a_name():
    """The kernel does not escape `comm`, and a line-split loses the process.

    Splitting the whole line gives five fields for `my prog`, and the sum then
    silently covers one process fewer — a machine that reads as quieter, which
    is the direction that hides a runaway.
    """
    host = ScriptedHost(files={"/proc/meminfo": meminfo(1024 * 1024)},
                        commands={("ps",): (0, memory_of("1002 10 524288 my prog"),
                                            "")})
    out = user_memory.snapshot(host)

    assert out["coverage"]["unparsed"] == 0
    assert out["users"][0]["top_comm"] == "my prog"
    assert out["users"][0]["rss_estimate_bytes"] == 524288 * 1024


def test_a_ps_that_refuses_is_unknown_and_never_zero_memory():
    """A permission refusal is not an idle machine, and it has no `users` key.

    The key's absence is the point: a caller that found `[]` would have been
    handed "nothing is using memory" spelled as a failure, and the card that
    follows is green.
    """
    host = ScriptedHost(commands={("ps",): (1, "", "ps: cannot read /proc")})
    out = user_memory.snapshot(host)

    assert out["state"] == "UNKNOWN"
    assert "users" not in out
    assert "did not answer" in out["reason"]


def test_a_ps_that_times_out_is_unknown_with_the_budget_named():
    host = ScriptedHost(
        commands={("ps",): (-1, "", f"timed out after {user_memory.SAMPLE_TIMEOUT}s")})
    out = user_memory.snapshot(host)

    assert out["state"] == "UNKNOWN"
    assert "timed out" in out["reason"]
    assert "users" not in out


def test_an_empty_table_is_not_an_idle_machine():
    """`ps -eo` always lists at least this process and pid 1.

    Zero rows is therefore a read that produced nothing. Summing it would be a
    measurement of zero, which is the same number an idle host produces.
    """
    host = ScriptedHost(files={"/proc/meminfo": meminfo(HOST_KB)},
                        commands={("ps",): (0, "", "")})
    out = user_memory.snapshot(host)

    assert out["state"] == "UNKNOWN"
    assert out["coverage"]["rows"] == 0
    assert "users" not in out


def test_without_a_host_total_there_is_no_percentage_so_it_is_unknown():
    """No `MemTotal`, no share — and a byte sum is not a substitute.

    The threshold this feeds is written in percent of the host, so a document
    carrying bytes and no denominator is one every caller would divide by
    something it does not have.
    """
    host = ScriptedHost(files={"/proc/meminfo": "MemFree: 10 kB\n"},
                        commands={("ps",): (0, memory_of(ACCIDENT), "")})
    out = user_memory.snapshot(host)

    assert out["state"] == "UNKNOWN"
    assert "total memory could not be read" in out["reason"]
    assert "users" not in out


def test_a_meminfo_that_cannot_be_read_at_all_is_the_same_unknown():
    """An unreadable file and a readable one without `MemTotal` are one answer.

    `None` is what `LiveHost.read_text` returns for an `OSError`, so a test that
    scripts it is scripting the permission refusal rather than a missing key.
    """
    host = ScriptedHost(files={"/proc/meminfo": None},
                        commands={("ps",): (0, memory_of(ACCIDENT), "")})
    out = user_memory.snapshot(host)

    assert out["state"] == "UNKNOWN"
    assert "users" not in out


def test_an_incomplete_table_is_partial_and_its_rows_are_not_zeroed():
    """A truncated line is counted, not read as a process using no memory.

    The whole rows are still reported — refusing the measurement would throw
    away the account that is genuinely large because a DIFFERENT line was cut —
    but the coverage says PARTIAL so nobody reads the total as complete.
    """
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(1024 * 1024)},
        commands={("ps",): (0, "1002 10 524288 pytest\n1002 11\nnonsense here now\n",
                            "")})
    out = user_memory.snapshot(host)

    assert out["state"] == "PRESENT"
    assert out["coverage"]["unparsed"] == 2
    assert out["coverage"]["state"] == "PARTIAL"
    assert out["coverage"]["rows"] == 1
    # The one whole row is measured; nothing was added for the cut ones.
    assert out["users"][0]["rss_estimate_bytes"] == 524288 * 1024


def test_a_hostile_command_name_is_data_and_never_a_command():
    """`comm` is whatever the process called itself, and it is never executed.

    Two independent guarantees: the module hands the host a LIST (there is no
    shell to interpret the name), and the only argv it ever asks for is the
    `ps` line itself. The assertion is on what the host was asked, which is the
    checkable half of "no shell interpolation happened".
    """
    hostile = "x; rm -rf / #`id`$(whoami)"
    host = ScriptedHost(files={"/proc/meminfo": meminfo(1024 * 1024)},
                        commands={("ps",): (0, memory_of(f"1002 10 4096 {hostile}"),
                                            "")})
    out = user_memory.snapshot(host)

    assert out["users"][0]["top_comm"] == hostile
    assert host.reads == [tuple(user_memory.PS_ARGV)]
    assert host.read_texts == ["/proc/meminfo"]


def test_an_injected_total_is_used_and_meminfo_is_not_read_again():
    """A caller that already knows the host size is not asked to measure it twice."""
    host = ScriptedHost(commands={("ps",): (0, memory_of(ACCIDENT), "")})
    out = user_memory.snapshot(host, total_bytes=HOST_KB * 1024)

    assert out["users"][0]["percent_of_host"] == 58.7
    assert host.read_texts == []


def test_a_process_that_left_between_rows_changes_nothing():
    """Rows are independent, and a shrinking set is a smaller measurement.

    One read lists pid 40 and the next does not; both are questions about the
    rows that arrived, so neither raises and neither invents a row for the
    process that is gone.
    """
    host = ScriptedHost(
        files={"/proc/meminfo": meminfo(1024 * 1024)},
        commands={("ps",): [(0, memory_of("1002 30 1024 a", "1002 40 2048 b"), ""),
                            (0, memory_of("1002 30 1024 a"), "")]})

    first = user_memory.snapshot(host)
    second = user_memory.snapshot(host)

    assert first["users"][0]["process_count"] == 2
    assert second["users"][0]["process_count"] == 1
    assert second["users"][0]["rss_estimate_bytes"] == 1024 * 1024


def test_the_top_process_of_two_equal_ones_is_the_same_on_every_read():
    """A tie broken by pid, so an unchanged machine names one process twice.

    Without the tie-break the "top process" is whichever `ps` printed first, and
    a card appears to have changed subject while nothing changed at all.
    """
    rows = memory_of("1002 500 4096 same", "1002 300 4096 same")
    host = ScriptedHost(files={"/proc/meminfo": meminfo(1024 * 1024)},
                        commands={("ps",): (0, rows, "")})
    out = user_memory.snapshot(host)

    assert out["users"][0]["top_pid"] == 300
    assert out["users"][0]["percent_of_host"] == round(100 * 8192 / 1048576, 1)


def test_the_provenance_says_the_number_is_an_estimate_and_says_what_of():
    """Summed RSS double-counts shared pages, and the row has to say so.

    A consumer that relabels this as PSS, or as the kernel's per-uid
    accounting, is reporting a tighter number than was measured — and the wrong
    direction is the one where an account looks like it is doing fine.
    """
    host = ScriptedHost(files={"/proc/meminfo": meminfo(1024 * 1024)},
                        commands={("ps",): (0, memory_of("1002 10 4096 a"), "")})
    out = user_memory.snapshot(host)

    for text in (out["provenance"], out["users"][0]["provenance"]):
        assert "ESTIMATE" in text
        assert "shared" in text
        assert "not PSS" in text


def test_self_visibility_is_a_third_answer_and_not_a_false_one():
    """`None` means not checked; `False` means checked and absent.

    A filtered table and a small one cannot be told apart from inside this
    process — see the module docstring — so the field reports what was looked
    for and what the answer was, and never guesses at `hidepid`.
    """
    host = ScriptedHost(files={"/proc/meminfo": meminfo(1024 * 1024)},
                        commands={("ps",): (0, memory_of("1002 10 4096 a"), "")})

    assert user_memory.snapshot(host)["coverage"]["self_visible"] is None
    assert user_memory.snapshot(host, self_pid=10)["coverage"]["self_visible"] is True
    assert user_memory.snapshot(host, self_pid=9999)["coverage"]["self_visible"] is False


def test_the_sample_budget_is_the_one_the_inventory_keeps():
    """Transcribed, so a test compares the two rather than review noticing.

    `inventory.BUDGET["fast"]` is the file's own ceiling for `/proc` reads via
    small tools, and this module runs a small tool. A second, silently different
    budget for the same kind of read is how one of them becomes the wrong one.
    """
    inventory = _load("serveros_runtime_inventory", "inventory.py")
    assert user_memory.SAMPLE_TIMEOUT == inventory.BUDGET["fast"]


def test_the_only_verbs_this_module_has_reach_nothing_that_can_change_a_process():
    """Read-only by construction, checked against the source rather than promised.

    §25 forbids acting on this measurement by name — `high memory => kill` — and
    the structural half of that is that there is no shell and no verb. A test
    says so because a docstring saying so is a claim about the day it was
    written.

    Read through the parse tree rather than as text: this file's own docstring
    names the verbs it does not use, so a substring search would fail on the
    prose that makes the promise and pass on a comment that had been edited.
    """
    tree = ast.parse((RUNTIME / "user_memory.py").read_text())

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(f"{getattr(node.value, 'id', '?')}.{node.attr}")
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)

    for verb in ("kill", "killpg", "system", "Popen", "call", "check_output",
                 "popen", "setsid", "setuid", "setgid"):
        assert verb not in names, f"{verb} has no business in a read-only producer"

    # Every subprocess call there IS must be shell-free. The one `run` in the
    # file is the `ps` read, and `shell=True` anywhere would put a kernel-chosen
    # command name in front of a shell.
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert calls, "the module stopped shelling out entirely, which this test cannot see"
    for call in calls:
        assert not any(kw.arg == "shell" for kw in call.keywords), (
            "a subprocess call in a read-only producer passes `shell`")
