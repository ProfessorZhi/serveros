"""The capability-gap matrix: what a Fleet caller can reach, and on what proof.

The question this answers
-------------------------
"CloudCLI exposes 31 native verbs; Fleet claims to inherit them. Which of the
ten things an operator actually needs — projects, runtimes/agents, sessions,
models, modes and grants, send, run state, stop, evidence, and the two
artifacts — work end to end, which work partially, and which does not exist on
the device at all?"

Why this is data and not prose
------------------------------
Every row carries the native evidence it rests on and the Fleet symbol that is
supposed to provide it, and both are resolved by
`tests/test_l2_cloudcli_control_matrix.py`. A row that cites a tool the device
does not declare fails; a row whose Fleet pointer no longer exists fails. The
document `CAPABILITY-MATRIX.md` is a rendering of this table, so the two cannot
drift.

Two tiers, never one
--------------------
`status` is what the CODE does — proven offline by the tests beside this file.
`device` is what has actually been SEEN of the device:

* ``OBSERVED-1.41.0`` — a verbatim CloudCLI 1.41.0 roundtrip in #411's capture
  exercises this shape.
* ``SCHEMA-ONLY`` — the verb and its arguments are declared in the device's own
  ``tools/list``; no roundtrip in that capture called it with this shape.
* ``NOT-OBSERVED`` — neither.
* ``FLEET-LAYER`` — the semantic is Fleet's own; the device has no opinion.

A ``SUPPORTED`` row that is only ``SCHEMA-ONLY`` is a weaker claim than a
``SUPPORTED`` row that is ``OBSERVED``, and the acceptance column in the
document says so per row instead of averaging them into one green word.

On the word "UNSUPPORTED"
-------------------------
A row is ``UNSUPPORTED`` only when the native provider genuinely has no verb for
it, and such a row must name the verbs that were looked for (`absent`) so the
claim can be re-checked against the capture instead of being taken on trust.
Faking the missing verb with a local guess — a model list, a runtime list — is
the failure mode this column exists to prevent.
"""
from __future__ import annotations

#: What the local code does today.
STATUSES = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "BLOCKED")

#: What has actually been seen of the device.
DEVICE_TIERS = ("OBSERVED-1.41.0", "SCHEMA-ONLY", "NOT-OBSERVED", "FLEET-LAYER")


def _row(**fields) -> dict:
    return fields


#: One row per semantic an operator needs. `native` entries are resolved against
#: `EVIDENCE-1.41.0.json`; `fleet` entries are resolved against the source tree.
ROWS: tuple[dict, ...] = (
    _row(
        id="projects.list",
        question="Which projects exist on this device, and may I mint into them?",
        native=(("tool", "projects_list"),),
        fleet=(("fleet_mcp/fleet.py", "FleetService._project_catalogue_row"),
               ("fleet_mcp/tools.py", "NATIVE_MIRROR_TOOLS")),
        tool="fleet_cloudcli_projects_list",
        status="SUPPORTED",
        device="SCHEMA-ONLY",
        gap="",
        note="The device's own catalogue is the only source of truth; a create "
             "verifies against it before minting rather than trusting a local list.",
    ),
    _row(
        id="projects.select",
        question="Can a caller switch which project is current?",
        native=(("tool_property", "session_create", "project"),
                ("tool_property", "sessions_list", "project")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.create_session"),
               ("fleet_mcp/fleet.py", "FleetService.list_sessions")),
        tool="fleet_create_session",
        status="PARTIAL",
        device="SCHEMA-ONLY",
        gap="Selection is ADDRESSING, not a mode switch: a project is named on "
            "create and as a listing filter. There is no native verb that changes "
            "a device-wide current project, so no tool is offered for it — a "
            "`project_select` here would be this surface deciding what the "
            "gateway's schema should be.",
        absent=("project_select", "project_switch", "project_open", "project_use"),
    ),
    _row(
        id="projects.mutate",
        question="Can a project be created or deleted through Fleet?",
        native=(("tool_in_release", "project_create", "1.40.0"),
                ("tool_in_release", "project_delete", "1.40.0"),
                ("tool_not_in_release", "project_create", "1.41.0"),
                ("tool_not_in_release", "project_delete", "1.41.0")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.create_project"),
               ("fleet_mcp/fleet.py", "FleetService.delete_project")),
        tool="fleet_create_project",
        status="PARTIAL",
        device="NOT-OBSERVED",
        gap="Both verbs are declared by 1.40.0 and NEITHER is declared by 1.41.0, "
            "so on the captured 1.41.0 device `fleet_create_project` and "
            "`fleet_delete_project` answer `CAPABILITY_UNSUPPORTED` — honestly, "
            "because both read the device's own advertised schema before calling. "
            "A `tools/list` capture cannot say whether 1.41 removed the two verbs "
            "or that device had them disabled, so this row reports the observation "
            "and not a theory. Either way the hand-written project tools are the "
            "one part of the surface whose native backing is not present on the "
            "device the rest of this matrix was measured against.",
    ),
    _row(
        id="runtimes.agents.enumerate",
        question="Which runtimes/agents exist on this device, and what can each do?",
        native=(("tool", "sessions_list"),),
        fleet=(("fleet_mcp/fleet.py", "FleetService._validate_runtime_selector"),
               ("fleet_mcp/tools.py", "TOOLS")),
        tool="fleet_list_devices",
        status="UNSUPPORTED",
        device="FLEET-LAYER",
        gap="The device declares no verb that enumerates its runtimes, agents or "
            "providers. Fleet's runtime selector is a name from the LOCAL binding, "
            "checked for shape and then resolved against a configured provider — it "
            "is never confirmed by asking the device. Reported as what it is rather "
            "than inferred from `sessions_list`, which lists sessions, not the "
            "agents that could run one.",
        absent=("runtimes_list", "runtime_list", "runtimes", "agents_list",
                "agent_list", "agents", "providers_list", "runtime_info"),
    ),
    _row(
        id="sessions.list",
        question="Which sessions exist, on which device and runtime?",
        native=(("tool", "sessions_list"),),
        fleet=(("fleet_mcp/fleet.py", "FleetService.list_sessions"),),
        tool="fleet_list_sessions",
        status="SUPPORTED",
        device="SCHEMA-ONLY",
        gap="",
        note="One row per (device, runtime): a runtime that did not answer is "
             "named in `devices_unreachable` and never hides another's rows.",
    ),
    _row(
        id="sessions.read",
        question="What did a session say, page by page?",
        native=(("case", "session_read_latest"), ("case", "session_read_outline"),
                ("case", "session_read_around_unknown"),
                ("case", "codex_session_read_outline")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.read_session"),),
        tool="fleet_read_session",
        status="SUPPORTED",
        device="OBSERVED-1.41.0",
        gap="",
        note="`aroundId` on an unknown id is observed answering a refusal, not a "
             "silent empty page — the distinction a reader needs.",
    ),
    _row(
        id="sessions.create",
        question="Can a session be minted, and does anyone claim its run started?",
        native=(("case", "create_no_message_claude_per_run"),
                ("case", "create_with_message_claude_per_run"),
                ("case", "create_no_message_codex_per_run")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.create_session"),),
        tool="fleet_create_session",
        status="SUPPORTED",
        device="OBSERVED-1.41.0",
        gap="",
        note="A create with a first message answers `{sessionId, deliveryId, runId}`; "
             "without one it answers `{sessionId}` only. The surface keeps that "
             "distinction: it reports `run_id: null` rather than inventing one.",
    ),
    _row(
        id="sessions.create.model",
        question="Can a caller name a model, and is a wrong one refused?",
        native=(("tool_property", "session_create", "model"),
                ("tool_property", "session_reconfigure", "model")),
        fleet=(("fleet_mcp/fleet.py", "FleetService._may_name_mode_and_model"),
               ("fleet_mcp/config.py", "CreateGrant.models")),
        tool="fleet_create_session",
        status="SUPPORTED",
        device="SCHEMA-ONLY",
        gap="",
        note="Declaring a model natively IS possible: `model` is a declared "
             "`session_create` argument. Fleet then narrows it to the project's "
             "own `create_allow.<project>.models`, which is EMPTY until an "
             "operator lists a model — so by default a caller may not name one and "
             "the provider's default applies. No roundtrip in the capture passed "
             "`model`, so what the device answers for an unknown model id is "
             "UNKNOWN here, not guessed.",
    ),
    _row(
        id="models.enumerate",
        question="Which model ids may I choose on this device?",
        native=(("tool_property", "session_create", "model"),
                ("case", "reconfigure_unsupported_mode")),
        fleet=(("fleet_mcp/fleet.py", "FleetService._may_name_mode_and_model"),),
        status="UNSUPPORTED",
        device="OBSERVED-1.41.0",
        gap="There is no enumeration verb. The device volunteers a `supported` list "
            "ONLY inside a refusal (`UNSUPPORTED_PERMISSION_MODE` carries the "
            "permission-mode set, and no captured refusal carries a model set at "
            "all), and `mcp_http.structured` relays the enum `code` but not "
            "`details`. So neither the device nor Fleet can answer 'which models'. "
            "Anything that produced a list here would be a local invention.",
        absent=("models_list", "model_list", "models", "providers_models",
                "session_models"),
    ),
    _row(
        id="sessions.mode",
        question="Which permission modes are legal, and what did the device apply?",
        native=(("tool_property", "session_create", "permissionMode"),
                ("case", "reconfigure_unsupported_mode"),
                ("case", "reconfigure_supported_mode")),
        fleet=(("fleet_mcp/fleet.py", "FleetService._may_name_mode_and_model"),
               ("fleet_mcp/fleet.py", "FleetService._read_effective_permission_mode")),
        tool="fleet_create_session",
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="The legal set is discoverable from the device only by being refused, "
            "and the refusal's `details.supported` (observed: `default, auto, "
            "acceptEdits, bypassPermissions, plan` for provider `claude`) is dropped "
            "before it reaches a caller. What DOES survive is the right half of the "
            "question: `permission_readiness` reports the mode the device said it "
            "applied, and keeps `requested`/`resolved`/`effective` apart because "
            "only the first two are known locally.",
    ),
    _row(
        id="grants",
        question="What may this caller do to this project/session, and who said so?",
        native=(("tool", "session_create"), ("tool", "session_send"),
                ("tool", "session_interrupt")),
        fleet=(("fleet_mcp/fleet.py", "FleetService._may_create"),
               ("fleet_mcp/fleet.py", "FleetService._may_send"),
               ("fleet_mcp/fleet.py", "FleetService._control_block"),
               ("fleet_mcp/config.py", "PolicyBinding.create_allow")),
        status="SUPPORTED",
        device="FLEET-LAYER",
        gap="",
        note="Grants are this layer's, not the device's: the device grants a scope "
             "to a credential, and Fleet narrows it per project and per session. "
             "Each answer is returned alongside the call it authorizes, so "
             "'created' is not mistaken for 'mine to drive'.",
    ),
    _row(
        id="send",
        question="Can a message be delivered to a session, and what came back?",
        native=(("case", "send_per_run"), ("case", "send_resident_queued"),
                ("case", "send_busy_per_run"), ("case", "send_resident_first")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.send_to_session"),),
        tool="fleet_send_to_session",
        status="SUPPORTED",
        device="OBSERVED-1.41.0",
        gap="",
        note="A busy session is observed answering `SESSION_BUSY` with the run "
             "that holds it; that is a refusal here, never an accepted send.",
    ),
    _row(
        id="send.ack",
        question="Which native acknowledgement fields survive into a receipt?",
        native=(("case", "send_per_run"), ("case", "send_resident_queued")),
        fleet=(("fleet_mcp/dispatch.py", "delivery_receipt"),
               ("fleet_mcp/dispatch.py", "RECEIPT_FIELDS")),
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="The device answers `{deliveryId, runId, queued, queuedMessageUuid, "
            "source, blockedBy}`. A receipt keeps `run_id` and "
            "`queued_message_uuid` and drops the other four, so `queued` (was this "
            "a delivery or a run?) and `blockedBy` are not reportable. Worse, "
            "`accepted` is SYNTHESIZED — defaulted to True when the payload is "
            "silent and hardcoded True for a project — while no captured native "
            "answer carries an `accepted` key at all. `accepted: true` therefore "
            "means 'a run or queue reference exists', not 'the device said so', "
            "and a consumer that reads it as a device ACK is reading a local word.",
    ),
    _row(
        id="run.get",
        question="Can the state of a run be read back, by which identifier?",
        native=(("case", "run_get_hit_completed"), ("case", "run_get_hit_aborted"),
                ("case", "run_get_delivery_miss"), ("case", "run_get_both_ids"),
                ("case", "run_get_run_hit_from_queued_send")),
        fleet=(("fleet_mcp/providers/cloudcli.py", "CloudCliProvider.get_run"),),
        tool="fleet_get_run",
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="The device accepts `runId` OR `deliveryId` (plus a legacy `run` "
            "alias) and answers an `idKind` saying which it used. Fleet sends "
            "`runId` only and its tool schema requires `run_id` with "
            "`additionalProperties: false`, so a delivery cannot be addressed and "
            "`idKind` never reaches a caller. The observed cases are not broken by "
            "this — a queued send answers BOTH ids — but the native half of the "
            "contract is not reflected.",
    ),
    _row(
        id="run.identity",
        question="Is this the same run, on the same boot, or a different one?",
        native=(("case", "run_get_hit_completed"),
                ("case", "run_get_delivery_miss"),
                ("case", "run_get_legacy_aliases_accepted_run_expired")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.get_run"),),
        tool="fleet_get_run",
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="`runId`, `deliveryId` and `bootId` are three distinct fields on the "
            "device, and every refusal carries the `bootId` it was evaluated "
            "under. A successful read passes the whole payload through, so "
            "`bootId`, `idKind`, `status` and `outcome` DO reach a caller. An "
            "ERROR envelope does not: `structured()` relays only the `code`, so a "
            "`RUN_NOT_FOUND` arrives without the `bootId` that would say 'this id "
            "belongs to a previous boot'. A restart is therefore visible only "
            "while the run still exists.",
    ),
    _row(
        id="run.not_found",
        question="Is a missing run a success, a transient, or a dead id?",
        native=(("case", "run_get_delivery_miss"),
                ("case", "run_get_legacy_aliases_accepted_run_expired"),
                ("case", "run_get_neither_id")),
        fleet=(("fleet_mcp/mcp_http.py", "REMOTE_REFUSAL_CODES"),),
        tool="fleet_get_run",
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="The important half holds: `RUN_NOT_FOUND` is an exact counterpart in "
            "`REMOTE_REFUSAL_CODES`, so it is raised as a refusal and never "
            "reported as an empty successful read. The other half is dropped: the "
            "device distinguishes `reason: never_issued` from `reason: expired` "
            "and marks both `retryable: false`, and none of that survives "
            "`structured()`. A caller learns 'not found' and cannot tell 'that id "
            "was never issued' from 'it aged out' — and blind-retrying either is "
            "exactly what the `retryable: false` was there to prevent.",
    ),
    _row(
        id="run.settled",
        question="Is a run still executing, finished, or unknown?",
        native=(("case", "run_get_hit_completed"), ("case", "run_get_hit_aborted")),
        fleet=(("fleet_mcp/errors.py", "RUN_UNSETTLED"),
               ("fleet_mcp/tools.py", "TOOLS")),
        tool="fleet_get_run",
        status="PARTIAL",
        device="OBSERVED-1.41.0",
        gap="`fleet_get_run`'s own description promises that a run not settled "
            "within the wait comes back as `RUN_UNSETTLED`. That code is declared "
            "in `errors.py` and RAISED NOWHERE — the documented behaviour does not "
            "exist. What a caller does get is the device's `status` and `outcome` "
            "passed through, which distinguishes running from terminal; the named "
            "state, and the 'still working, do not treat as failed' sentence "
            "attached to it, cannot occur.",
    ),
    _row(
        id="stop.session",
        question="Can an in-flight run be stopped?",
        native=(("case", "interrupt_running"), ("case", "interrupt_idle")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.interrupt_session"),),
        tool="fleet_interrupt_session",
        status="SUPPORTED",
        device="OBSERVED-1.41.0",
        gap="",
        note="An idle session answers `{aborted: false, message: ...}` — a success "
             "carrying a fact, forwarded rather than replaced.",
    ),
    _row(
        id="stop.run",
        question="Can ONE run be stopped while the session stays up?",
        native=(("tool", "session_interrupt"), ("tool", "session_background"),
                ("tool", "session_cancel_queued")),
        fleet=(("fleet_mcp/fleet.py", "FleetService.interrupt_session"),),
        status="UNSUPPORTED",
        device="SCHEMA-ONLY",
        gap="No native verb cancels a run by id. The nearest effects are "
            "session-scoped: `session_interrupt` aborts whatever is in flight in "
            "the SESSION, `session_cancel_queued` withdraws a queued MESSAGE by "
            "`queuedMessageUuid`, and `session_background` stops a background TASK "
            "by `stopTaskId`. None of the three accepts a run id, so a run-level "
            "stop tool would silently widen to a session-level one — refused here "
            "instead.",
        absent=("run_cancel", "run_abort", "run_stop", "run_interrupt"),
    ),
    _row(
        id="lifecycle.close",
        question="Can a session be closed, and is the refusal honest?",
        native=(("case", "close_resident"), ("case", "close_per_run_refused")),
        fleet=(("fleet_mcp/tools.py", "NATIVE_MIRROR_TOOLS"),),
        tool="fleet_cloudcli_session_close",
        status="SUPPORTED",
        device="OBSERVED-1.41.0",
        gap="",
        note="A per-run session is observed answering "
             "`LIFECYCLE_MODE_NOT_RESIDENT` rather than pretending to close.",
    ),
    _row(
        id="evidence.receipt",
        question="Is there an auditable record of what was asked and answered?",
        native=(("tool", "session_send"),),
        fleet=(("fleet_mcp/dispatch.py", "delivery_receipt"),
               ("fleet_mcp/fleet.py", "FleetService.send_to_session")),
        status="SUPPORTED",
        device="FLEET-LAYER",
        gap="",
        note="The device issues no receipt verb; the receipt is this surface's "
             "own record of the acknowledgement, bounded to address fields and "
             "never carrying the message. Its `accepted` field is local — see "
             "`send.ack`.",
    ),
    _row(
        id="artifact.split",
        question="Do the node and hub ship as two independently installable artifacts?",
        native=(("tool", "sessions_list"),),
        fleet=(("fleet_mcp/node/connector.py", "Connector"),),
        status="BLOCKED",
        device="FLEET-LAYER",
        gap="Measured in `integrations/fleet-v1-l2-release/`: the node artifact's "
            "closure still leaves the repository and neither artifact is "
            "`runtime_standalone`. The three-line cut that fixes it is published as "
            "`L2-CUT-A.patch` and is RED→GREEN contracted, but it lives in files "
            "another author owns and is not applied here.",
    ),
)


#: Rows whose status is not `SUPPORTED` must say why. Kept as a projection so a
#: new row cannot be added without one.
GAPS_REQUIRED = tuple(row["id"] for row in ROWS if row["status"] != "SUPPORTED")


def row(identifier: str) -> dict:
    for entry in ROWS:
        if entry["id"] == identifier:
            return entry
    raise KeyError(identifier)


def native_verbs_searched(entry: dict) -> tuple[str, ...]:
    """The names an `UNSUPPORTED` row looked for, for re-checking the absence."""
    return tuple(entry.get("absent") or ())


# -- rendering --------------------------------------------------------------

_STATUS_MARK = {"SUPPORTED": "✅ SUPPORTED", "PARTIAL": "🟡 PARTIAL",
                "UNSUPPORTED": "⛔ UNSUPPORTED", "BLOCKED": "⛔ BLOCKED"}


def _evidence_label(ref: tuple) -> str:
    if ref[0] == "tool":
        return f"`{ref[1]}`"
    if ref[0] == "tool_property":
        return f"`{ref[1]}.{ref[2]}`"
    if ref[0] == "case":
        return f"case `{ref[1]}`"
    if ref[0] == "tool_in_release":
        return f"`{ref[1]}` declared in {ref[2]}"
    if ref[0] == "tool_not_in_release":
        return f"`{ref[1]}` NOT declared in {ref[2]}"
    return str(ref)


def _pointer_label(pointer: tuple) -> str:
    return f"`{pointer[1]}` ({pointer[0]})"


def render_markdown(evidence: dict) -> str:
    """The document, generated from `ROWS` and the fixture.

    Nothing in it is written by hand, so it cannot disagree with the tests: the
    file is compared to this function's output character for character.
    """
    source = evidence["source"]
    tools, cases = evidence["tools"], evidence["cases"]
    counts: dict[str, int] = {}
    for entry in ROWS:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    lines = [
        "# L2 control: the capability-gap matrix",
        "",
        "> Generated by `python integrations/fleet-l2-control/matrix.py --render`.",
        "> Do not edit by hand: `tests/test_l2_cloudcli_control_matrix.py` compares",
        "> this file to the table it renders and fails on any difference.",
        "",
        "What this answers: for each semantic an operator needs, can a Fleet caller",
        "reach it through CloudCLI today, on what native evidence, and at which",
        "acceptance level. It is not a claim that Fleet can \"send messages\"; a",
        f"message that arrives is one row of {len(ROWS)}.",
        "",
        f"Native evidence: CloudCLI **{source['package']}** (MCP protocol",
        f"`{source['protocol_version']}`), {len(tools)} declared verbs and",
        f"{len(cases)} verbatim roundtrips, captured {source['captured_at_utc']}.",
        "Carried here as shapes only (`EVIDENCE-1.41.0.json`), re-derivable with",
        "`evidence.py`, and re-checked against the captures whenever they are on",
        "disk.",
        "",
        "## The two tiers",
        "",
        "`status` is what the CODE does — every row is exercised offline by",
        "`tests/test_l2_cloudcli_control_matrix.py`. `device` is what has actually",
        "been SEEN of the device:",
        "",
        "| tier | means |",
        "|---|---|",
        "| `OBSERVED-1.41.0` | a verbatim roundtrip in the capture exercises this shape |",
        "| `SCHEMA-ONLY` | the verb and arguments are declared; no roundtrip called it this way |",
        "| `NOT-OBSERVED` | neither |",
        "| `FLEET-LAYER` | the semantic is Fleet's own; the device has no opinion |",
        "",
        "A `SUPPORTED` row that is only `SCHEMA-ONLY` is a weaker claim than one",
        "that is `OBSERVED-1.41.0`. Averaging the two into one green word is how a",
        "surface comes to believe it has been tested on a device when it has been",
        "tested against a schema.",
        "",
        f"## Summary — {len(ROWS)} rows",
        "",
        "| | count |",
        "|---|---|",
    ]
    for status in STATUSES:
        lines.append(f"| {_STATUS_MARK[status]} | {counts.get(status, 0)} |")
    lines += [
        "",
        "## The declared set is not a constant",
        "",
        "Read from each release's own `tools/list` capture:",
        "",
        "| release | declared verbs |",
        "|---|---|",
    ]
    declared = evidence.get("declared_by_release") or {}
    for release in sorted(declared):
        lines.append(f"| {release} | {len(declared[release])} |")
    current = source["package"]
    for release in sorted(declared):
        if release == current:
            continue
        gained = sorted(set(declared[release]) - set(declared[current]))
        lost = sorted(set(declared[current]) - set(declared[release]))
        if gained:
            lines.append(f"- {current} does **not** declare, and {release} does: "
                         + ", ".join(f"`{name}`" for name in gained))
        if lost:
            lines.append(f"- {current} declares, and {release} does not: "
                         + ", ".join(f"`{name}`" for name in lost))
    lines += [
        "",
        "Any sentence in this repository that states a verb count is therefore a",
        "sentence with an expiry date. The tests compare SETS to sets, and the one",
        "literal (`cloudcli_native.NATIVE_TOOL_COUNT`) is asserted equal to both",
        "sides so it cannot agree with one and lie about the other.",
        "",
        "| # | semantic | status | seen on device | native evidence | Fleet symbol |",
        "|---|---|---|---|---|---|",
    ]
    for index, entry in enumerate(ROWS, start=1):
        native = "<br>".join(_evidence_label(ref) for ref in entry["native"])
        fleet = "<br>".join(_pointer_label(pointer) for pointer in entry["fleet"][:2])
        lines.append(
            f"| {index} | `{entry['id']}` — {entry['question']} | "
            f"{_STATUS_MARK[entry['status']]} | `{entry['device']}` | {native} | {fleet} |")

    lines += ["", "## Every row, in full", ""]
    for entry in ROWS:
        lines += [f"### `{entry['id']}` — {_STATUS_MARK[entry['status']]}", "",
                  f"{entry['question']}", ""]
        lines.append(f"- seen on the device: `{entry['device']}`")
        lines.append("- native evidence: "
                     + ", ".join(_evidence_label(ref) for ref in entry["native"]))
        lines.append("- Fleet: " + ", ".join(_pointer_label(p) for p in entry["fleet"]))
        if entry.get("tool"):
            lines.append(f"- tool: `{entry['tool']}`")
        if entry.get("note"):
            lines += ["", entry["note"]]
        if entry.get("gap"):
            lines += ["", "**Gap.** " + entry["gap"]]
        if entry.get("absent"):
            lines += ["", "Verbs searched for and **not** declared by the device: "
                      + ", ".join(f"`{name}`" for name in entry["absent"]) + "."]
        lines.append("")

    lines += [
        "## What is deliberately not offered",
        "",
        "Three of the rows above are `UNSUPPORTED` because the device has no verb",
        "for them: enumerating runtimes/agents, enumerating model ids, and",
        "stopping one run. Each would be easy to fake — a list from the local",
        "binding, a run-level stop built on `session_interrupt` — and each fake",
        "would be wrong in a way the caller could only discover by acting on it. A",
        "tool that answers \"here are the models\" from a config file is",
        "indistinguishable, at the call site, from one that asked the device.",
        "",
        "## How to re-check this without a device",
        "",
        "```",
        "python -m pytest tests/test_l2_cloudcli_control_matrix.py -q",
        "python integrations/fleet-l2-control/evidence.py --captures <capture-dir>",
        "python integrations/fleet-l2-control/matrix.py --render",
        "```",
        "",
        "The first command resolves every native reference against the fixture,",
        "every Fleet reference against the source, and every gap by calling the",
        "real code. The second and third regenerate what this document rests on.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Render CAPABILITY-MATRIX.md.")
    parser.add_argument("--render", action="store_true",
                        help="write CAPABILITY-MATRIX.md beside this module")
    parser.add_argument("--evidence", type=Path,
                        default=Path(__file__).with_name("EVIDENCE-1.41.0.json"))
    args = parser.parse_args(argv)
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    rendered = render_markdown(evidence)
    if args.render:
        out = Path(__file__).with_name("CAPABILITY-MATRIX.md")
        out.write_text(rendered + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {out} ({len(rendered)} chars)")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
