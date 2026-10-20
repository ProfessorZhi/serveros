# QA2 Round 7 — A′ P1 最小修复 #377 独立验证（2026-10-21）

**最终：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。不签 `QA_SIGNED`。**

- **上轮 P1 已关闭**：commit `grant_epoch=1` 的 token 1，在
  `proto_grant.grant_epoch` 单独改为 2、policy/authority 不变后，旧
  `check_writer_fenced` 返回 `authorized:true`；#377 现在返回
  **`BLOCKED_STALE_EPOCH`**，且 lease/commit/token 均无副作用。
- #376 上同一 witness 仍 **ACCEPTED**（RED）；#377 上 **BLOCKED**（GREEN）。
  另外 `policy_ref` lineage mismatch 在 #376 为 ACCEPTED，#377 为
  **`BLOCKED_OWNER_POLICY`**。
- 作者 **51P rc0**；round6 independent controls **29/29 rc0**；额外 lineage
  controls **5/5 rc0**；#364 三文件 **27P/1S rc0**。
- **P0 production-wiring barriers 仍在，正确披露**：legacy
  `ServerOSStore.acquire_lease` 在 revoke 后写新 lease；legacy
  `ServerOSStore.check_writer` 接受 pre-revoke token。README 标
  `PRODUCTION_WIRING_REQUIRED`，PR 没有误称已解决。
- **残留 P2**：`check_writer_fenced` 的 `commit.grant_ref == grant.grant_ref`
  是选择键的 tautology；它不读取当前 context，因此 context 绑定到另一个
  grant（同 `grant_epoch`）后旧 token 仍被接受。若 owner source 允许
  reissue 而不 bump `grant_epoch`，应升为 P1；正确的 owner 协议必须 bump
  generation。

| 项 | 值 |
|---|---|
| #377 exact head | `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` |
| base #376 exact | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| base RC-v9 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff vs #376 | 3 files, +64/−7（README/atomic_fence/tests） |
| `check_scope.py` vs #376 | `changed=3 allowed=3 forbidden=0`，`SCOPE_OK` rc0 |
| 作者测试 | **51 passed rc0** |
| #364 生产控制 | 三文件 **27P/1S rc0**（作者 26P/1S 是另一子集） |
| round6 controls @#377 | **29/29 rc0** |
| 额外 lineage controls @#377 | **5/5 rc0** |
| 额外 lineage controls @#376 | **3/5 failed**（grant-epoch/policy-ref RED；valid 仅因旧响应无 `grant_epoch` 字段） |
| round6 findings @#377 | 3/4 failed（P0×2 + P2；上轮 P1 已 ok） |
| 额外 findings @#377 | 4/4 failed（context P2、legacy P0×2、peer P2） |

---

## 1. 修复内容 / 静态范围

`d8edfe8..8b42de47` 的实际 base 是 #376 `01e8a9c`；diff 仅：

```text
integrations/fleet-v1-atomic-prototype/README.md
integrations/fleet-v1-atomic-prototype/atomic_fence.py
tests/test_s1_atomic_prototype.py
```

`check_scope.py` 以 #376 为 base：`changed=3 allowed=3 forbidden=0`，
`SCOPE_OK`。没有 `runtime/`、`workengine/`、`mcp/`、`fleet_mcp/`、`intake/`
import（0 hits）；没有 `"verified"` / `COMMIT_FENCED` 生产词；成功 receipt
仍是 `PROTOTYPE_FENCED_COMMIT` + `authorized:true` + `effect_eligible:false`；
没有 daemon wiring/迁移。

`check_writer_fenced` 在同一个 `BEGIN IMMEDIATE` 内新增：

```python
# atomic_fence.py:766-778
if str(commit["grant_ref"]) != str(grant["grant_ref"]): ...
if str(grant["policy_ref"]) != str(commit["policy_ref"]): ...
if int(commit["grant_epoch"]) != int(grant["grant_epoch"]):
    raise PrototypeRefusal(
        "BLOCKED_STALE_EPOCH",
        "the grant epoch moved ...; the token is historical")
```

成功响应新增 `grant_epoch`（`:799`）。新负测
`test_fenced_check_refuses_after_grant_epoch_bump`
（`tests/test_s1_atomic_prototype.py:599-616`）验证 legacy 仍 authorized、fenced
拒绝 `BLOCKED_STALE_EPOCH`。

---

## 2. RED → GREEN（同一独立 witness）

### #376 RED（`01e8a9c`）

```text
acquire(req:1) -> token 1, commit.grant_epoch=1
UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref='grant:proto'
check_writer_fenced(... token=1)
  -> ACCEPTED / PROTOTYPE_FENCED_CHECK authorized:true
  expected BLOCKED_STALE_EPOCH
```

同一 witness 下 `UPDATE proto_grant SET policy_ref='policy:other'` 后，
#376 也返回 **ACCEPTED**（无 policy_ref lineage 检查）。这两个是 RED。
（#376 的 valid control 失败是因为旧响应没有 `grant_epoch` 字段，不是安全
RED。）

### #377 GREEN（`8b42de47`）

```text
grant_epoch bump        -> BLOCKED_STALE_EPOCH
policy_ref mismatch     -> BLOCKED_OWNER_POLICY
policy revoke           -> POLICY_DENIED
grant expiry            -> GRANT_EXPIRED_AT_COMMIT
valid token             -> PROTOTYPE_FENCED_CHECK authorized:true
                           historical:false effect_eligible:false grant_epoch:1
```

所有 refusal 前后 `lease / commit / token_next` **完全一致**（before == after），
即 fenced check 是只读的，没有 lease/commit/effect side effects。**P1 关闭。**

---

## 3. 未削弱的其他 P1/P2（独立复跑）

- round6 controls @#377：**29/29 rc0**，包括
  commit-phase TTL/policy/grant/backward rollback、historical replay + epoch
  pins、blocked revoker `database is locked`、one-winner contention/WAL/
  integrity、crash-before/after、constructor clock/peer、request injection。
- 作者 `tests/test_s1_atomic_prototype.py`：**51P**（50 previous + 1 new）。
- #364 三文件生产控制：**27P/1S**；生产代码未被本 PR 修改。
- legacy `ServerOSStore.acquire_lease`/`check_writer` 的 P0 barriers 仍在，
  README 标 `PRODUCTION_WIRING_REQUIRED`；PR body 明确 “NOT fixed”。本修复
  没有把 legacy 路径误报成 fenced。
- `finalize_run_verified`/effect 仍独立事务；`REAL_FINALIZE_SAME_TXN`
  NOT_PROVEN；`NO_TRUSTED_CLOCK`/`NO_TRUSTED_PEER`/`SO_PEERCRED_NOT_BOUND`
  仍在 README。

---

## 4. 残留 P2 — context/grant lineage 未绑定

新增的 `commit.grant_ref == grant.grant_ref` 检查是**tautology**：grant 行就是
按 `commit["grant_ref"]` 选出来的，二者必然相等。它没有读取当前
`proto_context`。独立 witness：

```text
acquire(req:1) -> commit.grant_ref='grant:proto', token 1
INSERT proto_grant grant:proto-2 (same policy, grant_epoch=1)
UPDATE proto_context SET grant_ref='grant:proto-2'
check_writer_fenced(... token=1)
  -> PROTOTYPE_FENCED_CHECK authorized:true  (ACCEPTED)
  expected BLOCKED_STALE_EPOCH/BLOCKED_OWNER_POLICY
```

因此 context 绑定到另一个 grant（同 `grant_epoch`）后旧 token 仍被接受。
`grant.policy_ref == commit.policy_ref` 检查是有效的；`grant_epoch` 检查是
本 P1 的实质修复。**分级：P2**——它要求 owner source 在 reissue 时不 bump
`grant_epoch`（违反 generation 协议）；若 owner source 允许这种 rebind，
应升为 **P1**。建议后续让 `check_writer_fenced` 读取当前 context 并比较
`context.grant_ref`/actor，或把 owner reissue 强制为 grant_epoch bump。

另：`check_writer_fenced` 仍只接收 caller 的 `owner`/`token` 字符串，不绑定
constructor synthetic peer/context（P2；README 已标 `NO_TRUSTED_PEER`/
`SO_PEERCRED_NOT_BOUND`）。这是 production effect-path 的 peer 绑定问题，不是
本 P1 修复的回归。

---

## 5. P0 生产接线阻塞 vs PROTOTYPE_LOCAL_PASS

**P0（production wiring，未修、正确披露）：**

- legacy `ServerOSStore.acquire_lease` revoke 后写新 lease
  （owner=human:LEGACY, token 1, proto_commit=0）；
- legacy `ServerOSStore.check_writer` 对 pre-revoke token `authorized:true`，
  新 fenced check 拒绝；
- `serverosctl work revoke` 写 JSON ledger，不写 SQLite fence；
- finalize/effect 独立事务；无真实 owner key/kernel `SO_PEERCRED`/migration。

**PROTOTYPE_LOCAL_PASS：** #377 的 P1 修复在 LOCAL_REAL_SQLITE + synthetic
owner/clock/peer 范围内通过；51P、29/29、5/5、SCOPE_OK。**不等于生产
authorization，不等于 V1 signed。**

**NOT_RUN：** 真实 owner key/issuance/revocation、两个真实 OS uid、kernel
`SO_PEERCRED`、production daemon/WorkEngine wiring/migration、真实 Windows、
Provider/OAuth/send；power-loss/fsync NOT_PROVEN；finalize same transaction
NOT_PROVEN。

---

## 6. 证据

```text
logs/a-prime-377-author51.log                         51 passed rc0
logs/a-prime-377-364-three.log                        27P/1S rc0
logs/a-prime-377-probe-controls.log                   29/29 rc0
logs/a-prime-377-probe-findings.log                   3/4 failed rc1 (P0×2, P2; P1 ok)
logs/a-prime-377-extra-377-8b42de4-controls.log       5/5 rc0
logs/a-prime-377-extra-377-8b42de4-findings.log       4/4 failed rc1 (context P2, legacy P0×2, peer P2)
logs/a-prime-377-extra-376-01e8a9c-controls.log       3/5 failed rc1 (grant-epoch/policy-ref RED)
logs/a-prime-377-extra-376-01e8a9c-findings.log       4/4 failed rc1
logs/a-prime-377-check-scope.log                      changed=3 allowed=3 forbidden=0 SCOPE_OK
probes/a_prime_377_extra.py                           independent lineage/no-side-effect witness
```

Reproduce:

```sh
cd <8b42de47 snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_extra.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_extra.py --mode findings
pytest -q -p no:cacheprovider tests/test_s1_atomic_prototype.py
python integrations/fleet-v1-atomic-prototype/check_scope.py \
  01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f
```

运行均 `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 / timeout`；
无 full pytest、无生产 SQLite/Daemon/Service/权限/OAuth/Provider；未合 main、
未强推/删除、未改作者代码。

**结论：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。** P1 grant-epoch 修复
LOCAL 通过；context/grant rebind 仍为 P2（条件性 P1）；P0 生产接线阻塞
保持披露且未解决。
