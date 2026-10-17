#!/usr/bin/env python3
"""Check one Fleet V1 integration release manifest.

Dependency-free on purpose: this runs on a shared host where installing a
validator is itself a change. It enforces the invariants a release manifest
must never violate, and it refuses the specific over-claims this programme
keeps producing:

  * a SOURCE pin printed as if it were a device or client result;
  * ``COMPLETE_V1`` while a gate is NOT_RUN or its evidence level is lower
    than the gate requires;
  * a source merge performed before the gates that authorize it;
  * an integrator signing its own QA;
  * a credential pasted into a manifest.

Usage:
    python3 check_manifest.py <manifest.json> [--json]
    python3 check_manifest.py --self-test

Exit: 0 = no errors (warnings allowed), 1 = at least one error, 2 = usage.
"""

from __future__ import annotations

import json
import re
import sys

RANK = {
    "NOT_RUN": 0,
    "SOURCE": 1,
    "MOCK": 2,
    "LOCAL": 3,
    "REAL_DEVICE": 4,
    "CLIENT_E2E": 5,
}

SCHEMA = "serveros/fleet-v1-rc-manifest.v1"
STATES = {"RC_CANDIDATE", "COMPLETE_V1", "BLOCKED", "ABORTED"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
ISO_Z = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

SECRET_PATTERNS = [
    ("github-token", re.compile(r"gh[opsu]_[A-Za-z0-9]{20,}")),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}")),
]


class Report:
    def __init__(self) -> None:
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, code: str, where: str, message: str) -> None:
        self.errors.append({"code": code, "where": where, "message": message})

    def warn(self, code: str, where: str, message: str) -> None:
        self.warnings.append({"code": code, "where": where, "message": message})

    @property
    def ok(self) -> bool:
        return not self.errors


def _req(obj, key, where, report: Report):
    if not isinstance(obj, dict) or key not in obj:
        report.error("E100", where, f"missing required key {key!r}")
        return None
    return obj[key]


def check(doc, raw_text: str = "") -> Report:
    r = Report()
    if not isinstance(doc, dict):
        r.error("E001", "$", "manifest must be a JSON object")
        return r

    if doc.get("schema") != SCHEMA:
        r.error("E002", "schema", f"expected {SCHEMA!r}, got {doc.get('schema')!r}")

    mid = doc.get("manifest_id")
    if not isinstance(mid, str) or not re.match(r"^[a-z0-9][a-z0-9._-]{2,63}$", mid or ""):
        r.error("E003", "manifest_id", "must be a lowercase id of 3..64 characters")

    gen = doc.get("generated_at")
    if not isinstance(gen, str) or not ISO_Z.match(gen):
        r.error("E004", "generated_at", "must be an ISO-8601 UTC timestamp ending in Z")

    state = doc.get("state")
    if state not in STATES:
        r.error("E005", "state", f"must be one of {sorted(STATES)}")

    for field in ("campaign_ref", "integration_ref"):
        v = doc.get(field)
        if not isinstance(v, str) or not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+$", v or ""):
            r.error("E006", field, "must look like owner/repo#number")

    # ---- principals -------------------------------------------------------
    integrator = doc.get("integrator") or {}
    qa = doc.get("qa") or {}
    integrator_owner = integrator.get("owner")
    qa_owner = qa.get("owner")
    if not isinstance(integrator_owner, str) or len(integrator_owner) < 3:
        r.error("E010", "integrator.owner", "must name the integrating session")
    if not isinstance(qa_owner, str) or len(qa_owner) < 3:
        r.error("E011", "qa.owner", "must name the QA session")
    if qa.get("independent_of_integrator") and qa_owner == integrator_owner:
        r.error(
            "E012",
            "qa",
            "independent_of_integrator is true but qa.owner equals integrator.owner",
        )
    if state == "COMPLETE_V1" and not qa.get("independent_of_integrator"):
        r.error("E013", "qa", "COMPLETE_V1 requires a QA owner independent of the integrator")
    if state == "COMPLETE_V1" and not qa.get("signature"):
        r.error("E014", "qa.signature", "COMPLETE_V1 requires an independent QA signature")
    qa_status = qa.get("status")
    if qa_status not in {"PENDING", "ACTIVE", "SIGNED"}:
        r.error("E015", "qa.status", f"unknown QA status {qa_status!r}")
    if qa_status == "SIGNED" and not qa.get("signature"):
        r.error("E016", "qa.signature", "status SIGNED requires a signature")
    if qa_status in {"PENDING", "ACTIVE"} and qa.get("signature"):
        r.error("E017", "qa.signature", f"status {qa_status} cannot carry a signature")
    if state == "COMPLETE_V1" and qa_status != "SIGNED":
        r.error("E018", "qa.status", "COMPLETE_V1 requires qa.status == SIGNED")

    # ---- version drift guard ---------------------------------------------
    drift = doc.get("drift_guard")
    if not isinstance(drift, dict):
        r.error("E110", "drift_guard", "a drift guard recording the snapshot time is required")
        drift = {}
    snapshot = drift.get("snapshot_time")
    if not isinstance(snapshot, str) or not ISO_Z.match(snapshot):
        r.error("E111", "drift_guard.snapshot_time", "must be an ISO-8601 UTC timestamp ending in Z")
    if not isinstance(drift.get("policy"), str) or len(drift["policy"]) < 20:
        r.error("E112", "drift_guard.policy", "must state the re-pin policy")
    recheck = drift.get("recheck_before")
    if not isinstance(recheck, list) or not recheck:
        r.error("E113", "drift_guard.recheck_before", "must list at least one recheck point")
    stale_pins: list[str] = []

    # ---- repo pins --------------------------------------------------------
    repos = doc.get("repos")
    if not isinstance(repos, list) or not repos:
        r.error("E020", "repos", "must be a non-empty array")
        repos = []
    seen_repos: set[str] = set()
    for i, repo in enumerate(repos):
        where = f"repos[{i}]"
        if not isinstance(repo, dict):
            r.error("E021", where, "must be an object")
            continue
        name = repo.get("name")
        if not isinstance(name, str) or not name:
            r.error("E022", where, "name is required")
        elif name in seen_repos:
            r.error("E023", where, f"duplicate repo name {name!r}")
        else:
            seen_repos.add(name)
        rev = repo.get("revision")
        if not isinstance(rev, str) or not SHA40.match(rev):
            r.error("E024", f"{where}.revision", "must be a full 40-hex revision")
        if repo.get("reachable") is not True:
            r.warn("W010", f"{where}.reachable", "revision is not recorded as reachable")
        if not isinstance(repo.get("role"), str) or not repo.get("role"):
            r.error("E025", f"{where}.role", "role is required")

    # ---- candidates -------------------------------------------------------
    candidates = doc.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        r.error("E030", "candidates", "must be a non-empty array")
        candidates = []
    seen_pr: set[tuple[str, int]] = set()
    pinned_prs: set[tuple[str, int]] = set()
    for i, cand in enumerate(candidates):
        where = f"candidates[{i}]"
        if not isinstance(cand, dict):
            r.error("E031", where, "must be an object")
            continue
        repo = cand.get("repo")
        pr = cand.get("pr")
        if not isinstance(pr, int) or pr < 1:
            r.error("E032", f"{where}.pr", "must be a positive integer")
        if isinstance(repo, str) and isinstance(pr, int):
            key = (repo, pr)
            if key in seen_pr:
                r.error("E033", where, f"duplicate candidate {repo}#{pr}")
            seen_pr.add(key)
            pinned_prs.add(key)
        head = cand.get("head")
        if not isinstance(head, str) or not SHA40.match(head):
            r.error("E034", f"{where}.head", "must be a full 40-hex head SHA")
        if cand.get("draft") is not True:
            r.error("E035", f"{where}.draft", "every candidate in this manifest is a Draft")
        level = cand.get("evidence_level")
        if level not in RANK or level == "NOT_RUN":
            r.error("E036", f"{where}.evidence_level", f"unknown evidence level {level!r}")
        if cand.get("installed") is True and level == "SOURCE":
            r.error(
                "E037",
                f"{where}.installed",
                "an installed claim cannot rest on SOURCE evidence alone",
            )
        observed = cand.get("observed_at")
        if not isinstance(observed, str) or not ISO_Z.match(observed):
            r.error("E114", f"{where}.observed_at", "must be an ISO-8601 UTC timestamp ending in Z")
        elif isinstance(snapshot, str) and observed != snapshot:
            stale_pins.append(f"{repo}#{pr} observed {observed} != snapshot {snapshot}")
            r.warn(
                "W030",
                f"{where}.observed_at",
                f"pin observed at {observed}, snapshot is {snapshot}; re-pin before use",
            )
        if not isinstance(cand.get("base_ref"), str) or not cand["base_ref"]:
            r.error("E038", f"{where}.base_ref", "base_ref is required")
        if cand.get("stacks_on") is not None:
            base_pr = cand["stacks_on"]
            if not isinstance(base_pr, int):
                r.error("E039", f"{where}.stacks_on", "must be an integer or null")
            elif isinstance(repo, str) and (repo, base_pr) not in pinned_prs:
                r.warn(
                    "W011",
                    f"{where}.stacks_on",
                    f"base PR {repo}#{base_pr} is not itself pinned in this manifest",
                )

    # ---- source merge -----------------------------------------------------
    merge = doc.get("source_merge") or {}
    performed = merge.get("performed")
    if not isinstance(performed, bool):
        r.error("E040", "source_merge.performed", "must be a boolean")
    if not isinstance(merge.get("policy"), str) or len(merge["policy"]) < 10:
        r.error("E041", "source_merge.policy", "must state the merge policy")
    audit = merge.get("conflict_audit")
    if not isinstance(audit, list) or not audit:
        r.error("E042", "source_merge.conflict_audit", "must record at least one audit row")
        audit = []
    for i, row in enumerate(audit):
        where = f"source_merge.conflict_audit[{i}]"
        if not isinstance(row, dict):
            r.error("E043", where, "must be an object")
            continue
        if not isinstance(row.get("shared_own_commit_files"), int) or row["shared_own_commit_files"] < 0:
            r.error("E044", f"{where}.shared_own_commit_files", "must be a non-negative integer")
        if not isinstance(row.get("conclusion"), str) or len(row["conclusion"]) < 3:
            r.error("E045", f"{where}.conclusion", "conclusion is required")

    # ---- gates ------------------------------------------------------------
    gates = doc.get("gates")
    if not isinstance(gates, list) or not gates:
        r.error("E050", "gates", "must be a non-empty array")
        gates = []
    seen_gates: set[str] = set()
    all_gates_pass = bool(gates)
    for i, gate in enumerate(gates):
        where = f"gates[{i}]"
        if not isinstance(gate, dict):
            r.error("E051", where, "must be an object")
            all_gates_pass = False
            continue
        gid = gate.get("id")
        if not isinstance(gid, str) or not gid:
            r.error("E052", f"{where}.id", "id is required")
        elif gid in seen_gates:
            r.error("E053", where, f"duplicate gate id {gid!r}")
        else:
            seen_gates.add(gid)
        required = gate.get("required_level")
        achieved = gate.get("achieved_level")
        verdict = gate.get("verdict")
        if required not in RANK:
            r.error("E054", f"{where}.required_level", f"unknown level {required!r}")
        if achieved not in RANK:
            r.error("E055", f"{where}.achieved_level", f"unknown level {achieved!r}")
        if verdict not in {"PASS", "FAIL", "NOT_RUN", "BLOCKED", "PARTIAL"}:
            r.error("E056", f"{where}.verdict", f"unknown verdict {verdict!r}")
        if verdict != "PASS":
            all_gates_pass = False
        if (
            required in RANK
            and achieved in RANK
            and RANK[achieved] >= RANK[required]
            and verdict != "PASS"
        ):
            r.warn(
                "W020",
                where,
                f"evidence level {achieved} meets {required} but verdict is {verdict}",
            )
        if verdict in {"FAIL", "NOT_RUN", "BLOCKED"} and not gate.get("blocker"):
            r.warn("W021", where, f"verdict {verdict} without a blocker note")
        if not isinstance(gate.get("owner"), str) or len(gate["owner"]) < 3:
            r.error("E057", f"{where}.owner", "owner is required")

    # ---- hosted CI --------------------------------------------------------
    hosted = doc.get("hosted_ci") or {}
    if hosted.get("status") not in {
        "GREEN",
        "FAILED",
        "QUOTA_WAIVED",
        "NOT_CONFIGURED",
        "UNKNOWN",
    }:
        r.error("E060", "hosted_ci.status", "unknown hosted CI status")
    if not isinstance(hosted.get("evidence"), str) or len(hosted["evidence"]) < 3:
        r.error("E061", "hosted_ci.evidence", "must state what was observed")

    # ---- open P0 ----------------------------------------------------------
    open_p0 = doc.get("open_p0")
    if not isinstance(open_p0, list):
        r.error("E070", "open_p0", "must be an array")
        open_p0 = []
    for i, item in enumerate(open_p0):
        where = f"open_p0[{i}]"
        if not isinstance(item, dict):
            r.error("E071", where, "must be an object")
            continue
        for key, minimum in (("id", 2), ("summary", 10), ("owner_action", 10)):
            v = item.get(key)
            if not isinstance(v, str) or len(v) < minimum:
                r.error("E072", f"{where}.{key}", f"must be a string of at least {minimum} chars")

    # ---- rollback / claims ------------------------------------------------
    rollback = doc.get("rollback") or {}
    if not isinstance(rollback.get("design"), str) or len(rollback["design"]) < 10:
        r.error("E080", "rollback.design", "must describe the rollback")
    if not isinstance(rollback.get("verified"), bool):
        r.error("E081", "rollback.verified", "must be a boolean")

    claims = doc.get("claims") or {}
    for key in ("live_device", "client_e2e", "production"):
        if not isinstance(claims.get(key), bool):
            r.error("E082", f"claims.{key}", "must be a boolean")
    if state != "COMPLETE_V1" and any(claims.get(k) is True for k in ("live_device", "client_e2e", "production")):
        r.error(
            "E083",
            "claims",
            f"state {state} cannot carry a live/client/production claim",
        )

    # ---- state invariants -------------------------------------------------
    if state in {"RC_CANDIDATE", "BLOCKED"} and not open_p0:
        r.error(
            "E090",
            "open_p0",
            f"{state} must record why it is not COMPLETE_V1; open_p0 is empty",
        )
    if state == "COMPLETE_V1":
        if not all_gates_pass:
            r.error("E091", "gates", "COMPLETE_V1 requires every gate verdict to be PASS")
        for i, gate in enumerate(gates):
            required = gate.get("required_level")
            achieved = gate.get("achieved_level")
            if required in RANK and achieved in RANK and RANK[achieved] < RANK[required]:
                r.error(
                    "E092",
                    f"gates[{i}]",
                    f"COMPLETE_V1: evidence {achieved} is below required {required}",
                )
        if open_p0:
            r.error("E093", "open_p0", "COMPLETE_V1 cannot carry open P0 items")
        if any(claims.get(k) is not True for k in ("live_device", "client_e2e", "production")):
            r.error("E094", "claims", "COMPLETE_V1 requires live, client and production claims")
    if performed is True and not all_gates_pass:
        r.error(
            "E095",
            "source_merge.performed",
            "a source merge before every gate passes is forbidden by the I1 policy",
        )
    if state == "COMPLETE_V1" and stale_pins:
        r.error(
            "E115",
            "drift_guard",
            "COMPLETE_V1 cannot rest on pins older than the snapshot: " + "; ".join(stale_pins),
        )

    # ---- credential hygiene ----------------------------------------------
    if raw_text:
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(raw_text):
                r.error("E100", "$", f"credential-like material detected ({label})")

    return r


def _valid_fixture() -> dict:
    return {
        "schema": SCHEMA,
        "manifest_id": "fixture-rc-1",
        "generated_at": "2026-10-17T00:00:00Z",
        "campaign_ref": "Owner/repo#1",
        "integration_ref": "Owner/repo#2",
        "state": "RC_CANDIDATE",
        "integrator": {"owner": "integrator-a", "device_ref": "device:x", "authority": "read-only"},
        "qa": {
            "owner": "qa-b",
            "status": "PENDING",
            "independent_of_integrator": True,
            "session_ref": "device:x|claude|sess-b",
            "run_ref": "run-b",
            "signature": None,
        },
        "drift_guard": {
            "snapshot_time": "2026-10-17T00:00:00Z",
            "policy": "re-read every pin from the provider immediately before push and composition",
            "recheck_before": ["push", "composition"],
        },
        "repos": [
            {"name": "r", "repo": "Owner/repo", "revision": "a" * 40, "role": "host", "reachable": True}
        ],
        "candidates": [
            {
                "lane": "S1",
                "repo": "Owner/repo",
                "pr": 1,
                "head": "b" * 40,
                "base_ref": "main",
                "base_is_pr": False,
                "stacks_on": None,
                "draft": True,
                "evidence_level": "SOURCE",
                "installed": False,
                "observed_at": "2026-10-17T00:00:00Z",
            }
        ],
        "source_merge": {
            "performed": False,
            "policy": "no merge in this milestone",
            "conflict_audit": [
                {
                    "left": "a",
                    "right": "b",
                    "shared_own_commit_files": 0,
                    "conclusion": "disjoint",
                }
            ],
        },
        "gates": [
            {
                "id": "G1",
                "required_level": "LOCAL",
                "achieved_level": "NOT_RUN",
                "verdict": "NOT_RUN",
                "owner": "owner-a",
                "blocker": "not run yet",
                "receipt": None,
            }
        ],
        "hosted_ci": {"status": "QUOTA_WAIVED", "evidence": "no check runs observed"},
        "open_p0": [{"id": "P0", "summary": "not complete yet", "owner_action": "run the gate"}],
        "rollback": {"design": "revert one additive commit", "verified": False},
        "claims": {"live_device": False, "client_e2e": False, "production": False},
    }


def self_test() -> int:
    failures = 0

    def expect(name: str, mutate, codes: set[str]) -> None:
        nonlocal failures
        doc = _valid_fixture()
        mutate(doc)
        got = {e["code"] for e in check(doc, json.dumps(doc)).errors}
        missing = codes - got
        if missing:
            failures += 1
            print(f"FAIL self-test {name}: expected {sorted(missing)}, got {sorted(got)}")
        else:
            print(f"ok   self-test {name}")

    base = check(_valid_fixture(), json.dumps(_valid_fixture()))
    if base.errors:
        failures += 1
        print(f"FAIL self-test baseline: {base.errors}")
    else:
        print("ok   self-test baseline")

    expect("schema", lambda d: d.update(schema="wrong"), {"E002"})
    expect("state", lambda d: d.update(state="DONE"), {"E005"})
    expect("short-sha", lambda d: d["candidates"][0].update(head="deadbeef"), {"E034"})
    expect("not-draft", lambda d: d["candidates"][0].update(draft=False), {"E035"})
    expect(
        "installed-on-source",
        lambda d: d["candidates"][0].update(installed=True),
        {"E037"},
    )
    expect(
        "complete-with-not-run",
        lambda d: d.update(state="COMPLETE_V1", open_p0=[]),
        {"E091"},
    )
    expect(
        "complete-with-weak-evidence",
        lambda d: (
            d.update(state="COMPLETE_V1", open_p0=[]),
            d["gates"][0].update(verdict="PASS"),
            d["claims"].update(live_device=True, client_e2e=True, production=True),
            d["qa"].update(signature="sig"),
        ),
        {"E092"},
    )
    expect(
        "empty-p0-on-candidate",
        lambda d: d.update(open_p0=[]),
        {"E090"},
    )
    expect(
        "self-signed-qa",
        lambda d: d["qa"].update(owner="integrator-a"),
        {"E012"},
    )
    expect(
        "merge-before-gates",
        lambda d: d["source_merge"].update(performed=True),
        {"E095"},
    )
    expect(
        "claim-while-candidate",
        lambda d: d["claims"].update(live_device=True),
        {"E083"},
    )
    expect(
        "qa-signed-without-signature",
        lambda d: d["qa"].update(status="SIGNED"),
        {"E016"},
    )
    expect(
        "qa-pending-with-signature",
        lambda d: d["qa"].update(signature="sig"),
        {"E017"},
    )
    expect(
        "qa-bad-status",
        lambda d: d["qa"].update(status="DONE"),
        {"E015"},
    )
    expect("missing-drift-guard", lambda d: d.pop("drift_guard"), {"E110"})
    expect(
        "missing-observed-at",
        lambda d: d["candidates"][0].pop("observed_at"),
        {"E114"},
    )
    expect(
        "complete-on-stale-pin",
        lambda d: (
            d.update(state="COMPLETE_V1", open_p0=[]),
            d["gates"][0].update(verdict="PASS", achieved_level="LOCAL"),
            d["claims"].update(live_device=True, client_e2e=True, production=True),
            d["qa"].update(status="SIGNED", signature="sig"),
            d["candidates"][0].update(observed_at="2026-10-16T00:00:00Z"),
        ),
        {"E115"},
    )
    expect(
        "secret",
        lambda d: d.update(manifest_id="fixture-rc-1"),
        set(),
    )
    leak = _valid_fixture()
    text = json.dumps(leak) + " ghp_" + "A" * 30
    if not any(e["code"] == "E100" for e in check(leak, text).errors):
        failures += 1
        print("FAIL self-test secret-scan")
    else:
        print("ok   self-test secret-scan")

    print(f"\nself-test: {'PASS' if failures == 0 else f'{failures} FAILED'}")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip())
        return 2
    if argv[0] == "--self-test":
        return self_test()
    as_json = "--json" in argv
    path = argv[0]
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        doc = json.loads(raw)
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"{path}: invalid JSON: {exc}", file=sys.stderr)
        return 1

    report = check(doc, raw)
    if as_json:
        print(
            json.dumps(
                {
                    "path": path,
                    "ok": report.ok,
                    "errors": report.errors,
                    "warnings": report.warnings,
                },
                indent=2,
            )
        )
    else:
        print(f"{path}: {doc.get('manifest_id', '?')} state={doc.get('state', '?')}")
        for w in report.warnings:
            print(f"  WARN {w['code']} {w['where']}: {w['message']}")
        for e in report.errors:
            print(f"  ERROR {e['code']} {e['where']}: {e['message']}")
        print(f"  {'OK' if report.ok else 'FAILED'} ({len(report.errors)} errors, {len(report.warnings)} warnings)")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
