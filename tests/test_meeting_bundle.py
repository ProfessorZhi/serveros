"""`space/meeting_bundle.py` — the producer the relevance kernel did not have.

P3's completion line is 会议复用既有 Minutes/Bundle: a meeting's own material
reaching `space/meeting_relevance.py`. Before this round it could not.
`serverosctl space meeting --request` requires somebody to hand-author the
`bundle` object, and `grep` found no producer anywhere in the tree — so the
kernel was reachable and unreached, the same shape `work admit` had before
§9.13. This file tests the thing that closes that: a declaration of a meeting's
sidecars in, the reading and the kernel's bundle out.

WHAT THE FIVE STATES AND THE TWO READINGS ARE FOR
-------------------------------------------------
A bundle missing its audio is not a 75% bundle. The machine policy makes the
four members one unit, so the producer's answer to "is this one" is COMPLETE or
INCOMPLETE, and INCOMPLETE carries WHICH member and what state it was in. The
four non-PRESENT states are four different facts — PROCESSING is a reason to
wait, ABSENT is not, FAILED is a reason to stop waiting, UNREADABLE may mean the
material exists — and a producer that collapsed them into `missing` would make
the plan's own test list (缺音频 / processing) unstateable.

The tests below are grouped by the thing that would be wrong if they failed:

  * `TheContractAgainstTheVocabularyTest` — the contract's four member words and
    its kind mapping against `space/meeting-vocabulary.json`, so a vocabulary
    edit that drops `recording` fails here instead of silently turning "the
    audio is present" into a miss.
  * `PresenceTest` — the five states, and the refusal to call anything but all
    four PRESENT a bundle.
  * `ReadableTest` / `ContentMemberContractTest` — the reading's second answer:
    which material can be read now, with its content, while the unit is not
    complete. 已有合法 transcript 可先读，不等 MP3 或全套成员 is a thing `state` cannot
    say, and handover must not be convertible into "this is a bundle".
  * `ScopeTest` — one meeting's material, `MIXED_SCOPE` when it is not, and the
    wildcard refusal read from the kernel rather than copied.
  * `AxesTest` — the round's own defect, kept as a test: a producer-side word
    for "nobody said" builds a bundle the kernel rejects.
  * `TheBundleReachesTheKernelTest` — the integration. Everything else here can
    pass while the assembled bundle is one the kernel will not take, which is
    exactly what happened: `assemble()` wrote `ownership: "UNDECLARED"` and the
    kernel's declared set is `OWNED | SHARED | OTHER_OWNER | UNKNOWN`, so every
    COMPLETE declaration exited 2 at the last step.
  * `CliTest` — the wiring, as a real process, because an exit code is a fact
    about the shell and the `--sidecars` path has its own required flag.

Hermetic: no network, no clock, no this machine's checkouts, no real tenant,
chat, meeting or person. `AS_OF` is passed in and never read.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPACE = ROOT / "space"
RUNTIME = ROOT / "runtime"
KERNEL_PATH = SPACE / "meeting_relevance.py"
VOCABULARY_PATH = SPACE / "meeting-vocabulary.json"
CONTRACT_PATH = SPACE / "meeting-bundle-contract.yaml"


def _load(name: str, path: Path):
    """The by-path loader, cached in `sys.modules`.

    Without the cache this module would hold a second `meeting_bundle` object,
    and the `Refused` it raises would not be the `Refused` a caller catches —
    the same reason the producer's own loader caches.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bundle = _load("serveros_space_meeting_bundle_under_test", SPACE / "meeting_bundle.py")
kernel = _load("serveros_space_meeting_relevance_under_test", KERNEL_PATH)

VOCABULARY = kernel.load_vocabulary(VOCABULARY_PATH)

# Synthetic throughout.
TENANT = "tenant:t_synthetic"
CHAT = "chat:oc_support_0001"
MEETING = "meeting:m_support_0001"
OTHER_MEETING = "meeting:m_support_0002"
PROJECT = "SRV-0001"
OTHER_PROJECT = "SRV-0002"
AS_OF = "2026-09-22T00:00:00Z"

#: The four words, pinned HERE and nowhere else in the repository.
#:
#: The authority for them is the machine policy, which lives outside this
#: repository (`ability-management/instructions/machine-policy-invariant.md`,
#: measured 2026-09-22: "MeetingBundles require minute, audio, transcript, and
#: summary as one unit."). A test may hold a literal — that is what a test is —
#: but a contract that copied it would keep answering after the requirement
#: changed, which is why `space/meeting-bundle-contract.yaml` quotes the
#: sentence and names its source instead. A predicate measured against a file
#: on this host would be a test that fails on the next node.
POLICY_MEMBERS = ["audio", "minute", "summary", "transcript"]

PRESENT_STATES = ("PRESENT", "PROCESSING", "FAILED", "ABSENT", "UNREADABLE")


def declaration(**overrides) -> dict:
    """A complete declaration, with the one field a test is about overridden.

    Built fresh per call: `assemble` is not expected to mutate its input, and a
    module-level fixture would let a test that does pass by hiding behind a
    test that did it first.
    """
    document = {
        "meeting_ref": MEETING,
        "scope": {"tenant_ref": TENANT, "chat_ref": CHAT, "meeting_ref": MEETING},
        "members": [
            {"kind": "recording", "state": "PRESENT", "object_ref": "obj:rec",
             "revision": "rev:rec", "project_ref": PROJECT},
            {"kind": "transcript", "state": "PRESENT", "object_ref": "obj:tr",
             "revision": "rev:tr", "project_ref": PROJECT},
            {"kind": "summary", "state": "PRESENT", "object_ref": "obj:sum",
             "revision": "rev:sum", "project_ref": PROJECT},
            {"kind": "minute", "state": "PRESENT", "object_ref": "obj:min",
             "revision": "rev:min", "project_ref": PROJECT},
        ],
        "signals": {"title": "a synthetic meeting", "language": "zh"},
        "policies": [],
    }
    document.update(overrides)
    return document


def member(kind: str, state: str, **overrides) -> dict:
    entry = {"kind": kind, "state": state}
    if state == "PRESENT":
        entry.update({"object_ref": f"obj:{kind}", "revision": f"rev:{kind}"})
    entry.update(overrides)
    return entry


def assembled(document: dict) -> dict:
    return bundle.assemble(document, observed_at=AS_OF, vocabulary=VOCABULARY)


# ---------------------------------------------------------------------------
# the contract, against the vocabulary it joins with
# ---------------------------------------------------------------------------
class TheContractAgainstTheVocabularyTest(unittest.TestCase):
    def test_the_four_members_are_the_policys_four(self):
        """Order is not the policy's — it says "minute, audio, transcript,
        summary" in prose and a set is what the requirement is. A test that
        compared the list as written would fail on a reordering that changes
        nothing."""
        self.assertEqual(sorted(bundle.required_members()), sorted(POLICY_MEMBERS))

    def test_every_maching_kind_is_a_declared_object_type(self):
        """The contract's mapping, checked against the vocabulary on every read.

        `member_kinds` is where a vocabulary that dropped one of these four
        becomes a refusal. Asserting it here as well is not redundant: this is
        the test that fails when someone edits the CONTRACT to a kind the
        vocabulary does not declare, which `member_kinds` would also catch but
        only from the path that calls it.
        """
        declared = kernel._values(VOCABULARY, "object_type")
        mapping = bundle.member_kinds(VOCABULARY)
        self.assertEqual(sorted(mapping), sorted(POLICY_MEMBERS))
        for word, kind in mapping.items():
            with self.subTest(member=word):
                self.assertIn(kind, declared)

    def test_a_vocabulary_without_the_audio_kind_is_a_refusal(self):
        """The failure this check exists for, induced.

        `audio` maps to `recording`. A vocabulary edited to call it something
        else must produce a refusal naming it — not a bundle that reads COMPLETE
        while naming a member the kernel does not know.
        """
        thinned = json.loads(json.dumps(VOCABULARY))
        thinned["dimensions"]["object_type"]["values"] = [
            value for value in thinned["dimensions"]["object_type"]["values"]
            if value != "recording"]
        with self.assertRaises(bundle.Refused) as caught:
            bundle.member_kinds(thinned)
        self.assertIn("recording", str(caught.exception))

    def test_the_presence_states_are_the_contracts_five(self):
        self.assertEqual(sorted(bundle.presence_states()), sorted(PRESENT_STATES))
        self.assertEqual(set(bundle.NOT_PRESENT_STATES),
                         set(PRESENT_STATES) - {"PRESENT"})

    def test_the_two_axes_are_dimensions_the_kernel_actually_reads(self):
        """Read from the kernel, not from the contract.

        The contract's `axes` block names `ownership` and `hold`; if the kernel
        renamed one, the producer would validate a word nobody reads and send a
        word the kernel rejects. So the assertion is against the kernel's own
        source text, which is the thing that changes when a dimension does.
        """
        source = KERNEL_PATH.read_text(encoding="utf-8")
        for name in bundle.axes_names():
            with self.subTest(axis=name):
                self.assertIn(f'"{name}"', source)
                self.assertTrue(kernel._values(VOCABULARY, name),
                                f"the vocabulary declares no `{name}` dimension")


# ---------------------------------------------------------------------------
# the five states, and what makes a bundle
# ---------------------------------------------------------------------------
class PresenceTest(unittest.TestCase):
    def test_all_four_present_is_a_bundle(self):
        result = assembled(declaration())
        self.assertEqual(result["reading"]["state"], "COMPLETE")
        self.assertEqual(result["reading"]["not_present"],
                         {word: "PRESENT" for word in bundle.required_members()})
        self.assertIsNotNone(result["bundle"])

    def test_each_missing_member_makes_it_incomplete_and_says_which(self):
        mapping = bundle.member_kinds(VOCABULARY)
        for word in bundle.required_members():
            for state in ("PROCESSING", "FAILED", "ABSENT", "UNREADABLE"):
                members = [member(mapping[name], "PRESENT") for name in mapping]
                members[bundle.required_members().index(word)] = member(
                    mapping[word], state)
                with self.subTest(member=word, state=state):
                    result = assembled(declaration(members=members))
                    self.assertEqual(result["reading"]["state"], "INCOMPLETE")
                    self.assertEqual(result["reading"]["not_present"][word], state)
                    self.assertIn(state, result["reading"]["because"])
                    self.assertIn(word, result["reading"]["because"])

    def test_the_four_non_present_states_are_kept_apart(self):
        """The distinction the plan asks for by name.

        Four declarations that differ only in the audio member's state must
        produce four different readings. A producer that folded them into
        `missing` would pass every other test in this class.
        """
        seen = {}
        for state in bundle.NOT_PRESENT_STATES:
            result = assembled(declaration(members=[
                member("recording", state),
                member("transcript", "PRESENT"),
                member("summary", "PRESENT"),
                member("minute", "PRESENT"),
            ]))
            seen[state] = (result["reading"]["not_present"]["audio"],
                           result["reading"]["because"])
        self.assertEqual(len(set(seen.values())), 4, seen)
        for state, (reported, because) in seen.items():
            with self.subTest(state=state):
                self.assertEqual(reported, state)
                self.assertIn(state, because)

    def test_an_incomplete_reading_puts_no_question_to_the_kernel(self):
        """`bundle is None` is the design, not a convenience.

        Handing the kernel a bundle missing its audio produces a decision record
        about material that is not there, and a decision record is the thing
        somebody later acts on.
        """
        result = assembled(declaration(members=[
            member("recording", "PROCESSING"),
            member("transcript", "PRESENT"),
            member("summary", "PRESENT"),
            member("minute", "PRESENT"),
        ]))
        self.assertIsNone(result["bundle"])
        # The reading is what there is: no derived `bundle_ref`, which is the
        # field the kernel names a bundle by and the one a caller would use to
        # assemble a request of their own.
        self.assertNotIn("bundle_ref", result["reading"])
        self.assertEqual(sorted(result), ["bundle", "reading"])

    def test_a_present_member_without_a_revision_is_refused(self):
        """`PRESENT` is a claim about material that can be read."""
        for missing in ("revision", "object_ref"):
            document = declaration()
            document["members"][0].pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(bundle.Refused) as caught:
                    assembled(document)
                self.assertIn(missing, str(caught.exception))

    def test_an_undeclared_presence_state_is_refused(self):
        document = declaration(members=[member("recording", "MAYBE"),
                                        member("transcript", "PRESENT"),
                                        member("summary", "PRESENT"),
                                        member("minute", "PRESENT")])
        with self.assertRaises(bundle.Refused) as caught:
            assembled(document)
        self.assertIn("MAYBE", str(caught.exception))

    def test_a_meeting_that_never_declared_a_member_reads_absent(self):
        """"Never declared" and "declared ABSENT" are the same reading here.

        `not_present` reports the state, and `ABSENT` is the contract's word for
        "never declared by the source" — so a member the declaration omits is
        reported with it rather than with a null a renderer would have to guess
        at.
        """
        result = assembled(declaration(members=[member("transcript", "PRESENT"),
                                                member("summary", "PRESENT"),
                                                member("minute", "PRESENT")]))
        self.assertEqual(result["reading"]["state"], "INCOMPLETE")
        self.assertEqual(result["reading"]["not_present"]["audio"], "ABSENT")
        self.assertIsNone(result["reading"]["members"]["audio"])

    def test_a_declared_object_that_is_not_a_member_is_reported_not_dropped(self):
        result = assembled(declaration(members=[
            member("recording", "PRESENT"), member("transcript", "PRESENT"),
            member("summary", "PRESENT"), member("minute", "PRESENT"),
            {"kind": "document", "object_ref": "obj:doc"},
        ]))
        self.assertEqual(result["reading"]["state"], "COMPLETE")
        self.assertEqual([entry["kind"] for entry in result["reading"]["other_members"]],
                         ["document"])

    def test_two_members_of_one_kind_are_refused(self):
        """A bundle has one of each, and picking one is not the reader's job.

        Two recordings is an ordinary thing for a meeting to produce and not an
        ordinary thing to put in ONE bundle: which of the two the audio is would
        be a choice this reader made on the declaration's behalf.
        """
        document = declaration()
        document["members"].append(
            {"kind": "recording", "state": "PRESENT", "object_ref": "obj:rec2",
             "revision": "rev:rec2"})
        with self.assertRaises(bundle.Refused) as caught:
            assembled(document)
        self.assertIn("recording", str(caught.exception))


# ---------------------------------------------------------------------------
# what may be read now, apart from whether the unit is complete
# ---------------------------------------------------------------------------
#: Synthetic segments. `tie` is the producer's word for "this segment is about
#: that work item"; the reader passes it through and never invents one.
SEGMENTS = [{"tie": "WI-0001", "text": "a synthetic segment"},
            {"tie": None, "text": "a synthetic aside"}]


class ReadableTest(unittest.TestCase):
    def test_a_transcript_is_readable_while_the_audio_is_still_processing(self):
        """The plan's P2 line as a test: 已有合法 transcript 可先读，不等 MP3 或全套成员.

        Before this, the transcript's own segments were unreachable in exactly
        this case: `bundle` is None for every reading that is not COMPLETE, and
        the only place the segments were put was inside that bundle. "Not a unit
        yet" was being read as "nothing here is readable yet".
        """
        result = assembled(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PROCESSING"),
                     member("transcript", "PRESENT"),
                     member("summary", "ABSENT"),
                     member("minute", "PRESENT")]))
        reading = result["reading"]
        self.assertEqual(reading["state"], "INCOMPLETE")
        self.assertIsNone(result["bundle"])
        self.assertEqual(reading["readable"]["present"], ["transcript", "minute"])
        self.assertEqual(reading["readable"]["content"],
                         {"transcript_segments": SEGMENTS})
        self.assertEqual(reading["readable"]["withheld"], {})

    def test_reading_the_material_is_not_being_handed_a_bundle(self):
        """`readable` carries nothing the kernel names a bundle by.

        The two are different acts and the shape keeps them apart: a reader who
        wanted a decision still waits for four members, and there is no
        `bundle_ref` here to build a request of their own from.
        """
        result = assembled(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PROCESSING"),
                     member("transcript", "PRESENT"),
                     member("summary", "PRESENT"),
                     member("minute", "PRESENT")]))
        readable = result["reading"]["readable"]
        self.assertNotIn("bundle_ref", readable)
        self.assertNotIn("scope", readable)
        self.assertEqual(sorted(readable),
                         ["because", "content", "present", "withheld"])
        self.assertEqual(sorted(result), ["bundle", "reading"])
        self.assertIn("no question was put to the kernel", readable["because"])

    def test_the_readable_half_and_the_state_come_from_one_not_present_map(self):
        """A member handed over can never be one `state` called absent.

        Swept over every member and every state, because the failure this guards
        is the two halves being computed from two different readings of the same
        declaration — which no single case would show.
        """
        mapping = bundle.member_kinds(VOCABULARY)
        for word in bundle.required_members():
            for state in PRESENT_STATES:
                members = [member(mapping[name], "PRESENT") for name in mapping]
                members[bundle.required_members().index(word)] = member(
                    mapping[word], state)
                with self.subTest(member=word, state=state):
                    reading = assembled(declaration(
                        content={"transcript_segments": SEGMENTS},
                        members=members))["reading"]
                    present = reading["readable"]["present"]
                    for handed in present:
                        self.assertEqual(reading["not_present"][handed], "PRESENT")
                    self.assertEqual(
                        present,
                        [w for w in bundle.required_members()
                         if reading["not_present"][w] == "PRESENT"])

    def test_segments_attached_to_a_member_that_is_not_present_are_reported(self):
        """Attached, but for a transcript the declaration did not call present.

        Neither handed over nor dropped: a reader told only "no content" takes
        away that there was none, and the declaration's own inconsistency is a
        fact about it that somebody can act on.
        """
        result = assembled(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PRESENT"),
                     member("transcript", "PROCESSING"),
                     member("summary", "PRESENT"),
                     member("minute", "PRESENT")]))
        readable = result["reading"]["readable"]
        self.assertEqual(readable["present"], ["audio", "summary", "minute"])
        self.assertEqual(readable["content"], {})
        self.assertEqual(readable["withheld"], {"transcript_segments": "transcript"})

    def test_nothing_present_is_stated_and_not_an_empty_answer(self):
        result = assembled(declaration(members=[]))
        reading = result["reading"]
        self.assertEqual(reading["state"], "INCOMPLETE")
        self.assertIsNone(result["bundle"])
        self.assertEqual(reading["readable"]["present"], [])
        self.assertEqual(reading["readable"]["content"], {})
        self.assertIn("nothing is present to read yet", bundle.render(result))

    def test_a_complete_reading_hands_over_the_same_segments_the_bundle_carries(self):
        """One producer of the content, so the two halves cannot disagree about
        what a declaration's segments are."""
        result = assembled(declaration(content={"transcript_segments": SEGMENTS}))
        self.assertEqual(result["reading"]["state"], "COMPLETE")
        self.assertEqual(result["reading"]["readable"]["content"],
                         result["bundle"]["content"])
        self.assertEqual(result["bundle"]["content"]["transcript_segments"], SEGMENTS)

    def test_mixed_scope_hands_nothing_over(self):
        """The refusal there is about scope: the members name more than one
        meeting, and material whose meeting is undetermined is not read out
        under a meeting of this reader's choosing."""
        result = assembled(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PRESENT", meeting_ref=MEETING),
                     member("transcript", "PRESENT", meeting_ref=OTHER_MEETING),
                     member("summary", "PRESENT", meeting_ref=MEETING),
                     member("minute", "PRESENT", meeting_ref=MEETING)]))
        reading = result["reading"]
        self.assertEqual(reading["state"], "MIXED_SCOPE")
        self.assertIsNone(result["bundle"])
        self.assertEqual(reading["readable"]["content"], {})
        self.assertEqual(reading["readable"]["withheld"], {})
        self.assertIn("scope was refused", reading["readable"]["because"])
        # Listed, so a reader can see the material is there and is not being
        # handed over rather than concluding it is absent.
        self.assertIn("transcript", reading["readable"]["present"])

    def test_a_malformed_segments_shape_is_refused_on_both_paths(self):
        """The shape is checked once, so an incomplete reading cannot be the
        path that quietly reshapes content the complete one refuses."""
        for label, members in (
                ("complete", [member("recording", "PRESENT"),
                              member("transcript", "PRESENT"),
                              member("summary", "PRESENT"),
                              member("minute", "PRESENT")]),
                ("incomplete", [member("recording", "PROCESSING"),
                                member("transcript", "PRESENT"),
                                member("summary", "PRESENT"),
                                member("minute", "PRESENT")])):
            with self.subTest(reading=label):
                with self.assertRaises(bundle.Refused) as caught:
                    assembled(declaration(content={"transcript_segments": "not a list"},
                                          members=members))
                self.assertIn("transcript_segments", str(caught.exception))


class ContentMemberContractTest(unittest.TestCase):
    """Which member gates a content key is declared, not inferred from a name."""

    def test_every_content_key_is_gated_on_one_of_the_four_members(self):
        unit = bundle.required_members()
        mapping = bundle.content_members()
        self.assertTrue(mapping, "the contract declares no content_member at all")
        for key, word in mapping.items():
            with self.subTest(key=key):
                self.assertIn(word, unit)

    def test_a_content_key_gated_on_a_word_the_unit_is_not_made_of_is_refused(self):
        """The negative half is the point: a gate nobody can ever satisfy would
        otherwise be a key that is silently never readable."""
        original = bundle.contract()
        mutated = json.loads(json.dumps(original))
        mutated["content_member"]["values"] = {"transcript_segments": "recording"}
        bundle._CONTRACT_CACHE = mutated
        try:
            with self.assertRaises(bundle.Refused) as caught:
                bundle.content_members()
            self.assertIn("recording", str(caught.exception))
        finally:
            bundle._CONTRACT_CACHE = original

    def test_the_contract_says_what_the_readable_half_is_for(self):
        """The contract is where a producer learns the two answers are not one.
        A `readable` block that stopped explaining why would leave the next
        editor free to fold it back into `state`."""
        block = bundle.contract()["readable"]["_comment"]
        self.assertIn("可先读", block)


# ---------------------------------------------------------------------------
# one meeting, or two
# ---------------------------------------------------------------------------
class ScopeTest(unittest.TestCase):
    def test_members_of_two_meetings_are_mixed_scope_and_not_a_short_bundle(self):
        result = assembled(declaration(members=[
            member("recording", "PRESENT", meeting_ref=MEETING),
            member("transcript", "PRESENT", meeting_ref=MEETING),
            member("summary", "PRESENT", meeting_ref=OTHER_MEETING),
            member("minute", "PRESENT", meeting_ref=MEETING),
        ]))
        self.assertEqual(result["reading"]["state"], "MIXED_SCOPE")
        self.assertEqual(result["reading"]["stray_meeting_refs"], [OTHER_MEETING])
        self.assertIsNone(result["bundle"])
        self.assertIn(OTHER_MEETING, result["reading"]["because"])

    def test_every_member_present_and_two_meetings_is_still_not_complete(self):
        """A completeness count would call this COMPLETE.

        Four members, all PRESENT, and one of them belongs to another meeting.
        This pins the reading a per-member check cannot reach: the declaration
        is not a bundle with something wrong with it.
        """
        result = assembled(declaration(members=[
            member("recording", "PRESENT", meeting_ref=MEETING),
            member("transcript", "PRESENT", meeting_ref=MEETING),
            member("summary", "PRESENT", meeting_ref=OTHER_MEETING),
            member("minute", "PRESENT", meeting_ref=MEETING),
        ]))
        self.assertEqual(result["reading"]["state"], "MIXED_SCOPE")
        self.assertEqual(result["reading"]["not_present"],
                         {word: "PRESENT" for word in bundle.required_members()})

    def test_two_meetings_outranks_a_missing_member(self):
        """Which of the two readings wins, and why it is not observable above.

        A declaration can be BOTH short a member and made of two meetings, and
        then the order the two are decided in shows. `MIXED_SCOPE` is the right
        answer of the two: naming which member is missing would describe a
        bundle of one meeting that does not exist, and which meeting was meant
        is not this reader's to pick. The missing member is still REPORTED in
        `not_present` — the reading loses no fact, it just does not become an
        INCOMPLETE bundle.

        This test exists because the mutation harness found the gap: the first
        version of the test above used four PRESENT members, where both orders
        of the two branches produce MIXED_SCOPE, so a mutant that swapped them
        survived.
        """
        result = assembled(declaration(members=[
            member("recording", "PRESENT", meeting_ref=MEETING),
            member("transcript", "PRESENT", meeting_ref=MEETING),
            member("summary", "PRESENT", meeting_ref=OTHER_MEETING),
            member("minute", "ABSENT", meeting_ref=MEETING),
        ]))
        self.assertEqual(result["reading"]["state"], "MIXED_SCOPE")
        self.assertEqual(result["reading"]["stray_meeting_refs"], [OTHER_MEETING])
        self.assertEqual(result["reading"]["not_present"]["minute"], "ABSENT")
        self.assertIsNone(result["bundle"])
        self.assertNotIn("MINUTE", result["reading"]["because"].upper())
        self.assertIn(OTHER_MEETING, result["reading"]["because"])

    def test_a_wildcard_scope_id_is_refused_by_the_kernels_own_rule(self):
        for wildcard in kernel.WILDCARDS:
            with self.subTest(wildcard=wildcard):
                with self.assertRaises(bundle.Refused):
                    assembled(declaration(scope={"tenant_ref": wildcard,
                                                 "meeting_ref": MEETING}))

    def test_the_wildcard_rule_is_read_from_the_kernel_not_copied(self):
        """A second copy of a refusal drifts from the one the kernel enforces."""
        source = (SPACE / "meeting_bundle.py").read_text(encoding="utf-8")
        self.assertIn("kernel.WILDCARDS", source)
        self.assertNotIn('WILDCARDS = (', source)

    def test_the_bundle_ref_derives_from_the_meeting_and_never_from_a_clock(self):
        self.assertEqual(assembled(declaration())["bundle"]["bundle_ref"],
                         f"bundle:{MEETING}")

    def test_a_declared_bundle_ref_is_used_and_still_checked(self):
        result = assembled(declaration(bundle_ref="bundle:declared_0001"))
        self.assertEqual(result["bundle"]["bundle_ref"], "bundle:declared_0001")
        with self.assertRaises(bundle.Refused):
            assembled(declaration(bundle_ref="bundle:*"))

    def test_an_unnamed_bundle_is_refused_rather_than_invented(self):
        """A generated id would change per run and the kernel's records would
        not be comparable between two readings of one meeting."""
        document = declaration(bundle_ref=None)
        document.pop("meeting_ref")
        document["scope"] = {"tenant_ref": TENANT, "chat_ref": CHAT}
        with self.assertRaises(bundle.Refused) as caught:
            assembled(document)
        self.assertIn("bundle_ref", str(caught.exception))

    def test_the_reading_carries_an_instant_that_was_passed_in(self):
        result = assembled(declaration())
        self.assertEqual(result["reading"]["observed_at"], AS_OF)
        with self.assertRaises(bundle.Refused):
            bundle.assemble(declaration(), observed_at="2026-09-22", vocabulary=VOCABULARY)


# ---------------------------------------------------------------------------
# the project axis
# ---------------------------------------------------------------------------
class ProjectAxisTest(unittest.TestCase):
    def test_one_project_is_single_and_two_are_multiple(self):
        self.assertEqual(assembled(declaration())["reading"]["project_reading"],
                         "SINGLE")
        document = declaration()
        document["members"][1]["project_ref"] = OTHER_PROJECT
        result = assembled(document)
        self.assertEqual(result["reading"]["project_reading"], "MULTIPLE")
        self.assertEqual(result["reading"]["projects"], [PROJECT, OTHER_PROJECT])

    def test_no_project_declared_is_undeclared_and_not_an_empty_list(self):
        """`UNDECLARED` and `[]` are different facts about a bundle somebody may
        later act on, so the axis is reported and never defaulted."""
        document = declaration()
        for entry in document["members"]:
            entry.pop("project_ref")
        result = assembled(document)
        self.assertEqual(result["reading"]["project_reading"], "UNDECLARED")
        self.assertEqual(result["reading"]["projects"], [])

    def test_the_source_is_never_asked_to_pick_one(self):
        """多项目 is answered by reporting the set, and the reading says which
        reading it is so a consumer does not have to infer it from the list."""
        document = declaration()
        document["members"][1]["project_ref"] = OTHER_PROJECT
        document["members"][2]["project_ref"] = OTHER_PROJECT
        result = assembled(document)
        self.assertEqual(result["reading"]["project_reading"], "MULTIPLE")
        self.assertIn(PROJECT, result["reading"]["projects"])


# ---------------------------------------------------------------------------
# the two axes, and the round's own defect
# ---------------------------------------------------------------------------
class AxesTest(unittest.TestCase):
    def test_an_axis_the_declaration_did_not_carry_is_omitted_from_the_bundle(self):
        """The fix, as a test.

        The kernel's `_axes` reads an absent `ownership` as `UNKNOWN` and
        validates what it IS handed against its own declared set. So the
        producer's job when nobody said is to say nothing. Writing
        `"UNDECLARED"` here built a bundle every COMPLETE declaration handed to
        the kernel was rejected — see `TheBundleReachesTheKernelTest`.
        """
        result = assembled(declaration())
        self.assertNotIn("ownership", result["bundle"])
        self.assertNotIn("hold", result["bundle"])
        self.assertEqual(result["reading"]["axes"],
                         {"ownership": "UNDECLARED", "hold": "UNDECLARED"})

    def test_undeclared_in_the_reading_is_not_a_value_the_kernel_declares(self):
        """If the kernel ever declared `UNDECLARED`, the omission above would
        stop being a distinction and start being a lost fact."""
        for name in bundle.axes_names():
            with self.subTest(axis=name):
                self.assertNotIn("UNDECLARED", kernel._values(VOCABULARY, name))

    def test_a_declared_axis_reaches_the_bundle_unchanged(self):
        result = assembled(declaration(ownership="SHARED", hold="NONE"))
        self.assertEqual(result["bundle"]["ownership"], "SHARED")
        self.assertEqual(result["bundle"]["hold"], "NONE")
        self.assertEqual(result["reading"]["axes"],
                         {"ownership": "SHARED", "hold": "NONE"})

    def test_a_producer_side_word_for_nobody_said_is_refused(self):
        """`UNDECLARED` came from the contract's project axis and was carried
        into the kernel's by mistake. The producer must refuse it now, and the
        refusal must name the words the kernel does declare."""
        with self.assertRaises(bundle.Refused) as caught:
            assembled(declaration(ownership="UNDECLARED"))
        message = str(caught.exception)
        self.assertIn("UNDECLARED", message)
        self.assertIn("UNKNOWN", message)

    def test_a_vocabulary_without_an_axis_is_a_refusal(self):
        """The kernel validates every value it is handed against the dimension,
        so a vocabulary that lost one rejects every bundle. The producer can see
        that and says so."""
        thinned = json.loads(json.dumps(VOCABULARY))
        thinned["dimensions"].pop("hold")
        with self.assertRaises(bundle.Refused) as caught:
            bundle.assemble(declaration(), observed_at=AS_OF, vocabulary=thinned)
        self.assertIn("hold", str(caught.exception))

    def test_no_axis_name_is_written_as_a_literal_in_the_module(self):
        """Read from the contract on every path, never spelled out here.

        Asserted on the whole module text rather than on `assemble`, because the
        defect this replaces was one line in one function and the next one will
        be somewhere else: a literal axis name in this producer is a second copy
        of a closed set, wherever it is written.
        """
        source = (SPACE / "meeting_bundle.py").read_text(encoding="utf-8")
        self.assertIn("axes_names()", source)
        for name in bundle.axes_names():
            with self.subTest(axis=name):
                self.assertNotIn(f'"{name}"', source)
                self.assertNotIn(f"'{name}'", source)


# ---------------------------------------------------------------------------
# the integration: the bundle this producer builds, at the kernel
# ---------------------------------------------------------------------------
class TheBundleReachesTheKernelTest(unittest.TestCase):
    """"It assembles" and "the kernel accepts it" are different claims.

    Every other test in this file can pass while the bundle is one the kernel
    refuses. That is not hypothetical: it is what this round found — all four
    PRESENT, `state: COMPLETE`, and `serverosctl` exiting 2 with
    `unknown ownership: 'UNDECLARED'`.
    """

    def test_a_complete_declaration_is_a_bundle_the_kernel_answers(self):
        result = assembled(declaration())
        request = {"kind": "relevance", "bundle": result["bundle"],
                   "policies": [], "as_of": AS_OF}
        record = kernel.decide(request, VOCABULARY)
        self.assertIn(record["decision"], kernel.DECISIONS)
        self.assertEqual(record["axes"]["ownership"], "UNKNOWN")
        self.assertEqual(record["axes"]["hold"], "UNKNOWN")
        self.assertEqual(record["axes"]["deletability"], "NOT_EVALUATED")

    def test_the_same_bundle_answered_the_same_way_twice(self):
        """Nothing here reads a clock or a global, so two reads of one
        declaration must be byte-identical."""
        first = assembled(declaration())
        second = assembled(declaration())
        self.assertEqual(json.dumps(first, sort_keys=True),
                         json.dumps(second, sort_keys=True))

    def test_a_declared_axis_survives_the_kernel_intact(self):
        result = assembled(declaration(ownership="OTHER_OWNER", hold="LEGAL_HOLD"))
        record = kernel.decide({"kind": "relevance", "bundle": result["bundle"],
                                "policies": [], "as_of": AS_OF}, VOCABULARY)
        self.assertEqual(record["axes"]["ownership"], "OTHER_OWNER")
        self.assertEqual(record["axes"]["hold"], "LEGAL_HOLD")

    def test_the_producer_emits_every_field_the_kernel_reads(self):
        """Derived from the kernel's source, not from a list written here.

        A future kernel field that reads `bundle.get("…")` fails this until the
        producer emits it — or until `OPTIONAL` names it with a reason. That is
        the drift this catches: the kernel grew an axis or a block and the one
        producer in the tree kept building the older shape.
        """
        source = KERNEL_PATH.read_text(encoding="utf-8")
        reads = set(re.findall(r'bundle\.get\("([a-z_]+)"', source))
        reads |= set(re.findall(r'bundle\["([a-z_]+)"\]', source))
        self.assertTrue(reads, "the kernel's bundle reads were not found by "
                              "this regex; the test is measuring nothing")
        optional = {"ownership", "hold"}   # `_axes` defaults both from None
        self.assertLessEqual(reads - optional, set(assembled(declaration())["bundle"]))

    def test_every_required_member_reaches_the_kernel_as_a_declared_kind(self):
        result = assembled(declaration())
        declared = kernel._values(VOCABULARY, "object_type")
        for word, kind in bundle.member_kinds(VOCABULARY).items():
            with self.subTest(member=word):
                self.assertIn(kind, declared)

    def test_an_incomplete_reading_never_reaches_this_kernel(self):
        """Stated as an integration fact as well: the kernel is never asked
        about a bundle that is not one unit, because there is no bundle."""
        result = assembled(declaration(members=[
            member("recording", "UNREADABLE"),
            member("transcript", "PRESENT"),
            member("summary", "PRESENT"),
            member("minute", "PRESENT"),
        ]))
        self.assertIsNone(result["bundle"])
        with self.assertRaises(kernel.Rejected) as caught:
            # What a caller would have to do to reach the kernel anyway.
            kernel.decide({"kind": "relevance", "bundle": result["bundle"]},
                          VOCABULARY)
        self.assertIn("bundle", str(caught.exception))


# ---------------------------------------------------------------------------
# the wiring
# ---------------------------------------------------------------------------
def cli(*args: str) -> subprocess.CompletedProcess:
    """The real process, as `test_space_cli.py` does and for the same reason:
    an exit code is a fact about the shell, not about a return value."""
    return subprocess.run([sys.executable, str(RUNTIME / "cli.py"), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def sidecars(document: dict) -> Path:
    """A declaration in a directory this module keeps alive.

    A `TemporaryDirectory` that has been collected is the classic way for a
    "the CLI refused" test to pass because the file was missing.
    """
    directory = Path(tempfile.mkdtemp(prefix="meeting-bundle-"))
    KEEP.append(directory)
    path = directory / "sidecars.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


KEEP: list[Path] = []


class CliTest(unittest.TestCase):
    def test_a_complete_declaration_answers_zero_and_prints_the_reading(self):
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration())),
                   "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        out = proc.stdout.decode()
        self.assertIn("state       COMPLETE", out)
        self.assertIn(f"meeting_ref={MEETING}", out)
        self.assertIn(f"observed    {AS_OF}", out)
        self.assertIn("decision    ", out)

    def test_the_reading_is_printed_before_the_decision(self):
        """A decision made about a bundle assembled from the wrong members is a
        decision about another meeting, and a reader who sees only the decision
        has no way to notice."""
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration())),
                   "--observed-at", AS_OF)
        out = proc.stdout.decode()
        self.assertLess(out.index("state       COMPLETE"), out.index("decision "))

    def test_an_incomplete_declaration_exits_two_and_puts_no_question(self):
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration(members=[
            member("recording", "PROCESSING"),
            member("transcript", "PRESENT"),
            member("summary", "PRESENT"),
            member("minute", "PRESENT")]))),
            "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2, proc.stdout.decode())
        out = proc.stdout.decode()
        self.assertIn("state       INCOMPLETE", out)
        self.assertIn("PROCESSING", out)
        self.assertNotIn("decision ", out)
        self.assertIn("bundle      (none", out)

    def test_the_cli_prints_what_is_readable_and_still_puts_no_question(self):
        """The reading a person gets while the audio is still coming: material
        with no decision, and an exit code that says so."""
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PROCESSING"),
                     member("transcript", "PRESENT"),
                     member("summary", "PRESENT"),
                     member("minute", "PRESENT")]))),
            "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2, proc.stdout.decode())
        out = proc.stdout.decode()
        self.assertIn("state       INCOMPLETE", out)
        self.assertIn("readable    transcript, summary, minute", out)
        self.assertNotIn("withheld", out)
        self.assertIn("bundle      (none", out)

    def test_the_cli_prints_a_withheld_key_with_the_state_that_withheld_it(self):
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PRESENT"),
                     member("transcript", "PROCESSING"),
                     member("summary", "PRESENT"),
                     member("minute", "PRESENT")]))),
            "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2, proc.stdout.decode())
        out = proc.stdout.decode()
        self.assertIn("withheld  transcript_segments", out)
        self.assertIn("PROCESSING", out)

    def test_the_json_reading_carries_the_segments_while_no_bundle_does(self):
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration(
            content={"transcript_segments": SEGMENTS},
            members=[member("recording", "PROCESSING"),
                     member("transcript", "PRESENT"),
                     member("summary", "PRESENT"),
                     member("minute", "PRESENT")]))),
            "--observed-at", AS_OF, "--json")
        self.assertEqual(proc.returncode, 2, proc.stderr.decode())
        payload = json.loads(proc.stdout.decode())
        self.assertIsNone(payload["bundle"])
        self.assertEqual(sorted(payload), ["bundle", "reading"])
        self.assertEqual(payload["reading"]["readable"]["content"],
                         {"transcript_segments": SEGMENTS})

    def test_sidecars_without_an_observed_at_is_refused(self):
        """The reading is a statement about a meeting at an instant; one taken
        from the clock cannot be compared against a recorded one."""
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration())))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--observed-at", proc.stderr.decode())
        self.assertEqual(proc.stdout.decode(), "")

    def test_a_producer_refusal_exits_two_and_names_the_word(self):
        proc = cli("space", "meeting",
                   "--sidecars", str(sidecars(declaration(ownership="UNDECLARED"))),
                   "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("UNDECLARED", proc.stderr.decode())
        self.assertEqual(proc.stdout.decode(), "")

    def test_a_sidecars_file_that_is_not_json_exits_two(self):
        path = sidecars(declaration())
        path.write_text("{not json", encoding="utf-8")
        proc = cli("space", "meeting", "--sidecars", str(path), "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2)
        self.assertIn(str(path), proc.stderr.decode())

    def test_the_two_inputs_are_mutually_exclusive(self):
        """`--request` and `--sidecars` are different kinds of input: one is a
        bundle somebody wrote, the other is a meeting's material. Accepting both
        would make which one won a matter of argument order."""
        proc = cli("space", "meeting", "--request", "x.json",
                   "--sidecars", "y.json", "--observed-at", AS_OF)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not allowed with", proc.stderr.decode())

    def test_the_sidecars_path_keeps_the_kind_override(self):
        """Re-asking the same bundle the other question is a thing a person
        does, and it should not require editing the file."""
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration())),
                   "--observed-at", AS_OF, "--kind", "cleanup")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("policy", proc.stderr.decode())

    def test_the_reading_is_the_producers_and_the_decision_is_the_kernels(self):
        """The `--json` document carries the producer's reading and the kernel's
        own record, not a third shape invented at the CLI.

        Compared against a fresh `decide()` on the emitted bundle, so a CLI that
        summarised or re-keyed the record fails here rather than downstream,
        where the record is the thing a receipt is written from.
        """
        proc = cli("space", "meeting", "--sidecars", str(sidecars(declaration())),
                   "--observed-at", AS_OF, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        payload = json.loads(proc.stdout.decode())
        self.assertEqual(payload["reading"]["schema"], bundle.BUNDLE_SCHEMA)
        self.assertEqual(sorted(payload), ["bundle", "decision", "reading"])
        self.assertEqual(payload["decision"], kernel.decide(
            {"kind": "relevance", "bundle": payload["bundle"], "policies": [],
             "as_of": AS_OF}, VOCABULARY))
        self.assertEqual(payload["bundle"]["bundle_ref"], f"bundle:{MEETING}")


if __name__ == "__main__":
    unittest.main()
