"""Wire protocol shared by the Windows node connector and its Fleet client.

Both halves import this file, so the signature the signing side computes and
the signature the verifying side recomputes cannot drift. That is the only
reason it is a module rather than a paragraph in each side's README.

Shape
-----
A node connector dials **out** to the Fleet surface (an SSH reverse tunnel
today) and serves a small, fixed, allow-listed HTTP API on the far end of that
tunnel. Nothing else on the device is reachable through it: there is no
`exec`, no generic provider passthrough, and no way to name a path. The
endpoint set below is the whole of the surface.

Authentication
--------------
Every request is signed with a per-device secret that never leaves either
machine's own disk:

    base = METHOD \\n PATH \\n sha256hex(body) \\n device_ref \\n timestamp \\n nonce
    signature = hex(HMAC-SHA256(secret, base))

The verifier rejects, in this order:

*   a timestamp outside the window (replay of an old capture),
*   a nonce it has already seen (replay of a fresh capture),
*   a signature that does not match (a caller that does not hold the secret),
*   a `device_ref` that is not the one this connector is bound to (a secret
    reused across two devices).

Order matters: checking the signature first would make a replay look like a
bad-secret problem, and checking the device last would let a wrong-device
request consume a nonce.

The body is hashed, not signed raw, so that a large transcript read does not
have to be held in memory twice on either side.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

SIGNATURE_HEADER = "X-Fleet-Signature"
DEVICE_HEADER = "X-Fleet-Device"
TIMESTAMP_HEADER = "X-Fleet-Timestamp"
NONCE_HEADER = "X-Fleet-Nonce"
PROTOCOL_HEADER = "X-Fleet-Protocol"

PROTOCOL = "serveros/fleet-node/v1"
WINDOW_SECONDS = 120

# The closed set of node endpoints. A request for anything else is refused by
# the connector *by name*, before routing, so an added route is a deliberate
# edit to this tuple rather than a side effect of a framework's default.
ROUTES = (
    "GET /v1/health",
    "GET /v1/self",
    "GET /v1/sessions",
    "POST /v1/sessions",
    "GET /v1/sessions/{id}",
    "POST /v1/sessions/{id}/read",
    "POST /v1/sessions/{id}/send",
    "GET /v1/runs/{run_id}",
)


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body or b"").hexdigest()


def base_string(method: str, path: str, body: bytes, device_ref: str, timestamp: str, nonce: str) -> str:
    return "\n".join((method.upper(), path, body_digest(body), device_ref, timestamp, nonce))


def sign(secret: str, *, method: str, path: str, body: bytes, device_ref: str,
         timestamp: str | None = None, nonce: str | None = None) -> dict[str, str]:
    """Headers a caller attaches. Timestamp and nonce are returned for the log."""
    ts = timestamp or str(int(time.time()))
    nz = nonce or secrets.token_hex(16)
    signature = hmac.new(
        secret.encode("utf-8"),
        base_string(method, path, body, device_ref, ts, nz).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        DEVICE_HEADER: device_ref,
        TIMESTAMP_HEADER: ts,
        NONCE_HEADER: nz,
        SIGNATURE_HEADER: signature,
        PROTOCOL_HEADER: PROTOCOL,
    }


class SignatureError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class ReplayCache:
    """A bounded set of seen nonces.

    Size-bounded rather than time-bounded on purpose: the timestamp window
    already bounds how long a captured nonce is useful, and an attacker who can
    flood this past its capacity gains nothing but a replayed request inside
    the window they could already have sent once.
    """

    def __init__(self, capacity: int = 4096) -> None:
        self.capacity = capacity
        self._seen: dict[str, float] = {}

    def seen(self, nonce: str) -> bool:
        self._evict()
        if nonce in self._seen:
            return True
        self._seen[nonce] = time.time()
        return False

    def _evict(self) -> None:
        if len(self._seen) <= self.capacity:
            return
        cutoff = time.time() - WINDOW_SECONDS * 4
        for key in [k for k, at in self._seen.items() if at < cutoff]:
            self._seen.pop(key, None)
        while len(self._seen) > self.capacity:
            self._seen.pop(next(iter(self._seen)), None)


def verify(
    secret: str,
    *,
    method: str,
    path: str,
    body: bytes,
    headers: dict[str, str],
    expected_device_ref: str,
    replay: ReplayCache,
    now: float | None = None,
) -> str:
    """Verify a signed request, returning the device_ref it authenticated as."""
    lowered = {k.lower(): v for k, v in headers.items()}

    def header(name: str) -> str | None:
        return lowered.get(name.lower())

    if header(PROTOCOL_HEADER) != PROTOCOL:
        raise SignatureError("PROTOCOL_MISMATCH", f"expected {PROTOCOL}")
    device_ref = header(DEVICE_HEADER)
    if not device_ref:
        raise SignatureError("MISSING_DEVICE", "no device header")
    if device_ref != expected_device_ref:
        raise SignatureError(
            "DEVICE_MISMATCH",
            f"signed as {device_ref!r}; this connector is bound to {expected_device_ref!r}",
        )
    ts_raw = header(TIMESTAMP_HEADER)
    nonce = header(NONCE_HEADER)
    signature = header(SIGNATURE_HEADER)
    if not ts_raw or not nonce or not signature:
        raise SignatureError("MISSING_SIGNATURE", "timestamp, nonce and signature are all required")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise SignatureError("BAD_TIMESTAMP", f"timestamp {ts_raw!r} is not an integer") from None
    clock = now if now is not None else time.time()
    skew = abs(clock - ts)
    if skew > WINDOW_SECONDS:
        raise SignatureError("STALE_REQUEST", f"timestamp is {skew:.0f}s from local time")
    if replay.seen(nonce):
        raise SignatureError("REPLAY", "this nonce has already been used")
    expected = hmac.new(
        secret.encode("utf-8"),
        base_string(method, path, body, device_ref, ts_raw, nonce).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("BAD_SIGNATURE", "signature does not match")
    return device_ref


def split_path_and_query(target: str) -> tuple[str, str]:
    """`/v1/sessions?limit=5` -> (`/v1/sessions`, `limit=5`).

    The signature covers the path only, not the query string, because the query
    is a filter and the signed body carries the operation. A verifier that
    signed the raw target would refuse a request whose parameters were
    reordered — a difference that changes nothing about what was authorized.
    """
    if "?" in target:
        path, _, query = target.partition("?")
        return path, query
    return target, ""


def error_payload(code: str, detail: str) -> dict[str, Any]:
    return {"error": {"code": code, "detail": detail}}
