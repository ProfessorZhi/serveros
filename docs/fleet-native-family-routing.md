# Native families on one explicitly declared gateway

This is a source opt-in. It does not enable a live route or change a grant.

A base CloudCLI gateway can serve both Claude and Codex under the same account.
Add this field to that **existing** provider entry only after observing that
capability on its real read-only path:

```yaml
providers:
  device:work-fj:
    # Keep the existing kind, endpoint and vault credential anchor.
    multiplex_native_families: true
```

The default is false. The opt-in requires the unprofiled base runtime
`claude`, declared family `claude`, and the device's own account if an account
is declared. It conflicts with any separate same-device `codex` binding,
including a disabled one. Suffixed namespaces and other accounts never use it.

Use complete session refs returned by listing. A Codex session retains
`device:work-fj|codex|<native-id>`. The row's `provider` and `runtime` remain
the address namespace; `binding_runtime` identifies the actual gateway route.
`sessions_per_runtime` counts namespaces; `sessions_per_binding` counts the
routes actually queried.

Runtime selectors remain exact. For a runtime-selected capability or native
read, use the actual binding runtime (`claude` in this example).
`runtime: codex` does not gain a fallback. Existing mirrored session verbs
whose schema already declares `session_ref` can use the complete Codex ref;
this adds no outer fields to other native tools.

Routing is not authorization. The selected gateway's allow flags still apply.
A base `claude` operator grant does not silently authorize the Codex namespace:
use an existing exact session allowlist, or an explicit grant under
`device:work-fj|codex` for the verified principal and required verb. Native
verbs keep their additional existing grant checks. Account/project model and
permission ceilings are read from the actual binding and include their native
family restriction. The device's own authorization remains independent.

A non-base CloudCLI binding preserves its exact profile namespace after
checking native family evidence. Node wire refs must already identify the
correct device/profile; contradictory legacy metadata is a gap, not permission
to guess another route or invent a profile.

On an opted-in base, new Codex creates return Codex refs. The expected receipt
namespace is fixed before IO. All returned run and queued-message references
are retained, including a queue-only first-message ACK. Creation without a
first message does not invent an execution reference. A changed configuration
never relabels a historical accepted receipt or permits another create under
that key: it may refuse replay pending reconciliation. Disabling the opt-in
does not redirect old Codex refs elsewhere.

The production owner store is still NOT_WIRED. PROCESS_LOCAL retry protection
does not survive service reconstruction; missing ACKs and unknown outcomes
require reconciliation. Source tests, a read-only provider probe, a local
no-model CLI check, authenticated device execution and an actual plugin update
are separate acceptance claims.

