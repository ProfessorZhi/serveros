#!/usr/bin/env python3
"""#245 / S2 — the OUTER ADAPTER wrapper around the Core reconciliation caller.

WHERE THE DECISION LIVES NOW
----------------------------
The pure decision and the caller both live in the Core:
`runtime/dispatch_attempts.py` owns `reconcile_observation` (what an owner
observation authorizes) and `reconcile_attempt` (ask the injected source, then
record the answer in the EXISTING Agent Control store). `serverosd` calls that
Core function directly, so the daemon's loader closure does not reach this
directory.

WHY THIS FILE STILL EXISTS
--------------------------
It is the outer-adapter construction site and the compatibility facade for the
#299 test suite. It holds no state, opens no store, has no send path, and does
not decide anything: every call delegates to `runtime/dispatch_attempts.py`,
and the provider-specific part remains the observation source OBJECT injected
by the adapter (declared `CLIENT_TURN_ID` only when it can be asked by the id
this node still holds after a lost response).

A source that can only be asked by an id that arrived in the LOST response is
`RESPONSE_ID_ONLY` → `BLOCKED_PROVIDER_SOURCE`. A lookup miss is not an
absence, so `TURN_NOT_FOUND` / `ABSENT` / `MISSING` / `NOT_FOUND` /
`LOOKUP_MISS` normalise to `UNKNOWN`, which settles nothing and keeps the retry
door closed. `NOT_APPLIED` still requires provenance and an intent match at the
Core kernel. Where the provider cannot prove either state, the honest outcome
is EFFECT_UNKNOWN and exactly-once is never claimed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _kernel():
    """The Core module, loaded the way every sibling loader loads it.

    Loaded by path rather than imported as a package because `runtime/` is not
    a Python package in this repository. Reusing the module already in
    `sys.modules` keeps this facade on the SAME rules the daemon and the router
    use — there is one decision, not a second copy in the adapter.
    """
    name = "serveros_runtime_dispatch_attempts"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[2] / "runtime" / "dispatch_attempts.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Re-exported names are the Core's, not a second set. The #299 tests and any
# outer adapter construct against these spellings; an alias keeps one owner.
_KERNEL = _kernel()

CLIENT_TURN_ID = _KERNEL.CLIENT_TURN_ID
RESPONSE_ID_ONLY = _KERNEL.RESPONSE_ID_ONLY
NO_SOURCE = _KERNEL.NO_SOURCE

BLOCKED_PROVIDER_SOURCE = _KERNEL.BLOCKED_PROVIDER_SOURCE
NO_ATTEMPT = _KERNEL.NO_ATTEMPT
NOT_UNKNOWN = _KERNEL.NOT_UNKNOWN
NO_OBSERVATION = _KERNEL.NO_OBSERVATION
SOURCE_UNAVAILABLE = _KERNEL.SOURCE_UNAVAILABLE
LOOKUP_MISS_WORDS = _KERNEL.LOOKUP_MISS_WORDS

source_capability = _KERNEL.source_capability
normalize_observation = _KERNEL.normalize_observation


class EffectReconciler:
    """Outer-adapter facade over the Core's `reconcile_attempt` caller.

    Constructed with an `attempts` handle — the existing Agent Control store
    satisfies `get_dispatch_attempt`, `reconcile_dispatch_attempt`,
    `dispatch_attempts` — and an optional observation source. There is no
    `send` method and no transport: a reconciler that could dispatch is a
    reconciler that can blind-retry, which is the failure it exists to stop.
    """

    def __init__(self, *, attempts, observation_source=None, now=None) -> None:
        self._attempts = attempts
        self._source = observation_source
        self._now = now

    # -- the decision the router uses on a retry ---------------------------

    def retry_verdict(self, key: str, *, digest: str) -> dict:
        """What a retry of this key may do, read from the durable record.

        Pure read. Delegates to `dispatch_attempts.reconcile`, so the adapter
        cannot grow a second opinion about CALL_NOT_SENT / EFFECT_UNKNOWN /
        SETTLED — the Core kernel is the one place that answer lives.
        """
        recorded = self._attempts.get_dispatch_attempt(str(key))
        return _KERNEL.reconcile(recorded, digest=digest)

    # -- one attempt -------------------------------------------------------

    def reconcile(self, key: str, *, principal, writer=None,
                  current_writer=None) -> dict:
        """Delegate one reconciliation to the Core. Never dispatches."""
        return _KERNEL.reconcile_attempt(
            self._attempts, key, principal=principal,
            observation_source=self._source, writer=writer,
            current_writer=current_writer, now=self._now)

    # -- a reconnect -------------------------------------------------------

    def reconcile_unknown(self, *, principal, writer=None, current_writer=None,
                          work_item=None) -> dict:
        """Delegate the reconnect sweep to the Core. Never replays."""
        return _KERNEL.reconcile_unknown(
            self._attempts, principal=principal,
            observation_source=self._source, writer=writer,
            current_writer=current_writer, work_item=work_item)


def tree_shape() -> dict:
    """What this adapter declares, for a contract reader."""
    return {
        "capabilities": list(_KERNEL.tree_shape()["source_capabilities"]),
        "blocked": BLOCKED_PROVIDER_SOURCE,
        "lookup_misses": list(LOOKUP_MISS_WORDS),
        "owner": "runtime/agent_control_store.py#dispatch_attempts",
        "caller": "runtime/dispatch_attempts.py#reconcile_attempt",
        "role": "outer adapter facade; the Core does not load this file",
        "not_claimed": ("the adapter cannot verify that a source is truthful; "
                        "it enforces provenance and capability, and an "
                        "unproven outcome stays EFFECT_UNKNOWN"),
    }
