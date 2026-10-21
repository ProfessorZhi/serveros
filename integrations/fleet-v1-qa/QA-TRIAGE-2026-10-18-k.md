# Fleet V1 独立 QA — Snapshot K（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：
  - serveros #269 `322a8ce89117e55564fce84039f6ea01f4e98e81`（stacked on #263 `c9fb802`）——T10 `RunnerReceiptAuthority` preview bridge。
  - Work Engine #14 `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a`（stacked on #13 `40b0622`）——`finalize_run_verified` / authority seam。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改产品/WE/I1/G1/main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T07:32:32Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live |
|---|---|---|
| serveros #269 | `322a8ce89117e55564fce84039f6ea01f4e98e81` | 一致（Draft） |
| serveros #263 | `c9fb802a9db6ee307a7a239137aa2da03e4bad0f` | 一致 |
| WE #14 | `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a` | 一致（Draft） |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |

Preflight：load `13.87 / 13.59 / 13.89`（128 vCPU）；cpu PSI some `1.30`（full `0.00`）；io `0.02`；**memory PSI `0.00`**；MemAvailable 162 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 pytest/测试锁 → **GREEN**。

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_TREE=<#269> WE_TREE=<WE14> QA_SCRATCH=<scratch>
              python3 qa_probe_k_269.py'
```

真实 `runtime/bounded_invocation.invoke_once`（synthetic offline runner 打印 `ALL TESTS PASSED`，按参数 exit）+ 真实 `LocalRunnerReceiptAuthority.verify` + 真实 WE #14 `ServerOSStore.finalize_run_verified`；临时 WE state DB / git worktree / receipts。**exit 0，28 项全过。**

| 用例 | 结果 |
|---|---|
| **K1** exit 1 + `ALL TESTS PASSED` | bridge 返回 `process_exit_code=1`、`verdict=FAIL`；`finalize_run_verified(verdict=PASS, exit_code=0)` → **EXIT_CODE_DISAGREEMENT**；Run 仍 `FINALIZING`；**evidence 行数 0** |
| **K2** exit 0 | `finalize_run_verified` → **SUCCEEDED**；`finalize.json.runner_receipt` 写入 action_key/input_digest/receipt_sha256/process_exit_code/runner_identity/commit_sha；evidence `sha256` = `finalize.json` 字节哈希。**这是 LOCAL preview success，不是生产 T10 acceptance** |
| **K3** child 文本路径作为 `receipt_ref` | **UNTRUSTED_RECEIPT** |
| **K4** 缺 authority parent / expected / anchor / factory | **BLOCKED_TRUST_SOURCE** |
| **K5** 改 stdin/arg/env digest | **UNTRUSTED_RECEIPT** |
| **K6** stale commit SHA | **STALE_SHA** |
| **K7** WorkRun mismatch / 跨 principal | **RECEIPT_RUN_MISMATCH** / **CROSS_PRINCIPAL** |
| **K8** 篡改 receipt/stream anchor | **UNTRUSTED_RECEIPT** |
| **K9** 同一 Run 重复 finalize | fail-closed 或 idempotent；**没有第二条 evidence 行** |
| **K10** 缺失 Work Engine Run | **RECEIPT_RUN_MISMATCH** |
| **K11** revoked grant | **GRANT_REVOKED** |

## 3. SOURCE — preview 不是生产

- `authority_bridge.STATUS = PREVIEW_ONLY`、`PRODUCTION_STATUS = BLOCKED_ORIGINAL_OWNER`。
- **没有生产模块 import `authority_bridge`**；`finalize_run_verified` 在 serveros 生产代码中**没有调用方**（只有本 preview 与测试）。
- **legacy `finalize_run` 未被静默升级**：其函数体不调用 `finalize_run_verified`；旧的 caller-supplied verdict/exit 语义仍在，不能当作 T10 权威。
- bridge 不含 `SO_PEERCRED` / OS peer 校验；**没有 production OS-attested 跨进程 parent**。测试里的 parent 是合成进程，不是 daemon/kernel 验证。
- 因此分类仍为 **`BLOCKED_ORIGINAL_OWNER` / `BLOCKED_TRUST_SOURCE`**；K2 的 SUCCEEDED 仅是 preview 的 LOCAL eligibility。

## 4. 可选的 I1 V2 preview（仅记录，不改 QA 目标）

期间观察到源组合 Preview V2 **PR #271 @ `68e470f98add3587fc26ef355f7f0026761832a0`**（Draft，base `main`）。**未作为本 Snapshot 的测试目标、未独立 QA**，记为 **PENDING independent QA**；它不是 release。

## 5. 证据级别

| Claim | Level |
|---|---|
| exit1+PASS 不能 conclude SUCCEEDED；无 evidence 行 | **LOCAL（真实 invoke_once + bridge + WE14 store）** |
| exit0 preview SUCCEEDED；runner_receipt/evidence 锚 | **LOCAL（preview only）** |
| caller path / digest / SHA / run / item / grant / principal / tamper / revoked / missing run / duplicate | **LOCAL** |
| 无生产 caller、legacy finalize 未升级、无 OS peer verifier | **SOURCE** |
| REAL_DEVICE / CLIENT_E2E / provider / production T10 | **NOT_RUN / BLOCKED** |

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 探针日志（28 checks，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-11.log` | `f679e3765613df3451dd8deba387298a7c42f76a654fd806feb3a405a2d83e7d` |
| 探针 | `.../qa_probe_k_269.py` | `f485a3248aa8a1874878df64250a5f56d3733a53679841ae1aa40c6f73485ef7` |
| 只读树 | `k-269`、`k-we14-tree` | `git archive` / 精确 SHA |

**最终判定：`QA_PARTIAL`。** bridge 的 fail-closed 矩阵在 LOCAL 层成立；但它是 PREVIEW_ONLY、无生产 finalize 调用方、无 OS-attested parent。`BLOCKED_ORIGINAL_OWNER`、`BLOCKED_TRUST_SOURCE` 未关闭；T10 生产验收、G1 safety、REAL_DEVICE/CLIENT_E2E 均未达成，不签 PASS。
