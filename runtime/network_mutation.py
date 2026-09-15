#!/usr/bin/env python3
"""The managed path: intent, plan, compliance, executor.

    request  ->  transient plan  ->  compliance preflight  ->  executor

This is the module that makes `network-contract.yaml` more than a checker. A
verdict that nothing consults is a report, and `serverosctl network preflight`
on its own would be a report — accurate, useful, and unable to stop anything.

The plan is TRANSIENT and that is a decision
--------------------------------------------
There is no plan database, no plan lifecycle, no plan state machine and no
plan daemon. A plan here is a dict, built in one function, consumed in the next
call, and gone. `network-contract.yaml` says why: a plan that could be stored
would need a lifetime, and a lifetime would need a sweeper, and the sweeper
would need to decide when an intent had expired — which is a policy nobody has
written and a scheduler this round is not building.

What survives the call is the RECORD of what happened, which is the executor's
return value, and the preflight result inside it. That is enough to answer
"why was this created" and not enough to resume a half-finished operation,
because there are no half-finished operations: the executor is one command.

The executor refuses on its own
-------------------------------
`execute` reads the verdict itself and performs nothing unless it says ALLOW.
It does not take a caller's word for it, and it is not parameterized to skip
the check — a gate the caller may bypass is a convention, and the point of
wiring one in is that the code path does not exist.

Exit codes, and they are not two
--------------------------------
    0   ALLOW — the preflight admitted it. Whether anything RAN is a separate
        question, answered by `execution.performed`; `--execute` is off by
        default.
    10  DENY — a rule refused it, and `preflight.code` names which.
    11  REQUIRE_APPROVAL — nothing refused it and nobody may take it on the
        requester's own authority. Nothing ran. It is not 0 because a caller
        reading `$?` must not proceed past a mutation nobody approved, and it
        is not 10 because it is repaired by a different person: a DENY needs
        the request or the policy changed, and this needs a human.
    2   the question could not be put — a malformed request, an unnamed tier.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_policy = _load("serveros_runtime_network_policy", "network_policy.py")
_evidence = _load("serveros_runtime_network_evidence", "network_evidence.py")
_probes = _load("serveros_runtime_probes", "probes.py")
_ownership = _load("serveros_runtime_ownership_gate", "ownership_gate.py")

SCHEMA = "serveros/network.v1"
ACTION = "docker.network.create"

# From the contract's `actions` block. Named here because `plan()` puts it in
# the record and `tests/test_network_policy.py` asserts the two agree.
RECOVERY = "REVERSIBLE"


class Rejected(ValueError):
    """The request cannot be turned into a plan at all."""


# ---------------------------------------------------------------------------
# The policy input
# ---------------------------------------------------------------------------

def policy_location(explicit: str | None = None) -> str | None:
    """`--policy`, then `$SERVEROS_NETWORK_POLICY`, then nothing.

    Nothing is a real answer and it denies. See the contract's
    `policy.default_location_because` for why no path is built in.
    """
    return explicit or os.environ.get("SERVEROS_NETWORK_POLICY") or None


def load_policy(explicit: str | None = None) -> tuple[dict, str | None]:
    path = policy_location(explicit)
    return _policy.read_policy(path), path


# ---------------------------------------------------------------------------
# The plan. Built, used, discarded.
# ---------------------------------------------------------------------------

def _digest(value) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def plan(request: dict, *, host, policy: dict, policy_ref: str | None,
         now: str) -> dict:
    """Intent as data. No side effect, no storage, no identifier to resume by.

    `subject_ref` is derived from the request rather than minted, so that
    asking twice for the same network produces the same reference and the
    comparison is possible — the same reasoning as `approval.py`'s binding
    being its own ref.
    """
    block = request.get("network")
    if not isinstance(block, dict):
        raise Rejected("the request must carry a `network` mapping")
    subject = _policy.candidate(**block)

    sources = _evidence.sources(host)
    targets = _policy.witness_addresses(subject, {**sources, "policy": policy})
    # A truncated target list is not fatal and is not silent either: the
    # evaluator reports `probe_targets_dropped`, and the addresses that were not
    # asked about are absent from `route_decisions` like any other unprobed
    # address, so they deny on their own.
    sources["route_decisions"] = _evidence.probe(host, targets["targets"])

    return {
        "schema": SCHEMA,
        "transient": True,
        "action": ACTION,
        "subject_ref": f"network:{subject['network_name']}",
        "candidate": subject,
        "policy_context": {
            "host_profile_ref": policy_ref,
            "coverage": _policy.policy_coverage(policy),
        },
        "evidence_refs": [
            {"source": key, "digest": _digest(sources.get(key)),
             "rows": (None if sources.get(key) is None else len(sources[key]))}
            for key in ("docker_subnets", "host_iface_cidrs", "host_routes",
                        "route_decisions")
        ],
        "recovery": RECOVERY,
        "evidence": sources,
        "created_at": now,
    }


def preflight(plan_record: dict, *, policy: dict, ownership: dict | None = None,
              recovery: str | None = None) -> dict:
    """Compose the three inputs. It does not produce the ownership one.

    `ownership=None` is the state C1's contract names — "an absent input is not
    a vote" — and it is kept, because this function is a composition step and
    the decision to require a requester belongs to an entry point rather than
    to a fold. `run()` is that entry point and it always passes a verdict,
    including the NULL one that a missing context earns.
    """
    return _policy.evaluate(
        candidate=plan_record["candidate"],
        evidence=plan_record["evidence"],
        policy=policy,
        ownership=ownership,
        recovery=recovery or plan_record.get("recovery"),
    )


def ownership_verdict(plan_record: dict, *, context: dict | None,
                      registry: dict | None = None) -> dict:
    """C2's verdict on this candidate, from the requester the candidate names.

    This is the producer `network-contract.yaml` declared and did not have: its
    compose block reads `ownership` and says what the value means, and until
    now the only caller that ever filled the parameter in was a test.

    The subject is the REQUESTER and not the network. A network this call is
    about to create has no labels, no cgroup and no unit — every decisive
    ownership source on this node reads something that does not exist yet —
    while `candidate.required` makes `owner_ref` mandatory and defines it as
    "who is asking, resolved from the trusted context; never a name". So the
    owner question is asked of that ref, and a ref that arrived as a bare
    string with no context behind it is refused for exactly that reason.

    Read the two failure modes apart, because they are repaired differently:
    NO_TRUSTED_CONTEXT means nobody was named as asking, and OWNER_UNKNOWN
    means somebody was named and the registry does not know them.
    """
    index = registry if registry is not None else _ownership.build_registry_index()
    candidate = plan_record.get("candidate") or {}
    return _ownership.evaluate(
        subject={"kind": "network",
                 "name": candidate.get("network_name"),
                 "ref": plan_record.get("subject_ref")},
        action={"kind": "additive"},
        context=context,
        registry=index,
        resolved=_ownership.resolve_owner_ref(
            owner_ref=candidate.get("owner_ref"), registry=index, kind="network"),
    )


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------

def argv_for(candidate: dict) -> list[str]:
    """The command, as a list. Built here so a test can read it without running it."""
    argv = ["docker", "network", "create"]
    if candidate.get("driver"):
        argv += ["--driver", candidate["driver"]]
    argv += ["--subnet", candidate["subnet"]]
    if candidate.get("gateway"):
        argv += ["--gateway", candidate["gateway"]]
    argv.append(candidate["network_name"])
    return argv


def subprocess_runner(argv: list[str]) -> dict:
    """The only place this module runs anything. One command, named in argv."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {"returncode": out.returncode,
            "stdout": out.stdout.strip()[:2000],
            "stderr": out.stderr.strip()[:2000]}


def execute(plan_record: dict, verdict: dict, *, runner, enabled: bool = False) -> dict:
    """Perform the mutation, or say exactly why nothing was performed.

    Two refusals happen before the runner is reached, and they are separate
    because they are repaired by different people:

      * the verdict is not ALLOW — the requester's input or the operator's
        policy has to change;
      * `enabled` is False — the default. `serverosd` makes the same choice with
        `--enable-reconcile`, for the same reason: a tool that acts the first
        time somebody runs it is a tool nobody runs with `--help` first.

    `runner` has no default. An executor with a built-in way to reach the world
    is an executor that can be reached by accident, and the one thing this
    module must be able to prove is that on a DENY the actuator is never
    entered.
    """
    if verdict.get("decision") != "ALLOW":
        return {"performed": False, "runner_called": False,
                "decision": verdict.get("decision"),
                "code": verdict.get("code"),
                "reason": f"refused before the executor: {verdict.get('reason')}"}
    if not enabled:
        return {"performed": False, "runner_called": False, "decision": "ALLOW",
                "reason": ("preflight allowed it and nothing was performed: the "
                           "executor is off unless `--execute` is passed"),
                "argv": argv_for(plan_record["candidate"])}

    argv = argv_for(plan_record["candidate"])
    result = runner(argv)
    return {"performed": True, "runner_called": True, "decision": "ALLOW",
            "argv": argv, "result": result,
            "ok": result.get("returncode") == 0}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_request(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml
        return yaml.safe_load(text) or {}


def run(argv: list[str] | None = None, *, host=None, runner=None,
        registry: dict | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("preflight", "create"))
    parser.add_argument("--request", required=True, help="JSON or YAML request file")
    parser.add_argument("--policy", help="host profile holding `network_policy`")
    parser.add_argument("--execute", action="store_true",
                        help="perform the mutation; without it nothing is run")
    parser.add_argument("--level", choices=_policy.LEVELS)
    # Required in effect and not by argparse, which is the point: its absence
    # is a DENY with a code and a repair, not a usage error that teaches the
    # caller to pass any string that parses. `owner_ref` is documented as
    # "resolved from the trusted context; never a name", so a mutation with no
    # context has no requester — and a requester is what an approval belongs
    # to, so without one there is nothing for a person to approve either.
    parser.add_argument(
        "--context",
        help="trusted context document ({context_ref, actor_ref}). Without it "
             "the ownership gate answers NO_TRUSTED_CONTEXT and nothing runs")
    args = parser.parse_args(argv)

    host = host or _probes.LiveHost()
    policy, policy_ref = load_policy(args.policy)
    request = _read_request(args.request)

    import datetime
    try:
        plan_record = plan(request, host=host, policy=policy, policy_ref=policy_ref,
                           now=datetime.datetime.now(datetime.timezone.utc).isoformat(
                               timespec="seconds"))
    except Rejected as exc:
        # A malformed REQUEST is not a refused CANDIDATE. Only the second has a
        # code and a repair, and answering the first with a code would tell the
        # caller their subnet collided with something when in fact nothing was
        # looked at. Exit 2 so a caller can tell it apart from the four below.
        sys.stderr.write(f"REJECTED: {exc}\n")
        return 2
    context = _read_request(args.context) if args.context else None
    ownership = ownership_verdict(plan_record, context=context, registry=registry)
    verdict = preflight(plan_record, policy=policy, ownership=ownership)

    payload: dict = {"plan": {k: v for k, v in plan_record.items() if k != "evidence"},
                     # C2's verdict, at the level the caller asked for. It is
                     # reported even when it changed nothing, so that "ownership
                     # admitted this" and "ownership was never asked" cannot be
                     # read the same way from the output.
                     "ownership": _ownership.disclose(ownership, args.level)
                     if args.level else ownership,
                     "preflight": _policy.disclose(verdict, args.level)}
    if args.action == "create":
        payload["execution"] = execute(
            plan_record, verdict,
            runner=runner or subprocess_runner, enabled=args.execute)

    json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    render = _policy.render(verdict, args.level)
    if verdict["decision"] == "DENY":
        sys.stderr.write(render + "\n")
        return 10
    if verdict["decision"] == "REQUIRE_APPROVAL":
        # The third outcome, and it is not a success. Nothing ran — `execute`
        # refuses anything that is not ALLOW — and a caller reading `$?` has to
        # see that, or a shell doing `serverosctl network create ... && ...`
        # proceeds past a mutation nobody approved. 11 rather than 10 because
        # the two are repaired by different people: a DENY needs the request or
        # the policy changed, and this needs a person.
        sys.stderr.write(render + "\n")
        return 11
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
