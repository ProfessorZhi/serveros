# Fleet asynchronous control implementation plan

## Overview

Item SRV-0011; source baseline 597f7b8. Follow
`docs/proposals/proposal-fleet-async-control.md`. Root owns control code;
Tokyo P1 owns Operator Skills/packaging; Dell owns independent QA preparation;
Windows owns read-only native verification. Do not edit another lane's files.

## Phases

### 1. Atomic delivery claims and retries

Objectives: preserve existing authorization, reuse the existing attempt owner,
make concurrent and unknown retries safe within the declared durability scope.

Stages (TDD first):
1. Add focused failing tests (new dispatch test module or the existing
   `tests/test_fleet_mcp_surface.py`) for every create/send intent field, same-key
   changed instruction, racing identical sends/creates, lost ACK, malformed
   provider acknowledgement, create without first message, and failure to save
   the delivery receipt. Exercise two independently reconstructed owner stores
   over one temporary file, not merely two service objects sharing one dict.
2. Extend `tests/test_dispatch_attempts.py` and `tests/test_agent_control_store.py`
   for the explicitly new owner APIs `claim_dispatch_attempt` and
   `complete_dispatch_claim`. Prove the UNKNOWN record is committed before
   provider IO; a concurrent claimant cannot send or obtain the private claim
   marker; a wrong actor/digest/marker cannot complete; repeated completion
   cannot overwrite; generic `put_dispatch_attempt` still refuses UNKNOWN ->
   ACCEPTED/CALL_NOT_SENT. Preserve `runtime/dispatch_attempts.py`'s generic
   transition table unchanged.
3. Implement both owner operations inside
   `runtime/agent_control_store.py#AgentControlStore`'s existing `_mutation`
   boundary, building records through its existing dispatch kernel. Perform no
   provider IO under this lock. Claims start EFFECT_UNKNOWN, not CALL_NOT_SENT.
   Only the original private claimant may acknowledge ACCEPTED; unresolved
   failures continue through existing owner reconciliation, never resend.
4. Introduce `fleet_mcp/dispatch.py` as a standalone-safe adapter and optional
   `FleetService(..., attempts=...)` injection. Compute the full canonical MCP
   intent digest in Fleet; no runtime imports/dynamic loading/type dependency.
   Use the injected owner's claim/get/completion API without allocating a new
   store/path or fabricating a trusted actor context.
5. Route keyed send/create through the guard after all existing validation and
   authorization checks. Store only closed bounded session/run/queue receipt
   fields, never instruction/raw provider payload/control/grants/private claim
   markers in the public receipt. Rebuild replay responses from that receipt;
   recompute current control, and leave unavailable historical defaults unknown.
6. Test principal/verb/target isolation, revocation before replay, owner read or
   claim failure before IO, and completion failure after IO. Verify a bounded
   process-local capacity refuses new keys without forgetting ACCEPTED/UNKNOWN
   keys, still answers existing keys, and explicitly reports `PROCESS_LOCAL`.
   Verify requests without an idempotency key retain historical behavior.

Acceptance: one provider call under a racing key; conflict never sends; unknown
never sends again within the declared scope, including durable owner/service
reconstruction and receipt-write failure. Default process restart explicitly
loses its PROCESS_LOCAL guard and is not advertised as restart-safe.
An ACCEPTED delivery receipt replays immediately without implying task
completion. An accepted receipt is recoverable with the injected existing store
after reconstruction. Persisted bytes contain neither instruction nor raw
provider blob. Default service startup works with only `fleet_mcp/` and `fleet/`
available. No new durable state root, grant, task authority or guessed production
binding. Production remains NOT_WIRED.

Dependencies: approved proposal; existing owner `_mutation`, dispatch-attempt
kernel and provider interfaces. Run the focused new tests plus
`tests/test_dispatch_attempts.py`, `tests/test_agent_control_store.py`,
`tests/test_fleet_mcp_surface.py`, `tests/test_fleet_create_session.py` and
`tests/test_fleet_create_session_runtime.py` before Phase 2. Record actual commands
and outcomes; use the project's configured Python/test environment.

### 2. Bounded multi-device discovery

Objectives: independent provider reads and a finite caller wait.

Stages (TDD first):
1. Add event-driven failing tests for `FleetService.list_sessions`: fast results
   return while a slow provider is still blocked; deadline and capacity refusal
   are explicit gaps; provider exceptions keep their original error codes;
   metadata order is deterministic and session rows retain the existing
   last-activity-descending sort. Always release blocked test workers in cleanup.
2. Prove across repeated/concurrent requests that live workers AND total submitted
   provider calls stay within the limit, exact in-flight queries coalesce,
   different provider arguments do not coalesce, completed queries are queried
   afresh, and a timed-out caller does not release occupied provider capacity.
   Coalesced requests from different principals recompute their own permissions.
3. Introduce `fleet_mcp/discovery.py` as a service-owned fixed pool with
   nonblocking bounded admission and in-flight-only query sharing. Never use an
   executor context exit that waits for outstanding provider IO. Provider
   transport timeouts still apply; do not claim caller timeout killed a thread.
4. Extend `fleet_mcp/config.py#PolicyBinding` and `load_config` with finite positive
   timeout and bounded positive integer worker settings; test direct construction
   and YAML, including zero/negative/non-finite timeout, booleans/non-integral
   worker values, and an excessive worker count. Integrate only list_sessions;
   keep authorization and per-caller row/control assembly outside shared workers.

Acceptance: timeout returns partial results and explicit gaps; pending provider
calls never exceed worker count; no timeout is rendered as an absent session.
Always release fake blocked workers; avoid timing-only assertions.
Dependencies: Phase 1 green; unchanged provider `list_sessions` protocol and
existing runtime selection rules. Run focused discovery/config tests plus
`tests/test_fleet_multi_runtime_routing.py` and `tests/test_fleet_mcp_surface.py`
before Phase 3. `list_devices`, `status` and targeted reads are not advertised
as receiving this aggregate deadline.

### 3. Runtime identity and integration

Implementation refinement: `fleet_mcp/session_detail.py` owns the shared
validation used by FleetNodeProvider and FleetService. CloudCliProvider preserves
all flat native source fields before outer address attachment. Include actual
CloudCLI-path cases for every identity alias, not only synthetic Node responses.

Objectives: preserve the provider/session identity observed on the device.

Stages (TDD first): reproduce the Windows Codex mismatch with nested native
detail and the synthesized outer Claude key produced by
`fleet_mcp/node/connector.py#Connector._key` and
`fleet_mcp/providers/cloudcli.py#CloudCliProvider._normalize_detail`. Derive
native family for synthesized base keys at that normalizer/connector boundary,
preserving explicit non-base runtime/account tags. Validate the final returned
address against the requested `fleet_mcp/identity.py#SessionKey` at
`fleet_mcp/fleet.py#FleetService.get_session`; validate connector evidence in
`fleet_mcp/providers/fleet_node.py#get_session` as needed. Add contradictory nested
family/native-id/device tests, missing native-identity compatibility cases, and
Claude/non-base runtime/profile regression coverage. Repair only an outer key
demonstrably synthesized by the connector; do not silently retarget contradictory
native evidence. Do not change the node protocol or its authorization.

Acceptance: a Codex detail returns a Codex SessionKey; wrong-target metadata
never becomes an address for a subsequent mutation.

Dependencies: Phase 2 green; unchanged session address/runtime identity rules.
Run `tests/test_fleet_mcp_node.py`, `tests/test_fleet_node.py`,
`tests/test_fleet_multi_runtime_routing.py`, `tests/test_fleet_runtime_identity.py`,
`tests/test_fleet_create_session_runtime.py` and focused new identity tests.
An isolated installed-artifact import/startup check must exclude `runtime/` and
the source checkout from import search paths; ordinary imports in the full repo
do not prove standalone compatibility.

### 4. Independent verification and delivery

Pin and publish the reviewed source branch/Draft PR, send one complete test
envelope to Dell, and absorb its findings. Preserve separate source, fixture,
real-device and authenticated-client results. Keep production/owner trust
NOT_WIRED until independently accepted; do not deploy or merge.

Record test commands, exits, revision and artifacts under the allocated Run.
Review Operator Skills/package output separately and link each device receipt.

Dependencies: all prior phase gates green. Acceptance: independent QA references
the exact reviewed commit and distinguishes hermetic source tests from real
three-device/authenticated-client/update evidence. Link unavailable/unexecuted
acceptance as pending rather than claiming V1 release. Confirm the diff is
limited to the owning lane, re-run the affected test union after integration,
and attach any created Draft PR to the chat. No new scheduler, automatic task
graph runner, production deployment, or trust-gate closure is part of delivery.
