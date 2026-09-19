# 来源、版本和已知边界

研究读取日期：2026-09-20。设计来自用户本轮指示与前序讨论；源码事实以本表固定 revision 为限。发布本设计不证明东京当前安装/运行一致，不是功能完成证明。

## 固定源码基线

- S1 ServerOS canonical ARCHITECTURE：`b50b6fe0ca713f76004f3cdbb3327c6959cf8c08`，https://github.com/ProfessorZhi/serveros/blob/b50b6fe0ca713f76004f3cdbb3327c6959cf8c08/ARCHITECTURE.md 。用于 Core-2、四平面、唯一resident、闭合职责与仓库边界。
- S2 Ability Fabric：https://github.com/ProfessorZhi/serveros/blob/b50b6fe0ca713f76004f3cdbb3327c6959cf8c08/docs/ability-fabric.md 。既有 WHAT/HOW/WHERE/WHICH/Adapter 与 EffectiveAbility；按operation的支持不能由整体安装状态推断。
- S3 composition：https://github.com/ProfessorZhi/serveros/blob/b50b6fe0ca713f76004f3cdbb3327c6959cf8c08/plugin/bundle.yaml 。组合权威与 pinned reusable payload，不在serveros复制Skill源码。
- S4 ability-management：`caad9acff9c961ba916d307748c0aa735ecb8916`，https://github.com/ProfessorZhi/ability-management/blob/caad9acff9c961ba916d307748c0aa735ecb8916/README.md 。六类namespace、身份/Revision/兼容/安装分离、唯一管理协议。
- S5 work-engine：`65e9b56c2f5d566c5e12414cf8737c982e6b5408`，https://github.com/ProfessorZhi/work-engine/blob/65e9b56c2f5d566c5e12414cf8737c982e6b5408/README.md 。Item/Activity/Run、独立outcome、worktree与证据边界、根解析。
- S6 前序生成方案 `serveros-vnext-blueprint/SERVEROS-ARCHITECTURE-vNext.md` 与用户提供的《ServerOS 能力拓扑、资源归属与 Feishu 统一控制台总体设计研究报告.md》。作为需求背景而非canonical；前序方案指出的information-management手动刷新、board local-only约束，实施时必须重读对应canonical Skill后做显式增量，不凭研究报告代替源码。
- S7 Quay参考分支 `develop-archived-20260816@ddc4fb40f616148e3b278cbc062e6599285cd549`。另观察到develop为`8bb184ef35139458e487b5a79b064da3e2db1e59`、dist-plugin为`1a6b904fc764587ce624c47a6bb7c313ea8a4cf0`；未做这些分支的完整差异审查，不能推断它们或东京安装具有相同接口。
- S7a Goal实现：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/packages/quay/src/goal-store.ts 。实际读取1–210行，确认PHASE/AC、独立schema、status、origin、criterion、非空验收集、active phase规则；没有由此宣称已验证所有并发/原子性保证。
- S7b Goal规格：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/orchestration/SPEC-goal-store-2026-08-09.md 。实际读取前200行；Goal不是Task、ADR或Method Document。
- S7c author：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/plugin/skills/author/SKILL.md 。前序核读：独立交付物拆分与Proposal/Plan/AC/DoD；执行前以本机版本复核。
- S7d loop-driver：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/plugin/skills/loop-driver/SKILL.md 。前序核读：select/isolate/build/gate/audit/record/land、有界参数与范围风险。
- S7e task-operator：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/plugin/skills/quay-task-operator/SKILL.md 。前序核读：任务管理不是自主执行授权。
- S8 Quay directive：https://github.com/ProfessorZhi/quay/blob/ddc4fb40f616148e3b278cbc062e6599285cd549/plugin/skills/quay-directive/SKILL.md 。Task-canonical directive、按实际Provider能力写回/回读；不重建独立directive数据库。

## 官方公开资料

- S9 Claude Code Skills：https://code.claude.com/docs/en/skills 。本轮读取：Skill正文按需加载、插件命名空间、配置/内容加载边界。文档会随版本变化，适配按安装版本验证。
- S10 Claude Code MCP：https://code.claude.com/docs/en/mcp 。工具搜索、连接/权限与自定义网关兼容性；不是已证明所有模型渠道均可用。
- S11 飞书卡片折叠面板：https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/collapsible-panel 。本轮确认官方页面入口；网页正文未被工具提取，因此这里不声称验证具体JSON属性，CC实现前读取实际schema/SDK。

## 本次明确未做

未登录东京/北京/Windows执行命令；未读取或转移秘密；未运行模型/Quay任务；未安装插件；未测试真实飞书卡片；未启用定时器；未改任何线上网络/服务。只发布独立文档分支，不覆盖main和既有canonical架构。

## 比前序方案更正/明确的四点

1. Goal必须按Quay原生PHASE/AC建模，不只描述Task；支持情况仍需安装面确认。
2. 看板/候选先存在，选中后才展开单项需求文档；不要求全部候选先有长brief。
3. 本轮交付覆盖完整骨架与卡片/能力家族，不仅是一个个人看板生成器；深度业务细节随后打磨。
4. “骨架可运行、样例已验证、Quay实际执行、线上已切换”四层分别报告；批准发布文档不隐含部署授权。
