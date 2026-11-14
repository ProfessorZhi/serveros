# Fleet 需求

这是 Fleet 产品需求的唯一入口。2026-11-12 至 11-13 先后写了三份草稿（PR #626、#628、#629），它们合并在这里，去掉重复的入口，并改掉了已过时的现状描述。

**先读 [fleet-requirements.md](fleet-requirements.md)**：全部需求的唯一编号清单（验收、自动化档位、状态、待拍板问题）。下表的文档是它的背景与细节。

| 文档 | 回答什么 |
|---|---|
| [fleet-requirements.md](fleet-requirements.md) | 需求清单：总原则、工作流模型，以及采集与认识、对齐与看板、条目与编号、派活、观测、资源治理、能力发现、安全、性能各组需求 |
| [fleet-product-requirements.md](fleet-product-requirements.md) | 产品定位与最高原则；七个核心问题（插件性能、指挥延迟、直达 Agent、飞书体验、资源画像与告警、Ability 版本、自动发现与目录治理）；P0/P1/P2 顺序；GitHub 治理 |
| [fleet-efficiency-performance.md](fleet-efficiency-performance.md) | 性能与执行可靠性作为一等目标；可度量、可设预算；所有外部操作有界、进程树回收、单实例；跨平台一致；Windows 密钥同步事件是第一个案例 |
| [fleet-current-state-and-gaps.md](fleet-current-state-and-gaps.md) | 仓库目录职责与混层；九段理想工作流（信息采集 → 结构化认识 → 需求 → 每日对齐 → 调研设计 → 执行 → 验收 → 交付 → 对账与记忆）；飞书播报与可观测性现状 |
| [fleet-work-system-and-browser.md](fleet-work-system-and-browser.md) | 统一工作系统；把 Chrome 纳入 Fleet（Browser Runtime）与凭据中心的协同 |
| [rust-evaluation-2026-11-13.md](rust-evaluation-2026-11-13.md) | C++ / Rust / Python 的实测对照、Fleet 链路延迟基线，以及各组件改写成 Rust 的先后顺序 |

## 已定的决定

| 日期 | 决定 | 出处 |
|---|---|---|
| 2026-11-14 | **需求对齐后，Claude 可对 ServerOS / Fleet 大刀阔斧地改造、重构、删除与合并。** 底线不变：安全规则照旧；每步一个 PR、CI 全绿才合并、删除前打 tag；先并行再切换，不中断运行中的服务，部署由本地 CC 执行并回执；删生产数据、改生产服、开新前缀、凭据、对外动作仍先问 Owner；每步合并后告知改了什么、依据和怎么回退 | Owner |
| 2026-11-14 | 设备角色：东京是 Hub + 完整工作流；FJPCOS（个人工作站）与 A6000（GPU 计算节点，运行 LLM Lab：资源管理、模型上线与服务）不与东京对齐、不跑完整工作流，两台都可做生产日志查询与分析 | Owner；[需求清单 §3](fleet-requirements.md) |
| 2026-11-14 | 待定 10 项全部按 Claude 建议：D1/D9 立即做；东京目录标准与清理范围；飞书白名单与 Fleet 文件夹；分支规则；核心包 + 角色包；分发减重；能力/规矩/护栏分开 | Owner；[需求清单 §12](fleet-requirements.md) |
| 2026-11-13 | 飞书群聊只归档白名单，妙记全部归档；每日对齐在 ChatGPT 对话里进行；清理全自动；告警一律私发 Owner；新项目由 Owner 批准；编号只由 Git 签发（Work Engine 不再分配） | Owner；[需求清单 §11](fleet-requirements.md) |
| 2026-11-13 | **需求只在 Git 存一份**；飞书云空间文档自动生成、给人看；GPT 在聊天里渲染 | Owner |
| 2026-11-13 | **新的性能敏感组件默认用 Rust。** 现有 C++ / Python 组件按实测逐个决定是否改写；没有收益就不切换 | Owner；[Rust 评估](rust-evaluation-2026-11-13.md) |
| 2026-11-13 | 效率与性能从起点就是一等目标；任何外部操作必须有超时、整体期限和进程树回收 | Owner；[性能需求](fleet-efficiency-performance.md) |
| 2026-10-28 | 旧版 Fleet 与 Fleet Next 双轨并行，Next 不覆盖旧版 | Owner，#478（并入 #531） |

## V1 范围（Owner 对齐，2026-11-13）

分工：GPT 与本地 DeepSeek 负责采集信息；云端 Claude 负责决策并实现代码（PR，CI 绿即合并）；各设备的本地 Claude Code 负责部署。

V1 按以下顺序推进，每项都要三台设备的回执：

1. **Fleet 闭环跑通，设备侧高性能。** 派发 → Hub → 设备 → 结果 → 飞书播报与 agent 监控，中间任何一跳断了都能看见。新的性能敏感组件用 Rust，第一个是设备主动推送事件（见 [Rust 评估 §3](rust-evaluation-2026-11-13.md)）。
2. **CloudCLI 精确穿透，Claude Code 与 Codex 三台都要。** 指挥侧要能像人一样使用设备上的 Claude Code 与 Codex，包括列出并调用 Skill 和斜杠命令。CloudCLI（ProfessorZhi/claudecodeui）只补这一层薄的 MCP 工具；路由、权限、审计和能力发现留在 Fleet。
3. **指挥侧 Skill 融合业务知识。** 立项与编号（`<前缀>-<四位数>`，前缀注册表在 hwz-records 的 `ops/workspace/serveros/projects.yaml`）、项目开发知识、飞书目录。
4. **Ability 家族与能力发现，包括设备侧的「本机说明」。** 每台设备的 agent 能回答：我是谁，有哪些密钥（只列名字）以及怎么同步，有哪些服务和定时任务，目录在哪，网络走哪条路。
5. Grok / Gemini 等其他指挥端客户端暂缓。

依据：三台设备的只读采集报告，见 hwz-records 的 `information/raw/fleet-collect/2026-11-13/`（hwz-records PR #24）。

原有的 [#531](https://github.com/ProfessorZhi/serveros/issues/531)（三台设备 × 四部分，每格要设备回执）仍是验收形式。这里的需求大多属于 V1 之后，只有下面几项也进入 V1 的完成条件：

1. **所有外部操作有界**：有超时、整体期限、进程树回收和单实例（[性能需求 §3.2–3.4](fleet-efficiency-performance.md)）。第一个案例的修复 #627 已合并。
2. **性能有基线**：派发 → Hub 发现、设备完成 → 取回、取回 → 卡片，这几段的 P50/P95 每次发版前都用 `fleet_hub_watch/perf.py` 量一次，并写进发布说明。
3. **飞书播报完成真实客户端验收**：一次完成只发一张卡，失败标红，状态不明不报成功（与 #531 ③ 相同）。

## 链路基线（2026-11-13，fleet-journal 11 月 69 个 run）

| 环节 | P50 | P95 |
|---|---|---|
| 派发 → Hub 开始盯 | 13 s | 25 s |
| 派发 → 第一次读到状态 | 14 s | 29 s |
| 设备做完 → Hub 取回结果 | 1 s | 3 s |
| 取回结果 → 卡片处理 | 2 s | 11 s |

最大的等待是 Hub 轮询发现派发。下一步的性能工作就从这里开始，见 [Rust 评估 §3](rust-evaluation-2026-11-13.md)。
