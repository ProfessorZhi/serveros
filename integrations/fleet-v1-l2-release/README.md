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
| must not carry | `server.py`, `tools.py`, `oauth.py`, `owner_setup.py`, `deploy/**`, `bin/**` | `fleet_mcp/node/**`, `*.local.y*ml`, `bindings/*`, `*.service`, `*.timer`, `*.ps1`, `*.vbs`, `*.pem`, `*.key`, `.env`; and, **for non-source files only**, `*credential*`, `*token*` (a `token_cache.py` is code, a `tokens.json` is a credential) |
| provides | `node-connector` | `hub-ingress`, `mcp-tools` |
| version | independent | independent |

Membership is not a curated list. It is the **import closure of the entrypoint**,
computed by AST with relative imports resolved to absolute and parent-package
`__init__.py` files walked in, so "what is in this artifact" and "what this
artifact will import" cannot disagree.

## What is measured today, stated rather than wished away

Both closures are **30 files**. They differ by an entrypoint, not by a surface:
the node adds `fleet_mcp/node/{__init__,connector}.py`, the hub adds
`fleet_mcp/{server,tools}.py`, and **28 modules are in both**. The node artifact
is therefore refused by default, because its closure also leaves `fleet_mcp` and
needs the repository-root `fleet/` namespace package — through
`fleet_mcp/registry.py`, which does `from fleet.transport import ...` inside a
`try` that raises `INTERNAL` at import:

```
$ python integrations/fleet-v1-l2-release/l2_release.py build \
    --kind l2-node --version 1.4.0+n20261028a --output /tmp/n.tar.gz
REFUSED: the l2-node artifact's closure leaves fleet_mcp and needs in-repo
package(s) ['fleet'] that the artifact does not carry (fleet <- ['fleet_mcp.registry']) …
```

Building succeeds only when the caller names that package
(`--allow-external-in-repo fleet`). An allowance buys a build, never an install:
the flag cannot make the artifact standalone, and the manifest says so in a field
a deployment reader can act on.

## Two flags, because one was too blunt to act on

`standalone: false` used to mean "not external and not depends_on", which is an
arithmetic restatement of the manifest's own lists — a field that could never
catch the thing it was named for. There are two different questions here and the
node answers them differently:

| flag | question | `l2-node` | `l2-hub` |
|---|---|---|---|
| `import_standalone` | does `import <entrypoint>` succeed with only this payload on `sys.path`? | **true** | **false** |
| `runtime_standalone` | does everything the runtime path reaches also import? | **false** | **false** |
| `standalone` | legacy spelling of `runtime_standalone` | false | false |
| `install.independently_installable` | can this be installed without a checkout? | **false** | **false** |

```
$ python integrations/fleet-v1-l2-release/l2_release.py measure /tmp/node.tar.gz
{ "entry": "fleet_mcp.node.connector",
  "import_standalone": true, "runtime_standalone": false,
  "probe": {"fleet_mcp.node.connector": "imports",
            "fleet_mcp.registry": "FleetRefusal"} }
```

The node imports because `connector.py` defers `from ..fleet import FleetService`
into three function bodies (`1124`, `1317`, `1384`); it cannot run because
`fleet_mcp/registry.py:33` raises `FleetRefusal(INTERNAL)` without a checkout and
`fleet_mcp/fleet.py:67` imports it at module level. So the node artifact loads on
a device, comes up, and dies inside a later agent turn — which is exactly the
failure a single boolean cannot describe. The hub gets neither: its import chain
is eager, so it fails to import in the same sandbox.

Both numbers are **measured**, not declared: `measure_standalone` writes the
payload to a temporary directory and imports it in a separate interpreter
(`-I -B`, cwd elsewhere, any `sys.path` entry that can see a `fleet/` dropped),
and `verify` re-runs the same probe rather than believing the manifest. The
manifest carries the probe record next to the claim so the two can be compared.

The **dependency on the repository is pinned like the shared surface is**:
`EXTERNAL_EDGES` fixes one holder (`fleet_mcp.registry`), one module
(`fleet.transport`) and exactly seven names. A second module reaching `fleet`, an
`import fleet.registry`, a `from fleet import transport`, an eighth name, or a
repository package that no pin mentions all fail the build and the verify by name.

The 17 **pinned** modules are frozen in `NODE_SHARED_SURFACE` (the other 11 of
the 28 modules in both are declared node leaves and never needed a pin). Today's
coupling is legal because it is recorded; a module that is not in the tuple and
not under `fleet_mcp.node/` fails the build by name, and a pin entry the payload
no longer reaches — or carries but nothing imports — fails it too, so the list
cannot rot in either direction. `test_all_seventeen_pinned_modules_are_reachable_from_the_entrypoint`
walks the payload by AST to check *reachable*, not merely present.

Cutting the edges is #407's job and is written down in `HANDOFF-407.md` with the
measured numbers (three cuts take the node artifact from 30 files to a standalone
17). Cutting **A alone** — one file, three statements — is measured too, by
`test_cutting_the_connector_to_fleet_edge_flips_runtime_standalone`: 25 files,
`external_in_repo: []`, both flags true, `independently_installable: true`, 12
pinned modules left, and the resulting artifact verifies.

## The manifest

`ARTIFACT.json`, schema `serveros/artifact.v1`: the same name and the same schema
`scripts/build.py` writes and `scripts/release.py` reads — asserted by
`tests/test_fleet_l2_release_separation.py`, not merely intended.

Two shapes matter and are both easy to get wrong.

**`members` is a list of paths, not of records.** `scripts/release.py:739` does
`set(manifest["members"])` and compares it to the files on disk; records there
raise `TypeError` *inside* that comparison — a crash in the release tool instead
of a refusal with a reason. Digests therefore live in an additive
`member_sha256` map, which the existing reader ignores and this verifier requires.

**`ARTIFACT.json` is listed in `members` and has no digest.** That is what
`scripts/build.py:1011-1013` writes, and it has to be: line 739 exempts only
`RELEASE.json` from the "files the build did not produce" check, so an unlisted
`ARTIFACT.json` would be reported as an undeclared file. A manifest cannot carry
its own digest, so `member_sha256` covers every member *except* the manifest, and
both `tree_sha256` and `module_count` are computed over that same set.

```
schema, composition, name, version, kinds, skills, recommended, mcp_servers,
members[path…], member_sha256{path: sha256},
source{entry, module_count, tree_sha256},
l2{kind, entrypoint, platforms, depends_on, requires{python, modules},
   external_in_repo,
   import_standalone, runtime_standalone, standalone, probe{module: verdict},
   install_requires_repo{package: [holder…]},
   install{independently_installable, requires_repository, blocked_by[]},
   shared_surface, provides}
```

The `l2` additions after `external_in_repo` are additive and measured. A reader
that only knows the older shape (`scripts/release.py` and #436's
`verify_l2_layer`) ignores them, which is why adding them cannot break the
release path; this verifier requires them and compares them to a fresh probe.

Archives are deterministic: USTAR, gzip `mtime=0`, empty filename, uid/gid 0,
sorted members, mode 0644. Two builds of the same tree are byte-identical, so
"did this release change" is a comparison and not a conversation.

## The refusals

Each one is a test, and each names the thing it refused. The build and the
verifier call **the same functions** (`_refuse_cross_layer` →
`_refuse_node_members` / `_refuse_hub_members`), and there are tests that hand
each side a payload that did *not* come from this build, because a guard that
only protects the builder protects the thing that cannot lie.

- a node payload carrying `server.py` / `tools.py` / `oauth.py` / `owner_setup.py`
  / `deploy/**` / `bin/**`, or a hub module that is not on the pinned list (a
  module under `fleet_mcp.node/` is the node's own and needs no pin);
- a pin entry the closure no longer reaches — a cut edge must delete its entry in
  the same change;
- a hub payload carrying `fleet_mcp/node/**`, a credential, a binding, a unit, a
  launcher or a private key;
- a payload that lost a member the manifest still promises, or gained one it
  never declared;
- a member whose bytes are not its recorded digest (tampering);
- bytecode (`.pyc`/`.pyo`): a sourceless `fleet_mcp/server.pyc` imports where a
  deny-list written against `server.py` sees nothing;
- a member name with a second spelling (`fleet_mcp/./node/connector.py`), a
  duplicate name, a symlink or a directory, an absolute path or `..`;
- the wrong kind, an unknown kind, a version that drifted from the build, a
  manifest schema that drifted from `serveros/artifact.v1`;
- an `l2` block that contradicts the payload: a node claiming the hub's
  `provides`, a hub listing a shared surface, a `tree_sha256`/`module_count` that
  does not describe the member list;
- an isolation claim the payload does not bear out — `import_standalone`,
  `runtime_standalone`, the `probe` record, `install_requires_repo` or
  `install.independently_installable` differing from a fresh probe, and
  `standalone` disagreeing with `runtime_standalone`, whose legacy spelling it is;
- an artifact that needs a checkout calling itself
  `independently_installable: true`, or losing its `blocked_by` list — the build
  flag buys a build, and no manifest field can turn it into an install;
- an external edge that is not the pinned one: `fleet` reached from a second
  module, `import fleet.registry`, `from fleet import transport`, an eighth name,
  or a repository package (`runtime`) that neither `requires.modules` nor a pin
  mentions — and `fleet` itself declared under `requires.modules`, where
  `pip install fleet` would find some unrelated distribution;
- an import the manifest does not declare (`numpy`), an intra-package import the
  payload does not carry (`from .report import x` with no `fleet_mcp/report.py`),
  a `depends_on` the payload does not carry;
- a pin entry the payload carries but never reaches from its entrypoint — the pin
  is about what the artifact *uses*, and shipping a module nothing imports is
  dead weight in a release that still counts as shared surface;
- a **vendored CloudCLI tool schema** — a `.json` containing `inputSchema` and
  `run_get`. The provider forwards the running CLI's schema; a payload that ships
  a copy keeps answering after the CLI moves;
- a platform the kind does not support (`l2-hub` on `windows`), or a payload that
  under-declares the platforms its kind ships to — the build applies the same
  rule, so it cannot emit what the verifier refuses.

## Evidence grading

| claim | status |
|---|---|
| builds, reads back, verifies, refuses — both kinds | **REAL**, 77 tests (76 pass + 1 skip), offline |
| byte-identical rebuild | **REAL** (two builds compared) |
| member list is the shape `release.py` consumes | **REAL** (`test_the_member_list_is_the_shape_the_release_reader_consumes`) |
| the node imports without a checkout | **REAL** (`import_standalone: true`, measured by importing it in a separate interpreter) |
| the hub does not import without a checkout | **REAL** (`import_standalone: false`, `FleetRefusal`) |
| the node's runtime path needs a checkout | **REAL** (`fleet_mcp.registry` raises `FleetRefusal` in the same sandbox) |
| the repository dependency is one holder, one module, seven names | **REAL** (`EXTERNAL_EDGES`, pinned and re-checked on every verify) |
| all 17 pinned modules are reachable from the entrypoint | **REAL** (AST walk, not a count) |
| cutting edge A makes the node independently installable | **REAL ON A COPY** — 25 files, both flags true, verifies; the cut itself is #407's to make |
| `release.py` verifies `l2-node`/`l2-hub` | **NOT OURS** — #433/#436 own `scripts/release.py`; #436 @ `85493198` reads this manifest shape (see `HANDOFF-419.md` §9) |
| installed on a node or the hub | **NOT_RUN** — this directory installs nothing |
| signed artifacts | **NOT DONE** — digests are recorded, nothing is signed |
| Windows/Linux runtime behaviour of the artifacts | **NOT_RUN** — packaging is platform-tested, execution is not |

One limit, stated rather than implied: the verifier can tell a repository package
from a wheel by name only for the names in `EXTERNAL_EDGES`. A hand-written
manifest could list a second repository package (`runtime`) under
`requires.modules` and the byte-level check would treat it as a pip dependency.
The build cannot — it measures the third-party set from the source tree, where
`runtime/` is a directory and not a wheel — so a payload that passes the build is
not reachable that way; an artifact that never went through this build is.

## Run it

```
python integrations/fleet-v1-l2-release/l2_release.py build \
  --kind l2-node --version 1.4.0+n20261028a \
  --allow-external-in-repo fleet --output /tmp/node.tar.gz
python integrations/fleet-v1-l2-release/l2_release.py verify /tmp/node.tar.gz --kind l2-node
python integrations/fleet-v1-l2-release/l2_release.py pair /tmp/old.tar.gz /tmp/new.tar.gz --kind l2-node
python integrations/fleet-v1-l2-release/l2_release.py extract /tmp/node.tar.gz /tmp/rev
python integrations/fleet-v1-l2-release/l2_release.py measure /tmp/node.tar.gz
python -m pytest tests/test_fleet_l2_release_separation.py -q
```

`measure` imports the payload the way a fresh machine would and prints the two
flags, the per-module probe record and the repository dependencies. It runs the
same probe `build` and `verify` run; it is the evidence behind the manifest's
fields, available without reading the manifest.

`extract` is what connects this directory to `scripts/release.py`: that tool
stages a directory (`shutil.copytree`), not an archive. It runs only after
`read_artifact` has refused unsafe names, duplicate names, links and tampered
bytes, and it writes into an empty directory only — the files it lays down are
exactly the ones `members` names, so the release path stages this artifact and
not whatever was in that directory before.

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
