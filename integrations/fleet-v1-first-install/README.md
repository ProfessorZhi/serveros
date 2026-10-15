# Fleet V1 — the first-install plugin package

The plugin a client registers **once**, carrying a remote `streamable-http` MCP
binding and the Operator Skills that teach an assistant to use the Fleet
surface correctly. This directory is a separate, reviewable artefact from
`integrations/chatgpt-fleet-next/` (instruction-only, refuses `mcp.json`) and
from `integrations/fleet-app-overlay/` (an update overlay for a plugin that
already exists).

## Status, in the four layers that are not one thing

| Layer | State | What would change it |
|---|---|---|
| **Source** | **READY** | the tree in `plugin/` builds, and every gate has a test |
| **Final release ZIP** | **BLOCKED** | two things, both outside this repository: the two approved PNGs, and an observed Next `https://<name>.ts.net/mcp` reporting an issuer and the six Fleet scopes |
| **Registered** | **NOT STARTED** | a human creating the plugin in Plugin Creator and reading back its plugin id |
| **Client installed** | **NOT DONE** | a human installing it, and the token-scope re-consent |

No row below the first is discharged by anything in this repository, and
`build_first_install.py` cannot reach any of them: it has no network, no
Plugin Creator client, and no OAuth flow.

## Why the release ZIP is blocked, precisely

```
$ python3 integrations/fleet-v1-first-install/build_first_install.py \
      --next-mcp-url https://fleet-next.tail3d8b76.ts.net/mcp \
      --observation ./next-endpoint-observation.json
BLOCKED_MCP_ENDPOINT: the endpoint was not observed as a live Next ingress — …
```

Two independent blockers, and the builder reports whichever it reaches:

1. **`BLOCKED_MCP_ENDPOINT`** — the Next ingress has not been observed on a
   public hostname. A URL typed on the command line is a claim about a machine;
   the builder wants a *separate receipt* showing the URL reachable over
   verified TLS with both OAuth discovery documents read back at HTTP 200. The
   host must be a `<name>.ts.net` tailnet name, the path must be `/mcp`, and it
   must not be the V1 host this repository already observed
   (`integrations/chatgpt-fleet-next/PACKAGING.md`), because Next and V1 are
   separate releases with separate plugin ids.
2. **`MISSING_BRAND_ASSET`** — the two approved images exist only in the user's
   conversation. `brand-assets.lock.json` pins their SHA-256; the builder reads
   the bytes and compares. A file that is present and is *not* those bytes is
   `BRAND_ASSET_MISMATCH`, which is a different status because it is a
   different fix. Neither is repaired by drawing something similar, and there
   is a test that no PNG is committed here — so the lock's own warning cannot
   quietly become false.

When either blocks, the builder writes **no release archive**. The only file it
can still produce is named `<name>-v<version>-SOURCE-ONLY-NOT-INSTALLABLE.zip`
and carries a `NOT-INSTALLABLE.md` member. A release-shaped ZIP that cannot be
installed is the one output that must never exist, because it is the one that
gets installed.

## Building it once the two inputs exist

```sh
python3 integrations/fleet-v1-first-install/build_first_install.py \
    --next-mcp-url https://<next-ingress>.<tailnet>.ts.net/mcp \
    --observation  ./next-endpoint-observation.json \
    --check                     # build twice, assert both digests agree
```

The observation receipt is JSON with schema
`serveros/fleet-next-endpoint-observation/v1`; `tests/test_fleet_v1_first_install.py`
constructs one in `observation()` and is the reference for the shape. It must
carry, for the authorization server: an absolute HTTPS `issuer` **on the MCP
endpoint's own origin**, and a `scopes_supported` list that advertises the six
scopes this surface mints. Details below.

### The receipt is an operator's claim, not a probe

Every value in that receipt was typed by a person, `source: LIVE` included. This
builder has no network and cannot check any of it; its job is to refuse a
receipt that is *malformed or self-contradictory* and to make the remaining work
visible. The build record says so in its own fields —
`endpoint_evidence: OPERATOR_SUPPLIED_RECEIPT` and
`endpoint_probe_performed_by_this_tool: false` — and nothing it emits should be
read as an authenticated verification. A release gate still has to query the
Next endpoint and its discovery documents itself and check the audience before
anyone calls the result live.

What the builder *does* check about the claim is consistency, because that is
what an offline tool can honestly do:

- both discovery documents are on the endpoint's own origin, at absolute HTTPS
  URLs with no credential component or query;
- `issuer` is on that same origin — an issuer and a resource that were not
  observed together are not one observation;
- `scopes_supported` is well formed and contains all six Fleet scopes. A client
  cannot request a scope the authorization server does not advertise, so a
  missing one makes the Skill that needs it unusable while the package still
  builds — which is the failure this catches.

## Where the client reads the presentation block

`extensions.com.openai.interface` — `displayName`, `shortDescription`, and the
two icon slots `composerIcon` and `logo`, which must name the same files
`brand-assets.lock.json` pins by SHA-256. The first revision declared the icons
only under this repository's own namespace; that is not a declaration any client
is documented to read, so the approved artwork would have shipped without anyone
being asked to render it. There is a test for each way that can regress.

The custom namespace is kept for provenance and may not repeat a presentation
field: two answers to one question is how they come to disagree.

Passing this check says the manifest asks the documented way. It says nothing
about whether a client renders the image, which is a client behaviour and has
not been observed.

## The auth declaration

The MCP binding declares its authorization under
`mcpServers.<name>.extensions.com.openai.auth`, and the builder validates it
shape by shape rather than passing it through:

| Field | Rule |
|---|---|
| `auth.type` | must be `oauth`. The platform documents `none`, `oauth`, `mixed` and `api_key`; this server is an OAuth 2.1 authorization server with RFC 7591 dynamic client registration, and the other three describe a different service — refused by name |
| `client.mode` | must be `dcr`, which is what this server does. `provided` needs a real client id and this plugin has not been registered; `cimd` is not a mechanism it has |
| `clientId`, `clientSecret`, `secret`, `password` | refused at any depth. The format has no field for a client secret, and an invented client id is a false declaration |
| `client.tokenEndpointAuthMethod` | `none` or `private_key_jwt`; the two `client_secret_*` methods imply a shared secret a package cannot hold |
| `registrationUrl`, `authorizationServerBase` | absolute HTTPS, no credential component or query, and on the MCP endpoint's own origin |
| `authorizationUrl`, `tokenUrl`, `oidc*` URLs | absolute HTTPS, no credential component or query |
| `baseScopes`, `defaultScopes`, `oidcScopesSupported` | non-empty lists of `namespace:action` tokens, confined to the six Fleet scopes |
| any other key | refused, so a field added to the template arrives as a build failure with a name |
| `headers` | still refused everywhere — header values are not a portable secret mechanism |

`onboardingSkill` is deliberately **not** declared. The platform lists it, it is
optional for V1, and its exact shape is not established by anything this
repository has read; an optional release precondition that is only half
understood is one the release would carry for no verified benefit.

## Why MCP must be in the first release

A pure-Skills package cannot be turned into an MCP connector by a later update
of the same plugin: the format a client registers is the format it keeps. That
is why this builder **requires** `mcp.json` while
`integrations/chatgpt-fleet-next/build_zip.py` **refuses** it. The two are not
inconsistent; they are different artefacts, and the test suite pins that
difference rather than the similarity.

The binding is generated, never committed. `plugin/mcp.template.json` holds the
template and the builder substitutes the observed URL, because a checked-in
`mcp.json` would be a committed claim that a server is reachable somewhere —
and the source audit refuses one by name.

## The App-backed path

`--app-backed` is the private, App-backed variant. It needs the plugin id and
the App id **read back from Plugin Creator** (schema
`serveros/fleet-next-app-registration/v1`). Without that receipt it is
`APP_BINDING_UNREGISTERED`; a name supplied on the command line is not a
registration, and this tool neither creates plugins nor reports that it did.
The default path is portable and writes no `.app.json`.

## What the operator has to do next

1. Put the two approved PNGs at `plugin/assets/composerIcon.png` and
   `plugin/assets/logo.png`; the builder verifies the pinned digests.
2. Expose the Next ingress on a tailnet hostname, reaching `/mcp` over verified
   TLS, and record the observation receipt: both discovery readbacks, the
   `issuer` on the same origin, and a `scopes_supported` carrying the six Fleet
   scopes.
3. Create the plugin in Plugin Creator and read back its id — **once**; from
   then on the id is stable and backend-only updates need no new ZIP.
4. Install it in the client and complete the OAuth consent. Scopes are minted
   into the token at authorization, so a later widening is a re-consent, not an
   edit.

`docs/fleet-next-evidence-gate.md` and `docs/fleet-next-acceptance-runbook.md`
are the review side of the same release; this directory is the packaging side.

## Tests

```sh
python3 -m unittest tests.test_fleet_v1_first_install
```

Offline, single process, no network, no secret, no Plugin Creator, no Feishu,
no Tailscale. The two real images do not exist on any machine this suite runs
on, so the positive path constructs a lock over PNGs it generates — which is
the point: a test that could not produce the pinned bytes could not exercise
the gate that compares them.
