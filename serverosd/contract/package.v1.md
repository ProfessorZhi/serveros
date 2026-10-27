# `serveros/native-runtime-package.v1`

The manifest a native L1 build writes about itself, and the rules its verifier
enforces. One document, one reader (`serverosd/verify_native_runtime.py`), one
writer (`serverosd/package_manifest.py`), one recipe that produces the bytes
(`serverosd/Makefile`).

## Why this is not `serveros/artifact.v1`

`scripts/release.py` already owns an artifact contract, and its `node-runtime`
kind requires `bin/serverosd`, `bin/serverosctl`, `runtime/daemon.py`,
`runtime/cli.py`, `runtime/serverosd.service` and `workengine/schema.sql`. That
is the **Python** device runtime, and it is installed on this node.

The native C++ runtime is a different set of files with a different actuator, and
it is not that artifact. Declaring it `node-runtime` would make the release path
verify it against a file list it will never satisfy; adding a third kind would
mean this lane editing `scripts/release.py`, which it does not own. So this
schema is separate on purpose, and it says so here rather than leaving a reader
to wonder why two manifests exist.

The fields are deliberately shaped like the artifact manifest's (a schema, a
name, a version, a member list with digests) so that a future `native-runtime`
kind can adopt this document without a rewrite. That adoption is an integration
lane's change, not this one's.

## The document

| field | meaning |
|---|---|
| `schema` | `serveros/native-runtime-package.v1`. The only value the reader implements. |
| `name` | `serverosd`. The package's name — a directory and a manifest, not a binary. |
| `version` | Read from `native/node-core/supervisor/src/main.cpp`'s `kVersion`. Not a second copy; the verifier refuses a manifest whose version is not the binary's. |
| `source.commit` | The repository commit the build was made from, or `null` when git was not available. |
| `source.dirty` | Whether the work tree had uncommitted changes. A package built from a dirty tree is not reproducible, and this is the record that says so. |
| `source.dirty_paths` | How many paths were modified. **`0` is a measurement** — "I asked git and it said nothing is modified" — and is different from the empty value, which means git did not answer. |
| `source.reason` | Present only when provenance was not measured, saying so rather than leaving `null` to be read as clean. |
| `toolchain` | The compiler path, its `--version` first line, the flags, and `cmake: null` — this build does not use CMake, and stating that is cheaper than a reader inferring it. Filled by the recipe, which asks the compiler; the writer runs nothing. |
| `members[]` | One row per binary in `bin/`. |
| `tests[]` | The test executables the build produced, when the `tests` goal was built. Not members: nothing installs them. |
| `owner_map` | The schema of the shared owner map, its writer, and its readers. Declared so the single-executor rule is visible in the manifest and not only in prose. |
| `compatibility` | `renames` (always empty today), `does_not_occupy`, and the reason. |

### `members[]`

| field | rule |
|---|---|
| `path` | `bin/<name>`. The verifier refuses a member outside `bin/`. |
| `role` | `actuator` or `read-only`. **Exactly one `actuator`, and it must be `bin/serveros-native-core`.** |
| `component` | `collector`, `supervisor`, or `core-lite`. Informational. |
| `source` | The translation unit that defines `main()`. |
| `sha256` | Over the file's bytes. Compared, not trusted. |
| `bytes` | Size. Compared. |
| `mode` | `"0755"`. An artifact that is not executable fails at exec with `203/EXEC`. |
| `legacy_path` | The path the binary has always shipped under. Must equal `path`. |

## The four refusals that matter

1. **A second actuator.** Two binaries that can each ask the service manager for a
   verb is two programmes that each believe they hold the pen. The owner map
   serialises *units*; it does not stop a second actuator from asking about a
   different unit.
2. **A promoted actuator.** One actuator is not enough if it is the wrong
   binary. `bin/serveros-native-core` is the resident decision engine; nothing
   else in this package may take that role.
3. **A rename.** `serveros-node-resource-snapshot` is named in the Python
   adapter, in `contract/node-resource-snapshot.v1.md` and in the core-lite
   profile reader; `serveros-native-core` is named in
   `contract/generation-fence.v1.md` and in the unit template. A rename is a
   compatibility break that surfaces at exec time on a device, not at build time
   on a laptop.
4. **A claimed reserved path.** `bin/serverosd` and `bin/serverosctl` belong to
   the `node-runtime` artifact. This package's *name* is `serverosd`; it must not
   also take the *path*. The verifier checks both the declaration and the
   directory, because a manifest that says it left the path alone while a file of
   that name sits in `bin/` is how two artifacts come to claim one name.

## What this contract does not claim

- It is not an installation record. Nothing in the package says a service is
  installed, enabled or running.
- It is not a signature. There is no key, and the digest proves the bytes are the
  recorded bytes — not who built them.
- It does not cover `windows-core/`, which builds under its own toolchain and is
  a separate lane.
