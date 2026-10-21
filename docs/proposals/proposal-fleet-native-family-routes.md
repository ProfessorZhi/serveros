# Explicit native-family routes for a multiplexed base gateway

## Problem Statement

The 2026-10-21 candidate real-provider probe returned Codex references from
Tokyo and Windows listings, but get_session refused UNKNOWN_DEVICE. Existing
base-only bindings own a CloudCLI gateway that serves both native families.
FleetService._require_provider instead treats the native codex namespace as
another bound runtime. The lookup is unchanged from main 597f7b8. Generic
fallback would risk crossing an account/profile and must not be introduced.

## Goals

Make the owner explicitly declare that ONE base gateway serves native Claude
and Codex session namespaces. Preserve existing refs and exact non-base runtime
routing. Keep authorization scoped to the address the caller supplied and all
account/project ceilings on the actual selected binding. Default behavior stays
unchanged regarding route/authority expansion; correct non-base listing identity
without granting access to any new route. Test an in-memory opted-in copy of
existing live config without writing that file or changing production authorization.

## Non-Goals

No live configuration, credential, grant, deployment, network or OS changes.
No arbitrary aliases, cross-device/account/profile fallback, new identity
registry, provider discovery by probing, new tool verbs or owner-store wiring.

## Design

Introduce ProviderBinding.multiplex_native_families: bool = False. It is
strictly boolean; true is permitted only on the base runtime claude, family
claude, with no profile. Explicitly declared account identities continue to
pass the loader's existing device-owner checks; opted-in direct construction
must not bypass those checks for its declared account. Reject a padded/selector
runtime and an opted-in mapping key inconsistent with its binding's
device/runtime. It means that the same gateway
serves unqualified native-family namespaces claude and codex for this account;
it does not declare another credential runtime.

FleetConfig performs set-level ambiguity validation, including direct dataclass
construction: an opted-in base cannot coexist with ANY explicit codex binding
on that device (even disabled). Unknown profiles and suffixed namespaces never
use this path. A new session_binding(SessionKey) accessor returns the exact
binding first, or the opted-in base for the unqualified codex namespace.
provider_for(device, runtime) remains exact and unchanged: create/runtime
selectors cannot use a missing runtime through a session alias.

An exact disabled binding remains authoritative: it must not be skipped to find
another route. A disabled/missing base or missing provider object cannot serve
the opt-in. Ambiguity validation examines declarations, including disabled
entries, rather than only providers built by `build_providers`. It does not
install a second provider object under the alias or mutate the provider map.

FleetService uses a new _session_provider(key) helper for session-addressed
get/read/send/interrupt/get_run paths and matching binding lookups in
_may_send, _may_control and _session_control_state. The helper selects
providers using the returned binding's actual runtime, never a caller URL.
All gates remain in place. Update `session_detail.validate_binding_target` to
obey the same explicit opt-in instead of independently allowing any base gateway
to claim Codex. Service final detail validation uses the selected actual binding;
the output still carries the originally addressed namespace.
Operator-grant lookup continues to use key.provider
(the addressed namespace) so a base claude grant does NOT silently authorize
codex; exact session allowlists keep using the complete existing ref.

Only native tools already identified by
`cloudcli_native.is_session_addressed(native)` select the session binding/provider
through their existing outer `session_ref` field. Do not expand native read
schemas: `session_get`, `session_read`, `run_get`, discovery/capability and other
runtime-selected mirrors keep exact runtime routing. They can read the same
account through explicit runtime `claude`; they do not gain alias selectors.
`session_create` and other non-session tools never acquire alias routing merely
because a direct caller supplied `session_ref`.

These existing session-addressed native calls select the binding/provider,
retain the original address namespace for all authorization, and continue to
reject a conflicting explicit runtime or inner session id. Calls without a
session ref use only exact provider_for/runtime routing. Native reconfigure's
runtime_create_defaults ceiling must use the actual selected binding.runtime;
it must never lose a base-account restriction merely because the address is
codex. Its family restriction also applies: a Claude-only runtime/project rule
cannot authorize Codex model/mode changes even if opaque identifiers overlap.
Existing per-project ceilings, scopes and argument validation remain. A direct
read call supplying both outer and inner session coordinates must check their
equality before IO; this does not add the outer field to its MCP schema.
Retain approvals-list normalization, ownership enumeration, session-background
dynamic governance and read limits. An explicit `runtime` accompanying a session
ref denotes its addressed namespace and must match it; do not reinterpret it as
the selected route runtime.

The device's own authorization is unchanged. In particular, the existing
Connector send/native floors still use their own `_key(native_id)` and local
allowlists. A host alias cannot relabel, broaden or replace that machine floor.
Source fake-provider mutation tests prove host routing/gates only; real device
write behavior remains a separate acceptance item.

When creating on an opted-in base, compose the returned session reference from
the requested native family (claude or codex); non-base/profile refs remain
exactly their configured runtime. Before provider IO, derive that expected
receipt namespace and use it as DispatchGuard's runtime argument (the field is
the receipt's typed namespace), while _create_scope retains actual binding
runtime and project. This preserves the closed-owner receipt checks without
weakening them or changing the kernel/store schema. Provider/model remain part
of the intent digest; accepted replay must retain the same ref after owner
reconstruction. Create authorization and defaults remain on the actual runtime.

The expected namespace is deterministic before claim/provider IO: on the
opted-in base it is the requested native family, otherwise the exact configured
runtime. Reject unsupported families before creating anything. The original
principal/verb/actual-runtime/project/key scope and full intent digest stay
unchanged; changing family under that same create key is a conflict. A later
opt-in/config change never rewrites a saved receipt or authorizes resend:
current validation may replay or refuse, but may not rebind historical results.

### Listing normalization and provenance

The real `CloudCliProvider.list_sessions` currently composes every key from
`row.provider`, losing non-base/profile binding identity. For an explicit
non-base/profile binding, verify the native family against that binding and
compose its exact runtime namespace. For an opted-in base, allow only native
Claude/Codex namespaces. With base opt-in false, preserve historical native-family
listing behavior without making unresolved Codex addresses routable. Do not
infer account/profile/family by splitting arbitrary tag text.

`FleetNodeProvider.list_sessions` rehydrates typed wire references; validate
their device and namespace against the selected binding rather than treating
them as untyped native family. Do not rewrite another profile's typed ref into
the requested profile. Existing profile Node connectors omit NodeConfig.runtime
when constructing their inner CloudCliProvider; their legacy base refs may
therefore be refused. This phase reports that compatibility gap rather than
guessing a profile or changing the Node protocol/config.

Listing provenance is distinct from session routability: an unqualified Codex
observation from an opt-out base can remain listed only as the historically
unresolved address, never as an implicit binding. When a separate exact Codex
binding exists, refuse Codex rows originating from the other base route rather
than making that other account's ref appear to belong to the Codex binding.
Consequently, do not apply the strict session-routing opt-in check blindly to
all listing observations; test both the opt-out unresolved legacy case and the
cross-route collision case. Account/profile/device contradictions always refuse.

`FleetService.list_sessions` validates every summary against the exact queried
provider/binding, including injected providers; a forged row must not acquire
the authority of a different existing binding. Validate/stage one provider's
rows before appending them. A refusal becomes that provider's explicit gap;
other providers still return results.

Keep `provider`/`runtime` as the existing address namespace, and add row field
`binding_runtime` for the actual account route. Keep `sessions_per_runtime`
namespace counts and add `sessions_per_binding` counts consistent with actual
`runtimes_queried`. Gaps identify the queried binding. Sorting, per-caller
control, caller deadlines and in-flight-only coalescing stay intact. SessionKey
stays unchanged; there is no completed cache or inferred sticky mapping.

Describe the opt-in safely in config.describe and document an example only.
Never automatically persist it to a live config. The base gateway's protocol
still signs the base runtime; the native family is not presented as a new
signed account.

## Validation

TDD at service/config/provider boundaries:
- Default base-only Codex detail refuses; opt-in list -> detail/read/get_run
  reaches the same base gateway and preserves codex refs.
- Direct/YAML boolean shape; opt-in non-base/profile/family refusal; explicit
  codex binding ambiguity refusal including disabled; another device remains
  independent; suffixed/missing profiles never fall back.
- Sending/control/native mutations require scopes, base allow flags AND exact
  addressed-session allowlist or a codex namespace operator grant. A claude-only
  grant cannot authorize codex. Scope/grant/argument failures produce zero
  provider IO; project/ceiling checks may read metadata but never perform the
  refused mutation. Native verbs keep their existing additional operator-grant
  requirements; an exact session allowlist does not remove those requirements.
- Native inner/outer identity disagreement remains refused; read-only
  capability and no-session runtime calls cannot treat an alias as a runtime.
- Base account/project family, model and permission ceilings still bind native
  reconfigure; omitted parameters retain existing behavior.
- Keyed Codex create and send accepted replay survive injected owner
  reconstruction without duplicate provider calls and with unchanged address.
  Payload conflicts and uncertain outcomes remain refused.
- Non-base/profile routing and standalone installation regressions stay green.
- Test actual CloudCliProvider and FleetNodeProvider boundaries, not only fake
  service providers: preserve profile refs, refuse forged typed cross-route
  summaries, preserve other providers on a gap, and verify namespace/binding
  counts. Pin the unchanged mirrored-tool schema set.
- A separate read-only real-provider probe opts in only in memory, records
  family/ref/result and no credential, and never calls mutation verbs.

## Alternatives

An implicit fallback was rejected because absent or disabled runtimes must
not redirect to a different credential. Rewriting Codex refs to claude loses
the native-family identity and breaks existing addresses. Replacing refs with
a new four-part protocol is a broader migration than this compatibility seam.

## Open Questions

A source opt-in is not proof that a real gateway serves both families: only
the scoped real-provider readback can establish that observation. Activation
still needs a reviewed local configuration/grant migration, authenticated
client verification and owner-backed delivery persistence.

