# Fleet Next: selected spaceship-fleet avatar (asset lock)

The user approved the asymmetrical realistic space fleet image from the conversation (not the symmetric emblem or earlier fleet variants). On 2026-10-22 the same artwork was re-sent to the owner's Feishu self-chat so it could be retrieved onto Tokyo; the binaries now live in this repository.

**Status:** `SOURCE_DELIVERED_BY_FEISHU_JPEG_TRANSCODE / BINARY_COMMITTED / CLIENT_RENDERED_NOT_OBSERVED`.

Feishu serves images re-encoded as JPEG. The delivered file is therefore a 512x512 JPEG (219,855 bytes, SHA-256 `9fc328825e113b465009b850a6eedf5e3b18680fb4a26b1b3c9877e8a9f9cef1`), committed under `integrations/fleet-v1-first-install/brand-source/feishu-20261022.jpg`. The PNG quoted in the earlier conversation container (500,326 bytes, SHA-256 `15ad7a51...`) was never present on this host and could not be downloaded from Feishu; its digest is retained in the lock under `superseded_pins` for audit only. No image was drawn or substituted: both slots are derived from the delivered JPEG.

| Plugin slot | File under package root | Dimensions | SHA-256 |
| --- | --- | --- | --- |
| composerIcon | assets/composerIcon.png | 512x512 PNG, 452,278 bytes | `49c68d16dd4e130ce909743e92a3ac4ec6ed2579abf85ac3b03ff08e844966dc` |
| logo | assets/logo.png | 1024x1024 PNG, 1,371,784 bytes | `4f816acc8d94bef97475f92f3186136e8133764d92ee5dadb60915d6fe07aa9e` |

`composerIcon.png` is the lossless PNG encode of the delivered JPEG's decoded pixels. `logo.png` is a 2x LANCZOS resize (Pillow 10.2.0) of those same pixels. The authoritative enforcement record is [`integrations/fleet-v1-first-install/brand-assets.lock.json`](../fleet-v1-first-install/brand-assets.lock.json); it pins these exact bytes and the builder refuses any other.

The committed binaries are proof the assets reached GitHub, not proof that a client renders them. For the existing App-backed Fleet plugin, presentation ownership remains with the App/Creator: the `integrations/fleet-app-overlay` script and same-ID update mechanism do **not** prove that an updated avatar can be installed through a Skills-only ZIP. Verify Creator presentation editing and real client readback before claiming the icon is rendered.

Public asset limits (not proof private App-backed Creator renders it): square 48..4096 px, PNG/JPEG/WebP/SVG, <=5 MiB. Both files meet these limits.

Do not replace the installed `plugin_asdk_app_6ace0c9c0dfc81918ed5f0fac18182b6` identity, do not publish a new Fleet Next prod plugin, and do not change MCP/OAuth/permissions while performing brand updates.
