# 指挥侧客户端：一个 MCP，四种客户端

> 2026-11-19。指挥侧不绑定某一个客户端：Fleet MCP（`https://fleet-v1.professorzhi.fun/mcp`，
> Streamable HTTP）是唯一入口，各客户端只是不同的「窗口」。服务器不假设客户端有什么能力。

## 1. 四种客户端

| # | 种类 | 例子 | 怎么连 | 认证 | 规矩（Skill）从哪来 |
|---|---|---|---|---|---|
| ① | 通用编码 agent | Claude Code、Codex、Cursor、Gemini CLI、Copilot | 插件 = MCP + `SKILL.md` | OAuth | 插件 / `distribution/mcp/skills/` |
| ② | 带 Skill 的聊天 | ChatGPT、Claude.ai | 连接器 + 插件里的 Skill | OAuth | 插件 1.13.0 |
| ③ | 不带 Skill 的聊天 | Grok（grok.com/connectors → Custom）、Gemini（设置 → Connected Apps → 自定义） | 连接器 | OAuth（含 Client ID Metadata Document） | **服务器给**：`instructions` 指路，`fleet_guide` 按需返回 Skill 正文；可再把要点贴进 Gem / Grok Project 指令 |
| ④ | Muse | Meta Muse | 它在自己的云端虚拟机里写一个 MCP 客户端，存成它自己的 Skill | **OAuth（账号 + 密码 + 验证码）**，与 ChatGPT 同一授权页；走不通再用连接器 token | 同 ③，连上后让它读 `fleet-core` 并存进它的 Skill |
| — | 都不行时 | 任何 | 飞书管家 | 主人飞书身份 | — |

**入口地址**：云端和聊天客户端一律用公网 `https://fleet-v1.professorzhi.fun/mcp`。仓库里
`plugins/serveros-fleet-ingress/.mcp.json` 写的 `*.ts.net` 是 Tailscale 内网地址，只给装在主人自己设备上的
Claude Code 插件用；**不要**让任何云端客户端去连它，更不要把云端机器加进 Tailscale。

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
# 签发（和某个已登录插件一样）：token 只在 stdout 打印一次，其余说明在 stderr
@FLEET_MCP_RUNTIME@/fleet_mcp/bin/serveros-fleet-mcp --config <binding> \
    --mint-token Muse --like <ChatGPT 的 client_id> --days 365

# 查看 / 吊销（和 OAuth 客户端同一张表；吊销立即生效，无需重启）
serveros-fleet-mcp --config <binding> --list-clients
serveros-fleet-mcp --config <binding> --revoke-client token-XXXX
```

- `--scopes` 只能是本部署允许给客户端的那些（默认 `fleet:read`、`fleet:send`），`--days` 1–365，默认 90；没有 refresh，到期重新签发。
- **和 ChatGPT / CC / Codex 插件同等权限，不缩水：用 `--like <那个插件的 client_id>`**。派活、建会话、
  项目权限除了 scope 还看按身份写的 `policy.operator_grants` / `policy.owner_max`；这些插件在授权页
  用账号 + 密码 + 一次性码登录后，身份是 `oauth:<它的 client_id>`。`--like` 让 token 以**同一身份**
  出现、持有该插件**当前那次登录的全部 scope**，所以授权完全相同。
  吊销 Muse（它自己的 `token-…`）不影响那个插件；那个插件被吊销，Muse 也随之失效。
  不加 `--like` 时它是新身份 `oauth:token-…`，只吃 `"*"` 那份授权，scope 用 `--scopes` 指定。
- 签发出的客户端没有回调地址，不能拿它走 OAuth。
- 服务在跑时签发也立即生效（服务器遇到未知的 `token-` 客户端会从共享 state 文件补读）。

## 4. Muse 接入步骤

**默认走 OAuth，和 ChatGPT 一样，不用 token。** 授权页要账号 + 密码 + 6 位验证码三样一起，所以即使在
Muse 的云浏览器里登录、密码经过 Meta 的机器，没有主人手机上的验证器也登不进来。登录后的凭据自动续期。

1. **在 Muse 里对话**（原话即可）：
   > 按 serveros 仓库根目录 AGENTS.md 接 Fleet，地址 `https://fleet-v1.professorzhi.fun/mcp`（streamable HTTP），
   > 不要加 Tailscale。认证走 OAuth：打开授权页，我用账号、密码和验证码登录并勾权限，密码和验证码请弹安全卡片让我填，
   > 不要让我打在对话里。登录成功后先列出工具，再调用 `fleet_guide`（topic = `fleet-core`），把它的内容和工具清单一起存成
   > 一个 Skill，以后用 Fleet 都先按它来。
2. **授权页**：登录，勾和 ChatGPT 插件一样的权限，同意。
3. **在 Muse 设置 → Connectors** 决定这个连接器是「允许」还是「需要审批」（和你对其它插件的习惯一致即可）。
4. **按下面第 5 节验收**。
5. 不要用 Muse 内置的 Zapier 中转接 Fleet：派活会经过第三方，权限和日志不在我们手里。

**备用：OAuth 走不通时**（授权页弹不出来、回调跳不回 Muse、反复要求重新登录），改用第 3 节的连接器 token：
在东京 `--mint-token Muse --like <ChatGPT 的 client_id> --days 365`，stdout 直接重定向进 0600 文件；对 Muse
说认证改为 `Authorization: Bearer <token>`，它弹安全凭据框时粘贴，**不要贴进聊天**，粘完删掉东京上的文件。

## 5. 每个客户端的验收（同一套 6 步）

1. 连接 → 授权页（或 token）显示的客户端名对；只给需要的权限。
2. 「列出设备」→ 三台都在，能分清在线/离线。
3. 「Fleet 里关于 LiteLLM 的记忆」→ 有 `notes` 或 `archive` 命中（③④ 应先调过 `fleet_guide`）。
4. 「让东京只回复 OK」→ 带原话派出（trace 里 `owner request` 有原文），开批次。（只读阶段这一步应被拒为 `SCOPE_DENIED`）
5. 飞书收到完成卡（≤10 s），客户端里不轮询。
6. `fleet_trace` / 指挥日志里能看到这个客户端的名字和每一步。

结果记在 records `projects/serveros/` 的验收记录里：客户端、日期、6 步各自 ✅/❌、失败原文。
