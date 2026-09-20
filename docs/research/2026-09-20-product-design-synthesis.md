# ServerOS 最终产品设计研究汇编 · 2026-09-20

## 来源完整性声明

本文是本轮依据可读取的仓库、历史报告检索结果、用户约束、官方文档与论文检索重新核对的设计汇编，**不是此前 Deep Research 输出全文的逐字归档**。

截至本轮，可见会话附件只有告警卡截图；Library 检索到《ServerOS 能力拓扑、资源归属与 Feishu 统一控制台总体设计研究报告.md》，但没有检索到最新请求“ServerOS Final Product & Plugin Architecture”的完整输出。此前工具返回的 research session ID 只能证明研究被发起，不能证明正文已完成或已获取。最新原文归档仍待取得其真实正文；不得用本文件冒称已经完成原文保存。

已有 GitHub 架构工作稿与 human-control addendum 已在 `docs/final-architecture-20260920`，本次保留历史并形成规范总入口 `ARCHITECTURE.md` 与专题文档。这里只放证据和取舍，不成为第二总体架构权威。

## 决策摘要

| 研究/事实依据 | 采用的项目决策 | 证据不支持的过度推断 |
|---|---|---|
| 原生 plugins 可封装 Skills/MCP/hooks；Skills 支持渐进式加载 [S1–S4] | 一个薄 ServerOS Agent 入口，方法与工具按需展开 | 所有runtime具备相同热attach；安装多≠prompt自动小 |
| Context engineering 强调相关上下文和有限信息 [S5] | current Item capsule + 小候选集，本地lexical/tag先行 | 3–8是本项目初始目标，不是论文证明的普适最优k |
| 选择性回归研究展示成本/漏测折衷 [S6] | 依赖/配置感知的定向选测；发行边界广测 | 只跑改动文件测试就保证无回归 |
| SRE 强调可行动信号和低噪声 [S7] | Status/Alert分开，低价值事件静默/汇总 | 单纯把raw code翻成中文就成为好告警 |
| 飞书云文档包括多种内容/管理入口，My Docs可有子页面 [S8–S10] | surface/object/hierarchy 与 semantic role 分开 | 三入口是互不重叠内容库；MyDocs永远flat；UI有能力意味着API已可用 |
| 录制文件治理具有权限与不同删除/恢复动作 [S11] | relevance与deletability分离，MeetingBundle引用检查 | 不相关=有权永久删除；所有账号能用管理员功能 |
| MCP授权协议处理 transport credential boundary [S12] | native auth + ServerOS scope/admission，秘密不入普通state/log | MCP登录成功=允许所有业务操作 |
| 现有repo契约区分composition、registry、work state | 多领域authority、单一路径消费 | “一份真相”意味着所有状态放同一数据库 |

## 官方与一手来源（本轮检查）

- [S1] Anthropic, Create plugins: https://code.claude.com/docs/en/plugins
- [S2] Anthropic, Connect Claude Code to tools via MCP（包括tool-search/配置的运行时边界）: https://code.claude.com/docs/en/mcp
- [S3] OpenAI, Package your plugin（旧Codex build链接现重定向到统一插件文档）: https://developers.openai.com/plugins/build/plugins
- [S4] Agent Skills specification: https://agentskills.io/specification ; OpenAI Skills入口: https://developers.openai.com/codex/skills
- [S5] Anthropic, Effective context engineering for AI agents: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- [S6] Soetens, Demeyer, Zaidman & Pérez, Change-based test selection: an empirical evaluation, Empirical Software Engineering (2016), DOI 10.1007/s10664-015-9405-5。SciSpace本轮返回论文摘要，记录静态/动态选择的成本与漏测折衷；未宣称本项目已复现其结果。 https://doi.org/10.1007/s10664-015-9405-5
- [S7] Google SRE, Monitoring Distributed Systems: https://sre.google/sre-book/monitoring-distributed-systems/
- [S8] 飞书官方，飞书云文档怎么用（云文档总称及Wiki/MyDocs/Drive）: https://www.feishu.cn/content/article/7572026952902230020
- [S9] 飞书帮助中心，快速上手云盘: https://www.feishu.cn/hc/zh-CN/articles/778513900437
- [S10] 飞书帮助中心，使用子页面列表: https://www.feishu.cn/hc/zh-CN/articles/780474929438
- [S11] 飞书帮助中心，管理员管理录制文件（妙记）: https://www.feishu.cn/hc/zh-CN/articles/823897737029
- [S12] Model Context Protocol, Authorization (2025-11-25): https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization

API/SDK/产品可能继续变化；实现按当前官方接口和固定安装版本核验。上面产品帮助文档不能证明当前租户权限或某个API具体存在。原文无法获取的论文只以摘要/元数据级证据使用，不把检索器生成的摘要当全文实验证据。

## 本项目明确取舍

保留单常驻核心；不新建workflow平台；One product/multiple adapters；技术支持与调研等同为work；先看板后文档；Marek例外准确绑定；不相关录音先阻止进入知识与日报，不以分类置信度代替删除授权；所有required职责实现后逐项验收，不是MVP也不是无限增补功能。

## 现有仓库读取快照

- serveros 架构工作分支本轮开始：`958811d085285fbba652f7b6a9f8d8c29d420cad`，PR #3；main基线由该PR返回为 `b50b6fe0ca713f76004f3cdbb3327c6959cf8c08`。
- ability-management main本轮查得：`9a182a73a420f12e86c7e0321ea717ffabf70523`，已不是早先交接的 `caad9ac`；不能固定用旧交接状态推理。
- work-engine main本轮查得：`65e9b56c2f5d566c5e12414cf8737c982e6b5408`。
- 以上是GitHub读取，不是Tokyo现场复测；本轮不宣称运行测试、部署、发卡、清理或修改会议原件。
