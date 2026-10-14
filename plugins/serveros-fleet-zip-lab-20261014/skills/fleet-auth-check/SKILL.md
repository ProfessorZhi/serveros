---
name: fleet-auth-check
description: Explain the separation between Feishu SSO identity, Fleet OAuth access tokens, device grants, and ChatGPT plugin metadata.
---

# ServerOS Fleet authentication (packaging test)

This is a packaging experiment. An authenticated Feishu login only verifies a human identity and must not automatically grant device control. Check signed issuer, audience, PKCE, state, stable user identity scoped to provider and tenant, and explicit device/runtime/verb grants. Refuse to infer control rights from a shared OAuth client id. Do not request, disclose or store real credentials.
