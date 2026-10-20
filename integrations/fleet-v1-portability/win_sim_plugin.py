"""Simulate the Windows absence of POSIX-only modules for `--collect-only`.

Load with `-p win_sim_plugin`; the plugin writes nothing and changes nothing
on disk. `fcntl` is imported by pytest itself before this plugin runs, so it is
evicted from `sys.modules` and a meta-path finder refuses the NEXT import.
Product and test modules that do `import fcntl` then fail exactly as they do on
Windows. `os.sysconf` and the other POSIX-only `os` attributes are replaced
with a function that raises `AttributeError`, which is what Windows does.

This is an attribution instrument, not a Windows runtime. It deliberately does
not emulate the default code page; that class is measured separately.
"""
from __future__ import annotations

import asyncio  # noqa: F401  (initialise it with the real fcntl, as Windows would)
import importlib.abc
import os
import sys
import unittest.mock  # noqa: F401  (imports asyncio; keep that out of the simulation)

BLOCKED = {
    "fcntl", "pwd", "grp", "resource", "termios", "tty", "pty", "syslog",
    "crypt", "spwd", "nis",
}


class _WindowsAbsence(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ModuleNotFoundError(
                f"No module named {fullname!r} (Windows simulation)")
        return None


for _name in BLOCKED:
    sys.modules.pop(_name, None)
sys.meta_path.insert(0, _WindowsAbsence())


def _absent(*_args, **_kwargs):
    raise AttributeError("module 'os' has no attribute (Windows simulation)")


for _attr in (
    "sysconf", "getuid", "geteuid", "getgid", "getegid", "getgroups",
    "fork", "forkpty", "setsid", "setuid", "seteuid", "setgid", "setegid",
    "initgroups", "chown", "chroot", "getloadavg", "uname", "statvfs",
    "wait4", "killpg", "sched_getaffinity", "sched_setaffinity", "getpgid",
    "getsid", "setpgid", "pipe2",
):
    if hasattr(os, _attr):
        setattr(os, _attr, _absent)
