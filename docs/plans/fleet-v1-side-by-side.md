# Fleet V1 · 新旧并存部署（Tokyo）

> 状态：运行手册，2026-11-03。目的：V1（main `f29ba7e` 之后）在 Tokyo 上线试用，**现在在用的旧版完全不动**：
> GPT 继续用旧连接器，Claude Code 的旧插件继续可用，Legacy 0.1.0 与 Next 0.2.0 两个服务不停、不重启。
> 新版作为**第二套**并排运行，验收通过后再决定是否切换。

## 0. 并存的样子

| | 在用（不动） | V1 候选（新增） |
|---|---|---|
| 代码 | 现有 checkout | 新 checkout：`~/src/serveros-v1`（main） |
| Fleet 服务 | Legacy 0.1.0 + Next 0.2.0，现有端口 | 第二个实例：`bin/serveros-fleet-owned --config fleet-v1.yaml`，端口 3411 |
| OAuth 状态 | 现有目录 | 单独目录（`server.oauth_store_root`），两个签发方不共用密钥 |
| 对外地址 | 现有 `…/mcp` | 第二个地址，如 `https://serveros-fleet-v1.<tailnet>.ts.net/mcp`（Tailscale / Funnel，owner 配置） |
| Claude Code 插件 | `serveros-fleet-ingress@serveros` 1.0.1 | `serveros-fleet-v1@serveros-v1` 1.2.0（`install-side`，服务名 `serveros-fleet-v1`） |
| GPT | 现有连接器 | 新增第二个连接器 “ServerOS Fleet V1”，指向第二个地址 |
| 监控 | — | 常驻监控（journal 模式）+ core-lite 发布（只观测） |

两套的工具名前缀不同（MCP 服务名不同），同一个会话里可以同时加载，互不覆盖。

## 1. 新 checkout

```bash
git clone https://github.com/ProfessorZhi/serveros ~/src/serveros-v1
cd ~/src/serveros-v1 && git checkout main && git rev-parse --short HEAD
python3 scripts/fleet_plugin.py check          # committed plugin matches its source
```

## 2. 第二个 Fleet 实例

复制现有 Next 的 binding 为 `fleet-v1.yaml`（凭据文件路径照旧引用，不复制密钥），只改下面这些：

```yaml
server:
  bind: 127.0.0.1
  port: 3411                                   # 与现有实例不同
  public_base_url: https://serveros-fleet-v1.<tailnet>.ts.net
  oauth_store_root: ~/.local/state/serveros/fleet-v1/oauth   # 单独的 OAuth 状态
inbox:
  path: ~/.local/state/serveros/fleet-v1/inbox.sqlite        # 中枢收件箱（监控写、fleet_inbox 读）
work:
  state_dir: ~/.local/state/serveros/fleet-v1/work           # 需求看板
  records_root: <records 仓库 checkout>                       # 项目理解（可后补）
core_lite:
  snapshot_path: ~/.local/state/serveros/core-lite/status.json
```

启动（建议 systemd user unit，名字与现有的不同，例如 `serveros-fleet-v1.service`）：

```bash
cd ~/src/serveros-v1 && bin/serveros-fleet-owned --config ~/.config/serveros/fleet-v1.yaml
```

用 `bin/serveros-fleet-owned` 启动，派发去重走 ServerOS 自己的 Agent Control 存储（`idempotency_durability: AGENT_CONTROL_STORE`），
重启后同一幂等键重发也不会重复派发。

验证：`curl -s http://127.0.0.1:3411/health` 返回 `ok`；现有实例的 `/health` 前后不变。

## 3. 对外地址（owner）

在 Tailscale 上为 3411 配第二个地址（新的 Funnel 主机名或端口），路径保持 `/mcp`。现有地址不动。

## 4. Claude Code：并排安装新插件

```bash
cd ~/src/serveros-v1
python3 scripts/fleet_plugin.py install-side --next-mcp-url https://serveros-fleet-v1.<tailnet>.ts.net/mcp
```

只安装 `serveros-fleet-v1@serveros-v1`（生成在 `~/.cache/serveros/fleet-plugin-side/`），不卸载、不改旧插件；
OAuth 回调端口与旧插件不同。重开 Claude Code，新会话里应看到：旧工具（服务名 `serveros-fleet`）和新工具
（服务名 `serveros-fleet-v1`，50 个，含 `fleet_inbox`、`fleet_work_board`、`fleet_monitor_status` 等）同时在。

撤销：`claude plugin uninstall serveros-fleet-v1@serveros-v1`，再 `claude plugin marketplace remove serveros-v1`。

## 5. GPT：第二个连接器

在 GPT 里新增连接器 “ServerOS Fleet V1”，URL 填第二个地址的 `/mcp`，按授权页登录。旧连接器保留。
试用时在对话里明确说用哪个；验收通过前，日常指挥仍走旧连接器。

## 6. 监控与读数（只观测）

1. core-lite：按 `native/node-core/core-lite/deploy/README.md` 编译并启用 `serveros-core-lite-publish.timer`；
   新实例的 `fleet_status` 中 Tokyo 的 `core_lite.connected` 应为 `CONNECTED`。
2. 常驻监控：

   ```bash
   cd ~/src/serveros-v1
   PYTHONPATH=.:integrations/fleet-next-agent-monitor:integrations/fleet-hub-watch \
     python3 -m fleet_hub_watch --config ~/.config/serveros/fleet-v1.yaml --notify journal
   ```

   用新连接器调 `fleet_monitor_status` 看到 `watcher.state: RUNNING`；`fleet_monitor_control` 发
   `send_daily_now`，在 `notify.journal.jsonl` 里看到日报卡 JSON。确认无误后再改 `--notify lark:user:<open_id>`。

## 7. 验收后切换（另行决定）

V1 候选通过 #531 的四部分后：GPT 改用新连接器、旧插件卸载、新插件改用正式安装（`install`）、旧服务停用。
在那之前两套并存，旧的不动。
