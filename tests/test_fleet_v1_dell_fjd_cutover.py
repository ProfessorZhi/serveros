"""Lane: Dell `vince` -> `fjd` cutover — offline, negative-first.

No account is read, no home is entered, no service is touched, no `su`/`sudo`
and no `chown` is issued. `fjd`'s home is not opened by these tests and is not
opened by the module they exercise: `passwd_home` is INJECTED, so the real
`getent`/`id` output can be supplied by the operator and these tests need no
account at all.

The cases are mostly refusals, because the dangerous version of this migration
is the one that looks like a copy.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import dell_fjd_cutover as cut     # noqa: E402

PASSWD = {"vince": (1002, "/home/vince"), "fjd": (1000, "/home/operator")}


def manifest() -> dict:
    """The shipped example, which is a plan and NOT yet authorised."""
    path = (ROOT / "fleet_mcp" / "deploy"
            / "dell_fjd_cutover_manifest.example.json")
    return json.loads(path.read_text(encoding="utf-8"))


def authorised() -> dict:
    item = manifest()
    item["owner_authorization"] = {
        "fjd_owner_confirmed": True,
        "confirmed_by": "fjd",
        "evidence": "fjd confirmed the account owner authorises this migration",
    }
    return item


def asset(item: dict, asset_id: str) -> dict:
    return next(a for a in item["assets"] if a["id"] == asset_id)


class TheShippedExampleIsAHonestPlanTest(unittest.TestCase):

    def test_it_is_a_plan_and_not_yet_authorised(self):
        got = cut.evaluate(manifest(), passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_PENDING)
        self.assertEqual(got["blockers"], [])
        self.assertEqual(got["executable_now"], [],
                         "nothing is executable before the owner confirms")

    def test_with_owner_confirmation_it_becomes_ready_for_pushed_source_only(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_READY, got["blockers"])
        self.assertEqual(got["executable_now"], ["serveros-git-source",
                                                 "dell-e2e-tests"])
        for asset_id in got["executable_now"]:
            with self.subTest(id=asset_id):
                self.assertEqual(cut.evaluate(authorised(), passwd_home=PASSWD)
                                 ["assets"][0]["class"], "A")

    def test_it_declares_nothing_executed_and_nothing_written(self):
        for item in (manifest(), authorised()):
            with self.subTest(authorised=item["owner_authorization"]["fjd_owner_confirmed"]):
                got = cut.evaluate(item, passwd_home=PASSWD)
                self.assertEqual(got["writes"], [])
                self.assertEqual(got["executes"], [])

    def test_the_example_puts_the_credential_categories_out_of_reach(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        for asset_id in ("vince-ssh-material", "vince-gh-config",
                         "vince-cloudcli-auth", "vince-fleet-node-secret"):
            with self.subTest(id=asset_id):
                self.assertIn(asset_id, got["never_migrated"])


class ACredentialNeverMigratesTest(unittest.TestCase):

    def _blocked(self, path: str, declared: str = "A", **over) -> list:
        item = authorised()
        item["assets"] = [{"id": "x", "class": declared, "path": path, "note": ""}]
        item.update(over)
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_BLOCKED,
                         f"{path} was not refused")
        return got["blockers"]

    def test_ssh_material_declared_as_migratable_is_refused(self):
        blockers = self._blocked("/home/vince/.ssh")
        self.assertTrue(any("credential path" in b for b in blockers))

    def test_the_gh_config_is_refused_even_though_its_component_is_spelled_gh(self):
        blockers = self._blocked("/home/vince/.config/gh")
        self.assertTrue(any(".config/gh" in b for b in blockers))

    def test_a_token_file_is_refused_by_suffix(self):
        blockers = self._blocked("/home/vince/projects/notes/token")
        self.assertTrue(any("credential path" in b for b in blockers))

    def test_a_private_key_is_refused_by_suffix(self):
        for name in ("server.pem", "host.key", "id_ed25519", "known_hosts"):
            with self.subTest(name=name):
                blockers = self._blocked(f"/home/vince/projects/keys/{name}")
                self.assertTrue(any("credential path" in b for b in blockers))

    def test_a_credential_under_the_target_home_is_refused_too(self):
        """The rule is about the asset, not about which side it is on."""
        blockers = self._blocked("/home/operator/.ssh")
        self.assertTrue(any("credential path" in b for b in blockers))

    def test_a_correctly_declared_credential_is_accepted_as_never_migrated(self):
        item = authorised()
        item["assets"] = [{"id": "v", "class": "D", "path": "/home/vince/.ssh",
                           "note": ""}]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_READY, got["blockers"])
        self.assertEqual(got["never_migrated"], ["v"])

    def test_a_near_miss_component_is_not_a_false_positive(self):
        """`my.ssh-notes` is not `.ssh`. A refusal that fired on substrings would
        make the rule untrustworthy and get it widened until it fires on
        everything."""
        item = authorised()
        item["assets"] = [{"id": "ok", "class": "C",
                           "path": "/home/vince/projects/my.ssh-notes",
                           "note": ""}]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_READY, got["blockers"])
        self.assertEqual(got["never_migrated"], [])


class AWholeHomeIsNeverTheUnitTest(unittest.TestCase):

    def _blocked(self, path: str) -> list:
        item = authorised()
        item["assets"] = [{"id": "x", "class": "C", "path": path, "note": ""}]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_BLOCKED)
        return got["blockers"]

    def test_the_source_home_itself_is_refused(self):
        self.assertTrue(any("never a home tree" in b
                            for b in self._blocked("/home/vince")))

    def test_the_target_home_itself_is_refused(self):
        self.assertTrue(any("never a home tree" in b
                            for b in self._blocked("/home/operator")))

    def test_a_path_above_a_home_is_refused(self):
        """`/home` is how a bulk copy starts."""
        self.assertTrue(any("never a home tree" in b
                            for b in self._blocked("/home")))

    def test_a_narrow_path_inside_a_home_is_fine(self):
        item = authorised()
        item["assets"] = [{"id": "ok", "class": "C",
                           "path": "/home/vince/projects/one-file.json", "note": ""}]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_READY, got["blockers"])


class ALegacyAddressKeepsItsMeaningTest(unittest.TestCase):

    def test_remapping_the_legacy_tag_is_refused(self):
        item = authorised()
        item["session_refs"]["remap_legacy_tag"] = "claude-fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_BLOCKED)
        self.assertTrue(any("remap_legacy_tag" in b for b in got["blockers"]))

    def test_the_legacy_owner_may_not_be_restated(self):
        item = authorised()
        item["session_refs"]["legacy_base_owner"] = "fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("legacy_base_owner" in b for b in got["blockers"]))

    def test_the_legacy_base_tag_may_not_be_respelled(self):
        item = authorised()
        item["session_refs"]["legacy_base_tag"] = "claude-fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("legacy_base_tag" in b for b in got["blockers"]))

    def test_a_bare_next_runtime_tag_is_refused(self):
        item = authorised()
        item["session_refs"]["next_runtime_tags"] = ["claude"]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("bare tag is taken" in b for b in got["blockers"]))

    def test_a_next_runtime_tag_naming_the_retired_account_is_refused(self):
        item = authorised()
        item["session_refs"]["next_runtime_tags"] = ["claude-vince"]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("account being retired" in b for b in got["blockers"]))

    def test_the_default_shipped_manifest_does_not_remap_anything(self):
        self.assertIsNone(manifest()["session_refs"]["remap_legacy_tag"])
        self.assertEqual(manifest()["session_refs"]["legacy_base_tag"], "claude")
        self.assertEqual(manifest()["session_refs"]["legacy_base_owner"], "vince")


class TheRequiredMatrixDropsTheAccountButKeepsTheCapabilityTest(unittest.TestCase):

    def test_re_requiring_the_legacy_runtime_is_refused(self):
        item = authorised()
        item["runtime_requirements"]["required_runtimes"] = ["claude", "claude-fjd"]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("re-requires it" in b for b in got["blockers"]))

    def test_the_example_no_longer_requires_the_retired_runtime(self):
        self.assertNotIn("claude", manifest()["runtime_requirements"]["required_runtimes"])
        self.assertEqual(manifest()["runtime_requirements"]["required_runtimes"],
                         ["claude-fjd"])

    def test_dropping_multi_account_capability_is_refused(self):
        item = authorised()
        item["runtime_requirements"]["multi_account_capable"] = False
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("multi_account_capable" in b for b in got["blockers"]))

    def test_a_preference_without_the_ux_only_statement_is_refused(self):
        item = authorised()
        del item["runtime_requirements"]["preferred_runtime_is_ux_only"]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("preferred_runtime_is_ux_only" in b
                            for b in got["blockers"]))

    def test_a_preference_must_name_a_declared_runtime(self):
        item = authorised()
        item["runtime_requirements"]["preferred_runtime"] = "claude-someone-else"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("not a declared runtime tag" in b
                            for b in got["blockers"]))

    def test_a_device_default_account_is_refused_outright(self):
        """The rejected shortcut: repointing the legacy tag space because the
        production account changed."""
        item = authorised()
        item["runtime_requirements"]["device_default_account"] = "fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("device_default_account" in b for b in got["blockers"]))

    def test_a_required_runtime_must_be_one_of_the_declared_tags(self):
        item = authorised()
        item["runtime_requirements"]["required_runtimes"] = ["claude-fjd", "codex-fjd"]
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("not among the declared" in b for b in got["blockers"]))


class OwnerAuthorizationIsNotTheRequesterAskingTest(unittest.TestCase):

    def test_an_unconfirmed_plan_blocks_nothing_but_executes_nothing(self):
        got = cut.evaluate(manifest(), passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_PENDING)
        self.assertEqual(got["blockers"], [])
        self.assertEqual(got["executable_now"], [])

    def test_a_confirmation_without_evidence_is_refused(self):
        item = authorised()
        item["owner_authorization"]["evidence"] = "   "
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("evidence" in b for b in got["blockers"]))

    def test_naming_a_confirmer_while_unconfirmed_is_refused(self):
        item = manifest()
        item["owner_authorization"]["confirmed_by"] = "fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("confirmed_by" in b for b in got["blockers"]))

    def test_a_plan_with_problems_is_blocked_before_it_is_pending(self):
        """Ordering matters: a broken plan is not merely un-authorised."""
        item = manifest()
        item["assets"].append({"id": "bad", "class": "A",
                               "path": "/home/vince/.ssh", "note": ""})
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_BLOCKED)


class TheIdentityIsVerifiedAgainstTheHostNotTheManifestTest(unittest.TestCase):

    def test_a_uid_that_disagrees_with_passwd_is_refused(self):
        item = authorised()
        item["target"]["uid"] = 4242
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("passwd says 1000" in b for b in got["blockers"]))

    def test_a_home_that_disagrees_with_passwd_is_refused(self):
        item = authorised()
        item["target"]["home"] = "/home/not-fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("passwd says" in b for b in got["blockers"]))

    def test_a_user_with_no_passwd_entry_is_refused(self):
        got = cut.evaluate(authorised(), passwd_home={"vince": (1002, "/home/vince")})
        self.assertTrue(any("no passwd entry" in b for b in got["blockers"]))

    def test_the_wrong_source_account_is_refused(self):
        item = authorised()
        item["source"]["os_user"] = "fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("source.os_user" in b for b in got["blockers"]))

    def test_a_second_device_identity_is_refused(self):
        """One machine is one authenticated device; the account rides in the tag."""
        item = authorised()
        item["device_ref"] = "device:llm-lab-dell-fjd"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertTrue(any("device_ref" in b for b in got["blockers"]))


class UnpushedWorkIsNotCopiedTest(unittest.TestCase):

    def test_the_wip_entries_are_registered_but_never_migratable(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        by_id = {a["id"]: a for a in got["assets"]}
        for asset_id in ("runtime-identity-poc-wip", "fjd-node-bootstrap-script"):
            with self.subTest(id=asset_id):
                self.assertEqual(by_id[asset_id]["class"], "B")
                self.assertFalse(by_id[asset_id]["migratable"])
                self.assertIn("patch", by_id[asset_id]["why"])
                self.assertNotIn(asset_id, got["executable_now"])

    def test_no_wip_path_is_ever_executable_even_when_authorised(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        for asset_id in got["executable_now"]:
            with self.subTest(id=asset_id):
                self.assertEqual(
                    next(a for a in got["assets"] if a["id"] == asset_id)["class"], "A")


class ProductionStaysWhereItIsTest(unittest.TestCase):

    def test_the_strata_directory_is_in_place_and_never_migratable(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        entry = next(a for a in got["assets"] if a["id"] == "strata-production")
        self.assertEqual(entry["class"], "E")
        self.assertFalse(entry["migratable"])
        self.assertIn("never moved", entry["why"])

    def test_the_legacy_connector_is_isolated_rather_than_retired_here(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        entry = next(a for a in got["assets"] if a["id"] == "legacy-vince-connector")
        self.assertEqual(entry["class"], "F")
        self.assertFalse(entry["migratable"])
        self.assertNotIn("legacy-vince-connector", got["executable_now"])

    def test_no_asset_is_ever_marked_executable_for_the_in_place_classes(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        for cls in ("D", "E", "F", "B"):
            with self.subTest(cls=cls):
                for entry in got["assets"]:
                    if entry["class"] == cls:
                        self.assertFalse(entry["migratable"])


class TheEvaluationIsPureTest(unittest.TestCase):

    def test_evaluating_does_not_mutate_the_manifest(self):
        item = authorised()
        before = copy.deepcopy(item)
        cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(item, before)

    def test_two_evaluations_agree(self):
        item = authorised()
        self.assertEqual(cut.evaluate(item, passwd_home=PASSWD),
                         cut.evaluate(copy.deepcopy(item), passwd_home=PASSWD))

    def test_the_counts_add_up_to_the_asset_list(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        self.assertEqual(sum(got["counts"].values()), len(got["assets"]))

    def test_a_manifest_holding_a_secret_field_is_refused_without_echoing_it(self):
        item = authorised()
        item["assets"][0]["note"] = "fine"
        item["next_auth_token"] = "SENTINEL"
        got = cut.evaluate(item, passwd_home=PASSWD)
        self.assertEqual(got["decision"], cut.DECISION_BLOCKED)
        self.assertIn("unsupported field", " ".join(got["blockers"]))
        self.assertNotIn("SENTINEL", json.dumps(got))


class TheShippedExampleAndTheModuleAgreeTest(unittest.TestCase):

    def test_the_example_paths_are_categorised_as_declared(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        by_id = {a["id"]: a for a in got["assets"]}
        for entry in manifest()["assets"]:
            with self.subTest(id=entry["id"]):
                self.assertEqual(by_id[entry["id"]]["class"], entry["class"])

    def test_the_module_has_no_way_to_touch_an_account_or_run_anything(self):
        """Checked against the module's NAMESPACE, not its prose.

        The docstring names `getent`, `mv`, `rsync` and `chown` in order to say
        the module does none of them; a substring scan cannot tell a refusal
        from a capability and would push the wording out of the file rather than
        keep the behaviour out of it.
        """
        import fleet_mcp.deploy.dell_fjd_cutover as mod
        for banned in ("subprocess", "shutil", "os", "pwd", "grp", "socket",
                       "pathlib", "getpass"):
            with self.subTest(module_attr=banned):
                self.assertFalse(hasattr(mod, banned),
                                 f"{banned!r} is reachable from this module")

    def test_the_signature_takes_identity_as_an_argument_never_finds_it(self):
        """`passwd_home` is injected. A module that resolved the account itself
        would be reading a home it was never given."""
        import inspect
        import fleet_mcp.deploy.dell_fjd_cutover as mod
        params = set(inspect.signature(mod.evaluate).parameters)
        self.assertEqual(params, {"manifest", "passwd_home"})

    def test_the_module_declares_no_effect_of_its_own(self):
        got = cut.evaluate(authorised(), passwd_home=PASSWD)
        self.assertEqual(got["writes"], [])
        self.assertEqual(got["executes"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
