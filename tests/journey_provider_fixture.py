#!/usr/bin/env python3
"""A deterministic world in which the SIGNED delegation path can actually RUN.

WHY THIS FILE EXISTS
--------------------
`runtime/journey.py#signed_delegation_provider` delegates every trust decision to
`context/actor.py#verify_delegation`. On this host that function has never executed:
the project venv has no `cryptography`, and the operator's trust binding
(`~/.config/serveros/human-control-trust.json`) does not exist. A security path that has
never run is not "verified" — it is untested code that will make its first appearance in
production, on the day it matters.

Two honest ways to fix that; only one is available this round.

  * Install `cryptography` and an operator trust root — **forbidden here**: that is
    installing a production trust anchor, and the trust root is deliberately not
    something an admission path may choose (`context/actor.py#delegation_trust`).
  * Run the **unmodified** `verify_delegation` against a world that is faked instead.
    That is this file.

WHAT IS FAKED, AND WHAT IS NOT
------------------------------
Faked — exactly two things, and both are restored on exit:

  1. **The `cryptography` PACKAGE.** `verify_delegation` imports `Ed25519PublicKey` and
     `InvalidSignature` *inside the function*, so a double can be placed in `sys.modules`
     for the duration of a test and removed afterwards. The double implements one
     deterministic relation — `verify(sig, msg)` passes iff
     `sig == sha256(public_key_bytes + msg)`. That is NOT Ed25519 and does not claim to
     be; it exists so the *protocol* can run, not so a signature can be believed.
  2. **The operator trust BINDING.** A fixture file is written under the test's own
     `tmp_path`, and `DELEGATION_TRUST` is pointed at it for the duration. The
     production location is never created.

NOT faked: `verify_delegation` itself, and every check inside it — envelope shape,
issuer lookup, key enabled, audience, the Ed25519 verification CALL, actor-report ↔
provider match, issuance-clock ordering, freshness, the grant's allowed-field set,
actions, scope digests, budget, and the validity window. That is the code under test.

WHY IT LIVES IN `tests/`
------------------------
`tests/` is a member of no composition (`plugin/profiles/*.yaml`), so nothing here can
reach a plugin payload or a node runtime. If a future edit ever gives
`runtime/journey.py` an import of this module, the signature double becomes a production
verifier — which is the single failure this file's placement and name exist to prevent.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import sys
import types
from pathlib import Path

TRUST_SCHEMA = "serveros/human-control-trust.v1"
EVIDENCE_SCHEMA = "serveros/human-delegation.v1"

PROVIDER_ID = "botmux-human-control"
KEY_ID = "fixture-key-1"
AUDIENCE = "serveros-fixture"

#: The fixture's "public key". A fixed, obviously-not-secret byte string; the double
#: derives signatures from it deterministically. It is not, and must not be read as,
#: key material.
PUBLIC_KEY = b"fixture-public-key-not-a-real-ed25519-key"


class InvalidSignature(Exception):
    """Raised where `cryptography.exceptions.InvalidSignature` would be."""


def sign(public_key: bytes, message: bytes) -> bytes:
    """The double's only relation. Deterministic, and not a signature scheme."""
    return hashlib.sha256(bytes(public_key) + bytes(message)).digest()


class Ed25519PublicKey:
    """Shaped like the class `verify_delegation` imports; deliberately not it."""

    def __init__(self, raw: bytes):
        self._raw = bytes(raw)

    @classmethod
    def from_public_bytes(cls, raw: bytes) -> "Ed25519PublicKey":
        return cls(raw)

    def verify(self, signature: bytes, message: bytes) -> None:
        if not hmac.compare_digest(bytes(signature), sign(self._raw, bytes(message))):
            raise InvalidSignature("fixture signature did not verify")


#: Stamped on every module this file fabricates, so a test can ask "is the DOUBLE
#: still installed" without depending on whether the host happens to ship the real
#: package. Asserting `"cryptography" not in sys.modules` would be a claim about the
#: host; this is a claim about this file.
FIXTURE_MARK = "serveros_fixture_double"


def _package(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = []          # so the import system treats it as a package
    setattr(module, FIXTURE_MARK, True)
    return module


@contextlib.contextmanager
def fake_cryptography():
    """Put the double into `sys.modules`, then take it back out.

    Scoped rather than process-wide for the reason `tests/conftest.py` gives about the
    ingress record: a global mutation that outlives the test changes what the NEXT test
    is measuring, and nothing about the resulting failure says so.
    """
    names = ["cryptography", "cryptography.hazmat", "cryptography.hazmat.primitives",
             "cryptography.hazmat.primitives.asymmetric",
             "cryptography.hazmat.primitives.asymmetric.ed25519",
             "cryptography.exceptions"]
    saved = {name: sys.modules.get(name) for name in names}

    top, hazmat = _package("cryptography"), _package("cryptography.hazmat")
    primitives = _package("cryptography.hazmat.primitives")
    asymmetric = _package("cryptography.hazmat.primitives.asymmetric")
    ed25519 = _package("cryptography.hazmat.primitives.asymmetric.ed25519")
    exceptions = _package("cryptography.exceptions")
    ed25519.Ed25519PublicKey = Ed25519PublicKey
    exceptions.InvalidSignature = InvalidSignature
    top.hazmat, hazmat.primitives = hazmat, primitives
    primitives.asymmetric, asymmetric.ed25519 = asymmetric, ed25519

    for module in (top, hazmat, primitives, asymmetric, ed25519, exceptions):
        sys.modules[module.__name__] = module
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def write_binding(path, *, provider_id=PROVIDER_ID, key_id=KEY_ID, audience=AUDIENCE,
                  enabled=True, public_key=PUBLIC_KEY) -> Path:
    """The operator-owned trust root, as a fixture FILE.

    `delegation_trust` is explicit that an admission path may not choose its trust root,
    so this function is only ever called with a path under a test's `tmp_path`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    binding = {
        "schema": TRUST_SCHEMA,
        "audience": audience,
        "providers": {provider_id: {"keys": {key_id: {
            "enabled": enabled,
            "public_key": base64.b64encode(public_key).decode(),
        }}}},
    }
    path.write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class FixtureIssuer:
    """Mints the evidence a HumanControlProvider would sign, over the real schema.

    Every payload it produces is shaped by the CONTRACT, and its signature bytes come
    from `sign()` above. It cannot mint a payload `verify_delegation` would accept for
    the wrong reason, because the acceptance rules are not in this file.
    """

    def __init__(self, actor_module):
        self.actor = actor_module

    def report(self, **over) -> dict:
        report = {"human_control_provider": PROVIDER_ID, "transport": "feishu",
                  "transport_identity": "verified", "conversation_ref": "oc_fixture",
                  "message_ref": "om_approval_fixture",
                  "observed_at": "2026-10-14T09:59:00+09:00",
                  "provider_status": "VERIFIED", "provenance": "FACT",
                  "actor_ref": "feishu:union:on_fixture"}
        report.update(over)
        return report

    def grant(self, *, covers, scope_digests, **over) -> dict:
        grant = {"grant_ref": "grant:fixture-1",
                 "valid_from": "2026-10-14T09:00:00+09:00",
                 "expires_at": "2026-10-14T11:00:00+09:00",
                 "projects": [], "items": [], "actions": ["work.open"],
                 "covers": list(covers), "scope_digests": dict(scope_digests),
                 "budget": {"wall_seconds": 60, "max_attempts": 1,
                            "max_output_bytes": 4096, "exhausted": False}}
        grant.update(over)
        return grant

    def evidence(self, *, actor_report, grant, issued_at, provider_id=PROVIDER_ID,
                 key_id=KEY_ID, audience=AUDIENCE, signature=None,
                 public_key=PUBLIC_KEY, schema=EVIDENCE_SCHEMA) -> dict:
        payload = {"schema": schema, "provider_id": provider_id, "key_id": key_id,
                   "audience": audience, "issued_at": issued_at,
                   "actor_report": actor_report, "grant": grant}
        if signature is None:
            # Signed over the SAME bytes `verify_delegation` will sign-check, because
            # the encoder is the actor module's own. A second `json.dumps` here is how
            # two sides come to disagree about whitespace and call it a bad key.
            signature = base64.b64encode(
                sign(public_key, self.actor.signed_bytes(payload))).decode()
        return {"payload": payload, "signature": signature}

    def context_ref(self, actor_report, *, issued_at) -> str:
        """The handle a caller would present: derived by the actor module, not typed."""
        return self.actor.build(actor_report, now=issued_at)["context_ref"]


@contextlib.contextmanager
def provider_world(actor_module, tmp_path, *, binding=True, **binding_over):
    """A world where the real `verify_delegation` can run.

    `binding=False` writes no trust root at all — the production condition on this host,
    and the one that must fail closed rather than fall back to anything.
    """
    binding_path = Path(tmp_path) / "human-control-trust.json"
    if binding:
        write_binding(binding_path, **binding_over)
    previous = actor_module.DELEGATION_TRUST
    actor_module.DELEGATION_TRUST = binding_path
    try:
        with fake_cryptography():
            yield FixtureIssuer(actor_module)
    finally:
        actor_module.DELEGATION_TRUST = previous
