"""The first-install builder: what it builds, and every gate that stops it.

Three properties are load-bearing, and none of them is visible from reading the
builder once.

The first is that **a blocked build writes no release archive**. The dangerous
output of a tool like this is not the refusal — it is the plausible-looking ZIP
produced by a code path that decided to carry on. So the tests below run each
gate to its refusal and then look at the output directory, rather than only
asserting that the exception's status was the expected string.

The second is that **the endpoint must have been observed**. A URL a caller
typed is a claim about a machine; a receipt naming the discovery documents that
were read back is an observation of one. The builder refuses the first, and the
tests refuse to let that loosen — including for a URL that is *shaped*
correctly.

The third is that **the initial package carries MCP**. The format a client
registers is the format it keeps, so a first release without a binding can
never grow one. That is why `build_first_install.py` requires `mcp.json` while
`integrations/chatgpt-fleet-next/build_zip.py` refuses it: they are different
artefacts, and the tests here pin the difference rather than the similarity.

No network, no secret, no Plugin Creator, no Feishu, no Tailscale. The two brand
images do not exist on any machine this suite runs on, so the positive path uses
a lock built from PNGs the test generates — which is exactly the point: the
builder reads the pinned bytes, and a test that could not produce them could not
test the gate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-v1-first-install"

_spec = importlib.util.spec_from_file_location(
    "fleet_v1_first_install", HERE / "build_first_install.py")
builder = importlib.util.module_from_spec(_spec)
sys.modules["fleet_v1_first_install"] = builder
_spec.loader.exec_module(builder)

OBSERVED_URL = "https://fleet-next-tail3d8b76.tail3d8b76.ts.net/mcp"


def make_png(width: int, height: int) -> bytes:
    """A real, minimal, valid PNG. Not a fixture standing in for an image."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def observation(url: str = OBSERVED_URL, **overrides) -> dict:
    record = {
        "schema": builder.ENDPOINT_OBSERVATION_SCHEMA,
        "url": url,
        "source": "LIVE",
        "observed_at": "2026-10-15T12:00:00Z",
        "reachable": True,
        "tls": {"scheme": "https", "verified": True},
        "discovery": {
            "protected_resource": {"url": url.replace("/mcp", "/.well-known/oauth-protected-resource"),
                                   "status": 200, "receipt_ref": "evidence/obs/prm"},
            "authorization_server": {"url": url.replace("/mcp", "/.well-known/oauth-authorization-server"),
                                     "status": 200, "receipt_ref": "evidence/obs/as"},
        },
        "receipt_ref": "evidence/obs/endpoint",
    }
    record.update(overrides)
    return record


class Fixture:
    """A buildable tree: the real source, generated assets, a matching lock."""

    def __init__(self, *, icon=512, logo=1024, omit_assets=False,
                 icon_bytes=None, logo_bytes=None):
        self._tmp = tempfile.TemporaryDirectory(prefix="fleet-v1-fi-")
        self.root = Path(self._tmp.name)
        self.source = self.root / "plugin"
        shutil.copytree(HERE / "plugin", self.source)

        self.assets = self.source / "assets"
        self.assets.mkdir(exist_ok=True)
        icon_data = icon_bytes if icon_bytes is not None else make_png(icon, icon)
        logo_data = logo_bytes if logo_bytes is not None else make_png(logo, logo)
        if not omit_assets:
            (self.assets / "composerIcon.png").write_bytes(icon_data)
            (self.assets / "logo.png").write_bytes(logo_data)

        self.lock = {
            "schema": "serveros/fleet-next-brand-assets.v1",
            "slots": [
                {"slot": "composerIcon", "shipped_path": "assets/composerIcon.png",
                 "expected_sha256": hashlib.sha256(icon_data).hexdigest(),
                 "square_required": True},
                {"slot": "logo", "shipped_path": "assets/logo.png",
                 "expected_sha256": hashlib.sha256(logo_data).hexdigest(),
                 "square_required": False},
            ],
        }
        self.observation = self.root / "observation.json"
        self.observation.write_text(json.dumps(observation()), encoding="utf-8")
        self.out = self.root / "dist"

    def kwargs(self, **overrides):
        base = dict(next_mcp_url=OBSERVED_URL, observation=self.observation,
                    app_backed=False, plugin_id=None, registration=None,
                    v1_mcp_url=None)
        base.update(overrides)
        return base

    def build(self, **overrides) -> dict:
        return builder.build(self.source, self.out, self.lock, **self.kwargs(**overrides))

    def refusal(self, **overrides) -> builder.Refused:
        try:
            record = self.build(**overrides)
        except builder.Refused as refusal:
            return refusal
        raise AssertionError("the build was expected to be refused, and produced "
                             f"{record.get('archive')!r} instead")

    def archives(self) -> list[str]:
        return sorted(p.name for p in self.out.glob("*.zip"))

    def close(self) -> None:
        self._tmp.cleanup()


class BuildIt(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_a_fully_evidenced_source_builds(self) -> None:
        record = self.f.build()
        self.assertEqual(record["distribution"], "FIRST_INSTALL_RELEASE")
        self.assertEqual(record["mcp_url"], OBSERVED_URL)
        self.assertEqual(len(record["skills"]), 5)

    def test_the_first_release_carries_mcp(self) -> None:
        """The property the whole builder exists for."""
        record = self.f.build()
        self.assertIn("mcp.json", record["members"])

    def test_the_binding_is_streamable_http_at_the_observed_url(self) -> None:
        record = self.f.build()
        with zipfile.ZipFile(self.f.out / record["archive"]) as zf:
            binding = json.loads(zf.read("mcp.json"))
        self.assertEqual(binding["$schema"], builder.MCP_SCHEMA_URL)
        self.assertEqual(set(binding), {"$schema", "mcpServers"})
        server = binding["mcpServers"]["serveros-fleet"]
        self.assertEqual(server["type"], "streamable-http")
        self.assertEqual(server["url"], OBSERVED_URL)

    def test_every_skill_is_an_immediate_child_with_a_skill_md(self) -> None:
        record = self.f.build()
        for member in record["members"]:
            if member.startswith("skills/"):
                self.assertEqual(member.count("/"), 2, member)
                self.assertTrue(member.endswith("/SKILL.md"), member)

    def test_the_archive_carries_both_approved_assets(self) -> None:
        record = self.f.build()
        self.assertIn("assets/composerIcon.png", record["members"])
        self.assertIn("assets/logo.png", record["members"])

    def test_two_builds_of_one_source_agree(self) -> None:
        first = self.f.build()
        second = self.f.build()
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])

    def test_nothing_is_uploaded_installed_or_created(self) -> None:
        record = self.f.build()
        self.assertEqual(record["writes"], [])
        self.assertEqual(record["uploads"], [])
        self.assertEqual(record["installs"], [])
        self.assertFalse(record["creates_plugin"])


class TheEndpointMustHaveBeenObserved(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _blocked(self, **overrides) -> str:
        refusal = self.f.refusal(**overrides)
        self.assertEqual(refusal.status, builder.BLOCKED_MCP_ENDPOINT)
        return refusal.detail

    def test_no_url_is_not_a_default(self) -> None:
        self.assertIn("no Next MCP URL", self._blocked(next_mcp_url=None))

    def test_a_shaped_url_with_no_receipt_is_still_blocked(self) -> None:
        self.assertIn("not an observation", self._blocked(observation=None))

    def test_the_receipt_must_observe_the_same_url(self) -> None:
        other = self.f.root / "other.json"
        other.write_text(json.dumps(observation(
            "https://somewhere-else.tail3d8b76.ts.net/mcp")), encoding="utf-8")
        self.assertIn("may only be built for the URL that was observed",
                      self._blocked(observation=other))

    def test_a_receipt_that_is_not_live_is_not_an_observation(self) -> None:
        for overrides, needle in (
            ({"source": "MOCK"}, "not marked LIVE"),
            ({"reachable": False}, "was reachable"),
            ({"tls": {"verified": False}}, "verified TLS"),
            ({"observed_at": ""}, "no timestamp"),
        ):
            with self.subTest(overrides=str(overrides)):
                path = self.f.root / "mutated.json"
                path.write_text(json.dumps(observation(**overrides)), encoding="utf-8")
                self.assertIn(needle, self._blocked(observation=path))

    def test_both_discovery_documents_must_have_been_read(self) -> None:
        for key in ("protected_resource", "authorization_server"):
            with self.subTest(key=key):
                record = observation()
                record["discovery"][key] = {"status": 404, "receipt_ref": ""}
                path = self.f.root / "partial.json"
                path.write_text(json.dumps(record), encoding="utf-8")
                self.assertIn(f"no 200 readback for {key}", self._blocked(observation=path))

    def test_the_v1_ingress_host_is_refused(self) -> None:
        self.assertIn("V1 ingress",
                      self._blocked(next_mcp_url="https://serveros-fleet.tail3d8b76.ts.net/mcp"))

    def test_a_supplied_v1_url_is_refused_even_on_another_host(self) -> None:
        self.assertIn("may not share an endpoint",
                      self._blocked(next_mcp_url=OBSERVED_URL, v1_mcp_url=OBSERVED_URL))

    def test_the_url_must_be_https_on_the_tailnet_at_mcp(self) -> None:
        cases = (
            ("http://fleet-next.tail3d8b76.ts.net/mcp", "not https"),
            ("https://example.com/mcp", "not a <name>.ts.net"),
            ("https://fleet-next.tail3d8b76.ts.net/sse", "the path is"),
            ("https://fleet-next.tail3d8b76.ts.net:8443/mcp", "explicit port"),
            ("https://fleet-next.tail3d8b76.ts.net/mcp?x=1", "userinfo, a query"),
        )
        for url, needle in cases:
            with self.subTest(url=url):
                self.assertIn(needle, self._blocked(next_mcp_url=url))


class TheApprovedAssetOrNothing(unittest.TestCase):
    def test_an_absent_asset_is_named_missing(self) -> None:
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.MISSING_BRAND_ASSET)
        self.assertIn("Do not draw a substitute", refusal.detail)

    def test_a_similar_image_is_not_the_approved_one(self) -> None:
        f = Fixture()
        self.addCleanup(f.close)
        # A perfectly good PNG of the right shape, and not the approved bytes.
        f.assets.joinpath("composerIcon.png").write_bytes(make_png(512, 512) + b"\x00")
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.BRAND_ASSET_MISMATCH)
        self.assertIn("only the pinned bytes", refusal.detail)

    def test_a_composer_icon_must_be_square(self) -> None:
        f = Fixture(icon=512)
        self.addCleanup(f.close)
        data = make_png(512, 256)
        f.assets.joinpath("composerIcon.png").write_bytes(data)
        f.lock["slots"][0]["expected_sha256"] = hashlib.sha256(data).hexdigest()
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.BRAND_ASSET_MISMATCH)
        self.assertIn("square slot", refusal.detail)

    def test_a_logo_may_keep_its_aspect_ratio(self) -> None:
        f = Fixture(logo=1024)
        self.addCleanup(f.close)
        data = make_png(1024, 256)
        f.assets.joinpath("logo.png").write_bytes(data)
        f.lock["slots"][1]["expected_sha256"] = hashlib.sha256(data).hexdigest()
        self.assertEqual(f.build()["assets"]["logo"]["width"], 1024)

    def test_a_tiny_asset_is_refused(self) -> None:
        f = Fixture(icon=16)
        self.addCleanup(f.close)
        data = make_png(16, 16)
        f.assets.joinpath("composerIcon.png").write_bytes(data)
        f.lock["slots"][0]["expected_sha256"] = hashlib.sha256(data).hexdigest()
        self.assertEqual(f.refusal().status, builder.BRAND_ASSET_MISMATCH)


class TheSkillsMustBeLoadable(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _refuse(self, needle: str) -> None:
        refusal = self.f.refusal()
        self.assertEqual(refusal.status, builder.MISSING_SKILLS)
        self.assertIn(needle, refusal.detail)

    def test_no_skills_directory(self) -> None:
        shutil.rmtree(self.f.source / "skills")
        self._refuse("no skills/ directory")

    def test_a_loose_file_in_skills(self) -> None:
        (self.f.source / "skills" / "notes.md").write_text("x", encoding="utf-8")
        self._refuse("is not a directory")

    def test_a_skill_with_no_skill_md(self) -> None:
        shutil.rmtree(self.f.source / "skills" / "fleet-evidence-review")
        (self.f.source / "skills" / "empty-skill").mkdir()
        self._refuse("SKILL.md is absent")

    def test_an_empty_skill_md(self) -> None:
        (self.f.source / "skills" / "fleet-evidence-review" / "SKILL.md").write_text(
            "   \n", encoding="utf-8")
        self._refuse("is empty")

    def test_a_nested_skill_is_invisible_to_a_client_and_refused(self) -> None:
        deep = self.f.source / "skills" / "fleet-evidence-review" / "deeper"
        deep.mkdir()
        (deep / "SKILL.md").write_text("---\nname: deeper\n---\n", encoding="utf-8")
        self._refuse("does not search deeper descendants")


class NothingThatMustNotShipShips(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _refuse(self, needle: str) -> None:
        refusal = self.f.refusal()
        self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
        self.assertIn(needle, refusal.detail)

    def test_a_committed_mcp_json_is_refused(self) -> None:
        (self.f.source / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        self._refuse("a committed `mcp.json`")

    def test_a_symlink_is_refused(self) -> None:
        (self.f.source / "skills" / "link.md").symlink_to("/etc/hostname")
        self._refuse("symlink")

    def test_a_credential_shaped_file_is_refused(self) -> None:
        for body, needle in (
            ("token ccp_" + "a" * 20, "cloudcli_pat"),
            ("Authorization: Bearer " + "z" * 30, "bearer_header"),
        ):
            with self.subTest(needle=needle):
                target = self.f.source / "skills" / "fleet-evidence-review" / "notes.md"
                target.write_text(body, encoding="utf-8")
                self._refuse(needle)
                target.unlink()

    def test_an_env_dump_shape_is_refused(self) -> None:
        target = self.f.source / "skills" / "fleet-evidence-review" / "notes.md"
        target.write_text("FLEET_TOKEN=abcdef123456\n", encoding="utf-8")
        self._refuse("env_dump")

    def test_the_v1_host_may_not_appear_anywhere(self) -> None:
        target = self.f.source / "skills" / "fleet-evidence-review" / "notes.md"
        target.write_text("see https://serveros-fleet.tail3d8b76.ts.net/mcp\n", encoding="utf-8")
        self._refuse("v1_ingress_host")

    def test_the_native_plugin_name_may_not_appear_anywhere(self) -> None:
        target = self.f.source / "skills" / "fleet-evidence-review" / "notes.md"
        target.write_text("this replaces hwz-server-os\n", encoding="utf-8")
        self._refuse("native_plugin_name")

    def test_a_forbidden_suffix_is_refused(self) -> None:
        (self.f.source / "skills" / "fleet-evidence-review" / "id_ed25519").write_text(
            "x", encoding="utf-8")
        self._refuse("credential-shaped")


class NoPlaceholderIsEverWritten(unittest.TestCase):
    """The one output that must never exist: a release-shaped ZIP that blocks."""

    def _blocked_output(self, **overrides) -> list[str]:
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        with self.assertRaises(builder.Refused):
            f.build(**overrides)
        return f.archives()

    def test_a_blocked_build_writes_no_archive_at_all(self) -> None:
        self.assertEqual(self._blocked_output(), [])

    def test_a_blocked_build_with_a_source_bundle_writes_only_the_named_one(self) -> None:
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        try:
            f.build()
        except builder.Refused as refusal:
            manifest = builder.load_manifest(f.source)
            bundle = builder.source_bundle(f.source, f.out, manifest, refusal.status)
        self.assertEqual(bundle["archive"],
                         "serveros-fleet-ingress-v1.0.0" + builder.SOURCE_BUNDLE_SUFFIX)
        self.assertFalse(bundle["installable"])
        self.assertIn(builder.NOT_INSTALLABLE_MEMBER, bundle["members"])
        # …and no release-shaped archive beside it.
        self.assertEqual([n for n in f.archives()
                          if not n.endswith(builder.SOURCE_BUNDLE_SUFFIX)], [])

    def test_the_source_bundle_says_it_is_not_installable_inside_too(self) -> None:
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        manifest = builder.load_manifest(f.source)
        bundle = builder.source_bundle(f.source, f.out, manifest, builder.MISSING_BRAND_ASSET)
        with zipfile.ZipFile(f.out / bundle["archive"]) as zf:
            notice = zf.read(builder.NOT_INSTALLABLE_MEMBER).decode()
        self.assertIn("NOT INSTALLABLE", notice)
        self.assertIn(builder.MISSING_BRAND_ASSET, notice)

    def test_the_source_bundle_carries_no_generated_binding(self) -> None:
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        manifest = builder.load_manifest(f.source)
        bundle = builder.source_bundle(f.source, f.out, manifest, builder.MISSING_BRAND_ASSET)
        self.assertNotIn("mcp.json", bundle["members"])


class TheAppBackedPathNeedsAReceipt(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_it_is_not_reachable_without_a_registration(self) -> None:
        for overrides, needle in (
            ({"app_backed": True}, "not a registration"),
            ({"app_backed": True, "plugin_id": "plugin_typed_by_hand"},
             "registration receipt read back"),
        ):
            with self.subTest(overrides=str(overrides)):
                refusal = self.f.refusal(**overrides)
                self.assertEqual(refusal.status, builder.APP_BINDING_UNREGISTERED)
                self.assertIn(needle, refusal.detail)

    def test_a_receipt_that_is_not_live_is_refused(self) -> None:
        path = self.f.root / "registration.json"
        path.write_text(json.dumps({
            "schema": builder.APP_REGISTRATION_SCHEMA,
            "source": "MOCK", "plugin_id": "plugin_real", "app_id": "app_real",
        }), encoding="utf-8")
        refusal = self.f.refusal(app_backed=True, plugin_id="plugin_real", registration=path)
        self.assertEqual(refusal.status, builder.APP_BINDING_UNREGISTERED)

    def test_a_read_back_registration_is_accepted(self) -> None:
        path = self.f.root / "registration.json"
        path.write_text(json.dumps({
            "schema": builder.APP_REGISTRATION_SCHEMA,
            "source": "LIVE", "plugin_id": "plugin_real", "app_id": "app_real",
            "read_back_at": "2026-10-15T12:00:00Z", "receipt_ref": "evidence/plugin",
        }), encoding="utf-8")
        record = self.f.build(app_backed=True, plugin_id="plugin_real", registration=path)
        self.assertEqual(record["app_binding"]["app_id"], "app_real")

    def test_the_portable_path_writes_no_app_manifest(self) -> None:
        record = self.f.build()
        self.assertIsNone(record["app_binding"])
        self.assertNotIn(".app.json", record["members"])


class TheStatusSetIsClosed(unittest.TestCase):
    def test_every_refusal_names_a_status_a_caller_can_branch_on(self) -> None:
        # Reachable on a complete tree, so each status below is the gate that
        # stopped it rather than an earlier one that happened to fire first.
        whole = Fixture()
        self.addCleanup(whole.close)
        seen = set()
        for overrides in (dict(next_mcp_url=None),
                          dict(next_mcp_url="https://example.com/mcp"),
                          dict(app_backed=True, plugin_id="plugin_x"),
                          dict(v1_mcp_url=OBSERVED_URL)):
            try:
                whole.build(**overrides)
            except builder.Refused as refusal:
                seen.add(refusal.status)
        self.assertEqual(seen, {builder.BLOCKED_MCP_ENDPOINT,
                                builder.APP_BINDING_UNREGISTERED})

        short = Fixture(omit_assets=True)
        self.addCleanup(short.close)
        self.assertEqual(short.refusal().status, builder.MISSING_BRAND_ASSET)
        self.assertTrue(seen | {builder.MISSING_BRAND_ASSET} <= set(builder.STATUSES))

    def test_the_documented_statuses_are_the_ones_defined(self) -> None:
        self.assertEqual(len(builder.STATUSES), len(set(builder.STATUSES)))
        for name in ("READY_TO_BUILD", "BLOCKED_MCP_ENDPOINT", "MISSING_BRAND_ASSET",
                     "BRAND_ASSET_MISMATCH", "MISSING_SKILLS", "SOURCE_REFUSED",
                     "APP_BINDING_UNREGISTERED"):
            with self.subTest(name=name):
                self.assertIn(name, builder.STATUSES)


class TheRepositorysOwnArtifactsStayConsistent(unittest.TestCase):
    def test_the_pinned_hashes_match_the_document_that_records_them(self) -> None:
        """Two files carry these digests; a release must not have two answers."""
        lock = json.loads((HERE / "brand-assets.lock.json").read_text(encoding="utf-8"))
        pinned = {slot["slot"]: slot["expected_sha256"] for slot in lock["slots"]}
        self.assertEqual(
            pinned["composerIcon"],
            "644cf5a3ea36bcb16d1d560af45b3bec84aa4310ea510cdabcf80b304f13b111")
        self.assertEqual(
            pinned["logo"],
            "31bd3a24c3379190a8523a5dad0331d87b57a439f83a3d4ff9b77778d0d5c5f9")
        self.assertEqual(
            lock["approved_source"]["sha256"],
            "b5ae68cd337a29cc3d6a6df3212041c496c5432544380e84b784306e65db4fec")

        doc = (ROOT / "integrations" / "fleet-app-overlay"
               / "NEXT-BRAND-ASSET-LOCK.md").read_text(encoding="utf-8")
        for digest in pinned.values():
            with self.subTest(digest=digest[:12]):
                self.assertIn(digest, doc)
        self.assertIn(lock["approved_source"]["sha256"], doc)

    def test_the_lock_states_the_absent_behaviour(self) -> None:
        lock = json.loads((HERE / "brand-assets.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["on_absent_or_mismatched"], builder.MISSING_BRAND_ASSET)

    def test_no_approved_image_is_committed_here(self) -> None:
        """`status` in the lock says the bytes are not in this repository, and
        this asserts it: a PNG appearing under assets/ would make the lock's
        own warning false."""
        assets = HERE / "plugin" / "assets"
        if assets.is_dir():
            committed = sorted(p.name for p in assets.iterdir() if p.suffix == ".png")
            self.assertEqual(committed, [],
                             "an image was committed; the lock says the approved "
                             "bytes are not in this repository")


if __name__ == "__main__":
    unittest.main()
