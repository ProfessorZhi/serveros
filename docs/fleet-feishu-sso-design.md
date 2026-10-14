# Fleet Next · Feishu identity broker (phase A: isolated development)

Issue: [#183](https://github.com/ProfessorZhi/serveros/issues/183)
Branch: `feat/fleet-feishu-sso-broker-20261014`, from the Fleet RC
`12c418f898d96876bb1d86c5ae0ca8977b006c12`.

This document is the design record for a change that is **not deployed**. It
describes what was built, what is deliberately not enabled, and — the part that
matters most before anyone turns it on — what about the Feishu side is
**UNKNOWN and must be verified by hand**.

## What this is

A second consent proof for the Fleet MCP OAuth endpoint. Today the only way to
approve a ChatGPT connector is to paste the owner's personal access token into
the consent form. This adds the other proof: a whitelisted person signs in at
Feishu, the *server* exchanges the code for an identity, and that identity — not
a shared secret — is what the issued token is about.

```
ChatGPT ──GET /oauth/authorize──▶ Fleet consent form
                                    │  (a link, only when Feishu is enabled)
                                    ▼
                          GET /oauth/feishu/start
                            validate the request as the form does
                            hold client/redirect/scopes/PKCE + client state
                            server-side, single-use, TTL-bounded
                                    │ 302
                                    ▼
                          Feishu authorize page  ──(human signs in)──┐
                                    │                                │
                          GET /oauth/feishu/callback  ◀──────────────┘
                            consume the transaction (once)
                            exchange code → identity  (server-side only)
                            match against the whitelist (app + tenant + user)
                                    │ 302 with a Fleet code
                                    ▼
                          ChatGPT redirect_uri?code=fac-…&iss=…&state=…
                                    │ POST /oauth/token  (PKCE verifier)
                                    ▼
                          Fleet access token, sub = identity key, idp = feishu
```

## What this is not

*   **Not a login-to-authority bridge.** A whitelisted identity holds **no**
    device verb. Signing in makes the identity a *principal*
    (`feishu:<app_scope>:<tenant_key>:<open_id>`); whether that principal may
    touch a device is `policy.operator_grants`, and a fresh identity has no entry
    there. That is `PENDING_GRANT`, and every operation is refused until an
    operator writes the grant. Signing in and being authorized are answered by
    two different mechanisms on purpose.
*   **Not a scope change.** The six scopes are untouched. Nothing is collapsed
    into a `fleet:access`, and the identity is not mapped onto `owner-pat`. The
    identity path bounds the granted scope by `grantable_scopes` exactly as the
    PAT path does.
*   **Not enabled.** With no `auth.feishu` block, this code does nothing: the
    two new routes 404 exactly as an unknown path does, the consent form is
    byte-identical, and a token minted by the PAT path carries no new claim.

## The identity key

```
feishu : <app_scope> : <tenant_key> : <open_id>
```

| Part | Why it is in the key |
|------|----------------------|
| `app_scope` | Two Feishu applications are two namespaces. An `open_id` from one means nothing in the other. |
| `tenant_key` | An `open_id` is unique **within a tenant**, not across tenants. Matching the id alone would admit the same string from another organization. |
| `open_id` | The stable user id, read from the Feishu API by this process. |

`union_id` is recorded when the API returns one but is **never used to match**
across applications or tenants — it is documented as stable per developer, not
per organization, and building a cross-tenant match on an unverified assumption
is exactly the failure this module exists to avoid.

A display name, email, phone or avatar never reaches the key. Those are claims
the API makes and can change; the browser never supplies any of them, and the
only constructor for `FeishuIdentity` is `FeishuBroker.exchange_code`.

## The one-time transaction

`TransactionStore` holds the ChatGPT request **server-side** between the two
redirects, keyed by an opaque `fst-…` state. It binds the client id, the
registered redirect, the PKCE challenge and method, the requested scopes, the
resource and the client's own `state`. The browser carries only the opaque
state, so:

*   a callback cannot substitute a different client, redirect or scope — it can
    only present the state this server issued;
*   the state is **single-use** (replay is refused) and **TTL-bounded**;
*   an unknown or spent state has no trusted redirect, so the refusal is shown
    rather than redirected — that is the open-redirect the flow is built to
    avoid.

The granted scopes are rebuilt from the server's record, never from the
callback's query.

## Authorization is unchanged, and reused

`FleetService._operator_grant_verbs(principal, device_ref, runtime)` is already
the `device × runtime × verb` axis — it looks up
`policy.operator_grants[principal.name][provider_key(device_ref, runtime)]` and
requires the device's `ownership == "company"`. The Feishu identity plugs into
it by **being a principal name**; nothing in `fleet.py` was changed. The device
flag (`allow_send` / `allow_control` / `allow_create`), the per-session /
per-project lists, and the owner floor all still apply on top, exactly as before.

```yaml
policy:
  operator_grants:
    "feishu:cli_aa0e8c3d6f789cc6:tenant-company:ou_xxx":
      "device:tokyo-shared-dev-01": [send]
      "device:tokyo-shared-dev-01|claude-code": [control, send]
```

Absent → the identity can connect and every operation is refused
(`PENDING_GRANT`).

## Configuration

```yaml
auth:
  # ... existing keys ...
  feishu:
    enabled: false                # the shipped state; nothing below is in use
    app_scope: cli_aa0e8c3d6f789cc6
    app_id: cli_aa0e8c3d6f789cc6
    # A PATH. This module never reads the value; the broker reads it at the
    # moment of an exchange, through a reader, and never logs or returns it.
    secret_anchor: /path/to/vault/feishu-app2-secret
    redirect_uri: https://<fleet-public-host>/oauth/feishu/callback
    # Two identities, verified by the operator. Until they are written the
    # whitelist is empty and `enabled: true` is refused at load.
    whitelist: []
    #  - tenant_key: <tenant>
    #    user_id: <open_id>
    #    label: company-vince
    #  - tenant_key: <tenant>
    #    user_id: <open_id>
    #    label: personal-me
```

`enabled: true` with a missing field or an empty whitelist is refused at load.
The refusal names every missing piece at once and refuses rather than guessing:
this surface will not admit an identity nobody wrote down, and it does not
invent a bootstrap identity.

## Threat model, as the tests state it

Each of these is a sentence an attacker would try, and each is kept false:

| Attempt | Kept false by |
|---------|---------------|
| Replay the callback | `TransactionStore.consume` pops the state |
| Forge the state | unknown state → no trusted redirect → shown refusal |
| Post my own `open_id` / name | the identity only ever comes from the server-side exchange |
| Reuse a whitelisted id from another tenant | tenant and app scope are matched |
| Cross-tenant with `union_id` | `union_id` is never used to match |
| Sign in, then drive the fleet | principal has no `operator_grants` entry |
| Widen scope at refresh | pre-existing rule: refresh may narrow, never widen |
| Skip PKCE | the token exchange still requires the verifier |
| Reach the routes with Feishu off | both routes 404 as an unknown path does |

## VERIFIED

Verified here, offline, with the network faked (`FakeFeishu`) and no credential:

*   Identity key construction, validation, and that no display name / email /
    phone is carried.
*   Whitelist matching: unknown identity, wrong tenant, wrong app scope, empty
    whitelist, two admitted identities.
*   Transaction single-use, expiry, unknown state, and that it binds the
    original request.
*   Server-side exchange: the secret is read only through the reader, a missing
    secret refuses *before* any call, a failed exchange / user-info call is
    refused, and a code is never treated as an identity.
*   Config: absent is absent; disabled parses; enabled-and-incomplete and
    enabled-with-no-identity are refused; the secret anchor is a path and is
    never read at load.
*   OAuth: identity approval mints a code with no PAT; the token carries `sub`
    and `idp`; a plain approval still carries `sub=operator` and **no** `idp`
    claim; refresh preserves the identity; scopes stay bounded.
*   HTTP: start validates before redirecting; callback completes with `iss` and
    the client `state`; replay, decline, unknown identity, wrong tenant, API
    failure and bad PKCE each end in a refusal, not a token; both routes 404
    when disabled; the consent form offers the Feishu link only when enabled.
*   Principal: a Feishu token yields a `feishu` principal named by its identity;
    an OAuth token is still named by its client; a whitelisted identity holds no
    device verb until granted; and the grant is device × runtime × verb specific.

Regression: the existing fleet test files pass unchanged (≈1050 tests plus
subtests across the two runs recorded in the PR).

## UNKNOWN — must be verified by hand before phase B

None of the following could be checked without touching the real Feishu console,
and none is assumed by this code:

1.  **Application 2's OAuth capability.** Whether `cli_aa0e8c3d6f789cc6` may use
    the web OAuth (`authen/v1/authorize` + token + user_info) endpoints at all,
    and on which hosts. The `base_url` and three paths in `FeishuBinding` are
    **unverified defaults** and every one is overridable.
2.  **Redirect URI registration.** Whether the Fleet callback URL is (or can be)
    registered on application 2, and whether it may leave `127.0.0.1`.
3.  **Available scope / availability.** Whether the application's console scope
    (`offline_access` was observed) is sufficient, and the "available range" the
    identity must fall in.
4.  **Tenant of each identity.** Whether the two identities are in the same
    tenant as application 2. A personal Feishu account may be **a different
    tenant or a different brand**, in which case one self-built application
    cannot serve it and a second IdP entry is required. The identity key already
    carries `app_scope`, so a second entry is a config change and not a redesign,
    but which tenant each person is in is not knowable from here.
5.  **The identity payload shape.** Which fields (`open_id` / `union_id` /
    `tenant_key`) the user-info response actually returns, and whether
    `tenant_key` is present. If it is absent the exchange **refuses** — it is
    read as `tenant_key` or `tenant`, and nothing is guessed.
6.  **Whether the owner PAT is still required.** This change leaves
    `build_oauth`'s requirement that `pat_anchor` be readable in place, so the
    owner floor is unchanged. Whether SSO should eventually stand alone is a
    separate decision and is not made here.

Until (1)–(5) are answered, `enabled` stays `false` and the whitelist stays
empty. Enabling a path with an unverified redirect or an unverified tenant is
the kind of change that fails at the moment a person tries to use it.

## Non-goals (this PR)

*   Tool refresh / CIMD, scope merging — separate discussion, not mixed in.
*   Any change to applications 1 (`cli_aa08005233f91bcd`) or 3
    (`cli_aa0d5b239c399bcb`), to the Feishu console, to production, to the
    Funnel, to the ChatGPT plugin, or to any credential.
*   A consent form *after* the Feishu sign-in. The sign-in is the human act;
    the scopes are bounded by the deployment's `grantable_scopes`. If reviewers
    want the second form (the identity tick boxes), it is a small addition on
    top of `approve_identity`'s existing `granted=` parameter.
