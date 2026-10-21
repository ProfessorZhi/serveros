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
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from contextlib import redirect_stderr
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
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


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}"


def observation(url: str = OBSERVED_URL, **overrides) -> dict:
    """The shape an operator's endpoint receipt must have.

    Every value here is something a person typed. `source: LIVE` is a field, not
    a measurement, and the builder says so in its own record; this helper exists
    so the *shape* is checkable, not so the claim is.
    """
    base = origin(url)
    record = {
        "schema": builder.ENDPOINT_OBSERVATION_SCHEMA,
        "url": url,
        "source": "LIVE",
        "observed_at": "2026-10-15T12:00:00Z",
        "reachable": True,
        "tls": {"scheme": "https", "verified": True},
        "discovery": {
            "protected_resource": {"url": base + "/.well-known/oauth-protected-resource",
                                   "status": 200, "receipt_ref": "evidence/obs/prm"},
            "authorization_server": {
                "url": base + "/.well-known/oauth-authorization-server",
                "status": 200, "receipt_ref": "evidence/obs/as",
                "issuer": base,
                "scopes_supported": list(builder.FLEET_SCOPES),
            },
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
                 "square_required": True},
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

    def retemplate(self, mutate) -> None:
        """Edit `mcp.template.json` in place, and hand the caller the refusal."""
        path = self.source / "mcp.template.json"
        template = json.loads(path.read_text(encoding="utf-8"))
        mutate(template)
        path.write_text(json.dumps(template, indent=2), encoding="utf-8")

    def remanifest(self, mutate) -> None:
        path = self.source / "plugin.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        mutate(manifest)
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def binding(self, **overrides) -> dict:
        record = self.build(**overrides)
        with zipfile.ZipFile(self.out / record["archive"]) as zf:
            return json.loads(zf.read("mcp.json"))

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
        self.assertEqual(len(record["skills"]),
                         len(builder.check_skills(HERE / "plugin")))

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
        self.assertIn("required to be square", refusal.detail)

    def test_a_logo_must_be_square_too(self) -> None:
        """Was `test_a_logo_may_keep_its_aspect_ratio`, which asserted the
        opposite.

        The documented rule is "Icons and logos must be square and at least 48 by
        48 pixels" — it names logos explicitly, so the listing logo is held to
        the same shape as the composer avatar. Allowing a wordmark here was a
        preference wearing a rule's clothes, and it bought nothing: the approved
        asset is 1024x1024.
        """
        f = Fixture(logo=1024)
        self.addCleanup(f.close)
        data = make_png(1024, 256)
        f.assets.joinpath("logo.png").write_bytes(data)
        f.lock["slots"][1]["expected_sha256"] = hashlib.sha256(data).hexdigest()
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.BRAND_ASSET_MISMATCH)
        self.assertIn("required to be square", refusal.detail)

    def test_a_square_logo_builds(self) -> None:
        """The positive control: the rule refuses a shape, not the slot."""
        f = Fixture(logo=1024)
        self.addCleanup(f.close)
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

    def test_a_blocked_build_refuses_a_source_bundle_over_a_credential(self) -> None:
        """The fallback is refused, not merely renamed.

        `SOURCE-ONLY` is a label, not a permission: the bundle is written to a
        directory an operator uploads and mirrors, so a tree the audit refuses
        for a credential must not become an archive because the release build
        was refused first — that would package the very file whose discovery
        blocked the release. The refusal has to survive the fallback.
        """
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        (f.source / ".env").write_text("FLEET_TOKEN=abcdef123456\n", encoding="utf-8")
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
        manifest = builder.load_manifest(f.source)
        with self.assertRaises(builder.Refused) as caught:
            builder.source_bundle(f.source, f.out, manifest, refusal.status)
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertEqual(f.archives(), [])

    def test_a_source_bundle_does_not_smuggle_a_committed_binding(self) -> None:
        """A committed `mcp.json` blocks the release, so it may not ride along.

        It is refused by the audit rather than dropped from the member list: a
        file that is silently omitted is one a reader of this archive cannot
        tell from a file that was never there, and the binding is the member
        the whole fallback claims not to carry.
        """
        f = Fixture(omit_assets=True)
        self.addCleanup(f.close)
        (f.source / "mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        refusal = f.refusal()
        self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
        manifest = builder.load_manifest(f.source)
        with self.assertRaises(builder.Refused):
            builder.source_bundle(f.source, f.out, manifest, refusal.status)
        self.assertEqual(f.archives(), [])


class TheReleaseIsNotWrittenBackIntoItsOwnSource(unittest.TestCase):
    """An output inside the source changes the tree the *next* run reads.

    The audit runs before anything is written, so a release archive landing in
    the source is not merely untidy: `audit_source` accepts a `.zip`, so a later
    `--source-bundle` would carry a release archive inside the archive that
    claims to be the source, and two builds of "one source" would no longer be
    two builds of one source.

    The `dist` and `out` names are refused by the audit — on the **next** run,
    by accident. A test that used either would therefore pass against a builder
    with no guard at all, which is why one case below deliberately names a
    directory the audit allows.
    """

    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _tree(self) -> list[str]:
        return sorted(p.relative_to(self.f.source).as_posix()
                      for p in self.f.source.rglob("*"))

    def test_an_out_equal_to_the_source_is_refused_with_zero_writes(self) -> None:
        before = self._tree()
        with self.assertRaises(builder.Refused) as caught:
            builder.build(self.f.source, self.f.source, self.f.lock, **self.f.kwargs())
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertIn("inside it", caught.exception.detail)
        self.assertEqual(self._tree(), before)

    def test_an_out_below_the_source_is_refused_for_a_name_the_audit_allows(self) -> None:
        inside = self.f.source / "build"       # not `dist`/`out`: those are refused later
        before = self._tree()
        with self.assertRaises(builder.Refused) as caught:
            builder.build(self.f.source, inside, self.f.lock, **self.f.kwargs())
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertFalse(inside.exists())
        self.assertEqual(self._tree(), before)

    def test_a_symlinked_out_that_resolves_into_the_source_is_refused(self) -> None:
        link = self.f.root / "out-link"
        link.symlink_to(self.f.source)
        with self.assertRaises(builder.Refused) as caught:
            builder.build(self.f.source, link, self.f.lock, **self.f.kwargs())
        self.assertEqual(caught.exception.status, builder.SOURCE_REFUSED)
        self.assertEqual([p for p in self.f.source.rglob("*.zip")], [])


class ARefusalIsAStatusAndNotATraceback(unittest.TestCase):
    """The CLI's own contract, on the one input read outside the source.

    `Refused` documents itself as carrying "a status and a reason, not a
    traceback". The lock is the brand-asset authority — the file this whole
    package exists to enforce — so a mistyped `--lock` path is the likeliest
    operator error there is, and it used to arrive as a stack trace.
    """

    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_a_malformed_lock_is_a_status_not_a_traceback(self) -> None:
        bad = self.f.root / "bad-lock.json"
        bad.write_text("not json\n", encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            code = builder.main(["--source", str(self.f.source), "--lock", str(bad),
                                 "--out", str(self.f.out)])
        self.assertEqual(code, 2)
        self.assertIn(builder.SOURCE_REFUSED, err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_a_missing_lock_is_a_status_not_a_traceback(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = builder.main(["--source", str(self.f.source),
                                 "--lock", str(self.f.root / "absent-lock.json"),
                                 "--out", str(self.f.out)])
        self.assertEqual(code, 2)
        self.assertIn(builder.SOURCE_REFUSED, err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


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


class ThePresentationFieldsLiveWhereTheClientReadsThem(unittest.TestCase):
    """A slot declared under our own namespace asks nobody in particular.

    This is the whole of the P0 branding finding: the approved artwork existed,
    the digest was pinned, and the declaration was in a namespace the client is
    not documented to read — so the package looked complete and had not asked
    for the image to be rendered at all.
    """

    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_the_openai_interface_block_is_required(self) -> None:
        self.f.remanifest(lambda m: m["extensions"].pop("com.openai"))
        refusal = self.f.refusal()
        self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
        self.assertIn("extensions.com.openai", refusal.detail)

    def test_the_presentation_fields_must_be_nested_under_interface(self) -> None:
        def flatten(manifest):
            block = manifest["extensions"]["com.openai"]
            block.update(block.pop("interface"))

        self.f.remanifest(flatten)
        self.assertIn("interface", self.f.refusal().detail)

    def test_the_two_icon_paths_are_the_shipped_asset_paths(self) -> None:
        record = self.f.build()
        self.assertEqual(record["presentation"],
                         {"composerIcon": "./assets/composerIcon.png",
                          "logo": "./assets/logo.png"})

    def test_a_path_that_is_not_the_locked_one_is_refused(self) -> None:
        def move(manifest):
            manifest["extensions"]["com.openai"]["interface"]["logo"] = "./assets/other.png"

        self.f.remanifest(move)
        self.assertIn("different image", self.f.refusal().detail)

    def test_a_relative_path_that_escapes_the_plugin_is_refused(self) -> None:
        # Refused as an escape rather than as a mismatch: the two have different
        # fixes, and the path has to resolve before a digest is worth discussing.
        self.f.remanifest(
            lambda m: m["extensions"]["com.openai"]["interface"].update(
                {"logo": "./../shared/logo.png"}))
        self.assertIn("does not escape the plugin", self.f.refusal().detail)

    def test_the_display_name_is_part_of_the_approved_release(self) -> None:
        self.f.remanifest(
            lambda m: m["extensions"]["com.openai"]["interface"].update(
                {"displayName": "Fleet"}))
        self.assertIn("approved name", self.f.refusal().detail)

    def test_both_slots_are_required(self) -> None:
        for field in ("composerIcon", "logo", "displayName"):
            with self.subTest(field=field):
                f = Fixture()
                self.addCleanup(f.close)
                f.remanifest(
                    lambda m, field=field: m["extensions"]["com.openai"]["interface"].pop(field))
                self.assertEqual(f.refusal().status, builder.SOURCE_REFUSED)

    def test_the_custom_namespace_may_not_repeat_a_presentation_field(self) -> None:
        self.f.remanifest(
            lambda m: m["extensions"]["com.serveros.fleet-ingress"].update(
                {"composerIcon": "./assets/composerIcon.png"}))
        self.assertIn("repeats", self.f.refusal().detail)

    def test_the_custom_namespace_is_still_allowed_for_provenance(self) -> None:
        record = self.f.build()
        self.assertEqual(record["skills"].__len__(),
                         len(builder.check_skills(HERE / "plugin")))

    def test_passing_this_does_not_claim_a_render(self) -> None:
        """The check is about the manifest; rendering is a client behaviour."""
        manifest = json.loads((self.f.source / "plugin.json").read_text(encoding="utf-8"))
        note = manifest["extensions"]["com.openai"]["note"]
        self.assertIn("not a claim that a client renders", note)


class TheBindingDeclaresTheAuthTheServerOffers(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _auth(self, mutate) -> dict:
        self.f.retemplate(lambda t: mutate(
            t["mcpServers"]["serveros-fleet"]["extensions"]["com.openai"]["auth"]))
        return self.f.binding()["mcpServers"]["serveros-fleet"]["extensions"]["com.openai"]["auth"]

    def _refuse(self, needle: str, mutate) -> None:
        """Apply the mutation to the template, then assert the refusal names it."""
        self.f.retemplate(lambda t: mutate(
            t["mcpServers"]["serveros-fleet"]["extensions"]["com.openai"]["auth"]))
        refusal = self.f.refusal()
        self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
        self.assertIn(needle, refusal.detail)

    def test_the_shipped_binding_declares_oauth_with_dynamic_registration(self) -> None:
        server = self.f.binding()["mcpServers"]["serveros-fleet"]
        auth = server["extensions"]["com.openai"]["auth"]
        self.assertEqual(auth["type"], "oauth")
        self.assertEqual(auth["client"], {"mode": "dcr"})

    def test_no_client_id_is_invented_before_registration(self) -> None:
        auth = self.f.binding()["mcpServers"]["serveros-fleet"]["extensions"]["com.openai"]["auth"]
        self.assertNotIn("clientId", json.dumps(auth))

    def test_the_binding_top_level_is_exactly_the_two_documented_keys(self) -> None:
        binding = self.f.binding()
        self.assertEqual(set(binding), {"$schema", "mcpServers"})

    def test_the_auth_block_is_required(self) -> None:
        self.f.retemplate(lambda t: t["mcpServers"]["serveros-fleet"]["extensions"]["com.openai"].pop("auth"))
        self.assertIn("declares no `auth`", self.f.refusal().detail)

    def test_an_auth_block_under_another_namespace_is_refused(self) -> None:
        self.f.retemplate(
            lambda t: t["mcpServers"]["serveros-fleet"]["extensions"].update(
                {"com.example": t["mcpServers"]["serveros-fleet"]["extensions"].pop("com.openai")}))
        self.assertIn("com.openai", self.f.refusal().detail)

    def test_the_documented_but_wrong_auth_types_are_refused_by_name(self) -> None:
        for auth_type in ("none", "api_key", "mixed"):
            with self.subTest(auth_type=auth_type):
                f = Fixture()
                self.addCleanup(f.close)
                f.retemplate(lambda t, auth_type=auth_type: t["mcpServers"]["serveros-fleet"]
                             ["extensions"]["com.openai"]["auth"].update({"type": auth_type}))
                refusal = f.refusal()
                self.assertEqual(refusal.status, builder.SOURCE_REFUSED)
                self.assertIn(auth_type, refusal.detail)

    def test_an_undocumented_auth_type_is_refused(self) -> None:
        self._refuse("not one of the documented", lambda a: a.update({"type": "hmac"}))

    def test_an_unknown_auth_field_is_refused(self) -> None:
        self._refuse("does not define", lambda a: a.update({"resource2": "x"}))

    def test_a_client_id_is_refused(self) -> None:
        self._refuse("names a `clientId`",
                     lambda a: a["client"].update({"clientId": "invented"}))

    def test_client_credentials_are_refused_at_any_depth(self) -> None:
        for key in ("clientSecret", "client_secret", "secret", "password"):
            with self.subTest(key=key):
                f = Fixture()
                self.addCleanup(f.close)
                f.retemplate(lambda t, key=key: t["mcpServers"]["serveros-fleet"]
                             ["extensions"]["com.openai"]["auth"]["client"].update({key: "s3cret"}))
                self.assertIn("credential", f.refusal().detail)

    def test_provided_and_cimd_modes_are_refused(self) -> None:
        for mode in ("provided", "cimd"):
            with self.subTest(mode=mode):
                f = Fixture()
                self.addCleanup(f.close)
                f.retemplate(lambda t, mode=mode: t["mcpServers"]["serveros-fleet"]
                             ["extensions"]["com.openai"]["auth"]["client"].update({"mode": mode}))
                refusal = f.refusal()
                self.assertIn(mode, refusal.detail)

    def test_an_unknown_mode_is_refused(self) -> None:
        self._refuse("documented", lambda a: a["client"].update({"mode": "magic"}))

    def test_a_secret_bearing_token_auth_method_is_refused(self) -> None:
        for method in ("client_secret_basic", "client_secret_post"):
            with self.subTest(method=method):
                f = Fixture()
                self.addCleanup(f.close)
                f.retemplate(lambda t, method=method: t["mcpServers"]["serveros-fleet"]
                             ["extensions"]["com.openai"]["auth"]["client"].update(
                                 {"tokenEndpointAuthMethod": method}))
                self.assertIn("shared secret", f.refusal().detail)

    def test_a_secretless_token_auth_method_is_allowed(self) -> None:
        auth = self._auth(lambda a: a["client"].update({"tokenEndpointAuthMethod": "none"}))
        self.assertEqual(auth["client"]["tokenEndpointAuthMethod"], "none")

    def test_legacy_bearer_headers_are_still_refused(self) -> None:
        self.f.retemplate(lambda t: t["mcpServers"]["serveros-fleet"].update(
            {"headers": {"Authorization": "Bearer placeholder"}}))
        self.assertIn("declares headers", self.f.refusal().detail)

    def test_an_auth_url_on_another_origin_is_refused(self) -> None:
        self._refuse("own origin", lambda a: a.update(
            {"registrationUrl": "https://elsewhere.example.invalid/register"}))

    def test_a_same_origin_registration_url_is_allowed(self) -> None:
        auth = self._auth(lambda a: a.update(
            {"registrationUrl": origin(OBSERVED_URL) + "/register"}))
        self.assertTrue(auth["registrationUrl"].endswith("/register"))

    def test_an_auth_url_may_not_carry_a_credential_or_query(self) -> None:
        for bad in ("https://u:p@" + urlsplit(OBSERVED_URL).hostname + "/register",
                    origin(OBSERVED_URL) + "/register?code=abc",
                    origin(OBSERVED_URL) + "/register#frag",
                    "http://" + urlsplit(OBSERVED_URL).hostname + "/register"):
            with self.subTest(bad=bad):
                self._refuse("absolute HTTPS URL", lambda a, bad=bad: a.update({"registrationUrl": bad}))

    def test_a_scope_list_outside_the_fleet_scopes_is_refused(self) -> None:
        self._refuse("does not mint",
                     lambda a: a.update({"baseScopes": ["fleet:read", "fleet:admin"]}))

    def test_a_scope_list_inside_the_fleet_scopes_is_allowed(self) -> None:
        auth = self._auth(lambda a: a.update({"baseScopes": ["fleet:read", "fleet:send"]}))
        self.assertEqual(auth["baseScopes"], ["fleet:read", "fleet:send"])

    def test_an_ill_formed_scope_token_is_refused(self) -> None:
        for bad in (["read"], ["fleet:"], ["fleet:read", ""]):
            with self.subTest(bad=bad):
                self._refuse("scope token", lambda a, bad=bad: a.update({"baseScopes": bad}))

    def test_an_empty_scope_list_is_refused(self) -> None:
        self._refuse("non-empty list", lambda a: a.update({"defaultScopes": []}))

    def test_a_bare_scope_string_is_refused_rather_than_wrapped(self) -> None:
        # A string is not a one-element list, and reading it as one is how a
        # typo becomes a declaration.
        self._refuse("non-empty list", lambda a: a.update({"baseScopes": "fleet:read"}))


class TheObservationMustBeInternallyConsistent(unittest.TestCase):
    """The receipt is an operator's claim; it must at least not contradict itself."""

    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def _blocked(self, record: dict) -> str:
        path = self.f.root / "mutated.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        refusal = self.f.refusal(observation=path)
        self.assertEqual(refusal.status, builder.BLOCKED_MCP_ENDPOINT)
        return refusal.detail

    def test_discovery_on_another_origin_is_refused(self) -> None:
        record = observation()
        record["discovery"]["protected_resource"]["url"] = \
            "https://other.tail3d8b76.ts.net/.well-known/oauth-protected-resource"
        self.assertIn("ingress's own", self._blocked(record))

    def test_a_discovery_url_with_a_credential_component_is_refused(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"]["url"] = \
            "https://u:p@" + urlsplit(OBSERVED_URL).hostname + "/.well-known/oauth-authorization-server"
        self.assertIn("credential component", self._blocked(record))

    def test_the_issuer_must_be_the_endpoints_origin(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"]["issuer"] = "https://elsewhere.example.invalid"
        self.assertIn("must be the pair that was observed together", self._blocked(record))

    def test_a_missing_issuer_is_refused(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"].pop("issuer")
        self.assertIn("names no absolute HTTPS issuer", self._blocked(record))

    def test_an_issuer_with_a_query_is_refused(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"]["issuer"] = origin(OBSERVED_URL) + "/?x=1"
        self.assertIn("credential component", self._blocked(record))

    def test_the_six_fleet_scopes_must_be_advertised(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"]["scopes_supported"] = ["fleet:read", "fleet:send"]
        detail = self._blocked(record)
        self.assertIn("does not advertise", detail)
        self.assertIn("fleet:create", detail)

    def test_a_missing_scopes_list_is_refused(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"].pop("scopes_supported")
        self.assertIn("scopes_supported", self._blocked(record))

    def test_an_ill_formed_scope_is_refused(self) -> None:
        record = observation()
        record["discovery"]["authorization_server"]["scopes_supported"] = \
            list(builder.FLEET_SCOPES) + ["read"]
        self.assertIn("scopes_supported", self._blocked(record))


class TheReceiptIsNotAProbe(unittest.TestCase):
    def setUp(self) -> None:
        self.f = Fixture()
        self.addCleanup(self.f.close)

    def test_the_record_says_what_the_endpoint_evidence_is(self) -> None:
        record = self.f.build()
        self.assertEqual(record["endpoint_evidence"], "OPERATOR_SUPPLIED_RECEIPT")
        self.assertFalse(record["endpoint_probe_performed_by_this_tool"])

    def test_the_record_does_not_describe_the_manifest_as_live(self) -> None:
        record = self.f.build()
        self.assertIn("not a", record["warning"])
        self.assertIn("probe this tool performed", record["warning"])

    def test_the_six_scopes_still_match_the_surface_that_mints_them(self) -> None:
        """Named once here, asserted against `fleet_mcp.scopes` rather than
        trusted: a scope added to the surface must fail this build."""
        from fleet_mcp import scopes

        self.assertEqual(tuple(builder.FLEET_SCOPES), tuple(scopes.IMPLEMENTED_SCOPES))
        self.assertEqual(set(builder.FLEET_SCOPES), set(scopes.ALLOWED_SCOPES))

    def test_the_builder_has_no_network_or_creator_path(self) -> None:
        source = (HERE / "build_first_install.py").read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "socket", "http.client", "subprocess"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(f"import {forbidden}", source)


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

    def test_the_lock_distinguishes_absent_and_mismatched_assets(self) -> None:
        lock = json.loads((HERE / "brand-assets.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["on_absent"], builder.MISSING_BRAND_ASSET)
        self.assertEqual(lock["on_mismatched"], builder.BRAND_ASSET_MISMATCH)

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
