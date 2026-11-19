# 指挥侧客户端：一个 MCP，四种客户端

> 2026-11-19。指挥侧不绑定某一个客户端：Fleet MCP（`https://fleet-v1.professorzhi.fun/mcp`，
> Streamable HTTP）是唯一入口，各客户端只是不同的「窗口」。服务器不假设客户端有什么能力。

## 1. 四种客户端

| # | 种类 | 例子 | 怎么连 | 认证 | 规矩（Skill）从哪来 |
|---|---|---|---|---|---|
| ① | 通用编码 agent | Claude Code、Codex、Cursor、Gemini CLI、Copilot | 插件 = MCP + `SKILL.md` | OAuth | 插件 / `distribution/mcp/skills/` |
| ② | 带 Skill 的聊天 | ChatGPT、Claude.ai | 连接器 + 插件里的 Skill | OAuth | 插件 1.13.0 |
| ③ | 不带 Skill 的聊天 | Grok（grok.com/connectors → Custom）、Gemini（设置 → Connected Apps → 自定义） | 连接器 | OAuth（含 Client ID Metadata Document） | **服务器给**：`instructions` 指路，`fleet_guide` 按需返回 Skill 正文；可再把要点贴进 Gem / Grok Project 指令 |
| ④ | Muse | Meta Muse | 它在自己的云端虚拟机里写一个 MCP 客户端，存成它自己的 Skill | **连接器 token**（Bearer） | 同 ③，连上后让它读 `fleet-core` 并存进它的 Skill |
| — | 都不行时 | 任何 | 飞书管家 | 主人飞书身份 | — |

四种用的是**同一个服务器、同一套工具、同一份 Skill 文件**；差别只在「怎么连」「怎么认证」「规矩怎么到它手里」。

## 2. 服务器为此做的事

- **一次调用就返回**：派活只回收据，结果由东京发飞书卡；聊天客户端一轮结束也不丢事。
- **Skill 走 MCP**：`fleet_guide` 不带参数列主题，带 `topic` 返回该 `SKILL.md`（与插件同一份文件）。
- **协议**：接受 `2025-11-25`；不认识的版本回落 `2025-06-18`；不带版本头也照常处理。
- **认证三种，都是「一个客户端一份凭据」**：
  - OAuth 动态注册（①②③）；
  - Client ID Metadata Document：客户端用 https URL 当身份，服务器取文档（只取公网 https、不跟跳转、≤5 KB、身份必须等于 URL）；
  - **连接器 token**（④，以及其它只收 API key 的应用）：主机上签发，按权限、会过期、可吊销，见下。
- **主人 PAT 永不交给任何客户端**：它权限全开，还是授权页的钥匙。
- **按客户端给权限**：新客户端先只给 `fleet:read`，验收通过再加 `fleet:send`。指挥日志、`fleet_trace` 里都记客户端名字。

## 3. 连接器 token（在东京 hub 上）

```bash
# 签发：token 只在 stdout 打印一次，其余说明在 stderr；不落盘、不能再查看
@FLEET_MCP_RUNTIME@/fleet_mcp/bin/serveros-fleet-mcp --config <binding> \
    --mint-token Muse --scopes fleet:read --days 90

# 查看 / 吊销（和 OAuth 客户端同一张表；吊销立即生效，无需重启）
serveros-fleet-mcp --config <binding> --list-clients
serveros-fleet-mcp --config <binding> --revoke-client token-XXXX
```

- `--scopes` 只能是本部署允许给客户端的那些（默认 `fleet:read`、`fleet:send`），`--days` 1–365，默认 90；没有 refresh，到期重新签发。
- 签发出的客户端没有回调地址，不能拿它走 OAuth。
- 服务在跑时签发也立即生效（服务器遇到未知的 `token-` 客户端会从共享 state 文件补读）。

## 4. Muse 接入步骤

1. **在东京签只读 token**：上面的 `--mint-token Muse --scopes fleet:read --days 90`。把 stdout 那一行拿在手里，**不要贴进任何聊天**。
2. **在 Muse 里对话**（原话即可）：
   > 帮我建一个自定义连接器，叫 ServerOS Fleet。它是远程 MCP 服务器，地址 `https://fleet-v1.professorzhi.fun/mcp`，
   > 传输是 streamable HTTP，认证是 `Authorization: Bearer <token>`。token 我会在安全输入框里给你，不要让我贴在对话里。
   > 连上以后先列出工具，再调用 `fleet_guide`（topic = `fleet-core`），把它的内容和工具清单一起存成一个 Skill，以后用 Fleet 都先按它来。
3. **Muse 弹出安全凭据框时**粘贴 token（它存进 Secure Credentials Store，由网关在出网时注入，agent 本身拿不到明文）。
4. **在 Muse 设置 → Connectors** 把这个连接器设成「需要审批」（派活类操作每次确认）。
5. **按下面第 5 节验收**。只读通过后，签一个带 `fleet:send` 的新 token 替换，旧的 `--revoke-client` 吊销。
6. 不要用 Muse 内置的 Zapier 中转接 Fleet：派活会经过第三方，权限和日志不在我们手里。

## 5. 每个客户端的验收（同一套 6 步）

1. 连接 → 授权页（或 token）显示的客户端名对；只给需要的权限。
2. 「列出设备」→ 三台都在，能分清在线/离线。
3. 「Fleet 里关于 LiteLLM 的记忆」→ 有 `notes` 或 `archive` 命中（③④ 应先调过 `fleet_guide`）。
4. 「让东京只回复 OK」→ 带原话派出（trace 里 `owner request` 有原文），开批次。（只读阶段这一步应被拒为 `SCOPE_DENIED`）
5. 飞书收到完成卡（≤10 s），客户端里不轮询。
6. `fleet_trace` / 指挥日志里能看到这个客户端的名字和每一步。

结果记在 records `projects/serveros/` 的验收记录里：客户端、日期、6 步各自 ✅/❌、失败原文。
