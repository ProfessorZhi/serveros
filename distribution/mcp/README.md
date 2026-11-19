# ServerOS Fleet 1.13.0 — for any MCP agent

1. Add the server in `mcp.json` to your agent's MCP configuration: streamable HTTP at `https://fleet-v1.professorzhi.fun/mcp`.
2. On first use the agent opens the Fleet consent page; sign in with your account, password and one-time code, and tick only the permissions it needs.
3. Give the agent the Skills in `skills/` (each is one `SKILL.md`), in whatever form it loads instructions from. A chat app that cannot load Skills (Grok, Gemini, …) needs nothing more: the server tells it to read them with the `fleet_guide` tool.

Health: https://fleet-v1.professorzhi.fun/health
