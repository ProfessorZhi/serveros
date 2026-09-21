"""Tests for the Space Resolver and its vocabulary.

Hermetic: nothing here reads the network, a database, or this machine's
checkouts. The vocabulary shipped in the repository is the fixture, because the
vocabulary IS the contract — a test that used a synthetic one would prove the
resolver works on data nobody ships.

Two of these tests are structural rather than behavioural, and they carry most of
the weight:

  * `VocabularyContractTest` checks the vocabulary against itself, so a rule that
    can never fire (an undeclared role, an impossible surface pair) fails the
    build instead of sitting in the file looking authoritative.
  * `KernelPurityTest` checks what resolver.py is allowed to import and what its
    source may mention. The separation between decision kernel and surface
    adapter is a property of the code, so it is asserted against the code.
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPACE = ROOT / "space"
VOCABULARY_PATH = SPACE / "vocabulary.json"
RESOLVER_PATH = SPACE / "resolver.py"

_spec = importlib.util.spec_from_file_location("serveros_space_resolver", RESOLVER_PATH)
resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolver)

DECISIONS = {"KEEP", "MOVE", "INDEX", "PROPOSAL_REQUIRED", "REJECTED"}


def vocabulary() -> dict:
    return json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))


def resolve(**kwargs) -> dict:
    return resolver.resolve(vocabulary=vocabulary(), **kwargs)


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER_PATH), *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


class VocabularyContractTest(unittest.TestCase):
    """The vocabulary must be a closed, self-consistent set."""

    def setUp(self):
        self.vocab = vocabulary()
        dims = self.vocab["dimensions"]
        self.surfaces = set(dims["surface"]["values"])
        self.types = set(dims["object_type"]["values"])
        self.roles = set(dims["semantic_role"]["values"])
        self.rules = self.vocab["placement"]["rules"]

    def test_schema_is_declared(self):
        self.assertEqual(self.vocab["schema"], "serveros/space-vocabulary.v1")

    def test_semantic_roles_are_surface_neutral(self):
        """A physical surface name must never appear inside a role.

        This is the rule that retires `drive-project`, `drive-meeting`,
        `drive-staging` and `minute-sidecar`. Encoding it as a test is what keeps
        them retired: a role is WHY an object exists, and where it currently
        lives is a separate dimension.
        """
        for role in self.roles:
            for surface in self.surfaces:
                self.assertNotIn(
                    surface, role,
                    f"semantic_role {role!r} names surface {surface!r}; "
                    "surface belongs in the `surface` dimension",
                )

    def test_object_types_are_orthogonal_to_roles(self):
        self.assertEqual(self.roles & self.types, set(),
                         "object_type and semantic_role must not share a value")

    def test_every_rule_is_reachable(self):
        """A rule whose (surface, role) is undeclared can never fire."""
        for rule in self.rules:
            self.assertIn(rule["surface"], self.surfaces, rule)
            self.assertIn(rule["role"], self.roles, rule)

    def test_every_rule_targets_a_declared_surface_or_declines(self):
        for rule in self.rules:
            target = rule["canonical_surface"]
            self.assertTrue(
                target is None or target in self.surfaces,
                f"rule {rule['surface']}/{rule['role']} targets unknown surface {target!r}",
            )
            self.assertIn(rule.get("strength"), {"canonical", "default", "none"})

    def test_rules_are_unambiguous(self):
        keys = [(r["surface"], r["role"]) for r in self.rules]
        self.assertEqual(len(keys), len(set(keys)), "duplicate (surface, role) rule")

    def test_a_declining_rule_explains_itself(self):
        """`canonical_surface: null` is a real answer, so it must carry a reason."""
        for rule in self.rules:
            if rule["canonical_surface"] is None:
                self.assertTrue(rule.get("note"),
                                f"rule {rule['surface']}/{rule['role']} declines without a note")

    def test_surface_object_types_agree_with_the_surface_dimension(self):
        table = self.vocab["surface_object_types"]
        for surface, allowed in ((k, v) for k, v in table.items() if k != "_comment"):
            self.assertIn(surface, self.surfaces)
            for object_type in allowed:
                self.assertIn(object_type, self.types, f"{surface}: unknown type {object_type!r}")

    def test_target_templates_use_only_declared_slots(self):
        for rule in self.rules:
            template = rule.get("target_template")
            if not template:
                continue
            for slot in resolver.TEMPLATE_SLOTS:
                template = template.replace("{" + slot + "}", "")
            self.assertNotIn("{", template, f"unknown template slot in {template!r}")

    def test_vocabulary_carries_no_observed_state(self):
        """L1/L2 declarations are slow-changing; observed facts belong in L3.

        A vocabulary that embedded a count, a hash, a PID or a snapshot filename
        would rot within a day and then lie. Those facts live in
        `state/serveros/federation/**/current.jsonl` instead.
        """
        text = VOCABULARY_PATH.read_text(encoding="utf-8")
        for forbidden in ("sha256", "PID ", "current.jsonl", "observed_at", "feishu://"):
            self.assertNotIn(forbidden, text,
                             f"vocabulary embeds observed state: {forbidden!r}")


class KernelPurityTest(unittest.TestCase):
    """The decision kernel must not become a surface adapter.

    These read the module's AST rather than its text, so the module docstring —
    which quotes the anti-pattern it exists to prevent — is not mistaken for the
    anti-pattern itself. Code is checked; prose is exempt.
    """

    BANNED_IMPORTS = {
        "socket", "urllib", "http", "requests", "subprocess", "sqlite3", "os",
        "sys", "time", "datetime", "random", "shutil", "glob", "lark",
        "larksuite", "github", "yaml",
    }
    BANNED_TOKENS = ("lark-cli", "lark_cli", "tenant_key", "app_token",
                     "feishu://", "wiki/", "drive/", "time.time",
                     "datetime.now", "utcnow", "random.")

    @classmethod
    def setUpClass(cls):
        import ast
        cls.source = RESOLVER_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef))
        } - {None}

    def code_constants(self):
        import ast
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in self.docstrings:
                    continue
                yield node.value

    def test_imports_only_the_standard_decision_surface(self):
        import ast
        names = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertEqual(names - {"__future__"}, {"argparse", "json", "pathlib"})
        self.assertEqual(names & self.BANNED_IMPORTS, set())

    def test_no_code_literal_names_a_cloud_client_or_a_tenant_path(self):
        for literal in self.code_constants():
            for banned in self.BANNED_TOKENS:
                self.assertNotIn(
                    banned, literal,
                    f"resolver.py code contains {banned!r} in {literal!r}; "
                    "that belongs to a surface adapter",
                )

    def test_source_takes_no_clock(self):
        # The body is scanned too, so a clock call hidden in a lambda is caught.
        for banned in ("time.time", "datetime.now", "utcnow", "random."):
            for literal in self.code_constants():
                self.assertNotIn(banned, literal)


class PlacementDecisionTest(unittest.TestCase):

    def test_workbench_material_is_canonical_where_it_was_authored(self):
        """My Document Library is the workbench: a report does not move out of it.

        The rule is not "anything authored in My Docs is misplaced". It is "the
        part of it that outlives the work is knowledge" — see the test below.
        """
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="report", context={"project": "litellm"})
        self.assertEqual(record["decision"], "KEEP")
        self.assertEqual(record["canonical_surface"], "mydocs")
        self.assertEqual(record["approval"], "none-required")
        self.assertEqual(record["index"], "deliverable-index")

    def test_knowledge_promotes_off_the_workbench(self):
        """The half that does move: durable knowledge is not workbench material."""
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="knowledge", context={"project": "litellm"})
        self.assertEqual(record["decision"], "MOVE")
        self.assertEqual(record["canonical_surface"], "wiki")
        self.assertEqual(record["approval"], "proposal-only")

    def test_a_meeting_on_the_workbench_is_a_question_not_a_move(self):
        """No curated meeting index exists on a Wiki surface, so there is no rule.

        A Minute is the canon for a meeting and its sidecars are Drive files; a
        workbench note that merely concerns a meeting is none of those things.
        The absence is deliberate, and a rule set is allowed to have one.
        """
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="meeting", context={"project": "litellm"})
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertIsNone(record["canonical_surface"])

    def test_a_move_never_invents_a_locator(self):
        """No policy template means no concrete target — the adapter decides where."""
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="knowledge", context={"project": "litellm"})
        self.assertEqual(record["decision"], "MOVE")
        self.assertIsNone(record["target_locator"])
        self.assertEqual(record["logical_target"],
                         {"surface": "wiki", "semantic_role": "knowledge",
                          "project": "litellm"})

    def test_a_caller_template_is_rendered_when_complete(self):
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="knowledge", context={"project": "litellm"},
                         policy={"target_template": "wiki://{project}/{semantic_role}"})
        self.assertEqual(record["target_locator"], "wiki://litellm/knowledge")

    def test_an_incomplete_template_yields_a_proposal_not_a_broken_path(self):
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="knowledge",
                         policy={"target_template": "wiki://{project}/{item}/{semantic_role}"})
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertIsNone(record["target_locator"])

    def test_personal_scratch_stays_personal(self):
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="inbox", context={"project": "litellm"})
        self.assertEqual(record["decision"], "KEEP")
        self.assertEqual(record["approval"], "none-required")

    def test_a_canonical_object_missing_its_index_is_indexed(self):
        record = resolve(surface="minutes", object_type="recording",
                         semantic_role="source", context={"project": "litellm"},
                         observed={"indexed": False})
        self.assertEqual(record["decision"], "INDEX")
        self.assertEqual(record["canonical_surface"], "minutes")
        self.assertEqual(record["index"], "meeting-entry")

    def test_the_same_index_spoken_for_is_a_keep(self):
        record = resolve(surface="minutes", object_type="recording",
                         semantic_role="source", context={"project": "litellm"},
                         observed={"indexed": True})
        self.assertEqual(record["decision"], "KEEP")

    def test_unclassified_is_a_question_not_a_lookup(self):
        record = resolve(surface="mydocs", object_type="document",
                         semantic_role="unclassified")
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertIsNone(record["canonical_surface"])

    def test_a_role_with_no_rule_is_a_proposal(self):
        record = resolve(surface="wiki", object_type="document",
                         semantic_role="residue")
        self.assertEqual(record["decision"], "PROPOSAL_REQUIRED")
        self.assertEqual(record["confidence"], "low")

    def test_relocating_a_source_or_evidence_is_declined_not_guessed(self):
        for role in ("source", "evidence"):
            record = resolve(surface="mydocs", object_type="document",
                             semantic_role=role, context={"project": "litellm"})
            self.assertEqual(record["decision"], "PROPOSAL_REQUIRED", role)
            self.assertTrue(record["reason"])

    def test_a_structural_impossibility_is_rejected_not_proposed(self):
        """A recording cannot live in a Wiki space. That is a caller bug."""
        with self.assertRaises(resolver.Rejected):
            resolve(surface="wiki", object_type="recording", semantic_role="source")

    def test_unknown_vocabulary_values_are_rejected(self):
        for field, value in (("surface", "dropbox"), ("object_type", "blob"),
                             ("semantic_role", "drive-project")):
            kwargs = {"surface": "mydocs", "object_type": "document",
                      "semantic_role": "report"}
            kwargs[field] = value
            with self.assertRaises(resolver.Rejected, msg=field):
                resolve(**kwargs)

    def test_a_retired_surface_named_role_is_still_rejected(self):
        """`minute-sidecar` was retired; it must not quietly work again."""
        with self.assertRaises(resolver.Rejected):
            resolve(surface="minutes", object_type="recording",
                    semantic_role="minute-sidecar")

    def test_every_decision_is_one_of_five_and_explains_itself(self):
        cases = [
            dict(surface="mydocs", object_type="document", semantic_role="inbox"),
            dict(surface="mydocs", object_type="document", semantic_role="report"),
            dict(surface="minutes", object_type="recording", semantic_role="source",
                 observed={"indexed": False}),
            dict(surface="mydocs", object_type="document", semantic_role="unclassified"),
            dict(surface="wiki", object_type="document", semantic_role="residue"),
        ]
        for case in cases:
            record = resolve(**case)
            self.assertIn(record["decision"], DECISIONS)
            self.assertTrue(record["reason"], case)
            self.assertIn(record["confidence"], {"high", "medium", "low"})
            self.assertEqual(record["schema"], "serveros/space-decision.v1")

    def test_the_decision_is_deterministic(self):
        case = dict(surface="mydocs", object_type="document", semantic_role="report",
                    context={"project": "litellm"})
        self.assertEqual(resolve(**case), resolve(**case))


class ResolverContractTest(unittest.TestCase):
    """The vocabulary the resolver accepts, and the rules it enforces on it."""

    def test_a_duplicate_rule_is_a_hard_failure(self):
        vocab = vocabulary()
        vocab["placement"]["rules"].append(dict(vocab["placement"]["rules"][0]))
        with self.assertRaises(resolver.Rejected):
            resolver.resolve(surface="mydocs", object_type="document",
                             semantic_role="report", vocabulary=vocab)

    def test_a_rule_missing_its_key_is_a_hard_failure(self):
        vocab = vocabulary()
        vocab["placement"]["rules"].append({"surface": "wiki", "canonical_surface": "wiki"})
        with self.assertRaises(resolver.Rejected):
            resolver.resolve(surface="wiki", object_type="document",
                             semantic_role="knowledge", vocabulary=vocab)

    def test_a_mismatched_vocabulary_schema_is_a_hard_failure(self):
        import tempfile
        vocab = vocabulary()
        vocab["schema"] = "serveros/space-vocabulary.v0"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vocabulary.json"
            path.write_text(json.dumps(vocab), encoding="utf-8")
            with self.assertRaises(resolver.Rejected):
                resolver.load_vocabulary(path)

    def test_an_unreadable_vocabulary_is_a_hard_failure(self):
        with self.assertRaises(resolver.Rejected):
            resolver.load_vocabulary(ROOT / "space" / "no-such-vocabulary.json")

    def test_an_ambiguous_vocabulary_is_a_hard_failure(self):
        vocab = vocabulary()
        vocab["placement"]["rules"] = "not a list of rules"
        with self.assertRaises(resolver.Rejected):
            resolver._rules(vocab)

    def test_the_shipped_vocabulary_loads(self):
        vocab = resolver.load_vocabulary()
        self.assertEqual(vocab["schema"], resolver.VOCABULARY_SCHEMA)


class CliTest(unittest.TestCase):

    def test_a_decision_exits_zero_and_prints_json(self):
        proc = run_cli("--surface", "mydocs", "--object-type", "document",
                       "--semantic-role", "knowledge", "--project", "litellm")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        record = json.loads(proc.stdout.decode())
        self.assertEqual(record["decision"], "MOVE")

    def test_a_proposal_is_still_a_successful_decision(self):
        proc = run_cli("--surface", "mydocs", "--object-type", "document",
                       "--semantic-role", "unclassified")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "PROPOSAL_REQUIRED")

    def test_a_rejection_exits_two(self):
        proc = run_cli("--surface", "dropbox", "--object-type", "document",
                       "--semantic-role", "report")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REJECTED")

    def test_unindexed_flag_surfaces_a_missing_index(self):
        proc = run_cli("--surface", "minutes", "--object-type", "recording",
                       "--semantic-role", "source", "--unindexed")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "INDEX")

    def test_a_plain_agent_can_ask_without_a_request_document(self):
        """The delegation passes fields as flags, so the flags must be equivalent.

        If the two entry points could disagree, `serverosctl space resolve` would
        be a third opinion rather than a delegation.
        """
        proc = run_cli("--surface", "mydocs", "--object-type", "document",
                       "--semantic-role", "deliverable", "--project", "litellm",
                       "--observed-locator", "feishu://wiki/node/abc",
                       "--target-template", "wiki://{project}/{semantic_role}")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        record = json.loads(proc.stdout.decode())
        expected = resolve(surface="mydocs", object_type="document",
                           semantic_role="deliverable", context={"project": "litellm"},
                           observed={"locator": "feishu://wiki/node/abc"},
                           policy={"target_template": "wiki://{project}/{semantic_role}"})
        self.assertEqual(record, expected)

    def test_a_request_document_is_accepted(self):
        import tempfile
        request = {
            "object": {"surface": "mydocs", "object_type": "document",
                       "semantic_role": "knowledge"},
            "context": {"project": "litellm"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request), encoding="utf-8")
            proc = run_cli("--request", str(path))
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "MOVE")

    def test_missing_arguments_are_rejected_not_crashed(self):
        proc = run_cli("--surface", "mydocs")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout.decode())["decision"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
