# Fleet 需求

这是 Fleet 产品需求的唯一入口。2026-11-12 至 11-13 先后写了三份草稿（PR #626、#628、#629），它们合并在这里，去掉重复的入口，并改掉了已过时的现状描述。

| 文档 | 回答什么 |
|---|---|
| [fleet-product-requirements.md](fleet-product-requirements.md) | 产品定位与最高原则；七个核心问题（插件性能、指挥延迟、直达 Agent、飞书体验、资源画像与告警、Ability 版本、自动发现与目录治理）；P0/P1/P2 顺序；GitHub 治理 |
| [fleet-efficiency-performance.md](fleet-efficiency-performance.md) | 性能与执行可靠性作为一等目标；可度量、可设预算；所有外部操作有界、进程树回收、单实例；跨平台一致；Windows 密钥同步事件是第一个案例 |
| [fleet-current-state-and-gaps.md](fleet-current-state-and-gaps.md) | 仓库目录职责与混层；九段理想工作流（信息采集 → 结构化认识 → 需求 → 每日对齐 → 调研设计 → 执行 → 验收 → 交付 → 对账与记忆）；飞书播报与可观测性现状 |
| [fleet-work-system-and-browser.md](fleet-work-system-and-browser.md) | 统一工作系统；把 Chrome 纳入 Fleet（Browser Runtime）与凭据中心的协同 |
| [rust-evaluation-2026-11-13.md](rust-evaluation-2026-11-13.md) | C++ / Rust / Python 的实测对照、Fleet 链路延迟基线，以及各组件改写成 Rust 的先后顺序 |

## 已定的决定

| 日期 | 决定 | 出处 |
|---|---|---|
| 2026-11-13 | **新的性能敏感组件默认用 Rust。** 现有 C++ / Python 组件按实测逐个决定是否改写；没有收益就不切换 | Owner；[Rust 评估](rust-evaluation-2026-11-13.md) |
| 2026-11-13 | 效率与性能从起点就是一等目标；任何外部操作必须有超时、整体期限和进程树回收 | Owner；[性能需求](fleet-efficiency-performance.md) |
| 2026-10-28 | 旧版 Fleet 与 Fleet Next 双轨并行，Next 不覆盖旧版 | Owner，#478（并入 #531） |

## 和 V1 的关系

V1 的范围仍以 [#531](https://github.com/ProfessorZhi/serveros/issues/531) 为准：三台设备 × 四部分（Ability 家族、原子能力、监控与播报、指挥侧 Skill），加一次真实自迭代，每格要设备回执。

这里的需求大多属于 V1 之后。只有下面几项进入 V1 的完成条件：

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
