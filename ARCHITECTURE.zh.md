# ServerOS 架构导读

> 这是一份**导读**，不是规范。[ARCHITECTURE.md](ARCHITECTURE.md)（英文）是唯一规范性产品架构；本页只解释它为什么这样组织、各部分如何咬合。两者冲突时以规范为准。

## 一句话

ServerOS 是**个人用户态的 Agent 工作操作系统**：为 Claude Code、Codex 这类原生智能体提供常驻的环境、上下文、记忆与能力，而不是在它们之上再造一个规划者。

## 系统由什么组成

| 层 | 组件 | 负责 | 不负责 |
|---|---|---|---|
| 人面 | CloudCLI、终端、手机、飞书、文档 | 人看什么、决定什么 | 不持有业务状态 |
| 智能 | Claude Code、Codex | 理解、研究、设计、实现、迭代 | 不被 ServerOS 替代 |
| 入口 | ServerOS Plugin + 两个 MCP | 装载当前相关的 context、Skills、工具 | 不保存第二份状态 |
| 常驻层 | `serverosd`（每台设备唯一） | 节点事实、准入、租约、效果协调、自检 | 不做 LLM 规划，不重复 systemd |
| 语义与工作 | Claims/Views、Requirement、Work Engine、Agent Control | 当前事实、工作状态、Run 与回执 | 不代替外部系统的事实 |
| 能力 | ability-management + 本机安装 | 能力身份、来源、revision、分发 | 不要求每个能力配齐 Skill+MCP |
| 外部 | 飞书、GitHub、OpenViking、Quay、浏览器、OS、网络 | 各自领域的事实与效果 | 不被复制成第二权威 |

重要的边界只有一句：**Plugin 是入口，Core 是常驻层，二者都不是第二个状态库。**

## 一份材料走一圈

```text
飞书 / 网页 / 会议 / 文件
        │  采集（B1）
        ▼
结构化理解（B2）：Atomic Claim + Current View
        │  对账后形成需求候选（B3）
        ▼
Requirement → Work Item → 0–2 个 Focus
        │  Parent 持续负责（A1）
        ▼
执行：CC / 子 Agent / Quay（A6）
        │  验收与证据（A7）
        ▼
Outcome → 报告 / 文档 / 后续工作
```

这不是必须按序执行的流水线。调研可以先于设计，测试可以推翻计划，交付物也可以只是一份验证报告。**系统自动化的是「怎么做」，只在目标、重要取舍、受保护的效果或不可替代的人类输入上打断人。**

## 权威归属

同一个问题只有一个权威。其余都是投影、缓存或视图：

| 问题 | 权威 |
|---|---|
| 当前确认的事实 | 当前 Source + B2 Claims/Views |
| 稳定工作意图与需求 | Requirement / Work Item |
| 工作状态、Run 与外部效果回执 | Work Engine |
| 能力身份与来源 | ability-management（G1） |
| 权限与授权 | 设备策略、Binding 与 operator grants |
| 长期相关性记忆 | OpenViking（**不能覆盖**上面的当前事实） |
| 版本化语义记录与设计 | GitHub / 私有 records |

**投影可以用于阅读，不能用于授权一次新的风险写入。** 过期视图不能签发新权限。

## Session、上下文与记忆

- **Session 是资源，不是身份。** 一个需求可以有多个干净 Session；原生线程 ID、进程、tmux 窗口都不是 Item 身份。
- **渐进披露。** 默认只有很薄的入口；说出需求后加载相关目标、checkpoint、约束与来源；真正需要时才展开记忆、Skill 正文、MCP 和原始证据。
- **记忆分三层。** 当前 records 与 Source 是权威；OpenViking 负责跨 Session/项目/设备的相关性召回；原生 transcript 是本地便利层。召回不能复活已经纠正的旧结论。
- **交接靠 checkpoint。** 换设备或换 Session 时，从当前 Requirement、checkpoint、worker 和 effect 状态继续，不重讲历史、不重放未知效果。

## Fleet：多设备怎样工作

Fleet 只做四件事：**发现设备、路由会话、约束授权、可靠投递**。

- 每台设备有自己的 Core、Runtime 和本地策略；设备注册不代表其所有账号与会话都被授权。
- 会话地址是 `device:<id>|<provider>|<session id>`；单独一个 session id 不是地址。
- 创建、发送、控制、批准、导航是五个独立权限，互不隐含。
- 消息投递成功不等于接收方已经执行；超时后先对账，不当作可重试。

V1 已实现上述能力的候选版本；V2 的自治组织、角色委派与跨设备 Session 池只有设计，见 [Fleet V2](docs/architecture/FLEET-V2-AUTONOMOUS-ORGANIZATION.md) 与 [Agent 身份/委派](docs/architecture/AGENT-IDENTITY-SESSION-DELEGATION.md)。

## 安全边界

- **合规与非干扰优先。** 共享主机上，其他用户的进程、工作树和凭证不是可清理对象；未知归属一律 `DEFER`。
- **权限是代码，不是文案。** Skill 里的一句话或一张「已批准」卡片不构成技术强制边界。
- **凭据只在 vault。** 配置里只写路径，不写值；密码、token、cookie 不进仓库、日志或证据。
- **外部效果先记账再重试。** 超时后发送结果未知，不能直接重复；先对账真实状态。
- **放置有规则。** 源码、工作树、产物、记录、状态各有根；不为了「看起来整齐」移动历史仓库、别人的工作树或唯一证据。

## 五个常见误解

1. **「ServerOS 是另一个调度器。」** 不是。模型决定方法；ServerOS 提供环境、上下文、权限与证据。
2. **「Ability 就是一个 Skill。」** 不是。Ability 是产品职责；Skill、MCP、Plugin 是它的实现方式，可以只实现一部分。
3. **「记忆可以覆盖当前事实。」** 不能。检索结果只是线索，当前 Source/records 与权限事实优先。
4. **「文档写完就等于上线。」** 不等于。设计、代码、安装、真实验收是四层证据。
5. **「多设备就是同步文件。」** 不是。每台设备保留自己的状态与策略，通过受认证的接口交接，不做网络共享写入。

## 怎么读完整架构

| 你想了解 | 读 `ARCHITECTURE.md` 的 |
|---|---|
| 产品定义与整体形状 | §0 |
| 设计原则与日常运转 | §1–§3 |
| 信息、理解与文档 | §4–§5 |
| Session 与记忆 | §6–§7 |
| 工作、需求、研究与执行 | §8–§11 |
| 验证、报告、工作区 | §12–§15 |
| 能力体系 | §16 |
| 环境、设备、运行时 | §17–§21 |
| 组件边界、效果协议、存储与发布 | §22–§27 |

只需读当前任务相关的一节，不要把整份文档注入 Agent 上下文。
