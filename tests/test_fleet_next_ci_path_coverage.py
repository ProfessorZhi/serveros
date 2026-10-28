#!/usr/bin/env python3
"""The Fleet Next isolated workflow must actually cover, and actually collect.

WHY THIS FILE EXISTS
--------------------
`.github/workflows/fleet-next-isolated-contracts.yml` is the only hosted job that
runs the Fleet Next contracts off the Tokyo host. It is gated by `paths:`, and a
path filter is a gate that fails *silently*: when it does not match, GitHub does
not run the job and reports nothing at all — not a skip, not a red, an absence.
#502 and #506 are the measured case. Both PRs added a whole integration
directory plus its test file; neither appears in that workflow's `paths:`; the
Fleet Next workflow therefore **never triggered** and the checks on those PRs
are the five `Tests` jobs alone. A reviewer reading "5 checks, 3 green" cannot
see that the dedicated contract never ran.

So there are two distinct defects to pin, and this file pins both:

1. **Coverage.** Every surface the workflow is supposed to own must be named by
   at least one pattern. `REQUIRED_SURFACES` is that list; a pattern deleted in
   a later edit makes the corresponding surface "uncovered" and this file red,
   naming the path that lost its trigger.
2. **Collection.** A run that triggers and then collects nothing is the other
   half of the same false green. The workflow may not rely on `discover`
   printing `Ran 0 tests ... OK`; the runtime-tick composition job must assert
   the exact count (`3`) and refuse a skipped result, and the offline job must
   run *this* file so the coverage claim is re-checked on every run.

The path matcher below is minimatch's rules for the subset this workflow uses:
`*` and `?` stop at `/`, `**/` spans zero or more directories. It is written out
rather than imported because the check has to run with the same bounded
dependency set the workflow installs, and because a matcher that silently
disagrees with GitHub's is worse than no check at all.

The composition pin is a 40-hex commit, not a branch: a branch can move, and a
gate that follows a branch measures whatever it points at today. The job checks
out that commit, proves `HEAD` is that commit, and refuses to substitute a stub
or fetch a tree from anywhere else — a missing composition is `BLOCKED`, never
a green.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = Path(".github/workflows/fleet-next-isolated-contracts.yml")
COVERAGE_TEST = "tests/test_fleet_next_ci_path_coverage.py"

#: The surfaces this workflow exists to guard. Grouped by the wave that
#: introduced them so a failure says which family lost its trigger.
REQUIRED_SURFACES = {
    "Wave 16-18 RC": (
        "docs/integration/FLEET-NEXT-RC-WAVE16-20261028.md",
        "docs/integration/FLEET-NEXT-RC-WAVE18-20261029.md",
        "integrations/fleet-next-rc-wave18/composition.json",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/core.py",
        "integrations/fleet-next-agent-monitor-activation/activation.py",
        "tests/test_fleet_next_rc_wave18_ledger.py",
        "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py",
    ),
    "Wave 17 HubNode": (
        "docs/integration/FLEET-NEXT-WAVE17-HUB-NODE-CUT-20261028.md",
        "integrations/fleet-next-wave17-hub-node-release-cut/wave17_cut.py",
        "integrations/fleet-next-wave17-hub-node-release-cut/evidence/wave17-evidence.json",
        "tests/test_fleet_next_wave17_hub_node_release_cut.py",
    ),
    "Sidecar": (
        "docs/integration/FLEET-NEXT-WAVE17-SIDECAR-REVOKE-20261028.md",
        "integrations/fleet-next-wave17-sidecar-release/sidecar_release.py",
        "tests/test_fleet_next_wave17_sidecar_release.py",
        "fleet_mcp/deploy/next_tailscale_gate.py",
        "fleet_mcp/deploy/next_tailscale_plan.example.json",
    ),
    "Release Acceptance QA": (
        "docs/integration/FLEET-NEXT-WAVE16-RELEASE-PREFLIGHT-20261028.md",
        "integrations/fleet-next-wave16-release-preflight/wave16_preflight.py",
        "integrations/fleet-next-v1-release-captain/release-manifest.2026-10-29.json",
        "integrations/fleet-next-wave18-artifact-builder/evidence/ARTIFACTS.sha256",
        "tests/test_fleet_next_wave16_release_preflight.py",
        "tests/test_fleet_next_wave18_artifact_builder.py",
    ),
}

#: Paths that must stay outside the filter. A workflow that fires on these is
#: burning a runner (and, worse, claiming a contract over trees it never reads).
UNRELATED_PATHS = (
    "README.md",
    "docs/integration/FLEET-V1-RC-20261028.md",
    "docs/fleet-old-control-retirement.md",
    "integrations/fleet-v1-rc-wave13/README.md",
    "fleet_mcp/mcp_http.py",
    "tests/test_daemon.py",
    "src/fleet_next/inside_elsewhere.py",
    "docs/integration/FLEET-NEXT-RC-WAVE18-20261029.txt",
)

#: A pattern that matches everything is not a filter; it is the absence of one.
CATCH_ALL = ("**", "*", "**/*", "*/**", "integrations/**", "tests/**", "docs/**")


def to_regex(pattern: str) -> str:
    """Translate a GitHub path filter into an anchored regular expression.

    `*` and `?` never cross a `/` (that is what `**` is for), and `**/` also
    matches zero directories so `a/**/b` covers `a/b`.
    """
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*" and pattern.startswith("**/", index):
            out.append("(?:[^/]+/)*")
            index += 3
        elif char == "*" and pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    return "".join(out)


def matches(pattern: str, path: str) -> bool:
    return re.fullmatch(to_regex(pattern), path) is not None


def triggered(paths: list[str], triggers: dict, event: str) -> bool:
    return any(matches(pattern, path) for path in paths
               for pattern in triggers[event]["paths"])


def load_workflow() -> tuple[str, dict]:
    text = (REPO / WORKFLOW).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    return text, document


def triggers_of(document: dict) -> dict:
    """`on:` is a key, not a boolean — YAML 1.1 disagrees, so accept both."""
    for key in ("on", True, "true"):
        if isinstance(document, dict) and key in document:
            return document[key]
    raise AssertionError("the workflow has no `on:` trigger block")


def job_steps(document: dict, job_name: str) -> str:
    job = document["jobs"][job_name]
    return yaml.safe_dump(job, sort_keys=False)


class TheMatcherIsNotVacuous(unittest.TestCase):
    """A matcher that matched nothing would make every other check here green."""

    def test_a_literal_path_matches_itself_only(self):
        self.assertTrue(matches("tests/test_fleet_next_install.py",
                                "tests/test_fleet_next_install.py"))
        self.assertFalse(matches("tests/test_fleet_next_install.py",
                                 "tests/test_fleet_next_install.py.bak"))

    def test_a_star_stops_at_a_slash(self):
        self.assertTrue(matches("fleet_mcp/deploy/next_*",
                                "fleet_mcp/deploy/next_install.py"))
        self.assertFalse(matches("fleet_mcp/deploy/next_*",
                                 "fleet_mcp/deploy/next/sub.py"))

    def test_a_double_star_spans_directories_and_also_zero_of_them(self):
        self.assertTrue(matches("a/**/b.py", "a/b.py"))
        self.assertTrue(matches("a/**/b.py", "a/x/y/b.py"))
        self.assertTrue(matches("integrations/fleet-next-*/**",
                                "integrations/fleet-next-x/evidence/deep/y.json"))

    def test_a_question_mark_is_one_character(self):
        self.assertTrue(matches("tests/t?.py", "tests/ta.py"))
        self.assertFalse(matches("tests/t?.py", "tests/t.py"))


class ThePathFilterCoversWhatItOwns(unittest.TestCase):
    def setUp(self):
        _, self.document = load_workflow()
        self.triggers = triggers_of(self.document)
        for event in ("pull_request", "push"):
            self.assertIn(event, self.triggers,
                          f"the workflow no longer triggers on {event}")
            self.assertIn("paths", self.triggers[event],
                          f"{event} has no `paths:` filter, so it would run on "
                          "every push of the repository")

    def test_pull_request_and_push_agree_on_one_list(self):
        pull = self.triggers["pull_request"]["paths"]
        push = self.triggers["push"]["paths"]
        self.assertTrue(pull, "the pull_request path filter is empty")
        self.assertEqual(
            pull, push,
            "the two path lists drifted; a push-only edit would run a gate the "
            "PR never ran, and the reverse hides a surface on the branch")

    def test_every_required_surface_is_triggered(self):
        uncovered = {
            family: [path for path in paths
                     if not triggered([path], self.triggers, "pull_request")]
            for family, paths in REQUIRED_SURFACES.items()
        }
        missing = {family: paths for family, paths in uncovered.items() if paths}
        self.assertEqual(
            missing, {},
            "these surfaces no longer trigger the workflow, so their PRs run "
            f"the five Tests jobs only and the contract is NOT_RUN: {missing}")

    def test_unrelated_paths_do_not_trigger(self):
        leaked = [path for path in UNRELATED_PATHS
                  if triggered([path], self.triggers, "pull_request")]
        self.assertEqual(
            leaked, [],
            f"paths outside the Fleet Next contract trigger it: {leaked}")

    def test_no_pattern_is_a_catch_all(self):
        for event in ("pull_request", "push"):
            patterns = self.triggers[event]["paths"]
            self.assertEqual(
                [p for p in patterns if p in CATCH_ALL], [],
                f"{event} carries a pattern that matches the whole repository")

    def test_push_is_bounded_to_main_and_fleet_next_branches(self):
        branches = self.triggers["push"].get("branches") or []
        self.assertIn("main", branches,
                      "a push to main is the source-commit run the 5/5 claim is "
                      "read from; without it only pull_request runs exist")
        self.assertTrue(
            any(b.startswith(("feat/fleet-next-", "qa/fleet-next-",
                              "fix/fleet-next-", "ci/fleet-next-"))
                for b in branches),
            f"no Fleet Next branch family is covered by push: {branches}")


class TheRunCannotBeGreenWithoutCollecting(unittest.TestCase):
    def setUp(self):
        self.text, self.document = load_workflow()
        self.triggers = triggers_of(self.document)

    def test_no_pull_request_target(self):
        self.assertNotIn(
            "pull_request_target", self.text,
            "pull_request_target runs untrusted code with the base repository's "
            "token; this workflow only ever needs `contents: read` on the head")

    def test_permissions_are_read_only(self):
        self.assertEqual(self.document.get("permissions", {}).get("contents"),
                         "read")
        self.assertNotIn("secrets.", self.text,
                         "this workflow is credential-free by contract")

    def test_every_job_is_bounded(self):
        for name, job in self.document["jobs"].items():
            self.assertIn("timeout-minutes", job,
                          f"job {name} has no bounded timeout")
            self.assertLessEqual(job["timeout-minutes"], 20,
                                 f"job {name} reserves more than 20 minutes")

    def test_concurrency_cancels_stale_pull_request_runs(self):
        concurrency = self.document.get("concurrency")
        self.assertIsInstance(concurrency, dict,
                              "without `concurrency` each push leaves its "
                              "superseded run consuming a runner")
        self.assertTrue(concurrency.get("group"),
                        "the concurrency group is empty, so nothing cancels")
        self.assertIn("pull_request", str(concurrency.get("cancel-in-progress")),
                      "cancel-in-progress must be true for pull requests and "
                      "must not cancel a push to main mid-flight")

    def test_the_offline_job_collects_this_file(self):
        steps = job_steps(self.document, "offline-unit-contracts")
        self.assertIn(COVERAGE_TEST, steps,
                      "the offline job does not run the coverage check, so a "
                      "later `paths:` edit would be measured by nothing")
        self.assertIn("Ran ", steps,
                      "the offline job does not assert a collected-test floor; "
                      "`discover` prints `Ran 0 tests ... OK` for a missing file")

    def test_naming_a_family_did_not_drop_the_older_files(self):
        """The families are an addition; the files they do not match stay."""
        steps = job_steps(self.document, "offline-unit-contracts")
        for older in ("tests/test_fleet_runtime_create_policy.py",
                      "tests/test_fleet_create_session_runtime.py"):
            self.assertIn(
                older, steps,
                f"{older} was gated by this workflow before the path families "
                "were named and matches none of them; dropping it turns a "
                "widening into a silent narrowing")

    def test_the_composition_job_pins_an_exact_commit(self):
        job = self.document["jobs"]["composition-runtime-tick"]
        sha = str(job.get("env", {}).get("COMPOSITION_SHA", ""))
        self.assertRegex(sha, r"^[0-9a-f]{40}$",
                         "the composition is pinned by commit, never by a "
                         "branch that can move under the gate")
        self.assertIn("${{ env.COMPOSITION_SHA }}", str(job),
                      "the job must check the pinned commit out, not the PR head")
        self.assertIn("git rev-parse HEAD", str(job),
                      "the job must prove the checkout is the pinned commit")

    def test_the_composition_job_requires_the_three_tests_and_refuses_skips(self):
        job = self.document["jobs"]["composition-runtime-tick"]
        serialized = str(job)
        self.assertEqual(str(job.get("env", {}).get("EXPECTED_TESTS")), "3",
                         "the composed runtime-tick regression is three tests; "
                         "a different number is a different composition")
        for fragment in (
            "test_fleet_next_agent_monitor_activation_runtime_tick",
            "Ran 3 tests",
            "skipped",
            "FAIL_SKIPPED_COMPOSITION",
            "BLOCKED_COMPOSITION",
        ):
            self.assertIn(fragment, serialized,
                          f"the composition job no longer proves `{fragment}`")

    def test_the_composition_job_never_substitutes_a_stub(self):
        serialized = job_steps(self.document, "composition-runtime-tick")
        for forbidden in ("pip install", "curl", "wget", "git clone",
                          "SERVEROS_FLEET_NEXT_MONITOR_ROOT",
                          "skipTest"):
            self.assertNotIn(
                forbidden, serialized,
                f"`{forbidden}` in the composition job means a composition that "
                "could not be built is being made to look built")


if __name__ == "__main__":
    unittest.main(verbosity=2)
