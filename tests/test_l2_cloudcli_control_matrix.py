"""The L2 capability-gap matrix, checked against the device and against the code.

What these tests are for
------------------------
`integrations/fleet-l2-control/matrix.py` claims, for each of the semantics an
operator needs, what the local code does and on what native evidence. Both
halves of such a claim rot independently: the device's `tools/list` changes
between CloudCLI releases, and a symbol here is renamed. So nothing in this file
restates the claims — it RESOLVES them:

* every native reference is looked up in `EVIDENCE-1.41.0.json`, which
  `evidence.py` derives from #411's verbatim captures and which is re-derived
  and compared here whenever those captures are on disk;
* every Fleet reference is looked up in the source tree by AST;
* an `UNSUPPORTED` row must name the verbs it looked for, and none of them may
  be in the device's declared set;
* the gaps themselves are PROVEN by calling the real code offline, so a gap that
  closes turns red and has to be re-worded rather than quietly going stale.

Deliberately absent: any hard-coded verb count. The sets are compared to each
other; a number that agrees with today's device and not tomorrow's is the thing
this file exists to stop.

Every test here is offline. Nothing connects, starts, installs, or reads a
credential; the provider is driven through a recorder in place of its HTTP
client, and the only "device" answers are the shapes the capture recorded.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "integrations" / "fleet-l2-control"
EVIDENCE_PATH = PACKAGE / "EVIDENCE-1.41.0.json"

#: Where #411's captures live when they are on this disk. The fixture is
#: generated from them; when they are absent (which is the case on a plain
#: checkout of this branch) the comparison skips and the CODE-side assertions
#: below still run.
CAPTURE_DIRS = (
    ROOT / "tests" / "data",
    ROOT / "docs" / "compat",
    Path("D:/projects/serveros-compat-win/tests/data"),
)


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
    return module


matrix = _load("l2_control_matrix", PACKAGE / "matrix.py")
evidence = _load("l2_control_evidence", PACKAGE / "evidence.py")

EVIDENCE = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))


# -- resolving a claim ------------------------------------------------------

def _module_tree(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _named(node: ast.AST, name: str) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, ast.Assign):
        return any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    if isinstance(node, ast.AnnAssign):
        return isinstance(node.target, ast.Name) and node.target.id == name
    return False


def resolve_symbol(relative: str, dotted: str) -> bool:
    """Is `dotted` a def/class/assignment in that file, one level at a time?"""
    parts = dotted.split(".")
    tree = _module_tree(relative)
    body = tree.body
    for index, part in enumerate(parts):
        found = [node for node in body if _named(node, part)]
        if not found:
            return False
        if index == len(parts) - 1:
            return True
        node = found[0]
        if not isinstance(node, ast.ClassDef):
            return False
        body = node.body
    return False


def native_reference(ref: tuple) -> dict:
    """Resolve one native evidence reference against the fixture."""
    kind = ref[0]
    if kind == "tool":
        name = ref[1]
        assert name in EVIDENCE["tools"], f"the device does not declare {name!r}"
        return EVIDENCE["tools"][name]
    if kind == "tool_property":
        tool, prop = ref[1], ref[2]
        assert tool in EVIDENCE["tools"], f"the device does not declare {tool!r}"
        shape = EVIDENCE["tools"][tool]
        assert prop in shape["properties"], f"{tool} declares no {prop!r}"
        return shape
    if kind == "case":
        name = ref[1]
        assert name in EVIDENCE["cases"], f"no captured roundtrip named {name!r}"
        return EVIDENCE["cases"][name]
    if kind in ("tool_in_release", "tool_not_in_release"):
        tool, release = ref[1], ref[2]
        declared = EVIDENCE["declared_by_release"]
        assert release in declared, f"no tools/list capture for release {release!r}"
        present = tool in declared[release]
        if kind == "tool_in_release":
            assert present, f"{release} does not declare {tool!r}"
        else:
            assert not present, (
                f"{release} DOES declare {tool!r} — the row's absence claim is "
                f"stale")
        return {"tool": tool, "release": release, "declared": present}
    raise AssertionError(f"unknown evidence kind {kind!r}")


# -- the matrix is internally honest ---------------------------------------

def test_the_status_and_device_vocabularies_are_closed():
    for entry in matrix.ROWS:
        assert entry["status"] in matrix.STATUSES, entry["id"]
        assert entry["device"] in matrix.DEVICE_TIERS, entry["id"]


def test_every_row_cites_native_evidence_the_device_actually_declares():
    """A row with no evidence is an opinion.

    The reference is resolved, not merely spelled: the tool must be in the
    device's declared set and a named argument must be in that tool's schema.
    """
    for entry in matrix.ROWS:
        assert entry["native"], entry["id"]
        for ref in entry["native"]:
            native_reference(ref)


def test_every_fleet_pointer_still_exists():
    for entry in matrix.ROWS:
        for relative, symbol in entry["fleet"]:
            assert (ROOT / relative).is_file(), f"{entry['id']}: {relative} is gone"
            assert resolve_symbol(relative, symbol), (
                f"{entry['id']}: {relative}::{symbol} no longer resolves — "
                f"re-point the row or re-word it, do not delete the row")


def test_a_named_tool_is_a_tool_the_surface_serves():
    from fleet_mcp.tools import SERVED_BY_NAME

    for entry in matrix.ROWS:
        name = entry.get("tool")
        if name is None:
            continue
        assert name in SERVED_BY_NAME, f"{entry['id']}: {name} is not served"


def test_a_row_that_is_not_supported_says_why():
    for entry in matrix.ROWS:
        if entry["status"] == "SUPPORTED":
            continue
        assert entry.get("gap", "").strip(), f"{entry['id']} claims a gap and states none"


def test_an_unsupported_row_names_verbs_that_are_genuinely_absent():
    """`UNSUPPORTED` is a claim about the DEVICE, so it is re-checked there.

    Not "I could not find it in the source": the candidate native names are
    looked up in the device's own declared set, so a future CloudCLI that adds
    one of them turns this row red instead of leaving a stale 'does not exist'.
    """
    for entry in matrix.ROWS:
        if entry["status"] != "UNSUPPORTED":
            continue
        searched = matrix.native_verbs_searched(entry)
        assert searched, f"{entry['id']}: an absent-claim must name what it searched"
        present = sorted(name for name in searched if name in EVIDENCE["tools"])
        assert not present, (
            f"{entry['id']} claims no native verb exists, but the device declares "
            f"{present} — the gap has changed shape")


def test_the_document_is_a_rendering_of_this_table():
    """`CAPABILITY-MATRIX.md` cannot drift from the data it claims to show."""
    rendered = matrix.render_markdown(EVIDENCE)
    committed = (PACKAGE / "CAPABILITY-MATRIX.md").read_text(encoding="utf-8")
    assert committed.strip() == rendered.strip(), (
        "CAPABILITY-MATRIX.md is stale; run "
        "`python integrations/fleet-l2-control/matrix.py --render`")


# -- the evidence is the device's, not ours --------------------------------

def _capture_dir() -> Path | None:
    for candidate in CAPTURE_DIRS:
        if ((candidate / evidence.TOOLS_NAME).is_file()
                and (candidate / evidence.CASES_NAME).is_file()):
            return candidate
    return None


def test_the_fixture_is_derived_from_captures_and_not_from_the_tree():
    """Re-derive from the verbatim captures and compare, when they are here.

    This is the bidirectional half: the fixture above is what the CODE-side
    tests read, and it is checked against the device's own capture rather than
    being believed. When the captures are on disk and the fixture disagrees, the
    fixture is wrong.
    """
    directory = _capture_dir()
    if directory is None:
        pytest.skip("CloudCLI 1.41.0 captures are not on this checkout "
                    "(they live on #411's branch)")
    derived = evidence.derive(directory)
    assert derived == EVIDENCE


def test_fleets_recorded_native_set_is_the_devices_declared_set():
    """No count is written down: the two SETS are compared.

    A verb the device gained appears here as unrecorded; a verb it lost appears
    as recorded-but-absent. Either way the surface's closed set is stale and this
    fails by name.
    """
    from fleet_mcp import cloudcli_native

    recorded = set(cloudcli_native.NATIVE_BY_NAME)
    declared = set(EVIDENCE["tools"])
    assert recorded == declared, {
        "recorded_but_not_declared": sorted(recorded - declared),
        "declared_but_not_recorded": sorted(declared - recorded),
    }


def test_the_count_constant_agrees_with_both_sides():
    """The one place a number IS written down is guarded in both directions."""
    from fleet_mcp import cloudcli_native

    assert cloudcli_native.NATIVE_TOOL_COUNT == len(cloudcli_native.NATIVE_BY_NAME)
    assert cloudcli_native.NATIVE_TOOL_COUNT == len(EVIDENCE["tools"])


def test_every_recorded_verb_is_mirrored_under_the_fleet_prefix():
    from fleet_mcp.tools import NATIVE_MIRROR_TOOLS

    mirrored = {tool.name for tool in NATIVE_MIRROR_TOOLS}
    expected = {f"fleet_cloudcli_{name}" for name in EVIDENCE["tools"]}
    assert mirrored == expected


def test_the_served_surface_has_no_duplicate_names():
    from fleet_mcp.tools import SERVED_BY_NAME, SERVED_TOOLS

    assert len(SERVED_TOOLS) == len(SERVED_BY_NAME)


# -- the gaps, proven by calling the code ----------------------------------

@pytest.fixture()
def provider():
    """A real `CloudCliProvider` whose transport is a recorder, not a socket."""
    from fleet_mcp.providers.cloudcli import CloudCliProvider

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []
            self.answer: dict = {"structuredContent": {}}

        def tools_call(self, name: str, arguments: dict) -> dict:
            self.calls.append((name, arguments))
            return self.answer

        def tools_list(self) -> list[dict]:
            return []

    class Offline(CloudCliProvider):
        def __init__(self) -> None:
            super().__init__(SimpleNamespace(device_ref="device:d1"))
            self.recorder = Recorder()

        def _client(self):
            return self.recorder

    return Offline()


def test_a_run_read_addresses_a_run_and_never_a_delivery(provider):
    """`run.get` / `run.identity`: the native half is wider than what is sent.

    The device accepts `runId` OR `deliveryId` (and a legacy `run` alias) and
    answers an `idKind`. This surface sends `runId` and nothing else, so the
    measured claim in the matrix is the arguments recorded here — not a reading
    of the source.
    """
    from fleet_mcp.identity import SessionKey

    key = SessionKey.compose("device:d1", "claude", "native-session")
    provider.recorder.answer = {"structuredContent": {"status": "completed", "runId": "r-1"}}
    provider.get_run(key, "r-1")

    tool, arguments = provider.recorder.calls[0]
    assert tool == "run_get"
    assert set(arguments) == {"runId", "session", "wait"}
    assert "deliveryId" not in arguments

    declared = EVIDENCE["tools"]["run_get"]["properties"]
    assert {"runId", "deliveryId"} <= set(declared), (
        "the device no longer accepts both id kinds; the matrix row must change")


def test_a_create_never_invents_a_model_it_was_not_given(provider):
    """`sessions.create.model`: omitted stays omitted all the way to the device."""
    provider.recorder.answer = {"structuredContent": {"sessionId": "s-1"}}
    provider.create(project="p-1", message="hello", model=None, permission_mode=None)
    _, arguments = provider.recorder.calls[0]
    assert "model" not in arguments, "an omitted model was forwarded as a value"
    assert "permissionMode" not in arguments


def test_a_named_model_travels_under_the_devices_own_argument_name(provider):
    provider.recorder.answer = {"structuredContent": {"sessionId": "s-1"}}
    provider.create(project="p-1", model="a-model-id", permission_mode="plan")
    _, arguments = provider.recorder.calls[0]
    assert arguments["model"] == "a-model-id"
    assert arguments["permissionMode"] == "plan"
    assert "permission_mode" not in arguments


def test_a_model_or_mode_the_project_grant_does_not_list_is_refused_locally():
    """The ceiling is empty by default, and empty means 'you may name neither'."""
    from fleet_mcp.config import CreateGrant
    from fleet_mcp.errors import FleetRefusal
    from fleet_mcp.fleet import FleetService

    empty = CreateGrant()
    for what, requested in (("model", "gpt-whatever"),
                            ("permission_mode", "bypassPermissions")):
        values = {"model": None, "permission_mode": None}
        values[what] = requested
        with pytest.raises(FleetRefusal) as caught:
            FleetService._may_name_mode_and_model(
                None, "device:d1", "p-1", empty, **values)
        assert caught.value.code == "WRITE_NOT_GRANTED"
        assert caught.value.extra["allowed"] == []
        assert caught.value.extra["requested"] == requested

    granted = CreateGrant(permission_modes=("plan",), models=("a-model-id",))
    FleetService._may_name_mode_and_model(
        None, "device:d1", "p-1", granted,
        model="a-model-id", permission_mode="plan")
    assert matrix.row("sessions.create.model")["status"] == "SUPPORTED"


def test_a_run_not_found_keeps_its_code_and_loses_the_devices_reason():
    """`run.not_found`: not-a-success holds; not-a-transient does not.

    The envelope fed here is the one the capture recorded, minus its prose:
    `{code, retryable: false, details: {reason: never_issued, bootId, idKind}}`.
    """
    from fleet_mcp.errors import FleetRefusal
    from fleet_mcp.mcp_http import structured

    shape = EVIDENCE["cases"]["run_get_delivery_miss"]
    assert shape["code"] == "RUN_NOT_FOUND" and shape["retryable"] is False
    assert "reason" in shape["details_keys"]

    with pytest.raises(FleetRefusal) as caught:
        structured({
            "isError": True,
            "content": [{"type": "text", "text": "This deliveryId was never issued."}],
            "structuredContent": {
                "code": "RUN_NOT_FOUND",
                "retryable": False,
                "details": {"reason": "never_issued", "idKind": "delivery",
                            "bootId": "boot-1"},
            },
        })
    assert caught.value.code == "RUN_NOT_FOUND", "a miss must not read as success"
    assert caught.value.extra == {}, (
        "the device's `retryable: false` and `details.reason` survived after all — "
        "update the `run.not_found` row instead of leaving it pessimistic")
    assert "never_issued" not in caught.value.detail


def test_a_permission_mode_refusal_keeps_the_code_and_drops_the_supported_set():
    """`sessions.mode` / `models.enumerate`: the device volunteers a set only here."""
    from fleet_mcp.errors import FleetRefusal
    from fleet_mcp.mcp_http import structured

    shape = EVIDENCE["cases"]["reconfigure_unsupported_mode"]
    assert shape["code"] == "UNSUPPORTED_PERMISSION_MODE"
    assert "supported" in shape["details_keys"], (
        "the device stopped telling callers which modes it supports; the "
        "`sessions.mode` row must be re-worded")

    with pytest.raises(FleetRefusal) as caught:
        structured({
            "isError": True,
            "content": [{"type": "text", "text": "does not support that mode"}],
            "structuredContent": {
                "code": "UNSUPPORTED_PERMISSION_MODE",
                "retryable": False,
                "details": {"supported": ["default", "auto", "acceptEdits",
                                          "bypassPermissions", "plan"]},
            },
        })
    assert caught.value.code == "BAD_REQUEST"
    assert "UNSUPPORTED_PERMISSION_MODE" in caught.value.detail
    assert "acceptEdits" not in caught.value.detail, (
        "the supported set now reaches the caller — close the `sessions.mode` gap")


def test_a_receipt_says_accepted_without_the_device_having_said_it():
    """`send.ack`: the word exists locally, and the device never uses it."""
    from fleet_mcp.dispatch import delivery_receipt
    from fleet_mcp.identity import SessionKey

    for shape in EVIDENCE["cases"].values():
        assert "accepted" not in shape.get("result_keys", []), (
            "a captured native answer now carries `accepted`; the receipt's "
            "provenance note can be improved")

    key = SessionKey.compose("device:d1", "claude", "native-session")
    receipt = delivery_receipt(
        {"session": key.as_dict(), "run_id": "r-1", "queued_message_uuid": None})
    assert receipt["accepted"] is True
    assert "deliveryId" not in receipt and "queued" not in receipt

    declared = EVIDENCE["cases"]["send_resident_queued"]["result_keys"]
    assert {"deliveryId", "queued", "blockedBy"} <= set(declared), (
        "the send ack no longer carries these; re-word the `send.ack` row")


def test_a_create_that_started_no_run_does_not_report_one(tmp_path):
    """`sessions.create`: "created" and "started" are different words.

    The device answers `{sessionId}` alone when no first message was given, and
    `{sessionId, deliveryId, runId}` when one was. Both shapes are asserted, so a
    create that starts nothing can never be read as a run in flight — the ACK
    versus start distinction the batch tracker depends on.
    """
    from test_fleet_create_session import TOKYO, Harness as CreateHarness

    harness = CreateHarness(tmp_path)
    harness.providers[TOKYO].create = lambda **kwargs: {"sessionId": "new-session"}
    created = harness.service.create_session(
        harness.principal(), device=TOKYO, project="p-1")
    assert created["run_id"] is None
    assert created["permission_readiness"]["run_started"] is False
    assert created["created"]["queued_message_uuid"] is None

    harness.providers[TOKYO].create = lambda **kwargs: {
        "sessionId": "new-session", "deliveryId": "d-1", "runId": "r-1"}
    started = harness.service.create_session(
        harness.principal(), device=TOKYO, project="p-1", message="do one thing")
    assert started["run_id"] == "r-1"
    assert started["permission_readiness"]["run_started"] is True
    assert "deliveryId" not in started["created"], (
        "the create receipt now carries the device's deliveryId; widen the "
        "`send.ack` row's note about which acknowledgement fields are dropped")


def test_a_model_the_project_grant_does_not_list_never_reaches_the_device(tmp_path):
    """Refused here, before a session exists — which is the only useful moment."""
    from fleet_mcp.errors import FleetRefusal
    from test_fleet_create_session import TOKYO, Harness as CreateHarness

    harness = CreateHarness(tmp_path)
    asked: list[dict] = []
    harness.providers[TOKYO].create = lambda **kwargs: asked.append(kwargs) or {
        "sessionId": "should-not-exist"}

    with pytest.raises(FleetRefusal) as caught:
        harness.service.create_session(
            harness.principal(), device=TOKYO, project="p-1",
            message="do one thing", model="a-model-not-in-the-binding")
    assert caught.value.code == "WRITE_NOT_GRANTED"
    assert caught.value.extra["requested"] == "a-model-not-in-the-binding"
    assert asked == [], "the device was asked before the ceiling was applied"


def test_run_unsettled_is_declared_documented_and_raised_nowhere():
    """`run.settled`: the description promises a state the code cannot produce.

    The claim is made by AST, not by reading: no `refuse(...)` call in the
    package passes that code. It is a ratchet — the day one does, this fails and
    the matrix row is upgraded instead of staying pessimistic.
    """
    from fleet_mcp.errors import RUN_UNSETTLED

    assert RUN_UNSETTLED == "RUN_UNSETTLED"

    raisers: list[str] = []
    for path in sorted((ROOT / "fleet_mcp").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name not in ("refuse", "FleetRefusal"):
                continue
            for argument in node.args:
                if isinstance(argument, ast.Name) and argument.id == "RUN_UNSETTLED":
                    raisers.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}")

    assert raisers == [], (
        "RUN_UNSETTLED is now raised — the documented behaviour exists, so "
        f"upgrade the `run.settled` row instead of keeping it PARTIAL: {raisers}")
    assert "RUN_UNSETTLED" in (ROOT / "fleet_mcp" / "tools.py").read_text(encoding="utf-8"), (
        "the promise this test guards has been removed from the tool description")
