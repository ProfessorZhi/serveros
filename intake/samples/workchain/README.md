# SAMPLE — a work-chain fixture, and NOT this node's business state

Everything under this directory is a fixture. The project names (`sample-alpha`,
`sample-beta`) and the Item ids (`SAMP-*`) exist nowhere else, and the files here
are written to exercise the board's sections and its refusals. **No Item here
records real work, and `CLOSED` here means the fixture is complete — it is not a
claim that anything was delivered.**

It is a fixture and not a mock of the code. `serverosctl work board` runs against
it with nothing stubbed:

```
serverosctl work board \
  --boards-root  intake/samples/workchain/boards \
  --records-root intake/samples/workchain/records \
  --workspace-root intake/samples/workchain
```

The three roots are all given, which is the point of `--workspace-root` existing:
in this fixture a relative `card_locator` is relative to the FIXTURE, while on
the node it is relative to the node's workspace.

## What each row is here to exercise

| Item | State | Why it is in the fixture |
|---|---|---|
| `SAMP-0001` | INBOX | a candidate on a card alone — the entry §6 allows before a brief |
| `SAMP-0002` | ACTIVE | in flight with `design/` and no `plan/`, so one document is missing |
| `SAMP-0003` | DELIVERED | **the unmerged-PR counterexample**: work is done and pushed, the merge has not happened, so it is NOT 已完成 |
| `SAMP-0004` | CLOSED | the one complete Item: outcome set and every required document present |
| `SAMP-0005` | CANCELLED | **the cancellation counterexample**, carrying `superseded_by` |
| `SAMP-0006` | READY | what replaced `SAMP-0005` — a replacement is a reference, not the same Item |
| `SAMP-0007` | ACTIVE | **the empty-document counterexample**: `plan.md` exists and holds only whitespace |
| `SAMP-0101` | WAITING | waiting on somebody else |
| `SAMP-0102` | DELEGATED | off work-engine's mainline; §6 names it nowhere, so its filing is declared |
| `SAMP-0103` | NEEDS_HUMAN | a question only a person can answer |
| `SAMP-0104` | INBOX | carries a card with an `id` that is not where it lives — an UNREADABLE card |

`SAMP-0002` and `SAMP-0007` are both in flight and both carry `design/`. Put
them side by side because their missing documents are missing in different
ways, and the board says so differently: `SAMP-0002` has no `plan` at all, which
is `缺文档`; `SAMP-0007` has a `plan.md` holding two newlines, which is
`空文档`. Only the second is a defect the checker reports, and a fixture that
showed only the first would leave the checker's one real finding untested here —
the document states the board distinguishes are `MISSING`, `NOT_YET_REQUIRED`,
`EMPTY` and `DIVERGES`, and all four are in this fixture.

`SAMP-0104`'s card declares `id: SAMP-9999`. The board reports it rather than
joining it: a document whose identity is not where it lives is a question about
a different thing.

## What is deliberately NOT here

A priority. Both priority columns render `UNPRODUCED` in this fixture and on the
node, because no card anywhere declares one — see
`intake/workchain-contract.yaml#card_extension`.
