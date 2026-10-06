import datetime
import hashlib
import json
import os

import pytest

from intake import alignment_delivery
from runtime import report_delivery

CARD = "information/understanding/people/zzj.md"
MANIFEST = "feishu/chats/people/zzj/oc_d/manifest.json"


def claim(ident, *, subject="额度异常", now="确实存在", kind="OBSERVED_FACT",
          conflicts=(), supersedes=(), positions=(171,), excerpt="原话",
          confirmation=None, confirmation_why=None):
    record = {"id": ident, "kind": kind, "subject": subject,
              "assertion": {"was": None, "now": now},
              "support": {"positions": list(positions), "excerpt": excerpt},
              "applies_to": None, "touches": [], "supersedes": list(supersedes),
              "conflicts": list(conflicts), "card": CARD, "views": []}
    if confirmation is not None:
        record["confirmation"] = confirmation
        record["confirmation_why"] = confirmation_why
    return record


def resolution(*, committed=(), awaiting=(), retracted=()):
    return {"schema": alignment_delivery.RESOLUTION_SCHEMA,
            "reading": {"card": CARD, "manifest": MANIFEST,
                        "from_position": 150, "to_position": 199},
            "read": {"card": CARD, "content_identity": "sha256:x",
                     "card_digest": "sha256:y"},
            "reading_digest": "sha256:z",
            "committed": list(committed), "awaiting": list(awaiting),
            "retracted": list(retracted), "delegation": None, "acceptance": {}}


def pending_row(directory, name, claims):
    return {"reading": str(directory / "readings" / name / "reading.json"),
            "source": {"card": CARD, "manifest": MANIFEST},
            "read": {"card": CARD}, "claims": claims,
            "state": "AWAITING_DECISION_REVALIDATE_BEFORE_ACCEPT"}


def build(tmp_path, *, readings, pending, run_record=None):
    """One run directory, with one `readings/<name>/` per entry in `readings`.

    `readings` maps a directory name to its resolution payload. A `None` payload
    writes only `reading.json`, which is the shape a reading that was produced
    and never resolved leaves behind.
    """
    directory = tmp_path / "daily-intake"
    for name, payload in readings.items():
        folder = directory / "readings" / name
        folder.mkdir(parents=True)
        (folder / "reading.json").write_text(
            json.dumps({"source": {"card": CARD, "manifest": MANIFEST}}))
        if payload is not None:
            (folder / "resolution.json").write_text(json.dumps(payload))
    # The pending list is derived from the readings that exist, so a test cannot
    # accidentally describe a pending claim whose reading was never written —
    # which is the mismatch `gather` refuses.
    rows = [pending_row(directory, name, claims) for name, claims in pending]
    (directory / "alignment.json").write_text(json.dumps(
        {"schema": alignment_delivery.ALIGNMENT_SCHEMA, "pending": rows}))
    return directory


AT = "2026-10-06T11:20:00+00:00"


def prepared_at(directory, when):
    """Give `alignment.json` the modification time a real 19:20 pass would.

    `compose` refuses a brief built from another day's preparation, and a
    fixture written a moment ago is not that day — so the stamp is stated here
    rather than inherited from whenever the test happened to run.
    """
    stamp = datetime.datetime.fromisoformat(when).timestamp()
    os.utime(directory / "alignment.json", (stamp, stamp))
    return directory


def test_the_four_sections_are_sorted_by_what_each_one_asks_of_a_person(tmp_path):
    # The same claim object in both records, because that is what the producer
    # writes: `daily_reading.alignment` copies the resolution's `awaiting` rows
    # into `alignment.json` verbatim. A fixture that used two different claims
    # here would be testing a join that never happens.
    waiting = claim("c2", subject="等人点头", conflicts=["c9"],
                    confirmation="awaiting_decision", confirmation_why="需要人")
    directory = build(tmp_path, readings={
        "a": resolution(committed=[claim("c1", subject="已提交的事实",
                                          confirmation="agent_reconciled",
                                          confirmation_why="授权覆盖")],
                         awaiting=[waiting]),
        "b": resolution(retracted=["c7"]),
    }, pending=[("a", [waiting])])
    brief = alignment_delivery.gather(directory)

    assert [row["subject"] for row in brief["auto"]] == ["已提交的事实"]
    assert [row["subject"] for row in brief["decision"]] == ["等人点头"]
    assert [row["id"] for row in brief["conflict"]] == ["c2"]
    assert brief["conflict"][0]["conflicts"] == ["c9"]
    assert brief["conflict"][0]["settled"] is False
    assert [row["id"] for row in brief["gap"]] == ["c7"]
    assert "撤回" in brief["gap"][0]["why"]
    assert brief["counts"] == {"auto": 1, "decision": 1, "conflict": 1, "gap": 1}


def test_a_claim_no_longer_pending_is_reported_as_settled_rather_than_dropped(tmp_path):
    """The join's one fall-through, and the reason it is labelled.

    A reading resolved this claim as `awaiting`, and `alignment.json` does not
    list it: a receipt settled it in between. The person is not being asked, so
    it belongs under 自动更新 — but silently omitting it leaves "I expected to be
    asked and was not" with nothing to point at.
    """
    directory = build(tmp_path, readings={
        "a": resolution(awaiting=[claim("c1", confirmation="awaiting_decision",
                                         confirmation_why="等人")]),
    }, pending=[])
    brief = alignment_delivery.gather(directory)

    assert brief["counts"]["decision"] == 0
    assert [row["id"] for row in brief["auto"]] == ["c1"]
    assert "alignment.json 已不再列它" in brief["auto"][0]["why"]


def test_two_records_that_are_not_about_the_same_run_are_refused(tmp_path):
    """The failure this guard exists for, stated as the wrong answer it prevents.

    With the keys mismatched, every pending claim falls through to "settled by a
    receipt". The brief would then report the same N under 自动更新 and under
    需要人决定, and the two numbers agreeing would look like corroboration.
    """
    directory = build(tmp_path, readings={
        "a": resolution(awaiting=[claim("c1")]),
    }, pending=[("a", [claim("c1")])])
    (directory / "alignment.json").write_text(json.dumps({
        "schema": alignment_delivery.ALIGNMENT_SCHEMA,
        "pending": [{"reading": str(tmp_path / "elsewhere" / "reading.json"),
                     "source": {"card": CARD, "manifest": MANIFEST},
                     "read": {"card": CARD}, "claims": [claim("c1")]}]}))

    with pytest.raises(alignment_delivery.Refused) as raised:
        alignment_delivery.gather(directory)

    assert "不是同一次运行" in str(raised.value)


def test_a_run_that_stopped_early_reports_a_gap_even_though_unread_is_empty(tmp_path):
    """Measured, not hypothetical: the 2026-10-06 19:20 run reads exactly this.

    `BUDGET_EXHAUSTED` with `outcomes.unread == []` — the ranges it never
    reached never failed, so nothing recorded them. A brief that read the empty
    list as "no gaps" would be reporting the coverage of a pass that gave up.
    """
    directory = build(tmp_path, readings={"a": resolution()}, pending=[])
    brief = alignment_delivery.gather(directory, run_record={
        "state": "STOPPED", "stopped_because": "BUDGET_EXHAUSTED",
        "stopped_why": "ceiling is 0", "outcomes": {"unread": []}})

    assert [row["id"] for row in brief["gap"]] == ["run:BUDGET_EXHAUSTED"]
    assert brief["brief"]["measured_gaps"] is True


@pytest.mark.parametrize("code", ["NO_CHANGE", "NEEDS_DECISION"])
def test_a_complete_stop_code_does_not_invent_a_gap(tmp_path, code):
    directory = build(tmp_path, readings={"a": resolution()}, pending=[])
    brief = alignment_delivery.gather(directory, run_record={
        "state": "STOPPED", "stopped_because": code, "stopped_why": "到了该停的地方",
        "outcomes": {"unread": []}})

    assert brief["gap"] == []


def test_an_unread_source_is_a_gap_and_an_unresolved_reading_is_one_too(tmp_path):
    directory = build(tmp_path, readings={
        "a": resolution(),
        "b": None,                  # read, never resolved
    }, pending=[])
    brief = alignment_delivery.gather(directory, run_record={
        "stopped_because": "SOURCE_UNAVAILABLE",
        "outcomes": {"unread": [{"id": "zzj-im", "why": "来源测不出来"}]}})

    reasons = {row["id"]: row["why"] for row in brief["gap"]}
    assert reasons["zzj-im"] == "来源测不出来"
    assert "b" in reasons
    assert "run:SOURCE_UNAVAILABLE" in reasons


def test_a_missing_run_record_says_not_measured_rather_than_none(tmp_path):
    directory = build(tmp_path, readings={"a": resolution()}, pending=[])
    brief = alignment_delivery.gather(directory)
    text = alignment_delivery.render(brief, at="2026-10-06T11:20:00+00:00")

    assert "没有量过" in text
    assert "本次没有量到未完成的来源" not in text


def test_a_missing_alignment_file_is_refused_rather_than_read_as_no_problems(tmp_path):
    directory = build(tmp_path, readings={"a": resolution()}, pending=[])
    (directory / "alignment.json").unlink()

    with pytest.raises(alignment_delivery.Refused) as raised:
        alignment_delivery.gather(directory)

    assert "没有 alignment.json" in str(raised.value)


def test_the_card_prints_every_section_even_when_it_is_empty(tmp_path):
    directory = build(tmp_path, readings={"a": resolution()}, pending=[])
    brief = alignment_delivery.gather(directory, run_record={
        "stopped_because": "NO_CHANGE", "outcomes": {"unread": []}})
    text = alignment_delivery.render(brief, at="2026-10-06T11:20:00+00:00")

    for heading in alignment_delivery.HEADINGS.values():
        assert f"## {heading}（0）" in text
    # Beijing, not UTC: 11:20Z is the 19:20 the reader is being told about.
    assert "# 对齐 · 2026-10-06 19:20" in text


def test_a_long_excerpt_is_cut_and_the_cut_is_marked(tmp_path):
    long_excerpt = "字" * 400
    waiting = claim("c1", excerpt=long_excerpt, confirmation="awaiting_decision",
                    confirmation_why="等人")
    directory = build(tmp_path, readings={"a": resolution(awaiting=[waiting])},
                      pending=[("a", [waiting])])
    text = alignment_delivery.render(alignment_delivery.gather(directory),
                                     at="2026-10-06T11:20:00+00:00")

    assert "…（已截断）" in text
    assert long_excerpt not in text


def test_compose_writes_a_version_statement_the_delivery_discipline_can_read(tmp_path):
    """The seam this module exists for, asserted across the two modules.

    `compose` names a version; `report_delivery.held_version` is the only reader
    that matters, because `request`/`deliver` are what actually carry the brief.
    A version statement this function could produce but `held_version` could not
    read would be a brief nothing can deliver.
    """
    directory = prepared_at(build(tmp_path, readings={"a": resolution()}, pending=[]), AT)
    out = tmp_path / "delivery"
    written = alignment_delivery.compose(directory, out, period="2026-10-06", at=AT)

    held = report_delivery.held_version(out)
    body = (out / alignment_delivery.BRIEF_FILE).read_bytes()
    assert held["sha256"] == hashlib.sha256(body).hexdigest() == written["sha256"]
    assert held["file"] == written["brief"]
    assert held["period"] == "2026-10-06"
    assert held["schema"] == alignment_delivery.VERSION_SCHEMA


def test_compose_refuses_a_relative_out_dir(tmp_path):
    directory = prepared_at(build(tmp_path, readings={"a": resolution()}, pending=[]), AT)

    with pytest.raises(alignment_delivery.Refused) as raised:
        alignment_delivery.compose(directory, "relative/delivery", at=AT)

    assert "不是绝对路径" in str(raised.value)


def test_a_rewritten_brief_is_not_the_version_the_intent_named(tmp_path):
    """Editing the brief after composing must break the delivery, not silently ride it."""
    directory = prepared_at(build(tmp_path, readings={"a": resolution()}, pending=[]), AT)
    out = tmp_path / "delivery"
    alignment_delivery.compose(directory, out, at=AT)
    (out / alignment_delivery.BRIEF_FILE).write_text("改过了\n")

    with pytest.raises(report_delivery.Refused) as raised:
        report_delivery.held_version(out)

    assert "alignment-brief.md" in str(raised.value) or "version.json" in str(raised.value)


def test_a_brief_from_another_day_is_refused(tmp_path):
    """The failure the card cannot show: a calm answer that is a day old.

    If the 19:20 pass did not run, `alignment.json` is yesterday's and the brief
    built from it looks exactly like a fresh one — and says the same restful
    thing. "Nothing needs you" would then be believed precisely on the evening
    something did.
    """
    directory = prepared_at(build(tmp_path, readings={"a": resolution()}, pending=[]),
                            "2026-10-05T11:20:00+00:00")

    with pytest.raises(alignment_delivery.Refused) as raised:
        alignment_delivery.compose(directory, tmp_path / "delivery", at=AT)

    assert "19:20 的准备没有在当天跑成" in str(raised.value)


def test_stating_the_day_a_backfill_belongs_to_lets_it_through(tmp_path):
    """The guard compares against the stamp it was given, not the wall clock."""
    directory = prepared_at(build(tmp_path, readings={"a": resolution()}, pending=[]),
                            "2026-10-05T11:20:00+00:00")

    written = alignment_delivery.compose(directory, tmp_path / "delivery",
                                         at="2026-10-05T11:20:00+00:00")

    assert written["counts"]["decision"] == 0
