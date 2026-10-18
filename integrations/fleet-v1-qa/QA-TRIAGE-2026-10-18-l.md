# Fleet V1 独立 QA — Snapshot L（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：源组合代码预览 **#271 `68e470f98add3587fc26ef355f7f0026761832a0`**（merge parent `fb411de60a2f32ecb1ad727a6ef7a27e02ff1332`），`composed_from = [#267 905c472, #268 f4eade1, #269 322a8ce]`；Work Engine #14 `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a` 仅外部 pin。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改产品/WE/I1/G1/main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T07:52:03Z`**；`git fetch --all --prune` exit 0。

| 对象 | 精确 head | live |
|---|---|---|
| serveros #271（composed code preview） | `68e470f98add3587fc26ef355f7f0026761832a0` | 一致（Draft） |
| #268 resource-key | `f4eade1e03e10a8a38bcd802f8c39f90c2bd06d3` | 一致 |
| #269 T10 bridge | `322a8ce89117e55564fce84039f6ea01f4e98e81` | 一致 |
| WE #14 | `e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a` | 一致（Draft） |
| WE #13 | `40b062209de4a989a84b04ea57b6cc8657e1be1e` | 一致 |

Preflight：load `13.32 / 13.37 / 13.70`（128 vCPU）；cpu PSI some `0.64`（full `0.00`）；io `0.01`；**memory PSI `0.00`**；MemAvailable 164 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；无 pytest/测试锁 → **GREEN**。

## 2. 唯一一条受限命令（组合树双探针）

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_MODE=new QA_TREE=<#271> WE_TREE=<WE14> python3 qa_probe_j_268.py;
              QA_TREE=<#271> WE_TREE=<WE14> python3 qa_probe_k_269.py'
```

**exit 0，两段均 OK：L-a 37 checks，L-b 28 checks。**

### 2.1 (a) S1 — canonical lease + true local send gate（L-a）

- 真实 WE #14 `OwnerVerifiedLease` 写入 **`work-write:<Item>`**；真实 #271 daemon/binding/resolve + `LocalLoopbackTransport._dispatch`（provider 是明确 mock 的 `FakeService`）。
- 正确既有 holder A/1：create mint A/1，**send 到达 mock provider 恰好一次**。
- 拒绝：裸 Item 行（**无 fallback**）、无 lease、过期、已释放、被转移、伪造 caller writer（`UNTRUSTED_ACTOR`）。
- `work_write_resource` 单派生；`live_lease` 只查 canonical。

### 2.2 (b) S2 — dispatch attempt（L-a）

- 真实 attempt；principal 来自可信 context。
- 跨 actor SETTLED 拒；终态不可重开；`EFFECT_UNKNOWN` blind retry **REFUSE_UNKNOWN**；晚到 observation 拒。
- 同 key 换 session/runtime/principal 在 kernel 层为 **CONFLICT**（本次组合探针覆盖的 S2 子集全部通过）。

### 2.3 (c) T10 — bounded_invocation + #269 verifier + WE14 finalize（L-b）

- 真实 `invoke_once`（runner 打印 `ALL TESTS PASSED`，按参数 exit）+ 真实 `LocalRunnerReceiptAuthority.verify` + 真实 `finalize_run_verified`。
- **exit 1 + PASS 文本**：receipt FAIL/exit1；PASS/exit0 finalize → **EXIT_CODE_DISAGREEMENT**；Run 仍 `FINALIZING`，**0 evidence 行**。
- **exit 0**：`SUCCEEDED` + `finalize.json.runner_receipt`/evidence 锚；**只是 LOCAL preview eligible，不是生产 T10 acceptance**。
- 拒绝：child 文本路径、缺/不可信 parent（`BLOCKED_TRUST_SOURCE`）、改 stdin digest、stale SHA、cross-run、cross-principal、篡改 anchor、revoked grant、缺 Run；重复 finalize 无第二条 evidence。
- SOURCE：bridge `PREVIEW_ONLY`/`BLOCKED_ORIGINAL_OWNER`；无生产 import/caller；legacy `finalize_run` 未升级；无 `SO_PEERCRED`/OS-attested parent。

## 3. Preview guard（SOURCE）

`integrations/fleet-v1-release/PREVIEW-MANIFEST-2026-10-18.json`：

```json
"preview": {"label": "INTEGRATION_PREVIEW_V2", "not_release": true,
            "all_release_gates": false, "composed": true,
            "composed_from": ["905c472…", "f4eade1…", "322a8ce…"],
            "base_revision": "4a31628…", "composed_revision": "fb411de…"}
"claims": all false; "qa.status": "INDEPENDENT_QA_PARTIAL"; signature: null
```

- checker 原样：**exit 0**。
- 将 `claims.final_acceptance=true`：**exit 1**（E195/E173）。
- 将 `preview.not_release=false`：**exit 1**（E191）。
- 规则 E188–E196 要求 preview label 合法、`not_release=true`、`all_release_gates=false`、`composed_from` 为 40-hex；preview 不得带 QA signature、不得 claim final acceptance。**claims 无法绕过 `NOT_RELEASE`。**

## 4. 明确未完成

- **`BLOCKED_TRUST_SOURCE`**：真实 WE store 的 verifier 是测试用 `FakeLeaseAuthorityForQA`，不是 OS-attested parent；serveros 无受信 lease API，WE #14 无 CLI/MCP lease verb。
- **`BLOCKED_WIRING` / `BLOCKED_ORIGINAL_OWNER`**：T10 bridge 是 PREVIEW_ONLY，无生产 finalize 调用方；legacy `finalize_run` 未升级。
- 没有 REAL_DEVICE/CLIENT_E2E、没有真实 provider/session、没有 production 变更。**不是 completed V1。**

## 5. 证据级别

| Claim | Level |
|---|---|
| canonical lease/send gate/S2 负例（组合 #271） | **LOCAL（真实 WE14 store + 真实 daemon/loopback，mock provider）** |
| T10 exit1/exit0/负例（组合 #271） | **LOCAL（真实 invoke_once/verify/finalize）** |
| preview guard 不可由 claims 绕过 | **SOURCE + LOCAL checker mutation（exit 0 → 1）** |
| 无 OS parent/lease API/生产 finalize caller | **SOURCE** |
| REAL_DEVICE/CLIENT_E2E/production | **NOT_RUN / BLOCKED** |

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 组合日志（L-a 37 + L-b 28，exit 0） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-12.log` | `a4e17ec876fafe318ed1d917e2b86177fdd93418291a42175e94c6a2a422590f` |
| S1/S2 探针 | `.../qa_probe_j_268.py` | `691cf3e3d7eed22a7856a786bd4e09db4c0354e6fcfe9b70fefe535cd5c4ca4a` |
| T10 探针 | `.../qa_probe_k_269.py` | `f485a3248aa8a1874878df64250a5f56d3733a53679841ae1aa40c6f73485ef7` |
| 组合树 | `l-271`（`git archive 68e470f`） | — |

**最终判定：`QA_PARTIAL`。** 组合 #271 的 S1/S2 与 T10 preview 在 LOCAL 层通过且 preview guard 不可由 claims 绕过；但真实 OS-attested parent、authenticated lease producer、生产 T10 finalize caller 均不存在，`BLOCKED_TRUST_SOURCE`/`BLOCKED_WIRING`/`BLOCKED_ORIGINAL_OWNER` 未关闭。不签 PASS，不进入 release。
