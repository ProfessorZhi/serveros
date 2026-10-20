# Binding a live Fleet MCP to this plugin — what the spec allows, and what is UNKNOWN

Status of this document: **read from the published specification on 2026-10-14,
not from memory.** Where the specification is silent the gap is named as UNKNOWN
rather than filled in. Nothing here was tested against a client, and no client
was contacted.

Sources read:

- `https://agent-plugins.org/specification` — the normative specification text.
- `https://agent-plugins.org/schemas/1.0.0/plugin.schema.json` — the manifest
  schema, fetched and parsed (`properties`, `required`, `additionalProperties`).
- `https://github.com/agentplugins/agent-plugins-spec` — README (non-normative).
- This repository: `plugin/bundle.yaml`, `scripts/build.py`, and PR #185's lab.

## The short answer

**This package is instruction-only on purpose, and it is a complete, conforming
1.0.0 plugin as it stands.** A live Fleet MCP is bound by a **separate file at
the package root, `mcp.json`** — *not* by a manifest key. This package ships no
such file, and its builder refuses one, so adding a binding is a deliberate act
that shows up in a diff rather than a side effect of packaging.

An "app-backed" or OAuth-carrying binding is **not expressible in v1 at all**:
the specification states that v1 "defines no OAuth configuration or portable
credential-reference fields". That is not a gap in this repository's knowledge,
it is a stated boundary of the format.

## What the specification fixes (quoted)

**Package layout.** `plugin.json` is required at the plugin root. Two optional
components have fixed locations:

| component | location | note |
|---|---|---|
| Skills | `skills/` | "Subdirectories containing `SKILL.md`" |
| MCP servers | `mcp.json` | JSON configuration |

Skills are **immediate children** — the spec says clients "MUST NOT recursively
search deeper descendants", so a skill one level deeper is not a skill. Missing
fixed locations are not an error. Client-specific extension files go in a
top-level directory named for a reverse-domain namespace (e.g. `com.example.client/`).

`plugin.json` **cannot** override these locations or carry inline component
configuration.

**`mcp.json`.** Top level has exactly `$schema` and `mcpServers`, "with no other
top-level fields"; `$schema` must be
`https://agent-plugins.org/schemas/1.0.0/mcp.schema.json`. Each server MUST
carry `type` and match exactly one closed variant:

| `type` | required | optional |
|---|---|---|
| `stdio` | `command` | `args`, `env`, `cwd` |
| `streamable-http` | `url` | `headers` |
| `sse` | `url` | `headers` |

Three constraints bear directly on this repository:

- **Transport security.** "Non-loopback endpoints MUST use HTTPS. HTTP MAY be
  used when the URL host is exactly `localhost` or an IP literal in a loopback
  range." The Fleet MCP binds `127.0.0.1:3401` — loopback, so HTTP is legal
  *locally* — and a remote client reaches it through the tailnet hostname, which
  is non-loopback and therefore **must** be HTTPS.
- **No credentials in the package.** Header values are "not a portable secret
  mechanism", and clients "MUST NOT forward configured headers to a different
  origin without explicit user authorization". A Fleet bearer token does not
  belong in `mcp.json`.
- **No env-var expansion.** `url`, header names and header values are taken
  literally, so a placeholder like `${FLEET_TOKEN}` would be sent verbatim.

**OAuth.** "Agent Plugins v1 defines no OAuth configuration or portable
credential-reference fields." Authorization discovery, user interaction and
credential storage are **client-managed**, and an authorization failure is
treated as a *connection* failure rather than an invalid configuration.

**The manifest.** The closed property set is `$schema`, `name`, `version`,
`description`, `author`, `homepage`, `repository`, `license`, `keywords`,
`extensions`, with `additionalProperties: false`. There is **no `mcpServers`
key**: a manifest carrying one is invalid and must be reported and ignored.
`version` is optional metadata, and Semantic Versioning is only RECOMMENDED —
clients "MUST NOT reject a manifest solely because `version` is not valid
Semantic Versioning".

## The two integration paths, and which one this repository can take

### Portable — expressible, and deliberately not taken yet

Add `mcp.json` at the package root:

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "serveros-fleet": {
      "type": "streamable-http",
      "url": "https://<FLEET_PUBLIC_HOST>/mcp"
    }
  }
}
```

`https` because the host is not loopback. **This is not shipped**, and that is a
decision rather than an oversight: `build_zip.py` refuses a package containing
`mcp.json`, so the file's arrival is a reviewed change with a stated
justification, not something packaging could introduce quietly. The two
conditions under which it should be added are the two `blocked_on` entries in
`fleet/fleet-mcp-contract.yaml`: `public_exposure_decision` and
`chatgpt_oauth_consent`. Both are the operator's.

### App-backed / credential-carrying — not expressible in v1

There is no v1 mechanism for it. The specification defines no OAuth fields and
no credential reference; it explicitly calls header values unsuitable as a
secret channel; and it names no app manifest, no connector registration, and no
`.app.json`. **UNKNOWN:** whether any particular client offers an out-of-band
connector flow that a package can be associated with, and under what rules.
Nothing in the specification answers this, and answering it would require the
client's own documentation — which has not been read here.

`PACKAGING.md` therefore keeps its refresh gates marked NOT EXERCISED. A ZIP
that cannot carry the binding cannot close a gate about the binding.

## Icons

**Neither the specification nor the manifest schema defines an icon.** There is
no specified filename, format, dimension, or `plugin.json` field; the field list
is closed and `additionalProperties` is `false`, so an `icon` key is invalid and
must be ignored.

This package ships `icon.svg` as a plain file and declares it under the
`extensions` namespace, which the spec permits but "assigns no semantics to".
**UNKNOWN:** whether any client reads either. Treat the icon as a file whose
usefulness is unverified, not as a conforming declaration.

## What is statically verifiable today, and what is not

`tests/test_fleet_next_mcp_binding_contract.py` asserts the checkable half:

- the manifest's `$schema` is the 1.0.0 URL and its key set is within the closed
  list, so a future edit cannot add an inline MCP or icon field unnoticed;
- `skills/` holds only immediate children, each a directory with a `SKILL.md`,
  because deeper nesting is invisible to a conforming client;
- the package ships no `mcp.json` **and** the builder refuses one — so the
  deliberate absence and the enforced absence cannot drift apart;
- the manifest declares no `mcpServers`, which the schema forbids anyway.

Not verifiable here, and not claimed: that a client loads this package, that it
discovers the skills, that it renders the icon, that it refreshes a tool list,
and whether a client-side connector flow exists. Each is a client behaviour, and
no client was contacted.

## A note on the builder being stricter than the spec

`build_zip.py` rejects a non-SemVer `version` and requires at least one skill.
The specification requires neither — SemVer is RECOMMENDED and skills are
optional. Those are this repository's own release rules, and they are stricter
than the format on purpose: a package built here is meant to be releasable and
reviewable, and a version that cannot be ordered is neither. Recorded so the
difference is not mistaken for a conformance claim.

## The forms, and what each one's provenance actually is

Several file layouts are called "the plugin manifest" in conversation. They are
not the same thing, they do not come from the same authority, and two of them
have no authority at all — so they are listed separately, with the source this
repository actually has for each.

| form | defined by | lives | what this repository's evidence is | status |
|---|---|---|---|---|
| `.claude-plugin/plugin.json` | the layout `scripts/build.py` emits for Claude | inside the **Native** artifact | `build.py::claude_plugin_manifest`; `release.py`'s `REQUIRED_MEMBERS[PLUGIN]` names it | real — **for `hwz-server-os`, not for this package** |
| `.claude-plugin/marketplace.json` | same | inside | `build.py::claude_marketplace_manifest` | real, same artefact |
| `.codex-plugin/plugin.json` | **not established by this repository** | inside | `build.py::codex_plugin_manifest` *generates* it; `docs/claude-codex-parity.md` states a generated manifest is not installation proof, and that a `.codex-plugin` directory is not a supported format merely because `.claude-plugin` exists | **generated; client support UNKNOWN** |
| `.mcp.json` / `.codex-mcp.json` | this repository's own convention | package root | `build.py` emits them only when the bundle declares servers | real — for the **Native** plugin |
| `mcp.json` | **Agent Plugins 1.0.0 specification** | package root | quoted from the specification above | **specified; deliberately not shipped here** |
| `.app.json` | **UNKNOWN — no source** | — | appears **nowhere** in this repository except this file, `PACKAGING.md`, and the two tests beside them | **UNKNOWN; not a name anyone here identified** |

Two consequences worth stating plainly:

- **`.app.json` is the weakest entry and it used to be asserted more strongly
  than it deserves.** The earlier wording implied a name that had been looked
  up and found absent from the specification. The accurate statement is
  narrower: it is not in the Agent Plugins 1.0.0 specification, and there is no
  independent source anywhere in this repository for what reads it. It is
  refused as an *unlisted* component — which is a sound rule — and not because
  anyone established what it is. The builder's comment now says exactly that.
- **The Native layout is refused by directory, not by filename.** A file inside
  `.claude-plugin/` is called `plugin.json`, which is in neither
  `LIVE_SURFACE_NAMES` nor the payload allowlist, so a tree that grew a second
  copy of the Native artifact would have built clean and shipped as though
  nothing had happened. `GENERATED_LAYOUT_DIRS` closes that: the layout is
  refused wherever it sits, and the refusal names the directory.

Refusing that layout is a claim about **this artifact** — that it is not the
Native plugin. It is **not** a claim about either client's supported
installation format, and `docs/claude-codex-parity.md` is the reason the
distinction is drawn here rather than left to the reader.

## Human binding checklist (nothing below is automated)

Every item is a step a person takes, outside this repository, with the client's
own interface. None of them is discharged by anything in this package, and a
green build says nothing about any of them.

1. **Establish the packaging mode before writing a manifest.** App-backed or
   portable is a decision the client's documentation makes, not this
   repository: v1 defines no OAuth and no credential reference, so whichever
   path is chosen, the credential side is the client's. If the mode cannot be
   established from documentation, stop here — **BLOCKED**, not guessed.
2. **Fix the plugin identity.** Choose the plugin ID and keep it across
   releases; a new ID is a new plugin, not an update.
3. **Decide the binding, if any.** A portable binding is a root `mcp.json`,
   `type: streamable-http`, and an `https://` URL because the host is not
   loopback. Adding it means editing `LIVE_SURFACE_NAMES`' rule in
   `build_zip.py` in the same change — a refusal that can be bypassed by not
   editing the code is not a gate.
4. **Confirm both `blocked_on` decisions** in `fleet/fleet-mcp-contract.yaml`:
   `public_exposure_decision` and `chatgpt_oauth_consent`. Both are the
   operator's; an agent may not close either.
5. **Refresh the tool list and prove it changed.** Record the client's tool list
   before and after the surface revision moves. A list that did not change is
   not evidence that it refreshed.
6. **Record what was actually observed**, in the client's words, including
   failures. `PACKAGING.md`'s six gates stay NOT EXERCISED until a person has
   run them; nothing in this repository may tick them.

The ZIP Lab in PR #185 measured a private plugin being updated in place under
one client's Plugin Creator. That is a measurement of **that** flow, and it is
not evidence for any item above: it did not carry an MCP binding, did not
authorize a client, and did not list tools from a live server.
