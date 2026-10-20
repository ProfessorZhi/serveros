#!/usr/bin/env python3
"""QA2 round-2 independent no-write probe for S1 #366 @ 471e300.

Reuses the author's REAL_TEMP harness (real daemon AF_UNIX/`SO_PEERCRED`,
real `AgentControlStore`, real `ServerOSLeaseAuthority`/`OwnerVerifiedLease`,
temp WorkEngine `ServerOSStore`) but adds the QA2 controls the PR's five-case
probe does not cover: revoked policy, expired/future policy, expired/revoked
grant, no resolver and the owner-live commit-seam path. Owner/context/grant
facts are synthetic fixtures; one uid. No production daemon, provider,
credential, network, OAuth or OS grant.

Every case must write NO lease. A lease write or a `verified:true` positive is
a product failure; a reason-code difference is reported as an observation.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

SNAPSHOT = Path(os.environ.get(
    "S1_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-20/checkouts/s1-366-471e300")).resolve()
PROBE = SNAPSHOT / "tests" / "probes" / "s1_owner_atomic_commit_probe.py"

spec = importlib.util.spec_from_file_location("qa2_s1_366_author_probe", PROBE)
author = importlib.util.module_from_spec(spec)
sys.modules["qa2_s1_366_author_probe"] = author
spec.loader.exec_module(author)

opf = author.opf
from workengine.authority import AuthorityError, OwnerVerifiedLease  # noqa: E402
from workengine.serveros_authority import (  # noqa: E402
    ServerOSLeaseAuthority,
    UnixSocketVerifyClient,
)

RESULTS = []


def record(name, expected, actual, *, ok, note="", severity=None):
    RESULTS.append({"name": name, "expected": expected, "actual": actual,
                    "ok": bool(ok), "note": note, "severity": severity})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


def run_case(name, policy, *, resolver=True, mutate=None, accepted):
    with tempfile.TemporaryDirectory(prefix="qa2-s1-366-") as td:
        tmp = Path(td)
        resolver_obj = (opf.StaticOwnerPolicyResolver({}, default=policy)
                        if resolver else None)
        daemon = author.a_daemon(tmp, resolver_obj)
        context = author.a_context(daemon)
        grant = author.a_bound_grant(daemon, context, policy)
        if mutate is not None:
            mutate(daemon, grant)
            with daemon.control_store()._mutation():
                daemon.control_store()._data["grants"][grant["grant_ref"]] = grant
                daemon.control_store()._flush()
        answer = author.verify_socket(
            daemon, author.params(context, grant))
        store = author.a_work_store(tmp)
        error = None
        with author.serve_one(daemon):
            authority = ServerOSLeaseAuthority(
                UnixSocketVerifyClient(daemon.socket_path),
                grant_ref=grant["grant_ref"])
            try:
                OwnerVerifiedLease(store, authority).acquire(
                    context_ref=context["context_ref"],
                    project=author.PROJECT, item_ref=author.ITEM,
                    ttl_s=30.0, now=time.time())
            except AuthorityError as exc:
                error = exc
            except Exception as exc:  # noqa: BLE001
                error = exc
        lease = store.active_lease(author.RESOURCE, now=time.time())
        reason = getattr(error, "reason_code", None)
        answer_reason = answer.get("reason")
        ok = (answer.get("verified") is False and lease is None
              and error is not None and reason in accepted)
        record(name, {"verified": False, "lease": None,
                      "reason_in": sorted(accepted)},
               {"verified": answer.get("verified"),
                "answer_reason": answer_reason,
                "lease_reason": reason,
                "lease_written": lease is not None},
               ok=ok)


def main() -> int:
    live = opf.a_policy(os_principal=f"uid:{os.getuid()}",
                        actor_ref="human:A")
    run_case("revoked-policy", opf.a_policy(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A",
        revoked=True), accepted={"POLICY_DENIED"})
    run_case("expired-policy", opf.a_policy(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A",
        expires_at="2000-01-01T00:00:00Z"), accepted={"POLICY_DENIED"})
    run_case("future-policy", opf.a_policy(
        os_principal=f"uid:{os.getuid()}", actor_ref="human:A",
        valid_from="2999-01-01T00:00:00Z"), accepted={"POLICY_DENIED"})

    def expire_grant(daemon, grant):
        grant["expires_at"] = author.iso(daemon, -10)

    run_case("expired-grant", live, mutate=expire_grant,
             accepted={"POLICY_DENIED"})

    def revoke_grant(daemon, grant):
        grant["revoked"] = True

    run_case("revoked-grant", live, mutate=revoke_grant,
             accepted={"POLICY_DENIED"})
    run_case("no-resolver", live, resolver=False,
             accepted={"BLOCKED_OWNER_POLICY", "BLOCKED_TRUST_SOURCE"})
    run_case("owner-live-no-commit-fence", live,
             accepted={"BLOCKED_OWNER_COMMIT_SEAM"})

    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"total": len(RESULTS), "failed": len(failed),
                      "failed_names": [r["name"] for r in failed]},
                     ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
