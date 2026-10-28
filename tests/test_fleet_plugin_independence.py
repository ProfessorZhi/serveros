"""The L4 artifact is data, and a Skill update is not an L1/L2 release.

`plugins/serveros-fleet-ingress/` is what a commander side installs. Three
properties decide whether it can be updated on its own, and all three are
checked here rather than asserted in prose:

1. **It carries no executable code.** A plugin that shipped a script would have
   to be re-reviewed, re-trusted and possibly re-signed every time it changed.
   The artifact is manifests, Skills and images, so a Skill edit is a text edit.
2. **A Skill edit moves Skill bytes and nothing else.** The MCP binding
   (`.mcp.json`) is the artifact's only coupling to L2, and it carries the URL
   and the scope string — the things a re-consent is keyed on. If editing a
   Skill changed either, a documentation fix would force every commander to
   re-authorize and the hub to be rebuilt. This test edits one Skill in a copy
   of the source, regenerates, and requires the diff to be that one file.
3. **Both client layouts read one source.** Claude Code reads
   `.claude-plugin/plugin.json` + `.mcp.json`; Codex reads
   `.codex-plugin/plugin.json` + `.codex-mcp.json`. They are separate packages
   on purpose (see `docs/claude-codex-parity.md`: a `.codex-plugin` directory is
   not a supported format just because `.claude-plugin` exists), but every
   `SKILL.md` is byte-identical in both, so one Skill edit updates both clients
   without either runtime's packaging being rebuilt.

The version is written once, in the source manifest, and must reach both of the
Claude Code carriers unchanged: `plugins/serveros-fleet-ingress/.claude-plugin/
plugin.json` and the root `.claude-plugin/marketplace.json`. Claude Code's
`install`/`update` keys on the version, so a Skill edit that shipped under an
unchanged version would be an edit no installed client ever picked up. The
Codex development adapter keeps its own `0.0.1` and is deliberately outside
that authority — it is a development artefact, not a second release.

No network, no model call, no install, no service restart.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "plugins" / "serveros-fleet-ingress"
FIRST_INSTALL = ROOT / "integrations" / "fleet-v1-first-install"
DEV_ADAPTER = ROOT / "integrations" / "fleet-cc-dev-plugin"
ADAPTER = DEV_ADAPTER / "build_release_adapter.py"
MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"

#: Extensions that would make the artifact executable, or archives/objects that
#: carry code without an extension a reader recognises.
CODE_SUFFIXES = frozenset({
    ".py", ".pyc", ".pyd", ".sh", ".bash", ".zsh", ".bat", ".cmd", ".ps1",
    ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".so", ".dylib", ".dll",
    ".exe", ".jar", ".class", ".mcpb", ".whl",
})
ELF_MAGIC = b"\x7fELF"

#: What may live under `assets/`. Anything else there makes the directory a
#: place a text file — or a payload — can accumulate unnoticed.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"})


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


adapter = _load("fleet_plugin_independence_adapter", ADAPTER)
codex = _load("fleet_plugin_independence_codex", DEV_ADAPTER / "build_codex_plugin.py")
#: The URL the committed tree was built against. Read from the builder rather
#: than repeated here: a second copy of the endpoint would be the very drift
#: `scripts/fleet_plugin.py check` exists to catch.
fleet_plugin = _load("fleet_plugin_independence_tool", ROOT / "scripts" / "fleet_plugin.py")
COMMITTED_URL = fleet_plugin.DEFAULT_NEXT_MCP_URL


def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _generate_from(source_root: Path, out: Path) -> dict:
    """Generate a plugin tree from an alternative source root.

    The adapter reads its inputs from two module-level paths, so a copy of the
    source is exercised by pointing them at the copy for the duration of one
    generation and restoring them afterwards. Both are restored in `finally`
    because a leaked patch would silently make every later generation read
    somebody else's Skills.
    """
    saved = adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK
    adapter.RELEASE_PLUGIN = source_root / "plugin"
    adapter.SHARED_LOCK = source_root / "brand-assets.lock.json"
    try:
        return adapter.generate(out=out, next_mcp_url=COMMITTED_URL)
    finally:
        adapter.RELEASE_PLUGIN, adapter.SHARED_LOCK = saved


class TheArtifactIsData(unittest.TestCase):
    def test_the_committed_plugin_exists(self) -> None:
        # Without this, both checks below pass on a directory that is missing,
        # renamed, or never generated — which is the packaging failure they
        # exist to catch, reported as success.
        self.assertTrue(PLUGIN_DIR.is_dir(), f"{PLUGIN_DIR} is missing")
        self.assertTrue(_tree(PLUGIN_DIR), f"{PLUGIN_DIR} is empty")

    def test_the_committed_plugin_carries_no_executable_code(self) -> None:
        offenders = []
        for path in sorted(PLUGIN_DIR.rglob("*")):
            rel = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                # A link read through the plugin would carry code from outside
                # the reviewed tree, with neither an extension nor a magic
                # number this test could see.
                offenders.append(f"{rel} (symlink)")
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in CODE_SUFFIXES:
                offenders.append(f"{rel} (extension)")
                continue
            head = path.read_bytes()[:4]
            if head.startswith(b"#!"):
                offenders.append(f"{rel} (shebang)")
            elif head == ELF_MAGIC:
                offenders.append(f"{rel} (ELF)")
        self.assertEqual(offenders, [],
                         "the commander plugin must be manifests, Skills and "
                         "images only; a script makes every Skill edit a "
                         "re-trusted code change")

    def test_the_artifact_is_manifests_skills_and_images_only(self) -> None:
        members = sorted(_tree(PLUGIN_DIR))
        self.assertTrue(members, "an empty tree satisfies every rule below")
        allowed = (".claude-plugin/plugin.json", ".mcp.json", "README.md",
                   "PRIVACY.md")
        for rel in members:
            with self.subTest(member=rel):
                self.assertTrue(
                    rel in allowed
                    or (rel.startswith("skills/") and rel.endswith("/SKILL.md"))
                    or (rel.startswith("assets/")
                        and Path(rel).suffix.lower() in IMAGE_SUFFIXES),
                    f"{rel} is not a manifest, a Skill or an image")


class ASkillUpdateIsNotAnL2Release(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l4-independence-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Only the two inputs the release adapter reads are copied; the task
        # runners next to them are not part of a generation.
        self.source = self.tmp / "source"
        shutil.copytree(FIRST_INSTALL / "plugin", self.source / "plugin")
        shutil.copyfile(FIRST_INSTALL / "brand-assets.lock.json",
                        self.source / "brand-assets.lock.json")

    def test_a_skill_edit_changes_only_that_skill_file(self) -> None:
        before = self.tmp / "before"
        _generate_from(self.source, before)

        # Both halves of the Skill, because the plausible regression is a
        # builder that echoes the front matter — a `description` or a Skill
        # count — into a manifest. A body-only edit could not detect that.
        edited = self.source / "plugin" / "skills" / "fleet-evidence-review" / "SKILL.md"
        text = edited.read_text(encoding="utf-8")
        head, rest = text.split("\n---", 1)
        edited.write_text(
            head.replace("description: ", "description: Reviewed. ", 1)
            + "\n---" + rest + "\n<!-- reviewed 2026-10-27 -->\n",
            encoding="utf-8")
        self.assertIn("Reviewed.", edited.read_text(encoding="utf-8"))

        after = self.tmp / "after"
        _generate_from(self.source, after)

        old, new = _tree(before), _tree(after)
        changed = sorted(rel for rel in set(old) | set(new)
                         if old.get(rel) != new.get(rel))
        self.assertEqual(changed, ["skills/fleet-evidence-review/SKILL.md"],
                         "a Skill edit must not move the manifest, the binding "
                         "or any other Skill")
        self.assertEqual(new[".mcp.json"], old[".mcp.json"])
        self.assertEqual(new[".claude-plugin/plugin.json"],
                         old[".claude-plugin/plugin.json"])

    def test_the_binding_is_a_function_of_the_url_and_scopes_alone(self) -> None:
        """No Skill byte may reach the binding.

        This is the mechanical reason a Skill edit cannot force a re-consent:
        the file a commander's client reads for the endpoint and scopes is
        derived from the endpoint and the scope set, not from anything the
        Skills say.
        """
        scopes = " ".join(adapter.RELEASE_SCOPES)
        expected = adapter.mcp_config(COMMITTED_URL, scopes)
        committed = json.loads((PLUGIN_DIR / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(committed, expected)
        # Exactly the three keys a client reads for the endpoint and the
        # consent, and nothing carried over from the Skills.
        (server,) = committed["mcpServers"].values()
        self.assertEqual(set(server), {"type", "url", "oauth"})
        self.assertEqual(server["url"], COMMITTED_URL)
        self.assertEqual(set(server["oauth"]), {"scopes", "callbackPort"})


class TheVersionHasOneAuthority(unittest.TestCase):
    def test_source_and_both_generated_carriers_name_the_same_version(self) -> None:
        source = json.loads((FIRST_INSTALL / "plugin" / "plugin.json")
                            .read_text(encoding="utf-8"))
        generated = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json")
                               .read_text(encoding="utf-8"))
        market = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        (entry,) = market["plugins"]
        self.assertEqual(source["version"], generated["version"])
        self.assertEqual(source["version"], entry["version"],
                         "Claude Code's install/update keys on this version; a "
                         "Skill edit under an unchanged version is one no "
                         "installed client picks up")

    def test_a_fresh_generation_reports_the_same_version(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="l4-version-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        source_tmp = tmp / "source"
        shutil.copytree(FIRST_INSTALL / "plugin", source_tmp / "plugin")
        shutil.copyfile(FIRST_INSTALL / "brand-assets.lock.json",
                        source_tmp / "brand-assets.lock.json")
        record = _generate_from(source_tmp, tmp / "out")
        self.assertEqual(record["version"],
                         json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json")
                                    .read_text(encoding="utf-8"))["version"])


class BothClientLayoutsReadOneSource(unittest.TestCase):
    def test_the_codex_layout_carries_the_same_skill_bytes(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="l4-codex-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "codex"
        codex.generate(skills_source=FIRST_INSTALL / "plugin" / "skills",
                       out=out, next_mcp_url=COMMITTED_URL)
        names = sorted(p.name for p in (FIRST_INSTALL / "plugin" / "skills").iterdir()
                       if p.is_dir())
        self.assertTrue(names)
        for name in names:
            with self.subTest(skill=name):
                self.assertEqual(
                    (out / "skills" / name / "SKILL.md").read_bytes(),
                    (PLUGIN_DIR / "skills" / name / "SKILL.md").read_bytes(),
                    "the two client layouts must ship one Skill source")


if __name__ == "__main__":
    unittest.main()
