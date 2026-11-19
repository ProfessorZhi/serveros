# For an agent reading this repository

## Connecting a client to Fleet (you want to drive the owner's devices)

- **Endpoint:** `https://fleet-v1.professorzhi.fun/mcp` — public HTTPS, MCP streamable HTTP.
  Use this from any cloud or chat client (ChatGPT, Claude, Grok, Gemini, Meta Muse).
- **Do not** use `*.ts.net` addresses (e.g. `serveros-fleet-next.tail3d8b76.ts.net`): those are the
  owner's private Tailscale network, used only by plugins on the owner's own enrolled devices.
  **Never ask to add a cloud machine to the Tailscale network.**
- **Auth:** OAuth on the Fleet consent page — the owner signs in with account, password and a one-time
  code and ticks the scopes, the same as for the ChatGPT plugin (Muse included). Ask for the password
  and code through your **secure credential prompt**, never in the chat. Only if OAuth cannot complete:
  `Authorization: Bearer <connector token>`, which the owner mints on the hub
  (`serveros-fleet-mcp --config <binding> --mint-token <Name> --like <ChatGPT client_id> --days 365`)
  and pastes into your secure credential prompt.
- **First call:** `fleet_guide` with `topic: fleet-core`, and follow it (owner's words as
  `owner_request`, batches, results arrive as Feishu cards — do not poll).

Full guide, the four kinds of client and Muse's steps: [docs/architecture/commander-clients.md](docs/architecture/commander-clients.md).

## Working on this code

See [README.md](README.md) (tests: `scripts/hermetic_tests.py`) and [ARCHITECTURE.md](ARCHITECTURE.md).
