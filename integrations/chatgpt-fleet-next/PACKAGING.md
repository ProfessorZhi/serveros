# Fleet Next GPT plugin — packaging, update, and the refresh gates

Status: **SOURCE READY · ZIP BUILDABLE AND REPRODUCIBLE · NOT UPLOADED ·
NOT INSTALLED · NOT CONNECTED.** Nothing in this directory has been given to a
client, and no claim below about a client's behaviour has been measured here.

This file is the package's own notes. It is not a payload member — `build_zip.py`
ships `plugin.json`, `icon.svg`, `README.md` and `skills/`, and nothing else.

## What this artifact is, and what it is not

It is the **cloud-facing** half of Fleet Next: instruction-only Skills that tell
an assistant *when* to reach for a ServerOS device, *how* to check that it is
allowed to, and *how* to tell a delivered message from an accepted result.

It is **not** the Native ServerOS plugin. That composition lives at
`plugin/bundle.yaml`, ships a node runtime, MCP servers and executable entry
points, and is released as `release.py`'s `plugin` artefact kind. Issue #186
draws this line explicitly. Two guards keep it drawn:

- a test asserts the two compositions do not share a `name`
  (`hwz-server-os` vs `serveros-fleet-example`);
- the build refuses a tree containing any member that would declare a live MCP
  surface — `.mcp.json`, `mcp.json`, `.app.json` — **wherever it sits**, and
  refuses a `plugin.json` that declares `mcpServers`.

The refusal covers the whole tree rather than the shipped set on purpose. A
member allowlist cannot see a file it did not select, so a check that only
covered what ships could not distinguish "refused" from "quietly dropped".

## Build

```sh
python3 integrations/chatgpt-fleet-next/build_zip.py --check   # assert reproducibility
python3 integrations/chatgpt-fleet-next/build_zip.py           # write dist/
```

`--check` builds twice and refuses unless both the archive digest and the
content digest agree. Measured on this branch:

```
OK stable: serveros-fleet-example v0.1.0 (6 files)
archive_sha256=c22ebc129ac6c62e388242c2dd9c3c016203539d051ce03352fa8da3665383e3
content_sha256=247c0c916ed05049b7d5730af651c3a1bc5638abf18390087c137792f44eafdc
```

Two digests, because they answer different questions:

| digest | covers | stable across machines? |
|---|---|---|
| `content_sha256` | relative path + bytes of every member, sorted | **yes** — the same recipe `tests/test_plugin_composition.py` uses for the Native artifact |
| `archive_sha256` | the ZIP bytes | **no** — deflate output depends on the linked zlib. Portability is not claimed |

Compare `content_sha256` when two machines must agree. Compare `archive_sha256`
only against another build on the same interpreter.

The member set is `PAYLOAD_ENTRIES`, written down rather than derived. Packaging
"whatever is in the folder" fails on the second run: the first run writes
`dist/`, the second packages the previous archive inside the new one, and the
release identity changes every build — while `--check` still reports green,
because it builds into a scratch directory where none of that exists yet.

## The icon

`icon.svg`, shipped as a file.

**Neither the 1.0.0 manifest schema nor the specification defines an icon.**
The schema's property list is closed (`additionalProperties: false`), so an
`icon` key is refused by a conforming client; the specification names no icon
filename, format or dimension anywhere. Both were read from the published
documents, not inferred. The icon is therefore declared under the schema's
`extensions` namespace (`com.serveros.fleet-next.icon`) and shipped as a file,
and **whether any client reads either declaration is UNVERIFIED.** If the client
requires a raster icon at a specific size, that is a build step with a pinned
renderer and has not been done here.

## Attaching a live MCP

`MCP-INTEGRATION.md` covers this in full. In short: a portable binding goes in
a **separate root file, `mcp.json`** — the specification's own fixed location —
and not in the manifest, which cannot carry it. An OAuth-carrying or app-backed
binding is not expressible in v1 at all. This package ships neither, and
`build_zip.py` refuses `mcp.json`, so adding a binding is a reviewed change
rather than a packaging side effect.


## Updating the same plugin ID

The flow below was **measured once, on a throwaway private plugin**, and is
recorded in PR #185. It is repeated here as the known-good procedure, not as a
result reproduced on this branch.

1. Build a ZIP from a pinned source revision; record `content_sha256`.
2. Upload it to the existing plugin **by ID** — not a new plugin — using the
   guarded update that carries an **expected release ID**. The guard is what
   stops a routine rebuild from replacing a release nobody meant to replace.
3. Read back the plugin's file list and confirm: `plugin_id` unchanged, a new
   `release_id` issued, every Skill that was there before still there, and the
   newly added Skill present.

Measured in #185: `plugin_id` stayed the same, a new `release_id` was issued, the
previous Skill survived and the new one appeared.

What a ZIP update does **not** do: it does not change the live MCP server, it
does not repair a network path, and it does not install anything. **A GitHub push
does not install a private plugin.** Skills and the icon move by plugin
release/update; the tool surface moves by the server's own release.

## The ChatGPT tool-refresh gates

These are the conditions that must hold before anyone may say "the 34 tools are
live in the client". **None of them has been exercised from this worktree**, and
each is marked with what it would take. This is a checklist for the acceptance
run, not a report of one.

| # | Gate | Evidence it needs | State |
|---|---|---|---|
| G1 | The server serves exactly **44** tools at `tools/list` with `TOOL_SURFACE_REVISION == "fleet-mcp-6"` | one captured `tools/list` from the live endpoint | **NOT EXERCISED** (asserted in tests against fakes only) |
| G2 | OAuth: issuer + RFC 9207 `iss`, audience binding, PKCE S256, DCR | a real authorization with a real client | **BLOCKED** — `fleet-mcp-contract.yaml` records `chatgpt_oauth_consent` as requiring a human to complete the consent (account + password + TOTP), which no agent may supply or simulate |
| G3 | The client actually **refreshes** a cached tool list when the surface revision changes | an old client, a surface change, and a before/after tool list | **NOT EXERCISED** — no real client was driven |
| G4 | A read-only grant cannot reach a write verb, end to end through the client | a `fleet:read`-only consent plus an attempted write | **NOT EXERCISED** |
| G5 | Instructions-only Skills produce no fabricated local command | a real prompt against a real device | **NOT EXERCISED** |
| G6 | V1 keeps working while Fleet Next is added | V1's own `tools/list` captured before and after | **NOT EXERCISED** |

Two gates are owned by the human operator and cannot be closed by an agent:
`fleet-mcp-contract.yaml` lists `public_exposure_decision` and
`chatgpt_oauth_consent` under `blocked_on`, and `owner-grant-3devices.md`
records that widening the live token's scopes requires a re-consent, because
scopes are minted into the token at authorization and cannot be widened
afterwards.

## What is not claimed here

- No client was contacted; no upload, install or refresh was performed.
- `https://serveros-fleet.tail3d8b76.ts.net` serving its two OAuth discovery
  documents was observed from a public probe in #185. That is a fact about the
  discovery documents, **not** about tool discovery, authorization or refresh.
- The three Skills are drafts. They have been reviewed for content and packaged;
  they have not been run against a model in a real session.
