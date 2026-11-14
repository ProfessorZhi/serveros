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
another route. An absent configured session binding immediately yields
UNKNOWN_DEVICE, even when an injected provider-map entry exists. A disabled or
missing base cannot serve the opt-in. A missing/offline provider cannot perform
new IO, but is not a prerequisite for returning an already ACCEPTED delivery
receipt after current configuration/authorization checks.
Ambiguity validation examines declarations, including disabled
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

### Positive native identity proof for a multiplexed account

The upstream session send/control/read/run APIs carry a bare native ID, not the
typed family. The source review at 041f6e9 proved that a caller could label a
Claude ID as Codex, obtain a Codex-only grant decision, and send/interrupt that
Claude session; the inverse worked too. A correctly selected gateway and a
syntactically valid ref therefore do not establish the native-family target.

On a multiplexed binding, require positive native family plus matching native
ID evidence for both unqualified families. Use the original upstream native
session object, or the original flat native payload retained before routing-key
attachment. Do not use the adapter's generated outer provider/session_ref/
native_session_id as that evidence. A dictionary containing only a SessionKey
address, even wrapped as metadata, is not native proof. The existing semantic
distinction remains: a provider in an explicitly typed wire ref is a namespace,
not independent native-family evidence. Missing/ambiguous/malformed native
family or ID must refuse; all supplied contradictory native IDs still refuse.

Both CloudCliProvider normalization and shared `session_detail.normalize_detail`
must retain the original flat native source before adding typed routing fields.
An originally malformed nested session remains invalid. Normalizing an empty or
identity-free payload must never manufacture the positive family/ID evidence
from the caller's key. No new persistence namespace or public proof token.

Use a private fresh-proof helper over the selected provider. `get_session` uses
the metadata it already read; read_session/get_run prove the target before their
content/run call. Interrupt and existing native session verbs prove it before
effectful IO. Do not cache proof across operations. Scope/grant/argument and
configured enabled-route checks happen before proof IO. The original native
source remains distinct from the normalized routing wrapper on subsequent
validation; a second normalization must not promote a synthesized wrapper to
native proof. Proof is an internal condition for the authorized operation,
not a new public metadata permission.
Native reconfigure reuses this verified detail for project/family/model/mode
ceiling checks rather than reading the target twice.

Keyed send has this explicit order:

1. Validate arguments and current configured/enabled binding, flags, scope and
   addressed-namespace grant/allowlist, without requiring a provider connection.
2. Call `DispatchGuard.begin`. ACCEPTED replays the closed receipt immediately
   without provider lookup/proof/read; UNKNOWN/conflict still refuses.
3. Only a new claim resolves the provider, obtains positive native proof, and
   then sends. The existing claim has already latched EFFECT_UNKNOWN before
   those steps. Missing provider/proof failure produces no mutation and leaves
   that claim UNKNOWN; do not delete it, reopen CALL_NOT_SENT, or resend.
4. Only a successful acknowledged send completes the existing closed receipt.

Unkeyed send still requires proof before its effect, without pretending it has
a durable attempt. Accepted replay rechecks revocation but does not revalidate
the present provider's live family: it reports a historical delivery and does
not send anything. No owner/store/kernel phase transition or schema is changed.

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

Create differs from bare-ID delivery: its upstream API already carries the
native `provider` as part of the creation operation. Retain that atomic family
selection and the precomputed expected receipt namespace. Do not add a
post-create proof GET: a lost proof response must not erase a valid ACK's
session/run/queued-message references or invite duplicate creation. Effective
model/mode and later session observations remain separately reported facts.

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
- Actual-adapter target tests must supply native identity independently of the
  caller's namespace: both forged-family directions, missing native family/ID,
  flat-source preservation and address-only metadata refuse. Cover read/run,
  interrupt and all existing session-addressed mirrors, including modeless
  reconfigure; no wrong-family mutation or ACCEPTED receipt may result.
- Prove keyed send replay needs no online/mapped provider after a valid accepted
  receipt, while disabled configuration/revoked grants still refuse. New-claim
  proof failure remains UNKNOWN after owner reconstruction; no reverse phase
  transition, second proof/send or automatic retry is permitted.
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

