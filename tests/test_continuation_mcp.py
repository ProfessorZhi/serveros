#!/usr/bin/env python3
"""Tests for `serveros-continuation-mcp` — the surface ONE wake carries (J6, §2).

WHAT IS BEING PROVEN, AND WHAT IS NOT
-------------------------------------
The adapter is a boundary only if three things hold, and each has its own class
below:

1. It cannot be served without a wake. There is no envelope-less mode, no
   default Item and no fallback ref: a surface started without the launcher's
   envelope exits non-zero and names the variable (class 1).
2. The thing it carries is not a parameter. Every operation's target comes from
   the envelope, and the two arguments that COULD name another wake's work —
   `work.park`'s `checkpoint_ref` and `work.accept`'s `cycle` — are refused by
   the engine rather than remapped. Nothing was dispatched when they are
   (classes 3 and 4).
3. Authority is re-read, not remembered. The envelope says a grant was live when
   it was filed; the store decides now, and the same call under one action key
   either replays one receipt or is refused — never a second execution
   (classes 4 and 5).

WHAT THIS FILE DOES NOT PROVE, and says so rather than implying otherwise: that
`serverosctl work park` really performs a park. That is the verb's own suite's
question. Here `bin/serverosctl` is replaced by a script that records its argv
and prints a line, so what is asserted is the DECISION, the exact argv that
decision produces, and the receipt — the three things this surface adds. The
end-to-end run against the real verb, in a redirected state root, is the
acceptance run and not this file.

WHY THE INSTANTS ARE THE WALL CLOCK. `runtime/continuation_control.verify` takes
`now` as a parameter and holds no clock, so its own suite drives it at a fixed
instant. This adapter supplies the real one — that is the deployed behaviour —
so these fixtures are built around `utcnow()`: a trusted context is FRESH for 300
seconds, and a fixture pinned to a literal date would be answered by
`DELEGATION_DOES_NOT_AUTHORIZE` and prove nothing about the dispatch beneath it.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"


def _load(name: str, path: Path):
    """Load a module by path under a chosen name, once.

    The names are the surface's own — `serveros_runtime_continuation_control`,
    `serveros_runtime_bounded_invocation`, `cli` — and deliberately: the adapter
    looks each of them up in `sys.modules` before loading, so a test that
    pre-loaded them shares ONE module object with the surface instead of a
    second copy whose module-level state could disagree with it.
    """
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation", RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")
cc = _load("serveros_runtime_continuation_control",
           RUNTIME / "continuation_control.py")

mcp = _load("continuation_mcp", MCP / "continuation_mcp.py")

AgentControlStore = store_mod.AgentControlStore

ITEM = "SRV-0001"
OTHER_ITEM = "SRV-0002"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
SCOPE = "workspace/records"
BUDGET = {"wall_seconds": 900, "max_attempts": 2, "max_output_bytes": 200000}

#: A stand-in for `bin/serverosctl`, run by the interpreter so it needs neither
#: a shebang nor the executable bit. It records the argv it was handed, echoes
#: its stdin, and exits non-zero when asked to — which is how the "a verb that
#: failed is reported as failed" case is built.
FAKE_VERB = '''\
"""Records what it was handed. Not `serverosctl` — see the test module."""
import json, os, sys

log = os.environ.get("FAKE_VERB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")

argv = sys.argv[1:]
if "FAIL" in argv:
    sys.stderr.write("the verb refused\\n")
    sys.exit(3)
print(json.dumps({"argv": argv, "stdin": sys.stdin.read()}, sort_keys=True))
'''


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def minutes(n: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=n)).isoformat()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def a_context(**over) -> dict:
    """A trusted context that authorizes NOW, because the wall clock is what
    the adapter supplies: FRESH is 300 seconds from `observed_at`."""
    record = {"context_ref": CONTEXT, "actor_ref": "human:hwz",
              "provenance": "FACT", "provider_status": "VERIFIED",
              "observed_at": utcnow(), "expires_at": minutes(60)}
    record.update(over)
    return record


def a_grant(**over) -> dict:
    """A standing delegation wide enough to admit a cycle for either Item.

    Both Items, for `tests/test_continuation_control.py`'s reason: the child
    tests need a cycle admitted for a SIBLING Item to exist at all, and the
    store's `admit` is what checks that against this list — a refusal there
    would look like the adapter refusing it.
    """
    record = {"grant_ref": GRANT, "delegated_by": CONTEXT,
              "items": [ITEM, OTHER_ITEM], "actions": [], "revoked": False,
              "valid_from": minutes(-60), "expires_at": minutes(60),
              "budget": dict(BUDGET)}
    record.update(over)
    return record


def a_store(state_dir: Path, *, context=None, grant=None) -> AgentControlStore:
    """The store `serverosctl` would read, at the path it really resolves.

    Written to `<state>/runtime/agent-control/store.json` — the composition
    `runtime/registry.state_root` and `cli._agent_control_store_path` share —
    and read here through `cli._control_store()`, so these tests exercise the
    real path resolution and not a monkeypatched shortcut. Redirecting
    `SERVEROS_STATE_DIR` is the one supported way to run against a store that is
    not this node's, and the production store is never touched.
    """
    path = state_dir / "runtime" / "agent-control" / "store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = AgentControlStore(path)
    store.put_context(a_context() if context is None else context)
    store.put_grant(a_grant() if grant is None else grant)
    return store


def a_checkpoint(store: AgentControlStore, *, work_item: str = ITEM, epoch: int = 1,
                 child_refs=("cyc-1",), **over) -> dict:
    record = continuation.build_checkpoint(
        work_item=work_item, epoch=epoch, parent_owner="parent-a",
        next_safe_action="carry the Item on from the accepted evidence",
        waiting_on={"kind": "window", "until": minutes(30)}, now=utcnow(),
        requirement_revision="REQ-2026-09-20",
        child_attempts=list(child_refs), write_scope=[SCOPE])
    record.update(over)
    return store.put_checkpoint(record)


def an_admission(store: AgentControlStore, ref: str = "cyc-1",
                 work_item: str = ITEM) -> None:
    store.admit({
        "cycle_ref": ref, "grant_ref": GRANT,
        "selection": {"selection_digest": "a" * 64, "observed_at": utcnow(),
                      "selected": [work_item]},
        "reservation": dict(BUDGET),
        "lease": {"resource": "browser:chromium", "scope": "session",
                  "holder": ref},
        "action_key": "act-" + ref, "admitted_at": utcnow()})


def rewrite_store(store: AgentControlStore, mutate) -> None:
    """Edit the store document under the API, for a state the API will not make.

    `put_checkpoint` refuses to replace a live scoped record and a higher epoch
    SUPERSEDES the lower one in the same transaction, so the state the
    wrong-epoch rule guards is not reachable by calling the store. It is written
    here by editing the document, which is what a store edited by another
    revision or another process looks like. `_read()` keeps no cache, so what is
    written here is what the next reader sees.
    """
    doc = json.loads(store.path.read_text(encoding="utf-8"))
    mutate(doc)
    store.path.write_text(json.dumps(doc), encoding="utf-8")


def a_wake(store: AgentControlStore, wake_dir: Path, *, attempt: int = 1,
           key: str = "continuation-wake-SRV-0001-e1-a1", work_item: str = ITEM,
           epoch: int = 1, **over) -> Path:
    """File the envelope the launcher would have filed, through the launcher's
    own writer.

    `wake.write_envelope` and `wake.capability_envelope` rather than a JSON blob:
    an envelope this test composed itself would be this file's idea of one, and
    the adapter's idea — and the engine's — is what decides. The receipt root is
    the envelope's own directory, which is the derivation the class under test
    makes and the reason `receipts=` is not passed.
    """
    matches = [r for r in store.checkpoints().values()
               if r.get("work_item") == work_item and r.get("epoch") == epoch]
    assert len(matches) == 1, f"expected one record for {work_item}#{epoch}"
    envelope = wake.capability_envelope(
        matches[0], store.grants()[GRANT], GRANT, attempt=attempt, key=key,
        spend={"wall_seconds": BUDGET["wall_seconds"], "attempts_allowed": 2},
        now=utcnow())
    envelope.update(over)
    path = wake_dir / "envelope.json"
    wake.write_envelope(path, envelope)
    return path


@pytest.fixture
def state_dir(tmp_path, monkeypatch) -> Path:
    """`SERVEROS_STATE_DIR` pointed at a scratch root, and the scratch root.

    Set rather than bypassed: `cli._control_store()` composes the path from this
    variable, so a test that instead handed the store in would not exercise the
    resolution the deployed surface uses. Nothing here can reach this node's own
    store, which is the point — `ACTIVE` work is protected and a test is not a
    reason to open it.
    """
    root = tmp_path / "state"
    monkeypatch.setenv("SERVEROS_STATE_DIR", str(root))
    return root


@pytest.fixture
def verb_log(tmp_path, monkeypatch) -> Path:
    log = tmp_path / "verb.log"
    monkeypatch.setenv("FAKE_VERB_LOG", str(log))
    return log


@pytest.fixture
def fake_verb(tmp_path) -> Path:
    path = tmp_path / "fake-serverosctl.py"
    path.write_text(FAKE_VERB, encoding="utf-8")
    return path


@pytest.fixture
def wake_dir(tmp_path) -> Path:
    """Where the launcher files a wake: the envelope AND its receipt, together.

    Created here at 0700 because `wake.write_envelope` refuses a directory that
    is readable beyond its owner, and rightly: it is written before the spawn,
    so a directory another account can read would have received the capability
    before anything else refused.
    """
    path = tmp_path / "wake-SRV-0001-e1-a1"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def dispatched(log: Path) -> list:
    """Every argv the stand-in verb was actually handed, in order."""
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def a_completion(state: str = "OPEN", why: str = "two requirements are open"):
    return lambda item_id: {"item": item_id, "state": state, "why": why}


def a_server(store, wake_dir, fake_verb, *, completion=None, **over):
    """The surface as the launcher starts it: envelope path, and nothing else."""
    return mcp.ContinuationServer(
        wake_dir / "envelope.json", cwd=REPO, python=sys.executable,
        serverosctl=fake_verb,
        completion=completion or a_completion(), **over)


def call(server, name, arguments=None) -> dict:
    """One `tools/call`, answered the way a client reads it.

    Through `handle()` rather than `dispatch()`, so the argument allowlist is in
    the path: this is the only surface a Parent can reach, and testing the layer
    beneath it would leave the one that refuses unknown arguments untested.
    """
    response = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                             "params": {"name": name,
                                        "arguments": arguments or {}}})
    text = response["result"]["content"][0]["text"]
    if response["result"]["isError"]:
        return {"isError": True, "text": text}
    return {"isError": False, **json.loads(text)}


def denied(answer: dict, check: str) -> bool:
    """`answer` is a DENY whose FIRST refusal is `check`."""
    first = [c for c in answer["verdict"]["checks"] if c["decision"] != "ALLOW"]
    return (answer["decision"] == "DENY" and bool(first)
            and first[0]["check"] == check)


def a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb, **over):
    """One live checkpoint, one filed envelope, one server. The common setup."""
    store = a_store(state_dir)
    a_checkpoint(store)
    a_wake(store, wake_dir, **over)
    return store, a_server(store, wake_dir, fake_verb)


# ---------------------------------------------------------------------------
# 1. It cannot be served without a wake.
# ---------------------------------------------------------------------------


class TestItCannotBeServedWithoutAWake:
    def test_the_variable_it_reads_is_the_one_the_receiver_sets(self):
        """Asserted rather than restated: this module could keep its own copy of
        the name and the launcher's copy could drift, and the failure would be a
        surface reporting «no envelope» for every wake."""
        assert mcp.envelope_env_name() == cc.ENVELOPE_ENV_NAME
        assert mcp.envelope_env_name() == "SERVEROS_CONTINUATION_ENVELOPE"

    def test_main_refuses_a_missing_envelope_and_says_where_it_looked(self, capsys):
        code = mcp.main(["--envelope", "/nonexistent/envelope.json"])
        captured = capsys.readouterr()
        assert code == 2
        assert "/nonexistent/envelope.json" in captured.err
        assert mcp.envelope_env_name() in captured.err
        assert "cannot be served without the envelope" in captured.err

    def test_the_environment_variable_alone_is_enough(self, monkeypatch, tmp_path):
        monkeypatch.setenv(mcp.envelope_env_name(), str(tmp_path / "absent.json"))
        monkeypatch.setattr(sys, "argv", ["serveros-continuation-mcp"])
        assert mcp.main([]) == 2

    def test_a_document_with_another_schema_is_refused(self, tmp_path):
        path = tmp_path / "envelope.json"
        path.write_text(json.dumps({"schema": "serveros/something-else.v1"}))
        with pytest.raises(cc.BadEnvelope):
            mcp.ContinuationServer(path)

    def test_the_shim_the_contract_declares_is_present_and_executable(self):
        shim = MCP / "bin" / "serveros-continuation-mcp"
        assert shim.is_file()
        assert shim.stat().st_mode & stat.S_IXUSR
        # It adds `mcp/` to `sys.path` and imports the bare module name, which is
        # the fabric's own idiom — the artifact is not a set of packages.
        assert "sys.path.insert" in shim.read_text()

    def test_the_contract_declares_this_surface_and_names_this_file(self):
        """The declaration and the code are two documents, and this is the one
        place they are compared. `fleet/mcp-contract.yaml` is enforced by
        `tests/test_fleet_transport.py` for disjointness and for
        `remote.shell`; what is checked here is that the surface this file tests
        is the surface the contract describes, tool for tool."""
        contract = yaml.safe_load((REPO / "fleet" / "mcp-contract.yaml").read_text())
        surfaces = contract["surfaces"]
        declared = surfaces["serveros-continuation-mcp"]
        assert declared["implementation"] == "mcp/continuation_mcp.py"
        assert set(declared["tools"]) == set(mcp.tool_names())
        assert "remote.shell" in declared["explicitly_absent"]
        # The declared-but-unserved name, named as such, so that a reader of
        # `tools` does not conclude completion can be performed here.
        assert "work.complete" in declared["declared_but_refusing"]
        assert "work.complete" not in declared["owns"]


# ---------------------------------------------------------------------------
# 2. The surface is exactly the engine's vocabulary.
# ---------------------------------------------------------------------------


class TestTheSurfaceIsTheEnginesVocabulary:
    def test_the_tools_are_exactly_the_two_vocabularies_the_engine_declares(self):
        """No more and no fewer, in all three places that name the set.

        A tool the engine has no rule for is a route around it, and an operation
        the engine carries with no tool is a capability the Parent cannot reach
        and will shell out for. The issuer's two tuples and the engine's own
        table are compared here as well as with each other — the engine's suite
        asserts the first pair, this asserts the surface against both."""
        assert set(mcp.tool_names()) == (set(wake.CONTINUATION_OPERATIONS)
                                         | set(wake.CONTINUATION_OPERATIONS_UNSUPPORTED))
        assert set(mcp.tool_names()) == set(cc.OPERATIONS)

    def test_every_answer_carries_the_same_shape(self, state_dir, wake_dir,
                                                tmp_path, fake_verb):
        """One shape, so a caller that reads `decision` reads the right field.

        `continuation.inspect` used to nest its verdict and so had no top-level
        `decision` while every other tool had one, and the two read tools had no
        `executed` at all — so a Parent that had learned the shape from
        `work.park` read `None` from an ALLOW and concluded its wake was broken.
        Asserted across all six, ALLOW and DENY alike, which is also the only
        way to catch the next tool somebody adds."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        for name, arguments in (
                ("continuation.inspect", {}),
                ("child-result.inspect", {"cycle": "cyc-1"}),
                ("work.checkpoint", {"next_action": "carry on"}),
                ("work.park", {"reason": "stop"}),
                ("work.accept", {"cycle": "cyc-1", "locator": "rev-1"}),
                ("work.complete", {})):
            answer = call(server, name, arguments)
            assert not answer["isError"], name
            for field in ("operation", "decision", "because", "verdict",
                          "executed", "receipt"):
                assert field in answer, f"{name} has no {field}"
            assert answer["operation"] == name
            assert answer["decision"] in cc.DECISIONS
            assert answer["verdict"]["decision"] == answer["decision"], name
            # `executed` and `receipt` agree: nothing is ever handed back as
            # done without the record of the thing that did it, and nothing is
            # handed back as not-done with one.
            assert (answer["receipt"] is None) is (answer["executed"] is False), name

    def test_every_tool_declares_a_description_and_a_schema(self):
        for tool in mcp.TOOLS:
            assert tool["description"].strip(), tool["name"]
            assert tool["inputSchema"]["type"] == "object", tool["name"]

    def test_every_tool_refuses_arguments_it_does_not_declare(self):
        """`additionalProperties: false` on all six is what makes «the Item is
        not a parameter» true rather than aspirational: a caller cannot smuggle
        `item` past the schema and hope the layer beneath ignores it."""
        for tool in mcp.TOOLS:
            assert tool["inputSchema"]["additionalProperties"] is False, tool["name"]

    def test_no_tool_offers_an_item_an_epoch_or_a_grant(self):
        for tool in mcp.TOOLS:
            offered = set(tool["inputSchema"]["properties"])
            assert not offered & {"item", "work_item", "epoch", "grant",
                                  "grant_ref", "context", "context_ref"}, tool["name"]

    def test_the_unsupported_name_refuses_rather_than_absent(self, state_dir,
                                                             wake_dir, tmp_path,
                                                             fake_verb, verb_log):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.complete")
        assert denied(answer, "OPERATION_NAMED_UNSUPPORTED")
        assert cc.UNSUPPORTED_BECAUSE in answer["because"]
        assert answer["executed"] is False
        assert dispatched(verb_log) == []

    def test_the_boundary_a_parent_actually_has_is_reported_by_inspect(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "continuation.inspect")
        assert answer["decision"] == "ALLOW"
        assert answer["envelope"]["work_item_ref"] == ITEM
        assert answer["checkpoint"]["work_item"] == ITEM
        assert answer["grant"]["grant_ref"] == GRANT
        assert answer["completion"]["state"] == "OPEN"
        # An inspect reads. Nothing was dispatched, and the surface says which
        # operation it answered for rather than leaving the caller to infer it.
        assert answer["verdict"]["operation"] == "continuation.inspect"
        assert dispatched(verb_log) == []


# ---------------------------------------------------------------------------
# 3. The caller cannot name another wake's work.
# ---------------------------------------------------------------------------


class TestTheTargetIsNotAParameter:
    def test_inspect_takes_no_arguments_at_all(self, state_dir, wake_dir, tmp_path,
                                              fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "continuation.inspect", {"item": OTHER_ITEM})
        assert answer["isError"] is True
        assert "unexpected argument" in answer["text"]

    def test_checkpoint_offers_no_item_and_no_epoch_and_refuses_both(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """The strongest form of the wrong-Item rule available here: not a
        refusal naming the field that differs, but the absence of the field."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        for smuggled in ({"item": OTHER_ITEM}, {"epoch": 9}):
            answer = call(server, "work.checkpoint",
                          {"next_action": "carry on", **smuggled})
            assert answer["isError"] is True, smuggled
        assert dispatched(verb_log) == []

    def test_a_park_aimed_at_another_items_record_is_refused(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        theirs = a_checkpoint(store, work_item=OTHER_ITEM, epoch=1)
        answer = call(server, "work.park",
                      {"reason": "stop", "checkpoint_ref": theirs["checkpoint_ref"]})
        assert denied(answer, "PARK_TARGET_IS_NOT_THIS_ITEM")
        assert OTHER_ITEM in answer["because"]
        assert answer["executed"] is False and answer["receipt"] is None
        assert dispatched(verb_log) == []
        # ...and the other Item's record is untouched.
        still = store.checkpoints()[theirs["checkpoint_ref"]]
        assert not still.get("parked_at")

    def test_a_park_aimed_at_another_epoch_of_this_item_is_refused(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """Both records have to be live for this rule to be the one that
        answers, and the store will not leave two of one Item live — writing
        epoch 2 supersedes epoch 1 in the same transaction, so
        `CHECKPOINT_NOT_LIVE` fires first. `rewrite_store` writes the state the
        guard exists for, which is a document this store's own writer will not
        produce."""
        store = a_store(state_dir)
        lower = a_checkpoint(store, epoch=1)
        a_wake(store, wake_dir)
        higher = a_checkpoint(store, epoch=2, parent_owner="parent-b")
        rewrite_store(store, lambda doc: doc["checkpoints"][
            lower["checkpoint_ref"]].pop("superseded_by", None))
        server = a_server(store, wake_dir, fake_verb)
        answer = call(server, "work.park",
                      {"reason": "stop",
                       "checkpoint_ref": higher["checkpoint_ref"]})
        assert denied(answer, "PARK_TARGET_IS_ANOTHER_EPOCH")
        assert answer["executed"] is False
        assert dispatched(verb_log) == []

    def test_a_park_the_caller_did_not_aim_defaults_to_this_wakes_own(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.park", {"reason": "the window is closed"})
        assert answer["decision"] == "ALLOW"
        assert dispatched_env(answer)["checkpoint_ref"] == "SRV-0001#1"


def dispatched_env(answer: dict) -> dict:
    """The exact stdin the stand-in verb was handed, parsed.

    Read out of the receipt's stdout artifact rather than out of the answer: the
    artifact is the record the mandate's evidence rule points at, and a value
    the adapter merely reported would be its own account of what it sent. (The
    stand-in echoes its stdin back, which is how it gets here; the real
    `serverosctl` prints JSON of its own and the artifact holding that stdin is
    what this stands in for.)
    """
    return json.loads(answer["receipt"]["stdout_json"]["stdin"])


# ---------------------------------------------------------------------------
# 4. Every mutation is re-decided, and a refusal dispatches nothing.
# ---------------------------------------------------------------------------


class TestEveryMutationIsRefusedFromTheStore:
    """§2's negative cases, each through the surface and each asserting that
    nothing was started. That last assertion is the one that matters: a DENY
    that still ran the verb would be worse than no boundary at all."""

    def test_a_revoked_grant_refuses_whatever_the_envelope_still_says(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """THE CASE THE SURFACE EXISTS FOR. The envelope was filed while the
        grant was live and says so; the store says otherwise, and the store is
        what decides. Nothing was dispatched."""
        store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        assert call(server, "work.park", {"reason": "stop"})["decision"] == "ALLOW"
        assert len(dispatched(verb_log)) == 1

        store.revoke_grant(GRANT, at=utcnow(), reason="the person went home")
        answer = call(server, "work.park", {"reason": "stop"})
        assert denied(answer, "GRANT_REVOKED")
        assert "the person went home" in answer["because"]
        assert answer["executed"] is False and answer["receipt"] is None
        assert len(dispatched(verb_log)) == 1  # the first one, and none since

    def test_a_grant_whose_window_does_not_contain_now_refuses(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """One check, two directions, and this is the direction a real envelope
        can reach: a grant that has not started yet. The other direction — a
        grant that has already ended — cannot be filed from, because the
        envelope's own expiry is the EARLIER of the wall budget and the grant,
        so the store would be asked about a wake that had already stopped. That
        ordering is `ENVELOPE_IN_FORCE` before `GRANT_IN_FORCE`, and it is
        asserted in the engine's own suite rather than faked here."""
        store = a_store(state_dir, grant=a_grant(valid_from=minutes(20),
                                                 expires_at=minutes(40)))
        a_checkpoint(store)
        a_wake(store, wake_dir)
        server = a_server(store, wake_dir, fake_verb)
        answer = call(server, "work.park", {"reason": "stop"})
        assert denied(answer, "GRANT_NOT_CURRENT")
        assert answer["executed"] is False
        assert dispatched(verb_log) == []

    def test_a_checkpoint_outside_the_recorded_write_scope_refuses(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.checkpoint",
                      {"next_action": "carry on",
                       "write_scope": ["/etc", SCOPE + "-sibling"]})
        assert denied(answer, "WRITE_SCOPE_OUTSIDE_ENVELOPE")
        assert "/etc" in answer["because"] and SCOPE + "-sibling" in answer["because"]
        assert answer["executed"] is False
        assert dispatched(verb_log) == []

    def test_an_accept_of_a_cycle_this_checkpoint_never_named_refuses(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.accept",
                      {"cycle": "cyc-another-scope", "locator": "rev-1"})
        assert denied(answer, "CHILD_NOT_THIS_WAKE'S")
        assert answer["executed"] is False
        assert dispatched(verb_log) == []

    def test_a_receipt_of_another_items_admitted_cycle_refuses(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """Named AND admitted AND somebody else's. The two child operations are
        held to one predicate precisely so that this ref is no more readable
        than it is receivable — the reading would be the leak."""
        store = a_store(state_dir)
        a_checkpoint(store, child_refs=["cyc-2"])
        an_admission(store, "cyc-2", work_item=OTHER_ITEM)
        a_wake(store, wake_dir)
        server = a_server(store, wake_dir, fake_verb)
        for name, arguments in (
                ("child-result.inspect", {"cycle": "cyc-2"}),
                ("work.accept", {"cycle": "cyc-2", "locator": "rev-1"})):
            answer = call(server, name, arguments)
            assert denied(answer, "CHILD_ADMISSION_IS_ANOTHER_ITEM"), name
            assert answer["executed"] is False or name == "child-result.inspect"
        # The read is refused too, and that is the point of holding the two to
        # one predicate: a refused read carries no admission, so the leak the
        # rule exists to close is closed on the reading side as well.
        assert call(server, "child-result.inspect",
                    {"cycle": "cyc-2"})["admission"] is None
        assert dispatched(verb_log) == []

    def test_a_refusal_is_an_answer_and_not_a_tool_failure(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        """`isError` false for a refusal, which is `runtime/ownership_gate.py`'s
        distinction kept rather than collapsed: DENY is a verdict about the
        request, and a client is not told its call was malformed."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.accept",
                      {"cycle": "cyc-nope", "locator": "rev-1"})
        assert answer["isError"] is False
        assert answer["decision"] == "DENY"
        # The report carries the ALLOWs it got through before the refusal, which
        # is the whole value of the list: a person sees how far the wake got.
        decisions = [c["decision"] for c in answer["verdict"]["checks"]]
        assert "ALLOW" in decisions and decisions[-1] == "DENY"

    def test_the_store_is_read_again_on_every_call_and_not_remembered(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        """The envelope is a binding and not authority. Between two identical
        calls the store changes, and the second call must follow the store."""
        store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        assert call(server, "continuation.inspect")["decision"] == "ALLOW"
        store.park_checkpoint("SRV-0001#1", at=utcnow(), by="ops",
                              reason="set down for the night")
        answer = call(server, "continuation.inspect")
        assert denied(answer, "CHECKPOINT_NOT_LIVE")
        assert "parked at" in answer["because"]

    def test_a_store_that_is_not_there_refuses_rather_than_allowing(
            self, tmp_path, wake_dir, fake_verb, verb_log):
        """A `SERVEROS_STATE_DIR` with no store in it. An empty store answers
        «nobody recorded this», which is a refusal — not the ALLOW that reading
        a missing precondition as satisfied would give."""
        _store, server = a_ready_wake(tmp_path / "state", wake_dir, tmp_path,
                                      fake_verb)
        os.unlink(tmp_path / "state" / "runtime" / "agent-control" / "store.json")
        answer = call(server, "work.park", {"reason": "stop"})
        assert answer["decision"] == "DENY"
        assert denied(answer, "CHECKPOINT_NOT_IN_STORE")
        assert dispatched(verb_log) == []


# ---------------------------------------------------------------------------
# 5. An allowed mutation: the exact argv, one receipt, and one execution.
# ---------------------------------------------------------------------------


class TestAnAllowedMutationLeavesAReceipt:
    def test_legacy_acceptance_replays_without_blocking_a_second_child(
            self, state_dir, wake_dir, fake_verb, verb_log):
        store = a_store(state_dir)
        a_checkpoint(store, child_refs=["cyc-1", "cyc-2"])
        for cycle in ("cyc-1", "cyc-2"):
            an_admission(store, cycle)
        a_wake(store, wake_dir)
        server = a_server(store, wake_dir, fake_verb)
        key = server.envelope["action_key"] + "-work-accept"
        arguments = {"cycle": "cyc-1", "locator": "rev-1", "by": "parent-a"}
        mcp._bounded().invoke_once(
            key=key, argv=[server.python, str(fake_verb), "work", "accept",
                           "cyc-1", "--by", "parent-a", "--locator", "rev-1"],
            cwd=server.cwd, receipts=server.receipts,
            input_text=json.dumps(arguments, sort_keys=True),
            timeout=mcp.MUTATION_TIMEOUT_SECONDS, readonly=False,
            max_output_bytes=mcp.MUTATION_MAX_OUTPUT_BYTES,
            env={mcp.envelope_env_name(): str(server.envelope_path)})
        replay = call(server, "work.accept", {"cycle": "cyc-1", "locator": "rev-1"})
        assert replay["receipt"]["replayed"] is True
        assert replay["receipt"]["executed_now"] is False
        second = call(server, "work.accept", {"cycle": "cyc-2", "locator": "rev-2"})
        assert second["receipt"]["executed_now"] is True
        assert len(dispatched(verb_log)) == 2

    def test_invalid_checkpoint_can_be_corrected_without_consuming_the_attempt(
            self, state_dir, wake_dir, tmp_path):
        store, server = a_ready_wake(state_dir, wake_dir, tmp_path, REPO / "bin" / "serverosctl")
        invalid = call(server, "work.checkpoint", {"next_action": "continue verification"})
        assert invalid["executed"] is False
        assert invalid["receipt"] is None
        malformed = call(server, "work.checkpoint", {
            "next_action": "--verify", "waiting_on_child": "cyc-1"})
        assert malformed["isError"] is True
        assert "waiting_on_child" in malformed["text"]
        answer = call(server, "work.checkpoint", {
            "next_action": "--verify", "waiting_on_window": minutes(30)})
        assert answer["decision"] == "ALLOW", answer
        assert answer["receipt"]["process_exit_code"] == 0, answer
        assert store.checkpoints()[ITEM + "#1"]["superseded_by"] == ITEM + "#2"
        assert store.checkpoints()[ITEM + "#2"]["write_scope"] == [SCOPE]
        assert store.checkpoints()[ITEM + "#2"]["next_safe_action"] == "--verify"

    def test_two_child_results_are_accepted_once_through_the_real_cli(
            self, state_dir, wake_dir):
        store = a_store(state_dir)
        a_checkpoint(store, child_refs=["cyc-1", "cyc-2"])
        for cycle in ("cyc-1", "cyc-2"):
            an_admission(store, cycle)
            store.confirm_admission(cycle, phase="LEASE_HELD", at=utcnow(),
                                    evidence={"act": "DECLARE", "resource": "browser:chromium"})
            store.confirm_admission(cycle, phase="DISPATCHED", at=utcnow(),
                                    evidence={"action_key": "act-" + cycle, "status": "EXITED"})
            store.retire_admission(cycle, at=utcnow(), outcome="fixture completed")
        a_wake(store, wake_dir)
        server = a_server(store, wake_dir, REPO / "bin" / "serverosctl")
        for cycle in ("cyc-1", "cyc-2"):
            args = {"cycle": cycle, "locator": "evidence:" + cycle}
            first = call(server, "work.accept", args)
            assert first["decision"] == "ALLOW", first
            assert first["receipt"]["process_exit_code"] == 0, first
            second = call(server, "work.accept", args)
            assert second["receipt"]["replayed"] is True
            assert second["receipt"]["executed_now"] is False
            assert store.admissions()[cycle]["accepted_locator"] == args["locator"]
        conflict = call(server, "work.accept", {"cycle": "cyc-2", "locator": "different"})
        assert conflict["decision"] == "DENY"
        assert store.admissions()["cyc-2"]["accepted_locator"] == "evidence:cyc-2"

    def test_a_park_dispatches_the_verbs_own_argv_and_no_other(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.park", {"reason": "the window is closed"})
        assert answer["decision"] == "ALLOW" and answer["executed"] is True
        assert dispatched(verb_log) == [[
            "work", "park", "SRV-0001#1",
            "--reason", "the window is closed", "--by", "parent-a"]]
        # `--by` is the WAKE's parent_owner, not the operator login: `work park`
        # defaults `--by` to whoever the process runs as, which for a woken
        # Parent is the account and not the Parent — a name true of the machine
        # and false of the work.
        assert answer["receipt"]["process_exit_code"] == 0
        assert answer["receipt"]["status"] == "EXITED"

    def test_a_checkpoint_files_the_next_epoch_and_not_the_one_it_woke_from(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """The record this Parent was woken FROM is the one write it may not make.

        `AgentControlStore.put_checkpoint` keys a checkpoint by (Item, epoch) and
        refuses to replace a LIVE one under its own key. The record a wake
        carries is live by definition — `continuation_control.verify` refuses the
        mutation otherwise — so a checkpoint dispatched at the envelope's epoch
        is refused with the store's own sentence, «Carry the Item on under a new
        epoch, which takes the scope over and keeps this record readable». That
        refusal is the design: a Parent that replaces another writes the
        SUCCESSOR record, and the store supersedes the record it replaced in the
        same transaction.

        THIS ASSERTS THE ARGV BECAUSE THE ARGV IS WHERE THE DEFECT WAS. Nothing
        in this file reaches `put_checkpoint` — the stand-in verb records what it
        was handed and prints a line — so a checkpoint aimed at the epoch it woke
        from passed every test in the file. The acceptance run, in a redirected
        state root, is what found it. The second half below closes that gap from
        the other side: the epoch this dispatch named is handed to the REAL
        store, which is the property the change exists to satisfy rather than two
        numbers that happen to differ.
        """
        store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        until = minutes(30)
        answer = call(server, "work.checkpoint", {"next_action": "carry it on",
                                                 "waiting_on_window": until})
        assert answer["decision"] == "ALLOW", answer.get("because")
        assert answer["executed"] is True
        epoch = int(server.envelope["epoch"])
        assert dispatched(verb_log) == [[
            "work", "checkpoint", ITEM, "--epoch", str(epoch + 1),
            "--parent", "parent-a", "--next-action", "carry it on",
            "--waiting-on-window", until, "--write-scope", SCOPE]]
        # ...and the epoch named is not the one this wake carries.
        assert str(epoch + 1) != str(epoch)

        # The number is not merely different: it is the one the store TAKES for
        # this Item, and taking it supersedes the record the wake carried. Fed
        # the argv the adapter really dispatched, so a regression to the
        # envelope's epoch fails here as well as above.
        argv = dispatched(verb_log)[0]
        named = int(argv[argv.index("--epoch") + 1])
        successor = continuation.build_checkpoint(
            work_item=ITEM, epoch=named, parent_owner="parent-a",
            next_safe_action="carry it on",
            waiting_on={"kind": "window", "until": minutes(30)}, now=utcnow(),
            requirement_revision="REQ-2026-09-20",
            child_attempts=["cyc-1"], write_scope=[SCOPE])
        store.put_checkpoint(successor)
        assert store.checkpoints()[f"{ITEM}#{epoch}"]["superseded_by"] == \
            successor["checkpoint_ref"]

    def test_the_receipt_names_the_artifacts_a_verdict_can_rest_on(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.park", {"reason": "stop"})
        receipt = answer["receipt"]
        assert receipt["effect"] == "UNVERIFIED"
        assert set(receipt["output_sha256"]) == {"stdout", "stderr"}
        assert Path(receipt["stdout_artifact"]).is_file()
        assert Path(receipt["stderr_artifact"]).is_file()
        # The artifact the evidence rule actually wants: the exact stdin the
        # decision was made against, on disk, not the Parent's account of it.
        assert json.loads(receipt["stdout_json"]["stdin"])["reason"] == "stop"

    def test_the_receipts_live_beside_the_envelope_the_launcher_filed(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.park", {"reason": "stop"})
        assert Path(answer["receipt"]["stdout_artifact"]).parent == wake_dir

    def test_a_second_call_under_one_action_key_replays_and_does_not_re_run(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """One action key is one mutation. The second call is answered from the
        durable receipt, and the stand-in verb — which would happily run again —
        is not run again."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        first = call(server, "work.park", {"reason": "stop"})
        second = call(server, "work.park", {"reason": "stop"})
        assert first["decision"] == second["decision"] == "ALLOW"
        assert first["receipt"]["executed_now"] is True
        assert second["receipt"]["replayed"] is True
        assert second["receipt"]["executed_now"] is False
        assert len(dispatched(verb_log)) == 1

    def test_the_same_key_with_DIFFERENT_input_is_refused_and_runs_nothing(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """The other half of the same rule. A wake that could set its record
        down twice, for two different reasons, would have written a second
        mutation under one authorization — and `invoke_once` refuses it rather
        than silently replaying the first answer as if it were the second."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        call(server, "work.park", {"reason": "the window closed"})
        answer = call(server, "work.park", {"reason": "a different reason"})
        assert answer["decision"] == "DENY"
        assert answer["dispatch_refused"] is True
        assert "same action key with different input" in answer["because"]
        assert answer["executed"] is False
        assert len(dispatched(verb_log)) == 1

    def test_two_operations_of_one_wake_do_not_share_a_receipt(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """The key is the action key AND the operation. A park and an accept
        under one wake are two dispatches, and a single-receipt-per-wake rule
        would have refused the second."""
        store = a_store(state_dir)
        a_checkpoint(store, child_refs=["cyc-1"])
        an_admission(store, "cyc-1")
        a_wake(store, wake_dir)
        server = a_server(store, wake_dir, fake_verb)
        assert call(server, "work.accept",
                    {"cycle": "cyc-1", "locator": "rev-7"})["executed"] is True
        assert call(server, "work.park", {"reason": "stop"})["executed"] is True
        assert [a[1] for a in dispatched(verb_log)] == ["accept", "park"]

    def test_a_verb_that_failed_is_reported_as_failed_and_not_as_an_effect(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        """The adapter does not upgrade an exit code into a claim. A verb that
        exited non-zero is recorded as one that ran and failed, with the code
        and both streams on disk — which is the only thing a later reader can
        check."""
        path = tmp_path / "fake-serverosctl.py"
        path.write_text(FAKE_VERB.replace("argv = sys.argv[1:]",
                                          "argv = sys.argv[1:]\nargv.append('FAIL')"),
                        encoding="utf-8")
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, path)
        answer = call(server, "work.park", {"reason": "stop"})
        assert answer["decision"] == "ALLOW"  # the boundary allowed it
        assert answer["receipt"]["process_exit_code"] == 3
        assert answer["receipt"]["status"] == "EXITED"
        assert Path(answer["receipt"]["stderr_artifact"]).read_text().strip() == \
            "the verb refused"

    def test_a_new_attempt_of_the_same_item_gets_its_own_key_and_its_own_run(
            self, state_dir, wake_dir, tmp_path, fake_verb, verb_log):
        """A second WAKE is not a second call under one key. It has its own
        action key, its own envelope file and its own receipt — so the replay
        rule above has not quietly become «this Item may be checkpointed once»."""
        store = a_store(state_dir)
        a_checkpoint(store)
        first_dir = tmp_path / "wake-a"
        first_dir.mkdir(mode=0o700)
        second_dir = tmp_path / "wake-b"
        second_dir.mkdir(mode=0o700)
        a_wake(store, first_dir, attempt=1, key="continuation-wake-SRV-0001-e1-a1")
        a_wake(store, second_dir, attempt=2, key="continuation-wake-SRV-0001-e1-a2")
        one = a_server(store, first_dir, fake_verb)
        two = a_server(store, second_dir, fake_verb)
        assert one.envelope["action_key"] != two.envelope["action_key"]
        assert call(one, "work.park", {"reason": "stop"})["executed"] is True
        assert call(two, "work.park", {"reason": "stop"})["executed"] is True
        assert len(dispatched(verb_log)) == 2

    def test_the_envelope_environment_is_part_of_the_dispatch(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        """The verb runs in the same wake the adapter decided for, and the
        variable is part of `invoke_once`'s input digest — so two dispatches
        that differed only in their wake would be two dispatches."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        seen = {}
        real = mcp._bounded().invoke_once

        def capture(**kwargs):
            seen.update(kwargs)
            return real(**kwargs)

        mcp._bounded().invoke_once = capture
        try:
            assert call(server, "work.park", {"reason": "stop"})["executed"] is True
        finally:
            mcp._bounded().invoke_once = real
        assert seen["env"] == {
            mcp.envelope_env_name(): str(wake_dir / "envelope.json")}
        assert seen["readonly"] is False


# ---------------------------------------------------------------------------
# 6. The wire.
# ---------------------------------------------------------------------------


class TestTheWire:
    def test_initialize_announces_one_wakes_authority(self, state_dir, wake_dir,
                                                      tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": "2024-11-05"}})
        result = response["result"]
        assert result["protocolVersion"] == mcp.PROTOCOL_VERSION
        assert result["serverInfo"]["name"] == mcp.SERVER_NAME
        assert "ONE wake's authority" in result["instructions"]

    def test_tools_list_returns_the_six_and_the_schemas(self, state_dir, wake_dir,
                                                        tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listed = [t["name"] for t in response["result"]["tools"]]
        assert listed == list(mcp.tool_names())

    def test_a_notification_is_not_answered(self, state_dir, wake_dir, tmp_path,
                                            fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        assert server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled"}) is None

    def test_an_unknown_method_is_a_method_not_found(self, state_dir, wake_dir,
                                                     tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        response = server.handle({"jsonrpc": "2.0", "id": 3,
                                  "method": "resources/list"})
        assert response["error"]["code"] == -32601

    def test_an_unknown_tool_is_an_error_naming_the_ones_that_exist(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        """An error and not a refusal: a client that asked for a tool this
        surface does not have has made a mistake, and the two must not be
        collapsed — the same reason DENY and UNASKABLE are kept apart."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        answer = call(server, "work.teleport")
        assert answer["isError"] is True
        assert "work.park" in answer["text"]

    def test_serve_writes_one_object_per_line_and_survives_a_parse_error(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        instream = io.StringIO("{not json\n"
                               '{"jsonrpc":"2.0","id":4,"method":"ping"}\n')
        outstream = io.StringIO()
        assert mcp.serve(instream, outstream, server) == 0
        lines = [json.loads(l) for l in outstream.getvalue().splitlines()]
        assert lines[0]["error"]["code"] == -32700
        assert lines[1]["result"] == {}

    def test_a_dispatch_fault_does_not_hang_the_client(self, state_dir, wake_dir,
                                                       tmp_path, fake_verb):
        """`dispatch` raises `WiringFault` for a name it has no rule for, and a
        tool that reaches it must answer rather than take the process down."""
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        with pytest.raises(mcp.WiringFault):
            server.dispatch("continuation.teleport", {})
        response = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "continuation.teleport",
                                            "arguments": {}}})
        assert response["result"]["isError"] is True

    def test_list_tools_prints_the_names(self, capsys):
        assert mcp.main(["--list-tools"]) == 0
        printed = capsys.readouterr().out
        for name in mcp.tool_names():
            assert name in printed


class TestBoundedEngineering:
    def test_legacy_wake_keeps_control_without_home_or_engineering(
            self, state_dir, wake_dir, tmp_path, fake_verb):
        _store, server = a_ready_wake(state_dir, wake_dir, tmp_path, fake_verb)
        cwd = server.parent_cwd()
        assert cwd.parent == wake_dir
        assert list(cwd.iterdir()) == []
        assert not server.envelope_path.is_relative_to(cwd)
        denied = server.dispatch("continuation.execute", {"key": "test", "argv": ["python3", "-V"]})
        assert denied["decision"] == "DENY"
        assert denied["executed"] is False

    def test_an_item_in_no_projection_cannot_license_a_wake(self, tmp_path):
        """The guard that keeps a reader's blindness out of a permission.

        `intake/workchain.compose_item` falls back to the records tree for an
        Item no projection carries, which is what makes `work item LIT-0106`
        answer at all. That boundary is `NOT_ASSESSED` with `state: UNKNOWN`,
        and `runtime/continuation_control.verify` denies on `COMPLETE` alone —
        so if this reader accepted it, a wake that is refused today would be
        allowed, on the strength of a state nobody read.

        The Item below has a real card on the disk and no row anywhere, which is
        the shape of the six litellm Items on this node. The second half of the
        test is the positive control: the same Item WITH a projection row
        returns its boundary, so the guard refuses a missing row and not every
        row.
        """
        types = __import__("types")
        chain = _load("serveros_intake_workchain", REPO / "intake" / "workchain.py")
        root = tmp_path / "node"
        (root / "boards" / "px").mkdir(parents=True)
        (root / "boards" / "px" / "current.json").write_text(json.dumps(
            {"schema": "serveros/board.v3", "project": "px",
             "generated_at": "2026-01-01T00:00:00+00:00",
             "generation_source_revision": None, "items": []}), encoding="utf-8")
        item_dir = root / "records" / "projects" / "px" / "items" / "IT-9"
        item_dir.mkdir(parents=True)
        (item_dir / "card.yaml").write_text("id: IT-9\ntitle: one\n",
                                            encoding="utf-8")

        def no_git(*_args, **_kwargs):
            return 1, ""

        cli = types.SimpleNamespace(
            REPO_ROOT=REPO, _workchain_module=lambda: chain,
            _workchain_skeletons=lambda module: None,
            _workchain_git=lambda: no_git,
            _workchain_roots=lambda _args: (root / "boards", root / "records",
                                            root))
        read = mcp._completion_reader(cli)
        answer = read("IT-9")
        assert "unreadable" in answer, answer
        assert "none of the" in answer["unreadable"] or "no projection" in \
            answer["unreadable"] or "cannot be composed" in answer["unreadable"], \
            answer

        (root / "boards" / "px" / "current.json").write_text(json.dumps(
            {"schema": "serveros/board.v3", "project": "px",
             "generated_at": "2026-01-01T00:00:00+00:00",
             "generation_source_revision": None,
             "items": [{"id": "IT-9", "title": "one", "kind": "feature",
                        "state": "ACTIVE", "outcome": None, "card_locator": None,
                        "activities": [], "deliverables": []}]}), encoding="utf-8")
        boundary = read("IT-9")
        assert boundary.get("state") == "NOT_ASSESSED", boundary
        assert "unreadable" not in boundary

    @pytest.mark.skipif("SERVEROS_TEST_DOCKER_IMAGE" not in os.environ,
                        reason="requires an explicitly selected existing local image")
    def test_real_mcp_execute_uses_live_scope_and_replays(
            self, state_dir, wake_dir, tmp_path, monkeypatch):
        from types import SimpleNamespace
        tree = tmp_path / "workspace" / "worktrees" / "demo" / "item"
        tree.mkdir(parents=True)
        binding = {"schema": "serveros/engineering-binding.v1", "worktree": str(tree),
                   "image": os.environ["SERVEROS_TEST_DOCKER_IMAGE"],
                   "memory_bytes": 64 * 1024**2, "cpus": 1, "pids_limit": 16}
        store = a_store(state_dir, grant=a_grant(actions=["engineering.execute"], engineering=binding))
        a_checkpoint(store, write_scope=[str(tree)])
        envelope = a_wake(store, wake_dir)
        server = mcp.ContinuationServer(envelope, completion=lambda _: {"state": "OPEN"})
        paths = mcp._load("serveros_executor_paths", RUNTIME / "paths.py")
        monkeypatch.setattr(paths, "Paths", lambda: SimpleNamespace(bound=True,
            root=lambda name: tmp_path / "workspace"))
        assert server.parent_cwd() == tree
        args = {"key": "verify", "argv": ["python3", "-c", "print('engineering-complete')"]}
        first = server.dispatch("continuation.execute", args)
        assert first["decision"] == "ALLOW", first
        assert first["worker_stopped"] is True, first
        assert first["container"]["container_state"]["ExitCode"] == 0
        assert first["output_tail"]["stdout"].strip() == "engineering-complete"
        assert server.dispatch("continuation.execute", args)["receipt"]["replayed"] is True
        # An envelope cannot keep a broader scope after the current checkpoint narrows.
        rewrite_store(store, lambda doc: doc["checkpoints"][server.envelope["checkpoint_ref"]].update(write_scope=[]))
        denied = server.dispatch("continuation.execute", args)
        assert denied["decision"] == denied["verdict"]["decision"] == "DENY"
        assert denied["executed"] is False
        with pytest.raises(ValueError, match="scope"):
            server.parent_cwd()
