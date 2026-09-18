"""One account's share of the machine, and the quota it is held to.

The card's first block answers "how much of this machine am I using". That
question used to be answered with host numbers, which belong to every account
at once: 128 cores and 247 GiB are not one person's, and a reader shown them
under their own name has been told something false in the direction nobody
checks.

Two hazards below are worth more than the rest of the file, and both are about
a number that comes out *wrong* rather than missing:

* `/proc/<pid>/stat` puts the command name in parentheses and does not escape
  what is inside them. Splitting the whole line shifts every later field when
  the name contains a space, so utime and stime are read from where `minflt`
  and `cminflt` live -- still integers, still plausible, and wrong.

* `xfs_quota` prints blocks in 1024-byte units and does not say so. Read as
  512-byte units, a 450 GiB soft limit becomes 225 GiB: the account looks
  half as constrained as it is.

The host is scripted rather than faked, and an unscripted read raises. A
double that answers `{}` to everything tests the double -- and a read this
code did not expect is exactly the thing worth failing on here.
"""
from __future__ import annotations

import importlib.util
import os
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


inventory_mod = _load("serveros_runtime_inventory", "inventory.py")


class ScriptedHost:
    """A host that answers from a fixture, and refuses anything else.

    `files` is keyed by path and `commands` by argument prefix. Either value
    may be a list, consumed one call at a time -- which is how a test says
    "the machine read differently the second time" without a fake clock.
    """

    def __init__(self, files=None, commands=None):
        self.files = dict(files or {})
        self.commands = dict(commands or {})
        self.slept = []

    def read(self, args, timeout):
        for prefix, value in self.commands.items():
            if tuple(args[:len(prefix)]) != tuple(prefix):
                continue
            if isinstance(value, list):
                return value.pop(0)
            return value
        raise AssertionError(f"unscripted command: {list(args)!r}")

    def read_text(self, path):
        if path not in self.files:
            raise AssertionError(f"unscripted read_text: {path!r}")
        value = self.files[path]
        if isinstance(value, list):
            return value.pop(0) if value else None
        return value

    def sleep(self, seconds):
        # Recorded, never taken. A test that waited a real second per call
        # would be testing the clock.
        self.slept.append(seconds)


class WithFixedHome(inventory_mod.Inventory):
    """`Inventory` whose root contract answers one fixed path.

    `_root` is the only place this module reaches for `paths.py`, which reads
    the running machine's own host profile. What is under test here is the
    quota parser and the mount lookup, so the profile is stubbed rather than
    the behaviour that consumes it.
    """

    def __init__(self, host, home):
        super().__init__(host)
        self._fixed_home = home

    def _root(self, name):
        return self._fixed_home if name == "home" else None


def proc_stat(pid, comm="bash", utime=0, stime=0):
    """One `/proc/<pid>/stat`, with the columns that matter set.

    After the trailing `)` the state is index 0, so utime is 11 and stime 12.
    """
    head = ["S", "1", str(pid), str(pid), "0", "-1", "4194304",
            "100", "0", "0", "0", str(utime), str(stime)]
    return f"{pid} ({comm}) " + " ".join(head + ["0"] * 20) + "\n"


def cpuinfo(logical, model="Fake CPU"):
    return "\n".join(
        f"processor\t: {i}\nmodel name\t: {model}\ncpu cores\t: 1\nsiblings\t: 2"
        for i in range(logical)) + "\n"


# 8 GiB total, 4 GiB available.
MEMINFO = "MemTotal:       8388608 kB\nMemAvailable:   4194304 kB\n"
# One process, 1 GiB resident.
PS_ONE = "  100  1048576\n"

XFS_QUOTA_OUT = (
    "Disk quotas for User vince (1002)\n"
    "Filesystem   Blocks    Quota     Limit  Warn/Time      Mounted on\n"
    "/dev/vdb   23927360 471859200 524288000   00 [--------] /data\n"
)


def base_files(stat_lines):
    return {
        "/proc/cpuinfo": cpuinfo(4),
        "/proc/meminfo": MEMINFO,
        "/proc/100/stat": stat_lines,
    }


def test_cpu_is_a_delta_between_two_samples():
    """100 jiffies at 100 Hz over a 1.0 s window is one core-second per second.

    The window is passed in rather than taken from `USER_SAMPLE_SECONDS` so the
    arithmetic in the assertion is the arithmetic under test.
    """
    host = ScriptedHost(
        files=base_files([proc_stat(100, utime=10, stime=2),
                          proc_stat(100, utime=110, stime=2)]),
        commands={("ps", "-u"): (0, PS_ONE, "")},
    )
    result = inventory_mod.Inventory(host).user_summary(interval=1.0)

    assert result["state"] == "PRESENT"
    assert result["cpu_cores"] == 1.0
    assert result["cpu_percent_of_host"] == 25.0   # four logical CPUs
    assert result["sample_seconds"] == 1.0
    # The two samples are taken around one wait, and the wait is the window.
    assert host.slept == [1.0]


def test_a_command_name_with_a_space_does_not_shift_the_counters():
    """The name is not escaped, so the columns after it move.

    `ps -o cputimes` cannot be used instead: it is whole seconds, so every
    window shorter than a second reads as exactly zero. So the parse has to be
    right, and "right" means counting from after the last `)`, not splitting
    the line.
    """
    host = ScriptedHost(
        files=base_files([proc_stat(100, comm="my prog", utime=10, stime=2),
                          proc_stat(100, comm="my prog", utime=110, stime=2)]),
        commands={("ps", "-u"): (0, PS_ONE, "")},
    )
    result = inventory_mod.Inventory(host).user_summary(interval=1.0)

    # A line-splitting parse takes `minflt` and `cminflt` for the CPU times,
    # which here are 100 and 0, so the delta would come out as -100.
    assert result["state"] == "PRESENT"
    assert result["cpu_cores"] == 1.0


def test_a_process_that_left_between_the_samples_is_unavailable_not_idle():
    """A shrunken set is not a smaller number, it is not a measurement.

    Clamping the negative delta to zero would print "idle" for what is really
    "these two totals are of different things".
    """
    host = ScriptedHost(
        files={
            "/proc/cpuinfo": cpuinfo(4),
            "/proc/meminfo": MEMINFO,
            "/proc/100/stat": [proc_stat(100, utime=50, stime=0),
                               proc_stat(100, utime=52, stime=0)],
            # Only read in the first sample, where the second process exists.
            "/proc/200/stat": proc_stat(200, utime=60, stime=0),
        },
        commands={("ps", "-u"): [(0, "  100  1024\n  200  1024\n", ""),
                                 (0, "  100  1024\n", "")]},
    )
    result = inventory_mod.Inventory(host).user_summary(interval=1.0)

    assert result["state"] == inventory_mod.UNAVAILABLE
    assert "process set changed" in result["reason"]
    assert "cpu_cores" not in result


def test_a_ps_that_refuses_is_unavailable_not_zero_processes():
    host = ScriptedHost(commands={("ps", "-u"): (1, "", "ps: unknown user")})
    result = inventory_mod.Inventory(host).user_summary()

    assert result["state"] == inventory_mod.UNAVAILABLE
    assert "process_count" not in result


def test_memory_says_it_is_rss_and_not_kernel_accounting():
    """1 GiB resident of 8 GiB, and the word for what that is.

    The kernel's per-uid accounting is a different number, and a caller that
    needs it must go and get it rather than quietly relabel this one.
    """
    host = ScriptedHost(
        files=base_files([proc_stat(100, utime=0, stime=0),
                          proc_stat(100, utime=0, stime=0)]),
        commands={("ps", "-u"): (0, PS_ONE, "")},
    )
    result = inventory_mod.Inventory(host).user_summary(interval=1.0)

    assert result["memory_rss_bytes"] == 1048576 * 1024
    assert result["memory_percent_of_host"] == 12.5
    assert result["process_count"] == 1
    assert result["uid"] == os.getuid()
    assert "RSS" in result["provenance"]
    assert "not kernel per-uid accounting" in result["provenance"]


def test_quota_blocks_are_1024_byte_units_not_512():
    """471859200 blocks is 450 GiB, which is what `xfs_quota -h` prints.

    Read as 512-byte units the same column is 225 GiB -- a factor of two, in
    the direction that makes an account look comfortable.
    """
    host = ScriptedHost(commands={
        ("findmnt", "-no", "TARGET"): (0, "/data\n", ""),
        ("xfs_quota", "-c"): (0, XFS_QUOTA_OUT, ""),
        ("id", "-un"): (0, "vince\n", ""),
    })
    result = WithFixedHome(host, "/home/vince").user_quota()

    assert result["state"] == "PRESENT"
    assert result["mount"] == "/data"
    assert result["soft_limit_bytes"] == 450 * 1024 ** 3
    assert result["limit_bytes"] == 500 * 1024 ** 3
    assert result["used_bytes"] == 23927360 * 1024
    # The card's percentage is against the limit an account is actually held
    # to, not the soft one it is merely warned about.
    assert result["used_percent"] == round(100 * (23927360 * 1024) / (500 * 1024 ** 3), 1)


def test_a_filesystem_with_no_quota_is_not_configured_not_zero_used():
    """The header with no row under it is an answer, and it is not "0 used"."""
    host = ScriptedHost(commands={
        ("findmnt", "-no", "TARGET"): (0, "/data\n", ""),
        ("xfs_quota", "-c"): (0, "Disk quotas for User vince (1002)\n", ""),
        ("id", "-un"): (0, "vince\n", ""),
    })
    result = WithFixedHome(host, "/home/vince").user_quota()

    assert result["state"] == "NOT_CONFIGURED"
    assert "used_bytes" not in result
    assert "used_percent" not in result


def test_a_quota_that_cannot_be_read_is_unavailable_not_absent():
    """`xfs_quota` failing is not the same fact as a filesystem without quota."""
    host = ScriptedHost(commands={
        ("findmnt", "-no", "TARGET"): (0, "/data\n", ""),
        ("xfs_quota", "-c"): (-1, "", "timed out after 8.0s"),
        ("id", "-un"): (0, "vince\n", ""),
    })
    result = WithFixedHome(host, "/home/vince").user_quota()

    assert result["state"] == inventory_mod.UNAVAILABLE
    assert "timed out" in result["reason"]
