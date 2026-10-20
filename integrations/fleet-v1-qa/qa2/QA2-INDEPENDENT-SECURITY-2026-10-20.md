# QA2 — S1 #358 / R1 #356 独立安全审计（2026-10-20）

**结论：`QA_PARTIAL` / `NOT_RELEASE`。**

- **无 P0。** 两个 PR 在当前生产可达路径上仍然 fail-closed：S1 的生产
  `serverosd` 不绑定 owner resolver，`agent.grant.issue` 与
  `agent.lease.authority.verify` 都返回 `BLOCKED_OWNER_POLICY`；R1 的 gate
  没有被任何生产模块 import，production 模式恒返回
  `BLOCKED_PROVIDER_SOURCE` / `BLOCKED_OWNER_SIGNATURE`，没有 send/retry/settle。
- **但 P1 必须在绑定 owner source / wiring reconcile gate 之前修复**，否则
  “verify 之后 commit 之前撤销”和“synthetic 结果被上层误读”会变成真实的
  授权/结算缺口。
- **正向真实 owner resolver、真实多 UID、真实 Provider 回读/owner-signed
  observation 均为 `BLOCKED` / `NOT_RUN`**，不能作为 PASS 依据。

| Lane | PR | head（精确） | base（精确） | 生产状态 | QA |
|---|---|---|---|---|---|
| S1 owner policy | #358 | `b183aab0b8913ed36e5de539c50223d3eaf508f1` | `8620a90e58cf92ba261560b1e4eb5f0797beee09`（`rc/i1-v6-review-20261020`） | `BLOCKED_OWNER_POLICY` | `QA_PARTIAL` |
| R1 reconcile source | #356 | `b4020b73e6b7ec2b1f6301471a9872f8864c1643` | `34b215c355d93efe9e7b06fcc09f9a803c061f00`（#311 head） | `BLOCKED_PROVIDER_SOURCE` | `QA_PARTIAL` |

两个 head 均**不在** RC-v7 `85c79b2` 中；本 QA 的 base 是
`rc/i1-v7-review-20261020`。QA 工作树只新增
`integrations/fleet-v1-qa/qa2/**`，没有编辑产品源码、两位作者的工作树、
S1/R1 分支、生产 `serverosd`/Fleet/Connector、Provider、OAuth 或系统服务。

---

## 1. 精确范围与证据级别

### 1.1 S1 #358

| 项 | 值 |
|---|---|
| head | `b183aab0b8913ed36e5de539c50223d3eaf508f1` |
| base | `8620a90e58cf92ba261560b1e4eb5f0797beee09` |
| diff | 9 files, +1273/−13：`runtime/owner_policy.py`、`runtime/agent_control_store.py`、`runtime/daemon.py`、docs、tests |
| QA checkout | `git archive` 到 `ARTIFACTS_ROOT/skills/qa2-independent-security/2026-10-20/checkouts/s1-358-b183aab`，未进入作者 worktree |
| 证据级别 | `LOCAL_SYNTHETIC_PEER`（合成 peer 元数据，不是 `SO_PEERCRED`）；`LOCAL_SYNTHETIC_POLICY`（测试 `OwnerPolicy`）；`LOCAL_SYNTHETIC_TRUST`（临时 HOME + 本地 Ed25519 key，不是 Provider）；作者 `REAL_TEMP` 测试另计 |
| 未运行/阻塞 | 真实 owner source；两个真实 UID；真实 Provider 签名；原子 verify→commit seam |

### 1.2 R1 #356

| 项 | 值 |
|---|---|
| head | `b4020b73e6b7ec2b1f6301471a9872f8864c1643` |
| base | `34b215c355d93efe9e7b06fcc09f9a803c061f00`（#311） |
| diff | 5 files, +1129/−0：`reconcile_source_gate.py`、fixture、docs、README、tests |
| QA checkout | `git archive` 到 `.../checkouts/r1-356-b4020b7` |
| 证据级别 | `SYNTHETIC_FIXTURE`：fixture 的 `test_key` 是公开测试 key，不是 owner authority；没有真实 Provider、token、transcript、socket、subprocess 或付费 send |
| 下游参考 | 读了 pinned #328 `cf32af82c03eb52a4f34e2907e670f23b04a9b57` 的 `runtime/dispatch_attempts.py#reconcile_observation`；#328 **不在** #356 的 base 中，gate 也未 wiring，故下游只作 SOURCE 参考，不宣称集成通过 |

### 1.3 运行环境

2026-10-20 Tokyo shared dev；swap 近似耗尽（`32738/32767 MiB` used，
`29 MiB` free）。所有测试均：

```text
systemd-run --user --scope -q \
  -p MemoryMax=1G -p MemorySwapMax=0 -p CPUQuota=25% -- \
  nice -n 10 timeout 120 <pytest|python3> ...
PYTHONDONTWRITEBYTECODE=1  -p no:cacheprovider
```

单进程、无网络、无 Provider/model/credential/OAuth、无生产 daemon/Fleet/
Connector 调用；未创建 venv、浏览器 profile 或 HOME 测试输出。

---

## 2. 发现总表

| ID | Lane | 级别 | 发现 | 生产可达性 |
|---|---|---|---|---|
| S1-P1-1 | S1 | **P1** | `policy.revoked is True` 只识别精确 bool；`revoked=1` / `"yes"` 被当成未撤销 | 需绑定 resolver；生产当前无 resolver |
| S1-P1-2 | S1 | **P1** | verify→revoke→return 竞态：首次读 grant 后撤销，route 仍返回 `verified: true`；没有原子 commit seam | 需绑定 resolver + 并发撤销 |
| S1-P2-1 | S1 | P2 | `policy.issued_at` 只解析形状，未与 `now` 比较；未来签发的 policy 仍授权 | 需绑定 resolver |
| S1-P2-2 | S1 | P2 | `issue_grant` 不把 `OWNER_WRITTEN_GRANT_FIELDS` 应用于 `approved`；靠 `verify_delegation` 的白名单兜底 | 真实 verifier 当前会挡；防御纵深 |
| S1-P2-3 | S1 | P2 | 幂等 issue readback 在 owner resolver 之前返回；policy 撤销后重放同一 evidence 仍 `issued: true` | 需绑定 resolver |
| R1-P1-1 | R1 | **P1** | synthetic 结果带 `owner_signed: true`、`s2_verdict/s2_status: SETTLE_APPLIED`、`settle_allowed: true`；上层只读这些字段会误结算 | 当前无生产 import；future wiring 风险 |
| R1-P1-2 | R1 | **P1** | `observation_id` 为空/缺失时 replay 检查被跳过；签名的 APPLIED 可重放 | synthetic-only；contract 缺口 |
| R1-P2-1 | R1 | P2 | signed `source` 字段从不与 source capability 比对；`forged:source` 被接受 | synthetic-only |
| R1-P2-2 | R1 | P2 | signed `observed_at` 不解析、不校验 freshness；`not-a-time` 被接受 | synthetic-only |
| R1-P2-3 | R1 | P2 | source gate 只信 `present`/`capability`，忽略 `thread_known`/adapter/verb；矛盾声明可通过 | synthetic-only |
| R1-P2-4 | R1 | P2 | signed payload 不含 writer owner/epoch/token；epoch 7→8 的旧 observation 仍可 settle（若 applied set 未提供） | synthetic-only |

**为什么没有 P0：** 两个 PR 的生产入口都是 fail-closed。S1 唯一的生产
`Daemon(...)` 构造点在 `runtime/daemon.py:5195-5201`，没有传
`owner_policy_resolver`；因此生产 issue/verify 都在 resolver 检查处返回
`BLOCKED_OWNER_POLICY`。R1 gate 没有被 `runtime/`、`mcp/`、`fleet_mcp/`、
`workengine/`、`intake/` 或 `integrations/`（除自身目录）import；production
模式在 source 通过后固定返回 `BLOCKED_OWNER_SIGNATURE`，且
`send_allowed=False`、`retry_allowed=False`、`production_eligible=False`。

---

## 3. S1 #358 — owner policy

### 3.1 P1-1：`revoked is True` 的精确 bool fail-open

**精确代码** `runtime/owner_policy.py:118`：

```python
if policy.revoked is True:
    return _refusal("POLICY_DENIED", ...)
```

**推演。** `OwnerPolicy` 是 frozen dataclass，但 Python 不做运行时类型强制。
owner source 若从 JSON/整数列反序列化出 `revoked: 1`，或从字符串配置读出
`"yes"`，`is True` 为假，policy 被当作未撤销。`_common_refusal` 随后继续检查
epoch、window、uid、actor，全部匹配时 `check_policy_binding` 返回 `None`，
verify 返回 `verified: true`。这与同一函数对 `epoch` 的严格类型检查
（`isinstance(policy.epoch, bool) or not isinstance(policy.epoch, int)`，
`owner_policy.py:121-125`）不一致：epoch 的非 bool/非 int 会被
`BLOCKED_OWNER_POLICY`，revoked 的非 bool 却 fail-open。

**实测（LOCAL_SYNTHETIC_POLICY，日志 `logs/s1-findings.log`）：**

```text
f1-revoked-int  actual={"verified": true, "reason": null}  expected=POLICY_DENIED
f1-revoked-str  actual={"verified": true, "reason": null}  expected=POLICY_DENIED
```

同一探针中 `revoked=True` 正确返回 `POLICY_DENIED`（`s1-controls.log`）。

**最小负测。** 对 `revoked in (1, 0, "yes", "false", None)` 构造 policy，
断言：非 bool 一律 `BLOCKED_OWNER_POLICY`；bool `True` 为 `POLICY_DENIED`；
bool `False` 才进入后续检查。

**最小修复。**

```python
if not isinstance(policy.revoked, bool):
    return _refusal(BLOCKED_OWNER_POLICY,
                    f"the owner policy revoked field {policy.revoked!r} is not a bool")
if policy.revoked:
    return _refusal("POLICY_DENIED", ...)
```

不要只用 `if policy.revoked:`：那会把 `"false"` 也当成已撤销，虽然
fail-closed，但会把数据错误伪装成 owner 决定；严格类型 + 明确拒绝更可审计。

### 3.2 P1-2：verify→revoke→return 的 TOCTOU；原子 commit seam 为 BLOCKED

**精确代码** `runtime/daemon.py:3047-3094`（顺序）：

```python
result = verify_live_grant(store.grants(), ...)      # 3047：读 snapshot
if not result.get("verified"):
    return result
grant = store.get_grant(str(params.get("grant_ref") or "").strip()) or {}  # 3065
policy = resolver.resolve(...)                        # 3079
refusal = check_policy_binding(grant=grant, ...)      # 3088
return {**result, "owner_policy": audit(policy)}      # 3094
```

`check_policy_binding` 不检查 `grant["revoked"]`；它信任
`verify_live_grant` 之前的撤销检查。两次读取（`store.grants()` 与
`store.get_grant()`）之间，以及 resolver 调用期间，另一个 actor 可以
`revoke_grant`。route 返回的 `result["grant"]` 仍是撤销前的 10 字段快照
（`"revoked": false`），`verified: true`。

**实测（LOCAL_SYNTHETIC_POLICY，日志 `logs/s1-findings.log`）。**
resolver 在 `resolve` 内调用 `store.revoke_grant(...)`，模拟并发撤销：

```text
f3-revoke-after-verify-before-return
  actual={"verified": true, "reason": null, "stored_revoked": true}
  expected={"verified": false, "stored_revoked": true}
```

`stored_revoked: true` 证明撤销已落盘，但返回值仍是 `verified: true`。

**推演与 BLOCKED 判定。** 该 route 是只读的，PR 没有增加 lease token、
generation fence 或 commit-time CAS。即使 route 在返回前再读一次，return 之后
到 Work Engine 写入之间仍有窗口；真正的原子性只能由持有写锁的 commit seam
提供（在同一个 store mutation 内重检 grant epoch/revocation，或返回一次性
lease token）。因此：

- “verify before commit”作为**时点读**成立；
- 作为**原子授权**是 **`BLOCKED`**，本 PR 没有提供可验证的 commit seam。

**最小负测。** 上面的 deterministic revoke-in-resolver witness；再加一个
consumer 测试：在 verify 返回后、commit 写入前撤销 grant，commit 必须拒绝
（而不是依赖之前的 `verified: true`）。

**最小修复（按代价排序）。**

1. 在 `check_policy_binding` 之后重新 `get_grant` 并重跑
   `verify_live_grant`，若 `revoked`/`epoch`/`owner_policy_*` 变化则拒绝；
   这只能缩小窗口，不能关闭。
2. 在 `AgentControlStore` 增加 `verify_and_commit`/lease token：verify 与
   写操作在同一个 `_mutation()` 内完成，token 绑定 grant epoch 与
   owner policy epoch；consumer 只能提交一次。
3. 在 (2) 落地前，把返回标为 `point_in_time: true`，并明确禁止把
   `verified: true` 当作 lease。

### 3.3 P2-1：`issued_at` 未来时间未被拒绝

`runtime/owner_policy.py:129-133` 只检查 `issued_at` 是可读的 zoned instant，
没有与 `now_instant` 比较。实测：

```text
f2-policy-issued-in-future
  policy.issued_at="2999-01-01T00:00:00Z"
  actual={"verified": true, "reason": null}
  expected=BLOCKED_OWNER_POLICY
```

`valid_from`/`expires_at` 控制生效窗口，但 `issued_at` 是 owner 审计字段；
一个“尚未签发”的 policy 现在就能授权，是契约错误。最小修复：在 `now_instant`
可用后加

```python
if instant(policy.issued_at) > now_instant:
    return _refusal(BLOCKED_OWNER_POLICY,
                    f"{policy.policy_ref} was issued in the future ...")
```

负测：`issued_at` 为 2999、当前、2000 三组；只有前两组（未来）拒绝。

### 3.4 P2-2：`issue_grant` 对 `approved` 不应用 owner-written 字段拒绝

`runtime/agent_control_store.py:2059-2090`：

```python
recorded = {**approved, **policy_fields, "revoked": False, ...}
```

`put_grant` 在 `2162-2170` 明确拒绝 `owner_policy_ref`、`owner_policy_epoch`、
`owner_policy_issued_by`、`owner_policy_issued_at`、`actor_ref`；但
`issue_grant` 只在 resolver 存在时用 `policy_fields` 覆盖这些字段，**没有**
在 `approved` 上应用同一拒绝。若 `approved` 含这些字段且 resolver 为 `None`
（或未来 verifier 白名单放宽/被替换），它们会被原样写入。

**实测（LOCAL_SYNTHETIC_VERIFIER，日志 `logs/s1-findings.log`）。**
用一个测试 verifier 返回含 `owner_policy_ref="policy:forged"` 的 `approved`：

```text
f4-issue-without-resolver-stores-approved-owner-fields
  stored_owner_policy_ref="policy:forged"
  verified_after_forgery=true
```

**真实 verifier 当前挡住了。** `context/actor.py#verify_delegation` 的
`allowed` 集合只允许
`grant_ref/valid_from/expires_at/projects/items/actions/scope_digests/covers/budget/engineering`；
`owner_policy_ref`、`owner_policy_epoch`、`owner_policy_issued_by`、
`owner_policy_issued_at`、`actor_ref`、`os_principal`、`epoch`、`revoked`
都在签名验证后被 `DELEGATION_SCOPE_INVALID` 拒绝。我们的
**本地 Ed25519 真实签名探针**（临时 HOME，非 Provider）10/10 通过，逐字段
确认了这一拒绝（`logs/s1-signed-probe.log`）。因此这是 **P2 防御纵深**，
不是当前可利用路径；但应在绑定 owner source 前补上 store 自身的拒绝，避免
未来 verifier 变更把这条缝变成真缝。

**最小负测。** 分别在有/无 resolver 时，让 `approved` 携带上述字段，断言
`StoreError` 且零新增 grant。

**最小修复。** 在 `issue_grant` 的 `verify_delegation` 返回后：

```python
smuggled = set(approved) & set(OWNER_WRITTEN_GRANT_FIELDS)
if smuggled:
    raise StoreError(
        f"provider-approved scope may not name {sorted(smuggled)}; "
        f"the owner binding is written only by the resolver")
```

### 3.5 P2-3：幂等 issue readback 在 owner resolver 之前返回

`runtime/agent_control_store.py:2042-2047`：

```python
if existing is not None:
    issuance = existing.get("issuance") or {}
    if issuance.get("evidence") == evidence:
        return json.loads(json.dumps(existing))
```

该分支在 `verify_delegation` 和 owner resolver **之前**。若 owner policy 在
首次签发后被撤销，重放同一 evidence 会直接返回旧 grant，route 仍回答
`issued: true`；撤销的 policy 完全不被查询。

**实测（LOCAL_SYNTHETIC_VERIFIER，日志 `logs/s1-findings.log`）。**

```text
f5-idempotent-issue-readback-skips-revoked-policy
  first_issued=true
  second_issued=true
  second_reason=null
  stored_owner_policy_ref="policy:test"   # 第二次 resolver 是 revoked=True
```

**影响。** `issued: true` 是 mutating boundary 的回答；消费者可能把它读成
“当前仍获授权”。verify route 会拒绝（policy 已撤销），但 issue route 的
回答本身没有区分 “newly issued” / “readback”。这是 PR 新增 owner policy
维度后出现的语义缺口。

**最小负测。** issue → 换 revoked policy → 同一 evidence 再 issue；断言
`issued: false` / `POLICY_DENIED`，或至少返回 `readback: true, issued: false`
且不把旧 grant 当作当前授权。

**最小修复。** 在 `existing` 分支上重新执行 owner resolver +
`check_issue_policy`（以及 binding 检查），命中则拒绝；或返回明确的
`{"issued": false, "readback": true, "grant": existing, "reason": "..."}`
并要求消费者只信 verify。不要继续用 `issued: true` 表示 readback。

### 3.6 S1 正向控制（PASS）

以下均在真实 `Daemon.handle` / `AgentControlStore` 代码上通过
（`logs/s1-controls.log`，36/36，rc 0）：

- 无 resolver：`agent.grant.issue` 返回 `issued: false`、
  `BLOCKED_OWNER_POLICY`，`grants() == {}`；verify 返回
  `BLOCKED_OWNER_POLICY`。
- 有 resolver：返回恰好 10 字段 `LeaseGrant`（`principal/grant_ref/project/
  items/actions/valid_from/expires_at/revoked/epoch/os_principal`），
  top-level `owner_policy` audit；`items/actions` 是 tuple，epoch 是 int。
- `revoked=True` → `POLICY_DENIED`；`expired` / `not-yet-valid` →
  `POLICY_DENIED`；`stale epoch` → `BLOCKED_OWNER_POLICY`；wrong uid/actor →
  `UNTRUSTED_ACTOR`；out-of-scope → `POLICY_DENIED`；empty `issued_by` /
  bad `issued_at` / epoch 0/bool → `BLOCKED_OWNER_POLICY`。
- grant binding：缺 `owner_policy_ref`、ref 不匹配、`owner_policy_epoch`
  不匹配/缺失/bool → `BLOCKED_OWNER_POLICY`。
- caller 在 verify params 里塞 `owner_policy_ref=policy:permissive`：route
  仍用 grant 中存储的 strict policy，返回 `POLICY_DENIED`；塞
  `actor_ref=human:B` 被忽略，仍用 stored context 的 `human:A`。
- `put_grant` 对五个 owner 字段逐一 `StoreError`，零写入。
- issue 路径：无 peer、resolver None、resolver raises、revoked/expired/
  not-yet-valid/wrong-uid/wrong-actor/out-of-scope/epoch-0/epoch-bool/
  empty-issued-by/bad-issued-at 全部 `StoreError` 且零新增 grant。
- **真实本地 Ed25519 签名**（`s1_signed_probe.py`，10/10）：合法 signed
  evidence 经真实 `verify_delegation`，resolver 写入
  `owner_policy_ref/epoch/actor_ref`；八个 owner/OS 字段逐一被
  `DELEGATION_SCOPE_INVALID` 拒绝。
- **同一受信身份源**：issue 用 `actor.verify_delegation`（`build` +
  `authorizes`）并把 context 存入 store；verify 用
  `actor_mod.resolve(ref, store.contexts())` 读同一 stored context，
  `verify_live_grant` 再调用 `authorizes`。issue route 的 params 被限制为
  `{"evidence"}`（`daemon.py:3007-3018`），caller 不能提供
  `os_principal`/`actor_ref`/`owner_policy_ref`。
- **生产 resolver 为 None**：`runtime/daemon.py:5195-5201` 的 `Daemon(...)`
  没有 `owner_policy_resolver`；`runtime/` 中没有第二个非测试构造点。

### 3.7 S1 BLOCKED / NOT_RUN

- 真实 owner-issued uid→actor→scope source：**不存在**；本报告中的 policy
  都是测试 fixture，不是生产 owner。
- 两个真实 UID：**`BLOCKED_MULTIPRINCIPAL_NOT_RUN`**；本机无 passwordless
  sudo/setuid。合成 peer 只是 `LOCAL`，不是 `REAL_OS`。
- 真实 Provider 签名：**BLOCKED**；本地 Ed25519 key 只是
  `LOCAL_SYNTHETIC_TRUST`，临时 HOME，不是 Provider。
- verify→commit 原子性：**BLOCKED**，见 S1-P1-2。
- 作者 `REAL_TEMP` 测试使用真实 AF_UNIX + 真实 `SO_PEERCRED`，但
  context/grant/policy 是 fixture；我们没有把它升级成真实多 UID 证据。

---

## 4. R1 #356 — reconcile source gate

### 4.1 P1-1：synthetic 结果携带生产字段名，存在上层误读

**精确代码** `integrations/fleet-v1-runtime/reconcile_source_gate.py`：

```python
# 218-219
owner_signed = True
authority = SIGNATURE_AUTHORITY          # "SYNTHETIC_TEST_ONLY"

# 337-346
return _result(
    decision=SYNTHETIC_ACCEPTED_APPLIED,
    s2_verdict=S2_SETTLE_APPLIED,        # "SETTLE_APPLIED"
    settle_allowed=True,
    owner_signed=owner_signed,
    signature_authority=authority,
)
```

**实测（`logs/r1-findings.log`）：**

```text
f4-synthetic-result-production-field-names
  owner_signed=true
  s2_verdict=SETTLE_APPLIED
  s2_status=SETTLE_APPLIED
  settle_allowed=true
  production_eligible=false
  production_decision=BLOCKED_PROVIDER_SOURCE
  mode=synthetic
  signature_authority=SYNTHETIC_TEST_ONLY
```

**推演。** synthetic HMAC 用的是 fixture 中公开的 `test_key`
（`r1-synthetic-test-key-not-a-production-secret`），不是 owner key。结果却把
`owner_signed` 置 `true`，并把 `s2_verdict`/`s2_status` 设成 S2 kernel 的真实
`SETTLE_APPLIED`。一个上层消费者若只读 `owner_signed` + `settle_allowed` +
`s2_verdict`（最自然的三字段），就会把测试 harness 的结算当成生产结算；
`production_eligible=false` 和 `mode=synthetic` 是 qualifier，但结果对象本身
不强制消费者读它们。当前没有生产 import，所以不是 P0；但这是 contract
hazard，应在 wiring 前修掉。

**最小负测。** 一个 consumer contract test：只允许
`mode=="production" && production_eligible==true && signature_authority` 为
production authority 时才读 `s2_verdict`；断言 synthetic 结果永远不能被
feed 给 S2 kernel/store。

**最小修复。**

- synthetic 分支不要复用生产词：`s2_verdict="SYNTHETIC_ONLY_APPLIED"`，
  `s2_status="SYNTHETIC_ONLY"`；
- `owner_signed=False`（或改名 `test_signature_valid`）；
- `settle_allowed` 只在显式 `synthetic_authority` 对象/`synthetic_only=True`
  下为真，且该对象不能从请求数据构造；
- 或在 `_result` 中把 `production_eligible` 作为唯一可消费的 verdict 门，
  并把 `s2_verdict` 在 synthetic 下设为 `None`。

### 4.2 P1-2：空/缺失 `observation_id` 绕过 replay

**精确代码** `reconcile_source_gate.py:327-335`：

```python
observation_id = str(observation.get("observation_id") or "").strip()
if observation_id and observation_id in set(applied_observation_ids or ()):
    return _result(decision=REFUSED_REPLAY, ...)
```

`observation_id` 为空或缺失时，整个 replay 分支被跳过；签名的 APPLIED 继续
走到 `settle_allowed=True`。`observation_id` 在 `_SIGNED_FIELDS` 中，所以空
id 也会被签名覆盖并验证通过。

**实测（`logs/r1-findings.log`）：**

```text
f1-replay-with-empty-observation-id
  observation_id=""
  applied_observation_ids=("",)
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}
  expected=REFUSED_REPLAY

f1-replay-with-absent-observation-id
  observation_id=None
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}
  expected=REFUSED_REPLAY
```

非空 id 的同组控制正确返回 `REFUSED_REPLAY`。下游 S2 kernel
`reconcile_observation` 不检查 `observation_id`；因此这个空 id 不会被 kernel
补拦，store 的 applied 集合是唯一防线，而 gate 主动跳过了它。

**最小负测。** `observation_id in ("", None)` 的签名 APPLIED；断言
`REFUSED_REPLAY` 或新的 `REFUSED_MISSING_OBSERVATION_ID`。

**最小修复。** 在 APPLIED 分支之前要求：

```python
if not _text(observation_id):
    return _result(decision=REFUSED_MISSING_OBSERVATION_ID,
                   s2_verdict=S2_REFUSE_UNKNOWN, ...)
```

并把该 refusal 加入契约表/测试。

### 4.3 P2-1：signed `source` 未与 source capability 绑定

`_SIGNED_FIELDS`（`98-109`）包含 `source`，签名也覆盖它；但 `evaluate` 只在
`182-191` 检查 source 对象的 `present` 与 `capability`，**从不读**
`observation["source"]`。实测：

```text
f2-signed-source-not-bound
  observation.source="forged:source"
  source_capability="CLIENT_TURN_ID"
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}
```

S2 kernel 对 `source` 的唯一检查是 `str(observation.get("source") or "").strip()`
非空（`dispatch_attempts.py:421-426`），因此 `forged:source` 会穿过 gate 和
kernel。**修复：** 要求 signed `source` 与 source 对象声明的
provenance/capability 一致（例如 `codex:thread/turns/list` 对应
`CLIENT_TURN_ID`），不一致返回 `REFUSED_SOURCE_MISMATCH`；负测用
`forged:source` 和空 source。

### 4.4 P2-2：signed `observed_at` 未解析/未校验 freshness

`observed_at` 在 `_SIGNED_FIELDS` 中，但没有 `_parse`、没有 `now` 比较、
没有 max-age。实测：

```text
f3-observed-at-not-validated
  observed_at="not-a-time"
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}
```

S2 kernel 也不检查 `observed_at`。**修复：** 给 `evaluate` 增加 `now`（或由
调用方提供），要求 `observed_at` 是可读 zoned instant 且
`0 <= now - observed_at <= max_age`；或者明确把 freshness 归 store，并在
store 侧加一个负测。不要留下“签名覆盖了但没人读”的字段。

### 4.5 P2-3：source 声明只信 `capability`，不交叉检查

`182-191` 只看 `source["present"]` 和
`source["capability"] == "CLIENT_TURN_ID"`；`adapter`、`verb`、
`thread_known` 都不读。实测：

```text
f6-source-declaration-not-cross-checked-unknown-thread
  thread_known=false
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}

f6-source-declaration-not-cross-checked-cli-adapter-declares-turn-id
  adapter="codex-cli", verb="any", capability="CLIENT_TURN_ID"
  actual={"decision": "SYNTHETIC_ACCEPTED_APPLIED", "settle_allowed": true}
```

而 PR 自己的 `capability_matrix()` 说：Codex app-server `create`（thread
未知）是 `RESPONSE_ID_ONLY`，Codex CLI 是 `NO_SOURCE`。gate 允许矛盾声明，
说明“source capability”只是调用方自报，不是可核对的 source 事实。
**修复：** 对已知 adapter/verb 建立 `(adapter, verb, thread_known) ->
capability` 的交叉检查，矛盾即 `BLOCKED_PROVIDER_SOURCE`；或要求 source
对象由 Core 构造并带不可伪造的 capability 证据。

### 4.6 P2-4：signed payload 不含 writer fence

`_SIGNED_FIELDS` 没有 `writer.owner`/`writer.epoch`/`writer.token`；
`302-325` 的 writer 检查读的是 `attempt` 侧字段，不是签名内容。实测：把
`attempt.writer.epoch` 和 `attempt.current_writer.epoch` 同时从 7 改成 8，
observation 不变、签名仍有效，gate 仍返回
`SYNTHETIC_ACCEPTED_APPLIED`；只有 `applied_observation_ids` 含该非空 id
时才被 `REFUSED_REPLAY` 拦住。结合 P1-2（id 可为空），跨 epoch 的旧
observation 可以 settle 新 writer 的同 intent。

**修复：** 把 writer fence（或它的 digest）加入 `_SIGNED_FIELDS`，或在
observation 中增加 `writer_epoch`/`writer_token` 并参与匹配；负测：epoch
7→8、同一 observation、applied set 为空，必须拒绝。注意这需要与 S2 kernel
的 `_same_writer` 语义对齐，不能只改 gate。

### 4.7 R1 正向控制（PASS）

`logs/r1-controls.log`，22/22，rc 0：

- fixture 的 18 个 case 全部符合作者期望；
- production 模式即使有合法 synthetic 签名也
  `BLOCKED_OWNER_SIGNATURE`、`settle_allowed=false`、`owner_signed=false`；
- 无 source / `RESPONSE_ID_ONLY` / unknown capability →
  `BLOCKED_PROVIDER_SOURCE`；
- unsigned / bad signature → `BLOCKED_OWNER_SIGNATURE`；
- cross-owner/device/project/parent、changed digest、forged locator、
  stale writer、NOT_APPLIED、UNKNOWN、非空 id replay 全部按契约拒绝；
- `module-is-pure`：只 import `hashlib/hmac/json`，无 socket/subprocess/
  requests/os.environ/open/clock；
- 生产 import 扫描：`runtime/`、`mcp/`、`fleet_mcp/`、`workengine/`、
  `intake/` 及 `integrations/` 其他目录均未 import 该 gate；
- 所有结果 `send_allowed=false`、`retry_allowed=false`、
  `production_eligible=false`、`production_decision=BLOCKED_PROVIDER_SOURCE`。

### 4.8 R1 BLOCKED / NOT_RUN

- 真实 Codex app-server `clientUserMessageId` live echo：**NOT_RUN**；没有
  真实 send、没有 token/transcript、没有付费请求。
- 真实 owner-signed observation：**ABSENT**；synthetic HMAC 不是 authority。
- 下游 S2 kernel 集成：**SOURCE_ONLY**；#328 `cf32af8` 不在 #356 base 中，
  gate 未 wiring；只静态读了 `reconcile_observation` 的 phase/principal/
  writer/intent/source 非空/state 检查。
- 真实 production settle：**BLOCKED_PROVIDER_SOURCE**；没有 NOT_APPLIED、
  retry 或 exactly-once 声明。

---

## 5. 证据与 rc

| Gate | 命令/文件 | rc | 结果 |
|---|---|---|---|
| S1 author tests | `tests/test_owner_policy_binding.py`、`test_owner_policy_reduced_gate.py`、`test_v1_grant_epoch_authority.py`、`test_v1_grant_os_principal_schema.py`、`test_fleet_owner_grant*.py` | 0 | **86 passed, 1 skipped, 23 subtests**（`logs/s1-author-tests.log`） |
| S1 controls | `probes/s1_probe.py --mode controls` | 0 | **36/36 PASS**（`logs/s1-controls.log`） |
| S1 signed | `probes/s1_signed_probe.py` | 0 | **10/10 PASS**（`logs/s1-signed-probe.log`） |
| S1 findings | `probes/s1_probe.py --mode findings` | **1（预期）** | **6/6 gaps confirmed**（`logs/s1-findings.log`） |
| R1 author | `tests/test_fleet_v1_runtime_reconcile_source.py` | 0 | **35 passed**（`logs/r1-author-tests.log`） |
| R1 controls | `probes/r1_probe.py --mode controls` | 0 | **22/22 PASS**（`logs/r1-controls.log`） |
| R1 findings | `probes/r1_probe.py --mode findings` | **1（预期）** | **8/8 gaps confirmed**（`logs/r1-findings.log`） |

`findings` 的 rc=1 是探针**成功检测到缺口**的预期结果；它不是作者测试套件
的失败。控制组 rc=0 表示 PR 声称的 fail-closed 行为在本范围内成立。

---

## 6. 复现

```sh
cd integrations/fleet-v1-qa/qa2

# 1) 从确切 PR refs 导出快照（不进入作者 worktree）
./qa2-run.sh export

# 2) 作者测试 + QA 控制组 + 发现组
./qa2-run.sh all

# 或者只跑一侧
./qa2-run.sh s1
./qa2-run.sh r1
```

`qa2-run.sh` 使用 `git archive` 把
`b183aab0b8913ed36e5de539c50223d3eaf508f1` 与
`b4020b73e6b7ec2b1f6301471a9872f8864c1643` 导出到
`${QA2_WORKDIR:-/tmp/qa2-independent-security}`，运行前后校验 SHA；若对象
不在本地，则 `git fetch origin pull/358/head` / `pull/356/head` 并再次校验。
所有 pytest/probe 命令都带
`MemoryMax=1G / MemorySwapMax=0 / CPUQuota=25% / nice 10 / timeout 120`
（`systemd-run --user --scope`，不可用时退回 `nice + timeout`）。

---

## 7. 结论与建议顺序

1. **S1 before binding any owner resolver：** 修 P1-1（revoked 类型）、P1-2
   （commit seam 或明确 BLOCKED_OWNER_POLICY）、P2-1/2/3；把本报告的 36 个
   control + 6 个 finding 负测加入 PR 测试。
2. **R1 before wiring the gate：** 修 P1-1（synthetic 词表）、P1-2（空 id
   replay）、P2-1..4；加一个 consumer contract test，确保 synthetic 结果
   不能被 feed 给 S2 store/kernel。
3. **保持生产 fail-closed**，直到真实 owner-issued resolver、两真实 UID、
   真实 Provider live echo、owner-signed observation 和原子 commit seam
   都存在并有证据。

**最终：`QA_PARTIAL` / `NOT_RELEASE`。** 无 P0；P1 是 latent/contract 缺口，
不是当前生产可利用漏洞；但两 PR 都不能在修复前作为“owner-verified lease”
或“production effect proof”的依据。
