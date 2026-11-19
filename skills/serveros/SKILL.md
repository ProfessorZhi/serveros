---
name: serveros
description: Resolve missing node, work context, path placement or resource ownership for ServerOS work. Use native tools directly when context and boundaries are already known; load specialists only for the current task.
---

# ServerOS bootstrap

给 Native Agent 补齐当前工作所缺的环境、上下文和能力。原生工具能做且已获授权的工作直接做；
这里的入口按需选择，不是每个任务必须依次经过的流程。不要为普通读写强建 Item、Run 或设计文档。

## 只补缺失的上下文

- 已有当前 project/worktree、根契约和授权范围时直接工作；切换工作对象或信息失效时重新解析。
- 不清楚在哪里时，用 `serverosctl context current`，或 MCP `context.current` 并传实际 `cwd`。
  只需启动摘要时用 `serverosctl context startup`；不同时读摘要、完整 context 和 device。
- `item: UNKNOWN` 不代表普通工作被禁止。无 Item 的明确请求或治理工作继续，证据用 skill 路径；
  不编造 Item。只有写 Item 状态、接续已有 Item 等依赖该身份的操作才先查明归属。
- 仅当需要本机资源规格时读 `serverosctl device show`。解析缺失只阻塞依赖它的操作。

## 写入位置与资源边界

根值来自本机 profile/`serverosctl path contract`，不要照抄别的设备或发明替代根。
新 durable 位置先 `serverosctl path resolve <role>` / `serverosctl path admit <absolute-path>`；
已准入位置内的普通编辑无需重复准入。路径准入只回答 placement，不授予写权限或资源所有权。

- `WORKSPACE_ROOT` 只含 `src/ worktrees/ test-envs/ ops/ knowledge/ records/`；代码修改用 worktree。
- `artifacts/ toolchains/ envs/ runtime-assets/ caches/ data/ state/ logs/` 在 `DATA_ROOT`，不在 workspace。
- Item 证据：`ARTIFACTS_ROOT/work-items/<project>/<ITEM>/<RUN>/`；无 Item：`ARTIFACTS_ROOT/skills/<skill>/<RUN>/`。
- 拒绝 `../`、symlink 逃逸与 HOME/cwd 静默回落；不得创建 `workspace/artifacts`、`workspace/toolchains`、
  `~/files` 或 `legacy/ old/ backup/ compat/` 替代根。已有误放目录也不继续写。
- machine state 只在 `DATA_ROOT/state/serveros/`；`~/.serveros` 只放 identity/binding。

操作长命资源前读 `serverosctl runtime status <id>`；需要原因再 `serverosctl runtime explain <id>`。
跨资源整理才读 `serverosctl runtime hygiene`，并使用 `runtime-management`。
owner 依据 registry 或显式 `serveros.owner=<type>:<id>`，不把 compose 路径标签当 owner。
未知 owner/消费者 = DEFER；protected、ACTIVE、resumable waiting、active lease、`LIT-0103`、`LIT-0104`
及本机政策指定的资源不可擅动。清理、搬迁、重命名前重新取活动快照，清理须显式批准。
process/transport 存在不代表健康；`functional: UNKNOWN` 不得写成 HEALTHY。

## 保留的硬边界

- 不 reset/clean/stash/discard 他人 WIP；不 `git add .`；不 force-push；不代提交受保护仓库。
- 不移动活动 worktree/容器/venv/bind mount；磁盘压力只 observe/explain/recommend，永远不自动删未知内容，
  不 `docker system prune`，不 `docker volume prune`。系统盘与数据盘分开报。
- 不输出或记录 credential/token/cookie/私钥。工具可见、路径准入、网络可达都不扩大授权。

仅在问题涉及契约细节时读 `references/serveros-contract.json`、`references/object-model.md`；
架构一致性用 `serverosctl architecture check`，地图候选用 `serverosctl architecture project --out <dir>`。
后者不原地覆盖地图；check 的退出 2 是无法回答。产品架构只在需要时读仓库 `ARCHITECTURE.md`。

## More detail (read only when the task needs it)

- `references/02-按需发现能力.md` — 按需发现能力
- `references/03-只加载本次需要的-owner.md` — 只加载本次需要的 owner
