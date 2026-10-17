# Fleet V1 独立 QA — Snapshot E（2026-10-18）

- **角色**：独立 QA lane（campaign `ProfessorZhi/serveros#244` / `#237`）。非 S1/S2 开发者、非 I1 integrator。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：跨仓 S1 写租约安全对
  - serveros #241 `d0754edfd65a490213a40b3033a70a43b0a4a187`（reader / send admission）
  - work-engine #13 `40b062209de4a989a84b04ea57b6cc8657e1be1e`（lease writer）
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改任何产品分支、I1、main；未调用真实 provider/网络/凭据；无 subagents。

## 1. Snapshot 与 preflight

快照 UTC **`2026-10-17T22:19:56Z`**（local `2026-10-18T06:19:56+08:00`）。

| 对象 | 精确 head | live head |
|---|---|---|
| serveros #241 | `d0754edfd65a490213a40b3033a70a43b0a4a187` | 一致 |
| work-engine #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致（`refs/pull/13/head`） |
| I1 #254 | `613efd4` | 一致；manifest 对 S1 对的 pin 与本次完全相同 |

Preflight：load `14.26 / 14.02 / 13.73`（128 vCPU）；cpu PSI some avg10 `0.60`；io some `0.26` / full `0.17`；**memory PSI `0.00`**；MemAvailable 166 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 `pytest`/测试锁 → **resource GREEN**。

两仓均取**只读 archive 树**：serveros `git archive`；work-engine `git init` + `fetch --depth=1 <exact SHA>` + `archive`（44 files）。未 checkout、未改任何仓。

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope \
  -p MemoryMax=1G -p MemoryHigh=768M -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              S1_TREE=<s1-tree> WE_TREE=<we-tree> QA_SCRATCH=<scratch>
              python3 qa_probe_e.py'
```

- 探针加载两仓的真实模块：WE `workengine.store.ServerOSStore`；serveros `runtime/daemon.py`、`runtime/agent_control_store.py`、`runtime/coordination.py`、`context/resolver.py`。
- 在临时 state dir 建真实 `serveros.db`（WE schema + `items_index`），启动**真实 serveros daemon**，通过 `agent.binding.record` / `agent.binding.resolve` 写读绑定，再调用 `coordination.admit_work`（send admission 的同一判决函数）。
- **command exit = 1**：22 项通过，**1 项 SECURITY_FAIL**（E5）。
- 日志 `run-5.log` sha256 `f90974d552ed06bd43182263a2f4039fd4ae44fb2a38d45ad0e83af4b22cea0e`；探针 `qa_probe_e.py` sha256 `2b05a30c8a8ccb8dcce93f96297c1900bda2e9f94be23bbbead28d9ffded735f`。

## 3. 独立发现（LOCAL 已证）

### P0 · S1 的 send admission 不读 Work Engine #13 的 lease 权威

- WE 侧：`ServerOSStore.acquire_lease("SRV-0001", owner="agent:A")` 铸出 live lease **owner=A, token=1**；第二个 WE writer 被 `LEASE_HELD_BY_OTHER` 拒绝（E1）。
- serveros 侧：`agent.binding.record` 接受调用方显式 `writer={owner:"agent:EVIL", epoch:1, token:999}`；`agent.binding.resolve` 把它返回为 `current_writer`；`admit_work` 返回 **`ADMITTED`**（E4/E5）。
- 同一时刻 `we.active_lease("SRV-0001")` 仍是 **A/1**。admission 的 current writer 是 **EVIL/999**——两个排他权威不一致，而 send admission 选择的是 binding claim。

```text
SECURITY_FAIL E5 admission current writer equals the live WE writer:
  got=('agent:EVIL', '999') expected=('agent:A', '1')
```

- 源码路径：`LocalLoopbackTransport._dispatch` 的 `session.send`/`session.resume` → `_admit` → `coordination.admit_work(current_writer=read["current_writer"])`；`read` 来自 `DaemonBindings.get` → `agent.binding.resolve`；该 RPC 用 `coordination.current_writer(store.bindings(), bound)` 计算 current writer。**整条路径没有 `held_leases`/WE lease 查询**（`held_leases` 只被 `resolver.resolve()` 用于 `protected` 列表）。
- **Next owner：S1 #241（serveros）+ WE #13**。修复方向不是再造一个 gate，而是让 admission 的 current writer 来自 WE lease（或让 binding claim 与 WE lease 在同一事务/同一 authority 上产生）。

### P1 · binding 写入口接受未认证的调用方 writer

- `runtime/daemon.py#_writer_claim`：`params["writer"]` 是 dict 时**原样返回**；`agent.binding.record` 没有 `context_ref`/actor 参数，也没有签名。E4 在无任何可信 context 的情况下记录成功。
- 正常 Fleet create 路径不暴露该参数：`DaemonBindings.put` 只发送 `session_ref/session_role/bound_item_ref/device_ref`，由 daemon 的 `mint_writer` 铸 owner=session ref。差异在直接的 `agent.binding.record`（daemon socket / CLI）上——它能写任意 owner/token/expires_at。
- **Next owner：S1 #241**。若该写点是 operator-only，应在契约里写明；否则需要与 `agent.dispatch.record` 同级的可信 context 解析。

### P1 · 读取器正确但不在写路径上（T6 的真实范围）

- `resolver.held_leases` 正确过滤 `expires_at`（E3：过期行不再 held，但仍在磁盘上，作为证据保留），与 WE `active_lease` 一致；这是 #237 B3/T6 的真实修复。
- 但它只用于 `resolver.resolve()` 的 `protected`；send admission 不读它。因此“WE lease 过期但仍在 DB”不会影响 send 判决——不是漏洞本身，而是**该修复没有覆盖写门**。

## 4. T1/T2/T3/T6/T7 逐项

| ID | 本次独立结果 | 级别 / 说明 |
|---|---|---|
| T1  stale source after handoff | `agent.binding.close` 后 `admit_work` → `THREAD_CLOSED_FOR_NEW_WORK`（E6）。handoff 的 `_revoke_source_write_authority` 只在 target binding 存在且同 Item 时关闭 source（SOURCE） | **LOCAL（serveros 侧）**；跨设备 handoff 未执行 |
| T2  concurrent same-item exclusive writer | WE 同 resource 第二个 live acquire → `LEASE_HELD_BY_OTHER`；serveros 同 Item 两个 claim → 高 token 成为 current，旧 claim → `NOT_CURRENT_WRITER`（E7）。**两层各自排他，但未合并** | **PARTIAL（两层都 LOCAL，跨层未证）** |
| T3  expired/revoked lease or token during long turn | WE `renew_lease` 错 token → `STALE_TOKEN`，过期后续租 → `LEASE_EXPIRED`（E9）；serveros `expires_at` 过期 → `EXPIRED` → `NOT_CURRENT_WRITER`（E8）。claim 的 TTL 是调用方提供，未读 WE TTL | **PARTIAL（各自 LOCAL，跨层未合并）** |
| T6  WE lease expired but still in DB | `held_leases` 排除、`active_lease` None、行仍在（E3）；release 删除行（E10） | **LOCAL PASS（reader）**；不在 send admission |
| T7  cross-user/runtime ref + stale grant | binding 无 principal/签名；`agent.binding.record` 接受任意 owner（E4）。“stale grant” 在该边界没有可读的 grant 记录 | **OPEN / PARTIAL**；不能声称已覆盖 |

**Unavoidable fence limits**：WE 的 `leases` schema 只有 `resource/owner/token/expires_at`，**没有 revoked 列**；撤销=删除行（E10），因此“revoked writer 仍在 DB”在该 schema 下不可表示——记为 `NOT_RUN`，不是 PASS。没有真实 provider 发送，`BLOCKED_PROVIDER_SOURCE` 不变。

## 5. 模块内测试 vs 跨仓集成产品

- WE 的 `tests/test_lease.py` 与 serveros 的 coordination/router 测试是**模块内**证据；它们不会发现本次 E5 的跨层分歧。
- 本次是**跨仓 LOCAL 集成探针**：同一临时 `serveros.db`、真实 WE lease + 真实 serveros daemon binding store，才开始触及“两个权威是否一致”。
- 仍未达到：真实设备、真实 provider、OAuth/credential、跨机 handoff、客户端 E2E。`REAL_DEVICE`/`CLIENT_E2E` 全部 **NOT_RUN**。

## 6. 证据级别

| Claim | Level |
|---|---|
| WE lease 排他 / token / TTL / release | LOCAL（WE 真实 store，精确 SHA） |
| serveros `held_leases` 过滤过期行 | LOCAL（同一 DB） |
| binding 接受调用方 writer、无 actor | LOCAL（真实 daemon handler） |
| send admission 与 WE live lease 不一致（E5） | **LOCAL（跨仓）** |
| 跨层原子性 / 真实 handoff / real provider | **NOT_RUN / BLOCKED** |

**最终判定：`QA_PARTIAL`。** S1 的 send gate 在本机是“serveros binding claim 的 gate”，不是“Work Engine lease 的 gate”；在跨层权威合并、真实设备/客户端证据缺失前，不签 PASS，不进入 RC composition。
