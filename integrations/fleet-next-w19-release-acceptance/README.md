# W19 release acceptance

The independent gate for the W19 landing. Two verdicts, six layers, and a retire
map that is measured rather than described.

*   `LANDING_VERDICT` — may this tree be merged into `main`?
*   `RELEASE_VERDICT` — may this be called V1?

They are separate because #515 separates them in its own words ("开发合并 ≠ 正式
RELEASED"), and because the two answers differ today: the landing is green, and
nothing has run on a device.

## What a layer means

Each layer asks one narrow question — *does the landing's own delta leave this
layer sound?* — not *is the whole repository release-ready on this layer*. A
release has extra questions that a merge does not; those travel as findings with
`scope=RELEASE` instead of as a red merge layer, so the two verdicts cannot be
argued from the same number.

| layer | the question | as of 2026-10-29 |
| --- | --- | --- |
| CODE | does the delta introduce or worsen a code defect? | READY |
| UNIT | do the delta's own tests pass at the exact head? | READY |
| CI | is the hosted gate green at the exact head, with 0 new failures? | READY |
| INDEPENDENT_QA | did an independent pass run, and are its results recorded here? | READY |
| ARTIFACT | does the delta change any byte an artifact pin covers? | READY |
| REAL_DEVICE | has a device run this build? | NOT_RUN |

`REAL_DEVICE` is `NOT_RUN`, not green: the directive forbids a device gate in
this task. It blocks `RELEASE_VERDICT` and does not block `LANDING_VERDICT` —
a development merge is not a release.

## Reproducing the retire map

Nothing in the map is typed by hand. It comes from `git ls-tree` on the commits
themselves, because a file list can match while contents do not, and because
`core.autocrlf` makes the worktree lie about content.

```sh
git clone https://github.com/ProfessorZhi/serveros.git clone && cd clone
git fetch origin '+refs/heads/main:refs/heads/main'
for n in 483 506 507 508 509 512 513 514 516 517 518 519; do
  git fetch origin "+refs/pull/$n/head:refs/heads/p$n"
done
python w19_measure.py --repo . --pin release_pin_w19.json --out map.json
python -c "import json,w19_evidence;print(json.dumps(w19_evidence.build(json.load(open('map.json'))),indent=1))"
```

`release_pin_w19.json` states which refs to read and what each PR claims to own.
That claim is written from each PR's title and description, independently of
what the tree turned out to contain — otherwise the "added outside its own
scope" check would be measuring itself. A PR is `SUPERSEDED_ANCESTOR` only when
another PR's head literally contains its head (commit ancestry) or when it has
no file of its own that is not already in `main`. That is the only evidence on
which closing a PR loses nothing.

## Files

| file | what it is |
| --- | --- |
| `w19_gate.py` | the decision rules: layers, findings, the two verdicts, `compare()`, the retire classifier |
| `w19_measure.py` | `pins + a clone -> the retire map`, by blob comparison |
| `w19_evidence.py` | the recorded judgements, and the document the verdicts are read from |
| `release_pin_w19.json` | which refs, and what each PR claims to own |
| `evidence/w19-acceptance-20261029.json` | the measured map and the verdicts, as of the commits named inside |

Tests: `tests/test_fleet_next_w19_release_acceptance.py`.

## What this gate does not do

It does not merge, close, delete, install, or release anything. It does not run
a device gate, call a plugin creator, touch a service, or spend on a paid model.
`RELEASE_VERDICT = BLOCKED` is a statement about this build, not a verdict on
the people who built it.
