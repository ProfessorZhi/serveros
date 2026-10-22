# Fleet Next V1 人工验收手册

> **这篇回答什么：** 独立评审者拿到 `REVIEW_CANDIDATE_ONLY` 结论之后，具体看哪些东西、按什么顺序看、哪些空白必须记成 UNKNOWN。本文的每一步都是「看」：不部署、不安装、不切换、不发布。
>
> **状态：** 运行手册。工具面为 `34 tools / 6 scopes`（`fleet-mcp-5`）；验收时以 `fleet_mcp/version.py` 与实际 `tools/list` 回读为准，不要照抄本页数字。
>
> **怎么读：** §0 先跑离线检查，知道哪些结论有机器背书；§1–§3 是评审资格、端点与工具面；§4 是每设备/账号矩阵；§5 是机器检查清单；§6 是「被要求启动一个 run」时的准入判断；§7 是必须留下什么记录。
>
> **底线：** 离线门（`release_evidence.py`）与准入建议（`health_admission.py`）只判断材料是否成形，**不能证明任何回执是真的**。fixture 里的 `provenance: LIVE` 是门要求的字段，不是现场测量。

## 0. What the offline suite does and does not cover

Run it first, because it is cheap and it tells you which claims have a machine
behind them:

    python -m unittest tests.test_fleet_next_health_admission \
                       tests.test_fleet_next_release_evidence

Both modules are pure: no network, no secrets, no other OS account, no GPU. The
two positive controls are files under `tests/fixtures/fleet_next/` and their
`provenance: LIVE` / `source: LIVE` values are **fields the gate requires, not
measurements**. A fixture is not field evidence; treating one as evidence is the
failure this whole gate exists to prevent.

Already covered offline, so you do not have to re-derive it by hand:

| Claim | Covered by |
|---|---|
| Repeating a create with the same `idempotency_key` makes one session | `tests/test_fleet_create_session_runtime.py` (runtime- and project-scoped keys), `tests/test_fleet_create_session.py` |
| An idempotency key does not replay across runtimes or projects | `tests/test_fleet_create_session_runtime.py` |
| Scope denial is a refusal, not a silent no-op | `tests/test_fleet_mcp_surface.py`, `tests/test_fleet_create_policy.py` |
| The surface is 34 tools and 6 scopes, and the documents say so | `tests/test_fleet_next_release_gate.py` |

Not covered offline, and not coverable offline: **whether any of it works on a
real device with a real account.**

## 1. Before you start

- You are reviewing; you are not the owner of either Linux account. Do not
  `su`, do not read another user's credentials, do not restart Strata or any GPU
  workload, and do not start anything that allocates GPU memory.
- Collect the snapshot and the manifest as **non-secret** files. Neither
  document may contain a token, a cookie, a private key or a password, and the
  audit allowlist in `fleet_mcp/disclosure.py` is what enforces that on our
  side.
- A single reviewer looks at one manifest. Nothing here is a consensus
  mechanism.

## 2. Identity and endpoints

Look at the two MCP URLs yourself. They must be different endpoints, both real
HTTPS, both ending in `/mcp`, with no credentials, query string or fragment
embedded. Then confirm the two **plugin ids** are different: the whole
side-by-side plan fails silently if Next overwrote V1, and a manifest that
reports two ids is not proof that two exist.

## 3. The surface

`34 tools / 6 scopes` is a claim about what is *reachable*, and a registered
tool name is not a usable verb. For each of the six scopes, ask for the receipt
that shows a call **succeeding** with it and a call **refused** without it. The
scope list is exact: an extra one means a client can be issued authority no code
path honours.

## 4. The managed matrix

For every runtime on the owner's required list, the manifest must carry all six
dimensions. Read them as a table and ask two questions:

    Device × OSUser × uid × family × credentialInstance × Runtime × Project

1. **Are the Dell accounts separated?** `vince` (uid 1002) and `fjd` (uid 1000)
   must be two rows with two uids. If one row claims both, or one account
   appears under two uids, stop — that is the collision the six-dimension key
   exists to catch, and a green manifest that has it is describing a runtime
   nobody observed.
2. **Are two Claude accounts under one UID two rows?** They differ only in the
   credential instance. If the manifest has one row for two accounts, one of
   them was never verified.

For each row, the promised operations (`list` `read` `create` `send` `get_run`
`interrupt` `resume` `recover_after_disconnect`) must all be `PASS`. A
provider-gated operation (`approval` `reconfigure` `close`) may be `UNSUPPORTED`
— but read the capability receipt and satisfy yourself the provider really lacks
it. `UNKNOWN`, or a missing field, means nobody checked; it is not a pass.
Codex must name its provider and show a receipt for it.

Then ask the two questions the JSON cannot answer: was the session the receipt
describes **created by this runtime**, and was it **read back from the device**?
A receipt that only records a request going out is not an observation.

## 5. The machine checks

| Check | What to look at |
|---|---|
| Next cgroup | the unit's own `memory.max`/`tasks.max` readback, not a template value |
| V1 before/after | V1's health, before and after the Next work, with timestamps |
| Next health | a real request to the Next endpoint, with a timestamp inside the snapshot window |
| **Next-only rollback** | the rollback trace, and evidence that **V1 was not touched** — the V1 plugin id, endpoint and health must be the same before and after |
| Public HTTPS | the certificate and the endpoint as reached from outside, not from loopback |
| Feishu SSO | a real login, plus what happens to an account that is not on the whitelist |
| OAuth DCR + PKCE | dynamic client registration, an authorization-code flow with PKCE, and a token refresh; plus revocation |
| Same-plugin-ID ZIP update | the plugin id **before and after** the Skills/ZIP update is the same |
| Backend-only update | a backend change with **no plugin reinstall** — the client's tool list is refreshed, not re-installed |
| Short-window PSI and caps | a current PSI figure and real per-project caps. Swap *occupancy* is not a stall; do not accept a large `SwapUsed` alone as a blocker either, and do not accept it alone as evidence of health |

## 6. Admission, if you are being asked to start something

`health_admission` is advice about **one more** bounded run on one runtime, from
a snapshot under 60 seconds old. Check by hand that:

- the owner grant is present and the credential state is `active` — a credential
  that was revoked and restored is `active` and owes a **fresh** sample like any
  other;
- a **stuck session** is present in the report and is being counted against the
  runtime's capacity. It must block a new run: a wedged session that still holds
  the runtime is the case where "there is room, so start another" is the wrong
  answer. If the snapshot does not say how many sessions are stuck, that is
  `unknown` — not zero;
- Codex runtimes carry their provider.

A `GO_ONE_BOUNDED_RUN` is still not permission. The owner grant, the device's
own binding and the Node floor all still apply, and the adviser saw none of
them.

## 7. Recording the outcome

Record, for the reviewer who comes after you: the manifest hash, the six
dimensions you checked, which receipts you opened and which you could not, and
every place the manifest said `UNKNOWN`. A gate that passes on `UNKNOWN` data is
the failure mode this whole document is written against.
