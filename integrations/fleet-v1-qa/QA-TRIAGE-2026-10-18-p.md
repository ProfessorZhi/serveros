# Fleet V1 独立 QA — Snapshot P（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 V3/I1/A/WE 作者，非 release signer。
- **状态**：`QA_PARTIAL` / **`NOT_RELEASE`**。**没有** QA PASS，**没有** signature。
- **对象（exact heads，`git ls-remote` 复核）**：
  - **V3 组合代码预览 #276** `1bf20feb95b8fd55a4dbf9b1faaeaacfb0bb94c9`
    - merge parent（代码 merge head）`a4d853857198d8e1ed318108a618b928fb2db5ab`
    - `composed_from`：`68e470f98add3587fc26ef355f7f0026761832a0`（#271）+ `524d35ce559002bb33ef0d4941c13f8b6b5fd283`（#275）+ `f8246cedb51621e6b6111672567067c2fcef828a`（#274）
  - Work Engine **#15** `4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be`（原始 authority，只读）
  - Work Engine **#16** `8b32aabdfc4e25f99b271e2667bb2e4ae0228b2e`：**只记录 pin，NOT_RUN**。它按旧 #272 构建，**不得**据此声称 production bridge 已验证。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改 V3/I1/WE/S1/S2/T10/G1/Native/main；无 provider/session 登录、OAuth、凭据、argv/env；无真实 account、网络或生产 daemon/service；无 grant issuance 之外的 elevation；无 subagents、无全量套件。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T08:59:24Z`**（报告收口 `09:15Z` 前后）。Preflight（Tokyo 共享机）：

| 指标 | 值 | 判定 |
|---|---|---|
| load | `26.69 / 22.97 / 19.69`（128 vCPU） | 低 |
| MemAvailable | `171295772 kB`（≈163 GiB） | 充足 |
| swap | `33554428 kB` 总 / `3568556 kB` 空闲 → 已用 ≈ **89.4%** | `MemorySwapMax=0` |
| memory PSI | some `0.00` / full `0.00` | 无 memory stall |
| CPU PSI | some `0.01` / full `0.00` | 低 |
| 其他 Agent | 其他用户 claude/quay 进程存在，**无活跃 pytest** | 不冲突 |
| test lock | `flock -n /tmp/serveros-qa-v1.lock` → `LOCK_FREE` | GREEN |

→ **GREEN**，执行 **1 条**受限命令（同一探针的两次 harness 修正重跑保留在 `run-17a/b.log`，产品代码未改）。

## 2. 唯一受限命令

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576; flock -n /tmp/serveros-qa-v1.lock -c "
    QA_TREE=<p-v3: 1bf20fe> WE_TREE=<m-we15-tree: 4bd9e8a> \
    QA_SCRATCH=<p-scratch> python3 qa_probe_p_v3.py"'
```

最终：**83 PASS / 0 FAIL / 0 SECURITY_FAIL，exit 0**（`run-17.log`）。一个进程内同时使用：V3 组合树的真实 daemon/control store、真实 WE15 store、真实离线 `bounded_invocation.invoke_once`、真实 #274 strict consumer 与 legacy consumer。

> 说明：`83 PASS` 表示**探针断言符合预期**，其中包括对“legacy fail-open 确实存在”的**复现**（P4），不是所有安全门都已通过。P4 的该条是 finding，不是 release gate 的 green。

## 3. 已通过的 LOCAL 组合检查

### S1 — lease/writer（V3 daemon + WE15 store，16 checks）

- canonical `work-write:<Item>` live lease A/1：create 报告的 authority 与 minted claim 均为 `agent:A/1`；send **ADMITTED**。
- 无 lease：create 记录 `writer=None`、未 acquire/改 lease 行；send `NOT_CURRENT_WRITER`。
- expired / released / moved lease：均不再构成 authority；旧 claim 变 stale，send 拒。
- **cross-actor spoofed writer** `writer={owner: agent:EVIL, epoch:999, token:999}` → `recorded=False` / `UNTRUSTED_ACTOR`；手工伪造 binding 也输给 live lease。
- 两个 claimant：第一个 A/1，第二个无 claim 且 send 拒；closed source → `THREAD_CLOSED_FOR_NEW_WORK`。

### S2 — dispatch attempt（同一 daemon/store，18 checks）

- 可信 context 记录 attempt，principal 取自 context；**cross-actor forged SETTLED** → `UNTRUSTED_ACTOR`。
- 真实 SETTLED 记录后，terminal **不能 reopen**（`CALL_NOT_SENT` 被拒，phase 仍 SETTLED）。
- 同 key 换 session / runtime / principal → **CONFLICT**。
- EFFECT_UNKNOWN 不可重试（`REFUSE_UNKNOWN`）；重启后 SETTLED=REPLAY、unknown=REFUSE_UNKNOWN；晚到 observation 不能覆盖 SETTLED；无 principal → `REFUSE_PRINCIPAL`。

### Grant / peer / epoch（13 checks）

- 真实 Ed25519 fixture 签发（LOCAL test key，非 provider）→ store mint epoch `1`；正例 `verified=True`。
- 结果就是 WE15 `LeaseGrant` 十字段；`project/items/actions` 为请求的交集；窗口为 epoch float；`LeaseGrant(**grant)` 可构造，`os_principal=uid:4242`。
- **source/os-peer mismatch**（uid 9999）→ `UNTRUSTED_ACTOR`。
- **altered expected_epoch** → `INVALID_CONTEXT`；scope 外 action → `POLICY_DENIED`。
- **unbound holder**（`put_grant` 无 `os_principal`）→ `UNTRUSTED_ACTOR`；caller 自带 `os_principal` → `REFUSED`。
- **missing grant epoch**（legacy 记录）→ `INVALID_CONTEXT`；**revocation** → `POLICY_DENIED`。

## 4. T10 — 严格 consumer 与 legacy consumer 的分叉

真实 WE15 `finalize_run` 写 durable `finalize.json`/evidence；真实 `invoke_once` 收据由明确 test-double authority 包装（补 `actor_ref`）。`actor_ref` 是 fixture delegation context，不是 OS-attested principal。

| 控制 | 严格 #274 `finalization_result` | legacy `verdict_evidence.evaluate_acceptance` |
|---|---|---|
| legacy `runs.state=SUCCEEDED` + `authorization=UNSIGNED` | **DENY**（`UNSIGNED_FINALIZATION`） | DENY |
| pre-#15 记录（**删掉 `authorization`** 并重锚 evidence sha） | **DENY**（`AUTHORIZATION_NOT_T10_VERIFIED`） | **ELIGIBLE（fail-open）** |
| 真实收据 + `T10_VERIFIED` + exit 0 | **ELIGIBLE only**（parent/QA 均 false，production `BLOCKED_TRUST_SOURCE`） | n/a |
| fake child 文本 PASS + 实际 exit 1 | WE15 `finalize_run(t10_required=True)` **拒绝**（`ReceiptError`）；Run 仍 FINALIZING，0 evidence | n/a |
| evidence 字节被改、sha 未改 | **DENY**（`EVIDENCE_TAMPERED`） | n/a |
| cross-run | **DENY**（`RUN_MISMATCH`） | n/a |

**P0 / SECURITY_FAIL（legacy consumer，交回 #274 / I1）**：`verdict_evidence._check_signed_finalization_artifact` 在 `authorization is None` 时直接 `return`，保留 pre-#15 兼容。durable `finalize.json` 只要没有该键（sha 已重锚、其余字段一致），legacy acceptance 就返回 **ELIGIBLE**；而严格 gate 对同一条记录 **DENY**。`bounded_provenance._check_work` 对 dispatch 路径正是调用这个 legacy seam（`evaluate_continuation` 不调用，它走 `ContinuationAuthority`）。V3 树内该 fail-open 路径已实测存在；当前没有生产模块 import 这两个 consumer，因此生产是 **BLOCKED_WIRING**，但不能把“严格 API 存在”当作接线完成。**必须删除该兼容返回，或让 dispatch 路径改用 `finalization_result`，并加一条“unlabeled 必须 DENY”的测试。**

## 5. Source audit / manifest

- `bounded_provenance.evaluate_continuation` 不调用 legacy `evaluate_acceptance`；它检查 `authority.grant_revoked`，但 **`ContinuationAuthority` 没有 epoch 字段**。严格 T10 gate 也没有 `grant_ref/grant_epoch/epoch` 输入。
  - 因此 **altered epoch** 只有 `agent.lease.authority.verify(expected_epoch=...)` 这一层能拒（LOCAL 已证）；任何绕过该 verify 的 parent 都无法拒绝 stale/altered epoch → **NOT_RUN / BLOCKED（parent 缺失）**。
  - revocation 在 verify 与 continuation 两层都拒（LOCAL Source）。
- 无生产模块 import `finalization_result` 或 `verdict_evidence`；release 路径不读 `finalize.json` / `t10_acceptable` ⇒ **BLOCKED_WIRING**，不是当前的生产 fail-open。
- V3 preview manifest `PREVIEW-MANIFEST-2026-10-18.json`：
  - `check_manifest.py` 对**真实** manifest **exit 0**；`--self-test` **exit 0**。
  - 复制件改成 `claims.final_acceptance=true` + `qa.signature="forged"` → **exit 1**（over-claim 被拒）。
  - 复制件改成 `preview.not_release=false` → **exit 1**。
  - `preview.composed_from` 精确等于 #271/#275/#274 三个 SHA；`preview.not_release=true`、`all_release_gates=false`；`qa.signature=null`。**PREVIEW 不能被伪造为 final acceptance。**
  - **P2 观察**：manifest 的 `qa_triage.head` 仍 pin Snapshot N `1222c74654f1209193be4570c4b1c6a2b09ceca3`，不是本 Snapshot P 的 QA head；这是 manifest 数据漂移，不是安全门失败，但 RC 前必须刷新。

## 6. 证据级别

| Claim | Level |
|---|---|
| S1 lease/writer/peer、spoof/expiry/move/close | **LOCAL（V3 真实 daemon + WE15 store）** |
| S2 terminal/unknown/cross-actor | **LOCAL** |
| grant epoch/unbound/wrong-peer/revocation | **LOCAL（真实 store/daemon + Ed25519 fixture）** |
| T10 strict consumer 对 UNSIGNED/unlabeled/tamper/cross-run/fake-exit1 的拒绝 | **LOCAL（真实 WE15 durable + 真实 #274）** |
| legacy consumer 接受 pre-#15 unlabeled | **LOCAL 复现（SECURITY_FAIL seam）** |
| T10_VERIFIED exit0 | **LOCAL / test-double parent only** |
| manifest checker / forgery refusal | **SOURCE（checker 自测 + 复制件）** |
| parent-level epoch freshness | **NOT_RUN / BLOCKED（无字段、无 parent）** |
| WE #16 production bridge | **NOT_RUN（仅 pin）** |
| 生产 trusted parent / REAL_DEVICE / CLIENT_E2E | **BLOCKED / NOT_RUN** |

## 7. 证据文件

| 内容 | 路径 | sha256 |
|---|---|---|
| 最终合并探针日志（83 PASS，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-17.log` | `bbc8b92dcc388553e12d61722c62c8000b8e2b8647fa629aabf0ecf395ec8efa` |
| harness 中止日志（保留） | `.../run-17a.log` | `5b7cc49c47f88845c8ca7443939b0933962ed09d0ce24f7f53988584ef157e44` |
| harness 中止日志（保留） | `.../run-17b.log` | `edca712b6ef53b119366ebc9ebc3c089f2a20744fe5ca6545e454b47db0bfcf3` |
| 合并探针 | `.../qa_probe_p_v3.py` | `d64ccc09a2c574ce1a9061b87f02e00a218f29e05d777591587a2447325d1b96` |
| V3 只读树 | `.../p-v3`（`git archive 1bf20fe`） | — |
| WE15 只读树 | `.../m-we15-tree`（4bd9e8a） | — |
| pre-#15 durable 记录（sha 已重锚） | `.../p-scratch/p-v3-pshiibc5/legacy/artifacts/.../finalize.json` | `8ba1ceba819e04996637c3440038ecbd83c8cbf322dae636ae81c12f81df9176` |

**最终判定：`QA_PARTIAL` / `NOT_RELEASE`，signature `null`。** V3 组合树在 LOCAL 层的 S1/S2/grant/T10 主路径是 fail-closed；严格 consumer 拒绝 legacy UNSIGNED、pre-#15 unlabeled、tamper、cross-run 与 fake PASS/exit1，T10_VERIFIED exit0 仅 ELIGIBLE。**但 legacy consumer 的 pre-#15 兼容分支已被独立复现为 fail-open**，且 parent-level epoch freshness 与生产接线仍是 `BLOCKED`/`NOT_RUN`。不签 PASS，不进入 RC。
