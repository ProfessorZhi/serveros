# Fleet V1 独立 QA — Snapshot G（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 S1/T10/WE 作者、非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：
  - **A（优先）** S1 纠正 Draft #261 `a6bed158ff0f1e79778a486d66810fd77a2ad18b`（stacked on #241 `d0754ed`），配 Work Engine #13 `40b0622`。
  - **B** T10 producer Draft #260 `ca1c05e72822c1507374d6d47c6559d4d8e4c605`（stacked on #258 `5043ba1`）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改任何产品分支、I1、main；未调用 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T03:46:25Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live head |
|---|---|---|
| S1 #261 | `a6bed158ff0f1e79778a486d66810fd77a2ad18b` | 一致 |
| S1 base #241 | `d0754edfd65a490213a40b3033a70a43b0a4a187` | 一致 |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |
| T10 #260 | `ca1c05e72822c1507374d6d47c6559d4d8e4c605` | 一致 |
| T10 base #258 | `5043ba14232adff86dddbd444597c76865db5f41` | 一致 |
| I1 #254 | `497d955` | manifest pin 与上述 #261/#260/#258/#241/WE13 完全一致 |

Preflight：load `17.14 / 22.96 / 21.46`（128 vCPU）；cpu PSI some avg10 `0.20`；io some `0.08` / avg300 `2.30`；**memory PSI `0.00`**；MemAvailable 163 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 pytest/测试锁；其他用户的 quay driver 活动不计入本 lane。判定 **GREEN**，执行 1 条 A 命令 + 1 条 B 命令。

## 2. A — S1 #261 live-lease send gate

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_MODE=old QA_TREE=<#241> ... python3 qa_probe_g_s1.py;
              QA_MODE=new QA_TREE=<#261> ... python3 qa_probe_g_s1.py'
```

同一临时 state dir 内使用**真实 WE store**（#13）+ **真实 serveros daemon**（`agent.binding.record/resolve` + `admit_work`）；WE lease 与 daemon 的 `_item_roots()` 指向同一个 `serveros.db`。

| 运行 | exit | 结果 |
|---|---|---|
| #241 old（red） | **1** | 2 个 SECURITY_FAIL：显式 `writer=EVIL/999` 被记录；admission `ADMITTED`；current writer `EVIL/999`，而 WE live lease 是 `A/1` |
| #261 new（green） | **0** | **24 项全过**；`CONTROL_OK` |

### 2.1 已关闭（LOCAL）

- **E5 spoof**：`agent.binding.record` 显式 writer → `UNTRUSTED_ACTOR`；不再写入。
- **positive A/1**：新 execution binding 从 live lease mint `owner=A, token=1`；`agent.binding.resolve` 返回 `write_authority.source=work-engine:leases`；`admit_work(authority=...)` → `ADMITTED`。
- **no lease**：binding 记录无 claim；send → `NOT_CURRENT_WRITER`。
- **expired lease**：`live_lease` 排除；行仍留在盘上作为证据。
- **released/deleted lease**：`live_lease` None；binding 无 claim。
- **wrong owner/token**：binding claim `B/2` vs live lease `A/1` → `NOT_CURRENT_WRITER`。
- **stale source after handoff**：closed binding → `THREAD_CLOSED_FOR_NEW_WORK`。
- **concurrent same-Item**：第一个 binding 取得 claim；第二个无 claim；WE 第二个 live acquire → `LEASE_HELD_BY_OTHER`。
- **token moved**：lease 换 owner/token 后旧 claim → `NOT_CURRENT_WRITER`。
- **no-principal / explicit writer**：均拒绝。
- **T1/T2/T3/T6/T7**：T1/T2/T3/T6 的上述子项 LOCAL 通过；T7 的显式 writer/no-principal 通过；**grant 与真实设备仍 NOT_RUN**。

### 2.2 新发现的 P0 不完整性：生产 session create 不取得 lease

- SOURCE：serveros #261 全树**没有 `acquire_lease(`**；只有只读 `live_lease` 与 `held_leases`。binding 的 claim 由 daemon 从**已存在**的 live lease mint，而不是由 create 自己 acquire。
- LOCAL：在一个**没有 lease** 的 Item 上执行 `agent.binding.record`，记录成功但 `writer=None`；随后 send → `NOT_CURRENT_WRITER`。即“create → send”在没有任何外部 actor 先取得 lease 时是**不可用**的。
- 这与作者声明一致（“production session creation acquires NO”）。本地 gate 修复成立，但 **S1 仍不能自我 arm**：必须由 WE CLI/operator 在 create 之前取得 lease。若 composition 把 #261 当成“send 已可生产使用”，会得到一个永远拒绝的路径。
- **Next owner：S1 #261 + WE #13**。要么 create 路径按已认证 owner 取 lease，要么在契约中明确“lease 由外部取得”并把它作为 S1 的前置条件/验收项。I1 的 `E175–E177` 已经要求合法 session-create producer 取得权威 lease；本次实测确认该条件目前**不成立**。

## 3. B — T10 producer #260

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<#260> python3 qa_probe_g_t10.py'
```

真实 `bin/serverosctl work dispatch` + 真实 `runtime/bounded_invocation.invoke_once` + 真实 consumer `bounded_provenance.evaluate_dispatch`；临时 DATA_ROOT、临时 git worktree、合成 offline runner。**exit 0，37 项全过**。

| 检查 | 结果 |
|---|---|
| B1 子进程打印 `ALL TESTS PASSED` 但 exit 1 | 真实 receipt `process_exit_code=1`、`status=EXITED`；decision **DENY / PROCESS_EXIT_NONZERO**；不签 QA |
| B1 stdin | 等于 canonical T10 binding（cycle/action/item/run/SHA/principal/grant/runner） |
| B2 exit 0 + 精确 binding | decision **ELIGIBLE**（仅 QA review）；`eligible_for_qa_review=true`，`parent_accepted=false`，`qa_signature=false` |
| B2 T10 状态 | `t10_status=BLOCKED_WIRING`，`work_engine_consumption=NOT_WIRED` |
| B3 残缺 `--t10-*` | spawn 前拒绝，`Nothing was run`，无 receipt |
| B4 SHA ≠ worktree HEAD | spawn 前拒绝，无 receipt |
| B11 tracked tree dirty | spawn 前拒绝，`clean tracked worktree` |
| B5 revoked grant | spawn 前拒绝，无 receipt |
| B7 篡改 receipt 字节 | decision **DENY / RECEIPT_ANCHOR_MISMATCH** |
| B8 伪造 work_run | 与 receipt input digest 不符 → **DENY / INPUT_DIGEST_MISMATCH** |
| B9 伪造 principal | 同上 → **DENY**；CLI 无 `--t10-principal` flag；principal 来自 `grant.delegated_by`；传入未知 flag 不会改变 binding principal |
| B6 非 T10 dispatch | decision **BLOCKED_WIRING**（不是 ELIGIBLE） |
| B10 WE 消费 | WE 全树无 `receipt_sha256` / `SERVEROS_DISPATCH_BINDING` / `t10_binding`；decision 仍 `BLOCKED_WIRING`/`NOT_WIRED` |

**T10 仍是 producer/consumer LOCAL 配对，不是已完成的门**：Work Engine `finalize_run` 不消费 proof，continuation 仍 `BLOCKED_WIRING`。没有真实 Work Engine finalize、真实 runner、provider 或设备证据。

## 4. 证据级别

| Claim | Level |
|---|---|
| #261 spoof 被拒、lease A/1 被 admit、无/过期/释放/错 token/closed source 拒绝 | **LOCAL（两仓，合成 item/claim）** |
| #261 生产 create 不 acquire lease | **SOURCE + LOCAL** |
| T1/T2/T3/T6 的子项 | **LOCAL**；grant/真实设备 **NOT_RUN** |
| T10 B1–B11（真实 CLI + bounded_invocation + consumer） | **LOCAL（合成 runner/DATA_ROOT）** |
| WE 不消费 T10 proof | **SOURCE** |
| REAL_DEVICE / CLIENT_E2E / OAuth / production | **NOT_RUN / BLOCKED** |

## 5. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| A red/green 日志 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-7a.log` | `0c648248d80f9db34623715e4885e4a654ade7e8b05e5584ae7a888f07a097f0` |
| A 探针 | `.../qa_probe_g_s1.py` | `3a108044d39865e2840a03ff05019e6eb2c32cec147fd06d32948f4fb6a7f46f` |
| B 日志 | `.../run-7b.log` | `55091a187b7ac80036cbb3b3409995581b417f565360048da2673275902d40b4` |
| B 探针 | `.../qa_probe_g_t10.py` | `ca71e84a023a837f7620207048b8265ade7a422fa5c8cc2cb837db13ad360179` |
| 只读树 | `g-s1-old`、`g-s1-new`、`g-we13-tree`、`g-t10-258`、`g-t10-260` | `git archive` / 精确 SHA shallow fetch |

**最终判定：`QA_PARTIAL`。** A 的 spoof/ES 修复在 LOCAL 层成立，但生产 create 不取 lease 的缺口使其仍不能自我 arm；B 的 T10 producer 在 LOCAL 层正确拒绝，但 WE finalize 不消费 proof。真实设备、OAuth/client E2E、grant 与 production 均未达成，不签 PASS、不进入 RC composition。
