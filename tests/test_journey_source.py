#!/usr/bin/env python3
"""§B1 — a source revision must come from somewhere; a string is not a revision.

THE DEFECT THIS FILE WAS WRITTEN AGAINST
----------------------------------------
`journey.capture()` took `revision` and `trust` straight off the caller's JSON and
recorded them. Measured before the fix:

    journey.capture(journey.empty_store(), [{
        "source_id": "feishu:chat:oc_x", "message_id": "om_1",
        "revision": "totally-made-up-by-the-caller",
        "captured_at": "2026-10-16T10:00:00+09:00", "trust": "i-say-so"}])
    -> outcomes[0]["state"] == "NEW", stored revision == "totally-made-up-by-the-caller"

It is the same defect `authorize()` had, one stage earlier: a value that later
becomes load-bearing (it is what a signed human approval must cover) was accepted
from the party it is supposed to constrain.

WHERE THE REAL ANSWER ALREADY LIVES
-----------------------------------
`intake/source_reading.py#read_packet` — "Read captured source bytes at their recorded
digest; never fetch or write. **Neither a timestamp nor a filename is a revision.**"
It answers "what revision is this source at" with `content_identity`: a sha256 over the
bytes actually read at the recorded digest, checked against the manifest, refusing when
they disagree. That module is not re-implemented here; it is bound in.

THE RULE THIS FILE PINS
-----------------------
    an observation that declares a manifest, with no reader bound  -> REFUSED
    a caller `revision` that disagrees with the bytes read          -> REFUSED
    a manifest the reader cannot honour                             -> REFUSED
    an observation with no manifest and no reader                   -> recorded, UNVERIFIED
    a verified observation                                          -> the stored
                                                                       revision IS the
                                                                       reader's identity

The fourth line is deliberate and is not a loophole to be closed: capture alone mints
no work, and making every observation require a manifest would make `CAPTURED_ONLY`
unreachable. What must never happen is the first three, and what must always be visible
is which of the last two a receipt came from.

NOT MOCKED
----------
The positive case runs the REAL `read_packet` against a REAL manifest + `content.md` on
disk, under the caller's own records root. Nothing about it is stubbed.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


journey = _load("serveros_runtime_journey", REPO / "runtime" / "journey.py")
source_reading = _load("serveros_intake_source_reading",
                       REPO / "intake" / "source_reading.py")

CHAT = "feishu:chat:oc_sample"
MESSAGE = "om_sample_0001"
AT = "2026-10-16T10:00:00+09:00"
BODY = b"# synthetic feishu doc\n\nline two\n"
MANIFEST = "sample/manifest.json"


def records_root(tmp_path: Path, *, body: bytes = BODY, manifest_over: dict | None = None):
    """A records root with a real captured document, at its real digest."""
    raw = tmp_path / "records" / "information" / "raw" / "sample"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "content.md").write_bytes(body)
    document = {"url": "https://example.invalid/doc",
                "content_sha256": hashlib.sha256(body).hexdigest()}
    document.update(manifest_over or {})
    (raw / "manifest.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path / "records"


def observation(**over):
    spec = {"source_id": CHAT, "message_id": MESSAGE, "revision": "caller-asserted-rev",
            "captured_at": AT, "trust": "authorized-follow"}
    spec.update(over)
    return spec


def verified_observation(root, **over):
    """An observation whose `revision` is the digest of the bytes on disk."""
    identity = source_reading.read_packet(MANIFEST, root)["content_identity"]
    return observation(revision=identity, manifest=MANIFEST, **over)


def provider(root, **over):
    return journey.source_reading_provider(root, **over)


# ---------------------------------------------------------------------------
# 负控 —— 这一组是这份文件的用途
# ---------------------------------------------------------------------------

class TestASourceRevisionIsNotAString:
    def test_a_manifest_with_no_reader_bound_is_refused(self):
        """Declaring a manifest is a claim to have read real bytes. With nobody to
        read them, the observation must not be recorded as if somebody had."""
        with pytest.raises(journey.Refused) as caught:
            journey.capture(journey.empty_store(),
                            [observation(manifest=MANIFEST)], authority=None)
        assert "manifest" in str(caught.value)

    def test_a_caller_revision_that_disagrees_with_the_bytes_is_refused(self, tmp_path):
        """The defect, as a regression: the caller names a revision, the reader
        reads different bytes, and the disagreement — not the caller — wins."""
        root = records_root(tmp_path)
        with pytest.raises(journey.Refused) as caught:
            journey.capture(journey.empty_store(),
                            [observation(manifest=MANIFEST,
                                         revision="totally-made-up-by-the-caller")],
                            authority=provider(root))
        assert "content_identity" in str(caught.value)

    def test_a_manifest_whose_body_moved_is_refused(self, tmp_path):
        """The reader's own guard, reached through the provider: the bytes on disk
        no longer hash to what the manifest records."""
        root = records_root(tmp_path)
        identity = source_reading.read_packet(MANIFEST, root)["content_identity"]
        (tmp_path / "records" / "information" / "raw" / "sample" / "content.md"
         ).write_bytes(b"the body was replaced after the manifest was written\n")
        with pytest.raises(journey.Refused) as caught:
            journey.capture(journey.empty_store(),
                            [observation(manifest=MANIFEST, revision=identity)],
                            authority=provider(root))
        assert "manifest" in str(caught.value) or "digest" in str(caught.value)

    def test_a_manifest_that_is_not_there_is_refused(self, tmp_path):
        root = records_root(tmp_path)
        with pytest.raises(journey.Refused):
            journey.capture(journey.empty_store(),
                            [observation(manifest="nobody/manifest.json",
                                         revision="0" * 64)],
                            authority=provider(root))

    def test_a_reader_that_raises_authorizes_nothing(self, tmp_path):
        """A reader that raised is a check that did not happen."""
        def broken(_observation):
            raise RuntimeError("the records root went away")
        with pytest.raises(journey.Refused) as caught:
            journey.capture(journey.empty_store(),
                            [observation(manifest=MANIFEST)], authority=broken)
        assert "这次检查没有发生" in str(caught.value)

    def test_a_reader_that_cannot_find_the_source_is_refused(self, tmp_path):
        def nothing(_observation):
            return {"found": False, "because": "no such manifest"}
        with pytest.raises(journey.Refused) as caught:
            journey.capture(journey.empty_store(),
                            [observation(manifest=MANIFEST)], authority=nothing)
        assert "no such manifest" in str(caught.value)

    def test_the_observation_cannot_choose_its_own_asset_root(self):
        """`read_packet`'s own rule: external asset roots come from the trusted
        caller, never the manifest. The provider must therefore take them at
        construction — an observation that could name one would be choosing where
        its own evidence is read from."""
        import inspect
        parameters = inspect.signature(journey.source_reading_provider).parameters
        assert "records_root" in parameters
        assert "allowed_asset_roots" in parameters

    def test_walk_refuses_a_declared_manifest_with_no_authority(self, tmp_path):
        with pytest.raises(journey.Refused) as caught:
            journey.walk({"observations": [observation(manifest=MANIFEST)]},
                         source_authority=None)
        assert "manifest" in str(caught.value)


# ---------------------------------------------------------------------------
# 正控 —— 真实 read_packet，真实字节
# ---------------------------------------------------------------------------

class TestTheVerifiedPath:
    def test_a_real_packet_verifies_the_callers_revision(self, tmp_path):
        root = records_root(tmp_path)
        got = journey.capture(journey.empty_store(), [verified_observation(root)],
                              authority=provider(root))
        assert got["outcomes"][0]["state"] == "NEW"
        message = got["store"]["messages"][f"{CHAT}|{MESSAGE}"]
        assert message["verified"] is True

    def test_the_stored_revision_is_the_readers_not_the_callers(self, tmp_path):
        """The caller and the reader agree here — that is what the refusal above
        enforces — but the value that is STORED is the reader's identity, so the
        property holds by construction rather than by the caller's honesty."""
        root = records_root(tmp_path)
        identity = source_reading.read_packet(MANIFEST, root)["content_identity"]
        got = journey.capture(journey.empty_store(), [verified_observation(root)],
                              authority=provider(root))
        stored = got["store"]["messages"][f"{CHAT}|{MESSAGE}"]["revision"]
        assert stored == identity
        assert stored != "caller-asserted-rev"

    def test_the_receipt_says_which_of_the_two_it_was(self, tmp_path):
        root = records_root(tmp_path)
        verified = journey.capture(journey.empty_store(), [verified_observation(root)],
                                   authority=provider(root))["outcomes"][0]
        unverified = journey.capture(journey.empty_store(), [observation()],
                                     authority=None)["outcomes"][0]
        assert "核对" in verified["why"] and "自述" not in verified["why"]
        assert "自述" in unverified["why"]

    def test_an_observation_with_no_manifest_is_still_recorded_unverified(self):
        """Not a loophole: capture mints no work, and a receipt that says
        `unverified` is a different sentence from one that says `verified`."""
        got = journey.capture(journey.empty_store(), [observation()], authority=None)
        message = got["store"]["messages"][f"{CHAT}|{MESSAGE}"]
        assert message["verified"] is False
        assert "verified" not in message.get("provenance", {})

    def test_the_verified_revision_is_what_a_decision_has_to_cover(self, tmp_path):
        """The whole reason capture matters: `source_revision` — the thing a signed
        human approval must equal — is derived from whatever capture stored. Once
        that value is the reader's, an approval covering anything else cannot pass."""
        root = records_root(tmp_path)
        got = journey.capture(journey.empty_store(), [verified_observation(root)],
                              authority=provider(root))
        revision = journey.source_revision(got["store"], [f"{CHAT}|{MESSAGE}"])
        assert revision != "caller-asserted-rev"
        assert source_reading.read_packet(
            MANIFEST, root)["content_identity"] in got["store"]["messages"][
                f"{CHAT}|{MESSAGE}"]["revision"]
