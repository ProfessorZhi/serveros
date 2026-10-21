# Plan — facts writer and single-owner coordination

Companion to `proposals/proposal-native-facts-and-owner.md`. Same base
`69a18bc`, same branch as PR #390, additive commit.

## Review round 1 — against the existing runtime

- `probes.host.systemd_user_unit` already parses `LoadState/ActiveState/SubState`
  and refuses a not-loaded unit. Reused; no second systemd reader.
- `agent_sessions` has no "busy" predicate. Rather than invent one, the writer
  takes an **injected resolver**; with none configured the fact is `known: false`.
- No existing link observer. A configured TCP probe is the only honest source;
  unset means unknown.
- `runtime/agent_control_store.py` is on the root lane and **is not modified**;
  its public read API is used only if a caller passes a store.

## Review round 2 — against the code being added

- The lease must be the **same file** the Core is given with `--lease`, and must
  be taken **before** the first document is written, or the writer can publish
  facts while the Core owns the pen.
- The document must be written with `rename` from the same directory so a reader
  never sees a partial file (`facts.cpp` refuses malformed rather than retrying,
  by design).
- Owner map must be closed: an unlisted unit is refused, not defaulted, or a new
  service silently becomes the Core's.
- The maintenance marker is per-unit and read every cycle, so turning it on
  stops recovery without restarting anything.

## Files

| file | change |
|---|---|
| `runtime/native_core_facts.py` | new: collector, atomic writer, owner map, shared lease |
| `runtime/serveros-native-facts.service` | new: **template**, not installed |
| `tests/test_native_core_facts.py` | new: unittest (no pytest on this host) |
| `docs/…` | this proposal and plan |

## Verification

Offline unit tests plus live acceptance against a real transient user unit:
recovery, agent busy, link down, stale (writer stopped), second writer (Python vs
Core on one lease), maintenance/manual-stop behaviour, needs-login, and
upgrade/rollback of the writer itself.
