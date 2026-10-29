# Fleet V1 总跟踪：三设备 × 四部分 + 一次真实自迭代

> **状态：** 草稿，待 owner 确认后作为 GitHub issue 发布，替代 #434、#425、#515、#478 四个总收口 issue。
> **这篇回答什么：** V1 做完长什么样、每一格用什么证据算通过、现在每一格在哪、谁来做下一步。
> **不回答什么：** 实现细节（在各模块 README 和 `docs/plans/fleet-v1-delivery-plan.md`）。

---

## 1. V1 是什么

V1 = 在 **Tokyo（vince）、Dell（fjd）、FJPCOS Windows（work-fj）** 三台公司设备上，四部分都真机通过，并且**完成至少一次真实的自迭代**。

| # | 部分 | 一句话 | 代码位置 |
|---|---|---|---|
| ① | 设备 Ability 家族 | 每台设备上的 agent 装着哪些 Skill、遵守哪版规范（CLAUDE.md / AGENTS.md），可读回、可改、可回滚 | `ability-management`（registry、`control_plane.py readback/rollout`）、`abilities/` |
| ② | CloudCLI 指挥中心 | 指挥侧通过 Fleet MCP 控制各设备、各 agent 的原子能力 | `fleet_mcp/`、`fleet_mcp/node/` |
| ③ | serverosd 监控 + 播报 | 每台设备的 C++ 资源与服务监控；agent 任务完成后主动播报 | `native/node-core/`（core-lite、supervisor）、`integrations/fleet-next-agent-monitor/` |
| ④ | 指挥侧插件与 Skill 族 | 指挥侧照着 Skill 就能正确发现、派发、验收、改能力 | `integrations/fleet-v1-first-install/plugin/skills/` → `plugins/serveros-fleet-ingress/` |

**自迭代**：从指挥侧用 `fleet-ability-change` 发起一次规范修改，经 ability-management PR 合并得到 rev，在三台设备上 rollout，readback 一致，新会话行为验证通过，并实际演练一次回滚。

**不属于 V1（V2）：** 自治组织、角色团队、强弱模型自动调度、Goal Supervisor 自主派发、飞书到交付的全自动链路。

## 2. 发布形式（待 owner 决定）

- [ ] **推荐：** V1 = main 上的一个 tag + 各设备现有安装脚本（`fleet_mcp/node/install.sh` / `install.ps1`、`control_plane.py rollout`）。
- [ ] 独立构件、Sidecar 事务安装、可复现 digest（#506 #509 #513 #518 #525 #526 #529）推到 V1.1，不挡 V1。

## 3. 验收矩阵

每一格只有在贴出**设备侧回执**（JSON 或命令原始输出 + 命令本身 + 时间）后才能勾选。代码存在 ≠ 已安装 ≠ 真机通过。

| 设备 / runtime | ① Ability 家族 | ② 原子能力 | ③ 监控 + 播报 | ④ 指挥侧 Skill |
|---|---|---|---|---|
| Tokyo `device:tokyo-shared-dev-01` · `claude`（vince） | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Dell `device:llm-lab-dell` · `claude-fjd` | NOT_RUN | NOT_RUN | NOT_RUN | — |
| Windows `device:work-fj` · `claude` | NOT_RUN | NOT_RUN | NOT_RUN | — |
| **自迭代（三台）** | NOT_RUN | | | |

④ 是指挥侧的事，只验一次；它的验收在 Tokyo 一行里做。

### ① Ability 家族：每台设备

- [ ] ability-management 里有这台设备的 profile（目前只有 `hwz-server`，三台设备的 profile 都还没建）
- [ ] 设备上 `control_plane.py readback --profile <p> --host-profile <hp>` 输出 `result: CONVERGED`，`registry.head` 为预期 rev
- [ ] 通过 Fleet（`fleet-ability-inventory`）拿到同一份回执，而不是登录设备手跑
- [ ] hooks：回执标 `NOT_MANAGED`；V1 接受这一点，但要写进发布说明

### ② 原子能力：每台设备 × runtime，走 Next ingress

- [ ] `list` `read` `create` `send` `get_run` `interrupt` `resume` `recover_after_disconnect` 全部 PASS
- [ ] `approval` `reconfigure` `close`：PASS，或 `UNSUPPORTED` + provider 能力回执
- [ ] create 后 `permission_readiness.state = CONFIRMED`；无人值守 run 的 `prompt_risk` 为 `NONE`
- [ ] 断线后用原 `session_ref` / `run_id` 能续上，不重复派发

### ③ 监控 + 播报：每台设备

- [ ] core-lite 以 observe-only 运行；`fleet_status` 的 `core_lite` 块有新鲜读数（CPU / RAM / Swap / PSI / 磁盘 / 服务健康；Dell 另有 GPU 显存摘要），读不到的字段是 `UNKNOWN` 而不是 0
- [ ] 一个 agent run 完成后，飞书收到**一条**真实播报（不重复）；失败为红色并可操作；`UNKNOWN` 不报成功
- [ ] 监控不增加 MCP 工具，不授予任何执行权限

### ④ 指挥侧 Skill：一次

- [ ] 插件以固定 Plugin ID 安装；`tools/list` 读回
- [ ] 11 个 Skill 全部可被加载（A 发现 · B 派发执行 · C 验收 · D 能力与规范 · E 自身维护）
- [ ] 按 Skill 完成一次端到端：派发 → ACK → 立即返回 → 下一轮读回 → `fleet-evidence-review` 验收

### 自迭代：一次，覆盖三台

- [ ] `fleet-ability-change`：ability-management PR 合并，得到 `REV`
- [ ] 三台设备依次 dry-run → apply → `APPLIED`
- [ ] 每台在新会话里跑检查任务通过（`fleet-norm-audit`）
- [ ] 在一台设备上演练回滚到上一个 rev，readback `CONVERGED`

## 4. 推进顺序

1. 冻结源码：合并 #528、#523，打 `v1-rc1` tag。之后只接受修真机问题的 PR。
2. **Tokyo** 四部分全部打通，含一次自迭代 rollout 和一次回滚。
3. **Dell fjd**：照抄 Tokyo；新增第二个 runtime 和 GPU 读数。
4. **Windows**：补 `install.ps1` 的 create/control 字段，core-lite 走 Windows 适配。
5. 三台都过：自迭代三台一起验收，发布 V1。

## 5. 只有 owner 能做的事

- [ ] 新 Tailscale 节点：登录、MFA、Funnel 审批
- [ ] Next ingress 的真实 FQDN
- [ ] 注册 App 和 Plugin ID；品牌 PNG
- [ ] 飞书 app-2 回调；授权 Tokyo 发一条真实播报
- [ ] Tokyo live binding 从 `send_allow: ['*']` 迁移到 `operator_grants`
- [ ] 轮换暴露过的 CloudCLI 凭据（#152）

## 6. 发布前必须关闭的阻塞 issue

- [ ] #152 轮换暴露的 CloudCLI 凭据
- [ ] #171 MCP / OAuth token 不进 argv 和监控日志
- [ ] #237 session fencing / 幂等 / 独占租约：对照代码核查，仍成立则留作 ② 的阻塞项
- [ ] #306 租约 TTL / TOCTOU：同上

## 7. 开放 PR 的处理（建议，待 owner 确认）

| 处理 | PR |
|---|---|
| 合并 | #528、#523 |
| 关闭（被 #523 取代） | #511 |
| 推到 V1.1（Draft 或关闭，分支保留） | #529、#506、#513、#525、#509、#518、#526、#505、#431 |
| 二选一：留 #527，关 #522 | #522 / #527 |
| 关闭，只用来摘取内容 | #507、#508 |
| QA 改为评审意见，关闭 PR | #517、#524 |
| 关闭，标 `v2` | #407、#473、#512 |
| 保留，挂 ③ 播报 | #460 |
| 只摘安全部分，挂 #171 | #474 |
| 核查后决定 | #428 |

## 8. 协作规则

- 一个改动只开一个 PR；QA 写成评审意见，不单独开 PR。
- 不再新开只改台账或文档的 PR。
- 每个 PR 必须对应本页某一格；对应不上就不开。
- 本页是 V1 唯一的跟踪入口；各格的回执以评论形式贴在 issue 上，并回填本页状态。
