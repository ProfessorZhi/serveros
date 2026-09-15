#!/usr/bin/env python3
"""Tests for the ability-pack closure rule (§35/§62).

`abilities/pack-contract.yaml` declared, in as many words, that a pack naming
an ability which does not exist "is refused at build time, not carried with a
marker beside it". Nothing read the file. A grep for its own name over the
repository returned one hit — the contract. Three of the four packs it declares
named a required member that resolved to nothing, and the only place that
surfaced was a reader who happened to look.

So these tests are about two things, and the second is the one that matters:

  * the verdicts `abilities/packs.py` returns, and
  * that something CALLS it. A correct function with no caller passes every
    test of the function, which is precisely how the rule above stayed true and
    unimplemented for as long as it did. `BuildWiringTests` asserts against
    `scripts/build.py`'s own source, because the failure it guards against is a
    call site that quietly disappears — and a test of `packs.py` alone cannot
    see that.

The live contract is checked against the real catalogue at the end. It is the
only test here that reads a file outside `tests/`, and it is here because a
synthetic contract cannot catch the failure this whole file exists for: the
packs that are actually declared naming abilities that are actually missing.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
ABILITIES = REPO / "abilities"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


packs = _load("serveros_abilities_packs", ABILITIES / "packs.py")
# Loaded under its own name so it does not collide with `test_build.py`'s copy.
# The builder is still loaded here, but no longer because it owns part of the
# pin rule: `_pin_still_describes()` lived there for one revision and this file
# is where the cost of that showed up — see `OnePinRuleTests`. It is loaded for
# the wiring assertions in `BuildWiringTests`, which read the builder's source.
build = _load("serveros_build_for_pack_pins", REPO / "scripts" / "build.py")

# A revision that is not special: nothing here touches git, and the pin is
# compared as a string. The LIVE revision is in `LiveContractTests`.
REV = "95c31ed8a0a49f048ff0f89d3fabd0c6ba7fd2d5"
CATALOGUE = ["work-management", "runtime-management", "test-management",
             "shared-memory", "fleet-coordination"]


def a_pack(pack_id="pack:one", members=None, **extra) -> dict:
    return {
        "id": pack_id, "name": "One", "purpose": "to exist",
        "authority": {"repo": "ProfessorZhi/ability-management", "revision": REV},
        "members": members if members is not None else [
            {"ability": "ability:work-management", "role": "required"}],
        **extra,
    }


def a_contract(*packs_declared) -> dict:
    return {"schema": "serveros/ability-pack.v1", "packs": list(packs_declared)}


def _workspace_roots(start: Path) -> list[Path]:
    """Ancestors that look like a workspace: they hold both `src/` and `worktrees/`.

    The same test `test_architecture_check.py` uses to find the evaluator, kept
    as the same shape on purpose: two tests searching for the same repository by
    two different rules is how one of them starts finding a different checkout.
    """
    for parent in [start, *start.parents]:
        if (parent / "src").is_dir() and (parent / "worktrees").is_dir():
            return [parent]
    return []


def _ability_repos() -> list[Path]:
    """Every git work tree that might be able to answer for the pin.

    Searched rather than hard-coded, because the checkout that can answer for
    the pinned revision is an Item worktree, and worktrees move. Named roots
    first: `SERVEROS_ABILITY_REPO` for a caller that already knows, then the two
    roots this product declares for repositories — `workspace.src` and
    `workspace.worktrees` in `fleet/profiles.yaml`.

    Those two roots are found by looking for a workspace, not by counting
    parents. The previous version counted: it computed `<repo>/../../` and
    `<repo>/../../../src`, which are the right directories only for a layout
    where the repo sits directly under the workspace. On this machine's own
    layout — `workspace/src/serveros` — both candidates missed, the search
    returned no repository at all, and five tests failed with a message blaming
    ability-management for a file that was there the whole time. A discovery
    that finds nothing has to be a failure somewhere, and the place it failed
    was the one place it could not be seen from.
    """
    roots = []
    named = os.environ.get("SERVEROS_ABILITY_REPO")
    if named:
        roots.append(Path(named))
    for ws in _workspace_roots(REPO):
        roots += [ws / "src" / "ability-management",
                  ws / "worktrees" / "ability-management"]
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        # A work tree is `.git` a directory (a checkout) or a file (a linked
        # worktree). Both hold the repository's objects, and either can resolve
        # a commit the other created.
        if (root / ".git").exists() or (root / "abilities-manifest.yaml").is_file():
            found.append(root)
        found += [p for p in sorted(root.iterdir()) if p.is_dir() and (p / ".git").exists()]
    return found


def _manifest_at(revision: str) -> tuple[dict, Path] | None:
    """The manifest as a git object at `revision`, from the first repo that has it.

    Read from the object and not from a checkout, the same way
    `scripts/build.py` reads it. On this machine the difference is live: the
    same four packs give different answers against the canonical checkout and
    against the Item worktree, and a test that read a working tree would be
    asserting about whichever one happened to be checked out.
    """
    for repo in _ability_repos():
        proc = subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:abilities-manifest.yaml"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            return yaml.safe_load(proc.stdout.decode()), repo
    return None


def resolve_one(pack, catalogue=CATALOGUE, revision=REV):
    return packs.resolve(a_contract(pack), catalogue, catalogue_revision=revision)


# Where the published pack declarations live, and the revision that first
# carried them. The pin inside that file names `7bc7687`: the declarations were
# moved out of the ServerOS contract verbatim, so the catalogue they were
# validated against is the one `7bc7687` already had. `0091d8d` adds this file
# and changes no ability, which is WHY the pins are allowed to be an ancestor
# of the revision they are read from — see `AncestorPinTests` and
# `publishing.ancestor_rule_in` in the contract.
PACK_DECLARATIONS_FILE = "ability-packs.yaml"
DECLARATIONS_REV = "0091d8d72546b0c7b45bcaa5f2e3016bbb0124f6"


def _declarations_at(revision: str) -> tuple[list[dict], Path] | None:
    """The pack declarations as a git object at `revision`.

    The exact counterpart of `_manifest_at()` — one reads the catalogue, one
    reads the packs that name members of it, and they read the same repository
    the same way for the same reason. Read from the object and never from a
    checkout: the declarations moved into the repository whose catalogue they
    name, so a checkout would be a working copy of the very thing being asked
    about.
    """
    for repo in _ability_repos():
        proc = subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:{PACK_DECLARATIONS_FILE}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0:
            document = yaml.safe_load(proc.stdout.decode())
            return document["packs"], repo
    return None


class MemberVerdictTests(unittest.TestCase):
    def test_a_member_that_exists_resolves(self):
        result = resolve_one(a_pack())
        self.assertEqual(result["packs"][0]["status"], "CLOSED")
        self.assertEqual(result["packs"][0]["resolved"][0]["name"], "work-management")

    def test_a_member_that_does_not_exist_is_named_not_counted(self):
        """The repair differs per member, so the answer has to name them."""
        result = resolve_one(a_pack(members=[
            {"ability": "ability:work-management", "role": "required"},
            {"ability": "ability:document-stewardship", "role": "required"}]))
        unresolved = result["packs"][0]["unresolved"]
        self.assertEqual([m["ability"] for m in unresolved],
                         ["ability:document-stewardship"])

    def test_an_unresolved_member_is_never_silently_dropped(self):
        """The contract's word for this is `never: silently dropped`. The count
        is asserted because a filter that dropped one would leave every other
        assertion in this file green."""
        pack = a_pack(members=[
            {"ability": "ability:work-management", "role": "required"},
            {"ability": "ability:ghost", "role": "optional"},
            {"ability": "ability:phantom", "role": "required"}])
        result = resolve_one(pack)["packs"][0]
        self.assertEqual(result["members_total"], 3)
        self.assertEqual(len(result["resolved"]) + len(result["unresolved"]), 3)

    def test_optional_members_are_reported_and_do_not_refuse(self):
        """`closure.scope` says enforced for members, reported for the optional
        ones — and `optional` has to mean something at build time or it is a
        comment."""
        result = resolve_one(a_pack(members=[
            {"ability": "ability:work-management", "role": "required"},
            {"ability": "ability:feishu-report", "role": "optional"}]))["packs"][0]
        self.assertEqual(result["status"], "CLOSED")
        self.assertFalse(result["refused"])
        self.assertEqual([m["name"] for m in result["unresolved_optional"]],
                         ["feishu-report"])

    def test_one_missing_required_member_is_partial(self):
        result = resolve_one(a_pack(members=[
            {"ability": "ability:work-management", "role": "required"},
            {"ability": "ability:ghost", "role": "required"}]))["packs"][0]
        self.assertEqual(result["status"], "PARTIAL")
        self.assertTrue(result["refused"])

    def test_every_required_member_missing_is_unavailable(self):
        """PARTIAL means the pack is usable and incomplete. With nothing left
        to use, saying PARTIAL would report a state the pack is not in."""
        result = resolve_one(a_pack(members=[
            {"ability": "ability:ghost", "role": "required"},
            {"ability": "ability:phantom", "role": "required"}]))["packs"][0]
        self.assertEqual(result["status"], "UNAVAILABLE")

    def test_a_pack_of_only_optional_members_closes(self):
        result = resolve_one(a_pack(members=[
            {"ability": "ability:work-management", "role": "optional"}]))["packs"][0]
        self.assertEqual(result["status"], "CLOSED")


class MemberIdTests(unittest.TestCase):
    """`rules`: "A pack names abilities. It never names a provider, a device or
    a runtime — those are resolved, and a pack that names one has decided
    something it cannot know." The prefix is checkable, so it is checked."""

    def test_a_member_that_is_not_an_ability_is_refused(self):
        """`provider:feishu-mcp` is the case the rule names. It is refused
        rather than stripped and looked up — stripping it would report a
        missing ability called `provider:feishu-mcp`, which is a wrong answer
        to a question nobody asked, in place of the refusal the rule calls
        for."""
        for bad in ("provider:feishu-mcp", "device:server-beijing",
                    "runtime:claude-code", "work-management"):
            with self.subTest(bad=bad):
                with self.assertRaises(packs.ContractInvalid):
                    resolve_one(a_pack(members=[{"ability": bad, "role": "required"}]))

    def test_the_prefix_is_refused_not_stripped(self):
        with self.assertRaises(packs.ContractInvalid):
            packs.member_name("provider:feishu-mcp")

    def test_an_empty_name_after_the_prefix_is_refused(self):
        with self.assertRaises(packs.ContractInvalid):
            packs.member_name("ability:")


class PackShapeTests(unittest.TestCase):
    def test_a_pack_carrying_a_path_is_refused(self):
        with self.assertRaises(packs.ContractInvalid):
            resolve_one(a_pack(path="skills/one"))

    def test_a_pack_with_its_own_version_is_refused(self):
        """The abilities inside a pack already carry revisions; a pack that
        also had a version would need releasing."""
        with self.assertRaises(packs.ContractInvalid):
            resolve_one(a_pack(version="1.0.0"))

    def test_an_unknown_field_is_refused_rather_than_ignored(self):
        with self.assertRaises(packs.ContractInvalid):
            resolve_one(a_pack(payload_dir="x"))

    def test_a_role_outside_the_two_allowed_values_is_refused(self):
        for role in ("REQUIRED", "primary", None, "must"):
            with self.subTest(role=role):
                with self.assertRaises(packs.ContractInvalid):
                    resolve_one(a_pack(members=[{"ability": "ability:work-management",
                                                 "role": role}]))

    def test_a_pack_with_no_members_is_refused(self):
        with self.assertRaises(packs.ContractInvalid):
            resolve_one(a_pack(members=[]))

    def test_two_packs_with_one_id_is_refused(self):
        """`rules` says two packs may share a member, which is not a conflict.
        Two packs with one id is a different thing and is not answerable."""
        with self.assertRaises(packs.ContractInvalid):
            packs.resolve(a_contract(a_pack(), a_pack()), CATALOGUE,
                          catalogue_revision=REV)


class PinTests(unittest.TestCase):
    """`publishing.rule` — "a published pack pins the ability-management
    revision its member ids were validated against. Without that pin the
    pack's closure was true of a catalogue that no longer exists."

    Answered separately from the members, and reported for every pack. These
    are the tests for the case that goes GREEN: every member resolves and the
    answer is about the wrong catalogue.
    """

    def test_a_pin_that_matches_is_reported_per_pack(self):
        result = resolve_one(a_pack())
        self.assertTrue(result["pins"]["all_match"])
        self.assertEqual(result["pins"]["packs"][0]["status"], "PIN_MATCH")

    def test_a_pack_pinned_to_another_revision_does_not_go_green(self):
        other = packs.resolve(a_contract(a_pack()), CATALOGUE,
                              catalogue_revision="0" * 40)
        # The members resolve at both revisions. That is the whole point: a
        # stale pin is invisible in the member verdict.
        self.assertEqual(other["packs"][0]["status"], "CLOSED")
        self.assertEqual(other["pins"]["packs"][0]["status"], "PIN_MISMATCH")
        self.assertFalse(other["pins"]["all_match"])

    def test_the_mismatch_message_names_both_revisions(self):
        """"the pin is stale" is not actionable. Which pin, and stale against
        what, is."""
        other = packs.resolve(a_contract(a_pack()), CATALOGUE,
                              catalogue_revision="a" * 40)
        because = other["pins"]["packs"][0]["because"]
        self.assertIn(REV, because)
        self.assertIn("a" * 40, because)

    def test_a_pack_with_no_authority_is_pin_absent_not_pin_ok(self):
        pack = a_pack()
        del pack["authority"]
        result = resolve_one(pack)
        self.assertEqual(result["pins"]["packs"][0]["status"], "PIN_ABSENT")
        self.assertFalse(result["pins"]["all_match"])

    def test_an_absent_pin_does_not_make_the_members_unresolved(self):
        """Two questions, two answers. A missing pin is not a missing ability,
        and collapsing them would tell a reader to go and fix the catalogue."""
        pack = a_pack()
        del pack["authority"]
        result = resolve_one(pack)
        self.assertEqual(result["refused"], [])
        self.assertTrue(result["packs"][0]["resolved"])

    def test_resolving_without_a_catalogue_revision_is_refused(self):
        """An answer about an unnamed catalogue cannot be reproduced, which is
        the whole reason the pin exists."""
        with self.assertRaises(packs.ContractInvalid):
            packs.resolve(a_contract(a_pack()), CATALOGUE, catalogue_revision="")


class SummarizeTests(unittest.TestCase):
    def test_a_closed_pack_says_how_many_resolved(self):
        line = packs.summarize(resolve_one(a_pack()))
        self.assertIn("pack:one", line)
        self.assertIn("CLOSED", line)
        self.assertIn("1/1", line)

    def test_a_failed_pack_names_every_unresolved_member(self):
        line = packs.summarize(resolve_one(a_pack(members=[
            {"ability": "ability:work-management", "role": "required"},
            {"ability": "ability:ghost", "role": "required"},
            {"ability": "ability:phantom", "role": "optional"}])))
        self.assertIn("ability:ghost", line)
        self.assertIn("ability:phantom", line)

    def test_a_stale_pin_appears_even_when_every_member_resolved(self):
        """Otherwise the summary prints four lines of OK over the one defect
        it was run to find."""
        line = packs.summarize(packs.resolve(a_contract(a_pack()), CATALOGUE,
                                             catalogue_revision="0" * 40))
        self.assertIn("PIN_MISMATCH", line)


class BuildWiringTests(unittest.TestCase):
    """The rule was written down for a long time and nothing called it. This is
    the only test in the file that can catch that happening again."""

    def source(self) -> str:
        return (REPO / "scripts" / "build.py").read_text(encoding="utf-8")

    def test_the_build_validates_the_pack_contract(self):
        source = self.source()
        self.assertIn("def validate_packs(", source)
        self.assertIn("validate_packs(pack_contract_path", source)

    def test_the_build_defaults_to_this_repository_s_contract(self):
        self.assertIn('"pack-contract.yaml"', self.source())

    def test_the_build_reads_the_catalogue_from_the_pinned_revision(self):
        """Reading the working tree would let a dirty ability-management
        checkout answer a question about what the catalogue contains."""
        source = self.source()
        body = source[source.index("def catalogued_abilities"):
                      source.index("def _packs_module")]
        self.assertIn("show", body)
        self.assertNotIn("read_text", body)

    def test_an_empty_catalogue_is_refused_rather_than_reported_as_failures(self):
        """Every pack fails against an empty catalogue, which is the correct
        direction and the wrong diagnosis: it is far more likely the manifest
        moved."""
        source = self.source()
        body = source[source.index("def catalogued_abilities"):
                      source.index("def _packs_module")]
        self.assertIn("declares no abilities", body)

    def test_the_build_reads_the_packs_from_the_pinned_revision_too(self):
        """Two readers now, and they must agree about which revision they mean.

        The declarations moved to ability-management, so the build reads the
        catalogue and the packs from the same git object with the same
        discipline. A build that read the packs from a checkout would answer
        "what has been published" with whatever happened to be on disk — and
        the two readers disagreeing about the revision is the exact shape of
        PIN LAG, which is the defect the pin was added for.
        """
        source = self.source()
        body = source[source.index("def published_packs"):
                      source.index("def _packs_module")]
        self.assertIn("show", body)
        self.assertNotIn("read_text", body)
        # The filename by NAME, not by value: the reader must go through the
        # one constant that `publishing.file` also names, so the contract and
        # the reader cannot end up talking about two different files.
        self.assertIn("PACK_DECLARATIONS_FILE", body)

    def test_the_build_assembles_rules_and_declarations_from_two_repositories(self):
        """The contract's rules, the published packs, one verdict.

        Asserted structurally because the failure it guards against is silent:
        a `resolve()` call that went back to reading `contract["packs"]` would
        find no packs in this repository's contract and raise — and the tempting
        repair for that would be to put a copy of the packs back in the contract,
        which is two sources for one fact.
        """
        source = self.source()
        body = source[source.index("def validate_packs"):
                      source.index("def local_skill_files")]
        self.assertIn("packs=published_packs(ability_repo, ability_rev)", body)
        self.assertIn("dict(contract, packs=", body)


class AncestorPinTests(unittest.TestCase):
    """The one rule that came with publishing the declarations.

    A pin that is not the revision being read was unconditionally PIN_MISMATCH.
    It has to be, once the declarations live in the same repository as the
    catalogue they name: editing a pack moves the repository, so the strict
    rule would refuse the build for the one act it exists to permit — a gate
    that fails exactly when it is exercised.

    Both directions are exercised here, and the second one is the reason this
    class exists: a relaxation that is only ever shown to accept is a hole with
    a passing test in front of it.
    """

    def setUp(self):
        read = _declarations_at(DECLARATIONS_REV)
        if read is None:
            self.skipTest("no ability-management repository reachable; an absent "
                          "declarations file is not an unchanged one")
        self.repo = read[1]

    def test_the_declarations_parent_still_describes_them(self):
        """`0091d8d` adds this file and touches no ability, so the pin naming
        its parent still names a catalogue that can be produced again."""
        self.assertTrue(packs.pin_still_describes(
            "7bc768709ec93c9264b078f5d72b9dbed735cb43", DECLARATIONS_REV, self.repo))

    def test_an_ancestor_whose_manifest_moved_is_still_refused(self):
        """The case the relaxation must NOT swallow.

        `95c31ed` is an ancestor of `f97a43b`, so the first half of the rule
        passes; the manifest changed between them, which is precisely the drift
        the pin exists to catch. If ancestry alone were enough, a pin would
        survive the catalogue moving underneath it, and the closure would be a
        true statement about a catalogue nobody can produce.
        """
        self.assertFalse(packs.pin_still_describes(
            "95c31ed8a0a49f048ff0f89d3fabd0c6ba7fd2d5",
            "f97a43b176a0bbc8ec26812e80a8405735fc494d", self.repo))

    def test_a_pin_that_is_not_an_ancestor_is_refused(self):
        """An unknown sha is not an ancestor, and neither is a descendant."""
        self.assertFalse(packs.pin_still_describes("0" * 40, DECLARATIONS_REV, self.repo))

    def test_the_revision_itself_trivially_describes_itself(self):
        self.assertTrue(packs.pin_still_describes(DECLARATIONS_REV, DECLARATIONS_REV,
                                                  self.repo))


class OnePinRuleTests(unittest.TestCase):
    """The pin rule has ONE implementation, and both callers reach it.

    This class exists because of a defect that was measured rather than
    imagined. `resolve()` can only compare shas, so it reports every pin that is
    not the revision being read as PIN_MISMATCH; deciding which of those are
    merely an ancestor with an unchanged manifest needs git. For one revision
    that decision lived in `scripts/build.py`, and the consequence was that
    `abilities/packs.py` — which has a command-line entry point, a documented
    way to run this exact check — printed `FAIL ... PIN_MISMATCH` and exited 1
    over the same files the build built successfully.

    Two answers to one question, from two copies of one rule, on one machine, in
    one afternoon. The repair was not to fix the CLI: it was to keep one copy.
    """

    def setUp(self):
        read = _declarations_at(DECLARATIONS_REV)
        if read is None:
            self.skipTest("no ability-management repository reachable")
        self.repo = read[1]

    def settled(self, pin, revision=DECLARATIONS_REV):
        pack = a_pack(authority={"repo": "ProfessorZhi/ability-management",
                                 "revision": pin})
        report = packs.resolve(a_contract(pack), CATALOGUE,
                               catalogue_revision=revision)
        drifted = packs.settle_pins(report, revision=revision, repo=self.repo)
        return report, drifted

    def test_a_pin_that_is_only_older_is_upgraded_where_it_stands(self):
        """Not waved through: `resolve()` said PIN_MISMATCH and that verdict is
        replaced by a checked one, in the same field, so nothing downstream has
        to know there were two spellings of "acceptable"."""
        report, drifted = self.settled("7bc768709ec93c9264b078f5d72b9dbed735cb43")
        verdict = report["pins"]["packs"][0]
        self.assertEqual("PIN_EQUIVALENT", verdict["status"])
        self.assertIn("ancestor", verdict["because"])
        self.assertEqual([], drifted)
        self.assertTrue(report["pins"]["all_match"])

    def test_a_pin_that_really_drifted_is_returned_and_still_fails(self):
        report, drifted = self.settled("95c31ed8a0a49f048ff0f89d3fabd0c6ba7fd2d5",
                                       revision="f97a43b176a0bbc8ec26812e80a8405735fc494d")
        self.assertEqual("PIN_MISMATCH", report["pins"]["packs"][0]["status"])
        self.assertEqual(1, len(drifted))
        self.assertFalse(report["pins"]["all_match"],
                         "a settled report must not go green on a pin that drifted")

    def test_all_match_is_recomputed_rather_than_left_as_resolve_set_it(self):
        """`resolve()` sets it False for any pin that is not the revision; the
        settle step has to put it back, or a caller that only prints the summary
        reports a failure it just resolved."""
        pack = a_pack(authority={"repo": "ProfessorZhi/ability-management",
                                 "revision": "7bc768709ec93c9264b078f5d72b9dbed735cb43"})
        report = packs.resolve(a_contract(pack), CATALOGUE,
                               catalogue_revision=DECLARATIONS_REV)
        self.assertFalse(report["pins"]["all_match"])
        packs.settle_pins(report, revision=DECLARATIONS_REV, repo=self.repo)
        self.assertTrue(report["pins"]["all_match"])

    def test_the_builder_no_longer_keeps_a_second_copy(self):
        """Structural, and deliberately so: this is the assertion that fails if
        somebody re-adds the rule to `scripts/build.py` because it is convenient
        there. Convenient there is how it got there the first time."""
        source = (REPO / "scripts" / "build.py").read_text(encoding="utf-8")
        self.assertNotIn("def _pin_still_describes", source)
        self.assertIn("packs.settle_pins(report, revision=ability_rev, repo=ability_repo)",
                      source)

    def test_the_command_line_and_the_build_agree_on_the_real_files(self):
        """The defect, run end to end, on the files it happened to.

        The contract and the published declarations, the real catalogue, the
        real repository, through `packs.main()` — the entry point that used to
        exit 1 here. A test of either half alone cannot fail this way; only the
        two of them over the same inputs can.
        """
        read = _declarations_at(DECLARATIONS_REV)
        manifest = _manifest_at(DECLARATIONS_REV)
        if read is None or manifest is None:
            self.skipTest("no ability-management repository reachable")
        import contextlib, io, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / PACK_DECLARATIONS_FILE).write_text(
                yaml.safe_dump({"packs": read[0]}), encoding="utf-8")
            names = [a["name"] for a in manifest[0]["abilities"]]
            (tmp / "names.txt").write_text("\n".join(names) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = packs.main([
                    "--contract", str(REPO / "abilities" / "pack-contract.yaml"),
                    "--declarations", str(tmp / PACK_DECLARATIONS_FILE),
                    "--catalogue", str(tmp / "names.txt"),
                    "--catalogue-revision", DECLARATIONS_REV,
                    "--catalogue-repo", str(self.repo),
                ])
        self.assertEqual(0, code, out.getvalue())
        self.assertNotIn("FAIL", out.getvalue())


class LiveContractTests(unittest.TestCase):
    """The declared packs, against the declared catalogue.

    Synthetic packs cannot catch the defect this module was written for. The
    four packs in `abilities/pack-contract.yaml` named three members that
    resolved to nothing, and the way that was found was running the check over
    the real file.
    """

    def published(self) -> list[dict]:
        """The packs as published, read from the git object — not from a copy.

        This used to return `contract["packs"]`, because the packs were
        declared inside the contract. They are not any more: `publishing.location`
        said they belong in ability-management and this round made that
        sentence true, so a test that kept reading a local list would be
        checking a copy that no longer exists — and would pass if the published
        file were deleted.
        """
        read = _declarations_at(DECLARATIONS_REV)
        if read is None:
            self.fail(
                f"{PACK_DECLARATIONS_FILE} does not resolve at "
                f"{DECLARATIONS_REV[:12]} in any reachable ability-management "
                "repository. The packs are published there, so a missing file "
                "is not an empty catalogue — it is the declaration layer having "
                "no subject at all, which is the defect this file exists for")
        return read[0]

    def contract(self) -> dict:
        """The schema from here, the packs from the published revision.

        Assembled exactly the way `scripts/build.py` assembles them. Reading
        the schema from this file is the point: the two halves live in two
        repositories and the thing that could go wrong is their disagreement.
        """
        schema = yaml.safe_load(
            (ABILITIES / "pack-contract.yaml").read_text(encoding="utf-8"))
        return dict(schema, packs=self.published())

    def test_the_contract_does_not_also_declare_the_packs(self):
        """One source per fact.

        The packs are published in ability-management and read from there. A
        copy left in this file would be read by nothing — `validate_packs` takes
        them from the pinned revision — and would drift unobserved, which is
        this repository's recurring failure in a new costume: a declaration
        that has stopped having anything to do with what is used.

        `location_status` is asserted by name rather than left to the prose,
        because it is the field that was `DECLARED_NOT_IMPLEMENTED` while the
        sentence above it was untrue, and the only thing that should be able to
        move it back is the file it describes going away.
        """
        contract = yaml.safe_load(
            (ABILITIES / "pack-contract.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("packs", contract)
        self.assertEqual(contract["publishing"]["location_status"], "IMPLEMENTED")
        self.assertEqual(contract["publishing"]["file"], PACK_DECLARATIONS_FILE)

    def test_every_declared_pack_carries_a_pin(self):
        """`publishing.rule` requires one. Before this round none had one, and
        a rule requiring a field no record has is a rule with no subject."""
        for pack in self.contract()["packs"]:
            with self.subTest(pack=pack["id"]):
                authority = pack.get("authority") or {}
                self.assertTrue(authority.get("revision"),
                                f"{pack['id']} pins no revision")
                self.assertTrue(authority.get("repo"), f"{pack['id']} pins no repo")
                # A short sha is ambiguous, and the pack's closure would be
                # about whichever commit it happened to mean.
                self.assertEqual(len(str(authority["revision"])), 40)

    def test_every_declared_pack_names_only_abilities(self):
        for pack in self.contract()["packs"]:
            for member in pack["members"]:
                with self.subTest(member=member["ability"]):
                    self.assertTrue(member["ability"].startswith("ability:"))
                    self.assertIn(member["role"], ("required", "optional"))

    def test_the_packs_pin_one_catalogue(self):
        """Two packs validated against two catalogues is legal, and the report
        says so per pack. What is not legal is not knowing: a contract whose
        packs pin different revisions while its prose says they share one is a
        description that has stopped matching its data."""
        pins = {pack["authority"]["revision"] for pack in self.contract()["packs"]}
        self.assertEqual(len(pins), 1,
                         f"packs disagree about the catalogue: {sorted(pins)}")

    def test_the_pinned_catalogue_resolves_against_the_manifest_at_that_revision(self):
        """The one test here that leaves `tests/`. A contract that closes
        against a synthetic catalogue and not against the real one is the exact
        shape of the original defect — accurate, and about nothing.

        The manifest is read from the git object at the pinned revision, the
        same way `scripts/build.py` reads it, and not from a checkout on disk.
        A working tree would answer a question about what the catalogue
        CONTAINS with whatever happens to be checked out — and on this machine
        that difference is live: the same four packs give different answers
        against `src/ability-management` and against the Item worktree.

        Skipped, never passed, when no ability-management repository is
        reachable: an absent catalogue is not a closed one, and a test that
        treats them the same is the failure this file exists to catch.
        """
        contract = self.contract()
        revision = contract["packs"][0]["authority"]["revision"]
        repos = _ability_repos()
        if not repos:
            self.skipTest("no ability-management repository reachable; an absent "
                          "catalogue is not a closed one")
        read = _manifest_at(revision)
        if read is None:
            self.fail(
                f"the pinned revision {revision[:12]} does not resolve in any of "
                f"{[str(r) for r in repos]}. A pin naming a commit that exists on "
                "one machine only is a pin nobody else can check, and the pack's "
                "closure is a claim no reader can reproduce. Push the ability "
                "branch and repin from the remote HEAD (§62)")
        manifest, _repo = read
        names = [a["name"] for a in manifest["abilities"]]
        self.assertTrue(names, "the pinned catalogue declares no abilities")
        report = packs.resolve(contract, names, catalogue_revision=revision)
        self.assertEqual(report["refused"], [], packs.summarize(report))


class AgentOrchestrationPackTests(unittest.TestCase):
    """§14: the pack closure gate, exercised in BOTH directions.

    A gate that has only ever been shown to pass is a gate nobody has shown to
    work. This checks the same pack against two catalogues and requires the two
    answers to differ, in the direction the rule predicts — so that "the pack is
    CLOSED" is a statement about the catalogue it was checked against rather
    than about the checker having returned CLOSED.

    The catalogue is read from git objects at the pinned revision, the same way
    `scripts/build.py` reads it and for the same reason: a dirty checkout must
    not be able to answer a question about what the catalogue contains.
    """

    NEW_REV = "7bc768709ec93c9264b078f5d72b9dbed735cb43"
    OLD_REV = "95c31ed8a0a49f048ff0f89d3fabd0c6ba7fd2d5"

    def contract(self) -> dict:
        """Schema from here, packs from where they are published.

        This class mutates the pack it reads (it re-pins it, twice, to exercise
        both directions), so it must get a fresh object per call. Reading from
        the git object gives one every time; a module-level fixture would not.
        """
        schema = yaml.safe_load(
            (ABILITIES / "pack-contract.yaml").read_text(encoding="utf-8"))
        read = _declarations_at(DECLARATIONS_REV)
        if read is None:
            self.fail(f"{PACK_DECLARATIONS_FILE} does not resolve at "
                      f"{DECLARATIONS_REV[:12]} in any reachable "
                      "ability-management repository")
        return dict(schema, packs=read[0])

    @staticmethod
    def _catalogue(revision: str) -> list[str] | None:
        read = _manifest_at(revision)
        return None if read is None else [a["name"] for a in read[0]["abilities"]]

    def _pack(self, contract: dict, pack_id: str) -> dict:
        return next(p for p in contract["packs"] if p["id"] == pack_id)

    def test_the_pack_is_declared_with_the_members_the_brief_names(self):
        pack = self._pack(self.contract(), "pack:agent-orchestration")
        self.assertEqual(
            sorted(m["ability"] for m in pack["members"]),
            ["ability:agent-session-management", "ability:fleet-coordination",
             "ability:human-control", "ability:runtime-management",
             "ability:shared-memory", "ability:work-management"])
        self.assertTrue(all(m["role"] == "required" for m in pack["members"]))

    def test_the_new_pin_closes_and_the_old_pin_refuses_the_same_pack(self):
        if not _ability_repos():
            self.skipTest("no ability-management repository reachable; an absent "
                          "catalogue is not a closed one")
        contract = self.contract()
        new, old = self._catalogue(self.NEW_REV), self._catalogue(self.OLD_REV)
        if new is None or old is None:
            self.fail(
                f"one of {self.OLD_REV[:12]}/{self.NEW_REV[:12]} does not resolve "
                "in any reachable ability-management repository, so the two "
                "directions of this gate cannot both be exercised")

        pack = self._pack(contract, "pack:agent-orchestration")

        # Direction 1: pinned at the revision that has the members -> CLOSED.
        pack["authority"]["revision"] = self.NEW_REV
        report = packs.resolve(contract, new, catalogue_revision=self.NEW_REV)
        verdict = next(p for p in report["packs"] if p["id"] == "pack:agent-orchestration")
        self.assertEqual(verdict["status"], "CLOSED", packs.summarize(report))
        self.assertEqual(report["refused"], [])
        self.assertTrue(report["pins"]["all_match"])

        # Direction 2: the SAME pack pinned at the older revision -> refused,
        # and refused by name. The two members this round added are what the
        # older catalogue is missing, and a verdict that said only "the pack is
        # not closed" would not say which repair applies.
        pack["authority"]["revision"] = self.OLD_REV
        report = packs.resolve(contract, old, catalogue_revision=self.OLD_REV)
        verdict = next(p for p in report["packs"] if p["id"] == "pack:agent-orchestration")
        # PARTIAL, not UNAVAILABLE: the older catalogue still has four of the
        # six required members, so the pack is usable and incomplete rather than
        # absent. The distinction is the contract's, not this test's — getting
        # it wrong in either direction would misreport a partially-serviceable
        # intent as an unbuildable one.
        self.assertEqual(verdict["status"], "PARTIAL")
        self.assertIn("pack:agent-orchestration", report["refused"])
        self.assertEqual(
            sorted(m["ability"] for m in verdict["unresolved_required"]),
            ["ability:agent-session-management", "ability:human-control"])

    def test_the_two_abilities_this_round_added_are_in_the_new_catalogue(self):
        if not _ability_repos():
            self.skipTest("no ability-management repository reachable")
        new = self._catalogue(self.NEW_REV)
        if new is None:
            self.fail(f"{self.NEW_REV[:12]} does not resolve in any reachable "
                      "ability-management repository")
        for name in ("agent-session-management", "human-control"):
            self.assertIn(name, new)
