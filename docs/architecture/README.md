# ServerOS 系统架构导航

规范总入口：[ARCHITECTURE.md](../../ARCHITECTURE.md)，Design ID `serveros-system-v1`。

本目录按问题拆分正文，避免 Agent 一次读全套。只在首次系统整合时阅读全文；普通工作先读取相关能力与 Item。

| 问题 | 规范 |
|---|---|
| 最终产品、基本核、authority | [ARCHITECTURE](../../ARCHITECTURE.md) |
| 两大家族，每个工作归谁 | [ABILITY-FAMILIES](ABILITY-FAMILIES.md) |
| 插件/MCP/Skills、会话、人类控制、快路径、验证预算 | [AGENT-EXPERIENCE](AGENT-EXPERIENCE.md) |
| 岗位认识、看板、需求/设计、执行、Quay、日报 | [INFORMATION-WORK](INFORMATION-WORK.md) |
| 磁盘、地图、飞书三个入口、会议筛选与清理 | [SPACE-AND-RETENTION](SPACE-AND-RETENTION.md) |
| 怎样证明全体系落地而非空壳 | [ACCEPTANCE](ACCEPTANCE.md) |
| 研究依据、来源完整性与设计判断 | [研究汇编](../research/2026-09-20-product-design-synthesis.md) |
| 给 CC 的整轮执行提示词 | [CC-PROMPT](../execution/CC-PROMPT.md) |

`docs/proposals/2026-09-20-*.md` 是讨论历史，不再是本分支的目标规范。设计定稿不等于 CODE/INTEGRATION/LIVE 验收通过。
