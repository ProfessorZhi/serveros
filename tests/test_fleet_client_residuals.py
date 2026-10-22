"""Deterministic residual boundary tests; synthetic peers, no client/model/device."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
sys.path.insert(0, str(HERE))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = load("residual_fix_cc", "run_cc_client_e2e.py")
codex = load("residual_fix_codex", "run_codex_client_e2e.py")
framing = load("residual_fix_framing", "stdio_framing.py")
interposer = load("residual_fix_interposer", "stdio_interposer.py")


def test_lost_output_acquisition_preserves_foreign_receipt(tmp_path, monkeypatch):
    out = tmp_path / "raced"
    prepare = cc.Runner.prepare

    def race(self):
        out.mkdir()
        (out / "RECEIPT.json").write_text("foreign-sentinel")
        return prepare(self)

    monkeypatch.setattr(cc.Runner, "prepare", race)
    monkeypatch.setattr(cc.Runner, "_git_head", lambda self: "synthetic")
    assert cc.main(["--out", str(out)]) == 2
    assert (out / "RECEIPT.json").read_text() == "foreign-sentinel"
    assert list(out.iterdir()) == [out / "RECEIPT.json"]


def test_receipt_requires_retained_output_identity_and_is_exclusive(tmp_path, monkeypatch):
    out = tmp_path / "evidence"
    runner = cc.Runner(out, False)
    monkeypatch.setattr(runner, "_git_head", lambda: "synthetic")
    runner._acquire_output()
    try:
        (out / "RECEIPT.json").write_text("foreign-file")
        with pytest.raises(FileExistsError):
            runner.write_receipt()
        assert (out / "RECEIPT.json").read_text() == "foreign-file"
        assert not (out / "RECEIPT.md").exists()
        moved = tmp_path / "original"
        out.rename(moved)
        out.mkdir()
        (out / "RECEIPT.json").write_text("replacement")
        assert runner.write_receipt() is False
        assert (out / "RECEIPT.json").read_text() == "replacement"
    finally:
        runner.close_output()


def test_owned_output_can_receive_a_receipt_without_a_client(tmp_path, monkeypatch):
    runner = cc.Runner(tmp_path / "owned", False)
    monkeypatch.setattr(runner, "_git_head", lambda: "synthetic")
    runner._acquire_output()
    try:
        assert runner.write_receipt() is True
        assert json.loads((runner.out / "RECEIPT.json").read_text())["git_head"] == "synthetic"
    finally:
        runner.close_output()


def wire():
    return [
        {"connection_id": "one", "direction": "cc->owned", "method": "initialize", "id": 1},
        {"connection_id": "one", "direction": "owned->cc", "id": 1,
         "request_method": "initialize", "response_ok": True},
        {"connection_id": "one", "direction": "cc->owned", "method": "notifications/initialized"},
        {"connection_id": "one", "direction": "cc->owned", "method": "tools/list", "id": 2},
        {"connection_id": "one", "direction": "owned->cc", "id": 2,
         "request_method": "tools/list", "response_ok": True, "tool_count": 34},
    ]


def observe(tmp_path, rows):
    runner = cc.Runner(tmp_path, False, owned_entry="/synthetic/entry", owned_config="/synthetic/config")
    runner.logs.mkdir()
    runner.interposer_log = runner.logs / "wire.jsonl"
    runner.interposer_log.write_text("".join(json.dumps(row) + "\n" for row in rows))
    runner.run = lambda *a, **k: subprocess.CompletedProcess([], 0, "Connected " + runner.qualified_server, "")
    return runner, runner.mcp_list()


@pytest.mark.parametrize("corruption", ["error", "wrong-id", "wrong-connection", "list-wrong-id", "list-error", "observer-failed"])
def test_cc_requires_successful_correlated_ack_and_list(tmp_path, corruption):
    rows = wire()
    if corruption == "error":
        rows[1].update(response_ok=False, error="present", error_code=-32603)
    elif corruption == "wrong-id":
        rows[1]["id"] = 99
    elif corruption == "wrong-connection":
        rows[1]["connection_id"] = "other"
    elif corruption == "list-wrong-id":
        rows[4]["id"] = 99
    elif corruption == "list-error":
        rows[4].update(response_ok=False, error="present", error_code=-32603)
    else:
        rows.append({"connection_id": "one", "event": "observation_failed", "reason": "frame budget"})
    runner, ok = observe(tmp_path, rows)
    assert not ok
    assert runner.results["TOOLS_LIST_OK"]["status"] != "PASS"
    if corruption in {"error", "wrong-id", "wrong-connection", "observer-failed"}:
        assert runner.results["MCP_INITIALIZE_OK"]["status"] != "PASS"


def test_cc_accepts_actual_correlated_result_and_list(tmp_path):
    runner, ok = observe(tmp_path, wire())
    assert ok
    assert runner.results["MCP_INITIALIZE_OK"]["status"] == "PASS"
    assert runner.results["TOOLS_LIST_OK"]["evidence"]["tool_count"] == 34


@pytest.mark.parametrize("kwargs,payload", [
    ({"max_queue_frames": 4, "max_queue_bytes": 1024}, b"{}\n" * 20),
    ({"max_queue_frames": 20, "max_queue_bytes": 8}, b"12345\n67890\n"),
])
def test_framer_total_frame_and_byte_budgets_are_terminal(kwargs, payload):
    readfd, writefd = os.pipe()
    stream = os.fdopen(readfd, "rb", buffering=0)
    try:
        reader = framing.LineFramer(stream, max_line_bytes=32, **kwargs)
        os.write(writefd, payload)
        os.close(writefd)
        writefd = -1
        reader._thread.join(timeout=2)
        assert not reader._thread.is_alive()
        with pytest.raises(framing.FramingError):
            reader.next_line(1)
        assert reader._queue.qsize() <= kwargs["max_queue_frames"]
        assert sum(map(len, reader._queue.queue)) <= kwargs["max_queue_bytes"]
    finally:
        if writefd >= 0:
            os.close(writefd)
        stream.close()


@pytest.mark.parametrize("count_limit,byte_limit", [(2, 1000), (100, 20)])
def test_codex_notifications_have_a_lifetime_budget(count_limit, byte_limit):
    server = object.__new__(codex.CodexServer)
    server.timeout = 1
    server.notifications = []
    server.notification_bytes = 0
    server.max_notifications = count_limit
    server.max_notification_bytes = byte_limit
    server.send = lambda message: None
    messages = iter([b'{"method":"n"}'] * 3 + [b'{"id":1,"result":{}}'])
    server.reader = type("Reader", (), {"next_line": lambda self, timeout: next(messages)})()
    with pytest.raises(Exception, match="notification.*budget"):
        server.request(1, "initialize", {})
    assert len(server.notifications) <= count_limit
    assert server.notification_bytes <= byte_limit


def test_interposer_write_all_handles_interruptions_and_short_writes(monkeypatch):
    output = bytearray()
    first = True

    def short_write(fd, data):
        nonlocal first
        if first:
            first = False
            raise InterruptedError()
        count = min(2, len(data))
        output.extend(data[:count])
        return count

    monkeypatch.setattr(interposer.os, "write", short_write)
    interposer.write_all_fd(12345, b"exact-wire-bytes")
    assert output == b"exact-wire-bytes"


def test_interposer_unterminated_line_fails_with_bounded_observation(tmp_path):
    log = tmp_path / "wire.jsonl"
    state = interposer.Observation(log, max_line_bytes=16)
    readfd, writefd = os.pipe()
    sink_path = tmp_path / "forwarded"
    os.write(writefd, b"x" * 32)
    os.close(writefd)
    with os.fdopen(readfd, "rb", buffering=0) as source, sink_path.open("wb", buffering=0) as sink:
        interposer._pump(source, sink, state, "owned->cc")
    assert state.failed.is_set()
    assert state.buffer_peak <= 16 + 65536
    assert (b"x" * 32).startswith(sink_path.read_bytes())  # explicit fail-close
    assert any(json.loads(line).get("event") == "observation_failed" for line in log.read_text().splitlines())


def pump_bytes(tmp_path, payload, **limits):
    state = interposer.Observation(tmp_path / "bounded-wire.jsonl", **limits)
    readfd, writefd = os.pipe()
    os.write(writefd, payload)
    os.close(writefd)
    with os.fdopen(readfd, "rb", buffering=0) as source, (tmp_path / "sink").open("wb", buffering=0) as sink:
        interposer._pump(source, sink, state, "cc->owned")
    return state


def test_valid_wire_is_forwarded_byte_for_byte_and_body_is_not_logged(tmp_path):
    payload = b'  { "jsonrpc":"2.0", "method":"note", "params":{"private":"UNLOGGED_CANARY"} }\r\n\n'
    state = pump_bytes(tmp_path, payload)
    try:
        assert not state.failed.is_set()
        assert (tmp_path / "sink").read_bytes() == payload
        assert "UNLOGGED_CANARY" not in (tmp_path / "bounded-wire.jsonl").read_text()
    finally:
        state.close()


@pytest.mark.parametrize("limits,payload,reason", [
    ({"max_frames": 4}, b'{"method":"n"}\n' * 8, "frame budget"),
    ({"max_pending": 2}, b'{"id":1,"method":"r"}\n{"id":2,"method":"r"}\n{"id":3,"method":"r"}\n', "pending request"),
    ({"max_metadata_bytes": 1024}, b'{"method":"n"}\n' * 8, "metadata budget"),
    ({"max_wire_bytes": 16}, b'x' * 32, "wire byte budget"),
])
def test_interposer_total_observation_budgets_fail_explicitly(tmp_path, limits, payload, reason):
    state = pump_bytes(tmp_path, payload, **limits)
    try:
        assert state.failed.is_set()
        records = [json.loads(line) for line in (tmp_path / "bounded-wire.jsonl").read_text().splitlines()]
        assert any(row.get("event") == "observation_failed" and reason in row["reason"] for row in records)
        assert len(state.pending) <= state.max_pending
        assert (tmp_path / "bounded-wire.jsonl").stat().st_size <= state.max_metadata_bytes
    finally:
        state.close()
