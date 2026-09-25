"""P4 — 适配器这一层的几条不能靠记性维持的规矩。

这一层是**唯一**知道 `lark-cli` 是什么的地方，所以这里钉的不是「调用对不对」，是四件
会在真机上悄悄错掉的事：

- CLI 的 `ok` 信封要看，不能只看退出码 —— 一次调用可以退出 0 而仍然失败，而只读退出码
  正是「文档没被碰过」变成「文档更新了」的方式；
- 只有带 id 的元素才算块：没有 id 的元素没有名字去 patch 它，给它编一个位置名，就会在
  别人插入一段之后打到错的段落上；
- 认不出的标签**保留它自己的名字**而不是被压平 —— 压平之后，下游的「不认识的块要拒绝」
  就永远拒绝不了任何东西；
- 取节正文时，**有正文走整段替换、没有正文才插在标题之后**。选错不是差一点：对空范围做
  `block_replace` 会把标题删掉，而下一次 patch 正是要找那个标题。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_" + name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_runtime_" + name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("document_transport", "runtime/document_transport.py")

URL = "https://example.invalid/docx/abc"


# ────────────────────────────────────────────────────────────────────────────
# 块模型
# ────────────────────────────────────────────────────────────────────────────

class TestTheBlockModel:
    def test_a_block_is_only_something_with_an_id(self):
        blocks = transport.parse_blocks(
            '<h1 id="h">标题</h1><p>没有 id 的一段</p><p id="p1">有 id</p>')
        assert [block["id"] for block in blocks] == ["h", "p1"]

    def test_an_unmodelled_tag_keeps_its_own_name(self):
        # 认不出就压平，等于下游的「不认识就拒绝」永远不触发。
        blocks = transport.parse_blocks('<figure id="f"><img id="i"/></figure>')
        assert [block["type"] for block in blocks] == ["figure", "image"]

    def test_inline_markup_does_not_become_blocks(self):
        blocks = transport.parse_blocks('<p id="p1">a<b>粗</b>c</p>')
        assert len(blocks) == 1
        assert blocks[0]["text"] == "a粗c"

    def test_the_block_order_is_the_document_order(self):
        blocks = transport.parse_blocks(
            '<title id="t">T</title><h1 id="a">一</h1><p id="b">正文</p>')
        assert [block["id"] for block in blocks] == ["t", "a", "b"]


# ────────────────────────────────────────────────────────────────────────────
# 信封、受众
# ────────────────────────────────────────────────────────────────────────────

class TestWhatTheAdapterWillNotBelieve:
    def test_the_clis_own_error_envelope_is_honoured(self, monkeypatch):
        monkeypatch.setattr(transport, "run", transport.run)

        class Done:
            returncode = 0
            stdout = '{"ok": false, "error": {"message": "nope"}}'
            stderr = ""

        monkeypatch.setattr(transport.subprocess, "run", lambda *a, **k: Done())
        with pytest.raises(transport.TransportFault) as raised:
            transport.run(["lark-cli", "docs"])
        assert "nope" in str(raised.value)

    def test_an_answer_that_is_not_json_is_a_fault_not_a_pass(self, monkeypatch):
        class Done:
            returncode = 0
            stdout = "not json at all"
            stderr = ""

        monkeypatch.setattr(transport.subprocess, "run", lambda *a, **k: Done())
        with pytest.raises(transport.TransportFault):
            transport.run(["lark-cli", "docs"])

    def test_a_missing_cli_is_a_sentence_not_a_traceback(self, monkeypatch):
        def missing(*a, **k):
            raise FileNotFoundError("lark-cli")

        monkeypatch.setattr(transport.subprocess, "run", missing)
        with pytest.raises(transport.TransportFault) as raised:
            transport.run(["lark-cli", "docs"])
        assert "not on this host" in str(raised.value)

    def test_an_audience_that_could_not_be_read_is_none_and_not_empty(self, monkeypatch):
        def refuse(argv, **kwargs):
            raise transport.TransportFault("no")

        monkeypatch.setattr(transport, "run", refuse)
        assert transport.audience(URL) is None

    def test_an_audience_with_no_member_list_is_none_and_not_empty(self, monkeypatch):
        monkeypatch.setattr(transport, "run", lambda argv, **k: {"ok": True, "data": {}})
        assert transport.audience(URL) is None

    def test_an_empty_member_list_is_an_empty_list(self, monkeypatch):
        # 与上一条相反的方向：真的读了、真的是空的。两句不同的话。
        monkeypatch.setattr(transport, "run",
                            lambda argv, **k: {"ok": True, "data": {"items": []}})
        assert transport.audience(URL) == []

    def test_a_member_is_reproduced_as_name_and_permission(self, monkeypatch):
        monkeypatch.setattr(transport, "run", lambda argv, **k: {
            "ok": True, "data": {"items": [
                {"name": "有人", "perm": "full_access", "member_id": "ou_should_not_travel"},
                {"member_id": "ou_also_should_not_travel", "perm": "view"}]}})
        seen = transport.audience(URL)
        assert len(seen) == 2
        assert "有人:full_access" in seen
        assert not any("ou_" in entry for entry in seen)

    def test_the_same_audience_read_twice_compares_equal(self, monkeypatch):
        monkeypatch.setattr(transport, "run", lambda argv, **k: {
            "ok": True, "data": {"items": [{"name": "a", "perm": "view"},
                                           {"name": "b", "perm": "edit"}]}})
        assert transport.audience(URL) == transport.audience(URL)


# ────────────────────────────────────────────────────────────────────────────
# 写入的两个形状
# ────────────────────────────────────────────────────────────────────────────

class TestTheTwoWriteShapes:
    def _record(self, monkeypatch):
        calls = []

        def note(argv, **kwargs):
            calls.append((argv, kwargs))
            return {"ok": True, "data": {"revision_id": 11}}

        monkeypatch.setattr(transport, "run", note)
        return calls

    def test_a_section_with_a_body_is_replaced_as_a_range(self, monkeypatch):
        calls = self._record(monkeypatch)
        transport.body_writer(URL)(URL, {"heading_id": "h",
                                         "body_ids": ["a", "b"]}, "<p>x</p>", 10)
        argv = calls[0][0]
        assert "block_replace" in argv
        assert argv[argv.index("--start-block-id") + 1] == "a"
        assert argv[argv.index("--end-block-id") + 1] == "b"

    def test_a_section_with_no_body_inserts_after_the_heading(self, monkeypatch):
        calls = self._record(monkeypatch)
        transport.body_writer(URL)(URL, {"heading_id": "h", "body_ids": []},
                                   "<p>x</p>", 10)
        argv = calls[0][0]
        assert "block_insert_after" in argv
        assert argv[argv.index("--block-id") + 1] == "h"
        assert "--start-block-id" not in argv

    def test_the_heading_is_never_the_range_being_replaced(self, monkeypatch):
        # 第一次真写就是在这里把锚点吃掉的：范围含标题、内容里没有标题，
        # 于是下一轮找不到标题。这条判据守的就是「标题不在被替换的范围里」。
        calls = self._record(monkeypatch)
        transport.body_writer(URL)(URL, {"heading_id": "h", "body_ids": ["a"]},
                                   "<p>x</p>", 10)
        argv = calls[0][0]
        assert argv[argv.index("--start-block-id") + 1] != "h"
        assert argv[argv.index("--end-block-id") + 1] != "h"

    def test_the_revision_travels_with_the_write(self, monkeypatch):
        # 本地锁不等于全局安全：唯一那条非本地的并发控制就是把这个数带上。
        calls = self._record(monkeypatch)
        transport.body_writer(URL)(URL, {"heading_id": "h", "body_ids": ["a"]},
                                   "<p>x</p>", 1234)
        argv = calls[0][0]
        assert argv[argv.index("--revision-id") + 1] == "1234"

    def test_a_write_with_no_heading_to_anchor_to_is_refused(self, monkeypatch):
        self._record(monkeypatch)
        with pytest.raises(transport.TransportFault):
            transport.body_writer(URL)(URL, {"body_ids": ["a"]}, "<p>x</p>", 10)


class TestTheMediaWrite:
    def test_a_missing_source_is_a_fault_before_anything_is_sent(self, monkeypatch):
        sent = []
        monkeypatch.setattr(transport, "run",
                            lambda argv, **k: sent.append(argv) or {"ok": True})
        with pytest.raises(transport.TransportFault) as raised:
            transport.media_writer(URL)(URL, {"media": [{"path": "/nope/x.png"}]})
        assert "media source not found" in str(raised.value)
        assert sent == []

    def test_the_cli_runs_from_the_files_own_directory(self, monkeypatch, tmp_path):
        # CLI 只肯读工作目录 / `/tmp` / `~/files` 下的路径，而本机策略把临时文件放在
        # artifacts 下、也不建 `~/files`。不切工作目录，媒体这条边永远发不出去。
        seen = {}
        source = tmp_path / "tiny.png"
        source.write_bytes(b"\x89PNG")

        def note(argv, **kwargs):
            seen.update(kwargs)
            return {"ok": True, "data": {"revision_id": 12}}

        monkeypatch.setattr(transport, "run", note)
        transport.media_writer(URL)(URL, {"media": [{"path": str(source)}]})
        assert seen.get("cwd") == str(tmp_path)

    def test_media_that_is_not_asked_for_is_not_supplied(self):
        assert "media" not in transport.transport(URL)["writers"]
        assert "media" in transport.transport(URL, with_media=True)["writers"]
