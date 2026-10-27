# D7 · the node release and the hub release

Two artifact kinds, two manifests, two versions, two verifiers, one source tree.

D7 of the V1 gap plan says the Tokyo hub and the per-machine node connector still
share one release surface: both are `fleet_mcp/`, versioned together, shipped
together, rolled back together. This directory is the part of that work that can
be finished without touching the files another agent owns: it **describes** and
**builds** the two artifacts and **refuses** the ways they can lie. It does not
install anything, start anything, or connect anything.

| | `l2-node` | `l2-hub` |
|---|---|---|
| name | `fleet-l2-node` | `fleet-l2-hub` |
| entrypoint | `fleet_mcp.node.connector` | `fleet_mcp.server` |
| runs on | `linux`, `windows` | `linux` |
| required members | `fleet_mcp/__init__.py`, `fleet_mcp/node/__init__.py`, `fleet_mcp/node/connector.py` | `fleet_mcp/__init__.py`, `fleet_mcp/server.py` |
| must not carry | `server.py`, `tools.py`, `oauth.py`, `owner_setup.py`, `deploy/**`, `bin/**` | `fleet_mcp/node/**`, `*.local.y*ml`, `bindings/*`, `*.service`, `*.timer`, `*.ps1`, `*.vbs`, `*.pem`, `*.key`, `.env`, `*credential*`, `*token*` |
| provides | `node-connector` | `hub-ingress`, `mcp-tools` |
| version | independent | independent |

Membership is not a curated list. It is the **import closure of the entrypoint**,
computed by AST with relative imports resolved to absolute and parent-package
`__init__.py` files walked in, so "what is in this artifact" and "what this
artifact will import" cannot disagree.

## What is measured today, stated rather than wished away

Both closures are **30 files**. They differ by an entrypoint, not by a surface:
the node adds `fleet_mcp/node/{__init__,connector}.py`, the hub adds
`fleet_mcp/{server,tools}.py`, and 17 modules are in both. The node artifact is
therefore refused by default, because its closure also leaves `fleet_mcp` and
needs the repository-root `fleet/` namespace package — through
`fleet_mcp/registry.py`, which does `from fleet.transport import ...` inside a
`try` that raises `INTERNAL` at import. Both artifacts are **not standalone**:

```
$ python integrations/fleet-v1-l2-release/l2_release.py build \
    --kind l2-node --version 1.4.0+n20261028a --output /tmp/n.tar.gz
REFUSED: the l2-node artifact's closure leaves fleet_mcp and needs in-repo
package(s) ['fleet'] that the artifact does not carry (fleet <- ['fleet_mcp.registry']) …
```

Building succeeds only when the caller names that package
(`--allow-external-in-repo fleet`), and the manifest then records
`"standalone": false` and `"external_in_repo": ["fleet"]`. That is not a
formality: an artifact installed with it raises at import on a machine without
this checkout, and a manifest that hid that behind a `requires` line would make a
packaging defect look solved. Cutting the edges is #407's job and is written down
in `HANDOFF-407.md` with the measured numbers (three cuts take the node artifact
from 30 files to a standalone 17).

The 17 shared modules are frozen in `NODE_SHARED_SURFACE`. Today's coupling is
legal because it is recorded; a module that is not in the tuple fails the build
by name. When an edge is cut the entry is deleted in the same change, so the list
cannot rot into a claim nobody checks.

## The manifest

`ARTIFACT.json`, schema `serveros/artifact.v1`, the same name and the same schema
`scripts/release.py` writes and reads — asserted by
`tests/test_fleet_l2_release_separation.py`, not merely intended.

One shape matters and is easy to get wrong: **`members` is a list of paths, not
of records.** `scripts/release.py:734` does `set(manifest["members"])` and
compares it to the files on disk; records there raise `TypeError` *inside* that
comparison — a crash in the release tool instead of a refusal with a reason.
Digests therefore live in an additive `member_sha256` map, which the existing
reader ignores and this verifier requires.

```
schema, composition, name, version, kinds, skills, recommended, mcp_servers,
members[path…], member_sha256{path: sha256},
source{entry, module_count, tree_sha256},
l2{kind, entrypoint, platforms, depends_on, requires{python, modules},
   external_in_repo, standalone, shared_surface, provides}
```

Archives are deterministic: USTAR, gzip `mtime=0`, empty filename, uid/gid 0,
sorted members, mode 0644, and the manifest is a member that is not listed in
itself. Two builds of the same tree are byte-identical, so "did this release
change" is a comparison and not a conversation.

## The refusals

Each one is a test, and each names the thing it refused:

- a node payload carrying `server.py` / `tools.py` / `oauth.py` / `owner_setup.py`
  / `deploy/**` / `bin/**`, or a hub module that is not on the pinned list;
- a hub payload carrying `fleet_mcp/node/**`, a credential, a binding, a unit, a
  launcher or a private key;
- a payload that lost a member the manifest still promises, or gained one it
  never declared;
- a member whose bytes are not its recorded digest (tampering);
- the wrong kind, an unknown kind, a version that drifted from the build, a
  manifest schema that drifted from `serveros/artifact.v1`;
- an import the manifest does not declare (`numpy`), a `depends_on` the payload
  does not carry;
- a **vendored CloudCLI tool schema** — a `.json` containing `inputSchema` and
  `run_get`. The provider forwards the running CLI's schema; a payload that ships
  a copy keeps answering after the CLI moves;
- a platform the kind does not support (`l2-hub` on `windows`);
- an unsafe member name (absolute, or `..`).

## Evidence grading

| claim | status |
|---|---|
| builds, reads back, verifies, refuses — both kinds | **REAL**, 35 tests, offline |
| byte-identical rebuild | **REAL** (two builds compared) |
| member list is the shape `release.py` consumes | **REAL** (`test_the_member_list_is_the_shape_the_release_reader_consumes`) |
| `release.py` verifies `l2-node`/`l2-hub` | **NOT DONE** — needs `HANDOFF-419.md`; today it refuses our kinds by name |
| node artifact stand-alone (no repository checkout) | **FALSE TODAY** — measured, `standalone: false`, see #407 |
| installed on a node or the hub | **NOT_RUN** — this directory installs nothing |
| signed artifacts | **NOT DONE** — digests are recorded, nothing is signed |
| Windows/Linux runtime behaviour of the artifacts | **NOT_RUN** — packaging is platform-tested, execution is not |

## Run it

```
python integrations/fleet-v1-l2-release/l2_release.py build \
  --kind l2-node --version 1.4.0+n20261028a \
  --allow-external-in-repo fleet --output /tmp/node.tar.gz
python integrations/fleet-v1-l2-release/l2_release.py verify /tmp/node.tar.gz --kind l2-node
python integrations/fleet-v1-l2-release/l2_release.py pair /tmp/old.tar.gz /tmp/new.tar.gz --kind l2-node
python -m pytest tests/test_fleet_l2_release_separation.py -q
```

`pair` reads both revisions, verifies each, and reports what a rollback would
restore. It restarts nothing; "roll back" here means "the previous artifact is
still readable and still verifies", which is the whole of the property that can
be tested without a machine.

## Handoffs

- `HANDOFF-419.md` — the exact minimal contract for `scripts/release.py` (the
  kinds, the required members, the one dispatch branch that must be added before
  the node fall-through, and the `status` branch). Not written here because that
  file has an owner.
- `HANDOFF-407.md` — the four measured edges that keep the node artifact from
  being standalone, with line numbers and the file count after each cut.
