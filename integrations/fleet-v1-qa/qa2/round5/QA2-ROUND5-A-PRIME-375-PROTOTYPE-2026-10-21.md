# QA2 Round 5 — A′ atomic fence prototype #375 独立 RED→GREEN 安全审查（2026-10-21）

**最终判定：`LOCAL_PROTOTYPE_QA` / `NOT_V1_SIGNED`。不签 `QA_SIGNED`。**

- 原型事务形状在 `LOCAL` + `REAL_TEMP` SQLite 下通过独立 controls：39/39，三次运行均 rc0。
- **P0×1（production-wiring barrier，作者 README 已披露）**：legacy `ServerOSStore.acquire_lease` 在 policy revoke 后仍可写入新 lease，绕过 fence。
- **P1×2**：pre-revoke lease token 在 policy revoke 后仍被 `ServerOSStore.active_lease`/`check_writer` 接受；`expected_*` epoch pins 不在 idempotency digest 中，同 request id 不同 pin 被当作 replay。
- **P2×2**：`now`/`peer_uid` 是 `acquire` 的 caller 参数，caller 可选过去时间绕过真实过期；README 未显式标注 trusted clock / SO_PEERCRED 依赖。
- **NOT_RUN**：真实 owner key/issuance/revocation、两个真实 OS uid、production daemon/Fleet/WorkEngine wiring、真实 Windows、Provider/OAuth/send。合成 owner/peer 不能代替这些事实。

| 项 | 值 |
|---|---|
| 用户请求的 exact head | `0c081f7786b23e9c2757440a41fcfd184bab815d` |
| **当前远端 PR head（实际）** | `d8edfe8d7989674775bc94f373008368e3284e3e` |
| base RC-v9 / #372 | `31bfc28d9ff4a1d0a56914fb8e4b82ec6ebf85fd` |
| diff（base..0c081f7） | 4 files, +1447；prototype/README/check_scope/dedicated test |
| diff（0c081f7..d8edfe8） | 仅 `tests/test_s1_atomic_prototype.py` +9 行（5 个 `FORK_ONLY` markers）；原型字节相同 |
| 作者测试 | 0c081f7 **36P rc0**；d8edfe8 **36P rc0**（Linux fork 可用；Windows 5 个 fork 测试会 SKIP） |
| 独立 controls | **39/39 rc0**，三次运行 |
| 独立 findings | **5/5 rc1**（P0/P1/P2） |
| `check_scope.py` | `changed=4 allowed=4 forbidden=0`，`SCOPE_OK`，rc0 |

> **HEAD DRIFT（P2 metadata）：** 用户给出的 exact head 是 `0c081f7`，但远端
> PR #375 当前 head 已是 `d8edfe8`（作者 PR body 也写 latest head `d8edfe8`）。
> 两者原型代码逐字节相同，后者仅在测试文件加 5 个 `FORK_ONLY` markers。
> 本报告对 `0c081f7` 做 exact 审计，并对 `d8edfe8` 的 36P 与 test-only delta
> 做了对照；不把 `d8edfe8` 冒充为 `0c081f7`。

---

## 1. 范围 / 生产未接线

`git diff --name-only 31bfc28..0c081f7` 只有：

```text
integrations/fleet-v1-atomic-prototype/README.md
integrations/fleet-v1-atomic-prototype/atomic_fence.py
integrations/fleet-v1-atomic-prototype/check_scope.py
tests/test_s1_atomic_prototype.py
```

- `atomic_fence.py` 没有被 `runtime/`、`workengine/`、`mcp/`、`fleet_mcp/`、
  `intake/` import（独立 grep 0 hits）。
- 成功 receipt 是 `PROTOTYPE_FENCED_COMMIT` + `simulated_owner: True` +
  `owner_status: SIMULATED_OWNER`，**没有 `verified` key**。
- README 标记 `PROTOTYPE_ONLY` / `V1_NOT_RELEASE` / `PROD_DEFAULT_BLOCKED` /
  `NO_OWNER_TRUST` / `NO_DAEMON_WIRING` / `NO_MIGRATION` / `NOT_V1_SIGNED`；
  `REAL_FINALIZE_SAME_TXN` 和 `REAL_WINDOWS` / `TWO_REAL_UIDS` 为 NOT_PROVEN/
  NOT_RUN。
- 生产 #364 仍 `BLOCKED_OWNER_COMMIT_SEAM`；本 PR 不改变 daemon/WorkEngine/
  Grant/owner policy/credential/service。

---

## 2. 作者测试（LOCAL）

```text
0c081f7: pytest tests/test_s1_atomic_prototype.py -q -p no:cacheprovider
         36 passed, rc0
d8edfe8: 36 passed, rc0
```

测试包含 author RED baseline（跨域读 → revoke → 独立写租约，断言该 gap 确实
发生）和 GREEN fenced 事务案例。36P 不是生产授权；作者 README 明确它只是
transaction-shape 证据。

---

## 3. 独立 controls（LOCAL + REAL_TEMP SQLite）

独立 probe：`probes/a_prime_independent_probe.py`，三次运行均 **39/39 rc0**。

### 3.1 进程/连接排序

| Control | 实际 |
|---|---|
| revoke 先 commit，另一进程 acquire | `POLICY_DENIED`，lease null，proto_commit 0 |
| acquire 先 commit，另一进程 revoke | token 1 / commit epoch 1 / policy epoch 2；新 request `POLICY_DENIED`；旧 token 保留为历史 |
| `multiprocessing.Barrier(2)` acquire vs revoke（无 sleep） | 观察到的三次均为 revoke 先串行化：acquire `POLICY_DENIED`、revoke epoch 2、lease null、commit 0；若 acquire 先，control 也接受并断言后续 request 被拒 |
| 两进程同 resource 争 token | 恰好 1 个 success / 1 个 `LEASE_HELD_BY_OTHER`；lease token 1；proto_commit 1；`token_next=2`；第三进程仍 `LEASE_HELD_BY_OTHER` |
| WAL / busy_timeout / integrity | `journal_mode=wal`；`busy_timeout=30000`；`integrity_check=ok` |
| crash before COMMIT（`os._exit(17)`） | no lease / no commit / no token advance；重新打开 integrity ok |
| commit 后重启 + 同 request replay | lease token 1、commit 1、token_next 2；replay `replayed=True` token 1；no new commit |
| 无生产 positive / 无生产 import | receipt 无 `verified`；`ServerOSStore.active_lease`/`check_writer` 读到 fenced lease；production import 0 hits |

### 3.2 Fail-closed matrix（39 controls 的一部分）

每个 refusal 都断言 `lease is None` 且 `commit_count()==0`：

- revoked/expired/future/issued-in-future/unreadable-window policy；
- revoked/expired grant；stale owner-policy epoch / authority epoch / grant epoch；
- cross uid / cross actor / cross project / cross item / cross action；
- missing owner attestation / policy row / grant row / context row；
- non-bool `revoked`（2）policy 和 grant；
- TTL 0 / negative；
- caller 注入 `verified` / `COMMIT_FENCED` / `owner` / `uid` / `actor_ref` /
  `policy_ref` / `epoch` → `INVALID_REQUEST`；
- missing request id；同 request id + 不同 ttl → `IDEMPOTENCY_CONFLICT`，no new
  lease/commit。

---

## 4. Findings

### P0-1 — legacy `ServerOSStore.acquire_lease` 在 revoke 后写入新 lease（production-wiring barrier，README 已披露）

独立 witness 在 prototype 初始化的同一 temp DB 上：

1. seed policy/grant/context；
2. `revoke_policy(POLICY_REF, expected_policy_epoch=1)` → policy `revoked=1`；
3. 导入真实 `workengine.store.ServerOSStore`，直接调用
   `store.acquire_lease(RESOURCE, owner="human:LEGACY", ttl_s=30)`；
4. 实际：`lease.owner="human:LEGACY"`, `token=1`；`proto_commit` 仍为 **0**；
   policy `revoked=1`；`active_lease` 读到该 lease。

这就是用户要求的 **PRODUCTION-WIRING P0 BARRIER**：legacy 调用绕过 fence，
撤销后可以出现新 lease。作者 README 的第 54–71 行**正确披露**了它：
“Anyone can still call `ServerOSStore.acquire_lease` directly … why the
production default stays blocked.” PR body 也披露。因此这不是作者把原型
冒充生产，而是明确的生产接线前置条件：必须把所有 lease mutation 收口到
一个 fenced entrypoint。

### P1-1 — pre-revoke lease token 在 revoke 后仍被 `active_lease`/`check_writer` 接受

Witness：

1. fenced acquire 成功，token 1，commit row `policy_epoch=1`；
2. `revoke_policy` 成功，policy `policy_epoch=2`, `authority_epoch=2`,
   `revoked=1`；
3. 真实 `ServerOSStore.active_lease(RESOURCE, now=NOW+1)` 仍返回 token 1；
4. `ServerOSStore.check_writer(RESOURCE, owner=ACCOUNT, token=1)` 返回
   `authorized: true`。

现有 WorkEngine writer guard 只看 `leases` 行/owner/token，不读
`proto_commit` 的 policy/authority epoch。该 token 是“历史 token”，符合用户
“acquire 先 commit 时保留既有历史 token”的要求；但**生产 consumer 若只用
`check_writer` 在 revoke 后继续写，就没有被 authority epoch fence**。README
第 33–34 行说“a later revoke does not rewrite its token or epoch”，部分披露了
token 保留，但没有显式说明 `check_writer` 仍会授权。建议：

- 在 `revoke_policy` 的同一事务中把既有 lease 标记为 revoked/拒绝，或
- 生产 writer 在 `check_writer` 之外必须校验 `proto_commit` 的 authority/
  policy epoch；原型不为这一步提供实现，因此 `NOT_V1_SIGNED`。

严重度 **P1**；如果 production 直接把 `check_writer` 当作写授权，则应升为
P0。

### P1-2 — `expected_*` epoch pins 不在 idempotency digest 中

`_digest`（`atomic_fence.py:145-157`）只包含
`request_id/context_ref/project/item_ref/action/ttl_s/peer_uid`，不包含
`expected_authority_epoch` / `expected_grant_epoch`。Witness：

```text
acquire(request_id=req:1, expected_authority_epoch=1) -> success token 1
acquire(request_id=req:1, expected_authority_epoch=2) -> replayed=true token 1
        (expected IDEMPOTENCY_CONFLICT)
```

同 request id + 不同 pin 被当作 replay，返回旧 receipt，而不是
`IDEMPOTENCY_CONFLICT`。这是 **P1 replay gap**（用户把 replay 缺口列为 P1）。
最小修复：把两个 expected pin 规范化（None 也纳入）后加入 `_digest`，并加
同 id 不同 pin 的回归测试。

### P2-1 — `now` / `peer_uid` 是 caller 参数，caller 可选过去时间绕过真实过期

`acquire(..., peer_uid, now)` 的 `now`/`peer_uid` 是 out-of-band 参数，不在
`_ALLOWED_REQUEST_KEYS` 中（request 不能注入，这是好的）。但 API caller 可以
传任意值。Witness：policy/grant valid 2000–2001，`now=960_000_000`（2000-06）
→ `PROTOTYPE_FENCED_COMMIT` token 1；真实时钟是 2026，policy 早已过期。
`peer_uid` 同理由 caller 选择，policy 只需与之匹配。

生产 entrypoint 必须从可信时钟与 `SO_PEERCRED` 提供这两个值；原型不提供该
信任根。若生产 route 接受 caller 控制的 `now`，该问题应升为 **P0**（过期
policy 仍写 lease）。当前原型是 `NO_DAEMON_WIRING`，记为 **P2**。

### P2-2 — README 未显式标注 trusted clock / SO_PEERCRED

README 已正确标注 `NO_OWNER_TRUST`、`NO_DAEMON_WIRING`、`TWO_REAL_UIDS
NOT_RUN`，并说明 peer uid/context 由 harness 提供；但没有显式的
`NO_TRUSTED_CLOCK` / `NO_TRUSTED_PEER` / `SO_PEERCRED` 依赖项。P2 文档/覆盖
问题；建议在 flags/NOT_PROVEN 中加入这两项，并说明生产 entrypoint 的信任
来源。

---

## 5. 可信事实读取 / 撤销路径 / JSON

- `acquire` 在 `with self._transaction()`（`BEGIN IMMEDIATE`，`atomic_fence.py:516`）
  内读取 `proto_context`、`proto_grant`、`proto_owner_policy`（`:527-545`），
  执行 policy/grant/identity/scope 检查（`:546-563`），读取 lease 行、分配
  `id_sequences` token、写 `leases` + `proto_commit`（`:565-595`）。这些读取
  没有在锁外发生。
- `revoke_policy` 使用同一 `_transaction`/同一 SQLite DB（`:616-672`），
  policy/authority epoch 在同一 commit 中推进；这是正确的同域撤销。
- 原型没有 JSON/file policy 读取；`json` 只用于 scope 列表和 digest。外部
  JSON-only revoke 不会被原型“无意允许”为 fence 状态，但也**不会**更新
  SQLite fence；生产 owner 必须写同一 DB。README 的 authoritative-source
  premise 说明这一点。
- P0/P1 的缺口在 fence **之外**：legacy `ServerOSStore.acquire_lease` 写同一
  `leases` 表但不读 `proto_*`；`check_writer` 读 lease row 但不读
  `proto_commit` epoch。原型无法在不改 WorkEngine entrypoint/guard 的情况下
  关闭它们。

---

## 6. NOT_RUN / 不能以测试代替的事实

- 真实 owner-issued key/issuance/revocation：**NOT_RUN**；owner policy/
  attestation 是 synthetic fixture 字符串，不是签名/key。
- 两个真实 OS uid / 真实 `SO_PEERCRED`：**NOT_RUN**；peer_uid 是 harness
  参数。
- production daemon/Fleet/WorkEngine wiring、DB migration：**NOT_RUN**；
  `NO_DAEMON_WIRING` / `NO_MIGRATION`。
- 真实 Windows：**NOT_RUN**；d8edfe8 的 `FORK_ONLY` 只是测试 marker。
- Provider/OAuth/send/credential：**NOT_RUN**。
- `finalize_run_verified` 与 lease 同事务：**NOT_PROVEN**（README 已标）。

---

## 7. 结论与建议顺序

1. **P0 barrier（必须生产接线前解决）**：把所有 lease mutation 收口到一个
   fenced entrypoint；legacy `ServerOSStore.acquire_lease` 不得在 production
   可达。README 已披露，继续保持 `NO_DAEMON_WIRING`。
2. **P1**：revoke 时在同一事务中 fence/revoke 既有 lease，或要求 writer 校验
   `proto_commit` 的 authority epoch；把 expected pins 加入 idempotency digest。
3. **P2**：在 flags/NOT_PROVEN 中明确 trusted clock/SO_PEERCRED；生产
   entrypoint 不接受 caller 控制的 now/peer。
4. **保持** `PROTOTYPE_ONLY` / `NOT_V1_SIGNED`，直到真实 owner key、可信 OS
   peer、production wiring 与真实 Windows 证据具备。

**最终：`LOCAL_PROTOTYPE_QA` / `NOT_V1_SIGNED`；不签 `QA_SIGNED`。**
