"""D04 实际消费闭合: for every kind a composition ships, WHO READS IT, and on what.

THE SENTENCE, AND THE HALF OF IT THAT IS MISSING. `DIRECTORY-CONVERGENCE.md`
D04 reads 「服务/Agent 读到正确版本，**不只**检查文件存在或 PR 合并」. The second
clause names the failure mode directly: a claim that the thing was CONSUMED,
resting on the thing EXISTING. Two sharper versions of that failure are already
gated elsewhere in this tree -- `tree_drift` refuses a presence-only answer for
installed bytes and says so in its own comment ("an installed copy with the
right file names and stale bytes is exactly the failure this module exists to
catch") -- but there is a third that nothing asks: the installed bytes being
right is not the same fact as a consumer having READ them. §8 states the same
criterion from the other side, as 「当前 consumer 有实际读回」.

So this file asks two questions of every kind the compositions ship, and
requires both to be answered by name:

    installed  how is "the copy on disk holds the declared bytes" established?
    consumed   how is "something actually read those bytes" established?

THE SET IS DERIVED, NOT LISTED. `scripts/release.py` closes the set of kinds
(`ARTIFACT_KINDS`); `plugin/bundle.yaml` and `plugin/profiles/*.yaml` each declare
which of them that composition is released as, and `scripts/build.py` reads that
list as the artifact's own statement of what it is. The kinds this file must
classify are therefore the union of those declarations, read from the files --
so a new kind, or a kind newly added to a profile, is a FAILURE here rather than
a discovery, and the failure prints both differences rather than one.

THE CITATION IS CHECKED, NOT TRUSTED. Each answer names the file and the symbol
that carries the evidence, AND the test that proves the evidence can fail, and
the tests open both and look. "Somebody checks this somewhere" is exactly the
claim that stays true after the checker is deleted, so a citation that outlives
its reader must go red here. Two tests do it: `TheCitedEvidenceIsStillThereTests`
for the symbol, `TheCitedProofsAreRealTests` for the behavioural half.

WHY A SYMBOL CHECK IS NOT ENOUGH, and where the two halves part company. A
symbol survives its behaviour: the function can keep its name and stop meaning
what the citation says it means, and a check that only greps for the name would
stay green through that. So each answer also names the TEST whose failure would
refute it — normally the falsifying direction, the test that tampers with
something and requires the check to notice — and that name is checked too. The
pair is the strongest form available without running the suite from inside it,
and the mutation probe in the Run's own `probe/mutations.py` is what covers the
remaining gap by renaming the symbol and re-running these very tests.

WHAT THIS FILE DOES NOT DO. It does not add a readback, does not add an axis to
any report's verdict, and does not decide anything about the release generation.
It makes an absence and an inference LEGIBLE, which is the one thing a criterion
can do before somebody decides what to build.
"""
import pathlib
import re
import sys
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _compositions() -> dict[str, list[str]]:
    """composition name -> the artifact kinds it declares, read from the files.

    `plugin/bundle.yaml` is the core composition and `plugin/profiles/*.yaml` are
    the ones that `extends:` it. Read through `yaml.safe_load` because that is
    how the build reads them, so a syntax the build accepts is a syntax this file
    accepts -- a second reader with its own opinion about the format is how the
    two come to disagree about the same file.
    """
    paths = [ROOT / "plugin" / "bundle.yaml"]
    paths += sorted((ROOT / "plugin" / "profiles").glob("*.yaml"))
    out: dict[str, list[str]] = {}
    for path in paths:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = doc.get("composition")
        kinds = doc.get("artifacts")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{path} declares no `composition:`")
        if not isinstance(kinds, list) or not kinds:
            raise ValueError(f"{path} declares no `artifacts:`")
        out[name] = list(kinds)
    return out


#: The kinds a composition may be released as, and who reads each one.
#:
#: Keyed by KIND rather than by (composition, kind) ON PURPOSE, and the cost of
#: that choice is written down rather than hidden: the answer is a property of
#: the kind's consumer MECHANISM -- a Claude/Codex session loads a plugin, a
#: supervisor starts a node runtime -- so two compositions shipping the same kind
#: have the same answer, and a composition that shipped a kind to a DIFFERENT
#: consumer would not be caught here. The re-check condition is the day some
#: composition's consumer stops being the mechanism this table names.
#:
#: `installed` and `consumed` are separate because they fail separately. An entry
#: is (mode, the file, the symbol, why, and the proof) -- and `consumed` may be
#: INFERENCE or NONE, but never CONTENT: "the bytes on disk are the declared
#: bytes" is a statement about the disk, and reading it as an answer to "who read
#: them" is the exact substitution D04's second clause forbids.
#:
#: The fifth element is the test whose failure would refute the answer, and it is
#: `None` exactly when the mode is NONE. That biconditional is asserted below: a
#: claim of absence may not cite a proof, and a claim of presence may not go
#: without one.
CONTENT = "content"        # installed bytes are compared against a rebuild
READBACK = "readback"      # a consumer writes down, or answers on demand, what it read
INFERENCE = "inference"    # a consumer's version is DERIVED from other facts
NONE = "none"              # nothing establishes it, and the absence is deliberate

#: What `consumed` may be. `content` is deliberately absent: see the comment above.
CONSUMED_MODES = (READBACK, INFERENCE, NONE)

CLASSIFICATION = {
    "node-runtime": {
        "installed": (
            CONTENT,
            "scripts/runtime_truth.py", "expected_artifact",
            "`expected_artifact` rebuilds the revision's file set from canonical "
            "source plus the pin the artifact's own README records, and "
            "`tree_drift` compares CONTENT against it rather than names -- a "
            "presence-only check would call a right-named, stale-byted "
            "installation clean, which is the failure that module exists to "
            "catch. `node_revision` applies the same rule to RELEASE.json's "
            "tree_hash, recomputed rather than read.",
            "tests/test_runtime_truth.py::RuntimeTruthTests::test_stale_bytes_under_correct_names_are_detected",
        ),
        "consumed": (
            READBACK,
            "runtime/daemon.py", "_installed_identity",
            "The long-lived consumer ANSWERS, over its own IPC surface, which "
            "revision it is: `_installed_identity` derives the revision root "
            "from `__file__` -- the directory the interpreter actually loaded "
            "this module from, not a lookup of `current` -- and freezes the "
            "identity files it read there, so a process that re-resolved the "
            "pointer would be caught answering 'where the release is now' to a "
            "question about 'which bytes am I running'. `_pointer_state` asks "
            "the other side of it live, and the pair reaches a person through "
            "the daemon's own device payload.\n"
            "            THE WEAKER HALF OF THE SAME ROW IS AN INFERENCE, and "
            "naming it is the point: `scripts/runtime_truth.py` also places a "
            "running pid by comparing `process_started_at` against the `current` "
            "symlink's mtime, because `release.py promote` moves a symlink "
            "without restarting anything and argv is byte-identical before and "
            "after. That comparison is sound -- both numbers come from the same "
            "clock -- but it is a conclusion from two facts, not a record, and "
            "it can only speak about a pointer that MOVED. The readback above is "
            "what remains true when it did not.",
            "tests/test_runtime_truth.py::RuntimeTruthTests::test_an_installed_node_can_say_which_release_and_source_it_is",
        ),
    },
    "plugin": {
        "installed": (
            CONTENT,
            "scripts/runtime_truth.py", "expected_artifact",
            "Same machinery as the node runtime and the same rule: the installed "
            "plugin cache is compared as CONTENT against a rebuild from the "
            "artifact's own recorded source, with the source-substitution note "
            "carried when the comparison had to fall back to the checkout.",
            "tests/test_runtime_truth.py::RuntimeTruthTests::test_stale_bytes_under_correct_names_are_detected",
        ),
        "consumed": (
            NONE,
            "scripts/release.py", "ARTIFACT_KINDS",
            "NOTHING ESTABLISHES IT. The release path says so in its own output "
            "-- `scope  installed files; loaded units/processes not checked` -- "
            "and that line is not a disclaimer bolted on afterwards: the "
            "module's own docstring records the incident that put it there, when "
            "`status` reported agreement while the node ran a process that would "
            "not start. What the node can read about this face is INSTALLATION, "
            "not consumption: `read_claude_install`/`read_enabled_plugins` and "
            "`read_codex_install`/`read_codex_plugins` answer which plugin a "
            "config file records as installed and enabled, and a config file "
            "saying `enabled = true` is the same shape of answer as a directory "
            "listing -- it survives the session that never loaded anything.\n"
            "            WHY IT IS LEFT THIS WAY RATHER THAN FIXED HERE: a "
            "readback for this face means a record written by the consumer, and "
            "which generation that consumer is meant to be on is the question "
            "`native-work-env-20261008/PENDING-DECISIONS.md` entry 7 is open on. "
            "This file does not decide it and does not add an axis to any "
            "verdict; it makes the absence a red line.\n"
            "            RE-CHECK WHEN: a consumer-side readback record exists, "
            "or entry 7 is decided, or `scripts/release.py`'s scope line stops "
            "saying 'not checked'.",
            None,
        ),
    },
}

#: A proof is named as `<file>::<Class>::<method>`. Named here rather than
#: parsed out of the tuple so that a proof written in any other shape is a
#: failure to read rather than a silent pass.
PROOF_SHAPE = re.compile(r"^(?P<file>[^:]+)::(?P<cls>\w+)::(?P<method>\w+)$")

#: Every answer's `why` must be longer than this. A one-liner is how a table of
#: real judgements turns into a table of checkmarks: the entry that has nothing
#: to say is the entry that needed saying.
MIN_REASON = 80

#: The compositions this file has read, and the ONE sentence that covers all of
#: them: a composition is a thing this node can install, so D04's question has to
#: be asked of it. One reason rather than three, because three copies of one
#: sentence is how a review starts skimming the third -- but the names are
#: enumerated rather than counted, so a new profile is a reviewable event (what
#: does it ship, and who reads that?) rather than a silent widening of what this
#: file claims to have covered.
COMPOSITIONS_SEEN = {
    "serveros-core": "plugin/bundle.yaml",
    "serveros-linux-work": "plugin/profiles/linux-work.yaml",
    "serveros-testing": "plugin/profiles/testing.yaml",
}
WHY_EVERY_COMPOSITION_IS_LISTED = (
    "This is the set of compositions the repository can build, and each one is "
    "installed on some node -- so each one's shipped kinds are asked 'who reads "
    "it' by TheKindsAreExactlyTheClassifiedOnesTests. A composition that arrives "
    "without being added here may ship a kind this file has never asked about."
)

#: A citation names a symbol, and a symbol is defined in one of two shapes: a
#: function or class, where the keyword comes first, or a module-level constant,
#: where the name IS the left-hand side. Both are checked because a citation that
#: only ever matched the first shape would report a constant as deleted -- which
#: is how the first draft of this file failed, citing `ARTIFACT_KINDS` under a
#: pattern that could not see it.
DEFINITION = (r"^\s*(?:def|class)\s+%s\b", r"^%s\s*=")


def _find_symbol(path: pathlib.Path, symbol: str) -> bool:
    source = path.read_text(encoding="utf-8")
    return any(re.search(shape % re.escape(symbol), source, re.MULTILINE)
               for shape in DEFINITION)


class TheDerivationSawTheCompositionsTests(unittest.TestCase):
    """An empty scan and a scan nobody ran produce the same output."""

    def test_the_scan_read_more_than_one_composition(self):
        compositions = _compositions()
        self.assertGreaterEqual(
            len(compositions), 3,
            f"only {len(compositions)} composition(s) read: {sorted(compositions)}")

    def test_the_compositions_read_are_exactly_the_ones_listed(self):
        """Both differences, so neither direction hides behind the other."""
        read = set(_compositions())
        declared = set(COMPOSITIONS_SEEN)
        self.assertEqual(
            read, declared,
            f"a composition shipped here and not listed: {sorted(read - declared)}; "
            f"a listing whose file no longer declares it: "
            f"{sorted(declared - read)} -- a profile added to `plugin/profiles/` "
            "ships artifacts on some node, so D04's question has to be asked of "
            "it before this file can claim it was. " + WHY_EVERY_COMPOSITION_IS_LISTED)

    def test_every_listed_composition_names_the_file_that_declares_it(self):
        for name, layer in COMPOSITIONS_SEEN.items():
            with self.subTest(composition=name):
                self.assertTrue((ROOT / layer).exists(),
                                f"{layer} is named as {name}'s declaration and is absent")

    def test_the_core_composition_ships_both_kinds(self):
        """The composition this node runs is the one that must ship both."""
        core = _compositions().get("serveros-core")
        self.assertIsNotNone(core, "plugin/bundle.yaml is not among the compositions read")
        self.assertEqual(sorted(core), ["node-runtime", "plugin"], core)


class TheKindsAreExactlyTheClassifiedOnesTests(unittest.TestCase):
    def test_every_declared_kind_is_classified_and_every_classification_is_declared(self):
        declared: set[str] = set()
        for kinds in _compositions().values():
            declared |= set(kinds)
        self.assertEqual(
            declared, set(CLASSIFICATION),
            "these differ from the declaration: a kind a composition is released "
            "as must be classified here with who reads it, and a classification "
            "whose kind no composition ships must be removed. A kind that "
            "arrives unclassified is the discovery this gate exists to turn "
            "into a failure")

    def test_every_classification_answers_both_questions(self):
        for kind, entry in CLASSIFICATION.items():
            with self.subTest(kind=kind):
                self.assertEqual(set(entry), {"installed", "consumed"},
                                 "a kind must answer both questions or neither")

    def test_no_answer_rests_on_the_bytes_being_on_disk(self):
        """The substitution D04's second clause forbids, refused mechanically."""
        for kind, entry in CLASSIFICATION.items():
            with self.subTest(kind=kind):
                mode = entry["consumed"][0]
                self.assertIn(
                    mode, CONSUMED_MODES,
                    f"`consumed` for {kind} answers {mode!r}: installed bytes "
                    f"being correct is a fact about the disk, and the modes it "
                    f"may be answered with are {list(CONSUMED_MODES)}")

    def test_every_answer_names_a_file_a_symbol_and_a_reason(self):
        for kind, entry in CLASSIFICATION.items():
            for question, answer in entry.items():
                with self.subTest(kind=kind, question=question):
                    self.assertEqual(len(answer), 5,
                                     "an answer is (mode, file, symbol, why, proof)")
                    _mode, layer, symbol, why, _proof = answer
                    self.assertTrue((ROOT / layer).exists(),
                                    f"{layer} is cited for {kind}.{question} and is absent")
                    self.assertGreater(
                        len(why.strip()), MIN_REASON,
                        f"{kind}.{question} names {symbol} without saying why it "
                        "is the answer")

    def test_an_answer_that_claims_an_absence_cites_no_proof(self):
        """The biconditional between `none` and `None`, in both directions.

        A claim of absence carrying a proof is a contradiction -- if a test
        refutes the answer, the answer was not "nothing establishes it". A claim
        of presence carrying none is an assertion: it says where the evidence is
        and refuses to say what would show it wrong.
        """
        for kind, entry in CLASSIFICATION.items():
            for question, answer in entry.items():
                with self.subTest(kind=kind, question=question):
                    mode, _layer, _symbol, _why, proof = answer
                    if mode == NONE:
                        self.assertIsNone(
                            proof, f"{kind}.{question} answers `none` and cites "
                            f"{proof!r} as the test that would refute it")
                    else:
                        self.assertIsNotNone(
                            proof, f"{kind}.{question} answers {mode!r} and "
                            "names no test whose failure would refute it")


class TheCitedEvidenceIsStillThereTests(unittest.TestCase):
    """Each cited symbol is opened and looked for, so the claim can go stale.

    This is the half that makes the file a criterion rather than a comment. A
    citation is a statement about another file, and the file is free to change
    without telling this one -- so the statement is checked where it lives.
    """

    def test_the_cited_evidence_is_still_there(self):
        for kind, entry in CLASSIFICATION.items():
            for question, answer in entry.items():
                with self.subTest(kind=kind, question=question):
                    _mode, layer, symbol, _why, _proof = answer
                    self.assertTrue(
                        _find_symbol(ROOT / layer, symbol),
                        f"{layer} is cited as the evidence for {kind}.{question} "
                        f"and no longer defines {symbol} -- either the evidence "
                        "moved and the citation is stale, or it was deleted and "
                        "this kind is now answered by nothing")

    def test_the_consumed_answer_for_a_plugin_says_it_is_unestablished(self):
        """The one live finding, pinned as a statement rather than a printout.

        When somebody builds a plugin-side readback this test goes red, which is
        the correct way for a declaration of absence to end: not by being
        quietly edited, but by the thing it described stopping being true.
        """
        mode, layer, symbol, why, _proof = CLASSIFICATION["plugin"]["consumed"]
        self.assertEqual(mode, NONE, f"plugin.consumed now answers {mode!r}")
        source = (ROOT / "scripts/release.py").read_text(encoding="utf-8")
        self.assertIn("not checked", source,
                      "the scope line naming this as unchecked is gone; if the "
                      "release path now checks loaded processes, this entry and "
                      "its reason are out of date")
        self.assertIn("RE-CHECK WHEN", why,
                      "an absence without a re-check condition is an exemption")


class TheCitedProofsAreRealTests(unittest.TestCase):
    """Each proof is a test in this tree, and it is looked for where it lives.

    A symbol check proves the evidence EXISTS. It does not prove the evidence
    still BITES: a function keeps its name while its behaviour changes, and the
    citation stays green. The proof name is the answer to that, and it is
    checked here in the only way available from inside a test -- the class and
    the method are opened and looked for -- with the probe's rename mutation
    covering the reading that a pass here is not the same as a passing test.

    What this still does NOT catch, stated because a criterion that hides its
    own edge is the defect this whole round is about: a proof whose BODY is
    weakened in place, keeping its name. Only running it catches that, and the
    suite runs it -- so the honest statement is that this test makes the proof
    name impossible to lose silently, not that it keeps the proof strong.
    """

    def test_every_cited_proof_names_a_test_that_exists(self):
        for kind, entry in CLASSIFICATION.items():
            for question, answer in entry.items():
                _mode, _layer, _symbol, _why, proof = answer
                if proof is None:
                    continue
                with self.subTest(kind=kind, question=question, proof=proof):
                    self.assertIsNotNone(
                        PROOF_SHAPE.match(proof),
                        f"{proof!r} is not `<file>::<Class>::<method>`")
                    file, cls, method = PROOF_SHAPE.match(proof).groups()
                    path = ROOT / file
                    self.assertTrue(path.is_file(),
                                    f"{file} is cited as the proof for "
                                    f"{kind}.{question} and is not a file")
                    source = path.read_text(encoding="utf-8")
                    self.assertTrue(
                        re.search(r"^class\s+%s\b" % re.escape(cls), source, re.M),
                        f"{file} has no class {cls}, so {proof!r} names a proof "
                        "that cannot run")
                    self.assertTrue(
                        re.search(r"^\s+def\s+%s\b" % re.escape(method), source, re.M),
                        f"{cls} in {file} has no method {method}, so {proof!r} "
                        "names a proof that cannot run")

    def test_the_proofs_are_not_all_one_test(self):
        """One proof cannot carry two different claims about two different things."""
        proofs = [answer[4] for entry in CLASSIFICATION.values()
                  for answer in entry.values() if answer[4]]
        self.assertTrue(proofs, "no answer cites a proof at all")
        self.assertGreaterEqual(
            len(set(proofs)), 2,
            f"every citation names the same proof, which would mean one test is "
            f"being asked to establish more than one thing: {proofs}")


class TheOtherHalfIsAnsweredTooTests(unittest.TestCase):
    """Both questions are asked of a kind, and the second is not inferred from the first.

    The temptation this refuses: because `installed` is CONTENT for both kinds,
    a reader could collapse the two into "the bytes are right, so it is
    consumed". The modes are asserted to be independent so the collapse is a
    failure rather than a simplification somebody makes in prose.
    """

    def test_the_two_questions_are_not_the_same_answer_for_every_kind(self):
        modes = {kind: entry["consumed"][0] for kind, entry in CLASSIFICATION.items()}
        self.assertNotEqual(
            set(modes.values()), {CONTENT},
            "every kind answers `consumed` with the disk's own evidence")
        self.assertIn(NONE, set(modes.values()),
                      "the live absence this file was written to make legible "
                      "is no longer recorded anywhere here")


if __name__ == "__main__":
    sys.exit(unittest.main())
