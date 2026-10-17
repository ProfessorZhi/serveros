# Fleet V1 — I1 release candidate manifest

This directory is the **I1 integration artifact** for campaign
[#244](https://github.com/ProfessorZhi/serveros/issues/244) /
[#250](https://github.com/ProfessorZhi/serveros/issues/250). It is additive:
nothing here changes a lane's implementation.

It answers three questions and refuses to answer a fourth.

1. **What is pinned?** Every candidate PR and external repository at an exact
   40-hex revision, with its declared base and whether that base is itself a PR.
2. **What was observed?** Each acceptance gate at one of five evidence levels:
   `SOURCE`, `MOCK`, `LOCAL`, `REAL_DEVICE`, `CLIENT_E2E`.
3. **What is still open?** P0 conditions with the action each one needs.
4. *(refused)* **Does it work?** Not from this directory. A source pin is not a
   build, a local run is not a device, and a device is not an authenticated
   client. The manifest says `RC_CANDIDATE` and the checker keeps it there.

## Files

| File | Purpose |
|---|---|
| `rc-manifest.v1.schema.json` | JSON Schema for the manifest |
| `rc-manifest.2026-10-17.json` | The pinned manifest for this milestone |
| `check_manifest.py` | Dependency-free invariant checker and self-test |
| `QA-MATRIX.md` | Gate-by-gate acceptance matrix with evidence levels and owners |
| `BRANCH-TOPOLOGY.md` | Measured ancestry, sibling relationships and merge policy |

## Running the checker

```sh
python3 integrations/fleet-v1-release/check_manifest.py \
  integrations/fleet-v1-release/rc-manifest.2026-10-17.json

python3 integrations/fleet-v1-release/check_manifest.py --self-test
```

The checker is deliberately narrow and mechanical. It rejects:

- a short or abbreviated SHA (`E024`, `E034`);
- a candidate that is not a Draft (`E035`);
- `installed: true` resting on `SOURCE` evidence alone (`E037`);
- `COMPLETE_V1` while any gate is not `PASS` (`E091`) or while a gate's evidence
  level is below its required level (`E092`);
- `COMPLETE_V1` with open P0 items (`E093`) or without live/client/production
  claims (`E094`);
- an integrator signing its own QA (`E012`, `E013`, `E014`);
- a QA status that contradicts its signature (`E015`–`E018`);
- a missing drift guard, or `COMPLETE_V1` resting on a pin older than the
  snapshot (`E110`–`E115`);
- a source merge performed before every gate passes (`E095`);
- a `PASS` carrying a `BLOCKED_*` code (`E059`);
- a dependency opportunity recorded as a release receipt (`E132`);
- `COMPLETE_V1` without a SIGNED independent `qa_triage` (`E128`, `E129`);
- credential-like material in the manifest (`E100`).

Warnings are not passes. `W011` (`stacks_on` names a PR not pinned here) is
expected in this milestone: the intermediate links of the F1 and N1 chains are
not yet pinned and that is part of what the next milestone must close.

## Scope boundary

- No branch, worktree or file owned by another lane is modified.
- No cross-branch merge, cherry-pick or rebase is performed.
- No production service, credential, client registration or provider write is
  touched.
- Rollback is one `git revert` of this PR's commit: all files are new and live
  under this directory.
