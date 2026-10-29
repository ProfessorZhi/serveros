# W19E · current-main builder evidence

This is the main-based candidate pack. Unlike `../evidence/` (Wave 18) and
`../evidence-w19/` (the #518 fix, source `ff514f4`), this pack is built from a
commit on the **current main line**, `47030bc`, with the vendored D7 builder.

## Reference

| | |
|---|---|
| source commit | `47030bc1d9a728c4ce99f2b27ccd16017aea0eab` |
| version | `1.0.0+wave19e` |
| builder | `wave18_builder.py` `sha256:4a6b9b6b…` |
| D7 dependency | `vendor/l2_release.py`, byte-identical to #431 `517ef91` (`sha256:7f87a596…`) |

| unit | tarball sha256 | installable |
|---|---|---|
| `fleet-runtime-base` | `885ab0e57194e2f8e29e7f9867aaf1b64f22331fb960af1679e52edbcb37fc8b` | yes |
| `fleet-authority` | `38f7e6cc1726b31b9b74d5ebf35f7558578467828583b168d5d26627b309a01c` | yes |
| `fleet-hub` | `dce13bed52efa907ff160a34e49c614f2bc03099e7f2916f48305f68f83143f0` | yes |
| `fleet-node` | `ad9091491d514a4f3412030a7e954414c431f82d8363aa5791c39e6467f8b929` | yes |
| `fleet-next-composition` | `1a92993c654e6ea555cbb8c7193d90c6d29bc46444ef54332ce1d4454fb583b0` | **no** |

The composition has zero code members and declares
`install.independently_installable: false`; it pins the four units, it is not
an installable payload.

## Invariance

Two fresh builds at `47030bc` are byte-identical 5/5. The same five digests are
produced under:

* normal Git config;
* `GIT_CONFIG_SYSTEM` with `core.autocrlf=true`;
* `HOME/.gitconfig` with `core.autocrlf=true`.

The fixed builder refuses a CRLF transform with `BLOCKED_LINE_ENDING_DRIFT`.
The historical #514 RED digests (`d9f57a3d…`, `5a2f3389…`, `38316cf9…`,
`1156a806…`) are preserved in `../evidence-w19/ENVIRONMENT-MATRIX.json`.

## QA from a full clone

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit 47030bc1d9a728c4ce99f2b27ccd16017aea0eab \
  --version 1.0.0+wave19e \
  --expect-checksums integrations/fleet-next-wave18-artifact-builder/evidence-main/ARTIFACTS.sha256 \
  --out /tmp/w19e-qa --json
```

Rebuilds, compares 5/5, runs the negative control and exits 0 only if both
hold. A shallow hosted PR checkout may not carry `47030bc`; the CI test then
builds `HEAD` twice, compares 5/5 and runs the negative control.

## Verdict

`technical_verified: true` · `source_state: BLOCKED_OWNER` ·
`release_permitted: false` · `REAL_DEVICE NOT_RUN`. This is a builder
candidate, not a production release.
