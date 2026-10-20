# QA2 Round 8 — Context→Grant 重绑定独立 RED witness（#377 snapshot）

**首轮结论：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。新 P1：context fence 缺口；P2：context 无 epoch/generation；PRODUCTION_DEPENDENCY：真实 owner/peer/生产接线 NOT_RUN。不签 `QA_SIGNED`。**

| 项 | 值 |
|---|---|
| 唯一 snapshot | PR **#377** exact `8b42de479e5c0167f51bdb4cb8d7fba43be7479f` |
| base | #376 `01e8a9c9c0827ed9ff0f0579ad7e62b4a395546f`；RC-v9 `31bfc28…` |
| evidence | `LOCAL_REAL_SQLITE` + `SYNTHETIC_OWNER/CONTEXT/UID/CLOCK/PEER` |
| RED controls | 3/3 现有 grant/policy/epoch refusal 正确 rc0 |
| RED findings | **5/5 失败 rc1**：context rebind/actor/delete/same-grant mutation + commit 不持久 context |
| 真实 owner key / kernel `SO_PEERCRED` / 两真实 uid | **NOT_RUN** |

> 本报告只做独立 RED 证据与最小 invariant 建议；没有修改 #377 或任何 S1
> 源码。S1 后续修复 PR 由总控另行固定 SHA 派发复验。

---

## 1. 精确读取面

`atomic_fence.py`（#377）：

- `proto_commit` 列（`:270-283`）：

```text
request_id, request_digest, grant_ref, policy_ref, grant_epoch,
policy_epoch, authority_epoch, resource, owner, token, expires_at,
committed_at
```

**没有 `context_ref`，也没有 `actor_ref`。** `owner` 是
`policy.actor_ref`，不是当前 trusted context 的 actor。

- `proto_context` 列（`:265-269`）：

```text
context_ref, actor_ref, grant_ref
```

**没有 context epoch / generation / digest。**

- `check_writer_fenced`（`:730-802`）只读：

```text
leases            (resource, owner, token, expires_at)
proto_commit      (by resource/token)
proto_owner_policy(by commit.policy_ref)
proto_grant       (by commit.grant_ref)
```

**不读 `proto_context`。** `commit.grant_ref == grant.grant_ref` 是 tautology：
grant 行就是按 `commit["grant_ref"]` 选出来的（`:755-757`）。

- 成功 receipt 字段（`:644-662`）：`prototype_status`, `simulated_owner`,
  `owner_status`, `authorized`, `historical`, `effect_eligible`,
  `request_id`, `grant_ref`, `policy_ref`, `grant_epoch`, `policy_epoch`,
  `authority_epoch`, `resource`, `owner`, `token`, `expires_at`,
  `committed_at`, `replayed`。**没有 `context_ref`/`actor_ref`。**

---

## 2. RED witness（同一 temp DB，真实 WorkEngine schema）

先 fenced acquire：

```text
request:  {request_id: req:1, context_ref: ctx:proto, project: serveros,
           item_ref: SRV-0001, action: lease.acquire, ttl_s: 30.0}
receipt:  PROTOTYPE_FENCED_COMMIT, authorized:true, effect_eligible:false,
          grant_ref: grant:proto, policy_ref: policy:proto,
          grant_epoch:1, policy_epoch:1, authority_epoch:1,
          owner: human:A, token:1
lease:    {resource: work-write:SRV-0001, owner: human:A, token:"1",
           expires_at: 2033-05-18T03:33:50Z}
commit:   request_id req:1, grant_ref grant:proto, policy_ref policy:proto,
          grant_epoch 1, policy_epoch 1, authority_epoch 1,
          owner human:A, token 1
```

### 2.1 context.grant_ref 重绑定到另一合法 grant（同 policy / 同 grant_epoch）

插入 `grant:proto-2`（同 `policy:proto`、`grant_epoch=1`、
`owner_policy_epoch=1`、同 actor/scope），然后：

```sql
UPDATE proto_context SET grant_ref='grant:proto-2' WHERE context_ref='ctx:proto'
```

`check_writer_fenced(resource, owner='human:A', token=1)`：

```text
ACCEPTED
prototype_status=PROTOTYPE_FENCED_CHECK, authorized:true,
historical:false, effect_eligible:false, grant_epoch:1
```

实际状态：`context.grant_ref=grant:proto-2`，`commit.grant_ref=grant:proto`，
lease token 仍 `"1"`，`commit_count=1`，`token_next=2`。**旧 token 在 context
已指向另一个 grant 后仍 authorized。**

### 2.2 context.actor_ref 变更

```sql
UPDATE proto_context SET actor_ref='human:B' WHERE context_ref='ctx:proto'
```

`check_writer_fenced(owner='human:A', token=1)` → **ACCEPTED authorized:true**。
commit/lease 的 owner 仍是 `human:A`；context actor 已是 `human:B`。actor
变更未被任何 check 读取。

### 2.3 context 删除

```sql
DELETE FROM proto_context WHERE context_ref='ctx:proto'
```

`check_writer_fenced` → **ACCEPTED authorized:true**；`context_row=null`；
commit/lease/token 仍完整。**trusted context 已不存在，旧 token 仍被授权。**

### 2.4 同 grant_ref 的 context mutation

```sql
UPDATE proto_context SET actor_ref='human:C' WHERE context_ref='ctx:proto'
```

`grant_ref` 保持 `grant:proto`；`check_writer_fenced` → **ACCEPTED
authorized:true**。`proto_context` 没有 epoch/generation，因此这类 mutation
无法被任何 generation fence 观察到。

### 2.5 commit 不持久 context_ref/actor_ref

`DDL` 实测：

```text
proto_commit_columns = [request_id, request_digest, grant_ref, policy_ref,
  grant_epoch, policy_epoch, authority_epoch, resource, owner, token,
  expires_at, committed_at]
proto_context_columns = [context_ref, actor_ref, grant_ref]
```

commit row 有 `owner=human:A`、`grant_ref=grant:proto`，但**没有
`context_ref`/`actor_ref`**。`request_digest` 是 hash，`check_writer_fenced`
不使用它。因此即使 context 被删除/改 actor/改 grant，commit 侧没有可比对的
原值。

### 2.6 现有 refusal 仍然正确（controls 3/3）

| 变更 | 结果 |
|---|---|
| grant 行删除 | `BLOCKED_OWNER_POLICY`（the fenced commit has no grant） |
| grant.policy_ref → `policy:other` | `BLOCKED_OWNER_POLICY`（policy_ref mismatch） |
| grant.grant_epoch 1→2 | `BLOCKED_STALE_EPOCH`（grant epoch moved） |

说明 #377 的 grant/policy/epoch lineage 修复是有效的；缺口在 **context**。

---

## 3. 分级

### P1 — context fence 缺失

- context.grant_ref 重绑定到同 policy/同 grant_epoch 的另一合法 grant →
  旧 token `authorized:true`。
- context.actor_ref 变更 → 旧 token `authorized:true`，lease/commit owner
  仍是旧 actor。
- context 删除 → 旧 token `authorized:true`。
- 同 grant_ref 的 context actor mutation → `authorized:true`。
- commit 不持久 `context_ref`/`actor_ref`，check 不读 `proto_context`；
  `commit.grant_ref == grant.grant_ref` 是 tautology，无法替代
  `context.grant_ref == commit.grant_ref`。

这是 prototype 的 **effect-use fence 缺口**：acquire 时 trusted context 是
actor/grant 的根；commit 后 context 已变，fenced writer check 仍授权旧
token。分级 **P1**（LOCAL_REAL_SQLITE；synthetic owner/context/uid）。

### P2 — context 无 generation/epoch

`proto_context` 只有 `context_ref/actor_ref/grant_ref`，没有 epoch、
generation 或 digest。因此“同 grant_ref 的 context mutation”即使
`check_writer_fenced` 未来读取 context，也没有可比较的 generation；需要新增
`context_epoch` 或 `context_digest`，或规定 context 在 grant 生命周期内
immutable（任何 mutation 必须 bump grant/policy epoch 并重新 acquire）。

### PRODUCTION_DEPENDENCY（NOT_RUN）

- 真实 owner-issued key/issuance/revocation、真实 trusted context source；
- kernel `SO_PEERCRED`、两个真实 OS uid；
- production daemon/WorkEngine wiring/migration；
- 真实 Windows、Provider/OAuth/send。

这些不能用 synthetic context/uid 代替；本报告的 `SYNTHETIC_OWNER/CONTEXT/UID`
只是 fixture。此前轮次的 P0 production-wiring barriers（legacy
`ServerOSStore.acquire_lease`/`check_writer`、JSON work revoke、finalize
独立事务）仍未解决且已披露；本测试不改变它们。

---

## 4. 最小正确 fencing invariant（建议）

1. **commit 持久 context 根**：`proto_commit` 增加
   `context_ref TEXT NOT NULL`、`actor_ref TEXT NOT NULL`，以及
   `context_epoch INTEGER NOT NULL`（或 `context_digest TEXT NOT NULL`）。
   `acquire` 在写入 lease/commit 的同一事务中从 `proto_context` 读取并写入
   这些字段；不能从 request 或 caller 字段补齐。
2. **context 有 generation**：`proto_context` 增加
   `context_epoch INTEGER NOT NULL`（owner source 每次 actor/grant 变更必须
   bump），或至少对 `(context_ref, actor_ref, grant_ref)` 计算 digest。任何
   同 `grant_ref` 的 mutation 因此可观察。
3. **check_writer_fenced 同事务读取 context**：在现有 `BEGIN IMMEDIATE` 内
   按 `commit.context_ref` 读取 `proto_context`，并要求：
   - context 存在；
   - `context.context_ref == commit.context_ref`；
   - `context.actor_ref == commit.actor_ref == lease.owner == policy.actor_ref`；
   - `context.grant_ref == commit.grant_ref`（这是有意义的 grant lineage，
     不是 `commit.grant_ref == grant.grant_ref` 的 tautology）；
   - `context.context_epoch == commit.context_epoch`（或 digest 相等）；
   - 现有 grant/policy/authority epoch、window、revoke 检查保持；
   - 任一不匹配 → `BLOCKED_STALE_CONTEXT` / `BLOCKED_OWNER_POLICY`。
4. **context/grant 重绑定必须重新 acquire**：不能在同一 grant_epoch 下把
   context 指向另一个 grant 或改 actor；owner source 要么 bump
   context_epoch/grant_epoch，要么创建一个新的 grant 并让旧 token 失效。
5. 保持 `effect_eligible:false`；这不是 effect commit。

---

## 5. 计数 / 证据

| Gate | rc | 结果 |
|---|---|---|
| context probe controls（grant removed / policy_ref / grant_epoch） | 0 | **3/3** |
| context probe findings（rebind/actor/delete/same-grant/commit持久） | 1 | **5/5 失败（P1）** |
| #377 author 51 tests（round7 同 SHA） | 0 | 51 passed |
| round7 independent controls @#377 | 0 | 29/29 |
| #377 `check_scope` vs #376 | 0 | changed=3 allowed=3 forbidden=0 |

日志：

```text
logs/a-prime-377-ctx-controls.log   3/3 rc0
logs/a-prime-377-ctx-findings.log   5/5 rc1 (P1)
probes/a_prime_377_context_rebind.py
```

复现：

```sh
cd <8b42de47 snapshot>
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_context_rebind.py --mode controls
PYTHONPATH=$PWD A_PRIME_SNAPSHOT=$PWD \
  python3 <this>/probes/a_prime_377_context_rebind.py --mode findings
```

所有运行 `MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 /
timeout`；无生产 SQLite/Daemon/Service/权限/OAuth/Provider，未改作者代码，
未合 main/未强推/删除。

**首轮结论：`PROTOTYPE_QA_PARTIAL` / `NOT_V1_SIGNED`。** #377 的 grant/
policy/epoch P1 修复仍有效；新的 **P1 context fence** 与 **P2 context
generation** 需要 S1 后续修复；真实 owner/peer/生产接线仍
`PRODUCTION_DEPENDENCY / NOT_RUN`。
