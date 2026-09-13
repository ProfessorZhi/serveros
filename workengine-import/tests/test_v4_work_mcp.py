"""Tests for serveros-work-mcp — the Workengine control surface.

§15 and §16, and one property that is not in either: the surface must be
unable to write down an actor it did not verify. Everything an agent can say
about itself is a string; a `context_ref` is the only thing it can present that
ServerOS resolved beforehand.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path

import workengine
from workengine import mcp
from workengine.model import RunCancelReason, RunState
from workengine.store import ActiveWorkerAckRequired, ServerOSStore


class FakeContexts:
    """A resolver, and the record it found."""

    def __init__(self, actor="human:hwz", found=True, raises=None):
        self.actor = actor
        self.found = found
        self.raises = raises
        self.asked = []

    def __call__(self, ref):
        self.asked.append(ref)
        if self.raises is not None:
            raise self.raises
        return {"found": self.found, "actor_ref": self.actor, "context_ref": ref,
                "because": "no such context"}


class WorkMcpTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.store = ServerOSStore(self.root / "state")
        self.store.register_project("demo", "DEM")
        self.item = self.store.create_item(
            workengine.WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.store.add_activity(workengine.Activity(self.item.id, "test", "test"))
        self.contexts = FakeContexts()
        self.client = mcp.WorkControlClient(store=self.store,
                                            resolve_context=self.contexts)

    def tearDown(self):
        self.td.cleanup()

    def run_at(self, state, key="k"):
        run = self.store.begin_run(self.item.id, "test", "worker", key,
                                   self.root / "artifacts")
        for step in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
            if run.state == state:
                return run
            run = self.store.advance_run(self.item.id, run.id, step)
        if run.state != state:
            raise AssertionError(f"fixture cannot reach {state.value}")
        return run

    def payload(self, name, arguments):
        response = mcp.WorkMcpServer(client=self.client).handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        text = response["result"]["content"][0]["text"]
        return response["result"]["isError"], json.loads(text)

    def events(self, event_type="run_terminated"):
        return [e for e in self.store.item_events(self.item.id)
                if e["event_type"] == event_type]


class TestTheActorIsNeverSelfReported(WorkMcpTest):
    def test_an_actor_argument_is_refused_rather_than_ignored(self):
        """Ignoring it would let a caller believe its name was recorded."""
        run = self.run_at(RunState.ALLOCATED)
        response = mcp.WorkMcpServer(client=self.client).handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "work.run.cancel", "arguments": {
                "item": self.item.id, "run": run.id, "reason": "r",
                "context_ref": "ctx:whatever", "actor": "human:somebody",
            }},
        })
        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("unexpected=['actor']", text)
        # And the refusal is total: nothing was written under the name.
        self.assertEqual(self.store.get_run(self.item.id, run.id).state, RunState.ALLOCATED)
        self.assertEqual(self.events(), [])

    def test_no_context_resolver_means_no_mutation(self):
        # The default, and the one a deployment hits before it is wired.
        client = mcp.WorkControlClient(store=self.store)
        run = self.run_at(RunState.ALLOCATED)
        result = client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:x",
        })
        self.assertEqual(result["state"], "REFUSED")
        self.assertEqual(result["reason_code"], "UNTRUSTED_ACTOR")
        self.assertIn("no trusted-context resolver", result["detail"])
        self.assertEqual(self.store.get_run(self.item.id, run.id).state, RunState.ALLOCATED)

    def test_an_empty_context_ref_is_not_an_actor(self):
        result = self.client.actor_for("")
        self.assertEqual(result["reason_code"], "UNTRUSTED_ACTOR")
        self.assertIn("caller's word is not one", result["detail"])

    def test_a_context_that_does_not_resolve_is_refused(self):
        client = mcp.WorkControlClient(store=self.store,
                                       resolve_context=FakeContexts(found=False))
        result = client.actor_for("ctx:not-minted")
        self.assertEqual(result["reason_code"], "UNTRUSTED_ACTOR")
        self.assertIn("no such context", result["detail"])

    def test_a_context_without_an_actor_is_refused(self):
        client = mcp.WorkControlClient(store=self.store,
                                       resolve_context=FakeContexts(actor=""))
        self.assertEqual(client.actor_for("ctx:x")["reason_code"], "UNTRUSTED_ACTOR")

    def test_a_resolver_that_raises_is_a_refusal_not_a_traceback(self):
        client = mcp.WorkControlClient(store=self.store,
                                       resolve_context=FakeContexts(raises=RuntimeError("down")))
        result = client.actor_for("ctx:x")
        self.assertEqual(result["reason_code"], "UNTRUSTED_ACTOR")
        self.assertIn("RuntimeError", result["detail"])

    def test_the_recorded_actor_is_the_resolved_one(self):
        run = self.run_at(RunState.ALLOCATED)
        result = self.client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": run.id, "reason": "scope moved",
            "context_ref": "ctx:real",
        })
        self.assertEqual(result["actor"], "human:hwz")
        self.assertEqual(self.events()[0]["payload"]["actor"], "human:hwz")
        self.assertEqual(self.contexts.asked, ["ctx:real"])


class TestDryRunPlansWithoutWriting(WorkMcpTest):
    def test_a_dry_run_returns_the_plan_and_writes_nothing(self):
        run = self.run_at(RunState.FINALIZING)
        before = self.store.get_run(self.item.id, run.id).state
        _is_error, plan = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "dry_run": True,
        })
        for field in ("current_state", "target_state", "reason_code",
                      "active_worker_risk", "would_transition", "would_write_event"):
            self.assertIn(field, plan, f"§15 requires {field} in a dry-run plan")
        self.assertEqual(plan["current_state"], "FINALIZING")
        self.assertEqual(plan["target_state"], "CANCELLED")
        self.assertTrue(plan["would_transition"])
        self.assertFalse(plan["would_write_event"])
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["state"], "PLANNED")

        self.assertEqual(self.store.get_run(self.item.id, run.id).state, before)
        self.assertEqual(self.events(), [])

    def test_a_real_run_writes_the_event_the_dry_run_promised(self):
        run = self.run_at(RunState.FINALIZING)
        _is_error, plan = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "dry_run": True,
        })
        _is_error, done = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real",
        })
        self.assertEqual(done["state"], "TERMINATED")
        self.assertEqual(done["current_state"], plan["current_state"])
        self.assertEqual(done["target_state"], plan["target_state"])
        self.assertTrue(done["would_write_event"])
        self.assertFalse(done["dry_run"])
        self.assertEqual(self.store.get_run(self.item.id, run.id).state, RunState.CANCELLED)
        self.assertEqual(len(self.events()), 1)

    def test_abandon_records_its_own_reason_code(self):
        run = self.run_at(RunState.PREPARING)
        result = self.client.terminate(operation="abandon", arguments={
            "item": self.item.id, "run": run.id, "reason": "given up",
            "context_ref": "ctx:real",
        })
        self.assertEqual(result["reason_code"], RunCancelReason.ABANDONED.value)
        self.assertEqual(self.events()[0]["payload"]["operation"], "abandon")

    def test_a_terminated_run_is_not_promised_a_second_transition(self):
        run = self.run_at(RunState.FINALIZING)
        self.client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": run.id, "reason": "r", "context_ref": "ctx:real"})
        _is_error, again = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "dry_run": True})
        self.assertFalse(again["would_transition"])
        self.assertFalse(again["would_write_event"])
        self.assertEqual(again["state"], "PLANNED")

    def test_a_run_is_not_restated_as_a_different_ending(self):
        run = self.run_at(RunState.FINALIZING)
        self.client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": run.id, "reason": "r", "context_ref": "ctx:real"})
        _is_error, refused = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "target": "INVALID"})
        self.assertEqual(refused["state"], "REFUSED")
        self.assertEqual(refused["reason_code"], "INVALID_CONTEXT")
        self.assertEqual(len(self.events()), 1)


class TestActiveWorkerIsAnExplicitAcknowledgement(WorkMcpTest):
    def test_terminating_a_running_run_is_refused_by_default(self):
        run = self.run_at(RunState.RUNNING)
        _is_error, refused = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "dry_run": True})
        self.assertEqual(refused["state"], "REFUSED")
        self.assertEqual(refused["reason_code"], "ACTIVE_WORKER_ACK_REQUIRED")
        self.assertTrue(refused["active_worker_risk"])
        self.assertFalse(refused["would_transition"])
        self.assertEqual(refused["resume_with"], "acknowledge_active_worker=true")
        self.assertEqual(self.store.get_run(self.item.id, run.id).state, RunState.RUNNING)
        self.assertEqual(self.events(), [])

    def test_the_acknowledgement_lets_it_through_and_says_what_it_means(self):
        run = self.run_at(RunState.RUNNING)
        _is_error, done = self.payload("work.run.cancel", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "acknowledge_active_worker": True})
        self.assertEqual(done["state"], "TERMINATED")
        self.assertTrue(done["active_worker_risk"])
        self.assertTrue(done["does_not_stop_the_worker"])
        self.assertEqual(self.events()[0]["payload"]["runtime_may_still_be_active"], True)

    def test_the_refusal_keeps_its_own_type_for_callers_below_the_surface(self):
        # The MCP reports a code; the store raises a type. Both must name the
        # same condition, or the surface would be translating a different one.
        run = self.run_at(RunState.RUNNING)
        with self.assertRaises(ActiveWorkerAckRequired) as caught:
            self.store.cancel_run(self.item.id, run.id, reason="r", actor="a")
        self.assertEqual(caught.exception.reason_code, "ACTIVE_WORKER_ACK_REQUIRED")
        self.assertIsInstance(caught.exception, ValueError)

    def test_the_plan_says_the_control_plane_holds_no_worker_handle(self):
        run = self.run_at(RunState.RUNNING)
        _is_error, plan = self.payload("work.run.abandon", {
            "item": self.item.id, "run": run.id, "reason": "r",
            "context_ref": "ctx:real", "acknowledge_active_worker": True, "dry_run": True})
        self.assertTrue(plan["does_not_stop_the_worker"])
        self.assertIn("No worker process was signalled", plan["note"])


class TestReads(WorkMcpTest):
    def test_an_unknown_item_is_absent_rather_than_invented(self):
        _is_error, payload = self.payload("work.item.get", {"item": "DEM-9999"})
        self.assertEqual(payload["state"], "NOT_FOUND")
        self.assertNotIn("title", payload)

    def test_an_unknown_run_is_absent_rather_than_invented(self):
        _is_error, payload = self.payload("work.run.get",
                                          {"item": self.item.id, "run": "RUN-9999"})
        self.assertEqual(payload["state"], "NOT_FOUND")

    def test_list_runs_is_not_quietly_filtered_to_live_runs(self):
        live = self.run_at(RunState.RUNNING, key="live")
        done = self.run_at(RunState.FINALIZING, key="done")
        self.client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": done.id, "reason": "r", "context_ref": "ctx:real"})
        _is_error, payload = self.payload("work.run.list", {"item": self.item.id})
        states = {run["run"]: run["state"] for run in payload["runs"]}
        self.assertEqual(payload["count"], 2)
        self.assertEqual(states, {live.id: "RUNNING", done.id: "CANCELLED"})

    def test_list_runs_can_be_narrowed_by_state(self):
        run = self.run_at(RunState.RUNNING, key="live")
        _is_error, payload = self.payload("work.run.list",
                                          {"item": self.item.id, "state": "RUNNING"})
        self.assertEqual([r["run"] for r in payload["runs"]], [run.id])

    def test_events_says_what_the_ledger_does_not_cover(self):
        """The whole point: an empty list must not read as "nothing happened"."""
        run = self.run_at(RunState.RUNNING)
        _is_error, payload = self.payload("work.run.events",
                                          {"item": self.item.id, "run": run.id})
        self.assertEqual(payload["events"], [])
        self.assertEqual(payload["state"], "RUNNING")
        self.assertIn("not_recorded", payload)
        self.assertIn("does not mean the Run did nothing", payload["not_recorded"])
        self.assertEqual(payload["recorded_event_types"], [])

    def test_events_reports_the_termination_once_it_exists(self):
        run = self.run_at(RunState.FINALIZING)
        self.client.terminate(operation="cancel", arguments={
            "item": self.item.id, "run": run.id, "reason": "r", "context_ref": "ctx:real"})
        _is_error, payload = self.payload("work.run.events",
                                          {"item": self.item.id, "run": run.id})
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["recorded_event_types"], ["run_terminated"])
        self.assertEqual(payload["events"][0]["payload"]["actor"], "human:hwz")

    def test_events_for_an_unknown_run_is_refused(self):
        with self.assertRaises(KeyError):
            self.client.run_events(self.item.id, "RUN-9999")


class TestTheProtocolSurface(WorkMcpTest):
    def test_the_declared_store_verbs_exist(self):
        # §15's spelling is the surface's spelling plus a prefix. A mapping
        # entry for a verb that does not exist is the drift this checks for —
        # and §15 does name one, `abandon_run`, which the store has never had.
        for surface_name, verb in mcp.STORE_VERBS.items():
            self.assertIn(surface_name, mcp.tool_names())
            self.assertTrue(hasattr(ServerOSStore, verb),
                            f"{surface_name} declares store verb {verb}, which does not exist")

    def test_abandon_is_a_reason_code_not_a_second_entry_point(self):
        self.assertEqual(mcp.STORE_VERBS["work.run.abandon"],
                         mcp.STORE_VERBS["work.run.cancel"])
        run = self.run_at(RunState.PREPARING)
        via_abandon = self.client.terminate(operation="abandon", arguments={
            "item": self.item.id, "run": run.id, "reason": "r", "context_ref": "ctx:real"})
        self.assertEqual(via_abandon["reason_code"], RunCancelReason.ABANDONED.value)

    def test_the_served_version_is_the_package_version(self):
        # One source, checked, rather than a literal here that nobody updates.
        server = mcp.WorkMcpServer(client=self.client)
        reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {}})
        self.assertEqual(reply["result"]["serverInfo"]["version"], workengine.__version__)

    def test_an_unknown_tool_names_the_ones_that_exist(self):
        response = mcp.WorkMcpServer(client=self.client).handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "work.run.kill", "arguments": {}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("work.run.cancel", response["result"]["content"][0]["text"])

    def test_no_tool_can_express_a_shell_or_a_removal(self):
        for tool in mcp.TOOLS:
            with self.subTest(tool=tool["name"]):
                for forbidden in ("shell", "exec", "command", "rm", "stop_process"):
                    self.assertNotIn(forbidden, tool["name"])
                self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_the_transport_is_line_delimited_json_rpc(self):
        client = mcp.WorkControlClient(store=self.store, resolve_context=self.contexts)
        instream = io.StringIO("\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            "not json",
        ]) + "\n")
        outstream = io.StringIO()
        mcp.serve(instream=instream, outstream=outstream,
                  server=mcp.WorkMcpServer(client=client))
        lines = [json.loads(line) for line in outstream.getvalue().splitlines()]
        self.assertEqual([line.get("id") for line in lines], [1, 2, None])
        self.assertEqual(lines[0]["result"]["serverInfo"]["name"], "serveros-work-mcp")
        self.assertEqual(len(lines[1]["result"]["tools"]), len(mcp.TOOLS))
        self.assertEqual(lines[2]["error"]["code"], -32700)

    def test_list_tools_prints_every_tool(self):
        out = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(out):
            self.assertEqual(mcp.main(["--list-tools"]), 0)
        text = out.getvalue()
        for name in mcp.tool_names():
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
