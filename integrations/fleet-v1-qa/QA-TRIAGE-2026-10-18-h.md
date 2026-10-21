# Fleet V1 独立 QA — Snapshot H（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 S1/T10/WE 作者、非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：
  - **A（优先）** S1 纠正 Draft #262 `9ada9f585e2c5d5a0ae3185abb27eb83b9b81eba`（stacked on #261 `a6bed15`），配 WE #13 `40b0622`。
  - **B** T10 continuation Draft #263 `c9fb802a9db6ee307a7a239137aa2da03e4bad0f`（stacked on #260 `ca1c05e`，base #258 `5043ba1`）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改任何产品分支、I1、main；未调用 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T04:10:27Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live head |
|---|---|---|
| S1 #262 | `9ada9f585e2c5d5a0ae3185abb27eb83b9b81eba` | 一致 |
| S1 #261 | `a6bed158ff0f1e79778a486d66810fd77a2ad18b` | 一致 |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |
| T10 #263 | `c9fb802a9db6ee307a7a239137aa2da03e4bad0f` | 一致 |
| T10 #260 | `ca1c05e72822c1507374d6d47c6559d4d8e4c605` | 一致 |
| T10 base #258 | `5043ba14232adff86dddbd444597c76865db5f41` | 一致 |

Preflight：load `13.77 / 20.71 / 22.41`（128 vCPU）；cpu PSI some `0.19`；io some `0.41`；**memory PSI `0.00`**；MemAvailable 165 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；另一个用户的 litellm pytest 在跑，不构成本 lane 的锁。判定 **GREEN**，执行两条短命令。

## 2. A — S1 #262 create-time `write_authority`

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<#262> WE_TREE=<WE13> python3 qa_probe_h_s1.py'
```

真实 WE `ServerOSStore` + 真实 serveros daemon；lease 与 daemon 共享同一个 `serveros.db`。**exit 0，22 项全过。**

| 检查 | 结果 |
|---|---|
| A1 live lease A/1 | create 返回 `write_authority.source=work-engine:leases`，lease owner A/token 1；binding claim 从 lease mint A/1 |
| A2 无 lease | create 记录成功但 `writer=None`；`write_authority.lease=None`；**lease 表逐行未变**；未伪造 grant |
| A3 无 lease send | `NOT_CURRENT_WRITER` |
| A4 预取 A/1 | `ADMITTED` |
| A5 伪造 caller owner/epoch | `UNTRUSTED_ACTOR`，不写入 |
| A6 两个竞争 claimant | 第一个 A/1；第二个无 claim；第二个 send `NOT_CURRENT_WRITER` |
| A7 直接写入的伪造 claim B/2 | 相对 live lease A/1 → `NOT_CURRENT_WRITER` |
| A8 SOURCE | serveros 无 `acquire_lease(`、无 `INSERT/UPDATE/DELETE leases`；WE CLI/MCP 无 lease producer；WE schema 无 grants/actors 表；契约声明 `BLOCKED_PRODUCER_BOUNDARY` 与 `create_reports_authority` |

**分类：`BLOCKED_PRODUCER_BOUNDARY`，不是 closed。** 作者没有伪造 actor/grant，也没有替 serveros 写 lease；该实现只把 create 的不可发送状态**在创建时**说清楚（fail-closed reporting）。但两个仓库仍不存在“受信 lease producer”：WE 只把 `acquire_lease` 暴露为 Python 方法，无 CLI/MCP，也没有 actor/grant 权威；serveros 无边界可调用。因此 `session.create → session.send` 在生产上仍不可用，直到 WE 暴露受权的 acquire，或 serveros 获得带 actor/grant 的边界。**Next owner：S1 #262 + WE #13。**

## 3. B — T10 continuation #263

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<#263> WE_TREE=<WE13> python3 qa_probe_h_t10.py'
```

真实 `runtime/bounded_executor.execute` + 真实 `runtime/bounded_invocation.invoke_once`（只把 Docker argv 替换为真实 offline runner）+ 真实 `evaluate_continuation`。临时 state DB / git worktree / receipts。**exit 0，34 项全过。**

| 检查 | 结果 |
|---|---|
| H1 exit 0 | decision **ELIGIBLE**；`parent_accepted=false`、`qa_signature=false`；不是 V1 accepted |
| H1 T10 状态 | `t10_status=BLOCKED_WIRING`、`work_engine_consumption=NOT_WIRED` |
| H2 exit 1 + `ALL TESTS PASSED` | 真实 receipt `process_exit_code=1`；**DENY / PROCESS_EXIT_NONZERO** |
| H3 0 个 open Work Run | spawn 前拒绝（`open Work Engine Run`），invoke=0 |
| H4 多个 open Work Run | spawn 前拒绝（`more than one open Work Engine Run`），invoke=0 |
| H5 无 execution_context | spawn 前拒绝（`no Work Engine execution_context`），invoke=0 |
| H6 execution revision ≠ worktree HEAD | spawn 前拒绝（`not the worktree HEAD`） |
| H7 revoked grant | spawn 前拒绝（`revoked`） |
| H8 跨 principal envelope | spawn 前拒绝（`delegation`） |
| H9 store 的 Run 与 Parent 期望不同 | **DENY / CROSS_RUN** |
| H10 篡改 receipt 字节 | **DENY / RECEIPT_ANCHOR_MISMATCH** |
| H11 缺 anchor | **BLOCKED_WIRING / MISSING_RECEIPT_ANCHOR**，不 eligible |
| H12 forged expected principal | **DENY** |
| H13 binding 来源 | principal = `grant.delegated_by`；run/SHA 来自 store/execution_context，不是 caller 输入 |
| H14 SOURCE | `wake.py`/`continuation.py` 不写 `runs`/`execution_contexts`；checkpoint 无 run/context id |
| H15 SOURCE | WE 全树无 `receipt_sha256`/`continuation_binding`/`SERVEROS_DISPATCH_BINDING`；WE `finalize_run` 不消费 proof；decision 仍 `BLOCKED_WIRING`/`NOT_WIRED` |

**分类：`BLOCKED_WIRING`，不是 REAL_DEVICE/CLIENT_E2E。** producer/consumer 的本地配对正确（PASS 文本不能翻转 exit code；篡改/跨 run/跨 principal 被拒），但 wake/checkpoint 不创建/附着 Work Run 与 execution_context，WE finalize 也不消费 verified receipt。没有 Docker、真实 provider、真实 agent Session 或设备证据。

## 4. 证据级别

| Claim | Level |
|---|---|
| #262 create-time authority / no-lease reporting / send refusal / positive A-1 / forged claim refusal | **LOCAL（WE store + serveros daemon，合成 Item）** |
| #262 无 producer、无 lease write、WE 无 CLI/MCP/actor/grant | **SOURCE** |
| #263 exit 0/1、0/1/ambiguous Run、no context、stale SHA、revoked grant、cross-principal、cross-run、tamper、missing anchor、forged principal | **LOCAL（真实 executor/invoke_once/consumer，合成 runner/DB）** |
| wake/checkpoint 不附着 Run/context；WE 不消费 proof | **SOURCE** |
| REAL_DEVICE / CLIENT_E2E / Docker / provider / OAuth / grant | **NOT_RUN / BLOCKED** |

## 5. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| A 日志（22 checks，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-8a.log` | `b30e5dcd5c377ab3688e847a95ce1adc49e4a12b0ac311018c1e6909139cd727` |
| A 探针 | `.../qa_probe_h_s1.py` | `47cf1f396067140e0fd0a7d1fc13bcbfc1c92a5d9a02e8af81c8a59721bf5741` |
| B 日志（34 checks，exit 0） | `.../run-8b.log` | `6c8341b02877695ec6cfbe389907b4984b604447c5b59b2ea8498cf550552124` |
| B 探针 | `.../qa_probe_h_t10.py` | `8c044bf384853a7c4e805ba745f0d34043abbca7ff0ee2f127506830a6969b82` |
| 只读树 | `h-262`、`h-263`、`h-260`、`g-we13-tree` | `git archive` / 精确 SHA |

**最终判定：`QA_PARTIAL`。** A 的 fail-closed 回报正确，但 `BLOCKED_PRODUCER_BOUNDARY` 仍在；B 的 producer/consumer 本地配对正确，但 `BLOCKED_WIRING` 仍在。真实设备、Docker/provider、OAuth/client E2E、grant 与 production 均未达成；不签 PASS，不进入 RC composition。
