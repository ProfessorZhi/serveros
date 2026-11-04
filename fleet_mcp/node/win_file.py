"""Read an owner-fixed file on Windows with the same promises as the POSIX path.

The connector's POSIX reader (`connector._read_bounded_no_follow`) relies on
`O_NOFOLLOW` and `st_uid`, neither of which exists on Windows. This module keeps
each promise with the Windows mechanism that actually provides it:

| promise | POSIX | here |
|---|---|---|
| no link anywhere in the path | `resolve() == path` + `O_NOFOLLOW` | `resolve() == path`, and the final component is not a reparse point |
| it is a regular file | `S_ISREG` on the descriptor | `lstat` says file, the open handle's index matches `lstat`'s |
| it is this account's | `st_uid == geteuid()` | the file's owner SID is this token's user SID or its default owner SID (what this token stamps on files it creates; elevated administrators stamp BUILTIN\\Administrators) |
| bounded | read at most `cap + 1` | the same |

What it does NOT check, said rather than implied: the file's DACL. A POSIX mode
check refuses a group/other-writable file; here the file is expected under
`%LOCALAPPDATA%`, whose inherited DACL already grants only the owner, SYSTEM and
Administrators. The owner check above is the part a stray file fails.

Every failure is a `ValueError` whose message names the KIND of failure and
never the path or anything read from it; the caller maps it to its own refusal.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path


class WindowsReadRefused(ValueError):
    pass


def _owner_matches_token(path: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    TOKEN_QUERY = 0x0008
    TOKEN_USER_CLASS = 1
    TOKEN_OWNER_CLASS = 4

    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    if advapi32.GetNamedSecurityInfoW(str(path), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                                      ctypes.byref(owner), None, None, None,
                                      ctypes.byref(descriptor)) != 0:
        raise WindowsReadRefused("the owner of the Core snapshot could not be read")
    try:
        token = wintypes.HANDLE()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), TOKEN_QUERY,
                                         ctypes.byref(token)):
            raise WindowsReadRefused("this process's token could not be read")
        try:
            advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]

            def first_sid(which: int):
                size = wintypes.DWORD()
                advapi32.GetTokenInformation(token, which, None, 0, ctypes.byref(size))
                buffer = ctypes.create_string_buffer(size.value)
                if not advapi32.GetTokenInformation(token, which, buffer, size, ctypes.byref(size)):
                    raise WindowsReadRefused("this process's token could not be read")
                # TOKEN_USER and TOKEN_OWNER both begin with the PSID.
                return buffer, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]

            # Either SID the token itself stamps on what it creates: the user, or
            # the default owner (an elevated administrator's token makes
            # BUILTIN\Administrators the owner of new files).
            user_buf, me = first_sid(TOKEN_USER_CLASS)
            owner_buf, my_default_owner = first_sid(TOKEN_OWNER_CLASS)
            advapi32.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            return bool(owner.value) and (bool(advapi32.EqualSid(owner, me))
                                          or bool(advapi32.EqualSid(owner, my_default_owner)))
        finally:
            kernel32.CloseHandle(token)
    finally:
        kernel32.LocalFree(descriptor)


def read_owned_bounded(path: Path, cap: int, *, owner_check=_owner_matches_token) -> bytes:
    """At most `cap + 1` bytes from `path`, or `WindowsReadRefused` saying why."""
    if path.resolve() != path:
        raise WindowsReadRefused(
            "the Core snapshot path does not resolve to itself, so one of its components is "
            "a link or junction; it is an owner-fixed path and must be the file the operator chose")
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise WindowsReadRefused("no Core snapshot at the configured path") from None
    attributes = getattr(before, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise WindowsReadRefused("the Core snapshot is a reparse point (a link), not the file itself")
    if not stat.S_ISREG(before.st_mode):
        raise WindowsReadRefused("the Core snapshot is not a regular file")
    if not owner_check(path):
        raise WindowsReadRefused(
            "the Core snapshot is owned by a different account than the one this connector runs as")
    with open(path, "rb") as handle:
        after = os.fstat(handle.fileno())
        # The file opened is the file checked: same volume, same file index.
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise WindowsReadRefused("the Core snapshot changed between the check and the open")
        raw = handle.read(cap + 1)
    if len(raw) > cap:
        raise WindowsReadRefused(
            f"the Core snapshot is past the {cap} bytes this route carries; a Core-Lite document "
            f"is a bounded shape")
    return raw
