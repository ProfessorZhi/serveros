#!/usr/bin/env python3
"""§17 · thread isolation, and the words it refuses in.

The kernel is `runtime/coordination.py`. This file tests three different
things, and it is worth being explicit about which is which, because the
distinction is the whole reason the failure it exists for happened:

  1. THE KERNEL'S RULES. Pure functions, tested without a store, a daemon or a
     machine. `binding()` and `admit_work()` and `item_axis()`.

  2. THE VOCABULARY. Three words were added to a closed list that is owned by
     `abilities/target-contract.yaml`. That list is the one place a refusal may
     be spelled from, and `TransportFailure` validates against it at
     construction — so this file reads the two kernels' OWN SOURCE and asserts
     that every word declared new is produced by one of them, and that neither
     kernel can return a word that is not declared. A word with no producer is
     the defect this repository keeps finding one plane over.

  3. THE WIRING. The daemon's four `agent.binding.*` methods, and the router's
     two seams: `session.create` records a binding, `session.send` consults it.
     A kernel nobody calls is the "complete, tested and unreachable" shape
     `tests/test_declared_implementations.py` records. The router's side of
     those two seams is
     `tests/test_agent_control_router.py::TestTheTurnIntoAThreadIsGated`.

  4. THE MIGRATION PATH. `serverosctl coordination bind|resolve|close|list`,
     run here against a daemon in this process. Every session that predates
     §17 has no binding, and under §17 an unbound thread admits nothing —
     so this group is not a convenience, it is the only way those threads get
     a purpose or a closure.

Hermetic: no socket on the real machine, no network, no Item database except
where a test builds one on purpose.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coordination = _load("serveros_runtime_coordination", "coordination.py")
store_mod = _load("serveros_runtime_agent_control_store", "agent_control_store.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")


def a_registry():
    """An empty registry. §17 needs no resources, only a state directory."""
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(
        a_registry(), state_dir=tmp_path / "state",
        socket_path=tmp_path / "run/serveros.sock", log=lambda message: None)


@pytest.fixture
def store(tmp_path):
    return store_mod.AgentControlStore(tmp_path / "store.json")


def an_item_db(state_dir: Path, **items) -> Path:
    """A `serveros.db` holding an `items_index`, at the place the daemon looks.

    Written here rather than read from the live machine, and the shape is the
    live one — `item_state` selects `state, outcome` and nothing else. A test
    that read the real database would pass or fail with somebody's work.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "serveros.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items_index (item_id TEXT, state TEXT, outcome TEXT)")
    conn.executemany("INSERT INTO items_index VALUES (?, ?, ?)",
                     [(k, v[0], v[1]) for k, v in items.items()])
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# 1 · the kernel's rules
# ---------------------------------------------------------------------------

def test_an_execution_session_without_an_item_is_refused():
    """The exact case that produced the failure this module was written for.

    LIT-0101 was DELIVERED and the same execution conversation took on
    LIT-0205. It could do that because nothing had ever said which Item it was
    for — there was no field to check and no word for the absence.
    """
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.binding(session_role="execution")
    assert caught.value.reason == "INVALID_CONTEXT"
    assert "bound_item_ref" in caught.value.detail


def test_a_coordinator_may_be_bound_to_no_item_and_only_to_no_item():
    """The one role that is legitimately unbound, and not a second execution.

    A coordinator with one Item is an execution that did not say so, and every
    rule that reads this field would then treat it as one.
    """
    assert coordination.binding(session_role="coordinator")["bound_item_ref"] is None
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.binding(session_role="coordinator", bound_item_ref="LIT-0205")
    assert caught.value.reason == "INVALID_CONTEXT"
    assert "coordinator" in caught.value.detail


def test_there_is_no_default_role():
    """A missing role is refused, not filled in.

    A default would be a guess about somebody's intent recorded as a fact, and
    the record would afterwards be evidence for the guess.
    """
    for absent in (None, "", "   ", "worker", "EXECUTION"):
        with pytest.raises(coordination.ThreadRefused) as caught:
            coordination.parse_role(absent)
        assert caught.value.reason == "INVALID_CONTEXT"


def test_a_binding_does_not_pretend_to_have_a_session_ref():
    """The ref is the provider's, and it is minted during create.

    A ref invented here to make the record look finished would be the one field
    in it that no provider ever said — and `put_binding` refuses a record
    without one, so the invention would only move the failure.
    """
    record = coordination.binding(session_role="execution", bound_item_ref="LIT-0101")
    assert "session_ref" not in record
    assert record["bound_item_ref"] == "LIT-0101"
    assert record["bound_item_ref_frozen"] is True
    assert record["state"] == "OPEN"
    assert record["schema"] == "serveros/thread-binding.v1"


def test_a_payload_that_names_two_items_is_refused_at_the_door():
    """Caught inside one payload, before a provider session exists.

    A create that named LIT-0205 while asking to be bound to LIT-0101 would
    start a provider session that is already pointing at two Items, and the
    contradiction would surface later as a mystery rather than as this.
    """
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.binding(session_role="execution", bound_item_ref="LIT-0101",
                             work_item="LIT-0205")
    assert caught.value.reason == "THREAD_ITEM_MISMATCH"


def test_the_item_axis_has_three_values_and_the_third_refuses_nothing():
    """UNKNOWN is not OPEN, and it is not a refusal either.

    A machine with no Item database would otherwise refuse every turn, which
    would make this a gate rather than a reading, and would report "nobody
    asked work-engine" as "the work is forbidden".
    """
    assert coordination.item_axis("DELIVERED") == "READ_ONLY"
    assert coordination.item_axis("CLOSED") == "READ_ONLY"
    assert coordination.item_axis("ACTIVE") == "OPEN"
    assert coordination.item_axis("NEEDS_HUMAN") == "OPEN"
    assert coordination.item_axis(None) == "UNEVALUABLE"
    assert coordination.item_axis("") == "UNEVALUABLE"
    assert coordination.item_axis("UNKNOWN") == "UNEVALUABLE"
    verdict = coordination.admit_work(
        binding_record={"session_role": "execution", "bound_item_ref": "LIT-0205",
                        "state": "OPEN"},
        item_state=None)
    assert verdict["state"] == "ADMITTED"
    assert verdict["item_axis"] == "UNEVALUABLE"


def test_needs_human_is_not_a_closure():
    """An Item parked on a person is open, and LIT-0205 is exactly that today.

    Reading a live row to decide this was the point: `LIT-0205` is
    `NEEDS_HUMAN` in `items_index` right now, and treating it as read-only
    would strand the thread a human is about to unblock.
    """
    assert "NEEDS_HUMAN" not in coordination.READ_ONLY_ITEM_STATES
    assert coordination.item_axis("NEEDS_HUMAN") == "OPEN"


def test_the_session_contract_states_what_the_kernel_implements():
    """The declaration is read, so it cannot drift from the code.

    `runtime/agent-session-contract.yaml#coordination` was written as the
    declaration of §17 in the contract a session reader opens. A declaration
    nobody reads is the shape this repository keeps finding one plane over —
    so this test is the reader, and every field it asserts is a field that
    would otherwise be prose.

    The storage pointer is checked by RESOLVING it, not by matching a string:
    a contract that names a method the store does not have is the same defect
    as a contract that names no method at all.
    """
    import yaml
    block = yaml.safe_load(
        (RUNTIME / "agent-session-contract.yaml").read_text())["coordination"]

    assert tuple(block["roles"]) == coordination.ROLES
    assert tuple(block["item_states_that_close_a_thread"]) == \
        coordination.READ_ONLY_ITEM_STATES
    assert block["module"] == "runtime/coordination.py"

    module_ref, _, method = block["storage"].partition("#")
    assert module_ref == "runtime/agent_control_store.py"
    assert hasattr(store_mod.AgentControlStore, method), (
        f"{block['storage']} names a method the store does not have")

    declared = set(_reason_contract()["values"])
    assert set(block["refuses"]) <= declared
    # The three words the kernel produces, plus the one it reuses. Nothing
    # here is spelled for the first time: if this file grew a word of its own,
    # `TransportFailure` would refuse it at construction and the caller would
    # have no branch for it.
    assert set(block["refuses"]) == {"THREAD_ITEM_MISMATCH", "READ_ONLY_CLOSURE",
                                     "THREAD_CLOSED_FOR_NEW_WORK", "INVALID_CONTEXT"}

    # And the one distinction the contract exists to make, because it is the
    # one a reader would otherwise assume: this role is not `serveros_role`.
    projection = yaml.safe_load(
        (RUNTIME / "agent-session-contract.yaml").read_text())["projection"]
    assert "serveros_role" in projection
    assert "session_role" not in projection


def test_a_delivered_item_closes_its_thread_with_its_own_word():
    """READ_ONLY_CLOSURE, and NOT THREAD_ITEM_MISMATCH.

    The repairs differ, and that is this repository's bar for adding a word:
    a caller told THREAD_ITEM_MISMATCH goes and renames an Item in its payload,
    which changes nothing — the work it was for is finished.
    """
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(
            binding_record={"session_role": "execution",
                            "bound_item_ref": "LIT-0101", "state": "OPEN"},
            work_item="LIT-0101", item_state="DELIVERED", outcome="SUCCESS")
    assert caught.value.reason == "READ_ONLY_CLOSURE"
    assert "LIT-0101" in caught.value.detail
    assert "SUCCESS" in caught.value.detail


def test_a_closed_thread_is_not_reported_as_the_provider_being_unsupported():
    """THREAD_CLOSED_FOR_NEW_WORK, and why it is not ACTION_UNSUPPORTED.

    The provider session may be alive and perfectly able to accept a turn. That
    is the state the incident left behind, and calling it "unsupported" would
    say something false about the provider — and would stop a caller from ever
    retrying.
    """
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(
            binding_record={"session_role": "execution",
                            "bound_item_ref": "LIT-0101", "state": "CLOSED",
                            "closed_at": "2026-09-14T00:00:00+00:00"})
    assert caught.value.reason == "THREAD_CLOSED_FOR_NEW_WORK"
    assert "ACTION_UNSUPPORTED" in caught.value.detail


def test_an_unbound_thread_is_refused_rather_than_read_as_unconstrained():
    """None is a refusal, not a permission.

    "nobody stated a purpose" and "any purpose is allowed" are opposite
    readings, and only one of them is safe. This is also the migration path:
    a thread created before this record existed is answered here, and the
    refusal names the command that fixes it.
    """
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(binding_record=None, work_item="LIT-0205")
    assert caught.value.reason == "INVALID_CONTEXT"
    assert "coordination bind" in caught.value.detail


def test_the_checks_run_in_the_order_the_repairs_matter_in():
    """A closed thread is reported as closed even when the caller also named
    the wrong Item.

    Order is the whole content of this test. A caller told
    THREAD_ITEM_MISMATCH would fix the Item name, retry, and only then be told
    the thread is closed — two round trips to learn one thing, and the first
    one sent them to the wrong field.
    """
    closed = {"session_role": "execution", "bound_item_ref": "LIT-0101",
              "state": "CLOSED"}
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(binding_record=closed, work_item="LIT-0205",
                                item_state="DELIVERED")
    assert caught.value.reason == "THREAD_CLOSED_FOR_NEW_WORK"

    delivered = {"session_role": "execution", "bound_item_ref": "LIT-0101",
                 "state": "OPEN"}
    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(binding_record=delivered, work_item="LIT-0205",
                                item_state="DELIVERED", outcome="SUCCESS")
    assert caught.value.reason == "READ_ONLY_CLOSURE"

    with pytest.raises(coordination.ThreadRefused) as caught:
        coordination.admit_work(
            binding_record={"session_role": "execution",
                            "bound_item_ref": "LIT-0101", "state": "OPEN"},
            work_item="LIT-0205", item_state="ACTIVE")
    assert caught.value.reason == "THREAD_ITEM_MISMATCH"
    assert "still open" in caught.value.detail
    assert "LIT-0205" in caught.value.detail and "LIT-0101" in caught.value.detail


def test_the_same_thread_admits_its_own_item_while_that_item_is_open():
    """The positive control. Without it every assertion above is satisfied by
    a function that refuses everything."""
    verdict = coordination.admit_work(
        binding_record={"session_role": "execution", "bound_item_ref": "LIT-0205",
                        "state": "OPEN"},
        work_item="LIT-0205", item_state="ACTIVE", outcome=None)
    assert verdict == {"state": "ADMITTED", "session_role": "execution",
                       "bound_item_ref": "LIT-0205", "item_axis": "OPEN",
                       "item_state": "ACTIVE", "work_item_named": "LIT-0205"}


# ---------------------------------------------------------------------------
# 2 · the vocabulary, read from the kernels' own source
# ---------------------------------------------------------------------------

def _reason_contract() -> dict:
    import yaml
    return yaml.safe_load(
        (REPO / "abilities" / "target-contract.yaml").read_text())["reasons"]


def _handed_out(node, vocabulary: set[str], found: set[str]) -> set[str]:
    """Strings inside a returned/raised expression, not descending into tests.

    Recursive rather than `ast.walk` plus a skip, and the difference is not
    cosmetic: `ast.walk` yields every descendant regardless of what the caller
    does with the nodes it passes over, so a `continue` on a `Compare` skips
    the node and still visits the word inside it. The first draft of this
    function did exactly that and a test caught it.
    """
    import ast
    if isinstance(node, ast.Compare):
        # A comparison names a word in order to TEST it, so the whole subtree
        # under it is a question rather than an answer.
        return found
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in vocabulary:
            found.add(node.value)
        return found
    for child in ast.iter_child_nodes(node):
        _handed_out(child, vocabulary, found)
    return found


def _words_handed_out(path: Path, vocabulary: set[str]) -> set[str]:
    """Which of `vocabulary` this module HANDS OUT, read from its syntax tree.

    The rule is a position, not a name, and the position is what makes it a
    producer test rather than a mention test: a word counts when it appears
    inside a `return` or a `raise` — the two ways a function gives a value
    back. Everything else is a comparison, a table, or a sentence.

    Two narrower rules were tried and both were wrong in the direction that
    makes a gate worthless:

      `raise ThreadRefused(<constant>)` only        missed all five of
        `runtime/approval.py`'s verdicts, because that kernel returns its word
        through `_verdict(...)` instead of raising it. The gate would have
        reported five produced words as unproduced, and the first person to
        look at that red would have widened the exemption rather than the rule.

      `<constant>` matched by the name of the call it sits in  is the same
        rule as above wearing a helper's name, and it fails again the next time
        somebody spells the helper differently.

    A word built at runtime — `MAP[key]` — is NOT found, and that is a known
    and accepted limit: it is stated here so that a reader knows what a green
    does not cover, rather than inferring from the green that nothing is
    missed. Both kernels spell their words literally today.
    """
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Return, ast.Raise)):
            _handed_out(node, vocabulary, found)
    return found


def test_every_word_this_kernel_returns_is_in_the_closed_vocabulary():
    """The kernel cannot refuse in a word the caller does not have.

    `TransportFailure` enforces this at construction for the transport's own
    refusals; this is the same assertion one layer down, where the words are
    first chosen. The scanner is paired with a positive control below, because
    a scanner that matches nothing satisfies this assertion.
    """
    declared = set(_reason_contract()["values"])
    produced = _words_handed_out(RUNTIME / "coordination.py", declared)
    assert produced == {"INVALID_CONTEXT", "THREAD_ITEM_MISMATCH",
                        "READ_ONLY_CLOSURE", "THREAD_CLOSED_FOR_NEW_WORK"}, (
        f"the scanner found {sorted(produced)} in runtime/coordination.py")
    assert produced <= declared


def test_the_scanner_finds_the_call_argument_and_the_returned_mapping():
    """The positive control for the rule above, plus the limit it declares.

    Both real shapes are here, and one of them is the shape that defeated the
    first draft: `raise ThreadRefused("WORD", ...)` and
    `return _verdict("WORD", ...)` are different syntax, and a rule that reads
    only the first reports the approval kernel's five words as unproduced.

    The last function records what the rule does NOT see. A word held in a
    table and returned by name — `TABLE[key]` — is invisible to it, because the
    constant is not in the `Return` subtree. Both kernels spell their words
    literally today, so the gate is complete today; the limit is asserted here
    rather than left for somebody to infer from a green.
    """
    source = (
        'def raised():\n'
        '    raise Thing("THREAD_ITEM_MISMATCH", "why")\n'
        'def returned():\n'
        '    return {"reason": "READ_ONLY_CLOSURE"}\n'
        'def compared():\n'
        '    return 1 if value == "THREAD_CLOSED_FOR_NEW_WORK" else 2\n'
        'def tabulated():\n'
        '    TABLE = {"a": "INVALID_CONTEXT"}\n'
        '    return TABLE\n')
    import ast
    vocabulary = {"THREAD_ITEM_MISMATCH", "READ_ONLY_CLOSURE",
                  "THREAD_CLOSED_FOR_NEW_WORK", "INVALID_CONTEXT"}
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Return, ast.Raise)):
            _handed_out(node, vocabulary, found)
    assert "THREAD_ITEM_MISMATCH" in found, "the raise shape was missed"
    assert "READ_ONLY_CLOSURE" in found, "the returned-mapping shape was missed"
    assert "THREAD_CLOSED_FOR_NEW_WORK" not in found, "a comparison is not a return"
    assert "INVALID_CONTEXT" not in found, (
        "the rule now sees a word returned by lookup; that is a widening, and it "
        "should be a deliberate change with this assertion updated alongside it")


def test_the_words_declared_new_are_produced_by_the_file_that_declares_them():
    """The gate on the contract's `produced_by` block, read as source.

    `abilities/target-contract.yaml` says which module produces which word.
    This reads those modules and checks it. A word in the contract with no
    producer is a vocabulary that reads as enforced because it is written
    down — the defect this repository keeps finding one plane over.
    """
    declared = set(_reason_contract()["values"])
    for module, words in _reason_contract()["produced_by"].items():
        path = REPO / module
        assert path.is_file(), f"{module} is named as a producer and does not exist"
        produced = _words_handed_out(path, declared)
        missing = sorted(set(words) - produced)
        assert not missing, f"{module} is declared to produce {missing} and cannot"
        stray = sorted(produced - declared)
        assert not stray, f"{module} hands out {stray}, which is not in the vocabulary"


def test_a_word_cannot_enter_the_vocabulary_without_a_producer():
    """The two-way equality, and both directions are needed.

    One direction alone is satisfied by an empty list. What this asserts is
    that the words the two kernels hand out which are NOT among the brief's
    fifteen are exactly the six declared here — so a kernel cannot begin
    returning a word nobody declared, and a word cannot be declared that no
    kernel returns.
    """
    contract = _reason_contract()
    declared = set(contract["values"])
    from_source: set[str] = set()
    for module in contract["produced_by"]:
        from_source |= _words_handed_out(REPO / module, declared)
    brief = declared - set(contract["declared_here_only"])
    assert len(brief) == contract["from_brief"] == 15
    assert from_source - brief == set(contract["declared_here_only"])
    assert set(contract["declared_here_only"]) == {
        "APPROVAL_CONTEXT_MISMATCH", "APPROVAL_EXPIRED",
        "APPROVAL_ALREADY_CONSUMED", "THREAD_ITEM_MISMATCH",
        "READ_ONLY_CLOSURE", "THREAD_CLOSED_FOR_NEW_WORK"}


def test_the_daemon_turns_a_store_refusal_into_a_word_the_caller_has():
    """The write point refuses in the closed vocabulary, not in its own.

    `AgentControlStore` raises `StoreError`, which is right — it is the
    persister and a persisted-record problem is not a fleet verdict. The
    daemon is where that becomes a word a caller can branch on, and this
    asserts the conversion exists rather than assuming it.
    """
    declared = set(_reason_contract()["values"])
    handed_out = _words_handed_out(RUNTIME / "daemon.py", declared)
    assert "INVALID_CONTEXT" in handed_out
    assert handed_out <= declared


def test_an_unreadable_store_refuses_rather_than_forgetting_every_binding(tmp_path):
    """A corrupt store is a refusal, not an empty one.

    This matters more for bindings than for anything else the store holds. A
    process that started with an empty store would find every thread unbound,
    and every unbound thread refuses — so the symptom would be "every session
    stopped working" with no reason anywhere. Refusing at construction puts the
    cause where a person can see it.
    """
    path = tmp_path / "store.json"
    path.write_text("{ this is not json")
    with pytest.raises(store_mod.StoreError) as caught:
        store_mod.AgentControlStore(path)
    assert "Refusing to start with an empty control store" in str(caught.value)


# ---------------------------------------------------------------------------
# 3 · the daemon's four methods
# ---------------------------------------------------------------------------

def test_a_binding_is_recorded_and_read_back_with_its_item_axis(daemon):
    """The record survives, and the Item read is reported beside it.

    `item_axis` is `UNEVALUABLE` here because this daemon's state directory
    holds no Item database — which is the honest answer and the one a caller
    has to be able to tell apart from OPEN.
    """
    recorded = daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    assert recorded["ok"] and recorded["result"]["recorded"] is True

    read = daemon.handle({"method": "agent.binding.resolve",
                          "params": {"session_ref": "session-1"}})["result"]
    assert read["found"] is True
    assert read["binding"]["bound_item_ref"] == "LIT-0101"
    assert read["item_axis"] == "UNEVALUABLE"
    assert read["provenance"]["state_db_present"] is False


def test_the_daemon_refuses_the_binding_the_router_would_refuse(daemon):
    """The write point validates its own input.

    The CLI reaches `agent.binding.record` directly. If the rules lived only at
    the MCP call site, the CLI would be a way to create a binding the MCP path
    refuses — and the two would disagree until somebody noticed.
    """
    answer = daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution"}})["result"]
    assert answer["recorded"] is False
    assert answer["reason"] == "INVALID_CONTEXT"
    assert "bound_item_ref" in answer["detail"]
    assert daemon.handle({"method": "agent.binding.resolve",
                          "params": {"session_ref": "session-1"}})["result"]["found"] is False


def test_a_thread_cannot_be_rebound_to_another_item(daemon):
    """Immutability is the store's, and it is structural.

    There is no method that can change `bound_item_ref` after the write. A
    retried create re-reports an equal record and is idempotent; a different
    one is refused, and the refusal says to start a new session rather than
    quietly doing it.
    """
    daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    again = daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})["result"]
    assert again["recorded"] is True, "a retried create must be idempotent"

    other = daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0205"}})["result"]
    assert other["recorded"] is False
    assert "Create a session for the Item you mean" in other["detail"]
    assert daemon.handle({"method": "agent.binding.resolve", "params": {
        "session_ref": "session-1"}})["result"]["binding"]["bound_item_ref"] == "LIT-0101"


def test_closing_a_thread_keeps_the_record_rather_than_deleting_it(daemon):
    """A closed thread and a thread nobody bound must stay distinguishable.

    Making them indistinguishable is precisely the reading that let the failure
    happen: an execution thread with no Item and a closed execution thread both
    answered "no binding", and the second one's closure was the fact worth
    keeping.
    """
    daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    closed = daemon.handle({"method": "agent.binding.close", "params": {
        "session_ref": "session-1", "reason": "item delivered"}})["result"]
    assert closed["closed"] is True and closed["binding"]["state"] == "CLOSED"
    assert closed["binding"]["bound_item_ref"] == "LIT-0101"

    read = daemon.handle({"method": "agent.binding.resolve",
                          "params": {"session_ref": "session-1"}})["result"]
    assert read["found"] is True
    assert read["binding"]["state"] == "CLOSED"


def test_closing_a_thread_that_was_never_bound_is_refused(daemon):
    """Closing is a statement about a record, and there is no record."""
    answer = daemon.handle({"method": "agent.binding.close",
                            "params": {"session_ref": "session-nope"}})["result"]
    assert answer["closed"] is False
    assert "never been bound" not in answer["detail"]  # the store's own wording
    assert answer["reason"] == "INVALID_CONTEXT"


def test_the_item_read_is_driven_by_the_binding_and_takes_no_item_argument():
    """There is no way to ask this surface about an Item.

    A parameter for the Item would make `agent.binding.resolve` a second
    `work.item.get`: the Item authority read again, from the node, with none of
    the rules that authority applies. A caller can ask about a thread it holds
    a ref for.
    """
    import inspect
    source = inspect.getsource(daemon_mod.Daemon._r_agent_binding_resolve)
    assert '_required(params, "session_ref")' in source
    assert "bound_item_ref" in source
    assert '_required(params, "item' not in source
    assert 'params.get("item' not in source


def _daemon_over(root: Path):
    """A daemon whose state dir follows `default_state_dir()`'s own composition.

    `<root>/runtime` for its own state and `<root>/serveros.db` for the Item
    authority — the pair the live unit produces when it sets
    `SERVEROS_STATE_DIR=<root>`. Built that way on purpose: a test that put
    the two anywhere else would be testing a layout no deployment has.
    """
    return daemon_mod.Daemon(
        a_registry(), state_dir=root / "runtime",
        socket_path=root / "run/serveros.sock", log=lambda message: None)


def test_a_live_item_state_is_read_when_the_state_directory_has_one(tmp_path):
    """The paired control for UNEVALUABLE: same call, an Item database present.

    Without this pair, `UNEVALUABLE` is satisfied by a reader that never finds
    anything — and the three-valued axis would be two values with a longer
    word for one of them.
    """
    an_item_db(tmp_path,
               **{"LIT-0101": ("DELIVERED", "SUCCESS"),
                  "LIT-0205": ("NEEDS_HUMAN", None)})
    instance = _daemon_over(tmp_path)
    instance.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    read = instance.handle({"method": "agent.binding.resolve",
                            "params": {"session_ref": "session-1"}})["result"]
    assert read["item"]["state"] == "DELIVERED"
    assert read["item"]["outcome"] == "SUCCESS"
    assert read["item"]["item_ref"] == "LIT-0101"
    assert read["item"]["source"] == "context/resolver.py#item_state"
    assert read["item_axis"] == "READ_ONLY"
    assert read["provenance"]["state_db_present"] is True


def test_an_item_parked_on_a_person_does_not_close_its_thread(tmp_path):
    """LIT-0205 is `NEEDS_HUMAN` in the live table, and that is the row this
    test is modeled on.

    The axis is read from the same reader and the answer is OPEN, which is the
    whole reason `NEEDS_HUMAN` is not in `READ_ONLY_ITEM_STATES`: refusing here
    would strand the thread a human is about to unblock.
    """
    an_item_db(tmp_path, **{"LIT-0205": ("NEEDS_HUMAN", None)})
    instance = _daemon_over(tmp_path)
    instance.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0205"}})
    read = instance.handle({"method": "agent.binding.resolve",
                            "params": {"session_ref": "session-1"}})["result"]
    assert read["item_axis"] == "OPEN"


def test_the_daemon_reads_the_database_beside_its_own_state_directory(tmp_path):
    """Not the machine's. A daemon pointed at a directory answers about it.

    This is the failure this repository has recorded under other names three
    times: something read the live world and reported the result as its own.
    """
    other = tmp_path / "other"
    an_item_db(other, **{"LIT-0101": ("DELIVERED", "SUCCESS")})
    instance = _daemon_over(tmp_path)
    instance.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    read = instance.handle({"method": "agent.binding.resolve",
                            "params": {"session_ref": "session-1"}})["result"]
    assert read["item_axis"] == "UNEVALUABLE", (
        f"this daemon read {other} instead of its own state directory")
    assert read["provenance"]["state_db"] == str(tmp_path / "serveros.db")


def test_the_binding_list_reports_roles_and_counts_never_records(daemon):
    """A status surface that cannot be asked who or which Item."""
    daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-1", "session_role": "execution",
        "bound_item_ref": "LIT-0101"}})
    daemon.handle({"method": "agent.binding.record", "params": {
        "session_ref": "session-2", "session_role": "coordinator"}})
    listing = daemon.handle({"method": "agent.binding.list", "params": {}})["result"]
    assert listing["count"] == 2
    assert listing["by_role"] == {"coordinator": ["session-2"],
                                  "execution": ["session-1"]}
    assert listing["closed"] == []
    assert "LIT-0101" not in json.dumps(listing["by_role"])


def test_the_store_refuses_a_binding_keyed_by_anything_but_a_session(store):
    """There is no actor key here, and the same test in `test_actor_context.py`
    forbids the method names that would make one.

    A binding is a statement about a session. A binding whose session nobody
    named is a rule with no subject.
    """
    with pytest.raises(store_mod.StoreError):
        store.put_binding({"schema": coordination.SCHEMA,
                           "session_role": "execution",
                           "bound_item_ref": "LIT-0101"})


def test_the_store_never_sweeps_a_binding(store):
    """Contexts and approvals are windows; a binding has no window.

    `sweep` is called by the daemon. If it took bindings with it, a closure
    would become indistinguishable from a thread that was never bound — the
    reading §17 exists to remove.
    """
    store.put_binding({"schema": coordination.SCHEMA, "session_ref": "session-1",
                       "session_role": "execution", "bound_item_ref": "LIT-0101",
                       "state": "OPEN", "recorded_at": "2000-01-01T00:00:00+00:00"})
    store.sweep(before="2099-01-01T00:00:00+00:00")
    assert store.get_binding("session-1") is not None


# ---------------------------------------------------------------------------
# 4 · the CLI.
#
# `serverosctl coordination` is the migration path: every session that existed
# before §17 has no binding, and under §17 an unbound thread admits nothing.
# Somebody has to be able to say what those threads are for, or close them,
# without pretending to be an Agent Session — and a person starting a
# coordinator thread is not one either.
#
# These tests run `cli.main` against a daemon in this process rather than a
# socket, so the whole path is exercised — the CLI's argument shaping, the
# daemon's handler, the store — without a running node and without touching
# the machine's own control store.
# ---------------------------------------------------------------------------

def _cli_with(monkeypatch, daemon):
    """`runtime/cli.py`, with the socket replaced by this daemon.

    Only the transport is replaced. `ask_daemon` exists to make the "no node
    runtime at ..." refusal unmissable, and a fixture that replaced the whole
    answer would stop exercising that — the last test here checks it directly,
    without this patch.
    """
    cli = _load("serveros_runtime_cli", "cli.py")

    def _in_process(method, params=None, **kwargs):
        reply = daemon.handle({"method": method, "params": params or {}})
        if not reply.get("ok"):
            raise AssertionError(reply.get("error"))
        return reply["result"]

    monkeypatch.setattr(cli, "ask_daemon", _in_process)
    return cli


def test_the_cli_binds_a_thread_and_reads_it_back(monkeypatch, daemon, capsys):
    cli = _cli_with(monkeypatch, daemon)
    assert cli.main(["coordination", "bind", "session-1",
                     "--role", "execution", "--item", "LIT-0101"]) == 0
    assert cli.main(["coordination", "resolve", "session-1"]) == 0
    printed = capsys.readouterr().out
    assert "role        execution" in printed
    assert "item        LIT-0101" in printed
    assert "thread      OPEN" in printed


def test_the_cli_refuses_the_binding_the_kernel_refuses(monkeypatch, daemon, capsys):
    """An execution with no Item, from a terminal.

    Exit 1, not 2: the daemon answered, and what it answered is a refusal with
    a reason word. Exit 2 means "could not look", and collapsing the two is how
    a monitor reports outages during its own failures.
    """
    cli = _cli_with(monkeypatch, daemon)
    assert cli.main(["coordination", "bind", "session-1",
                     "--role", "execution"]) == 1
    assert "INVALID_CONTEXT" in capsys.readouterr().err
    assert daemon.handle({"method": "agent.binding.list", "params": {}})["result"]["count"] == 0


def test_the_cli_cannot_rebind_a_thread_to_another_item(monkeypatch, daemon, capsys):
    """The migration path is not a rebind path.

    There is no flag on `bind` that changes which Item a thread is for, and a
    second, different binding is refused by the store rather than overwriting
    the first — which is what makes immutability structural rather than a
    sentence.
    """
    cli = _cli_with(monkeypatch, daemon)
    assert cli.main(["coordination", "bind", "session-1",
                     "--role", "execution", "--item", "LIT-0101"]) == 0
    assert cli.main(["coordination", "bind", "session-1",
                     "--role", "execution", "--item", "LIT-0205"]) == 1
    assert "Create a session for the Item you mean" in capsys.readouterr().err
    stored = daemon.control_store().get_binding("session-1")
    assert stored["bound_item_ref"] == "LIT-0101"


def test_the_cli_closes_a_thread_and_the_record_survives_it(monkeypatch, daemon, capsys):
    cli = _cli_with(monkeypatch, daemon)
    cli.main(["coordination", "bind", "session-1",
              "--role", "execution", "--item", "LIT-0101"])
    assert cli.main(["coordination", "close", "session-1",
                     "--reason", "delivered"]) == 0
    printed = capsys.readouterr().out
    assert "item kept   LIT-0101" in printed
    assert daemon.control_store().get_binding("session-1")["state"] == "CLOSED"


def test_a_cli_binding_is_one_the_router_would_accept(monkeypatch, daemon):
    """The two write paths agree, asserted rather than hoped for.

    `agent.binding.record` is what the MCP loopback calls during create and
    what this CLI calls directly. A CLI able to write a binding the router
    would refuse would be a second, weaker door into the same store.
    """
    cli = _cli_with(monkeypatch, daemon)
    cli.main(["coordination", "bind", "session-1",
              "--role", "coordinator"])
    record = daemon.control_store().get_binding("session-1")
    assert coordination.binding(
        session_role=record["session_role"],
        bound_item_ref=record["bound_item_ref"])["session_role"] == "coordinator"


def test_the_cli_says_it_cannot_answer_when_there_is_no_daemon(monkeypatch, tmp_path, capsys):
    """The unpatched path, and the difference between exit 2 and exit 1.

    A command that quietly answered from somewhere else when the control plane
    was down would be worse than one that says it cannot answer.
    """
    cli = _load("serveros_runtime_cli", "cli.py")
    monkeypatch.setattr(cli, "daemon_socket_path",
                        lambda: tmp_path / "nobody-is-listening.sock")
    assert cli.main(["coordination", "list"]) == 2
    assert "no node runtime at" in capsys.readouterr().err
