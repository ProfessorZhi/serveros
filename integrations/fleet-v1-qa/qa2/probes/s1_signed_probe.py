#!/usr/bin/env python3
"""QA2 S1 signed-evidence probe.

Runs the REAL `context/actor.py#verify_delegation` against a locally generated
Ed25519 key in a temporary HOME. This is not a provider and not a production
trust root; it is a LOCAL_SYNTHETIC_TRUST fixture that exercises the real
signature path and the real grant-field allowlist.

It answers two questions:
  1. Can a signed payload smuggle `owner_policy_ref` / `actor_ref` / `epoch`
     into `approved`? The real verifier's allowlist should refuse before
     `issue_grant` ever sees it.
  2. With a legitimate signed payload, does `issue_grant` write the owner
     binding from the resolver (not from the payload)?

No network, provider, credential, transcript, token or production service.
Set HOME to a temp directory before launching; the script writes only there.
"""
from __future__ import annotations

import base64
import datetime
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SNAPSHOT = Path(os.environ.get(
    "S1_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-20/checkouts/s1-358-b183aab")).resolve()


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


actor = _load("_serveros_context_actor", SNAPSHOT / "context/actor.py")
owner_mod = _load("serveros_runtime_owner_policy",
                  SNAPSHOT / "runtime/owner_policy.py")
store_mod = _load("serveros_runtime_agent_control_store",
                  SNAPSHOT / "runtime/agent_control_store.py")

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

PROVIDER = "provider:qa2-local"
KEY_ID = "key:qa2-local"
AUDIENCE = "qa2-local-audience"
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
ISO = NOW.isoformat()


def iso(offset_s: float = 0.0) -> str:
    return (NOW + datetime.timedelta(seconds=offset_s)).isoformat()


def write_trust(home: Path, public_b64: str) -> None:
    target = home / ".config" / "serveros" / "human-control-trust.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "schema": "serveros/human-control-trust.v1",
        "audience": AUDIENCE,
        "providers": {
            PROVIDER: {
                "keys": {
                    KEY_ID: {"public_key": public_b64, "enabled": True},
                },
            },
        },
    }, sort_keys=True), encoding="utf-8")


def a_report() -> dict:
    return {
        "human_control_provider": PROVIDER,
        "transport": "feishu",
        "transport_identity": "qa2-local-identity",
        "conversation_ref": "oc_qa2_local",
        "message_ref": "om_qa2_local",
        "observed_at": ISO,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": "human:A",
    }


def a_grant(**over) -> dict:
    grant = {
        "grant_ref": "grant:qa2-signed",
        "valid_from": iso(-60),
        "expires_at": iso(600),
        "projects": ["serveros"],
        "items": ["SRV-0001"],
        "actions": ["lease.acquire"],
        "scope_digests": {"SRV-0001": "sha256:qa2-fixture"},
        "budget": {"wall_seconds": 60, "max_attempts": 1,
                   "max_output_bytes": 1000, "exhausted": False},
    }
    grant.update(over)
    return grant


def a_payload(**over) -> dict:
    payload = {
        "schema": actor.DELEGATION_EVIDENCE_SCHEMA,
        "provider_id": PROVIDER,
        "key_id": KEY_ID,
        "audience": AUDIENCE,
        "issued_at": ISO,
        "actor_report": a_report(),
        "grant": a_grant(),
    }
    payload.update(over)
    return payload


def sign(payload: dict, private) -> dict:
    signature = private.sign(actor.signed_bytes(payload))
    return {"payload": payload,
            "signature": base64.b64encode(signature).decode("ascii")}


class StaticResolver:
    def __init__(self, policy):
        self.policy = policy

    def resolve(self, *, policy_ref=None, grant=None, context=None, peer=None,
                project=None, item_ref=None, action=None, now=None):
        if policy_ref is not None and policy_ref != self.policy.policy_ref:
            return None
        return self.policy


RESULTS = []


def record(name, expected, actual, ok):
    RESULTS.append({"name": name, "expected": expected, "actual": actual,
                    "ok": bool(ok)})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="qa2-home-"))
    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    write_trust(home, public_b64)
    # Path.home() was evaluated at import time; set it explicitly for this run.
    actor.DELEGATION_TRUST = home / ".config" / "serveros" / "human-control-trust.json"

    store_path = home / "store.json"
    store = store_mod.AgentControlStore(store_path)
    policy = owner_mod.OwnerPolicy(
        policy_ref="policy:qa2-local",
        os_principal=f"uid:{os.getuid()}",
        actor_ref="human:A",
        projects=("serveros",),
        items=("SRV-0001",),
        actions=("lease.acquire",),
        valid_from="2000-01-01T00:00:00Z",
        expires_at="2999-01-01T00:00:00Z",
        revoked=False,
        epoch=1,
        issued_by="owner:qa2-local",
        issued_at=ISO,
    )

    # 1. Real signature verifies; owner binding comes from the resolver.
    evidence = sign(a_payload(), private)
    approved, context = actor.verify_delegation(evidence, now=ISO)
    record("real-signature-verifies", {"approved_grant_ref": "grant:qa2-signed"},
           {"approved_grant_ref": approved.get("grant_ref")},
           approved.get("grant_ref") == "grant:qa2-signed")
    try:
        stored = store.issue_grant(
            evidence, now=ISO, os_principal=f"uid:{os.getuid()}",
            owner_policy_resolver=StaticResolver(policy))
        record("resolver-writes-owner-binding",
               {"owner_policy_ref": policy.policy_ref,
                "owner_policy_epoch": 1,
                "actor_ref": "human:A"},
               {"owner_policy_ref": stored.get("owner_policy_ref"),
                "owner_policy_epoch": stored.get("owner_policy_epoch"),
                "actor_ref": stored.get("actor_ref")},
               stored.get("owner_policy_ref") == policy.policy_ref
               and stored.get("owner_policy_epoch") == 1
               and stored.get("actor_ref") == "human:A")
    except store_mod.StoreError as exc:
        record("resolver-writes-owner-binding", {"stored": True},
               {"store_error": str(exc)}, False)

    # 2. The real allowlist refuses each owner-written field, even when the
    #    payload is validly signed with the local test key.
    for field, value in (("owner_policy_ref", "policy:forged"),
                         ("owner_policy_epoch", 1),
                         ("owner_policy_issued_by", "owner:forged"),
                         ("owner_policy_issued_at", ISO),
                         ("actor_ref", "human:B"),
                         ("os_principal", f"uid:{os.getuid()}"),
                         ("epoch", 9),
                         ("revoked", False)):
        payload = a_payload(grant=a_grant(**{field: value}))
        evidence = sign(payload, private)
        try:
            actor.verify_delegation(evidence, now=ISO)
            actual = "ACCEPTED"
        except actor.Rejected as exc:
            actual = str(exc).split(":", 1)[0]
        record(f"allowlist-refuses-{field}",
               {"rejected": "DELEGATION_SCOPE_INVALID"},
               {"actual": actual},
               actual == "DELEGATION_SCOPE_INVALID")

    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"total": len(RESULTS), "failed": len(failed),
                      "failed_names": [r["name"] for r in failed]},
                     ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
