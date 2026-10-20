# QA2 Round 6 — A′ prototype hardening #376 独立验收（2026-10-21）

**最终：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。不签 `QA_SIGNED`。**

- 作者 50P 在请求的 `02688cec` 与当前远端 head `01e8a9c` 均通过；独立 controls **29/29 rc0**。
- 上一轮 P1/P2 在 prototype scope 内已关闭：commit-phase TTL/policy/grant/backward re-check（rollback）、historical replay + epoch pins、blocked-revoker `database is locked`、constructor-bound clock/peer + per-call TypeError、check_writer_fenced 的 revoke/expired-grant 拒绝。
- **新 P1（必须修）**：`check_writer_fenced` 比较了 commit 的 `policy_epoch`/`authority_epoch`，但**没有比较 `commit.grant_epoch` 与当前 `grant.grant_epoch`**；grant epoch 1→2 后旧 token 仍 `authorized:true`。已给精确静态行号与动态反例。
- **P0 production-wiring barriers（未修，README 正确披露 `PRODUCTION_WIRING_REQUIRED`）**：legacy `ServerOSStore.acquire_lease` 仍可在 revoke 后写新 lease；legacy `ServerOSStore.check_writer` 仍接受 pre-revoke token；JSON `work revoke`/finalize/effect 不在同一 fence。PR 没有误称已解决。
- **P2**：`check_writer_fenced` 不绑定 constructor synthetic peer/context（仅 owner/token 字符串）；`NO_TRUSTED_CLOCK`/`NO_TRUSTED_PEER`/`SO_PEERCRED_NOT_BOUND`；power-loss/fsync NOT_PROVEN；HEAD DRIFT。

| 项 | 值 |
|---|---|
| 用户请求的 exact head | `02688cecf7df37dfde6e81c5a632595783148a8d` |
| **当前远端 #376 head（实际）** | `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f` |
| base #375 head | `d8edfe8d7989674775bc94f373008368e3284e3e` |
| base RC-v9 / #372 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff（d8edfe8..02688cec） | 4 files, +639/−131（prototype/README/check_scope/test） |
| diff（02688cec..01e8a9c） | 仅 README +12（residual-P2 notes）；prototype/test 字节相同 |
| 作者测试 | `02688cec` **50P rc0**；`01e8a9c` **50P rc0** |
| 独立 controls | **29/29 rc0** |
| 独立 findings | **4/4 rc1**（P0×2、P1×1、P2×1） |
| `check_scope.py` @02688cec | `changed=4 allowed=4 forbidden=0`，`SCOPE_OK` rc0 |
| #364 生产控制 | 三文件 **27P/1S**（作者 receipt 的 26P/1S 是另一控制子集；commit_fence 单文件 2P；生产文件未被本 PR 修改） |

> **HEAD DRIFT**：请求的 `02688cec` 是 hardening commit；远端 #376 当前
> head 是 `01e8a9c`，差异仅 README +12（PR body 称 residual-P2 notes）。
> 两者 prototype/test 字节相同。我按 `02688cec` 做 exact 审计，并对
> `01e8a9c` 重跑 50P；不把 `01e8a9c` 冒充为 `02688cec`。

---

## 1. Scope / 生产未接线

`d8edfe8..02688cec` 只有：

```text
integrations/fleet-v1-atomic-prototype/atomic_fence.py
integrations/fleet-v1-atomic-prototype/README.md
integrations/fleet-v1-atomic-prototype/check_scope.py
tests/test_s1_atomic_prototype.py
```

- `check_scope.py`：`changed=4 allowed=4 forbidden=0`，`SCOPE_OK`。
- 无 `runtime/`、`workengine/`、`mcp/`、`fleet_mcp/`、`intake/` import；
  独立 grep 0 hits。
- 成功 receipt `PROTOTYPE_FENCED_COMMIT`：`authorized:true`,
  `historical:false`, `effect_eligible:false`, **无 `verified`**；replay
  `PROTOTYPE_HISTORICAL_REPLAY`: `authorized:false`, `historical:true`,
  `effect_eligible:false`。
- README flags 全部存在：`NO_TRUSTED_CLOCK`, `NO_TRUSTED_PEER`,
  `SO_PEERCRED_NOT_BOUND`, `PRODUCTION_WIRING_REQUIRED`,
  `REAL_FINALIZE_SAME_TXN`, `POWER_LOSS`, `NO_OWNER_TRUST`,
  `NO_DAEMON_WIRING`, `NO_MIGRATION`, `NOT_V1_SIGNED`。
- 生产 #364 仍 `BLOCKED_OWNER_COMMIT_SEAM`；本 PR 不改生产代码/DB/服务。

---

## 2. 作者测试 / 生产控制

| Gate | rc | 结果 |
|---|---|---|
| `tests/test_s1_atomic_prototype.py` @02688cec | 0 | **50 passed** |
| 同上 @01e8a9c | 0 | **50 passed** |
| `tests/test_owner_commit_fence.py` @02688cec | 0 | 2 passed |
| `test_owner_policy_binding.py` + `test_owner_commit_fence.py` + `test_owner_policy_reduced_gate.py` | 0 | **27 passed, 1 skipped**（two-uid skip） |
| `check_scope.py` @02688cec | 0 | **SCOPE_OK** |

作者 receipt 的 “#364 control 26P/1S” 未在本文环境逐字复现为同一 suite；我用
三文件 27P/1S + commit_fence 2P 记录实际计数。**生产文件未被 #376 修改**，
所以这不影响 prototype 判定。

---

## 3. 独立 controls（29/29 rc0）

### A. Commit-phase re-check（hold 中合成 clock 跳变）

| Case | 实际 | lease/commit/token |
|---|---|---|
| TTL `NOW→NOW+31`（ttl 30） | `LEASE_EXPIRED_AT_COMMIT` | null / 0 / null |
| policy expires `NOW+10`，jump `NOW+11` | `POLICY_EXPIRED_AT_COMMIT` | null / 0 / null |
| grant expires `NOW+10`，jump `NOW+11` | `GRANT_EXPIRED_AT_COMMIT` | null / 0 / null |
| backward before window `NOW-200` | `POLICY_DENIED` | null / 0 / null |
| within window `NOW+5` | `PROTOTYPE_FENCED_COMMIT` token 1 | lease 1 / 1 / 2 |

re-check 在同一个 `BEGIN IMMEDIATE` 内、hold 之后重读 constructor clock，
三类窗口和 backward 都 rollback 了 lease row / `id_sequences` token /
`proto_commit`。**A 关闭。**

### B. Historical replay / epoch pins

- same request id + same facts → `PROTOTYPE_HISTORICAL_REPLAY`,
  `authorized:false`, `historical:true`, `effect_eligible:false`, same token 1,
  commit 1, token_next 2。
- different `expected_authority_epoch` / `expected_grant_epoch` →
  `IDEMPOTENCY_CONFLICT`，no new commit/token。
- replay after revoke → historical/authorized:false/effect:false；新 request 2
  → `POLICY_DENIED`；commit 仍 1。
- 成功 receipt 无 `verified`，`effect_eligible:false`。**B 关闭。**

### C. 进程 / 锁 / crash

- **blocked revoker**：acquirer 持有 `BEGIN IMMEDIATE` 并 hold；独立
  `sqlite3.connect(timeout=0)` 的 revoker 尝试 `BEGIN IMMEDIATE` 得到
  **`database is locked`**（精确字符串已记录）；release 后 acquirer 提交
  token 1/commit policy_epoch 1；revoker 提交 epoch 2；新 request
  `POLICY_DENIED`；无 deadlock。
- **double acquire contention**：两进程 barrier 同时 acquire → 恰好 1
  winner / 1 `LEASE_HELD_BY_OTHER`；lease token 1；commit 1；token_next 2；
  `journal_mode=wal`；`integrity_check=ok`。
- **crash before COMMIT**：`os._exit(17)` → no lease / no commit / no token；
  reopen integrity ok。
- **commit then `os._exit(23)`**：reopen lease token 1 / commit 1 /
  token_next 2；replay historical/authorized:false；WAL + integrity ok。
- 明确：`os._exit` 是 process kill，**power-loss/fsync NOT_PROVEN**（README
  已标）。**C 关闭（process-kill 范围）。**

### D. check_writer_fenced / legacy

- valid token → `PROTOTYPE_FENCED_CHECK`, `authorized:true`,
  `effect_eligible:false`。
- after policy revoke → fenced `POLICY_DENIED`；legacy
  `ServerOSStore.check_writer` 仍 `authorized:true`（P0 production barrier）。
- after grant expiry → fenced `GRANT_EXPIRED_AT_COMMIT`；legacy 仍
  `authorized:true`（P0 production barrier）。
- **after grant epoch 1→2（同一 policy/authority）→ fenced 仍
  `authorized:true`（P1，见 §4）。**

### E. Constructor clock/peer

- `acquire(request)` 不接受 per-call `now`/`peer_uid`：两者均 **`TypeError`**，
  no lease/commit。
- `synthetic_peer_uid=None` / `True` → `UNTRUSTED_ACTOR`，no lease/commit。
- request 自述 `uid`/`actor_ref`/`verified`/`authority`/`COMMIT_FENCED`/`now`
  → 全部 `INVALID_REQUEST`，no lease/commit。
- constructor clock/peer 是 **SYNTHETIC** fixture；README flags
  `NO_TRUSTED_CLOCK`/`NO_TRUSTED_PEER`/`SO_PEERCRED_NOT_BOUND`；
  **KERNEL_PEER_NOT_RUN**。**E 在 prototype API 形状上关闭；真实 peer 仍 NOT_RUN。**

### F. Finalize / effect / production

- `REAL_FINALIZE_SAME_TXN` **NOT_PROVEN**；`finalize_run_verified` 仍开自己的
  事务，prototype 没有组合它。
- 无生产 import；成功 receipt 无 `verified`；`effect_eligible:false`。
- **F 保持 P0 生产门槛。**

---

## 4. P1 新发现 — `check_writer_fenced` 漏 grant epoch

**静态精确位置：**

- `atomic_fence.py:739-758`：`check_writer_fenced` 读取 `proto_commit`
  （含 `grant_epoch`, `policy_epoch`, `authority_epoch`）、当前
  `proto_owner_policy`、当前 `proto_grant`。
- `atomic_fence.py:759`：调用 `_check_commit_windows(...)`；它检查 policy/
  grant 窗口、revocation、`grant.owner_policy_epoch == policy.policy_epoch`。
- `atomic_fence.py:763-768`：比较 `commit["policy_epoch"]` 与
  `policy["policy_epoch"]`、`commit["authority_epoch"]` 与
  `policy["authority_epoch"]`。
- **缺失**：`commit["grant_epoch"]` 与 `grant["grant_epoch"]` 的比较。
  `proto_commit.grant_epoch`（schema 270-283 / insert 625-634）和
  `proto_grant.grant_epoch`（251-264 / insert 339-349）都存在，但
  `check_writer_fenced` 没有使用两者做 fence。

**动态反例（LOCAL_REAL_SQLITE）：**

```text
1. acquire(request_id=req:1) -> PROTOTYPE_FENCED_COMMIT token 1,
   proto_commit.grant_epoch = 1
2. UPDATE proto_grant SET grant_epoch = 2 WHERE grant_ref = 'grant:proto'
   (policy.policy_epoch / authority_epoch unchanged)
3. check_writer_fenced(resource, owner='human:A', token=1)
   -> PROTOTYPE_FENCED_CHECK authorized: true, historical: false,
      effect_eligible: false
   expected -> BLOCKED_STALE_EPOCH / refusal
```

即：**grant 的 generation fence 已经前进，但旧的 pre-revoke/旧 generation
lease token 仍被新的 fenced writer check 接受。** 这是 P1（stale grant epoch /
authority generation fence），不是 production legacy gap；它是 #376 新增
`check_writer_fenced` 自身的缺口。作者测试覆盖了 acquire 时的
`STALE_GRANT_EPOCH`，但没有覆盖 commit 后 grant epoch 变化时的 fenced check。

**最小修复（作者侧，不由 QA 修改）：**

```python
if int(commit["grant_epoch"]) != int(grant["grant_epoch"]):
    raise PrototypeRefusal(
        "BLOCKED_STALE_EPOCH",
        "the grant generation moved after this commit")
```

并在 `check_writer_fenced` 加回归测试：acquire → bump `proto_grant.grant_epoch`
→ 断言 refusal；另可比较当前 context 的 `grant_ref`/actor 与 commit binding。
在修复前，`check_writer_fenced` 不能声称覆盖 grant generation fence。

---

## 5. P0 production-wiring barriers（未修，正确披露）

这些是 #375/#376 已明确标为 `PRODUCTION_WIRING_REQUIRED` 的生产接线门槛，
不是 #376 prototype 的回归，也不得被读成 production positive：

- **legacy `ServerOSStore.acquire_lease`**：在 prototype revoke 后仍写入
  `owner=human:LEGACY`, token 1, `proto_commit=0`，policy revoked=1。
- **legacy `ServerOSStore.check_writer`**：在 revoke 后仍对 pre-revoke token
  返回 `authorized:true`；新 `check_writer_fenced` 对同一 token 返回
  `POLICY_DENIED`。生产必须使用 fenced check 并在 effect 期间持有 fence。
- `serverosctl work revoke` 写 JSON ledger，不写这个 SQLite fence。
- `finalize_run_verified`/effect 独立事务；`REAL_FINALIZE_SAME_TXN =
  NOT_PROVEN`。
- 无真实 owner key/signature/trust root、无 kernel `SO_PEERCRED` 绑定、无
  migration/dual-read/divergence path。

PR body 明确写 “P0 production-wiring gaps — NOT fixed”；README 写
`PRODUCTION_WIRING_REQUIRED`，并有两个 legacy negative tests。**正确披露，
没有误称已解决。** 生产仍不得使用 prototype 作为 authorization。

---

## 6. P2 / NOT_RUN / 其他

- **P2**：`check_writer_fenced` 只用 caller 提供的 `owner`/`token` 字符串，
  不读取 constructor `synthetic_peer_uid` 或当前 context；它是 token/
  authority check，不是 caller authentication。README 已标
  `NO_TRUSTED_PEER`/`SO_PEERCRED_NOT_BOUND`；生产必须绑定 kernel peer 并在
  effect 期间持有 fence。
- **P2**：constructor clock 是 synthetic fixture（`NO_TRUSTED_CLOCK`）；
  production 必须用 store clock。
- **P2**：power-loss/fsync 未证明；`os._exit` 只是 process kill，README 已标。
- **P2**：HEAD DRIFT `02688cec` → `01e8a9c`（README +12 only）。
- **NOT_RUN**：真实 owner key/issuance/revocation、两个真实 OS uid、kernel
  `SO_PEERCRED`、production daemon/Fleet/WorkEngine wiring、DB migration、
  真实 Windows、Provider/OAuth/send；`REAL_FINALIZE_SAME_TXN` NOT_PROVEN。

---

## 7. 证据

```text
logs/a-prime-376-author50.log         50 passed rc0
logs/a-prime-376-01e8a9c-author50.log 50 passed rc0 (current head README delta)
logs/a-prime-376-probe-controls.log   29/29 rc0
logs/a-prime-376-probe-findings.log   4/4 rc1 (P0×2, P1×1, P2×1)
logs/a-prime-376-check-scope.log      SCOPE_OK rc0
logs/a-prime-376-364-three.log        27P/1S rc0
logs/a-prime-376-364control.log       2P rc0
probes/a_prime_376_probe.py           independent A/B/C/D/E/F witnesses
```

Reproduce:

```sh
cd <02688cec snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_376_probe.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_376_probe.py --mode findings
pytest -q -p no:cacheprovider tests/test_s1_atomic_prototype.py
```

所有运行 `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`；无 full pytest、无生产 SQLite/Daemon/Service/权限/OAuth/Provider，
未合 main、未强推/删除、未改作者代码。

**结论：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。** prototype-scope 的
A/B/C/E 与 D 的 revoke/expired 已通过；新增 P1 grant-epoch fenced gap 必须
由作者修复；P0 production-wiring barriers 已正确披露且未解决；真实 owner/
peer/生产接线/Windows 仍 NOT_RUN。
