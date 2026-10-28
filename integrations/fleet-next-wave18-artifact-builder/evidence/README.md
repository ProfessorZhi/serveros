# Wave 18 evidence

Produced at code commit `a50d2ec19e5e6e6f74fd9929f9155fd06e0f3f71` from a
`git archive` of that commit, with CUT-A/CUT-B applied to the isolated copy.

| file | what it is |
|---|---|
| `PROVENANCE.json` | source commit/tree/archive digest, patch provenance (owner commit, original/result SHA), unit checksums, isolation, negative control, rollback, release plan, verdict |
| `ARTIFACTS.sha256` | the five tarball checksums |
| `RELEASE-PLAN.json` | Hub/Node install roots, ports, sockets, locks, backend ids, OAuth audiences, collision matrix, shadow decisions |
| `BASELINE.json` | the RED baseline: cross-component leaks and two-artifact boot failures |

The tarballs are reproducible build products and are not committed. Recreate
them byte-for-byte with:

```bash
python3 -I integrations/fleet-next-wave18-artifact-builder/wave18_builder.py \
  --repo . --commit a50d2ec19e5e6e6f74fd9929f9155fd06e0f3f71 \
  --version 1.0.0+wave18 --out /tmp/w18-evidence --json
```

`source.commit` in each unit manifest is the commit above, so `git archive`
plus the two patches is the whole input. `technical_state: GREEN_ARTIFACTS`,
`source_state: BLOCKED_OWNER`, `real_device: NOT_RUN`.
