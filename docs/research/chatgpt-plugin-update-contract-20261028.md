# ChatGPT plugin same-identity update — official contract research

- Retrieved: 2026-10-28
- Scope: source-level package/preflight only; no Creator call, upload, install,
  OAuth/scope change or user plugin modification.
- Runtime evidence level: **DOCUMENT + ISSUE**, not a live Creator observation.

## Sources

1. OpenAI Developers, *Plugins — submission/deploy*:
   https://developers.openai.com/plugins/deploy/submission
2. OpenAI ChatGPT Enterprise, *Plugin management*:
   https://learn.chatgpt.com/docs/enterprise/plugin-management
3. `openai/codex` issue #49957, *Plugin Creator: support deleting retired paths
   during account plugin updates*:
   https://github.com/openai/codex/issues/49957
   (reports the installed `plugin-creator:update-plugin` skill at version
   `0.1.22`; it is an issue, not an OpenAI product contract page)

## Contract table

| claim | evidence | status |
|---|---|---|
| `update_plugin` arguments are `plugin_id`, `expected_release_id`, `archive` | issue #49957: "The exposed update arguments are `archive`, `expected_release_id`, and `plugin_id`." | **TOOL_BEHAVIOUR_REPORTED**, not a public developer-doc page |
| `expected_release_id` is the current release ID guard, not a semver | issue #49957 uses the plugin's "current release ID as `expected_release_id`" | **REPORTED**; the exact mismatch response is not quoted |
| update is same-plugin, identity-preserving | issue #49957: overlay update; official page: "To update metadata, assets, or bundled skills, upload a new ZIP to the existing plugin." | **SUPPORTED** |
| archive format | official page: "create a complete ZIP"; issue #49957: "Prepare a complete ZIP". No public page or issue text establishes `tar.gz`. | **ZIP_ONLY_PUBLIC_EVIDENCE**; `tar.gz` = **NOT_ESTABLISHED** |
| omitted files | issue #49957: "uploaded files overlay the current release; omitted files and binary assets remain intact." Official page: "complete ZIP, including all components you intend to keep." | **CONFLICT_OF_SCOPE**: tool behavior vs official instruction. This repo takes the stricter complete-ZIP rule. |
| version must not go backwards | official page says use an explicit semantic version for updates but does not state a monotonic rule; issue #49957 only shows a version bump | **OWNER_POLICY**, not a quoted official rule. The local preflight enforces `next > previous` as a safety policy. |
| plugin ID vs app ID | enterprise page: "Use the app ID, not a `plugin_...` ID" for app linking; "a plugin URL containing `plugin_asdk_app_example` represents the app `asdk_app_example`." | **DISTINCT**. Do not infer the backend plugin ID from an app ID. |
| bad update retains last working version | enterprise page: "its last working version is retained" | **DOCUMENTED** for the managed/GitHub sync path; **not** a documented Creator rollback procedure |
| version rollback | no public Creator procedure found | **NOT_DOCUMENTED**; do not claim a rollback by decreasing the version |
| App-backed display/name/avatar change via a Skills-only ZIP | submission page covers metadata/assets with a new ZIP; enterprise page does not define App-backed presentation changes; no public source covers the frozen case | **UNKNOWN / BLOCKED** for App-backed presentation; see the matrix |

## The operational rule this repository uses

1. Build a **complete ZIP** from source. Do not rely on overlay omission even
   though the Creator issue reports it; the official page tells the operator to
   include everything they intend to keep.
2. Supply the **exact backend `plugin_id`** and the current
   **`expected_release_id`**. Never derive the plugin ID from an app ID or from
   a plugin URL alone.
3. Require `next_version > previous_version` locally. This is a repository
   safety policy, because public docs do not state a monotonic rule; if the
   platform later documents a different rule, this preflight must be updated.
4. Treat App-backed `displayName` / avatar changes as **UNKNOWN/BLOCKED** until a
   test plugin readback proves them. A ZIP that validates does not prove a
   client renders the asset.
5. Never call Creator, upload, install or mutate OAuth/scopes from this tooling.

## What is explicitly not claimed

- `tar.gz` support; only ZIP is evidenced.
- That `expected_release_id` mismatch has any particular error shape; the
  issue calls it a guard but does not quote the refusal.
- That a version decrease performs a rollback.
- That the backend plugin ID equals the app ID.
- That an App-backed avatar/display changes by uploading a Skills-only ZIP.
- That local icon dimension checks predict client rendering.

## Local artifacts

- `integrations/fleet-app-overlay/chatgpt_update_preflight.py` — dry-run
  validator; calls no network and writes only the requested JSON.
- `integrations/fleet-app-overlay/chatgpt_presentation_matrix.json` — support
  matrix with `UNKNOWN`/`BLOCKED` App-backed rows.
- `tests/test_fleet_chatgpt_plugin_update_contract.py` — positive and negative
  fixtures (wrong ID, stale release, tar.gz, version, omitted member, icon).
- `tests/data/fleet-overlay-fixture/` — minimal manifest/icon fixture reused as
  the synthetic package input.
