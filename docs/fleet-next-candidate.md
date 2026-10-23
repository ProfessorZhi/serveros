# Fleet Next — the candidate, and how to take it back

> **HISTORICAL (2026-10-14 candidate).** This describes the five-branch
> candidate as it stood at `fleet-mcp-3`; that work is now on `main`. The
> current surface is `fleet-mcp-6`: **44 tools = 11 hand-written + 31 mirrored
> CloudCLI verbs + 2 capability reads**, and the consent step is account +
> password + TOTP with a PAT fallback. Numbers, revisions and status rows below
> are what was measured at the time and are kept as a record, not as current
> state.

This file names one candidate as one thing. It exists because "the Fleet Next
candidate" was previously a chain of five stacked branches, and a chain is not
something you can turn off, roll back or deploy as a unit.

## What this candidate is

The A-line work, collapsed into a single tip so it can be **enabled, disabled,
or rolled back as one object** rather than as five branches that must move in
order.

| | |
|---|---|
| inherited from | `feat/fleet-cloudcli-full-inheritance-20261013` → `feat/fleet-multi-runtime-identity-20261014` → `feat/fleet-release-candidate-20261014` → `feat/fleet-feishu-sso-broker-20261014` (`634a392`) |
| plus | `design/fleet-next-agent-federation-20261014` (`e137fa8`) — the cloud-facing plugin source |
| tool surface revision | `fleet-mcp-3` (`fleet_mcp/version.py`) |
| merge base with `main` | `91330f2`; `main` had advanced two commits over disjoint files, so the integration carries no conflict resolution |

Line-level review of the inherited work belongs to its own pull requests
(#177, #182, #184, #188). Nothing here re-authors it.

## The surface, counted from the code

- **34 tools** = `TOOLS` (10 hand-written, including `fleet_create_project`) +
  `NATIVE_MIRROR_TOOLS` (22 CloudCLI verbs from 1.39.0) + the two
  mirror-descriptor tools. `SERVED_TOOLS` in `fleet_mcp/tools.py` is the
  definition; `tests/test_fleet_multi_runtime_routing.py` and
  `tests/test_fleet_release_plan.py` assert the count.
- **6 scopes** — `fleet_mcp/scopes.py`: `fleet:read`, `fleet:send`,
  `fleet:control`, `fleet:create`, `fleet:approve`, `fleet:navigate`.
  `ALLOWED_SCOPES` equals the implemented set on purpose: what is advertised is
  what is enforced.
- **Default grant** — `DEFAULT_GRANTABLE_SCOPES = (fleet:read, fleet:send)`.
  `create`, `control`, `approve` and `navigate` are implemented and **not**
  default-granted; an operator who writes nothing does not obtain them.
- **Grants** — authority is `operator_grants: {principal: {device: [verbs]}}`.
  All five verbs are accepted; a wildcard principal or a wildcard device is
  refused at load. A device is grant-eligible only when the registry declares
  `ownership: company`.

## Default-disabled: what is actually off

The honest summary is that the surface is **absent rather than idle**: with no
binding file the process refuses to start, and the loader's refusal is what
`runtime.main()` turns into exit code 3.

| element | default | note |
|---|---|---|
| the binding file | absent | absent → refuse to start (exit 3), not "empty fleet" |
| `allow_send` / `allow_create` / `allow_control` | `False` | per device; a bound device is descriptive until one is set |
| `send_allow` / `control_allow` / `create_allow` | empty | nobody may drive anything |
| `operator_grants` | `{}` | nobody holds any verb on any device |
| `auth.grantable_scopes` | `read`, `send` | the OAuth ceiling |
| `auth.feishu` | `None` | the two SSO routes 404 exactly as an unknown path does |
| `auth.oauth_enabled` | **`True`** | OAuth is wired unless disabled — but still refuses without a verifier (account/password anchor, or the PAT fallback) |
| `ProviderBinding.enabled` | **`True`** | a binding has a route. It grants nothing until a verb flag is set |
| `policy.read_redact` | **`True`** | credentials read out of transcripts are redacted |

Three defaults are `True`, and they are listed rather than omitted because a
reader who assumes "everything defaults to off" would be wrong about them.

**There is no single global kill-switch flag.** The nearest things are: no
binding (exit 3), `systemctl --user stop serveros-fleet-mcp`, and
`auth.oauth_enabled: false` — and the last one closes only the off-host OAuth
path, not the owner-token path. Stated as an absence because the alternative is
to describe a mechanism that does not exist.

## Enabling, minimised

An operator writes, in the (never-committed) `fleet-mcp.local.yaml`:

1. a binding — without it nothing starts;
2. `allow_send: true` on a device to drive it; `allow_create: true` **plus** a
   project key in `policy.create_allow` to mint sessions; `allow_control: true`
   **plus** the session in `policy.control_allow` to interrupt;
3. `policy.operator_grants` to say *who* may use which verb on which device;
4. `auth.grantable_scopes` to raise the OAuth ceiling above read+send, if the
   caller is meant to receive more;
5. a whole `auth.feishu` block to turn SSO on — `enabled: true` plus
   `app_scope`, `app_id`, `secret_anchor`, `redirect_uri` and a **non-empty
   whitelist**. A half-configured sign-in is refused; so is an enabled block with
   an empty whitelist.

The host service is then placed with
`python3 fleet_mcp/deploy/install.py install --config <binding>` (`--dry-run` to
preview). `fleet_mcp/deploy/release_plan.py` performs no install at all: its mode
is hard-coded `DRY_RUN`, it reports `"writes": []`, and its `--execute` only
prints a note saying it will not.

## Rolling back: four mechanisms, by scope

| scope | action | what it costs |
|---|---|---|
| the served code | `install.py rollback` — repoints `ingress/current` at `previous` and restarts | one symlink swap; refuses if there is no `previous` ("a rollback would be a no-op wearing the word rollback") |
| the host service | `install.py uninstall` (`--purge` also deletes revisions) | disables, stops and removes the unit; **the binding, owner token and audit log survive by design** |
| behaviour | turn off the verb flags in the binding and redeploy, or stop the unit | needs a restart |
| SSO | `enabled: false`, or delete the block | routes return to 404 |
| device half | `fleet_mcp/node/uninstall.sh` on the device's own account | removes the units and any connector/tunnel; leaves the binding, node secret and logs unless purged |
| OAuth clients | `--revoke-client <id>` or `POST /oauth/revoke` | tokens die immediately, no restart |

## Status, in the four words that mean different things

| | state | basis |
|---|---|---|
| **CODE READY** | the 34-tool surface, 6 scopes, grant model, per-runtime routing, Feishu SSO broker and the packaging above | present in this branch |
| **TESTED** | all of the above | asserted in `tests/test_fleet_*.py`, including `test_fleet_multi_runtime_routing.py` (34 tools, per-runtime grants, Dell two-runtime routing) against **fake providers** |
| **DEVICE NOT DEPLOYED** | every device | `docs/fleet-multi-runtime-design.md`: "nothing here is deployed and no second runtime exists on any machine yet"; `fleet_mcp/node/README.linux.md`: "`device:llm-lab-dell` is not enrolled by any of this" |
| **CLIENT NOT INSTALLED** | every client | no upload, no install, no refresh has been performed (see `integrations/chatgpt-fleet-next/PACKAGING.md`) |

Per-device dry-run gaps are collected separately in
`docs/fleet-next-device-readiness-gaps.md`.

## What this candidate does **not** settle

- **The live Tokyo binding would not start under this branch.** It carries
  `send_allow: ['*']` with `allow_wildcard_send: true`; this branch's loader
  refuses a wildcard at load. `fleet/release-plan-owner-grant.md` §2 records
  this against the 2026-10-13 measurement. Migrating it is an operator edit, not
  a code change.
- **The live grant cannot be widened in place.** Scopes are minted into the
  token at authorization; the live token carries `(fleet:read, fleet:send)`, so
  `create`/`control`/`approve`/`navigate` each need a fresh human consent.
- **`fleet-mcp-contract.yaml` `blocked_on`** names two decisions that are the
  operator's and cannot be closed by an agent: `public_exposure_decision` and
  `chatgpt_oauth_consent`.
- **`control` has no per-principal grant path** (`fleet/owner-grant-3devices.md`
  G1): the only seam is a device-level `allow_control`. G2–G4 in that file
  (an anchor read by nothing, no `close` verb, `session_start` being a spawn
  rather than a resume) are unchanged by this candidate.
- **Documentation drift (fixed since):** the docstring and comments in
  `fleet_mcp/cloudcli_native.py` used to describe `approve`/`navigate` as
  planned-and-not-implemented while `scopes.py` had them in
  `IMPLEMENTED_SCOPES`. The prose was corrected after this candidate; the
  behaviour was right throughout — both remain default-deny.
