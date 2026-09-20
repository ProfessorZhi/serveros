# 广义 ServerOS 完整性与验收合同

> Design ID `serveros-system-v1`。每项都要有结果，不允许以“最小实现先做三个”替代本版范围；也不把未要求的平台/功能加入范围。

## 1. 三轴而非一个绿灯

每个要求记录：设计 ref、代码/Skill revision、入口、实际 source、测试/Run、产物、CODE / INTEGRATION / LIVE、未完成与原因。状态取 PASS/FAIL/BLOCKED/NOT_RUN/NOT_APPLICABLE，并解释 NOT_APPLICABLE；不用 BLOCKED 冒充完成。

CODE：机制/协议真实、定向案例通过。
INTEGRATION：真正 producer 到 consumer，跨 repo pin/安装 surface/路径跑通。
LIVE：目标设备、真实授权账号/来源/接收面端到端验证。

fixture/HTML 预览/exit 0/manifest PRESENT/Task created 不能单独形成 LIVE PASS。无 live 外部连接的能力不因此省掉实现；完整实现后单列外部阻塞。

## 2. 必需职责验收矩阵

| ID | 范围 | 正向交付 | 必需反例/边界 |
|---|---|---|---|
| SYS-01 | serverosd 核与资源 | 可启动、自己/整机/文件系统/quota/shm、runtime 四轴与 provenance | optional Provider 全缺仍能启动；profile/live冲突；观测器失败；无数据非零 |
| SYS-02 | 动态环境 | declare/resolve/attach/release/restore desired refs | 最后一引用退出、crash/lease 过期、外部/protected、不误停持久共享资源 |
| SYS-03 | Ability management | 来源/身份/revision/compatibility/install/sanitize/reconcile 全链 | 同名异源、同源不同 artifact type、不兼容、未观察到≠删除、fingerprint mismatch |
| SYS-04 | Discover/resolve | small-context 真实 search→正文/工具→operation | 未知、unsupported、denied 分开；无需全 catalog；无 vector DB 仍有可用路径 |
| SYS-05 | Claude/Codex adapter | 各自在真实 runtime 进入同一 Item，按需使用能力 | 不支持热 attach 不撒谎；不覆盖全局配置；无重复 active materialization |
| SYS-06 | workspace/maps | role-based context/worktree/test-env/evidence，L1/L2/L3 | own scope 新路径可解释；ACTIVE/unknown/例外不动；全局旧红项如实保留 |
| SYS-07 | live Session | list/inspect/send/result、支持的 create/resume/control | accepted/turn/session 分离；重复请求、并发 writer、stop≠interrupt、Provider失联 |
| SYS-08 | history/memory | scoped search/checkpoint/handoff；支持的 archive/restore | 不归档活会话、不上传 transcript、不跨 scope、失效索引可重建 |
| SYS-09 | 测试与工程纪律 | 真实测试方案、可复用环境、定向验证 | 源码不复制test-envs；端口/卷隔离；fixture变化失效；不全局gate每步 |
| SYS-10 | 文档治理 | canonical/revision/link/publish/supersede | 人工并发编辑不覆盖；broken ref、不可读、云端source不双写 |
| SYS-11 | build/release/deploy | 固定输入构建、digest、smoke、发布/部署/回滚分离 | secret不入镜像；tag竞争、跨repo pin不存在、部署未授权 |
| SYS-12 | Feishu 三入口 | scoped cloud-doc/Wiki、Drive、MyDocs inventory与placement | partial coverage、同对象多入口去重、无API支持不伪造 |
| SYS-13 | MeetingBundle | 相关片段→认识；无关过滤/清理计划/可恢复路径 | SPACE-AND-RETENTION §10全部关键边界，Marek UNBOUND绝不删 |
| SYS-14 | 信息/岗位认识 | authorized delta→source-backed claims/角色/项目/commitments | 噪声不变需求、推断非确认、prompt injection、403不清空认识 |
| SYS-15 | Board/Item | 跨项目候选→选中→brief；全类型kind | 候选无长brief也能显示；重复输入去重；取消/失败/PR未合并不冒充完成 |
| SYS-16 | 研究/需求/设计 | 有引用研究→技术比较/人类决定→设计/按需计划 | 不以调研数量充成果、不自行改目标/验收、简单任务无模板膨胀 |
| SYS-17 | 自动推进 | 增量感知、执行、汇报三个有限recipe | 无变化不调用模型、重入幂等、预算到/无进展停、待决策只阻塞依赖 |
| SYS-18 | 技术支持/杂事 | 至少覆盖支持、文档、测试/调研、开发/部署的真实应用案例 | code/KB双仓边界；不改并行业务做demo；闭麦工作仍有非音频证据 |
| SYS-19 | CC→Quay | 一段真实有界工作，非dry-run执行→回读→父工作/日报 | 零执行、失败AC、task done不关Item、重复派发、并行写冲突 |
| SYS-20 | Human control | TEST渠道真实事件/身份/审批绑定/执行receipt | 重放、过期、错actor/target/revision、普通yes非批准、无假按钮 |
| SYS-21 | 卡片 | Status/Alert/Recovery/Work/Decision/Report统一语义，窄屏/桌面 | 无关ledger不首屏、观测失败非恢复、未展示不记已告知、发送未知回查 |
| SYS-22 | Console | CLI/Agent无需CloudCLI；可选Web插件对接真实ServerOS读/控制 | 原生CloudCLI写入不谎称被准入；无Web不影响核心；权限隔离 |
| SYS-23 | 日报/周报 | 定期真实工作差量+Quay记录、canonical doc+publication receipt | 无进展如实说、未运行/未测/缺覆盖标明；无关纪要不进入 |
| SYS-24 | 全体系发行 | serveros/ability/work-engine/ops/adapter refs可达、隔离安装/升级/rollback | 源码目录外运行；重启恢复；保留授权/工作/receipt；无悬空pin |

## 3. 覆盖原则

每个 Ability 合同、每个必须 operation、每个 Provider adapter 的支持表、每个地图/目录 role 都映射到上述 ID，避免遗漏与无限增加检查。一个真实案例可以覆盖多个 ID，证据引用复用，不能重复计成功。

外部 Provider 原本不支持的 operation 需要可用的拒绝/替代说明，不要求实现整个第三方产品。但本版用户明确需要的功能若因此做不到，该功能 LIVE 仍是 BLOCKED，不能重命名为 optional 绕过验收。

## 4. 交付集

统一 START-HERE：当前版本/怎么使用、两大家族、真实环境/会话、Board/Today、飞书三个入口覆盖、相关会议/待复核、卡片预览、Quay结果、日报、已知阻塞。

release manifest 固定所有代码/能力/策略/adapter revision 与 hash、schema 版本和恢复信息。必须有可执行安装/升级/回滚、clean working directory smoke、用户可检查的产物链接。不是只提交长文档或让用户自己拼十个repo。

语义工作台可以复用现有 Web/飞书/CLI；不要求从零实现 IDE。审计细节按需展开，首次体验不需要读所有架构文件。

## 5. 不得偷换的“完整”

全面覆盖 ≠ 所有仓库clean；技能齐全 ≠ 所有正文进prompt；自动推进 ≠ 无限制agent loop；自动清理 ≠ 自动删除录音；全部测试通过 ≠ live可用；设计已批准 ≠所有生产动作已授权。

最终报告固定给实际修改/commit/PR、矩阵逐行状态、外部阻塞、授权待决、rollback证据。所有 REQUIRED 完成前不得写“全体系已上线”；所有可独立完成项也不能因为一个外部阻塞停在 skeleton。
