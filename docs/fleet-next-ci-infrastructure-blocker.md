# CI infrastructure blocker: no hosted runner is ever allocated

This repository's GitHub Actions **cannot pass a single job**, and that is a
property of the account/runner allocation, not of any diff. This document
records the evidence so a reader can re-run it rather than trust it.

Measured 2026-10-16 against `ProfessorZhi/serveros`.

## What is observed

Every workflow run has failed. Not "most" — every one.

```bash
gh api "repos/ProfessorZhi/serveros/actions/runs?per_page=100" \
  --jq '[.workflow_runs[] | {w:.path, c:(.conclusion // .status)}]
        | {total: length,
           failures: (map(select(.c=="failure")) | length),
           by_workflow: (group_by(.w)
             | map({(.[0].w): (map(select(.c=="failure")) | length)}) | add)}'
# {"total":57,"failures":57,
#  "by_workflow":{".github/workflows/fleet-next-isolated-contracts.yml":55,
#                 ".github/workflows/fleet-plugin-zip-lab.yml":2}}
```

The signature is a job that never reached a runner:

```bash
RID=$(gh api "repos/ProfessorZhi/serveros/actions/runs?per_page=1" --jq '.workflow_runs[0].id')
gh api "repos/ProfessorZhi/serveros/actions/runs/$RID/jobs" \
  --jq '.jobs[] | {name, runner_name, runner_group_name, labels,
                   started_at, completed_at, steps: (.steps | length), conclusion}'
# {"name":"offline-unit-contracts","runner_name":"","runner_group_name":"",
#  "labels":["ubuntu-latest"],"started_at":"...:04Z","completed_at":"...:07Z",
#  "steps":0,"conclusion":"failure"}
# {"name":"runner-smoke", ... "steps":0, "conclusion":"failure"}
```

`runner_name` is empty, `steps` is `0`, and the job reaches `failure` in about
three seconds. The `runner-smoke` job's only step is `python3 --version`, so it
cannot fail for any reason connected to this repository's code.

## What is NOT the cause

Every repository-level Actions setting is the ordinary, permissive default:

```bash
gh api repos/ProfessorZhi/serveros/actions/permissions
#   {"enabled":true,"allowed_actions":"all","sha_pinning_required":false}
gh api repos/ProfessorZhi/serveros/actions/permissions/workflow
#   {"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}
gh api repos/ProfessorZhi/serveros/actions/permissions/selected-actions
#   409 "All actions and workflows are allowed on this repository"
gh api repos/ProfessorZhi/serveros/actions/workflows --jq '.workflows[] | {name,path,state}'
#   both workflows: "state":"active"
```

The repository is **private**, so its `ubuntu-latest` jobs consume the
account's included Actions minutes. Billing is the remaining candidate, and it
is deliberately **not** inspected here: reading it needs the `user` token scope,
and changing it is an owner action this work is not authorized to take.

## What this means for a pull request

A red check on this repository carries **no information about the diff**. Two
consequences follow, and both are load-bearing:

1. A PR here may not claim "CI is green", and may not treat a red check as a
   code finding to fix. The verification evidence is a local, bounded suite
   (see the PR body), not a check mark.
2. Merging into the non-production integration branch is done under an
   **explicitly documented infrastructure waiver**, never as "CI passed".

## What would change the answer

Somebody with owner rights would need to look at the account's Actions billing
/quota, or attach a self-hosted runner. Neither is done, proposed, or configured
by any file in this repository.
