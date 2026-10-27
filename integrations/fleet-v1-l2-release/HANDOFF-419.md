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
required members fit and **exactly one** kind fits. The two lists are **not
disjoint** — both require `fleet_mcp/__init__.py`, which is what makes them both
L2 artifacts. (An earlier version of this handoff said "disjoint"; the #433
implementer found the mistake by testing it, and the correction is theirs.)

The property that actually makes the fallback single-valued is that **no
directory satisfies two complete lists**: an L2 node directory has
`fleet_mcp/node/connector.py` and no `fleet_mcp/server.py`; an L2 hub directory
has the reverse; neither has `bin/serverosd` or `.claude-plugin/plugin.json`. A
directory that somehow holds both `connector.py` and `server.py` is refused as
ambiguous, which is the right answer.

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

## 8. What changed in #431 after `001eb7c` — re-read this if you copied then

PR #433 documents copying these tables from #431 at **head `001eb7c`**. The next
commit (`2469ec5`) changed the payload in four ways that a copied verifier will
now disagree with. None of them changes the six checks; all of them change what a
correct payload looks like.

1. **`members` now lists `ARTIFACT.json`** (as `scripts/build.py` writes it), and
   the digest map covers every member *except* it. A verifier that compares
   `members` against `on_disk − ARTIFACT.json` will now report the manifest as a
   missing member; a tree digest computed over all of `members` will disagree.
   Compute both over `members − {ARTIFACT.json}`.
2. **Member names are canonicalised**: `PurePosixPath(name).as_posix() == name`
   is required, and `..`, backslashes, control characters, duplicates, links and
   directories are refused at read. `fleet_mcp/./node/connector.py` passes a
   `..`-and-absolute-prefix check and resolves to the real file, so a layer guard
   written against string prefixes lets it through.
3. **Bytecode is refused** (`.pyc`/`.pyo`). `_l2_module_of` that returns `""` for
   anything but `.py` makes `fleet_mcp/server.pyc` invisible to the deny-list,
   although Python imports it.
4. **`VERSION_RE` accepts a prerelease** (`1.4.0-rc.1`), because a release
   candidate is a version; and `depends_on` accepts a package
   (`fleet_mcp/__init__.py`), not only a module.

Nothing here is a request to copy more code. Item 1 is the one that would turn a
correct artifact red today.

## 9. Checked against #436 `85493198` — what aligns, what does not

`#436` (head `85493198`, `scripts/release.py` + `tests/test_release_l2_layers.py`)
is the stacked change that consumes this manifest. Read at that SHA, **the shape
now aligns**; three smaller things do not, and none of them is fixed here because
that file has an owner.

Aligned, verified:

| | where |
|---|---|
| `members` includes `ARTIFACT.json`; `member_sha256` covers everything except it | `test_a_member_list_without_the_manifest_is_refused`, `test_a_digest_map_that_does_not_cover_the_members_is_refused` |
| the tree digest is computed over `digests = members − {ARTIFACT.json}` | their `tree_digest = _l2_tree_digest([... for name in digests])` |
| `on_disk` has the manifest discarded *and* `declared_files` excludes it, so it is neither "missing" nor "extra" | their `missing`/`extra` |
| canonical member names, no backslash, no control characters | their `PurePosixPath(name) != name` check |
| bytecode refused | their `.pyc`/`.pyo` check |
| a pin entry the payload no longer carries | their `stale` check |
| an intra-package import the payload does not carry | their `uncarried` check |
| both kinds' required members, platforms, layer separation | unchanged since `001eb7c` |

Three divergences, all reported rather than patched:

1. **Prerelease versions.** `L2_VERSION_RE` (`scripts/release.py:322`) is
   `^\d+\.\d+\.\d+(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$` — no `-rc.1` branch, while
   `l2_release.VERSION_RE` accepts one (§8 item 4). A release candidate built here
   verifies here and is refused by the release path. Today's shipped versions
   (`1.4.0+n…`) pass both, so nothing is broken now; the fix is four characters on
   their side (or a decision that an RC is not a version, which would then belong
   in `l2_release.VERSION_RE` too — the two must not disagree).
2. **`depends_on` is module-only.** Their loop is still
   `wanted = f"{str(dependency).replace('.', '/')}.py"`, so a package
   (`fleet_mcp`) is refused although it carries `fleet_mcp/__init__.py`. §8 item 4.
   Nothing builds with `depends_on` today, so this is latent, not live.
3. **`standalone` is recomputed, never measured.**
   `scripts/release.py:1188` is `standalone = l2.get("standalone") is True and not
   external`, and that value becomes `"installable"` in the status report. It is
   the same arithmetic this module had before this revision and it cannot see the
   failure it is meant to catch: a payload that imports and then dies on its first
   fleet call. The manifest now carries the answer as `runtime_standalone` plus the
   probe record that produced it, so the fix on their side is to prefer
   `runtime_standalone` when present and fall back to the old expression when not.
   Until then the two agree on both kinds today (`standalone: false`), which is why
   this is a finding and not a breakage.

What #436 does **not** need to do: it reads no field this revision adds, so
`import_standalone`, `runtime_standalone`, `probe`, `install_requires_repo` and
`install` are additive by construction. Its own tests were checked the same way —
they build payloads through a `make_l2`/`reseal` pair that already excludes
`ARTIFACT.json` from the digest map, so they describe this shape.

## State after each side

| | today | after this lands |
|---|---|---|
| `release.py` reads our manifest | refuses kinds by name | verifies both kinds |
| `l2_release.py` verifies our manifest | **REAL** (77 tests: 76 pass, 1 skip) | unchanged, plus parity test stops skipping |
| our manifest shape matches yours | **REAL** (asserted), and re-checked against `#436 @ 85493198` in §9 | unchanged |
