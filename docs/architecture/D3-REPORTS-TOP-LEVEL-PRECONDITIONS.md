# D3 的下一安全切片：顶层 `reports/` 的前置条件

2026-10-28。本文是 [D3 工单](../plans/fleet-v1-gap-and-work-packages.md)（product 部分迁出 `runtime/`）在 **stage 1 已落地之后**的消费者普查与严格规范。它不授权任何搬迁，也不替代 §24 的判定；它把「下一步能安全做什么」写成可复现的实测，而不是一张更大的清单。

**结论先行**：顶层 `reports/` **现在不能做**，而且原因不是门禁文案、不是层表登记，是 `runtime/daemon.py` 在**模块级**加载 product 模块 —— 一旦 product 迁到顶层，core 的**启动**就依赖 product，这是 §24 禁止的倒置。工单给的验收口径「`runtime/` 的 L1 模块不加载 `reports/`」今天也**无法实现**，因为 `layers.yaml` 里的 L1 集合是散文，`runtime/` 下 88 个模块里 34 个不匹配其中任何 token。

---

## 1. 消费者普查（实测，非推断）

测量对象：`a2112ff`（D3 车道 tip，stage 1 = `runtime/reports/business_calendar.py` + 旧路径 facade）。

| 读数 | 值 |
|---|---|
| `runtime/reports/business_calendar.py` ∈ `closure(runtime/daemon.py)` | **是**（闭包 45 个文件） |
| ∈ `closure(runtime/cli.py)` | **是**（110） |
| ∈ `production_closure()` | **是**（123） |
| 闭包内**真正按路径解析到它**的文件 | **2 个**：`runtime/card_day.py:140`、`runtime/card_source.py:100` |
| 其余 4 个加载点（`card_period`、`card_scenarios`、`card_surface_runner`、`surface_cli`） | 都在**任何闭包之外** |
| 旧路径 facade `runtime/business_calendar.py` | 在**任何闭包之外**（现在只有测试加载它） |

两条细节，都会让「按文件名搜一遍就搬」的做法出错：

1. **两种拼法，解析基准相同**（`_HERE / filename`）。`card_day`、`card_period`、`card_surface_runner`、`surface_cli` 写 `_HERE / "reports" / "business_calendar.py"`；`card_source`、`card_scenarios` 写裸串 `"reports/business_calendar.py"`。单模式搜索会漏掉后 2 处。
2. **加载时机不同**：`card_day:140` 在函数 `_calendar()` 内（惰性），`card_source:100` 在模块级（`business_calendar = _load(...)`）。但 `cli.py:5883` 通过函数里的 `_load_module_from` 才走到 `card_source`，所以**两个入口在启动时都不加载日历**。§24 的扫描器（AST）看不见这个区别 —— 它自己的文件头就写明了这一点。

## 2. 顶层搬迁实测：naive move 会红什么

在只读工作树里实际执行（`git mv` 到顶层 `reports/`、改 7 处加载点 + facade 的 `IMPLEMENTATION`），跑 3 个有意见的门禁，然后逐字节还原：

```
10 failed, 35 passed, 2 errors, 35 subtests passed
```

| 红 | 归属 |
|---|---|
| `test_core_ability_boundary.py::...::test_the_core_process_reaches_only_core_directories` | **§24 白名单**：唯一 offender 是 `reports/business_calendar.py`（`_tree()` = `reports` ∉ `CORE_DIRS`） |
| `test_repository_layers.py::...::test_every_tracked_top_level_directory_is_registered` | `reports/` 未登记（层表那一行） |
| `test_reports_layer_migration.py` × 7 | stage 1 门禁自己的**布局断言**（它就是为 `runtime/reports/` 写的） |
| 其余 3 条（1 failed + 2 errors） | **基线噪声**：本机 Windows 无 `fcntl`，导入 `runtime/daemon.py` 即失败；干净树上同样红 |

⇒ 可归因于搬迁的是 **9 条**，其中只有 1 条是真正的架构判定。

**反向发现**：`FABRIC_DIRS` 黑名单（`test_the_core_process_loads_nothing_it_governs`）**没有响** —— `reports` 不在 `FABRIC_DIRS` 里。也就是说 §24 的两张表对 `reports/` 一个响一个不响：白名单（core 出界）会响，黑名单（core 加载受管系统）视而不见。这不是 bug，是两张表回答不同问题；但它决定了 D3 的新规则**只能**长成白名单形态。

还有一处字面量已经在等这一天：`test_a_new_top_level_directory_is_still_a_failure` 的第一个反例就是 `reports/business_calendar.py`；而 `test_a_file_nested_under_a_core_directory_is_part_of_it` 拿 `runtime/reports/business_calendar.py` 当「分阶段搬迁」的样板。**「嵌套合法、顶层是一次决策」这件事，树里已经写好了。**

## 3. 真正的拦路石：daemon 的模块级 product 加载

按 `layers.yaml#directories.runtime.parts` 把 `runtime/*.py` 分类（散文 → glob），再看 `runtime/daemon.py` 的每一条出边**是在模块体里发生，还是在函数里**：

| `daemon.py` 行 | 目标 | 分片 | 时机 |
|---|---|---|---|
| 451 | `agent_sessions.py` | L0 | **module** |
| 462 | `agent_control_store.py` | L0 | **module** |
| 468 | `owner_policy.py` | L0 | **module** |
| 474 | `alerting.py` | *散文未分类，职责属 product（告警状态机）* | **module** |
| 480 | `alert_digest.py` | product | **module** |
| 485 | `card_delivery.py` | product | **module** |
| 493 | `approval.py` | L0 | **module** |
| 497 | `coordination.py` | L0 | **module** |
| 500 | `dispatch_attempts.py` | L0 | **module** |

daemon 的闭包里还有 `presentation`（← `alert_digest:183` 惰性）、`card_day`（← `presentation:85` 惰性）、`reports/business_calendar.py`（← `card_day:140` 惰性）—— 这三条都是惰性的，**不是**启动依赖。

所以准确的说法是：**core 的启动依赖里有 3 个 product 模块**（`alerting`、`alert_digest`、`card_delivery`），另有 6 个 L0（D4 的范围）。一旦 `reports/` 是顶层目录，`runtime/daemon.py` 的模块体就要打开 `reports/` 下的文件才能继续导入。

这与 `KNOWN_CORE_CROSSINGS` 表自己的准入门槛直接冲突，其注释写的是：

> An entry can therefore only ever name something the core does NOT need in order to start.

⇒ **这不是一个可以登记的例外，是一个必须先拆掉的依赖。**

拆分是有界的：三条语句（`:474`/`:480`/`:485`）改成惰性访问器，照 `runtime/presentation.py:76-85` 的 `_card_day()` 写法；使用点分别是 `alerting_mod` 13 处、`digest_mod` 9 处、`card_delivery_mod` 8 处。**`runtime/daemon.py` 在 #407 的在飞路径内**，所以这是 owner handoff，不是本车道能改的。

## 4. 验收口径今天无法实现

工单写的是「`runtime/` 的 **L1 模块**不加载 `reports/`」。要测这条，得先有一个可机器读的 L1 集合，而 `layers.yaml` 给的是散文：

- `runtime.parts.L1` = `节点守护与自愈、监控（daemon, health, probes, … serverosd.service 等）` —— 18 个 token，其中一个是 systemd 单元名。
- 按这些 token 分类 `runtime/*.py`：**54/88 命中，34 个不命中**。不命中的里面既有显而易见的 L1（`registry`、`view`、`human_console`、`hygiene`、`ability_projection`），也有显而易见的 product（`alerting` —— 产品 token 是 `alert_*`，匹配不到它），还有 `cli`、`surface_cli`、`monitoring_policy`、`message_transport`、`wake`。
- `tests/test_repository_layers.py::test_a_split_directory_says_what_each_part_is` 只断言「split 目录至少两个 part」，**没有任何门禁读 parts 的内容**。

⇒ 按现口径写规则，`alerting.py`（告警状态机，daemon 的启动依赖）会被算作「未分类」而**免检**。这正是这条规则要防的东西。

另外，`tests/test_repository_layers.py#TheLayerRulesHold` 现有三条规则都是**文本正则**（`fleet_mcp/**/*.py` 里搜 `runtime`、`mcp`），而 `_load(_HERE / "reports" / "…")` 这种运行期拼法对正则不友好；§24 用的是 AST 闭包。新规则要能看见 D3 的边，必须走 §24 那条路（`tests/loader_graph.py` 的闭包），不是正则那条。

最后，`layers.yaml#rules` 的 id 集合是被**精确断言**的（`test_the_rules_tested_here_are_the_rules_declared` 用 `assertEqual` 比集合），加一条规则要同时改 `layers.yaml`（#414 面）和这个测试（层守卫面）—— 两个 owner，两个 PR。

## 5. 严格规范：下一安全切片

| 选项 | 内容 | 代价 | 判定 |
|---|---|---|---|
| **A（推荐）** | 继续**嵌套**在 `runtime/reports/`，把「不在任何闭包内」的 product 模块并进来 | 零门禁代价：`runtime/` 是 `CORE_MODULES` 成员、递归复制；`_tree()` 仍是 `runtime` ⇒ §24 不响 | 可做 |
| **B** | 顶层 `reports/` | 需同时在场：目录 + `layers.yaml` 一行 + `CORE_MODULES` 一行 + `bundle.yaml` 的 `path:`（D2 的会合态教训：两两互斥，拆开合必红）**再加** §24 白名单 1 条、`test_a_new_top_level_directory_is_still_a_failure` 的字面量、stage-1 门禁重写 ×7 | **阻塞**：先做 §3 的拆分 |
| **C** | 单一最小受控例外 | `KNOWN_CORE_CROSSINGS` 的 cap 是 `assertEqual(len, 1)`，且门槛是「为什么没有别的进程能回答」。日历是纯函数、任何进程都能算 ⇒ **给不出这个 `because`** | 不满足 |

⇒ **D3 的下一安全切片不是路径搬迁**，是两件更小的事：

1. **owner handoff（`runtime/daemon.py` → #407）**：把 `alerting_mod`/`digest_mod`/`card_delivery_mod` 三条模块级加载改成惰性访问器。**只动这 3 条语句与其使用点，不动逻辑**（与 D4「只搬不改逻辑」同一纪律）。这件事不需要任何层表或打包改动，做完之后「core 启动不依赖 product」才是真的。
2. **层表可机器读（`layers.yaml` → #414）**：给 `runtime.parts` 一个可判定的形态（例如 `parts.<name>.modules: [glob…]`），并让 `test_a_split_directory_says_what_each_part_is` 至少校验 glob 能覆盖 `runtime/*.py`。没有这一步，D3 的验收规则写不出来；有了它，规则可以直接照 §24 的白名单形态写：**`runtime/` 的 L1 模块闭包不得含 `reports/`**。

在这两件之前，能安全推进的只有 A：把 `cards*`、`card_*`、`daily_report`、`report_*`、`alert_*`、`notifications`、`direct_lark_notify`、`live_sender`、`workbench*`、`presentation`、`business_calendar` 中**不在任何闭包内**的那些并进 `runtime/reports/`（本分支已做 `business_calendar` 一个），为将来一次性翻转准备一个**已经成形的目录**，而不是 26 次散搬。

## 6. 复现

两个脚本都在仓库外，都是「改 → 测 → 还原」，结束时断言工作树逐字节复原（`git status --porcelain` 为空）：

- `measure-d3-l1-edges.py` —— 解析 `layers.yaml#runtime.parts` 散文 → glob，分类 `runtime/*.py`，再用 AST 列出两个入口闭包内每条进入非 L1 模块的边及其时机（`module` / `lazy`）。产出 §3 的两张表。
- `measure-d3-topmove.py` —— (A) 用 AST 给出「谁按路径解析到实现、在哪个时机」；(B) 真的把文件搬到顶层 `reports/`、改两种拼法的 7 处加载点 + facade，跑 3 个门禁，然后 `git mv` 回来并 `git checkout` 还原。产出 §2 的红表；**基线 3 条 Windows-only `fcntl` 红必须先减掉再读**。

§3 的「启动时不加载」是**静态**结论（AST 时机）。动态那一半（`AStartedCoreHasLoadedNoFabric` 那种包住 `spec_from_file_location` 的探测）在本机 Windows 跑不了 —— 导入 `runtime/daemon.py` 第一件事就是 `import fcntl`（`runtime/daemon.py:53`）。它在 Linux CI 上可跑，也是搬迁时必须加的那条证据。
