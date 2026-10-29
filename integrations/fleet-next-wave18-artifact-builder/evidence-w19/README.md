# Wave 19 evidence · reproducibility repair

This pack supersedes `../evidence/` for the artifact digests. The earlier pack
recorded what a normal Linux build produced; it did not survive a host with
`core.autocrlf=true`. That was #514.

## Reference build

Source commit `ff514f4bd447588e77274753c34f0a664258ca64`, version
`1.0.0+wave18`, built by `wave18_builder.py` at `d0d12bb`
(`sha256:e8d0d71053f1dea6e3380ffc8006a709af05b3df9eb1c3757c9855615953da60`).

| unit | tarball sha256 | installable |
|---|---|---|
| `fleet-runtime-base` | `333caea13df2e17de068fb69ab6d47299eed938a8767607b7cfa2b6b601f7649` | yes |
| `fleet-authority` | `ace00295ca00a84026a8ee70b6a2328c29738cdf93fa77c0fba91533d70103e9` | yes |
| `fleet-hub` | `4ca314f502a80d17c31bcccf8d460df6163afc6ec9083f1d0b8f9ed58ed86534` | yes |
| `fleet-node` | `381dbd8e1404082d4d9af4c952019e2a0e80a4b859ec639d052d4059a227251b` | yes |
| `fleet-next-composition` | `82983d7f98f1049b64fa5fb3085af17ad6b60c983b2bedb994589bc4da840c6f` | **no** |

The first four match #509's record exactly. The composition moved from
`2a0933b9…` because its manifest now declares
`install.independently_installable: false` with a `blocked_by` reason: it is a
zero-member manifest that pins the four units, not an installable payload.

## Why the old record did not reproduce

`git apply` honoured the host's `core.autocrlf=true` and wrote the patched
text files as CRLF. The authority unit contains no patched file, so it stayed
identical; the Hub, Node and Base moved; the composition moved because it pins
their digests. `ENVIRONMENT-MATRIX.json` contains the exact old/new digests for
each mode, including #514's four prefixes.

## Reproduce (QA: PR head is the only input)

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit ff514f4bd447588e77274753c34f0a664258ca64 \
  --version 1.0.0+wave18 \
  --expect-checksums integrations/fleet-next-wave18-artifact-builder/evidence-w19/ARTIFACTS.sha256 \
  --out /tmp/w19-qa --json
```

`--expect-checksums` rebuilds, compares all five digests byte-for-byte, and
runs the negative control (removing `fleet_mcp/errors.py`,
`fleet_mcp/config.py` and `fleet/transport.py` must each fail the boot). Exit
code is 0 only when both hold.

The builder is hermetic: `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_ATTR_NOSYSTEM=1`,
`core.autocrlf=false`, `core.eol=lf`. A CRLF text member is refused with
`BLOCKED_LINE_ENDING_DRIFT` rather than published as another digest.
