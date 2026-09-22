"""`serverosctl space` — the two kernels in `space/`, and what wiring them means.

BOTH KERNELS SHIPPED IN THE ARTIFACT AND NOTHING NAMED THEM
------------------------------------------------------------
`space/resolver.py` and `space/meeting_relevance.py` are in `space/`, `space/`
ships in every revision, and `grep -rn "space\\.resolver\\|from space\\|import space"
runtime/ mcp/ abilities/` found nothing. On an installed node the verb was not
missing, it was unnameable: the only way to run either kernel was `python3
space/resolver.py --request …` from a source checkout.

That is the same defect `cmd_document_resolve`'s docstring records for
`documents/document_governance.py`, and it is worth a test file rather than a
line in one, because the fix has four properties that are individually easy to
get wrong and that a passing exit code does not distinguish:

  1. **The exit codes are the kernels' own.** Both kernels say the same thing in
     their own docstrings: 0 for a decision, 2 for a malformed or unreadable
     request. `PROPOSAL_REQUIRED`, `REFUSAL`, `UNKNOWN` and `REVIEW` are all
     *decisions* — each is the most honest answer its kernel gives — so a verb
     that exits non-zero on one of them makes honesty look like failure. This is
     testable only because the CLI is run as a real process: a `main()` called
     in-process can return 0 while the shell sees something else.

  2. **The record is not re-shaped.** `--json` must be the kernel's record, not
     a CLI-shaped summary of it. A second schema is a second authority, and the
     kernels' records are the thing every other consumer reads.

  3. **A proposal is not a permission.** The resolver's vocabulary sets
     `approval.cross_surface` to `proposal-only` because on Feishu "add to wiki"
     has no automatic undo. A `MOVE` rendered without its `approval` reads as an
     instruction.

  4. **No clock is reachable.** The meeting kernel evaluates expiry against an
     explicit `as_of` and fails closed when it cannot. A `--as-of` flag on the
     CLI would be a place for a shell-derived value to enter, so there is none,
     and that absence is asserted here rather than assumed.

THE REJECTED VALUE
------------------
Both kernels name `REJECTED` in their `DECISIONS` tuple. No path in `resolve()`,
`classify()` or `plan_cleanup()` returns it: it is emitted only by that module's
`main()` except-branch, and this CLI does not call `main()`. The tests below
assert the reachable set is the reachable set — a value in a closed tuple that
nothing can emit reads as a check, which is exactly the class of thing
`cmd_document_resolve` refused to invent a status for.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

AS_OF = "2026-09-20T00:00:00Z"
LATER = "2027-01-01T00:00:00Z"
CHAT = "chat:oc_support_0001"
TENANT = "tenant:t_synthetic"

ALL_CHECKS = [
    "CURRENT_OBJECT_REVISION",
    "CONFIRMED_OWNER_AND_PERMISSION",
    "NO_ACTIVE_OR_DURABLE_REFERENCES_REQUIRING_PRESERVATION",
    "NO_HUMAN_UNIQUE_EDITS",
    "NO_ORGANIZATIONAL_HOLD",
    "PROTECTED_EXCEPTIONS_RESOLVED",
    "PROVIDER_ACTION_SUPPORT_VERIFIED",
    "ACTUAL_RECOVERY_SEMANTICS_VERIFIED",
]


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load("serveros_space_resolver_kernel", ROOT / "space" / "resolver.py")
meeting = _load("serveros_space_meeting_kernel", ROOT / "space" / "meeting_relevance.py")


def cli(*args: str, stdin=None) -> subprocess.CompletedProcess:
    """The real process, so an exit code is a fact about the shell."""
    return subprocess.run(
        [sys.executable, str(RUNTIME / "cli.py"), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=stdin)


def request(document: dict) -> Path:
    """A request document in a directory that outlives the call.

    Callers append to a list this module owns; a TemporaryDirectory that has
    been cleaned up is the classic way for a "the CLI refused" test to pass
    because the file was missing rather than because the request was bad.
    """
    directory = Path(tempfile.mkdtemp(prefix="space-cli-"))
    KEEP.append(directory)
    path = directory / "request.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


KEEP: list[Path] = []


# ---------------------------------------------------------------------------
# request fixtures, in the shapes the kernels' own tests use
# ---------------------------------------------------------------------------


def placement(surface: str, role: str, **context) -> dict:
    return {"object": {"surface": surface, "object_type": "document",
                       "semantic_role": role},
            "context": context}


def bundle() -> dict:
    return {
        "bundle_ref": "bundle:mb_0001",
        "scope": {"chat_ref": CHAT, "tenant_ref": TENANT},
        "signals": {},
        "content": {"transcript_segments": [
            {"start_s": 12.0, "end_s": 40.0, "provenance": "transcript:v1",
             "text": "SENTINEL-TRANSCRIPT-TEXT-MUST-NOT-BE-COPIED",
             "tie": {"kind": "support_request", "ref": "msg:om_support_0001"}}]},
        "work_relation": {"references": []},
        "dependants": [],
        "ownership": "OWNED",
        "hold": "NONE",
    }


def policy(level: str = "CONTENT_ISOLATION", **overrides) -> dict:
    base = {
        "policy_revision": "rev-0007",
        "source_selector": {"kind": "chat_ref", "id": CHAT},
        "operation": level,
        "scope": {"chat_ref": CHAT},
        "owner_constraint": "SELF_ONLY",
        "protected_exceptions": ["support:marek-open-mic"],
        "relevance_rule": "confirmed-muted-support-source",
        "retention_window": {"approved_policy_ref": "retention:meeting-support-v1",
                             "days": 30},
        "allowed_content_depth": "TRANSCRIPT",
        "delete_mode": {"CONTENT_ISOLATION": "NO_DELETE", "REBUILDABLE_ARTIFACT_CLEANUP": "NO_DELETE",
                        "CLOUD_RECOVERABLE_DISPOSAL": "RECYCLE_BIN",
                        "IRREVERSIBLE_DELETE": "PERMANENT"}[level],
        "approval": "PROPOSAL_ONLY",
        "binding_state": "BOUND",
        "expires_at": LATER,
        "references_holds_checks": list(ALL_CHECKS),
        "restore_capability": {"mechanism": "content-isolation-record",
                               "entry": "isolation:log"},
        "receipt_store": "receipts/meeting/support",
    }
    base.update(overrides)
    return base


def relevance_request() -> dict:
    return {"kind": "relevance", "as_of": AS_OF, "bundle": bundle(),
            "policies": [policy()]}


def cleanup_request(level: str = "CONTENT_ISOLATION") -> dict:
    return {
        "kind": "cleanup", "level": level, "as_of": AS_OF,
        "objects": [{
            "object_ref": "rec:audio_0001", "object_type": "recording",
            "scope": {"chat_ref": CHAT, "tenant_ref": TENANT},
            "ownership": "OWNED", "permission_confirmed": True,
            "revision": "r7", "planned_revision": "r7", "holds": [], "references": [],
            "human_unique_edits": False, "rebuildable": True,
            "observed": {"status": "NOT_ATTEMPTED"}}],
        "provider": {"recycle_bin": True, "restore": True},
        "retry_budget": 0, "policy": policy(level),
    }


# ---------------------------------------------------------------------------
# the exit codes are the kernels' own
# ---------------------------------------------------------------------------


class TheDecisionIsNotAFailure(unittest.TestCase):
    """Every decision the resolver can reach, through the process, exits 0.

    The cases are chosen so that at least one of them *looks* like a failure to
    a reader who equates non-zero with bad and zero with done: `PROPOSAL_REQUIRED`
    is the answer that says "no rule covers this and I will not guess", and a verb
    that exits 2 on it teaches its callers to read the most honest answer the
    kernel gives as an error.
    """

    CASES = {
        "a canonical object already in its place": (placement("wiki", "knowledge",
                                                              project="litellm"), "KEEP"),
        "an object on the wrong surface": (placement("mydocs", "knowledge",
                                                     project="litellm"), "MOVE"),
        "canonical but missing its index entry": (
            {**placement("wiki", "deliverable", project="litellm"),
             "observed": {"indexed": False}}, "INDEX"),
        "a role the policy declines to choose a canon for": (
            placement("mydocs", "source", project="litellm"), "PROPOSAL_REQUIRED"),
        "a role nobody has classified": (placement("mydocs", "unclassified"),
                                        "PROPOSAL_REQUIRED"),
    }

    def test_each_reachable_decision_exits_zero(self):
        for name, (document, expected) in self.CASES.items():
            with self.subTest(case=name):
                proc = cli("space", "resolve", "--request", str(request(document)),
                           "--json")
                self.assertEqual(proc.returncode, 0, proc.stderr.decode())
                self.assertEqual(json.loads(proc.stdout.decode())["decision"], expected)

    def test_the_reachable_set_is_what_the_cli_claims(self):
        """The five decisions above are exactly the ones `resolve()` can return.

        Asserted against the kernel rather than against the fixtures: if a sixth
        decision becomes reachable, the case list above is incomplete and this
        fails, which is the only way a list of "every case" stays true.
        """
        source = (ROOT / "space" / "resolver.py").read_text(encoding="utf-8")
        body = source.split("def resolve(", 1)[1].split("\ndef ", 1)[0]
        reached = {name for name in resolver.DECISIONS if f'"{name}"' in body}
        self.assertEqual(reached, {"KEEP", "MOVE", "INDEX", "PROPOSAL_REQUIRED"},
                         "resolve() gained or lost a decision; the case list above "
                         "is a claim about every case, so it has to move with it")

    def test_a_refusal_is_a_determination_and_exits_zero(self):
        """`REFUSAL` is the cleanup kernel declining to plan. It is an answer.

        The kernel's own docstring says so, and the case is load-bearing: the
        refusal here is the shipped `support:marek-open-mic` exception being
        UNBOUND, which is a property of the declaration working, not a fault.
        """
        proc = cli("space", "meeting", "--request",
                   str(request(cleanup_request("IRREVERSIBLE_DELETE"))), "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        record = json.loads(proc.stdout.decode())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertEqual(record["axes"]["deletability"], "BLOCKED")


class ARequestThatCannotBeReadIsNotADecision(unittest.TestCase):
    """2 is for the asking, not for the object. A caller's typo is not an answer."""

    def test_a_malformed_request_exits_two_and_prints_no_decision(self):
        proc = cli("space", "resolve", "--request",
                   str(request(placement("dropbox", "report"))))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown surface", proc.stderr.decode())
        self.assertEqual(proc.stdout.decode(), "",
                         "a refusal printed on stdout reads as output; the caller "
                         "that pipes stdout into a parser gets an empty parse "
                         "instead of a reason")

    def test_an_unreadable_request_exits_two(self):
        proc = cli("space", "resolve", "--request", "/nonexistent/request.json")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot read", proc.stderr.decode())

    def test_a_request_that_is_not_json_exits_two(self):
        directory = Path(tempfile.mkdtemp(prefix="space-cli-"))
        KEEP.append(directory)
        path = directory / "request.json"
        path.write_text("{not json", encoding="utf-8")
        proc = cli("space", "resolve", "--request", str(path))
        self.assertEqual(proc.returncode, 2)


# ---------------------------------------------------------------------------
# the record is the kernel's
# ---------------------------------------------------------------------------


class TheCliAddsNoSchema(unittest.TestCase):
    """`--json` is the kernel's record. A second shape would be a second authority."""

    def test_the_placement_record_is_byte_for_byte_the_kernels(self):
        document = {**placement("mydocs", "deliverable", project="litellm",
                                item="LIT-0101"),
                    "observed": {"locator": "feishu://mydocs/doc_abc"},
                    "policy": {"target_template": "feishu://wiki/{project}/{semantic_role}"}}
        proc = cli("space", "resolve", "--request", str(request(document)), "--json")
        expected = resolver.resolve(
            surface="mydocs", object_type="document", semantic_role="deliverable",
            context={"project": "litellm", "item": "LIT-0101"},
            observed={"locator": "feishu://mydocs/doc_abc"},
            policy={"target_template": "feishu://wiki/{project}/{semantic_role}"})
        self.assertEqual(json.loads(proc.stdout.decode()), expected)

    def test_the_relevance_record_is_the_kernels_too(self):
        proc = cli("space", "meeting", "--request", str(request(relevance_request())),
                   "--json")
        record = json.loads(proc.stdout.decode())
        self.assertEqual(record["schema"], meeting.SCHEMA)
        # The property section 7 rests on, asserted through the process: a
        # relevance verdict cannot reach a deletion because there is no field
        # for one to reach.
        self.assertIsNone(record["cleanup"])

    def test_the_cleanup_record_is_the_kernels_too(self):
        proc = cli("space", "meeting", "--request", str(request(cleanup_request())),
                   "--json")
        record = json.loads(proc.stdout.decode())
        self.assertEqual(record["schema"], meeting.CLEANUP_SCHEMA)
        self.assertEqual(record["decision"], "PLAN")


# ---------------------------------------------------------------------------
# the presentation cannot be misread
# ---------------------------------------------------------------------------


class AProposalIsNotAPermission(unittest.TestCase):
    def test_a_move_prints_its_approval(self):
        proc = cli("space", "resolve", "--request",
                   str(request(placement("mydocs", "knowledge", project="litellm"))))
        text = proc.stdout.decode()
        self.assertIn("MOVE", text)
        self.assertIn("proposal-only", text)
        self.assertIn("nothing here writes", text)

    def test_a_keep_does_not_claim_a_proposal(self):
        """The other direction, so the line is a reading and not decoration.

        `none-required` is the resolver's own answer for a same-surface decision,
        and a renderer that printed the warning unconditionally would pass the
        test above while telling every KEEP that it needed approval.
        """
        proc = cli("space", "resolve", "--request",
                   str(request(placement("wiki", "knowledge", project="litellm"))))
        text = proc.stdout.decode()
        self.assertIn("none-required", text)
        self.assertNotIn("nothing here writes", text)

    def test_no_locator_is_reported_as_a_property_of_the_policy(self):
        """The kernel never invents a locator; the CLI must not imply it forgot.

        "locator (none)" and "locator (none — no template in the policy)" are the
        same fact told two ways, and only the second one is true: the caller's
        policy carried no template, so there was nothing to render. The kernel
        refuses to synthesise one from a pattern of its own.
        """
        proc = cli("space", "resolve", "--request",
                   str(request(placement("mydocs", "knowledge", project="litellm"))))
        self.assertIn("no template in the policy", proc.stdout.decode())

    def test_an_undecided_placement_does_not_print_a_dash_as_a_surface(self):
        proc = cli("space", "resolve", "--request",
                   str(request(placement("mydocs", "source", project="litellm"))))
        text = proc.stdout.decode()
        self.assertIn("(undecided)", text)


class TheRelevanceRecordSaysWhatItsNullMeans(unittest.TestCase):
    def test_null_cleanup_is_not_rendered_as_nothing_to_clean_up(self):
        """The one field that keeps relevance from reaching a deletion.

        Read as "nothing to clean up", `cleanup: null` inverts section 7: the
        kernel does not produce a cleanup level at all, and a plan is asked for
        as a separate question with a separate input set.
        """
        proc = cli("space", "meeting", "--request", str(request(relevance_request())))
        text = proc.stdout.decode()
        self.assertIn("no cleanup level", text)
        self.assertIn("asked for separately", text)
        self.assertNotIn("nothing to clean up", text)

    def test_a_plan_says_it_is_not_an_execution(self):
        proc = cli("space", "meeting", "--request", str(request(cleanup_request())))
        text = proc.stdout.decode()
        self.assertIn("PLAN", text)
        self.assertIn("not an execution", text)


# ---------------------------------------------------------------------------
# no clock
# ---------------------------------------------------------------------------


class TheCliCannotSupplyATime(unittest.TestCase):
    def test_neither_verb_offers_an_as_of(self):
        for verb in ("resolve", "meeting"):
            with self.subTest(verb=verb):
                proc = cli("space", verb, "--help")
                self.assertNotIn("--as-of", proc.stdout.decode(),
                                 "the meeting kernel evaluates expiry against an "
                                 "explicit as_of and fails closed without one; a flag "
                                 "here is where a shell's clock enters, and a check "
                                 "whose answer depends on when it ran cannot be "
                                 "compared against a recorded one")

    def test_the_kernel_still_refuses_a_removal_with_no_as_of(self):
        """The property the absent flag protects, asserted on the kernel.

        Only the levels that take something away fail closed here — a retention
        window's expiry is not a gate on `CONTENT_ISOLATION`, which removes no
        material, and asserting otherwise would be asserting a rule the kernel
        does not have. `REBUILDABLE_ARTIFACT_CLEANUP` is one of the levels where
        it is a gate: without an `as_of` the expiry is unevaluated, and an
        unevaluated expiry that let the operation through would be a plan
        resting on a check that never ran.
        """
        document = cleanup_request("REBUILDABLE_ARTIFACT_CLEANUP")
        document.pop("as_of")
        proc = cli("space", "meeting", "--request", str(request(document)), "--json")
        record = json.loads(proc.stdout.decode())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("as_of" in reason for reason in record["reason"]),
                        record["reason"])


# ---------------------------------------------------------------------------
# a thin surface
# ---------------------------------------------------------------------------


def _function_source(name: str) -> str:
    """One top-level function's body, from `runtime/cli.py`.

    Sliced rather than parsed: `ast` would be the thorough way, and this file's
    point is a property of the source text — that a call is not written there —
    so a text slice is the form that fails when the call is added, which is what
    a control has to do.
    """
    source = (RUNTIME / "cli.py").read_text(encoding="utf-8")
    body = source.split(f"\ndef {name}(", 1)[1]
    return body.split("\ndef ", 1)[0]


class TheCliShellsOutToNothing(unittest.TestCase):
    def test_it_does_not_call_either_kernels_main(self):
        """`main()` parses argv and prints; both are already done here.

        Calling it would also mean the kernels' own exit-code line —
        `2 if record["decision"] == "REJECTED"` — decides the CLI's status, and
        nothing in either kernel can return `REJECTED`. A status nothing can be
        observed emitting is worse than no status, because it reads as a check.
        """
        for name in ("cmd_space_resolve", "cmd_space_meeting"):
            with self.subTest(function=name):
                self.assertNotIn("mod.main(", _function_source(name))
                self.assertIn("mod.decide(", _function_source(name))

    def test_both_kernels_are_loaded_from_the_tree_not_imported(self):
        """The `_load_module_from` form, because `space/` is not a package.

        An `import space.resolver` would need `space/__init__.py`, which does not
        exist and should not be added for this: the loader form is how every
        other kernel in this tree is reached, and it keeps `sys.path` free of a
        directory whose name would shadow any package called `space`.
        """
        source = (RUNTIME / "cli.py").read_text(encoding="utf-8")
        self.assertIn('REPO_ROOT / "space" / "resolver.py"', source)
        self.assertIn('REPO_ROOT / "space" / "meeting_relevance.py"', source)
        self.assertFalse((ROOT / "space" / "__init__.py").exists(),
                         "space/ is not a package; the loader form is the idiom")

    def test_the_kind_override_does_not_mutate_the_callers_document(self):
        """Re-asked, not rewritten: the caller's document is theirs."""
        document = cleanup_request()
        path = request(document)
        cli("space", "meeting", "--request", str(path), "--kind", "relevance")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), document)

    def test_the_kind_override_selects_the_other_question(self):
        """And it is the other question, so asking a cleanup document for
        relevance is refused for the right reason: it has no bundle."""
        proc = cli("space", "meeting", "--request", str(request(cleanup_request())),
                   "--kind", "relevance")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("bundle", proc.stderr.decode())


if __name__ == "__main__":
    unittest.main()
