---
name: serveros
description: Use when an agent needs to understand its ServerOS working context, resolve the current Project/Item/worktree/test/evidence environment, avoid conflicting resources, or discover the relevant ServerOS capabilities. Start with `serverosctl context resolve`; specialized work is delegated to native domain Skills.
---

# serveros — ServerOS bootstrap

**ServerOS = Environment + Context + Capabilities.** 本 skill 只做一件事：让 Agent 先确定自己在哪个
project / repo / Item / worktree / evidence 环境里，然后把专业工作交给对应的 native skill。

它**不是**强制 workflow router。普通任务不需要从这里走一遍流程，只需要先 resolve context。

## 1. Environment — 路径不变量

```text
HOME=/home/hwz
DATA_ROOT=/opt/applications/data/home-hwz
WORKSPACE_ROOT=$DATA_ROOT/workspace     # 只含 src/ worktrees/ test-envs/ ops/ knowledge/ records/
ARTIFACTS_ROOT=$DATA_ROOT/artifacts
TOOLCHAINS_ROOT=$DATA_ROOT/toolchains
```

- `artifacts/ toolchains/ envs/ runtime-assets/ caches/ data/ state/` 位于 `DATA_ROOT`，**不在** workspace。
- 禁止创建 `workspace/artifacts`、`workspace/toolchains`、`~/files` 等替代根。
- `project` 用于 records/artifacts/state；`repo` 用于 src/worktrees/test-envs。
- 证据路径：Item → `$ARTIFACTS_ROOT/work-items/<project>/<ITEM>/<RUN>/`；
  无业务 Item 的 skill 产物 → `$ARTIFACTS_ROOT/skills/<skill>/<RUN>/`。
- 越界、`../` 或 symlink 逃逸、目标冲突必须**显式失败**，不得静默回落 HOME/cwd。

## 2. Context — 第一件事永远是 resolve

```bash
serverosctl context resolve <ITEM>   # 例：serverosctl context resolve SRV-0003
serverosctl context resolve          # cwd 能唯一解析时
```

输出 `serveros/context-capsule.v1`：project / repo / Item / worktree / revision / dirty /
test-env / evidence_root / delivery_target / capability_hints。零 LLM、约 0.5s、**不做全域扫描**。

- `evidence_root` 是绝对路径；把它**原样**交给实际写文件的工具，不要自己拼。
- `writable=false` 的 Run 只读；要写入先 `serverosctl context attach`。
- 解析失败就停下来问，**不要**猜一个 Item 继续跑。

## 3. Capabilities — 交给 owner，不要自己造

| 问题 | owner |
|---|---|
| 发生过什么、目前认识到什么 | `information-management` |
| Item / Activity / Run / 交付物 / 记录闭环 | `work-management` |
| 测试环境定义、构建与隔离 | `test-management` |
| 目录、地图、漂移、placement 合规 | `workspace-management` |
| 能力登记、发现、分发到 runtime | `ability-management` |
| 飞书 Wiki/Drive/Minutes 对象放在哪里 | `feishu-space-management` |
| 日报、周报、里程碑报告发布 | `feishu-report` |

ServerOS 只编排关系与交接，**不抢占以上 owner 的数据权威**。除此以外继续使用 Claude / Codex
自己的 native skill discovery——上表是分工，不是白名单。

## 4. 最小纪律

- 不 reset/clean/stash/discard 他人 WIP；不 `git add .`；不 force-push；不代提交受保护仓库。
- 不移动活动 worktree / 容器 / venv / bind mount；未知消费者标 `DEFER`。
- 不把 secret、token、cookie、私钥写进 evidence、state 或地图。
- `state/serveros/serveros.db` 是唯一 machine state；`board/current.{json,md}` 是它的原子生成投影，
  不手改、不提交，也不当第二状态库。
- `serverosd` 目前只是 planned boundary，**没有常驻 daemon**；不要为它引入 cron/watcher。
- 能力发布由 canonical 构建（reusable Skill 源在 `ability-management`），**禁止从 runtime 反向覆盖 canonical**。

---

- 机器 contract：`references/serveros-contract.json` —— 改动路由或路径契约后必须运行
  `python3 scripts/validate_contract.py`。
- 对象模型与生命周期：`references/object-model.md`。
