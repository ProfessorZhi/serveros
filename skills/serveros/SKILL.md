---
name: serveros
description: Use when an agent starts work on this node and needs to know where it is, which paths it may write to, whether a resource it is about to touch is safe, or which capability owns a question. Start here for context resolution, path admission and runtime status; specialized work is delegated to the owning Skill.
---

# serveros — ServerOS bootstrap

**ServerOS = 一个 user-space Agent Operating Fabric**，四个平面是 Node Runtime Plane /
Ability Fabric / Memory-Context Fabric / Coordination Fabric；它交付给 Agent 的仍然是
Environment / Context / Capabilities 三件事。完整定义只有一份：仓库根的 `ARCHITECTURE.md`；
本 skill 不复述架构。
本 skill 只做三件事：让 Agent 先确定自己在哪个
project / repo / Item / worktree / evidence 环境里、先确认要写的路径是否被契约允许、先查要碰的资源
属于谁。然后把专业工作交给对应的 owner skill。

它不是强制 workflow router。普通任务不需要从这里走一遍流程，只需要先 resolve。

## 1. 第一件事永远是 resolve context

```bash
serverosctl context current          # 这一台机器、这个 worktree 的完整 context
serverosctl device show              # 本机身份与实测资源
```

解析失败就停下来问，**不要**猜一个 Item 继续跑。`context current` 报 `item: UNKNOWN` 是正常的：
无业务 Item 的治理类工作本来就没有 Item，它需要的是你确认，不是一个编出来的编号。

## 2. 第二件事是 resolve path，不是拼字符串

不要自己发明 durable path。不要拼 `DATA_ROOT/xxx/yyyy`。路径由一个封闭的根契约决定：

```bash
serverosctl path resolve state/runtime/observed
serverosctl path admit /abs/path/you/are/about/to/write
serverosctl path contract                     # 完整契约
```

```text
DATA_ROOT
WORKSPACE_ROOT=$DATA_ROOT/workspace     # 只含 src/ worktrees/ test-envs/ ops/ knowledge/ records/
ARTIFACTS_ROOT=$DATA_ROOT/artifacts
TOOLCHAINS_ROOT=$DATA_ROOT/toolchains
```

**这五个值由节点自己回答，不要抄别人机器上的数字**（`serverosctl path contract`）。
本 skill 的旧版本把另一台机器的绝对路径印在这里，读到的人会把别的根的数值当成这里的契约。

- `artifacts/ toolchains/ envs/ runtime-assets/ caches/ data/ state/ logs/` 位于 `DATA_ROOT`，**不在** workspace。
- 禁止创建 `workspace/artifacts`、`workspace/toolchains`、`~/files` 等替代根；`admit` 会拒绝它们，
  **即使它们已经存在**——因为正确的修法是把里面的东西搬走，不是继续往里写。
- 证据路径：Item → `artifacts/work-items/<project>/<ITEM>/<RUN>/`；
  无业务 Item 的 skill 产物 → `artifacts/skills/<skill>/<RUN>/`。
- `workspace/src` 是 **read-mostly**：可以 fetch / inspect / 读 ref；正常代码改动放 worktree。
- `~/.serveros` 只放 identity 与 binding，不放报告、观测或第二份 state。
- 越界、`../`、symlink 逃逸必须**显式失败**，不得静默回落 HOME/cwd。
- `legacy/ old/ backup/ compat/` 这类近义目录名被拒绝：它们活得比任务长，然后变成结构。

`admit` 返回**决定**，不写任何东西；写文件的是调用它的那个工具。

## 3. 第三件事是查 owner

要碰任何长命资源（容器、user service、MCP server、endpoint、test runtime）之前：

```bash
serverosctl runtime status <id>      # 四轴健康 + 允许动作
serverosctl runtime explain <id>     # 为什么能动 / 不能动
serverosctl runtime hygiene          # 谁 stale、谁 protected、谁 unknown
```

**process running ≠ healthy；transport connected ≠ healthy。** 只有
`functional: OK` 才是 `HEALTHY`；`UNKNOWN` 不是软性的 healthy。

**container label 不是 owner。** 本机 8 个容器跨 3 个 compose project 共用一个
`working_dir`；把 label 当 owner 会把受保护的并行工作流算进回收桶。
ownership 只有两个来源：registry 登记，或显式 `serveros.owner=<type>:<id>`。
**未知 owner = DEFER。**

- 受保护资源（`device:server-beijing`、`work-item:LIT-0103`、`work-item:LIT-0104`）**绝对不碰**。
  保护检查跑在最前面；命中保护只意味着「别动它」，从不意味着「这是你的」。
- 名称匹配**只有一个方向**。
- 具体流程（风险等级、生命周期、什么时候 DEFER）见 `runtime-management`。

## 4. Capabilities — 交给 owner，不要自己造

| 问题 | owner |
|---|---|
| 发生过什么、目前认识到什么 | `information-management` |
| Item / Activity / Run / 交付物 / 记录闭环 | `work-management` |
| 测试环境定义、构建与隔离 | `test-management` |
| 目录、地图、漂移、placement 合规 | `workspace-management` |
| 能力登记、发现、分发到 runtime | `ability-management` |
| 长命运行时资源的健康与回收 | `runtime-management` |
| 飞书 Wiki/Drive/Minutes 对象放在哪里 | `feishu-space-management` |
| 日报、周报、里程碑报告发布 | `feishu-report` |

ServerOS 只编排关系与交接，**不抢占以上 owner 的数据权威**。

### 4.1 快路径：先搜再问，不读目录

`serveros-node-mcp` 上只需要这四个工具，按顺序用。**不要**为了找一条 ability 去加载整个目录
——目录会随能力数增长，一次问对的成本不会：

| 工具 | 它回答的问题 |
|---|---|
| `context.current` | 我在哪。roots 来自根契约；**Item 只从你给的 `cwd` 推断**，不给就是 UNKNOWN，不会替你猜 |
| `ability.search` | 哪个 ability 可能管这件事。子串匹配 name / id / repo path / domain / agents，默认返回 ≤8 行 |
| `ability.inspect` | 这一条是什么。单条完整记录；它**不**判断能不能执行 |
| `ability.resolve` | 这台机器**现在**能不能做，以及**缺哪些输入**。先读 `inputs_missing`，再读 `status` |

- `matched: null` 不是 `matched: 0`：前者是「没得搜 / 没搜」，后者是「搜了，没有」。
- `ability.resolve` 只补三个输入（ability / runtime / 本机身份），其余五个它**明说不看**。
  `UNKNOWN` + `inputs_missing` 是诚实答案，不是失败；要补齐就跑
  `serverosctl ability resolve --request <file>`。
- 能力可以是**操作级**的：`ability:<id>#<verb>`。verb 不进入 catalogue key。
- 这一层全是只读。它没有、也不会长出一个 `restart` / `stop` / `reclaim`。

`ability-management` 是 Capability / Ability Authority。本机只有一份**本地观测投影**
（`state/serveros/abilities/effective.*`），它可能过时，`registry_status` 会说。用它做 bootstrap，
**不要**从它做安装决定。

## 5. 三层产品，别把它们混成一个

```text
serverosd           常驻 user-space semantic controller —— 观察、探针、落盘、检测漂移
  ↑ Unix socket
serveros-node-mcp   薄 adapter —— 不含状态、不改任何东西，只转发
  ↑ plugin
Skill + Plugin      操作语义与发行入口
```

- **MCP 不是 daemon**：Claude / Codex 重启不应该让控制层下线。
- **Plugin 不是 state authority**：它只负责把上面这些送进 runtime。
- **Skill 不做 primitive**：规则只写在 Skill 里的，就是没有别人会遵守的规则。
- `serverosd` **默认 observe-only**，`--enable-reconcile` 才允许动资源，且需要 budget 允许。
  单元文件里没有这个 flag，所以重启机器不会把写入打开。

## 6. 最小纪律

- 不 reset/clean/stash/discard 他人 WIP；不 `git add .`；不 force-push；不代提交受保护仓库。
- 不移动活动 worktree / 容器 / venv / bind mount；未知消费者标 `DEFER`。
- 不输出 credential / token / cookie / 私钥；registry、observation、report、log 里都不写值。
- **root 磁盘压力只 observe / explain / recommend。** 永远不自动删未知内容，不 `docker system prune`，
  不 `docker volume prune`。`/` 是系统盘，`/opt/applications` 是数据盘，两者分开报。
- machine state 只在 `DATA_ROOT/state/serveros/`；`~/.serveros` 保持小。
- 能力发布由 canonical 构建（reusable Skill 源在 `ability-management`），**禁止从 runtime 反向覆盖 canonical**。

---

- 机器 contract：`references/serveros-contract.json`。
- 对象模型与生命周期：`references/object-model.md`。
- 运行时流程细节：`runtime-management`。
