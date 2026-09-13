#!/usr/bin/env python3
"""The Codex app-server, as an AgentSessionProvider.

The protocol these tests hold the adapter to was not recalled: it was read off
the installed `codex-cli 0.153.4` — its own generated JSON Schema, then four
live probes against a running app-server — because a request shape written from
memory is a guess that reads exactly like a fact. `tests/fake_codex_app_server.py`
speaks that measured wire format from a real subprocess, so what is tested is
the adapter's handling of a provider, not its agreement with itself.

Three separations are what most of this file is about, and each one has a way of
collapsing that looks reasonable while doing it:

    ServerOS Agent Session  ≠  codex thread  ≠  codex turn  ≠  OS process
    support  ≠  readiness  ≠  admission
    "we interrupted it"  ≠  "it failed"  ≠  "it completed"

And one that is a policy rather than a distinction: this adapter never grants an
approval. See `test_an_approval_request_is_declined_and_the_decline_is_recorded`.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MCP = REPO / "mcp"
RUNTIME = REPO / "runtime"
ABILITIES = REPO / "abilities"
TESTS = REPO / "tests"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codex_mod = _load("serveros_codex_provider", MCP / "codex_app_server_provider.py")
botmux_mod = _load("serveros_botmux_provider", MCP / "botmux_provider.py")
semantics = _load("serveros_runtime_agent_sessions", RUNTIME / "agent_sessions.py")
service_mod = _load("serveros_agent_session_service", MCP / "agent_session_service.py")

CodexAppServerProvider = codex_mod.CodexAppServerProvider
CodexConfig = codex_mod.CodexConfig
CodexUnavailable = codex_mod.CodexUnavailable
CodexUnsupported = codex_mod.CodexUnsupported
CodexNoActiveTurn = codex_mod.CodexNoActiveTurn
CodexProtocolError = codex_mod.CodexProtocolError
MEASURED_AGAINST = codex_mod.MEASURED_AGAINST
session_ref = semantics.session_ref
SessionService = service_mod.SessionService

DEVICE = "device:server-beijing"
PROVIDER = "provider:codex-app-server"
FAKE = TESTS / "fake_codex_app_server.py"


# ---------------------------------------------------------------------------
# Harness: a real subprocess speaking the real wire format
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_server(tmp_path):
    """A `codex` on PATH that is this repository's fake app-server.

    A shell wrapper rather than the script itself, because the adapter spawns
    `[binary, "app-server", "--listen", "stdio://"]` and the fake checks that
    argv. The wrapper makes the executable bit a test-fixture concern instead of
    a git-metadata one, where a mode change is easy to lose and hard to notice.
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    wrapper = tmp_path / "codex"
    wrapper.write_text(f'#!/bin/sh\nexec {sys.executable} "{FAKE}" "$@"\n')
    wrapper.chmod(0o755)

    # The fake reads FAKE_CODEX_HOME from the environment of the spawned
    # process, which the adapter inherits from this one.
    os.environ["FAKE_CODEX_HOME"] = str(home)
    built = []

    def factory(*, ephemeral: bool = False, cwd: str | None = None):
        """A provider wired to the fake. Every one built here is closed at the end."""
        provider = CodexAppServerProvider(CodexConfig(
            binary=str(wrapper), timeout=15, turn_timeout=30,
            ephemeral=ephemeral, cwd=cwd or str(tmp_path),
        ))
        built.append(provider)
        return provider

    # `home` is carried on the factory rather than returned beside it: the
    # tests that reach into the fake's store need the path, and every other test
    # would have to unpack a tuple to ignore it.
    factory.home = home
    yield factory
    for provider in built:
        provider.close()


def started(fake_server, **kwargs):
    provider = fake_server(**kwargs)
    provider.process().start()
    return provider


def settle(provider, turn, *, timeout=30.0):
    """Wait for THIS turn to report a terminal state, not for some turn to.

    Filtered by turn id on purpose: "a turn completed" and "the turn I started
    completed" are different claims, and only the second one is what a test that
    asserts on turn outcomes means.
    """
    provider.process().wait_for("turn/completed", timeout=timeout,
                                turn_id=turn.get("turn_id"))
    return True


# ---------------------------------------------------------------------------
# Identity: what may and may not become a ref
# ---------------------------------------------------------------------------

def test_the_provider_names_the_canonical_runtime_and_not_a_cli_or_a_path():
    """§4's rule, applied to the provider that had the most ways to break it.

    The temptations here are concrete and each one was available: `codex` is a
    binary at a path, the app-server is a process with a pid, and the handshake
    returns a `userAgent` string. All three are evidence; none is identity. The
    runtime ref is the canonical one the session contract's `runtime_ref`
    pattern produces and the BotMux adapter already maps to — the same id for
    the same runtime, not a second spelling of it.
    """
    provider = CodexAppServerProvider()
    assert provider.provider_id == PROVIDER
    # The provider id is the contract's `provider:<kebab-id>` and nothing else.
    assert PROVIDER.startswith("provider:")
    assert PROVIDER.removeprefix("provider:").replace("-", "").isalnum()

    assert codex_mod.RUNTIME_REF == "agent-runtime:codex"
    assert codex_mod.RUNTIME_REF == botmux_mod.BotMuxProvider._runtime_ref("codex")

    # Nothing machine-local, and nothing that moves when the process does.
    for forbidden in ("/", "0.153", "pid:", "server-beijing"):
        assert forbidden not in codex_mod.RUNTIME_REF
        assert forbidden not in PROVIDER


def test_the_session_ref_carries_the_thread_id_and_not_the_process(fake_server):
    """The whole point of `thread/resume`: the session outlives its server.

    A ref minted from a pid would be a ref that stops working the moment the
    process does, which is the opposite of what a session is for.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hello", idempotency_key="k1")
    settle(provider, created)

    thread_id = created["provider_session_id"]
    assert thread_id and thread_id not in (None, "")
    assert created["session_ref"] == session_ref(DEVICE, PROVIDER, thread_id)
    assert str(os.getpid()) not in created["session_ref"]
    # And it parses back to exactly three parts, none of them a process.
    assert semantics.parse_session_ref(created["session_ref"]) == (
        DEVICE, PROVIDER, thread_id)


def test_a_session_outlives_the_process_that_created_it(fake_server):
    """Restart the adapter; the session is still there and still the same one.

    This is the test that would fail for an adapter that stored its session
    table in memory and called it the truth. The thread lives on the provider's
    side of the boundary, and `thread/list` is how you find out.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="first", idempotency_key="k1")
    settle(provider, created)
    provider.close()

    second = fake_server()
    rows = second.list_sessions(device_ref=DEVICE)
    refs = [row["session_ref"] for row in rows]
    assert created["session_ref"] in refs

    resumed = second.resume(provider_session_id=created["provider_session_id"],
                            device_ref=DEVICE)
    assert resumed["session_ref"] == created["session_ref"]
    assert resumed["provider_session_id"] == created["provider_session_id"]


def test_the_process_carries_no_identity_and_the_ref_survives_a_new_pid(fake_server):
    """Two pids, one session. The pid difference must be invisible in the ref."""
    first = started(fake_server)
    created = first.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="a", idempotency_key="k1")
    settle(first, created)
    first_pid = first.process().pid
    first.close()

    second = fake_server()
    second.process().start()
    assert second.process().pid != first_pid
    again = second.resume(provider_session_id=created["provider_session_id"],
                          device_ref=DEVICE)
    assert again["session_ref"] == created["session_ref"]


def test_list_agents_is_one_agent_and_reports_what_was_measured():
    """One app-server per device is one Agent, not one Agent per model.

    The model name is configuration the provider reports; promoting it to an
    Agent would populate a fleet nobody enrolled.
    """
    provider = CodexAppServerProvider()
    agents = provider.list_agents(device_ref=DEVICE)
    assert len(agents) == 1
    agent = agents[0]
    assert agent["agent_id"] == codex_mod.AGENT_ID
    assert agent["agent_runtime"] == "agent-runtime:codex"
    assert agent["provider"] == PROVIDER
    assert agent["agent_ref"] == semantics.agent_ref(DEVICE, PROVIDER, codex_mod.AGENT_ID)
    assert agent["device_ref"] == DEVICE
    assert MEASURED_AGAINST in agent["measured_against"]


# ---------------------------------------------------------------------------
# Support: what the protocol can do, said per verb
# ---------------------------------------------------------------------------

def test_support_is_declared_per_verb_and_stop_is_unsupported_with_a_reason():
    actions = CodexAppServerProvider.actions()
    assert actions["session.create"] if "session.create" in actions else True
    for verb in ("session.create", "session.inspect", "session.send",
                 "session.result", "session.resume", "session.interrupt"):
        assert actions[verb]["state"] == "SUPPORTED", verb
    assert actions["session.stop"]["state"] == "UNSUPPORTED"
    reason = actions["session.stop"]["reason"]
    # The reason must name what DOES exist. "not supported" alone sends a reader
    # looking for a flag; naming archive and delete tells them why neither is one.
    assert "thread/archive" in reason and "thread/delete" in reason


def test_interrupt_is_declared_as_turn_scoped_because_that_is_what_it_is():
    """`turn/interrupt` stops a turn. A session is not a turn.

    Declaring it as a session verb without saying so is how a caller comes to
    believe a session ended when a turn did.
    """
    reason = CodexAppServerProvider.actions()["session.interrupt"]["reason"]
    assert "TURN" in reason and "does not end the session" in reason


def test_session_stop_raises_unsupported_and_not_a_success():
    provider = CodexAppServerProvider()
    with pytest.raises(CodexUnsupported) as excinfo:
        provider.stop(provider_session_id="whatever")
    assert "thread/archive" in str(excinfo.value)


def test_support_is_not_readiness_and_neither_is_admission():
    """Three questions, three answers, no substitution.

    `send` is SUPPORTED and says nothing about whether the provider is up.
    Readiness says nothing about whether this actor may act. The BotMux slice
    collapsed exactly these and reported a dead provider as healthy.
    """
    actions = CodexAppServerProvider.actions()
    assert actions["session.send"]["state"] == "SUPPORTED"

    provider = CodexAppServerProvider(CodexConfig(
        binary="/nonexistent/codex-for-the-readiness-test"))
    readiness = provider.observe(device_ref=DEVICE)
    assert readiness["status"] in ("UNAVAILABLE", "UNKNOWN")
    # Support is unchanged by the provider being down: they are different facts.
    assert provider.actions()["session.send"]["state"] == "SUPPORTED"

    admission = semantics.admission("session.create", {})
    assert admission["class"] in ("read_only", "delegation", "human_gated")
    assert "class" in admission


def test_an_ephemeral_thread_declares_the_inspect_limitation_up_front():
    """Measured, not inferred: `includeTurns` on an ephemeral thread is refused.

    Declaring it in the capability table means a caller learns it before the
    call rather than from an error in the middle of a flow.
    """
    ephemeral = CodexAppServerProvider.actions(ephemeral=True)["session.inspect"]
    assert ephemeral["state"] == "SUPPORTED"
    assert "ephemeral" in ephemeral["reason"]


# ---------------------------------------------------------------------------
# Normalization: the provider's words, carried honestly
# ---------------------------------------------------------------------------

def test_a_completed_turn_is_completed_and_scoped_to_the_turn(fake_server):
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="say pong", idempotency_key="k1")
    settle(provider, created)
    result = provider.result(provider_session_id=created["provider_session_id"])
    assert result["turn_state"] == "TURN_COMPLETED"
    assert result["turn_state_provenance"] == "FACT"
    assert "echo: say pong" in result["output"]
    assert result["scope"] == "current-turn"
    assert result["turn_id"] == created["turn_id"]


def test_a_failed_turn_does_not_become_a_failed_session(fake_server):
    """Turn lifecycle is not session lifecycle.

    The provider said the TURN failed. It said the thread is idle. Folding the
    first into the second is how a session gets written off while it is still
    perfectly usable — and this adapter's whole reason for existing is to drive
    the next turn on it.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="FAIL please", idempotency_key="k1")
    settle(provider, created)
    result = provider.result(provider_session_id=created["provider_session_id"])
    assert result["turn_state"] == "TURN_FAILED"
    assert result["error"]

    row = provider.inspect(device_ref=DEVICE,
                           provider_session_id=created["provider_session_id"])
    assert row["status"] == "IDLE"
    assert row["status"] != "FAILED"


def test_an_interrupted_turn_is_not_reported_as_completed_or_failed(fake_server):
    """A turn a person stopped is neither a success nor a breakage.

    ServerOS's turn vocabulary has no word for it, and inventing one — or
    filing it under FAILED — would make "the model failed" and "we stopped it"
    the same event. The state reads UNKNOWN and codex's own word travels beside
    it, which is the same discipline the BotMux adapter applies to `queued`.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="SLOW count to forty", idempotency_key="k1")
    # It stays inProgress; that is what gives the interrupt something to stop.
    assert created["turn_state"] == "TURN_RUNNING"

    provider.interrupt(provider_session_id=created["provider_session_id"])
    settle(provider, created)

    result = provider.result(provider_session_id=created["provider_session_id"])
    assert result["turn_state"] == "TURN_UNKNOWN"
    assert result["turn_state_provenance"] == "UNKNOWN"
    assert result["provider_state"] == "interrupted"
    assert result["provider_state"] not in ("completed", "failed")
    assert "interrupted" in result["turn_state_reason"]


def test_an_unmapped_thread_status_is_unknown_and_carried_verbatim(fake_server):
    """A word codex grows later must not be silently folded into one we know."""
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    settle(provider, created)
    thread_id = created["provider_session_id"]

    # Reach into the fake's store: this is standing in for a future codex that
    # reports a status this adapter has never seen.
    import json as _json
    store = Path(os.environ["FAKE_CODEX_HOME"]) / "threads.json"
    state = _json.loads(store.read_text())
    state["threads"][thread_id]["status"] = {"type": "rateLimited"}
    store.write_text(_json.dumps(state))

    rows = provider.list_sessions(device_ref=DEVICE)
    row = [r for r in rows if r["provider_session_id"] == thread_id][0]
    assert row["status"] == "UNKNOWN"
    assert row["status_provenance"] == "UNKNOWN"
    assert row["provider_status"] == "rateLimited"
    assert "rateLimited" in row["status_reason"]


def test_an_unmapped_turn_status_is_unknown_and_carried_verbatim(fake_server):
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    settle(provider, created)
    thread_id = created["provider_session_id"]

    import json as _json
    store = Path(os.environ["FAKE_CODEX_HOME"]) / "threads.json"
    state = _json.loads(store.read_text())
    state["turns"][thread_id][-1]["status"] = "deferred"
    store.write_text(_json.dumps(state))

    # A fresh adapter, which is the case this fallback exists for: it witnessed
    # no notifications, so it reads the provider's own stored turns instead of
    # reporting on a turn it never saw.
    reader = fake_server()
    result = reader.result(provider_session_id=thread_id)
    assert result["observation_source"] == "codex:thread/read"
    assert result["turn_state"] == "TURN_UNKNOWN"
    assert result["provider_state"] == "deferred"
    assert "deferred" in result["turn_state_reason"]


def test_a_restarted_adapter_reads_turn_outcomes_from_the_provider(fake_server):
    """The fallback is not a corner case: it is what a restart looks like.

    An adapter that answered from its own memory would report TURN_NOT_FOUND
    here, and a caller polling after a restart would conclude the turn never
    happened. The provider's stored turns are the authority, and they survive
    the process that witnessed them.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="say hi", idempotency_key="k1")
    settle(provider, created)

    reader = fake_server()
    result = reader.result(provider_session_id=created["provider_session_id"])
    assert result["observation_source"] == "codex:thread/read"
    assert result["turn_state"] == "TURN_COMPLETED"
    assert "echo: say hi" in result["output"]


def test_the_provider_status_is_kept_beside_the_normalized_one(fake_server):
    """`systemError` and any other FAILED are not the same repair.

    A record that keeps only the ServerOS word cannot tell an operator which
    one happened, and the normalized word is the one a caller acts on.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    settle(provider, created)
    thread_id = created["provider_session_id"]

    import json as _json
    store = Path(os.environ["FAKE_CODEX_HOME"]) / "threads.json"
    state = _json.loads(store.read_text())
    state["threads"][thread_id]["status"] = {"type": "systemError"}
    store.write_text(_json.dumps(state))

    row = [r for r in provider.list_sessions(device_ref=DEVICE)
           if r["provider_session_id"] == thread_id][0]
    assert row["status"] == "FAILED"
    assert row["provider_status"] == "systemError"


# ---------------------------------------------------------------------------
# The half of the protocol the client does not initiate
# ---------------------------------------------------------------------------

def test_an_approval_request_is_declined_and_the_decline_is_recorded(fake_server):
    """This adapter does not grant approvals, and says so in the record.

    Codex's human-in-the-loop surface is a server-to-client request. ServerOS
    has no verified human approval transport on this deployment (§67 measured
    the HumanControlProvider's as UNSUPPORTED), so there is no one for this
    adapter to ask. Answering `allow` would manufacture a consent no person
    gave; answering `decline` is the only answer that is not a fabrication.

    The declined request is recorded, so a turn that failed for want of an
    approval is distinguishable from one that failed for any other reason.
    """
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="ASK to run a command", idempotency_key="k1")
    settle(provider, created)

    process = provider.process()
    assert len(process.approvals_declined) == 1
    declined = process.approvals_declined[0]
    assert "requestApproval" in declined["method"]
    assert "no verified human approval transport" in declined["because"]

    result = provider.result(provider_session_id=created["provider_session_id"])
    # The fake fails the turn when its command is not approved, which is what
    # a real codex does. The important half is that nothing was approved.
    assert result["turn_state"] == "TURN_FAILED"
    assert "not approved" in (result["error"] or "")


def test_the_server_asking_the_time_is_answered(fake_server):
    """`initialize` only completes if the client answers the server's question.

    A client written from its own method list would hang here, and the hang
    would look like a slow provider. The fake asks during startup for exactly
    this reason: the handshake below cannot succeed unless the adapter reads
    and answers messages that are not responses to anything it sent.

    The `userAgent` echoes the client name back — that was the real server's
    behaviour, not the fixture's idea — so its presence is evidence that the
    `initialize` this adapter sent is the one that was answered, rather than a
    server talking to itself.
    """
    provider = started(fake_server)
    handshake = provider.process().handshake
    assert handshake["user_agent"].startswith("serveros/")
    assert "fake-fixture" in handshake["user_agent"]
    assert handshake["codex_home"]
    assert handshake["platform_family"] == "unix"
    assert handshake["pid"]
    assert handshake["measured_against"] == MEASURED_AGAINST


def test_an_unknown_method_is_a_protocol_error_carrying_the_code(fake_server):
    provider = started(fake_server)
    with pytest.raises(CodexProtocolError) as excinfo:
        provider.process().call("no/such/method", {})
    assert excinfo.value.code == -32601
    assert "no/such/method" in str(excinfo.value)


def test_an_ephemeral_thread_refuses_include_turns_and_the_adapter_reports_it(fake_server):
    """The refusal is measured behaviour, so it is tested rather than retried away.

    A thread whose turns cannot be read is a fact about the session. Silently
    dropping `includeTurns` and returning the thinner record would look like a
    complete answer to whatever asked.
    """
    provider = started(fake_server, ephemeral=True)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    row = provider.inspect(device_ref=DEVICE,
                           provider_session_id=created["provider_session_id"])
    assert row.get("inspect_limitation")
    assert "ephemeral" in row["inspect_limitation"]
    assert row["session_ref"] == created["session_ref"]


# ---------------------------------------------------------------------------
# Refusals that must not be confused with each other
# ---------------------------------------------------------------------------

def test_interrupting_a_thread_with_no_running_turn_is_a_refusal_not_a_stop(fake_server):
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="quick", idempotency_key="k1")
    settle(provider, created)

    with pytest.raises(CodexNoActiveTurn) as excinfo:
        provider.interrupt(provider_session_id=created["provider_session_id"])
    # The error has to say what it did NOT do, or a caller reads a failed
    # interrupt as a session that ended.
    assert "not a session stop" in str(excinfo.value)


def test_a_dead_server_is_a_stopped_provider_and_not_a_closed_session(fake_server, tmp_path):
    """Transport failure is not a session state. This is §8's rule where it bites.

    The tempting mistake is to report the sessions as CLOSED, because that is
    the only word available and something clearly went wrong. Nothing is known
    about those sessions; only about the transport.
    """
    dead = tmp_path / "codex-dead"
    dead.write_text('#!/bin/sh\necho "fake server refusing to start" >&2\nexit 3\n')
    dead.chmod(0o755)
    provider = CodexAppServerProvider(CodexConfig(binary=str(dead), timeout=5))
    record = provider.observe(device_ref=DEVICE)

    assert record["status"] == "UNAVAILABLE"
    control = [o for o in record["observations"]
               if o["axis"] == codex_mod.instances.AXIS_CONTROL_PLANE][0]
    assert control["state"] == "STOPPED"
    assert "code=3" in json.dumps(control)
    assert "refusing to start" in json.dumps(control)
    assert "CLOSED" not in json.dumps(record)


def test_a_missing_binary_is_unavailable_not_an_exception_out_of_observe():
    """A caller asked whether it works. "No" is an answer to that question."""
    provider = CodexAppServerProvider(CodexConfig(
        binary="definitely-not-a-real-codex-binary-xyz"))
    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "RUNTIME_UNHEALTHY"
    assert "PATH" in json.dumps(record)


def test_the_missing_binary_error_names_the_environment_variable(tmp_path):
    provider = CodexAppServerProvider(CodexConfig(
        binary="definitely-not-a-real-codex-binary-xyz"))
    with pytest.raises(CodexUnavailable) as excinfo:
        provider.process().start()
    assert "SERVEROS_CODEX_BIN" in str(excinfo.value)


def test_an_unknown_agent_id_is_refused_rather_than_nicknamed(fake_server):
    """One app-server is one Agent. A second Agent would need a second runtime."""
    provider = started(fake_server)
    with pytest.raises(CodexUnsupported) as excinfo:
        provider.create(device_ref=DEVICE, agent_id="codex-something-else",
                        instruction="hi", idempotency_key="k1")
    assert codex_mod.AGENT_ID in str(excinfo.value)


# ---------------------------------------------------------------------------
# Readiness: the second axis, measured rather than assumed
# ---------------------------------------------------------------------------

def test_a_provider_that_answers_but_has_done_nothing_reads_unknown_execution(fake_server):
    """Reachable is not ready. The control plane answered; nothing has run.

    This is the exact shape of the incident that produced the second axis: every
    probe passed and no turn ever ran. A fresh instance must be UNKNOWN on the
    execution axis, never AVAILABLE.
    """
    provider = started(fake_server)
    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNKNOWN"
    assert record["reason"] == "EXECUTION_UNVERIFIED"
    assert record["status_provenance"] == "UNKNOWN"
    assert record["persistence"] == "none"


def test_a_completed_turn_moves_the_execution_axis_and_the_status_with_it(fake_server):
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    settle(provider, created)
    provider.result(provider_session_id=created["provider_session_id"])

    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "AVAILABLE"
    execution = [o for o in record["observations"]
                 if o["axis"] == codex_mod.instances.AXIS_EXECUTION][0]
    assert execution["state"] == "OK"


def test_a_failed_turn_is_recorded_as_execution_failed_and_refuses(fake_server):
    """The one case the old vocabulary could not express: reachable and broken."""
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="FAIL now", idempotency_key="k1")
    settle(provider, created)
    result = provider.result(provider_session_id=created["provider_session_id"])
    assert result["turn_state"] == "TURN_FAILED"

    record = provider.observe(device_ref=DEVICE)
    assert record["status"] == "UNAVAILABLE"
    assert record["reason"] == "EXECUTION_FAILED"
    control = [o for o in record["observations"]
               if o["axis"] == codex_mod.instances.AXIS_CONTROL_PLANE][0]
    # The control plane is still up. Both facts, together, in one record.
    assert control["state"] == "RUNNING"


def test_readiness_is_never_written_down(fake_server):
    provider = started(fake_server)
    record = provider.observe(device_ref=DEVICE)
    assert record["persistence"] == "none"
    assert not hasattr(provider, "health_db")


# ---------------------------------------------------------------------------
# The service boundary: the adapter is driven, not exempted
# ---------------------------------------------------------------------------

def test_the_service_drives_the_codex_provider_through_its_own_preflight(fake_server):
    """End to end through `SessionService`, over a real subprocess.

    The point is that Codex arrives as one more provider in the existing
    machinery — support, readiness, admission, then the call — and not as a
    second path with its own rules.
    """
    provider = started(fake_server)
    service = SessionService(
        device_ref=DEVICE, providers=[provider],
        admission=lambda action, context: {"decision": "ALLOW",
                                           "class": "read_only",
                                           "action": action},
    )
    listing = service.agent_list()
    assert [a["agent_id"] for a in listing["agents"]] == [codex_mod.AGENT_ID]
    assert listing["complete"] is True

    created = service.create(provider_id=PROVIDER, agent_id=codex_mod.AGENT_ID,
                             instruction="hello service", idempotency_key="k1")
    settle(provider, created)
    result = service.result(session_ref_value=created["session_ref"])
    assert result["turn_state"] == "TURN_COMPLETED"

    sessions = service.session_list()
    assert created["session_ref"] in [s["session_ref"] for s in sessions["sessions"]]
    inspected = service.session_inspect(created["session_ref"])
    assert inspected["provider_session_id"] == created["provider_session_id"]


def test_two_providers_do_not_share_a_session_ref_namespace(fake_server):
    """A ref says which provider, so a listing of two is still unambiguous."""
    provider = started(fake_server)
    created = provider.create(
        device_ref=DEVICE, agent_id=codex_mod.AGENT_ID,
        instruction="hi", idempotency_key="k1")
    other = session_ref(DEVICE, "provider:botmux-agent-session",
                        created["provider_session_id"])
    assert other != created["session_ref"]
    assert semantics.parse_session_ref(other)[1] == "provider:botmux-agent-session"


# ---------------------------------------------------------------------------
# Live smoke. Gated on an environment variable, and it asserts nothing it did
# not observe: without the variable, this file proves the adapter against the
# measured protocol and says nothing about a real model.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("SERVEROS_CODEX_LIVE") != "1",
                    reason="set SERVEROS_CODEX_LIVE=1 to drive the installed codex")
def test_live_smoke_against_the_installed_codex(tmp_path):
    device = os.environ.get("SERVEROS_DEVICE_REF")
    if not device:
        pytest.skip("SERVEROS_DEVICE_REF is required: this adapter will not guess "
                    "which machine it is on")
    provider = CodexAppServerProvider(CodexConfig(
        binary=os.environ.get("SERVEROS_CODEX_BIN", "codex"),
        timeout=30, turn_timeout=300, cwd=str(tmp_path),
        sandbox="read-only", approval_policy="never",
    ))
    try:
        handshake = provider.process().start()
        # Deliberately not pinned to a version string: this asserts the
        # handshake is a real app-server handshake and records what it said,
        # so an upgrade changes a value in the report rather than a test.
        print("live userAgent:", handshake["user_agent"])
        assert handshake["user_agent"], "the app-server answered initialize with no userAgent"
        assert "serveros" in handshake["user_agent"], (
            "the userAgent should echo the clientInfo name this adapter sent")
        assert handshake["codex_home"]
        assert handshake["platform_family"]

        first = provider.create(
            device_ref=device, agent_id=codex_mod.AGENT_ID,
            instruction="Reply with exactly this token and nothing else: "
                        "SERVEROS_CODEX_PROVIDER_OK",
            idempotency_key="serveros-codex-smoke-1",
            workspace=str(tmp_path))
        provider.process().wait_for("turn/completed", timeout=300,
                                    turn_id=first["turn_id"])
        result = provider.result(provider_session_id=first["provider_session_id"])
        assert result["turn_state"] == "TURN_COMPLETED", result
        assert "SERVEROS_CODEX_PROVIDER_OK" in (result["output"] or "")

        second = provider.send(
            device_ref=device, provider_session_id=first["provider_session_id"],
            instruction="Reply with exactly this token and nothing else: "
                        "SERVEROS_CODEX_PROVIDER_FOLLOWUP_OK",
            turn_idempotency_key="serveros-codex-smoke-2")
        provider.process().wait_for("turn/completed", timeout=300,
                                    turn_id=second["turn_id"])
        followup = provider.result(provider_session_id=first["provider_session_id"])
        assert followup["turn_state"] == "TURN_COMPLETED", followup
        assert "SERVEROS_CODEX_PROVIDER_FOLLOWUP_OK" in (followup["output"] or "")

        # Same ServerOS session, same provider session, two different turns.
        assert second["session_ref"] == first["session_ref"]
        assert second["provider_session_id"] == first["provider_session_id"]
        assert second["turn_id"] != first["turn_id"]
    finally:
        provider.close()
