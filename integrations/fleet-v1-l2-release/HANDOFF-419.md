# Handoff to the `scripts/release.py` owner (#419) · registering `l2-node` and `l2-hub`

`scripts/release.py` has an owner in this round, so nothing here writes it. This
is the exact contract the L2 work needs from it — five edits and one invariant —
plus what happens today, which is that the release path refuses our kinds by
name (`read_artifact_manifest`, `477-485`) and therefore verifies nothing we
build. That refusal is correct; it is also the reason `l2_release.py` currently
carries its own verifier.

## 1. The kinds

```python
NODE_RUNTIME = "node-runtime"
PLUGIN = "plugin"
L2_NODE = "l2-node"          # the per-machine connector
L2_HUB = "l2-hub"            # the Tokyo hub ingress
ARTIFACT_KINDS = (NODE_RUNTIME, PLUGIN, L2_NODE, L2_HUB)
```

## 2. Their required members

Artifact root = the repository-relative tree (`fleet_mcp/…`), the same layout
`build.py` produces (no nested `dist/`). These are the members whose absence
means the kind is not the thing it claims to be:

```python
    L2_NODE: ("fleet_mcp/__init__.py", "fleet_mcp/node/__init__.py",
              "fleet_mcp/node/connector.py"),
    L2_HUB: ("fleet_mcp/__init__.py", "fleet_mcp/server.py"),
```

## 3. The dispatch branch — this is the one that must not be forgotten

`verify_revision` (`620-628`) refuses a kind that is not in `ARTIFACT_KINDS`, then
falls through to `verify_node_runtime` for *every* kind it does not name. The
moment the two kinds above are registered, that fall-through becomes reachable for
a payload that has no daemon: `verify_node_runtime` will ask an L2 artifact for
`bin/serverosd` and refuse a correct release for a reason that is about a
different product. Insert before the fall-through:

```python
    if kind == PLUGIN:
        return verify_plugin(rev_dir)
    if kind in (L2_NODE, L2_HUB):
        return verify_l2_layer(rev_dir, kind=kind)
    return verify_node_runtime(rev_dir, state_dir)
```

## 4. `status` has a second fall-through (line ~1020)

`status` special-cases `PLUGIN` ("nothing installed outside the revision
directory") and otherwise compares unit, CLI shim and symlink. An L2 revision has
no unit and no shim, so a healthy one would print `DIVERGENT` and exit 1. It needs
the same treatment as `PLUGIN`:

```python
    if kind in (PLUGIN, L2_NODE, L2_HUB):
        print("unit installed    n/a (no unit outside the revision directory)")
        print("cli shim          n/a")
        ok = cur is not None and (root / "current").is_dir()
```

`promote` (`1105`) and `rollback` (`1175`) need **no** change: both already gate
unit rendering on `kind == NODE_RUNTIME`.

## 5. The invariant on `REQUIRED_MEMBERS`

`resolve_kind`'s structural fallback (`540-547`) accepts a kind only when its
required members fit and **exactly one** kind fits. So the two new member lists
must stay disjoint from each other and from the existing two — which they are:
an L2 node directory has `fleet_mcp/node/connector.py` and no
`fleet_mcp/server.py`; an L2 hub directory has the reverse; neither has
`bin/serverosd` or `.claude-plugin/plugin.json`. A directory that somehow has
both `connector.py` and `server.py` will be refused as ambiguous, which is the
right answer.

## 6. What `verify_l2_layer(rev_dir, *, kind)` must check

Six checks; the first four already exist in `verify_plugin`/`_verify_layer` shapes
and can be shared:

1. `ARTIFACT.json` present and its `kinds` is exactly `[kind]`
   (`read_artifact_manifest` already does this once the kinds are registered);
2. `set(manifest["members"])` equals the files on disk minus `RELEASE.json` —
   note that **`members` is a list of paths**, which is what makes
   `set(recorded)` at line 739 work, **and that it includes `ARTIFACT.json`
   itself**, exactly as `scripts/build.py:1011-1013` writes it: line 739 exempts
   only `RELEASE.json`, so an unlisted manifest would be reported as a file the
   build did not produce. The L2 manifest carries an additive `member_sha256` map
   covering every member *except* the manifest (a file cannot hold its own
   digest), and a payload whose bytes do not match it must be refused;
3. required members present (the shared loop at `622-625` already does this);
4. layer separation — `L2_NODE` must not contain `fleet_mcp/server.py`,
   `fleet_mcp/tools.py`, `fleet_mcp/oauth.py`, `fleet_mcp/owner_setup.py`,
   `fleet_mcp/deploy/**`, `fleet_mcp/bin/**`, or any `fleet_mcp.*` module that is
   neither under `fleet_mcp.node` nor in
   `NODE_EXCLUSIVE ∪ NODE_SHARED_SURFACE` — **both sets**, not the pin alone: 13
   of the node's modules (`fleet_mcp`, `config`, `providers`, `providers.base`,
   `providers.cloudcli`, `node_protocol`, `errors`, `identity`, `local_roots`,
   `runtime_identity`, `version`, `fleet_mcp.node`, `fleet_mcp.node.connector`)
   are declared node leaves and are not in the pin, so a check against the pin
   alone refuses the connector's own payload. A pin entry the payload no longer
   contains is also a failure (a cut edge must delete its entry);
   `L2_HUB` must not contain `fleet_mcp/node/**` or any machine-specific file
   (`*.local.y*ml`, `bindings/*`, `*.service`, `*.timer`, `*.ps1`, `*.vbs`,
   `*.pem`, `*.key`, `.env`), and — **for non-source files only** — `*credential*`
   / `*token*`: a `token_cache.py` is code, a `tokens.json` is a credential;
5. the declared third-party modules are a **superset** of what the payload
   imports (a manifest that does not name `yaml` installs into an `ImportError`;
   naming an extra module is a smaller sin than hiding one), and every
   `fleet_mcp.*` module the payload imports is a module it carries;
6. `PLATFORMS[kind]` is a **subset of** the declared platforms — the payload must
   claim every platform its kind ships to, and no platform outside it, so
   `l2-hub` never declares `windows` and `l2-node` always declares both.

A payload that carries bytecode (`.pyc`/`.pyo`), a member name with a second
spelling (`fleet_mcp/./node/connector.py`), a duplicate name, or a link/directory
member should be refused too; those are one-line checks and the reference
implementation has them.

`integrations/fleet-v1-l2-release/l2_release.py` implements these six and is the
reference; `verify_l2_layer` does not have to import it (an integration directory
is not a library), but the two must agree on the six — the extra checks there
(digests, `l2`-block consistency, name canonicalisation) are this build's own and
are not a request. A parity test is already staged in
`tests/test_fleet_l2_release_separation.py` and skips until the kinds are
registered.

## 7. What is not being asked

No new lifecycle, no new directory layout, no change to `stage`/`promote`/
`rollback` semantics, no change to `ARTIFACT_MANIFEST` or
`ARTIFACT_MANIFEST_SCHEMA` (`l2_release.py` asserts both against your module, so
a rename there fails our build by name). Nothing in this handoff installs,
starts, or restarts anything.

## State after each side

| | today | after this lands |
|---|---|---|
| `release.py` reads our manifest | refuses kinds by name | verifies both kinds |
| `l2_release.py` verifies our manifest | **REAL** (56 tests: 55 pass, 1 skip) | unchanged, plus parity test stops skipping |
| our manifest shape matches yours | **REAL** (asserted) | unchanged |
