# Fleet V1 独立 QA — Snapshot Q（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 #277/#274/WE/I1 作者，非 release signer。
- **状态**：`QA_PARTIAL` / **`NOT_RELEASE`**。**没有** QA PASS，**没有** signature。
- **对象（exact heads，`git ls-remote` 复核）**：
  - **新 T10 Owner #277** `0ba1ef07e24118ac61b35f0a323a10f850eed297`（standalone，parent `f8246cedb51621e6b6111672567067c2fcef828a`）
  - 原 #269 `322a8ce89117e55564fce84039f6ea01f4e98e81`（#277 的 runtime 来源）
  - 旧 **#276 / V3** `1bf20feb95b8fd55a4dbf9b1faaeaacfb0bb94c9`（红前基线）
  - Work Engine **#15** `4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be`（只读）
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改 #277/WE/I1/A/S1/N1/G1/Native/main；无 provider/网络/登录/凭据/argv/env；无系统/网络/产品 DB 修改；无 subagents/全量套件。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T09:17:23Z`**。Preflight（Tokyo 共享机）：

| 指标 | 值 | 判定 |
|---|---|---|
| load | `11.97 / 15.72 / 17.50`（128 vCPU） | 低 |
| MemAvailable | `171185852 kB`（≈163 GiB） | 充足 |
| swap | `33554428 kB` 总 / `3569836 kB` 空闲 → 已用 ≈ **89.4%** | `MemorySwapMax=0` |
| memory PSI | some `0.00` / full `0.00` | 无 memory stall |
| CPU PSI | some `0.44` / full `0.00` | 低 |
| 其他 Agent | 无活跃 pytest | 不冲突 |
| test lock | `flock -n /tmp/serveros-qa-v1.lock` → `LOCK_FREE` | GREEN |

→ **GREEN**，执行 **1 条** `systemd-run --user` 命令（内含 old/new 两次离线探针）。

## 2. 唯一受限命令

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; flock -n /tmp/serveros-qa-v1.lock q-run.sh <artifacts>'
```

`q-run.sh` 顺序执行：

```sh
QA_MODE=old QA_TREE=<p-v3: 1bf20fe> QA_SCRATCH=<q-scratch> python3 qa_probe_q_277.py; old=$?
QA_MODE=new QA_TREE=<q-277: 0ba1ef0> QA_SCRATCH=<q-scratch> python3 qa_probe_q_277.py; new=$?
echo old_exit=$old new_exit=$new; test $old -eq 0 -a $new -eq 0 && echo CONTROL_OK
```

最终：**old 13 PASS / new 88 PASS，`old_exit=0 new_exit=0`，`CONTROL_OK`，exit 0**（`run-18.log`）。  
两次运行共 **101 checks**；均使用真实 WE15 `finalize_run` durable `finalize.json`/evidence + 真实离线 `bounded_invocation.invoke_once` 收据 + **明确标注的 test-double parent**。LOCAL ONLY。

> Harness 记账：第一次运行因探针把 `finalization_result` 以两个模块实例加载、导致 V1 的 `isinstance` 看到异构 `readback` 而误红；修正为使用 `V1.FINAL` 同一实例后重跑。`run-18a.log` 保留，产品代码未改。

## 3. RED — 旧 #276 `1bf20fe`

| 控制 | 结果 |
|---|---|
| pre-#15 unlabeled durable 记录（`authorization` 删除、evidence sha 重锚） | legacy `evaluate_acceptance` → **ELIGIBLE** |
| legacy decision 的 V1 标记 | **不存在 `v1_acceptable` 字段** |
| `v1_required=True` | **TypeError（不支持该参数）** |
| V1 acceptance 模块 | **不存在** `v1_acceptance.py` |
| `bounded_provenance.DispatchDecision` | **无 `v1_acceptable` 字段** |
| `bounded_provenance._check_work` 对该记录 | **不产生 WORK_ denial（ELIGIBLE）** |
| strict `finalization_result` 单独看该记录 | DENY（存在，但旧树里没有 V1-shaped 入口） |

→ **OLD_RED_CONFIRMED**：旧树只有 legacy 的“V1-shaped”读法，且它会把无标签记录判为 ELIGIBLE；外部消费者若读 `decision == ELIGIBLE` 即可误接受。

## 4. GREEN — 新 #277 `0ba1ef0`

### 4.1 legacy 被隔离（13 checks）

- 同一 pre-#15 unlabeled 记录，legacy 仍返回 **ELIGIBLE**（历史兼容模式保留）；
- 但 `decision.v1_acceptable` **恒为 False**，`v1_mode="LEGACY_COMPATIBILITY"`；
- `v1_required=True` → **DENY**，reason `V1_REQUIRES_STRICT_PATH`（该 reason 在 `_DENY_REASONS` 中）；
- labelled `UNSIGNED` → **DENY**；
- `bounded_provenance` 的 dispatch 路径仍调用 legacy seam（`_check_work` 不产生 WORK_ denial），但其 `DispatchDecision` 新增字段默认 `v1_acceptable=False`、`v1_mode=LEGACY_COMPATIBILITY`。

### 4.2 新 V1 acceptance（88 checks）

`evaluate_v1_acceptance` 的独立矩阵：

| 输入 | 结果 |
|---|---|
| `v1_required=False` | **BLOCKED_WIRING** / `V1_CONTEXT_REQUIRED` |
| 空 `expected_grant_ref` | **BLOCKED_WIRING** / `GRANT_REF_REQUIRED` |
| 缺 `GrantFreshnessSnapshot`（无 parent） | **BLOCKED_WIRING** / `NO_TRUSTED_PARENT_FOR_GRANT_EPOCH` |
| caller JSON 冒充 grant（即使含 `trusted-parent:` 字符串） | **DENY** / `CALLER_JSON_NOT_AUTHORITY` |
| stale（`fresh=False`） | BLOCKED_WIRING / `STALE_GRANT_UNKNOWN` |
| revoked | **DENY** / `GRANT_REVOKED` |
| epoch 缺失/<1 | BLOCKED_WIRING / `GRANT_EPOCH_UNKNOWN` |
| `verified=False` | BLOCKED_WIRING / `GRANT_FRESHNESS_UNKNOWN` |
| wrong grant_ref / principal | DENY / `GRANT_MISMATCH` / `ACTOR_MISMATCH` |
| `verified_by="trusted-parent:..."`（无可信 parent 绑定） | **BLOCKED_WIRING** / `V1_ACCEPTANCE_NOT_WIRED`，`trusted_parent=UNVERIFIED` |
| unrecognized source | BLOCKED_WIRING / `UNRECOGNIZED_TRUST_SOURCE` |
| **test-double** + 真实 exit0 T10_VERIFIED | **ELIGIBLE_FOR_QA only**；`v1_acceptable=False`、`parent_accepted=False`、`qa_signature=False`、`production_status=BLOCKED_TRUST_SOURCE` |
| legacy UNSIGNED | **DENY** / `UNSIGNED_FINALIZATION` |
| pre-#15 无标签 | **DENY** / `AUTHORIZATION_NOT_T10_VERIFIED` |
| tampered evidence | **DENY** / `EVIDENCE_TAMPERED` |
| durable `runner_receipt.process_exit_code=1` | **DENY** / `NO_VERIFIED_ZERO_EXIT` |
| wrong item / run / commit / actor | **DENY** / `ITEM_MISMATCH` / `RUN_MISMATCH` / `COMMIT_MISMATCH` / `ACTOR_MISMATCH` |
| caller JSON readback | **DENY** / `CALLER_JSON_NOT_AUTHORITY` |
| fake child 文本 PASS + 实际 exit1 | WE15 `finalize_run(t10_required=True)` **拒绝**；Run 停 FINALIZING、0 evidence |

源码断言：

- `v1_acceptance.py` 中**不存在** `v1_acceptable=True`；`parent_accepted=False`、`qa_signature=False` 为硬编码；
- `V1_REQUIRES_STRICT_PATH` 在 legacy `_DENY_REASONS` 中；
- **没有任何生产模块** import `v1_acceptance` / `verdict_evidence` / `finalization_result` / `bounded_provenance`。

## 5. 发现与交回

### 已闭合（LOCAL）

- #277 把 legacy 模式显式隔离：legacy 可以继续返回 ELIGIBLE，但 `v1_acceptable=False`；`v1_required=True` 明确拒绝（`V1_REQUIRES_STRICT_PATH`）。
- 新 V1 入口在缺 parent、caller JSON、stale/revoked/epoch 未知、wrong principal/item/run/sha、UNSIGNED/无标签、tamper、实际 exit1、fake PASS 等全部 **不产生正 V1 acceptance**。
- test-double 的正例仅 `ELIGIBLE_FOR_QA`，`v1_acceptable`、parent、QA 三项均为 false。

### P2 · 残留的 legacy ELIGIBLE 读法（owner #277 / I1）

`bounded_provenance._check_work` 对无标签记录仍会算出 legacy ELIGIBLE；`DispatchDecision` 现在带 `v1_acceptable=False` 作为**给消费者的标记**，但模块无法强制一个只读 `decision` 字段的旧消费者去检查它。当前无任何生产 import，因此不是已发生的生产 fail-open；一旦把 bounded_provenance 接进 V1/release，调用点必须检查 `v1_acceptable` 或只使用 `evaluate_v1_acceptance`。建议 #277 Owner 在明确的接线点加 guard + 负测，而不是依赖文档约定。

### P2 · `GrantFreshnessSnapshot` 的信任形状

`checked_at` 只校验非空字符串，不解析时间；`verified_by` 是字符串前缀判定。任何调用方都可以构造 `GrantFreshnessSnapshot(verified_by="test-double:...")` 得到 `ELIGIBLE_FOR_QA`，但 `v1_acceptable` 仍为 false，`trusted-parent:` 一律 `V1_ACCEPTANCE_NOT_WIRED`。这是“没有 OS-attested parent 时只做 QA 资格”的正确边界；但真实 parent 接线时必须由 parent 内部产生该快照，不能接受调用方对象。

### BLOCKED_ORIGINAL_OWNER · 未 ship

无生产模块 import V1 入口或 legacy consumer；`v1_acceptance.py` 只在 `integrations/fleet-v1-verdict-evidence/**` 内。**不是已发布路径**。真实生产 parent / OS attestation / 调用点仍 **BLOCKED**。

### 仍未验

- **REAL_DEVICE / CLIENT_E2E：NOT_RUN**。
- 真实可信 parent、真实 `RunnerReceiptAuthority`、真实 grant/epoch 来源：**BLOCKED_TRUST_SOURCE / BLOCKED_ORIGINAL_OWNER**。
- `trusted-parent:` 字符串没有密码学/OS 认证，已被正确判为 not wired；不得把 test-double 或 mocked provider 当作生产 principal。
- Work Engine #16 本轮未涉及；不主张其 production bridge 已验证。

## 6. 证据级别

| Claim | Level |
|---|---|
| 旧 #276 legacy 接受 pre-#15 unlabeled、无 V1 字段 | **LOCAL 复现（真实 durable + 真实 legacy consumer）** |
| #277 legacy `v1_acceptable=False` / `v1_required` 拒绝 | **LOCAL** |
| #277 V1 acceptance 全负例与 test-double 正例 | **LOCAL（真实 WE15 + 真实 invoke_once 收据 + test-double parent）** |
| fake child PASS/actual exit1 被 WE15 拒绝 | **LOCAL** |
| 无生产 import / 未 ship | **SOURCE** |
| 真实 parent / REAL_DEVICE / CLIENT_E2E | **BLOCKED / NOT_RUN** |

## 7. 证据文件

| 内容 | 路径 | sha256 |
|---|---|---|
| 最终 old/new 探针日志（13+88 PASS，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-18.log` | `9050e9b906b20eba4727401e1daa9bffbf584f865875c82a68884ab1bb839a3c` |
| harness 中止日志（保留） | `.../run-18a.log` | `6d7aaad3dbbda5fd27d7e250abdb6b089a9e76a17fe8b7e550787929ebfa66e3` |
| 探针 | `.../qa_probe_q_277.py` | `daa26151e30f05af6aa992b9aed24018c9f828e226d133e1da4724a13d446518` |
| 受限命令 wrapper | `.../q-run.sh` | `36e0d425370701ad32bb402393139f071c56e5d18c7d328277effd834df537ac` |
| 只读树 | `.../p-v3`（1bf20fe）、`.../q-277`（0ba1ef0）、`.../m-we15-tree`（4bd9e8a） | — |

**最终判定：`QA_PARTIAL` / `NOT_RELEASE`，signature `null`。** #277 的 legacy 隔离与 V1 严格入口在 LOCAL 层成立，未发现新的正 V1 acceptance 绕过；但 v1_acceptable 仅是消费者标记、生产接线仍不存在，REAL_DEVICE/CLIENT_E2E 与真实可信 parent 仍未验。**不签 PASS，不进入 RC。**
