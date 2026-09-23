"""§B1/P1 — which sources are in force, by what policy, decided by which rule.

WHY THIS FILE EXISTS
--------------------
`docs/INFORMATION-DOCUMENT-LIFECYCLE.md` §6 names `information/sources/` as the
place a source is DECLARED and says the constraint the whole design turns on:
不让两份可编辑列表同时生效. Two editable lists in force is an ORDERING problem —
when `DRIFT_WATCHLIST.txt` and `not-archived.yaml` disagree there is no answer to
which one a collector obeys, and the answers a reader reaches for instead (the
newer file, the one that loaded last) are not rules.

Two axes, and the assertions here are written so that each one would have to be
wrong for the other to look fine:

  1. THE POLICY IS DECIDED, NOT DEFAULTED. Every row carries the ONE rule that
     decided it at the one precedence level that was reached, and a label met
     under a deny is reported in `bypassed` rather than dropped. The tests below
     reach each of the four levels by its own route and check the row names that
     route; and they check the two ways a level is reached badly — two labels on
     one axis, which is a question answered twice and is reported as CONFLICT,
     and a rule about an object no capture holds, which is UNRESOLVED rather
     than invisible.

  2. THE KEY IS AN IDENTITY, NOT A NAME. `docx-research-outreach-sop` is the
     directory name of two different link objects in this tree, so a slug is a
     label that is unique until it is not. Declarations are matched on
     `feishu:chat:<chat_id>` / `feishu:doc:<url>` / `feishu:person:<open_id>`;
     the collector's slug is reported beside the identity and never used as one,
     and a declaration whose only key is a display name is refused with the
     reason rather than matched by luck. The near miss is asserted as well: a
     name that matches and an id that does not is a NON-match, which is the
     whole point of the requirement.

WHAT IS ASSERTED HERE, AND WHAT IS NOT
--------------------------------------
Not that the designed policy is the right policy: the five labels and the four
precedence levels are read from the documents, and a test that re-decided them
would be a second opinion rather than a check. What is asserted is that a row's
answer is the answer of a named rule at a named level, that a name never decides
one, and that the consumer which already existed for a followed-source list —
`workchain.source_coverage`, reading a file nobody has written — can be answered
by these rows instead.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "runtime" / "cli.py"
CONTRACT = REPO_ROOT / "intake" / "workchain-contract.yaml"

#: The canonical records checkout, as `tests/test_capture_coverage.py` and
#: `tests/test_source_waterline.py` also name it. The tests at the end of this
#: file assert only tree-INDEPENDENT invariants against it, so that they stay
#: true when the tree moves and still catch a reader that stopped respecting
#: them.
LIVE_RECORDS = Path("/data/home/vince/workspace/records")


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("source_policy", "source_policy.py")
capture = _load("capture_coverage", "capture_coverage.py")
chain = _load("workchain", "workchain.py")

RAW = "information/raw"
CHAT_ROOT = f"{RAW}/feishu/chats"
SOURCES = "information/sources"
WATCHLIST = f"{RAW}/DRIFT_WATCHLIST.txt"
REGISTRY = "information/not-archived.yaml"

#: The key shape a row may carry. A key is `provider:kind:id`, which is the
#: property the whole no-display-name rule is about: a slug has no kind in it,
#: and one that wandered into a key would fail this.
KEY_SHAPE = re.compile(r"^[a-z]+:(chat|doc|person):.+$")

FOLLOW_BLOCK = {
    "root_object": "feishu:doc:https://x.feishu.cn/wiki/root",
    "depth": 2,
    "include": ["pages"],
    "exclude": [],
    "new_descendants": "follow",
    "history_start": "2026-09-01",
    "check_policy": "daily",
    "budget": {"pages": 40},
    "audience": "local snapshot, this node only",
}
SCOPE = {"history_start": "2026-08-01", "budget": {"pages": 200}}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def a_chat(records: Path, *, slug: str = "x", chat_id: str | None = "oc_x",
           kind: str = "people", chat_type: str = "p2p", name: str = "X",
           open_id: str | None = None, tenant_key: str | None = None) -> Path:
    """One chat capture object: `<kind>/<slug>/<chat_id>/manifest.json`."""
    directory = records / CHAT_ROOT / kind / slug / (chat_id or "no-id")
    directory.mkdir(parents=True, exist_ok=True)
    document: dict = {"chat": {"chat_type": chat_type, "name": name},
                      "sync": {"mode": "incremental", "msg_count": 3,
                               "pagination_complete": True,
                               "latest_create_time": "2026-09-11 10:07"}}
    if chat_id:
        document["chat"]["chat_id"] = chat_id
    if open_id or tenant_key:
        document["contact"] = {k: v for k, v in
                               (("open_id", open_id), ("tenant_key", tenant_key),
                                ("name", name)) if v}
    (directory / "manifest.json").write_text(
        json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return directory


def a_document(records: Path, *, slug: str = "docx-a",
               url: str = "https://x.feishu.cn/docx/a", title: str = "A",
               chat_slug: str = "g", chat_id: str = "oc_g",
               message_id: str = "om_1",
               content_sha256: str = "a" * 64) -> Path:
    """One link capture object: `<chat>/links/<slug>/manifest.json`.

    Two of these in two chats with one `url` is the shape the real tree holds
    and the reason both rows are kept: §3 of DEEP-ARCHIVE-POLICY wants the
    discovery edges preserved, so the reader states `same_identity_as` instead
    of collapsing them.
    """
    directory = records / CHAT_ROOT / "groups" / chat_slug / chat_id / "links" / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({
        "url": url, "type": "docx", "title": title,
        "content_sha256": content_sha256, "source_message_id": message_id,
        "media": [],
    }, ensure_ascii=False), encoding="utf-8")
    return directory


def declared(records: Path, document: dict, *, name: str = "sources.yaml") -> Path:
    """`information/sources/<name>` — the design's own declaration location."""
    directory = records / SOURCES
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    import yaml
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def a_source(**over) -> dict:
    """A declaration entry that is readable, keyed and complete."""
    entry = {
        "id": over.pop("id", "s1"),
        "identity": over.pop("identity", {"provider": "feishu", "chat_id": "oc_x"}),
        "policies": over.pop("policies", ["DEEP_REQUIRED"]),
        "scope": over.pop("scope", dict(SCOPE)),
        "why": over.pop("why", "对接人指定"),
    }
    entry.update(over)
    return entry


def a_contact(**over) -> dict:
    """A contact seed: keyed by the contact's own open_id, never by a name."""
    seed = {
        "id": over.pop("id", "c1"),
        "identity": over.pop("identity", {"provider": "feishu",
                                          "open_id": "ou_bruce"}),
        "recorded_as": over.pop("recorded_as", "Br"),
        "relation": over.pop("relation", "priority_contact"),
        "p2p_policy": over.pop("p2p_policy", "DEEP_REQUIRED"),
        "scope": over.pop("scope", dict(SCOPE)),
        "decided_by_user": over.pop("decided_by_user", "2026-09-20"),
        "why": over.pop("why", "重点对接人"),
    }
    seed.update(over)
    return seed


def write_watchlist(records: Path, lines: list[str]) -> Path:
    path = records / WATCHLIST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# a comment\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_registry(records: Path, document: dict) -> Path:
    import yaml
    path = records / REGISTRY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    return path


def tree(tmp_path: Path, *chats, **kwargs) -> Path:
    """A records checkout holding exactly the capture objects asked for."""
    records = kwargs.pop("records", None) or (tmp_path / "records")
    (records / CHAT_ROOT).mkdir(parents=True, exist_ok=True)
    for chat in chats:
        if isinstance(chat, dict):
            a_chat(records, **chat)
    return records


def resolved(records: Path) -> dict:
    """The resolver, run the way the verb runs it: objects come from the walk."""
    return policy.resolve(records, capture.read_root(records)["objects"])


def row_for(document: dict, key: str) -> dict:
    return next(row for row in document["rows"]
                if (row["identity"] or {}).get("key") == key)


def rules_of(row: dict, decided_by: str) -> list[dict]:
    return [rule for rule in row["rules"] if rule["decided_by"] == decided_by]


@pytest.fixture(scope="module")
def document() -> dict:
    """The live tree, resolved once for the whole class that asserts on it."""
    if not LIVE_RECORDS.is_dir():
        pytest.skip("this node has no records checkout to walk")
    return resolved(LIVE_RECORDS)


def cli(*args, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(CLI), *map(str, args)],
                          capture_output=True, text=True, cwd=str(REPO_ROOT),
                          env=env)


def data_root(tmp_path: Path) -> dict:
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {"DATA_ROOT": str(data), "HOME": str(data),
            "WORKSPACE_ROOT": str(data / "workspace"),
            "ARTIFACTS_ROOT": str(data / "artifacts"),
            "SERVEROS_STATE_DIR": str(data / "state" / "serveros")}


# ---------------------------------------------------------------------------
# 1. The policy is decided, not defaulted
# ---------------------------------------------------------------------------


class TestThePolicyIsDecidedNotDefaulted:
    def test_a_chat_no_rule_reaches_takes_the_general_default(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "IN_FORCE"
        assert row["policies"] == ["SHALLOW"]
        assert row["policy"] == "SHALLOW"
        assert row["axis"] == "depth"
        assert row["decided_by"] == "GENERAL_DEFAULT"
        assert any("no rule reaches this object" in sentence
                   for sentence in row["why"])

    def test_the_default_is_stated_with_its_rule_and_the_registry_it_was_read_over(
            self, tmp_path):
        """A default that cannot be told from a decision is how a policy nobody
        made gets obeyed — and §1's 一般默认 is what the registry's silence is
        read against, so the denominator it closed over is named."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {"total": 23, "by_reason": {"bot": 23},
                                 "entries": [], "reclassified": []})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["decided_by"] == "GENERAL_DEFAULT"
        why = " ".join(row["why"])
        assert "23" in why
        assert "not one of them" in why

    def test_a_declaration_beats_the_general_default(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source()]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "IN_FORCE"
        assert row["policies"] == ["DEEP_REQUIRED"]
        assert row["decided_by"] == "DECLARATION"
        assert row["declared"]["id"] == "s1"
        assert "对接人指定" in row["rules"][0]["why"]

    def test_a_document_is_undeclared_and_not_shallow(self, tmp_path):
        """The general default of §3 is a default for CHATS. A document
        captured from a chat is material OF that chat, and whether it is also a
        followed source is a decision nobody has made: granting it SHALLOW would
        report a decision that was never taken."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        a_document(records)
        row = row_for(resolved(records), "feishu:doc:https://x.feishu.cn/docx/a")
        assert row["state"] == "UNDECLARED"
        assert row["policies"] == []
        assert row["decided_by"] is None
        assert any("the general default for chats is not a default for documents"
                   in sentence for sentence in row["why"])

    def test_a_chat_with_no_chat_id_takes_no_default(self, tmp_path):
        """A manifest with no `chat.chat_id` cannot be named by a declaration,
        so it cannot be given a default either: it is reported, not guessed."""
        records = tree(tmp_path, {"slug": "x", "chat_id": None})
        document = resolved(records)
        row = document["rows"][0]
        assert row["state"] == "UNDECLARED"
        assert row["identity"]["key"] is None
        assert any("no stable identity" in sentence for sentence in row["why"])

    def test_a_deny_outranks_a_declaration_and_the_label_is_reported_as_bypassed(
            self, tmp_path):
        """「具体标签不能绕上级deny」 as a field instead of a sentence."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {
            "total": 1, "entries": [{"chat_gid": "oc_x", "name": "X",
                                     "reason": "bot"}], "reclassified": []})
        declared(records, {"entries": [a_source()]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "IN_FORCE"
        assert row["policies"] == ["NONE"]
        assert row["axis"] == "exclusion"
        assert row["decided_by"] == "AUTHORIZATION_REGISTRY"
        assert [item["labels"] for item in row["bypassed"]] == [["DEEP_REQUIRED"]]
        assert row["bypassed"][0]["decided_by"] == "DECLARATION"
        # The entry that wrote it is still on disk, so the rule is still here.
        assert rules_of(row, "DECLARATION")

    def test_a_deny_is_not_a_second_answer_to_the_depth_question(self, tmp_path):
        """`NONE` says which question not to ask; it is not an answer to the
        depth question, so a deny beside a depth label is not a CONFLICT."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {"total": 1, "reclassified": [],
                                 "entries": [{"chat_gid": "oc_x",
                                              "reason": "bot"}]})
        declared(records, {"entries": [a_source()]})
        assert row_for(resolved(records), "feishu:chat:oc_x")["state"] == "IN_FORCE"

    def test_two_labels_on_one_axis_are_a_conflict(self, tmp_path):
        """One object has one answer to "how deep"; two is a question declared
        twice, and picking the stronger one would be this reader deciding it."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["DEEP_REQUIRED",
                                                          "SHALLOW"])]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "CONFLICT"
        assert row["policies"] == ["DEEP_REQUIRED", "SHALLOW"]
        assert any("one answer to" in sentence for sentence in row["why"])

    def test_two_labels_on_different_axes_are_both_in_force(self, tmp_path):
        """Depth and tracking are different questions about one object, which is
        the whole reason the labels are said to be one vocabulary over axes."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["DEEP_REQUIRED", "FOLLOW"],
                                                follow=dict(FOLLOW_BLOCK))]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "IN_FORCE"
        assert row["policies"] == ["DEEP_REQUIRED", "FOLLOW"]
        assert row["axes"] == ["depth", "tracking"]
        assert row["policy"] == "DEEP_REQUIRED"
        assert row["follow"]["state"] == "DECLARED"

    def test_the_contact_relation_resolves_on_the_contact_id_and_not_the_name(
            self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x",
                                  "open_id": "ou_bruce", "name": "Bruce"})
        declared(records, {"contacts": [a_contact(recorded_as="Br")]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["state"] == "IN_FORCE"
        assert row["policies"] == ["DEEP_REQUIRED"]
        assert row["decided_by"] == "CONTACT_RELATION"
        assert rules_of(row, "CONTACT_RELATION")[0]["evidence"]["basis"] == "p2p"

    def test_a_matching_name_and_a_different_id_is_not_a_match(self, tmp_path):
        """The requirement is 按稳定身份, and this is the test that makes it
        real: the display names agree and the ids do not, so no rule is reached
        and the chat takes the general default."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x",
                                  "open_id": "ou_someone_else", "name": "Br"})
        declared(records, {"contacts": [a_contact(recorded_as="Br")]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["policies"] == ["SHALLOW"]
        assert row["decided_by"] == "GENERAL_DEFAULT"
        assert not rules_of(row, "CONTACT_RELATION")

    def test_a_contact_is_upgraded_only_by_being_named_as_an_entry(self, tmp_path):
        """A project group takes the relation's label only when the relation
        names that group by its own id. A group that merely CONTAINS the
        contact is not upgraded, which is what 「大通用群不因包含这些联系人全部
        deep」 excludes."""
        records = tree(tmp_path,
                       {"slug": "x", "chat_id": "oc_x", "open_id": "ou_bruce"},
                       {"slug": "big", "chat_id": "oc_big", "kind": "groups",
                        "chat_type": "group", "name": "大通用群"})
        declared(records, {"contacts": [a_contact()]})
        document = resolved(records)
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == "CONTACT_RELATION"
        big = row_for(document, "feishu:chat:oc_big")
        assert big["decided_by"] == "GENERAL_DEFAULT"
        assert big["policies"] == ["SHALLOW"]

    def test_a_project_group_is_upgraded_when_the_relation_names_it(
            self, tmp_path):
        records = tree(tmp_path,
                       {"slug": "x", "chat_id": "oc_x", "open_id": "ou_bruce"},
                       {"slug": "small", "chat_id": "oc_small", "kind": "groups",
                        "chat_type": "group", "name": "小项目群"})
        declared(records, {"contacts": [a_contact(project_groups=[
            {"provider": "feishu", "chat_id": "oc_small"}])]})
        row = row_for(resolved(records), "feishu:chat:oc_small")
        assert row["decided_by"] == "CONTACT_RELATION"
        assert row["policies"] == ["DEEP_REQUIRED"]
        assert rules_of(row, "CONTACT_RELATION")[0]["evidence"]["basis"] == \
            "project_group"

    def test_a_project_group_named_by_a_display_name_is_refused(self, tmp_path):
        records = tree(tmp_path,
                       {"slug": "x", "chat_id": "oc_x", "open_id": "ou_bruce"},
                       {"slug": "small", "chat_id": "oc_small", "kind": "groups",
                        "chat_type": "group", "name": "小项目群"})
        declared(records, {"contacts": [a_contact(project_groups=["小项目群"])]})
        document = resolved(records)
        assert row_for(document, "feishu:chat:oc_small")["decided_by"] == \
            "GENERAL_DEFAULT"
        items = document["summary"]["unusable_declarations"]["items"]
        assert any("按稳定身份" in item["why"] for item in items)

    def test_a_reclassified_entry_does_not_deny(self, tmp_path):
        """An entry a person moved OUT of the exclusion registry is a recorded
        judgement that the object IS a source. Reading the registry as an
        exclusion would deny exactly the object the judgement was about."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {"total": 0, "entries": [], "reclassified": [
            {"chat_gid": "oc_x", "name": "X", "was": "machine_named",
             "now": "source", "why": "真人来源"}]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["policies"] == ["SHALLOW"]
        assert row["state"] == "IN_FORCE"
        assert any("RECLASSIFIED" in sentence for sentence in row["why"])

    def test_a_reclassified_entry_that_is_also_still_denied_stays_denied(
            self, tmp_path):
        """The clearing rule is `reclassified` minus `denied`, not
        `reclassified` on its own: an entry that is in both is not yet moved."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {
            "total": 1, "entries": [{"chat_gid": "oc_x", "reason": "bot"}],
            "reclassified": [{"chat_gid": "oc_x", "was": "machine_named",
                              "now": "source"}]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["policies"] == ["NONE"]

    def test_a_declaration_about_a_source_that_was_never_captured_is_unresolved(
            self, tmp_path):
        """A rule about a source missing from a coverage report reads as a
        source with nothing wrong, so the row exists even with no object."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(
            identity={"provider": "feishu",
                      "url": "https://x.feishu.cn/docx/never"})]})
        document = resolved(records)
        row = row_for(document, "feishu:doc:https://x.feishu.cn/docx/never")
        assert row["state"] == "UNRESOLVED"
        assert row["decided_by"] == "DECLARATION"
        assert row["shape"] == "NO_CAPTURE_OBJECT"
        assert "no capture object in this tree holds it" in row["why"][0]

    def test_a_watchlist_line_matching_no_object_is_unresolved(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_watchlist(records, ["ghost"])
        row = row_for(resolved(records), "legacy:ghost")
        assert row["state"] == "UNRESOLVED"
        assert row["decided_by"] == "LEGACY_WATCHLIST"

    def test_a_watchlist_line_matching_two_objects_is_applied_to_neither(
            self, tmp_path):
        """A slug is a directory name and two chats can hold one of the same
        name. Which object the line means is the author's question; applying it
        to both would put a follow nobody declared on one of them."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"},
                       {"slug": "y", "chat_id": "oc_y"})
        write_watchlist(records, ["x"])
        a_document(records, slug="x", chat_slug="x", chat_id="oc_x")
        a_document(records, slug="x", chat_slug="y", chat_id="oc_y")
        document = resolved(records)
        ambiguous = row_for(document, "legacy:x")
        assert ambiguous["state"] == "UNRESOLVED"
        assert "matches 2 capture objects" in ambiguous["why"][0]
        key = "feishu:doc:https://x.feishu.cn/docx/a"
        for row in [item for item in document["rows"]
                    if (item["identity"] or {}).get("key") == key]:
            assert not rules_of(row, "LEGACY_WATCHLIST")

    def test_the_legacy_watchlist_puts_follow_in_force_and_asks_to_be_migrated(
            self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_watchlist(records, ["wiki-litellm-work-doc"])
        a_document(records, slug="wiki-litellm-work-doc", title="LiteLLM工作文档",
                   url="https://x.feishu.cn/wiki/litellm")
        row = row_for(resolved(records), "feishu:doc:https://x.feishu.cn/wiki/litellm")
        assert row["policies"] == ["FOLLOW"]
        assert row["policy"] == "FOLLOW"
        assert row["axis"] == "tracking"
        assert row["decided_by"] == "LEGACY_WATCHLIST"
        assert row["follow"]["state"] == "UNDECLARED_BY_LEGACY"
        assert sorted(row["follow"]["missing"]) == sorted(policy.FOLLOW_PRODUCED)
        assert row["migration"]["needed"] is True
        assert row["migration"]["from"] == ["LEGACY_WATCHLIST"]

    def test_a_registered_exclusion_and_a_capture_of_one_chat_cannot_both_be_right(
            self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_registry(records, {"total": 1, "reclassified": [],
                                 "entries": [{"chat_gid": "oc_x",
                                              "reason": "bot"}]})
        document = resolved(records)
        assert [item["kind"] for item in document["contradictions"]] == \
            ["DENIED_BUT_CAPTURED"]
        assert document["contradictions"][0]["key"] == "feishu:chat:oc_x"
        assert "not this reader's to decide" in document["contradictions"][0]["why"]

    def test_every_rule_names_its_level_and_its_evidence(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_watchlist(records, ["x"])
        a_document(records, slug="x", chat_slug="x", chat_id="oc_x")
        write_registry(records, {"total": 0, "reclassified": [],
                                 "entries": [{"chat_gid": "oc_other",
                                              "reason": "bot"}]})
        declared(records, {"entries": [a_source()]})
        document = resolved(records)
        for row in document["rows"]:
            for rule in row["rules"]:
                assert rule["level"] == policy.LEVEL_OF[rule["decided_by"]]
                assert rule["evidence"]
                assert rule["why"]
                assert isinstance(rule["labels"], list)

    def test_the_rows_are_read_only_from_the_records(self, tmp_path):
        """This module is a reader. It is asserted here rather than trusted
        because a resolver that also collected would be deciding the thing it
        was asked to describe."""
        source = (REPO_ROOT / "intake" / "source_policy.py").read_text(
            encoding="utf-8")
        for verb in ("os.replace", "write_text", ".write(", "unlink", "mkdir",
                     "shutil."):
            assert verb not in source, f"source_policy.py must not {verb}"


# ---------------------------------------------------------------------------
# 2. The key is an identity, not a name
# ---------------------------------------------------------------------------


class TestTheKeyIsAnIdentityNotAName:
    def test_a_declaration_keyed_only_by_a_display_name_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "docx-research-outreach-sop",
                                  "chat_id": "oc_x", "name": "对接SOP"})
        declared(records, {"entries": [
            {"id": "s1", "name": "docx-research-outreach-sop",
             "policy": "DEEP_REQUIRED", "scope": dict(SCOPE), "why": "看起来像"}]})
        document = resolved(records)
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == \
            "GENERAL_DEFAULT"
        item = document["summary"]["unusable_declarations"]["items"][0]
        assert "docx-research-outreach-sop" in item["why"]
        assert "no stable identity" in item["why"]

    def test_a_declaration_naming_two_keys_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(identity={
            "provider": "feishu", "chat_id": "oc_x",
            "url": "https://x.feishu.cn/docx/a"})]})
        items = resolved(records)["summary"]["unusable_declarations"]["items"]
        assert any("is about one of them" in item["why"] for item in items)

    def test_a_declaration_naming_no_provider_is_refused(self, tmp_path):
        """A key with no provider is not one any provider's own id space can be
        checked against — the same chat id can be spelled by two products."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(identity={"chat_id": "oc_x"})]})
        items = resolved(records)["summary"]["unusable_declarations"]["items"]
        assert any("names no `provider`" in item["why"] for item in items)

    def test_a_label_that_is_not_one_of_the_five_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["ARCHIVE_EVERYTHING"])]})
        items = resolved(records)["summary"]["unusable_declarations"]["items"]
        assert any("is not one of" in item["why"] for item in items)

    def test_an_entry_with_no_policy_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        entry = a_source()
        entry.pop("policies")
        declared(records, {"entries": [entry]})
        items = resolved(records)["summary"]["unusable_declarations"]["items"]
        assert any("nothing for it to put in force" in item["why"] for item in items)

    def test_two_objects_with_one_identity_are_two_rows_and_one_source(
            self, tmp_path):
        """`docx-research-outreach-sop` in the real tree: one url, one content
        hash, two `source_message_id`s in two chats. §3 keeps both discovery
        edges, so the rows stay two and the fact that they are one source is
        stated on both instead of being collapsed by a deduplication."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"},
                       {"slug": "y", "chat_id": "oc_y", "kind": "groups",
                        "chat_type": "group"})
        a_document(records, slug="docx-research-outreach-sop", chat_slug="x",
                   chat_id="oc_x", message_id="om_1")
        a_document(records, slug="docx-research-outreach-sop", chat_slug="y",
                   chat_id="oc_y", message_id="om_2")
        key = "feishu:doc:https://x.feishu.cn/docx/a"
        document = resolved(records)
        rows = [row for row in document["rows"]
                if (row["identity"] or {}).get("key") == key]
        assert len(rows) == 2
        assert all(len(row["same_identity_as"]) == 1 for row in rows)
        assert rows[0]["observed_as"] == rows[1]["observed_as"] == \
            "docx-research-outreach-sop"
        # A declaration on the identity reaches both, and reaches each once.
        declared(records, {"entries": [a_source(
            identity={"provider": "feishu", "url": key.split("doc:", 1)[1]})]})
        document = resolved(records)
        declared_rows = [row for row in document["rows"]
                         if rules_of(row, "DECLARATION")]
        assert len(declared_rows) == 2
        for row in declared_rows:
            assert len(rules_of(row, "DECLARATION")) == 1
            assert row["policies"] == ["DEEP_REQUIRED"]
        assert document["summary"]["unusable_declarations"]["count"] == 0

    def test_the_slug_is_reported_beside_the_identity_and_never_as_one(
            self, tmp_path):
        """The directory name a link object was written to is the collector's
        word for it, reported as `observed_as` and never composed into a key."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        a_document(records, slug="the-collectors-word", url="https://x/docx/w")
        row = row_for(resolved(records), "feishu:doc:https://x/docx/w")
        assert row["observed_as"] == "the-collectors-word"
        assert row["identity"]["observed_as"] == "the-collectors-word"
        assert row["identity"]["key"] == "feishu:doc:https://x/docx/w"
        assert KEY_SHAPE.match(row["identity"]["key"])
        assert "the-collectors-word" not in row["identity"]["key"]

    def test_a_chats_observed_name_is_its_id_and_not_the_collectors_slug(
            self, tmp_path):
        """The capture layout puts a chat's manifest under its own id, so the
        directory name a chat was found under IS the id. That is why no legacy
        line can key a chat: `DRIFT_WATCHLIST.txt` matches `links/<link-id>/`
        directory names, and for a chat there is no such word to match on — the
        manifest is what its identity is read from."""
        records = tree(tmp_path, {"slug": "the-collectors-word", "chat_id": "oc_x"})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["observed_as"] == "oc_x"
        assert row["identity"]["key"] == "feishu:chat:oc_x"

    def test_a_pinned_tenant_matches_silence_and_not_a_different_tenant(
            self, tmp_path):
        """A key that is stable within a tenant is not stable across tenants. A
        different one is a near miss reported with its reason; a manifest that
        carries none contradicts nothing and is matched."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x",
                                  "tenant_key": "tk_other"})
        declared(records, {"entries": [a_source(identity={
            "provider": "feishu", "chat_id": "oc_x", "tenant_key": "tk_mine"})]})
        document = resolved(records)
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == \
            "GENERAL_DEFAULT"
        assert document["summary"]["near_misses"], "a near miss must be reported"
        assert "different one" in document["summary"]["near_misses"][0]["why"]
        # And with the tenant absent from the manifest, nothing contradicts it.
        records2 = tree(tmp_path / "b", {"slug": "x", "chat_id": "oc_x"})
        declared(records2, {"entries": [a_source(identity={
            "provider": "feishu", "chat_id": "oc_x", "tenant_key": "tk_mine"})]})
        assert row_for(resolved(records2), "feishu:chat:oc_x")["decided_by"] == \
            "DECLARATION"

    def test_a_person_identity_keys_a_person(self):
        assert policy.key_of({"provider": "feishu", "open_id": "ou_a"}) == \
            "feishu:person:ou_a"
        assert policy.key_of({"provider": "feishu"}) is None
        # The provider is part of the key: one product's id space is not
        # another's, and a rule matched against the wrong one's ids is a rule
        # about a different object.
        assert policy.key_of({"provider": "slack", "chat_id": "oc_x"}) == \
            "slack:chat:oc_x"

    def test_the_absent_key_is_none_and_not_an_empty_string(self):
        """A row keyed by '' would match the next unnamed object's '' — the
        failure mode the whole no-display-name rule exists to prevent, one
        level down."""
        for document in ({}, {"chat": {}, "sync": {}}):
            identity = policy.identity_of(document, "chat_capture", "a/b")
            assert identity["key"] is None
            assert identity["why"]


# ---------------------------------------------------------------------------
# 3. What a label requires before it is in force
# ---------------------------------------------------------------------------


class TestWhatALabelRequires:
    def test_a_follow_without_its_block_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["FOLLOW"])]})
        document = resolved(records)
        item = document["summary"]["unusable_declarations"]["items"][0]
        assert "a follow is eight declarations and not a label" in item["why"]
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == \
            "GENERAL_DEFAULT"

    def test_a_follow_missing_one_of_the_nine_is_refused(self, tmp_path):
        """The eight of §6 are required TOGETHER: a root with no budget is a
        promise to crawl a subtree forever."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        block = dict(FOLLOW_BLOCK)
        block.pop("budget")
        declared(records, {"entries": [a_source(policies=["FOLLOW"], follow=block)]})
        item = resolved(records)["summary"]["unusable_declarations"]["items"][0]
        assert "`budget`" in item["why"]

    def test_a_deep_obligation_without_a_range_is_refused(self, tmp_path):
        """§2 makes a deep archive an obligation to complete a material
        closure, and an obligation with no declared range is an obligation to
        archive everything."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        entry = a_source()
        entry.pop("scope")
        declared(records, {"entries": [entry]})
        document = resolved(records)
        item = document["summary"]["unusable_declarations"]["items"][0]
        assert "`scope`" in item["why"]
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == \
            "GENERAL_DEFAULT"

    def test_a_contact_that_imposes_a_deep_label_needs_a_range_too(self, tmp_path):
        """A deep obligation is deep whoever imposed it, so the accepted
        relation has an effect rather than an exemption."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x",
                                  "open_id": "ou_bruce"})
        seed = a_contact()
        seed.pop("scope")
        declared(records, {"contacts": [seed]})
        document = resolved(records)
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == \
            "GENERAL_DEFAULT"
        assert any("obligation with no declared range" in item["why"]
                   for item in document["summary"]["unusable_declarations"]["items"])

    def test_a_reference_without_its_block_is_refused(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["REFERENCE"])]})
        item = resolved(records)["summary"]["unusable_declarations"]["items"][0]
        assert "is a bookmark" in item["why"]

    def test_a_reference_that_adopts_is_refused(self, tmp_path):
        """Adoption is not a property of a source: it is a design change decided
        by B5/B6 against the accepted baseline, so a declaration that adopts
        would be the reference library writing into canon."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["REFERENCE"], reference={
            "canonical_url": "https://x/blog", "permitted_scope": "read",
            "save": "LINK_ONLY", "adopt": True})]})
        item = resolved(records)["summary"]["unusable_declarations"]["items"][0]
        assert "Adoption is not a property of a source" in item["why"]

    def test_a_whole_article_copy_is_refused(self, tmp_path):
        """A whole-article copy needs a permission of its own, so the source
        declaration is not the document that grants it."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["REFERENCE"], reference={
            "canonical_url": "https://x/blog", "permitted_scope": "read",
            "save": "FULL_COPY"})]})
        item = resolved(records)["summary"]["unusable_declarations"]["items"][0]
        assert "a whole-article copy needs a permission" in item["why"]

    def test_a_reference_with_a_snapshot_is_accepted_and_named(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(policies=["REFERENCE"], reference={
            "canonical_url": "https://x/blog", "permitted_scope": "read, cite",
            "save": "SNAPSHOT"})]})
        row = row_for(resolved(records), "feishu:chat:oc_x")
        assert row["policies"] == ["REFERENCE"]
        assert row["reference"]["state"] == "DECLARED"
        assert row["reference"]["block"]["save"] == "SNAPSHOT"

    def test_a_declaration_file_that_cannot_be_parsed_leaves_the_rest_in_force(
            self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source()]}, name="a-good.yaml")
        broken = records / SOURCES / "b-broken.yaml"
        broken.write_text("entries: [\n", encoding="utf-8")
        document = resolved(records)
        assert document["declarations"]["state"] == "PARTIAL"
        assert any("cannot read" in item["why"]
                   for item in document["declarations"]["unreadable"])
        assert "could not be read" in document["declarations"]["why"]
        assert row_for(document, "feishu:chat:oc_x")["decided_by"] == "DECLARATION"

    def test_the_absent_location_is_a_state_and_not_an_empty_list(self, tmp_path):
        """`entries: []` would be a declaration that every source is undeclared.
        A statement about this tree is not a statement about the world."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declarations = resolved(records)["declarations"]
        assert declarations["state"] == "ABSENT"
        assert declarations["entries"] == []
        assert declarations["files"] == []
        assert "does not exist" in declarations["why"]
        assert "statement about this tree" in declarations["why"]

    def test_an_empty_declaration_file_is_not_an_absent_one(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        (records / SOURCES).mkdir(parents=True, exist_ok=True)
        (records / SOURCES / "sources.yaml").write_text("", encoding="utf-8")
        declarations = resolved(records)["declarations"]
        assert declarations["state"] == "PARTIAL"
        assert "an empty file declares nothing" in declarations["unreadable"][0]["why"]


# ---------------------------------------------------------------------------
# 4. The consumer that already existed
# ---------------------------------------------------------------------------


class TestTheConsumerThatAlreadyExisted:
    def test_the_contract_is_what_says_which_fields_the_consumer_needs(self):
        contract = chain.load_contract(CONTRACT)
        entry = next(item for item in contract["coverage"]
                     if item["id"] == "followed-sources")
        assert entry["fields"] == ["identity", "because", "refresh", "depth",
                                  "credential_ref", "publishable"]

    def test_the_rows_are_projected_into_the_consumers_field_set(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        fields = ["identity", "because", "refresh", "depth", "credential_ref",
                  "publishable"]
        projected = policy.project_rows(resolved(records), fields)
        assert projected["state"] == "READ"
        assert sorted(projected["fields"]) == sorted(fields)
        row = projected["rows"][0]
        assert row["depth"] == "SHALLOW"
        assert row["identity"] == "feishu:chat:oc_x"
        # A row decided by the general default carries no check policy, no
        # credential reference and no publish decision, and the projection says
        # so instead of filling them in: three of the consumer's six fields.
        assert row["missing_fields"] == ["refresh", "credential_ref",
                                        "publishable"]
        assert row["state"] == "MISSING"
        assert row["because"].startswith("no rule reaches this object")

    def test_the_consumer_reads_them_and_reports_its_own_coverage(self, tmp_path):
        """`workchain.source_coverage` reads `projects/<project>/context/
        sources.yaml`, a file nobody has written, which is why every project
        reports UNPRODUCED there today. It was already the consumer of a
        followed-source list; this is the adapter that lets the resolved policy
        answer it, and the fields come from its own contract entry."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source(
            policies=["DEEP_REQUIRED", "FOLLOW"], follow=dict(FOLLOW_BLOCK),
            credential_ref="vault:feishu/x-reader", publishable=False)]})
        contract = chain.load_contract(CONTRACT)
        entry = next(item for item in contract["coverage"]
                     if item["id"] == "followed-sources")
        projected = policy.project_rows(resolved(records), entry["fields"])
        report = chain.source_coverage(contract=contract, sources=projected["rows"])
        assert report["state"] == "READ"
        assert report["fields"] == entry["fields"]
        assert report["coverage"] == {"authored": 1, "complete": 1, "refused": 0}
        assert report["rows"][0]["state"] == "PRESENT"
        assert report["rows"][0]["depth"] == "DEEP_REQUIRED"
        assert report["rows"][0]["publishable"] is False

    def test_a_source_with_no_policy_at_all_is_not_handed_to_the_consumer(
            self, tmp_path):
        """A document nobody has decided about is UNDECLARED, and a projection
        that emitted it would report a followed source that nobody follows."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        a_document(records)
        fields = ["identity", "because", "refresh", "depth", "credential_ref",
                  "publishable"]
        document = resolved(records)
        assert row_for(document, "feishu:doc:https://x.feishu.cn/docx/a")["state"] \
            == "UNDECLARED"
        projected = policy.project_rows(document, fields)
        assert [row["identity"] for row in projected["rows"]] == ["feishu:chat:oc_x"]

    def test_the_consumer_refuses_a_credential_where_a_reference_belongs(
            self, tmp_path):
        """The field is validated rather than trusted, so what reaches the
        consumer matters even though this module only copies it."""
        contract = chain.load_contract(CONTRACT)
        report = chain.source_coverage(contract=contract, sources=[{
            "identity": "feishu:chat:oc_x", "because": "x", "refresh": "daily",
            "depth": "SHALLOW", "publishable": False,
            "credential_ref": "sk-" + "a" * 40}])
        assert report["rows"][0]["state"] == "REFUSED"
        assert report["coverage"]["refused"] == 1


# ---------------------------------------------------------------------------
# 5. The verb
# ---------------------------------------------------------------------------


class TestTheVerb:
    def test_the_verb_renders_the_policy_and_names_the_absent_location(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        run = cli("work", "source-policy", "--records-root", str(records),
                  env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        assert "declarations ABSENT" in run.stdout
        assert "feishu:chat:oc_x" in run.stdout
        assert "GENERAL_DEFAULT" in run.stdout

    def test_an_undeclared_document_is_visible_as_such(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        a_document(records)
        run = cli("work", "source-policy", "--records-root", str(records),
                  env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        assert "UNDECLARED" in run.stdout

    def test_the_verb_refuses_a_relative_records_root(self, tmp_path):
        run = cli("work", "source-policy", "--records-root", "records",
                  env_extra=data_root(tmp_path))
        assert run.returncode != 0
        assert "must be absolute" in run.stderr

    def test_the_verb_json_carries_the_schema_and_the_counts_add_up(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        write_watchlist(records, ["ghost"])
        run = cli("work", "source-policy", "--records-root", str(records), "--json",
                  env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        document = json.loads(run.stdout)
        assert document["schema"] == policy.SCHEMA
        assert sum(document["summary"]["by_state"].values()) == \
            document["summary"]["rows"]
        assert document["summary"]["objects"] == 1
        assert document["summary"]["rows"] == 2  # the chat and the ghost line

    def test_the_verb_hands_the_rows_to_the_consumer_it_already_had(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        run = cli("work", "source-policy", "--records-root", str(records),
                  "--coverage", "--json", env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        document = json.loads(run.stdout)
        assert document["followed_sources"]["state"] == "READ"
        assert document["followed_sources"]["coverage"] == {
            "authored": 1, "complete": 0, "refused": 0}
        assert document["followed_sources"]["fields"] == [
            "identity", "because", "refresh", "depth", "credential_ref",
            "publishable"]

    def test_the_verb_writes_nothing_into_the_records_tree(self, tmp_path):
        """A resolver a person runs to find out what is in force must not be a
        way for the answer to change."""
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        declared(records, {"entries": [a_source()]})
        write_watchlist(records, ["ghost"])
        write_registry(records, {"total": 1, "reclassified": [],
                                 "entries": [{"chat_gid": "oc_z",
                                              "reason": "bot"}]})

        def snapshot() -> dict:
            return {path.relative_to(records).as_posix():
                    path.read_bytes() for path in sorted(records.rglob("*"))
                    if path.is_file()}

        before = snapshot()
        run = cli("work", "source-policy", "--records-root", str(records),
                  "--coverage", env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        assert snapshot() == before

    def test_the_guard_refuses_a_field_no_producer_names(self):
        """The guard is applied where a record is EMITTED, so the test asserts
        it fires on the emitted document rather than on a producer."""
        with pytest.raises(policy.Refused) as caught:
            policy.no_unsourced({"schema": policy.SCHEMA, "invented": 1},
                                policy.DOCUMENT_PRODUCED, "policy")
        assert "policy.invented" in str(caught.value)

    def test_every_emitted_key_is_named_by_a_producer_table(self, tmp_path):
        records = tree(tmp_path, {"slug": "x", "chat_id": "oc_x"})
        document = resolved(records)
        assert set(document) <= set(policy.DOCUMENT_PRODUCED)
        for row in document["rows"]:
            assert set(row) <= set(policy.ROW_PRODUCED)
            for rule in row["rules"]:
                assert set(rule) <= set(policy.RULE_PRODUCED)
        assert set(document["summary"]) <= set(policy.SUMMARY_PRODUCED)


# ---------------------------------------------------------------------------
# 5b. A page captured from outside this workspace
# ---------------------------------------------------------------------------


#: Where a page capture lands, in the layout the real tree uses.
WEB_SLUG = "web/openai-developers/rethinking-skills-and-prompts"

WEB_URL = "https://developers.openai.com/blog/rethinking-skills-and-prompts"


def a_web_page(records: Path, *, slug: str = WEB_SLUG, url: str = WEB_URL,
               final_url: str | None = None, redirected: bool = False,
               title: str = "A Page",
               provider: str = "web") -> Path:
    """One page capture object, under `information/raw/web/<publisher>/<slug>/`."""
    directory = records / RAW / slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({
        "schema": "serveros/web-capture.v1",
        "source": {"provider": provider, "requested_url": url,
                   "final_url": final_url or url, "redirected": redirected},
        "identity_observed": {"title": title},
        "fetch": {"at": "2026-09-23T18:31+08:00", "http_status": 200,
                  "bytes": 317035, "sha256": "b" * 64, "captured_by": "curl"},
        "materials": {"inventoried": False, "count": 0},
    }, ensure_ascii=False), encoding="utf-8")
    return directory


def a_reference(**over) -> dict:
    """A REFERENCE declaration entry: the policy a page is captured under."""
    entry = {
        "id": over.pop("id", "ref1"),
        "identity": over.pop("identity", {"provider": "web", "url": WEB_URL}),
        "policy": over.pop("policy", "REFERENCE"),
        "body_authority": over.pop("body_authority", WEB_URL),
        "audience": over.pop("audience", "本人"),
        "why": over.pop("why", "外部参考：与现有 Skill 指引比较"),
        "decided_by_user": over.pop("decided_by_user", "2026-09-23"),
        "publishable": over.pop("publishable", False),
        "reference": over.pop("reference", {
            "canonical_url": WEB_URL, "permitted_scope": "身份字段 + 自撰摘要",
            "save": "SNAPSHOT"}),
    }
    entry.update(over)
    return entry


class TestAPageCapturedFromOutsideThisWorkspace:
    """§5's web source, end to end through this module: a declaration written in
    `information/sources/` is consumed by the capture object it names.

    This is the half `information/README.md` §8 makes the test of a valid
    registration — 来源声明未消费不能算登记有效 — so what is asserted is that
    the join HAPPENS and that it happens on the address, not on the title.
    """

    def test_a_declaration_about_a_page_is_consumed(self, tmp_path):
        records = tree(tmp_path)
        a_web_page(records)
        declared(records, {"entries": [a_reference()]})
        row = row_for(resolved(records), f"web:doc:{WEB_URL}")
        assert row["state"] == "IN_FORCE"
        assert row["decided_by"] == "DECLARATION"
        assert row["policies"] == ["REFERENCE"]
        assert row["shape"] == "web_capture"
        assert row["role"] == "web"
        assert row["coverage_owner"] == (
            "intake/capture_coverage.py — body, response, materials")
        assert "外部参考" in row["rules"][0]["why"]

    def test_the_key_is_the_address_and_never_the_title(self, tmp_path):
        """Same title, another address: two objects. Same address, another
        title: one object. A title is a label that is unique until it is not."""
        records = tree(tmp_path)
        a_web_page(records, title="Rethinking skills and prompts")
        declared(records, {"entries": [a_reference(
            identity={"provider": "web", "url": "https://example.invalid/other"},
            recorded_as=["Rethinking skills and prompts"])]})
        document = resolved(records)
        row = row_for(document, f"web:doc:{WEB_URL}")
        assert row["state"] == "UNDECLARED", (
            "a declaration naming another address, and carrying this object's "
            "title as `recorded_as`, must not reach it")
        assert "not a default for pages" in " ".join(row["why"])
        unresolved = [r for r in document["rows"] if r["state"] == "UNRESOLVED"]
        assert [r["object"] for r in unresolved] == [
            "web:doc:https://example.invalid/other"]

    def test_a_capture_that_was_redirected_is_keyed_by_the_address_asked_for(
            self, tmp_path):
        """A declaration names what to read. Matching it against wherever the
        server sent the reader would accept a declaration for an address this
        capture does not name."""
        records = tree(tmp_path)
        a_web_page(records, url="https://example.invalid/a",
                   final_url="https://example.invalid/b", redirected=True)
        declared(records, {"entries": [a_reference(
            identity={"provider": "web", "url": "https://example.invalid/b"},
            reference={"canonical_url": "https://example.invalid/b",
                       "permitted_scope": "身份字段", "save": "SNAPSHOT"})]})
        document = resolved(records)
        row = row_for(document, "web:doc:https://example.invalid/a")
        assert row["identity"]["url_final"] == "https://example.invalid/b"
        assert row["state"] == "UNDECLARED"

    def test_a_declaration_for_another_provider_is_not_every_chats_near_miss(
            self, tmp_path):
        """A near miss is a declaration that matched on the KEY.

        Read the other way, one web declaration reports 66 feishu objects as
        near misses, and a list that long is one nobody reads the real entry
        in. `SUMMARY_PRODUCED` publishes the narrower definition; this asserts
        the reader holds to it.
        """
        records = tree(tmp_path)
        a_chat(records, slug="a", chat_id="oc_a")
        a_chat(records, slug="b", chat_id="oc_b")
        a_web_page(records)
        declared(records, {"entries": [a_reference()]})
        assert resolved(records)["summary"]["near_misses"] == []

    def test_the_same_key_under_another_provider_is_still_a_near_miss(
            self, tmp_path):
        """The narrow rule must not become no rule: one URL declared under two
        providers is a declaration a person has to see refused."""
        records = tree(tmp_path)
        a_document(records, url="https://example.invalid/shared")
        declared(records, {"entries": [a_reference(
            identity={"provider": "web", "url": "https://example.invalid/shared"},
            reference={"canonical_url": "https://example.invalid/shared",
                       "permitted_scope": "身份字段", "save": "LINK_ONLY"})]})
        near = resolved(records)["summary"]["near_misses"]
        assert [n["why"] for n in near] == [
            "declared provider `web` and the manifest's is `feishu`, on the "
            "same key"]

    def test_a_page_with_no_address_has_no_key_and_is_told_apart(self, tmp_path):
        """It is reported, not keyed: a directory name is not an identity."""
        records = tree(tmp_path)
        a_web_page(records, url=None)
        row = next(r for r in resolved(records)["rows"]
                   if r["shape"] == "web_capture")
        assert row["identity"]["key"] is None
        assert row["state"] == "UNDECLARED"
        assert "source.requested_url" in " ".join(row["why"])

    def test_the_shapes_this_module_parses_are_the_readable_shapes(self):
        """One set, asked in two places. Measured 2026-09-23: adding a shape to
        `capture_coverage` and not here reported a fully parsed object as
        `UNREADABLE` while its own identity sat in the row beside it.

        Asserted as an equality and not a containment, because the failure it
        catches is a shape nobody added — a new readable shape has to be added
        here, and the error message says so.
        """
        readable = {shape for shape in capture.SHAPES
                    if shape not in ("UNREADABLE", "UNKNOWN_SHAPE")}
        assert readable == set(policy.PARSED_SHAPES), (
            "every shape `capture_coverage` can read a manifest for is a shape "
            "this module has to parse; the two lists move together")


# ---------------------------------------------------------------------------
# 6. The live tree
# ---------------------------------------------------------------------------


class TestTheLiveTree:
    """Invariants that hold whatever the records checkout holds.

    Deliberately not "the tree has N objects" or "this link is FOLLOW": those
    are facts about a tree that people are still capturing into, and a suite
    that fails when somebody captures a chat teaches its readers to ignore it.
    What is asserted is what the reader must be true of ANY tree.
    """

    def test_it_resolves_at_all_and_every_row_is_one_of_the_states(self, document):
        assert document["schema"] == policy.SCHEMA
        assert document["summary"]["rows"] == len(document["rows"])
        assert sum(document["summary"]["by_state"].values()) == len(document["rows"])
        assert set(document["summary"]["by_state"]) <= set(policy.ROW_STATES)

    def test_it_emits_a_row_for_every_capture_object_and_never_fewer(self, document):
        assert document["summary"]["rows"] >= document["summary"]["objects"]
        assert document["summary"]["rows"] == len(document["rows"])

    def test_no_key_is_a_display_name(self, document):
        for row in document["rows"]:
            key = (row["identity"] or {}).get("key")
            if key and row["state"] != "UNRESOLVED":
                assert KEY_SHAPE.match(key), (
                    f"{row['object']} is keyed by `{key}`, which is not one of "
                    f"the three provider id spaces")

    def test_every_rule_names_its_level_and_is_reachable_from_the_document(
            self, document):
        for row in document["rows"]:
            for rule in row["rules"]:
                assert rule["level"] == policy.LEVEL_OF[rule["decided_by"]]
                assert rule["decided_by"] in policy.DECIDED_BY
                assert set(rule["labels"]) <= set(policy.POLICIES)

    def test_a_watchlist_line_is_either_applied_or_reported(self, document):
        """A line whose slug matches two objects is applied to neither and
        reported; a line that matches none is reported. Either way it appears
        somewhere, because a rule that vanishes is a rule nobody can fix."""
        keys = set(document["legacy"]["watchlist"]["keys"])
        unresolved = {row["object"] for row in document["rows"]
                      if row["state"] == "UNRESOLVED"}
        applied = {rule["evidence"].get("key")
                   for row in document["rows"] for rule in row["rules"]
                   if rule["decided_by"] == "LEGACY_WATCHLIST"}
        for key in keys:
            assert key in unresolved or key in applied

    def test_a_registered_exclusion_and_a_capture_are_never_both_silently_right(
            self, document):
        """The contradiction is reported, and the deny is in force on the row:
        the two records about one chat cannot both be right, and which one is
        wrong is not the reader's to decide."""
        for item in document["contradictions"]:
            assert item["kind"] == "DENIED_BUT_CAPTURED"
            row = row_for(document, item["key"])
            assert row["policies"] == ["NONE"]
            assert row["state"] != "UNDECLARED"

    def test_no_declaration_is_silently_dropped(self, document):
        """Every entry the reader could not key is counted, and each one says
        why. The count is the reason a policy report's denominator cannot shrink
        unnoticed: an entry that vanishes from both the rows and the count is a
        source nobody can tell is missing."""
        summary = document["summary"]["unusable_declarations"]
        assert summary["count"] == len(summary["items"])
        for item in summary["items"]:
            assert item["why"]
        refused = {item["why"] for item in document["declarations"]["unusable"]}
        counted = {item["why"] for item in summary["items"]}
        assert refused <= counted, (
            "an entry the declaration reader refused must be in the count")

    def test_the_publish_plane_registries_are_reported_and_not_merged(self, document):
        """§8 keeps the two 判据 apart, so the registries are read, reported,
        and never become a policy input: no row was decided by one."""
        assert set(document["planes"]) == {"heavy_media", "publish_excluded",
                                          "credential_registry"}
        for name, plane in document["planes"].items():
            assert plane["path"]
            assert plane["why_not_a_policy_input"]
        assert not set(document["planes"]) & set(policy.DECIDED_BY)
        assert set(document["summary"]["by_decided_by"]) <= set(policy.DECIDED_BY)

    def test_the_verb_runs_against_the_real_tree(self, tmp_path):
        if not LIVE_RECORDS.is_dir():
            pytest.skip("this node has no records checkout to walk")
        run = cli("work", "source-policy", "--records-root", str(LIVE_RECORDS),
                  "--coverage", "--json", env_extra=data_root(tmp_path))
        assert run.returncode == 0, run.stderr
        document = json.loads(run.stdout)
        followed = document["followed_sources"]
        assert followed["state"] == "READ"
        assert followed["coverage"]["authored"] == len(followed["rows"])
        assert followed["coverage"]["complete"] <= followed["coverage"]["authored"]
        assert len(followed["rows"]) <= document["summary"]["rows"]
