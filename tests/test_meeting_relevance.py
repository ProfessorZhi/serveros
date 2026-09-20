"""Tests for the Meeting relevance / retention kernel and its vocabulary.

Hermetic: nothing here reads the network, a database, a clock, or this machine's
checkouts. The vocabulary shipped in the repository is the fixture, because the
vocabulary IS the contract — a test that used a synthetic one would prove the
kernel works on data nobody ships. The one exception is `bound_vocabulary()`,
explained where it is defined.

Four of these tests carry most of the weight, because they are structural rather
than behavioural:

  * `VocabularyContractTest` checks the vocabulary against itself, so a decision
    the kernel can return but the vocabulary does not declare — or a rule that
    can never fire — fails the build instead of sitting in the file looking
    authoritative.
  * `KernelPurityTest` reads the kernel's AST: what it may import, what its code
    literals may name, and the one function allowed to read `action_when_bound`.
  * `IndependentAxesTest` asserts that relevance cannot reach a deletion, by
    checking every field of every record the relevant entry point can return.
  * `SignalsAreNotJudgementsTest` asserts that no branch of the kernel reads a
    signal, by requiring the record to be identical when all six signals are set
    against the bundle and when they are set for it.

Where a case below needs a behaviour that the fail-closed default forbids, it
says so in the test's own docstring rather than quietly widening a selector.
"""
import ast
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPACE = ROOT / "space"
VOCABULARY_PATH = SPACE / "meeting-vocabulary.json"
KERNEL_PATH = SPACE / "meeting_relevance.py"
SCHEMA_PATH = SPACE / "meeting-policy.schema.yaml"

_spec = importlib.util.spec_from_file_location("serveros_meeting_relevance", KERNEL_PATH)
meeting = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(meeting)

# Synthetic identifiers throughout. No real tenant, chat, meeting or person
# appears anywhere in this file, and none appears in the vocabulary.
TENANT = "tenant:t_synthetic"
SUPPORT_CHAT = "chat:oc_support_0001"
OTHER_CHAT = "chat:oc_support_0002"
UNRELATED_CHAT = "chat:oc_support_0003"
MEETING = "meeting:m_support_0001"
OTHER_MEETING = "meeting:m_support_0002"
AS_OF = "2026-09-20T00:00:00Z"
LATER = "2027-01-01T00:00:00Z"
EARLIER = "2026-01-01T00:00:00Z"

CONTENT_ISOLATION = "CONTENT_ISOLATION"
REBUILDABLE = "REBUILDABLE_ARTIFACT_CLEANUP"
DISPOSAL = "CLOUD_RECOVERABLE_DISPOSAL"
IRREVERSIBLE = "IRREVERSIBLE_DELETE"

LEVEL_OPERATION = {
    CONTENT_ISOLATION: CONTENT_ISOLATION,
    REBUILDABLE: REBUILDABLE,
    DISPOSAL: DISPOSAL,
    IRREVERSIBLE: IRREVERSIBLE,
}
LEVEL_DELETE_MODE = {
    CONTENT_ISOLATION: "NO_DELETE",
    REBUILDABLE: "NO_DELETE",
    DISPOSAL: "RECYCLE_BIN",
    IRREVERSIBLE: "PERMANENT",
}

REQUIRED_FIELDS = [
    "policy_revision", "source_selector", "operation", "scope", "owner_constraint",
    "protected_exceptions", "relevance_rule", "retention_window",
    "allowed_content_depth", "delete_mode", "approval", "expires_at",
    "references_holds_checks", "restore_capability", "receipt_store",
]

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

SIGNALS = ["audio_duration_s", "user_muted", "language", "title", "participants",
           "auto_summary"]

DAMNING_SIGNALS = {
    "audio_duration_s": 3.5,
    "user_muted": True,
    "language": "pl",
    "title": "Marek open mic",
    "participants": ["Marek", "unknown"],
    "auto_summary": "no business value",
}
FLATTERING_SIGNALS = {
    "audio_duration_s": 7200,
    "user_muted": False,
    "language": "en",
    "title": "Support escalation review",
    "participants": ["agent:me", "user:counterparty"],
    "auto_summary": "a live support escalation with a decision",
}

SENTINEL = "SENTINEL-TRANSCRIPT-TEXT-MUST-NOT-BE-COPIED"


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def vocabulary() -> dict:
    return json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))


def bound_vocabulary() -> dict:
    """The shipped vocabulary, with the user's exception BOUND to a synthetic id.

    `support:marek-open-mic` ships UNBOUND, and while it is UNBOUND the kernel
    refuses every plan that removes material — which is requirement 4 working,
    not a defect. The section 10 cases that need such a plan to be reachable at
    all (403, partial failure, restore and reindex, candidate regrowth, a
    revision that changed) therefore run against this fixture, in which a stable
    identifier for that group has been obtained. The shipped declaration stays
    UNBOUND: nothing here weakens it, and `MarekExceptionTest` asserts that it
    stays UNBOUND and that its `action_when_bound` is unreachable.
    """
    vocab = vocabulary()
    declaration = vocab["protected_exceptions"][0]
    declaration["binding_state"] = "BOUND"
    declaration["tenant_ref"] = TENANT
    declaration["meeting_selector"] = {"kind": "meeting_ref", "id": MEETING}
    return vocab


def policy(level: str = CONTENT_ISOLATION, **overrides) -> dict:
    """A complete, executable policy. Every one of the fifteen fields is filled.

    An undeclared level is passed through as the operation and gets a harmless
    delete_mode, so a test can hand the kernel a level the vocabulary does not
    declare and reach its own rejection rather than a KeyError here.
    """
    base = {
        "policy_revision": "rev-0007",
        "source_selector": {"kind": "chat_ref", "id": SUPPORT_CHAT},
        "operation": LEVEL_OPERATION.get(level, level),
        "scope": {"chat_ref": SUPPORT_CHAT},
        "owner_constraint": "SELF_ONLY",
        "protected_exceptions": ["support:marek-open-mic"],
        "relevance_rule": "confirmed-muted-support-source",
        "retention_window": {"approved_policy_ref": "retention:meeting-support-v1",
                             "days": 30},
        "allowed_content_depth": "TRANSCRIPT",
        "delete_mode": LEVEL_DELETE_MODE.get(level, "NO_DELETE"),
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


def bundle(segments=None, references=None, **overrides) -> dict:
    base = {
        "bundle_ref": "bundle:mb_0001",
        "scope": {"chat_ref": SUPPORT_CHAT, "tenant_ref": TENANT},
        "signals": {},
        "content": {"transcript_segments": segments or []},
        "work_relation": {"references": references or []},
        "dependants": [],
        "ownership": "OWNED",
        "hold": "NONE",
    }
    base.update(overrides)
    return base


def segment(start: float, end: float, tie=None, text=SENTINEL) -> dict:
    located = {
        "start_s": start,
        "end_s": end,
        "provenance": "transcript:v1",
        "text": text,
    }
    if tie is not None:
        located["tie"] = tie
    return located


SUPPORT_TIE = {"kind": "support_request", "ref": "msg:om_support_0001"}


def classify(document, policies=None, vocab=None, as_of=None, **kwargs) -> dict:
    if policies is None:
        policies = [policy()]
    return meeting.classify(bundle=document, policies=policies,
                            vocabulary=vocab if vocab is not None else vocabulary(),
                            as_of=as_of, **kwargs)


def person(object_ref="rec:audio_0001", object_type="recording", **overrides) -> dict:
    base = {
        "object_ref": object_ref,
        "object_type": object_type,
        "scope": {"chat_ref": SUPPORT_CHAT, "tenant_ref": TENANT},
        "ownership": "OWNED",
        "permission_confirmed": True,
        "revision": "r7",
        "planned_revision": "r7",
        "holds": [],
        "references": [],
        "human_unique_edits": False,
        "rebuildable": True,
        "observed": {"status": "NOT_ATTEMPTED"},
    }
    base.update(overrides)
    return base


def authorization(operation=IRREVERSIBLE, **overrides) -> dict:
    base = {
        "kind": "PER_OPERATION",
        "operation_ref": operation,
        "approved_by": "actor:human_0001",
        "expires_at": LATER,
    }
    base.update(overrides)
    return base


def cleanup(level=CONTENT_ISOLATION, objects=None, policy_overrides=None,
            vocab=None, **overrides) -> dict:
    request = {
        "kind": "cleanup",
        "level": level,
        "as_of": AS_OF,
        "objects": objects if objects is not None else [person()],
        "provider": {"recycle_bin": True, "restore": True},
        "retry_budget": 0,
        "policy": policy(level, **(policy_overrides or {})),
    }
    request.update(overrides)
    return meeting.plan_cleanup(request, vocabulary=vocab if vocab is not None
                                else vocabulary())


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(KERNEL_PATH), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def dump(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------
# the vocabulary is a closed, self-consistent set
# --------------------------------------------------------------------------

class VocabularyContractTest(unittest.TestCase):

    def setUp(self):
        self.vocab = vocabulary()
        self.dimensions = self.vocab["dimensions"]

    def test_schema_is_declared(self):
        self.assertEqual(self.vocab["schema"], meeting.VOCABULARY_SCHEMA)

    def test_the_shipped_vocabulary_loads(self):
        self.assertEqual(meeting.load_vocabulary()["schema"], meeting.VOCABULARY_SCHEMA)

    def test_every_decision_the_kernel_can_return_is_declared(self):
        """A decision the vocabulary does not declare is a value nobody agreed on."""
        declared = set(self.dimensions["decision"]["values"])
        self.assertEqual(set(meeting.DECISIONS), declared)

    def test_required_policy_fields_are_the_fifteen_of_the_design(self):
        declared = self.vocab["policy_required_fields"]["values"]
        self.assertEqual(declared, REQUIRED_FIELDS)
        self.assertEqual(len(declared), len(set(declared)))

    def test_the_four_cleanup_levels_are_a_closed_set(self):
        self.assertEqual(list(self.dimensions["cleanup_level"]["values"]),
                         list(meeting.CLEANUP_LEVELS))

    def test_the_level_ladder_matches_the_vocabulary(self):
        ladder = self.dimensions["delete_mode_for_level"]
        operations = self.dimensions["operation_for_level"]
        for level, mode in meeting.LEVEL_DELETE_MODE.items():
            with self.subTest(level=level):
                self.assertEqual(ladder[level], mode,
                                 f"{level} declares {ladder[level]!r} in the vocabulary and "
                                 f"{mode!r} in the kernel")
                self.assertIn(mode, self.dimensions["delete_mode"]["values"])
                self.assertEqual(operations[level], meeting.LEVEL_OPERATION[level])
                self.assertIn(mode, ladder.values())
        self.assertEqual(set(ladder) - {"_comment"}, set(meeting.CLEANUP_LEVELS))

    def test_only_the_three_removal_levels_need_an_approved_retention(self):
        self.assertEqual(list(self.vocab["retention_levels"]["values"]),
                         list(meeting.REMOVAL_LEVELS))
        self.assertNotIn(CONTENT_ISOLATION, self.vocab["retention_levels"]["values"])

    def test_every_rule_declares_the_keys_the_kernel_reads(self):
        rules = self.vocab["relevance_rules"]["values"]
        self.assertTrue(rules)
        for name, rule in rules.items():
            with self.subTest(rule=name):
                for key in ("source_confirmed", "audio_fetch",
                            "incidental_audio_to_understanding",
                            "incidental_audio_to_board",
                            "incidental_audio_to_report", "evidence_paths"):
                    self.assertIn(key, rule)
                self.assertIn(rule["audio_fetch"], ("ALLOW", "DENY"))
                self.assertIn(rule["incidental_audio_to_understanding"],
                              ("EXCLUDE", "KEEP_IF_RELEVANT", "REVIEW"))
                for path in rule["evidence_paths"]:
                    self.assertIn(path, self.dimensions["evidence_path"]["values"])

    def test_no_unconfirmed_rule_can_exclude_anything(self):
        """The fail-closed direction for an unknown source is review, not exclusion."""
        for name, rule in self.vocab["relevance_rules"]["values"].items():
            if rule["source_confirmed"]:
                continue
            with self.subTest(rule=name):
                self.assertEqual(rule["audio_fetch"], "DENY")
                for key in ("incidental_audio_to_understanding",
                            "incidental_audio_to_board", "incidental_audio_to_report"):
                    self.assertNotEqual(rule[key], "EXCLUDE")

    def test_an_unmatched_bundle_can_only_be_reviewed(self):
        allowed = self.vocab["review_actions"]["values"]
        self.assertEqual(allowed, list(meeting.DECISIONS)[2:4])
        self.assertIn(self.vocab["unmatched_default"], allowed)
        for value in allowed:
            self.assertIn(value, self.dimensions["decision"]["values"])
            self.assertNotIn("EXCLUDE", value)
            self.assertNotIn("PLAN", value)

    def test_the_stage_and_state_sets_are_closed(self):
        self.assertEqual(self.vocab["dimensions"]["stage"]["values"], list(meeting.STAGES))
        self.assertEqual(set(self.vocab["dimensions"]["stage_state"]["values"]),
                         {"PRODUCED", "NO_INPUT", "NO_MATCH", "AMBIGUOUS", "NOT_PERMITTED"})

    def test_the_six_signals_are_the_design_s_own_list(self):
        self.assertEqual(self.vocab["signals"]["values"], SIGNALS)

    def test_the_marek_declaration_is_present_and_unbound(self):
        declarations = {d["ref"]: d for d in self.vocab["protected_exceptions"]
                        if isinstance(d, dict) and "ref" in d}
        declaration = declarations["support:marek-open-mic"]
        self.assertEqual(declaration["binding_state"], "UNBOUND")
        for key in ("tenant_ref", "chat_ref", "meeting_selector"):
            self.assertIsNone(declaration[key],
                              f"{key} must be null while the declaration is UNBOUND: an "
                              "unbound declaration that carries a selector is how a guess "
                              "gets made on the user's behalf")
        self.assertFalse(declaration["title_or_name_matching_allowed"])
        self.assertEqual(declaration["action_when_bound"], "REVIEW_RELEVANT_SEGMENTS")
        self.assertEqual(declaration["action_when_unbound"], "REVIEW_NO_DELETE")
        self.assertEqual(declaration["action_when_unbound"],
                         self.vocab["unmatched_default"])

    def test_the_shipped_template_is_disabled_and_authorises_nothing(self):
        """CONTRACT-hwz-ops section 4, as data. A template is not consent."""
        template = self.vocab["templates"][0]
        self.assertEqual(template["status"], "TEMPLATE_NOT_INSTALLED")
        self.assertFalse(template["automation_enabled"])
        self.assertEqual(template["binding_state"], "UNBOUND")
        self.assertEqual(template["source_selectors"], [])
        self.assertIsNone(template["retention"]["approved_policy_ref"])
        self.assertIsNone(template["retention"]["days"])
        self.assertEqual(template["retention"]["permanent_delete"],
                         "PER_OPERATION_APPROVAL_REQUIRED")
        self.assertEqual(sorted(template["eligibility_requires"]), sorted(ALL_CHECKS))

    def test_the_template_s_own_retention_cannot_run_any_level(self):
        """A policy built from the template's own retention values is refused."""
        for level in (REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                record = cleanup(level=level,
                                 policy_overrides={"retention_window": {
                                     "approved_policy_ref": None, "days": None}})
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("retention_window.days" in r
                                    for r in record["reason"]), record["reason"])

    def test_the_vocabulary_carries_no_observed_state(self):
        text = VOCABULARY_PATH.read_text(encoding="utf-8")
        for forbidden in ("sha256", "current.jsonl", "observed_at", "feishu://",
                          "access_token", "app_secret"):
            self.assertNotIn(forbidden, text,
                             f"vocabulary embeds observed state or a credential: {forbidden!r}")

    def test_every_dimension_value_the_kernel_reports_is_declared(self):
        for dimension in ("relevance", "decision", "ownership", "hold", "deletability",
                          "content_depth", "delete_mode", "binding_state",
                          "observed_status", "intent", "object_type", "owner_constraint",
                          "operation", "evidence_path", "cleanup_level"):
            with self.subTest(dimension=dimension):
                self.assertTrue(self.dimensions[dimension]["values"])


# --------------------------------------------------------------------------
# the kernel must not become a surface adapter, and must not read a signal
# --------------------------------------------------------------------------

class KernelPurityTest(unittest.TestCase):
    """These read the module's AST, not its text, so the docstring — which quotes
    the anti-pattern it exists to prevent — is not mistaken for the pattern."""

    BANNED_IMPORTS = {"socket", "urllib", "http", "requests", "subprocess", "sqlite3",
                      "os", "sys", "time", "datetime", "random", "shutil", "glob",
                      "lark", "larksuite", "github", "yaml"}
    BANNED_TOKENS = ("lark-cli", "lark_cli", "tenant_key", "app_token", "feishu://",
                     "wiki/", "drive/", "time.time", "datetime.now", "utcnow",
                     "random.", "date.today", "requests.", "http://", "https://")

    @classmethod
    def setUpClass(cls):
        cls.source = KERNEL_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
        } - {None}

    def code_constants(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in self.docstrings:
                    continue
                yield node.value

    def functions_mentioning(self, token):
        found = set()
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Constant) and token in str(inner.value):
                        found.add(node.name)
        return found

    def test_imports_only_the_standard_decision_surface(self):
        names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"argparse", "json", "pathlib"})
        self.assertEqual(names & self.BANNED_IMPORTS, set())

    def test_no_code_literal_names_a_cloud_client_or_a_tenant_path(self):
        for literal in self.code_constants():
            for banned in self.BANNED_TOKENS:
                self.assertNotIn(banned, literal,
                                 f"meeting_relevance.py code contains {banned!r} in "
                                 f"{literal!r}; that belongs to a surface adapter")

    def test_the_kernel_takes_no_clock(self):
        """Expiry is evaluated against an explicit `as_of`, never against now()."""
        for banned in ("time.time", "datetime.now", "utcnow", "random.", "date.today"):
            for literal in self.code_constants():
                self.assertNotIn(banned, literal)

    def test_classify_cannot_reach_the_cleanup_entry_point(self):
        """The relevance kernel has no call into a plan, so no verdict can act."""
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == "classify":
                called = {inner.id for inner in ast.walk(node)
                          if isinstance(inner, ast.Name)}
                called |= {inner.attr for inner in ast.walk(node)
                           if isinstance(inner, ast.Attribute)}
                self.assertNotIn("plan_cleanup", called)
                self.assertNotIn("decide", called)

    def test_action_when_bound_is_heard_in_exactly_three_places(self):
        """One of them produces a value; the other two only read it back.

        `exception_action` is the single producer, behind the single binding
        check. `_exception_view` echoes it only when the declaration applies, and
        `_exceptions` validates it as a declared decision. Nothing else in the
        module may look at it, which is what makes REVIEW_RELEVANT_SEGMENTS
        unreachable while the declaration is UNBOUND.
        """
        self.assertEqual(self.functions_mentioning("action_when_bound"),
                         {"exception_action", "_exception_view", "_exceptions"})

    def test_the_one_read_of_action_when_bound_is_inside_a_binding_check(self):
        """Structural, not promised: the read sits inside `if _is_bound(...)`."""
        function = next(node for node in ast.walk(self.tree)
                        if isinstance(node, ast.FunctionDef)
                        and node.name == "exception_action")

        def reads(node):
            return [inner for inner in ast.walk(node)
                    if isinstance(inner, ast.Subscript)
                    and isinstance(inner.slice, ast.Constant)
                    and inner.slice.value == "action_when_bound"]

        guarded = []
        for node in ast.walk(function):
            if isinstance(node, ast.If) and any(
                    isinstance(test, ast.Name) and test.id == "_is_bound"
                    for test in ast.walk(node.test)):
                guarded.extend(reads(node))

        self.assertEqual(len(reads(function)), 1,
                         "action_when_bound is read more than once in exception_action")
        self.assertEqual(len(guarded), 1,
                         "the read of action_when_bound is not inside the binding check")

    def test_the_records_only_echo_action_when_bound_for_an_applied_bound_declaration(self):
        vocab = bound_vocabulary()
        elsewhere = classify(bundle(scope={"chat_ref": SUPPORT_CHAT,
                                           "meeting_ref": OTHER_MEETING,
                                           "tenant_ref": TENANT}), vocab=vocab)
        view = elsewhere["protected_exceptions"][0]
        self.assertFalse(view["applied"])
        self.assertNotIn("action_when_bound", view)
        self.assertEqual(view["action"], "REVIEW_NO_DELETE")

    def test_no_title_or_language_or_participant_matcher_exists(self):
        for function in (node.name for node in ast.walk(self.tree)
                         if isinstance(node, ast.FunctionDef)):
            self.assertNotIn(function.lower(), {"match_title", "match_name", "match_language",
                                                "match_participant", "guess_group"})


# --------------------------------------------------------------------------
# section 7: the pipeline
# --------------------------------------------------------------------------

class PipelineTest(unittest.TestCase):
    """metadata/scope -> policy match -> bounded content -> verdict -> action."""

    def test_all_five_stages_are_reported_in_order(self):
        cases = {
            "no scope": bundle(scope={}),
            "no policy": bundle(),
            "matched muted source": bundle(),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                record = classify(document, policies=[] if name == "no policy" else None)
                self.assertEqual([s["stage"] for s in record["stages"]], list(meeting.STAGES))
                for stage in record["stages"]:
                    self.assertTrue(stage["detail"])
                    self.assertIn(stage["state"],
                                  ("PRODUCED", "NO_INPUT", "NO_MATCH", "AMBIGUOUS",
                                   "NOT_PERMITTED"))

    def test_a_stage_with_no_input_yields_unknown_and_never_a_default(self):
        record = classify(bundle(scope={}))
        self.assertEqual(record["relevance"], "UNKNOWN")
        self.assertEqual(record["stages"][0]["state"], "NO_INPUT")
        self.assertEqual(record["stages"][1]["state"], "NO_INPUT")
        self.assertEqual(record["stages"][2]["state"], "NO_INPUT")
        self.assertEqual(record["stages"][3]["state"], "NO_INPUT")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")

    def test_the_no_input_invariant_holds_over_every_shape_of_bundle(self):
        """A relevance stage that had no input can only produce UNKNOWN.

        The invariant is stated in one direction on purpose. A stage that DID have
        input need not produce a verdict — a confirmed rule, or a reference naming
        the bundle, may be what reached it — but a stage that had none never
        invents one. The case list is asserted to be non-vacuous, because an
        implication whose antecedent never holds would pass while proving nothing.
        """
        cases = {
            "no scope": (bundle(scope={}), None),
            "no policy": (bundle(), []),
            "no scope and no policy": (bundle(scope={}), []),
            "a name but no rule": (bundle(scope={"meeting_ref": MEETING}), []),
            "no depth permits reading": (
                bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)]),
                [policy(allowed_content_depth="METADATA_ONLY")]),
            "a rule over an empty bundle": (bundle(segments=[], references=[]), None),
            "a rule and a tie": (bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)]), None),
            "a reference and no content": (bundle(references=[{"ref": "SRV-0011"}]), None),
            "an unconfirmed rule": (
                bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)]),
                [policy(relevance_rule="unconfirmed-source")]),
        }
        reached_no_input = 0
        for name, (document, policies) in cases.items():
            with self.subTest(case=name):
                record = classify(document, policies=policies)
                if record["stages"][3]["state"] == "NO_INPUT":
                    reached_no_input += 1
                    self.assertEqual(record["relevance"], "UNKNOWN", record["reason"])
                    self.assertNotIn(record["decision"],
                                     ("INCLUDE_SEGMENTS", "EXCLUDE_FROM_WORK"),
                                     record["reason"])
                else:
                    self.assertTrue(record["stages"][3]["detail"],
                                    "a stage that produced a verdict must say what reached it")
        self.assertGreater(reached_no_input, 0,
                           "no case reached NO_INPUT, so the invariant was never exercised")

    def test_an_absence_of_content_is_never_the_reason_for_an_exclusion(self):
        """'We looked and there was nothing' is not a finding about the content.

        A bundle with no segment and no reference is still excluded — by the
        confirmed rule that governs its source, which the record names, and not by
        the emptiness of the bundle. The absence of content is not itself a
        reason for anything.
        """
        record = classify(bundle(segments=[], references=[]))
        self.assertEqual(record["stages"][2]["state"], "NO_INPUT")
        self.assertEqual(record["stages"][3]["state"], "PRODUCED")
        self.assertEqual(record["decision"], "EXCLUDE_FROM_WORK")
        self.assertTrue(any("relevance_rule=" in reason for reason in record["reason"]),
                        record["reason"])
        for reason in record["reason"]:
            self.assertNotIn("no segment", reason.lower(), reason)
            self.assertNotIn("empty", reason.lower(), reason)

    def test_a_tie_on_a_confirmed_open_source_is_relevant_with_a_stated_reason(self):
        """The verdict here comes from the content, not from the rule, and says so.

        A confirmed open source is not itself a verdict — it admits the audio
        within the policy's depth. The verdict comes from the tie on the segment,
        and the record carries the reason for it rather than a verdict with
        nothing behind it. (This combination had no reason at all until an audit
        over the kernel's input space found it; the test suite had never reached
        it, which is why the audit was worth running.)
        """
        record = classify(bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)]),
                          policies=[policy(relevance_rule="confirmed-open-source")])
        self.assertEqual(record["relevance"], "RELEVANT")
        self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")
        self.assertEqual(record["stages"][3]["state"], "PRODUCED")
        self.assertEqual(record["audio_fetch"], "ALLOW")
        self.assertTrue(record["reason"], "a verdict nobody can check is not a verdict")
        self.assertTrue(any("tie" in reason for reason in record["reason"]),
                        record["reason"])

    def test_an_unmatched_bundle_is_reviewed_never_excluded(self):
        record = classify(bundle(scope={"chat_ref": UNRELATED_CHAT}))
        self.assertEqual(record["stages"][1]["state"], "NO_MATCH")
        self.assertEqual(record["relevance"], "UNKNOWN")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")
        self.assertNotEqual(record["decision"], "EXCLUDE_FROM_WORK")

    def test_an_ambiguous_policy_set_is_a_question_for_a_human(self):
        record = classify(bundle(), policies=[policy(), policy(policy_revision="rev-0008")])
        self.assertEqual(record["stages"][1]["state"], "AMBIGUOUS")
        self.assertEqual(record["relevance"], "UNKNOWN")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")

    def test_a_named_source_with_a_tie_is_relevant(self):
        record = classify(bundle(segments=[
            segment(0, 30, tie=SUPPORT_TIE),
            segment(30, 60, tie={"kind": "support_request", "ref": "msg:om_support_0002"}),
        ]))
        self.assertEqual(record["relevance"], "RELEVANT")
        self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")
        self.assertEqual(record["stages"][3]["state"], "PRODUCED")

    def test_a_work_reference_is_a_tie_without_a_segment(self):
        record = classify(bundle(references=[{"ref": "SRV-0011", "kind": "item"}]))
        self.assertEqual(record["relevance"], "RELEVANT")
        self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")

    def test_an_open_source_with_no_tie_is_unknown_not_relevant(self):
        """Content was read and produced nothing to classify. That is UNKNOWN.

        Treating 'we found no tie' as RELEVANT would be the same error as
        treating it as IRRELEVANT, in the other direction.
        """
        record = classify(bundle(segments=[segment(0, 30), segment(30, 60)]),
                          policies=[policy(relevance_rule="confirmed-open-source")])
        self.assertEqual(record["relevance"], "UNKNOWN")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")

    def test_an_unconfirmed_source_excludes_nothing_and_is_denied_audio(self):
        record = classify(bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)]),
                          policies=[policy(relevance_rule="unconfirmed-source")])
        self.assertEqual(record["relevance"], "UNKNOWN")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")
        self.assertEqual(record["audio_fetch"], "DENY")
        self.assertFalse(record["exclusions"]["understanding"])
        self.assertFalse(record["exclusions"]["board"])
        self.assertFalse(record["exclusions"]["report"])

    def test_a_metadata_only_policy_reads_no_content(self):
        document = bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)])
        record = classify(document, policies=[policy(allowed_content_depth="METADATA_ONLY")])
        self.assertEqual(record["stages"][2]["state"], "NOT_PERMITTED")
        self.assertEqual(record["segments"], [])
        self.assertNotIn(SENTINEL, dump(record))

    def test_a_metadata_only_policy_still_sees_a_work_reference(self):
        record = classify(bundle(references=[{"ref": "SRV-0011"}]),
                          policies=[policy(allowed_content_depth="METADATA_ONLY")])
        self.assertEqual(record["relevance"], "RELEVANT")

    def test_a_policy_whose_selector_names_nothing_is_reported_inert(self):
        """A wildcard selector is not 'everything'. It is nothing, and it says so."""
        record = classify(bundle(),
                          policies=[policy(source_selector={"kind": "chat_ref", "id": "*"})])
        self.assertEqual(record["stages"][1]["state"], "NO_MATCH")
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")
        self.assertTrue(any("inert" in reason for reason in record["reason"]),
                        record["reason"])

    def test_a_selector_left_null_selects_nothing(self):
        """A null selector is refused as an absent field before it can select anything."""
        record = classify(bundle(), policies=[policy(source_selector=None)])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertNotEqual(record["decision"], "INCLUDE_SEGMENTS")
        self.assertTrue(any("source_selector" in reason for reason in record["reason"]),
                        record["reason"])

    def test_the_pipeline_is_deterministic(self):
        document = bundle(segments=[segment(0, 30, tie=SUPPORT_TIE)])
        self.assertEqual(classify(document), classify(document))


# --------------------------------------------------------------------------
# section 7: signals are recorded, and insufficient
# --------------------------------------------------------------------------

class SignalsAreNotJudgementsTest(unittest.TestCase):

    def test_the_six_signals_are_recorded_and_marked_insufficient(self):
        record = classify(bundle(signals=DAMNING_SIGNALS))
        for name in SIGNALS:
            with self.subTest(signal=name):
                self.assertIn(name, record["signals"])
        self.assertEqual(record["signals"]["sufficiency"], "RECORDED_INSUFFICIENT")
        self.assertIn("insufficient", record["signals"]["note"])

    def test_no_branch_reads_a_signal(self):
        """Identical bundles that differ only in their signals decide identically.

        Setting every signal to its most damning value — three seconds, muted, a
        Polish title naming the exception, an unnamed participant, a summary
        saying there is no value — must not move the record by one field.
        """
        damning = classify(bundle(signals=DAMNING_SIGNALS))
        flattering = classify(bundle(signals=FLATTERING_SIGNALS))
        for record in (damning, flattering):
            record.pop("signals")
        self.assertEqual(damning, flattering)

    def test_every_signal_at_its_worst_still_cannot_reach_a_deletion(self):
        """The whole point of section 7's 'no single signal proves no value'.

        Each signal is set to its own worst value on its own, against a bundle
        that is otherwise identical to the plain one, and the record must not
        move. The REVIEW family is the furthest any of them reaches.
        """
        baseline = classify(bundle())
        for name in SIGNALS:
            with self.subTest(signal=name):
                record = classify(bundle(signals={name: DAMNING_SIGNALS[name]}))
                self.assertEqual(record["decision"], baseline["decision"])
                self.assertEqual(record["relevance"], baseline["relevance"])
                self.assertIn(record["decision"],
                              {"REVIEW", "REVIEW_NO_DELETE", "REVIEW_PENDING",
                               "REVIEW_RELEVANT_SEGMENTS", "UNKNOWN", "EXCLUDE_FROM_WORK",
                               "INCLUDE_SEGMENTS"})
                self.assertIsNone(record["cleanup"])
                self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")

    def test_all_six_signals_at_their_worst_cannot_make_a_delete_candidate(self):
        """Even with a hold, a shared owner and a human-approved dependant."""
        document = bundle(segments=[segment(0, 3)], signals=DAMNING_SIGNALS,
                          ownership="SHARED", hold="LEGAL_HOLD",
                          dependants=[{"kind": "report", "ref": "report:published_0001",
                                       "human_approved": True}])
        record = classify(document)
        self.assertIsNone(record["cleanup"])
        self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")
        self.assertNotIn(record["decision"], ("PLAN", "PARTIAL"))

    def test_a_signal_cannot_change_the_verdict_of_a_matched_rule(self):
        plain = classify(bundle())
        damning = classify(bundle(signals=DAMNING_SIGNALS))
        self.assertEqual(plain["decision"], damning["decision"])
        self.assertEqual(plain["relevance"], damning["relevance"])

    def test_a_signal_cannot_change_the_verdict_of_an_unmatched_bundle(self):
        plain = classify(bundle(scope={"chat_ref": UNRELATED_CHAT}))
        damning = classify(bundle(scope={"chat_ref": UNRELATED_CHAT},
                                  signals=DAMNING_SIGNALS))
        self.assertEqual(plain["decision"], damning["decision"])


# --------------------------------------------------------------------------
# section 7: the axes are independent, and deletability is not derived
# --------------------------------------------------------------------------

class IndependentAxesTest(unittest.TestCase):

    def test_a_record_reports_all_five_axes(self):
        record = classify(bundle())
        self.assertEqual(set(record["axes"]),
                         {"relevance", "ownership", "retention", "hold", "deletability"})

    def test_relevance_is_never_an_eligibility_input_and_deletability_is_not_evaluated(self):
        cases = {
            "irrelevant": bundle(segments=[segment(0, 30)]),
            "relevant": bundle(references=[{"ref": "SRV-0011"}]),
            "unknown": bundle(),
            "unmatched": bundle(scope={"chat_ref": UNRELATED_CHAT}),
            "held": bundle(hold="LEGAL_HOLD"),
            "shared": bundle(ownership="SHARED"),
        }
        for name, document in cases.items():
            with self.subTest(case=name):
                record = classify(document)
                self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED",
                                 "relevance reached a deletability verdict")
                self.assertIsNone(record["cleanup"],
                                  "a relevance record carried a cleanup level; SPACE-AND-"
                                  "RETENTION 7 makes deletability an axis of its own, not a "
                                  "consequence of relevance")

    def test_an_irrelevant_bundle_is_never_a_delete_candidate(self):
        """Case: the confirmed muted recording is excluded, and nothing follows from it."""
        record = classify(bundle(segments=[segment(0, 30)]))
        self.assertEqual(record["relevance"], "IRRELEVANT")
        self.assertEqual(record["decision"], "EXCLUDE_FROM_WORK")
        self.assertIsNone(record["cleanup"])
        self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")
        self.assertNotIn("delete", dump(record["decision"]).lower())

    def test_ownership_and_hold_are_recorded_and_do_not_move_the_verdict(self):
        owned = classify(bundle(ownership="OWNED", hold="NONE"))
        shared = classify(bundle(ownership="SHARED", hold="LEGAL_HOLD"))
        self.assertEqual(shared["axes"]["ownership"], "SHARED")
        self.assertEqual(shared["axes"]["hold"], "LEGAL_HOLD")
        for key in ("decision", "relevance"):
            self.assertEqual(shared[key], owned[key],
                             f"{key} moved because ownership or hold changed")

    def test_a_relevance_verdict_supplied_to_a_plan_is_recorded_and_ignored(self):
        record = cleanup(CONTENT_ISOLATION, relevance={"relevance": "IRRELEVANT"})
        self.assertIn("relevance", record["eligibility_inputs_ignored"])
        self.assertEqual(record["decision"], "PLAN")
        self.assertTrue(any("not an eligibility input" in reason
                            for reason in record["reason"]), record["reason"])

    def test_an_irrelevant_verdict_cannot_stop_an_eligible_isolation_and_vice_versa(self):
        """Relevance is not an input in either direction."""
        with_irrelevant = cleanup(CONTENT_ISOLATION, relevance={"relevance": "IRRELEVANT"})
        with_relevant = cleanup(CONTENT_ISOLATION, relevance={"relevance": "RELEVANT"})
        self.assertEqual(with_irrelevant["decision"], with_relevant["decision"])
        self.assertEqual(with_irrelevant["objects"], with_relevant["objects"])


# --------------------------------------------------------------------------
# section 7 + CONTRACT 4: the Marek exception
# --------------------------------------------------------------------------

class MarekExceptionTest(unittest.TestCase):

    def test_the_kernel_has_the_declared_exception_unbound(self):
        self.assertEqual(
            meeting.exception_action(vocabulary()["protected_exceptions"][0]),
            "REVIEW_NO_DELETE")

    def test_action_when_bound_is_unreachable_while_unbound(self):
        declaration = vocabulary()["protected_exceptions"][0]
        view = meeting._exception_view(declaration, {"chat_ref": UNRELATED_CHAT})
        self.assertNotIn("action_when_bound", view)
        self.assertFalse(view["action_when_bound_reachable"])
        self.assertEqual(view["action"], "REVIEW_NO_DELETE")
        self.assertFalse(view["applied"])

    def test_a_title_a_language_and_a_participant_list_do_not_select_it(self):
        """Marek is one support group, not everyone named Marek and not Polish."""
        document = bundle(
            scope={"chat_ref": UNRELATED_CHAT},
            signals={"title": "Marek", "language": "pl",
                     "participants": ["Marek", "Marek's colleague"]},
        )
        record = classify(document)
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")
        self.assertEqual([e["ref"] for e in record["protected_exceptions"]],
                         ["support:marek-open-mic"])
        self.assertFalse(record["protected_exceptions"][0]["applied"])
        self.assertEqual(record["protected_exceptions"][0]["action"], "REVIEW_NO_DELETE")
        self.assertNotIn("action_when_bound", record["protected_exceptions"][0])

    def test_a_bundle_in_the_exception_s_own_group_is_still_not_admitted_while_unbound(self):
        """The kernel refuses to guess this group, so the bundle is reviewed.

        This is the honest consequence of the missing identifier: not an
        admission, and not a deletion.
        """
        document = bundle(scope={"chat_ref": SUPPORT_CHAT},
                          references=[{"ref": "SRV-0011"}])
        record = classify(document)
        self.assertFalse(record["protected_exceptions"][0]["applied"])
        self.assertNotEqual(record["decision"], "REVIEW_RELEVANT_SEGMENTS")

    def test_the_same_title_in_a_different_group_is_not_the_exception(self):
        """Case: 同名不同群. Only the stable identifier decides."""
        vocab = bound_vocabulary()
        with_selector = classify(
            bundle(scope={"chat_ref": SUPPORT_CHAT, "meeting_ref": MEETING,
                          "tenant_ref": TENANT}),
            vocab=vocab)
        self.assertTrue(with_selector["protected_exceptions"][0]["applied"])
        self.assertEqual(with_selector["decision"], "REVIEW_RELEVANT_SEGMENTS")
        self.assertEqual(with_selector["protected_exceptions"][0]["action"],
                         "REVIEW_RELEVANT_SEGMENTS")
        self.assertEqual(with_selector["protected_exceptions"][0]["action_when_bound"],
                         "REVIEW_RELEVANT_SEGMENTS")

        same_title_other_group = classify(
            bundle(scope={"chat_ref": SUPPORT_CHAT, "meeting_ref": OTHER_MEETING,
                          "tenant_ref": TENANT},
                   signals={"title": "Marek"}),
            vocab=vocab)
        self.assertFalse(same_title_other_group["protected_exceptions"][0]["applied"])
        self.assertEqual(same_title_other_group["protected_exceptions"][0]["action"],
                         "REVIEW_NO_DELETE")
        self.assertNotIn("action_when_bound",
                         same_title_other_group["protected_exceptions"][0])

    def test_a_wildcard_selector_applies_to_nothing(self):
        declaration = {
            "ref": "support:hand_built",
            "binding_state": "BOUND",
            "action_when_bound": "REVIEW_RELEVANT_SEGMENTS",
            "action_when_unbound": "REVIEW_NO_DELETE",
            "meeting_selector": {"kind": "chat_ref", "id": "*"},
        }
        for scope in ({}, {"chat_ref": SUPPORT_CHAT}, {"chat_ref": "*"}):
            with self.subTest(scope=scope):
                self.assertFalse(meeting.exception_applies(declaration, scope))

    def test_a_vocabulary_that_binds_the_exception_with_a_wildcard_is_rejected(self):
        vocab = bound_vocabulary()
        vocab["protected_exceptions"][0]["meeting_selector"] = {"kind": "chat_ref", "id": "*"}
        with self.assertRaises(meeting.Rejected):
            classify(bundle(), vocab=vocab)

    def test_a_bound_exception_without_a_selector_is_rejected(self):
        """'BOUND' with nothing to match is a claim the kernel will not act on."""
        vocab = bound_vocabulary()
        vocab["protected_exceptions"][0]["meeting_selector"] = None
        with self.assertRaises(meeting.Rejected):
            classify(bundle(), vocab=vocab)

    def test_an_unbound_declaration_carrying_a_selector_is_rejected(self):
        vocab = vocabulary()
        vocab["protected_exceptions"][0]["meeting_selector"] = {"kind": "chat_ref",
                                                                "id": SUPPORT_CHAT}
        with self.assertRaises(meeting.Rejected):
            classify(bundle(), vocab=vocab)

    def test_a_declaration_asking_for_name_matching_is_rejected(self):
        vocab = vocabulary()
        vocab["protected_exceptions"][0]["title_or_name_matching_allowed"] = True
        with self.assertRaises(meeting.Rejected):
            classify(bundle(), vocab=vocab)

    def test_an_unbound_destructive_policy_fails_closed(self):
        """The whole destructive path is refused while the exception is open."""
        for level in (DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                record = cleanup(level=level,
                                 objects=[person()],
                                 authorization=authorization()
                                 if level == IRREVERSIBLE else None)
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertEqual(record["objects"], [],
                                 "a refusal carried a plan")
                self.assertTrue(any("protected exceptions are not resolved" in r
                                    for r in record["reason"]), record["reason"])

    def test_a_protected_object_is_not_acted_on_at_any_level(self):
        """The exception protects what it names, at every rung of the ladder."""
        vocab = bound_vocabulary()
        protected = person(object_ref="rec:audio_0009",
                           scope={"chat_ref": SUPPORT_CHAT, "tenant_ref": TENANT,
                                  "meeting_ref": MEETING})
        for level in (CONTENT_ISOLATION, REBUILDABLE, DISPOSAL):
            with self.subTest(level=level):
                record = cleanup(level=level, objects=[protected], vocab=vocab)
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("protected exception" in reason
                                    for reason in record["reason"]), record["reason"])
                for entry in record["objects"]:
                    self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)

    def test_an_object_outside_the_exception_s_group_is_eligible(self):
        vocab = bound_vocabulary()
        record = cleanup(CONTENT_ISOLATION, objects=[person()], vocab=vocab)
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(record["objects"][0]["intent"], "ISOLATE")


# --------------------------------------------------------------------------
# section 7: muted does not mean irrelevant
# --------------------------------------------------------------------------

class MutedSupportSourceTest(unittest.TestCase):

    def test_a_confirmed_muted_recording_is_excluded_and_not_deleted(self):
        """Case 1: the confirmed muted irrelevant recording."""
        record = classify(bundle(segments=[segment(0, 1800)]))
        self.assertEqual(record["relevance"], "IRRELEVANT")
        self.assertEqual(record["decision"], "EXCLUDE_FROM_WORK")
        self.assertEqual(record["audio_fetch"], "DENY")
        self.assertTrue(record["exclusions"]["understanding"])
        self.assertTrue(record["exclusions"]["board"])
        self.assertTrue(record["exclusions"]["report"])
        self.assertTrue(record["exclusions"]["restore_entry"])
        self.assertIsNone(record["cleanup"])
        self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")

    def test_the_original_is_untouched(self):
        record = classify(bundle(segments=[segment(0, 1800)]))
        self.assertEqual(record["segments"], [])
        self.assertEqual(record["excluded_segments"]["count"], 1)
        self.assertTrue(record["excluded_segments"]["reason"])
        self.assertNotIn(SENTINEL, dump(record))

    def test_a_counterparty_s_question_keeps_its_segments(self):
        """Case 2: muted, but the counterparty raised a support question."""
        record = classify(bundle(segments=[
            segment(0, 120),
            segment(120, 300, tie=SUPPORT_TIE),
        ]))
        self.assertEqual(record["relevance"], "MIXED")
        self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")
        self.assertEqual(len(record["segments"]), 1)
        kept = record["segments"][0]
        self.assertEqual(kept["start_s"], 120)
        self.assertEqual(kept["end_s"], 300)
        self.assertEqual(kept["tie"], SUPPORT_TIE)
        self.assertEqual(kept["provenance"], "transcript:v1")
        self.assertEqual(record["excluded_segments"]["count"], 1)

    def test_excluding_the_audio_does_not_delete_the_work(self):
        """The non-audio evidence path is first-class, not an inference."""
        record = classify(bundle(segments=[segment(0, 1800)]))
        self.assertFalse(record["work_evidence"]["audio_required"])
        for path in ("SUPPORT_CHAT", "CONFIG_DIFF", "TEST_RESULT", "RESULT_CONFIRMATION"):
            self.assertIn(path, record["work_evidence"]["paths"])
        self.assertNotIn("MEETING_AUDIO", record["work_evidence"]["paths"])

    def test_the_support_question_is_still_relevant_without_any_audio(self):
        """Support work can be evidenced entirely off the audio."""
        record = classify(bundle(references=[{"ref": "SRV-0011", "kind": "item"}]))
        self.assertEqual(record["relevance"], "RELEVANT")
        self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")
        self.assertEqual(record["audio_fetch"], "DENY")
        self.assertFalse(record["work_evidence"]["audio_required"])

    def test_no_transcript_text_is_ever_copied_into_a_record(self):
        document = bundle(segments=[
            segment(0, 120),
            segment(120, 300, tie=SUPPORT_TIE),
        ])
        record = classify(document)
        self.assertNotIn(SENTINEL, dump(record))
        self.assertLessEqual(set(record["segments"][0]),
                             {"start_s", "end_s", "provenance", "tie"})


# --------------------------------------------------------------------------
# section 7: MIXED keeps the tied segments, and only those
# --------------------------------------------------------------------------

class MixedBundleTest(unittest.TestCase):

    def test_only_tied_segments_are_kept_and_they_keep_their_provenance(self):
        record = classify(bundle(segments=[
            segment(0, 30),
            segment(30, 60, tie=SUPPORT_TIE),
            segment(60, 90),
            segment(90, 120, tie={"kind": "decision", "ref": "dec:decision_0001"}),
        ]))
        self.assertEqual(record["relevance"], "MIXED")
        self.assertEqual([s["start_s"] for s in record["segments"]], [30, 90])
        self.assertEqual([s["tie"]["kind"] for s in record["segments"]],
                         ["support_request", "decision"])
        self.assertEqual(record["excluded_segments"]["count"], 2)

    def test_other_people_s_chatter_does_not_become_the_user_s_work(self):
        record = classify(bundle(segments=[segment(0, 30), segment(30, 60, tie=SUPPORT_TIE)]))
        self.assertEqual(len(record["segments"]), 1)
        self.assertNotIn(0, [s["start_s"] for s in record["segments"]])
        for forbidden in ("todos", "responsibilities", "completed_outcomes", "action_items"):
            self.assertNotIn(forbidden, dump({k: v for k, v in record.items()
                                              if k != "reason"}))

    def test_a_tie_must_be_declared_and_carries_no_text(self):
        record = classify(bundle(segments=[segment(30, 60, tie=SUPPORT_TIE)]))
        self.assertEqual(record["segments"][0]["tie"], SUPPORT_TIE)
        self.assertNotIn("text", record["segments"][0])

    def test_a_multilingual_mix_is_decided_by_the_tie_and_not_by_the_language(self):
        """Case 6: 多语言混合. Language is a recorded signal, and nothing more.

        The transcript carries two languages and the bundle does not say which
        one is the user's. The kernel has no language matcher, so the only thing
        that decides is which segments are tied to a support request or a
        decision — and it must decide the same way whichever language the
        signals name.
        """
        segments = [
            segment(0, 30, text=None),                       # unrelated, language A
            segment(30, 60, tie=SUPPORT_TIE, text=None),     # the request
            segment(60, 90, text=None),                      # unrelated, language B
        ]
        records = []
        for language in ("en", "pl", "en+pl", None):
            with self.subTest(language=language):
                record = classify(bundle(segments=segments,
                                         signals={"language": language}))
                self.assertEqual(record["relevance"], "MIXED")
                self.assertEqual(record["decision"], "INCLUDE_SEGMENTS")
                self.assertEqual([s["start_s"] for s in record["segments"]], [30])
                self.assertEqual(record["excluded_segments"]["count"], 2)
                records.append(record)
        stripped = []
        for record in records:
            record = dict(record)
            record.pop("signals")
            stripped.append(record)
        for record in stripped[1:]:
            self.assertEqual(record, stripped[0],
                             "the language moved the decision")


# --------------------------------------------------------------------------
# section 9: the required policy fields
# --------------------------------------------------------------------------

class RequiredPolicyFieldsTest(unittest.TestCase):

    def test_a_complete_policy_is_executable(self):
        record = classify(bundle(), policies=[policy()])
        self.assertNotEqual(record["decision"], "REFUSAL")

    def test_each_missing_field_is_refused_and_named(self):
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                broken = policy()
                del broken[field]
                record = classify(bundle(), policies=[broken])
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertEqual(record["relevance"], "UNKNOWN")
                self.assertTrue(any(field in reason for reason in record["reason"]),
                                f"{field} was not named in {record['reason']}")

    def test_each_null_field_is_refused_and_named(self):
        """A null is not a permissive default. It is an absent value."""
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                record = classify(bundle(), policies=[policy(**{field: None})])
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any(field in reason for reason in record["reason"]),
                                f"{field} was not named in {record['reason']}")

    def test_a_missing_field_is_a_refusal_and_not_a_rejected_request(self):
        """A refusal is a decision. REJECTED is reserved for a caller bug."""
        broken = policy()
        del broken["receipt_store"]
        record = classify(bundle(), policies=[broken])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertIn("REFUSAL", meeting.DECISIONS)
        self.assertTrue(record["reason"])

    def test_a_policy_with_no_usable_scope_is_refused(self):
        record = classify(bundle(), policies=[policy(scope={})])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("scope" in reason for reason in record["reason"]))

    def test_days_null_is_never_a_default_retention(self):
        for level in (CONTENT_ISOLATION, REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                record = cleanup(level=level, policy_overrides={
                    "retention_window": {"approved_policy_ref": "retention:x", "days": None}})
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("retention_window.days" in r for r in record["reason"]),
                                record["reason"])

    def test_no_approved_retention_is_a_proposal_for_the_removal_levels(self):
        for level in (REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                record = cleanup(level=level, policy_overrides={
                    "retention_window": {"approved_policy_ref": None, "days": 30}})
                self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
                self.assertTrue(any("approved_policy_ref" in r for r in record["reason"]),
                                record["reason"])

    def test_isolation_is_not_gated_on_a_retention_period_because_it_deletes_nothing(self):
        record = cleanup(CONTENT_ISOLATION, policy_overrides={
            "retention_window": {"approved_policy_ref": None, "days": 30}})
        self.assertEqual(record["decision"], "PLAN")

    def test_a_policy_that_cannot_name_its_source_may_not_act(self):
        record = cleanup(CONTENT_ISOLATION, policy_overrides={"source_selector": None})
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("source_selector" in r for r in record["reason"]),
                        record["reason"])

    def test_a_unit_of_days_that_is_not_a_positive_number_is_rejected(self):
        for days in (0, -1, "30", 3.5):
            with self.subTest(days=days):
                with self.assertRaises(meeting.Rejected):
                    classify(bundle(), policies=[policy(retention_window={
                        "approved_policy_ref": "retention:x", "days": days})])

    def test_the_policy_shape_is_one_shape_for_every_operation(self):
        """The same key set is required for a classification and for a disposal."""
        for level in (CONTENT_ISOLATION, REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                broken = policy(level)
                del broken["allowed_content_depth"]
                record = cleanup(level=level, policy=broken)
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("allowed_content_depth" in r for r in record["reason"]))


# --------------------------------------------------------------------------
# section 7, last bullet: human-approved material is never silently rewritten
# --------------------------------------------------------------------------

class HumanApprovedDependantsTest(unittest.TestCase):

    def test_a_published_report_that_used_an_excluded_source_is_marked_pending(self):
        """Case 9: 人工修订摘要."""
        document = bundle(
            segments=[segment(0, 1800)],
            dependants=[{"kind": "report", "ref": "report:published_0001",
                         "human_approved": True, "published": True}],
        )
        record = classify(document)
        self.assertEqual(record["decision"], "REVIEW_PENDING")
        self.assertEqual(record["delta"]["kind"], "SOURCE_EXCLUDED")
        self.assertEqual(record["delta"]["mutation"], "NONE")
        self.assertEqual([d["ref"] for d in record["delta"]["affected"]],
                         ["report:published_0001"])
        self.assertTrue(record["delta"]["note"])
        self.assertTrue(any("REVIEW_PENDING" in reason for reason in record["reason"]))

    def test_a_human_edited_draft_is_pending_even_if_it_was_never_published(self):
        document = bundle(segments=[segment(0, 1800)],
                          dependants=[{"kind": "claim", "ref": "claim:edited_0001",
                                       "human_approved": True}])
        record = classify(document)
        self.assertEqual(record["decision"], "REVIEW_PENDING")
        self.assertEqual(record["delta"]["mutation"], "NONE")

    def test_a_machine_draft_is_recomputable_and_the_exclusion_stands(self):
        document = bundle(segments=[segment(0, 1800)],
                          dependants=[{"kind": "draft", "ref": "draft:auto_0001"}])
        record = classify(document)
        self.assertEqual(record["decision"], "EXCLUDE_FROM_WORK")
        self.assertEqual(record["delta"]["mutation"], "REBUILD_ALLOWED")
        self.assertEqual(record["delta"]["affected"], [])
        self.assertEqual([d["ref"] for d in record["delta"]["recomputable"]],
                         ["draft:auto_0001"])

    def test_nothing_is_mutated_by_a_relevance_record(self):
        document = bundle(segments=[segment(0, 1800)],
                          dependants=[{"kind": "item", "ref": "SRV-0011",
                                       "human_approved": True}])
        record = classify(document)
        self.assertEqual(record["delta"]["mutation"], "NONE")
        self.assertNotIn("new_text", dump(record["delta"]))
        self.assertEqual(record["delta"]["affected"][0]["required_action"], "REVIEW")

    def test_an_approved_dependant_does_not_turn_a_review_into_an_exclusion(self):
        document = bundle(scope={"chat_ref": UNRELATED_CHAT},
                          dependants=[{"kind": "report", "ref": "report:published_0001",
                                       "human_approved": True}])
        record = classify(document)
        self.assertEqual(record["decision"], "REVIEW_NO_DELETE")


# --------------------------------------------------------------------------
# section 8: the four levels are a ladder
# --------------------------------------------------------------------------

class CleanupLadderTest(unittest.TestCase):

    def test_level_a_isolates_and_deletes_nothing(self):
        record = cleanup(CONTENT_ISOLATION)
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(record["level"], CONTENT_ISOLATION)
        self.assertEqual(record["objects"][0]["intent"], "ISOLATE")
        self.assertEqual(record["axes"]["deletability"], "ELIGIBLE")

    def test_level_b_clears_only_what_is_rebuildable(self):
        record = cleanup(REBUILDABLE, vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(record["objects"][0]["intent"], "CLEAN_REBUILDABLE")

    def test_level_b_refuses_an_object_the_producer_cannot_regenerate(self):
        blocked = cleanup(REBUILDABLE, objects=[person(rebuildable=False)],
                          vocab=bound_vocabulary())
        self.assertEqual(blocked["decision"], "REFUSAL")
        self.assertTrue(any("rebuildable" in reason for reason in blocked["reason"]),
                        blocked["reason"])
        for entry in blocked["objects"]:
            self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)

    def test_level_c_needs_a_recycle_bin_that_exists(self):
        record = cleanup(DISPOSAL, vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(record["objects"][0]["intent"], "DISPOSE")

    def test_level_c_without_a_verified_recycle_bin_is_unknown_not_a_delete(self):
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(),
                         provider={"recycle_bin": None, "restore": None})
        self.assertEqual(record["decision"], "UNKNOWN")
        self.assertEqual(record["objects"], [])

    def test_level_c_without_recycle_support_is_unsupported_not_a_permanent_delete(self):
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(),
                         provider={"recycle_bin": False, "restore": True})
        self.assertEqual(record["decision"], "UNSUPPORTED")
        self.assertEqual(record["objects"], [])
        self.assertNotEqual(record["decision"], "PLAN")

    def test_level_d_needs_this_operation_s_own_authorization(self):
        record = cleanup(IRREVERSIBLE, vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("IRREVERSIBLE_DELETE requires this operation" in r
                            for r in record["reason"]), record["reason"])

    def test_level_d_runs_only_with_a_per_operation_authorization(self):
        record = cleanup(IRREVERSIBLE, vocab=bound_vocabulary(),
                         authorization=authorization())
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(record["objects"][0]["intent"], "DISPOSE")

    def test_an_authorization_for_another_operation_does_not_authorise_this_one(self):
        record = cleanup(IRREVERSIBLE, vocab=bound_vocabulary(),
                         authorization=authorization(operation="CONTENT_ISOLATION"))
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("operation_ref" in r for r in record["reason"]), record["reason"])

    def test_a_blanket_authorization_is_not_a_per_operation_authorization(self):
        record = cleanup(IRREVERSIBLE, vocab=bound_vocabulary(),
                         authorization=authorization(kind="BLANKET"))
        self.assertEqual(record["decision"], "REFUSAL")

    def test_an_expired_authorization_is_refused(self):
        record = cleanup(IRREVERSIBLE, vocab=bound_vocabulary(),
                         authorization=authorization(expires_at=EARLIER))
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("expired" in r for r in record["reason"]), record["reason"])

    def test_no_classification_can_supply_an_authorization(self):
        """Level D is unreachable from relevance: classify has no such field."""
        record = classify(bundle(segments=[segment(0, 30)]))
        self.assertIsNone(record["cleanup"])
        self.assertNotIn("authorization", record)
        self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")

    def test_a_policy_written_for_one_operation_is_not_reusable_on_another(self):
        for level, other in ((CONTENT_ISOLATION, "REBUILDABLE_ARTIFACT_CLEANUP"),
                             (DISPOSAL, CONTENT_ISOLATION),
                             (IRREVERSIBLE, DISPOSAL)):
            with self.subTest(level=level, operation=other):
                record = cleanup(level, policy_overrides={"operation": other})
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("policy.operation" in r for r in record["reason"]),
                                record["reason"])

    def test_no_rung_of_the_ladder_implies_the_next(self):
        """A level A policy holding a PERMANENT delete mode is refused."""
        record = cleanup(CONTENT_ISOLATION, policy_overrides={"delete_mode": "PERMANENT"})
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("delete_mode" in r for r in record["reason"]), record["reason"])
        record = cleanup(REBUILDABLE, policy_overrides={"delete_mode": "RECYCLE_BIN"})
        self.assertEqual(record["decision"], "REFUSAL")

    def test_the_four_levels_are_a_closed_set(self):
        request = {"kind": "cleanup", "level": "SOFT_DELETE", "as_of": AS_OF,
                   "objects": [person()], "policy": policy()}
        with self.assertRaises(meeting.Rejected):
            meeting.plan_cleanup(request, vocabulary())

    def test_an_unbound_policy_is_refused_before_anything_is_planned(self):
        record = cleanup(REBUILDABLE, policy_overrides={"binding_state": "UNBOUND"})
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertEqual(record["objects"], [])
        self.assertTrue(any("binding_state" in reason for reason in record["reason"]),
                        record["reason"])

    def test_a_policy_that_names_no_owner_may_not_take_anything_away(self):
        for level in (REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                record = cleanup(level, vocab=bound_vocabulary(),
                                 policy_overrides={"owner_constraint": "NONE"})
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("owner_constraint" in r for r in record["reason"]))

    def test_a_policy_that_does_not_check_the_revision_or_the_owner_is_refused(self):
        for missing in ("CURRENT_OBJECT_REVISION", "CONFIRMED_OWNER_AND_PERMISSION"):
            with self.subTest(check=missing):
                record = cleanup(REBUILDABLE, policy_overrides={
                    "references_holds_checks": [c for c in ALL_CHECKS if c != missing]})
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any(missing in r for r in record["reason"]), record["reason"])

    def test_an_expired_policy_is_refused(self):
        """Case 16: the policy expired before the operation."""
        record = cleanup(REBUILDABLE, policy_overrides={"expires_at": EARLIER})
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("expired" in r for r in record["reason"]), record["reason"])

    def test_an_expiry_that_cannot_be_evaluated_fails_closed(self):
        for level, extra in ((REBUILDABLE, {}), (DISPOSAL, {"vocab": bound_vocabulary()})):
            with self.subTest(level=level):
                record = cleanup(level, as_of=None, **extra)
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("as_of" in r for r in record["reason"]), record["reason"])

    def test_an_unreadable_instant_is_a_caller_bug(self):
        for bad in ("2026-09-20", "2026-09-20T00:00:00", "not-a-time", "2026-13-20T00:00:00Z"):
            with self.subTest(as_of=bad):
                if bad == "2026-13-20T00:00:00Z":
                    # Shape-valid; the kernel validates the shape it was given and
                    # is not a calendar. It compares strings, which is only sound
                    # because the format is fixed-width and zero-padded.
                    self.assertEqual(meeting._iso8601(bad, "as_of"), bad)
                    continue
                with self.assertRaises(meeting.Rejected):
                    meeting._iso8601(bad, "as_of")

    def test_the_shipped_file_plans_only_the_level_that_removes_nothing(self):
        """Requirement 4's fail-closed consequence, asserted on the file that ships.

        `support:marek-open-mic` ships UNBOUND. Every level that removes material
        has to be able to show that its objects are not the exception's, and while
        the identifier is missing it cannot, so it returns a refusal rather than a
        plan. CONTENT_ISOLATION removes nothing and acts on nobody's object, so it
        still plans. Both answers are determinations: a refusal is not a crash, and
        it is not an UNSUPPORTED dressed up either. This is the mechanism holding
        while a real-world value is missing, which is the correct reading rather
        than a gap — the shipped declaration is never widened to make it pass.
        """
        declaration = vocabulary()["protected_exceptions"][0]
        self.assertEqual(declaration["binding_state"], "UNBOUND")
        self.assertIsNone(declaration["meeting_selector"])

        isolation = cleanup(CONTENT_ISOLATION, vocab=vocabulary())
        self.assertEqual(isolation["decision"], "PLAN", isolation["reason"])
        self.assertEqual(isolation["objects"][0]["intent"], "ISOLATE")

        for level in (REBUILDABLE, DISPOSAL, IRREVERSIBLE):
            with self.subTest(level=level):
                request = {
                    "kind": "cleanup",
                    "level": level,
                    "as_of": AS_OF,
                    "objects": [person()],
                    "provider": {"recycle_bin": True, "restore": True},
                    "retry_budget": 1,
                    # Supplied so the refusal can only be about the exception: for
                    # level D nothing else in this request is missing either.
                    "authorization": authorization(operation=LEVEL_OPERATION[level]),
                    "policy": policy(level),
                }
                record = meeting.plan_cleanup(request, vocabulary())
                self.assertEqual(record["decision"], "REFUSAL", record["reason"])
                self.assertEqual(record["objects"], [],
                                 "a refusal plans nothing, not even a blocked object")
                self.assertFalse(record["partial"],
                                 "a refusal is not a partial outcome")
                self.assertTrue(any("protected exceptions are not resolved" in reason
                                    for reason in record["reason"]), record["reason"])
                self.assertTrue(any("support:marek-open-mic" in reason
                                    for reason in record["reason"]), record["reason"])
                self.assertTrue(any("UNBOUND" in reason for reason in record["reason"]),
                                record["reason"])


# --------------------------------------------------------------------------
# section 8: partial failure, re-entry, and the per-object record
# --------------------------------------------------------------------------

class CleanupFailureAndReentryTest(unittest.TestCase):
    """These run against `bound_vocabulary()`, because the shipped UNBOUND
    exception refuses every plan that removes material. See `bound_vocabulary`.

    Levels B, C and D are all gated on the exception being resolved: a plan that
    removes material has to be able to show that its objects are not the
    exception's, and while the identifier is missing it cannot. Only
    CONTENT_ISOLATION, which removes nothing, runs against the shipped file.
    """

    def test_every_object_carries_its_own_intent_reason_and_eligibility(self):
        record = cleanup(CONTENT_ISOLATION, objects=[
            person("rec:audio_0001"),
            person("tr:transcript_0001", object_type="transcript"),
        ])
        self.assertEqual(record["decision"], "PLAN")
        self.assertEqual(len(record["objects"]), 2)
        for entry in record["objects"]:
            with self.subTest(object=entry["object_ref"]):
                self.assertIn(entry["intent"], meeting.ACTING_INTENTS + (
                    "ALREADY_EXECUTED", "BLOCKED_PERMISSION_DENIED", "REINDEX_REQUIRED",
                    "REFUSED", "SKIP"))
                self.assertTrue(entry["reason"])
                self.assertIn(entry["eligibility"], ("ELIGIBLE", "BLOCKED"))
                self.assertIn(entry["observed_status"], meeting.OBSERVED_STATUSES)

    def test_a_partial_result_says_which_objects_did_not_proceed(self):
        """Case 12: 局部删除失败."""
        record = cleanup(CONTENT_ISOLATION, objects=[
            person("rec:audio_0001"),
            person("tr:transcript_0001", object_type="transcript",
                   observed={"status": "FAILED"}),
        ])
        self.assertEqual(record["decision"], "PARTIAL")
        self.assertTrue(record["partial"])
        intents = {e["object_ref"]: e["intent"] for e in record["objects"]}
        self.assertEqual(intents["rec:audio_0001"], "ISOLATE")
        self.assertEqual(intents["tr:transcript_0001"], "SKIP")
        self.assertTrue(any("1 of 2" in reason for reason in record["reason"]),
                        record["reason"])

    def test_a_retry_budget_is_required_to_re_attempt_a_failed_object(self):
        failed = person(observed={"status": "FAILED"})
        without = cleanup(CONTENT_ISOLATION, objects=[failed])
        self.assertEqual(without["objects"][0]["intent"], "SKIP")
        with_budget = cleanup(CONTENT_ISOLATION, objects=[failed], retry_budget=1)
        self.assertEqual(with_budget["objects"][0]["intent"], "ISOLATE")

    def test_re_entry_does_not_retry_a_deletion_that_already_succeeded(self):
        """Case 14 partly: 候选不重生 — an executed object is read back, not repeated."""
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(), objects=[
            person("rec:audio_0001", observed={"status": "EXECUTED",
                                               "receipt": "receipt:r_0001"}),
            person("tr:transcript_0001", object_type="transcript"),
        ])
        self.assertEqual(record["decision"], "PLAN")
        executed = record["objects"][0]
        self.assertEqual(executed["intent"], "ALREADY_EXECUTED")
        self.assertNotIn(executed["intent"], meeting.ACTING_INTENTS)
        self.assertEqual(executed["receipt"], "receipt:r_0001")
        self.assertIn("rec:audio_0001", record["already_executed"])
        self.assertIn("not retried", executed["reason"][0])

    def test_a_restored_object_requires_a_reindex_and_suppresses_regrowth(self):
        """Case 13: 恢复后重索引."""
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(), objects=[
            person("rec:audio_0001", observed={"status": "RESTORED",
                                               "receipt": "receipt:r_0002"}),
        ])
        entry = record["objects"][0]
        self.assertEqual(entry["intent"], "REINDEX_REQUIRED")
        self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)
        self.assertIn("rec:audio_0001", record["reindex_required"])
        self.assertIn("rec:audio_0001", record["suppress_candidate_regeneration"])
        self.assertTrue(any("regrow" in reason or "regenerat" in reason.lower()
                            for reason in record["reason"]), record["reason"])

    def test_the_candidate_does_not_regrow_from_an_index_that_still_remembers_it(self):
        """Case 14: 候选不重生.

        A restored object leaves a derived index that still points at it, so the
        plan has to say which objects must be reindexed and which must not be
        re-proposed as candidates — otherwise the next run rediscovers the same
        object and disposes of it again.
        """
        restored = person("rec:audio_0001", observed={"status": "RESTORED",
                                                      "receipt": "receipt:r_0002"})
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(),
                         objects=[restored, person("tr:transcript_0001",
                                                   object_type="transcript")])
        self.assertEqual(record["decision"], "PLAN")
        self.assertIn("rec:audio_0001", record["reindex_required"])
        self.assertIn("rec:audio_0001", record["suppress_candidate_regeneration"])
        self.assertNotIn("tr:transcript_0001", record["suppress_candidate_regeneration"])
        for entry in record["objects"]:
            if entry["object_ref"] == "rec:audio_0001":
                self.assertEqual(entry["intent"], "REINDEX_REQUIRED")
                self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)

    def test_a_re_entry_does_not_reindex_an_object_it_never_disposed_of(self):
        record = cleanup(DISPOSAL, vocab=bound_vocabulary(),
                         objects=[person("tr:transcript_0001", object_type="transcript")])
        self.assertEqual(record["reindex_required"], [])
        self.assertEqual(record["already_executed"], [])

    def test_a_permission_denial_is_not_retried_under_another_identity(self):
        """Case 11: 403."""
        record = cleanup(CONTENT_ISOLATION, objects=[
            person("rec:audio_0001", observed={"status": "PERMISSION_DENIED"}),
            person("tr:transcript_0001", object_type="transcript"),
        ])
        self.assertEqual(record["decision"], "PARTIAL")
        denied = record["objects"][0]
        self.assertEqual(denied["intent"], "BLOCKED_PERMISSION_DENIED")
        self.assertTrue(any("not retried under another identity" in r
                            for r in denied["reason"]), denied["reason"])
        self.assertTrue(any("not retried under another identity" in r
                            for r in record["reason"]), record["reason"])

    def test_nothing_is_planned_for_a_denied_object(self):
        record = cleanup(CONTENT_ISOLATION,
                         objects=[person(observed={"status": "PERMISSION_DENIED"})])
        self.assertEqual(record["decision"], "PARTIAL")
        self.assertNotIn(record["objects"][0]["intent"], meeting.ACTING_INTENTS)

    def test_a_revision_that_changed_after_the_plan_is_refused(self):
        """Case 16: 删除前 revision 改变."""
        record = cleanup(CONTENT_ISOLATION,
                         objects=[person(revision="r8", planned_revision="r7")])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("revision" in reason for reason in record["reason"]),
                        record["reason"])
        for entry in record["objects"]:
            self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)

    def test_an_object_still_named_by_a_reference_is_preserved(self):
        """Case 8: 仍被 Item 引用."""
        record = cleanup(REBUILDABLE, vocab=bound_vocabulary(),
                         objects=[person(references=[{"ref": "SRV-0011"}])])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("reference" in reason for reason in record["reason"]),
                        record["reason"])

    def test_a_legal_hold_requires_preservation(self):
        """Case 10: legal hold."""
        record = cleanup(REBUILDABLE, objects=[person(holds=["LEGAL_HOLD"])],
                         vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("hold" in reason for reason in record["reason"]),
                        record["reason"])

    def test_a_shared_or_other_owned_object_defers(self):
        """Case 7: shared / non-owner."""
        for ownership in ("SHARED", "OTHER_OWNER", "UNKNOWN"):
            with self.subTest(ownership=ownership):
                record = cleanup(REBUILDABLE, objects=[person(ownership=ownership)],
                                 vocab=bound_vocabulary())
                self.assertEqual(record["decision"], "REFUSAL")
                self.assertTrue(any("ownership" in reason for reason in record["reason"]),
                                record["reason"])

    def test_an_unconfirmed_permission_defers(self):
        record = cleanup(REBUILDABLE, objects=[person(permission_confirmed=False)],
                         vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("permission" in reason for reason in record["reason"]),
                        record["reason"])

    def test_human_unique_edits_are_never_silently_rewritten(self):
        record = cleanup(REBUILDABLE, objects=[person(human_unique_edits=True)],
                         vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("human-unique" in reason for reason in record["reason"]),
                        record["reason"])

    def test_an_object_outside_the_policy_scope_is_refused(self):
        record = cleanup(REBUILDABLE, vocab=bound_vocabulary(),
                         objects=[person(scope={"chat_ref": OTHER_CHAT})])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertTrue(any("outside the policy scope" in reason
                            for reason in record["reason"]), record["reason"])

    def test_a_refusal_carries_no_acting_intent(self):
        """A refusal reports why, and it never reports an object about to be acted on."""
        record = cleanup(REBUILDABLE, vocab=bound_vocabulary(), objects=[
            person("rec:audio_0001", holds=["LEGAL_HOLD"]),
            person("tr:transcript_0001", object_type="transcript", ownership="SHARED"),
        ])
        self.assertEqual(record["decision"], "REFUSAL")
        self.assertFalse(record["partial"])
        for entry in record["objects"]:
            with self.subTest(object=entry["object_ref"]):
                self.assertEqual(entry["intent"], "REFUSED")
                self.assertNotIn(entry["intent"], meeting.ACTING_INTENTS)
                self.assertEqual(entry["eligibility"], "BLOCKED")

    def test_a_level_that_is_a_proposal_carries_no_plan(self):
        record = cleanup(DISPOSAL, policy_overrides={
            "retention_window": {"approved_policy_ref": None, "days": 30}},
            vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertEqual(record["objects"], [])

    def test_the_same_request_returns_the_same_plan(self):
        objects = [person("rec:audio_0001"),
                   person("tr:transcript_0001", object_type="transcript",
                          observed={"status": "PERMISSION_DENIED"})]
        self.assertEqual(cleanup(CONTENT_ISOLATION, objects=objects),
                         cleanup(CONTENT_ISOLATION, objects=objects))


# --------------------------------------------------------------------------
# section 11: closed vocabularies, non-empty reasons, and the two failure kinds
# --------------------------------------------------------------------------

class DecisionContractTest(unittest.TestCase):
    """Every record; a declared decision; a non-empty reason. REJECTED is a bug."""

    CASES = {
        "muted irrelevant": (bundle(segments=[segment(0, 1800)]), None),
        "muted with a question": (bundle(segments=[segment(0, 120), segment(120, 300,
                                                                           tie=SUPPORT_TIE)]),
                                  None),
        "relevant by reference": (bundle(references=[{"ref": "SRV-0011"}]), None),
        "unknown source": (bundle(scope={"chat_ref": UNRELATED_CHAT}), None),
        "no scope": (bundle(scope={}), None),
        "no policies": (bundle(), []),
        "human-approved dependant": (bundle(segments=[segment(0, 30)],
                                            dependants=[{"kind": "report",
                                                         "ref": "report:published_0001",
                                                         "human_approved": True}]), None),
        "policy missing a field": (bundle(), [policy(receipt_store=None)]),
    }

    def test_every_record_is_a_declared_decision_with_a_reason(self):
        for name, (document, policies) in self.CASES.items():
            with self.subTest(case=name):
                record = classify(document, policies=policies)
                self.assertIn(record["decision"], meeting.DECISIONS)
                self.assertTrue(record["reason"], f"{name}: empty reason")
                for reason in record["reason"]:
                    self.assertTrue(isinstance(reason, str) and reason.strip())
                self.assertEqual(record["schema"], meeting.SCHEMA)
                self.assertIn(record["confidence"], ("high", "medium", "low"))
                self.assertIn(record["approval"], ("none-required", "policy-approved",
                                                   "explicit"))

    def test_a_cleanup_record_is_also_declared_and_reasoned(self):
        records = [
            cleanup(CONTENT_ISOLATION),
            cleanup(REBUILDABLE, policy_overrides={"expires_at": EARLIER}),
            cleanup(DISPOSAL, vocab=bound_vocabulary(),
                    provider={"recycle_bin": False, "restore": True}),
            cleanup(IRREVERSIBLE, vocab=bound_vocabulary()),
        ]
        for record in records:
            with self.subTest(decision=record["decision"]):
                self.assertIn(record["decision"], meeting.DECISIONS)
                self.assertTrue(record["reason"])
                self.assertEqual(record["schema"], meeting.CLEANUP_SCHEMA)

    def test_a_malformed_request_is_rejected_not_answered(self):
        cases = {
            "unknown level": lambda: meeting.plan_cleanup(
                {"kind": "cleanup", "level": "SOFT_DELETE", "as_of": AS_OF,
                 "objects": [person()], "policy": policy()}, vocabulary()),
            "unknown object type": lambda: cleanup(objects=[person(object_type="blob")]),
            "unknown operation": lambda: cleanup(policy_overrides={"operation": "PURGE"}),
            "unknown relevance rule": lambda: classify(
                bundle(), policies=[policy(relevance_rule="whatever-seems-right")]),
            "unknown ownership": lambda: classify(bundle(ownership="PROBABLY_MINE")),
            "unknown binding state": lambda: cleanup(policy_overrides={
                "binding_state": "MAYBE"}),
            "policy not an object": lambda: classify(bundle(), policies=["a policy"]),
            "selector not an object": lambda: cleanup(policy_overrides={
                "source_selector": "chat:oc_support_0001"}),
            "malformed as_of": lambda: cleanup(as_of="yesterday"),
            "segment ending before it starts": lambda: classify(
                bundle(segments=[segment(60, 30)])),
            "segment without provenance": lambda: classify(bundle(
                segments=[{"start_s": 0, "end_s": 30}])),
            "objects not a list": lambda: cleanup(objects={"object_ref": "rec:1"}),
            "empty object list": lambda: cleanup(objects=[]),
            "negative retry budget": lambda: cleanup(retry_budget=-1),
            "unknown request kind": lambda: meeting.decide({"kind": "summarise"}),
            "bundle not an object": lambda: classify("a meeting"),
        }
        for name, call in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(meeting.Rejected):
                    call()

    def test_a_missing_rule_is_a_question_and_not_a_caller_bug(self):
        """PROPOSAL_REQUIRED is a successful determination about a gap."""
        record = cleanup(DISPOSAL,
                         policy_overrides={"retention_window": {
                             "approved_policy_ref": None, "days": 30}},
                         vocab=bound_vocabulary())
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertNotEqual(record["decision"], "REJECTED")
        self.assertIn("PROPOSAL_REQUIRED", meeting.DECISIONS)

    def test_rejected_is_not_one_of_the_answers_the_pipeline_produces(self):
        """No input to the pipeline can produce REJECTED; only a caller bug can."""
        for name, (document, policies) in self.CASES.items():
            with self.subTest(case=name):
                self.assertNotEqual(classify(document, policies=policies)["decision"],
                                    "REJECTED")

    def test_an_unknown_vocabulary_schema_is_rejected(self):
        import tempfile
        vocab = vocabulary()
        vocab["schema"] = "serveros/meeting-vocabulary.v0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meeting-vocabulary.json"
            path.write_text(json.dumps(vocab), encoding="utf-8")
            with self.assertRaises(meeting.Rejected):
                meeting.load_vocabulary(path)

    def test_an_unreadable_vocabulary_is_rejected(self):
        with self.assertRaises(meeting.Rejected):
            meeting.load_vocabulary(SPACE / "no-such-vocabulary.json")


# --------------------------------------------------------------------------
# the entry points
# --------------------------------------------------------------------------

class EntryPointTest(unittest.TestCase):

    def test_decide_routes_both_kinds(self):
        relevance = meeting.decide({"kind": "relevance", "bundle": bundle()},
                                   vocabulary())
        self.assertEqual(relevance["schema"], meeting.SCHEMA)
        plan = meeting.decide({"kind": "cleanup", "level": CONTENT_ISOLATION,
                               "as_of": AS_OF, "objects": [person()],
                               "policy": policy()}, vocabulary())
        self.assertEqual(plan["schema"], meeting.CLEANUP_SCHEMA)

    def test_a_request_without_a_kind_is_a_relevance_request(self):
        record = meeting.decide({"bundle": bundle()}, vocabulary())
        self.assertEqual(record["schema"], meeting.SCHEMA)

    def test_the_cli_and_the_library_agree(self):
        """If they could disagree, the CLI would be a second opinion."""
        import tempfile
        request = {"kind": "relevance", "bundle": bundle(
            segments=[segment(0, 120), segment(120, 300, tie=SUPPORT_TIE)])}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode()),
                         meeting.decide(request, vocabulary()))

    def test_a_review_exits_zero_because_it_is_a_decision(self):
        import tempfile
        request = {"kind": "relevance", "bundle": bundle()}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REVIEW_NO_DELETE")

    def test_a_cleanup_request_exits_zero(self):
        import tempfile
        request = {"kind": "cleanup", "level": CONTENT_ISOLATION, "as_of": AS_OF,
                   "objects": [person()], "policy": policy()}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "PLAN")

    def test_a_caller_bug_exits_two(self):
        import tempfile
        request = {"kind": "relevance", "bundle": bundle(ownership="MAYBE")}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REJECTED")

    def test_no_request_document_is_a_caller_bug_not_a_default_answer(self):
        proc = run_cli()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REJECTED")

    def test_a_request_that_is_not_json_is_a_caller_bug(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text("{not json", encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REJECTED")


class SchemaDocumentTest(unittest.TestCase):

    def test_the_schema_document_declares_its_schema_and_its_verification(self):
        """The prose and the machine checks live beside the kernel, as for Space."""
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("schema: serveros/meeting-relevance-decision.v1", text)
        for section in ("request:", "response:", "rules:", "verification:"):
            self.assertIn(section, text)
        self.assertGreaterEqual(text.count('  - "'), 10)
        # Every required field of section 9 is named in the document.
        for field in REQUIRED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, text, f"{field} is not documented in the schema")

    def test_the_kernel_and_the_schema_agree_on_the_schema_ids(self):
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn(meeting.SCHEMA, text)
        self.assertIn(meeting.CLEANUP_SCHEMA, text)


if __name__ == "__main__":
    unittest.main()
