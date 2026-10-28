# Fleet V1 first-install — reproducible build and acceptance checklist

This is the **packaging lane's own** checklist. It does not restate the release
runbook (`docs/fleet-next-acceptance-runbook.md`) or the evidence gate
(`docs/fleet-next-evidence-gate.md`); those belong to the release-QA lane and
are referenced, not copied. What is here is the sequence someone runs to
reproduce this package's state, and the exact evidence each step leaves.

Everything in Phase A and Phase B is offline, secret-free and bounded. Nothing
in this file installs, uploads, registers or logs in.

## The four layers, still four

| layer | state | what would change it |
|---|---|---|
| Source | **READY** | every gate has a test; the suite runs green offline |
| Final release ZIP | **BUILT** (2026-10-22) | `--check` reproduced both digests; the observation receipt and the two approved assets are in place (the README records the build) |
| Registered | **NOT STARTED** | an owner creating the plugin in Plugin Creator, and reading back its id |
| Client installed | **PARTIAL** | the Claude Code adapter is installed and its MCP server is Connected; the portable package in an App-backed client and the OAuth consent are still owner actions |

The bottom three are **owner actions on external systems**. No command in this
file discharges any of them, and none may be reported as done because a script
exited 0.

## Phase A · Reproduce the source state (offline)

```sh
python3 -m unittest tests.test_fleet_v1_first_install tests.test_fleet_v1_stage_manifest
```

Expected today: **111 tests, OK** — 99 in the builder's suite and 12 in the
staging suite. No network, no secret, no Plugin Creator, no Feishu, no
Tailscale. The two real images have been in the repository since 2026-10-22;
the positive path still uses a lock over PNGs the test generates, so the gate
that compares bytes is exercised without depending on the committed ones.

Three properties this run is asserting, beyond "the tests pass":

- a blocked build writes **no** release archive;
- every refusal names a status a caller can branch on;
- the **fallback is gated too** — a source the audit refuses yields no
  `SOURCE-ONLY` archive either (§ the README). This is the property a
  regression would quietly remove, because the fallback only runs on failure.

## Phase B · Pre-generate the staging tree and the secret-free manifest

```sh
python3 integrations/fleet-v1-first-install/stage_manifest.py \
    --source integrations/fleet-v1-first-install/plugin \
    --lock   integrations/fleet-v1-first-install/brand-assets.lock.json \
    --out    <evidence-dir>
```

This writes `<evidence-dir>/staging/` and
`<evidence-dir>/<name>-v<version>-STAGING-MANIFEST.json`, and **no archive**.

A run is **one-shot**, and refuses in two cases before it reads or writes
anything:

- **`--out` at or below `--source`** — the audit reads the source before any
  output exists, so a tree created under it is one the *next* run audits and
  copies. Keep the output in a separate evidence directory.
- **either output path already existing** — a directory-only check misses the
  incomplete run, which is the one whose manifest is most tempting to write
  over. The existing manifest keeps its original bytes; nothing is written.

Expected today, measured 2026-10-22 (the assets are in the repository; the
endpoint gate still refuses a run that supplies no observation):

```
serveros-fleet-ingress v1.0.0 STAGED (not a release)
staging_dir=staging manifest=serveros-fleet-ingress-v1.0.0-STAGING-MANIFEST.json
present=10 absent=[] skills=5
blocked[endpoint]=BLOCKED_MCP_ENDPOINT
```

Check three things in the manifest, because each is a way a staging run could
lie:

| check | why |
|---|---|
| `installable` is `false` and no `.zip` exists beside it | a ZIP is the one artifact that gets installed |
| `absent_members` is empty here, and `pinned_assets` carries the **approved** digests from the lock | an absent slot is named in `absent_members`; a present one is compared with the pinned digest, never against a substitute |
| `blockers` names both gates, and `mcp_url` is `null` | a block that is recorded is one an operator can act on; a block that is swallowed is one that becomes a fabricated URL |

The staging tree is the release payload **minus `mcp.json`**. That member is
generated from an observed URL, and there is no observed URL while the endpoint
gate is blocked — so a `mcp.json` here would be a fabricated binding wearing a
generated filename.

## Phase C · The inputs only an owner can supply

Each is unblocking, and each is an owner's act. None is a build step.

| # | input | owner action | unblocks |
|---|---|---|---|
| 1 | the two approved PNGs | transfer them in; they must hash to the values in `brand-assets.lock.json` | `MISSING_BRAND_ASSET` |
| 2 | an observed Next endpoint | expose the Next ingress on a tailnet hostname reaching `/mcp` over verified TLS, and record the observation receipt (both discovery readbacks, `issuer` on the same origin, `scopes_supported` carrying the six Fleet scopes) | `BLOCKED_MCP_ENDPOINT` |
| 3 | a Plugin ID | create the plugin in Plugin Creator **once**, and read the id back | the registration layer |
| 4 | the Feishu app-2 callback | point it at that FQDN (production console edit) | the SSO path, not the ZIP |

A path or a name supplied on a command line is not a registration and is not an
observation. The builder refuses both by construction.

## Phase D · Build the release, once C has landed

```sh
python3 integrations/fleet-v1-first-install/build_first_install.py \
    --next-mcp-url https://<next-ingress>.<tailnet>.ts.net/mcp \
    --observation  <observation.json> \
    --check
```

`--check` builds twice and asserts both digests agree. Record `archive_sha256`
and `content_sha256`; the archive is deterministic (fixed timestamp, fixed mode,
sorted members), so a rebuild must reproduce them.

Read the record's own fields before calling anything live:
`endpoint_evidence: OPERATOR_SUPPLIED_RECEIPT` and
`endpoint_probe_performed_by_this_tool: false`. The builder has no network. The
release gate must still query the endpoint and its discovery documents itself
and check the audience.

## Phase E · Install, and the one consent

Install the ZIP once, under its own Plugin ID, and complete the OAuth consent.
Scopes are minted into the token at authorization, so a later widening is a
re-consent — not an edit to the package. Do not reuse V1's plugin id or its
host: this repository refuses V1's host as the Next binding for that reason.

## What must not be claimed

- **The served tool count is read, not frozen** — at `fleet-mcp-7`,
  `SERVED_TOOLS` is 44 (11 authored + 31 mirror + 2 descriptor tools). Read the
  actual `tools/list`; the count is what is *reachable*, not what a given token
  may do.
- **6 scopes** is true: `ALLOWED_SCOPES` equals `IMPLEMENTED_SCOPES` — all six,
  measured. `DEFAULT_GRANTABLE_SCOPES` is **two** (`fleet:read`, `fleet:send`);
  an operator who writes nothing does not obtain create, control, approve or
  navigate.
- A green suite is **not** an installed plugin, a live endpoint, or a rendered
  icon. Those are three separate facts, none of which this lane can produce.
