"""Username, password and a time-based one-time code: the human login proof.

Why this module exists
----------------------
The consent page used to ask for the owner's personal access token: one string,
pasted into a password box, compared in constant time. That is a *credential*
check, not a *login* — it cannot be remembered, it cannot be rotated without
editing every client, and it looks enough like a password that people treat it
like one and reuse it.

This module keeps the human proof but changes it into the shape people already
know: an account name, a password that is stored only as a slow hash, and a
six-digit code derived from a shared secret and the clock (RFC 6238). Nothing
here talks to the network, and the third factor is a file: no dependency, no
vendor, no SMS.

What is stored, and what never is
---------------------------------
Two anchor files, both `0600`, both referenced by PATH from the binding:

    <password_anchor>   vince:$scrypt$<n>$<r>$<p>$<salt_b64>$<digest_b64>
    <totp_anchor>       vince:<BASE32SECRET>

The plaintext password and the six-digit code are never written anywhere. The
secret is the one durable secret — if it leaks, the second factor is gone — so
it lives in its own file, is never logged, and is never returned by any
function here. `verify_totp` compares, and hands back the counter step so the
caller can refuse a replay; it does not hand back the secret.

Replay
------
A code is valid for `period` seconds on either side of a clock skew window. A
code observed by an attacker inside that window could otherwise be presented a
second time, so the accepted step is recorded and any step at or below it is
refused. The record is persisted because a process restart must not reopen a
window that was already used.

Throttling
----------
Deliberately keyed by USERNAME, not by address. This server sits behind a
tunnel: every public request's peer is loopback, and a forwarded-for header is
supplied by the caller and therefore attacker-controlled. An address-keyed
limiter here would be a limiter that only one honest client ever trips.
"""
from __future__ import annotations

import base64
import binascii
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

#: `hashlib.scrypt` parameters. 16 MiB is the value the standard library
#: documents as interactive; it is re-read from the anchor on every verify, so
#: an operator can raise it without this file changing.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

TOTP_PERIOD = 30
TOTP_DIGITS = 6
#: One step of skew in each direction. Clocks drift; a code refused because the
#: phone is four seconds behind reads as "the server is broken".
TOTP_SKEW_STEPS = 1

COOKIE_NAME = "fleet_session"


class HumanAuthError(Exception):
    """A configuration or storage problem, not a wrong password."""


# --------------------------------------------------------------------------
# Anchor files
# --------------------------------------------------------------------------

def _read_anchor(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HumanAuthError(f"{path} does not exist") from None
    except OSError as exc:
        raise HumanAuthError(f"{path} is not readable: {exc.strerror}") from None


def _check_private(path: Path) -> None:
    """Refuse a credential file other users on this host can read.

    The mode is the statement on POSIX; on Windows `st_mode` carries no useful
    group/other bits, so the check is skipped there rather than pretending to
    pass. A symlink is refused everywhere: the anchor names a file, and a link
    is a second name whose owner this module did not verify.
    """
    if os.name == "nt":
        return
    try:
        st = path.lstat()
    except FileNotFoundError:
        raise HumanAuthError(f"{path} does not exist") from None
    except OSError as exc:
        raise HumanAuthError(f"{path} cannot be inspected: {exc.strerror}") from None
    if stat.S_ISLNK(st.st_mode):
        raise HumanAuthError(f"{path} is a symlink; the anchor must be the file itself")
    if (st.st_mode & 0o077) != 0:
        raise HumanAuthError(f"{path} is readable by group or other; it must be 0600")


@dataclass(frozen=True)
class Credential:
    username: str
    salt: bytes
    digest: bytes
    n: int = SCRYPT_N
    r: int = SCRYPT_R
    p: int = SCRYPT_P


def load_passwords(path: Path) -> dict[str, Credential]:
    """Parse the password anchor. Malformed lines are refused, not skipped.

    Skipping is the failure mode worth avoiding: a typo would silently remove an
    account, and the symptom would be "my password stopped working" rather than
    "the file is wrong".
    """
    _check_private(path)
    out: dict[str, Credential] = {}
    for number, raw in enumerate(_read_anchor(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) != 2:
            raise HumanAuthError(f"{path}:{number}: expected `<user>:$scrypt$...`")
        username, spec = parts[0].strip(), parts[1].strip()
        fields = spec.split("$")
        if len(fields) != 7 or fields[0] != "" or fields[1] != "scrypt":
            raise HumanAuthError(f"{path}:{number}: expected `$scrypt$n$r$p$salt$hash`")
        try:
            n, r, p = int(fields[2]), int(fields[3]), int(fields[4])
            salt = base64.b64decode(fields[5], validate=True)
            digest = base64.b64decode(fields[6], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise HumanAuthError(f"{path}:{number}: {exc}") from None
        if not username or not salt or not digest:
            raise HumanAuthError(f"{path}:{number}: empty field")
        out[username] = Credential(username=username, salt=salt, digest=digest, n=n, r=r, p=p)
    if not out:
        raise HumanAuthError(f"{path} contains no accounts")
    return out


def load_totp(path: Path) -> dict[str, bytes]:
    """Parse the TOTP anchor. The decoded secret never leaves this module."""
    _check_private(path)
    out: dict[str, bytes] = {}
    for number, raw in enumerate(_read_anchor(path).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        username, _, encoded = line.partition(":")
        username = username.strip()
        secret = _decode_base32(encoded.strip())
        if not username or not secret:
            raise HumanAuthError(f"{path}:{number}: expected `<user>:<base32 secret>`")
        out[username] = secret
    if not out:
        raise HumanAuthError(f"{path} contains no TOTP secrets")
    return out


def _decode_base32(text: str) -> bytes:
    cleaned = "".join(text.split()).rstrip("=").upper()
    padding = "=" * (-len(cleaned) % 8)
    try:
        return base64.b32decode(cleaned + padding)
    except (binascii.Error, ValueError):
        return b""


def encode_base32(raw: bytes) -> str:
    return base64.b32encode(raw).decode("ascii").rstrip("=")


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------

def verify_password(credential: Credential, password: str) -> bool:
    """Constant-time comparison of a scrypt digest.

    `scrypt` is recomputed, never compared against a stored *hash of the
    password* — the salt and cost live in the anchor, so raising `n` later
    invalidates old entries visibly rather than silently.
    """
    if not password:
        return False
    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=credential.salt,
            n=credential.n, r=credential.r, p=credential.p,
            maxmem=SCRYPT_MAXMEM, dklen=len(credential.digest),
        )
    except (ValueError, MemoryError):
        return False
    return hmac.compare_digest(candidate, credential.digest)


def totp_at(secret: bytes, *, at: float | None = None) -> tuple[str, int]:
    """The code and counter step for a moment in time."""
    step = int((time.time() if at is None else at) // TOTP_PERIOD)
    message = struct.pack(">Q", step)
    digest = hmac.new(secret, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS), step


def verify_totp(secret: bytes, code: str, *, last_step: int | None = None,
                at: float | None = None) -> tuple[bool, int]:
    """Whether `code` is current, and the step it matched.

    Returns `(False, 0)` for a wrong code. A matched step at or below
    `last_step` is a replay and is refused as `(False, 0)` — the caller must not
    advance the recorded step for a refused attempt.
    """
    presented = (code or "").strip().replace(" ", "")
    if len(presented) != TOTP_DIGITS or not presented.isdigit():
        return False, 0
    now = time.time() if at is None else at
    for delta in range(-TOTP_SKEW_STEPS, TOTP_SKEW_STEPS + 1):
        candidate, step = totp_at(secret, at=now + delta * TOTP_PERIOD)
        if hmac.compare_digest(candidate, presented):
            if last_step is not None and step <= last_step:
                return False, 0
            return True, step
    return False, 0


# --------------------------------------------------------------------------
# Throttling
# --------------------------------------------------------------------------

class LoginThrottle:
    """Failures per username, with exponential lockout.

    Kept in memory on purpose: it is a brake on online guessing, and a restart
    that clears it costs an attacker a fresh attempt budget — which they already
    had by waiting. Persisting it would add a write to every failed login.
    """

    def __init__(self, *, limit: int = 5, window: float = 300.0,
                 base_delay: float = 2.0, cap: float = 300.0) -> None:
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.base_delay = float(base_delay)
        self.cap = float(cap)
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, key: str, *, now: float | None = None) -> float:
        moment = time.time() if now is None else now
        until = self._locked_until.get(key, 0.0)
        return max(0.0, until - moment)

    def record_failure(self, key: str, *, now: float | None = None) -> float:
        """Record one failure and return the seconds the caller must now wait."""
        moment = time.time() if now is None else now
        failures = [t for t in self._failures.get(key, []) if moment - t < self.window]
        failures.append(moment)
        self._failures[key] = failures
        # The Nth failure locks, not the (N+1)th: `limit` is the number of free
        # attempts an attacker gets, and an implementation that locks one later
        # is an implementation that allows one more than it says.
        if len(failures) < self.limit:
            return 0.0
        delay = min(self.cap, self.base_delay * (2 ** (len(failures) - self.limit)))
        self._locked_until[key] = moment + delay
        return delay

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)


class TotpReplayGuard:
    """The last accepted counter step per account, persisted.

    A missing or unreadable file is treated as "no step recorded": the cost of
    being wrong here is one replay window after a crash, while refusing to log
    in because a state file went missing would be a self-inflicted outage.
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._steps: dict[str, int] = {}
        if path is not None:
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._steps = {str(k): int(v) for k, v in loaded.items() if isinstance(v, int)}
            except (FileNotFoundError, OSError, ValueError, TypeError):
                self._steps = {}

    def last_step(self, username: str) -> int | None:
        return self._steps.get(username)

    def accept(self, username: str, step: int) -> None:
        self._steps[username] = step
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(str(self.path), flags, 0o600)
            try:
                os.write(fd, json.dumps(self._steps, sort_keys=True).encode("utf-8"))
            finally:
                os.close(fd)
        except OSError:
            # The login itself already succeeded. Losing the record widens one
            # replay window; failing the request would be a worse trade.
            pass


# --------------------------------------------------------------------------
# Session cookie
# --------------------------------------------------------------------------

def _sign(key: bytes, purpose: bytes, payload: bytes) -> str:
    mac = hmac.new(key, purpose + b"." + payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode("ascii")


def issue_session(key: bytes, username: str, *, ttl: float,
                  now: float | None = None) -> str:
    """A signed `username:expiry:nonce` value. Carries no secret but the signature."""
    moment = time.time() if now is None else now
    payload = f"{username}:{int(moment + ttl)}:{secrets.token_urlsafe(12)}".encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    return f"{encoded}.{_sign(key, b'session', payload)}"


def verify_session(key: bytes, value: str | None, *, now: float | None = None) -> str | None:
    """The username a valid cookie names, or `None`."""
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding)
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(_sign(key, b"session", payload), signature):
        return None
    try:
        username, expiry, _nonce = payload.decode("utf-8").split(":")
        expires_at = int(expiry)
    except (ValueError, UnicodeDecodeError):
        return None
    if expires_at <= (time.time() if now is None else now):
        return None
    return username


def csrf_for(key: bytes, session_value: str) -> str:
    """A token bound to the cookie it was rendered with.

    Only meaningful once a cookie exists: before login the form carries nothing
    ambient for a cross-site request to ride on, so there is nothing to bind.
    """
    return _sign(key, b"csrf", session_value.encode("utf-8"))


# --------------------------------------------------------------------------
# The composition used by the HTTP surface
# --------------------------------------------------------------------------

class HumanLogin:
    """Password and one-time code, with the state that makes them safe to expose.

    One object owns the anchors, the throttle, the replay record and the cookie
    key, so the transport never has to hold a secret or remember to reset a
    counter. It answers two questions — "is this login good" and "who is this
    cookie" — and nothing else.
    """

    def __init__(self, *, passwords: dict[str, Credential],
                 totp: dict[str, bytes] | None, key: bytes, session_ttl: float,
                 throttle: LoginThrottle | None = None,
                 replay: TotpReplayGuard | None = None) -> None:
        self.passwords = passwords
        self.totp = totp
        self.key = key
        self.session_ttl = float(session_ttl)
        self.throttle = throttle or LoginThrottle()
        self.replay = replay or TotpReplayGuard(None)
        # A credential that no password matches, used when the username is
        # unknown: without it, "no such user" answers in microseconds and
        # "wrong password" answers in milliseconds, which is a directory.
        self._decoy = Credential(
            username="", salt=secrets.token_bytes(16),
            digest=secrets.token_bytes(32),
        )

    @property
    def totp_required(self) -> bool:
        return bool(self.totp)

    @classmethod
    def from_paths(cls, *, password_anchor: Path, totp_anchor: Path | None,
                   replay_path: Path | None, key: bytes, session_ttl: float,
                   throttle: LoginThrottle | None = None) -> "HumanLogin":
        return cls(
            passwords=load_passwords(password_anchor),
            totp=load_totp(totp_anchor) if totp_anchor is not None else None,
            key=key, session_ttl=session_ttl, throttle=throttle,
            replay=TotpReplayGuard(replay_path),
        )

    def verify(self, username: str, password: str, code: str = "") -> tuple[bool, str]:
        """`(ok, reason)`. The reason is for the log, never for the page.

        The page gets one sentence for every failure — a message that says
        "no such account" is a message that enumerates accounts.
        """
        username = (username or "").strip()
        if not username:
            return False, "missing_username"
        wait = self.throttle.retry_after(username)
        if wait > 0:
            return False, f"throttled:{int(wait)}s"
        credential = self.passwords.get(username)
        if credential is None:
            verify_password(self._decoy, password)
            self.throttle.record_failure(username)
            return False, "unknown_user"
        if not verify_password(credential, password):
            self.throttle.record_failure(username)
            return False, "bad_password"
        if self.totp is not None:
            secret = self.totp.get(username)
            if secret is None:
                self.throttle.record_failure(username)
                return False, "no_totp_secret"
            ok, step = verify_totp(secret, code, last_step=self.replay.last_step(username))
            if not ok:
                self.throttle.record_failure(username)
                return False, "bad_totp"
            self.replay.accept(username, step)
        self.throttle.reset(username)
        return True, "ok"

    def issue(self, username: str) -> str:
        return issue_session(self.key, username, ttl=self.session_ttl)

    def user(self, cookie: str | None) -> str | None:
        username = verify_session(self.key, cookie)
        if username is None or username not in self.passwords:
            return None
        return username

    def csrf(self, cookie: str | None) -> str | None:
        return csrf_for(self.key, cookie) if cookie else None

    def csrf_ok(self, cookie: str | None, presented: str | None) -> bool:
        expected = self.csrf(cookie)
        if expected is None:
            return True  # no cookie, nothing ambient to protect
        return bool(presented) and hmac.compare_digest(expected, presented)


# --------------------------------------------------------------------------
# Operator helper: write the anchors without putting a secret in argv
# --------------------------------------------------------------------------

def _hash_line(username: str, password: str, *, n: int = SCRYPT_N,
               r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                            maxmem=SCRYPT_MAXMEM, dklen=32)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"{username}:$scrypt${n}${r}${p}${b64(salt)}${b64(digest)}"


def main(argv: list[str] | None = None) -> int:
    """`python -m fleet_mcp.human_auth <password-anchor> <totp-anchor>`.

    Prompts on the terminal; the password is never an argument, because on this
    host `ps` is readable and arguments are not private.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m fleet_mcp.human_auth PASSWORD_ANCHOR TOTP_ANCHOR", file=sys.stderr)
        return 2
    password_path, totp_path = Path(args[0]), Path(args[1])
    username = input("username: ").strip()
    password = getpass.getpass("password: ")
    if not username or not password:
        print("username and password must be non-empty", file=sys.stderr)
        return 2
    if getpass.getpass("repeat password: ") != password:
        print("passwords do not match", file=sys.stderr)
        return 2
    secret = secrets.token_bytes(20)
    for path, line in (
        (password_path, _hash_line(username, password)),
        (totp_path, f"{username}:{encode_base32(secret)}"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    print(f"wrote {password_path} and {totp_path}")
    print("Add this to your authenticator now; it is not shown again:")
    print(f"  secret: {encode_base32(secret)}")
    print(f"  uri:    otpauth://totp/{username}?secret={encode_base32(secret)}&issuer=ServerOS+Fleet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
