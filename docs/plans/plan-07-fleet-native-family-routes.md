# Explicit native-family session routes

## Overview

Item SRV-0011, source baseline `6c34efe18bc0ca9d2d840c5c20227981d0f4290a`.
Implement `docs/proposals/proposal-fleet-native-family-routes.md` in this
isolated worktree. Root owns all source/test changes. This reviewer owns only
design documents and review records. Plan 06 is reserved by the concurrent
operator Skills lane; this work uses 07 without copying that lane's files.

The implementation separates an addressed session namespace from its actual
binding runtime. No generic fallback, new ref schema, runtime selector alias,
provider-map alias, mutable inferred mapping or live configuration update.

## Phases

### 1. Explicit declaration and unambiguous session binding

Objective: an opt-in must be reviewable, false by default and safe before it
selects any provider. TDD first in an explicitly new focused module,
`tests/test_fleet_native_family_routes.py` (split modules are acceptable if their
coverage remains explicit).

Stages:

1. RED config tests for omitted/false/true `multiplex_native_families`;
   non-boolean values including strings, numeric values and null; true on
   non-base runtime, Codex family, or profile; opted-in declared account that
   disagrees with existing owner rules; and inconsistent opted-in mapping keys.
   Exercise both direct construction and YAML loading. Explicit false remains
   valid on non-base/profile bindings.
2. RED set-level tests: opted-in base plus any same-device exact Codex binding
   refuses, including disabled entries and direct `FleetConfig` construction.
   Another device's Codex binding does not conflict. Disabled/missing base and
   suffixed/missing namespaces never become a fallback route.
3. Add the field/shape checks to `fleet_mcp/config.py#ProviderBinding`, set-level
   checks to `FleetConfig`, YAML parsing and safe `describe` output. Preserve
   existing account/instance identity validation instead of adding a second
   derivation rule. YAML reports BAD_REQUEST; direct construction may raise
   ValueError. Do not coerce the new boolean with `bool(value)`.
4. Introduce `FleetConfig.session_binding(SessionKey)`. Exact binding wins,
   including a disabled exact declaration; otherwise only unqualified Codex
   may resolve to the explicitly opted-in same-device base. `provider_for`
   remains exact. The resolver selects no endpoint or credential from input.

Acceptance: default base-only Codex session binding remains absent; true selects
only the declared base; exact runtime lookup of Codex remains absent. A disabled
declaration is never bypassed. No provider/network IO or provider objects are created by
these config tests. All existing runtime/config identity tests remain green.

Dependencies: approved proposal. Gate: focused config cases and
`tests/test_fleet_runtime_identity.py`,
`tests/test_fleet_multi_runtime_routing.py`.

### 2. Session operations preserve namespace, route and authorization

Objective: get/read/send/interrupt/get_run use one explicit session route while
all permissions continue to describe the caller's exact typed session.

Stages, RED first:

1. Test the full opted-in Codex list/reference -> detail/read/run path using
   the same base provider; default false still refuses session routing. Test
   missing/disabled provider, another device, missing profile, explicit profile
   and exact Codex route behavior. Include actual Node detail normalization.
2. Add `FleetService._session_provider(key)` backed by `session_binding`, then
   use it only for the five hand-written session verbs. Resolve actual binding
   lookups in `_may_send`, `_may_control`, `_session_control_state` and final
   `get_session` normalization through the same session resolver. Provider-map
   presence alone cannot invent a configured route.
3. Adjust `fleet_mcp/session_detail.py#validate_binding_target` to honor the
   explicit opt-in. Keep strict non-base namespace identity, native family
   evidence checks and narrowly bounded legacy outer-Claude repair.
4. Negative tests prove missing scopes, base allow flags, addressed-session
   allowlists or addressed-namespace grants refuse before the mutation. A
   Claude-only grant never supplies a Codex grant. Positive namespace grants
   and exact allowlists retain their existing verb-specific behavior. No
   implicit grant/config mutation, and no change to read redaction or limits.

Acceptance: returned refs stay `device|codex|native-id` through the same account
route; querying another profile never falls back. Scope/grant failures cause
zero provider IO. Old accepted/unknown retry behavior remains unchanged.

Dependencies: Phase 1 green. Gate: focused session tests plus
`tests/test_fleet_session_detail_identity.py`,
`tests/test_fleet_mcp_surface.py`, `tests/test_fleet_multi_runtime_routing.py`.

### 3. Actual listing adapters and per-provider provenance

Objective: rows cannot lose a profile or be laundered into a different existing
route; a malformed provider cannot suppress the whole Fleet response.

Stages, RED first:

1. Drive real `CloudCliProvider.list_sessions` with recorded raw-native rows:
   base opt-in false/true, Claude/Codex native families, explicit profile and
   non-base bindings, wrong family and native IDs. For profiles, verify family
   before composing `binding.runtime`; never infer a profile from a tag.
2. Drive `FleetNodeProvider.list_sessions` using typed wire rows. Wrong device,
   another existing profile, unsupported namespace, malformed wire and a legacy
   profile Node base ref must refuse instead of being relabeled. Preserve the
   historically unresolved base Codex listing only where it cannot become a
   ref into another exact Codex binding. Listing observation does not enable
   session routing.
3. At `FleetService.list_sessions`, validate/stage one queried binding's whole
   result before appending it. Test an injected provider returning another
   device/profile's otherwise valid `SessionSummary`: its own gap is reported,
   other providers still return, and no row acquires another binding's control.
4. Add row `binding_runtime` and separate `sessions_per_binding` counts; retain
   existing typed `provider`/`runtime`, `sessions_per_runtime` namespace counts,
   `runtimes_queried`, deterministic ordering and partial-result behavior.
   Test per-caller control and discovery deadline/coalescing regressions.

Acceptance: actual adapter tests, not only fake service providers, prove profile
preservation. A base row cannot impersonate a separate exact Codex route. No
completed-result cache or provider aliases are introduced. Legacy profile Node
metadata that lacks trustworthy profile identity is a visible gap; this phase
does not modify Connector/NodeConfig/protocol to invent missing identity.

Dependencies: Phase 2 green. Gate: focused adapter/provenance tests plus
`tests/test_fleet_bounded_discovery.py`, `tests/test_fleet_mcp_node.py`,
`tests/test_fleet_v1_runtime_profile_boundary.py`.

### 4. Existing mirrored native session verbs and actual-route ceilings

Objective: keep the current tool schema set and grant floors, while the existing
session-addressed mirrors can reach an opted-in Codex session safely.

Stages, RED first:

1. Pin `cloudcli_native.is_session_addressed` and generated outer schemas.
   Only tools already exposing outer `session_ref` use session alias routing.
   `session_get/read`, `run_get`, overview/search/discovery/capability and
   no-session calls retain exact runtime selectors. Native `session_create`
   must not acquire a missing runtime through an incidental outer session ref.
2. Keep addressed namespace separate from actual binding runtime in
   `native_call`, `_gate_native_write`, operator-grant calls and
   `_native_reconfigure_ceiling`. Pass the actual route only where account
   binding/default ceilings are needed; grant lookup stays on the typed
   namespace. Preserve all current scope, node-floor and per-session checks.
3. Reject outer device/explicit runtime/inner native-session disagreement before
   IO. For direct API reads supplying both coordinates, verify their equality
   without enlarging the public MCP schema. Preserve the existing approvals-list
   fill/ownership check and dynamic read/control `session_background` semantics.
4. Test actual-route reconfigure policy: native project readback must match the
   declared project; global project and actual-runtime/project family, model
   and mode ceilings all bind. Claude-only rules cannot authorize Codex changes.
   Omitted parameters keep existing semantics. Authorized metadata reads may
   occur for a ceiling decision, but a refused mutation must never be sent.

Acceptance: namespace-only grants are enforced and actual account/project rules
cannot be skipped. Native schemas and exact runtime lookup are unchanged. No
unified rewrite of all native tools or new optional addressing fields.

Dependencies: Phase 3 green. Gate: focused native cases plus
`tests/test_fleet_mirror_runtime_addressing.py`,
`tests/test_fleet_cloudcli_full_inheritance.py`,
`tests/test_fleet_runtime_create_policy.py`.

The device's Connector `_key(native_id)`, send/native floors and local allowlists
are not changed. Fake-provider mutation PASS proves host routing/authorization,
not that a real device floor grants the same operation. Keep device writes as
separate pending acceptance; never relabel its allowlist from the host.

### 5. Create receipt namespace and durable replay safety

Objective: newly created opted-in Codex sessions remain addressable, with no
change to the closed owner receipt or its claim kernel.

Stages, RED first:

1. Compute expected typed receipt namespace from the selected exact binding and
   requested family before claim/provider IO. Opted-in base uses native family;
   non-base/profile uses its exact runtime. Unsupported family refuses before
   IO. Create selectors, owner grant and defaults stay on actual runtime.
2. Supply this expected namespace as `DispatchGuard.begin(..., runtime=...)`,
   while `_create_scope` keeps actual runtime/project and intent params keep
   requested native family/model/mode. Compose the response with the same
   expected namespace. Do not edit dispatch kernel/store schema or weaken receipt
   validation; send still binds to its complete addressed session ref.
3. Test create without initial message, queued/reference-only acknowledgements,
   unknown response, changed payload/family under one scoped key, same key on
   another actual target, and accepted Codex create/send replay after rebuilding
   both service and injected owner over the same temporary file.
4. Toggle opt-in around an old accepted create key: an old Claude receipt for
   requested Codex may now refuse as DISPATCH_STATE_UNAVAILABLE, but must not be
   rewritten or create a second session. Disable opt-in after a Codex receipt:
   its old ref does not acquire a fallback route. Preserve PROCESS_LOCAL's
   restart limitation and UNKNOWN's no-resend rule.

Acceptance: exactly one provider create/send for a durable replay; saved refs and
all closed receipt fields are unchanged; config toggles cannot convert a retry
into another effect. No new owner/store imports in installed Fleet artifacts.

Dependencies: Phase 4 green. Gate: focused receipt cases plus
`tests/test_fleet_dispatch_claims.py`,
`tests/test_fleet_create_session_runtime.py`, `tests/test_fleet_control_standalone.py`.

### 6. Read-only real-provider acceptance and delivery

Objective: establish the scoped observation, separately from source tests or
deployment authority.

Document the false-default opt-in with an example and its ambiguity/account
limitations; do not change live config. Run the union of affected regression
tests once after integration, using the existing interpreter and Run artifact
TMPDIR, no source bytecode/pytest cache. Pin revision, command, exits and counts.

Prepare a separate in-memory copy of existing config, with only the intended
base bindings opted in. Use a read-only principal, preserve original config
bytes and authorization, and query target device Session lists then exact
returned Codex detail/read/run where available. Record namespace, binding,
result/error and elapsed time without credentials/private transcript. Missing
Codex/Run evidence is pending, not permission to manufacture work with create or
send. No model call, OAuth mutation, deployment, tunnel/service restart or live
state migration. Real-provider credential anchors remain outside the workspace.

Acceptance: evidence labels source fixtures vs real-provider read-only vs
authenticated-client/device-write/update acceptance separately. No production
activation, missing profile Node identity, client grant verification or durable
owner wiring is falsely declared solved. Link the reviewed revision and any PR;
production activation remains a separate approved change.

Dependencies: Phases 1-5 green and approved read-only context. Evidence belongs
under the allocated SRV-0011 Run, with absolute paths and no traversal/symlink
escape. No automatic cleanup or removal of other actors' worktrees/evidence.
