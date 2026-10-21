# Fleet V1 独立 QA — Snapshot J（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：
  - **A（主）** serveros #268 `f4eade1e03e10a8a38bcd802f8c39f90c2bd06d3`（stacked on #264 `aee38cb`），配 Work Engine #14 `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a`（stacked on #13 `40b0622`）。
  - **B** WE #14 fail-closed seam / #267 preview 的 source-level 检查。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改产品/WE/I1/main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T07:26:25Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live |
|---|---|---|
| serveros #268 | `f4eade1e03e10a8a38bcd802f8c39f90c2bd06d3` | 一致（Draft） |
| serveros #264 | `aee38cb3b051be84519df19ef9e5703e40ae1b24` | 一致 |
| WE #14 | `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a` | 一致（Draft） |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |
| #267 preview | `905c4728ba009c1d892636719a23cfd04caa6325` | 一致 |

Preflight：load `14.41 / 13.58 / 14.02`（128 vCPU）；cpu PSI some `0.86`；io `0.00`；**memory PSI `0.00`**；MemAvailable 163 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；另一个用户的 litellm pytest 在跑，不构成本 lane 锁 → **GREEN**。

## 2. A — `work-write:<Item>` 跨仓命名空间（红→绿）

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_MODE=old QA_TREE=<#264> ... python3 qa_probe_j_268.py;
              QA_MODE=new QA_TREE=<#268> ... python3 qa_probe_j_268.py'
```

真实 WE #14 `ServerOSStore` + `OwnerVerifiedLease` 写入 canonical 行；真实 serveros daemon/binding/resolve + **真实 `LocalLoopbackTransport._dispatch`**，其 provider 是明确 mock 的 `FakeService`（不发送任何东西）。WE 的 verifier 是测试中明确命名的 `FakeLeaseAuthorityForQA`，**不是生产信任源**。

| 运行 | exit | 结果 |
|---|---|---|
| #264 old（red） | **1** | 6 个 SECURITY_FAIL：canonical `work-write:SRV-CANON` **看不到**；裸 `SRV-NAKED` 行**被读取**；canonical create 无 claim；canonical send 拒绝；naked create 铸 claim；naked send 到达 mock provider |
| #268 new（green） | **0** | **37 项全过**；`CONTROL_OK` |

### 2.1 已关闭（LOCAL）

- WE #14 写入 `work-write:SRV-CANON`（owner `human:A`, token `1`）。
- #268 `work_write_resource` / `item_from_work_write_resource` 单派生；`live_lease` 只查 canonical，**裸 Item 行不被读**。
- canonical create 从 lease mint claim A/1；**loopback send 到达 mock provider 恰好一次**。
- 裸行 create 无 claim、send 拒绝；无 lease create 无 claim、send 拒绝。
- 过期、已释放、被转移（A/1→B/2）的 canonical lease 全部不 live/旧 claim stale。
- 显式 caller writer → `UNTRUSTED_ACTOR`。
- S2 同 daemon 上：跨 actor SETTLED 拒、终态不可重开、`EFFECT_UNKNOWN` 不可 retry、晚到 observation 拒。

### 2.2 仍开放（明确）

- **`BLOCKED_TRUST_SOURCE`**：`FakeLeaseAuthorityForQA` 只证明 seam 能携带 grant；它不是 OS attestation，也不是生产 authority。serveros 没有 `acquire_lease`、没有可执行的 `lease.*`/`work.commit` action、没有 import `workengine.authority`；WE #14 没有 CLI/MCP lease producer。生产 trusted source 仍 **ABSENT**。
- **`BLOCKED_PRODUCER_BOUNDARY`**：因此 `create → lease → send` 在生产上仍不能成立；#268 只修正了**读取命名空间**，没有制造 producer。
- P2 观察：`live_lease` 内联 `f"{WORK_WRITE_PREFIX}{item}"`，未调用同模块的 `work_write_resource(item)`；两者当前一致（同一个常量），但这是同一模块内的两处拼写。建议后续统一调用 helper。

## 3. B — WE #14 fail-closed seam 与 #267 预览（SOURCE）

- **Lease seam**：`OwnerVerifiedLease.acquire/renew/release/check_writer` **不接受 `owner` 参数**；无 authority → `BLOCKED_TRUST_SOURCE`；空 context → `UNTRUSTED_ACTOR`；错误 project/Item/action → `PROJECT_MISMATCH`/`ITEM_MISMATCH`/`ACTION_NOT_GRANTED`；revoked/expired/not-yet-valid → `GRANT_REVOKED`/`GRANT_EXPIRED`/`GRANT_NOT_YET_VALID`；缺 epoch → `EPOCH_MISSING`；stale epoch → `EPOCH_STALE`；OS principal 不符 → `OS_PRINCIPAL_MISMATCH`。没有 CLI/MCP verb（测试断言其缺席）。
- **Receipt seam**：`finalize_run_verified` 无 authority → `BLOCKED_TRUST_SOURCE`；无 `receipt_ref` → `RUNNER_RECEIPT_REQUIRED`；非 `VerifiedRunReceipt`/authority 不答 → `UNTRUSTED_RECEIPT`；校验 Item/Run/verdict/exit；`PASS` + 非零 process exit → `RECEIPT_INCONSISTENT`；`SUCCEEDED` 要求 verdict PASS、caller exit 0 **且** verified receipt `process_exit_code == 0`。不解析 child stdout/stderr，不接受 caller hash/bare exit code。
- **#267 preview**：`PREVIEW-README` 标 `INTEGRATION_PREVIEW` / `BLOCKED_SEMANTIC` / `NOT_RELEASE`；manifest `state=RC_CANDIDATE`、claims 全 false、`signature=null`。**不是** `V1_VERIFIED_COMPLETE`，不得把 source-local QA 提升为 release。

## 4. 证据级别

| Claim | Level |
|---|---|
| canonical 命名空间、无 naked fallback、create/send/loopback | **LOCAL（真实 WE14 store + 真实 serveros daemon/loopback，mock provider）** |
| 过期/释放/转移/伪 writer/S2 负例 | **LOCAL** |
| 无生产 trusted source / no CLI-MCP / grant 无 lease actions | **SOURCE** |
| WE14 receipt seam 拒绝分支 / #267 非 release | **SOURCE** |
| REAL_DEVICE / CLIENT_E2E / provider / G1 safety / T10 finalize | **NOT_RUN / BLOCKED** |

## 5. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 红→绿日志（old 6 SECURITY_FAIL / new 37 PASS，CONTROL_OK） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-10.log` | `dbb6b5c7bdaba3e10a1e169b7d2714778b747130343fb2334f4adcb13b3dea6b` |
| 探针 | `.../qa_probe_j_268.py` | `691cf3e3d7eed22a7856a786bd4e09db4c0354e6fcfe9b70fefe535cd5c4ca4a` |
| 只读树 | `j-268`、`j-264`、`j-we14-tree`、`j-267` | `git archive` / 精确 SHA |

**最终判定：`QA_PARTIAL`。** 跨仓 `work-write:<Item>` 命名空间回归在 LOCAL 层关闭且有红→绿对照；但生产 trusted lease source 仍 `BLOCKED_TRUST_SOURCE`、producer 仍 `BLOCKED_PRODUCER_BOUNDARY`。T10 finalize、G1 safety、REAL_DEVICE/CLIENT_E2E 均未达成；不签 PASS，不进入 RC composition。
