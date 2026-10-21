# Fleet Next — device readiness gaps (dry run, read from the repository)

Scope: what would have to be true, per device and per verb, before a Fleet Next
dry run could be executed on Tokyo, the FJPCOS Windows workstation, and the Dell
A6000.

Method: read from this branch only. **No device was contacted, no account was
logged into, and nothing belonging to `fjd` was read, touched or inferred.** The
`fjd` half is UNKNOWN by construction, and it is recorded as UNKNOWN rather than
filled in from a plausible-looking guess.

Two facts reshape the question before the matrix:

- **The registry enrolls five devices, not three.** `fleet/registry.yaml` holds
  `device:server-beijing`, `device:personal-dell`, `device:work-fj`,
  `device:tokyo-shared-dev-01`, `device:llm-lab-dell`. Only three carry
  `ownership: company` and are in scope:
  `company_devices() == [device:llm-lab-dell, device:tokyo-shared-dev-01, device:work-fj]`.
  The other two are personal and are excluded by the release plan, not by
  accident.
- **The registry cannot declare a second runtime on one account.** A
  `runtime_bindings` entry names `runtime_ref: agent-runtime:<family>` and has no
  field to say "and a second CloudCLI under account `fjd`". That is the category
  error `docs/fleet-multi-runtime-design.md` §12 records. So for the Dell, the
  registry can say *claude-code belongs here* and cannot say *the second runtime
  exists*.

Legend — **READY** code path exists in this branch · **TESTED** asserted by an
executing test · **NOT DEPLOYED** no such thing on the device · **NOT INSTALLED**
no connector/binding present · **UNKNOWN** not determinable from the repository.

## `device:tokyo-shared-dev-01` — company, linux, `claude-code` only

| verb | code | tested | on the machine |
|---|---|---|---|
| enumerate | READY | TESTED | deployed-ish — **but the live binding would not start under this branch** |
| send | READY | TESTED | live `allow_send: true` |
| create | READY | TESTED | **not granted**: `allow_create` absent; no `fleet:create` in the token |
| control | READY | TESTED | **not granted**: `allow_control` absent; no scope; and G1 below |
| approve | READY (default deny) | TESTED | **not granted**; node key defaults off |
| navigate | READY (default deny) | TESTED | **not granted**; node key defaults off |

- The measured live state is in `fleet/release-plan-owner-grant.md` §2
  (2026-10-13): `allow_send: true`, `send_allow: ['*']`,
  `allow_wildcard_send: true`, **7 tools served**, and — the load-bearing line —
  *"the live binding is REFUSED by this branch's loader (wildcard)"*. This
  branch refuses a wildcard `send_allow` at load. **The currently running
  binding does not start under this branch.** Migrating it to `operator_grants`
  is an operator edit and has not been done.
- `agents: [claude-code]` only: *"Codex is not installed here and never has
  been."*

## `device:work-fj` — company, Windows 10

| verb | code | tested | on the machine |
|---|---|---|---|
| enumerate | READY | TESTED | present in the live binding as a provider |
| send | READY | TESTED | live `allow_send: true` |
| create | READY | TESTED | **not granted** |
| control | READY | TESTED | **not granted**; G1 applies |
| approve / navigate | READY (default deny) | TESTED | **not granted**; the Windows template writes neither key |

- **The installer writes a binding that can only ever send.** `install.ps1`'s
  template carries `device_ref`, `secret_anchor`, `cloudcli_endpoint`,
  `cloudcli_credential_anchor`, `bind`, `port`, `allow_send_to: []` and notes —
  no `runtime:` key, no `allow_create`, no `allow_control`, no `allow_approve`,
  no `allow_navigate`. An empty `allow_send_to` refuses every send, including
  the Fleet owner's. So a Windows machine installed by this script is the base
  runtime and a sender, until a human edits `fleet-node.local.yaml` by hand.
- **The Windows path is proven hygienic, not functional.** The only Windows-side
  test, `tests/test_fleet_secret_hygiene.py`, makes **static text assertions on
  `install.ps1`** — it greps the script. PowerShell is never executed in this
  suite, so nothing here demonstrates that the scheduled tasks, the connector or
  the tunnel actually start.
- `start-tunnel.ps1` builds the reverse tunnel with `BatchMode=yes` but **does
  not pin the host key** — no `StrictHostKeyChecking`. Named as an assumption,
  not asserted to be wrong: it trusts whatever `known_hosts` state that box has.
- Hard-coded installation assumptions to check on the machine, not from here:
  repo path `D:\projects\serveros-fleet-work\repo`, endpoint
  `http://127.0.0.1:3001/mcp`, forwarded ports `3402`.
- `fleet/profiles.yaml` → `windows-corporate`: volumes C:/D:/Z: BOUND, E:/F:
  NOT_ASSERTED, `home: UNBOUND`, artifacts path UNVERIFIED.

## `device:llm-lab-dell` — company, linux, two intended runtimes

| verb | code | tested | on the machine |
|---|---|---|---|
| enumerate | READY for base **and** second runtime | TESTED **with fake providers** | **NOT DEPLOYED** |
| send / create / control | READY per runtime | TESTED (routing) | **NOT DEPLOYED** |
| approve / navigate | READY (default deny); the per-runtime signature path is v2, v1 stays byte-identical | TESTED | **NOT DEPLOYED** |
| the `fjd` runtime itself | **CODE READY** — `claude-fjd` is derivable, `provider_key`/`runtime` parsing accepts it | TESTED **only via fakes** | **NOT DEPLOYED**, and the `fjd` half is explicitly the operator's work |

- `fleet_mcp/node/README.linux.md`, verbatim: *"`device:llm-lab-dell` is not
  enrolled by any of this"* — the Linux bootstrap deliberately does not enroll
  the Dell; its identity "needs human approval".
- `docs/fleet-multi-runtime-design.md`: *"nothing here is deployed and no second
  runtime exists on any machine yet."*
- **Three of the four isolation facts are declared unverifiable from the Fleet
  host.** `fleet_mcp/runtime_identity.py`'s `ISOLATION_CONTRACT` names
  `os_account`, `cloudcli_credential`, `state_root` (owner: `device`) and
  `node_secret` (owner: `device + host`). The host "cannot see another account's
  home, credential, or state root, and must not report them as verified".
- Profile gaps to settle before a deploy, from `fleet/profiles.yaml` →
  `linux-gpu-compute`: `data_root: UNBOUND`, **no `scratch`**, **no `volumes`
  block**, `knowledge.corporate: UNBOUND`.
- `agents: [claude-code]` only: *"Codex is not installed on this machine."*

## What blocks any dry run, and is policy rather than code

1. **Wildcards are refused at load.** The live Tokyo binding uses
   `send_allow: ['*']`. Under this branch it does not start. This is the single
   most immediate blocker and it is a configuration migration.
2. **Scopes cannot be widened in place.** They are minted into the OAuth token
   at authorization; the live token is `(fleet:read, fleet:send)`. `create`,
   `control`, `approve` and `navigate` each require a **fresh human consent**.
3. **Two decisions are the operator's**, per `fleet-mcp-contract.yaml`
   `blocked_on`: `public_exposure_decision` and `chatgpt_oauth_consent`. Neither
   may be decided by an agent, and the second "must not be simulated in a
   report".
4. **Grant-path gaps that are not device-specific** (`fleet/owner-grant-3devices.md`
   §5): G1 `control` has no per-principal grant (only a device-level
   `allow_control`); G2 `auth.owner_grant_anchor` is parsed and read by nothing;
   G3 no `close` verb; G4 `session_start` spawns rather than resumes.

## UNKNOWN — recorded as unknown, not estimated

- Whether the Windows tunnel/task is **up** now. `transport-contract.yaml`
  separates "controller available" from "presence known"; no repository artifact
  records that box's live state, and the release plan declines to touch it.
- Whether the live Tokyo process is **running this branch**. Last measured
  2026-10-13, refused at load.
- The **`fjd` OAuth client id** and its grant rows. `operator_grants` is keyed by
  principal, and no committed file carries one — and by the standing constraint
  this stays UNKNOWN; resolving it would require logging into that account.
- Whether a Dell deploy needs `data_root`/`scratch` at all. The profile leaves
  them unbound and nothing decides it here.

## Not done, on purpose

No login to `fjd` or any account; no device contacted; no binding written; no
service started, stopped or restarted; no scope changed; no consent simulated.
Everything above is read from committed files, and every claim carries the file
it came from.
