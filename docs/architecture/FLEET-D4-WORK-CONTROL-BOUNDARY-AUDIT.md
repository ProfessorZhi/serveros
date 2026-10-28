# D4 Work Control 边界审计（只读）

D4 把 10 个 work-control 模块搬进 `workengine/agent_control/`。本文件是**只读**结论：
没有文件被移动，本文与配套的契约测试只做两件事 —— 把边界现在的承诺写下来，
并让它在搬迁后依然成立。真正的搬迁不在这里，原因见第 4 节。

- 快照：`main @ e69fc0337ae5d33000478e3d5162077f525bc8cc`（2026-10-28）
- 配套测试：`tests/test_work_control_fence_contract.py`（20 条，全绿）
- 数字来源：`docs/architecture/RUNTIME-CUSTODY-MATRIX.md`（#438 的普查）

## 一、五条结论

1. **一个词，三个计数器。** 这条边界上 `epoch` 同时指三样东西 —— 所有者策略代次、
   grant 计数器、写入者围栏代次。三者各有各的拒绝词，谁也不能替换谁（第 2 节）。
   把它们收进同一个叫 `agent_control` 的目录，风险不是重名，而是它们**看起来可互换**。

2. **两个 D4 模块在 #407 手里**：`agent_control_store`（68 个消费文件，全树第四大）
   与 `owner_policy`（8 个）。两者都在 #407 的 85 条路径里，搬不了。

3. **剩下的 8 个即使「无主」也搬不了**，因为目的地 `workengine/` 整目录正在被 #407 改写：
   归属快照里 **13 条 `workengine/` 路径属于 #407**，其中
   `workengine/owner_fence.py`、`owner_client.py`、`owner_context.py`、`migration_lock.py`
   在 `main` 上**都还不存在** —— #407 正在**新建**一个所有者侧的围栏。

4. **这正是本次审计最重要的发现**：D4 的目的地正在长出一个**第二套围栏**。
   `runtime/coordination.py` 已经有一套写入者围栏（owner/epoch/token），
   #407 正在 `workengine/` 里写另一套。两套围栏各自成立、各自有测试，
   但它们之间的关系**没有任何一处写下来**。在两个分支都看不见对方的前提下把它们并到
   一个目录里，问题不是冲突，而是**事后没人说得清哪个是权威**。
   ⇒ D4 的搬迁必须等 #407 落地，并且要先有一份「两套围栏的分工」声明。

5. **`wake` 已经跨两个顶层目录**：它加载 `context/delegation.py` 与
   `intake/daily_cycle.py`。D4 说「搬进 `workengine/agent_control/`」，
   但对这两个模块来说，搬家会把 `runtime/` 内的相对路径换成跨顶层路径 ——
   这类改动在源码树上永远绿，在安装产物里才断（C4）。

## 二、三个 `epoch` 各自是什么

| # | 字段 | 家 | 产生方式 | 消费/拒绝 | 变了意味着 |
|---|---|---|---|---|---|
| 1 | `OwnerPolicy.epoch` | `runtime/owner_policy.py` | 所有者签发的策略文档 | grant 里存 `owner_policy_epoch`；不等 ⇒ `BLOCKED_OWNER_POLICY`「the grant's binding is stale」 | **所有权换手**：旧 grant 一律作废 |
| 2 | `grant_epoch` | `runtime/agent_control_store.py` | 只有 `_mutation()` 内部能推进的持久计数器 | 新 grant 取「+1」 | **一条 grant 的一生**：撤销不能让后一条复号 |
| 3 | 写入者 `epoch` / `token` | `runtime/coordination.py` | `mint_writer()`：epoch 持稳、token 越过已发出的最大值 | `writer_axis()` 判 `CURRENT / STALE / EXPIRED / UNKNOWN` | **写入权限换手**：epoch 动＝换人，token 动＝同一人重取 |

三者的关系是「都叫 epoch，且都必须互不替换」，而它们**没有任何一处交叉引用**：
`coordination.py` 不加载任何模块（纯内核，`module_refs` 为空），
所以它**结构上不可能**读到所有者策略。这条性质是本文能说「不会混」的唯一根据，
也是搬迁最可能弄丢的东西 —— 因为目的地 `workengine/agent_control/` 正是策略住的地方。

契约测试 `TestThreeEpochsAreThreeCounters` 把这五件事各自钉住，并带阳性对照：
把 `owner_policy.py` 的 epoch 比较改成 `if False:`，四条里立刻红一条（已实测，见第 6 节）。

## 三、lease 面在哪（不在 D4 的 10 个里）

容易误判的一点：名字里有 lease 的文件**不属于 D4**。

| 面 | 位置 | 与 D4 的关系 |
|---|---|---|
| 资源租约存储 | `runtime/lease_store.py`（`LeaseStore.declare/release`） | 不在 D4 清单里 |
| 环境释放裁决 | `runtime/environment.py`（`release_verdict`、scope/sharing 词表） | 不在 D4 清单里 |
| 租约授权校验 | `runtime/daemon.py#_r_agent_lease_authority_verify` | **#407 持有** |
| 租约 CLI | `runtime/cli.py` 的 `lease declare\|release\|list\|inspect\|environment` | D10 的对象 |

D4 真正碰到 lease 的地方是 `agent_control_store` 的 grant 签发/校验 ——
它读的是**所有者策略的那个 epoch**（第 2 节 #1），不是 `lease_store` 的过期时间。

## 四、10 个模块的搬迁成本（按依赖形状与归属）

`消费` = 引用该文件的文件数（已跟踪，含 tests/docs/scripts/ci）；
`加载` = 它自己 `_load`/import 的模块；`#407` = 在 #407 的路径声明里。

| 模块 | 行数 | 消费 | 加载 | #407 | 今天能搬？ |
|---|---:|---:|---|---|---|
| `agent_control_store` | 3100 | 68 | `actor`（context/） | 是 | 否 —— 归属 |
| `owner_policy` | 291 | 8 | — | 是 | 否 —— 归属 |
| `coordination` | 675 | 33 | — | 否 | 否 —— 目的地被占，且与 #407 的新围栏撞责 |
| `wake` | 1251 | 24 | delegation、daily_cycle、agent_control_store、bounded_invocation、continuation | 否 | 否 —— 跨顶层路径 + 目的地被占 |
| `dispatch_attempts` | 669 | 15 | — | 否 | 否 —— 目的地被占 |
| `agent_sessions` | 92 | 14 | — | 否 | 否 —— 目的地被占 |
| `approval` | 367 | 12 | `actor`（context/） | 否 | 否 —— 同 `wake` |
| `continuation` | 1066 | 11 | — | 否 | 否 —— 目的地被占 |
| `continuation_control` | 557 | 5 | delegation、wake | 否 | 否 —— 依 `wake` |
| `review_receipt` | 59 | 3 | daily_cycle（intake/） | 否 | 否 —— 跨顶层路径 |

「消费」是**下界**：`f-string` 拼出的路径、配置里读出的路径、反射加载在图上都没有边。

### 归属快照的一个读数陷阱

`docs/architecture/runtime-custody-claims-20261028.json` 记了 187 条路径声明，其中
**103 条在 `main` 上根本不存在** —— 它们不是「正在改的文件」，而是「这条 PR 将要新建的文件」。
所以「路径不冲突」不等于「不冲突」：D4 新建 `workengine/agent_control/coordination.py`
与 #407 新建 `workengine/owner_fence.py` 路径不冲突，职责冲突。

## 五、契约测试清单（`tests/test_work_control_fence_contract.py`，20 条）

只读：全部是纯函数与源码扫描，不开 socket、不建 store、不依赖 POSIX，Windows 上可跑。

| 类 | 钉住什么 |
|---|---|
| `TestTheWriterFenceIsOneTuple` | 轴恰好四个闭集词，且四个都**有生产者**；时间戳是定宽 `%Y-%m-%dT%H:%M:%SZ`（`isoformat()` 的反例作为对照）；`True` 不是 epoch；没有 token 的 writer 被拒 |
| `TestTheWriterAxisFailsClosed` | 无 writer ⇒ `UNKNOWN`；无可比对对象 ⇒ `UNKNOWN`；**过期时间读不出来 ⇒ `EXPIRED` 而不是 `CURRENT`**；owner/epoch/token 任一动 ⇒ `STALE`；内核是纯的（输入不被改写、两次调用同答） |
| `TestASuccessorOutranksItsPredecessor` | 首个 claim 是 epoch 1 / token 1；token 跨 owner 推进而 epoch 持稳；另一 Item 的 token 不参与本作用域 |
| `TestTheLeaseStampIsOneFormatAcrossThreeDirectories` | `context/resolver.py`、`workengine/store.py`、`runtime/coordination.py` 三处各有同一份格式常量，全树 7 处定义必须一致；`%z` / `%f` / `+00:00` 三种写法被显式拒绝 |
| `TestThreeEpochsAreThreeCounters` | stale grant 被拒且**报文点名两个 epoch**（配一条「同 epoch 通过」的对照）；缺 epoch / 布尔 epoch ⇒ 拒绝且理由相同；`coordination.py` 结构上够不到所有者策略（`module_refs` 为空 + 源码不含该名），并配「`wake.py` 有边」「`owner_policy.py` 含该名」两条对照 |

## 六、这些测试凭什么不是空转（实测）

对每个承诺做一次变异，跑对应的测试，看它是否翻红：

| 变异 | 结果 |
|---|---|
| `WRITER_AXIS` 加第五个词 | 红 |
| `mint_writer` 的 `token + 1` 改成 `token` | 红（报文：another item's token must not set this scope's） |
| 过期时间读不出来时返回 `CURRENT` | 红 |
| `owner_policy.py` 的 `if policy.epoch != stored_epoch:` 改成 `if False:` | 红 |
| `coordination.py` 里出现 `owner_policy_epoch` | 红 |

**5/5 变异被抓到。** 第 5 条第一次是**绿的** —— 因为测试当时扫的是写死的
`RUNTIME / "coordination.py"`，而探针替换的是已加载的模块对象。改成按
`coordination.__file__` 取路径后翻红，这个改动同时让测试在搬迁后依然有效
（否则「搬家」会被迫改测试，那恰好说明它断言的是目录而不是边界）。

## 七、给 #429 / #425 的集成次序建议

1. 等 **#407** 落地（它要新建 `workengine/owner_fence.py`、`owner_client.py`、
   `owner_context.py`、`migration_lock.py`、`runtime/fleet_event_delivery.py`、
   `runtime/owner_operations.py`）。
2. #407 落地后，先写一份**两套围栏的分工声明**（哪一套是写入权威、哪一套是租约权威，
   以及 `epoch` 三义如何避免互换），再谈 D4 搬迁。
3. 在那之前，D4 唯一合法的产出就是本文与第六节的契约测试 —— 它们不含搬迁，
   也不会因为在 #407 之前合并而产生冲突。

## 八、方法与边界

- 消费数来自 `tests/loader_graph.py` 反查（与打包门禁同一扫描器），
  文本引用单独计数；两者都在 `docs/architecture/RUNTIME-CUSTODY-MATRIX.md` 里可复现。
- 归属是**快照**，且快照里 103/187 条是「尚未存在的路径」。引用前先重跑。
- 「能搬」只按**依赖形状 + 归属 + 跨顶层路径**三条判，不按行数或热度。
- 本文不下「应该怎么搬」的结论：那需要 #407 的分工声明，而它现在不存在。
