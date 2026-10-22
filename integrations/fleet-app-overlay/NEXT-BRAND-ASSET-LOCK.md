# ServerOS Fleet: approved starship-fleet artwork — authoritative selection (2026-10-22)

> **User approval:** The attached **512×512 PNG** from the 2026-10-22 conversation is now the **sole approved visual source** for the existing **ServerOS Fleet** brand. It supersedes the earlier 1254×1254 source and both previously recorded PNG slot digests. Do not choose a different picture, redraw it, or substitute the geometric blue-hub SVG.
>
> **Status:** `SOURCE_VERIFIED_FROM_USER_UPLOAD / BRAND_LOCK_UPDATED / BINARY_NOT_YET_IN_GITHUB / NOT_INSTALLED`. Changing this document is NOT proof that the binary was committed or that the installed App-backed plugin displays it.

## Identifying the approved image

A realistic asymmetric space fleet. A large dark armored capital ship dominates the foreground; multiple starships occupy the background against a planet, small moons, asteroids and a sunrise. Do not confuse this with the three-node geometric emblem in `integrations/chatgpt-fleet-next/icon.svg`.

| Property | Value |
| --- | --- |
| User-provided filename | `image(20261022-061408).png` |
| Image type | PNG, RGB |
| Actual dimensions | **512 × 512** |
| Actual bytes | **500,326** |
| SHA-256 of exact original PNG | `15ad7a51e11070a43691cc69b1a1be43593ff87ed70412cceb7549c83c275836` |
| Git blob SHA of original PNG (not yet committed) | `3707d764d7ca33be3a245434d99e25da0f90c03d` |

**SHA-256 is the acceptance test:** if a purported `composerIcon.png` differs, it is not the exact user upload.

## Intended published asset layout

The existing App-backed plugin interface names `composerIcon` and `logo`. These are **presentation slots**, not separate design alternatives.

| Slot | Expected location under the package source | Dimensions | SHA-256 |
| --- | --- | --- | --- |
| composerIcon (byte-for-byte user original) | `assets/composerIcon.png` | 512 × 512 | `15ad7a51e11070a43691cc69b1a1be43593ff87ed70412cceb7549c83c275836` |
| logo (same artwork, deterministic 2× resize) | `assets/logo.png` | 1024 × 1024 | `cdb7ccbdc4c8bf28a5afc8e3b81ac8c9da617242a12197db4864ad645ae5bfe2` |

The logo variant above was produced locally from the approved user PNG using Pillow/LANCZOS. It is **a resize, not another generated image**. Both expected files were generated and hash-verified in the conversation container for a downloadable asset pack. Their raw image bytes are not currently committed to the GitHub repository; the pack must be uploaded before claiming asset delivery.

## Repository integration

- The V1 first-install source's authoritative enforcement record is [`integrations/fleet-v1-first-install/brand-assets.lock.json`](../fleet-v1-first-install/brand-assets.lock.json). It is updated to reflect this exact approved artwork.
- For the existing App-backed Fleet plugin, presentation ownership remains with the App/Creator. The `integrations/fleet-app-overlay` script and same-ID update mechanism do **not** prove that an updated avatar can be installed through a Skills-only ZIP. Verify available Creator presentation editing and real client readback before claiming success.
- Do not replace the installed `plugin_asdk_app_6ace0c9c0dfc81918ed5f0fac18182b6` identity, do not publish a new Fleet Next prod plugin, and do not change MCP/OAuth/permissions while performing brand updates.
- After actual GitHub binary upload, check the Git blob SHA or decoded SHA-256 from **the committed file** and update the status to `BINARY_COMMITTED`; only after Creator/readback may it advance to `CLIENT_RENDERED`.

## Previous selection

The old 1254×1254 hash beginning `b5ae68cd`, composerIcon hash beginning `644cf5a3`, and logo hash beginning `31bd3a24` are **SUPERSEDED**. They must not be used as pack validation values for this version. They may remain visible in Git history for audit but have no current brand authority.
