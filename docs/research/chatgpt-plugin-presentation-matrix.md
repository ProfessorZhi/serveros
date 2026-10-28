# ChatGPT plugin presentation support matrix (source-level)

- Retrieved: 2026-10-28
- Source of record: `integrations/fleet-app-overlay/chatgpt_presentation_matrix.json`
- This is a **support matrix and a local fixture**, not a Creator observation.

| case | identity | displayName | avatar | status | what would change it |
|---|---|---|---|---|---|
| portable plugin manifest/assets | exact plugin_id | manifest metadata | manifest asset paths | `SUPPORTED_BY_PUBLIC_DOC` (rendering not verified) | new ZIP + client readback |
| App-backed skills-only | exact plugin_id + release_id; app ID distinct | App/Creator-owned | App/Creator-owned | `UNKNOWN` | a test plugin + creator-side readback |
| App-backed avatar via ZIP | exact plugin_id | may be frozen | may be frozen | `BLOCKED` | a platform-supported presentation path, not a guess |
| app ID used as plugin ID | app ID ≠ backend plugin ID | n/a | n/a | `BLOCKED` | read the exact backend plugin_id |
| Claude Code marketplace | its own plugin id | Claude manifest | Claude manifest | `NOT_APPLICABLE` | not a ChatGPT Creator path |

## Minimal fixture

`tests/data/fleet-overlay-fixture/` supplies a synthetic manifest with
`extensions.com.openai.composerIcon` and `logo`, plus valid PNG files. It is
used by `tests/test_fleet_chatgpt_plugin_update_contract.py` to exercise:

- declared icon files are inspected (dimensions/format), not rendered;
- a 1×1 icon is refused;
- `.app.json` marks a package `app_backed` and the preflight returns
  `APP_BACKED_PRESENTATION_UNKNOWN`;
- the matrix never marks an App-backed avatar case `SUPPORTED` or `verified`.

## Honest boundaries

- Public docs state metadata/assets changes use a new ZIP, but do not prove the
  App-backed avatar/display path.
- The enterprise page uses the **app ID** for app linking; it does not say the
  app ID is the backend plugin ID.
- A green local icon check means the file is a readable image of an acceptable
  size; `renders_as` remains `null`.
