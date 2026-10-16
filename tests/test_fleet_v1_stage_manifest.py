"""The staging run: what it writes, and the two things it must never write.

The staging tool exists for the window the builder is blocked in. That window is
also where it is most dangerous: an operator with a blocked release wants
*something* they can hand over, and the useful-looking something is a ZIP. So
the tests here are mostly about the absences.

Two absences are load-bearing. First, **no archive**: a staging tree that could
be zipped into an installable shape is the release path arriving through a side
door. Second, **no `mcp.json`**: it is generated from an observed URL, and while
the endpoint gate is blocked there is no observed URL, so a binding written here
would be a fabricated one wearing a generated filename.

The third property is shared with the builder and checked the same way: a source
tree the audit refuses must not be described, copied or staged. A manifest is a
description of bytes that are about to be copied out, so it is a place a
credential could leave from — which is why the audit runs before anything else.

No network, no secret, no Plugin Creator, no Feishu, no Tailscale.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_fleet_v1_first_install import Fixture, OBSERVED_URL  # noqa: E402

import importlib.util  # noqa: E402

HERE = ROOT / "integrations" / "fleet-v1-first-install"
_spec = importlib.util.spec_from_file_location(
    "fleet_v1_stage_manifest", HERE / "stage_manifest.py")
staging = importlib.util.module_from_spec(_spec)
sys.modules["fleet_v1_stage_manifest"] = staging
_spec.loader.exec_module(staging)

builder = staging.builder


class StagingRun(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture(omit_assets=True)
        self.addCleanup(self.f.close)
        # `stage()` takes the lock path; the Fixture holds the lock as a dict.
        self.lock_path = self.f.root / "brand-assets.lock.json"
        self.lock_path.write_text(json.dumps(self.f.lock), encoding="utf-8")

    def stage(self, **overrides) -> dict:
        return staging.stage(self.f.source, self.f.out, self.lock_path,
                             **overrides)

    def archives(self) -> list[str]:
        return sorted(p.name for p in self.f.out.glob("*.zip"))

    def test_a_staging_run_writes_no_archive(self) -> None:
        """The whole point of the name: nothing here is installable."""
        record = self.stage(next_mcp_url=None)
        self.assertFalse(record["installable"])
        self.assertEqual(self.archives(), [])

    def test_the_staging_tree_carries_the_payload_but_never_a_binding(self) -> None:
        self.stage(next_mcp_url=None)
        staged = self.f.out / staging.STAGING_DIR_NAME
        self.assertTrue((staged / "plugin.json").is_file())
        self.assertTrue((staged / "skills").is_dir())
        self.assertFalse((staged / "mcp.json").exists())

    def test_every_staged_member_is_in_the_manifest_with_its_digest(self) -> None:
        record = self.stage(next_mcp_url=None)
        staged = self.f.out / staging.STAGING_DIR_NAME
        named = {m["member"] for m in record["present_members"]}
        on_disk = {p.relative_to(staged).as_posix()
                   for p in staged.rglob("*") if p.is_file()}
        self.assertEqual(named, on_disk)
        self.assertNotIn("mcp.json", named)

    def test_the_absent_asset_is_recorded_with_its_pinned_digest_not_guessed(self) -> None:
        """The manifest passes the lock through; it does not compute a substitute.

        The digest it reports is the one the lock pinned, whatever that is — a
        manifest that hashed the tree instead would report a digest for an asset
        that is not the approved one and read as if it were. That the
        repository's own lock carries the *approved* digests is a separate
        property, pinned in the builder's suite.
        """
        record = self.stage(next_mcp_url=None)
        self.assertIsNone(record["asset_readback"])
        self.assertEqual([s["slot"] for s in record["pinned_assets"]],
                         ["composerIcon", "logo"])
        self.assertEqual(
            [s["expected_sha256"] for s in record["pinned_assets"]],
            [s["expected_sha256"] for s in self.f.lock["slots"]],
        )
        statuses = {b["gate"]: b["status"] for b in record["blockers"]}
        self.assertEqual(statuses.get("assets"), builder.MISSING_BRAND_ASSET)

    def test_the_endpoint_blocker_is_recorded_not_swallowed(self) -> None:
        record = self.stage(next_mcp_url=None, observation=None)
        statuses = {b["gate"]: b["status"] for b in record["blockers"]}
        self.assertEqual(statuses.get("endpoint"), builder.BLOCKED_MCP_ENDPOINT)
        self.assertIsNone(record["mcp_url"])

    def test_a_source_the_audit_refuses_is_not_even_described(self) -> None:
        """A manifest is a description of bytes about to be copied out."""
        (self.f.source / ".env").write_text("FLEET_TOKEN=abcdef123456\n",
                                            encoding="utf-8")
        with self.assertRaises(builder.Refused) as caught:
            self.stage(next_mcp_url=None)
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertEqual(self.archives(), [])
        self.assertFalse((self.f.out / staging.STAGING_DIR_NAME).exists())

    def test_a_second_run_refuses_to_blend_into_the_first(self) -> None:
        self.stage(next_mcp_url=None)
        with self.assertRaises(builder.Refused) as caught:
            self.stage(next_mcp_url=None)
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertIn("already exists", caught.exception.detail)

    def test_a_source_with_every_gate_passing_says_to_build_a_release_instead(self) -> None:
        """A staging run over a buildable source is the wrong tool, and says so.

        The fixture here is the one the builder's own positive path uses, so the
        report has to point at the release builder rather than emit a "staging"
        tree for something that is installable.
        """
        healthy = Fixture()
        self.addCleanup(healthy.close)
        lock_path = healthy.root / "brand-assets.lock.json"
        lock_path.write_text(json.dumps(healthy.lock), encoding="utf-8")
        record = staging.stage(healthy.source, healthy.out, lock_path,
                               next_mcp_url=OBSERVED_URL,
                               observation=healthy.observation)
        self.assertEqual([b["gate"] for b in record["blockers"]], ["builder"])
        self.assertEqual(record["blockers"][0]["status"], builder.READY_TO_BUILD)
        self.assertEqual(record["mcp_url"], OBSERVED_URL)


if __name__ == "__main__":
    unittest.main()
