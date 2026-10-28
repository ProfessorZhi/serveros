# Fleet Next RC Wave 13 QA (blocked owner)

Independent test-only composition of:

```text
#487 d8dc1f9  (Wave 12 RC)
  + #477 be2c1cc  model evidence + CreateGrant
  + #481 9b7710a  bounded batch one-shot CLI
  + #480 3777d13  Legacy/Next parallel-plugin preflight
```

The four producer trees were merged in this order; no producer file was
edited. The QA module calls their public functions:

- `model_grant_facts()` — #477 project grant denies before provider evidence;
  allowed explicit model and missing-evidence cases; zero provider calls.
- `parallel_gate_facts()` — #480 refuses the Legacy backend ID, the Legacy MCP
  URL and a shadow write; zero Creator calls.
- `archive_facts()` — canonical 9-Skill/15-member archive vs standalone
  7-member/4-skill package; distinct archive and content hashes.
- `legacy_surface_touched()` — no path under `fleet_mcp/`, `runtime/`,
  `services/`, `plugin/` or `deploy/` in the P2 delta.
- `wave12_conflict()` — the exact owner block.

## Owner block

`tests/test_fleet_next_rc_wave12_ledger.py` pins the LF-normalised digest of
`integrations/fleet-v1-milestone-batch/README.md` to
`d3b5c1e5…0e6cf`. #481 legitimately changes that README; the composed tree
digest is `3f479eec…04558`. The exact old test therefore fails:

```text
FAILED tests/test_fleet_next_rc_wave12_ledger.py::
  TheCompositionIsTheAuthorsBytes::
  test_each_composed_file_still_holds_the_authors_content[
  file='integrations/fleet-v1-milestone-batch/README.md']
```

This is `BLOCKED_OWNER`, not a QA edit. Either #487 re-derives the Wave 12
ledger for the new composition, or #481 splits/rebases the README delta. This
lane does not change either owner file.

## Commands

```sh
timeout 300 taskset -c 0 env PYTHONDONTWRITEBYTECODE=1 \
  python3 -m pytest -q -p no:cacheprovider \
  tests/test_fleet_next_rc_wave13_compose.py
```

- QA suite: **7 passed**.
- #477: 45 passed, 32 subtests; #481: 24 passed; #480: 16 passed, 11 subtests.
- Combined #487/P2/QA targeted: **235 passed, 1 known #487 ledger failure,
  88 subtests**.

No real device, Creator, service, OAuth, credential, model, IM or GPU is used.
