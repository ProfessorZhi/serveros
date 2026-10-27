"""CI parity contract for the #417 milestone announcer (#418, lane C).

WHY THIS FILE EXISTS
--------------------
`tests/test_fleet_v1_milestone_notify.py` is #417's implementation contract.
The sharded suite globs every `tests/test_*.py`, and `scripts/hermetic_tests.py`
already turns a *collection error* into a red run — a file whose body cannot
import is reported as a failing file, not as "no tests here".

What none of that can see is the file **being absent from the tree entirely**.
`shard_files()` enumerates the directory; a missing file is simply fewer tests
scheduled, and the run stays green. That is exactly the failure the owner named:
a capability implemented on one machine that CI cannot test and another device
cannot find.

So this contract fails while the source is absent, and when it is present it
checks the four things "it is tested" would otherwise be assumed to mean:

* it exists, both the implementation and its test,
* the sharded suite actually schedules it (not merely that the name looks
  right), and it has not been parked in `hermetic-known-failures.txt`,
* it is not empty and still covers every behaviour #417 promised,
* it stays offline and portable — a stand-in provider, no network import, no
  POSIX-only import that would turn Windows collection into an error.

This is deliberately a **presence** contract. It does not re-assert the
behaviour; that would be a second, weaker copy of #417's tests. The proof that
a success is sent once, that yellow is silent and that red falls back is those
tests running in the same suite. This file only refuses to let them be absent,
unlisted, gutted, or unrunnable elsewhere.

MERGE ORDER — READ THIS BEFORE MAKING IT GREEN
----------------------------------------------
Until #417 lands, this file is RED on purpose. Do **not** skip it, and do not
add it to `tests/hermetic-known-failures.txt`: a skipped or baselined gate is
the same silence this file exists to break. Merge #417 first, or in the same
wave; this goes green on its own and needs no edit.

No network, no provider call, no credential: this file only reads the tree.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MILESTONE_DIR = ROOT / "integrations" / "fleet-v1-milestone-notify"
MILESTONE_MODULE = MILESTONE_DIR / "milestone_notify.py"
MILESTONE_TEST = ROOT / "tests" / "test_fleet_v1_milestone_notify.py"
BASELINE = ROOT / "tests" / "hermetic-known-failures.txt"
HERMETIC = ROOT / "scripts" / "hermetic_tests.py"
HARNESS_SHARDS = ("1/4", "2/4", "3/4", "4/4")

#: The floor that separates "a test file" from "a token test file". #417 ships
#: more than this; the floor exists so a rewrite that drops most of the matrix
#: fails the gate instead of quietly shrinking it.
MINIMUM_TESTS = 18

#: Modules that would let a test reach the network or the real provider. The
#: promise is a fully mocked, deterministic offline run; an import is how that
#: promise is broken quietly.
NETWORK_MODULES = frozenset({
    "socket", "ssl", "http", "urllib", "urllib3", "requests", "httpx",
    "aiohttp", "websocket", "websockets", "lark_oapi", "ftplib", "smtplib",
    "telnetlib", "xmlrpc",
})

#: Modules that exist only where POSIX does. Importing one at module scope turns
#: Windows collection into an ERROR, which the baseline gate reports as a new
#: failure — the opposite of the explicit skip #418 asks for.
POSIX_ONLY_MODULES = frozenset({
    "fcntl", "termios", "tty", "pwd", "grp", "resource", "posix",
})

#: What #417 promised, as tokens that must all appear in the milestone test
#: module. Deliberately concept-level rather than exact test names, so a rename
#: is free and a removal is not.
REQUIRED_BEHAVIOURS = {
    "an important success is delivered exactly once": ("deliver", "once"),
    "ordinary green and yellow stay silent on the card": ("yellow", "card"),
    "red sends a deterministic template when the summarizer fails":
        ("deterministic",),
    "an unverified completion is UNKNOWN, never success": ("unknown",),
    "a duplicate observation or webhook does not resend":
        ("duplicate", "idempot"),
    "a restart replays without a second send": ("restart", "replay"),
    "the provider receipt decides DELIVERED": ("receipt",),
    "no prompt, transcript or token reaches the text or the ledger":
        ("secret", "prompt"),
}

FAKE_PROVIDER = re.compile(r"(?i)^class\s+\w*(?:fake|stub|mock|double)\w*\b", re.M)

# -- the release acceptance record ----------------------------------------

ACCEPTANCE_SCHEMA = "serveros/fleet-release-acceptance.v1"
ACCEPTANCE_LEVELS = ("CODE", "HOSTED_CI", "REAL_DEVICE", "CLIENT_E2E",
                     "PRODUCTION")
#: A claim level is one of these. `NOT_OBSERVED` ("nobody looked") and
#: `NOT_INSTALLED` are not interchangeable with `FAIL`; `NOT_TOUCHED` exists so
#: production can be recorded without implying it was exercised.
ACCEPTANCE_VALUES = frozenset({
    "PASS", "FAIL", "NOT_RUN", "NOT_OBSERVED", "BLOCKED", "NOT_TOUCHED",
})
ARTIFACT_KINDS = frozenset({
    "node_runtime", "device_ability_plugin", "node_connector", "fleet_hub",
    "commander_skills", "milestone_announcer",
})
REQUIRED_RECORD_KEYS = (
    "artifact_kind", "device_ref", "os_principal", "observed_at", "source_sha",
    "version", "digest", "installed_path", "previous_digest", "rollback",
    "compatible_cloudcli", "identities", "acceptance",
)
IDENTITY_KEYS = ("run_id", "delivery_id", "boot_id")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|_)(token|secret|password|passwd|pat|bearer|authorization|"
    r"credential|client_secret)(?:$|_)")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _imports(path: Path) -> set[str]:
    """Every module the file imports, at any depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _test_count(path: Path) -> int:
    return sum(1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name.startswith("test_"))


def _secret_keys(value, trail: str = "") -> list[str]:
    """Every key anywhere in the document that looks like a credential."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            here = f"{trail}.{key}" if trail else str(key)
            if SECRET_KEY_RE.search(str(key)):
                found.append(here)
            found.extend(_secret_keys(inner, here))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found.extend(_secret_keys(inner, f"{trail}[{index}]"))
    return found


def validate_record(record: dict) -> list[str]:
    """Return every way `record` breaks the release acceptance contract."""
    problems: list[str] = []
    missing = [key for key in REQUIRED_RECORD_KEYS if key not in record]
    if missing:
        problems.append(f"missing keys: {missing}")
    extra = sorted(set(record) - set(REQUIRED_RECORD_KEYS))
    if extra:
        problems.append(f"unexpected keys: {extra}")

    kind = record.get("artifact_kind")
    if kind is not None and kind not in ARTIFACT_KINDS:
        problems.append(f"artifact_kind {kind!r} is not one of {sorted(ARTIFACT_KINDS)}")

    sha = record.get("source_sha")
    if sha is not None and not SHA_RE.match(str(sha)):
        problems.append(f"source_sha {sha!r} is not a 40-hex revision")

    digest = record.get("digest")
    if digest is not None and digest != "NOT_OBSERVED" and not DIGEST_RE.match(str(digest)):
        problems.append("digest must be sha256:<64 hex> or NOT_OBSERVED")

    # A missing installation and an unread one are different claims, and a
    # record that conflates them cannot be rolled back from.
    installed = record.get("installed_path")
    if installed is not None and not isinstance(installed, str):
        problems.append("installed_path must be a path string or NOT_INSTALLED")

    acceptance = record.get("acceptance")
    if isinstance(acceptance, dict):
        if set(acceptance) != set(ACCEPTANCE_LEVELS):
            problems.append(
                f"acceptance must name exactly {list(ACCEPTANCE_LEVELS)}, "
                f"got {sorted(acceptance)}")
        bad = {k: v for k, v in acceptance.items() if v not in ACCEPTANCE_VALUES}
        if bad:
            problems.append(f"acceptance values outside the vocabulary: {bad}")
    elif acceptance is not None:
        problems.append("acceptance must be an object")

    identities = record.get("identities")
    if isinstance(identities, dict):
        if set(identities) != set(IDENTITY_KEYS):
            problems.append(f"identities must name exactly {list(IDENTITY_KEYS)}")
        # runId, deliveryId and bootId are separately scoped. Recording one as
        # another is how "the run finished" becomes "the owner was told".
        seen = {k: v for k, v in identities.items() if isinstance(v, str) and v}
        for left in IDENTITY_KEYS:
            for right in IDENTITY_KEYS:
                if left < right and seen.get(left) and seen.get(left) == seen.get(right):
                    problems.append(
                        f"identities.{left} and .{right} carry the same value "
                        f"{seen[left]!r}; they are separately scoped")
    elif identities is not None:
        problems.append("identities must be an object")

    problems.extend(f"credential-shaped key: {key}" for key in _secret_keys(record))
    return problems


def validate_document(doc: dict) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != ACCEPTANCE_SCHEMA:
        problems.append(f"schema must be {ACCEPTANCE_SCHEMA!r}")
    records = doc.get("records")
    if not isinstance(records, list) or not records:
        return problems + ["records must be a non-empty list"]
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            problems.append(f"records[{index}] is not an object")
            continue
        problems.extend(f"records[{index}]: {p}" for p in validate_record(record))
    return problems


def example() -> dict:
    path = (ROOT / "tests" / "fixtures" / "fleet_release_acceptance"
            / "component-record.example.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TheAnnouncerSourceIsPresent(unittest.TestCase):
    """The gate that is red until #417 lands. See the merge-order note above."""

    def test_the_implementation_and_its_test_are_both_in_the_tree(self) -> None:
        missing = [str(p.relative_to(ROOT)) for p in
                   (MILESTONE_MODULE, MILESTONE_TEST) if not p.is_file()]
        self.assertEqual(
            missing, [],
            "BLOCKED: the #417 milestone announcer is not merged, so CI has "
            "nothing to test and no device can install it. Merge #417 (or land "
            "it in the same wave) — do not skip this test and do not add it to "
            "tests/hermetic-known-failures.txt.")

    def test_the_implementation_is_a_module_not_a_directory_of_scripts(self) -> None:
        if not MILESTONE_MODULE.is_file():
            self.skipTest("covered by the present-or-red test above")
        self.assertTrue(MILESTONE_DIR.is_dir())
        stray = sorted(p.name for p in MILESTONE_DIR.rglob("*.py")
                       if p.resolve() != MILESTONE_MODULE.resolve())
        self.assertEqual(stray, [],
                         "the announcer is one module; a second .py beside it is "
                         "a second implementation to keep in step")


class TheSuiteActuallySchedulesIt(unittest.TestCase):
    """Present is not scheduled. These check the harness, not the filename."""

    def setUp(self) -> None:
        if not MILESTONE_TEST.is_file():
            self.skipTest("covered by the present-or-red test above")
        if not HERMETIC.is_file():
            self.skipTest("scripts/hermetic_tests.py is not in this tree")
        self.harness = _load("fleet_contract_hermetic", HERMETIC)

    def test_the_milestone_test_is_scheduled_by_exactly_one_shard(self) -> None:
        rel = "tests/test_fleet_v1_milestone_notify.py"
        scheduled = {spec: sum(1 for f in self.harness.shard_files(spec)
                               if Path(f).as_posix() == rel)
                     for spec in HARNESS_SHARDS}
        self.assertEqual(sum(scheduled.values()), 1,
                         f"{rel} must be collected by one shard, saw {scheduled}")

    def test_the_shards_partition_the_suite_without_loss(self) -> None:
        """A file no shard schedules is a test CI never runs."""
        seen: dict[str, int] = {}
        for spec in HARNESS_SHARDS:
            for name in self.harness.shard_files(spec):
                seen[name] = seen.get(name, 0) + 1
        self.assertEqual(sorted(n for n in seen if seen[n] > 1), [],
                         "a file scheduled by two shards runs twice")
        on_disk = sorted(p.relative_to(ROOT).as_posix()
                         for p in (ROOT / "tests").glob("test_*.py"))
        self.assertEqual(sorted(seen), on_disk,
                         "the shards must schedule every tests/test_*.py")

    def test_it_has_not_been_parked_in_the_known_failures_baseline(self) -> None:
        self.assertTrue(BASELINE.is_file(), f"{BASELINE} is missing")
        listed = [line.strip() for line in
                  BASELINE.read_text(encoding="utf-8").splitlines()
                  if line.strip() and not line.strip().startswith("#")]
        parked = [line for line in listed
                  if "test_fleet_v1_milestone_notify" in line]
        self.assertEqual(parked, [],
                         "the milestone announcer may not be baselined; a "
                         "baselined gate is the silence this contract breaks")


class TheContractIsNotEmpty(unittest.TestCase):
    def setUp(self) -> None:
        if not MILESTONE_TEST.is_file():
            self.skipTest("covered by the present-or-red test above")
        self.text = MILESTONE_TEST.read_text(encoding="utf-8")
        self.lowered = self.text.lower()

    def test_it_defines_a_real_test_matrix(self) -> None:
        self.assertGreaterEqual(
            _test_count(MILESTONE_TEST), MINIMUM_TESTS,
            "the announcer test file is a token file; a green run on it would "
            "not mean the matrix was exercised")

    def test_every_promised_behaviour_is_still_covered(self) -> None:
        gaps = {name: tokens for name, tokens in REQUIRED_BEHAVIOURS.items()
                if not all(token in self.lowered for token in tokens)}
        self.assertEqual(gaps, {},
                         "behaviours #417 promised are no longer named in its "
                         "test module")


class TheTestsStayOfflineAndPortable(unittest.TestCase):
    def setUp(self) -> None:
        if not MILESTONE_TEST.is_file():
            self.skipTest("covered by the present-or-red test above")

    def test_neither_module_imports_a_network_client(self) -> None:
        offenders = {}
        for path in (MILESTONE_TEST, MILESTONE_MODULE):
            if not path.is_file():
                continue
            hit = sorted(_imports(path) & NETWORK_MODULES)
            if hit:
                offenders[path.name] = hit
        self.assertEqual(offenders, {},
                         "the milestone run is fully mocked and offline; a "
                         "network import is how that stops being true")

    def test_neither_module_imports_a_posix_only_module(self) -> None:
        offenders = {}
        for path in (MILESTONE_TEST, MILESTONE_MODULE):
            if not path.is_file():
                continue
            hit = sorted(_imports(path) & POSIX_ONLY_MODULES)
            if hit:
                offenders[path.name] = hit
        self.assertEqual(offenders, {},
                         "a POSIX-only import turns Windows collection into an "
                         "ERROR; #418 asks for an explicit skip instead")

    def test_a_stand_in_provider_replaces_the_real_one(self) -> None:
        self.assertRegex(
            MILESTONE_TEST.read_text(encoding="utf-8"), FAKE_PROVIDER,
            "the provider must be a stand-in class in the test, so no "
            "credential or socket is needed to exercise delivery")


class TheReleaseAcceptanceRecord(unittest.TestCase):
    """The checklist contract #418 asks every artifact to report against."""

    def test_the_shipped_example_satisfies_the_contract(self) -> None:
        self.assertEqual(validate_document(example()), [])

    def test_the_example_covers_both_a_known_and_an_unobserved_artifact(self) -> None:
        records = example()["records"]
        digests = {record["digest"] for record in records}
        installed = {record["installed_path"] for record in records}
        self.assertIn("NOT_OBSERVED", digests,
                      "an artifact nobody read must be recordable without "
                      "inventing a version")
        self.assertIn("NOT_INSTALLED", installed,
                      "source in the repository is not an installation")

    def test_a_record_without_a_source_revision_is_refused(self) -> None:
        record = dict(example()["records"][0])
        record.pop("source_sha")
        self.assertIn("missing keys: ['source_sha']", validate_record(record))

    def test_a_claim_level_outside_the_vocabulary_is_refused(self) -> None:
        record = json.loads(json.dumps(example()["records"][0]))
        record["acceptance"]["REAL_DEVICE"] = "GREEN"
        self.assertTrue(any("vocabulary" in p for p in validate_record(record)),
                        "a made-up claim level must not validate")

    def test_a_missing_claim_level_is_refused(self) -> None:
        record = json.loads(json.dumps(example()["records"][0]))
        del record["acceptance"]["CLIENT_E2E"]
        self.assertTrue(any("acceptance must name exactly" in p
                            for p in validate_record(record)))

    def test_conflated_run_delivery_and_boot_identities_are_refused(self) -> None:
        record = json.loads(json.dumps(example()["records"][0]))
        record["identities"]["delivery_id"] = record["identities"]["run_id"]
        self.assertTrue(any("separately scoped" in p for p in validate_record(record)),
                        "runId and deliveryId are different facts; recording one "
                        "as the other is how an ACK becomes a delivery")

    def test_a_credential_shaped_field_is_refused(self) -> None:
        record = json.loads(json.dumps(example()["records"][0]))
        record["provider_token"] = "redacted"
        self.assertTrue(any("credential-shaped key" in p
                            for p in validate_record(record)))

    def test_a_document_with_no_records_is_refused(self) -> None:
        self.assertIn("records must be a non-empty list",
                      validate_document({"schema": ACCEPTANCE_SCHEMA,
                                         "records": []}))


if __name__ == "__main__":
    unittest.main()
