# 指挥侧客户端：一个 MCP，多种客户端

> 2026-11-19。指挥侧不绑定某一个客户端：Fleet MCP（`https://fleet-v1.professorzhi.fun/mcp`，
> Streamable HTTP + OAuth）是唯一入口，各客户端只是不同的「窗口」。

## 1. 三类客户端

| 类 | 客户端 | 怎么接 | Skill 从哪来 |
|---|---|---|---|
| 编码 agent | Claude Code、Codex、Cursor、Gemini CLI、Copilot … | MCP + `SKILL.md`（Agent Skills 标准，各家基本统一） | 插件 / `distribution/mcp/skills/` |
| 能装 Skill 的聊天 | ChatGPT、Claude.ai | 自定义连接器（MCP）+ 插件里的 Skill | 插件 1.13.0 |
| 只认 MCP 的聊天 | Grok（grok.com/connectors → Custom）、Gemini（设置 → Connected Apps → 自定义）、Muse（未证实，先试） | 自定义连接器（MCP） | **服务器自己给**：`instructions` 指路，`fleet_guide` 按需返回 Skill 正文 |
| 没有 MCP 的 | 任何 | 飞书管家 | — |

## 2. 服务器为此做的事

- **Skill 走 MCP**：`fleet_guide` 不带参数列出主题，带 `topic` 返回该 `SKILL.md` 正文（与插件里是同一份文件）。
  服务器 `instructions` 开头就告诉客户端：没装 Skill 就先读 `fleet-core`。
- **协议版本**：接受 `2025-11-25`（较新的客户端发它）；不认识的版本仍回落到 `2025-06-18`；不带
  `MCP-Protocol-Version` 头的请求照常处理。
- **客户端身份**：除动态注册外，接受 **Client ID Metadata Document**——客户端用一个 https URL 当
  `client_id`，服务器取那份文档（只取公网 https、不跟跳转、≤5 KB、`client_id` 必须等于 URL、
  回调地址同样要 https 或本机）。授权页与指挥日志按客户端自报的名字区分。
- **按客户端给权限**：同意页逐项勾选。新客户端建议先只给 `fleet:read`，验收通过再加 `fleet:send`。

## 3. 每个客户端的验收（同一套 6 步）

1. 添加连接器 → 授权页显示的客户端名对；只勾需要的权限。
2. 「列出设备」→ 三台都在，能分清在线/离线。
3. 「Fleet 里关于 LiteLLM 的记忆」→ 有 `notes` 或 `archive` 命中（只认 MCP 的客户端应先调 `fleet_guide`）。
4. 「让东京只回复 OK」→ 带原话派出（trace 里 `owner request` 有原文），开批次。
5. 飞书收到完成卡（≤10 s），客户端里不轮询。
6. `fleet_trace` / 指挥日志里能看到这个客户端的名字和每一步。

结果记在 records `projects/serveros/` 的验收记录里：客户端、日期、6 步各自 ✅/❌、失败原文。
