import hashlib
import json
from pathlib import Path

import pytest
import yaml

from intake import daily_reading
from runtime import native_reader, recipes


def fixture(tmp_path):
    records = tmp_path / "records"
    card = "information/understanding/projects/example.md"
    manifest = "feishu/chats/people/x/oc_x/links/doc/manifest.json"
    raw = records / "information/raw" / manifest
    raw.parent.mkdir(parents=True)
    body = raw.with_name("content.md")
    body.write_text("The endpoint returned HTTP 200.\n")
    raw.write_text(json.dumps({"url": "https://example.test/doc", "type": "wiki",
                              "content_sha256": hashlib.sha256(body.read_bytes()).hexdigest()}))
    (records / card).parent.mkdir(parents=True)
    (records / card).write_text(f"# Example\n\n## Sources\n- `information/raw/{manifest}` sha deadbeef\n")
    sources = records / "projects/example/context/sources.yaml"
    sources.parent.mkdir(parents=True)
    sources.write_text(yaml.safe_dump({"sources": [{"id": "doc", "kind": "understanding", "card": card,
                                                    "manifest": manifest, "grant_ref": "fixture"}]}))
    return records, card


def model(transport, ask, ctx):
    return {"claims": [{"kind": "OBSERVED_FACT", "subject": "example",
                        "assertion": {"was": None, "now": "The endpoint returned HTTP 200."},
                        "support": {"positions": [], "start_line": 1, "end_line": 1},
                        "applies_to": None, "touches": [], "supersedes": [], "conflicts": []}]}


def run(tmp_path, records, call=model, day=6):
    engine = recipes.Engine(state_root=tmp_path / "state", records_root=records,
                            scope="project:example", period={"id": f"day-{day}", "start": f"2026-10-{day:02}T00:00:00Z",
                                                              "end": f"2026-10-{day:02}T23:59:00Z"},
                            transport="native-claude-reader", model=call, run_dir=tmp_path / "evidence")
    return engine.run(recipe_id="perception", budget=1)


def test_pending_reading_is_cached_and_never_turned_into_authority(tmp_path, monkeypatch):
    records, card = fixture(tmp_path)
    cli = daily_reading._load("runtime/cli.py")
    monkeypatch.setattr(cli, "_delegation_from_grant", lambda *_: (None, "unbound fixture"))
    before = (records / card).read_bytes()
    result = run(tmp_path, records)
    assert result["stopped_because"] == "NEEDS_DECISION", result
    assert result["model_calls"] == 1
    result = run(tmp_path, records, call=lambda *_: pytest.fail("cached source was sent again"), day=7)
    assert result["model_calls"] == 0 and result["stopped_because"] == "NEEDS_DECISION", result
    assert (records / card).read_bytes() == before
    assert result["cursor"] == {}
    assert len(result["outcomes"]["readings"][0]["awaiting"]) == 1
    # No source body in the durable recipe state; the packet invocation owns it.
    assert "The endpoint" not in json.dumps(result["steps"])


def test_granted_reading_uses_writer_then_recall_and_next_period_no_model(tmp_path, monkeypatch):
    records, card = fixture(tmp_path)
    cli = daily_reading._load("runtime/cli.py")
    monkeypatch.setattr(cli, "_delegation_from_grant", lambda *_: (
        {"covers": ["example"], "until": "2026-10-31T00:00:00Z", "granted_by": "fixture"}, "test grant"))
    result = run(tmp_path, records)
    assert result["stopped_because"] == "COMPLETED", result
    assert result["outcomes"]["readings"][0]["receipt"]["state"] == "COMPLETE"
    recall = daily_reading._load("intake/claim_recall.py").recall(records)
    assert recall["counts"]["CURRENT"] == 1, recall
    result = run(tmp_path, records, call=lambda *_: pytest.fail("completed source was sent again"), day=7)
    assert result["stopped_because"] == "NO_CHANGE", result
    assert result["model_calls"] == 0


def test_reader_identity_and_placement_are_not_model_fields():
    brief = {"source": {"card": "information/understanding/projects/x.md"}, "read": {"card_digest": "a"},
             "material": {"text": "The endpoint returned HTTP 200.\n"}}
    result = model(None, None, None)
    reading = native_reader.reading_from(brief, result)
    assert reading["claims"][0]["card"] == brief["source"]["card"]
    assert reading["source"] == brief["source"]
    result["claims"][0]["confirmation"] = "user_confirmed"
    with pytest.raises(ValueError, match="shape"):
        native_reader.reading_from(brief, result)


def test_complete_fact_receipt_does_not_drop_a_pending_decision_next_day(tmp_path, monkeypatch):
    records, card = fixture(tmp_path)
    cli = daily_reading._load("runtime/cli.py")
    monkeypatch.setattr(cli, "_delegation_from_grant", lambda *_: (
        {"covers": ["example"], "until": "2026-10-31T00:00:00Z", "granted_by": "fixture"}, "test grant"))
    def mixed(*args):
        result = model(*args)
        result["claims"].append({**result["claims"][0], "kind": "PROPOSAL", "touches": ["goal"]})
        return result
    first = run(tmp_path, records, call=mixed)
    assert first["stopped_because"] == "NEEDS_DECISION", first
    assert first["outcomes"]["readings"][0]["receipt"]["state"] == "COMPLETE"
    second = run(tmp_path, records, call=lambda *_: pytest.fail("do not repeat the model"), day=7)
    assert second["stopped_because"] == "NEEDS_DECISION", second
    assert second["outcomes"]["alignment"]["pending_readings"] == 1
    assert second["model_calls"] == 0

    # Accept only the pending business decision from the original reading.
    writer = daily_reading._load("intake/understanding_semantic.py")
    result = first["outcomes"]["readings"][0]
    reading_path = Path(result["reading"])
    reading = writer.read_reading(reading_path)
    resolution = json.loads(Path(result["resolution"]).read_text())
    identity = resolution["acceptance"]["identity"]
    row = dict(zip(writer.ACCEPTANCE_IDENTITY, identity))
    row.update(by="test-human", at="2026-10-07T01:00:00Z",
               claims=[resolution["awaiting"][0]["id"]])
    accept_path = tmp_path / "accepted.jsonl"
    accept_path.write_text(json.dumps(row) + "\n")
    capture = daily_reading._load("intake/capture_coverage.py")
    delta = daily_reading._load("intake/understanding_delta.py")
    def measure():
        report = delta.read_and_reconcile(records, capture.read_root(records)["objects"])
        return cli._reading_current(writer, report, reading)
    receipt = writer.apply(reading_path, records, measure=measure,
                           accepted=writer.read_acceptances(accept_path),
                           committed_at="2026-10-07T01:00:00Z")
    assert receipt["state"] == "COMPLETE"
    third = run(tmp_path, records, call=lambda *_: pytest.fail("accepted source sent again"), day=8)
    assert third["stopped_because"] == "NO_CHANGE", third
    assert third["outcomes"]["alignment"]["pending_readings"] == 0


def test_unreadable_bound_source_is_not_no_change(tmp_path):
    records, _ = fixture(tmp_path)
    path = next((records / "information/raw").rglob("manifest.json"))
    path.write_text('{"not":"a capture"}')
    result = run(tmp_path, records, call=lambda *_: pytest.fail("unreadable source"))
    assert result["stopped_because"] == "SOURCE_UNAVAILABLE", result


def test_oversized_source_is_refused_before_invocation(tmp_path, monkeypatch):
    monkeypatch.setattr(native_reader, "invoke_once", lambda **_: pytest.fail("must refuse before spawn"))
    with pytest.raises(ValueError, match="input limit"):
        native_reader.invoke({"text": "x" * 100_000}, directory=tmp_path, executable="/bin/false")


def test_document_quote_uses_original_escaped_bytes_and_not_model_im_positions():
    body = "Header\n\\- **Endpoint**: \\`test\\` returned 200.\n"
    packet = {"source": {"card": "information/understanding/projects/x.md"}, "read": {},
              "material": {"text": body}}
    output = model(None, None, None)
    output["claims"][0]["support"] = {"start_line": 2, "end_line": 2, "positions": [2]}
    support = native_reader.reading_from(packet, output)["claims"][0]["support"]
    assert support == {"positions": [], "excerpt": body.splitlines()[1]}
    output["claims"][0]["support"]["end_line"] = 3
    with pytest.raises(ValueError, match="outside"):
        native_reader.reading_from(packet, output)


def test_multiline_source_quote_remains_one_mergeable_claim_line():
    writer = daily_reading._load("intake/understanding_semantic.py")
    claim = {**model(None, None, None)["claims"][0], "id": "quoted", "confirmation": "agent_reconciled",
             "support": {"positions": [], "excerpt": "line one\nline two\r\n<!-- untrusted -->"}}
    line = writer._claim_line(claim)
    assert "\n" not in line and "\r" not in line
    assert "<!--" not in line
    ids, _, _ = writer._merge_claims(line + "\n", [("next", "- `next` another claim")], [])
    assert ids == ["next", "quoted"]


@pytest.mark.parametrize("escape", ["traversal", "symlink"])
def test_semantic_writer_cannot_escape_through_claim_card(tmp_path, escape):
    writer = daily_reading._load("intake/understanding_semantic.py")
    base = tmp_path / "information/understanding"
    base.mkdir(parents=True)
    other = tmp_path / "private.md"
    other.write_text("Unrelated human content.\n")
    if escape == "symlink":
        (base / "linked.md").symlink_to(other)
        card = "information/understanding/linked.md"
    else:
        card = "information/understanding/../../private.md"
    with pytest.raises(writer.Refused, match="not under"):
        writer._write_fence(tmp_path, base, card, [("c1", "- `c1` text")], role=writer.OWNER)
    assert other.read_text() == "Unrelated human content.\n"
