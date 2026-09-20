# ServerOS 系统图

> 本图是 [ARCHITECTURE](../../ARCHITECTURE.md) 的导航视图，不另立authority或额外必需服务。固定正文基线：`ef4386e21c2ed4be65ea9d538308c46925cba966`。

```mermaid
flowchart TB
  HUMAN[用户 + ChatGPT\n目标/需求/研究/选型/设计/评审]
  F[飞书\n播报/告警/Today/决定/审批/日报]
  UI[可选 CloudCLI Console\n会话/工作/证据/终端]
  CLI[CLI / tmux / SSH\n原生或人工入口]
  AGENT[Claude Code / Codex]
  KIT[薄 ServerOS Agent Kit\nbootstrap + typed discovery]
  RES[Context / Ability / Space resolvers\n按需读取，不是新daemon]
  ADM[共享操作准入语义\nactor + scope + target revision + policy]
  A[A 工作基础设施\n能力/环境/空间/会话/记忆/测试/文档/构建/控制]
  B[B 信息到交付\n认识/候选/Item/研究设计/实施/验收/汇报]
  W[既有 work-engine\nCandidate / Item / Activity / Run / Envelope / receipts]
  REG[ability-management\ncanonical artifacts / revisions / compatibility / installations]
  OPS[实例配置与policy绑定\nroots / 来源 / 保护 / 时间窗 / 接收人]
  D[serverosd\n唯一常驻核心\n本地事实 + ownership + admission + 有限reconcile]
  P[外部 Providers\nMCP / Browser / Container / Tunnel / LLM / Quay / Feishu]
  SCHED[OS Scheduler\n有限oneshot recipes，非LLM daemon]
  DOC[records / artifacts / canonical云文档\n每类事实保持其写权威]
  PRES[统一 presentation model\nStatus / Alert / Work / Report]

  HUMAN --> F
  HUMAN --> UI
  HUMAN --> AGENT
  AGENT --> KIT --> RES
  REG --> RES
  OPS --> RES
  RES --> A
  RES --> B
  B --> W
  A --> ADM
  B --> ADM
  F --> ADM
  UI --> ADM
  CLI --> ADM
  ADM --> D
  ADM --> P
  D -->|有预算的本地观察与受控操作| P
  P -->|带来源的结果/观察| D
  W --> DOC
  B --> DOC
  SCHED --> A
  SCHED --> B
  D --> PRES
  W --> PRES
  DOC --> PRES
  PRES --> F
  PRES --> UI
  PRES --> CLI
```

注意：图中的CloudCLI插件路径走共享语义；CloudCLI原生文件/Git/shell操作、用户直接SSH仍可能绕过ServerOS API。它们必须受native permissions/OS隔离/工作区scope约束，或标external/unmanaged，不能把图画成不存在的全局拦截器。

## 两个常见时序

```text
Agent执行：
目标 -> current Item capsule -> search(可跳过)
 -> inspect selected capability -> per-operation support
 -> fresh-enough readiness -> exact authorization
 -> load/attach -> bounded execution -> result/evidence -> checkpoint

日常工作：
授权源增量 -> relevance-filtered understanding -> Candidate Board
 -> 用户/ChatGPT选择与设计 -> Execution Envelope
 -> CC/Codex实施（可委派Quay）-> targeted verify
 -> 交付/决定/阻塞 -> 日报与知识更新
```

报告与卡片只是人类呈现，不回写为新的事实权威。图上一个方框不代表一个进程、repo、Skill或数据库。
