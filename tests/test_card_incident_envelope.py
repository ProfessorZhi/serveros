#!/usr/bin/env python3
"""`serverosctl card alert --with-frozen`: which incident the card is about.

WHY THIS FILE EXISTS
--------------------
A sender has to answer one question before it sends: «may I change the message
that already carries this, or is this the second card for one incident». It
cannot answer it from the card — the card is a rendered face — and it must not
answer it by re-deriving the incident locally, because that would be a second
reading of one name. So the envelope carries it: `incident`, naming the finding
the face leads with, in the same vocabulary the delivery ledger files receipts
under.

None of that was tested. `tests/test_cards.py` proves the renderer against a
model, `tests/test_card_delivery.py` proves the receipt, `tests/test_card_cli.py`
proves `card status` through its parser — and the envelope's third key was
exercised by hand once, in a shell, on 2026-09-26. A hand run is evidence about
one afternoon.

WHAT IS AND IS NOT STUBBED
--------------------------
Nothing. `--digest <file>` reads the document from disk instead of asking the
daemon, so this file touches no store, no socket and no host fact; everything
else — the parser, the model, the sort, the renderer, the freeze — is the real
code. The episode is checked against the documented formula computed here with
`hashlib`, rather than by calling `alert_digest.episode_of` and comparing it
with itself.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("serveros_runtime_cli", ROOT / "runtime" / "cli.py")
presentation = _load("serveros_runtime_presentation", ROOT / "runtime" / "presentation.py")

AT = "2026-09-26T14:00:00+08:00"


def episode_of_formula(row: dict) -> str:
    """The documented episode, expressed here rather than imported.

    `alert_digest.episode_of` computes `sha256("key|first_seen|resolved_at")[:16]`
    over `EPISODE_FIELDS`. Calling it from this file would make the assertion
    «the envelope agrees with the function» — true even if both were wrong about
    the fields. Written out, it is «the envelope agrees with the contract».
    """
    material = "|".join(str(row.get(field)) for field in
                        ("key", "first_seen", "resolved_at"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def row(key: str, *, code: str, severity: str, word: str, rank: int,
        subject: str, first_seen: str = "2026-09-26T13:00:00+08:00") -> dict:
    return {"key": key, "code": code, "severity": severity,
            "severity_word": word, "severity_rank": rank,
            "subject_ref": subject, "resource_ref": subject, "state": "OPEN",
            "first_seen": first_seen, "resolved_at": None, "occurrence_count": 1,
            "owner": None, "kind": "NEW", "was_announced": False,
            "backlog": False, "immediate": True,
            "provenance": {"freshness": "FRESH", "source": "observation"}}


#: One finding that may occupy a line (`declared` expectation → REVIEW) and one
#: that may only ever be counted (`consumption` → WATCH). The second is first in
#: `rows` on purpose: if the envelope ever reported the head of the ROW LIST
#: rather than the head of the FACE, this document would say so.
REVIEWED = row("alert:reviewed0000001", code="DESIRED_ACTUAL_MISMATCH",
               severity="high", word="CRITICAL", rank=3,
               subject="runtime:REVIEWED-ONE")
WATCHED = row("alert:watched00000001", code="HIGH_MEMORY_SUSTAINED",
              severity="high", word="CRITICAL", rank=3,
              subject="runtime:WATCHED-TWO")


def digest(rows: list[dict], *, generation: int = 12) -> dict:
    keys = [r["key"] for r in rows]
    return {"schema": "serveros/alert-digest.v1", "at": AT,
            "baseline_at": "2026-09-25T00:00:00+08:00", "generation": generation,
            "kinds": ["NEW", "ESCALATED", "RECOVERED", "REOPENED"],
            "counts": {"NEW": len(rows)}, "severity_counts": {},
            "rows": rows, "held": [], "shown": rows, "shown_keys": keys,
            "more": 0, "immediate": keys, "standing": [], "backlog": 0,
            "silent_resolutions": 0}


class CardIncidentEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-incident-test-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir,
                                                            ignore_errors=True))

    def card(self, document: dict, *argv) -> tuple[int, dict, str]:
        """One `card alert` run: exit code, parsed stdout, the human lines."""
        path = self.dir / "digest.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(["card", "alert", "--json", *argv,
                             "--digest", str(path), "--at", AT])
        text = out.getvalue()
        self.assertEqual(code, 0, text)
        # `--json` on this command prints the payload alone; the human lines ride
        # along only when the payload is not wanted as JSON.
        return code, json.loads(text), text

    @staticmethod
    def face_text(card: dict) -> str:
        return "\n".join(e.get("content", "") for e in card["body"]["elements"]
                         if isinstance(e.get("content"), str))

    # ---------------------------------------------------------------- §29 信封

    def test_the_envelope_names_the_incident_and_only_that(self):
        """Three keys, and the incident is the finding the face leads with."""
        _code, data, _text = self.card(digest([WATCHED, REVIEWED]), "--with-frozen")
        self.assertEqual(sorted(data), ["card", "frozen", "incident"])
        incident = data["incident"]
        self.assertEqual(incident["ref"], REVIEWED["key"])
        self.assertEqual(incident["episode"], episode_of_formula(REVIEWED))
        self.assertEqual(incident["code"], REVIEWED["code"])
        self.assertEqual(incident["event"], "NEW")

    def test_the_face_and_the_envelope_name_the_same_finding(self):
        """The receipt's incident is the card's FIRST line, not merely a row."""
        _code, data, _text = self.card(digest([WATCHED, REVIEWED]), "--with-frozen")
        text = self.face_text(data["card"])
        self.assertLess(text.index("REVIEWED-ONE"), len(text))
        self.assertNotIn("WATCHED-TWO", text)          # counted, never printed
        self.assertEqual(data["incident"]["events_on_card"], 1)

    def test_every_event_on_the_face_is_counted_in_the_envelope(self):
        """A card carries up to three, and a receipt naming one of two would read
        as if the message were about that incident alone."""
        other = row("alert:reviewed0000002", code="DESIRED_ACTUAL_MISMATCH",
                    severity="medium", word="WARNING", rank=2,
                    subject="runtime:REVIEWED-THREE")
        _code, data, _text = self.card(digest([REVIEWED, other]), "--with-frozen")
        self.assertEqual(data["incident"]["events_on_card"], 2)
        self.assertEqual(data["incident"]["ref"], REVIEWED["key"])

    def test_a_card_about_no_single_incident_carries_no_incident(self):
        """Absent rather than invented: a made-up episode would make a sender
        patch another incident's message."""
        _code, data, _text = self.card(digest([]), "--with-frozen")
        self.assertEqual(sorted(data), ["card", "frozen"])
        self.assertNotIn("incident", data)

    def test_the_frozen_version_is_the_card_that_was_rendered(self):
        """The two halves of the envelope are one payload — the hash is what a
        sender's receipt is checked against later."""
        _code, data, _text = self.card(digest([REVIEWED]), "--with-frozen")
        self.assertEqual(data["frozen"]["kind"], "alert")
        self.assertEqual(data["frozen"]["at"], AT)
        canonical = json.dumps(data["card"], ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        self.assertEqual(data["frozen"]["payload_hash"],
                         hashlib.sha256(canonical).hexdigest())
        self.assertEqual(data["frozen"]["version"],
                         "alert@" + data["frozen"]["payload_hash"][:12])

    def test_the_envelope_is_opt_in(self):
        """Without `--with-frozen` the JSON is still the card itself: every
        consumer that already renders one would otherwise have to learn the
        envelope before it could do anything."""
        _code, data, _text = self.card(digest([REVIEWED]))
        self.assertEqual(data["schema"], "2.0")
        self.assertNotIn("frozen", data)

    # ------------------------------------------------------ §29 事件自己带的话

    def test_the_event_carries_the_episode_the_ledger_files_under(self):
        """`presentation._event_of` and `alert_digest.episode_of` must be one
        name, not two: the envelope reads the event because the ledger keys on
        the same token."""
        model = presentation.alert_model(digest([REVIEWED]), at=AT)
        self.assertEqual([e["ref"] for e in model["events"]], [REVIEWED["key"]])
        self.assertEqual(model["events"][0]["episode"],
                         episode_of_formula(REVIEWED))

    def test_two_episodes_of_one_finding_are_two_tokens(self):
        """An episode is the finding's span, not its code: resolved and reopened
        is a different incident, and the ledger must be able to tell."""
        reopened = dict(REVIEWED, key=REVIEWED["key"],
                        first_seen="2026-09-27T09:00:00+08:00")
        self.assertNotEqual(episode_of_formula(REVIEWED),
                            episode_of_formula(reopened))
        model = presentation.alert_model(digest([reopened]), at=AT)
        self.assertEqual(model["events"][0]["episode"],
                         episode_of_formula(reopened))


if __name__ == "__main__":
    unittest.main()
