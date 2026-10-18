# Fleet V1 独立 QA — Snapshot I（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：
  - **A（主）** 合并 PR #264 `aee38cb3b051be84519df19ef9e5703e40ae1b24`（parents 精确为 #262 `9ada9f5` + #252 `7a87e5c`），配 WE #13 `40b0622`。
  - **B** 新 PR #266 `41de0434e4dd1a6a6590ec7b56fd3e1245c6183a`（source-schema only）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改产品、I1、G1、main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T07:11:11Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live |
|---|---|---|
| #264 merge | `aee38cb3b051be84519df19ef9e5703e40ae1b24` | 一致；parents `9ada9f5` + `7a87e5c` |
| #262 | `9ada9f585e2c5d5a0ae3185abb27eb83b9b81eba` | 一致 |
| #252 | `7a87e5cd6dc290a339c0d6d1eb6abf167b4b4cf1` | 一致 |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |
| #266 | `41de0434e4dd1a6a6590ec7b56fd3e1245c6183a` | 一致；**OPEN，非 Draft** |

Preflight：load `15.53 / 14.47 / 15.03`（128 vCPU）；cpu PSI some `0.34`；io `0.35`；**memory PSI `0.00`**；MemAvailable 164 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 pytest/测试锁 → **GREEN**。

## 2. A — #264 合并后的真实 store/daemon 边界

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; QA_TREE=<#264> WE_TREE=<WE13> python3 qa_probe_i_264.py'
```

同一个真实 WE store（#13）+ 同一个真实 serveros daemon（#264）在同一 state dir；S1 send admission 与 S2 dispatch-attempt 写读在同进程交叉验证。**exit 0，41 项全过。**

### 2.1 S1（lease gate）

- live **A/1**：create 返回 `write_authority`，claim 从 lease mint；send **ADMITTED**。
- 无 lease：create 记录无 claim、报告 `lease=None`、**不改 lease 行**；send **NOT_CURRENT_WRITER**。
- 过期、已释放、被转移（A→B token 2）的 lease：旧 claim 全部 **NOT_CURRENT_WRITER**。
- 伪造 caller writer/epoch：**UNTRUSTED_ACTOR**；两个竞争 claimant：第二个无 claim 且 send 拒绝；直接写入的伪造 claim B/2 输给 live lease。
- closed source：**THREAD_CLOSED_FOR_NEW_WORK**。

### 2.2 S2（dispatch attempt）

- 真实 attempt 写入；principal 来自可信 context（`human:A`）。
- 另一 actor 伪造 SETTLED：**UNTRUSTED_ACTOR**；真实 SETTLED 后不可回退（stored phase 仍 SETTLED）。
- 同 key 换 session/runtime/principal：三者均 **CONFLICT**。
- `EFFECT_UNKNOWN` retry：**REFUSE_UNKNOWN**；restart 后 SETTLED→**REPLAY**、UNKNOWN→**REFUSE_UNKNOWN**；SETTLED 后的晚到 observation **REFUSE_NOT_UNKNOWN**；无 principal attempt **REFUSE_PRINCIPAL**。

### 2.3 明确未闭合的边界

- **S1 没有受信 lease producer。** serveros 全树无 `acquire_lease(` / 无 lease 写；WE 无 lease CLI/MCP，且无 actor/grant 表。**create → lease → send 在生产上不能成功**；#264 合并没有改变这一点。分类 **BLOCKED_PRODUCER_BOUNDARY**。
- **S2 没有 provider-owned idempotency readback。** `SETTLED` 的 `result` 由原 actor 提交，daemon 无法验证它来自 provider；**不是 exactly-once**，`BLOCKED_PROVIDER_SOURCE` 仍成立。
- 两层是**两套权威/两个 namespace**：send gate 读 WE `leases`；dispatch attempt 读可信 context。它们在同一 daemon/store 内共存且各自的负例成立，但**不是一次跨层事务**；不要把它读成“send 与 dispatch attempt 已合并为同一判决”。
- 未经真实 session/provider/device；所有 Item/context/claim 均为合成。

## 3. B — #266 源码 schema 检查（SOURCE）

- `approvals_list` 的 governance 仍是 **`GOV_READ`**（`cloudcli_native.py:242`）。
- `SESSION_ADDRESSED_READS = frozenset({"approvals_list"})`；`is_session_addressed()` 被 `mirrored_schema` 的**属性**与**required**两半共同读取。
- `mirrored_schema` 为它声明并 required `session_ref`；**不声明 `project`**；顶层 `additionalProperties: false`；native `arguments.session` 仍为可选，由 runtime 注入。
- runtime `fleet.py` 对 `approvals_list` 先 `_native_session_key(device_ref, session_ref, native)`：无 `session_ref` → **BAD_REQUEST**；并把解析出的 native session 写进 `arguments.session`。这与 published schema 的 required 一致。
- **GOV_READ 未被扩权**：只增加了一个 schema 轴/一个 read 例外；governance 值、scope 词表与 verb 集未变；`SESSION_ADDRESSED_READS` 只有 `approvals_list`。
- **发布卫生**：#266 是 **OPEN 非 Draft**；作者记录的 `tests/test_fleet_mcp_node.py` 既有失败 `10 != 11` 未由本 lane 复跑；**不主张 full suite green**。

## 4. 证据级别

| Claim | Level |
|---|---|
| #264 S1 lease gate（A/1 admitted、无/过期/释放/转移拒绝、伪 writer/双 claimant/伪造 claim/closed source 拒绝） | **LOCAL（真实 WE store + 合并 daemon，合成 item/claim）** |
| #264 S2 dispatch attempt（跨 actor settle、终态回退、三轴 conflict、unknown retry、restart、late observation、no principal） | **LOCAL（真实 daemon/store）** |
| S1 无 producer、WE 无 CLI/MCP/actor-grant | **SOURCE** |
| S2 provider result 无 readback / not exactly-once | **SOURCE / BLOCKED_PROVIDER_SOURCE** |
| #266 schema 与 runtime session admission 一致、无 GOV_READ 扩权 | **SOURCE** |
| REAL_DEVICE / CLIENT_E2E / provider / G1 safety / T10 finalize | **NOT_RUN / BLOCKED** |

## 5. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| #264 合并边界日志（41 checks，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-9.log` | `0976bfeee9b8bdca6be1cab9ba5a36a015feeca17e88bf13f137292ae7511b3a` |
| #264 探针 | `.../qa_probe_i_264.py` | `55009830972533d91375cae38e4d44140ffe32571e40e7f04bfdacab86d578cb` |
| 只读树 | `i-264`、`i-266`、`g-we13-tree` | `git archive` / 精确 SHA |

**最终判定：`QA_PARTIAL`。** #264 合并后的两个 LOCAL 边界均成立且无回归；但 S1 仍 `BLOCKED_PRODUCER_BOUNDARY`、S2 仍 `BLOCKED_PROVIDER_SOURCE`（非 exactly-once），T10 finalize、G1 safety、REAL_DEVICE/CLIENT_E2E 均未达成。不签 PASS，不进入 RC composition。
