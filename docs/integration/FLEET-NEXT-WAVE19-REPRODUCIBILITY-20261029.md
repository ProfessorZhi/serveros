# Wave 19 · Hub/Node artifact reproducibility repair (offline)

Date: 2026-10-29
Scope: `ProfessorZhi/serveros`, source and offline builds only.
Role: original builder owner of #505/#509, repairing #514's finding.
Base: #509 head `3520d0c886a3abe0ba712eeb855ca2bcdd65579d`, stacked on #505
`3512c55` and #431 `517ef91`.

## 0. Read this first

No install was run, no service manager was addressed, no port or socket was
bound, no credential/OAuth/Tailscale/Feishu operation was performed, and no
Legacy configuration was changed. **REAL_DEVICE NOT_RUN.**

Only these paths were written:

```
integrations/fleet-next-wave18-artifact-builder/wave18_builder.py
integrations/fleet-next-wave18-artifact-builder/evidence-w19/**
integrations/fleet-next-wave18-artifact-builder/evidence/README.md  (supersession note)
tests/test_fleet_next_wave18_artifact_builder.py
docs/integration/FLEET-NEXT-WAVE19-REPRODUCIBILITY-20261029.md
```

`fleet_mcp/**`, `fleet/**`, `runtime/**`, `scripts/release.py`, the V2-rooted
#407/#461/#467/#473 sources and every Legacy file stayed read-only. No merge to
`main`; the merge decision belongs to the W19 RC owner.

## 1. #514's finding, reproduced exactly

#514 rebuilt from source commit `ff514f4` with the committed builder and got:

| unit | #509 claimed | #514 produced |
|---|---|---|
| `fleet-authority` | `ace00295…` | `ace00295…` **match** |
| `fleet-hub` | `4ca314f5…` | `d9f57a3d…` |
| `fleet-node` | `381dbd8e…` | `5a2f3389…` |
| `fleet-runtime-base` | `333caea1…` | `38316cf9…` |
| `fleet-next-composition` | `2a0933b9…` | `1156a806…` |

This lane reproduced all four differing digests **byte-for-byte** from a tree
where the two reviewed patches were applied with `core.autocrlf=true`. The
full values are in `evidence-w19/ENVIRONMENT-MATRIX.json`:

```text
old_builder_crlf_apply_only
  hub          d9f57a3d22b6bba1d16bd0f9afecdfd477edafadc89cad44027ba20a3d8cca50
  node         5a2f338938be363dc0ea570b9fdd8a8862945838ea46a61d69ca3df26d382149
  base         38316cf96b96bddfaece9da181799174e6ae43c78e5168c2b6edfe27d11f8214
  composition  1156a80689a245758d2d7d44a5056c82dd5731e56eb53d994f4d0e2228b7ba9b
  authority    ace00295ca00a84026a8ee70b6a2328c29738cdf93fa77c0fba91533d70103e9
```

## 2. Root cause, not a stale text list

`git apply` honoured the host's `core.autocrlf=true` and wrote the **patched
text files** as CRLF. Everything else was copied from the LF `git archive`.
That explains the exact asymmetry:

* `fleet-authority` carries `fleet/transport.py` and its contract, neither of
  which any patch touches — it stayed identical;
* `fleet-runtime-base`, `fleet-hub` and `fleet-node` carry patched files —
  their bytes changed;
* `fleet-next-composition` carries no files but pins the other units' SHA256 —
  it changed because its inputs did.

The archive ordering, gzip header, mtime, paths and version string were not
the cause. The transform input was. A second hostile mode
(`GIT_CONFIG_SYSTEM` with `autocrlf=true`) also converts the archive and moves
all five; both modes are in the matrix.

## 3. Fix

The transform, not the checksum file, changed:

1. **Hermetic Git for every internal call** — `GIT_CONFIG_NOSYSTEM=1`,
   `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_ATTR_NOSYSTEM=1`, and
   `-c core.autocrlf=false -c core.eol=lf -c core.safecrlf=false
   -c core.precomposeUnicode=false -c core.ignoreCase=false
   -c core.fileMode=false`. `git apply` also uses `--whitespace=nowarn`.
2. **CRLF is refused, never published** — the archive source, every patched
   result and every text member are checked; `BLOCKED_LINE_ENDING_DRIFT` is
   raised rather than producing a second digest.
3. **The zero-member composition is explicitly non-installable** —
   `install.independently_installable: false` and a `blocked_by` reason. It is
   a manifest that pins the four units, not a payload.

## 4. GREEN

Reference: source `ff514f4`, version `1.0.0+wave18`, builder at `d0d12bb`
(`sha256:e8d0d710…`).

| unit | tarball sha256 | installable |
|---|---|---|
| `fleet-runtime-base` | `333caea13df2e17de068fb69ab6d47299eed938a8767607b7cfa2b6b601f7649` | yes |
| `fleet-authority` | `ace00295ca00a84026a8ee70b6a2328c29738cdf93fa77c0fba91533d70103e9` | yes |
| `fleet-hub` | `4ca314f502a80d17c31bcccf8d460df6163afc6ec9083f1d0b8f9ed58ed86534` | yes |
| `fleet-node` | `381dbd8e1404082d4d9af4c952019e2a0e80a4b859ec639d052d4059a227251b` | yes |
| `fleet-next-composition` | `82983d7f98f1049b64fa5fb3085af17ad6b60c983b2bedb994589bc4da840c6f` | **no** |

* The four **code** units reproduce #509's claimed digests exactly.
* The composition digest moved because its manifest now tells the truth; that
  is a correctness fix, not a checksum edit.
* Two builds of the same commit are **byte-identical, 5/5**, including the
  `git archive` input.
* `fixed_builder_normal == fixed_builder_hostile_system ==
  fixed_builder_hostile_global` (the test runs all three).
* The fixed builder on the pre-made CRLF tree raises
  `BLOCKED_LINE_ENDING_DRIFT`; the old builder on that tree produces #514's
  four digests (the RED test).
* Isolation, negative control and rollback are unchanged: Hub/Node boot with
  no checkout and no neighbour; removing `errors.py`, `config.py` or
  `transport.py` fails; independent and compensating rollback leave the Legacy
  0.1.0 fake's `get_session`/`get_run`/`send`, identities and deliveries
  unchanged.

## 5. Independent QA: PR head is the only input

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit ff514f4bd447588e77274753c34f0a664258ca64 \
  --version 1.0.0+wave18 \
  --expect-checksums integrations/fleet-next-wave18-artifact-builder/evidence-w19/ARTIFACTS.sha256 \
  --out /tmp/w19-qa --json
```

The command rebuilds, compares all five digests, runs the negative control and
exits 0 only if both hold. The test
`test_independent_qa_rebuild_matches_published_checksums` performs the same
check in CI from the committed pack.

## 6. Verdict

| field | value |
|---|---|
| `technical_verified` | **true** — 5/5 reproducible, environment-invariant, isolation + negative control + rollback pass |
| `source_state` | `BLOCKED_OWNER` — the CUT-A/CUT-B transforms are not landed in `fleet_mcp/**` |
| `release_permitted` | **false** — #431/#505 stack remains blocked; a real install/co-install is outside this lane |
| `real_device` | `NOT_RUN` |

## 7. Evidence

`integrations/fleet-next-wave18-artifact-builder/evidence-w19/`:

| file | what |
|---|---|
| `PROVENANCE.json` | source commit/tree/archive digest, hermetic transform record, per-patch owner/input/original/result, units, isolation, negative control, rollback |
| `ARTIFACTS.sha256` | the five reference digests |
| `ENVIRONMENT-MATRIX.json` | old/new builder under normal, CRLF-apply, hostile-system and CRLF-tree modes |
| `BASELINE.json` | RED cross-component leaks and two-artifact boot failures |
| `RELEASE-PLAN.json` | footprints + #483/#480 veto |

The Wave 18 pack in `../evidence/` is retained for history and marked
superseded for digests.

## 8. Non-goals honoured

* No `main` merge, no real install, no service/port/socket change.
* No OAuth, key, Tailscale, Feishu, paid-model or Legacy configuration change.
* No V2 #407 merge; no `fleet_mcp/**` edit; no merge of #431/#505.
* No artifact is fabricated; the zero-member composition is not installable.
* `REAL_DEVICE NOT_RUN`.
