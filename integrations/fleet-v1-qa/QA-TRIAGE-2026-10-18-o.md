# Fleet V1 独立 QA — Snapshot O（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 A / Work Engine / #274 作者，非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature，**不**宣称 V1 complete。
- **对象（exact heads，`git ls-remote` 复核）**：
  - serveros **#275** `524d35ce559002bb33ef0d4941c13f8b6b5fd283`（P0-A 修复；stacked on #272）
  - serveros **#274** `f8246cedb51621e6b6111672567067c2fcef828a`（严格 T10 finalization consumer）
  - serveros 旧 **#272** `fe4738ae5e8759e330915a2325b184ddcdcfe427`（P0-A 红前基线）
  - Work Engine **#15** `4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be`（`finalize_run(t10_required=True)`）
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改 runtime / grants / WE / S1 / T10 / I1 / G1 / Native 分支或 main；无 provider/session 登录、OAuth、凭据、argv/env；无 grant issuance/elevation；无 subagents；无保护分支合并。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T08:54:50Z`**（报告收口 `08:56:20Z`）。Preflight（Tokyo，共享机）：

| 指标 | 值 | 判定 |
|---|---|---|
| load | `15.79 / 17.71 / 17.63`（128 vCPU） | 低 |
| MemAvailable | `168594660 kB`（≈160.8 GiB） | 充足 |
| swap | `33554428 kB` 总 / `3568300 kB` 空闲 → 已用 ≈ **89.4%** | 强制 `MemorySwapMax=0` |
| memory PSI | some `0.00` / full `0.00`（avg300 0.00） | 无 memory stall |
| CPU PSI | some `0.75` / full `0.00` | 低 |
| 其他 Agent | 多用户 claude/quay 进程存在，**无活跃 pytest** | 不冲突 |
| test lock | `flock -n /tmp/serveros-qa-v1.lock` → `LOCK_FREE` | GREEN |

→ **GREEN**，允许受限 LOCAL 测试。全程 **2 条** `systemd-run --user` 命令（P0-A、P0-B）。

## 2. 受限命令

共同约束：`MemoryMax=1G`、`MemorySwapMax=0`、`CPUQuota=25%`、`ulimit -v 1048576`、`flock -n`、`timeout ≤ 660s`。

**P0-A（红前 / 绿后，同一条命令内两段；`run-15.log`）**

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; flock -n /tmp/serveros-qa-v1.lock -c "
    QA_MODE=old QA_TREE=<l-272: fe4738a> WE_TREE=<m-we15-tree> \
      QA_SCRATCH=<o-scratch-old> python3 qa_probe_o_275.py; old=$?;
    QA_MODE=new QA_TREE=<o-275: 524d35c> WE_TREE=<m-we15-tree> \
      QA_SCRATCH=<o-scratch-new> python3 qa_probe_o_275.py; new=$?;
    echo old_exit=$old new_exit=$new;
    test $old -eq 1 -a $new -eq 0 && echo CONTROL_OK"'
```

观测：`old_exit=1`（17 failures，含 **3 SECURITY_FAIL**）、`new_exit=0`（22 checks）、`CONTROL_OK`。

**P0-B（`run-16.log`）**

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; flock -n /tmp/serveros-qa-v1.lock -c "
    QA_TREE=<p-274: f8246ce> WE_TREE=<m-we15-tree: 4bd9e8a> \
    QA_SCRATCH=<o-scratch-274> python3 qa_probe_o_274.py"'
```

观测：**35 PASS / 0 FAIL / 0 SECURITY_FAIL，exit 0**。

> Harness 记账：P0-B 前两次执行分别因 QA 断言常量名写错、以及“篡改”用例实际未改变字节而中止（`run-16a.log`、`run-16b.log`，均 exit 1）。两次都是**探针自身**的 harness 修正，产品代码未改；最终 `run-16.log` 为完整矩阵。

## 3. P0-A — 同一组失败控制，旧 #272 红、新 #275 绿

| 控制 | 旧 #272 `fe4738a` | 新 #275 `524d35c` |
|---|---|---|
| WE `LeaseGrant` 10 字段齐全 | 缺 `project/items/actions` | **齐全** |
| `project/items/actions` 来自已授权 scope | 缺失 | `serveros` / `('SRV-0001',)` / `('lease.acquire',)` |
| 时间窗口 | ISO 字符串 | **epoch float** |
| `LeaseGrant(**grant)` 可构造 | **TypeError（缺 3 个必填参数）** | **成功** |
| **不同 OS uid（4242 vs 9999）** | **`verified=True`（SECURITY_FAIL）** | **`False` + `UNTRUSTED_ACTOR`** |
| **legacy 未绑定 grant** | **被接受（无 reason）** | **`UNTRUSTED_ACTOR`** |
| **`put_grant` 自带 `os_principal`** | **ACCEPTED（SECURITY_FAIL）** | **REFUSED** |
| 签名 payload 自带 `os_principal` | REFUSED | REFUSED |
| naive / unreadable 时间窗 | 接受（None） | `INVALID_CONTEXT` |
| bool/string/负数 peer uid | `POLICY_DENIED`（错误码不符） | `UNTRUSTED_ACTOR` |
| 非 int `expected_epoch` | 接受（None） | `INVALID_CONTEXT` |
| 签发时绑定 kernel peer（SOURCE） | 无 | `os_principal=...os_principal_of(_CALLER_PEER.get())` |
| socket 用 SO_PEERCRED 填 peer（SOURCE） | 有 | 有 |

结论：**Snapshot N 的 P0（schema）与 P1（peer 未授权）在 #275 的 LOCAL 层已修复**；red-before/green-after 成立。签发使用**本地 Ed25519 fixture key**，不是生产 provider；peer 由 `handle(peer=...)` 注入（Unix socket 用 `SO_PEERCRED` 填充的同一 seam），**不是 REAL_DEVICE**。

## 4. P0-B — #274 严格 consumer 对真实 WE15 记录

记录生产方式：真实 `bounded_invocation.invoke_once` 产生离线收据（synthetic runner，exit 0）→ **明确标注的 test-double** `RunnerReceiptAuthority` 将其包装为 WE15 `VerifiedRunReceipt`（补 `actor_ref`）→ **真实 WE15 `finalize_run(t10_required=True, t10_binding=...)`** 写 durable `finalize.json` + `evidence` → **真实 #274 `read_work_engine_finalization` + `evaluate_signed_finalization`** 判定。`actor_ref` 是 fixture delegation context，不是 OS-attested principal。

| # | 控制 | 结果 |
|---|---|---|
| O1 | legacy `SUCCEEDED` + `authorization=UNSIGNED` | **DENY**（`UNSIGNED_FINALIZATION`） |
| O2 | 真实收据 + `T10_VERIFIED`、exit 0、全部字段匹配 | **ELIGIBLE only**；`parent_accepted=False`、`qa_signature=False`、`production_status=BLOCKED_TRUST_SOURCE` |
| O3 | caller 传 JSON 自称 `T10_VERIFIED` | **DENY**（`CALLER_JSON_NOT_AUTHORITY`） |
| O4 | readback 缺失 | **BLOCKED_TRUST_SOURCE** |
| O5 | store 不存在 | **BLOCKED_TRUST_SOURCE** |
| O6 | evidence 字节被改、sha 未改 | **DENY**（`EVIDENCE_TAMPERED`） |
| O7 | 删 `authorization`/`runner_receipt` 并同步 evidence sha | **DENY**（`AUTHORIZATION_NOT_T10_VERIFIED`） |
| O8 | `runner_receipt.process_exit_code=1` 并同步 sha | **DENY**（`NO_VERIFIED_ZERO_EXIT`） |
| O9 | cross-run / actor / commit / action / input_digest / receipt anchor / runner | 逐项 **DENY**，reason 分别为 `RUN_MISMATCH`、`ACTOR_MISMATCH`、`COMMIT_MISMATCH`、`ACTION_MISMATCH`、`INPUT_DIGEST_MISMATCH`、`RECEIPT_ANCHOR_MISMATCH`、`RUNNER_MISMATCH` |
| O10 | stale grant | **无法在该 gate 判定**：`ExpectedFinalization` 无 `grant_ref/grant_epoch/epoch` 字段（见 §5 P1） |

Durable 证据（scratch `o-274-2ramddru`）：

```json
// legacy/.../finalize.json  sha256 4c5ef536…
{"command":"pytest","cases":["one"],"exit_code":0,"revision":"0258809…","dirty":false,
 "verdict":"PASS","authorization":"UNSIGNED"}

// t10/.../finalize.json     sha256 8aab6080…
{"command":"pytest","cases":["one"],"exit_code":0,"revision":"0258809…","dirty":false,
 "verdict":"PASS","authorization":"T10_VERIFIED",
 "runner_receipt":{"action_key":"exec-a12dd3a7fd3203a452eb34d9",
   "input_digest":"81f32818…","receipt_sha256":"d3e21584…",
   "process_exit_code":0,"runner_identity":"claude-code",
   "commit_sha":"0258809…","actor_ref":"ctx-delegated-1"}}
```

结论：**#274 的严格 consumer 会对 legacy UNSIGNED、caller JSON、tamper、缺 marker、非零实际 exit、跨 Run/actor/commit/action/digest/anchor/runner 全部 fail-closed**，并且只把“真实收据 + test-double parent + WE15 durable T10_VERIFIED + exit 0”判为 **ELIGIBLE for QA**，不产生 parent acceptance 或 QA signature。

## 5. 需交回 Owner 的发现

### P1 · #274：stale grant 在该 gate 无法拒绝（覆盖缺口）

`ExpectedFinalization` / `WorkEngineFinalizeReadback` 均无 grant 或 epoch 输入；O10 实测字段集合不含 `grant_ref/grant_epoch/epoch`。因此“stale grant 必须 refuse”在本 consumer **不可能成立**。这不是要求该 gate 去读 grant，而是要求**可信 parent 在生成 `ExpectedFinalization` 前**用 #275 的 `verify_live_grant` 校验 grant/epoch 新鲜度；该生产 parent 本机不存在（见下），故 stale-grant 拒绝仍是 **NOT_RUN / BLOCKED**，不能记作已覆盖。

### P1 · #274：historic unlabeled compatibility 不能成为 V1 fail-open 路径

`verdict_evidence.py` 的 `_check_t10_label` 在 `authorization is None` 时**保持 pre-#15 兼容并直接返回**；同一仓的严格 gate 则对缺 marker 判 `AUTHORIZATION_NOT_T10_VERIFIED`（O7 已实测）。因此风险不在“严格 gate 会放行”，而在**接线**：任何 V1 release 路径若消费旧的 `verdict_evidence` 而不是 `finalization_result`，未标注的 legacy payload 仍会通过。**交回 #274 / I1**：让严格 consumer 成为唯一 V1 路径，或显式拒绝未标注 payload。

### 阻断 · 生产 parent / finalize 调用点仍不存在

源码扫描：`finalization_result.py` 未被任何非测试生产模块 import，且该文件本身无 `finalize_run(` 调用点（S2 两项均实测）。真实可信 parent、真实 `RunnerReceiptAuthority`、真实调用点仍 **`BLOCKED_TRUST_SOURCE` / `BLOCKED_WIRING`**；O2 的 ELIGIBLE 仅在 test double 下成立。

### P2 · #275 的进程内 peer seam

`handle(peer=...)` 仍接受调用方提供的 peer；生产 Unix socket 由 `SO_PEERCRED` 填充（**SOURCE**，本轮未做 REAL_DEVICE）。因此“issuance 绑定实际 kernel peer”目前是 **LOCAL seam + SOURCE 证明**，不是 OS attestation；需 REAL_DEVICE/CLIENT_E2E 才能升级。

## 6. 证据级别

| Claim | Level |
|---|---|
| #275 LeaseGrant schema / granted scope / epoch floats | **LOCAL（真实 store/daemon + 真实 Ed25519 fixture 签发）** |
| #275 wrong-uid / unbound / revoked / naive / malformed peer 拒绝 | **LOCAL** |
| #275 issuance 绑定 kernel peer、socket 用 SO_PEERCRED | **SOURCE（不是 REAL_DEVICE）** |
| #274 对 legacy UNSIGNED / caller JSON / tamper / 缺 marker / 非零 exit / 跨域字段的拒绝 | **LOCAL（真实 WE15 durable 记录 + 真实 #274 consumer）** |
| #274 T10_VERIFIED positive | **LOCAL / test-double parent only** |
| stale grant 拒绝 | **NOT_RUN（gate 无该输入）** |
| 生产 trusted parent / finalize 调用点 / REAL_DEVICE / CLIENT_E2E | **BLOCKED** |
| provider / session / OAuth / 生产凭据 | **NOT_RUN（按边界禁止）** |

## 7. 证据文件

| 内容 | 路径 | sha256 |
|---|---|---|
| P0-A 红→绿日志 | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-15.log` | `70e4a7b276b17b2ee2224817311cc56a591e794cb986d54b89e94de50fa058cb` |
| P0-B 最终日志（35 PASS，exit 0） | `.../run-16.log` | `a5e6a62787476ea6529c4e68c25364d1c17f814c6ebb42bc6fc3cb77de9a0d97` |
| P0-B harness 中止日志（保留） | `.../run-16a.log` | `8e41c895f2d5ae4332dbce9291342f17f8f66b572996077d622066c072a31797` |
| P0-B harness 中止日志（保留） | `.../run-16b.log` | `af32171f74d4f989a6ec07906d35937248d332f4d9b7cc2768d2af959d02f292` |
| P0-A 探针 | `.../qa_probe_o_275.py` | `a0128424346c8ddb8915bcfb07eff05e006b48bd3d76d55cfaa35fd361f760c6` |
| P0-B 探针 | `.../qa_probe_o_274.py` | `95772b85812b9b55ea2979a0e45d377668fa66b49a29a721e7770fd942fdd567` |
| 只读树 | `l-272`（fe4738a）、`o-275`（524d35c）、`p-274`（f8246ce）、`m-we15-tree`（4bd9e8a） | — |

**最终判定：`QA_PARTIAL`，signature `null`。** #275 已修复 Snapshot N 的两项 LOCAL 安全缺陷；#274 的严格 consumer 在真实 WE15 durable 记录上 fail-closed，legacy UNSIGNED / caller JSON / tamper / 缺 marker / 非零 exit / 跨域字段均被拒。仍未闭合：stale-grant 不在该 gate 的输入面、可信生产 parent 与 finalize 调用点 `BLOCKED`、REAL_DEVICE/CLIENT_E2E `NOT_RUN`。**不签 V1，不进入 RC。**
