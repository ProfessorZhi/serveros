# 协作实现索引：一份主计划，按当前代码接力

2026-09-23。唯一顺序、最新源码观察和最终门在[CC-EXECUTION-PLAN](CC-EXECUTION-PLAN.md) P0–P12。本页只映射边界，不保留第二份独立施工清单。COLLABORATION-CONTRACT继续定义Parent/receiver/权限/预算/恢复。

## 1. 已落地的实现必须复用

本轮提交前serveros前进到7b9d417：understanding_semantic.py已经提供reading校验、委托内部分语义提交、fence内写入、候选与幂等receipt；source_waterline.py已经提供区间/gap/tail和document digest处理；understanding_patch原有content_identity/card_digest保护也已存在。

因此不能再列“B2没有writer、缺hash、缺水位模块”为当前任务。当前需要验证并接通的是：真实原生Agent读取材料→生成符合范围/版本的reading→可信控制面注入授权/actor→复用现有writer→相关view/失效→下一consumer。模块不直接调用模型是分工，不是缺陷；源码存在也不证明完整J03/J06已跑。

records同时到81673ebb：Nature新增消息/材料/SOP，Bruce/Nature/项目理解与发布排除登记已合入。保留这些产物及其原始证据/排除表，不重复下载，不把旧38条/无文件继续列缺口。README历史统计和本轮源代码检查不是LIVE测量。

## 2. 接力边界

| 接力 | 当前owner/接口责任 | 主计划/验证 |
|---|---|---|
| 关注声明→采集 | desired source与旧watchlist/排除登记适配，manifest保observed | P1/P2；DEEP §10、REF06 |
| 捕获→语义 | 原生reading真实生产；现有semantic writer接受；水位区间保gap | P2/P3；J03 |
| Source→个人参考 | B1核原文，B9独立Reference，Space定位本人参考节点 | P4/P5；REF01–REF12 |
| 参考→采用 | 只产生有出处候选，B5/B6核适用性和接受范围 | P3/P5/P7；REF11 |
| 当前工作→执行 | Card意图+Work Engine控制+Agent Control委托+executor事实 | P6/P7；J06 |
| GPT/Quay→Parent | 真Session、输入版本、实际worker与领域验收 | P7/P11；J06 |
| 正文→发布/通知 | 单正文authority、人批注保护、精确readback/receipt | P4/P5/P11；J04/J10 |
| shared模型→业务 | 两个真实consumer；服务健康与答案质量分开 | P10；J05 |
| Core/服务→人面 | 真实probe、默认可读model、native卡片、独立故障路径 | P8；J08/V系列 |
| Git/飞书/本地 | 指定方向snapshot/投影/回源proposal，不mtime双主 | P1/P4/P5；REF09 |
| 目录→consumer | 先切消费者、再核实际引用和保留条件 | P12；J09 |

## 3. 不需要新增的系统

Reference是B1+B9/Space组合；catalog是source声明与manifest的派生视图；语义提交已经有机制；focus用原控制权威join；Parent是原生Session角色。都不需要新source数据库、Claim DB、全局handoff库、workflowd或新的LLM调度平台。

一资源只一个生命周期owner：Quay管driver/Task，OS管受管进程，ServerOS管准入/观测和产品约束。模型不能根据自己的reading声称获得授权，文件里的署名仍需可信调用者解析。

## 4. 并行、发布安全与退出

同文件WIP有作者/接口/commit接力，不抢写，其他包继续。当前publish-excluded.json处理内容不宜Git发布，heavy-media处理体积，不合并判据；本次文档不扩大现有redaction到结构身份字段。历史已传播凭据不能只从HEAD去掉就声称安全，记录owner核验/轮换/处置而不重抄或越权操作。

“存入飞书”需原doc回读，“本地保存”需实际范围/字节，“发报告”需真实sender回执；no-code任务不强制PR/生产。每次未知effects先查同attempt/目标actual，已完成阶段不重放。没有实际consumer的待续文件不能承诺后台执行。

此文不产生授权/部署。主计划已经列四条真实链、21项接收矩阵和源更新；执行重点是现有机制的真实输入、可信身份和业务接受，不再反复追加架构分类。
