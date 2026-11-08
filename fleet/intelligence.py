"""What the fleet knows about its devices, and how it knows it.

`fleet/intelligence-contract.yaml` is the model; this module is its reader.
Everything is pure: facts in, verdicts out. Collecting facts (Core-Lite, the
node connector, readback) and serving them (Fleet MCP) are other layers.

Three questions this answers and nothing else:

* is this fact well formed, and is its status one its source may claim?
  (`validate_fact`) — a declaration cannot say OBSERVED, VERIFIED needs
  evidence, and no secret-shaped key may sit inside a value;
* what is true about this subject now? (`resolve`) — the strongest fresh fact
  wins, an expired one reads STALE, a declaration that disagrees with an
  observation is a finding, and nothing known resolves to UNKNOWN, never to
  "absent";
* which (device, principal, runtime, profile) can take this task?
  (`candidates`) — hard requirements only, failing closed on UNKNOWN. Scoring
  among the eligible is the commander's business.

The registry is untouched: it stays authored identity. Work assignment is not
a fact here (`not_facts` in the contract).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

HERE = Path(__file__).resolve().parent


class IntelligenceError(ValueError):
    """A fact or requirement this model refuses, with the reason."""


@lru_cache(maxsize=1)
def contract() -> dict:
    return yaml.safe_load((HERE / "intelligence-contract.yaml").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _forbidden_keys() -> frozenset[str]:
    source = contract()["envelope"]["forbidden_value_keys_source"]
    filename, _, pointer = source.partition("#")
    node = yaml.safe_load((HERE / filename).read_text(encoding="utf-8"))
    for part in pointer.split("."):
        node = node[part]
    return frozenset(str(k).lower() for k in node)


def _known() -> list[str]:
    return contract()["statuses"]["known"]


def _rank(status: str) -> int:
    known = _known()
    return known.index(status) if status in known else -1


def subject_kind(ref: str) -> str:
    for kind, rule in contract()["subjects"].items():
        if isinstance(ref, str) and re.match(rule["pattern"], ref):
            return kind
    raise IntelligenceError(f"subject_ref {ref!r} is not a device, principal, runtime, "
                            f"profile, path or target ref")


def device_of(ref: str) -> str:
    """`device:<id>` for any subject on a device."""
    kind = subject_kind(ref)
    if kind == "target":
        raise IntelligenceError(f"{ref!r} is not on a device")
    rest = ref.split(":", 1)[1]
    return "device:" + rest.split("/", 1)[0]


def _parse_time(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise IntelligenceError(f"{field} {value!r} is not an ISO time") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _secret_paths(node: Any, trail: str = "value") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, child in node.items():
            here = f"{trail}.{key}"
            lowered = str(key).lower()
            if any(bad in lowered for bad in _forbidden_keys()):
                found.append(here)
            found += _secret_paths(child, here)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found += _secret_paths(child, f"{trail}[{index}]")
    return found


def validate_fact(fact: dict) -> dict:
    """The fact, unchanged, or `IntelligenceError` naming what is wrong."""
    if not isinstance(fact, dict):
        raise IntelligenceError("a fact is a mapping")
    envelope = contract()["envelope"]
    missing = [k for k in envelope["required"] if k not in fact]
    if missing:
        raise IntelligenceError(f"fact lacks {missing}")
    unknown = sorted(set(fact) - set(envelope["required"]) - set(envelope["optional"]))
    if unknown:
        raise IntelligenceError(f"fact carries unknown field(s) {unknown}")
    kind = subject_kind(fact["subject_ref"])
    spec = contract()["fact_types"].get(fact["fact_type"])
    if spec is None:
        raise IntelligenceError(f"fact_type {fact['fact_type']!r} is not declared")
    if spec["scope"] != kind:
        raise IntelligenceError(f"{fact['fact_type']} is about a {spec['scope']}, "
                                f"not a {kind} ({fact['subject_ref']})")
    allowed = contract()["sources"].get(fact["source"])
    if allowed is None:
        raise IntelligenceError(f"source {fact['source']!r} is not declared")
    if fact["status"] not in allowed:
        raise IntelligenceError(f"a {fact['source']} cannot claim {fact['status']}; "
                                f"it may say {allowed}")
    if fact["status"] == "VERIFIED" and not fact.get("evidence_ref"):
        raise IntelligenceError("VERIFIED needs an evidence_ref")
    _parse_time(fact["observed_at"], "observed_at")
    secrets = _secret_paths(fact["value"])
    if secrets:
        # Named by position only: quoting the value would repeat the leak.
        raise IntelligenceError(f"value carries credential-named key(s) at {secrets}")
    if fact["fact_type"] == "profile.models":
        sources = contract()["model_sources"]
        for entry in fact["value"] or []:
            if not isinstance(entry, dict) or entry.get("source") not in sources:
                raise IntelligenceError(f"profile.models entries are {{model, source}} with "
                                        f"source one of {sorted(sources)}")
    return fact


def current_status(fact: dict, now: datetime) -> str:
    """The fact's status as of `now`: past its freshness an observation is STALE."""
    status = fact["status"]
    if status in ("DECLARED", "SOURCE_CODE", "DEPLOYED") or status not in _known():
        return status
    if fact.get("expires_at"):
        expires = _parse_time(fact["expires_at"], "expires_at")
    else:
        ttl = contract()["fact_types"][fact["fact_type"]]["ttl_seconds"]
        expires = _parse_time(fact["observed_at"], "observed_at").timestamp() + ttl
        expires = datetime.fromtimestamp(expires, tz=timezone.utc)
    return "STALE" if now > expires else status


def resolve(facts: Iterable[dict], subject_ref: str, fact_type: str,
            now: datetime) -> dict[str, Any]:
    """What is known about one subject's one fact type now.

    The strongest fresh fact wins; ties go to the latest observation. A
    declaration that disagrees with the winning observation is reported as
    `DECLARED_OBSERVED_MISMATCH` — the observation wins, the declaration is a
    finding. With no fresh known fact the answer is UNKNOWN (or STALE when
    something expired), never an invented value.
    """
    mine = [f for f in facts if f["subject_ref"] == subject_ref and f["fact_type"] == fact_type]
    scored = [(current_status(f, now), f) for f in mine]
    live = [(s, f) for s, f in scored if s in _known()]
    findings: list[str] = []
    if not live:
        status = "STALE" if any(s == "STALE" for s, _ in scored) else "UNKNOWN"
        return {"status": status, "value": None, "fact": None, "findings": findings}
    live.sort(key=lambda pair: (_rank(pair[0]), _parse_time(pair[1]["observed_at"], "observed_at")))
    status, best = live[-1]
    declared = [f for s, f in live if s == "DECLARED"]
    if _rank(status) > _rank("DECLARED") and any(d["value"] != best["value"] for d in declared):
        findings.append("DECLARED_OBSERVED_MISMATCH")
    return {"status": status, "value": best["value"], "fact": best, "findings": findings}


def path_binding_state(binding: dict) -> str:
    """One principal's path role, judged by the contract's path_binding rules.

    `binding` holds what is declared and what was observed:
    {declared: bool, canonical_count: int, exists: bool|None,
     owner_matches: bool|None, shared: bool, in_use: bool}
    """
    if not binding.get("declared"):
        return "UNDECLARED_IN_USE" if binding.get("in_use") else "UNBOUND"
    if binding.get("canonical_count", 1) > 1:
        return "CONFLICT"
    if binding.get("exists") is None:
        return "UNKNOWN"
    if binding["exists"] is False:
        return "STALE_BINDING"
    if binding.get("owner_matches") is False and not binding.get("shared"):
        return "BLOCKED_IDENTITY"
    if binding.get("owner_matches") is None:
        return "UNKNOWN"
    return "BOUND"


def candidates(requirements: list[dict], facts: list[dict], subjects: list[str],
               now: datetime) -> list[dict[str, Any]]:
    """Which subjects meet every hard requirement now.

    A requirement is {fact_type, min_status, and one of: equals / contains /
    model_source}. `contains` checks a list value; `model_source` checks a
    profile.models value for a model from that source. A subject whose fact
    resolves below `min_status` (UNKNOWN and STALE included) is NOT eligible —
    unknown fails closed — and the reason says which requirement and why.
    """
    results = []
    for subject in subjects:
        reasons = []
        for req in requirements:
            if req.get("min_status") not in _known():
                raise IntelligenceError(f"min_status must be one of {_known()}")
            answer = resolve(facts, subject, req["fact_type"], now)
            if _rank(answer["status"]) < _rank(req["min_status"]):
                reasons.append(f"{req['fact_type']}: {answer['status']} < {req['min_status']}")
                continue
            value = answer["value"]
            if "equals" in req and value != req["equals"]:
                reasons.append(f"{req['fact_type']}: value does not match")
            elif "contains" in req and req["contains"] not in (value or []):
                reasons.append(f"{req['fact_type']}: {req['contains']!r} not present")
            elif "model_source" in req:
                offered = [m for m in value or [] if isinstance(m, dict)
                           and m.get("source") == req["model_source"]
                           and m.get("model") == req.get("model", m.get("model"))]
                if not offered:
                    reasons.append(f"{req['fact_type']}: no model from {req['model_source']}")
                elif subject_kind(subject) == "profile":
                    # The owner's rules are applied here, to every profile
                    # candidate: a model the profile offers but policy forbids
                    # is not offered at all.
                    gateway = resolve(facts, subject, "profile.gateway", now)["value"] or {}
                    verdicts = [policy_allows(subject, model=m["model"], source=m["source"],
                                              gateway=gateway.get("target")) for m in offered]
                    if not any(ok for ok, _ in verdicts):
                        reasons.append("policy: " + verdicts[0][1])
        results.append({"subject_ref": subject, "eligible": not reasons, "reasons": reasons})
    return results


# -- the owner's runtime policy ----------------------------------------------

#: Where the live rules live: the hub's own node root, never this repository.
#: `SERVEROS_RUNTIME_POLICY` names another file (tests, a second hub).
LOCAL_POLICY = Path("~/.serveros/runtime-policy.yaml")


def _policy_path() -> Path:
    import os
    named = os.environ.get("SERVEROS_RUNTIME_POLICY")
    return Path(named).expanduser() if named else LOCAL_POLICY.expanduser()


@lru_cache(maxsize=8)
def _read_policy(path: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def runtime_policy() -> dict:
    """The rules in force: the repository's defaults, overlaid by the hub's file.

    The repository file is a starting point, not the rules. The owner changes
    rules by editing the local file — no code change, no pull request, read
    again on its next modification. The overlay is per key: a local `default`
    replaces the repository's `default`; a local device entry replaces that
    device's entry and leaves the others; `devices: {device:x: null}` removes one.
    """
    base = _read_policy(str(HERE / "runtime-policy.yaml"),
                        (HERE / "runtime-policy.yaml").stat().st_mtime)
    local_path = _policy_path()
    if not local_path.is_file():
        return {**base, "source": "repository-default"}
    local = _read_policy(str(local_path), local_path.stat().st_mtime)
    if local.get("schema") not in (None, base.get("schema")):
        raise IntelligenceError(f"{local_path}: schema {local.get('schema')!r} is not "
                                f"{base.get('schema')!r}")
    devices = {**(base.get("devices") or {}), **(local.get("devices") or {})}
    return {**base, "default": local.get("default", base["default"]),
            "tunable": {**(base.get("tunable") or {}), **(local.get("tunable") or {})},
            "gateway_hosts": {**(base.get("gateway_hosts") or {}), **(local.get("gateway_hosts") or {})},
            "devices": {k: v for k, v in devices.items() if v is not None},
            "source": str(local_path)}


def gateway_for_url(url: Any) -> str | None:
    """The gateway target a base URL belongs to, from runtime-policy `gateway_hosts`.

    Matched on host:port, so `http://127.0.0.1:26601/v1` and the bare
    `127.0.0.1:26601` agree. Unknown URLs are None — never guessed.
    """
    import urllib.parse

    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urllib.parse.urlsplit(url if "://" in url else f"http://{url}")
    hostport = parsed.netloc.lower()
    for target, hosts in (runtime_policy().get("gateway_hosts") or {}).items():
        for host in hosts or []:
            if str(host).lower().split("://")[-1].rstrip("/") == hostport:
                return target
    return None


def policy_rule(device_ref: str, runtime_kind: str) -> dict | None:
    """The rule in force for one runtime kind on one device, or None (refused)."""
    policy = runtime_policy()
    rules = (policy.get("devices", {}).get(device_ref) or policy["default"])["runtimes"]
    return rules.get(runtime_kind)


def default_choice(device_ref: str, runtime_kind: str) -> dict:
    """What a session on this device runs when the caller names no model.

    `default_model` in the rule is the owner's choice for "nothing was asked
    for". Without one the device's own default applies, which is only known to
    be allowed when the rule does not constrain the model at all.
    """
    rule = policy_rule(device_ref, runtime_kind)
    if rule is None:
        return {"allowed": False, "model": None,
                "reason": f"{runtime_kind} is not allowed on {device_ref}"}
    if rule.get("default_model"):
        return {"allowed": True, "model": rule["default_model"],
                "reason": "the runtime policy's default_model for this device"}
    constrained = bool(rule.get("model_patterns") or rule.get("gateways")
                       or "official_subscription" not in rule.get("model_sources", []))
    return {"allowed": True, "model": None,
            "reason": ("the device's own default; the policy constrains models here "
                       "but names no default_model, so what runs is unverified")
                      if constrained else "the device's own default"}


# -- self-tuning: what an agent may change inside the owner's bounds --------

#: Knobs an agent can be allowed to turn. The owner opts each one in under
#: `tunable:` in the policy (repository or local file); none is on otherwise.
TUNABLE_KNOBS = ("default_model",)


def tunable(knob: str) -> bool:
    return knob in TUNABLE_KNOBS and bool((runtime_policy().get("tunable") or {}).get(knob))


def _journal_path() -> Path:
    return _policy_path().with_name("runtime-policy.journal.jsonl")


def set_default_model(device_ref: str, runtime_kind: str, model: str | None, *,
                      by: str, reason: str) -> dict:
    """Write one rule's `default_model` into the hub's LOCAL policy file.

    The local file overlays the repository's per device, so the device's whole
    rule as now in force is copied there first and only `default_model`
    changes: no other bound moves. `model=None` removes the default. Every
    change is journalled beside the file with its before and after.
    """
    import copy
    import json
    import os
    import tempfile

    if subject_kind(device_ref) != "device":
        raise IntelligenceError(f"not a device ref: {device_ref!r}")
    policy = runtime_policy()
    current = policy.get("devices", {}).get(device_ref) or policy["default"]
    rule = (current.get("runtimes") or {}).get(runtime_kind)
    if rule is None:
        raise IntelligenceError(f"{runtime_kind} is not allowed on {device_ref}")
    before = rule.get("default_model")
    path = _policy_path()
    local = {}
    if path.is_file():
        local = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    devices = local.setdefault("devices", {}) or {}
    local["devices"] = devices
    entry = copy.deepcopy(devices.get(device_ref) or current)
    target = entry.setdefault("runtimes", {}).setdefault(runtime_kind, copy.deepcopy(rule))
    if model is None:
        target.pop("default_model", None)
    else:
        target["default_model"] = model
    devices[device_ref] = entry
    local.setdefault("schema", policy.get("schema"))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".runtime-policy-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# The hub's live overlay of fleet/runtime-policy.yaml. Edited by the owner,\n"
                 "# and by fleet_tune for knobs the owner made tunable (journal beside it).\n")
        yaml.safe_dump(local, fh, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    record = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "knob": "default_model", "device_ref": device_ref, "runtime": runtime_kind,
              "before": before, "after": model, "by": by, "reason": reason}
    with _journal_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def tuning_journal(limit: int = 20) -> list[dict]:
    import json

    path = _journal_path()
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:]


def model_name_allowed(device_ref: str, runtime_kind: str, model: str) -> tuple[bool, str]:
    """May a session on this device be created on `model`, by name alone?

    The source and gateway of a model entry are judged when the entry is
    written (fleet_model_upsert); here only the name is known.
    """
    rule = policy_rule(device_ref, runtime_kind)
    if rule is None:
        return False, f"{runtime_kind} is not allowed on {device_ref}"
    patterns = rule.get("model_patterns")
    if patterns and not any(re.search(p, model) for p in patterns):
        return False, f"model {model!r} is not allowed for {runtime_kind} on {device_ref}"
    return True, "allowed"


def policy_allows(profile_ref: str, *, model: str, source: str,
                  gateway: str | None = None) -> tuple[bool, str]:
    """May this profile run `model` from `source` (through `gateway`)?

    Rules come from `runtime-policy.yaml`: the device's own entry, else
    `default`. A runtime kind the rule does not name is refused; a rule that
    names gateways refuses an unknown gateway (fail closed).
    """
    if subject_kind(profile_ref) != "profile":
        raise IntelligenceError(f"policy is judged per profile, not {profile_ref!r}")
    device_id, _principal, runtime, _name = profile_ref.split(":", 1)[1].split("/")
    device = f"device:{device_id}"
    policy = runtime_policy()
    rules = (policy.get("devices", {}).get(device) or policy["default"])["runtimes"]
    rule = rules.get(runtime)
    if rule is None:
        return False, f"{runtime} is not allowed on {device}"
    if source not in rule.get("model_sources", []):
        return False, f"{source} is not an allowed model source for {runtime} on {device}"
    if rule.get("gateways") and gateway not in rule["gateways"]:
        return False, f"gateway {gateway or 'UNKNOWN'} is not allowed for {runtime} on {device}"
    patterns = rule.get("model_patterns")
    if patterns and not any(re.search(p, model) for p in patterns):
        return False, f"model {model!r} is not allowed for {runtime} on {device}"
    return True, "allowed"
