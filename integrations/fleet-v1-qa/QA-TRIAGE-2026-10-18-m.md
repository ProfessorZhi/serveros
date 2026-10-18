# Fleet V1 独立 QA — Snapshot M（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 Work Engine 作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：Work Engine #15 `4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be`（stacked on #14 `e4c4a2b` / #13 `40b0622`）的**生产面向 finalize 入口**：`workengine run finalize` → `workengine/cli.py::_cmd_run` → `ServerOSStore.finalize_run`。下游消费者 source 取自 serveros #271 组合预览 `68e470f`。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改 Work Engine/serveros 产品分支、I1、main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T07:58:54Z`**；live heads 已核对：WE #15 `4bd9e8a`（Draft）、#14 `e4c4a2b`、#13 `40b0622`、serveros #271 `68e470f`（Draft）。

Preflight：load `16.19 / 14.80 / 14.19`（128 vCPU）；cpu PSI some `2.56`（full `0.00`）；io `0.15`；**memory PSI `0.00`**；MemAvailable 161 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 pytest/测试锁 → **GREEN**。

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_TREE=<WE15> SERVEROS_TREE=<#271> python3 qa_probe_m_we15.py'
```

真实 `workengine.cli.main`（CLI parser + `_cmd_run`）+ 真实 `ServerOSStore.finalize_run`；hermetic SQLite；receipt authority 是明确标注的 **`StubReceiptAuthority`（TEST DOUBLE，非生产 parent）**。**exit 0，50 项全过。**

## 3. 结果

### 3.1 CLI 入口（实际 route）

| 用例 | 结果 |
|---|---|
| **legacy `run finalize`**（无 `--t10-required`） | exit 0；返回 **`state=SUCCEEDED`、`authorization=UNSIGNED`、`t10_acceptable=false`**；durable `finalize.json.authorization=UNSIGNED` |
| **`run finalize --t10-required`**（无 trusted parent） | **exit 2、`BLOCKED_TRUST_SOURCE`**；Run 仍 `FINALIZING`；**0 evidence 行** |

### 3.2 直接 `finalize_run(t10_required=True)` 负例

- 无 authority → `BLOCKED_TRUST_SOURCE`；无 binding → `T10_BINDING_REQUIRED`；无 receipt ref → `RUNNER_RECEIPT_REQUIRED`。
- caller 文件路径 / JSON hash 作为 `receipt_ref` → `UNTRUSTED_RECEIPT`。
- 子进程打印 `ALL TESTS PASSED` 但 receipt `process_exit_code=1`、caller 仍 PASS/0 → **EXIT_CODE_DISAGREEMENT**；**两次重试仍拒绝**，Run 仍 FINALIZING，0 evidence。
- 跨 Item / Run / commit / actor → `T10_ITEM_MISMATCH` / `T10_RUN_MISMATCH` / `T10_COMMIT_MISMATCH` / `T10_ACTOR_MISMATCH`；无 evidence。
- revoked grant（authority 抛 `AUTHORITY_REVOKED`）→ 传播拒绝；无 evidence。

### 3.3 Test-double 正例与幂等

- 已验证 exit0 + 精确 binding → `state=SUCCEEDED`、**`authorization=T10_VERIFIED`、`t10_acceptable=true`**；durable `finalize.json` 同标签；**仅 1 条 evidence 行**。
- 第二次 T10 finalize → 幂等返回 `(SUCCEEDED, T10_VERIFIED)`，**无额外 evidence**。
- 已由 legacy 结为 `UNSIGNED` 的终态 Run，再请求 `t10_required=True` → **`T10_UNSIGNED_RUN`**，不能洗白，标签仍 UNSIGNED，evidence 仍 1 条。

## 4. P0 downstream blocker（不是 WE #15 的缺陷）

`finalize_run` 的 legacy 路径仍会返回 `state=SUCCEEDED`（按 caller verdict/exit），只是 durable evidence 现在明确标 `authorization=UNSIGNED`、`t10_acceptable=false`。

独立 source 检查 serveros #271 的 V1 消费者（`integrations/fleet-v1-verdict-evidence/verdict_evidence.py`、`bounded_provenance.py`）：它们读取 **`runs.state` / evidence `verdict`**，但**不读取 `t10_acceptable` 或 `authorization`**。因此，任何只按 `SUCCEEDED`/`PASS` 判断的 V1 release consumer 仍会把 **UNSIGNED** 结论当成功。

**这是 downstream blocker（owner：serveros V1 consumer / I1），不是 “#15 已修复”。** 必须由消费者显式要求 `authorization=T10_VERIFIED`（或拒绝 UNSIGNED）才能关闭。

## 5. 来源/证据级别与限制

- **LOCAL / HERMETIC**：真实 CLI + 真实 store；receipt authority 是 test double，不是 OS-attested serverosd parent；#269 bridge 是 `PREVIEW_ONLY`。
- 没有 authenticated worker grant、原始 trusted verifier、跨进程 receipt 取回、production session API 或用户批准的外部 provider 调用。
- `T10_VERIFIED` 只是 store 侧标签，**不是独立 QA 结论、不是 release**；docs 明确 “independent QA and the V1 release gate still decide acceptance”。
- REAL_DEVICE / CLIENT_E2E **NOT_RUN**；exactly-once 未主张；`BLOCKED_TRUST_SOURCE` 仍开。

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 探针日志（50 checks，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-13.log` | `8cea04cca2a044064686f18f92e0aab698d2dc20537edf03ddfd2e272664c60a` |
| 探针 | `.../qa_probe_m_we15.py` | `2fa64f8f402c894a13d04b495d8db6f156e84c9bc2bc481d825dde61ae8a9114` |
| 只读树 | `m-we15-tree`（`git archive 4bd9e8a`）、`l-271` | — |

**最终判定：`QA_PARTIAL`。** WE #15 的 T10-required 入口在 LOCAL 层 fail-closed、legacy 明确标 UNSIGNED、T10_VERIFIED 仅 test-double；生产 trusted parent 仍不存在。下游消费者忽略 UNSIGNED 标签是 P0 blocker，不能称为已修复。不签 PASS。
