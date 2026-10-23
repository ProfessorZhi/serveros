# Fleet Next — 设备就绪缺口（只读推演）

> **这篇回答什么：** 在 Tokyo、FJPCOS Windows、Dell A6000 上跑一次 Fleet Next dry run 之前，每台设备、每个 verb 分别还缺什么。
>
> **状态：** 缺口清单，2026-10-13，从**仓库**读出，不是现场测量。文中的「7 tools served」等数字是当天值；当前工具面以 `fleet_mcp/version.py` 为准。
>
> **方法边界：** 只读本分支。没有联系任何设备、没有登录任何账号、没有读取或推断 `fjd` 的任何东西。`fjd` 的一半按构造就是 `UNKNOWN`，并如实记为 `UNKNOWN`，而不是用看起来合理的猜测填上。
>
> **两个先知道的事实：**
> 1. **registry 注册了 5 台设备，不是 3 台。** `fleet/registry.yaml` 里有 `device:server-beijing`、`device:personal-dell`、`device:work-fj`、`device:tokyo-shared-dev-01`、`device:llm-lab-dell`；只有 3 台带 `ownership: company`，在范围内：`company_devices() == [device:llm-lab-dell, device:tokyo-shared-dev-01, device:work-fj]`。另外两台是 personal，被发布计划排除，不是遗漏。
> 2. **registry 无法声明「一个账号下的第二个 runtime」。** `runtime_bindings` 只能写 `runtime_ref: agent-runtime:<family>`，没有字段能表达「以及 `fjd` 账号下的第二个 CloudCLI」。这正是 `docs/fleet-multi-runtime-design.md` §12 记录的类别错误。所以在 Dell 上，registry 能说 *claude-code 属于这里*，不能说 *第二个 runtime 存在*。
>
> **怎么读：** 先看 Legend 的五个状态词，再按设备读表。每张表的 `on the machine` 列才是设备事实；`code` / `tested` 两列只说明仓库里的代码与测试。

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
