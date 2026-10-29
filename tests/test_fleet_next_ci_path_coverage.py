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

   The offline job's runner is `pytest`, not `unittest discover`, and that is a
   measured requirement rather than a preference. Both test styles are live in
   this tree: the Wave files are `unittest.TestCase` classes, and
   `tests/test_fleet_next_legacy_survival.py` (#478/#483, already on `main`) is
   plain `def test_*`. The first hosted run of this change read
   `tests/test_fleet_next_legacy_survival.py  exit=5 collected=0 floor=1` —
   `unittest` had imported a real 70-test contract and found nothing to run.
   The floor was right to refuse that as green, but a gate that reds a file
   because it looked with the wrong instrument is the same defect as one that
   greens it. `pytest` is the runner `tests.yml` uses, it collects both styles,
   and the tests below hold the gate to it — including a live check that no
   `tests/test_fleet_next_*.py` in this tree is invisible to it.

THE COMPOSITION SUBJECT (W19)
-----------------------------
#511 measured the composition by checking out the literal `05da04f8`. That was
reproducible but it froze the gate on one commit: as `main` advances, the
measurement stops being about the head under review. The subject is now the run
itself — `github.event.pull_request.head.sha || github.sha` — with a SHA
injectable only through `workflow_dispatch` (which requires write access), so a
pull request cannot point the gate at a commit of its choosing. The one literal
SHA left is a *dated* compatibility witness that runs only when the head does
not carry the monitor stack, and the tests below bound it: 40 hex, an expiry
inside a short window, and an `STACK_ABSORBED_ON` switch that turns "absent"
from tolerated into red once a landing PR puts the stack on `main`.

The path matcher below is minimatch's rules for the subset this workflow uses:
`*` and `?` stop at `/`, `**/` spans zero or more directories. It is written out
rather than imported because the check has to run with the same bounded
dependency set the workflow installs, and because a matcher that silently
disagrees with GitHub's is worse than no check at all.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = Path(".github/workflows/fleet-next-isolated-contracts.yml")
COVERAGE_TEST = "tests/test_fleet_next_ci_path_coverage.py"

#: The surfaces this workflow exists to guard. Grouped by the wave (or W19
#: landing lane) that introduced them so a failure says which family lost its
#: trigger.
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
    # W19's job is to land these onto `main` in narrow batches. A landing PR
    # that touches only its own module must still be measured, otherwise the
    # merge that finally puts the stack on `main` is the one run that skips the
    # contract.
    "W19 mainline landing": (
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/contract.py",
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/notifier.py",
        "tests/test_fleet_next_agent_monitor_activation_truth.py",
        "integrations/fleet-next-wave18-sidecar-installer/installer.py",
        "integrations/fleet-next-wave18-sidecar-installer/probe_guards.py",
        "tests/test_fleet_next_wave18_sidecar_installer.py",
        "integrations/fleet-next-wave18-artifact-builder/wave18_builder.py",
        "integrations/fleet-next-v1-release-captain/release_captain.py",
        "tests/test_fleet_next_v1_release_captain.py",
        "integrations/fleet-next-local-goal-supervisor/supervisor.py",
        "tests/test_fleet_next_local_goal_supervisor.py",
        "integrations/fleet-next-wave18-release-acceptance/wave18_acceptance.py",
        "integrations/fleet-next-wave18-release-acceptance/release_pin.json",
        "tests/test_fleet_next_wave18_release_acceptance.py",
        "integrations/fleet-next-legacy-safety/legacy_safety.py",
        "docs/integration/FLEET-NEXT-LEGACY-SAFETY.md",
    ),
    # W19-D replaces wave-named directories with a stable tree. A migration that
    # writes only these paths is the change a reader most expects to be gated.
    "W19 canonical docs": (
        "docs/fleet-next/architecture/README.md",
        "docs/fleet-next/operations/rollback.md",
        "docs/fleet-next/verification/gates.md",
        "docs/fleet-next/history/wave18-rc.md",
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
    "docs/fleet-next-notes.md",
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
    """The job's text as written, not re-wrapped.

    `yaml.safe_dump` folds long `run:` blocks at the column limit and escapes
    quotes, so an assertion about a shell line would fail on the folding rather
    than on the line being gone. Join the raw strings instead.
    """
    job = document["jobs"][job_name]
    parts = [json.dumps(job.get("env", {}), sort_keys=False)]
    for step in job.get("steps", []):
        parts.append(str(step.get("name", "")))
        parts.append(str(step.get("if", "")))
        if "run" in step:
            parts.append(str(step["run"]))
        if "with" in step:
            parts.append(json.dumps(step["with"], sort_keys=False))
    return "\n".join(parts)


def composition_job(document: dict) -> dict:
    return document["jobs"]["composition-runtime-tick"]


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

    def test_the_w19_canonical_tree_is_a_path_family(self):
        """W19-D replaces wave-named directories with `docs/fleet-next/`."""
        self.assertTrue(
            triggered(["docs/fleet-next/architecture/README.md"],
                      self.triggers, "pull_request"),
            "the W19 canonical doc tree is not gated, so the migration PR that "
            "moves the wave records would run NOT_RUN")


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

    def test_the_offline_job_uses_the_same_runner_as_the_main_suite(self):
        """One instrument, and the same version as `tests.yml`.

        `unittest discover` reports `Ran 0 tests ... OK` for an absent file and
        collects zero cases from a `def test_*` file that is right there. Both
        would be measured by a second, quieter runner than the one that decides
        whether `main` is green.
        """
        steps = job_steps(self.document, "offline-unit-contracts")
        self.assertIn("python3 -m pytest", steps,
                      "the offline gate is not run with pytest, so a "
                      "`def test_*` contract in this tree collects nothing and "
                      "reads as red (or, with a laxer floor, as green)")
        self.assertIn("-p no:cacheprovider", steps,
                      "a nested runner that writes .pytest_cache into the "
                      "checkout leaves state the next run can read")
        self.assertNotIn("python3 -m unittest", steps,
                         "the offline gate still shells out to unittest, whose "
                         "`Ran 0 tests ... OK` is the reading this file exists "
                         "to refuse")

    def test_the_runner_version_matches_the_main_suite_pin(self):
        """A second runner version is a second answer to the same question."""
        steps = job_steps(self.document, "offline-unit-contracts")
        pinned = re.findall(r"'pytest==([0-9][^']*)'", steps)
        self.assertTrue(pinned, "the offline job does not pin a pytest version")
        main_workflow = (REPO / ".github/workflows/tests.yml").read_text(
            encoding="utf-8")
        expected = re.findall(r"'pytest==([0-9][^']*)'", main_workflow)
        self.assertTrue(expected, "tests.yml no longer pins pytest")
        self.assertEqual(
            set(pinned), set(expected),
            f"the Fleet Next gate pins pytest {sorted(set(pinned))} but the "
            f"main suite pins {sorted(set(expected))}; two versions of the "
            "runner are two different answers about the same tree")

    def test_the_floor_counts_passes_and_not_collections(self):
        """A file whose every test skips has run nothing.

        `collected >= floor` is satisfied by three skipped tests under a
        package import that does not resolve — which is exactly the #503
        reading, one job over.
        """
        steps = job_steps(self.document, "offline-unit-contracts")
        self.assertIn("count_in_log", steps,
                      "the offline gate does not parse pytest's summary, so its "
                      "floor is not reading a count at all")
        self.assertIn("FAIL_NO_COLLECTION", steps,
                      "the offline gate does not name the absent-file case")
        self.assertIn('passed" -lt "$floor"', steps,
                      "the floor is not applied to the passing count; a gated "
                      "file whose tests all skip would satisfy it")
        self.assertIn('status" -eq 5', steps,
                      "pytest exits 5 when it collected nothing, and that exit is "
                      "the one reading that must never be called green")

    def test_a_failing_file_is_not_reported_as_absent(self):
        """A file whose one test failed was collected; it ran.

        The floor and the failure branch must be ordered so the diagnosis says
        `FAIL: … failed=1`, not `FAIL_NO_COLLECTION`. A control on the first
        draft caught exactly that: the floor was checked first, so a single
        failing test in a file with floor 1 read as "collected nothing" and sent
        its owner looking for a missing test file.
        """
        steps = job_steps(self.document, "offline-unit-contracts")
        self.assertIn("passed + failed + errors + skipped", steps,
                      "the gate does not distinguish 'nothing ran' from 'ran "
                      "and failed'")
        failing = steps.index("FAIL: %s failed=")
        floor = steps.index("passed %s of %s collected")
        self.assertLess(failing, floor,
                        "the floor is still checked before the failure branch, "
                        "so a failing file is reported as an absence")

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


class NoFleetNextFileIsInvisibleToTheRunner(unittest.TestCase):
    """The live half: ask the runner itself, on this tree.

    The static tests above pin *how* the offline job collects. This one answers
    whether, right now, every `tests/test_fleet_next_*.py` in the checkout
    yields at least one node to that runner. It is the check that would have
    caught the first hosted run of this change locally:
    `tests/test_fleet_next_legacy_survival.py` was collected by pytest 70 times
    and by `unittest` zero times.
    """

    def test_every_contract_in_this_tree_is_collected(self):
        files = sorted(p.name for p in (REPO / "tests").glob("test_fleet_next_*.py"))
        self.assertTrue(files, "no tests/test_fleet_next_*.py in this tree")
        gated = [f for f in files if not f.endswith("_ledger.py")]
        self.assertTrue(gated, "every Fleet Next contract was excluded by name")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "--collect-only", *[f"tests/{name}" for name in gated]],
            cwd=str(REPO), capture_output=True, text=True)
        output = proc.stdout + proc.stderr
        self.assertEqual(
            proc.returncode, 0,
            f"pytest could not collect this tree (exit {proc.returncode}):\n"
            f"{output[-4000:]}")
        counted: dict[str, int] = {}
        for line in output.splitlines():
            match = re.match(r"(tests/[^:]+)::", line.strip())
            if match:
                counted[match.group(1)] = counted.get(match.group(1), 0) + 1
        invisible = [f"tests/{name}" for name in gated
                     if counted.get(f"tests/{name}", 0) == 0]
        self.assertEqual(
            invisible, [],
            f"the runner collects nothing from {invisible}; a file whose tests "
            "the gate cannot see is not measured, and a gate that reports it "
            "red is reporting its own instrument")


class TheCompositionMeasuresTheRunNotAMuseumPiece(unittest.TestCase):
    """W19: the subject is the run's own head, and a SHA cannot be injected."""

    def setUp(self):
        self.text, self.document = load_workflow()
        self.job = composition_job(self.document)
        self.serialized = job_steps(self.document, "composition-runtime-tick")

    def test_the_subject_is_the_run_itself(self):
        env = self.job.get("env", {})
        self.assertEqual(
            str(env.get("HEAD_SHA", "")).replace(" ", ""),
            "${{github.event.pull_request.head.sha||github.sha}}",
            "the head is not taken from the event; a fixed value here is what "
            "pinned #511 to a commit that main will never advance past")
        self.assertNotIn("COMPOSITION_SHA", env,
                         "a literal subject SHA is back in the environment; the "
                         "primary path must measure the head, and only the dated "
                         "witness may pin")
        self.assertNotIn("env.COMPOSITION_SHA", self.serialized,
                         "a checkout is still pointed at a literal subject SHA")

    def test_the_checkout_is_the_resolved_subject(self):
        self.assertIn('"ref": "${{ steps.subject.outputs.subject }}"',
                      self.serialized,
                      "the job does not check out the subject it resolved")
        self.assertIn("git rev-parse HEAD", self.serialized,
                      "the job does not prove the checkout is the subject it "
                      "claims to measure")

    def test_a_candidate_sha_can_only_come_from_a_maintainer_dispatch(self):
        dispatch = self.triggers_of_dispatch()
        self.assertIn("candidate_sha",
                      str(dispatch.get("inputs", {})),
                      "there is no way to inject a candidate SHA at all, so a "
                      "mainline landing cannot be measured before it merges")
        for fragment in ("BLOCKED_CANDIDATE_NOT_ALLOWED", "workflow_dispatch",
                         "^[0-9a-f]{40}$", "BLOCKED_CANDIDATE_SHA"):
            self.assertIn(fragment, self.serialized,
                          f"the candidate path no longer proves `{fragment}`")

    def test_the_candidate_is_never_read_from_pull_request_content(self):
        for forbidden in ("pull_request.body", "pull_request.title",
                          "pull_request.labels", "github.event.issue",
                          ".github/ci-pin", "ci-pin.json"):
            self.assertNotIn(
                forbidden, self.text,
                f"`{forbidden}` lets pull-request content name the commit this "
                "gate measures, which is an escalation, not a trigger")

    def triggers_of_dispatch(self) -> dict:
        return triggers_of(self.document).get("workflow_dispatch") or {}


class TheCompositionCannotBeQuietlyAbsent(unittest.TestCase):
    """A missing stack is BLOCKED or a dated witness — never a green."""

    def setUp(self):
        self.text, self.document = load_workflow()
        self.job = composition_job(self.document)
        self.env = {k: str(v) for k, v in (self.job.get("env") or {}).items()}
        self.serialized = job_steps(self.document, "composition-runtime-tick")

    def test_the_witness_pin_is_an_exact_commit(self):
        self.assertRegex(self.env.get("COMPAT_PIN", ""), r"^[0-9a-f]{40}$",
                         "the compatibility witness is not a 40-hex commit")

    def test_the_witness_is_dated_and_expires(self):
        expires = self.env.get("COMPAT_PIN_EXPIRES", "")
        try:
            when = datetime.date.fromisoformat(expires)
        except ValueError:
            raise AssertionError(
                f"COMPAT_PIN_EXPIRES={expires!r} is not an ISO date, so the "
                "compatibility window has no end and the witness can stay on "
                "main forever")
        today = datetime.date.today()
        self.assertGreater(
            when, today,
            "the compatibility window has already closed; a stale tree would be "
            "the only way to run the three tests")
        self.assertLessEqual(
            (when - today).days, 60,
            f"the window ends {expires}, more than 60 days out; that is long "
            "enough to forget it is there")
        self.assertIn("-d \"$COMPAT_PIN_EXPIRES\"", self.serialized,
                      "COMPAT_PIN_EXPIRES is declared but never compared to the "
                      "date, so it is decoration rather than a bound")
        self.assertIn("COMPAT_WINDOW_EXPIRED", self.serialized,
                      "an expired window does not fail; the witness would keep "
                      "the gate green on an ancient tree")

    def test_the_witness_only_runs_when_the_head_lacks_the_stack(self):
        witness = [step for step in self.job["steps"]
                   if step.get("name", "").startswith(
                       "Check out the compatibility witness")]
        self.assertEqual(len(witness), 1,
                         "the compatibility witness checkout is missing")
        self.assertIn(
            "steps.presence.outputs.stack_present == '0'",
            str(witness[0].get("if", "")),
            "the witness is not conditional on the head lacking the stack, so it "
            "could stand in for a head that was never measured")

    def test_a_missing_stack_is_never_green_inside_the_gate(self):
        for fragment in ("BLOCKED_COMPOSITION", "BLOCKED_COMPOSITION_REMOVED",
                         "STACK_ABSORBED_ON", "COMPOSITION_SUBJECT_ABSENT"):
            self.assertIn(fragment, self.serialized,
                          f"the job no longer proves `{fragment}`")

    def test_the_run_says_which_tree_it_measured(self):
        for fragment in ("COMPOSITION_MEASURED_SUBJECT", "COMPOSITION_WITNESS_PIN"):
            self.assertIn(fragment, self.serialized,
                          f"the log never says `{fragment}`, so a reader cannot "
                          "tell the head from the witness")

    def test_an_absorbed_stack_cannot_be_removed_silently(self):
        absorbed = self.env.get("STACK_ABSORBED_ON", "")
        self.assertTrue(absorbed == "none"
                        or re.fullmatch(r"[0-9a-f]{40}", absorbed),
                        f"STACK_ABSORBED_ON={absorbed!r} is neither `none` nor a "
                        "40-hex merge SHA, so the switch cannot be flipped")
        self.assertIn('if [ "$STACK_ABSORBED_ON" != "none" ]', self.serialized,
                      "the absorbed switch is not read before tolerating an "
                      "absent stack")

    def test_the_three_tests_and_the_skip_refusal_survive(self):
        self.assertEqual(self.env.get("EXPECTED_TESTS"), "3",
                         "the composed runtime-tick regression is three tests; "
                         "a different number is a different composition")
        for fragment in (
            "test_fleet_next_agent_monitor_activation_runtime_tick",
            "Ran 3 tests",
            "skipped",
            "FAIL_SKIPPED_COMPOSITION",
            "BLOCKED_COMPOSITION",
        ):
            self.assertIn(fragment, self.serialized,
                          f"the composition job no longer proves `{fragment}`")

    def test_no_stub_override_download_or_second_repository(self):
        for forbidden in ("pip install", "curl", "wget", "git clone",
                          "SERVEROS_FLEET_NEXT_MONITOR_ROOT",
                          "skipTest"):
            self.assertNotIn(
                forbidden, self.serialized,
                f"`{forbidden}` in the composition job means a composition that "
                "could not be built is being made to look built")


PRESENCE_STEP = "Decide whether this head carries the composition"

STACK_FILES = {
    "MONITOR_RUNTIME":
        "integrations/fleet-next-agent-monitor/fleet_next_agent_monitor/runtime.py",
    "ACTIVATION":
        "integrations/fleet-next-agent-monitor-activation/activation.py",
    "RUNTIME_TICK":
        "tests/test_fleet_next_agent_monitor_activation_runtime_tick.py",
}


def presence_script(document: dict) -> str:
    """The presence step's shell as written — executed, not pattern-matched."""
    steps = [step for step in composition_job(document)["steps"]
             if step.get("name") == PRESENCE_STEP]
    if len(steps) != 1:
        raise AssertionError(
            f"composition-runtime-tick: step `{PRESENCE_STEP}` is missing, so "
            "the decision that excuses an absent stack cannot be measured")
    return str(steps[0]["run"])


class TheAbsorbedStackIsRealAndCannotBeRevived(unittest.TestCase):
    """#520 put the stack on `main`; from that commit, absence is red.

    The switch is what stands between "tolerated absence" and "the stack was
    deleted". A revert to `none` is the quiet failure: the dated witness
    becomes reachable again and a tree that lost the stack measures a museum
    piece instead. Two of these tests exist only to make that revert loud —
    one reads the switch against the tree this test runs in, the other proves
    the declared landing is a real ancestor rather than a decorative string.
    """

    def setUp(self):
        self.text, self.document = load_workflow()
        self.job = composition_job(self.document)
        self.env = {k: str(v) for k, v in (self.job.get("env") or {}).items()}
        self.serialized = job_steps(self.document, "composition-runtime-tick")

    def test_a_tree_that_carries_the_stack_may_not_say_none(self):
        present = [rel for rel in STACK_FILES.values() if (REPO / rel).is_file()]
        self.assertEqual(
            len(present), len(STACK_FILES),
            "this checkout does not carry the monitor stack, so it cannot say "
            f"what `main` absorbed; missing: "
            f"{sorted(set(STACK_FILES.values()) - set(present))}")
        absorbed = self.env.get("STACK_ABSORBED_ON", "")
        self.assertNotEqual(
            absorbed, "none",
            "this tree carries `runtime.py`, `activation.py` and the "
            "runtime-tick regression, so `main` has absorbed the stack — but "
            "STACK_ABSORBED_ON still says `none`. That is the revert that "
            "re-opens the dated witness: a later tree that loses the stack "
            "would fall back to COMPAT_PIN and go green against a museum piece "
            "instead of red with BLOCKED_COMPOSITION_REMOVED")
        self.assertRegex(absorbed, r"^[0-9a-f]{40}$",
                         f"STACK_ABSORBED_ON={absorbed!r} is not a 40-hex "
                         "landing commit")

    def test_the_declared_landing_is_a_real_ancestor_of_this_head(self):
        absorbed = self.env["STACK_ABSORBED_ON"]
        git = shutil.which("git")
        if git is None or not (REPO / ".git").exists():
            self.skipTest("no git history in this checkout to read ancestry from")
        known = subprocess.run(
            [git, "cat-file", "-e", f"{absorbed}^{{commit}}"],
            cwd=REPO, capture_output=True, text=True)
        if known.returncode != 0:
            # A truncated clone cannot tell a bogus sha from an old one: the
            # shards check out with `fetch-depth: 1`, where only HEAD exists and
            # `cat-file -e` exits 128 for any landing that is not the tip. So
            # this half is measured where history is complete — the offline job
            # fetches the full history on purpose, and
            # `test_the_strict_ancestry_check_runs_where_history_is_complete`
            # keeps it that way. Reading a shallow absence as "names nothing"
            # would red the shards on a correct switch.
            if self._is_shallow(git):
                self.skipTest(
                    f"shallow clone: {absorbed} is not in this depth-1 checkout, "
                    "so ancestry is unmeasurable here — the offline gate, which "
                    "checks out with fetch-depth: 0, is where this assertion "
                    "actually runs")
            self.fail(
                f"STACK_ABSORBED_ON={absorbed} names nothing in this clone; the "
                "switch has to point at the landing that actually put the stack "
                "on main, not at a plausible-looking sha")
        merged = subprocess.run(
            [git, "merge-base", "--is-ancestor", absorbed, "HEAD"],
            cwd=REPO, capture_output=True, text=True)
        self.assertEqual(
            merged.returncode, 0,
            f"{absorbed} is not an ancestor of this head, yet the workflow "
            "uses it to claim the stack is already on `main`")

    def _is_shallow(self, git):
        probe = subprocess.run(
            [git, "rev-parse", "--is-shallow-repository"],
            cwd=REPO, capture_output=True, text=True)
        return probe.stdout.strip() == "true"

    def test_the_strict_ancestry_check_runs_where_history_is_complete(self):
        """The shallow skip above is not an escape hatch, and this pins why.

        If every checkout were shallow, the ancestry assertion would never run
        and any 40-hex string would do. The offline job is the one that runs this
        file's contracts for real, and it must keep the full history — the same
        job that owns the `gate_file` counters.
        """
        offline = self.document["jobs"]["offline-unit-contracts"]
        checkouts = [step for step in offline["steps"]
                     if "actions/checkout@" in str(step.get("uses", ""))]
        self.assertEqual(len(checkouts), 1,
                         "the offline job must check out exactly once")
        depth = str((checkouts[0].get("with") or {}).get("fetch-depth"))
        self.assertEqual(
            depth, "0",
            f"offline-unit-contracts checks out with fetch-depth={depth}; with a "
            "truncated history the ancestry assertion in this file can only ever "
            "skip, and STACK_ABSORBED_ON becomes a decorative string")

    def test_the_witness_cannot_run_once_the_stack_is_absorbed(self):
        witness = [step for step in self.job["steps"]
                   if step.get("name", "").startswith(
                       ("Check out the compatibility witness",
                        "Prove the witness is the pinned commit"))]
        self.assertEqual(len(witness), 2,
                         "the compatibility witness steps are missing")
        for step in witness:
            condition = str(step.get("if", ""))
            self.assertIn(
                "env.STACK_ABSORBED_ON == 'none'", condition,
                f"`{step['name']}` runs on a head that lacks the stack even "
                "after absorption; the presence step reds that head, but the "
                "witness condition is the structural half — without it, "
                "re-adding the witness is one edit away")

    def test_the_presence_decision_still_reads_the_absorbed_switch_first(self):
        self.assertIn('if [ "$STACK_ABSORBED_ON" != "none" ]', self.serialized,
                      "the absorbed switch is not read before tolerating an "
                      "absent stack")
        self.assertLess(
            self.serialized.index('if [ "$STACK_ABSORBED_ON" != "none" ]'),
            self.serialized.index("COMPAT_WINDOW_EXPIRED"),
            "the dated witness is decided before the absorbed switch, so an "
            "expired-but-absorbed head would report the wrong reason")


class ThePresenceDecisionFailsClosedOnItsOwn(unittest.TestCase):
    """Execute the job's own presence step: stack present, then removed.

    Every other test in this file reads the workflow. This one *runs* the shell
    the job will run, against a tree built for the purpose, so the claim
    "a removed stack is red" is a measurement rather than a quote. The
    controls go in both directions: the same tree is green while it carries
    the stack, red once it loses it, and the only shape that still tolerates
    absence is the pre-absorption one, bounded by its date.
    """

    def setUp(self):
        self.text, self.document = load_workflow()
        self.env = {k: str(v) for k, v in
                    (composition_job(self.document).get("env") or {}).items()}
        self.script = presence_script(self.document)
        if shutil.which("bash") is None:
            self.skipTest("no bash on this host; the hosted gate is the place "
                          "this step actually runs")

    def run_presence(self, *, files: int, absorbed: str, expires: str):
        """Build a throwaway tree and run the step's shell inside it."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for index, rel in enumerate(STACK_FILES.values()):
                if index < files:
                    target = root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        "# stand-in: the step reads presence, not content\n",
                        encoding="utf-8")
            (root / "test-output").mkdir()
            output = root / "github_output"
            output.write_text("", encoding="utf-8")
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GITHUB_OUTPUT": str(output),
                "STACK_ABSORBED_ON": absorbed,
                "COMPAT_PIN_EXPIRES": expires,
            }
            environment.update(STACK_FILES)
            completed = subprocess.run(
                ["bash", "-e", "-c", self.script],
                cwd=root, env=environment, capture_output=True, text=True,
                timeout=120)
            log_path = root / "test-output" / "composition-identity.log"
            log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            return completed, log, output.read_text(encoding="utf-8")

    def test_the_tree_that_carries_the_stack_is_measured_not_excused(self):
        completed, log, output = self.run_presence(
            files=3, absorbed=self.env["STACK_ABSORBED_ON"], expires="2099-01-01")
        self.assertEqual(completed.returncode, 0,
                         f"a tree carrying all three stack files was refused:\n"
                         f"{completed.stdout}\n{completed.stderr}\n{log}")
        self.assertIn("stack_present=1", output)
        self.assertNotIn("BLOCKED_COMPOSITION_REMOVED", log,
                         "the control tree was read as having removed the stack")

    def test_the_same_tree_after_the_stack_is_removed_is_red(self):
        completed, log, _ = self.run_presence(
            files=2, absorbed=self.env["STACK_ABSORBED_ON"], expires="2099-01-01")
        self.assertEqual(
            completed.returncode, 1,
            "a tree that lost the stack — with the switch already naming the "
            f"landing — did not fail; it silently became a witness run:\n{log}")
        self.assertIn("BLOCKED_COMPOSITION_REMOVED", log)
        self.assertIn(self.env["STACK_ABSORBED_ON"], log,
                      "the failure does not name the landing it expected")
        self.assertNotIn("COMPOSITION_SUBJECT_ABSENT", log,
                         "the removed-stack tree was excused as a pre-absorption "
                         "head, which is the false green the switch exists to stop")

    def test_an_expired_window_does_not_excuse_an_absent_stack(self):
        completed, log, _ = self.run_presence(
            files=0, absorbed="none", expires="2020-01-01")
        self.assertEqual(completed.returncode, 1,
                         f"an absent stack past the expiry was tolerated:\n{log}")
        self.assertIn("COMPAT_WINDOW_EXPIRED", log)

    def test_an_unparseable_expiry_is_red_rather_than_open_ended(self):
        completed, log, _ = self.run_presence(
            files=0, absorbed="none", expires="not-a-date")
        self.assertEqual(completed.returncode, 1,
                         f"an unparseable expiry left the witness open-ended:\n{log}")
        self.assertIn("COMPAT_PIN_UNPARSEABLE", log)

    def test_the_pre_absorption_witness_is_still_reachable_on_purpose(self):
        completed, log, output = self.run_presence(
            files=0, absorbed="none", expires="2099-01-01")
        self.assertEqual(completed.returncode, 0,
                         "a maintainer can no longer measure a pre-absorption "
                         f"commit at all:\n{log}")
        self.assertIn("COMPOSITION_SUBJECT_ABSENT", log)
        self.assertIn("stack_present=0", output)


class TheGateCannotEscalate(unittest.TestCase):
    """Read-only token, no `pull_request_target`, no secret, no PR-named SHA."""

    def setUp(self):
        self.text, self.document = load_workflow()

    def test_the_token_is_read_only(self):
        permissions = {k: str(v) for k, v in
                       (self.document.get("permissions") or {}).items()}
        self.assertEqual(permissions, {"contents": "read"},
                         "the workflow's token is not read-only")

    def test_no_job_widens_the_token(self):
        for name, job in self.document["jobs"].items():
            self.assertNotIn(
                "permissions", job,
                f"job `{name}` declares its own permissions; a job-level block "
                "can widen what the workflow-level read-only grant allows")

    def test_there_is_no_pull_request_target_and_no_secret(self):
        self.assertNotIn(
            "pull_request_target", self.text,
            "`pull_request_target` runs with the base repository's token and "
            "secrets against untrusted content")
        self.assertNotIn("secrets.", self.text,
                         "this gate needs no secret; one here is an escalation "
                         "waiting for a use")

    def test_no_arbitrary_sha_arrives_from_pull_request_content(self):
        for banned in ("pull_request.body", "pull_request.title",
                       "pull_request.labels", "pull_request.head.label"):
            self.assertNotIn(banned, self.text,
                             f"`{banned}` lets a pull request name the commit "
                             "this gate measures")


class EveryFailureLeavesItsReadingBehind(unittest.TestCase):
    """The runner starts bash with `-e`; a red gate must still print why."""

    def setUp(self):
        self.text, self.document = load_workflow()

    def _gate_steps(self) -> list[dict]:
        wanted = (
            ("offline-unit-contracts",
             "Run every Fleet Next contract in this tree, with a collection floor"),
            ("composition-runtime-tick",
             "Run the composed regression and refuse a skip"),
            ("composition-runtime-tick",
             "Assert the collection holds exactly the expected tests"),
            ("composition-runtime-tick",
             "Decide whether this head carries the composition"),
        )
        found = []
        for job, step_name in wanted:
            steps = [s for s in self.document["jobs"][job]["steps"]
                     if s.get("name") == step_name]
            self.assertEqual(len(steps), 1,
                             f"{job}: step `{step_name}` is missing, so the "
                             "reading it produced is gone too")
            found.append(steps[0])
        return found

    def test_every_gate_step_disables_the_runner_errexit(self):
        for step in self._gate_steps():
            self.assertIn(
                "set +e", yaml.safe_dump(step),
                f"{step['name']}: the runner starts bash with `-e`, so the first "
                "failing command kills the step before its diagnosis is printed")

    def test_the_composed_run_prints_the_whole_log(self):
        steps = {s.get("name"): s for s in
                 self.document["jobs"]["composition-runtime-tick"]["steps"]}
        run = str(steps["Run the composed regression and refuse a skip"]["run"])
        self.assertIn('cat "$log"', run,
                      "only a tail is printed; a failure outside the tail would "
                      "be red with no reading in it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
