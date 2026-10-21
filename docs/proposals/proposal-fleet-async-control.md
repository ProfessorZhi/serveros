# Fleet asynchronous control hardening

## Problem Statement

The operator dispatches several complete, long tasks and returns to coordination.
The current Fleet service uses a process-local replay dictionary, ignores changed
payloads under an existing key, and reads devices serially. A lost response,
concurrent retry or slow device can therefore produce duplicate work or occupy
the commander. Session detail can also report a provider different from the
native session it describes.

## Goals

- Preserve the existing MCP verbs and provider authorization checks.
- Admit one send/create per principal, verb, target and idempotency key; reject
  a changed payload and retain an unknown outcome rather than retrying it.
- Reuse Agent Control's existing dispatch-attempt collection for persistence
  when its owner supplies that store. Add no Work, grant or task database.
- Return session discovery within a bounded deadline with honest partial results
  and bounded outstanding provider work.
- Keep CC/Codex session identity consistent in detail and subsequent addressing.

## Non-Goals

No production activation, authority/key/ACL/database migration, network change,
new scheduler, automatic dependency runner, new MCP batch verb, or claim that a
Skill can keep running after its host exits. The existing Work/Parent owns the
task graph. Operator Skills and packaging are a separate device-agent lane.
This change does not close campaign #244's OS trust/lease/effect safety gates.

## Design

### Retry boundary

Introduce `fleet_mcp/dispatch.py` as an adapter, not a store. It uses
an owner-supplied `runtime/agent_control_store.py#AgentControlStore` through a
narrow injected interface. Standalone installed artifacts contain only
`fleet_mcp/` and `fleet/`: the adapter must not import `runtime`, dynamically load
its files, or require it through annotations. The owner alone imports its
`runtime/dispatch_attempts.py` kernel and builds the durable record.

Fleet computes a canonical JSON SHA-256 intent digest over the verb, credential
principal, canonical target and every caller-supplied send/create field. For
send this includes the complete instruction and session reference; for create
it includes device, runtime, project, initial message, agent family, model,
lifecycle mode and permission mode, preserving omitted/null distinctions where
the API makes them meaningful. Hashing does not persist these inputs. The
attempt key remains scoped to principal, verb and canonical target, without
ambiguous string concatenation. Authorization and argument validation still run
on every request, before claim lookup or replay.

Add `AgentControlStore.claim_dispatch_attempt` as an explicitly new owner API.
Under its existing mutation lock it compares an existing intent or records the
new attempt as EFFECT_UNKNOWN **before** provider IO. Only the successful
claimant receives a private invocation claim marker. A concurrent retry receives
the recorded verdict without that marker. A crash before the actual send may
conservatively block a retry; it must never authorize a second uncertain send.
The marker correlates one invocation; it grants no OS or execution authority.

Also add the explicitly new owner API
`complete_dispatch_claim(key, digest, claim_id, actor, receipt)`. Under the same
mutation lock it verifies the matching private marker, principal, digest and
current EFFECT_UNKNOWN phase before recording ACCEPTED. This is the original
claimant acknowledging delivery, not an unknown-outcome retry. The generic
`put_dispatch_attempt` and `runtime/dispatch_attempts.py#DISPATCH_TRANSITIONS`
must keep refusing EFFECT_UNKNOWN -> ACCEPTED/CALL_NOT_SENT; no global phase
relaxation is permitted. Repeated completion cannot overwrite a receipt, and
unknown-outcome recovery remains the existing owner reconciliation operation.
The claim marker stays internal to the adapter/owner exchange and is never
returned in MCP results, audit fields or the replay receipt.

Successful delivery records ACCEPTED with a closed, bounded receipt containing
only the typed session address, opaque run/queued-message identifiers and
explicit acceptance facts. Do not persist instructions, raw provider blobs,
credential values, policy/control blocks or a copy of authorization. Replaying
that delivery receipt does not mean that the business task finished. Exceptions,
malformed provider responses or a failed receipt write retain EFFECT_UNKNOWN;
neither transport classifications nor elapsed time authorize a blind resend.
Store read/claim errors fail closed before provider IO. Receipt-write failure
returns an unknown outcome even when the provider appeared to accept the call.
Create without an initial message still records its session receipt.
Reconstruct the public response from the receipt and recompute current control
permissions on replay. Historical defaults not saved in the receipt remain
unknown; never relabel current policy defaults as the earlier execution's facts.

`FleetService` accepts an optional `attempts` dependency. An embedded owner
can pass its existing store; the standalone runtime will not guess a store path
or mint a trusted context. Without one, the same guard is single-flight only
inside this process and responses explicitly report `PROCESS_LOCAL` durability;
injected-owner receipts identify their owner-backed durability. An unknown
or accepted attempt is never expired/evicted to make a retry sendable. Bound the
process-local guard with a finite capacity that refuses new keyed dispatches
when full, while allowing lookups of existing attempts. Requests without a key
retain historical non-idempotent behavior. A replay of ACCEPTED returns
the saved delivery receipt without applying the kernel's business-task meaning
of IN_FLIGHT as a command to wait.
There is no claim that the default deployed service survives restart safely.

### Discovery boundary

Introduce `fleet_mcp/discovery.py`: a service-owned fixed worker pool with
nonblocking capacity admission and same-query in-flight coalescing. Pending
work is bounded by worker count; a caller never creates an unbounded queue.
Completed jobs are removed, not cached. The request waits only to its configured
deadline, assembles target/gap metadata in registry/runtime order, and reports
timeout/capacity gaps without claiming that sessions are absent. It does not
cancel or restart provider runs.
Provider IO retains its own transport timeout; caller timeout is not thread
termination. Test providers must release their blocked worker in cleanup.

`fleet_mcp/config.py#PolicyBinding` supplies validated finite positive discovery
timeout and a positive integer worker count with an explicit upper bound; YAML
loading and direct dataclass construction enforce both. Bound live workers and
submitted provider calls, not merely the executor's thread count. Coalescing is
scoped to the exact provider/runtime and provider-query arguments. Share only
provider results, then apply each caller's current control permissions outside
the worker. Do not retain completed results between requests or block at executor
context exit. A caller timeout releases its wait, not the provider's capacity.

This immediate change bounds `FleetService.list_sessions` only. Existing
last-activity-descending session sorting, per-runtime filtering, read
authorization, `runtimes_queried` and session counts remain intact. A provider
error remains distinct from deadline expiry or capacity refusal. `list_devices`,
`status` and targeted detail reads keep their existing transport bounds; no
aggregate health query is a prerequisite for a targeted read.

### Session identity

Trace `fleet_mcp/providers/fleet_node.py#get_session` through
`fleet_mcp/node/connector.py#Connector._key` to
`fleet_mcp/providers/cloudcli.py#CloudCliProvider._normalize_detail`. The
connector currently synthesizes a Claude key even when native detail names
Codex. Derive native family at the CloudCLI normalizer/connector boundary only
for a synthesized base key, preserving explicit non-base runtime/account tags.
At `fleet_mcp/fleet.py#FleetService.get_session` validate that the returned
address matches the requested `fleet_mcp/identity.py#SessionKey`, preserving runtime/account tags
separately from the native agent family. Native family and native session id,
when supplied, must agree with the requested address under existing runtime
identity rules; a connector can serve multiple native families, so its default
binding family alone is not a session-family assertion. Contradictory nested native
identity is refused. The Fleet Node adapter may validate the connector's result,
but must not silently turn a wrong session into the requested one. Only a demonstrably synthesized outer identity may be
repaired from consistent native evidence, never by blindly overwriting every
field with the caller's key. Existing Claude and non-base runtime/profile
addressing must continue to work. No provider protocol/authentication change is
introduced in this phase.

## Alternatives

Keeping a new SQLite journal in Fleet would duplicate Agent Control ownership.
An unbounded executor or an executor context manager would respectively leak
work or wait for the slow provider at context exit. Blind replay after an
exception would confuse an unknown delivery with a failed delivery.

## Open Questions

Production persistence still needs the approved owner wiring and trust boundary;
it will be reported NOT_WIRED, not enabled through a caller-provided file path.
Real long-task/three-device/client update acceptance remains an integration gate.
