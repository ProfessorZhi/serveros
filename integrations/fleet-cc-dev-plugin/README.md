# Fleet — the Claude Code development adapter

A **DEVELOPMENT ONLY** Claude Code plugin generated from the same sources as the
ChatGPT first-install package. It exists so the server side can be verified in a
real Claude Code session while the release stays blocked.

```sh
python3 integrations/fleet-cc-dev-plugin/build_cc_plugin.py \
    --out <empty-dir> \
    --next-mcp-url https://<next-ingress>.<tailnet>.ts.net/mcp \
    [--scopes "fleet:read"]
claude --plugin-dir <empty-dir> plugin details serveros-fleet-dev
```

## Two artefacts, one source, and they are not interchangeable

| | this adapter | `integrations/fleet-v1-first-install/` |
|---|---|---|
| output | a **directory**, loaded with `--plugin-dir` | a **ZIP**, installed once |
| brand | none — deliberately unbranded | the two approved PNGs, pinned by SHA-256 |
| binding | `.mcp.json` with `type: http` | `mcp.json` generated from an observed URL |
| plugin id | none | assigned by the client's Plugin Creator |
| blocked by | nothing | `MISSING_BRAND_ASSET`, and an unobserved endpoint |
| status | **DEVELOPMENT_ONLY** | **FINAL ZIP BLOCKED** |

The **Skills are not duplicated**: the generator reads
`integrations/fleet-v1-first-install/plugin/skills/` and writes those bytes into
the plugin. The scope vocabulary comes from the same builder's `FLEET_SCOPES`, so
there is one list rather than two that drift. `tests/test_fleet_cc_dev_plugin.py`
pins the byte-identity, because a test that only counted five directories would
pass on a generator that had re-typed them.

The absence of the approved PNGs blocks the **release build** and nothing else.
It must not be repaired here, and this adapter must never be described as the
release.

## What was verified, on this host

Claude Code **2.1.272**, `--plugin-dir`, from a clean working directory.

| field | value |
|---|---|
| manifest location | `.claude-plugin/plugin.json`, components at the plugin root |
| binding | `.mcp.json` — `{"type": "http", "url": …, "oauth": {"scopes": "…"}}` |

`claude plugin validate` → **Validation passed** (also under `--strict`, with no
warnings). `claude --plugin-dir … plugin details serveros-fleet-dev` → 5 Skills
enumerated by name, **1 MCP server** registered.

### Two real differences from the documentation, measured here

1. **`claude plugin validate` on 2.1.272 does not read `.mcp.json` at all.** A
   deliberately broken binding — a `url` with no `type`, and a non-absolute URL
   — still reports `Validation passed`. The manifest reference states the MCP
   checks require **v2.1.281 or later**, and this host is below that. The
   tests cover the binding's shape because the local validator will not.
2. **`oauth.scopes` is documented, and this version's validator cannot confirm
   it.** The field is in the `.mcp.json` schema (`oauth.scopes`, one
   space-separated string, taking precedence over discovery), but since (1)
   holds, "accepted by this version's validator" would be a claim about a check
   that did not run. Runtime acceptance on 2.1.272 is **UNVERIFIED**.

## Receipts

Each is a separate fact, and none is discharged by another.

| receipt | state | what it rests on |
|---|---|---|
| `LOCAL_CC_PLUGIN_LOADED` | **VERIFIED** | validate passed; `plugin details` listed 5 Skills and 1 MCP server |
| `NEXT_TOOLS_DISCOVERED` | **NOT ACHIEVED — `BLOCKED_BY_OWNER_OAUTH`** | unauthenticated `initialize` and `tools/list` both returned HTTP **401 `invalid_token`** with `www-authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource"`. No token was obtained and no consent was completed, so there is **no tool list** — this is a refusal, not a discovery |
| `SKILL_BEHAVIOR_VERIFIED` | **VERIFIED** | one restricted model call; the seven substantive points were answered from the Skills, and the eighth was answered `UNKNOWN` rather than guessed |
| `DEVICE_ACTIONS_TESTED` | **NOT_RUN** | no create/send/control attempted, and none was possible: no credential, `fleet:read` pinned, and `Task`/`Bash`/`Write`/`Edit` denied to the probe |
| `CORE_MONITORING_VERIFIED` | **NOT_RUN** | the monitoring plane was not read in this round |
| `SELF_HEAL_VERIFIED` | **NOT_RUN — and must not be attempted** | automatic self-healing is V2, and restarting a live channel is forbidden outright (`fleet-safety-boundaries`) |

A mock or a fixture does not stand in for any row marked `NOT_RUN`. The
authenticated `tools/list` is the one that needs the owner: it is an
interactive OAuth consent, and this round did not simulate one.
