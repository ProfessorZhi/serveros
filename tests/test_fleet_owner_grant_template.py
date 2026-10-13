#!/usr/bin/env python3
"""The configuration template has to be a file the loaders accept.

A runbook is a claim about a program. The claims in
`fleet/owner-grant-3devices.md` are the kind that fail quietly: a key spelled
`models` in the doc and read as `models` by the loader is fine, one spelled
`allow_models` is a binding that refuses to load at the worst possible moment —
during a cutover, on a host somebody is trying not to disturb — or, worse,
loads and does not mean what the operator read.

So this does not check the prose. It takes the two YAML blocks **out of the
document, verbatim**, fills the `ANGLE_CAPS` placeholders with test values, and
runs them through the real loaders. A key that the doc invents is a load
failure here, and a key that the doc gets right but the loader ignores is caught
by the assertions that follow, which name the properties the template is for.

The placeholders are substituted rather than left, because `device:<COMPANY_DEVICE_A>`
is not a typed ref and the loader is right to refuse it — that refusal is a
different fact from "this key is misspelled", and allowing it to fire here
would make the test pass for the wrong reason.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fleet_mcp.config import load_config  # noqa: E402
from fleet_mcp.node.connector import load_node_config  # noqa: E402
from fleet_mcp.scopes import SCOPE_CONTROL, SCOPE_CREATE  # noqa: E402

DOC = REPO / "fleet" / "owner-grant-3devices.md"

#: `device:<A>` -> `device:device-a`, and so on. Applied to the whole block, so
#: a placeholder the doc adds later is substituted without editing this file —
#: and a placeholder it adds that this map does not know is caught by the guard
#: in `setUpClass` rather than surviving as a literal in a test fixture.
SUBSTITUTIONS = {
    # Order matters: `<A_PORT>` before `<A>`, or the longer placeholder is left
    # as `company-a_PORT>`. Each entry is tried against the whole block in turn.
    "<COMPANY_DEVICE_A>": "company-a",
    "<COMPANY_DEVICE_B>": "company-b",
    "<COMPANY_DEVICE_C>": "company-c",
    "<A_PORT>": "3402",
    "<B_PORT>": "3403",
    "<C_PORT>": "3404",
    "<PROJECT_ID_A>": "project-alpha",
    "<PROJECT_ID_B>": "project-beta",
    "<FLEET_PUBLIC_HOST>": "fleet.example.invalid",
    "<REAL_CLIENT_ID>": "client-id-for-this-test",
    "<CLOUDCLI_PORT>": "13001",
    "<A>": "company-a",
    "<B>": "company-b",
    "<C>": "company-c",
}

YAML_BLOCK = re.compile(r"```yaml\n(.*?)```", re.DOTALL)
PLACEHOLDER = re.compile(r"<[A-Z][A-Z0-9_]*>")


def blocks() -> list[str]:
    # Newlines are normalised rather than trusted. This checkout is
    # `core.autocrlf=true`, so the same commit yields LF here and CRLF on the
    # next checkout of this machine — and the write below would then send
    # `\r\r\n` through text mode into a YAML parser, which is a failure with
    # nothing to do with the document. The subject of this test is the key set,
    # not the line ending.
    text = DOC.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return YAML_BLOCK.findall(text)


def fill(block: str) -> str:
    for placeholder, value in SUBSTITUTIONS.items():
        block = block.replace(placeholder, value)
    return block


class TheTemplateIsALoadableBinding(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_the_document_has_exactly_the_two_blocks_and_no_unknown_placeholder(self):
        found = blocks()
        self.assertEqual(
            len(found), 2,
            "expected a Fleet block and a node block; the count changed, so the "
            "assertions below may now be reading a different block than the one "
            "they were written for",
        )
        for index, block in enumerate(found):
            with self.subTest(block=index):
                self.assertEqual(
                    PLACEHOLDER.findall(fill(block)), [],
                    "the template gained a placeholder that SUBSTITUTIONS does "
                    "not know, so the loader below would be asked to accept a "
                    "literal angle-bracket as a real value",
                )
        self.assertIn("schema: serveros/fleet-mcp.local/v1", found[0])
        self.assertIn("schema: serveros/fleet-node.local/v1", found[1])

    def _write(self, name: str, text: str) -> Path:
        path = self.root / name
        # `newline=""` because the loader under test is the thing being
        # measured; the platform's newline policy is not.
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def test_the_fleet_block_loads_and_grants_what_it_claims(self):
        path = self._write("fleet-mcp.local.yaml", fill(blocks()[0]))
        config = load_config(path)

        self.assertEqual(
            sorted(config.policy.operator_grants),
            ["oauth:client-id-for-this-test", "owner-pat"],
            "the template documents one OAuth principal and the PAT; the parser "
            "is the authority on whether it was read",
        )
        for principal in ("oauth:client-id-for-this-test", "owner-pat"):
            with self.subTest(principal=principal):
                self.assertEqual(
                    sorted(config.policy.operator_grants[principal]),
                    ["device:company-a", "device:company-b", "device:company-c"],
                )
                self.assertEqual(
                    config.policy.operator_grants[principal]["device:company-c"],
                    ("send",),
                    "`send` is the whole vocabulary this axis accepts; a "
                    "template that wrote `control` here would be refused at "
                    "load, and this is where that would show",
                )

        for ref in ("device:company-a", "device:company-b", "device:company-c"):
            with self.subTest(device=ref):
                binding = config.provider_for(ref)
                self.assertIsNotNone(binding)
                self.assertTrue(binding.allow_send)
                self.assertTrue(binding.allow_create)
                self.assertTrue(binding.allow_control)

        self.assertTrue(config.policy.allow_wildcard_control)
        self.assertEqual(tuple(config.policy.control_allow), ("*",))
        self.assertFalse(
            config.policy.allow_wildcard_send,
            "the template replaces the send wildcard with a grant table; "
            "leaving both on would be the file that reads as narrower and is "
            "not",
        )
        self.assertEqual(
            sorted(config.policy.create_allow),
            ["project-alpha", "project-beta"],
        )
        for scope in (SCOPE_CREATE, SCOPE_CONTROL):
            with self.subTest(scope=scope):
                self.assertIn(scope, config.auth.grantable_scopes)
        self.assertIn("fleet:read", config.auth.grantable_scopes)
        self.assertIn("fleet:send", config.auth.grantable_scopes)

    def test_the_node_block_loads_and_opens_exactly_the_three_grants(self):
        path = self._write("fleet-node.local.yaml", fill(blocks()[1]))
        config = load_node_config(path)

        self.assertEqual(config.device_ref, "device:company-a")
        self.assertEqual(
            config.allow_all_sessions_for_owner, ("send", "control"),
            "the owner floor is where 'no per-session approval' is written; if "
            "the template stopped naming `control` here, interrupt would still "
            "be reachable per session and not device-wide, which is the "
            "difference the template exists to make expressible",
        )
        self.assertTrue(config.allow_create)
        self.assertEqual(
            config.allow_create_projects, ("project-alpha", "project-beta"),
        )
        self.assertTrue(config.allow_control)
        self.assertEqual(config.allow_control_to, ())
        self.assertEqual(config.allow_send_to, ())

    def test_the_three_controls_the_commentary_promises_are_real(self):
        """Three negative controls, each one a refusal the prose claims.

        They are here because the two tests above are assertions about a
        *result*: a loader that accepted everything and ignored what it did not
        recognise would satisfy some of them by accident. Each mutation below
        edits the documented block in the one place the corresponding sentence
        says the loader is strict, and each has to be refused.

        The second one is not hypothetical. It is the mistake this document made
        in its first draft — `allow_models` where the parser reads `models` —
        and the doc's own block would have been a binding that refused to load.
        """
        fleet_text = fill(blocks()[0])
        node_text = fill(blocks()[1])

        cases = (
            # `control` is in PLANNED_OPERATOR_GRANT_VERBS: a binding that names
            # it is a binding whose author believes a control verb is live.
            ("a planned verb in operator_grants", load_config, fleet_text,
             fleet_text.replace("[send]", "[send, control]", 1), "plan"),
            # `create_allow` has exactly two value keys, and an unknown one is
            # refused rather than ignored.
            ("an invented key inside create_allow", load_config, fleet_text,
             fleet_text.replace("      models: []", "      allow_models: []", 1),
             "unknown key"),
            # The owner floor's vocabulary is closed, so a word that reads as
            # "all of them" is a refusal at load rather than the widest grant
            # the file can express.
            ("a floor word outside the vocabulary", load_node_config, node_text,
             node_text.replace("[send, control]", "[send, all]", 1), "control"),
        )

        for index, (label, loader, original, mutated, expected) in enumerate(cases):
            with self.subTest(mutation=label):
                self.assertNotEqual(
                    mutated, original,
                    "the mutation did not apply, so this case would 'pass' by "
                    "loading the unmutated block and not raising at all",
                )
                path = self._write(f"mutation-{index}.yaml", mutated)
                with self.assertRaises(Exception) as caught:
                    loader(path)
                self.assertIn(
                    expected, str(caught.exception),
                    f"refused for a different reason than the one promised: "
                    f"{caught.exception}",
                )


if __name__ == "__main__":
    unittest.main()
