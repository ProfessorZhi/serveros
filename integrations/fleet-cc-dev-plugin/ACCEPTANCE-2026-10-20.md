# Claude Code functional E2E acceptance — 2026-10-20

**Verdict: `LOCAL_CC_FUNCTIONAL_E2E_PASS`**, with the boundaries below. This is
a real Claude Code CLI on this host, over **local stdio**, against a
**temporary fixture daemon**. It is not `REAL_DEVICE`, not the live Fleet Next
ingress, and not `PRODUCTION`.

| field | value |
|---|---|
| product | ServerOS local CC Fleet plugin (Draft PR #300) |
| code SHA | **`b24b15a`** (`b24b15af5b72679751390fab0577d8efb6958905`); local = remote at run time |
| Claude Code | **2.1.272** (`claude --version`) |
| runner | `integrations/fleet-cc-dev-plugin/run_cc_client_e2e.py --model-readbacks` |
| evidence | `work-items/serveros/P1-CC-PLUGIN-20261020/acceptance-model-20261020T062500Z/` (local, not committed) |
| model calls | **1** |
| model cost | **$0.218094** (`total_cost_usd`, 4 turns) |
| host | Tokyo; sequential run, `nice -n 10`, `timeout 900`; swap full but ~155 GiB memory available and IO full PSI ~7% at start |

## Layers

The receipt keeps four layers apart. A pass in one is not a pass in another.

### L0 — code / package structure

| check | status | evidence |
|---|---|---|
| `FIXTURE_BUILD` | **PASS** | the generated TEST_FIXTURE directory |
| `PLUGIN_METADATA` | **PASS** | only documented manifest keys (`stray_keys: []`); name `serveros-fleet-dev-fixture`; binding `type: stdio` |
| `SKILLS_BYTES` | **PASS** | **5/5** `SKILL.md` byte-identical to `integrations/fleet-v1-first-install/plugin/skills/`; SHA-256 below |

```text
fleet-create-with-grants            b0a68f5463528ddf3eea3c37dd1de866621f9c911d458cf9be155e4132174a46
fleet-device-and-session-discovery  62283dd6484a26489db2f5cb4e1a74c8f0bb13a4388f7043bae2f4037a06b664
fleet-evidence-review               b4ab1d93513f7a10894e253fc74422f158508e9cb5bc61fbd71f85eada2ed67d
fleet-reuse-idle-session            7b74361db7a60586cbba871ede985893f07c256668dde0366f499a6b4af52b1e
fleet-safety-boundaries             a61b5f3ca9cf0bf6fa89b2bef1d82ff8686a116ad6f1819571e4548d1e9b6a76
```

### L1 — local Claude Code CLI, no model

| check | status | evidence |
|---|---|---|
| `PLUGIN_LOAD_OK` | **PASS** | `claude plugin validate --strict` exit 0; `plugin details` listed **5 Skills / 1 MCP server** |
| `CLI_NO_SIDE_EFFECT` | **PASS** | `claude --plugin-dir … mcp get plugin:serveros-fleet-dev-fixture:serveros-fleet-fixture` exit 0; `claude plugin list` exit 0; the inline fixture is **not** an installed plugin |
| `MCP_INITIALIZE_OK` | **PASS** | the real client sent `initialize` + `notifications/initialized`; the server answered `serveros-fleet-mcp` |
| `TOOLS_LIST_OK` | **PASS** | `tools/list` returned **33** tools |
| `GLOBAL_CONFIG` | **OBSERVED — unchanged** | `~/.claude.json` and `~/.claude/settings.json` hashes identical before/after; `~/.claude/.credentials.json` not touched |

All Claude Code calls used a fresh `CLAUDE_CONFIG_DIR` under the evidence
directory, so the user-level plugin/MCP configuration was not a source of truth
and was not written.

### L2 — local temporary daemon, virtual facts only

`fixture_stdio_server.py` was started as a **temporary stdio daemon**. It is not
`serverosd`; it opens no socket to a device and its device/session are constants.

| check | status | evidence |
|---|---|---|
| `TEMP_DAEMON_VIRTUAL_FACTS` | **PASS** | 33 tools; one virtual device `device:fixture-node`; one virtual session `device:fixture-node\|claude\|fixture-session-0001`; capability `OBSERVED` (2 of 22 verbs) |
| `SEND_SCOPE_DENIED` | **PASS** | `fleet_send_to_session` → `SCOPE_DENIED`; the fake provider recorded **0** sends |
| `AUTH_SCOPE` | **PASS** | the `fleet:read` principal discovered all 33 tools, and the write was refused before the provider |

The fixture explicitly reports `virtual_facts: true`, `real_device: false`,
`serverosd_started: false`.

### L3 — real model-driven read-only calls

One `claude -p` session, restricted with `--allowedTools` to exactly three
read-only tools. The wire log shows the client performed `tools/call` for each;
each response is `isError: false`.

| tool | result |
|---|---|
| `fleet_list_devices` | non-error; one virtual device |
| `fleet_list_sessions` | non-error; one virtual session |
| `fleet_cloudcli_native_capability` | non-error; `state: OBSERVED` |

`permission_denials: []`, `num_turns: 4`, `is_error: false`. The prompt asked for
read-only calls only; no write, send, create, start, stop or interrupt was
attempted.

## What this acceptance is not

- **Not `REAL_DEVICE`.** The device is `device:fixture-node`, invented by the
  fixture. No real Fleet device was contacted.
- **Not the live Fleet Next ingress.** The live authenticated
  `initialize/tools/list` remains **`BLOCKED_OWNER_OAUTH`**: the surface is up
  and advertises `fleet:read`, but unauthenticated calls are 401 and no CC
  configuration holds a live Fleet grant. See
  `README.md#live-fleet-next-read-preflight-2026-10-20--blocked_owner_oauth`.
- **Not `PRODUCTION`.** No `serverosd`, no Fleet service, no Connector, no
  CloudCLI process, no production grant or registry was touched.
- **No OAuth registration/consent**, no PAT read or borrowing, no credential
  value read/copied/printed.
- **No global plugin install** and no global plugin/MCP configuration write.

## Reproduce

```sh
# all four layers in one receipt; the single model call is opt-in
PYTHONDONTWRITEBYTECODE=1 nice -n 10 \
  python3 integrations/fleet-cc-dev-plugin/run_cc_client_e2e.py \
    --out <evidence-dir> --model-readbacks

# the offline/negative suite (no model, no network off loopback)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_fleet_cc_dev_plugin \
  tests.test_fleet_cc_dev_plugin_client_e2e_gate \
  tests.test_fleet_cc_dev_plugin_offline_stack \
  tests.test_fleet_cc_dev_plugin_cc_client \
  tests.test_fleet_cc_dev_plugin_live_preflight
# Ran 73 tests — OK (expected failures=1)
```

The single `expectedFailure` is the product finding in
`fleet_mcp/fleet.py:918`: a read-only principal is shown
`control_allowed.send_allowed: true` while `send_to_session` refuses
`SCOPE_DENIED`. F1 PR #305 (`4a68903`) fixes it on the read surface; this branch
will turn the expected failure into a real assertion once rebased onto a base
that contains #305. It is not counted as a pass here.

## Review verdict

**PASS at `LOCAL`/`CLIENT_E2E` for the stated fixture path**, with every
non-local claim explicitly `NOT_RUN` or `BLOCKED`. The package, the CLI path,
the temporary daemon, the scope refusal and the one model-driven read-only
sequence are reproducible from the command above; the receipt is
machine-readable and pins the code SHA, Claude version, one model call and its
cost.
