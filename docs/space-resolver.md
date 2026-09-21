# Space Resolver

The Space Resolver is the placement counterpart of the [Context
Capsule](context-capsule.md). Context answers *"what is my working
environment"*; Space answers *"where does this object canonically belong, and
does it need to move"*.

It is a **read-only decision kernel**. It is given inputs and returns a record.
Applying the record is somebody else's job.

## Design rule

> The resolver decides **what** the canon is. An adapter decides **where** the
> bytes go. A locator the kernel invented is a policy for one tenant's current
> folder layout, frozen into the control plane.

This rule exists because it was broken. An earlier placement implementation
carried `feishu://wiki/<space>/项目/<project>/工作项/<item>` and a list of
object classes named `wiki_current_view`, `wiki_work_log`, `wiki_milestone_report`
and `drive_item_attachment` inside what was supposed to be a deterministic
primitive. It stopped being a kernel and became a policy engine for one Feishu
tree — and it would have gone silently wrong the first time a human reorganised
a folder. The two failure modes are therefore made *unrepresentable*, not merely
discouraged:

- the canonical answer is a **logical** placement — surface + role + context —
  never a cloud path;
- `target_locator` is `null` unless the caller supplied a `target_template`, and
  a template with an unfilled slot yields no locator at all rather than half a
  path;
- `tests/test_space_resolver.py` asserts the module's imports and its string
  literals, so a future edit cannot quietly reintroduce a cloud client.

## Three orthogonal dimensions

A single "role" list cannot carry both *what an object is* and *where it lives*.
That conflation is what produced `drive-meeting`, `drive-staging` and
`minute-sidecar`. The vocabulary therefore has three independent axes:

| Dimension | Question | Examples |
|---|---|---|
| `surface` | **where** it physically lives | `local`, `git`, `github`, `wiki`, `drive`, `minutes`, `mydocs` |
| `object_type` | **what** it physically is | `document`, `recording`, `worktree`, `pr`, `transcript` |
| `semantic_role` | **why** it exists | `deliverable`, `knowledge`, `meeting`, `residue`, `unclassified` |

A Minute's recording is `surface=minutes, object_type=recording,
semantic_role=source` — not `role=minute-sidecar`. Only the last axis carries
policy, and it is surface-neutral by construction: a test asserts that no role
value contains the name of any surface.

`semantic_role: unclassified` is a real value. It means a human or an Agent must
decide, and the resolver must not decide on their behalf.

## Decisions

| Decision | Meaning |
|---|---|
| `KEEP` | already on its canonical surface; nothing to do |
| `INDEX` | canonical, but the logical index entry that makes it findable is missing |
| `MOVE` | another surface is canonical; always carries `approval: proposal-only` |
| `PROPOSAL_REQUIRED` | the resolver cannot answer — no rule, an undecided role, or a policy that declines to choose |
| `REJECTED` | the request itself is malformed (unknown value, or an impossible surface/type pair) |

`PROPOSAL_REQUIRED` exits `0`: it is a successful determination that a human
must decide. `REJECTED` exits `2`: it is a caller bug, and conflating the two
would hide it.

**A rule that does not exist is not a default.** The absence of a placement rule
yields `PROPOSAL_REQUIRED`, never "so it can stay where it is". "We did not find
anything wrong" is not the same as "this is right".

## Usage

```bash
python3 space/resolver.py --surface mydocs --object-type document \
    --semantic-role knowledge --project litellm

python3 space/resolver.py --request request.json
```

```json
{
  "schema": "serveros/space-decision.v1",
  "decision": "MOVE",
  "canonical_surface": "wiki",
  "canonical_role": "knowledge",
  "target_locator": null,
  "logical_target": {"surface": "wiki", "semantic_role": "knowledge", "project": "litellm"},
  "index": null,
  "reason": [
    "semantic_role=knowledge is canonical on wiki, not on mydocs",
    "Durable knowledge outlives the work that produced it and is canonical on a knowledge surface."
  ],
  "confidence": "high",
  "approval": "proposal-only"
}
```

## What `mydocs` is, and which half of it moves

`mydocs` is the Feishu **My Document Library** — a Wiki-provider space
(`space_type=my_library`) with node/subpage hierarchy, not a Drive-style folder
tree. That matters to the rules below only because it means the surface holds
authored documents rather than files.

It is a **workbench**, and that is a full-time job. Workbench, project, work-item,
report and deliverable material is canonical *there* and does not move: a report
does not become more canonical by being carried somewhere else. Only `knowledge`
and `method` promote, because those outlive the work that produced them.

So the rule is not "anything authored in My Document Library is misplaced". It is
"the part of it that outlives the work is knowledge". An earlier version of the
table promoted all seven, which was correct only while a single curated tree held
both jobs; once curation was split by purpose, the two halves had to be said
separately.

`mydocs`/`meeting` has **no rule on purpose**, and the resulting
`PROPOSAL_REQUIRED` is the table working rather than a hole in it. A meeting's
canon is its Minute, and a minute's sidecars are Drive files; no curated meeting
index exists on a Wiki surface. A workbench note that merely concerns a meeting
is none of those, so it is a question for a human — which is exactly what a
missing rule is for.

The vocabulary and the request/response schema are documented in
[`space/schema.yaml`](../space/schema.yaml); the vocabulary itself is data in
[`space/vocabulary.json`](../space/vocabulary.json).

## Ownership

```text
ProfessorZhi/serveros
├── context/resolver.py     ← canonical Capsule resolver
└── space/
    ├── resolver.py         ← canonical placement resolver
    ├── vocabulary.json     ← the controlled vocabulary (data)
    └── schema.yaml         ← the request/response contract
work-engine
└── workengine/
    ├── context.py          → thin delegation: locates the bundle, invokes it
    └── space.py            → thin delegation, same shape
```

Both modules ship inside the generated runtime artifact (`plugin/bundle.yaml`
declares `context.path` and `space.path`), so the CLI depends on a stable
distribution path and never on a movable source checkout. `work-engine` owns no
placement policy. Neither does `hwz-ops`.

Applying a decision belongs to a surface adapter under
[`workspace-management`](../../knowledge/) — moving a Feishu document, creating a
wiki node, writing an index entry. The resolver itself opens no socket, mutates
no database, scans no tree, and does not know what `lark-cli` is. The API call
is the adapter's job; the approval gate is the decision record's `approval`
field.

## What goes in the vocabulary, and what does not

Declarations only. The vocabulary is **slow-changing**: surfaces, object types,
roles, and the placement rules between them. It carries no observed state — no
count, no hash, no PID, no snapshot filename, no cloud path. Current facts live
in the L3 projection (`state/serveros/federation/<domain>/current.jsonl`) and are
regenerated, never transcribed.

> Maps should change slowly. L3 should change fast. A declaration that embeds
> today's numbers is a declaration that lies tomorrow.

A test enforces this against the shipped file.
