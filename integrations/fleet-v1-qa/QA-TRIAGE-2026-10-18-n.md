# Fleet V1 独立 QA — Snapshot N（2026-10-18）

- **角色**：独立 QA（campaign `ProfessorZhi/serveros#244` / `#237`）。非 A/Work Engine 作者、非 release signer。
- **状态**：`QA_PARTIAL`。**没有** QA PASS，**没有** signature。
- **对象**：serveros #272 `fe4738ae5e8759e330915a2325b184ddcdcfe427`（stacked on #268 `f4eade1` / #264 `aee38cb`）的 **grant epoch + `agent.lease.authority.verify` 读**；WE #14 `e4c4a2b` 用于 `LeaseGrant` schema 对照；WE #15 `4bd9e8a` 本轮**未测**（按要求不测未 ready 的 WE 新 bridge）。
- **边界**：只改 `integrations/fleet-v1-qa/**`；未改 runtime/grants/WE/S1 产品分支、I1、main；无 provider/网络/登录/凭据；无 subagents。

## 1. Snapshot 与 preflight

UTC **`2026-10-18T08:07:19Z`**；`git fetch --all --prune` exit 0。live：serveros #272 `fe4738a`（Draft）、#268 `f4eade1`、#264 `aee38cb`；WE #14 `e4c4a2b`（Draft）、#15 `4bd9e8a`。

Preflight：load `13.55 / 14.17 / 14.18`（128 vCPU）；cpu PSI some `0.08`；io `0.13`；**memory PSI `0.00`**；MemAvailable 162 GiB；swap 已用约 30.0 GiB / 32 GiB（≈89.4%，强制 `MemorySwapMax=0`）；其他用户的 pytest 不计入本 lane 锁 → **GREEN**。

## 2. 唯一一条受限命令

```sh
systemd-run --user --scope -p MemoryMax=1G -p CPUQuota=25% -p MemorySwapMax=0 \
  -- bash -c 'ulimit -v 1048576;
              QA_TREE=<#272> WE_TREE=<WE14> python3 qa_probe_n_272.py'
```

真实 `AgentControlStore` + 真实 `Daemon` RPC `agent.lease.authority.verify`；peer 通过 `handle(peer=...)` 传入（Unix socket 用 SO_PEERCRED 填充的同一 seam）。**exit 1：33 PASS + 2 SECURITY_FAIL（N13、N17）。**

## 3. 已通过（LOCAL fixture）

- epoch 由 store 铸造：两条 grant → `1,2`；**重启后**新 store 实例下一条 → `3`；调用方自带 `epoch` → `StoreError`。
- 正例：context A + peer uid 4242 + 明确授予 `lease.acquire/renew/release/work.commit` → `verified=True`；principal=`human:A`、os_principal=`uid:4242`、epoch=1；**读不修改 grant**。
- old `expected_epoch` → `INVALID_CONTEXT`；revoked / expired / not-yet-valid → `POLICY_DENIED`。
- 无/未知 context、无 peer、跨 actor context（B 对 A 的 grant）→ `UNTRUSTED_ACTOR`。
- 错 Item / project / action → `POLICY_DENIED`。
- 相同 grant 重放 → 幂等同一 epoch；**改 scope 的重放 → `StoreError`**；legacy 无 epoch → `INVALID_CONTEXT`（不回填）。
- 读只读：源码无 `_mutation`/`_flush`/`put_grant`；serveros 无 `acquire_lease`；grant actions 未被扩宽。

## 4. P0/P1 — 需交回 original A Owner（#272）

### P0 · 跨仓结果 schema 不匹配（#272 的 read 不能直接喂 WE14 `OwnerVerifiedLease`）

serveros positive 结果：

```json
{"verified": true,
 "grant": {"grant_ref": "...", "principal": "human:A",
           "os_principal": "uid:4242", "epoch": 1,
           "valid_from": "2026-10-18T...+00:00",
           "expires_at": "2026-10-18T...+00:00", "revoked": false}}
```

WE14 `LeaseGrant` 需要：

```text
principal, grant_ref, project, items, actions,
valid_from (float), expires_at (float), revoked, epoch, os_principal
```

精确差异：

| 差异 | serveros 结果 | WE14 需要 |
|---|---|---|
| `project` | **缺失** | 必需 `str` |
| `items` | **缺失** | 必需 `tuple[str,...]` |
| `actions` | **缺失** | 必需 `tuple[str,...]` |
| `valid_from` | **ISO 字符串** | **epoch float** |
| `expires_at` | **ISO 字符串** | **epoch float** |
| 包装 | `{"verified": true, "grant": {...}}` | 直接 `LeaseGrant` |

`LeaseGrant(**result["grant"])` 实测 **TypeError（缺 3 个 required 参数）**。即使补齐字段，`OwnerVerifiedLease._verified` 会对 ISO 字符串做 `float(...)` 并失败。**结论：这个 read 本身 fail-closed，但不是 WE14 可消费的 `LeaseGrant`；跨仓 bridge 仍不可用。** 需要一方修改 contract：serveros 返回 `project/items/actions` 与 epoch 浮点时间，或 WE14 适配 serveros 形状（并重新定义谁验证 project/item/action）。

### P1 · OS peer 只被记录，未被授权

`verify_live_grant` 要求 peer 有 `uid`，但只用它填 `os_principal`，**从不把 peer uid 与 grant 绑定的 OS principal 比较**（grant 记录没有该字段）。实测：同一 context/grant 下，peer uid **4242** 与 **9999** 都返回 `verified=True`，仅 `os_principal` 不同。context_ref 仍是 actor 门，但「实际 peer 与 grant delegated_by 不匹配必须拒绝」在当前 schema 下无法成立。需要 #272 Owner 明确：peer 只是 provenance（并由 bridge 比较），或在 context/grant 中绑定 expected OS principal。

### P2 · in-process peer seam（非生产绕过）

`handle(peer=...)` 接受任意 caller peer：`{"uid":"999"}` 被 `int()` 强制成 `uid:999` 并 verified；`{"uid":"not-a-uid"}` 触发未捕获的 `ValueError`（daemon 返回 typed error，不是干净 refusal）。生产 Unix socket 由 `_peer_credentials(connection)` 用 **SO_PEERCRED** 填充（源码已确认），所以这是测试 seam 的健壮性问题，不是 REAL_DEVICE 绕过。**源码断言不等于 OS attestation；本轮 peer 是 LOCAL fixture。**

## 5. 证据级别与限制

| Claim | Level |
|---|---|
| epoch 铸造/重启单调/调用方 epoch 拒绝 | **LOCAL（真实 store/daemon）** |
| positive verify + 各 refusal 面 | **LOCAL fixture** |
| schema mismatch / peer 未授权 | **LOCAL reproduced + SOURCE 字段对照** |
| SO_PEERCRED 由 socket 填充 | **SOURCE（不是 REAL_DEVICE）** |
| REAL_DEVICE / CLIENT_E2E / production lease producer | **NOT_RUN / BLOCKED** |

`acquire_lease` 仍 **`BLOCKED_PRODUCER_BOUNDARY`**；#272 只读，不制造 lease；WE #15 未测；exactly-once 未主张。

## 6. 证据

| 内容 | 路径 | sha256 |
|---|---|---|
| 探针日志（33 PASS + 2 SECURITY_FAIL，exit 1） | `/data/home/vince/artifacts/skills/automated-testing/v1-qa-20261017/run-14.log` | `b7f2a2f8475b7a1c85b03398e70dd44904d3c3230c6a59bc8dff1240536b5b0d` |
| 探针 | `.../qa_probe_n_272.py` | `8aa719ab5d4583dbda20576cff27421c7e6d674f4d41a557d57d6b18102a989d` |
| 只读树 | `l-272`（`git archive fe4738a`）、`k-we14-tree` | — |

**最终判定：`QA_PARTIAL`。** #272 的 epoch 与 verify read 在 LOCAL 层 fail-closed，但 **P0 schema 不匹配**使其不能直接接入 WE14，**P1 peer 未授权**仍未覆盖「mismatched actual peer」；不签 PASS，不进入 RC composition。
