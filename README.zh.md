<!--
TRANSLATION. This file is the Chinese translation of `README.md`. The English
file is canonical: where the two differ, the English one is right, and a change
to the product lands in `README.md` first.

It is a translation and not a second statement. `ARCHITECTURE.md` is the single
canonical architecture statement (invariant 1: other documents link, never
restate), and that rule applies to a translation of a document too — this file
links to the same places `README.md` links to, and states nothing of its own.
-->

# ServerOS

ServerOS 是一个轻量的 **user-space Agent Operating Fabric**，面向 Claude Code、Codex
以及未来的编码 Agent —— 一个构建在它之上的 Agent Engineering Workbench。

完整定义在 [`ARCHITECTURE.md`](ARCHITECTURE.md)，**只陈述一次**。早先的措辞
「Agent control plane」不算错 —— control plane 如今恰好是四个平面之一的
Node Runtime Plane。

它的职责不是规定 Agent 怎么干活。

它的职责是把三样东西交到 Agent 手里：

- **Environment**（环境）
- **Context**（上下文）
- **Capabilities**（能力）

ServerOS 优化的是**一条很小的 happy path**，以及**渐进的、按需展开**。

> 给 Agent 开始工作所需的最小正确上下文，
> 之后让 Agent 在需要时自己去发现并加载更多能力。

ServerOS 是一张能力地图加一个工作台，**不是工作流状态机**。

---

## 1. 为什么需要 ServerOS

一个编码 Agent 被丢到共享服务器上，是从零开始的：它不知道哪个目录是 canonical、
它的产出该放哪、哪些相邻资源属于别人、交付目标是什么。常见的解法 —— 一段巨大的
onboarding prompt、一次全盘扫描、给每个 Agent 一个私有工作区 —— 都以同一种方式失败：
它们让 Agent 的**第一分钟**变慢，并且让每个 Agent 各自发明一套世界。

ServerOS 存在的目的，是让**任何** Agent、在**任何** runtime 上，都能被交付一份
小而正确、限定在任务范围内的上下文，然后开始工作 —— 不需要有人重新解释一遍项目。

## 2. Environment / Context / Capabilities

**Environment** —— 准备好该干活的地方：canonical 源码仓、Item worktree、测试环境、
工具链、端口与 runtime 资源、证据位置、交付目标。

> 我在哪工作、产出放哪、怎么避免碰到别人的资源？

**Context** —— 交给 Agent 一份小而准确、与任务相关的 *Context Capsule*：
project、当前 Item、目标与验收、当前 milestone、开发仓、归属的 worktree、
test-env、证据路径、相关来源 locator、交付目标、受保护的并行资源。

> 我是谁、我在做什么、已经知道什么、什么不能碰？

**Capabilities** —— 按需提供当前任务需要的东西：Skills、`serverosctl`、飞书、
GitHub、测试工具、工作区工具、信息工具。

> 这个任务需要哪些可靠的能力？

ServerOS **刻意不**决定 Agent 的内部方法 —— 先 grep 还是先读文档、先写测试还是先做实验、
要不要用子代理、按什么顺序分析 / 设计 / 构建 / 验证。那些留在 Claude Code 和 Codex 手里。

见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 3. 架构

→ **[`ARCHITECTURE.md`](ARCHITECTURE.md) 是唯一的架构陈述。** 本 README 不重述它；
一句「也正确」的第二陈述，正是那份文档存在的意义所要防的漂移。

一句话：ServerOS 是一个 user-space Agent Operating Fabric —— 一个小型节点运行时
（`serverosd`）、语义服务、面向 Agent 的 MCP、一个 bootstrap Skill、按需解析的
Ability 驱动流程、插件组合，以及一个可选的 Fleet。可用性是**按设备声明的**，
不是产品级的常开承诺。四个平面是 Node Runtime Plane、Ability Fabric、
Memory-Context Fabric 和 Coordination Fabric。

组件边界同时有机器可读版本：
[`integration/components.yaml`](integration/components.yaml)。

## 4. Context Capsule

Context Capsule 是 ServerOS 与任何 Agent 之间的契约。它是一个小而稳定的 schema ——
不是地图、历史或 Skill 文档的倾倒场。

→ [`docs/context-capsule.md`](docs/context-capsule.md)

## 4b. Space Resolver

Capsule 回答*「我的工作环境是什么」*，Space Resolver 回答*「这个对象在 canonical 意义上
属于哪里、需不需要移动」*。它是一个确定性的只读内核：不读网络、不读数据库、不读地图，
也不发明任何云端路径。真正执行它的判定 —— 实际的 MOVE、实际的索引写入 —— 是 adapter 的事。

→ [`docs/space-resolver.md`](docs/space-resolver.md)

## 4c. Fleet（可选）

Fleet 是**按设备可选的**。一台声明不参与 fleet 的设备是一个正常的 ServerOS 节点，
只是没有拓扑邻居，不是坏掉的节点。

设备参与时，ServerOS 拥有稳定的设备身份与 fleet 拓扑。设备连接 locator 与凭据只留在本地；
每台设备上装了什么能力由 `ability-management` 的观测拥有，并投影回 Fleet 视图。

→ [`fleet/README.md`](fleet/README.md)

## 5. Native-first 的 Skill 发现

ServerOS **强化**每个 runtime 自己的原生 Skill 发现。它从不替代它，也从不收窄它。

→ [`docs/capability-discovery.md`](docs/capability-discovery.md)

## 6. Claude / Codex 对等

> 同样的能力、同样的语义、同样的上下文；只换 runtime adapter。

→ [`docs/claude-codex-parity.md`](docs/claude-codex-parity.md)

## 7. 工作区布局

```text
HOME=/home/hwz
DATA_ROOT=/opt/applications/data/home-hwz
WORKSPACE_ROOT=$DATA_ROOT/workspace
ARTIFACTS_ROOT=$DATA_ROOT/artifacts
STATE_ROOT=$DATA_ROOT/state/serveros
```

- `WORKSPACE_ROOT` 只承载 `src/ worktrees/ test-envs/ ops/ knowledge/ records/`。
- `src/` = canonical Git 仓库。
- `worktrees/<repo>/<ITEM>/` = 按 Item 的开发检出。
- `test-envs/<repo>/<ITEM>/` = 按 Item 的测试环境定义。
- 证据位于 `DATA_ROOT` 下，**从不**放在 `WORKSPACE_ROOT` 下。

## 8. 地图

`HWZ-MAP`（结构）· `TREE-MAP`（语义）· `FEISHU-MAP` · `GITHUB-MAP`。

地图是**生成的投影视图**，不是独立的权威。它们的输入是机器契约 / 语义注册表 / 观测到的清单；
投影编译器从这些输入渲染出给人看的地图区域。普通会话不会整份加载地图：
Context Capsule 只投影出与当前任务相关的那一小片。

## 9. 安全与并发

这台服务器由并发的 Agent 与 Item 共享。

→ [`docs/safety-and-concurrency.md`](docs/safety-and-concurrency.md)

## 10. 仓库与组件边界

| 仓库 | 角色 |
|---|---|
| `ProfessorZhi/serveros` | 产品入口；**composition canonical**（`plugin/bundle.yaml` = `serveros-core`，`plugin/profiles/*.yaml` = 通过 `extends:` 扩展它的更宽集合）；bootstrap Skill；Context/Space/Relations 内核；确定性构建 |
| `ProfessorZhi/ability-management` | Capability/Ability 注册表；**可复用 Skill 的 canonical 源**；revision/compatibility/discovery/reconcile/installation/distribution |
| `ProfessorZhi/work-engine` | `serverosctl` 确定性控制平面，管 Item/Activity/Run 状态与投影 |
| `ProfessorZhi/hwz-ops` | 本机的拓扑/策略实例与地图投影目标 |

`ability-management/plugins/hwz-server-os` 只是一个过渡性的、生成出来的兼容面。
它不是第二份插件 canonical。

仓库之间的*关系*不等于工作区的*归属* —— 见
[`docs/repository-roles.md`](docs/repository-roles.md)。

## 11. 非目标

那份封闭清单在 [`ARCHITECTURE.md`](ARCHITECTURE.md) 的
**NOT SERVEROS RESPONSIBILITY** 之下，此处不重述。它存在的目的是让这些东西不要长回来。

在加任何东西之前，先问：**它改善了 Environment、Context 还是 Capabilities ——
以及去掉它，哪个真实的失败案例会失败？** 两个问题都没有答案，它就不是 ServerOS。
