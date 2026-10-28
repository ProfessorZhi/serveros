# Fleet Next parallel plugin — source-only preflight

**Status: SOURCE/PREFLIGHT ONLY. Nothing here creates, updates, installs or
redirects a ChatGPT plugin.** This directory exists because Fleet Next is a
**parallel** release, not an update to the Legacy Fleet plugin that ChatGPT is
already using.

## Two tracks

| track | identity | what must stay true |
|---|---|---|
| Legacy Fleet | its existing backend `plugin_id`, release, MCP URL, OAuth audience, credential, port, datastore and tool namespace | ChatGPT keeps real create/read/send/get_run/stop; the 8-Skill controller is unchanged; a Next failure must not touch it |
| Fleet Next | a separately registered backend `plugin_id`, release, MCP URL/audience/credential, port/datastore and tool namespace | separate artifact and independent version; read-only shadow until separately approved |

`parallel_plugin.preflight()` returns `PASS` only when all of the following
hold:

- next backend `plugin_id` is present and is not the legacy ID
  (`BLOCKED_NEXT_PLUGIN_NOT_REGISTERED` if absent; never a synthetic ID);
- legacy baseline identity and 8-Skill controller are unchanged
  (`LEGACY_RENAMED`, `LEGACY_RESOURCE_CHANGED`, `LEGACY_CONTROLLER_MUTATED`);
- next does not share legacy URL, OAuth audience, credential anchor, port,
  datastore or tool namespace (`NEXT_TAKEOVER_*` / `NEXT_SHARED_*`);
- next carries exactly the 8 Operator Skills + `fleet-batch-commander`
  (`NEXT_CONTROLLER_INCOMPLETE`);
- next uses the canonical 9-Skill trusted pin, not the standalone
  `chatgpt-fleet-next` 7-member package (`MIXED_SOURCE_PIN`,
  `SOURCE_PIN_MISMATCH`, `MIXED_SOURCE_ARCHIVE`);
- a same-ID update is only a future version of **Next**, with explicit consent,
  exact `expected_release_id`, and an advancing version
  (`NEXT_UPDATE_CONSENT_REQUIRED`, `STALE_EXPECTED_RELEASE_ID`,
  `NEXT_VERSION_NOT_ADVANCING`); legacy IDs are always refused;
- shadow requests are read-only (`NEXT_SHADOW_WRITE_FORBIDDEN`).

## Trusted source pins

The two products have different member sets and digests; they must not be
mixed:

| pin | product | members | skills | archive sha256 |
|---|---|---:|---:|---|
| `fleet_v1_9skill` | canonical commander 9-Skill | 15 | 9 | `7320129e…` |
| `chatgpt_fleet_next_7member` | standalone ChatGPT package | 7 | 4 | `14b3b4ee…` |

The 7-member standalone package is not a Fleet Next plugin format and cannot be
substituted for the 15-member commander artifact.

## Failure/rollback contract

A `PASS` plan carries `legacy_usable_on_failure=True`, `next_install_isolated=True`
and `rollback_target` = the legacy release ID. A refused plan carries the same
legacy-usable flag. Nothing in this module executes a rollback, installs a
service or calls Creator; `creator_calls=0`, `network=False`,
`production_verified=False`.

`MockCreator` is an offline test stand-in. It refuses a missing or legacy ID
and records every call; it never invents a registered ID.

## Usage

```python
from parallel_plugin import preflight

plan = preflight(
    legacy_baseline=legacy_readback,
    legacy_current=legacy_readback_now,
    next_plugin=next_readback,       # backend_plugin_id must be real
    previous_next=previous_next,     # only for a same-ID Next update
    explicit_next_update_consent=False,
    expected_release_id=None,
    shadow={"requested_writes": []},
)
# plan["verdict"] == "PASS" or plan["code"] is a typed refusal
```

No real Creator/API call, OAuth/secret read, service restart, paid model, IM or
main merge is performed by this directory.
