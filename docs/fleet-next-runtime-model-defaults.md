# Fleet Next Runtime defaults — model and permission isolation

This document is the candidate schema for a second, independently installed
Fleet Next service. It does NOT change the current V1 runtime or production
CloudCLI settings. A runtime is a stable credential instance, not an OS user.
One physical device and one OS UID may each host many Claude and Codex runtimes.

## DeepSeek candidates

1. PRIMARY: FJ production gateway (admin) / v4.1flash model
   deepseek-v4-flash-vision-anthropic
2. ALTERNATIVE: FJ production gateway (vince) / v4.1flash model
   deepseek-v4-flash-anthropic
3. ALTERNATIVE: Tokyo gateway 0 / v4.1flash model
   v4.1flash-anthropic

These are historical candidate route identifiers, NOT verified as live and
authorized on every CloudCLI installation. The label "admin" is an upstream
MODEL/BILLING gateway choice, NOT Linux root, Fleet owner, or sudo capability.
No credential copying, silent fallback to another account, or unannounced API
billing substitutions are permitted.

Exception A: official owner-subscribed Claude Code account, separate credentials.
Exception B: MarketOps dedicated Claude Code account, separate credentials.
Neither exception inherits DeepSeek, or a bypassPermissions default. Its owner
must name actual model and project permission policy. Codex has its own family.

## Typed implementation

Optional Fleet local configuration: policy.runtime_create_defaults.

Structure:
  runtime_create_defaults:
    "device:tokyo-shared-dev-01|claude-vince-deepseek":
      "project-approved-id":
        kind: cloudcli-deepseek
        family: claude
        owner_approved: true
        default_model: deepseek-v4-flash-vision-anthropic
        default_permission_mode: bypassPermissions
        allowed_models:
          - deepseek-v4-flash-vision-anthropic
          - deepseek-v4-flash-anthropic
          - v4.1flash-anthropic
        allowed_permission_modes: [default, bypassPermissions]

This field is parsed by fleet_mcp/runtime_create_policy.py into an immutable
typed policy. It requires an EXACT already-bound runtime key and project name.
The target project must be present in policy.create_allow, and both runtime
model and permission allowlists must be subsets of that CreateGrant ceiling.
No wildcard runtime/project; unknown fields, duplicate models or missing
explicit owner approval are fatal at configuration load.

At create_session time, the exact runtime/project entry must also have an
operator_grants entry for the presented caller and the verb create. The caller
must still have fleet:create, an existing project grant and a provider binding
whose allow_create is enabled. It is not enough to own a model identifier.
Only after these checks does Fleet fill omitted model / permission_mode fields;
explicit overrides must also appear in the runtime-specific allowlists.

No runtime defaults entry means LEGACY behavior: let CloudCLI use its own
remembered defaults. It does NOT mean the administrator's DeepSeek profile,
and no default is borrowed from a sibling runtime. A wrong agent family or
unauthorized model fails closed before the remote provider is invoked.

## Sample configuration prerequisites

A correctly shaped full config also requires a separately enrolled provider
binding:
- key = device:tokyo-shared-dev-01|claude-vince-deepseek
- runtime = claude-vince-deepseek
- kind = fleet-node or cloudcli-mcp-http as confirmed on that device
- endpoint = distinct owner-authorized local endpoint (NOT guessed)
- credential_anchor = owner-managed file (NEVER copy secret into a repository)
- allow_create = true only after actual owner approval

The project's policy.create_allow should explicitly list three model IDs and
the permission modes the actual owner allows. The OAuth principal's
policy.operator_grants must explicitly name the exact runtime provider key
with the create verb. Scope and project alone are insufficient for the
new enhanced policy.

bypassPermissions is a CloudCLI/Claude permission-mode choice, NOT OS admin,
and does not bypass Fleet scopes, per-OS account ownership, project ACLs or
the local Node's authorization floor. It must be independently verified by
creating a permitted development session before declaring it LIVE.

## Test and release evidence

- Pure typed model parser: 19 local isolated Python unit tests PASSED.
- Service-level fake connector integration: tests are checked into the
  Draft branch; official CI runner is not functional, so result UNVERIFIED.
- Actual CloudCLI route availability: NOT VERIFIED.
- Official / MarketOps login: not performed, pending owner actions.
- Real Next deployment and Plugin Creator upload: NOT PERFORMED.
- V1 unit, tunnel, account secrets and Plugin ID: UNCHANGED.

Next is to reconcile this stacked change with the A-line worktree, verify
offline regression and then deploy an independently bounded Next canary
alongside V1. V1 uninstall happens ONLY after real acceptance.
